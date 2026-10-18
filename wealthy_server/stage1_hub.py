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

# Historical scholarly classifications, NOT a claim that every article shares a viewpoint.
_EDITORIAL_STUDY="https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART001533645"
_EDITORIAL_STUDY_2="https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART001372728"
_EDITORIAL_OUTLETS=(
    (("조선일보","chosun ilbo"),"보수 성향으로 분류된 연구 있음",_EDITORIAL_STUDY),
    (("동아일보","donga ilbo","dong-a ilbo"),"보수 성향으로 분류된 연구 있음",_EDITORIAL_STUDY),
    (("중앙일보","joongang ilbo"),"보수 성향으로 분류된 연구 있음",_EDITORIAL_STUDY_2),
    (("한겨레","hankyoreh"),"진보 성향으로 분류된 연구 있음",_EDITORIAL_STUDY),
    (("경향신문","kyunghyang"),"진보 성향으로 분류된 연구 있음",_EDITORIAL_STUDY),
)
def _editorial_context(publisher):
    key=publisher.strip().casefold()
    for names,label,source in _EDITORIAL_OUTLETS:
        if key in names:
            return label,source
    return "평가 근거 미확인",None

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
    live_fetcher=fetcher is None
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
    if items:
        from .stage1_research import translate_headline_redundant
        english=[i for i in items if i["market"]=="US"][:12] if live_fetcher else []
        if english:
            with ThreadPoolExecutor(max_workers=4) as pool:
                translations=list(pool.map(translate_headline_redundant,[i["title"] for i in english]))
            for item,translated in zip(english,translations):
                item["title_ko"]=translated
                item["translation_status"]="translated_external" if translated else "translation_unavailable"
    return {"items":items,"status":"partial" if missing and items else "unavailable" if not items else "ok","unavailable":missing,
            "coverage":"공개 Google News RSS 검색의 최근 30일 결과 일부. 모든 기사·원문 분석 보장 안 함"}

def _fetch_rss(query,market,language):
    params={"q":query,"hl":"ko","gl":"KR","ceid":"KR:ko"} if language=="ko" else {
        "q":query,"hl":"en-US","gl":"US","ceid":"US:en"}
    base="https://news.google.com/rss/search?"
    candidates=[base+urllib.parse.urlencode(params)]
    # Query syntax may return no items; retry a simpler topic before giving up.
    simpler=dict(params);simpler["q"]="주식 코스피" if language=="ko" else "stocks markets"
    candidates.append(base+urllib.parse.urlencode(simpler))
    # Independent public publisher feed if Google News is unavailable for US.
    if market=="US":
        candidates.append("https://feeds.bbci.co.uk/news/business/rss.xml")
    else:
        candidates.extend(("https://www.mk.co.kr/rss/50200011/",
                           "https://www.hankyung.com/feed/finance",
                           "https://www.yna.co.kr/rss/economy.xml"))
    root=None
    for url in candidates:
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 WealthyPersonalResearch"})
            with urllib.request.urlopen(req,timeout=6) as response:
                raw=response.read(230_001)
            if len(raw)>230_000:raise ValueError("oversized")
            parsed=ET.fromstring(raw)
            if parsed.findall("./channel/item"):
                root=parsed
                break
        except (OSError,ValueError,ET.ParseError):
            continue
    if root is None:
        raise OSError("Google News RSS unavailable or zero items in both queries")
    items=[]
    for row in root.findall("./channel/item")[:90]:
        title=(row.findtext("title") or "").strip()[:250]
        link=(row.findtext("link") or "").strip()
        pub=(row.findtext("pubDate") or "").strip()
        allowed_prefixes=("https://news.google.com/","https://www.bbc.com/","https://www.bbc.co.uk/",
                          "https://www.mk.co.kr/","https://www.hankyung.com/","https://www.yna.co.kr/")
        if title and any(link.startswith(prefix) for prefix in allowed_prefixes) and pub:
            publisher=(row.findtext("source") or "").strip()[:100]
            if not publisher:
                publisher=("BBC News" if "bbc." in link else
                           "매일경제" if "mk.co.kr" in link else
                           "한국경제" if "hankyung.com" in link else
                           "연합뉴스" if "yna.co.kr" in link else "언론사 미확인")
            editorial,source_url=_editorial_context(publisher) if market=="KR" else ("해당 없음",None)
            items.append({"title":title,"url":link,"published":pub,"source":"Google News RSS",
                          "publisher":publisher,"editorial_lean":editorial,
                          "editorial_source":source_url,
                          "editorial_basis":"과거 연구의 매체별 분류로, 현재 해당 기사나 언론사의 고정된 정치색을 증명하지 않습니다."})
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
    """Keep owner cash visible when market/news/recommendation services fail.

    Do not swallow errors reading the actual account ledger or invent valuations.
    """
    accounts=portfolios.list(owner)["accounts"]
    errors=[]
    try:
        p=portfolio_report(owner,portfolios,ledger) if report_provider is None else report_provider()
        history=asset_history(owner,portfolios,ledger,performance=p)
    except (OSError,ValueError,KeyError,TypeError,ArithmeticError):
        errors.append("valuation_unavailable")
        p={"complete":False,"portfolio_value_krw":None,"contributions_krw":None,
           "return_krw":None,"missing":[]}
        history=[]
    try:
        decision=(decision_provider() if decision_provider else
                  proposal(owner,portfolios,prefs,StrategyPolicy(portfolios)))
    except (OSError,ValueError,KeyError,TypeError,ArithmeticError):
        errors.append("decision_unavailable")
        decision={"recommendations":[],"coverage_limitations":["투자 판단에 필요한 자료를 확인하지 못했습니다."]}
    try:
        markets=market_provider()
    except (OSError,ValueError,KeyError,TypeError,ArithmeticError):
        errors.append("markets_unavailable")
        markets=[{"name":name,"status":"unavailable","value":None,
                  "change_pct":None,"as_of":None,"source":"source unavailable"}
                 for name,_ in INDEXES]
    return {"markets":markets,"performance":p,"history":history,
            "accounts":[{"id":a["id"],"name":a["name"],"krw_cash":a["krw_cash"],
               "usd_cash":a["usd_cash"],"holdings":len(a["holdings"])} for a in accounts],
            "recommendations":decision["recommendations"][:8],
            "decision_constraints":decision["coverage_limitations"],
            "partial_errors":errors,
            "notice":"검증된 자료만 표시합니다. 외부 시장자료 오류는 가상계좌 현금 조회를 차단하지 않습니다."}
