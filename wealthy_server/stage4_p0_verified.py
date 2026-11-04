"""P0 Stage4 executor: preflight outside lock, one atomic fill per choice.

Disabled from production until verified provider opening NAV evidence and the
coordinator's domestic-session policy are accepted. Not a broker interface.
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal

from .stage1_verified_execution import execute_verified_paper
from .stage4_portfolio import (
    _decimal, DEVIATION, MAX_ORDER_KRW, KST, _signed_day
)
from .stage6_risk import review_candidate


def run_verified_stage4(engine, owner, *, now=None):
    now=now or datetime.now(timezone.utc)
    from .stage7_release import audit_paper_ledger
    audit=audit_paper_ledger(owner,engine.portfolios,now=now)
    if audit["blocking_count"]:
        return {"status":"integrity_blocked","executed":[],"skipped":[],
                "reason":"가상 원장 무결성 오류","simulated_only":True}
    plan=engine.preview(owner,now=now)
    if plan["status"]!="ready" or plan.get("trade_enabled") is not True:
        return {"status":"blocked","reason":plan.get("reason","검증 대기"),
                "executed":[],"skipped":[],"simulated_only":True}
    day=now.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat()
    results=[]
    for item in plan.get("proposed_orders",[])[:8]:
        try:
            if item["symbol"].isdigit():
                # Domestic-common quota boundary is NOT yet coordinator-approved.
                raise ValueError("DOMESTIC_COMMON_SESSION_POLICY_UNAPPROVED")
            quote=engine.quote_provider(item["symbol"])
            if not isinstance(quote,dict) or quote.get("currency")!="USD":
                raise ValueError("UNVERIFIED_STAGE4_QUOTE")
            current=_decimal(quote.get("price"))
            original=_decimal(item["planned_price"])
            if original<=0 or current<=0 or abs(current/original-1)>DEVIATION:
                raise ValueError("PRICE_MOVED_MORE_THAN_3_PERCENT")
            if not quote.get("source") or not quote.get("source_url"):
                raise ValueError("UNVERIFIED_EXECUTION_SOURCE")
            # Existing independent risk/price snapshot digests still veto BUY.
            if item["side"]=="BUY":
                refs=engine.market_store.latest_many([
                    ("PRICE",item["symbol"]),("HISTORY",item["symbol"])],now=now)
                if (refs[("PRICE",item["symbol"])].get("sha256")!=item.get("source_hash") or
                    refs[("HISTORY",item["symbol"])].get("sha256")!=item.get("risk_history_hash")):
                    raise ValueError("RISK_OR_PRICE_REFERENCE_CHANGED")
                if review_candidate(refs[("HISTORY",item["symbol"])],
                                    item["symbol"],now)["buy_guard"]["status"]!=(
                        "permitted_for_stage4_checks"):
                    raise ValueError("STAGE6_BUY_RISK_REJECTED")
            fx=engine.fx_provider()
            if fx.get("status")!="ok" or fx.get("executable") is not False:
                raise ValueError("STAGE4_FX_UNVERIFIED")
            rate=_decimal(fx["usd_krw"])
            if not 100<=rate<=10000:
                raise ValueError("STAGE4_FX_OUT_OF_RANGE")
            ref_fx=engine.market_store.latest_many([("FX","USD/KRW")],now=now)
            from .stage4_portfolio import _fx
            reference=_fx(ref_fx[("FX","USD/KRW")],now)
            if reference is None or abs(rate/reference-1)>DEVIATION:
                raise ValueError("STAGE4_FX_REFERENCE_MOVED")
            value=current*_decimal(item["quantity"])*rate
            if value>=Decimal("300000") or value>MAX_ORDER_KRW:
                raise ValueError("STAGE4_ORDER_LIMIT_OR_APPROVAL_REQUIRED")
            if item["side"]=="BUY" and value>_decimal(item["notional_krw"])*Decimal("1.03"):
                raise ValueError("STAGE4_PLANNED_BUDGET_EXCEEDED")
            result=execute_verified_paper(
                engine.portfolios,owner=owner,account=item["account"],
                symbol=item["symbol"],side=item["side"],quantity=item["quantity"],
                request_id=item["request_id"],quote=quote,fx=fx,
                auto_fx=item["side"]=="BUY",session_date=day,
                stage4_plan={
                    "plan_hash":plan["plan_hash"],
                    "daily_spend_cap_krw":plan["daily_spend_cap_krw"],
                    "reserve_percent":plan["reserve_percent"],
                    "portfolio_value_krw":plan["portfolio_value_krw"],
                    "kind":item["kind"],
                    "policy_prefs":plan["policy_prefs"],
                    "policy_weights":plan["policy_weights"],
                },now=now)
            results.append({"request_id":item["request_id"],
                            "symbol":item["symbol"],"side":item["side"],
                            "quantity":item["quantity"],"account":item["account"],
                            "status":"executed" if result["status"]=="executed" else "already_executed",
                            "reason":"ATOMIC_PAPER_LEDGER_CONFIRMED"})
        except (ValueError,ArithmeticError,KeyError,TypeError) as exc:
            results.append({"request_id":item.get("request_id"),"symbol":item.get("symbol"),
                            "status":"skipped","reason":str(exc)})
    return {"status":"completed","plan_hash":plan["plan_hash"],
            "executed":[x for x in results if x["status"]=="executed"],
            "skipped":[x for x in results if x["status"]!="executed"],
            "review":plan.get("review",[]),"simulated_only":True}
