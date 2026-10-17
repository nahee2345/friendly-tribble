"""Isolated stage-1 funding and simulated currency ledger; never communicates with a broker."""
from __future__ import annotations

import calendar
import sqlite3
import os
import stat
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

INITIAL_KRW = 4_000_000
MONTHLY_KRW = 350_000
DEPOSIT_DAY = 25


class Stage1Ledger:
    def __init__(self, location: Path):
        self.location = location
        if location.is_symlink() or not location.parent.is_dir() or location.parent.is_symlink():
            raise ValueError('안전한 데이터 경로가 아닙니다.')

    def _connect(self):
        if self.location.is_symlink():
            raise ValueError('안전하지 않은 원장 파일입니다.')
        if self.location.exists():
            st = self.location.stat(follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or (os.name == 'posix' and (st.st_uid != os.getuid() or st.st_mode & 0o077)):
                raise ValueError('원장 소유권 또는 권한을 확인할 수 없습니다.')
        db = sqlite3.connect(self.location, timeout=10, isolation_level=None)
        if os.name == 'posix':
            os.chmod(self.location, 0o600)
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS stage1_settings (owner TEXT PRIMARY KEY, monthly_krw INTEGER NOT NULL, deposit_day INTEGER NOT NULL, reinvest INTEGER NOT NULL CHECK(reinvest=1))')
        db.execute('CREATE TABLE IF NOT EXISTS stage1_events (owner TEXT NOT NULL, event_key TEXT NOT NULL, kind TEXT NOT NULL, amount_krw INTEGER NOT NULL, amount_usd TEXT NOT NULL, rate TEXT, created_utc TEXT NOT NULL, PRIMARY KEY(owner,event_key))')
        return db

    def _ensure(self, db, owner):
        db.execute('INSERT OR IGNORE INTO stage1_settings VALUES (?, ?, ?, 1)', (owner, MONTHLY_KRW, DEPOSIT_DAY))
        db.execute('INSERT OR IGNORE INTO stage1_events VALUES (?, ?, ?, ?, ?, ?, ?)', (owner,'initial','deposit',INITIAL_KRW,'0',None,datetime.now(timezone.utc).isoformat()))

    def state(self, owner: str, today: date | None = None) -> dict:
        today = today or date.today()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._ensure(db, owner)
            monthly, day, reinvest = db.execute('SELECT monthly_krw,deposit_day,reinvest FROM stage1_settings WHERE owner=?',(owner,)).fetchone()
            # Never backfill dates before the deployment. Credit due month at most once.
            if today.day >= min(day,calendar.monthrange(today.year,today.month)[1]):
                key=f'deposit:{today.year:04d}-{today.month:02d}'
                db.execute('INSERT OR IGNORE INTO stage1_events VALUES (?, ?, ?, ?, ?, ?, ?)', (owner,key,'deposit',monthly,'0',None,datetime.now(timezone.utc).isoformat()))
            events=db.execute('SELECT event_key,kind,amount_krw,amount_usd,rate,created_utc FROM stage1_events WHERE owner=? ORDER BY created_utc,event_key',(owner,)).fetchall()
            krw=sum(row[2] for row in events)
            usd=sum((Decimal(row[3]) for row in events), Decimal('0'))
            db.execute('COMMIT')
        return {'currency':'KRW','initial_krw':INITIAL_KRW,'monthly_krw':monthly,'deposit_day':day,'reinvest':bool(reinvest), 'krw_cash':krw,'usd_cash':format(usd,'f'),'auto_trading_enabled':False, 'real_broker_connected':False,'events':[dict(zip(('id','kind','krw','usd','rate','timestamp'), e)) for e in events]}

    def exchange(self, owner: str, amount: int, rate: str, key: str) -> dict:
        if not isinstance(amount,int) or isinstance(amount,bool) or amount <= 0 or amount > 10_000_000 or not isinstance(key,str) or not 8<=len(key)<=100 or not all(c.isascii() and (c.isalnum() or c in '-_:') for c in key):
            raise ValueError('환전 입력 오류')
        try:
            decimal_rate=Decimal(rate)
        except (InvalidOperation,TypeError,ValueError):
            raise ValueError('환율 입력 오류') from None
        if not decimal_rate.is_finite() or not Decimal('100') <= decimal_rate <= Decimal('10000') or decimal_rate.as_tuple().exponent < -4:
            raise ValueError('환율 범위 오류')
        received=(Decimal(amount)/decimal_rate).quantize(Decimal('0.01'),rounding=ROUND_DOWN)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._ensure(db,owner)
            prior=db.execute('SELECT amount_krw,amount_usd,rate FROM stage1_events WHERE owner=? AND event_key=?',(owner,key)).fetchone()
            if prior:
                if prior != (-amount,format(received,'f'),format(decimal_rate,'f')):
                    raise ValueError('멱등키 충돌')
                db.execute('COMMIT')
                return self.state(owner)
            available=db.execute('SELECT COALESCE(SUM(amount_krw),0) FROM stage1_events WHERE owner=?',(owner,)).fetchone()[0]
            has_transfers=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_transfers'").fetchone()
            if has_transfers:
                available-=db.execute("SELECT COALESCE(SUM(amount_krw),0) FROM portfolio_transfers WHERE owner=? AND source='default'",(owner,)).fetchone()[0]
                available+=db.execute("SELECT COALESCE(SUM(amount_krw),0) FROM portfolio_transfers WHERE owner=? AND destination='default'",(owner,)).fetchone()[0]
            if available<amount: raise ValueError('원화 가상 잔액 부족')
            db.execute('INSERT INTO stage1_events VALUES (?,?,?,?,?,?,?)',(owner,key,'virtual_fx',-amount,format(received,'f'),format(decimal_rate,'f'),datetime.now(timezone.utc).isoformat()))
            db.execute('COMMIT')
        return self.state(owner)
