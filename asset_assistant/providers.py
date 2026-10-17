"""공식 읽기 전용 자료 공급자와 제한된 HTTPS 클라이언트.

주문, 계좌 조회, 브로커 잔고 기능은 의도적으로 존재하지 않는다. 외부 응답은
데이터로만 다루며 프로그램 설정이나 권한을 바꿀 수 없다.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Callable, Protocol
from xml.etree import ElementTree

from .credential_store import CredentialStore, CredentialStoreError, valid_sec_user_agent
from .domain import DomainValidationError
from .market_data import (
    CollectionIssue,
    CollectionResult,
    CollectionStatus,
    NormalizedRecord,
    RecordKind,
)


MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_JSON_INTEGER_DIGITS = 100
MAX_JSON_DECIMAL_CHARACTERS = 100
MAX_JSON_DECIMAL_EXPONENT = 100
_SERIES_ID = re.compile(r"[A-Z0-9_#-]{1,50}\Z")
_CIK = re.compile(r"\d{10}\Z")
_SEC_CONCEPT = re.compile(r"[A-Za-z][A-Za-z0-9]{0,99}\Z")
_CORP_CODE = re.compile(r"\d{8}\Z")
_KIS_DOMESTIC_CODE = re.compile(r"[A-Z0-9]{6,12}\Z")
_ECOS_CODE = re.compile(r"[A-Za-z0-9_.-]{1,40}\Z")
_BLS_UNITS = {
    "CUUR0000SA0": "INDEX_1982_84_100",
    "LNS14000000": "PERCENT",
}


class ProviderState(str, Enum):
    AVAILABLE = "AVAILABLE"
    NEEDS_CONFIGURATION = "NEEDS_CONFIGURATION"
    RIGHTS_NOT_VERIFIED = "RIGHTS_NOT_VERIFIED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True)
class ProviderDescriptor:
    code: str
    name: str
    capabilities: tuple[RecordKind, ...]
    state: ProviderState
    authentication: str
    markets: str
    official_url: str
    checked_on: str
    notes: str


PROVIDER_DESCRIPTORS: tuple[ProviderDescriptor, ...] = (
    ProviderDescriptor(
        "bls",
        "미국 노동통계국 BLS Public Data API",
        (RecordKind.MACRO,),
        ProviderState.AVAILABLE,
        "v2 URL의 v1 호환 다중 시계열 요청은 미등록 한도; v2 확장 한도는 등록키 필요",
        "미국 경제지표",
        "https://www.bls.gov/developers/home.htm",
        "2026-10-11",
        "공표자료만 조회하며 공식 안내상 API 반영이 발표보다 하루 늦을 수 있습니다.",
    ),
    ProviderDescriptor(
        "sec",
        "미국 SEC EDGAR",
        (RecordKind.DISCLOSURE, RecordKind.FINANCIAL),
        ProviderState.NEEDS_CONFIGURATION,
        "API 키 없음; ASCII 조직명과 연락 이메일 형식 User-Agent를 로컬 보안 저장소에 등록",
        "미국 상장사 공시/XBRL",
        "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        "2026-10-11",
        "SEC 공정접근 정책에 따라 전체 장비 합산 10회/초 이하로 사용합니다.",
    ),
    ProviderDescriptor(
        "opendart",
        "금융감독원 OpenDART",
        (RecordKind.DISCLOSURE, RecordKind.FINANCIAL),
        ProviderState.NEEDS_CONFIGURATION,
        "OpenDART 인증키 필요",
        "대한민국 기업 공시/재무",
        "https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001",
        "2026-10-11",
        "키와 대상 고유번호 등록 후 읽기 전용으로 호출합니다.",
    ),
    ProviderDescriptor(
        "ecos",
        "한국은행 ECOS",
        (RecordKind.MACRO,),
        ProviderState.NEEDS_CONFIGURATION,
        "ECOS 인증키 필요",
        "대한민국 공식 경제통계",
        "https://ecos.bok.or.kr/api/",
        "2026-10-11",
        "통계표·주기·단위가 확인된 대상만 조회합니다.",
    ),
    ProviderDescriptor(
        "kis",
        "한국투자증권 KIS Open API",
        (RecordKind.PRICE_VOLUME,),
        ProviderState.NEEDS_CONFIGURATION,
        "사용자 본인의 모의/실전 App Key·Secret 필요",
        "지원되는 국내 주식/ETF 시세·거래량",
        "https://apiportal.koreainvestment.com/",
        "2026-10-11",
        "시세 조회 전용이며 계좌조회·주문 인터페이스가 없습니다.",
    ),
    ProviderDescriptor(
        "fed_news",
        "미국 연방준비제도 공식 보도자료 RSS",
        (RecordKind.NEWS,),
        ProviderState.AVAILABLE,
        "등록 불필요",
        "미국 통화정책·감독·금융안정 관련 연준 보도자료",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "2026-10-11",
        "공식 RSS의 제목·링크·분류·공표시각 메타데이터만 조회하며 기사 본문은 저장하지 않습니다.",
    ),
    ProviderDescriptor(
        "news",
        "계약형 뉴스 공급자",
        (RecordKind.NEWS,),
        ProviderState.RIGHTS_NOT_VERIFIED,
        "공급자별 계약/구독 필요",
        "계약 범위에 따름",
        "https://api.ap.org/media/v/docs/Getting_Started_API.htm",
        "2026-10-11",
        "자동조회·분석·저장 권리가 확인되기 전에는 호출하거나 크롤링하지 않습니다.",
    ),
    ProviderDescriptor(
        "etf_issuer",
        "ETF 발행사/거래소 상품정보",
        (RecordKind.ETF_PRODUCT,),
        ProviderState.RIGHTS_NOT_VERIFIED,
        "공식 API 승인 또는 이용권한 확인 필요",
        "발행사·거래소별",
        "https://openapi.krx.co.kr/",
        "2026-10-11",
        "웹 공개만으로 자동수집 권리를 추정하지 않습니다.",
    ),
)


class ProviderError(DomainValidationError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class HttpResponse(Protocol):
    headers: Any

    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> "HttpResponse": ...

    def __exit__(self, *args: object) -> object: ...


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_host: str, allowed_port: int) -> None:
        super().__init__()
        self.allowed_host = allowed_host
        self.allowed_port = allowed_port

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        parsed = urllib.parse.urlsplit(newurl)
        try:
            redirect_port = parsed.port or 443
        except ValueError:
            raise urllib.error.HTTPError(newurl, code, "redirect blocked", headers, fp) from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.allowed_host
            or redirect_port != self.allowed_port
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise urllib.error.HTTPError(newurl, code, "redirect blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderError("INVALID_RESPONSE", "공급자 응답 형식을 확인할 수 없습니다.")
        result[key] = value
    return result


def _safe_json_integer(value: str) -> int:
    if len(value.lstrip("-")) > MAX_JSON_INTEGER_DIGITS:
        raise ProviderError("INVALID_RESPONSE", "공급자 응답 형식을 확인할 수 없습니다.")
    return int(value)


def _safe_json_decimal(value: str) -> str:
    """Validate a JSON decimal while preserving its exact source text.

    Returning the lexeme as text prevents SEC and other financial values from
    passing through binary ``float`` before the provider converts them to a
    :class:`~decimal.Decimal`.  Character, coefficient and exponent bounds
    also keep an untrusted response from creating an impractical decimal.
    """

    if len(value) > MAX_JSON_DECIMAL_CHARACTERS:
        raise ProviderError("INVALID_RESPONSE", "공급자 응답 형식을 확인할 수 없습니다.")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ProviderError("INVALID_RESPONSE", "공급자 응답 형식을 확인할 수 없습니다.") from None
    exponent = parsed.as_tuple().exponent
    if (
        not parsed.is_finite()
        or not isinstance(exponent, int)
        or len(parsed.as_tuple().digits) > MAX_JSON_INTEGER_DIGITS
        or abs(exponent) > MAX_JSON_DECIMAL_EXPONENT
        or abs(parsed.adjusted()) > MAX_JSON_DECIMAL_EXPONENT
    ):
        raise ProviderError("INVALID_RESPONSE", "공급자 응답 형식을 확인할 수 없습니다.")
    return value


def safe_json_loads(payload: bytes) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_int=_safe_json_integer,
            parse_float=_safe_json_decimal,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ProviderError("INVALID_RESPONSE", "공급자 응답 형식을 확인할 수 없습니다.")
            ),
        )
    except ProviderError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ProviderError("INVALID_RESPONSE", "공급자 응답 형식을 확인할 수 없습니다.") from None


class SafeHttpsClient:
    """고정 HTTPS host만 호출하고 응답/URL/인증정보를 오류에 되풀이하지 않는다."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        opener: Callable[[urllib.request.Request, float], HttpResponse] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not (0.1 <= timeout_seconds <= 60):
            raise DomainValidationError("HTTP 제한시간이 허용 범위를 벗어났습니다.")
        if not (0 <= max_retries <= 2):
            raise DomainValidationError("HTTP 재시도 횟수가 허용 범위를 벗어났습니다.")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_response_bytes = max_response_bytes
        self._opener = opener
        self._sleeper = sleeper

    @staticmethod
    def _validate_url(url: str, allowed_host: str) -> int:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != allowed_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443, 9443, 29443)
        ):
            raise ProviderError("UNSAFE_ENDPOINT", "허용되지 않은 공급자 연결 주소입니다.")
        return parsed.port or 443

    def request_bytes(
        self,
        *,
        url: str,
        allowed_host: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        encoded_body: bytes | None = None,
        accepted_content_markers: tuple[str, ...],
        before_attempt: Callable[[], None] | None = None,
    ) -> bytes:
        allowed_port = self._validate_url(url, allowed_host)
        request = urllib.request.Request(
            url, data=encoded_body, headers=headers or {}, method=method
        )
        for attempt in range(self.max_retries + 1):
            try:
                if before_attempt is not None:
                    before_attempt()
                if self._opener is None:
                    opener = urllib.request.build_opener(
                        _SameHostRedirectHandler(allowed_host, allowed_port)
                    )
                    response_context = opener.open(request, timeout=self.timeout_seconds)
                else:
                    response_context = self._opener(request, self.timeout_seconds)
                with response_context as response:
                    content_type = str(response.headers.get("Content-Type", "")).lower()
                    if not any(marker in content_type for marker in accepted_content_markers):
                        raise ProviderError(
                            "INVALID_CONTENT_TYPE", "공급자 응답 형식을 확인할 수 없습니다."
                        )
                    length = response.headers.get("Content-Length")
                    if length is not None:
                        parsed_length = int(length)
                        if parsed_length < 0 or parsed_length > self.max_response_bytes:
                            raise ProviderError(
                                "RESPONSE_TOO_LARGE", "공급자 응답 크기가 제한을 넘었습니다."
                            )
                    payload = response.read(self.max_response_bytes + 1)
                    if len(payload) > self.max_response_bytes:
                        raise ProviderError("RESPONSE_TOO_LARGE", "공급자 응답 크기가 제한을 넘었습니다.")
                    return payload
            except ProviderError:
                raise
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                if retryable and attempt < self.max_retries:
                    self._sleeper(min(2.0, 0.25 * (2**attempt)))
                    continue
                code = "RATE_LIMITED" if exc.code == 429 else "AUTHENTICATION_FAILED" if exc.code in (401, 403) else "HTTP_ERROR"
                raise ProviderError(code, "공급자 연결 요청이 거부되었습니다.", retryable=retryable) from None
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                TimeoutError,
                socket.timeout,
                OSError,
            ):
                if attempt < self.max_retries:
                    self._sleeper(min(2.0, 0.25 * (2**attempt)))
                    continue
                raise ProviderError("NETWORK_ERROR", "공급자 연결을 완료하지 못했습니다.", retryable=True) from None
            except (TypeError, ValueError, OverflowError):
                raise ProviderError("INVALID_RESPONSE", "공급자 응답 형식을 확인할 수 없습니다.") from None
        raise ProviderError("NETWORK_ERROR", "공급자 연결을 완료하지 못했습니다.")

    def request_json(
        self,
        *,
        url: str,
        allowed_host: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        before_attempt: Callable[[], None] | None = None,
    ) -> Any:
        encoded = None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        payload = self.request_bytes(
            url=url,
            allowed_host=allowed_host,
            method=method,
            headers=request_headers,
            encoded_body=encoded,
            accepted_content_markers=("json",),
            before_attempt=before_attempt,
        )
        return safe_json_loads(payload)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ym_period(year: str, period: str) -> datetime:
    if not re.fullmatch(r"\d{4}", year) or not re.fullmatch(r"M(?:0[1-9]|1[0-2])", period):
        raise ProviderError("INVALID_RESPONSE", "공급자 응답의 기준 기간을 확인할 수 없습니다.")
    return datetime(int(year), int(period[1:]), 1, tzinfo=timezone.utc)


