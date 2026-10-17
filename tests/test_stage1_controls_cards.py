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
