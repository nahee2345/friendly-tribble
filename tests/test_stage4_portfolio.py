"""Stage4 full-portfolio planner / paper executor offline acceptance.

Synthetic validated observations, no HTTP, no live brokerage credentials.
"""
from __future__ import annotations
import math
from datetime import datetime,date,timedelta,timezone

import pytest

from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_strategy_policy import StrategyPolicy,DEFAULT_WEIGHTS
from wealthy_server.stage1_discovery import PreferenceStore
from wealthy_server.stage1_market_store import MarketStore,validated_snapshot
from wealthy_server.stage1_controls import status as paused_status,set_paused
from wealthy_server.stage4_portfolio import PortfolioEngine,recorded_claims,CANDIDATES

NOW=datetime(2026,10,19,10,0,tzinfo=timezone.utc)
CLOSE="2026-10-16"
FX_URL="https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW"


def source(symbol):
    return ("https://query1.finance.yahoo.com/v8/finance/chart/"+symbol+".KS?range=3mo&interval=1d"
           if symbol.isdigit() else
           "https://stooq.com/q/d/l/?s="+symbol.lower()+".us&i=d&d1=20260301&d2=20261019")


def quote(symbol,price=None):
    if price is None:
        price="80" if symbol=="VOO" else "20000"
    return {"status":"ok","tradeable":True,"symbol":symbol,
            "currency":"KRW" if symbol.isdigit() else "USD",
            "price":str(price),"as_of":CLOSE,"source":"independent fixture close"}


def fresh_fx():
    return {"status":"ok","executable":False,"usd_krw":"1400",
            "as_of":CLOSE,"source":"independent fixture USD/KRW"}


def add(cache,kind,symbol,payload,provider=None,url=None):
    provider=provider or ("FRANKFURTER" if kind=="FX" else
                          "YAHOO_UNOFFICIAL" if symbol.isdigit() else "STOOQ")
    url=url or (FX_URL if kind=="FX" else source(symbol))
    cache.save(validated_snapshot(kind,symbol,provider,url,payload,now=NOW))


def history(symbol):
    days=[NOW.date()-timedelta(days=i) for i in reversed(range(145))]
    rows=[]
    for index,day in enumerate(d for d in days if d.weekday()<5 and d<=date(2026,10,16)):
        base=80 if symbol=="VOO" else 20000
        value=base*(0.89+index*.0012)+base*.001*math.sin(index/4)
        rows.append({"date":day.isoformat(),"close":str(round(value,4))})
    return {"status":"ok","symbol":symbol,
            "currency":"KRW" if symbol.isdigit() else "USD",
            "as_of":rows[-1]["date"],"prices":rows}


def setup(tmp_path,*,us=90,etf=80,indextarget=True,with_fx=True):
    path=tmp_path/"stage1-paper.sqlite3"
    ledger=Stage1Ledger(path)
    portfolios=Portfolios(path)
    initialize_paper_accounts("owner",ledger,portfolios,today=NOW.date())
    prefs=PreferenceStore(path)
    prefs.save("owner",us,etf)
    policy=StrategyPolicy(portfolios)
    if indextarget:
        weights={key:(100 if key=="index" else 0) for key in DEFAULT_WEIGHTS}
        policy.save("owner",weights)
    store=MarketStore.for_portfolios(portfolios)
    if with_fx:
        add(store,"FX","USD/KRW",{"status":"ok","executable":False,
            "usd_krw":"1400","as_of":CLOSE})
    engine=PortfolioEngine(portfolios,prefs,store,policy=policy,
                           quote_provider=quote,fx_provider=fresh_fx)
    return ledger,portfolios,prefs,policy,store,engine


def make_symbol(store,symbol,price):
    add(store,"PRICE",symbol,quote(symbol,price))
    add(store,"HISTORY",symbol,history(symbol))


