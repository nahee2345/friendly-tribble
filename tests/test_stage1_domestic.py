from datetime import date
from pathlib import Path

import pytest
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_domestic import paper_run_domestic
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios


def quote(symbol):
    return {'status':'ok','tradeable':True,'symbol':symbol,'currency':'KRW',
            'price':'10000','source':'test fixture daily close','as_of':'2026-10-16'}


def test_kr_and_isa_paper_buy_once(tmp_path: Path):
    p=tmp_path/'stage1-paper.sqlite3'
    l,a=Stage1Ledger(p),Portfolios(p)
    initialize_paper_accounts('o',l,a,today=date(2026,10,17))
    res=paper_run_domestic('o',a,today=date(2026,10,17),quote_provider=quote)
    assert [x['status'] for x in res['results']] == ['executed','executed']
    assert next(x for x in a.list('o')['accounts'] if x['id']=='kr-base')['holdings'] == [
        {'symbol':'069500','quantity':1}]
    again=paper_run_domestic('o',a,today=date(2026,10,17),quote_provider=quote)
    assert all(x['status']=='already_executed' for x in again['results'])


def test_missing_domestic_quote_no_trade(tmp_path: Path):
    p=tmp_path/'stage1-paper.sqlite3'
    l,a=Stage1Ledger(p),Portfolios(p)
    initialize_paper_accounts('o',l,a,today=date(2026,10,17))
    bad=lambda symbol: {'status':'unavailable','tradeable':False,'symbol':symbol}
    res=paper_run_domestic('o',a,today=date(2026,10,17),quote_provider=bad)
    assert all(x['status']=='skipped' for x in res['results'])
    assert not a.orders('o','isa-base')['orders']


def test_isa_blocks_unlisted_or_us_stock(tmp_path: Path):
    p=tmp_path/'stage1-paper.sqlite3'
    l,a=Stage1Ledger(p),Portfolios(p)
    initialize_paper_accounts('o',l,a,today=date(2026,10,17))
    with pytest.raises(ValueError):
        a.trade('o','isa-base','005930','BUY',1,'isa-test-0001',quote('005930'))
    us={'status':'ok','tradeable':True,'symbol':'VOO','currency':'USD',
        'price':'600','source':'fixture','as_of':'2026-10-16'}
    with pytest.raises(ValueError):
        a.trade('o','isa-base','VOO','BUY',1,'isa-test-0002',us)
