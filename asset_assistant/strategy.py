"""재현 가능한 전략 혼합·후보 지표 계산.

점수 계산과 정책 검사는 일반 코드로만 수행한다. 입력 자료가 부족하면 0점으로
대체하지 않고 평가 불가로 반환하며, 매수 수량이나 미래 수익률을 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from typing import Mapping, Sequence

from .domain import DomainValidationError


HUNDRED = Decimal("100")
_MAX_EXACT_SUM_PRECISION = 10_000
_MAX_WEIGHT_DIGITS = 128
_MAX_WEIGHT_ABS_EXPONENT = 100


class StrategyKind(str, Enum):
    BROAD_MARKET = "BROAD_MARKET"
    QUALITY = "QUALITY"
    VALUE = "VALUE"
    DIVIDEND_GROWTH = "DIVIDEND_GROWTH"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    MOMENTUM = "MOMENTUM"
    SATELLITE_THEME = "SATELLITE_THEME"
    BOND_CASH = "BOND_CASH"


class AssessmentStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    UNAVAILABLE = "UNAVAILABLE"
    EXCLUDED = "EXCLUDED"


def _decimal(value: Decimal | str | int, *, field_name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise DomainValidationError(f"{field_name}: float/bool 값은 허용되지 않습니다.")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise DomainValidationError(f"{field_name}: 정확한 십진수가 필요합니다.") from None
    if not result.is_finite():
        raise DomainValidationError(f"{field_name}: 유한한 값이어야 합니다.")
    return result


def strategy_weight_decimal(value: Decimal | str | int) -> Decimal:
    """Parse one strategy weight without float coercion or unbounded rendering."""

    if isinstance(value, str) and len(value) > 256:
        raise DomainValidationError("전략 비중 입력이 너무 깁니다.")
    result = _decimal(value, field_name="strategy.weight")
    decimal_tuple = result.as_tuple()
    exponent = int(decimal_tuple.exponent)
    if (
        len(decimal_tuple.digits) > _MAX_WEIGHT_DIGITS
        or abs(exponent) > _MAX_WEIGHT_ABS_EXPONENT
    ):
        raise DomainValidationError("전략 비중의 소수 자릿수가 너무 깁니다.")
    if result < 0 or result > HUNDRED:
        raise DomainValidationError("전략 비중은 0~100% 범위여야 합니다.")
    return result


def exact_decimal_sum(values: Sequence[Decimal]) -> Decimal:
    """Return an exact, context-independent sum for bounded strategy weights.

    ``Decimal`` addition normally uses the process-wide decimal context.  Its
    default precision can therefore round a value just below or above 100 to
    exactly 100.  Strategy weights are user-controlled, so derive sufficient
    precision from their finite decimal tuples before adding them.  Extremely
    deep exponents are rejected instead of allocating an unbounded context.
    """

    items = tuple(values)
    nonzero = tuple(value for value in items if value)
    if not nonzero:
        return Decimal("0")

    effective_exponents: list[int] = []
    for value in nonzero:
        decimal_tuple = value.as_tuple()
        digits = decimal_tuple.digits
        trailing_zeroes = 0
        for digit in reversed(digits):
            if digit != 0:
                break
            trailing_zeroes += 1
        effective_exponents.append(int(decimal_tuple.exponent) + trailing_zeroes)

    lowest_exponent = min(effective_exponents)
    highest_adjusted = max(value.adjusted() for value in nonzero)
    required_precision = (
        highest_adjusted
        - lowest_exponent
        + 1
        + len(str(len(nonzero)))
        + 1
    )
    if required_precision > _MAX_EXACT_SUM_PRECISION:
        raise DomainValidationError("전략 비중의 소수 자릿수가 너무 깁니다.")

    with localcontext() as context:
        context.prec = max(28, required_precision)
        return sum(items, Decimal("0"))


@dataclass(frozen=True)
class StrategyMix:
    version: str
    weights: Mapping[StrategyKind, Decimal]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.version, str)
            or not self.version.strip()
            or len(self.version) > 100
            or any(ord(character) < 32 or ord(character) == 127 for character in self.version)
        ):
            raise DomainValidationError("전략 버전 이름 형식을 확인할 수 없습니다.")
        normalized: dict[StrategyKind, Decimal] = {}
        for strategy, value in self.weights.items():
            if not isinstance(strategy, StrategyKind):
                raise DomainValidationError("지원하지 않는 전략 종류입니다.")
            weight = strategy_weight_decimal(value)
            if weight:
                normalized[strategy] = weight
        if exact_decimal_sum(tuple(normalized.values())) != HUNDRED:
            raise DomainValidationError("전략 혼합 비중의 합은 정확히 100%여야 합니다.")
        object.__setattr__(self, "weights", normalized)


@dataclass(frozen=True)
class CandidateInput:
    instrument_id: str
    prices: tuple[Decimal, ...] = ()
    volumes: tuple[Decimal, ...] = ()
    trading_values: tuple[Decimal, ...] = ()
    broad_market_coverage: Decimal | None = None
    quality_score: Decimal | None = None
    value_score_within_sector: Decimal | None = None
    dividend_growth_score: Decimal | None = None
    theme_score: Decimal | None = None
    bond_cash_score: Decimal | None = None
    disclosure_or_news_risk: bool | None = None
    prohibited_product: bool = False
    product_policy_verified: bool = False
    source_record_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class MarketMetrics:
    latest_price: Decimal | None
    latest_volume: Decimal | None
    latest_trading_value: Decimal | None
    relative_volume_20d: Decimal | None
    momentum_6m: Decimal | None
    annualized_volatility: Decimal | None


@dataclass(frozen=True)
class StrategyComponent:
    strategy: StrategyKind
    status: AssessmentStatus
    score: Decimal | None
    reason: str


@dataclass(frozen=True)
class CandidateAssessment:
    instrument_id: str
    status: AssessmentStatus
    total_score: Decimal | None
    components: tuple[StrategyComponent, ...]
    metrics: MarketMetrics
    positive_reasons: tuple[str, ...]
    counter_reasons: tuple[str, ...]
    source_record_ids: tuple[int, ...]


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def calculate_market_metrics(candidate: CandidateInput) -> MarketMetrics:
    prices = tuple(_decimal(value, field_name="price") for value in candidate.prices)
    volumes = tuple(_decimal(value, field_name="volume") for value in candidate.volumes)
    trading_values = tuple(
        _decimal(value, field_name="trading_value") for value in candidate.trading_values
    )
    if any(value <= 0 for value in prices):
        raise DomainValidationError("가격 시계열에는 0 이하 값이 있을 수 없습니다.")
    if any(value < 0 for value in (*volumes, *trading_values)):
        raise DomainValidationError("거래량·거래대금에는 음수가 있을 수 없습니다.")

    relative_volume = None
    if len(volumes) >= 21:
        baseline = _mean(volumes[-21:-1])
        if baseline > 0:
            relative_volume = volumes[-1] / baseline

    momentum = None
    if len(prices) >= 126:
        momentum = prices[-1] / prices[-126] - Decimal("1")

    volatility = None
    if len(prices) >= 21:
        returns = tuple(prices[index] / prices[index - 1] - Decimal("1") for index in range(1, len(prices)))
        window = returns[-20:]
        average = _mean(window)
        variance = sum((value - average) ** 2 for value in window) / Decimal(len(window) - 1)
        with localcontext() as context:
            context.prec = 28
            volatility = variance.sqrt() * Decimal("252").sqrt()

    return MarketMetrics(
        latest_price=prices[-1] if prices else None,
        latest_volume=volumes[-1] if volumes else None,
        latest_trading_value=trading_values[-1] if trading_values else None,
        relative_volume_20d=relative_volume,
        momentum_6m=momentum,
        annualized_volatility=volatility,
    )


def _bounded_score(value: Decimal | None, label: str) -> StrategyComponent | None:
    if value is None:
        return None
    score = _decimal(value, field_name=label)
    if score < 0 or score > HUNDRED:
        raise DomainValidationError(f"{label}: 점수는 0~100 범위여야 합니다.")
    return StrategyComponent(StrategyKind.QUALITY, AssessmentStatus.ELIGIBLE, score, "검증된 입력 점수 사용")


def _component(strategy: StrategyKind, candidate: CandidateInput, metrics: MarketMetrics) -> StrategyComponent:
    raw: Decimal | None
    reason: str
    if strategy is StrategyKind.BROAD_MARKET:
        raw, reason = candidate.broad_market_coverage, "광범위 지수 편입 범위"
    elif strategy is StrategyKind.QUALITY:
        raw, reason = candidate.quality_score, "재무 품질 지표"
    elif strategy is StrategyKind.VALUE:
        raw, reason = candidate.value_score_within_sector, "업종 내 가치평가"
    elif strategy is StrategyKind.DIVIDEND_GROWTH:
        raw, reason = candidate.dividend_growth_score, "배당 성장 이력"
    elif strategy is StrategyKind.SATELLITE_THEME:
        raw, reason = candidate.theme_score, "테마·섹터 근거"
    elif strategy is StrategyKind.BOND_CASH:
        raw, reason = candidate.bond_cash_score, "채권·현금성 적합성"
    elif strategy is StrategyKind.MOMENTUM:
        if metrics.momentum_6m is None:
            raw, reason = None, "최소 126개 시점 가격 필요"
        else:
            raw = max(Decimal("0"), min(HUNDRED, Decimal("50") + metrics.momentum_6m * Decimal("100")))
            reason = "6개월 가격 추세"
    elif strategy is StrategyKind.LOW_VOLATILITY:
        if metrics.annualized_volatility is None:
            raw, reason = None, "최소 21개 시점 가격 필요"
        else:
            raw = max(Decimal("0"), min(HUNDRED, HUNDRED - metrics.annualized_volatility * Decimal("200")))
            reason = "20일 수익률 연환산 변동성"
    else:  # pragma: no cover - Enum exhaustiveness
        raise DomainValidationError("지원하지 않는 전략입니다.")

    if raw is None:
        return StrategyComponent(strategy, AssessmentStatus.UNAVAILABLE, None, f"자료 부족: {reason}")
    score = _decimal(raw, field_name=reason)
    if score < 0 or score > HUNDRED:
        raise DomainValidationError(f"{reason}: 점수는 0~100 범위여야 합니다.")
    return StrategyComponent(strategy, AssessmentStatus.ELIGIBLE, score, reason)


def evaluate_candidate(candidate: CandidateInput, mix: StrategyMix) -> CandidateAssessment:
    if not candidate.instrument_id.strip():
        raise DomainValidationError("후보 종목 식별자는 비어 있을 수 없습니다.")
    metrics = calculate_market_metrics(candidate)
    if candidate.prohibited_product:
        return CandidateAssessment(
            candidate.instrument_id,
            AssessmentStatus.EXCLUDED,
            None,
            (),
            metrics,
            (),
            ("PROJECT_RULES 금지 상품에 해당하여 제외",),
            candidate.source_record_ids,
        )

    components = tuple(_component(strategy, candidate, metrics) for strategy in mix.weights)
    missing = tuple(component for component in components if component.status is AssessmentStatus.UNAVAILABLE)
    positive = tuple(
        f"{component.strategy.value}: {component.reason} {component.score}%"
        for component in components
        if component.score is not None and component.score >= Decimal("60")
    )
    counter: list[str] = [component.reason for component in missing]
    if candidate.disclosure_or_news_risk is True:
        counter.append("공시·뉴스 위험 신호가 있어 사용자 검토 필요")
    elif candidate.disclosure_or_news_risk is None:
        counter.append("공시·뉴스 위험 자료 미연결 또는 미확인")
    if not candidate.product_policy_verified:
        counter.append("상품유형·금지상품 여부가 검증되지 않아 후보 적격 판정 불가")
    if missing or not candidate.product_policy_verified:
        return CandidateAssessment(
            candidate.instrument_id,
            AssessmentStatus.UNAVAILABLE,
            None,
            components,
            metrics,
            positive,
            tuple(counter),
            candidate.source_record_ids,
        )

    total = sum(
        component.score * mix.weights[component.strategy] / HUNDRED
        for component in components
        if component.score is not None
    )
    return CandidateAssessment(
        candidate.instrument_id,
        AssessmentStatus.ELIGIBLE,
        total,
        components,
        metrics,
        positive,
        tuple(counter),
        candidate.source_record_ids,
    )


@dataclass(frozen=True)
class ExposureCheck:
    instrument_weights: Mapping[str, Decimal]
    violations: tuple[str, ...]


def check_combined_exposures(
    strategy_allocations: Mapping[StrategyKind, Mapping[str, Decimal]],
    *,
    etf_instruments: frozenset[str],
    satellite_instruments: frozenset[str],
    approved_broad_index_etfs: frozenset[str] = frozenset(),
) -> ExposureCheck:
    """전략 중복과 5%/40%·명시 승인 50%/위성 10% 한도를 검사한다."""

    if not approved_broad_index_etfs.issubset(etf_instruments):
        raise DomainValidationError(
            "50% 승인 예외 종목은 ETF로 확인된 종목에만 지정할 수 있습니다."
        )

    combined: dict[str, Decimal] = {}
    for allocations in strategy_allocations.values():
        for instrument, raw_weight in allocations.items():
            weight = _decimal(raw_weight, field_name=f"exposure.{instrument}")
            if weight < 0:
                raise DomainValidationError("후보 비중은 음수일 수 없습니다.")
            combined[instrument] = combined.get(instrument, Decimal("0")) + weight
    violations: list[str] = []
    for instrument, weight in sorted(combined.items()):
        cap = (
            Decimal("50")
            if instrument in approved_broad_index_etfs
            else Decimal("40")
            if instrument in etf_instruments
            else Decimal("5")
        )
        if weight > cap:
            violations.append(f"{instrument}: 전략 합산 비중 {weight}%가 기본 한도 {cap}% 초과")
    satellite = sum(
        (weight for instrument, weight in combined.items() if instrument in satellite_instruments),
        Decimal("0"),
    )
    if satellite > Decimal("10"):
        violations.append("위성 합산 비중 10% 초과 · 신규 위성 매수 중지")
    return ExposureCheck(combined, tuple(violations))
