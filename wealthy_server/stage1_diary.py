"""Persistent daily paper execution diary (not investment advice).

One row per owner and Korea-local date. Upserts preserve latest result of
the hourly worker without duplicate daily entries. No brokerage connection.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo


def save_daily_paper_report(owner, portfolios, *, us, domestic, exits):
    now=datetime.now(ZoneInfo('Asia/Seoul'))
    date=now.date().isoformat()
    summary={
        'date':date, 'checked_at':now.isoformat(),
        'us':{key:us.get(key) for key in ('status','traded','symbol','quantity','reason','price_usd')},
        'domestic':domestic.get('results',[]),
        'exits':exits.get('events',[]),
        'price_note':'일간 종가 가상거래. 시세 미확인 시 거래 없음.',
        'execution':'SIMULATED_ONLY',
    }
    with portfolios._db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS stage1_daily_reports (
            owner TEXT NOT NULL, report_date TEXT NOT NULL, checked_utc TEXT NOT NULL,
            report_json TEXT NOT NULL, PRIMARY KEY(owner,report_date)
        )''')
        db.execute('''INSERT INTO stage1_daily_reports VALUES(?,?,?,?)
            ON CONFLICT(owner,report_date) DO UPDATE SET
            checked_utc=excluded.checked_utc, report_json=excluded.report_json''',
            (owner,date,now.isoformat(),json.dumps(summary,ensure_ascii=False)))
        db.execute('COMMIT')
    return summary


def list_daily_paper_reports(owner, portfolios, limit=30):
    if not isinstance(limit,int) or not 1<=limit<=100:
        raise ValueError('limit')
    with portfolios._db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS stage1_daily_reports (
            owner TEXT NOT NULL, report_date TEXT NOT NULL, checked_utc TEXT NOT NULL,
            report_json TEXT NOT NULL, PRIMARY KEY(owner,report_date)
        )''')
        rows=db.execute('''SELECT report_json FROM stage1_daily_reports
            WHERE owner=? ORDER BY report_date DESC LIMIT ?''',
            (owner,limit)).fetchall()
    return {'items':[json.loads(row[0]) for row in rows], 'simulated_only':True}
