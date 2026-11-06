"""Prerequisites for using *real* public PRICE/FX records at NYSE opening.

All records in this test are synthetic and isolated: passing proves validation
of evidence metadata, NOT that a public market provider has been contacted.
No broker credentials, production data or PAPER readiness toggle.
"""
from datetime import datetime, timedelta, timezone

import pytest

from wealthy_server.stage1_nav_proof import establish_verified_opening_nav
from wealthy_server.stage1_market_store import validated_snapshot
from test_stage2_verified_opening_nav_and_atomic_paper import (
    sample_store, OWNER, DAY, NOW, BEFORE, FX_URL, FX
)


def assert_no_opening_nav_or_claim(p):
    with p._db() as db:
        nav=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stage1_verified_opening_nav'").fetchone()
        if nav is not None:
            assert db.execute("SELECT COUNT(*) FROM stage1_verified_opening_nav WHERE owner=?",(OWNER,)).fetchone()[0]==0
        claim=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stage1_turnover_claims'").fetchone()
        if claim is not None:
            assert db.execute("SELECT COUNT(*) FROM stage1_turnover_claims WHERE owner=?",(OWNER,)).fetchone()[0]==0


def test_fx_cache_payload_mutation_blocks_nav_without_any_write(tmp_path):
    p,market=sample_store(tmp_path)
    with market._db() as db:
        db.execute("UPDATE market_snapshots SET payload_json=? WHERE kind='FX' AND symbol='USD/KRW'",('{}',))
    assert market.latest_many([('FX','USD/KRW')],now=NOW)[('FX','USD/KRW')]['reason']=='CACHE_INTEGRITY_FAILED'
    with pytest.raises(ValueError,match="UNVERIFIED_NAV_REFERENCE_FX"):
        establish_verified_opening_nav(p,market,OWNER,DAY,now=NOW)
    assert_no_opening_nav_or_claim(p)


def test_public_fx_observed_after_session_open_is_rejected(tmp_path):
    p,market=sample_store(tmp_path)
    record=validated_snapshot('FX','USD/KRW','FRANKFURTER',FX_URL,FX,now=NOW)
    market.save(record)
    # The later replacement becomes the current cache pointer and cannot be
    # backdated into the 09:30 NY opening reference.
    with pytest.raises(ValueError,match="UNVERIFIED_NAV_REFERENCE_FX"):
        establish_verified_opening_nav(p,market,OWNER,DAY,now=NOW)
    assert_no_opening_nav_or_claim(p)


def test_five_day_reference_age_is_hard_boundary(tmp_path):
    p,market=sample_store(tmp_path)
    stale_time=BEFORE-timedelta(days=7)
    old=validated_snapshot('FX','USD/KRW','FRANKFURTER',FX_URL,
                           {**FX,'as_of':stale_time.date().isoformat()},now=stale_time)
    with market._db() as db:
        db.execute("DELETE FROM market_current WHERE kind='FX' AND symbol='USD/KRW'")
        db.execute("DELETE FROM market_snapshots WHERE kind='FX' AND symbol='USD/KRW'")
    market.save(old)
    with pytest.raises(ValueError,match="STALE_OR_POST_OPEN_NAV_REFERENCE"):
        establish_verified_opening_nav(p,market,OWNER,DAY,now=NOW)
    assert_no_opening_nav_or_claim(p)


def test_exactly_fifteen_minutes_after_open_is_too_late(tmp_path):
    p,market=sample_store(tmp_path)
    # NYSE 09:45 America/New_York is the exclusive upper boundary.
    at_open_plus_15=datetime(2026,11,4,14,45,tzinfo=timezone.utc)
    with pytest.raises(ValueError,match="NAV_CREATION_WINDOW_EXPIRED"):
        establish_verified_opening_nav(p,market,OWNER,DAY,now=at_open_plus_15)
    assert_no_opening_nav_or_claim(p)


def test_next_calendar_year_cannot_auto_use_unverified_2026_schedule(tmp_path):
    p,market=sample_store(tmp_path)
    with pytest.raises(ValueError,match="CALENDAR_YEAR_NOT_VERIFIED"):
        establish_verified_opening_nav(p,market,OWNER,"2027-01-04",now=NOW)
    assert_no_opening_nav_or_claim(p)
