"""Authenticated backup and restore for the localhost SQLite runtime.

The V1 contract keeps the market-data and web-state pair unchanged.  The
separate V2 contract adds PAPER state under an explicit cooperating-writer
barrier.  SQLite's online backup API validates every snapshot in memory before
the encrypted bundle is published.  No plaintext backup file is created, and
restore always targets an empty explicit directory rather than an operating
database.
"""

from __future__ import annotations

import base64
import hmac
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import struct
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from asset_assistant.cli import _SafeArgumentParser
from asset_assistant.credential_store import CredentialStoreError, KeyringBackend
from asset_assistant.domain import DomainValidationError
from wealthy_server.local_credentials import LOCAL_SECRET_SERVICE, LocalSecretStore


OUTER_MAGIC = b"WEALTHY-LOCAL-SQLITE-AES256-GCM-V1\n"
INNER_MAGIC = b"WEALTHY-LOCAL-SQLITE-BUNDLE-V1\n"
OUTER_MAGIC_V2 = b"WEALTHY-LOCAL-SQLITE-AES256-GCM-V2\n"
INNER_MAGIC_V2 = b"WEALTHY-LOCAL-SQLITE-BUNDLE-V2\n"
BACKUP_KEY_ITEM = "sqlite-backup-key-v1"
NONCE_BYTES = 12
KEY_BYTES = 32
MAX_HEADER_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
ENTRY_NAMES = ("market-data.sqlite3", "web-state.sqlite3")
V2_ENTRY_NAMES = (
    "market-data.sqlite3",
    "web-state.sqlite3",
    "paper-state.sqlite3",
)
_V2_SCHEMAS = (
    frozenset({"MARKET_DATA_V2", "MARKET_DATA_V3"}),
    frozenset({"LOCAL_WEB_STATE_V1"}),
    frozenset({"PAPER_STATE_V1"}),
)
_QUIESCE_SENTINEL = object()
_RECOVERY_POINT_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class LocalBackupError(DomainValidationError):
    """A path- and content-redacted local backup failure."""


class BackupQuiesceProof:
    """An active *fixture-only* writer-barrier capability for a V2 snapshot.

    Production writers for all three databases are not yet wired to one shared
    barrier.  Consequently this capability is deliberately available only
    through the explicit ``trusted_test_barrier=True`` fixture injection.  It
    must not be represented as an operational recovery point.  The V2 API
    refuses a missing, expired, inherited, or path-replaced capability.
    """

    __slots__ = (
        "_active",
        "_descriptor",
        "_device",
        "_inode",
        "_owner_pid",
        "_sentinel",
        "barrier_mode",
        "quiesced_at_utc_us",
        "recovery_point_id",
    )

    def __init__(
        self,
        *,
        descriptor: int,
        device: int,
        inode: int,
        quiesced_at_utc_us: int,
        recovery_point_id: str,
        sentinel: object,
    ) -> None:
        if sentinel is not _QUIESCE_SENTINEL:
            raise LocalBackupError("백업 쓰기 중지 상태를 확인할 수 없습니다.")
        self._active = True
        self._descriptor = descriptor
        self._device = device
        self._inode = inode
        self._owner_pid = os.getpid()
        self._sentinel = sentinel
        self.barrier_mode = "TRUSTED_TEST_FIXTURE_ONLY"
        self.quiesced_at_utc_us = quiesced_at_utc_us
        self.recovery_point_id = recovery_point_id

    def _assert_active(self) -> None:
        try:
            info = os.fstat(self._descriptor)
            if (
                self._sentinel is not _QUIESCE_SENTINEL
                or not self._active
                or self._owner_pid != os.getpid()
                or self.barrier_mode != "TRUSTED_TEST_FIXTURE_ONLY"
                or not stat.S_ISREG(info.st_mode)
                or (info.st_dev, info.st_ino) != (self._device, self._inode)
            ):
                raise OSError
        except (OSError, ValueError):
            raise LocalBackupError(
                "백업 쓰기 중지 상태를 확인할 수 없습니다."
            ) from None


def _open_coordination_lock(path: Path) -> int:
    if os.name == "nt":
        raise LocalBackupError("현재 백업 조정 잠금은 Ubuntu에서만 지원합니다.")
    try:
        candidate = path.expanduser()
        if not candidate.is_absolute():
            raise ValueError
        parent = candidate.parent.resolve(strict=True)
        if parent != candidate.parent:
            raise ValueError
        parent_info = parent.stat(follow_symlinks=False)
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid():
            raise ValueError
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(descriptor)
            raise ValueError
        os.fchmod(descriptor, 0o600)
        return descriptor
    except (OSError, RuntimeError, ValueError):
        raise LocalBackupError(
            "백업 조정 잠금을 안전하게 사용할 수 없습니다. 경로는 표시하지 않습니다."
        ) from None


@contextmanager
def coordinated_backup_writer(lock_path: Path) -> Iterator[None]:
    """Fixture primitive for a mutation participating in the test barrier.

    This does not certify that the production market, web, and PAPER writers
    use the lock; operational V2 remains unavailable until they do.
    """

    import fcntl

    descriptor = _open_coordination_lock(lock_path)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            raise LocalBackupError(
                "일관된 백업을 위해 새 데이터 변경이 일시 중지되었습니다."
            ) from None
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


