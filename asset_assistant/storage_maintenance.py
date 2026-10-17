"""Fixture-only storage maintenance and low-space safety gates.

This module intentionally has no CLI, web, scheduler, or runtime wiring.  A
destructive operation requires an explicit capability rooted below
``/dev/shm`` and a marker created by the fixture owner.  It therefore cannot
be used to clean an existing user data directory in this development stage.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, Literal, Protocol

from .domain import DomainValidationError, aware_datetime


GIB = 1024**3
DEFAULT_LOW_SPACE_BYTES = 2 * GIB
DEFAULT_SPACE_CHECK_INTERVAL_SECONDS = 300
TEMPORARY_RETENTION_MICROSECONDS = 24 * 60 * 60 * 1_000_000
FIXTURE_MARKER_NAME = ".wealthy-storage-cleanup-fixture-v1"
FIXTURE_MARKER_CONTENT = b"WEALTHY_STORAGE_CLEANUP_FIXTURE_V1\n"
REFERENCE_HOLD_TABLE = "market_information_retention_holds"
_ALLOWED_BACKUP_FORMATS = frozenset(
    {
        "WEALTHY-LOCAL-SQLITE-AES256-GCM-V1",
        "WEALTHY-LOCAL-SQLITE-AES256-GCM-V2",
    }
)
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class StorageMaintenanceError(DomainValidationError):
    """A path- and user-data-redacted maintenance failure."""


class _StatVFS(Protocol):
    f_bavail: int
    f_frsize: int
    f_blocks: int
    f_favail: int


class CleanupAuditRecorder(Protocol):
    """Two-phase audit port supplied by the fixture owner.

    ``begin`` must durably record an intent before any deletion is attempted.
    ``complete`` records only category/count/status data from ``CleanupResult``;
    paths and manifest identifiers are intentionally absent from this port.
    """

    def begin(self, *, category: str, started_at_us: int) -> object: ...

    def complete(
        self,
        token: object,
        *,
        result: "CleanupResult",
        finished_at_us: int,
    ) -> None: ...


def _utc_epoch_microseconds(value: datetime, *, label: str) -> int:
    aware_datetime(value, field_name=label)
    delta = value.astimezone(UTC) - _UTC_EPOCH
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _stored_exact_datetime(value: object, epoch_us: object) -> datetime | None:
    if not isinstance(value, str) or isinstance(epoch_us, bool) or not isinstance(
        epoch_us, int
    ):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        aware_datetime(parsed, field_name="first_seen_at")
        if _utc_epoch_microseconds(parsed, label="first_seen_at") != epoch_us:
            return None
        return parsed.astimezone(UTC)
    except (DomainValidationError, OverflowError, TypeError, ValueError):
        return None


def _calendar_year_later(value: datetime) -> datetime | None:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        try:
            return value.replace(year=value.year + 1, month=2, day=28)
        except ValueError:
            return None


def _exact_retention_expiry_us(first_seen_at: object, first_seen_at_us: object) -> int | None:
    """Return an expiry only from exact first-seen evidence.

    The read-only market-information preview may conservatively estimate a
    first-seen time from ``collected_at``.  That fallback is deliberately not
    accepted as authority for destructive cleanup.
    """

    first_seen = _stored_exact_datetime(first_seen_at, first_seen_at_us)
    if first_seen is None:
        return None
    expires_at = _calendar_year_later(first_seen)
    if expires_at is None:
        return None
    return _utc_epoch_microseconds(expires_at, label="retention_expires_at")


@dataclass(frozen=True)
class FixtureCleanupCapability:
    """Unforgeable-by-path convention for this phase's destructive fixtures."""

    root: Path
    root_device: int
    root_inode: int
    owner_uid: int

    @classmethod
    def issue(cls, root: Path) -> "FixtureCleanupCapability":
        try:
            shared_memory = Path("/dev/shm").resolve(strict=True)
            candidate = root.expanduser()
            if not candidate.is_absolute() or candidate.is_symlink():
                raise ValueError
            resolved = candidate.resolve(strict=True)
            if resolved == shared_memory or not resolved.is_relative_to(shared_memory):
                raise ValueError
            info = candidate.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError
            owner_uid = os.getuid() if os.name != "nt" else info.st_uid
            if os.name != "nt" and (info.st_uid != owner_uid or info.st_mode & 0o077):
                raise ValueError
            marker = candidate / FIXTURE_MARKER_NAME
            marker_info = marker.stat(follow_symlinks=False)
            if (
                marker.is_symlink()
                or not stat.S_ISREG(marker_info.st_mode)
                or marker_info.st_nlink != 1
                or (os.name != "nt" and marker_info.st_uid != owner_uid)
                or marker_info.st_mode & 0o077
                or marker.read_bytes() != FIXTURE_MARKER_CONTENT
            ):
                raise ValueError
            return cls(resolved, info.st_dev, info.st_ino, owner_uid)
        except (OSError, RuntimeError, ValueError):
            raise StorageMaintenanceError(
                "격리된 정리 시험 경계를 안전하게 확인할 수 없습니다."
            ) from None

    def assert_valid(self) -> None:
        try:
            info = self.root.stat(follow_symlinks=False)
            marker = self.root / FIXTURE_MARKER_NAME
            marker_info = marker.stat(follow_symlinks=False)
            if (
                self.root.is_symlink()
                or not stat.S_ISDIR(info.st_mode)
                or info.st_dev != self.root_device
                or info.st_ino != self.root_inode
                or (os.name != "nt" and info.st_uid != self.owner_uid)
                or info.st_mode & 0o077
                or marker.is_symlink()
                or not stat.S_ISREG(marker_info.st_mode)
                or marker_info.st_nlink != 1
                or (os.name != "nt" and marker_info.st_uid != self.owner_uid)
                or marker_info.st_mode & 0o077
                or marker.read_bytes() != FIXTURE_MARKER_CONTENT
            ):
                raise ValueError
        except (OSError, RuntimeError, ValueError):
            raise StorageMaintenanceError(
                "격리된 정리 시험 경계가 변경되어 작업을 중지했습니다."
            ) from None

    def immediate_child(self, path: Path, *, kind: Literal["file", "directory"]) -> Path:
        self.assert_valid()
        try:
            candidate = path.expanduser()
            if not candidate.is_absolute() or candidate.parent != self.root:
                raise ValueError
            info = candidate.stat(follow_symlinks=False)
            if candidate.is_symlink():
                raise ValueError
            if kind == "file" and not stat.S_ISREG(info.st_mode):
                raise ValueError
            if kind == "directory" and not stat.S_ISDIR(info.st_mode):
                raise ValueError
            if os.name != "nt" and info.st_uid != self.owner_uid:
                raise ValueError
            if info.st_mode & 0o077:
                raise ValueError
            if kind == "file" and info.st_nlink != 1:
                raise ValueError
            return candidate
        except (OSError, RuntimeError, ValueError):
            raise StorageMaintenanceError(
                "관리 대상 경계를 안전하게 확인할 수 없습니다."
            ) from None


