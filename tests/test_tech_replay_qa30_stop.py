# Replayed unchanged QA-team PR #30 acceptance checks against technical candidate.
# Upstream test blob SHA: 349fb7e8b62abcc1978147a9fb4e784d593104a4
# Running this from technical CI does NOT imply independent QA acceptance.
"""QA-owned, independent stop/restart contract on temporary SQLite only.

No production DB, keyring, network quotes, broker or PAPER automation.
"""
from __future__ import annotations
import sqlite3
import subprocess
import sys

import pytest

from wealthy_server.stage1_controls import set_paused
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage4_portfolio import PortfolioEngine
from wealthy_server.p0_paper_execution_readiness import paper_execution_readiness

OWNER = "qa-stage2-owner"
QUOTE = {"symbol":"069500","currency":"KRW","price":"20000",
         "status":"ok","tradeable":True,"source":"qa-synthetic-only",
         "as_of":"2026-11-04"}

def _count(db,table):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                  (table,)).fetchone() is None:
        return 0
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

def _counts(p):
    with p._db() as db:
        return {t:_count(db,t) for t in (
            "virtual_orders", "stage1_events", "stage1_turnover_claims",
            "stage4_execution_claims", "stage1_approvals")}

@pytest.mark.parametrize("control",["missing","other_owner","paused","invalid"])
def test_no_control_permission_never_writes(tmp_path,control):
    p=Portfolios(tmp_path/"qa-no-control.sqlite3")
    if control=="other_owner":
        set_paused("different-owner",p,False,"isolated QA")
    elif control in ("paused","invalid"):
        set_paused(OWNER,p,True if control=="paused" else False,"isolated QA")
        if control=="invalid":
            with p._db() as db:
                db.execute("PRAGMA ignore_check_constraints=ON")
                db.execute("UPDATE stage1_trade_control SET paused=2 WHERE owner=?",(OWNER,))
    before=_counts(p)
    with pytest.raises(ValueError,match="긴급 중지"):
        p.trade(OWNER,"kr-base","069500","BUY",1,
                "qa-stop-order-0001",QUOTE)
    assert _counts(p)==before

def test_explicit_stop_is_durable_across_new_process(tmp_path):
    path=tmp_path/"qa-pause-restart.sqlite3"
    p=Portfolios(path)
    set_paused(OWNER,p,False,"isolated QA setup")
    set_paused(OWNER,p,True,"QA simulated user stop")
    before=_counts(p)
    code=(
        "from wealthy_server.stage1_portfolios import Portfolios\n"
        "p=Portfolios(__import__('sys').argv[1])\n"
        "with p._db() as db:\n"
        "  db.execute('BEGIN IMMEDIATE')\n"
        "  try:\n"
        "    p._assert_not_paused(db,'qa-stage2-owner')\n"
        "  except ValueError as e:\n"
        "    assert '긴급 중지' in str(e)\n"
        "  else:\n"
        "    raise AssertionError('stop bypass')\n"
        "  db.execute('ROLLBACK')\n"
    )
    result=subprocess.run([sys.executable,"-c",code,str(path)],
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0, result.stdout+"\n"+result.stderr
    assert _counts(Portfolios(path))==before

def test_no_verified_nav_cannot_release_stage4(tmp_path):
    from datetime import datetime,timezone
    p=Portfolios(tmp_path/"qa-stage4-block.sqlite3")
    set_paused(OWNER,p,False,"isolated QA, not authorization")
    before=_counts(p)
    result=PortfolioEngine(p,None,None).run(
        OWNER,now=datetime(2026,11,4,12,0,tzinfo=timezone.utc))
    assert result["status"]=="session_unverified"
    assert result["executed"]==[]
    assert _counts(p)==before

def test_readiness_is_denial_only_and_never_fake_nav():
    result=paper_execution_readiness()
    assert result["verified"] is False
    assert result["code"]=="UNVERIFIED_SESSION_OPENING_NAV"
