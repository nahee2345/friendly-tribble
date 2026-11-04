"""P0 single-writer PAPER order execution shared by HTTP, Stage4 and FX+BUY.

No network calls, no market collection or FX queries are performed while
holding SQLite BEGIN IMMEDIATE. A verified opening NAV proof is mandatory.
The method is a technical integration candidate, not a release toggle.
"""
from __future__ import annotations

from datetime import datetime, timezone, date, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP, InvalidOperation
from urllib.parse import urlsplit

from .stage1_nav_proof import assert_verified_opening_nav
from .stage1_turnover_reservations import reserve, mark_filled
from .stage1_portfolios import SYMBOL, ISA_ETF_ALLOWED, valid_key

MAX_STAGE4_ORDER = Decimal("250000")
STAGE4_DAY_BUY = Decimal("0.05")


def _dec(value):
    if value is None or isinstance(value, bool):
        raise ValueError("UNVERIFIED_ORDER_AMOUNT")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError("UNVERIFIED_ORDER_AMOUNT") from exc
    if not result.is_finite() or result <= 0 or result > Decimal("1000000000000") or result.as_tuple().exponent < -6:
        raise ValueError("UNVERIFIED_ORDER_AMOUNT")
    return result


def _verified_quote(quote, symbol, now):
    if (not isinstance(quote, dict) or quote.get("status") != "ok"
        or quote.get("tradeable") is not True or quote.get("symbol") != symbol
        or quote.get("currency") != ("KRW" if symbol.isdigit() else "USD")
        or not quote.get("source") or not quote.get("source_url")):
        raise ValueError("UNVERIFIED_EXECUTION_QUOTE")
    # Execution references can only come from the established, bounded
    # public providers, not an arbitrary caller-provided URL.
    host=urlsplit(quote["source_url"]).hostname
    if (urlsplit(quote["source_url"]).scheme!="https" or
        host not in {"stooq.com","query1.finance.yahoo.com","query2.finance.yahoo.com"}):
        raise ValueError("UNVERIFIED_EXECUTION_SOURCE")
    try:
        day=date.fromisoformat(quote["as_of"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("UNVERIFIED_EXECUTION_DATE") from exc
    if not 0 <= (now.date()-day).days <= 4:
        raise ValueError("STALE_EXECUTION_QUOTE")
    return _dec(quote["price"])


def _fx_checked(fx, now):
    # Actual executable brokerage rate is unavailable pre-KIS. This is a
    # reference conversion for PAPER only; never relabel as executable FX.
    if (not isinstance(fx, dict) or fx.get("status")!="ok"
        or fx.get("executable") is not False or
        fx.get("source_url")!="https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW"):
        raise ValueError("UNVERIFIED_PAPER_FX")
    try:
        day=date.fromisoformat(fx["as_of"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("UNVERIFIED_PAPER_FX_DATE") from exc
    if not 0 <= (now.date()-day).days <= 5:
        raise ValueError("STALE_PAPER_FX")
    rate=_dec(fx["usd_krw"])
    if not 100<=rate<=10000:
        raise ValueError("INVALID_PAPER_FX")
    return rate


def execute_verified_paper(
    portfolios, *, owner, account, symbol, side, quantity, request_id,
    quote, session_date, fx=None, auto_fx=False, stage4_plan=None,
    now=None, fail_after=None,
):
    """Single transaction: control→proof→approval→reserve→FX/fill→receipt.

    Test-only fail_after is never an HTTP parameter. All failure injection
    occurs *within* the writer transaction and rolls back completely.
    All replays check one owner-wide ID, not (owner,portfolio,request_id).
    """
    clock=now or datetime.now(timezone.utc)
    if not isinstance(clock,datetime) or clock.tzinfo is None:
        raise ValueError("VERIFIED_CLOCK_REQUIRED")
    clock=clock.astimezone(timezone.utc)
    if (not isinstance(owner,str) or not owner
        or not isinstance(account,str) or not account
        or not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol)
        or side not in ("BUY","SELL") or type(quantity) is not int
        or not 1<=quantity<=100000 or not valid_key(request_id)):
        raise ValueError("INVALID_PAPER_ORDER_IDENTITY")
    if auto_fx and (side!="BUY" or quantity!=1 or account!="default"):
        raise ValueError("INVALID_AUTOMATIC_FX_ORDER")
    price=_verified_quote(quote,symbol,clock)
    currency=quote["currency"]
    rate=_fx_checked(fx,clock) if currency=="USD" else None
    if currency=="KRW" and fx is not None:
        raise ValueError("UNNECESSARY_FX_FOR_KRW")
    notional=price*quantity*(rate if rate is not None else Decimal(1))
    if notional.as_tuple().exponent < -6:
        raise ValueError("UNVERIFIED_ORDER_PRECISION")
    stamp=clock.isoformat()
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        portfolios._assert_not_paused(db,owner)
        proof=assert_verified_opening_nav(db,owner,session_date,clock)
        if account not in proof["scope"]:
            raise ValueError("ACCOUNT_NOT_IN_OPENING_PAPER_SCOPE")
        row=db.execute("SELECT kind FROM portfolios WHERE owner=? AND id=?",
                       (owner,account)).fetchone()
        if row is None:
            raise ValueError("PAPER_ACCOUNT_MISSING")
        kind=row[0]
        if ((currency=="USD" and kind!="GENERAL_OVERSEAS") or
            (currency=="KRW" and kind=="GENERAL_OVERSEAS") or
            (kind=="ISA_SIMULATED" and symbol not in ISA_ETF_ALLOWED)):
            raise ValueError("ACCOUNT_INSTRUMENT_MISMATCH")
        # One globally unique owner request_id across every portfolio.
        existing=db.execute("""SELECT portfolio,symbol,side,quantity,price,currency
                   FROM virtual_orders WHERE owner=? AND request_id=?""",
                   (owner,request_id)).fetchall()
        if existing:
            if len(existing)!=1 or existing[0]!=(account,symbol,side,quantity,format(price,"f"),currency):
                raise ValueError("CONFLICTING_OWNER_REQUEST_ID")
            matched=db.execute("""SELECT session_date,state,account,symbol,side,quantity,
                  gross_krw FROM stage1_turnover_claims
                  WHERE owner=? AND request_id=?""",(owner,request_id)).fetchone()
            if matched != (session_date,"FILLED",account,symbol,side,quantity,format(notional,"f")):
                raise ValueError("UNRECONCILED_PAPER_REPLAY")
            db.execute("COMMIT")
            return {"status":"already_executed","request_id":request_id,
                    "paper_only":True,"execution_authorized":False}
        # A collision in a different account or an unresolved old session
        # must never cause the same request key to be re-executed.
        prior=db.execute("""SELECT account,symbol,side,quantity,gross_krw,state
               FROM stage1_turnover_claims WHERE owner=? AND request_id=?""",
               (owner,request_id)).fetchone()
        if prior is not None:
            raise ValueError("RESERVATION_RECONCILIATION_REQUIRED")
        approval=db.execute("""SELECT account,symbol,side,quantity,amount_krw,
                   status,expires_at FROM stage1_approvals
                   WHERE owner=? AND request_id=?""",(owner,request_id)).fetchone()
        if approval is not None:
            if (approval[:4]!=(account,symbol,side,quantity) or
                approval[5]!="APPROVED" or
                datetime.fromisoformat(approval[6]).astimezone(timezone.utc)<=clock or
                notional>_dec(approval[4])):
                raise ValueError("APPROVAL_SCOPE_AMOUNT_OR_EXPIRY_MISMATCH")
        elif notional>=Decimal("300000"):
            raise ValueError("APPROVAL_REQUIRED_300000_KRW")
        cash_krw,cash_usd,holdings,_=portfolios._money(db,owner,account)
        if side=="SELL" and holdings.get(symbol,0)<quantity:
            raise ValueError("INSUFFICIENT_PAPER_HOLDING")
        fx_needed=Decimal(0)
        fx_received=Decimal(0)
        if side=="BUY":
            if currency=="KRW":
                if cash_krw<price*quantity: raise ValueError("INSUFFICIENT_KRW_CASH")
            elif auto_fx:
                gap=max(Decimal(0),price*quantity-cash_usd)
                if gap:
                    fx_needed=(gap*rate).to_integral_value(rounding=ROUND_UP)
                    while (fx_needed/rate).quantize(Decimal("0.01"),rounding=ROUND_DOWN)<gap:
                        fx_needed+=1
                    fx_received=(fx_needed/rate).quantize(Decimal("0.01"),rounding=ROUND_DOWN)
                    if fx_needed>cash_krw or fx_needed>Decimal("10000000"):
                        raise ValueError("INSUFFICIENT_FX_PAPER_CASH")
            elif cash_usd<price*quantity:
                raise ValueError("INSUFFICIENT_USD_CASH")
        if stage4_plan is not None:
            # Stage4 5% BUY-only cap stays an *additional* conservative limit
            # beside the owner-wide 20% BUY+SELL cap; never replaces it.
            if (not isinstance(stage4_plan,dict) or not isinstance(stage4_plan.get("plan_hash"),str)
                or len(stage4_plan["plan_hash"])<16 or notional>=Decimal("300000")
                or notional>MAX_STAGE4_ORDER):
                raise ValueError("STAGE4_PLAN_INVALID_OR_ORDER_LIMIT")
            if side=="BUY":
                already=db.execute("""SELECT COALESCE(SUM(CAST(gross_krw AS REAL)),0)
                       FROM stage1_turnover_claims WHERE owner=? AND session_date=?
                       AND side='BUY' AND state IN ('PENDING','RESERVED','UNKNOWN','FILLED')""",
                       (owner,session_date)).fetchone()[0]
                # SQLite REAL is only used as conservative precheck; precise
                # Decimal summation is the authoritative decision below.
                rows=db.execute("""SELECT gross_krw FROM stage1_turnover_claims
                        WHERE owner=? AND session_date=? AND side='BUY'
                        AND state IN ('PENDING','RESERVED','UNKNOWN','FILLED')""",
                        (owner,session_date)).fetchall()
                buy_used=sum((Decimal(x[0]) for x in rows),Decimal(0))
                plan_max=_dec(stage4_plan.get("daily_spend_cap_krw"))
                allowed=min(Decimal(proof["nav_krw"])*STAGE4_DAY_BUY,plan_max)
                if buy_used+notional>allowed:
                    raise ValueError("STAGE4_5_PERCENT_CAP_EXCEEDED")
            old=db.execute("""SELECT 1 FROM stage4_execution_claims
                        WHERE owner=? AND request_id=?""",(owner,request_id)).fetchone() if db.execute(
                        "SELECT name FROM sqlite_master WHERE name='stage4_execution_claims'"
                    ).fetchone() else None
            if old: raise ValueError("UNRECONCILED_STAGE4_REQUEST")
        result=reserve(db,owner=owner,session_date=session_date,
                       request_id=request_id,account=account,symbol=symbol,
                       side=side,quantity=quantity,gross_krw=format(notional,"f"),
                       now_utc=stamp)
        if result["status"]!="RESERVED":
            raise ValueError("RESERVATION_RECONCILIATION_REQUIRED")
        if fail_after=="reserve":raise RuntimeError("INJECTED_AFTER_RESERVE")
        if fx_needed:
            db.execute("""INSERT INTO stage1_events VALUES(?,?,?,?,?,?,?)""",
                    (owner,request_id+":fx","virtual_fx",-int(fx_needed),
                    format(fx_received,"f"),format(rate,"f"),stamp))
        if fail_after=="fx":raise RuntimeError("INJECTED_AFTER_FX")
        db.execute("""INSERT INTO virtual_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                   (owner,account,request_id,symbol,side,quantity,format(price,"f"),
                    currency,quote["source"],quote["as_of"],stamp))
        if fail_after=="fill":raise RuntimeError("INJECTED_AFTER_FILL")
        portfolios._receipt(db,owner,account,request_id,quote,
                             format(rate,"f") if rate is not None else None)
        mark_filled(db,owner,request_id,stamp)
        if fail_after=="mark_filled":raise RuntimeError("INJECTED_AFTER_MARK_FILLED")
        if stage4_plan is not None:
            from .stage4_portfolio import _schema
            _schema(db)
            db.execute("""INSERT INTO stage4_execution_claims VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (owner,request_id,stage4_plan["plan_hash"],account,
                 symbol,side,quantity,format(notional,"f"),
                 "EXECUTED","VERIFIED_IN_PAPER_LEDGER",stamp,stamp))
        if fail_after=="stage4":raise RuntimeError("INJECTED_AFTER_STAGE4")
        db.execute("COMMIT")
    return {"status":"executed","request_id":request_id,
            "notional_krw":format(notional,"f"),
            "session_date":session_date,"paper_only":True,
            "execution_authorized":False}
