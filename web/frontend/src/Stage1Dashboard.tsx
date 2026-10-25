import {useEffect,useMemo,useState} from 'react';
import './stage1-dashboard.css';

type Market={name:string;status:string;value:string|null;change_pct:number|null;as_of:string|null;source:string};
type Position={account:string;symbol:string;quantity:number;currency:string;price:string|null;value:string|null;pnl:string|null};
type Performance={complete:boolean;contributions_krw:number;portfolio_value_krw:string|null;return_krw:string|null;return_pct:string|null;missing:{account:string;symbol:string;reason:string}[];positions:Position[]};
type Recommendation={symbol:string;name:string;region:string;kind:string;account:string;status:string;decision:string;priority_score:number;price:string|null;currency:string;quantity_suggestion:number;holding_horizon:string;exit_review:string;why:string[];risks:string[];as_of?:string;source?:string};
type History={date:string;value_krw:string|null;contributed_krw:number;complete:boolean};
type Hub={markets:Market[];performance:Performance;history:History[];asset_scope?:{selected:string;total_label:string;included_accounts:string[];included_scopes:string[];excluded_scopes:string[];value_field:string;complete:boolean;note:string};accounts:{id:string;name:string;krw_cash:string;usd_cash:string;holdings:number;scope?:string}[];recommendations:Recommendation[];notice:string};
type PriceTrend={status:string;symbol:string;market:string;change_pct?:number;first_date?:string;as_of?:string;points?:{date:string;close:string}[];source?:string;reason?:string};
type News={title:string;title_ko?:string|null;translation_status?:string;publisher?:string;editorial_lean?:string;editorial_source?:string|null;url:string;market:string;symbols:string[];impact:string;reason:string;confidence:string;published_utc:string;source:string};
type NewsReport={status:string;summary:{checked:number;related:number;risk_suspected:number};news:News[];risk_suspected:News[];coverage:string;unavailable?:string[]};
type Security={market:string;symbol:string;name:string;kind:string;source:string;updated_at:string};
type Catalog={items:Security[];total:number;is_complete:boolean;catalog_status:Record<string,{status:string;download_rows:number}>;notice:string};
type Factor={status:'scored'|'unavailable';score:number|null;why:string[];risks:string[];metrics:Record<string,unknown>;sources:SourceEvidence[];reason?:string};
type SourceEvidence={kind:string;provider:string;source_url:string;as_of:string;sha256:string;verified_utc:string};
type QuantAssessment={symbol:string;status:string;recommendation:string;score:number|null;partial_score:number|null;coverage_pct:number;strategies:Record<string,Factor>;strategies_scored:string[];positive_reasons:string[];risk_reasons:string[];unknown_reasons:string[];source_evidence:SourceEvidence[];no_orders:boolean;paper_order_eligible:boolean};
type QuantBatch={status:string;items:QuantAssessment[];weights:Record<string,number>;evaluated:number;ready:number;scoring_basis:string};
type PaperPlan={status:string;reason:string;for_date?:string;portfolio_value_krw:string|null;cash_krw_equivalent:string|null;reserve_percent?:string;daily_spend_cap_krw:string|null;allocation:Record<string,{target_pct:string;held_krw:string;drift_krw:string}>;proposed_orders:{request_id:string;account:string;symbol:string;side:string;quantity:number;notional_krw:string;strategy_score:number|null;coverage_pct:number;reason:string}[];review:{symbol:string;account:string;reason:string;approval_required?:boolean}[];data_errors:string[];trade_enabled:boolean;quant_ready:number;quant_evaluated:number;rules:string[]};
type RiskMetric={status:string;annualized_volatility_pct:number;max_drawdown_pct:number;historical_5pct_daily_return_pct:number;historical_5pct_tail_avg_pct:number;observation_sessions:number};
type RiskEvidence={provider:string;url:string;as_of:string;sha256:string;verified_utc:string};
type Replay={status:string;reason?:string;period_start?:string;period_end?:string;strategy_return_pct?:number;buy_hold_return_pct?:number;strategy_max_drawdown_pct?:number;friction_each_switch_pct?:number;switches?:number;limitations?:string[]};
type RiskReport={engine_version:string;status:string;as_of_utc:string;holdings:{account:string;symbol:string;quantity:number;currency:string;value_krw:string|null;risk:RiskMetric|null;source:RiskEvidence|null}[];currency_proxies:{currency:string;risk?:RiskMetric;status:string}[];combined_krw_historical_risk:{reason:string;status:string};concentration:{symbol:string;share_of_invested_assets_pct:number;alert:boolean}[];possible_etf_index_overlap:{symbols:string[];reason:string}[];stress:{estimated_loss_krw:string;exposure_krw:string}|null;alerts:{symbol:string;level:string;reason:string;source:RiskEvidence|null;other_source?:RiskEvidence|null}[];valuation_missing:string[];candidate?:{symbol:string;risk:RiskMetric|null;guard:{status:string;reason:string};source:RiskEvidence|null;momentum_replay:Replay}|null};
type Stage8Order={symbol:string;side:string;account:string;quantity:number;krw_notional:string;hypothetical_unit_price:string;unit_currency:string;strategy_score:number|null;coverage_pct:number|null};
type Stage8Scenario={decision_explanation?:{scope:string;selection:string;voo_not_bought:string;spym:string;kospi200:string;risk_stop:string;no_historical_market_fact:boolean;verified_live_data_used:boolean};status:string;trust_label:string;scenario_for_kst:string;hypothetical_reference_as_of:string;initial_cash_krw:string;next_monthly_deposit_krw:string;strategy_example:{weights:Record<string,number>;allocation:{us_weight:number;kr_weight:number;etf_weight:number;stock_weight:number};note:string};starting_accounts:{account_id:string;cash_krw:string;cash_usd:string;positions:{symbol:string;quantity:number}[]}[];market_closed_check:{status:string;orders_executed:number};weekday_replay:{status:string;risk_budget_krw:string;estimated_spend_krw:string;orders:Stage8Order[];skipped:{symbol:string;reason?:string}[];ending_accounts:{account_id:string;cash_krw:string;cash_usd:string;positions:{symbol:string;quantity:number}[]}[];integrity:string;repeat_run_new_orders:number;funding_reconciled:boolean};price_fixture:Record<string,string|Record<string,string>>;important:string[];active_user_ledger_touched:boolean;provider_network_used:boolean;broker_used:boolean;ai_used:boolean};
type Stage8Result={status:string;mode:string;local_market:{today_kst:string;weekend:boolean;exchange_holiday_calendar_verified:boolean;notice:string};actual_owner:{initial_4000000_recorded:boolean;cash_krw:string;cash_usd:string;cash_only_total_krw:string|null;holdings_count:number;accounts:{id:string;name:string;krw_cash:string;usd_cash:string;holdings_count:number}[];valuation_notice:string};scenario:Stage8Scenario|{status:string;error_class?:string;orders:Stage8Order[]};not_actual_orders:boolean;no_user_trades_added:boolean;market_news_not_synthesized:boolean};
type Stage5Accounting={nav_history:History[];status:string;valuation_complete:boolean;market_valuation_krw:string|null;gross_change_after_contributions_krw:string|null;contributions_krw:string;gross_change_pct:string|null;reference_usd_krw:string|null;xirr:{status:string;pct:number|null;reason?:string};twr:{status:string;pct:number|null;reason?:string};native_gross_pnl:Record<string,{realized:string;unrealized:string}>;positions:{account:string;symbol:string;currency:string;unrealized_native:string;fx_effect_krw:string|null;market_price_effect_krw:string|null;fx_split_status:string;price_as_of:string;price_source_url:string}[];potential_dividends:{symbol:string;ex_date:string;potential_gross:string;currency:string;source_url:string}[];fee_analysis:{illustrative_trade_fees_krw:string|null;actual_fee_amount_complete:boolean};tax_illustrations:{status:string;domestic_isa?:{included_symbols:string[];taxable_gain_proxy_krw:string;general_scenario_tax_krw:string;isa_scenario_tax_krw:string;potential_isa_saving_krw:string}|null;warning?:string};missing:string[];warnings:string[]};


