from datetime import date
from pathlib import Path

from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_risk import paper_exit_review


def quote(symbol, price):
    return {'status':'ok','tradeable':True,'currency':'KRW' if symbol[0].isdigit() else 'USD',
        'symbol':symbol,'price':str(price),'as_of':'2026-10-16','source':'fixture'}


def test_profit_taking_sells_domestic_etf_once(tmp_path: Path):
    path=tmp_path/'paper.sqlite3'
    ledger,portfolios=Stage1Ledger(path),Portfolios(path)
    initialize_paper_accounts('test',ledger,portfolios,today=date(2026,10,17))
    portfolios.trade('test','isa-base','360750','BUY',1,'initial-buy-0001',quote('360750',10000))
    result=paper_exit_review('test',portfolios,today=date(2026,10,17),
        quote_provider=lambda s: quote(s,12000))
    assert result['events'][0]['status']=='executed'
    assert result['events'][0]['side']=='SELL'
    assert portfolios.list('test')['accounts'][2]['holdings']==[]
    assert paper_exit_review('test',portfolios,today=date(2026,10,17),
        quote_provider=lambda s: quote(s,12000))['events']==[]


def test_no_sell_without_verified_quote(tmp_path: Path):
    path=tmp_path/'paper.sqlite3'
    ledger,portfolios=Stage1Ledger(path),Portfolios(path)
    initialize_paper_accounts('test',ledger,portfolios,today=date(2026,10,17))
    portfolios.trade('test','kr-base','069500','BUY',1,'initial-buy-0001',quote('069500',10000))
    result=paper_exit_review('test',portfolios,today=date(2026,10,17),
        quote_provider=lambda s: {'status':'unavailable','tradeable':False,'symbol':s})
    assert result['events'][0]['status']=='skipped'
    assert len(portfolios.orders('test','kr-base')['orders'])==1
