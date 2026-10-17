"""Stage1 v1 migration acceptance: protect every legacy row and recovery path."""
import hashlib
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_schema import CURRENT_VERSION, version_status
from wealthy_server.stage1_audit import Stage1Audit


def legacy_fixture(tmp_path):
    """Create real v0 tables with no migration registry at all."""
    state = tmp_path / "state"
    state.mkdir()
    location = state / "paper.sqlite3"
    Stage1Ledger(location).state("owner", date(2026,10,17))
    with sqlite3.connect(location) as db:
        db.execute("""CREATE TABLE portfolios (
            owner TEXT NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,
            kind TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(owner,id))""")
        db.execute("""CREATE TABLE portfolio_transfers (
            owner TEXT NOT NULL,request_id TEXT NOT NULL,
            source TEXT NOT NULL,destination TEXT NOT NULL,
            amount_krw INTEGER NOT NULL,created_at TEXT NOT NULL,
            PRIMARY KEY(owner,request_id))""")
        db.execute("""CREATE TABLE virtual_orders (
            owner TEXT NOT NULL,portfolio TEXT NOT NULL,request_id TEXT NOT NULL,
            symbol TEXT NOT NULL,side TEXT NOT NULL,quantity INTEGER NOT NULL,
            price TEXT NOT NULL,currency TEXT NOT NULL,source TEXT NOT NULL,
            as_of TEXT NOT NULL,created_at TEXT NOT NULL,
            PRIMARY KEY(owner,portfolio,request_id))""")
        timestamp="2026-10-17T09:00:00+00:00"
        db.execute("INSERT INTO portfolios VALUES(?,?,?,?,?)",
                   ("owner","default","기본 가상계좌","GENERAL_OVERSEAS",timestamp))
        db.execute("INSERT INTO portfolios VALUES(?,?,?,?,?)",
                   ("owner","kr-base","국내 계좌","GENERAL_DOMESTIC",timestamp))
        db.execute("INSERT INTO portfolio_transfers VALUES(?,?,?,?,?,?)",
                   ("owner","initial-kr-transfer","default","kr-base",500000,timestamp))
        db.execute("INSERT INTO virtual_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("owner","kr-base","historic-order","005930","BUY",1,
                    "50000","KRW","original verified source","2026-10-16",timestamp))
        db.commit()
    return location


def legacy_data(location):
    with sqlite3.connect(location) as db:
        return {
            table:db.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            for table in ("stage1_events","stage1_settings","portfolios",
                          "portfolio_transfers","virtual_orders")
        }


def test_migration_creates_verified_backup_and_never_alters_legacy_rows(tmp_path):
    location=legacy_fixture(tmp_path)
    previous=legacy_data(location)
    portfolio=Portfolios(location)
    accounts=portfolio.list("owner")["accounts"]
    assert legacy_data(location)==previous
    assert [x["id"] for x in accounts]==["default","kr-base"]
    assert next(x for x in accounts if x["id"]=="kr-base")["krw_cash"]=="450000"
    assert next(x for x in accounts if x["id"]=="kr-base")["holdings"]==[{"symbol":"005930","quantity":1}]
    with portfolio._db() as db:
        status=version_status(db)
        assert status["schema_version"]==CURRENT_VERSION
        assert status["backup_created"] is True
        filename=db.execute("SELECT backup_filename FROM stage1_schema_registry WHERE id=1").fetchone()[0]
    saved=tmp_path/"backups"/filename
    assert saved.exists() and not saved.is_symlink()
    assert (saved.stat().st_mode & 0o077)==0
    assert hashlib.sha256(saved.read_bytes()).hexdigest()==status["pre_migration_sha256"]
    assert legacy_data(saved)==previous
    with sqlite3.connect(saved) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert backup.execute("""SELECT 1 FROM sqlite_master
             WHERE name='stage1_schema_registry'""").fetchone() is None
    assert len(list((tmp_path/"backups").glob("stage1-premigration-*.sqlite3")))==1
    Portfolios(location).list("owner")
    assert len(list((tmp_path/"backups").glob("stage1-premigration-*.sqlite3")))==1


def test_new_empty_db_gets_schema_without_unnecessary_backups(tmp_path):
    path=tmp_path/"paper.sqlite3"
    p=Portfolios(path)
    assert len(p.list("owner")["accounts"])==1
    with p._db() as db:
        status=version_status(db)
    assert status["schema_version"]==1
    assert status["backup_created"] is False


def test_unknown_newer_schema_refuses_downgrade(tmp_path):
    path=tmp_path/"paper.sqlite3"
    p=Portfolios(path)
    p.list("owner")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE stage1_schema_registry SET schema_version=99")
    with pytest.raises(ValueError,match="newer"):
        Portfolios(path).list("owner")


def test_bad_preflight_directory_fails_closed_without_touching_legacy_data(tmp_path):
    path=legacy_fixture(tmp_path)
    previous=legacy_data(path)
    external=tmp_path/"external"
    external.mkdir()
    (tmp_path/"backups").symlink_to(external,target_is_directory=True)
    with pytest.raises(ValueError,match="symlink"):
        Portfolios(path).list("owner")
    assert legacy_data(path)==previous
    with sqlite3.connect(path) as db:
        assert db.execute("""SELECT name FROM sqlite_master
               WHERE name='stage1_schema_registry'""").fetchone() is None


