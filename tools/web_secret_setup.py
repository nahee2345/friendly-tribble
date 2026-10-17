"""Create runtime secret files without accepting secret values on argv."""

from __future__ import annotations

import base64
import os
import secrets
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from asset_assistant.cli import _SafeArgumentParser


SECRET_NAMES = (
    "postgres_password",
    "database_url",
    "master_key",
    "backup_key",
)


def _write_secret_set(directory: Path, values: dict[str, bytes]) -> None:
    temporary: dict[str, Path] = {}
    try:
        for name, value in values.items():
            handle = tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{name}.", dir=directory, delete=False
            )
            temp_path = Path(handle.name)
            temporary[name] = temp_path
            try:
                if os.name != "nt":
                    os.chmod(temp_path, 0o600)
                handle.write(value + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()

        for name in SECRET_NAMES:
            os.replace(temporary.pop(name), directory / name)
    finally:
        for path in temporary.values():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _safe_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("비밀 폴더로 심볼릭 링크를 사용할 수 없습니다.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ValueError("비밀 파일 대상이 폴더가 아닙니다.")
    if os.name != "nt":
        os.chmod(path, 0o700)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ValueError("비밀 폴더 권한을 소유자 전용으로 설정할 수 없습니다.")
    return path


def build_values() -> dict[str, bytes]:
    password = secrets.token_urlsafe(36)
    encoded_password = quote(password, safe="")
    database_url = (
        "postgresql+psycopg://wealthy:"
        f"{encoded_password}@postgres:5432/wealthy"
    )
    return {
        "postgres_password": password.encode("ascii"),
        "database_url": database_url.encode("ascii"),
        "master_key": base64.urlsafe_b64encode(secrets.token_bytes(32)),
        "backup_key": base64.urlsafe_b64encode(secrets.token_bytes(32)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(
        description="Wealthy 런타임 비밀을 무작위 생성합니다. 값은 출력하지 않습니다."
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path("deploy/secrets"),
        help="실제 비밀 파일을 둘 로컬 폴더",
    )
    args = parser.parse_args(argv)

    try:
        directory = _safe_directory(args.directory)
        existing = [name for name in SECRET_NAMES if (directory / name).exists()]
        if existing:
            print(
                "기존 비밀 파일이 있어 변경하지 않았습니다. "
                "운영 키 교체는 앱의 전용 회전 절차를 사용하세요.",
                file=sys.stderr,
            )
            return 2

        for name in SECRET_NAMES:
            target = directory / name
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise ValueError("비밀 파일 대상 형식이 안전하지 않습니다.")

        _write_secret_set(directory, build_values())
        print("비밀 파일 4개를 생성했습니다. 값은 표시하지 않았습니다.")
        return 0
    except (OSError, ValueError):
        print("비밀 파일을 안전하게 생성할 수 없습니다.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
