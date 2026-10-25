"""QA-002: owner-scoped persistent last-good news cache.

External provider failures never overwrite last-good rows. Headlines are
unverified research, not trade recommendations. No automatic web fetch here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

ALLOWED_HOSTS = {
    "news.google.com", "www.bbc.com", "www.bbc.co.uk",
    "www.mk.co.kr", "www.hankyung.com", "www.yna.co.kr",
}
MARKETS = {"KR", "US"}


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage10_news_last_good(
        owner TEXT NOT NULL, market TEXT NOT NULL,url TEXT NOT NULL,
        data_json TEXT NOT NULL,published_utc TEXT NOT NULL,
        last_success_utc TEXT NOT NULL,
        PRIMARY KEY(owner,market,url))""")
    db.execute("""CREATE TABLE IF NOT EXISTS stage10_news_fetch_state(
        owner TEXT NOT NULL,market TEXT NOT NULL,
        last_success_utc TEXT NOT NULL,
        PRIMARY KEY(owner,market))""")


def _safe(item, now):
    if not isinstance(item,dict):
        return None
    market=item.get("market")
    link=item.get("url")
    title=item.get("title")
    published=item.get("published_utc") or item.get("published")
    if (market not in MARKETS or not isinstance(title,str) or
        not 1<=len(title.strip())<=250 or not isinstance(link,str) or
        len(link)>2000 or not isinstance(published,str)):
        return None
    try:
        url=urlsplit(link)
        if (url.scheme!="https" or url.hostname not in ALLOWED_HOSTS or
            url.username is not None or url.password is not None or
            url.port not in (None,443)):
            return None
        try:
            ts=datetime.fromisoformat(published.replace("Z","+00:00"))
        except ValueError:
            ts=parsedate_to_datetime(published)
        if ts.tzinfo is None:
            return None
        ts=ts.astimezone(timezone.utc)
        if ts>now+timedelta(minutes=5) or ts<now-timedelta(days=30):
            return None
    except (ValueError,TypeError,OverflowError):
        return None
    clean={k:item.get(k) for k in (
        "market","title","url","published","published_utc","source",
        "publisher","editorial_lean","editorial_source","editorial_basis",
        "title_ko","translation_status",
    )}
    if not isinstance(clean.get("title_ko"),str) or not clean["title_ko"].strip():
        clean["title_ko"]=None
        clean["translation_status"]="translation_unavailable" if market=="US" else "original_ko"
    clean["published_utc"]=ts.isoformat()
    return clean


def reconcile_feed(portfolios, owner, live_feed, *, now=None, limit=30):
    """Do not downgrade a valid last-good cache during total/partial outages.

    Only accepted rows from responding markets update the cache. Last-good
    items are read back for unavailable markets, labelled as stale and never
    inserted into the current successful observation set.
    """
    now=now or datetime.now(timezone.utc)
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    now=now.astimezone(timezone.utc)
    if not isinstance(live_feed,dict) or not isinstance(live_feed.get("items"),list):
        raise ValueError("invalid feed structure")
    if not isinstance(owner,str) or not owner or type(limit) is not int or not 1<=limit<=100:
        raise ValueError("invalid owner or news limit")
    raw=live_feed["items"][:500]
    fresh={}
    for item in raw:
        clean=_safe(item,now)
        if clean:
            fresh[(clean["market"],clean["url"])]=clean
    unavailable=set(live_feed.get("unavailable") or []) & MARKETS
    if live_feed.get("status")=="unavailable" and not fresh:
        unavailable=set(MARKETS)
    now_text=now.isoformat()

    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        for (market,url),item in fresh.items():
            db.execute("""INSERT INTO stage10_news_last_good
                 (owner,market,url,data_json,published_utc,last_success_utc)
                 VALUES (?,?,?,?,?,?)
                 ON CONFLICT(owner,market,url) DO UPDATE SET
                 data_json=excluded.data_json,
                 published_utc=excluded.published_utc,
                 last_success_utc=excluded.last_success_utc""",
               (owner,market,url,json.dumps(item,ensure_ascii=False,sort_keys=True),
                item["published_utc"],now_text))
        for market in {m for m,_ in fresh}:
            db.execute("""INSERT INTO stage10_news_fetch_state VALUES (?,?,?)
                 ON CONFLICT(owner,market) DO UPDATE SET
                 last_success_utc=excluded.last_success_utc""",
                (owner,market,now_text))
        saved=db.execute(
            "SELECT market,url,data_json,last_success_utc FROM stage10_news_last_good "
            "WHERE owner=? ORDER BY published_utc DESC LIMIT 500", (owner,)
        ).fetchall()
        last=db.execute(
            "SELECT market,last_success_utc FROM stage10_news_fetch_state "
            "WHERE owner=?", (owner,)
        ).fetchall()
        db.execute("COMMIT")
    items={}
    for market,url,payload,last_success in saved:
        try:
            data=json.loads(payload)
            checked=_safe(data,now)
        except (TypeError,ValueError,json.JSONDecodeError):
            checked=None
        if checked is None:
            # Older rows remain safely stored, not promoted as current.
            continue
        key=market,url
        if key in fresh:
            data=clean=fresh[key].copy()
            clean["cache_status"]="fresh"
        elif market in unavailable or market not in {m for m,_ in fresh}:
            data=clean=checked.copy()
            clean["cache_status"]="stale_cache"
        else:
            data=clean=checked.copy()
            clean["cache_status"]="cached"
        clean["last_success_utc"]=last_success
        items[key]=clean
    final=sorted(items.values(),key=lambda x:x["published_utc"],reverse=True)[:limit]
    cache_status="fresh" if fresh and not unavailable else (
        "partial" if fresh else "stale_cache" if final else "unavailable"
    )
    return {
        "items":final,
        "status":"partial" if cache_status in ("partial","stale_cache") else
                 "ok" if cache_status=="fresh" else "unavailable",
        "cache_status":cache_status,
        "last_success_by_market":dict(last),
        "last_attempt_utc":now_text,
        "unavailable":sorted(unavailable),
        "coverage":"검증된 공개 헤드라인의 마지막 정상 캐시. 오래된 기사와 원문 번역 실패 표시.",
        "tradeable":False,
    }
