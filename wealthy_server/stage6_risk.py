"""Stage 6: conservative, source-backed paper portfolio risk review.

No HTTP, no brokerage, no trades. All observations come from the read-only
SHA-checked Stage2 market cache. Historical risk is NOT a forecast, and the
fixed-weight proxy is NOT the actual time series of the user's changing book.
USD and KRW asset returns are never summed as an FX-hedged KRW series without
historical USD/KRW data.
"""
from __future__ import annotations

import hashlib
import math
from datetime import date,datetime,timezone
from decimal import Decimal,InvalidOperation
from statistics import mean,stdev

from .stage1_quantitative import _fresh,_history
from .stage1_market_store import MarketStore

VERSION="stage6-risk-v1"
MIN_OBSERVATIONS=65
COMMON_RETURNS=60
LOOKBACK=63
HOLDOUT=30
FEE_ONE_WAY=0.001
ANNUAL_SESSIONS=252
STOCK_SHOCK=-0.20
ETF_SHOCK=-0.12
USD_KRW_SHOCK=-0.07
RISK_VOL_LIMIT=85.0
RISK_DRAWDOWN_LIMIT=-30.0
RISK_CONCENTRATION_WARN=25.0
CORRELATION_WARN=0.90
# Curated index *identity hints*, NOT confirmed constituents/percentage overlap.
INDEX_IDENTITY={"VOO":"US_SP500","360750":"US_SP500",
                "VTI":"US_TOTAL","069500":"KR_KOSPI200",
                "QQQM":"US_NASDAQ100","SCHD":"US_DIVIDEND",
                "VXUS":"NON_US_EQUITY"}


def _number(value):
    if isinstance(value,bool) or value is None:return None
    try:
        n=Decimal(str(value))
        if not n.is_finite() or n<0 or n>Decimal("100000000000000"):
            return None
        return n
    except (TypeError,ValueError,InvalidOperation):
        return None


def _source(ref):
    if not isinstance(ref,dict) or not ref.get("sha256"):return None
    return {"provider":ref["provider"],"url":ref["source_url"],
            "as_of":ref["as_of"],"sha256":ref["sha256"],
            "verified_utc":ref["last_verified_utc"]}


def _series(ref,symbol,today):
    evidence=_fresh(ref,"HISTORY",symbol,today)
    if not evidence:return None
    points=_history(evidence,today)
    if points is None or len(points)<MIN_OBSERVATIONS:return None
    return points


def _daily(points):
    return [(points[i][0],points[i][1]/points[i-1][1]-1)
            for i in range(1,len(points))]


def _drawdown(returns):
    peak=1.0;value=1.0;worst=0.0
    for ret in returns:
        value*=1+ret
        peak=max(peak,value)
        worst=min(worst,value/peak-1)
    return worst*100


def _risk(returns):
    if len(returns)<COMMON_RETURNS:return None
    values=returns[-LOOKBACK:]
    if any(not math.isfinite(x) or x<=-1 or x>10 for x in values):
        return None
    vol=stdev(values)*math.sqrt(ANNUAL_SESSIONS)*100
    if not math.isfinite(vol):return None
    ordered=sorted(values)
    # Lower empirical 5% tail, not Gaussian VaR or estimated future loss.
    tail_count=max(1,math.ceil(0.05*len(ordered)))
    tail=ordered[:tail_count]
    historical_tail=ordered[tail_count-1]*100
    es=mean(tail)*100
    max_dd=_drawdown(values)
    critical=(vol>=RISK_VOL_LIMIT or max_dd<=RISK_DRAWDOWN_LIMIT)
    elevated=(critical or vol>=55 or max_dd<=-20)
    return {"status":"critical" if critical else "elevated" if elevated else "observed",
        "annualized_volatility_pct":round(vol,3),
        "max_drawdown_pct":round(max_dd,3),
        "historical_5pct_daily_return_pct":round(historical_tail,3),
        "historical_5pct_tail_avg_pct":round(es,3),
        "observation_sessions":len(values),
        "risk_window":"최근 63개 거래일 수익률(64종가)",
        "future_return_prediction":False}


def _correlation(a,b):
    dates=sorted(set(a).intersection(b))[-126:]
    if len(dates)<COMMON_RETURNS:return None
    va=[a[d] for d in dates];vb=[b[d] for d in dates]
    sa=stdev(va);sb=stdev(vb)
    if sa<1e-12 or sb<1e-12:return None
    ma=mean(va);mb=mean(vb)
    value=sum((x-ma)*(y-mb) for x,y in zip(va,vb))/(len(dates)-1)/sa/sb
    if not math.isfinite(value):return None
    return {"correlation":round(max(-1,min(1,value)),4),
            "common_sessions":len(dates),"same_currency_only":True}


