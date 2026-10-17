"""No-key Korean individual-stock paper entries based on delayed KRX closes.

Uses explicit conservative limits and portfolio preference, not alleged AI
forecasts. Stocks in the normal domestic account only; never ISA.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo
from .stage1_quotes import verified_quote

STOCKS=(("005930","삼성전자"),("000660","SK하이닉스"))


def paper_run_kr_stocks(owner,portfolios,preference_store,*,today=None,
                        quote_provider=verified_quote,reserve_percent=25):
    day=today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    prefs=preference_store.get(owner)
    if prefs["kr_weight"]==0 or prefs["stock_weight"]==0:
        return {"status":"skipped","traded":False,"reason":"한국 개별주 비중 0%"}
    accounts={a["id"]:a for a in portfolios.list(owner)["accounts"]}
    account=accounts.get("kr-base")
    if account is None:
        return {"status":"skipped","traded":False,"reason":"국내 일반 가상계좌 미존재"}
    if type(reserve_percent) is not int or not 0<=reserve_percent<=100:
        return {"status":"skipped","traded":False,"reason":"현금 유보율 오류"}
    order_key=f"auto:{day:%Y%m%d}:kr-stock"
    history=portfolios.orders(owner,"kr-base",limit=100000)["orders"]
    if any(o["id"]==order_key for o in history):
        return {"status":"already_executed","traded":False}
    if any(o["side"]=="SELL" and o["id"].startswith(f"exit:{day:%Y%m%d}:") for o in history):
        return {"status":"skipped","traded":False,"reason":"금일 자동 매도 후 재매수 제한"}
    cash=Decimal(account["krw_cash"])
    max_spend=min(Decimal("100000"),cash*Decimal(100-reserve_percent)/100,
                  cash*Decimal(prefs["stock_weight"])/100)
    for ticker,name in STOCKS:
        q=quote_provider(ticker)
        if (q.get("status")!="ok" or q.get("tradeable") is not True or
             q.get("symbol")!=ticker or q.get("currency")!="KRW"):
            continue
        try:
            p=Decimal(str(q["price"]))
            if not p.is_finite() or p<=0 or p>max_spend:
                continue
            shares=next((int(h["quantity"]) for h in account["holdings"]
                         if h["symbol"]==ticker),0)
            if (shares+1)*p>Decimal("200000"):
                continue
            portfolios.trade(owner,"kr-base",ticker,"BUY",1,order_key,q)
            return {"status":"executed","traded":True,"symbol":ticker,
                    "name":name,"account":"kr-base","quantity":1,
                    "reference_price_krw":str(p),"price_as_of":q["as_of"],
                    "reason":"한국 일반계좌 개별주 투자비중에 따른 제한적 시세 검증 매수"}
        except (ValueError,KeyError,InvalidOperation,ArithmeticError):
            continue
    return {"status":"skipped","traded":False,"reason":"검증가격·잔액·보유한도 미충족"}
