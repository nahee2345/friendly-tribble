"""Stage 3: auditable eight-factor research scoring from validated public snapshots.

Pure research, NO order routing or claims of future returns. A score is an
explicit fixed-rule heuristic, not a probability. Missing inputs are never
filled with 0 or neutral 50; weight coverage is reported independently.

Risk policy:
- Prices and quote snapshots in this module are DISPLAY/RESEARCH ONLY.
- Annual SEC facts are compared with the *same* fiscal duration and form.
- Dividends are historical ex-date events, NOT cash payouts.
- Allocation diversification is estimated only against observed same-currency
  owned positions and has limited meaning without FX-adjusted total returns.
- All research data comes from Stage2 SHA-checked local cache (no HTTP here).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import mean, pstdev
from typing import Mapping

from .stage1_strategy_policy import DEFAULT_WEIGHTS,STRATEGIES
from .stage1_market_store import MarketStore

ENGINE_VERSION="3.0-rule-1"
KINDS=("PRICE","HISTORY","FUNDAMENTALS","DIVIDENDS")
STRATEGY_CODES=tuple(DEFAULT_WEIGHTS)
LABELS=dict(STRATEGIES)
# Curated, *static* issuer/identifier classifications. Not proof of current
# holdings, fund expenses, tracking error or issuer solvency.
INDEX_PROXIES={
    "VOO":("미국 대형주 지수 추종 ETF로 분류된 고정 상품정보",82),
    "VTI":("미국 전체시장 지수 추종 ETF로 분류된 고정 상품정보",90),
    "QQQM":("NASDAQ 100 지수 추종 ETF; 성장주 집중",66),
    "VXUS":("미국 외 선진·신흥시장 지수 추종 ETF",87),
    "SCHD":("배당주 지수 기반 ETF; 산업·배당 집중 가능",63),
    "069500":("KOSPI 200 지수 기반 국내 ETF",80),
    "360750":("S&P 500 지수 기반 국내 상장 ETF",81),
}


def _clamp(value: float, low=0.0, high=100.0) -> int:
    if not math.isfinite(value):
        raise ValueError("nonfinite scoring metric")
    return int(round(min(high,max(low,value))))


def _decimal(value):
    try:
        if isinstance(value,bool) or value is None:
            return None
        result=Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError,TypeError,InvalidOperation):
        return None


def _fresh(snapshot,kind,market,today):
    """Accept research snapshots only within an explicit observation window."""
    if not isinstance(snapshot,dict) or snapshot.get("status") not in ("ok","stale"):
        return None
    data=snapshot.get("data")
    if not isinstance(data,dict) or snapshot.get("kind")!=kind:
        return None
    if data.get("symbol",market)!=market:
        return None
    if snapshot.get("use_for_paper_execution") is not False:
        return None
    last=snapshot.get("last_verified_utc",snapshot.get("recorded_utc"))
    try:
        observed=datetime.fromisoformat(last)
        if observed.tzinfo is None: return None
        utc=datetime.combine(today,datetime.max.time(),tzinfo=timezone.utc)
        lag=(utc-observed.astimezone(timezone.utc)).total_seconds()
        if not 0<=lag<=({"PRICE":2,"HISTORY":3,"FUNDAMENTALS":35,
                         "DIVIDENDS":35}[kind]+1)*86400:
            return None
        as_of=date.fromisoformat(data["as_of"])
        age=(today-as_of).days
        if age<0:return None
        if kind in ("PRICE","HISTORY") and age>7:return None
        if kind=="FUNDAMENTALS" and age>480:return None
        # Dividend snapshots represent events from several years and may have
        # the latest payout far in the past; recency of update is separate.
    except (ValueError,TypeError,KeyError,OverflowError):
        return None
    if kind in ("PRICE","HISTORY","DIVIDENDS"):
        expected="KRW" if market.isdigit() else "USD"
        if data.get("currency")!=expected:return None
    if kind=="FUNDAMENTALS" and market.isdigit():
        return None
    return snapshot


def _sources(refs):
    """Trace actual user-visible provenance with content hashes and source dates."""
    result=[]
    seen=set()
    for value in refs:
        if not value:continue
        key=(value.get("kind"),value.get("sha256"))
        if key in seen:continue
        seen.add(key)
        result.append({"kind":value["kind"],"provider":value["provider"],
            "source_url":value["source_url"],"as_of":value["as_of"],
            "sha256":value["sha256"],
            "verified_utc":value.get("last_verified_utc",value["recorded_utc"])})
    return result


def _result(score=None,why=None,risks=None,metrics=None,refs=None,reason=None,quality=None):
    if score is None:
        return {"status":"unavailable","score":None,
                "reason":reason or "검증 가능한 자료 부족",
                "why":[],"risks":[],"metrics":{},"sources":[]}
    return {"status":"scored","score":_clamp(score),
            "why":why or [],"risks":risks or [],"metrics":metrics or {},
            "sources":_sources(refs or []),"quality":quality or "VERIFIED_HISTORICAL_RESEARCH"}


def _price_data(price_ref):
    value=_decimal(price_ref["data"].get("price")) if price_ref else None
    return value if value is not None and value>0 else None


def _history(history_ref, today):
    if history_ref is None:return None
    data=history_ref["data"]
    points=data.get("prices")
    if not isinstance(points,list) or len(points)<2:return None
    result=[]
    previous=None
    for row in points:
        try:day=date.fromisoformat(row["date"])
        except (ValueError,TypeError,KeyError):return None
        value=_decimal(row.get("close"))
        if (value is None or value<=0 or day>today
                or (previous is not None and day<=previous)):
            return None
        result.append((day,float(value)))
        previous=day
    return result


def _returns(history):
    if history is None or len(history)<2:return {}
    return {history[i][0]:history[i][1]/history[i-1][1]-1
            for i in range(1,len(history)) if history[i-1][1]>0}


def _drawdown(prices):
    peak=prices[0]
    result=0.0
    for p in prices:
        peak=max(peak,p)
        result=min(result,p/peak-1)
    return result*100


def _momentum(history,ref):
    if history is None or len(history)<65:
        return _result(reason="검증된 최소 65거래일 가격 이력 없음")
    prices=[p for _,p in history]
    ret63=(prices[-1]/prices[-64]-1)*100
    returns=[prices[i]/prices[i-1]-1 for i in range(len(prices)-62,len(prices))]
    volatility=pstdev(returns)*math.sqrt(252)*100
    ma20=mean(prices[-20:]);ma60=mean(prices[-60:])
    trend=prices[-1]>ma20>ma60
    drawdown=_drawdown(prices[-64:])
    score=_clamp(48+1.4*ret63-0.28*volatility+(-0.32*abs(drawdown))+
                 (12 if trend else -8))
    risks=[]
    if volatility>45:risks.append("최근 63거래일 추정 연환산 변동성이 45%를 초과합니다.")
    if drawdown < -15:risks.append("최근 63거래일 고점 대비 낙폭이 15%를 초과했습니다.")
    return _result(score=score,
        why=[f"최근 63거래일 종가 변화 {ret63:+.2f}%, 연환산 변동성 {volatility:.2f}%",
             "20일 > 60일 이동평균 상승 배열" if trend else "20일/60일 상승 배열 미충족"],
        risks=risks,metrics={"return_63_sessions_pct":round(ret63,3),
            "volatility_annualized_pct":round(volatility,3),
            "drawdown_63_sessions_pct":round(drawdown,3),
            "ma20":round(ma20,4),"ma60":round(ma60,4),
            "trend_up":trend,"samples":len(prices)},
        refs=[ref])


def _index(symbol,kind,history,ref):
    if kind!="ETF" or symbol not in INDEX_PROXIES:
        return _result(reason="현재 검증된 지수형 ETF 분류 대상이 아님")
    if history is None or len(history)<65:
        return _result(reason="지수형 ETF 특성·위험을 비교할 최소 65거래일 이력 없음")
    label,base=INDEX_PROXIES[symbol]
    tail=[p for _,p in history[-64:]]
    drawdown=_drawdown(tail)
    returns=[tail[i]/tail[i-1]-1 for i in range(1,len(tail))]
    vol=pstdev(returns)*math.sqrt(252)*100
    score=_clamp(base-0.22*vol-0.27*abs(drawdown))
    return _result(score=score,
        why=[label,f"최근 63거래일 변동성 {vol:.2f}%, 최대낙폭 {drawdown:.2f}%"],
        risks=["지수 편입종목·ETF 총보수·추적오차의 최신 검증자료는 반영되지 않았습니다."],
        metrics={"volatility_annualized_pct":round(vol,3),
                 "drawdown_63_sessions_pct":round(drawdown,3),
                 "classification_source":"고정 ETF 상품 분류(최신 구성 미확인)"},
        refs=[ref],quality="HISTORY_PLUS_CURATED_ETF_IDENTITY")


def _annual(facts,tag,now,unit="USD"):
    """Only full-year 10-K/10-KA SEC periods (and point-in-time balance items).

    Do not treat 10-Q quarterly profits as yearly earnings; annual periods
    must include a recorded start date and satisfy 280-400 calendar days.
    """
    results={}
    for row in facts:
        if (row.get("tag")!=tag or row.get("unit")!=unit
                or row.get("form") not in ("10-K","10-K/A")):
            continue
        try:
            end=date.fromisoformat(row["period_end"])
            filed=date.fromisoformat(row["filed"])
            start=row.get("period_start")
            if start is not None:
                duration=(end-date.fromisoformat(start)).days
                if not 280<=duration<=400:continue
            value=_decimal(row.get("value"))
            if value is None or not end<=filed<=now or (now-filed).days>1200:
                continue
            previous=results.get(end)
            if previous is None or filed>previous[0]:
                results[end]=(filed,value)
        except (ValueError,TypeError,KeyError,OverflowError):
            continue
    return sorted([(d,v) for d,(_,v) in results.items()],reverse=True)


def _annual_flow(facts,tag,now,unit="USD"):
    rows=_annual(facts,tag,now,unit)
    valid=[]
    for end,value in rows:
        if any(x.get("tag")==tag and x.get("unit")==unit
                and x.get("form") in ("10-K","10-K/A")
                and x.get("period_end")==end.isoformat()
                and isinstance(x.get("period_start"),str)
                and 280<=(end-date.fromisoformat(x["period_start"])).days<=400
                for x in facts):
            valid.append((end,value))
    return valid


def _annual_revenue(facts,now):
    for tag in ("RevenueFromContractWithCustomerExcludingAssessedTax","Revenues"):
        rows=_annual_flow(facts,tag,now)
        if rows:return rows
    return []


def _find_at(rows,end):
    return next((v for day,v in rows if day==end),None)


def _value(stock,price,facts,price_ref,facts_ref,now):
    if stock!="STOCK" or price is None or facts_ref is None:
        return _result(reason="가치평가에 필요한 주가·연간 주당장부가 자료가 없음")
    eq=_annual(facts,"StockholdersEquity",now)
    shares=_annual(facts,"CommonStockSharesOutstanding",now,"shares")
    if not eq or not shares:return _result(reason="같은 회계연도 자기자본·발행주식수 확인 불가")
    latest,book=eq[0]
    shares_out=_find_at(shares,latest)
    if book<=0 or shares_out is None or shares_out<=0:
        return _result(reason="양(+)의 자기자본·동일 기말 발행주식수 부족")
    pb=price*shares_out/book
    if not 0<pb<=Decimal("1000"):
        return _result(reason="비정상 PBR 산출(자료 대조 필요)")
    # Fixed research buckets; never pretend this is intrinsic fair value.
    score=_clamp(90-19*float(pb-Decimal("1")) if pb>1 else 90+7*float(1-pb))
    return _result(score=score,
        why=[f"SEC 동일 회계연도 자기자본/주식수 기반 PBR 약 {float(pb):.2f}배",
             "낮은 PBR은 관찰 신호일 뿐 적정 주가 또는 안전마진의 증거가 아닙니다."],
        risks=["자산 재평가·무형자산·부채 및 미래 이익을 단일 PBR로 대체할 수 없습니다."],
        metrics={"pb_ratio_estimate":round(float(pb),3),
                 "fiscal_end":latest.isoformat(),"valuation_method":"가격×유통발행주식수/기말 자기자본"},
        refs=[price_ref,facts_ref])


def _quality(facts,facts_ref,now):
    if facts_ref is None:return _result(reason="검증된 SEC 연간 이익·자기자본 자료 없음")
    annual_income=_annual_flow(facts,"NetIncomeLoss",now)
    equity=_annual(facts,"StockholdersEquity",now)
    if not annual_income or len(equity)<2:
        return _result(reason="비교 가능한 연간 이익과 2개 연말 자기자본 없음")
    end,income=annual_income[0]
    current=_find_at(equity,end)
    previous=next((v for d,v in equity if 300<=(end-d).days<=430),None)
    if current is None or previous is None or current<=0 or previous<=0:
        return _result(reason="해당 회계연도 평균 자기자본이 양(+)이고 비교 가능해야 함")
    roe=100*income/((current+previous)/Decimal(2))
    revenues=_annual_revenue(facts,now)
    sales=_find_at(revenues,end)
    if sales is None or sales<=0:return _result(reason="동일 연도 매출 실적이 확인되지 않아 수익성 평가 보류")
    margin=100*income/sales
    if not (-300<roe<500) or not (-300<margin<300):
        return _result(reason="비정상 ROE 또는 순이익률, 추가 대조 필요")
    score=_clamp(42+1.3*float(roe)+0.65*float(margin))
    return _result(score=score,
        why=[f"연간 순이익/2개 연말 평균자기자본 ROE {float(roe):.2f}%",
             f"같은 연도 순이익률 {float(margin):.2f}%"],
        risks=["단일 회계연도 지표로 지속가능한 경쟁우위를 검증한 것은 아닙니다."],
        metrics={"roe_pct":round(float(roe),3),
                 "net_margin_pct":round(float(margin),3),
                 "fiscal_end":end.isoformat()},
        refs=[facts_ref])


def _growth(facts,facts_ref,now):
    if facts_ref is None:return _result(reason="SEC 연간 매출 비교 원자료 없음")
    rev=_annual_revenue(facts,now)
    if len(rev)<2:return _result(reason="비교 가능한 SEC 최근 2개 회계연도 매출 자료 없음")
    recent,new=rev[0]
    prior,old=next(((d,v) for d,v in rev[1:] if 300<=(recent-d).days<=430),(None,None))
    if old is None or old<=0 or new<0:
        return _result(reason="2개 연간 동일 항목의 유효한 매출 비교 불가능")
    yoy=100*(new/old-1)
    if not (-100<=yoy<=1000):return _result(reason="매출 성장률 이상치 검토 필요")
    score=_clamp(49+1.55*float(yoy))
    return _result(score=score,
        why=[f"검증된 10-K 연간 매출 전년 대비 {float(yoy):+.2f}%",
             f"비교 연도 {prior.year} → {recent.year}, 동일 SEC 매출 태그"],
        risks=["매출 증가만으로 미래 이익·기업가치 상승이 보장되지 않습니다."],
        metrics={"revenue_yoy_pct":round(float(yoy),3),
                 "from_fiscal_end":prior.isoformat(),
                 "to_fiscal_end":recent.isoformat()},
        refs=[facts_ref])


def _dividends(dividends,ref,price,price_ref,now,*,growing=False):
    if dividends is None or ref is None:
        return _result(reason="과거 배당락일·주당금액 원자료 없음")
    series=[]
    for row in dividends["data"].get("events",[]):
        try:
            day=date.fromisoformat(row["date"]);amount=_decimal(row["amount_per_share"])
            if day>now or amount is None or amount<=0:return _result(reason="잘못된 배당 원자료")
            series.append((day,amount))
        except (ValueError,TypeError,KeyError):
            return _result(reason="잘못된 배당 원자료")
    if not series or series[-1][0]<now-timedelta(days=390):
        return _result(reason="최근 13개월 배당 이벤트 확인되지 않음")
    this=sum((v for day,v in series if now-timedelta(days=365)<day<=now),Decimal(0))
    prev=sum((v for day,v in series if now-timedelta(days=730)<day<=now-timedelta(days=365)),Decimal(0))
    count_now=sum(1 for day,_ in series if now-timedelta(days=365)<day<=now)
    count_prev=sum(1 for day,_ in series if now-timedelta(days=730)<day<=now-timedelta(days=365))
    if growing:
        if not count_now or not count_prev or prev<=0:
            return _result(reason="비교 가능한 2개 연속 365일 배당 이벤트 부족")
        growth=100*(this/prev-1)
        if not (-100<=growth<=500):
            return _result(reason="배당성장률 이상치 검증 필요")
        score=_clamp(50+0.9*float(growth)-abs(count_now-count_prev)*6)
        return _result(score=score,
            why=[f"직전 365일 대비 주당 배당 이벤트 금액 변화 {float(growth):+.2f}%",
                 f"현재 {count_now}회, 이전 {count_prev}회 배당락일 기록"],
            risks=["특별배당·분할·배당 지급일·실제 현금 수령은 검증되지 않았습니다."],
            metrics={"historical_dividend_growth_pct":round(float(growth),3),
                     "events_recent":count_now,"events_prior":count_prev},
            refs=[ref])
    if this<=0 or price is None or price_ref is None:
        return _result(reason="지난 365일 배당합계·검증 종가 불충분")
    payout=100*this/price
    if payout<=0 or payout>100:
        return _result(reason="배당수익률 비정상값, 자료 재검증 필요")
    yield_pct=float(payout)
    score=_clamp(38+yield_pct*6-(max(0,yield_pct-12))*5)
    risks=["배당락일 총액 기반 과거 수익률이며 실제 배당 지급·세후 수익률은 아님"]
    if yield_pct>=10:
        risks.append("높은 과거 배당수익률은 주가 급락이나 배당 삭감 위험을 내포할 수 있습니다.")
    return _result(score=score,
        why=[f"지난 365일 배당락일 주당 합계/최근 지연 종가 {yield_pct:.2f}%",
             f"검증된 배당락일 {count_now}회, 현금 지급 여부 미확인"],
        risks=risks,metrics={"ex_date_dividend_yield_pct":round(yield_pct,3),
                            "events_recent":count_now},
        refs=[ref,price_ref])


def _diversification(symbol,history,ref,positions,all_history,other_refs):
    if history is None or len(history)<65:
        return _result(reason="후보의 최소 65거래일 가격 이력 없음")
    base=_returns(history)
    own=[]
    for other,currency in positions:
        if other==symbol or (other.isdigit()!=symbol.isdigit()):
            continue
        row=all_history.get(other)
        if row and len(row)>=65:
            own.append((other,_returns(row)))
    if not own:return _result(reason="비교 가능한 동일 통화 보유종목 가격 이력이 없음")
    dates=set(base)
    for _,series in own:dates&=set(series)
    times=sorted(dates)[-126:]
    if len(times)<60:
        return _result(reason="보유종목과 동일한 날짜의 수익률 60개 미만")
    candidate=[base[d] for d in times]
    portfolio=[mean(r[d] for _,r in own) for d in times]
    sx=pstdev(candidate);sy=pstdev(portfolio)
    if sx<=1e-12 or sy<=1e-12:
        return _result(reason="변동성이 거의 0이라 상관계수 해석 불가")
    cov=mean((x-mean(candidate))*(y-mean(portfolio))
             for x,y in zip(candidate,portfolio))
    corr=max(-1.0,min(1.0,cov/(sx*sy)))
    score=_clamp(65-35*corr)
    return _result(score=score,
        why=[f"동일 통화 보유종목 {len(own)}종의 동일가중 가격변화와 상관계수 {corr:+.3f}",
             f"공통 수익률 {len(times)}거래일; 낮은 상관은 단순 가격 분산 신호"],
        risks=["실제 보유 금액 비중·환율·배당·ETF 중복 구성종목은 반영하지 않았습니다."],
        metrics={"pearson_correlation":round(corr,4),
                 "common_sessions":len(times),"peer_count":len(own),
                 "portfolio_proxy":"동일통화 보유종목의 동일가중 종가수익률"},
        refs=[ref]+[other_refs.get(x) for x,_ in own])


def _validate_weights(weights):
    if (not isinstance(weights,dict) or set(weights)!=set(DEFAULT_WEIGHTS)
        or any(type(v) is not int or not 0<=v<=100 for v in weights.values())
        or sum(weights.values())!=100):
        raise ValueError("invalid eight-strategy weights")
    return weights


def evaluate(symbol,kind,weights,refs,*,today,portfolio_positions=(),history_map=None,
             history_refs=None):
    """Pure deterministic calculation from already SHA-verified Stage2 records."""
    _validate_weights(weights)
    if not isinstance(symbol,str) or not isinstance(kind,str) or not isinstance(today,date):
        raise ValueError("invalid assessment request")
    history_map=history_map or {}
    history_refs=history_refs or {}
    q=_fresh(refs.get("PRICE"),"PRICE",symbol,today)
    h=_fresh(refs.get("HISTORY"),"HISTORY",symbol,today)
    fundamental=_fresh(refs.get("FUNDAMENTALS"),"FUNDAMENTALS",symbol,today)
    div=_fresh(refs.get("DIVIDENDS"),"DIVIDENDS",symbol,today)
    price=_price_data(q)
    hist=_history(h,today)
    facts=fundamental["data"].get("facts",[]) if fundamental else []
    results={
        "index":_index(symbol,kind,hist,h),
        "value":_value(kind,price,facts,q,fundamental,today),
        "quality_growth":_quality(facts,fundamental,today),
        "growth":_growth(facts,fundamental,today),
        "high_dividend":_dividends(div,div,price,q,today),
        "dividend_growth":_dividends(div,div,price,q,today,growing=True),
        "momentum":_momentum(hist,h),
        "asset_allocation":_diversification(symbol,hist,h,portfolio_positions,
                  history_map,history_refs),
    }
    active=sum(1 for v in weights.values() if v)
    scored=[k for k in STRATEGY_CODES
            if weights[k]>0 and results[k]["status"]=="scored"]
    covered=sum(weights[k] for k in scored)
    partial=(round(sum(weights[k]*results[k]["score"] for k in scored)/covered,2)
             if covered else None)
    # A one-factor 100% user configuration is allowed as a *single-factor*
    # research score, but never misconstrued as multi-factor confidence.
    sufficient=covered>=70 and len(scored)>=min(2,active)
    full_score=partial if sufficient else None
    risk=[]
    for code in scored:
        risk.extend(results[code]["risks"])
    if sufficient and full_score>=70:
        recommendation="정량 관심 후보 (추가 검토)"
    elif sufficient:
        recommendation="정량 관찰"
    else:
        recommendation="자료 부족 · 매수 판단 보류"
    used=[]
    for code in scored:
        used+=results[code]["sources"]
    source_keys={(x["kind"],x["sha256"]):x for x in used}
    return {
        "symbol":symbol,"kind":kind,"engine_version":ENGINE_VERSION,
        "status":"research_ready" if sufficient else "insufficient_data",
        "recommendation":recommendation,
        "score":full_score,"partial_score":partial,
        "coverage_pct":covered,"strategies_scored":scored,
        "strategies":results,"source_evidence":list(source_keys.values()),
        "positive_reasons":[f"{LABELS[k]}: {results[k]['why'][0]}"
                            for k in scored if results[k]["why"]][:8],
        "risk_reasons":list(dict.fromkeys(risk))[:8],
        "unknown_reasons":[f"{LABELS[k]}: {results[k]['reason']}"
                           for k in STRATEGY_CODES
                           if weights[k] and results[k]["status"]=="unavailable"],
        "rules":["고정 임계값 기반 연구 점수이며 수익확률이나 기대수익률이 아닙니다.",
                 "자료가 없는 전략을 0점/50점으로 대체하지 않습니다.",
                 "평가값은 실제·가상 거래 명령에 연결되지 않습니다.",
                 "시세와 ETF 편입·비용·배당 지급일의 최신성은 별도로 확인해야 합니다."],
        "no_orders":True,"paper_order_eligible":False,
    }


class QuantEngine:
    """Evaluate a capped universe with one SHA-validated SQLite market read."""
    def __init__(self,store:MarketStore):
        self.store=store

    def batch(self,items,weights,*,holdings=(),now=None):
        _validate_weights(weights)
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("timezone-aware evaluation time required")
        if not isinstance(items,(list,tuple)) or not 1<=len(items)<=20:
            raise ValueError("bounded candidate list must contain 1 to 20 entries")
        known=[]
        for item in items:
            if (not isinstance(item,dict) or not isinstance(item.get("symbol"),str)
                or item.get("kind") not in ("STOCK","ETF","UNVERIFIED")):
                raise ValueError("invalid candidate item")
            if item["symbol"] not in known:known.append(item["symbol"])
        held=[]
        for row in holdings:
            if (not isinstance(row,dict) or not isinstance(row.get("symbol"),str)):
                raise ValueError("invalid held symbol")
            if row["symbol"] not in held and row["symbol"] not in known:
                held.append(row["symbol"])
        held=held[:20]
        targets=list(dict.fromkeys(known+held))
        if len(targets)>24:
            targets=targets[:24]
        pairs=[(kind,sym) for sym in targets for kind in KINDS]
        observed=self.store.latest_many(pairs,now=now)
        histories={}
        history_refs={}
        for symbol in targets:
            evidence=_fresh(observed.get(("HISTORY",symbol)),"HISTORY",symbol,now.date())
            histories[symbol]=_history(evidence,now.date())
            history_refs[symbol]=evidence
        positions=[(row["symbol"],"KRW" if row["symbol"].isdigit() else "USD")
                   for row in holdings if row["symbol"] in histories]
        results=[]
        for candidate in items:
            symbol=candidate["symbol"]
            refs={kind:observed[(kind,symbol)] for kind in KINDS}
            result=evaluate(symbol,candidate["kind"],weights,refs,
                            today=now.date(),portfolio_positions=positions,
                            history_map=histories,history_refs=history_refs)
            if candidate.get("name"):result["name"]=candidate["name"]
            if candidate.get("region"):result["region"]=candidate["region"]
            if candidate.get("holding") is not None:result["holding"]=candidate["holding"]
            results.append(result)
        results.sort(key=lambda x:(x["score"] is None,
                                   -(x["score"] if x["score"] is not None else -1),
                                   -x["coverage_pct"],x["symbol"]))
        return {"status":"ok","engine_version":ENGINE_VERSION,
                "scoring_basis":"검증된 공개 자료·고정 규칙, 일부 전략은 자료 부족으로 미산정",
                "read_only":True,"no_orders":True,"as_of_utc":now.isoformat(),
                "weights":weights,"items":results,"evaluated":len(results),
                "ready":sum(x["status"]=="research_ready" for x in results)}
