import {useEffect,useState} from 'react';
import './manual-assets.css';

type Stock={account:string;symbol:string;currency:string;quantity:string};
type Balance={label:string;currency:string;amount:string};
type Result={items:Stock[];balances:Balance[];scope:string;included_in_paper_nav:boolean};

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
  const [account,setAccount]=useState('default');
  const [symbol,setSymbol]=useState('AAPL');
  const [currency,setCurrency]=useState('USD');
  const [quantity,setQuantity]=useState('1');
  const [label,setLabel]=useState('별도 보유 현금');
  const [assetCurrency,setAssetCurrency]=useState('KRW');
  const [amount,setAmount]=useState('100000');
  async function refresh(){try{setData(await request<Result>('/api/v1/stage1/manual-assets'));}catch{setMessage('자산 기록 조회 실패. 로그인과 서버 상태를 확인하세요.');}}
  useEffect(()=>{void refresh();},[]);
  async function change(path:string,body:unknown){
    setBusy(true);setMessage('');
    try{await request(path,body);await refresh();setMessage('수동 보유 기록을 수정했습니다. 모의투자 원장과 별도입니다.');}
    catch(e){setMessage('수정 실패: '+(e instanceof Error?e.message:'입력값을 확인하세요.'));}
    finally{setBusy(false);}
  }
  return <section className="card manual-assets" aria-label="사용자 직접 자산 입력">
    <details>
      <summary><span><strong>직접 자산 및 보유주식 관리</strong><small>자주 사용하지 않는 입력 기능 · 필요할 때만 펼치기</small></span><span className="manual-chevron" aria-hidden="true">⌄</span></summary>
      <div className="manual-assets-content">
    <h2>수동 기록 추가·차감</h2>
    <p>직접 입력한 주식과 자산만 기록합니다. 실제 주문이나 자동 모의투자 계좌의 현금·손익에는 반영되지 않습니다.</p>
    <h3>주식·ETF 수량</h3>
    <div className="manual-form-grid">
      <select aria-label="보유 주식 계좌" value={account} onChange={e=>setAccount(e.target.value)}><option value="default">미국 계좌</option><option value="kr-base">한국 계좌</option><option value="isa-base">ISA</option></select>
      <input aria-label="보유 종목 코드" value={symbol} onChange={e=>setSymbol(e.target.value)} placeholder="AAPL / 005930"/>
      <select aria-label="보유 주식 통화" value={currency} onChange={e=>setCurrency(e.target.value)}><option>USD</option><option>KRW</option></select>
      <input aria-label="주식 수량" inputMode="decimal" value={quantity} onChange={e=>setQuantity(e.target.value)}/>
      <button type="button" disabled={busy} onClick={()=>void change('/api/v1/stage1/manual-assets',{account,symbol,currency,quantity,side:'ADD'})}>+ 주식 추가</button>
      <button type="button" disabled={busy} onClick={()=>void change('/api/v1/stage1/manual-assets',{account,symbol,currency,quantity,side:'REMOVE'})}>− 주식 차감</button>
    </div>
    <h3>현금·예금 등 별도 자산</h3>
    <div className="manual-form-grid">
      <input aria-label="직접 자산 이름" value={label} onChange={e=>setLabel(e.target.value)} placeholder="예금 / 현금 등"/>
      <input aria-label="증감할 자산 금액" inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)}/>
      <select aria-label="자산 통화" value={assetCurrency} onChange={e=>setAssetCurrency(e.target.value)}><option>KRW</option><option>USD</option></select>
      <button type="button" disabled={busy} onClick={()=>void change('/api/v1/stage1/manual-assets/balance',{label,currency:assetCurrency,amount,side:'ADD'})}>+ 자산 추가</button>
      <button type="button" disabled={busy} onClick={()=>void change('/api/v1/stage1/manual-assets/balance',{label,currency:assetCurrency,amount,side:'REMOVE'})}>− 자산 차감</button>
      <button type="button" onClick={()=>void refresh()}>목록 갱신</button>
    </div>
    {message&&<p role="status">{message}</p>}
    <h3>저장된 주식 기록</h3>
    {(data?.items??[]).map(x=><p key={x.account+x.symbol+x.currency}>{x.account} · {x.symbol} · {x.quantity}주 ({x.currency})</p>)}
    {!data?.items.length&&<p>직접 등록된 주식이 없습니다.</p>}
    <h3>저장된 현금·기타 자산</h3>
    {(data?.balances??[]).map(x=><p key={x.label+x.currency}>{x.label} · {x.amount} {x.currency}</p>)}
    {!data?.balances.length&&<p>직접 등록된 자산이 없습니다.</p>}
      </div>
    </details>
  </section>;
}

export function ManualAssetsSummary(){
  const [data,setData]=useState<Result|null>(null);
  const [error,setError]=useState(false);
  useEffect(()=>{let active=true;
    void request<Result>('/api/v1/stage1/manual-assets')
      .then(r=>{if(active)setData(r);}).catch(()=>{if(active)setError(true);});
    return()=>{active=false;};
  },[]);
  return <section className="card manual-summary" aria-label="직접 등록한 자산 요약">
    <div className="card-title"><h2>직접 등록한 자산</h2><a href="/settings">설정에서 관리 →</a></div>
    <p>아래 자료는 직접 입력한 기록입니다. 실제 증권사 계좌·자동 모의투자·총자산 평가에는 합산하지 않습니다.</p>
    {error&&<p role="status">직접 입력한 자산 조회 실패 · 설정에서 로그인·저장 상태 확인</p>}
    {data&&<>
      <h3>보유주식 · {data.items.length}종</h3>
      {!data.items.length&&<p>직접 등록한 주식이 없습니다.</p>}
      {data.items.map(item=><p key={item.account+item.symbol+item.currency}>{item.symbol} · {item.quantity}주 · {item.currency}</p>)}
      <h3>기타 자산 · {data.balances.length}건</h3>
      {!data.balances.length&&<p>직접 등록한 현금성 자산이 없습니다.</p>}
      {data.balances.map(item=><p key={item.label+item.currency}>{item.label} · {item.amount} {item.currency}</p>)}
    </>}
  </section>;
}
