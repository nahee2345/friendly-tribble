"""Stage7 independent release acceptance and deliberate-fault injection.

This test suite uses temporary isolated simulated SQLite files only. No
network, no broker, no actual user's holdings and no production restoration.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime,date,timedelta,timezone
from pathlib import Path

import pytest

from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_backup import online_paper_backup
from wealthy_server.stage1_controls import set_paused
from wealthy_server.stage1_market_store import MarketStore
from wealthy_server.stage1_discovery import PreferenceStore
from wealthy_server.stage4_portfolio import PortfolioEngine,_schema as claim_schema
from wealthy_server.stage7_release import audit_paper_ledger,verify_backup_copy
from wealthy_server.stage1_worker import PaperWorker
from wealthy_server.stage5_accounting import _tables as nav_schema

NOW=datetime(2026,10,19,10,0,tzinfo=timezone.utc)
QUOTE_DATE="2026-10-16"


def setup(tmp_path,owner="alice"):
    path=tmp_path/"stage1-paper.sqlite3"
    ledger=Stage1Ledger(path)
    portfolio=Portfolios(path)
    initialize_paper_accounts(owner,ledger,portfolio,today=date(2026,10,18))
    return ledger,portfolio


def q(symbol,price="10000"):
    return {"status":"ok","tradeable":True,"symbol":symbol,
            "currency":"KRW" if symbol.isdigit() else "USD",
            "price":price,"source":"offline_verified_fixture",
            "as_of":QUOTE_DATE}


def audit(p,owner="alice"):
    return audit_paper_ledger(owner,p,now=NOW)


def write_claim(p,*,key="s4:20261019:KR:ETF",state="CLAIMED",
                account="kr-base",symbol="069500",side="BUY",qty=1):
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        claim_schema(db)
        db.execute("""INSERT INTO stage4_execution_claims VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("alice",key,"7"*64,account,symbol,side,qty,
             "10000",state,"SIMULATED_CRASH",NOW.isoformat(),NOW.isoformat()))
        db.execute("COMMIT")


def test_default_funded_accounts_are_healthy_and_unpaused_is_not_assumed(tmp_path):
    ledger,p=setup(tmp_path)
    result=audit(p)
    assert result["status"]=="healthy",result
    assert result["blocking_count"]==0
    assert result["read_only"] is True and result["repair_performed"] is False
    assert result["paper_execution_permitted_by_integrity"] is True
    assert result["counts"]["accounts"]==3
    assert result["counts"]["orders"]==0
    assert result["counts"]["paused"] is True
    assert all(float(values["KRW"])>=0 for values in result["counts"]["cash_balances"].values())


def test_cross_account_funding_fx_buy_sale_and_sqlite_checks(tmp_path):
    ledger,p=setup(tmp_path)
    p.trade("alice","kr-base","005930","BUY",2,"first-purchase-kr",q("005930","10000"))
    p.trade("alice","kr-base","005930","SELL",1,"partial-exit-kr",q("005930","12000"))
    p.auto_buy_usd_with_fx("alice","VOO",q("VOO","85"),"1400","first-us-etf-001")
    result=audit(p)
    assert result["status"]=="healthy",result
    assert result["counts"]["orders"]==3
    assert result["counts"]["positions"]==2
    assert result["counts"]["events"]>=2
    assert not result["issues"]


def test_sqlite_readonly_audit_does_not_change_paper_table_counts(tmp_path):
    _,p=setup(tmp_path)
    with p._db() as db:
        before={table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("stage1_events","portfolios","virtual_orders","portfolio_transfers")}
    first=audit(p);second=audit(p)
    assert first["status"]==second["status"]=="healthy"
    with p._db() as db:
        after={table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before}
    assert before==after


def test_negative_position_from_tampered_order_is_blocked(tmp_path):
    _,p=setup(tmp_path)
    p.trade("alice","kr-base","005930","BUY",1,"buy-inventory-001",q("005930","10000"))
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE virtual_orders SET side='SELL' WHERE owner=? AND request_id=?",
                   ("alice","buy-inventory-001"))
        db.execute("COMMIT")
    result=audit(p)
    assert result["status"]=="blocked"
    assert any(x["code"]=="INVALID_PAPER_FILL" for x in result["issues"])


