"""Stage10 read-only news/owner-declared holdings behavior."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime
import pytest
from wealthy_server.stage10_manual_assets import adjust,adjust_balance,list_assets
from wealthy_server.stage1_hub import news_30d,_editorial_context

class FakeStore:
    def __init__(self,p):
        self.p=p
        with self._db() as db:
            db.execute("CREATE TABLE portfolios (owner TEXT,id TEXT)")
            db.execute("INSERT INTO portfolios VALUES('alice','default')")
    @contextmanager
    def _db(self):
        conn=sqlite3.connect(self.p,isolation_level=None)
        try:
            yield conn
        finally:
            conn.close()

def test_manual_stock_plus_minus_are_isolated(tmp_path):
    store=FakeStore(tmp_path/"a.sqlite3")
    assert adjust(store,"alice","default","AAPL","USD","2","ADD")["quantity"]=="2"
    assert adjust(store,"alice","default","AAPL","USD","1","REMOVE")["quantity"]=="1"
    assert list_assets(store,"alice")["items"][0]["quantity"]=="1"
    assert list_assets(store,"bob")["items"]==[]
    with pytest.raises(ValueError):
        adjust(store,"alice","default","AAPL","USD","2","REMOVE")
    assert list_assets(store,"alice")["items"][0]["quantity"]=="1"
    assert list_assets(store,"alice")["included_in_paper_nav"] is False

def test_manual_cash_assets_plus_minus(tmp_path):
    store=FakeStore(tmp_path/"b.sqlite3")
    assert adjust_balance(store,"alice","예금","KRW","50000","ADD")["amount"]=="50000"
    assert adjust_balance(store,"alice","예금","KRW","20000","REMOVE")["amount"]=="30000"
    with pytest.raises(ValueError):adjust_balance(store,"alice","예금","KRW","50000","REMOVE")
    assert list_assets(store,"alice")["balances"][0]["amount"]=="30000"
    assert list_assets(store,"bob")["balances"]==[]

def test_news_fixture_has_no_external_translation(monkeypatch):
    import wealthy_server.stage1_research as research
    def fail(*args):raise AssertionError("offline test must not request translator")
    monkeypatch.setattr(research,"translate_headline_redundant",fail)
    now=datetime(2026,10,18,tzinfo=timezone.utc)
    def source(query,market,language):
        return [{"title":"Apple shares rise","url":"https://news.google.com/rss/articles/test",
                 "published":format_datetime(now),"publisher":"연합뉴스"}]
    result=news_30d(now=now,fetcher=source)
    assert len(result["items"])==2 and result["status"]=="ok"

def test_editorial_labels_retain_uncertainty():
    label,source=_editorial_context("한겨레")
    assert "연구" in label and source and source.startswith("https://")
    assert _editorial_context("미분류")[1] is None

def test_korean_publisher_rss_fallback_when_google_fails(monkeypatch):
    import wealthy_server.stage1_hub as hub
    from urllib.request import Request
    from io import BytesIO
    class Response:
        def __init__(self,data):self.buf=BytesIO(data)
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def read(self,n):return self.buf.read(n)
    rss=b"""<?xml version="1.0" encoding="utf-8"?>
      <rss><channel><item><title>Economy</title>
      <link>https://www.mk.co.kr/news/stock/12345</link>
      <pubDate>Sun, 18 Oct 2026 00:00:00 GMT</pubDate>
      </item></channel></rss>"""
    def get(request,timeout):
        url=request.full_url
        if 'news.google.com' in url:raise OSError('google unavailable')
        if 'www.mk.co.kr/rss/' in url:return Response(rss)
        raise AssertionError('unnecessary fallback')
    monkeypatch.setattr(hub.urllib.request,'urlopen',get)
    items=hub._fetch_rss('stocks','KR','ko')
    assert len(items)==1 and items[0]['publisher']=='매일경제'
    assert items[0]['editorial_lean']=='평가 근거 미확인'
