"""Offline Stage2 source-validation, provider resilience and cache integration."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from wealthy_server.stage1_market_store import MarketStore, validated_snapshot
from wealthy_server.stage1_collectors import Collector, dividends_from_public_chart, sec_facts
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios


NOW=datetime(2026,10,17,10,0,0,tzinfo=timezone.utc)
URL_STOOQ="https://stooq.com/q/d/l/?s=aapl.us&i=d&d1=20260101&d2=20261017"
URL_KR="https://query1.finance.yahoo.com/v8/finance/chart/005930.KS?range=10d&interval=1d"
URL_FX="https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW"
URL_NEWS="https://news.google.com/rss/search"


def quote():
    return {"status":"ok","tradeable":True,"symbol":"AAPL","currency":"USD",
            "price":"180.25","source":"Stooq delayed daily close",
            "source_url":URL_STOOQ,"as_of":"2026-10-16"}


def fx():
    return {"status":"ok","executable":False,"usd_krw":"1400.34",
            "source_url":URL_FX,"as_of":"2026-10-16"}


def test_immutable_source_snapshot_separate_from_paper_ledger(tmp_path):
    paper=Portfolios(tmp_path/"paper.sqlite3")
    cash=Stage1Ledger(paper.path)
    cash.state("owner",date(2026,10,17))
    before=paper.list("owner")["accounts"]
    cache=MarketStore.for_portfolios(paper)
    record=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,quote(),now=NOW)
    assert cache.save(record)["saved"] is True
    assert cache.save(record)["saved"] is False
    snapshot=cache.latest("PRICE","AAPL",now=NOW)
    assert snapshot["status"]=="ok"
    assert snapshot["as_of"]=="2026-10-16"
    assert snapshot["source_url"]==URL_STOOQ
    assert snapshot["tradeable"] is False and snapshot["use_for_paper_execution"] is False
    assert snapshot["data"]["order_eligible_from_cache"] is False
    assert cache.latest("PRICE","AAPL",now=NOW+timedelta(hours=2))["status"]=="stale"
    assert paper.list("owner")["accounts"]==before
    assert cache.path!=paper.path
    assert cache.path.stat().st_mode&0o077==0


@pytest.mark.parametrize("modified",[
    {"price":"NaN"},{"price":"Infinity"},{"price":"-1"},
    {"as_of":"2026-10-18"},{"as_of":"2026-08-01"},
    {"symbol":"MSFT"},{"currency":"KRW"},{"tradeable":False},
    {"status":"unavailable"},{"price":"0"}
])
def test_broken_quote_is_rejected(modified):
    with pytest.raises(ValueError):
        validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,
                           {**quote(),**modified},now=NOW)


@pytest.mark.parametrize("url",[
    "http://stooq.com/q/d/l/?s=aapl.us&i=d",
    "https://stooq.com.evil.invalid/q/d/l/?s=aapl.us&i=d",
    "https://stooq.com/q/d/l/?s=msft.us&i=d",
    "https://stooq.com/q/d/l/?s=aapl.us&i=w",
    "https://user:secret@stooq.com/q/d/l/?s=aapl.us&i=d",
])
def test_source_origin_and_ticker_are_immutable(url):
    with pytest.raises(ValueError):
        validated_snapshot("PRICE","AAPL","STOOQ",url,quote(),now=NOW)


def test_kr_and_fx_validate_independently():
    kr={"status":"ok","symbol":"005930","tradeable":True,
        "currency":"KRW","price":"80000","as_of":"2026-10-16"}
    assert validated_snapshot("PRICE","005930","YAHOO_UNOFFICIAL",
                              URL_KR,kr,now=NOW)["payload"]["price"]=="80000"
    with pytest.raises(ValueError):
        validated_snapshot("PRICE","005930","YAHOO_UNOFFICIAL",URL_KR,
                           {**kr,"price":"80000.45"},now=NOW)
    with pytest.raises(ValueError):
        validated_snapshot("FX","USD/KRW","FRANKFURTER",
                           "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=KRW",
                           fx(),now=NOW)
    good=validated_snapshot("FX","USD/KRW","FRANKFURTER",URL_FX,fx(),now=NOW)
    assert good["payload"]["executable"] is False


def test_history_duplicate_future_and_binary_float_normalization():
    payload={"status":"ok","symbol":"AAPL","currency":"USD",
             "as_of":"2026-10-16","prices":[
                 {"date":"2026-10-15","close":"180.23999786376953"},
                 {"date":"2026-10-16","close":"181.20"}]}
    recorded=validated_snapshot("HISTORY","AAPL","STOOQ",URL_STOOQ,payload,now=NOW)
    assert recorded["quality"]=="RESEARCH_ONLY"
    assert recorded["payload"]["prices"][0]["close"]=="180.239998"
    with pytest.raises(ValueError):
        validated_snapshot("HISTORY","AAPL","STOOQ",URL_STOOQ,
                           {**payload,"prices":[payload["prices"][0]]*2},now=NOW)


def test_collect_cooldown_failures_do_not_replace_latest_good(tmp_path):
    store=MarketStore(tmp_path/"market.sqlite3")
    calls=[]
    def quote_provider(symbol):
        calls.append(symbol)
        return quote() if len(calls)==1 else {"status":"unavailable","reason":"SOURCE_OFFLINE"}
    ingester=Collector(store,quote=quote_provider)
    first=ingester.collect("PRICE","AAPL",now=NOW)
    assert first["status"]=="ok" and len(calls)==1
    later=ingester.collect("PRICE","AAPL",now=NOW+timedelta(minutes=5))
    assert later["status"]=="deferred" and len(calls)==1
    failed=ingester.collect("PRICE","AAPL",now=NOW+timedelta(hours=2))
    assert failed["status"]=="unavailable"
    assert len(calls)==2
    assert store.latest("PRICE","AAPL",now=NOW+timedelta(hours=2))["status"]=="stale"
    assert store.latest("PRICE","AAPL")["tradeable"] is False
    attempts=store.health()["recent_attempts"]
    assert attempts[0]["error_code"]=="SOURCE_OFFLINE"


def test_bad_checksum_or_unauthorized_cache_path_is_denied(tmp_path):
    store=MarketStore(tmp_path/"market.sqlite3")
    good=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,quote(),now=NOW)
    with pytest.raises(ValueError,match="hash"):
        store.save({**good,"sha256":"0"*64})
    location=tmp_path/"unsafe.sqlite3"
    location.symlink_to(tmp_path/"market.sqlite3")
    with pytest.raises(ValueError):
        MarketStore(location)
    assert store.health()["snapshots"]=={}


def test_news_has_grounded_link_date_and_partial_coverage(tmp_path):
    item={"title":"AAPL profits decline","url":"https://news.google.com/articles/demo",
          "published_utc":"2026-10-16T18:00:00+00:00"}
    good=validated_snapshot("NEWS","US","GOOGLE_NEWS",URL_NEWS,
                            {"items":[item,item]},now=NOW)
    assert len(good["payload"]["items"])==1
    assert good["payload"]["full_article_verified"] is False
    with pytest.raises(ValueError):
        validated_snapshot("NEWS","US","GOOGLE_NEWS",URL_NEWS,
                           {"items":[{**item,"url":"https://evil.invalid/news"}]},now=NOW)
    with pytest.raises(ValueError):
        validated_snapshot("NEWS","US","GOOGLE_NEWS",URL_NEWS,
                           {"items":[{**item,"published_utc":"2026-10-16T18:00:00"}]},now=NOW)
    def bundled_news():
        return {"status":"partial","items":[{**item,"market":"US"}],
                "unavailable":["KR"]}
    store=MarketStore(tmp_path/"news.sqlite3")
    ingester=Collector(store,news=bundled_news)
    assert ingester.collect("NEWS","US",now=NOW)["status"]=="ok"
    assert ingester.collect("NEWS","KR",now=NOW)["status"]=="unavailable"
    assert store.latest("NEWS","US",now=NOW)["data"]["coverage"]=="PUBLIC_RSS_SUBSET"
    assert store.latest("NEWS","KR")["status"]=="unavailable"


class Response:
    def __init__(self,body):self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n):return self.body[:n]

def test_dividend_events_are_research_only_and_no_cash():
    stamp=int(datetime(2026,10,16,15,tzinfo=timezone.utc).timestamp())
    sample={"chart":{"result":[{"meta":{"symbol":"AAPL","currency":"USD"},
             "events":{"dividends":{str(stamp):{"date":stamp,"amount":0.26}}}}]}}
    fetched=[]
    def fake(request,timeout):
        fetched.append(request.full_url)
        return Response(json.dumps(sample).encode())
    d=dividends_from_public_chart("AAPL",fetcher=fake,now=NOW)
    assert d["status"]=="ok" and len(fetched)==1
    norm=validated_snapshot("DIVIDENDS","AAPL","YAHOO_UNOFFICIAL",
                            d["source_url"],d,now=NOW)
    assert norm["payload"]["cash_applied"] is False
    assert norm["payload"]["payment_dates_verified"] is False


def test_sec_no_contact_never_calls_external_service():
    def no_request(*args,**kwargs):raise AssertionError("SEC must not be called")
    result=sec_facts("AAPL",fetcher=no_request,contact="")
    assert result["reason"]=="SEC_CONTACT_NOT_CONFIGURED"


def test_sec_signed_facts_are_research_only():
    payload={"status":"ok","symbol":"AAPL","facts":[
        {"tag":"NetIncomeLoss","value":"-200000","unit":"USD",
         "filed":"2026-10-16","period_end":"2026-09-30","form":"10-Q","accession":"test"},
        {"tag":"EarningsPerShareDiluted","value":"-0.32","unit":"USD/shares",
         "filed":"2026-10-16","period_end":"2026-09-30","form":"10-Q","accession":"test"}]}
    normalized=validated_snapshot("FUNDAMENTALS","AAPL","SEC_EDGAR",
              "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
              payload,now=NOW)
    assert normalized["payload"]["facts"][0]["value"]=="-200000"
    assert normalized["payload"]["ratios_computed"] is False


def test_same_close_reverified_without_mutating_immutable_observation(tmp_path):
    store=MarketStore(tmp_path/"cache.sqlite3")
    first=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,quote(),now=NOW)
    assert store.save(first)["saved"] is True
    again=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,quote(),
                             now=NOW+timedelta(hours=2))
    assert store.save(again)["saved"] is False
    entry=store.latest("PRICE","AAPL",now=NOW+timedelta(hours=2))
    assert entry["status"]=="ok" and entry["age_seconds"]==0
    assert entry["recorded_utc"]==NOW.isoformat()
    assert entry["last_verified_utc"]==(NOW+timedelta(hours=2)).isoformat()
    assert store.health()["snapshots"]=={"PRICE":1}


def test_reverted_close_becomes_latest_without_rewriting_old_price(tmp_path):
    store=MarketStore(tmp_path/"cache.sqlite3")
    old=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,quote(),now=NOW)
    changed=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,
                               {**quote(),"price":"181.20"},
                               now=NOW+timedelta(hours=1))
    reverted=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,quote(),
                                now=NOW+timedelta(hours=2))
    assert store.save(old)["saved"]
    assert store.save(changed)["saved"]
    assert store.save(reverted)["saved"] is False
    current=store.latest("PRICE","AAPL",now=NOW+timedelta(hours=2))
    assert current["data"]["price"]=="180.25"
    assert current["last_verified_utc"]==(NOW+timedelta(hours=2)).isoformat()
    assert store.health()["snapshots"]=={"PRICE":2}


def test_backdated_attempt_cannot_supersede_newer_verified_reference(tmp_path):
    store=MarketStore(tmp_path/"cache.sqlite3")
    fresh=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,quote(),
                             now=NOW+timedelta(hours=3))
    stale=validated_snapshot("PRICE","AAPL","STOOQ",URL_STOOQ,
                             {**quote(),"price":"150.00"},now=NOW)
    store.save(fresh)
    store.save(stale)
    current=store.latest("PRICE","AAPL",now=NOW+timedelta(hours=3))
    assert current["data"]["price"]=="180.25"
    assert current["status"]=="ok"


def test_news_feed_download_is_shared_across_us_kr_collections(tmp_path):
    captured=[]
    def news_bundle():
        captured.append("rss")
        return {"status":"ok","unavailable":[],"items":[
            {"title":"US company earnings","market":"US",
             "url":"https://news.google.com/articles/us-example",
             "published_utc":"2026-10-16T18:00:00+00:00"},
            {"title":"한국 기업 실적","market":"KR",
             "url":"https://news.google.com/articles/kr-example",
             "published_utc":"2026-10-16T18:00:00+00:00"}]}
    store=MarketStore(tmp_path/"rss.sqlite3")
    collector=Collector(store,news=news_bundle)
    assert collector.collect("NEWS","US",now=NOW)["status"]=="ok"
    assert collector.collect("NEWS","KR",now=NOW)["status"]=="ok"
    assert captured==["rss"]
    assert len(store.latest("NEWS","US",now=NOW)["data"]["items"])==1
    assert len(store.latest("NEWS","KR",now=NOW)["data"]["items"])==1


def test_bounded_market_refresh_never_places_paper_orders(tmp_path):
    path=tmp_path/"paper.sqlite3"
    ledger=Stage1Ledger(path)
    ledger.state("owner",date(2026,10,17))
    portfolios=Portfolios(path)
    before=portfolios.list("owner")["accounts"]
    store=MarketStore.for_portfolios(portfolios)
    def news_bundle():return {"status":"ok","items":[],"unavailable":[]}
    def quote_unavailable(code):return {"status":"unavailable","reason":"PRICE_UNVERIFIED"}
    collector=Collector(store,news=news_bundle,fx=fx,quote=quote_unavailable)
    result=collector.tick("owner",portfolios,max_requests=4)
    assert len(result["attempts"])==4
    assert result["paper_trade_status"]=="NOT_CONNECTED"
    assert len(portfolios.orders("owner","default")["orders"])==0
    assert portfolios.list("owner")["accounts"]==before
    assert set(x["kind"] for x in result["attempts"])=={"FX","NEWS","PRICE"}


def test_sec_official_no_key_fetch_with_configured_identity():
    import urllib.request
    from datetime import date
    current=date.today().isoformat()
    official={"0":{"ticker":"AAPL","cik_str":320193}}
    facts={"cik":320193,"facts":{"us-gaap":{"Assets":{"units":{"USD":[{
           "form":"10-Q","filed":current,"end":current,"val":120000.0
    }]}}}}}
    requested=[]
    def fake(req,timeout):
        requested.append((req.full_url,req.headers.get("User-agent")))
        if req.full_url.endswith("company_tickers.json"):
            return Response(json.dumps(official).encode())
        return Response(json.dumps(facts).encode())
    result=sec_facts("AAPL",fetcher=fake,contact="research@example.com")
    assert result["status"]=="ok" and result["cik"]==320193
    assert len(requested)==2 and all("research@example.com" in x[1] for x in requested)
    validated=validated_snapshot("FUNDAMENTALS","AAPL","SEC_EDGAR",
                                  result["source_url"],result)
    assert validated["payload"]["facts"][0]["tag"]=="Assets"
    assert validated["quality"]=="RESEARCH_ONLY"
