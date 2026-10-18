"""Stage 4 unified *paper-only* investment plan and controlled execution.

An owner sees one whole-portfolio plan instead of four independent sleeve
autobuy loops. MarketStore snapshots are research observations, NEVER quotes
for execution. Every attempted fill retrieves a new provider quote and FX,
checks a tight deviation bound and calls the existing atomic paper ledger.
Claims are persisted before fills, fail closed after crashes, and cannot be
silently replayed. No broker client or API credential is used here.

Order generation is intentionally conservative. The next stages add audited
tax, commission, optimized rebalancing, backtests and reconciliation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from zoneinfo import ZoneInfo

from .stage1_controls import status as pause_status
from .stage1_decisions import CATALOG,reserve_ratio
from .stage1_quantitative import QuantEngine
from .stage1_strategy_policy import StrategyPolicy
from .stage1_quotes import verified_quote
from .stage1_research import current_fx
from .stage1_portfolios import ISA_ETF_ALLOWED
from .stage1_market_store import MarketStore

KST=ZoneInfo("Asia/Seoul")
MODEL="stage4-v1"
MAX_ORDER_KRW=Decimal("250000")
DAY_LIMIT_PCT=Decimal("0.05")
SINGLE_STOCK_CAP=Decimal("0.10")
SINGLE_ETF_CAP=Decimal("0.20")
LOSS_TRIGGER=Decimal("-0.07")
GAIN_TRIGGER=Decimal("0.15")
DRIFT_TRIGGER=Decimal("0.05")
DEVIATION=Decimal("0.03")
CANDIDATES={
    sym:{"symbol":sym,"name":name,"market":market,"kind":kind}
    for sym,name,market,kind,*_ in CATALOG
}
# Domestic common shares were already supported by the legacy paper sleeves.
CANDIDATES.update({
    "005930":{"symbol":"005930","name":"삼성전자","market":"KR","kind":"STOCK"},
    "000660":{"symbol":"000660","name":"SK하이닉스","market":"KR","kind":"STOCK"},
})


def _decimal(value):
    if isinstance(value,bool) or value is None:raise ValueError("invalid monetary value")
    try:
        n=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError):
        raise ValueError("invalid monetary value") from None
    if not n.is_finite() or abs(n)>Decimal("1000000000000"):
        raise ValueError("unbounded monetary value")
    return n


def _signed_day(value):
    if not isinstance(value,datetime) or value.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(KST).date()


def _reference(observation,kind,symbol,now,*,max_verified_hours=48,max_close_days=6):
    if (not isinstance(observation,dict)
        or observation.get("kind")!=kind or observation.get("symbol")!=symbol
        or observation.get("status") not in ("ok","stale")
        or observation.get("tradeable") is not False
        or observation.get("use_for_paper_execution") is not False
        or not isinstance(observation.get("data"),dict)
        or not observation.get("sha256") or not observation.get("source_url")):
        return None
    try:
        verified=datetime.fromisoformat(observation["last_verified_utc"]).astimezone(timezone.utc)
        age=(now.astimezone(timezone.utc)-verified).total_seconds()
        base=date.fromisoformat(observation["as_of"])
        days=(now.astimezone(KST).date()-base).days
        if age<0 or age>max_verified_hours*3600 or days<0 or days>max_close_days:
            return None
    except (ValueError,KeyError,TypeError,OverflowError):
        return None
    return observation


def _price(observation,symbol,now):
    ref=_reference(observation,"PRICE",symbol,now)
    if ref is None:return None
    data=ref["data"]
    if data.get("currency")!=("KRW" if symbol.isdigit() else "USD"):
        return None
    try:
        price=_decimal(data["price"])
        if price<=0:return None
    except (ValueError,KeyError):
        return None
    return price


def _fx(observation,now):
    ref=_reference(observation,"FX","USD/KRW",now,
                   max_verified_hours=48,max_close_days=6)
    if ref is None:return None
    try:
        rate=_decimal(ref["data"]["rate"])
        if not Decimal("100")<=rate<=Decimal("10000"):return None
        return rate
    except (ValueError,KeyError):return None


def _canonical_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
                                    separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _cost_basis(orders,symbol,expected):
    units=0
    cost=Decimal(0)
    for order in reversed(orders):
        if order["symbol"]!=symbol:continue
        qty=int(order["quantity"])
        p=_decimal(order["price"])
        if qty<=0 or p<=0:return None
        if order["side"]=="BUY":
            units+=qty;cost+=p*qty
        elif order["side"]=="SELL":
            if qty>units:return None
            cost-=cost*qty/units;units-=qty
        else:return None
    if units!=expected or units<=0 or cost<=0:return None
    return cost/units


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage4_execution_claims(
        owner TEXT NOT NULL,request_id TEXT NOT NULL,
        plan_hash TEXT NOT NULL,account TEXT NOT NULL,symbol TEXT NOT NULL,
        side TEXT NOT NULL,quantity INTEGER NOT NULL,notional_krw TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('CLAIMED','EXECUTED','REJECTED')),
        reason TEXT NOT NULL,created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL,
        PRIMARY KEY(owner,request_id))""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_stage4_claim_date
                  ON stage4_execution_claims(owner,created_utc,status)""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_stage4_order_day
                  ON virtual_orders(owner,created_at,side)""")


