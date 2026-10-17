"""Bounded simulated US individual-stock entry engine.

No paid provider or AI. Uses authenticated public delayed closes, fixed
audited watchlist and saved strategy preferences. Does not claim an expected
profit. One individual share per KST day; independent of the ETF sleeve.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo

from .stage1_quotes import verified_quote
from .stage1_research import current_fx
from .stage1_decisions import reserve_ratio
from .stage1_strategy_policy import DEFAULT_WEIGHTS

# Narrow initial universe; no unsupported fundamental claims.
STOCKS=(
    ("MSFT",("quality_growth","growth")),
    ("AAPL",("quality_growth","growth")),
)


def paper_run_us_stocks(owner,ledger,portfolios,preferences,*,today=None,
                        quote_provider=verified_quote,fx_provider=current_fx,
                        strategy_weights=None):
    day=today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    prefs=preferences.get(owner)
    if prefs["us_weight"]==0 or prefs["stock_weight"]==0:
        return {"status":"skipped","traded":False,"reason":"미국 개별주 비중이 0%"}
    weights=strategy_weights or DEFAULT_WEIGHTS
    if set(weights)!=set(DEFAULT_WEIGHTS) or sum(weights.values())!=100 or any(
          type(w) is not int or w<0 or w>100 for w in weights.values()):
        return {"status":"skipped","traded":False,"reason":"전략 비중 오류"}
    accounts={x["id"]:x for x in portfolios.list(owner)["accounts"]}
    cash=accounts.get("default")
    if not cash:
        return {"status":"skipped","traded":False,"reason":"해외 일반계좌 없음"}
    # These symbols are only eligible if their supporting strategy category is nonzero.
    ranked=sorted(STOCKS,key=lambda t:(-sum(weights[k] for k in t[1]),t[0]))
    ranked=[x for x in ranked if sum(weights[k] for k in x[1])>0]
    if not ranked:
        return {"status":"skipped","traded":False,"reason":"개별주 성장/우량성장 전략 비중 0%"}
    key=f"auto:{day:%Y%m%d}:us-stock"
    history=portfolios.orders(owner,"default",limit=100000)["orders"]
    if any(o["id"]==key for o in history):
        return {"status":"already_executed","traded":False,"reason":"오늘 개별주 주문 처리 완료"}
    if any(o["side"]=="SELL" and o["id"].startswith(f"exit:{day:%Y%m%d}:") for o in history):
        return {"status":"skipped","traded":False,"reason":"오늘 매도 후 자동 재매수 제한"}
    fx=fx_provider()
    if fx.get("status")!="ok" or fx.get("executable") is not False:
        return {"status":"skipped","traded":False,"reason":"달러환율 미검증"}
    try:
        rate=Decimal(str(fx["usd_krw"]))
        if not rate.is_finite() or not 100<=rate<=10000:
            raise ValueError
    except (ValueError,TypeError,KeyError,InvalidOperation):
        return {"status":"skipped","traded":False,"reason":"환율 형식 오류"}
    gross=Decimal(cash["krw_cash"])+Decimal(cash["usd_cash"])*rate
    # Limit 5% of original total per stock purchase and retain a cash buffer.
    initial=Decimal(ledger.state(owner,day)["initial_krw"])
    invest_cap=min(initial*Decimal("0.05"),gross*(Decimal(100-reserve_ratio(weights))/100),
                   gross*Decimal(prefs["stock_weight"])/100)
    for symbol,tags in ranked:
        q=quote_provider(symbol)
        if (q.get("status")!="ok" or q.get("tradeable") is not True or
             q.get("symbol")!=symbol or q.get("currency")!="USD"):
            continue
        try:
            price=Decimal(str(q["price"]))
            if not price.is_finite() or price<=0 or price*rate>invest_cap:
                continue
            result=portfolios.auto_buy_usd_with_fx(owner,symbol,q,str(rate),key)
        except (ValueError,KeyError,ArithmeticError,InvalidOperation):
            continue
        if result["status"]=="already_executed":
            return {"status":"already_executed","traded":False}
        return {"status":"executed","traded":True,"symbol":symbol,
                "quantity":1,"reference_price_usd":str(price),"price_as_of":q["as_of"],
                "account":"default","strategy_tags":tags,
                "reason":"사용자 전략 비중에 부합한 한정된 개별주 후보; 미래 수익 예측 아님"}
    return {"status":"skipped","traded":False,
            "reason":"검증된 미국 개별주가 가격·현금·한도 조건을 충족하지 못함"}
