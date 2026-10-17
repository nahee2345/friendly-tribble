"""Three default virtual accounts, persistent funding policy and atomic allocations.

No brokerage access. Deposits are created by Stage1Ledger, then distributed
inside a *single* SQLite transaction. Historical allocations never change
when the user updates the policy.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .stage1_paper import Stage1Ledger
from .stage1_portfolios import Portfolios

BASE = (
    ('default', '미국주식 가상계좌', 'GENERAL_OVERSEAS'),
    ('kr-base', '한국주식 가상계좌', 'GENERAL_DOMESTIC'),
    ('isa-base', 'ISA 가상계좌', 'ISA_SIMULATED'),
)
DEFAULT_SPLIT = (80, 10, 10)


def _policy_tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS stage1_funding_policy (
        owner TEXT PRIMARY KEY, us_pct INTEGER NOT NULL, kr_pct INTEGER NOT NULL,
        isa_pct INTEGER NOT NULL, updated_utc TEXT NOT NULL,
        CHECK(us_pct BETWEEN 0 AND 100 AND kr_pct BETWEEN 0 AND 100
              AND isa_pct BETWEEN 0 AND 100 AND us_pct + kr_pct + isa_pct = 100)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS stage1_allocations (
        owner TEXT NOT NULL, event_key TEXT NOT NULL,
        us_pct INTEGER NOT NULL, kr_pct INTEGER NOT NULL, isa_pct INTEGER NOT NULL,
        allocated_utc TEXT NOT NULL, PRIMARY KEY(owner,event_key)
    )''')


def get_funding_allocation(owner: str, portfolios: Portfolios) -> dict:
    with portfolios._db() as db:
        _policy_tables(db)
        row=db.execute('SELECT us_pct,kr_pct,isa_pct FROM stage1_funding_policy WHERE owner=?',
                       (owner,)).fetchone() or DEFAULT_SPLIT
    return {'us_pct':row[0],'kr_pct':row[1],'isa_pct':row[2],
            'notice':'변경된 비율은 신규 미배분 입금부터 적용됩니다.'}


def set_funding_allocation(owner: str, portfolios: Portfolios,
                           us_pct: int, kr_pct: int, isa_pct: int) -> dict:
    vals=(us_pct,kr_pct,isa_pct)
    if any(not isinstance(v,int) or isinstance(v,bool) or v<0 or v>100 for v in vals) or sum(vals)!=100:
        raise ValueError('배분 비율은 합계 100%의 정수여야 합니다.')
    with portfolios._db() as db:
        db.execute('BEGIN IMMEDIATE')
        _policy_tables(db)
        db.execute('''INSERT INTO stage1_funding_policy VALUES(?,?,?,?,?)
            ON CONFLICT(owner) DO UPDATE SET us_pct=excluded.us_pct,
            kr_pct=excluded.kr_pct,isa_pct=excluded.isa_pct,
            updated_utc=excluded.updated_utc''',
            (owner,*vals,datetime.now(timezone.utc).isoformat()))
        db.execute('COMMIT')
    return get_funding_allocation(owner,portfolios)


def initialize_paper_accounts(owner: str, ledger: Stage1Ledger, portfolios: Portfolios,
                              *, today=None) -> dict:
    state = ledger.state(owner, today)
    with portfolios._db() as db:
        db.execute('BEGIN IMMEDIATE')
        portfolios._ensure(db, owner)
        _policy_tables(db)
        for ident,name,kind in BASE:
            db.execute('''INSERT OR IGNORE INTO portfolios(owner,id,name,kind,created_at)
                VALUES(?,?,?,?,?)''',
                (owner,ident,name,kind,datetime.now(timezone.utc).isoformat()))
        db.execute("UPDATE portfolios SET name='미국주식 가상계좌' "
                   "WHERE owner=? AND id='default' AND name='기본 가상계좌'",(owner,))
        policy=db.execute('SELECT us_pct,kr_pct,isa_pct FROM stage1_funding_policy WHERE owner=?',
                          (owner,)).fetchone() or DEFAULT_SPLIT
        for event in state['events']:
            if event['kind']!='deposit' or int(event['krw'])<=0:
                continue
            key=event['id']
            if db.execute('SELECT 1 FROM stage1_allocations WHERE owner=? AND event_key=?',
                          (owner,key)).fetchone():
                continue
            amount=int(event['krw'])
            old=[]
            for account_id in ('kr-base','isa-base'):
                old.append(db.execute(
                    'SELECT amount_krw FROM portfolio_transfers WHERE owner=? AND request_id=?',
                    (owner,f'alloc:{key}:{account_id}')).fetchone())
            if any(v is not None for v in old):
                # Migrate allocations performed by older code. Refuse partial legacy state.
                expected=(amount//10, amount//10)
                if any(v is None for v in old) or tuple(int(v[0]) for v in old)!=expected:
                    raise ValueError('기존 입금 배분이 불완전합니다. 데이터를 변경하지 않습니다.')
                split=DEFAULT_SPLIT
            else:
                split=policy
                domestic=amount*split[1]//100
                isa=amount*split[2]//100
                available=portfolios._money(db,owner,'default')[0]
                if available < domestic+isa:
                    raise ValueError('입금 배분 시 기본계좌 가상 잔액 부족')
                for account_id,value in (('kr-base',domestic),('isa-base',isa)):
                    if value:
                        db.execute('''INSERT INTO portfolio_transfers
                            (owner,request_id,source,destination,amount_krw,created_at)
                            VALUES(?,?,?,?,?,?)''',
                            (owner,f'alloc:{key}:{account_id}','default',account_id,value,
                             datetime.now(timezone.utc).isoformat()))
            db.execute('INSERT INTO stage1_allocations VALUES(?,?,?,?,?,?)',
                (owner,key,*split,datetime.now(timezone.utc).isoformat()))
        db.execute('COMMIT')
    return portfolios.list(owner)
