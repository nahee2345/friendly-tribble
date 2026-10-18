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
