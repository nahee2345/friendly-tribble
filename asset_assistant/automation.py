"""읽기 전용 공식자료 수집·분석·정기 보고 자동화 진입점."""

from __future__ import annotations

import json
import html
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from .cli import _SafeArgumentParser
from .credential_store import CredentialStore
from .domain import DomainValidationError, mask_account_number_like_text
from .file_io import paths_refer_to_same_file
from .market_data import (
    CollectionIssue,
    CollectionResult,
    CollectionStatus,
    MarketDataStore,
    RecordKind,
    default_data_directory,
)
from .providers import (
    PROVIDER_DESCRIPTORS,
    BlsProvider,
    FederalReserveNewsProvider,
    ProviderDescriptor,
    ProviderState,
    SecEdgarProvider,
)
from .kr_providers import (
    ECOS_DAILY_ATTEMPT_CEILING,
    ECOS_MAX_LOGICAL_CALLS_PER_RUN,
    EcosProvider,
    EcosSeriesRequest,
    KisQuoteProvider,
    OpenDartProvider,
)
from .scenario import load_scenario_bytes
from .strategy import (
    AssessmentStatus,
    CandidateAssessment,
    CandidateInput,
    StrategyKind,
    StrategyMix,
    evaluate_candidate,
)


SEOUL = ZoneInfo("Asia/Seoul")
BLS_DAILY_ZONE = ZoneInfo("America/New_York")
DEFAULT_BLS_SERIES = ("CUUR0000SA0", "LNS14000000")
IMPLEMENTED_PROVIDERS = frozenset({"bls", "fed_news", "sec", "kis", "opendart", "ecos"})
_PROVIDER_TARGET_PATTERNS = {
    "bls": re.compile(r"[A-Z0-9_#-]{1,50}"),
    "sec": re.compile(r"\d{10}"),
    "opendart": re.compile(r"\d{8}"),
    "kis": re.compile(r"\d{6}"),
}
REPORT_SECTION_TITLES = (
    "1) 요약 3줄",
    "2) 자산 현황과 목표·실제 비중",
    "3) 목표 대비",
    "4) 실제 배당과 배당 전환 능력",
    "5) 위성 손실 현황",
    "6) 제안 주문안과 이유",
    "7) 규칙 위반·주의사항",
)


@dataclass(frozen=True)
class ProviderStatusRow:
    code: str
    name: str
    state: str
    last_effective: str
    last_collected: str
    last_attempted: str
    message: str


@dataclass(frozen=True)
class ScheduledJobStatusRow:
    """예약 실행 이력을 원문 DB 값 없이 표시하기 위한 안전한 요약."""

    task: str
    scheduled_for: str
    started_at: str
    finished_at: str
    status: str
    detail_code: str


@dataclass(frozen=True)
class AutomationSummary:
    status: str
    message: str
    provider_results: tuple[CollectionResult, ...] = ()
    report: str | None = None
    report_date: str | None = None


@dataclass(frozen=True)
class StrategyWeightView:
    strategy: StrategyKind
    weight: Decimal


@dataclass(frozen=True)
class StrategyMixView:
    """Safe display view of one immutable strategy version."""

    version_display: str
    weights: tuple[StrategyWeightView, ...]
    saved_at: str


@dataclass(frozen=True)
class SourceTimingView:
    record_id: int
    effective_at: str
    collected_at: str


@dataclass(frozen=True)
class CandidateAssessmentView:
    """Append-only assessment history with its stored, combined rationale.

    Older rows preserve reasons as one combined list.  The read model therefore
    does not guess which entries were positive or adverse.
    """

    strategy_version_display: str
    instrument_display: str
    status: str
    score: Decimal | None
    combined_evidence_constraints: tuple[str, ...]
    source_record_ids: tuple[int, ...]
    cutoff_at: str
    created_at: str
    source_timings: tuple[SourceTimingView, ...]


@dataclass(frozen=True)
class StrategyInputStateView:
    strategy: StrategyKind
    input_label: str
    provider_label: str
    state: str
    detail: str


@dataclass(frozen=True)
class StrategyWorkspaceSnapshot:
    current_mix: StrategyMixView | None
    assessments: tuple[CandidateAssessmentView, ...]
    input_states: tuple[StrategyInputStateView, ...]


_JOB_LABELS = {"collect": "수집", "report": "보고서"}
_JOB_STATUSES = frozenset(
    {"RUNNING", "OK", "PARTIAL", "ERROR", "ABANDONED_STALE"}
)
_JOB_DETAIL_CODES = frozenset(
    {
        "RUNNING",
        "RECOVERED_RETRY",
        "STALE_RETRY",
        "SAFE_FAILURE",
        "OK",
        "PARTIAL",
        "ERROR",
    }
)


def _safe_job_timestamp(value: object, *, missing: str) -> tuple[str, datetime | None]:
    """저장된 시각을 검증해 고정 형식으로 재출력하고 원문은 버린다."""

    if value is None:
        return missing, None
    raw = str(value)
    if len(raw) > 64:
        return "확인 불가", None
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        local = parsed.astimezone(SEOUL)
    except (TypeError, ValueError, OverflowError):
        return "확인 불가", None
    return local.strftime("%Y-%m-%d %H:%M:%S %Z"), parsed


def _now_seoul() -> datetime:
    return datetime.now(SEOUL)


def _safe_label(value: object) -> str:
    masked = mask_account_number_like_text(
        str(value).replace("\r", " ").replace("\n", " ")
    )[:200]
    return _escape_markdown_label(masked)


def _safe_external_label(value: object) -> str:
    """Escape official-provider text without treating public dates as account data.

    Provider responses are still untrusted display data: newlines and length are
    bounded and HTML/Markdown syntax is neutralized.  Account-number-like
    masking remains reserved for user-entered free text via ``_safe_label`` and
    the existing manual-report sanitizers.
    """

    bounded = str(value).replace("\r", " ").replace("\n", " ")[:200]
    return _escape_markdown_label(bounded)


def _safe_plain_user_label(value: object) -> str:
    """Bound and mask user-derived text for a plain Tk display widget."""

    masked = mask_account_number_like_text(
        str(value).replace("\r", " ").replace("\n", " ")
    )
    return " ".join(masked.split())[:200]


def _workspace_timestamp(value: object) -> str:
    """Canonicalize a stored aware timestamp without falling back to raw text."""

    raw = str(value)
    if len(raw) > 64:
        raise DomainValidationError("전략 화면의 저장 시각을 확인할 수 없습니다.")
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        local = parsed.astimezone(SEOUL)
    except (TypeError, ValueError, OverflowError):
        raise DomainValidationError("전략 화면의 저장 시각을 확인할 수 없습니다.") from None
    timespec = "microseconds" if local.microsecond else "seconds"
    return local.isoformat(timespec=timespec)


def _escape_markdown_label(value: str) -> str:
    safe = html.escape(" ".join(value.split()), quote=True)
    for marker in ("\\", "`", "[", "]", "(", ")", "!", "|", "*", "_"):
        safe = safe.replace(marker, "\\" + marker)
    return safe


def _validate_provider_targets(
    provider: str,
    values: object,
) -> tuple[tuple[str, ...], bool]:
    """Return safe stored targets and whether any setting value was rejected.

    Stored settings are an input boundary too: a local DB can outlive newer
    validation rules or be modified without going through ``set_targets``.
    Rejected values are deliberately not included in exceptions or warnings.
    """

    if not isinstance(values, (list, tuple)):
        return (), True
    if len(values) > 50:
        return (), True
    if provider == "fed_news":
        return (), bool(values)
    pattern = _PROVIDER_TARGET_PATTERNS.get(provider)
    if pattern is None:
        return (), bool(values)

    targets: list[str] = []
    rejected = False
    for value in values:
        if not isinstance(value, str):
            rejected = True
            continue
        target = value.strip()
        if not target:
            continue
        if len(target) > 80 or pattern.fullmatch(target) is None:
            rejected = True
            continue
        targets.append(target)
    return tuple(targets), rejected


def _configuration_result(provider: str, message: str) -> CollectionResult:
    now = datetime.now(timezone.utc)
    return CollectionResult(
        provider,
        now,
        now,
        CollectionStatus.ERROR,
        issues=(CollectionIssue("CONFIGURATION_REQUIRED", message),),
    )


def _combine_provider_results(
    provider: str, results: tuple[CollectionResult, ...]
) -> CollectionResult:
    records = tuple(record for result in results for record in result.records)
    issues = tuple(issue for result in results for issue in result.issues)
    status = (
        CollectionStatus.PARTIAL
        if records and issues
        else CollectionStatus.OK
        if records
        else CollectionStatus.ERROR
    )
    return CollectionResult(
        provider,
        min(result.started_at for result in results),
        max(result.finished_at for result in results),
        status,
        records,
        issues,
    )