@dataclass(frozen=True)
class SpaceSnapshot:
    checked_at_us: int
    available_bytes: int
    total_bytes: int
    available_inodes: int
    low_space: bool
    large_operation_allowed: bool
    paper_mutation_allowed: bool
    write_block_latched: bool


class SpaceGate:
    """Conservative, injectable filesystem-space gate for PAPER writes."""

    def __init__(
        self,
        capability: FixtureCleanupCapability,
        *,
        low_space_bytes: int = DEFAULT_LOW_SPACE_BYTES,
        statvfs: Callable[[Path], _StatVFS] = os.statvfs,
    ) -> None:
        if not isinstance(low_space_bytes, int) or low_space_bytes <= 0:
            raise StorageMaintenanceError("저장공간 안전 기준을 확인할 수 없습니다.")
        self.capability = capability
        self.low_space_bytes = low_space_bytes
        self._statvfs = statvfs
        self._write_block_latched = False

    @property
    def write_block_latched(self) -> bool:
        return self._write_block_latched

    def note_write_failure(self, code: str) -> None:
        if code not in {"SQLITE_FULL", "ENOSPC", "IO_ERROR"}:
            raise StorageMaintenanceError("저장 실패 상태 코드를 확인할 수 없습니다.")
        self._write_block_latched = True

    def inspect(
        self,
        *,
        now: datetime,
        planned_write_bytes: int = 0,
    ) -> SpaceSnapshot:
        aware_datetime(now, field_name="space.checked_at")
        if (
            isinstance(planned_write_bytes, bool)
            or not isinstance(planned_write_bytes, int)
            or planned_write_bytes < 0
        ):
            raise StorageMaintenanceError("예상 쓰기 크기를 확인할 수 없습니다.")
        self.capability.assert_valid()
        try:
            values = self._statvfs(self.capability.root)
            available = int(values.f_bavail) * int(values.f_frsize)
            total = int(values.f_blocks) * int(values.f_frsize)
            inodes = int(values.f_favail)
            if min(available, total, inodes) < 0 or available > total:
                raise ValueError
        except (OSError, TypeError, ValueError, OverflowError):
            raise StorageMaintenanceError(
                "저장공간 상태를 안전하게 확인할 수 없습니다."
            ) from None
        # A low-space observation is a safety event, not a transient UI hint.
        # PAPER mutations remain blocked until an explicit integrity and
        # reconciliation check clears the latch.  A merely *planned* large
        # operation is refused without unnecessarily latching ordinary PAPER
        # writes while the filesystem itself is still above the floor.
        low = available < self.low_space_bytes or inodes == 0
        if low:
            self._write_block_latched = True
        large_allowed = not low and (
            available - planned_write_bytes >= self.low_space_bytes
        )
        paper_allowed = not self._write_block_latched and not low
        return SpaceSnapshot(
            checked_at_us=_utc_epoch_microseconds(now, label="space.checked_at"),
            available_bytes=available,
            total_bytes=total,
            available_inodes=inodes,
            low_space=low,
            large_operation_allowed=large_allowed,
            paper_mutation_allowed=paper_allowed,
            write_block_latched=self._write_block_latched,
        )

    def try_recover(
        self,
        *,
        now: datetime,
        integrity_ok: bool,
        reconciliation_ok: bool,
    ) -> bool:
        if not isinstance(integrity_ok, bool) or not isinstance(
            reconciliation_ok, bool
        ):
            raise StorageMaintenanceError("저장소 복구 확인 결과를 확인할 수 없습니다.")
        snapshot = self.inspect(now=now)
        if not (integrity_ok and reconciliation_ok and not snapshot.low_space):
            return False
        self._write_block_latched = False
        return True


