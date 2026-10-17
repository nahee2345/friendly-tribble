"""Database construction with PostgreSQL production and SQLite test support."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, func, inspect, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from asset_assistant.domain import DomainValidationError

from .models import (
    Base,
    LoginAttempt,
    MigrationLedger,
    ScenarioSnapshot,
    StrategyTargetVersion,
    User,
    WebSession,
)


_LOCAL_SCHEMA_REVISION = "local-web-state-v1"
_LOCAL_SCHEMA_CHECKSUM = (
    "2f535a1b683ddf919062475494780f8d8b5ce8cc2866976b817ac082705f5053"
)
_LOCAL_TABLES = (
    User.__table__,
    WebSession.__table__,
    LoginAttempt.__table__,
    ScenarioSnapshot.__table__,
    StrategyTargetVersion.__table__,
    MigrationLedger.__table__,
)
_LOCAL_TABLE_NAMES = frozenset(table.name for table in _LOCAL_TABLES)


class Database:
    """Own one SQLAlchemy engine and its non-expiring session factory."""

    def __init__(
        self,
        url: str,
        *,
        echo: bool = False,
        local_file: Path | None = None,
    ) -> None:
        try:
            parsed = make_url(url)
        except Exception:
            raise DomainValidationError("데이터베이스 연결 설정을 확인할 수 없습니다.") from None
        if parsed.drivername not in {
            "postgresql",
            "postgresql+psycopg",
            "sqlite",
            "sqlite+pysqlite",
        }:
            raise DomainValidationError("지원하지 않는 데이터베이스 방식입니다.")

        checked_local_file: Path | None = None
        if local_file is not None:
            if not parsed.drivername.startswith("sqlite"):
                raise DomainValidationError("로컬 상태 DB는 SQLite여야 합니다.")
            checked_local_file = self._prepare_local_file(local_file)
            try:
                parsed_database = Path(parsed.database or "").resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                raise DomainValidationError("로컬 상태 DB 경로를 확인할 수 없습니다.") from None
            if parsed_database != checked_local_file:
                raise DomainValidationError("로컬 상태 DB 경로가 일치하지 않습니다.")

            # This descriptor-based check must finish before create_engine can
            # open the first SQLite connection.  Closing any separately opened
            # descriptor while SQLite holds POSIX locks can release every lock
            # this process owns for that inode.
            self._secure_local_sqlite_files(checked_local_file)

        options: dict[str, object] = {"echo": echo, "future": True, "pool_pre_ping": True}
        if parsed.drivername.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False}
            if parsed.database in {None, "", ":memory:"}:
                options["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **options)
        if parsed.drivername.startswith("sqlite"):
            if checked_local_file is not None:
                # Runtime checks deliberately use path metadata and chmod
                # only.  They must never open/close another descriptor for a
                # database inode while SQLite connections are alive.
                event.listen(
                    self.engine,
                    "connect",
                    self._secure_local_sqlite_on_connect,
                )
            event.listen(self.engine, "connect", self._configure_sqlite)
            if checked_local_file is not None:
                event.listen(
                    self.engine,
                    "checkout",
                    self._secure_local_sqlite_on_checkout,
                )
                event.listen(
                    self.engine,
                    "checkin",
                    self._secure_local_sqlite_on_checkin,
                )
        self.local_file = checked_local_file
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=True,
        )

    @staticmethod
    def _configure_sqlite(dbapi_connection: object, connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=FULL")
        try:
            database_rows = cursor.execute("PRAGMA database_list").fetchall()
            database_path = str(database_rows[0][2]) if database_rows else ""
            if database_path:
                cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:
            # A failed safety pragma must prevent a partially configured local
            # runtime. SQLAlchemy wraps this DB-API exception safely upstream.
            cursor.close()
            raise
        cursor.close()

    @staticmethod
    def _secure_local_sqlite_files(path: Path) -> None:
        """Prepare owned SQLite files before the first DB-API connection.

        Descriptor checks avoid following a pre-existing symbolic link.  All
        descriptors are closed before SQLite is allowed to open the database;
        this helper must never be called from a live connection event.
        """

        if os.name == "nt":
            return
        try:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{path}{suffix}")
                flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(candidate, flags, 0o600)
                try:
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        raise OSError
                    os.fchmod(descriptor, 0o600)
                finally:
                    os.close(descriptor)
        except OSError:
            raise DomainValidationError(
                "로컬 상태 DB 권한을 안전하게 설정할 수 없습니다."
            ) from None

    @staticmethod
    def _secure_local_sqlite_paths_while_running(path: Path) -> None:
        """Recheck live SQLite paths without opening another descriptor.

        The owner-only parent directory prevents unrelated users from racing
        the path operation.  Pre/post metadata checks still reject links,
        replacements, non-regular files, and ownership changes.  SQLite may
        legitimately remove WAL sidecars after its last connection closes, so
        absent sidecars are left for SQLite to recreate from the 0600 main
        database mode.
        """

        if os.name == "nt":
            return
        try:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{path}{suffix}")
                try:
                    before = candidate.stat(follow_symlinks=False)
                except FileNotFoundError:
                    if suffix:
                        continue
                    raise
                if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
                    raise OSError
                os.chmod(candidate, 0o600)
                after = candidate.stat(follow_symlinks=False)
                if (
                    (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                    or not stat.S_ISREG(after.st_mode)
                    or after.st_uid != os.getuid()
                    or stat.S_IMODE(after.st_mode) != 0o600
                ):
                    raise OSError
        except OSError:
            raise DomainValidationError(
                "로컬 상태 DB 권한을 안전하게 설정할 수 없습니다."
            ) from None

    def _secure_local_sqlite_on_connect(
        self, dbapi_connection: object, connection_record: object
    ) -> None:
        if self.local_file is not None:
            self._secure_local_sqlite_paths_while_running(self.local_file)

    def _secure_local_sqlite_on_checkout(
        self,
        dbapi_connection: object,
        connection_record: object,
        connection_proxy: object,
    ) -> None:
        if self.local_file is not None:
            self._secure_local_sqlite_paths_while_running(self.local_file)

    def _secure_local_sqlite_on_checkin(
        self, dbapi_connection: object, connection_record: object
    ) -> None:
        if self.local_file is not None:
            self._secure_local_sqlite_paths_while_running(self.local_file)

    @staticmethod
    def _prepare_local_file(path: Path) -> Path:
        try:
            candidate = path.expanduser()
            if not candidate.is_absolute():
                raise ValueError
            parent = candidate.parent.resolve(strict=True)
            if candidate.parent != parent:
                # Do not silently traverse a symlinked data directory.
                raise ValueError
            parent_info = parent.stat(follow_symlinks=False)
            if not stat.S_ISDIR(parent_info.st_mode):
                raise ValueError
            if os.name != "nt":
                if parent_info.st_uid != os.getuid():
                    raise ValueError
                os.chmod(parent, 0o700)
            if candidate.exists() or candidate.is_symlink():
                info = candidate.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError
                if os.name != "nt" and info.st_uid != os.getuid():
                    raise ValueError
                resolved = candidate.resolve(strict=True)
                if resolved != candidate:
                    raise ValueError
            else:
                resolved = candidate
            return resolved
        except (OSError, RuntimeError, ValueError):
            raise DomainValidationError(
                "로컬 상태 DB 경로를 안전하게 사용할 수 없습니다. 전체 경로는 표시하지 않습니다."
            ) from None

    @classmethod
    def for_local_file(cls, path: Path, *, echo: bool = False) -> "Database":
        checked = cls._prepare_local_file(path)
        # URL.create keeps spaces, ``#`` and non-ASCII path characters in the
        # SQLite database component instead of interpreting them as URL syntax.
        url = URL.create("sqlite+pysqlite", database=str(checked))
        return cls(
            url.render_as_string(hide_password=False),
            echo=echo,
            local_file=checked,
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def create_local_schema(self) -> None:
        """Create only local web-state tables, never financial store tables.

        The market/report/job/monthly tables remain owned by
        :class:`asset_assistant.market_data.MarketDataStore` in its separate
        database. Pointing this runtime at that database therefore fails closed
        instead of creating a second, incompatible interpretation of a table.
        """

        if self.local_file is None:
            raise DomainValidationError("로컬 상태 DB 설정이 아닙니다.")
        before = frozenset(inspect(self.engine).get_table_names())
        unexpected = before - _LOCAL_TABLE_NAMES
        if unexpected:
            raise DomainValidationError(
                "로컬 상태 DB에 지원하지 않는 테이블이 있습니다. 기존 자료는 변경하지 않았습니다."
            )
        Base.metadata.create_all(self.engine, tables=list(_LOCAL_TABLES))
        after = frozenset(inspect(self.engine).get_table_names())
        if after != _LOCAL_TABLE_NAMES:
            raise DomainValidationError("로컬 상태 DB 스키마를 안전하게 확인할 수 없습니다.")
        with self.session_factory.begin() as session:
            if int(session.scalar(select(func.count(User.id))) or 0) > 1:
                # Provider credentials intentionally reuse the existing
                # machine-local CredentialStore item names, so local mode is
                # strictly single-user rather than pretending tenant isolation.
                raise DomainValidationError(
                    "로컬 웹 상태 DB는 하나의 사용자 계정만 지원합니다."
                )
            item = session.get(MigrationLedger, _LOCAL_SCHEMA_REVISION)
            if item is None:
                session.add(
                    MigrationLedger(
                        revision=_LOCAL_SCHEMA_REVISION,
                        checksum_sha256=_LOCAL_SCHEMA_CHECKSUM,
                    )
                )
            elif item.checksum_sha256 != _LOCAL_SCHEMA_CHECKSUM:
                raise DomainValidationError("로컬 상태 DB 스키마 이력을 확인할 수 없습니다.")
        self._secure_local_sqlite_paths_while_running(self.local_file)

    def sqlite_pragmas(self) -> dict[str, object]:
        """Return non-sensitive pragma state for diagnostics and tests."""

        if self.engine.dialect.name != "sqlite":
            raise DomainValidationError("SQLite 상태가 아닙니다.")
        with self.engine.connect() as connection:
            return {
                "foreign_keys": connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one(),
                "busy_timeout": connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one(),
                "journal_mode": connection.exec_driver_sql("PRAGMA journal_mode").scalar_one(),
                "synchronous": connection.exec_driver_sql("PRAGMA synchronous").scalar_one(),
            }

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory.begin() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()
