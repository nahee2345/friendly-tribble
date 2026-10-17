"""확정 자산배분 계산과 미확정 리밸런싱 점검을 분리합니다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .domain import (
    AssetClass,
    DomainValidationError,
    PolicyStatus,
    Purpose,
    UnconfirmedPolicyError,
    ValueStatus,
)
from .ledger import PortfolioLedger


CORE_SHARE = Decimal("0.90")
SATELLITE_SHARE = Decimal("0.10")
US_WITHIN_CORE_EQUITY = Decimal("0.70")
KOREA_WITHIN_CORE_EQUITY = Decimal("0.30")
PROPOSED_REVIEW_THRESHOLD_PP = Decimal("5")


class SatelliteCapStatus(str, Enum):
    """전체 투자자산 대비 위성 10% 상한의 별도 판정 상태."""

    BELOW_CAP = "BELOW_CAP"
    AT_CAP = "AT_CAP"
    EXCEEDED = "EXCEEDED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class AllocationPolicy:
    year_start: int
    year_end: int
    core_equity_within_core: Decimal
    core_bond_within_core: Decimal
    core_cash_within_core: Decimal

    @property
    def core_targets(self) -> dict[AssetClass, Decimal]:
        return {
            AssetClass.CORE_EQUITY: self.core_equity_within_core,
            AssetClass.CORE_BOND: self.core_bond_within_core,
            AssetClass.CORE_CASH: self.core_cash_within_core,
        }

    @property
    def whole_portfolio_targets(self) -> dict[AssetClass, Decimal]:
        return {
            AssetClass.CORE_EQUITY: self.core_equity_within_core * CORE_SHARE,
            AssetClass.CORE_BOND: self.core_bond_within_core * CORE_SHARE,
            AssetClass.CORE_CASH: self.core_cash_within_core * CORE_SHARE,
            AssetClass.SATELLITE: SATELLITE_SHARE,
        }

    @property
    def country_targets(self) -> dict[str, Decimal]:
        whole_equity = self.whole_portfolio_targets[AssetClass.CORE_EQUITY]
        return {
            "US": whole_equity * US_WITHIN_CORE_EQUITY,
            "KR": whole_equity * KOREA_WITHIN_CORE_EQUITY,
        }


@dataclass(frozen=True)
class AllocationLine:
    asset_class: AssetClass
    target_ratio: Decimal
    actual_ratio: Decimal | None
    deviation_percentage_points: Decimal | None
    proposed_review: bool | None


@dataclass(frozen=True)
class SatelliteCapReview:
    cap_ratio: Decimal
    actual_ratio: Decimal | None
    status: SatelliteCapStatus


@dataclass(frozen=True)
class AllocationReview:
    policy: AllocationPolicy
    lines: tuple[AllocationLine, ...]
    total_investment_krw: Decimal
    complete: bool
    unavailable_reasons: tuple[str, ...]
    satellite_cap: SatelliteCapReview
    review_rule_status: PolicyStatus = PolicyStatus.PROPOSED_UNCONFIRMED


def allocation_policy_for_year(year: int) -> AllocationPolicy:
    if isinstance(year, bool) or not isinstance(year, int):
        raise DomainValidationError("year는 정수여야 합니다.")
    if 2028 <= year <= 2040:
        return AllocationPolicy(
            2028, 2040, Decimal("0.70"), Decimal("0.25"), Decimal("0.05")
        )
    if 2041 <= year <= 2052:
        return AllocationPolicy(
            2041, 2052, Decimal("0.60"), Decimal("0.33"), Decimal("0.07")
        )
    if 2053 <= year <= 2061:
        return AllocationPolicy(
            2053, 2061, Decimal("0.50"), Decimal("0.42"), Decimal("0.08")
        )
    if 2062 <= year <= 2064:
        raise UnconfirmedPolicyError(
            "2062~2064의 주식·채권·현금성 정확한 비중은 미확정입니다. 이전 구간을 자동 연장하지 않습니다."
        )
    raise DomainValidationError("자산배분 정책은 2028~2064 계획 구간을 대상으로 합니다.")


def review_allocation(ledger: PortfolioLedger, *, year: int) -> AllocationReview:
    policy = allocation_policy_for_year(year)
    aggregate = ledger.investment_value()
    reasons: list[str] = []
    if not aggregate.complete:
        reasons.append("평가값이 미입력/미확인인 투자 포지션이 있습니다.")

    positions = ledger.positions_for_purpose(Purpose.INVESTMENT)
    if any(position.asset_class is AssetClass.UNCLASSIFIED for position in positions):
        reasons.append("미분류 투자자산이 있어 배분 합계를 정확하다고 표시할 수 없습니다.")
    if any(position.value_status is ValueStatus.STALE for position in positions):
        reasons.append("오래된 평가값이 있습니다.")
    if aggregate.amount_krw == 0:
        reasons.append("투자 평가액이 0원이어서 실제 비율을 계산하지 않습니다.")

    total_ratio_available = not reasons
    if total_ratio_available:
        satellite_total = sum(
            (
                position.market_value_krw or Decimal("0")
                for position in positions
                if position.asset_class is AssetClass.SATELLITE
            ),
            Decimal("0"),
        )
        satellite_ratio = satellite_total / aggregate.amount_krw
        if satellite_ratio > SATELLITE_SHARE:
            satellite_status = SatelliteCapStatus.EXCEEDED
        elif satellite_ratio == SATELLITE_SHARE:
            satellite_status = SatelliteCapStatus.AT_CAP
        else:
            satellite_status = SatelliteCapStatus.BELOW_CAP
    else:
        satellite_ratio = None
        satellite_status = SatelliteCapStatus.UNAVAILABLE

    core_classes = {
        AssetClass.CORE_EQUITY,
        AssetClass.CORE_BOND,
        AssetClass.CORE_CASH,
    }
    core_total = sum(
        (
            position.market_value_krw or Decimal("0")
            for position in positions
            if position.asset_class in core_classes
        ),
        Decimal("0"),
    )
    if total_ratio_available and core_total == 0:
        reasons.append("코어 평가액이 0원이어서 코어 내부 비율을 계산하지 않습니다.")

    complete = total_ratio_available and core_total != 0
    lines: list[AllocationLine] = []
    for asset_class, target in policy.core_targets.items():
        if not complete:
            actual = None
            deviation = None
            proposed_review = None
        else:
            class_total = sum(
                (
                    position.market_value_krw or Decimal("0")
                    for position in positions
                    if position.asset_class is asset_class
                ),
                Decimal("0"),
            )
            actual = class_total / core_total
            deviation = (actual - target) * Decimal("100")
            proposed_review = abs(deviation) >= PROPOSED_REVIEW_THRESHOLD_PP
        lines.append(
            AllocationLine(asset_class, target, actual, deviation, proposed_review)
        )
    return AllocationReview(
        policy=policy,
        lines=tuple(lines),
        total_investment_krw=aggregate.amount_krw,
        complete=complete,
        unavailable_reasons=tuple(reasons),
        satellite_cap=SatelliteCapReview(
            cap_ratio=SATELLITE_SHARE,
            actual_ratio=satellite_ratio,
            status=satellite_status,
        ),
    )
