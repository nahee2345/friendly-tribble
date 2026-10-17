"""Stream pg_dump into an authenticated encrypted file; never write plaintext."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from asset_assistant.cli import _SafeArgumentParser

from tools.web_backup_crypto import (
    BackupCryptoError,
    encrypt_stream_to_temporary,
    load_key,
)


def _drain_limited(stream: object, result: bytearray, limit: int = 8192) -> None:
    reader = getattr(stream, "read", None)
    if reader is None:
        return
    while True:
        chunk = reader(4096)
        if not chunk:
            return
        if len(result) < limit:
            result.extend(chunk[: limit - len(result)])


def _compose_prefix(compose_file: Path) -> list[str]:
    return ["docker", "compose", "-f", str(compose_file)]


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(
        description="PostgreSQL custom dump를 AES-256-GCM으로 즉시 암호화합니다."
    )
    parser.add_argument("--compose-file", type=Path, default=Path("deploy/compose.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--database", default="wealthy")
    parser.add_argument("--database-user", default="wealthy")
    args = parser.parse_args(argv)

    if args.output.exists() or args.output.is_symlink():
        print("출력 백업이 이미 있어 덮어쓰지 않았습니다.", file=sys.stderr)
        return 2

    temporary: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        key = load_key(args.key_file)
        command = _compose_prefix(args.compose_file) + [
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "--username",
            args.database_user,
            "--dbname",
            args.database,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
        ]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False
        )
        assert process.stdout is not None
        assert process.stderr is not None
        errors = bytearray()
        drain = threading.Thread(
            target=_drain_limited, args=(process.stderr, errors), daemon=True
        )
        drain.start()
        temporary, digest, size = encrypt_stream_to_temporary(
            process.stdout, args.output, key
        )
        process.stdout.close()
        return_code = process.wait()
        drain.join(timeout=5)
        if return_code != 0:
            temporary.unlink(missing_ok=True)
            temporary = None
            raise BackupCryptoError("PostgreSQL 백업 생성이 완료되지 않았습니다.")
        os.replace(temporary, args.output)
        temporary = None
        if os.name != "nt":
            os.chmod(args.output, 0o600)
        print(
            json.dumps(
                {
                    "status": "OK",
                    "format": "WEALTHY-PGDUMP-AES256-GCM-V1",
                    "bytes": size,
                    "sha256": digest,
                    "plaintext_written": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (BackupCryptoError, OSError, subprocess.SubprocessError):
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        print("암호화 백업을 안전하게 만들 수 없습니다.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
