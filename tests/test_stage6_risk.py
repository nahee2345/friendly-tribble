"""Stage6 acceptance: owner-scoped, no-API risk/stop and historical replay.

All references are pre-validated offline. No live prices, synthetic earnings,
broker orders or look-ahead strategy optimization.
"""
from __future__ import annotations

import json
import math
from datetime import date,datetime,timedelta,timezone

import pytest

from wealthy_server.stage1_market_store import MarketStore,validated_snapshot
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_inbox import inbox
from wealthy_server.stage6_risk import (_risk,_drawdown,_correlation,walk_forward_momentum,
    review_candidate,RiskEngine)
from wealthy_server.stage4_portfolio import PortfolioEngine
from wealthy_server.stage1_strategy_policy import StrategyPolicy,DEFAULT_WEIGHTS
from wealthy_server.stage1_discovery import PreferenceStore
from wealthy_server.stage1_collectors import kr_research_history

NOW=datetime(2026,10,19,10,0,tzinfo=timezone.utc)
CLOSE=date(2026,10,16)


def url(code,kind="HISTORY"):
    return ("https://query1.finance.yahoo.com/v8/finance/chart/"+code+
            ".KS?range=1y&interval=1d" if code.isdigit() else
            "https://stooq.com/q/d/l/?s="+code.lower()+".us&i=d&d1=20260101&d2=20261019")


def quote(code,amount):
    return {"status":"ok","tradeable":True,"symbol":code,
            "currency":"KRW" if code.isdigit() else "USD",
            "price":str(amount),"source":"independent offline delayed close",
            "as_of":CLOSE.isoformat()}


def history(code="VOO",*,size=120,shock=0.0,direction=1,amplitude=0.02):
    dates=[CLOSE-timedelta(days=i) for i in reversed(range(330))]
    dates=[day for day in dates if day.weekday()<5][-size:]
    base=100 if not code.isdigit() else 10000
    out=[]
    for i,day in enumerate(dates):
        multiplier=(1+direction*0.001*i+amplitude*math.sin(i/7))
        if shock and i>=size-5:multiplier*=1-shock
        out.append({"date":day.isoformat(),"close":str(round(base*multiplier,5))})
    return {"status":"ok","symbol":code,
            "currency":"KRW" if code.isdigit() else "USD",
            "as_of":CLOSE.isoformat(),"prices":out}


def write(cache,kind,symbol,data,*,now=NOW,provider=None,url_override=None):
    source=provider or ("FRANKFURTER" if kind=="FX" else
                        "YAHOO_UNOFFICIAL" if symbol.isdigit() else "STOOQ")
    endpoint=url_override or (("https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW")
                              if kind=="FX" else url(symbol))
    cache.save(validated_snapshot(kind,symbol,source,endpoint,data,now=now))


def prepare(tmp_path):
    path=tmp_path/"stage1-paper.sqlite3"
    ledger=Stage1Ledger(path)
    portfolios=Portfolios(path)
    initialize_paper_accounts("alice",ledger,portfolios,today=NOW.date())
    cache=MarketStore.for_portfolios(portfolios)
    write(cache,"FX","USD/KRW",{"status":"ok","executable":False,
                                 "usd_krw":"1400","as_of":CLOSE.isoformat()})
    return ledger,portfolios,cache,RiskEngine(portfolios,cache)


def mark(cache,symbol,price,*,shock=0.0,direction=1,size=120):
    write(cache,"HISTORY",symbol,history(symbol,shock=shock,direction=direction,size=size))
    write(cache,"PRICE",symbol,quote(symbol,price))


def test_risk_low_volatility_returns_are_source_backed(tmp_path):
    _,_,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100")
    report=risk.report("alice",now=NOW,candidate="VOO")
    row=report["candidate"]
    assert row["risk"]["status"]=="observed"
    assert row["risk"]["observation_sessions"]==63
    assert row["guard"]["status"]=="permitted_for_stage4_checks"
    assert row["source"]["sha256"] and row["source"]["url"].startswith("https://stooq.com/")
    assert not report["no_orders"] is False
    assert row["momentum_replay"]["status"]=="historical_replay"
    assert row["momentum_replay"]["sessions"]==30


