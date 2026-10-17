"""투자전략 화면용 순수 입력 모델과 설명 메타데이터.

이 모듈은 Tk 위젯에 의존하지 않는다. 화면 입력을 정확한 :class:`Decimal`로
검증한 뒤 저장 직전에 도메인 모델인 :class:`StrategyMix`로 다시 검증한다.
전략 비중은 후보 평가용 가중치이며 자산배분이나 매수 금액을 뜻하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from .domain import DomainValidationError
from .strategy import (
    HUNDRED,
    StrategyKind,
    StrategyMix,
    exact_decimal_sum,
    strategy_weight_decimal,
)


STRATEGY_ORDER: tuple[StrategyKind, ...] = tuple(StrategyKind)

STRATEGY_DISPLAY_NAMES: Mapping[StrategyKind, str] = MappingProxyType(
    {
        StrategyKind.BROAD_MARKET: "광범위 지수",
        StrategyKind.QUALITY: "퀄리티",
        StrategyKind.VALUE: "가치",
        StrategyKind.DIVIDEND_GROWTH: "배당성장",
        StrategyKind.LOW_VOLATILITY: "저변동성",
        StrategyKind.MOMENTUM: "추세·모멘텀",
        StrategyKind.SATELLITE_THEME: "위성 테마·섹터",
        StrategyKind.BOND_CASH: "채권·현금성",
    }
)


@dataclass(frozen=True)
class StrategyDetail:
    """화면 상세 패널에 표시할 정책 중립적인 전략 설명."""

    display_name: str
    description: str
    selection_criteria: tuple[str, ...]
    strengths: tuple[str, ...]
    cautions: tuple[str, ...]
    required_inputs: tuple[str, ...]


STRATEGY_DETAILS: Mapping[StrategyKind, StrategyDetail] = MappingProxyType(
    {
        StrategyKind.BROAD_MARKET: StrategyDetail(
            display_name=STRATEGY_DISPLAY_NAMES[StrategyKind.BROAD_MARKET],
            description="광범위한 시장을 대표하는 지수 편입 범위를 후보 평가에 반영합니다.",
            selection_criteria=("지수 추종 범위", "상품유형과 추종 지수 확인"),
            strengths=("특정 기업에 대한 집중을 줄이는 데 도움",),
            cautions=("지수 편입만으로 상품 정책 적합성이 확인되지는 않음",),
            required_inputs=("지수 편입 범위", "상품유형", "금지상품 검증 상태"),
        ),
        StrategyKind.QUALITY: StrategyDetail(
            display_name=STRATEGY_DISPLAY_NAMES[StrategyKind.QUALITY],
            description="기업의 재무 품질 자료를 이용해 후보를 비교합니다.",
            selection_criteria=("수익성", "재무 건전성", "현금흐름 품질"),
            strengths=("재무 기반의 재현 가능한 비교 가능",),
            cautions=("공시 시점과 연결·별도 기준을 구분해야 함",),
            required_inputs=("재무제표", "공시 기준일", "연결·별도 구분"),
        ),
        StrategyKind.VALUE: StrategyDetail(
            display_name=STRATEGY_DISPLAY_NAMES[StrategyKind.VALUE],
            description="동일 업종 안에서 확인 가능한 가치평가 자료를 비교합니다.",
            selection_criteria=("업종 내 상대 가치평가", "재무자료 기준일"),
            strengths=("업종 차이를 분리한 비교에 도움",),
            cautions=("낮은 평가값의 원인과 공시 위험을 함께 확인해야 함",),
            required_inputs=("업종 분류", "가치평가 지표", "공시 위험 자료"),
        ),
        StrategyKind.DIVIDEND_GROWTH: StrategyDetail(
            display_name=STRATEGY_DISPLAY_NAMES[StrategyKind.DIVIDEND_GROWTH],
            description="확인된 배당 이력과 성장의 지속성을 후보 평가에 반영합니다.",
            selection_criteria=("배당 이력", "배당 성장의 지속성", "현금흐름"),
            strengths=("배당 변화의 장기 이력 비교에 도움",),
            cautions=("과거 배당이 미래 배당을 보장하지 않음",),
            required_inputs=("배당 이력", "재무제표", "배당 기준일"),
        ),
        StrategyKind.LOW_VOLATILITY: StrategyDetail(
            display_name=STRATEGY_DISPLAY_NAMES[StrategyKind.LOW_VOLATILITY],
            description="충분한 가격 시계열에서 계산한 변동성을 후보 평가에 사용합니다.",
            selection_criteria=("동일 기준의 가격 시계열", "20일 수익률 변동성"),
            strengths=("가격 변동 위험을 정량적으로 비교 가능",),
            cautions=("짧거나 결측된 시계열에서는 평가할 수 없음",),
            required_inputs=("최소 21개 시점 가격", "수정 여부와 기준 시각"),
        ),
        StrategyKind.MOMENTUM: StrategyDetail(
            display_name=STRATEGY_DISPLAY_NAMES[StrategyKind.MOMENTUM],
            description="충분한 가격 시계열로 계산한 추세를 후보 평가에 사용합니다.",
            selection_criteria=("6개월 가격 추세", "자료 연속성과 기준 시각"),
            strengths=("동일한 산식으로 추세를 반복 계산 가능",),
            cautions=("추세 지속을 보장하지 않으며 결측 자료에서는 평가할 수 없음",),
            required_inputs=("최소 126개 시점 가격", "수정 여부와 기준 시각"),
        ),
        StrategyKind.SATELLITE_THEME: StrategyDetail(
            display_name=STRATEGY_DISPLAY_NAMES[StrategyKind.SATELLITE_THEME],
            description="테마·섹터 근거가 확인된 후보를 위성 평가에 반영합니다.",
            selection_criteria=("테마·섹터 근거", "공시·뉴스 위험", "상품 정책 적합성"),
            strengths=("코어와 분리해 제한된 후보군을 검토 가능",),
            cautions=("전체 투자자산 위성 상한과 종목 한도를 별도로 검사해야 함",),
            required_inputs=("테마 점수 근거", "공시·뉴스 위험", "금지상품 검증 상태"),
        ),
        StrategyKind.BOND_CASH: StrategyDetail(
            display_name=STRATEGY_DISPLAY_NAMES[StrategyKind.BOND_CASH],
            description="채권·현금성 상품의 확인된 적합성 자료를 후보 평가에 반영합니다.",
            selection_criteria=("상품유형", "만기·신용·통화 특성", "정책 적합성"),
            strengths=("주식 후보와 다른 자료 요구사항을 명시적으로 구분",),
            cautions=("상품유형과 위험 자료가 없으면 평가할 수 없음",),
            required_inputs=("상품유형", "만기·신용·통화 자료", "금지상품 검증 상태"),
        ),
    }
)


def _format_percent(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _parse_weight(value: object) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise DomainValidationError("전략 비중은 정확한 십진수 문자로 입력해야 합니다.")
    if isinstance(value, str):
        if not value.strip():
            raise DomainValidationError("전략 비중을 모두 입력해야 합니다.")
        source: object = value.strip()
    elif isinstance(value, (Decimal, int)):
        source = value
    else:
        raise DomainValidationError("전략 비중은 정확한 십진수 문자로 입력해야 합니다.")
    try:
        parsed = strategy_weight_decimal(source)  # type: ignore[arg-type]
    except DomainValidationError:
        raise DomainValidationError("전략 비중은 정확한 십진수 문자로 입력해야 합니다.") from None
    return parsed


@dataclass(frozen=True)
class StrategyFormState:
    """한 시점의 입력 검증 결과.

    ``weights``는 읽기 전용 매핑이다. ``to_strategy_mix``는 저장 직전에
    도메인 검증을 다시 수행하므로 화면의 저장 버튼 상태만 신뢰하지 않는다.
    """

    weights: Mapping[StrategyKind, Decimal]
    total: Decimal | None
    errors: tuple[str, ...]
    guidance: str

    @property
    def can_save(self) -> bool:
        return not self.errors and self.total == HUNDRED

    def to_strategy_mix(self, version: str) -> StrategyMix:
        if not self.can_save:
            raise DomainValidationError("전략 혼합 입력을 확인한 뒤 저장해 주세요.")
        return StrategyMix(version=version, weights=dict(self.weights))


def validate_strategy_form(raw_weights: Mapping[object, object]) -> StrategyFormState:
    """8개 전략 입력을 정확한 십진수로 검증하고 합계 안내를 만든다."""

    expected = set(STRATEGY_ORDER)
    provided = set(raw_weights)
    if provided != expected:
        return StrategyFormState(
            weights=MappingProxyType({}),
            total=None,
            errors=("8개 전략 비중을 빠짐없이 입력해야 합니다.",),
            guidance="입력값을 확인해 주세요.",
        )

    normalized: dict[StrategyKind, Decimal] = {}
    errors: list[str] = []
    for strategy in STRATEGY_ORDER:
        try:
            normalized[strategy] = _parse_weight(raw_weights[strategy])
        except DomainValidationError:
            errors.append(f"{STRATEGY_DISPLAY_NAMES[strategy]} 비중을 확인해 주세요.")

    readonly_weights: Mapping[StrategyKind, Decimal] = MappingProxyType(dict(normalized))
    if errors:
        return StrategyFormState(
            weights=readonly_weights,
            total=None,
            errors=tuple(errors),
            guidance="입력값을 확인해 주세요.",
        )

    try:
        total = exact_decimal_sum(tuple(normalized.values()))
    except DomainValidationError:
        return StrategyFormState(
            weights=readonly_weights,
            total=None,
            errors=("전략 비중의 소수 자릿수를 확인해 주세요.",),
            guidance="입력값을 확인해 주세요.",
        )
    if total < HUNDRED:
        difference = HUNDRED - total
        guidance = f"{_format_percent(difference)}%를 추가해야 합니다."
    elif total > HUNDRED:
        difference = total - HUNDRED
        guidance = f"{_format_percent(difference)}%를 줄여야 합니다."
    else:
        guidance = "합계 100%입니다. 저장할 수 있습니다."

    return StrategyFormState(
        weights=readonly_weights,
        total=total,
        errors=(),
        guidance=guidance,
    )


__all__ = [
    "STRATEGY_DETAILS",
    "STRATEGY_DISPLAY_NAMES",
    "STRATEGY_ORDER",
    "StrategyDetail",
    "StrategyFormState",
    "validate_strategy_form",
]
