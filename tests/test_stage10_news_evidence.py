from datetime import datetime,timezone,timedelta
import pytest
from wealthy_server.stage10_news_evidence import review
NOW=datetime(2026,10,18,tzinfo=timezone.utc)
def item(title='AAPL profit growth',url='https://news.google.com/a',at=NOW):
    return {'title':title,'url':url,'published_utc':at.isoformat()}
def test_positive_headline_not_order():
    x=review([item()],now=NOW,symbols=['AAPL'])
    assert x['items'][0]['signal']=='positive_keyword'
    assert x['items'][0]['symbols']==['AAPL']
    assert x['items'][0]['tradeable'] is False
def test_duplicate_and_stale_filter():
    x=review([item(),item(),item(at=NOW-timedelta(days=31))],now=NOW)
    assert len(x['items'])==1 and x['rejected']==1
def test_reject_non_https_and_credentials():
    x=review([item(url='http://bad.test/a'),item(url='https://u:p@bad.test/a')],now=NOW)
    assert x['status']=='unavailable' and x['rejected']==2
def test_boundary_symbol():
    x=review([item('CAAPL profit growth')],now=NOW,symbols=['AAPL'])
    assert x['items'][0]['symbols']==[]
def test_conflict_and_no_fake_impact():
    x=review([item('AAPL downgrade and profit growth')],now=NOW,symbols=['AAPL'])
    assert x['items'][0]['signal']=='mixed'
    assert x['items'][0]['price_impact_verified'] is False
@pytest.mark.parametrize('max_items',[0,-1,True,201])
def test_limits(max_items):
    with pytest.raises(ValueError):review([],now=NOW,max_items=max_items)
