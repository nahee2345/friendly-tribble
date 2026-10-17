"""외부 I/O가 없는 INTERNAL_PAPER 순수 도메인 엔진.

이 모듈은 실제 계좌, 증권사 모의계좌, ``MANUAL_PAPER_SNAPSHOT``과
독립된 단일 프로세스 메모리 모델이다. 시각·가격·환율·정책·ID는 모두
호출자가 명시하며 네트워크, 시스템 시계, 환경변수, 운영 DB를 사용하지 않는다.

메모리 저장소의 copy-on-write transaction은 한 Python 프로세스 안의 thread
원자성만 제공한다. SQLite/다중 프로세스/브로커 원자성 계약은 후속 범위다.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    localcontext,
)
from enum import Enum
from threading import RLock, local
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, TypeVar

from .domain import (
    DomainValidationError,
    aware_datetime,
    exact_decimal,
    krw_amount,
    nonempty,
)


INITIAL_PAPER_CASH_KRW = Decimal("4000000")
DAILY_GROSS_RATE = Decimal("0.20")
LARGE_TRADE_THRESHOLD_KRW = Decimal("300000")
APPROVAL_LIFETIME = timedelta(hours=24)
STRATEGY_CHANGE_INTERVAL = timedelta(days=14)
_PAPER_DECIMAL_PRECISION = 128


def _paper_decimal_context(*, exact: bool = False) -> Context:
    """반환값을 공유하지 않는 결정적 PAPER 계산 문맥을 만든다."""

    traps = [InvalidOperation, DivisionByZero, Overflow]
    if exact:
        traps.append(Inexact)
    return Context(
        prec=_PAPER_DECIMAL_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=-999_999,
        Emax=999_999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=traps,
    )


class PaperBlockedError(DomainValidationError):
    """정책·자료·상태 gate가 실행을 안전하게 차단했음을 나타낸다."""

    def __init__(self, code: str, message: str = "PAPER 작업이 안전 정책에 의해 차단됐습니다.") -> None:
        self.code = nonempty(code, field_name="block code")
        super().__init__(message)


class PaperScope(str, Enum):
    INTERNAL_PAPER = "INTERNAL_PAPER"


class Currency(str, Enum):
    KRW = "KRW"
    USD = "USD"


class PaperStrategyKind(str, Enum):
    """PROJECT_RULES의 사용자 목표 8전략. 기존 후보 점수 StrategyKind와 별개다."""

    LONG_TERM_INDEX = "LONG_TERM_INDEX"
    VALUE = "VALUE"
    QUALITY_GROWTH = "QUALITY_GROWTH"
    GROWTH_STOCK = "GROWTH_STOCK"
    HIGH_DIVIDEND = "HIGH_DIVIDEND"
    DIVIDEND_GROWTH = "DIVIDEND_GROWTH"
    MOMENTUM = "MOMENTUM"
    ASSET_ALLOCATION = "ASSET_ALLOCATION"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class GateStatus(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class LedgerEventType(str, Enum):
    GENESIS = "GENESIS"
    FX = "FX"
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    FEE = "FEE"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    RESERVE = "RESERVE"
    RELEASE = "RELEASE"
    CORRECTION = "CORRECTION"


class InstrumentType(str, Enum):
    COMMON_STOCK = "COMMON_STOCK"
    ETF = "ETF"
    BROAD_MARKET_ETF = "BROAD_MARKET_ETF"
    PROHIBITED = "PROHIBITED"
    UNCLASSIFIED = "UNCLASSIFIED"


class RiskRole(str, Enum):
    CORE = "CORE"
    SATELLITE = "SATELLITE"
    UNCLASSIFIED_ASSUMED_SATELLITE = "UNCLASSIFIED_ASSUMED_SATELLITE"
    INELIGIBLE = "INELIGIBLE"


class RiskDenominatorBasis(str, Enum):
    CURRENT_PAPER_NAV = "CURRENT_PAPER_NAV"
    SESSION_OPENING_PAPER_NAV = "SESSION_OPENING_PAPER_NAV"


class ReservationState(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    CONSUMED = "CONSUMED"
    UNKNOWN = "UNKNOWN"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"


class CostBasisMethod(str, Enum):
    SPECIFIC_LOT = "SPECIFIC_LOT"
    FIFO = "FIFO"


class ApprovalType(str, Enum):
    CANDIDATE_SET = "CANDIDATE_SET"
    LARGE_TRADE = "LARGE_TRADE"
    NEW_ROOT_PURPOSE = "NEW_ROOT_PURPOSE"


class ApprovalState(str, Enum):
    APPROVED = "APPROVED"
    BOUND = "BOUND"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class CandidateSetState(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    WAITING_SET_APPROVAL = "WAITING_SET_APPROVAL"
    SET_APPROVED = "SET_APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REGENERATION_REQUIRED = "REGENERATION_REQUIRED"
    SUPERSEDED = "SUPERSEDED"


class ProposalState(str, Enum):
    PROPOSED = "PROPOSED"
    ELIGIBILITY_CHECKED = "ELIGIBILITY_CHECKED"
    RESERVED = "RESERVED"
    READY = "READY"
    TEMPORARILY_HELD = "TEMPORARILY_HELD"
    USER_STOPPED = "USER_STOPPED"
    PAPER_EXECUTING = "PAPER_EXECUTING"
    PAPER_RECORDED = "PAPER_RECORDED"
    RECONCILED = "RECONCILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    MISSED_WINDOW = "MISSED_WINDOW"
    DEFINITIVELY_UNEXECUTABLE = "DEFINITIVELY_UNEXECUTABLE"
    UNKNOWN = "UNKNOWN"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"


class RunState(str, Enum):
    DISABLED = "DISABLED"
    READY = "READY"
    RUNNING = "RUNNING"
    TEMPORARILY_HELD = "TEMPORARILY_HELD"
    USER_STOPPED = "USER_STOPPED"
    MISSED_WINDOW = "MISSED_WINDOW"
    COMPLETED = "COMPLETED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILED = "RECONCILED"


class CompletionKind(str, Enum):
    NONE = "NONE"
    FULL = "FULL"
    PARTIAL = "PARTIAL"


class AttemptResult(str, Enum):
    UNKNOWN = "UNKNOWN"
    NO_FILL = "NO_FILL"
    FILL_RECORDED = "FILL_RECORDED"


class CandidateFailureReason(str, Enum):
    PERMANENT_POLICY_INELIGIBLE = "PERMANENT_POLICY_INELIGIBLE"
    CONFIRMED_PERMANENTLY_UNTRADABLE = "CONFIRMED_PERMANENTLY_UNTRADABLE"
    APPROVAL_WAITING = "APPROVAL_WAITING"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    PRICE_CONDITION_NOT_MET = "PRICE_CONDITION_NOT_MET"
    MISSED_WINDOW = "MISSED_WINDOW"
    DATA_TEMPORARILY_UNAVAILABLE = "DATA_TEMPORARILY_UNAVAILABLE"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"
    TEMPORARY_HALT = "TEMPORARY_HALT"


class PriceConditionOperator(str, Enum):
    AT_OR_BELOW = "AT_OR_BELOW"
    AT_OR_ABOVE = "AT_OR_ABOVE"


class PurposeChangeResult(str, Enum):
    DIFFERENT = "DIFFERENT"
    SAME = "SAME"
    UNPROVEN = "UNPROVEN"


class SplitCheckResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise DomainValidationError(f"{field_name}: 시각 형식이 올바르지 않습니다.")
    try:
        normalized = aware_datetime(value, field_name=field_name).astimezone(
            timezone.utc
        )
        if (
            type(normalized) is not datetime
            or normalized.tzinfo is None
            or normalized.utcoffset() is None
        ):
            raise DomainValidationError(
                f"{field_name}: 시각 형식이 올바르지 않습니다."
            )
        return normalized
    except DomainValidationError:
        raise
    except Exception:
        raise DomainValidationError(
            f"{field_name}: 시각 형식이 올바르지 않습니다."
        ) from None


def utc_epoch_microseconds(value: datetime, *, field_name: str = "timestamp") -> int:
    normalized = _utc(value, field_name=field_name)
    try:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = normalized - epoch
        return (
            (delta.days * 86_400 + delta.seconds) * 1_000_000
            + delta.microseconds
        )
    except Exception:
        raise DomainValidationError(
            f"{field_name}: 시각 형식이 올바르지 않습니다."
        ) from None


def _positive_decimal(value: Any, *, field_name: str, allow_zero: bool = False) -> Decimal:
    result = exact_decimal(value, field_name=field_name)
    if len(result.as_tuple().digits) > _PAPER_DECIMAL_PRECISION:
        raise DomainValidationError(
            f"{field_name}: PAPER 계산 정밀도 범위를 초과했습니다."
        )
    context = _paper_decimal_context()
    if result and not context.Emin <= result.adjusted() <= context.Emax:
        raise DomainValidationError(
            f"{field_name}: PAPER 계산 지수 범위를 초과했습니다."
        )
    if result < 0 or (result == 0 and not allow_zero):
        qualifier = "0 이상" if allow_zero else "0보다 큰"
        raise DomainValidationError(f"{field_name}: {qualifier} 값이어야 합니다.")
    return result


def _positive_quantity(value: Any, *, field_name: str = "quantity", allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(f"{field_name}: 수량은 정수여야 합니다.")
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "0 이상" if allow_zero else "1 이상"
        raise DomainValidationError(f"{field_name}: {qualifier}이어야 합니다.")
    return value


def _require_bool(value: Any, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise DomainValidationError(f"{field_name}: true/false 값이어야 합니다.")
    return value


def _require_optional_bool(value: Any, *, field_name: str) -> bool | None:
    if value is not None:
        return _require_bool(value, field_name=field_name)
    return None


def _require_enum(value: Any, enum_type: type[Enum], *, field_name: str) -> Enum:
    if not isinstance(value, enum_type):
        raise DomainValidationError(f"{field_name}: 허용된 enum 값이어야 합니다.")
    return value


def _require_date(value: Any, *, field_name: str) -> date:
    if type(value) is not date:
        raise DomainValidationError(f"{field_name}: 달력 날짜여야 합니다.")
    return value


def _sequence(value: Any, *, field_name: str) -> tuple[Any, ...]:
    """외부 iterable을 한 번만 물질화하고 민감값 없는 오류로 바꾼다."""

    if isinstance(value, (str, bytes)):
        raise DomainValidationError(f"{field_name}: 목록 형식이 올바르지 않습니다.")
    try:
        return tuple(value)
    except Exception:
        raise DomainValidationError(
            f"{field_name}: 목록 형식이 올바르지 않습니다."
        ) from None


def _pair_sequence(value: Any, *, field_name: str) -> tuple[tuple[Any, Any], ...]:
    pairs: list[tuple[Any, Any]] = []
    for item in _sequence(value, field_name=field_name):
        if isinstance(item, (str, bytes)):
            raise DomainValidationError(f"{field_name}: 쌍 목록 형식이 올바르지 않습니다.")
        try:
            pair = tuple(item)
        except Exception:
            raise DomainValidationError(
                f"{field_name}: 쌍 목록 형식이 올바르지 않습니다."
            ) from None
        if len(pair) != 2:
            raise DomainValidationError(f"{field_name}: 쌍 목록 형식이 올바르지 않습니다.")
        pairs.append((pair[0], pair[1]))
    return tuple(pairs)


def _mapping_pairs(value: Any, *, field_name: str) -> tuple[tuple[Any, Any], ...]:
    """외부 mapping을 한 번만 읽고 구현체 예외를 안전한 입력 오류로 바꾼다."""

    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name}: mapping 형식이 올바르지 않습니다.")
    try:
        items = value.items()
    except Exception:
        raise DomainValidationError(
            f"{field_name}: mapping 형식이 올바르지 않습니다."
        ) from None
    return _pair_sequence(items, field_name=field_name)


def _normalize_price_mapping(value: Any) -> dict[str, VerifiedPrice]:
    normalized: dict[str, VerifiedPrice] = {}
    for raw_key, item in _mapping_pairs(value, field_name="price inputs"):
        key = nonempty(raw_key, field_name="price instrument")
        if type(item) is not VerifiedPrice:
            raise DomainValidationError("price inputs: 가격 자료 형식이 올바르지 않습니다.")
        if key != item.instrument_id:
            raise DomainValidationError("price inputs: 종목 식별자가 일치하지 않습니다.")
        if key in normalized:
            raise DomainValidationError("price inputs: 중복 종목 식별자는 허용되지 않습니다.")
        normalized[key] = item
    return normalized


def _normalize_fx_mapping(value: Any) -> dict[Currency, VerifiedFxRate]:
    normalized: dict[Currency, VerifiedFxRate] = {}
    for key, item in _mapping_pairs(value, field_name="FX inputs"):
        if not isinstance(key, Currency) or type(item) is not VerifiedFxRate:
            raise DomainValidationError("FX inputs: 환율 자료 형식이 올바르지 않습니다.")
        if key is not item.currency:
            raise DomainValidationError("FX inputs: 통화 식별자가 일치하지 않습니다.")
        if key in normalized:
            raise DomainValidationError("FX inputs: 중복 통화는 허용되지 않습니다.")
        normalized[key] = item
    return normalized


def _normalize_classification_mapping(
    value: Any,
) -> dict[str, InstrumentClassification]:
    normalized: dict[str, InstrumentClassification] = {}
    for raw_key, item in _mapping_pairs(value, field_name="instrument classifications"):
        key = nonempty(raw_key, field_name="classification instrument")
        if type(item) is not InstrumentClassification:
            raise DomainValidationError(
                "instrument classifications: 분류 자료 형식이 올바르지 않습니다."
            )
        if key != item.instrument_id:
            raise DomainValidationError(
                "instrument classifications: 종목 식별자가 일치하지 않습니다."
            )
        if key in normalized:
            raise DomainValidationError(
                "instrument classifications: 중복 종목 식별자는 허용되지 않습니다."
            )
        normalized[key] = item
    return normalized


def _normalize_exposure_mapping(value: Any) -> dict[str, Decimal]:
    normalized: dict[str, Decimal] = {}
    for raw_key, amount in _mapping_pairs(value, field_name="active buy exposures"):
        key = nonempty(raw_key, field_name="active exposure instrument")
        if key in normalized:
            raise DomainValidationError(
                "active buy exposures: 중복 종목 식별자는 허용되지 않습니다."
            )
        normalized[key] = krw_amount(
            amount,
            field_name="active reserved buy exposure",
        )
    return normalized


def _canonical_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__paper_dataclass__": (
                f"{value.__class__.__module__}.{value.__class__.__qualname__}"
            ),
            "fields": {
                item.name: _canonical_value(getattr(value, item.name))
                for item in fields(value)
            },
        }
    if isinstance(value, Enum):
        return {
            "__paper_enum__": (
                f"{value.__class__.__module__}.{value.__class__.__qualname__}"
            ),
            "value": value.value,
        }
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DomainValidationError("hash 입력에는 유한 Decimal만 허용됩니다.")
        if value == 0:
            return {"__paper_decimal__": [0, "0", 0]}
        # Decimal.normalize() consults the process-global decimal context and can
        # round before removing zeroes.  Hash material must be context independent.
        sign, digits, exponent = value.as_tuple()
        digit_list = list(digits)
        while digit_list and digit_list[-1] == 0:
            digit_list.pop()
            exponent += 1
        coefficient = "".join(str(digit) for digit in digit_list) or "0"
        return {"__paper_decimal__": [sign, coefficient, exponent]}
    if isinstance(value, datetime):
        return {"__paper_datetime_us__": utc_epoch_microseconds(value)}
    if isinstance(value, date):
        return {"__paper_date__": value.isoformat()}
    if isinstance(value, Mapping):
        try:
            items = tuple(value.items())
        except Exception:
            raise DomainValidationError(
                "hash mapping을 안전하게 읽을 수 없습니다."
            ) from None
        if any(not isinstance(key, str) for key, _ in items):
            raise DomainValidationError("hash mapping key는 문자열이어야 합니다.")
        return {
            "__paper_mapping__": [
                [key, _canonical_value(item)]
                for key, item in sorted(items)
            ]
        }
    if isinstance(value, tuple):
        return {"__paper_tuple__": [_canonical_value(item) for item in value]}
    if isinstance(value, list):
        return {"__paper_list__": [_canonical_value(item) for item in value]}
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise DomainValidationError("hash 입력에 지원하지 않는 값이 있습니다.")


def canonical_hash(value: Any) -> str:
    try:
        payload = json.dumps(
            _canonical_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except DomainValidationError:
        raise
    except Exception:
        raise DomainValidationError("hash 입력을 안전하게 정규화할 수 없습니다.") from None
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class GateDecision:
    status: GateStatus
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_enum(self.status, GateStatus, field_name="gate status")
        reasons = _sequence(self.reason_codes, field_name="gate reason codes")
        object.__setattr__(
            self,
            "reason_codes",
            tuple(
                nonempty(item, field_name="reason code")
                for item in reasons
            ),
        )

    @property
    def permits_execution(self) -> bool:
        return self.status is GateStatus.PASS and not self.reason_codes

    @classmethod
    def passed(cls) -> "GateDecision":
        return cls(GateStatus.PASS)

    @classmethod
    def blocked(cls, *reason_codes: str) -> "GateDecision":
        return cls(GateStatus.BLOCKED, tuple(nonempty(code, field_name="reason code") for code in reason_codes))

    @classmethod
    def unknown(cls, *reason_codes: str) -> "GateDecision":
        return cls(GateStatus.UNKNOWN, tuple(nonempty(code, field_name="reason code") for code in reason_codes))


@dataclass(frozen=True)
class SatelliteLossGate:
    decision: GateDecision
    policy_version: str
    as_of: datetime
    evidence_ref: str

    def __post_init__(self) -> None:
        if type(self.decision) is not GateDecision:
            raise DomainValidationError("위성 손실 gate 판정 형식이 올바르지 않습니다.")
        object.__setattr__(
            self,
            "policy_version",
            nonempty(self.policy_version, field_name="satellite loss policy version"),
        )
        object.__setattr__(
            self,
            "as_of",
            _utc(self.as_of, field_name="satellite loss gate as_of"),
        )
        object.__setattr__(
            self,
            "evidence_ref",
            nonempty(self.evidence_ref, field_name="satellite loss evidence"),
        )


@dataclass(frozen=True)
class PaperPortfolio:
    portfolio_id: str
    owner_ref: str
    created_at: datetime
    scope: PaperScope = PaperScope.INTERNAL_PAPER
    base_currency: Currency = Currency.KRW
    initial_cash_krw: Decimal = INITIAL_PAPER_CASH_KRW

    def __post_init__(self) -> None:
        object.__setattr__(self, "portfolio_id", nonempty(self.portfolio_id, field_name="portfolio_id"))
        object.__setattr__(self, "owner_ref", nonempty(self.owner_ref, field_name="owner_ref"))
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="created_at"))
        if self.scope is not PaperScope.INTERNAL_PAPER or self.base_currency is not Currency.KRW:
            raise DomainValidationError("PAPER portfolio 영역 또는 기준통화가 올바르지 않습니다.")
        amount = krw_amount(self.initial_cash_krw, field_name="initial PAPER cash")
        if amount != INITIAL_PAPER_CASH_KRW:
            raise DomainValidationError("PAPER genesis 가상 현금은 4,000,000원으로 고정됩니다.")
        object.__setattr__(self, "initial_cash_krw", amount)


@dataclass(frozen=True)
class PaperLedgerEntry:
    entry_id: str
    event_type: LedgerEventType
    portfolio_id: str
    occurred_at: datetime
    recorded_at: datetime
    amount: Decimal
    currency: Currency
    strategy_id: str | None = None
    instrument_id: str | None = None
    quantity: int = 0
    source_ref: str = "PURE_DOMAIN"
    corrects_entry_id: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.event_type, LedgerEventType, field_name="ledger event type")
        _require_enum(self.currency, Currency, field_name="ledger currency")
        object.__setattr__(self, "entry_id", nonempty(self.entry_id, field_name="entry_id"))
        object.__setattr__(self, "portfolio_id", nonempty(self.portfolio_id, field_name="portfolio_id"))
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, field_name="occurred_at"))
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, field_name="recorded_at"))
        if self.recorded_at < self.occurred_at:
            raise DomainValidationError("원장 기록시각은 발생시각보다 이를 수 없습니다.")
        object.__setattr__(self, "amount", exact_decimal(self.amount, field_name="ledger amount"))
        object.__setattr__(self, "quantity", _positive_quantity(self.quantity, field_name="ledger quantity", allow_zero=True))
        object.__setattr__(self, "source_ref", nonempty(self.source_ref, field_name="source_ref"))


@dataclass(frozen=True)
class PaperBudgetVersion:
    version_id: str
    policy_version: str
    allocations_krw: tuple[tuple[PaperStrategyKind, Decimal], ...]
    approved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_id", nonempty(self.version_id, field_name="budget version"))
        object.__setattr__(self, "policy_version", nonempty(self.policy_version, field_name="policy version"))
        object.__setattr__(self, "approved_at", _utc(self.approved_at, field_name="budget approved_at"))
        allocations = _pair_sequence(
            self.allocations_krw,
            field_name="strategy budget allocations",
        )
        normalized: list[tuple[PaperStrategyKind, Decimal]] = []
        seen: set[PaperStrategyKind] = set()
        for strategy_id, amount in allocations:
            if not isinstance(strategy_id, PaperStrategyKind):
                raise DomainValidationError("PAPER 예산에는 확정된 8전략 식별자만 허용됩니다.")
            if strategy_id in seen:
                raise DomainValidationError("전략 예산 ID가 중복됐습니다.")
            seen.add(strategy_id)
            normalized.append((strategy_id, krw_amount(amount, field_name="strategy budget")))
        if seen != set(PaperStrategyKind):
            raise DomainValidationError("PAPER 예산 버전에는 8전략 배정값이 모두 필요합니다.")
        object.__setattr__(self, "allocations_krw", tuple(normalized))

    @property
    def total_krw(self) -> Decimal:
        try:
            with localcontext(_paper_decimal_context(exact=True)):
                return sum(
                    (amount for _, amount in self.allocations_krw),
                    Decimal("0"),
                )
        except DecimalException as exc:
            raise DomainValidationError(
                "PAPER 전략 예산 합계를 정확하게 계산할 수 없습니다."
            ) from exc


@dataclass(frozen=True)
class PaperStrategyVersion:
    version_id: str
    content_hash: str
    created_at: datetime
    prior_version_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_id", nonempty(self.version_id, field_name="strategy version"))
        object.__setattr__(self, "content_hash", nonempty(self.content_hash, field_name="strategy hash"))
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="strategy created_at"))


@dataclass(frozen=True)
class StrategyApplication:
    application_id: str
    strategy_version_id: str
    approval_ref: str
    applied_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", nonempty(self.application_id, field_name="application_id"))
        object.__setattr__(self, "strategy_version_id", nonempty(self.strategy_version_id, field_name="strategy version"))
        object.__setattr__(self, "approval_ref", nonempty(self.approval_ref, field_name="approval_ref"))
        object.__setattr__(self, "applied_at", _utc(self.applied_at, field_name="strategy applied_at"))


@dataclass(frozen=True)
class PaperPositionLot:
    lot_id: str
    strategy_id: str
    instrument_id: str
    acquired_at: datetime
    currency: Currency
    unit_cost: Decimal
    original_cost: Decimal
    remaining_cost: Decimal
    original_quantity: int
    remaining_quantity: int
    acquisition_entry_id: str

    def __post_init__(self) -> None:
        _require_enum(self.currency, Currency, field_name="lot currency")
        for field_name in ("lot_id", "strategy_id", "instrument_id", "acquisition_entry_id"):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "acquired_at", _utc(self.acquired_at, field_name="lot acquired_at"))
        object.__setattr__(self, "unit_cost", _positive_decimal(self.unit_cost, field_name="unit cost"))
        object.__setattr__(self, "original_cost", _positive_decimal(self.original_cost, field_name="original lot cost"))
        object.__setattr__(self, "remaining_cost", _positive_decimal(self.remaining_cost, field_name="remaining lot cost", allow_zero=True))
        object.__setattr__(self, "original_quantity", _positive_quantity(self.original_quantity, field_name="original quantity"))
        object.__setattr__(self, "remaining_quantity", _positive_quantity(self.remaining_quantity, field_name="remaining quantity", allow_zero=True))
        if self.remaining_quantity > self.original_quantity:
            raise DomainValidationError("lot 잔여수량은 최초수량을 초과할 수 없습니다.")
        if self.remaining_cost > self.original_cost:
            raise DomainValidationError("lot 잔여원가는 최초원가를 초과할 수 없습니다.")
        if (self.remaining_quantity == 0) != (self.remaining_cost == 0):
            raise DomainValidationError("lot 잔여수량과 잔여원가의 0 상태가 일치해야 합니다.")


@dataclass(frozen=True)
class VerifiedPrice:
    instrument_id: str
    value: Decimal
    currency: Currency
    as_of: datetime
    source_record_id: str
    verified: bool

    def __post_init__(self) -> None:
        _require_enum(self.currency, Currency, field_name="price currency")
        _require_bool(self.verified, field_name="price verified")
        object.__setattr__(self, "instrument_id", nonempty(self.instrument_id, field_name="instrument_id"))
        object.__setattr__(self, "value", _positive_decimal(self.value, field_name="price"))
        object.__setattr__(self, "as_of", _utc(self.as_of, field_name="price as_of"))
        object.__setattr__(self, "source_record_id", nonempty(self.source_record_id, field_name="source record"))


@dataclass(frozen=True)
class PriceCondition:
    operator: PriceConditionOperator
    threshold: Decimal
    currency: Currency
    evidence_ref: str
    evidence_cutoff_at: datetime

    def __post_init__(self) -> None:
        _require_enum(
            self.operator,
            PriceConditionOperator,
            field_name="price condition operator",
        )
        _require_enum(self.currency, Currency, field_name="price condition currency")
        object.__setattr__(
            self,
            "threshold",
            _positive_decimal(self.threshold, field_name="price condition threshold"),
        )
        object.__setattr__(
            self,
            "evidence_ref",
            nonempty(self.evidence_ref, field_name="price condition evidence"),
        )
        object.__setattr__(
            self,
            "evidence_cutoff_at",
            _utc(
                self.evidence_cutoff_at,
                field_name="price condition evidence cutoff",
            ),
        )

    def is_met(self, price: Decimal) -> bool:
        observed = _positive_decimal(price, field_name="observed condition price")
        if self.operator is PriceConditionOperator.AT_OR_BELOW:
            return observed <= self.threshold
        return observed >= self.threshold


@dataclass(frozen=True)
class PriceConditionCheck:
    proposal_id: str
    proposal_hash: str
    observed_price: VerifiedPrice
    observation_cutoff_at: datetime
    evaluated_at: datetime
    decision: GateDecision

    def __post_init__(self) -> None:
        for field_name in ("proposal_id", "proposal_hash"):
            object.__setattr__(
                self,
                field_name,
                nonempty(getattr(self, field_name), field_name=field_name),
            )
        if type(self.observed_price) is not VerifiedPrice:
            raise DomainValidationError("가격조건 관측값 형식이 올바르지 않습니다.")
        if type(self.decision) is not GateDecision:
            raise DomainValidationError("가격조건 판정 형식이 올바르지 않습니다.")
        object.__setattr__(
            self,
            "observation_cutoff_at",
            _utc(self.observation_cutoff_at, field_name="price observation cutoff"),
        )
        object.__setattr__(
            self,
            "evaluated_at",
            _utc(self.evaluated_at, field_name="price condition evaluated_at"),
        )
        if (
            self.observed_price.as_of > self.observation_cutoff_at
            or self.observation_cutoff_at > self.evaluated_at
        ):
            raise DomainValidationError("가격조건 판정 시각의 인과관계가 올바르지 않습니다.")


@dataclass(frozen=True)
class VerifiedFxRate:
    currency: Currency
    krw_per_unit: Decimal
    as_of: datetime
    source_record_id: str
    verified: bool

    def __post_init__(self) -> None:
        _require_enum(self.currency, Currency, field_name="FX currency")
        _require_bool(self.verified, field_name="FX verified")
        if self.currency is Currency.KRW:
            raise DomainValidationError("KRW 자체 환율 입력은 사용하지 않습니다.")
        object.__setattr__(self, "krw_per_unit", _positive_decimal(self.krw_per_unit, field_name="FX rate"))
        object.__setattr__(self, "as_of", _utc(self.as_of, field_name="FX as_of"))
        object.__setattr__(self, "source_record_id", nonempty(self.source_record_id, field_name="FX source record"))


@dataclass(frozen=True)
class PaperValuationSnapshot:
    snapshot_id: str
    portfolio_id: str
    as_of: datetime
    cutoff_at: datetime
    status: GateStatus
    nav_krw: Decimal | None
    cash_krw: Decimal | None
    position_value_krw: Decimal | None
    unrealized_pnl_krw: Decimal | None
    instrument_values_krw: tuple[tuple[str, Decimal], ...]
    reason_codes: tuple[str, ...] = ()
    source_state_hash: str | None = None

    def __post_init__(self) -> None:
        instrument_values = _pair_sequence(
            self.instrument_values_krw,
            field_name="valuation instrument values",
        )
        reasons = _sequence(self.reason_codes, field_name="valuation reason codes")
        object.__setattr__(
            self,
            "reason_codes",
            tuple(nonempty(reason, field_name="valuation reason code") for reason in reasons),
        )
        _require_enum(self.status, GateStatus, field_name="valuation status")
        object.__setattr__(self, "snapshot_id", nonempty(self.snapshot_id, field_name="snapshot_id"))
        object.__setattr__(self, "portfolio_id", nonempty(self.portfolio_id, field_name="portfolio_id"))
        object.__setattr__(self, "as_of", _utc(self.as_of, field_name="valuation as_of"))
        object.__setattr__(self, "cutoff_at", _utc(self.cutoff_at, field_name="valuation cutoff"))
        if self.source_state_hash is not None:
            object.__setattr__(
                self,
                "source_state_hash",
                nonempty(self.source_state_hash, field_name="valuation source state hash"),
            )
        if self.as_of > self.cutoff_at:
            raise DomainValidationError("평가 기준시각은 cutoff 뒤일 수 없습니다.")
        if self.status is GateStatus.PASS:
            if any(value is None for value in (self.nav_krw, self.cash_krw, self.position_value_krw, self.unrealized_pnl_krw)):
                raise DomainValidationError("완료된 PAPER 평가에는 모든 계산값이 필요합니다.")
            for field_name in ("nav_krw", "cash_krw", "position_value_krw"):
                value = _positive_decimal(
                    getattr(self, field_name),
                    field_name=field_name,
                    allow_zero=True,
                )
                object.__setattr__(self, field_name, value)
            object.__setattr__(
                self,
                "unrealized_pnl_krw",
                exact_decimal(self.unrealized_pnl_krw, field_name="unrealized_pnl_krw"),
            )
            seen: set[str] = set()
            normalized_values: list[tuple[str, Decimal]] = []
            for instrument_id, raw_value in instrument_values:
                instrument = nonempty(instrument_id, field_name="valuation instrument_id")
                if instrument in seen:
                    raise DomainValidationError("평가 종목 값이 중복됐습니다.")
                seen.add(instrument)
                normalized_values.append(
                    (
                        instrument,
                        _positive_decimal(
                            raw_value,
                            field_name="instrument value",
                            allow_zero=True,
                        ),
                    )
                )
            object.__setattr__(self, "instrument_values_krw", tuple(normalized_values))
            try:
                with localcontext(_paper_decimal_context(exact=True)):
                    if self.cash_krw + self.position_value_krw != self.nav_krw:
                        raise DomainValidationError("PAPER NAV는 현금과 보유자산 평가액의 합이어야 합니다.")
                    if sum((value for _, value in self.instrument_values_krw), Decimal("0")) != self.position_value_krw:
                        raise DomainValidationError("종목별 평가액 합이 position 평가액과 일치하지 않습니다.")
            except DecimalException as exc:
                raise DomainValidationError("PAPER 평가 합계를 정확하게 검증할 수 없습니다.") from exc
        else:
            if any(value is not None for value in (self.nav_krw, self.cash_krw, self.position_value_krw, self.unrealized_pnl_krw)):
                raise DomainValidationError("결측 PAPER 평가는 0 또는 부분 합계로 표시하지 않습니다.")
            if instrument_values:
                raise DomainValidationError("결측 PAPER 평가는 종목별 부분 합계를 표시하지 않습니다.")
            object.__setattr__(self, "instrument_values_krw", ())


@dataclass(frozen=True)
class InstrumentClassification:
    instrument_id: str
    instrument_type: InstrumentType
    risk_role: RiskRole
    policy_version: str
    evidence_ref: str
    verified: bool

    def __post_init__(self) -> None:
        _require_enum(self.instrument_type, InstrumentType, field_name="instrument type")
        _require_enum(self.risk_role, RiskRole, field_name="risk role")
        _require_bool(self.verified, field_name="classification verified")
        for field_name in ("instrument_id", "policy_version", "evidence_ref"):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        if self.instrument_type is InstrumentType.UNCLASSIFIED and self.risk_role is not RiskRole.UNCLASSIFIED_ASSUMED_SATELLITE:
            raise DomainValidationError("미분류 상품은 위성으로 한 번 계상하는 역할이어야 합니다.")
        if self.instrument_type is InstrumentType.PROHIBITED and self.risk_role is not RiskRole.INELIGIBLE:
            raise DomainValidationError("금지상품은 INELIGIBLE로 분류해야 합니다.")


@dataclass(frozen=True)
class RiskPolicyInputs:
    policy_version: str
    denominator_basis: RiskDenominatorBasis | None
    session_opening_nav_krw: Decimal | None
    satellite_loss_policy_confirmed: bool | None
    mdd_policy_confirmed: bool | None
    industry_policy_confirmed: bool | None
    company_policy_confirmed: bool | None
    satellite_loss_gate: SatelliteLossGate | None = None

    def __post_init__(self) -> None:
        if self.denominator_basis is not None:
            _require_enum(
                self.denominator_basis,
                RiskDenominatorBasis,
                field_name="risk denominator basis",
            )
        for field_name in (
            "satellite_loss_policy_confirmed",
            "mdd_policy_confirmed",
            "industry_policy_confirmed",
            "company_policy_confirmed",
        ):
            _require_optional_bool(getattr(self, field_name), field_name=field_name)
        if self.satellite_loss_gate is not None and type(self.satellite_loss_gate) is not SatelliteLossGate:
            raise DomainValidationError("위성 손실 gate 형식이 올바르지 않습니다.")
        if (
            self.denominator_basis is RiskDenominatorBasis.CURRENT_PAPER_NAV
            and self.session_opening_nav_krw is not None
        ):
            raise DomainValidationError("현재 NAV 분모에는 세션 시작 NAV를 함께 넣지 않습니다.")
        if (
            self.denominator_basis is RiskDenominatorBasis.SESSION_OPENING_PAPER_NAV
            and self.session_opening_nav_krw is None
        ):
            raise DomainValidationError("세션 시작 NAV 분모 값이 필요합니다.")
        object.__setattr__(self, "policy_version", nonempty(self.policy_version, field_name="risk policy version"))
        if self.session_opening_nav_krw is not None:
            object.__setattr__(
                self,
                "session_opening_nav_krw",
                _positive_decimal(
                    self.session_opening_nav_krw,
                    field_name="session opening NAV",
                    allow_zero=True,
                ),
            )


@dataclass(frozen=True)
class PaperRiskAssessment:
    status: GateStatus
    denominator_krw: Decimal | None
    instrument_ratio: Decimal | None
    satellite_ratio: Decimal | None
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_enum(self.status, GateStatus, field_name="risk assessment status")
        for field_name in ("denominator_krw", "instrument_ratio", "satellite_ratio"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    exact_decimal(value, field_name=field_name),
                )
        reasons = _sequence(self.reason_codes, field_name="risk reason codes")
        object.__setattr__(
            self,
            "reason_codes",
            tuple(
                nonempty(item, field_name="risk reason code")
                for item in reasons
            ),
        )

    @property
    def permits_buy(self) -> bool:
        return self.status is GateStatus.PASS and not self.reason_codes


@dataclass(frozen=True)
class PaperRiskContext:
    valuation: PaperValuationSnapshot
    classifications: tuple[InstrumentClassification, ...]
    policy: RiskPolicyInputs

    def __post_init__(self) -> None:
        if type(self.valuation) is not PaperValuationSnapshot:
            raise DomainValidationError("위험 평가 snapshot 형식이 올바르지 않습니다.")
        if type(self.policy) is not RiskPolicyInputs:
            raise DomainValidationError("위험 정책 입력 형식이 올바르지 않습니다.")
        normalized = _sequence(
            self.classifications,
            field_name="risk classifications",
        )
        if any(type(item) is not InstrumentClassification for item in normalized):
            raise DomainValidationError("위험 분류 목록 값이 올바르지 않습니다.")
        object.__setattr__(self, "classifications", normalized)
        instrument_ids = tuple(item.instrument_id for item in self.classifications)
        if len(set(instrument_ids)) != len(instrument_ids):
            raise DomainValidationError("위험 분류 종목이 중복됐습니다.")
        if any(
            item.verified and item.policy_version != self.policy.policy_version
            for item in self.classifications
        ):
            raise DomainValidationError("검증된 상품 분류와 위험 정책 버전이 일치해야 합니다.")

    def classification_map(self) -> dict[str, InstrumentClassification]:
        return {item.instrument_id: item for item in self.classifications}


@dataclass(frozen=True)
class TradeAmounts:
    gross_trade_currency: Decimal
    maximum_cost_trade_currency: Decimal
    cash_required: Decimal
    cash_currency: Currency
    gross_krw: Decimal
    maximum_cost_krw: Decimal
    approval_judgment_krw: Decimal
    fx_krw_per_unit: Decimal | None = None

    def __post_init__(self) -> None:
        _require_enum(self.cash_currency, Currency, field_name="cash currency")
        for field_name in ("gross_trade_currency", "maximum_cost_trade_currency", "cash_required"):
            object.__setattr__(self, field_name, _positive_decimal(getattr(self, field_name), field_name=field_name, allow_zero=True))
        for field_name in ("gross_krw", "maximum_cost_krw", "approval_judgment_krw"):
            object.__setattr__(self, field_name, krw_amount(getattr(self, field_name), field_name=field_name))
        try:
            with localcontext(_paper_decimal_context(exact=True)):
                if self.cash_required != self.gross_trade_currency + self.maximum_cost_trade_currency:
                    raise DomainValidationError("현금 필요액은 거래대금과 최대비용의 합이어야 합니다.")
                if self.approval_judgment_krw != self.gross_krw + self.maximum_cost_krw:
                    raise DomainValidationError("승인 판정액은 KRW 거래대금과 최대비용의 합이어야 합니다.")
                if self.cash_currency is Currency.KRW:
                    if self.fx_krw_per_unit is not None:
                        raise DomainValidationError("KRW 금액 묶음에는 FX를 넣지 않습니다.")
                    if self.gross_trade_currency != self.gross_krw or self.maximum_cost_trade_currency != self.maximum_cost_krw:
                        raise DomainValidationError("KRW native 금액과 KRW 판정액이 일치해야 합니다.")
                else:
                    if self.fx_krw_per_unit is None:
                        raise DomainValidationError("외화 금액 묶음에는 검증된 FX가 필요합니다.")
                    fx = _positive_decimal(self.fx_krw_per_unit, field_name="trade FX rate")
                    object.__setattr__(self, "fx_krw_per_unit", fx)
                    if self.gross_krw != (self.gross_trade_currency * fx).to_integral_value(rounding=ROUND_CEILING):
                        raise DomainValidationError("외화 gross의 KRW 환산값이 일치하지 않습니다.")
                    if self.maximum_cost_krw != (self.maximum_cost_trade_currency * fx).to_integral_value(rounding=ROUND_CEILING):
                        raise DomainValidationError("외화 비용의 KRW 환산값이 일치하지 않습니다.")
        except DecimalException as exc:
            raise DomainValidationError("PAPER 금액 묶음을 정확하게 검증할 수 없습니다.") from exc


@dataclass(frozen=True)
class TradeAmountsResult:
    decision: GateDecision
    amounts: TradeAmounts | None


def calculate_trade_amounts(
    *,
    quantity: int,
    unit_price: Decimal | str | int | None,
    currency: Currency,
    maximum_cost: Decimal | str | int | None,
    fx_krw_per_unit: Decimal | str | int | None,
) -> TradeAmountsResult:
    """검증된 caller 입력으로 gross/cash/approval 금액을 서로 분리한다."""
    try:
        context = _paper_decimal_context(exact=True)
        with localcontext(context):
            return _calculate_trade_amounts_exact(
                quantity=quantity,
                unit_price=unit_price,
                currency=currency,
                maximum_cost=maximum_cost,
                fx_krw_per_unit=fx_krw_per_unit,
            )
    except DecimalException as exc:
        raise DomainValidationError("PAPER 금액을 정확하게 계산할 수 없습니다.") from exc


def _calculate_trade_amounts_exact(
    *,
    quantity: int,
    unit_price: Decimal | str | int | None,
    currency: Currency,
    maximum_cost: Decimal | str | int | None,
    fx_krw_per_unit: Decimal | str | int | None,
) -> TradeAmountsResult:
    _require_enum(currency, Currency, field_name="trade currency")
    qty = _positive_quantity(quantity)
    if unit_price is None:
        return TradeAmountsResult(GateDecision.unknown("PRICE_UNAVAILABLE"), None)
    if maximum_cost is None:
        return TradeAmountsResult(GateDecision.unknown("COST_POLICY_INPUT_REQUIRED"), None)
    price = _positive_decimal(unit_price, field_name="unit price")
    cost = _positive_decimal(maximum_cost, field_name="maximum cost", allow_zero=True)
    gross = price * Decimal(qty)
    if currency is Currency.KRW:
        if fx_krw_per_unit is not None:
            raise DomainValidationError("KRW 거래에는 FX 값을 함께 넣지 않습니다.")
        gross_krw = krw_amount(gross, field_name="KRW gross")
        cost_krw = krw_amount(cost, field_name="KRW maximum cost")
        fx: Decimal | None = None
    else:
        if fx_krw_per_unit is None:
            return TradeAmountsResult(GateDecision.unknown("FX_UNAVAILABLE"), None)
        fx = _positive_decimal(fx_krw_per_unit, field_name="FX rate")
        gross_krw = (gross * fx).to_integral_value(rounding=ROUND_CEILING)
        cost_krw = (cost * fx).to_integral_value(rounding=ROUND_CEILING)
    return TradeAmountsResult(
        GateDecision.passed(),
        TradeAmounts(
            gross_trade_currency=gross,
            maximum_cost_trade_currency=cost,
            cash_required=gross + cost,
            cash_currency=currency,
            gross_krw=gross_krw,
            maximum_cost_krw=cost_krw,
            approval_judgment_krw=gross_krw + cost_krw,
            fx_krw_per_unit=fx,
        ),
    )


@dataclass(frozen=True)
class VerifiedSessionWindow:
    us_session_date: date
    session_calendar_ref: str
    opens_at: datetime
    evidence_ref: str
    verified: bool

    def __post_init__(self) -> None:
        _require_date(self.us_session_date, field_name="US session date")
        _require_bool(self.verified, field_name="session verified")
        object.__setattr__(
            self,
            "session_calendar_ref",
            nonempty(self.session_calendar_ref, field_name="session calendar ref"),
        )
        object.__setattr__(self, "opens_at", _utc(self.opens_at, field_name="session opens_at"))
        object.__setattr__(
            self,
            "evidence_ref",
            nonempty(self.evidence_ref, field_name="session evidence ref"),
        )


@dataclass(frozen=True)
class PaperDailyLimit:
    us_session_date: date
    session_calendar_ref: str
    opening_valuation_snapshot_id: str
    opening_nav_krw: Decimal
    limit_krw: Decimal
    executed_gross_krw: Decimal = Decimal("0")
    active_reserved_gross_krw: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _require_date(self.us_session_date, field_name="daily limit US session date")
        object.__setattr__(self, "session_calendar_ref", nonempty(self.session_calendar_ref, field_name="session calendar ref"))
        object.__setattr__(
            self,
            "opening_valuation_snapshot_id",
            nonempty(self.opening_valuation_snapshot_id, field_name="opening valuation snapshot"),
        )
        opening = _positive_decimal(
            self.opening_nav_krw,
            field_name="opening NAV",
            allow_zero=True,
        )
        limit_value = krw_amount(
            self.limit_krw,
            field_name="daily limit",
        )
        try:
            with localcontext(_paper_decimal_context(exact=True)):
                expected_limit = (opening * DAILY_GROSS_RATE).to_integral_value(
                    rounding=ROUND_FLOOR
                )
                if limit_value != expected_limit:
                    raise DomainValidationError(
                        "PAPER 일한도는 세션 시작 NAV 20%의 원 단위 내림값이어야 합니다."
                    )
        except DecimalException as exc:
            raise DomainValidationError("PAPER 일한도를 정확하게 검증할 수 없습니다.") from exc
        object.__setattr__(self, "opening_nav_krw", opening)
        object.__setattr__(self, "limit_krw", limit_value)
        object.__setattr__(self, "executed_gross_krw", krw_amount(self.executed_gross_krw, field_name="executed gross"))
        object.__setattr__(self, "active_reserved_gross_krw", krw_amount(self.active_reserved_gross_krw, field_name="reserved gross"))
        try:
            with localcontext(_paper_decimal_context(exact=True)):
                if self.executed_gross_krw + self.active_reserved_gross_krw > self.limit_krw:
                    raise DomainValidationError("PAPER 일한도 보존식이 위반됐습니다.")
        except DecimalException as exc:
            raise DomainValidationError("PAPER 일한도 사용액을 정확하게 검증할 수 없습니다.") from exc

    @property
    def available_krw(self) -> Decimal:
        try:
            with localcontext(_paper_decimal_context(exact=True)):
                return self.limit_krw - self.executed_gross_krw - self.active_reserved_gross_krw
        except DecimalException as exc:
            raise DomainValidationError("PAPER 일한도 잔액을 정확하게 계산할 수 없습니다.") from exc


@dataclass(frozen=True)
class TradePurpose:
    trade_purpose_id: str
    stable_root_trade_purpose_id: str
    purpose_version: str
    purpose_version_hash: str
    strategy_id: PaperStrategyKind
    strategy_version_ref: str
    budget_version_ref: str
    policy_version_ref: str
    intended_effect: str
    created_at: datetime
    parent_trade_purpose_id: str | None = None
    changed_from_root_id: str | None = None
    new_root_approval_ref: str | None = None
    new_root_audit_ref: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "trade_purpose_id",
            "stable_root_trade_purpose_id",
            "purpose_version",
            "purpose_version_hash",
            "strategy_version_ref",
            "budget_version_ref",
            "policy_version_ref",
            "intended_effect",
        ):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="purpose created_at"))
        if not isinstance(self.strategy_id, PaperStrategyKind):
            raise DomainValidationError("TradePurpose 전략 식별자가 올바르지 않습니다.")
        expected_hash = trade_purpose_version_hash(
            trade_purpose_id=self.trade_purpose_id,
            stable_root_trade_purpose_id=self.stable_root_trade_purpose_id,
            purpose_version=self.purpose_version,
            strategy_id=self.strategy_id,
            strategy_version_ref=self.strategy_version_ref,
            budget_version_ref=self.budget_version_ref,
            policy_version_ref=self.policy_version_ref,
            intended_effect=self.intended_effect,
            parent_trade_purpose_id=self.parent_trade_purpose_id,
            changed_from_root_id=self.changed_from_root_id,
        )
        if self.purpose_version_hash != expected_hash:
            raise DomainValidationError("TradePurpose 불변 version hash가 내용과 일치하지 않습니다.")


def trade_purpose_version_hash(
    *,
    trade_purpose_id: str,
    stable_root_trade_purpose_id: str,
    purpose_version: str,
    strategy_id: PaperStrategyKind,
    strategy_version_ref: str,
    budget_version_ref: str,
    policy_version_ref: str,
    intended_effect: str,
    parent_trade_purpose_id: str | None = None,
    changed_from_root_id: str | None = None,
) -> str:
    """승인 대상에 묶이는 TradePurpose 경제적 내용의 결정적 hash."""

    return canonical_hash(
        {
            "trade_purpose_id": nonempty(trade_purpose_id, field_name="trade_purpose_id"),
            "stable_root_trade_purpose_id": nonempty(
                stable_root_trade_purpose_id,
                field_name="stable_root_trade_purpose_id",
            ),
            "purpose_version": nonempty(purpose_version, field_name="purpose_version"),
            "strategy_id": strategy_id,
            "strategy_version_ref": nonempty(strategy_version_ref, field_name="strategy_version_ref"),
            "budget_version_ref": nonempty(budget_version_ref, field_name="budget_version_ref"),
            "policy_version_ref": nonempty(policy_version_ref, field_name="policy_version_ref"),
            "intended_effect": nonempty(intended_effect, field_name="intended_effect"),
            "parent_trade_purpose_id": parent_trade_purpose_id,
            "changed_from_root_id": changed_from_root_id,
        }
    )


@dataclass(frozen=True)
class Candidate:
    rank: int
    instrument_id: str

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or not 1 <= self.rank <= 4:
            raise DomainValidationError("후보 순위는 1~4 정수여야 합니다.")
        object.__setattr__(self, "instrument_id", nonempty(self.instrument_id, field_name="candidate instrument"))


def candidate_set_content_hash(
    *,
    set_id: str,
    version: str,
    trade_purpose_id: str,
    stable_root_trade_purpose_id: str,
    candidates: tuple[Candidate, ...],
    evidence_cutoff: datetime,
    created_at: datetime,
    regeneration_of: str | None = None,
) -> str:
    return canonical_hash(
        {
            "set_id": set_id,
            "version": version,
            "trade_purpose_id": trade_purpose_id,
            "stable_root_trade_purpose_id": stable_root_trade_purpose_id,
            "candidates": candidates,
            "evidence_cutoff": _utc(evidence_cutoff, field_name="candidate cutoff"),
            "created_at": _utc(created_at, field_name="candidate created_at"),
            "regeneration_of": regeneration_of,
        }
    )


@dataclass(frozen=True)
class CandidateRankClearance:
    set_id: str
    failed_rank: int
    reason: CandidateFailureReason
    evidence_ref: str
    verified_at: datetime
    approved_reason_set_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "set_id", nonempty(self.set_id, field_name="candidate set_id"))
        object.__setattr__(
            self,
            "failed_rank",
            _positive_quantity(self.failed_rank, field_name="failed candidate rank"),
        )
        _require_enum(self.reason, CandidateFailureReason, field_name="candidate failure reason")
        object.__setattr__(
            self,
            "evidence_ref",
            nonempty(self.evidence_ref, field_name="candidate failure evidence"),
        )
        object.__setattr__(self, "verified_at", _utc(self.verified_at, field_name="candidate failure verified_at"))
        object.__setattr__(
            self,
            "approved_reason_set_hash",
            nonempty(self.approved_reason_set_hash, field_name="approved reason set hash"),
        )


@dataclass(frozen=True)
class CandidateSet:
    set_id: str
    version: str
    set_hash: str
    trade_purpose_id: str
    stable_root_trade_purpose_id: str
    candidates: tuple[Candidate, ...]
    evidence_cutoff: datetime
    created_at: datetime
    state: CandidateSetState = CandidateSetState.DRAFT
    regeneration_of: str | None = None
    approval_ref: str | None = None

    def __post_init__(self) -> None:
        normalized_candidates = _sequence(
            self.candidates,
            field_name="candidate list",
        )
        if any(type(candidate) is not Candidate for candidate in normalized_candidates):
            raise DomainValidationError("후보 목록에는 Candidate만 허용됩니다.")
        object.__setattr__(self, "candidates", normalized_candidates)
        _require_enum(self.state, CandidateSetState, field_name="candidate set state")
        for field_name in ("set_id", "version", "set_hash", "trade_purpose_id", "stable_root_trade_purpose_id"):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "evidence_cutoff", _utc(self.evidence_cutoff, field_name="candidate cutoff"))
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="candidate created_at"))
        if self.created_at < self.evidence_cutoff:
            raise DomainValidationError("후보군 생성시각은 근거 cutoff보다 이를 수 없습니다.")
        if not 1 <= len(self.candidates) <= 4:
            raise DomainValidationError("후보군은 제공된 후보 1~4개여야 합니다.")
        ranks = tuple(candidate.rank for candidate in self.candidates)
        if len(set(ranks)) != len(ranks):
            raise DomainValidationError("후보 순위가 중복됐습니다.")
        if tuple(sorted(ranks)) != tuple(range(1, len(ranks) + 1)):
            raise DomainValidationError("후보 순위는 1부터 연속되어야 합니다.")
        expected_hash = candidate_set_content_hash(
            set_id=self.set_id,
            version=self.version,
            trade_purpose_id=self.trade_purpose_id,
            stable_root_trade_purpose_id=self.stable_root_trade_purpose_id,
            candidates=self.candidates,
            evidence_cutoff=self.evidence_cutoff,
            created_at=self.created_at,
            regeneration_of=self.regeneration_of,
        )
        if self.set_hash != expected_hash:
            raise DomainValidationError("CandidateSet 불변 hash가 내용과 일치하지 않습니다.")


def trade_proposal_content_hash(
    *,
    proposal_id: str,
    version: str,
    candidate_set_id: str,
    candidate_rank: int,
    trade_purpose_id: str,
    purpose_version: str,
    stable_root_trade_purpose_id: str,
    strategy_id: PaperStrategyKind,
    trade_intent_id: str,
    instrument_id: str,
    side: Side,
    quantity: int,
    amounts: TradeAmounts,
    us_session_date: date,
    kst_intent_date: date,
    expires_at: datetime,
    pricing_fx_rate: VerifiedFxRate | None = None,
    pricing_cutoff_at: datetime | None = None,
    price_condition: PriceCondition | None = None,
) -> str:
    return canonical_hash(
        {
            "proposal_id": proposal_id,
            "version": version,
            "candidate_set_id": candidate_set_id,
            "candidate_rank": candidate_rank,
            "trade_purpose_id": trade_purpose_id,
            "purpose_version": purpose_version,
            "stable_root_trade_purpose_id": stable_root_trade_purpose_id,
            "strategy_id": strategy_id,
            "trade_intent_id": trade_intent_id,
            "instrument_id": instrument_id,
            "side": side,
            "quantity": quantity,
            "amounts": amounts,
            "us_session_date": _require_date(
                us_session_date,
                field_name="proposal US session date",
            ),
            "kst_intent_date": _require_date(
                kst_intent_date,
                field_name="proposal KST intent date",
            ),
            "expires_at": _utc(expires_at, field_name="proposal expires_at"),
            "pricing_fx_rate": pricing_fx_rate,
            "pricing_cutoff_at": (
                _utc(pricing_cutoff_at, field_name="proposal FX cutoff")
                if pricing_cutoff_at is not None
                else None
            ),
            "price_condition": price_condition,
        }
    )


@dataclass(frozen=True)
class TradeProposal:
    proposal_id: str
    version: str
    proposal_hash: str
    candidate_set_id: str
    candidate_rank: int
    trade_purpose_id: str
    purpose_version: str
    stable_root_trade_purpose_id: str
    strategy_id: PaperStrategyKind
    trade_intent_id: str
    instrument_id: str
    side: Side
    quantity: int
    amounts: TradeAmounts
    us_session_date: date
    kst_intent_date: date
    expires_at: datetime
    state: ProposalState = ProposalState.PROPOSED
    pricing_fx_rate: VerifiedFxRate | None = None
    pricing_cutoff_at: datetime | None = None
    price_condition: PriceCondition | None = None

    def __post_init__(self) -> None:
        if type(self.amounts) is not TradeAmounts:
            raise DomainValidationError("proposal 금액 형식이 올바르지 않습니다.")
        if self.pricing_fx_rate is not None and type(self.pricing_fx_rate) is not VerifiedFxRate:
            raise DomainValidationError("proposal FX 근거 형식이 올바르지 않습니다.")
        _require_enum(self.strategy_id, PaperStrategyKind, field_name="proposal strategy")
        _require_enum(self.side, Side, field_name="proposal side")
        _require_enum(self.state, ProposalState, field_name="proposal state")
        _require_date(self.us_session_date, field_name="proposal US session date")
        _require_date(self.kst_intent_date, field_name="proposal KST intent date")
        for field_name in (
            "proposal_id",
            "version",
            "proposal_hash",
            "candidate_set_id",
            "trade_purpose_id",
            "purpose_version",
            "stable_root_trade_purpose_id",
            "trade_intent_id",
            "instrument_id",
        ):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        if isinstance(self.candidate_rank, bool) or not isinstance(self.candidate_rank, int) or not 1 <= self.candidate_rank <= 4:
            raise DomainValidationError("proposal 후보 순위가 올바르지 않습니다.")
        object.__setattr__(self, "quantity", _positive_quantity(self.quantity))
        object.__setattr__(self, "expires_at", _utc(self.expires_at, field_name="proposal expires_at"))
        if type(self.price_condition) is not PriceCondition:
            raise DomainValidationError("proposal에는 명시적인 가격조건이 필요합니다.")
        if self.price_condition.currency is not self.amounts.cash_currency:
            raise DomainValidationError("가격조건 통화와 proposal 통화가 일치해야 합니다.")
        expected_operator = (
            PriceConditionOperator.AT_OR_BELOW
            if self.side is Side.BUY
            else PriceConditionOperator.AT_OR_ABOVE
        )
        if self.price_condition.operator is not expected_operator:
            raise DomainValidationError("매수·매도 방향에 맞는 가격조건이 필요합니다.")
        if self.amounts.cash_currency is Currency.KRW:
            if self.pricing_fx_rate is not None or self.pricing_cutoff_at is not None:
                raise DomainValidationError("KRW proposal에는 FX 근거를 넣지 않습니다.")
        else:
            if self.pricing_fx_rate is None or self.pricing_cutoff_at is None:
                raise DomainValidationError("외화 proposal에는 검증된 FX 근거가 필요합니다.")
            cutoff = _utc(self.pricing_cutoff_at, field_name="proposal FX cutoff")
            object.__setattr__(self, "pricing_cutoff_at", cutoff)
            if (
                not self.pricing_fx_rate.verified
                or self.pricing_fx_rate.currency is not self.amounts.cash_currency
                or self.pricing_fx_rate.krw_per_unit != self.amounts.fx_krw_per_unit
                or self.pricing_fx_rate.as_of > cutoff
            ):
                raise DomainValidationError("proposal FX 근거와 판정액 FX가 일치해야 합니다.")
        if not isinstance(self.strategy_id, PaperStrategyKind):
            raise DomainValidationError("proposal 전략 식별자가 올바르지 않습니다.")
        expected_hash = trade_proposal_content_hash(
            proposal_id=self.proposal_id,
            version=self.version,
            candidate_set_id=self.candidate_set_id,
            candidate_rank=self.candidate_rank,
            trade_purpose_id=self.trade_purpose_id,
            purpose_version=self.purpose_version,
            stable_root_trade_purpose_id=self.stable_root_trade_purpose_id,
            strategy_id=self.strategy_id,
            trade_intent_id=self.trade_intent_id,
            instrument_id=self.instrument_id,
            side=self.side,
            quantity=self.quantity,
            amounts=self.amounts,
            us_session_date=self.us_session_date,
            kst_intent_date=self.kst_intent_date,
            expires_at=self.expires_at,
            pricing_fx_rate=self.pricing_fx_rate,
            pricing_cutoff_at=self.pricing_cutoff_at,
            price_condition=self.price_condition,
        )
        if self.proposal_hash != expected_hash:
            raise DomainValidationError("TradeProposal 불변 hash가 내용과 일치하지 않습니다.")

    @property
    def approval_group_key(self) -> tuple[str, str, Side, date]:
        return (
            self.stable_root_trade_purpose_id,
            self.instrument_id,
            self.side,
            self.us_session_date,
        )


@dataclass(frozen=True)
class ApprovalTarget:
    target_id: str
    target_version: str
    target_hash: str

    def __post_init__(self) -> None:
        for field_name in ("target_id", "target_version", "target_hash"):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))


@dataclass(frozen=True)
class NewRootPurposeTarget:
    prior_root_id: str
    proposed_root_id: str
    trade_purpose_id: str
    purpose_version: str
    purpose_version_hash: str
    purpose_change_evidence_ref: str
    purpose_change_evidence_hash: str
    intended_effect_diff: str
    split_check_result: SplitCheckResult
    split_check_input_hash: str
    policy_version: str
    checked_at: datetime
    cutoff_at: datetime

    def __post_init__(self) -> None:
        _require_enum(
            self.split_check_result,
            SplitCheckResult,
            field_name="new root split check result",
        )
        for field_name in (
            "prior_root_id",
            "proposed_root_id",
            "trade_purpose_id",
            "purpose_version",
            "purpose_version_hash",
            "purpose_change_evidence_ref",
            "purpose_change_evidence_hash",
            "intended_effect_diff",
            "split_check_input_hash",
            "policy_version",
        ):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "checked_at", _utc(self.checked_at, field_name="root check time"))
        object.__setattr__(self, "cutoff_at", _utc(self.cutoff_at, field_name="root check cutoff"))
        if self.checked_at < self.cutoff_at:
            raise DomainValidationError("새 root 검사시각은 근거 cutoff보다 이를 수 없습니다.")
        if self.prior_root_id == self.proposed_root_id:
            raise DomainValidationError("새 root ID는 기존 root와 달라야 합니다.")


@dataclass(frozen=True)
class Approval:
    approval_id: str
    approval_type: ApprovalType
    target: ApprovalTarget
    approved_by_user_id: str
    approved_at: datetime
    expires_at: datetime
    audit_ref: str
    state: ApprovalState = ApprovalState.APPROVED
    new_root_target: NewRootPurposeTarget | None = None
    bound_to_ref: str | None = None
    state_changed_at: datetime | None = None
    invalidation_reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.target) is not ApprovalTarget:
            raise DomainValidationError("승인 대상 형식이 올바르지 않습니다.")
        if self.new_root_target is not None and type(self.new_root_target) is not NewRootPurposeTarget:
            raise DomainValidationError("새 root 승인 대상 형식이 올바르지 않습니다.")
        _require_enum(self.approval_type, ApprovalType, field_name="approval type")
        _require_enum(self.state, ApprovalState, field_name="approval state")
        for field_name in ("approval_id", "approved_by_user_id", "audit_ref"):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        approved = _utc(self.approved_at, field_name="approved_at")
        expires = _utc(self.expires_at, field_name="expires_at")
        object.__setattr__(self, "approved_at", approved)
        object.__setattr__(self, "expires_at", expires)
        if self.bound_to_ref is not None:
            object.__setattr__(
                self,
                "bound_to_ref",
                nonempty(self.bound_to_ref, field_name="approval bound ref"),
            )
        if self.state_changed_at is not None:
            changed = _utc(
                self.state_changed_at,
                field_name="approval state_changed_at",
            )
            if changed < approved:
                raise DomainValidationError("승인 상태 변경시각은 승인시각보다 이를 수 없습니다.")
            object.__setattr__(self, "state_changed_at", changed)
        if self.invalidation_reason is not None:
            object.__setattr__(
                self,
                "invalidation_reason",
                nonempty(
                    self.invalidation_reason,
                    field_name="approval invalidation reason",
                ),
            )
        if expires != approved + APPROVAL_LIFETIME:
            raise DomainValidationError("승인 만료시각은 승인시각부터 정확히 24시간이어야 합니다.")
        if self.approval_type is ApprovalType.NEW_ROOT_PURPOSE:
            if self.new_root_target is None:
                raise DomainValidationError("NEW_ROOT_PURPOSE 승인에는 전용 target이 필요합니다.")
            expected = ApprovalTarget(
                self.new_root_target.trade_purpose_id,
                self.new_root_target.purpose_version,
                self.new_root_target.purpose_version_hash,
            )
            if self.target != expected:
                raise DomainValidationError("새 root 승인 target version/hash가 일치하지 않습니다.")
        elif self.new_root_target is not None:
            raise DomainValidationError("다른 승인 유형에는 새 root target을 넣을 수 없습니다.")


@dataclass(frozen=True)
class PaperExecutionPolicy:
    policy_version: str
    monthly_budget_krw: Decimal | None
    monthly_budget_month: str | None
    monthly_budget_applies_to_buy: bool | None
    monthly_budget_applies_to_sell: bool | None
    per_order_limit_krw: Decimal | None
    cost_model_confirmed: bool | None
    fx_model_confirmed: bool | None
    cost_basis_method: CostBasisMethod | None

    def __post_init__(self) -> None:
        for field_name in (
            "monthly_budget_applies_to_buy",
            "monthly_budget_applies_to_sell",
            "cost_model_confirmed",
            "fx_model_confirmed",
        ):
            _require_optional_bool(getattr(self, field_name), field_name=field_name)
        if self.cost_basis_method is not None:
            _require_enum(
                self.cost_basis_method,
                CostBasisMethod,
                field_name="cost basis method",
            )
        object.__setattr__(self, "policy_version", nonempty(self.policy_version, field_name="execution policy version"))
        if self.monthly_budget_krw is not None:
            object.__setattr__(self, "monthly_budget_krw", krw_amount(self.monthly_budget_krw, field_name="monthly PAPER budget"))
        if self.monthly_budget_month is not None:
            month = nonempty(self.monthly_budget_month, field_name="monthly budget month")
            if len(month) != 7 or month[4] != "-" or not (month[:4] + month[5:]).isdigit():
                raise DomainValidationError("월 예산 기준월은 YYYY-MM 형식이어야 합니다.")
            object.__setattr__(self, "monthly_budget_month", month)
        if self.per_order_limit_krw is not None:
            object.__setattr__(self, "per_order_limit_krw", krw_amount(self.per_order_limit_krw, field_name="per-order PAPER limit"))


@dataclass(frozen=True)
class ReservationRequest:
    reservation_id: str
    proposal_id: str
    created_at: datetime
    sell_lot_quantities: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reservation_id", nonempty(self.reservation_id, field_name="reservation_id"))
        object.__setattr__(self, "proposal_id", nonempty(self.proposal_id, field_name="proposal_id"))
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="reservation created_at"))
        requested_lots = _pair_sequence(
            self.sell_lot_quantities,
            field_name="sell lot quantities",
        )
        seen: set[str] = set()
        normalized: list[tuple[str, int]] = []
        for lot_id, quantity in requested_lots:
            lot_key = nonempty(lot_id, field_name="sell lot_id")
            if lot_key in seen:
                raise DomainValidationError("매도 예약 lot이 중복됐습니다.")
            seen.add(lot_key)
            normalized.append((lot_key, _positive_quantity(quantity, field_name="sell reserved quantity")))
        object.__setattr__(self, "sell_lot_quantities", tuple(normalized))


@dataclass(frozen=True)
class PaperReservation:
    reservation_id: str
    proposal_id: str
    side: Side
    cash_currency: Currency
    original_cash_amount: Decimal
    remaining_cash_amount: Decimal
    original_daily_gross_krw: Decimal
    remaining_daily_gross_krw: Decimal
    sell_lot_quantities: tuple[tuple[str, int], ...]
    remaining_sell_lot_quantities: tuple[tuple[str, int], ...]
    candidate_approval_id: str
    large_approval_id: str | None
    cost_basis_method: CostBasisMethod | None
    risk_snapshot_id: str
    risk_policy_version: str
    risk_context_hash: str
    execution_policy_hash: str
    budget_version_id: str
    strategy_id: PaperStrategyKind
    original_strategy_budget_krw: Decimal
    remaining_strategy_budget_krw: Decimal
    original_monthly_budget_krw: Decimal
    remaining_monthly_budget_krw: Decimal
    created_at: datetime
    state: ReservationState = ReservationState.ACTIVE
    released_at: datetime | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.side, Side, field_name="reservation side")
        _require_enum(self.cash_currency, Currency, field_name="reservation currency")
        _require_enum(self.strategy_id, PaperStrategyKind, field_name="reservation strategy")
        _require_enum(self.state, ReservationState, field_name="reservation state")
        if self.cost_basis_method is not None:
            _require_enum(
                self.cost_basis_method,
                CostBasisMethod,
                field_name="reservation cost basis method",
            )
        for field_name in (
            "reservation_id",
            "proposal_id",
            "candidate_approval_id",
            "risk_snapshot_id",
            "risk_policy_version",
            "risk_context_hash",
            "execution_policy_hash",
            "budget_version_id",
        ):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "created_at", _utc(self.created_at, field_name="reservation created_at"))
        for field_name in ("original_cash_amount", "remaining_cash_amount"):
            object.__setattr__(self, field_name, _positive_decimal(getattr(self, field_name), field_name=field_name, allow_zero=True))
        for field_name in ("original_daily_gross_krw", "remaining_daily_gross_krw"):
            object.__setattr__(self, field_name, krw_amount(getattr(self, field_name), field_name=field_name))
        for field_name in ("original_strategy_budget_krw", "remaining_strategy_budget_krw"):
            object.__setattr__(self, field_name, krw_amount(getattr(self, field_name), field_name=field_name))
        for field_name in ("original_monthly_budget_krw", "remaining_monthly_budget_krw"):
            object.__setattr__(self, field_name, krw_amount(getattr(self, field_name), field_name=field_name))
        if self.remaining_cash_amount > self.original_cash_amount or self.remaining_daily_gross_krw > self.original_daily_gross_krw:
            raise DomainValidationError("예약 잔여액이 최초 예약액을 초과할 수 없습니다.")
        if self.remaining_strategy_budget_krw > self.original_strategy_budget_krw:
            raise DomainValidationError("전략 예산 예약 잔여액이 최초 예약액을 초과할 수 없습니다.")
        if self.remaining_monthly_budget_krw > self.original_monthly_budget_krw:
            raise DomainValidationError("월 예산 예약 잔여액이 최초 예약액을 초과할 수 없습니다.")
        if not isinstance(self.strategy_id, PaperStrategyKind):
            raise DomainValidationError("예약 전략 식별자가 올바르지 않습니다.")
        original_items = _pair_sequence(
            self.sell_lot_quantities,
            field_name="reserved lot quantities",
        )
        remaining_items = _pair_sequence(
            self.remaining_sell_lot_quantities,
            field_name="remaining reserved lot quantities",
        )
        original: dict[str, int] = {}
        for lot_id, quantity in original_items:
            key = nonempty(lot_id, field_name="reserved lot_id")
            if key in original:
                raise DomainValidationError("예약 lot이 중복됐습니다.")
            original[key] = _positive_quantity(quantity, field_name="reserved lot quantity")
        remaining: dict[str, int] = {}
        for lot_id, quantity in remaining_items:
            key = nonempty(lot_id, field_name="remaining reserved lot_id")
            if key in remaining:
                raise DomainValidationError("잔여 예약 lot이 중복됐습니다.")
            remaining[key] = _positive_quantity(quantity, field_name="remaining reserved lot quantity")
            if key not in original or quantity > original[key]:
                raise DomainValidationError("잔여 매도 예약수량이 최초 예약수량을 초과합니다.")
        object.__setattr__(self, "sell_lot_quantities", tuple(original.items()))
        object.__setattr__(
            self,
            "remaining_sell_lot_quantities",
            tuple(remaining.items()),
        )
        if self.side is Side.BUY and (original or remaining):
            raise DomainValidationError("BUY 예약에는 매도 lot 수량이 없어야 합니다.")
        if self.side is Side.SELL and self.original_cash_amount != 0:
            raise DomainValidationError("SELL 예약은 매수 현금을 예약하지 않습니다.")


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    proposal_id: str
    reservation_id: str
    quantity: int
    amounts: TradeAmounts
    filled_at: datetime
    observed_price: VerifiedPrice | None = None
    observation_cutoff_at: datetime | None = None
    observed_fx_rate: VerifiedFxRate | None = None
    virtual: bool = True
    realized_pnl_trade_currency: Decimal | None = None

    def __post_init__(self) -> None:
        if type(self.amounts) is not TradeAmounts:
            raise DomainValidationError("PAPER fill 금액 형식이 올바르지 않습니다.")
        if type(self.observed_price) is not VerifiedPrice:
            raise DomainValidationError("PAPER fill 관측 가격 형식이 올바르지 않습니다.")
        if self.observed_fx_rate is not None and type(self.observed_fx_rate) is not VerifiedFxRate:
            raise DomainValidationError("PAPER fill FX 근거 형식이 올바르지 않습니다.")
        _require_bool(self.virtual, field_name="fill virtual")
        for field_name in ("fill_id", "proposal_id", "reservation_id"):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "quantity", _positive_quantity(self.quantity))
        object.__setattr__(self, "filled_at", _utc(self.filled_at, field_name="filled_at"))
        if self.observed_price is None or self.observation_cutoff_at is None:
            raise DomainValidationError("PAPER fill에는 관측 가격과 cutoff가 필요합니다.")
        object.__setattr__(
            self,
            "observation_cutoff_at",
            _utc(self.observation_cutoff_at, field_name="fill observation cutoff"),
        )
        if not self.virtual:
            raise DomainValidationError("P1 PAPER fill은 반드시 내부 가상 체결이어야 합니다.")
        if self.realized_pnl_trade_currency is not None:
            object.__setattr__(
                self,
                "realized_pnl_trade_currency",
                exact_decimal(self.realized_pnl_trade_currency, field_name="realized P/L"),
            )


@dataclass(frozen=True)
class AuditEvent:
    audit_id: str
    event_type: str
    related_ref: str
    recorded_at: datetime
    actor_ref: str | None = None
    evidence_ref: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("audit_id", "event_type", "related_ref"):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, field_name="audit recorded_at"))
        for field_name in ("actor_ref", "evidence_ref", "reason_code"):
            if getattr(self, field_name) is not None:
                object.__setattr__(
                    self,
                    field_name,
                    nonempty(getattr(self, field_name), field_name=field_name),
                )


@dataclass(frozen=True)
class ExecutionAttempt:
    event_id: str
    proposal_id: str
    attempted_at: datetime
    result: AttemptResult = AttemptResult.UNKNOWN

    def __post_init__(self) -> None:
        _require_enum(self.result, AttemptResult, field_name="attempt result")
        object.__setattr__(self, "event_id", nonempty(self.event_id, field_name="attempt event_id"))
        object.__setattr__(self, "proposal_id", nonempty(self.proposal_id, field_name="attempt proposal_id"))
        object.__setattr__(self, "attempted_at", _utc(self.attempted_at, field_name="attempted_at"))


@dataclass(frozen=True)
class ExecutionRun:
    execution_run_id: str
    portfolio_id: str
    us_session_date: date
    included_proposals: tuple[tuple[str, str], ...]
    included_hash: str
    window_start: datetime
    window_end: datetime
    state: RunState
    attempts: tuple[ExecutionAttempt, ...] = ()
    started_at: datetime | None = None
    completion_kind: CompletionKind = CompletionKind.NONE
    terminal_reason: str | None = None
    ended_at: datetime | None = None
    state_reason_code: str | None = None
    state_evidence_ref: str | None = None
    state_changed_at: datetime | None = None

    def __post_init__(self) -> None:
        included = _pair_sequence(
            self.included_proposals,
            field_name="execution run proposals",
        )
        attempts = _sequence(self.attempts, field_name="execution run attempts")
        if any(
            len(item) != 2
            or not all(isinstance(value, str) for value in item)
            for item in included
        ):
            raise DomainValidationError("ExecutionRun proposal 목록 형식이 올바르지 않습니다.")
        included = tuple(
            (
                nonempty(item[0], field_name="run proposal id"),
                nonempty(item[1], field_name="run proposal version"),
            )
            for item in included
        )
        if any(type(attempt) is not ExecutionAttempt for attempt in attempts):
            raise DomainValidationError("ExecutionRun attempt 목록 형식이 올바르지 않습니다.")
        object.__setattr__(self, "included_proposals", included)
        object.__setattr__(self, "attempts", attempts)
        _require_date(self.us_session_date, field_name="run US session date")
        _require_enum(self.state, RunState, field_name="execution run state")
        _require_enum(
            self.completion_kind,
            CompletionKind,
            field_name="execution completion kind",
        )
        for field_name in ("execution_run_id", "portfolio_id", "included_hash"):
            object.__setattr__(self, field_name, nonempty(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "window_start", _utc(self.window_start, field_name="run window_start"))
        object.__setattr__(self, "window_end", _utc(self.window_end, field_name="run window_end"))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", _utc(self.ended_at, field_name="run ended_at"))
        if self.started_at is not None:
            object.__setattr__(self, "started_at", _utc(self.started_at, field_name="run started_at"))
        if self.state_changed_at is not None:
            object.__setattr__(
                self,
                "state_changed_at",
                _utc(self.state_changed_at, field_name="run state_changed_at"),
            )
        if self.state_reason_code is not None:
            object.__setattr__(
                self,
                "state_reason_code",
                nonempty(self.state_reason_code, field_name="run state reason"),
            )
        if self.state_evidence_ref is not None:
            object.__setattr__(
                self,
                "state_evidence_ref",
                nonempty(self.state_evidence_ref, field_name="run state evidence"),
            )
        if self.window_start >= self.window_end:
            raise DomainValidationError("실행창 시작은 종료보다 앞서야 합니다.")
        if not self.included_proposals:
            raise DomainValidationError("ExecutionRun에는 proposal이 하나 이상 필요합니다.")
        if len(set(self.included_proposals)) != len(self.included_proposals):
            raise DomainValidationError("ExecutionRun proposal 목록이 중복됐습니다.")
        expected_hash = canonical_hash(self.included_proposals)
        if self.included_hash != expected_hash:
            raise DomainValidationError("ExecutionRun 불변 proposal hash가 일치하지 않습니다.")
        if len({attempt.event_id for attempt in self.attempts}) != len(self.attempts):
            raise DomainValidationError("ExecutionRun attempt event ID가 중복됐습니다.")
        if self.attempts and self.started_at is None:
            raise DomainValidationError("실행 시도가 있는 run에는 실제 시작시각이 필요합니다.")
        if self.started_at is not None and (
            self.started_at < self.window_start or self.started_at >= self.window_end
        ):
            raise DomainValidationError("실제 실행 시작시각은 실행창 안이어야 합니다.")
        if (
            self.ended_at is not None
            and self.started_at is not None
            and self.ended_at < self.started_at
        ):
            raise DomainValidationError("run 종료시각은 실제 시작시각보다 이를 수 없습니다.")
        if (
            self.ended_at is not None
            and self.state_changed_at is not None
            and self.ended_at < self.state_changed_at
        ):
            raise DomainValidationError("run 종료시각은 마지막 상태 변경시각보다 이를 수 없습니다.")
        if self.attempts and self.started_at != min(item.attempted_at for item in self.attempts):
            raise DomainValidationError("run 실제 시작시각은 첫 실행 시도시각이어야 합니다.")
        if self.state_changed_at is not None and self.state_changed_at < self.window_start:
            raise DomainValidationError("run 상태 변경시각은 실행창 시작보다 이를 수 없습니다.")
        if self.state_changed_at is not None and self.attempts and self.state_changed_at < max(
            item.attempted_at for item in self.attempts
        ):
            raise DomainValidationError("run 상태 변경시각은 실행 시도보다 이를 수 없습니다.")
        if self.state in {RunState.TEMPORARILY_HELD, RunState.USER_STOPPED} and any(
            value is None
            for value in (
                self.state_reason_code,
                self.state_evidence_ref,
                self.state_changed_at,
            )
        ):
            raise DomainValidationError("보류·사용자 중지 run에는 원인·근거·시각이 필요합니다.")
        terminal_states = {RunState.MISSED_WINDOW, RunState.COMPLETED, RunState.RECONCILED}
        if self.state in terminal_states and self.ended_at is None:
            raise DomainValidationError("terminal ExecutionRun에는 종료시각이 필요합니다.")
        if self.state not in terminal_states | {RunState.RECONCILIATION_REQUIRED} and self.ended_at is not None:
            raise DomainValidationError("종료되지 않은 ExecutionRun에 종료시각을 둘 수 없습니다.")
        if self.state is RunState.MISSED_WINDOW and self.attempt_count != 0:
            raise DomainValidationError("run MISSED_WINDOW는 실행 시도 0건에만 허용됩니다.")
        if self.state in {RunState.RUNNING, RunState.RECONCILIATION_REQUIRED, RunState.COMPLETED, RunState.RECONCILED} and not self.attempted:
            raise DomainValidationError("해당 ExecutionRun 상태에는 실행 시도가 필요합니다.")
        if self.state is RunState.COMPLETED and self.completion_kind not in {CompletionKind.FULL, CompletionKind.PARTIAL}:
            raise DomainValidationError("COMPLETED run에는 FULL/PARTIAL 구분이 필요합니다.")
        if self.state is not RunState.COMPLETED and self.state is not RunState.RECONCILED and self.completion_kind is not CompletionKind.NONE:
            raise DomainValidationError("완료되지 않은 run에는 completion kind를 둘 수 없습니다.")

    @property
    def attempt_count(self) -> int:
        return len({attempt.event_id for attempt in self.attempts})

    @property
    def attempted(self) -> bool:
        return self.attempt_count > 0


@dataclass
class _PaperState:
    portfolio: PaperPortfolio | None = None
    cash: dict[Currency, Decimal] = field(default_factory=dict)
    ledger_entries: list[PaperLedgerEntry] = field(default_factory=list)
    budgets: dict[str, PaperBudgetVersion] = field(default_factory=dict)
    strategy_versions: dict[str, PaperStrategyVersion] = field(default_factory=dict)
    strategy_applications: list[StrategyApplication] = field(default_factory=list)
    lots: dict[str, PaperPositionLot] = field(default_factory=dict)
    valuations: dict[str, PaperValuationSnapshot] = field(default_factory=dict)
    daily_limits: dict[date, PaperDailyLimit] = field(default_factory=dict)
    purposes: dict[str, TradePurpose] = field(default_factory=dict)
    candidate_sets: dict[str, CandidateSet] = field(default_factory=dict)
    candidate_rank_clearances: dict[tuple[str, int], CandidateRankClearance] = field(
        default_factory=dict
    )
    proposals: dict[str, TradeProposal] = field(default_factory=dict)
    price_condition_checks: list[PriceConditionCheck] = field(default_factory=list)
    approvals: dict[str, Approval] = field(default_factory=dict)
    reservations: dict[str, PaperReservation] = field(default_factory=dict)
    fills: dict[str, PaperFill] = field(default_factory=dict)
    runs: dict[str, ExecutionRun] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)
    kst_intent_claims: dict[tuple[str, date], str] = field(default_factory=dict)
    us_session_claims: dict[tuple[str, date], str] = field(default_factory=dict)


T = TypeVar("T")


class PaperRepository(Protocol):
    """운영 DB가 아닌 순수 엔진용 transaction port."""

    def transact(self, operation: Callable[[_PaperState], T]) -> T: ...

    def snapshot(self) -> _PaperState: ...


class InMemoryPaperRepository:
    """RLock + copy-on-write의 단일 프로세스 메모리 구현."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._transaction_local = local()
        self._state = _PaperState()

    def transact(self, operation: Callable[[_PaperState], T]) -> T:
        if getattr(self._transaction_local, "active", False):
            raise DomainValidationError("중첩 PAPER memory transaction은 허용되지 않습니다.")
        with self._lock:
            self._transaction_local.active = True
            try:
                try:
                    with localcontext(_paper_decimal_context(exact=True)):
                        candidate = copy.deepcopy(self._state)
                        result = operation(candidate)
                        returned = copy.deepcopy(result)
                        committed = copy.deepcopy(candidate)
                        self._state = committed
                        return returned
                except DecimalException as exc:
                    raise DomainValidationError(
                        "PAPER 상태를 정확하게 계산할 수 없습니다."
                    ) from exc
                except TypeError:
                    raise DomainValidationError(
                        "PAPER 입력 형식이 올바르지 않습니다."
                    ) from None
            finally:
                self._transaction_local.active = False

    def snapshot(self) -> _PaperState:
        with self._lock:
            return copy.deepcopy(self._state)


