"""Offline Stage 3 quantitative strategy acceptance tests.

Never fetch providers, execute virtual trades, invent missing scores, or treat
SEC quarterly values as annual figures.
"""
from __future__ import annotations
import math
import json
from datetime import datetime,timedelta,date,timezone
from decimal import Decimal

import pytest

from wealthy_server.stage1_market_store import MarketStore,validated_snapshot
from wealthy_server.stage1_quantitative import (
    QuantEngine,evaluate,_annual,_growth,_quality,_value,_dividends,
    _momentum,_diversification,_history,_fresh,
)
from wealthy_server.stage1_strategy_policy import DEFAULT_WEIGHTS

NOW=datetime(2026,10,17,10,0,tzinfo=timezone.utc)
US_URL="https://stooq.com/q/d/l/?s={symbol}.us&i=d&d1=20251201&d2=20261017"
KR_URL="https://query1.finance.yahoo.com/v8/finance/chart/069500.KS?range=3mo&interval=1d"
SEC_URL="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
DIV_URL="https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5y&interval=1d&events=div"
YEAR_CURRENT=date(2025,9,30)
YEAR_OLD=date(2024,9,30)


def _price(symbol="AAPL",price="100"):
    return {"status":"ok","tradeable":True,"symbol":symbol,"currency":"USD",
            "price":price,"as_of":"2026-10-16"}


def _rows(symbol="AAPL",direction=1):
    # Market weekdays only. No future prices, 160+ overlapping sessions.
    days=[NOW.date()-timedelta(days=i) for i in reversed(range(255))]
    days=[d for d in days if d.weekday()<5]
    rows=[]
    for i,day in enumerate(days):
        p=100+direction*i*.18+1.6*math.sin(i/5+direction*.5)
        rows.append({"date":day.isoformat(),"close":str(round(p,4))})
    return {"status":"ok","symbol":symbol,"currency":"USD",
            "as_of":rows[-1]["date"],"prices":rows}


def _fact(tag,value,end,start=None,unit="USD"):
    return {"tag":tag,"value":str(value),"unit":unit,
            "filed":(end+timedelta(days=35)).isoformat(),
            "period_end":end.isoformat(),
            "period_start":start.isoformat() if start else None,
            "form":"10-K","accession":"0000320193-26-000001"}


def annual_facts():
    return [
        _fact("StockholdersEquity",10000,YEAR_CURRENT),
        _fact("StockholdersEquity",9000,YEAR_OLD),
        _fact("CommonStockSharesOutstanding",100,YEAR_CURRENT,unit="shares"),
        _fact("NetIncomeLoss",1000,YEAR_CURRENT,date(2024,10,1)),
        _fact("Revenues",6000,YEAR_CURRENT,date(2024,10,1)),
        _fact("Revenues",5000,YEAR_OLD,date(2023,10,1)),
    ]


def _dividends():
    return {"status":"ok","symbol":"AAPL","currency":"USD",
            "events":[
                {"date":"2024-12-15","amount_per_share":"0.20"},
                {"date":"2025-02-15","amount_per_share":"0.20"},
                {"date":"2025-05-15","amount_per_share":"0.20"},
                {"date":"2025-08-15","amount_per_share":"0.20"},
                {"date":"2025-11-15","amount_per_share":"0.30"},
                {"date":"2026-02-15","amount_per_share":"0.30"},
                {"date":"2026-05-15","amount_per_share":"0.30"},
                {"date":"2026-08-15","amount_per_share":"0.30"},
            ]}


def save(store,kind,symbol,provider,url,payload):
    value=validated_snapshot(kind,symbol,provider,url,payload,now=NOW)
    store.save(value)
    return value


def populated(tmp_path):
    cache=MarketStore(tmp_path/"market.sqlite3")
    save(cache,"PRICE","AAPL","STOOQ",
         US_URL.format(symbol="aapl"),_price())
    save(cache,"HISTORY","AAPL","STOOQ",
         US_URL.format(symbol="aapl"),_rows())
    save(cache,"HISTORY","MSFT","STOOQ",
         US_URL.format(symbol="msft"),_rows("MSFT",-1))
    save(cache,"FUNDAMENTALS","AAPL","SEC_EDGAR",SEC_URL,
         {"status":"ok","symbol":"AAPL","facts":annual_facts()})
    save(cache,"DIVIDENDS","AAPL","YAHOO_UNOFFICIAL",DIV_URL,_dividends())
    return cache


