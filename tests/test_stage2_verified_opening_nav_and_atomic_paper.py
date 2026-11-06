"""P0 NAV provenance and unified PAPER execution on *ephemeral* SQLite.

The fixture simulates a previously fetched, allowlisted market reference;
it is NOT evidence that live public quote/FX providers worked on the test day.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import sqlite3

import pytest

from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_market_store import MarketStore, validated_snapshot
from wealthy_server.stage1_controls import set_paused, request_approval, decide_approval
from wealthy_server.stage1_nav_proof import (
    establish_verified_opening_nav, assert_verified_opening_nav
)
from wealthy_server.stage1_verified_execution import execute_verified_paper
from wealthy_server.stage1_strategy_policy import StrategyPolicy
from wealthy_server.stage1_discovery import PreferenceStore

DAY="2026-11-04"
START="2026-11-04T14:30:00+00:00"  # NY 09:30 after DST ends
NOW=datetime(2026,11,4,14,35,tzinfo=timezone.utc)
BEFORE=datetime(2026,11,3,20,0,tzinfo=timezone.utc)
OWNER="p0-owner"
FX_URL="https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW"
US_URL="https://stooq.com/q/d/l/?s=spym.us&i=d&d1=20260101&d2=20261104"
KR_URL="https://query1.finance.yahoo.com/v8/finance/chart/069500.KS?range=10d&interval=1d"
US={"symbol":"SPYM","currency":"USD","price":"100","status":"ok",
    "tradeable":True,"as_of":"2026-11-03","source":"validated isolated source",
    "source_url":US_URL}
KR={"symbol":"069500","currency":"KRW","price":"10000","status":"ok",
    "tradeable":True,"as_of":"2026-11-03","source":"validated isolated source",
    "source_url":KR_URL}
FX={"status":"ok","executable":False,"usd_krw":"1400","as_of":"2026-11-03",
    "source":"isolated reference","source_url":FX_URL}


def sample_store(tmp_path, *, bad_fx=False, held=0):
    p=Portfolios(tmp_path/"paper-owner.sqlite3")
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO portfolios VALUES(?,?,?,?,?)",
                   (OWNER,"default","US PAPER","GENERAL_OVERSEAS",BEFORE.isoformat()))
        db.execute("INSERT INTO portfolios VALUES(?,?,?,?,?)",
                   (OWNER,"kr-base","KR PAPER","GENERAL_DOMESTIC",BEFORE.isoformat()))
        db.execute("INSERT INTO stage1_events VALUES(?,?,?,?,?,?,?)",
                   (OWNER,"initial","deposit",4000000,"0",None,BEFORE.isoformat()))
        db.execute("INSERT INTO portfolio_transfers VALUES(?,?,?,?,?,?)",
                   (OWNER,"initial-kr-transfer","default","kr-base",1200000,BEFORE.isoformat()))
        if held:
            # A historical holding financed by the transferred cash.
            db.execute("INSERT INTO virtual_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (OWNER,"kr-base","prior-kr-holding","069500","BUY",held,"10000","KRW",
                 "prior-day-fixture","2026-11-03",BEFORE.isoformat()))
        db.execute("COMMIT")
    set_paused(OWNER,p,False,"synthetic fixture only")
    cache=MarketStore.for_portfolios(p)
    for kind,sym,provider,url,data in (
        ("FX","USD/KRW","FRANKFURTER",FX_URL,
         {**FX,"usd_krw":"0" if bad_fx else "1400"}),
        ("PRICE","069500","YAHOO_UNOFFICIAL",KR_URL,KR)):
        if kind=="PRICE" and not held:continue
        if bad_fx and kind=="FX":continue
        cache.save(validated_snapshot(kind,sym,provider,url,data,now=BEFORE))
    return p,cache


def ready(tmp_path,*,held=0):
    p,cache=sample_store(tmp_path,held=held)
    proof=establish_verified_opening_nav(p,cache,OWNER,DAY,now=NOW)
    assert proof["nav_krw"]=="4000000.00"
    assert proof["cap_krw"]=="800000.0000"
    assert proof["scope"]==["default","kr-base"]
    return p,cache,proof


def test_nav_proof_is_immutable_and_replayable_after_trades(tmp_path):
    p,_,proof=ready(tmp_path)
    filled=execute_verified_paper(p,owner=OWNER,account="default",symbol="SPYM",
        side="BUY",quantity=1,request_id="fxbuy-p0-0001",quote=US,
        session_date=DAY,fx=FX,auto_fx=True,now=NOW)
    assert filled["status"]=="executed"
    with p._db() as db:
        assert assert_verified_opening_nav(db,OWNER,DAY,NOW)["proof_sha256"]==proof["proof_sha256"]
        assert db.execute("""SELECT COUNT(*) FROM stage1_turnover_claims
                              WHERE owner=? AND state='FILLED'""",(OWNER,)).fetchone()[0]==1
        assert db.execute("""SELECT COUNT(*) FROM stage1_events
                 WHERE owner=? AND kind='virtual_fx'""",(OWNER,)).fetchone()[0]==1
        assert db.execute("""SELECT COUNT(*) FROM stage1_trade_metadata
                    WHERE owner=?""",(OWNER,)).fetchone()[0]==1
    again=execute_verified_paper(p,owner=OWNER,account="default",symbol="SPYM",
        side="BUY",quantity=1,request_id="fxbuy-p0-0001",quote=US,
        session_date=DAY,fx=FX,auto_fx=True,now=NOW)
    assert again["status"]=="already_executed"
    with p._db() as db:
        assert db.execute("SELECT nav_krw FROM stage1_verified_opening_nav WHERE owner=?",(OWNER,)).fetchone()[0]=="4000000.00"


def test_nav_requires_valid_preopening_reference_and_rejects_revisions(tmp_path):
    p,cache=sample_store(tmp_path,bad_fx=True)
    with pytest.raises(ValueError):
        establish_verified_opening_nav(p,cache,OWNER,DAY,now=NOW)
    with p._db() as db:
        assert db.execute("""SELECT COUNT(*) FROM sqlite_master WHERE
              name='stage1_verified_opening_nav'""").fetchone()[0]==0
    other=tmp_path/"other"
    other.mkdir()
    p,cache,proof=ready(other)
    with p._db() as db:
        db.execute("UPDATE stage1_events SET amount_krw=4000001 WHERE owner=?",(OWNER,))
    with p._db() as db:
        with pytest.raises(ValueError,match="CORRUPT_OR_CHANGED_OPENING_NAV_PROOF"):
            assert_verified_opening_nav(db,OWNER,DAY,NOW)


def test_post_open_financial_event_blocks_backdated_nav(tmp_path):
    p,cache=sample_store(tmp_path)
    with p._db() as db:
        db.execute("INSERT INTO stage1_events VALUES(?,?,?,?,?,?,?)",
            (OWNER,"post-open-deposit","deposit",10,"0",None,NOW.isoformat()))
    with pytest.raises(ValueError,match="POST_OPEN_LEDGER"):
        establish_verified_opening_nav(p,cache,OWNER,DAY,now=NOW)


@pytest.mark.parametrize("at",["reserve","fx","fill","mark_filled"])
def test_failed_order_stage_rolls_back_claim_cash_fx_and_receipt(tmp_path,at):
    p,_,_=ready(tmp_path)
    with pytest.raises(RuntimeError,match="INJECTED"):
        execute_verified_paper(p,owner=OWNER,account="default",symbol="SPYM",
            side="BUY",quantity=1,request_id="errorinject-0001",quote=US,
            session_date=DAY,fx=FX,auto_fx=True,now=NOW,fail_after=at)
    with p._db() as db:
        for table in ("virtual_orders","stage1_turnover_claims","stage1_trade_metadata"):
            assert db.execute("SELECT COUNT(*) FROM "+table+
                              " WHERE owner=?",(OWNER,)).fetchone()[0]==0
        assert db.execute("""SELECT COUNT(*) FROM stage1_events
                     WHERE owner=? AND kind='virtual_fx'""",(OWNER,)).fetchone()[0]==0


def test_approval_exact_boundary_and_owner_20pct_cross_paths(tmp_path):
    p,_,_=ready(tmp_path)
    # 300,000 KRW must be approved; 299,999 would not require this row.
    with pytest.raises(ValueError,match="APPROVAL_REQUIRED"):
        execute_verified_paper(p,owner=OWNER,account="kr-base",symbol="069500",
            side="BUY",quantity=30,request_id="manual-300k-001",quote=KR,
            session_date=DAY,now=NOW)
    request_approval(OWNER,p,request_id="manual-300k-001",
        account="kr-base",symbol="069500",side="BUY",quantity=30,
        amount_krw=300000,rationale="synthetic exact threshold")
    decide_approval(OWNER,p,"manual-300k-001",True)
    assert execute_verified_paper(p,owner=OWNER,account="kr-base",
        symbol="069500",side="BUY",quantity=30,
        request_id="manual-300k-001",quote=KR,session_date=DAY,now=NOW)["status"]=="executed"
    assert execute_verified_paper(p,owner=OWNER,account="default",
        symbol="SPYM",side="BUY",quantity=1,request_id="fxbuy-140k-002",
        quote=US,fx=FX,auto_fx=True,session_date=DAY,now=NOW)["status"]=="executed"
    # 36 shares would put owner BUY gross at 800k, exact cap.
    request_approval(OWNER,p,request_id="manual-360k-003",
        account="kr-base",symbol="069500",side="BUY",quantity=36,
        amount_krw=360000,rationale="synthetic cap boundary")
    decide_approval(OWNER,p,"manual-360k-003",True)
    assert execute_verified_paper(p,owner=OWNER,account="kr-base",symbol="069500",
        side="BUY",quantity=36,request_id="manual-360k-003",
        quote=KR,session_date=DAY,now=NOW)["status"]=="executed"
    with pytest.raises(ValueError,match="GROSS_TURNOVER_CAP_EXCEEDED"):
        execute_verified_paper(p,owner=OWNER,account="kr-base",symbol="069500",
            side="BUY",quantity=1,request_id="one-more-10k",
            quote=KR,session_date=DAY,now=NOW)
    with p._db() as db:
        assert db.execute("""SELECT SUM(CAST(gross_krw AS REAL)) FROM
                   stage1_turnover_claims WHERE owner=?""",(OWNER,)).fetchone()[0]==800000
        assert db.execute("""SELECT COUNT(*) FROM stage1_turnover_claims
                   WHERE owner=?""",(OWNER,)).fetchone()[0]==3


def test_global_request_identity_conflicts_across_accounts(tmp_path):
    p,_,_=ready(tmp_path)
    assert execute_verified_paper(p,owner=OWNER,account="kr-base",symbol="069500",
        side="BUY",quantity=1,request_id="global-replay-001",
        quote=KR,session_date=DAY,now=NOW)["status"]=="executed"
    with pytest.raises(ValueError,match="CONFLICTING_OWNER_REQUEST_ID"):
        execute_verified_paper(p,owner=OWNER,account="default",symbol="SPYM",
            side="BUY",quantity=1,request_id="global-replay-001",
            quote=US,fx=FX,auto_fx=True,session_date=DAY,now=NOW)


def test_stage4_conservative_5pct_is_stricter_than_20pct(tmp_path):
    p,_,_=ready(tmp_path)
    from wealthy_server.stage1_strategy_policy import DEFAULT_WEIGHTS
    prefs=PreferenceStore(p.path)
    observed_prefs=prefs.get(OWNER)
    policy=StrategyPolicy(p)
    expected_weights=policy.get(OWNER)["weights"]
    plan={"plan_hash":"a"*64,"daily_spend_cap_krw":"200000",
          "portfolio_value_krw":"4000000",
          "reserve_percent":"10","kind":"ETF",
          "policy_prefs":observed_prefs,"policy_weights":expected_weights}
    # A Stage4 order above 200k is blocked even when 20% 800k is free.
    with pytest.raises(ValueError,match="STAGE4_5_PERCENT_CAP_EXCEEDED"):
        execute_verified_paper(p,owner=OWNER,account="default",symbol="SPYM",
            side="BUY",quantity=1,request_id="stage4-too-big-001",
            quote={**US,"price":"160"},
            fx=FX,auto_fx=True,session_date=DAY,now=NOW,stage4_plan=plan)
    with p._db() as db:
        assert db.execute("""SELECT COUNT(*) FROM stage1_turnover_claims""").fetchone()[0]==0


def test_runtime_portfolios_reject_legacy_unreserved_bypass(tmp_path):
    p,_,_=ready(tmp_path)
    guarded=Portfolios(p.path,require_verified_p0=True)
    with pytest.raises(ValueError,match="USE_VERIFIED_ATOMIC_PAPER_ENGINE"):
        guarded.trade(OWNER,"kr-base","069500","BUY",1,"legacy-test-0001",KR)
    with pytest.raises(ValueError,match="USE_VERIFIED_ATOMIC_PAPER_ENGINE"):
        guarded.auto_buy_usd_with_fx(OWNER,"SPYM",US,"1400","legacy-fx-0001")
    assert execute_verified_paper(guarded,owner=OWNER,account="default",
        symbol="SPYM",side="BUY",quantity=1,request_id="allowed-p0-0001",
        quote=US,fx=FX,auto_fx=True,session_date=DAY,now=NOW)["status"]=="executed"


def test_stage4_dispatches_through_owner_claim_and_receipt(tmp_path,monkeypatch):
    p,cache,_=ready(tmp_path)
    from wealthy_server.stage1_strategy_policy import DEFAULT_WEIGHTS
    from wealthy_server.stage4_portfolio import PortfolioEngine
    policy=StrategyPolicy(p)
    prefs=PreferenceStore(p.path)
    policy_weights=policy.get(OWNER)["weights"]
    policy_prefs=prefs.get(OWNER)
    plan={
        "status":"ready","reason":"","trade_enabled":True,
        "proposed_orders":[{
            "request_id":"s4:20261104:p0-a","symbol":"SPYM","side":"BUY",
            "quantity":1,"account":"default","kind":"ETF",
            "planned_price":"100","notional_krw":"140000",
            "source_hash":"p"*64,"risk_history_hash":"h"*64}],
        "plan_hash":"a"*64,"daily_spend_cap_krw":"200000",
        "portfolio_value_krw":"4000000","reserve_percent":"10",
        "policy_prefs":policy_prefs,"policy_weights":policy_weights,
        "review":[]}
    class MarketEvidence:
        def latest_many(self,pairs,*,now=None):
            values={}
            for pair in pairs:
                if pair==("PRICE","SPYM"):
                    values[pair]={"sha256":"p"*64}
                elif pair==("HISTORY","SPYM"):
                    values[pair]={"sha256":"h"*64}
                elif pair==("FX","USD/KRW"):
                    values[pair]={
                        "status":"stale","kind":"FX","symbol":"USD/KRW",
                        "provider":"FRANKFURTER","source_url":FX_URL,
                        "last_verified_utc":BEFORE.isoformat(),
                        "as_of":"2026-11-03","sha256":"f"*64,
                        "tradeable":False,"use_for_paper_execution":False,
                        "data":{"rate":"1400"}}
            return values
    engine=PortfolioEngine(p,prefs,MarketEvidence(),policy=policy,
        quote_provider=lambda symbol:US,fx_provider=lambda:FX)
    monkeypatch.setattr(engine,"preview",lambda owner,now=None:plan)
    monkeypatch.setattr("wealthy_server.p0_paper_execution_readiness.paper_execution_readiness",
                        lambda:{"verified":True,"session_date":DAY})
    monkeypatch.setattr("wealthy_server.stage7_release.audit_paper_ledger",
                        lambda *args,**kwargs:{"blocking_count":0})
    monkeypatch.setattr("wealthy_server.stage4_p0_verified.review_candidate",
                        lambda *args,**kwargs:{"buy_guard":{"status":"permitted_for_stage4_checks"}})
    outcome=engine.run(OWNER,now=NOW)
    assert len(outcome["executed"])==1,outcome
    assert outcome["executed"][0]["symbol"]=="SPYM"
    with p._db() as db:
        assert db.execute("""SELECT COUNT(*) FROM stage1_turnover_claims
                              WHERE owner=? AND state='FILLED'""",(OWNER,)).fetchone()[0]==1
        assert db.execute("""SELECT status FROM stage4_execution_claims
                              WHERE owner=?""",(OWNER,)).fetchone()[0]=="EXECUTED"
        assert db.execute("""SELECT COUNT(*) FROM virtual_orders
                              WHERE owner=?""",(OWNER,)).fetchone()[0]==1
    repeat=engine.run(OWNER,now=NOW)
    assert not repeat["executed"]
    assert len(p.orders(OWNER,"default")["orders"])==1


def _parallel_order(location,number):
    p=Portfolios(location,require_verified_p0=True)
    try:
        out=execute_verified_paper(
            p,owner=OWNER,account="kr-base",symbol="069500",
            side="BUY",quantity=20,request_id=f"multios-{number:04d}",
            quote=KR,session_date=DAY,now=NOW)
        return out["status"]
    except (ValueError,sqlite3.OperationalError) as exc:
        return str(exc)


def test_two_or_more_os_processes_share_owner_800k_limit(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    p,_,_=ready(tmp_path)
    with ProcessPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(_parallel_order,[str(p.path)]*8,range(8)))
    assert results.count("executed")==4,results
    assert all(x=="executed" or
               "GROSS_TURNOVER_CAP_EXCEEDED" in x for x in results),results
    with p._db() as db:
        claims=db.execute("""SELECT gross_krw,state FROM stage1_turnover_claims
                            WHERE owner=?""",(OWNER,)).fetchall()
        assert len(claims)==4
        assert all(x[0]=="200000" and x[1]=="FILLED" for x in claims)
        assert db.execute("""SELECT COUNT(*) FROM virtual_orders
                             WHERE owner=?""",(OWNER,)).fetchone()[0]==4


def test_unknown_reservation_is_not_released_or_replayed(tmp_path):
    from wealthy_server.stage1_turnover_reservations import reserve,mark_unknown
    p,_,_=ready(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        reserve(db,owner=OWNER,session_date=DAY,
                request_id="unknown-p0-001",account="default",
                symbol="SPYM",side="SELL",quantity=1,gross_krw="750000",
                now_utc=NOW.isoformat())
        mark_unknown(db,OWNER,"unknown-p0-001",NOW.isoformat())
        db.execute("COMMIT")
    with pytest.raises(ValueError,match="GROSS_TURNOVER_CAP_EXCEEDED"):
        execute_verified_paper(p,owner=OWNER,account="default",
            symbol="SPYM",side="BUY",quantity=1,
            request_id="blocked-after-unknown-001",
            quote=US,fx=FX,auto_fx=True,session_date=DAY,now=NOW)
    with p._db() as db:
        assert db.execute("""SELECT state FROM stage1_turnover_claims
                 WHERE owner=? AND request_id='unknown-p0-001'""",
                 (OWNER,)).fetchone()[0]=="UNKNOWN"
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==0


def _sigkill_worker(db_path,phase):
    """Real OS kill at a writer transition, without production code toggles."""
    import os
    import signal
    from pathlib import Path
    import wealthy_server.stage1_verified_execution as executor
    p=Portfolios(Path(db_path),require_verified_p0=True)
    if phase=="after_reserve":
        original=executor.reserve
        def stop(*args,**kwargs):
            result=original(*args,**kwargs)
            os.kill(os.getpid(),signal.SIGKILL)
            return result
        executor.reserve=stop
    elif phase=="after_fx_and_order_insert":
        # Receipts are inserted after the FX event and virtual order row.
        p._receipt=lambda *args,**kwargs:os.kill(os.getpid(),signal.SIGKILL)
    elif phase=="after_filled_before_commit":
        original=executor.mark_filled
        def stop(*args,**kwargs):
            result=original(*args,**kwargs)
            os.kill(os.getpid(),signal.SIGKILL)
            return result
        executor.mark_filled=stop
    result=execute_verified_paper(
        p,owner=OWNER,account="default",symbol="SPYM",side="BUY",
        quantity=1,request_id="sigkill-order-001",quote=US,fx=FX,
        auto_fx=True,session_date=DAY,now=NOW)
    assert result["status"]=="executed"
    if phase=="after_commit":
        os.kill(os.getpid(),signal.SIGKILL)


@pytest.mark.parametrize("phase",[
    "after_reserve","after_fx_and_order_insert",
    "after_filled_before_commit","after_commit"
])
def test_real_sigkill_and_restart_leave_exactly_one_consistent_fill(tmp_path,phase):
    from multiprocessing import get_context
    import signal
    folder=tmp_path/phase
    folder.mkdir()
    p,_,_=ready(folder)
    child=get_context("fork").Process(
        target=_sigkill_worker,args=(str(p.path),phase))
    child.start()
    child.join(timeout=15)
    if child.is_alive():
        child.kill()
        child.join(10)
        pytest.fail("interrupted writer failed to exit within 15 seconds")
    assert child.exitcode==-signal.SIGKILL
    restarted=Portfolios(p.path,require_verified_p0=True)
    with restarted._db() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()==("ok",)
        counts=[
            db.execute("SELECT COUNT(*) FROM "+table+" WHERE owner=?",(OWNER,)).fetchone()[0]
            for table in ("virtual_orders","stage1_turnover_claims","stage1_trade_metadata")]
        fx_count=db.execute("""SELECT COUNT(*) FROM stage1_events
                   WHERE owner=? AND kind='virtual_fx'""",(OWNER,)).fetchone()[0]
        if phase=="after_commit":
            assert counts==[1,1,1] and fx_count==1
        else:
            assert counts==[0,0,0] and fx_count==0
    retry=execute_verified_paper(
        restarted,owner=OWNER,account="default",symbol="SPYM",side="BUY",
        quantity=1,request_id="sigkill-order-001",quote=US,fx=FX,
        auto_fx=True,session_date=DAY,now=NOW)
    assert retry["status"]==(
        "already_executed" if phase=="after_commit" else "executed")
    with restarted._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders WHERE owner=?",(OWNER,)).fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims WHERE owner=?",(OWNER,)).fetchone()[0]==1
        assert db.execute("""SELECT COUNT(*) FROM stage1_events WHERE owner=?
                   AND kind='virtual_fx'""",(OWNER,)).fetchone()[0]==1
