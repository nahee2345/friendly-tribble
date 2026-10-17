from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from asset_assistant.scheduler import COLLECT_TIMER as LEGACY_COLLECT_TIMER
from tools.web_local_service import (
    COLLECT_SERVICE,
    COLLECT_TIMER,
    ENABLED_UNIT_NAMES,
    LEGACY_UNIT_NAMES,
    MANAGED_MARKER,
    REPORT_SERVICE,
    REPORT_TIMER,
    UNIT_NAMES,
    WEB_SERVICE,
    LocalServiceError,
    generate_units,
    inspect_status,
    install,
    main,
    uninstall,
)


class FakeRunner:
    """Small systemctl double; all probes are absent unless explicitly set."""

    def __init__(
        self,
        states: dict[tuple[str, str], int] | None = None,
        operations: dict[str, int] | None = None,
    ) -> None:
        self.states = states or {}
        self.operations = operations or {}
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        operation = argv[2] if len(argv) > 2 else ""
        if operation in {"is-enabled", "is-active"}:
            unit = argv[3]
            returncode = self.states.get((operation, unit), 1)
        else:
            returncode = self.operations.get(operation, 0)
        if kwargs.get("check") and returncode:
            raise subprocess.CalledProcessError(
                returncode,
                argv,
                stderr="private fixture output that must never be exposed",
            )
        return subprocess.CompletedProcess(
            argv,
            returncode,
            stdout="fixture\n",
            stderr="private fixture output that must never be exposed",
        )


