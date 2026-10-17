"""Server-side daily reference closes for *simulated* orders only.

Daily closes are delayed reference data, not executable market quotes. Unknown,
missing, stale or malformed values are never eligible for simulated execution.
"""
from __future__ import annotations
import csv, io, re, urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

_PATTERN=re.compile(r'^[A-Z][A-Z0-9.]{0,11}$')

def verified_quote(symbol:str)->dict:
    if not isinstance(symbol,str) or not _PATTERN.fullmatch(symbol):
        return {'status':'unavailable','tradeable':False,'reason':'INVALID_SYMBOL'}
    # Strict whitelist style URL construction; no arbitrary remote URL accepted.
    url=f'https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d&d1=20260101&d2={datetime.now(timezone.utc):%Y%m%d}'
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'WealthyResearch/1.0'})
        with urllib.request.urlopen(req,timeout=8) as response:
            raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('response too large')
        rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
        if not rows: raise ValueError('missing rows')
        last=rows[-1]
        quote_date=datetime.strptime(last['Date'],'%Y-%m-%d').date()
        age=(datetime.now(timezone.utc).date()-quote_date).days
        price=Decimal(last['Close'])
        if not 0<=age<=4 or not price.is_finite() or price<=0 or price.as_tuple().exponent < -4:raise ValueError('stale/invalid')
        return {'status':'ok','tradeable':True,'symbol':symbol,'price':format(price,'f'),'currency':'USD','as_of':str(quote_date),'source':'Stooq delayed daily close','delay_notice':'일간 종가 기준 모의 체결이며 실시간 호가가 아닙니다.'}
    except (OSError,ValueError,KeyError,UnicodeError,InvalidOperation,TimeoutError):
        return {'status':'unavailable','tradeable':False,'symbol':symbol,'reason':'PRICE_UNVERIFIED'}