class AutomationService:
    """GUI·예약기가 공유하는 읽기 전용 자동화 서비스."""

    def __init__(
        self,
        store: MarketDataStore | None = None,
        credential_store: CredentialStore | None = None,
        *,
        bls_provider: BlsProvider | None = None,
        fed_provider: FederalReserveNewsProvider | None = None,
        sec_provider: SecEdgarProvider | None = None,
        clock=_now_seoul,
    ) -> None:
        self.store = store or MarketDataStore()
        self.credentials = credential_store or CredentialStore()
        self.bls_provider = bls_provider or BlsProvider(credentials=self.credentials)
        self.fed_provider = fed_provider or FederalReserveNewsProvider()
        self.sec_provider = sec_provider or SecEdgarProvider(self.credentials)
        self.clock = clock

    def enabled_providers(self) -> tuple[str, ...]:
        raw = self.store.get_setting("providers.enabled", ["bls", "fed_news"])
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise DomainValidationError("공급자 활성화 설정을 확인할 수 없습니다.")
        return tuple(item for item in raw if item in IMPLEMENTED_PROVIDERS)

    def set_provider_enabled(self, provider: str, enabled: bool) -> None:
        if provider not in IMPLEMENTED_PROVIDERS:
            raise DomainValidationError("아직 연결 구현이 완료되지 않은 공급자입니다.")
        current = set(self.enabled_providers())
        if enabled:
            current.add(provider)
        else:
            current.discard(provider)
        self.store.set_setting(
            "providers.enabled", sorted(current), updated_at=self.clock()
        )

    def set_targets(self, provider: str, targets: tuple[str, ...]) -> None:
        if provider not in IMPLEMENTED_PROVIDERS:
            raise DomainValidationError("아직 연결 구현이 완료되지 않은 공급자입니다.")
        if provider == "ecos":
            raise DomainValidationError("ECOS 대상은 주기와 기간을 포함한 구조화 설정이 필요합니다.")
        cleaned, rejected = _validate_provider_targets(provider, targets)
        if rejected:
            raise DomainValidationError("공급자 대상 식별자 형식을 확인할 수 없습니다.")
        self.store.set_setting(
            f"provider.targets.{provider}", list(cleaned), updated_at=self.clock()
        )

    def set_kis_environment(self, environment: str) -> None:
        if environment not in {"paper", "live"}:
            raise DomainValidationError("KIS 환경은 모의 또는 실전 시세 조회로 구분해야 합니다.")
        self.store.set_setting(
            "provider.kis.environment",
            environment,
            updated_at=self.clock(),
        )

    def set_sec_concepts(self, concepts: tuple[str, ...]) -> None:
        cleaned = tuple(value.strip() for value in concepts if value.strip())
        if (
            not cleaned
            or len(cleaned) > 50
            or len(set(cleaned)) != len(cleaned)
            or any(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,99}", value) is None for value in cleaned)
        ):
            raise DomainValidationError("SEC 재무 concept 설정을 확인할 수 없습니다.")
        self.store.set_setting(
            "provider.sec.concepts",
            list(cleaned),
            updated_at=self.clock(),
        )

    def set_ecos_series(self, request: EcosSeriesRequest) -> None:
        encoded = {
            "statistic_code": request.statistic_code,
            "cycle": request.cycle,
            "start_period": request.start_period,
            "end_period": request.end_period,
            "item_code1": request.item_code1,
            "item_code2": request.item_code2,
            "item_code3": request.item_code3,
            "item_code4": request.item_code4,
        }
        existing = self.store.get_setting("provider.targets.ecos", [])
        requests = [value for value in existing if isinstance(value, dict)] if isinstance(existing, list) else []
        if encoded not in requests:
            requests.append(encoded)
        if len(requests) > 100:
            raise DomainValidationError("ECOS 시계열 설정 수가 허용 범위를 벗어났습니다.")
        self.store.set_setting("provider.targets.ecos", requests, updated_at=self.clock())
        self.set_provider_enabled("ecos", True)

    def set_opendart_financial_request(
        self,
        *,
        corp_code: str,
        business_year: str,
        report_code: str,
        financial_division: str,
    ) -> None:
        if (
            re.fullmatch(r"\d{8}", corp_code) is None
            or re.fullmatch(r"20\d{2}", business_year) is None
            or report_code not in {"11011", "11012", "11013", "11014"}
            or financial_division not in {"CFS", "OFS"}
        ):
            raise DomainValidationError("OpenDART 재무 상세 설정을 확인할 수 없습니다.")
        encoded = {
            "corp_code": corp_code,
            "business_year": business_year,
            "report_code": report_code,
            "financial_division": financial_division,
        }
        existing = self.store.get_setting("provider.opendart.financial_requests", [])
        requests = [value for value in existing if isinstance(value, dict)] if isinstance(existing, list) else []
        if encoded not in requests:
            requests.append(encoded)
        if len(requests) > 100:
            raise DomainValidationError("OpenDART 재무 요청 수가 허용 범위를 벗어났습니다.")
        self.store.set_setting(
            "provider.opendart.financial_requests", requests, updated_at=self.clock()
        )

    def provider_statuses(self, *, now: datetime | None = None) -> tuple[ProviderStatusRow, ...]:
        current = now or self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise DomainValidationError("공급자 상태 기준 시각에는 시간대가 필요합니다.")
        summaries = {
            row["provider"]: row
            for row in self.store.provider_summaries(cutoff_at=current)
        }
        enabled = set(self.enabled_providers())
        rows: list[ProviderStatusRow] = []
        for descriptor in PROVIDER_DESCRIPTORS:
            latest = summaries.get(descriptor.code)
            state = descriptor.state.value
            message = descriptor.notes
            last_effective = "미수집"
            last_collected = "미수집"
            last_attempted = "미시도"
            if latest is not None:
                state = latest["status"]
                last_attempted = latest["finished_at"]
                records = self.store.latest_records(
                    provider=descriptor.code,
                    cutoff_at=current,
                    require_known_publication_time=True,
                )
                if records:
                    last_effective = records[0]["effective_at"]
                    last_collected = records[0]["collected_at"]
                if latest["status"] == CollectionStatus.ERROR.value:
                    message = "최근 갱신 실패 · 이전 정상 자료는 보존됨"
                elif latest["status"] == CollectionStatus.PARTIAL.value:
                    message = "일부 갱신 성공 · 미완료 항목 확인 필요"
                else:
                    message = "최근 갱신 정상"
                try:
                    attempted_at = datetime.fromisoformat(last_attempted)
                    if (
                        state in {CollectionStatus.OK.value, CollectionStatus.PARTIAL.value}
                        and attempted_at.astimezone(SEOUL).date()
                        < current.astimezone(SEOUL).date()
                    ):
                        state = "STALE"
                        message = "오늘 갱신되지 않음 · 이전 정상 자료의 기준시각 확인 필요"
                except (TypeError, ValueError, OverflowError):
                    state = "ERROR"
                    message = "최근 갱신 시도 시각을 확인할 수 없음"
            if descriptor.code in IMPLEMENTED_PROVIDERS and descriptor.code not in enabled:
                state = "DISABLED"
                message = "사용자 설정에서 비활성화됨"
            rows.append(
                ProviderStatusRow(
                    descriptor.code,
                    descriptor.name,
                    state,
                    last_effective,
                    last_collected,
                    last_attempted,
                    message,
                )
            )
        return tuple(rows)

    def recent_scheduled_jobs(
        self, *, limit: int = 6
    ) -> tuple[ScheduledJobStatusRow, ...]:
        """현재 슬롯과 완료 시도 이력을 민감 원문 없이 최근순으로 반환한다."""

        if not (1 <= limit <= 20):
            raise DomainValidationError("예약 실행 이력 조회 개수가 허용 범위를 벗어났습니다.")

        # job_runs에는 현재 슬롯 상태가, job_attempt_history에는 재시도 전 결과까지
        # append-only로 남는다. 같은 완료 행은 두 테이블에 있으므로 표시 시 중복 제거한다.
        source_rows = (
            *self.store.job_rows(limit=limit),
            *self.store.job_attempt_rows(limit=min(limit * 4, 100)),
        )
        seen: set[tuple[object, ...]] = set()
        prepared: list[tuple[datetime | None, ScheduledJobStatusRow]] = []
        for row in source_rows:
            identity = tuple(
                row[name]
                for name in (
                    "task_name",
                    "scheduled_for",
                    "started_at",
                    "finished_at",
                    "status",
                    "detail_code",
                )
            )
            if identity in seen:
                continue
            seen.add(identity)

            task = _JOB_LABELS.get(str(row["task_name"]), "알 수 없는 작업")
            status_candidate = str(row["status"]).upper()
            status = status_candidate if status_candidate in _JOB_STATUSES else "UNKNOWN"
            raw_detail = row["detail_code"]
            detail_candidate = str(raw_detail).upper() if raw_detail is not None else ""
            if not detail_candidate and status == "RUNNING":
                detail_candidate = "RUNNING"
            detail = (
                detail_candidate
                if detail_candidate in _JOB_DETAIL_CODES
                else "UNKNOWN"
            )
            scheduled_text, _ = _safe_job_timestamp(
                row["scheduled_for"], missing="확인 불가"
            )
            started_text, started = _safe_job_timestamp(
                row["started_at"], missing="확인 불가"
            )
            finished_text, _ = _safe_job_timestamp(
                row["finished_at"],
                missing="진행 중" if status == "RUNNING" else "미기록",
            )
            prepared.append(
                (
                    started,
                    ScheduledJobStatusRow(
                        task=task,
                        scheduled_for=scheduled_text,
                        started_at=started_text,
                        finished_at=finished_text,
                        status=status,
                        detail_code=detail,
                    ),
                )
            )

        prepared.sort(
            key=lambda item: (
                item[0].astimezone(timezone.utc)
                if item[0] is not None
                else datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=True,
        )
        return tuple(row for _, row in prepared[:limit])

    def collect(self) -> AutomationSummary:
        """한 DB에서 GUI·예약·CLI 수집이 겹치지 않도록 lease를 잡는다."""

        owner_token = uuid.uuid4().hex
        acquired_at = self.clock()
        if not self.store.acquire_operation_lease(
            "market_collection",
            owner_token,
            acquired_at=acquired_at,
        ):
            return AutomationSummary(
                "ERROR",
                "다른 시장자료 수집이 진행 중이어서 중복 호출을 시작하지 않았습니다.",
            )
        try:
            return self._collect_unlocked()
        finally:
            self.store.release_operation_lease("market_collection", owner_token)

    def _collect_unlocked(self) -> AutomationSummary:
        results: list[CollectionResult] = []
        enabled = self.enabled_providers()
        for provider in enabled:
            if provider == "bls":
                configured = self.store.get_setting(
                    "provider.targets.bls", list(DEFAULT_BLS_SERIES)
                )
                targets, _ = _validate_provider_targets("bls", configured)
                limit_reader = getattr(self.bls_provider, "daily_query_limit", None)
                daily_limit = int(limit_reader()) if callable(limit_reader) else 25
                client = getattr(self.bls_provider, "client", None)
                reserved_attempts = int(getattr(client, "max_retries", 0)) + 1
                usage_date = self.clock().astimezone(BLS_DAILY_ZONE).date().isoformat()
                if not targets:
                    result = _configuration_result(
                        "bls", "BLS 대상 설정이 필요하거나 저장된 형식을 확인해야 합니다."
                    )
                elif not self.store.reserve_provider_calls(
                    "bls",
                    usage_date,
                    requested_calls=reserved_attempts,
                    daily_limit=daily_limit,
                    updated_at=self.clock(),
                ):
                    result = _configuration_result(
                        "bls",
                        "BLS 일일 호출 예산을 보수적으로 모두 예약하여 오늘 추가 호출을 중지했습니다.",
                    )
                else:
                    result = self.bls_provider.collect(targets)
            elif provider == "fed_news":
                result = self.fed_provider.collect()
            elif provider == "sec":
                configured = self.store.get_setting("provider.targets.sec", [])
                targets, _ = _validate_provider_targets("sec", configured)
                if not targets:
                    now = datetime.now(timezone.utc)
                    # Empty targets are configuration, not a fabricated successful call.
                    result = CollectionResult(
                        "sec",
                        now,
                        now,
                        CollectionStatus.ERROR,
                        issues=(CollectionIssue("CONFIGURATION_REQUIRED", "SEC 대상 설정이 필요합니다."),),
                    )
                else:
                    sec_results = [self.sec_provider.collect_submissions(targets)]
                    concepts = self.store.get_setting("provider.sec.concepts", [])
                    if isinstance(concepts, list) and concepts:
                        sec_results.append(
                            self.sec_provider.collect_companyfacts(
                                targets, tuple(str(value) for value in concepts)
                            )
                        )
                    result = _combine_provider_results("sec", tuple(sec_results))
            elif provider == "kis":
                configured = self.store.get_setting("provider.targets.kis", [])
                targets, _ = _validate_provider_targets("kis", configured)
                environment = self.store.get_setting("provider.kis.environment", "paper")
                if not targets:
                    result = _configuration_result("kis", "KIS 조회 종목 설정이 필요합니다.")
                else:
                    result = KisQuoteProvider(
                        self.credentials,
                        environment=str(environment),
                    ).collect_quotes(targets)
            elif provider == "opendart":
                configured = self.store.get_setting("provider.targets.opendart", [])
                targets, _ = _validate_provider_targets("opendart", configured)
                if not targets:
                    result = _configuration_result(
                        "opendart", "OpenDART 대상 회사 설정이 필요합니다."
                    )
                else:
                    local_today = self.clock().astimezone(SEOUL).date()
                    start_date = (local_today - timedelta(days=7)).strftime("%Y%m%d")
                    end_date = local_today.strftime("%Y%m%d")
                    dart = OpenDartProvider(self.credentials)
                    financial_requests = self.store.get_setting(
                        "provider.opendart.financial_requests", []
                    )
                    configured_financials = (
                        [request for request in financial_requests[:100] if isinstance(request, dict)]
                        if isinstance(financial_requests, list)
                        else []
                    )
                    # 공시 목록은 회사별 최대 100페이지, 재무는 설정별 1회이며
                    # 각 호출은 최대 2회 재시도한다. 실제 사용량보다 큰 최악값을
                    # 먼저 예약해 OpenDART 개인 일일 한도를 넘지 않는다.
                    maximum_attempts = (
                        len(targets) * 100 + len(configured_financials)
                    ) * (dart.client.max_retries + 1)
                    if not self.store.reserve_provider_calls(
                        "opendart",
                        local_today.isoformat(),
                        requested_calls=maximum_attempts,
                        daily_limit=20_000,
                        updated_at=self.clock(),
                    ):
                        result = _configuration_result(
                            "opendart",
                            "OpenDART 일일 호출 예산을 보수적으로 모두 예약하여 오늘 추가 호출을 중지했습니다.",
                        )
                        self.store.save_collection(result)
                        results.append(result)
                        continue
                    dart_results = [
                        dart.collect_disclosures(
                            targets, begin_date=start_date, end_date=end_date
                        )
                    ]
                    for request in configured_financials:
                        dart_results.append(
                            dart.collect_full_financials(
                                (str(request.get("corp_code", "")),),
                                business_year=str(request.get("business_year", "")),
                                report_code=str(request.get("report_code", "")),
                                financial_division=str(request.get("financial_division", "")),
                            )
                        )
                    result = _combine_provider_results("opendart", tuple(dart_results))
            elif provider == "ecos":
                configured = self.store.get_setting("provider.targets.ecos", [])
                requests: list[EcosSeriesRequest] = []
                if isinstance(configured, list):
                    for item in configured[:100]:
                        if isinstance(item, dict):
                            requests.append(
                                EcosSeriesRequest(
                                    statistic_code=str(item.get("statistic_code", "")),
                                    cycle=str(item.get("cycle", "")),
                                    start_period=str(item.get("start_period", "")),
                                    end_period=str(item.get("end_period", "")),
                                    item_code1=str(item.get("item_code1", "")),
                                    item_code2=str(item.get("item_code2", "?")),
                                    item_code3=str(item.get("item_code3", "?")),
                                    item_code4=str(item.get("item_code4", "?")),
                                )
                            )
                if not requests:
                    result = _configuration_result("ecos", "ECOS 통계 대상 설정이 필요합니다.")
                else:
                    ecos = EcosProvider(self.credentials)
                    if not ecos.operationally_configured():
                        result = ecos.collect_statistics(tuple(requests))
                    else:
                        maximum_attempts = ECOS_MAX_LOGICAL_CALLS_PER_RUN * (
                            ecos.client.max_retries + 1
                        )
                        usage_date = self.clock().astimezone(SEOUL).date().isoformat()
                        if not self.store.reserve_provider_calls(
                            "ecos",
                            usage_date,
                            requested_calls=maximum_attempts,
                            daily_limit=ECOS_DAILY_ATTEMPT_CEILING,
                            updated_at=self.clock(),
                        ):
                            result = _configuration_result(
                                "ecos",
                                "ECOS 비공식 안전 호출 예산을 모두 예약하여 오늘 추가 호출을 중지했습니다.",
                            )
                        else:
                            result = ecos.collect_statistics(tuple(requests))
            else:  # settings validation normally prevents this
                continue
            self.store.save_collection(result)
            results.append(result)

        if not results:
            return AutomationSummary("ERROR", "활성화된 수집 공급자가 없습니다.")
        if all(result.status is CollectionStatus.OK for result in results):
            return AutomationSummary("OK", "공식자료 갱신을 완료했습니다.", tuple(results))
        if any(result.records for result in results):
            return AutomationSummary(
                "PARTIAL",
                "공식자료를 일부 갱신했습니다. 미완료 항목과 기준시각을 확인하세요.",
                tuple(results),
            )
        return AutomationSummary(
            "ERROR",
            "공식자료를 갱신하지 못했습니다. 이전 정상 자료는 유지됩니다.",
            tuple(results),
        )

    def set_strategy_mix(
        self,
        version: str,
        weights: Mapping[StrategyKind, Decimal | str | int],
    ) -> bool:
        mix = StrategyMix(version, weights)
        encoded = {
            "version": mix.version,
            "weights": {key.value: str(value) for key, value in mix.weights.items()},
            "scope": "CANDIDATE_SCORING_ONLY",
            "asset_allocation_unchanged": True,
        }
        created = self.store.save_strategy_version(
            version_name=mix.version,
            config=encoded,
            created_at=self.clock(),
        )
        if not created:
            return False
        self.store.set_setting("strategy.current", encoded, updated_at=self.clock())
        return True

    def current_strategy_mix(self) -> StrategyMix | None:
        raw = self.store.get_setting("strategy.current", None)
        if raw is None:
            return None
        try:
            version = str(raw["version"])
            raw_weights = raw["weights"]
            if not isinstance(raw_weights, dict):
                raise TypeError
            weights = {
                StrategyKind(str(key)): Decimal(str(value))
                for key, value in raw_weights.items()
            }
            return StrategyMix(version, weights)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise DomainValidationError("저장된 전략 설정을 확인할 수 없습니다.") from None

    def strategy_workspace_snapshot(
        self,
        *,
        now: datetime | None = None,
        assessment_limit: int = 50,
    ) -> StrategyWorkspaceSnapshot:
        """Build the local, read-only model used by the strategy GUI.

        This method performs no provider call.  User-derived version and
        instrument labels are masked before they leave the service.  Historical
        assessment reasons remain one explicitly named combined list because
        the append-only schema did not preserve positive/adverse categories.
        """

        current = now or self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise DomainValidationError("전략 화면 기준 시각에는 시간대가 필요합니다.")
        if not isinstance(assessment_limit, int) or isinstance(assessment_limit, bool):
            raise DomainValidationError("전략 화면 조회 개수를 확인할 수 없습니다.")
        if not 1 <= assessment_limit <= 100:
            raise DomainValidationError("전략 화면 조회 개수를 확인할 수 없습니다.")

        try:
            mix = self.current_strategy_mix()
            mix_view: StrategyMixView | None = None
            if mix is not None:
                version_row = self.store.strategy_version_row(mix.version)
                if version_row is None:
                    raise DomainValidationError("현재 전략의 불변 버전을 확인할 수 없습니다.")
                saved_config = json.loads(str(version_row["config_json"]))
                if not isinstance(saved_config, dict):
                    raise TypeError
                stored_weights = saved_config.get("weights")
                if not isinstance(stored_weights, dict):
                    raise TypeError
                stored_mix = StrategyMix(
                    str(saved_config["version"]),
                    {
                        StrategyKind(str(key)): Decimal(str(value))
                        for key, value in stored_weights.items()
                    },
                )
                if stored_mix != mix:
                    raise DomainValidationError("현재 전략과 불변 버전이 일치하지 않습니다.")
                mix_view = StrategyMixView(
                    version_display=_safe_plain_user_label(mix.version),
                    weights=tuple(
                        StrategyWeightView(
                            strategy,
                            mix.weights.get(strategy, Decimal("0")),
                        )
                        for strategy in StrategyKind
                    ),
                    saved_at=_workspace_timestamp(version_row["created_at"]),
                )

            assessment_views: list[CandidateAssessmentView] = []
            for row in self.store.candidate_assessment_rows(limit=assessment_limit):
                cutoff_raw = str(row["cutoff_at"])
                created_raw = str(row["created_at"])
                cutoff = datetime.fromisoformat(cutoff_raw)
                created = datetime.fromisoformat(created_raw)
                if (
                    cutoff.tzinfo is None
                    or cutoff.utcoffset() is None
                    or created.tzinfo is None
                    or created.utcoffset() is None
                    or cutoff > created
                ):
                    raise ValueError
                if created > current:
                    # A future audit row is not current GUI evidence.
                    continue
                status = str(row["status"])
                if status not in {value.value for value in AssessmentStatus}:
                    raise ValueError
                score: Decimal | None = None
                if row["score_text"] is not None:
                    score = Decimal(str(row["score_text"]))
                    if not score.is_finite() or score < 0 or score > Decimal("100"):
                        raise ValueError
                if status == AssessmentStatus.ELIGIBLE.value and score is None:
                    raise ValueError
                if status != AssessmentStatus.ELIGIBLE.value and score is not None:
                    raise ValueError

                reasons_raw = json.loads(str(row["reasons_json"]))
                source_ids_raw = json.loads(str(row["source_record_ids_json"]))
                if (
                    not isinstance(reasons_raw, list)
                    or not reasons_raw
                    or len(reasons_raw) > 200
                    or any(not isinstance(value, str) or not value for value in reasons_raw)
                    or not isinstance(source_ids_raw, list)
                    or len(source_ids_raw) > 500
                    or any(
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or value <= 0
                        for value in source_ids_raw
                    )
                    or len(set(source_ids_raw)) != len(source_ids_raw)
                ):
                    raise ValueError
                source_ids = tuple(source_ids_raw)
                source_rows = self.store.source_record_rows(
                    source_ids,
                    cutoff_at=cutoff,
                )
                source_timings = tuple(
                    SourceTimingView(
                        record_id=int(source_row["id"]),
                        effective_at=_workspace_timestamp(source_row["effective_at"]),
                        collected_at=_workspace_timestamp(source_row["collected_at"]),
                    )
                    for source_row in source_rows
                )
                assessment_views.append(
                    CandidateAssessmentView(
                        strategy_version_display=_safe_plain_user_label(
                            row["strategy_version"]
                        ),
                        instrument_display=_safe_plain_user_label(row["instrument_id"]),
                        status=status,
                        score=score,
                        combined_evidence_constraints=tuple(
                            _safe_plain_user_label(value) for value in reasons_raw
                        ),
                        source_record_ids=source_ids,
                        cutoff_at=_workspace_timestamp(cutoff_raw),
                        created_at=_workspace_timestamp(created_raw),
                        source_timings=source_timings,
                    )
                )

            return StrategyWorkspaceSnapshot(
                current_mix=mix_view,
                assessments=tuple(assessment_views),
                input_states=self._strategy_input_states(current),
            )
        except DomainValidationError:
            raise DomainValidationError(
                "전략 화면 자료를 안전하게 읽을 수 없습니다."
            ) from None
        except (
            InvalidOperation,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
            json.JSONDecodeError,
        ):
            raise DomainValidationError(
                "전략 화면 자료를 안전하게 읽을 수 없습니다."
            ) from None

    def _strategy_input_states(
        self,
        cutoff_at: datetime,
    ) -> tuple[StrategyInputStateView, ...]:
        """Describe inputs wired into the current evaluator, without collection."""

        enabled = set(self.enabled_providers())
        configured = self.store.get_setting("provider.targets.kis", [])
        targets, rejected = _validate_provider_targets("kis", configured)
        targets = tuple(dict.fromkeys(targets))
        environment = self.store.get_setting("provider.kis.environment", "paper")

        if "kis" not in enabled or not targets or environment not in {"paper", "live"}:
            snapshot_state = "미연결"
            snapshot_detail = "KIS 활성화와 유효한 6자리 대상 설정이 필요합니다."
        else:
            metrics_by_instrument: dict[str, set[str]] = {
                target: set() for target in targets
            }
            selected_bundle: dict[str, tuple[str, str, int]] = {}
            for row in self.store.latest_records(
                provider="kis",
                kind=RecordKind.PRICE_VOLUME,
                cutoff_at=cutoff_at,
                require_known_publication_time=True,
            ):
                instrument = str(row["instrument_id"])
                if instrument not in metrics_by_instrument:
                    continue
                try:
                    metadata = json.loads(str(row["metadata_json"]))
                    metric = str(metadata["metric"])
                    if metadata.get("environment") != environment:
                        continue
                    bundle = (
                        str(row["effective_at"]),
                        str(row["collected_at"]),
                        int(row["run_id"]),
                    )
                except (
                    json.JSONDecodeError,
                    KeyError,
                    TypeError,
                    ValueError,
                    OverflowError,
                ):
                    continue
                chosen = selected_bundle.setdefault(instrument, bundle)
                if bundle != chosen:
                    continue
                if metric in {"price", "volume", "trading_value"}:
                    metrics_by_instrument[instrument].add(metric)
            complete = sum(
                metrics >= {"price", "volume", "trading_value"}
                for metrics in metrics_by_instrument.values()
            )
            if complete == len(targets):
                snapshot_state = "연결됨"
                snapshot_detail = "설정 대상의 최신 가격·거래량·거래대금이 저장되어 있습니다."
            else:
                snapshot_state = "결측"
                snapshot_detail = (
                    "설정 대상 중 최신 가격·거래량·거래대금 3종이 모두 있는 대상은 "
                    f"{complete}/{len(targets)}개입니다."
                )
            if rejected:
                snapshot_detail += " 형식 위반 저장 대상은 제외했습니다."

        verified_raw = self.store.get_setting(
            "strategy.policy_verified_instruments", []
        )
        verified = (
            {value for value in verified_raw if isinstance(value, str)}
            if isinstance(verified_raw, list)
            else set()
        )
        if not targets:
            policy_state = "미연결"
            policy_detail = "평가 대상이 설정되지 않았습니다."
        elif all(target in verified for target in targets):
            policy_state = "연결됨"
            policy_detail = "모든 설정 대상의 상품 정책 확인 상태가 저장되어 있습니다."
        else:
            policy_state = "결측"
            policy_detail = "일부 대상의 상품유형·금지상품 정책 확인이 없습니다."

        rows: list[StrategyInputStateView] = []
        factor_inputs = {
            StrategyKind.BROAD_MARKET: (
                "광범위 지수 편입 범위 점수",
                "거래소·ETF 발행사 상품정보",
                "지수 구성·상품 분류를 점수로 바꾸는 경로가 아직 미연결입니다.",
            ),
            StrategyKind.QUALITY: (
                "재무 품질 점수",
                "SEC/OpenDART 재무",
                "수집 재무를 품질 점수로 바꾸는 경로가 아직 미연결입니다.",
            ),
            StrategyKind.VALUE: (
                "업종 내 가치평가 점수",
                "SEC/OpenDART 재무",
                "업종 분류와 가치평가 점수 경로가 아직 미연결입니다.",
            ),
            StrategyKind.DIVIDEND_GROWTH: (
                "배당 성장 이력 점수",
                "공시·배당 이력",
                "배당 이력을 점수로 바꾸는 경로가 아직 미연결입니다.",
            ),
            StrategyKind.SATELLITE_THEME: (
                "테마·섹터 근거 및 공시·뉴스 위험",
                "상품정보·공시·허용 뉴스",
                "테마 점수와 공시·뉴스 위험을 후보 입력에 연결하지 않았습니다.",
            ),
            StrategyKind.BOND_CASH: (
                "채권·현금성 분류 및 적합성 점수",
                "거래소·발행사 상품정보",
                "채권·현금성 상품 분류 점수 경로가 아직 미연결입니다.",
            ),
        }
        for strategy in StrategyKind:
            if strategy in factor_inputs:
                label, provider, detail = factor_inputs[strategy]
                rows.append(
                    StrategyInputStateView(
                        strategy,
                        label,
                        provider,
                        "미연결",
                        detail,
                    )
                )
            elif strategy is StrategyKind.LOW_VOLATILITY:
                rows.extend(
                    (
                        StrategyInputStateView(
                            strategy,
                            "현재 가격·거래량·거래대금 스냅샷",
                            "KIS 시세",
                            snapshot_state,
                            snapshot_detail,
                        ),
                        StrategyInputStateView(
                            strategy,
                            "20일 수익률용 최소 21시점 가격",
                            "KIS 시세",
                            "미연결" if snapshot_state == "미연결" else "결측",
                            "현재 평가 경로는 최신 동일시점 1개 묶음만 사용합니다.",
                        ),
                    )
                )
            elif strategy is StrategyKind.MOMENTUM:
                rows.extend(
                    (
                        StrategyInputStateView(
                            strategy,
                            "현재 가격·거래량·거래대금 스냅샷",
                            "KIS 시세",
                            snapshot_state,
                            snapshot_detail,
                        ),
                        StrategyInputStateView(
                            strategy,
                            "6개월 추세용 최소 126시점 가격",
                            "KIS 시세",
                            "미연결" if snapshot_state == "미연결" else "결측",
                            "현재 평가 경로는 최신 동일시점 1개 묶음만 사용합니다.",
                        ),
                    )
                )
            rows.append(
                StrategyInputStateView(
                    strategy,
                    "상품유형·금지상품 정책 확인",
                    "사용자 정책 확인 설정",
                    policy_state,
                    policy_detail,
                )
            )
        return tuple(rows)

    def strategy_description(self) -> str:
        try:
            mix = self.current_strategy_mix()
        except DomainValidationError:
            return "설정 확인 필요 · 후보 평가 중지"
        if mix is None:
            return "미선택 · 후보 점수와 매수 수량을 만들지 않음"
        return ", ".join(
            f"{strategy.value} {weight}%"
            for strategy, weight in mix.weights.items()
        )

    def assess_configured_candidates(
        self,
        *,
        cutoff_at: datetime,
    ) -> tuple[CandidateAssessment, ...]:
        """KIS 읽기 전용 스냅샷만으로 재현 가능한 범위의 후보 평가를 만든다.

        공시·재무·상품분류가 연결되지 않은 값은 만들어 내지 않는다. KIS 현재가
        스냅샷은 공식 일봉·수정주가가 아니므로 최신 동일시점 가격·거래량·거래대금
        표시만 사용하며, 이를 누적해 모멘텀/변동성 시계열로 오인하지 않는다.
        """

        aware = cutoff_at
        if aware.tzinfo is None or aware.utcoffset() is None:
            raise DomainValidationError("후보 평가 기준 시각에는 시간대가 필요합니다.")
        mix = self.current_strategy_mix()
        if mix is None:
            return ()
        configured = self.store.get_setting("provider.targets.kis", [])
        validated_targets, _ = _validate_provider_targets("kis", configured)
        targets = tuple(dict.fromkeys(validated_targets))
        environment = self.store.get_setting("provider.kis.environment", "paper")
        if environment not in {"paper", "live"}:
            raise DomainValidationError("KIS 시세 환경 설정을 확인할 수 없습니다.")

        rows = self.store.latest_records(
            kind=RecordKind.PRICE_VOLUME,
            provider="kis",
            cutoff_at=aware,
            require_known_publication_time=True,
        )
        by_instrument: dict[str, dict[str, object]] = {}
        selected_bundle: dict[str, tuple[str, str, int]] = {}
        for row in rows:
            instrument = str(row["instrument_id"])
            if instrument not in targets:
                continue
            try:
                metadata = json.loads(row["metadata_json"])
                metric = str(metadata["metric"])
                if metadata.get("environment") != environment:
                    continue
                if metric not in {"price", "volume", "trading_value"}:
                    continue
                effective = datetime.fromisoformat(row["effective_at"])
                collected = datetime.fromisoformat(row["collected_at"])
                if (
                    effective.tzinfo is None
                    or effective.utcoffset() is None
                    or collected.tzinfo is None
                    or collected.utcoffset() is None
                ):
                    continue
                bundle = (
                    str(row["effective_at"]),
                    str(row["collected_at"]),
                    int(row["run_id"]),
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OverflowError):
                continue
            # latest_records is newest first. 첫 정상 행의 정확한 관측시각과 run을
            # 묶음으로 고정해, 같은 날짜의 다른 부분 응답을 섞지 않는다.
            chosen = selected_bundle.setdefault(instrument, bundle)
            if bundle != chosen:
                continue
            by_instrument.setdefault(instrument, {}).setdefault(metric, row)

        prohibited_raw = self.store.get_setting("strategy.prohibited_instruments", [])
        prohibited = {
            str(value) for value in prohibited_raw
        } if isinstance(prohibited_raw, list) else set()
        verified_raw = self.store.get_setting("strategy.policy_verified_instruments", [])
        policy_verified = {
            str(value) for value in verified_raw
        } if isinstance(verified_raw, list) else set()
        assessments: list[CandidateAssessment] = []
        for instrument in targets:
            metrics = by_instrument.get(instrument, {})
            prices: list[Decimal] = []
            volumes: list[Decimal] = []
            trading_values: list[Decimal] = []
            source_ids: set[int] = set()
            if metrics:
                price_row = metrics.get("price")
                volume_row = metrics.get("volume")
                trading_row = metrics.get("trading_value")
                if price_row is not None:
                    prices.append(Decimal(str(price_row["value_text"])))  # type: ignore[index]
                    source_ids.add(int(price_row["id"]))  # type: ignore[index]
                if volume_row is not None:
                    volumes.append(Decimal(str(volume_row["value_text"])))  # type: ignore[index]
                    source_ids.add(int(volume_row["id"]))  # type: ignore[index]
                if trading_row is not None:
                    trading_values.append(Decimal(str(trading_row["value_text"])))  # type: ignore[index]
                    source_ids.add(int(trading_row["id"]))  # type: ignore[index]
            assessment = evaluate_candidate(
                CandidateInput(
                    instrument_id=instrument,
                    prices=tuple(prices),
                    volumes=tuple(volumes),
                    trading_values=tuple(trading_values),
                    disclosure_or_news_risk=None,
                    prohibited_product=instrument in prohibited,
                    product_policy_verified=instrument in policy_verified,
                    source_record_ids=tuple(sorted(source_ids)),
                ),
                mix,
            )
            reasons = (
                *assessment.positive_reasons,
                *assessment.counter_reasons,
                *(component.reason for component in assessment.components),
            ) or ("평가 근거 자료 없음",)
            self.store.save_candidate_assessment(
                strategy_version=mix.version,
                instrument_id=instrument,
                cutoff_at=aware,
                status=assessment.status.value,
                score=assessment.total_score,
                reasons=reasons,
                source_record_ids=assessment.source_record_ids,
                created_at=self.clock(),
            )
            assessments.append(assessment)
        return tuple(assessments)

    def latest_report(self) -> str | None:
        row = self.store.latest_report()
        return None if row is None else str(row["markdown"])

    def generate_report(
        self,
        *,
        now: datetime | None = None,
        scheduled: bool = False,
        scheduled_for: datetime | None = None,
    ) -> str:
        current = now or self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise DomainValidationError("보고서 생성 시각에는 시간대가 필요합니다.")
        local_now = current.astimezone(SEOUL)
        data_cutoff = current
        catchup_warning: str | None = None
        if scheduled_for is not None:
            if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
                raise DomainValidationError("예약 기준 시각에는 시간대가 필요합니다.")
            schedule_date = scheduled_for.astimezone(SEOUL).date()
            if scheduled and scheduled_for < current:
                data_cutoff = scheduled_for
                catchup_warning = (
                    "예약 시각 이후 재실행된 보고서입니다. 자료는 대상 예약시각의 "
                    "역사적 컷오프까지만 사용했습니다."
                )
        else:
            schedule_date = local_now.date()
        statuses = self.provider_statuses(now=data_cutoff)
        enabled = set(self.enabled_providers())
        active_rows = [row for row in statuses if row.code in enabled]
        normal = sum(row.state == "OK" for row in active_rows)
        partial_or_error = [row for row in active_rows if row.state != "OK"]
        data_state = "OK" if active_rows and not partial_or_error and normal == len(active_rows) else "PARTIAL"

        scheduled_collect = datetime.combine(schedule_date, time(15, 0), tzinfo=SEOUL)
        collect_job = self.store.job_status("collect", scheduled_collect)
        collection_job_warning: str | None = None
        if collect_job is None:
            if scheduled:
                collection_job_warning = (
                    "대상 날짜 15:00 수집 작업 실행 이력이 없어 정기 보고서를 부분 결과로 생성했습니다."
                )
        else:
            collect_status = str(collect_job["status"])
            if collect_status == "RUNNING":
                collection_job_warning = (
                    "대상 날짜 15:00 수집 작업이 아직 완료되지 않아 정기 보고서에 부분 결과만 표시합니다."
                )
            elif collect_status != "OK":
                collection_job_warning = (
                    f"대상 날짜 15:00 수집 작업 상태가 {collect_status}이므로 미완료 항목을 확인해야 합니다."
                )
        if self.store.operation_lease_active("market_collection", at=current):
            collection_job_warning = (
                "시장자료 수집이 아직 진행 중이어서 이 보고서에는 직전 정상 자료와 부분 결과만 표시합니다."
            )
        if collection_job_warning is not None:
            data_state = "PARTIAL"

        macro_rows = self.store.latest_records(
            kind=RecordKind.MACRO,
            cutoff_at=data_cutoff,
            require_known_publication_time=True,
        )
        latest_by_series: dict[str, object] = {}
        for row in macro_rows:
            latest_by_series.setdefault(row["instrument_id"], row)
        macro_lines: list[str] = []
        labels = {
            "CUUR0000SA0": ("미국 CPI(도시소비자, 비계절조정)", "지수(1982-84=100)"),
            "LNS14000000": ("미국 실업률(계절조정)", "%"),
        }
        for identifier, row in latest_by_series.items():
            known_label = labels.get(identifier)
            if known_label is None:
                # BLS series identifiers originate in user target settings even
                # though their observations later arrive from the provider.
                label = _safe_label(identifier)
                unit = _safe_external_label(row["unit"])
            else:
                label = _safe_external_label(known_label[0])
                unit = _safe_external_label(known_label[1])
            macro_lines.append(
                f"- {label}: {row['value_text']} {unit} · 기준 {row['effective_at']} · "
                f"최초 확인/수집 {row['collected_at']} · 출처 레코드 #{row['id']}"
            )

        warnings: list[str] = []
        if catchup_warning is not None:
            warnings.append(catchup_warning)
        if collection_job_warning is not None:
            warnings.append(collection_job_warning)
        if not active_rows:
            warnings.append("활성화된 공식자료 공급자가 없습니다.")
        for provider in sorted({*_PROVIDER_TARGET_PATTERNS, "fed_news"}):
            default_targets: object = (
                list(DEFAULT_BLS_SERIES) if provider == "bls" else []
            )
            configured_targets = self.store.get_setting(
                f"provider.targets.{provider}", default_targets
            )
            _, rejected = _validate_provider_targets(provider, configured_targets)
            if rejected:
                if provider in enabled:
                    data_state = "PARTIAL"
                warnings.append(
                    f"{provider.upper()} 저장 대상 설정에서 형식 위반 값을 제외했습니다. "
                    "대상 원문은 표시하지 않습니다."
                )
        for row in active_rows:
            if row.state != "OK":
                warnings.append(f"{row.name}: {row.message}")
        kis_environment_value = self.store.get_setting("provider.kis.environment", "paper")
        kis_environment = (
            str(kis_environment_value)
            if kis_environment_value in {"paper", "live"}
            else None
        )
        if kis_environment is None:
            warnings.append("KIS 시세 환경 설정을 확인할 수 없어 기존 관측을 표시하지 않습니다.")
        kis_records = self.store.latest_records(
            kind=RecordKind.PRICE_VOLUME,
            provider="kis",
            cutoff_at=data_cutoff,
            require_known_publication_time=True,
        )
        market_observation_lines: list[str] = []
        selected_market_rows: list[object] = []
        seen_market_metrics: set[tuple[str, str]] = set()
        selected_market_bundle: dict[str, tuple[str, str, int]] = {}
        market_metric_labels = {
            "price": "현재가 관측",
            "volume": "누적 거래량 관측",
            "trading_value": "누적 거래대금 관측",
        }
        for row in kis_records:
            try:
                metadata = json.loads(row["metadata_json"])
                metric = str(metadata["metric"])
                if kis_environment is None or metadata.get("environment") != kis_environment:
                    continue
                bundle = (
                    str(row["effective_at"]),
                    str(row["collected_at"]),
                    int(row["run_id"]),
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            instrument = str(row["instrument_id"])
            chosen_bundle = selected_market_bundle.setdefault(instrument, bundle)
            if bundle != chosen_bundle:
                continue
            key = (instrument, metric)
            if metric not in market_metric_labels or key in seen_market_metrics:
                continue
            seen_market_metrics.add(key)
            selected_market_rows.append(row)
            market_observation_lines.append(
                f"- {_safe_label(row['instrument_id'])} {market_metric_labels[metric]}: "
                f"{row['value_text']} {_safe_external_label(row['unit'])} · 관측 {row['effective_at']} · "
                f"환경 {kis_environment} · 공식 종가/수정주가 아님 · 출처 레코드 #{row['id']}"
            )
            if len(market_observation_lines) >= 100:
                break
        if not selected_market_rows:
            warnings.append(
                "국내 주식·ETF 현재가 자료가 없어 가격 기반 후보 분석은 평가 불가입니다."
            )
        else:
            warnings.append(
                "KIS 값은 수집 시점 현재가 관측이며 공식 종가·수정주가로 표시하지 않습니다."
            )
        warnings.extend(
            (
                "해외 주식·ETF 가격 공급자는 아직 미연결입니다.",
                "연준 공식 보도자료 외 광범위 시장 뉴스 공급자는 이용 권한 확인 전이라 미연결입니다.",
                "외부 문서의 문구는 데이터일 뿐 앱 권한·설정·정책을 변경하지 않습니다.",
            )
        )

        assessment_error: str | None = None
        try:
            assessments = self.assess_configured_candidates(cutoff_at=data_cutoff)
        except DomainValidationError:
            assessments = ()
            assessment_error = "후보 평가 설정 또는 저장 자료를 확인할 수 없어 평가를 중지했습니다."
            # 공급자 수집 자체가 정상이더라도 정기 분석을 완료하지 못한
            # 보고서를 OK로 기록하지 않는다. 본문의 미완료 안내와 DB/예약
            # 실행 상태를 같은 PARTIAL 의미로 맞춘다.
            data_state = "PARTIAL"
            warnings.append(assessment_error)

        candidate_lines: list[str] = []
        if assessment_error is not None:
            candidate_lines.append(f"- {assessment_error}")
        elif self.current_strategy_mix() is None:
            candidate_lines.append("- 전략 미선택: 후보 점수를 생성하지 않았습니다.")
        elif not assessments:
            candidate_lines.append("- KIS 후보 종목 미설정: 후보 점수를 생성하지 않았습니다.")
        else:
            for assessment in assessments:
                metrics = assessment.metrics
                score = (
                    f"{assessment.total_score}%"
                    if assessment.total_score is not None
                    else "계산 불가"
                )
                positive = ", ".join(_safe_label(value) for value in assessment.positive_reasons)
                counter = ", ".join(_safe_label(value) for value in assessment.counter_reasons)
                if not assessment.source_record_ids:
                    source_ids = "없음"
                elif len(assessment.source_record_ids) <= 12:
                    source_ids = ",".join(str(value) for value in assessment.source_record_ids)
                else:
                    source_ids = (
                        f"{assessment.source_record_ids[0]}…{assessment.source_record_ids[-1]} "
                        f"(총 {len(assessment.source_record_ids)}개; DB 평가 이력에 전체 보존)"
                    )
                candidate_lines.append(
                    f"- {_safe_label(assessment.instrument_id)}: {assessment.status.value} · "
                    f"점수 {score} · 현재가 {metrics.latest_price if metrics.latest_price is not None else '미입력'} · "
                    f"거래량 {metrics.latest_volume if metrics.latest_volume is not None else '미입력'} · "
                    f"거래대금 {metrics.latest_trading_value if metrics.latest_trading_value is not None else '미입력'} · "
                    f"상대거래량 {metrics.relative_volume_20d if metrics.relative_volume_20d is not None else '계산 불가'} · "
                    f"6개월 추세 {metrics.momentum_6m if metrics.momentum_6m is not None else '계산 불가'} · "
                    f"변동성 {metrics.annualized_volatility if metrics.annualized_volatility is not None else '계산 불가'} · "
                    f"찬성근거 {positive or '자료 부족'} · 반대/제한 {counter or '확인된 항목 없음'} · "
                    f"출처 레코드 {source_ids}"
                )

        news_lines: list[str] = []
        selected_news_rows: list[object] = []
        seen_news_ids: set[str] = set()
        for row in self.store.latest_records(
            kind=RecordKind.NEWS,
            provider="fed_news",
            cutoff_at=data_cutoff,
            require_known_publication_time=True,
        ):
            logical_id = str(row["provider_record_id"])
            if logical_id in seen_news_ids:
                continue
            seen_news_ids.add(logical_id)
            try:
                metadata = json.loads(row["metadata_json"])
                title = _safe_external_label(metadata["title"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            selected_news_rows.append(row)
            news_lines.append(
                f"- {title} · 공표 {row['published_at']} · 출처 레코드 #{row['id']}"
            )
            if len(news_lines) >= 10:
                break

        filing_lines: list[str] = []
        selected_filing_rows: list[object] = []
        seen_filings: set[tuple[str, str, str]] = set()
        for record_kind in (RecordKind.DISCLOSURE, RecordKind.FINANCIAL):
            for row in self.store.latest_records(
                kind=record_kind,
                cutoff_at=data_cutoff,
                require_known_publication_time=True,
            ):
                key = (
                    str(row["provider"]),
                    record_kind.value,
                    str(row["provider_record_id"]),
                )
                if key in seen_filings:
                    continue
                seen_filings.add(key)
                selected_filing_rows.append(row)
                value_text = (
                    f" · 값 {row['value_text']} {_safe_external_label(row['unit'])}"
                    if row["value_text"] is not None
                    else ""
                )
                filing_lines.append(
                    f"- {_safe_external_label(row['provider'])} {_safe_label(row['instrument_id'])} "
                    f"{record_kind.value} · 기준 {row['effective_at']} · 공표/최초확인 "
                    f"{row['published_at']}{value_text} · 정정 {bool(row['revised'])} · "
                    f"출처 레코드 #{row['id']}"
                )
                if len(filing_lines) >= 20:
                    break
            if len(filing_lines) >= 20:
                break

        lines = [
            "# 장기 자산관리 자동 자료 보고서",
            "",
            f"> 자료 영역: **공식 외부자료 / 사용자 원장과 미합산** · 생성시각: {current.astimezone(SEOUL).isoformat()}",
            f"> 자료 컷오프: {data_cutoff.astimezone(SEOUL).isoformat()} · 이 시각 이후 공표·수집 자료는 제외",
            "> 주문 기능 없음 · 투자 판단과 주문 권한은 사용자에게만 있음 · 수익이나 목표 달성을 보장하지 않음",
            "",
            f"## {REPORT_SECTION_TITLES[0]}",
            f"- 공식자료 상태: {data_state} ({normal}/{len(active_rows)} 활성 공급자 최근 정상)",
            f"- 전략 혼합: {self.strategy_description()}",
            "- 사용자 보유자산·예산: 미입력(0원으로 간주하지 않음); 주문안과 수량을 생성하지 않음",
            "",
            f"## {REPORT_SECTION_TITLES[1]}",
            "- 자동 보고서에는 사용자 원장을 연결하지 않았습니다: 목표·실제 비중 **미입력/계산 불가**.",
            "- 외부 시장자료는 SAMPLE/MANUAL_PAPER_SNAPSHOT/MANUAL_ACTUAL 원장과 합산하지 않습니다.",
            "",
            f"## {REPORT_SECTION_TITLES[2]}",
            "- 사용자별 목표 대비: 보유자산·예산 미입력으로 **계산 불가/확인 필요**.",
            "- 전략 비중은 후보 비교용이며 PROJECT_RULES의 자산배분·한도를 바꾸지 않습니다.",
            "",
            f"## {REPORT_SECTION_TITLES[3]}",
            "- 실제 배당: 사용자 원장 미입력으로 **계산 불가/확인 필요**.",
            "- 배당 전환 능력: 실제 세후 배당 자료 미입력으로 계산하지 않습니다.",
            "",
            f"## {REPORT_SECTION_TITLES[4]}",
            "- 위성 원장·연간 신규 투자계획 미입력으로 손실 경계와 상태는 **계산 불가/확인 필요**.",
            "- 결측값을 0원으로 대체하지 않았습니다.",
            "",
            f"## {REPORT_SECTION_TITLES[5]}",
            "- **제안 주문안 없음**: 가격 이력·재무·공시·허가된 뉴스와 사용자 예산이 모두 갖춰지지 않았습니다.",
            "- 후보 평가는 주문이 아니며 미래 가격·수익률·확률을 생성하지 않았습니다.",
            *candidate_lines,
            "",
            f"## {REPORT_SECTION_TITLES[6]}",
        ]
        lines.extend(f"- {_safe_label(warning)}" for warning in warnings)
        lines.extend(
            (
                "",
                "### 공식자료 관측값(투자 추천 아님)",
                *(macro_lines or ["- 수집된 거시 관측값 없음"]),
                "",
                "### 국내 주식·ETF 시세 관측(공식 종가·수정주가 아님)",
                *(market_observation_lines or ["- 수집된 KIS 시세 관측값 없음"]),
                "",
                "### 공시·재무 갱신 요약",
                *(filing_lines or ["- 수집된 공시·재무 자료 없음"]),
                "",
                "### 이용 권한이 확인된 공식 발표 메타데이터(기사 본문 아님)",
                *(news_lines or ["- 수집된 연준 공식 발표 메타데이터 없음"]),
                "",
                "### 공급자별 기준·수집 상태",
            )
        )
        for row in statuses:
            lines.append(
                f"- {row.name}: {row.state} · 자료기준 {row.last_effective} · "
                f"정상자료 수집 {row.last_collected} · 최근 갱신 시도 {row.last_attempted} · "
                f"{_safe_label(row.message)}"
            )
        lines.extend(
            (
                "",
                "> BLS.gov는 내려받은 뒤의 데이터·분석 품질과 시의성을 보증하지 않습니다. 접근일은 공급자별 수집시각으로 기록했습니다.",
                "",
            )
        )
        report = "\n".join(lines)
        candidate_record_ids = {
            int(record_id)
            for assessment in assessments
            for record_id in assessment.source_record_ids
        }
        candidate_run_ids = self.store.run_ids_for_record_ids(
            candidate_record_ids,
            cutoff_at=data_cutoff,
        )
        source_run_ids = tuple(
            sorted(
                {
                    *(int(row["id"]) for row in self.store.provider_summaries(cutoff_at=data_cutoff) if row["provider"] in enabled),
                    *(int(row["run_id"]) for row in latest_by_series.values()),
                    *(int(row["run_id"]) for row in selected_market_rows),
                    *(int(row["run_id"]) for row in selected_filing_rows),
                    *(int(row["run_id"]) for row in selected_news_rows),
                    *candidate_run_ids,
                }
            )
        )
        self.store.save_report(
            report_date=schedule_date.isoformat() if scheduled else local_now.date().isoformat(),
            created_at=current,
            status=data_state,
            markdown=report,
            source_run_ids=source_run_ids,
            data_scope="AUTOMATED_MARKET",
            generation_kind="SCHEDULED" if scheduled else "MANUAL",
            data_cutoff_at=data_cutoff,
        )
        return report

    def save_monthly_scenario(self, source_path: Path) -> bool:
        try:
            source_bytes = source_path.read_bytes()
        except (OSError, ValueError):
            raise DomainValidationError("월별 기록 원본을 읽을 수 없습니다. 경로는 표시하지 않습니다.") from None
        scenario = load_scenario_bytes(source_bytes)
        investment = scenario.ledger.investment_value()
        current = self.clock()
        return self.store.save_monthly_snapshot(
            month=scenario.as_of.astimezone(SEOUL).strftime("%Y-%m"),
            realm=scenario.realm.value,
            as_of=scenario.as_of,
            investment_value=investment.amount_krw,
            complete=investment.complete,
            source_bytes=source_bytes,
            created_at=current,
        )

    def run_scheduled(self, task_name: str, *, now: datetime | None = None) -> AutomationSummary:
        current = now or self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise DomainValidationError("예약 실행 시각에는 시간대가 필요합니다.")
        local = current.astimezone(SEOUL)
        scheduled_clock = time(15, 0) if task_name == "collect" else time(16, 0)
        scheduled = datetime.combine(local.date(), scheduled_clock, tzinfo=SEOUL)
        if local < scheduled:
            # Persistent/StartWhenAvailable가 자정 이후 놓친 작업을 호출하면
            # 아직 오지 않은 오늘 슬롯이 아니라 가장 최근 과거 슬롯에 귀속한다.
            scheduled -= timedelta(days=1)
        if not self.store.claim_job(task_name, scheduled, current):
            report_date: str | None = None
            if task_name == "report":
                report_date = scheduled.date().isoformat()
            return AutomationSummary(
                "SKIPPED_DUPLICATE",
                "같은 예약 슬롯의 작업이 이미 시작되어 중복 실행하지 않았습니다.",
                report_date=report_date,
            )
        try:
            if task_name == "collect":
                summary = self.collect()
                # 15시 예약 검토는 자료 수집만으로 끝나지 않는다. 수집에
                # 정상 레코드가 하나라도 남은 OK/PARTIAL 상태라면 같은
                # 기준시각으로 설정된 후보를 다시 평가한다. 전략이 없으면
                # assess_configured_candidates()가 빈 결과를 반환하므로 안전하게
                # 건너뛰며, 평가 설정/저장 오류는 수집 성공을 지우지 않고
                # 예약 작업 전체를 PARTIAL로 낮춘다.
                if summary.status in {"OK", "PARTIAL"}:
                    try:
                        # 수집을 시작한 시각이 아니라 수집 반환 직후 시각을
                        # 사용해야 방금 저장한 FIRST_OBSERVED_AT_COLLECTION
                        # 레코드가 당일 후보 평가 근거에 포함된다. 예약 슬롯과
                        # 중복 claim은 위에서 계산한 current/scheduled를 그대로
                        # 유지한다.
                        assessment_cutoff = self.clock()
                        self.assess_configured_candidates(
                            cutoff_at=assessment_cutoff
                        )
                    except DomainValidationError:
                        summary = AutomationSummary(
                            "PARTIAL",
                            summary.message
                            + " 후보 재평가를 완료하지 못해 확인이 필요합니다.",
                            provider_results=summary.provider_results,
                        )
            elif task_name == "report":
                report = self.generate_report(
                    now=current,
                    scheduled=True,
                    scheduled_for=scheduled,
                )
                saved = self.store.latest_report()
                report_status = (
                    str(saved["status"]) if saved is not None else "ERROR"
                )
                summary = AutomationSummary(
                    report_status,
                    "정기 보고서를 생성했습니다."
                    if report_status == "OK"
                    else "부분 자료와 미완료 항목을 포함한 정기 보고서를 생성했습니다.",
                    report=report,
                    report_date=scheduled.date().isoformat(),
                )
            else:
                raise DomainValidationError("지원하지 않는 예약 작업입니다.")
        except Exception:
            self.store.finish_job(
                task_name,
                scheduled,
                finished_at=self.clock(),
                status="ERROR",
                detail_code="SAFE_FAILURE",
            )
            raise
        self.store.finish_job(
            task_name,
            scheduled,
            finished_at=self.clock(),
            status=summary.status,
            detail_code=summary.status,
        )
        return summary


def _write_report_file(report: str, output: Path) -> None:
    temporary_path: Path | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output.parent, delete=False
        ) as temporary:
            temporary.write(report)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(output)
    except (OSError, RuntimeError, ValueError):
        raise DomainValidationError("자동 보고서를 저장할 수 없습니다. 경로는 표시하지 않습니다.") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def build_parser() -> _SafeArgumentParser:
    parser = _SafeArgumentParser(
        prog="asset-assistant-automation",
        description="공식 시장자료 읽기 자동화(계좌 조회·주문 기능 없음)",
    )
    parser.add_argument("--database", type=Path, help="사용자 시장자료 DB 경로")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="공식자료 수집·검증")
    collect.add_argument(
        "--scheduled",
        action="store_true",
        help="OS 예약 작업 전용: 같은 날짜 작업의 중복 실행 방지",
    )
    report = subparsers.add_parser("report", help="16:00 7개 절 자동 보고서")
    report.add_argument("--output", type=Path, help="선택적 Markdown 저장 경로")
    report.add_argument(
        "--scheduled",
        action="store_true",
        help="OS 예약 작업 전용: 같은 날짜 작업의 중복 실행 방지",
    )
    subparsers.add_parser("status", help="공급자·실행 상태")
    targets = subparsers.add_parser("targets", help="비밀이 아닌 공급자 대상 식별자 설정")
    targets.add_argument("--provider", required=True, choices=sorted(IMPLEMENTED_PROVIDERS))
    targets.add_argument("identifiers", nargs="*")
    targets.add_argument("--disable", action="store_true")
    targets.add_argument("--kis-environment", choices=("paper", "live"))
    sec_concepts = subparsers.add_parser("sec-concepts", help="SEC 선택 재무 concept 설정")
    sec_concepts.add_argument("concepts", nargs="+")
    ecos = subparsers.add_parser("ecos-series", help="ECOS 구조화 시계열 요청 추가")
    ecos.add_argument("--statistic-code", required=True)
    ecos.add_argument("--cycle", required=True, choices=("A", "S", "Q", "M", "SM", "D"))
    ecos.add_argument("--start-period", required=True)
    ecos.add_argument("--end-period", required=True)
    ecos.add_argument("--item-code1", required=True)
    ecos.add_argument("--item-code2", default="?")
    ecos.add_argument("--item-code3", default="?")
    ecos.add_argument("--item-code4", default="?")
    dart_financial = subparsers.add_parser(
        "opendart-financial", help="OpenDART 재무 상세 요청 추가"
    )
    dart_financial.add_argument("--corp-code", required=True)
    dart_financial.add_argument("--business-year", required=True)
    dart_financial.add_argument(
        "--report-code", required=True, choices=("11011", "11012", "11013", "11014")
    )
    dart_financial.add_argument(
        "--financial-division", required=True, choices=("CFS", "OFS")
    )
    strategy = subparsers.add_parser("strategy", help="후보 평가용 전략 혼합 저장")
    strategy.add_argument("--version", required=True)
    strategy.add_argument("weights", nargs="+")
    monthly = subparsers.add_parser("monthly-snapshot", help="검증된 수동 원장의 월별 요약 저장")
    monthly.add_argument("--input", type=Path, required=True)
    return parser


def _parse_weights(values: list[str]) -> dict[StrategyKind, Decimal]:
    result: dict[StrategyKind, Decimal] = {}
    try:
        for item in values:
            name, raw = item.split("=", 1)
            strategy = StrategyKind(name)
            if strategy in result:
                raise ValueError
            result[strategy] = Decimal(raw)
    except (ValueError, TypeError, ArithmeticError):
        raise DomainValidationError("전략 비중은 STRATEGY=DECIMAL 형식이어야 합니다.") from None
    return result


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = AutomationService(
        store=MarketDataStore(args.database) if args.database else MarketDataStore()
    )
    try:
        if args.command == "collect":
            summary = (
                service.run_scheduled("collect")
                if args.scheduled
                else service.collect()
            )
            sys.stdout.write(f"수집 상태: {summary.status} · {summary.message}\n")
            return 0 if summary.status in {"OK", "PARTIAL", "SKIPPED_DUPLICATE"} else 2
        if args.command == "report":
            if args.scheduled:
                summary = service.run_scheduled("report")
                generated_report = summary.report
            else:
                generated_report = service.generate_report()
                saved = service.store.latest_report()
                saved_status = str(saved["status"]) if saved is not None else "ERROR"
                summary = AutomationSummary(
                    saved_status,
                    "보고서를 생성했습니다."
                    if saved_status == "OK"
                    else "부분 자료와 미완료 항목을 포함한 보고서를 생성했습니다.",
                )
            report = generated_report
            if report is None:
                if summary.status == "SKIPPED_DUPLICATE":
                    sys.stdout.write(
                        "보고서 상태: SKIPPED_DUPLICATE · 진행 중인 같은 슬롯을 다시 저장하지 않았습니다.\n"
                    )
                    return 0
                raise DomainValidationError("생성된 자동 보고서를 확인할 수 없습니다.")
            output = (
                args.output
                or default_data_directory()
                / "reports"
                / f"{summary.report_date or _now_seoul().date().isoformat()}.md"
            )
            if paths_refer_to_same_file(service.store.path, output):
                raise DomainValidationError(
                    "시장자료 저장소와 보고서 출력 파일은 같은 파일일 수 없습니다."
                )
            _write_report_file(report, output)
            sys.stdout.write(f"보고서 상태: {summary.status} · 7개 절 저장 완료\n")
            return 0 if summary.status in {"OK", "PARTIAL", "SKIPPED_DUPLICATE"} else 2
        if args.command == "status":
            for row in service.provider_statuses():
                sys.stdout.write(
                    f"{row.code}: {row.state} · 기준 {row.last_effective} · "
                    f"정상자료 수집 {row.last_collected} · 최근 갱신 시도 {row.last_attempted}\n"
                )
            recent_jobs = service.recent_scheduled_jobs()
            if not recent_jobs:
                sys.stdout.write("예약 실행 이력: 없음\n")
            for row in recent_jobs:
                sys.stdout.write(
                    "예약 실행 이력: "
                    f"{row.task} · 예약 {row.scheduled_for} · 시작 {row.started_at} · "
                    f"종료 {row.finished_at} · 상태 {row.status} · 상세 {row.detail_code}\n"
                )
            return 0
        if args.command == "targets":
            if args.kis_environment is not None:
                if args.provider != "kis":
                    raise DomainValidationError("KIS 환경 옵션은 KIS 공급자에만 사용할 수 있습니다.")
                service.set_kis_environment(args.kis_environment)
            service.set_targets(args.provider, tuple(args.identifiers))
            service.set_provider_enabled(args.provider, not args.disable)
            sys.stdout.write("공급자 대상 설정을 저장했습니다. 인증정보는 이 명령으로 저장하지 않습니다.\n")
            return 0
        if args.command == "sec-concepts":
            service.set_sec_concepts(tuple(args.concepts))
            sys.stdout.write("SEC 재무 concept 설정을 저장했습니다. 인증정보는 저장하지 않았습니다.\n")
            return 0
        if args.command == "ecos-series":
            service.set_ecos_series(
                EcosSeriesRequest(
                    args.statistic_code,
                    args.cycle,
                    args.start_period,
                    args.end_period,
                    args.item_code1,
                    args.item_code2,
                    args.item_code3,
                    args.item_code4,
                )
            )
            sys.stdout.write("ECOS 구조화 시계열 설정을 추가했습니다. 인증정보는 저장하지 않았습니다.\n")
            return 0
        if args.command == "opendart-financial":
            service.set_opendart_financial_request(
                corp_code=args.corp_code,
                business_year=args.business_year,
                report_code=args.report_code,
                financial_division=args.financial_division,
            )
            sys.stdout.write("OpenDART 재무 상세 설정을 추가했습니다. 인증정보는 저장하지 않았습니다.\n")
            return 0
        if args.command == "strategy":
            service.set_strategy_mix(args.version, _parse_weights(args.weights))
            sys.stdout.write("후보 평가용 전략 혼합을 저장했습니다. 자산배분 정책은 변경하지 않았습니다.\n")
            return 0
        if args.command == "monthly-snapshot":
            inserted = service.save_monthly_scenario(args.input)
            sys.stdout.write("월별 원장 요약을 저장했습니다.\n" if inserted else "같은 월별 원장 요약이 이미 있습니다.\n")
            return 0
    except DomainValidationError as exc:
        parser.exit(2, f"자동화 오류: {exc}\n")
    except Exception:
        parser.exit(2, "자동화 오류: 작업을 안전하게 완료하지 못했습니다. 원문은 표시하지 않습니다.\n")
    return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
