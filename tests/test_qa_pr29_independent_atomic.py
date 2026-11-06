"""QA PR29: independent assertions on isolated P0 writer and crash recovery.

Synthetic fixture setup is borrowed from the tech's known ledger setup ONLY;
all behavioral checks and child process failure injection below are QA-owned.
No user DB, live quotes, KIS, or actual PAPER activation.
"""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal

import pytest

from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_verified_execution import execute_verified_paper

FIXTURE_PATH = Path(__file__).with_name("test_stage2_verified_opening_nav_and_atomic_paper.py")
spec = importlib.util.spec_from_file_location("qa_pr29_fixture", FIXTURE_PATH)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def _ledger(p):
    with p._db() as db:
        rows = {}
        for table in ("stage1_turnover_claims", "virtual_orders",
                      "stage1_events", "stage1_trade_metadata",
                      "stage4_execution_claims"):
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            rows[table] = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE owner=?",
                (fixture.OWNER,)).fetchone()[0] if exists else 0
        return rows


def _order(p, key, quantity=1, *, account="kr-base", quote=None):
    return execute_verified_paper(
        p, owner=fixture.OWNER, account=account, symbol="069500",
        side="BUY", quantity=quantity, request_id=key,
        quote=quote or fixture.KR, session_date=fixture.DAY,
        now=fixture.NOW)


def test_qa_exact_20pct_all_accounts_and_denial_no_write(tmp_path):
    p, _, proof = fixture.ready(tmp_path)
    assert Decimal(proof["nav_krw"]) == Decimal("4000000")
    for i in range(4):
        assert _order(p, f"qa-cap-{i:04d}", 20)["status"] == "executed"
    before = _ledger(p)
    assert before["stage1_turnover_claims"] == 4
    with pytest.raises(ValueError, match="GROSS_TURNOVER_CAP_EXCEEDED"):
        _order(p, "qa-cap-over-0001", 1)
    assert _ledger(p) == before
    with p._db() as db:
        total = sum((Decimal(v[0]) for v in db.execute(
            "SELECT gross_krw FROM stage1_turnover_claims WHERE owner=?",
            (fixture.OWNER,))), Decimal(0))
    assert total == Decimal("800000")


def test_qa_different_account_key_collision_and_exact_replay(tmp_path):
    p, _, _ = fixture.ready(tmp_path)
    assert _order(p, "qa-shared-key-001")["status"] == "executed"
    before = _ledger(p)
    assert _order(p, "qa-shared-key-001")["status"] == "already_executed"
    assert _ledger(p) == before
    with pytest.raises(ValueError, match="CONFLICTING_OWNER_REQUEST_ID"):
        execute_verified_paper(
            p, owner=fixture.OWNER, account="default", symbol="SPYM",
            side="BUY", quantity=1, request_id="qa-shared-key-001",
            quote=fixture.US, fx=fixture.FX, auto_fx=True,
            session_date=fixture.DAY, now=fixture.NOW)
    assert _ledger(p) == before


def test_qa_threshold_exactly_300000_rejects_without_approval(tmp_path):
    p, _, _ = fixture.ready(tmp_path)
    before = _ledger(p)
    with pytest.raises(ValueError, match="APPROVAL_REQUIRED_300000_KRW"):
        _order(p, "qa-approval-300k", 30)
    assert _ledger(p) == before


def test_qa_crash_after_reserve_rolls_back_all_writer_rows(tmp_path):
    p, _, _ = fixture.ready(tmp_path)
    before = _ledger(p)
    child_script = r"""
import importlib.util, os, signal, sys
from pathlib import Path
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server import stage1_verified_execution as writer
path=Path(sys.argv[1])
source=Path(sys.argv[2])
spec=importlib.util.spec_from_file_location('qa_child_fixture',source)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
original=writer.reserve
def killed_after_reservation(*args,**kwargs):
    result=original(*args,**kwargs)
    os.kill(os.getpid(),signal.SIGKILL)
    return result
writer.reserve=killed_after_reservation
writer.execute_verified_paper(Portfolios(path),owner=mod.OWNER,account='kr-base',
    symbol='069500',side='BUY',quantity=20,request_id='qa-crash-reserve-1',
    quote=mod.KR,session_date=mod.DAY,now=mod.NOW)
"""
    child = subprocess.run(
        [sys.executable, "-c", child_script, str(p.path), str(FIXTURE_PATH)],
        capture_output=True, text=True, timeout=30)
    assert child.returncode == -signal.SIGKILL, child.stderr
    reopened = Portfolios(p.path, require_verified_p0=True)
    assert _ledger(reopened) == before
    assert _order(reopened, "qa-after-crash-1")["status"] == "executed"


def _parallel_writer(args):
    dbpath, key = args
    try:
        p = Portfolios(dbpath, require_verified_p0=True)
        return _order(p, key, 20)["status"]
    except ValueError as exc:
        return str(exc)