def _active_reservation(state: ReservationState) -> bool:
    return state in {
        ReservationState.ACTIVE,
        ReservationState.UNKNOWN,
        ReservationState.NEEDS_RECONCILIATION,
    }


def _available_cash(state: _PaperState, currency: Currency) -> Decimal:
    posted = state.cash.get(currency, Decimal("0"))
    reserved = sum(
        (
            reservation.remaining_cash_amount
            for reservation in state.reservations.values()
            if reservation.cash_currency is currency and _active_reservation(reservation.state)
        ),
        Decimal("0"),
    )
    return posted - reserved


def _lot_reserved_quantity(state: _PaperState, lot_id: str) -> int:
    total = 0
    for reservation in state.reservations.values():
        if not _active_reservation(reservation.state):
            continue
        total += dict(reservation.remaining_sell_lot_quantities).get(lot_id, 0)
    return total


def _paper_denominator(
    valuation: PaperValuationSnapshot,
    policy: RiskPolicyInputs,
) -> tuple[Decimal | None, tuple[str, ...]]:
    if policy.denominator_basis is None:
        return None, ("PAPER_RISK_DENOMINATOR_REQUIRED",)
    if policy.denominator_basis is RiskDenominatorBasis.CURRENT_PAPER_NAV:
        if valuation.status is not GateStatus.PASS or valuation.nav_krw is None:
            return None, ("PAPER_NAV_UNAVAILABLE",)
        return valuation.nav_krw, ()
    if policy.session_opening_nav_krw is None:
        return None, ("SESSION_OPENING_PAPER_NAV_REQUIRED",)
    return policy.session_opening_nav_krw, ()


