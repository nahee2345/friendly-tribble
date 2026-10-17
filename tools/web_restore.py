"""Restore an authenticated backup into a new, explicit PostgreSQL database."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

from asset_assistant.cli import _SafeArgumentParser

from tools.web_backup_crypto import (
    BackupCryptoError,
    decrypt_verified_file_to_sink,
    load_key,
    verify_encrypted_file,
)


DATABASE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
RESERVED_TARGETS = frozenset({"postgres", "template0", "template1", "wealthy"})


def _compose(compose_file: Path, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(compose_file), *args]


def _run_control(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
    )


def _database_exists(compose_file: Path, user: str, target: str) -> bool:
    query = f"SELECT 1 FROM pg_database WHERE datname = '{target}'"
    result = _run_control(
        _compose(
            compose_file,
            "exec",
            "-T",
            "postgres",
            "psql",
            "--username",
            user,
            "--dbname",
            "postgres",
            "--tuples-only",
            "--no-align",
            "--command",
            query,
        )
    )
    if result.returncode != 0:
        raise BackupCryptoError("대상 데이터베이스 상태를 확인할 수 없습니다.")
    return result.stdout.strip() == b"1"


def _drop_database(compose_file: Path, user: str, target: str) -> None:
    _run_control(
        _compose(
            compose_file,
            "exec",
            "-T",
            "postgres",
            "dropdb",
            "--if-exists",
            "--username",
            user,
            target,
        )
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_counts(
    compose_file: Path, user: str, target: str
) -> dict[str, int]:
    listing = _run_control(
        _compose(
            compose_file,
            "exec",
            "-T",
            "postgres",
            "psql",
            "--username",
            user,
            "--dbname",
            target,
            "--tuples-only",
            "--no-align",
            "--field-separator=\t",
            "--command",
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
            "ORDER BY schemaname, tablename",
        )
    )
    if listing.returncode != 0:
        raise BackupCryptoError("복원 결과의 테이블 목록을 확인할 수 없습니다.")
    counts: dict[str, int] = {}
    for raw_line in listing.stdout.decode("utf-8", errors="strict").splitlines():
        schema, separator, table = raw_line.partition("\t")
        if not separator:
            raise BackupCryptoError("복원 결과의 테이블 목록 형식이 올바르지 않습니다.")
        query = (
            f"SELECT count(*) FROM {_quote_identifier(schema)}."
            f"{_quote_identifier(table)}"
        )
        result = _run_control(
            _compose(
                compose_file,
                "exec",
                "-T",
                "postgres",
                "psql",
                "--username",
                user,
                "--dbname",
                target,
                "--tuples-only",
                "--no-align",
                "--command",
                query,
            )
        )
        if result.returncode != 0:
            raise BackupCryptoError("복원 결과의 행 수를 확인할 수 없습니다.")
        counts[f"{schema}.{table}"] = int(result.stdout.strip())
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(
        description="인증된 암호화 백업을 존재하지 않는 명시적 DB로 복원합니다."
    )
    parser.add_argument("--compose-file", type=Path, default=Path("deploy/compose.yaml"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--database-user", default="wealthy")
    args = parser.parse_args(argv)

    target = args.target_database
    if not DATABASE_NAME.fullmatch(target) or target in RESERVED_TARGETS:
        print(
            "복원 대상은 기존 운영 DB가 아닌 새 소문자 DB 이름이어야 합니다.",
            file=sys.stderr,
        )
        return 2

    created = False
    restore: subprocess.Popen[bytes] | None = None
    try:
        key = load_key(args.key_file)
        digest = verify_encrypted_file(args.input, key)
        if _database_exists(args.compose_file, args.database_user, target):
            raise BackupCryptoError("복원 대상 데이터베이스가 이미 존재합니다.")
        creation = _run_control(
            _compose(
                args.compose_file,
                "exec",
                "-T",
                "postgres",
                "createdb",
                "--username",
                args.database_user,
                "--encoding=UTF8",
                "--template=template0",
                target,
            )
        )
        if creation.returncode != 0:
            raise BackupCryptoError("새 복원 대상 데이터베이스를 만들 수 없습니다.")
        created = True

        restore = subprocess.Popen(
            _compose(
                args.compose_file,
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "--username",
                args.database_user,
                "--dbname",
                target,
                "--single-transaction",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            shell=False,
        )
        assert restore.stdin is not None
        assert restore.stderr is not None
        error_bytes = bytearray()

        def drain_errors() -> None:
            while True:
                chunk = restore.stderr.read(4096)
                if not chunk:
                    return
                if len(error_bytes) < 8192:
                    error_bytes.extend(chunk[: 8192 - len(error_bytes)])

        drain = threading.Thread(target=drain_errors, daemon=True)
        drain.start()
        decrypt_verified_file_to_sink(args.input, key, restore.stdin)
        restore.stdin.close()
        return_code = restore.wait()
        drain.join(timeout=5)
        if return_code != 0:
            raise BackupCryptoError("PostgreSQL 복원이 완료되지 않았습니다.")
        counts = _table_counts(args.compose_file, args.database_user, target)
        print(
            json.dumps(
                {
                    "status": "OK",
                    "backup_sha256": digest,
                    "table_counts": counts,
                    "plaintext_written": False,
                    "production_database_replaced": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (BackupCryptoError, OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        if restore is not None and restore.poll() is None:
            restore.kill()
            restore.wait()
        if created:
            _drop_database(args.compose_file, args.database_user, target)
        print("암호화 백업을 안전하게 복원할 수 없습니다.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