class WebLocalServiceTests(unittest.TestCase):
    def _layout(self, parent: Path) -> tuple[Path, Path, Path, Path]:
        root = parent / "한글 project with spaces"
        python = root / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("python fixture", encoding="utf-8")
        data_dir = parent / "사용자 data with spaces"
        market_database = parent / "market data" / "market.sqlite3"
        return root, python, data_dir, market_database

    def test_units_are_exact_loopback_hardened_and_task_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, python, data_dir, market_database = self._layout(Path(directory))
            units = generate_units(root, python, data_dir, market_database)

        self.assertEqual(set(units), set(UNIT_NAMES))
        self.assertEqual(len(units), 5)
        self.assertIn('"wealthy_server.local_runtime" "serve"', units[WEB_SERVICE])
        self.assertIn('"--host" "127.0.0.1"', units[WEB_SERVICE])
        self.assertIn('"--port" "8000"', units[WEB_SERVICE])
        self.assertNotIn("0.0.0.0", units[WEB_SERVICE])
        self.assertIn(
            '"wealthy_server.local_runtime" "run-task" "collect"',
            units[COLLECT_SERVICE],
        )
        self.assertIn(
            '"wealthy_server.local_runtime" "run-task" "report"',
            units[REPORT_SERVICE],
        )
        for service in (WEB_SERVICE, COLLECT_SERVICE, REPORT_SERVICE):
            content = units[service]
            self.assertIn(f"WorkingDirectory={root.resolve()}", content)
            self.assertIn(f'"--data-dir" "{data_dir.resolve()}"', content)
            self.assertIn(
                f'"--market-database" "{market_database.resolve()}"', content
            )
            self.assertIn("UMask=0077", content)
            self.assertIn("UnsetEnvironment=PYTHONPATH AMENT_PREFIX_PATH", content)
            self.assertIn("NoNewPrivileges=true", content)
            self.assertIn("PrivateTmp=true", content)
        self.assertIn("OnCalendar=*-*-* 15:00:00 Asia/Seoul", units[COLLECT_TIMER])
        self.assertIn("OnCalendar=*-*-* 16:00:00 Asia/Seoul", units[REPORT_TIMER])
        self.assertIn(f"Unit={COLLECT_SERVICE}", units[COLLECT_TIMER])
        self.assertIn(f"Unit={REPORT_SERVICE}", units[REPORT_TIMER])
        self.assertIn("Persistent=true", units[COLLECT_TIMER])
        self.assertIn("Persistent=true", units[REPORT_TIMER])
        combined = "\n".join(units.values()).lower()
        for forbidden in ("credential", "password", "api_key", "token="):
            self.assertNotIn(forbidden, combined)

    def test_invalid_paths_are_rejected_without_echoing_private_value(self) -> None:
        private_fragment = "sensitive-local-fragment"
        with self.assertRaises(LocalServiceError) as caught:
            generate_units(
                Path("/missing") / private_fragment,
                Path("/also-missing"),
                Path("/data"),
                Path("/market.sqlite3"),
            )
        self.assertNotIn(private_fragment, str(caught.exception))

    def test_virtual_environment_python_symlink_is_kept_as_execstart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            launcher = root / ".venv" / "bin" / "python"
            launcher.parent.mkdir(parents=True)
            launcher.symlink_to(Path(sys.executable).resolve())
            units = generate_units(
                root,
                launcher,
                base / "data",
                base / "market" / "market.sqlite3",
            )
        self.assertIn(f'ExecStart="{launcher.absolute()}"', units[WEB_SERVICE])

    def test_install_writes_only_fixed_units_atomically_and_never_uses_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, python, data_dir, market_database = self._layout(base)
            unit_dir = base / "systemd user"
            runner = FakeRunner()
            status = install(
                root,
                python,
                data_dir,
                market_database,
                unit_dir=unit_dir,
                runner=runner,
                platform_name="linux",
            )

            self.assertTrue(status.installed)
            self.assertEqual({path.name for path in unit_dir.iterdir()}, set(UNIT_NAMES))
            for name in UNIT_NAMES:
                self.assertEqual(
                    (unit_dir / name).read_text(encoding="utf-8").splitlines()[0],
                    MANAGED_MARKER,
                )
            self.assertFalse(list(unit_dir.glob("*.tmp")))

        mutating = [call[0] for call in runner.calls if call[0][2] in {"daemon-reload", "enable", "disable"}]
        self.assertEqual(mutating[0], ["systemctl", "--user", "daemon-reload"])
        self.assertEqual(
            mutating[1],
            ["systemctl", "--user", "enable", "--now", *ENABLED_UNIT_NAMES],
        )
        self.assertFalse(any(call[0][0] == "loginctl" for call in runner.calls))
        for argv, kwargs in runner.calls:
            self.assertEqual(argv[:2], ["systemctl", "--user"])
            self.assertIs(kwargs["shell"], False)
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(kwargs["text"])
            self.assertEqual(kwargs["timeout"], 20)

    def test_dry_run_validates_but_changes_no_files_or_systemd_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, python, data_dir, market_database = self._layout(base)
            unit_dir = base / "not-created"
            runner = FakeRunner()
            status = install(
                root,
                python,
                data_dir,
                market_database,
                unit_dir=unit_dir,
                runner=runner,
                platform_name="linux",
                dry_run=True,
            )
            self.assertFalse(unit_dir.exists())
        self.assertFalse(status.installed)
        self.assertIn("DRY RUN", status.detail)
        self.assertFalse(
            any(
                call[0][2] in {"daemon-reload", "enable", "disable"}
                for call in runner.calls
            )
        )

    def test_install_fails_closed_for_legacy_file_and_preserves_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, python, data_dir, market_database = self._layout(base)
            unit_dir = base / "units"
            unit_dir.mkdir()
            legacy = unit_dir / LEGACY_COLLECT_TIMER
            legacy.write_text("legacy fixture", encoding="utf-8")
            runner = FakeRunner()

            with self.assertRaises(LocalServiceError):
                install(
                    root,
                    python,
                    data_dir,
                    market_database,
                    unit_dir=unit_dir,
                    runner=runner,
                    platform_name="linux",
                )

            self.assertEqual(legacy.read_text(encoding="utf-8"), "legacy fixture")
            self.assertFalse(any((unit_dir / name).exists() for name in UNIT_NAMES))
        self.assertFalse(
            any(call[0][2] in {"daemon-reload", "enable", "disable"} for call in runner.calls)
        )

    def test_install_fails_closed_for_runtime_legacy_or_partial_local_unit(self) -> None:
        active_legacy = FakeRunner({("is-active", LEGACY_COLLECT_TIMER): 0})
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, python, data_dir, market_database = self._layout(base)
            legacy_target = base / "legacy conflict"
            with self.assertRaises(LocalServiceError):
                install(
                    root,
                    python,
                    data_dir,
                    market_database,
                    unit_dir=legacy_target,
                    runner=active_legacy,
                    platform_name="linux",
                )
            self.assertFalse(legacy_target.exists())

            partial_dir = base / "partial"
            partial_dir.mkdir()
            partial = partial_dir / WEB_SERVICE
            partial.write_text("leave untouched", encoding="utf-8")
            with self.assertRaises(LocalServiceError):
                install(
                    root,
                    python,
                    data_dir,
                    market_database,
                    unit_dir=partial_dir,
                    runner=FakeRunner(),
                    platform_name="linux",
                )
            self.assertEqual(partial.read_text(encoding="utf-8"), "leave untouched")
            self.assertEqual(list(partial_dir.iterdir()), [partial])

            local_runtime_target = base / "local runtime conflict"
            with self.assertRaises(LocalServiceError):
                install(
                    root,
                    python,
                    data_dir,
                    market_database,
                    unit_dir=local_runtime_target,
                    runner=FakeRunner({("is-active", WEB_SERVICE): 0}),
                    platform_name="linux",
                )
            self.assertFalse(local_runtime_target.exists())

    def test_status_distinguishes_absent_partial_legacy_and_running_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            absent = inspect_status(
                unit_dir=unit_dir, runner=FakeRunner(), platform_name="linux"
            )
            self.assertFalse(absent.installed)
            self.assertFalse(absent.conflict)

            runtime_only = inspect_status(
                unit_dir=unit_dir,
                runner=FakeRunner({("is-active", WEB_SERVICE): 0}),
                platform_name="linux",
            )
            self.assertFalse(runtime_only.installed)
            self.assertTrue(runtime_only.conflict)

            (unit_dir / WEB_SERVICE).write_text("partial", encoding="utf-8")
            partial = inspect_status(
                unit_dir=unit_dir, runner=FakeRunner(), platform_name="linux"
            )
            self.assertFalse(partial.installed)
            self.assertTrue(partial.conflict)
            self.assertIsNone(partial.enabled)
            (unit_dir / WEB_SERVICE).unlink()

            (unit_dir / LEGACY_COLLECT_TIMER).write_text("legacy", encoding="utf-8")
            legacy = inspect_status(
                unit_dir=unit_dir, runner=FakeRunner(), platform_name="linux"
            )
            self.assertTrue(legacy.conflict)
            (unit_dir / LEGACY_COLLECT_TIMER).unlink()

            for name in UNIT_NAMES:
                (unit_dir / name).write_text(MANAGED_MARKER + "\n", encoding="utf-8")
            states = {
                (operation, name): 0
                for operation in ("is-enabled", "is-active")
                for name in ENABLED_UNIT_NAMES
            }
            running = inspect_status(
                unit_dir=unit_dir,
                runner=FakeRunner(states),
                platform_name="linux",
            )
            self.assertTrue(running.installed)
            self.assertTrue(running.enabled)
            self.assertTrue(running.active)
            self.assertFalse(running.conflict)

    def test_uninstall_removes_only_marker_owned_units_and_keeps_data_and_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            unit_dir = base / "units"
            unit_dir.mkdir()
            for name in UNIT_NAMES:
                (unit_dir / name).write_text(MANAGED_MARKER + "\nfixture", encoding="utf-8")
            legacy = unit_dir / LEGACY_COLLECT_TIMER
            legacy.write_text("keep legacy", encoding="utf-8")
            unrelated = unit_dir / "unrelated.timer"
            unrelated.write_text("keep unrelated", encoding="utf-8")
            data_file = base / "wealthy.sqlite3"
            data_file.write_text("keep data", encoding="utf-8")
            runner = FakeRunner()

            status = uninstall(
                unit_dir=unit_dir, runner=runner, platform_name="linux"
            )

            self.assertFalse(status.installed)
            self.assertFalse(any((unit_dir / name).exists() for name in UNIT_NAMES))
            self.assertEqual(legacy.read_text(encoding="utf-8"), "keep legacy")
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep unrelated")
            self.assertEqual(data_file.read_text(encoding="utf-8"), "keep data")
        mutating = [call[0] for call in runner.calls]
        self.assertEqual(
            mutating[0],
            ["systemctl", "--user", "disable", "--now", *ENABLED_UNIT_NAMES],
        )
        self.assertEqual(mutating[1], ["systemctl", "--user", "daemon-reload"])
        flattened = " ".join(" ".join(call) for call in mutating)
        for legacy_name in LEGACY_UNIT_NAMES:
            self.assertNotIn(legacy_name, flattened)

    def test_uninstall_refuses_unowned_unit_and_absent_state_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            unowned = unit_dir / WEB_SERVICE
            unowned.write_text("[Unit]\nDescription=not ours\n", encoding="utf-8")
            runner = FakeRunner()
            with self.assertRaises(LocalServiceError):
                uninstall(unit_dir=unit_dir, runner=runner, platform_name="linux")
            self.assertTrue(unowned.is_file())
            self.assertFalse(runner.calls)

            unowned.unlink()
            absent = uninstall(
                unit_dir=unit_dir, runner=runner, platform_name="linux"
            )
            self.assertFalse(absent.installed)
            self.assertFalse(
                any(call[0][2] in {"daemon-reload", "enable", "disable"} for call in runner.calls)
            )

    def test_failed_enable_rolls_back_and_failed_disable_preserves_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, python, data_dir, market_database = self._layout(base)
            install_dir = base / "failed install"
            with self.assertRaises(LocalServiceError):
                install(
                    root,
                    python,
                    data_dir,
                    market_database,
                    unit_dir=install_dir,
                    runner=FakeRunner(operations={"enable": 1}),
                    platform_name="linux",
                )
            self.assertTrue(install_dir.is_dir())
            self.assertFalse(any((install_dir / name).exists() for name in UNIT_NAMES))

            uninstall_dir = base / "failed uninstall"
            uninstall_dir.mkdir()
            for name in UNIT_NAMES:
                (uninstall_dir / name).write_text(
                    MANAGED_MARKER + "\nfixture", encoding="utf-8"
                )
            with self.assertRaises(LocalServiceError):
                uninstall(
                    unit_dir=uninstall_dir,
                    runner=FakeRunner(operations={"disable": 1}),
                    platform_name="linux",
                )
            self.assertTrue(all((uninstall_dir / name).is_file() for name in UNIT_NAMES))

    def test_command_errors_and_unknown_arguments_do_not_expose_values(self) -> None:
        class FailingRunner:
            def __call__(
                self, argv: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                del argv, kwargs
                raise subprocess.CalledProcessError(
                    1,
                    ["systemctl"],
                    stderr="private/path credential-value",
                )

        with tempfile.TemporaryDirectory() as directory:
            root, python, data_dir, market_database = self._layout(Path(directory))
            with self.assertRaises(LocalServiceError) as caught:
                install(
                    root,
                    python,
                    data_dir,
                    market_database,
                    unit_dir=Path(directory) / "units",
                    runner=FailingRunner(),
                    platform_name="linux",
                )
        self.assertNotIn("private/path", str(caught.exception))
        self.assertNotIn("credential-value", str(caught.exception))

        private_value = "secret-value-456789"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught_exit:
                main(["status", "--unknown", private_value])
        self.assertEqual(caught_exit.exception.code, 2)
        self.assertNotIn(private_value, stderr.getvalue())
        self.assertNotIn("456789", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_non_linux_never_attempts_systemd(self) -> None:
        runner = FakeRunner()
        status = inspect_status(runner=runner, platform_name="win32")
        self.assertFalse(status.installed)
        self.assertIsNone(status.enabled)
        with self.assertRaises(LocalServiceError):
            uninstall(runner=runner, platform_name="win32")
        self.assertFalse(runner.calls)


if __name__ == "__main__":
    unittest.main()