def test_overspent_virtual_account_cash_blocks_unsafe_auto_restarts(tmp_path):
    _,p=setup(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO stage1_events VALUES
             (?,?,?,?,?,?,?)""",
             ("alice","virtual-drain-001","virtual_fx",-3_500_000,
              "2500","1400",NOW.isoformat()))
        db.execute("COMMIT")
    result=audit(p)
    assert result["status"]=="blocked"
    assert any(x["code"]=="NEGATIVE_ACCOUNT_CASH" for x in result["issues"])
    engine=PortfolioEngine(p,PreferenceStore(p.path),MarketStore.for_portfolios(p),
           quote_provider=lambda *_a:pytest.fail("invalid book must not fetch execution quote"))
    set_paused("alice",p,False,"audit regression")
    executed=engine.run("alice",now=NOW)
    assert executed["status"]=="integrity_blocked"
    assert not executed["executed"]
    assert not p.orders("alice","default")["orders"]


def test_orphan_trade_metadata_blocks_simulated_buy(tmp_path):
    _,p=setup(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO stage1_trade_metadata
          (owner,portfolio,order_key,quote_as_of,foreign_exchange_rate,fee_amount,
           tax_amount,costs_currency,source,observed_at_utc,created_at_utc)
          VALUES (?,?,?, ?,NULL,NULL,NULL,NULL,?,?,?)""",
          ("alice","kr-base","missing-order-001",QUOTE_DATE,"fixture",
           NOW.isoformat(),NOW.isoformat()))
        db.execute("COMMIT")
    assert "ORPHAN_TRADE_METADATA" in {x["code"] for x in audit(p)["issues"]}


def test_unreconciled_stage4_claim_blocks_replay_and_does_not_auto_fix(tmp_path):
    _,p=setup(tmp_path)
    write_claim(p)
    before=audit(p)
    assert before["status"]=="blocked"
    assert "INTERRUPTED_EXECUTION_CLAIM" in {x["code"] for x in before["issues"]}
    assert len(before["issues"])>=1
    with p._db() as db:
        assert db.execute("SELECT status FROM stage4_execution_claims").fetchone()[0]=="CLAIMED"
    with p._db() as db:
        assert not db.execute("SELECT 1 FROM virtual_orders LIMIT 1").fetchone()


def test_executed_claim_without_real_order_is_not_trusted(tmp_path):
    _,p=setup(tmp_path)
    write_claim(p,state="EXECUTED")
    assert "CLAIM_WITHOUT_MATCHING_FILL" in {x["code"] for x in audit(p)["issues"]}


def test_claim_with_real_simulated_order_is_consistent(tmp_path):
    _,p=setup(tmp_path)
    p.trade("alice","kr-base","069500","BUY",1,"s4:20261019:KR:ETF",
            q("069500","10000"))
    write_claim(p,state="EXECUTED")
    assert audit(p)["status"]=="healthy"


