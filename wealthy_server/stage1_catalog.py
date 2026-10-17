"""No-key listed-security catalog with bounded downloads and local SQLite cache.

Every record has an explicit source; partial data are never called complete.
The US symbol directories are downloadable public reference data. Korean KIND
listing is best-effort and may require website changes; it is not silently
replaced with a fabricated complete list.
"""
from __future__ import annotations
import csv
import html.parser
import io
import re
import sqlite3
import threading
import urllib.request
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo

US_URLS=(
 ("NASDAQ","https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"),
 ("OTHER","https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"),
)
KR_URL="https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
SEEDS=(
 ("US","VOO","Vanguard S&P 500 ETF","ETF"),("US","VTI","Vanguard Total Stock Market ETF","ETF"),
 ("US","QQQM","Invesco NASDAQ 100 ETF","ETF"),("US","SCHD","Schwab US Dividend Equity ETF","ETF"),
 ("US","VXUS","Vanguard Total International ETF","ETF"),("US","AAPL","Apple Inc.","STOCK"),
 ("US","MSFT","Microsoft Corp.","STOCK"),("KR","005930","삼성전자","STOCK"),
 ("KR","000660","SK하이닉스","STOCK"),("KR","069500","KODEX 200","ETF"),
 ("KR","360750","TIGER 미국S&P500","ETF"),
)
_LOCK=threading.Lock()

def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_security_master(
        market TEXT NOT NULL,symbol TEXT NOT NULL,name TEXT NOT NULL,
        kind TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(market,symbol))""")
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_security_refresh(
        market TEXT PRIMARY KEY, checked_at TEXT NOT NULL,
        status TEXT NOT NULL,rows INTEGER NOT NULL)""")

def _url(url,maxbytes):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 WealthyPersonalResearch/1.0"})
    with urllib.request.urlopen(req,timeout=9) as resp:
        data=resp.read(maxbytes+1)
    if len(data)>maxbytes: raise ValueError("oversized listing")
    return data

class _KoreanTable(html.parser.HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.row=None;self.cell=None;self.buffer=[]
    def handle_starttag(self,tag,attrs):
        if tag=="tr":self.row=[]
        if tag in ("td","th") and self.row is not None:self.cell=tag;self.buffer=[]
    def handle_data(self,data):
        if self.cell:self.buffer.append(data)
    def handle_endtag(self,tag):
        if tag==self.cell and self.row is not None:
            self.row.append(" ".join("".join(self.buffer).split()))
            self.cell=None
        if tag=="tr" and self.row is not None:
            if self.row:self.rows.append(self.row)
            self.row=None

def _us_rows():
    out={}
    for market,url in US_URLS:
        try:
            raw=_url(url,2_500_000).decode("utf-8-sig")
            lines=list(csv.DictReader(io.StringIO(raw),delimiter="|"))
            if len(lines)<500:continue
            for item in lines:
                symbol=(item.get("Symbol") or item.get("ACT Symbol") or "").strip().upper()
                name=(item.get("Security Name") or "").strip()
                if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}",symbol) or not name or len(name)>190:continue
                if (item.get("Test Issue") or "N")=="Y":continue
                kind="ETF" if (item.get("ETF") or "").strip()=="Y" else "STOCK"
                out[symbol]=("US",symbol,name,kind,market)
        except (OSError,ValueError,UnicodeError,csv.Error):
            continue
    return list(out.values())

def _kr_rows():
    try:
        raw=_url(KR_URL,5_000_000)
        # KIND returns an HTML table often tagged EUC-KR.
        table=_KoreanTable()
        decoded=None
        for encoding in ("euc-kr","utf-8"):
            try:decoded=raw.decode(encoding);break
            except UnicodeError:pass
        if decoded is None:return []
        table.feed(decoded)
        headings=table.rows[0] if table.rows else []
        code_col=next((i for i,h in enumerate(headings) if "종목코드" in h),None)
        name_col=next((i for i,h in enumerate(headings) if "회사명" in h),None)
        if code_col is None or name_col is None:return []
        out={}
        for row in table.rows[1:]:
            if max(code_col,name_col)>=len(row):continue
            symbol=re.sub(r"\D","",row[code_col]).zfill(6)
            name=row[name_col].strip()
            if re.fullmatch(r"\d{6}",symbol) and 0<len(name)<=190:
                out[symbol]=("KR",symbol,name,"STOCK","KRX KIND listed corporations")
        return list(out.values())
    except (OSError,ValueError,UnicodeError,html.parser.HTMLParseError):
        return []

