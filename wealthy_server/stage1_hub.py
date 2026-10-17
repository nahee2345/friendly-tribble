"""No-key market dashboard and grounded 30-day news monitoring.

Market and portfolio data are reference closes, not real-time quotes.
Missing evidence always stays missing. Risk notifications are headline-
matching *review requests*, not assertions of causality or trading orders.
"""
from __future__ import annotations
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from email.utils import parsedate_to_datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .stage1_valuation import portfolio_report
from .stage1_news_impact import TICKER_TERMS,NEGATIVE_WORDS,POSITIVE_WORDS
from .stage1_inbox import _schema as inbox_schema
from .stage1_decisions import proposal
from .stage1_strategy_policy import StrategyPolicy

INDEXES=(("KOSPI","^KS11"),("KOSDAQ","^KQ11"),
         ("S&P 500","^GSPC"),("NASDAQ","^IXIC"))

def _index(item):
    label,symbol=item
    url="https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol,safe="")+"?range=8d&interval=1d"
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 WealthyPersonalResearch"})
        with urllib.request.urlopen(req,timeout=5) as response:
            raw=response.read(90_001)
        if len(raw)>90_000:raise ValueError
        result=json.loads(raw)["chart"]["result"][0]
        timestamps=result["timestamp"]
        prices=result["indicators"]["quote"][0]["close"]
        valid=[(ts,Decimal(str(p))) for ts,p in zip(timestamps,prices) if p is not None]
        valid=[(ts,p) for ts,p in valid if p.is_finite() and p>0]
        if len(valid)<2:raise ValueError
        stamp=datetime.fromtimestamp(valid[-1][0],timezone.utc).date()
        age=(datetime.now(timezone.utc).date()-stamp).days
        if not 0<=age<=6:raise ValueError
        change=(valid[-1][1]/valid[-2][1]-1)*100
        return {"name":label,"status":"ok","value":str(valid[-1][1].quantize(Decimal(".01"))),
                "change_pct":round(float(change),2),"as_of":str(stamp),
                "source":"Yahoo Finance unofficial delayed chart"}
    except (OSError,ValueError,KeyError,TypeError,IndexError,TimeoutError):
        return {"name":label,"status":"unavailable","value":None,"change_pct":None,
                "as_of":None,"source":"public index feed unavailable"}

def market_indices():
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(_index,INDEXES))

def news_30d(*,now=None,fetcher=None,limit=30):
    now=now or datetime.now(timezone.utc)
    fetcher=fetcher or _fetch_rss
    summaries=[]
    missing=[]
    for market,query,language in (
        ("KR","주식 OR 코스피 OR 삼성전자 OR SK하이닉스 when:30d","ko"),
        ("US","US stocks OR S&P500 OR NASDAQ OR Apple OR Microsoft when:30d","en")):
        try:
            items=fetcher(query,market,language)
            for item in items:
                published=parsedate_to_datetime(item["published"]).astimezone(timezone.utc)
                if not now-timedelta(days=30)<=published<=now+timedelta(minutes=5):
                    continue
                item={**item,"market":market,"published_utc":published.isoformat()}
                summaries.append(item)
        except (KeyError,TypeError,ValueError,OSError,ET.ParseError):
            missing.append(market)
    dedup={}
    for item in summaries:
        dedup[(item["market"],item["title"].casefold())]=item
    items=sorted(dedup.values(),key=lambda x:x["published_utc"],reverse=True)[:limit]
    return {"items":items,"status":"partial" if missing else "ok","unavailable":missing,
            "coverage":"공개 Google News RSS 검색의 최근 30일 결과 일부. 모든 기사·원문 분석 보장 안 함"}

def _fetch_rss(query,market,language):
    params={"q":query,"hl":"ko","gl":"KR","ceid":"KR:ko"} if language=="ko" else {
        "q":query,"hl":"en-US","gl":"US","ceid":"US:en"}
    url="https://news.google.com/rss/search?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 WealthyPersonalResearch"})
    with urllib.request.urlopen(req,timeout=6) as response:
        raw=response.read(230_001)
    if len(raw)>230_000:raise ValueError("oversized")
    root=ET.fromstring(raw)
    items=[]
    for row in root.findall("./channel/item")[:90]:
        title=(row.findtext("title") or "").strip()[:250]
        link=(row.findtext("link") or "").strip()
        pub=(row.findtext("pubDate") or "").strip()
        if title and link.startswith("https://news.google.com/") and pub:
            items.append({"title":title,"url":link,"published":pub,"source":"Google News RSS"})
    return items

