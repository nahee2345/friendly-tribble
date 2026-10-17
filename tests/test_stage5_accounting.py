"""Stage5: offline P&L, no retroactive FX, unpaid dividends, cashflow-true returns.

No brokerage API or public web provider used by these fixtures.
"""
from __future__ import annotations

from datetime import datetime, timedelta, date, timezone
from decimal import Decimal

import pytest

from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_market_store import MarketStore,validated_snapshot
from wealthy_server.stage5_accounting import AccountingEngine,xirr,twr_from_verified_snapshots
from wealthy_server.stage5_tax import (
    domestic_isa_scenario,foreign_ordinary_scenario,instrument_class)
from wealthy_server.stage1_controls import set_paused

START=datetime.now(timezone.utc)
NOW=(START+timedelta(days=20)).replace(hour=9,minute=0,second=0,microsecond=0)
URL_FX="https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW"


def price_url(symbol):
    if symbol.isdigit():
        return "https://query1.finance.yahoo.com/v8/finance/chart/"+symbol+".KS?range=10d&interval=1d"
    return "https://stooq.com/q/d/l/?s="+symbol.lower()+".us&i=d&d1=20260201&d2=20261019"


def dividend_url(symbol):
    return "https://query1.finance.yahoo.com/v8/finance/chart/"+symbol+(
        ".KS" if symbol.isdigit() else "")+"?range=5y&interval=1d&events=div"


def quote(symbol,amount):
    return {"symbol":symbol,"currency":"KRW" if symbol.isdigit() else "USD",
            "status":"ok","tradeable":True,"price":str(amount),
            "as_of":(START.date()).isoformat(),"source":"verified independent test close"}


def snapshot(cache,kind,symbol,data,*,at=NOW):
    provider=("FRANKFURTER" if kind=="FX" else
              "YAHOO_UNOFFICIAL" if kind=="DIVIDENDS" else
              "YAHOO_UNOFFICIAL" if symbol.isdigit() else "STOOQ")
    url=URL_FX if kind=="FX" else (dividend_url(symbol) if kind=="DIVIDENDS"
         else price_url(symbol))
    cache.save(validated_snapshot(kind,symbol,provider,url,data,now=at))


def environment(tmp_path):
    path=tmp_path/"stage1-paper.sqlite3"
    ledger=Stage1Ledger(path)
    p=Portfolios(path)
    initialize_paper_accounts("alice",ledger,p,today=START.date())
    cache=MarketStore.for_portfolios(p)
    engine=AccountingEngine(p,cache)
    return ledger,p,cache,engine


def market(cache,now=NOW,*,fx="1400",prices=None):
    snapshot(cache,"FX","USD/KRW",{"status":"ok","executable":False,
            "usd_krw":str(fx),"as_of":(now-timedelta(days=1)).date().isoformat()},at=now)
    for code,value in (prices or {}).items():
        snapshot(cache,"PRICE",code,
                 {"status":"ok","tradeable":True,"symbol":code,
                  "currency":"KRW" if code.isdigit() else "USD",
                  "price":str(value),"as_of":(now-timedelta(days=1)).date().isoformat()},at=now)


