from __future__ import annotations

import gc
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont

from asset_assistant.automation import _safe_external_label
from asset_assistant.domain import DomainValidationError, StrategyVersionConflictError
from asset_assistant.gui import AssetAssistantApp
from asset_assistant.gui_service import (
    STRATEGY_VERSION_CONFLICT_MESSAGE,
    build_gui_report,
    load_gui_data,
    render_report_for_display,
    save_gui_report,
    user_facing_error_message,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "offline_sample.json"


def sample_data() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def r3_variants() -> tuple[str, ...]:
    ascii_digit_variants = (
        "110－123－456789",
        "110ㅡ123ㅡ456789",
        "110·123·456789",
        "110・123・456789",
        "110．123．456789",
        "110:123:456789",
        "110'123'456789",
        "110#123#456789",
    )
    fullwidth_table = str.maketrans("0123456789", "０１２３４５６７８９")
    return (
        *ascii_digit_variants,
        *(value.translate(fullwidth_table) for value in ascii_digit_variants),
        "１１０-１２３-４５６７８９",
        "110ㅡ１２３·456789",
    )


def wait_for_idle(app: AssetAssistantApp, root: tk.Tk, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while app.is_busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    root.update()
    if app.is_busy:
        raise AssertionError("GUI 비동기 작업이 제한 시간 안에 끝나지 않았습니다.")


class GuiServiceTests(unittest.TestCase):
    def test_strategy_version_conflict_has_specific_safe_message(self) -> None:
        sensitive = "민감 버전 계좌 110-123-456789"
        conflict = StrategyVersionConflictError(sensitive)

        message = user_facing_error_message(conflict, operation="strategy_save")

        self.assertEqual(message, STRATEGY_VERSION_CONFLICT_MESSAGE)
        self.assertNotIn(sensitive, message)
        self.assertNotIn("456789", message)
        self.assertNotIn("Traceback", message)
        self.assertEqual(
            user_facing_error_message(
                DomainValidationError(sensitive), operation="strategy_save"
            ),
            "작업을 완료할 수 없습니다. 입력 자료를 확인해 주세요.",
        )

    def test_display_renderer_unescapes_external_markdown_once(self) -> None:
        stored = f"- {_safe_external_label('**bold** *u*')}\n"

        self.assertEqual(stored, "- \\*\\*bold\\*\\* \\*u\\*\n")
        self.assertEqual(
            tuple(line.text for line in render_report_for_display(stored)),
            ("• **bold** *u*",),
        )

        stored_underscore = f"- {_safe_external_label('_under_')}\n"
        self.assertEqual(stored_underscore, "- \\_under\\_\n")
        self.assertEqual(
            tuple(line.text for line in render_report_for_display(stored_underscore)),
            ("• _under_",),
        )

        recursively_encoded = r"- &amp;lt;tag&amp;gt; \\*literal\\*" + "\n"
        self.assertEqual(
            tuple(line.text for line in render_report_for_display(recursively_encoded)),
            (r"• &lt;tag&gt; \*literal\*",),
        )

    def test_sample_dashboard_uses_validated_calculations(self) -> None:
        loaded = load_gui_data(SAMPLE)
        dashboard = loaded.dashboard
        self.assertTrue(dashboard.is_sample)
        self.assertEqual(dashboard.realm_code, "SAMPLE")
        self.assertEqual(dashboard.realm_label, "가상 샘플")
        self.assertEqual(dashboard.investment_assets_text, "4,000,000원")
        self.assertEqual(dashboard.declared_assets_text, "4,000,000원")
        self.assertIn("일치", dashboard.reconciliation_text)
        self.assertIn("자동 최신성 검증 없음", dashboard.freshness_text)
        self.assertTrue(any("실제 잔고가 아닙니다" in item for item in dashboard.warnings))

        report = build_gui_report(loaded)
        self.assertIn("OFFLINE / 가상 샘플", report)
        self.assertIn("상세 원장과 **합산하지 않고 대조만 함**", report)
        self.assertNotIn("8,000,000원", report)
        headings = [f"## {index})" for index in range(1, 8)]
        self.assertEqual([report.index(value) for value in headings], sorted(report.index(value) for value in headings))

    def test_missing_and_stale_values_are_not_displayed_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = sample_data()
            missing["positions"][0]["market_value_krw"] = None
            missing["positions"][0]["value_status"] = "MISSING"
            missing_path = Path(temporary) / "missing.json"
            missing_path.write_text(json.dumps(missing, ensure_ascii=False), encoding="utf-8")
            missing_dashboard = load_gui_data(missing_path).dashboard
            self.assertIn("불완전", missing_dashboard.investment_assets_text)
            self.assertNotEqual(missing_dashboard.investment_assets_text, "0원")
            self.assertIn("미입력·미확인", missing_dashboard.freshness_text)

            stale = sample_data()
            stale["positions"][0]["market_value_krw"] = None
            stale["positions"][0]["value_status"] = "STALE"
            stale_path = Path(temporary) / "stale.json"
            stale_path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
            stale_dashboard = load_gui_data(stale_path).dashboard
            self.assertIn("미갱신", stale_dashboard.freshness_text)
            self.assertTrue(any("미갱신" in item for item in stale_dashboard.warnings))

    def test_invalid_input_does_not_prevent_later_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            invalid = Path(temporary) / "민감 110－123－456789.json"
            invalid.write_text("{invalid", encoding="utf-8")
            with self.assertRaises(DomainValidationError) as raised:
                load_gui_data(invalid)
            message = user_facing_error_message(raised.exception, operation="load")
            self.assertNotIn(str(invalid), message)
            self.assertNotIn("456789", message)
            self.assertNotIn("Traceback", message)

            recovered = load_gui_data(SAMPLE)
            self.assertEqual(recovered.dashboard.investment_assets_text, "4,000,000원")

    def test_gui_title_reuses_r3_masking_without_global_nfkc_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            variants = (
                "110－123－456789",
                "110ㅡ123ㅡ456789",
                "110·123·456789",
                "110・123・456789",
                "110．123．456789",
                "110:123:456789",
                "110'123'456789",
                "110#123#456789",
            )
            for index, sensitive in enumerate(variants):
                with self.subTest(sensitive=sensitive):
                    data = sample_data()
                    data["scenario_label"] = f"ＡＢＣ 보고서 {sensitive} 끝"
                    path = Path(temporary) / f"input-{index}.json"
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    loaded = load_gui_data(path)
                    self.assertIn("ＡＢＣ 보고서", loaded.dashboard.title)
                    self.assertIn("계좌번호 형태 마스킹", loaded.dashboard.title)
                    self.assertNotIn("456789", loaded.dashboard.title)

    def test_display_renderer_decodes_markdown_after_masking_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = sample_data()
            variants = r3_variants()
            all_variants = " ".join(
                f"검증{index} {sensitive} 끝"
                for index, sensitive in enumerate(variants, start=1)
            )
            data["scenario_label"] = f"<표시> & 제목 {all_variants}"
            data["accounts"][0]["alias"] = f"별칭 {all_variants}"
            data["positions"][0]["name"] = f"종목 {all_variants}"
            data["assumptions"] = [
                f"<표시> & 가정 {sensitive}" for sensitive in variants
            ]
            path = Path(temporary) / "display.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            report = build_gui_report(load_gui_data(path))
            rendered = "\n".join(
                line.text for line in render_report_for_display(report)
            )

            self.assertIn(r"\[계좌번호 형태 마스킹\]", report)
            self.assertIn("&lt;표시&gt; &amp; 가정", report)
            self.assertIn("**", report)
            self.assertIn("[계좌번호 형태 마스킹]", rendered)
            self.assertGreaterEqual(rendered.count("[계좌번호 형태 마스킹]"), 18 * 4)
            self.assertIn("<표시> & 가정", rendered)
            self.assertIn("2028-01-01T00:00:00+09:00", rendered)
            self.assertIn("4,000,000원", rendered)
            for forbidden in (
                "456789",
                "４５６７８９",
                r"\[계좌번호 형태 마스킹\]",
                "**",
                "|---|",
                "&lt;",
                "&amp;",
            ):
                self.assertNotIn(forbidden, rendered)

    def test_save_supports_korean_space_path_and_blocks_input_overwrite(self) -> None:
        loaded = load_gui_data(SAMPLE)
        report = build_gui_report(loaded)
        original = SAMPLE.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "한글 공백 폴더" / "자산 보고서.md"
            save_gui_report(report, output, source_path=SAMPLE)
            self.assertEqual(output.read_text(encoding="utf-8"), report)

            with self.assertRaises(DomainValidationError):
                save_gui_report(report, SAMPLE, source_path=SAMPLE)
            self.assertEqual(SAMPLE.read_bytes(), original)

            hardlink = Path(temporary) / "input-alias.json"
            try:
                os.link(SAMPLE, hardlink)
            except OSError:
                self.skipTest("하드링크를 만들 수 없는 파일시스템입니다.")
            with self.assertRaises(DomainValidationError):
                save_gui_report(report, hardlink, source_path=SAMPLE)
            self.assertEqual(SAMPLE.read_bytes(), original)

    def test_save_symlink_loop_becomes_generic_gui_error(self) -> None:
        loaded = load_gui_data(SAMPLE)
        report = build_gui_report(loaded)
        with tempfile.TemporaryDirectory() as temporary:
            sensitive_name = "민감-110-123-456789.md"
            output = Path(temporary) / sensitive_name
            try:
                output.symlink_to(output.name)
            except OSError as exc:
                self.skipTest(f"테스트 파일시스템이 심볼릭 링크를 지원하지 않음: {exc}")
            with self.assertRaises(DomainValidationError) as raised:
                save_gui_report(report, output, source_path=SAMPLE)
            message = user_facing_error_message(raised.exception, operation="save")
            self.assertNotIn(str(output), message)
            self.assertNotIn(sensitive_name, message)
            self.assertNotIn("456789", message)
            self.assertNotIn("Traceback", message)

    @unittest.skipIf(os.name == "nt", "POSIX 파일 모드·symlink 검사")
    def test_save_is_owner_only_atomic_and_does_not_follow_output_symlink(self) -> None:
        loaded = load_gui_data(SAMPLE)
        report = build_gui_report(loaded)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "기존 대상.txt"
            target.write_text("보존", encoding="utf-8")
            output = Path(temporary) / "보고서 링크.md"
            output.symlink_to(target)

            save_gui_report(report, output, source_path=SAMPLE)

            self.assertEqual(target.read_text(encoding="utf-8"), "보존")
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_text(encoding="utf-8"), report)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


class GuiDisplayTests(unittest.TestCase):
    def make_root(self) -> tk.Tk:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"실제 GUI 디스플레이를 사용할 수 없음: {exc}")
        return root

    def test_minsize_keeps_three_warning_lines_and_operations_notice_visible(self) -> None:
        root = self.make_root()
        app = AssetAssistantApp(root, error_handler=lambda message: self.fail(message))
        try:
            with tempfile.TemporaryDirectory() as temporary:
                data = sample_data()
                data["realm"] = "MANUAL_ACTUAL"
                values = ("2340000", "900000", "180000", "580000")
                for position, value in zip(data["positions"], values, strict=True):
                    position["market_value_krw"] = value
                path = Path(temporary) / "satellite-cap.json"
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

                root.geometry("900x600")
                app.load_path(path)
                wait_for_idle(app, root)
                root.update_idletasks()

                warning_font = tkfont.Font(root=root, font=app.warning_text.cget("font"))
                self.assertGreaterEqual(
                    app.warning_text.winfo_height(),
                    warning_font.metrics("linespace") * 3,
                )
                self.assertIn(
                    "위성 상한 초과 · 신규 위성 매수 중지",
                    app.warning_text.get("1.0", "end-1c"),
                )
                notice_bottom = (
                    app.operation_requirements_label.winfo_rooty()
                    + app.operation_requirements_label.winfo_height()
                )
                dashboard_bottom = app.dashboard.winfo_rooty() + app.dashboard.winfo_height()
                self.assertTrue(app.operation_requirements_label.winfo_ismapped())
                self.assertLessEqual(notice_bottom, dashboard_bottom)
        finally:
            app.close()
            del app
            gc.collect()

    def test_report_widget_uses_readable_display_without_revealing_r3_values(self) -> None:
        root = self.make_root()
        errors: list[str] = []
        app = AssetAssistantApp(root, error_handler=errors.append)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                data = sample_data()
                variants = r3_variants()
                all_variants = " ".join(
                    f"검증{index} {sensitive} 끝"
                    for index, sensitive in enumerate(variants, start=1)
                )
                data["scenario_label"] = f"<표시> & 제목 {all_variants}"
                data["accounts"][0]["alias"] = f"별칭 {all_variants}"
                data["positions"][0]["name"] = f"종목 {all_variants}"
                data["assumptions"] = [
                    f"<표시> & 가정 {sensitive}" for sensitive in variants
                ]
                path = Path(temporary) / "display-r3.json"
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                app.load_path(path)
                wait_for_idle(app, root)
                app.generate_report()
                wait_for_idle(app, root)

                self.assertFalse(errors)
                self.assertIsNotNone(app.current_report)
                self.assertIn("**", app.current_report or "")
                visible = app.report_text_content()
                self.assertIn("[계좌번호 형태 마스킹]", visible)
                self.assertGreaterEqual(visible.count("[계좌번호 형태 마스킹]"), 18 * 4)
                self.assertIn("<표시> & 가정", visible)
                for forbidden in (
                    "456789",
                    "４５６７８９",
                    r"\[계좌번호 형태 마스킹\]",
                    "**",
                    "|---|",
                    "&lt;",
                    "&amp;",
                ):
                    self.assertNotIn(forbidden, visible)
        finally:
            app.close()
            del app
            gc.collect()


if __name__ == "__main__":
    unittest.main()
