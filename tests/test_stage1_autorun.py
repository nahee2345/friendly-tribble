from datetime import date
from pathlib import Path

from wealthy_server.stage1_autorun import paper_run_once
from wealthy_server.stage1_discovery import PreferenceStore
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios


def test_one_step_paper_buy_idempotent(tmp_path: Path):
    owner = 'test-owner'
    path = tmp_path / 'paper.sqlite3'
    ledger, portfolios, prefs = Stage1Ledger(path), Portfolios(path), PreferenceStore(path)
    quote = {'symbol': 'SCHD', 'status': 'ok', 'tradeable': True,
             'currency': 'USD', 'price': '25.00', 'source': 'fixture',
             'as_of': '2026-10-16'}
    fx = {'status': 'ok', 'executable': False, 'usd_krw': '1400',
          'as_of': '2026-10-16'}
    def q(ticker):
        return quote if ticker == 'SCHD' else {'status': 'unavailable', 'tradeable': False}
    opts = {'today': date(2026, 10, 17), 'quote_provider': q,
            'fx_provider': lambda: fx}
    first = paper_run_once(owner, ledger, portfolios, prefs, **opts)
    assert first['status'] == 'executed' and first['symbol'] == 'SCHD'
    assert portfolios.list(owner)['accounts'][0]['holdings'] == [
        {'symbol': 'SCHD', 'quantity': 1}]
    assert paper_run_once(owner, ledger, portfolios, prefs, **opts)['status'] == 'already_executed'
    assert len(portfolios.orders(owner, 'default')['orders']) == 1


def test_fail_closed_on_missing_rate(tmp_path):
    path = tmp_path / 'paper.sqlite3'
    result = paper_run_once('x', Stage1Ledger(path), Portfolios(path),
                            PreferenceStore(path), today=date(2026, 10, 17),
                            fx_provider=lambda: {'status': 'unavailable'})
    assert result['status'] == 'skipped' and result['traded'] is False


def test_user_allocation_can_disable_one_click(tmp_path):
    path = tmp_path / 'paper.sqlite3'
    prefs = PreferenceStore(path)
    prefs.save('x', 0, 20)
    result = paper_run_once('x', Stage1Ledger(path), Portfolios(path), prefs,
                            today=date(2026, 10, 17),
                            fx_provider=lambda: (_ for _ in ()).throw(
                                AssertionError('no network expected')))
    assert result['status'] == 'skipped'