def test_large_observed_drawdown_vetoes_new_auto_buy(tmp_path):
    ledger,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100",shock=.42)
    ref=cache.latest("HISTORY","VOO",now=NOW)
    result=review_candidate(ref,"VOO",NOW)
    assert result["risk"]["status"]=="critical"
    assert result["risk"]["max_drawdown_pct"]<=-30
    assert result["buy_guard"]["status"]=="hold"


def test_missing_or_bad_sha_history_rejects_candidate_but_not_cash(tmp_path):
    ledger,p,cache,risk=prepare(tmp_path)
    assert risk.report("alice",now=NOW,candidate="VOO")["candidate"]["guard"]["status"]=="hold"
    mark(cache,"VOO","100")
    with cache._db() as db:
        db.execute("UPDATE market_snapshots SET payload_json='{}' WHERE kind='HISTORY' AND symbol='VOO'")
    report=risk.report("alice",now=NOW,candidate="VOO")
    assert report["candidate"]["risk"] is None
    assert report["candidate"]["guard"]["status"]=="hold"
    assert not p.orders("alice","default")["orders"]


def test_30_session_signal_only_uses_prior_closes_no_lookahead():
    data=history("VOO",size=125)["prices"]
    points=[(date.fromisoformat(x["date"]),float(x["close"])) for x in data]
    # Compare differing final closes, preserving all earlier observations:
    # historical positions prior to the final close cannot change.
    bearish=[(d,v*1.35 if i==len(points)-1 else v)
             for i,(d,v) in enumerate(points)]
    normal=walk_forward_momentum(points)
    alternative=walk_forward_momentum(bearish)
    assert normal["period_start"]==alternative["period_start"]
    assert normal["sessions"]==alternative["sessions"]==30
    assert normal["switches"]==alternative["switches"]
    assert normal["invested_sessions"]==alternative["invested_sessions"]
    assert normal["buy_hold_return_pct"]!=alternative["buy_hold_return_pct"]
    assert normal["friction_is_assumed"] and normal["future_prediction"] is False


def test_rule_has_no_fitted_weights_and_no_realized_nav_claim():
    pts=[(date.fromisoformat(r["date"]),float(r["close"]))
         for r in history("AAPL",size=120)["prices"]]
    res=walk_forward_momentum(pts)
    assert res["no_fit"] is True
    assert res["is_actual_owner_portfolio_backtest"] is False
    assert res["friction_each_switch_pct"]==0.1
    assert len(res["limitations"])>=2
    assert walk_forward_momentum(pts[:60])["status"]=="unavailable"


def test_cross_currency_portfolio_refuses_historical_krw_volatility(tmp_path):
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"069500","10000")
    mark(cache,"VOO","100")
    p.trade("alice","kr-base","069500","BUY",1,"test-kr-001",quote("069500",10000))
    p.auto_buy_usd_with_fx("alice","VOO",quote("VOO",100),"1400","test-us-001")
    result=risk.report("alice",now=NOW)
    assert result["status"]=="complete"
    assert result["combined_krw_historical_risk"]["status"]=="unavailable"
    assert {x["currency"] for x in result["currency_proxies"]}=={"KRW","USD"}
    assert not result["pairwise_correlation"]
    assert result["stress"]["status"]=="hypothetical_only"
    assert Decimal(result["stress"]["estimated_loss_krw"])<0


