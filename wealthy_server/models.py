"""SQLAlchemy persistence models for the authenticated Wealthy server.

PostgreSQL is the production database.  The small UTC type decorator keeps the
same aware-datetime contract when SQLite is used by isolated tests.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, JSON, TypeDecorator

from asset_assistant.domain import DomainValidationError


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist timestamps as timezone-aware UTC values on every backend."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise DomainValidationError("저장 시각에는 시간대가 필요합니다.")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    password_changed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )


class WebSession(Base):
    __tablename__ = "web_sessions"
    __table_args__ = (Index("ix_web_sessions_owner_expires", "owner_id", "expires_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempt_window", "username_fingerprint", "attempted_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # A successful lookup sets owner_id.  Unknown-user attempts cannot have one.
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    username_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    client_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ScenarioSnapshot(Base):
    __tablename__ = "scenario_snapshots"
    __table_args__ = (Index("ix_scenario_owner_created", "owner_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    realm: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ReportSnapshot(Base):
    __tablename__ = "report_snapshots"
    __table_args__ = (Index("ix_report_owner_generated", "owner_id", "generated_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    scenario_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_snapshots.id"), nullable=True
    )
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_cutoff_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class StrategyTargetVersion(Base):
    __tablename__ = "strategy_target_versions"
    __table_args__ = (
        UniqueConstraint("owner_id", "version_name", name="uq_target_owner_version"),
        Index("ix_target_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    version_name: Mapped[str] = mapped_column(String(100), nullable=False)
    weights: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ProviderSecret(Base):
    __tablename__ = "provider_secrets"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "provider", "environment", "secret_name", name="uq_provider_secret"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)
    secret_name: Mapped[str] = mapped_column(String(60), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("owner_id", "job_kind", "scheduled_for", name="uq_job_slot"),
        Index("ix_job_owner_scheduled", "owner_id", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    job_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    claimed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    outcome_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_run_id", "attempt_number", name="uq_job_attempt_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome_code: Mapped[str] = mapped_column(String(60), nullable=False)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("owner_id", "endpoint_hash", name="uq_push_owner_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class MonthlyAssetSnapshot(Base):
    __tablename__ = "monthly_asset_snapshots"
    __table_args__ = (Index("ix_monthly_owner_month", "owner_id", "snapshot_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    snapshot_month: Mapped[date] = mapped_column(Date, nullable=False)
    realm: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class CollectionRun(Base):
    """One provider collection attempt; official-data rows are global."""

    __tablename__ = "collection_runs"
    __table_args__ = (Index("ix_collection_provider_started", "provider", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    connection_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="FIXTURE")
    # A manual trigger may identify its user, while scheduled public collection is global.
    initiated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(60), nullable=True)


class MarketRecord(Base):
    """Normalized, provenance-carrying official provider record."""

    __tablename__ = "market_records"
    __table_args__ = (
        UniqueConstraint(
            "provider", "kind", "provider_record_id", "content_sha256", name="uq_market_content"
        ),
        Index("ix_market_lookup", "provider", "kind", "instrument_id", "effective_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_record_id: Mapped[str] = mapped_column(String(200), nullable=False)
    instrument_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    market: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    effective_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    revised: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column("metadata_json", JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class MigrationLedger(Base):
    __tablename__ = "migration_ledger"

    revision: Mapped[str] = mapped_column(String(100), primary_key=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    rollback_revision: Mapped[str | None] = mapped_column(String(100), nullable=True)


def _forbid_snapshot_mutation(mapper: Any, connection: Any, target: Any) -> None:
    raise DomainValidationError("불변 스냅샷은 수정하거나 삭제할 수 없습니다.")


for _snapshot_type in (StrategyTargetVersion, ReportSnapshot):
    event.listen(_snapshot_type, "before_update", _forbid_snapshot_mutation)
    event.listen(_snapshot_type, "before_delete", _forbid_snapshot_mutation)