def test_daily_nav_snapshot_sha_mutation_blocks_new_investments(tmp_path):
    _,p=setup(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        nav_schema(db)
        db.execute("""INSERT INTO stage5_nav_snapshots VALUES
            (?,?,?,?,?,?,?)""",
            ("alice",NOW.date().isoformat(),NOW.isoformat(),
             "4000000","1"*64,"2"*64,"3"*64))
        db.execute("COMMIT")
    result=audit(p)
    assert result["status"]=="blocked"
    assert "NAV_SNAPSHOT_MISMATCH" in {x["code"] for x in result["issues"]}


def test_unsigned_old_nav_produces_warning_not_false_corroboration(tmp_path):
    _,p=setup(tmp_path)
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        nav_schema(db)
        db.execute("""INSERT INTO stage5_nav_snapshots VALUES
            (?,?,?,?,?,?,?)""",
            ("alice",NOW.date().isoformat(),NOW.isoformat(),
             "4000000","1"*64,None,None))
        db.execute("COMMIT")
    result=audit(p)
    assert result["status"]=="warning"
    assert result["blocking_count"]==0
    assert result["warning_count"]==1


def test_backup_can_be_verified_without_restore_source_overwrite(tmp_path):
    _,p=setup(tmp_path)
    backups=tmp_path/"backups"
    first=online_paper_backup(p,directory=backups)
    before=audit(p)
    result=verify_backup_copy(p,first["filename"],"alice",directory=backups)
    assert result["status"]=="structurally_verified"
    assert result["sha256_computed_now"]==first["sha256"]
    assert result["trusted_manifest_hash_verified"] is False
    assert result["temporary_restore_drill_ok"] is True
    assert result["temporary_restore_issue"] is None
    assert not list(backups.glob(".stage7-restore-*"))
    assert result["restore_performed"] is False
    assert result["source_overwritten"] is False
    assert result["owner_rows"]["stage1_events"]>=1
    assert audit(p)==before


def test_corrupted_backup_fails_without_touching_active_paper_db(tmp_path):
    _,p=setup(tmp_path)
    backup_dir=tmp_path/"backups"
    saved=online_paper_backup(p,directory=backup_dir)
    target=backup_dir/saved["filename"]
    with target.open("r+b") as f:
        f.seek(100);f.write(b"not a valid SQLite copy")
    verified=verify_backup_copy(p,saved["filename"],"alice",directory=backup_dir)
    assert verified["status"]=="unusable"
    assert verified["temporary_restore_drill_ok"] is False
    assert not list(backup_dir.glob(".stage7-restore-*"))
    assert verified["restore_performed"] is False
    assert audit(p)["status"]=="healthy"


def test_backup_path_traversal_symlink_and_file_permissions_blocked(tmp_path):
    _,p=setup(tmp_path)
    folder=tmp_path/"backups"
    saved=online_paper_backup(p,directory=folder)
    for filename in ("../../stage1-paper.sqlite3","stage1-paper-evil.sqlite3",".."):
        with pytest.raises(ValueError):
            verify_backup_copy(p,filename,"alice",directory=folder)
    destination=folder/saved["filename"]
    sym=folder/"stage1-paper-20261019T000000000001Z.sqlite3"
    sym.symlink_to(destination)
    with pytest.raises(ValueError):
        verify_backup_copy(p,sym.name,"alice",directory=folder)


def test_user_isolation_no_cross_owner_orders_or_claims(tmp_path):
    ledger,p=setup(tmp_path,"alice")
    initialize_paper_accounts("bob",ledger,p,today=date(2026,10,18))
    p.trade("alice","kr-base","005930","BUY",1,"alice-order-001",
            q("005930","5000"))
    write_claim(p,state="CLAIMED")
    assert audit(p,"alice")["status"]=="blocked"
    bob=audit(p,"bob")
    assert bob["status"]=="healthy",bob
    assert bob["counts"]["orders"]==0
    assert bob["counts"]["stage4_claims"]==0


def test_stopped_worker_wait_timeout_does_not_create_second_scheduler():
    class StillStopping:
        def __init__(self):self.joined=False
        def is_alive(self):return True
        def join(self,timeout=None):self.joined=True
    worker=PaperWorker(None,None,None,None)
    active=StillStopping()
    worker.thread=active
    worker.stop()
    assert active.joined and worker.thread is active
    worker.start()
    assert worker.thread is active


def test_invalid_timestamp_and_nonfinite_order_value_are_detected(tmp_path):
    _,p=setup(tmp_path)
    p.trade("alice","kr-base","005930","BUY",1,"corrupt-order-001",
            q("005930","10000"))
    with p._db() as db:
        db.execute("""UPDATE virtual_orders SET price='NaN',created_at=?
            WHERE owner=? AND request_id=?""",
            ((NOW+timedelta(days=10)).isoformat(),"alice","corrupt-order-001"))
    issues={x["code"] for x in audit(p)["issues"]}
    assert "INVALID_PAPER_FILL" in issues


def test_audit_owner_ledger_limit_is_bounded_to_50000_rows(tmp_path):
    _,p=setup(tmp_path)
    # Avoid allocating giant inputs; confirm the declared resource budget and
    # that invalid unbounded requests never happen through the public API.
    from wealthy_server.stage7_release import MAX_ROWS,ERRORS_LIMIT
    assert MAX_ROWS==50000 and ERRORS_LIMIT<=35
    assert audit(p)["read_only"]


def test_old_five_column_daily_nav_is_warning_and_audit_does_not_migrate(tmp_path):
    _,p=setup(tmp_path)
    with p._db() as db:
        db.execute("""CREATE TABLE stage5_nav_snapshots(
            owner TEXT NOT NULL,as_of_day TEXT NOT NULL,
            observed_utc TEXT NOT NULL,nav_krw TEXT NOT NULL,
            sources_sha256 TEXT NOT NULL,
            PRIMARY KEY(owner,as_of_day))""")
        db.execute("""INSERT INTO stage5_nav_snapshots
            VALUES (?,?,?,?,?)""",("alice",NOW.date().isoformat(),
            NOW.isoformat(),"4000000","a"*64))
    result=audit(p)
    assert result["status"]=="warning",result
    assert "UNSIGNED_LEGACY_NAV" in {x["code"] for x in result["issues"]}
    with p._db() as db:
        assert len(db.execute("PRAGMA table_info(stage5_nav_snapshots)").fetchall())==5
    assert result["repair_performed"] is False
