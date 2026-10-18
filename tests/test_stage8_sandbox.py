"""Stage8 paper sandbox end-to-end, real engine and complete no-user-data boundary."""
from __future__ import annotations
from datetime import datetime,date,timezone,timedelta
from decimal import Decimal
import os
from pathlib import Path

import pytest

from wealthy_server.stage8_sandbox import (
    INITIAL_KRW, DEMO_OWNER, SYMBOLS, next_simulated_session,
    _uncommitted_scenario, sandbox_preview)


def test_next_session_after_kst_sunday_and_reference_is_previous_friday():
    now=datetime(2026,10,18,9,45,tzinfo=timezone.utc)
    run,close=next_simulated_session(now)
    assert run.astimezone(timezone.utc).date()==date(2026,10,19)
    assert run.weekday()==0
    assert close==date(2026,10,16)
    assert run.astimezone(timezone.utc).hour==10


def test_next_session_rolls_over_weekend_and_after_daily_cutoff():
    run,close=next_simulated_session(datetime(2026,10,16,11,0,
                                              tzinfo=timezone.utc))
    assert run.date()==date(2026,10,19)
    assert close==date(2026,10,16)


def test_4m_sandbox_actual_stage4_buy_and_idempotency():
    run,close=next_simulated_session()
    result=_uncommitted_scenario(run,close)
    assert result["status"]=="completed_sandbox_only",result
    assert result["initial_cash_krw"]=="4000000"
    assert result["strategy_example"]["weights"]["index"]==100
    assert result["strategy_example"]["allocation"]["etf_weight"]==100
    assert result["provider_network_used"] is False
    assert result["market_closed_check"]=={"status":"market_closed","orders_executed":0}
    sample=result["weekday_replay"]
    assert sample["status"]=="completed"
    assert sample["integrity"]=="healthy"
    assert sample["funding_reconciled"] is True
    assert sample["repeat_run_new_orders"]==0
    assert Decimal(sample["estimated_spend_krw"])<=Decimal(200000)
    assert Decimal(sample["estimated_spend_krw"])>0
    assert {x["symbol"] for x in sample["orders"]}=={"VOO","069500"}
    executed={x["symbol"]:x for x in sample["orders"]}
    assert executed["VOO"]["account"]=="default" and executed["VOO"]["quantity"]==1
    assert executed["069500"]["account"]=="isa-base" and executed["069500"]["quantity"]==4
    assert Decimal(executed["VOO"]["krw_notional"])==112000
    assert Decimal(executed["069500"]["krw_notional"])==80000
    assert Decimal(sample["estimated_spend_krw"])==192000
    assert all(x["side"]=="BUY" for x in sample["orders"])
    assert all(x["strategy_score"]>=70 and x["coverage_pct"]==100
               for x in sample["orders"])
    positions={x["symbol"]:x["quantity"] for acc in sample["ending_accounts"]
               for x in acc["positions"]}
    assert set(positions)=={"VOO","069500"}
    assert all(v>0 for v in positions.values())


def test_fixture_pricing_never_claimed_as_verified_live_close():
    result=sandbox_preview()
    assert result["trust_label"].startswith("합성 시세")
    assert result["active_user_ledger_touched"] is False
    assert result["simulated_orders_exported"] is False
    assert result["broker_used"] is False and result["ai_used"] is False
    assert result["price_fixture"]["VOO"]["USD"]=="80"
    assert result["price_fixture"]["069500"]["KRW"]=="20000"
    assert result["price_fixture"]["USD/KRW"]=="1400"
    assert result["weekday_replay"]["status"]=="completed"
    assert "다음 거래일" in " ".join(result["important"])


def test_sandbox_never_creates_permanent_owner_files(tmp_path,monkeypatch):
    # Independent fixture SQL only; simulate an unrelated user's live
    # recording path and make sure sandbox does not mutate that SQLite file.
    from wealthy_server.stage1_paper import Stage1Ledger
    from wealthy_server.stage1_portfolios import Portfolios
    from wealthy_server.stage1_bootstrap import initialize_paper_accounts
    active=tmp_path/"active-paper.sqlite3"
    l=Stage1Ledger(active)
    p=Portfolios(active)
    initialize_paper_accounts("actual-owner",l,p,today=date(2026,10,18))
    from wealthy_server.stage7_release import audit_paper_ledger
    before=audit_paper_ledger("actual-owner",p)
    _uncommitted_scenario(*next_simulated_session())
    after=audit_paper_ledger("actual-owner",p)
    assert before["counts"]["cash_balances"]==after["counts"]["cash_balances"]
    assert before["counts"]["orders"]==after["counts"]["orders"]==0
    assert "STAGE8_SYNTHETIC_SANDBOX_QUOTE" not in active.read_bytes().decode("utf-8","ignore")


def test_production_owner_hold_and_defaults_remain_frozen():
    from wealthy_server.stage1_strategy_policy import DEFAULT_WEIGHTS
    assert INITIAL_KRW==4000000
    assert DEMO_OWNER!="owner" and DEMO_OWNER!="alice"
    assert DEFAULT_WEIGHTS["index"]==30
    assert sum(DEFAULT_WEIGHTS.values())==100
    assert len(SYMBOLS)==2


def test_no_external_request_even_if_internet_blocked(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,"urlopen",
                        lambda *_a,**_kw:pytest.fail("sandbox initiated HTTP"))
    result=_uncommitted_scenario(*next_simulated_session())
    assert result["provider_network_used"] is False


def test_example_has_no_permanent_orders_or_unaudited_funding():
    result=_uncommitted_scenario(*next_simulated_session())
    start=result["starting_accounts"]
    assert sum(Decimal(a["cash_krw"]) for a in start)==Decimal(4000000)
    assert all(not a["positions"] for a in start)
    assert result["weekday_replay"]["integrity"]=="healthy"
    assert result["active_user_ledger_touched"] is False


def test_elevated_risk_and_weekend_paper_execution_rules_not_disabled():
    # The demo must use the actual 4/6/7 policies, not a quick mock
    # calculation representing "guaranteed 400만원 fully invested".
    from wealthy_server.stage4_portfolio import DAY_LIMIT_PCT,MAX_ORDER_KRW
    assert DAY_LIMIT_PCT==Decimal(".05")
    assert MAX_ORDER_KRW==Decimal("250000")
    result=sandbox_preview()
    cap=Decimal(result["weekday_replay"]["risk_budget_krw"])
    assert cap==Decimal(4000000)*DAY_LIMIT_PCT
    assert result["market_closed_check"]["orders_executed"]==0