@contextmanager
def backup_quiesce(
    lock_path: Path,
    *,
    trusted_test_barrier: bool = False,
) -> Iterator[BackupQuiesceProof]:
    """Issue a fixture-only V2 proof; operational use fails closed."""

    if trusted_test_barrier is not True:
        raise LocalBackupError(
            "세 데이터베이스의 운영 쓰기 중지 연동이 완료되지 않아 V2 백업을 시작할 수 없습니다."
        )

    import fcntl

    descriptor = _open_coordination_lock(lock_path)
    proof: BackupQuiesceProof | None = None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            raise LocalBackupError(
                "데이터 변경 작업이 진행 중이어서 일관된 백업을 시작할 수 없습니다."
            ) from None
        info = os.fstat(descriptor)
        proof = BackupQuiesceProof(
            descriptor=descriptor,
            device=info.st_dev,
            inode=info.st_ino,
            quiesced_at_utc_us=time.time_ns() // 1_000,
            recovery_point_id=uuid.uuid4().hex,
            sentinel=_QUIESCE_SENTINEL,
        )
        yield proof
    finally:
        if proof is not None:
            proof._active = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def load_or_create_backup_key(
    backend: KeyringBackend | None = None,
    *,
    trusted_test_backend: bool = False,
) -> bytes:
    """Load a dedicated backup key from the existing OS security boundary."""

    try:
        secure_backend = LocalSecretStore(
            backend,
            trusted_test_backend=trusted_test_backend,
        ).backend()
        encoded = secure_backend.get_password(LOCAL_SECRET_SERVICE, BACKUP_KEY_ITEM)
        if encoded is None:
            key = os.urandom(KEY_BYTES)
            encoded = base64.urlsafe_b64encode(key).decode("ascii")
            secure_backend.set_password(LOCAL_SECRET_SERVICE, BACKUP_KEY_ITEM, encoded)
            if (
                secure_backend.get_password(LOCAL_SECRET_SERVICE, BACKUP_KEY_ITEM)
                != encoded
            ):
                raise LocalBackupError("보안 저장소 쓰기 결과를 확인할 수 없습니다.")
        if not isinstance(encoded, str) or len(encoded) > 256:
            raise ValueError
        key = base64.b64decode(
            encoded.strip().encode("ascii"), altchars=b"-_", validate=True
        )
        if len(key) != KEY_BYTES:
            raise ValueError
        return key
    except LocalBackupError:
        raise
    except (CredentialStoreError, OSError, RuntimeError, TypeError, ValueError):
        raise LocalBackupError(
            "운영체제 보안 저장소의 백업 키를 사용할 수 없습니다. 평문으로 대체하지 않습니다."
        ) from None


def _safe_existing_file(path: Path, *, label: str) -> Path:
    del label
    try:
        candidate = path.expanduser()
        if not candidate.is_absolute():
            raise ValueError
        info = candidate.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError
        if os.name != "nt" and info.st_uid != os.getuid():
            raise ValueError
        resolved = candidate.resolve(strict=True)
        if resolved != candidate:
            raise ValueError
        return resolved
    except (OSError, RuntimeError, ValueError):
        raise LocalBackupError(
            "로컬 데이터 파일을 안전하게 확인할 수 없습니다. 전체 경로는 표시하지 않습니다."
        ) from None


def _safe_output(path: Path) -> Path:
    try:
        candidate = path.expanduser()
        if not candidate.is_absolute() or candidate.exists() or candidate.is_symlink():
            raise ValueError
        parent = candidate.parent.resolve(strict=True)
        if parent != candidate.parent:
            raise ValueError
        info = parent.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError
        if os.name != "nt" and info.st_uid != os.getuid():
            raise ValueError
        return candidate
    except (OSError, RuntimeError, ValueError):
        raise LocalBackupError(
            "백업 출력 위치를 안전하게 사용할 수 없습니다. 전체 경로는 표시하지 않습니다."
        ) from None


def _schema_kind(connection: sqlite3.Connection) -> str:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if {"schema_meta", "market_records", "job_runs"}.issubset(tables):
        row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None or int(row[0]) not in {2, 3}:
            raise LocalBackupError("시장자료 SQLite 스키마 버전을 확인할 수 없습니다.")
        version = int(row[0])
        if version == 3 and not {
            "market_information",
            "market_information_provider_state",
            "market_information_refresh_claims",
        }.issubset(tables):
            raise LocalBackupError("시장자료 SQLite v3 확장 구조를 확인할 수 없습니다.")
        return f"MARKET_DATA_V{version}"
    if {"users", "web_sessions", "migration_ledger"}.issubset(tables):
        row = connection.execute(
            "SELECT checksum_sha256 FROM migration_ledger "
            "WHERE revision = 'local-web-state-v1'"
        ).fetchone()
        if row is None or not isinstance(row[0], str) or len(row[0]) != 64:
            raise LocalBackupError("로컬 웹 SQLite 스키마 이력을 확인할 수 없습니다.")
        return "LOCAL_WEB_STATE_V1"
    if "paper_schema_ledger" in tables:
        try:
            from asset_assistant.paper_persistence import (
                PAPER_SCHEMA_VERSION,
                validate_paper_database_connection,
            )

            # Schema presence alone is not a restorable PAPER backup.  The
            # public read-only validator also checks the exact 21-field
            # projection, revision chain, guards, receipts, owner binding, and
            # state graph for every aggregate.
            validate_paper_database_connection(connection)
            if PAPER_SCHEMA_VERSION != 1:
                raise LocalBackupError(
                    "PAPER SQLite 스키마 버전을 확인할 수 없습니다."
                )
            return "PAPER_STATE_V1"
        except LocalBackupError:
            raise
        except (DomainValidationError, ImportError, AttributeError, sqlite3.Error):
            raise LocalBackupError(
                "PAPER SQLite 스키마를 안전하게 확인할 수 없습니다."
            ) from None
    raise LocalBackupError("지원되는 SQLite 스키마가 아닙니다.")


