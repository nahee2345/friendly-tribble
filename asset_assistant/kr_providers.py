"""Read-only adapters for official Korean market-data providers.

Credentials are obtained only from :class:`CredentialStore`.  This module has
no account lookup or transaction API, and provider responses are treated only
as untrusted data.  The official ``sample`` ECOS key is confined to the
explicit connectivity demo and can never produce persistable market records.
"""

from __future__ import annotations

import calendar
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from .credential_store import CredentialStore, CredentialStoreError
from .domain import DomainValidationError
from .market_data import (
    CollectionIssue,
    CollectionResult,
    CollectionStatus,
    NormalizedRecord,
    RecordKind,
)
from .providers import ProviderError, SafeHttpsClient


SEOUL = ZoneInfo("Asia/Seoul")
_DOMESTIC_SYMBOL = re.compile(r"\d{6}\Z")
_CORP_CODE = re.compile(r"\d{8}\Z")
_RECEIPT_NO = re.compile(r"\d{14}\Z")
_DATE = re.compile(r"\d{8}\Z")
_ECOS_CODE = re.compile(r"[A-Za-z0-9_.#-]{1,40}\Z")
_ECOS_ITEM = re.compile(r"[A-Za-z0-9_.#-]{1,40}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_REPORT_CODES = frozenset({"11011", "11012", "11013", "11014"})
_FINANCIAL_DIVISIONS = frozenset({"CFS", "OFS"})
_KIS_MARKETS = {"J": "KRX", "NX": "NXT", "UN": "KRX_NXT_CONSOLIDATED"}
_ECOS_CYCLES = frozenset({"A", "S", "Q", "M", "SM", "D"})
ECOS_MIN_REQUEST_INTERVAL_SECONDS = 0.25
ECOS_MAX_LOGICAL_CALLS_PER_RUN = 100
ECOS_DAILY_ATTEMPT_CEILING = 900


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _result(
    provider: str,
    started: datetime,
    records: list[NormalizedRecord],
    issues: list[CollectionIssue],
    clock: Callable[[], datetime],
) -> CollectionResult:
    status = (
        CollectionStatus.PARTIAL
        if records and issues
        else CollectionStatus.OK
        if records
        else CollectionStatus.ERROR
    )
    return CollectionResult(provider, started, clock(), status, tuple(records), tuple(issues))


def _issue(code: str, message: str) -> CollectionIssue:
    """Create an issue without ever copying exception/provider text."""

    return CollectionIssue(code, message)


def _error_code(error: Exception) -> str:
    return error.code if isinstance(error, ProviderError) else "INVALID_RESPONSE"


def _credential(
    store: CredentialStore,
    provider: str,
    field: str,
) -> str | None:
    try:
        return store.get_secret(provider, field)
    except CredentialStoreError:
        return None


def _safe_external_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value if character >= " " and character != "\x7f")[
        :limit
    ]


def _parse_date(value: str, *, code: str = "INVALID_RESPONSE") -> datetime:
    if _DATE.fullmatch(value) is None:
        raise ProviderError(code, "공급자 응답의 날짜 형식을 확인할 수 없습니다.")
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=SEOUL)
    except ValueError:
        raise ProviderError(code, "공급자 응답의 날짜 형식을 확인할 수 없습니다.") from None


def _validate_date_input(value: str, field_name: str) -> str:
    try:
        _parse_date(value, code="INVALID_INPUT")
    except ProviderError:
        raise DomainValidationError(f"{field_name}은 YYYYMMDD 형식의 실제 날짜여야 합니다.") from None
    return value


def _decimal_text(value: Any) -> str | None:
    """Normalize a provider decimal without turning missing markers into zero."""

    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "N/A", "NA", "null", "None"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1].strip()}"
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text) is None:
        raise ProviderError("INVALID_RESPONSE", "공급자 숫자 형식을 확인할 수 없습니다.")
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise ProviderError("INVALID_RESPONSE", "공급자 숫자 형식을 확인할 수 없습니다.") from None
    if not parsed.is_finite():
        raise ProviderError("INVALID_RESPONSE", "공급자 숫자 형식을 확인할 수 없습니다.")
    return text.lstrip("+")


def _provider_payload_error(payload: dict[str, Any], *, provider: str) -> None:
    status = str(payload.get("status", payload.get("rt_cd", "")))
    if provider == "kis":
        message_code = str(payload.get("msg_cd", payload.get("error_code", "")))
        if status in {"", "0", "000"} and not message_code.startswith("EGW"):
            return
        if message_code == "EGW00201":
            raise ProviderError("RATE_LIMITED", "공급자 호출 제한에 도달했습니다.", retryable=True)
        if message_code.startswith("EGW001") or message_code in {"EGW00204", "EGW00206"}:
            raise ProviderError("AUTHENTICATION_FAILED", "공급자 인증을 확인할 수 없습니다.")
    else:
        if status in {"", "0", "000"}:
            return
        if status in {"010", "011", "012", "901"}:
            raise ProviderError("AUTHENTICATION_FAILED", "공급자 인증을 확인할 수 없습니다.")
        if status == "020":
            raise ProviderError("RATE_LIMITED", "공급자 호출 제한에 도달했습니다.", retryable=True)
        if status == "800":
            raise ProviderError("PROVIDER_UNAVAILABLE", "공급자가 현재 자료를 제공하지 않습니다.", retryable=True)
        if status in {"013", "014"}:
            raise ProviderError("NO_DATA", "조회 가능한 공급자 자료가 없습니다.")
    raise ProviderError("PROVIDER_REJECTED", "공급자가 자료 요청을 완료하지 못했습니다.")