def recorded_claims(owner,portfolios,*,limit=30):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError("invalid claim limit")
    with portfolios._db() as db:
        _schema(db)
        rows=db.execute("""SELECT request_id,plan_hash,account,symbol,side,
            quantity,notional_krw,status,reason,created_utc,updated_utc
            FROM stage4_execution_claims WHERE owner=?
            ORDER BY created_utc DESC LIMIT ?""",(owner,limit)).fetchall()
        ledger=db.execute("""SELECT portfolio,request_id,symbol,side,quantity
            FROM virtual_orders WHERE owner=? AND request_id LIKE 's4:%'""",
            (owner,)).fetchall()
    keys=("request_id","plan_hash","account","symbol","side","quantity",
          "notional_krw","status","reason","created_utc","updated_utc")
    executions={(account,request): (symbol,side,qty)
                for account,request,symbol,side,qty in ledger}
    items=[]
    for row in rows:
        item=dict(zip(keys,row))
        actual=executions.get((item["account"],item["request_id"]))
        item["ledger_order_verified"]=(actual==
            (item["symbol"],item["side"],item["quantity"]))
        item["needs_manual_reconciliation"]=(
            item["status"]=="CLAIMED" or
            (item["status"]=="EXECUTED" and not item["ledger_order_verified"]) or
            (item["status"]=="REJECTED" and item["ledger_order_verified"]))
        items.append(item)
    return {"items":items,
            "notice":"미대조·상태 불일치는 자동 재실행하지 않고 주문 원장과 수동 대조합니다."}


