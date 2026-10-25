"""P0 UI approved-order click regression contract.

Static UI checks complement (but never replace) authenticated browser testing.
"""
from pathlib import Path


SOURCE=Path(__file__).resolve().parents[1]/"web/frontend/src/Stage1Page.tsx"


def test_approved_paper_order_button_has_click_handler():
    code=SOURCE.read_text(encoding="utf-8")
    text="승인된 가상 주문 실행 · 시세 재검증</button>"
    pos=code.index(text)
    button=code[code.rfind("<button",0,pos):pos]
    assert "onClick={()=>void executeApprovedOrder(a)}" in button
    assert "a.status!=='APPROVED'" in button
    assert "a.executed" in button
    assert "a.needs_reconciliation" in button
    assert "paperControl?.paused!==false" in button
    assert "paper_execution_permitted_by_integrity!==true" in button
    assert "Date.parse(a.expires_at)<=Date.now()" in button


def test_order_result_is_reconciled_with_persisted_fill_before_success():
    code=SOURCE.read_text(encoding="utf-8")
    segment=code.split("async function executeApprovedOrder(a:Approval){",1)[1].split(
        "async function decideApproval(",1)[0]
    assert "approvalExecuteLock.current=true" in segment
    assert "get<ReleaseAudit>('/api/v1/stage1/release-integrity')" in segment
    assert "get<{items:Approval[]}>('/api/v1/stage1/approvals')" in segment
    assert "fresh.status!=='APPROVED'" in segment
    assert "fresh.needs_reconciliation" in segment
    assert "Date.parse(fresh.expires_at)<=Date.now()" in segment
    assert "/api/v1/stage1/portfolios/" in segment and "/orders" in segment
    assert "if(!confirmed)" in segment
    assert "setNotice(" in segment.split("if(!confirmed)",1)[1]
    assert "approvalExecuteLock.current=false" in segment