def test_failed_migration_is_rolled_back(tmp_path,monkeypatch):
    import wealthy_server.stage1_schema as schema
    path=legacy_fixture(tmp_path)
    previous=legacy_data(path)
    def broken(db):
        db.execute("CREATE TABLE stage1_schema_registry (id INTEGER)")
        raise RuntimeError("fault injection")
    monkeypatch.setattr(schema,"_create_v1",broken)
    with pytest.raises(RuntimeError,match="fault injection"):
        Portfolios(path).list("owner")
    assert legacy_data(path)==previous
    with sqlite3.connect(path) as db:
        assert db.execute("""SELECT name FROM sqlite_master
                              WHERE name='stage1_schema_registry'""").fetchone() is None


def test_new_trades_have_provenance_but_unknown_fees(tmp_path):
    path=tmp_path/"paper.sqlite3"
    ledger=Stage1Ledger(path)
    ledger.state("owner",date(2026,10,17))
    portfolio=Portfolios(path)
    from wealthy_server.stage1_bootstrap import initialize_paper_accounts
    initialize_paper_accounts("owner",ledger,portfolio,today=date(2026,10,17))
    quote={"symbol":"069500","currency":"KRW","price":"25000",
           "status":"ok","tradeable":True,"source":"test market",
           "as_of":"2026-10-16"}
    before=portfolio.list("owner")["accounts"]
    portfolio.trade("owner","kr-base","069500","BUY",1,"test-receipt-0001",quote)
    portfolio.trade("owner","kr-base","069500","BUY",1,"test-receipt-0001",quote)
    with portfolio._db() as db:
        rows=db.execute("""SELECT quote_as_of,fee_amount,tax_amount,source
             FROM stage1_trade_metadata WHERE owner=?""",("owner",)).fetchall()
    assert rows==[("2026-10-16",None,None,"test market")]
    assert len(portfolio.orders("owner","kr-base")["orders"])==1


def test_audit_evidence_is_idempotent_owner_scoped_and_cash_neutral(tmp_path):
    path=tmp_path/"paper.sqlite3"
    ledger=Stage1Ledger(path)
    ledger.state("owner",date(2026,10,17))
    portfolio=Portfolios(path)
    portfolio.list("owner")
    audit=Stage1Audit(portfolio)
    starting=portfolio.list("owner")["accounts"][0]["krw_cash"]
    params=dict(event_key="verified-dividend-0001",portfolio="default",
                category="DIVIDEND",amount="3.20",currency="USD",
                observed_at_utc="2026-10-16T12:00:00+00:00",
                reference="AAPL corporate release",evidence_source="source-document")
    assert audit.accounting_event("owner",**params)["status"]=="recorded"
    assert audit.accounting_event("owner",**params)["status"]=="existing"
    with pytest.raises(ValueError,match="collision"):
        audit.accounting_event("owner",**{**params,"amount":"5.00"})
    with pytest.raises(ValueError,match="not owned"):
        audit.accounting_event("other",**params)
    with pytest.raises(ValueError):
        audit.accounting_event("owner",**{**params,"amount":"NaN","event_key":"invalid-amount-01"})
    with pytest.raises(ValueError):
        audit.accounting_event("owner",**{**params,"currency":"KRW","amount":"2.2","event_key":"invalid-precision-01"})
    assert portfolio.list("owner")["accounts"][0]["krw_cash"]==starting
    assert audit.overview("owner")["accounting"]==[
        {"category":"DIVIDEND","status":"PENDING_RECONCILIATION","count":1}]
    assert audit.overview("other")["accounting"]==[]


def test_corporate_action_is_recorded_but_does_not_adjust_holdings(tmp_path):
    path=tmp_path/"paper.sqlite3"
    ledger=Stage1Ledger(path)
    ledger.state("owner",date(2026,10,17))
    portfolio=Portfolios(path)
    portfolio.list("owner")
    audit=Stage1Audit(portfolio)
    params=dict(event_key="action-microsoft-001",symbol="MSFT",
          category="SPLIT",effective_date="2026-10-16",
          terms={"numerator":2,"denominator":1},
          evidence_source="company notice")
    assert audit.corporate_event("owner",**params)["holdings_adjusted"] is False
    assert audit.corporate_event("owner",**params)["status"]=="existing"
    with pytest.raises(ValueError,match="collision"):
        audit.corporate_event("owner",**{**params,"terms":{"numerator":3}})
    assert audit.overview("owner")["corporate"]==[
        {"category":"SPLIT","status":"PENDING_RECONCILIATION","count":1}]


def test_us_simulated_auto_fx_receipt_and_index_coverage(tmp_path):
    path=tmp_path/"paper.sqlite3"
    ledger=Stage1Ledger(path)
    ledger.state("owner",date(2026,10,17))
    portfolio=Portfolios(path)
    q={"symbol":"VOO","currency":"USD","price":"95.00",
       "status":"ok","tradeable":True,"source":"verified daily US close",
       "as_of":"2026-10-16"}
    first=portfolio.auto_buy_usd_with_fx("owner","VOO",q,"1400","us-auto-00001")
    assert first["status"]=="executed"
    assert portfolio.auto_buy_usd_with_fx(
        "owner","VOO",q,"1400","us-auto-00001")["status"]=="already_executed"
    with portfolio._db() as db:
        record=db.execute("""SELECT foreign_exchange_rate,fee_amount,tax_amount,
            quote_as_of,source FROM stage1_trade_metadata
            WHERE owner='owner' AND order_key='us-auto-00001'""").fetchone()
        assert record==("1400",None,None,"2026-10-16","verified daily US close")
        assert db.execute("""SELECT COUNT(*) FROM stage1_events
            WHERE event_key='us-auto-00001:fx'""").fetchone()[0]==1
        order_indexes={row[1] for row in db.execute("PRAGMA index_list('virtual_orders')")}
        assert "idx_stage1_orders_history" in order_indexes
