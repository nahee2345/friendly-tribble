"""Copy Compose file-secrets into tmpfs, then permanently drop privileges.

Docker Compose implements local file-backed secrets as bind mounts, so their
host ``0600`` ownership may not match the image's unprivileged uid.  This
entrypoint starts with only SETUID/SETGID capabilities, copies a fixed
allowlist into the container's ephemeral ``/tmp`` tmpfs, and executes the
configured process as uid/gid 10001.  Secret values are never printed or put
in environment variables.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


RUNTIME_UID = 10001
RUNTIME_GID = 10001
SECRET_FILE_ENVIRONMENTS = (
    "WEALTHY_DATABASE_URL_FILE",
    "WEALTHY_MASTER_KEY_FILE",
    "WEALTHY_VAPID_PUBLIC_KEY_FILE",
    "WEALTHY_VAPID_PRIVATE_KEY_FILE",
)
_SOURCE_ROOT = Path("/run/secrets")
_DESTINATION_ROOT = Path("/tmp/wealthy-runtime-secrets")
_MAX_SECRET_BYTES = 16_384


def _copy_secret(environment_name: str, raw_source: str) -> None:
    source = Path(raw_source)
    try:
        resolved_parent = source.parent.resolve(strict=True)
        source_info = source.stat(follow_symlinks=False)
    except (OSError, RuntimeError, ValueError):
        raise RuntimeError("secret source is unavailable") from None
    if (
        resolved_parent != _SOURCE_ROOT
        or source.parent != _SOURCE_ROOT
        or not stat.S_ISREG(source_info.st_mode)
        or source_info.st_size < 1
        or source_info.st_size > _MAX_SECRET_BYTES
    ):
        raise RuntimeError("secret source is unsafe")

    destination = _DESTINATION_ROOT / source.name
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=16_384)
        os.chmod(destination, 0o600)
        os.chown(destination, RUNTIME_UID, RUNTIME_GID)
    except OSError:
        raise RuntimeError("secret copy failed") from None
    os.environ[environment_name] = str(destination)


def prepare_and_drop_privileges() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("entrypoint must start as root")
    os.umask(0o077)
    try:
        _DESTINATION_ROOT.mkdir(mode=0o700, parents=False, exist_ok=False)
        os.chown(_DESTINATION_ROOT, RUNTIME_UID, RUNTIME_GID)
    except OSError:
        raise RuntimeError("runtime secret directory failed") from None

    for environment_name in SECRET_FILE_ENVIRONMENTS:
        raw_source = os.environ.get(environment_name)
        if raw_source:
            _copy_secret(environment_name, raw_source)

    os.setgroups([])
    os.setgid(RUNTIME_GID)
    os.setuid(RUNTIME_UID)
    if os.geteuid() != RUNTIME_UID or os.getegid() != RUNTIME_GID:
        raise RuntimeError("privilege drop failed")


def main(argv: list[str] | None = None) -> int:
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        sys.stderr.write("컨테이너 오류: 실행 명령이 없습니다.\n")
        return 2
    try:
        prepare_and_drop_privileges()
        os.execvp(command[0], command)
    except (OSError, RuntimeError):
        sys.stderr.write("컨테이너 오류: 보안 초기화를 완료할 수 없습니다.\n")
        return 2
    return 2  # pragma: no cover - successful exec never returns


if __name__ == "__main__":
    raise SystemExit(main())
