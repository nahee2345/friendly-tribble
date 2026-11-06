"""QA-only Stage2 failure-injection and recovery tests.

Every database is a pytest tmp_path file. Synthetic snapshots are NOT live
market prices. SIGKILL is sent only to isolated child Python interpreters.
"""
from __future__ import annotations

import importlib.util
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_verified_execution import execute_verified_paper


FIXTURE_PATH = Path(__file__).with_name(
    "test_stage2_verified_opening_nav_and_atomic_paper.py")
spec = importlib.util.spec_from_file_location("qa_tech_fixture", FIXTURE_PATH)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def _counts(p, owner=fixture.OWNER):
    with p._db() as db:
        result = {}
        for table in ("stage1_turnover_claims", "virtual_orders",
                      "stage1_events", "stage1_trade_metadata",
                      "stage4_execution_claims"):
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            result[table] = (db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE owner=?", (owner,)
            ).fetchone()[0] if exists else 0)
        return result


def _execute(p, request_id, *, account="default", symbol="SPYM",
             quote=None, fx=True):
    return execute_verified_paper(
        p, owner=fixture.OWNER, account=account,
        symbol=symbol, side="BUY", quantity=1,
        request_id=request_id, quote=quote or fixture.US,
        session_date=fixture.DAY,
        fx=fixture.FX if fx else None, auto_fx=fx, now=fixture.NOW)


def _child_kill_after_source_line(path, marker):
    """Child SIGKILL after the previous SQL INSERT, before COMMIT."""
    script = r"""
import importlib.util
import inspect
import os
import signal
import sys
from pathlib import Path
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server import stage1_verified_execution as writer

fpath=Path(sys.argv[2])
spec=importlib.util.spec_from_file_location('qa_child_fixture',fpath)
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
marker=sys.argv[3]
source,first=inspect.getsourcelines(writer.execute_verified_paper)
line_numbers=[first+i for i,line in enumerate(source) if marker in line]
assert len(line_numbers)==1,(marker,line_numbers)
target=line_numbers[0]

def trace(frame,event,arg):
    if (event=='line' and frame.f_code is writer.execute_verified_paper.__code__
            and frame.f_lineno==target):
        os.kill(os.getpid(),signal.SIGKILL)
    return trace

sys.settrace(trace)
writer.execute_verified_paper(
    Portfolios(sys.argv[1],require_verified_p0=True),
    owner=fixture.OWNER,account='default',symbol='SPYM',side='BUY',
    quantity=1,request_id='qa-killed-at-insert',
    quote=fixture.US,fx=fixture.FX,auto_fx=True,
    session_date=fixture.DAY,now=fixture.NOW)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(FIXTURE_PATH), marker],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == -signal.SIGKILL, result.stdout + result.stderr


@pytest.mark.parametrize("next_statement",[
    'if fail_after=="fx":',       # FX event has been inserted, order not yet inserted
    'if fail_after=="fill":',     # FX event and virtual_order inserted
])
def test_sigkill_after_fx_or_order_insert_rolls_back_in_wal(tmp_path, next_statement):
    p, _, _ = fixture.ready(tmp_path)
    with sqlite3.connect(p.path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()=="wal"
    before = _counts(p)
    _child_kill_after_source_line(p.path, next_statement)
    reopened=Portfolios(p.path,require_verified_p0=True)
    assert _counts(reopened)==before
    with sqlite3.connect(p.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert _execute(reopened,"qa-recovered-new-request")["status"]=="executed"
    assert _counts(reopened)["virtual_orders"]==before["virtual_orders"]+1


def test_online_sqlite_backup_restore_preserves_claim_and_prevents_replay(tmp_path):
    p, _, _ = fixture.ready(tmp_path)
    key="qa-backup-committed-001"
    assert _execute(p,key)["status"]=="executed"
    before=_counts(p)
    backup=tmp_path/"qa-backup.sqlite3"
    with sqlite3.connect(p.path) as source,sqlite3.connect(backup) as target:
        source.backup(target)
    backup.chmod(0o600)  # production file-owner permission check must stay enforced
    restored=Portfolios(backup,require_verified_p0=True)
    assert _counts(restored)==before
    assert _execute(restored,key)["status"]=="already_executed"
    assert _counts(restored)==before
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


def test_lost_response_retries_original_id_without_second_fill(tmp_path):
    p, _, _ = fixture.ready(tmp_path)
    key="qa-response-lost-001"
    _execute(p,key)  # Simulated HTTP response loss after transaction commit
    before=_counts(p)
    restarted=Portfolios(p.path,require_verified_p0=True)
    assert _execute(restarted,key)["status"]=="already_executed"
    assert _counts(restarted)==before


def test_other_owner_cannot_spend_first_users_nav_or_read_orders(tmp_path):
    p, _, proof = fixture.ready(tmp_path)
    assert proof["scope"]==["default","kr-base"]
    assert _execute(p,"qa-first-owner-only-1")["status"]=="executed"
    before=_counts(p)
    other="qa-other-owner"
    with pytest.raises(ValueError):
        execute_verified_paper(
            p,owner=other,account="default",symbol="SPYM",side="BUY",
            quantity=1,request_id="qa-other-owner-order-1",
            quote=fixture.US,fx=fixture.FX,auto_fx=True,
            session_date=fixture.DAY,now=fixture.NOW)
    assert _counts(p)==before
    assert _counts(p,other)=={k:0 for k in before}
    with pytest.raises(ValueError, match="계좌 없음"):
        p.orders(other,"default")  # no implicit account creation or cross-owner read