def test_initial_cash_only_gross_nav_and_unknown_net_costs(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    report=engine.report("alice",now=NOW)
    assert report["valuation_complete"]
    assert Decimal(report["market_valuation_krw"])==4_000_000
    assert report["gross_change_after_contributions_krw"]=="0"
    assert report["fee_analysis"]["actual_fee_amount_complete"] is False
    assert report["fee_tax_actual_status"]=="UNKNOWN_NOT_NETTED"
    assert report["dividend_cash_posted_krw"] is None
    assert report["never_posts_cash"]
    assert report["xirr"]["status"]=="ok"
    assert abs(report["xirr"]["pct"])<1e-5


def test_usd_purchase_fx_split_exact_and_reconcile_original_cash(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    p.auto_buy_usd_with_fx("alice","AAPL",quote("AAPL",100),"1400","usd-purchase-0001")
    market(cache,fx="1500",prices={"AAPL":"110"})
    before=p.list("alice")["accounts"]
    report=engine.report("alice",now=NOW)
    assert report["valuation_complete"]
    pos=next(x for x in report["positions"] if x["symbol"]=="AAPL")
    assert pos["cost_basis_native"]=="100"
    assert pos["unrealized_native"]=="10"
    assert pos["market_price_effect_krw"]=="14000"
    assert pos["fx_effect_krw"]=="11000"
    assert pos["unrealized_krw_total"]=="25000"
    assert Decimal(pos["value_krw"])==165000
    assert report["native_gross_pnl"]["USD"]["unrealized"]=="10"
    # USD cash 0 after atomic FX; KRW cash 4m-140000.
    assert Decimal(report["market_valuation_krw"])==4_025_000
    assert p.list("alice")["accounts"]==before


def test_usd_manual_purchases_without_fx_are_never_revalued_at_today_buy_rate(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    ledger.exchange("alice",140000,"1400","manual-fx-0001")
    p.trade("alice","default","AAPL","BUY",1,"manual-buy-0001",quote("AAPL",100))
    market(cache,fx="1500",prices={"AAPL":"110"})
    report=engine.report("alice",now=NOW)
    pos=report["positions"][0]
    assert pos["unrealized_native"]=="10"
    assert pos["fx_split_status"]=="missing_acquisition_fx"
    assert pos["fx_effect_krw"] is None
    assert pos["market_price_effect_krw"] is None
    assert pos["unrealized_krw_total"] is None
    assert report["valuation_complete"] # total value still valued at current FX


def test_partial_exit_moving_average_realized_native(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    p.trade("alice","kr-base","069500","BUY",4,"kr-buy-00001",quote("069500",10000))
    p.trade("alice","kr-base","069500","BUY",2,"kr-buy-00002",quote("069500",12000))
    p.trade("alice","kr-base","069500","SELL",3,"kr-sell-0001",quote("069500",14000))
    market(cache,prices={"069500":"15000"})
    data=engine.report("alice",now=NOW)
    x=next(v for v in data["positions"] if v["symbol"]=="069500")
    assert x["quantity"]==3
    assert Decimal(x["cost_basis_native"])==32000
    assert Decimal(x["unrealized_native"])==13000
    assert Decimal(data["native_gross_pnl"]["KRW"]["realized"])==10000
    assert data["fee_analysis"]["illustrative_trade_fees_krw"] is not None


def test_realized_usd_sale_missing_fx_source_does_not_fabricate_conversion(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    ledger.exchange("alice",140000,"1400","manual-fx-0002")
    p.trade("alice","default","AAPL","BUY",1,"manual-buy-0002",quote("AAPL",100))
    p.trade("alice","default","AAPL","SELL",1,"manual-sell-0002",quote("AAPL",120))
    market(cache,fx="1500",prices={"AAPL":"130"})
    data=engine.report("alice",now=NOW)
    assert data["native_gross_pnl"]["USD"]["realized"]=="20"
    item=next(x for x in data["realizations"] if x["symbol"]=="AAPL")
    assert item["realized_price_effect_krw"] is None
    assert item["realized_fx_effect_krw"] is None
    assert data["valuation_complete"]
    assert not data["positions"]


def test_realized_usd_fx_split_from_both_proven_acquisition_and_exit_rates(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    p.auto_buy_usd_with_fx("alice","AAPL",quote("AAPL",100),"1400","autobuy-0001")
    p.trade("alice","default","AAPL","SELL",1,"sell-with-fx-0001",quote("AAPL",120))
    with p._db() as db:
        db.execute("UPDATE stage1_trade_metadata SET foreign_exchange_rate='1450' "
                   "WHERE owner=? AND portfolio=? AND order_key=?",
                   ("alice","default","sell-with-fx-0001"))
    market(cache,fx="1500")
    data=engine.report("alice",now=NOW)
    gain=next(x for x in data["realizations"] if x["symbol"]=="AAPL")
    assert gain["realized_price_effect_krw"]=="28000"
    assert gain["realized_fx_effect_krw"]=="6000"
    assert gain["split_krw_confirmed"]
    assert data["native_gross_pnl"]["USD"]["realized"]=="20"


def test_missing_held_quote_makes_nav_and_xirr_unavailable(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    p.trade("alice","kr-base","069500","BUY",1,"first-0001",quote("069500",10000))
    market(cache)
    data=engine.report("alice",now=NOW)
    assert not data["valuation_complete"]
    assert data["market_valuation_krw"] is None
    assert data["xirr"]["status"]=="unavailable"
    assert data["missing"]
    assert engine.capture_daily_nav("alice",now=NOW)["status"]=="incomplete"


def test_unconfirmed_dividend_exdate_belongs_to_pre_exdate_holder_only(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    p.auto_buy_usd_with_fx("alice","AAPL",quote("AAPL",100),"1400","div-buy-00001")
    market(cache,prices={"AAPL":"100"})
    ex=START.date()+timedelta(days=4)
    snapshot(cache,"DIVIDENDS","AAPL",{"status":"ok","symbol":"AAPL",
        "currency":"USD","events":[{"date":ex.isoformat(),"amount_per_share":"0.25"}]})
    before=p.list("alice")["accounts"]
    result=engine.report("alice",now=NOW)
    assert len(result["potential_dividends"])==1
    indicator=result["potential_dividends"][0]
    assert indicator["potential_gross"]=="0.25"
    assert indicator["status"]=="UNCONFIRMED_PAYMENT"
    assert indicator["cash_booked"] is False
    assert result["dividend_cash_posted_krw"] is None
    assert p.list("alice")["accounts"]==before


def test_dividend_without_pre_exdate_holdings_is_not_counted(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    p.auto_buy_usd_with_fx("alice","AAPL",quote("AAPL",100),"1400","later-0001")
    market(cache,prices={"AAPL":"100"})
    ex=START.date()-timedelta(days=1)
    snapshot(cache,"DIVIDENDS","AAPL",{"status":"ok","symbol":"AAPL",
        "currency":"USD","events":[{"date":ex.isoformat(),"amount_per_share":"0.25"}]})
    result=engine.report("alice",now=NOW)
    assert result["potential_dividends"]==[]


def test_isa_scenario_domestic_foreign_listed_exclusion_and_allowance():
    a=domestic_isa_scenario("3000000")
    assert a["general_scenario_tax_krw"]=="462000"
    assert a["isa_scenario_tax_krw"]=="99000"
    assert a["potential_isa_saving_krw"]=="363000"
    assert a["not_actual_tax"] and a["deduction_not_verified"]
    b=domestic_isa_scenario("3000000",allowance_type="preferential")
    assert b["isa_scenario_tax_krw"]=="0"
    assert instrument_class("069500","GENERAL_DOMESTIC")=="DOMESTIC_EQUITY_ETF"
    assert instrument_class("VOO","GENERAL_OVERSEAS")=="FOREIGN_LISTED"
    us=foreign_ordinary_scenario("4500000")
    assert us["general_scenario_tax_krw"]=="440000"
    assert us["isa_comparison"] is None


def test_isa_comparison_does_not_misclassify_domestic_equity_etf_gain(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    p.trade("alice","kr-base","069500","BUY",1,"isacomp-0001",quote("069500",10000))
    p.trade("alice","isa-base","360750","BUY",1,"isacomp-0002",quote("360750",10000))
    market(cache,prices={"069500":"20000","360750":"30000"})
    report=engine.report("alice",now=NOW)
    scenario=report["tax_illustrations"]["domestic_isa"]
    assert scenario["taxable_gain_proxy_krw"]=="20000"
    assert "360750" in scenario["included_symbols"]
    assert "069500" not in scenario["included_symbols"]
    assert scenario["general_scenario_tax_krw"]=="3080"
    assert scenario["isa_scenario_tax_krw"]=="0"
    assert report["never_posts_cash"]


def test_twr_only_links_verified_flow_free_nav_intervals():
    t0=NOW.isoformat();t1=(NOW+timedelta(days=1)).isoformat()
    a=[{"observed_utc":t0,"nav_krw":"4000000"},
       {"observed_utc":t1,"nav_krw":"4040000"}]
    out=twr_from_verified_snapshots(a,[])
    assert out["status"]=="ok"
    assert out["pct"]==1.0
    b=twr_from_verified_snapshots(a,[("deposit-two",
         (NOW+timedelta(hours=3)).isoformat(),100000)])
    assert b["status"]=="incomplete"
    assert b["pct"] is None
    assert b["reason"]=="EXTERNAL_FLOW_WITHOUT_BEFORE_AFTER_NAV"


def test_xirr_protects_same_day_annualization_and_deposit_timing():
    test_time=NOW
    same=xirr([(4000000,(test_time-timedelta(hours=12)).isoformat())],
              "4000000",test_time)
    assert same["reason"]=="LESS_THAN_SEVEN_DAYS"
    longer=xirr([(4000000,(test_time-timedelta(days=365)).isoformat())],
                "4400000",test_time)
    assert longer["status"]=="ok"
    assert 9.9<longer["pct"]<10.1
    assert xirr([(4000000,(test_time+timedelta(days=1)).isoformat())],
                "4000000",test_time)["reason"]=="FUTURE_DATED_CASHFLOW"


def test_daily_nav_snapshot_idempotent_owner_scoped_and_no_backfill(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    first=engine.capture_daily_nav("alice",now=NOW)
    assert first["status"]=="saved"
    assert not engine.capture_daily_nav("alice",now=NOW)["saved"]
    second=engine.capture_daily_nav("alice",now=NOW+timedelta(days=1))
    assert second["status"]=="saved"
    result=engine.report("alice",now=NOW+timedelta(days=1))
    assert result["twr"]["status"]=="ok"
    with p._db() as db:
        rows=db.execute("SELECT COUNT(*) FROM stage5_nav_snapshots WHERE owner='alice'").fetchone()[0]
    assert rows==2
    assert engine.report("nobody",now=NOW)["positions"]==[]
    # No claimed historical snapshot is written at the times of prior
    # monthly deposits or backtest history.


def test_recorded_paper_fee_does_not_silently_reduce_cash(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    p.trade("alice","kr-base","069500","BUY",1,"fee-00001",quote("069500",10000))
    with p._db() as db:
        db.execute("UPDATE stage1_trade_metadata SET fee_amount='20', "
                   "tax_amount='5',costs_currency='KRW' "
                   "WHERE owner='alice' AND portfolio='kr-base' AND order_key='fee-00001'")
    market(cache,prices={"069500":"10000"})
    before=p.list("alice")["accounts"]
    report=engine.report("alice",now=NOW)
    assert report["fee_analysis"]["fee_records_and_history"]["known_reported"]==1
    assert report["fee_tax_actual_status"]=="UNKNOWN_NOT_NETTED"
    assert p.list("alice")["accounts"]==before


def test_invalid_simulated_sell_inventory_fails_closed(tmp_path):
    ledger,p,cache,engine=environment(tmp_path)
    with p._db() as db:
        db.execute("""INSERT INTO virtual_orders
            (owner,portfolio,request_id,symbol,side,quantity,price,currency,
             source,as_of,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("alice","kr-base","corrupt-001","069500","SELL",2,"10000","KRW",
             "fixture",START.date().isoformat(),START.isoformat()))
    with pytest.raises(ValueError,match="negative historic inventory"):
        engine.report("alice",now=NOW)