def test_all_eight_factors_are_computed_from_verified_evidence(tmp_path):
    cache=populated(tmp_path)
    weights={x:12 for x in DEFAULT_WEIGHTS}
    weights["index"]=16
    # Stock cannot inherit an ETF index score even with perfect price data.
    output=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK","name":"Apple"}],
                                  weights,holdings=[{"symbol":"MSFT","quantity":2}],now=NOW)
    result=output["items"][0]
    assert result["strategies"]["index"]["score"] is None
    for code in ("value","quality_growth","growth","high_dividend",
                 "dividend_growth","momentum","asset_allocation"):
        assert result["strategies"][code]["status"]=="scored",code
        assert 0<=result["strategies"][code]["score"]<=100
    assert result["coverage_pct"]==84
    assert result["score"] is not None
    assert result["status"]=="research_ready"
    assert result["no_orders"] is True and result["paper_order_eligible"] is False
    assert any(x["provider"]=="SEC_EDGAR" for x in result["source_evidence"])
    assert any(x["provider"]=="STOOQ" for x in result["source_evidence"])


def test_default_allocation_is_insufficient_without_source_data(tmp_path):
    store=MarketStore(tmp_path/"empty.sqlite3")
    results=QuantEngine(store).batch([{"symbol":"AAPL","kind":"STOCK"}],
                                    DEFAULT_WEIGHTS.copy(),now=NOW)
    item=results["items"][0]
    assert item["score"] is None and item["partial_score"] is None
    assert item["coverage_pct"]==0
    assert item["recommendation"]=="자료 부족 · 매수 판단 보류"
    assert all(x["score"] is None for x in item["strategies"].values())


def test_partial_high_score_is_not_full_recommendation(tmp_path):
    cache=populated(tmp_path)
    weights={k:0 for k in DEFAULT_WEIGHTS}
    weights["index"]=80
    weights["momentum"]=20
    result=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],
                                  weights,now=NOW)["items"][0]
    assert result["coverage_pct"]==20
    assert result["partial_score"] is not None
    assert result["score"] is None
    assert result["recommendation"]=="자료 부족 · 매수 판단 보류"


def test_quarterly_figures_must_not_be_used_as_yearly_growth(tmp_path):
    cache=MarketStore(tmp_path/"cache.sqlite3")
    annual=annual_facts()
    # Remove proper annual sales, substitute two quarterly records with
    # apparently high quarterly increase; never call this annual growth.
    annual=[row for row in annual if row["tag"]!="Revenues"]
    for end,val in ((YEAR_CURRENT,10000),(YEAR_OLD,1000)):
        row=_fact("Revenues",val,end,end-timedelta(days=89))
        row["form"]="10-Q"
        annual.append(row)
    save(cache,"FUNDAMENTALS","AAPL","SEC_EDGAR",SEC_URL,
         {"status":"ok","symbol":"AAPL","facts":annual})
    weights={k:0 for k in DEFAULT_WEIGHTS};weights["growth"]=100
    got=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],
                                weights,now=NOW)["items"][0]
    assert got["strategies"]["growth"]["status"]=="unavailable"
    assert got["score"] is None


def test_annual_yoy_needs_matching_revenue_tag_and_valid_duration(tmp_path):
    cache=populated(tmp_path)
    fact=cache.latest("FUNDAMENTALS","AAPL",now=NOW)["data"]["facts"]
    assert _growth(fact,{"kind":"FUNDAMENTALS","provider":"SEC_EDGAR",
           "source_url":SEC_URL,"as_of":"2025-11-04","sha256":"a",
           "recorded_utc":NOW.isoformat()},NOW.date())["status"]=="scored"
    invalid=[x for x in fact if not (x["tag"]=="Revenues" and x["period_end"]==YEAR_OLD.isoformat())]
    assert _growth(invalid,{"kind":"FUNDAMENTALS","provider":"SEC_EDGAR",
           "source_url":SEC_URL,"as_of":"2025-11-04","sha256":"a",
           "recorded_utc":NOW.isoformat()},NOW.date())["status"]=="unavailable"


