"""PostgreSQL-claimed Asia/Seoul schedule for read-only collection and reports."""

from __future__ import annotations

import socket
import sys
import time
from datetime import UTC, date, datetime, time as wall_time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from asset_assistant.cli import _SafeArgumentParser
from asset_assistant.domain import DomainValidationError
from asset_assistant.market_data import CollectionStatus

from .application import WebApplicationService
from .collection import collect_public_official_sources
from .config import ServerSettings
from .database import Database
from .models import User
from .repositories import (
    CredentialRepository,
    JobRepository,
    MarketRepository,
    ReportRepository,
    ScenarioRepository,
    StrategyTargetRepository,
)
from .security import SecretVault


SEOUL = ZoneInfo("Asia/Seoul")
SCHEDULES = {"collect": wall_time(15, 0), "report": wall_time(16, 0)}


def _slot(day: date, task: str) -> datetime:
    return datetime.combine(day, SCHEDULES[task], tzinfo=SEOUL).astimezone(UTC)


def due_slots(now: datetime) -> tuple[tuple[str, datetime], ...]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise DomainValidationError("worker 현재 시각에는 시간대가 필요합니다.")
    local = now.astimezone(SEOUL)
    due: list[tuple[str, datetime]] = []
    # The prior day is retried for up to 24 hours after a restart.  Older jobs
    # are left missing and visible rather than silently backfilled with future data.
    for day in (local.date() - timedelta(days=1), local.date()):
        for task in ("collect", "report"):
            scheduled = _slot(day, task)
            if scheduled <= now.astimezone(UTC) < scheduled + timedelta(hours=24):
                due.append((task, scheduled))
    return tuple(due)


class ServerWorker:
    def __init__(self, database: Database, settings: ServerSettings, *, clock=lambda: datetime.now(UTC)) -> None:
        self.database = database
        self.clock = clock
        self.worker_id = f"{socket.gethostname()[:40]}-{uuid4().hex[:12]}"
        vault = SecretVault({"v1": settings.master_key}, active_key_version="v1")
        self.jobs = JobRepository(database.session_factory)
        self.market = MarketRepository(database.session_factory)
        self.service = WebApplicationService(
            scenarios=ScenarioRepository(database.session_factory),
            reports=ReportRepository(database.session_factory),
            strategy_targets=StrategyTargetRepository(database.session_factory),
            credentials=CredentialRepository(database.session_factory, vault),
            jobs=self.jobs,
            market=self.market,
            clock=clock,
        )

    def active_owner_ids(self) -> tuple[str, ...]:
        with self.database.session_factory() as session:
            return tuple(session.scalars(select(User.id).where(User.is_active.is_(True))))

    def run_due_once(self) -> int:
        now = self.clock().astimezone(UTC)
        completed = 0
        for owner_id in self.active_owner_ids():
            for task, scheduled_for in due_slots(now):
                job = self.jobs.claim(
                    owner_id,
                    job_kind=task,
                    scheduled_for=scheduled_for,
                    worker_id=self.worker_id,
                    now=now,
                    lease_for=timedelta(minutes=45),
                )
                if job is None:
                    continue
                state, code = self._run_job(owner_id, task)
                self.jobs.finish(
                    owner_id,
                    job.id,
                    worker_id=self.worker_id,
                    state=state,
                    outcome_code=code,
                    finished_at=self.clock().astimezone(UTC),
                )
                completed += 1
        return completed

    def _run_job(self, owner_id: str, task: str) -> tuple[str, str]:
        try:
            if task == "collect":
                results = collect_public_official_sources(
                    self.market,
                    initiated_by=owner_id,
                )
                if all(result.status is CollectionStatus.OK for result in results):
                    return "SUCCEEDED", "PUBLIC_OFFICIAL_COLLECTION_OK"
                if any(result.records for result in results):
                    return "PARTIAL", "PUBLIC_OFFICIAL_COLLECTION_PARTIAL"
                return "FAILED", "PUBLIC_OFFICIAL_COLLECTION_FAILED"
            if task == "report":
                self.service.generate_report(owner_id)
                return "SUCCEEDED", "SEVEN_SECTION_REPORT_CREATED"
            return "FAILED", "UNSUPPORTED_TASK"
        except DomainValidationError:
            # Missing ledger or provider failure must not erase prior valid data.
            return "FAILED", "SAFE_DOMAIN_FAILURE"
        except Exception:
            return "FAILED", "SAFE_UNEXPECTED_FAILURE"


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(prog="wealthy-worker", description="Wealthy 서버 예약 worker")
    parser.add_argument("--once", action="store_true", help="현재까지 due인 slot을 한 번 확인")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if not 10 <= args.poll_seconds <= 600:
        sys.stderr.write("worker 오류: polling 간격을 확인할 수 없습니다.\n")
        return 2
    try:
        settings = ServerSettings.from_environment()
        database = Database(settings.database_url)
        database.create_schema()
        worker = ServerWorker(database, settings)
        if args.once:
            count = worker.run_due_once()
            sys.stdout.write(f"worker 확인 완료: 처리 slot {count}개\n")
            return 0
        while True:
            worker.run_due_once()
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    except DomainValidationError as exc:
        sys.stderr.write(f"worker 오류: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