def _snapshot(path: Path) -> tuple[bytes, str, dict[str, int]]:
    source: sqlite3.Connection | None = None
    memory: sqlite3.Connection | None = None
    try:
        uri = f"file:{quote(path.as_posix(), safe='/')}?mode=ro"
        source = sqlite3.connect(uri, uri=True, timeout=5.0)
        source.execute("PRAGMA query_only=ON")
        memory = sqlite3.connect(":memory:")
        source.backup(memory)
        integrity = memory.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise LocalBackupError("SQLite 스냅샷 무결성 검사에 실패했습니다.")
        if memory.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise LocalBackupError("SQLite 스냅샷 참조 무결성 검사에 실패했습니다.")
        kind = _schema_kind(memory)
        counts: dict[str, int] = {}
        for (table,) in memory.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            quoted = '"' + str(table).replace('"', '""') + '"'
            counts[str(table)] = int(
                memory.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
            )
        # The online source may use WAL.  sqlite3.deserialize() cannot reopen a
        # serialized image whose header still requests an external WAL file.
        # backup() has already copied a consistent, checkpointed image, so mark
        # that self-contained image as rollback-journal format (header bytes
        # 18/19) before encryption.  Database pages and source files are not
        # changed.
        serialized = bytearray(memory.serialize())
        if len(serialized) < 100 or serialized[:16] != b"SQLite format 3\x00":
            raise LocalBackupError("SQLite 스냅샷 헤더를 확인할 수 없습니다.")
        serialized[18] = 1
        serialized[19] = 1
        payload = bytes(serialized)
        if not payload or len(payload) > MAX_BUNDLE_BYTES:
            raise LocalBackupError("SQLite 스냅샷 크기를 안전하게 처리할 수 없습니다.")
        return payload, kind, counts
    except LocalBackupError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
        raise LocalBackupError("SQLite 스냅샷을 안전하게 만들 수 없습니다.") from None
    finally:
        if source is not None:
            source.close()
        if memory is not None:
            memory.close()


def _build_bundle(entries: list[tuple[str, bytes, str, dict[str, int]]]) -> bytes:
    header_entries: list[dict[str, object]] = []
    for name, payload, schema, counts in entries:
        header_entries.append(
            {
                "name": name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "schema": schema,
                "table_counts": counts,
            }
        )
    header = json.dumps(
        {
            "format": "WEALTHY-LOCAL-SQLITE-BUNDLE-V1",
            "created_at": datetime.now(UTC).isoformat(),
            "entries": header_entries,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(header) > MAX_HEADER_BYTES:
        raise LocalBackupError("백업 메타데이터 크기가 허용 범위를 벗어났습니다.")
    bundle = INNER_MAGIC + struct.pack(">I", len(header)) + header
    bundle += b"".join(payload for _, payload, _, _ in entries)
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise LocalBackupError("백업 크기가 허용 범위를 벗어났습니다.")
    return bundle


def backup_local_databases(
    market_database: Path,
    web_state_database: Path,
    output: Path,
    key: bytes,
) -> dict[str, object]:
    """Create an encrypted, non-overwriting bundle of both runtime DBs."""

    if len(key) != KEY_BYTES:
        raise LocalBackupError("백업 키 형식이 올바르지 않습니다.")
    market = _safe_existing_file(market_database, label="market")
    web_state = _safe_existing_file(web_state_database, label="web")
    destination = _safe_output(output)
    if market == web_state or destination in {market, web_state}:
        raise LocalBackupError("백업 입출력 파일을 서로 분리해야 합니다.")

    market_payload, market_schema, market_counts = _snapshot(market)
    web_payload, web_schema, web_counts = _snapshot(web_state)
    bundle = _build_bundle(
        [
            (ENTRY_NAMES[0], market_payload, market_schema, market_counts),
            (ENTRY_NAMES[1], web_payload, web_schema, web_counts),
        ]
    )
    nonce = os.urandom(NONCE_BYTES)
    encrypted = AESGCM(key).encrypt(nonce, bundle, OUTER_MAGIC)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{destination.name}.",
            suffix=".partial",
            dir=destination.parent,
            delete=False,
        ) as sink:
            temporary = Path(sink.name)
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            sink.write(OUTER_MAGIC)
            sink.write(nonce)
            sink.write(encrypted)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(temporary, destination)
        temporary = None
        if os.name != "nt":
            os.chmod(destination, 0o600)
        raw = destination.read_bytes()
        return {
            "status": "OK",
            "format": "WEALTHY-LOCAL-SQLITE-AES256-GCM-V1",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "entries": list(ENTRY_NAMES),
            "plaintext_backup_written": False,
            "source_replaced": False,
        }
    except (OSError, RuntimeError):
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise LocalBackupError("암호화 SQLite 백업을 안전하게 저장할 수 없습니다.") from None


def _assert_private_distinct_v2_sources(paths: tuple[Path, ...]) -> None:
    try:
        identities: set[tuple[int, int]] = set()
        for path in paths:
            info = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or path.is_symlink()
                or (os.name != "nt" and info.st_uid != os.getuid())
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600)
            ):
                raise ValueError
            identity = (info.st_dev, info.st_ino)
            if identity in identities:
                raise ValueError
            identities.add(identity)
    except (OSError, RuntimeError, ValueError):
        raise LocalBackupError(
            "V2 백업 원본 파일을 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다."
        ) from None


def _validate_v2_owner_binding(
    market: sqlite3.Connection,
    web_state: sqlite3.Connection,
    paper: sqlite3.Connection,
) -> None:
    del market
    try:
        web_owners = web_state.execute("SELECT id FROM users LIMIT 2").fetchall()
        paper_owners = paper.execute(
            "SELECT owner_id,binding_hash FROM paper_owner_bindings LIMIT 2"
        ).fetchall()
        if len(web_owners) != 1 or len(paper_owners) != 1:
            raise ValueError
        web_owner = web_owners[0][0]
        paper_owner = paper_owners[0][0]
        if not isinstance(web_owner, str) or not isinstance(paper_owner, str):
            raise ValueError
        if not hmac.compare_digest(web_owner.encode("utf-8"), paper_owner.encode("utf-8")):
            raise ValueError
        expected_binding_hash = hashlib.sha256(
            ("paper-owner-binding-v1\0" + paper_owner).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(
            str(paper_owners[0][1]), expected_binding_hash
        ):
            raise ValueError
        # The PAPER schema deliberately stores the portfolio inside the
        # aggregate projection rather than in a ``paper_portfolios`` table.
        # Check every actual owner-scoped table so a database built with
        # foreign-key enforcement disabled cannot be accepted merely because
        # its singleton binding happens to match the web owner.
        paper_tables = tuple(
            str(row[0])
            for row in paper.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'paper_%' ORDER BY name"
            )
        )
        for table in paper_tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = {
                str(row[1]) for row in paper.execute(f"PRAGMA table_info({quoted})")
            }
            if "owner_id" not in columns:
                continue
            foreign_owner = paper.execute(
                f"SELECT 1 FROM {quoted} "
                "WHERE owner_id IS NULL OR owner_id <> ? LIMIT 1",
                (paper_owner,),
            ).fetchone()
            if foreign_owner is not None:
                raise ValueError
    except (sqlite3.Error, TypeError, UnicodeError, ValueError):
        raise LocalBackupError(
            "V2 백업의 로컬 사용자 소유권 연결을 확인할 수 없습니다."
        ) from None