def test_qa_cross_process_overlapping_cap_and_reopen(tmp_path):
    p, _, _ = fixture.ready(tmp_path)
    with ProcessPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(
            _parallel_writer,
            [(str(p.path), f"qa-parallel-{i:04d}") for i in range(5)]))
    assert results.count("executed") == 4, results
    assert all(x == "executed" or
               "GROSS_TURNOVER_CAP_EXCEEDED" in x for x in results), results
    reopened = Portfolios(p.path, require_verified_p0=True)
    assert _ledger(reopened)["virtual_orders"] == 4
    assert _ledger(reopened)["stage1_turnover_claims"] == 4


def test_qa_approval_299999_allowed_and_300001_denied(tmp_path):
    first = tmp_path / "under"
    first.mkdir()
    p, _, _ = fixture.ready(first)
    assert _order(p, "qa-under-300k-1", quote={
        **fixture.KR, "price": "299999"})["status"] == "executed"
    second = tmp_path / "over"
    second.mkdir()
    p, _, _ = fixture.ready(second)
    before = _ledger(p)
    with pytest.raises(ValueError, match="APPROVAL_REQUIRED_300000_KRW"):
        _order(p, "qa-over-300k-1", quote={
            **fixture.KR, "price": "300001"})
    assert _ledger(p) == before


def test_qa_approval_exact_expiry_is_not_valid(tmp_path):
    from wealthy_server.stage1_controls import request_approval, decide_approval
    p, _, _ = fixture.ready(tmp_path)
    key = "qa-expires-at-boundary"
    request_approval(fixture.OWNER, p, request_id=key,
                     account="kr-base", symbol="069500", side="BUY",
                     quantity=30, amount_krw=300000,
                     rationale="QA synthetic expiry boundary")
    decide_approval(fixture.OWNER, p, key, True)
    with p._db() as db:
        db.execute("UPDATE stage1_approvals SET expires_at=? "
                   "WHERE owner=? AND request_id=?",
                   (fixture.NOW.isoformat(), fixture.OWNER, key))
    before = _ledger(p)
    with pytest.raises(ValueError, match="APPROVAL_SCOPE_AMOUNT_OR_EXPIRY_MISMATCH"):
        _order(p, key, 30)
    assert _ledger(p) == before


def test_qa_unknown_survives_restart_and_consumes_cap(tmp_path):
    from wealthy_server.stage1_turnover_reservations import reserve, mark_unknown
    p, _, _ = fixture.ready(tmp_path)
    key = "qa-unknown-reservation-1"
    with p._db() as db:
        db.execute("BEGIN IMMEDIATE")
        reserve(db, owner=fixture.OWNER, session_date=fixture.DAY,
                request_id=key, account="kr-base", symbol="069500",
                side="BUY", quantity=79, gross_krw="790000",
                now_utc=fixture.NOW.isoformat())
        mark_unknown(db, fixture.OWNER, key, fixture.NOW.isoformat())
        db.execute("COMMIT")
    reopened = Portfolios(p.path, require_verified_p0=True)
    before = _ledger(reopened)
    with pytest.raises(ValueError, match="RESERVATION_RECONCILIATION_REQUIRED"):
        _order(reopened, key, 79)
    with pytest.raises(ValueError, match="GROSS_TURNOVER_CAP_EXCEEDED"):
        _order(reopened, "qa-another-after-unknown", 2)
    assert _ledger(reopened) == before
    with reopened._db() as db:
        row = db.execute(
            "SELECT state FROM stage1_turnover_claims "
            "WHERE owner=? AND request_id=?", (fixture.OWNER, key)).fetchone()
    assert row == ("UNKNOWN",)


def test_qa_sigkill_after_commit_replay_no_second_fill(tmp_path):
    p, _, _ = fixture.ready(tmp_path)
    child_script = r"""
import importlib.util, os, signal, sys
from pathlib import Path
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_verified_execution import execute_verified_paper
source=Path(sys.argv[2])
spec=importlib.util.spec_from_file_location('qa_after_commit_fixture',source)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
execute_verified_paper(Portfolios(sys.argv[1],require_verified_p0=True),
    owner=mod.OWNER, account='kr-base', symbol='069500',side='BUY',
    quantity=1,request_id='qa-after-commit-001',quote=mod.KR,
    session_date=mod.DAY,now=mod.NOW)
os.kill(os.getpid(),signal.SIGKILL)
"""
    child = subprocess.run(
        [sys.executable, "-c", child_script, str(p.path), str(FIXTURE_PATH)],
        capture_output=True, text=True, timeout=30)
    assert child.returncode == -signal.SIGKILL, child.stderr
    reopened = Portfolios(p.path, require_verified_p0=True)
    before = _ledger(reopened)
    assert before["virtual_orders"] == 1
    assert before["stage1_turnover_claims"] == 1
    assert _order(reopened, "qa-after-commit-001")["status"] == "already_executed"
    assert _ledger(reopened) == before
