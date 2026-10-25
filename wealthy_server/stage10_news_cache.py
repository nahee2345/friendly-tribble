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
    """Retain last-good news without confusing a successful fetch with new news.

    A validated response can contain *unchanged* articles. Such articles are
    cached, never fresh, and are not inputs to new-risk notifications.
    """
    now=now or datetime.now(timezone.utc)
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    now=now.astimezone(timezone.utc)
    if not isinstance(live_feed,dict) or not isinstance(live_feed.get("items"),list):
        raise ValueError("invalid feed structure")
    if not isinstance(owner,str) or not owner or type(limit) is not int or not 1<=limit<=100:
        raise ValueError("invalid owner or news limit")
    available={}
    for article in live_feed["items"][:500]:
        clean=_safe(article,now)
        if clean:
            available[(clean["market"],clean["url"])]=clean

    unavailable=set(live_feed.get("unavailable") or ()) & MARKETS
    if live_feed.get("status")=="unavailable":
        unavailable=set(MARKETS)
    # Never treat a feed claiming a market failure as a fresh successful fetch.
    available={key:value for key,value in available.items() if key[0] not in unavailable}
    observed={m for m,_ in available}
    unavailable|=MARKETS-observed
    now_text=now.isoformat()

    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        previous={
            (market,url): (data_json,published,last_success)
            for market,url,data_json,published,last_success in db.execute(
                "SELECT market,url,data_json,published_utc,last_success_utc "
                "FROM stage10_news_last_good WHERE owner=?", (owner,)
            )
        }
        new_items=set()
        for (market,url),item in available.items():
            prior=previous.get((market,url))
            changed=prior is None
            if prior is not None:
                try:
                    old=json.loads(prior[0])
                    changed=(old.get("title")!=item["title"] or
                             old.get("published_utc")!=item["published_utc"])
                except (ValueError,TypeError):
                    changed=True
            if changed:
                new_items.add((market,url))
                db.execute("""INSERT INTO stage10_news_last_good
                   (owner,market,url,data_json,published_utc,last_success_utc)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(owner,market,url) DO UPDATE SET
                   data_json=excluded.data_json,
                   published_utc=excluded.published_utc,
                   last_success_utc=excluded.last_success_utc""",
                   (owner,market,url,json.dumps(item,ensure_ascii=False,sort_keys=True),
                    item["published_utc"],now_text))
        # A successful poll with old headlines is a successful provider fetch
        # but not a new headline. We keep the last valid observation timestamp.
        for market in observed:
            db.execute("""INSERT INTO stage10_news_fetch_state VALUES (?,?,?)
                 ON CONFLICT(owner,market) DO UPDATE SET
                 last_success_utc=excluded.last_success_utc""",
                 (owner,market,now_text))
        saved=db.execute(
            "SELECT market,url,data_json,last_success_utc FROM stage10_news_last_good "
            "WHERE owner=? ORDER BY published_utc DESC LIMIT 500", (owner,)
        ).fetchall()
        successful=dict(db.execute(
            "SELECT market,last_success_utc FROM stage10_news_fetch_state "
            "WHERE owner=?", (owner,)
        ))
        db.execute("COMMIT")

    items={}
    latest_pub={}
    for market,url,payload,last_update in saved:
        try:
            checked=_safe(json.loads(payload),now)
        except (ValueError,TypeError):
            checked=None
        if checked is None:
            continue
        key=(market,url)
        if key in new_items:
            checked=available[key].copy()
            article_state="fresh"
        elif key in available:
            article_state="cached"
        else:
            article_state="stale_cache" if market in unavailable else "cached"
        checked["cache_status"]=article_state
        checked["last_success_utc"]=successful.get(market,last_update)
        items[key]=checked
        latest_pub[market]=max(latest_pub.get(market,""),checked["published_utc"])

    market_states={}
    for market in sorted(MARKETS):
        count=sum(1 for k in new_items if k[0]==market)
        status=("stale_cache" if market in unavailable and
                any(k[0]==market for k in items) else
                "unavailable" if market in unavailable else
                "fresh" if count else "cached")
        market_states[market]={
            "status":status,
            "fetch_status":"unavailable" if market in unavailable else "ok",
            "new_articles":count,
            "last_success_utc":successful.get(market),
            "latest_published_utc":latest_pub.get(market),
            "last_attempt_utc":now_text,
        }
    all_states={x["status"] for x in market_states.values()}
    if all_states=={"fresh"}:
        cache_status="fresh"
    elif all_states=={"cached"}:
        cache_status="cached"
    elif all_states <= {"stale_cache","unavailable"}:
        cache_status="stale_cache" if items else "unavailable"
    else:
        cache_status="partial"
    final=sorted(items.values(),key=lambda x:x["published_utc"],reverse=True)[:limit]
    return {
        "items":final,
        "status":"ok" if cache_status in ("fresh","cached") else
                 "unavailable" if cache_status=="unavailable" else "partial",
        "cache_status":cache_status,
        "market_states":market_states,
        "last_success_by_market":successful,
        "last_attempt_utc":now_text,
        "unavailable":sorted(unavailable),
        "coverage":"최신 제공원 조회와 신규 기사 갱신 여부를 구분합니다. 기존 뉴스는 새 위험 알림으로 발송하지 않습니다.",
        "tradeable":False,
    }
