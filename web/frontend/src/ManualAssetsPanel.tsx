import {useEffect,useRef,useState} from 'react';
import './manual-assets.css';

type Stock={account:string;symbol:string;currency:string;quantity:string;scope:string;cost_basis_status:string;average_cost_native:string|null;total_cost_native:string|null;realized_pnl_native:string|null;unrealized_pnl_native:string|null;duplicate_currency_conflict:boolean;history_reconciled:boolean};
type Balance={label:string;currency:string;amount:string};
type ManualTrade={account:string;symbol:string;currency:string;side:string;quantity:string;unit_price:string|null;trade_date:string|null;recorded_at:string};
type Result={items:Stock[];balances:Balance[];history:ManualTrade[];scope:string;included_in_paper_nav:boolean;included_in_sample_nav?:boolean;verified_real_asset_total_krw?:string|null;real_total_status?:string;notice?:string};

function csrf():string{
  const item=document.cookie.split(';').map(v=>v.trim()).find(v=>v.startsWith('wealthy_csrf='));
  return item?decodeURIComponent(item.slice('wealthy_csrf='.length)):'';
}
async function request<T>(path:string,payload?:unknown):Promise<T>{
  const r=await fetch(path,payload===undefined
    ?{credentials:'same-origin',cache:'no-store'}
    :{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify(payload)});
  if(!r.ok)throw new Error('HTTP '+r.status);
  return r.json() as Promise<T>;
}
export function ManualAssetsPanel(){
  const [data,setData]=useState<Result|null>(null);
  const [message,setMessage]=useState('');
  const [busy,setBusy]=useState(false);
  // One immutable request id per unresolved user intention, not per retry.
  // The payload stays in memory only: never expose account details to localStorage.
  const inflight=useRef(false);
  const pending=useRef<{path:string;signature:string;body:Record<string,unknown>;request_id:string}|null>(null);
  const [retryNeeded,setRetryNeeded]=useState(false);
  const [account,setAccount]=useState('default');
  const [symbol,setSymbol]=useState('AAPL');
  const [operation,setOperation]=useState('BUY');
  const [cashOperation,setCashOperation]=useState('DEPOSIT');
  const [currency,setCurrency]=useState('USD');
  const [quantity,setQuantity]=useState('1');
  const [unitPrice,setUnitPrice]=useState('');
  const [tradeDate,setTradeDate]=useState('');
  const [label,setLabel]=useState('별도 보유 현금');
  const [assetCurrency,setAssetCurrency]=useState('KRW');
  const [amount,setAmount]=useState('100000');
  async function refresh(){try{setData(await request<Result>('/api/v1/stage1/manual-assets'));}catch{setData(null);setMessage('자산 기록 조회 실패. 인증이나 저장 상태를 확인하세요. 이전 금융정보는 숨겼습니다.');}}
  useEffect(()=>{void refresh();},[]);
  async function change(path:string,body:Record<string,unknown>,retry=false){
    if(inflight.current)return;
    const signature=JSON.stringify([path,body]);
    if(pending.current && (!retry || pending.current.signature!==signature)){
      setMessage('이전 요청 결과가 불확실합니다. 같은 요청 ID로 재시도한 다음 새 입력을 진행하세요.');
      return;
    }
    const entry=pending.current??{
      path,signature,body:{...body},request_id:crypto.randomUUID()
    };
    pending.current=entry;
    inflight.current=true;
    setBusy(true);setRetryNeeded(false);setMessage('');
    try{
      const result=await request<{status?:string}>(entry.path,{
        ...entry.body,request_id:entry.request_id
      });
      if(result.status && result.status!=='recorded'&&result.status!=='already_recorded')
        throw Error('저장 결과 확인 불가: '+result.status);
      // Only an acknowledged write permits generating a new request ID.
      pending.current=null;
      await refresh();
      setMessage(result.status==='already_recorded'
        ?'기존 요청의 저장을 확인했습니다. 중복 입력은 발생하지 않았습니다.'
        :'수동 기록 저장을 확인했습니다. PAPER 원장은 변경되지 않습니다.');
    }catch(e){
      const error=e instanceof Error?e.message:String(e);
      if(/HTTP (400|401|403|422)(?!\\d)/.test(error)){
        // Invalid inputs and revoked sessions must not leave retained owner data.
        pending.current=null;
        if(/HTTP (401|403)/.test(error))setData(null);
        setRetryNeeded(false);
        setMessage('요청 거절: '+error+' · 입력 또는 인증 상태를 확인하세요.');
      }else{
        // A timeout/5xx may have committed. Same key+same payload on retry.
        setRetryNeeded(true);
        setMessage('응답 유실 또는 처리 불확실. 신규 요청을 만들지 말고 동일 ID로 재시도하세요. '+error);
      }
    }finally{inflight.current=false;setBusy(false);}
  }
  function retryPending(){
    const entry=pending.current;
    if(entry)void change(entry.path,entry.body,true);
  }
  return <section className="card manual-assets" aria-label="사용자 직접 자산 입력">
    <details>
      <summary><span><strong>직접 자산 및 보유주식 관리</strong><small>자주 사용하지 않는 입력 기능 · 필요할 때만 펼치기</small></span><span className="manual-chevron" aria-hidden="true">⌄</span></summary>
      <div className="manual-assets-content">
    <h2>실제 보유 직접기록 · PAPER와 별도</h2>
    <p>본인이 신고한 실보유 기록이며 증권사 검증 전입니다. PAPER·SAMPLE·PAPER 총자산에는 포함되지 않습니다. 중복 등록과 취득단가 미확정인 자료는 실제 총자산으로 합산하지 않습니다.</p>
    <h3>주식·ETF 수량</h3>
    <div className="manual-form-grid">
      <select aria-label="보유 주식 계좌" value={account} onChange={e=>setAccount(e.target.value)}><option value="default">수동 실보유 · 미국 구분</option><option value="kr-base">수동 실보유 · 한국 구분</option><option value="isa-base">수동 실보유 · ISA 구분(적격성 미확인)</option></select>
      <input aria-label="보유 종목 코드" value={symbol} onChange={e=>setSymbol(e.target.value)} placeholder="AAPL / 005930"/>
      <select aria-label="보유 주식 통화" value={currency} onChange={e=>setCurrency(e.target.value)}><option>USD</option><option>KRW</option></select>
      <input aria-label="주식 수량" inputMode="decimal" value={quantity} onChange={e=>setQuantity(e.target.value)}/>
      <input aria-label="매매 단가 (선택)" inputMode="decimal" value={unitPrice} onChange={e=>setUnitPrice(e.target.value)} placeholder="주당 매수가 (선택)"/>
      <input aria-label="매매 날짜 (선택)" type="date" value={tradeDate} onChange={e=>setTradeDate(e.target.value)}/>
      <select aria-label="주식 기록 종류" value={operation} onChange={e=>setOperation(e.target.value)}>
        <option value="BUY">매수 · 수량 증가</option><option value="SELL">매도 · 수량 감소</option>
        <option value="IN">입고 · 수량 증가</option><option value="OUT">출고 · 수량 감소</option>
        <option value="CORRECT_ADD">수동 정정 · 수량 증가</option>
        <option value="CORRECT_REMOVE">수동 정정 · 수량 감소</option>
      </select>
      <button type="button" disabled={busy||retryNeeded} onClick={()=>void change('/api/v1/stage1/manual-assets',{account,symbol,currency,quantity,unit_price:unitPrice||null,trade_date:tradeDate||null,side:operation})} className="manual-action manual-action-add">선택한 주식 내역 기록</button>
    </div>
    <h3>현금·예금 등 별도 자산</h3>
    <div className="manual-form-grid">
      <input aria-label="직접 자산 이름" value={label} onChange={e=>setLabel(e.target.value)} placeholder="예금 / 현금 등"/>
      <input aria-label="증감할 자산 금액" inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)}/>
      <select aria-label="자산 통화" value={assetCurrency} onChange={e=>setAssetCurrency(e.target.value)}><option>KRW</option><option>USD</option></select>
      <select aria-label="실제 현금 기록 종류" value={cashOperation} onChange={e=>setCashOperation(e.target.value)}>
        <option value="DEPOSIT">입금</option><option value="WITHDRAW">출금</option>
        <option value="CORRECT_ADD">잔액 정정 · 증가</option>
        <option value="CORRECT_REMOVE">잔액 정정 · 감소</option>
      </select>
      <button type="button" disabled={busy||retryNeeded} onClick={()=>void change('/api/v1/stage1/manual-assets/balance',{label,currency:assetCurrency,amount,side:cashOperation})} className="manual-action manual-action-add">선택한 현금 내역 기록</button>
      <button type="button" onClick={()=>void refresh()}>목록 갱신</button>
    </div>
    {message&&<p role="status">{message}</p>}
    {retryNeeded&&<button type="button" disabled={busy} onClick={retryPending}>
      이전 요청 동일 ID로 재시도 (새 거래 아님)
    </button>}
    <h3>저장된 주식 기록</h3>
    {(data?.items??[]).map(x=><p key={x.account+x.symbol+x.currency}>{x.account} · {x.symbol} · {x.quantity}주 ({x.currency}) · {x.cost_basis_status==='known'?'평균취득가 '+x.average_cost_native:'취득단가·손익 계산 불가'}{x.duplicate_currency_conflict?' · 통화 중복: 확인 필요':''}</p>)}
    {!data?.items.length&&<p>직접 등록된 주식이 없습니다.</p>}
    <h3>저장된 현금·기타 자산</h3>
    {(data?.balances??[]).map(x=><p key={x.label+x.currency}>{x.label} · {x.amount} {x.currency}</p>)}
    {!data?.balances.length&&<p>직접 등록된 자산이 없습니다.</p>}
    <h3>직접 기록한 주식 증감 이력 · 최근 100건</h3>
    {(data?.history??[]).map((x,i)=><p key={x.recorded_at+x.symbol+i}>
      {x.trade_date??x.recorded_at.slice(0,10)} · {x.side} {x.symbol} {x.quantity}주
      {x.unit_price?' · 주당 '+x.unit_price+' '+x.currency:' · 취득단가 미입력'}
    </p>)}
    <p>매수·매도·입출고·정정은 별도 이력으로 보존됩니다. 취득단가가 없으면 평균단가·실현손익을 계산할 수 없습니다. 현재가가 검증되지 않으면 미실현손익도 계산 불가입니다.</p>
      </div>
    </details>
  </section>;
}