const strategyLabels:Record<string,string>={index:'장기지수',value:'가치',quality_growth:'우량성장',growth:'성장',high_dividend:'고배당',dividend_growth:'배당성장',momentum:'모멘텀',asset_allocation:'자산배분'};
type ReferenceHealth={current_references:Record<string,number>;recent_attempts:{kind:string;symbol:string;provider:string;checked_utc:string;status:string;error_code:string|null;retry_after_utc:string}[];paper_order_eligible:boolean;notice:string;sec_contact_configured:boolean};
const fmt=(x:string|number|null|undefined,d=0)=>x===null||x===undefined?'자료 없음':Number(x).toLocaleString('ko-KR',{maximumFractionDigits:d});
function csrf(){const token=document.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('wealthy_csrf='));return token?decodeURIComponent(token.slice(13)):'';}
async function read<T>(url:string):Promise<T>{const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});if(!r.ok)throw Error('데이터 요청 실패: '+r.status);return r.json();}
async function write<T>(url:string,payload:unknown):Promise<T>{const r=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify(payload)});if(!r.ok)throw Error('업데이트 실패: '+r.status);return r.json();}
function AssetGraph({data}:{data:History[]}){
  const values=data.filter(x=>x.value_krw!==null).map(x=>Number(x.value_krw));
  const contributed=data.map(x=>x.contributed_krw);
  const all=values.concat(contributed);
  if(data.length<2||!values.length)return <div className="wh-empty">자산 스냅샷 기록 중 · 두 날짜 이상 쌓이면 흐름을 표시합니다.</div>;
  const lo=Math.min(...all),hi=Math.max(...all),span=Math.max(hi-lo,1);
  const xy=(i:number,v:number)=>[12+i*400/(data.length-1),85-(v-lo)*70/span];
  const line=(get:(x:History)=>number|null)=>data.map((x,i)=>{const n=get(x);return n===null?'':(i===0?'M':'L')+xy(i,n).join(',')}).filter(Boolean).join(' ');
  return <svg viewBox="0 0 425 100" preserveAspectRatio="none" role="img" aria-label="PAPER 모의계좌의 날짜별 평가자산과 누적 가상입금 차트">
    {[19,49,79].map(y=><path key={y} d={'M12 '+y+' H412'} stroke="currentColor" strokeOpacity=".09" strokeDasharray="4 4"/>)}
    <path d={line(x=>x.contributed_krw)} fill="none" stroke="#88959e" strokeWidth="1.9" strokeDasharray="6 4"/>
    <path d={line(x=>x.value_krw===null?null:Number(x.value_krw))} fill="none" stroke="#16a085" strokeWidth="2.5"/>
  </svg>;
}
export function Stage1Dashboard({onAdvanced}:{onAdvanced:()=>void}){
  const [tab,setTab]=useState<'home'|'search'>(['/analysis','/search'].includes(window.location.pathname)?'search':'home');
  const [hub,setHub]=useState<Hub|null>(null);
  const [news,setNews]=useState<NewsReport|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [detail,setDetail]=useState<Recommendation|null>(null);
  const [selectedRisk,setSelectedRisk]=useState<News|null>(null);
  const [slide,setSlide]=useState(0);
  const [trend,setTrend]=useState<PriceTrend|null>(null);
  const [query,setQuery]=useState('');
  const [market,setMarket]=useState('ALL');
  const [kind,setKind]=useState('ALL');
  const [catalogOffset,setCatalogOffset]=useState(0);
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [screen,setScreen]=useState<{screened:number;ready:number;items:{symbol:string;status:string;score:number|null;coverage_pct:number}[]}|null>(null);
  const [selected,setSelected]=useState<Security|null>(null);
  const [selectedQuote,setSelectedQuote]=useState<{status:string;price?:string;currency?:string;as_of?:string;source?:string}|null>(null);
  const [riskUnread,setRiskUnread]=useState(0);
  const [referenceHealth,setReferenceHealth]=useState<ReferenceHealth|null>(null);
  const [quant,setQuant]=useState<QuantBatch|null>(null);
  const [plan,setPlan]=useState<PaperPlan|null>(null);
  const [accounting,setAccounting]=useState<Stage5Accounting|null>(null);
  const [showAccounting,setShowAccounting]=useState(false);
  const [showPlan,setShowPlan]=useState(false);
  const [stage8,setStage8]=useState<Stage8Result|null>(null);
  const [showStage8,setShowStage8]=useState(false);
  const [riskReview,setRiskReview]=useState<RiskReport|null>(null);
  const [showRiskReview,setShowRiskReview]=useState(false);
  const [selectedSymbolRisk,setSelectedSymbolRisk]=useState<RiskReport|null>(null);
  const [selectedAssessment,setSelectedAssessment]=useState<QuantAssessment|null>(null);
  const [showReferences,setShowReferences]=useState(false);
  async function loadHome(){
    setBusy(true);setError('');
    // Owner cash and sandbox explanation must be visible even when an
    // external quote/news provider stalls one of the expensive report APIs.
    void read<Stage8Result>('/api/v1/stage1/stage8-simulation')
      .then(setStage8).catch(()=>setStage8(null));
    // Render each panel as soon as its own API returns. Slow public news
    // feeds must never delay visible local owner cash or account data.
    const requests=[
      read<Hub>('/api/v1/stage1/hub').then(setHub).catch(()=>setError('시장·자산 정보 조회 실패. 계좌 자료를 별도 확인하세요.')),
      read<NewsReport>('/api/v1/stage1/news-30d').then(setNews).catch(()=>setNews({
        status:'unavailable',summary:{checked:0,related:0,risk_suspected:0},
        news:[],risk_suspected:[],coverage:'RSS 통신 또는 서버 처리 실패',unavailable:['KR','US']
      })),
      read<QuantBatch>('/api/v1/stage1/quantitative').then(setQuant).catch(()=>setQuant(null)),
      read<PaperPlan>('/api/v1/stage1/portfolio-plan').then(setPlan).catch(()=>setPlan(null)),
      read<Stage5Accounting>('/api/v1/stage1/accounting').then(setAccounting).catch(()=>setAccounting(null)),
      read<RiskReport>('/api/v1/stage1/risk-review').then(setRiskReview).catch(()=>setRiskReview(null))
    ];
    await Promise.allSettled(requests);

    const inbox=await read<{items:{event_id:string;read_at:string|null}[]}>('/api/v1/stage1/inbox').catch(()=>null);
    if(inbox)setRiskUnread(inbox.items.filter(x=>x.event_id.startsWith('risk:')&&!x.read_at).length);
    const reference=await read<ReferenceHealth>('/api/v1/stage1/reference/health').catch(()=>null);
    if(reference)setReferenceHealth(reference);
    setBusy(false);
  }
  useEffect(()=>{void loadHome();const interval=setInterval(()=>void loadHome(),300_000);return()=>clearInterval(interval);},[]);
  useEffect(()=>{if(tab!=='search')return;let active=true;
    const delay=setTimeout(()=>{read<Catalog>('/api/v1/stage1/securities?'+new URLSearchParams({q:query,market,kind,limit:'50',offset:String(catalogOffset)}))
      .then(r=>{if(active)setCatalog(r);}).catch(()=>{if(active)setError('종목목록 검색에 실패했습니다.');});},280);
    return()=>{active=false;clearTimeout(delay);};
  },[tab,query,market,kind,catalogOffset]);
  const relevantNews=useMemo(()=>detail&&news?news.news.filter(n=>n.symbols.includes(detail.symbol)).slice(0,5):[],[detail,news]);
  const activeQuant=useMemo(()=>detail?quant?.items.find(x=>x.symbol===detail.symbol)??null:null,[detail,quant]);
  const sandbox=stage8?.status==='ready'&&stage8.scenario.status==='completed_sandbox_only'
    ?stage8.scenario as Stage8Scenario:null;
  const actualOnlyCash=stage8?.actual_owner.cash_only_total_krw??null;
  const actualAccounts=stage8?.actual_owner.accounts.map(a=>({...a,holdings:a.holdings_count}))??[];
  const rankedRecommendations=useMemo(()=>{
    const items=(hub?.recommendations??[]).slice();
    if(!quant)return items;
    const lookup=new Map(quant.items.map(x=>[x.symbol,x]));
    return items.sort((a,b)=>{
      const qa=lookup.get(a.symbol),qb=lookup.get(b.symbol);
      const sa=qa?.score,sb=qb?.score;
      if(sa!==null&&sa!==undefined&&sb!==null&&sb!==undefined){
        if(sa!==sb)return sb-sa;
      }else if(sa!==null&&sa!==undefined)return -1;
      else if(sb!==null&&sb!==undefined)return 1;
      const coverage=(qb?.coverage_pct??0)-(qa?.coverage_pct??0);
      return coverage||b.priority_score-a.priority_score||a.symbol.localeCompare(b.symbol);
    });
  },[hub,quant]);
  const slides=detail?[
    {head:'투자 판단 요약',body:<><b>{activeQuant?.recommendation??'정량 평가 조회 중'}</b><p>{detail.name} ({detail.symbol})</p>
      <p>종합점수: {activeQuant?.score??'자료 부족'} · 자료 충족률 {activeQuant?.coverage_pct??'–'}%
        {activeQuant?.score===null&&activeQuant?.partial_score!==null&&<small> · 부분점수 {activeQuant?.partial_score}점 (추천 판정 보류)</small>}</p>
      <p>기초 매수 검토: {detail.decision} · 배정 계좌 {detail.account==='default'?'미국 일반':detail.account==='isa-base'?'ISA':'한국 일반'}</p>
      <p>지연 종가: {detail.price?(detail.currency==='USD'?'$':'₩')+fmt(detail.price,2):'미확인'} · 검토 수량 {detail.quantity_suggestion}주 · 기준일 {detail.as_of??'없음'}</p>
      <p>정량 점수는 주문과 연결되지 않으며 기존 상품 분류와 다른 결과가 나올 수 있습니다.</p></>},
    {head:'8개 투자전략 점수·충족률',body:<>
      <p>사용자 전략 비중 중 평가 가능한 비중: <b>{activeQuant?.coverage_pct??0}%</b></p>
      {Object.entries(strategyLabels).map(([code,label])=><div className="wh-factor" key={code}><b>{label}</b>
        <span>비중 {quant?.weights?.[code]??0}% · {activeQuant?.strategies[code]?.status==='scored'
          ?activeQuant?.strategies[code].score+'점':'자료 미확보'}</span>
        <small>{activeQuant?.strategies[code]?.status==='scored'
          ?activeQuant?.strategies[code].why[0]:activeQuant?.strategies[code]?.reason??'조회 중'}</small></div>)}
      <p>미평가 전략은 0점 또는 50점으로 채우지 않습니다.</p>
    </>},
    {head:'최근 30일 가격·추천 근거',body:<>{trend?.status==='ok'?<p><b>실제 최근 30일 종가 변화: {trend.change_pct}%</b> · {trend.first_date} ~ {trend.as_of} · {trend.source}</p>:<p>최근 30일 검증 종가는 {trend?.reason??'조회 중 또는 미확인'}입니다.</p>}{detail.why.map((w,i)=><p key={i}>{i+1}. {w}</p>)}<p>우선순위 {detail.priority_score}: 정책 비중과 상품 분류의 일치도입니다. 향후 수익 예측 점수가 아닙니다.</p></>},
    {head:'반대 근거·위험',body:<>
      {activeQuant?.risk_reasons.map((w,i)=><p key={'q'+i}>{i+1}. {w}</p>)}
      {detail.risks.map((w,i)=><p key={i}>기초 판단: {w}</p>)}
      {activeQuant?.unknown_reasons.slice(0,5).map((w,i)=><p key={'miss'+i}><small>자료 부족: {w}</small></p>)}
      <p>자료 누락과 지연 종가의 위험은 별도 확인이 필요합니다.</p></>},
    {head:'6단계 위험·매도 재검토',body:<>
      <p>보유기간 검토: {detail.holding_horizon}</p><p>{detail.exit_review}</p>
      <p><b>신규 매수 위험평가:</b> {selectedSymbolRisk?.candidate?.guard.reason??'과거 시세 검증 대기'}</p>
      {selectedSymbolRisk?.candidate?.risk?<p>최근 63거래일 연환산 변동성 {selectedSymbolRisk.candidate.risk.annualized_volatility_pct}%
        · 최대낙폭 {selectedSymbolRisk.candidate.risk.max_drawdown_pct}%
        · 하위 5% 일간 수익률 {selectedSymbolRisk.candidate.risk.historical_5pct_daily_return_pct}%</p>:
        <p>검증된 65개 이상 종가가 없어 위험 수치를 임의로 생성하지 않았습니다.</p>}
      <p>확정 매도일이나 미래 수익 예측이 아닙니다. 위험 신호만으로 자동매도하지 않습니다.</p>
    </>},
    {head:'고정 모멘텀 규칙 · 30거래일 과거 재현',body:<>
      {selectedSymbolRisk?.candidate?.momentum_replay.status==='historical_replay'?<>
        <p>20일/60일 이동평균 규칙: {selectedSymbolRisk.candidate.momentum_replay.period_start} ~ {selectedSymbolRisk.candidate.momentum_replay.period_end}</p>
        <p>규칙 재현 {fmt(selectedSymbolRisk.candidate.momentum_replay.strategy_return_pct,2)}%
          · 같은 기간 단순 보유 {fmt(selectedSymbolRisk.candidate.momentum_replay.buy_hold_return_pct,2)}%</p>
        <p>전략 낙폭 {fmt(selectedSymbolRisk.candidate.momentum_replay.strategy_max_drawdown_pct,2)}%
          · 가정 거래비용 {selectedSymbolRisk.candidate.momentum_replay.friction_each_switch_pct}%/전환
          · 전환 {selectedSymbolRisk.candidate.momentum_replay.switches}회</p>
        {selectedSymbolRisk.candidate.momentum_replay.limitations?.map((x,i)=><p key={i}>{x}</p>)}
      </>:<p>미산정: {selectedSymbolRisk?.candidate?.momentum_replay.reason??'최소 95개 종가 필요'}</p>}
      <p>미래 수익 예측이나 실제 보유계좌 백테스트가 아닙니다. 8전략 전체 성과 검증은 자료 부족으로 보류합니다.</p>
      {selectedSymbolRisk?.candidate?.source&&<p><a href={selectedSymbolRisk.candidate.source.url} target="_blank" rel="noreferrer">
        검증된 과거 종가 원본 ↗</a></p>}
    </>},
    {head:'정량 분석 원본·기준일',body:<>
      <p>계산에 사용된 검증된 공개 자료만 표시합니다.</p>
      {activeQuant?.source_evidence.length?activeQuant.source_evidence.map((x,i)=><p key={i}>
        <b>{x.kind} · {x.provider}</b> · {x.as_of}<small> · SHA {x.sha256.slice(0,12)}…</small>
        <a href={x.source_url} target="_blank" rel="noreferrer"> 원본 보기 ↗</a>
      </p>):<p>정량 계산에 사용할 검증된 자료가 없습니다.</p>}
    </>},
    {head:'최근 30일 뉴스·출처',body:<>{relevantNews.length?relevantNews.map((n,i)=><p key={i}><b>{n.impact}</b> · <a href={n.url} target="_blank" rel="noreferrer">{n.title}</a><small> {n.published_utc.slice(0,10)} · {n.confidence}</small></p>):<p>관련성이 검증된 제목 매칭 뉴스가 없습니다. 호재·악재가 없다는 의미는 아닙니다.</p>}<p>가격 출처: {detail.source??'미확인'}</p></>}
  ]:[];
  async function refreshCatalog(){setBusy(true);try{await write('/api/v1/stage1/securities/refresh',{});setCatalog(await read<Catalog>('/api/v1/stage1/securities?'+new URLSearchParams({q:query,market,kind,limit:'50'})));}catch{setError('공개 종목 목록을 갱신하지 못했습니다.');}finally{setBusy(false);}}
  async function openSecurity(x:Security){setSelected(x);setSelectedQuote(null);setSelectedAssessment(null);setSelectedSymbolRisk(null);
    const [q,assessment,riskDetail]=await Promise.all([
      read<typeof selectedQuote>('/api/v1/stage1/quote/'+encodeURIComponent(x.symbol)).catch(()=>null),
      read<QuantAssessment>('/api/v1/stage1/quantitative/'+encodeURIComponent(x.symbol)).catch(()=>null),
      read<RiskReport>('/api/v1/stage1/risk-review/'+encodeURIComponent(x.symbol)).catch(()=>null)
    ]);
    setSelectedQuote(q);setSelectedAssessment(assessment);setSelectedSymbolRisk(riskDetail);
  }
  async function openRecommendation(x:Recommendation){setDetail(x);setSlide(0);setTrend(null);setSelectedSymbolRisk(null);
    const [trendResult,riskResult]=await Promise.all([
      read<PriceTrend>('/api/v1/stage1/last-30d/'+encodeURIComponent(x.symbol)).catch(()=>null),
      read<RiskReport>('/api/v1/stage1/risk-review/'+encodeURIComponent(x.symbol)).catch(()=>null)
    ]);
    setTrend(trendResult);setSelectedSymbolRisk(riskResult);
  }
  return <div className="wh-wrap">
    <header className="wh-header"><div><b>WEALTHY</b><small>미국·한국 자동 모의투자 · 실시간 호가 아님</small></div>
      <nav><button className={tab==='home'?'on':''} onClick={()=>setTab('home')}>통합 대시보드</button>
      <button className={tab==='search'?'on':''} onClick={()=>setTab('search')}>전체 종목 검색</button>
      <button onClick={onAdvanced}>자산·설정·보고서</button></nav>
      <button aria-label="새로고침" onClick={()=>void loadHome()} disabled={busy}>↻</button>
      <button className="wh-refhealth" onClick={()=>setShowReferences(true)} title="공개자료 검증 및 공급자 상태">
        공개자료 {Object.values(referenceHealth?.current_references??{}).reduce((a,b)=>a+b,0)}종 · 오류 {referenceHealth?.recent_attempts.filter(x=>x.status==='unavailable').length??0}
      </button>
      <span title="보유종목 관련 하방 위험 의심 알림">위험 알림 {riskUnread}</span>
    </header>
    {error&&<div className="wh-error">{error}</div>}
    {tab==='home'&&<div className="wh-stage8-strip">
      <span><b>실제 가상계좌</b> {stage8?.actual_owner.initial_4000000_recorded?
        '초기 400만원 입금 확인':'초기 입금 확인 중'}</span>
      <span>{stage8?.local_market.weekend?'주말 · 시장 휴장 (자동주문 차단)':
        '거래일 여부: 주말 외 공식 휴장일 별도 확인'}</span>
      <button onClick={()=>setShowStage8(true)} aria-label="400만원 검증용 격리 모의투자 결과 보기">
        400만원 가정 모의투자 {sandbox?sandbox.weekday_replay.orders.length+'건 검증':'결과 확인'} · 상세 ›
      </button>
    </div>}
    {tab==='home'?<main className="wh-grid">
      <section className="wh-card wh-markets"><div className="wh-head"><b>시장 현황</b><small>지연 일간 종가 · 기준일 표시</small></div><div className="wh-indexgrid">
        {(hub?.markets??[{name:'KOSPI'},{name:'KOSDAQ'},{name:'S&P 500'},{name:'NASDAQ'}]).map(m=><div className="wh-index" key={m.name}><small>{m.name}</small><strong>{'value'in m&&m.value!==null&&m.value!==undefined?fmt(m.value,2):'미확인'}</strong><span>{'change_pct'in m&&m.change_pct!==null&&m.change_pct!==undefined?(m.change_pct>0?'+':'')+m.change_pct+'%':'등락 미확인'}</span><small title={'source'in m && m.source ? m.source : '공개 시장자료 수집 대기'}>{'as_of'in m&&m.as_of?'기준일 '+m.as_of:'시세 공급원 미응답 · 인터넷 연결과 데이터 제공 상태 확인'}</small></div>)}
        </div></section>
      <section className="wh-card wh-assets"><div className="wh-head"><b>PAPER 모의자산 흐름</b>
        <button className="wh-plan-trigger" onClick={()=>setShowAccounting(true)}>
          손익·환율 근거 ›
        </button></div>
        <small>합산: PAPER 미국·한국·ISA 모의계좌만 · 수동 실제자산·SAMPLE 제외</small>
        <div className="wh-total">{hub?.asset_scope?.selected!=='PAPER'?'영역 확인 필요':accounting?.valuation_complete?'₩'+fmt(accounting.market_valuation_krw):
          hub?.performance.complete?'₩'+fmt(hub.performance.portfolio_value_krw):
          actualOnlyCash!==null?'₩'+fmt(actualOnlyCash)+' (현금만)':'시세 확인 필요'}</div>
        <div className="wh-kpis"><span>가상 누적 입금 ₩{fmt(accounting?.contributions_krw??hub?.performance.contributions_krw)}</span>
          <span>PAPER 손익(세전) {accounting?.valuation_complete?'₩'+fmt(accounting.gross_change_after_contributions_krw):
            hub?.performance.complete?'₩'+fmt(hub.performance.return_krw):'미확정'}</span>
          <span>XIRR {accounting?.xirr.status==='ok'?fmt(accounting.xirr.pct,2)+'%':'미확정'}</span></div>
        <div className="wh-chart"><AssetGraph data={accounting?.nav_history??[]}/></div><div className="wh-legend"><span>━ PAPER 평가자산</span><span>┄ 가상 입금원금</span></div></section>
      <section className="wh-card wh-accounts"><div className="wh-head"><b>가상계좌</b><small>미국 · 한국 · ISA</small></div>
        {(hub?.accounts??actualAccounts).map(a=><div className="wh-account" key={a.id}><b>{a.id==='default'?'미국 일반':a.id==='kr-base'?'한국 일반':a.id==='isa-base'?'ISA':a.name}</b><span>₩{fmt(a.krw_cash)} · ${fmt(a.usd_cash,2)}</span><small>보유 {a.holdings}종</small></div>)}
        {!hub&&actualAccounts.length===0&&<p>실제 가상계좌 데이터 확인 중</p>}</section>
      <section className="wh-card wh-news"><div className="wh-head"><b>최근 30일 뉴스·보유종목 영향</b><small>{news?.summary.checked??0}건 검토 · 관련 {news?.summary.related??0}건</small></div>
        <div className="wh-feed">
          <h4>해외 뉴스 · 한국어 번역</h4>
          {(news?.news??[]).filter(n=>n.market==='US').slice(0,15).map((n,i)=><div className="wh-newsrow" key={'us'+i}>
            <span className={n.impact==='하방 위험 의심'?'wh-danger':''}>{n.impact}</span>
            <a href={n.url} target="_blank" rel="noreferrer">{n.title_ko??n.title}</a>
            <small>{n.title_ko?'자동 번역 · 원문 링크 제공':'번역 서비스 실패 · 영어 원문'} · {n.publisher??'언론사 미확인'}</small>
          </div>)}
          <h4>한국 뉴스 · 언론사별</h4>
          {[...new Set((news?.news??[]).filter(n=>n.market==='KR').map(n=>n.publisher??'언론사 미확인'))].map(pub=>
            <div key={pub} className="wh-publisher-group">
              <b>{pub}</b>
              {(news?.news??[]).filter(n=>n.market==='KR'&&(n.publisher??'언론사 미확인')===pub).map((n,i)=>
                <div className="wh-newsrow" key={'kr'+i}>
                  <span>{n.editorial_lean??'정치 성향 근거 미확인'}</span>
                  <a href={n.url} target="_blank" rel="noreferrer">{n.title}</a>
                  {n.editorial_source&&<small><a href={n.editorial_source} target="_blank" rel="noreferrer">성향 분류 연구 보기 ↗</a></small>}
                </div>)}
            </div>)}
          {!news?.news.length&&<p>{news?.status==='partial'?'일부 뉴스 수집 실패':news?.status==='ok'?'검색 범위에서 확인된 기사 없음':'뉴스 수집 실패'} · {news?.unavailable?.length?'미응답 시장: '+news.unavailable.join(', '):'공개 RSS 또는 네트워크 상태 미확인'}. 미국·한국 공개 뉴스 공급원이 응답하지 않으면 기사 내용을 임의로 생성하지 않습니다.</p>}
        </div>
        <small className="wh-caveat">한국 언론사의 정치 성향은 과거 연구의 분류를 참고한 것이며 해당 기사 자체의 성향이나 사실 여부를 뜻하지 않습니다. 해외 기사는 번역 실패 시 원문을 표시합니다.</small></section>
      <section className="wh-card wh-risks"><div className="wh-head"><b>보유종목 위험 알림</b>
        <button className="wh-plan-trigger" onClick={()=>setShowRiskReview(true)}
          title="과거 변동성·상관관계·가정 충격·위험 근거 확인">
          정량위험 {riskReview?.alerts.length??0}건 · 상세 ›
        </button></div>
        {(riskReview?.alerts??[]).slice(0,2).map((item,i)=><button className="wh-riskrow" key={'quant'+i}
          onClick={()=>setShowRiskReview(true)}><b>{item.symbol}</b> {item.reason}</button>)}
        {(news?.risk_suspected??[]).slice(0,(riskReview?.alerts.length??0)>0?1:3).map((n,i)=><button className="wh-riskrow" key={i} onClick={()=>setSelectedRisk(n)}><b>{n.symbols.join(', ')}</b> {n.title}</button>)}
        {!news?.risk_suspected.length&&!riskReview?.alerts.length&&<p>현재 검증된 부정 기사·가격 위험 신호가 없습니다. 위험이 없다는 뜻은 아닙니다.</p>}
        <small className="wh-caveat">뉴스가 손실을 확정한다는 뜻이 아닙니다. 서버 가동 중 확인 후 알림함에 저장됩니다.</small></section>
      <section className="wh-card wh-recs"><div className="wh-head"><b>정량 투자 검토 종목</b>
        <button className="wh-plan-trigger" onClick={()=>setShowPlan(true)}
          title="자산 전체 목표비중·투자계획·검증 근거 보기">
          전체 비중 계획 {plan?.status==='ready'?(plan?.proposed_orders.length??0)+'건 검토':'자료 확인 필요'} ›
        </button>
      </div><div className="wh-reclist">
        {rankedRecommendations.slice(0,5).map(x=>{
          const q=quant?.items.find(s=>s.symbol===x.symbol);
          return <button key={x.symbol} onClick={()=>void openRecommendation(x)}><b>{x.symbol}</b><span>{x.name}</span>
            <em>{q?.score!==null&&q?.score!==undefined?q.score+'점 · '+q.coverage_pct+'% 충족':q?'자료 부족 · '+q.coverage_pct+'%':'평가 대기'}</em>
            <small>{q?.recommendation??'기초 후보'} ›</small></button>;
        })}
        {!rankedRecommendations.length&&sandbox&&sandbox.weekday_replay.orders.map(o=>
          <button key={'stage8-'+o.symbol} onClick={()=>setShowStage8(true)}
            title="합성가격을 사용한 독립 시험용 종목이며 실제 매수 권유가 아닙니다.">
            <b>{o.symbol}</b><span>가정 모의투자 예시</span>
            <em>{o.strategy_score??'–'}점 · {o.coverage_pct??'–'}% 시험 충족</em>
            <small>실제 추천 아님 · 근거 ›</small>
          </button>)}
      </div><small className="wh-caveat">{!rankedRecommendations.length&&sandbox?
          '검증용 합성 시세 예시이며 실제 투자·추천이 아닙니다.':'정량 평가는 저장된 검증자료만 사용하며, 주문과 분리된 읽기 전용 연구입니다.'}</small></section>
    </main>:<main className="wh-search"><div className="wh-searchbar"><div><h2>미국·한국 주식·ETF 검색</h2><p>공개 상장목록 기반 검색 · 시세와 매매 적격성은 별도 확인</p></div>
       <nav aria-label="종목 시장별 빠른 선택" style={{display:'flex',gap:8,flexWrap:'wrap'}}>
         {([['US','STOCK','미국 주식'],['KR','STOCK','한국 주식'],['ALL','ETF','미국·한국 ETF'],['ALL','ALL','모든 종목']] as const).map(([m,k,label])=>
           <button key={label} type="button" aria-pressed={market===m&&kind===k}
             onClick={()=>{setMarket(m);setKind(k);setCatalogOffset(0);setScreen(null);setSelected(null);}}>
             {label}</button>)}
       </nav>
       <input value={query} onChange={e=>{setQuery(e.target.value);setCatalogOffset(0);}} placeholder="이름 또는 종목코드 (예: 삼성전자, AAPL)"/>
       <select value={market} onChange={e=>{setMarket(e.target.value);setCatalogOffset(0);}}><option value="ALL">전체 시장</option><option value="KR">한국</option><option value="US">미국</option></select>
       <select value={kind} onChange={e=>{setKind(e.target.value);setCatalogOffset(0);}}><option value="ALL">전체 상품</option><option value="STOCK">개별주</option><option value="ETF">ETF</option></select>
       <button disabled={busy} onClick={()=>void refreshCatalog()}>전체 목록 갱신</button></div>
       <div className="wh-catalog"><div className="wh-searchresults"><div className="wh-head"><b>검색 결과 {fmt(catalog?.total)}개</b><small>미국 {catalog?.catalog_status?.US?.download_rows??'미확인'}개 · 한국 {catalog?.catalog_status?.KR?.download_rows??'미확인'}개 수집 기록 · 최대 50개/페이지</small></div>
         <button disabled={busy} onClick={()=>{setScreen(null);void read<{screened:number;ready:number;items:{symbol:string;status:string;score:number|null;coverage_pct:number}[]}>('/api/v1/stage1/screen?'+new URLSearchParams({q:query,market,kind,limit:'20'})).then(setScreen).catch(()=>setError('정량 검색 결과를 확인할 수 없습니다.'));}}>현재 검색조건 상위 20개 정량 평가</button>
         {screen&&<div role="status"><b>평가 {screen.screened}종 · 분석 가능 {screen.ready}종</b><small>미확인 전략은 점수를 임의로 채우지 않습니다. 자동매매와 분리된 참고 정보입니다.</small>
           {screen.items.map(x=><p key={x.symbol}>{x.symbol} · {x.score===null?'자료 부족':x.score+'점'} · 충족률 {x.coverage_pct??0}%</p>)}</div>}
         {(catalog?.items??[]).map(x=><button key={x.market+x.symbol} onClick={()=>void openSecurity(x)}><b>{x.symbol}</b><span>{x.name}</span><small>{x.market} · {x.kind}</small></button>)}
         {!catalog?.items.length&&<p>검색 결과가 없습니다. 공개 목록 갱신을 눌러 미국 Nasdaq Trader·한국 KIND 목록을 불러오세요. 외부 사이트 연결 실패 시 기존 캐시만 표시됩니다.</p>}
         <div style={{display:'flex',gap:8,alignItems:'center'}}>
           <button type="button" disabled={catalogOffset===0} onClick={()=>setCatalogOffset(v=>Math.max(0,v-50))}>이전 50개</button>
           <span>{catalog?.total?catalogOffset+1:0}~{Math.min(catalogOffset+50,catalog?.total??0)} / {catalog?.total??0}개</span>
           <button type="button" disabled={!catalog||catalogOffset+50>=catalog.total} onClick={()=>setCatalogOffset(v=>v+50)}>다음 50개</button>
         </div>
         <p><small>상장 목록에는 거래정지·상장폐지 반영 지연이나 ETF 누락이 있을 수 있습니다. 검색 가능과 거래 가능은 다릅니다.</small></p></div>
       <div className="wh-security"><h3>종목 상세</h3>{selected?<><h2>{selected.name}</h2><p>{selected.symbol} · {selected.market} · {selected.kind}</p>
         <p>기준 종가: {selectedQuote?.status==='ok'?fmt(selectedQuote.price,2)+' '+(selectedQuote.currency||''):'검증되지 않음'}</p><p>기준일: {selectedQuote?.as_of??'미확인'}</p><p>출처: {selectedQuote?.source??selected.source}</p>
         <div className="wh-assessment"><b>8전략 정량 연구</b>
           <p>종합점수: {selectedAssessment?.score??'자료 부족'} · 충족률 {selectedAssessment?.coverage_pct??'–'}% · {selectedAssessment?.recommendation??'조회 중'}</p>
           {Object.entries(strategyLabels).map(([code,label])=><p key={code}>{label}: {selectedAssessment?.strategies[code]?.score??'미평가'}
             {selectedAssessment?.strategies[code]?.status!=='scored'&&<small> · {selectedAssessment?.strategies[code]?.reason??'자료 부족'}</small>}</p>)}
           <p><b>6단계 위험:</b> {selectedSymbolRisk?.candidate?.risk?
              ('변동성 '+selectedSymbolRisk.candidate.risk.annualized_volatility_pct+
              '% · 낙폭 '+selectedSymbolRisk.candidate.risk.max_drawdown_pct+'%'):
              selectedSymbolRisk?.candidate?.guard.reason??'검증 대기'}</p>
           <p>모멘텀 30거래일 재현: {selectedSymbolRisk?.candidate?.momentum_replay.status==='historical_replay'?
             '규칙 '+fmt(selectedSymbolRisk.candidate.momentum_replay.strategy_return_pct,2)+
             '% / 단순보유 '+fmt(selectedSymbolRisk.candidate.momentum_replay.buy_hold_return_pct,2)+'%':
             '자료 부족'}</p>
           {selectedSymbolRisk?.candidate?.source&&<p><a href={selectedSymbolRisk.candidate.source.url} target="_blank" rel="noreferrer">
             위험 계산 종가 출처 ↗</a></p>}
           <small>검증된 캐시 자료로만 계산합니다. 연구 점수는 주문을 승인하지 않습니다.</small>
         </div>
         <p>종목 검색은 자동매수 승인과 다릅니다. 검증되지 않은 데이터는 체결에 사용하지 않습니다.</p></>:<p>왼쪽에서 종목을 선택해 주세요.</p>}
         <small>{catalog?.notice??'종목목록 범위 확인 중'}</small></div></div>
      </main>}
    {showStage8&&<div className="wh-overlay" onClick={()=>setShowStage8(false)}>
      <div className="wh-modal" role="dialog" aria-modal="true" aria-label="8단계 가상투자 검증" onClick={e=>e.stopPropagation()}>
        <div className="wh-head"><b>8단계 · 400만원 모의투자 1회 검증</b><button onClick={()=>setShowStage8(false)} aria-label="닫기">✕</button></div>
        <div className="wh-slide">
          <h4>실제 사용자 가상계좌</h4>
          <p>초기 400만원: {stage8?.actual_owner.initial_4000000_recorded?"입금 확인":"확인 대기"}</p>
          <p>원화 현금 ₩{fmt(stage8?.actual_owner.cash_krw)} · 달러 현금 {fmt(stage8?.actual_owner.cash_usd,2)} USD · 실제 보유 {stage8?.actual_owner.holdings_count??0}종</p>
          <p>아래 내역은 별도 임시 SQLite 테스트이며 내 가상계좌의 매매나 추천이 아닙니다.</p>
          {sandbox?<>
            <h4>가정한 매수 근거와 조건</h4>
            <p>{sandbox.trust_label}</p>
            <p>자료일 {sandbox.hypothetical_reference_as_of} · 가정 평일 {sandbox.scenario_for_kst}</p>
            <p>{sandbox.strategy_example.note}</p>
            <p>주말 주문 {sandbox.market_closed_check.orders_executed}건 · 가정 평일 예산 ₩{fmt(sandbox.weekday_replay.risk_budget_krw)} · 실제 시험 매수 ₩{fmt(sandbox.weekday_replay.estimated_spend_krw)}</p>
            {sandbox.weekday_replay.orders.map((order,i)=><p key={i}>
              <b>{order.symbol}</b> · {order.account} · {order.quantity}주
              · 단가 {fmt(order.hypothetical_unit_price,2)} {order.unit_currency}
              · 원화 환산 ₩{fmt(order.krw_notional)}
              · 전략 점수 {order.strategy_score??"미산정"} · 충족률 {order.coverage_pct??0}%
            </p>)}
            <h4>왜 이 ETF를 선택했는가? (합성 시세 시험 근거)</h4>
            <p>{sandbox.decision_explanation?.selection??'알고리즘 기준 상세 근거 확인 대기'}</p>
            <p>{sandbox.decision_explanation?.spym}</p>
            <p>{sandbox.decision_explanation?.kospi200}</p>
            <p><b>VOO를 매수하지 않은 이유:</b> {sandbox.decision_explanation?.voo_not_bought}</p>
            <p>{sandbox.decision_explanation?.risk_stop}</p>
            <h4>가상체결 후 계좌</h4>
            {sandbox.weekday_replay.ending_accounts.map(a=><p key={a.account_id}>
              {a.account_id} · 현금 ₩{fmt(a.cash_krw)} / {fmt(a.cash_usd,2)} USD
              · 보유 {a.positions.map(x=>x.symbol+" "+x.quantity+"주").join(", ")||"없음"}
            </p>)}
            <p>중복 재체결 {sandbox.weekday_replay.repeat_run_new_orders}건 · 무결성 {sandbox.weekday_replay.integrity}</p>
            <h4>언제 매도하는가?</h4>
            <p>확정 매도일을 예측할 수 없습니다. 기존 기준은 -7% 손실, +15% 이익, 집중도와 목표비중 초과를 다시 검토하며 매도 전 새 시세를 요구합니다.</p>
            {sandbox.important.map((item,i)=><p key={i}><small>{item}</small></p>)}
          </>:<p>검증용 실행이 완료되지 않았습니다. 실제 시세나 임의 주문으로 결과를 대체하지 않습니다.</p>}
        </div>
      </div>
    </div>}
    {showRiskReview&&<div className="wh-overlay" onClick={()=>setShowRiskReview(false)}>
      <div className="wh-modal" role="dialog" aria-modal="true" aria-label="6단계 보유종목 위험 검토" onClick={e=>e.stopPropagation()}>
        <div className="wh-head"><b>6단계 변동성·집중도·스트레스 분석</b><button onClick={()=>setShowRiskReview(false)} aria-label="닫기">✕</button></div>
        <div className="wh-slide">
          <p>검증 상태: <b>{riskReview?.status==='complete'?'보유종목 평가 가능':'일부 시세 미확인'}</b>
            · 과거 원화 기준 전체 변동성은 미산정(보유수량 및 환율 시계열 부족)</p>
          <h4>보유종목의 과거 위험</h4>
          {riskReview?.holdings.length?riskReview.holdings.map((x,i)=><p key={x.account+x.symbol+i}>
            <b>{x.symbol}</b> · {x.account} · 평가 {x.value_krw===null?'미확인':'₩'+fmt(x.value_krw)}
            {x.risk?<span> · 연환산 변동성 {x.risk.annualized_volatility_pct}%
              · 최대낙폭 {x.risk.max_drawdown_pct}% · 과거 하위5% 일수익 {x.risk.historical_5pct_daily_return_pct}%</span>:
              <span> · 65개 이상 검증 종가 부족</span>}
            {x.source&&<a href={x.source.url} target="_blank" rel="noreferrer"> 원본 ↗</a>}
          </p>):<p>아직 보유종목이 없습니다.</p>}
          <h4>동일 통화 기준 위험·집중</h4>
          {riskReview?.currency_proxies.map((x,i)=><p key={i}>{x.currency}
            {x.risk?' · 변동성 '+x.risk.annualized_volatility_pct+'%, 낙폭 '+x.risk.max_drawdown_pct+'%':' · 공통 자료 부족'}
            <small> · 현재 투자 비중 고정, 실제 과거 자산 흐름과 다름</small></p>)}
          {riskReview?.concentration.filter(x=>x.alert).map((x,i)=><p key={'con'+i}>
            <b>{x.symbol}</b> · 투자자산 중 {x.share_of_invested_assets_pct}% 집중 주의</p>)}
          {riskReview?.possible_etf_index_overlap.map((x,i)=><p key={'ov'+i}>
            {x.symbols.join(' / ')} · {x.reason}</p>)}
          <h4>가정 충격 손실 (확률 예측 아님)</h4>
          {riskReview?.stress?<p>개별주 -20%, ETF -12%, 미국상장 상품 원/달러 -7% 동시 가정:
            <b> ₩{fmt(riskReview.stress.estimated_loss_krw)} </b>평가액 변화.
            <small> 배당·금리·급변 거래비용 제외</small></p>:
            <p>보유 종목의 검증 시세·환율 자료 부족으로 가정 손실을 산정하지 않았습니다.</p>}
          <h4>정량 위험 알림·근거</h4>
          {riskReview?.alerts.length?riskReview.alerts.map((x,i)=><p key={i}>
            <b>{x.symbol}</b> · {x.reason}
            {x.source&&<a href={x.source.url} target="_blank" rel="noreferrer"> 출처1 ↗</a>}
            {x.other_source&&<a href={x.other_source.url} target="_blank" rel="noreferrer"> 출처2 ↗</a>}
          </p>):<p>현재 계산 가능한 범위에 추가 위험 알림 없음</p>}
          {riskReview?.valuation_missing.map((x,i)=><p key={'miss'+i}>검증 부족: {x}</p>)}
          <p>뉴스 위험 알림과 별개입니다. 6단계 위험 신호만으로 자동 매도하지 않으며,
            신규매수는 위험과 현금·주문 검사를 모두 통과해야 합니다.</p>
        </div>
      </div>
    </div>}
    {showAccounting&&<div className="wh-overlay" onClick={()=>setShowAccounting(false)}>
      <div className="wh-modal" role="dialog" aria-modal="true" aria-label="5단계 자산별 손익·환율·배당·세금 분석" onClick={e=>e.stopPropagation()}>
        <div className="wh-head"><b>5단계 회계·세후 시나리오</b><button onClick={()=>setShowAccounting(false)} aria-label="닫기">✕</button></div>
        <div className="wh-slide">
          <p>총 가상자산 <b>{accounting?.valuation_complete?'₩'+fmt(accounting.market_valuation_krw):'검증 자료 부족'}</b>
             · 총입금 ₩{fmt(accounting?.contributions_krw)}</p>
          <p>입금 후 총손익(비용·세금·배당 입금 전) <b>{accounting?.valuation_complete?'₩'+fmt(accounting.gross_change_after_contributions_krw):'미확정'}</b></p>
          <p>입금일 반영 연환산 XIRR: {accounting?.xirr.status==='ok'?fmt(accounting.xirr.pct,2)+'%':'미산정 · '+(accounting?.xirr.reason??'검증 대기')}
            · TWR: {accounting?.twr.status==='ok'?fmt(accounting.twr.pct,2)+'%':'미산정 · '+(accounting?.twr.reason??'일별 관측 부족')}</p>
          <h4>종목별 원화/달러 주가손익과 환율효과</h4>
          {accounting?.positions.length?accounting.positions.map((x,i)=><p key={x.account+x.symbol+i}>
            <b>{x.symbol}</b> {x.account} · 미실현 주가손익 {fmt(x.unrealized_native,2)} {x.currency}
            {x.currency==='USD'?<small> · 원화 주가효과 {x.market_price_effect_krw===null?'매수환율 미확인':fmt(x.market_price_effect_krw)}
             · 환율효과 {x.fx_effect_krw===null?'매수환율 미확인':fmt(x.fx_effect_krw)}</small>:null}
             · <a href={x.price_source_url} target="_blank" rel="noreferrer">기준 종가 원본 ↗</a>
          </p>):<p>검증된 보유종목 손익이 없거나 자료가 부족합니다.</p>}
          <h4>배당락일 권리 가능성 (지급 미확인)</h4>
          {accounting?.potential_dividends.length?accounting.potential_dividends.slice(0,8).map((x,i)=><p key={x.symbol+x.ex_date+i}>
            {x.symbol} {x.ex_date} · 추정 {x.potential_gross} {x.currency}
            · <a href={x.source_url} target="_blank" rel="noreferrer">공개 이벤트 ↗</a></p>):
            <p>검증된 배당락일 후보 없음. 실제 지급 0원을 의미하지 않습니다.</p>}
          <h4>거래비용 및 일반계좌·ISA 비교</h4>
          <p>수수료 가정(확정 비용 아님): {accounting?.fee_analysis.illustrative_trade_fees_krw?'₩'+fmt(accounting.fee_analysis.illustrative_trade_fees_krw):'미산정'}</p>
          {accounting?.tax_illustrations.domestic_isa?<p>국내상장 해외지수 ETF만 포함:
             일반계좌 예시세금 ₩{fmt(accounting.tax_illustrations.domestic_isa.general_scenario_tax_krw)}
             · ISA 예시세금 ₩{fmt(accounting.tax_illustrations.domestic_isa.isa_scenario_tax_krw)}
             · 가정상 차이 ₩{fmt(accounting.tax_illustrations.domestic_isa.potential_isa_saving_krw)}</p>:
            <p>비교 가능한 국내상장 해외지수 ETF 평가손익 없음</p>}
          <small>배당·수수료·세금은 실제 가상 현금에 차감/입금하지 않았습니다. ISA 차이는 가정 시나리오이며 실제 세금이 아닙니다.</small>
          {accounting?.missing.map((x,i)=><p key={i}>자료 부족: {x}</p>)}
        </div>
      </div>
    </div>}
    {showPlan&&<div className="wh-overlay" onClick={()=>setShowPlan(false)}>
      <div className="wh-modal" role="dialog" aria-modal="true" aria-label="통합 모의투자 계획과 시장별 목표 비중" onClick={e=>e.stopPropagation()}>
        <div className="wh-head"><b>4단계 통합 자산·투자계획</b><button onClick={()=>setShowPlan(false)} aria-label="닫기">✕</button></div>
        <div className="wh-slide">
          <p><b>자산 평가: {plan?.portfolio_value_krw?'₩'+fmt(plan.portfolio_value_krw):'미확정'}</b>
          · 8전략 분석 가능 {plan?.quant_ready??0}/{plan?.quant_evaluated??0}종</p>
          <p>매수 검토 한도: {plan?.daily_spend_cap_krw?'₩'+fmt(plan.daily_spend_cap_krw):'미확인'}
          · {plan?.trade_enabled?'모의운용 허용':'가상 자동매매 중지'}</p>
          {plan?.data_errors.length?plan.data_errors.map((v,i)=><p key={'err'+i}><b>검증 보류:</b> {v}</p>):null}
          <h4>시장·상품군 목표와 실제 보유</h4>
          {Object.entries(plan?.allocation??{}).map(([group,x])=><p key={group}>
            <b>{group}</b> 목표 {x.target_pct}% · 보유 ₩{fmt(x.held_krw)} · 부족/초과 ₩{fmt(x.drift_krw)}</p>)}
          <h4>검토 중인 가상 주문</h4>
          {plan?.proposed_orders.length?plan.proposed_orders.map(x=><p key={x.request_id}>
            <b>{x.side} {x.symbol} {x.quantity}주</b> · {x.account} · ₩{fmt(x.notional_krw)}
            <small> · 정량 {x.strategy_score??'미평가'}점 / 충족률 {x.coverage_pct}%</small>
            <span className="wh-plan-explain">{x.reason}</span>
          </p>):<p>실행 가능한 계획이 없습니다. 데이터 부족·현금 유보 또는 목표 비중 충족일 수 있습니다.</p>}
          <h4>보유종목 위험 재검토</h4>
          {plan?.review.length?plan.review.slice(0,10).map((x,i)=><p key={i}>{x.symbol} · {x.reason}{x.approval_required?' (별도 승인 필수)':''}</p>):<p>자료 범위에서 추가 매도 재검토 사항 없음</p>}
          <small>모든 주문은 가상 원장에서만 발생하며, 실제 시세 재검증 후 상세 설정 화면에서 실행됩니다.</small>
        </div>
      </div>
    </div>}
    {showReferences&&<div className="wh-overlay" onClick={()=>setShowReferences(false)}>
      <div className="wh-modal" role="dialog" aria-modal="true" aria-label="공개 금융자료 품질 및 수집 상태" onClick={e=>e.stopPropagation()}>
        <div className="wh-head"><b>시장 자료 검증 상태</b><button onClick={()=>setShowReferences(false)} aria-label="닫기">✕</button></div>
        <div className="wh-slide">
          <p>시세·환율·뉴스·재무는 출처 검증 후 별도 SQLite 캐시에 저장하며, 캐시값만으로 자동 모의주문을 체결하지 않습니다.</p>
          <p>종류별 검증된 현재 참조: {Object.entries(referenceHealth?.current_references??{}).map(([key,n])=>key+' '+n+'건').join(' / ')||'아직 없음'}</p>
          {referenceHealth?.recent_attempts.slice(0,20).map((x,i)=><p key={i}>
            <b>{x.kind} · {x.symbol}</b> · {x.status==='ok'?'조회 확인':'조회 보류'}
            {x.error_code?' · '+x.error_code:''}
            <small> · {x.checked_utc.slice(0,16)} UTC · {x.provider}</small>
          </p>)}
          <p>SEC 기업 재무자료 연락처: {referenceHealth?.sec_contact_configured?'설정됨':'미설정(자동 조회 불가)'}</p>
          <p>한국 공식 공시 일부는 인증키 없는 안정적인 조회 범위가 확보되지 않았으므로 미지원 상태입니다.</p>
        </div>
      </div>
    </div>}
    {selectedRisk&&<div className="wh-overlay" onClick={()=>setSelectedRisk(null)}>
      <div className="wh-modal" role="dialog" aria-modal="true" aria-label="보유종목 위험 근거" onClick={e=>e.stopPropagation()}>
        <div className="wh-head"><b>보유종목 뉴스 위험 검토 · {selectedRisk.symbols.join(', ')}</b><button onClick={()=>setSelectedRisk(null)} aria-label="닫기">✕</button></div>
        <div className="wh-slide"><h3>{selectedRisk.impact}</h3><p>{selectedRisk.title}</p>
          <p><b>판단 근거:</b> {selectedRisk.reason}</p>
          <p><b>검증 수준:</b> {selectedRisk.confidence}</p>
          <p><b>뉴스 발행:</b> {selectedRisk.published_utc.slice(0,10)} · {selectedRisk.source}</p>
          <p>이 뉴스만으로 자동 매도하지 않습니다. 실제 기업 손익 및 주가 인과관계는 추가 검증이 필요합니다.</p>
          <p><a href={selectedRisk.url} target="_blank" rel="noreferrer">기사 원문 확인 ↗</a></p>
        </div>
      </div></div>}
    {detail&&<div className="wh-overlay" onClick={()=>setDetail(null)}><div className="wh-modal" role="dialog" aria-modal="true" aria-label="투자 근거 카드뉴스" onClick={e=>e.stopPropagation()}>
       <div className="wh-head"><b>{detail.symbol} · {slides[slide].head}</b><button onClick={()=>setDetail(null)} aria-label="닫기">✕</button></div>
       <div className="wh-slide">{slides[slide].body}</div>
       <div className="wh-slidefooter"><button disabled={slide===0} onClick={()=>setSlide(x=>x-1)}>이전</button><span>{slide+1} / {slides.length}</span><button onClick={()=>slide===slides.length-1?setDetail(null):setSlide(x=>x+1)}>{slide===slides.length-1?'닫기':'다음'}</button></div>
      </div></div>}
  </div>;
}
