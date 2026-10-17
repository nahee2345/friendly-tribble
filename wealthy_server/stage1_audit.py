"""Structured, immutable Stage1 accounting and corporate-action evidence.

Stage 1 intentionally DOES NOT book dividends, fee adjustments or tax amounts
into spendable virtual cash. Records remain pending until the dedicated
reconciliation/valuation engine (later development step) is tested. Unknown
fees and taxes are NULL, never invented zeros.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .stage1_portfolios import Portfolios, valid_key


CATEGORY = frozenset({
    "DIVIDEND", "DIVIDEND_TAX", "INTEREST", "TRADING_FEE", "TRADING_TAX",
    "FX_FEE", "ADJUSTMENT",
})
CORPORATE = frozenset({"SPLIT", "REVERSE_SPLIT", "MERGER", "DELISTING", "SPINOFF"})
SYMBOL = re.compile(r"^(?:[A-Z][A-Z0-9.]{0,11}|[0-9]{6})$")


def _amount(value: str, currency: str) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Amount must be a decimal string.")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid accounting amount.") from None
    if not number.is_finite() or number == 0 or abs(number) > 100_000_000:
        raise ValueError("Invalid magnitude or nonfinite amount.")
    if number.as_tuple().exponent < (-2 if currency == "USD" else 0):
        raise ValueError("Currency precision invalid.")
    return format(number, "f")


def _timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Timestamp required.")
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise ValueError("Timestamp must be timezone-aware ISO 8601.") from None


class Stage1Audit:
    def __init__(self, portfolios: Portfolios):
        self.portfolios = portfolios

    def accounting_event(self, owner: str, *, event_key: str, portfolio: str,
                         category: str, amount: str, currency: str,
                         observed_at_utc: str, reference: str,
                         evidence_source: str) -> dict:
        if (not valid_key(event_key) or category not in CATEGORY
            or currency not in ("KRW", "USD")
            or not isinstance(reference, str) or not 1 <= len(reference) <= 250
            or not isinstance(evidence_source, str) or not 1 <= len(evidence_source) <= 250):
            raise ValueError("Invalid evidence event.")
        amount = _amount(amount, currency)
        observed = _timestamp(observed_at_utc)
        expected = (portfolio, category, amount, currency, observed, reference, evidence_source)
        with self.portfolios._db() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute("SELECT 1 FROM portfolios WHERE owner=? AND id=?",
                                (owner, portfolio)).fetchone()
            if not exists:
                raise ValueError("Portfolio not owned by caller.")
            prior = db.execute("""SELECT portfolio,category,amount,currency,observed_at_utc,
                                  reference,evidence_source FROM stage1_accounting_events
                                  WHERE owner=? AND event_key=?""", (owner,event_key)).fetchone()
            if prior and prior != expected:
                raise ValueError("Immutable accounting event key collision.")
            if not prior:
                db.execute("""INSERT INTO stage1_accounting_events
                    (owner,event_key,portfolio,category,amount,currency,observed_at_utc,
                    reference,evidence_source,status,created_at_utc)
                    VALUES (?,?,?,?,?,?,?,?,?,'PENDING_RECONCILIATION',?)""",
                    (owner,event_key,*expected,datetime.now(timezone.utc).isoformat()))
            db.execute("COMMIT")
        return {"status":"existing" if prior else "recorded",
                "event_key":event_key, "cash_applied":False}

    def corporate_event(self, owner: str, *, event_key: str, symbol: str,
                        category: str, effective_date: str, terms: dict,
                        evidence_source: str) -> dict:
        if (not valid_key(event_key) or not isinstance(symbol,str)
            or not SYMBOL.fullmatch(symbol) or category not in CORPORATE
            or not isinstance(terms,dict) or not isinstance(evidence_source,str)
            or not 1<=len(evidence_source)<=250):
            raise ValueError("Invalid corporate action.")
        try:
            datetime.strptime(effective_date,"%Y-%m-%d")
            encoded=json.dumps(terms,ensure_ascii=False,sort_keys=True,
                               separators=(",",":"),allow_nan=False)
        except (ValueError,TypeError):
            raise ValueError("Invalid dated corporate action terms.") from None
        if len(encoded)>3000:
            raise ValueError("Corporate action exceeds size bound.")
        expected=(symbol,category,effective_date,encoded,evidence_source)
        with self.portfolios._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM portfolios WHERE owner=? LIMIT 1",(owner,)).fetchone():
                raise ValueError("Owner has no paper portfolio.")
            prior=db.execute("""SELECT symbol,category,effective_date,terms_json,evidence_source
                  FROM stage1_corporate_events WHERE owner=? AND event_key=?""",
                  (owner,event_key)).fetchone()
            if prior and prior!=expected:
                raise ValueError("Immutable corporate action collision.")
            if not prior:
                db.execute("""INSERT INTO stage1_corporate_events
                   (owner,event_key,symbol,category,effective_date,terms_json,
                    evidence_source,created_at_utc) VALUES(?,?,?,?,?,?,?,?)""",
                   (owner,event_key,*expected,datetime.now(timezone.utc).isoformat()))
            db.execute("COMMIT")
        return {"status":"existing" if prior else "recorded",
                "event_key":event_key,"holdings_adjusted":False}

    def overview(self, owner: str) -> dict:
        with self.portfolios._db() as db:
            count = db.execute("""SELECT category,status,COUNT(*) FROM stage1_accounting_events
                                 WHERE owner=? GROUP BY category,status""",(owner,)).fetchall()
            corporate = db.execute("""SELECT category,status,COUNT(*) FROM stage1_corporate_events
                                      WHERE owner=? GROUP BY category,status""",(owner,)).fetchall()
            trades = db.execute("""SELECT COUNT(*) FROM stage1_trade_metadata
                                    WHERE owner=?""",(owner,)).fetchone()[0]
        return {
            "accounting":[{"category":a,"status":s,"count":n} for a,s,n in count],
            "corporate":[{"category":a,"status":s,"count":n} for a,s,n in corporate],
            "trade_receipts":trades,
            "unreconciled_notice":"Stage 1 evidence ledger is audit-only. Dividends/taxes/fees do not change cash until the reconciler is implemented and independently tested."
        }
