"""Independent P0 20% reservation ledger contract, on disposable owner-scoped SQLite.

This is NOT an integrated Stage4/HTTP fill test. No live orders, KIS, network,
user credentials or operational SQLite database are touched.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_turnover_reservations import (
    establish_session, reserve, mark_unknown, mark_filled, release_without_fill
)

STAMP="2026-10-26T14:00:00+00:00"
START="2026-10-26T13:30:00+00:00"
END="2026-10-26T20:00:00+00:00"
SESSION="2026-10-26"
OWNER="only-user"
NAV="4000000"


def store(tmp_path):
    p=Portfolios(tmp_path/"isolated-paper.sqlite3")
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO portfolios(owner,id,name,kind,created_at)
            VALUES(?,?,?,?,?)""",(OWNER,"kr-base","KR fixture","GENERAL_DOMESTIC",STAMP))
        establish_session(db,owner=OWNER,session_date=SESSION,
            opening_nav_krw=NAV,nav_source="verified-historical-valuation-fixture",
            verified_utc=STAMP,start_utc=START,end_utc=END)
        db.execute("COMMIT")
    return p


def claim(db,key,amount,*,side="BUY",owner=OWNER,account="kr-base"):
    return reserve(db,owner=owner,session_date=SESSION,request_id=key,
        account=account,symbol="069500",side=side,quantity=1,
        gross_krw=amount,now_utc=STAMP)


