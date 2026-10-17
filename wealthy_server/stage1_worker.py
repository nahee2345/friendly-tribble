"""Opted-in by running the local web server: bounded, fail-closed paper worker.

Runs immediately on startup and periodically while the server is running.
No external brokerage connector and no persistent daemon.
"""
from __future__ import annotations

import logging
from threading import Event, Thread
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from .models import User
from .stage1_bootstrap import initialize_paper_accounts
from .stage1_autorun import paper_run_once
from .stage1_domestic import paper_run_domestic

log = logging.getLogger("wealthy.paper.worker")


class PaperWorker:
    def __init__(self, database, ledger, portfolios, preferences, interval=3600):
        self.database, self.ledger = database, ledger
        self.portfolios, self.preferences = portfolios, preferences
        self.interval = max(3600, interval)
        self.stop_signal = Event()
        self.thread = None
        self.last_result = {'status': 'waiting', 'reason': '첫 검토 대기'}
        self.last_checked_at = None

    def tick(self):
        # Local mode enforces one owner; never fabricate an owner id.
        with self.database.session_factory() as session:
            owners = [str(x) for x in session.scalars(select(User.id))]
        if len(owners) != 1:
            log.warning("Paper worker suspended: expected one local owner")
            return
        owner = owners[0]
        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        initialize_paper_accounts(owner, self.ledger, self.portfolios, today=today)
        result = paper_run_once(owner, self.ledger, self.portfolios, self.preferences, today=today)
        domestic_result = paper_run_domestic(owner, self.portfolios, today=today)
        self.last_result = {'status': result.get('status', 'unknown'),
                            'domestic': domestic_result.get('results', []),
                            'reason': result.get('reason', ''),
                            'symbol': result.get('symbol')}
        self.last_checked_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
        log.info("Paper worker tick: %s", result.get("status", "unknown"))

    def _loop(self):
        while not self.stop_signal.is_set():
            try:
                self.tick()
            except Exception:
                self.last_result = {'status': 'error',
                                    'reason': '데이터 수집 또는 원장 검증 실패'}
                self.last_checked_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
                log.exception("Paper worker tick failed; no live trade was attempted")
            if self.stop_signal.wait(self.interval):
                break

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_signal.clear()
        self.thread = Thread(target=self._loop, name="wealthy-paper-stage1", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_signal.set()
        if self.thread is not None:
            self.thread.join(timeout=12)
            self.thread = None
