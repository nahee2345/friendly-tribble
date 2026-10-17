"""Keyless 30-calendar-day price trend for a catalog-verified symbol.

Daily reference close only. Returns unavailable when fewer than two
observations or the most recent close is stale. Not a forecast.
"""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
from urllib.parse import quote
from urllib.request import Request,urlopen
import json,re


def last_30_days(market,symbol,*,now=None,opener=None):
    if market not in ("KR","US"):
        return {"status":"unavailable","reason":"INVALID_MARKET"}
    if not re.fullmatch(r"[0-9]{6}" if market=="KR" else r"[A-Z][A-Z0-9.\-]{0,14}",symbol):
        return {"status":"unavailable","reason":"INVALID_SYMBOL"}
    now=now or datetime.now(timezone.utc)
    candidates=[symbol+".KS",symbol+".KQ"] if market=="KR" else [symbol]
    for ticker in candidates:
        try:
            url="https://query1.finance.yahoo.com/v8/finance/chart/"+quote(ticker,safe="")+"?range=3mo&interval=1d"
            request=Request(url,headers={"User-Agent":"Mozilla/5.0 WealthyPersonalResearch"})
            fetch=opener or urlopen
            with fetch(request,timeout=7) as response:
                raw=response.read(100_001)
            if len(raw)>100_000:raise ValueError
            payload=json.loads(raw)["chart"]["result"][0]
            meta=payload["meta"]
            expected="KRW" if market=="KR" else "USD"
            if meta.get("currency")!=expected or meta.get("symbol","").upper()!=ticker.upper():
                raise ValueError("symbol/currency mismatch")
            prices=payload["indicators"]["quote"][0]["close"]
            rows=[]
            for ts,p in zip(payload["timestamp"],prices):
                if p is None:continue
                day=datetime.fromtimestamp(int(ts),timezone.utc).date()
                price=Decimal(str(p))
                if price.is_finite() and price>0 and now.date()-timedelta(days=32)<=day<=now.date():
                    rows.append((day,price))
            rows.sort(key=lambda x:x[0])
            if len(rows)<2 or (now.date()-rows[-1][0]).days>6:
                raise ValueError("stale or missing")
            first=rows[0][1];last=rows[-1][1]
            return {"status":"ok","symbol":symbol,"market":market,
                "currency":expected,"as_of":str(rows[-1][0]),
                "first_date":str(rows[0][0]),"first_close":str(first),
                "last_close":str(last),"change_pct":round(float((last/first-1)*100),2),
                "points":[{"date":str(d),"close":str(v)} for d,v in rows],
                "source":"Yahoo Finance unofficial delayed daily closes",
                "notice":"지난 30여 일 실제 공개 종가 기준. 배당·세금·환율 변동 및 장중 가격은 제외."}
        except (OSError,KeyError,IndexError,TypeError,ValueError,InvalidOperation,TimeoutError,json.JSONDecodeError):
            continue
    return {"status":"unavailable","symbol":symbol,"market":market,
            "reason":"검증된 최근 30일 종가 자료 없음"}
