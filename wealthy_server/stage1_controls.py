"""Persisted fail-closed paper trading control and approval queue.

Only controls paper orders, not live brokers. The global hold survives restart.
Approval does not fill an order or bypass fresh quote, cash and risk checks.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone


def now_utc():
    return datetime.now(timezone.utc)


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_trade_control(
      owner TEXT PRIMARY KEY,
      paused INTEGER NOT NULL DEFAULT 1 CHECK(paused IN (0,1)),
      reason TEXT NOT NULL, changed_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS stage1_approvals(
      owner TEXT NOT NULL, request_id TEXT NOT NULL,
      account TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
      quantity INTEGER NOT NULL, amount_krw INTEGER NOT NULL,
      rationale TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
      decided_at TEXT,
      PRIMARY KEY(owner,request_id),
      CHECK(status IN ('PENDING','APPROVED','REJECTED','EXPIRED')),
      CHECK(amount_krw>=300000)
    )""")


def status(owner, portfolios):
    with portfolios._db() as db:
        schema(db)
        row=db.execute("SELECT paused,reason,changed_at FROM stage1_trade_control WHERE owner=?",
                       (owner,)).fetchone()
    if row is None:
        return {"paused":True,"reason":"검증 전 기본 중지","changed_at":None,
                "note":"자동매매 기본 OFF. 수동 활성화 후에도 자료·위험 검증 필요."}
    return {"paused":bool(row[0]),"reason":row[1],"changed_at":row[2]}


def set_paused(owner, portfolios, paused, reason="사용자 설정"):
    if type(paused) is not bool or not isinstance(reason,str) or len(reason)>200:
        raise ValueError("invalid paper control")
    if not paused and status(owner,portfolios)["reason"] == "검증 전 기본 중지":
        # Opt-in is explicit, not a side effect of opening the page.
        pass
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        db.execute("""INSERT INTO stage1_trade_control(owner,paused,reason,changed_at)
          VALUES(?,?,?,?) ON CONFLICT(owner) DO UPDATE SET
          paused=excluded.paused,reason=excluded.reason,changed_at=excluded.changed_at""",
          (owner,int(paused),reason,now_utc().isoformat()))
        db.execute("COMMIT")
    return status(owner,portfolios)


def request_approval(owner,portfolios,*,request_id,account,symbol,side,quantity,
                     amount_krw,rationale):
    from .stage1_portfolios import valid_key
    if (not valid_key(request_id) or not all(isinstance(x,str) and 1<=len(x)<=40
                                           for x in (account,symbol,side))
            or side not in ("BUY","SELL") or type(quantity) is not int or quantity<1
            or type(amount_krw) is not int or amount_krw<300000 or amount_krw>100000000
            or not isinstance(rationale,str) or not 1<=len(rationale)<=2000):
        raise ValueError("invalid approval")
    created=now_utc()
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        existing=db.execute("SELECT account,symbol,side,quantity,amount_krw,rationale FROM stage1_approvals WHERE owner=? AND request_id=?",
                            (owner,request_id)).fetchone()
        expected=(account,symbol,side,quantity,amount_krw,rationale)
        if existing is not None and existing!=expected:
            raise ValueError("conflicting approval key")
        if existing is None:
            db.execute("INSERT INTO stage1_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (owner,request_id,account,symbol,side,quantity,amount_krw,rationale,
                        "PENDING",created.isoformat(),(created+timedelta(hours=24)).isoformat(),None))
        db.execute("COMMIT")
    return list_approvals(owner,portfolios)


def list_approvals(owner,portfolios):
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        db.execute("""UPDATE stage1_approvals SET status='EXPIRED'
            WHERE owner=? AND status IN ('PENDING','APPROVED') AND expires_at<=?""",
            (owner,now_utc().isoformat()))
        rows=db.execute("""SELECT request_id,account,symbol,side,quantity,amount_krw,
                          rationale,status,created_at,expires_at,decided_at
                          FROM stage1_approvals WHERE owner=?
                          ORDER BY created_at DESC LIMIT 100""",(owner,)).fetchall()
        # A reused request key alone is NOT evidence that the approved order
        # was filled. Verify the immutable account, instrument, side and size.
        # If a key was reused for a different order, expose reconciliation
        # rather than showing a misleading completed approval.
        filled={
            (account, request): (symbol, side, quantity)
            for account, request, symbol, side, quantity in db.execute(
                "SELECT portfolio,request_id,symbol,side,quantity "
                "FROM virtual_orders WHERE owner=?", (owner,)
            ).fetchall()
        }
        db.execute("COMMIT")
    names=("request_id","account","symbol","side","quantity","amount_krw",
           "rationale","status","created_at","expires_at","decided_at")
    items=[dict(zip(names,row)) for row in rows]
    for item in items:
        match=filled.get((item["account"],item["request_id"]))
        expected=(item["symbol"],item["side"],item["quantity"])
        item["executed"]=(match==expected)
        item["needs_reconciliation"]=(match is not None and match!=expected)
    return {"items":items,
            "disclaimer":"승인은 실제 주문이나 가상 체결을 실행하지 않습니다. 주문 실행 시 새 시세·잔액·위험 재검증 필요."}


def decide_approval(owner,portfolios,request_id,approve):
    if type(approve) is not bool or not isinstance(request_id,str):
        raise ValueError("invalid decision")
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        row=db.execute("SELECT status,expires_at FROM stage1_approvals WHERE owner=? AND request_id=?",
                       (owner,request_id)).fetchone()
        if not row or row[0]!="PENDING" or row[1]<=now_utc().isoformat():
            raise ValueError("approval not active")
        db.execute("UPDATE stage1_approvals SET status=?,decided_at=? WHERE owner=? AND request_id=?",
                   ("APPROVED" if approve else "REJECTED",now_utc().isoformat(),owner,request_id))
        db.execute("COMMIT")
    return list_approvals(owner,portfolios)