class PortfolioEngine:
    def __init__(self,portfolios,preferences,market_store,*,policy=None,
                 quote_provider=verified_quote,fx_provider=current_fx):
        self.portfolios=portfolios
        self.preferences=preferences
        self.market_store=market_store
        self.policy=policy or StrategyPolicy(portfolios)
        self.quote_provider=quote_provider
        self.fx_provider=fx_provider

    def preview(self,owner,*,now=None):
        now=now or datetime.now(timezone.utc)
        day=_signed_day(now)
        prefs=self.preferences.get(owner)
        weights=self.policy.get(owner)["weights"]
        accounts=self.portfolios.list(owner)["accounts"]
        account_map={a["id"]:a for a in accounts}
        held=[{"account":a["id"],"symbol":h["symbol"],"quantity":h["quantity"]}
              for a in accounts for h in a["holdings"] if h["quantity"]>0]
        unknown=sorted(set(h["symbol"] for h in held)-set(CANDIDATES))
        if len(held)>20:
            return self._blocked(now,prefs,weights,"보유 종목 20건 초과: 전수 검증 필요")
        symbols=list(dict.fromkeys(list(CANDIDATES)+[x["symbol"] for x in held]))
        if len(symbols)>24:
            return self._blocked(now,prefs,weights,"종목 수집 상한 초과")
        pairs=[("FX","USD/KRW")]+[("PRICE",s) for s in symbols]+[
            ("HISTORY",s) for s in symbols]
        observations=self.market_store.latest_many(pairs,now=now)
        rate=_fx(observations[("FX","USD/KRW")],now)
        missing=[]
        prices={s:_price(observations[("PRICE",s)],s,now) for s in symbols}
        needs_usd=any(_decimal(a["usd_cash"])!=0 for a in accounts)
        needs_usd|=any(not h["symbol"].isdigit() for h in held)
        if (needs_usd or prefs["us_weight"]>0) and rate is None:
            missing.append("USD/KRW: 최신 확인된 환율 없음")
        cash_krw=Decimal(0)
        for account in accounts:
            cash_krw+=_decimal(account["krw_cash"])
            if rate is not None:
                cash_krw+=_decimal(account["usd_cash"])*rate
        # A market quote and an independent historical latest close should
        # not materially disagree. Research scores remain visible, but BUY
        # is held for data reconciliation if two sources diverge.
        coherence={}
        for symbol in symbols:
            hist=observations.get(("HISTORY",symbol),{})
            last=(hist.get("data",{}).get("prices") or [{}])[-1]
            historic=_decimal(last["close"]) if isinstance(last,dict) and last.get("close") is not None else None
            spot=prices.get(symbol)
            coherence[symbol]=(historic is None or spot is None or historic<=0
                or abs(spot/historic-1)<=Decimal("0.10"))
        values={}
        sleeve_held={(market,kind):Decimal(0)
                     for market in ("US","KR") for kind in ("ETF","STOCK")}
        for holding in held:
            symbol=holding["symbol"]
            entry=CANDIDATES.get(symbol)
            if entry is None:
                missing.append(symbol+": 검증된 상품분류가 없음")
                continue
            price=prices.get(symbol)
            if price is None:
                missing.append(symbol+": 현재 검증 종가 없음")
                continue
            if entry["market"]=="US" and rate is None:
                missing.append(symbol+": 환율 없음")
                continue
            if ((entry["market"]=="US") != (holding["account"]=="default")):
                missing.append(symbol+": 보유계좌/시장 분류 불일치")
                continue
            krw=price*_decimal(holding["quantity"])*(rate if entry["market"]=="US" else Decimal(1))
            values[(holding["account"],symbol)]=krw
            sleeve_held[(entry["market"],entry["kind"])]+=krw
        total=cash_krw+sum(values.values(),Decimal(0))
        if total<=0:missing.append("평가자산 0 이하")
        if unknown:missing.append("미분류 보유종목 존재: "+", ".join(unknown))
        complete=not missing
        targets={}
        gaps={}
        for m in ("US","KR"):
            for kind in ("ETF","STOCK"):
                key=(m,kind)
                pct=Decimal(prefs["us_weight"] if m=="US" else prefs["kr_weight"])*(
                    Decimal(prefs["etf_weight"] if kind=="ETF" else prefs["stock_weight"]))/Decimal(10000)
                targets[key]=total*pct
                gaps[key]=targets[key]-sleeve_held[key]
        holdings_quant=[{"symbol":h["symbol"],"quantity":h["quantity"]} for h in held]
        candidates=[entry.copy() for entry in CANDIDATES.values()]
        try:
            assessed=QuantEngine(self.market_store).batch(candidates,weights,
                         holdings=holdings_quant,now=now)["items"]
        except (ValueError,KeyError,TypeError):
            assessed=[]
            missing.append("전략 정량 평가 불가능")
            complete=False
        assessed_by={a["symbol"]:a for a in assessed}
        orders=[]
        watches=[]
        reserve=Decimal(reserve_ratio(weights))/100
        daily_cap=min(total*DAY_LIMIT_PCT,Decimal("500000")) if complete else Decimal(0)
        remaining=max(Decimal(0),daily_cap)
        pending_by_account={}
        target_exposure={f"{m}_{kind}":{
            "target_pct":str((targets[(m,kind)]/total*100).quantize(Decimal(".01"))) if total>0 else "0",
            "target_krw":str(targets[(m,kind)].quantize(Decimal(".01"))),
            "held_krw":str(sleeve_held[(m,kind)].quantize(Decimal(".01"))),
            "drift_krw":str(gaps[(m,kind)].quantize(Decimal(".01")))}
            for m in ("US","KR") for kind in ("ETF","STOCK")}
        # Stage6 historical risk is a BUY veto/watch only. It never silently
        # overrides the existing exit/approval policy or creates trades.
        from .stage6_risk import review_candidate
        risk_cache={}
        for symbol in symbols:
            risk_cache[symbol]=review_candidate(
                observations.get(("HISTORY",symbol)),symbol,now)
        for holding in held:
            signal=risk_cache.get(holding["symbol"])
            if signal and signal.get("risk") and signal["risk"]["status"] in ("elevated","critical"):
                risk=signal["risk"]
                watches.append({"account":holding["account"],"symbol":holding["symbol"],
                    "kind":"QUANT_RISK","reason":
                    f"과거 63거래일 연환산 변동성 {risk['annualized_volatility_pct']}%, "+
                    f"최대낙폭 {risk['max_drawdown_pct']}%: 추가 위험 점검(이 신호만으로 자동매도하지 않음)",
                    "source":signal["source"],"sell_automatically":False})
        # Existing holdings get explicit exit and overweight review, based on
        # average acquisition cost and correctly normalized portfolio exposure.
        for a in accounts:
            old_orders=self.portfolios.orders(owner,a["id"],limit=100000)["orders"] if a["holdings"] else []
            for h in a["holdings"]:
                symbol=h["symbol"];qty=int(h["quantity"])
                if qty<=0 or symbol not in CANDIDATES:continue
                price=prices.get(symbol)
                if price is None:continue
                basis=_cost_basis(old_orders,symbol,qty)
                if basis is None:
                    watches.append({"account":a["id"],"symbol":symbol,
                                    "kind":"COST_UNKNOWN","reason":"매입원가 원장 대조 필요"})
                    continue
                diff=price/basis-1
                exposure=values.get((a["id"],symbol),Decimal(0))
                cap_pct=SINGLE_ETF_CAP if CANDIDATES[symbol]["kind"]=="ETF" else SINGLE_STOCK_CAP
                concentration=bool(total>0 and exposure>total*cap_pct)
                overweight=gaps[(CANDIDATES[symbol]["market"],CANDIDATES[symbol]["kind"])] < -total*DRIFT_TRIGGER if total>0 else False
                reasons=[]
                if diff<=LOSS_TRIGGER:reasons.append("기준 종가 대비 매입원가 -7% 손실 검토")
                if diff>=GAIN_TRIGGER:reasons.append("기준 종가 대비 매입원가 +15% 이익 검토")
                if concentration:reasons.append("동일 종목 포트폴리오 집중 한도 초과")
                if overweight:reasons.append("미국/한국·ETF/개별주 슬리브가 목표보다 5%p 이상 초과")
                if not reasons:continue
                watches.append({"account":a["id"],"symbol":symbol,"kind":"EXIT_REVIEW",
                    "return_pct":round(float(diff*100),3),
                    "concentration":concentration,
                    "reason":"; ".join(reasons)})
                # Stage4 will not liquidate simply because a fund is overweight:
                # compare approved stop/target triggers; otherwise WATCH only.
                if not complete or LOSS_TRIGGER<diff<GAIN_TRIGGER:
                    continue
                notional=price*qty*(rate if not symbol.isdigit() and rate else Decimal(1))
                if notional>=Decimal("300000"):
                    watches[-1]["approval_required"]=True
                    watches[-1]["reason"]+="; 30만원 이상 매도는 별도 승인 필요"
                    continue
                key=f"s4:{day:%Y%m%d}:{a['id']}:{symbol}:exit"
                orders.append({"request_id":key,"side":"SELL","account":a["id"],
                    "symbol":symbol,"quantity":qty,"kind":CANDIDATES[symbol]["kind"],
                    "market":CANDIDATES[symbol]["market"],
                    "planned_price":str(price),"notional_krw":str(notional),
                    "reason":"; ".join(reasons),
                    "source_hash":observations[("PRICE",symbol)]["sha256"],
                    "coverage_pct":assessed_by.get(symbol,{}).get("coverage_pct",0),
                    "strategy_score":assessed_by.get(symbol,{}).get("score")})
        # A risk-triggered exit suppresses all same-day BUY orders in that
        # account (no immediate re-entry into a different fund or stock).
        exit_accounts={order["account"] for order in orders if order["side"]=="SELL"}
        exit_sleeves={(order["market"],order["kind"]) for order in orders
                      if order["side"]=="SELL"}
        # One opportunity per target sleeve. Account cash is not silently
        # transferred; if account funding is insufficient, report a blocker.
        for market,kind in (("US","ETF"),("US","STOCK"),("KR","ETF"),("KR","STOCK")):
            group=(market,kind)
            if group in exit_sleeves:continue
            deficit=gaps[group]
            if not complete or deficit<=0 or remaining<=0:continue
            acceptable=[r for r in assessed if
                CANDIDATES.get(r["symbol"],{}).get("market")==market and
                CANDIDATES.get(r["symbol"],{}).get("kind")==kind and
                r["status"]=="research_ready" and r["score"] is not None and r["score"]>=70]
            acceptable.sort(key=lambda r:(-r["score"],-r["coverage_pct"],r["symbol"]))
            for result in acceptable:
                symbol=result["symbol"]
                price=prices.get(symbol)
                if price is None or not coherence.get(symbol,False):continue
                risk_signal=risk_cache.get(symbol)
                if not risk_signal or risk_signal["buy_guard"]["status"]!="permitted_for_stage4_checks":
                    continue
                if market=="US" and rate is None:continue
                converted=price*(rate if market=="US" else Decimal(1))
                if converted<=0:continue
                cap=(SINGLE_ETF_CAP if kind=="ETF" else SINGLE_STOCK_CAP)*total
                already=sum(value for (_,sym),value in values.items() if sym==symbol)
                headroom=cap-already
                if headroom<converted:continue
                candidates_accounts=["default"] if market=="US" else (
                    ["isa-base","kr-base"] if kind=="ETF" and symbol in ISA_ETF_ALLOWED
                    else ["kr-base"])
                for account_id in candidates_accounts:
                    if account_id in exit_accounts:continue
                    account=account_map.get(account_id)
                    if not account:continue
                    available=_decimal(account["krw_cash"])+(
                        _decimal(account["usd_cash"])*rate if market=="US" and rate else 0)
                    available-=pending_by_account.get(account_id,Decimal(0))
                    # Elevated historical risk halves the usual order
                    # budget, never raises an allocation or risk limit.
                    risk_factor=(Decimal("0.5") if
                        risk_signal["risk"]["status"]=="elevated" else Decimal(1))
                    budget=min(max(Decimal(0),deficit),max(Decimal(0),headroom),
                               max(Decimal(0),available*(1-reserve)),
                               remaining,MAX_ORDER_KRW*risk_factor)
                    quantity=int((budget/converted).to_integral_value(rounding=ROUND_DOWN))
                    if market=="US":quantity=min(quantity,1) # atomic verified-FX buy path
                    quantity=min(quantity,5)
                    if quantity<1:continue
                    cost=converted*quantity
                    key=f"s4:{day:%Y%m%d}:{market}:{kind}"
                    orders.append({"request_id":key,"side":"BUY","account":account_id,
                        "symbol":symbol,"quantity":quantity,"kind":kind,
                        "market":market,"planned_price":str(price),
                        "notional_krw":str(cost),
                        "strategy_score":result["score"],
                        "coverage_pct":result["coverage_pct"],
                        "source_hash":observations[("PRICE",symbol)]["sha256"],
                        "risk_history_hash":risk_signal["source"]["sha256"],
                        "risk_level":risk_signal["risk"]["status"],
                        "risk_volatility_pct":risk_signal["risk"]["annualized_volatility_pct"],
                        "reason":f"목표 대비 {market}/{kind} 부족 {str(deficit.quantize(Decimal('.01')))}원; "
                                 f"8전략 점수 {result['score']}점·검증자료 {result['coverage_pct']}%"})
                    pending_by_account[account_id]=pending_by_account.get(account_id,Decimal(0))+cost
                    remaining-=cost
                    break
                if any(x["request_id"]==f"s4:{day:%Y%m%d}:{market}:{kind}" for x in orders):
                    break
        if not complete:orders=[]
        result={"status":"ready" if complete else "blocked",
                "reason":"계좌·종목·환율 평가 검증 완료" if complete else "; ".join(missing[:8]),
                "engine_version":MODEL,"generated_utc":now.astimezone(timezone.utc).isoformat(),
                "for_date":day.isoformat(),"simulated_only":True,
                "complete_valuation":complete,
                "portfolio_value_krw":str(total) if complete else None,
                "cash_krw_equivalent":str(cash_krw) if complete else None,
                "reserve_percent":str(reserve*100),
                "policy_prefs":prefs,"policy_weights":weights,
                "daily_spend_cap_krw":str(daily_cap) if complete else None,
                "allocation":target_exposure if complete else {},
                "proposed_orders":orders,"review":watches,
                "data_errors":missing,"quant_ready":sum(x["status"]=="research_ready" for x in assessed),
                "quant_evaluated":len(assessed),
                "trade_enabled":(not pause_status(owner,self.portfolios)["paused"]
                                 and day.weekday()<5),
                "rules":["데이터 캐시는 거래 체결 가격이 아닙니다.",
                         "매수 체결 직전 새 공식/공개 지연종가·환율을 재검증합니다.",
                         "30만원 이상 자동 체결 금지, 승인 생략 금지.",
                         "하루 신규매수 한도 평가자산 5%, 종목별 ETF 20%/개별주 10% 이내.",
                         "최근 63거래일 위험자료 부족·극단적 변동/낙폭이면 자동매수 보류.",
                         "상승/하락 추세 재현은 매수 승인이나 미래 수익률 예측이 아님.",
                         "전략 점수는 미래수익 예측 아님. 목표비중과 재고 부족의 보수적 검토 신호.",
                         "과도 비중만으로 자동매도하지 않으며 매도는 기준손익 조건일 때만 검토."]}
        # Hash only deterministic policy/valuation/intent; timestamp changes
        # cannot change the meaning of a proposed order.
        evidence={"version":MODEL,"owner":owner,"date":str(day),"prefs":prefs,
                  "weights":weights,"prices":{s:str(p) if p is not None else None
                                             for s,p in prices.items()},
                  "fx":str(rate) if rate else None,
                  "allocation":result["allocation"],"orders":orders}
        result["plan_hash"]=_canonical_digest(evidence)
        return result

    @staticmethod
    def _blocked(now,prefs,weights,reason):
        return {"status":"blocked","reason":reason,"generated_utc":now.isoformat(),
                "complete_valuation":False,"proposed_orders":[],
                "review":[],"allocation":{},"data_errors":[reason],
                "trade_enabled":False,"simulated_only":True,"plan_hash":None}

    def run(self,owner,*,now=None):
        """Apply bounded orders via independently verified fresh providers.

        Claimed, interrupted requests are not silently repeated. Every
        state-changing quote goes through the original Portfolios atomic
        balance, paused-state and account-instrument checks.
        """
        now=now or datetime.now(timezone.utc)
        if _signed_day(now).weekday()>=5:
            return {"status":"market_closed","reason":"한국시간 주말에는 자동 모의거래를 기록하지 않습니다.",
                    "executed":[],"skipped":[],"simulated_only":True}
        if pause_status(owner,self.portfolios)["paused"]:
            return {"status":"paused","reason":"가상매매 중지 상태",
                    "executed":[],"skipped":[],"simulated_only":True}
        plan=self.preview(owner,now=now)
        if plan["status"]!="ready":
            return {"status":"blocked","reason":plan["reason"],
                    "executed":[],"skipped":[],"simulated_only":True}
        claims=[]
        for choice in plan["proposed_orders"][:8]:
            key=choice["request_id"]
            # Fresh quote does NOT come from MarketStore. Its symbol/currency,
            # as-of, source and deviation are validated anew.
            try:
                if pause_status(owner,self.portfolios)["paused"]:
                    raise ValueError("PAUSED_DURING_EXECUTION")
                quote=self.quote_provider(choice["symbol"])
                expected="KRW" if choice["symbol"].isdigit() else "USD"
                if (not isinstance(quote,dict) or quote.get("status")!="ok"
                    or quote.get("tradeable") is not True
                    or quote.get("symbol")!=choice["symbol"]
                    or quote.get("currency")!=expected
                    or not quote.get("source") or not quote.get("as_of")):
                    raise ValueError("UNVERIFIED_FRESH_QUOTE")
                live=_decimal(quote.get("price"))
                original=_decimal(choice["planned_price"])
                if live<=0 or abs(live/original-1)>DEVIATION:
                    raise ValueError("PRICE_MOVED_MORE_THAN_3_PERCENT")
                live_date=date.fromisoformat(quote["as_of"])
                if (now.astimezone(KST).date()-live_date).days not in range(0,5):
                    raise ValueError("STALE_EXECUTION_QUOTE")
                rate=None
                if expected=="USD":
                    forex=self.fx_provider()
                    if (not isinstance(forex,dict) or forex.get("status")!="ok"
                        or forex.get("executable") is not False
                        or not forex.get("as_of")):
                        raise ValueError("UNVERIFIED_EXECUTION_FX")
                    rate=_decimal(forex["usd_krw"])
                    cached=self.market_store.latest_many([("FX","USD/KRW")],now=now)
                    reference=_fx(cached[("FX","USD/KRW")],now)
                    if (reference is None or rate<100 or rate>10000
                        or abs(rate/reference-1)>DEVIATION):
                        raise ValueError("FX_MOVED_MORE_THAN_3_PERCENT")
                    if (now.astimezone(KST).date()-date.fromisoformat(forex["as_of"])).days not in range(0,6):
                        raise ValueError("STALE_EXECUTION_FX")
                # Stage6 cannot turn its research history into an execution
                # price. Both cached source hashes must remain unchanged
                # between plan and independent execution quote.
                if choice["side"]=="BUY":
                    references=self.market_store.latest_many([
                        ("PRICE",choice["symbol"]),("HISTORY",choice["symbol"])],now=now)
                    fresh_price=references.get(("PRICE",choice["symbol"]),{})
                    fresh_history=references.get(("HISTORY",choice["symbol"]),{})
                    if (fresh_price.get("sha256")!=choice["source_hash"] or
                        fresh_history.get("sha256")!=choice.get("risk_history_hash")):
                        raise ValueError("RISK_OR_PRICE_REFERENCE_CHANGED")
                    if review_candidate(fresh_history,choice["symbol"],now)[
                            "buy_guard"]["status"]!="permitted_for_stage4_checks":
                        raise ValueError("STAGE6_BUY_RISK_REJECTED")
                actual=live*_decimal(choice["quantity"])*(rate if rate else Decimal(1))
                if actual>=Decimal("300000") or actual>MAX_ORDER_KRW:
                    raise ValueError("APPROVAL_REQUIRED_300K_OR_ORDER_LIMIT")
                if choice["side"]=="BUY" and actual>_decimal(choice["notional_krw"])*Decimal("1.03"):
                    raise ValueError("PLANNED_BUDGET_EXCEEDED")
                stamp=datetime.now(timezone.utc).isoformat()
                with self.portfolios._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    _schema(db)
                    before=db.execute("""SELECT status FROM stage4_execution_claims
                        WHERE owner=? AND request_id=?""",(owner,key)).fetchone()
                    if before is not None:
                        db.execute("COMMIT")
                        claims.append({"request_id":key,"symbol":choice["symbol"],
                                       "status":"already_claimed","stored_status":before[0]})
                        continue
                    if choice["side"]=="BUY":
                        krw_cash,usd_cash,_,_=self.portfolios._money(db,owner,choice["account"])
                        available=krw_cash+(usd_cash*rate if expected=="USD" else Decimal(0))
                        ratio=_decimal(plan["reserve_percent"])/100
                        if actual>max(Decimal(0),available*(1-ratio)):
                            db.execute("COMMIT")
                            claims.append({"request_id":key,"symbol":choice["symbol"],
                                           "status":"skipped","reason":"CASH_RESERVE_OR_BALANCE_CHANGED"})
                            continue
                    # A change to preferences or strategy weights after
                    # planning invalidates the authorization for this fill.
                    pref=db.execute("""SELECT us_weight,etf_weight
                        FROM stage1_preferences WHERE owner=?""",(owner,)).fetchone()
                    expected_prefs=plan["policy_prefs"]
                    saved_prefs=pref if pref else (90,80)
                    if saved_prefs!=(expected_prefs["us_weight"],expected_prefs["etf_weight"]):
                        db.execute("COMMIT")
                        claims.append({"request_id":key,"symbol":choice["symbol"],
                                       "status":"skipped","reason":"POLICY_CHANGED_AFTER_PLAN"})
                        continue
                    from .stage1_strategy_policy import DEFAULT_WEIGHTS
                    rows=db.execute("""SELECT strategy,weight FROM stage1_strategy_weights
                        WHERE owner=?""",(owner,)).fetchall()
                    observed_policy=dict(rows) if rows else DEFAULT_WEIGHTS.copy()
                    if observed_policy!=plan["policy_weights"]:
                        db.execute("COMMIT")
                        claims.append({"request_id":key,"symbol":choice["symbol"],
                                       "status":"skipped","reason":"STRATEGY_CHANGED_AFTER_PLAN"})
                        continue
                    # Concurrent manual fills also consume the daily cash
                    # budget. Executed Stage4 claims are counted from fills;
                    # only unfilled claims are counted separately.
                    day_kst=_signed_day(now)
                    start=datetime.combine(day_kst,datetime.min.time(),tzinfo=KST).astimezone(timezone.utc)
                    end=start+timedelta(days=1)
                    completed=db.execute("""SELECT portfolio,request_id,quantity,
                        price,currency FROM virtual_orders
                        WHERE owner=? AND side='BUY' AND created_at>=?
                        AND created_at<?""",
                        (owner,start.isoformat(),end.isoformat())).fetchall()
                    used=Decimal(0)
                    complete_keys=set()
                    for account_id,request,qty,unit_price,currency in completed:
                        converted=rate if currency=="USD" else Decimal(1)
                        if converted is None:
                            raise ValueError("MISSING_FX_FOR_HISTORICAL_DAILY_CAP")
                        used+=_decimal(unit_price)*_decimal(qty)*converted
                        complete_keys.add((account_id,request))
                    prefix=f"s4:{day_kst:%Y%m%d}:"
                    claims_day=db.execute("""SELECT account,request_id,notional_krw
                        FROM stage4_execution_claims
                        WHERE owner=? AND side='BUY' AND
                        status IN ('CLAIMED','EXECUTED') AND
                        request_id LIKE ?""",(owner,prefix+"%")).fetchall()
                    for account_id,request,nominal in claims_day:
                        if (account_id,request) not in complete_keys:
                            used+=_decimal(nominal)
                    allowed=_decimal(plan["daily_spend_cap_krw"])
                    if used+actual>allowed:
                        db.execute("COMMIT")
                        claims.append({"request_id":key,"symbol":choice["symbol"],
                                       "status":"skipped","reason":"DAILY_CAP_EXCEEDED"})
                        continue
                    if choice["side"]=="BUY":
                        holdings_now=db.execute("""SELECT
                            COALESCE(SUM(CASE WHEN side='BUY' THEN quantity
                                ELSE -quantity END),0)
                            FROM virtual_orders WHERE owner=? AND symbol=?""",
                            (owner,choice["symbol"])).fetchone()[0]
                        cap=(SINGLE_ETF_CAP if choice["kind"]=="ETF"
                             else SINGLE_STOCK_CAP)*_decimal(plan["portfolio_value_krw"])
                        if (holdings_now+choice["quantity"])*live*(
                                rate if expected=="USD" else Decimal(1))>cap:
                            db.execute("COMMIT")
                            claims.append({"request_id":key,"symbol":choice["symbol"],
                                           "status":"skipped","reason":"CONCENTRATION_CAP_CHANGED"})
                            continue
                    db.execute("""INSERT INTO stage4_execution_claims VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (owner,key,plan["plan_hash"],choice["account"],
                         choice["symbol"],choice["side"],choice["quantity"],
                         str(actual),"CLAIMED","EXECUTION_PENDING_RECONCILIATION",
                         stamp,stamp))
                    db.execute("COMMIT")
                try:
                    # Only current, validated market data reach the atomic
                    # simulated ledger. US single-share BUY can atomically FX.
                    if choice["side"]=="BUY" and expected=="USD":
                        outcome=self.portfolios.auto_buy_usd_with_fx(
                            owner,choice["symbol"],quote,str(rate),key)
                    else:
                        outcome=self.portfolios.trade(owner,choice["account"],
                            choice["symbol"],choice["side"],choice["quantity"],key,quote)
                    success=True
                    order_state=outcome.get("status","executed") if isinstance(outcome,dict) else "executed"
                except (ValueError,ArithmeticError,KeyError,TypeError) as error:
                    success=False
                    order_state=type(error).__name__
                with self.portfolios._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    _schema(db)
                    recorded=db.execute("""SELECT 1 FROM virtual_orders
                       WHERE owner=? AND portfolio=? AND request_id=?""",
                        (owner,choice["account"],key)).fetchone()
                    # Ledger state wins even if the response was interrupted.
                    status="EXECUTED" if recorded else "REJECTED"
                    db.execute("""UPDATE stage4_execution_claims
                       SET status=?,reason=?,updated_utc=?
                       WHERE owner=? AND request_id=? AND status='CLAIMED'""",
                       (status,"VERIFIED_IN_PAPER_LEDGER" if recorded else
                        "EXECUTION_REJECTED_"+order_state,datetime.now(timezone.utc).isoformat(),
                        owner,key))
                    db.execute("COMMIT")
                claims.append({"request_id":key,"symbol":choice["symbol"],
                               "side":choice["side"],"quantity":choice["quantity"],
                               "account":choice["account"],"status":"executed" if recorded else "skipped",
                               "reason":"VERIFIED_IN_PAPER_LEDGER" if recorded else "EXECUTION_REJECTED"})
            except (ValueError,KeyError,ArithmeticError,TypeError) as error:
                claims.append({"request_id":key,"symbol":choice["symbol"],
                               "status":"skipped","reason":str(error)})
        return {"status":"completed","plan_hash":plan["plan_hash"],
                "executed":[x for x in claims if x["status"]=="executed"],
                "skipped":[x for x in claims if x["status"]!="executed"],
                "review":plan["review"],"simulated_only":True,
                "note":"가상 체결만 생성. 모든 신규 매수는 원장·긴급중지·시장자료 검증 후 처리."}
