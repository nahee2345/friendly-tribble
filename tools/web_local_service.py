"""Install and inspect the Ubuntu user units for the local Wealthy web app.

Only fixed, project-owned unit names are ever written, enabled, disabled, or
removed.  The older desktop timers are treated as a conflict and are never
modified by this tool.  Unit files contain paths and fixed command arguments,
but never credentials or secret values.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from asset_assistant.cli import _SafeArgumentParser
from asset_assistant.scheduler import (
    COLLECT_SERVICE as LEGACY_COLLECT_SERVICE,
    COLLECT_TIMER as LEGACY_COLLECT_TIMER,
    REPORT_SERVICE as LEGACY_REPORT_SERVICE,
    REPORT_TIMER as LEGACY_REPORT_TIMER,
    SchedulerError,
    _atomic_write_text,
    _systemd_path_value,
    _systemd_quote,
)


TIME_ZONE = "Asia/Seoul"
WEB_SERVICE = "wealthy-local-web.service"
COLLECT_SERVICE = "wealthy-local-collect.service"
COLLECT_TIMER = "wealthy-local-collect.timer"
REPORT_SERVICE = "wealthy-local-report.service"
REPORT_TIMER = "wealthy-local-report.timer"

UNIT_NAMES = (
    WEB_SERVICE,
    COLLECT_SERVICE,
    COLLECT_TIMER,
    REPORT_SERVICE,
    REPORT_TIMER,
)
ENABLED_UNIT_NAMES = (WEB_SERVICE, COLLECT_TIMER, REPORT_TIMER)
LEGACY_UNIT_NAMES = (
    LEGACY_COLLECT_SERVICE,
    LEGACY_COLLECT_TIMER,
    LEGACY_REPORT_SERVICE,
    LEGACY_REPORT_TIMER,
)
MANAGED_MARKER = "# Managed-By: WealthyLocalServiceTool/v1"


class LocalServiceError(SchedulerError):
    """A local-service error which never embeds a path or command output."""


@dataclass(frozen=True)
class LocalServiceStatus:
    installed: bool
    enabled: bool | None
    active: bool | None
    conflict: bool
    detail: str
    present_units: tuple[str, ...] = ()


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "wealthy"


def _default_market_database() -> Path:
    return (
        Path.home()
        / ".local"
        / "share"
        / "long-term-asset-assistant"
        / "market-data.sqlite3"
    )


def _existing_directory(path: Path, *, message: str) -> Path:
    try:
        expanded = path.expanduser()
        resolved = expanded.resolve(strict=True)
        if not resolved.is_dir() or expanded.is_symlink():
            raise OSError
    except (OSError, RuntimeError, ValueError):
        raise LocalServiceError(message) from None
    if any(character in str(resolved) for character in ("\x00", "\n", "\r")):
        raise LocalServiceError(message)
    return resolved


def _existing_file(path: Path, *, message: str) -> Path:
    try:
        expanded = path.expanduser()
        resolved_target = expanded.resolve(strict=True)
        if not resolved_target.is_file():
            raise OSError
        absolute = expanded.absolute()
    except (OSError, RuntimeError, ValueError):
        raise LocalServiceError(message) from None
    if any(character in str(absolute) for character in ("\x00", "\n", "\r")):
        raise LocalServiceError(message)
    # Virtual-environment launchers are normally symlinks.  Keep the launcher
    # path after validating its target so Python retains the venv prefix.
    return absolute


def _future_path(path: Path, *, directory: bool) -> Path:
    """Resolve a runtime target without creating user data during installation."""

    message = "로컬 자료 경로를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다."
    try:
        expanded = path.expanduser()
        if any(character in str(expanded) for character in ("\x00", "\n", "\r")):
            raise ValueError
        resolved = expanded.resolve(strict=False)
        if resolved.exists():
            if expanded.is_symlink():
                raise OSError
            if directory and not resolved.is_dir():
                raise OSError
            if not directory and not resolved.is_file():
                raise OSError
        else:
            ancestor = resolved.parent
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            if not ancestor.is_dir() or ancestor.is_symlink():
                raise OSError
    except (OSError, RuntimeError, ValueError):
        raise LocalServiceError(message) from None
    return resolved


def generate_units(
    project_root: Path,
    python_executable: Path,
    data_dir: Path,
    market_database: Path,
) -> dict[str, str]:
    """Return the exact five local web/service timer unit files."""

    root = _existing_directory(
        project_root,
        message="프로젝트 경로를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다.",
    )
    python = _existing_file(
        python_executable,
        message="Python 실행 경로를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다.",
    )
    runtime_data = _future_path(data_dir, directory=True)
    market_db = _future_path(market_database, directory=False)
    working_directory = _systemd_path_value(str(root))

    def command(*arguments: str) -> str:
        return " ".join(
            _systemd_quote(token)
            for token in (
                str(python),
                "-B",
                "-m",
                "wealthy_server.local_runtime",
                *arguments,
            )
        )

    shared_service = (
        f"{MANAGED_MARKER}\n"
        "[Service]\n"
        f"WorkingDirectory={working_directory}\n"
        "UMask=0077\n"
        "UnsetEnvironment=PYTHONPATH AMENT_PREFIX_PATH\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n"
    )
    web_command = command(
        "serve",
        "--data-dir",
        str(runtime_data),
        "--market-database",
        str(market_db),
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    )

    def task_service(task: str) -> str:
        task_command = command(
            "run-task",
            task,
            "--data-dir",
            str(runtime_data),
            "--market-database",
            str(market_db),
        )
        return (
            f"{MANAGED_MARKER}\n"
            "[Unit]\n"
            f"Description=Wealthy local {task} task (read-only data, no orders)\n"
            "Wants=network-online.target\n"
            "After=network-online.target wealthy-local-web.service\n\n"
            + shared_service.split("\n", 1)[1]
            + "Type=oneshot\n"
            + f"ExecStart={task_command}\n"
            + "TimeoutStartSec=45min\n"
        )

    def timer(description: str, service_name: str, local_time: str) -> str:
        return (
            f"{MANAGED_MARKER}\n"
            "[Unit]\n"
            f"Description={description}\n\n"
            "[Timer]\n"
            f"OnCalendar=*-*-* {local_time}:00 {TIME_ZONE}\n"
            "Persistent=true\n"
            "AccuracySec=1min\n"
            "RandomizedDelaySec=0\n"
            f"Unit={service_name}\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        )

    return {
        WEB_SERVICE: (
            f"{MANAGED_MARKER}\n"
            "[Unit]\n"
            "Description=Wealthy local web application (loopback only)\n"
            "Wants=network-online.target\n"
            "After=network-online.target\n\n"
            + shared_service.split("\n", 1)[1]
            + "Type=simple\n"
            + f"ExecStart={web_command}\n"
            + "Restart=on-failure\n"
            + "RestartSec=5s\n"
            + "TimeoutStopSec=30s\n\n"
            + "[Install]\n"
            + "WantedBy=default.target\n"
        ),
        COLLECT_SERVICE: task_service("collect"),
        COLLECT_TIMER: timer(
            "Run Wealthy local collection at 15:00 Asia/Seoul",
            COLLECT_SERVICE,
            "15:00",
        ),
        REPORT_SERVICE: task_service("report"),
        REPORT_TIMER: timer(
            "Run Wealthy local report at 16:00 Asia/Seoul",
            REPORT_SERVICE,
            "16:00",
        ),
    }


def _run_systemctl(
    arguments: Sequence[str],
    *,
    runner: Runner,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            ["systemctl", "--user", *arguments],
            check=check,
            shell=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if check and result.returncode != 0:
            raise LocalServiceError(
                "사용자 서비스 상태를 안전하게 확인할 수 없습니다. 운영체제 오류는 표시하지 않습니다."
            )
        return result
    except (OSError, subprocess.SubprocessError):
        raise LocalServiceError(
            "사용자 서비스 상태를 안전하게 확인할 수 없습니다. 운영체제 오류는 표시하지 않습니다."
        ) from None


def _runtime_true(unit: str, operation: str, *, runner: Runner) -> bool:
    result = _run_systemctl((operation, unit), runner=runner, check=False)
    return result.returncode == 0


def _present(unit_dir: Path, names: Sequence[str]) -> tuple[str, ...]:
    return tuple(name for name in names if (unit_dir / name).is_file())


def _runtime_conflicts(*, runner: Runner) -> tuple[bool, bool]:
    legacy = any(
        _runtime_true(name, operation, runner=runner)
        for name in LEGACY_UNIT_NAMES
        for operation in ("is-enabled", "is-active")
    )
    local = any(
        _runtime_true(name, operation, runner=runner)
        for name in UNIT_NAMES
        for operation in ("is-enabled", "is-active")
    )
    return legacy, local


def _safe_unit_dir(path: Path, *, create: bool) -> Path:
    message = "사용자 서비스 폴더를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다."
    try:
        expanded = path.expanduser()
        if any(character in str(expanded) for character in ("\x00", "\n", "\r")):
            raise ValueError
        if create:
            expanded.mkdir(parents=True, exist_ok=True)
        resolved = expanded.resolve(strict=True)
        if not resolved.is_dir() or expanded.is_symlink():
            raise OSError
    except (OSError, RuntimeError, ValueError):
        raise LocalServiceError(message) from None
    return resolved


def inspect_status(
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
) -> LocalServiceStatus:
    if (platform_name or sys.platform) != "linux":
        return LocalServiceStatus(
            installed=False,
            enabled=None,
            active=None,
            conflict=False,
            detail="Ubuntu/Linux 사용자 서비스 상태가 아닙니다.",
        )
    destination = unit_dir or _default_unit_dir()
    try:
        destination = _safe_unit_dir(destination, create=False)
    except LocalServiceError:
        try:
            destination_exists = destination.expanduser().exists()
        except (OSError, RuntimeError, ValueError):
            raise LocalServiceError(
                "사용자 서비스 폴더를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다."
            ) from None
        if destination_exists:
            raise
        destination = destination.expanduser().resolve(strict=False)

    present = _present(destination, UNIT_NAMES)
    legacy_files = _present(destination, LEGACY_UNIT_NAMES)
    try:
        legacy_runtime, local_runtime = _runtime_conflicts(runner=runner)
    except LocalServiceError:
        return LocalServiceStatus(
            installed=len(present) == len(UNIT_NAMES),
            enabled=None,
            active=None,
            conflict=bool(legacy_files) or (0 < len(present) < len(UNIT_NAMES)),
            detail="단위 파일은 확인했으나 systemd 실행 상태는 확인할 수 없습니다.",
            present_units=present,
        )
    if legacy_files or legacy_runtime:
        return LocalServiceStatus(
            installed=len(present) == len(UNIT_NAMES),
            enabled=None,
            active=None,
            conflict=True,
            detail="기존 데스크톱 예약 작업이 있어 중복 실행 방지 확인이 필요합니다.",
            present_units=present,
        )
    if 0 < len(present) < len(UNIT_NAMES):
        return LocalServiceStatus(
            installed=False,
            enabled=None,
            active=None,
            conflict=True,
            detail="로컬 웹 사용자 단위가 일부만 존재합니다. 자동으로 덮어쓰지 않습니다.",
            present_units=present,
        )
    if not present:
        return LocalServiceStatus(
            installed=False,
            enabled=False,
            active=False,
            conflict=local_runtime,
            detail=(
                "단위 파일 없이 동일 이름 서비스 상태가 남아 있습니다. 확인이 필요합니다."
                if local_runtime
                else "미등록"
            ),
        )
    enabled = all(
        _runtime_true(name, "is-enabled", runner=runner)
        for name in ENABLED_UNIT_NAMES
    )
    active = all(
        _runtime_true(name, "is-active", runner=runner)
        for name in ENABLED_UNIT_NAMES
    )
    return LocalServiceStatus(
        installed=True,
        enabled=enabled,
        active=active,
        conflict=False,
        detail=(
            f"등록됨: 활성화={'예' if enabled else '아니요'}, "
            f"실행/대기={'예' if active else '아니요'}"
        ),
        present_units=present,
    )


def install(
    project_root: Path,
    python_executable: Path,
    data_dir: Path,
    market_database: Path,
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
    dry_run: bool = False,
) -> LocalServiceStatus:
    if (platform_name or sys.platform) != "linux":
        raise LocalServiceError("Ubuntu/Linux 사용자 서비스 설치만 지원합니다.")
    units = generate_units(project_root, python_executable, data_dir, market_database)
    raw_destination = unit_dir or _default_unit_dir()
    try:
        destination_exists = raw_destination.expanduser().exists()
    except (OSError, RuntimeError, ValueError):
        raise LocalServiceError(
            "사용자 서비스 폴더를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다."
        ) from None
    if destination_exists:
        destination = _safe_unit_dir(raw_destination, create=False)
    else:
        destination = _future_path(raw_destination, directory=True)

    present = _present(destination, UNIT_NAMES)
    legacy_files = _present(destination, LEGACY_UNIT_NAMES)
    legacy_runtime, local_runtime = _runtime_conflicts(runner=runner)
    if legacy_files or legacy_runtime:
        raise LocalServiceError(
            "기존 데스크톱 예약 작업이 있어 설치하지 않았습니다. 기존 작업은 변경하지 않았습니다."
        )
    if present or local_runtime:
        raise LocalServiceError(
            "같은 이름의 로컬 웹 사용자 단위가 이미 있거나 실행 중입니다. 자동으로 덮어쓰지 않습니다."
        )
    if dry_run:
        return LocalServiceStatus(
            installed=False,
            enabled=False,
            active=False,
            conflict=False,
            detail=f"DRY RUN: {len(units)}개 단위 검증 완료, 파일·systemd 변경 없음",
        )

    destination = _safe_unit_dir(raw_destination, create=True)
    written: list[Path] = []
    try:
        for name in UNIT_NAMES:
            destination_path = destination / name
            _atomic_write_text(destination_path, units[name])
            written.append(destination_path)
        _run_systemctl(("daemon-reload",), runner=runner, check=True)
        _run_systemctl(
            ("enable", "--now", *ENABLED_UNIT_NAMES),
            runner=runner,
            check=True,
        )
    except (LocalServiceError, SchedulerError, OSError):
        try:
            _run_systemctl(
                ("disable", "--now", *ENABLED_UNIT_NAMES),
                runner=runner,
                check=False,
            )
        except LocalServiceError:
            pass
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            _run_systemctl(("daemon-reload",), runner=runner, check=False)
        except LocalServiceError:
            pass
        raise LocalServiceError(
            "로컬 웹 사용자 서비스를 안전하게 설치할 수 없습니다. 부분 설치는 정리했습니다."
        ) from None
    return LocalServiceStatus(
        installed=True,
        enabled=True,
        active=True,
        conflict=False,
        detail=(
            "등록됨: 로컬 웹 127.0.0.1:8000, 매일 15:00 수집·16:00 보고 "
            f"({TIME_ZONE})"
        ),
        present_units=UNIT_NAMES,
    )


def uninstall(
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
) -> LocalServiceStatus:
    if (platform_name or sys.platform) != "linux":
        raise LocalServiceError("Ubuntu/Linux 사용자 서비스 해제만 지원합니다.")
    raw_destination = unit_dir or _default_unit_dir()
    try:
        destination = _safe_unit_dir(raw_destination, create=False)
    except LocalServiceError:
        try:
            destination_exists = raw_destination.expanduser().exists()
        except (OSError, RuntimeError, ValueError):
            raise LocalServiceError(
                "사용자 서비스 폴더를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다."
            ) from None
        if not destination_exists:
            return LocalServiceStatus(False, False, False, False, "미등록")
        raise
    present = _present(destination, UNIT_NAMES)
    if not present:
        legacy_files = _present(destination, LEGACY_UNIT_NAMES)
        legacy_runtime, local_runtime = _runtime_conflicts(runner=runner)
        if legacy_files or legacy_runtime or local_runtime:
            raise LocalServiceError(
                "소유권을 확인할 수 없는 예약 작업 상태가 있어 자동으로 변경하지 않았습니다."
            )
        return LocalServiceStatus(False, False, False, False, "미등록")
    for name in present:
        try:
            with (destination / name).open(encoding="utf-8") as unit_file:
                first_line = unit_file.readline().rstrip("\n")
        except (OSError, UnicodeError):
            raise LocalServiceError(
                "사용자 단위 소유권을 확인할 수 없어 해제하지 않았습니다."
            ) from None
        if first_line != MANAGED_MARKER:
            raise LocalServiceError(
                "도구가 관리하지 않는 같은 이름의 단위가 있어 해제하지 않았습니다."
            )
    _run_systemctl(
        ("disable", "--now", *ENABLED_UNIT_NAMES), runner=runner, check=True
    )
    try:
        for name in present:
            (destination / name).unlink()
    except OSError:
        raise LocalServiceError(
            "로컬 웹 사용자 단위를 안전하게 제거할 수 없습니다."
        ) from None
    _run_systemctl(("daemon-reload",), runner=runner, check=True)
    return LocalServiceStatus(
        installed=False,
        enabled=False,
        active=False,
        conflict=False,
        detail="해제됨: 데이터베이스·보고서·기존 데스크톱 예약 작업은 삭제하지 않음",
    )


def build_parser() -> _SafeArgumentParser:
    parser = _SafeArgumentParser(
        prog="wealthy-local-service",
        description="Ubuntu 사용자 수준 Wealthy 로컬 웹·15시/16시 작업을 관리합니다.",
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=_SafeArgumentParser
    )
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    install_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    install_parser.add_argument("--data-dir", type=Path, default=_default_data_dir())
    install_parser.add_argument(
        "--market-database", type=Path, default=_default_market_database()
    )
    install_parser.add_argument("--unit-dir", type=Path)
    install_parser.add_argument("--dry-run", action="store_true")

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--unit-dir", type=Path)

    uninstall_parser = subparsers.add_parser("uninstall")
    uninstall_parser.add_argument("--unit-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            status = install(
                args.project_root,
                args.python,
                args.data_dir,
                args.market_database,
                unit_dir=args.unit_dir,
                dry_run=args.dry_run,
            )
        elif args.command == "status":
            status = inspect_status(unit_dir=args.unit_dir)
        else:
            status = uninstall(unit_dir=args.unit_dir)
    except (LocalServiceError, SchedulerError) as exc:
        sys.stderr.write(f"로컬 서비스 오류: {exc}\n")
        return 2
    sys.stdout.write(status.detail + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
