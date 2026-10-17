"""Adapters from the local web application to the existing SQLite store.

These adapters deliberately do not create SQLAlchemy copies of market,
report, job, or monthly tables.  The mature ``MarketDataStore`` remains the
single authority for those records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from asset_assistant.domain import DomainValidationError
from asset_assistant.market_data import MarketDataStore


SEOUL = ZoneInfo("Asia/Seoul")


def _owner(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise DomainValidationError("사용자 식별자를 확인할 수 없습니다.")


def _aware(value: object, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError, OverflowError):
        raise DomainValidationError(f"{label}을 확인할 수 없습니다.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DomainValidationError(f"{label}에는 시간대가 필요합니다.")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class LocalReport:
    id: int
    generated_at: datetime
    status: str
    report_markdown: str
    data_cutoff_at: datetime | None
    data_scope: str | None
    generation_kind: str | None


class LocalReportRepository:
    def __init__(
        self,
        store: MarketDataStore,
        *,
        owner_guard: Callable[[str | None], None],
    ) -> None:
        self.store = store
        self._owner_guard = owner_guard

    def _from_row(self, row: Any) -> LocalReport:
        metadata = self.store.report_metadata(int(row["id"]))
        return LocalReport(
            id=int(row["id"]),
            generated_at=_aware(row["created_at"], label="보고서 생성 시각"),
            status=str(row["status"]),
            report_markdown=str(row["markdown"]),
            # Historical rows have no display metadata.  Keep every field
            # unknown rather than deriving a scope or generation kind from the
            # Markdown and silently rewriting history.
            data_cutoff_at=(
                _aware(metadata["data_cutoff_at"], label="보고서 자료 기준 시각")
                if metadata is not None
                else None
            ),
            data_scope=metadata["data_scope"] if metadata is not None else None,
            generation_kind=(
                metadata["generation_kind"] if metadata is not None else None
            ),
        )

    def save(
        self,
        owner_id: str,
        *,
        report_markdown: str,
        status: str,
        data_cutoff_at: datetime,
        generated_at: datetime | None = None,
        scenario_snapshot_id: int | None = None,
        data_scope: str | None = None,
        generation_kind: str | None = None,
    ) -> LocalReport:
        self._owner_guard(owner_id)
        _owner(owner_id)
        del scenario_snapshot_id
        generated = generated_at or datetime.now(UTC)
        if generated.tzinfo is None or generated.utcoffset() is None:
            raise DomainValidationError("보고서 생성 시각에는 시간대가 필요합니다.")
        if data_cutoff_at.tzinfo is None or data_cutoff_at.utcoffset() is None:
            raise DomainValidationError("보고서 자료 기준 시각에는 시간대가 필요합니다.")
        mapped_status = {
            "COMPLETE": "OK",
            "OK": "OK",
            "PARTIAL": "PARTIAL",
            "ERROR": "ERROR",
        }.get(status)
        if mapped_status is None:
            raise DomainValidationError("보고서 상태를 확인할 수 없습니다.")
        metadata: dict[str, object] = {}
        if data_scope is not None or generation_kind is not None:
            if data_scope is None or generation_kind is None:
                raise DomainValidationError("보고서 이력 메타데이터를 확인할 수 없습니다.")
            metadata = {
                "data_scope": data_scope,
                "generation_kind": generation_kind,
                "data_cutoff_at": data_cutoff_at,
            }
        report_id = self.store.save_report(
            report_date=data_cutoff_at.astimezone(SEOUL).date().isoformat(),
            created_at=generated,
            status=mapped_status,
            markdown=report_markdown,
            source_run_ids=(),
            **metadata,
        )
        row = self.store.report_row(report_id)
        if row is None:  # pragma: no cover - defensive storage invariant
            raise DomainValidationError("저장된 보고서를 다시 확인할 수 없습니다.")
        return self._from_row(row)

    def list(self, owner_id: str, *, limit: int = 50) -> tuple[LocalReport, ...]:
        self._owner_guard(owner_id)
        _owner(owner_id)
        return tuple(self._from_row(row) for row in self.store.report_rows(limit=limit))

    def get(self, owner_id: str, report_id: int) -> LocalReport | None:
        self._owner_guard(owner_id)
        _owner(owner_id)
        row = self.store.report_row(report_id)
        return None if row is None else self._from_row(row)


@dataclass(frozen=True)
class LocalJob:
    id: int
    job_kind: str
    scheduled_for: datetime
    state: str
    claimed_at: datetime
    finished_at: datetime | None
    outcome_code: str | None
    attempt_count: int = 1


class LocalJobRepository:
    def __init__(
        self,
        store: MarketDataStore,
        *,
        owner_guard: Callable[[str | None], None],
    ) -> None:
        self.store = store
        self._owner_guard = owner_guard

    def list(self, owner_id: str, *, limit: int = 50) -> tuple[LocalJob, ...]:
        self._owner_guard(owner_id)
        _owner(owner_id)
        rows = self.store.job_rows(limit=min(limit, 200))
        return tuple(
            LocalJob(
                id=int(row["id"]),
                job_kind=str(row["task_name"]),
                scheduled_for=_aware(row["scheduled_for"], label="예약 슬롯 시각"),
                state=str(row["status"]),
                claimed_at=_aware(row["started_at"], label="예약 시작 시각"),
                finished_at=(
                    _aware(row["finished_at"], label="예약 완료 시각")
                    if row["finished_at"] is not None
                    else None
                ),
                outcome_code=(
                    str(row["detail_code"]) if row["detail_code"] is not None else None
                ),
            )
            for row in rows
        )


@dataclass(frozen=True)
class LocalCollectionRun:
    provider: str
    connection_mode: str
    status: str
    started_at: datetime
    finished_at: datetime


class LocalMarketRepository:
    def __init__(self, store: MarketDataStore) -> None:
        self.store = store

    def latest_run(self, provider: str) -> LocalCollectionRun | None:
        row = self.store.latest_collection_run(provider)
        if row is None:
            return None
        status = {"OK": "SUCCEEDED", "PARTIAL": "PARTIAL", "ERROR": "FAILED"}.get(
            str(row["status"]), "UNKNOWN"
        )
        return LocalCollectionRun(
            provider=str(row["provider"]),
            # The v2 local schema predates an explicit fixture/live column.
            # Do not claim a live connection from an unknowable historical row.
            connection_mode="LOCAL_STORE_UNSPECIFIED",
            status=status,
            started_at=_aware(row["started_at"], label="수집 시작 시각"),
            finished_at=_aware(row["finished_at"], label="수집 완료 시각"),
        )

    def monthly_history(self, *, limit: int = 120) -> tuple[dict[str, object], ...]:
        rows = self.store.monthly_snapshot_rows(limit=limit)
        return tuple(
            {
                "month": str(row["month"]),
                "investable_total_krw": (
                    str(row["investment_value_text"])
                    if row["investment_value_text"] is not None
                    else None
                ),
                "net_contributions_krw": None,
                "investment_pnl_krw": None,
                "data_scope": str(row["realm"]),
                "as_of": str(row["as_of"]),
                "status": "KNOWN" if bool(row["complete"]) else "INCOMPLETE",
            }
            for row in rows
        )
