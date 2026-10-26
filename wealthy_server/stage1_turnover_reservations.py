"""P0 owner/session-wide PAPER turnover reservation *building block*.

A caller must already hold BEGIN IMMEDIATE on the SAME SQLite connection used
for virtual_orders and approval commits. This module never trades by itself.
Existing Stage4 and manual trade routes DO NOT call it yet: deploying this file
alone does not enforce a 20% limit, nor prove NAV/market-session authenticity.
It refuses missing session proofs and unreconciled historic orders rather than
manufacture NAV, FX or cancelled reservations.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from .p0_turnover import evaluate_session_turnover

ACTIVE = frozenset({"PENDING", "RESERVED", "UNKNOWN", "FILLED"})
CLOSED = frozenset({"REJECTED", "CANCELED", "EXPIRED"})
STATES = ACTIVE | CLOSED
US_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9:_-]{8,100}$")


def _number(value, *, zero=False):
    if value is None or isinstance(value, bool):
        raise ValueError("missing or invalid PAPER notional")
    try:
        x = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("invalid PAPER notional") from exc
    if not x.is_finite() or (x < 0 if zero else x <= 0) or x.as_tuple().exponent < -6:
        raise ValueError("unverified PAPER notional")
    return x


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("market session instant missing")
    try:
        x = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("bad session UTC instant") from exc
    if x.tzinfo is None:
        raise ValueError("timezone required")
    return x.astimezone(timezone.utc).isoformat()


def _day(value):
    if not isinstance(value, str) or not US_DATE.fullmatch(value):
        raise ValueError("market session date required")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise ValueError("invalid market session date") from exc
    return value


def ensure_schema(db: sqlite3.Connection):
    """Non-destructive tables, no legacy transaction rewrite."""
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_turnover_sessions(
        owner TEXT NOT NULL, session_date TEXT NOT NULL,
        opening_nav_krw TEXT NOT NULL, nav_source TEXT NOT NULL,
        verified_utc TEXT NOT NULL, start_utc TEXT NOT NULL,
        end_utc TEXT NOT NULL,
        PRIMARY KEY(owner,session_date))""")
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_turnover_claims(
        owner TEXT NOT NULL, request_id TEXT NOT NULL,
        session_date TEXT NOT NULL, account TEXT NOT NULL,
        symbol TEXT NOT NULL, side TEXT NOT NULL,
        quantity INTEGER NOT NULL, gross_krw TEXT NOT NULL,
        state TEXT NOT NULL, reconciled_no_fill INTEGER NOT NULL DEFAULT 0,
        recorded_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
        PRIMARY KEY(owner,request_id),
        CHECK(side IN ('BUY','SELL')),
        CHECK(quantity>0),
        CHECK(state IN ('PENDING','RESERVED','UNKNOWN','FILLED',
                        'REJECTED','CANCELED','EXPIRED')))""")


def _locked(db):
    if not db.in_transaction:
        raise ValueError("BEGIN IMMEDIATE transaction required")
    # sqlite3.in_transaction alone accepts BEGIN DEFERRED. Force a harmless
    # writer statement on THIS connection before reading NAV/claims so a
    # concurrent process cannot reserve capacity between read and insert.
    # The 0-row UPDATE still takes SQLite's writer lock (verified with a
    # competing BEGIN IMMEDIATE); no existing session value is changed.
    try:
        ensure_schema(db)
        db.execute("""UPDATE stage1_turnover_sessions
            SET opening_nav_krw=opening_nav_krw WHERE owner=?""",
            ("__internal_no_such_owner_writer_barrier__",))
    except sqlite3.OperationalError as error:
        raise ValueError("TURNOVER_SQLITE_WRITE_LOCK_UNAVAILABLE") from error


def establish_session(db, *, owner, session_date, opening_nav_krw,
                      nav_source, verified_utc, start_utc, end_utc):
    """Record an immutable, caller-verified US market session NAV.

    nav_source must be a real identified provider/valuation proof supplied
    by an upstream verifier, not a placeholder. This method does not certify
    provider authenticity.
    """
    _locked(db);ensure_schema(db)
    day=_day(session_date);nav=_number(opening_nav_krw)
    if (not isinstance(owner,str) or not owner or not isinstance(nav_source,str)
        or len(nav_source.strip())<5 or nav_source.strip().lower() in
        {"unknown","sample","test","unverified","none","placeholder"}):
        raise ValueError("verified opening NAV provenance required")
    start=_instant(start_utc);end=_instant(end_utc);verified=_instant(verified_utc)
    if start>=end or not start<=verified<=end:
        raise ValueError("invalid verified US session window")
    data=(owner,day,format(nav,"f"),nav_source.strip(),verified,start,end)
    db.execute("""INSERT OR IGNORE INTO stage1_turnover_sessions
                  VALUES(?,?,?,?,?,?,?)""", data)
    old=db.execute("""SELECT owner,session_date,opening_nav_krw,
                        nav_source,verified_utc,start_utc,end_utc
                        FROM stage1_turnover_sessions
                        WHERE owner=? AND session_date=?""",(owner,day)).fetchone()
    if old!=data:
        raise ValueError("opening NAV/session proof is immutable")
    return {"session_date":day,"cap_krw":str(nav*Decimal("0.20"))}


def _session(db,owner,day):
    row=db.execute("""SELECT opening_nav_krw,start_utc,end_utc,verified_utc
                      FROM stage1_turnover_sessions
                      WHERE owner=? AND session_date=?""",(owner,day)).fetchone()
    if row is None:
        raise ValueError("UNVERIFIED_SESSION_OPENING_NAV")
    return row


def _coverage(db,owner,day):
    """Reject all fills that predate integration and unclaimed Stage4 work."""
    nav,start,end,verified=_session(db,owner,day)
    claims=db.execute("""SELECT request_id,session_date,side,gross_krw,
                     state,reconciled_no_fill,account,symbol,quantity
                     FROM stage1_turnover_claims
                     WHERE owner=? AND session_date=?""",(owner,day)).fetchall()
    claim_map={row[0]:row for row in claims}
    known={r[0] for r in claims}
    seen_fills=set()
    orders=db.execute("""SELECT portfolio,request_id,symbol,side,quantity
                  FROM virtual_orders WHERE owner=? AND created_at>=?
                  AND created_at<?""",(owner,start,end)).fetchall()
    for account,request,symbol,side,qty in orders:
        if request not in known:
            raise ValueError("UNRECONCILED_LEGACY_PAPER_FILL")
        claim=claim_map[request]
        if (claim[4]!="FILLED" or
            (claim[6],claim[7],claim[2],claim[8])!=(account,symbol,side,qty)):
            raise ValueError("CLAIM_AND_FILL_CONFLICT")
        seen_fills.add(request)
    for row in claims:
        if row[4]=="FILLED" and row[0] not in seen_fills:
            raise ValueError("MISSING_FILLED_LEDGER_ROW")
    table=db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                     AND name='stage4_execution_claims'""").fetchone()
    if table:
        unresolved=db.execute("""SELECT 1 FROM stage4_execution_claims
                  WHERE owner=? AND status IN ('CLAIMED','UNKNOWN')
                  LIMIT 1""",(owner,)).fetchone()
        if unresolved:
            raise ValueError("UNRECONCILED_STAGE4_CLAIM")
    return nav,claims,start,end,verified


