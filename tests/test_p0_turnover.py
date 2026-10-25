"""Pure US-market-session PAPER turnover tests (no user database)."""
import pytest
from wealthy_server.p0_turnover import evaluate_session_turnover as check

DAY = "2026-10-26"


def rec(request_id, side, amount, status="FILLED", **kwargs):
    return {
        "session_date": DAY, "request_id": request_id,
        "side": side, "gross_notional_krw": amount, "status": status,
        **kwargs,
    }


def test_4m_opening_nav_allows_800k_gross_buy_plus_sell():
    records = [
        rec("buy-001", "BUY", "350000", "FILLED"),
        rec("sell-001", "SELL", "300000", "FILLED"),
        rec("buy-pending-001", "BUY", "100000", "PENDING"),
    ]
    result = check(
        session_date=DAY, verified_opening_nav_krw="4000000",
        records=records, proposed_gross_krw="50000",
    )
    assert result["cap_krw"] == "800000.00"
    assert result["used_and_reserved_krw"] == "750000"
    assert result["permitted"] is True
    exceeded = check(
        session_date=DAY, verified_opening_nav_krw="4000000",
        records=records, proposed_gross_krw="50000.01",
    )
    assert exceeded["permitted"] is False
    assert exceeded["reason"] == "GROSS_TURNOVER_CAP_EXCEEDED"


def test_claims_and_unknowns_consume_capacity_instead_of_releasing_it():
    res = check(session_date=DAY, verified_opening_nav_krw=4000000,
                records=[rec("pending", "BUY", 400000, "CLAIMED"),
                         rec("unknown", "SELL", 400000, "UNKNOWN")],
                proposed_gross_krw="0.01")
    assert res["remaining_krw"] == "0"
    assert res["permitted"] is False


def test_reconciled_cancellation_can_release_reserved_amount():
    rows = [rec("cancelled", "BUY", 700000, "CANCELED", reconciled_no_fill=True)]
    assert check(session_date=DAY, verified_opening_nav_krw=4000000,
                 records=rows, proposed_gross_krw=800000)["permitted"]
    del rows[0]["reconciled_no_fill"]
    with pytest.raises(ValueError, match="unreconciled"):
        check(session_date=DAY, verified_opening_nav_krw=4000000,
              records=rows, proposed_gross_krw=1)


@pytest.mark.parametrize("broken", [
    [rec("repeat", "BUY", 100), rec("repeat", "SELL", 100)],
    [rec("bad-status", "BUY", 100, "UNRECOGNIZED")],
    [{**rec("wrong-day", "BUY", 100), "session_date": "2026-10-27"}],
    [rec("nan", "BUY", "NaN")],
    [rec("bad-side", "CREDIT", 100)],
])
def test_bad_or_ambiguous_records_fail_closed(broken):
    with pytest.raises(ValueError):
        check(session_date=DAY, verified_opening_nav_krw=4000000,
              records=broken, proposed_gross_krw=1)


@pytest.mark.parametrize("nav", [None, "0", "-1", "NaN", "Infinity", True])
def test_invalid_or_unverified_nav_rejected(nav):
    with pytest.raises(ValueError):
        check(session_date=DAY, verified_opening_nav_krw=nav,
              records=[], proposed_gross_krw=1)


def test_no_us_session_prediction_or_fx_conversion_in_pure_guard():
    """The caller must supply the verified session and KRW value."""
    with pytest.raises(ValueError):
        check(session_date="KST-today", verified_opening_nav_krw="4000000",
              records=[], proposed_gross_krw=1)
