"""P0: durable PAPER stop is fail-closed at the SQLite writer boundary.

Only ephemeral temporary databases; no provider, broker, user database or keyring.
"""
from pathlib import Path

import pytest

from wealthy_server.stage1_controls import schema, set_paused, status
from wealthy_server.stage1_portfolios import Portfolios

OWNER = "isolated-owner"
KR_QUOTE = {
    "symbol": "069500", "currency": "KRW", "price": "20000",
    "status": "ok", "tradeable": True, "source": "synthetic-test",
    "as_of": "2026-11-04"
}
US_QUOTE = {
    "symbol": "SPYM", "currency": "USD", "price": "50",
    "status": "ok", "tradeable": True, "source": "synthetic-test",
    "as_of": "2026-11-04"
}


def isolated(tmp_path: Path) -> Portfolios:
    return Portfolios(tmp_path / "p0-owner-test.sqlite3")


def try_paper_writes(p: Portfolios) -> None:
    with pytest.raises(ValueError, match="긴급 중지"):
        p.trade(
            OWNER, "kr-base", "069500", "BUY", 1,
            "stop-direct-order-0001", KR_QUOTE
        )
    with pytest.raises(ValueError, match="긴급 중지"):
        p.auto_buy_usd_with_fx(
            OWNER, "SPYM", US_QUOTE, "1400", "stop-auto-fx-0001"
        )
    with p._db() as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM stage1_events").fetchone()[0] == 0


def test_stop_table_absent_and_owner_row_absent_both_block(tmp_path):
    p = isolated(tmp_path)
    try_paper_writes(p)
    # Even after the table exists, another owner's opt-in never enables us.
    with p._db() as db:
        schema(db)
    assert status(OWNER, p)["paused"] is True
    set_paused("another-owner", p, False, "fixture only")
    try_paper_writes(p)


def test_explicit_pause_blocks_even_after_restart(tmp_path):
    p = isolated(tmp_path)
    set_paused(OWNER, p, False, "synthetic test only")
    assert status(OWNER, p)["paused"] is False
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        p._assert_not_paused(db, OWNER)
        db.execute("ROLLBACK")

    set_paused(OWNER, p, True, "stop all PAPER fills")
    reopened = Portfolios(p.path)
    assert status(OWNER, reopened)["paused"] is True
    try_paper_writes(reopened)


def test_invalid_control_value_never_becomes_permission(tmp_path):
    p = isolated(tmp_path)
    set_paused(OWNER, p, False, "fixture only")
    with p._db() as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute("UPDATE stage1_trade_control SET paused=2 WHERE owner=?", (OWNER,))
    assert status(OWNER, p)["paused"] is True
    assert status(OWNER, p)["state_verified"] is False
    try_paper_writes(p)
