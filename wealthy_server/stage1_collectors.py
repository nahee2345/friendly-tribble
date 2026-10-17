"""Bounded, source-identified credential-free Stage 2 data collectors.

An observation enters the market cache only after its provider has been
verified, then after a second independent normalization/quality pass.
This module does not write to the paper trading ledger or produce orders.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

from .stage1_market_store import MarketStore, validated_snapshot
from .stage1_quotes import verified_quote
from .stage1_analytics import fetch_daily_history
from .stage1_history30 import last_30_days
from .stage1_research import current_fx
from .stage1_hub import news_30d


SEC_TICKERS="https://www.sec.gov/files/company_tickers.json"
SEC_FACTS="https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FACT_TAGS=(
    "Assets","Liabilities","StockholdersEquity",
    "Revenues","RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss","OperatingIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities",
    "EarningsPerShareDiluted","CommonStockSharesOutstanding",
)
# Network-only research. Never take arbitrary URLs or CIKs from a user.
YAHOO_DIVIDENDS="https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1d&events=div"


def dividends_from_public_chart(symbol, *,fetcher=None,now=None):
    """Unofficial Yahoo declared ex-date events; no payout date guarantee."""
    from datetime import timedelta
    now=now or datetime.now(timezone.utc)
    if not isinstance(symbol,str) or not re.fullmatch(r"(?:[A-Z][A-Z0-9.\-]{0,14}|[0-9]{6})",symbol):
        return {"status":"unavailable","reason":"INVALID_SYMBOL"}
    currency="KRW" if symbol.isdigit() else "USD"
    suffixes=(".KS",".KQ") if symbol.isdigit() else ("",)
    fetch=fetcher or urllib.request.urlopen
    for suffix in suffixes:
        ticker=symbol+suffix
        url=YAHOO_DIVIDENDS.format(ticker=urllib.parse.quote(ticker,safe=""))
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 WealthyPersonalResearch"})
            with fetch(req,timeout=8) as response:
                raw=response.read(1_500_001)
            if len(raw)>1_500_000:raise ValueError("oversized dividend response")
            parsed=json.loads(raw)["chart"]["result"][0]
            if (parsed["meta"].get("currency")!=currency or
                    parsed["meta"].get("symbol","").upper()!=ticker.upper()):
                raise ValueError("ticker/currency mismatch")
            event_map=(parsed.get("events") or {}).get("dividends") or {}
            if not isinstance(event_map,dict) or not event_map:
                continue
            events=[]
            for evt in event_map.values():
                if not isinstance(evt,dict):raise ValueError("bad dividend event")
                exdate=datetime.fromtimestamp(int(evt["date"]),timezone.utc).date()
                if not (now.date()-timedelta(days=2000)<=exdate<=now.date()):
                    raise ValueError("stale/future dividend event")
                amt=Decimal(str(evt["amount"]))
                if not amt.is_finite() or amt<=0 or amt>1_000_000:
                    raise ValueError("invalid dividend amount")
                events.append({"date":exdate.isoformat(),
                               "amount_per_share":str(amt)})
            events.sort(key=lambda x:x["date"])
            if not 1<=len(events)<=200:raise ValueError("unbounded dividend events")
            return {"status":"ok","symbol":symbol,"currency":currency,"events":events,
                    "source_url":url,"source":"Yahoo Finance unofficial dividend events",
                    "payment_dates_verified":False,"cash_applied":False}
        except (OSError,ValueError,TypeError,KeyError,IndexError,OverflowError,
                InvalidOperation,TimeoutError,json.JSONDecodeError):
            continue
    return {"status":"unavailable","reason":"NO_VERIFIED_DIVIDEND_EVENTS","symbol":symbol}


def sec_facts(symbol,*,fetcher=None,contact=None):
    """Official SEC Company Facts data without an API key, only with declared contact.

    A genuine SEC-compliant contact address is required. If none is configured,
    no request is made. No SEC figures are silently converted into ratios.
    """
    if not isinstance(symbol,str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}",symbol):
        return {"status":"unavailable","reason":"INVALID_SYMBOL"}
    contact=contact if contact is not None else os.environ.get("WEALTHY_SEC_CONTACT","")
    if not isinstance(contact,str) or not re.fullmatch(r"[^\s@]{1,64}@[^\s@]{1,190}",contact):
        return {"status":"unavailable","reason":"SEC_CONTACT_NOT_CONFIGURED"}
    fetch=fetcher or urllib.request.urlopen
    def download(url,max_bytes):
        req=urllib.request.Request(url,headers={
            "User-Agent":"Wealthy Personal Research (contact: "+contact+")",
            "Accept":"application/json"})
        with fetch(req,timeout=9) as r:data=r.read(max_bytes+1)
        if len(data)>max_bytes:raise ValueError("SEC payload oversized")
        return json.loads(data)
    try:
        tickers=download(SEC_TICKERS,1_000_000)
        if not isinstance(tickers,dict):raise ValueError("missing SEC symbol list")
        match=next((v for v in tickers.values() if isinstance(v,dict)
                    and v.get("ticker","").upper()==symbol),None)
        if match is None:return {"status":"unavailable","reason":"SEC_TICKER_NOT_FOUND"}
        cik=int(match["cik_str"])
        if not 1<=cik<=9999999999:raise ValueError("SEC CIK out of range")
        url=SEC_FACTS.format(cik=cik)
        data=download(url,9_000_000)
        if int(data["cik"])!=cik:raise ValueError("SEC issuer mismatch")
        facts=data["facts"]["us-gaap"]
        from datetime import date
        now=date.today()
        items=[]
        for tag in FACT_TAGS:
            entry=facts.get(tag)
            if not isinstance(entry,dict):continue
            unit="shares" if tag=="CommonStockSharesOutstanding" else "USD"
            if tag=="EarningsPerShareDiluted":unit="USD/shares"
            units=entry.get("units",{}).get(unit,[])
            candidates=[x for x in units if isinstance(x,dict)
                        and x.get("form") in ("10-K","10-Q","10-K/A","10-Q/A")
                        and isinstance(x.get("filed"),str) and isinstance(x.get("end"),str)
                        and x.get("val") is not None]
            valid=[]
            for item in candidates:
                try:
                    filed=date.fromisoformat(item["filed"])
                    ended=date.fromisoformat(item["end"])
                    amount=Decimal(str(item["val"]))
                    if (ended<=filed<=now and (now-filed).days<=2000
                            and amount.is_finite() and abs(amount)<Decimal("1000000000000000")):
                        valid.append((filed,ended,item,amount))
                except (ValueError,InvalidOperation,TypeError):continue
            if valid:
                last=max(valid,key=lambda x:(x[0],x[1]))
                filed,end,item,amount=last
                items.append({"tag":tag,"value":str(amount),"unit":unit,
                              "filed":filed.isoformat(),"period_end":end.isoformat(),
                              "form":item["form"],"accession":item.get("accn","")})
        if not items:return {"status":"unavailable","reason":"SEC_NO_VALID_XBRL_FACTS"}
        return {"status":"ok","symbol":symbol,"facts":items,
                "source_url":url,"source":"SEC EDGAR US GAAP Company Facts",
                "cik":cik}
    except (OSError,ValueError,KeyError,IndexError,TypeError,TimeoutError,
            json.JSONDecodeError):
        return {"status":"unavailable","reason":"SEC_SOURCE_UNAVAILABLE"}


def _provider(kind,symbol):
    if kind=="PRICE":return ("YAHOO_UNOFFICIAL" if symbol.isdigit() else "STOOQ")
    if kind=="HISTORY":return ("YAHOO_UNOFFICIAL" if symbol.isdigit() else "STOOQ")
    if kind=="FX":return "FRANKFURTER"
    if kind=="NEWS":return "GOOGLE_NEWS"
    if kind=="DIVIDENDS":return "YAHOO_UNOFFICIAL"
    if kind=="FUNDAMENTALS":return "SEC_EDGAR"
    raise ValueError("unsupported feed type")


class Collector:
    def __init__(self,store,*,quote=verified_quote,history_us=fetch_daily_history,
                 history_kr=last_30_days,fx=current_fx,news=news_30d,
                 dividends=dividends_from_public_chart,fundamentals=sec_facts):
        self.store=store
        self.quote=quote;self.history_us=history_us;self.history_kr=history_kr
        self.fx=fx;self.news=news;self.dividends=dividends;self.fundamentals=fundamentals

    def _fetch(self,kind,symbol):
        if kind=="PRICE":return self.quote(symbol)
        if kind=="HISTORY":return (self.history_kr("KR",symbol) if symbol.isdigit()
                                   else self.history_us(symbol))
        if kind=="FX":return self.fx()
        if kind=="DIVIDENDS":return self.dividends(symbol)
        if kind=="FUNDAMENTALS":return self.fundamentals(symbol)
        if kind=="NEWS":
            bundle=self.news()
            if symbol in bundle.get("unavailable",[]):
                return {"status":"unavailable","reason":"NEWS_FEED_UNAVAILABLE"}
            articles=[x for x in bundle.get("items",[]) if x.get("market")==symbol]
            return {"status":"ok","items":articles,
                    "source_url":"https://news.google.com/rss/search",
                    "symbol":symbol}
        raise ValueError("unsupported feed")

    def collect(self,kind,symbol,*,now=None,force=False):
        clock=now or datetime.now(timezone.utc)
        provider=_provider(kind,symbol)
        # Disable automatic attempts for Korean company fundamentals until a
        # legally/technically supported non-key route is actually available.
        if kind=="FUNDAMENTALS" and (symbol.isdigit() or symbol in ("KR","US")):
            return {"status":"unsupported","kind":kind,"symbol":symbol,
                    "reason":"KOREAN_OFFICIAL_FUNDAMENTALS_REQUIRE_PROVIDER_VALIDATION"}
        if not force and not self.store.due(kind,symbol,provider,now=clock):
            return {"status":"deferred","kind":kind,"symbol":symbol,
                    "cached":self.store.latest(kind,symbol,now=clock)["status"]}
        ttl={"PRICE":3600,"HISTORY":21600,"FX":3600,"NEWS":3600,
             "DIVIDENDS":86400,"FUNDAMENTALS":86400}[kind]
        try:
            raw=self._fetch(kind,symbol)
            if not isinstance(raw,dict) or raw.get("status")!="ok":
                reason=raw.get("reason") if isinstance(raw,dict) else None
                raise ValueError(reason or "PROVIDER_UNAVAILABLE")
            url=raw.get("source_url")
            if not url:raise ValueError("SOURCE_URL_MISSING")
            if kind=="NEWS" and symbol not in ("US","KR"):
                raise ValueError("INVALID_NEWS_MARKET")
            if kind=="FUNDAMENTALS" and symbol.isdigit():
                raise ValueError("KR_SEC_FACTS_UNSUPPORTED")
            # Reject mismatched source, currency, future dates and malformed
            # records before inserting anything into the cache.
            snapshot=validated_snapshot(kind,symbol,provider,url,raw,now=clock)
            stored=self.store.save(snapshot)
            self.store.record_attempt(kind,symbol,provider,"ok",now=clock,
                                      retry_seconds=ttl)
            return {"status":"ok","kind":kind,"symbol":symbol,
                    "recorded":stored["saved"],"sha256":stored["sha256"]}
        except (ValueError,KeyError,TypeError,OverflowError,OSError,TimeoutError):
            self.store.record_attempt(kind,symbol,provider,"unavailable",
                                      error_code="SOURCE_OR_VALIDATION_FAILED",
                                      now=clock,retry_seconds=max(ttl,3600))
            return {"status":"unavailable","kind":kind,"symbol":symbol,
                    "reason":"SOURCE_OR_VALIDATION_FAILED",
                    "old_snapshot":self.store.latest(kind,symbol,now=clock)["status"]}

    def tick(self,owner,portfolios,*,max_requests=7):
        """Refresh only a bounded subset, keeping server responsive offline."""
        if type(max_requests) is not int or not 1<=max_requests<=12:
            raise ValueError("invalid data-collection request budget")
        accounts=portfolios.list(owner)["accounts"]
        held=[]
        for account in accounts:
            for pos in account["holdings"]:
                if pos["symbol"] not in held:
                    held.append(pos["symbol"])
        from .stage1_discovery import CANDIDATES
        universe=held+[code for code,*_ in CANDIDATES if code not in held]
        universe+=["005930","000660","069500","360750"]
        tasks=[("FX","USD/KRW"),("NEWS","US"),("NEWS","KR")]
        tasks.extend(("PRICE",s) for s in dict.fromkeys(universe))
        tasks.extend(("HISTORY",s) for s in dict.fromkeys(universe[:4]))
        tasks.extend(("DIVIDENDS",s) for s in dict.fromkeys(universe[:4]))
        attempted=[]
        for kind,symbol in tasks:
            if len(attempted)>=max_requests:break
            provider=_provider(kind,symbol)
            if self.store.due(kind,symbol,provider):
                attempted.append(self.collect(kind,symbol))
        return {"attempts":attempted,"max_requests":max_requests,
                "paper_trade_status":"NOT_CONNECTED","store":self.store.health(limit=12)}
