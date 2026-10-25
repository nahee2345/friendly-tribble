"""QA-002: offline fail-safe news cache tests; no provider calls."""
from datetime import datetime, timedelta, timezone
import sqlite3
from contextlib import contextmanager

from wealthy_server.stage10_news_cache import reconcile_feed

NOW=datetime(2026,10,25,6,0,tzinfo=timezone.utc)


class Store:
    def __init__(self,path):
        self.path=path
    @contextmanager
    def _db(self):
        c=sqlite3.connect(self.path,isolation_level=None)
        try:
            yield c
        finally:
            c.close()


def item(title="AAPL reports results",url="https://news.google.com/rss/articles/id1",market="US"):
    return dict(market=market,title=title,url=url,published_utc=NOW.isoformat(),
                title_ko=None,translation_status="translation_unavailable",
                source="Google News RSS")


def test_full_provider_outage_uses_last_good_and_preserves_original(tmp_path):
    p=Store(tmp_path/"news.sqlite3")
    first=reconcile_feed(p,"alice",{"status":"ok","items":[item()],"unavailable":[]},now=NOW)
    assert first["status"]=="ok"
    assert first["items"][0]["cache_status"]=="fresh"
    later=NOW+timedelta(hours=4)
    fail=reconcile_feed(p,"alice",{"status":"unavailable","items":[],"unavailable":["US","KR"]},now=later)
    assert fail["status"]=="partial"
    assert fail["cache_status"]=="stale_cache"
    assert fail["items"][0]["title"]=="AAPL reports results"
    assert fail["items"][0]["title_ko"] is None
    assert fail["items"][0]["cache_status"]=="stale_cache"
    assert fail["last_success_by_market"]["US"]==NOW.isoformat()
    assert fail["last_attempt_utc"]==later.isoformat()


def test_partial_failure_preserves_other_market_cache_and_deduplicates_url(tmp_path):
    p=Store(tmp_path/"news.sqlite3")
    kr=item(title="반도체 업황",url="https://www.mk.co.kr/news/stock/123",market="KR")
    us=item()
    reconcile_feed(p,"alice",{"status":"ok","items":[kr,us],"unavailable":[]},now=NOW)
    partial=reconcile_feed(p,"alice",{"status":"partial","items":[us,us],"unavailable":["KR"]},
                           now=NOW+timedelta(minutes=30))
    assert len(partial["items"])==2
    assert partial["cache_status"]=="partial"
    assert {x["market"] for x in partial["items"]}=={"KR","US"}
    assert next(x for x in partial["items"] if x["market"]=="KR")["cache_status"]=="stale_cache"
    assert next(x for x in partial["items"] if x["market"]=="US")["cache_status"]=="fresh"


def test_unsafe_urls_and_other_owners_are_not_returned(tmp_path):
    p=Store(tmp_path/"news.sqlite3")
    bad=[
        item(url="http://news.google.com/rss/articles/id1"),
        item(url="https://user:pass@news.google.com/a"),
        item(url="https://news.google.com.example.net/a"),
        item(url="https://127.0.0.1/private"),
        item(url="https://news.google.com:5555/a"),
    ]
    result=reconcile_feed(p,"alice",{"status":"ok","items":bad,"unavailable":[]},now=NOW)
    assert result["items"]==[]
    good=reconcile_feed(p,"alice",{"status":"ok","items":[item()],"unavailable":[]},now=NOW)
    assert len(good["items"])==1
    other=reconcile_feed(p,"bob",{"status":"unavailable","items":[],"unavailable":["US","KR"]},now=NOW)
    assert other["items"]==[]


def test_failed_translation_never_replaces_original_title(tmp_path):
    p=Store(tmp_path/"news.sqlite3")
    news=item(title="Original English headline")
    news["title_ko"]=" "
    news["translation_status"]="translated_external"
    r=reconcile_feed(p,"alice",{"status":"ok","items":[news],"unavailable":[]},now=NOW)
    assert r["items"][0]["title"]=="Original English headline"
    assert r["items"][0]["title_ko"] is None
    assert r["items"][0]["translation_status"]=="translation_unavailable"
