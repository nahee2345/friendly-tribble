"""P0: immutable, reproducible PAPER US-session opening NAV evidence.

Reference quotes are obtained from independently validated MarketStore *before*
the SQLite writer lock. They are not executable quotes. There is no fallback
to a planned initial contribution, a caller-supplied nav_source, or a guessed FX.
Actual market provider availability is a separate deployment prerequisite.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, ROUND_HALF_EVEN

from .stage1_turnover_reservations import establish_calendar_checked_session
from .stage1_us_market_calendar import nyse_regular_window

POLICY = "paper-owner-all-portfolios-us-open-v1"
MAX_REFERENCE_AGE = timedelta(days=5)
MAX_CREATION_DELAY = timedelta(minutes=15)
EVIDENCE_CODE = "UNVERIFIED_SESSION_OPENING_NAV"


def _utc(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(EVIDENCE_CODE) from exc


def _number(value, *, zero=False):
    if value is None or isinstance(value, bool):
        raise ValueError(EVIDENCE_CODE)
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise ValueError(EVIDENCE_CODE) from exc
    if not amount.is_finite() or abs(amount) > Decimal("100000000000000") or (amount < 0 if zero else amount <= 0):
        raise ValueError(EVIDENCE_CODE)
    return amount


def _canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(data):
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def _ledger_state(db, owner, opening):
    """Entire pre-open PAPER ledger fingerprint and explicit portfolio IDs.

    Any change to a pre-opening row makes the snapshot unverifiable; later
    legitimate fills do not modify the opening fingerprint.
    """
    tables = (
        ("portfolios", "created_at", "id"),
        ("stage1_events", "created_utc", "event_key"),
        ("portfolio_transfers", "created_at", "request_id"),
        ("virtual_orders", "created_at", "portfolio,request_id"),
    )
    rows = {}
    for table, timestamp, order in tables:
        cols = [c[1] for c in db.execute("PRAGMA table_info(" + table + ")")]
        if not cols or timestamp not in cols or "owner" not in cols:
            raise ValueError(EVIDENCE_CODE)
        entries = db.execute(
            "SELECT * FROM " + table + " WHERE owner=? ORDER BY " + order, (owner,)
        ).fetchall()
        for row in entries:
            stamp = _utc(row[cols.index(timestamp)])
            if stamp > opening:
                # A session must never be back-dated after financial writes.
                raise ValueError("POST_OPEN_LEDGER_EVENT_REQUIRES_RECONCILIATION")
        rows[table] = [list(row) for row in entries]
    account_rows = rows["portfolios"]
    if not account_rows or len(account_rows) > 10:
        raise ValueError(EVIDENCE_CODE)
    portfolio_cols = [c[1] for c in db.execute("PRAGMA table_info(portfolios)")]
    ids = sorted(x[portfolio_cols.index("id")] for x in account_rows)
    if len(set(ids)) != len(ids) or not all(isinstance(x, str) and x for x in ids):
        raise ValueError(EVIDENCE_CODE)
    # Only native owner-scoped PAPER tables; USER_DECLARED_REAL and SAMPLE
    # are intentionally absent from this fingerprint and valuation.
    return {"owner": owner, "scope": ids, "policy": POLICY, "rows": rows}


def _validated_reference(record, kind, symbol, opening):
    if (not isinstance(record, dict) or record.get("kind") != kind
        or record.get("symbol") != symbol
        or record.get("quality") != "VERIFIED_REFERENCE"
        or record.get("status") not in ("ok", "stale")
        or record.get("use_for_paper_execution") is not False
        or not isinstance(record.get("data"), dict)
        or not isinstance(record.get("sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
        or not isinstance(record.get("source_url"), str)
        or not record["source_url"].startswith("https://")
        or not record.get("provider")):
        raise ValueError("UNVERIFIED_NAV_REFERENCE_" + kind)
    observed = _utc(record.get("recorded_utc"))
    confirmed = _utc(record.get("last_verified_utc"))
    if not opening - MAX_REFERENCE_AGE <= observed <= confirmed <= opening:
        raise ValueError("STALE_OR_POST_OPEN_NAV_REFERENCE")
    try:
        as_of = date.fromisoformat(record["as_of"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(EVIDENCE_CODE) from exc
    if not opening.date() - timedelta(days=6) <= as_of <= opening.date():
        raise ValueError("STALE_OR_FUTURE_NAV_REFERENCE")
    data = record["data"]
    if data.get("reference_only") is not True:
        raise ValueError(EVIDENCE_CODE)
    value = _number(data.get("rate") if kind == "FX" else data.get("price"))
    if kind == "FX":
        if (data.get("pair") != "USD/KRW" or data.get("executable") is not False
            or not Decimal("100") <= value <= Decimal("10000")):
            raise ValueError(EVIDENCE_CODE)
    elif (data.get("symbol") != symbol or
          data.get("currency") != ("KRW" if symbol.isdigit() else "USD")):
        raise ValueError(EVIDENCE_CODE)
    return {
        "kind": kind, "symbol": symbol, "provider": record["provider"],
        "source_url": record["source_url"], "source_sha256": record["sha256"],
        "as_of": record["as_of"], "observed_utc": observed.isoformat(),
        "confirmed_utc": confirmed.isoformat(), "value": str(value),
        "currency": "KRW" if kind == "FX" or symbol.isdigit() else "USD",
    }


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_verified_opening_nav(
        owner TEXT NOT NULL, session_date TEXT NOT NULL,
        policy TEXT NOT NULL, start_utc TEXT NOT NULL,
        scope_json TEXT NOT NULL, ledger_sha256 TEXT NOT NULL,
        input_sha256 TEXT NOT NULL, evidence_json TEXT NOT NULL,
        nav_krw TEXT NOT NULL, created_utc TEXT NOT NULL,
        PRIMARY KEY(owner,session_date))""")


