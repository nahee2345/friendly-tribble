"""정책 문서의 7개 절 순서를 지키는 OFFLINE Markdown 보고서."""

from __future__ import annotations

import html
from decimal import Decimal, ROUND_CEILING

from .allocation import SatelliteCapStatus, review_allocation
from .calculations import (
    allocate_monthly_surplus,
    display_won,
    planned_monthly_living_expense,
    recalculate_next_year_goal,
)
from .domain import (
    AssetClass,
    DataRealm,
    DomainValidationError,
    Purpose,
    ValueStatus,
    mask_account_number_like_text,
)
from .satellite import assess_satellite_loss
from .scenario import OfflineScenario


ASSET_LABELS = {
    AssetClass.CORE_EQUITY: "코어 주식",
    AssetClass.CORE_BOND: "코어 채권",
    AssetClass.CORE_CASH: "코어 현금성",
    AssetClass.SATELLITE: "위성",
    AssetClass.UNCLASSIFIED: "미분류",
}

REALM_LABELS = {
    DataRealm.SAMPLE: "가상 샘플",
    DataRealm.MANUAL_PAPER_SNAPSHOT: "수동 자체모의 스냅샷",
    DataRealm.MANUAL_ACTUAL: "수동 실제자료",
}


def _won(value: Decimal) -> str:
    return f"{display_won(value):,.0f}원"


def _trigger_won(value: Decimal) -> str:
    """정수 원 손실이 경계를 실제로 충족하는 최소 금액을 표시합니다."""

    return f"{value.quantize(Decimal('1'), rounding=ROUND_CEILING):,.0f}원"


def _exact_won(value: Decimal) -> str:
    """계산 경계를 소수 원까지 정확히 표시하되 불필요한 0은 제거합니다."""

    whole, separator, fraction = format(value, "f").partition(".")
    fraction = fraction.rstrip("0")
    decimal_part = f".{fraction}" if separator and fraction else ""
    return f"{int(whole):,}{decimal_part}원"


def _percent(ratio: Decimal) -> str:
    return f"{ratio * Decimal('100'):.2f}%"


def _precise_percent(ratio: Decimal) -> str:
    whole, fraction = f"{ratio * Decimal('100'):.8f}".split(".")
    fraction = fraction.rstrip("0")
    fraction = fraction.ljust(2, "0")
    return f"{whole}.{fraction}%"


def _inline(value: str) -> str:
    masked = mask_account_number_like_text(value)
    safe = html.escape(
        " ".join(masked.replace("\r", " ").replace("\n", " ").split()),
        quote=True,
    )
    for marker in ("\\", chr(96), "[", "]", "(", ")", "!"):
        safe = safe.replace(marker, "\\" + marker)
    return safe


def _cell(value: str) -> str:
    return _inline(value).replace("|", "\\|")


def _aggregate_text(amount: Decimal, *, complete: bool, record_count: int) -> str:
    if complete:
        return _won(amount)
    if record_count == 0:
        return "미입력(0원으로 간주하지 않음)"
    return f"알려진 부분합 {_won(amount)}(불완전)"


