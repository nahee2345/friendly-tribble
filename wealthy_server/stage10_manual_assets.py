"""Owner-scoped self-declared holdings and asset adjustments, separate from paper fills."""
import re
from datetime import datetime,timezone
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
def list_assets(portfolios,owner):
    with portfolios._db() as db:
        _schema(db)
        rows=db.execute("SELECT account,symbol,currency,quantity,updated_at FROM stage10_manual_assets WHERE owner=? ORDER BY account,symbol",(owner,)).fetchall()
    return {"items":[{"account":a,"symbol":s,"currency":c,"quantity":q,"updated_at":t} for a,s,c,q,t in rows],
            "scope":"USER_DECLARED_ONLY","included_in_paper_nav":False}
def adjust(portfolios,owner,account,symbol,currency,quantity,side):
    symbol=symbol.upper().strip() if isinstance(symbol,str) else ''
    if not SYMBOL.fullmatch(symbol) or currency not in ('KRW','USD') or side not in ('ADD','REMOVE'):
        raise ValueError('invalid adjustment')
    delta=_amount(quantity)
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
        db.execute("COMMIT")
    return {"symbol":symbol,"quantity":str(updated),"scope":"USER_DECLARED_ONLY","paper_orders_added":False}