def _validate_v2_snapshot_set(
    entries: list[tuple[str, bytes, str, dict[str, int], int, int]],
) -> None:
    validated: list[sqlite3.Connection] = []
    try:
        if tuple(item[0] for item in entries) != V2_ENTRY_NAMES:
            raise LocalBackupError("V2 백업 파일 목록이 올바르지 않습니다.")
        for index, (_, payload, schema, described_counts, _, _) in enumerate(entries):
            if schema not in _V2_SCHEMAS[index]:
                raise LocalBackupError("V2 백업 SQLite 역할이 올바르지 않습니다.")
            connection, counts = _validate_payload(payload, schema)
            if counts != described_counts:
                connection.close()
                raise LocalBackupError("V2 백업 SQLite 행 수가 일치하지 않습니다.")
            validated.append(connection)
        _validate_v2_owner_binding(*validated)
    finally:
        for connection in validated:
            connection.close()


def _build_bundle_v2(
    entries: list[tuple[str, bytes, str, dict[str, int], int, int]],
    quiesce: BackupQuiesceProof,
) -> bytes:
    quiesce._assert_active()
    header_entries: list[dict[str, object]] = []
    for name, payload, schema, counts, started_at, finished_at in entries:
        if started_at < quiesce.quiesced_at_utc_us or finished_at < started_at:
            raise LocalBackupError("V2 백업 복구 지점 시각이 올바르지 않습니다.")
        header_entries.append(
            {
                "name": name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "schema": schema,
                "table_counts": counts,
                "snapshot_started_at_utc_us": started_at,
                "snapshot_finished_at_utc_us": finished_at,
            }
        )
    header = json.dumps(
        {
            "format": "WEALTHY-LOCAL-SQLITE-BUNDLE-V2",
            "created_at_utc_us": time.time_ns() // 1_000,
            "recovery_point": {
                "id": quiesce.recovery_point_id,
                "mode": "TRUSTED_TEST_FIXTURE_WRITER_BARRIER",
                "quiesced_at_utc_us": quiesce.quiesced_at_utc_us,
            },
            "entries": header_entries,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(header) > MAX_HEADER_BYTES:
        raise LocalBackupError("V2 백업 메타데이터 크기가 허용 범위를 벗어났습니다.")
    bundle = INNER_MAGIC_V2 + struct.pack(">I", len(header)) + header
    bundle += b"".join(item[1] for item in entries)
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise LocalBackupError("V2 백업 크기가 허용 범위를 벗어났습니다.")
    return bundle


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_v2_encrypted_bundle(
    destination: Path,
    bundle: bytes,
    key: bytes,
) -> tuple[int, str]:
    nonce = os.urandom(NONCE_BYTES)
    encrypted = AESGCM(key).encrypt(nonce, bundle, OUTER_MAGIC_V2)
    raw = OUTER_MAGIC_V2 + nonce + encrypted
    temporary: Path | None = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{destination.name}.",
            suffix=".partial",
            dir=destination.parent,
            delete=False,
        ) as sink:
            temporary = Path(sink.name)
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            sink.write(raw)
            sink.flush()
            os.fsync(sink.fileno())
        # link() is an atomic no-replace publication on the same filesystem.
        # Unlike os.replace(), it cannot overwrite a file created after the
        # preflight check.
        os.link(temporary, destination, follow_symlinks=False)
        published = True
        temporary.unlink()
        temporary = None
        if os.name != "nt":
            os.chmod(destination, 0o600)
        _fsync_directory(destination.parent)
        return len(raw), hashlib.sha256(raw).hexdigest()
    except (OSError, RuntimeError, ValueError):
        if published:
            try:
                destination.unlink()
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise LocalBackupError("암호화 V2 백업을 안전하게 저장할 수 없습니다.") from None


def backup_local_databases_v2(
    market_database: Path,
    web_state_database: Path,
    paper_state_database: Path,
    output: Path,
    key: bytes,
    *,
    quiesce: BackupQuiesceProof | None,
) -> dict[str, object]:
    """Create a fixture V2 bundle under an injected test writer barrier.

    This function must run in a dedicated backup process.  Calling it inside a
    process that already owns live SQLite connections would violate the POSIX
    lock lifetime protected by the X1 regression.  It is not operationally
    enabled until all three production writer paths share the barrier.
    """

    if len(key) != KEY_BYTES:
        raise LocalBackupError("백업 키 형식이 올바르지 않습니다.")
    if not isinstance(quiesce, BackupQuiesceProof):
        raise LocalBackupError("V2 백업에는 검증된 쓰기 중지 상태가 필요합니다.")
    quiesce._assert_active()
    market = _safe_existing_file(market_database, label="market")
    web_state = _safe_existing_file(web_state_database, label="web")
    paper_state = _safe_existing_file(paper_state_database, label="paper")
    sources = (market, web_state, paper_state)
    _assert_private_distinct_v2_sources(sources)
    destination = _safe_output(output)
    if destination in set(sources):
        raise LocalBackupError("백업 입출력 파일을 서로 분리해야 합니다.")

    entries: list[tuple[str, bytes, str, dict[str, int], int, int]] = []
    for name, source in zip(V2_ENTRY_NAMES, sources, strict=True):
        quiesce._assert_active()
        started_at = time.time_ns() // 1_000
        payload, schema, counts = _snapshot(source)
        finished_at = time.time_ns() // 1_000
        entries.append((name, payload, schema, counts, started_at, finished_at))
    quiesce._assert_active()
    _validate_v2_snapshot_set(entries)
    bundle = _build_bundle_v2(entries, quiesce)
    byte_count, digest = _write_v2_encrypted_bundle(destination, bundle, key)
    try:
        quiesce._assert_active()
        validated = validate_local_backup_v2(destination, key)
        if (
            validated.get("recovery_point_id") != quiesce.recovery_point_id
            or validated.get("entries") != list(V2_ENTRY_NAMES)
        ):
            raise LocalBackupError("V2 백업 게시 결과를 확인할 수 없습니다.")
    except Exception as exc:
        # ``destination`` can only have been created by this invocation: the
        # no-replace publication above rejects pre-existing targets.  Never
        # leave an apparently complete artifact after read-back validation
        # fails.
        try:
            destination.unlink()
            _fsync_directory(destination.parent)
        except OSError:
            pass
        if isinstance(exc, LocalBackupError):
            raise
        raise LocalBackupError("V2 백업 게시 결과를 확인할 수 없습니다.") from None
    return {
        "status": "OK",
        "format": "WEALTHY-LOCAL-SQLITE-AES256-GCM-V2",
        "bytes": byte_count,
        "sha256": digest,
        "entries": list(V2_ENTRY_NAMES),
        "recovery_point_id": quiesce.recovery_point_id,
        "consistency": "FIXTURE_QUIESCED_NOT_OPERATIONAL",
        "operational_recovery_point": False,
        "plaintext_backup_written": False,
        "source_replaced": False,
    }


def _decrypt_bundle(path: Path, key: bytes) -> tuple[dict[str, object], dict[str, bytes]]:
    source = _safe_existing_file(path, label="backup")
    try:
        raw = source.read_bytes()
        minimum = len(OUTER_MAGIC) + NONCE_BYTES + 16
        if len(raw) < minimum or len(raw) > MAX_BUNDLE_BYTES + MAX_HEADER_BYTES:
            raise LocalBackupError("암호화 SQLite 백업 크기가 올바르지 않습니다.")
        if not raw.startswith(OUTER_MAGIC):
            raise LocalBackupError("지원하지 않는 암호화 SQLite 백업 형식입니다.")
        offset = len(OUTER_MAGIC)
        nonce = raw[offset : offset + NONCE_BYTES]
        plain = AESGCM(key).decrypt(nonce, raw[offset + NONCE_BYTES :], OUTER_MAGIC)
    except InvalidTag:
        raise LocalBackupError("백업 인증에 실패했습니다. 복원을 시작하지 않았습니다.") from None
    except LocalBackupError:
        raise
    except (OSError, ValueError, OverflowError):
        raise LocalBackupError("암호화 SQLite 백업을 안전하게 읽을 수 없습니다.") from None

    if not plain.startswith(INNER_MAGIC):
        raise LocalBackupError("SQLite 백업 내부 형식이 올바르지 않습니다.")
    cursor = len(INNER_MAGIC)
    if len(plain) < cursor + 4:
        raise LocalBackupError("SQLite 백업이 완전하지 않습니다.")
    header_size = struct.unpack(">I", plain[cursor : cursor + 4])[0]
    cursor += 4
    if header_size <= 0 or header_size > MAX_HEADER_BYTES or len(plain) < cursor + header_size:
        raise LocalBackupError("SQLite 백업 메타데이터가 올바르지 않습니다.")
    try:
        header = json.loads(plain[cursor : cursor + header_size].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise LocalBackupError("SQLite 백업 메타데이터를 확인할 수 없습니다.") from None
    cursor += header_size
    if not isinstance(header, dict) or header.get("format") != "WEALTHY-LOCAL-SQLITE-BUNDLE-V1":
        raise LocalBackupError("SQLite 백업 메타데이터 형식이 올바르지 않습니다.")
    metadata = header.get("entries")
    if not isinstance(metadata, list) or len(metadata) != len(ENTRY_NAMES):
        raise LocalBackupError("SQLite 백업 파일 목록이 올바르지 않습니다.")
    payloads: dict[str, bytes] = {}
    for expected_name, item in zip(ENTRY_NAMES, metadata, strict=True):
        if not isinstance(item, dict) or item.get("name") != expected_name:
            raise LocalBackupError("SQLite 백업 파일 목록이 올바르지 않습니다.")
        size = item.get("bytes")
        digest = item.get("sha256")
        if not isinstance(size, int) or size <= 0 or size > MAX_BUNDLE_BYTES:
            raise LocalBackupError("SQLite 백업 파일 크기가 올바르지 않습니다.")
        if not isinstance(digest, str) or len(digest) != 64:
            raise LocalBackupError("SQLite 백업 파일 해시가 올바르지 않습니다.")
        end = cursor + size
        if end > len(plain):
            raise LocalBackupError("SQLite 백업이 완전하지 않습니다.")
        payload = plain[cursor:end]
        cursor = end
        if hashlib.sha256(payload).hexdigest() != digest:
            raise LocalBackupError("SQLite 백업 파일 무결성 검사에 실패했습니다.")
        payloads[expected_name] = payload
    if cursor != len(plain):
        raise LocalBackupError("SQLite 백업에 허용되지 않은 후행 데이터가 있습니다.")
    return header, payloads


def _decrypt_bundle_v2(
    path: Path,
    key: bytes,
) -> tuple[
    dict[str, object],
    dict[str, bytes],
    list[tuple[str, bytes, str, dict[str, int], int, int]],
]:
    """Authenticate and parse a V2 bundle without writing plaintext files."""

    source = _safe_existing_file(path, label="backup")
    try:
        raw = source.read_bytes()
        minimum = len(OUTER_MAGIC_V2) + NONCE_BYTES + 16
        if len(raw) < minimum or len(raw) > MAX_BUNDLE_BYTES + MAX_HEADER_BYTES:
            raise LocalBackupError("암호화 V2 SQLite 백업 크기가 올바르지 않습니다.")
        if not raw.startswith(OUTER_MAGIC_V2):
            raise LocalBackupError("지원하지 않는 암호화 V2 SQLite 백업 형식입니다.")
        offset = len(OUTER_MAGIC_V2)
        nonce = raw[offset : offset + NONCE_BYTES]
        plain = AESGCM(key).decrypt(
            nonce,
            raw[offset + NONCE_BYTES :],
            OUTER_MAGIC_V2,
        )
    except InvalidTag:
        raise LocalBackupError(
            "V2 백업 인증에 실패했습니다. 복원을 시작하지 않았습니다."
        ) from None
    except LocalBackupError:
        raise
    except (OSError, ValueError, OverflowError):
        raise LocalBackupError(
            "암호화 V2 SQLite 백업을 안전하게 읽을 수 없습니다."
        ) from None

    if not plain.startswith(INNER_MAGIC_V2):
        raise LocalBackupError("V2 SQLite 백업 내부 형식이 올바르지 않습니다.")
    cursor = len(INNER_MAGIC_V2)
    if len(plain) < cursor + 4:
        raise LocalBackupError("V2 SQLite 백업이 완전하지 않습니다.")
    header_size = struct.unpack(">I", plain[cursor : cursor + 4])[0]
    cursor += 4
    if (
        header_size <= 0
        or header_size > MAX_HEADER_BYTES
        or len(plain) < cursor + header_size
    ):
        raise LocalBackupError("V2 SQLite 백업 메타데이터가 올바르지 않습니다.")
    try:
        header = json.loads(plain[cursor : cursor + header_size].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise LocalBackupError(
            "V2 SQLite 백업 메타데이터를 확인할 수 없습니다."
        ) from None
    cursor += header_size
    if (
        type(header) is not dict
        or set(header) != {
            "format",
            "created_at_utc_us",
            "recovery_point",
            "entries",
        }
        or header.get("format") != "WEALTHY-LOCAL-SQLITE-BUNDLE-V2"
        or type(header.get("created_at_utc_us")) is not int
    ):
        raise LocalBackupError("V2 SQLite 백업 메타데이터 형식이 올바르지 않습니다.")
    recovery = header.get("recovery_point")
    if (
        type(recovery) is not dict
        or set(recovery) != {"id", "mode", "quiesced_at_utc_us"}
        or type(recovery.get("id")) is not str
        or _RECOVERY_POINT_PATTERN.fullmatch(str(recovery.get("id"))) is None
        or recovery.get("mode") != "TRUSTED_TEST_FIXTURE_WRITER_BARRIER"
        or type(recovery.get("quiesced_at_utc_us")) is not int
    ):
        raise LocalBackupError("V2 SQLite 복구 지점 설명이 올바르지 않습니다.")
    quiesced_at = int(recovery["quiesced_at_utc_us"])
    metadata = header.get("entries")
    if type(metadata) is not list or len(metadata) != len(V2_ENTRY_NAMES):
        raise LocalBackupError("V2 SQLite 백업 파일 목록이 올바르지 않습니다.")

    payloads: dict[str, bytes] = {}
    entries: list[tuple[str, bytes, str, dict[str, int], int, int]] = []
    previous_finished = quiesced_at
    required_entry_keys = {
        "name",
        "bytes",
        "sha256",
        "schema",
        "table_counts",
        "snapshot_started_at_utc_us",
        "snapshot_finished_at_utc_us",
    }
    for index, (expected_name, item) in enumerate(
        zip(V2_ENTRY_NAMES, metadata, strict=True)
    ):
        if type(item) is not dict or set(item) != required_entry_keys:
            raise LocalBackupError("V2 SQLite 백업 파일 설명이 올바르지 않습니다.")
        size = item.get("bytes")
        digest = item.get("sha256")
        schema = item.get("schema")
        counts = item.get("table_counts")
        started_at = item.get("snapshot_started_at_utc_us")
        finished_at = item.get("snapshot_finished_at_utc_us")
        if (
            item.get("name") != expected_name
            or type(size) is not int
            or size <= 0
            or size > MAX_BUNDLE_BYTES
            or type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or type(schema) is not str
            or schema not in _V2_SCHEMAS[index]
            or type(counts) is not dict
            or any(
                type(name) is not str
                or type(count) is not int
                or count < 0
                for name, count in counts.items()
            )
            or type(started_at) is not int
            or type(finished_at) is not int
            or started_at < previous_finished
            or finished_at < started_at
        ):
            raise LocalBackupError("V2 SQLite 백업 파일 설명이 올바르지 않습니다.")
        end = cursor + size
        if end > len(plain):
            raise LocalBackupError("V2 SQLite 백업이 완전하지 않습니다.")
        payload = plain[cursor:end]
        cursor = end
        if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), digest):
            raise LocalBackupError("V2 SQLite 백업 파일 무결성 검사에 실패했습니다.")
        payloads[expected_name] = payload
        entries.append(
            (
                expected_name,
                payload,
                schema,
                {str(name): int(count) for name, count in counts.items()},
                started_at,
                finished_at,
            )
        )
        previous_finished = finished_at
    if cursor != len(plain):
        raise LocalBackupError("V2 SQLite 백업에 허용되지 않은 후행 데이터가 있습니다.")
    return header, payloads, entries


def validate_local_backup_v2(backup: Path, key: bytes) -> dict[str, object]:
    """Authenticate all V2 entries and verify schema, counts, and owner binding."""

    if len(key) != KEY_BYTES:
        raise LocalBackupError("백업 키 형식이 올바르지 않습니다.")
    header, _, entries = _decrypt_bundle_v2(backup, key)
    _validate_v2_snapshot_set(entries)
    recovery = header["recovery_point"]
    assert isinstance(recovery, dict)
    return {
        "status": "OK",
        "format": "WEALTHY-LOCAL-SQLITE-AES256-GCM-V2",
        "entries": list(V2_ENTRY_NAMES),
        "recovery_point_id": recovery["id"],
        "backup_authenticated": True,
        "consistency": "FIXTURE_QUIESCED_NOT_OPERATIONAL",
        "operational_recovery_point": False,
    }


def _validate_payload(payload: bytes, expected_schema: str) -> tuple[sqlite3.Connection, dict[str, int]]:
    memory = sqlite3.connect(":memory:")
    try:
        memory.deserialize(payload)
        integrity = memory.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise LocalBackupError("복원 SQLite 무결성 검사에 실패했습니다.")
        if memory.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise LocalBackupError("복원 SQLite 참조 무결성 검사에 실패했습니다.")
        if _schema_kind(memory) != expected_schema:
            raise LocalBackupError("복원 SQLite 스키마가 백업 설명과 일치하지 않습니다.")
        counts: dict[str, int] = {}
        for (table,) in memory.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            quoted = '"' + str(table).replace('"', '""') + '"'
            counts[str(table)] = int(
                memory.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
            )
        return memory, counts
    except LocalBackupError:
        memory.close()
        raise
    except (sqlite3.Error, TypeError, ValueError, OverflowError):
        memory.close()
        raise LocalBackupError("복원 SQLite를 안전하게 검사할 수 없습니다.") from None


def restore_local_databases(
    backup: Path,
    destination_directory: Path,
    key: bytes,
) -> dict[str, object]:
    """Restore into a new/empty directory after authenticating both DBs."""

    if len(key) != KEY_BYTES:
        raise LocalBackupError("백업 키 형식이 올바르지 않습니다.")
    header, payloads = _decrypt_bundle(backup, key)
    metadata = header["entries"]
    assert isinstance(metadata, list)

    validated: dict[str, tuple[sqlite3.Connection, dict[str, int]]] = {}
    for name, item in zip(ENTRY_NAMES, metadata, strict=True):
        assert isinstance(item, dict)
        schema = item.get("schema")
        if schema not in {"MARKET_DATA_V2", "MARKET_DATA_V3", "LOCAL_WEB_STATE_V1"}:
            raise LocalBackupError("복원 SQLite 스키마 설명이 올바르지 않습니다.")
        connection, counts = _validate_payload(payloads[name], str(schema))
        described_counts = item.get("table_counts")
        if not isinstance(described_counts, dict) or described_counts != counts:
            connection.close()
            raise LocalBackupError("복원 SQLite 행 수가 백업 설명과 일치하지 않습니다.")
        validated[name] = (connection, counts)

    directory_created = False
    temporary_paths: list[Path] = []
    committed_paths: list[Path] = []
    try:
        directory = destination_directory.expanduser()
        if not directory.is_absolute() or directory.is_symlink():
            raise ValueError
        if directory.exists():
            resolved = directory.resolve(strict=True)
            info = directory.stat(follow_symlinks=False)
            if resolved != directory or not stat.S_ISDIR(info.st_mode):
                raise ValueError
            if any(directory.iterdir()):
                raise ValueError
            if os.name != "nt" and info.st_uid != os.getuid():
                raise ValueError
        else:
            parent = directory.parent.resolve(strict=True)
            if parent != directory.parent:
                raise ValueError
            directory.mkdir(mode=0o700)
            directory_created = True
        if os.name != "nt":
            os.chmod(directory, 0o700)

        destinations = {name: directory / name for name in ENTRY_NAMES}
        if any(path.exists() or path.is_symlink() for path in destinations.values()):
            raise ValueError

        for name in ENTRY_NAMES:
            memory, _ = validated[name]
            descriptor, raw_temporary = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".partial", dir=directory
            )
            os.close(descriptor)
            temporary = Path(raw_temporary)
            temporary_paths.append(temporary)
            disk = sqlite3.connect(temporary)
            try:
                memory.backup(disk)
                disk.commit()
                check = disk.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise LocalBackupError("복원 파일 무결성 검사에 실패했습니다.")
            finally:
                disk.close()
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            with temporary.open("rb") as source:
                os.fsync(source.fileno())

        for name, temporary in zip(ENTRY_NAMES, temporary_paths, strict=True):
            destination = destinations[name]
            os.replace(temporary, destination)
            committed_paths.append(destination)
        temporary_paths.clear()
        return {
            "status": "OK",
            "files": list(ENTRY_NAMES),
            "table_counts": {
                name: validated[name][1] for name in ENTRY_NAMES
            },
            "backup_authenticated": True,
            "operating_databases_replaced": False,
        }
    except LocalBackupError:
        raise
    except (OSError, RuntimeError, sqlite3.Error, ValueError):
        raise LocalBackupError(
            "복원 대상 위치를 안전하게 사용할 수 없습니다. 기존 데이터는 변경하지 않았습니다."
        ) from None
    finally:
        for memory, _ in validated.values():
            memory.close()
        for path in temporary_paths:
            try:
                path.unlink()
            except OSError:
                pass
        # These paths can only have been created by this call in an initially
        # empty destination.  Remove them on a partial commit rather than leave
        # an apparently complete restore set.
        if committed_paths and len(committed_paths) != len(ENTRY_NAMES):
            for path in committed_paths:
                try:
                    path.unlink()
                except OSError:
                    pass
        if directory_created:
            try:
                destination_directory.rmdir()
            except OSError:
                pass


