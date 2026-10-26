import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {ManualAssetsPanel} from './ManualAssetsPanel';

function json(body:unknown,status=200) {
  return Promise.resolve(new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}}));
}
function empty() {
  return {items:[],balances:[],history:[],scope:'USER_DECLARED_REAL',included_in_paper_nav:false};
}
afterEach(()=>{cleanup();localStorage.clear();vi.restoreAllMocks();vi.unstubAllGlobals();});

describe('QA-016-03 manual assets idempotent UI',()=>{
  it('retries a lost stock response with the SAME key and payload',async()=>{
    let counter=0;
    const payloads:Record<string,unknown>[]=[];
    vi.stubGlobal('crypto',{randomUUID:()=> 'retry-stock-unique-id'});
    vi.spyOn(globalThis,'fetch').mockImplementation((input,options)=>{
      if(options?.method==='POST'){
        payloads.push(JSON.parse(String(options.body)) as Record<string,unknown>);
        counter++;
        if(counter===1)return Promise.reject(new Error('lost response after commit'));
        return json({status:'already_recorded'});
      }
      return json(empty());
    });
    render(<ManualAssetsPanel/>);
    const record=await screen.findByRole('button',{name:'선택한 주식 내역 기록'});
    fireEvent.click(record);
    await screen.findByRole('button',{name:/이전 요청 동일 ID로 재시도/});
    expect(record).toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:/이전 요청 동일 ID로 재시도/}));
    await waitFor(()=>expect(payloads).toHaveLength(2));
    await screen.findByText(/중복 입력은 발생하지 않았습니다/);
    expect(payloads[0].request_id).toEqual('retry-stock-unique-id');
    expect(payloads[1]).toEqual(payloads[0]);
  });

  it('blocks concurrent double-click before state re-render, then new explicit request can differ',async()=>{
    const payloads:Record<string,unknown>[]=[];
    let resolveFirst:(value:Response)=>void=()=>{};
    let ids=0;
    vi.stubGlobal('crypto',{randomUUID:()=> 'stable-click-'+(++ids)});
    const pending=new Promise<Response>(resolve=>{resolveFirst=resolve;});
    vi.spyOn(globalThis,'fetch').mockImplementation((_input,options)=>{
      if(options?.method==='POST'){
        payloads.push(JSON.parse(String(options.body)) as Record<string,unknown>);
        return payloads.length===1?pending:json({status:'recorded'});
      }
      return json(empty());
    });
    render(<ManualAssetsPanel/>);
    const record=await screen.findByRole('button',{name:'선택한 주식 내역 기록'});
    fireEvent.click(record);
    fireEvent.click(record);
    expect(payloads).toHaveLength(1);
    resolveFirst(new Response(JSON.stringify({status:'recorded'}),{status:200,headers:{'Content-Type':'application/json'}}));
    await screen.findByText(/수동 기록 저장을 확인했습니다/);
    fireEvent.click(record);
    await waitFor(()=>expect(payloads).toHaveLength(2));
    expect(payloads[0].request_id).not.toEqual(payloads[1].request_id);
  });

  it('cash retry uses original same intent, not edited balance fields',async()=>{
    const sent:Record<string,unknown>[]=[];
    vi.stubGlobal('crypto',{randomUUID:()=> 'retry-cash-unique-id'});
    vi.spyOn(globalThis,'fetch').mockImplementation((input,options)=>{
      if(options?.method==='POST'){
        const body=JSON.parse(String(options.body)) as Record<string,unknown>;
        sent.push(body);
        return sent.length===1?Promise.reject(new Error('network lost')):json({status:'already_recorded'});
      }
      return json(empty());
    });
    render(<ManualAssetsPanel/>);
    fireEvent.click(await screen.findByRole('button',{name:'선택한 현금 내역 기록'}));
    await screen.findByRole('button',{name:/이전 요청 동일 ID로 재시도/});
    fireEvent.change(screen.getByRole('textbox',{name:'증감할 자산 금액'}),{target:{value:'900000'}});
    fireEvent.click(screen.getByRole('button',{name:/이전 요청 동일 ID로 재시도/}));
    await waitFor(()=>expect(sent).toHaveLength(2));
    expect(sent[0]).toEqual(sent[1]);
    expect(sent[1].amount).toEqual('100000');
  });

  it('remembers only an opaque ID across reload and reconciles a committed response',async()=>{
    let requests=0;
    vi.stubGlobal('crypto',{randomUUID:()=> 'persisted-stock-request-01'});
    vi.spyOn(globalThis,'fetch').mockImplementation((path,opts)=>{
      if(opts?.method==='POST'){requests++;return Promise.reject(new Error('reply lost'));}
      if(String(path).includes('/manual-assets/request/'))return json({
        status:'recorded',kind:'stock',request_id:'persisted-stock-request-01',
        recorded_at:'2026-10-25T10:00:00+00:00',safe_to_resubmit:false
      });
      return json(empty());
    });
    render(<ManualAssetsPanel/>);
    fireEvent.click(await screen.findByRole('button',{name:'선택한 주식 내역 기록'}));
    await screen.findByRole('button',{name:/이전 요청 동일 ID로 재시도/});
    const stored=localStorage.getItem('wealthy:manual-unresolved:v1');
    expect(stored).toContain('persisted-stock-request-01');
    expect(stored).not.toContain('AAPL');
    expect(stored).not.toContain('100000');
    cleanup();
    render(<ManualAssetsPanel/>);
    await screen.findByText(/중복 재전송 없이 복구했습니다/);
    await waitFor(()=>expect(localStorage.getItem('wealthy:manual-unresolved:v1')).toBeNull());
    expect(requests).toBe(1);
  });

  it('after tab restart, an unfound request stays blocked and is never auto-posted',async()=>{
    localStorage.setItem('wealthy:manual-unresolved:v1',JSON.stringify({
      request_id:'unconfirmed-still-pending-01',kind:'cash',
      created_at:'2026-10-25T10:00:00Z'
    }));
    let writes=0;
    vi.spyOn(globalThis,'fetch').mockImplementation((path,opts)=>{
      if(opts?.method==='POST'){writes++;return json({status:'recorded'});}
      if(String(path).includes('/manual-assets/request/'))return json({
        status:'not_found',kind:null,recorded_at:null,safe_to_resubmit:false
      });
      return json(empty());
    });
    render(<ManualAssetsPanel/>);
    await screen.findByText(/최종 저장 여부가 미확정입니다/);
    const submit=screen.getByRole('button',{name:'선택한 현금 내역 기록'});
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(writes).toBe(0);
    expect(localStorage.getItem('wealthy:manual-unresolved:v1')).toContain('unconfirmed-still-pending-01');
  });

  it('does not claim success if a 200 response omitted the acknowledged status',async()=>{
    let writes=0;
    vi.stubGlobal('crypto',{randomUUID:()=> 'ambiguous-200-reply-id'});
    vi.spyOn(globalThis,'fetch').mockImplementation((_path,opts)=>{
      if(opts?.method==='POST'){writes++;return json({});}
      return json(empty());
    });
    render(<ManualAssetsPanel/>);
    fireEvent.click(await screen.findByRole('button',{name:'선택한 현금 내역 기록'}));
    await screen.findByText(/저장 결과 확인 불가/);
    expect(writes).toBe(1);
    expect(localStorage.getItem('wealthy:manual-unresolved:v1')).toContain('ambiguous-200-reply-id');
    expect(screen.getByRole('button',{name:'선택한 현금 내역 기록'})).toBeDisabled();
  });

  it('keeps successful commit message separate from failed subsequent list refresh',async()=>{
    let reads=0;
    vi.stubGlobal('crypto',{randomUUID:()=> 'saved-then-list-failed'});
    vi.spyOn(globalThis,'fetch').mockImplementation((_path,opts)=>{
      if(opts?.method==='POST')return json({status:'recorded'});
      reads++;
      return reads>1?json({code:'ERROR'},503):json(empty());
    });
    render(<ManualAssetsPanel/>);
    await screen.findByRole('button',{name:'선택한 주식 내역 기록'});
    fireEvent.click(screen.getByRole('button',{name:'선택한 주식 내역 기록'}));
    await screen.findByText(/수동 기록 저장을 확인했습니다.*목록 재조회만 실패했습니다/);
    expect(localStorage.getItem('wealthy:manual-unresolved:v1')).toBeNull();
    expect(screen.queryByText(/기존 요청 결과가 불확실합니다/)).toBeNull();
  });

  it('blocks a fresh write immediately when another tab has a pending marker',async()=>{
    let writes=0;
    vi.spyOn(globalThis,'fetch').mockImplementation((_path,opts)=>{
      if(opts?.method==='POST'){writes++;return json({status:'recorded'});}
      return json(empty());
    });
    render(<ManualAssetsPanel/>);
    await screen.findByRole('button',{name:'선택한 주식 내역 기록'});
    const marker=JSON.stringify({request_id:'another-tab-unconfirmed-id',kind:'cash',
      created_at:'2026-10-26T00:00:00Z'});
    localStorage.setItem('wealthy:manual-unresolved:v1',marker);
    window.dispatchEvent(new StorageEvent('storage',{
      key:'wealthy:manual-unresolved:v1',newValue:marker
    }));
    await screen.findByText(/다른 탭 또는 이전 사용자의 처리 미확정 요청/);
    fireEvent.click(screen.getByRole('button',{name:'선택한 주식 내역 기록'}));
    expect(writes).toBe(0);
  });

});
