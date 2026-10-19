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
def list_assets(portfolios,owner):
    with portfolios._db() as db:
        _schema(db)
        rows=db.execute("SELECT account,symbol,currency,quantity,updated_at FROM stage10_manual_assets WHERE owner=? ORDER BY account,symbol",(owner,)).fetchall()
        balances=db.execute("SELECT label,currency,amount,updated_at FROM stage10_manual_balances WHERE owner=? ORDER BY label,currency",(owner,)).fetchall()
        history=db.execute("SELECT account,symbol,currency,side,quantity,unit_price,trade_date,recorded_at FROM stage10_manual_trade_history WHERE owner=? ORDER BY recorded_at DESC,event_id DESC LIMIT 100",(owner,)).fetchall()
    return {"items":[{"account":a,"symbol":s,"currency":c,"quantity":q,"updated_at":t} for a,s,c,q,t in rows],
            "balances":[{"label":label,"currency":currency,"amount":amount,"updated_at":stamp} for label,currency,amount,stamp in balances],
            "history":[{"account":a,"symbol":s,"currency":cc,"side":side,"quantity":q,"unit_price":p,"trade_date":d,"recorded_at":t} for a,s,cc,side,q,p,d,t in history],
            "scope":"USER_DECLARED_ONLY","included_in_paper_nav":False}
def adjust(portfolios,owner,account,symbol,currency,quantity,side,*,unit_price=None,trade_date=None):
    symbol=symbol.upper().strip() if isinstance(symbol,str) else ''
    if not SYMBOL.fullmatch(symbol) or currency not in ('KRW','USD') or side not in ('ADD','REMOVE'):
        raise ValueError('invalid adjustment')
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
        row=db.execute("SELECT quantity FROM stage10_manual_assets WHERE owner=? AND account=? AND symbol=? AND currency=?",(owner,account,symbol,currency)).fetchone()
        current=Decimal(row[0]) if row else Decimal(0)
        updated=current+delta if side=='ADD' else current-delta
        if updated<0:raise ValueError('insufficient declared holdings')
        if updated==0:
            db.execute("DELETE FROM stage10_manual_assets WHERE owner=? AND account=? AND symbol=? AND currency=?",(owner,account,symbol,currency))
        else:
            db.execute("""INSERT INTO stage10_manual_assets VALUES(?,?,?,?,?,?)
             ON CONFLICT(owner,account,symbol,currency) DO UPDATE SET quantity=excluded.quantity,updated_at=excluded.updated_at""",
             (owner,account,symbol,currency,str(updated),datetime.now(timezone.utc).isoformat()))
        db.execute("""INSERT INTO stage10_manual_trade_history VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (owner,uuid4().hex,account,symbol,currency,side,str(delta),
                    str(price) if price is not None else None,
                    trade_day.isoformat() if trade_day is not None else None,
                    datetime.now(timezone.utc).isoformat()))
        db.execute("COMMIT")
    return {"symbol":symbol,"quantity":str(updated),"scope":"USER_DECLARED_ONLY",
            "paper_orders_added":False,"unit_price_recorded":price is not None}

def adjust_balance(portfolios,owner,label,currency,amount,side):
    """Record personal cash/other monetary assets, never alter Stage1 paper balances."""
    if not isinstance(label,str) or not 1<=len(label.strip())<=60 or any(ord(v)<32 for v in label):
        raise ValueError('invalid label')
    if currency not in ('KRW','USD') or side not in ('ADD','REMOVE'):
        raise ValueError('invalid balance adjustment')
    delta=_amount(amount);label=label.strip()
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        row=db.execute("SELECT amount FROM stage10_manual_balances WHERE owner=? AND label=? AND currency=?",(owner,label,currency)).fetchone()
        current=Decimal(row[0]) if row else Decimal(0)
        updated=current+delta if side=='ADD' else current-delta
        if updated<0:raise ValueError('insufficient manual balance')
        if updated==0:
            db.execute("DELETE FROM stage10_manual_balances WHERE owner=? AND label=? AND currency=?",(owner,label,currency))
        else:
            db.execute("""INSERT INTO stage10_manual_balances VALUES(?,?,?,?,?)
               ON CONFLICT(owner,label,currency) DO UPDATE SET amount=excluded.amount,updated_at=excluded.updated_at""",
               (owner,label,currency,str(updated),datetime.now(timezone.utc).isoformat()))
        db.execute("COMMIT")
    return {"label":label,"currency":currency,"amount":str(updated),"scope":"USER_DECLARED_ONLY","paper_ledger_modified":False}
