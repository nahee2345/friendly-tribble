"""Owner-isolated local notification inbox for paper workflow events.

Derived from persisted approval, report and stop-state records. No fabricated
market alert, no mobile push, no external API. Stable ids are restart-safe.
"""
from __future__ import annotations
from datetime import datetime,timezone
from .stage1_controls import list_approvals, status
from .stage1_daily_cards import list_saved_daily_cards


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_inbox (
        owner TEXT NOT NULL, event_id TEXT NOT NULL, title TEXT NOT NULL,
        detail TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT,
        PRIMARY KEY(owner,event_id)
    )""")


def inbox(owner, portfolios):
    approvals=list_approvals(owner,portfolios)["items"]
    control=status(owner,portfolios)
    reports=list_saved_daily_cards(owner,portfolios,limit=30)["items"]
    events=[]
    for a in approvals:
        events.append(("approval:"+a["request_id"]+":"+a["status"],
            "모의거래 승인 "+a["status"],
            a["symbol"]+" / "+str(a["amount_krw"])+"원 / "+a["status"],
            a["decided_at"] or a["created_at"]))
    if control["changed_at"]:
        events.append(("pause:"+control["changed_at"],
            "가상 자동주문 중지" if control["paused"] else "가상 자동주문 허용",
            control["reason"],control["changed_at"]))
    for report in reports:
        events.append(("report:"+report["for_date"],
            "Wealthy 일일 보고서 생성",
            report["for_date"]+(" · 지연 생성" if report.get("late_generation") else " · 정시 생성"),
            report["generated_kst"]))
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        for row in events:
            db.execute("INSERT OR IGNORE INTO stage1_inbox(owner,event_id,title,detail,created_at,read_at) VALUES(?,?,?,?,?,NULL)",
                       (owner,*row))
        result=db.execute("""SELECT event_id,title,detail,created_at,read_at
                 FROM stage1_inbox WHERE owner=? ORDER BY created_at DESC,event_id DESC LIMIT 100""",
                 (owner,)).fetchall()
        unread=db.execute("SELECT COUNT(*) FROM stage1_inbox WHERE owner=? AND read_at IS NULL",
                          (owner,)).fetchone()[0]
        db.execute("COMMIT")
    cols=("event_id","title","detail","created_at","read_at")
    return {"items":[dict(zip(cols,r)) for r in result],"unread":unread,
            "scope":"가상투자 프로그램 내부 알림"}


def mark_read(owner, portfolios, event_id):
    if not isinstance(event_id,str) or len(event_id)>200 or not event_id:
        raise ValueError("invalid event key")
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        count=db.execute("""UPDATE stage1_inbox SET read_at=COALESCE(read_at,?)
            WHERE owner=? AND event_id=?""",
            (datetime.now(timezone.utc).isoformat(),owner,event_id)).rowcount
        if count!=1:
            raise ValueError("inbox record missing")
        db.execute("COMMIT")
    return inbox(owner,portfolios)
