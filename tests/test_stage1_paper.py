from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory
from pathlib import Path
import pytest
from wealthy_server.stage1_paper import Stage1Ledger

def test_funding_restart_no_duplicate():
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/'test.sqlite3'
        first=Stage1Ledger(path).state('owner-a',date(2026,10,24))
        assert first['krw_cash']==4_000_000
        later=Stage1Ledger(path).state('owner-a',date(2026,10,25))
        assert later['krw_cash']==4_350_000
        assert Stage1Ledger(path).state('owner-a',date(2026,10,28))['krw_cash']==4_350_000
        assert Stage1Ledger(path).state('owner-b',date(2026,10,24))['krw_cash']==4_000_000

def test_exchange_and_conflict():
    with TemporaryDirectory() as tmp:
        ledger=Stage1Ledger(Path(tmp)/'test.sqlite3')
        first=ledger.exchange('a',350_000,'1400','request_000001')
        assert first['krw_cash']==3_650_000
        assert Decimal(first['usd_cash'])==Decimal('250.00')
        assert ledger.exchange('a',350_000,'1400','request_000001')['krw_cash']==3_650_000
        with pytest.raises(ValueError): ledger.exchange('a',350_000,'1300','request_000001')
        with pytest.raises(ValueError): ledger.exchange('a',6_000_000,'1400','request_000002')
        with pytest.raises(ValueError): ledger.exchange('a',10,'NaN','request_000003')

def test_account_eligibility():
    from wealthy_server.stage1_research import account_eligibility
    assert 'KOREAN_ISA_DIRECT' in account_eligibility('US_LISTED_EQUITY','US')['ineligible']
    assert not account_eligibility('UNKNOWN','XX')['eligible']
