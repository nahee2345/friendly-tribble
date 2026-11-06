"""QA-only real TestClient logout and approval boundary/revocation races.
Uses temporary local DBs and synthetic NAV; never enables production PAPER.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib.util
from pathlib import Path
import threading

import pytest
from fastapi.testclient import TestClient

from wealthy_server.stage1_controls import (
    request_approval, decide_approval, set_paused,
)
from wealthy_server.stage1_verified_execution import execute_verified_paper
from test_qa_p0_api_ledger import local_api, funds

FIXTURE_PATH=Path(__file__).with_name("test_stage2_verified_opening_nav_and_atomic_paper.py")
spec=importlib.util.spec_from_file_location("qa_approval_fixture",FIXTURE_PATH)
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def _count_orders(p, owner=fixture.OWNER):
    with p._db() as db:
        return db.execute(
            "SELECT COUNT(*) FROM virtual_orders WHERE owner=?",(owner,)
        ).fetchone()[0]


def _approved(p,key,quantity=30):
    import wealthy_server.stage1_controls as controls
    original=controls.now_utc
    controls.now_utc=lambda:fixture.NOW
    try:
        request_approval(fixture.OWNER,p,request_id=key,account="kr-base",
            symbol="069500",side="BUY",quantity=quantity,
            amount_krw=quantity*10000,rationale="QA expiry fixture")
        decide_approval(fixture.OWNER,p,key,True)
    finally:
        controls.now_utc=original


def _buy(p,key,quantity=30):
    return execute_verified_paper(p,owner=fixture.OWNER,account="kr-base",
        symbol="069500",side="BUY",quantity=quantity,request_id=key,
        quote=fixture.KR,session_date=fixture.DAY,now=fixture.NOW)


def test_logout_protected_api_401_with_stolen_old_session_cookie(local_api):
    client,post,paper,app,*_ = local_api
    protected="/api/v1/stage1/portfolios"
    before=client.get(protected)
    assert before.status_code == 200,before.text
    old_cookies=dict(client.cookies)
    logout=post("/api/v1/logout",{})
    assert logout.status_code==200,logout.text
    assert logout.json().get("ok") is True
    after=client.get(protected)
    assert after.status_code==401,after.text
    assert "total_scope" not in after.text
    copied=TestClient(app,base_url="http://localhost")
    try:
        copied.cookies.update(old_cookies)
        replay=copied.get(protected)
        assert replay.status_code==401,replay.text
        assert "total_scope" not in replay.text
        order=copied.get("/api/v1/stage1/portfolios/kr-base/orders")
        assert order.status_code==401,order.text
    finally:
        copied.close()


def test_real_approval_creation_uses_exact_24h_and_rejection_prevents_fill(tmp_path,monkeypatch):
    p,_,_=fixture.ready(tmp_path)
    monkeypatch.setattr("wealthy_server.stage1_controls.now_utc",lambda:fixture.NOW)
    key="qa-24h-actual-creation"
    request_approval(fixture.OWNER,p,request_id=key,account="kr-base",
        symbol="069500",side="BUY",quantity=30,amount_krw=300000,
        rationale="QA 24h")
    with p._db() as db:
        row=db.execute(
            "SELECT created_at,expires_at,status FROM stage1_approvals "
            "WHERE owner=? AND request_id=?",(fixture.OWNER,key)).fetchone()
    from datetime import datetime
    assert datetime.fromisoformat(row[1])-datetime.fromisoformat(row[0])==timedelta(hours=24)
    assert row[2]=="PENDING"
    decide_approval(fixture.OWNER,p,key,False)
    before=_count_orders(p)
    with pytest.raises(ValueError,match="APPROVAL_SCOPE_AMOUNT_OR_EXPIRY_MISMATCH"):
        _buy(p,key)
    assert _count_orders(p)==before


def test_approved_request_altered_to_rejected_before_writer_commit_blocks(tmp_path):
    p,_,_=fixture.ready(tmp_path)
    key="qa-approval-condition-revoked"
    _approved(p,key)
    with p._db() as db:
        db.execute("UPDATE stage1_approvals SET status='REJECTED' "
                   "WHERE owner=? AND request_id=?",(fixture.OWNER,key))
    before=_count_orders(p)
    with pytest.raises(ValueError,match="APPROVAL_SCOPE_AMOUNT_OR_EXPIRY_MISMATCH"):
        _buy(p,key)
    assert _count_orders(p)==before


def test_concurrent_rejected_approval_and_writer_are_serialized(tmp_path):
    p,_,_=fixture.ready(tmp_path)
    key="qa-approved-condition-race"
    _approved(p,key)
    start=threading.Barrier(2)
    def writer():
        start.wait()
        try:
            return _buy(p,key)["status"]
        except ValueError as exc:
            return str(exc)
    def reject():
        start.wait()
        with p._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE stage1_approvals SET status='REJECTED' "
                       "WHERE owner=? AND request_id=?",(fixture.OWNER,key))
            db.execute("COMMIT")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(writer)
        b=pool.submit(reject)
        result=a.result(timeout=10)
        b.result(timeout=10)
    assert result=="executed" or "APPROVAL_SCOPE_AMOUNT_OR_EXPIRY_MISMATCH" in result
    assert _count_orders(p) in (0,1)
    with p._db() as db:
        claims=db.execute(
            "SELECT COUNT(*) FROM stage1_turnover_claims "
            "WHERE owner=? AND request_id=?",(fixture.OWNER,key)).fetchone()[0]
    assert claims==_count_orders(p)
