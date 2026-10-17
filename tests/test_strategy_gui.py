from __future__ import annotations

import gc
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import tkinter as tk

from asset_assistant.automation import AutomationService
from asset_assistant.gui import AssetAssistantApp
from asset_assistant.gui_service import STRATEGY_VERSION_CONFLICT_MESSAGE
from asset_assistant.market_data import (
    CollectionResult,
    CollectionStatus,
    MarketDataStore,
    NormalizedRecord,
    RecordKind,
)
from asset_assistant.strategy import StrategyKind


SEOUL_NOW = datetime.fromisoformat("2026-10-11T15:30:00+09:00")


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
        raise AssertionError("전략 GUI 비동기 작업이 제한 시간 안에 끝나지 않았습니다.")


def visible_widget_text(widget: tk.Misc) -> str:
    values: list[str] = []
    try:
        text = widget.cget("text")
    except tk.TclError:
        text = ""
    if text:
        values.append(str(text))
    if isinstance(widget, tk.Text):
        values.append(widget.get("1.0", "end-1c"))
    for child in widget.winfo_children():
        values.append(visible_widget_text(child))
    return "\n".join(values)


def drag_scale(root: tk.Tk, scale: tk.Scale, target: Decimal) -> None:
    """Exercise the mapped Tk scale binding instead of its private callback."""

    root.update_idletasks()
    if not scale.winfo_ismapped():
        raise AssertionError("전략 슬라이더가 실제 DISPLAY에 매핑되지 않았습니다.")
    start_x, start_y = scale.coords(scale.get())
    target_x, target_y = scale.coords(str(target))
    scale.event_generate("<ButtonPress-1>", x=start_x, y=start_y)
    root.update()
    scale.event_generate("<B1-Motion>", x=target_x, y=target_y)
    root.update()
    scale.event_generate("<ButtonRelease-1>", x=target_x, y=target_y)
    root.update()