def _classify(item,holdings):
    title=item["title"].casefold()
    matches=[]
    for symbol in holdings:
        if symbol.lower() in title or any(term in title for term in TICKER_TERMS.get(symbol,())):
            matches.append(symbol)
    down=any(word in title for word in NEGATIVE_WORDS)
    up=any(word in title for word in POSITIVE_WORDS)
    direction=("하방 위험 의심" if down and not up else
               "상방 요인 의심" if up and not down else "영향 미확인")
    return {**item,"symbols":sorted(set(matches)),"impact":direction,
            "reason":"기사 제목의 기업·시장 키워드를 보유종목과 비교했습니다. 기사 본문 및 실제 가격 영향은 검증되지 않았습니다.",
            "confidence":"제목만 분석 · 추가 검증 필요"}

def review_news(owner,portfolios,*,feed=None,now=None,notify=True):
    feed=feed if feed is not None else news_30d(now=now)
    positions=[h["symbol"] for a in portfolios.list(owner)["accounts"] for h in a["holdings"]]
    mapped=[_classify(item,positions) for item in feed["items"]]
    related=[x for x in mapped if x["symbols"]]
    risks=[x for x in related if x["impact"]=="하방 위험 의심"]
    if notify and risks:
        stamp=(now or datetime.now(timezone.utc)).isoformat()
        with portfolios._db() as db:
            db.execute("BEGIN IMMEDIATE");inbox_schema(db)
            for item in risks[:30]:
                # Stable owner-level fingerprint on source URL & symbols; no repeat alarm.
                import hashlib
                eid="risk:"+hashlib.sha256((item["url"]+"|"+",".join(item["symbols"])).encode()).hexdigest()[:32]
                title="보유종목 관련 부정 뉴스 검토: "+",".join(item["symbols"])
                detail=(item["title"]+" / 원문: "+item["url"]+" / "+item["reason"])[:1800]
                db.execute("""INSERT OR IGNORE INTO stage1_inbox
                  (owner,event_id,title,detail,created_at,read_at)
                  VALUES(?,?,?,?,?,NULL)""",(owner,eid,title,detail,stamp))
            db.execute("COMMIT")
    return {"status":feed["status"],"summary":{"checked":len(mapped),
             "related":len(related),"risk_suspected":len(risks),
             "not_verified":"제목 기반 의심. 뉴스가 실제 손실을 발생시킨다는 결론 아님"},
            "news":mapped[:30],"related":related[:15],
            "risk_suspected":risks[:10],"coverage":feed.get("coverage","")}

def _snapshot_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_portfolio_snapshots(
        owner TEXT NOT NULL,day TEXT NOT NULL, total_krw TEXT,
        contributions_krw INTEGER NOT NULL,complete INTEGER NOT NULL,
        captured_kst TEXT NOT NULL,PRIMARY KEY(owner,day))""")

def asset_history(owner,portfolios,ledger,*,performance=None,now=None):
    now=now or datetime.now(ZoneInfo("Asia/Seoul"))
    performance=performance if performance is not None else portfolio_report(owner,portfolios,ledger)
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE");_snapshot_schema(db)
        db.execute("""INSERT INTO stage1_portfolio_snapshots VALUES(?,?,?,?,?,?)
          ON CONFLICT(owner,day) DO UPDATE SET total_krw=excluded.total_krw,
          contributions_krw=excluded.contributions_krw,complete=excluded.complete,
          captured_kst=excluded.captured_kst""",
          (owner,now.date().isoformat(),performance["portfolio_value_krw"],
           performance["contributions_krw"],int(performance["complete"]),now.isoformat()))
        rows=db.execute("""SELECT day,total_krw,contributions_krw,complete
             FROM stage1_portfolio_snapshots WHERE owner=?
             ORDER BY day DESC LIMIT 31""",(owner,)).fetchall()
        db.execute("COMMIT")
    return [{"date":d,"value_krw":v if ok else None,"contributed_krw":n,
             "complete":bool(ok)} for d,v,n,ok in reversed(rows)]

def dashboard(owner,portfolios,ledger,prefs,*,market_provider=market_indices,
              report_provider=None,decision_provider=None):
    # Separate failures by panel, never hide a market data problem behind a
    # fabricated amount. Slow providers should be cached by the caller.
    p=portfolio_report(owner,portfolios,ledger) if report_provider is None else report_provider()
    history=asset_history(owner,portfolios,ledger,performance=p)
    decision=(decision_provider() if decision_provider else
              proposal(owner,portfolios,prefs,StrategyPolicy(portfolios)))
    accounts=portfolios.list(owner)["accounts"]
    return {"markets":market_provider(),"performance":p,"history":history,
            "accounts":[{"id":a["id"],"name":a["name"],"krw_cash":a["krw_cash"],
               "usd_cash":a["usd_cash"],"holdings":len(a["holdings"])} for a in accounts],
            "recommendations":decision["recommendations"][:8],
            "decision_constraints":decision["coverage_limitations"],
            "notice":"시장 데이터는 지연 종가입니다. 일별 자산 차트는 실제 기록한 날짜부터 누적되며 과거값을 생성하지 않습니다."}
