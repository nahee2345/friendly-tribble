"""Read-only inventory for planning an explicit SQLite-to-PostgreSQL import."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import struct
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from asset_assistant.cli import _SafeArgumentParser


KNOWN_TABLES = frozenset(
    {
        "schema_meta",
        "collection_runs",
        "market_records",
        "app_settings",
        "strategy_versions",
        "candidate_assessments",
        "report_snapshots",
        "monthly_asset_snapshots",
        "job_runs",
        "job_attempt_history",
        "provider_daily_usage",
        "operation_leases",
    }
)
CHUNK_SIZE = 1024 * 1024


class InspectionError(ValueError):
    pass


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_state(path: Path) -> dict[str, object]:
    state: dict[str, object] = {
        "database_bytes": path.stat().st_size,
        "database_sha256": _file_hash(path),
    }
    wal = Path(f"{path}-wal")
    if wal.exists():
        if wal.is_symlink() or not wal.is_file():
            raise InspectionError("SQLite WAL 파일 형식이 안전하지 않습니다.")
        state["wal_bytes"] = wal.stat().st_size
        state["wal_sha256"] = _file_hash(wal)
    else:
        state["wal_bytes"] = 0
        state["wal_sha256"] = None
    return state


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _hash_value(digest: object, value: object) -> None:
    update = getattr(digest, "update")
    if value is None:
        update(b"N;")
    elif isinstance(value, bytes):
        update(b"B" + struct.pack(">Q", len(value)) + value)
    elif isinstance(value, int):
        encoded = str(value).encode("ascii")
        update(b"I" + struct.pack(">Q", len(encoded)) + encoded)
    elif isinstance(value, float):
        encoded = value.hex().encode("ascii")
        update(b"F" + struct.pack(">Q", len(encoded)) + encoded)
    else:
        encoded = str(value).encode("utf-8")
        update(b"T" + struct.pack(">Q", len(encoded)) + encoded)


def _table_summary(
    connection: sqlite3.Connection, table: str
) -> dict[str, object]:
    quoted = _quote_identifier(table)
    column_rows = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
    columns = [str(row[1]) for row in column_rows]
    if not columns:
        raise InspectionError("열이 없는 테이블은 자동 계획 대상이 아닙니다.")
    count = int(connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0])
    schema_digest = hashlib.sha256()
    for column_row in column_rows:
        for value in column_row:
            _hash_value(schema_digest, value)
    digest = hashlib.sha256()
    for column in columns:
        _hash_value(digest, column)
    try:
        cursor = connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid")
    except sqlite3.OperationalError:
        order = ", ".join(_quote_identifier(column) for column in columns)
        cursor = connection.execute(f"SELECT * FROM {quoted} ORDER BY {order}")
    seen = 0
    for row in cursor:
        digest.update(b"R")
        for value in row:
            _hash_value(digest, value)
        seen += 1
    if seen != count:
        raise InspectionError("SQLite 읽기 중 행 수가 변경되었습니다.")
    return {
        "rows": count,
        "content_sha256": digest.hexdigest(),
        "column_count": len(columns),
        "schema_sha256": schema_digest.hexdigest(),
    }


def inspect(source: Path) -> dict[str, object]:
    try:
        info = source.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise InspectionError("SQLite 원본은 일반 파일이어야 합니다.")
        before = _source_state(source)
        uri = f"file:{quote(str(source.resolve()), safe='/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            data_version_before = int(
                connection.execute("PRAGMA data_version").fetchone()[0]
            )
            integrity = [
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            ]
            if integrity != ["ok"]:
                raise InspectionError("SQLite 무결성 검사가 통과하지 않았습니다.")
            table_names = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            all_summaries = {
                name: _table_summary(connection, name) for name in table_names
            }
            summaries = {
                name: summary
                for name, summary in all_summaries.items()
                if name in KNOWN_TABLES
            }
            contains_actual = False
            if "monthly_asset_snapshots" in table_names:
                contains_actual = bool(
                    connection.execute(
                        "SELECT EXISTS(SELECT 1 FROM monthly_asset_snapshots "
                        "WHERE realm='MANUAL_ACTUAL')"
                    ).fetchone()[0]
                )
            schema_version = None
            if "schema_meta" in table_names:
                row = connection.execute(
                    "SELECT version FROM schema_meta LIMIT 1"
                ).fetchone()
                schema_version = int(row[0]) if row is not None else None
            data_version_after = int(
                connection.execute("PRAGMA data_version").fetchone()[0]
            )
            connection.rollback()
        finally:
            connection.close()
        after = _source_state(source)
        if before != after or data_version_before != data_version_after:
            raise InspectionError("점검 중 SQLite 원본이 변경되어 계획을 만들지 않았습니다.")
    except InspectionError:
        raise
    except (OSError, sqlite3.Error, UnicodeError, ValueError, OverflowError):
        raise InspectionError("SQLite 원본을 안전하게 점검할 수 없습니다.") from None

    unknown_count = len(set(table_names) - KNOWN_TABLES)
    return {
        "status": "INSPECTED_ONLY",
        "source": before,
        "sqlite_schema_version": schema_version,
        "integrity_check": "ok",
        "tables": summaries,
        "unknown_table_count": unknown_count,
        "contains_manual_actual_snapshot": contains_actual,
        "postgres_import_performed": False,
        "automatic_actual_import_allowed": False,
        "next_step": (
            "사용자가 자료영역과 소유자를 확정한 뒤 별도 staging importer를 검증해야 합니다."
        ),
    }


def _write_plan(path: Path, payload: str) -> None:
    if path.exists() or path.is_symlink():
        raise InspectionError("계획 출력 파일이 이미 있어 덮어쓰지 않았습니다.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(
        description=(
            "SQLite를 read-only로 검사해 행 수·내용 hash 계획만 만듭니다. "
            "PostgreSQL에는 연결하거나 쓰지 않습니다."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.source)
        payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            _write_plan(args.output, payload)
            print("읽기 전용 마이그레이션 점검 계획을 만들었습니다.")
        else:
            sys.stdout.write(payload)
        return 0
    except InspectionError:
        print("SQLite 마이그레이션 계획을 안전하게 만들 수 없습니다.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
