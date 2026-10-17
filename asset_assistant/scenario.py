"""OFFLINE JSON 입력을 도메인 객체로 바꾸는 엄격한 스키마 계층."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

from .domain import (
    OFFLINE_MODE,
    Account,
    AssetClass,
    DataRealm,
    DeclaredAssetPlan,
    DomainValidationError,
    PolicyStatus,
    PositionRecord,
    Purpose,
    ValueStatus,
    aware_datetime,
    krw_amount,
    nonempty,
)
from .ledger import PortfolioLedger


@dataclass(frozen=True)
class CashflowInput:
    year: int
    monthly_net_income_krw: Decimal
    essential_expenses_krw: Decimal


@dataclass(frozen=True)
class GoalInput:
    actual_year_end_assets_krw: Decimal
    planned_next_year_investment_krw: Decimal


@dataclass(frozen=True)
class SatelliteInput:
    expected_annual_new_investment_krw: Decimal
    measured_loss_krw: Decimal | None


@dataclass(frozen=True)
class OfflineScenario:
    schema_version: int
    mode: str
    scenario_label: str
    realm: DataRealm
    as_of: datetime
    declared_initial_assets: DeclaredAssetPlan
    ledger: PortfolioLedger
    allocation_year: int
    cashflow: CashflowInput | None
    goal: GoalInput | None
    satellite: SatelliteInput | None
    assumptions: tuple[str, ...]
    policy_status: PolicyStatus = PolicyStatus.PROPOSED_UNCONFIRMED


def _object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DomainValidationError(f"{field_name}: JSON 객체여야 합니다.")
    return value


def _list(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise DomainValidationError(f"{field_name}: JSON 배열이어야 합니다.")
    return value


def _keys(
    value: dict[str, Any],
    *,
    field_name: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - value.keys()
    unexpected = value.keys() - required - optional
    if missing:
        raise DomainValidationError(f"{field_name}: 필수 필드 누락 {sorted(missing)}")
    if unexpected:
        raise DomainValidationError(
            f"{field_name}: 알 수 없는 필드가 있습니다. 입력값은 오류에 표시하지 않습니다."
        )


def _enum(enum_type: type, value: Any, *, field_name: str):
    try:
        return enum_type(value)
    except (ValueError, TypeError) as exc:
        raise DomainValidationError(
            f"{field_name}: 허용되지 않은 값입니다. 입력값은 오류에 표시하지 않습니다."
        ) from exc


def _datetime(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name}: ISO 8601 문자열이어야 합니다.")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DomainValidationError(f"{field_name}: 유효한 ISO 8601 시각이 아닙니다.") from exc
    return aware_datetime(result, field_name=field_name)


def _integer(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(f"{field_name}: 정수여야 합니다.")
    return value


def _reject_json_float(value: str) -> NoReturn:
    raise DomainValidationError(
        "JSON 소수/지수 숫자는 허용되지 않습니다. 금액은 원 단위 정수 문자열을 사용하세요."
    )


def _reject_json_constant(value: str) -> NoReturn:
    raise DomainValidationError("JSON 비유한 숫자는 허용되지 않습니다.")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DomainValidationError(
                "JSON 객체에 중복 필드가 있습니다. 필드명과 원본 입력값은 오류에 표시하지 않습니다."
            )
        result[key] = value
    return result


def _parse_scenario_source(source: str) -> OfflineScenario:
    try:
        raw = json.loads(
            source,
            parse_float=_reject_json_float,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except DomainValidationError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise DomainValidationError(
            "입력 JSON을 처리할 수 없습니다. 경로와 원본 입력값은 오류에 표시하지 않습니다."
        ) from None
    root = _object(raw, field_name="root")
    _keys(
        root,
        field_name="root",
        required={
            "schema_version",
            "mode",
            "scenario_label",
            "realm",
            "as_of",
            "declared_initial_assets_krw",
            "allocation_year",
            "accounts",
            "positions",
            "assumptions",
        },
        optional={"cashflow", "goal", "satellite"},
    )
    version = _integer(root["schema_version"], field_name="schema_version")
    if version != 1:
        raise DomainValidationError("지원하는 schema_version은 1뿐입니다.")
    if root["mode"] != OFFLINE_MODE:
        raise DomainValidationError("첫 버전은 OFFLINE 모드만 허용합니다.")
    realm = _enum(DataRealm, root["realm"], field_name="realm")
    as_of = _datetime(root["as_of"], field_name="as_of")

    accounts: list[Account] = []
    for index, item in enumerate(_list(root["accounts"], field_name="accounts")):
        data = _object(item, field_name=f"accounts[{index}]")
        _keys(
            data,
            field_name=f"accounts[{index}]",
            required={"account_id", "alias", "account_type", "purpose", "currency"},
        )
        accounts.append(
            Account(
                account_id=data["account_id"],
                alias=data["alias"],
                account_type=data["account_type"],
                realm=realm,
                purpose=_enum(Purpose, data["purpose"], field_name=f"accounts[{index}].purpose"),
                currency=data["currency"],
            )
        )

    positions: list[PositionRecord] = []
    for index, item in enumerate(_list(root["positions"], field_name="positions")):
        data = _object(item, field_name=f"positions[{index}]")
        _keys(
            data,
            field_name=f"positions[{index}]",
            required={
                "position_id",
                "account_id",
                "name",
                "asset_class",
                "market_value_krw",
                "as_of",
                "value_status",
                "source",
            },
        )
        status = _enum(
            ValueStatus,
            data["value_status"],
            field_name=f"positions[{index}].value_status",
        )
        position_as_of = _datetime(
            data["as_of"], field_name=f"positions[{index}].as_of"
        )
        if position_as_of != as_of:
            raise DomainValidationError(
                f"positions[{index}].as_of: 평가 스냅샷 시각은 보고서 as_of와 정확히 같아야 합니다."
            )
        amount = (
            None
            if data["market_value_krw"] is None
            else krw_amount(
                data["market_value_krw"],
                field_name=f"positions[{index}].market_value_krw",
            )
        )
        positions.append(
            PositionRecord(
                position_id=data["position_id"],
                account_id=data["account_id"],
                name=data["name"],
                asset_class=_enum(
                    AssetClass,
                    data["asset_class"],
                    field_name=f"positions[{index}].asset_class",
                ),
                market_value_krw=amount,
                as_of=position_as_of,
                value_status=status,
                source=data["source"],
            )
        )

    assumptions_raw = _list(root["assumptions"], field_name="assumptions")
    assumptions = tuple(
        nonempty(value, field_name=f"assumptions[{index}]")
        for index, value in enumerate(assumptions_raw)
    )

    cashflow = None
    if "cashflow" in root and root["cashflow"] is not None:
        data = _object(root["cashflow"], field_name="cashflow")
        _keys(
            data,
            field_name="cashflow",
            required={"year", "monthly_net_income_krw", "essential_expenses_krw"},
        )
        cashflow = CashflowInput(
            year=_integer(data["year"], field_name="cashflow.year"),
            monthly_net_income_krw=krw_amount(
                data["monthly_net_income_krw"],
                field_name="cashflow.monthly_net_income_krw",
            ),
            essential_expenses_krw=krw_amount(
                data["essential_expenses_krw"],
                field_name="cashflow.essential_expenses_krw",
            ),
        )

    goal = None
    if "goal" in root and root["goal"] is not None:
        data = _object(root["goal"], field_name="goal")
        _keys(
            data,
            field_name="goal",
            required={"actual_year_end_assets_krw", "planned_next_year_investment_krw"},
        )
        goal = GoalInput(
            actual_year_end_assets_krw=krw_amount(
                data["actual_year_end_assets_krw"],
                field_name="goal.actual_year_end_assets_krw",
            ),
            planned_next_year_investment_krw=krw_amount(
                data["planned_next_year_investment_krw"],
                field_name="goal.planned_next_year_investment_krw",
            ),
        )

    satellite = None
    if "satellite" in root and root["satellite"] is not None:
        data = _object(root["satellite"], field_name="satellite")
        _keys(
            data,
            field_name="satellite",
            required={"expected_annual_new_investment_krw", "measured_loss_krw"},
        )
        satellite = SatelliteInput(
            expected_annual_new_investment_krw=krw_amount(
                data["expected_annual_new_investment_krw"],
                field_name="satellite.expected_annual_new_investment_krw",
            ),
            measured_loss_krw=(
                None
                if data["measured_loss_krw"] is None
                else krw_amount(
                    data["measured_loss_krw"], field_name="satellite.measured_loss_krw"
                )
            ),
        )

    ledger = PortfolioLedger(realm=realm, accounts=accounts, positions=positions)
    return OfflineScenario(
        schema_version=version,
        mode=OFFLINE_MODE,
        scenario_label=nonempty(root["scenario_label"], field_name="scenario_label"),
        realm=realm,
        as_of=as_of,
        declared_initial_assets=DeclaredAssetPlan(root["declared_initial_assets_krw"]),
        ledger=ledger,
        allocation_year=_integer(root["allocation_year"], field_name="allocation_year"),
        cashflow=cashflow,
        goal=goal,
        satellite=satellite,
        assumptions=assumptions,
    )


def load_scenario_bytes(source_bytes: bytes) -> OfflineScenario:
    """한 번 읽은 UTF-8 바이트를 엄격히 파싱한다."""

    try:
        source = source_bytes.decode("utf-8")
    except (UnicodeError, ValueError):
        raise DomainValidationError(
            "입력 파일을 읽을 수 없습니다. 경로와 원본 입력값은 오류에 표시하지 않습니다."
        ) from None
    return _parse_scenario_source(source)


def load_scenario(path: Path) -> OfflineScenario:
    """UTF-8 JSON을 읽습니다. 네트워크·환경변수·자격증명을 사용하지 않습니다."""

    try:
        source_bytes = path.read_bytes()
    except (OSError, ValueError):
        raise DomainValidationError(
            "입력 파일을 읽을 수 없습니다. 경로와 원본 입력값은 오류에 표시하지 않습니다."
        ) from None
    return load_scenario_bytes(source_bytes)
