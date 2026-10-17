from datetime import date

import pytest
from wealthy_server.stage1_bootstrap import (
    initialize_paper_accounts, get_funding_allocation, set_funding_allocation)
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios


def test_policy_changes_only_future_deposits(tmp_path):
    p=tmp_path/'paper.sqlite3'
    ledger,portfolio=Stage1Ledger(p),Portfolios(p)
    initialize_paper_accounts('owner',ledger,portfolio,today=date(2026,10,17))
    set_funding_allocation('owner',portfolio,60,20,20)
    assert get_funding_allocation('owner',portfolio)['us_pct']==60
    before={r['id']:r['krw_cash'] for r in portfolio.list('owner')['accounts']}
    assert before['default']=='3200000'
    assert before['kr-base']=='400000'
    assert before['isa-base']=='400000'
    initialize_paper_accounts('owner',ledger,portfolio,today=date(2026,10,25))
    after={r['id']:r['krw_cash'] for r in portfolio.list('owner')['accounts']}
    assert after['default']=='3410000'
    assert after['kr-base']=='470000'
    assert after['isa-base']=='470000'
    initialize_paper_accounts('owner',Stage1Ledger(p),Portfolios(p),today=date(2026,10,26))
    assert {r['id']:r['krw_cash'] for r in portfolio.list('owner')['accounts']}==after


def test_invalid_funding_policy_rejected(tmp_path):
    p=tmp_path/'paper.sqlite3'
    portfolio=Portfolios(p)
    with pytest.raises(ValueError):
        set_funding_allocation('owner',portfolio,70,20,20)
    with pytest.raises(ValueError):
        set_funding_allocation('owner',portfolio,True,0,99)


def test_zero_kr_isa_allocation_stays_historic(tmp_path):
    p=tmp_path/'paper.sqlite3'
    ledger,portfolio=Stage1Ledger(p),Portfolios(p)
    set_funding_allocation('owner',portfolio,100,0,0)
    initialize_paper_accounts('owner',ledger,portfolio,today=date(2026,10,17))
    before={r['id']:r['krw_cash'] for r in portfolio.list('owner')['accounts']}
    assert before['default']=='4000000'
    set_funding_allocation('owner',portfolio,80,10,10)
    initialize_paper_accounts('owner',ledger,portfolio,today=date(2026,10,25))
    after={r['id']:r['krw_cash'] for r in portfolio.list('owner')['accounts']}
    assert after['default']=='4280000'
    assert after['kr-base']=='35000'
    assert after['isa-base']=='35000'
