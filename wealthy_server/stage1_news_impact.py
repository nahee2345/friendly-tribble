"""Title-only news exposure mapping for user-owned simulated positions.

Do not fabricate positive/negative effects from headlines. Show relevant
symbols and uncertainty ahead of the linked source. External articles are not
downloaded or interpreted as verified fundamental changes.
"""
from __future__ import annotations

import re
from .stage1_research import news_headlines

TICKER_TERMS = {
    "VOO": ("s&p 500","s&p500","미국 증시","us stocks"),
    "VTI": ("us stocks","미국 증시","wall street"),
    "QQQM": ("nasdaq","나스닥","technology stocks","tech stocks"),
    "SCHD": ("dividend","배당"),
    "VXUS": ("global markets","글로벌 증시","world equities"),
    "AAPL": ("apple","iphone","애플"),
    "MSFT": ("microsoft","azure","마이크로소프트"),
    "069500": ("kospi","코스피","korean stocks","한국 증시"),
    "360750": ("s&p 500","s&p500","미국 증시","us stocks"),
}
NEGATIVE_WORDS = ("fell","drops","slump","downturn","decline","급락","하락","감소","침체")
POSITIVE_WORDS = ("rises","rally","surges","record high","상승","급등","증가","최고치")


def title_impact(headlines, holdings):
    owned = set(holdings)
    results=[]
    for headline in headlines:
        title=(headline.get("title_original") or "").casefold()
        if not title: continue
        related=[s for s in owned if s in title.upper() or any(k in title for k in TICKER_TERMS.get(s,()))]
        if not related: continue
        has_negative=any(k in title for k in NEGATIVE_WORDS)
        has_positive=any(k in title for k in POSITIVE_WORDS)
        direction = ("하방 위험 신호 검토" if has_negative and not has_positive else
                     "상방 요인 검토" if has_positive and not has_negative else
                     "영향 방향 미확인")
        results.append({
            "outcome": direction, "symbols":sorted(related), "confidence":"제목 키워드 단순 매칭",
            "reason":"제목의 시장·기업 언급을 보유종목 노출과 비교했습니다. 실제 실적·주가 영향은 확인하지 않았습니다.",
            "title":headline.get("title_ko") or headline.get("title_original"),
            "original":headline.get("title_original"),"url":headline.get("url"),
            "published":headline.get("published"),"source_status":headline.get("translation_status"),
            "action":"확인 필요 / 이 기사만으로 자동 주문하지 않음",
        })
    return results


def portfolio_news_impacts(owner, portfolios, *, provider=news_headlines):
    held=[h["symbol"] for account in portfolios.list(owner)["accounts"] for h in account["holdings"]]
    reports=[]
    unavailable=[]
    for market in ("KR","US"):
        data=provider(market)
        if data.get("status")!="ok":
            unavailable.append(market)
            continue
        reports.extend(title_impact(data.get("items",[]),held))
    return {"status":"partial" if unavailable else "ok",
            "summary":{"portfolio_holdings":len(held),
                       "headline_matches":len(reports),
                       "unavailable_feeds":unavailable,
                       "portfolio_action":"뉴스 제목만으로 거래하지 않음",
                       "interpretation":"관련성 탐지이며 호재·악재 확정 판정이나 인과관계 추론이 아닙니다."},
            "impacts":reports[:24],"live_orders":False}
