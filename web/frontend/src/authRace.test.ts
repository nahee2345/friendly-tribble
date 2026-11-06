import {afterEach,describe,expect,it,vi} from 'vitest';
import {ApiError,api,invalidateAuthSession,noteAuthenticatedLogin,request,subscribeAuthInvalidation} from './api';

type ResponseResolver=(response:Response)=>void;
function pendingResponse():{promise:Promise<Response>;resolve:ResponseResolver}{
  let resolve!:ResponseResolver;
  const promise=new Promise<Response>((complete)=>{resolve=complete;});
  return {promise,resolve};
}
const json=(value:unknown,status=200)=>new Response(JSON.stringify(value),{
  status,headers:{'Content-Type':'application/json'}
});
afterEach(()=>{
  vi.restoreAllMocks();
  document.cookie='wealthy_csrf=; Max-Age=0; path=/';
});

describe('Real API auth-generation and delayed 401/403 safety contracts',()=>{
  it('suppresses a delayed financial 200 after logout (not a successful new card)',async()=>{
    const old=pendingResponse();
    vi.spyOn(globalThis,'fetch').mockReturnValue(old.promise);
    const response=api.portfolio();
    invalidateAuthSession(); // logout invalidates visible account synchronously
    old.resolve(json({data_scope:'MANUAL_ACTUAL',positions:[{symbol:'PRIVATE-OLD'}]}));
    await expect(response).rejects.toMatchObject({status:401});
  });

  it.each([401,403])('does not let an old %i invalidate a newer login',async(status)=>{
    const old=pendingResponse();
    vi.spyOn(globalThis,'fetch').mockReturnValue(old.promise);
    const observer=vi.fn();
    const dispose=subscribeAuthInvalidation(observer);
    try{
      const response=request('/api/v1/stage1/manual-assets');
      invalidateAuthSession();
      noteAuthenticatedLogin();
      old.resolve(json({error:{code:'STALE_AUTH'}},status));
      await expect(response).rejects.toMatchObject({status:401});
      expect(observer).toHaveBeenCalledTimes(1); // only explicit logout
      // New session remains able to read new cards and is not invalidated.
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(json({items:[]}));
      await expect(request('/api/v1/stage1/manual-assets')).resolves.toEqual({items:[]});
      expect(observer).toHaveBeenCalledTimes(1);
    }finally{dispose();}
  });

  it.each([401,403])('fresh denied Stage1 request (%i) clears authenticated UI',async(status)=>{
    const callback=vi.fn();
    const dispose=subscribeAuthInvalidation(callback);
    vi.spyOn(globalThis,'fetch').mockResolvedValue(json({error:'no sensitive text'},status));
    try{
      await expect(request('/api/v1/stage1/portfolios')).rejects.toBeInstanceOf(ApiError);
      expect(callback).toHaveBeenCalledTimes(1);
    }finally{dispose();}
  });

  it('rejects delayed PAPER/approval data even if JSON parsing itself was delayed',async()=>{
    const old=pendingResponse();
    const fetchMock=vi.spyOn(globalThis,'fetch').mockReturnValueOnce(old.promise);
    const deferred=pendingResponse();
    const approval=request<{items:{account:string;status:string}[]}>('/api/v1/stage1/approvals');
    // A response arriving after authenticated account switch is never displayed.
    noteAuthenticatedLogin();
    old.resolve(json({items:[{account:'old-owner',status:'APPROVED'}]}));
    await expect(approval).rejects.toMatchObject({status:401});
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // A new authenticated request after switch may succeed separately.
    vi.mocked(globalThis.fetch).mockReturnValueOnce(deferred.promise);
    const fresh=request<{items:unknown[]}>('/api/v1/stage1/approvals');
    deferred.resolve(json({items:[]}));
    await expect(fresh).resolves.toEqual({items:[]});
  });

  it('sends Stage1 writes through shared same-origin CSRF barrier',async()=>{
    document.cookie='wealthy_csrf=fresh-csrf; path=/';
    const mocked=vi.spyOn(globalThis,'fetch').mockResolvedValue(json({status:'blocked',reason:'UNVERIFIED_SESSION_OPENING_NAV'}));
    await request('/api/v1/stage1/order',{method:'POST',body:JSON.stringify({portfolio:'default'})});
    expect(mocked).toHaveBeenCalledWith('/api/v1/stage1/order',expect.objectContaining({credentials:'same-origin',cache:'no-store'}));
    const init=mocked.mock.calls[0][1]!;
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('fresh-csrf');
  });
});