class BlsProvider:
    code = "bls"
    host = "api.bls.gov"
    source_url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

    def __init__(
        self,
        client: SafeHttpsClient | None = None,
        *,
        credentials: CredentialStore | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client or SafeHttpsClient()
        self.credentials = credentials
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_request_at: float | None = None

    def _pace_request(self) -> None:
        # 재시도까지 같은 경로를 통과시키는 보수적 요청 간격. 일일 쿼리
        # 한도는 AutomationService의 영속 예약 카운터가 별도로 제한한다.
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = 0.21 - (now - self._last_request_at)
            if remaining > 0:
                self._sleeper(remaining)
                now = self._monotonic()
        self._last_request_at = now

    def registration_key(self) -> str | None:
        if self.credentials is None:
            return None
        try:
            value = self.credentials.get_secret("bls", "registration_key")
        except CredentialStoreError:
            return None
        if value is None or re.fullmatch(r"[A-Za-z0-9]{16,64}", value) is None:
            return None
        return value

    def daily_query_limit(self) -> int:
        return 500 if self.registration_key() is not None else 25

    def collect(self, series_ids: tuple[str, ...]) -> CollectionResult:
        started = _now_utc()
        records: list[NormalizedRecord] = []
        issues: list[CollectionIssue] = []
        if (
            not series_ids
            or len(set(series_ids)) != len(series_ids)
            or any(_SERIES_ID.fullmatch(series_id) is None for series_id in series_ids)
        ):
            raise DomainValidationError("BLS 시계열 식별자 형식이 올바르지 않습니다.")
        registration_key = self.registration_key()
        series_limit = 50 if registration_key is not None else 25
        if len(series_ids) > series_limit:
            return CollectionResult(
                self.code,
                started,
                _now_utc(),
                CollectionStatus.ERROR,
                issues=(
                    CollectionIssue(
                        "QUERY_LIMIT",
                        "BLS 요청당 시계열 제한을 넘었습니다. 등록 상태와 대상 수를 확인하세요.",
                    ),
                ),
            )
        request_body: dict[str, Any] = {"seriesid": list(series_ids)}
        if registration_key is not None:
            request_body["registrationkey"] = registration_key
        try:
            payload = self.client.request_json(
                url=self.source_url,
                allowed_host=self.host,
                method="POST",
                headers={"User-Agent": "long-term-asset-assistant/0.2"},
                body=request_body,
                before_attempt=self._pace_request,
            )
            raw_series = payload["Results"]["series"]
            if payload.get("status") != "REQUEST_SUCCEEDED" or not isinstance(raw_series, list):
                raise ProviderError("PROVIDER_REJECTED", "BLS가 자료 요청을 완료하지 못했습니다.")
            by_id: dict[str, dict[str, Any]] = {}
            for item in raw_series:
                if not isinstance(item, dict):
                    raise ProviderError("INVALID_RESPONSE", "BLS 응답 형식을 확인할 수 없습니다.")
                series_id = str(item.get("seriesID", ""))
                if series_id in by_id or series_id not in series_ids:
                    raise ProviderError("INVALID_RESPONSE", "BLS 응답 형식을 확인할 수 없습니다.")
                by_id[series_id] = item
            collected = _now_utc()
            for series_id in series_ids:
                series = by_id.get(series_id)
                if series is None:
                    issues.append(
                        CollectionIssue("MISSING_SERIES", "해당 BLS 시계열 응답이 누락되었습니다.")
                    )
                    continue
                observations = series.get("data", [])
                if not isinstance(observations, list):
                    raise ProviderError("INVALID_RESPONSE", "BLS 응답 형식을 확인할 수 없습니다.")
                missing_observation = False
                for item in observations:
                    if not isinstance(item, dict):
                        raise ProviderError("INVALID_RESPONSE", "BLS 응답 형식을 확인할 수 없습니다.")
                    period = str(item.get("period", ""))
                    if period == "M13":
                        continue
                    effective = _parse_ym_period(str(item.get("year", "")), period)
                    value = str(item.get("value", ""))
                    try:
                        numeric_value = Decimal(value)
                    except (InvalidOperation, ValueError):
                        missing_observation = True
                        continue
                    if not numeric_value.is_finite():
                        missing_observation = True
                        continue
                    footnotes = item.get("footnotes", [])
                    if not isinstance(footnotes, list):
                        raise ProviderError("INVALID_RESPONSE", "BLS 응답 형식을 확인할 수 없습니다.")
                    records.append(
                        NormalizedRecord(
                            provider=self.code,
                            kind=RecordKind.MACRO,
                            record_id=f"{series_id}:{effective.date().isoformat()}",
                            instrument_id=series_id,
                            market="US",
                            currency=None,
                            effective_at=effective,
                            published_at=collected,
                            collected_at=collected,
                            unit=_BLS_UNITS.get(series_id, "BLS_SERIES_UNIT_UNCONFIRMED"),
                            value=value,
                            revised=any(
                                str(note.get("code", "")).upper() in {"R", "C"}
                                for note in footnotes
                                if isinstance(note, dict)
                            ),
                            source_url=self.source_url,
                            metadata={
                                "period_name": str(item.get("periodName", ""))[:80],
                                "publication_time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                                "api_version": (
                                    "v2-registered"
                                    if registration_key is not None
                                    else "v1-compatible-unregistered"
                                ),
                            },
                        )
                    )
                if missing_observation:
                    issues.append(
                        CollectionIssue(
                            "MISSING_OBSERVATION",
                            "해당 BLS 시계열에 수치가 없는 관측이 있어 건너뛰었습니다.",
                        )
                    )
        except (ProviderError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            code = exc.code if isinstance(exc, ProviderError) else "INVALID_RESPONSE"
            issues.append(CollectionIssue(code, "BLS 시계열을 갱신하지 못했습니다."))
        finished = _now_utc()
        if records and issues:
            status = CollectionStatus.PARTIAL
        elif records:
            status = CollectionStatus.OK
        else:
            status = CollectionStatus.ERROR
        return CollectionResult(self.code, started, finished, status, tuple(records), tuple(issues))


def _xml_local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _rss_child_text(item: ElementTree.Element, name: str, *, limit: int) -> str:
    values = [
        "".join(child.itertext()).strip()
        for child in item
        if _xml_local_name(child.tag) == name.lower()
    ]
    text = " ".join(value for value in values if value).strip()
    if len(text) > limit:
        raise ProviderError("INVALID_RESPONSE", "연준 RSS 항목 형식을 확인할 수 없습니다.")
    return text


def _parse_rss_datetime(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        raise ProviderError("INVALID_RESPONSE", "연준 RSS 공표시각을 확인할 수 없습니다.") from None
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderError("INVALID_RESPONSE", "연준 RSS 공표시각을 확인할 수 없습니다.")
    return parsed.astimezone(timezone.utc)


def _validate_fed_item_link(value: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise ProviderError("INVALID_RESPONSE", "연준 RSS 링크 형식을 확인할 수 없습니다.") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.federalreserve.gov"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ProviderError("INVALID_RESPONSE", "연준 RSS 링크 형식을 확인할 수 없습니다.")


class FederalReserveNewsProvider:
    """연준 공식 보도자료 RSS의 메타데이터만 읽는 공급자.

    제목과 분류를 포함한 외부 문구는 실행 지시가 아닌 불신 데이터로 저장한다.
    링크된 기사 본문에는 접근하지 않는다.
    """

    code = "fed_news"
    host = "www.federalreserve.gov"
    source_url = "https://www.federalreserve.gov/feeds/press_all.xml"
    max_items = 500

    def __init__(self, client: SafeHttpsClient | None = None) -> None:
        self.client = client or SafeHttpsClient()

    def collect(self) -> CollectionResult:
        started = _now_utc()
        records: list[NormalizedRecord] = []
        issues: list[CollectionIssue] = []
        try:
            payload = self.client.request_bytes(
                url=self.source_url,
                allowed_host=self.host,
                headers={
                    "Accept": "application/rss+xml, application/xml, text/xml",
                    "User-Agent": "long-term-asset-assistant/0.2",
                },
                accepted_content_markers=("xml",),
            )
            upper_payload = payload.upper()
            if b"<!DOCTYPE" in upper_payload or b"<!ENTITY" in upper_payload:
                raise ProviderError("UNSAFE_XML", "연준 RSS 응답을 안전하게 해석할 수 없습니다.")
            try:
                root = ElementTree.fromstring(payload)
            except (ElementTree.ParseError, RecursionError, ValueError):
                raise ProviderError("INVALID_RESPONSE", "연준 RSS 응답 형식을 확인할 수 없습니다.") from None
            items = [element for element in root.iter() if _xml_local_name(element.tag) == "item"]
            if not items or len(items) > self.max_items:
                raise ProviderError("INVALID_RESPONSE", "연준 RSS 항목 수를 확인할 수 없습니다.")
        except ProviderError as exc:
            return CollectionResult(
                self.code,
                started,
                _now_utc(),
                CollectionStatus.ERROR,
                issues=(CollectionIssue(exc.code, "연준 공식 보도자료 RSS를 갱신하지 못했습니다."),),
            )

        seen_record_ids: set[str] = set()
        for item in items:
            try:
                title = _rss_child_text(item, "title", limit=1_000)
                link = _rss_child_text(item, "link", limit=2_048)
                guid = _rss_child_text(item, "guid", limit=2_048) or link
                publication_text = _rss_child_text(item, "pubdate", limit=100)
                categories = tuple(
                    "".join(child.itertext()).strip()
                    for child in item
                    if _xml_local_name(child.tag) == "category"
                    and "".join(child.itertext()).strip()
                )
                if (
                    not title
                    or not link
                    or not guid
                    or not publication_text
                    or any(len(category) > 200 for category in categories)
                ):
                    raise ProviderError("INVALID_RESPONSE", "연준 RSS 항목 형식을 확인할 수 없습니다.")
                _validate_fed_item_link(link)
                published = _parse_rss_datetime(publication_text)
                collected = _now_utc()
                if guid in seen_record_ids:
                    raise ProviderError("DUPLICATE_ITEM", "연준 RSS 항목 식별자가 중복되었습니다.")
                seen_record_ids.add(guid)
                records.append(
                    NormalizedRecord(
                        provider=self.code,
                        kind=RecordKind.NEWS,
                        record_id=guid,
                        instrument_id="FEDERAL_RESERVE_PRESS_RELEASE",
                        market="US",
                        currency=None,
                        effective_at=published,
                        published_at=published,
                        collected_at=collected,
                        unit="RSS_ITEM_METADATA",
                        source_url=link,
                        metadata={
                            "title": title,
                            "link": link,
                            "guid": guid,
                            "category": list(categories),
                            "publication_time_basis": "RSS_PUBDATE",
                            "content_scope": "METADATA_ONLY_NO_ARTICLE_BODY",
                            "external_content_policy": "DATA_ONLY_NO_INSTRUCTIONS",
                            "feed_source": self.source_url,
                        },
                    )
                )
            except (ProviderError, DomainValidationError, TypeError, ValueError):
                issues.append(
                    CollectionIssue("INVALID_ITEM", "연준 RSS 항목 하나를 안전하게 처리하지 못했습니다.")
                )

        finished = _now_utc()
        status = (
            CollectionStatus.PARTIAL
            if records and issues
            else CollectionStatus.OK
            if records
            else CollectionStatus.ERROR
        )
        if status is CollectionStatus.ERROR and not issues:
            issues.append(CollectionIssue("EMPTY_FEED", "연준 RSS에서 사용할 수 있는 항목이 없습니다."))
        return CollectionResult(self.code, started, finished, status, tuple(records), tuple(issues))


class SecEdgarProvider:
    code = "sec"
    host = "data.sec.gov"
    source_url = "https://data.sec.gov/submissions/"
    companyfacts_url = "https://data.sec.gov/api/xbrl/companyfacts/"

    def __init__(
        self,
        credentials: CredentialStore,
        client: SafeHttpsClient | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials
        self.client = client or SafeHttpsClient()
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_request_at: float | None = None

    def _pace_request(self) -> None:
        # SEC의 전체 장비 합산 10회/초 한도보다 보수적인 간격.
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = 0.11 - (now - self._last_request_at)
            if remaining > 0:
                self._sleeper(remaining)
                now = self._monotonic()
        self._last_request_at = now

    def _user_agent(self) -> str | None:
        """Return only a bounded, header-safe SEC identity with a contact email."""

        try:
            value = self.credentials.get_secret("sec", "user_agent")
        except CredentialStoreError:
            return None
        if not valid_sec_user_agent(value):
            return None
        return value

    def collect_submissions(self, ciks: tuple[str, ...]) -> CollectionResult:
        started = _now_utc()
        records: list[NormalizedRecord] = []
        issues: list[CollectionIssue] = []
        user_agent = self._user_agent()
        if not user_agent:
            return CollectionResult(
                self.code,
                started,
                _now_utc(),
                CollectionStatus.ERROR,
                issues=(CollectionIssue("CONFIGURATION_REQUIRED", "SEC 연결 설정이 필요합니다."),),
            )
        for cik in ciks:
            if _CIK.fullmatch(cik) is None:
                raise DomainValidationError("SEC CIK는 앞자리 0을 포함한 10자리 숫자여야 합니다.")
            try:
                payload = self.client.request_json(
                    url=f"{self.source_url}CIK{cik}.json",
                    allowed_host=self.host,
                    headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
                    before_attempt=self._pace_request,
                )
                recent = payload["filings"]["recent"]
                accessions = recent["accessionNumber"]
                forms = recent["form"]
                dates = recent["filingDate"]
                primary_docs = recent["primaryDocument"]
                if not all(len(values) == len(accessions) for values in (forms, dates, primary_docs)):
                    raise ProviderError("INVALID_RESPONSE", "SEC 응답 열 길이가 일치하지 않습니다.")
                collected = _now_utc()
                for accession, form, filing_date, primary_document in zip(
                    accessions, forms, dates, primary_docs, strict=True
                ):
                    effective = datetime.strptime(filing_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    records.append(
                        NormalizedRecord(
                            provider=self.code,
                            kind=RecordKind.DISCLOSURE,
                            record_id=str(accession),
                            instrument_id=f"CIK{cik}",
                            market="US",
                            currency=None,
                            effective_at=effective,
                            published_at=collected,
                            collected_at=collected,
                            unit="FILING",
                            source_url=self.source_url,
                            metadata={
                                "form": str(form)[:40],
                                "primary_document": str(primary_document)[:200],
                                "publication_time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                            },
                        )
                    )
            except (ProviderError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                code = exc.code if isinstance(exc, ProviderError) else "INVALID_RESPONSE"
                issues.append(CollectionIssue(code, "해당 SEC 발행인의 공시를 갱신하지 못했습니다."))
        finished = _now_utc()
        status = CollectionStatus.PARTIAL if records and issues else CollectionStatus.OK if records else CollectionStatus.ERROR
        return CollectionResult(self.code, started, finished, status, tuple(records), tuple(issues))

    def collect_companyfacts(
        self,
        ciks: tuple[str, ...],
        concepts: tuple[str, ...],
    ) -> CollectionResult:
        """선택한 US-GAAP concept만 정규화한다.

        원본 companyfacts 전체나 외부 설명문은 저장하지 않는다. ``published_at``은
        이 앱이 처음 확인한 수집시각으로 두어 그 전 시점의 분석에 쓰이지 않게 한다.
        """

        started = _now_utc()
        records: list[NormalizedRecord] = []
        issues: list[CollectionIssue] = []
        if not concepts or len(concepts) > 50 or any(
            _SEC_CONCEPT.fullmatch(concept) is None for concept in concepts
        ):
            raise DomainValidationError("SEC 재무 concept 목록을 확인할 수 없습니다.")
        if len(set(concepts)) != len(concepts):
            raise DomainValidationError("SEC 재무 concept 목록에 중복이 있습니다.")
        user_agent = self._user_agent()
        if not user_agent:
            return CollectionResult(
                self.code,
                started,
                _now_utc(),
                CollectionStatus.ERROR,
                issues=(CollectionIssue("CONFIGURATION_REQUIRED", "SEC 연결 설정이 필요합니다."),),
            )

        for cik in ciks:
            if _CIK.fullmatch(cik) is None:
                raise DomainValidationError("SEC CIK는 앞자리 0을 포함한 10자리 숫자여야 합니다.")
            try:
                payload = self.client.request_json(
                    url=f"{self.companyfacts_url}CIK{cik}.json",
                    allowed_host=self.host,
                    headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
                    before_attempt=self._pace_request,
                )
                us_gaap = payload["facts"]["us-gaap"]
                if not isinstance(us_gaap, dict):
                    raise ProviderError("INVALID_RESPONSE", "SEC 재무 응답 형식을 확인할 수 없습니다.")
                collected = _now_utc()
                for concept in concepts:
                    concept_payload = us_gaap.get(concept)
                    if concept_payload is None:
                        issues.append(
                            CollectionIssue("CONCEPT_NOT_FOUND", "선택한 SEC 재무 항목을 찾지 못했습니다.")
                        )
                        continue
                    if not isinstance(concept_payload, dict):
                        issues.append(
                            CollectionIssue("INVALID_CONCEPT", "선택한 SEC 재무 항목을 처리하지 못했습니다.")
                        )
                        continue
                    units = concept_payload.get("units")
                    if not isinstance(units, dict):
                        issues.append(
                            CollectionIssue("INVALID_CONCEPT", "선택한 SEC 재무 항목을 처리하지 못했습니다.")
                        )
                        continue
                    for unit, observations in units.items():
                        if (
                            not isinstance(unit, str)
                            or not unit
                            or len(unit) > 40
                            or not isinstance(observations, list)
                        ):
                            issues.append(
                                CollectionIssue("INVALID_CONCEPT", "선택한 SEC 재무 항목을 처리하지 못했습니다.")
                            )
                            continue
                        for observation in observations:
                            try:
                                if not isinstance(observation, dict):
                                    raise ProviderError(
                                        "INVALID_OBSERVATION", "SEC 재무 관측 형식을 확인할 수 없습니다."
                                    )
                                end = str(observation["end"])
                                accession = str(observation["accn"])
                                form = str(observation["form"])
                                filed = str(observation["filed"])
                                if not accession or not form or not filed:
                                    raise ProviderError(
                                        "INVALID_OBSERVATION", "SEC 재무 관측 형식을 확인할 수 없습니다."
                                    )
                                effective = datetime.strptime(end, "%Y-%m-%d").replace(
                                    tzinfo=timezone.utc
                                )
                                value = str(observation["val"])
                                currency = unit if re.fullmatch(r"[A-Z]{3}", unit) else None
                                frame_value = observation.get("frame")
                                frame = None if frame_value is None else str(frame_value)
                                start_value = observation.get("start")
                                start = None if start_value is None else str(start_value)
                                record_id = ":".join(
                                    (
                                        cik,
                                        concept,
                                        unit,
                                        accession,
                                        end,
                                        frame or "NO_FRAME",
                                    )
                                )
                                records.append(
                                    NormalizedRecord(
                                        provider=self.code,
                                        kind=RecordKind.FINANCIAL,
                                        record_id=record_id,
                                        instrument_id=f"CIK{cik}",
                                        market="US",
                                        currency=currency,
                                        effective_at=effective,
                                        published_at=collected,
                                        collected_at=collected,
                                        unit=unit,
                                        value=value,
                                        source_url=self.companyfacts_url,
                                        metadata={
                                            "taxonomy": "us-gaap",
                                            "concept": concept,
                                            "unit": unit,
                                            "currency": currency,
                                            "frame": frame,
                                            "fy": observation.get("fy"),
                                            "fp": observation.get("fp"),
                                            "form": form,
                                            "filed": filed,
                                            "accn": accession,
                                            "start": start,
                                            "end": end,
                                            "publication_time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                                            "external_content_policy": "DATA_ONLY_NO_INSTRUCTIONS",
                                        },
                                    )
                                )
                            except (
                                ProviderError,
                                DomainValidationError,
                                KeyError,
                                TypeError,
                                ValueError,
                            ):
                                issues.append(
                                    CollectionIssue(
                                        "INVALID_OBSERVATION",
                                        "SEC 재무 관측 하나를 안전하게 처리하지 못했습니다.",
                                    )
                                )
            except (ProviderError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                code = exc.code if isinstance(exc, ProviderError) else "INVALID_RESPONSE"
                issues.append(CollectionIssue(code, "해당 SEC 발행인의 재무자료를 갱신하지 못했습니다."))

        finished = _now_utc()
        status = (
            CollectionStatus.PARTIAL
            if records and issues
            else CollectionStatus.OK
            if records
            else CollectionStatus.ERROR
        )
        return CollectionResult(self.code, started, finished, status, tuple(records), tuple(issues))