def test_default_pause_does_not_execute_planned_buy(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path)
    make_symbol(cache,"VOO",80)
    plan=engine.preview("owner",now=NOW)
    assert plan["status"]=="ready"
    assert plan["complete_valuation"] is True
    assert plan["trade_enabled"] is False
    assert len(plan["proposed_orders"])==1
    order=plan["proposed_orders"][0]
    assert order["symbol"]=="VOO" and order["account"]=="default"
    assert order["quantity"]==1 and order["side"]=="BUY"
    assert order["strategy_score"]>=70 and order["coverage_pct"]==100
    assert engine.run("owner",now=NOW)["status"]=="paused"
    assert p.orders("owner","default")["orders"]==[]


def test_one_owner_paper_execution_idempotent_and_within_cap(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path)
    make_symbol(cache,"VOO",80)
    set_paused("owner",p,False,"test stage4 opt in")
    first=engine.run("owner",now=NOW)
    assert first["status"]=="completed"
    assert len(first["executed"])==1
    assert first["executed"][0]["symbol"]=="VOO"
    assert p.list("owner")["accounts"][0]["holdings"]==[{"symbol":"VOO","quantity":1}]
    second=engine.run("owner",now=NOW)
    assert not second["executed"]
    assert len(p.orders("owner","default")["orders"])==1
    claims=recorded_claims("owner",p)["items"]
    assert len(claims)==1 and claims[0]["status"]=="EXECUTED"
    assert not recorded_claims("other",p)["items"]


def test_independently_reverified_price_deviation_rejected_without_claim(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path)
    make_symbol(cache,"VOO",80)
    set_paused("owner",p,False,"test")
    engine.quote_provider=lambda symbol:quote(symbol,100)   # +25%
    blocked=engine.run("owner",now=NOW)
    assert not blocked["executed"]
    assert blocked["skipped"][0]["reason"]=="PRICE_MOVED_MORE_THAN_3_PERCENT"
    assert recorded_claims("owner",p)["items"]==[]
    engine.quote_provider=quote
    assert len(engine.run("owner",now=NOW)["executed"])==1


def test_missing_fx_entire_global_plan_blocks_and_no_provider_call(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path,with_fx=False)
    make_symbol(cache,"VOO",80)
    engine.quote_provider=lambda symbol:(_ for _ in ()).throw(AssertionError("no trade quote"))
    plan=engine.preview("owner",now=NOW)
    assert plan["status"]=="blocked"
    assert "USD/KRW" in plan["reason"]
    set_paused("owner",p,False,"test")
    assert engine.run("owner",now=NOW)["status"]=="blocked"
    assert p.orders("owner","default")["orders"]==[]


def test_missing_held_security_price_blocks_buy_until_valued(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path)
    make_symbol(cache,"VOO",80)
    p.trade("owner","kr-base","005930","BUY",1,"historic-kr-buy-01",
            quote("005930",20000))
    plan=engine.preview("owner",now=NOW)
    assert plan["status"]=="blocked"
    assert "005930" in plan["reason"]
    assert plan["proposed_orders"]==[]


def test_domestic_isa_routing_only_for_approved_etf(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path,us=0,etf=100,with_fx=False)
    make_symbol(cache,"360750",20000)
    plan=engine.preview("owner",now=NOW)
    assert plan["status"]=="ready"
    assert len(plan["proposed_orders"])==1
    action=plan["proposed_orders"][0]
    assert action["account"]=="isa-base"
    assert action["symbol"]=="360750"
    set_paused("owner",p,False,"test")
    assert len(engine.run("owner",now=NOW)["executed"])==1
    assert p.orders("owner","kr-base")["orders"]==[]
    assert p.orders("owner","isa-base")["orders"][0]["symbol"]=="360750"


def test_stop_loss_review_and_sell_stops_same_sleeve_reentry(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path,us=0,etf=100,with_fx=False)
    p.trade("owner","kr-base","069500","BUY",1,"oldbuy-00001",
            quote("069500",10000))
    make_symbol(cache,"069500",8800)
    make_symbol(cache,"360750",20000)
    plan=engine.preview("owner",now=NOW)
    assert any(x["kind"]=="EXIT_REVIEW" for x in plan["review"])
    exits=[x for x in plan["proposed_orders"] if x["side"]=="SELL"]
    assert exits and exits[0]["account"]=="kr-base"
    set_paused("owner",p,False,"test")
    result=engine.run("owner",now=NOW)
    assert any(x["side"]=="SELL" for x in result["executed"])
    assert not p.list("owner")["accounts"][1]["holdings"]


