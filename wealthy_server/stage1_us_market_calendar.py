"""2026 NYSE regular equity market calendar, bounded and fail-closed.

Sources: NYSE official https://www.nyse.com/trade/hours-calendars
          NASDAQ corroboration https://www.nasdaq.com/market-activity/stock-market-holiday-schedule

This validates only the public *scheduled trading window*. It is NOT proof
that a market-data provider is up, a stock is tradeable, an exchange has not
declared an extraordinary closure, or the PAPER opening NAV is independently
valued. Do not use calendar status alone to enable any PAPER fill.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

_EXCHANGE_ZONE = ZoneInfo("America/New_York")
_UTC = timezone.utc
_SOURCE_URL = "https://www.nyse.com/trade/hours-calendars"
# Published for the 2026-2028 calendars; only 2026 is pinned here.
_SUPPORTED_YEAR = 2026
_CLOSED_2026 = frozenset({
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
})
_EARLY_CLOSE_2026 = frozenset({"2026-11-27", "2026-12-24"})


def _valid_day(day: str) -> date:
    if not isinstance(day, str):
        raise ValueError("INVALID_SESSION_DATE")
    try:
        candidate=date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError("INVALID_SESSION_DATE") from exc
    if candidate.isoformat()!=day:
        raise ValueError("INVALID_SESSION_DATE")
    if candidate.year!=_SUPPORTED_YEAR:
        raise ValueError("CALENDAR_YEAR_NOT_VERIFIED")
    return candidate


def nyse_regular_window(session_date: str) -> dict[str, str]:
    """Return scheduled NYSE equity CORE session in UTC.

    No generic Mon-Fri fallback. Unsupported years or official holidays fail
    closed instead of assuming the market is open.
    """
    day=_valid_day(session_date)
    if day.weekday()>=5 or session_date in _CLOSED_2026:
        raise ValueError("US_MARKET_HOLIDAY_OR_WEEKEND")
    opening=datetime.combine(day,time(9,30),_EXCHANGE_ZONE)
    closing=datetime.combine(day,time(13 if session_date in _EARLY_CLOSE_2026 else 16),_EXCHANGE_ZONE)
    return {
        "session_date":session_date,
        "open_utc":opening.astimezone(_UTC).isoformat(),
        "close_utc":closing.astimezone(_UTC).isoformat(),
        "time_zone":"America/New_York",
        "calendar_source":_SOURCE_URL,
        "calendar_year":"2026",
        "early_close": "true" if session_date in _EARLY_CLOSE_2026 else "false",
    }


def assert_matching_core_session(
    *, session_date: str, open_utc: str, close_utc: str
) -> dict[str, str]:
    """Fail closed if caller-supplied UTC window isn't the pinned NYSE window.

    This is calendar validation, NOT verification of opening NAV/cost/FX.
    """
    window=nyse_regular_window(session_date)
    for name,value,expected in (
        ("open_utc",open_utc,window["open_utc"]),
        ("close_utc",close_utc,window["close_utc"]),
    ):
        if not isinstance(value,str):
            raise ValueError("US_MARKET_SESSION_MISMATCH")
        try:
            parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
            if parsed.tzinfo is None or parsed.astimezone(_UTC).isoformat()!=expected:
                raise ValueError("US_MARKET_SESSION_MISMATCH")
        except (TypeError,ValueError) as exc:
            raise ValueError("US_MARKET_SESSION_MISMATCH") from exc
    return window


def verified_regular_hours_now(*, session_date:str, now:datetime) -> bool:
    """Scheduled hours only. 'True' MUST NOT be interpreted as approval to trade."""
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise ValueError("VERIFIED_CLOCK_REQUIRED")
    window=nyse_regular_window(session_date)
    start=datetime.fromisoformat(window["open_utc"])
    end=datetime.fromisoformat(window["close_utc"])
    utc=now.astimezone(_UTC)
    return start<=utc<end
