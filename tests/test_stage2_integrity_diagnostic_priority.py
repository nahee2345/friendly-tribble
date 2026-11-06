"""Regression: ledger corruption must be shown before generic NAV denial.

All writes are confined to tmp_path. No production readiness toggle exists.
"""
from datetime import date, datetime, timezone

from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_controls import set_paused
from wealthy_server.stage1_market_store import MarketStore
from wealthy_server.stage1_discovery import PreferenceStore
from wealthy_server.stage4_portfolio import PortfolioEngine, _schema
from wealthy_server.stage7_release import audit_paper_ledger


def _fixture(tmp_path,monkeypatch):
    import wealthy_server.stage4_portfolio as stage4
    # Calendar policy is not what this test exercises; pin to a regular weekday
    # without modifying any persisted transaction timestamps.
    monkeypatch.setattr(stage4,"_signed_day",lambda _now:date(2026,10,19))
    p=Portfolios(tmp_path/"paper.sqlite3")
    initialize_paper_accounts("owner",Stage1Ledger(p.path),p,today=date.today())
    set_paused("owner",p,False,"isolated integrity priority regression")
    def no_execution_quote(_symbol):
        raise AssertionError("release denial must not access provider")
    engine=PortfolioEngine(p,PreferenceStore(p.path),
                           MarketStore.for_portfolios(p),
                           quote_provider=no_execution_quote)
    return p,engine


def test_corrupt_owner_ledger_precedes_session_gate_without_any_fill(tmp_path,monkeypatch):
    p,engine=_fixture(tmp_path,monkeypatch)
    stamp=datetime.now(timezone.utc).isoformat()
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        db.execute("""INSERT INTO stage4_execution_claims VALUES
           (?,?,?,?,?,?,?,?,?,?,?,?)""",(
            "owner","integrity-interrupted-001","8"*64,"kr-base",
            "069500","BUY",1,"10000","CLAIMED","SIMULATED_CRASH",
            stamp,stamp))
        db.execute("COMMIT")
    status=audit_paper_ledger("owner",p)
    assert status["blocking_count"]>0
    assert "INTERRUPTED_EXECUTION_CLAIM" in {
        issue["code"] for issue in status["issues"]}
    result=engine.run("owner",now=datetime.now(timezone.utc))
    assert result["status"]=="integrity_blocked"
    assert "INTERRUPTED_EXECUTION_CLAIM" in {
        issue["code"] for issue in result["issues"]}
    assert not result["executed"]
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims").fetchone()[0]==0
        row=db.execute("""SELECT status FROM stage4_execution_claims
                           WHERE request_id=?""",
                       ("integrity-interrupted-001",)).fetchone()
        assert row==("CLAIMED",)


def test_healthy_owner_still_denied_without_market_nav(tmp_path,monkeypatch):
    p,engine=_fixture(tmp_path,monkeypatch)
    status=audit_paper_ledger("owner",p)
    assert status["blocking_count"]==0,status
    result=engine.run("owner",now=datetime.now(timezone.utc))
    assert result["status"]=="session_unverified"
    assert result["executed"]==[]
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims").fetchone()[0]==0