def test_sell_notional_above_300k_requires_approval_not_auto_fill(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path,us=0,etf=100,with_fx=False)
    p.trade("owner","kr-base","069500","BUY",40,"oldbuy-00001",
            quote("069500",10000))
    make_symbol(cache,"069500",8500)
    plan=engine.preview("owner",now=NOW)
    watching=next(x for x in plan["review"] if x["symbol"]=="069500")
    assert watching["approval_required"] is True
    assert not any(x["side"]=="SELL" and x["symbol"]=="069500"
                   for x in plan["proposed_orders"])
    set_paused("owner",p,False,"test")
    result=engine.run("owner",now=NOW)
    assert not any(x["side"]=="SELL" for x in result["executed"])
    assert p.list("owner")["accounts"][1]["holdings"]==[
        {"symbol":"069500","quantity":40}]


def test_unknown_held_security_prevents_guessing_market_exposure(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path)
    p.auto_buy_usd_with_fx("owner","TSLA",quote("TSLA",10),"1400","unknown-001")
    make_symbol(cache,"VOO",80)
    plan=engine.preview("owner",now=NOW)
    assert plan["status"]=="blocked"
    assert "미분류 보유종목" in plan["reason"]
    assert not plan["proposed_orders"]


def test_corrupt_cached_history_prevents_quant_trade_signal(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path)
    make_symbol(cache,"VOO",80)
    with cache._db() as db:
        db.execute("""UPDATE market_snapshots SET payload_json='{}'
             WHERE kind='HISTORY' AND symbol='VOO'""")
    plan=engine.preview("owner",now=NOW)
    assert plan["quant_ready"]==0
    assert plan["proposed_orders"]==[]
    set_paused("owner",p,False,"test")
    assert not engine.run("owner",now=NOW)["executed"]


def test_allocations_obey_total_daily_cap_across_multiple_sleeves(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path,us=50,etf=100)
    make_symbol(cache,"VOO",80)
    make_symbol(cache,"360750",20000)
    plan=engine.preview("owner",now=NOW)
    assert plan["status"]=="ready"
    cost=sum(float(x["notional_krw"]) for x in plan["proposed_orders"] if x["side"]=="BUY")
    assert cost<=float(plan["daily_spend_cap_krw"])+.01
    assert len(plan["proposed_orders"])==2
    set_paused("owner",p,False,"test")
    result=engine.run("owner",now=NOW)
    assert len(result["executed"])==2
    assert sum(float(x["notional_krw"]) for x in recorded_claims("owner",p)["items"])<=200000
    assert not engine.run("owner",now=NOW)["executed"]


def test_claimed_after_crash_is_not_replayed(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path)
    make_symbol(cache,"VOO",80)
    plan=engine.preview("owner",now=NOW)
    proposed=plan["proposed_orders"][0]
    import wealthy_server.stage4_portfolio as stage4
    stamp=NOW.isoformat()
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        stage4._schema(db)
        db.execute("""INSERT INTO stage4_execution_claims VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?)""",("owner",proposed["request_id"],
            plan["plan_hash"],proposed["account"],proposed["symbol"],"BUY",1,
            proposed["notional_krw"],"CLAIMED","TEST_CRASH",stamp,stamp))
        db.execute("COMMIT")
    set_paused("owner",p,False,"test")
    result=engine.run("owner",now=NOW)
    assert result["executed"]==[]
    assert result["skipped"][0]["status"]=="already_claimed"
    assert p.orders("owner","default")["orders"]==[]


def test_external_paper_emergency_pause_at_execution_boundary(tmp_path):
    ledger,p,prefs,policy,cache,engine=setup(tmp_path)
    make_symbol(cache,"VOO",80)
    set_paused("owner",p,False,"test")
    def pause_inside_quote(symbol):
        set_paused("owner",p,True,"concurrent emergency stop")
        return quote(symbol)
    engine.quote_provider=pause_inside_quote
    result=engine.run("owner",now=NOW)
    assert not result["executed"]
    assert not p.orders("owner","default")["orders"]
