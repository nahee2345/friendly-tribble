from __future__ import annotations

import gc
import tempfile
import time
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tkinter as tk

from asset_assistant.gui import AssetAssistantApp


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = PROJECT_ROOT / "examples" / "offline_sample.json"


def wait_for_idle(app: AssetAssistantApp, root: tk.Tk, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while app.is_busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    root.update()
    if app.is_busy:
        raise AssertionError("GUI 비동기 작업이 제한 시간 안에 끝나지 않았습니다.")


@dataclass(frozen=True)
class FakeProviderRow:
    code: str
    name: str
    state: str
    last_effective: str
    last_collected: str
    last_attempted: str
    message: str


def automated_report() -> str:
    lines = [
        "# 자동 **시장자료** 보고서",
        "",
        "> 공식 공급자 자료 / 사용자 원장과 미합산",
        "",
    ]
    for index, title in enumerate(
        (
            "요약 3줄",
            "자산 현황과 목표·실제 비중",
            "목표 대비",
            "실제 배당과 배당 전환 능력",
            "위성 손실 현황",
            "제안 주문안과 이유",
            "규칙 위반·주의사항",
        ),
        start=1,
    ):
        lines.extend((f"## {index}) {title}", "- 미입력은 0으로 간주하지 않음", ""))
    return "\n".join(lines)


class FakeAutomationService:
    def __init__(self) -> None:
        self.status_calls = 0
        self.collect_calls = 0
        self.report_calls = 0
        self.latest_calls = 0
        self.targets: dict[str, tuple[str, ...]] = {}
        self.enabled: dict[str, bool] = {}
        self.kis_environment = "paper"
        self.strategy: tuple[str, dict[object, str]] | None = None
        self.fail_report = False

    def provider_statuses(self) -> tuple[FakeProviderRow, ...]:
        self.status_calls += 1
        return (
            FakeProviderRow(
                "bls",
                "BLS",
                "OK",
                "2026-09-01T00:00:00+00:00",
                "2026-10-11T15:00:00+09:00",
                "2026-10-11T15:00:01+09:00",
                "정상",
            ),
            FakeProviderRow(
                "sec", "SEC", "NEEDS_CONFIGURATION", "", "", "", "설정 필요"
            ),
        )

    def collect(self) -> object:
        self.collect_calls += 1
        time.sleep(0.03)
        return SimpleNamespace(status="OK", message="수집 완료")

    def generate_report(self) -> str:
        self.report_calls += 1
        time.sleep(0.03)
        if self.fail_report:
            raise RuntimeError("SECRET-TOKEN /tmp/private 110-123-456789")
        return automated_report()

    def latest_report(self) -> str | None:
        self.latest_calls += 1
        return automated_report()

    def set_targets(self, provider: str, targets: tuple[str, ...]) -> None:
        self.targets[provider] = targets

    def set_provider_enabled(self, provider: str, enabled: bool) -> None:
        self.enabled[provider] = enabled

    def set_kis_environment(self, environment: str) -> None:
        self.kis_environment = environment

    def set_strategy_mix(self, version: str, weights: dict[object, object]) -> bool:
        if (
            not version
            or any(isinstance(value, (float, bool)) for value in weights.values())
            or sum((Decimal(str(value)) for value in weights.values()), Decimal("0"))
            != Decimal("100")
        ):
            raise ValueError("invalid strategy")
        self.strategy = (version, {key: str(value) for key, value in weights.items()})
        return True


class FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.status_calls = 0
        self.fail_save = False

    def status(self) -> object:
        self.status_calls += 1
        return SimpleNamespace(available=True, backend="TestSecureVault", message="사용 가능")

    def set_secret(self, provider: str, field: str, secret: str) -> None:
        if self.fail_save:
            raise RuntimeError(f"vault failed: {secret} /tmp/private")
        self.values[(provider, field)] = secret

    def delete_secret(self, provider: str, field: str) -> None:
        self.values.pop((provider, field), None)

    def configured_fields(self, provider: str) -> frozenset[str]:
        return frozenset(field for saved_provider, field in self.values if saved_provider == provider)


class GuiAutomationDisplayTests(unittest.TestCase):
    def make_root(self) -> tk.Tk:
        try:
            return tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"실제 GUI DISPLAY를 사용할 수 없음: {exc}")

    def make_app(
        self,
        root: tk.Tk,
        service: FakeAutomationService,
        store: FakeCredentialStore,
        errors: list[str],
    ) -> AssetAssistantApp:
        return AssetAssistantApp(
            root,
            automation_service=service,
            credential_store=store,
            open_dialog=lambda: "",
            save_dialog=lambda: "",
            error_handler=errors.append,
        )

    def test_startup_is_network_idle_and_toolbar_runs_explicit_collection(self) -> None:
        root = self.make_root()
        service = FakeAutomationService()
        store = FakeCredentialStore()
        errors: list[str] = []
        app = self.make_app(root, service, store, errors)
        try:
            root.update()
            self.assertEqual(service.status_calls, 0)
            self.assertEqual(service.collect_calls, 0)
            self.assertEqual(store.status_calls, 0)
            self.assertEqual(app.market_collect_button.cget("text"), "시장자료 갱신")
            self.assertEqual(app.automated_report_button.cget("text"), "자동 보고서")
            self.assertEqual(app.provider_settings_button.cget("text"), "공급자 설정")
            self.assertNotIn("OFFLINE", root.title())

            app.collect_market_data()
            wait_for_idle(app, root)
            self.assertEqual(service.collect_calls, 1)
            self.assertIn("갱신을 완료", app.status_text)
            self.assertIn("주문", app.status_text)
            self.assertFalse(errors)
        finally:
            app.close()
            del app
            gc.collect()

    def test_open_gui_displays_new_scheduled_report_without_network_collection(self) -> None:
        root = self.make_root()
        service = FakeAutomationService()
        store = FakeCredentialStore()
        errors: list[str] = []
        app = self.make_app(root, service, store, errors)
        try:
            app._poll_scheduled_report()
            deadline = time.monotonic() + 5
            while app._scheduled_report_polling and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
            root.update()
            self.assertFalse(app._scheduled_report_polling)
            self.assertEqual(service.latest_calls, 1)
            self.assertEqual(service.collect_calls, 0)
            self.assertIn("자동 시장자료 보고서", app.report_text_content())
            self.assertIn("정기 보고서를 앱에 표시", app.status_text)
            self.assertFalse(errors)
        finally:
            app.close()
            del app
            gc.collect()

    def test_automated_report_is_separate_saveable_and_failure_clears_stale_result(self) -> None:
        root = self.make_root()
        service = FakeAutomationService()
        store = FakeCredentialStore()
        errors: list[str] = []
        app = self.make_app(root, service, store, errors)
        try:
            app.generate_automated_report()
            self.assertIsNone(app.current_report)
            wait_for_idle(app, root)
            self.assertEqual(service.report_calls, 1)
            self.assertIsNone(app.current_data)
            self.assertIsNotNone(app.current_report)
            self.assertIn("공식 공급자 자료", app.realm_var.get())
            visible = app.report_text_content()
            self.assertIn("자동 시장자료 보고서", visible)
            self.assertNotIn("**", visible)

            with tempfile.TemporaryDirectory() as temporary:
                protected_database = Path(temporary) / "market.sqlite3"
                protected_database.write_bytes(b"SQLite format 3\x00protected")
                service.store = SimpleNamespace(path=protected_database)  # type: ignore[attr-defined]
                output = Path(temporary) / "한글 공백" / "자동 보고서.md"
                app.save_to_path(output)
                wait_for_idle(app, root)
                self.assertEqual(output.read_text(encoding="utf-8"), automated_report())

                # 수동 입력을 연 뒤 자동 보고서로 전환해도 그 입력 JSON은
                # 보고서 저장 대상으로 덮어쓸 수 없어야 한다.
                protected_input = Path(temporary) / "보호할 입력.json"
                protected_input.write_bytes(SAMPLE.read_bytes())
                app.load_path(protected_input)
                wait_for_idle(app, root)
                app.generate_automated_report()
                wait_for_idle(app, root)
                original = protected_input.read_bytes()
                error_count = len(errors)
                app.save_to_path(protected_input)
                wait_for_idle(app, root)
                self.assertEqual(protected_input.read_bytes(), original)
                self.assertEqual(len(errors), error_count + 1)
                self.assertNotIn(str(protected_input), errors[-1])

                database_before = protected_database.read_bytes()
                error_count = len(errors)
                app.save_to_path(protected_database)
                wait_for_idle(app, root)
                self.assertEqual(protected_database.read_bytes(), database_before)
                self.assertEqual(len(errors), error_count + 1)

            service.fail_report = True
            app.generate_automated_report()
            self.assertIsNone(app.current_report)
            self.assertNotIn("자동 시장자료 보고서", app.report_text_content())
            wait_for_idle(app, root)
            self.assertIsNone(app.current_report)
            combined = "\n".join((*errors, app.status_text, app.report_text_content()))
            for forbidden in ("SECRET-TOKEN", "/tmp/private", "110-123-456789", "456789", "Traceback"):
                self.assertNotIn(forbidden, combined)
            self.assertIn("직전 결과는 표시하지 않습니다", app.report_text_content())
        finally:
            app.close()
            del app
            gc.collect()

    def test_settings_uses_masked_entry_and_secure_store_without_echoing_secret(self) -> None:
        root = self.make_root()
        service = FakeAutomationService()
        store = FakeCredentialStore()
        errors: list[str] = []
        app = self.make_app(root, service, store, errors)
        scheduler = SimpleNamespace(installed=False, enabled=False, active=False, detail="미등록")
        try:
            with patch("asset_assistant.scheduler.systemd_user_status", return_value=scheduler):
                app.open_provider_settings()
                wait_for_idle(app, root)
            self.assertGreaterEqual(service.status_calls, 1)
            self.assertGreaterEqual(store.status_calls, 1)
            self.assertEqual(app.secret_entry.cget("show"), "•")
            self.assertEqual(len(app.provider_status_tree.get_children()), 2)
            first_values = app.provider_status_tree.item(
                app.provider_status_tree.get_children()[0], "values"
            )
            self.assertEqual(first_values[3], "2026-10-11 15:00:00 KST")
            self.assertEqual(first_values[4], "2026-10-11 15:00:01 KST")
            self.assertEqual(app.scheduler_badge_var.get(), "예약 미등록")

            app.settings_provider_var.set("sec")
            app._update_settings_field_choices()
            app.settings_field_var.set("user_agent")
            secret = "private-contact@example.invalid SECRET-TOKEN"
            app.secret_entry.insert(0, secret)
            app.save_selected_secret()
            self.assertEqual(app.secret_entry.get(), "")
            wait_for_idle(app, root)
            self.assertEqual(store.values[("sec", "user_agent")], secret)
            visible = "\n".join((app.status_text, app.settings_status_var.get()))
            self.assertNotIn(secret, visible)
            self.assertNotIn("SECRET-TOKEN", visible)

            store.fail_save = True
            failed_secret = "SHOULD-NOT-LEAK-110-123-456789"
            app.secret_entry.insert(0, failed_secret)
            app.save_selected_secret()
            wait_for_idle(app, root)
            failed_visible = "\n".join((*errors, app.status_text, app.settings_status_var.get()))
            for forbidden in (failed_secret, "456789", "/tmp/private", "Traceback"):
                self.assertNotIn(forbidden, failed_visible)
            store.fail_save = False

            app.settings_provider_var.set("bls")
            app._update_settings_field_choices()
            app.target_entry.insert(0, "CUUR0000SA0, LNS14000000")
            app.save_provider_targets()
            self.assertEqual(app.target_entry.get(), "")
            wait_for_idle(app, root)
            self.assertEqual(
                service.targets["bls"],
                ("CUUR0000SA0", "LNS14000000"),
            )
            self.assertTrue(service.enabled["bls"])

            app.strategy_version_var.set("candidate-v1")
            app.strategy_weight_vars[next(iter(app.strategy_weight_vars))].set("100")
            app.save_strategy_mix()
            wait_for_idle(app, root)
            self.assertIsNotNone(service.strategy)
            self.assertEqual(service.strategy[0], "candidate-v1")

            app.settings_provider_var.set("sec")
            app.settings_field_var.set("user_agent")
            with patch("asset_assistant.gui.messagebox.askyesno", return_value=True):
                app.delete_selected_secret()
                wait_for_idle(app, root)
            self.assertNotIn(("sec", "user_agent"), store.values)
            self.assertEqual(len(errors), 1)
        finally:
            app.close()
            del app
            gc.collect()

    def test_scheduler_badge_does_not_call_inactive_timer_active(self) -> None:
        root = self.make_root()
        service = FakeAutomationService()
        store = FakeCredentialStore()
        errors: list[str] = []
        app = self.make_app(root, service, store, errors)
        inactive = SimpleNamespace(
            installed=True,
            enabled=True,
            active=False,
            detail="등록됨: 활성화=예, 대기 중=아니요",
        )
        try:
            with patch(
                "asset_assistant.scheduler.systemd_user_status",
                return_value=inactive,
            ):
                app.open_provider_settings()
                wait_for_idle(app, root)

            self.assertEqual(app.scheduler_badge_var.get(), "예약 확인 필요")
            self.assertIn("대기 중이 아님", app.scheduler_status_var.get())
            self.assertNotEqual(app.scheduler_badge_var.get(), "예약 활성")
            self.assertFalse(errors)
        finally:
            app.close()
            del app
            gc.collect()


if __name__ == "__main__":
    unittest.main()
