"""Application services joining the web persistence layer to the proven engine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib import resources
from typing import Any, Mapping

from asset_assistant.domain import (
    DomainValidationError,
    Purpose,
    ValueStatus,
    mask_account_number_like_text,
)
from asset_assistant.gui_service import build_dashboard_data
from asset_assistant.providers import PROVIDER_DESCRIPTORS
from asset_assistant.reporting import build_markdown_report
from asset_assistant.scenario import OfflineScenario, load_scenario_bytes

from .repositories import (
    CredentialRepository,
    JobRepository,
    MarketRepository,
    ReportRepository,
    ScenarioRepository,
    StrategyTargetRepository,
)
from .strategy_targets import TargetStrategyKind, TargetStrategyMix, strategy_catalog


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _scenario_from_payload(payload: Mapping[str, Any]) -> OfflineScenario:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise DomainValidationError("저장된 자료를 안전하게 읽을 수 없습니다.") from None
    return load_scenario_bytes(encoded)


def _parse_validated_payload(source: bytes) -> tuple[OfflineScenario, dict[str, Any]]:
    scenario = load_scenario_bytes(source)
    try:
        payload = json.loads(source.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):  # already validated; defensive only
        raise DomainValidationError("입력 자료를 안전하게 처리할 수 없습니다.") from None
    if not isinstance(payload, dict):
        raise DomainValidationError("입력 자료를 안전하게 처리할 수 없습니다.")
    return scenario, payload


@dataclass(frozen=True)
class StoredScenarioResult:
    scenario_id: int
    report_id: int
    dashboard: dict[str, Any]


class WebApplicationService:
    """Owner-scoped web use cases; no account lookup or order path exists."""

    def __init__(
        self,
        *,
        scenarios: ScenarioRepository,
        reports: ReportRepository,
        strategy_targets: StrategyTargetRepository,
        credentials: CredentialRepository,
        jobs: JobRepository,
        market: MarketRepository | None = None,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.scenarios = scenarios
        self.reports = reports
        self.strategy_targets = strategy_targets
        self.credentials = credentials
        self.jobs = jobs
        self.market = market
        self.clock = clock

    def import_scenario(self, owner_id: str, source: bytes) -> StoredScenarioResult:
        scenario, payload = _parse_validated_payload(source)
        now = self.clock()
        snapshot = self.scenarios.save(
            owner_id,
            realm=scenario.realm.value,
            payload=payload,
            effective_at=scenario.as_of,
            created_at=now,
        )
        report_text = build_markdown_report(scenario)
        report = self.reports.save(
            owner_id,
            scenario_snapshot_id=snapshot.id,
            report_markdown=report_text,
            status="COMPLETE",
            data_cutoff_at=scenario.as_of,
            generated_at=now,
            data_scope=scenario.realm.value,
            generation_kind="MANUAL",
        )
        return StoredScenarioResult(
            scenario_id=snapshot.id,
            report_id=report.id,
            dashboard=self.dashboard(owner_id),
        )

    def import_sample(self, owner_id: str) -> StoredScenarioResult:
        try:
            payload = resources.files("wealthy_server").joinpath("offline_sample.json").read_bytes()
        except (OSError, TypeError):
            raise DomainValidationError("기본 샘플 자료를 읽을 수 없습니다.") from None
        return self.import_scenario(owner_id, payload)

    def generate_report(self, owner_id: str) -> dict[str, Any]:
        snapshot = self.scenarios.latest(owner_id)
        if snapshot is None:
            raise DomainValidationError("보고서 원장 자료가 미입력입니다.")
        scenario = _scenario_from_payload(snapshot.payload)
        now = self.clock()
        report = self.reports.save(
            owner_id,
            scenario_snapshot_id=snapshot.id,
            report_markdown=build_markdown_report(scenario),
            status="COMPLETE",
            data_cutoff_at=scenario.as_of,
            generated_at=now,
            data_scope=scenario.realm.value,
            generation_kind="MANUAL",
        )
        return self.report_view(report)

    def dashboard(self, owner_id: str) -> dict[str, Any]:
        snapshot = self.scenarios.latest(owner_id)
        latest_reports = self.reports.list(owner_id, limit=1)
        if snapshot is None:
            return {
                "data_scope": "MISSING",
                "as_of": None,
                "title": "자료 미입력",
                "investable_total_krw": None,
                "asset_total_status": "MISSING",
                "declared_initial_assets_krw": None,
                "warnings": ("보유자산이나 예산이 입력되지 않았습니다.",),
                "provider_statuses": self.provider_statuses(owner_id),
                "latest_report": self._report_summary(latest_reports[0]) if latest_reports else None,
            }
        scenario = _scenario_from_payload(snapshot.payload)
        summary = build_dashboard_data(scenario)
        investment = scenario.ledger.investment_value()
        return {
            "data_scope": summary.realm_code,
            "as_of": summary.as_of_text,
            "title": summary.title,
            "investable_total_krw": (
                _decimal_text(investment.amount_krw) if investment.complete else None
            ),
            "known_investable_partial_krw": _decimal_text(investment.amount_krw),
            "asset_total_status": "KNOWN" if investment.complete else "INCOMPLETE",
            "declared_initial_assets_krw": _decimal_text(
                scenario.declared_initial_assets.amount_krw
            ),
            "reconciliation": summary.reconciliation_text,
            "freshness": summary.freshness_text,
            "warnings": summary.warnings,
            "sample": summary.is_sample,
            "provider_statuses": self.provider_statuses(owner_id),
            "latest_report": self._report_summary(latest_reports[0]) if latest_reports else None,
        }

    def strategy_preview(
        self,
        weights: Mapping[str, str],
        *,
        total_assets_krw: str | None,
    ) -> dict[str, Any]:
        try:
            mix = TargetStrategyMix.from_strings(weights)
        except DomainValidationError as exc:
            # Do not echo values.  The UI may independently show a non-authoritative sum.
            return {
                "valid": False,
                "total": None,
                "delta": None,
                "direction": "invalid",
                "errors": (str(exc),),
                "target_amounts_krw": None,
                "total_assets_krw": total_assets_krw,
            }
        amounts = mix.target_amounts(total_assets_krw)
        return {
            "valid": True,
            "total": "100",
            "delta": "0",
            "direction": "none",
            "errors": (),
            "weights": mix.as_strings(),
            "total_assets_krw": total_assets_krw,
            "target_amounts_krw": (
                None
                if amounts is None
                else {kind.value: _decimal_text(value) for kind, value in amounts.items()}
            ),
        }

    def save_strategy(
        self, owner_id: str, *, version: str, weights: Mapping[str, str]
    ) -> dict[str, Any]:
        mix = TargetStrategyMix.from_strings(weights)
        item = self.strategy_targets.save(
            owner_id,
            version_name=version,
            mix=mix,
            created_at=self.clock(),
        )
        return self.strategy_view(item)

    def current_strategy(self, owner_id: str) -> dict[str, Any] | None:
        item = self.strategy_targets.current(owner_id)
        return None if item is None else self.strategy_view(item)

    def strategy_history(self, owner_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(self.strategy_view(item) for item in self.strategy_targets.list(owner_id))

    @staticmethod
    def strategy_view(item: Any) -> dict[str, Any]:
        return {
            "id": item.id,
            "version": mask_account_number_like_text(item.version_name),
            "created_at": item.created_at.isoformat(),
            "weights": dict(item.weights),
            "scope": "TOTAL_INVESTMENT_ASSET_TARGET",
            "notice": "후보 평가 점수 가중치와 별도이며 주문 금액이나 주문 승인이 아닙니다.",
        }

    @staticmethod
    def strategy_catalog_view() -> dict[str, Any]:
        descriptions = {
            TargetStrategyKind.LONG_TERM_INDEX: ("광범위 지수를 장기 보유하는 목표", "지수 범위·상품 정책", "분산", "시장 전체 하락"),
            TargetStrategyKind.VALUE: ("업종 안의 상대 가치 지표를 검토", "업종 내 가치평가 자료", "가격 규율", "가치 함정·자료 결측"),
            TargetStrategyKind.QUALITY_GROWTH: ("재무 품질과 성장 근거를 함께 검토", "품질·성장 재무자료", "질적 선별", "성장 엔진 미완성"),
            TargetStrategyKind.GROWTH: ("검증된 성장 자료가 있을 때만 평가", "성장률·평가가치·공시", "현재 평가 불가", "독립 엔진 미구현"),
            TargetStrategyKind.HIGH_DIVIDEND: ("검증된 배당 자료가 있을 때만 평가", "배당 이력·지급가능성·세제", "현재 평가 불가", "수익률 함정·세제 미확인"),
            TargetStrategyKind.DIVIDEND_GROWTH: ("배당 성장 이력을 후보 근거로 사용", "배당 성장 이력", "현금흐름 성장 검토", "배당 삭감"),
            TargetStrategyKind.MOMENTUM: ("재현 가능한 가격 추세를 후보 근거로 사용", "최소 126개 가격 시점", "추세 확인", "급반전·자료 부족"),
            TargetStrategyKind.ASSET_ALLOCATION: ("자산군을 함께 보는 목표", "주식·채권·현금 실제 노출", "자산군 분산", "현재 채권·현금 요소만 부분 지원"),
        }
        items = []
        for entry in strategy_catalog():
            description, criteria, benefits, risks = descriptions[entry.kind]
            items.append(
                {
                    "id": entry.kind.value,
                    "name": entry.korean_name,
                    "description": description,
                    "criteria": criteria,
                    "benefits": benefits,
                    "risks": risks,
                    "required_data": criteria,
                    "status": entry.mapping_status.value,
                    "mapping": entry.status_note,
                }
            )
        return {"items": items}

    def reports_view(self, owner_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(self._report_summary(item) for item in self.reports.list(owner_id))

    def report(self, owner_id: str, report_id: int) -> dict[str, Any] | None:
        item = self.reports.get(owner_id, report_id)
        return None if item is None else self.report_view(item)

    @staticmethod
    def _report_summary(item: Any) -> dict[str, Any]:
        return {
            "id": item.id,
            "created_at": item.generated_at.isoformat(),
            "status": item.status,
            "data_cutoff_at": (
                item.data_cutoff_at.isoformat() if item.data_cutoff_at is not None else None
            ),
            "data_scope": getattr(item, "data_scope", None),
            "generation_kind": getattr(item, "generation_kind", None),
        }

    @classmethod
    def report_view(cls, item: Any) -> dict[str, Any]:
        return {**cls._report_summary(item), "markdown": item.report_markdown}

    def provider_statuses(self, owner_id: str) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for descriptor in PROVIDER_DESCRIPTORS:
            state = descriptor.state.value
            configured: bool | None = None
            if descriptor.code in {"bls", "fed_news"}:
                configured = True
            elif descriptor.code in {"kis", "opendart", "ecos", "sec"}:
                configured = self._provider_configured(owner_id, descriptor.code)
                if configured:
                    state = "READY_FOR_CONNECTION_TEST"
            latest_run = self.market.latest_run(descriptor.code) if self.market is not None else None
            live_test = "NOT_RUN"
            as_of = None
            if latest_run is not None:
                as_of = (
                    latest_run.finished_at or latest_run.started_at
                ).isoformat()
                if latest_run.connection_mode == "LIVE":
                    live_test = latest_run.status
                    state = f"LIVE_{latest_run.status}"
                elif latest_run.connection_mode == "LOCAL_STORE_UNSPECIFIED":
                    live_test = "HISTORICAL_MODE_UNSPECIFIED"
                    state = f"LOCAL_{latest_run.status}"
                else:
                    live_test = "FIXTURE_ONLY"
            rows.append(
                {
                    "provider": descriptor.code,
                    "name": descriptor.name,
                    "status": state,
                    "configured": configured,
                    "as_of": as_of,
                    "official_url": descriptor.official_url,
                    "checked_on": descriptor.checked_on,
                    "live_test": live_test,
                }
            )
        return tuple(rows)

    def _provider_configured(self, owner_id: str, provider: str) -> bool:
        required: tuple[tuple[str, str], ...]
        if provider == "kis":
            # Either environment can be prepared; neither grants account/order authority.
            for environment in ("paper", "live"):
                if all(
                    self.credentials.status(
                        owner_id,
                        provider="kis",
                        environment=environment,
                        secret_name=field,
                    ).configured
                    for field in ("app_key", "app_secret")
                ):
                    return True
            return False
        required = {
            "opendart": (("default", "api_key"),),
            "ecos": (("default", "api_key"),),
            "sec": (("default", "user_agent"),),
        }.get(provider, ())
        return bool(required) and all(
            self.credentials.status(
                owner_id,
                provider=provider,
                environment=environment,
                secret_name=field,
            ).configured
            for environment, field in required
        )

    def jobs_view(self, owner_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "id": item.id,
                "kind": item.job_kind,
                "scheduled_for": item.scheduled_for.isoformat(),
                "state": item.state,
                "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                "outcome_code": item.outcome_code,
                "attempt_count": item.attempt_count,
            }
            for item in self.jobs.list(owner_id)
        )

    def notifications_view(self, owner_id: str) -> tuple[dict[str, Any], ...]:
        """Derive non-sensitive notices; no second notification ledger is created."""

        notices: list[dict[str, Any]] = []
        for item in self.jobs.list(owner_id, limit=20):
            when = item.finished_at or item.scheduled_for
            state = str(item.state)
            notices.append(
                {
                    "id": f"job-{item.id}",
                    "kind": "AUTOMATION",
                    "title": "예약 작업 상태",
                    "message": (
                        "예약 작업을 완료했습니다."
                        if state in {"OK", "SUCCEEDED"}
                        else "예약 작업의 일부 또는 전체를 확인해 주세요."
                    ),
                    "created_at": when.isoformat(),
                    "status": state,
                    "_sort": when,
                }
            )
        for item in self.reports.list(owner_id, limit=20):
            when = item.generated_at
            notices.append(
                {
                    "id": f"report-{item.id}",
                    "kind": "REPORT",
                    "title": "보고서 생성 상태",
                    "message": (
                        "7개 절 보고서가 생성되었습니다."
                        if str(item.status) in {"OK", "COMPLETE"}
                        else "부분 자료 또는 미완료 항목이 포함된 보고서입니다."
                    ),
                    "created_at": when.isoformat(),
                    "status": str(item.status),
                    "_sort": when,
                }
            )
        notices.sort(key=lambda value: value["_sort"], reverse=True)
        return tuple(
            {key: value for key, value in item.items() if key != "_sort"}
            for item in notices[:40]
        )

    def portfolio_view(self, owner_id: str) -> dict[str, Any]:
        """Expose only values represented by the validated scenario ledger."""

        snapshot = self.scenarios.latest(owner_id)
        if snapshot is None:
            return {
                "data_scope": "MISSING",
                "as_of": None,
                "stale": True,
                "investable_total_krw": None,
                "accounts": (),
                "positions": (),
                "asset_class_totals": (),
                "monthly_history": self._monthly_history(),
                "actual_strategy": {
                    "status": "UNAVAILABLE",
                    "as_of": None,
                    "weights": None,
                    "missing_reason": "검증된 보유자산 자료가 없습니다.",
                    "double_count_checked": False,
                },
            }
        scenario = _scenario_from_payload(snapshot.payload)
        investment = scenario.ledger.investment_value()
        now = self.clock()
        stale = scenario.as_of.date() < now.astimezone(scenario.as_of.tzinfo).date()
        account_positions: dict[str, list[Any]] = {
            account.account_id: [] for account in scenario.ledger.accounts
        }
        accounts_by_id = {account.account_id: account for account in scenario.ledger.accounts}
        for position in scenario.ledger.positions:
            account_positions[position.account_id].append(position)

        account_rows: list[dict[str, Any]] = []
        for account in scenario.ledger.accounts:
            rows = account_positions[account.account_id]
            known_total = sum(
                (
                    position.market_value_krw or Decimal(0)
                    for position in rows
                    if position.value_status is ValueStatus.KNOWN
                ),
                Decimal(0),
            )
            complete = bool(rows) and all(
                position.value_status is ValueStatus.KNOWN for position in rows
            )
            account_rows.append(
                {
                    "id": mask_account_number_like_text(account.account_id),
                    "alias": mask_account_number_like_text(account.alias),
                    "account_type": mask_account_number_like_text(account.account_type),
                    "tax_treatment": None,
                    "market_value_krw": _decimal_text(known_total) if complete else None,
                    "data_scope": scenario.realm.value,
                    "as_of": scenario.as_of.isoformat(),
                    "status": "KNOWN" if complete else "INCOMPLETE",
                }
            )

        position_rows: list[dict[str, Any]] = []
        totals: dict[str, Decimal] = {}
        class_complete: dict[str, bool] = {}
        for position in scenario.ledger.positions:
            account = accounts_by_id[position.account_id]
            position_rows.append(
                {
                    "id": mask_account_number_like_text(position.position_id),
                    "instrument_id": None,
                    "name": mask_account_number_like_text(position.name),
                    "instrument_type": None,
                    "market": None,
                    "currency": account.currency,
                    "quantity": None,
                    "market_value_krw": (
                        _decimal_text(position.market_value_krw)
                        if position.value_status is ValueStatus.KNOWN
                        and position.market_value_krw is not None
                        else None
                    ),
                    "asset_class": position.asset_class.value,
                    "as_of": position.as_of.isoformat(),
                    "status": position.value_status.value,
                }
            )
            if account.purpose is Purpose.INVESTMENT:
                key = position.asset_class.value
                totals.setdefault(key, Decimal(0))
                class_complete.setdefault(key, True)
                if position.value_status is ValueStatus.KNOWN:
                    totals[key] += position.market_value_krw or Decimal(0)
                else:
                    class_complete[key] = False

        class_rows: list[dict[str, Any]] = []
        for name in sorted(totals):
            amount = totals[name]
            complete = class_complete[name]
            weight = None
            if complete and investment.complete and investment.amount_krw > 0:
                weight = _decimal_text(amount * Decimal(100) / investment.amount_krw)
            class_rows.append(
                {
                    "asset_class": name,
                    "amount_krw": _decimal_text(amount) if complete else None,
                    "weight_percent": weight,
                    "status": "KNOWN" if complete else "INCOMPLETE",
                }
            )

        return {
            "data_scope": scenario.realm.value,
            "as_of": scenario.as_of.isoformat(),
            "stale": stale,
            "investable_total_krw": (
                _decimal_text(investment.amount_krw) if investment.complete else None
            ),
            "accounts": tuple(account_rows),
            "positions": tuple(position_rows),
            "asset_class_totals": tuple(class_rows),
            "monthly_history": self._monthly_history(),
            "actual_strategy": {
                "status": "UNAVAILABLE",
                "as_of": scenario.as_of.isoformat(),
                "weights": None,
                "missing_reason": "전략별 실제 귀속 자료가 없어 중복 없는 실제 비중을 계산할 수 없습니다.",
                "double_count_checked": False,
            },
        }

    def _monthly_history(self) -> tuple[dict[str, object], ...]:
        method = getattr(self.market, "monthly_history", None)
        if method is None:
            return ()
        return tuple(method(limit=120))
