from datetime import date
from pathlib import Path
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios


def test_initial_capital_is_distributed_once(tmp_path: Path):
    path = tmp_path / 'stage1-paper.sqlite3'
    ledger, accounts = Stage1Ledger(path), Portfolios(path)
    result = initialize_paper_accounts('owner', ledger, accounts, today=date(2026, 10, 17))
    kinds = {a['kind']: a for a in result['accounts']}
    assert len(result['accounts']) == 3
    assert int(kinds['GENERAL_OVERSEAS']['krw_cash']) == 3_200_000
    assert int(kinds['GENERAL_DOMESTIC']['krw_cash']) == 400_000
    assert int(kinds['ISA_SIMULATED']['krw_cash']) == 400_000
    result = initialize_paper_accounts('owner', ledger, accounts, today=date(2026, 10, 17))
    assert sum(int(a['krw_cash']) for a in result['accounts']) == 4_000_000


def test_monthly_credit_25th_and_restart_safe(tmp_path: Path):
    path = tmp_path / 'stage1-paper.sqlite3'
    ledger, accounts = Stage1Ledger(path), Portfolios(path)
    initialize_paper_accounts('owner', ledger, accounts, today=date(2026, 10, 17))
    before = initialize_paper_accounts('owner', ledger, accounts, today=date(2026, 10, 24))
    assert sum(int(a['krw_cash']) for a in before['accounts']) == 4_000_000
    after = initialize_paper_accounts('owner', ledger, accounts, today=date(2026, 10, 25))
    assert sum(int(a['krw_cash']) for a in after['accounts']) == 4_350_000
    after2 = initialize_paper_accounts('owner', Stage1Ledger(path), Portfolios(path), today=date(2026, 10, 26))
    assert sum(int(a['krw_cash']) for a in after2['accounts']) == 4_350_000
    assert next(a for a in after2['accounts'] if a['kind']=='ISA_SIMULATED')['krw_cash'] == '435000'


def test_bootstrap_does_not_duplicate_existing_accounts(tmp_path: Path):
    path = tmp_path / 'stage1-paper.sqlite3'
    ledger, accounts = Stage1Ledger(path), Portfolios(path)
    accounts.create('owner', '추가 사용자 계좌', 'GENERAL_DOMESTIC')
    initialize_paper_accounts('owner', ledger, accounts, today=date(2026, 10, 17))
    assert len(accounts.list('owner')['accounts']) == 4
