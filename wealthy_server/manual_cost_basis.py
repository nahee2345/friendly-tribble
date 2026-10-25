"""QA-001: derived, non-mutating moving-average cost from declared events.

Legacy ADD/REMOVE events remain unchanged. Missing acquisition cost is never
estimated from current price or represented as zero. No PAPER fills or actual
broker account balances are read or altered.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


INFLOWS = frozenset({"BUY", "IN", "ADD", "CORRECT_ADD"})
OUTFLOWS = frozenset({"SELL", "OUT", "REMOVE", "CORRECT_REMOVE"})


def _decimal(value, *, required=False):
    if value is None:
        if required:
            raise ValueError("missing cost")
        return None
    try:
        amount = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise ValueError("invalid manual cost") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("invalid manual cost")
    return amount


def derive_costs(events, expected_quantities):
    """Return per-(account,symbol,currency) cost statements or unknowns.

    Event order is the immutable recording order; the original trade date
    is provenance and does not silently reorder later corrections.
    `expected_quantities` is the owner-scoped stored position snapshot and
    must match derived quantity or the valuation fails closed.
    """
    state = {}
    for event in events:
        key = event["account"], event["symbol"], event["currency"]
        s = state.setdefault(key, {
            "quantity": Decimal(0), "basis": Decimal(0),
            "basis_known": True, "realized": Decimal(0),
            "realized_known": True, "events": 0,
        })
        side = event["side"]
        if side not in INFLOWS | OUTFLOWS:
            raise ValueError("unknown manual event type")
        qty = _decimal(event["quantity"], required=True)
        price = _decimal(event.get("unit_price"))
        if side in INFLOWS:
            s["quantity"] += qty
            # A pure correction changes the recorded count, not evidence of
            # what was paid. Unknown original basis remains unknown.
            if side == "CORRECT_ADD" or price is None:
                s["basis_known"] = False
            elif s["basis_known"]:
                s["basis"] += qty * price
        else:
            if qty > s["quantity"]:
                raise ValueError("outflow greater than known historic quantity")
            average = (s["basis"] / s["quantity"]
                       if s["basis_known"] and s["quantity"] > 0 else None)
            if side == "SELL":
                if average is None or price is None:
                    s["realized_known"] = False
                elif s["realized_known"]:
                    s["realized"] += qty * (price - average)
            elif side in ("REMOVE", "CORRECT_REMOVE"):
                # Legacy REMOVE and corrections are not proof of a sale.
                pass
            if average is not None:
                s["basis"] -= qty * average
            s["quantity"] -= qty
            if s["quantity"] == 0:
                s["basis"] = Decimal(0)
                s["basis_known"] = True
        s["events"] += 1

    rows = {}
    for key, quantity in expected_quantities.items():
        expected = Decimal(str(quantity))
        s = state.get(key)
        consistent = bool(s and s["quantity"] == expected and expected > 0)
        known = consistent and s["basis_known"]
        average = s["basis"] / expected if known else None
        rows[key] = {
            "cost_basis_status": "known" if known else "unavailable",
            "average_cost_native": format(average, "f") if average is not None else None,
            "total_cost_native": format(s["basis"], "f") if known else None,
            "realized_pnl_native": (
                format(s["realized"], "f") if s and s["realized_known"] and known else None
            ),
            "unrealized_pnl_native": None,
            "valuation_status": "requires_verified_market_price",
            "history_reconciled": consistent,
            "cost_method": "moving_average_from_user_entries",
        }
    return rows
