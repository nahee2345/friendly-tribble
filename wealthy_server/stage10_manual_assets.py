"""Owner-scoped self-declared holdings and asset adjustments, separate from paper fills."""
import re
from datetime import date,datetime,timezone
from uuid import uuid4
from decimal import Decimal,InvalidOperation

SYMBOL=re.compile(r"(?:[A-Z][A-Z0-9.\-]{0,14}|[0-9]{6})\Z")
def _amount(raw):
    try:
        if isinstance(raw,bool):raise ValueError
        d=Decimal(str(raw))
        if not d.is_finite() or d<=0 or d>Decimal('100000000000') or d.as_tuple().exponent< -4:raise ValueError
        return d
    except (InvalidOperation,TypeError,ValueError):raise ValueError('invalid quantity') from None
def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage10_manual_assets(
      owner TEXT NOT NULL, account TEXT NOT NULL,symbol TEXT NOT NULL,
      currency TEXT NOT NULL CHECK(currency IN ('KRW','USD')),
      quantity TEXT NOT NULL,updated_at TEXT NOT NULL,
      PRIMARY KEY(owner,account,symbol,currency))""")
    db.execute("""CREATE TABLE IF NOT EXISTS stage10_manual_balances(
      owner TEXT NOT NULL,label TEXT NOT NULL,currency TEXT NOT NULL CHECK(currency IN ('KRW','USD')),
      amount TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(owner,label,currency))""")
    db.execute("""CREATE TABLE IF NOT EXISTS stage10_manual_trade_history(
      owner TEXT NOT NULL,event_id TEXT NOT NULL,account TEXT NOT NULL,
      symbol TEXT NOT NULL,currency TEXT NOT NULL,side TEXT NOT NULL,
      quantity TEXT NOT NULL,unit_price TEXT,trade_date TEXT,recorded_at TEXT NOT NULL,
      PRIMARY KEY(owner,event_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS stage10_manual_balance_history(
      owner TEXT NOT NULL,event_id TEXT NOT NULL,label TEXT NOT NULL,
      currency TEXT NOT NULL,side TEXT NOT NULL,amount TEXT NOT NULL,
      recorded_at TEXT NOT NULL,PRIMARY KEY(owner,event_id))""")
def list_assets(portfolios,owner):
    """User-declared REAL records; neither PAPER nor SAMPLE contributes.

    Missing acquisition prices, unverified valuations and legacy records
    without history produce explicit unavailable values rather than zeros.
    """
    from .manual_cost_basis import derive_costs
    with portfolios._db() as db:
        _schema(db)
        rows=db.execute(
            "SELECT account,symbol,currency,quantity,updated_at "
            "FROM stage10_manual_assets WHERE owner=? ORDER BY account,symbol,currency",
            (owner,)
        ).fetchall()
        balances=db.execute(
            "SELECT label,currency,amount,updated_at "
            "FROM stage10_manual_balances WHERE owner=? ORDER BY label,currency",
            (owner,)
        ).fetchall()
        history=db.execute(
            "SELECT account,symbol,currency,side,quantity,unit_price,trade_date,recorded_at "
            "FROM stage10_manual_trade_history WHERE owner=? "
            "ORDER BY recorded_at DESC,event_id DESC LIMIT 100",
            (owner,)
        ).fetchall()
        # Full owner-scoped history, never just the last 100 visible rows.
        events=db.execute(
            "SELECT account,symbol,currency,side,quantity,unit_price "
            "FROM stage10_manual_trade_history WHERE owner=? "
            "ORDER BY recorded_at,event_id LIMIT 50001",
            (owner,)
        ).fetchall()
    expected={(account,symbol,currency):quantity for account,symbol,currency,quantity,_ in rows}
    cost_data={}
    if len(events)<=50000:
        try:
            cost_data=derive_costs(
                [dict(zip(("account","symbol","currency","side","quantity","unit_price"),e))
                 for e in events], expected
            )
        except (ValueError,ArithmeticError):
            cost_data={}
    currencies={}
    for account,symbol,currency,_,_ in rows:
        currencies.setdefault((account,symbol),set()).add(currency)
    items=[]
    for account,symbol,currency,quantity,updated_at in rows:
        cost=cost_data.get((account,symbol,currency),{
            "cost_basis_status":"unavailable",
            "average_cost_native":None,"total_cost_native":None,
            "realized_pnl_native":None,"unrealized_pnl_native":None,
            "valuation_status":"requires_verified_market_price",
            "history_reconciled":False,"cost_method":"unavailable",
        })
        if len(currencies[(account,symbol)])!=1:
            cost={**cost,"cost_basis_status":"unavailable","average_cost_native":None,
                  "total_cost_native":None,"realized_pnl_native":None,
                  "history_reconciled":False}
        items.append({
            "account":account,"symbol":symbol,"currency":currency,
            "quantity":quantity,"updated_at":updated_at,
            "scope":"USER_DECLARED_REAL","verified_broker_holding":False,
            "duplicate_currency_conflict":len(currencies[(account,symbol)])!=1,
            **cost
        })
    return {
        "items":items,
        "balances":[{"label":label,"currency":currency,"amount":amount,
                     "updated_at":stamp,"scope":"USER_DECLARED_REAL"}
                    for label,currency,amount,stamp in balances],
        "history":[{"account":a,"symbol":s,"currency":cc,"side":side,
                    "quantity":q,"unit_price":p,"trade_date":d,"recorded_at":t}
                   for a,s,cc,side,q,p,d,t in history],
        "scope":"USER_DECLARED_REAL",
        "included_in_paper_nav":False,
        "included_in_sample_nav":False,
        "verified_real_asset_total_krw":None,
        "real_total_status":"unavailable_without_verified_prices_fx_and_deduplication",
        "aggregate_policy":{
            "PAPER":["PAPER"],
            "USER_DECLARED_REAL":["USER_DECLARED_REAL"],
            "SAMPLE":["SAMPLE"],
            "cross_scope_sum_allowed":False,
            "same_account_symbol_currency_unique":True,
        },
        "notice":"실보유 직접 입력은 증권사 조회로 검증되지 않았으며 PAPER·SAMPLE 합계에 포함되지 않습니다."
    }

def adjust(portfolios,owner,account,symbol,currency,quantity,side,*,unit_price=None,trade_date=None,request_id=None):
    symbol=symbol.upper().strip() if isinstance(symbol,str) else ''
    if not SYMBOL.fullmatch(symbol) or currency not in ('KRW','USD') or side not in ('ADD','REMOVE','BUY','SELL','IN','OUT','CORRECT_ADD','CORRECT_REMOVE'):
        raise ValueError('invalid adjustment')
    if request_id is not None and (not isinstance(request_id,str) or
        not re.fullmatch(r"[A-Za-z0-9_:-]{8,100}",request_id)):
        raise ValueError('invalid request id')
    event_id=request_id or uuid4().hex
    delta=_amount(quantity)
    if unit_price not in (None,''):
        price=_amount(unit_price)
    else:
        price=None
    if trade_date not in (None,''):
        try:
            trade_day=date.fromisoformat(trade_date)
            if trade_day>datetime.now(timezone.utc).date():raise ValueError('future trade date')
        except (TypeError,ValueError):raise ValueError('invalid trade date') from None
    else:
        trade_day=None
    if symbol=='CASH':
        raise ValueError('cash must have a clearly identified account and ledger flow')
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        if not db.execute("SELECT 1 FROM portfolios WHERE owner=? AND id=?",(owner,account)).fetchone():
            raise ValueError('unknown account')
        previous=db.execute(
            "SELECT account,symbol,currency,side,quantity,unit_price,trade_date "
            "FROM stage10_manual_trade_history WHERE owner=? AND event_id=?",
            (owner,event_id)
        ).fetchone()
        intended=(account,symbol,currency,side,str(delta),
                  str(price) if price is not None else None,
                  trade_day.isoformat() if trade_day is not None else None)
        if db.execute(
            "SELECT 1 FROM stage10_manual_balance_history "
            "WHERE owner=? AND event_id=?", (owner,event_id)
        ).fetchone():
            raise ValueError('idempotency key already used by manual cash')
        if previous is not None:
            if previous!=intended:
                raise ValueError('conflicting idempotency key')
            row=db.execute(
                "SELECT quantity FROM stage10_manual_assets "
                "WHERE owner=? AND account=? AND symbol=? AND currency=?",
                (owner,account,symbol,currency)
            ).fetchone()
            db.execute("COMMIT")
            return {"symbol":symbol,"quantity":row[0] if row else "0",
                    "scope":"USER_DECLARED_REAL","status":"already_recorded",
                    "paper_orders_added":False,"unit_price_recorded":price is not None}
        alternate=db.execute(
            "SELECT 1 FROM stage10_manual_assets "
            "WHERE owner=? AND account=? AND symbol=? AND currency<>?",
            (owner,account,symbol,currency)
        ).fetchone()
        if alternate:
            raise ValueError('ambiguous duplicate symbol currency')
        row=db.execute("SELECT quantity FROM stage10_manual_assets WHERE owner=? AND account=? AND symbol=? AND currency=?",(owner,account,symbol,currency)).fetchone()
        current=Decimal(row[0]) if row else Decimal(0)
        updated=current+delta if side in ('ADD','BUY','IN','CORRECT_ADD') else current-delta
        if updated<0:raise ValueError('insufficient declared holdings')
        if updated==0:
            db.execute("DELETE FROM stage10_manual_assets WHERE owner=? AND account=? AND symbol=? AND currency=?",(owner,account,symbol,currency))
        else:
            db.execute("""INSERT INTO stage10_manual_assets VALUES(?,?,?,?,?,?)
             ON CONFLICT(owner,account,symbol,currency) DO UPDATE SET quantity=excluded.quantity,updated_at=excluded.updated_at""",
             (owner,account,symbol,currency,str(updated),datetime.now(timezone.utc).isoformat()))
        db.execute("""INSERT INTO stage10_manual_trade_history VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (owner,event_id,account,symbol,currency,side,str(delta),
                    str(price) if price is not None else None,
                    trade_day.isoformat() if trade_day is not None else None,
                    datetime.now(timezone.utc).isoformat()))
        db.execute("COMMIT")
    return {"symbol":symbol,"quantity":str(updated),"scope":"USER_DECLARED_REAL",
            "paper_orders_added":False,"unit_price_recorded":price is not None}

def adjust_balance(portfolios,owner,label,currency,amount,side,*,request_id=None):
    """Owner-scoped declared cash event; preserved separately from PAPER cash."""
    if not isinstance(label,str) or not 1<=len(label.strip())<=60 or any(ord(v)<32 for v in label):
        raise ValueError('invalid label')
    inflows=('ADD','DEPOSIT','CORRECT_ADD')
    outflows=('REMOVE','WITHDRAW','CORRECT_REMOVE')
    if currency not in ('KRW','USD') or side not in inflows+outflows:
        raise ValueError('invalid balance adjustment')
    if request_id is not None and (not isinstance(request_id,str) or
        not re.fullmatch(r"[A-Za-z0-9_:-]{8,100}",request_id)):
        raise ValueError('invalid request id')
    event_id=request_id or uuid4().hex
    delta=_amount(amount);label=label.strip()
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        previous=db.execute(
            "SELECT label,currency,side,amount FROM stage10_manual_balance_history "
            "WHERE owner=? AND event_id=?", (owner,event_id)
        ).fetchone()
        intended=(label,currency,side,str(delta))
        if db.execute(
            "SELECT 1 FROM stage10_manual_trade_history "
            "WHERE owner=? AND event_id=?", (owner,event_id)
        ).fetchone():
            raise ValueError('idempotency key already used by manual holdings')
        if previous is not None and previous!=intended:
            raise ValueError('conflicting cash adjustment request')
        if previous is None:
            row=db.execute(
                "SELECT amount FROM stage10_manual_balances "
                "WHERE owner=? AND label=? AND currency=?", (owner,label,currency)
            ).fetchone()
            current=Decimal(row[0]) if row else Decimal(0)
            updated=current+delta if side in inflows else current-delta
            if updated<0:
                raise ValueError('insufficient manual balance')
            if updated==0:
                db.execute(
                    "DELETE FROM stage10_manual_balances WHERE owner=? AND label=? AND currency=?",
                    (owner,label,currency)
                )
            else:
                db.execute("""INSERT INTO stage10_manual_balances VALUES(?,?,?,?,?)
                   ON CONFLICT(owner,label,currency) DO UPDATE SET
                   amount=excluded.amount,updated_at=excluded.updated_at""",
                   (owner,label,currency,str(updated),datetime.now(timezone.utc).isoformat()))
            db.execute(
                "INSERT INTO stage10_manual_balance_history VALUES(?,?,?,?,?,?,?)",
                (owner,event_id,label,currency,side,str(delta),datetime.now(timezone.utc).isoformat())
            )
        db.execute("COMMIT")
        row=db.execute(
            "SELECT amount FROM stage10_manual_balances "
            "WHERE owner=? AND label=? AND currency=?", (owner,label,currency)
        ).fetchone()
    return {"label":label,"currency":currency,"amount":row[0] if row else "0",
            "scope":"USER_DECLARED_REAL","paper_ledger_modified":False,
            "status":"already_recorded" if previous is not None else "recorded"}


def request_status(portfolios, owner, request_id):
    """Read-only authenticated reconciliation of one opaque manual write key.

    Absence is NOT proof an in-flight request will never commit. Response does
    not reveal another owner's records, positions, or transaction contents.
    """
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{8,100}", request_id):
        raise ValueError("invalid reconciliation key")
    with portfolios._db() as db:
        _schema(db)
        stock = db.execute(
            "SELECT recorded_at FROM stage10_manual_trade_history "
            "WHERE owner=? AND event_id=?", (owner, request_id)
        ).fetchone()
        cash = db.execute(
            "SELECT recorded_at FROM stage10_manual_balance_history "
            "WHERE owner=? AND event_id=?", (owner, request_id)
        ).fetchone()
    if stock and cash:
        return {"status":"conflict","kind":None,"request_id":request_id,
                "recorded_at":None,"safe_to_resubmit":False}
    if stock or cash:
        row=stock if stock else cash
        return {"status":"recorded","kind":"stock" if stock else "cash",
                "request_id":request_id,"recorded_at":row[0],
                "safe_to_resubmit":False}
    return {"status":"not_found","kind":None,"request_id":request_id,
            "recorded_at":None,"safe_to_resubmit":False}
