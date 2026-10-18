from copy import deepcopy
import pytest
from wealthy_server.stage10_corporate_actions import preview

SPLIT={'event_id':'split-a','symbol':'AAPL','currency':'USD','effective_date':'2026-01-01','kind':'SPLIT','amount':'2','source_url':'https://example.com/a'}
DIVIDEND={'event_id':'div-a','symbol':'AAPL','currency':'USD','effective_date':'2026-02-01','kind':'DIVIDEND','amount':'0.25','source_url':'https://example.com/b'}

def check(events):
    return preview('AAPL','10',events,currency='USD',as_of='2026-10-18')

def test_sorted_actions_and_no_mutation():
    source=[deepcopy(DIVIDEND),deepcopy(SPLIT)]
    before=deepcopy(source)
    result=check(source)
    assert result['potential_quantity']=='20'
    assert result['potential_gross_dividends']=='5.00'
    assert result['cash_or_holdings_modified'] is False
    assert source==before

def test_duplicate_prevented():
    with pytest.raises(ValueError):check([SPLIT,SPLIT])

def test_future_rejected():
    event={**SPLIT,'effective_date':'2027-01-01'}
    with pytest.raises(ValueError):check([event])

def test_wrong_instrument_rejected():
    with pytest.raises(ValueError):check([{**DIVIDEND,'symbol':'MSFT'}])

def test_invalid_amount_rejected():
    with pytest.raises(ValueError):check([{**DIVIDEND,'amount':'NaN'}])

def test_no_evidence_is_not_payment():
    r=check([])
    assert r['potential_gross_dividends']=='0'
    assert r['verified_payment'] is False