def test_same_currency_pairwise_return_correlation(tmp_path):
    from decimal import Decimal
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100")
    mark(cache,"VTI","100")
    p.auto_buy_usd_with_fx("alice","VOO",quote("VOO",100),"1400","test-cor-001")
    p.auto_buy_usd_with_fx("alice","VTI",quote("VTI",100),"1400","test-cor-002")
    report=risk.report("alice",now=NOW)
    pairs=report["pairwise_correlation"]
    assert len(pairs)==1
    assert pairs[0]["common_sessions"]>=60
    assert pairs[0]["correlation"]>0.99
    assert pairs[0]["alert"] is True


def test_stress_is_scenario_only_and_requires_held_quote(tmp_path):
    from decimal import Decimal
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100")
    p.auto_buy_usd_with_fx("alice","VOO",quote("VOO",100),"1400","test-stress-001")
    result=risk.report("alice",now=NOW)
    assert result["stress"]["no_probability_assigned"] is True
    assert Decimal(result["stress"]["estimated_loss_krw"])==Decimal("-25424.00")
    assert Decimal(result["stress"]["exposure_krw"])==140000
    with cache._db() as db:
        db.execute("DELETE FROM market_current WHERE kind='PRICE' AND symbol='VOO'")
    unavailable=risk.report("alice",now=NOW)
    assert unavailable["status"]=="incomplete"
    assert unavailable["stress"] is None
    assert unavailable["invested_exposure_krw"] is None


def test_source_based_high_risk_alert_deduplicates_and_is_owner_scoped(tmp_path):
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100",shock=.42)
    p.auto_buy_usd_with_fx("alice","VOO",quote("VOO",100),"1400","test-notify-001")
    first=risk.publish_alerts("alice",now=NOW)
    second=risk.publish_alerts("alice",now=NOW)
    assert first["new_alerts"]>=1
    assert second["new_alerts"]==0
    notes=inbox("alice",p)["items"]
    entry=next(x for x in notes if x["event_id"].startswith("risk:quant:"))
    assert "https://stooq.com/" in entry["detail"]
    assert "SHA256" in entry["detail"]
    assert not any(x["event_id"].startswith("risk:quant:") for x in inbox("bob",p)["items"])


def test_review_does_not_write_orders_or_change_owner_portfolio(tmp_path):
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100")
    state=p.list("alice")
    report=risk.report("alice",now=NOW,candidate="VOO")
    assert report["no_orders"]
    assert p.list("alice")==state
    assert p.orders("alice","default")["orders"]==[]


def test_stale_verified_reference_fails_closed(tmp_path):
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100")
    candidate=risk.report("alice",now=NOW+timedelta(days=6),candidate="VOO")
    assert candidate["candidate"]["risk"] is None
    assert candidate["candidate"]["guard"]["status"]=="hold"


def test_incorrect_duplicate_dates_are_not_accepted_by_market_store(tmp_path):
    _,p,cache,risk=prepare(tmp_path)
    data=history("VOO")
    data["prices"][1]["date"]=data["prices"][0]["date"]
    with pytest.raises(ValueError):
        validated_snapshot("HISTORY","VOO","STOOQ",url("VOO"),data,now=NOW)


def test_index_overlap_warning_is_not_fictitious_constituent_percentage(tmp_path):
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100")
    mark(cache,"360750","10000")
    p.auto_buy_usd_with_fx("alice","VOO",quote("VOO",100),"1400","test-index-001")
    p.trade("alice","isa-base","360750","BUY",1,"test-index-002",
            quote("360750",10000))
    result=risk.report("alice",now=NOW)
    assert len(result["possible_etf_index_overlap"])==1
    assert result["possible_etf_index_overlap"][0]["status"]=="possible_overlap_not_verified"
    assert "overlap_pct" not in result["possible_etf_index_overlap"][0]


def test_full_eight_strategy_backtest_is_explicitly_unavailable(tmp_path):
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100")
    results=risk.report("alice",now=NOW,candidate="VOO")["validation_of_eight_strategies"]
    assert len(results)==8
    assert "원자료 부족" in results["value"]
    assert "30일" in results["momentum"]