@dataclass(frozen=True)
class TemporaryManifestEntry:
    artifact_id: str
    relative_name: str
    state: Literal["ACTIVE", "COMPLETED"]
    completed_at_us: int | None
    device: int
    inode: int


@dataclass(frozen=True)
class BackupManifestEntry:
    backup_id: str
    relative_name: str
    created_at_us: int
    validation_state: Literal["VERIFIED", "FAILED", "UNKNOWN"]
    bundle_format: str
    sha256: str
    byte_size: int
    device: int
    inode: int


@dataclass(frozen=True)
class CleanupResult:
    category: Literal["MARKET_INFORMATION", "TEMPORARY", "BACKUP"]
    status: Literal["OK", "PARTIAL", "BLOCKED", "NOTHING_TO_DO"]
    considered_count: int
    deleted_count: int
    skipped_count: int
    failure_count: int
    reason_codes: tuple[str, ...]


def _safe_relative_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise StorageMaintenanceError("관리 파일 식별자를 확인할 수 없습니다.")
    return value


def _safe_manifest_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(
            not (character.isascii() and (character.isalnum() or character in "-_.:"))
            for character in value
        )
    ):
        raise StorageMaintenanceError("관리 항목 식별자를 확인할 수 없습니다.")
    return value


def _safe_positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StorageMaintenanceError("관리 항목의 파일 식별 정보를 확인할 수 없습니다.")
    return value


