"""Offline controls, approval and seven-card paper-stage acceptance checks."""
from datetime import date
from pathlib import Path

import pytest

from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_controls import (
    status, set_paused, request_approval, list_approvals, decide_approval)
from wealthy_server.stage1_discovery import PreferenceStore
from wealthy_server.stage1_strategy_policy import StrategyPolicy, DEFAULT_WEIGHTS
from wealthy_server.stage1_daily_cards import generate_cards


def environment(tmp_path: Path):
    path=tmp_path/"stage1-paper.sqlite3"
    ledger=Stage1Ledger(path)
    portfolios=Portfolios(path)
    initialize_paper_accounts("owner",ledger,portfolios,today=date(2026,10,17))
    return ledger,portfolios,PreferenceStore(path),StrategyPolicy(portfolios)


def test_default_stop_survives_restart(tmp_path: Path):
    ledger,p,prefs,policy=environment(tmp_path)
    assert status("owner",p)["paused"] is True
    set_paused("owner",p,False,"모의운용 허용")
    assert status("owner",Portfolios(p.path))["paused"] is False
    set_paused("owner",p,True,"긴급 정지")
    assert status("owner",Portfolios(p.path))["paused"] is True


def test_approval_is_expiring_and_not_executing_orders(tmp_path: Path):
    ledger,p,prefs,policy=environment(tmp_path)
    key="approval-request-001"
    params=dict(request_id=key,account="default",symbol="VOO",side="BUY",
                quantity=1,amount_krw=310000,rationale="가상 주문 비용과 위험 확인")
    request_approval("owner",p,**params)
    request_approval("owner",p,**params)
    assert len(list_approvals("owner",p)["items"])==1
    assert list_approvals("other",p)["items"]==[]
    approved=decide_approval("owner",p,key,True)
    assert approved["items"][0]["status"]=="APPROVED"
    assert p.orders("owner","default")["orders"]==[]
    with pytest.raises(ValueError):
        decide_approval("owner",p,key,True)


def test_strategy_cooldown_and_same_policy_idempotency(tmp_path: Path):
    ledger,p,prefs,policy=environment(tmp_path)
    first={k:(100 if k=="momentum" else 0) for k in DEFAULT_WEIGHTS}
    policy.save("owner",first)
    assert policy.save("owner",first)["weights"]==first
    second={k:(100 if k=="index" else 0) for k in DEFAULT_WEIGHTS}
    with pytest.raises(ValueError):
        policy.save("owner",second)
    assert policy.get("owner")["weights"]==first


def test_seven_cards_are_complete_structure_not_fake_market_values(tmp_path: Path):
    ledger,p,prefs,policy=environment(tmp_path)
    cards=generate_cards("owner",p,ledger,prefs,policy,today=date(2026,10,17))
    assert cards["kind"]=="PREVIEW"
    assert len(cards["cards"])==7
    assert [c["id"] for c in cards["cards"]]==list(range(1,8))
    dividend=cards["cards"][3]
    assert dividend["data"]["verified_dividend_krw"] is None
    assert cards["cards"][0]["data"]["paused"] is True


def test_emergency_pause_blocks_direct_db_order(tmp_path: Path):
    ledger,p,prefs,policy=environment(tmp_path)
    set_paused("owner",p,True,"긴급중지")
    quote={"symbol":"069500","currency":"KRW","price":"10000","status":"ok",
           "tradeable":True,"source":"offline test","as_of":"2026-10-16"}
    with pytest.raises(ValueError,match="긴급 중지"):
        p.trade("owner","kr-base","069500","BUY",1,"blocked-order-001",quote)
    assert p.orders("owner","kr-base")["orders"]==[]
    set_paused("owner",p,False,"사용자 모의투자 재개")
    p.trade("owner","kr-base","069500","BUY",1,"allowed-order-001",quote)
    assert len(p.orders("owner","kr-base")["orders"])==1


def test_daily_16_card_snapshot_is_idempotent_and_marks_late(tmp_path: Path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from wealthy_server.stage1_daily_cards import (
        store_daily_cards_after_16,list_saved_daily_cards)
    ledger,p,prefs,policy=environment(tmp_path)
    before=datetime(2026,10,17,15,50,tzinfo=ZoneInfo("Asia/Seoul"))
    after=datetime(2026,10,17,17,10,tzinfo=ZoneInfo("Asia/Seoul"))
    assert store_daily_cards_after_16("owner",p,ledger,prefs,policy,now=before)["status"]=="not_due"
    first=store_daily_cards_after_16("owner",p,ledger,prefs,policy,now=after)
    assert first["status"]=="saved"
    assert first["delayed"] is True
    assert store_daily_cards_after_16("owner",p,ledger,prefs,policy,now=after)["status"]=="already_saved"
    reports=list_saved_daily_cards("owner",p)["items"]
    assert len(reports)==1
    assert reports[0]["late_generation"] is True
    assert reports[0]["for_date"]=="2026-10-17"
    assert list_saved_daily_cards("other",p)["items"]==[]


def test_approved_paper_order_is_filled_only_on_explicit_repeat(tmp_path: Path):
    ledger,p,prefs,policy=environment(tmp_path)
    req="approval-fulfillment-01"
    request_approval("owner",p,request_id=req,account="kr-base",
        symbol="069500",side="BUY",quantity=1,amount_krw=310000,
        rationale="비용 검증 완료 후 별도 체결")
    decide_approval("owner",p,req,True)
    before=list_approvals("owner",p)["items"][0]
    assert before["executed"] is False
    assert not p.orders("owner","kr-base")["orders"]
    quote={"status":"ok","tradeable":True,"symbol":"069500","currency":"KRW",
           "price":"10000","source":"fixture","as_of":"2026-10-16"}
    set_paused("owner",p,False,"approved paper execution")
    p.trade("owner","kr-base","069500","BUY",1,req,quote)
    after=list_approvals("owner",p)["items"][0]
    assert after["executed"] is True


def test_approved_paper_order_expires_after_24h(tmp_path: Path):
    ledger,p,prefs,policy=environment(tmp_path)
    req="approval-expiry-0001"
    request_approval("owner",p,request_id=req,account="default",
       symbol="VOO",side="BUY",quantity=1,amount_krw=310000,rationale="기간 확인")
    decide_approval("owner",p,req,True)
    with p._db() as db:
        db.execute("UPDATE stage1_approvals SET expires_at='2020-01-01T00:00:00+00:00' WHERE owner=? AND request_id=?",
                   ("owner",req))
    assert list_approvals("owner",p)["items"][0]["status"]=="EXPIRED"
