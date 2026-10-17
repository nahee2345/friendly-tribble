"""Tk 위젯과 분리된 OFFLINE GUI용 로드·요약·보고서 서비스."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from .allocation import SatelliteCapStatus, review_allocation
from .calculations import display_won
from .domain import (
    DataRealm,
    DomainValidationError,
    Purpose,
    StrategyVersionConflictError,
    ValueStatus,
    mask_account_number_like_text,
)
from .file_io import write_report_text
from .reporting import REALM_LABELS, build_markdown_report
from .scenario import OfflineScenario, load_scenario


@dataclass(frozen=True)
class DashboardData:
    title: str
    realm_code: str
    realm_label: str
    as_of_text: str
    investment_assets_text: str
    declared_assets_text: str
    reconciliation_text: str
    freshness_text: str
    warnings: tuple[str, ...]
    is_sample: bool


@dataclass(frozen=True)
class LoadedGuiData:
    source_path: Path
    scenario: OfflineScenario
    dashboard: DashboardData


@dataclass(frozen=True)
class DisplayReportLine:
    """Tk 평문 표시에 사용할 본문과 스타일."""

    text: str
    tag: str | None = None


_TABLE_SEPARATOR_CELL = re.compile(r":?-{3,}:?\Z")
# Keep this display-only, single-pass decoder aligned with the characters
# escaped by ``automation._escape_markdown_label``.  It intentionally does
# not feed decoded text through itself again.
_MARKDOWN_ESCAPABLE = frozenset("\\`[]()!|*_")
STRATEGY_VERSION_CONFLICT_MESSAGE = (
    "같은 이름의 다른 전략 버전이 이미 있습니다. 새 버전 이름을 입력해 주세요."
)


def _won(value) -> str:
    return f"{display_won(value):,.0f}원"


def _plain_user_text(value: str) -> str:
    masked = mask_account_number_like_text(value)
    return " ".join(masked.replace("\r", " ").replace("\n", " ").split())


def _investment_text(scenario: OfflineScenario) -> str:
    positions = scenario.ledger.positions_for_purpose(Purpose.INVESTMENT)
    investment = scenario.ledger.investment_value()
    if investment.complete:
        return _won(investment.amount_krw)
    if not positions:
        return "미입력(0원으로 간주하지 않음)"
    return f"알려진 부분합 {_won(investment.amount_krw)} (불완전)"


def build_dashboard_data(scenario: OfflineScenario) -> DashboardData:
    """Build a UI-neutral dashboard summary from the shared domain engine."""
    reconciliation = scenario.ledger.reconcile(scenario.declared_initial_assets)
    positions = scenario.ledger.positions
    has_stale = any(position.value_status is ValueStatus.STALE for position in positions)
    has_unknown = any(position.value_status is not ValueStatus.KNOWN for position in positions)

    if has_stale:
        freshness = "미갱신(STALE) 자료 포함 · 입력 기준시각 확인 필요"
    elif has_unknown:
        freshness = "미입력·미확인 자료 포함 · 자동 최신성 검증 없음"
    else:
        freshness = "입력 스냅샷 기준 · 자동 최신성 검증 없음"

    if reconciliation.complete and reconciliation.matches:
        reconciliation_text = "선언값과 상세 원장 일치"
    elif reconciliation.complete:
        reconciliation_text = (
            f"선언값과 상세 원장 불일치 · 차이 {_won(reconciliation.difference_krw)}"
        )
    else:
        reconciliation_text = "상세 원장 불완전 · 선언값과 최종 대조 불가"

    warnings: list[str] = []
    if scenario.realm is DataRealm.SAMPLE:
        warnings.append("가상 샘플입니다. 실제 잔고가 아닙니다.")
    elif scenario.realm is DataRealm.MANUAL_PAPER_SNAPSHOT:
        warnings.append("수동 자체모의 자료이며 KIS 모의운영 검증이 아닙니다.")
    else:
        warnings.append("수동 실제자료이며 외부 계좌와 자동 대조되지 않았습니다.")
    warnings.append(
        "원장 스냅샷은 별도 시장자료와 자동 합산하지 않습니다. 입력 기준시각 이후 잔고 변화는 반영되지 않습니다."
    )
    if has_stale:
        warnings.append("미갱신(STALE) 평가값이 있어 정확한 현재 비중을 단정할 수 없습니다.")
    elif has_unknown:
        warnings.append("미입력·미확인 평가값을 0원으로 대체하지 않았습니다.")
    if not reconciliation.matches:
        warnings.append(reconciliation_text)

    try:
        allocation = review_allocation(scenario.ledger, year=scenario.allocation_year)
    except DomainValidationError:
        warnings.append("자산배분 점검 일부는 계산 불가/확인 필요입니다.")
    else:
        if allocation.satellite_cap.status is SatelliteCapStatus.EXCEEDED:
            warnings.append("위성 상한 초과 · 신규 위성 매수 중지")
        elif allocation.satellite_cap.status is SatelliteCapStatus.AT_CAP:
            warnings.append("위성 10% 상한 도달")
        elif allocation.satellite_cap.status is SatelliteCapStatus.UNAVAILABLE:
            warnings.append("위성 상한 판정 불가 · 입력 자료 확인 필요")

    return DashboardData(
        title=_plain_user_text(scenario.scenario_label),
        realm_code=scenario.realm.value,
        realm_label=REALM_LABELS[scenario.realm],
        as_of_text=scenario.as_of.isoformat(),
        investment_assets_text=_investment_text(scenario),
        declared_assets_text=_won(scenario.declared_initial_assets.amount_krw),
        reconciliation_text=reconciliation_text,
        freshness_text=freshness,
        warnings=tuple(warnings),
        is_sample=scenario.realm is DataRealm.SAMPLE,
    )


def load_gui_data(path: Path) -> LoadedGuiData:
    scenario = load_scenario(path)
    return LoadedGuiData(
        source_path=path,
        scenario=scenario,
        dashboard=build_dashboard_data(scenario),
    )


def build_gui_report(loaded: LoadedGuiData) -> str:
    return build_markdown_report(loaded.scenario)


def _unescape_report_inline(value: str) -> str:
    """마스킹 후 보고서가 추가한 표시용 escape만 한 번 해제합니다."""

    decoded = html.unescape(value)
    decoded = decoded.replace("**", "")
    result: list[str] = []
    index = 0
    while index < len(decoded):
        character = decoded[index]
        if (
            character == "\\"
            and index + 1 < len(decoded)
            and decoded[index + 1] in _MARKDOWN_ESCAPABLE
        ):
            result.append(decoded[index + 1])
            index += 2
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _split_table_row(value: str) -> list[str] | None:
    stripped = value.strip()
    if len(stripped) < 2 or not stripped.startswith("|") or not stripped.endswith("|"):
        return None

    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in stripped[1:-1]:
        if character == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(character)
        if character == "\\":
            escaped = not escaped
        else:
            escaped = False
    cells.append("".join(current).strip())
    return cells


def render_report_for_display(report: str) -> tuple[DisplayReportLine, ...]:
    """Markdown 저장 원문은 보존하고 Tk에만 읽기 쉬운 평문을 제공합니다.

    ``build_markdown_report``가 사용자 문구를 먼저 마스킹한 뒤 생성한
    완성본에만 적용합니다. HTML entity는 재귀적으로 해제하지 않아
    사용자가 entity 문자열 자체를 입력한 경우를 다시 해석하지 않습니다.
    """

    rendered: list[DisplayReportLine] = []
    for raw_line in report.splitlines():
        line = raw_line
        tag: str | None = None
        heading = re.match(r"^(#{1,4})[ \t]+(.*)\Z", line)
        if heading is not None:
            level = len(heading.group(1))
            tag = "h1" if level == 1 else "h2" if level == 2 else "h3"
            line = heading.group(2)
        elif line.startswith(">"):
            tag = "quote"
            line = line[1:].lstrip()

        cells = _split_table_row(line)
        if cells is not None:
            if cells and all(_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in cells):
                continue
            line = "  │  ".join(_unescape_report_inline(cell) for cell in cells)
        else:
            line = _unescape_report_inline(line)
            if line.startswith("- "):
                line = f"• {line[2:]}"
        rendered.append(DisplayReportLine(text=line, tag=tag))
    return tuple(rendered)


def save_gui_report(report: str, output_path: Path, *, source_path: Path) -> None:
    write_report_text(report, output_path, source_path=source_path)


def user_facing_error_message(error: BaseException, *, operation: str) -> str:
    """예외 원문·경로·입력값을 노출하지 않는 GUI 오류 문구를 만듭니다."""

    if operation == "strategy_save" and isinstance(
        error, StrategyVersionConflictError
    ):
        return STRATEGY_VERSION_CONFLICT_MESSAGE
    if operation == "load":
        return "자료를 열 수 없습니다. JSON 형식과 필수 항목을 확인해 주세요."
    if operation == "generate":
        return "보고서를 생성할 수 없습니다. 입력 자료 상태를 확인해 주세요."
    if operation == "save":
        return "보고서를 저장할 수 없습니다. 다른 위치를 선택해 주세요."
    return "작업을 완료할 수 없습니다. 입력 자료를 확인해 주세요."