def _assess_paper_risk(
    *,
    valuation: PaperValuationSnapshot,
    classifications: Mapping[str, InstrumentClassification],
    proposed_instrument_id: str,
    proposed_buy_gross_krw: Decimal | str | int,
    policy: RiskPolicyInputs,
    active_buy_exposures_krw: Mapping[str, Decimal] | None = None,
    proposed_maximum_cost_krw: Decimal | str | int = 0,
    active_buy_maximum_costs_krw: Decimal | str | int = 0,
) -> PaperRiskAssessment:
    proposed_id = nonempty(proposed_instrument_id, field_name="proposed instrument")
    proposed = _positive_decimal(
        krw_amount(proposed_buy_gross_krw, field_name="proposed buy gross"),
        field_name="proposed buy gross",
        allow_zero=True,
    )
    proposed_cost = _positive_decimal(
        krw_amount(proposed_maximum_cost_krw, field_name="proposed maximum cost"),
        field_name="proposed maximum cost",
        allow_zero=True,
    )
    active_costs = _positive_decimal(
        krw_amount(
            active_buy_maximum_costs_krw,
            field_name="active reserved maximum costs",
        ),
        field_name="active reserved maximum costs",
        allow_zero=True,
    )
    denominator, reasons = _paper_denominator(valuation, policy)
    if (
        denominator is not None
        and policy.denominator_basis is RiskDenominatorBasis.CURRENT_PAPER_NAV
    ):
        denominator -= proposed_cost + active_costs
    missing_policy = tuple(
        code
        for confirmed, code in (
            (policy.mdd_policy_confirmed, "MDD_POLICY_INPUT_REQUIRED"),
            (policy.industry_policy_confirmed, "INDUSTRY_POLICY_INPUT_REQUIRED"),
            (policy.company_policy_confirmed, "COMPANY_POLICY_INPUT_REQUIRED"),
        )
        if confirmed is not True
    )
    if reasons or missing_policy:
        return PaperRiskAssessment(GateStatus.UNKNOWN, denominator, None, None, reasons + missing_policy)
    assert denominator is not None
    if denominator <= 0:
        return PaperRiskAssessment(GateStatus.UNKNOWN, denominator, None, None, ("PAPER_RISK_DENOMINATOR_NONPOSITIVE",))

    if any(
        item.verified and item.policy_version != policy.policy_version
        for item in classifications.values()
    ):
        return PaperRiskAssessment(
            GateStatus.UNKNOWN,
            denominator,
            None,
            None,
            ("CLASSIFICATION_POLICY_VERSION_MISMATCH",),
        )
    if any(
        item.verified
        and (
            item.instrument_type is InstrumentType.PROHIBITED
            or item.risk_role is RiskRole.INELIGIBLE
        )
        and valuation_value > 0
        for instrument_id, valuation_value in valuation.instrument_values_krw
        if (item := classifications.get(instrument_id)) is not None
    ):
        return PaperRiskAssessment(
            GateStatus.BLOCKED,
            denominator,
            None,
            None,
            ("EXISTING_PROHIBITED_PRODUCT_EXPOSURE",),
        )

    values = dict(valuation.instrument_values_krw)
    for instrument_id, raw_value in (active_buy_exposures_krw or {}).items():
        key = nonempty(instrument_id, field_name="active exposure instrument")
        value = krw_amount(raw_value, field_name="active reserved buy exposure")
        values[key] = values.get(key, Decimal("0")) + value
    current_value = values.get(proposed_id, Decimal("0"))
    classification = classifications.get(proposed_id)
    if classification is None or not classification.verified:
        satellite_value = sum(
            value
            for instrument_id, value in values.items()
            if (
                instrument_id not in classifications
                or not classifications[instrument_id].verified
                or classifications[instrument_id].risk_role
                in {RiskRole.SATELLITE, RiskRole.UNCLASSIFIED_ASSUMED_SATELLITE}
            )
        )
        return PaperRiskAssessment(
            GateStatus.BLOCKED,
            denominator,
            (current_value + proposed) / denominator,
            (satellite_value + proposed) / denominator,
            ("UNCLASSIFIED_ASSUMED_SATELLITE_BUY_BLOCKED",),
        )
    if classification.instrument_type is InstrumentType.PROHIBITED or classification.risk_role is RiskRole.INELIGIBLE:
        return PaperRiskAssessment(GateStatus.BLOCKED, denominator, None, None, ("PROHIBITED_PRODUCT",))

    cap = {
        InstrumentType.COMMON_STOCK: Decimal("0.05"),
        InstrumentType.ETF: Decimal("0.40"),
        InstrumentType.BROAD_MARKET_ETF: Decimal("0.50"),
        InstrumentType.UNCLASSIFIED: Decimal("0"),
    }[classification.instrument_type]
    instrument_ratio = (current_value + proposed) / denominator
    reason_list: list[str] = []
    unknown_reason_list: list[str] = []
    if classification.instrument_type is InstrumentType.UNCLASSIFIED:
        reason_list.append("UNCLASSIFIED_ASSUMED_SATELLITE_BUY_BLOCKED")
    elif current_value + proposed > denominator * cap:
        reason_list.append("INSTRUMENT_CONCENTRATION_LIMIT_EXCEEDED")

    satellite_value = Decimal("0")
    counted: set[str] = set()
    for instrument_id, value in values.items():
        if instrument_id in counted:
            continue
        counted.add(instrument_id)
        item = classifications.get(instrument_id)
        if item is None or not item.verified or item.risk_role in {
            RiskRole.SATELLITE,
            RiskRole.UNCLASSIFIED_ASSUMED_SATELLITE,
        }:
            satellite_value += value
    if classification.risk_role in {RiskRole.SATELLITE, RiskRole.UNCLASSIFIED_ASSUMED_SATELLITE}:
        satellite_value += proposed
        satellite_gate = policy.satellite_loss_gate
        if (
            policy.satellite_loss_policy_confirmed is not True
            or satellite_gate is None
            or satellite_gate.policy_version != policy.policy_version
            or satellite_gate.as_of > valuation.cutoff_at
        ):
            unknown_reason_list.append("SATELLITE_LOSS_POLICY_INPUT_REQUIRED")
        elif not satellite_gate.decision.permits_execution:
            gate_reasons = satellite_gate.decision.reason_codes or (
                "SATELLITE_LOSS_GATE_NOT_PASS",
            )
            if satellite_gate.decision.status is GateStatus.UNKNOWN:
                unknown_reason_list.extend(gate_reasons)
            else:
                reason_list.extend(gate_reasons)
    satellite_ratio = satellite_value / denominator
    if (
        classification.risk_role
        in {RiskRole.SATELLITE, RiskRole.UNCLASSIFIED_ASSUMED_SATELLITE}
        and satellite_value > denominator * Decimal("0.10")
    ):
        reason_list.append("SATELLITE_LIMIT_EXCEEDED")
    return PaperRiskAssessment(
        (
            GateStatus.UNKNOWN
            if unknown_reason_list
            else GateStatus.BLOCKED
            if reason_list
            else GateStatus.PASS
        ),
        denominator,
        instrument_ratio,
        satellite_ratio,
        tuple(reason_list + unknown_reason_list),
    )


def assess_paper_risk(
    *,
    valuation: PaperValuationSnapshot,
    classifications: Mapping[str, InstrumentClassification],
    proposed_instrument_id: str,
    proposed_buy_gross_krw: Decimal | str | int,
    policy: RiskPolicyInputs,
    active_buy_exposures_krw: Mapping[str, Decimal] | None = None,
    proposed_maximum_cost_krw: Decimal | str | int = 0,
    active_buy_maximum_costs_krw: Decimal | str | int = 0,
) -> PaperRiskAssessment:
    """전역 Decimal context와 무관한 PAPER 위험 평가."""

    if type(valuation) is not PaperValuationSnapshot:
        raise DomainValidationError("PAPER valuation 형식이 올바르지 않습니다.")
    if type(policy) is not RiskPolicyInputs:
        raise DomainValidationError("PAPER risk policy 형식이 올바르지 않습니다.")
    normalized_classifications = _normalize_classification_mapping(classifications)
    normalized_exposures = (
        None
        if active_buy_exposures_krw is None
        else _normalize_exposure_mapping(active_buy_exposures_krw)
    )
    try:
        with localcontext(_paper_decimal_context()):
            return _assess_paper_risk(
                valuation=valuation,
                classifications=normalized_classifications,
                proposed_instrument_id=proposed_instrument_id,
                proposed_buy_gross_krw=proposed_buy_gross_krw,
                policy=policy,
                active_buy_exposures_krw=normalized_exposures,
                proposed_maximum_cost_krw=proposed_maximum_cost_krw,
                active_buy_maximum_costs_krw=active_buy_maximum_costs_krw,
            )
    except DecimalException as exc:
        raise DomainValidationError("PAPER 위험 한도를 안전하게 계산할 수 없습니다.") from exc


