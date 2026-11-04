"""Runtime release barrier for unverified PAPER opening NAV (P0).

PAPER 20% owner-wide turnover is implemented only in an *opt-in* reservation
builder. No independently verified market-session opening valuation pipeline
currently attests quote timestamps, FX provenance, holdings, and session
integrity. Consequently there is NO safe runtime authorization path here.

This gate is deliberately non-configurable. An arbitrary database row, local
flag, or long nav_source string may not be elevated to proof by any caller.
Replace this module only with an independently tested verifier wired to all
three order writers in the same SQLite transaction; NEVER use it as a toggle.
"""
from __future__ import annotations


def paper_execution_readiness() -> dict[str, object]:
    """Fail closed. Preview and approval state are not trading authorization."""
    return {
        "verified": False,
        "code": "UNVERIFIED_SESSION_OPENING_NAV",
        "reason": (
            "검증된 미국 세션 시작 자산·시세·환율 증거와 "
            "통합 20% 거래한도 원자 연결이 없어 PAPER 주문을 보류합니다."
        ),
    }