export function ManualAssetsSummary(){
  const [data,setData]=useState<Result|null>(null);
  const [error,setError]=useState(false);
  useEffect(()=>{let active=true;
    void request<Result>('/api/v1/stage1/manual-assets')
      .then(r=>{if(active){setData(r);setError(false);}}).catch(()=>{if(active){setData(null);setError(true);}});
    return()=>{active=false;};
  },[]);
  return <section className="card manual-summary" aria-label="직접 등록한 자산 요약">
    <div className="card-title"><h2>실제 보유 직접기록 · 별도</h2><a href="/settings">설정에서 관리 →</a></div>
    <p>사용자 입력 실보유 기록(증권사 미검증). PAPER와 SAMPLE 평가액에 합산하지 않으며, 실제 총자산은 가격·중복 검증 전 계산 불가입니다.</p>
    {error&&<p role="status">직접 입력한 자산 조회 실패 · 설정에서 로그인·저장 상태 확인</p>}
    {data&&<>
      <h3>보유주식 · {data.items.length}종</h3>
      {!data.items.length&&<p>직접 등록한 주식이 없습니다.</p>}
      {data.items.map(item=><p key={item.account+item.symbol+item.currency}>{item.symbol} · {item.quantity}주 · {item.currency} · {item.cost_basis_status==='known'?'평균 취득단가 '+item.average_cost_native:'취득원가 미확정'}</p>)}
      <h3>기타 자산 · {data.balances.length}건</h3>
      {!data.balances.length&&<p>직접 등록한 현금성 자산이 없습니다.</p>}
      {data.balances.map(item=><p key={item.label+item.currency}>{item.label} · {item.amount} {item.currency}</p>)}
    </>}
  </section>;
}