def test_4000000_nav_combined_buy_sell_and_multi_account_limit(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        assert claim(db,"initial-buy-0001","300000")["status"]=="RESERVED"
        assert claim(db,"second-sell-0002","500000",side="SELL",
                     account="another-account")["status"]=="RESERVED"
        with pytest.raises(ValueError,match="GROSS_TURNOVER_CAP_EXCEEDED"):
            claim(db,"one-over-0003","1")
        db.execute("COMMIT")
    with p._db() as db:
        states=db.execute("SELECT state FROM stage1_turnover_claims ORDER BY request_id").fetchall()
        assert states==[("RESERVED",),("RESERVED",)]


def test_same_idempotent_request_and_payload_conflict(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        assert claim(db,"idempotent-0001","150000")["status"]=="RESERVED"
        assert claim(db,"idempotent-0001","150000")["status"]=="already_reserved"
        with pytest.raises(ValueError,match="CONFLICTING_REQUEST_ID"):
            claim(db,"idempotent-0001","100000")
        db.execute("COMMIT")
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims").fetchone()[0]==1


def test_missing_provenance_and_immutable_opening_nav(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError,match="immutable"):
            establish_session(db,owner=OWNER,session_date=SESSION,
                opening_nav_krw="8000000",nav_source="verified-historical-valuation-fixture",
                verified_utc=STAMP,start_utc=START,end_utc=END)
        with pytest.raises(ValueError,match="provenance"):
            establish_session(db,owner="unverified-owner",session_date=SESSION,
                opening_nav_krw=NAV,nav_source="sample",
                verified_utc=STAMP,start_utc=START,end_utc=END)
        with pytest.raises(ValueError,match="UNVERIFIED_SESSION_OPENING_NAV"):
            reserve(db,owner="unknown-owner",session_date=SESSION,
                request_id="missing-session-01",account="default",
                symbol="069500",side="BUY",quantity=1,gross_krw="1",
                now_utc=STAMP)
        db.execute("COMMIT")


def test_unknown_survives_restart_and_never_releases_optimistically(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        claim(db,"unknown-claimed-01","600000")
        mark_unknown(db,OWNER,"unknown-claimed-01",STAMP)
        db.execute("COMMIT")
    p=Portfolios(p.path) # recreated process / fresh SQLite connection
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError,match="unreconciled"):
            release_without_fill(db,OWNER,"unknown-claimed-01",STAMP,"CANCELED")
        with pytest.raises(ValueError,match="GROSS_TURNOVER_CAP_EXCEEDED"):
            claim(db,"following-claim-02","200001")
        assert claim(db,"following-claim-03","200000")["status"]=="RESERVED"
        db.execute("COMMIT")


def test_closed_request_requires_no_fill_and_key_cannot_be_reused(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        claim(db,"cancelled-key-01","700000")
        release_without_fill(db,OWNER,"cancelled-key-01",STAMP,"CANCELED")
        assert claim(db,"different-key-02","800000")["status"]=="RESERVED"
        with pytest.raises(ValueError,match="CLOSED_REQUEST_CANNOT_REPLAY"):
            claim(db,"cancelled-key-01","700000")
        db.execute("COMMIT")


def test_fill_requires_matching_virtual_order_inside_transaction(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        claim(db,"verified-fill-01","200000")
        with pytest.raises(ValueError,match="matching PAPER fill"):
            mark_filled(db,OWNER,"verified-fill-01",STAMP)
        db.execute("""INSERT INTO virtual_orders VALUES
          (?,?,?,?,?,?,?,?,?,?,?)""",(OWNER,"kr-base","verified-fill-01",
             "069500","BUY",1,"200000","KRW","isolated-fixture",
             "2026-10-26",STAMP))
        mark_filled(db,OWNER,"verified-fill-01",STAMP)
        db.execute("COMMIT")
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        assert claim(db,"after-200k-fill-01","600000")["status"]=="RESERVED"
        db.execute("COMMIT")


def test_unreconciled_legacy_fill_blocks_new_reservations(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO virtual_orders VALUES
          (?,?,?,?,?,?,?,?,?,?,?)""",(OWNER,"kr-base","legacy-unclaimed-001",
             "069500","BUY",1,"50000","KRW","isolated-fixture",SESSION,STAMP))
        with pytest.raises(ValueError,match="UNRECONCILED_LEGACY_PAPER_FILL"):
            claim(db,"new-after-legacy-01","10000")
        db.execute("COMMIT")


def test_concurrent_sqlite_writers_share_one_owner_session_limit(tmp_path):
    p=store(tmp_path)
    def attempt(i):
        with p._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                claim(db,f"parallel-resv-{i:04d}","300000")
                db.execute("COMMIT")
                return True
            except ValueError:
                db.execute("ROLLBACK")
                return False
    with ThreadPoolExecutor(max_workers=5) as pool:
        values=list(pool.map(attempt,range(5)))
    assert values.count(True)==2 and values.count(False)==3
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims WHERE owner=?",
            (OWNER,)).fetchone()[0]==2


def test_other_owner_cannot_consume_or_read_claims(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        claim(db,"single-owner-01","800000")
        establish_session(db,owner="other-owner",session_date=SESSION,
            opening_nav_krw="1000000",
            nav_source="verified-historical-valuation-fixture",
            verified_utc=STAMP,start_utc=START,end_utc=END)
        assert claim(db,"other-owner-01","200000",owner="other-owner")["status"]=="RESERVED"
        db.execute("COMMIT")
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims WHERE owner=?",
            ("other-owner",)).fetchone()[0]==1


def test_stage4_unreconciled_claim_blocks_new_session_reservations(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE stage4_execution_claims(
            owner TEXT, request_id TEXT, status TEXT)""")
        db.execute("INSERT INTO stage4_execution_claims VALUES(?,?,?)",
           (OWNER,"old-auto-unknown","CLAIMED"))
        with pytest.raises(ValueError,match="UNRECONCILED_STAGE4_CLAIM"):
            claim(db,"new-manual-001","100000")
        db.execute("COMMIT")


def integrated_store(tmp_path):
    """No external providers: create a scoped fixture NAV and PAPER cash."""
    from datetime import timedelta
    p=Portfolios(tmp_path/"integrated-paper.sqlite3")
    moment=datetime.now(timezone.utc)
    day=moment.date().isoformat()
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO portfolios VALUES(?,?,?,?,?)",
                   (OWNER,"kr-base","KR test","GENERAL_DOMESTIC",moment.isoformat()))
        db.execute("INSERT INTO stage1_events VALUES(?,?,?,?,?,?,?)",
                   (OWNER,"initial","initial",4000000,"0",None,moment.isoformat()))
        db.execute("INSERT INTO portfolio_transfers VALUES(?,?,?,?,?,?)",
                   (OWNER,"fixture-transfer-01","default","kr-base",2000000,
                    moment.isoformat()))
        establish_session(db,owner=OWNER,session_date=day,
            opening_nav_krw=NAV,
            nav_source="verified-historical-valuation-fixture",
            verified_utc=moment.isoformat(),
            start_utc=(moment-timedelta(hours=1)).isoformat(),
            end_utc=(moment+timedelta(hours=1)).isoformat())
        db.execute("COMMIT")
    quote={"symbol":"069500","status":"ok","tradeable":True,
           "currency":"KRW","price":"200000",
           "source":"isolated-fixture","as_of":day}
    return p,day,quote


def test_opt_in_trade_atomic_session_claim_fill_buy_sell_and_cap(tmp_path):
    p,day,quote=integrated_store(tmp_path)
    for ix,side in enumerate(("BUY","SELL","BUY","BUY"),1):
        p.trade(OWNER,"kr-base","069500",side,1,f"opt-in-order-{ix:03d}",quote,
                manual_approval_krw="200000",
                verified_turnover_session_date=day)
    with p._db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM stage1_turnover_claims WHERE state='FILLED'"
        ).fetchone()[0]==4
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==4
    with pytest.raises(ValueError,match="GROSS_TURNOVER_CAP_EXCEEDED"):
        p.trade(OWNER,"kr-base","069500","BUY",1,"opt-in-order-005",quote,
                manual_approval_krw="200000",
                verified_turnover_session_date=day)
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==4
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims").fetchone()[0]==4


def test_opt_in_trade_restart_replay_requires_matching_reservation(tmp_path):
    p,day,quote=integrated_store(tmp_path)
    key="opt-in-retry-0001"
    p.trade(OWNER,"kr-base","069500","BUY",1,key,quote,
            manual_approval_krw="200000",verified_turnover_session_date=day)
    reopened=Portfolios(p.path)
    reopened.trade(OWNER,"kr-base","069500","BUY",1,key,quote,
            manual_approval_krw="200000",verified_turnover_session_date=day)
    with reopened._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims").fetchone()[0]==1


def test_opt_in_trade_missing_session_refuses_without_fill(tmp_path):
    p,day,quote=integrated_store(tmp_path)
    wrong_day="2036-10-26"
    with pytest.raises(ValueError,match="UNVERIFIED_SESSION_OPENING_NAV"):
        p.trade(OWNER,"kr-base","069500","BUY",1,"opt-in-no-nav-001",quote,
                manual_approval_krw="200000",
                verified_turnover_session_date=wrong_day)
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims").fetchone()[0]==0


def test_opt_in_trade_rejects_unknown_reservation_over_limit(tmp_path):
    p,day,quote=integrated_store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        reserve(db,owner=OWNER,session_date=day,request_id="unknown-optin-001",
                account="kr-base",symbol="069500",side="SELL",quantity=1,
                gross_krw="650000",now_utc=datetime.now(timezone.utc).isoformat())
        mark_unknown(db,OWNER,"unknown-optin-001",datetime.now(timezone.utc).isoformat())
        db.execute("COMMIT")
    with pytest.raises(ValueError,match="GROSS_TURNOVER_CAP_EXCEEDED"):
        p.trade(OWNER,"kr-base","069500","BUY",1,"new-after-unknown-01",quote,
                manual_approval_krw="200000",
                verified_turnover_session_date=day)
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==0


def test_opt_in_trade_concurrent_accounts_share_one_owner_cap(tmp_path):
    p,day,quote=integrated_store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO portfolios VALUES(?,?,?,?,?)",
                   (OWNER,"isa-base","ISA test","ISA_SIMULATED",STAMP))
        db.execute("INSERT INTO portfolio_transfers VALUES(?,?,?,?,?,?)",
                   (OWNER,"fixture-transfer-02","default","isa-base",1000000,STAMP))
        db.execute("COMMIT")
    def attempt(i):
        account="kr-base" if i%2==0 else "isa-base"
        try:
            p.trade(OWNER,account,"069500","BUY",1,f"opt-in-parallel-{i:03d}",
                quote,manual_approval_krw="200000",
                verified_turnover_session_date=day)
            return True
        except ValueError as error:
            assert "GROSS_TURNOVER_CAP_EXCEEDED" in str(error)
            return False
    with ThreadPoolExecutor(max_workers=6) as executor:
        outputs=list(executor.map(attempt,range(6)))
    assert outputs.count(True)==4 and outputs.count(False)==2
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims").fetchone()[0]==4
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==4

def test_reservation_needs_actual_verified_session_window(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError,match="US_MARKET_SESSION_NOT_VERIFIED_OR_CLOSED"):
            reserve(db,owner=OWNER,session_date=SESSION,
                request_id="outside-hours-001",account="kr-base",symbol="069500",
                side="BUY",quantity=1,gross_krw="100000",
                now_utc="2026-10-26T21:00:00+00:00")
        with pytest.raises(ValueError,match="US_MARKET_SESSION_NOT_VERIFIED_OR_CLOSED"):
            reserve(db,owner=OWNER,session_date=SESSION,
                request_id="too-early-hours-001",account="kr-base",symbol="069500",
                side="BUY",quantity=1,gross_krw="100000",
                now_utc="2026-10-26T13:31:00+00:00")
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims").fetchone()[0]==0
        db.execute("COMMIT")


def test_session_proof_is_owner_scoped_and_cannot_be_weakened(tmp_path):
    p=store(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError,match="opening NAV/session proof is immutable"):
            establish_session(db,owner=OWNER,session_date=SESSION,
                opening_nav_krw=NAV,nav_source="totally-different-research-source",
                verified_utc=STAMP,start_utc=START,end_utc=END)
        with pytest.raises(ValueError,match="invalid verified US session window"):
            establish_session(db,owner="another",session_date=SESSION,
                opening_nav_krw=NAV,nav_source="verified-historical-valuation-fixture",
                verified_utc=STAMP,start_utc=END,end_utc=START)
        db.execute("COMMIT")
