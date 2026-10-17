"""User target-strategy allocation, separate from candidate score weights."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from asset_assistant.domain import DomainValidationError
from asset_assistant.strategy import StrategyKind


class TargetStrategyKind(str, Enum):
    LONG_TERM_INDEX = "LONG_TERM_INDEX"
    VALUE = "VALUE"
    QUALITY_GROWTH = "QUALITY_GROWTH"
    GROWTH = "GROWTH"
    HIGH_DIVIDEND = "HIGH_DIVIDEND"
    DIVIDEND_GROWTH = "DIVIDEND_GROWTH"
    MOMENTUM = "MOMENTUM"
    ASSET_ALLOCATION = "ASSET_ALLOCATION"


class MappingStatus(str, Enum):
    DIRECT = "DIRECT"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class StrategyCatalogEntry:
    kind: TargetStrategyKind
    korean_name: str
    mapping_status: MappingStatus
    candidate_strategy: StrategyKind | None
    status_note: str


_CATALOG = (
    StrategyCatalogEntry(TargetStrategyKind.LONG_TERM_INDEX, "장기 지수투자", MappingStatus.DIRECT, StrategyKind.BROAD_MARKET, "광범위 시장 후보 평가와 직접 대응"),
    StrategyCatalogEntry(TargetStrategyKind.VALUE, "가치투자", MappingStatus.DIRECT, StrategyKind.VALUE, "업종 내 가치 후보 평가와 직접 대응"),
    StrategyCatalogEntry(TargetStrategyKind.QUALITY_GROWTH, "우량 성장투자", MappingStatus.PARTIAL, StrategyKind.QUALITY, "현재 후보 평가는 퀄리티 요소만 지원"),
    StrategyCatalogEntry(TargetStrategyKind.GROWTH, "성장주 투자", MappingStatus.UNAVAILABLE, None, "독립 성장 후보 평가 모델 미구현"),
    StrategyCatalogEntry(TargetStrategyKind.HIGH_DIVIDEND, "고배당 투자", MappingStatus.UNAVAILABLE, None, "고배당 독립 후보 평가 모델 미구현"),
    StrategyCatalogEntry(TargetStrategyKind.DIVIDEND_GROWTH, "배당성장 투자", MappingStatus.DIRECT, StrategyKind.DIVIDEND_GROWTH, "배당 성장 후보 평가와 직접 대응"),
    StrategyCatalogEntry(TargetStrategyKind.MOMENTUM, "모멘텀 투자", MappingStatus.DIRECT, StrategyKind.MOMENTUM, "모멘텀 후보 평가와 직접 대응"),
    StrategyCatalogEntry(TargetStrategyKind.ASSET_ALLOCATION, "자산배분 투자", MappingStatus.PARTIAL, StrategyKind.BOND_CASH, "후보 평가의 채권·현금성 요소만 부분 대응"),
)


def strategy_catalog() -> tuple[StrategyCatalogEntry, ...]:
    return _CATALOG


def _weight(value: object) -> Decimal:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise DomainValidationError("전략 목표 비중은 정확한 십진수 문자열이어야 합니다.")
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        raise DomainValidationError("전략 목표 비중은 정확한 십진수 문자열이어야 합니다.") from None
    if not number.is_finite() or number < 0 or number > 100:
        raise DomainValidationError("전략 목표 비중은 0~100% 범위여야 합니다.")
    parts = number.as_tuple()
    if len(parts.digits) > 128 or abs(int(parts.exponent)) > 100:
        raise DomainValidationError("전략 목표 비중의 자릿수가 너무 깁니다.")
    return number


def _exact_sum(values: tuple[Decimal, ...]) -> Decimal:
    lowest = min((int(value.as_tuple().exponent) for value in values if value), default=0)
    highest = max((value.adjusted() for value in values if value), default=0)
    precision = highest - lowest + 8
    if precision > 10_000:
        raise DomainValidationError("전략 목표 비중의 자릿수가 너무 깁니다.")
    with localcontext() as context:
        context.prec = max(28, precision)
        return sum(values, Decimal(0))


@dataclass(frozen=True, init=False)
class TargetStrategyMix:
    """Exact eight-way target allocation; not a candidate scoring mix."""

    weights: Mapping[TargetStrategyKind, Decimal]

    def __init__(self, weights: Mapping[TargetStrategyKind | str, str]) -> None:
        normalized = self._validated_weights(weights)
        object.__setattr__(self, "weights", MappingProxyType(normalized))

    @classmethod
    def from_strings(cls, weights: Mapping[TargetStrategyKind | str, str]) -> "TargetStrategyMix":
        return cls(weights)

    @staticmethod
    def _validated_weights(
        weights: Mapping[TargetStrategyKind | str, str]
    ) -> dict[TargetStrategyKind, Decimal]:
        if not isinstance(weights, Mapping):
            raise DomainValidationError("8개 전략 목표 비중을 모두 입력해야 합니다.")
        expected = set(TargetStrategyKind)
        normalized: dict[TargetStrategyKind, Decimal] = {}
        for raw_kind, raw_weight in weights.items():
            try:
                kind = raw_kind if isinstance(raw_kind, TargetStrategyKind) else TargetStrategyKind(raw_kind)
            except (ValueError, TypeError):
                raise DomainValidationError("지원하지 않는 전략 목표 종류입니다.") from None
            if kind in normalized:
                raise DomainValidationError("전략 목표 종류가 중복되었습니다.")
            normalized[kind] = _weight(raw_weight)
        if set(normalized) != expected:
            raise DomainValidationError("8개 전략 목표 비중을 모두 입력해야 합니다.")
        if _exact_sum(tuple(normalized.values())) != Decimal("100"):
            raise DomainValidationError("전략 목표 비중의 합은 정확히 100%여야 합니다.")
        return normalized

    def as_strings(self) -> dict[str, str]:
        return {kind.value: format(self.weights[kind], "f") for kind in TargetStrategyKind}

    def target_amounts(self, actual_investment_total: str | None) -> dict[TargetStrategyKind, Decimal] | None:
        """Return targets only when an explicit actual-investment total exists."""

        if actual_investment_total is None:
            return None
        if (
            not isinstance(actual_investment_total, str)
            or not actual_investment_total.strip()
            or len(actual_investment_total) > 256
        ):
            raise DomainValidationError("실제 투자자산 합계는 정확한 십진수 문자열이어야 합니다.")
        try:
            total = Decimal(actual_investment_total.strip())
        except InvalidOperation:
            raise DomainValidationError("실제 투자자산 합계를 확인할 수 없습니다.") from None
        total_parts = total.as_tuple()
        if (
            not total.is_finite()
            or total < 0
            or len(total_parts.digits) > 128
            or abs(int(total_parts.exponent)) > 100
        ):
            raise DomainValidationError("실제 투자자산 합계를 확인할 수 없습니다.")
        return {kind: total * self.weights[kind] / Decimal("100") for kind in TargetStrategyKind}
