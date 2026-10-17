"""네트워크나 UI에 의존하지 않는 핵심 도메인 타입."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


OFFLINE_MODE = "OFFLINE"
KRW_QUANTUM = Decimal("1")
_INTEGER_TEXT = re.compile(r"[-+]?\d+\Z")
_ACCOUNT_NUMBER_LIKE = re.compile(
    r"(?<!\d)(?:\d[-\s._,/*`()\[\]{}!|~\\:'#·・ㅡᅳ‐‑‒–—―−\u00ad\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]*){7,}\d(?!\d)"
)
_ACCOUNT_ALIAS_MASK = "[계좌번호 형태 마스킹]"


class DomainValidationError(ValueError):
    """입력이 도메인 불변조건을 위반했을 때 발생합니다."""


class StrategyVersionConflictError(DomainValidationError):
    """같은 전략 버전 이름에 서로 다른 내용을 저장하려 할 때 발생합니다."""


class UnconfirmedPolicyError(DomainValidationError):
    """사용자 확정이 필요한 정책을 계산에 적용하려 할 때 발생합니다."""


class DataRealm(str, Enum):
    SAMPLE = "SAMPLE"
    MANUAL_PAPER_SNAPSHOT = "MANUAL_PAPER_SNAPSHOT"
    MANUAL_ACTUAL = "MANUAL_ACTUAL"


class Purpose(str, Enum):
    INVESTMENT = "INVESTMENT"
    PROTECTED_SAVINGS = "PROTECTED_SAVINGS"
    EMERGENCY = "EMERGENCY"


class AssetClass(str, Enum):
    CORE_EQUITY = "CORE_EQUITY"
    CORE_BOND = "CORE_BOND"
    CORE_CASH = "CORE_CASH"
    SATELLITE = "SATELLITE"
    UNCLASSIFIED = "UNCLASSIFIED"


class PolicyStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROPOSED_UNCONFIRMED = "PROPOSED_UNCONFIRMED"
    UNKNOWN = "UNKNOWN"


class ValueStatus(str, Enum):
    KNOWN = "KNOWN"
    MISSING = "MISSING"
    UNCONFIRMED = "UNCONFIRMED"
    UNSUPPORTED = "UNSUPPORTED"
    ERROR = "ERROR"
    STALE = "STALE"


def exact_decimal(value: Any, *, field_name: str) -> Decimal:
    """문자열·정수·Decimal만 정확한 Decimal로 바꿉니다."""

    if isinstance(value, bool) or isinstance(value, float):
        raise DomainValidationError(
            f"{field_name}: float/bool 입력은 허용되지 않습니다. 문자열 또는 정수를 사용하세요."
        )
    if not isinstance(value, (str, int, Decimal)):
        raise DomainValidationError(f"{field_name}: 지원하지 않는 숫자 형식입니다.")
    if isinstance(value, str) and not value.strip():
        raise DomainValidationError(f"{field_name}: 빈 문자열은 숫자가 아닙니다.")
    try:
        result = Decimal(str(value).strip()) if isinstance(value, str) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise DomainValidationError(f"{field_name}: 유효한 십진수가 아닙니다.") from exc
    if not result.is_finite():
        raise DomainValidationError(f"{field_name}: 무한대와 NaN은 허용되지 않습니다.")
    return result


def krw_amount(
    value: Any,
    *,
    field_name: str,
    allow_negative: bool = False,
) -> Decimal:
    """원 단위 KRW 금액을 검증하며 암묵적 반올림을 하지 않습니다."""

    if isinstance(value, str) and _INTEGER_TEXT.fullmatch(value.strip()) is None:
        raise DomainValidationError(
            f"{field_name}: KRW 문자열은 소수점·지수표기 없는 원 단위 정수여야 합니다."
        )
    result = exact_decimal(value, field_name=field_name)
    if result != result.to_integral_value():
        raise DomainValidationError(f"{field_name}: KRW 금액은 원 단위 정수여야 합니다.")
    if not allow_negative and result < 0:
        raise DomainValidationError(f"{field_name}: 음수 금액은 허용되지 않습니다.")
    return result


def aware_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name}: 시간대가 포함된 시각이어야 합니다.")
    return value


def nonempty(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name}: 비어 있을 수 없습니다.")
    return value.strip()


def mask_account_number_like_text(value: str) -> str:
    """긴 숫자와 표시용 구분자로 된 계좌번호 형태를 마스킹합니다.

    자유 텍스트에서 날짜·금액과 계좌번호를 확실히 구분할 수 없는
    형식은 노출 방지를 우선합니다. 구조화된 날짜·금액은 별도 포매터가
    출력하므로 이 자유 텍스트 마스킹을 거치지 않습니다.
    """

    normalized: list[str] = []
    source_indexes: list[int] = []
    for source_index, character in enumerate(value):
        shadow = unicodedata.normalize("NFKC", character)
        normalized.extend(shadow)
        source_indexes.extend([source_index] * len(shadow))

    shadow_text = "".join(normalized)
    source_spans: list[tuple[int, int]] = []
    for match in _ACCOUNT_NUMBER_LIKE.finditer(shadow_text):
        start = source_indexes[match.start()]
        end = source_indexes[match.end() - 1] + 1
        if source_spans and start <= source_spans[-1][1]:
            previous_start, previous_end = source_spans[-1]
            source_spans[-1] = (previous_start, max(previous_end, end))
        else:
            source_spans.append((start, end))

    if not source_spans:
        return value

    masked: list[str] = []
    cursor = 0
    for start, end in source_spans:
        masked.append(value[cursor:start])
        masked.append(_ACCOUNT_ALIAS_MASK)
        cursor = end
    masked.append(value[cursor:])
    return "".join(masked)


@dataclass(frozen=True)
class Account:
    account_id: str
    alias: str
    account_type: str
    realm: DataRealm
    purpose: Purpose
    currency: str = "KRW"

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", nonempty(self.account_id, field_name="account_id"))
        alias = nonempty(self.alias, field_name="alias")
        object.__setattr__(self, "alias", mask_account_number_like_text(alias))
        object.__setattr__(
            self, "account_type", nonempty(self.account_type, field_name="account_type")
        )
        if self.currency != "KRW":
            raise DomainValidationError(
                "첫 OFFLINE 버전은 환율 근거 없는 통화 합산을 막기 위해 KRW만 집계합니다."
            )


@dataclass(frozen=True)
class PositionRecord:
    position_id: str
    account_id: str
    name: str
    asset_class: AssetClass
    market_value_krw: Decimal | None
    as_of: datetime
    value_status: ValueStatus = ValueStatus.KNOWN
    source: str = "manual"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "position_id", nonempty(self.position_id, field_name="position_id")
        )
        object.__setattr__(self, "account_id", nonempty(self.account_id, field_name="account_id"))
        object.__setattr__(self, "name", nonempty(self.name, field_name="position name"))
        object.__setattr__(self, "source", nonempty(self.source, field_name="source"))
        aware_datetime(self.as_of, field_name="position as_of")
        if self.value_status is ValueStatus.KNOWN:
            if self.market_value_krw is None:
                raise DomainValidationError("KNOWN 평가값에는 금액이 필요합니다.")
            object.__setattr__(
                self,
                "market_value_krw",
                krw_amount(self.market_value_krw, field_name="market_value_krw"),
            )
        elif self.market_value_krw is not None:
            raise DomainValidationError(
                "미입력/미확인 상태는 0과 구분하기 위해 평가금액을 함께 저장하지 않습니다."
            )


@dataclass(frozen=True)
class DeclaredAssetPlan:
    """상세 원장과 합산하지 않고 대조만 하는 초기자산 선언값."""

    amount_krw: Decimal
    label: str = "초기 총자산 계획값"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "amount_krw",
            krw_amount(self.amount_krw, field_name="declared asset plan"),
        )
        object.__setattr__(self, "label", nonempty(self.label, field_name="plan label"))


@dataclass(frozen=True)
class AggregateValue:
    amount_krw: Decimal
    complete: bool
    missing_position_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReconciliationResult:
    declared_krw: Decimal
    detailed_krw: Decimal
    difference_krw: Decimal
    matches: bool
    complete: bool