def walk_forward_momentum(points):
    """Pre-registered moving-average rule applied at t-1 to return at t.

    Fixed 20/60-day MA, price > MA20 > MA60 => fully long NEXT session.
    Last 30 returns only; minimum 95 closes; no parameter fitting or
    look-ahead; fee assumed on transitions, not a brokerage fee posting.
    Both strategies start with unit notional at same boundary.
    """
    if points is None or len(points)<95:
        return {"status":"unavailable","reason":"최소 95개 일간 종가 필요",
                "future_prediction":False}
    observed=points[-(max(95,min(len(points),220))):]
    start=len(observed)-HOLDOUT
    values=[p for _,p in observed]
    equity=1.0;benchmark=1.0
    signal_before=0;switches=0;gross=1.0
    equity_series=[];benchmark_series=[];positive=0;active=0
    for idx in range(start,len(values)):
        previous=values[idx-1]
        ma20=mean(values[idx-20:idx])
        ma60=mean(values[idx-60:idx])
        position=int(previous>ma20>ma60)
        movement=values[idx]/previous-1
        if not math.isfinite(movement) or movement<=-1:
            return {"status":"unavailable","reason":"검증 불가능한 일간 변화",
                    "future_prediction":False}
        gross*=1+position*movement
        if position!=signal_before:
            equity*=1-FEE_ONE_WAY
            switches+=1
        equity*=1+position*movement
        benchmark*=1+movement
        if position:
            active+=1
            if movement>0:positive+=1
        equity_series.append(equity)
        benchmark_series.append(benchmark)
        signal_before=position
    strategy_returns=[equity_series[0]-1]+[
        equity_series[i]/equity_series[i-1]-1 for i in range(1,len(equity_series))]
    benchmark_returns=[benchmark_series[0]-1]+[
        benchmark_series[i]/benchmark_series[i-1]-1 for i in range(1,len(benchmark_series))]
    return {"status":"historical_replay",
        "method":"20/60 종가 이동평균 + 1거래일 지연, 최근 30거래일",
        "period_start":observed[start][0].isoformat(),
        "period_end":observed[-1][0].isoformat(),
        "sessions":HOLDOUT,
        "strategy_return_pct":round((equity-1)*100,4),
        "buy_hold_return_pct":round((benchmark-1)*100,4),
        "strategy_max_drawdown_pct":round(_drawdown(strategy_returns),4),
        "buy_hold_max_drawdown_pct":round(_drawdown(benchmark_returns),4),
        "switches":switches,"invested_sessions":active,
        "positive_invested_sessions":positive,
        "friction_each_switch_pct":FEE_ONE_WAY*100,
        "friction_is_assumed":True,
        "no_fit":True,"is_actual_owner_portfolio_backtest":False,
        "future_prediction":False,
        "limitations":["단일 종목 종가 재현이며 당시 사용자 보유수량을 재현하지 않습니다.",
            "재투자 배당·환전·세금·슬리피지·실제 체결 가능성을 반영하지 않습니다.",
            "투자전략을 최근 성과에 맞춰 최적화하지 않았습니다."]}


def _guard(metrics):
    if metrics is None:
        return {"status":"hold","reason":"검증된 최근 65종가가 없어 신규 자동매수 보류"}
    if metrics["status"]=="critical":
        return {"status":"hold","reason":"과거 63거래일 변동성 85% 또는 낙폭 -30% 초과: 신규매수 보류"}
    return {"status":"permitted_for_stage4_checks",
            "reason":"시장위험 1차 검사 통과(현금·승인·가격 검증은 별도)"}


def review_candidate(ref,symbol,now):
    """Used by Stage4: never authorizes BUY, can only veto it."""
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise ValueError("timezone required")
    series=_series(ref,symbol,now.astimezone(timezone.utc).date())
    metrics=_risk([r for _,r in _daily(series)]) if series else None
    guard=_guard(metrics)
    return {"symbol":symbol,"status":"ok" if metrics else "unavailable",
            "risk":metrics,"buy_guard":guard,
            "source":_source(ref) if series else None,"read_only":True,
            "no_orders":True,"risk_model":VERSION}


