from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
from decimal import Decimal
import pytest
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios

def test_default_and_separate_accounts_restart():
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/'stage1-paper.sqlite3'
        Stage1Ledger(path).state('a',date(2026,10,24))
        p=Portfolios(path)
        assert p.list('a')['accounts'][0]['krw_cash']=='4000000'
        accounts=p.create('a','배당','GENERAL_DOMESTIC')['accounts']
        ident=next(x['id'] for x in accounts if x['id']!='default')
        p.transfer('a','default',ident,250000,'transfer_0001')
        p.transfer('a','default',ident,250000,'transfer_0001')
        new=Portfolios(path).list('a')['accounts']
        assert next(x for x in new if x['id']=='default')['krw_cash']=='3750000'
        assert next(x for x in new if x['id']==ident)['krw_cash']=='250000'
        with pytest.raises(ValueError):p.transfer('a','default',ident,150000,'transfer_0001')
        assert len(p.list('b')['accounts'])==1
        with pytest.raises(ValueError):p.transfer('b','default',ident,1,'transfer_0002')

def test_virtual_buy_sell_idempotency():
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/'stage1-paper.sqlite3'
        ledger=Stage1Ledger(path)
        ledger.state('a',date(2026,10,24))
        ledger.exchange('a',140000,'1400','exchange_0001')
        p=Portfolios(path)
        quote={'symbol':'AAPL','price':'20.00','currency':'USD','status':'ok','tradeable':True,'source':'fixture','as_of':'2026-10-16'}
        one=p.trade('a','default','AAPL','BUY',2,'order_00001',quote)
        assert one['accounts'][0]['holdings'][0]['quantity']==2
        assert Decimal(one['accounts'][0]['usd_cash'])==Decimal('60')
        p.trade('a','default','AAPL','BUY',2,'order_00001',quote)
        assert p.list('a')['accounts'][0]['trades']==1
        with pytest.raises(ValueError):p.trade('a','default','AAPL','BUY',3,'order_00001',quote)
        with pytest.raises(ValueError):p.trade('a','default','AAPL','BUY',4,'order_00002',quote)
        with pytest.raises(ValueError):p.trade('a','default','AAPL','SELL',3,'order_00003',quote)
        after=p.trade('a','default','AAPL','SELL',1,'order_00004',quote)
        assert after['accounts'][0]['holdings'][0]['quantity']==1
        assert Portfolios(path).list('a')['accounts'][0]['trades']==2

def test_unverified_quote_rejected():
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/'stage1-paper.sqlite3'
        Stage1Ledger(path).state('a',date(2026,10,24))
        with pytest.raises(ValueError):Portfolios(path).trade('a','default','AAPL','BUY',1,'order_00005',{'symbol':'AAPL','price':'2','currency':'USD','status':'unavailable','tradeable':False})