def establish_verified_opening_nav(portfolios, market_store, owner, session_date, *, now=None):
    """Build and persist verified reference NAV, but NEVER activate trading.

    No provider HTTP call occurs under the SQLite writer lock. A source cache
    with no matching pre-open reference is insufficient. All owner accounts
    are rechecked after BEGIN IMMEDIATE before writing the immutable proof.
    """
    window = nyse_regular_window(session_date)
    opening, ending = _utc(window["open_utc"]), _utc(window["close_utc"])
    instant = _utc(now or datetime.now(timezone.utc))
    if not opening <= instant < min(ending, opening + MAX_CREATION_DELAY):
        raise ValueError("NAV_CREATION_WINDOW_EXPIRED")
    with portfolios._db() as db:
        db.execute("BEGIN")
        before = _ledger_state(db, owner, opening)
        pairs = set()
        for account in before["scope"]:
            _, usd, holdings, _ = portfolios._money(db, owner, account)
            if usd: pairs.add(("FX", "USD/KRW"))
            for symbol, qty in holdings.items():
                if qty < 0: raise ValueError("NEGATIVE_PAPER_HOLDING")
                if qty: pairs.add(("PRICE", symbol))
        db.execute("COMMIT")
    # Pre-acquired evidence is held immutable until a new writer rechecks
    # the portfolio scope and ledger digest.
    references = market_store.latest_many(sorted(pairs), now=opening)
    checked = {f"{kind}:{sym}": _validated_reference(references.get((kind, sym)),
                 kind, sym, opening) for kind, sym in sorted(pairs)}
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        again = _ledger_state(db, owner, opening)
        if again != before: raise ValueError("NAV_LEDGER_CHANGED_DURING_PREFLIGHT")
        total = Decimal(0)
        fx = _number(checked["FX:USD/KRW"]["value"]) if "FX:USD/KRW" in checked else None
        for account in before["scope"]:
            cash, usd, holdings, _ = portfolios._money(db, owner, account)
            if cash < 0 or usd < 0: raise ValueError("NEGATIVE_OPENING_CASH")
            total += cash
            if usd:
                if fx is None: raise ValueError(EVIDENCE_CODE)
                total += usd * fx
            for symbol, quantity in holdings.items():
                if quantity < 0: raise ValueError("NEGATIVE_PAPER_HOLDING")
                if quantity:
                    ref = checked.get("PRICE:" + symbol)
                    if ref is None: raise ValueError(EVIDENCE_CODE)
                    unit = _number(ref["value"])
                    total += unit * quantity * (fx if ref["currency"] == "USD" else Decimal(1))
        if total <= 0: raise ValueError(EVIDENCE_CODE)
        nav = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        payload = {
            "version": POLICY, "owner": owner, "session_date": session_date,
            "start_utc": opening.isoformat(), "end_utc": ending.isoformat(),
            "scope": before["scope"], "ledger_sha256": _hash(before),
            "prices_and_fx": checked, "opening_nav_krw": format(nav, "f"),
            "unrounded_nav_krw": str(total),
        }
        digest = _hash(payload)
        _schema(db)
        db.execute("""INSERT OR IGNORE INTO stage1_verified_opening_nav
          VALUES (?,?,?,?,?,?,?,?,?,?)""", (
            owner,session_date,POLICY,opening.isoformat(),
            _canonical(before["scope"]),_hash(before),digest,
            _canonical(payload),format(nav,"f"),instant.isoformat()))
        row = db.execute("""SELECT input_sha256,nav_krw FROM stage1_verified_opening_nav
                            WHERE owner=? AND session_date=?""",
                         (owner,session_date)).fetchone()
        if row != (digest,format(nav,"f")):
            raise ValueError("IMMUTABLE_OPENING_NAV_CONFLICT")
        establish_calendar_checked_session(
            db, owner=owner, session_date=session_date,
            opening_nav_krw=format(nav, "f"),
            nav_source="sha256:"+digest,
            verified_utc=instant.isoformat())
        db.execute("COMMIT")
    return {"session_date":session_date,"nav_krw":format(nav,"f"),
            "cap_krw":str(nav*Decimal("0.20")),"proof_sha256":digest,
            "scope":before["scope"],"policy":POLICY,"execution_authorized":False}


