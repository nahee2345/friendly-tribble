"""P0 release barrier: a calendar or unaudited NAV string is not trade authorization.

All tests use disposable SQLite databases. No external quotes or broker calls.
"""
from datetime import datetime, timezone

from wealthy_server.p0_paper_execution_readiness import paper_execution_readiness
from wealthy_server.stage1_controls import set_paused
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage4_portfolio import PortfolioEngine


def test_unverified_session_is_not_runtime_permission():
    readiness = paper_execution_readiness()
    assert readiness["verified"] is False
    assert readiness["code"] == "UNVERIFIED_SESSION_OPENING_NAV"


def test_stage4_does_not_create_claims_or_fills_when_nav_unverified(tmp_path):
    p = Portfolios(tmp_path / "unverified-stage4.sqlite3")
    set_paused("owner", p, False, "isolated fixture only")
    # No preferences, stored quotes or authenticated opening NAV are provided.
    # The release gate must run *before* any planning or market-data lookup.
    engine = PortfolioEngine(p, None, None)
    result = engine.run("owner", now=datetime(2026, 11, 4, 12, 0,
                                             tzinfo=timezone.utc))
    assert result["status"] == "session_unverified"
    assert result["executed"] == []
    assert result["trade_enabled"] is False
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0] == 0
        claim_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='stage4_execution_claims'"
        ).fetchone()
        assert claim_table is None
