"""위성 위험 예산의 확정 경계만 계산합니다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .domain import krw_amount


@dataclass(frozen=True)
class SatelliteLossAssessment:
    annual_risk_budget_krw: Decimal
    pause_threshold_krw: Decimal
    year_lock_threshold_krw: Decimal
    measured_loss_krw: Decimal | None
    state: str


def assess_satellite_loss(
    *, expected_annual_new_investment_krw: object, measured_loss_krw: object | None
) -> SatelliteLossAssessment:
    """제공된 측정값에 B_y 및 정확한 50%/100% 경계만 적용합니다."""

    annual = krw_amount(
        expected_annual_new_investment_krw,
        field_name="expected_annual_new_investment_krw",
    )
    budget = annual * Decimal("0.10")
    pause = budget * Decimal("0.50")
    if measured_loss_krw is None:
        return SatelliteLossAssessment(budget, pause, budget, None, "CALCULATION_UNAVAILABLE")
    loss = krw_amount(measured_loss_krw, field_name="measured_loss_krw")
    if budget == 0:
        state = "BUDGET_ZERO_BLOCKED"
    elif loss >= budget:
        state = "LOCKED_UNTIL_YEAR_END"
    elif loss >= pause:
        state = "PAUSED_REQUIRES_REVIEW"
    else:
        state = "BELOW_ALERT_THRESHOLD"
    return SatelliteLossAssessment(budget, pause, budget, loss, state)

