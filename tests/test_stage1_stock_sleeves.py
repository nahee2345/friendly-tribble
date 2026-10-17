from datetime import date

from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_discovery import PreferenceStore
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_us_stocks import paper_run_us_stocks
from wealthy_server.stage1_kr_stocks import paper_run_kr_stocks


def setup(tmp_path):
    path=tmp_path/"paper.sqlite3"
    ledger,portfolios,prefs=Stage1Ledger(path),Portfolios(path),PreferenceStore(path)
    initialize_paper_accounts("owner",ledger,portfolios,today=date(2026,10,17))
    return ledger,portfolios,prefs


def quote(symbol):
    return {"status":"ok","tradeable":True,"symbol":symbol,
            "price":"180" if symbol.isalpha() else "40000",
            "currency":"USD" if symbol.isalpha() else "KRW",
            "as_of":"2026-10-16","source":"fixture"}


def fx():
    return {"status":"ok","executable":False,"usd_krw":"1400","source":"fixture"}


def test_us_stock_purchase_uses_independent_key_and_same_cash(tmp_path):
    ledger,p,prefs=setup(tmp_path)
    options=dict(today=date(2026,10,17),quote_provider=quote,fx_provider=fx)
    first=paper_run_us_stocks("owner",ledger,p,prefs,**options)
    assert first["status"]=="executed"
    assert first["symbol"] in ("MSFT","AAPL")
    second=paper_run_us_stocks("owner",ledger,p,prefs,**options)
    assert second["status"]=="already_executed"
    assert len(p.orders("owner","default")["orders"])==1


def test_kr_stock_purchase_not_isa(tmp_path):
    ledger,p,prefs=setup(tmp_path)
    first=paper_run_kr_stocks("owner",p,prefs,today=date(2026,10,17),
                               quote_provider=quote)
    assert first["status"]=="executed"
    assert first["account"]=="kr-base"
    assert not p.orders("owner","isa-base")["orders"]
    assert paper_run_kr_stocks("owner",p,prefs,today=date(2026,10,17),
                               quote_provider=quote)["status"]=="already_executed"


def test_zero_stock_target_does_not_fetch_market(tmp_path):
    ledger,p,prefs=setup(tmp_path)
    prefs.save("owner",90,100)
    def no_network(*args):
        raise AssertionError("no market download allowed")
    assert paper_run_us_stocks("owner",ledger,p,prefs,quote_provider=no_network)["status"]=="skipped"
    assert paper_run_kr_stocks("owner",p,prefs,quote_provider=no_network)["status"]=="skipped"


def test_invalid_prices_fail_closed(tmp_path):
    ledger,p,prefs=setup(tmp_path)
    bad=lambda symbol:{"status":"unavailable","tradeable":False,"symbol":symbol}
    assert paper_run_us_stocks("owner",ledger,p,prefs,quote_provider=bad,fx_provider=fx)["status"]=="skipped"
    assert paper_run_kr_stocks("owner",p,prefs,quote_provider=bad)["status"]=="skipped"
    assert p.orders("owner","default")["orders"]==[]
    assert p.orders("owner","kr-base")["orders"]==[]
