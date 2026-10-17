"""Ubuntu와 Windows용 한국어 자산관리 데스크톱 GUI.

수동 JSON 원장은 기존 검산 흐름을 그대로 유지하고, 공식 시장자료 수집은
사용자가 명시적으로 버튼을 누를 때만 별도 서비스에 위임한다. GUI에는
투자 계산·정책·주문 로직을 두지 않는다.
"""

from __future__ import annotations

import hashlib
import queue
import re
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .credential_store import ALLOWED_FIELDS, CredentialStore
from .domain import DomainValidationError, mask_account_number_like_text
from .file_io import paths_refer_to_same_file, write_report_text
from .gui_service import (
    LoadedGuiData,
    STRATEGY_VERSION_CONFLICT_MESSAGE,
    build_gui_report,
    load_gui_data,
    render_report_for_display,
    save_gui_report,
    user_facing_error_message,
)
from .strategy import StrategyKind, strategy_weight_decimal
from .strategy_ui import (
    STRATEGY_DETAILS,
    STRATEGY_DISPLAY_NAMES,
    STRATEGY_ORDER,
    StrategyFormState,
    validate_strategy_form,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = PROJECT_ROOT / "examples" / "offline_sample.json"
SEOUL = ZoneInfo("Asia/Seoul")

PROVIDER_LABELS: dict[str, str] = {
    "bls": "BLS 미국 경제지표",
    "fed_news": "미국 연준 공식 보도자료 RSS",
    "sec": "SEC EDGAR 미국 공시",
    "opendart": "OpenDART 국내 공시·재무",
    "ecos": "한국은행 ECOS 경제지표",
    "kis": "KIS 국내 주식·ETF 시세",
    "kis_live": "KIS 실전 인증(시세 조회만)",
    "kis_paper": "KIS 모의 인증(시세 조회만)",
}
TARGET_PROVIDERS = ("bls", "fed_news", "sec", "opendart", "kis")
# 기존 내부 이름을 유지하되 화면 메타데이터의 단일 원본을 사용한다.
STRATEGY_LABELS: dict[StrategyKind, str] = dict(STRATEGY_DISPLAY_NAMES)
FIELD_LABELS: dict[str, str] = {
    "registration_key": "등록 키(선택)",
    "user_agent": "ASCII 조직명 + 연락 이메일",
    "api_key": "API 키",
    "app_key": "App Key",
    "app_secret": "App Secret",
}


def _object_value(value: object, name: str, default: object = "") -> object:
    """데이터클래스와 테스트용 dict 모두에서 공공 표시값을 읽는다."""

    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_text(value: object) -> str:
    candidate = getattr(value, "value", value)
    return str(candidate).upper()


def _safe_display_cell(value: object, *, limit: int = 80) -> str:
    """공급자 상태 표에는 제어문자·계좌번호형 문구를 그대로 내보내지 않는다."""

    text = mask_account_number_like_text(str(value))
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _plain_decimal_text(value: Decimal) -> str:
    """화면 백분율을 float 변환 없이 고정 십진 표기로 만든다."""

    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _safe_provider_timestamp(value: object, *, missing: str = "없음") -> str:
    """공급자 DB 시각만 파싱해 고정 형식으로 표시하고 임의 원문은 버린다."""

    raw = str(value)
    if raw in {"미수집", "미시도"}:
        return raw
    if len(raw) > 64:
        return "확인 불가"
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return missing if not raw else "확인 불가"
    return parsed.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M:%S %Z")


_JOB_DISPLAY_STATUSES = frozenset(
    {"RUNNING", "OK", "PARTIAL", "ERROR", "ABANDONED_STALE"}
)
_JOB_DISPLAY_DETAILS = frozenset(
    {
        "RUNNING",
        "RECOVERED_RETRY",
        "STALE_RETRY",
        "SAFE_FAILURE",
        "OK",
        "PARTIAL",
        "ERROR",
        "UNKNOWN",
    }
)
_JOB_TIME_PATTERN = re.compile(r"\A\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z]{1,8}\Z")


def _scheduled_job_history_text(rows: object) -> str:
    """서비스가 만든 고정형 실행 이력만 GUI에 표시한다."""

    if rows is None:
        return "최근 예약 실행 이력: 확인 불가"
    try:
        candidates = tuple(rows)[:4]  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "최근 예약 실행 이력: 확인 불가"
    if not candidates:
        return "최근 예약 실행 이력: 없음"

    lines = ["최근 예약 실행 이력:"]
    for row in candidates:
        task = str(_object_value(row, "task", ""))
        if task not in {"수집", "보고서", "알 수 없는 작업"}:
            task = "알 수 없는 작업"
        status = _enum_text(_object_value(row, "status", ""))
        if status not in _JOB_DISPLAY_STATUSES:
            status = "UNKNOWN"
        detail = _enum_text(_object_value(row, "detail_code", ""))
        if detail not in _JOB_DISPLAY_DETAILS:
            detail = "UNKNOWN"

        timestamps: list[str] = []
        for field, fallback in (
            ("scheduled_for", "확인 불가"),
            ("started_at", "확인 불가"),
            ("finished_at", "미기록"),
        ):
            value = str(_object_value(row, field, fallback))
            allowed_words = {"확인 불가", "진행 중", "미기록"}
            timestamps.append(
                value if value in allowed_words or _JOB_TIME_PATTERN.fullmatch(value) else fallback
            )
        scheduled, started, finished = timestamps
        lines.append(
            f"• {task} · 예약 {scheduled} · 시작 {started} · 종료 {finished} · "
            f"{status}/{detail}"
        )
    return "\n".join(lines)


class AssetAssistantApp:
    """정책 계산을 소유하지 않고 수동·자동화 서비스를 호출하는 Tk 화면."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        sample_path: Path = DEFAULT_SAMPLE,
        open_dialog: Callable[[], str] | None = None,
        save_dialog: Callable[[], str] | None = None,
        error_handler: Callable[[str], None] | None = None,
        automation_service: object | None = None,
        credential_store: object | None = None,
    ) -> None:
        self.root = root
        self.sample_path = sample_path
        self._open_dialog = open_dialog or self._default_open_dialog
        self._save_dialog = save_dialog or self._default_save_dialog
        self._error_handler = error_handler or self._default_error_handler
        # 기본 서비스는 시작 중 생성하지 않는다. 버튼을 누른 후에만
        # 저장소·보안 저장소를 열며, 네트워크 호출도 collect에만 위임한다.
        self._automation_service = automation_service
        self._credential_store = credential_store
        self._loaded: LoadedGuiData | None = None
        self._report: str | None = None
        self._report_source_path: Path | None = None
        # 자동 보고서 화면으로 전환한 뒤에도 이 세션에서 성공적으로 연
        # 입력 JSON을 보고서 저장 대상으로 덮어쓰지 못하게 보호한다.
        self._protected_input_paths: set[Path] = set()
        self._busy = False
        self._closed = False
        self._operation_token = 0
        self._settings_window: tk.Toplevel | None = None
        self._strategy_window: tk.Toplevel | None = None
        self._strategy_draft_initialized = False
        self._strategy_draft_generation = 0
        self._strategy_syncing = False
        self._strategy_saved_weights: dict[StrategyKind, Decimal] | None = None
        self._strategy_saved_version_display = "없음"
        self._strategy_saved_at_display = "없음"
        self._strategy_workspace: object | None = None
        self._selected_strategy = StrategyKind.BROAD_MARKET
        self.strategy_version_var = tk.StringVar(master=self.root, value="")
        self.strategy_weight_vars: dict[StrategyKind, tk.StringVar] = {
            strategy: tk.StringVar(master=self.root, value="0")
            for strategy in STRATEGY_ORDER
        }
        self.strategy_scale_vars: dict[StrategyKind, tk.StringVar] = {
            strategy: tk.StringVar(master=self.root, value="0")
            for strategy in STRATEGY_ORDER
        }
        self._strategy_form_state: StrategyFormState = validate_strategy_form(
            {strategy: "0" for strategy in STRATEGY_ORDER}
        )
        self._scheduled_report_polling = False
        self._last_scheduled_report_digest: str | None = None

        self.root.title("장기 자산관리 보조 프로그램 · 읽기 전용 자료 자동화")
        self.root.geometry("1180x760")
        self.root.minsize(1000, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._configure_fonts_and_styles()
        self._build_widgets()
        self._clear_loaded_state(message="샘플 또는 로컬 JSON을 선택해 주세요.")
        self.root.after(10_000, self._poll_scheduled_report)

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def current_data(self) -> LoadedGuiData | None:
        return self._loaded

    @property
    def current_report(self) -> str | None:
        return self._report

    @property
    def status_text(self) -> str:
        return self.status_var.get()

    def report_text_content(self) -> str:
        return self.report_text.get("1.0", "end-1c")

    def _configure_fonts_and_styles(self) -> None:
        families = set(tkfont.families(self.root))
        candidates = (
            "Noto Sans CJK KR",
            "Noto Sans KR",
            "Malgun Gothic",
            "맑은 고딕",
            "Apple SD Gothic Neo",
        )
        self.ui_font_family = next(
            (candidate for candidate in candidates if candidate in families),
            tkfont.nametofont("TkDefaultFont").actual("family"),
        )
        tkfont.nametofont("TkDefaultFont").configure(family=self.ui_font_family, size=10)
        tkfont.nametofont("TkTextFont").configure(family=self.ui_font_family, size=10)

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Header.TFrame", background="#17324d")
        style.configure(
            "HeaderTitle.TLabel",
            background="#17324d",
            foreground="white",
            font=(self.ui_font_family, 18, "bold"),
        )
        style.configure(
            "HeaderSub.TLabel",
            background="#17324d",
            foreground="#d7e6f4",
            font=(self.ui_font_family, 10),
        )
        style.configure(
            "Sample.TLabel",
            background="#fff0c2",
            foreground="#7a4300",
            font=(self.ui_font_family, 12, "bold"),
            padding=8,
        )
        style.configure("Value.TLabel", font=(self.ui_font_family, 11, "bold"))
        style.configure("Status.TLabel", foreground="#334155", padding=(8, 5))
        style.configure(
            "StrategyNotice.TLabel",
            background="#dbeafe",
            foreground="#1e3a8a",
            font=(self.ui_font_family, 10, "bold"),
            padding=9,
        )
        style.configure(
            "StrategyReady.TLabel",
            background="#e7f7f1",
            foreground="#0e765d",
            font=(self.ui_font_family, 10, "bold"),
            padding=8,
        )
        style.configure(
            "StrategyError.TLabel",
            background="#fff4e8",
            foreground="#b45309",
            font=(self.ui_font_family, 10, "bold"),
            padding=8,
        )
        style.configure(
            "StrategyTitle.TLabel",
            font=(self.ui_font_family, 18, "bold"),
            foreground="#161b23",
        )

    def _build_widgets(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, style="Header.TFrame", padding=(18, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="장기 자산관리 보조 프로그램",
            style="HeaderTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=(
                "공식자료 선택 연결 + 로컬 JSON 검산 · "
                "수익이나 목표 달성을 보장하지 않습니다"
            ),
            style="HeaderSub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        badge_frame = tk.Frame(header, bg="#17324d")
        badge_frame.grid(row=0, column=1, rowspan=2, sticky="e")
        self.scheduler_badge_var = tk.StringVar(value="예약 상태 확인")
        badges = (
            ("공식자료 선택 연결", "#d9f99d", "#365314"),
            ("인증 공급자 선택 설정", "#dbeafe", "#1e3a8a"),
            ("주문 기능 없음", "#fee2e2", "#7f1d1d"),
        )
        for column, (text, background, foreground) in enumerate(badges):
            tk.Label(
                badge_frame,
                text=text,
                bg=background,
                fg=foreground,
                padx=9,
                pady=5,
                font=(self.ui_font_family, 9, "bold"),
            ).grid(row=0, column=column, padx=(6, 0))
        tk.Label(
            badge_frame,
            textvariable=self.scheduler_badge_var,
            bg="#e5e7eb",
            fg="#374151",
            padx=9,
            pady=5,
            font=(self.ui_font_family, 9, "bold"),
        ).grid(row=0, column=len(badges), padx=(6, 0))

        toolbar = ttk.Frame(self.root, padding=(14, 10))
        toolbar.grid(row=1, column=0, sticky="ew")
        self.sample_button = ttk.Button(toolbar, text="샘플로 시작", command=self.load_sample)
        self.open_button = ttk.Button(toolbar, text="자료 열기", command=self.choose_input_file)
        self.generate_button = ttk.Button(toolbar, text="보고서 생성", command=self.generate_report)
        self.save_button = ttk.Button(toolbar, text="보고서 저장", command=self.save_report_dialog)
        self.market_collect_button = ttk.Button(
            toolbar,
            text="시장자료 갱신",
            command=self.collect_market_data,
        )
        self.automated_report_button = ttk.Button(
            toolbar,
            text="자동 보고서",
            command=self.generate_automated_report,
        )
        self.latest_report_button = ttk.Button(
            toolbar,
            text="최근 자동보고서",
            command=self.show_latest_automated_report,
        )
        self.provider_settings_button = ttk.Button(
            toolbar,
            text="공급자 설정",
            command=self.open_provider_settings,
        )
        self.strategy_settings_button = ttk.Button(
            toolbar,
            text="투자전략",
            command=self.open_strategy_settings,
        )
        self.strategy_button = self.strategy_settings_button
        toolbar_buttons = (
            self.sample_button,
            self.open_button,
            self.generate_button,
            self.save_button,
            self.market_collect_button,
            self.automated_report_button,
            self.latest_report_button,
            self.strategy_settings_button,
            self.provider_settings_button,
        )
        for index, widget in enumerate(toolbar_buttons):
            widget.grid(
                row=index // 5,
                column=index % 5,
                sticky="w",
                padx=(0, 8),
                pady=(0, 4) if index < 5 else 0,
            )
        toolbar.columnconfigure(5, weight=1)
        self.progress = ttk.Progressbar(toolbar, mode="indeterminate", length=150)
        self.progress.grid(row=0, column=5, rowspan=2, sticky="e", padx=(10, 0))

        content = ttk.Panedwindow(self.root, orient="horizontal")
        content.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 10))

        dashboard = ttk.LabelFrame(content, text="대시보드", padding=12)
        self.dashboard = dashboard
        report_frame = ttk.LabelFrame(content, text="보고서 보기", padding=8)
        content.add(dashboard, weight=1)
        content.add(report_frame, weight=3)

        dashboard.columnconfigure(1, weight=1)
        self.sample_banner = ttk.Label(
            dashboard,
            text="가상 샘플 · 실제 잔고 아님",
            style="Sample.TLabel",
            anchor="center",
        )
        self.sample_banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        self.title_var = tk.StringVar()
        self.realm_var = tk.StringVar()
        self.as_of_var = tk.StringVar()
        self.investment_var = tk.StringVar()
        self.declared_var = tk.StringVar()
        self.reconciliation_var = tk.StringVar()
        self.freshness_var = tk.StringVar()

        fields = (
            ("보고서 제목", self.title_var),
            ("자료 영역", self.realm_var),
            ("기준 시각", self.as_of_var),
            ("투자자산 합계", self.investment_var),
            ("초기자산 선언값", self.declared_var),
            ("원장 대조", self.reconciliation_var),
            ("자료 상태", self.freshness_var),
        )
        for row, (label, variable) in enumerate(fields, start=1):
            ttk.Label(dashboard, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 10), pady=4)
            ttk.Label(
                dashboard,
                textvariable=variable,
                style="Value.TLabel" if label == "투자자산 합계" else "TLabel",
                wraplength=270,
                justify="left",
            ).grid(row=row, column=1, sticky="nw", pady=4)

        ttk.Label(dashboard, text="주요 경고").grid(row=8, column=0, columnspan=2, sticky="w", pady=(12, 4))
        warning_frame = ttk.Frame(dashboard)
        warning_frame.grid(row=9, column=0, columnspan=2, sticky="nsew")
        warning_frame.columnconfigure(0, weight=1)
        warning_frame.rowconfigure(0, weight=1)
        self.warning_text = tk.Text(
            warning_frame,
            height=9,
            wrap="word",
            relief="flat",
            background="#fff7ed",
            foreground="#7c2d12",
            padx=8,
            pady=8,
            font=(self.ui_font_family, 9),
        )
        warning_scroll = ttk.Scrollbar(warning_frame, orient="vertical", command=self.warning_text.yview)
        self.warning_text.configure(yscrollcommand=warning_scroll.set)
        self.warning_text.grid(row=0, column=0, sticky="nsew")
        warning_scroll.grid(row=0, column=1, sticky="ns")

        warning_font = tkfont.Font(root=self.root, font=self.warning_text.cget("font"))
        warning_padding = 2 * int(self.warning_text.cget("pady"))
        dashboard.rowconfigure(
            9,
            weight=1,
            minsize=warning_font.metrics("linespace") * 3 + warning_padding + 4,
        )

        self.operation_requirements_label = ttk.Label(
            dashboard,
            text=(
                "운영 기준: 월별 자산 정리 · 매일 15:00 자료 검토 · "
                "16:00 보고서 생성 · Asia/Seoul · PC 종료 중 실행 보장 없음"
            ),
            wraplength=330,
            foreground="#475569",
            justify="left",
        )
        self.operation_requirements_label.grid(
            row=10,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(12, 0),
        )

        report_frame.columnconfigure(0, weight=1)
        report_frame.rowconfigure(0, weight=1)
        self.report_text = tk.Text(
            report_frame,
            wrap="none",
            undo=False,
            padx=12,
            pady=10,
            background="#fbfdff",
            foreground="#111827",
            font=(self.ui_font_family, 10),
        )
        report_y = ttk.Scrollbar(report_frame, orient="vertical", command=self.report_text.yview)
        report_x = ttk.Scrollbar(report_frame, orient="horizontal", command=self.report_text.xview)
        self.report_text.configure(yscrollcommand=report_y.set, xscrollcommand=report_x.set)
        self.report_text.grid(row=0, column=0, sticky="nsew")
        report_y.grid(row=0, column=1, sticky="ns")
        report_x.grid(row=1, column=0, sticky="ew")
        self.report_text.tag_configure("h1", font=(self.ui_font_family, 16, "bold"), foreground="#17324d")
        self.report_text.tag_configure("h2", font=(self.ui_font_family, 13, "bold"), foreground="#1d4ed8")
        self.report_text.tag_configure("h3", font=(self.ui_font_family, 11, "bold"), foreground="#334155")
        self.report_text.tag_configure("quote", foreground="#475569")

        self.status_var = tk.StringVar()
        status = ttk.Frame(self.root)
        status.grid(row=3, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            status,
            text="공식 공급자 읽기 전용 · 투자 판단과 주문 권한은 사용자에게만 있음",
            style="Status.TLabel",
        ).grid(row=0, column=1, sticky="e")

    def _default_open_dialog(self) -> str:
        return filedialog.askopenfilename(
            parent=self.root,
            title="자산 자료 JSON 열기",
            filetypes=(("JSON 자료", "*.json"), ("모든 파일", "*.*")),
        )

    def _default_save_dialog(self) -> str:
        return filedialog.asksaveasfilename(
            parent=self.root,
            title="Markdown 보고서 저장",
            defaultextension=".md",
            filetypes=(("Markdown 보고서", "*.md"), ("모든 파일", "*.*")),
        )

    def _default_error_handler(self, message: str) -> None:
        messagebox.showerror("작업 오류", message, parent=self.root)

    def _get_automation_service(self) -> object:
        if self._automation_service is None:
            # 지연 import/생성으로 GUI 시작 시 저장소나 네트워크를
            # 열지 않는다. 실제 호출은 아래 명시적 버튼 동작에서만 한다.
            from .automation import AutomationService

            self._automation_service = AutomationService()
        return self._automation_service

    def _get_credential_store(self) -> object:
        if self._credential_store is None:
            self._credential_store = CredentialStore()
        return self._credential_store

    def _selected_provider_fields(self) -> tuple[str, ...]:
        provider = self.settings_provider_var.get()
        return tuple(sorted(ALLOWED_FIELDS.get(provider, ())))

    def _update_settings_field_choices(self, _event: object | None = None) -> None:
        fields = self._selected_provider_fields()
        self.settings_field_combo.configure(values=fields)
        if self.settings_field_var.get() not in fields:
            self.settings_field_var.set(fields[0] if fields else "")
        field = self.settings_field_var.get()
        self.settings_field_label_var.set(FIELD_LABELS.get(field, field or "선택 필요"))
        if _event is not None:
            self.refresh_provider_settings()

    def _update_settings_field_label(self, _event: object | None = None) -> None:
        del _event
        field = self.settings_field_var.get()
        self.settings_field_label_var.set(FIELD_LABELS.get(field, field or "선택 필요"))

    def open_provider_settings(self) -> None:
        """공급자 상태와 OS 보안 저장소 설정을 별도 창에 표시한다."""

        if self._settings_window is not None:
            try:
                if self._settings_window.winfo_exists():
                    self._settings_window.deiconify()
                    self._settings_window.lift()
                    self.refresh_provider_settings()
                    return
            except tk.TclError:
                pass

        window = tk.Toplevel(self.root)
        self._settings_window = window
        window.title("공식자료 공급자 설정 · 읽기 전용")
        window.geometry("1020x840")
        window.minsize(900, 700)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        ttk.Label(
            window,
            text=(
                "시장자료 조회만 설정합니다. 계좌 조회·주문 기능은 없으며, "
                "인증정보는 평문 파일이 아닌 OS 보안 저장소에만 보관합니다."
            ),
            wraplength=840,
            justify="left",
            foreground="#334155",
            padding=(12, 10),
        ).grid(row=0, column=0, sticky="ew")

        status_frame = ttk.LabelFrame(window, text="공급자 연결 준비 상태", padding=8)
        status_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)
        columns = ("provider", "state", "effective", "collected", "attempted")
        self.provider_status_tree = ttk.Treeview(
            status_frame,
            columns=columns,
            show="headings",
            height=7,
        )
        headings = {
            "provider": "공급자",
            "state": "상태",
            "effective": "자료 기준 시각",
            "collected": "정상자료 수집 시각",
            "attempted": "최근 갱신 시도",
        }
        widths = {
            "provider": 210,
            "state": 125,
            "effective": 165,
            "collected": 165,
            "attempted": 165,
        }
        for column in columns:
            self.provider_status_tree.heading(column, text=headings[column])
            self.provider_status_tree.column(column, width=widths[column], minwidth=100)
        tree_scroll = ttk.Scrollbar(
            status_frame,
            orient="vertical",
            command=self.provider_status_tree.yview,
        )
        self.provider_status_tree.configure(yscrollcommand=tree_scroll.set)
        self.provider_status_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")

        credential_frame = ttk.LabelFrame(window, text="인증정보 등록·교체·삭제", padding=10)
        credential_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        credential_frame.columnconfigure(5, weight=1)
        self.settings_provider_var = tk.StringVar(value=next(iter(ALLOWED_FIELDS)))
        self.settings_field_var = tk.StringVar()
        self.settings_field_label_var = tk.StringVar()
        ttk.Label(credential_frame, text="공급자").grid(row=0, column=0, sticky="w")
        self.settings_provider_combo = ttk.Combobox(
            credential_frame,
            textvariable=self.settings_provider_var,
            values=tuple(ALLOWED_FIELDS),
            state="readonly",
            width=15,
        )
        self.settings_provider_combo.grid(row=0, column=1, sticky="w", padx=(6, 14))
        self.settings_provider_combo.bind("<<ComboboxSelected>>", self._update_settings_field_choices)
        ttk.Label(credential_frame, text="항목").grid(row=0, column=2, sticky="w")
        self.settings_field_combo = ttk.Combobox(
            credential_frame,
            textvariable=self.settings_field_var,
            state="readonly",
            width=17,
        )
        self.settings_field_combo.grid(row=0, column=3, sticky="w", padx=(6, 14))
        self.settings_field_combo.bind("<<ComboboxSelected>>", self._update_settings_field_label)
        ttk.Label(credential_frame, textvariable=self.settings_field_label_var).grid(
            row=0, column=4, sticky="w", padx=(0, 8)
        )
        self.secret_entry = ttk.Entry(credential_frame, show="•", width=28)
        self.secret_entry.grid(row=0, column=5, sticky="ew", padx=(0, 8))
        self.save_secret_button = ttk.Button(
            credential_frame,
            text="등록/교체",
            command=self.save_selected_secret,
        )
        self.save_secret_button.grid(row=0, column=6, padx=(0, 6))
        self.delete_secret_button = ttk.Button(
            credential_frame,
            text="삭제",
            command=self.delete_selected_secret,
        )
        self.delete_secret_button.grid(row=0, column=7)

        target_frame = ttk.LabelFrame(window, text="수집 대상·활성 상태", padding=10)
        target_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        target_frame.columnconfigure(2, weight=1)
        self.target_provider_var = tk.StringVar(value="bls")
        self.provider_enabled_var = tk.BooleanVar(value=True)
        self.kis_environment_var = tk.StringVar(value="paper")
        ttk.Label(target_frame, text="공급자").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            target_frame,
            textvariable=self.target_provider_var,
            values=TARGET_PROVIDERS,
            state="readonly",
            width=13,
        ).grid(row=0, column=1, sticky="w", padx=(6, 8))
        self.target_entry = ttk.Entry(target_frame)
        self.target_entry.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        ttk.Checkbutton(
            target_frame, text="활성", variable=self.provider_enabled_var
        ).grid(row=0, column=3, padx=(0, 8))
        self.save_targets_button = ttk.Button(
            target_frame, text="설정 저장", command=self.save_provider_targets
        )
        self.save_targets_button.grid(row=0, column=4)
        ttk.Label(
            target_frame,
            text="쉼표 구분: BLS series ID, SEC 10자리 CIK, OpenDART 8자리 corp code, KIS 6자리 종목코드",
            foreground="#475569",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Label(target_frame, text="KIS 환경").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            target_frame,
            textvariable=self.kis_environment_var,
            values=("paper", "live"),
            state="readonly",
            width=13,
        ).grid(row=2, column=1, sticky="w", padx=(6, 8), pady=(6, 0))
        ttk.Label(
            target_frame,
            text="paper/live 인증은 OS 보안 저장소에 별도 등록되며 시세 조회에만 사용됩니다.",
            foreground="#475569",
        ).grid(row=2, column=2, columnspan=3, sticky="w", pady=(6, 0))

        strategy_frame = ttk.LabelFrame(window, text="후보 평가 전략 혼합", padding=10)
        strategy_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
        strategy_frame.columnconfigure(0, weight=1)
        ttk.Label(
            strategy_frame,
            text=(
                "전용 화면에서 8개 후보 평가 가중치와 최신 평가 근거를 확인합니다. "
                "기존 자산배분·위험한도·주문 승인은 바뀌지 않습니다."
            ),
            foreground="#475569",
            wraplength=760,
        ).grid(row=0, column=0, sticky="w")
        self.provider_strategy_button = ttk.Button(
            strategy_frame,
            text="투자전략 화면 열기",
            command=self.open_strategy_settings,
        )
        self.provider_strategy_button.grid(row=0, column=1, sticky="e", padx=(12, 0))

        bottom = ttk.Frame(window, padding=(12, 0, 12, 10))
        bottom.grid(row=5, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        self.settings_status_var = tk.StringVar(value="상태를 확인하지 않았습니다.")
        self.secure_store_status_var = tk.StringVar(value="OS 보안 저장소: 확인 전")
        self.scheduler_status_var = tk.StringVar(value="예약: 확인 전")
        self.scheduled_job_history_var = tk.StringVar(value="최근 예약 실행 이력: 확인 전")
        ttk.Label(bottom, textvariable=self.settings_status_var, wraplength=600).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(bottom, textvariable=self.secure_store_status_var).grid(
            row=1, column=0, sticky="w", pady=(3, 0)
        )
        ttk.Label(bottom, textvariable=self.scheduler_status_var, wraplength=600).grid(
            row=2, column=0, sticky="w", pady=(3, 0)
        )
        ttk.Label(
            bottom,
            textvariable=self.scheduled_job_history_var,
            wraplength=880,
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Button(bottom, text="상태 새로고침", command=self.refresh_provider_settings).grid(
            row=0, column=1, rowspan=2, padx=(8, 6)
        )
        ttk.Button(
            bottom,
            text="저장된 최신 보고서 보기",
            command=self.show_latest_automated_report,
        ).grid(row=0, column=2, rowspan=2)

        self._update_settings_field_choices()
        self.refresh_provider_settings()

    def _strategy_window_available(self) -> bool:
        if self._strategy_window is None:
            return False
        try:
            return bool(self._strategy_window.winfo_exists())
        except tk.TclError:
            return False

    def _close_strategy_window(self) -> None:
        window = self._strategy_window
        self._strategy_window = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def open_strategy_settings(self) -> None:
        """후보 평가 전략 비중을 편집하는 스크롤 가능 전용 창을 연다."""

        if self._strategy_window_available():
            assert self._strategy_window is not None
            self._strategy_window.deiconify()
            self._strategy_window.lift()
            self.refresh_strategy_workspace()
            return

        window = tk.Toplevel(self.root)
        self._strategy_window = window
        window.title("투자전략 혼합 설정 · 후보 평가용")
        window.geometry("1180x780")
        window.minsize(1000, 680)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._close_strategy_window)

        header = ttk.Frame(window, padding=(20, 15, 20, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="투자전략 혼합 설정",
            style="StrategyTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="8개 전략의 후보 평가 가중치를 조정하고 합계가 정확히 100%일 때 새 버전으로 저장합니다.",
            foreground="#6c7480",
        ).grid(row=1, column=0, sticky="w", pady=(3, 7))
        self.strategy_policy_notice_label = ttk.Label(
            header,
            text="후보 평가용 가중치이며 실제 자산배분·매수 금액이 아닙니다.",
            style="StrategyNotice.TLabel",
            anchor="w",
        )
        self.strategy_policy_notice_label.grid(row=2, column=0, sticky="ew")

        controls = ttk.Frame(header)
        controls.grid(row=0, column=1, rowspan=3, sticky="e", padx=(18, 0))
        self.strategy_total_var = tk.StringVar(master=window, value="0% / 100%")
        self.strategy_guidance_var = tk.StringVar(master=window, value="100%를 추가해야 합니다.")
        ttk.Label(controls, text="전략 합계", foreground="#6c7480").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            controls,
            textvariable=self.strategy_total_var,
            font=(self.ui_font_family, 16, "bold"),
            foreground="#2563eb",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(1, 5))
        ttk.Label(controls, text="새 버전 이름").grid(row=2, column=0, sticky="w")
        self.strategy_version_entry = ttk.Entry(
            controls,
            textvariable=self.strategy_version_var,
            width=22,
        )
        self.strategy_version_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(6, 0))
        self.strategy_restore_button = ttk.Button(
            controls,
            text="비중 복원",
            command=self.restore_strategy_mix,
        )
        self.strategy_restore_button.grid(row=3, column=1, sticky="e", pady=(7, 0), padx=(0, 6))
        self.save_strategy_button = ttk.Button(
            controls,
            text="새 버전 저장",
            command=self.save_strategy_mix,
        )
        self.save_strategy_button.grid(row=3, column=2, sticky="e", pady=(7, 0))

        canvas_frame = ttk.Frame(window)
        canvas_frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)
        self.strategy_canvas = tk.Canvas(
            canvas_frame,
            background="#f7f8fa",
            highlightthickness=0,
        )
        strategy_scroll = ttk.Scrollbar(
            canvas_frame,
            orient="vertical",
            command=self.strategy_canvas.yview,
        )
        self.strategy_canvas.configure(yscrollcommand=strategy_scroll.set)
        self.strategy_canvas.grid(row=0, column=0, sticky="nsew")
        strategy_scroll.grid(row=0, column=1, sticky="ns")

        self.strategy_content_frame = ttk.Frame(self.strategy_canvas, padding=(8, 6, 8, 16))
        content_window = self.strategy_canvas.create_window(
            (0, 0),
            window=self.strategy_content_frame,
            anchor="nw",
        )
        self.strategy_content_frame.bind(
            "<Configure>",
            lambda _event: self.strategy_canvas.configure(
                scrollregion=self.strategy_canvas.bbox("all")
            ),
        )
        def resize_strategy_content(event: tk.Event) -> None:
            self.strategy_canvas.itemconfigure(
                content_window,
                width=max(int(event.width), 960),
            )
            self._layout_strategy_content(int(event.width))

        self.strategy_canvas.bind("<Configure>", resize_strategy_content)
        self.strategy_content_frame.columnconfigure(0, weight=3)
        self.strategy_content_frame.columnconfigure(1, weight=2)

        weight_frame = ttk.LabelFrame(
            self.strategy_content_frame,
            text="전략별 비중 설정(8개)",
            padding=10,
        )
        weight_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.strategy_weight_frame = weight_frame
        weight_frame.columnconfigure(0, weight=1)
        self.strategy_detail_buttons: dict[StrategyKind, ttk.Button] = {}
        self.strategy_scale_widgets: dict[StrategyKind, tk.Scale] = {}
        self.strategy_entry_widgets: dict[StrategyKind, ttk.Entry] = {}
        for row, strategy in enumerate(STRATEGY_ORDER):
            card = ttk.Frame(weight_frame, padding=(5, 7))
            card.grid(row=row, column=0, sticky="ew", pady=2)
            card.columnconfigure(1, weight=1)
            detail_button = ttk.Button(
                card,
                text=f"{STRATEGY_DISPLAY_NAMES[strategy]} · 상세보기",
                command=lambda selected=strategy: self.show_strategy_detail(selected),
                width=23,
            )
            detail_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
            scale = tk.Scale(
                card,
                from_=0,
                to=100,
                resolution=0.01,
                orient="horizontal",
                showvalue=False,
                variable=self.strategy_scale_vars[strategy],
                command=lambda value, selected=strategy: self._on_strategy_scale(
                    selected, value
                ),
                length=270,
                takefocus=True,
                background="#ffffff",
                activebackground="#2563eb",
                highlightthickness=0,
                troughcolor="#e5e8ed",
            )
            scale.grid(row=0, column=1, sticky="ew", padx=(0, 8))
            entry = ttk.Entry(
                card,
                textvariable=self.strategy_weight_vars[strategy],
                width=9,
                justify="right",
            )
            entry.grid(row=0, column=2, sticky="e")
            ttk.Label(card, text="%").grid(row=0, column=3, sticky="w", padx=(3, 0))
            self.strategy_detail_buttons[strategy] = detail_button
            self.strategy_scale_widgets[strategy] = scale
            self.strategy_entry_widgets[strategy] = entry

        self.strategy_guidance_label = ttk.Label(
            weight_frame,
            textvariable=self.strategy_guidance_var,
            style="StrategyError.TLabel",
            anchor="w",
        )
        self.strategy_guidance_label.grid(
            row=len(STRATEGY_ORDER),
            column=0,
            sticky="ew",
            pady=(9, 0),
        )

        side = ttk.Frame(self.strategy_content_frame)
        side.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.strategy_side_frame = side
        side.columnconfigure(0, weight=1)

        summary_frame = ttk.LabelFrame(side, text="전략 혼합 요약", padding=9)
        summary_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        summary_frame.columnconfigure(0, weight=1)
        self.strategy_summary_text = tk.Text(
            summary_frame,
            height=12,
            wrap="word",
            relief="flat",
            background="#ffffff",
            foreground="#161b23",
            padx=6,
            pady=6,
            font=(self.ui_font_family, 9),
        )
        self.strategy_summary_text.grid(row=0, column=0, sticky="ew")

        detail_frame = ttk.LabelFrame(side, text="전략 상세", padding=9)
        detail_frame.grid(row=1, column=0, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        self.strategy_detail_title_var = tk.StringVar(master=window, value="")
        ttk.Label(
            detail_frame,
            textvariable=self.strategy_detail_title_var,
            font=(self.ui_font_family, 12, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.strategy_detail_text = tk.Text(
            detail_frame,
            height=24,
            wrap="word",
            relief="flat",
            background="#ffffff",
            foreground="#161b23",
            padx=6,
            pady=6,
            font=(self.ui_font_family, 9),
        )
        detail_scroll = ttk.Scrollbar(
            detail_frame,
            orient="vertical",
            command=self.strategy_detail_text.yview,
        )
        self.strategy_detail_text.configure(yscrollcommand=detail_scroll.set)
        self.strategy_detail_text.grid(row=1, column=0, sticky="nsew")
        detail_scroll.grid(row=1, column=1, sticky="ns")

        assessment_frame = ttk.LabelFrame(
            self.strategy_content_frame,
            text="최근 후보 평가 · 저장 당시 출처",
            padding=9,
        )
        assessment_frame.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="nsew",
            pady=(12, 0),
        )
        self.strategy_assessment_frame = assessment_frame
        assessment_frame.columnconfigure(0, weight=1)
        self.strategy_assessment_text = tk.Text(
            assessment_frame,
            height=13,
            wrap="word",
            relief="flat",
            background="#ffffff",
            foreground="#161b23",
            padx=8,
            pady=8,
            font=(self.ui_font_family, 9),
        )
        assessment_scroll = ttk.Scrollbar(
            assessment_frame,
            orient="vertical",
            command=self.strategy_assessment_text.yview,
        )
        self.strategy_assessment_text.configure(yscrollcommand=assessment_scroll.set)
        self.strategy_assessment_text.grid(row=0, column=0, sticky="nsew")
        assessment_scroll.grid(row=0, column=1, sticky="ns")

        if not hasattr(self, "_strategy_trace_ids"):
            self._strategy_trace_ids = {
                strategy: variable.trace_add(
                    "write",
                    lambda *_args, selected=strategy: self._on_strategy_entry_change(
                        selected
                    ),
                )
                for strategy, variable in self.strategy_weight_vars.items()
            }
            self._strategy_version_trace_id = self.strategy_version_var.trace_add(
                "write", lambda *_args: self._refresh_strategy_form()
            )

        self._refresh_strategy_form()
        self.show_strategy_detail(self._selected_strategy)
        self.refresh_strategy_workspace()

    def _layout_strategy_content(self, width: int) -> None:
        """폭이 좁으면 오른쪽 요약을 아래로 내려 폰트 축소를 피한다."""

        if not self._strategy_window_available() or not hasattr(self, "strategy_side_frame"):
            return
        if width < 1200:
            self.strategy_weight_frame.grid_configure(
                row=0,
                column=0,
                columnspan=2,
                padx=0,
            )
            self.strategy_side_frame.grid_configure(
                row=1,
                column=0,
                columnspan=2,
                padx=0,
                pady=(12, 0),
            )
            self.strategy_assessment_frame.grid_configure(row=2)
        else:
            self.strategy_weight_frame.grid_configure(
                row=0,
                column=0,
                columnspan=1,
                padx=(0, 8),
            )
            self.strategy_side_frame.grid_configure(
                row=0,
                column=1,
                columnspan=1,
                padx=(8, 0),
                pady=0,
            )
            self.strategy_assessment_frame.grid_configure(row=1)

    def _on_strategy_scale(self, strategy: StrategyKind, value: str) -> None:
        if self._strategy_syncing:
            return
        try:
            parsed = strategy_weight_decimal(value)
        except DomainValidationError:
            return
        self._strategy_syncing = True
        try:
            self.strategy_weight_vars[strategy].set(_plain_decimal_text(parsed))
        finally:
            self._strategy_syncing = False
        self._strategy_draft_generation += 1
        self._refresh_strategy_form()

    def _on_strategy_entry_change(self, strategy: StrategyKind) -> None:
        if self._strategy_syncing:
            return
        raw = self.strategy_weight_vars[strategy].get().strip()
        try:
            parsed = strategy_weight_decimal(raw)
        except DomainValidationError:
            parsed = None
        if parsed is not None:
            self._strategy_syncing = True
            try:
                self.strategy_scale_vars[strategy].set(_plain_decimal_text(parsed))
            finally:
                self._strategy_syncing = False
        self._strategy_draft_generation += 1
        self._refresh_strategy_form()

    def _strategy_version_is_valid(self) -> bool:
        value = self.strategy_version_var.get()
        return bool(
            value.strip()
            and len(value) <= 100
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )

    def _refresh_strategy_form(self) -> None:
        state = validate_strategy_form(
            {
                strategy: self.strategy_weight_vars[strategy].get()
                for strategy in STRATEGY_ORDER
            }
        )
        self._strategy_form_state = state
        if not self._strategy_window_available():
            return
        if state.total is None:
            self.strategy_total_var.set("입력 확인 / 100%")
        else:
            self.strategy_total_var.set(f"{_plain_decimal_text(state.total)}% / 100%")
        guidance = state.guidance
        if state.can_save and not self._strategy_version_is_valid():
            guidance = "합계 100%입니다. 새 버전 이름을 입력해 주세요."
        self.strategy_guidance_var.set(guidance)
        self.strategy_guidance_label.configure(
            style="StrategyReady.TLabel"
            if state.can_save and self._strategy_version_is_valid()
            else "StrategyError.TLabel"
        )
        self.save_strategy_button.configure(
            state=(
                "normal"
                if not self._busy and state.can_save and self._strategy_version_is_valid()
                else "disabled"
            )
        )
        self._render_strategy_summary()

    def _render_strategy_summary(self) -> None:
        if not self._strategy_window_available() or not hasattr(self, "strategy_summary_text"):
            return
        lines = [
            f"저장 버전: {self._strategy_saved_version_display}",
            f"저장 시각: {self._strategy_saved_at_display}",
            "",
        ]
        state = self._strategy_form_state
        for strategy in STRATEGY_ORDER:
            weight = state.weights.get(strategy)
            value = "입력 확인" if weight is None else f"{_plain_decimal_text(weight)}%"
            lines.append(f"• {STRATEGY_DISPLAY_NAMES[strategy]}: {value}")
        self._replace_readonly_text(self.strategy_summary_text, "\n".join(lines))

    def _set_strategy_draft(
        self,
        weights: dict[StrategyKind, Decimal],
        *,
        mark_edited: bool = False,
    ) -> None:
        self._strategy_syncing = True
        try:
            for strategy in STRATEGY_ORDER:
                text = _plain_decimal_text(weights.get(strategy, Decimal("0")))
                self.strategy_weight_vars[strategy].set(text)
                self.strategy_scale_vars[strategy].set(text)
        finally:
            self._strategy_syncing = False
        if mark_edited:
            self._strategy_draft_generation += 1
        self._refresh_strategy_form()

    def restore_strategy_mix(self) -> None:
        restored = self._strategy_saved_weights or {
            strategy: Decimal("0") for strategy in STRATEGY_ORDER
        }
        self._set_strategy_draft(dict(restored), mark_edited=True)
        self.strategy_version_var.set("")
        message = (
            "마지막 저장 버전의 비중으로 복원했습니다."
            if self._strategy_saved_weights is not None
            else "저장된 전략이 없어 0% 편집 상태로 복원했습니다."
        )
        self.status_var.set(message)

    def refresh_strategy_workspace(self) -> None:
        if self._busy or not self._strategy_window_available():
            return
        self._replace_readonly_text(self.strategy_summary_text, "저장된 전략을 확인하는 중입니다…")
        self._replace_readonly_text(self.strategy_assessment_text, "최근 후보 평가를 확인하는 중입니다…")
        self._set_busy(True, status="투자전략과 후보 평가 상태를 확인하는 중입니다…")
        draft_generation = self._strategy_draft_generation

        def read_workspace() -> object:
            service = self._get_automation_service()
            reader = getattr(service, "strategy_workspace_snapshot", None)
            if callable(reader):
                return reader(), draft_generation
            current_reader = getattr(service, "current_strategy_mix", None)
            current = current_reader() if callable(current_reader) else None
            return (
                {
                    "current_mix": current,
                    "assessments": (),
                    "input_states": (),
                },
                draft_generation,
            )

        self._run_async(
            "strategy_workspace",
            read_workspace,
            self._apply_strategy_workspace,
            lambda _message: self._strategy_workspace_failed(),
        )

    def _strategy_workspace_failed(self) -> None:
        self._strategy_workspace = None
        if self._strategy_window_available():
            self._replace_readonly_text(
                self.strategy_summary_text,
                "저장 상태를 확인할 수 없습니다. 편집 중인 값은 유지했습니다.",
            )
            self._replace_readonly_text(
                self.strategy_assessment_text,
                "최근 후보 평가를 확인할 수 없습니다. 이전 결과를 새 결과로 표시하지 않습니다.",
            )
            self.show_strategy_detail(self._selected_strategy)

    def _apply_strategy_workspace(self, payload: object) -> None:
        if not self._strategy_window_available():
            return
        try:
            snapshot, requested_generation = payload  # type: ignore[misc]
        except (TypeError, ValueError):
            snapshot, requested_generation = payload, self._strategy_draft_generation
        self._strategy_workspace = snapshot
        current = _object_value(snapshot, "current_mix", None)
        saved_weights = {strategy: Decimal("0") for strategy in STRATEGY_ORDER}
        version = "없음"
        saved_at = "없음"
        if current is not None:
            version = _safe_display_cell(
                _object_value(current, "version_display", _object_value(current, "version", "확인 불가")),
                limit=100,
            )
            saved_at = _safe_provider_timestamp(
                _object_value(current, "saved_at", ""),
                missing="확인 불가",
            )
            raw_weights = _object_value(current, "weights", ())
            if isinstance(raw_weights, dict):
                weight_items = raw_weights.items()
            else:
                weight_items = (
                    (
                        _object_value(row, "strategy", ""),
                        _object_value(row, "weight", "0"),
                    )
                    for row in raw_weights  # type: ignore[union-attr]
                )
            for raw_strategy, raw_weight in weight_items:
                try:
                    strategy = (
                        raw_strategy
                        if isinstance(raw_strategy, StrategyKind)
                        else StrategyKind(str(getattr(raw_strategy, "value", raw_strategy)))
                    )
                    parsed = Decimal(str(raw_weight))
                except (ValueError, InvalidOperation, TypeError):
                    continue
                if parsed.is_finite() and 0 <= parsed <= 100:
                    saved_weights[strategy] = parsed
            self._strategy_saved_weights = dict(saved_weights)
            if (
                not self._strategy_draft_initialized
                and requested_generation == self._strategy_draft_generation
            ):
                self._set_strategy_draft(saved_weights)
            self._strategy_draft_initialized = True
        elif not self._strategy_draft_initialized:
            self._strategy_saved_weights = None
            self._strategy_draft_initialized = True
        self._strategy_saved_version_display = version
        self._strategy_saved_at_display = saved_at
        self._render_strategy_summary()
        self._render_candidate_assessments()
        self.show_strategy_detail(self._selected_strategy)
        self.status_var.set("투자전략과 최근 후보 평가 상태를 확인했습니다. 네트워크 호출은 하지 않았습니다.")
        self._refresh_strategy_form()

    def show_strategy_detail(self, strategy: StrategyKind) -> None:
        self._selected_strategy = strategy
        if not self._strategy_window_available():
            return
        detail = STRATEGY_DETAILS[strategy]
        self.strategy_detail_title_var.set(detail.display_name)
        lines = [
            "설명",
            detail.description,
            "",
            "선정 기준",
            *(f"• {value}" for value in detail.selection_criteria),
            "",
            "장점",
            *(f"• {value}" for value in detail.strengths),
            "",
            "주의점",
            *(f"• {value}" for value in detail.cautions),
            "",
            "필요 입력 자료와 현재 상태",
        ]
        input_rows = ()
        if self._strategy_workspace is not None:
            input_rows = _object_value(self._strategy_workspace, "input_states", ())
        matched = []
        for row in input_rows:  # type: ignore[union-attr]
            raw_strategy = _object_value(row, "strategy", "")
            raw_value = str(getattr(raw_strategy, "value", raw_strategy))
            if raw_strategy is strategy or raw_value == strategy.value:
                matched.append(row)
        if matched:
            for row in matched:
                label = _safe_display_cell(_object_value(row, "input_label", "입력 자료"))
                provider = _safe_display_cell(_object_value(row, "provider_label", "공급자 확인 필요"))
                state = _safe_display_cell(_object_value(row, "state", "결측"))
                note = _safe_display_cell(_object_value(row, "detail", ""), limit=140)
                lines.append(f"• {label} · {provider} · {state} · {note}")
        else:
            lines.extend(f"• {value} · 결측/연결 상태 확인 필요" for value in detail.required_inputs)
        self._replace_readonly_text(self.strategy_detail_text, "\n".join(lines))

    def _render_candidate_assessments(self) -> None:
        if not self._strategy_window_available():
            return
        assessments = ()
        if self._strategy_workspace is not None:
            assessments = _object_value(self._strategy_workspace, "assessments", ())
        if not assessments:
            self._replace_readonly_text(
                self.strategy_assessment_text,
                "최근 후보 평가가 없습니다. 자료가 없으면 평가 불가이며 0점으로 대체하지 않습니다.",
            )
            return
        lines: list[str] = []
        for row in assessments:  # type: ignore[union-attr]
            instrument = _safe_display_cell(_object_value(row, "instrument_display", "식별자 확인 필요"))
            version = _safe_display_cell(_object_value(row, "strategy_version_display", "버전 확인 필요"))
            status = _safe_display_cell(_object_value(row, "status", "UNAVAILABLE"))
            score = _object_value(row, "score", None)
            score_text = "평가 불가" if score is None else f"{_safe_display_cell(score)}점"
            cutoff = _safe_provider_timestamp(
                _object_value(row, "cutoff_at", ""),
                missing="확인 불가",
            )
            created = _safe_provider_timestamp(
                _object_value(row, "created_at", ""),
                missing="확인 불가",
            )
            lines.append(f"[{status}] {instrument} · {score_text} · 전략 {version}")
            lines.append(f"  자료 기준 {cutoff} · 평가 저장 {created}")
            evidence = _object_value(row, "combined_evidence_constraints", ())
            if evidence:
                lines.append("  근거·반대 근거(저장 당시 통합):")
                lines.extend(f"    - {_safe_display_cell(value, limit=180)}" for value in evidence)
            else:
                lines.append("  근거·반대 근거: 저장된 내용 없음")
            source_ids = _object_value(row, "source_record_ids", ())
            normalized_source_ids = tuple(int(value) for value in source_ids)
            lines.append(
                "  출처 레코드 ID: "
                + (
                    ", ".join(str(value) for value in normalized_source_ids)
                    if normalized_source_ids
                    else "없음"
                )
            )
            timings = _object_value(row, "source_timings", ())
            displayed_timing_ids: set[int] = set()
            if timings:
                for timing in timings:
                    record_id = int(_object_value(timing, "record_id", 0))
                    displayed_timing_ids.add(record_id)
                    effective = _safe_provider_timestamp(
                        _object_value(timing, "effective_at", ""),
                        missing="확인 불가",
                    )
                    collected = _safe_provider_timestamp(
                        _object_value(timing, "collected_at", ""),
                        missing="확인 불가",
                    )
                    lines.append(
                        f"    #{record_id} 자료 기준 {effective} · 수집 {collected}"
                    )
            for missing_record_id in normalized_source_ids:
                if missing_record_id not in displayed_timing_ids:
                    lines.append(
                        f"    #{missing_record_id} 자료 기준 확인 불가 · 수집 확인 불가"
                    )
            lines.append("")
        self._replace_readonly_text(self.strategy_assessment_text, "\n".join(lines).rstrip())

    def _settings_window_available(self) -> bool:
        if self._settings_window is None:
            return False
        try:
            return bool(self._settings_window.winfo_exists())
        except tk.TclError:
            return False

    def refresh_provider_settings(self) -> None:
        if self._busy or not self._settings_window_available():
            return
        selected_provider = self.settings_provider_var.get()
        self.settings_status_var.set("로컬 상태를 확인하는 중입니다…")
        self._set_busy(True, status="공급자 설정 상태를 확인하는 중입니다…")

        def job() -> tuple[
            object,
            object,
            tuple[str, ...],
            object | None,
            object,
        ]:
            service = self._get_automation_service()
            store = self._get_credential_store()
            rows = service.provider_statuses()  # type: ignore[attr-defined]
            credential_status = store.status()  # type: ignore[attr-defined]
            configured: tuple[str, ...] = ()
            if bool(_object_value(credential_status, "available", False)):
                configured = tuple(
                    sorted(store.configured_fields(selected_provider))  # type: ignore[attr-defined]
                )
            scheduler_status: object | None = None
            try:
                from .scheduler import scheduler_status as read_scheduler_status

                scheduler_status = read_scheduler_status()
            except Exception:
                scheduler_status = None
            recent_jobs: object = None
            try:
                history_reader = getattr(service, "recent_scheduled_jobs", None)
                if callable(history_reader):
                    recent_jobs = history_reader(limit=4)
                else:
                    recent_jobs = ()
            except Exception:
                recent_jobs = None
            return rows, credential_status, configured, scheduler_status, recent_jobs

        def refreshed(
            payload: tuple[
                object,
                object,
                tuple[str, ...],
                object | None,
                object,
            ]
        ) -> None:
            if not self._settings_window_available():
                return
            rows, credential_status, configured, scheduler_status, recent_jobs = payload
            for item in self.provider_status_tree.get_children():
                self.provider_status_tree.delete(item)
            state_labels = {
                "AVAILABLE": "즉시 사용 가능",
                "READY": "연결 준비",
                "OK": "최근 수집 정상",
                "PARTIAL": "일부 수집",
                "ERROR": "최근 수집 실패",
                "NEEDS_CONFIGURATION": "사용자 설정 필요",
                "RIGHTS_NOT_VERIFIED": "이용 권한 확인 필요",
                "NOT_IMPLEMENTED": "미구현",
                "NO_DATA": "수집 자료 없음",
                "DISABLED": "사용자 비활성",
                "STALE": "오늘 미갱신",
            }
            for row in rows:  # type: ignore[union-attr]
                code = str(_object_value(row, "code", ""))
                fallback_name = _object_value(row, "name", "공급자")
                name = PROVIDER_LABELS.get(code, _safe_display_cell(fallback_name))
                state = state_labels.get(
                    _enum_text(_object_value(row, "state", "")),
                    "확인 필요",
                )
                effective = _object_value(row, "last_effective", None)
                collected = _object_value(row, "last_collected", None)
                attempted = _object_value(row, "last_attempted", None)
                self.provider_status_tree.insert(
                    "",
                    "end",
                    values=(
                        name,
                        state,
                        _safe_provider_timestamp(effective),
                        _safe_provider_timestamp(collected),
                        _safe_provider_timestamp(attempted),
                    ),
                )

            secure_available = bool(_object_value(credential_status, "available", False))
            if secure_available:
                backend = _safe_display_cell(_object_value(credential_status, "backend", "OS"))
                field_names = ", ".join(FIELD_LABELS.get(item, item) for item in configured)
                configured_text = field_names or "선택 공급자에 등록된 항목 없음"
                self.secure_store_status_var.set(
                    f"OS 보안 저장소 사용 가능({backend}) · {configured_text}"
                )
            else:
                self.secure_store_status_var.set(
                    "OS 보안 저장소 사용 불가 · 평문 파일로 대체하지 않음"
                )

            if scheduler_status is None:
                schedule_text = "예약 상태 확인 불가"
                badge = "예약 확인 불가"
            else:
                installed = bool(_object_value(scheduler_status, "installed", False))
                enabled = _object_value(scheduler_status, "enabled", None)
                active = _object_value(scheduler_status, "active", None)
                if installed and enabled is True and active is True:
                    schedule_text = (
                        "예약 등록·활성화: 15:00 수집, 16:00 보고서 "
                        "(Asia/Seoul; PC 종료 중 보장 없음)"
                    )
                    badge = "예약 활성"
                elif installed and enabled is True and active is False:
                    schedule_text = (
                        "예약은 등록·활성화 설정됐지만 현재 타이머가 대기 중이 아님 · 확인 필요"
                    )
                    badge = "예약 확인 필요"
                elif installed and enabled is None:
                    schedule_text = _safe_display_cell(
                        _object_value(scheduler_status, "detail", "예약 등록 확인")
                    )
                    badge = "예약 등록 확인"
                elif installed:
                    schedule_text = "예약 파일은 있으나 활성 상태 확인 필요"
                    badge = "예약 확인 필요"
                elif enabled is None:
                    schedule_text = _safe_display_cell(
                        _object_value(scheduler_status, "detail", "예약 상태 확인 불가")
                    )
                    badge = "예약 확인 필요"
                else:
                    schedule_text = "예약 미등록 · 수동 갱신은 사용 가능"
                    badge = "예약 미등록"
            self.scheduler_status_var.set(schedule_text)
            self.scheduled_job_history_var.set(
                _scheduled_job_history_text(recent_jobs)
            )
            self.scheduler_badge_var.set(badge)
            self.settings_status_var.set("공급자 상태를 새로고쳤습니다. 네트워크 호출은 하지 않았습니다.")
            self.status_var.set("공급자와 예약 상태를 확인했습니다.")

        self._run_async(
            "provider_status",
            job,
            refreshed,
            lambda _: self.settings_status_var.set(
                "공급자 상태를 확인하지 못했습니다. 비밀값과 오류 원문은 표시하지 않습니다."
            )
            if self._settings_window_available()
            else None,
        )

    def save_selected_secret(self) -> None:
        if self._busy or not self._settings_window_available():
            return
        provider = self.settings_provider_var.get()
        field = self.settings_field_var.get()
        secret = self.secret_entry.get()
        # Tk 입력 위젯에 인증정보를 남겨두지 않는다.
        self.secret_entry.delete(0, "end")
        if not secret:
            self.settings_status_var.set("인증정보를 로컬 입력칸에 입력해 주세요.")
            return
        self.settings_status_var.set("OS 보안 저장소에 등록하는 중입니다…")
        self._set_busy(True, status="인증정보를 안전하게 등록하는 중입니다…")

        def saved(_: object) -> None:
            if self._settings_window_available():
                self.settings_status_var.set("인증정보를 OS 보안 저장소에 등록/교체했습니다.")
            self.status_var.set("공급자 인증정보를 등록/교체했습니다.")
            self._update_button_states()

        self._run_async(
            "credential_save",
            lambda: self._get_credential_store().set_secret(provider, field, secret),  # type: ignore[attr-defined]
            saved,
            lambda _: self.settings_status_var.set(
                "OS 보안 저장소에 등록하지 못했습니다. 평문 저장으로 대체하지 않습니다."
            )
            if self._settings_window_available()
            else None,
        )

    def delete_selected_secret(self) -> None:
        if self._busy or not self._settings_window_available():
            return
        provider = self.settings_provider_var.get()
        field = self.settings_field_var.get()
        if not messagebox.askyesno(
            "인증정보 삭제",
            "선택한 공급자 인증정보를 OS 보안 저장소에서 삭제할까요?",
            parent=self._settings_window,
        ):
            return
        self.secret_entry.delete(0, "end")
        self.settings_status_var.set("OS 보안 저장소에서 삭제하는 중입니다…")
        self._set_busy(True, status="인증정보를 삭제하는 중입니다…")

        def deleted(_: object) -> None:
            if self._settings_window_available():
                self.settings_status_var.set("선택한 인증정보를 삭제했습니다.")
            self.status_var.set("공급자 인증정보를 삭제했습니다.")
            self._update_button_states()

        self._run_async(
            "credential_delete",
            lambda: self._get_credential_store().delete_secret(provider, field),  # type: ignore[attr-defined]
            deleted,
            lambda _: self.settings_status_var.set(
                "OS 보안 저장소에서 삭제하지 못했습니다. 상태를 다시 확인해 주세요."
            )
            if self._settings_window_available()
            else None,
        )

    def save_provider_targets(self) -> None:
        if self._busy or not self._settings_window_available():
            return
        provider = self.target_provider_var.get()
        enabled = self.provider_enabled_var.get()
        kis_environment = self.kis_environment_var.get()
        raw = self.target_entry.get()
        self.target_entry.delete(0, "end")
        targets = tuple(item.strip() for item in raw.split(",") if item.strip())
        self.settings_status_var.set("수집 대상을 저장하는 중입니다…")
        self._set_busy(True, status="수집 대상 설정을 저장하는 중입니다…")

        def saved(_: object) -> None:
            if self._settings_window_available():
                self.settings_status_var.set(
                    f"수집 대상 {len(targets)}개를 저장했습니다. 서로 다른 자료 영역은 합산하지 않습니다."
                )
            self.status_var.set("공식자료 수집 대상을 저장했습니다.")
            self._update_button_states()

        def save_configuration() -> None:
            service = self._get_automation_service()
            service.set_targets(provider, targets)  # type: ignore[attr-defined]
            if provider == "kis":
                service.set_kis_environment(kis_environment)  # type: ignore[attr-defined]
            service.set_provider_enabled(provider, enabled)  # type: ignore[attr-defined]

        self._run_async(
            "target_save",
            save_configuration,
            saved,
            lambda _: self.settings_status_var.set(
                "수집 대상을 저장하지 못했습니다. 지원되는 공급자와 식별자를 확인해 주세요."
            )
            if self._settings_window_available()
            else None,
        )

    def save_strategy_mix(self) -> None:
        if self._busy or not (
            self._settings_window_available() or self._strategy_window_available()
        ):
            return
        version = self.strategy_version_var.get().strip()
        state = validate_strategy_form(
            {
                strategy: self.strategy_weight_vars[strategy].get()
                for strategy in STRATEGY_ORDER
            }
        )
        try:
            mix = state.to_strategy_mix(version)
        except DomainValidationError:
            message = "전략을 저장할 수 없습니다. 버전 이름과 합계 100%를 확인해 주세요."
            self.status_var.set(message)
            if self._settings_window_available():
                self.settings_status_var.set(message)
            self._refresh_strategy_form()
            return
        weights = dict(mix.weights)
        saved_draft = dict(state.weights)
        if self._settings_window_available():
            self.settings_status_var.set("새 전략 버전을 검증·저장하는 중입니다…")
        self._set_busy(True, status="후보 평가 전략을 저장하는 중입니다…")

        def saved(created: object) -> None:
            if created is False:
                message = "동일한 전략 버전이 이미 있습니다. 새 버전 이름으로 저장해 주세요."
                self.status_var.set(message)
                if self._settings_window_available():
                    self.settings_status_var.set(message)
                self._refresh_strategy_form()
                return
            self._strategy_saved_weights = saved_draft
            self._strategy_draft_initialized = True
            # 버전명은 요약에 마스킹된 값으로만 남기고 입력칸에서는 제거한다.
            self.strategy_version_var.set("")
            if self._settings_window_available():
                self.settings_status_var.set(
                    "후보 평가 전략 버전을 저장했습니다. 기존 자산배분 정책은 변경하지 않았습니다."
                )
            self.status_var.set("후보 평가 전략을 저장했습니다. 주문 기능에는 연결되지 않습니다.")
            self._update_button_states()
            if self._strategy_window_available():
                self.refresh_strategy_workspace()

        def save_failed(message: str) -> None:
            if self._settings_window_available():
                self.settings_status_var.set(
                    message
                    if message == STRATEGY_VERSION_CONFLICT_MESSAGE
                    else "전략을 저장하지 못했습니다. 버전 이름과 합계 100%를 확인해 주세요."
                )
            else:
                self._refresh_strategy_form()

        self._run_async(
            "strategy_save",
            lambda: self._get_automation_service().set_strategy_mix(version, weights),  # type: ignore[attr-defined]
            saved,
            save_failed,
        )

    def _replace_readonly_text(self, widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _show_report(self, report: str) -> None:
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", "end")
        for line in render_report_for_display(report):
            self.report_text.insert("end", f"{line.text}\n", line.tag)
        self.report_text.configure(state="disabled")
        self.report_text.yview_moveto(0.0)

    def _clear_loaded_state(self, *, message: str) -> None:
        self._loaded = None
        self._report = None
        self._report_source_path = None
        self.title_var.set("자료 없음")
        self.realm_var.set("미선택")
        self.as_of_var.set("미입력")
        self.investment_var.set("미입력(0원으로 간주하지 않음)")
        self.declared_var.set("미입력")
        self.reconciliation_var.set("계산 전")
        self.freshness_var.set("자동 최신성 검증 없음")
        self.sample_banner.grid_remove()
        self._replace_readonly_text(self.warning_text, "- 자료를 선택하면 주요 경고를 표시합니다.")
        self._replace_readonly_text(self.report_text, "보고서를 생성하면 이 영역에 7개 절이 표시됩니다.")
        self.status_var.set(message)
        self._update_button_states()

    def _apply_dashboard(self, loaded: LoadedGuiData) -> None:
        dashboard = loaded.dashboard
        self.title_var.set(dashboard.title)
        self.realm_var.set(f"{dashboard.realm_code} · {dashboard.realm_label}")
        self.as_of_var.set(dashboard.as_of_text)
        self.investment_var.set(dashboard.investment_assets_text)
        self.declared_var.set(dashboard.declared_assets_text)
        self.reconciliation_var.set(dashboard.reconciliation_text)
        self.freshness_var.set(dashboard.freshness_text)
        if dashboard.is_sample:
            self.sample_banner.grid()
        else:
            self.sample_banner.grid_remove()
        warnings = "\n".join(f"• {warning}" for warning in dashboard.warnings)
        self._replace_readonly_text(self.warning_text, warnings)

    def _prepare_automation_dashboard(self) -> None:
        """외부 시장자료를 수동 원장이나 실제 잔고와 합산하지 않는다."""

        self._loaded = None
        self._report = None
        self._report_source_path = None
        self.title_var.set("정기 시장자료 보고서")
        self.realm_var.set("공식 공급자 자료 · 사용자 원장과 미합산")
        self.as_of_var.set("공급자별 기준 시각은 보고서 본문 참조")
        self.investment_var.set("보유자산 미입력(0원으로 간주하지 않음)")
        self.declared_var.set("미입력")
        self.reconciliation_var.set("사용자 원장 미선택")
        self.freshness_var.set("수집 성공·오래됨·누락 상태는 보고서 본문 참조")
        self.sample_banner.grid_remove()
        self._replace_readonly_text(
            self.warning_text,
            (
                "• 시장 후보 분석은 매수 지시가 아니며 수익을 보장하지 않습니다.\n"
                "• 보유자산·예산이 없으면 수량을 임의로 생성하지 않습니다.\n"
                "• 자료 기준 시각과 미완료 공급자를 보고서에서 확인하세요."
            ),
        )

    def _update_button_states(self) -> None:
        common_state = "disabled" if self._busy else "normal"
        self.sample_button.configure(state=common_state)
        self.open_button.configure(state=common_state)
        self.market_collect_button.configure(state=common_state)
        self.automated_report_button.configure(state=common_state)
        self.latest_report_button.configure(state=common_state)
        self.strategy_settings_button.configure(state=common_state)
        self.provider_settings_button.configure(state=common_state)
        self.generate_button.configure(
            state="normal" if not self._busy and self._loaded is not None else "disabled"
        )
        self.save_button.configure(
            state="normal" if not self._busy and self._report is not None else "disabled"
        )
        for name in (
            "save_secret_button",
            "delete_secret_button",
            "save_targets_button",
            "strategy_restore_button",
            "provider_strategy_button",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                try:
                    widget.configure(state=common_state)
                except tk.TclError:
                    pass
        self._refresh_strategy_form()

    def _set_busy(self, busy: bool, *, status: str | None = None) -> None:
        self._busy = busy
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()
        if status is not None:
            self.status_var.set(status)
        self._update_button_states()

    def _run_async(
        self,
        operation: str,
        job: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._operation_token += 1
        token = self._operation_token
        results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                results.put((True, job()))
            except Exception as exc:  # UI에는 예외 원문을 전달하지 않습니다.
                results.put((False, exc))

        threading.Thread(target=worker, name=f"asset-gui-{operation}", daemon=True).start()

        def poll() -> None:
            if self._closed or token != self._operation_token:
                return
            try:
                success, payload = results.get_nowait()
            except queue.Empty:
                self.root.after(25, poll)
                return
            self._set_busy(False)
            if success:
                try:
                    on_success(payload)
                except Exception as callback_error:
                    message = user_facing_error_message(
                        callback_error,
                        operation=operation,
                    )
                    self.status_var.set(message)
                    if on_error is not None:
                        on_error(message)
                    self._error_handler(message)
            else:
                message = user_facing_error_message(payload, operation=operation)
                self.status_var.set(message)
                if on_error is not None:
                    on_error(message)
                self._error_handler(message)

        self.root.after(25, poll)

    def load_sample(self) -> None:
        self.load_path(self.sample_path)

    def choose_input_file(self) -> None:
        if self._busy:
            return
        selected = self._open_dialog()
        if not selected:
            self.status_var.set("자료 열기를 취소했습니다. 기존 결과는 유지됩니다.")
            return
        self.load_path(Path(selected))

    def load_path(self, path: Path) -> None:
        if self._busy:
            return
        self._clear_loaded_state(message="새 자료를 확인하는 중입니다…")
        self._set_busy(True, status="새 자료를 확인하는 중입니다…")

        def loaded_successfully(loaded: LoadedGuiData) -> None:
            self._loaded = loaded
            self._report = None
            self._report_source_path = None
            self._protected_input_paths.add(loaded.source_path)
            self._apply_dashboard(loaded)
            self._replace_readonly_text(
                self.report_text,
                "자료 검증이 끝났습니다. ‘보고서 생성’을 눌러 7개 절 보고서를 확인하세요.",
            )
            self.status_var.set(
                "수동 JSON을 안전하게 불러왔습니다. 공식 시장자료와는 별도 영역입니다."
            )
            self._update_button_states()

        self._run_async("load", lambda: load_gui_data(path), loaded_successfully)

    def generate_report(self) -> None:
        if self._busy or self._loaded is None:
            return
        loaded = self._loaded
        self._report = None
        self._replace_readonly_text(self.report_text, "보고서를 계산하고 있습니다…")
        self._set_busy(True, status="검증된 입력으로 보고서를 생성하는 중입니다…")

        def generated(report: str) -> None:
            self._report = report
            self._report_source_path = loaded.source_path
            self._show_report(report)
            self.status_var.set("보고서를 생성했습니다. 주문은 생성되거나 실행되지 않았습니다.")
            self._update_button_states()

        self._run_async("generate", lambda: build_gui_report(loaded), generated)

    def collect_market_data(self) -> None:
        """사용자 클릭으로만 공식 공급자 수집을 시작한다."""

        if self._busy:
            return
        self._set_busy(
            True,
            status=(
                "연결·이용 권한이 확인된 공식 공급자에서 "
                "읽기 전용 자료를 갱신하는 중입니다…"
            ),
        )

        def collected(summary: object) -> None:
            state = _enum_text(_object_value(summary, "status", ""))
            if state == "OK":
                message = "공식 시장자료 갱신을 완료했습니다. 주문은 생성되지 않았습니다."
            elif state == "PARTIAL":
                message = (
                    "시장자료를 일부만 갱신했습니다. 실패 공급자의 기존 정상 "
                    "자료는 보존되며 보고서에 미완료로 표시됩니다."
                )
            else:
                message = (
                    "시장자료 갱신을 완료하지 못했습니다. 기존 정상 자료는 "
                    "보존되며 공급자 설정·상태를 확인해 주세요."
                )
            self.status_var.set(message)
            self._update_button_states()
            if self._settings_window_available():
                self.refresh_provider_settings()

        self._run_async(
            "market_collect",
            lambda: self._get_automation_service().collect(),  # type: ignore[attr-defined]
            collected,
            lambda message: self.status_var.set(
                "시장자료 갱신을 완료하지 못했습니다. "
                "기존 정상 자료는 보존됩니다."
            ),
        )

    def _begin_automated_report(self, job: Callable[[], object], *, latest: bool) -> None:
        if self._busy:
            return
        # 실패할 경우 직전 수동/자동 보고서가 새 결과처럼 남지 않도록
        # 출력 상태를 먼저 전환한다.
        self._prepare_automation_dashboard()
        self._replace_readonly_text(
            self.report_text,
            "저장된 최신 보고서를 확인하는 중입니다…"
            if latest
            else "검증된 시장자료로 7개 절 보고서를 생성하는 중입니다…",
        )
        self._set_busy(
            True,
            status="자동 보고서를 준비하는 중입니다…",
        )

        def generated(value: object) -> None:
            if value is None:
                self._report = None
                self._report_source_path = None
                self._replace_readonly_text(
                    self.report_text,
                    "저장된 자동 보고서가 없습니다. 먼저 '자동 보고서'를 실행하세요.",
                )
                self.status_var.set("저장된 자동 보고서가 없습니다.")
                self._update_button_states()
                return
            if not isinstance(value, str) or not value.strip():
                self._report = None
                self._report_source_path = None
                self._replace_readonly_text(
                    self.report_text,
                    "자동 보고서를 표시할 수 없습니다. 공급자 상태를 확인해 주세요.",
                )
                message = "자동 보고서를 표시할 수 없습니다."
                self.status_var.set(message)
                self._error_handler(message)
                self._update_button_states()
                return
            self._report = value
            self._report_source_path = None
            self._show_report(value)
            self.status_var.set(
                "자동 시장자료 보고서를 표시했습니다. 제안은 주문이 아닙니다."
            )
            self._update_button_states()

        def failed(_: str) -> None:
            self._report = None
            self._report_source_path = None
            self._replace_readonly_text(
                self.report_text,
                "자동 보고서를 생성하지 못했습니다. 직전 결과는 표시하지 않습니다.",
            )
            self.status_var.set(
                "자동 보고서를 생성하지 못했습니다. 공급자 상태를 확인해 주세요."
            )
            self._update_button_states()

        self._run_async("automated_report", job, generated, failed)

    def generate_automated_report(self) -> None:
        self._begin_automated_report(
            lambda: self._get_automation_service().generate_report(),  # type: ignore[attr-defined]
            latest=False,
        )

    def show_latest_automated_report(self) -> None:
        self._begin_automated_report(
            lambda: self._get_automation_service().latest_report(),  # type: ignore[attr-defined]
            latest=True,
        )

    def _poll_scheduled_report(self) -> None:
        """열린 앱에서 예약 프로세스가 만든 새 보고서를 비동기로 감지한다.

        수동 원장을 보고 있는 동안에는 화면을 덮지 않고 알림만 표시한다.
        PC나 앱이 꺼져 있던 동안 화면 표시를 보장하지 않는다.
        """

        if self._closed:
            return
        if self._scheduled_report_polling:
            self.root.after(30_000, self._poll_scheduled_report)
            return
        self._scheduled_report_polling = True
        result_queue: queue.Queue[object | None] = queue.Queue(maxsize=1)

        def worker() -> None:
            report: object | None = None
            try:
                report = self._get_automation_service().latest_report()  # type: ignore[attr-defined]
            except Exception:
                report = None
            result_queue.put(report)

        threading.Thread(
            target=worker,
            name="asset-gui-scheduled-report-poll",
            daemon=True,
        ).start()

        def poll_result() -> None:
            if self._closed:
                return
            try:
                report = result_queue.get_nowait()
            except queue.Empty:
                self.root.after(25, poll_result)
                return
            self._scheduled_report_polling = False
            if isinstance(report, str) and report.strip():
                digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
                if digest != self._last_scheduled_report_digest:
                    self._last_scheduled_report_digest = digest
                    if not self._busy and self._loaded is None:
                        self._prepare_automation_dashboard()
                        self._report = report
                        self._show_report(report)
                        self.status_var.set(
                            "새 정기 보고서를 앱에 표시했습니다. 주문 기능은 없습니다."
                        )
                        self._update_button_states()
                    else:
                        self.status_var.set(
                            "새 정기 보고서가 있습니다. ‘최근 자동보고서’에서 확인할 수 있습니다."
                        )
            self.root.after(30_000, self._poll_scheduled_report)

        self.root.after(25, poll_result)

    def save_report_dialog(self) -> None:
        if self._busy or self._report is None:
            return
        selected = self._save_dialog()
        if not selected:
            self.status_var.set("보고서 저장을 취소했습니다. 현재 보고서는 유지됩니다.")
            return
        self.save_to_path(Path(selected))

    def save_to_path(self, output_path: Path) -> None:
        if self._busy or self._report is None:
            return
        report = self._report
        source_path = self._report_source_path
        protected_paths = set(self._protected_input_paths)
        if self._automation_service is not None:
            store = getattr(self._automation_service, "store", None)
            database_path = getattr(store, "path", None)
            if isinstance(database_path, Path):
                protected_paths.add(database_path)
        protected_input_paths = tuple(protected_paths)
        self._set_busy(True, status="보고서를 저장하는 중입니다…")

        def saved(_: None) -> None:
            self.status_var.set("보고서를 UTF-8 Markdown으로 저장했습니다.")
            self._update_button_states()

        def write_safely() -> None:
            for protected_path in protected_input_paths:
                if paths_refer_to_same_file(protected_path, output_path):
                    raise DomainValidationError(
                        "입력 파일과 출력 파일은 같은 파일일 수 없습니다."
                    )
            if source_path is not None:
                save_gui_report(report, output_path, source_path=source_path)
            else:
                write_report_text(report, output_path)

        self._run_async("save", write_safely, saved)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._operation_token += 1
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def run() -> int:
    try:
        root = tk.Tk()
    except tk.TclError:
        sys.stderr.write(
            "GUI를 시작할 수 없습니다. 데스크톱 디스플레이와 Tk 설치를 확인해 주세요.\n"
        )
        return 2
    AssetAssistantApp(root)
    root.mainloop()
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
