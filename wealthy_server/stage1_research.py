"""Read-only sources for paper-stage research. Fail closed on unavailable information."""
from __future__ import annotations

import json
import html
import functools
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

_FEEDS = {
    'US': 'https://news.google.com/rss/search?q=US+stock+market+when%3A1d&hl=en-US&gl=US&ceid=US:en',
    'KR': 'https://news.google.com/rss/search?q=%EC%A3%BC%EC%8B%9D+%EC%A6%9D%EC%8B%9C+when%3A1d&hl=ko&gl=KR&ceid=KR:ko',
}


@functools.lru_cache(maxsize=256)
def _translate_headline(title: str) -> str | None:
    """Best-effort third-party translation. Never label original as translated."""
    if not title or len(title)>400:
        return None
    params=urllib.parse.urlencode({'q':title,'langpair':'en|ko'})
    url='https://api.mymemory.translated.net/get?'+params
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'WealthyResearch/1.0'})
        with urllib.request.urlopen(request,timeout=3) as response:
            raw=response.read(12001)
        if len(raw)>12000: return None
        data=json.loads(raw)
        if int(data.get('responseStatus',0))!=200: return None
        value=html.unescape(str(data['responseData']['translatedText'])).strip()
        if not value or value.casefold()==title.casefold() or not any('가'<=c<='힣' for c in value):return None
        return value[:400]
    except (ValueError,KeyError,TypeError,OSError,TimeoutError,json.JSONDecodeError):
        return None

def _translate_google_public(title: str) -> str | None:
    """Best-effort backup for public headlines; no claim of SLA or accuracy.

    Google's undocumented public endpoint can change, be rate limited or block
    requests. No private user text is sent. Never label an unchanged English
    headline as a completed Korean translation.
    """
    if not title or len(title)>400:
        return None
    params=urllib.parse.urlencode(
        {'client':'gtx','sl':'en','tl':'ko','dt':'t','q':title})
    url='https://translate.googleapis.com/translate_a/single?'+params
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req,timeout=4) as response:
            raw=response.read(16_001)
        if len(raw)>16_000:return None
        data=json.loads(raw)
        translated=''.join(piece[0] for piece in data[0] if isinstance(piece,list)
                            and piece and isinstance(piece[0],str)).strip()
        if not translated or not any('가'<=char<='힣' for char in translated):
            return None
        return html.unescape(translated)[:400]
    except (OSError,ValueError,KeyError,TypeError,IndexError,TimeoutError,
            json.JSONDecodeError):
        return None


def translate_headline_redundant(title: str) -> str | None:
    return _translate_headline(title) or _translate_google_public(title)


def news_headlines(region: str) -> dict:
    """Original headlines and links; translation needs a verified translator."""
    if region not in _FEEDS:
        raise ValueError('unsupported region')
    request=urllib.request.Request(_FEEDS[region],headers={'User-Agent':'WealthyResearch/1.0 (personal feed reader)'})
    try:
        with urllib.request.urlopen(request,timeout=6) as response:
            raw=response.read(256_001)
        if len(raw)>256_000: raise ValueError('too large')
        root=ET.fromstring(raw)
        items=[]
        for entry in root.findall('./channel/item')[:12]:
            title=(entry.findtext('title') or '').strip()[:200]
            link=(entry.findtext('link') or '').strip()
            if not title or not link.startswith('https://news.google.com/'):
                continue
            items.append({'title_original':title,'title_ko':title if region=='KR' else None,'translation_status':'original_ko' if region=='KR' else 'translator_not_configured','url':link,'published':(entry.findtext('pubDate') or '')[:60]})
        if region=='US' and items:
            with ThreadPoolExecutor(max_workers=4) as pool:
                translations=list(pool.map(translate_headline_redundant,[i['title_original'] for i in items]))
            for item,translation in zip(items,translations):
                if translation:
                    item['title_ko']=translation
                    item['translation_status']='translated_external'
        return {'region':region,'status':'ok','items':items,'fetched_utc':datetime.now(UTC).isoformat(),'investment_signal':False}
    except (OSError,ValueError,ET.ParseError,TimeoutError):
        return {'region':region,'status':'unavailable','items':[],'investment_signal':False}


def current_fx() -> dict:
    # ECB-derived/reference rate. This is not an executable brokerage exchange quote.
    url='https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW'
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'WealthyResearch/1.0'}),timeout=6) as response:
            raw=response.read(16_001)
        if len(raw)>16_000: raise ValueError('too large')
        data=json.loads(raw)
        rate=Decimal(str(data['rates']['KRW']))
        quote_date=datetime.strptime(data['date'],'%Y-%m-%d').date()
        age=(datetime.now(UTC).date()-quote_date).days
        if not rate.is_finite() or rate<=0 or not 0<=age<=5: raise ValueError('stale')
        return {'status':'ok','usd_krw':format(rate,'f'),'as_of':str(quote_date),'age_days':age,'executable':False,'source':'Frankfurter / reference market rate'}
    except (KeyError, TypeError, OSError, ValueError, json.JSONDecodeError, InvalidOperation):
        return {'status':'unavailable','executable':False}


def account_eligibility(instrument: str, market: str) -> dict:
    if instrument == 'US_LISTED_EQUITY' and market == 'US':
        return {'eligible':['GENERAL_OVERSEAS'], 'ineligible':['KOREAN_ISA_DIRECT'], 'message':'미국 거래소 상장주식은 국내 중개형 ISA에서 직접 매수할 수 없습니다. 실제 세후 비교는 별도 검증이 필요합니다.'}
    if instrument == 'KR_LISTED_ETF' and market == 'KR':
        return {'eligible':['GENERAL_DOMESTIC','ISA_CONDITIONAL'], 'ineligible':[], 'message':'국내 상장 ETF는 계좌 및 상품 세제 조건을 추가 확인해야 합니다.'}
    return {'eligible':[], 'ineligible':[], 'message':'거래 상품 유형과 계좌 적격성이 확인되지 않아 배정하지 않습니다.'}