class StorageMaintenanceService:
    """Explicit fixture service; never wired to an operating application."""

    def __init__(
        self,
        capability: FixtureCleanupCapability,
        *,
        market_database: Path,
        temporary_directory: Path,
        backup_directory: Path,
        space_gate: SpaceGate,
        audit_recorder: CleanupAuditRecorder,
    ) -> None:
        self.capability = capability
        self.market_database = capability.immediate_child(market_database, kind="file")
        self.temporary_directory = capability.immediate_child(
            temporary_directory, kind="directory"
        )
        self.backup_directory = capability.immediate_child(
            backup_directory, kind="directory"
        )
        self.space_gate = space_gate
        self.audit_recorder = audit_recorder

    def _run_audited(
        self,
        category: Literal["MARKET_INFORMATION", "TEMPORARY", "BACKUP"],
        *,
        now: datetime,
        operation: Callable[[], CleanupResult],
    ) -> CleanupResult:
        started_at_us = _utc_epoch_microseconds(now, label="cleanup.audit.started_at")
        try:
            token = self.audit_recorder.begin(
                category=category, started_at_us=started_at_us
            )
        except Exception:
            raise StorageMaintenanceError(
                "정리 감사 기록을 안전하게 시작할 수 없습니다."
            ) from None
        try:
            result = operation()
            if result.category != category:
                raise StorageMaintenanceError("정리 결과 범주를 확인할 수 없습니다.")
        except Exception:
            failure = CleanupResult(
                category,
                "PARTIAL",
                0,
                0,
                0,
                1,
                ("OPERATION_FAILED",),
            )
            try:
                self.audit_recorder.complete(
                    token,
                    result=failure,
                    finished_at_us=started_at_us,
                )
            except Exception:
                raise StorageMaintenanceError(
                    "정리 실패 감사 기록을 안전하게 완료할 수 없습니다."
                ) from None
            raise
        try:
            self.audit_recorder.complete(
                token,
                result=result,
                finished_at_us=started_at_us,
            )
        except Exception:
            # The durable intent remains incomplete for reconciliation.  A
            # deletion is never reported as successful without its completion
            # audit record.
            raise StorageMaintenanceError(
                "정리 결과 감사 기록을 안전하게 완료할 수 없습니다."
            ) from None
        return result

    @staticmethod
    def _reference_table_available(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (REFERENCE_HOLD_TABLE,),
        ).fetchone()
        if row is None:
            return False
        columns = {
            str(item[1])
            for item in connection.execute(
                f'PRAGMA table_info("{REFERENCE_HOLD_TABLE}")'
            ).fetchall()
        }
        return {"info_id", "active"}.issubset(columns)

    def cleanup_expired_market_information(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> CleanupResult:
        return self._run_audited(
            "MARKET_INFORMATION",
            now=now,
            operation=lambda: self._cleanup_expired_market_information(
                now=now, limit=limit
            ),
        )

    def _cleanup_expired_market_information(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> CleanupResult:
        """Delete an exact-first-seen, unreferenced fixture batch atomically."""

        self.capability.assert_valid()
        self.capability.immediate_child(self.market_database, kind="file")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise StorageMaintenanceError("시장정보 정리 묶음 크기를 확인할 수 없습니다.")
        # Inspection deliberately latches a low-space PAPER write block, but
        # low space must not disable the narrowly scoped cleanup that can help
        # an operator recover.  This fixture-only service is not a general DB
        # write bypass: it can delete only exact-expiry, unreferenced market
        # metadata below the issued capability root.
        self.space_gate.inspect(now=now)
        now_us = _utc_epoch_microseconds(now, label="cleanup.now")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.market_database, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.create_function(
                "wealthy_exact_cleanup_expiry_us", 2, _exact_retention_expiry_us
            )
            connection.execute("BEGIN IMMEDIATE")
            market_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_information'"
            ).fetchone()
            if market_table is None or not self._reference_table_available(connection):
                connection.rollback()
                return CleanupResult(
                    "MARKET_INFORMATION",
                    "BLOCKED",
                    0,
                    0,
                    0,
                    0,
                    ("REFERENCE_REGISTRY_UNAVAILABLE",),
                )
            candidates = connection.execute(
                f"""SELECT item.info_id, item.first_seen_at_us
                      FROM market_information AS item
                      WHERE wealthy_exact_cleanup_expiry_us(
                                item.first_seen_at, item.first_seen_at_us
                            ) IS NOT NULL
                        AND wealthy_exact_cleanup_expiry_us(
                                item.first_seen_at, item.first_seen_at_us
                            ) <= ?
                        AND NOT EXISTS (
                            SELECT 1 FROM {REFERENCE_HOLD_TABLE} AS hold
                            WHERE hold.info_id = item.info_id AND hold.active = 1
                        )
                      ORDER BY item.first_seen_at_us ASC, item.info_id ASC
                      LIMIT ?""",
                (now_us, limit),
            ).fetchall()
            deleted = 0
            for row in candidates:
                cursor = connection.execute(
                    f"""DELETE FROM market_information
                         WHERE info_id=? AND first_seen_at_us=?
                           AND wealthy_exact_cleanup_expiry_us(
                                   first_seen_at, first_seen_at_us
                               ) <= ?
                           AND NOT EXISTS (
                               SELECT 1 FROM {REFERENCE_HOLD_TABLE} AS hold
                               WHERE hold.info_id = market_information.info_id
                                 AND hold.active = 1
                           )""",
                    (row["info_id"], row["first_seen_at_us"], now_us),
                )
                deleted += max(cursor.rowcount, 0)
            connection.commit()
            considered = len(candidates)
            return CleanupResult(
                "MARKET_INFORMATION",
                "OK" if deleted else "NOTHING_TO_DO",
                considered,
                deleted,
                considered - deleted,
                0,
                (),
            )
        except sqlite3.Error:
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            raise StorageMaintenanceError(
                "시장정보 정리를 안전하게 완료할 수 없습니다."
            ) from None
        finally:
            if connection is not None:
                connection.close()

    def _open_managed_directory(self, directory: Path) -> int:
        self.capability.assert_valid()
        checked = self.capability.immediate_child(directory, kind="directory")
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(checked, flags)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or (os.name != "nt" and info.st_uid != self.capability.owner_uid)
                or info.st_mode & 0o077
            ):
                os.close(descriptor)
                raise ValueError
            return descriptor
        except (OSError, ValueError):
            raise StorageMaintenanceError(
                "관리 폴더를 안전하게 확인할 수 없습니다."
            ) from None

    @staticmethod
    def _hash_descriptor(descriptor: int) -> tuple[str, int]:
        digest = hashlib.sha256()
        total = 0
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            digest.update(block)
        return digest.hexdigest(), total

    def _verify_registered_file(
        self,
        directory_descriptor: int,
        *,
        relative_name: str,
        expected_device: int,
        expected_inode: int,
        expected_sha256: str,
        expected_size: int,
    ) -> tuple[bool, str | None]:
        name = _safe_relative_name(relative_name)
        descriptor: int | None = None
        try:
            before = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_dev != expected_device
                or before.st_ino != expected_inode
                or before.st_nlink != 1
                or before.st_size != expected_size
                or (os.name != "nt" and before.st_uid != self.capability.owner_uid)
                or before.st_mode & 0o077
            ):
                return False, "FILE_IDENTITY_MISMATCH"
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(name, flags, dir_fd=directory_descriptor)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return False, "FILE_IDENTITY_MISMATCH"
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return False, "FILE_IN_USE"
            digest, size = self._hash_descriptor(descriptor)
            current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                return False, "FILE_IDENTITY_MISMATCH"
            if digest != expected_sha256 or size != expected_size:
                return False, "FILE_INTEGRITY_MISMATCH"
            return True, None
        except FileNotFoundError:
            return False, "FILE_MISSING"
        except OSError:
            return False, "FILE_OPERATION_FAILED"
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)

    def _unlink_registered_file(
        self,
        directory_descriptor: int,
        *,
        relative_name: str,
        expected_device: int,
        expected_inode: int,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
    ) -> tuple[bool, str | None]:
        name = _safe_relative_name(relative_name)
        descriptor: int | None = None
        try:
            before = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_dev != expected_device
                or before.st_ino != expected_inode
                or before.st_nlink != 1
                or (os.name != "nt" and before.st_uid != self.capability.owner_uid)
                or before.st_mode & 0o077
            ):
                return False, "FILE_IDENTITY_MISMATCH"
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(name, flags, dir_fd=directory_descriptor)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return False, "FILE_IDENTITY_MISMATCH"
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False, "FILE_IN_USE"
            if expected_sha256 is not None:
                digest, size = self._hash_descriptor(descriptor)
                if digest != expected_sha256 or size != expected_size:
                    return False, "FILE_INTEGRITY_MISMATCH"
            current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                return False, "FILE_IDENTITY_MISMATCH"
            os.unlink(name, dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
            return True, None
        except FileNotFoundError:
            return False, "FILE_MISSING"
        except OSError:
            return False, "FILE_OPERATION_FAILED"
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)

    def cleanup_temporary_files(
        self,
        entries: Iterable[TemporaryManifestEntry],
        *,
        now: datetime,
    ) -> CleanupResult:
        prepared = tuple(entries)
        return self._run_audited(
            "TEMPORARY",
            now=now,
            operation=lambda: self._cleanup_temporary_files(prepared, now=now),
        )

    def _cleanup_temporary_files(
        self,
        entries: tuple[TemporaryManifestEntry, ...],
        *,
        now: datetime,
    ) -> CleanupResult:
        now_us = _utc_epoch_microseconds(now, label="cleanup.now")
        prepared = entries
        seen_ids: set[str] = set()
        seen_files: set[tuple[int, int]] = set()
        for entry in prepared:
            artifact_id = _safe_manifest_identifier(entry.artifact_id)
            name = _safe_relative_name(entry.relative_name)
            device = _safe_positive_integer(entry.device)
            inode = _safe_positive_integer(entry.inode)
            if entry.state not in {"ACTIVE", "COMPLETED"}:
                raise StorageMaintenanceError("임시파일 관리 상태를 확인할 수 없습니다.")
            if (
                entry.completed_at_us is not None
                and (
                    isinstance(entry.completed_at_us, bool)
                    or not isinstance(entry.completed_at_us, int)
                    or entry.completed_at_us < 0
                )
            ):
                raise StorageMaintenanceError("임시파일 완료 시각을 확인할 수 없습니다.")
            if entry.state == "ACTIVE" and entry.completed_at_us is not None:
                raise StorageMaintenanceError("임시파일 관리 상태를 확인할 수 없습니다.")
            if artifact_id in seen_ids or (device, inode) in seen_files:
                return CleanupResult(
                    "TEMPORARY",
                    "BLOCKED",
                    len(prepared),
                    0,
                    len(prepared),
                    0,
                    ("MANIFEST_DUPLICATE",),
                )
            seen_ids.add(artifact_id)
            seen_files.add((device, inode))
            # Force validation before a directory descriptor is opened.
            del name
        descriptor = self._open_managed_directory(self.temporary_directory)
        deleted = 0
        skipped = 0
        failed = 0
        reasons: set[str] = set()
        try:
            for entry in prepared:
                if (
                    entry.state != "COMPLETED"
                    or entry.completed_at_us is None
                    or entry.completed_at_us > now_us - TEMPORARY_RETENTION_MICROSECONDS
                ):
                    skipped += 1
                    reasons.add("NOT_COMPLETED_OR_TOO_NEW")
                    continue
                removed, reason = self._unlink_registered_file(
                    descriptor,
                    relative_name=entry.relative_name,
                    expected_device=entry.device,
                    expected_inode=entry.inode,
                )
                if removed:
                    deleted += 1
                else:
                    failed += 1
                    reasons.add(reason or "FILE_OPERATION_FAILED")
        finally:
            os.close(descriptor)
        status: Literal["OK", "PARTIAL", "BLOCKED", "NOTHING_TO_DO"]
        if failed:
            status = "PARTIAL"
        elif deleted:
            status = "OK"
        else:
            status = "NOTHING_TO_DO"
        return CleanupResult(
            "TEMPORARY",
            status,
            len(prepared),
            deleted,
            skipped,
            failed,
            tuple(sorted(reasons)),
        )

    def cleanup_backups(
        self,
        entries: Iterable[BackupManifestEntry],
        *,
        keep_count: int | None,
        newly_verified_backup_id: str,
        now: datetime,
    ) -> CleanupResult:
        prepared = tuple(entries)
        return self._run_audited(
            "BACKUP",
            now=now,
            operation=lambda: self._cleanup_backups(
                prepared,
                keep_count=keep_count,
                newly_verified_backup_id=newly_verified_backup_id,
            ),
        )

    def _cleanup_backups(
        self,
        entries: tuple[BackupManifestEntry, ...],
        *,
        keep_count: int | None,
        newly_verified_backup_id: str,
    ) -> CleanupResult:
        prepared = entries
        if keep_count is None:
            return CleanupResult(
                "BACKUP",
                "BLOCKED",
                len(prepared),
                0,
                len(prepared),
                0,
                ("RETENTION_POLICY_UNSET",),
            )
        if not isinstance(keep_count, int) or isinstance(keep_count, bool) or not 1 <= keep_count <= 100:
            raise StorageMaintenanceError("백업 보존 개수를 확인할 수 없습니다.")
        _safe_manifest_identifier(newly_verified_backup_id)
        descriptor = self._open_managed_directory(self.backup_directory)
        valid: list[BackupManifestEntry] = []
        reasons: set[str] = set()
        try:
            seen_ids: set[str] = set()
            seen_files: set[tuple[int, int]] = set()
            for entry in prepared:
                try:
                    backup_id = _safe_manifest_identifier(entry.backup_id)
                    name = _safe_relative_name(entry.relative_name)
                    device = _safe_positive_integer(entry.device)
                    inode = _safe_positive_integer(entry.inode)
                    if (
                        isinstance(entry.created_at_us, bool)
                        or not isinstance(entry.created_at_us, int)
                        or entry.created_at_us < 0
                        or isinstance(entry.byte_size, bool)
                        or not isinstance(entry.byte_size, int)
                    ):
                        raise ValueError
                    if backup_id in seen_ids or (device, inode) in seen_files:
                        reasons.add("MANIFEST_DUPLICATE")
                        raise ValueError
                    seen_ids.add(backup_id)
                    seen_files.add((device, inode))
                    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    structurally_valid = (
                        entry.validation_state == "VERIFIED"
                        and entry.bundle_format in _ALLOWED_BACKUP_FORMATS
                        and name.endswith(".wbackup")
                        and len(entry.sha256) == 64
                        and all(character in "0123456789abcdef" for character in entry.sha256)
                        and entry.byte_size > 0
                        and stat.S_ISREG(info.st_mode)
                        and info.st_dev == entry.device
                        and info.st_ino == entry.inode
                        and info.st_nlink == 1
                        and info.st_size == entry.byte_size
                        and (os.name == "nt" or info.st_uid == self.capability.owner_uid)
                        and not (info.st_mode & 0o077)
                    )
                    if not structurally_valid:
                        raise ValueError
                    verified, reason = self._verify_registered_file(
                        descriptor,
                        relative_name=name,
                        expected_device=device,
                        expected_inode=inode,
                        expected_sha256=entry.sha256,
                        expected_size=entry.byte_size,
                    )
                    if not verified:
                        reasons.add(reason or "FILE_OPERATION_FAILED")
                        raise ValueError
                    valid.append(entry)
                except (OSError, StorageMaintenanceError, ValueError):
                    reasons.add("UNVERIFIED_OR_UNSAFE_BACKUP")
            if len(valid) != len(prepared):
                return CleanupResult(
                    "BACKUP",
                    "BLOCKED",
                    len(prepared),
                    0,
                    len(prepared),
                    0,
                    tuple(sorted(reasons | {"MANIFEST_INTEGRITY_FAILED"})),
                )
            new_entry = next(
                (
                    entry
                    for entry in valid
                    if entry.backup_id == newly_verified_backup_id
                ),
                None,
            )
            if new_entry is None:
                return CleanupResult(
                    "BACKUP",
                    "BLOCKED",
                    len(prepared),
                    0,
                    len(prepared),
                    0,
                    tuple(sorted(reasons | {"NEW_BACKUP_NOT_VERIFIED"})),
                )
            valid.sort(key=lambda entry: (entry.created_at_us, entry.backup_id), reverse=True)
            if new_entry not in valid[:keep_count]:
                return CleanupResult(
                    "BACKUP",
                    "BLOCKED",
                    len(prepared),
                    0,
                    len(prepared),
                    0,
                    ("NEW_BACKUP_NOT_CURRENT",),
                )
            candidates = valid[keep_count:]
            deleted = 0
            failed = 0
            for entry in candidates:
                removed, reason = self._unlink_registered_file(
                    descriptor,
                    relative_name=entry.relative_name,
                    expected_device=entry.device,
                    expected_inode=entry.inode,
                    expected_sha256=entry.sha256,
                    expected_size=entry.byte_size,
                )
                if removed:
                    deleted += 1
                else:
                    failed += 1
                    reasons.add(reason or "FILE_OPERATION_FAILED")
            skipped = len(prepared) - len(candidates)
            if failed:
                status: Literal["OK", "PARTIAL", "BLOCKED", "NOTHING_TO_DO"] = "PARTIAL"
            elif deleted:
                status = "OK"
            else:
                status = "NOTHING_TO_DO"
            return CleanupResult(
                "BACKUP",
                status,
                len(prepared),
                deleted,
                skipped,
                failed,
                tuple(sorted(reasons)),
            )
        finally:
            os.close(descriptor)
