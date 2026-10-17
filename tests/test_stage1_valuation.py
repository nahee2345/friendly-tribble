"""Stage1 valuation and atomic exchange/entry regression tests."""
from datetime import date
from decimal import Decimal
import sqlite3

import pytest

from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_valuation import portfolio_report


def fake_fx():
    return {'status': 'ok', 'executable': False, 'usd_krw': '1400',
            'as_of': '2026-10-16', 'source': 'fixture'}


def fake_quote(symbol):
    return {'status': 'ok', 'tradeable': True, 'currency': 'USD',
            'symbol': symbol, 'price': '25.00', 'source': 'fixture',
            'as_of': '2026-10-16'}


def test_portfolio_report_excludes_deposits_from_profit(tmp_path):
    p = tmp_path / 'stage1-paper.sqlite3'
    ledger, accounts = Stage1Ledger(p), Portfolios(p)
    initialize_paper_accounts('test', ledger, accounts, today=date(2026, 10, 17))
    report = portfolio_report('test', accounts, ledger,
            today=date(2026, 10, 17), quote_provider=fake_quote, fx_provider=fake_fx)
    assert report['complete']
    assert report['contributions_krw'] == 4_000_000
    assert Decimal(report['portfolio_value_krw']) == 4_000_000
    assert Decimal(report['return_krw']) == 0


def test_atomic_auto_exchange_and_buy(tmp_path):
    p = tmp_path / 'stage1-paper.sqlite3'
    ledger, accounts = Stage1Ledger(p), Portfolios(p)
    initialize_paper_accounts('test', ledger, accounts, today=date(2026, 10, 17))
    quote = fake_quote('SCHD')
    result = accounts.auto_buy_usd_with_fx('test', 'SCHD', quote, '1400', 'auto:20261017:etf')
    assert result['status'] == 'executed'
    assert accounts.list('test')['accounts'][0]['holdings'] == [{'symbol': 'SCHD', 'quantity': 1}]
    again = accounts.auto_buy_usd_with_fx('test', 'SCHD', quote, '1400', 'auto:20261017:etf')
    assert again['status'] == 'already_executed'
    report = portfolio_report('test', accounts, ledger,
           today=date(2026, 10, 17), quote_provider=fake_quote, fx_provider=fake_fx)
    assert report['complete']
    assert len(report['positions']) == 1
    assert len(accounts.orders('test', 'default')['orders']) == 1


def test_bad_quote_rolls_back_fx_and_order(tmp_path):
    p = tmp_path / 'stage1-paper.sqlite3'
    ledger, accounts = Stage1Ledger(p), Portfolios(p)
    initialize_paper_accounts('test', ledger, accounts, today=date(2026, 10, 17))
    before = ledger.state('test', date(2026, 10, 17))['events']
    q = fake_quote('SCHD')
    q['source'] = None
    with pytest.raises((ValueError, TypeError, KeyError, sqlite3.IntegrityError)):
        accounts.auto_buy_usd_with_fx('test','SCHD',q,'1400','auto:20261017:etf')
    assert ledger.state('test', date(2026, 10, 17))['events'] == before
    assert accounts.orders('test','default')['orders'] == []


def test_missing_quote_never_reports_complete_profit(tmp_path):
    p=tmp_path/'stage1-paper.sqlite3'
    ledger, accounts=Stage1Ledger(p), Portfolios(p)
    initialize_paper_accounts('test', ledger, accounts, today=date(2026,10,17))
    accounts.auto_buy_usd_with_fx('test','SCHD',fake_quote('SCHD'),'1400','auto:20261017:etf')
    report=portfolio_report('test',accounts,ledger,today=date(2026,10,17),
        quote_provider=lambda _: {'status':'unavailable','tradeable':False},
        fx_provider=fake_fx)
    assert report['complete'] is False
    assert report['portfolio_value_krw'] is None
    assert report['missing'][0]['symbol'] == 'SCHD'