def refresh(portfolios,*,force=False,now=None):
    now=now or datetime.now(timezone.utc)
    with _LOCK:
        with portfolios._db() as db:
            _schema(db)
            rows=db.execute("SELECT market,checked_at FROM stage1_security_refresh").fetchall()
        last={k:v for k,v in rows}
        updated={}
        for market,loader in (("US",_us_rows),("KR",_kr_rows)):
            if not force and market in last:
                try:
                    if now-datetime.fromisoformat(last[market])<timedelta(days=1):
                        continue
                except ValueError:pass
            items=loader()
            # Never delete previously verified names on a failed refresh.
            status="ok" if len(items)>500 else "unavailable"
            with portfolios._db() as db:
                db.execute("BEGIN IMMEDIATE")
                _schema(db)
                if status=="ok":
                    db.executemany("""INSERT INTO stage1_security_master VALUES(?,?,?,?,?,?)
                      ON CONFLICT(market,symbol) DO UPDATE SET
                      name=excluded.name,kind=excluded.kind,source=excluded.source,
                      updated_at=excluded.updated_at""",
                      [(*x,now.isoformat()) for x in items])
                db.execute("""INSERT INTO stage1_security_refresh VALUES(?,?,?,?)
                  ON CONFLICT(market) DO UPDATE SET checked_at=excluded.checked_at,
                  status=excluded.status,rows=excluded.rows""",
                  (market,now.isoformat(),status,len(items)))
                db.execute("COMMIT")
            updated[market]={"status":status,"count":len(items)}
        return updated

def search(portfolios,query="",market="ALL",kind="ALL",limit=50,offset=0):
    if market not in ("ALL","US","KR") or kind not in ("ALL","ETF","STOCK"):
        raise ValueError("invalid filter")
    if (not isinstance(query,str) or len(query)>100 or type(limit) is not int
        or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=100_000):
        raise ValueError("invalid search")
    with portfolios._db() as db:
        _schema(db)
        params=[]
        filters=[]
        if market!="ALL":filters.append("market=?");params.append(market)
        if kind!="ALL":filters.append("kind=?");params.append(kind)
        if query.strip():
            filters.append("(name LIKE ? ESCAPE '\\' OR symbol LIKE ? ESCAPE '\\')")
            q=query.strip().replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
            params.extend(["%"+q+"%","%"+q+"%"])
        where=(" WHERE "+" AND ".join(filters)) if filters else ""
        total=db.execute("SELECT COUNT(*) FROM stage1_security_master"+where,params).fetchone()[0]
        rows=db.execute("""SELECT market,symbol,name,kind,source,updated_at
            FROM stage1_security_master"""+where+
            " ORDER BY CASE WHEN symbol=? THEN 0 ELSE 1 END,name LIMIT ? OFFSET ?",
            params+[query.strip().upper(),limit,offset]).fetchall()
        states=db.execute("SELECT market,checked_at,status,rows FROM stage1_security_refresh").fetchall()
    cols=("market","symbol","name","kind","source","updated_at")
    return {"items":[dict(zip(cols,r)) for r in rows],"total":total,
            "catalog_status":{m:{"checked_at":d,"status":s,"download_rows":n} for m,d,s,n in states},
            "is_complete":False,
            "notice":"상장법인/미국 증권 디렉터리 범위입니다. 한국 ETF 전체·상장폐지/거래정지 상태까지 보증하지 않습니다."}

def ensure_seed(portfolios):
    now=datetime.now(timezone.utc).isoformat()
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE");_schema(db)
        db.executemany("""INSERT OR IGNORE INTO stage1_security_master VALUES(?,?,?,?,?,?)""",
                       [(*x,"bundled verified identifiers",now) for x in SEEDS])
        db.execute("COMMIT")
