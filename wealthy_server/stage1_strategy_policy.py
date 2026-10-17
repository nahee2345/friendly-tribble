"""Persistent owner-scoped user *policy* for the paper-investment decision engine.

Only user-provided settings: eight strategy weights, US/KR allocation and
ETF/equity allocation. Everything else is decided by the simulator.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

STRATEGIES = (
    ("index", "장기지수"),
    ("value", "가치"),
    ("quality_growth", "우량성장"),
    ("growth", "성장"),
    ("high_dividend", "고배당"),
    ("dividend_growth", "배당성장"),
    ("momentum", "모멘텀"),
    ("asset_allocation", "자산배분"),
)
DEFAULT_WEIGHTS = {
    "index": 30, "value": 10, "quality_growth": 10, "growth": 10,
    "high_dividend": 10, "dividend_growth": 10, "momentum": 10,
    "asset_allocation": 10,
}


class StrategyPolicy:
    def __init__(self, portfolios):
        self.portfolios = portfolios

    @staticmethod
    def _schema(db):
        db.execute("""CREATE TABLE IF NOT EXISTS stage1_strategy_weights (
            owner TEXT NOT NULL, strategy TEXT NOT NULL, weight INTEGER NOT NULL
            CHECK (weight >= 0 AND weight <= 100),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(owner,strategy)
        )""")

    def get(self, owner):
        with self.portfolios._db() as db:
            self._schema(db)
            rows = db.execute(
                "SELECT strategy,weight FROM stage1_strategy_weights WHERE owner=?",
                (owner,)).fetchall()
        weights = dict(rows) if rows else DEFAULT_WEIGHTS.copy()
        if set(weights) != set(DEFAULT_WEIGHTS) or sum(weights.values()) != 100:
            raise ValueError("전략 비중 원장의 무결성 오류")
        last_updated = None
        with self.portfolios._db() as db:
            self._schema(db)
            last = db.execute("SELECT MAX(updated_at) FROM stage1_strategy_weights WHERE owner=?",
                              (owner,)).fetchone()
            last_updated = last[0] if last else None
        next_change = None
        locked = False
        if last_updated:
            applied = datetime.fromisoformat(last_updated)
            deadline = applied + timedelta(days=14)
            locked = datetime.now(timezone.utc) < deadline
            next_change = deadline.isoformat()
        return {"weights": weights, "labels": dict(STRATEGIES),
                "applied_at":last_updated,"next_change_at":next_change,
                "locked":locked,"notice":"적용 후 14일 변경 제한. 기존 가상 거래는 변경하지 않습니다."}

    def save(self, owner, weights):
        if (not isinstance(weights, dict) or set(weights) != set(DEFAULT_WEIGHTS)
                or any(type(value) is not int or not 0 <= value <= 100
                       for value in weights.values())
                or sum(weights.values()) != 100):
            raise ValueError("8개 전략 비중 합계는 반드시 100%여야 합니다.")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self.portfolios._db() as db:
            self._schema(db)
            current=db.execute("SELECT strategy,weight,updated_at FROM stage1_strategy_weights WHERE owner=?",
                               (owner,)).fetchall()
            if current:
                previous={item[0]:item[1] for item in current}
                if previous==weights:
                    return self.get(owner)
                last_time=max(datetime.fromisoformat(item[2]) for item in current)
                if last_time.tzinfo is None or last_time > now_dt or now_dt-last_time < timedelta(days=14):
                    raise ValueError("전략 변경은 직전 적용일로부터 14일 후 가능합니다.")
        with self.portfolios._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._schema(db)
            db.execute("DELETE FROM stage1_strategy_weights WHERE owner=?", (owner,))
            db.executemany(
                "INSERT INTO stage1_strategy_weights(owner,strategy,weight,updated_at) VALUES(?,?,?,?)",
                [(owner, code, weights[code], now) for code in DEFAULT_WEIGHTS])
            db.execute("COMMIT")
        return self.get(owner)