class RiskEngine:
    def __init__(self,portfolios,market:MarketStore):
        self.portfolios=portfolios
        self.market=market

    def report(self,owner,*,now=None,candidate=None):
        now=now or datetime.now(timezone.utc)
        if not isinstance(now,datetime) or now.tzinfo is None:
            raise ValueError("timezone-aware risk evaluation required")
        now=now.astimezone(timezone.utc)
        date_today=now.date()
        accounts=self.portfolios.list(owner)["accounts"]
        holds=[{"account":account["id"],"symbol":row["symbol"],
                "quantity":row["quantity"]}
               for account in accounts for row in account["holdings"]
               if row["quantity"]>0]
        if len(holds)>20:raise ValueError("risk report requires no more than 20 holdings")
        held_symbols=list(dict.fromkeys(x["symbol"] for x in holds))
        if candidate is not None and (not isinstance(candidate,str) or len(candidate)>16
            or not (candidate.isdigit() and len(candidate)==6 or
                    candidate.isascii() and candidate.isupper() and
                    candidate[0].isalpha() and all(c.isalnum() or c in ".-" for c in candidate))):
            raise ValueError("invalid candidate symbol")
        symbols=list(dict.fromkeys(held_symbols+([candidate] if candidate else [])))
        if len(symbols)>21:raise ValueError("risk symbol budget exceeded")
        pairs=[("FX","USD/KRW")]+[(kind,sym) for sym in symbols
                                  for kind in ("PRICE","HISTORY")]
        observed=self.market.latest_many(pairs,now=now)
        history={}
        metrics={}
        refs={}
        backtests={}
        for symbol in symbols:
            ref=observed[("HISTORY",symbol)]
            pts=_series(ref,symbol,date_today)
            history[symbol]=pts
            refs[symbol]=_source(ref) if pts else None
            risk=_risk([v for _,v in _daily(pts)]) if pts else None
            metrics[symbol]=risk
            backtests[symbol]=walk_forward_momentum(pts)
        fx_ref=observed[("FX","USD/KRW")]
        fx=None
        if fx_ref.get("status") in ("ok","stale"):
            try:
                validated=datetime.fromisoformat(fx_ref["last_verified_utc"])
                age=(now-validated).total_seconds()
                as_of=date.fromisoformat(fx_ref["as_of"])
                value=_number(fx_ref["data"]["rate"])
                if (0<=age<=48*3600 and 0<=(date_today-as_of).days<=7
                    and value is not None and 100<=value<=10000):
                    fx=value
            except (KeyError,ValueError,TypeError):pass
        held=[]
        unknown=[]
        native_exposure={"USD":{},"KRW":{}}
        sum_krw=Decimal(0);total_stock=Decimal(0);total_etf=Decimal(0)
        for position in holds:
            symbol=position["symbol"]
            price_ref=observed[("PRICE",symbol)]
            money=None
            if price_ref.get("status") in ("ok","stale"):
                try:
                    verify=datetime.fromisoformat(price_ref["last_verified_utc"])
                    day=date.fromisoformat(price_ref["as_of"])
                    price=_number(price_ref["data"]["price"])
                    if (price_ref.get("tradeable") is False
                        and price_ref.get("use_for_paper_execution") is False
                        and price_ref["data"]["currency"]==
                        ("KRW" if symbol.isdigit() else "USD")
                        and 0<=(now-verify).total_seconds()<=48*3600
                        and 0<=(date_today-day).days<=7
                        and price is not None and price>0):
                        money=price*position["quantity"]
                except (KeyError,ValueError,TypeError):pass
            ccy="KRW" if symbol.isdigit() else "USD"
            valued_krw=money*(fx if ccy=="USD" else Decimal(1)) if money is not None and (fx is not None or ccy=="KRW") else None
            if valued_krw is None:
                unknown.append(symbol+": 검증된 종가/환율로 원화평가 불가")
            else:
                sum_krw+=valued_krw
                native_exposure[ccy][symbol]=native_exposure[ccy].get(symbol,Decimal(0))+money
            # Classification only uses known curated holdings; unlisted
            # instruments may be reported but cannot be assumed to be ETFs.
            from .stage4_portfolio import CANDIDATES
            kind=CANDIDATES.get(symbol,{}).get("kind","UNVERIFIED")
            if valued_krw is not None:
                if kind=="STOCK":total_stock+=valued_krw
                elif kind=="ETF":total_etf+=valued_krw
            held.append({"account":position["account"],"symbol":symbol,
                "quantity":position["quantity"],"currency":ccy,
                "kind":kind,"value_krw":str(valued_krw) if valued_krw is not None else None,
                "risk":metrics.get(symbol),"buy_guard":_guard(metrics.get(symbol)),
                "source":refs.get(symbol),
                "price_source":_source(price_ref) if valued_krw is not None else None})
        # Statistical correlation is only legitimate with synchronized
        # returns in a common denomination; no hidden FX/time interpolation.
        corr=[]
        for currency,assets in native_exposure.items():
            codes=sorted(assets)
            for i,one in enumerate(codes):
                if history.get(one) is None:continue
                left=dict(_daily(history[one]))
                for two in codes[i+1:]:
                    if history.get(two) is None:continue
                    item=_correlation(left,dict(_daily(history[two])))
                    if item:
                        corr.append({"left":one,"right":two,"currency":currency,
                                     **item,"alert":item["correlation"]>=CORRELATION_WARN})
        # Historic current-weight, daily-rebalanced proxy—not realized NAV.
        currency_proxies=[]
        for ccy,assets in native_exposure.items():
            if not assets:continue
            price_ready=all(history.get(code) is not None for code in assets)
            dates=set()
            histories={}
            if price_ready:
                histories={code:dict(_daily(history[code])) for code in assets}
                dates=set.intersection(*(set(v) for v in histories.values()))
            sessions=sorted(dates)[-126:]
            if not price_ready or len(sessions)<COMMON_RETURNS:
                currency_proxies.append({"currency":ccy,"status":"unavailable",
                    "reason":"공통일 60개 또는 보유종목 65개 종가 부족",
                    "metrics":None})
                continue
            total_native=sum(assets.values(),Decimal(0))
            risk_returns=[sum(float(v/total_native)*histories[code][d]
                         for code,v in assets.items()) for d in sessions]
            current=_risk(risk_returns)
            currency_proxies.append({"currency":ccy,"status":"proxy_only",
                "risk":current,"sessions":len(sessions),
                "method":"현재 보유금액 비중 고정·매일 리밸런싱 가정 종가수익률",
                "not_realized_nav":True,"currency_fx_history_included":False})
        concentration=[]
        if sum_krw>0 and not unknown:
            bysymbol={}
            for x in held:
                if x["value_krw"] is not None:
                    bysymbol[x["symbol"]]=bysymbol.get(x["symbol"],Decimal(0))+Decimal(x["value_krw"])
            for symbol,value in sorted(bysymbol.items(),key=lambda pair:-pair[1]):
                portion=float(value/sum_krw*100)
                concentration.append({"symbol":symbol,
                    "share_of_invested_assets_pct":round(portion,3),
                    "alert":portion>RISK_CONCENTRATION_WARN})
        overlap=[]
        grouped={}
        for symbol in held_symbols:
            identity=INDEX_IDENTITY.get(symbol)
            if identity:grouped.setdefault(identity,[]).append(symbol)
        for identity,members in grouped.items():
            if len(members)>=2:
                overlap.append({"symbols":sorted(members),"index_identity":identity,
                    "status":"possible_overlap_not_verified",
                    "reason":"고정된 기초지수 명칭이 같습니다. 최신 실제 편입종목/비중은 확보되지 않았습니다."})
        # Intrinsic financial stress is a *hypothetical* shift to current
        # reference marks. Historical portfolio holding weights are unknown.
        stress=None
        if not unknown and held:
            stress_krw=Decimal(0)
            for x in held:
                amount=Decimal(x["value_krw"])
                shock=Decimal(str(ETF_SHOCK if x["kind"]=="ETF" else STOCK_SHOCK))
                if x["currency"]=="USD":
                    # (1+asset shock)*(1+FX shock)-1; not additive.
                    shock=(1+shock)*(1+Decimal(str(USD_KRW_SHOCK)))-1
                stress_krw+=amount*shock
            stress={"status":"hypothetical_only",
                "shock_equity_pct":STOCK_SHOCK*100,
                "shock_etf_pct":ETF_SHOCK*100,
                "shock_usd_krw_pct":USD_KRW_SHOCK*100,
                "estimated_loss_krw":str(stress_krw.quantize(Decimal("0.01"))),
                "exposure_krw":str(sum_krw.quantize(Decimal("0.01"))),
                "includes_cash":False,"no_probability_assigned":True}
        risks=[]
        for item in held:
            rating=item["risk"]
            if rating and rating["status"] in ("elevated","critical"):
                risks.append({"symbol":item["symbol"],"level":rating["status"],
                    "reason":f"63거래일 변동성 {rating['annualized_volatility_pct']}%, 최대낙폭 {rating['max_drawdown_pct']}%",
                    "source":item["source"],"no_automatic_sell":True})
        for item in corr:
            if item["alert"]:
                risks.append({"symbol":item["left"]+" / "+item["right"],
                    "level":"correlation",
                    "reason":f"동일 통화 가격수익률 상관 {item['correlation']}; 포트폴리오 분산 효과 재검토",
                    "source":refs.get(item["left"]),
                    "other_source":refs.get(item["right"]),
                    "no_automatic_sell":True})
        return {"engine_version":VERSION,"status":"complete" if not unknown else "incomplete",
            "as_of_utc":now.isoformat(),"holdings":held,
            "invested_exposure_krw":str(sum_krw) if not unknown else None,
            "stock_exposure_krw":str(total_stock) if not unknown else None,
            "etf_exposure_krw":str(total_etf) if not unknown else None,
            "valuation_missing":unknown,"currency_proxies":currency_proxies,
            "combined_krw_historical_risk":{"status":"unavailable",
                "reason":"시간별 실제 보유수량·원/달러 과거 환율·배당 재투자 자료 부족"},
            "pairwise_correlation":corr,
            "concentration":concentration,
            "possible_etf_index_overlap":overlap,"stress":stress,
            "alerts":risks[:20],
            "candidate":({"symbol":candidate,"risk":metrics[candidate],
                "guard":_guard(metrics[candidate]),"source":refs[candidate],
                "momentum_replay":backtests[candidate]} if candidate else None),
            "validation_of_eight_strategies":{
                "momentum":"단일종목 최근 30일 후행 신호 재현 가능(미래 예측 아님)",
                "index":"과거 가격 위험 분석만 가능; 총보수·배당 포함 수익률 검증 불가",
                "value":"과거 당시 PBR/자기자본의 시점별 원자료 부족",
                "quality_growth":"당시 실적·공시 정정 시점 복원 불가",
                "growth":"과거 연도별 시점 기준 매출 원자료 부족",
                "high_dividend":"세후 배당지급·배당락 수정 이력 부족",
                "dividend_growth":"과거 편입시점·분할/지급 금액 불충분",
                "asset_allocation":"시점별 실제 구성비와 FX 총수익률 없음"},
            "read_only":True,"no_orders":True,
            "disclaimers":["위험 지표는 과거 종가에 근거하며 미래 손실 범위를 보장하지 않습니다.",
                "기간 중 실제 보유이력을 재현하지 않고 현재 비중을 고정한 연구용 위험 분석입니다.",
                "국내상장 해외 ETF의 환율 노출·실편입은 종목별 검증자료로 확인되지 않았습니다.",
                "매도 시점은 예측 불가하며 자동손절 정책과 위험알림을 혼동하지 마세요."]}

    def publish_alerts(self,owner,*,now=None):
        """Deduplicate severe historical-risk alerts; no external notifications."""
        from .stage1_inbox import _schema
        report=self.report(owner,now=now)
        checked=report["as_of_utc"]
        stored=0
        with self.portfolios._db() as db:
            db.execute("BEGIN IMMEDIATE")
            _schema(db)
            for item in report["alerts"][:12]:
                src=item.get("source")
                if not src:continue
                companion=item.get("other_source")
                identity=(item["symbol"]+"|"+item["level"]+"|"+src["sha256"]+
                          ("|"+companion["sha256"] if companion else ""))
                key="risk:quant:"+hashlib.sha256(identity.encode()).hexdigest()[:32]
                label="보유종목 정량 위험 검토: "+item["symbol"]
                details=(item["reason"]+" / 근거1 "+src["url"]+
                         " / 기준일 "+src["as_of"]+
                         " / SHA256 "+src["sha256"]+
                         (" / 근거2 "+companion["url"]+
                          " / 기준일 "+companion["as_of"]+
                          " / SHA256 "+companion["sha256"] if companion else "")+
                         " / 단순 과거 위험 신호이며 매도 확정 아님")[:1800]
                db.execute("""INSERT OR IGNORE INTO stage1_inbox
                    (owner,event_id,title,detail,created_at,read_at)
                    VALUES(?,?,?,?,?,NULL)""",
                    (owner,key,label,details,checked))
                stored+=db.execute("SELECT changes()").fetchone()[0]
            db.execute("COMMIT")
        return {"status":"ok" if report["status"]=="complete" else "partial",
                "new_alerts":stored,"checked":len(report["alerts"]),
                "data_errors":report["valuation_missing"],"read_only":True}
