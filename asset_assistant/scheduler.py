"""사용자 수준의 정기 수집·보고 작업을 안전하게 등록합니다.

예약기는 시장 자료 수집이나 투자 판단을 직접 수행하지 않습니다. 15:00 수집과
16:00 보고서 생성을 ``asset_assistant.automation``에 위임하며 주문 경로는 없습니다.
Ubuntu에서는 systemd 사용자 타이머를 설치할 수 있고, Windows에서는 실제 Windows에서
검토·등록할 수 있는 작업 스케줄러 XML 명세만 생성합니다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from xml.etree import ElementTree

from .cli import _SafeArgumentParser


TIME_ZONE = "Asia/Seoul"
REVIEW_TIME = "15:00"
REPORT_TIME = "16:00"
PC_OFF_CAVEAT = (
    "PC가 꺼진 동안에는 실행되지 않습니다. Ubuntu 타이머는 다음 사용자 로그인 또는 "
    "사용자 타이머 활성화 뒤 놓친 실행을 한 번 처리하도록 요청하지만, 상시 실행을 "
    "보장하지 않습니다."
)
DUPLICATE_EXECUTION_OWNER = "asset_assistant.automation의 작업 임대(lease)"

COLLECT_SERVICE = "asset-assistant-collect.service"
COLLECT_TIMER = "asset-assistant-collect.timer"
REPORT_SERVICE = "asset-assistant-report.service"
REPORT_TIMER = "asset-assistant-report.timer"
SYSTEMD_UNIT_NAMES = (
    COLLECT_SERVICE,
    COLLECT_TIMER,
    REPORT_SERVICE,
    REPORT_TIMER,
)

WINDOWS_COLLECT_TASK = "LongTermAssetAssistant-Collect"
WINDOWS_REPORT_TASK = "LongTermAssetAssistant-Report"


class SchedulerError(RuntimeError):
    """경로·외부 명령 원문을 공개하지 않는 예약 작업 오류."""


@dataclass(frozen=True)
class SchedulerStatus:
    installed: bool
    enabled: bool | None
    active: bool | None
    detail: str
    pc_off_caveat: str = PC_OFF_CAVEAT


@dataclass(frozen=True)
class WindowsTaskSpec:
    """Windows에서 사용자가 직접 등록할 작업 스케줄러 명세."""

    task_name: str
    file_name: str
    local_time: str
    xml: str
    create_argv: tuple[str, ...]
    delete_argv: tuple[str, ...]
    query_argv: tuple[str, ...]


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _validated_absolute_path(value: Path, *, must_be_directory: bool) -> Path:
    try:
        resolved = value.expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise SchedulerError(
            "예약 작업 경로를 안전하게 확인할 수 없습니다. 원본 경로는 표시하지 않습니다."
        ) from None
    if "\x00" in str(resolved) or "\n" in str(resolved) or "\r" in str(resolved):
        raise SchedulerError(
            "예약 작업 경로를 안전하게 확인할 수 없습니다. 원본 경로는 표시하지 않습니다."
        )
    if must_be_directory and not resolved.is_dir():
        raise SchedulerError(
            "예약 작업 경로를 안전하게 확인할 수 없습니다. 원본 경로는 표시하지 않습니다."
        )
    if not must_be_directory and not resolved.is_file():
        raise SchedulerError(
            "예약 작업 경로를 안전하게 확인할 수 없습니다. 원본 경로는 표시하지 않습니다."
        )
    return resolved


def _systemd_quote(value: str) -> str:
    """systemd 단위 파일의 단일 토큰을 인용합니다."""

    if any(character in value for character in ("\x00", "\n", "\r")):
        raise SchedulerError(
            "예약 작업 경로를 안전하게 처리할 수 없습니다. 원본 경로는 표시하지 않습니다."
        )
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def _systemd_path_value(value: str) -> str:
    """WorkingDirectory처럼 따옴표가 경로 문자가 되는 지시문용 값."""

    if any(character in value for character in ("\x00", "\n", "\r")):
        raise SchedulerError(
            "예약 작업 경로를 안전하게 처리할 수 없습니다. 원본 경로는 표시하지 않습니다."
        )
    return value.replace("%", "%%")


def generate_systemd_user_units(
    project_root: Path,
    python_executable: Path,
) -> dict[str, str]:
    """Ubuntu systemd 사용자 서비스·타이머 네 개의 내용을 만듭니다."""

    root = _validated_absolute_path(project_root, must_be_directory=True)
    python = _validated_absolute_path(python_executable, must_be_directory=False)
    working_directory = _systemd_path_value(str(root))

    def service(description: str, action: str) -> str:
        command = " ".join(
            _systemd_quote(token)
            for token in (
                str(python),
                "-B",
                "-m",
                "asset_assistant.automation",
                action,
                "--scheduled",
            )
        )
        return (
            "[Unit]\n"
            f"Description={description}\n"
            "After=network-online.target\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"WorkingDirectory={working_directory}\n"
            "UnsetEnvironment=PYTHONPATH AMENT_PREFIX_PATH\n"
            f"ExecStart={command}\n"
            "NoNewPrivileges=true\n"
            "PrivateTmp=true\n"
            "TimeoutStartSec=30min\n"
        )

    def timer(description: str, service_name: str, local_time: str) -> str:
        return (
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
        COLLECT_SERVICE: service(
            "Long-term asset assistant market data review (no orders)", "collect"
        ),
        COLLECT_TIMER: timer(
            "Run long-term asset assistant review at 15:00 Asia/Seoul",
            COLLECT_SERVICE,
            REVIEW_TIME,
        ),
        REPORT_SERVICE: service(
            "Long-term asset assistant scheduled report (no orders)", "report"
        ),
        REPORT_TIMER: timer(
            "Create long-term asset assistant report at 16:00 Asia/Seoul",
            REPORT_SERVICE,
            REPORT_TIME,
        ),
    }


def _default_systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _run_systemctl(
    arguments: Sequence[str],
    *,
    runner: Runner,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            ["systemctl", "--user", *arguments],
            check=check,
            shell=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        raise SchedulerError(
            "사용자 예약 작업 명령을 완료할 수 없습니다. 운영체제 오류 원문은 표시하지 않습니다."
        ) from None


def _atomic_write_text(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o644)
        temporary_path.replace(destination)
    except (OSError, RuntimeError, ValueError):
        raise SchedulerError(
            "예약 작업 파일을 안전하게 저장할 수 없습니다. 원본 경로는 표시하지 않습니다."
        ) from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def install_systemd_user(
    project_root: Path,
    python_executable: Path,
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
) -> SchedulerStatus:
    """systemd 사용자 타이머를 설치하고 활성화합니다."""

    if (platform_name or sys.platform) != "linux":
        raise SchedulerError("Ubuntu/Linux 사용자 예약 설치만 지원합니다.")
    units = generate_systemd_user_units(project_root, python_executable)
    destination = unit_dir or _default_systemd_user_dir()
    try:
        destination.mkdir(parents=True, exist_ok=True)
        if not destination.is_dir():
            raise OSError
    except (OSError, RuntimeError, ValueError):
        raise SchedulerError(
            "예약 작업 폴더를 안전하게 준비할 수 없습니다. 원본 경로는 표시하지 않습니다."
        ) from None

    for name, content in units.items():
        _atomic_write_text(destination / name, content)

    _run_systemctl(("daemon-reload",), runner=runner, check=True)
    _run_systemctl(
        ("enable", "--now", COLLECT_TIMER, REPORT_TIMER),
        runner=runner,
        check=True,
    )
    return SchedulerStatus(
        installed=True,
        enabled=True,
        active=True,
        detail=(
            f"등록됨: 매일 {REVIEW_TIME} 검토, {REPORT_TIME} 보고서 생성 "
            f"({TIME_ZONE}); 중복 판정은 {DUPLICATE_EXECUTION_OWNER}에서 수행"
        ),
    )


def uninstall_systemd_user(
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
) -> SchedulerStatus:
    """고정된 네 개 단위 파일만 비활성화하고 제거합니다."""

    if (platform_name or sys.platform) != "linux":
        raise SchedulerError("Ubuntu/Linux 사용자 예약 해제만 지원합니다.")
    destination = unit_dir or _default_systemd_user_dir()
    _run_systemctl(
        ("disable", "--now", COLLECT_TIMER, REPORT_TIMER),
        runner=runner,
        check=False,
    )
    try:
        for name in SYSTEMD_UNIT_NAMES:
            (destination / name).unlink(missing_ok=True)
    except (OSError, RuntimeError, ValueError):
        raise SchedulerError(
            "예약 작업 파일을 안전하게 제거할 수 없습니다. 원본 경로는 표시하지 않습니다."
        ) from None
    _run_systemctl(("daemon-reload",), runner=runner, check=True)
    return SchedulerStatus(
        installed=False,
        enabled=False,
        active=False,
        detail="해제됨: 앱 데이터와 보고서는 삭제하지 않음",
    )


def systemd_user_status(
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
) -> SchedulerStatus:
    """단위 파일과 systemd 활성 상태를 읽기 전용으로 확인합니다."""

    if (platform_name or sys.platform) != "linux":
        return SchedulerStatus(
            installed=False,
            enabled=None,
            active=None,
            detail="Ubuntu/Linux systemd 사용자 타이머 상태가 아님",
        )
    destination = unit_dir or _default_systemd_user_dir()
    present_units = tuple(
        name for name in SYSTEMD_UNIT_NAMES if (destination / name).is_file()
    )
    if not present_units:
        return SchedulerStatus(
            installed=False,
            enabled=False,
            active=False,
            detail="미등록",
        )
    if len(present_units) != len(SYSTEMD_UNIT_NAMES):
        return SchedulerStatus(
            installed=False,
            enabled=None,
            active=None,
            detail="예약 단위 파일 일부만 존재함 · 부분 등록/확인 필요",
        )

    try:
        enabled_result = _run_systemctl(
            ("is-enabled", COLLECT_TIMER, REPORT_TIMER), runner=runner, check=False
        )
        active_result = _run_systemctl(
            ("is-active", COLLECT_TIMER, REPORT_TIMER), runner=runner, check=False
        )
    except SchedulerError:
        return SchedulerStatus(
            installed=True,
            enabled=None,
            active=None,
            detail="단위 파일은 있으나 systemd 활성 상태는 확인 불가",
        )
    enabled = enabled_result.returncode == 0
    active = active_result.returncode == 0
    return SchedulerStatus(
        installed=True,
        enabled=enabled,
        active=active,
        detail=(
            f"등록됨: 활성화={'예' if enabled else '아니요'}, "
            f"대기 중={'예' if active else '아니요'}"
        ),
    )


def windows_task_status(
    *,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
) -> SchedulerStatus:
    """고정된 Windows 작업 두 개의 등록 여부만 읽기 전용으로 질의합니다.

    ``schtasks /Query``의 표 출력은 Windows 표시 언어에 따라 달라집니다. 성공
    응답만으로 작업의 Enabled 설정이나 현재 실행 상태를 추정하지 않습니다.
    """

    if (platform_name or sys.platform) != "win32":
        return SchedulerStatus(
            installed=False,
            enabled=None,
            active=None,
            detail="Windows 작업 스케줄러 상태가 아님",
        )
    found: list[bool] = []
    for name in (WINDOWS_COLLECT_TASK, WINDOWS_REPORT_TASK):
        try:
            result = runner(
                ["schtasks.exe", "/Query", "/TN", name],
                check=False,
                shell=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return SchedulerStatus(
                installed=False,
                enabled=None,
                active=None,
                detail="Windows 예약 등록 상태를 확인할 수 없음",
            )
        found.append(result.returncode == 0)
    if all(found):
        detail = "두 예약 작업 등록 확인; 활성화와 실행 상태는 확인 필요"
    elif any(found):
        detail = "예약 작업이 일부만 등록됨; 활성화와 실행 상태는 확인 필요"
    else:
        detail = "두 예약 작업의 등록을 확인하지 못함"
    return SchedulerStatus(
        installed=all(found),
        enabled=None,
        active=None,
        detail=detail,
    )


def scheduler_status(
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
    platform_name: str | None = None,
) -> SchedulerStatus:
    """현재 OS에 맞는 예약 등록 상태를 읽기 전용으로 확인합니다."""

    platform = platform_name or sys.platform
    if platform == "linux":
        return systemd_user_status(
            unit_dir=unit_dir,
            runner=runner,
            platform_name=platform,
        )
    if platform == "win32":
        return windows_task_status(runner=runner, platform_name=platform)
    return SchedulerStatus(
        installed=False,
        enabled=None,
        active=None,
        detail="이 운영체제의 예약 상태 확인은 지원하지 않음",
    )


def _windows_task_xml(
    *,
    task_name: str,
    local_time: str,
    action: str,
    project_root: Path,
    python_executable: Path,
) -> str:
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ElementTree.register_namespace("", namespace)
    task = ElementTree.Element(f"{{{namespace}}}Task", {"version": "1.4"})
    registration = ElementTree.SubElement(task, f"{{{namespace}}}RegistrationInfo")
    ElementTree.SubElement(registration, f"{{{namespace}}}Description").text = (
        f"Long-term asset assistant {action}; data/report only, no orders"
    )
    triggers = ElementTree.SubElement(task, f"{{{namespace}}}Triggers")
    trigger = ElementTree.SubElement(triggers, f"{{{namespace}}}CalendarTrigger")
    ElementTree.SubElement(trigger, f"{{{namespace}}}StartBoundary").text = (
        f"2026-01-01T{local_time}:00+09:00"
    )
    ElementTree.SubElement(trigger, f"{{{namespace}}}Enabled").text = "true"
    schedule = ElementTree.SubElement(trigger, f"{{{namespace}}}ScheduleByDay")
    ElementTree.SubElement(schedule, f"{{{namespace}}}DaysInterval").text = "1"
    settings = ElementTree.SubElement(task, f"{{{namespace}}}Settings")
    ElementTree.SubElement(settings, f"{{{namespace}}}MultipleInstancesPolicy").text = (
        "IgnoreNew"
    )
    ElementTree.SubElement(settings, f"{{{namespace}}}StartWhenAvailable").text = "true"
    ElementTree.SubElement(settings, f"{{{namespace}}}DisallowStartIfOnBatteries").text = (
        "false"
    )
    ElementTree.SubElement(settings, f"{{{namespace}}}StopIfGoingOnBatteries").text = (
        "false"
    )
    ElementTree.SubElement(settings, f"{{{namespace}}}ExecutionTimeLimit").text = "PT30M"
    actions = ElementTree.SubElement(task, f"{{{namespace}}}Actions")
    execute = ElementTree.SubElement(actions, f"{{{namespace}}}Exec")
    ElementTree.SubElement(execute, f"{{{namespace}}}Command").text = str(
        python_executable
    )
    ElementTree.SubElement(execute, f"{{{namespace}}}Arguments").text = (
        f"-B -m asset_assistant.automation {action} --scheduled"
    )
    ElementTree.SubElement(execute, f"{{{namespace}}}WorkingDirectory").text = str(
        project_root
    )
    body = ElementTree.tostring(task, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def generate_windows_task_specs(
    project_root: Path,
    python_executable: Path,
) -> tuple[WindowsTaskSpec, WindowsTaskSpec]:
    """실제 등록하지 않고 Windows 작업 XML과 명령 인자 명세를 만듭니다."""

    root = _validated_absolute_path(project_root, must_be_directory=True)
    python = _validated_absolute_path(python_executable, must_be_directory=False)
    definitions = (
        (WINDOWS_COLLECT_TASK, "asset-assistant-collect.xml", REVIEW_TIME, "collect"),
        (WINDOWS_REPORT_TASK, "asset-assistant-report.xml", REPORT_TIME, "report"),
    )
    specs: list[WindowsTaskSpec] = []
    for task_name, file_name, local_time, action in definitions:
        specs.append(
            WindowsTaskSpec(
                task_name=task_name,
                file_name=file_name,
                local_time=local_time,
                xml=_windows_task_xml(
                    task_name=task_name,
                    local_time=local_time,
                    action=action,
                    project_root=root,
                    python_executable=python,
                ),
                create_argv=(
                    "schtasks.exe",
                    "/Create",
                    "/TN",
                    task_name,
                    "/XML",
                    file_name,
                    "/F",
                ),
                delete_argv=("schtasks.exe", "/Delete", "/TN", task_name, "/F"),
                query_argv=("schtasks.exe", "/Query", "/TN", task_name),
            )
        )
    return specs[0], specs[1]


def write_windows_task_specs(
    project_root: Path,
    python_executable: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Windows XML 명세만 저장하며 작업을 등록하거나 실행하지 않습니다."""

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if not output_dir.is_dir():
            raise OSError
    except (OSError, RuntimeError, ValueError):
        raise SchedulerError(
            "Windows 예약 명세 폴더를 준비할 수 없습니다. 원본 경로는 표시하지 않습니다."
        ) from None
    paths: list[Path] = []
    for spec in generate_windows_task_specs(project_root, python_executable):
        destination = output_dir / spec.file_name
        _atomic_write_text(destination, spec.xml)
        paths.append(destination)
    return paths[0], paths[1]


