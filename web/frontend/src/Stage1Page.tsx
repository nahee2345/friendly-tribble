import { useEffect, useState } from 'react';
import './stage1.css';

type A={id:string;name:string;kind:string;krw_cash:string;usd_cash:string;holdings:{symbol:string;quantity:number}[];trades:number};
type State={initial_krw:number;monthly_krw:number;deposit_day:number;reinvest:boolean;auto_trading_enabled?:boolean;events:{id:string;kind:string;krw:number;usd:string}[]};
type Article={title_original:string;title_ko:string|null;url:string;published:string;translation_status:string};
type Quote={status:string;tradeable:boolean;symbol?:string;price?:string;currency?:string;as_of?:string;delay_notice?:string};
const money=(v:string|number)=>Number(v).toLocaleString('ko-KR',{maximumFractionDigits:2});
const newKey=()=>`stage1_${crypto.randomUUID()}`;
function csrf(){const item=document.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('wealthy_csrf='));return item?decodeURIComponent(item.slice('wealthy_csrf='.length)):'';}
async function get<T>(path:string):Promise<T>{const r=await fetch(path,{credentials:'same-origin',cache:'no-store'});if(!r.ok)throw Error(`조회 실패 (${r.status})`);return r.json();}
async function post<T>(path:string,value:unknown):Promise<T>{const r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify(value)});if(!r.ok)throw Error(`요청이 거부되었습니다 (${r.status}). 입력·잔액·시세를 확인해 주세요.`);return r.json();}
const accountTypes:Record<string,string>={GENERAL_OVERSEAS:'해외주식 일반계좌',GENERAL_DOMESTIC:'국내주식 일반계좌',ISA_SIMULATED:'ISA 가상계좌'};
export function Stage1Page(){
  const [fund,setFund]=useState<State|null>(null);
  const [accounts,setAccounts]=useState<A[]>([]);
  const [selected,setSelected]=useState('default');
  const [newName,setNewName]=useState('');const [newKind,setNewKind]=useState('GENERAL_OVERSEAS');
  const [news,setNews]=useState<{US:Article[];KR:Article[]}>({US:[],KR:[]});
  const [section,setSection]=useState<'overview'|'trade'|'news'|'history'|'reports'|'discover'>('discover');
  const [symbol,setSymbol]=useState('AAPL');const [qty,setQty]=useState(1);const [side,setSide]=useState<'BUY'|'SELL'>('BUY');
  const [quote,setQuote]=useState<Quote|null>(null);const [fx,setFx]=useState<{status:string;usd_krw?:string;as_of?:string}|null>(null);
  const [fxAmount,setFxAmount]=useState(100000);const [transferAmount,setTransferAmount]=useState(100000);
  const [orders,setOrders]=useState<{id:string;symbol:string;side:string;quantity:number;price:string;as_of:string}[]>([]);
  const [preferences,setPreferences]=useState({us_weight:90,kr_weight:10,etf_weight:80,stock_weight:20});
  const [fundingPolicy,setFundingPolicy]=useState({us_pct:80,kr_pct:10,isa_pct:10});
  const [dailyReports,setDailyReports]=useState<{date:string;checked_at:string;us:{status:string;symbol?:string;reason?:string};domestic:{account:string;status:string;symbol?:string;reason?:string}[];exits:{account:string;symbol:string;status:string}[]}[]>([]);
  type Candidate={symbol:string;name:string;kind:string;market:string;thesis:string;risks:string;issuer_url:string;quote:Quote;rating:string;account:string; evidence:string[]};
  const [candidates,setCandidates]=useState<Candidate[]>([]);
  const [loadingCandidates,setLoadingCandidates]=useState(false);
  async function discover(){setLoadingCandidates(true);try{const result=await get<{items:Candidate[]}>('/api/v1/stage1/candidates');setCandidates(result.items);}catch(e){setError(String(e));}finally{setLoadingCandidates(false);}}
  async function savePreferences(){await action(()=>post('/api/v1/stage1/preferences',{us_weight:preferences.us_weight,etf_weight:preferences.etf_weight}),'투자 선호 비중이 저장되었습니다.');void discover();}
  const [performance,setPerformance]=useState<{complete:boolean;contributions_krw:number;portfolio_value_krw:string|null;return_krw:string|null;return_pct:string|null;missing:{account:string;symbol:string;reason:string}[]}|null>(null);
  const [auto,setAuto]=useState<{enabled:boolean;last_checked_at:string|null;last_result:{status:string;reason?:string;symbol?:string;domestic?:{account:string;status:string;symbol?:string;reason?:string}[];exits?:{account:string;status:string;symbol:string;side?:string}[]}}|null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  async function refresh(){try{const f=await get<State>('/api/v1/stage1/account');get<typeof performance>('/api/v1/stage1/performance').then(x=>setPerformance(x)).catch(()=>setPerformance(null));const p=await get<{accounts:A[]}>('/api/v1/stage1/portfolios');setFund(f);setAccounts(p.accounts);setError('');}catch(e){setError(String(e));}}
  useEffect(()=>{void refresh();get<typeof preferences>('/api/v1/stage1/preferences').then(setPreferences).catch(()=>{});get<typeof fundingPolicy>('/api/v1/stage1/funding-allocation').then(setFundingPolicy).catch(()=>{});get<{items:typeof dailyReports}>('/api/v1/stage1/daily-reports').then(x=>setDailyReports(x.items)).catch(()=>{});void discover();get<{status:string;usd_krw?:string;as_of?:string}>('/api/v1/stage1/fx').then(setFx).catch(()=>{});(['US','KR'] as const).forEach(region=>get<{items:Article[]}>('/api/v1/stage1/news/'+region).then(x=>setNews(prev=>({...prev,[region]:x.items||[]}))).catch(()=>{}));},[]);
  useEffect(()=>{let active=true;const fetchStatus=()=>{get<{enabled:boolean;last_checked_at:string|null;last_result:{status:string;reason?:string;symbol?:string}}>('/api/v1/stage1/auto-status').then(x=>{if(active)setAuto(x);}).catch(()=>{});};fetchStatus();const handle=setInterval(fetchStatus,60_000);return()=>{active=false;clearInterval(handle);};},[]);
  useEffect(()=>{get<{orders:typeof orders}>(`/api/v1/stage1/portfolios/${encodeURIComponent(selected)}/orders`).then(x=>setOrders(x.orders)).catch(()=>setOrders([]));},[selected,accounts.length]);
  const current=accounts.find(x=>x.id===selected)||accounts[0];
  async function action(fn:()=>Promise<unknown>,done:string){setBusy(true);setError('');setNotice('');try{await fn();await refresh();setNotice(done);if(current){const x=await get<{orders:typeof orders}>(`/api/v1/stage1/portfolios/${encodeURIComponent(selected)}/orders`);setOrders(x.orders);}}catch(e){setError(String(e));}finally{setBusy(false);}}
  const displayValue=(accounts.reduce((v,a)=>v+Number(a.krw_cash),0));
  return <div className="s1-shell">
    <div className="s1-heading"><div><div className="s1-eyebrow">WEALTHY / PAPER INVESTING</div><p className="s1-desc">기본 계좌: 미국 주식 · 한국 주식 · ISA 각 1개. 시작금 400만 원(미국 80% / 국내 10% / ISA 10%), 매월 25일 35만 원을 동일 비율로 자동 가상 입금합니다.</p><h1>가상 투자 워크스페이스</h1><p>실제 금융계좌와 분리된 모의투자 · 서버 실행 중 1시간마다 검토 · 지연 종가 기준</p></div><span className="s1-pill">{fund?.auto_trading_enabled?'● 자동 모의투자 실행 중':'● 모의거래 전용 / 자동 운용 대기'}</span></div>
    {error&&<div className="s1-error" role="alert">{error}</div>}{notice&&<div className="s1-notice" role="status">{notice}</div>}
    <section className="s1-panel"><div className="s1-panelhead"><h2>자동 가상투자 운용</h2><span>{auto?.enabled?'서버 실행 중':'검토 대기'}</span></div>
      <p className="s1-desc">서버 시작 시 1회, 이후 약 1시간마다 시세·환율을 확인합니다. 미국 ETF에 한해 하루 최대 1주, 최초 투자금의 5% 이내에서 가상 거래합니다. 시세가 없으면 거래하지 않습니다.</p>
      <p className="s1-desc">최근 검사: {auto?.last_checked_at??'대기 중'} · 결과: {auto?.last_result.status??'대기'} {auto?.last_result.symbol??''} {auto?.last_result.reason??''}</p>
      {auto?.last_result.domestic?.map((item,i)=><p className="s1-desc" key={i}>{item.account==='kr-base'?'한국 일반계좌':'ISA 계좌'}: {item.status} · {item.symbol??''} {item.reason??''}</p>)}
      {auto?.last_result.exits?.map((item,i)=><p className="s1-desc" key={'exit-'+i}>가상 매도 검사: {item.account} · {item.symbol} · {item.status}</p>)}
      <p className="s1-desc">주식 시세: Stooq 지연 일간 종가 · 환율: Frankfurter 참고환율 · 국내주식/ISA 자동 체결: 미지원</p></section>
    <section className="s1-panel"><div className="s1-panelhead"><h2>전체 가상 포트폴리오 성과</h2><span>{performance?.complete?'검증된 지연 종가 기준':'가격·환율 추가 확인 필요'}</span></div>
      <div className="s1-balances"><div><small>누적 투자원금</small><strong>₩ {money(performance?.contributions_krw??fund?.initial_krw??4000000)}</strong></div><div><small>총 자산 평가</small><strong>{performance?.complete?'₩ '+money(performance.portfolio_value_krw??'0'):'미확정'}</strong></div><div><small>원금 대비 수익률</small><strong>{performance?.complete?Number(performance.return_pct).toFixed(2)+'%':'미확정'}</strong></div></div>
      {performance?.missing?.length? <p className="s1-desc">시세 누락: {performance.missing.map(x=>x.symbol).join(', ')} · 가격 미확인 종목은 임의 평가하지 않습니다.</p>:null}
      <p className="s1-desc">입금은 투자 수익에서 제외합니다. 거래비용·세금·배당금 정산은 아직 반영되지 않았습니다.</p></section>
    <div className="s1-stats">
      <div className="s1-tile"><small>전체 가상 원화 현금</small><strong>₩ {money(displayValue)}</strong><span>계좌 {accounts.length}개 합계</span></div>
      <div className="s1-tile"><small>가상 달러 현금</small><strong>$ {money(accounts.reduce((v,a)=>v+Number(a.usd_cash),0))}</strong><span>미국 주식 모의매수에 사용</span></div>
      <div className="s1-tile"><small>매월 자동 적립</small><strong>₩ {money(fund?.monthly_krw??350000)}</strong><span>매월 {fund?.deposit_day??25}일 · 최초 입금 400만원</span></div>
      <div className="s1-tile"><small>지연 기준환율 USD/KRW</small><strong>{fx?.status==='ok'?`₩ ${money(fx.usd_krw??'0')}`:'조회 불가'}</strong><span>{fx?.as_of??'시세 검증 대기'} · 실거래 호가 아님</span></div>
    </div>
    <div className="s1-tabs">{([['discover','ETF·종목 후보'],['overview','계좌 관리'],['trade','가상 거래'],['history','거래 내역'],['reports','자동 보고서'],['news','시장 뉴스']] as const).map(([id,label])=><button key={id} type="button" className={section===id?'selected':''} onClick={()=>setSection(id)}>{label}</button>)}</div>
    {section==='discover'&&<div className="s1-discovery">
      <section className="s1-panel s1-preferences"><div className="s1-panelhead"><h2>모의투자 선호 설정</h2><span>사용자별 저장</span></div>
        <div className="s1-preference-grid"><label>미국 시장 {preferences.us_weight}% / 한국 시장 {100-preferences.us_weight}%<input type="range" min="0" max="100" step="5" value={preferences.us_weight} onChange={e=>setPreferences(p=>({...p,us_weight:Number(e.target.value),kr_weight:100-Number(e.target.value)}))}/></label>
        <label>ETF {preferences.etf_weight}% / 개별주 {100-preferences.etf_weight}%<input type="range" min="0" max="100" step="5" value={preferences.etf_weight} onChange={e=>setPreferences(p=>({...p,etf_weight:Number(e.target.value),stock_weight:100-Number(e.target.value)}))}/></label></div>
        <button className="s1-primary" disabled={busy} onClick={()=>void savePreferences()}>비중 저장</button><p className="s1-desc">선호 비중은 조사 후보의 표시 우선순위에만 반영됩니다. 실제 자동매매나 무검증 국내주식 거래에는 사용되지 않습니다.</p>
      </section>
      <section className="s1-panel"><h2>가상 ETF 1회 자동 실행</h2><p className="s1-desc">미국·ETF 선호 비중이 각각 50% 이상이면 검증된 지연 종가로 미국 ETF 최대 1주를 가상 매수합니다. 최초 투자금의 5%를 넘지 않으며 필요한 경우 참고환율로 가상 환전을 기록합니다. 하루 중복 주문은 차단됩니다. 24시간 무인 자동매매나 AI 수익 예측은 아닙니다.</p><button className="s1-primary" disabled={busy} onClick={()=>void action(async()=>{const r=await post<{status:string;symbol?:string;reason?:string}>('/api/v1/stage1/auto-run',{});if(r.status!=='executed')throw Error(r.reason??'해당 조건에서 주문이 실행되지 않았습니다.');},'ETF 가상 주문 1건이 기록되었습니다.')}>{busy?'검증 중…':'ETF 가상 모의투자 1회 실행'}</button></section>
      <section className="s1-panel"><h2>국내 ETF 조사 후보 · 한국/ISA 모의거래</h2>
        <p className="s1-desc">국내 ETF는 6자리 종목코드의 공개 지연 종가가 검증될 때만 가상매수가 가능합니다. 출처: 비공식 Yahoo Finance 일간 차트. 투자수익 보장 또는 실시간 주문이 아닙니다.</p>
        <div className="s1-candidates">
          {([['069500','KODEX 200','국내 KOSPI 200 대형주 지수','kr-base'],
             ['360750','TIGER 미국S&P500','한국 거래소 상장 미국 대형주 ETF, 환헤지 여부 등 개별 상품 설명 확인','isa-base']] as const).map(([code,name,thesis,account])=>
          <article className="s1-candidate" key={code}><h3>{code} · {name}</h3><p>{thesis}</p><small>위험: 주가 하락·추적 오차·상품 구조 및 계좌 세제 확인 필요</small>
            <div className="s1-candidate-actions"><button className="s1-primary" onClick={()=>{setSelected(account);setSymbol(code);setQuote(null);setSide('BUY');setQty(1);setSection('trade');}}>국내 ETF 가상 주문 열기</button></div></article>)}
        </div>
      </section>
      <section className="s1-panel"><div className="s1-panelhead"><h2>미국 ETF·주식 조사 후보</h2><button className="s1-outline" disabled={loadingCandidates} onClick={()=>void discover()}>{loadingCandidates?'시세 조회 중…':'후보·가격 새로고침'}</button></div>
      <p className="s1-desc">미국 자산 우선 · ETF 선호 반영 · 공식 발행사 자료와 지연 종가. 예상 수익 또는 AI 매수 신호가 아닙니다.</p>
      <div className="s1-candidates">{candidates.map(c=><article className="s1-candidate" key={c.symbol}><div className="s1-candidate-header"><div><span className="s1-symbol">{c.symbol}</span> <span className="s1-kind">{c.kind==='ETF'?'ETF':'개별주'}</span><h3>{c.name}</h3></div><strong>{c.quote.tradeable?`$${money(c.quote.price??0)}`:'시세 미확인'}</strong></div>
        <p>{c.thesis}</p><small>위험: {c.risks}</small><small>{c.account}</small><small>{c.quote.as_of?`시세 기준일 ${c.quote.as_of} · 지연 종가`:'시세 검증 실패 · 주문 차단'}</small>
        <div className="s1-candidate-actions"><a href={c.issuer_url} target="_blank" rel="noopener noreferrer">운용사·기업 공식 자료 보기 ↗</a><button disabled={!c.quote.tradeable} className="s1-primary" onClick={()=>{setSymbol(c.symbol);setSide('BUY');setQty(1);setQuote(c.quote);setSection('trade');}}>이 종목 가상 매수하기</button></div>
      </article>)}</div>
      {!loadingCandidates&&!candidates.length&&<p className="s1-empty">조사 후보를 불러오지 못했습니다. 네트워크 상태를 확인해 주세요.</p>}
      <p className="s1-desc">국내 주식은 검증된 가격 공급원과 계좌·세제 검사가 연결되기 전까지 주문할 수 없습니다. 미국 주식도 주문 순간 서버에서 가격을 재확인합니다.</p>
      </section>
    </div>}
    {section==='overview'&&<section className="s1-panel">
      <h2>신규 입금 배분 설정</h2>
      <p className="s1-desc">초기자본 400만원은 기존 설정대로 유지됩니다. 매월 25일 35만원의 다음 미배분 입금부터 적용합니다. 세 계좌 비중의 합계는 100%여야 합니다.</p>
      <div className="s1-preference-grid">
        <label>미국 주식 계좌 (%)<input type="number" min="0" max="100" value={fundingPolicy.us_pct} onChange={e=>setFundingPolicy(p=>({...p,us_pct:Number(e.target.value)}))}/></label>
        <label>한국 주식 계좌 (%)<input type="number" min="0" max="100" value={fundingPolicy.kr_pct} onChange={e=>setFundingPolicy(p=>({...p,kr_pct:Number(e.target.value)}))}/></label>
        <label>ISA 가상계좌 (%)<input type="number" min="0" max="100" value={fundingPolicy.isa_pct} onChange={e=>setFundingPolicy(p=>({...p,isa_pct:Number(e.target.value)}))}/></label>
      </div>
      <p className="s1-desc">합계: {fundingPolicy.us_pct+fundingPolicy.kr_pct+fundingPolicy.isa_pct}%</p>
      <button className="s1-primary" disabled={busy||fundingPolicy.us_pct+fundingPolicy.kr_pct+fundingPolicy.isa_pct!==100} onClick={()=>void action(async()=>{const next=await post<typeof fundingPolicy>('/api/v1/stage1/funding-allocation',fundingPolicy);setFundingPolicy(next);},'미래 입금 배분 비율이 저장되었습니다.')}>미래 입금 배분 저장</button>
    </section>}
    {section==='overview'&&<div className="s1-columns"><section className="s1-panel"><div className="s1-panelhead"><h2>내 가상계좌</h2><span>최대 10개</span></div><div className="s1-accountlist">{accounts.map(a=><button key={a.id} className={`s1-account ${selected===a.id?'active':''}`} onClick={()=>setSelected(a.id)}><div><b>{a.name}</b><small>{accountTypes[a.kind]||a.kind}</small></div><strong>₩ {money(a.krw_cash)}</strong></button>)}</div><form className="s1-form" onSubmit={e=>{e.preventDefault();void action(()=>post('/api/v1/stage1/portfolios',{name:newName,kind:newKind}),'가상계좌가 추가되었습니다.').then(()=>setNewName(''));}}><h3>계좌 추가</h3><input aria-label="새 계좌 이름" value={newName} maxLength={40} required onChange={e=>setNewName(e.target.value)} placeholder="예: 배당 ETF 계좌"/><select aria-label="새 계좌 유형" value={newKind} onChange={e=>setNewKind(e.target.value)}>{Object.entries(accountTypes).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select><button className="s1-primary" disabled={busy||!newName.trim()}>+ 가상계좌 추가</button></form></section>
    <section className="s1-panel"><div className="s1-panelhead"><h2>선택한 계좌</h2><span>{current?.name??'조회 중'}</span></div>{current&&<><div className="s1-balances"><div><small>원화 잔고</small><strong>₩ {money(current.krw_cash)}</strong></div><div><small>달러 잔고</small><strong>$ {money(current.usd_cash)}</strong></div></div><h3>보유 종목</h3>{current.holdings.length?<div className="s1-holdings">{current.holdings.map(h=><div key={h.symbol}><b>{h.symbol}</b><span>{h.quantity.toLocaleString()}주</span></div>)}</div>:<p className="s1-empty">아직 보유한 종목이 없습니다.</p>}{current.id!=='default'&&<form className="s1-form" onSubmit={e=>{e.preventDefault();void action(()=>post('/api/v1/stage1/transfer',{source:'default',destination:current.id,amount_krw:transferAmount,request_id:newKey()}),'기본 계좌에서 가상 원화를 이동했습니다.');}}><h3>기본 계좌에서 가상자금 이동</h3><input aria-label="이동할 원화 금액" type="number" min="1" value={transferAmount} onChange={e=>setTransferAmount(Number(e.target.value))}/><button className="s1-primary" disabled={busy}>계좌 간 이동</button></form>}{current.id==='default'&&<form className="s1-form" onSubmit={e=>{e.preventDefault();if(!fx||fx.status!=='ok'||!fx.usd_krw)return;void action(()=>post('/api/v1/stage1/exchange',{krw:fxAmount,usd_krw:fx.usd_krw,request_id:newKey()}),'가상 환전이 기록되었습니다.');}}><h3>가상 원화 → 달러 환전</h3><input aria-label="환전할 원화 금액" type="number" min="1" value={fxAmount} onChange={e=>setFxAmount(Number(e.target.value))}/><button className="s1-primary" disabled={busy||fx?.status!=='ok'}>기준환율로 가상 환전</button><small>환전 수수료 미반영 · 실거래 환율 아님</small></form>}</>}</section></div>}
    {section==='trade'&&<div className="s1-columns"><section className="s1-panel"><h2>가상 주식 주문</h2><p className="s1-desc">주문 시 서버에서 지연 종가를 다시 조회합니다. 가격을 확인할 수 없으면 거래가 거부됩니다.</p><label>매매 계좌<select value={selected} onChange={e=>setSelected(e.target.value)}>{accounts.map(a=><option key={a.id} value={a.id}>{a.name} · {accountTypes[a.kind]}</option>)}</select></label><div className="s1-side"><button className={side==='BUY'?'active':''} onClick={()=>setSide('BUY')}>가상 매수</button><button className={side==='SELL'?'active':''} onClick={()=>setSide('SELL')}>가상 매도</button></div><label>종목코드 (미국: VOO / 국내: 069500)<input value={symbol} maxLength={12} onChange={e=>{setSymbol(e.target.value.toUpperCase());setQuote(null);}} placeholder="VOO 또는 069500"/></label><label>주문 수량<input type="number" min="1" max="100000" value={qty} onChange={e=>setQty(Number(e.target.value))}/></label><button className="s1-outline" disabled={busy} onClick={()=>void get<Quote>('/api/v1/stage1/quote/'+encodeURIComponent(symbol)).then(setQuote).catch(()=>setQuote({status:'unavailable',tradeable:false}))}>시장 종가 조회</button>{quote&&<div className="s1-quote">{quote.tradeable?<><strong>{quote.symbol} · ${quote.price}</strong><span>{quote.as_of} / {quote.delay_notice}</span></>:<span>검증된 가격이 없습니다. 주문할 수 없습니다.</span>}</div>}<button className="s1-primary" disabled={busy||!quote?.tradeable||qty<=0||!Number.isInteger(qty)} onClick={()=>void action(()=>post('/api/v1/stage1/order',{portfolio:selected,symbol,side,quantity:qty,request_id:newKey()}),'모의 거래가 기록되었습니다.').then(()=>setQuote(null))}>{busy?'처리 중…':side==='BUY'?'가상 매수 실행':'가상 매도 실행'}</button></section><section className="s1-panel"><h2>거래 안전장치</h2><div className="s1-checks"><p>✓ 실제 증권계좌 및 주문 API 사용 안 함</p><p>✓ 서버에서 시세를 재검증</p><p>✓ 가상 잔고 및 보유수량 초과 주문 거부</p><p>✓ 계좌·사용자별 원장 분리</p><p>✓ 동일 요청 중복 체결 방지</p><p>✓ 데이터베이스 트랜잭션으로 확정</p></div><h3>현재 제한</h3><p className="s1-desc">미국·국내 주식은 지연 일간 종가 기반 가상 체결입니다. 국내 가격은 비공식 공개 데이터이며 검증 실패 시 주문이 차단됩니다. ISA는 승인된 국내 상장 ETF 2종(069500, 360750)만 허용합니다. 수수료·세금·배당·정교한 AI 판단은 미반영입니다.</p></section></div>}
    {section==='history'&&<div className="s1-panel"><div className="s1-panelhead"><h2>모의 거래 내역</h2><select aria-label="내역 계좌 선택" value={selected} onChange={e=>setSelected(e.target.value)}>{accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></div><div className="s1-table"><table><thead><tr><th>종목</th><th>구분</th><th>수량</th><th>가상 체결가</th><th>기준일</th></tr></thead><tbody>{orders.map(o=><tr key={o.id}><td><b>{o.symbol}</b></td><td>{o.side==='BUY'?'매수':'매도'}</td><td>{o.quantity}</td><td>{accounts.find(a=>a.id===selected)?.kind==='GENERAL_OVERSEAS'?'$':'₩'}{money(o.price)}</td><td>{o.as_of}</td></tr>)}</tbody></table>{!orders.length&&<p className="s1-empty">아직 모의 거래가 없습니다.</p>}</div></div>}
    {section==='reports'&&<section className="s1-panel"><div className="s1-panelhead"><h2>일별 자동 모의투자 보고서</h2><button className="s1-outline" onClick={()=>void get<{items:typeof dailyReports}>('/api/v1/stage1/daily-reports').then(x=>setDailyReports(x.items)).catch(()=>setError('보고서를 조회하지 못했습니다.'))}>새로고침</button></div>
        <p className="s1-desc">최근 30일, 한국 시간 기준. 서버 실행 중 생성된 가상 주문·검토 결과입니다. 뉴스 기반 투자 예측 결과가 아닙니다.</p>
        {dailyReports.length?dailyReports.map(item=><article className="s1-candidate" key={item.date}><h3>{item.date}</h3><small>{item.checked_at}</small><p>미국 ETF: {item.us.status} {item.us.symbol??''} {item.us.reason??''}</p>{item.domestic.map((d,i)=><p key={i}>{d.account==='kr-base'?'국내계좌':'ISA'}: {d.status} {d.symbol??''} {d.reason??''}</p>)}{item.exits.map((d,i)=><p key={'exit'+i}>가상 매도 검토: {d.symbol} {d.status}</p>)}</article>):<p className="s1-empty">기록된 자동 보고서가 없습니다. 웹 서버의 자동운용 실행 상태를 확인해 주세요.</p>}
      </section>}
    {section==='news'&&<div className="s1-columns">{(['US','KR'] as const).map(region=><section className="s1-panel" key={region}><div className="s1-panelhead"><h2>{region==='US'?'미국 시장 뉴스':'한국 시장 뉴스'}</h2><span>공개 RSS</span></div>{region==='US'&&<p className="s1-desc">외부 번역 서비스가 성공한 기사만 한국어로 표시합니다. 실패한 기사는 영어 원문을 유지합니다.</p>}{news[region].length?news[region].map((n,i)=><a className="s1-news" key={`${i}-${n.url}`} href={n.url} target="_blank" rel="noopener noreferrer"><b>{n.title_ko??n.title_original}</b><small>{n.translation_status==='translated_external'?'자동 번역 · 원문 확인 가능':n.translation_status==='original_ko'?'국문 기사':'번역 실패 · 영어 원문'} · {n.published}</small></a>):<p className="s1-empty">기사를 조회하지 못했습니다.</p>}</section>)}</div>}
    <p className="s1-footer">개발용 모의 투자 웹 · 실제 금전 거래 없음 · 검증되지 않은 자동매매 기능은 실행되지 않습니다.</p>
  </div>;
}