class KisQuoteProvider:
    """KIS domestic quote-only REST adapter with an in-memory OAuth token."""

    code = "kis"
    quote_path = "/uapi/domestic-stock/v1/quotations/inquire-price"
    token_path = "/oauth2/tokenP"

    def __init__(
        self,
        credentials: CredentialStore,
        *,
        environment: str = "paper",
        client: SafeHttpsClient | None = None,
        clock: Callable[[], datetime] = _now_utc,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if environment not in {"live", "paper"}:
            raise DomainValidationError("KIS 환경은 live 또는 paper여야 합니다.")
        self.credentials = credentials
        self.environment = environment
        self.client = client or SafeHttpsClient()
        self.clock = clock
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.host = (
            "openapi.koreainvestment.com"
            if environment == "live"
            else "openapivts.koreainvestment.com"
        )
        self.port = 9443 if environment == "live" else 29443
        self.base_url = f"https://{self.host}:{self.port}"
        self._access_token: str | None = None
        self._token_valid_until: datetime | None = None
        self._last_request_at: float | None = None

    def _pace_request(self) -> None:
        """KIS 공지 한도보다 보수적으로 요청 간격을 둔다."""

        interval = 1.05 if self.environment == "paper" else 0.06
        now = self.monotonic()
        if self._last_request_at is not None:
            remaining = interval - (now - self._last_request_at)
            if remaining > 0:
                self.sleeper(remaining)
                now = self.monotonic()
        self._last_request_at = max(
            now,
            (self._last_request_at + interval) if self._last_request_at is not None else now,
        )

    def _secrets(self) -> tuple[str, str] | None:
        provider = f"kis_{self.environment}"
        app_key = _credential(self.credentials, provider, "app_key")
        app_secret = _credential(self.credentials, provider, "app_secret")
        if not app_key or not app_secret:
            return None
        for value in (app_key, app_secret):
            if not 8 <= len(value) <= 512 or any(ord(char) < 33 or ord(char) > 126 for char in value):
                return None
        return app_key, app_secret

    def _token(self, app_key: str, app_secret: str) -> str:
        now = self.clock()
        if (
            self._access_token is not None
            and self._token_valid_until is not None
            and now < self._token_valid_until
        ):
            return self._access_token
        payload = self.client.request_json(
            url=f"{self.base_url}{self.token_path}",
            allowed_host=self.host,
            method="POST",
            body={
                "grant_type": "client_credentials",
                "appkey": app_key,
                "appsecret": app_secret,
            },
            before_attempt=self._pace_request,
        )
        if not isinstance(payload, dict):
            raise ProviderError("INVALID_RESPONSE", "KIS 인증 응답 형식을 확인할 수 없습니다.")
        _provider_payload_error(payload, provider="kis")
        token = payload.get("access_token")
        if (
            not isinstance(token, str)
            or not 16 <= len(token) <= 8192
            or any(ord(character) < 33 or ord(character) > 126 for character in token)
        ):
            raise ProviderError("INVALID_RESPONSE", "KIS 인증 응답 형식을 확인할 수 없습니다.")
        try:
            lifetime = int(payload.get("expires_in", 86400))
        except (TypeError, ValueError, OverflowError):
            raise ProviderError("INVALID_RESPONSE", "KIS 인증 응답 형식을 확인할 수 없습니다.") from None
        if not 60 <= lifetime <= 172800:
            raise ProviderError("INVALID_RESPONSE", "KIS 인증 응답 형식을 확인할 수 없습니다.")
        self._access_token = token
        self._token_valid_until = now + timedelta(seconds=max(1, lifetime - 30))
        return token

    def collect_quotes(
        self,
        symbols: Iterable[str],
        *,
        market_code: str = "UN",
    ) -> CollectionResult:
        started = self.clock()
        symbol_list = tuple(symbols)
        if not symbol_list:
            raise DomainValidationError("KIS 조회 종목이 하나 이상 필요합니다.")
        if len(symbol_list) > 200:
            raise DomainValidationError("한 번의 KIS 수집 종목 수가 제한을 넘었습니다.")
        if market_code not in _KIS_MARKETS:
            raise DomainValidationError("지원하지 않는 KIS 국내 시장 구분입니다.")
        for symbol in symbol_list:
            if not isinstance(symbol, str) or _DOMESTIC_SYMBOL.fullmatch(symbol) is None:
                raise DomainValidationError("KIS 국내 종목코드는 6자리 숫자여야 합니다.")

        secrets = self._secrets()
        if secrets is None:
            return CollectionResult(
                self.code,
                started,
                self.clock(),
                CollectionStatus.ERROR,
                issues=(_issue("CONFIGURATION_REQUIRED", "KIS 시세 연결 설정이 필요합니다."),),
            )
        app_key, app_secret = secrets
        records: list[NormalizedRecord] = []
        issues: list[CollectionIssue] = []
        try:
            token = self._token(app_key, app_secret)
        except (ProviderError, KeyError, TypeError, ValueError) as error:
            return CollectionResult(
                self.code,
                started,
                self.clock(),
                CollectionStatus.ERROR,
                issues=(_issue(_error_code(error), "KIS 시세 인증을 완료하지 못했습니다."),),
            )

        for symbol in symbol_list:
            try:
                query = urllib.parse.urlencode(
                    {"FID_COND_MRKT_DIV_CODE": market_code, "FID_INPUT_ISCD": symbol}
                )
                payload = self.client.request_json(
                    url=f"{self.base_url}{self.quote_path}?{query}",
                    allowed_host=self.host,
                    headers={
                        "authorization": f"Bearer {token}",
                        "appkey": app_key,
                        "appsecret": app_secret,
                        "tr_id": "FHKST01010100",
                        "custtype": "P",
                    },
                    before_attempt=self._pace_request,
                )
                if not isinstance(payload, dict):
                    raise ProviderError("INVALID_RESPONSE", "KIS 시세 응답 형식을 확인할 수 없습니다.")
                _provider_payload_error(payload, provider="kis")
                output = payload.get("output")
                if not isinstance(output, dict):
                    raise ProviderError("INVALID_RESPONSE", "KIS 시세 응답 형식을 확인할 수 없습니다.")
                collected = self.clock()
                values = (
                    ("price", "stck_prpr", "KRW"),
                    ("volume", "acml_vol", "SHARES"),
                    ("trading_value", "acml_tr_pbmn", "KRW"),
                )
                found = 0
                for suffix, field, unit in values:
                    value = _decimal_text(output.get(field))
                    if value is None:
                        continue
                    parsed_value = Decimal(value)
                    if parsed_value < 0 or parsed_value != parsed_value.to_integral_value():
                        raise ProviderError("INVALID_RESPONSE", "KIS 시세 숫자 형식을 확인할 수 없습니다.")
                    found += 1
                    records.append(
                        NormalizedRecord(
                            provider=self.code,
                            kind=RecordKind.PRICE_VOLUME,
                            record_id=f"{self.environment}:{market_code}:{symbol}:{collected.isoformat()}:{suffix}",
                            instrument_id=symbol,
                            market=_KIS_MARKETS[market_code],
                            currency="KRW" if unit == "KRW" else None,
                            effective_at=collected,
                            published_at=collected,
                            collected_at=collected,
                            unit=unit,
                            value=value,
                            source_url=f"{self.base_url}{self.quote_path}",
                            metadata={
                                "metric": suffix,
                                "environment": self.environment,
                                "market_code": market_code,
                                "time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                                "adjustment": "NOT_APPLICABLE_CURRENT_QUOTE",
                            },
                        )
                    )
                if found != len(values):
                    issues.append(_issue("MISSING_FIELD", "KIS 시세 일부 항목이 미입력입니다."))
            except (ProviderError, DomainValidationError, KeyError, TypeError, ValueError) as error:
                issues.append(_issue(_error_code(error), "해당 KIS 종목 시세를 갱신하지 못했습니다."))
        return _result(self.code, started, records, issues, self.clock)


class OpenDartProvider:
    """OpenDART disclosure-list and full-financial-statement adapter."""

    code = "opendart"
    host = "opendart.fss.or.kr"
    base_url = "https://opendart.fss.or.kr/api"

    def __init__(
        self,
        credentials: CredentialStore,
        *,
        client: SafeHttpsClient | None = None,
        clock: Callable[[], datetime] = _now_utc,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials
        self.client = client or SafeHttpsClient()
        self.clock = clock
        self.monotonic = monotonic
        self.sleeper = sleeper
        self._last_request_at: float | None = None

    def _pace_request(self) -> None:
        # 분당 1,000회 과도 접속 기준보다 보수적으로 약 857회/분 이하.
        now = self.monotonic()
        if self._last_request_at is not None:
            remaining = 0.07 - (now - self._last_request_at)
            if remaining > 0:
                self.sleeper(remaining)
                now = self.monotonic()
        self._last_request_at = now

    def _api_key(self) -> str | None:
        key = _credential(self.credentials, self.code, "api_key")
        if key is None or re.fullmatch(r"[A-Za-z0-9]{40}", key) is None:
            return None
        return key

    @staticmethod
    def _corp_codes(corp_codes: Iterable[str]) -> tuple[str, ...]:
        values = tuple(corp_codes)
        if not values or len(values) > 100:
            raise DomainValidationError("OpenDART 고유번호는 1~100개여야 합니다.")
        if any(not isinstance(value, str) or _CORP_CODE.fullmatch(value) is None for value in values):
            raise DomainValidationError("OpenDART 고유번호는 8자리 숫자여야 합니다.")
        return values

    def _configuration_error(self, started: datetime) -> CollectionResult:
        return CollectionResult(
            self.code,
            started,
            self.clock(),
            CollectionStatus.ERROR,
            issues=(_issue("CONFIGURATION_REQUIRED", "OpenDART 연결 설정이 필요합니다."),),
        )

    def collect_disclosures(
        self,
        corp_codes: Iterable[str],
        *,
        begin_date: str,
        end_date: str,
    ) -> CollectionResult:
        started = self.clock()
        corps = self._corp_codes(corp_codes)
        begin = _validate_date_input(begin_date, "공시 검색 시작일")
        end = _validate_date_input(end_date, "공시 검색 종료일")
        if begin > end:
            raise DomainValidationError("공시 검색 시작일은 종료일보다 늦을 수 없습니다.")
        api_key = self._api_key()
        if api_key is None:
            return self._configuration_error(started)
        records: list[NormalizedRecord] = []
        issues: list[CollectionIssue] = []
        endpoint = f"{self.base_url}/list.json"
        for corp_code in corps:
            try:
                page = 1
                while page <= 100:
                    query = urllib.parse.urlencode(
                        {
                            "crtfc_key": api_key,
                            "corp_code": corp_code,
                            "bgn_de": begin,
                            "end_de": end,
                            "page_no": page,
                            "page_count": 100,
                        }
                    )
                    payload = self.client.request_json(
                        url=f"{endpoint}?{query}",
                        allowed_host=self.host,
                        before_attempt=self._pace_request,
                    )
                    if not isinstance(payload, dict):
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 응답 형식을 확인할 수 없습니다.")
                    _provider_payload_error(payload, provider=self.code)
                    items = payload.get("list")
                    if not isinstance(items, list):
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 응답 형식을 확인할 수 없습니다.")
                    collected = self.clock()
                    for item in items:
                        if not isinstance(item, dict):
                            raise ProviderError("INVALID_RESPONSE", "OpenDART 응답 형식을 확인할 수 없습니다.")
                        if str(item.get("corp_code", "")) != corp_code:
                            raise ProviderError(
                                "INVALID_RESPONSE", "OpenDART 회사 식별자를 확인할 수 없습니다."
                            )
                        receipt = str(item.get("rcept_no", ""))
                        receipt_date = str(item.get("rcept_dt", ""))
                        if _RECEIPT_NO.fullmatch(receipt) is None:
                            raise ProviderError("INVALID_RESPONSE", "OpenDART 접수번호를 확인할 수 없습니다.")
                        if receipt[:8] != receipt_date:
                            raise ProviderError("INVALID_RESPONSE", "OpenDART 접수일을 확인할 수 없습니다.")
                        effective = _parse_date(receipt_date)
                        if effective.date() > collected.astimezone(SEOUL).date():
                            raise ProviderError("INVALID_RESPONSE", "OpenDART 접수일을 확인할 수 없습니다.")
                        report_name = _safe_external_text(item.get("report_nm"), 240)
                        remark = _safe_external_text(item.get("rm"), 120)
                        records.append(
                            NormalizedRecord(
                                provider=self.code,
                                kind=RecordKind.DISCLOSURE,
                                record_id=receipt,
                                instrument_id=corp_code,
                                market="KR",
                                currency=None,
                                effective_at=effective,
                                published_at=collected,
                                collected_at=collected,
                                unit="FILING",
                                revised="정정" in report_name or "정정" in remark,
                                source_url=endpoint,
                                metadata={
                                    "reception_number": receipt,
                                    "reception_date": receipt_date,
                                    "report_name": report_name,
                                    "corporation_name": _safe_external_text(item.get("corp_name"), 160),
                                    "stock_code": _safe_external_text(item.get("stock_code"), 12),
                                    "corporation_class": _safe_external_text(item.get("corp_cls"), 8),
                                    "filer_name": _safe_external_text(item.get("flr_nm"), 160),
                                    "remark": remark,
                                    "publication_time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                                },
                            )
                        )
                    try:
                        total_pages = int(payload.get("total_page", 1))
                    except (TypeError, ValueError, OverflowError):
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 페이지 정보를 확인할 수 없습니다.") from None
                    if not 1 <= total_pages <= 100:
                        raise ProviderError("RESPONSE_LIMIT", "OpenDART 응답 페이지 제한을 넘었습니다.")
                    if page >= total_pages:
                        break
                    page += 1
                else:
                    raise ProviderError("RESPONSE_LIMIT", "OpenDART 응답 페이지 제한을 넘었습니다.")
            except (ProviderError, DomainValidationError, KeyError, TypeError, ValueError) as error:
                issues.append(_issue(_error_code(error), "해당 OpenDART 공시를 갱신하지 못했습니다."))
        return _result(self.code, started, records, issues, self.clock)

    def collect_full_financials(
        self,
        corp_codes: Iterable[str],
        *,
        business_year: str,
        report_code: str,
        financial_division: str,
    ) -> CollectionResult:
        started = self.clock()
        corps = self._corp_codes(corp_codes)
        if re.fullmatch(r"20\d{2}", business_year) is None:
            raise DomainValidationError("OpenDART 사업연도 형식이 올바르지 않습니다.")
        if report_code not in _REPORT_CODES:
            raise DomainValidationError("지원하지 않는 OpenDART 보고서 코드입니다.")
        if financial_division not in _FINANCIAL_DIVISIONS:
            raise DomainValidationError("재무제표 구분은 CFS 또는 OFS여야 합니다.")
        api_key = self._api_key()
        if api_key is None:
            return self._configuration_error(started)
        records: list[NormalizedRecord] = []
        issues: list[CollectionIssue] = []
        endpoint = f"{self.base_url}/fnlttSinglAcntAll.json"
        period_end = {
            "11013": (3, 31),
            "11012": (6, 30),
            "11014": (9, 30),
            "11011": (12, 31),
        }[report_code]
        effective = datetime(int(business_year), *period_end, tzinfo=SEOUL)
        for corp_code in corps:
            try:
                query = urllib.parse.urlencode(
                    {
                        "crtfc_key": api_key,
                        "corp_code": corp_code,
                        "bsns_year": business_year,
                        "reprt_code": report_code,
                        "fs_div": financial_division,
                    }
                )
                payload = self.client.request_json(
                    url=f"{endpoint}?{query}",
                    allowed_host=self.host,
                    before_attempt=self._pace_request,
                )
                if not isinstance(payload, dict):
                    raise ProviderError("INVALID_RESPONSE", "OpenDART 응답 형식을 확인할 수 없습니다.")
                _provider_payload_error(payload, provider=self.code)
                items = payload.get("list")
                if not isinstance(items, list):
                    raise ProviderError("INVALID_RESPONSE", "OpenDART 응답 형식을 확인할 수 없습니다.")
                collected = self.clock()
                if effective.date() > collected.astimezone(SEOUL).date():
                    raise ProviderError("INVALID_RESPONSE", "OpenDART 재무 기준일을 확인할 수 없습니다.")
                for item in items:
                    if not isinstance(item, dict):
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 응답 형식을 확인할 수 없습니다.")
                    if (
                        str(item.get("corp_code", "")) != corp_code
                        or str(item.get("bsns_year", "")) != business_year
                        or str(item.get("reprt_code", "")) != report_code
                    ):
                        raise ProviderError(
                            "INVALID_RESPONSE", "OpenDART 재무자료 식별자를 확인할 수 없습니다."
                        )
                    receipt = str(item.get("rcept_no", ""))
                    if _RECEIPT_NO.fullmatch(receipt) is None:
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 접수번호를 확인할 수 없습니다.")
                    receipt_time = _parse_date(receipt[:8])
                    if receipt_time.date() > collected.astimezone(SEOUL).date():
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 접수일을 확인할 수 없습니다.")
                    response_fs = str(item.get("fs_div", financial_division))
                    if response_fs not in _FINANCIAL_DIVISIONS or response_fs != financial_division:
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 재무제표 구분을 확인할 수 없습니다.")
                    currency = str(item.get("currency", "")).upper()
                    if _CURRENCY.fullmatch(currency) is None:
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 재무 통화를 확인할 수 없습니다.")
                    statement = _safe_external_text(item.get("sj_div"), 8)
                    account_id = _safe_external_text(item.get("account_id"), 200)
                    account_name = _safe_external_text(item.get("account_nm"), 240)
                    if not statement or not account_id or not account_name:
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 재무 계정을 확인할 수 없습니다.")
                    order = str(item.get("ord", ""))
                    if re.fullmatch(r"\d{1,8}", order) is None:
                        raise ProviderError("INVALID_RESPONSE", "OpenDART 재무 항목 순서를 확인할 수 없습니다.")
                    for amount_field, amount_basis in (
                        ("thstrm_amount", "CURRENT_OR_QUARTER"),
                        ("thstrm_add_amount", "CUMULATIVE"),
                    ):
                        amount = _decimal_text(item.get(amount_field))
                        if amount is None:
                            continue
                        records.append(
                            NormalizedRecord(
                                provider=self.code,
                                kind=RecordKind.FINANCIAL,
                                record_id=(
                                    f"{receipt}:{response_fs}:{statement}:{account_id}:"
                                    f"{amount_field}:{order}"
                                ),
                                instrument_id=corp_code,
                                market="KR",
                                currency=currency,
                                effective_at=effective,
                                published_at=collected,
                                collected_at=collected,
                                unit=currency,
                                value=amount,
                                source_url=endpoint,
                                metadata={
                                    "reception_number": receipt,
                                    "reception_date": receipt[:8],
                                    "business_year": business_year,
                                    "report_code": report_code,
                                    "financial_division": response_fs,
                                    "statement_division": statement,
                                    "statement_name": _safe_external_text(item.get("sj_nm"), 160),
                                    "account_id": account_id,
                                    "account_name": account_name,
                                    "account_detail": _safe_external_text(item.get("account_detail"), 300),
                                    "amount_basis": amount_basis,
                                    "period_label": _safe_external_text(item.get("thstrm_nm"), 100),
                                    "publication_time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                                },
                            )
                        )
            except (ProviderError, DomainValidationError, KeyError, TypeError, ValueError) as error:
                issues.append(_issue(_error_code(error), "해당 OpenDART 재무자료를 갱신하지 못했습니다."))
        return _result(self.code, started, records, issues, self.clock)


@dataclass(frozen=True)
class EcosSeriesRequest:
    statistic_code: str
    cycle: str
    start_period: str
    end_period: str
    item_code1: str
    item_code2: str = "?"
    item_code3: str = "?"
    item_code4: str = "?"

    def __post_init__(self) -> None:
        if _ECOS_CODE.fullmatch(self.statistic_code) is None:
            raise DomainValidationError("ECOS 통계표 코드 형식이 올바르지 않습니다.")
        if self.cycle not in _ECOS_CYCLES:
            raise DomainValidationError("지원하지 않는 ECOS 주기입니다.")
        if not self.start_period or not self.end_period or self.start_period > self.end_period:
            raise DomainValidationError("ECOS 조회 기간이 올바르지 않습니다.")
        for item in (self.item_code1, self.item_code2, self.item_code3, self.item_code4):
            if item != "?" and _ECOS_ITEM.fullmatch(item) is None:
                raise DomainValidationError("ECOS 통계항목 코드 형식이 올바르지 않습니다.")
        _ecos_effective_time(self.start_period, self.cycle, input_error=True)
        _ecos_effective_time(self.end_period, self.cycle, input_error=True)


@dataclass(frozen=True)
class ConnectivityDemoResult:
    provider: str
    ok: bool
    checked_at: datetime
    observed_rows: int
    message: str
    persistable: bool = False


def _ecos_effective_time(label: str, cycle: str, *, input_error: bool = False) -> datetime:
    try:
        if cycle == "A" and re.fullmatch(r"\d{4}", label):
            return datetime(int(label), 12, 31, tzinfo=SEOUL)
        if cycle == "S" and re.fullmatch(r"\d{4}S[12]", label):
            year, half = int(label[:4]), int(label[-1])
            return datetime(year, 6 if half == 1 else 12, 30 if half == 1 else 31, tzinfo=SEOUL)
        if cycle == "Q" and re.fullmatch(r"\d{4}Q[1-4]", label):
            year, quarter = int(label[:4]), int(label[-1])
            month = quarter * 3
            return datetime(year, month, calendar.monthrange(year, month)[1], tzinfo=SEOUL)
        if cycle == "M" and re.fullmatch(r"\d{6}", label):
            year, month = int(label[:4]), int(label[4:])
            return datetime(year, month, calendar.monthrange(year, month)[1], tzinfo=SEOUL)
        if cycle == "SM" and re.fullmatch(r"\d{6}S[12]", label):
            year, month, half = int(label[:4]), int(label[4:6]), int(label[-1])
            day = 15 if half == 1 else calendar.monthrange(year, month)[1]
            return datetime(year, month, day, tzinfo=SEOUL)
        if cycle == "D" and re.fullmatch(r"\d{8}", label):
            return _parse_date(label)
    except (ValueError, OverflowError, ProviderError):
        pass
    if input_error:
        raise DomainValidationError("ECOS 기간 표기가 선택한 주기와 맞지 않습니다.")
    raise ProviderError("INVALID_RESPONSE", "ECOS 응답 기간을 확인할 수 없습니다.")


class EcosProvider:
    code = "ecos"
    host = "ecos.bok.or.kr"
    base_url = "https://ecos.bok.or.kr/api"

    def __init__(
        self,
        credentials: CredentialStore,
        *,
        client: SafeHttpsClient | None = None,
        clock: Callable[[], datetime] = _now_utc,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        max_logical_calls_per_run: int = ECOS_MAX_LOGICAL_CALLS_PER_RUN,
    ) -> None:
        if not 1 <= max_logical_calls_per_run <= ECOS_MAX_LOGICAL_CALLS_PER_RUN:
            raise DomainValidationError("ECOS 실행당 호출 상한이 허용 범위를 벗어났습니다.")
        self.credentials = credentials
        self.client = client or SafeHttpsClient()
        self.clock = clock
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.max_logical_calls_per_run = max_logical_calls_per_run
        self._last_request_at: float | None = None

    def _pace_request(self) -> None:
        """공식 정량 한도 확인 전 보수적인 저속 호출 간격을 적용한다."""

        now = self.monotonic()
        if self._last_request_at is not None:
            remaining = ECOS_MIN_REQUEST_INTERVAL_SECONDS - (now - self._last_request_at)
            if remaining > 0:
                self.sleeper(remaining)
                now = self.monotonic()
        self._last_request_at = now

    def _api_key(self) -> str | None:
        key = _credential(self.credentials, self.code, "api_key")
        if key is None or key.lower() == "sample" or re.fullmatch(r"[A-Za-z0-9]{8,64}", key) is None:
            return None
        return key

    def operationally_configured(self) -> bool:
        """운영 호출 가능한 키가 있는지만 반환하며 값을 노출하지 않는다."""

        return self._api_key() is not None

    @staticmethod
    def _url(key: str, request: EcosSeriesRequest, *, start_row: int = 1, end_row: int = 1000) -> str:
        fixed = [
            "StatisticSearch",
            key,
            "json",
            "kr",
            str(start_row),
            str(end_row),
            request.statistic_code,
            request.cycle,
            request.start_period,
            request.end_period,
            request.item_code1,
            request.item_code2,
            request.item_code3,
            request.item_code4,
        ]
        encoded = (urllib.parse.quote(part, safe="") for part in fixed)
        return f"{EcosProvider.base_url}/{'/'.join(encoded)}"

    @staticmethod
    def _page(payload: Any) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(payload, dict):
            raise ProviderError("INVALID_RESPONSE", "ECOS 응답 형식을 확인할 수 없습니다.")
        if "RESULT" in payload:
            result = payload.get("RESULT")
            code = str(result.get("CODE", "")) if isinstance(result, dict) else ""
            if code == "INFO-100":
                raise ProviderError("AUTHENTICATION_FAILED", "ECOS 인증을 확인할 수 없습니다.")
            if code == "INFO-200":
                raise ProviderError("NO_DATA", "조회 가능한 ECOS 자료가 없습니다.")
            if code == "ERROR-602":
                raise ProviderError("RATE_LIMITED", "공급자 호출 제한에 도달했습니다.", retryable=True)
            if code in {"ERROR-500", "ERROR-600", "ERROR-601"}:
                raise ProviderError(
                    "PROVIDER_UNAVAILABLE", "공급자가 현재 자료를 제공하지 않습니다.", retryable=True
                )
            raise ProviderError("PROVIDER_REJECTED", "ECOS가 자료 요청을 완료하지 못했습니다.")
        root = payload.get("StatisticSearch")
        rows = root.get("row") if isinstance(root, dict) else None
        if not isinstance(rows, list):
            raise ProviderError("INVALID_RESPONSE", "ECOS 응답 형식을 확인할 수 없습니다.")
        if not all(isinstance(row, dict) for row in rows):
            raise ProviderError("INVALID_RESPONSE", "ECOS 응답 형식을 확인할 수 없습니다.")
        try:
            total_count = int(root.get("list_total_count"))
        except (TypeError, ValueError, OverflowError):
            raise ProviderError("INVALID_RESPONSE", "ECOS 응답 건수를 확인할 수 없습니다.") from None
        if total_count < len(rows) or total_count > 100_000:
            raise ProviderError("RESPONSE_LIMIT", "ECOS 응답 건수 제한을 넘었습니다.")
        if total_count == 0:
            raise ProviderError("NO_DATA", "조회 가능한 ECOS 자료가 없습니다.")
        return rows, total_count

    @classmethod
    def _rows(cls, payload: Any) -> list[dict[str, Any]]:
        return cls._page(payload)[0]

    def collect_statistics(self, requests: Iterable[EcosSeriesRequest]) -> CollectionResult:
        started = self.clock()
        request_list = tuple(requests)
        if not request_list or len(request_list) > 100:
            raise DomainValidationError("ECOS 시계열 요청은 1~100개여야 합니다.")
        if not all(isinstance(request, EcosSeriesRequest) for request in request_list):
            raise DomainValidationError("ECOS 시계열 요청 형식이 올바르지 않습니다.")
        api_key = self._api_key()
        if api_key is None:
            return CollectionResult(
                self.code,
                started,
                self.clock(),
                CollectionStatus.ERROR,
                issues=(_issue("CONFIGURATION_REQUIRED", "ECOS 연결 설정이 필요합니다."),),
            )
        records: list[NormalizedRecord] = []
        issues: list[CollectionIssue] = []
        logical_calls = 0
        for request in request_list:
            call_limit_reached = False
            try:
                first_row = 1
                expected_total: int | None = None
                while expected_total is None or first_row <= expected_total:
                    if logical_calls >= self.max_logical_calls_per_run:
                        call_limit_reached = True
                        raise ProviderError(
                            "RUN_CALL_LIMIT",
                            "ECOS 실행당 안전 호출 상한에 도달했습니다.",
                        )
                    last_row = first_row + 999
                    if expected_total is not None:
                        last_row = min(last_row, expected_total)
                    logical_calls += 1
                    payload = self.client.request_json(
                        url=self._url(api_key, request, start_row=first_row, end_row=last_row),
                        allowed_host=self.host,
                        before_attempt=self._pace_request,
                    )
                    rows, reported_total = self._page(payload)
                    if expected_total is None:
                        expected_total = reported_total
                    elif reported_total != expected_total:
                        raise ProviderError("INVALID_RESPONSE", "ECOS 응답 건수가 일관되지 않습니다.")
                    expected_rows = min(1000, expected_total - first_row + 1)
                    if len(rows) != expected_rows:
                        raise ProviderError("INVALID_RESPONSE", "ECOS 응답 페이지를 확인할 수 없습니다.")
                    collected = self.clock()
                    for row in rows:
                        stat_code = str(row.get("STAT_CODE", ""))
                        time_label = str(row.get("TIME", ""))
                        if stat_code != request.statistic_code:
                            raise ProviderError("INVALID_RESPONSE", "ECOS 통계표 코드를 확인할 수 없습니다.")
                        if not request.start_period <= time_label <= request.end_period:
                            raise ProviderError("INVALID_RESPONSE", "ECOS 응답 기간을 확인할 수 없습니다.")
                        value = _decimal_text(row.get("DATA_VALUE"))
                        if value is None:
                            issues.append(_issue("MISSING_VALUE", "ECOS 값 일부가 미입력입니다."))
                            continue
                        effective = _ecos_effective_time(time_label, request.cycle)
                        item_codes = tuple(
                            "" if row.get(f"ITEM_CODE{index}") is None else str(row.get(f"ITEM_CODE{index}"))
                            for index in range(1, 5)
                        )
                        requested_items = (
                            request.item_code1,
                            request.item_code2,
                            request.item_code3,
                            request.item_code4,
                        )
                        if any(
                            (actual and _ECOS_ITEM.fullmatch(actual) is None)
                            or (expected != "?" and actual != expected)
                            for actual, expected in zip(item_codes, requested_items, strict=True)
                        ):
                            raise ProviderError("INVALID_RESPONSE", "ECOS 통계항목 코드를 확인할 수 없습니다.")
                        record_item = "/".join(code for code in item_codes if code)
                        if not record_item:
                            raise ProviderError("INVALID_RESPONSE", "ECOS 통계항목 코드를 확인할 수 없습니다.")
                        unit = _safe_external_text(row.get("UNIT_NAME"), 80)
                        if not unit:
                            raise ProviderError("INVALID_RESPONSE", "ECOS 단위를 확인할 수 없습니다.")
                        records.append(
                            NormalizedRecord(
                                provider=self.code,
                                kind=RecordKind.MACRO,
                                record_id=f"{stat_code}:{record_item}:{time_label}",
                                instrument_id=f"{stat_code}:{record_item}",
                                market="KR_MACRO",
                                currency=None,
                                effective_at=effective,
                                published_at=collected,
                                collected_at=collected,
                                unit=unit,
                                value=value,
                                revised=False,
                                source_url=f"{self.base_url}/StatisticSearch/",
                                metadata={
                                    "statistic_name": _safe_external_text(row.get("STAT_NAME"), 240),
                                    "item_names": [
                                        _safe_external_text(row.get(f"ITEM_NAME{index}"), 160)
                                        for index in range(1, 5)
                                    ],
                                    "item_codes": list(item_codes),
                                    "period": time_label,
                                    "cycle": request.cycle,
                                    "weight": _safe_external_text(row.get("WGT"), 80),
                                    "publication_time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                                    "revision_flag_available": False,
                                },
                            )
                        )
                    first_row += len(rows)
            except (ProviderError, DomainValidationError, KeyError, TypeError, ValueError) as error:
                issues.append(_issue(_error_code(error), "해당 ECOS 시계열을 갱신하지 못했습니다."))
            if call_limit_reached:
                break
        return _result(self.code, started, records, issues, self.clock)

    def connectivity_demo(self) -> ConnectivityDemoResult:
        """Call the official sample query without returning storable observations."""

        checked = self.clock()
        request = EcosSeriesRequest("200Y101", "A", "2020", "2023", "10101")
        try:
            rows = self._rows(
                self.client.request_json(
                    url=self._url("sample", request, start_row=1, end_row=10),
                    allowed_host=self.host,
                )
            )
            return ConnectivityDemoResult(
                self.code,
                True,
                checked,
                len(rows),
                "ECOS 공식 예제 연결을 확인했습니다. 운영 자료로 저장하지 않습니다.",
            )
        except (ProviderError, KeyError, TypeError, ValueError):
            return ConnectivityDemoResult(
                self.code,
                False,
                checked,
                0,
                "ECOS 공식 예제 연결을 확인하지 못했습니다.",
            )