def build_parser() -> _SafeArgumentParser:
    parser = _SafeArgumentParser(
        prog="asset-assistant-scheduler",
        description=(
            "Asia/Seoul 15:00 자료 검토와 16:00 보고서 생성을 사용자 수준으로 예약합니다."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_SafeArgumentParser,
    )

    install = subparsers.add_parser("install")
    install.add_argument("--project-root", type=Path, default=Path.cwd())
    install.add_argument("--python", type=Path, default=Path(sys.executable))
    install.add_argument("--unit-dir", type=Path)

    uninstall = subparsers.add_parser("uninstall")
    uninstall.add_argument("--unit-dir", type=Path)

    status = subparsers.add_parser("status")
    status.add_argument("--unit-dir", type=Path)

    windows = subparsers.add_parser("windows-spec")
    windows.add_argument("--project-root", type=Path, default=Path.cwd())
    windows.add_argument("--python", type=Path, default=Path(sys.executable))
    windows.add_argument("--output-dir", type=Path, required=True)
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            status = install_systemd_user(
                args.project_root,
                args.python,
                unit_dir=args.unit_dir,
            )
        elif args.command == "uninstall":
            status = uninstall_systemd_user(unit_dir=args.unit_dir)
        elif args.command == "status":
            status = scheduler_status(unit_dir=args.unit_dir)
        else:
            write_windows_task_specs(
                args.project_root,
                args.python,
                args.output_dir,
            )
            status = SchedulerStatus(
                installed=False,
                enabled=None,
                active=None,
                detail=(
                    "Windows 작업 XML 명세만 생성함(NOT RUN). 실제 Windows에서 검토 후 "
                    "schtasks.exe로 등록해야 함"
                ),
            )
    except SchedulerError:
        parser.exit(
            2,
            "예약 오류: 작업을 안전하게 완료할 수 없습니다. "
            "원본 인수·경로·운영체제 오류는 표시하지 않습니다.\n",
        )
    sys.stdout.write(status.detail + "\n" + status.pc_off_caveat + "\n")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