def reserve(db, *, owner, session_date, request_id, account, symbol, side,
            quantity, gross_krw, now_utc, state="RESERVED"):
    """Reserve all accounts' BUY+SELL gross under one 20% cap, atomically.

    Same request is idempotent only when its original semantics are identical.
    Historical/unknown claims remain reserved. Never automatically cancel.
    """
    _locked(db);ensure_schema(db)
    day=_day(session_date)
    if (not isinstance(request_id,str) or not SAFE_ID.fullmatch(request_id)
        or not isinstance(owner,str) or not owner or
        not isinstance(account,str) or not account or
        not isinstance(symbol,str) or not symbol or
        side not in ("BUY","SELL") or type(quantity) is not int or quantity<=0
        or state not in ("PENDING","RESERVED")):
        raise ValueError("invalid reservation identity")
    stamp=_instant(now_utc);gross=_number(gross_krw)
    nav,claims,start,end,verified=_coverage(db,owner,day)
    prior=db.execute("""SELECT session_date,account,symbol,side,quantity,
                      gross_krw,state FROM stage1_turnover_claims
                      WHERE owner=? AND request_id=?""",(owner,request_id)).fetchone()
    if prior:
        if prior[:6]!=(day,account,symbol,side,quantity,format(gross,"f")):
            raise ValueError("CONFLICTING_REQUEST_ID")
        if prior[6] in CLOSED:
            raise ValueError("CLOSED_REQUEST_CANNOT_REPLAY")
        if prior[6]=="UNKNOWN":
            raise ValueError("UNKNOWN_RESERVATION_NEEDS_RECONCILIATION")
        # This is only a status/reconciliation response. A fill callsite
        # MUST require status=RESERVED for a brand-new reservation: an
        # existing pending/claimed ID must never implicitly trigger a fill.
        return {"status":"already_reserved","request_id":request_id,
                "state":prior[6]}
    if not verified<=stamp<end or stamp<start:
        raise ValueError("US_MARKET_SESSION_NOT_VERIFIED_OR_CLOSED")
    rows=[{"request_id":r[0],"session_date":r[1],"side":r[2],
           "gross_notional_krw":r[3],"status":r[4],
           "reconciled_no_fill":r[5]==1} for r in claims]
    verdict=evaluate_session_turnover(session_date=day,
       verified_opening_nav_krw=nav,records=rows,proposed_gross_krw=gross)
    if not verdict["permitted"]:
        raise ValueError("GROSS_TURNOVER_CAP_EXCEEDED")
    db.execute("""INSERT INTO stage1_turnover_claims VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (owner,request_id,day,account,symbol,side,quantity,
               format(gross,"f"),state,0,stamp,stamp))
    return {"status":state,"request_id":request_id,
            "remaining_before_claim_krw":verdict["remaining_krw"]}


def mark_unknown(db,owner,request_id,now_utc):
    _locked(db);ensure_schema(db)
    stamp=_instant(now_utc)
    old=db.execute("""SELECT state FROM stage1_turnover_claims
            WHERE owner=? AND request_id=?""",(owner,request_id)).fetchone()
    if not old or old[0] in CLOSED or old[0]=="FILLED":
        raise ValueError("cannot mark nonexistent or settled request unknown")
    db.execute("""UPDATE stage1_turnover_claims
                SET state='UNKNOWN',updated_utc=? WHERE owner=?
                AND request_id=?""",(stamp,owner,request_id))


def mark_filled(db,owner,request_id,now_utc):
    """Only transition in same transaction after matching virtual_orders INSERT."""
    _locked(db);ensure_schema(db);stamp=_instant(now_utc)
    claim=db.execute("""SELECT account,symbol,side,quantity,state
                   FROM stage1_turnover_claims WHERE owner=? AND request_id=?""",
                   (owner,request_id)).fetchone()
    if not claim or claim[4] in CLOSED:
        raise ValueError("missing or cancelled claim")
    order=db.execute("""SELECT portfolio,symbol,side,quantity FROM virtual_orders
                   WHERE owner=? AND request_id=?""",(owner,request_id)).fetchall()
    if len(order)!=1 or order[0]!=claim[:4]:
        raise ValueError("cannot finalize without matching PAPER fill")
    db.execute("""UPDATE stage1_turnover_claims SET state='FILLED',updated_utc=?
                WHERE owner=? AND request_id=?""",(stamp,owner,request_id))


def release_without_fill(db,owner,request_id,now_utc,reason):
    """Release only a demonstrably unfilled claim; never release UNKNOWN."""
    _locked(db);ensure_schema(db)
    if reason not in CLOSED:
        raise ValueError("invalid no-fill release")
    row=db.execute("""SELECT state FROM stage1_turnover_claims
                  WHERE owner=? AND request_id=?""",(owner,request_id)).fetchone()
    if not row or row[0] not in {"PENDING","RESERVED"}:
        raise ValueError("unreconciled or irreversible request")
    if db.execute("""SELECT 1 FROM virtual_orders
                  WHERE owner=? AND request_id=?""",(owner,request_id)).fetchone():
        raise ValueError("paper fill exists; cannot release claim")
    db.execute("""UPDATE stage1_turnover_claims
                  SET state=?,reconciled_no_fill=1,updated_utc=?
                  WHERE owner=? AND request_id=?""",
               (reason,_instant(now_utc),owner,request_id))