def test_dividend_past_events_not_cash_or_tax_gains(tmp_path):
    cache=populated(tmp_path)
    weights={k:0 for k in DEFAULT_WEIGHTS}
    weights["high_dividend"]=50;weights["dividend_growth"]=50
    result=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],
                                  weights,now=NOW)["items"][0]
    assert result["coverage_pct"]==100 and result["score"] is not None
    assert "cash_applied" not in result
    assert result["strategies"]["high_dividend"]["metrics"]["events_recent"]>=3
    assert result["strategies"]["dividend_growth"]["metrics"]["historical_dividend_growth_pct"]>0


def test_held_security_cannot_correlate_with_itself_as_diversification(tmp_path):
    cache=populated(tmp_path)
    weights={k:0 for k in DEFAULT_WEIGHTS};weights["asset_allocation"]=100
    only_self=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],
                                      weights,holdings=[{"symbol":"AAPL","quantity":5}],
                                      now=NOW)["items"][0]
    assert only_self["strategies"]["asset_allocation"]["status"]=="unavailable"
    with_peer=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],
                                      weights,holdings=[{"symbol":"MSFT","quantity":5}],
                                      now=NOW)["items"][0]
    assert with_peer["strategies"]["asset_allocation"]["status"]=="scored"
    assert with_peer["strategies"]["asset_allocation"]["metrics"]["common_sessions"]>=60


def test_source_sha_mismatch_fail_closed_for_evaluation(tmp_path):
    cache=populated(tmp_path)
    with cache._db() as db:
        db.execute("UPDATE market_snapshots SET payload_json='{}' WHERE kind='HISTORY' AND symbol='AAPL'")
    got=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],
                               DEFAULT_WEIGHTS.copy(),now=NOW)["items"][0]
    assert got["strategies"]["momentum"]["status"]=="unavailable"
    assert got["strategies"]["value"]["status"]=="scored"
    assert got["score"] is None


def test_input_weights_strict_no_bool_missing_or_excess(tmp_path):
    cache=MarketStore(tmp_path/"m.sqlite3")
    bad=[{**DEFAULT_WEIGHTS,"momentum":True},
         {**DEFAULT_WEIGHTS,"momentum":200},
         {**DEFAULT_WEIGHTS,"growth":-5},
         {"index":100}]
    for weights in bad:
        with pytest.raises(ValueError):
            QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],weights,now=NOW)
    with pytest.raises(ValueError):
        QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}]*21,
                                 DEFAULT_WEIGHTS.copy(),now=NOW)


def test_quant_engine_makes_zero_network_calls_or_trades(tmp_path,monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,"urlopen",
                        lambda *a,**kw: (_ for _ in ()).throw(AssertionError("network")))
    cache=populated(tmp_path)
    response=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],
                                       DEFAULT_WEIGHTS.copy(),now=NOW)
    assert response["read_only"] is True
    assert response["items"][0]["paper_order_eligible"] is False
    assert cache.health()["snapshots"]


def test_index_eligibility_requires_etf_history_and_not_stock(tmp_path):
    cache=MarketStore(tmp_path/"m.sqlite3")
    save(cache,"HISTORY","VOO","STOOQ",US_URL.format(symbol="voo"),
         _rows("VOO"))
    weights={k:0 for k in DEFAULT_WEIGHTS};weights["index"]=100
    etf=QuantEngine(cache).batch([{"symbol":"VOO","kind":"ETF"}],weights,now=NOW)["items"][0]
    assert etf["coverage_pct"]==100 and etf["score"] is not None
    stock=QuantEngine(cache).batch([{"symbol":"VOO","kind":"STOCK"}],weights,now=NOW)["items"][0]
    assert stock["score"] is None


def test_missing_cross_currency_data_never_claims_diversification(tmp_path):
    cache=populated(tmp_path)
    weights={k:0 for k in DEFAULT_WEIGHTS};weights["asset_allocation"]=100
    result=QuantEngine(cache).batch([{"symbol":"AAPL","kind":"STOCK"}],weights,
                 holdings=[{"symbol":"069500","quantity":2}],now=NOW)["items"][0]
    assert result["strategies"]["asset_allocation"]["status"]=="unavailable"
