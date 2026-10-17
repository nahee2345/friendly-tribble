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
from .stage1_diary import save_daily_paper_report
from .stage1_strategy_policy import StrategyPolicy
from .stage1_controls import status as trade_status
from .stage1_daily_cards import store_daily_cards_after_16
from .stage1_catalog import ensure_seed,refresh as refresh_security_catalog
from .stage1_hub import review_news

log = logging.getLogger("wealthy.paper.worker")


class PaperWorker:
    def __init__(self, database, ledger, portfolios, preferences, interval=3600,
                 market_collector=None, portfolio_engine=None,
                 accounting_engine=None):
        self.database, self.ledger = database, ledger
        self.portfolios, self.preferences = portfolios, preferences
        self.interval = max(3600, interval)
        self.market_collector = market_collector
        self.portfolio_engine = portfolio_engine
        self.accounting_engine = accounting_engine
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
        store_daily_cards_after_16(owner,self.portfolios,self.ledger,
                                   self.preferences,StrategyPolicy(self.portfolios))
        # Collection failures must not suppress paper risk controls.
        try:
            ensure_seed(self.portfolios)
            refresh_security_catalog(self.portfolios)
        except Exception:
            log.warning("Security directory refresh deferred",exc_info=True)
        try:
            review_news(owner,self.portfolios,notify=True)
        except Exception:
            log.warning("30-day holding news review unavailable",exc_info=True)
        # Market data are cached separately from paper cash/orders. A cache
        # failure never authorizes or substitutes a simulated execution quote.
        if self.market_collector is not None:
            try:
                data_result=self.market_collector.tick(owner,self.portfolios,max_requests=6)
                log.info("Stage2 public reference refresh: %s attempts",
                         len(data_result["attempts"]))
            except Exception:
                log.warning("Stage2 market-cache refresh deferred",exc_info=True)
        # A daily NAV is sampled only after 16:00 KST and only if all held
        # instruments pass Stage2 source verification. A cash-only book is
        # valid without FX when no USD cash or securities are held.
        if self.accounting_engine is not None:
            try:
                saved=self.accounting_engine.capture_daily_nav(owner)
                log.info("Stage5 gross NAV snapshot: %s",saved.get("status"))
            except Exception:
                log.warning("Stage5 daily NAV sampling deferred",exc_info=True)
        control = trade_status(owner, self.portfolios)
        if control["paused"]:
            self.last_result = {"status":"paused","reason":control["reason"],
                                "domestic":[],"exits":[]}
            self.last_checked_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
            return
        # Stage4 is the single owner of unattended paper allocation and
        # cost-basis exits. The four historical sleeve loops are deliberately
        # NOT invoked: they had independent cash caps and could interfere
        # with portfolio-wide targets.
        if self.portfolio_engine is None:
            self.last_result={"status":"disabled",
                              "reason":"통합 포트폴리오 엔진이 설정되지 않음"}
        else:
            result=self.portfolio_engine.run(owner)
            filled=result.get("executed",[])
            exits={"events":[{"account":x["account"],"symbol":x["symbol"],
                "status":"executed","side":"SELL","quantity":x["quantity"]}
                for x in filled if x["side"]=="SELL"]}
            save_daily_paper_report(owner,self.portfolios,
                us={"status":result["status"],"traded":bool(filled),
                    "reason":result.get("reason",""),
                    "symbol":filled[0]["symbol"] if filled else None},
                domestic={"results":[x for x in filled
                    if x["account"] in ("kr-base","isa-base")]},
                exits=exits,
                us_stocks={"status":"stage4_unified","engine":"stage4-v1"},
                kr_stocks={"status":"stage4_unified","engine":"stage4-v1"})
            self.last_result={
                "status":result["status"],"engine":"stage4-v1",
                "executed":filled,"skipped":result.get("skipped",[]),
                "exits":exits["events"],"domestic":[x for x in filled
                    if x["account"] in ("kr-base","isa-base")],
                "reason":result.get("reason",""),
                "symbol":filled[0]["symbol"] if filled else None}
        self.last_checked_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
        log.info("Paper worker Stage4 tick: %s", self.last_result.get("status","unknown"))

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
