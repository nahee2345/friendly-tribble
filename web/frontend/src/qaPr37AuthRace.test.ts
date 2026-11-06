import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError, invalidateAuthSession, noteAuthenticatedLogin, request,
  subscribeAuthInvalidation,
} from './api';

type Deferred<T> = { promise: Promise<T>; resolve: (value:T)=>void };
function deferred<T>():Deferred<T>{
  let resolve!:(value:T)=>void;
  const promise=new Promise<T>(accept=>{resolve=accept;});
  return {promise,resolve};
}
function response(value:unknown,status=200):Response{
  return new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
}
afterEach(()=>vi.restoreAllMocks());

describe('QA-owned PR37 independent auth races',()=>{
  it('denies old successful PAPER/approval data after logout and fresh login',async()=>{
    const delayed=deferred<Response>();
    const fetchMock=vi.spyOn(globalThis,'fetch').mockReturnValueOnce(delayed.promise);
    const old=request<{items:unknown[]}>('/api/v1/stage1/approvals');
    invalidateAuthSession();
    noteAuthenticatedLogin();
    delayed.resolve(response({items:[{account:'OWNER_A',status:'APPROVED'}]}));
    await expect(old).rejects.toMatchObject({status:401});
    fetchMock.mockResolvedValueOnce(response({items:[{account:'OWNER_B'}]}));
    const current=await request<{items:{account:string}[]}>('/api/v1/stage1/approvals');
    expect(current.items).toEqual([{account:'OWNER_B'}]);
  });

  it.each([401,403])('stale %i in Stage1 request cannot invalidate a newer session',async(status)=>{
    const delayed=deferred<Response>();
    const spy=vi.spyOn(globalThis,'fetch').mockReturnValueOnce(delayed.promise);
    const invalidated=vi.fn();
    const unsubscribe=subscribeAuthInvalidation(invalidated);
    try{
      const old=request('/api/v1/stage1/portfolios');
      invalidateAuthSession();
      noteAuthenticatedLogin();
      delayed.resolve(response({error:{code:'OLD_AUTH_FAILURE'}},status));
      await expect(old).rejects.toMatchObject({status:401});
      expect(invalidated).toHaveBeenCalledTimes(1);
      spy.mockResolvedValueOnce(response({accounts:[]}));
      await expect(request('/api/v1/stage1/portfolios')).resolves.toEqual({accounts:[]});
      expect(invalidated).toHaveBeenCalledTimes(1);
    }finally{unsubscribe();}
  });

  it.each([401,403])('current %i does invalidate and hide old protected data',async(status)=>{
    const invalidated=vi.fn();
    const unsubscribe=subscribeAuthInvalidation(invalidated);
    try{
      vi.spyOn(globalThis,'fetch').mockResolvedValue(response({error:{code:'DENIED'}},status));
      await expect(request('/api/v1/stage1/manual-assets')).rejects.toBeInstanceOf(ApiError);
      expect(invalidated).toHaveBeenCalledTimes(1);
    }finally{unsubscribe();}
  });

  it('late successful JSON parsing is rejected after authenticated switch',async()=>{
    const responseBody=deferred<unknown>();
    const delayedJSON={
      ok:true,status:200,json:()=>responseBody.promise,
    } as Response;
    vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(delayedJSON);
    const old=request('/api/v1/stage1/manual-assets');
    await Promise.resolve();
    noteAuthenticatedLogin();
    responseBody.resolve({items:[{secret:'PREVIOUS_OWNER'}]});
    await expect(old).rejects.toMatchObject({status:401});
  });

  it('POST through shared barrier preserves same-origin, no-store, CSRF',async()=>{
    document.cookie='wealthy_csrf=qa-pr37-token; path=/';
    const spy=vi.spyOn(globalThis,'fetch').mockResolvedValue(response({status:'blocked'}));
    await request('/api/v1/stage1/order',{method:'POST',body:JSON.stringify({request_id:'qa-only'})});
    const [path,init]=spy.mock.calls[0];
    expect(path).toBe('/api/v1/stage1/order');
    expect(init?.credentials).toBe('same-origin');
    expect(init?.cache).toBe('no-store');
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('qa-pr37-token');
    document.cookie='wealthy_csrf=; Max-Age=0; path=/';
  });
});
