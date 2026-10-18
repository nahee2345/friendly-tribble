"""Stage 9: local CSV import into Stage1 search-only security catalog. No trading."""
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

def import_listing(portfolios, filename, *, market, source, at=None, max_rows=200000):
    if market not in ("US","KR") or not isinstance(source,str) or not source.strip() or len(source)>180:
        raise ValueError("invalid market/source")
    if type(max_rows) is not int or not 1<=max_rows<=200000:
        raise ValueError("invalid limit")
    path=Path(filename)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>30000000:
        raise ValueError("unsafe/oversized CSV")
    at=at or datetime.now(timezone.utc)
    if not isinstance(at,datetime) or at.tzinfo is None:
        raise ValueError("timezone required")
    stamp=at.astimezone(timezone.utc).isoformat()
    pattern=r"[A-Z][A-Z0-9.\\-]{0,14}" if market=="US" else r"[0-9]{6}"
    rows=[];seen=set()
    with path.open(encoding="utf-8-sig",newline="") as f:
        rd=csv.DictReader(f)
        if rd.fieldnames is None or not {"symbol","name","kind"}.issubset(rd.fieldnames):
            raise ValueError("CSV needs symbol,name,kind")
        for x in rd:
            if len(rows)>=max_rows or None in x or not all(isinstance(x.get(k),str) for k in ("symbol","name","kind")):
                raise ValueError("malformed/oversized rows")
            symbol,name,kind=(x[k].strip() for k in ("symbol","name","kind"))
            symbol=symbol.upper();kind=kind.upper()
            if not re.fullmatch(pattern,symbol) or not name or len(name)>190 or any(ord(c)<32 for c in name):
                raise ValueError("invalid symbol/name")
            if kind not in ("STOCK","ETF","OTHER") or symbol in seen:
                raise ValueError("invalid kind/duplicate")
            seen.add(symbol)
            rows.append((market,symbol,name,kind,"user CSV: "+source.strip(),stamp))
    if not rows:raise ValueError("empty list")
    from .stage1_catalog import _schema
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        db.executemany("""INSERT INTO stage1_security_master
          (market,symbol,name,kind,source,updated_at) VALUES(?,?,?,?,?,?)
          ON CONFLICT(market,symbol) DO UPDATE SET name=excluded.name,
          kind=excluded.kind,source=excluded.source,updated_at=excluded.updated_at""",rows)
        db.execute("COMMIT")
    return {"market":market,"imported":len(rows),"is_complete":False,"tradeable":False}