def assert_verified_opening_nav(db, owner, session_date, now):
    """Validate immutable proof and original pre-open row revision in writer."""
    window = nyse_regular_window(session_date)
    opening, ending = _utc(window["open_utc"]), _utc(window["close_utc"])
    if not opening <= _utc(now) < ending:
        raise ValueError("US_MARKET_SESSION_NOT_VERIFIED_OR_CLOSED")
    _schema(db)
    row = db.execute("""SELECT policy,start_utc,scope_json,ledger_sha256,
                       input_sha256,evidence_json,nav_krw FROM stage1_verified_opening_nav
                       WHERE owner=? AND session_date=?""",(owner,session_date)).fetchone()
    if row is None or row[0] != POLICY or row[1] != opening.isoformat():
        raise ValueError(EVIDENCE_CODE)
    try:
        data = json.loads(row[5])
        scope = json.loads(row[2])
    except (ValueError, TypeError) as exc:
        raise ValueError("CORRUPT_OPENING_NAV_PROOF") from exc
    if (_hash(data) != row[4] or data.get("ledger_sha256") != row[3]
        or data.get("scope") != scope or data.get("opening_nav_krw") != row[6]
        or data.get("start_utc") != opening.isoformat()
        or row[3] != _hash(_ledger_state_before(db, owner, opening))):
        raise ValueError("CORRUPT_OR_CHANGED_OPENING_NAV_PROOF")
    session=db.execute("""SELECT opening_nav_krw,nav_source,start_utc,end_utc
            FROM stage1_turnover_sessions WHERE owner=? AND session_date=?""",
            (owner,session_date)).fetchone()
    if session != (row[6],"sha256:"+row[4],opening.isoformat(),ending.isoformat()):
        raise ValueError("OPENING_NAV_SESSION_MISMATCH")
    return {"session_date":session_date,"nav_krw":row[6],
            "scope":scope,"proof_sha256":row[4],"policy":POLICY}


def _ledger_state_before(db, owner, opening):
    """Reconstruct only opening rows; later valid trades must not alter NAV."""
    tables = (
        ("portfolios","created_at","id"),
        ("stage1_events","created_utc","event_key"),
        ("portfolio_transfers","created_at","request_id"),
        ("virtual_orders","created_at","portfolio,request_id"),
    )
    snapshot = {}
    for table, timestamp, ordering in tables:
        rows=db.execute(
            "SELECT * FROM "+table+" WHERE owner=? AND "+timestamp+"<=? ORDER BY "+ordering,
            (owner,opening.isoformat())).fetchall()
        snapshot[table]=[list(row) for row in rows]
    ids=sorted(row[1] for row in snapshot["portfolios"])
    return {"owner":owner,"scope":ids,"policy":POLICY,"rows":snapshot}
