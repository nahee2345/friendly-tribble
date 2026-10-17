"""Isolated Stage 2 no-credential market-data snapshots.

Never shares a database with the paper ledger, and NEVER authorizes trades.
The latest *observed* reference is not necessarily a fresh/executable price.
All numerical and published-date checks happen before an immutable snapshot
can be persisted. Provider failures never overwrite a verified earlier entry.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit


CATEGORIES = frozenset({"PRICE","HISTORY","FX","NEWS","FUNDAMENTALS","DIVIDENDS"})
PROVIDERS = frozenset({"STOOQ","YAHOO_UNOFFICIAL","FRANKFURTER","GOOGLE_NEWS","SEC_EDGAR"})
DOMAINS = {
    "STOOQ": {"stooq.com"},
    "YAHOO_UNOFFICIAL": {"query1.finance.yahoo.com","query2.finance.yahoo.com"},
    "FRANKFURTER": {"api.frankfurter.dev"},
    "GOOGLE_NEWS": {"news.google.com"},
    "SEC_EDGAR": {"data.sec.gov","www.sec.gov"},
}
RECORD_LIMIT = 240_000
ALLOWED_SYMBOL = re.compile(r"^(?:[A-Z][A-Z0-9.\-]{0,14}|[0-9]{6}|USD/KRW|KR|US)$")
MAX_PRICE = Decimal("1000000000")


def _aware(t: datetime | None) -> datetime:
    result = t or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("timezone-aware UTC timestamp required")
    return result.astimezone(timezone.utc)


def _date(text: str, now: datetime, *, max_age: int) -> str:
    if not isinstance(text,str):
        raise ValueError("invalid source date")
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError("invalid source date") from None
    age=(now.date()-parsed).days
    if age<0 or age>max_age:
        raise ValueError("future or out-of-range source date")
    return parsed.isoformat()


def _money(value, *, ceiling=MAX_PRICE, min_positive=True) -> str:
    if isinstance(value,bool) or value is None:
        raise ValueError("invalid numeric source value")
    try:
        number=Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError("invalid numeric source value") from None
    if not number.is_finite() or number > ceiling or (number<=0 if min_positive else number<0):
        raise ValueError("out-of-range source value")
    if number.as_tuple().exponent < -6:
        raise ValueError("unsupported source decimal precision")
    return format(number,"f")


def _rounded_reference_price(value) -> str:
    # Yahoo daily adjusted-close floats commonly expose binary artifacts.
    # Normalize only for display/research, never order execution.
    if isinstance(value,bool) or value is None:
        raise ValueError("invalid reference price")
    try:
        price=Decimal(str(value))
    except (ValueError,TypeError,InvalidOperation):
        raise ValueError("invalid reference price") from None
    if not price.is_finite() or not 0<price<=MAX_PRICE:
        raise ValueError("invalid reference price magnitude")
    rounded=price.quantize(Decimal("0.000001"))
    if rounded<=0:raise ValueError("reference precision underflow")
    return format(rounded,"f")


def _url(value, provider):
    if not isinstance(value,str) or not 8<=len(value)<=512:
        raise ValueError("missing source url")
    try:
        info=urlsplit(value)
        allowed=(info.scheme=="https" and not info.username and not info.password
                 and info.port is None and (info.hostname or "").lower() in DOMAINS[provider])
    except ValueError:
        allowed=False
    if not allowed:
        raise ValueError("source is not an allowlisted provider")
    return value


def validated_snapshot(kind, symbol, provider, source_url, payload, *, now=None):
    """Return immutable normalized content or refuse unverifiable observations."""
    now=_aware(now)
    if kind not in CATEGORIES or provider not in PROVIDERS:
        raise ValueError("unknown type/provider")
    if not isinstance(symbol,str) or not ALLOWED_SYMBOL.fullmatch(symbol):
        raise ValueError("invalid symbol")
    url=_url(source_url,provider)
    if not isinstance(payload,dict):
        raise ValueError("payload must be a mapping")
    if kind=="PRICE":
        expected="KRW" if symbol.isdigit() else "USD"
        if symbol in ("US","KR","USD/KRW"):raise ValueError("not a security")
        if (provider not in ("STOOQ","YAHOO_UNOFFICIAL") or
                payload.get("status")!="ok" or payload.get("tradeable") is not True
                or payload.get("symbol")!=symbol or payload.get("currency")!=expected):
            raise ValueError("mismatched/unverified security quote")
        as_of=_date(payload.get("as_of"),now,max_age=4)
        normalized={"symbol":symbol,"currency":expected,"as_of":as_of,
                    "price":_money(payload.get("price")),
                    "reference_only":True,"order_eligible_from_cache":False}
        if expected=="KRW" and Decimal(normalized["price"]).as_tuple().exponent<0:
            raise ValueError("fractional KRW close")
        quality="VERIFIED_REFERENCE"
    elif kind=="FX":
        if provider!="FRANKFURTER" or symbol!="USD/KRW" or payload.get("status")!="ok" or payload.get("executable") is not False:
            raise ValueError("unverified reference FX")
        as_of=_date(payload.get("as_of"),now,max_age=5)
        rate=_money(payload.get("usd_krw"),ceiling=Decimal("10000"))
        if Decimal(rate)<100:raise ValueError("FX out of range")
        normalized={"pair":"USD/KRW","rate":rate,"as_of":as_of,
                    "reference_only":True,"executable":False}
        quality="VERIFIED_REFERENCE"
    elif kind=="HISTORY":
        if provider not in ("STOOQ","YAHOO_UNOFFICIAL") or payload.get("status")!="ok":
            raise ValueError("unverified price history")
        expected="KRW" if symbol.isdigit() else "USD"
        if payload.get("symbol")!=symbol or payload.get("currency")!=expected:
            raise ValueError("history currency/symbol mismatch")
        input_rows=payload.get("prices",payload.get("points"))
        if not isinstance(input_rows,list) or not 2<=len(input_rows)<=450:
            raise ValueError("insufficient/excessive price history")
        rows=[]; last=None
        for item in input_rows:
            if not isinstance(item,dict): raise ValueError("malformed history item")
            day=_date(item.get("date"),now,max_age=2000)
            if last is not None and day<=last:raise ValueError("out-of-order or duplicate history date")
            last=day
            rows.append({"date":day,"close":_rounded_reference_price(item.get("close"))})
        as_of=_date(payload.get("as_of"),now,max_age=6)
        if rows[-1]["date"]!=as_of:raise ValueError("history latest-date mismatch")
        normalized={"symbol":symbol,"currency":expected,"as_of":as_of,
                    "prices":rows,"reference_only":True,
                    "observations":len(rows)}
        quality="RESEARCH_ONLY"
    elif kind=="NEWS":
        if provider!="GOOGLE_NEWS" or symbol not in ("US","KR"):
            raise ValueError("news market/source mismatch")
        stories=payload.get("items")
        if not isinstance(stories,list) or len(stories)>100:
            raise ValueError("malformed news dataset")
        unique=set();items=[]
        for item in stories:
            if not isinstance(item,dict):raise ValueError("malformed article")
            title=item.get("title")
            link=_url(item.get("url"),provider)
            when=item.get("published_utc")
            if not isinstance(title,str) or not 1<=len(title)<=300:
                raise ValueError("invalid article title")
            try:
                published=datetime.fromisoformat(when)
                if published.tzinfo is None:raise ValueError("naive publication time")
                published=published.astimezone(timezone.utc)
            except (ValueError,TypeError,AttributeError):
                raise ValueError("invalid article date") from None
            if (published>now+timedelta(minutes=5) or
                    published<now-timedelta(days=30,minutes=5)):
                raise ValueError("article outside 30-day window")
            if link in unique:continue
            unique.add(link)
            items.append({"title":title,"url":link,"published_utc":published.isoformat()})
        as_of=now.date().isoformat()
        normalized={"market":symbol,"as_of":as_of,"items":items,
                    "coverage":"PUBLIC_RSS_SUBSET","full_article_verified":False}
        quality="RESEARCH_ONLY"
    elif kind=="DIVIDENDS":
        if provider!="YAHOO_UNOFFICIAL" or symbol in ("US","KR","USD/KRW"):
            raise ValueError("unsupported dividend source")
        expected="KRW" if symbol.isdigit() else "USD"
        if payload.get("status")!="ok" or payload.get("currency")!=expected or payload.get("symbol")!=symbol:
            raise ValueError("invalid dividend symbol/currency")
        items=payload.get("events")
        if not isinstance(items,list) or not 1<=len(items)<=200:
            raise ValueError("verified dividend events required")
        seen=set();events=[]
        for item in items:
            if not isinstance(item,dict):raise ValueError("invalid dividend event")
            d=_date(item.get("date"),now,max_age=2000)
            if d in seen:raise ValueError("duplicate dividend event")
            seen.add(d)
            events.append({"date":d,"amount_per_share":_rounded_reference_price(item.get("amount_per_share"))})
        events.sort(key=lambda x:x["date"])
        as_of=events[-1]["date"]
        normalized={"symbol":symbol,"currency":expected,"as_of":as_of,
                    "events":events,"cash_applied":False,
                    "payment_dates_verified":False}
        quality="RESEARCH_ONLY"
    elif kind=="FUNDAMENTALS":
        if provider!="SEC_EDGAR" or symbol in ("US","KR","USD/KRW"):
            raise ValueError("unsupported fundamentals provider")
        if payload.get("status")!="ok" or payload.get("symbol")!=symbol:
            raise ValueError("unverified fundamentals")
        # Facts used only for research, not securities pricing. No fabricated
        # financial ratios: accept a subset of explicitly sourced fiscal facts.
        facts=payload.get("facts")
        if not isinstance(facts,list) or not 1<=len(facts)<=50:
            raise ValueError("missing SEC facts")
        output=[]
        for item in facts:
            if not isinstance(item,dict) or item.get("unit") not in ("USD","shares"):
                raise ValueError("invalid SEC fact")
            filed=_date(item.get("filed"),now,max_age=2000)
            period=_date(item.get("period_end"),now,max_age=2000)
            if period>filed:raise ValueError("SEC fiscal period after filing")
            name=item.get("tag")
            if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,80}",name):
                raise ValueError("invalid SEC XBRL tag")
            value=item.get("value")
            if isinstance(value,bool) or value is None:raise ValueError("invalid SEC value")
            try: numerical=Decimal(str(value))
            except (TypeError,ValueError,InvalidOperation):
                raise ValueError("invalid SEC value") from None
            if (not numerical.is_finite() or abs(numerical)>Decimal("1000000000000000")
                    or numerical.as_tuple().exponent < -6):
                raise ValueError("invalid SEC magnitude")
            output.append({"tag":name,"value":format(numerical,"f"),
                "unit":item["unit"],"filed":filed,"period_end":period,
                "form":item.get("form",""),"accession":item.get("accession","")})
        as_of=max(x["filed"] for x in output)
        normalized={"symbol":symbol,"as_of":as_of,"facts":output,
                    "source_kind":"SEC_XBRL_FACTS","ratios_computed":False}
        quality="RESEARCH_ONLY"
    else:
        raise ValueError("unsupported data")
    packed=json.dumps(normalized,ensure_ascii=False,sort_keys=True,
                      allow_nan=False,separators=(",",":"))
    if len(packed.encode("utf-8"))>RECORD_LIMIT:
        raise ValueError("oversized validated content")
    return {"kind":kind,"symbol":symbol,"provider":provider,
            "source_url":url,"as_of":as_of,"currency":normalized.get("currency"),
            "quality":quality,"recorded_utc":now.isoformat(),"payload":normalized,
            "sha256":hashlib.sha256(packed.encode("utf-8")).hexdigest(),
            "payload_json":packed,"raw_payload":payload}


class MarketStore:
    def __init__(self, path: Path):
        self.path=Path(path)
        if self.path.is_symlink() or not self.path.parent.is_dir() or self.path.parent.is_symlink():
            raise ValueError("unsafe separate market cache path")

    @classmethod
    def for_portfolios(cls,portfolios):
        return cls(portfolios.path.with_name("stage1-market-cache.sqlite3"))

    def _db(self):
        if self.path.is_symlink():raise ValueError("market cache symlink")
        if self.path.exists():
            info=self.path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or (os.name=="posix" and (info.st_uid!=os.getuid() or info.st_mode & 0o077)):
                raise ValueError("unsafe cache owner or mode")
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        try:
            if os.name=="posix":os.chmod(self.path,0o600)
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS market_snapshots(
                kind TEXT NOT NULL,symbol TEXT NOT NULL,provider TEXT NOT NULL,
                source_url TEXT NOT NULL,as_of TEXT NOT NULL,currency TEXT,
                quality TEXT NOT NULL,recorded_utc TEXT NOT NULL,
                sha256 TEXT NOT NULL,payload_json TEXT NOT NULL,
                PRIMARY KEY(kind,symbol,provider,sha256))""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_market_latest
                          ON market_snapshots(kind,symbol,recorded_utc DESC)""")
            db.execute("""CREATE TABLE IF NOT EXISTS market_attempts(
                kind TEXT NOT NULL,symbol TEXT NOT NULL,provider TEXT NOT NULL,
                checked_utc TEXT NOT NULL,status TEXT NOT NULL,
                error_code TEXT, retry_after_utc TEXT NOT NULL,
                PRIMARY KEY(kind,symbol,provider))""")
            return db
        except Exception:
            db.close()
            raise

    def save(self,snapshot):
        if not isinstance(snapshot,dict) or snapshot.get("kind") not in CATEGORIES:
            raise ValueError("unvalidated snapshot")
        checked=validated_snapshot(snapshot["kind"],snapshot["symbol"],
                                   snapshot["provider"],snapshot["source_url"],
                                   snapshot["raw_payload"],now=datetime.fromisoformat(snapshot["recorded_utc"]))
        if checked["sha256"]!=snapshot.get("sha256"):
            raise ValueError("snapshot hash mismatch")
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""INSERT OR IGNORE INTO market_snapshots
                    (kind,symbol,provider,source_url,as_of,currency,quality,
                     recorded_utc,sha256,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    tuple(checked[k] for k in ("kind","symbol","provider","source_url",
                                               "as_of","currency","quality","recorded_utc",
                                               "sha256","payload_json")))
                inserted=db.execute("SELECT changes()").fetchone()[0]==1
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return {"saved":inserted,"sha256":checked["sha256"],
                "kind":checked["kind"],"symbol":checked["symbol"]}

    def record_attempt(self,kind,symbol,provider,status,error_code=None,*,now=None,retry_seconds=1800):
        if (kind not in CATEGORIES or provider not in PROVIDERS or not isinstance(symbol,str)
                or not ALLOWED_SYMBOL.fullmatch(symbol) or status not in ("ok","unavailable")
                or (error_code is not None and (not isinstance(error_code,str)
                or not re.fullmatch(r"[A-Z_]{1,45}",error_code)))
                or type(retry_seconds) is not int or not 60<=retry_seconds<=86400):
            raise ValueError("invalid collection attempt")
        clock=_aware(now)
        next_time=clock+timedelta(seconds=retry_seconds)
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""INSERT INTO market_attempts VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT(kind,symbol,provider) DO UPDATE SET
                  checked_utc=excluded.checked_utc,status=excluded.status,
                  error_code=excluded.error_code,retry_after_utc=excluded.retry_after_utc""",
                  (kind,symbol,provider,clock.isoformat(),status,error_code,next_time.isoformat()))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def due(self,kind,symbol,provider,*,now=None):
        with closing(self._db()) as db:
            row=db.execute("""SELECT retry_after_utc FROM market_attempts
                WHERE kind=? AND symbol=? AND provider=?""",(kind,symbol,provider)).fetchone()
        return not row or _aware(now)>=datetime.fromisoformat(row[0])

    def latest(self,kind,symbol,*,now=None):
        if kind not in CATEGORIES or not isinstance(symbol,str) or not ALLOWED_SYMBOL.fullmatch(symbol):
            raise ValueError("invalid cache lookup")
        with closing(self._db()) as db:
            row=db.execute("""SELECT provider,source_url,as_of,currency,quality,
                       recorded_utc,sha256,payload_json FROM market_snapshots
                       WHERE kind=? AND symbol=? ORDER BY recorded_utc DESC LIMIT 1""",
                       (kind,symbol)).fetchone()
        if row is None:return {"status":"unavailable","kind":kind,"symbol":symbol,"reason":"NO_VERIFIED_SNAPSHOT"}
        provider,url,as_of,currency,quality,recorded_utc,digest,packed=row
        age_seconds=max(0,(_aware(now)-datetime.fromisoformat(recorded_utc)).total_seconds())
        # stale snapshots are DISPLAY-ONLY even when the original quote was
        # eligible for an order. Paper orders use fresh provider verification.
        return {"status":"ok" if age_seconds<=3600 else "stale",
                "kind":kind,"symbol":symbol,"provider":provider,
                "source_url":url,"as_of":as_of,"currency":currency,"quality":quality,
                "recorded_utc":recorded_utc,"age_seconds":round(age_seconds,1),
                "sha256":digest,"data":json.loads(packed),
                "tradeable":False,"use_for_paper_execution":False}

    def health(self,*,limit=40):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("invalid limit")
        with closing(self._db()) as db:
            snapshots=db.execute("""SELECT kind,COUNT(*) FROM market_snapshots
                                      GROUP BY kind ORDER BY kind""").fetchall()
            failures=db.execute("""SELECT kind,symbol,provider,checked_utc,status,
                                    error_code,retry_after_utc FROM market_attempts
                                    ORDER BY checked_utc DESC LIMIT ?""",(limit,)).fetchall()
        return {"snapshots":dict(snapshots),
                "recent_attempts":[dict(zip(("kind","symbol","provider","checked_utc",
                    "status","error_code","retry_after_utc"),r)) for r in failures],
                "paper_order_eligible":False,
                "notice":"공개 자료 전용 캐시. 기록된 과거 값·조회 성공 여부만으로 실시간 거래를 승인하지 않습니다."}