class PaperEngine:
    """명시적 입력만 사용하는 PAPER 원장·승인·상태기계 facade."""

    def __init__(self, repository: PaperRepository) -> None:
        self._repository = repository

    @property
    def snapshot(self) -> _PaperState:
        return self._repository.snapshot()

    def create_portfolio(
        self,
        *,
        portfolio_id: str,
        owner_ref: str,
        created_at: datetime,
        genesis_entry_id: str,
    ) -> PaperPortfolio:
        portfolio = PaperPortfolio(portfolio_id, owner_ref, created_at)

        def operation(state: _PaperState) -> PaperPortfolio:
            if state.portfolio is not None:
                raise PaperBlockedError("PAPER_PORTFOLIO_ALREADY_EXISTS")
            state.portfolio = portfolio
            state.cash[Currency.KRW] = INITIAL_PAPER_CASH_KRW
            state.cash[Currency.USD] = Decimal("0")
            state.ledger_entries.append(
                PaperLedgerEntry(
                    genesis_entry_id,
                    LedgerEventType.GENESIS,
                    portfolio.portfolio_id,
                    portfolio.created_at,
                    portfolio.created_at,
                    INITIAL_PAPER_CASH_KRW,
                    Currency.KRW,
                    source_ref="INTERNAL_PAPER_GENESIS",
                )
            )
            return portfolio

        return self._repository.transact(operation)

    def save_budget_version(self, budget: PaperBudgetVersion) -> None:
        if type(budget) is not PaperBudgetVersion:
            raise DomainValidationError("PAPER 예산 버전 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> None:
            portfolio = self._require_portfolio(state)
            if budget.approved_at < portfolio.created_at:
                raise PaperBlockedError("BUDGET_VERSION_BEFORE_PORTFOLIO")
            if budget.version_id in state.budgets:
                raise PaperBlockedError("BUDGET_VERSION_IMMUTABLE")
            if budget.total_krw > INITIAL_PAPER_CASH_KRW:
                raise PaperBlockedError("STRATEGY_BUDGET_EXCEEDS_PAPER_GENESIS")
            state.budgets[budget.version_id] = budget

        self._repository.transact(operation)

    def save_strategy_draft(self, version: PaperStrategyVersion) -> None:
        if type(version) is not PaperStrategyVersion:
            raise DomainValidationError("PAPER 전략 버전 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> None:
            portfolio = self._require_portfolio(state)
            if version.created_at < portfolio.created_at:
                raise PaperBlockedError("STRATEGY_VERSION_BEFORE_PORTFOLIO")
            if version.version_id in state.strategy_versions:
                raise PaperBlockedError("STRATEGY_VERSION_IMMUTABLE")
            if version.prior_version_id is not None and version.prior_version_id not in state.strategy_versions:
                raise PaperBlockedError("PRIOR_STRATEGY_VERSION_NOT_FOUND")
            if version.prior_version_id is not None:
                prior = state.strategy_versions[version.prior_version_id]
                if version.created_at < prior.created_at:
                    raise PaperBlockedError("STRATEGY_VERSION_TIME_ROLLBACK")
            state.strategy_versions[version.version_id] = version

        self._repository.transact(operation)

    def apply_strategy_version(self, application: StrategyApplication) -> None:
        if type(application) is not StrategyApplication:
            raise DomainValidationError("PAPER 전략 적용 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> None:
            portfolio = self._require_portfolio(state)
            if application.applied_at < portfolio.created_at:
                raise PaperBlockedError("STRATEGY_APPLICATION_BEFORE_PORTFOLIO")
            version = state.strategy_versions.get(application.strategy_version_id)
            if version is None:
                raise PaperBlockedError("STRATEGY_VERSION_NOT_FOUND")
            if application.applied_at < version.created_at:
                raise PaperBlockedError("STRATEGY_APPLICATION_BEFORE_VERSION")
            if any(item.application_id == application.application_id for item in state.strategy_applications):
                raise PaperBlockedError("STRATEGY_APPLICATION_IMMUTABLE")
            if state.strategy_applications:
                last = state.strategy_applications[-1]
                if version.version_id == last.strategy_version_id:
                    raise PaperBlockedError("STRATEGY_VERSION_ALREADY_APPLIED")
                if version.prior_version_id != last.strategy_version_id:
                    raise PaperBlockedError("STALE_STRATEGY_VERSION")
                if application.applied_at < last.applied_at + STRATEGY_CHANGE_INTERVAL:
                    raise PaperBlockedError("STRATEGY_CHANGE_INTERVAL_NOT_ELAPSED")
            elif version.prior_version_id is not None:
                raise PaperBlockedError("INITIAL_STRATEGY_VERSION_MUST_BE_ROOT")
            state.strategy_applications.append(application)

        self._repository.transact(operation)

    def record_dividend(
        self,
        *,
        entry_id: str,
        strategy_id: PaperStrategyKind,
        instrument_id: str,
        amount: Decimal | str | int,
        currency: Currency,
        occurred_at: datetime,
        recorded_at: datetime,
        source_ref: str,
    ) -> PaperLedgerEntry:
        _require_enum(strategy_id, PaperStrategyKind, field_name="dividend strategy")
        _require_enum(currency, Currency, field_name="dividend currency")
        instrument = nonempty(instrument_id, field_name="dividend instrument")
        dividend = _positive_decimal(amount, field_name="dividend amount")
        normalized_occurred = _utc(occurred_at, field_name="dividend occurred_at")
        normalized_recorded = _utc(recorded_at, field_name="dividend recorded_at")

        def operation(state: _PaperState) -> PaperLedgerEntry:
            portfolio = self._require_portfolio(state)
            if any(item.entry_id == entry_id for item in state.ledger_entries):
                raise PaperBlockedError("LEDGER_ENTRY_ID_DUPLICATE")
            if normalized_occurred < portfolio.created_at:
                raise PaperBlockedError("LEDGER_EVENT_BEFORE_PORTFOLIO")
            if not any(
                lot.instrument_id == instrument
                and lot.strategy_id == strategy_id.value
                and lot.currency is currency
                and lot.remaining_quantity > 0
                and lot.acquired_at <= normalized_occurred
                for lot in state.lots.values()
            ):
                raise PaperBlockedError("DIVIDEND_POSITION_NOT_FOUND")
            entry = PaperLedgerEntry(
                entry_id,
                LedgerEventType.DIVIDEND,
                portfolio.portfolio_id,
                normalized_occurred,
                normalized_recorded,
                dividend,
                currency,
                strategy_id=strategy_id.value,
                instrument_id=instrument,
                source_ref=source_ref,
            )
            state.cash[currency] = state.cash.get(currency, Decimal("0")) + dividend
            state.ledger_entries.append(entry)
            return entry

        return self._repository.transact(operation)

    def correct_ledger_entry(
        self,
        *,
        correction_entry_id: str,
        corrects_entry_id: str,
        amount: Decimal | str | int,
        occurred_at: datetime,
        recorded_at: datetime,
        source_ref: str,
    ) -> PaperLedgerEntry:
        normalized_occurred = _utc(
            occurred_at,
            field_name="correction occurred_at",
        )
        normalized_recorded = _utc(
            recorded_at,
            field_name="correction recorded_at",
        )

        def operation(state: _PaperState) -> PaperLedgerEntry:
            portfolio = self._require_portfolio(state)
            if any(item.entry_id == correction_entry_id for item in state.ledger_entries):
                raise PaperBlockedError("LEDGER_ENTRY_ID_DUPLICATE")
            corrected = next(
                (item for item in state.ledger_entries if item.entry_id == corrects_entry_id),
                None,
            )
            if corrected is None:
                raise PaperBlockedError("CORRECTED_ENTRY_NOT_FOUND")
            if corrected.event_type is LedgerEventType.GENESIS:
                raise PaperBlockedError("GENESIS_CORRECTION_FORBIDDEN")
            if corrected.event_type is not LedgerEventType.DIVIDEND:
                raise PaperBlockedError("LEDGER_CORRECTION_REQUIRES_SPECIALIZED_RECONCILIATION")
            if (
                normalized_occurred < corrected.occurred_at
                or normalized_recorded < corrected.recorded_at
            ):
                raise PaperBlockedError("LEDGER_CORRECTION_TIME_INVALID")
            entry = PaperLedgerEntry(
                correction_entry_id,
                LedgerEventType.CORRECTION,
                portfolio.portfolio_id,
                normalized_occurred,
                normalized_recorded,
                exact_decimal(amount, field_name="correction amount"),
                corrected.currency,
                source_ref=source_ref,
                corrects_entry_id=corrects_entry_id,
            )
            resulting_cash = state.cash.get(corrected.currency, Decimal("0")) + entry.amount
            active_reserved = state.cash.get(corrected.currency, Decimal("0")) - _available_cash(
                state,
                corrected.currency,
            )
            if resulting_cash < active_reserved:
                raise PaperBlockedError("CORRECTION_WOULD_MAKE_AVAILABLE_CASH_NEGATIVE")
            state.cash[corrected.currency] = resulting_cash
            state.ledger_entries.append(entry)
            return entry

        return self._repository.transact(operation)

    def record_fx(
        self,
        *,
        event_id: str,
        from_currency: Currency,
        from_amount: Decimal | str | int,
        to_currency: Currency,
        to_amount: Decimal | str | int,
        occurred_at: datetime,
        fx_rate: VerifiedFxRate,
        cutoff_at: datetime,
        cost_krw: Decimal | str | int,
        cost_model_confirmed: bool,
    ) -> None:
        if type(fx_rate) is not VerifiedFxRate:
            raise DomainValidationError("PAPER FX 근거 형식이 올바르지 않습니다.")
        _require_enum(from_currency, Currency, field_name="FX source currency")
        _require_enum(to_currency, Currency, field_name="FX destination currency")
        source_amount = _positive_decimal(from_amount, field_name="FX source amount")
        destination_amount = _positive_decimal(to_amount, field_name="FX destination amount")
        normalized_cutoff = _utc(cutoff_at, field_name="FX cutoff")
        normalized_occurred = _utc(occurred_at, field_name="FX occurred_at")
        cost = krw_amount(cost_krw, field_name="FX cost KRW")
        if {from_currency, to_currency} != {Currency.KRW, Currency.USD}:
            raise DomainValidationError("P1 FX는 KRW와 USD 사이의 명시적 환전만 지원합니다.")
        if cost_model_confirmed is not True:
            raise PaperBlockedError("FX_COST_POLICY_INPUT_REQUIRED")
        if (
            not fx_rate.verified
            or fx_rate.currency is not Currency.USD
            or fx_rate.as_of > normalized_cutoff
            or fx_rate.as_of > normalized_occurred
        ):
            raise PaperBlockedError("VERIFIED_FX_RATE_REQUIRED")
        try:
            with localcontext(_paper_decimal_context(exact=True)):
                if from_currency is Currency.KRW:
                    expected = destination_amount * fx_rate.krw_per_unit + cost
                    if source_amount != expected:
                        raise PaperBlockedError("FX_AMOUNT_RATE_MISMATCH")
                else:
                    expected = source_amount * fx_rate.krw_per_unit - cost
                    if destination_amount != expected or expected < 0:
                        raise PaperBlockedError("FX_AMOUNT_RATE_MISMATCH")
        except DecimalException as exc:
            raise DomainValidationError("PAPER 환전 금액을 정확하게 계산할 수 없습니다.") from exc

        def operation(state: _PaperState) -> None:
            portfolio = self._require_portfolio(state)
            if normalized_occurred < portfolio.created_at:
                raise PaperBlockedError("LEDGER_EVENT_BEFORE_PORTFOLIO")
            if any(
                entry.entry_id
                in {f"{event_id}:debit", f"{event_id}:credit", f"{event_id}:fee"}
                for entry in state.ledger_entries
            ):
                raise PaperBlockedError("LEDGER_ENTRY_ID_DUPLICATE")
            if _available_cash(state, from_currency) < source_amount:
                raise PaperBlockedError("INSUFFICIENT_AVAILABLE_CASH")
            state.cash[from_currency] -= source_amount
            state.cash[to_currency] = state.cash.get(to_currency, Decimal("0")) + destination_amount
            debit_amount = (
                -(source_amount - cost)
                if from_currency is Currency.KRW
                else -source_amount
            )
            credit_amount = (
                destination_amount + cost
                if to_currency is Currency.KRW
                else destination_amount
            )
            entries = [
                    PaperLedgerEntry(
                    f"{event_id}:debit",
                    LedgerEventType.FX,
                    portfolio.portfolio_id,
                    normalized_occurred,
                    normalized_occurred,
                    debit_amount,
                    from_currency,
                    source_ref="EXPLICIT_PAPER_FX",
                    ),
                    PaperLedgerEntry(
                        f"{event_id}:credit",
                        LedgerEventType.FX,
                        portfolio.portfolio_id,
                        normalized_occurred,
                        normalized_occurred,
                        credit_amount,
                        to_currency,
                        source_ref="EXPLICIT_PAPER_FX",
                    ),
            ]
            if cost:
                entries.append(
                    PaperLedgerEntry(
                        f"{event_id}:fee",
                        LedgerEventType.FEE,
                        portfolio.portfolio_id,
                        normalized_occurred,
                        normalized_occurred,
                        -cost,
                        Currency.KRW,
                        source_ref="EXPLICIT_PAPER_FX_COST",
                    )
                )
            state.ledger_entries.extend(entries)

        self._repository.transact(operation)

    def value_portfolio(
        self,
        *,
        snapshot_id: str,
        as_of: datetime,
        cutoff_at: datetime,
        prices: Mapping[str, VerifiedPrice],
        fx_rates: Mapping[Currency, VerifiedFxRate],
    ) -> PaperValuationSnapshot:
        normalized_prices = _normalize_price_mapping(prices)
        normalized_fx_rates = _normalize_fx_mapping(fx_rates)
        try:
            return self._value_portfolio_exact(
                snapshot_id=snapshot_id,
                as_of=as_of,
                cutoff_at=cutoff_at,
                prices=normalized_prices,
                fx_rates=normalized_fx_rates,
            )
        except DecimalException as exc:
            raise DomainValidationError(
                "PAPER 자산평가를 정확하게 계산할 수 없습니다."
            ) from exc

    def _value_portfolio_exact(
        self,
        *,
        snapshot_id: str,
        as_of: datetime,
        cutoff_at: datetime,
        prices: Mapping[str, VerifiedPrice],
        fx_rates: Mapping[Currency, VerifiedFxRate],
    ) -> PaperValuationSnapshot:
        state = self._repository.snapshot()
        source_hash = canonical_hash((state.cash, state.lots))
        portfolio = self._require_portfolio(state)
        normalized_as_of = _utc(as_of, field_name="valuation as_of")
        normalized_cutoff = _utc(cutoff_at, field_name="valuation cutoff")
        material_event_types = {
            LedgerEventType.GENESIS,
            LedgerEventType.FX,
            LedgerEventType.BUY,
            LedgerEventType.SELL,
            LedgerEventType.DIVIDEND,
            LedgerEventType.FEE,
            LedgerEventType.CORRECTION,
            LedgerEventType.CORPORATE_ACTION,
        }
        latest_material_state_at = max(
            (portfolio.created_at,)
            + tuple(
                max(item.occurred_at, item.recorded_at)
                for item in state.ledger_entries
                if item.event_type in material_event_types
            )
            + tuple(item.acquired_at for item in state.lots.values())
        )
        if normalized_as_of < latest_material_state_at:
            raise PaperBlockedError("RETROACTIVE_CURRENT_STATE_VALUATION_FORBIDDEN")
        reasons: list[str] = []

        def fx_value(currency: Currency) -> Decimal | None:
            if currency is Currency.KRW:
                return Decimal("1")
            rate = fx_rates.get(currency)
            if (
                rate is None
                or rate.currency is not currency
                or not rate.verified
                or rate.as_of > normalized_cutoff
                or rate.as_of > normalized_as_of
            ):
                reasons.append(f"FX_UNAVAILABLE_{currency.value}")
                return None
            return rate.krw_per_unit

        with localcontext(_paper_decimal_context(exact=True)):
            cash_krw = Decimal("0")
            for currency, amount in state.cash.items():
                if amount == 0:
                    continue
                rate = fx_value(currency)
                if rate is not None:
                    cash_krw += amount * rate
            instrument_values: dict[str, Decimal] = {}
            cost_krw = Decimal("0")
            for lot in state.lots.values():
                if lot.remaining_quantity == 0:
                    continue
                price = prices.get(lot.instrument_id)
                if (
                    price is None
                    or price.instrument_id != lot.instrument_id
                    or not price.verified
                    or price.currency is not lot.currency
                    or price.as_of > normalized_cutoff
                    or price.as_of > normalized_as_of
                ):
                    reasons.append("PRICE_UNAVAILABLE")
                    continue
                rate = fx_value(lot.currency)
                if rate is None:
                    continue
                value = price.value * Decimal(lot.remaining_quantity) * rate
                instrument_values[lot.instrument_id] = instrument_values.get(
                    lot.instrument_id,
                    Decimal("0"),
                ) + value
                cost_krw += lot.remaining_cost * rate
        if reasons:
            snapshot = PaperValuationSnapshot(
                snapshot_id,
                portfolio.portfolio_id,
                normalized_as_of,
                normalized_cutoff,
                GateStatus.UNKNOWN,
                None,
                None,
                None,
                None,
                (),
                tuple(sorted(set(reasons))),
                source_hash,
            )
        else:
            with localcontext(_paper_decimal_context(exact=True)):
                position_value = sum(instrument_values.values(), Decimal("0"))
                snapshot = PaperValuationSnapshot(
                    snapshot_id,
                    portfolio.portfolio_id,
                    normalized_as_of,
                    normalized_cutoff,
                    GateStatus.PASS,
                    cash_krw + position_value,
                    cash_krw,
                    position_value,
                    position_value - cost_krw,
                    tuple(sorted(instrument_values.items())),
                    (),
                    source_hash,
                )

        def store_snapshot(current: _PaperState) -> PaperValuationSnapshot:
            self._require_portfolio(current)
            if snapshot.snapshot_id in current.valuations:
                raise PaperBlockedError("VALUATION_SNAPSHOT_IMMUTABLE")
            if canonical_hash((current.cash, current.lots)) != source_hash:
                raise PaperBlockedError("VALUATION_SOURCE_CHANGED")
            current.valuations[snapshot.snapshot_id] = snapshot
            return snapshot

        return self._repository.transact(store_snapshot)

    def open_daily_limit(
        self,
        *,
        session: VerifiedSessionWindow,
        opening_valuation_snapshot_id: str,
    ) -> PaperDailyLimit:
        if type(session) is not VerifiedSessionWindow:
            raise DomainValidationError("PAPER 세션 정보 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> PaperDailyLimit:
            portfolio = self._require_portfolio(state)
            snapshot = state.valuations.get(opening_valuation_snapshot_id)
            if (
                not session.verified
                or snapshot is None
                or snapshot.portfolio_id != portfolio.portfolio_id
                or snapshot.status is not GateStatus.PASS
                or snapshot.nav_krw is None
                or snapshot.as_of > session.opens_at
                or snapshot.cutoff_at > session.opens_at
            ):
                raise PaperBlockedError("VERIFIED_OPENING_NAV_SNAPSHOT_REQUIRED")
            existing = state.daily_limits.get(session.us_session_date)
            if existing is not None:
                if (
                    existing.opening_nav_krw != snapshot.nav_krw
                    or existing.session_calendar_ref != session.session_calendar_ref
                    or existing.opening_valuation_snapshot_id != snapshot.snapshot_id
                ):
                    raise PaperBlockedError("SESSION_OPENING_LIMIT_IMMUTABLE")
                return existing
            if snapshot.source_state_hash != canonical_hash((state.cash, state.lots)):
                raise PaperBlockedError("FRESH_OPENING_NAV_SNAPSHOT_REQUIRED")
            nav = snapshot.nav_krw
            try:
                with localcontext(_paper_decimal_context(exact=True)):
                    limit_krw = (nav * DAILY_GROSS_RATE).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
            except DecimalException as exc:
                raise DomainValidationError(
                    "PAPER 세션 한도를 정확하게 계산할 수 없습니다."
                ) from exc
            limit = PaperDailyLimit(
                session.us_session_date,
                session.session_calendar_ref,
                snapshot.snapshot_id,
                nav,
                limit_krw,
            )
            state.daily_limits[session.us_session_date] = limit
            return limit

        return self._repository.transact(operation)

    def create_trade_purpose(self, purpose: TradePurpose) -> None:
        if type(purpose) is not TradePurpose:
            raise DomainValidationError("TradePurpose 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> None:
            portfolio = self._require_portfolio(state)
            if purpose.created_at < portfolio.created_at:
                raise PaperBlockedError("TRADE_PURPOSE_BEFORE_PORTFOLIO")
            self._require_purpose_references(state, purpose)
            budget = state.budgets[purpose.budget_version_ref]
            if purpose.created_at < budget.approved_at:
                raise PaperBlockedError("TRADE_PURPOSE_BEFORE_BUDGET_APPROVAL")
            matching_applications = tuple(
                item
                for item in state.strategy_applications
                if item.strategy_version_id == purpose.strategy_version_ref
            )
            if (
                not matching_applications
                or purpose.created_at < matching_applications[-1].applied_at
            ):
                raise PaperBlockedError("TRADE_PURPOSE_BEFORE_STRATEGY_APPLICATION")
            if purpose.trade_purpose_id in state.purposes:
                raise PaperBlockedError("TRADE_PURPOSE_IMMUTABLE")
            if purpose.parent_trade_purpose_id is not None:
                parent = state.purposes.get(purpose.parent_trade_purpose_id)
                if parent is None:
                    raise PaperBlockedError("PARENT_TRADE_PURPOSE_NOT_FOUND")
                if purpose.created_at < parent.created_at:
                    raise PaperBlockedError("TRADE_PURPOSE_TIME_ROLLBACK")
                if parent.stable_root_trade_purpose_id != purpose.stable_root_trade_purpose_id:
                    raise PaperBlockedError("SAME_PURPOSE_MUST_KEEP_STABLE_ROOT")
                if purpose.changed_from_root_id is not None or purpose.new_root_approval_ref is not None:
                    raise PaperBlockedError("NEW_ROOT_REQUIRES_ATOMIC_APPROVAL_CONSUMPTION")
            elif purpose.changed_from_root_id is not None or purpose.new_root_approval_ref is not None:
                raise PaperBlockedError("NEW_ROOT_REQUIRES_ATOMIC_APPROVAL_CONSUMPTION")
            elif state.purposes:
                raise PaperBlockedError("NEW_ROOT_REQUIRES_ATOMIC_APPROVAL_CONSUMPTION")
            state.purposes[purpose.trade_purpose_id] = purpose

        self._repository.transact(operation)

    def create_candidate_set(self, candidate_set: CandidateSet) -> None:
        if type(candidate_set) is not CandidateSet:
            raise DomainValidationError("CandidateSet 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> None:
            self._require_portfolio(state)
            if candidate_set.state is not CandidateSetState.DRAFT:
                raise PaperBlockedError("NEW_CANDIDATE_SET_MUST_BE_DRAFT")
            purpose = state.purposes.get(candidate_set.trade_purpose_id)
            if purpose is None or purpose.stable_root_trade_purpose_id != candidate_set.stable_root_trade_purpose_id:
                raise PaperBlockedError("CANDIDATE_PURPOSE_NOT_FOUND")
            if (
                candidate_set.created_at < purpose.created_at
                or candidate_set.evidence_cutoff > candidate_set.created_at
            ):
                raise PaperBlockedError("CANDIDATE_SET_CAUSAL_TIME_INVALID")
            if candidate_set.set_id in state.candidate_sets:
                raise PaperBlockedError("CANDIDATE_SET_IMMUTABLE")
            if candidate_set.regeneration_of is not None and candidate_set.regeneration_of not in state.candidate_sets:
                raise PaperBlockedError("REGENERATED_SET_PARENT_NOT_FOUND")
            if candidate_set.regeneration_of is not None:
                parent = state.candidate_sets[candidate_set.regeneration_of]
                if parent.state is not CandidateSetState.REGENERATION_REQUIRED:
                    raise PaperBlockedError("REGENERATION_NOT_REQUESTED")
                regeneration_requested_at = max(
                    (
                        audit.recorded_at
                        for audit in state.audits
                        if audit.event_type == "LARGE_TRADE_REJECTED"
                        and audit.evidence_ref == parent.set_id
                    ),
                    default=None,
                )
                if regeneration_requested_at is None:
                    raise PaperBlockedError("REGENERATION_REQUEST_AUDIT_NOT_FOUND")
                if candidate_set.created_at < regeneration_requested_at:
                    raise PaperBlockedError("REGENERATED_SET_TIME_ROLLBACK")
                if (
                    parent.trade_purpose_id != candidate_set.trade_purpose_id
                    or parent.stable_root_trade_purpose_id
                    != candidate_set.stable_root_trade_purpose_id
                ):
                    raise PaperBlockedError("REGENERATED_SET_PURPOSE_MISMATCH")
                state.candidate_sets[parent.set_id] = replace(
                    parent,
                    state=CandidateSetState.SUPERSEDED,
                )
            state.candidate_sets[candidate_set.set_id] = candidate_set

        self._repository.transact(operation)

    def transition_candidate_set(
        self,
        set_id: str,
        new_state: CandidateSetState,
        *,
        approval_id: str | None = None,
        transitioned_at: datetime | None = None,
    ) -> CandidateSet:
        _require_enum(
            new_state,
            CandidateSetState,
            field_name="candidate set state",
        )
        allowed = {
            CandidateSetState.DRAFT: {CandidateSetState.VALIDATED},
            CandidateSetState.VALIDATED: {CandidateSetState.WAITING_SET_APPROVAL},
            CandidateSetState.WAITING_SET_APPROVAL: {
                CandidateSetState.SET_APPROVED,
                CandidateSetState.REJECTED,
                CandidateSetState.EXPIRED,
            },
            CandidateSetState.REGENERATION_REQUIRED: {
                CandidateSetState.EXPIRED,
                CandidateSetState.SUPERSEDED,
            },
        }

        def operation(state: _PaperState) -> CandidateSet:
            current = state.candidate_sets.get(set_id)
            if current is None:
                raise PaperBlockedError("CANDIDATE_SET_NOT_FOUND")
            if new_state not in allowed.get(current.state, set()):
                raise PaperBlockedError("INVALID_CANDIDATE_SET_TRANSITION")
            if new_state is CandidateSetState.REJECTED:
                raise PaperBlockedError("CANDIDATE_REJECTION_REQUIRES_AUDIT_METHOD")
            approval_ref = current.approval_ref
            if new_state is CandidateSetState.SET_APPROVED:
                if approval_id is None or transitioned_at is None:
                    raise PaperBlockedError("CANDIDATE_SET_APPROVAL_REQUIRED")
                normalized_at = _utc(
                    transitioned_at,
                    field_name="candidate approval transition time",
                )
                if normalized_at < current.created_at:
                    raise PaperBlockedError("CANDIDATE_APPROVAL_BEFORE_SET_CREATION")
                approval = self._bind_valid_approval_in_state(
                    state,
                    approval_id=approval_id,
                    expected_type=ApprovalType.CANDIDATE_SET,
                    expected_target=ApprovalTarget(
                        current.set_id,
                        current.version,
                        current.set_hash,
                    ),
                    now=normalized_at,
                    consumer_ref=current.set_id,
                )
                if approval.approved_at < current.created_at:
                    raise PaperBlockedError("CANDIDATE_APPROVAL_BEFORE_SET_CREATION")
                approval_ref = approval.approval_id
            elif approval_id is not None or transitioned_at is not None:
                raise PaperBlockedError("CANDIDATE_APPROVAL_ARGUMENT_NOT_ALLOWED")
            updated = replace(current, state=new_state, approval_ref=approval_ref)
            state.candidate_sets[set_id] = updated
            return updated

        return self._repository.transact(operation)

    def reject_candidate_set(
        self,
        set_id: str,
        *,
        rejected_at: datetime,
        rejected_by_user_ref: str,
        evidence_ref: str,
        audit_id: str,
    ) -> CandidateSet:
        normalized_at = _utc(rejected_at, field_name="candidate set rejected_at")
        user_ref = nonempty(
            rejected_by_user_ref,
            field_name="candidate rejection user ref",
        )
        evidence = nonempty(evidence_ref, field_name="candidate rejection evidence")
        audit = nonempty(audit_id, field_name="candidate rejection audit ID")

        def operation(state: _PaperState) -> CandidateSet:
            candidate_set = state.candidate_sets.get(set_id)
            if (
                candidate_set is None
                or candidate_set.state is not CandidateSetState.WAITING_SET_APPROVAL
            ):
                raise PaperBlockedError("CANDIDATE_SET_NOT_REJECTABLE")
            if normalized_at < max(
                candidate_set.created_at,
                candidate_set.evidence_cutoff,
            ):
                raise PaperBlockedError("CANDIDATE_REJECTION_TIME_INVALID")
            if any(
                proposal.candidate_set_id == candidate_set.set_id
                for proposal in state.proposals.values()
            ):
                raise PaperBlockedError("CANDIDATE_REJECTION_AFTER_PROPOSAL_FORBIDDEN")
            self._require_audit_id_available(state, audit)
            updated = replace(candidate_set, state=CandidateSetState.REJECTED)
            state.candidate_sets[set_id] = updated
            state.audits.append(
                AuditEvent(
                    audit,
                    "CANDIDATE_SET_REJECTED",
                    set_id,
                    normalized_at,
                    actor_ref=user_ref,
                    evidence_ref=evidence,
                    reason_code="USER_REJECTED",
                )
            )
            return updated

        return self._repository.transact(operation)

    def next_candidate(
        self,
        *,
        set_id: str,
        current_rank: int,
        reason: CandidateFailureReason,
        evidence_verified: bool,
        approved_permanent_reasons: frozenset[CandidateFailureReason],
        evidence_ref: str | None = None,
        verified_at: datetime | None = None,
    ) -> Candidate | None:
        _require_bool(evidence_verified, field_name="candidate evidence verified")
        _require_enum(reason, CandidateFailureReason, field_name="candidate failure reason")
        approved_reason_items = _sequence(
            approved_permanent_reasons,
            field_name="approved permanent reasons",
        )
        if any(
            not isinstance(item, CandidateFailureReason)
            for item in approved_reason_items
        ):
            raise DomainValidationError("승인된 영구 실패 사유 집합이 올바르지 않습니다.")
        approved_reason_set = frozenset(approved_reason_items)
        permanent = {
            CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE,
            CandidateFailureReason.CONFIRMED_PERMANENTLY_UNTRADABLE,
        }
        if not evidence_verified or reason not in permanent or reason not in approved_reason_set:
            return None
        if evidence_ref is None or verified_at is None:
            raise PaperBlockedError("VERIFIED_PERMANENT_FAILURE_EVIDENCE_REQUIRED")
        clearance = CandidateRankClearance(
            set_id=set_id,
            failed_rank=current_rank,
            reason=reason,
            evidence_ref=evidence_ref,
            verified_at=verified_at,
            approved_reason_set_hash=canonical_hash(
                tuple(sorted(item.value for item in approved_reason_set))
            ),
        )

        def operation(state: _PaperState) -> Candidate | None:
            candidate_set = state.candidate_sets.get(set_id)
            if candidate_set is None:
                raise PaperBlockedError("CANDIDATE_SET_NOT_FOUND")
            if candidate_set.state is not CandidateSetState.SET_APPROVED:
                raise PaperBlockedError("CANDIDATE_SET_NOT_APPROVED")
            if clearance.verified_at < candidate_set.created_at:
                raise PaperBlockedError("CANDIDATE_FAILURE_EVIDENCE_TIME_INVALID")
            current = next(
                (item for item in candidate_set.candidates if item.rank == current_rank),
                None,
            )
            following = next(
                (item for item in candidate_set.candidates if item.rank == current_rank + 1),
                None,
            )
            if current is None or following is None:
                return None
            rank_proposals = tuple(
                proposal
                for proposal in state.proposals.values()
                if proposal.candidate_set_id == set_id
                and proposal.candidate_rank == current_rank
            )
            for proposal in rank_proposals:
                has_active_reservation = any(
                    reservation.proposal_id == proposal.proposal_id
                    and _active_reservation(reservation.state)
                    for reservation in state.reservations.values()
                )
                has_attempt_or_fill = self._proposal_has_open_attempt(
                    state,
                    proposal.proposal_id,
                ) or any(
                    fill.proposal_id == proposal.proposal_id
                    for fill in state.fills.values()
                )
                if has_active_reservation or has_attempt_or_fill:
                    raise PaperBlockedError("PRIOR_CANDIDATE_ALREADY_ACTIVE_OR_EXECUTED")
                if proposal.state not in {
                    ProposalState.PROPOSED,
                    ProposalState.ELIGIBILITY_CHECKED,
                    ProposalState.DEFINITIVELY_UNEXECUTABLE,
                }:
                    raise PaperBlockedError("PRIOR_CANDIDATE_NOT_PERMANENTLY_INELIGIBLE")
            key = (set_id, current_rank)
            existing = state.candidate_rank_clearances.get(key)
            if existing is not None and existing != clearance:
                raise PaperBlockedError("CANDIDATE_RANK_CLEARANCE_IMMUTABLE")
            for proposal in rank_proposals:
                if proposal.state is not ProposalState.DEFINITIVELY_UNEXECUTABLE:
                    state.proposals[proposal.proposal_id] = replace(
                        proposal,
                        state=ProposalState.DEFINITIVELY_UNEXECUTABLE,
                    )
            state.candidate_rank_clearances[key] = clearance
            return following

        return self._repository.transact(operation)

    def create_proposal(self, proposal: TradeProposal) -> None:
        if type(proposal) is not TradeProposal:
            raise DomainValidationError("TradeProposal 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> None:
            portfolio = self._require_portfolio(state)
            candidate_set = state.candidate_sets.get(proposal.candidate_set_id)
            purpose = state.purposes.get(proposal.trade_purpose_id)
            if candidate_set is None or purpose is None:
                raise PaperBlockedError("PROPOSAL_PARENT_NOT_FOUND")
            if candidate_set.state is not CandidateSetState.SET_APPROVED:
                raise PaperBlockedError("CANDIDATE_SET_NOT_APPROVED")
            if proposal.expires_at <= candidate_set.created_at:
                raise PaperBlockedError("PROPOSAL_EXPIRY_CAUSAL_TIME_INVALID")
            assert proposal.price_condition is not None
            if proposal.price_condition.evidence_cutoff_at > candidate_set.created_at:
                raise PaperBlockedError("PRICE_CONDITION_EVIDENCE_FROM_FUTURE")
            if proposal.stable_root_trade_purpose_id != purpose.stable_root_trade_purpose_id:
                raise PaperBlockedError("PROPOSAL_ROOT_MISMATCH")
            if proposal.strategy_id is not purpose.strategy_id:
                raise PaperBlockedError("PROPOSAL_STRATEGY_MISMATCH")
            if not any(
                candidate.rank == proposal.candidate_rank and candidate.instrument_id == proposal.instrument_id
                for candidate in candidate_set.candidates
            ):
                raise PaperBlockedError("PROPOSAL_CANDIDATE_MISMATCH")
            self._require_candidate_rank_available(state, proposal)
            if proposal.proposal_id in state.proposals:
                raise PaperBlockedError("PROPOSAL_IMMUTABLE")
            if proposal.state is not ProposalState.PROPOSED:
                raise PaperBlockedError("NEW_PROPOSAL_MUST_BE_PROPOSED")
            if (
                candidate_set.trade_purpose_id != proposal.trade_purpose_id
                or candidate_set.stable_root_trade_purpose_id
                != proposal.stable_root_trade_purpose_id
                or proposal.purpose_version != purpose.purpose_version
            ):
                raise PaperBlockedError("PROPOSAL_PURPOSE_MISMATCH")
            # portfolio_id is part of the effective aggregation key through this state.
            if portfolio.scope is not PaperScope.INTERNAL_PAPER:
                raise PaperBlockedError("PAPER_SCOPE_MISMATCH")
            state.proposals[proposal.proposal_id] = proposal

        self._repository.transact(operation)

    def evaluate_proposal_price_condition(
        self,
        *,
        proposal_id: str,
        observed_price: VerifiedPrice,
        observation_cutoff_at: datetime,
        evaluated_at: datetime,
    ) -> PriceConditionCheck:
        """명시적인 관측값으로 가격조건을 판정하고 보류/적격 상태를 보존한다."""

        if type(observed_price) is not VerifiedPrice:
            raise DomainValidationError("가격조건 관측값 형식이 올바르지 않습니다.")
        cutoff = _utc(
            observation_cutoff_at,
            field_name="price observation cutoff",
        )
        evaluated = _utc(evaluated_at, field_name="price condition evaluated_at")

        def operation(state: _PaperState) -> PriceConditionCheck:
            proposal = state.proposals.get(proposal_id)
            if proposal is None:
                raise PaperBlockedError("PROPOSAL_NOT_FOUND")
            if proposal.state not in {
                ProposalState.PROPOSED,
                ProposalState.ELIGIBILITY_CHECKED,
                ProposalState.TEMPORARILY_HELD,
            }:
                raise PaperBlockedError("PROPOSAL_PRICE_NOT_CHECKABLE")
            if evaluated >= proposal.expires_at:
                raise PaperBlockedError("PROPOSAL_EXPIRED")
            candidate_set = state.candidate_sets.get(proposal.candidate_set_id)
            if candidate_set is None or candidate_set.approval_ref is None:
                raise PaperBlockedError("CANDIDATE_SET_NOT_APPROVED")
            approval = state.approvals.get(candidate_set.approval_ref)
            if approval is None:
                raise PaperBlockedError("CANDIDATE_SET_APPROVAL_NOT_FOUND")
            assert proposal.price_condition is not None
            if observed_price.as_of > cutoff or cutoff > evaluated:
                raise PaperBlockedError("PRICE_OBSERVATION_CAUSAL_TIME_INVALID")
            if (
                not observed_price.verified
                or observed_price.instrument_id != proposal.instrument_id
                or observed_price.currency is not proposal.amounts.cash_currency
            ):
                decision = GateDecision.unknown("PRICE_OBSERVATION_UNVERIFIED")
            elif observed_price.as_of < max(
                proposal.price_condition.evidence_cutoff_at,
                approval.approved_at,
            ):
                decision = GateDecision.unknown("PRICE_OBSERVATION_BEFORE_APPROVAL")
            elif proposal.price_condition.is_met(observed_price.value):
                decision = GateDecision.passed()
            else:
                decision = GateDecision.blocked("PRICE_CONDITION_NOT_MET")
            check = PriceConditionCheck(
                proposal.proposal_id,
                proposal.proposal_hash,
                observed_price,
                cutoff,
                evaluated,
                decision,
            )
            prior_checks = tuple(
                item
                for item in state.price_condition_checks
                if item.proposal_id == proposal.proposal_id
            )
            proposal_time_floor = self._proposal_state_time_floor(state, proposal)
            if any(
                moment < proposal_time_floor
                for moment in (
                    check.observed_price.as_of,
                    check.observation_cutoff_at,
                    check.evaluated_at,
                )
            ):
                raise PaperBlockedError("PRICE_CONDITION_TIME_ROLLBACK")
            if prior_checks:
                latest = max(
                    prior_checks,
                    key=lambda item: (
                        item.evaluated_at,
                        item.observation_cutoff_at,
                        item.observed_price.as_of,
                    ),
                )
                if check == latest:
                    return latest
                if (
                    check.evaluated_at < latest.evaluated_at
                    or check.observation_cutoff_at
                    < latest.observation_cutoff_at
                    or check.observed_price.as_of < latest.observed_price.as_of
                ):
                    raise PaperBlockedError("PRICE_CONDITION_TIME_ROLLBACK")
                if check.evaluated_at == latest.evaluated_at:
                    raise PaperBlockedError("PRICE_CONDITION_TIME_CONFLICT")
            state.price_condition_checks.append(check)
            has_active_reservation = any(
                reservation.proposal_id == proposal.proposal_id
                and reservation.state is ReservationState.ACTIVE
                for reservation in state.reservations.values()
            )
            state.proposals[proposal.proposal_id] = replace(
                proposal,
                state=(
                    (
                        ProposalState.READY
                        if has_active_reservation
                        else ProposalState.ELIGIBILITY_CHECKED
                    )
                    if decision.permits_execution
                    else ProposalState.TEMPORARILY_HELD
                ),
            )
            return check

        return self._repository.transact(operation)

    def large_approval_group_amount(self, proposal_id: str) -> Decimal:
        nonempty(proposal_id, field_name="proposal ID")
        state = self._repository.snapshot()
        proposal = state.proposals.get(proposal_id)
        if proposal is None:
            raise PaperBlockedError("PROPOSAL_NOT_FOUND")
        terminal = {
            ProposalState.CANCELLED,
            ProposalState.EXPIRED,
            ProposalState.MISSED_WINDOW,
            ProposalState.DEFINITIVELY_UNEXECUTABLE,
        }
        try:
            with localcontext(_paper_decimal_context(exact=True)):
                return sum(
                    (
                        item.amounts.approval_judgment_krw
                        for item in state.proposals.values()
                        if item.approval_group_key == proposal.approval_group_key
                        and item.state not in terminal
                    ),
                    Decimal("0"),
                )
        except DecimalException as exc:
            raise DomainValidationError(
                "PAPER 승인 집계액을 정확하게 계산할 수 없습니다."
            ) from exc

    def large_approval_group_target(self, proposal_id: str) -> ApprovalTarget:
        nonempty(proposal_id, field_name="proposal ID")
        state = self._repository.snapshot()
        portfolio = self._require_portfolio(state)
        proposal = state.proposals.get(proposal_id)
        if proposal is None:
            raise PaperBlockedError("PROPOSAL_NOT_FOUND")
        members = tuple(
            sorted(
                (
                    item.proposal_id,
                    item.version,
                    item.amounts.approval_judgment_krw,
                    item.amounts.fx_krw_per_unit,
                    item.pricing_fx_rate.source_record_id
                    if item.pricing_fx_rate is not None
                    else None,
                    item.pricing_cutoff_at,
                )
                for item in state.proposals.values()
                if item.approval_group_key == proposal.approval_group_key
                and item.state
                not in {
                    ProposalState.CANCELLED,
                    ProposalState.EXPIRED,
                    ProposalState.MISSED_WINDOW,
                    ProposalState.DEFINITIVELY_UNEXECUTABLE,
                }
            )
        )
        full_key = (portfolio.portfolio_id, *proposal.approval_group_key)
        group_hash = canonical_hash((full_key, members))
        group_id = canonical_hash(full_key)
        return ApprovalTarget(group_id, proposal.us_session_date.isoformat(), group_hash)

    def grant_approval(
        self,
        *,
        approval_id: str,
        approval_type: ApprovalType,
        target: ApprovalTarget,
        approved_by_user_id: str,
        approved_at: datetime,
        audit_ref: str,
        new_root_target: NewRootPurposeTarget | None = None,
    ) -> Approval:
        if type(target) is not ApprovalTarget:
            raise DomainValidationError("승인 대상 형식이 올바르지 않습니다.")
        if new_root_target is not None and type(new_root_target) is not NewRootPurposeTarget:
            raise DomainValidationError("새 root 승인 대상 형식이 올바르지 않습니다.")
        normalized_at = _utc(approved_at, field_name="approved_at")
        approval = Approval(
            approval_id,
            approval_type,
            target,
            approved_by_user_id,
            normalized_at,
            normalized_at + APPROVAL_LIFETIME,
            audit_ref,
            new_root_target=new_root_target,
        )
        if new_root_target is not None and new_root_target.checked_at > normalized_at:
            raise PaperBlockedError("NEW_ROOT_CHECK_AFTER_APPROVAL")

        def operation(state: _PaperState) -> Approval:
            self._require_portfolio(state)
            if approval.approval_id in state.approvals:
                raise PaperBlockedError("APPROVAL_IMMUTABLE")
            self._require_audit_id_available(state, audit_ref)
            state.approvals[approval.approval_id] = approval
            state.audits.append(AuditEvent(audit_ref, "APPROVAL_GRANTED", approval.approval_id, normalized_at))
            return approval

        return self._repository.transact(operation)

    def cancel_approval(self, approval_id: str, *, cancelled_at: datetime, audit_id: str) -> None:
        normalized_at = _utc(cancelled_at, field_name="cancelled_at")

        def operation(state: _PaperState) -> None:
            approval = state.approvals.get(approval_id)
            if approval is None:
                raise PaperBlockedError("APPROVAL_NOT_FOUND")
            if normalized_at < self._approval_state_time_floor(state, approval):
                raise PaperBlockedError("APPROVAL_STATE_TIME_INVALID")
            if approval.state in {
                ApprovalState.CONSUMED,
                ApprovalState.CANCELLED,
                ApprovalState.INVALIDATED,
                ApprovalState.EXPIRED,
            }:
                raise PaperBlockedError("APPROVAL_NOT_CANCELLABLE")
            self._require_audit_id_available(state, audit_id)
            state.approvals[approval_id] = replace(
                approval,
                state=ApprovalState.CANCELLED,
                state_changed_at=normalized_at,
            )
            for reservation in tuple(state.reservations.values()):
                if (
                    reservation.candidate_approval_id == approval_id
                    or reservation.large_approval_id == approval_id
                ) and _active_reservation(reservation.state):
                    if self._proposal_has_open_attempt(state, reservation.proposal_id):
                        state.reservations[reservation.reservation_id] = replace(
                            reservation,
                            state=ReservationState.NEEDS_RECONCILIATION,
                            reason_code="APPROVAL_CANCELLED_RESULT_UNKNOWN",
                        )
                        proposal = state.proposals[reservation.proposal_id]
                        state.proposals[proposal.proposal_id] = replace(
                            proposal,
                            state=ProposalState.NEEDS_RECONCILIATION,
                        )
                        self._hold_open_attempt_runs_for_reconciliation(
                            state,
                            proposal_id=proposal.proposal_id,
                            at=normalized_at,
                            reason="APPROVAL_CANCELLED_RESULT_UNKNOWN",
                        )
                        continue
                    self._release_active_reservation_for_proposal(
                        state,
                        proposal_id=reservation.proposal_id,
                        released_at=normalized_at,
                        reason_code="APPROVAL_CANCELLED",
                    )
                    proposal = state.proposals[reservation.proposal_id]
                    state.proposals[proposal.proposal_id] = replace(
                        proposal,
                        state=ProposalState.CANCELLED,
                    )
                    self._hold_execution_runs_for_gate_loss(
                        state,
                        proposal_id=proposal.proposal_id,
                        at=normalized_at,
                        reason="APPROVAL_CANCELLED",
                        evidence=approval_id,
                    )
            state.audits.append(AuditEvent(audit_id, "APPROVAL_CANCELLED", approval_id, normalized_at))

        self._repository.transact(operation)

    def expire_approval(
        self,
        approval_id: str,
        *,
        expired_at: datetime,
        evidence_ref: str,
        audit_id: str,
    ) -> Approval:
        """24시간 경계에 도달한 승인을 예약 상태와 함께 명시적으로 만료한다."""

        normalized_at = _utc(expired_at, field_name="approval expired_at")
        evidence = nonempty(evidence_ref, field_name="approval expiry evidence")
        audit = nonempty(audit_id, field_name="approval expiry audit ID")

        def operation(state: _PaperState) -> Approval:
            approval = state.approvals.get(approval_id)
            if approval is None:
                raise PaperBlockedError("APPROVAL_NOT_FOUND")
            if normalized_at < approval.expires_at:
                raise PaperBlockedError("APPROVAL_NOT_EXPIRED")
            if approval.state in {
                ApprovalState.CONSUMED,
                ApprovalState.CANCELLED,
                ApprovalState.INVALIDATED,
                ApprovalState.EXPIRED,
            }:
                raise PaperBlockedError("APPROVAL_NOT_EXPIRABLE")
            if normalized_at < self._approval_state_time_floor(state, approval):
                raise PaperBlockedError("APPROVAL_STATE_TIME_INVALID")
            self._require_audit_id_available(state, audit)
            updated_approval = replace(
                approval,
                state=ApprovalState.EXPIRED,
                state_changed_at=normalized_at,
                invalidation_reason="APPROVAL_EXPIRED",
            )
            state.approvals[approval_id] = updated_approval
            for reservation in tuple(state.reservations.values()):
                if (
                    reservation.candidate_approval_id != approval_id
                    and reservation.large_approval_id != approval_id
                ) or not _active_reservation(reservation.state):
                    continue
                if self._proposal_has_open_attempt(state, reservation.proposal_id):
                    state.reservations[reservation.reservation_id] = replace(
                        reservation,
                        state=ReservationState.NEEDS_RECONCILIATION,
                        reason_code="APPROVAL_EXPIRED_RESULT_UNKNOWN",
                    )
                    proposal = state.proposals[reservation.proposal_id]
                    state.proposals[proposal.proposal_id] = replace(
                        proposal,
                        state=ProposalState.NEEDS_RECONCILIATION,
                    )
                    self._hold_open_attempt_runs_for_reconciliation(
                        state,
                        proposal_id=proposal.proposal_id,
                        at=normalized_at,
                        reason="APPROVAL_EXPIRED_RESULT_UNKNOWN",
                    )
                else:
                    self._release_active_reservation_for_proposal(
                        state,
                        proposal_id=reservation.proposal_id,
                        released_at=normalized_at,
                        reason_code="APPROVAL_EXPIRED",
                    )
                    proposal = state.proposals[reservation.proposal_id]
                    state.proposals[proposal.proposal_id] = replace(
                        proposal,
                        state=ProposalState.EXPIRED,
                    )
                    self._hold_execution_runs_for_gate_loss(
                        state,
                        proposal_id=proposal.proposal_id,
                        at=normalized_at,
                        reason="APPROVAL_EXPIRED",
                        evidence=approval_id,
                    )
            if approval.approval_type is ApprovalType.CANDIDATE_SET:
                candidate_set = state.candidate_sets.get(approval.target.target_id)
                if (
                    candidate_set is not None
                    and candidate_set.approval_ref == approval_id
                    and candidate_set.state is CandidateSetState.SET_APPROVED
                ):
                    state.candidate_sets[candidate_set.set_id] = replace(
                        candidate_set,
                        state=CandidateSetState.EXPIRED,
                    )
            state.audits.append(
                AuditEvent(
                    audit,
                    "APPROVAL_EXPIRED",
                    approval_id,
                    normalized_at,
                    evidence_ref=evidence,
                )
            )
            return updated_approval

        return self._repository.transact(operation)

    def invalidate_approval(
        self,
        approval_id: str,
        *,
        invalidated_at: datetime,
        reason_code: str,
        audit_id: str,
    ) -> None:
        normalized_at = _utc(invalidated_at, field_name="invalidated_at")
        reason = nonempty(reason_code, field_name="invalidation reason")

        def operation(state: _PaperState) -> None:
            approval = state.approvals.get(approval_id)
            if approval is None:
                raise PaperBlockedError("APPROVAL_NOT_FOUND")
            if normalized_at < self._approval_state_time_floor(state, approval):
                raise PaperBlockedError("APPROVAL_STATE_TIME_INVALID")
            if approval.state in {
                ApprovalState.CONSUMED,
                ApprovalState.CANCELLED,
                ApprovalState.INVALIDATED,
                ApprovalState.EXPIRED,
            }:
                raise PaperBlockedError("APPROVAL_NOT_INVALIDATABLE")
            self._require_audit_id_available(state, audit_id)
            state.approvals[approval_id] = replace(
                approval,
                state=ApprovalState.INVALIDATED,
                state_changed_at=normalized_at,
                invalidation_reason=reason,
            )
            for reservation in tuple(state.reservations.values()):
                if (
                    reservation.candidate_approval_id == approval_id
                    or reservation.large_approval_id == approval_id
                ) and _active_reservation(reservation.state):
                    if self._proposal_has_open_attempt(state, reservation.proposal_id):
                        state.reservations[reservation.reservation_id] = replace(
                            reservation,
                            state=ReservationState.NEEDS_RECONCILIATION,
                            reason_code="APPROVAL_INVALIDATED_RESULT_UNKNOWN",
                        )
                        proposal = state.proposals[reservation.proposal_id]
                        state.proposals[proposal.proposal_id] = replace(
                            proposal,
                            state=ProposalState.NEEDS_RECONCILIATION,
                        )
                        self._hold_open_attempt_runs_for_reconciliation(
                            state,
                            proposal_id=proposal.proposal_id,
                            at=normalized_at,
                            reason="APPROVAL_INVALIDATED_RESULT_UNKNOWN",
                        )
                        continue
                    self._release_active_reservation_for_proposal(
                        state,
                        proposal_id=reservation.proposal_id,
                        released_at=normalized_at,
                        reason_code="APPROVAL_INVALIDATED",
                    )
                    proposal = state.proposals[reservation.proposal_id]
                    state.proposals[proposal.proposal_id] = replace(
                        proposal,
                        state=ProposalState.CANCELLED,
                    )
                    self._hold_execution_runs_for_gate_loss(
                        state,
                        proposal_id=proposal.proposal_id,
                        at=normalized_at,
                        reason="APPROVAL_INVALIDATED",
                        evidence=approval_id,
                    )
            state.audits.append(
                AuditEvent(audit_id, "APPROVAL_INVALIDATED", approval_id, normalized_at)
            )

        self._repository.transact(operation)

    def reject_large_trade_proposal(
        self,
        proposal_id: str,
        *,
        rejected_at: datetime,
        rejected_by_user_ref: str,
        audit_id: str,
    ) -> CandidateSet:
        """1순위 개별승인 거절을 종료·재생성 필요 상태로 원자 기록한다."""

        normalized_at = _utc(rejected_at, field_name="large trade rejected_at")
        user_ref = nonempty(
            rejected_by_user_ref,
            field_name="large trade rejection user ref",
        )
        audit = nonempty(audit_id, field_name="large trade rejection audit ID")

        def operation(state: _PaperState) -> CandidateSet:
            proposal = state.proposals.get(proposal_id)
            if proposal is None:
                raise PaperBlockedError("PROPOSAL_NOT_FOUND")
            candidate_set = state.candidate_sets.get(proposal.candidate_set_id)
            if candidate_set is None or candidate_set.state is not CandidateSetState.SET_APPROVED:
                raise PaperBlockedError("CANDIDATE_SET_NOT_APPROVED")
            if proposal.candidate_rank != 1:
                raise PaperBlockedError("FIRST_CANDIDATE_REQUIRED")
            if any(
                reservation.proposal_id == proposal.proposal_id
                for reservation in state.reservations.values()
            ):
                raise PaperBlockedError(
                    "APPROVAL_REJECTION_AFTER_RESERVATION_FORBIDDEN"
                )
            if proposal.state not in {
                ProposalState.PROPOSED,
                ProposalState.ELIGIBILITY_CHECKED,
                ProposalState.TEMPORARILY_HELD,
            }:
                raise PaperBlockedError("LARGE_TRADE_REJECTION_STATE_INVALID")
            if self._proposal_has_open_attempt(state, proposal.proposal_id) or any(
                item.proposal_id == proposal.proposal_id
                for item in state.fills.values()
            ):
                raise PaperBlockedError("APPROVAL_REJECTION_AFTER_ATTEMPT_FORBIDDEN")
            if normalized_at < max(
                candidate_set.created_at,
                candidate_set.evidence_cutoff,
                proposal.price_condition.evidence_cutoff_at,
            ):
                raise PaperBlockedError("APPROVAL_REJECTION_TIME_INVALID")
            if self._large_group_amount_in_state(state, proposal) < LARGE_TRADE_THRESHOLD_KRW:
                raise PaperBlockedError("LARGE_TRADE_APPROVAL_NOT_REQUIRED")
            expected_group = self._large_group_target_in_state(state, proposal)
            if any(
                approval.approval_type is ApprovalType.LARGE_TRADE
                and approval.target == expected_group
                and approval.state
                not in {
                    ApprovalState.CANCELLED,
                    ApprovalState.INVALIDATED,
                    ApprovalState.EXPIRED,
                }
                for approval in state.approvals.values()
            ):
                raise PaperBlockedError("LARGE_TRADE_ALREADY_APPROVED")
            if normalized_at < self._proposal_state_time_floor(state, proposal):
                raise PaperBlockedError("APPROVAL_REJECTION_TIME_INVALID")
            self._require_audit_id_available(state, audit)
            state.proposals[proposal.proposal_id] = replace(
                proposal,
                state=ProposalState.CANCELLED,
            )
            updated_set = replace(
                candidate_set,
                state=CandidateSetState.REGENERATION_REQUIRED,
            )
            state.candidate_sets[candidate_set.set_id] = updated_set
            state.audits.append(
                AuditEvent(
                    audit,
                    "LARGE_TRADE_REJECTED",
                    proposal.proposal_id,
                    normalized_at,
                    actor_ref=user_ref,
                    evidence_ref=candidate_set.set_id,
                    reason_code="LARGE_TRADE_REJECTED",
                )
            )
            return updated_set

        return self._repository.transact(operation)

    def expire_proposal(
        self,
        proposal_id: str,
        *,
        expired_at: datetime,
        evidence_ref: str,
        audit_id: str,
    ) -> TradeProposal:
        """정확한 만료 경계에서 시도 여부와 예약을 함께 종결한다."""

        normalized_at = _utc(expired_at, field_name="proposal expired_at")
        evidence = nonempty(evidence_ref, field_name="proposal expiry evidence")
        audit = nonempty(audit_id, field_name="proposal expiry audit ID")

        def operation(state: _PaperState) -> TradeProposal:
            proposal = state.proposals.get(proposal_id)
            if proposal is None:
                raise PaperBlockedError("PROPOSAL_NOT_FOUND")
            if normalized_at < proposal.expires_at:
                raise PaperBlockedError("PROPOSAL_NOT_EXPIRED")
            if proposal.state in {
                ProposalState.EXPIRED,
                ProposalState.CANCELLED,
                ProposalState.MISSED_WINDOW,
                ProposalState.DEFINITIVELY_UNEXECUTABLE,
                ProposalState.RECONCILED,
                ProposalState.PAPER_RECORDED,
            }:
                raise PaperBlockedError("PROPOSAL_TERMINAL")
            if normalized_at < self._proposal_state_time_floor(state, proposal):
                raise PaperBlockedError("PROPOSAL_STATE_TIME_INVALID")
            self._require_audit_id_available(state, audit)
            if self._proposal_has_open_attempt(state, proposal.proposal_id) or proposal.state in {
                ProposalState.PAPER_EXECUTING,
                ProposalState.UNKNOWN,
                ProposalState.NEEDS_RECONCILIATION,
            }:
                for reservation_id, reservation in tuple(state.reservations.items()):
                    if (
                        reservation.proposal_id == proposal.proposal_id
                        and _active_reservation(reservation.state)
                    ):
                        state.reservations[reservation_id] = replace(
                            reservation,
                            state=ReservationState.NEEDS_RECONCILIATION,
                            reason_code="PROPOSAL_EXPIRED_RESULT_UNKNOWN",
                        )
                updated = replace(
                    proposal,
                    state=ProposalState.NEEDS_RECONCILIATION,
                )
                self._hold_open_attempt_runs_for_reconciliation(
                    state,
                    proposal_id=proposal.proposal_id,
                    at=normalized_at,
                    reason="PROPOSAL_EXPIRED_RESULT_UNKNOWN",
                )
                event_type = "PROPOSAL_EXPIRY_RECONCILIATION_REQUIRED"
            else:
                self._release_active_reservation_for_proposal(
                    state,
                    proposal_id=proposal.proposal_id,
                    released_at=normalized_at,
                    reason_code="PROPOSAL_EXPIRED_NO_ATTEMPT",
                )
                updated = replace(proposal, state=ProposalState.EXPIRED)
                self._hold_execution_runs_for_gate_loss(
                    state,
                    proposal_id=proposal.proposal_id,
                    at=normalized_at,
                    reason="PROPOSAL_EXPIRED",
                    evidence=audit,
                )
                event_type = "PROPOSAL_EXPIRED"
            state.proposals[proposal.proposal_id] = updated
            state.audits.append(
                AuditEvent(
                    audit,
                    event_type,
                    proposal.proposal_id,
                    normalized_at,
                    evidence_ref=evidence,
                )
            )
            return updated

        return self._repository.transact(operation)

    def bind_approval(
        self,
        *,
        approval_id: str,
        expected_type: ApprovalType,
        expected_target: ApprovalTarget,
        consumer_ref: str,
        now: datetime,
    ) -> Approval:
        _require_enum(expected_type, ApprovalType, field_name="approval type")
        if type(expected_target) is not ApprovalTarget:
            raise DomainValidationError("승인 대상 형식이 올바르지 않습니다.")
        normalized_now = _utc(now, field_name="approval check now")
        consumer = nonempty(consumer_ref, field_name="consumer_ref")

        def operation(state: _PaperState) -> Approval:
            approval = self._require_valid_approval(
                state,
                approval_id=approval_id,
                expected_type=expected_type,
                expected_target=expected_target,
                now=normalized_now,
                consumer_ref=consumer,
            )
            if approval.state is ApprovalState.APPROVED:
                approval = replace(
                    approval,
                    state=ApprovalState.BOUND,
                    bound_to_ref=consumer,
                    state_changed_at=normalized_now,
                )
                state.approvals[approval_id] = approval
            return approval

        return self._repository.transact(operation)

    def consume_new_root_approval(
        self,
        *,
        approval_id: str,
        expected_target: NewRootPurposeTarget,
        purpose_change_result: PurposeChangeResult,
        strategy_id: PaperStrategyKind,
        strategy_version_ref: str,
        budget_version_ref: str,
        policy_version_ref: str,
        intended_effect: str,
        created_at: datetime,
        audit_id: str,
        fault_injector: Callable[[], None] | None = None,
    ) -> TradePurpose:
        if type(expected_target) is not NewRootPurposeTarget:
            raise DomainValidationError("새 root 승인 대상 형식이 올바르지 않습니다.")
        _require_enum(
            purpose_change_result,
            PurposeChangeResult,
            field_name="purpose change result",
        )
        normalized_at = _utc(created_at, field_name="new purpose created_at")
        target = ApprovalTarget(
            expected_target.trade_purpose_id,
            expected_target.purpose_version,
            expected_target.purpose_version_hash,
        )

        def operation(state: _PaperState) -> TradePurpose:
            if purpose_change_result is PurposeChangeResult.SAME:
                raise PaperBlockedError("SAME_PURPOSE_MUST_KEEP_STABLE_ROOT")
            if purpose_change_result is not PurposeChangeResult.DIFFERENT:
                raise PaperBlockedError("PURPOSE_CHANGE_UNPROVEN")
            if expected_target.split_check_result is not SplitCheckResult.PASS:
                raise PaperBlockedError("ROOT_OVERLAP_SPLIT_CHECK_NOT_PASS")
            budget = state.budgets.get(budget_version_ref)
            if (
                expected_target.policy_version != policy_version_ref
                or budget is None
                or budget.policy_version != expected_target.policy_version
            ):
                raise PaperBlockedError("NEW_ROOT_POLICY_VERSION_MISMATCH")
            if expected_target.checked_at > normalized_at:
                raise PaperBlockedError("NEW_ROOT_CHECK_AFTER_CONSUMPTION")
            prior_purposes = tuple(
                item
                for item in state.purposes.values()
                if item.stable_root_trade_purpose_id == expected_target.prior_root_id
            )
            if not prior_purposes:
                raise PaperBlockedError("PRIOR_ROOT_NOT_FOUND")
            if any(item.intended_effect == intended_effect for item in prior_purposes):
                raise PaperBlockedError("SAME_PURPOSE_MUST_KEEP_STABLE_ROOT")
            if any(
                item.stable_root_trade_purpose_id == expected_target.proposed_root_id
                for item in state.purposes.values()
            ):
                raise PaperBlockedError("PROPOSED_ROOT_ALREADY_EXISTS")
            approval = self._require_valid_approval(
                state,
                approval_id=approval_id,
                expected_type=ApprovalType.NEW_ROOT_PURPOSE,
                expected_target=target,
                now=normalized_at,
                consumer_ref=expected_target.trade_purpose_id,
            )
            if approval.new_root_target != expected_target:
                raise PaperBlockedError("APPROVAL_TARGET_MISMATCH")
            if expected_target.trade_purpose_id in state.purposes:
                raise PaperBlockedError("TRADE_PURPOSE_IMMUTABLE")
            self._require_audit_id_available(state, audit_id)
            purpose = TradePurpose(
                trade_purpose_id=expected_target.trade_purpose_id,
                stable_root_trade_purpose_id=expected_target.proposed_root_id,
                purpose_version=expected_target.purpose_version,
                purpose_version_hash=expected_target.purpose_version_hash,
                strategy_id=strategy_id,
                strategy_version_ref=strategy_version_ref,
                budget_version_ref=budget_version_ref,
                policy_version_ref=policy_version_ref,
                intended_effect=intended_effect,
                created_at=normalized_at,
                changed_from_root_id=expected_target.prior_root_id,
                new_root_approval_ref=approval_id,
                new_root_audit_ref=audit_id,
            )
            self._require_purpose_references(state, purpose)
            budget = state.budgets[purpose.budget_version_ref]
            matching_applications = tuple(
                item
                for item in state.strategy_applications
                if item.strategy_version_id == purpose.strategy_version_ref
            )
            if (
                normalized_at < budget.approved_at
                or not matching_applications
                or normalized_at < matching_applications[-1].applied_at
            ):
                raise PaperBlockedError("NEW_ROOT_PURPOSE_CAUSAL_TIME_INVALID")
            state.approvals[approval_id] = replace(
                approval,
                state=ApprovalState.CONSUMED,
                bound_to_ref=purpose.trade_purpose_id,
                state_changed_at=normalized_at,
            )
            state.purposes[purpose.trade_purpose_id] = purpose
            state.audits.append(AuditEvent(audit_id, "NEW_ROOT_PURPOSE_CREATED", purpose.trade_purpose_id, normalized_at))
            if fault_injector is not None:
                fault_injector()
            return purpose

        return self._repository.transact(operation)

    def reserve_proposal(
        self,
        request: ReservationRequest,
        *,
        execution_policy: PaperExecutionPolicy,
        risk_context: PaperRiskContext,
        candidate_approval_id: str,
        large_approval_id: str | None = None,
    ) -> PaperReservation:
        if type(request) is not ReservationRequest:
            raise DomainValidationError("PAPER 예약 요청 형식이 올바르지 않습니다.")
        if type(execution_policy) is not PaperExecutionPolicy:
            raise DomainValidationError("PAPER 실행 정책 형식이 올바르지 않습니다.")
        if type(risk_context) is not PaperRiskContext:
            raise DomainValidationError("PAPER 위험 판정 문맥 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> PaperReservation:
            portfolio = self._require_portfolio(state)
            proposal = state.proposals.get(request.proposal_id)
            if proposal is None:
                raise PaperBlockedError("PROPOSAL_NOT_FOUND")
            purpose = state.purposes.get(proposal.trade_purpose_id)
            if purpose is None:
                raise PaperBlockedError("TRADE_PURPOSE_NOT_FOUND")
            self._require_purpose_references(state, purpose)
            if execution_policy.policy_version != purpose.policy_version_ref:
                raise PaperBlockedError("EXECUTION_POLICY_VERSION_MISMATCH")
            if request.created_at >= proposal.expires_at:
                raise PaperBlockedError("PROPOSAL_EXPIRED")
            candidate_set = state.candidate_sets.get(proposal.candidate_set_id)
            if candidate_set is None:
                raise PaperBlockedError("CANDIDATE_SET_NOT_FOUND")
            self._require_candidate_rank_available(state, proposal)
            if (
                request.created_at < purpose.created_at
                or request.created_at < candidate_set.created_at
                or request.created_at < candidate_set.evidence_cutoff
            ):
                raise PaperBlockedError("RESERVATION_CAUSAL_TIME_INVALID")
            if (
                proposal.pricing_cutoff_at is not None
                and proposal.pricing_cutoff_at > request.created_at
            ):
                raise PaperBlockedError("FUTURE_PROPOSAL_FX_FORBIDDEN")
            if proposal.state is not ProposalState.ELIGIBILITY_CHECKED:
                raise PaperBlockedError("PROPOSAL_NOT_RESERVABLE")
            proposal_price_checks = tuple(
                item
                for item in state.price_condition_checks
                if item.proposal_id == proposal.proposal_id
            )
            latest_price_check = (
                max(
                    proposal_price_checks,
                    key=lambda item: (
                        item.evaluated_at,
                        item.observation_cutoff_at,
                        item.observed_price.as_of,
                    ),
                )
                if proposal_price_checks
                else None
            )
            if (
                latest_price_check is None
                or latest_price_check.proposal_hash != proposal.proposal_hash
                or not latest_price_check.decision.permits_execution
                or latest_price_check.evaluated_at > request.created_at
            ):
                raise PaperBlockedError("PRICE_CONDITION_PASS_REQUIRED")
            if request.reservation_id in state.reservations or any(
                item.proposal_id == proposal.proposal_id and _active_reservation(item.state)
                for item in state.reservations.values()
            ):
                raise PaperBlockedError("ACTIVE_RESERVATION_ALREADY_EXISTS")
            self._require_ledger_entry_ids_available(
                state,
                f"reservation:{request.reservation_id}",
            )
            self._require_execution_policy(state, execution_policy, proposal)
            if risk_context.valuation.portfolio_id != portfolio.portfolio_id:
                raise PaperBlockedError("RISK_SNAPSHOT_PORTFOLIO_MISMATCH")
            self._require_risk_denominator_binding(state, proposal, risk_context)
            if (
                risk_context.valuation.as_of > request.created_at
                or risk_context.valuation.cutoff_at > request.created_at
            ):
                raise PaperBlockedError("FUTURE_RISK_SNAPSHOT_FORBIDDEN")
            stored_valuation = state.valuations.get(risk_context.valuation.snapshot_id)
            if stored_valuation is None or canonical_hash(stored_valuation) != canonical_hash(
                risk_context.valuation
            ):
                raise PaperBlockedError("VERIFIED_RISK_SNAPSHOT_REQUIRED")
            if (
                risk_context.valuation.source_state_hash is None
                or risk_context.valuation.source_state_hash
                != canonical_hash((state.cash, state.lots))
            ):
                raise PaperBlockedError("FRESH_RISK_SNAPSHOT_REQUIRED")
            if proposal.side is Side.BUY:
                risk_decision = assess_paper_risk(
                    valuation=risk_context.valuation,
                    classifications=risk_context.classification_map(),
                    proposed_instrument_id=proposal.instrument_id,
                    proposed_buy_gross_krw=proposal.amounts.gross_krw,
                    policy=risk_context.policy,
                    active_buy_exposures_krw=self._active_buy_exposures(
                        state,
                    ),
                    proposed_maximum_cost_krw=proposal.amounts.maximum_cost_krw,
                    active_buy_maximum_costs_krw=self._active_buy_maximum_costs(
                        state,
                    ),
                )
                if not risk_decision.permits_buy:
                    raise PaperBlockedError("RISK_GATE_NOT_PASS")
            if candidate_set.state is not CandidateSetState.SET_APPROVED:
                raise PaperBlockedError("CANDIDATE_SET_NOT_APPROVED")
            if (
                candidate_set.approval_ref is None
                or candidate_approval_id != candidate_set.approval_ref
            ):
                raise PaperBlockedError("CANDIDATE_SET_APPROVAL_MISMATCH")
            candidate_target = ApprovalTarget(candidate_set.set_id, candidate_set.version, candidate_set.set_hash)
            self._bind_valid_approval_in_state(
                state,
                approval_id=candidate_approval_id,
                expected_type=ApprovalType.CANDIDATE_SET,
                expected_target=candidate_target,
                now=request.created_at,
                consumer_ref=candidate_set.set_id,
            )
            group_amount = self._large_group_amount_in_state(state, proposal)
            if group_amount >= LARGE_TRADE_THRESHOLD_KRW:
                if large_approval_id is None:
                    raise PaperBlockedError("LARGE_TRADE_APPROVAL_REQUIRED")
                expected_group = self._large_group_target_in_state(state, proposal)
                large_approval = self._bind_valid_approval_in_state(
                    state,
                    approval_id=large_approval_id,
                    expected_type=ApprovalType.LARGE_TRADE,
                    expected_target=expected_group,
                    now=request.created_at,
                    consumer_ref=expected_group.target_id,
                )
                if large_approval.approved_at < self._large_group_evidence_floor(
                    state,
                    proposal,
                ):
                    raise PaperBlockedError("LARGE_TRADE_APPROVAL_BEFORE_EVIDENCE")
            limit = state.daily_limits.get(proposal.us_session_date)
            if limit is None:
                raise PaperBlockedError("DAILY_LIMIT_NOT_OPEN")
            if proposal.amounts.gross_krw > limit.available_krw:
                raise PaperBlockedError("DAILY_GROSS_LIMIT_EXCEEDED")

            self._claim_trade_dates(state, proposal)
            cash_amount = proposal.amounts.cash_required if proposal.side is Side.BUY else Decimal("0")
            strategy_budget_amount = (
                proposal.amounts.approval_judgment_krw
                if proposal.side is Side.BUY
                else Decimal("0")
            )
            monthly_applies = (
                execution_policy.monthly_budget_applies_to_buy
                if proposal.side is Side.BUY
                else execution_policy.monthly_budget_applies_to_sell
            )
            monthly_budget_amount = (
                proposal.amounts.approval_judgment_krw
                if monthly_applies is True
                else Decimal("0")
            )
            if proposal.side is Side.BUY:
                if request.sell_lot_quantities:
                    raise DomainValidationError("BUY 예약에는 매도 lot을 넣을 수 없습니다.")
                if _available_cash(state, proposal.amounts.cash_currency) < cash_amount:
                    raise PaperBlockedError("INSUFFICIENT_AVAILABLE_CASH")
                allocation = dict(
                    state.budgets[purpose.budget_version_ref].allocations_krw
                )[proposal.strategy_id]
                already_used = self._strategy_budget_used_or_reserved(
                    state,
                    budget_version_id=purpose.budget_version_ref,
                    strategy_id=proposal.strategy_id,
                )
                if already_used + strategy_budget_amount > allocation:
                    raise PaperBlockedError("STRATEGY_BUDGET_EXCEEDED")
            else:
                if sum(quantity for _, quantity in request.sell_lot_quantities) != proposal.quantity:
                    raise PaperBlockedError("SELL_RESERVATION_QUANTITY_MISMATCH")
                for lot_id, quantity in request.sell_lot_quantities:
                    lot = state.lots.get(lot_id)
                    if (
                        lot is None
                        or lot.instrument_id != proposal.instrument_id
                        or lot.currency is not proposal.amounts.cash_currency
                        or lot.strategy_id != proposal.strategy_id.value
                    ):
                        raise PaperBlockedError("SELL_LOT_NOT_FOUND")
                    if lot.remaining_quantity - _lot_reserved_quantity(state, lot_id) < quantity:
                        raise PaperBlockedError("INSUFFICIENT_AVAILABLE_QUANTITY")
                if execution_policy.cost_basis_method is CostBasisMethod.FIFO:
                    remaining = proposal.quantity
                    expected_fifo: list[tuple[str, int]] = []
                    eligible_lots = sorted(
                        (
                            lot
                            for lot in state.lots.values()
                            if lot.instrument_id == proposal.instrument_id
                            and lot.currency is proposal.amounts.cash_currency
                            and lot.strategy_id == proposal.strategy_id.value
                            and lot.remaining_quantity
                            - _lot_reserved_quantity(state, lot.lot_id)
                            > 0
                        ),
                        key=lambda lot: (lot.acquired_at, lot.lot_id),
                    )
                    for lot in eligible_lots:
                        if remaining == 0:
                            break
                        available = lot.remaining_quantity - _lot_reserved_quantity(
                            state,
                            lot.lot_id,
                        )
                        allocated = min(available, remaining)
                        expected_fifo.append((lot.lot_id, allocated))
                        remaining -= allocated
                    if remaining or request.sell_lot_quantities != tuple(expected_fifo):
                        raise PaperBlockedError("FIFO_SELL_RESERVATION_MISMATCH")

            reservation = PaperReservation(
                request.reservation_id,
                proposal.proposal_id,
                proposal.side,
                proposal.amounts.cash_currency,
                cash_amount,
                cash_amount,
                proposal.amounts.gross_krw,
                proposal.amounts.gross_krw,
                request.sell_lot_quantities,
                request.sell_lot_quantities,
                candidate_approval_id,
                large_approval_id,
                execution_policy.cost_basis_method,
                risk_context.valuation.snapshot_id,
                risk_context.policy.policy_version,
                canonical_hash((risk_context.classifications, risk_context.policy)),
                canonical_hash(execution_policy),
                purpose.budget_version_ref,
                proposal.strategy_id,
                strategy_budget_amount,
                strategy_budget_amount,
                monthly_budget_amount,
                monthly_budget_amount,
                request.created_at,
            )
            state.reservations[reservation.reservation_id] = reservation
            state.daily_limits[proposal.us_session_date] = replace(
                limit,
                active_reserved_gross_krw=limit.active_reserved_gross_krw + proposal.amounts.gross_krw,
            )
            state.proposals[proposal.proposal_id] = replace(proposal, state=ProposalState.READY)
            state.ledger_entries.append(
                PaperLedgerEntry(
                    f"reservation:{reservation.reservation_id}",
                    LedgerEventType.RESERVE,
                    portfolio.portfolio_id,
                    request.created_at,
                    request.created_at,
                    proposal.amounts.gross_krw,
                    Currency.KRW,
                    instrument_id=proposal.instrument_id,
                    quantity=proposal.quantity,
                    source_ref="PAPER_RESERVATION",
                )
            )
            return reservation

        return self._repository.transact(operation)

    def record_fill(
        self,
        fill: PaperFill,
        *,
        execution_policy: PaperExecutionPolicy,
        risk_context: PaperRiskContext,
        run_id: str,
        attempt_event_id: str,
        lot_id: str | None = None,
    ) -> PaperFill:
        if type(fill) is not PaperFill:
            raise DomainValidationError("PAPER fill 형식이 올바르지 않습니다.")
        if type(execution_policy) is not PaperExecutionPolicy:
            raise DomainValidationError("PAPER 실행 정책 형식이 올바르지 않습니다.")
        if type(risk_context) is not PaperRiskContext:
            raise DomainValidationError("PAPER 위험 판정 문맥 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> PaperFill:
            portfolio = self._require_portfolio(state)
            if fill.fill_id in state.fills:
                raise PaperBlockedError("DUPLICATE_FILL_ID")
            self._require_ledger_entry_ids_available(
                state,
                f"fill:{fill.fill_id}",
                *(
                    (f"fee:{fill.fill_id}",)
                    if fill.amounts.maximum_cost_trade_currency
                    else ()
                ),
            )
            proposal = state.proposals.get(fill.proposal_id)
            reservation = state.reservations.get(fill.reservation_id)
            if proposal is None or reservation is None or reservation.proposal_id != proposal.proposal_id:
                raise PaperBlockedError("FILL_RESERVATION_NOT_FOUND")
            self._require_candidate_rank_available(state, proposal)
            if reservation.state not in {ReservationState.ACTIVE}:
                raise PaperBlockedError("RESERVATION_NOT_FILLABLE")
            run = state.runs.get(run_id)
            if run is None or run.state is not RunState.RUNNING:
                raise PaperBlockedError("RUNNING_EXECUTION_RUN_REQUIRED")
            attempt = next(
                (
                    item
                    for item in run.attempts
                    if item.event_id == attempt_event_id
                    and item.proposal_id == proposal.proposal_id
                ),
                None,
            )
            if attempt is None or attempt.result is not AttemptResult.UNKNOWN:
                raise PaperBlockedError("OPEN_EXECUTION_ATTEMPT_REQUIRED")
            if fill.filled_at < attempt.attempted_at or fill.filled_at >= run.window_end:
                raise PaperBlockedError("FILL_OUTSIDE_EXECUTION_WINDOW")
            if fill.filled_at < reservation.created_at or fill.filled_at >= proposal.expires_at:
                raise PaperBlockedError("RETROACTIVE_FILL_FORBIDDEN")
            if fill.realized_pnl_trade_currency is not None:
                raise DomainValidationError("실현손익은 PAPER 엔진이 lot 원가에서 계산합니다.")
            observed_price = fill.observed_price
            assert observed_price is not None
            if (
                not observed_price.verified
                or observed_price.instrument_id != proposal.instrument_id
                or observed_price.currency is not proposal.amounts.cash_currency
                or observed_price.as_of < reservation.created_at
                or observed_price.as_of < attempt.attempted_at
                or observed_price.as_of > fill.observation_cutoff_at
                or fill.observation_cutoff_at > fill.filled_at
            ):
                raise PaperBlockedError("VERIFIED_FILL_PRICE_REQUIRED")
            assert proposal.price_condition is not None
            if not proposal.price_condition.is_met(observed_price.value):
                raise PaperBlockedError("PRICE_CONDITION_NOT_MET")
            with localcontext(_paper_decimal_context(exact=True)):
                if (
                    observed_price.value * Decimal(fill.quantity)
                    != fill.amounts.gross_trade_currency
                ):
                    raise PaperBlockedError("FILL_PRICE_AMOUNT_MISMATCH")
            if proposal.amounts.cash_currency is Currency.KRW:
                if fill.observed_fx_rate is not None:
                    raise PaperBlockedError("KRW_FILL_MUST_NOT_HAVE_FX")
            else:
                observed_fx = fill.observed_fx_rate
                proposal_fx = proposal.pricing_fx_rate
                if (
                    observed_fx is None
                    or proposal_fx is None
                    or not observed_fx.verified
                    or observed_fx.currency is not proposal.amounts.cash_currency
                    or observed_fx.as_of > fill.observation_cutoff_at
                    or observed_fx.krw_per_unit != fill.amounts.fx_krw_per_unit
                    or observed_fx.source_record_id != proposal_fx.source_record_id
                    or observed_fx.as_of != proposal_fx.as_of
                ):
                    raise PaperBlockedError("VERIFIED_FILL_FX_REQUIRED")
            if (
                risk_context.policy.policy_version != reservation.risk_policy_version
                or risk_context.valuation.portfolio_id != portfolio.portfolio_id
                or canonical_hash(
                    (risk_context.classifications, risk_context.policy)
                )
                != reservation.risk_context_hash
            ):
                raise PaperBlockedError("RISK_CONTEXT_CHANGED")
            self._require_risk_denominator_binding(state, proposal, risk_context)
            stored_valuation = state.valuations.get(risk_context.valuation.snapshot_id)
            if stored_valuation is None or canonical_hash(stored_valuation) != canonical_hash(
                risk_context.valuation
            ):
                raise PaperBlockedError("VERIFIED_RISK_SNAPSHOT_REQUIRED")
            if (
                risk_context.valuation.source_state_hash is None
                or risk_context.valuation.source_state_hash
                != canonical_hash((state.cash, state.lots))
                or risk_context.valuation.as_of > fill.filled_at
                or risk_context.valuation.cutoff_at > fill.filled_at
            ):
                raise PaperBlockedError("FRESH_RISK_SNAPSHOT_REQUIRED")
            if canonical_hash(execution_policy) != reservation.execution_policy_hash:
                raise PaperBlockedError("EXECUTION_POLICY_CHANGED")
            self._require_execution_policy(
                state,
                execution_policy,
                proposal,
                existing_reservation_id=reservation.reservation_id,
            )
            if proposal.side is Side.BUY:
                current_risk = assess_paper_risk(
                    valuation=risk_context.valuation,
                    classifications=risk_context.classification_map(),
                    proposed_instrument_id=proposal.instrument_id,
                    proposed_buy_gross_krw=reservation.remaining_daily_gross_krw,
                    policy=risk_context.policy,
                    active_buy_exposures_krw=self._active_buy_exposures(
                        state,
                        excluding_reservation_id=reservation.reservation_id,
                    ),
                    proposed_maximum_cost_krw=self._remaining_buy_maximum_cost_krw(
                        state,
                        proposal,
                    ),
                    active_buy_maximum_costs_krw=self._active_buy_maximum_costs(
                        state,
                        excluding_reservation_id=reservation.reservation_id,
                    ),
                )
                if not current_risk.permits_buy:
                    raise PaperBlockedError("RISK_GATE_NOT_PASS")
            candidate_set = state.candidate_sets[proposal.candidate_set_id]
            if candidate_set.state is not CandidateSetState.SET_APPROVED:
                raise PaperBlockedError("CANDIDATE_SET_NOT_APPROVED")
            self._require_valid_approval(
                state,
                approval_id=reservation.candidate_approval_id,
                expected_type=ApprovalType.CANDIDATE_SET,
                expected_target=ApprovalTarget(
                    candidate_set.set_id,
                    candidate_set.version,
                    candidate_set.set_hash,
                ),
                now=fill.filled_at,
                consumer_ref=candidate_set.set_id,
            )
            current_group_amount = self._large_group_amount_in_state(state, proposal)
            if current_group_amount >= LARGE_TRADE_THRESHOLD_KRW:
                if reservation.large_approval_id is None:
                    raise PaperBlockedError("LARGE_TRADE_APPROVAL_REQUIRED")
                current_group_target = self._large_group_target_in_state(state, proposal)
                large_approval = self._require_valid_approval(
                    state,
                    approval_id=reservation.large_approval_id,
                    expected_type=ApprovalType.LARGE_TRADE,
                    expected_target=current_group_target,
                    now=fill.filled_at,
                    consumer_ref=current_group_target.target_id,
                )
                if large_approval.approved_at < self._large_group_evidence_floor(
                    state,
                    proposal,
                ):
                    raise PaperBlockedError("LARGE_TRADE_APPROVAL_BEFORE_EVIDENCE")
            already_filled = sum(item.quantity for item in state.fills.values() if item.proposal_id == proposal.proposal_id)
            if already_filled + fill.quantity > proposal.quantity:
                raise PaperBlockedError("FILL_EXCEEDS_PROPOSAL_QUANTITY")
            if fill.amounts.cash_currency is not proposal.amounts.cash_currency:
                raise PaperBlockedError("FILL_CURRENCY_MISMATCH")
            if fill.amounts.fx_krw_per_unit != proposal.amounts.fx_krw_per_unit:
                raise PaperBlockedError("FILL_FX_SNAPSHOT_MISMATCH")
            prior_gross = sum(
                item.amounts.gross_trade_currency
                for item in state.fills.values()
                if item.proposal_id == proposal.proposal_id
            )
            prior_cost = sum(
                item.amounts.maximum_cost_trade_currency
                for item in state.fills.values()
                if item.proposal_id == proposal.proposal_id
            )
            prior_gross_krw = sum(
                (item.amounts.gross_krw for item in state.fills.values() if item.proposal_id == proposal.proposal_id),
                Decimal("0"),
            )
            prior_cost_krw = sum(
                (item.amounts.maximum_cost_krw for item in state.fills.values() if item.proposal_id == proposal.proposal_id),
                Decimal("0"),
            )
            if (
                prior_gross + fill.amounts.gross_trade_currency
                > proposal.amounts.gross_trade_currency
                or prior_cost + fill.amounts.maximum_cost_trade_currency
                > proposal.amounts.maximum_cost_trade_currency
                or prior_gross_krw + fill.amounts.gross_krw > proposal.amounts.gross_krw
                or prior_cost_krw + fill.amounts.maximum_cost_krw
                > proposal.amounts.maximum_cost_krw
            ):
                raise PaperBlockedError("FILL_EXCEEDS_PROPOSAL_AMOUNT")
            if fill.amounts.gross_krw > reservation.remaining_daily_gross_krw:
                raise PaperBlockedError("FILL_EXCEEDS_RESERVED_DAILY_GROSS")
            realized_pnl: Decimal | None = None
            if proposal.side is Side.BUY:
                if fill.amounts.cash_required > reservation.remaining_cash_amount:
                    raise PaperBlockedError("FILL_EXCEEDS_RESERVED_CASH")
                if state.cash.get(fill.amounts.cash_currency, Decimal("0")) < fill.amounts.cash_required:
                    raise PaperBlockedError("INSUFFICIENT_POSTED_CASH")
                state.cash[fill.amounts.cash_currency] -= fill.amounts.cash_required
                new_lot_id = nonempty(lot_id or fill.fill_id, field_name="new lot_id")
                if new_lot_id in state.lots:
                    raise PaperBlockedError("LOT_ID_DUPLICATE")
                with localcontext(_paper_decimal_context()):
                    unit_cost = fill.amounts.cash_required / Decimal(fill.quantity)
                state.lots[new_lot_id] = PaperPositionLot(
                    new_lot_id,
                    proposal.strategy_id.value,
                    proposal.instrument_id,
                    fill.filled_at,
                    fill.amounts.cash_currency,
                    unit_cost,
                    fill.amounts.cash_required,
                    fill.amounts.cash_required,
                    fill.quantity,
                    fill.quantity,
                    f"fill:{fill.fill_id}",
                )
            else:
                if execution_policy.cost_basis_method is None or reservation.cost_basis_method is None:
                    raise PaperBlockedError("COST_BASIS_POLICY_INPUT_REQUIRED")
                if execution_policy.cost_basis_method is not reservation.cost_basis_method:
                    raise PaperBlockedError("COST_BASIS_POLICY_MISMATCH")
                remaining_to_apply = fill.quantity
                ordered_lots = list(reservation.remaining_sell_lot_quantities)
                if execution_policy.cost_basis_method is CostBasisMethod.FIFO:
                    ordered_lots.sort(key=lambda item: state.lots[item[0]].acquired_at)
                allocated_cost = Decimal("0")
                applied_by_lot: dict[str, int] = {}
                for reserved_lot_id, reserved_quantity in ordered_lots:
                    if remaining_to_apply == 0:
                        break
                    apply_quantity = min(reserved_quantity, remaining_to_apply)
                    lot = state.lots[reserved_lot_id]
                    if lot.currency is not fill.amounts.cash_currency:
                        raise PaperBlockedError("SELL_LOT_CURRENCY_MISMATCH")
                    if apply_quantity == lot.remaining_quantity:
                        lot_cost = lot.remaining_cost
                    else:
                        with localcontext(_paper_decimal_context()):
                            lot_cost = (
                                lot.remaining_cost
                                * Decimal(apply_quantity)
                                / Decimal(lot.remaining_quantity)
                            )
                    allocated_cost += lot_cost
                    applied_by_lot[reserved_lot_id] = apply_quantity
                    state.lots[reserved_lot_id] = replace(
                        lot,
                        remaining_quantity=lot.remaining_quantity - apply_quantity,
                        remaining_cost=lot.remaining_cost - lot_cost,
                    )
                    remaining_to_apply -= apply_quantity
                if remaining_to_apply:
                    raise PaperBlockedError("FILL_EXCEEDS_RESERVED_SELL_QUANTITY")
                net_proceeds = fill.amounts.gross_trade_currency - fill.amounts.maximum_cost_trade_currency
                if net_proceeds < 0:
                    raise PaperBlockedError("SELL_COST_EXCEEDS_GROSS")
                state.cash[fill.amounts.cash_currency] = state.cash.get(fill.amounts.cash_currency, Decimal("0")) + net_proceeds
                realized_pnl = net_proceeds - allocated_cost

            remaining_cash = reservation.remaining_cash_amount
            if proposal.side is Side.BUY:
                remaining_cash -= fill.amounts.cash_required
            remaining_daily = reservation.remaining_daily_gross_krw - fill.amounts.gross_krw
            remaining_strategy_budget = reservation.remaining_strategy_budget_krw
            if proposal.side is Side.BUY:
                remaining_strategy_budget -= fill.amounts.approval_judgment_krw
            if remaining_strategy_budget < 0:
                raise PaperBlockedError("FILL_EXCEEDS_RESERVED_STRATEGY_BUDGET")
            remaining_monthly_budget = reservation.remaining_monthly_budget_krw
            if reservation.original_monthly_budget_krw:
                remaining_monthly_budget -= fill.amounts.approval_judgment_krw
                if remaining_monthly_budget < 0:
                    raise PaperBlockedError("FILL_EXCEEDS_RESERVED_MONTHLY_BUDGET")
            remaining_sell = reservation.remaining_sell_lot_quantities
            if proposal.side is Side.SELL:
                remaining_map = dict(reservation.remaining_sell_lot_quantities)
                for reserved_lot_id, applied in applied_by_lot.items():
                    remaining_map[reserved_lot_id] -= applied
                remaining_sell = tuple((key, value) for key, value in remaining_map.items() if value)
            complete = already_filled + fill.quantity == proposal.quantity
            completed_remainder = (
                remaining_cash,
                remaining_daily,
                remaining_strategy_budget,
                remaining_monthly_budget,
                remaining_sell,
            )
            has_completed_remainder = complete and any(
                (
                    remaining_cash,
                    remaining_daily,
                    remaining_strategy_budget,
                    remaining_monthly_budget,
                    bool(remaining_sell),
                )
            )
            remainder_release_entry_id = (
                f"release:{reservation.reservation_id}:fill:{fill.fill_id}"
            )
            remainder_release_audit_id = f"audit:{remainder_release_entry_id}"
            if has_completed_remainder:
                self._require_ledger_entry_ids_available(
                    state,
                    remainder_release_entry_id,
                )
                self._require_audit_id_available(
                    state,
                    remainder_release_audit_id,
                )
            updated_reservation = replace(
                reservation,
                remaining_cash_amount=Decimal("0") if complete else remaining_cash,
                remaining_daily_gross_krw=Decimal("0") if complete else remaining_daily,
                remaining_strategy_budget_krw=(
                    Decimal("0") if complete else remaining_strategy_budget
                ),
                remaining_monthly_budget_krw=(
                    Decimal("0") if complete else remaining_monthly_budget
                ),
                remaining_sell_lot_quantities=() if complete else remaining_sell,
                state=ReservationState.CONSUMED if complete else ReservationState.ACTIVE,
            )
            limit = state.daily_limits[proposal.us_session_date]
            state.daily_limits[proposal.us_session_date] = replace(
                limit,
                active_reserved_gross_krw=limit.active_reserved_gross_krw - fill.amounts.gross_krw - (remaining_daily if complete else Decimal("0")),
                executed_gross_krw=limit.executed_gross_krw + fill.amounts.gross_krw,
            )
            state.reservations[reservation.reservation_id] = updated_reservation
            recorded_fill = replace(fill, realized_pnl_trade_currency=realized_pnl)
            state.fills[fill.fill_id] = recorded_fill
            state.proposals[proposal.proposal_id] = replace(
                proposal,
                state=ProposalState.PAPER_RECORDED if complete else ProposalState.PAPER_EXECUTING,
            )
            if complete:
                state.runs[run_id] = replace(
                    run,
                    attempts=tuple(
                        replace(item, result=AttemptResult.FILL_RECORDED)
                        if item.event_id == attempt_event_id
                        else item
                        for item in run.attempts
                    ),
                )
            state.ledger_entries.append(
                PaperLedgerEntry(
                    f"fill:{fill.fill_id}",
                    LedgerEventType.BUY if proposal.side is Side.BUY else LedgerEventType.SELL,
                    portfolio.portfolio_id,
                    fill.filled_at,
                    fill.filled_at,
                    -fill.amounts.gross_trade_currency if proposal.side is Side.BUY else fill.amounts.gross_trade_currency,
                    fill.amounts.cash_currency,
                    strategy_id=proposal.strategy_id.value,
                    instrument_id=proposal.instrument_id,
                    quantity=fill.quantity,
                    source_ref="INTERNAL_PAPER_FILL",
                )
            )
            if fill.amounts.maximum_cost_trade_currency:
                state.ledger_entries.append(
                    PaperLedgerEntry(
                        f"fee:{fill.fill_id}",
                        LedgerEventType.FEE,
                        portfolio.portfolio_id,
                        fill.filled_at,
                        fill.filled_at,
                        -fill.amounts.maximum_cost_trade_currency,
                        fill.amounts.cash_currency,
                        strategy_id=proposal.strategy_id.value,
                        instrument_id=proposal.instrument_id,
                        source_ref="INTERNAL_PAPER_FILL_COST",
                    )
                )
            if has_completed_remainder:
                state.ledger_entries.append(
                    PaperLedgerEntry(
                        remainder_release_entry_id,
                        LedgerEventType.RELEASE,
                        portfolio.portfolio_id,
                        fill.filled_at,
                        fill.filled_at,
                        remaining_daily,
                        Currency.KRW,
                        instrument_id=proposal.instrument_id,
                        source_ref="PAPER_COMPLETE_FILL_REMAINDER_RELEASE",
                    )
                )
                state.audits.append(
                    AuditEvent(
                        remainder_release_audit_id,
                        "RESERVATION_REMAINDER_RELEASED",
                        reservation.reservation_id,
                        fill.filled_at,
                        evidence_ref=canonical_hash(completed_remainder),
                        reason_code="COMPLETE_FILL_PRICE_OR_COST_IMPROVEMENT",
                    )
                )
            return recorded_fill

        return self._repository.transact(operation)

    def mark_reservation_unknown(
        self,
        reservation_id: str,
        *,
        run_id: str,
        attempt_event_id: str,
        at: datetime,
        reason_code: str,
    ) -> None:
        normalized_at = _utc(at, field_name="unknown at")
        reason = nonempty(reason_code, field_name="reason_code")

        def operation(state: _PaperState) -> None:
            reservation = state.reservations.get(reservation_id)
            if reservation is None or reservation.state is not ReservationState.ACTIVE:
                raise PaperBlockedError("RESERVATION_NOT_ACTIVE")
            run = state.runs.get(run_id)
            if run is None or run.state not in {
                RunState.RUNNING,
                RunState.RECONCILIATION_REQUIRED,
            }:
                raise PaperBlockedError("RUNNING_EXECUTION_RUN_REQUIRED")
            attempt = next(
                (
                    item
                    for item in run.attempts
                    if item.event_id == attempt_event_id
                    and item.proposal_id == reservation.proposal_id
                    and item.result is AttemptResult.UNKNOWN
                ),
                None,
            )
            if attempt is None:
                raise PaperBlockedError("OPEN_EXECUTION_ATTEMPT_REQUIRED")
            if normalized_at < max(
                reservation.created_at,
                attempt.attempted_at,
                self._run_state_time_floor(state, run),
            ):
                raise PaperBlockedError("RESERVATION_STATE_TIME_INVALID")
            unknown_audit_id = f"unknown:{reservation_id}"
            self._require_audit_id_available(state, unknown_audit_id)
            state.reservations[reservation_id] = replace(
                reservation,
                state=ReservationState.NEEDS_RECONCILIATION,
                reason_code=reason,
            )
            proposal = state.proposals[reservation.proposal_id]
            state.proposals[proposal.proposal_id] = replace(proposal, state=ProposalState.NEEDS_RECONCILIATION)
            state.runs[run_id] = replace(
                run,
                state=RunState.RECONCILIATION_REQUIRED,
                terminal_reason="ATTEMPT_RESULT_UNKNOWN",
                state_reason_code=reason,
                state_changed_at=normalized_at,
            )
            state.audits.append(AuditEvent(unknown_audit_id, "RESERVATION_UNKNOWN", reservation_id, normalized_at))

        self._repository.transact(operation)

    def reconcile_reservation_no_fill(
        self,
        reservation_id: str,
        *,
        run_id: str,
        attempt_event_id: str,
        reconciled_at: datetime,
        evidence_ref: str,
        audit_id: str,
    ) -> None:
        normalized_at = _utc(reconciled_at, field_name="reservation reconciled_at")
        nonempty(evidence_ref, field_name="reconciliation evidence")

        def operation(state: _PaperState) -> None:
            reservation = state.reservations.get(reservation_id)
            if reservation is None or reservation.state not in {
                ReservationState.UNKNOWN,
                ReservationState.NEEDS_RECONCILIATION,
            }:
                raise PaperBlockedError("RESERVATION_NOT_RECONCILABLE")
            self._require_audit_id_available(state, audit_id)
            self._require_ledger_entry_ids_available(
                state,
                f"release:{reservation_id}",
            )
            run = state.runs.get(run_id)
            if run is None or run.state is not RunState.RECONCILIATION_REQUIRED:
                raise PaperBlockedError("EXECUTION_RUN_NOT_RECONCILABLE")
            attempt = next(
                (
                    item
                    for item in run.attempts
                    if item.event_id == attempt_event_id
                    and item.proposal_id == reservation.proposal_id
                ),
                None,
            )
            if attempt is None or attempt.result is not AttemptResult.UNKNOWN:
                raise PaperBlockedError("UNKNOWN_ATTEMPT_REQUIRED")
            proposal = state.proposals[reservation.proposal_id]
            proposal_fills = tuple(
                item
                for item in state.fills.values()
                if item.proposal_id == proposal.proposal_id
            )
            related_approvals = tuple(
                state.approvals[approval_id]
                for approval_id in (
                    reservation.candidate_approval_id,
                    reservation.large_approval_id,
                )
                if approval_id is not None and approval_id in state.approvals
            )
            reservation_audits = tuple(
                item
                for item in state.audits
                if item.related_ref == reservation.reservation_id
            )
            causal_floor = max(
                (
                    reservation.created_at,
                    attempt.attempted_at,
                    run.ended_at or attempt.attempted_at,
                    *(item.filled_at for item in proposal_fills),
                    *(item.recorded_at for item in reservation_audits),
                    *(
                        approval.state_changed_at or approval.approved_at
                        for approval in related_approvals
                    ),
                )
            )
            if normalized_at < causal_floor:
                raise PaperBlockedError("RECONCILIATION_TIME_INVALID")
            reconciled_result = (
                AttemptResult.FILL_RECORDED
                if proposal_fills
                else AttemptResult.NO_FILL
            )
            proposal_state = (
                ProposalState.PAPER_RECORDED
                if proposal_fills
                else ProposalState.RECONCILED
            )
            reconciliation_reason = (
                "RECONCILED_PARTIAL_FILL_REMAINDER"
                if proposal_fills
                else "RECONCILED_NO_FILL"
            )
            limit = state.daily_limits[proposal.us_session_date]
            state.daily_limits[proposal.us_session_date] = replace(
                limit,
                active_reserved_gross_krw=(
                    limit.active_reserved_gross_krw
                    - reservation.remaining_daily_gross_krw
                ),
            )
            state.reservations[reservation_id] = replace(
                reservation,
                remaining_cash_amount=Decimal("0"),
                remaining_daily_gross_krw=Decimal("0"),
                remaining_strategy_budget_krw=Decimal("0"),
                remaining_monthly_budget_krw=Decimal("0"),
                remaining_sell_lot_quantities=(),
                state=ReservationState.RELEASED,
                released_at=normalized_at,
                reason_code=reconciliation_reason,
            )
            state.proposals[proposal.proposal_id] = replace(
                proposal,
                state=proposal_state,
            )
            state.runs[run_id] = replace(
                run,
                attempts=tuple(
                    replace(item, result=reconciled_result)
                    if item.event_id == attempt_event_id
                    else item
                    for item in run.attempts
                ),
            )
            state.ledger_entries.append(
                PaperLedgerEntry(
                    f"release:{reservation_id}",
                    LedgerEventType.RELEASE,
                    state.portfolio.portfolio_id,
                    normalized_at,
                    normalized_at,
                    reservation.remaining_daily_gross_krw,
                    Currency.KRW,
                    instrument_id=proposal.instrument_id,
                    source_ref="PAPER_RECONCILIATION_RELEASE",
                )
            )
            state.audits.append(
                AuditEvent(
                    audit_id,
                    "RESERVATION_RECONCILED_PARTIAL_FILL"
                    if proposal_fills
                    else "RESERVATION_RECONCILED_NO_FILL",
                    reservation_id,
                    normalized_at,
                    evidence_ref=evidence_ref,
                )
            )

        self._repository.transact(operation)

    def release_reservation(self, reservation_id: str, *, released_at: datetime, reason_code: str) -> None:
        normalized_at = _utc(released_at, field_name="released_at")
        reason = nonempty(reason_code, field_name="release reason")

        def operation(state: _PaperState) -> None:
            portfolio = self._require_portfolio(state)
            reservation = state.reservations.get(reservation_id)
            if reservation is None:
                raise PaperBlockedError("RESERVATION_NOT_FOUND")
            if reservation.state in {ReservationState.UNKNOWN, ReservationState.NEEDS_RECONCILIATION}:
                raise PaperBlockedError("RECONCILIATION_REQUIRED_RESERVATION_RETAINED")
            if reservation.state is not ReservationState.ACTIVE:
                raise PaperBlockedError("RESERVATION_NOT_ACTIVE")
            self._require_ledger_entry_ids_available(
                state,
                f"release:{reservation_id}",
            )
            if normalized_at < reservation.created_at:
                raise PaperBlockedError("RESERVATION_STATE_TIME_INVALID")
            proposal = state.proposals[reservation.proposal_id]
            if normalized_at < self._proposal_state_time_floor(state, proposal):
                raise PaperBlockedError("RESERVATION_STATE_TIME_INVALID")
            if self._proposal_has_open_attempt(state, proposal.proposal_id):
                raise PaperBlockedError("EXECUTION_RESULT_RECONCILIATION_REQUIRED")
            limit = state.daily_limits[proposal.us_session_date]
            state.daily_limits[proposal.us_session_date] = replace(
                limit,
                active_reserved_gross_krw=limit.active_reserved_gross_krw - reservation.remaining_daily_gross_krw,
            )
            state.reservations[reservation_id] = replace(
                reservation,
                remaining_cash_amount=Decimal("0"),
                remaining_daily_gross_krw=Decimal("0"),
                remaining_strategy_budget_krw=Decimal("0"),
                remaining_monthly_budget_krw=Decimal("0"),
                remaining_sell_lot_quantities=(),
                state=ReservationState.RELEASED,
                released_at=normalized_at,
                reason_code=reason,
            )
            state.proposals[proposal.proposal_id] = replace(
                proposal,
                state=ProposalState.CANCELLED,
            )
            self._hold_execution_runs_for_gate_loss(
                state,
                proposal_id=proposal.proposal_id,
                at=normalized_at,
                reason=reason,
                evidence=reservation_id,
            )
            state.ledger_entries.append(
                PaperLedgerEntry(
                    f"release:{reservation_id}",
                    LedgerEventType.RELEASE,
                    portfolio.portfolio_id,
                    normalized_at,
                    normalized_at,
                    reservation.remaining_daily_gross_krw,
                    Currency.KRW,
                    instrument_id=proposal.instrument_id,
                    source_ref="PAPER_RESERVATION_RELEASE",
                )
            )

        self._repository.transact(operation)

    def create_execution_run(self, run: ExecutionRun, *, gates: GateDecision) -> None:
        if type(run) is not ExecutionRun:
            raise DomainValidationError("ExecutionRun 형식이 올바르지 않습니다.")
        if type(gates) is not GateDecision:
            raise DomainValidationError("ExecutionRun gate 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> None:
            portfolio = self._require_portfolio(state)
            if run.portfolio_id != portfolio.portfolio_id:
                raise PaperBlockedError("RUN_PORTFOLIO_MISMATCH")
            if run.execution_run_id in state.runs:
                raise PaperBlockedError("EXECUTION_RUN_IMMUTABLE")
            if run.state not in {RunState.DISABLED, RunState.READY}:
                raise PaperBlockedError("NEW_EXECUTION_RUN_STATE_INVALID")
            if run.state is RunState.READY and not gates.permits_execution:
                raise PaperBlockedError("EXECUTION_RUN_GATES_NOT_PASS")
            if run.state is RunState.DISABLED and gates.permits_execution:
                raise PaperBlockedError("DISABLED_RUN_CANNOT_HAVE_PASSING_GATES")
            for proposal_id, version in run.included_proposals:
                proposal = state.proposals.get(proposal_id)
                if proposal is None or proposal.version != version or proposal.us_session_date != run.us_session_date:
                    raise PaperBlockedError("RUN_PROPOSAL_SCOPE_MISMATCH")
                if run.state is RunState.READY and proposal.state is not ProposalState.READY:
                    raise PaperBlockedError("RUN_PROPOSAL_NOT_READY")
                if run.state is RunState.READY and not any(
                    reservation.proposal_id == proposal_id
                    and reservation.state is ReservationState.ACTIVE
                    for reservation in state.reservations.values()
                ):
                    raise PaperBlockedError("ACTIVE_RESERVATION_REQUIRED")
                if run.state is RunState.READY and any(
                    proposal_id == existing_proposal_id
                    and existing.state
                    not in {
                        RunState.MISSED_WINDOW,
                        RunState.COMPLETED,
                        RunState.RECONCILED,
                    }
                    for existing in state.runs.values()
                    for existing_proposal_id, _ in existing.included_proposals
                ):
                    raise PaperBlockedError("PROPOSAL_ALREADY_CLAIMED_BY_EXECUTION_RUN")
            if run.attempts or run.completion_kind is not CompletionKind.NONE or run.ended_at is not None:
                raise DomainValidationError("새 ExecutionRun은 시도·종료 결과가 없어야 합니다.")
            state.runs[run.execution_run_id] = run

        self._repository.transact(operation)

    def record_execution_attempt(
        self,
        *,
        run_id: str,
        event_id: str,
        proposal_id: str,
        attempted_at: datetime,
    ) -> ExecutionRun:
        attempt = ExecutionAttempt(event_id, proposal_id, attempted_at)

        def operation(state: _PaperState) -> ExecutionRun:
            run = state.runs.get(run_id)
            if run is None:
                raise PaperBlockedError("EXECUTION_RUN_NOT_FOUND")
            if run.state not in {RunState.READY, RunState.RUNNING}:
                raise PaperBlockedError("EXECUTION_RUN_NOT_ATTEMPTABLE")
            if attempt.attempted_at < run.window_start or attempt.attempted_at >= run.window_end:
                raise PaperBlockedError("ATTEMPT_OUTSIDE_EXECUTION_WINDOW")
            if not any(item[0] == proposal_id for item in run.included_proposals):
                raise PaperBlockedError("ATTEMPT_PROPOSAL_NOT_IN_RUN")
            for existing_run in state.runs.values():
                for existing in existing_run.attempts:
                    if existing.event_id != event_id:
                        continue
                    if (
                        existing_run.execution_run_id == run_id
                        and existing == attempt
                    ):
                        return run
                    raise PaperBlockedError("ATTEMPT_EVENT_ID_REUSE_FORBIDDEN")
            if attempt.attempted_at < self._run_state_time_floor(state, run):
                raise PaperBlockedError("ATTEMPT_TIME_ROLLBACK")
            proposal = state.proposals[proposal_id]
            if attempt.attempted_at >= proposal.expires_at:
                raise PaperBlockedError("PROPOSAL_EXPIRED")
            if proposal.state is not ProposalState.READY:
                raise PaperBlockedError("ATTEMPT_PROPOSAL_NOT_READY")
            reservation = next(
                (
                    item
                    for item in state.reservations.values()
                    if item.proposal_id == proposal_id and item.state is ReservationState.ACTIVE
                ),
                None,
            )
            if reservation is None:
                raise PaperBlockedError("ACTIVE_RESERVATION_REQUIRED")
            if attempt.attempted_at < reservation.created_at:
                raise PaperBlockedError("ATTEMPT_BEFORE_RESERVATION")
            candidate_set = state.candidate_sets[proposal.candidate_set_id]
            if candidate_set.state is not CandidateSetState.SET_APPROVED:
                raise PaperBlockedError("CANDIDATE_SET_NOT_APPROVED")
            self._require_valid_approval(
                state,
                approval_id=reservation.candidate_approval_id,
                expected_type=ApprovalType.CANDIDATE_SET,
                expected_target=ApprovalTarget(
                    candidate_set.set_id,
                    candidate_set.version,
                    candidate_set.set_hash,
                ),
                now=attempt.attempted_at,
                consumer_ref=candidate_set.set_id,
            )
            if self._large_group_amount_in_state(state, proposal) >= LARGE_TRADE_THRESHOLD_KRW:
                if reservation.large_approval_id is None:
                    raise PaperBlockedError("LARGE_TRADE_APPROVAL_REQUIRED")
                group_target = self._large_group_target_in_state(state, proposal)
                self._require_valid_approval(
                    state,
                    approval_id=reservation.large_approval_id,
                    expected_type=ApprovalType.LARGE_TRADE,
                    expected_target=group_target,
                    now=attempt.attempted_at,
                    consumer_ref=group_target.target_id,
                )
            updated = replace(
                run,
                state=RunState.RUNNING,
                attempts=run.attempts + (attempt,),
                started_at=run.started_at or attempt.attempted_at,
            )
            state.runs[run_id] = updated
            state.proposals[proposal_id] = replace(proposal, state=ProposalState.PAPER_EXECUTING)
            return updated

        return self._repository.transact(operation)

    def confirm_attempt(
        self,
        *,
        run_id: str,
        event_id: str,
        result: AttemptResult,
        confirmed_at: datetime,
    ) -> ExecutionRun:
        normalized_at = _utc(confirmed_at, field_name="attempt confirmed_at")
        if result is AttemptResult.UNKNOWN:
            raise DomainValidationError("확인 결과는 UNKNOWN일 수 없습니다.")

        def operation(state: _PaperState) -> ExecutionRun:
            run = state.runs.get(run_id)
            if run is None:
                raise PaperBlockedError("EXECUTION_RUN_NOT_FOUND")
            if run.state is not RunState.RUNNING:
                raise PaperBlockedError("EXECUTION_RUN_NOT_CONFIRMABLE")
            if not any(item.event_id == event_id for item in run.attempts):
                raise PaperBlockedError("ATTEMPT_NOT_FOUND")
            attempt = next(item for item in run.attempts if item.event_id == event_id)
            if normalized_at < max(
                attempt.attempted_at,
                self._run_state_time_floor(state, run),
            ):
                raise PaperBlockedError("ATTEMPT_CONFIRMATION_TIME_INVALID")
            proposal = state.proposals[attempt.proposal_id]
            has_recorded_fill = any(
                item.proposal_id == proposal.proposal_id
                for item in state.fills.values()
            )
            if result is AttemptResult.FILL_RECORDED:
                if proposal.state is not ProposalState.PAPER_RECORDED:
                    raise PaperBlockedError("RECORDED_FILL_REQUIRED")
                latest_fill_at = max(
                    item.filled_at
                    for item in state.fills.values()
                    if item.proposal_id == proposal.proposal_id
                )
                if normalized_at < latest_fill_at:
                    raise PaperBlockedError("ATTEMPT_CONFIRMATION_TIME_INVALID")
            else:
                if has_recorded_fill:
                    raise PaperBlockedError("NO_FILL_AFTER_RECORDED_FILL_FORBIDDEN")
                self._release_active_reservation_for_proposal(
                    state,
                    proposal_id=proposal.proposal_id,
                    released_at=normalized_at,
                    reason_code="CONFIRMED_NO_FILL",
                )
                state.proposals[proposal.proposal_id] = replace(
                    proposal,
                    state=ProposalState.CANCELLED,
                )
            attempts = tuple(
                replace(item, result=result) if item.event_id == event_id else item
                for item in run.attempts
            )
            updated = replace(run, attempts=attempts)
            state.runs[run_id] = updated
            return updated

        return self._repository.transact(operation)

    def finalize_partial_fill(
        self,
        *,
        run_id: str,
        event_id: str,
        confirmed_at: datetime,
        evidence_ref: str,
        audit_id: str,
    ) -> ExecutionRun:
        """기록된 부분 체결을 확정하고 미체결 예약만 원자적으로 해제한다."""

        normalized_at = _utc(confirmed_at, field_name="partial fill confirmed_at")
        evidence = nonempty(evidence_ref, field_name="partial fill evidence")

        def operation(state: _PaperState) -> ExecutionRun:
            run = state.runs.get(run_id)
            if run is None or run.state is not RunState.RUNNING:
                raise PaperBlockedError("EXECUTION_RUN_NOT_CONFIRMABLE")
            attempt = next(
                (item for item in run.attempts if item.event_id == event_id),
                None,
            )
            if attempt is None or attempt.result is not AttemptResult.UNKNOWN:
                raise PaperBlockedError("UNKNOWN_ATTEMPT_REQUIRED")
            latest_fill_at = max(
                (
                    item.filled_at
                    for item in state.fills.values()
                    if item.proposal_id == attempt.proposal_id
                ),
                default=None,
            )
            if normalized_at < self._run_state_time_floor(state, run) or (
                latest_fill_at is not None and normalized_at < latest_fill_at
            ):
                raise PaperBlockedError("PARTIAL_FILL_CONFIRMATION_TIME_INVALID")
            proposal = state.proposals[attempt.proposal_id]
            reservation = next(
                (
                    item
                    for item in state.reservations.values()
                    if item.proposal_id == proposal.proposal_id
                    and item.state is ReservationState.ACTIVE
                ),
                None,
            )
            filled_quantity = sum(
                item.quantity
                for item in state.fills.values()
                if item.proposal_id == proposal.proposal_id
            )
            if reservation is None or not 0 < filled_quantity < proposal.quantity:
                raise PaperBlockedError("PARTIAL_FILL_STATE_REQUIRED")
            self._require_audit_id_available(state, audit_id)
            self._release_active_reservation_for_proposal(
                state,
                proposal_id=proposal.proposal_id,
                released_at=normalized_at,
                reason_code="PARTIAL_FILL_REMAINDER_CANCELLED",
            )
            state.proposals[proposal.proposal_id] = replace(
                proposal,
                state=ProposalState.PAPER_RECORDED,
            )
            updated = replace(
                run,
                attempts=tuple(
                    replace(item, result=AttemptResult.FILL_RECORDED)
                    if item.event_id == event_id
                    else item
                    for item in run.attempts
                ),
            )
            state.runs[run_id] = updated
            state.audits.append(
                AuditEvent(
                    audit_id,
                    "PARTIAL_FILL_CONFIRMED",
                    reservation.reservation_id,
                    normalized_at,
                    evidence_ref=evidence,
                )
            )
            return updated

        return self._repository.transact(operation)

    def hold_execution_run(
        self,
        run_id: str,
        *,
        held_at: datetime,
        reason_code: str,
        evidence_ref: str,
        audit_id: str,
    ) -> ExecutionRun:
        normalized_at = _utc(held_at, field_name="run held_at")
        reason = nonempty(reason_code, field_name="run hold reason")
        evidence = nonempty(evidence_ref, field_name="run hold evidence")
        audit = nonempty(audit_id, field_name="run hold audit ID")

        def operation(state: _PaperState) -> ExecutionRun:
            run = state.runs.get(run_id)
            if run is None or run.state not in {RunState.READY, RunState.RUNNING}:
                raise PaperBlockedError("EXECUTION_RUN_NOT_HOLDABLE")
            if (
                normalized_at < self._run_state_time_floor(state, run)
                or normalized_at >= run.window_end
            ):
                raise PaperBlockedError("EXECUTION_RUN_STATE_TIME_INVALID")
            for proposal_id, _version in run.included_proposals:
                proposal_attempts = tuple(
                    item
                    for item in run.attempts
                    if item.proposal_id == proposal_id
                )
                if proposal_attempts:
                    if any(
                        item.result is AttemptResult.UNKNOWN
                        for item in proposal_attempts
                    ):
                        raise PaperBlockedError("EXECUTION_RUN_GATES_NOT_PASS")
                    continue
                proposal = state.proposals[proposal_id]
                if proposal.state not in {
                    ProposalState.TEMPORARILY_HELD,
                    ProposalState.USER_STOPPED,
                    ProposalState.READY,
                } or not any(
                    reservation.proposal_id == proposal_id
                    and reservation.state is ReservationState.ACTIVE
                    for reservation in state.reservations.values()
                ):
                    raise PaperBlockedError("EXECUTION_RUN_GATES_NOT_PASS")
            self._require_audit_id_available(state, audit)
            updated = replace(
                run,
                state=RunState.TEMPORARILY_HELD,
                state_reason_code=reason,
                state_evidence_ref=evidence,
                state_changed_at=normalized_at,
            )
            state.runs[run_id] = updated
            for proposal_id, _version in run.included_proposals:
                if any(
                    item.proposal_id == proposal_id
                    for item in run.attempts
                ):
                    continue
                proposal = state.proposals[proposal_id]
                if proposal.state in {
                    ProposalState.READY,
                    ProposalState.ELIGIBILITY_CHECKED,
                }:
                    state.proposals[proposal_id] = replace(
                        proposal,
                        state=ProposalState.TEMPORARILY_HELD,
                    )
            state.audits.append(AuditEvent(audit, "EXECUTION_RUN_HELD", run_id, normalized_at))
            return updated

        return self._repository.transact(operation)

    def stop_execution_run(
        self,
        run_id: str,
        *,
        stopped_at: datetime,
        reason_code: str,
        evidence_ref: str,
        audit_id: str,
    ) -> ExecutionRun:
        normalized_at = _utc(stopped_at, field_name="run stopped_at")
        reason = nonempty(reason_code, field_name="run stop reason")
        evidence = nonempty(evidence_ref, field_name="run stop evidence")
        audit = nonempty(audit_id, field_name="run stop audit ID")

        def operation(state: _PaperState) -> ExecutionRun:
            run = state.runs.get(run_id)
            if run is None or run.state not in {RunState.READY, RunState.RUNNING, RunState.TEMPORARILY_HELD}:
                raise PaperBlockedError("EXECUTION_RUN_NOT_STOPPABLE")
            if (
                normalized_at < self._run_state_time_floor(state, run)
                or normalized_at >= run.window_end
            ):
                raise PaperBlockedError("EXECUTION_RUN_STATE_TIME_INVALID")
            self._require_audit_id_available(state, audit)
            has_unresolved_attempt = any(
                item.result is AttemptResult.UNKNOWN
                for item in run.attempts
            )
            updated = replace(
                run,
                state=(
                    RunState.RECONCILIATION_REQUIRED
                    if has_unresolved_attempt
                    else RunState.USER_STOPPED
                ),
                terminal_reason=(
                    "USER_STOPPED_RESULT_UNKNOWN"
                    if has_unresolved_attempt
                    else run.terminal_reason
                ),
                state_reason_code=reason,
                state_evidence_ref=evidence,
                state_changed_at=normalized_at,
            )
            state.runs[run_id] = updated
            for proposal_id, _version in run.included_proposals:
                proposal = state.proposals[proposal_id]
                if proposal.state in {
                    ProposalState.READY,
                    ProposalState.TEMPORARILY_HELD,
                    ProposalState.ELIGIBILITY_CHECKED,
                }:
                    state.proposals[proposal_id] = replace(
                        proposal,
                        state=ProposalState.USER_STOPPED,
                    )
            state.audits.append(
                AuditEvent(
                    audit,
                    (
                        "EXECUTION_RUN_USER_STOPPED_RECONCILIATION_REQUIRED"
                        if has_unresolved_attempt
                        else "EXECUTION_RUN_USER_STOPPED"
                    ),
                    run_id,
                    normalized_at,
                )
            )
            return updated

        return self._repository.transact(operation)

    def reactivate_execution_run(
        self,
        run_id: str,
        *,
        explicitly_requested: bool,
        gates: GateDecision,
        requested_at: datetime | None = None,
        evidence_ref: str | None = None,
        audit_id: str | None = None,
    ) -> ExecutionRun:
        _require_bool(explicitly_requested, field_name="explicit reactivation")
        if type(gates) is not GateDecision:
            raise DomainValidationError("ExecutionRun gate 형식이 올바르지 않습니다.")

        def operation(state: _PaperState) -> ExecutionRun:
            run = state.runs.get(run_id)
            if run is None or run.state not in {RunState.TEMPORARILY_HELD, RunState.USER_STOPPED}:
                raise PaperBlockedError("EXECUTION_RUN_NOT_REACTIVATABLE")
            if not explicitly_requested or not gates.permits_execution:
                raise PaperBlockedError("EXPLICIT_REACTIVATION_AND_RECHECK_REQUIRED")
            if requested_at is None or evidence_ref is None or audit_id is None:
                raise PaperBlockedError("REACTIVATION_EVIDENCE_REQUIRED")
            normalized_at = _utc(requested_at, field_name="run reactivation requested_at")
            evidence = nonempty(evidence_ref, field_name="run reactivation evidence")
            audit = nonempty(audit_id, field_name="run reactivation audit ID")
            if (
                normalized_at < self._run_state_time_floor(state, run)
                or normalized_at >= run.window_end
            ):
                raise PaperBlockedError("EXECUTION_RUN_STATE_TIME_INVALID")
            self._require_audit_id_available(state, audit)
            proposal_reservations: list[tuple[TradeProposal, PaperReservation]] = []
            for proposal_id, version in run.included_proposals:
                proposal_attempts = tuple(
                    item
                    for item in run.attempts
                    if item.proposal_id == proposal_id
                )
                if proposal_attempts:
                    if any(
                        item.result is AttemptResult.UNKNOWN
                        for item in proposal_attempts
                    ):
                        raise PaperBlockedError("EXECUTION_RUN_GATES_NOT_PASS")
                    continue
                proposal = state.proposals.get(proposal_id)
                if (
                    proposal is None
                    or proposal.version != version
                    or proposal.state
                    not in {
                        ProposalState.READY,
                        ProposalState.USER_STOPPED,
                    }
                    or normalized_at >= proposal.expires_at
                ):
                    raise PaperBlockedError("EXECUTION_RUN_GATES_NOT_PASS")
                reservations = tuple(
                    reservation
                    for reservation in state.reservations.values()
                    if reservation.proposal_id == proposal_id
                    and reservation.state is ReservationState.ACTIVE
                )
                if len(reservations) != 1:
                    raise PaperBlockedError("EXECUTION_RUN_GATES_NOT_PASS")
                reservation = reservations[0]
                candidate_set = state.candidate_sets.get(proposal.candidate_set_id)
                if (
                    candidate_set is None
                    or candidate_set.state is not CandidateSetState.SET_APPROVED
                ):
                    raise PaperBlockedError("EXECUTION_RUN_GATES_NOT_PASS")
                self._require_valid_approval(
                    state,
                    approval_id=reservation.candidate_approval_id,
                    expected_type=ApprovalType.CANDIDATE_SET,
                    expected_target=ApprovalTarget(
                        candidate_set.set_id,
                        candidate_set.version,
                        candidate_set.set_hash,
                    ),
                    now=normalized_at,
                    consumer_ref=candidate_set.set_id,
                )
                current_group_amount = self._large_group_amount_in_state(
                    state,
                    proposal,
                )
                if (
                    current_group_amount >= LARGE_TRADE_THRESHOLD_KRW
                    and reservation.large_approval_id is None
                ):
                    raise PaperBlockedError("EXECUTION_RUN_GATES_NOT_PASS")
                if reservation.large_approval_id is not None:
                    group_target = self._large_group_target_in_state(state, proposal)
                    self._require_valid_approval(
                        state,
                        approval_id=reservation.large_approval_id,
                        expected_type=ApprovalType.LARGE_TRADE,
                        expected_target=group_target,
                        now=normalized_at,
                        consumer_ref=group_target.target_id,
                    )
                proposal_reservations.append((proposal, reservation))
            updated = replace(
                run,
                state=RunState.RUNNING if run.attempted else RunState.READY,
                state_reason_code="EXPLICIT_REACTIVATION",
                state_evidence_ref=evidence,
                state_changed_at=normalized_at,
            )
            state.runs[run_id] = updated
            for proposal, _reservation in proposal_reservations:
                if proposal.state in {
                    ProposalState.TEMPORARILY_HELD,
                    ProposalState.USER_STOPPED,
                }:
                    state.proposals[proposal.proposal_id] = replace(
                        proposal,
                        state=ProposalState.READY,
                    )
            state.audits.append(AuditEvent(audit, "EXECUTION_RUN_REACTIVATED", run_id, normalized_at))
            return updated

        return self._repository.transact(operation)

    def close_execution_window(self, run_id: str, *, ended_at: datetime) -> ExecutionRun:
        normalized_end = _utc(ended_at, field_name="run ended_at")

        def operation(state: _PaperState) -> ExecutionRun:
            run = state.runs.get(run_id)
            if run is None:
                raise PaperBlockedError("EXECUTION_RUN_NOT_FOUND")
            if run.state in {RunState.MISSED_WINDOW, RunState.COMPLETED, RunState.RECONCILED}:
                raise PaperBlockedError("EXECUTION_RUN_TERMINAL")
            if normalized_end < run.window_end:
                raise PaperBlockedError("EXECUTION_WINDOW_NOT_ENDED")
            if normalized_end < self._run_state_time_floor(state, run):
                raise PaperBlockedError("EXECUTION_RUN_STATE_TIME_INVALID")
            if not run.attempted:
                for proposal_id, _ in run.included_proposals:
                    self._release_active_reservation_for_proposal(
                        state,
                        proposal_id=proposal_id,
                        released_at=normalized_end,
                        reason_code="MISSED_WINDOW_NO_ATTEMPT",
                    )
                    proposal = state.proposals[proposal_id]
                    if proposal.state not in {
                        ProposalState.CANCELLED,
                        ProposalState.EXPIRED,
                        ProposalState.DEFINITIVELY_UNEXECUTABLE,
                        ProposalState.PAPER_RECORDED,
                        ProposalState.RECONCILED,
                        ProposalState.NEEDS_RECONCILIATION,
                    }:
                        state.proposals[proposal_id] = replace(
                            proposal,
                            state=ProposalState.MISSED_WINDOW,
                        )
                updated = replace(
                    run,
                    state=RunState.MISSED_WINDOW,
                    terminal_reason="NO_ATTEMPT_BEFORE_WINDOW_END",
                    ended_at=normalized_end,
                )
            elif any(item.result is AttemptResult.UNKNOWN for item in run.attempts):
                attempted_proposals = {
                    item.proposal_id for item in run.attempts
                }
                included_ids = {
                    proposal_id for proposal_id, _version in run.included_proposals
                }
                for attempt in run.attempts:
                    if attempt.result is not AttemptResult.UNKNOWN:
                        continue
                    for reservation_id, reservation in tuple(state.reservations.items()):
                        if (
                            reservation.proposal_id == attempt.proposal_id
                            and reservation.state is ReservationState.ACTIVE
                        ):
                            state.reservations[reservation_id] = replace(
                                reservation,
                                state=ReservationState.NEEDS_RECONCILIATION,
                                reason_code="WINDOW_ENDED_RESULT_UNKNOWN",
                            )
                    proposal = state.proposals[attempt.proposal_id]
                    state.proposals[proposal.proposal_id] = replace(
                        proposal,
                        state=ProposalState.NEEDS_RECONCILIATION,
                    )
                for proposal_id in included_ids - attempted_proposals:
                    self._release_active_reservation_for_proposal(
                        state,
                        proposal_id=proposal_id,
                        released_at=normalized_end,
                        reason_code="RECONCILIATION_RUN_UNATTEMPTED",
                    )
                    proposal = state.proposals[proposal_id]
                    if proposal.state not in {
                        ProposalState.EXPIRED,
                        ProposalState.DEFINITIVELY_UNEXECUTABLE,
                        ProposalState.PAPER_RECORDED,
                        ProposalState.RECONCILED,
                        ProposalState.NEEDS_RECONCILIATION,
                    }:
                        state.proposals[proposal_id] = replace(
                            proposal,
                            state=ProposalState.MISSED_WINDOW,
                        )
                updated = replace(
                    run,
                    state=RunState.RECONCILIATION_REQUIRED,
                    terminal_reason="ATTEMPT_RESULT_UNKNOWN",
                    ended_at=normalized_end,
                )
            else:
                attempted_proposals = {item.proposal_id for item in run.attempts}
                included_ids = {item[0] for item in run.included_proposals}
                completion = CompletionKind.FULL if attempted_proposals == included_ids else CompletionKind.PARTIAL
                updated = replace(
                    run,
                    state=RunState.COMPLETED,
                    completion_kind=completion,
                    terminal_reason="ALL_ATTEMPT_RESULTS_CONFIRMED",
                    ended_at=normalized_end,
                )
                for proposal_id in included_ids - attempted_proposals:
                    self._release_active_reservation_for_proposal(
                        state,
                        proposal_id=proposal_id,
                        released_at=normalized_end,
                        reason_code="PARTIAL_RUN_UNATTEMPTED",
                    )
                    proposal = state.proposals[proposal_id]
                    if proposal.state not in {
                        ProposalState.CANCELLED,
                        ProposalState.EXPIRED,
                        ProposalState.DEFINITIVELY_UNEXECUTABLE,
                        ProposalState.PAPER_RECORDED,
                        ProposalState.RECONCILED,
                        ProposalState.NEEDS_RECONCILIATION,
                    }:
                        state.proposals[proposal_id] = replace(
                            proposal,
                            state=ProposalState.MISSED_WINDOW,
                        )
            state.runs[run_id] = updated
            return updated

        return self._repository.transact(operation)

    def reconcile_execution_run(self, run_id: str, *, evidence_ref: str, reconciled_at: datetime) -> ExecutionRun:
        nonempty(evidence_ref, field_name="reconciliation evidence")
        normalized_at = _utc(reconciled_at, field_name="reconciled_at")

        def operation(state: _PaperState) -> ExecutionRun:
            run = state.runs.get(run_id)
            if run is None or run.state is not RunState.RECONCILIATION_REQUIRED:
                raise PaperBlockedError("EXECUTION_RUN_NOT_RECONCILABLE")
            included_ids = {proposal_id for proposal_id, _ in run.included_proposals}
            if any(
                reservation.proposal_id in included_ids
                and reservation.state
                in {
                    ReservationState.UNKNOWN,
                    ReservationState.NEEDS_RECONCILIATION,
                }
                for reservation in state.reservations.values()
            ):
                raise PaperBlockedError("RESERVATION_RECONCILIATION_REQUIRED")
            if any(attempt.result is AttemptResult.UNKNOWN for attempt in run.attempts):
                raise PaperBlockedError("ATTEMPT_RECONCILIATION_REQUIRED")
            related_reservations = tuple(
                item
                for item in state.reservations.values()
                if item.proposal_id in included_ids
            )
            related_fills = tuple(
                item
                for item in state.fills.values()
                if item.proposal_id in included_ids
            )
            causal_floor = max(
                (
                    run.ended_at or run.window_start,
                    *(item.attempted_at for item in run.attempts),
                    *(item.filled_at for item in related_fills),
                    *(
                        item.released_at
                        for item in related_reservations
                        if item.released_at is not None
                    ),
                )
            )
            if normalized_at < causal_floor:
                raise PaperBlockedError("RUN_RECONCILIATION_TIME_INVALID")
            reconciliation_audit_id = (
                f"run-reconcile:{run_id}:{utc_epoch_microseconds(normalized_at)}"
            )
            self._require_audit_id_available(state, reconciliation_audit_id)
            attempted_proposals = {item.proposal_id for item in run.attempts}
            included_ids = {item[0] for item in run.included_proposals}
            for proposal_id in included_ids - attempted_proposals:
                self._release_active_reservation_for_proposal(
                    state,
                    proposal_id=proposal_id,
                    released_at=normalized_at,
                    reason_code="RECONCILIATION_RUN_UNATTEMPTED",
                )
                proposal = state.proposals[proposal_id]
                if proposal.state not in {
                    ProposalState.EXPIRED,
                    ProposalState.DEFINITIVELY_UNEXECUTABLE,
                    ProposalState.PAPER_RECORDED,
                    ProposalState.RECONCILED,
                    ProposalState.NEEDS_RECONCILIATION,
                }:
                    state.proposals[proposal_id] = replace(
                        proposal,
                        state=ProposalState.CANCELLED,
                    )
                if run.ended_at is not None and run.ended_at >= run.window_end:
                    proposal = state.proposals[proposal_id]
                    if proposal.state not in {
                        ProposalState.EXPIRED,
                        ProposalState.DEFINITIVELY_UNEXECUTABLE,
                        ProposalState.PAPER_RECORDED,
                        ProposalState.RECONCILED,
                        ProposalState.NEEDS_RECONCILIATION,
                    }:
                        state.proposals[proposal_id] = replace(
                            proposal,
                            state=ProposalState.MISSED_WINDOW,
                        )
            if any(
                reservation.proposal_id in included_ids
                and _active_reservation(reservation.state)
                for reservation in state.reservations.values()
            ):
                raise PaperBlockedError("ACTIVE_RESERVATION_RECONCILIATION_REQUIRED")
            completion = (
                CompletionKind.FULL
                if attempted_proposals == included_ids
                else CompletionKind.PARTIAL
            )
            updated = replace(
                run,
                state=RunState.RECONCILED,
                completion_kind=completion,
                terminal_reason="EXPLICIT_RECONCILIATION_COMPLETED",
                ended_at=normalized_at,
            )
            state.runs[run_id] = updated
            state.audits.append(
                AuditEvent(
                    reconciliation_audit_id,
                    "EXECUTION_RUN_RECONCILED",
                    run_id,
                    normalized_at,
                    evidence_ref=evidence_ref,
                )
            )
            return updated

        return self._repository.transact(operation)

    @staticmethod
    def _require_portfolio(state: _PaperState) -> PaperPortfolio:
        if state.portfolio is None:
            raise PaperBlockedError("PAPER_PORTFOLIO_NOT_CREATED")
        return state.portfolio

    @staticmethod
    def _require_ledger_entry_ids_available(
        state: _PaperState,
        *entry_ids: str,
    ) -> None:
        normalized = tuple(
            nonempty(entry_id, field_name="ledger entry ID")
            for entry_id in entry_ids
        )
        if len(set(normalized)) != len(normalized) or any(
            entry.entry_id in normalized
            for entry in state.ledger_entries
        ):
            raise PaperBlockedError("LEDGER_ENTRY_ID_DUPLICATE")

    @staticmethod
    def _require_audit_id_available(state: _PaperState, audit_id: str) -> None:
        normalized = nonempty(audit_id, field_name="audit ID")
        if any(item.audit_id == normalized for item in state.audits):
            raise PaperBlockedError("AUDIT_ID_DUPLICATE")

    @staticmethod
    def _approval_state_time_floor(
        state: _PaperState,
        approval: Approval,
    ) -> datetime:
        related_reservations = tuple(
            item
            for item in state.reservations.values()
            if item.candidate_approval_id == approval.approval_id
            or item.large_approval_id == approval.approval_id
        )
        related_proposal_ids = {
            item.proposal_id for item in related_reservations
        }
        related_runs = tuple(
            run
            for run in state.runs.values()
            if any(
                proposal_id in related_proposal_ids
                for proposal_id, _version in run.included_proposals
            )
        )
        related_reservation_ids = {
            item.reservation_id for item in related_reservations
        }
        return max(
            (
                approval.state_changed_at or approval.approved_at,
                *(item.created_at for item in related_reservations),
                *(
                    item.released_at
                    for item in related_reservations
                    if item.released_at is not None
                ),
                *(
                    attempt.attempted_at
                    for run in state.runs.values()
                    for attempt in run.attempts
                    if attempt.proposal_id in related_proposal_ids
                ),
                *(
                    fill.filled_at
                    for fill in state.fills.values()
                    if fill.proposal_id in related_proposal_ids
                ),
                *(
                    run.started_at
                    for run in related_runs
                    if run.started_at is not None
                ),
                *(
                    run.ended_at
                    for run in related_runs
                    if run.ended_at is not None
                ),
                *(
                    run.state_changed_at
                    for run in related_runs
                    if run.state_changed_at is not None
                ),
                *(
                    audit.recorded_at
                    for audit in state.audits
                    if audit.related_ref in related_reservation_ids
                ),
            )
        )

    @staticmethod
    def _proposal_state_time_floor(
        state: _PaperState,
        proposal: TradeProposal,
    ) -> datetime:
        """proposal 관련 확정 사건보다 과거로 상태를 되돌리지 않는다."""

        candidate_set = state.candidate_sets.get(proposal.candidate_set_id)
        related_reservations = tuple(
            item
            for item in state.reservations.values()
            if item.proposal_id == proposal.proposal_id
        )
        related_reservation_ids = {
            item.reservation_id for item in related_reservations
        }
        related_runs = tuple(
            run
            for run in state.runs.values()
            if any(
                proposal_id == proposal.proposal_id
                for proposal_id, _version in run.included_proposals
            )
            or any(
                attempt.proposal_id == proposal.proposal_id
                for attempt in run.attempts
            )
        )
        related_run_ids = {run.execution_run_id for run in related_runs}
        related_approval_ids = {
            approval_id
            for reservation in related_reservations
            for approval_id in (
                reservation.candidate_approval_id,
                reservation.large_approval_id,
            )
            if approval_id is not None
        }
        if candidate_set is not None and candidate_set.approval_ref is not None:
            related_approval_ids.add(candidate_set.approval_ref)
        floor_values = [proposal.price_condition.evidence_cutoff_at]
        if candidate_set is not None:
            floor_values.extend(
                (candidate_set.created_at, candidate_set.evidence_cutoff)
            )
        for check in state.price_condition_checks:
            if check.proposal_id == proposal.proposal_id:
                floor_values.extend(
                    (
                        check.observed_price.as_of,
                        check.observation_cutoff_at,
                        check.evaluated_at,
                    )
                )
        for reservation in related_reservations:
            floor_values.append(reservation.created_at)
            if reservation.released_at is not None:
                floor_values.append(reservation.released_at)
        for run in related_runs:
            if run.started_at is not None:
                floor_values.append(run.started_at)
            if run.ended_at is not None:
                floor_values.append(run.ended_at)
            if run.state_changed_at is not None:
                floor_values.append(run.state_changed_at)
            floor_values.extend(attempt.attempted_at for attempt in run.attempts)
        floor_values.extend(
            fill.filled_at
            for fill in state.fills.values()
            if fill.proposal_id == proposal.proposal_id
        )
        for approval_id in related_approval_ids:
            approval = state.approvals.get(approval_id)
            if approval is None:
                continue
            floor_values.append(approval.approved_at)
            if approval.state_changed_at is not None:
                floor_values.append(approval.state_changed_at)
        floor_values.extend(
            audit.recorded_at
            for audit in state.audits
            if audit.related_ref
            in {
                proposal.proposal_id,
                *related_reservation_ids,
                *related_run_ids,
            }
        )
        return max(floor_values)

    @staticmethod
    def _require_purpose_references(state: _PaperState, purpose: TradePurpose) -> None:
        if purpose.strategy_version_ref not in state.strategy_versions:
            raise PaperBlockedError("STRATEGY_VERSION_NOT_FOUND")
        if not state.strategy_applications or (
            state.strategy_applications[-1].strategy_version_id
            != purpose.strategy_version_ref
        ):
            raise PaperBlockedError("STRATEGY_VERSION_NOT_APPLIED")
        budget = state.budgets.get(purpose.budget_version_ref)
        if budget is None:
            raise PaperBlockedError("BUDGET_VERSION_NOT_FOUND")
        if budget.policy_version != purpose.policy_version_ref:
            raise PaperBlockedError("PURPOSE_POLICY_BUDGET_VERSION_MISMATCH")
        if purpose.strategy_id not in dict(budget.allocations_krw):
            raise PaperBlockedError("STRATEGY_BUDGET_ALLOCATION_NOT_FOUND")

    @staticmethod
    def _require_valid_approval(
        state: _PaperState,
        *,
        approval_id: str,
        expected_type: ApprovalType,
        expected_target: ApprovalTarget,
        now: datetime,
        consumer_ref: str,
    ) -> Approval:
        approval = state.approvals.get(approval_id)
        if approval is None:
            raise PaperBlockedError("APPROVAL_NOT_FOUND")
        if approval.approval_type is not expected_type:
            raise PaperBlockedError("APPROVAL_TYPE_MISMATCH")
        if approval.target != expected_target:
            raise PaperBlockedError("APPROVAL_TARGET_MISMATCH")
        if now < approval.approved_at:
            raise PaperBlockedError("APPROVAL_NOT_YET_VALID")
        if now >= approval.expires_at:
            raise PaperBlockedError("APPROVAL_EXPIRED")
        if approval.state in {
            ApprovalState.CANCELLED,
            ApprovalState.INVALIDATED,
            ApprovalState.EXPIRED,
        }:
            raise PaperBlockedError("APPROVAL_NOT_ACTIVE")
        if approval.state is ApprovalState.CONSUMED:
            raise PaperBlockedError("APPROVAL_ALREADY_CONSUMED")
        if approval.state is ApprovalState.BOUND and approval.bound_to_ref != consumer_ref:
            raise PaperBlockedError("APPROVAL_REUSE_FORBIDDEN")
        return approval

    @classmethod
    def _bind_valid_approval_in_state(
        cls,
        state: _PaperState,
        *,
        approval_id: str,
        expected_type: ApprovalType,
        expected_target: ApprovalTarget,
        now: datetime,
        consumer_ref: str,
    ) -> Approval:
        approval = cls._require_valid_approval(
            state,
            approval_id=approval_id,
            expected_type=expected_type,
            expected_target=expected_target,
            now=now,
            consumer_ref=consumer_ref,
        )
        if approval.state is ApprovalState.APPROVED:
            audit_id = f"bind:{approval_id}:{consumer_ref}"
            cls._require_audit_id_available(state, audit_id)
            approval = replace(
                approval,
                state=ApprovalState.BOUND,
                bound_to_ref=consumer_ref,
                state_changed_at=now,
            )
            state.approvals[approval_id] = approval
            state.audits.append(
                AuditEvent(
                    audit_id,
                    "APPROVAL_BOUND",
                    approval_id,
                    now,
                )
            )
        return approval

    @staticmethod
    def _claim_trade_dates(state: _PaperState, proposal: TradeProposal) -> None:
        keys = (
            (state.kst_intent_claims, (proposal.instrument_id, proposal.kst_intent_date)),
            (state.us_session_claims, (proposal.instrument_id, proposal.us_session_date)),
        )
        for mapping, key in keys:
            existing_proposal = mapping.get(key)
            if existing_proposal is not None and existing_proposal != proposal.proposal_id:
                raise PaperBlockedError("SAME_INSTRUMENT_DATE_CONFLICT")
        for mapping, key in keys:
            mapping[key] = proposal.proposal_id

    @staticmethod
    def _proposal_has_open_attempt(state: _PaperState, proposal_id: str) -> bool:
        return any(
            attempt.proposal_id == proposal_id
            and attempt.result is AttemptResult.UNKNOWN
            and run.state
            in {
                RunState.RUNNING,
                RunState.RECONCILIATION_REQUIRED,
                RunState.TEMPORARILY_HELD,
                RunState.USER_STOPPED,
            }
            for run in state.runs.values()
            for attempt in run.attempts
        )

    @staticmethod
    def _run_state_time_floor(
        state: _PaperState,
        run: ExecutionRun,
    ) -> datetime:
        included_ids = {proposal_id for proposal_id, _version in run.included_proposals}
        return max(
            (
                run.window_start,
                run.state_changed_at or run.window_start,
                run.started_at or run.window_start,
                *(attempt.attempted_at for attempt in run.attempts),
                *(
                    PaperEngine._proposal_state_time_floor(state, state.proposals[proposal_id])
                    for proposal_id in included_ids
                    if proposal_id in state.proposals
                ),
                *(
                    fill.filled_at
                    for fill in state.fills.values()
                    if fill.proposal_id in included_ids
                ),
            )
        )

    @classmethod
    def _require_candidate_rank_available(
        cls,
        state: _PaperState,
        proposal: TradeProposal,
    ) -> None:
        set_id = proposal.candidate_set_id
        if (set_id, proposal.candidate_rank) in state.candidate_rank_clearances:
            raise PaperBlockedError("CANDIDATE_RANK_ALREADY_CLEARED")
        for prior_rank in range(1, proposal.candidate_rank):
            if (set_id, prior_rank) not in state.candidate_rank_clearances:
                raise PaperBlockedError("PRIOR_CANDIDATE_PERMANENT_FAILURE_REQUIRED")
            for prior in state.proposals.values():
                if (
                    prior.candidate_set_id != set_id
                    or prior.candidate_rank != prior_rank
                ):
                    continue
                if prior.state is not ProposalState.DEFINITIVELY_UNEXECUTABLE:
                    raise PaperBlockedError("PRIOR_CANDIDATE_NOT_PERMANENTLY_INELIGIBLE")
                if any(
                    reservation.proposal_id == prior.proposal_id
                    and _active_reservation(reservation.state)
                    for reservation in state.reservations.values()
                ) or cls._proposal_has_open_attempt(state, prior.proposal_id) or any(
                    fill.proposal_id == prior.proposal_id
                    for fill in state.fills.values()
                ):
                    raise PaperBlockedError("PRIOR_CANDIDATE_ALREADY_ACTIVE_OR_EXECUTED")

    @staticmethod
    def _hold_open_attempt_runs_for_reconciliation(
        state: _PaperState,
        *,
        proposal_id: str,
        at: datetime,
        reason: str,
    ) -> None:
        for run_id, run in tuple(state.runs.items()):
            if any(
                attempt.proposal_id == proposal_id
                and attempt.result is AttemptResult.UNKNOWN
                for attempt in run.attempts
            ) and run.state in {
                RunState.RUNNING,
                RunState.TEMPORARILY_HELD,
                RunState.USER_STOPPED,
            }:
                state.runs[run_id] = replace(
                    run,
                    state=RunState.RECONCILIATION_REQUIRED,
                    terminal_reason=reason,
                    ended_at=at,
                )

    @staticmethod
    def _hold_execution_runs_for_gate_loss(
        state: _PaperState,
        *,
        proposal_id: str,
        at: datetime,
        reason: str,
        evidence: str,
    ) -> None:
        """승인·예약 gate 상실 뒤 기존 run을 READY/RUNNING으로 남기지 않는다."""

        for run_id, run in tuple(state.runs.items()):
            if not any(
                included_id == proposal_id
                for included_id, _version in run.included_proposals
            ):
                continue
            if run.state not in {
                RunState.READY,
                RunState.RUNNING,
                RunState.TEMPORARILY_HELD,
            }:
                continue
            if at < PaperEngine._run_state_time_floor(state, run):
                raise PaperBlockedError("EXECUTION_RUN_STATE_TIME_INVALID")
            state.runs[run_id] = replace(
                run,
                state=RunState.TEMPORARILY_HELD,
                state_reason_code=reason,
                state_evidence_ref=evidence,
                state_changed_at=at,
            )

    @staticmethod
    def _release_active_reservation_for_proposal(
        state: _PaperState,
        *,
        proposal_id: str,
        released_at: datetime,
        reason_code: str,
    ) -> None:
        portfolio = state.portfolio
        if portfolio is None:
            raise PaperBlockedError("PAPER_PORTFOLIO_NOT_CREATED")
        reservations = tuple(
            item
            for item in state.reservations.values()
            if item.proposal_id == proposal_id and _active_reservation(item.state)
        )
        for reservation in reservations:
            if reservation.state in {
                ReservationState.UNKNOWN,
                ReservationState.NEEDS_RECONCILIATION,
            }:
                raise PaperBlockedError("RECONCILIATION_REQUIRED_RESERVATION_RETAINED")
            if released_at < reservation.created_at:
                raise PaperBlockedError("RESERVATION_RELEASE_TIME_INVALID")
            latest_fill_at = max(
                (
                    fill.filled_at
                    for fill in state.fills.values()
                    if fill.proposal_id == proposal_id
                ),
                default=None,
            )
            if latest_fill_at is not None and released_at < latest_fill_at:
                raise PaperBlockedError("RESERVATION_RELEASE_TIME_INVALID")
            PaperEngine._require_ledger_entry_ids_available(
                state,
                f"release:{reservation.reservation_id}",
            )
            proposal = state.proposals[proposal_id]
            limit = state.daily_limits[proposal.us_session_date]
            state.daily_limits[proposal.us_session_date] = replace(
                limit,
                active_reserved_gross_krw=(
                    limit.active_reserved_gross_krw
                    - reservation.remaining_daily_gross_krw
                ),
            )
            state.reservations[reservation.reservation_id] = replace(
                reservation,
                remaining_cash_amount=Decimal("0"),
                remaining_daily_gross_krw=Decimal("0"),
                remaining_strategy_budget_krw=Decimal("0"),
                remaining_monthly_budget_krw=Decimal("0"),
                remaining_sell_lot_quantities=(),
                state=ReservationState.RELEASED,
                released_at=released_at,
                reason_code=reason_code,
            )
            state.ledger_entries.append(
                PaperLedgerEntry(
                    f"release:{reservation.reservation_id}",
                    LedgerEventType.RELEASE,
                    portfolio.portfolio_id,
                    released_at,
                    released_at,
                    reservation.remaining_daily_gross_krw,
                    Currency.KRW,
                    instrument_id=proposal.instrument_id,
                    source_ref="PAPER_RESERVATION_RELEASE",
                )
            )

    @staticmethod
    def _require_risk_denominator_binding(
        state: _PaperState,
        proposal: TradeProposal,
        risk_context: PaperRiskContext,
    ) -> None:
        policy = risk_context.policy
        if policy.denominator_basis is RiskDenominatorBasis.CURRENT_PAPER_NAV:
            return
        if policy.denominator_basis is not RiskDenominatorBasis.SESSION_OPENING_PAPER_NAV:
            raise PaperBlockedError("RISK_DENOMINATOR_POLICY_INPUT_REQUIRED")
        limit = state.daily_limits.get(proposal.us_session_date)
        if (
            limit is None
            or policy.session_opening_nav_krw != limit.opening_nav_krw
            or risk_context.valuation.as_of > risk_context.valuation.cutoff_at
        ):
            raise PaperBlockedError("SESSION_OPENING_RISK_DENOMINATOR_MISMATCH")

    @staticmethod
    def _require_execution_policy(
        state: _PaperState,
        policy: PaperExecutionPolicy,
        proposal: TradeProposal,
        *,
        existing_reservation_id: str | None = None,
    ) -> None:
        if policy.cost_model_confirmed is not True:
            raise PaperBlockedError("COST_POLICY_INPUT_REQUIRED")
        if proposal.side is Side.SELL and policy.cost_basis_method is None:
            raise PaperBlockedError("COST_BASIS_POLICY_INPUT_REQUIRED")
        if proposal.amounts.cash_currency is not Currency.KRW and policy.fx_model_confirmed is not True:
            raise PaperBlockedError("FX_POLICY_INPUT_REQUIRED")
        applies = (
            policy.monthly_budget_applies_to_buy
            if proposal.side is Side.BUY
            else policy.monthly_budget_applies_to_sell
        )
        if applies is None:
            raise PaperBlockedError("MONTHLY_BUDGET_APPLICABILITY_REQUIRED")
        if policy.per_order_limit_krw is None:
            raise PaperBlockedError("PER_ORDER_LIMIT_POLICY_INPUT_REQUIRED")
        if proposal.amounts.approval_judgment_krw > policy.per_order_limit_krw:
            raise PaperBlockedError("PER_ORDER_LIMIT_EXCEEDED")
        if applies:
            session_month = proposal.us_session_date.strftime("%Y-%m")
            if policy.monthly_budget_krw is None or policy.monthly_budget_month is None:
                raise PaperBlockedError("MONTHLY_BUDGET_INPUT_REQUIRED")
            if policy.monthly_budget_month != session_month:
                raise PaperBlockedError("MONTHLY_BUDGET_MONTH_MISMATCH")
            used = sum(
                (
                    fill.amounts.approval_judgment_krw
                    for fill in state.fills.values()
                    if state.reservations[fill.reservation_id].original_monthly_budget_krw
                    and state.proposals[fill.proposal_id].us_session_date.strftime("%Y-%m")
                    == session_month
                ),
                Decimal("0"),
            )
            used += sum(
                (
                    reservation.remaining_monthly_budget_krw
                    for reservation in state.reservations.values()
                    if _active_reservation(reservation.state)
                    and state.proposals[reservation.proposal_id].us_session_date.strftime(
                        "%Y-%m"
                    )
                    == session_month
                ),
                Decimal("0"),
            )
            additional = (
                Decimal("0")
                if existing_reservation_id is not None
                else proposal.amounts.approval_judgment_krw
            )
            if existing_reservation_id is not None:
                current = state.reservations.get(existing_reservation_id)
                if (
                    current is None
                    or current.proposal_id != proposal.proposal_id
                    or not _active_reservation(current.state)
                ):
                    raise PaperBlockedError("ACTIVE_RESERVATION_REQUIRED")
            if used + additional > policy.monthly_budget_krw:
                raise PaperBlockedError("MONTHLY_BUDGET_EXCEEDED")

    @staticmethod
    def _large_group_amount_in_state(state: _PaperState, proposal: TradeProposal) -> Decimal:
        terminal = {
            ProposalState.CANCELLED,
            ProposalState.EXPIRED,
            ProposalState.MISSED_WINDOW,
            ProposalState.DEFINITIVELY_UNEXECUTABLE,
        }
        return sum(
            (
                item.amounts.approval_judgment_krw
                for item in state.proposals.values()
                if item.approval_group_key == proposal.approval_group_key and item.state not in terminal
            ),
            Decimal("0"),
        )

    @staticmethod
    def _active_buy_exposures(
        state: _PaperState,
        *,
        excluding_reservation_id: str | None = None,
    ) -> dict[str, Decimal]:
        exposures: dict[str, Decimal] = {}
        for reservation in state.reservations.values():
            if reservation.reservation_id == excluding_reservation_id:
                continue
            if not _active_reservation(reservation.state):
                continue
            proposal = state.proposals[reservation.proposal_id]
            if proposal.side is not Side.BUY:
                continue
            exposures[proposal.instrument_id] = (
                exposures.get(proposal.instrument_id, Decimal("0"))
                + reservation.remaining_daily_gross_krw
            )
        return exposures

    @staticmethod
    def _remaining_buy_maximum_cost_krw(
        state: _PaperState,
        proposal: TradeProposal,
    ) -> Decimal:
        recorded = sum(
            (
                fill.amounts.maximum_cost_krw
                for fill in state.fills.values()
                if fill.proposal_id == proposal.proposal_id
            ),
            Decimal("0"),
        )
        remaining = proposal.amounts.maximum_cost_krw - recorded
        if remaining < 0:
            raise PaperBlockedError("PROPOSAL_MAXIMUM_COST_EXCEEDED")
        return remaining

    @classmethod
    def _active_buy_maximum_costs(
        cls,
        state: _PaperState,
        *,
        excluding_reservation_id: str | None = None,
    ) -> Decimal:
        total = Decimal("0")
        for reservation in state.reservations.values():
            if reservation.reservation_id == excluding_reservation_id:
                continue
            if not _active_reservation(reservation.state):
                continue
            proposal = state.proposals[reservation.proposal_id]
            if proposal.side is Side.BUY:
                total += cls._remaining_buy_maximum_cost_krw(state, proposal)
        return total

    @staticmethod
    def _strategy_budget_used_or_reserved(
        state: _PaperState,
        *,
        budget_version_id: str,
        strategy_id: PaperStrategyKind,
    ) -> Decimal:
        used = Decimal("0")
        del budget_version_id  # 현재 cap version과 무관하게 기존 전략 보유원가를 보존한다.
        fill_by_entry = {
            f"fill:{fill.fill_id}": fill
            for fill in state.fills.values()
        }
        counted_fills: set[str] = set()
        for lot in state.lots.values():
            if lot.remaining_quantity == 0 or lot.strategy_id != strategy_id.value:
                continue
            fill = fill_by_entry.get(lot.acquisition_entry_id)
            if fill is None or fill.fill_id in counted_fills:
                raise PaperBlockedError("STRATEGY_BUDGET_LOT_PROVENANCE_REQUIRED")
            proposal = state.proposals[fill.proposal_id]
            if proposal.side is not Side.BUY or proposal.strategy_id is not strategy_id:
                raise PaperBlockedError("STRATEGY_BUDGET_LOT_PROVENANCE_MISMATCH")
            counted_fills.add(fill.fill_id)
            # 부분 매도 중에는 원 BUY charge를 보수적으로 유지하고 전량 매도 때만 해제한다.
            used += fill.amounts.approval_judgment_krw
        for reservation in state.reservations.values():
            if (
                reservation.strategy_id is strategy_id
                and _active_reservation(reservation.state)
            ):
                used += reservation.remaining_strategy_budget_krw
        return used

    @staticmethod
    def _large_group_target_in_state(state: _PaperState, proposal: TradeProposal) -> ApprovalTarget:
        if state.portfolio is None:
            raise PaperBlockedError("PAPER_PORTFOLIO_NOT_CREATED")
        members = tuple(
            sorted(
                (
                    item.proposal_id,
                    item.version,
                    item.amounts.approval_judgment_krw,
                    item.amounts.fx_krw_per_unit,
                    item.pricing_fx_rate.source_record_id
                    if item.pricing_fx_rate is not None
                    else None,
                    item.pricing_cutoff_at,
                )
                for item in state.proposals.values()
                if item.approval_group_key == proposal.approval_group_key
                and item.state
                not in {
                    ProposalState.CANCELLED,
                    ProposalState.EXPIRED,
                    ProposalState.MISSED_WINDOW,
                    ProposalState.DEFINITIVELY_UNEXECUTABLE,
                }
            )
        )
        full_key = (state.portfolio.portfolio_id, *proposal.approval_group_key)
        return ApprovalTarget(
            canonical_hash(full_key),
            proposal.us_session_date.isoformat(),
            canonical_hash((full_key, members)),
        )

    @staticmethod
    def _large_group_evidence_floor(
        state: _PaperState,
        proposal: TradeProposal,
    ) -> datetime:
        evidence_times: list[datetime] = []
        terminal = {
            ProposalState.CANCELLED,
            ProposalState.EXPIRED,
            ProposalState.MISSED_WINDOW,
            ProposalState.DEFINITIVELY_UNEXECUTABLE,
        }
        for member in state.proposals.values():
            if (
                member.approval_group_key != proposal.approval_group_key
                or member.state in terminal
            ):
                continue
            candidate_set = state.candidate_sets.get(member.candidate_set_id)
            if candidate_set is None:
                raise PaperBlockedError("CANDIDATE_SET_NOT_FOUND")
            evidence_times.extend(
                (candidate_set.created_at, candidate_set.evidence_cutoff)
            )
            if member.pricing_cutoff_at is not None:
                evidence_times.append(member.pricing_cutoff_at)
        if not evidence_times:
            raise PaperBlockedError("LARGE_TRADE_EVIDENCE_NOT_FOUND")
        return max(evidence_times)


def immutable_state_view(state: _PaperState) -> Mapping[str, Any]:
    """검증용 읽기 전용 요약. 사용자/실자료 출력 API가 아니다."""

    return MappingProxyType(
        {
            "scope": state.portfolio.scope.value if state.portfolio else None,
            "cash": MappingProxyType(dict(state.cash)),
            "ledger_count": len(state.ledger_entries),
            "lot_count": len(state.lots),
            "reservation_count": len(state.reservations),
            "fill_count": len(state.fills),
        }
    )