def restore_local_databases_v2(
    backup: Path,
    destination_directory: Path,
    key: bytes,
) -> dict[str, object]:
    """Restore a validated V2 recovery point into one new isolated directory."""

    if len(key) != KEY_BYTES:
        raise LocalBackupError("백업 키 형식이 올바르지 않습니다.")
    header, payloads, entries = _decrypt_bundle_v2(backup, key)
    validated: dict[str, tuple[sqlite3.Connection, dict[str, int]]] = {}
    try:
        for index, (name, _, schema, described_counts, _, _) in enumerate(entries):
            if name != V2_ENTRY_NAMES[index] or schema not in _V2_SCHEMAS[index]:
                raise LocalBackupError("V2 복원 SQLite 역할이 올바르지 않습니다.")
            connection, counts = _validate_payload(payloads[name], schema)
            if counts != described_counts:
                connection.close()
                raise LocalBackupError(
                    "V2 복원 SQLite 행 수가 백업 설명과 일치하지 않습니다."
                )
            validated[name] = (connection, counts)
        _validate_v2_owner_binding(
            validated[V2_ENTRY_NAMES[0]][0],
            validated[V2_ENTRY_NAMES[1]][0],
            validated[V2_ENTRY_NAMES[2]][0],
        )

        directory_created = False
        temporary_paths: list[Path] = []
        committed_paths: list[Path] = []
        try:
            directory = destination_directory.expanduser()
            if not directory.is_absolute() or directory.is_symlink():
                raise ValueError
            if directory.exists():
                resolved = directory.resolve(strict=True)
                info = directory.stat(follow_symlinks=False)
                if resolved != directory or not stat.S_ISDIR(info.st_mode):
                    raise ValueError
                if any(directory.iterdir()):
                    raise ValueError
                if os.name != "nt" and info.st_uid != os.getuid():
                    raise ValueError
            else:
                parent = directory.parent.resolve(strict=True)
                if parent != directory.parent:
                    raise ValueError
                directory.mkdir(mode=0o700)
                directory_created = True
            if os.name != "nt":
                os.chmod(directory, 0o700)

            destinations = {name: directory / name for name in V2_ENTRY_NAMES}
            if any(
                path.exists() or path.is_symlink() for path in destinations.values()
            ):
                raise ValueError

            for name in V2_ENTRY_NAMES:
                memory, _ = validated[name]
                descriptor, raw_temporary = tempfile.mkstemp(
                    prefix=f".{name}.", suffix=".partial", dir=directory
                )
                os.close(descriptor)
                temporary = Path(raw_temporary)
                temporary_paths.append(temporary)
                disk = sqlite3.connect(temporary)
                try:
                    memory.backup(disk)
                    disk.commit()
                    check = disk.execute("PRAGMA integrity_check").fetchone()
                    if check is None or check[0] != "ok":
                        raise LocalBackupError("V2 복원 파일 무결성 검사에 실패했습니다.")
                    if _schema_kind(disk) != entries[V2_ENTRY_NAMES.index(name)][2]:
                        raise LocalBackupError("V2 복원 파일 스키마 검사에 실패했습니다.")
                finally:
                    disk.close()
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                with temporary.open("rb") as source:
                    os.fsync(source.fileno())

            for name, temporary in zip(
                V2_ENTRY_NAMES, temporary_paths, strict=True
            ):
                destination = destinations[name]
                # The restore directory is empty and owner-only, but link()
                # still provides an atomic no-overwrite publication for each
                # member if another same-user process races this operation.
                os.link(temporary, destination, follow_symlinks=False)
                committed_paths.append(destination)
                temporary.unlink()
            temporary_paths.clear()
            _fsync_directory(directory)
            recovery = header["recovery_point"]
            assert isinstance(recovery, dict)
            return {
                "status": "OK",
                "format": "WEALTHY-LOCAL-SQLITE-AES256-GCM-V2",
                "files": list(V2_ENTRY_NAMES),
                "table_counts": {
                    name: validated[name][1] for name in V2_ENTRY_NAMES
                },
                "recovery_point_id": recovery["id"],
                "backup_authenticated": True,
                "owner_binding_verified": True,
                "consistency": "FIXTURE_QUIESCED_NOT_OPERATIONAL",
                "operational_recovery_point": False,
                "operating_databases_replaced": False,
            }
        except LocalBackupError:
            raise
        except (OSError, RuntimeError, sqlite3.Error, ValueError):
            raise LocalBackupError(
                "V2 복원 대상 위치를 안전하게 사용할 수 없습니다. 기존 데이터는 변경하지 않았습니다."
            ) from None
        finally:
            for path in temporary_paths:
                try:
                    path.unlink()
                except OSError:
                    pass
            if committed_paths and len(committed_paths) != len(V2_ENTRY_NAMES):
                for path in committed_paths:
                    try:
                        path.unlink()
                    except OSError:
                        pass
            if directory_created:
                try:
                    destination_directory.rmdir()
                except OSError:
                    pass
    finally:
        for memory, _ in validated.values():
            memory.close()


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(
        prog="web-local-backup",
        description="localhost SQLite 두 파일을 OS 보안 저장소 키로 백업·복원합니다.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup")
    backup_parser.add_argument("--market-database", type=Path, required=True)
    backup_parser.add_argument("--web-state-database", type=Path, required=True)
    backup_parser.add_argument("--output", type=Path, required=True)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--input", type=Path, required=True)
    restore_parser.add_argument("--destination-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        key = load_or_create_backup_key()
        if args.command == "backup":
            result = backup_local_databases(
                args.market_database, args.web_state_database, args.output, key
            )
        else:
            result = restore_local_databases(
                args.input, args.destination_directory, key
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (LocalBackupError, OSError, sqlite3.Error):
        print(
            "로컬 SQLite 백업 작업을 안전하게 완료할 수 없습니다. 경로와 원문은 표시하지 않습니다.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
