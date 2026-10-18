"""Stage10 verifiable headline evidence. Offline and read-only; never permits orders."""
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit
import re
NEGATIVE=('급락','하락','적자','소송','리콜','파산','downgrade','lawsuit','loss','bankruptcy','recall')
POSITIVE=('상승','흑자','증가','성장','beat estimates','upgrade','profit growth')
def review(items,*,now,symbols=(),max_items=100):
    if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('timezone-aware now required')
    if type(max_items) is not int or not 1<=max_items<=200:raise ValueError('invalid limit')
    if not isinstance(items,(list,tuple)) or len(items)>2000:raise ValueError('invalid items')
    if not isinstance(symbols,(list,tuple,set)) or len(symbols)>200:raise ValueError('invalid symbols')
    watch=tuple(s.upper() for s in symbols if isinstance(s,str) and re.fullmatch(r'[A-Za-z0-9.\-]{1,15}',s))
    now=now.astimezone(timezone.utc)
    accepted={};rejected=0
    for item in items:
        if not isinstance(item,dict):rejected+=1;continue
        title=item.get('title');url=item.get('url');published=item.get('published_utc')
        if not all(isinstance(x,str) for x in (title,url,published)) or not title.strip() or len(title)>500:
            rejected+=1;continue
        try:
            link=urlsplit(url)
            if link.scheme!='https' or not link.hostname or link.username or link.password or len(url)>2000:raise ValueError('unsafe link')
            ts=datetime.fromisoformat(published.replace('Z','+00:00'))
            if ts.tzinfo is None:raise ValueError('naive date')
            ts=ts.astimezone(timezone.utc)
            if ts<now-timedelta(days=30) or ts>now+timedelta(minutes=5):raise ValueError('outside period')
        except (ValueError,TypeError):rejected+=1;continue
        headline=title.casefold()
        negatives=[x for x in NEGATIVE if x in headline]
        positives=[x for x in POSITIVE if x in headline]
        matching=[s for s in watch if re.search(r'(?<![A-Za-z0-9])'+re.escape(s.casefold())+r'(?![A-Za-z0-9])',headline)]
        direction=('mixed' if negatives and positives else 'negative_keyword' if negatives else 'positive_keyword' if positives else 'unverified')
        row={'title':title.strip(),'url':url,'published_utc':ts.isoformat(),'symbols':matching,
             'signal':direction,'status':'headline_only','price_impact_verified':False,'tradeable':False}
        key=(url,tuple(matching))
        if key not in accepted or row['published_utc']>accepted[key]['published_utc']:accepted[key]=row
    rows=sorted(accepted.values(),key=lambda r:(r['published_utc'],r['url']),reverse=True)[:max_items]
    return {'status':'ok' if rows else 'unavailable','items':rows,'rejected':rejected,'unique_count':len(accepted),
            'coverage':'최근 30일 제공된 헤드라인 일부만 검토','conclusions_are_trading_signals':False}
