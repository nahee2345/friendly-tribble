"""Offline acceptance of catalog, no-key news risk, indexed dashboard history."""
from datetime import datetime,date,timezone,timedelta
from email.utils import format_datetime
from pathlib import Path

from wealthy_server.stage1_catalog import ensure_seed,search,_KoreanTable
from wealthy_server.stage1_hub import review_news,asset_history,dashboard,news_30d
from wealthy_server.stage1_inbox import inbox
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_discovery import PreferenceStore

def setup(tmp_path):
    path=tmp_path/'paper.sqlite3'
    ledger=Stage1Ledger(path);portfolio=Portfolios(path)
    initialize_paper_accounts('o',ledger,portfolio,today=date(2026,10,17))
    return ledger,portfolio,PreferenceStore(path)

def quote(sym):
    return dict(status='ok',tradeable=True,currency='KRW',symbol=sym,price='10000',
                as_of='2026-10-16',source='offline fixture')

def test_search_seed_filters_and_escape(tmp_path):
    _,p,_=setup(tmp_path)
    ensure_seed(p)
    all_=search(p,'','ALL','ALL')
    assert all_['total']>=10 and all_['is_complete'] is False
    assert [x['symbol'] for x in search(p,'삼성','KR','STOCK')['items']]==['005930']
    assert search(p,'%', 'ALL','ALL')['total']==0
    assert search(p,'VOO','US','ETF')['items'][0]['symbol']=='VOO'

def test_kind_table_html_parse():
    parser=_KoreanTable()
    parser.feed('<table><tr><th>회사명</th><th>종목코드</th></tr><tr><td>테스트기업</td><td>123456</td></tr></table>')
    assert parser.rows[1]==['테스트기업','123456']

def test_news_last_thirty_days_filter():
    now=datetime(2026,10,17,tzinfo=timezone.utc)
    recent=format_datetime(now-timedelta(days=2))
    old=format_datetime(now-timedelta(days=40))
    def fetch(q,m,l):
        return [{'title':'S&P 500 drops','url':'https://news.google.com/one','published':recent},
                {'title':'Stale article','url':'https://news.google.com/old','published':old}]
    outcome=news_30d(now=now,fetcher=fetch)
    assert len(outcome['items'])==2
    assert all(x['title']!='Stale article' for x in outcome['items'])

def test_owner_risk_inbox_deduplicates_with_source(tmp_path):
    ledger,p,prefs=setup(tmp_path)
    p.trade('o','kr-base','069500','BUY',1,'initial-buy-001',quote('069500'))
    item={'title':'코스피 급락 이후 변동 확대','url':'https://news.google.com/one',
          'published_utc':'2026-10-17T00:00:00+00:00','market':'KR','source':'fixture'}
    news={'status':'ok','items':[item],'coverage':'fixture'}
    one=review_news('o',p,feed=news,notify=True)
    assert len(one['risk_suspected'])==1
    assert one['risk_suspected'][0]['symbols']==['069500']
    review_news('o',p,feed=news,notify=True)
    messages=[x for x in inbox('o',p)['items'] if x['event_id'].startswith('risk:')]
    assert len(messages)==1 and item['url'] in messages[0]['detail']
    assert inbox('other',p)['items']==[]

def test_snapshot_only_real_observation_dates(tmp_path):
    ledger,p,prefs=setup(tmp_path)
    perf={'complete':True,'portfolio_value_krw':'4000000','contributions_krw':4000000}
    snap=asset_history('o',p,ledger,performance=perf,
                       now=datetime(2026,10,17,17,tzinfo=timezone.utc))
    assert len(snap)==1
    assert snap[0]['value_krw']=='4000000'
    snap=asset_history('o',p,ledger,performance=perf,
                       now=datetime(2026,10,18,17,tzinfo=timezone.utc))
    assert len(snap)==2
