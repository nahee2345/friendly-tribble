from __future__ import annotations

import contextlib
import gc
import io
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tkinter as tk

from asset_assistant.automation import AutomationService, ScheduledJobStatusRow, run
from asset_assistant.gui import AssetAssistantApp, _scheduled_job_history_text
from asset_assistant.market_data import MarketDataStore


SEOUL = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 11, 15, 5, tzinfo=SEOUL)


class _CredentialStore:
    def status(self) -> object:
        return SimpleNamespace(available=False, backend="", message="사용 불가")

    def configured_fields(self, provider: str) -> frozenset[str]:
        del provider
        return frozenset()


def _wait_for_idle(app: AssetAssistantApp, root: tk.Tk) -> None:
    deadline = time.monotonic() + 5
    while app.is_busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    root.update()
    if app.is_busy:
        raise AssertionError("GUI 상태 조회가 제한 시간 안에 끝나지 않았습니다.")


class ScheduledJobStatusTests(unittest.TestCase):
    def test_service_merges_current_and_attempt_history_without_raw_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            failed_slot = datetime(2026, 10, 10, 15, 0, tzinfo=SEOUL)
            running_slot = datetime(2026, 10, 11, 16, 0, tzinfo=SEOUL)
            self.assertTrue(store.claim_job("collect", failed_slot, NOW))
            store.finish_job(
                "collect",
                failed_slot,
                finished_at=NOW + timedelta(minutes=1),
                status="ERROR",
                detail_code="private-/tmp/path-110-123-456789",
            )
            self.assertTrue(
                store.claim_job("report", running_slot, NOW + timedelta(minutes=2))
            )

            rows = AutomationService(store=store, clock=lambda: NOW).recent_scheduled_jobs()

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].task, "보고서")
            self.assertEqual(rows[0].status, "RUNNING")
            self.assertEqual(rows[0].finished_at, "진행 중")
            self.assertEqual(rows[1].task, "수집")
            self.assertEqual(rows[1].status, "ERROR")
            self.assertEqual(rows[1].detail_code, "UNKNOWN")
            combined = "\n".join(str(row) for row in rows)
            for forbidden in ("/tmp/path", "110-123-456789", "456789"):
                self.assertNotIn(forbidden, combined)

    def test_status_cli_shows_safe_job_fields_and_never_echoes_detail_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            store = MarketDataStore(path)
            slot = datetime(2026, 10, 11, 15, 0, tzinfo=SEOUL)
            store.claim_job("collect", slot, NOW)
            store.finish_job(
                "collect",
                slot,
                finished_at=NOW + timedelta(minutes=1),
                status="ERROR",
                detail_code="TOKEN-/private/path-110-123-456789",
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                result = run(["--database", str(path), "status"])

            visible = stdout.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("예약 실행 이력: 수집", visible)
            self.assertIn("예약 2026-10-11 15:00:00 KST", visible)
            self.assertIn("상태 ERROR · 상세 UNKNOWN", visible)
            for forbidden in ("TOKEN", "/private/path", "110-123-456789", "456789"):
                self.assertNotIn(forbidden, visible)

    def test_gui_formatter_accepts_only_fixed_safe_fields(self) -> None:
        valid = ScheduledJobStatusRow(
            task="보고서",
            scheduled_for="2026-10-11 16:00:00 KST",
            started_at="2026-10-11 16:01:00 KST",
            finished_at="진행 중",
            status="RUNNING",
            detail_code="RECOVERED_RETRY",
        )
        malicious = SimpleNamespace(
            task="/private/110-123-456789",
            scheduled_for="TOKEN-/private/path",
            started_at="110-123-456789",
            finished_at="SECRET",
            status="TOKEN",
            detail_code="110-123-456789",
        )

        visible = _scheduled_job_history_text((valid, malicious))

        self.assertIn("보고서 · 예약 2026-10-11 16:00:00 KST", visible)
        self.assertIn("RUNNING/RECOVERED_RETRY", visible)
        self.assertIn("알 수 없는 작업 · 예약 확인 불가", visible)
        self.assertIn("UNKNOWN/UNKNOWN", visible)
        for forbidden in ("/private", "TOKEN", "SECRET", "110-123-456789", "456789"):
            self.assertNotIn(forbidden, visible)

    def test_settings_window_displays_recent_job_history(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"실제 GUI DISPLAY를 사용할 수 없음: {exc}")

        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            slot = datetime(2026, 10, 11, 15, 0, tzinfo=SEOUL)
            store.claim_job("collect", slot, NOW)
            store.finish_job(
                "collect",
                slot,
                finished_at=NOW + timedelta(minutes=1),
                status="OK",
                detail_code="OK",
            )
            service = AutomationService(store=store, clock=lambda: NOW)
            app = AssetAssistantApp(
                root,
                automation_service=service,
                credential_store=_CredentialStore(),
                error_handler=lambda message: self.fail(message),
            )
            scheduler = SimpleNamespace(
                installed=False,
                enabled=False,
                active=False,
                detail="미등록",
            )
            try:
                with patch(
                    "asset_assistant.scheduler.systemd_user_status",
                    return_value=scheduler,
                ):
                    app.open_provider_settings()
                    _wait_for_idle(app, root)
                visible = app.scheduled_job_history_var.get()
                self.assertIn("최근 예약 실행 이력", visible)
                self.assertIn("수집 · 예약 2026-10-11 15:00:00 KST", visible)
                self.assertIn("OK/OK", visible)
            finally:
                app.close()
                del app
                gc.collect()


if __name__ == "__main__":
    unittest.main()
