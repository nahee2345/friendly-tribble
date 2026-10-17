"""외부 시장자료의 정규형과 로컬 SQLite 저장소.

이 모듈은 주문이나 계좌 조회 기능을 제공하지 않는다. 외부 자료와 사용자 원장은
서로 합산하지 않으며, 실패한 수집은 마지막 정상 자료를 삭제하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from .domain import (
    DomainValidationError,
    StrategyVersionConflictError,
    aware_datetime,
    nonempty,
)


SCHEMA_VERSION = 3

_REPORT_DATA_SCOPES = frozenset(
    {"SAMPLE", "MANUAL_PAPER_SNAPSHOT", "MANUAL_ACTUAL", "AUTOMATED_MARKET"}
)
_REPORT_GENERATION_KINDS = frozenset({"MANUAL", "SCHEDULED"})
_REPORT_METADATA_PREFIX = "report.metadata."

_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _utc_epoch_microseconds(value: datetime, *, field_name: str) -> int:
    """시간대가 있는 시각을 float 손실 없는 UTC epoch µs로 바꾼다."""

    aware_datetime(value, field_name=field_name)
    delta = value.astimezone(timezone.utc) - _UTC_EPOCH
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _stored_epoch_microseconds(value: str, *, field_name: str) -> int:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError, OverflowError):
        raise DomainValidationError(
            "시장자료 저장소의 기존 시각을 안전하게 변환할 수 없습니다."
        ) from None
    return _utc_epoch_microseconds(parsed, field_name=field_name)


class RecordKind(str, Enum):
    PRICE_VOLUME = "PRICE_VOLUME"
    DISCLOSURE = "DISCLOSURE"
    FINANCIAL = "FINANCIAL"
    MACRO = "MACRO"
    ETF_PRODUCT = "ETF_PRODUCT"
    NEWS = "NEWS"


class CollectionStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"


@dataclass(frozen=True)
class NormalizedRecord:
    provider: str
    kind: RecordKind
    record_id: str
    instrument_id: str
    market: str
    currency: str | None
    effective_at: datetime
    collected_at: datetime
    unit: str
    value: str | None = None
    published_at: datetime | None = None
    revised: bool = False
    source_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("provider", "record_id", "instrument_id", "market", "unit"):
            nonempty(getattr(self, name), field_name=name)
        aware_datetime(self.effective_at, field_name="effective_at")
        aware_datetime(self.collected_at, field_name="collected_at")
        if self.published_at is not None:
            aware_datetime(self.published_at, field_name="published_at")
            if self.published_at > self.collected_at:
                raise DomainValidationError("공표 시각은 수집 시각보다 미래일 수 없습니다.")
        if self.value is not None:
            try:
                parsed = Decimal(self.value)
            except (InvalidOperation, ValueError):
                raise DomainValidationError("외부 자료 값은 정확한 십진 문자열이어야 합니다.") from None
            if not parsed.is_finite():
                raise DomainValidationError("외부 자료 값에 무한대와 NaN은 허용되지 않습니다.")
        if self.source_url and not self.source_url.startswith("https://"):
            raise DomainValidationError("외부 자료 출처는 HTTPS 공식 주소여야 합니다.")
        try:
            json.dumps(self.metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise DomainValidationError("외부 자료 메타데이터를 안전하게 저장할 수 없습니다.") from None


@dataclass(frozen=True)
class CollectionIssue:
    code: str
    message: str

    def __post_init__(self) -> None:
        nonempty(self.code, field_name="issue.code")
        nonempty(self.message, field_name="issue.message")


@dataclass(frozen=True)
class CollectionResult:
    provider: str
    started_at: datetime
    finished_at: datetime
    status: CollectionStatus
    records: tuple[NormalizedRecord, ...] = ()
    issues: tuple[CollectionIssue, ...] = ()

    def __post_init__(self) -> None:
        nonempty(self.provider, field_name="provider")
        aware_datetime(self.started_at, field_name="started_at")
        aware_datetime(self.finished_at, field_name="finished_at")
        if self.finished_at < self.started_at:
            raise DomainValidationError("수집 완료 시각은 시작 시각보다 빠를 수 없습니다.")
        if self.status is CollectionStatus.OK and self.issues:
            raise DomainValidationError("정상 수집 결과에는 오류 항목이 있을 수 없습니다.")
        if self.status is CollectionStatus.ERROR and self.records:
            raise DomainValidationError("전체 실패 결과에는 새 정상 자료를 포함할 수 없습니다.")
        if any(record.provider != self.provider for record in self.records):
            raise DomainValidationError("수집 결과와 자료의 공급자가 일치하지 않습니다.")


def default_data_directory() -> Path:
    """운영체제별 사용자 자료 디렉터리(프로젝트 밖)를 반환한다."""

    import os
    import sys

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise DomainValidationError("Windows 사용자 자료 폴더를 확인할 수 없습니다.")
        return Path(base) / "LongTermAssetAssistant"
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "long-term-asset-assistant"
    return Path.home() / ".local" / "share" / "long-term-asset-assistant"


def default_database_path() -> Path:
    return default_data_directory() / "market-data.sqlite3"


class MarketDataStore:
    """스레드마다 연결을 여는 작은 SQLite 저장소."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_database_path()

    def initialize(self) -> None:
        """Open, migrate, and close the store without reading user rows."""

        connection = self._connect()
        connection.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            parent_existed = self.path.parent.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt" and (
                not parent_existed or self.path == default_database_path()
            ):
                os.chmod(self.path.parent, 0o700)
            connection = sqlite3.connect(self.path, timeout=5.0)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            self._ensure_schema(connection)
            return connection
        except (OSError, sqlite3.Error):
            raise DomainValidationError(
                "시장자료 저장소를 열 수 없습니다. 전체 경로는 표시하지 않습니다."
            ) from None

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        # Reject unknown databases before any CREATE/INDEX statement can
        # mutate them.  SQLite ``executescript`` commits implicitly, so the
        # v3 extension itself is created later with individual execute calls
        # inside BEGIN IMMEDIATE.
        meta_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
        ).fetchone()
        if meta_exists is not None:
            existing = connection.execute(
                "SELECT version FROM schema_meta LIMIT 1"
            ).fetchone()
            if existing is not None and existing[0] not in {1, 2, SCHEMA_VERSION}:
                raise DomainValidationError("지원하지 않는 시장자료 저장소 버전입니다.")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collection_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                finished_at_us INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('OK','PARTIAL','ERROR')),
                issue_codes_json TEXT NOT NULL,
                record_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS market_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES collection_runs(id),
                provider TEXT NOT NULL,
                kind TEXT NOT NULL,
                provider_record_id TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                market TEXT NOT NULL,
                currency TEXT,
                effective_at TEXT NOT NULL,
                effective_at_us INTEGER NOT NULL,
                published_at TEXT,
                published_at_us INTEGER,
                collected_at TEXT NOT NULL,
                collected_at_us INTEGER NOT NULL,
                unit TEXT NOT NULL,
                value_text TEXT,
                revised INTEGER NOT NULL CHECK(revised IN (0,1)),
                source_url TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                UNIQUE(provider, kind, provider_record_id, content_sha256)
            );
            CREATE INDEX IF NOT EXISTS market_records_lookup
                ON market_records(provider, kind, instrument_id, effective_at, collected_at);
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategy_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_name TEXT NOT NULL UNIQUE,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidate_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_version TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                cutoff_at TEXT NOT NULL,
                status TEXT NOT NULL,
                score_text TEXT,
                reasons_json TEXT NOT NULL,
                source_record_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS report_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                markdown TEXT NOT NULL,
                source_run_ids_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS monthly_asset_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                realm TEXT NOT NULL,
                as_of TEXT NOT NULL,
                investment_value_text TEXT,
                complete INTEGER NOT NULL CHECK(complete IN (0,1)),
                source_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(month, realm, source_sha256)
            );
            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                detail_code TEXT,
                UNIQUE(task_name, scheduled_for)
            );
            CREATE TABLE IF NOT EXISTS job_attempt_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_code TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_daily_usage (
                provider TEXT NOT NULL,
                usage_date TEXT NOT NULL,
                reserved_calls INTEGER NOT NULL CHECK(reserved_calls >= 0),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(provider, usage_date)
            );
            CREATE TABLE IF NOT EXISTS operation_leases (
                operation_name TEXT PRIMARY KEY,
                owner_token TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )
        row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is not None and row[0] not in {1, 2, SCHEMA_VERSION}:
            raise DomainValidationError("지원하지 않는 시장자료 저장소 버전입니다.")
        try:
            connection.execute("BEGIN IMMEDIATE")
            MarketDataStore._migrate_precision_columns(connection)
            MarketDataStore._migrate_market_information_v3(connection)
            if row is None:
                connection.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif row[0] in {1, 2}:
                connection.execute(
                    "UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,)
                )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS market_records_lookup_us
                   ON market_records(
                       provider, kind, instrument_id,
                       effective_at_us, collected_at_us
                   )"""
            )
            connection.commit()
        except DomainValidationError:
            connection.rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError):
            connection.rollback()
            raise DomainValidationError(
                "시장자료 저장소의 시각 정밀도를 안전하게 변환하지 못했습니다."
            ) from None

    @staticmethod
    def _migrate_market_information_v3(connection: sqlite3.Connection) -> None:
        """Create the v3 market-information extension inside one transaction."""

        statements = (
            """CREATE TABLE IF NOT EXISTS market_information (
                info_id TEXT PRIMARY KEY,
                canonical_key TEXT NOT NULL UNIQUE,
                provider_id TEXT NOT NULL,
                institution_id TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                source_name TEXT NOT NULL,
                title_original TEXT NOT NULL,
                language TEXT NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('MARKET','COMPANY','ECONOMY','FILING')),
                related_symbols_json TEXT NOT NULL,
                original_url TEXT,
                published_at TEXT,
                published_at_us INTEGER,
                collected_at TEXT NOT NULL,
                collected_at_us INTEGER NOT NULL,
                provider_item_id TEXT,
                source_document_id TEXT,
                published_precision TEXT NOT NULL CHECK(published_precision IN ('DATE','DATETIME','UNKNOWN')),
                is_sample INTEGER NOT NULL CHECK(is_sample IN (0,1)),
                sources_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                first_seen_at_us INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                updated_at_us INTEGER NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS market_information_published
                ON market_information(published_at_us DESC, collected_at_us DESC, info_id)""",
            """CREATE INDEX IF NOT EXISTS market_information_collected
                ON market_information(collected_at_us DESC, info_id)""",
            """CREATE TABLE IF NOT EXISTS market_information_provider_state (
                provider_id TEXT PRIMARY KEY,
                institution_id TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                source_name TEXT NOT NULL,
                status TEXT NOT NULL,
                cadence_seconds INTEGER NOT NULL CHECK(cadence_seconds > 0),
                last_attempt_at TEXT,
                last_attempt_at_us INTEGER,
                last_success_at TEXT,
                last_success_at_us INTEGER,
                next_allowed_at TEXT,
                next_allowed_at_us INTEGER,
                etag TEXT,
                last_modified TEXT,
                error_code TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
                last_http_status INTEGER,
                checked_not_modified INTEGER NOT NULL DEFAULT 0 CHECK(checked_not_modified IN (0,1)),
                updated_at TEXT NOT NULL,
                updated_at_us INTEGER NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS market_information_refresh_claims (
                provider_id TEXT PRIMARY KEY,
                owner_token TEXT NOT NULL,
                expires_at_us INTEGER NOT NULL
            )""",
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _migrate_precision_columns(connection: sqlite3.Connection) -> None:
        """v1 ISO 시각을 보존하며 UTC epoch µs 열을 추가·역채운다."""

        run_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(collection_runs)")
        }
        if "finished_at_us" not in run_columns:
            connection.execute(
                "ALTER TABLE collection_runs ADD COLUMN finished_at_us INTEGER"
            )

        record_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(market_records)")
        }
        for column in ("effective_at_us", "published_at_us", "collected_at_us"):
            if column not in record_columns:
                connection.execute(
                    f"ALTER TABLE market_records ADD COLUMN {column} INTEGER"  # nosec: fixed column names
                )

        for run in connection.execute(
            "SELECT id, finished_at FROM collection_runs WHERE finished_at_us IS NULL"
        ):
            connection.execute(
                "UPDATE collection_runs SET finished_at_us=? WHERE id=?",
                (
                    _stored_epoch_microseconds(
                        run["finished_at"], field_name="collection_run.finished_at"
                    ),
                    run["id"],
                ),
            )

        for record in connection.execute(
            """SELECT id, effective_at, published_at, collected_at
               FROM market_records
               WHERE effective_at_us IS NULL
                  OR (published_at IS NOT NULL AND published_at_us IS NULL)
                  OR collected_at_us IS NULL"""
        ):
            published_at = record["published_at"]
            connection.execute(
                """UPDATE market_records
                   SET effective_at_us=?, published_at_us=?, collected_at_us=?
                   WHERE id=?""",
                (
                    _stored_epoch_microseconds(
                        record["effective_at"], field_name="record.effective_at"
                    ),
                    (
                        _stored_epoch_microseconds(
                            published_at, field_name="record.published_at"
                        )
                        if published_at is not None
                        else None
                    ),
                    _stored_epoch_microseconds(
                        record["collected_at"], field_name="record.collected_at"
                    ),
                    record["id"],
                ),
            )

        incomplete_run = connection.execute(
            "SELECT 1 FROM collection_runs WHERE finished_at_us IS NULL LIMIT 1"
        ).fetchone()
        incomplete_record = connection.execute(
            """SELECT 1 FROM market_records
               WHERE effective_at_us IS NULL
                  OR (published_at IS NOT NULL AND published_at_us IS NULL)
                  OR collected_at_us IS NULL
               LIMIT 1"""
        ).fetchone()
        if incomplete_run is not None or incomplete_record is not None:
            raise DomainValidationError(
                "시장자료 저장소의 기존 시각을 안전하게 변환할 수 없습니다."
            )

    @staticmethod
    def _record_payload(record: NormalizedRecord) -> str:
        publication_basis = record.metadata.get("publication_time_basis")
        payload = {
            "provider": record.provider,
            "kind": record.kind.value,
            "record_id": record.record_id,
            "instrument_id": record.instrument_id,
            "market": record.market,
            "currency": record.currency,
            "effective_at": record.effective_at.isoformat(),
            # 수집 시각과 FIRST_OBSERVED_AT_COLLECTION 시각은 같은 공급자
            # 레코드의 내용이 아니라 관측 이력이다. 이를 해시에서 빼야 매일
            # 같은 공시·통계·뉴스를 중복 저장하지 않고 실제 값/메타데이터
            # 변경만 새 버전으로 남길 수 있다.
            "published_at": (
                None
                if publication_basis == "FIRST_OBSERVED_AT_COLLECTION"
                else record.published_at.isoformat() if record.published_at else None
            ),
            "unit": record.unit,
            "value": record.value,
            "revised": record.revised,
            "source_url": record.source_url,
            "metadata": record.metadata,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def save_collection(self, result: CollectionResult) -> int:
        """한 결과를 원자적으로 저장한다. 오류 결과는 이전 정상 레코드를 보존한다."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """INSERT INTO collection_runs
                   (provider, started_at, finished_at, finished_at_us, status,
                    issue_codes_json, record_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.provider,
                    result.started_at.isoformat(),
                    result.finished_at.isoformat(),
                    _utc_epoch_microseconds(
                        result.finished_at, field_name="finished_at"
                    ),
                    result.status.value,
                    json.dumps([issue.code for issue in result.issues], ensure_ascii=False),
                    len(result.records),
                ),
            )
            run_id = int(cursor.lastrowid)
            inserted_count = 0
            for record in result.records:
                payload = self._record_payload(record)
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                record_cursor = connection.execute(
                    """INSERT OR IGNORE INTO market_records
                       (run_id, provider, kind, provider_record_id, instrument_id, market,
                        currency, effective_at, effective_at_us,
                        published_at, published_at_us, collected_at, collected_at_us,
                        unit, value_text, revised, source_url, metadata_json, content_sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        record.provider,
                        record.kind.value,
                        record.record_id,
                        record.instrument_id,
                        record.market,
                        record.currency,
                        record.effective_at.isoformat(),
                        _utc_epoch_microseconds(
                            record.effective_at, field_name="record.effective_at"
                        ),
                        record.published_at.isoformat() if record.published_at else None,
                        (
                            _utc_epoch_microseconds(
                                record.published_at, field_name="record.published_at"
                            )
                            if record.published_at is not None
                            else None
                        ),
                        record.collected_at.isoformat(),
                        _utc_epoch_microseconds(
                            record.collected_at, field_name="record.collected_at"
                        ),
                        record.unit,
                        record.value,
                        int(record.revised),
                        record.source_url,
                        json.dumps(record.metadata, ensure_ascii=False, sort_keys=True, allow_nan=False),
                        digest,
                    ),
                )
                inserted_count += max(0, record_cursor.rowcount)
            connection.execute(
                "UPDATE collection_runs SET record_count = ? WHERE id = ?",
                (inserted_count, run_id),
            )
            connection.commit()
            return run_id
        except (sqlite3.Error, OSError, TypeError, ValueError):
            connection.rollback()
            raise DomainValidationError(
                "시장자료 갱신을 저장하지 못했습니다. 기존 정상 자료는 유지됩니다."
            ) from None
        finally:
            connection.close()

    def latest_records(
        self,
        *,
        kind: RecordKind | None = None,
        provider: str | None = None,
        cutoff_at: datetime | None = None,
        require_known_publication_time: bool = False,
    ) -> tuple[sqlite3.Row, ...]:
        clauses: list[str] = []
        arguments: list[str | int] = []
        if kind is not None:
            clauses.append("kind = ?")
            arguments.append(kind.value)
        if provider is not None:
            clauses.append("provider = ?")
            arguments.append(provider)
        if cutoff_at is not None:
            aware_datetime(cutoff_at, field_name="cutoff_at")
            cutoff_us = _utc_epoch_microseconds(cutoff_at, field_name="cutoff_at")
            clauses.append("effective_at_us <= ?")
            arguments.append(cutoff_us)
            clauses.append("(published_at IS NULL OR published_at_us <= ?)")
            arguments.append(cutoff_us)
            clauses.append("collected_at_us <= ?")
            arguments.append(cutoff_us)
        if require_known_publication_time:
            clauses.append("published_at IS NOT NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""SELECT * FROM market_records {where}
                    ORDER BY effective_at_us DESC,
                             collected_at_us DESC,
                             id DESC""",  # nosec: fixed clauses only
                arguments,
            ).fetchall()
            return tuple(rows)
        finally:
            connection.close()

    def provider_summaries(
        self, *, cutoff_at: datetime | None = None
    ) -> tuple[sqlite3.Row, ...]:
        arguments: tuple[int, ...] = ()
        cutoff_clause = ""
        if cutoff_at is not None:
            aware_datetime(cutoff_at, field_name="cutoff_at")
            cutoff_clause = "WHERE finished_at_us <= ?"
            arguments = (
                _utc_epoch_microseconds(cutoff_at, field_name="cutoff_at"),
            )
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""SELECT r.* FROM collection_runs r
                   JOIN (SELECT provider, MAX(id) AS max_id FROM collection_runs
                         {cutoff_clause} GROUP BY provider) x
                     ON x.max_id = r.id
                   ORDER BY r.provider""",  # nosec: fixed optional clause only
                arguments,
            ).fetchall()
            return tuple(rows)
        finally:
            connection.close()

    def set_setting(self, key: str, value: Any, *, updated_at: datetime) -> None:
        nonempty(key, field_name="setting.key")
        aware_datetime(updated_at, field_name="updated_at")
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise DomainValidationError("설정값을 안전하게 저장할 수 없습니다.") from None
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                                                  updated_at=excluded.updated_at""",
                (key, encoded, updated_at.isoformat()),
            )
            connection.commit()
        except sqlite3.Error:
            raise DomainValidationError("설정을 저장할 수 없습니다.") from None
        finally:
            connection.close()

    def get_setting(self, key: str, default: Any = None) -> Any:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return default if row is None else json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError):
            raise DomainValidationError("설정을 읽을 수 없습니다.") from None
        finally:
            connection.close()

    def save_strategy_version(
        self,
        *,
        version_name: str,
        config: dict[str, Any],
        created_at: datetime,
    ) -> bool:
        """전략 설정을 이름별 불변 버전으로 보존한다.

        같은 이름과 같은 내용의 재저장은 멱등 처리하고, 같은 이름으로 내용을
        덮어쓰는 것은 거부한다.
        """

        name = nonempty(version_name, field_name="strategy.version")
        aware_datetime(created_at, field_name="created_at")
        try:
            encoded = json.dumps(
                config,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise DomainValidationError("전략 버전을 안전하게 저장할 수 없습니다.") from None
        connection = self._connect()
        try:
            existing = connection.execute(
                "SELECT config_json FROM strategy_versions WHERE version_name = ?",
                (name,),
            ).fetchone()
            if existing is not None:
                if existing["config_json"] == encoded:
                    return False
                raise StrategyVersionConflictError(
                    "같은 전략 버전 이름의 이전 기록은 덮어쓸 수 없습니다. 새 버전 이름이 필요합니다."
                )
            connection.execute(
                """INSERT INTO strategy_versions(version_name, config_json, created_at)
                   VALUES (?, ?, ?)""",
                (name, encoded, created_at.isoformat()),
            )
            connection.commit()
            return True
        except DomainValidationError:
            raise
        except sqlite3.Error:
            raise DomainValidationError("전략 버전을 저장할 수 없습니다.") from None
        finally:
            connection.close()

    def strategy_version_row(self, version_name: str) -> sqlite3.Row | None:
        """Return one immutable strategy-version row without exposing SQL errors.

        The GUI read model uses this to show the creation timestamp belonging to
        the currently selected immutable version.  It deliberately does not
        infer that timestamp from the mutable ``strategy.current`` setting.
        """

        name = nonempty(version_name, field_name="strategy.version")
        connection = self._connect()
        try:
            return connection.execute(
                """SELECT version_name, config_json, created_at
                   FROM strategy_versions WHERE version_name = ?""",
                (name,),
            ).fetchone()
        except sqlite3.Error:
            raise DomainValidationError("전략 버전 기록을 읽을 수 없습니다.") from None
        finally:
            connection.close()

    def save_candidate_assessment(
        self,
        *,
        strategy_version: str,
        instrument_id: str,
        cutoff_at: datetime,
        status: str,
        score: Decimal | None,
        reasons: Iterable[str],
        source_record_ids: Iterable[int],
        created_at: datetime,
    ) -> int:
        """후보 평가를 새 행으로 추가해 당시 근거를 덮어쓰지 않는다."""

        aware_datetime(cutoff_at, field_name="cutoff_at")
        aware_datetime(created_at, field_name="created_at")
        cutoff_us = _utc_epoch_microseconds(cutoff_at, field_name="cutoff_at")
        if cutoff_at > created_at:
            raise DomainValidationError(
                "후보 평가 기준시각은 기록 생성시각보다 미래일 수 없습니다."
            )
        if status not in {"ELIGIBLE", "UNAVAILABLE", "EXCLUDED"}:
            raise DomainValidationError("후보 평가 상태를 확인할 수 없습니다.")
        reason_list = [nonempty(value, field_name="assessment.reason") for value in reasons]
        if not reason_list:
            raise DomainValidationError("후보 평가에는 하나 이상의 근거가 필요합니다.")
        record_ids = [int(value) for value in source_record_ids]
        if any(value <= 0 for value in record_ids) or len(set(record_ids)) != len(record_ids):
            raise DomainValidationError("후보 평가의 출처 레코드 ID가 올바르지 않습니다.")
        if score is not None and (
            not isinstance(score, Decimal)
            or not score.is_finite()
            or score < Decimal("0")
            or score > Decimal("100")
        ):
            raise DomainValidationError("후보 평가 점수는 0~100 범위의 정확한 십진수여야 합니다.")
        if status == "ELIGIBLE":
            if score is None or not record_ids:
                raise DomainValidationError(
                    "적격 후보 평가는 점수와 출처 레코드를 모두 보존해야 합니다."
                )
        elif score is not None:
            raise DomainValidationError("적격이 아닌 후보 평가에는 점수를 저장할 수 없습니다.")
        instrument = nonempty(instrument_id, field_name="instrument_id")
        connection = self._connect()
        try:
            strategy_row = connection.execute(
                "SELECT 1 FROM strategy_versions WHERE version_name = ?",
                (nonempty(strategy_version, field_name="strategy_version"),),
            ).fetchone()
            if strategy_row is None:
                raise DomainValidationError(
                    "후보 평가에 사용한 전략 버전을 확인할 수 없습니다."
                )
            if record_ids:
                placeholders = ",".join("?" for _ in record_ids)
                source_rows = connection.execute(
                    f"""SELECT id, instrument_id FROM market_records
                        WHERE id IN ({placeholders})
                          AND effective_at_us <= ?
                          AND published_at IS NOT NULL
                          AND published_at_us <= ?
                          AND collected_at_us <= ?""",  # nosec: placeholders only
                    (*record_ids, cutoff_us, cutoff_us, cutoff_us),
                ).fetchall()
                if len(source_rows) != len(record_ids):
                    raise DomainValidationError(
                        "후보 평가의 출처 레코드와 기준시각을 확인할 수 없습니다."
                    )
                if any(row["instrument_id"] != instrument for row in source_rows):
                    raise DomainValidationError(
                        "후보 평가 종목과 출처 레코드 종목이 일치하지 않습니다."
                    )
            cursor = connection.execute(
                """INSERT INTO candidate_assessments
                   (strategy_version, instrument_id, cutoff_at, status, score_text,
                    reasons_json, source_record_ids_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_version,
                    instrument,
                    cutoff_at.isoformat(),
                    nonempty(status, field_name="assessment.status"),
                    str(score) if score is not None else None,
                    json.dumps(reason_list, ensure_ascii=False),
                    json.dumps(record_ids),
                    created_at.isoformat(),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        except sqlite3.Error:
            raise DomainValidationError("후보 평가 기록을 저장할 수 없습니다.") from None
        finally:
            connection.close()

    def run_ids_for_record_ids(
        self,
        record_ids: Iterable[int],
        *,
        cutoff_at: datetime,
    ) -> tuple[int, ...]:
        """기준시각까지 사용 가능한 레코드들의 수집 run을 완전하게 반환한다."""

        aware_datetime(cutoff_at, field_name="cutoff_at")
        cutoff_us = _utc_epoch_microseconds(cutoff_at, field_name="cutoff_at")
        identifiers = [int(value) for value in record_ids]
        if any(value <= 0 for value in identifiers) or len(set(identifiers)) != len(identifiers):
            raise DomainValidationError("출처 레코드 ID가 올바르지 않습니다.")
        if not identifiers:
            return ()
        placeholders = ",".join("?" for _ in identifiers)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""SELECT id, run_id FROM market_records
                    WHERE id IN ({placeholders})
                      AND effective_at_us <= ?
                      AND published_at IS NOT NULL
                      AND published_at_us <= ?
                      AND collected_at_us <= ?""",  # nosec: placeholders only
                (*identifiers, cutoff_us, cutoff_us, cutoff_us),
            ).fetchall()
            if len(rows) != len(identifiers):
                raise DomainValidationError(
                    "보고서 후보 출처 레코드와 기준시각을 확인할 수 없습니다."
                )
            return tuple(sorted({int(row["run_id"]) for row in rows}))
        except sqlite3.Error:
            raise DomainValidationError("보고서 후보 출처를 확인할 수 없습니다.") from None
        finally:
            connection.close()

    def candidate_assessment_rows(self, *, limit: int = 100) -> tuple[sqlite3.Row, ...]:
        if not (1 <= limit <= 1000):
            raise DomainValidationError("후보 평가 조회 개수가 허용 범위를 벗어났습니다.")
        connection = self._connect()
        try:
            return tuple(
                connection.execute(
                    "SELECT * FROM candidate_assessments ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )
        finally:
            connection.close()

    def source_record_rows(
        self,
        record_ids: Iterable[int],
        *,
        cutoff_at: datetime,
    ) -> tuple[sqlite3.Row, ...]:
        """Resolve assessment source rows that were public by ``cutoff_at``.

        Missing or subsequently corrupted references are simply absent from the
        result so a read-only UI can mark their timing as unavailable.  Values
        after the historical assessment cutoff are never returned.
        """

        aware_datetime(cutoff_at, field_name="cutoff_at")
        identifiers = [int(value) for value in record_ids]
        if (
            any(value <= 0 for value in identifiers)
            or len(set(identifiers)) != len(identifiers)
            or len(identifiers) > 500
        ):
            raise DomainValidationError("출처 레코드 ID가 올바르지 않습니다.")
        if not identifiers:
            return ()
        cutoff_us = _utc_epoch_microseconds(cutoff_at, field_name="cutoff_at")
        placeholders = ",".join("?" for _ in identifiers)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""SELECT id, effective_at, collected_at
                    FROM market_records
                    WHERE id IN ({placeholders})
                      AND effective_at_us <= ?
                      AND published_at IS NOT NULL
                      AND published_at_us <= ?
                      AND collected_at_us <= ?""",  # nosec: placeholders only
                (*identifiers, cutoff_us, cutoff_us, cutoff_us),
            ).fetchall()
            by_id = {int(row["id"]): row for row in rows}
            return tuple(by_id[value] for value in identifiers if value in by_id)
        except sqlite3.Error:
            raise DomainValidationError("후보 평가 출처 시각을 읽을 수 없습니다.") from None
        finally:
            connection.close()

    def claim_job(self, task_name: str, scheduled_for: datetime, started_at: datetime) -> bool:
        nonempty(task_name, field_name="task_name")
        aware_datetime(scheduled_for, field_name="scheduled_for")
        aware_datetime(started_at, field_name="started_at")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO job_runs(task_name, scheduled_for, started_at, status)
                   VALUES (?, ?, ?, 'RUNNING')""",
                (task_name, scheduled_for.isoformat(), started_at.isoformat()),
            )
            connection.commit()
            return True
        except sqlite3.IntegrityError:
            connection.rollback()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """SELECT status, started_at FROM job_runs
                       WHERE task_name=? AND scheduled_for=?""",
                    (task_name, scheduled_for.isoformat()),
                ).fetchone()
                recoverable = False
                if row is not None and row["status"] == "ERROR":
                    recoverable = True
                elif row is not None and row["status"] == "RUNNING":
                    previous_start = datetime.fromisoformat(row["started_at"])
                    recoverable = previous_start <= started_at - timedelta(hours=2)
                if not recoverable:
                    connection.rollback()
                    return False
                if row is not None and row["status"] == "RUNNING":
                    connection.execute(
                        """INSERT INTO job_attempt_history
                           (task_name, scheduled_for, started_at, finished_at,
                            status, detail_code)
                           VALUES (?, ?, ?, ?, 'ABANDONED_STALE', 'STALE_RETRY')""",
                        (
                            task_name,
                            scheduled_for.isoformat(),
                            row["started_at"],
                            started_at.isoformat(),
                        ),
                    )
                cursor = connection.execute(
                    """UPDATE job_runs
                       SET started_at=?, finished_at=NULL, status='RUNNING',
                           detail_code='RECOVERED_RETRY'
                       WHERE task_name=? AND scheduled_for=?
                         AND status IN ('ERROR','RUNNING')""",
                    (started_at.isoformat(), task_name, scheduled_for.isoformat()),
                )
                connection.commit()
                return cursor.rowcount == 1
            except (sqlite3.Error, ValueError, TypeError, OverflowError):
                connection.rollback()
                raise DomainValidationError(
                    "예약 작업 재시도 상태를 안전하게 확인할 수 없습니다."
                ) from None
        except sqlite3.Error:
            connection.rollback()
            raise DomainValidationError("예약 작업 상태를 저장할 수 없습니다.") from None
        finally:
            connection.close()

    def finish_job(
        self,
        task_name: str,
        scheduled_for: datetime,
        *,
        finished_at: datetime,
        status: str,
        detail_code: str,
    ) -> None:
        aware_datetime(finished_at, field_name="finished_at")
        connection = self._connect()
        try:
            cursor = connection.execute(
                """UPDATE job_runs SET finished_at=?, status=?, detail_code=?
                   WHERE task_name=? AND scheduled_for=?""",
                (
                    finished_at.isoformat(),
                    nonempty(status, field_name="status"),
                    nonempty(detail_code, field_name="detail_code"),
                    task_name,
                    scheduled_for.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise DomainValidationError("예약 작업 완료 대상을 확인할 수 없습니다.")
            row = connection.execute(
                """SELECT started_at FROM job_runs
                   WHERE task_name=? AND scheduled_for=?""",
                (task_name, scheduled_for.isoformat()),
            ).fetchone()
            connection.execute(
                """INSERT INTO job_attempt_history
                   (task_name, scheduled_for, started_at, finished_at,
                    status, detail_code)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    task_name,
                    scheduled_for.isoformat(),
                    row["started_at"],
                    finished_at.isoformat(),
                    nonempty(status, field_name="status"),
                    nonempty(detail_code, field_name="detail_code"),
                ),
            )
            connection.commit()
        except sqlite3.Error:
            raise DomainValidationError("예약 작업 완료 상태를 저장할 수 없습니다.") from None
        finally:
            connection.close()

    def job_rows(self, *, limit: int = 20) -> tuple[sqlite3.Row, ...]:
        if not (1 <= limit <= 200):
            raise DomainValidationError("실행 이력 조회 개수가 허용 범위를 벗어났습니다.")
        connection = self._connect()
        try:
            return tuple(
                connection.execute(
                    "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            )
        finally:
            connection.close()

    def job_attempt_rows(self, *, limit: int = 100) -> tuple[sqlite3.Row, ...]:
        if not (1 <= limit <= 1000):
            raise DomainValidationError("예약 시도 이력 조회 개수가 허용 범위를 벗어났습니다.")
        connection = self._connect()
        try:
            return tuple(
                connection.execute(
                    "SELECT * FROM job_attempt_history ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )
        finally:
            connection.close()

    def job_status(self, task_name: str, scheduled_for: datetime) -> sqlite3.Row | None:
        """같은 실제 예약 시각의 실행 상태를 UTC 오프셋과 무관하게 찾는다."""

        nonempty(task_name, field_name="task_name")
        aware_datetime(scheduled_for, field_name="scheduled_for")
        connection = self._connect()
        try:
            return connection.execute(
                """SELECT * FROM job_runs
                   WHERE task_name = ?
                     AND julianday(scheduled_for) = julianday(?)
                   ORDER BY id DESC LIMIT 1""",
                (task_name, scheduled_for.isoformat()),
            ).fetchone()
        finally:
            connection.close()

    def reserve_provider_calls(
        self,
        provider: str,
        usage_date: str,
        *,
        requested_calls: int,
        daily_limit: int,
        updated_at: datetime,
    ) -> bool:
        """공급자 일일 호출 예산을 프로세스 간 원자적으로 보수 예약한다."""

        nonempty(provider, field_name="provider")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", usage_date) is None:
            raise DomainValidationError("공급자 호출 예산 날짜 형식이 올바르지 않습니다.")
        if not (1 <= requested_calls <= daily_limit <= 100_000):
            raise DomainValidationError("공급자 호출 예산 범위가 올바르지 않습니다.")
        aware_datetime(updated_at, field_name="updated_at")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT reserved_calls FROM provider_daily_usage
                   WHERE provider=? AND usage_date=?""",
                (provider, usage_date),
            ).fetchone()
            used = 0 if row is None else int(row["reserved_calls"])
            if used + requested_calls > daily_limit:
                connection.rollback()
                return False
            connection.execute(
                """INSERT INTO provider_daily_usage
                   (provider, usage_date, reserved_calls, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(provider, usage_date) DO UPDATE SET
                       reserved_calls=excluded.reserved_calls,
                       updated_at=excluded.updated_at""",
                (
                    provider,
                    usage_date,
                    used + requested_calls,
                    updated_at.isoformat(),
                ),
            )
            connection.commit()
            return True
        except sqlite3.Error:
            connection.rollback()
            raise DomainValidationError(
                "공급자 호출 예산을 안전하게 확인할 수 없습니다."
            ) from None
        finally:
            connection.close()

    def acquire_operation_lease(
        self,
        operation_name: str,
        owner_token: str,
        *,
        acquired_at: datetime,
        lease_seconds: int = 7200,
    ) -> bool:
        nonempty(operation_name, field_name="operation_name")
        nonempty(owner_token, field_name="owner_token")
        aware_datetime(acquired_at, field_name="acquired_at")
        if not (60 <= lease_seconds <= 86_400):
            raise DomainValidationError("작업 잠금 유지시간이 허용 범위를 벗어났습니다.")
        expires_at = acquired_at + timedelta(seconds=lease_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM operation_leases WHERE julianday(expires_at) <= julianday(?)",
                (acquired_at.isoformat(),),
            )
            try:
                connection.execute(
                    """INSERT INTO operation_leases
                       (operation_name, owner_token, acquired_at, expires_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        operation_name,
                        owner_token,
                        acquired_at.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return False
            connection.commit()
            return True
        except sqlite3.Error:
            connection.rollback()
            raise DomainValidationError("작업 중복 실행 잠금을 확인할 수 없습니다.") from None
        finally:
            connection.close()

    def release_operation_lease(self, operation_name: str, owner_token: str) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "DELETE FROM operation_leases WHERE operation_name=? AND owner_token=?",
                (operation_name, owner_token),
            )
            connection.commit()
        except sqlite3.Error:
            raise DomainValidationError("작업 중복 실행 잠금을 해제할 수 없습니다.") from None
        finally:
            connection.close()

    def operation_lease_active(self, operation_name: str, *, at: datetime) -> bool:
        aware_datetime(at, field_name="at")
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT 1 FROM operation_leases
                   WHERE operation_name=?
                     AND julianday(expires_at) > julianday(?)""",
                (operation_name, at.isoformat()),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def save_monthly_snapshot(
        self,
        *,
        month: str,
        realm: str,
        as_of: datetime,
        investment_value: Decimal | None,
        complete: bool,
        source_bytes: bytes,
        created_at: datetime,
    ) -> bool:
        if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month) is None:
            raise DomainValidationError("월별 기록의 월은 YYYY-MM 형식이어야 합니다.")
        aware_datetime(as_of, field_name="as_of")
        aware_datetime(created_at, field_name="created_at")
        digest = hashlib.sha256(source_bytes).hexdigest()
        connection = self._connect()
        try:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO monthly_asset_snapshots
                   (month, realm, as_of, investment_value_text, complete,
                    source_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    month,
                    realm,
                    as_of.isoformat(),
                    str(investment_value) if investment_value is not None else None,
                    int(complete),
                    digest,
                    created_at.isoformat(),
                ),
            )
            connection.commit()
            return cursor.rowcount == 1
        except sqlite3.Error:
            raise DomainValidationError("월별 자산 기록을 저장할 수 없습니다.") from None
        finally:
            connection.close()

    def monthly_snapshot_rows(self, *, limit: int = 120) -> tuple[sqlite3.Row, ...]:
        """Read monthly summaries without exposing or inventing missing values."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise DomainValidationError("월별 기록 조회 개수가 허용 범위를 벗어났습니다.")
        connection = self._connect()
        try:
            return tuple(
                connection.execute(
                    """SELECT * FROM monthly_asset_snapshots
                       ORDER BY month DESC, id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            )
        except sqlite3.Error:
            raise DomainValidationError("월별 자산 기록을 읽을 수 없습니다.") from None
        finally:
            connection.close()

    def save_report(
        self,
        *,
        report_date: str,
        created_at: datetime,
        status: str,
        markdown: str,
        source_run_ids: Iterable[int],
        data_scope: str | None = None,
        generation_kind: str | None = None,
        data_cutoff_at: datetime | None = None,
    ) -> int:
        aware_datetime(created_at, field_name="created_at")
        created_at_us = _utc_epoch_microseconds(created_at, field_name="created_at")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date) is None:
            raise DomainValidationError("보고서 날짜 형식이 올바르지 않습니다.")
        if status not in {"OK", "PARTIAL", "ERROR"}:
            raise DomainValidationError("보고서 상태를 확인할 수 없습니다.")
        metadata_values = (data_scope, generation_kind, data_cutoff_at)
        if any(value is not None for value in metadata_values) and not all(
            value is not None for value in metadata_values
        ):
            raise DomainValidationError("보고서 이력 메타데이터를 확인할 수 없습니다.")
        if data_scope is not None and data_scope not in _REPORT_DATA_SCOPES:
            raise DomainValidationError("보고서 자료 영역을 확인할 수 없습니다.")
        if generation_kind is not None and generation_kind not in _REPORT_GENERATION_KINDS:
            raise DomainValidationError("보고서 생성 종류를 확인할 수 없습니다.")
        if data_cutoff_at is not None:
            aware_datetime(data_cutoff_at, field_name="data_cutoff_at")
        run_ids = [int(value) for value in source_run_ids]
        if any(value <= 0 for value in run_ids) or len(set(run_ids)) != len(run_ids):
            raise DomainValidationError("보고서 출처 실행 ID가 올바르지 않습니다.")
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                existing_count = connection.execute(
                    f"""SELECT COUNT(*) FROM collection_runs
                        WHERE id IN ({placeholders})
                          AND finished_at_us <= ?""",  # nosec: placeholders only
                    (*run_ids, created_at_us),
                ).fetchone()[0]
                if existing_count != len(run_ids):
                    raise DomainValidationError(
                        "보고서 출처 실행과 생성시각을 확인할 수 없습니다."
                    )
            cursor = connection.execute(
                """INSERT INTO report_snapshots
                   (report_date, created_at, status, markdown, source_run_ids_json, content_sha256)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    report_date,
                    created_at.isoformat(),
                    status,
                    markdown,
                    json.dumps(run_ids),
                    digest,
                ),
            )
            report_id = int(cursor.lastrowid)
            if data_scope is not None:
                metadata = {
                    "data_scope": data_scope,
                    "generation_kind": generation_kind,
                    "data_cutoff_at": data_cutoff_at.isoformat(),
                }
                connection.execute(
                    """INSERT INTO app_settings(key, value_json, updated_at)
                       VALUES (?, ?, ?)""",
                    (
                        f"{_REPORT_METADATA_PREFIX}{report_id}",
                        json.dumps(
                            metadata,
                            ensure_ascii=False,
                            sort_keys=True,
                            allow_nan=False,
                        ),
                        created_at.isoformat(),
                    ),
                )
            connection.commit()
            return report_id
        except sqlite3.Error:
            raise DomainValidationError("자동 보고서를 저장할 수 없습니다.") from None
        finally:
            connection.close()

    def report_metadata(self, report_id: int) -> dict[str, str] | None:
        """Return immutable display metadata, or ``None`` for historical rows.

        Report rows created before this metadata was introduced are deliberately
        not guessed from their Markdown or timestamps.  The web UI must label
        those rows as unknown instead of rewriting history.
        """

        if isinstance(report_id, bool) or not isinstance(report_id, int) or report_id < 1:
            raise DomainValidationError("보고서 식별자를 확인할 수 없습니다.")
        value = self.get_setting(f"{_REPORT_METADATA_PREFIX}{report_id}")
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {
            "data_scope",
            "generation_kind",
            "data_cutoff_at",
        }:
            raise DomainValidationError("보고서 이력 메타데이터를 확인할 수 없습니다.")
        data_scope = value.get("data_scope")
        generation_kind = value.get("generation_kind")
        cutoff_text = value.get("data_cutoff_at")
        if (
            data_scope not in _REPORT_DATA_SCOPES
            or generation_kind not in _REPORT_GENERATION_KINDS
            or not isinstance(cutoff_text, str)
        ):
            raise DomainValidationError("보고서 이력 메타데이터를 확인할 수 없습니다.")
        try:
            cutoff = datetime.fromisoformat(cutoff_text)
        except (TypeError, ValueError, OverflowError):
            raise DomainValidationError("보고서 이력 메타데이터를 확인할 수 없습니다.") from None
        aware_datetime(cutoff, field_name="report.data_cutoff_at")
        return {
            "data_scope": data_scope,
            "generation_kind": generation_kind,
            "data_cutoff_at": cutoff.isoformat(),
        }

    def latest_report(self) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM report_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()

    def report_rows(self, *, limit: int = 50) -> tuple[sqlite3.Row, ...]:
        """Return immutable report snapshots for the local read-only web UI."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise DomainValidationError("보고서 조회 개수가 허용 범위를 벗어났습니다.")
        connection = self._connect()
        try:
            return tuple(
                connection.execute(
                    "SELECT * FROM report_snapshots ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )
        except sqlite3.Error:
            raise DomainValidationError("저장된 보고서 목록을 읽을 수 없습니다.") from None
        finally:
            connection.close()

    def report_row(self, report_id: int) -> sqlite3.Row | None:
        """Read one report by its integer id without accepting raw SQL input."""

        if isinstance(report_id, bool) or not isinstance(report_id, int) or report_id < 1:
            raise DomainValidationError("보고서 식별자를 확인할 수 없습니다.")
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT * FROM report_snapshots WHERE id=?", (report_id,)
            ).fetchone()
        except sqlite3.Error:
            raise DomainValidationError("저장된 보고서를 읽을 수 없습니다.") from None
        finally:
            connection.close()

    def latest_collection_run(self, provider: str) -> sqlite3.Row | None:
        """Return provider run metadata only; provider content is not exposed."""

        checked = nonempty(provider, field_name="provider")
        connection = self._connect()
        try:
            return connection.execute(
                """SELECT * FROM collection_runs
                   WHERE provider=?
                   ORDER BY finished_at_us DESC, id DESC LIMIT 1""",
                (checked,),
            ).fetchone()
        except sqlite3.Error:
            raise DomainValidationError("공급자 수집 상태를 읽을 수 없습니다.") from None
        finally:
            connection.close()

    def latest_report_for_date(self, report_date: str) -> sqlite3.Row | None:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date) is None:
            raise DomainValidationError("보고서 날짜 형식이 올바르지 않습니다.")
        connection = self._connect()
        try:
            return connection.execute(
                """SELECT * FROM report_snapshots
                   WHERE report_date=? ORDER BY id DESC LIMIT 1""",
                (report_date,),
            ).fetchone()
        finally:
            connection.close()
