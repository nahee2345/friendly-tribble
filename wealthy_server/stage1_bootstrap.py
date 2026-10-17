"""Initialize exactly three base paper portfolios and allocate deposits idempotently.

Funding is already recorded in stage1_events by Stage1Ledger. Transfers here
only redistribute virtual KRW; no additional money is created.
"""
from __future__ import annotations

from .stage1_paper import Stage1Ledger
from .stage1_portfolios import Portfolios

# Starting allocation: US 80%, KR general 10%, Korean ISA 10%.
# Investment preferences and capital allocation are separate concepts.
BASE = (
    ('default', '미국주식 가상계좌', 'GENERAL_OVERSEAS'),
    ('kr-base', '한국주식 가상계좌', 'GENERAL_DOMESTIC'),
    ('isa-base', 'ISA 가상계좌', 'ISA_SIMULATED'),
)


def initialize_paper_accounts(owner: str, ledger: Stage1Ledger, portfolios: Portfolios,
                              *, today=None) -> dict:
    state = ledger.state(owner, today)
    with portfolios._db() as db:
        db.execute('BEGIN IMMEDIATE')
        portfolios._ensure(db, owner)
        for ident, name, kind in BASE:
            db.execute(
                "INSERT OR IGNORE INTO portfolios(owner,id,name,kind,created_at) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP)", (owner, ident, name, kind))
        db.execute('COMMIT')
    # A stable transfer key per credited deposit makes restarts harmless.
    for event in state['events']:
        if event['kind'] != 'deposit' or event['krw'] <= 0:
            continue
        key = event['id']
        amount = int(event['krw'])
        for destination, fraction in (('kr-base', 10), ('isa-base', 10)):
            transfer = amount * fraction // 100
            if transfer > 0:
                portfolios.transfer(
                    owner, 'default', destination, transfer,
                    f'alloc:{key}:{destination}')
    return portfolios.list(owner)