class Response:
    def __init__(self,payload):self.payload=json.dumps(payload).encode()
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n):return self.payload[:n]


def test_korean_one_year_collector_has_sufficient_65_daily_closes():
    rows=history("069500",size=145)["prices"]
    timestamps=[int(datetime.fromisoformat(x["date"]).replace(tzinfo=timezone.utc).timestamp())
                for x in rows]
    chart={"chart":{"result":[{"meta":{"symbol":"069500.KS","currency":"KRW"},
        "timestamp":timestamps,"indicators":{"quote":[{"close":[float(x["close"]) for x in rows]}]}}]}}
    captured=[]
    def fake(request,timeout):
        captured.append(request.full_url)
        return Response(chart)
    result=kr_research_history("KR","069500",now=NOW,opener=fake)
    assert result["status"]=="ok"
    assert len(result["prices"])>=65
    assert "range=1y" in result["source_url"]
    assert len(captured)==1
    norm=validated_snapshot("HISTORY","069500","YAHOO_UNOFFICIAL",
                            result["source_url"],result,now=NOW)
    assert len(norm["payload"]["prices"])>=65


def test_korean_history_mismatched_meta_fails_without_fabricating():
    rows=history("069500",size=130)["prices"]
    stamp=[int(datetime.fromisoformat(x["date"]).replace(tzinfo=timezone.utc).timestamp())
           for x in rows]
    chart={"chart":{"result":[{"meta":{"symbol":"000660.KS","currency":"USD"},
            "timestamp":stamp,"indicators":{"quote":[{"close":[100.0]*len(rows)}]}}]}}
    got=kr_research_history("KR","069500",now=NOW,opener=lambda *_args,**_kw:Response(chart))
    assert got["status"]=="unavailable"


def test_risk_engine_receives_no_broker_or_network_dependency(tmp_path,monkeypatch):
    import urllib.request
    _,p,cache,risk=prepare(tmp_path)
    mark(cache,"VOO","100")
    monkeypatch.setattr(urllib.request,"urlopen",
        lambda *_a,**_kw:(_ for _ in ()).throw(AssertionError("network")))
    assert risk.report("alice",now=NOW,candidate="VOO")["candidate"]["risk"] is not None


def test_stage4_candidate_risk_veto_is_enforced_when_quant_signal_exists(tmp_path):
    ledger,p,cache,risk=prepare(tmp_path)
    prefs=PreferenceStore(p.path)
    prefs.save("alice",100,100)
    policy=StrategyPolicy(p)
    weights={key:(100 if key=="high_dividend" else 0) for key in DEFAULT_WEIGHTS}
    policy.save("alice",weights)
    mark(cache,"VOO","100",shock=.42)
    # Historical dividend payout proxy is 7%: an independent high score.
    exdays=[date(2026,2,14),date(2026,5,15),date(2026,8,14),date(2026,10,2)]
    div={"status":"ok","symbol":"VOO","currency":"USD",
         "events":[{"date":d.isoformat(),"amount_per_share":"1.75"} for d in exdays]}
    write(cache,"DIVIDENDS","VOO",div,provider="YAHOO_UNOFFICIAL",
          url_override="https://query1.finance.yahoo.com/v8/finance/chart/VOO?range=5y&interval=1d&events=div")
    engine=PortfolioEngine(p,prefs,cache,policy=policy,
        quote_provider=lambda code:quote(code,100),
        fx_provider=lambda:{"status":"ok","executable":False,
            "as_of":CLOSE.isoformat(),"usd_krw":"1400"})
    plan=engine.preview("alice",now=NOW)
    assert plan["quant_ready"]>=1
    assert not any(x["side"]=="BUY" and x["symbol"]=="VOO"
                   for x in plan["proposed_orders"])
    assert risk.report("alice",now=NOW,candidate="VOO")["candidate"]["guard"]["status"]=="hold"
