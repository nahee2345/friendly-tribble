"""Pure PAPER session-turnover preflight, NOT an executable order gateway.

The caller must independently verify the US regular market session, the
session-opening owner NAV and the USD/KRW conversion *before* constructing
this input. Reservation/claim/ledger transitions must still be implemented
atomically in SQLite before this can authorize a real PAPER execution.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

CAP_RATIO = Decimal("0.20")
COUNTED = frozenset({"PENDING", "RESERVED", "CLAIMED", "UNKNOWN", "FILLED"})
CLOSED = frozenset({"REJECTED", "CANCELED", "EXPIRED"})


def _money(value: object, *, strictly_positive: bool) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid turnover amount")
    try:
        amount = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise ValueError("invalid turnover amount") from None
    if not amount.is_finite() or (amount <= 0 if strictly_positive else amount < 0):
        raise ValueError("invalid turnover amount")
    return amount


def evaluate_session_turnover(
    *, session_date: str, verified_opening_nav_krw: object,
    records: Sequence[Mapping[str, object]], proposed_gross_krw: object = "0",
) -> dict[str, str | bool]:
    """Calculate a BUY+SELL session limit without changing user data.

    Every previously issued request must have one authoritative record for
    this owner/session; ambiguous, conflicting or unreconciled closed
    requests consume capacity or block the calculation (fail closed).
    """
    try:
        if date.fromisoformat(session_date).isoformat() != session_date:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("invalid market session date") from None

    nav = _money(verified_opening_nav_krw, strictly_positive=True)
    proposed = _money(proposed_gross_krw, strictly_positive=False)
    limit = nav * CAP_RATIO
    used = Decimal("0")
    seen = set()

    for rec in records:
        if rec.get("session_date") != session_date:
            raise ValueError("wrong session record")
        key = rec.get("request_id")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("ambiguous turnover request identity")
        seen.add(key)
        if rec.get("side") not in ("BUY", "SELL"):
            raise ValueError("invalid turnover side")
        state = rec.get("status")
        if state not in COUNTED | CLOSED:
            raise ValueError("unknown turnover status")
        amount = _money(rec.get("gross_notional_krw"), strictly_positive=True)
        if state in CLOSED:
            # A cancelled or expired request that might have filled still
            # counts until its actual ledger status has been reconciled.
            if rec.get("reconciled_no_fill") is not True:
                raise ValueError("unreconciled released reservation")
        else:
            used += amount

    remaining = max(Decimal("0"), limit - used)
    allowed = used + proposed <= limit
    return {
        "session_date": session_date,
        "cap_krw": str(limit),
        "used_and_reserved_krw": str(used),
        "remaining_krw": str(remaining),
        "proposed_gross_krw": str(proposed),
        "permitted": allowed,
        "reason": "WITHIN_CAP" if allowed else "GROSS_TURNOVER_CAP_EXCEEDED",
    }
