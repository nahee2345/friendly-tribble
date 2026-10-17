"""실제 DISPLAY에서 수동 원장과 읽기 전용 자동 보고 GUI를 점검한다."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from decimal import Decimal
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont

from asset_assistant.gui import AssetAssistantApp
from asset_assistant.strategy import StrategyKind


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "offline_sample.json"


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


def wait_for_idle(app: AssetAssistantApp, root: tk.Tk, *, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    heartbeats = 0
    while app.is_busy and time.monotonic() < deadline:
        root.update()
        heartbeats += 1
        time.sleep(0.01)
    root.update()
    if app.is_busy:
        raise AssertionError("GUI 비동기 작업이 제한 시간 안에 끝나지 않았습니다.")
    return heartbeats


def drag_scale(root: tk.Tk, scale: tk.Scale, target: Decimal) -> None:
    """Drag a mapped Tk scale so smoke coverage includes the widget binding."""

    root.update_idletasks()
    assert scale.winfo_ismapped()
    start_x, start_y = scale.coords(scale.get())
    target_x, target_y = scale.coords(str(target))
    scale.event_generate("<ButtonPress-1>", x=start_x, y=start_y)
    root.update()
    scale.event_generate("<B1-Motion>", x=target_x, y=target_y)
    root.update()
    scale.event_generate("<ButtonRelease-1>", x=target_x, y=target_y)
    root.update()


def run_smoke(
    *,
    artifacts_dir: Path,
    hold_seconds: float,
    market_database: Path | None = None,
) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    root = tk.Tk()
    from asset_assistant.automation import AutomationService
    from asset_assistant.market_data import MarketDataStore

    strategy_database = market_database or artifacts_dir / "gui-smoke-market.sqlite3"
    automation_service = AutomationService(store=MarketDataStore(strategy_database))
    collection_calls = 0

    def reject_unexpected_collection() -> object:
        nonlocal collection_calls
        collection_calls += 1
        raise AssertionError("GUI 전략 화면이 공급자 수집을 호출하면 안 됩니다.")

    automation_service.collect = reject_unexpected_collection  # type: ignore[method-assign]
    app = AssetAssistantApp(
        root,
        open_dialog=lambda: "",
        save_dialog=lambda: "",
        error_handler=errors.append,
        automation_service=automation_service,
    )
    root.update()
    total_heartbeats = 0

    app.choose_input_file()
    assert app.current_data is None
    assert "취소" in app.status_text

    invalid = artifacts_dir / "민감 110－123－456789 입력.json"
    invalid.write_text("{invalid", encoding="utf-8")
    app.load_path(invalid)
    assert app.current_data is None and app.current_report is None
    total_heartbeats += wait_for_idle(app, root)
    assert app.current_data is None and app.current_report is None
    assert errors and "456789" not in errors[-1] and str(invalid) not in errors[-1]

    app.load_path(SAMPLE)
    total_heartbeats += wait_for_idle(app, root)
    assert app.current_data is not None
    assert app.current_data.dashboard.investment_assets_text == "4,000,000원"
    app.generate_report()
    total_heartbeats += wait_for_idle(app, root)
    assert app.current_report is not None

    visible_report = app.report_text_content()
    heading_positions = [visible_report.index(f"{index})") for index in range(1, 8)]
    assert heading_positions == sorted(heading_positions)
    assert "**" not in visible_report and "|---|" not in visible_report

    previous_report = app.current_report
    app.choose_input_file()
    assert app.current_report == previous_report
    app.save_report_dialog()
    assert app.current_report == previous_report and "취소" in app.status_text

    output = artifacts_dir / "한글 공백 폴더" / "GUI 저장 보고서.md"
    app.save_to_path(output)
    total_heartbeats += wait_for_idle(app, root)
    assert output.read_text(encoding="utf-8") == app.current_report

    sample_before = SAMPLE.read_bytes()
    error_count = len(errors)
    app.save_to_path(SAMPLE)
    total_heartbeats += wait_for_idle(app, root)
    assert len(errors) == error_count + 1
    assert SAMPLE.read_bytes() == sample_before

    cap_data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    cap_data["realm"] = "MANUAL_ACTUAL"
    cap_values = ("2340000", "900000", "180000", "580000")
    for position, value in zip(cap_data["positions"], cap_values, strict=True):
        position["market_value_krw"] = value
    cap_path = artifacts_dir / "satellite-cap.json"
    cap_path.write_text(json.dumps(cap_data, ensure_ascii=False), encoding="utf-8")
    root.geometry("900x600")
    app.load_path(cap_path)
    total_heartbeats += wait_for_idle(app, root)
    root.update_idletasks()
    warning_font = tkfont.Font(root=root, font=app.warning_text.cget("font"))
    assert app.warning_text.winfo_height() >= warning_font.metrics("linespace") * 3
    assert "위성 상한 초과 · 신규 위성 매수 중지" in app.warning_text.get(
        "1.0", "end-1c"
    )
    notice_bottom = (
        app.operation_requirements_label.winfo_rooty()
        + app.operation_requirements_label.winfo_height()
    )
    dashboard_bottom = app.dashboard.winfo_rooty() + app.dashboard.winfo_height()
    assert app.operation_requirements_label.winfo_ismapped()
    assert notice_bottom <= dashboard_bottom

    sensitive_data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    variants = r3_variants()
    sensitive = variants[0]
    sensitive_data["scenario_label"] = f"GUI 제목 {variants[0]} 끝"
    sensitive_data["accounts"][0]["alias"] = f"GUI 별칭 {variants[1]} 끝"
    sensitive_data["positions"][0]["name"] = f"GUI 종목 {variants[2]} 끝"
    sensitive_data["assumptions"] = [
        f"<표시> & GUI 가정 {value} 끝" for value in variants
    ]
    sensitive_path = artifacts_dir / "sensitive.json"
    sensitive_path.write_text(json.dumps(sensitive_data, ensure_ascii=False), encoding="utf-8")
    app.load_path(sensitive_path)
    total_heartbeats += wait_for_idle(app, root)
    assert app.current_data is not None
    assert "456789" not in app.current_data.dashboard.title
    app.generate_report()
    total_heartbeats += wait_for_idle(app, root)
    assert app.current_report is not None and "456789" not in app.current_report
    visible_sensitive_report = app.report_text_content()
    assert "[계좌번호 형태 마스킹]" in visible_sensitive_report
    assert visible_sensitive_report.count("[계좌번호 형태 마스킹]") >= 18
    assert "<표시> & GUI 가정" in visible_sensitive_report
    for forbidden in (
        "456789",
        "４５６７８９",
        r"\[계좌번호 형태 마스킹\]",
        "**",
        "|---|",
        "&lt;",
        "&amp;",
    ):
        assert forbidden not in visible_sensitive_report
    sensitive_output = artifacts_dir / "한글 공백 폴더" / "민감 문자열 검사 보고서.md"
    app.save_to_path(sensitive_output)
    total_heartbeats += wait_for_idle(app, root)
    saved_sensitive_report = sensitive_output.read_text(encoding="utf-8")
    assert sensitive not in saved_sensitive_report and "456789" not in saved_sensitive_report

    app.load_path(invalid)
    assert app.current_data is None and app.current_report is None
    total_heartbeats += wait_for_idle(app, root)
    app.load_path(SAMPLE)
    total_heartbeats += wait_for_idle(app, root)
    app.generate_report()
    total_heartbeats += wait_for_idle(app, root)
    assert app.current_report is not None

    if market_database is not None:
        errors_before_automation = len(errors)
        app.generate_automated_report()
        total_heartbeats += wait_for_idle(app, root, timeout=10.0)
        automated_visible = app.report_text_content()
        assert "공식자료 상태" in automated_visible
        assert "미국 노동통계국 BLS" in automated_visible
        assert "미국 연방준비제도" in automated_visible
        assert "제안 주문안 없음" in automated_visible
        assert len(errors) == errors_before_automation
        automated_output = artifacts_dir / "한글 공백 폴더" / "자동 시장자료 보고서.md"
        app.save_to_path(automated_output)
        total_heartbeats += wait_for_idle(app, root)
        assert automated_output.read_text(encoding="utf-8") == app.current_report

        # 자동 보고서 모드에서도 직전에 연 원본 JSON은 계속 보호한다.
        sample_before_automated_save = SAMPLE.read_bytes()
        error_count = len(errors)
        app.save_to_path(SAMPLE)
        total_heartbeats += wait_for_idle(app, root)
        assert len(errors) == error_count + 1
        assert SAMPLE.read_bytes() == sample_before_automated_save

    # 후보 평가용 전략 혼합은 별도 창에서 정확한 Decimal 합계만 저장한다.
    app.open_strategy_settings()
    total_heartbeats += wait_for_idle(app, root)
    assert app._strategy_window is not None
    app._strategy_window.deiconify()
    app._strategy_window.geometry("1000x680")
    root.update()
    app.strategy_version_var.set(f"gui-smoke-{time.time_ns()}")
    for variable in app.strategy_weight_vars.values():
        variable.set("0")
    broad_scale = app.strategy_scale_widgets[StrategyKind.BROAD_MARKET]
    drag_scale(root, broad_scale, Decimal("95"))
    dragged = Decimal(app.strategy_weight_vars[StrategyKind.BROAD_MARKET].get())
    assert Decimal("94") < dragged < Decimal("96")
    app.strategy_weight_vars[StrategyKind.BROAD_MARKET].set("95")
    root.update()
    assert app.strategy_weight_vars[StrategyKind.BROAD_MARKET].get() == "95"
    assert app.save_strategy_button.instate(["disabled"])
    assert app.strategy_guidance_var.get() == "5%를 추가해야 합니다."
    drag_scale(root, broad_scale, Decimal("100"))
    assert not app.save_strategy_button.instate(["disabled"])
    app.save_strategy_button.invoke()
    total_heartbeats += wait_for_idle(app, root)
    assert app._strategy_saved_weights is not None
    assert app._strategy_saved_weights[StrategyKind.BROAD_MARKET] == 100

    app.strategy_detail_buttons[StrategyKind.VALUE].invoke()
    root.update()
    assert app.strategy_detail_title_var.get() == "가치"
    assert "업종 내 상대 가치평가" in app.strategy_detail_text.get("1.0", "end-1c")

    app.strategy_weight_vars[StrategyKind.BROAD_MARKET].set("25")
    app.strategy_weight_vars[StrategyKind.QUALITY].set("75")
    root.update()
    app._close_strategy_window()
    app.open_strategy_settings()
    total_heartbeats += wait_for_idle(app, root)
    assert app.strategy_weight_vars[StrategyKind.BROAD_MARKET].get() == "25"
    assert app.strategy_weight_vars[StrategyKind.QUALITY].get() == "75"
    app.restore_strategy_mix()
    assert app.strategy_weight_vars[StrategyKind.BROAD_MARKET].get() == "100"
    assert app.strategy_weight_vars[StrategyKind.QUALITY].get() == "0"

    assert app._strategy_window is not None
    app._strategy_window.geometry("1000x680")
    root.update()
    strategy_bbox = app.strategy_canvas.bbox("all")
    assert strategy_bbox is not None
    assert strategy_bbox[3] - strategy_bbox[1] > app.strategy_canvas.winfo_height()
    assert int(app.strategy_side_frame.grid_info()["row"]) == 1
    app.strategy_canvas.yview_moveto(1.0)
    root.update()
    assert app.strategy_canvas.yview()[0] > 0
    strategy_visible = "\n".join(
        (
            app.strategy_policy_notice_label.cget("text"),
            app.strategy_summary_text.get("1.0", "end-1c"),
            app.strategy_detail_text.get("1.0", "end-1c"),
            app.strategy_assessment_text.get("1.0", "end-1c"),
        )
    )
    assert "실제 자산배분·매수 금액이 아닙니다" in strategy_visible
    for forbidden in ("목표 금액", "예상 수익률", "목표가", "매수 수량"):
        assert forbidden not in strategy_visible
    assert collection_calls == 0
    app.strategy_detail_buttons[StrategyKind.VALUE].invoke()
    app._strategy_window.geometry("1280x800")
    app.strategy_canvas.yview_moveto(0.0)
    root.update()

    root.geometry("1280x800")
    root.update()
    app.report_text.yview_moveto(0.35)
    root.update()
    app.report_text.yview_moveto(0.0)
    root.update()

    print(
        f"gui_smoke=PASS heartbeats={total_heartbeats} strategy_collection_calls={collection_calls}",
        flush=True,
    )
    deadline = time.monotonic() + max(hold_seconds, 0.0)
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    app.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--hold-seconds", type=float, default=0.5)
    parser.add_argument(
        "--market-database",
        type=Path,
        help="이미 수집한 읽기 전용 시장자료 DB로 자동 보고서 화면도 점검",
    )
    args = parser.parse_args()
    if args.artifacts_dir is not None:
        run_smoke(
            artifacts_dir=args.artifacts_dir,
            hold_seconds=args.hold_seconds,
            market_database=args.market_database,
        )
        return
    with tempfile.TemporaryDirectory() as temporary:
        run_smoke(
            artifacts_dir=Path(temporary),
            hold_seconds=args.hold_seconds,
            market_database=args.market_database,
        )


if __name__ == "__main__":
    main()
