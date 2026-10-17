from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from asset_assistant.scheduler import (
    COLLECT_SERVICE,
    COLLECT_TIMER,
    PC_OFF_CAVEAT,
    REPORT_SERVICE,
    REPORT_TIMER,
    REVIEW_TIME,
    SchedulerError,
    WINDOWS_COLLECT_TASK,
    WINDOWS_REPORT_TASK,
    generate_systemd_user_units,
    generate_windows_task_specs,
    install_systemd_user,
    run,
    scheduler_status,
    systemd_user_status,
    uninstall_systemd_user,
    windows_task_status,
    write_windows_task_specs,
)


class FakeRunner:
    def __init__(self, returncodes: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.returncodes = returncodes or {}

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        operation = argv[2] if len(argv) > 2 else ""
        return subprocess.CompletedProcess(
            argv,
            self.returncodes.get(operation, 0),
            stdout="enabled\n",
            stderr="",
        )


class FakeWindowsRunner:
    def __init__(self, returncodes: tuple[int, int] = (0, 0)) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.returncodes = returncodes

    def __call__(
        self, argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            self.returncodes[len(self.calls) - 1],
            stdout="private/path credential-value",
            stderr="private/path credential-value",
        )


class SchedulerTests(unittest.TestCase):
    def _layout(self, parent: Path) -> tuple[Path, Path]:
        root = parent / "한글 project with spaces"
        python = root / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("not an executable fixture", encoding="utf-8")
        return root, python

    def test_systemd_units_use_exact_paths_timezone_and_persistent_timers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, python = self._layout(Path(directory))
            units = generate_systemd_user_units(root, python)

        self.assertEqual(
            set(units),
            {COLLECT_SERVICE, COLLECT_TIMER, REPORT_SERVICE, REPORT_TIMER},
        )
        self.assertIn(f"WorkingDirectory={root.resolve()}", units[COLLECT_SERVICE])
        self.assertIn(f'ExecStart="{python.resolve()}"', units[COLLECT_SERVICE])
        self.assertIn('"asset_assistant.automation" "collect"', units[COLLECT_SERVICE])
        self.assertIn('"asset_assistant.automation" "report"', units[REPORT_SERVICE])
        self.assertIn('"collect" "--scheduled"', units[COLLECT_SERVICE])
        self.assertIn('"report" "--scheduled"', units[REPORT_SERVICE])
        self.assertIn("UnsetEnvironment=PYTHONPATH AMENT_PREFIX_PATH", units[REPORT_SERVICE])
        self.assertIn("OnCalendar=*-*-* 15:00:00 Asia/Seoul", units[COLLECT_TIMER])
        self.assertIn("OnCalendar=*-*-* 16:00:00 Asia/Seoul", units[REPORT_TIMER])
        self.assertIn("Persistent=true", units[COLLECT_TIMER])
        self.assertIn("Persistent=true", units[REPORT_TIMER])
        combined = "\n".join(units.values()).lower()
        self.assertNotIn("order", combined.replace("no orders", ""))
        self.assertNotIn("credential", combined)
        self.assertNotIn("secret", combined)

    def test_invalid_path_is_rejected_without_echo(self) -> None:
        private_fragment = "sensitive-local-fragment"
        with self.assertRaises(SchedulerError) as caught:
            generate_systemd_user_units(
                Path("/path/that/does/not/exist") / private_fragment,
                Path("/also/missing"),
            )
        self.assertNotIn(private_fragment, str(caught.exception))

    def test_install_writes_fixed_units_and_uses_argv_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, python = self._layout(base)
            unit_dir = base / "systemd user"
            runner = FakeRunner()
            status = install_systemd_user(
                root,
                python,
                unit_dir=unit_dir,
                runner=runner,
                platform_name="linux",
            )
            self.assertTrue(status.installed)
            self.assertEqual(
                {path.name for path in unit_dir.iterdir()},
                {COLLECT_SERVICE, COLLECT_TIMER, REPORT_SERVICE, REPORT_TIMER},
            )
            self.assertEqual(runner.calls[0][0], ["systemctl", "--user", "daemon-reload"])
            self.assertEqual(
                runner.calls[1][0],
                [
                    "systemctl",
                    "--user",
                    "enable",
                    "--now",
                    COLLECT_TIMER,
                    REPORT_TIMER,
                ],
            )
            for _argv, kwargs in runner.calls:
                self.assertIs(kwargs["shell"], False)
                self.assertTrue(kwargs["capture_output"])
                self.assertEqual(kwargs["timeout"], 20)
            self.assertIn(REVIEW_TIME, status.detail)
            self.assertEqual(status.pc_off_caveat, PC_OFF_CAVEAT)

    def test_uninstall_removes_only_owned_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            for name in (COLLECT_SERVICE, COLLECT_TIMER, REPORT_SERVICE, REPORT_TIMER):
                (unit_dir / name).write_text("fixture", encoding="utf-8")
            unrelated = unit_dir / "unrelated.timer"
            unrelated.write_text("keep", encoding="utf-8")
            runner = FakeRunner()
            status = uninstall_systemd_user(
                unit_dir=unit_dir,
                runner=runner,
                platform_name="linux",
            )
            self.assertFalse(status.installed)
            self.assertTrue(unrelated.is_file())
            for name in (COLLECT_SERVICE, COLLECT_TIMER, REPORT_SERVICE, REPORT_TIMER):
                self.assertFalse((unit_dir / name).exists())
            self.assertEqual(runner.calls[0][0][2:4], ["disable", "--now"])

    def test_status_distinguishes_files_enabled_and_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            absent = systemd_user_status(
                unit_dir=unit_dir,
                runner=FakeRunner(),
                platform_name="linux",
            )
            self.assertFalse(absent.installed)
            for name in (COLLECT_SERVICE, COLLECT_TIMER, REPORT_SERVICE, REPORT_TIMER):
                (unit_dir / name).write_text("fixture", encoding="utf-8")
            runner = FakeRunner({"is-active": 3})
            present = systemd_user_status(
                unit_dir=unit_dir,
                runner=runner,
                platform_name="linux",
            )
            self.assertTrue(present.installed)
            self.assertTrue(present.enabled)
            self.assertFalse(present.active)
        self.assertIn("활성화=예", present.detail)
        self.assertIn("대기 중=아니요", present.detail)

    def test_status_reports_partially_present_systemd_units_as_check_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            (unit_dir / COLLECT_TIMER).write_text("fixture", encoding="utf-8")

            status = systemd_user_status(
                unit_dir=unit_dir,
                runner=FakeRunner(),
                platform_name="linux",
            )

        self.assertFalse(status.installed)
        self.assertIsNone(status.enabled)
        self.assertIsNone(status.active)
        self.assertIn("부분 등록", status.detail)
        self.assertIn("확인 필요", status.detail)

    def test_runner_failure_is_generic_and_does_not_leak_stderr(self) -> None:
        class FailingRunner:
            def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                del argv, kwargs
                raise subprocess.CalledProcessError(
                    1,
                    ["systemctl"],
                    stderr="private/path credential-value",
                )

        with tempfile.TemporaryDirectory() as directory:
            root, python = self._layout(Path(directory))
            with self.assertRaises(SchedulerError) as caught:
                install_systemd_user(
                    root,
                    python,
                    unit_dir=Path(directory) / "units",
                    runner=FailingRunner(),
                    platform_name="linux",
                )
        message = str(caught.exception)
        self.assertNotIn("private/path", message)
        self.assertNotIn("credential-value", message)

    def test_windows_specs_are_generated_but_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, python = self._layout(Path(directory))
            specs = generate_windows_task_specs(root, python)
            output_dir = Path(directory) / "windows specs"
            written = write_windows_task_specs(root, python, output_dir)

        self.assertEqual([spec.local_time for spec in specs], ["15:00", "16:00"])
        self.assertEqual([spec.file_name for spec in specs], [path.name for path in written])
        for spec, action in zip(specs, ("collect", "report"), strict=True):
            self.assertIn("StartWhenAvailable>true", spec.xml)
            self.assertIn("MultipleInstancesPolicy>IgnoreNew", spec.xml)
            self.assertIn(f"T{spec.local_time}:00+09:00", spec.xml)
            self.assertIn(str(root.resolve()), spec.xml)
            self.assertIn(str(python.resolve()), spec.xml)
            self.assertIn(f"asset_assistant.automation {action}", spec.xml)
            self.assertIn(f"asset_assistant.automation {action} --scheduled", spec.xml)
            self.assertEqual(spec.create_argv[0], "schtasks.exe")
            self.assertEqual(spec.delete_argv[1], "/Delete")
            self.assertEqual(spec.query_argv[1], "/Query")

    def test_windows_status_queries_only_fixed_tasks_without_shell(self) -> None:
        runner = FakeWindowsRunner()
        status = scheduler_status(runner=runner, platform_name="win32")
        self.assertTrue(status.installed)
        self.assertIsNone(status.enabled)
        self.assertIsNone(status.active)
        self.assertIn("등록 확인", status.detail)
        self.assertEqual(
            [call[0] for call in runner.calls],
            [
                ["schtasks.exe", "/Query", "/TN", WINDOWS_COLLECT_TASK],
                ["schtasks.exe", "/Query", "/TN", WINDOWS_REPORT_TASK],
            ],
        )
        for _argv, kwargs in runner.calls:
            self.assertIs(kwargs["shell"], False)
            self.assertIs(kwargs["check"], False)
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(kwargs["text"])
            self.assertEqual(kwargs["timeout"], 20)
        self.assertNotIn("private/path", status.detail)
        self.assertNotIn("credential-value", status.detail)

    def test_windows_status_partial_missing_and_command_error_are_safe(self) -> None:
        partial = windows_task_status(
            runner=FakeWindowsRunner((0, 1)), platform_name="win32"
        )
        self.assertFalse(partial.installed)
        self.assertIsNone(partial.enabled)
        self.assertIn("일부만 등록", partial.detail)
        missing = windows_task_status(
            runner=FakeWindowsRunner((1, 1)), platform_name="win32"
        )
        self.assertFalse(missing.installed)
        self.assertIn("확인하지 못함", missing.detail)

        def failing_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del argv, kwargs
            raise OSError("private/path credential-value")

        unknown = windows_task_status(runner=failing_runner, platform_name="win32")
        self.assertFalse(unknown.installed)
        self.assertIsNone(unknown.enabled)
        self.assertNotIn("private/path", unknown.detail)
        self.assertNotIn("credential-value", unknown.detail)

    def test_scheduler_status_keeps_linux_systemd_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            for name in (COLLECT_SERVICE, COLLECT_TIMER, REPORT_SERVICE, REPORT_TIMER):
                (unit_dir / name).write_text("fixture", encoding="utf-8")
            runner = FakeRunner({"is-active": 3})
            status = scheduler_status(
                unit_dir=unit_dir, runner=runner, platform_name="linux"
            )
        self.assertTrue(status.installed)
        self.assertTrue(status.enabled)
        self.assertFalse(status.active)
        self.assertEqual(runner.calls[0][0][:3], ["systemctl", "--user", "is-enabled"])

    def test_windows_spec_cli_labels_windows_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, python = self._layout(Path(directory))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = run(
                    [
                        "windows-spec",
                        "--project-root",
                        str(root),
                        "--python",
                        str(python),
                        "--output-dir",
                        str(Path(directory) / "xml"),
                    ]
                )
        self.assertEqual(result, 0)
        self.assertIn("NOT RUN", output.getvalue())
        self.assertIn("PC가 꺼진 동안에는 실행되지 않습니다", output.getvalue())

    def test_safe_parser_does_not_echo_unknown_argument_value(self) -> None:
        sensitive = "credential-tail-456789"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            run(["status", "--unknown", sensitive])
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn(sensitive, stderr.getvalue())
        self.assertNotIn("456789", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