def build_markdown_report(scenario: OfflineScenario) -> str:
    ledger = scenario.ledger
    investment_positions = ledger.positions_for_purpose(Purpose.INVESTMENT)
    investment = ledger.investment_value()
    reconciliation = ledger.reconcile(scenario.declared_initial_assets)
    realm_label = REALM_LABELS[scenario.realm]
    investment_text = _aggregate_text(
        investment.amount_krw,
        complete=investment.complete,
        record_count=len(investment_positions),
    )

    if scenario.realm is DataRealm.SAMPLE:
        risk_line = "실제 계좌 구성과 2027년 소액 재검증 조건은 미입력·미확정입니다."
    elif scenario.realm is DataRealm.MANUAL_PAPER_SNAPSHOT:
        risk_line = "이 자료는 수동 자체모의 스냅샷이며 KIS 모의운영 검증으로 간주하지 않습니다."
    else:
        risk_line = (
            "수동 실제자료는 외부 계좌와 자동 대조되지 않았습니다."
            if investment.complete
            else "수동 실제자료에 미입력·미확인 평가값이 있어 정확한 총액을 계산할 수 없습니다."
        )

    try:
        allocation = review_allocation(ledger, year=scenario.allocation_year)
        allocation_error = None
    except DomainValidationError as exc:
        allocation = None
        allocation_error = str(exc)

    lines: list[str] = [
        f"# {_inline(scenario.scenario_label)}",
        "",
        f"> 모드: **OFFLINE / {realm_label}** · 기준시각: {scenario.as_of.isoformat()}",
        "> 이 보고서는 투자 수익·원금·목표 달성을 보장하지 않으며 주문을 실행할 수 없습니다.",
        "",
        "## 1) 요약 3줄",
        "",
        f"- 현재 상태: {realm_label} 원장의 투자자산은 {investment_text}입니다.",
        f"- 핵심 위험/미달: {risk_line}",
        "- 필요한 결정: 초기 400만 원의 상세 구성, 소액 재검증의 일정·자금 출처·한도 의미를 실전 전에 확인해야 합니다.",
        "",
        "## 2) 자산 현황표",
        "",
        "| 계좌 별칭 | 구분 | 자산 | 평가액 | 상태 | 기준시각 |",
        "|---|---|---|---:|---|---|",
    ]
    accounts = {account.account_id: account for account in ledger.accounts}
    for position in ledger.positions:
        account = accounts[position.account_id]
        amount = (
            _won(position.market_value_krw)
            if position.value_status is ValueStatus.KNOWN and position.market_value_krw is not None
            else position.value_status.value
        )
        lines.append(
            f"| {_cell(account.alias)} | {account.purpose.value} | {_cell(position.name)} ({ASSET_LABELS[position.asset_class]}) | {amount} | {position.value_status.value} | {position.as_of.isoformat()} |"
        )
    if not ledger.positions:
        lines.append("| 미입력 | 미입력 | 미입력 | 미입력 | MISSING | 미입력 |")

    detail_text = _aggregate_text(
        reconciliation.detailed_krw,
        complete=reconciliation.complete,
        record_count=len(ledger.positions),
    )
    lines.extend(
        [
            "",
            f"- 초기자산 선언값: {_won(reconciliation.declared_krw)} (상세 원장과 **합산하지 않고 대조만 함**)",
            f"- 상세 합계: {detail_text} / 대조: {'일치' if reconciliation.matches else '불일치 또는 미완료'}",
            f"- 보호저축·비상금은 투자자산 집계({investment_text})와 배분 분모에서 제외됩니다.",
            "",
            f"### {scenario.allocation_year}년 자산배분 점검",
            "",
        ]
    )
    if allocation is None:
        lines.append(f"- 계산 불가/확인 필요: {_inline(allocation_error or '미확정 정책')} ")
    else:
        lines.extend(
            [
                "#### 코어 내부 ±5%p 점검(제안)",
                "",
                f"- 판정 규칙 상태: 제안·미확정({allocation.review_rule_status.value})",
                "",
                "| 코어 자산군 | 코어 내부 목표 | 실제 | 차이 | ±5%p 점검(제안) |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for allocation_line in allocation.lines:
            if (
                allocation_line.actual_ratio is None
                or allocation_line.deviation_percentage_points is None
            ):
                actual = "계산 불가"
                deviation = "계산 불가"
                flag = "계산 불가"
            else:
                actual = _percent(allocation_line.actual_ratio)
                deviation = f"{allocation_line.deviation_percentage_points:+.2f}%p"
                flag = "점검" if allocation_line.proposed_review else "범위 내"
            lines.append(
                f"| {ASSET_LABELS[allocation_line.asset_class]} | {_percent(allocation_line.target_ratio)} | {actual} | {deviation} | {flag} |"
            )
        if allocation.unavailable_reasons:
            lines.append(
                "\n- 정확한 배분 표시 불가(코어 내부): "
                + " / ".join(allocation.unavailable_reasons)
            )
        satellite_cap = allocation.satellite_cap
        if satellite_cap.status is SatelliteCapStatus.UNAVAILABLE:
            satellite_text = "판정 불가 — 투자자산 분모가 불완전하거나 계산 불가"
        elif satellite_cap.actual_ratio is None:
            satellite_text = "판정 불가"
        elif satellite_cap.status is SatelliteCapStatus.EXCEEDED:
            satellite_text = (
                f"실제 {_precise_percent(satellite_cap.actual_ratio)} (정확 계산상 상한 초과) / "
                f"최대 {_percent(satellite_cap.cap_ratio)} / "
                "상한 초과·신규 위성 매수 중지 / 조정안만 보고·승인 없는 자동 매도 없음"
            )
        elif satellite_cap.status is SatelliteCapStatus.AT_CAP:
            satellite_text = (
                f"실제 {_precise_percent(satellite_cap.actual_ratio)} (정확 계산상 상한 도달) / "
                f"최대 {_percent(satellite_cap.cap_ratio)} / "
                "상한 도달"
            )
        else:
            satellite_text = (
                f"실제 {_precise_percent(satellite_cap.actual_ratio)} (정확 계산상 상한 미만) / "
                f"최대 {_percent(satellite_cap.cap_ratio)} / "
                "상한 미만·매수 의무 없음"
            )
        lines.append(
            "\n- 위성 상한 점검(전체 투자자산 기준): " + satellite_text
        )
        if satellite_cap.status is SatelliteCapStatus.BELOW_CAP:
            lines.append(
                "- 위성 부족분의 미배정 투자 현금: 원장에 별도 항목이 없어 산정 불가/확인 필요."
            )
        country = allocation.policy.country_targets
        lines.append(
            f"\n- 코어 주식 국가 목표(전체 기준): 미국 {_percent(country['US'])}, 한국 {_percent(country['KR'])}. 전세계 ETF 분류 관계는 확인 필요입니다."
        )

    lines.extend(["", "## 3) 목표 대비", ""])
    if scenario.goal is None:
        lines.append("- 목표 계산 입력: 미입력")
    else:
        goal = recalculate_next_year_goal(
            actual_year_end_assets_krw=scenario.goal.actual_year_end_assets_krw,
            planned_next_year_investment_krw=scenario.goal.planned_next_year_investment_krw,
        )
        lines.extend(
            [
                f"- 입력: 연말 자산 {_won(scenario.goal.actual_year_end_assets_krw)}, 다음 해 예정 투자금 {_won(scenario.goal.planned_next_year_investment_krw)}",
                f"- 재산정 기본 목표(6%/3% 공식): {_won(goal.recalculated_base_krw)}",
                f"- 5% 가정 기준선(보장 하한 아님): {_won(goal.five_percent_assumption_line_krw)}",
                f"- 85% 경고선: {_won(goal.warning_line_krw)}",
            ]
        )
    lines.extend(
        [
            "- 2034년 1억 원 점검일 2034-03-31은 제안값이며, 2034년 연말 1.44억 원 목표와 구분합니다.",
            "- 2043년 목표는 마감일 확정 전 2043-11-30과 2043-12-31을 함께 비교해야 합니다.",
            "",
            "## 4) 배당",
            "",
            "- 실제 세후 수령액: 미입력",
            "- 사용자 공식의 배당 전환 능력: ISA·일반계좌별 평가액 미입력으로 계산 불가",
            "- 조건을 반영한 인출 추정치: 세제·상품·인출 조건 미확인으로 계산 불가",
            "",
            "## 5) 위성",
            "",
        ]
    )
    if scenario.satellite is None:
        lines.append("- 연간 B_y 및 측정 손실: 미입력")
    else:
        satellite = assess_satellite_loss(
            expected_annual_new_investment_krw=scenario.satellite.expected_annual_new_investment_krw,
            measured_loss_krw=scenario.satellite.measured_loss_krw,
        )
        measured = (
            "미입력"
            if satellite.measured_loss_krw is None
            else _won(satellite.measured_loss_krw)
        )
        lines.extend(
            [
                f"- 가상 연간 신규 투자계획 기준 정확 B_y: {_exact_won(satellite.annual_risk_budget_krw)}",
                f"- 50% 중단: 정확 경계 {_exact_won(satellite.pause_threshold_krw)} / 정수 손실 최초 중단액 {_trigger_won(satellite.pause_threshold_krw)}",
                f"- 100% 연말 잠금: 정확 경계 {_exact_won(satellite.year_lock_threshold_krw)} / 정수 손실 최초 잠금액 {_trigger_won(satellite.year_lock_threshold_krw)}",
                f"- 측정 손실: {measured} / 상태: {satellite.state}",
                "- 손실 정의와 50% 상태 재개 조건은 미확정이므로 이 계산은 실전 위성 매수 허가가 아닙니다.",
            ]
        )

    lines.extend(
        [
            "",
            "## 6) 주문안과 이유",
            "",
            "- 주문안: 없음",
            "- 승인 여부: 해당 없음",
            "- 이유: 이 수동 원장 보고서는 계좌 조회·주문 실행 모듈과 연결되지 않습니다.",
            "",
            "## 7) 위반·주의",
            "",
            f"- 데이터 영역: {scenario.realm.value}. 다른 영역과 합산하지 않습니다.",
        ]
    )
    if scenario.realm is DataRealm.SAMPLE:
        lines.append(
            "- 초기 400만 원은 상세 구성이 미입력된 계획 총액입니다. 샘플 상세액은 실제 잔고가 아닙니다."
        )
    elif scenario.realm is DataRealm.MANUAL_PAPER_SNAPSHOT:
        lines.append(
            "- 수동 자체모의 값은 실제 자산과 합산하지 않으며 KIS PAPER 단계 완료 근거가 아닙니다."
        )
    else:
        lines.append(
            "- 수동 실제자료는 입력자가 제공한 값이며 외부 계좌 대조·최신성을 자동 보증하지 않습니다."
        )
    lines.extend(
        [
            "- 2027년 100만 원을 구간 전체 누적 투입원금 상한으로 보는 것은 제안이며 아직 확정되지 않았습니다.",
            "- 원 단위 반올림은 '투자 몫 내림·잔여 보호저축' 제안 기본값이며 정책 확정값이 아닙니다.",
            "- 2062~2064년 정확한 주식·채권·현금성 배분은 확인 필요입니다.",
        ]
    )
    if scenario.cashflow is not None:
        cashflow_year = scenario.cashflow.year
        income = scenario.cashflow.monthly_net_income_krw
        expenses = scenario.cashflow.essential_expenses_krw
        if 2028 <= cashflow_year <= 2064:
            cashflow = allocate_monthly_surplus(
                net_income_krw=income,
                essential_expenses_krw=expenses,
            )
            planned = planned_monthly_living_expense(cashflow_year)
        else:
            cashflow = None
            planned = None
        if cashflow is None:
            difference = income - expenses
            if difference >= 0:
                lines.append(
                    f"- 월 현금흐름({cashflow_year}): 소득 {_won(income)}, 필수생활비 {_won(expenses)}, 잉여금 {_won(difference)}."
                )
            else:
                lines.append(
                    f"- 월 현금흐름({cashflow_year}): 소득 {_won(income)}, 필수생활비 {_won(expenses)}, 생활비 부족 {_won(-difference)}."
                )
            if cashflow_year == 2027:
                lines.append(
                    "- 투자·보호저축 배분: 계산 불가/확인 필요 — 2027년 80:20 적용 여부 미확정."
                )
            else:
                lines.append(
                    "- 투자·보호저축 배분: 계산 불가/확인 필요 — 계획 범위 밖 연도입니다."
                )
            lines.append("- 계획 생활비: 계산 불가/확인 필요 — 해당 연도 계획식이 정의되지 않았습니다.")
        elif cashflow.shortfall_krw > 0:
            lines.append(
                f"- 생활비 부족: {_won(cashflow.shortfall_krw)}. 투자·보호저축 자동 배정은 각각 0원입니다."
            )
        else:
            lines.append(
                f"- 월 현금흐름({scenario.cashflow.year}): 소득 {_won(cashflow.net_income_krw)}, 필수생활비 {_won(cashflow.essential_expenses_krw)}, 투자 {_won(cashflow.investment_krw)}, 보호저축 {_won(cashflow.protected_savings_krw)}."
            )
        if planned is not None:
            lines.append(
                f"- 같은 연도의 문서상 계획 생활비는 {_won(planned)}이며 실제 지출 입력이 우선합니다."
            )
    assumption_label = "샘플 가정" if scenario.realm is DataRealm.SAMPLE else "입력 가정"
    for assumption in scenario.assumptions:
        lines.append(f"- {assumption_label}: {_inline(assumption)}")
    lines.append("")
    return "\n".join(lines)
