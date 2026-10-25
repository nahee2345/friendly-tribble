"""QA-016-02: a successful poll is not proof of new article content."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from contextlib import contextmanager

from wealthy_server.stage10_news_cache import reconcile_feed

NOW=datetime(2026,10,25,6,tzinfo=timezone.utc)


class Store:
    def __init__(self,path:Path):
        self.path=path
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,isolation_level=None)
        try: yield db
        finally: db.close()


def news(market,code,when=NOW):
    return {"market":market,"title":f"{market} {code} loss warning",
            "url":f"https://news.google.com/rss/articles/{market}-{code}",
            "published_utc":when.isoformat(),"source":"Google News RSS"}


def test_new_us_with_unchanged_kr_is_partial_not_fresh(tmp_path):
    db=Store(tmp_path/"news.sqlite3")
    old_kr=news("KR","old",NOW-timedelta(days=3))
    initial=reconcile_feed(db,"alice",{"status":"ok","items":[old_kr,news("US","first")],"unavailable":[]},now=NOW)
    assert initial["cache_status"]=="fresh"
    next_time=NOW+timedelta(minutes=30)
    updated=reconcile_feed(db,"alice",{
        "status":"ok",
        "items":[old_kr, news("US","next",next_time)],
        "unavailable":[]
    },now=next_time)
    assert updated["cache_status"]=="partial"
    assert updated["market_states"]["KR"]["fetch_status"]=="ok"
    assert updated["market_states"]["KR"]["status"]=="cached"
    assert updated["market_states"]["KR"]["new_articles"]==0
    assert updated["market_states"]["US"]["status"]=="fresh"
    assert updated["market_states"]["US"]["new_articles"]==1
    assert next(x for x in updated["items"] if x["market"]=="KR")["cache_status"]=="cached"
    assert updated["last_success_by_market"]["KR"]==next_time.isoformat()


def test_repeated_same_headline_is_cached_and_not_fresh(tmp_path):
    db=Store(tmp_path/"news.sqlite3")
    both=[news("KR","one"),news("US","one")]
    first=reconcile_feed(db,"alice",{"status":"ok","items":both,"unavailable":[]},now=NOW)
    assert first["cache_status"]=="fresh"
    second=reconcile_feed(db,"alice",{"status":"ok","items":both,"unavailable":[]},
                          now=NOW+timedelta(minutes=30))
    assert second["cache_status"]=="cached"
    assert second["status"]=="ok"
    assert {x["cache_status"] for x in second["items"]}=={"cached"}
    assert second["market_states"]["KR"]["new_articles"]==0
    assert second["market_states"]["US"]["new_articles"]==0


def test_total_outage_stale_state_preserves_last_good(tmp_path):
    db=Store(tmp_path/"news.sqlite3")
    both=[news("KR","one"),news("US","one")]
    reconcile_feed(db,"alice",{"status":"ok","items":both,"unavailable":[]},now=NOW)
    later=NOW+timedelta(minutes=90)
    fail=reconcile_feed(db,"alice",{"status":"unavailable","items":[],"unavailable":["KR","US"]},now=later)
    assert fail["cache_status"]=="stale_cache"
    assert all(x["cache_status"]=="stale_cache" for x in fail["items"])
    assert fail["market_states"]["KR"]["last_success_utc"]==NOW.isoformat()
    assert fail["market_states"]["US"]["fetch_status"]=="unavailable"
    assert fail["last_attempt_utc"]==later.isoformat()


def test_partial_feed_conflicts_fails_closed_for_that_market(tmp_path):
    db=Store(tmp_path/"news.sqlite3")
    reconcile_feed(db,"alice",{"status":"ok","items":[news("KR","one"),news("US","one")],
                              "unavailable":[]},now=NOW)
    later=NOW+timedelta(minutes=40)
    fail=reconcile_feed(db,"alice",{"status":"partial",
                                    "items":[news("KR","new",later),news("US","new",later)],
                                    "unavailable":["KR"]},now=later)
    assert fail["market_states"]["KR"]["status"]=="stale_cache"
    assert not any(x["url"].endswith("KR-new") for x in fail["items"])
    assert fail["market_states"]["US"]["status"]=="fresh"
