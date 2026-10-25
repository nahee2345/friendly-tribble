"""QA-001/QA-003 regression: isolated SQLite, no network, no broker."""
import sqlite3
from contextlib import contextmanager

import pytest

from wealthy_server.stage10_manual_assets import adjust, adjust_balance, list_assets
from wealthy_server.stage1_hub import dashboard


class Store:
    def __init__(self, path):
        self.path=path
        with self._db() as db:
            db.execute("CREATE TABLE portfolios (owner TEXT, id TEXT)")
            db.execute("INSERT INTO portfolios VALUES('alice', 'default')")

    @contextmanager
    def _db(self):
        c=sqlite3.connect(self.path,isolation_level=None)
        try:
            yield c
        finally:
            c.close()


def test_unknown_acquisition_never_replaced_by_zero_or_current_price(tmp_path):
    store=Store(tmp_path/"manual.sqlite3")
    adjust(store,"alice","default","AAPL","USD",2,"BUY")
    row=list_assets(store,"alice")["items"][0]
    assert row["cost_basis_status"]=="unavailable"
    assert row["average_cost_native"] is None
    assert row["total_cost_native"] is None
    assert row["unrealized_pnl_native"] is None
    assert row["scope"]=="USER_DECLARED_REAL"
    assert list_assets(store,"alice")["verified_real_asset_total_krw"] is None
    assert list_assets(store,"bob")["items"] == []


def test_moving_average_partial_sale_and_immutable_event_history(tmp_path):
    store=Store(tmp_path/"manual.sqlite3")
    adjust(store,"alice","default","AAPL","USD",10,"BUY",unit_price="100",
           request_id="event-first-001")
    adjust(store,"alice","default","AAPL","USD",10,"BUY",unit_price="120",
           request_id="event-second-001")
    adjust(store,"alice","default","AAPL","USD",5,"SELL",unit_price="130",
           request_id="event-third-001")
    row=list_assets(store,"alice")["items"][0]
    assert row["quantity"]=="15"
    assert row["cost_basis_status"]=="known"
    assert row["average_cost_native"]=="110"
    assert row["total_cost_native"]=="1650"
    assert row["realized_pnl_native"]=="100"
    assert row["unrealized_pnl_native"] is None
    assert len(list_assets(store,"alice")["history"])==3
    retry=adjust(store,"alice","default","AAPL","USD",5,"SELL",
                 unit_price="130",request_id="event-third-001")
    assert retry["status"]=="already_recorded"
    assert list_assets(store,"alice")["items"][0]["quantity"]=="15"
    with pytest.raises(ValueError,match="idempotency"):
        adjust(store,"alice","default","AAPL","USD",4,"SELL",
               unit_price="130",request_id="event-third-001")


def test_buy_sell_transfer_and_corrections_are_not_interchangeable(tmp_path):
    store=Store(tmp_path/"manual.sqlite3")
    adjust(store,"alice","default","AAPL","USD",3,"IN",unit_price="150")
    adjust(store,"alice","default","AAPL","USD",1,"OUT")
    adjust(store,"alice","default","AAPL","USD",1,"CORRECT_ADD")
    row=list_assets(store,"alice")["items"][0]
    assert row["quantity"]=="3"
    assert row["cost_basis_status"]=="unavailable"
    assert row["realized_pnl_native"]=="0"
    assert [x["side"] for x in list_assets(store,"alice")["history"]]==[
        "CORRECT_ADD","OUT","IN"
    ]
    assert list_assets(store,"alice")["included_in_paper_nav"] is False
    assert list_assets(store,"alice")["included_in_sample_nav"] is False


def test_cross_currency_duplicate_symbol_is_blocked_without_mutation(tmp_path):
    store=Store(tmp_path/"manual.sqlite3")
    adjust(store,"alice","default","AAPL","USD",2,"BUY",unit_price="100")
    with pytest.raises(ValueError,match="duplicate"):
        adjust(store,"alice","default","AAPL","KRW",1,"BUY",unit_price="200")
    row=list_assets(store,"alice")["items"]
    assert len(row)==1 and row[0]["currency"]=="USD"


def test_cash_changes_are_owner_isolated_idempotent_and_preserve_history(tmp_path):
    store=Store(tmp_path/"manual.sqlite3")
    kwargs=dict(request_id="cash-event-001")
    assert adjust_balance(store,"alice","예금","KRW",50000,"DEPOSIT",**kwargs)["amount"]=="50000"
    assert adjust_balance(store,"alice","예금","KRW",50000,"DEPOSIT",**kwargs)["status"]=="already_recorded"
    assert adjust_balance(store,"alice","예금","KRW",20000,"WITHDRAW",request_id="cash-event-002")["amount"]=="30000"
    assert list_assets(store,"bob")["balances"]==[]
    with store._db() as db:
        assert db.execute("SELECT COUNT(*) FROM stage10_manual_balance_history").fetchone()[0]==2


def test_qa003_dashboard_paper_scope_excludes_manual_and_sample(tmp_path,monkeypatch):
    import wealthy_server.stage1_hub as hub
    class Accounts:
        def list(self,owner):
            assert owner=="alice"
            return {"accounts":[{"id":"default","name":"PAPER_US","krw_cash":"4000000",
                     "usd_cash":"0","holdings":[]}]}
    monkeypatch.setattr(hub,"asset_history",lambda *args,**kwargs:[])
    p={"complete":True,"portfolio_value_krw":"4000000",
       "contributions_krw":4000000,"return_krw":"0"}
    x=dashboard("alice",Accounts(),object(),object(),report_provider=lambda:p,
                decision_provider=lambda:{"recommendations":[],"coverage_limitations":[]},
                market_provider=lambda:[])
    assert x["asset_scope"]["selected"]=="PAPER"
    assert x["asset_scope"]["included_accounts"]==["default"]
    assert x["asset_scope"]["excluded_scopes"]==["USER_DECLARED_REAL","SAMPLE"]
    assert x["accounts"][0]["scope"]=="PAPER"
    assert x["performance"]["portfolio_value_krw"]=="4000000"

def test_concurrent_retries_record_one_manual_event(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store=Store(tmp_path/"simultaneous.sqlite3")
    def perform(_):
        return adjust(store,"alice","default","MSFT","USD",1,"BUY",
                      unit_price="200",request_id="manual-concurrent-01")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(perform,range(4)))
    assert len([r for r in rows if r.get("status")=="already_recorded"])==3
    current=list_assets(store,"alice")["items"][0]
    assert current["quantity"]=="1"
    assert current["total_cost_native"]=="200"
    assert len(list_assets(store,"alice")["history"])==1


def test_manual_events_cannot_modify_paper_order_table(tmp_path):
    store=Store(tmp_path/"paper-still-untouched.sqlite3")
    with store._db() as db:
        db.execute("CREATE TABLE virtual_orders (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO virtual_orders VALUES ('untouched-paper')")
    adjust(store,"alice","default","AAPL","USD",1,"BUY",unit_price="100")
    adjust_balance(store,"alice","예금","KRW",10000,"DEPOSIT")
    with store._db() as db:
        assert db.execute("SELECT id FROM virtual_orders").fetchall()==[("untouched-paper",)]