class StrategyGuiTests(unittest.TestCase):
    def make_root(self) -> tk.Tk:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"실제 GUI DISPLAY를 사용할 수 없음: {exc}")
        root.withdraw()
        return root

    def set_weights(
        self,
        app: AssetAssistantApp,
        root: tk.Tk,
        broad: object,
        quality: object = "0",
    ) -> None:
        for strategy, variable in app.strategy_weight_vars.items():
            variable.set("0")
        app.strategy_weight_vars[StrategyKind.BROAD_MARKET].set(broad)
        app.strategy_weight_vars[StrategyKind.QUALITY].set(quality)
        root.update()

    def test_conflicting_version_shows_specific_safe_error_without_mutation(self) -> None:
        root = self.make_root()
        errors: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "strategy.sqlite3")
            service = AutomationService(store=store, clock=lambda: SEOUL_NOW)
            sensitive = r3_variants()[0]
            version = f"probe-v1 {sensitive}"
            original_weights = {
                StrategyKind.QUALITY: Decimal("60"),
                StrategyKind.VALUE: Decimal("40"),
            }
            self.assertTrue(service.set_strategy_mix(version, original_weights))
            before = store.strategy_version_row(version)
            assert before is not None
            before_config = str(before["config_json"])

            app = AssetAssistantApp(
                root,
                automation_service=service,
                open_dialog=lambda: "",
                save_dialog=lambda: "",
                error_handler=errors.append,
            )
            try:
                app.open_strategy_settings()
                wait_for_idle(app, root)
                for variable in app.strategy_weight_vars.values():
                    variable.set("0")
                app.strategy_weight_vars[StrategyKind.QUALITY].set("10")
                app.strategy_weight_vars[StrategyKind.MOMENTUM].set("90")
                app.strategy_version_var.set(version)
                root.update()
                self.assertFalse(app.save_strategy_button.instate(["disabled"]))

                app.save_strategy_button.invoke()
                wait_for_idle(app, root)

                self.assertEqual(errors, [STRATEGY_VERSION_CONFLICT_MESSAGE])
                self.assertEqual(app.status_text, STRATEGY_VERSION_CONFLICT_MESSAGE)
                visible_error = "\n".join((*errors, app.status_text))
                self.assertNotIn(sensitive, visible_error)
                self.assertNotIn("456789", visible_error)
                self.assertNotIn("４５６７８９", visible_error)
                self.assertNotIn("저장했습니다", visible_error)
                after = store.strategy_version_row(version)
                assert after is not None
                self.assertEqual(str(after["config_json"]), before_config)
                connection = store._connect()
                try:
                    row_count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM strategy_versions"
                        ).fetchone()[0]
                    )
                finally:
                    connection.close()
                self.assertEqual(row_count, 1)
                current = service.current_strategy_mix()
                assert current is not None
                self.assertEqual(current.version, version)
                self.assertEqual(dict(current.weights), original_weights)
            finally:
                app.close()
                del app
                gc.collect()

    def test_strategy_window_validation_versions_restore_detail_and_scroll(self) -> None:
        root = self.make_root()
        errors: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "strategy.sqlite3")
            service = AutomationService(store=store, clock=lambda: SEOUL_NOW)
            sensitive = "계좌 110-123-456789"
            first_version = f"첫 전략 {sensitive}"
            self.assertTrue(
                service.set_strategy_mix(
                    first_version,
                    {StrategyKind.BROAD_MARKET: Decimal("100")},
                )
            )
            effective = SEOUL_NOW - timedelta(minutes=5)
            collected = SEOUL_NOW - timedelta(minutes=4)
            record = NormalizedRecord(
                provider="kis",
                kind=RecordKind.PRICE_VOLUME,
                record_id="strategy-gui-sensitive-fixture",
                instrument_id=sensitive,
                market="TEST",
                currency="KRW",
                effective_at=effective,
                published_at=effective,
                collected_at=collected,
                unit="KRW",
                value="1",
                source_url="https://example.invalid/fixture",
                metadata={"metric": "price", "environment": "paper"},
            )
            store.save_collection(
                CollectionResult(
                    "kis",
                    collected,
                    collected,
                    CollectionStatus.OK,
                    (record,),
                )
            )
            source_id = int(store.latest_records(provider="kis")[0]["id"])
            store.save_candidate_assessment(
                strategy_version=first_version,
                instrument_id=sensitive,
                cutoff_at=SEOUL_NOW,
                status="UNAVAILABLE",
                score=None,
                reasons=(f"통합 근거 {sensitive}",),
                source_record_ids=(source_id,),
                created_at=SEOUL_NOW,
            )
            missing_record = NormalizedRecord(
                provider="kis",
                kind=RecordKind.PRICE_VOLUME,
                record_id="strategy-gui-missing-timing-fixture",
                instrument_id="missing-ref",
                market="TEST",
                currency="KRW",
                effective_at=effective,
                published_at=effective,
                collected_at=collected,
                unit="KRW",
                value="1",
                source_url="https://example.invalid/fixture",
                metadata={"metric": "price", "environment": "paper"},
            )
            store.save_collection(
                CollectionResult(
                    "kis",
                    collected,
                    collected,
                    CollectionStatus.OK,
                    (missing_record,),
                )
            )
            missing_source_id = next(
                int(row["id"])
                for row in store.latest_records(provider="kis")
                if row["instrument_id"] == "missing-ref"
            )
            store.save_candidate_assessment(
                strategy_version=first_version,
                instrument_id="missing-ref",
                cutoff_at=SEOUL_NOW,
                status="UNAVAILABLE",
                score=None,
                reasons=("손상 참조 표시 회귀",),
                source_record_ids=(missing_source_id,),
                created_at=SEOUL_NOW,
            )
            connection = store._connect()
            try:
                connection.execute(
                    "DELETE FROM market_records WHERE id = ?", (missing_source_id,)
                )
                connection.commit()
            finally:
                connection.close()
            for index, variant in enumerate(r3_variants()):
                store.save_candidate_assessment(
                    strategy_version=first_version,
                    instrument_id=f"후보 {index} {variant}",
                    cutoff_at=SEOUL_NOW,
                    status="UNAVAILABLE",
                    score=None,
                    reasons=(f"저장 근거 {variant}",),
                    source_record_ids=(),
                    created_at=SEOUL_NOW,
                )

            app = AssetAssistantApp(
                root,
                automation_service=service,
                open_dialog=lambda: "",
                save_dialog=lambda: "",
                error_handler=errors.append,
            )
            try:
                app.open_strategy_settings()
                wait_for_idle(app, root)
                window = app._strategy_window
                self.assertIsNotNone(window)
                assert window is not None
                window.deiconify()
                window.geometry("1000x680")
                root.update()

                self.assertEqual(tuple(int(value) for value in window.minsize()), (1000, 680))
                self.assertEqual(
                    app.strategy_policy_notice_label.cget("text"),
                    "후보 평가용 가중치이며 실제 자산배분·매수 금액이 아닙니다.",
                )
                summary = app.strategy_summary_text.get("1.0", "end-1c")
                assessment = app.strategy_assessment_text.get("1.0", "end-1c")
                combined_safe = "\n".join((summary, assessment))
                self.assertIn("[계좌번호 형태 마스킹]", combined_safe)
                self.assertNotIn(sensitive, combined_safe)
                self.assertNotIn("456789", combined_safe)
                self.assertNotIn("４５６７８９", combined_safe)
                for variant in r3_variants():
                    self.assertNotIn(variant, combined_safe)
                self.assertIn("근거·반대 근거(저장 당시 통합)", assessment)
                self.assertIn(f"출처 레코드 ID: {source_id}", assessment)
                self.assertIn("2026-10-11 15:25:00 KST", assessment)
                self.assertIn("2026-10-11 15:26:00 KST", assessment)
                self.assertIn(
                    f"#{missing_source_id} 자료 기준 확인 불가 · 수집 확인 불가",
                    assessment,
                )

                app.strategy_version_var.set("gui-boundary-v2")
                cases = (
                    ("95", "0", True, "5%를 추가해야 합니다."),
                    ("100", "5", True, "5%를 줄여야 합니다."),
                    ("99.99", "0", True, "0.01%를 추가해야 합니다."),
                    ("100", "0.01", True, "0.01%를 줄여야 합니다."),
                    ("100", "0", False, "합계 100%입니다. 저장할 수 있습니다."),
                )
                for broad, quality, disabled, guidance in cases:
                    with self.subTest(total=(broad, quality)):
                        self.set_weights(app, root, broad, quality)
                        self.assertEqual(
                            app.save_strategy_button.instate(["disabled"]), disabled
                        )
                        self.assertEqual(app.strategy_guidance_var.get(), guidance)

                for invalid in ("-1", "", "문자", "1e-999999999"):
                    with self.subTest(invalid=invalid):
                        self.set_weights(app, root, invalid)
                        self.assertTrue(app.save_strategy_button.instate(["disabled"]))

                # Slider callback also keeps the business value as Decimal text.
                self.set_weights(app, root, "0")
                drag_scale(
                    root,
                    app.strategy_scale_widgets[StrategyKind.BROAD_MARKET],
                    Decimal("95"),
                )
                dragged = Decimal(
                    app.strategy_weight_vars[StrategyKind.BROAD_MARKET].get()
                )
                self.assertGreater(dragged, Decimal("94"))
                self.assertLess(dragged, Decimal("96"))
                app.strategy_weight_vars[StrategyKind.BROAD_MARKET].set("95")
                root.update()
                self.assertTrue(app.save_strategy_button.instate(["disabled"]))

                first_before = str(store.strategy_version_row(first_version)["config_json"])
                self.set_weights(app, root, "60", "40")
                app.strategy_version_var.set("gui-boundary-v2")
                root.update()
                self.assertFalse(app.save_strategy_button.instate(["disabled"]))
                app.save_strategy_button.invoke()
                wait_for_idle(app, root)
                self.assertIsNotNone(store.strategy_version_row("gui-boundary-v2"))
                self.assertEqual(
                    str(store.strategy_version_row(first_version)["config_json"]),
                    first_before,
                )

                self.set_weights(app, root, "100")
                app.strategy_version_var.set("gui-boundary-v3")
                root.update()
                app.save_strategy_button.invoke()
                wait_for_idle(app, root)
                self.assertIsNotNone(store.strategy_version_row("gui-boundary-v3"))

                # 같은 불변 버전의 재저장은 새 저장으로 표시하지 않는다.
                app.strategy_version_var.set("gui-boundary-v3")
                root.update()
                app.save_strategy_button.invoke()
                wait_for_idle(app, root)
                self.assertIn("이미 있습니다", app.status_text)

                # Closing/reopening does not discard an unsaved draft.
                self.set_weights(app, root, "25", "75")
                app._close_strategy_window()
                app.open_strategy_settings()
                wait_for_idle(app, root)
                self.assertEqual(
                    app.strategy_weight_vars[StrategyKind.BROAD_MARKET].get(), "25"
                )
                self.assertEqual(
                    app.strategy_weight_vars[StrategyKind.QUALITY].get(), "75"
                )
                app.restore_strategy_mix()
                self.assertEqual(
                    app.strategy_weight_vars[StrategyKind.BROAD_MARKET].get(), "100"
                )
                self.assertEqual(
                    app.strategy_weight_vars[StrategyKind.QUALITY].get(), "0"
                )

                app.strategy_detail_buttons[StrategyKind.VALUE].invoke()
                root.update()
                self.assertEqual(app.strategy_detail_title_var.get(), "가치")
                detail = app.strategy_detail_text.get("1.0", "end-1c")
                self.assertIn("업종 내 상대 가치평가", detail)
                self.assertIn("미연결", detail)

                window = app._strategy_window
                assert window is not None
                window.geometry("1000x680")
                root.update()
                bbox = app.strategy_canvas.bbox("all")
                self.assertIsNotNone(bbox)
                assert bbox is not None
                self.assertGreater(bbox[3] - bbox[1], app.strategy_canvas.winfo_height())
                self.assertEqual(int(app.strategy_side_frame.grid_info()["row"]), 1)
                app.strategy_canvas.yview_moveto(1.0)
                root.update()
                self.assertGreater(app.strategy_canvas.yview()[0], 0)

                visible = visible_widget_text(window)
                for forbidden in (
                    "목표 금액",
                    "예상 수익률",
                    "목표가",
                    "매수 수량",
                    "삼성전자",
                    "현대차",
                    "KB금융",
                    "456789",
                ):
                    self.assertNotIn(forbidden, visible)
                self.assertFalse(errors)
            finally:
                app.close()
                del app
                gc.collect()


if __name__ == "__main__":
    unittest.main()
