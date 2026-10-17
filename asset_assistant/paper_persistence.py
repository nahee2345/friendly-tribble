"""Crash-safe SQLite persistence for the isolated INTERNAL_PAPER engine.

This adapter deliberately is not a broker, scheduler, web API, or operating-DB
migration.  A caller supplies an already authenticated owner guard and an
explicit command envelope.  The P1 :class:`~asset_assistant.paper.PaperEngine`
is executed against a private, one-command repository inside one SQLite
``BEGIN IMMEDIATE`` transaction.

The database stores both an exact 21-field projection and an append-only full
state revision chain.  The duplication is intentional: projections make a
missing field visible, while the revision chain detects partial writes and
preserves overwritten reservation/approval/run states for later audit and
reconciliation.  No pickle or dynamic import is used.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException, localcontext
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Generic, Mapping, TypeVar

from .domain import DomainValidationError, nonempty
from .market_data import default_data_directory
from .paper import (
    Approval,
    ApprovalState,
    ApprovalTarget,
    ApprovalType,
    AttemptResult,
    AuditEvent,
    Candidate,
    CandidateFailureReason,
    CandidateRankClearance,
    CandidateSet,
    CandidateSetState,
    CompletionKind,
    CostBasisMethod,
    Currency,
    ExecutionAttempt,
    ExecutionRun,
    GateDecision,
    GateStatus,
    InstrumentClassification,
    InstrumentType,
    LedgerEventType,
    NewRootPurposeTarget,
    PaperBudgetVersion,
    PaperDailyLimit,
    PaperExecutionPolicy,
    PaperFill,
    PaperEngine,
    PaperLedgerEntry,
    PaperPortfolio,
    PaperPositionLot,
    PaperReservation,
    PaperRiskAssessment,
    PaperRiskContext,
    PaperScope,
    PaperStrategyKind,
    PaperStrategyVersion,
    PaperValuationSnapshot,
    PriceCondition,
    PriceConditionCheck,
    PriceConditionOperator,
    ProposalState,
    PurposeChangeResult,
    ReservationRequest,
    ReservationState,
    RiskDenominatorBasis,
    RiskPolicyInputs,
    RiskRole,
    RunState,
    SatelliteLossGate,
    Side,
    SplitCheckResult,
    StrategyApplication,
    TradeAmounts,
    TradeAmountsResult,
    TradeProposal,
    TradePurpose,
    VerifiedFxRate,
    VerifiedPrice,
    VerifiedSessionWindow,
    _PaperState,
    _paper_decimal_context,
    _utc,
)


PAPER_DATABASE_FILENAME = "paper-state.sqlite3"
PAPER_SCHEMA_VERSION = 1
PAPER_SCHEMA_REVISION = "paper-state-v1"
PAPER_CODEC_VERSION = 1
_ZERO_HASH = "0" * 64
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_CODEC_DEPTH = 96
_MAX_CODEC_ITEMS = 500_000
_COMMAND_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")


class PaperPersistenceError(DomainValidationError):
    """A path- and data-redacted persistence failure."""

    def __init__(self, code: str, message: str = "PAPER 저장 작업을 안전하게 완료할 수 없습니다.") -> None:
        self.code = nonempty(code, field_name="PAPER persistence code")
        super().__init__(message)


@dataclass(frozen=True)
class PaperCommandEnvelope:
    """Immutable metadata used to deduplicate exactly one P1 mutation."""

    owner_id: str
    portfolio_id: str
    idempotency_key: str
    command_type: str
    request_payload: Any
    expected_revision: int

    def __post_init__(self) -> None:
        for name in ("owner_id", "portfolio_id", "idempotency_key", "command_type"):
            object.__setattr__(self, name, nonempty(getattr(self, name), field_name=name))
        if not _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise DomainValidationError("idempotency key 형식이 올바르지 않습니다.")
        if not _COMMAND_NAME.fullmatch(self.command_type):
            raise DomainValidationError("PAPER command type 형식이 올바르지 않습니다.")
        if isinstance(self.expected_revision, bool) or not isinstance(self.expected_revision, int):
            raise DomainValidationError("PAPER expected revision은 정수여야 합니다.")
        if self.expected_revision < 0:
            raise DomainValidationError("PAPER expected revision은 음수일 수 없습니다.")


T = TypeVar("T")


@dataclass(frozen=True)
class PaperCommitResult(Generic[T]):
    value: T
    result_revision: int
    historical_replay: bool
    completed_at: datetime


@dataclass(frozen=True)
class PaperStoredSnapshot:
    state: _PaperState
    revision: int
    state_hash: str


_DATACLASS_TYPES = (
    GateDecision,
    SatelliteLossGate,
    PaperPortfolio,
    PaperLedgerEntry,
    PaperBudgetVersion,
    PaperStrategyVersion,
    StrategyApplication,
    PaperPositionLot,
    VerifiedPrice,
    PriceCondition,
    PriceConditionCheck,
    VerifiedFxRate,
    PaperValuationSnapshot,
    InstrumentClassification,
    RiskPolicyInputs,
    PaperRiskAssessment,
    PaperRiskContext,
    TradeAmounts,
    TradeAmountsResult,
    VerifiedSessionWindow,
    PaperDailyLimit,
    TradePurpose,
    Candidate,
    CandidateRankClearance,
    CandidateSet,
    TradeProposal,
    ApprovalTarget,
    NewRootPurposeTarget,
    Approval,
    PaperExecutionPolicy,
    ReservationRequest,
    PaperReservation,
    PaperFill,
    AuditEvent,
    ExecutionAttempt,
    ExecutionRun,
    _PaperState,
)
_DATACLASS_BY_NAME = {item.__name__: item for item in _DATACLASS_TYPES}
_ENUM_TYPES = (
    PaperScope,
    Currency,
    PaperStrategyKind,
    Side,
    GateStatus,
    InstrumentType,
    LedgerEventType,
    RiskRole,
    RiskDenominatorBasis,
    ReservationState,
    CostBasisMethod,
    ApprovalType,
    ApprovalState,
    CandidateSetState,
    ProposalState,
    RunState,
    CompletionKind,
    AttemptResult,
    CandidateFailureReason,
    PriceConditionOperator,
    PurposeChangeResult,
    SplitCheckResult,
)
_ENUM_BY_NAME = {item.__name__: item for item in _ENUM_TYPES}
_STATE_FIELD_NAMES = tuple(item.name for item in fields(_PaperState))
if len(_STATE_FIELD_NAMES) != 21:  # import-time fail closed if P1 grows silently
    raise RuntimeError("PAPER state persistence mapping must be updated")


class _CodecBudget:
    def __init__(self) -> None:
        self.items = 0

    def add(self, count: int = 1) -> None:
        self.items += count
        if self.items > _MAX_CODEC_ITEMS:
            raise DomainValidationError("PAPER 저장 자료 항목 수가 허용 범위를 초과했습니다.")


def _decimal_parts(value: Decimal) -> tuple[int, str, int]:
    if not value.is_finite():
        raise DomainValidationError("PAPER 저장 Decimal은 유한값이어야 합니다.")
    if value == 0:
        return (0, "0", 0)
    sign, digits, exponent = value.as_tuple()
    normalized = list(digits)
    while normalized and normalized[-1] == 0:
        normalized.pop()
        exponent += 1
    return sign, "".join(str(item) for item in normalized) or "0", exponent


def _encode_value(value: Any, *, depth: int, budget: _CodecBudget) -> Any:
    if depth > _MAX_CODEC_DEPTH:
        raise DomainValidationError("PAPER 저장 자료 중첩이 허용 범위를 초과했습니다.")
    budget.add()
    if is_dataclass(value) and not isinstance(value, type):
        value_type = type(value)
        if value_type.__name__ not in _DATACLASS_BY_NAME or _DATACLASS_BY_NAME[value_type.__name__] is not value_type:
            raise DomainValidationError("PAPER 저장에 지원하지 않는 객체 형식입니다.")
        return {
            "@": "dataclass",
            "name": value_type.__name__,
            "fields": [
                [item.name, _encode_value(getattr(value, item.name), depth=depth + 1, budget=budget)]
                for item in fields(value)
            ],
        }
    if isinstance(value, Enum):
        value_type = type(value)
        if value_type.__name__ not in _ENUM_BY_NAME or _ENUM_BY_NAME[value_type.__name__] is not value_type:
            raise DomainValidationError("PAPER 저장에 지원하지 않는 enum 형식입니다.")
        return {"@": "enum", "name": value_type.__name__, "value": value.value}
    if isinstance(value, Decimal):
        sign, coefficient, exponent = _decimal_parts(value)
        return {"@": "decimal", "sign": sign, "coefficient": coefficient, "exponent": exponent}
    if type(value) is datetime:
        normalized = _utc(value, field_name="PAPER stored datetime")
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = normalized - epoch
        micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
        return {"@": "datetime_us", "value": micros}
    if type(value) is date:
        return {"@": "date", "value": value.isoformat()}
    if isinstance(value, Mapping):
        pairs: list[tuple[str, Any, Any]] = []
        try:
            raw_items = tuple(value.items())
        except Exception:
            raise DomainValidationError("PAPER 저장 mapping을 안전하게 읽을 수 없습니다.") from None
        for key, item in raw_items:
            encoded_key = _encode_value(key, depth=depth + 1, budget=budget)
            encoded_item = _encode_value(item, depth=depth + 1, budget=budget)
            key_text = json.dumps(encoded_key, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            pairs.append((key_text, encoded_key, encoded_item))
        pairs.sort(key=lambda item: item[0])
        if any(pairs[index - 1][0] == pairs[index][0] for index in range(1, len(pairs))):
            raise DomainValidationError("PAPER 저장 mapping key가 중복됐습니다.")
        return {"@": "mapping", "items": [[key, item] for _, key, item in pairs]}
    if isinstance(value, tuple):
        return {"@": "tuple", "items": [_encode_value(item, depth=depth + 1, budget=budget) for item in value]}
    if isinstance(value, list):
        return {"@": "list", "items": [_encode_value(item, depth=depth + 1, budget=budget) for item in value]}
    if isinstance(value, frozenset):
        encoded_items = [
            _encode_value(item, depth=depth + 1, budget=budget) for item in value
        ]
        encoded_items.sort(
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        return {"@": "frozenset", "items": encoded_items}
    if value is None or type(value) in {str, int, bool}:
        return value
    raise DomainValidationError("PAPER 저장에 지원하지 않는 값 형식입니다.")


def _object_pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DomainValidationError("PAPER 저장 JSON key가 중복됐습니다.")
        result[key] = value
    return result


def _decode_value(value: Any, *, depth: int, budget: _CodecBudget) -> Any:
    if depth > _MAX_CODEC_DEPTH:
        raise DomainValidationError("PAPER 저장 자료 중첩이 허용 범위를 초과했습니다.")
    budget.add()
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is list:
        raise DomainValidationError("PAPER 저장 JSON 목록 tag가 올바르지 않습니다.")
    if type(value) is not dict:
        raise DomainValidationError("PAPER 저장 JSON 값이 올바르지 않습니다.")
    tag = value.get("@")
    if tag == "decimal" and set(value) == {"@", "sign", "coefficient", "exponent"}:
        sign = value["sign"]
        coefficient = value["coefficient"]
        exponent = value["exponent"]
        if sign not in {0, 1} or type(coefficient) is not str or not coefficient.isdigit() or type(exponent) is not int:
            raise DomainValidationError("PAPER 저장 Decimal 형식이 올바르지 않습니다.")
        if len(coefficient) > 128:
            raise DomainValidationError("PAPER 저장 Decimal 정밀도가 허용 범위를 초과했습니다.")
        return Decimal((sign, tuple(int(item) for item in coefficient), exponent))
    if tag == "datetime_us" and set(value) == {"@", "value"}:
        micros = value["value"]
        if isinstance(micros, bool) or not isinstance(micros, int):
            raise DomainValidationError("PAPER 저장 시각 형식이 올바르지 않습니다.")
        try:
            return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=micros)
        except (OverflowError, ValueError):
            raise DomainValidationError("PAPER 저장 시각 범위가 올바르지 않습니다.") from None
    if tag == "date" and set(value) == {"@", "value"}:
        if type(value["value"]) is not str:
            raise DomainValidationError("PAPER 저장 날짜 형식이 올바르지 않습니다.")
        try:
            return date.fromisoformat(value["value"])
        except ValueError:
            raise DomainValidationError("PAPER 저장 날짜 형식이 올바르지 않습니다.") from None
    if tag == "enum" and set(value) == {"@", "name", "value"}:
        enum_type = _ENUM_BY_NAME.get(value["name"]) if type(value["name"]) is str else None
        if enum_type is None:
            raise DomainValidationError("지원하지 않는 PAPER enum version입니다.")
        try:
            return enum_type(value["value"])
        except (TypeError, ValueError):
            raise DomainValidationError("PAPER enum 값을 복원할 수 없습니다.") from None
    if tag in {"tuple", "list"} and set(value) == {"@", "items"} and type(value["items"]) is list:
        decoded = [_decode_value(item, depth=depth + 1, budget=budget) for item in value["items"]]
        return tuple(decoded) if tag == "tuple" else decoded
    if tag == "frozenset" and set(value) == {"@", "items"} and type(value["items"]) is list:
        decoded = [_decode_value(item, depth=depth + 1, budget=budget) for item in value["items"]]
        try:
            result = frozenset(decoded)
        except TypeError:
            raise DomainValidationError("PAPER frozenset 항목 형식이 올바르지 않습니다.") from None
        if len(result) != len(decoded):
            raise DomainValidationError("PAPER frozenset 항목이 중복됐습니다.")
        return result
    if tag == "mapping" and set(value) == {"@", "items"} and type(value["items"]) is list:
        result: dict[Any, Any] = {}
        for pair in value["items"]:
            if type(pair) is not list or len(pair) != 2:
                raise DomainValidationError("PAPER mapping 항목 형식이 올바르지 않습니다.")
            key = _decode_value(pair[0], depth=depth + 1, budget=budget)
            item = _decode_value(pair[1], depth=depth + 1, budget=budget)
            try:
                if key in result:
                    raise DomainValidationError("PAPER mapping key가 중복됐습니다.")
                result[key] = item
            except TypeError:
                raise DomainValidationError("PAPER mapping key 형식이 올바르지 않습니다.") from None
        return result
    if tag == "dataclass" and set(value) == {"@", "name", "fields"}:
        type_name = value["name"]
        raw_fields = value["fields"]
        target = _DATACLASS_BY_NAME.get(type_name) if type(type_name) is str else None
        if target is None or type(raw_fields) is not list:
            raise DomainValidationError("지원하지 않는 PAPER domain version입니다.")
        expected = tuple(item.name for item in fields(target))
        if len(raw_fields) != len(expected):
            raise DomainValidationError("PAPER domain 필드 수가 올바르지 않습니다.")
        values: dict[str, Any] = {}
        for pair, expected_name in zip(raw_fields, expected, strict=True):
            if type(pair) is not list or len(pair) != 2 or pair[0] != expected_name:
                raise DomainValidationError("PAPER domain 필드 순서가 올바르지 않습니다.")
            values[expected_name] = _decode_value(pair[1], depth=depth + 1, budget=budget)
        try:
            return target(**values)
        except DomainValidationError:
            raise
        except (TypeError, ValueError, OverflowError):
            raise DomainValidationError("PAPER domain 객체를 안전하게 복원할 수 없습니다.") from None
    raise DomainValidationError("PAPER 저장 JSON tag가 올바르지 않습니다.")


def _encode(value: Any) -> str:
    try:
        encoded = _encode_value(value, depth=0, budget=_CodecBudget())
        text = json.dumps(encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(text.encode("utf-8")) > _MAX_JSON_BYTES:
            raise DomainValidationError("PAPER 저장 자료 크기가 허용 범위를 초과했습니다.")
        return text
    except DomainValidationError:
        raise
    except (RecursionError, TypeError, ValueError, OverflowError):
        raise DomainValidationError("PAPER 저장 자료를 안전하게 직렬화할 수 없습니다.") from None


def _decode(text: str) -> Any:
    if type(text) is not str or len(text.encode("utf-8")) > _MAX_JSON_BYTES:
        raise DomainValidationError("PAPER 저장 자료 크기가 올바르지 않습니다.")
    try:
        raw = json.loads(text, object_pairs_hook=_object_pairs_no_duplicates)
        return _decode_value(raw, depth=0, budget=_CodecBudget())
    except DomainValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError, OverflowError):
        raise DomainValidationError("PAPER 저장 자료를 안전하게 복원할 수 없습니다.") from None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_DDL = (
    """CREATE TABLE paper_schema_ledger (
        revision TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        checksum_sha256 TEXT NOT NULL,
        codec_version INTEGER NOT NULL,
        created_at_utc_us INTEGER NOT NULL
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_owner_bindings (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        owner_id TEXT NOT NULL UNIQUE,
        bound_at_utc_us INTEGER NOT NULL,
        binding_hash TEXT NOT NULL CHECK(length(binding_hash) = 64)
    )""",
    """CREATE TABLE paper_aggregates (
        owner_id TEXT NOT NULL,
        portfolio_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        state_json TEXT NOT NULL,
        state_hash TEXT NOT NULL CHECK(length(state_hash) = 64),
        last_event_seq INTEGER NOT NULL CHECK(last_event_seq = revision),
        updated_at_utc_us INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id),
        FOREIGN KEY(owner_id) REFERENCES paper_owner_bindings(owner_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_state_fields (
        owner_id TEXT NOT NULL,
        portfolio_id TEXT NOT NULL,
        field_name TEXT NOT NULL,
        field_order INTEGER NOT NULL CHECK(field_order >= 0 AND field_order < 21),
        payload_json TEXT NOT NULL,
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        revision INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, field_name),
        UNIQUE(owner_id, portfolio_id, field_order),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_state_revisions (
        owner_id TEXT NOT NULL,
        portfolio_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        previous_state_hash TEXT NOT NULL CHECK(length(previous_state_hash) = 64),
        state_hash TEXT NOT NULL CHECK(length(state_hash) = 64),
        state_json TEXT NOT NULL,
        command_fingerprint TEXT NOT NULL CHECK(length(command_fingerprint) = 64),
        committed_at_utc_us INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, revision),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_command_receipts (
        owner_id TEXT NOT NULL,
        portfolio_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        command_type TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64),
        status TEXT NOT NULL CHECK(status = 'COMPLETED'),
        result_revision INTEGER NOT NULL CHECK(result_revision >= 1),
        result_json TEXT NOT NULL,
        result_hash TEXT NOT NULL CHECK(length(result_hash) = 64),
        completed_at_utc_us INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, idempotency_key),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_kst_intent_claims (
        owner_id TEXT NOT NULL, portfolio_id TEXT NOT NULL,
        instrument_id TEXT NOT NULL, intent_date TEXT NOT NULL,
        proposal_id TEXT NOT NULL, created_revision INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, instrument_id, intent_date),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_us_session_claims (
        owner_id TEXT NOT NULL, portfolio_id TEXT NOT NULL,
        instrument_id TEXT NOT NULL, session_date TEXT NOT NULL,
        proposal_id TEXT NOT NULL, created_revision INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, instrument_id, session_date),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_reservation_guards (
        owner_id TEXT NOT NULL, portfolio_id TEXT NOT NULL,
        reservation_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ACTIVE','RELEASED','CONSUMED','UNKNOWN','NEEDS_RECONCILIATION')),
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        updated_revision INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, reservation_id),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_fill_guards (
        owner_id TEXT NOT NULL, portfolio_id TEXT NOT NULL,
        fill_id TEXT NOT NULL, payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        created_revision INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, fill_id),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_approval_guards (
        owner_id TEXT NOT NULL, portfolio_id TEXT NOT NULL,
        approval_id TEXT NOT NULL, approval_type TEXT NOT NULL, state TEXT NOT NULL,
        immutable_target_hash TEXT NOT NULL CHECK(length(immutable_target_hash) = 64),
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        updated_revision INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, approval_id),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_rank_clearance_guards (
        owner_id TEXT NOT NULL, portfolio_id TEXT NOT NULL,
        set_id TEXT NOT NULL, failed_rank INTEGER NOT NULL,
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        created_revision INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, set_id, failed_rank),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE paper_price_check_guards (
        owner_id TEXT NOT NULL, portfolio_id TEXT NOT NULL,
        proposal_id TEXT NOT NULL, check_sequence INTEGER NOT NULL,
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
        created_revision INTEGER NOT NULL,
        PRIMARY KEY(owner_id, portfolio_id, proposal_id, check_sequence),
        FOREIGN KEY(owner_id, portfolio_id) REFERENCES paper_aggregates(owner_id, portfolio_id)
    ) WITHOUT ROWID""",
    """CREATE UNIQUE INDEX paper_one_active_reservation_per_proposal
        ON paper_reservation_guards(owner_id, portfolio_id, proposal_id)
        WHERE state IN ('ACTIVE','UNKNOWN','NEEDS_RECONCILIATION')""",
)
PAPER_SCHEMA_CHECKSUM = hashlib.sha256(
    "\n".join(" ".join(statement.split()) for statement in _DDL).encode("utf-8")
).hexdigest()
PAPER_TABLE_NAMES = frozenset(
    {
        "paper_schema_ledger",
        "paper_owner_bindings",
        "paper_aggregates",
        "paper_state_fields",
        "paper_state_revisions",
        "paper_command_receipts",
        "paper_kst_intent_claims",
        "paper_us_session_claims",
        "paper_reservation_guards",
        "paper_fill_guards",
        "paper_approval_guards",
        "paper_rank_clearance_guards",
        "paper_price_check_guards",
    }
)
PAPER_INDEX_NAMES = frozenset({"paper_one_active_reservation_per_proposal"})


def validate_paper_schema_connection(connection: sqlite3.Connection) -> dict[str, int]:
    """Read-only exact-schema validation used by repository startup and backup V2."""

    try:
        tables = frozenset(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        if tables != PAPER_TABLE_NAMES:
            raise PaperPersistenceError("PAPER_SCHEMA_UNSUPPORTED")
        indexes = frozenset(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"
            )
        )
        if indexes != PAPER_INDEX_NAMES:
            raise PaperPersistenceError("PAPER_SCHEMA_UNSUPPORTED")
        row = connection.execute(
            "SELECT schema_version, checksum_sha256, codec_version FROM paper_schema_ledger WHERE revision=?",
            (PAPER_SCHEMA_REVISION,),
        ).fetchone()
        if (
            row is None
            or int(row[0]) != PAPER_SCHEMA_VERSION
            or not hmac.compare_digest(str(row[1]), PAPER_SCHEMA_CHECKSUM)
            or int(row[2]) != PAPER_CODEC_VERSION
            or connection.execute("SELECT count(*) FROM paper_schema_ledger").fetchone()[0] != 1
        ):
            raise PaperPersistenceError("PAPER_SCHEMA_UNSUPPORTED")
        counts: dict[str, int] = {}
        for table in sorted(PAPER_TABLE_NAMES):
            quoted = '"' + table.replace('"', '""') + '"'
            counts[table] = int(connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0])
        return counts
    except PaperPersistenceError:
        raise
    except (sqlite3.Error, TypeError, ValueError, OverflowError):
        raise PaperPersistenceError("PAPER_SCHEMA_UNSUPPORTED") from None


def default_paper_database_path() -> Path:
    return default_data_directory() / PAPER_DATABASE_FILENAME


_LIVE_PATHS: set[tuple[int, str]] = set()
_LIVE_PATHS_LOCK = RLock()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def _prepare_database_path(path: Path, forbidden_paths: tuple[Path, ...]) -> tuple[Path, tuple[int, int]]:
    if not isinstance(path, Path):
        raise PaperPersistenceError("PAPER_PATH_UNSAFE")
    try:
        candidate = path.expanduser()
        if not candidate.is_absolute():
            raise OSError
        parent = candidate.parent.resolve(strict=True)
        if parent != candidate.parent:
            raise OSError
        parent_info = parent.stat(follow_symlinks=False)
        if not stat.S_ISDIR(parent_info.st_mode):
            raise OSError
        if os.name != "nt":
            if parent_info.st_uid != os.getuid():
                raise OSError
            os.chmod(parent, 0o700)
        normalized_forbidden = tuple(item.expanduser().resolve(strict=False) for item in forbidden_paths)
        if candidate.resolve(strict=False) in normalized_forbidden:
            raise OSError
        if candidate.exists() or candidate.is_symlink():
            info = candidate.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or candidate.is_symlink():
                raise OSError
            if os.name != "nt" and (info.st_uid != os.getuid() or info.st_nlink != 1):
                raise OSError
            for forbidden in forbidden_paths:
                if forbidden.exists() and os.path.samefile(candidate, forbidden):
                    raise OSError
            flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(candidate, flags)
        else:
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(candidate, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise OSError
            if os.name != "nt":
                if info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise OSError
                os.fchmod(descriptor, 0o600)
            identity = (info.st_dev, info.st_ino)
        finally:
            os.close(descriptor)
        return candidate, identity
    except (OSError, RuntimeError, ValueError):
        raise PaperPersistenceError("PAPER_PATH_UNSAFE") from None


def _runtime_secure_paths(path: Path, main_identity: tuple[int, int]) -> None:
    """Validate/chmod by pathname only; never open a live SQLite inode (X1)."""

    try:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            try:
                before = candidate.stat(follow_symlinks=False)
            except FileNotFoundError:
                if suffix:
                    continue
                raise
            if not stat.S_ISREG(before.st_mode):
                raise OSError
            if suffix == "" and (before.st_dev, before.st_ino) != main_identity:
                raise OSError
            if os.name != "nt":
                if before.st_uid != os.getuid() or before.st_nlink != 1:
                    raise OSError
                os.chmod(candidate, 0o600)
                after = candidate.stat(follow_symlinks=False)
                if (
                    (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                    or not stat.S_ISREG(after.st_mode)
                    or after.st_uid != os.getuid()
                    or after.st_nlink != 1
                    or stat.S_IMODE(after.st_mode) != 0o600
                ):
                    raise OSError
    except OSError:
        raise PaperPersistenceError("PAPER_PATH_UNSAFE") from None


def _epoch_us(value: datetime) -> int:
    normalized = _utc(value, field_name="PAPER persistence timestamp")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = normalized - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _from_epoch_us(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PaperPersistenceError("PAPER_STATE_CORRUPT")
    try:
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value)
    except (OverflowError, ValueError):
        raise PaperPersistenceError("PAPER_STATE_CORRUPT") from None


def _approval_target_hash(approval: Approval) -> str:
    return _digest(_encode((approval.approval_type, approval.target, approval.new_root_target)))


def _decode_state(text: str) -> _PaperState:
    try:
        value = _decode(text)
    except DomainValidationError:
        raise PaperPersistenceError("PAPER_STATE_CORRUPT") from None
    if type(value) is not _PaperState:
        raise PaperPersistenceError("PAPER_STATE_CORRUPT")
    return value


def _classified_storage_error(exc: BaseException) -> PaperPersistenceError:
    sqlite_code = getattr(exc, "sqlite_errorcode", None)
    sqlite_primary = (
        sqlite_code & 0xFF
        if isinstance(sqlite_code, int) and not isinstance(sqlite_code, bool)
        else None
    )
    if (
        isinstance(exc, OSError)
        and exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}
    ) or sqlite_primary == getattr(sqlite3, "SQLITE_FULL", -1):
        return PaperPersistenceError("PAPER_STORAGE_FULL")
    if sqlite_primary == getattr(sqlite3, "SQLITE_IOERR", -1):
        return PaperPersistenceError("PAPER_STORAGE_IO_ERROR")
    if sqlite_primary in {
        getattr(sqlite3, "SQLITE_BUSY", -1),
        getattr(sqlite3, "SQLITE_LOCKED", -1),
    }:
        return PaperPersistenceError("PAPER_STORAGE_BUSY")
    return PaperPersistenceError("PAPER_STORAGE_UNAVAILABLE")


def _validate_state_graph(state: _PaperState, owner_id: str, portfolio_id: str) -> None:
    try:
        if type(state) is not _PaperState:
            raise DomainValidationError("PAPER state type")
        if state.portfolio is None:
            if any(getattr(state, name) for name in _STATE_FIELD_NAMES if name != "portfolio"):
                raise DomainValidationError("blank PAPER state has rows")
            return
        if state.portfolio.owner_ref != owner_id or state.portfolio.portfolio_id != portfolio_id:
            raise DomainValidationError("PAPER owner/portfolio mismatch")
        if set(state.cash) - set(Currency) or any(type(item) is not Decimal for item in state.cash.values()):
            raise DomainValidationError("PAPER cash type")
        for name, mapping, identity_name in (
            ("budgets", state.budgets, "version_id"),
            ("strategy_versions", state.strategy_versions, "version_id"),
            ("lots", state.lots, "lot_id"),
            ("valuations", state.valuations, "snapshot_id"),
            ("purposes", state.purposes, "trade_purpose_id"),
            ("candidate_sets", state.candidate_sets, "set_id"),
            ("proposals", state.proposals, "proposal_id"),
            ("approvals", state.approvals, "approval_id"),
            ("reservations", state.reservations, "reservation_id"),
            ("fills", state.fills, "fill_id"),
            ("runs", state.runs, "execution_run_id"),
        ):
            for key, item in mapping.items():
                if key != getattr(item, identity_name):
                    raise DomainValidationError(f"PAPER {name} identity")
        if len({item.entry_id for item in state.ledger_entries}) != len(state.ledger_entries):
            raise DomainValidationError("PAPER ledger duplicate")
        if any(item.portfolio_id != portfolio_id for item in state.ledger_entries):
            raise DomainValidationError("PAPER ledger portfolio")
        if len({item.application_id for item in state.strategy_applications}) != len(state.strategy_applications):
            raise DomainValidationError("PAPER strategy application duplicate")
        if any(item.strategy_version_id not in state.strategy_versions for item in state.strategy_applications):
            raise DomainValidationError("PAPER strategy application ref")
        if any(key != value.us_session_date for key, value in state.daily_limits.items()):
            raise DomainValidationError("PAPER daily limit key")
        for key, item in state.candidate_rank_clearances.items():
            if key != (item.set_id, item.failed_rank) or item.set_id not in state.candidate_sets:
                raise DomainValidationError("PAPER candidate clearance ref")
        for item in state.price_condition_checks:
            proposal = state.proposals.get(item.proposal_id)
            if proposal is None or proposal.proposal_hash != item.proposal_hash:
                raise DomainValidationError("PAPER price check ref")
        active_by_proposal: set[str] = set()
        active_states = {ReservationState.ACTIVE, ReservationState.UNKNOWN, ReservationState.NEEDS_RECONCILIATION}
        for item in state.reservations.values():
            if item.proposal_id not in state.proposals:
                raise DomainValidationError("PAPER reservation ref")
            if item.state in active_states:
                if item.proposal_id in active_by_proposal:
                    raise DomainValidationError("PAPER active reservation duplicate")
                active_by_proposal.add(item.proposal_id)
        for item in state.fills.values():
            if item.proposal_id not in state.proposals or item.reservation_id not in state.reservations:
                raise DomainValidationError("PAPER fill ref")
        if len({item.audit_id for item in state.audits}) != len(state.audits):
            raise DomainValidationError("PAPER audit duplicate")
        for (instrument, claim_date), proposal_id in state.kst_intent_claims.items():
            proposal = state.proposals.get(proposal_id)
            if proposal is None or proposal.instrument_id != instrument or proposal.kst_intent_date != claim_date:
                raise DomainValidationError("PAPER KST claim ref")
        for (instrument, session_date), proposal_id in state.us_session_claims.items():
            proposal = state.proposals.get(proposal_id)
            if proposal is None or proposal.instrument_id != instrument or proposal.us_session_date != session_date:
                raise DomainValidationError("PAPER US claim ref")
        for run in state.runs.values():
            if run.portfolio_id != portfolio_id or any(proposal_id not in state.proposals for proposal_id, _ in run.included_proposals):
                raise DomainValidationError("PAPER execution run ref")
    except DomainValidationError:
        raise PaperPersistenceError("PAPER_STATE_CORRUPT") from None


def _validate_transition(before: _PaperState, after: _PaperState) -> None:
    def same_except(old: Any, new: Any, mutable_fields: frozenset[str]) -> bool:
        if type(old) is not type(new) or not is_dataclass(old):
            return False
        return all(
            item.name in mutable_fields
            or getattr(old, item.name) == getattr(new, item.name)
            for item in fields(old)
        )

    try:
        if before.portfolio is not None and before.portfolio != after.portfolio:
            raise DomainValidationError("portfolio immutable")
        for name in ("ledger_entries", "strategy_applications", "price_condition_checks", "audits"):
            old = getattr(before, name)
            new = getattr(after, name)
            if len(new) < len(old) or new[: len(old)] != old:
                raise DomainValidationError("append-only PAPER history changed")
        for name in (
            "cash",
            "budgets",
            "strategy_versions",
            "lots",
            "valuations",
            "daily_limits",
            "purposes",
            "candidate_sets",
            "candidate_rank_clearances",
            "proposals",
            "approvals",
            "reservations",
            "fills",
            "runs",
            "kst_intent_claims",
            "us_session_claims",
        ):
            old = getattr(before, name)
            new = getattr(after, name)
            if not set(old).issubset(new):
                raise DomainValidationError("PAPER state row removed")
        for name in ("budgets", "strategy_versions", "valuations", "purposes", "candidate_rank_clearances", "fills", "kst_intent_claims", "us_session_claims"):
            old = getattr(before, name)
            new = getattr(after, name)
            if any(new[key] != value for key, value in old.items()):
                raise DomainValidationError("immutable PAPER row changed")
        mutable_contracts: tuple[tuple[str, frozenset[str]], ...] = (
            (
                "lots",
                frozenset({"remaining_quantity", "remaining_cost"}),
            ),
            (
                "daily_limits",
                frozenset({"executed_gross_krw", "active_reserved_gross_krw"}),
            ),
            (
                "candidate_sets",
                frozenset({"state", "approval_ref"}),
            ),
            (
                "proposals",
                frozenset({"state"}),
            ),
            (
                "approvals",
                frozenset(
                    {
                        "state",
                        "bound_to_ref",
                        "state_changed_at",
                        "invalidation_reason",
                    }
                ),
            ),
            (
                "reservations",
                frozenset(
                    {
                        "remaining_cash_amount",
                        "remaining_daily_gross_krw",
                        "remaining_sell_lot_quantities",
                        "remaining_strategy_budget_krw",
                        "remaining_monthly_budget_krw",
                        "state",
                        "released_at",
                        "reason_code",
                    }
                ),
            ),
            (
                "runs",
                frozenset(
                    {
                        "state",
                        "attempts",
                        "started_at",
                        "completion_kind",
                        "terminal_reason",
                        "ended_at",
                        "state_reason_code",
                        "state_evidence_ref",
                        "state_changed_at",
                    }
                ),
            ),
        )
        for name, mutable_fields in mutable_contracts:
            old_mapping = getattr(before, name)
            new_mapping = getattr(after, name)
            if any(
                not same_except(old_value, new_mapping[key], mutable_fields)
                for key, old_value in old_mapping.items()
            ):
                raise DomainValidationError("immutable PAPER row content changed")
    except DomainValidationError:
        raise PaperPersistenceError("PAPER_ILLEGAL_STATE_TRANSITION") from None


class _OneCommandRepository:
    """Private PaperRepository implementation valid only during execute()."""

    def __init__(self, state: _PaperState) -> None:
        self._state = copy.deepcopy(state)
        self._used = False

    def transact(self, operation: Callable[[_PaperState], T]) -> T:
        if self._used:
            raise DomainValidationError("하나의 PAPER command에는 mutation 하나만 허용됩니다.")
        self._used = True
        try:
            with localcontext(_paper_decimal_context(exact=True)):
                candidate = copy.deepcopy(self._state)
                result = operation(candidate)
                self._state = copy.deepcopy(candidate)
                return copy.deepcopy(result)
        except DecimalException as exc:
            raise DomainValidationError("PAPER 상태를 정확하게 계산할 수 없습니다.") from exc
        except TypeError:
            raise DomainValidationError("PAPER 입력 형식이 올바르지 않습니다.") from None

    def snapshot(self) -> _PaperState:
        return copy.deepcopy(self._state)

    @property
    def used(self) -> bool:
        return self._used


class SQLitePaperRepository:
    """Owner-scoped, persistent P2 adapter; not directly usable as a P1 port."""

    def __init__(
        self,
        path: Path,
        *,
        owner_id: str,
        portfolio_id: str,
        owner_guard: Callable[[str | None], None],
        forbidden_paths: tuple[Path, ...] = (),
        clock: Callable[[], datetime] | None = None,
        fault_hook: Callable[[str], None] | None = None,
        mutation_guard: Callable[[], None] | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.owner_id = nonempty(owner_id, field_name="PAPER owner_id")
        self.portfolio_id = nonempty(portfolio_id, field_name="PAPER portfolio_id")
        if not callable(owner_guard):
            raise DomainValidationError("PAPER owner guard가 필요합니다.")
        self._owner_guard = owner_guard
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._fault_hook = fault_hook
        if mutation_guard is not None and not callable(mutation_guard):
            raise DomainValidationError("PAPER mutation guard 형식이 올바르지 않습니다.")
        self._mutation_guard = mutation_guard
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or not 1 <= busy_timeout_ms <= 60_000
        ):
            raise DomainValidationError("PAPER SQLite busy timeout 형식이 올바르지 않습니다.")
        self._busy_timeout_ms = busy_timeout_ms
        self._lock = RLock()
        self._closed = False
        self._pid = os.getpid()
        self._check_owner_guard()
        key: str | None = None
        connection: sqlite3.Connection | None = None
        try:
            candidate = path.expanduser()
            key = _path_key(candidate)
            live_key = (os.getpid(), key)
            with _LIVE_PATHS_LOCK:
                if live_key in _LIVE_PATHS:
                    raise PaperPersistenceError("PAPER_DATABASE_ALREADY_OPEN")
                _LIVE_PATHS.add(live_key)
            self.path, self._main_identity = _prepare_database_path(candidate, forbidden_paths)
            connection = sqlite3.connect(
                self.path,
                timeout=busy_timeout_ms / 1_000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            connection.execute("PRAGMA synchronous=FULL")
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise PaperPersistenceError("PAPER_SQLITE_CONFIGURATION_FAILED")
            self._connection = connection
            self._live_key = live_key
            self._initialize_or_validate_schema()
            self._bind_owner()
            integrity = self._connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise PaperPersistenceError("PAPER_INTEGRITY_FAILED")
            _runtime_secure_paths(self.path, self._main_identity)
            # Hydration at startup ensures an apparently valid schema cannot
            # defer state corruption until the first mutation.
            with self._transaction(self, immediate=False):
                self._hydrate_locked()
        except Exception as exc:
            if connection is not None:
                connection.close()
            if key is not None:
                with _LIVE_PATHS_LOCK:
                    _LIVE_PATHS.discard((os.getpid(), key))
            if isinstance(exc, (PaperPersistenceError, DomainValidationError)):
                raise
            if isinstance(exc, (sqlite3.Error, OSError)):
                raise _classified_storage_error(exc) from None
            raise PaperPersistenceError("PAPER_STORAGE_UNAVAILABLE") from None

    def __enter__(self) -> "SQLitePaperRepository":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _emit_fault(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    def _check_owner_guard(self) -> None:
        try:
            self._owner_guard(self.owner_id)
        except PaperPersistenceError:
            raise
        except Exception:
            raise PaperPersistenceError("PAPER_OWNER_SCOPE_REJECTED") from None

    def _require_live(self) -> None:
        if self._closed or os.getpid() != self._pid:
            raise PaperPersistenceError("PAPER_REPOSITORY_NOT_AVAILABLE")
        self._check_owner_guard()
        _runtime_secure_paths(self.path, self._main_identity)

    def _initialize_or_validate_schema(self) -> None:
        tables = frozenset(
            str(row[0])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        if tables:
            validate_paper_schema_connection(self._connection)
            return
        now_us = _epoch_us(self._clock())
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for statement in _DDL:
                self._connection.execute(statement)
            self._connection.execute(
                "INSERT INTO paper_schema_ledger(revision,schema_version,checksum_sha256,codec_version,created_at_utc_us) VALUES(?,?,?,?,?)",
                (PAPER_SCHEMA_REVISION, PAPER_SCHEMA_VERSION, PAPER_SCHEMA_CHECKSUM, PAPER_CODEC_VERSION, now_us),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        validate_paper_schema_connection(self._connection)

    def _bind_owner(self) -> None:
        binding_hash = hashlib.sha256(("paper-owner-binding-v1\0" + self.owner_id).encode("utf-8")).hexdigest()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT owner_id,binding_hash FROM paper_owner_bindings WHERE singleton=1"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO paper_owner_bindings(singleton,owner_id,bound_at_utc_us,binding_hash) VALUES(1,?,?,?)",
                    (self.owner_id, _epoch_us(self._clock()), binding_hash),
                )
            elif not (
                hmac.compare_digest(str(row["owner_id"]), self.owner_id)
                and hmac.compare_digest(str(row["binding_hash"]), binding_hash)
            ):
                raise PaperPersistenceError("PAPER_OWNER_MISMATCH")
            if self._connection.execute("SELECT count(*) FROM paper_owner_bindings").fetchone()[0] != 1:
                raise PaperPersistenceError("PAPER_OWNER_MISMATCH")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    class _transaction:
        def __init__(self, repository: "SQLitePaperRepository", immediate: bool) -> None:
            self.repository = repository
            self.immediate = immediate

        def __enter__(self) -> sqlite3.Connection:
            self.repository._connection.execute("BEGIN IMMEDIATE" if self.immediate else "BEGIN")
            return self.repository._connection

        def __exit__(self, exc_type, exc, traceback) -> bool:
            if exc_type is None:
                self.repository._connection.commit()
            else:
                self.repository._connection.rollback()
            return False

    def _scoped_count(self, table: str) -> int:
        return int(
            self._connection.execute(
                f'SELECT count(*) FROM "{table}" WHERE owner_id=? AND portfolio_id=?',
                (self.owner_id, self.portfolio_id),
            ).fetchone()[0]
        )

    def _hydrate_locked(self) -> PaperStoredSnapshot:
        validate_paper_schema_connection(self._connection)
        aggregate = self._connection.execute(
            "SELECT revision,state_json,state_hash,last_event_seq FROM paper_aggregates WHERE owner_id=? AND portfolio_id=?",
            (self.owner_id, self.portfolio_id),
        ).fetchone()
        scoped_tables = (
            "paper_state_fields",
            "paper_state_revisions",
            "paper_command_receipts",
            "paper_kst_intent_claims",
            "paper_us_session_claims",
            "paper_reservation_guards",
            "paper_fill_guards",
            "paper_approval_guards",
            "paper_rank_clearance_guards",
            "paper_price_check_guards",
        )
        if aggregate is None:
            if any(self._scoped_count(table) for table in scoped_tables):
                raise PaperPersistenceError("PAPER_STATE_CORRUPT")
            state = _PaperState()
            _validate_state_graph(state, self.owner_id, self.portfolio_id)
            return PaperStoredSnapshot(state, 0, _ZERO_HASH)
        revision = int(aggregate["revision"])
        state_text = str(aggregate["state_json"])
        state_hash = str(aggregate["state_hash"])
        if revision < 1 or int(aggregate["last_event_seq"]) != revision or not hmac.compare_digest(_digest(state_text), state_hash):
            raise PaperPersistenceError("PAPER_STATE_CORRUPT")
        decoded = _decode_state(state_text)

        field_rows = self._connection.execute(
            "SELECT field_name,field_order,payload_json,payload_hash,revision FROM paper_state_fields WHERE owner_id=? AND portfolio_id=? ORDER BY field_order",
            (self.owner_id, self.portfolio_id),
        ).fetchall()
        if len(field_rows) != len(_STATE_FIELD_NAMES):
            raise PaperPersistenceError("PAPER_STATE_CORRUPT")
        field_values: dict[str, Any] = {}
        for order, (row, expected_name) in enumerate(zip(field_rows, _STATE_FIELD_NAMES, strict=True)):
            payload = str(row["payload_json"])
            if (
                row["field_name"] != expected_name
                or int(row["field_order"]) != order
                or int(row["revision"]) != revision
                or not hmac.compare_digest(_digest(payload), str(row["payload_hash"]))
            ):
                raise PaperPersistenceError("PAPER_STATE_CORRUPT")
            try:
                field_values[expected_name] = _decode(payload)
            except DomainValidationError:
                raise PaperPersistenceError("PAPER_STATE_CORRUPT") from None
        try:
            projected = _PaperState(**field_values)
        except (TypeError, ValueError, DomainValidationError):
            raise PaperPersistenceError("PAPER_STATE_CORRUPT") from None
        if _encode(projected) != state_text or projected != decoded:
            raise PaperPersistenceError("PAPER_STATE_CORRUPT")

        events = self._connection.execute(
            "SELECT revision,previous_state_hash,state_hash,state_json FROM paper_state_revisions WHERE owner_id=? AND portfolio_id=? ORDER BY revision",
            (self.owner_id, self.portfolio_id),
        ).fetchall()
        if len(events) != revision:
            raise PaperPersistenceError("PAPER_STATE_CORRUPT")
        prior = _ZERO_HASH
        for expected_revision, row in enumerate(events, start=1):
            payload = str(row["state_json"])
            current_hash = str(row["state_hash"])
            if (
                int(row["revision"]) != expected_revision
                or not hmac.compare_digest(str(row["previous_state_hash"]), prior)
                or not hmac.compare_digest(_digest(payload), current_hash)
                or type(_decode_state(payload)) is not _PaperState
            ):
                raise PaperPersistenceError("PAPER_STATE_CORRUPT")
            prior = current_hash
        if not hmac.compare_digest(prior, state_hash) or str(events[-1]["state_json"]) != state_text:
            raise PaperPersistenceError("PAPER_STATE_CORRUPT")
        _validate_state_graph(projected, self.owner_id, self.portfolio_id)
        self._verify_guards_locked(projected)
        self._verify_receipts_locked(revision)
        return PaperStoredSnapshot(projected, revision, state_hash)

    def _verify_guards_locked(self, state: _PaperState) -> None:
        def rows(table: str, columns: str) -> list[sqlite3.Row]:
            return self._connection.execute(
                f'SELECT {columns} FROM "{table}" WHERE owner_id=? AND portfolio_id=? ORDER BY 1,2',
                (self.owner_id, self.portfolio_id),
            ).fetchall()

        expected_kst = sorted((instrument, day.isoformat(), proposal) for (instrument, day), proposal in state.kst_intent_claims.items())
        actual_kst = [(str(row[0]), str(row[1]), str(row[2])) for row in rows("paper_kst_intent_claims", "instrument_id,intent_date,proposal_id")]
        expected_us = sorted((instrument, day.isoformat(), proposal) for (instrument, day), proposal in state.us_session_claims.items())
        actual_us = [(str(row[0]), str(row[1]), str(row[2])) for row in rows("paper_us_session_claims", "instrument_id,session_date,proposal_id")]
        expected_reservations = sorted((item.reservation_id, item.proposal_id, item.state.value, _digest(_encode(item))) for item in state.reservations.values())
        actual_reservations = [(str(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows("paper_reservation_guards", "reservation_id,proposal_id,state,payload_hash")]
        expected_fills = sorted((item.fill_id, _digest(_encode(item))) for item in state.fills.values())
        actual_fills = [(str(row[0]), str(row[1])) for row in rows("paper_fill_guards", "fill_id,payload_hash")]
        expected_approvals = sorted((item.approval_id, item.approval_type.value, item.state.value, _approval_target_hash(item), _digest(_encode(item))) for item in state.approvals.values())
        actual_approvals = [(str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4])) for row in rows("paper_approval_guards", "approval_id,approval_type,state,immutable_target_hash,payload_hash")]
        expected_clearances = sorted((item.set_id, item.failed_rank, _digest(_encode(item))) for item in state.candidate_rank_clearances.values())
        actual_clearances = [(str(row[0]), int(row[1]), str(row[2])) for row in rows("paper_rank_clearance_guards", "set_id,failed_rank,payload_hash")]
        sequences: dict[str, int] = {}
        expected_checks: list[tuple[str, int, str]] = []
        for item in state.price_condition_checks:
            sequences[item.proposal_id] = sequences.get(item.proposal_id, 0) + 1
            expected_checks.append((item.proposal_id, sequences[item.proposal_id], _digest(_encode(item))))
        expected_checks.sort()
        actual_checks = [(str(row[0]), int(row[1]), str(row[2])) for row in rows("paper_price_check_guards", "proposal_id,check_sequence,payload_hash")]
        if not all((expected_kst == actual_kst, expected_us == actual_us, expected_reservations == actual_reservations, expected_fills == actual_fills, expected_approvals == actual_approvals, expected_clearances == actual_clearances, expected_checks == actual_checks)):
            raise PaperPersistenceError("PAPER_STATE_CORRUPT")

    def _verify_receipts_locked(self, current_revision: int) -> None:
        receipt_rows = self._connection.execute(
            "SELECT idempotency_key,command_type,request_fingerprint,status,result_revision,result_json,result_hash,completed_at_utc_us "
            "FROM paper_command_receipts WHERE owner_id=? AND portfolio_id=?",
            (self.owner_id, self.portfolio_id),
        ).fetchall()
        for row in receipt_rows:
            result_text = str(row["result_json"])
            request_fingerprint = str(row["request_fingerprint"])
            result_hash = str(row["result_hash"])
            try:
                result_revision = int(row["result_revision"])
                _from_epoch_us(row["completed_at_utc_us"])
                _decode(result_text)
            except (DomainValidationError, PaperPersistenceError, TypeError, ValueError, OverflowError):
                raise PaperPersistenceError("PAPER_RECONCILIATION_REQUIRED") from None
            if (
                not _IDEMPOTENCY_KEY.fullmatch(str(row["idempotency_key"]))
                or not _COMMAND_NAME.fullmatch(str(row["command_type"]))
                or row["status"] != "COMPLETED"
                or not re.fullmatch(r"[0-9a-f]{64}", request_fingerprint)
                or not re.fullmatch(r"[0-9a-f]{64}", result_hash)
                or not hmac.compare_digest(_digest(result_text), result_hash)
                or not 1 <= result_revision <= current_revision
            ):
                raise PaperPersistenceError("PAPER_RECONCILIATION_REQUIRED")

    def _save_state_locked(
        self,
        *,
        before: PaperStoredSnapshot,
        state: _PaperState,
        revision: int,
        fingerprint: str,
        committed_at_us: int,
    ) -> str:
        state_text = _encode(state)
        state_hash = _digest(state_text)
        if before.revision == 0:
            self._connection.execute(
                "INSERT INTO paper_aggregates(owner_id,portfolio_id,revision,state_json,state_hash,last_event_seq,updated_at_utc_us) VALUES(?,?,?,?,?,?,?)",
                (self.owner_id, self.portfolio_id, revision, state_text, state_hash, revision, committed_at_us),
            )
        else:
            updated = self._connection.execute(
                "UPDATE paper_aggregates SET revision=?,state_json=?,state_hash=?,last_event_seq=?,updated_at_utc_us=? WHERE owner_id=? AND portfolio_id=? AND revision=?",
                (revision, state_text, state_hash, revision, committed_at_us, self.owner_id, self.portfolio_id, before.revision),
            )
            if updated.rowcount != 1:
                raise PaperPersistenceError("PAPER_REVISION_CONFLICT")
        self._connection.execute(
            "DELETE FROM paper_state_fields WHERE owner_id=? AND portfolio_id=?",
            (self.owner_id, self.portfolio_id),
        )
        for order, name in enumerate(_STATE_FIELD_NAMES):
            payload = _encode(getattr(state, name))
            self._connection.execute(
                "INSERT INTO paper_state_fields(owner_id,portfolio_id,field_name,field_order,payload_json,payload_hash,revision) VALUES(?,?,?,?,?,?,?)",
                (self.owner_id, self.portfolio_id, name, order, payload, _digest(payload), revision),
            )
        self._connection.execute(
            "INSERT INTO paper_state_revisions(owner_id,portfolio_id,revision,previous_state_hash,state_hash,state_json,command_fingerprint,committed_at_utc_us) VALUES(?,?,?,?,?,?,?,?)",
            (self.owner_id, self.portfolio_id, revision, before.state_hash, state_hash, state_text, fingerprint, committed_at_us),
        )
        self._sync_guards_locked(state, revision)
        return state_hash

    def _sync_guards_locked(self, state: _PaperState, revision: int) -> None:
        for table, mapping, date_column in (
            ("paper_kst_intent_claims", state.kst_intent_claims, "intent_date"),
            ("paper_us_session_claims", state.us_session_claims, "session_date"),
        ):
            existing = {
                (str(row[0]), str(row[1])): str(row[2])
                for row in self._connection.execute(
                    f'SELECT instrument_id,{date_column},proposal_id FROM "{table}" WHERE owner_id=? AND portfolio_id=?',
                    (self.owner_id, self.portfolio_id),
                )
            }
            expected = {(instrument, day.isoformat()): proposal for (instrument, day), proposal in mapping.items()}
            if any(expected.get(key) != value for key, value in existing.items()):
                raise PaperPersistenceError("PAPER_STATE_CORRUPT")
            for (instrument, day_text), proposal in expected.items():
                if (instrument, day_text) not in existing:
                    self._connection.execute(
                        f'INSERT INTO "{table}"(owner_id,portfolio_id,instrument_id,{date_column},proposal_id,created_revision) VALUES(?,?,?,?,?,?)',
                        (self.owner_id, self.portfolio_id, instrument, day_text, proposal, revision),
                    )
        self._connection.execute("DELETE FROM paper_reservation_guards WHERE owner_id=? AND portfolio_id=?", (self.owner_id, self.portfolio_id))
        for item in state.reservations.values():
            self._connection.execute(
                "INSERT INTO paper_reservation_guards(owner_id,portfolio_id,reservation_id,proposal_id,state,payload_hash,updated_revision) VALUES(?,?,?,?,?,?,?)",
                (self.owner_id, self.portfolio_id, item.reservation_id, item.proposal_id, item.state.value, _digest(_encode(item)), revision),
            )
        existing_fills = {
            str(row[0]): str(row[1])
            for row in self._connection.execute(
                "SELECT fill_id,payload_hash FROM paper_fill_guards WHERE owner_id=? AND portfolio_id=?",
                (self.owner_id, self.portfolio_id),
            )
        }
        for item in state.fills.values():
            digest = _digest(_encode(item))
            if item.fill_id in existing_fills and not hmac.compare_digest(existing_fills[item.fill_id], digest):
                raise PaperPersistenceError("PAPER_STATE_CORRUPT")
            if item.fill_id not in existing_fills:
                self._connection.execute(
                    "INSERT INTO paper_fill_guards(owner_id,portfolio_id,fill_id,payload_hash,created_revision) VALUES(?,?,?,?,?)",
                    (self.owner_id, self.portfolio_id, item.fill_id, digest, revision),
                )
        self._connection.execute("DELETE FROM paper_approval_guards WHERE owner_id=? AND portfolio_id=?", (self.owner_id, self.portfolio_id))
        for item in state.approvals.values():
            self._connection.execute(
                "INSERT INTO paper_approval_guards(owner_id,portfolio_id,approval_id,approval_type,state,immutable_target_hash,payload_hash,updated_revision) VALUES(?,?,?,?,?,?,?,?)",
                (self.owner_id, self.portfolio_id, item.approval_id, item.approval_type.value, item.state.value, _approval_target_hash(item), _digest(_encode(item)), revision),
            )
        existing_clearances = {
            (str(row[0]), int(row[1])): str(row[2])
            for row in self._connection.execute(
                "SELECT set_id,failed_rank,payload_hash FROM paper_rank_clearance_guards WHERE owner_id=? AND portfolio_id=?",
                (self.owner_id, self.portfolio_id),
            )
        }
        for item in state.candidate_rank_clearances.values():
            key = (item.set_id, item.failed_rank)
            digest = _digest(_encode(item))
            if key in existing_clearances and not hmac.compare_digest(existing_clearances[key], digest):
                raise PaperPersistenceError("PAPER_STATE_CORRUPT")
            if key not in existing_clearances:
                self._connection.execute(
                    "INSERT INTO paper_rank_clearance_guards(owner_id,portfolio_id,set_id,failed_rank,payload_hash,created_revision) VALUES(?,?,?,?,?,?)",
                    (self.owner_id, self.portfolio_id, item.set_id, item.failed_rank, digest, revision),
                )
        existing_checks = {
            (str(row[0]), int(row[1])): str(row[2])
            for row in self._connection.execute(
                "SELECT proposal_id,check_sequence,payload_hash FROM paper_price_check_guards WHERE owner_id=? AND portfolio_id=?",
                (self.owner_id, self.portfolio_id),
            )
        }
        sequences: dict[str, int] = {}
        for item in state.price_condition_checks:
            sequences[item.proposal_id] = sequences.get(item.proposal_id, 0) + 1
            key = (item.proposal_id, sequences[item.proposal_id])
            digest = _digest(_encode(item))
            if key in existing_checks and not hmac.compare_digest(existing_checks[key], digest):
                raise PaperPersistenceError("PAPER_STATE_CORRUPT")
            if key not in existing_checks:
                self._connection.execute(
                    "INSERT INTO paper_price_check_guards(owner_id,portfolio_id,proposal_id,check_sequence,payload_hash,created_revision) VALUES(?,?,?,?,?,?)",
                    (self.owner_id, self.portfolio_id, item.proposal_id, key[1], digest, revision),
                )

    def snapshot_with_revision(self) -> PaperStoredSnapshot:
        with self._lock:
            self._require_live()
            try:
                with self._transaction(self, immediate=False):
                    result = self._hydrate_locked()
                return PaperStoredSnapshot(copy.deepcopy(result.state), result.revision, result.state_hash)
            except PaperPersistenceError:
                raise
            except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError):
                raise PaperPersistenceError("PAPER_STORAGE_UNAVAILABLE") from None

    def snapshot(self) -> _PaperState:
        return self.snapshot_with_revision().state

    def execute(
        self,
        envelope: PaperCommandEnvelope,
        callback: Callable[[PaperEngine], T],
    ) -> PaperCommitResult[T]:
        if type(envelope) is not PaperCommandEnvelope or not callable(callback):
            raise DomainValidationError("PAPER command 형식이 올바르지 않습니다.")
        if envelope.owner_id != self.owner_id or envelope.portfolio_id != self.portfolio_id:
            raise PaperPersistenceError("PAPER_OWNER_SCOPE_REJECTED")
        request_text = _encode(
            (
                envelope.command_type,
                envelope.owner_id,
                envelope.portfolio_id,
                envelope.request_payload,
            )
        )
        fingerprint = _digest(request_text)
        with self._lock:
            self._require_live()
            committed_result: PaperCommitResult[T] | None = None
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._emit_fault("after_begin")
                receipt = self._connection.execute(
                    "SELECT command_type,request_fingerprint,status,result_revision,result_json,result_hash,completed_at_utc_us FROM paper_command_receipts WHERE owner_id=? AND portfolio_id=? AND idempotency_key=?",
                    (self.owner_id, self.portfolio_id, envelope.idempotency_key),
                ).fetchone()
                if receipt is not None:
                    if not (
                        receipt["command_type"] == envelope.command_type
                        and hmac.compare_digest(str(receipt["request_fingerprint"]), fingerprint)
                    ):
                        raise PaperPersistenceError("IDEMPOTENCY_KEY_CONFLICT")
                    result_text = str(receipt["result_json"])
                    if receipt["status"] != "COMPLETED" or not hmac.compare_digest(_digest(result_text), str(receipt["result_hash"])):
                        raise PaperPersistenceError("PAPER_RECONCILIATION_REQUIRED")
                    try:
                        value = _decode(result_text)
                    except DomainValidationError:
                        raise PaperPersistenceError("PAPER_RECONCILIATION_REQUIRED") from None
                    current = self._hydrate_locked()
                    if not 1 <= int(receipt["result_revision"]) <= current.revision:
                        raise PaperPersistenceError("PAPER_RECONCILIATION_REQUIRED")
                    committed_result = PaperCommitResult(
                        value,
                        int(receipt["result_revision"]),
                        True,
                        _from_epoch_us(receipt["completed_at_utc_us"]),
                    )
                    self._connection.commit()
                else:
                    if self._mutation_guard is not None:
                        try:
                            self._mutation_guard()
                        except PaperPersistenceError:
                            raise
                        except DomainValidationError:
                            raise PaperPersistenceError("PAPER_MUTATION_BLOCKED") from None
                        except Exception:
                            raise PaperPersistenceError("PAPER_MUTATION_BLOCKED") from None
                    before = self._hydrate_locked()
                    self._emit_fault("after_hydrate")
                    if before.revision != envelope.expected_revision:
                        raise PaperPersistenceError("PAPER_REVISION_CONFLICT")
                    transaction_repository = _OneCommandRepository(before.state)
                    value = callback(PaperEngine(transaction_repository))
                    if not transaction_repository.used:
                        raise PaperPersistenceError("PAPER_COMMAND_DID_NOT_MUTATE")
                    state = transaction_repository.snapshot()
                    _validate_transition(before.state, state)
                    _validate_state_graph(state, self.owner_id, self.portfolio_id)
                    self._emit_fault("after_callback")
                    revision = before.revision + 1
                    completed_at = _utc(self._clock(), field_name="PAPER completed_at")
                    completed_at_us = _epoch_us(completed_at)
                    self._save_state_locked(
                        before=before,
                        state=state,
                        revision=revision,
                        fingerprint=fingerprint,
                        committed_at_us=completed_at_us,
                    )
                    self._emit_fault("after_projection")
                    result_text = _encode(value)
                    self._connection.execute(
                        "INSERT INTO paper_command_receipts(owner_id,portfolio_id,idempotency_key,command_type,request_fingerprint,status,result_revision,result_json,result_hash,completed_at_utc_us) VALUES(?,?,?,?,?,'COMPLETED',?,?,?,?)",
                        (
                            self.owner_id,
                            self.portfolio_id,
                            envelope.idempotency_key,
                            envelope.command_type,
                            fingerprint,
                            revision,
                            result_text,
                            _digest(result_text),
                            completed_at_us,
                        ),
                    )
                    self._emit_fault("before_commit")
                    self._connection.commit()
                    committed_result = PaperCommitResult(copy.deepcopy(value), revision, False, completed_at)
                _runtime_secure_paths(self.path, self._main_identity)
            except PaperPersistenceError:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            except (sqlite3.IntegrityError, sqlite3.OperationalError, sqlite3.DatabaseError, OSError) as exc:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise _classified_storage_error(exc) from None
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            assert committed_result is not None
            self._emit_fault("after_commit")
            return committed_result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True
            with _LIVE_PATHS_LOCK:
                _LIVE_PATHS.discard(self._live_key)


def validate_paper_database_connection(connection: sqlite3.Connection) -> dict[str, int]:
    """Validate a read-only PAPER backup snapshot through every persisted layer.

    The caller owns ``connection``.  This function changes no pragma, table, or
    row and restores the caller's row factory before returning.
    """

    if not isinstance(connection, sqlite3.Connection):
        raise PaperPersistenceError("PAPER_SCHEMA_UNSUPPORTED")
    prior_row_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        counts = validate_paper_schema_connection(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise PaperPersistenceError("PAPER_INTEGRITY_FAILED")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise PaperPersistenceError("PAPER_STATE_CORRUPT")
        bindings = connection.execute(
            "SELECT singleton,owner_id,binding_hash FROM paper_owner_bindings"
        ).fetchall()
        if len(bindings) != 1 or int(bindings[0]["singleton"]) != 1:
            raise PaperPersistenceError("PAPER_OWNER_MISMATCH")
        owner_id = str(bindings[0]["owner_id"])
        try:
            nonempty(owner_id, field_name="PAPER owner_id")
        except DomainValidationError:
            raise PaperPersistenceError("PAPER_OWNER_MISMATCH") from None
        expected_binding_hash = hashlib.sha256(
            ("paper-owner-binding-v1\0" + owner_id).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(str(bindings[0]["binding_hash"]), expected_binding_hash):
            raise PaperPersistenceError("PAPER_OWNER_MISMATCH")
        aggregates = connection.execute(
            "SELECT owner_id,portfolio_id FROM paper_aggregates ORDER BY owner_id,portfolio_id"
        ).fetchall()
        scoped_tables = PAPER_TABLE_NAMES - {
            "paper_schema_ledger",
            "paper_owner_bindings",
            "paper_aggregates",
        }
        if not aggregates and any(counts[table] for table in scoped_tables):
            raise PaperPersistenceError("PAPER_STATE_CORRUPT")
        for row in aggregates:
            if str(row["owner_id"]) != owner_id:
                raise PaperPersistenceError("PAPER_OWNER_MISMATCH")
            portfolio_id = str(row["portfolio_id"])
            try:
                nonempty(portfolio_id, field_name="PAPER portfolio_id")
            except DomainValidationError:
                raise PaperPersistenceError("PAPER_STATE_CORRUPT") from None
            probe = object.__new__(SQLitePaperRepository)
            probe._connection = connection
            probe.owner_id = owner_id
            probe.portfolio_id = portfolio_id
            probe._hydrate_locked()
        counts["paper_validated_portfolios"] = len(aggregates)
        return counts
    except PaperPersistenceError:
        raise
    except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError):
        raise PaperPersistenceError("PAPER_STATE_CORRUPT") from None
    finally:
        connection.row_factory = prior_row_factory


__all__ = [
    "PAPER_CODEC_VERSION",
    "PAPER_DATABASE_FILENAME",
    "PAPER_INDEX_NAMES",
    "PAPER_SCHEMA_CHECKSUM",
    "PAPER_SCHEMA_REVISION",
    "PAPER_SCHEMA_VERSION",
    "PAPER_TABLE_NAMES",
    "PaperCommandEnvelope",
    "PaperCommitResult",
    "PaperPersistenceError",
    "PaperStoredSnapshot",
    "SQLitePaperRepository",
    "default_paper_database_path",
    "validate_paper_schema_connection",
    "validate_paper_database_connection",
]
