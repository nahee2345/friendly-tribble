"""Source-pinned NYSE session dates, EDT/EST and half-day checks.

Official 2026 market schedule: https://www.nyse.com/trade/hours-calendars
These tests DO NOT verify opening PAPER NAV or unlock paper trading.
"""
from datetime import datetime, timezone

import pytest

from wealthy_server.stage1_us_market_calendar import (
    nyse_regular_window, assert_matching_core_session,
    verified_regular_hours_now,
)


@pytest.mark.parametrize(("day","open_utc","close_utc"),[
    ("2026-01-02","2026-01-02T14:30:00+00:00","2026-01-02T21:00:00+00:00"),
    ("2026-03-06","2026-03-06T14:30:00+00:00","2026-03-06T21:00:00+00:00"),
    ("2026-03-09","2026-03-09T13:30:00+00:00","2026-03-09T20:00:00+00:00"),
    ("2026-10-26","2026-10-26T13:30:00+00:00","2026-10-26T20:00:00+00:00"),
    ("2026-11-02","2026-11-02T14:30:00+00:00","2026-11-02T21:00:00+00:00"),
    ("2026-11-27","2026-11-27T14:30:00+00:00","2026-11-27T18:00:00+00:00"),
    ("2026-12-24","2026-12-24T14:30:00+00:00","2026-12-24T18:00:00+00:00"),
])
def test_verified_2026_regular_open_close(day,open_utc,close_utc):
    found=nyse_regular_window(day)
    assert (found["open_utc"],found["close_utc"])==(open_utc,close_utc)
    assert found["calendar_source"].startswith("https://www.nyse.com/")
    assert assert_matching_core_session(session_date=day,
      open_utc=open_utc,close_utc=close_utc)==found


@pytest.mark.parametrize("day",[
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03",
    "2026-05-25","2026-06-19","2026-07-03","2026-09-07",
    "2026-11-26","2026-12-25","2026-10-25","2026-10-31"
])
def test_nyse_holidays_and_weekends_fail_closed(day):
    with pytest.raises(ValueError,match="US_MARKET_HOLIDAY_OR_WEEKEND"):
        nyse_regular_window(day)


@pytest.mark.parametrize("day",["2025-10-27","2027-10-25","2028-01-03",
                                "2026-2-01","garbage","2030-10-26"])
def test_unsupported_or_invalid_year_never_assumed_open(day):
    with pytest.raises(ValueError,match="CALENDAR_YEAR_NOT_VERIFIED|INVALID_SESSION_DATE"):
        nyse_regular_window(day)


def test_wrong_dst_or_early_close_blocks_arbitrary_session_proof():
    with pytest.raises(ValueError,match="US_MARKET_SESSION_MISMATCH"):
        assert_matching_core_session(
          session_date="2026-11-02",
          open_utc="2026-11-02T13:30:00+00:00",
          close_utc="2026-11-02T20:00:00+00:00")
    with pytest.raises(ValueError,match="US_MARKET_SESSION_MISMATCH"):
        assert_matching_core_session(
          session_date="2026-11-27",
          open_utc="2026-11-27T14:30:00+00:00",
          close_utc="2026-11-27T21:00:00+00:00")


def test_timezone_naive_or_fake_clock_fails_closed():
    with pytest.raises(ValueError,match="VERIFIED_CLOCK_REQUIRED"):
        verified_regular_hours_now(session_date="2026-10-26",
          now=datetime(2026,10,26,14))
    with pytest.raises(ValueError,match="US_MARKET_SESSION_MISMATCH"):
        assert_matching_core_session(session_date="2026-10-26",
          open_utc="2026-10-26T13:30:00",
          close_utc="2026-10-26T20:00:00+00:00")


def test_market_before_and_after_regular_hours_is_not_open():
    day="2026-10-26"
    assert not verified_regular_hours_now(session_date=day,
        now=datetime(2026,10,26,13,29,59,tzinfo=timezone.utc))
    assert verified_regular_hours_now(session_date=day,
        now=datetime(2026,10,26,13,30,tzinfo=timezone.utc))
    assert verified_regular_hours_now(session_date=day,
        now=datetime(2026,10,26,19,59,59,tzinfo=timezone.utc))
    assert not verified_regular_hours_now(session_date=day,
        now=datetime(2026,10,26,20,0,tzinfo=timezone.utc))



def test_calendar_checked_session_reservation_half_day_and_late_denial(tmp_path):
    from wealthy_server.stage1_portfolios import Portfolios
    from wealthy_server.stage1_turnover_reservations import (
        establish_calendar_checked_session,reserve
    )
    paper=Portfolios(tmp_path/"only-test.sqlite3")
    with paper._db() as db:
        db.execute("BEGIN IMMEDIATE")
        result=establish_calendar_checked_session(
            db,owner="test-only",session_date="2026-11-27",
            opening_nav_krw="4000000",
            nav_source="2026-fixture-valuation-not-live-provider",
            verified_utc="2026-11-27T14:30:00+00:00"
        )
        assert result["cap_krw"]=="800000.00"
        assert reserve(
            db,owner="test-only",session_date="2026-11-27",
            request_id="core-hour-fixture-01",account="kr-base",
            symbol="SPY",side="BUY",quantity=1,
            gross_krw="300000",now_utc="2026-11-27T17:59:59+00:00"
        )["status"]=="RESERVED"
        with pytest.raises(ValueError,match="US_MARKET_SESSION_NOT_VERIFIED_OR_CLOSED"):
            reserve(db,owner="test-only",session_date="2026-11-27",
                request_id="post-half-day-02",account="kr-base",
                symbol="SPY",side="BUY",quantity=1,
                gross_krw="100000",now_utc="2026-11-27T18:00:00+00:00")
        db.execute("COMMIT")


def test_calendar_checked_session_refuses_official_holiday_even_if_nav_supplied(tmp_path):
    from wealthy_server.stage1_portfolios import Portfolios
    from wealthy_server.stage1_turnover_reservations import establish_calendar_checked_session
    paper=Portfolios(tmp_path/"only-test.sqlite3")
    with paper._db() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError,match="US_MARKET_HOLIDAY_OR_WEEKEND"):
            establish_calendar_checked_session(
                db,owner="test-only",session_date="2026-11-26",
                opening_nav_krw="4000000",
                nav_source="2026-fixture-valuation-not-live-provider",
                verified_utc="2026-11-26T14:30:00+00:00")
        db.execute("COMMIT")
