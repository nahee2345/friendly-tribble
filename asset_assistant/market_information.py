"""Official-feed metadata collection for the local US market information list.

Only titles, identifiers, timestamps and original links are retained.  This
module deliberately does not fetch article bodies, translate or summarize
content, infer tickers from titles, make recommendations, or place orders.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import io
import ipaddress
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Callable, Iterable, Mapping, Protocol
from xml.parsers import expat
from xml.etree import ElementTree

from .domain import DomainValidationError, aware_datetime
from .credential_store import valid_sec_user_agent
from .market_data import MarketDataStore, _utc_epoch_microseconds


MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_FEED_ITEMS = 100
MAX_XML_NODES = 12_000
MAX_XML_TEXT = 2 * 1024 * 1024
DEFAULT_COLLECTION_TIMEOUT_SECONDS = 60.0
_TIMEOUT_RETRY_SECONDS = 300
_READ_CHUNK_BYTES = 64 * 1024
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,299}\Z")
_CATEGORIES = frozenset({"MARKET", "COMPANY", "ECONOMY", "FILING"})
_SORTS = frozenset({"PUBLISHED_DESC", "COLLECTED_DESC"})
_RELATED_FILTERS = frozenset({"ALL", "RELATED", "UNCONFIRMED"})
RETENTION_UPCOMING_DAYS = 30
RETENTION_PERIOD_LABEL = "CALENDAR_YEAR_1"


class PublishedPrecision(str, Enum):
    DATE = "DATE"
    DATETIME = "DATETIME"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    institution_id: str
    provider_name: str
    source_name: str
    endpoint: str
    category: str
    cadence_seconds: int
    allowed_feed_hosts: frozenset[str]
    allowed_link_hosts: frozenset[str]
    requires_sec_contact: bool = False


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "sec_edgar",
        "sec",
        "U.S. Securities and Exchange Commission",
        "SEC EDGAR Latest Filings",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom&count=100&owner=include",
        "FILING",
        1_800,
        frozenset({"www.sec.gov", "sec.gov"}),
        frozenset({"www.sec.gov", "sec.gov", "data.sec.gov"}),
        True,
    ),
    ProviderSpec(
        "sec_press",
        "sec",
        "U.S. Securities and Exchange Commission",
        "SEC Press Releases RSS",
        "https://www.sec.gov/news/pressreleases.rss",
        "MARKET",
        3_600,
        frozenset({"www.sec.gov", "sec.gov"}),
        frozenset({"www.sec.gov", "sec.gov"}),
        True,
    ),
    ProviderSpec(
        "federal_reserve",
        "federal_reserve",
        "Board of Governors of the Federal Reserve System",
        "Federal Reserve Press Releases RSS",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "MARKET",
        3_600,
        frozenset({"www.federalreserve.gov", "federalreserve.gov"}),
        frozenset({"www.federalreserve.gov", "federalreserve.gov"}),
    ),
    ProviderSpec(
        "bls_employment",
        "bls",
        "U.S. Bureau of Labor Statistics",
        "BLS Employment Situation RSS",
        "https://www.bls.gov/feed/empsit.rss",
        "ECONOMY",
        3_600,
        frozenset({"www.bls.gov", "bls.gov"}),
        frozenset({"www.bls.gov", "bls.gov"}),
    ),
    ProviderSpec(
        "bls_cpi",
        "bls",
        "U.S. Bureau of Labor Statistics",
        "BLS Consumer Price Index RSS",
        "https://www.bls.gov/feed/cpi.rss",
        "ECONOMY",
        3_600,
        frozenset({"www.bls.gov", "bls.gov"}),
        frozenset({"www.bls.gov", "bls.gov"}),
    ),
    ProviderSpec(
        "bls_ppi",
        "bls",
        "U.S. Bureau of Labor Statistics",
        "BLS Producer Price Index RSS",
        "https://www.bls.gov/feed/ppi.rss",
        "ECONOMY",
        3_600,
        frozenset({"www.bls.gov", "bls.gov"}),
        frozenset({"www.bls.gov", "bls.gov"}),
    ),
    ProviderSpec(
        "bls_jolts",
        "bls",
        "U.S. Bureau of Labor Statistics",
        "BLS Job Openings and Labor Turnover RSS",
        "https://www.bls.gov/feed/jolts.rss",
        "ECONOMY",
        3_600,
        frozenset({"www.bls.gov", "bls.gov"}),
        frozenset({"www.bls.gov", "bls.gov"}),
    ),
    ProviderSpec(
        "bea",
        "bea",
        "U.S. Bureau of Economic Analysis",
        "BEA News Release RSS",
        "https://apps.bea.gov/rss/rss.xml",
        "ECONOMY",
        3_600,
        frozenset({"apps.bea.gov", "www.bea.gov", "bea.gov"}),
        frozenset({"apps.bea.gov", "www.bea.gov", "bea.gov"}),
    ),
)

PROVIDER_BY_ID = {spec.provider_id: spec for spec in PROVIDER_SPECS}


def _safe_text(value: object, *, limit: int, required: bool = True) -> str:
    if not isinstance(value, str):
        if required:
            raise DomainValidationError("외부 시장정보 텍스트를 확인할 수 없습니다.")
        return ""
    normalized = " ".join(_CONTROL.sub("", value).split())
    if (required and not normalized) or len(normalized) > limit:
        raise DomainValidationError("외부 시장정보 텍스트를 확인할 수 없습니다.")
    return normalized


def _normalized_host(value: str) -> str:
    return value.rstrip(".").lower()


def _blocked_hostname(hostname: str) -> bool:
    host = _normalized_host(hostname)
    if host in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        return True
    if host.endswith((".localhost", ".local", ".internal", ".home", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return not address.is_global


def validate_public_url(
    value: object,
    *,
    allowed_hosts: frozenset[str] | None = None,
) -> str | None:
    """Return a normalized display URL or ``None`` without performing I/O."""

    if not isinstance(value, str) or not value or len(value) > 2_048:
        return None
    if _CONTROL.search(value) or any(ch.isspace() for ch in value):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    hostname = _normalized_host(parsed.hostname or "")
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme.lower() == "http" and port not in {None, 80})
        or (parsed.scheme.lower() == "https" and port not in {None, 443})
        or _blocked_hostname(hostname)
        or (allowed_hosts is not None and hostname not in allowed_hosts)
    ):
        return None
    path = parsed.path or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, "")
    )


_ResolvedAddress = tuple[int, int, int, tuple[object, ...]]


def _deadline_remaining(
    deadline_monotonic: float | None,
    monotonic: Callable[[], float],
) -> float | None:
    if deadline_monotonic is None:
        return None
    remaining = deadline_monotonic - monotonic()
    if remaining <= 0:
        raise MarketInformationFetchError("COLLECTION_TIMEOUT")
    return remaining


def _assert_public_dns(
    hostname: str,
    port: int,
    *,
    deadline_monotonic: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[_ResolvedAddress, ...]:
    _deadline_remaining(deadline_monotonic, monotonic)
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        raise MarketInformationFetchError("DNS_FAILED") from None
    # ``getaddrinfo`` is an OS resolver call and cannot be cancelled safely by
    # Python in-process.  Check immediately afterwards so no connection is
    # attempted once the cooperative collection deadline has elapsed.
    _deadline_remaining(deadline_monotonic, monotonic)
    if not answers:
        raise MarketInformationFetchError("DNS_FAILED")
    validated: list[_ResolvedAddress] = []
    for answer in answers:
        try:
            family, socktype, proto, _canonical_name, raw_sockaddr = answer
            if family not in {socket.AF_INET, socket.AF_INET6}:
                raise ValueError
            if socktype != socket.SOCK_STREAM or not isinstance(raw_sockaddr, tuple):
                raise ValueError
            address = ipaddress.ip_address(raw_sockaddr[0])
            if family == socket.AF_INET:
                sockaddr: tuple[object, ...] = (str(address), port)
            else:
                flowinfo = int(raw_sockaddr[2]) if len(raw_sockaddr) > 2 else 0
                scope_id = int(raw_sockaddr[3]) if len(raw_sockaddr) > 3 else 0
                sockaddr = (str(address), port, flowinfo, scope_id)
        except (IndexError, TypeError, ValueError):
            raise MarketInformationFetchError("DNS_FAILED") from None
        if not address.is_global:
            raise MarketInformationFetchError("UNSAFE_DESTINATION")
        candidate = (family, socktype, proto, sockaddr)
        if candidate not in validated:
            validated.append(candidate)
    if not validated:
        raise MarketInformationFetchError("DNS_FAILED")
    return tuple(validated)


@dataclass(frozen=True)
class FetchResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    received_at: datetime


class FeedFetcher(Protocol):
    def fetch(
        self,
        spec: ProviderSpec,
        *,
        etag: str | None,
        last_modified: str | None,
        user_agent: str,
        deadline_monotonic: float | None = None,
    ) -> FetchResponse: ...


class MarketInformationFetchError(Exception):
    def __init__(
        self,
        code: str,
        *,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


class _LocalOwnerGuardFailed(Exception):
    """Keep local single-owner failures out of provider error handling."""


class _NoUnsafeRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        self.count += 1
        checked = validate_public_url(newurl, allowed_hosts=self.spec.allowed_feed_hosts)
        if self.count > 3 or checked is None or not checked.startswith("https://"):
            raise MarketInformationFetchError("UNSAFE_REDIRECT")
        return super().redirect_request(req, fp, code, msg, headers, checked)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to prevalidated IPs while keeping the original TLS hostname."""

    def __init__(
        self,
        host: str,
        *,
        pinned_addresses: tuple[_ResolvedAddress, ...],
        deadline_monotonic: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        **kwargs: object,
    ) -> None:
        super().__init__(host, **kwargs)
        self.pinned_addresses = pinned_addresses
        self.deadline_monotonic = deadline_monotonic
        self.monotonic = monotonic
        # HTTPConnection deliberately exposes this hook for tests.  Replacing
        # it also lets HTTPSConnection retain its standard TLS wrapping,
        # certificate verification and server_hostname=self.host behavior.
        self._create_connection = self._connect_pinned

    def connect(self) -> None:
        # HTTPSConnection performs the normal certificate verification and
        # SNI setup before we wrap the established TLS socket.  The wrapper
        # only replaces makefile() so every low-level header/body recv uses
        # the remaining absolute collection budget.  A single relative
        # timeout on BufferedReader.read()/readline() is insufficient because
        # a slow peer can keep resetting it by sending one byte at a time.
        super().connect()
        if self.sock is not None and not isinstance(
            self.sock, _DeadlineSocketProxy
        ):
            self.sock = _DeadlineSocketProxy(
                self.sock,
                deadline_monotonic=self.deadline_monotonic,
                monotonic=self.monotonic,
                operation_timeout=self.timeout,
            )

    def _connect_pinned(
        self,
        _address: tuple[str, int],
        timeout: object = socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        last_error: OSError | None = None
        for family, socktype, proto, sockaddr in self.pinned_addresses:
            candidate: socket.socket | None = None
            try:
                candidate = socket.socket(family, socktype, proto)
                remaining = _deadline_remaining(
                    self.deadline_monotonic, self.monotonic
                )
                socket_timeout = timeout
                if remaining is not None:
                    socket_timeout = (
                        remaining
                        if timeout is socket._GLOBAL_DEFAULT_TIMEOUT
                        else min(float(timeout), remaining)
                    )
                if socket_timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    candidate.settimeout(socket_timeout)  # type: ignore[arg-type]
                if source_address is not None:
                    candidate.bind(source_address)
                candidate.connect(sockaddr)  # type: ignore[arg-type]
                return candidate
            except MarketInformationFetchError:
                if candidate is not None:
                    candidate.close()
                raise
            except OSError as exc:
                last_error = exc
                if candidate is not None:
                    candidate.close()
        if last_error is not None:
            raise last_error
        raise OSError("no validated destination")


class _DeadlineSocketReader(io.RawIOBase):
    """Socket reader that reapplies an absolute deadline before every recv."""

    def __init__(
        self,
        sock: socket.socket,
        raw_socket_file: io.RawIOBase,
        *,
        deadline_monotonic: float | None,
        monotonic: Callable[[], float],
        operation_timeout: object,
    ) -> None:
        super().__init__()
        self._socket = sock
        self._raw_socket_file = raw_socket_file
        self._deadline_monotonic = deadline_monotonic
        self._monotonic = monotonic
        self._operation_timeout = operation_timeout

    def readable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._raw_socket_file.fileno()

    def readinto(self, buffer: object) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed socket reader")
        remaining = _deadline_remaining(
            self._deadline_monotonic, self._monotonic
        )
        timeout: float | None
        if self._operation_timeout is socket._GLOBAL_DEFAULT_TIMEOUT:
            timeout = None
        else:
            timeout = self._operation_timeout  # type: ignore[assignment]
        if remaining is not None:
            timeout = remaining if timeout is None else min(float(timeout), remaining)
        self._socket.settimeout(timeout)
        try:
            return self._raw_socket_file.readinto(buffer)  # type: ignore[arg-type]
        except (TimeoutError, socket.timeout):
            # Prefer the stable global-budget code when the absolute deadline
            # caused the timeout; otherwise preserve the ordinary socket
            # timeout for the fetcher's existing error mapping.
            _deadline_remaining(self._deadline_monotonic, self._monotonic)
            raise

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._raw_socket_file.close()
        finally:
            super().close()


class _DeadlineSocketProxy:
    """Delegate a connected TLS socket while supplying a deadline-aware file."""

    def __init__(
        self,
        sock: socket.socket,
        *,
        deadline_monotonic: float | None,
        monotonic: Callable[[], float],
        operation_timeout: object,
    ) -> None:
        self._socket = sock
        self._deadline_monotonic = deadline_monotonic
        self._monotonic = monotonic
        self._operation_timeout = operation_timeout

    def makefile(
        self,
        mode: str = "r",
        buffering: int | None = None,
        *,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> object:
        if mode != "rb" or any(
            value is not None for value in (encoding, errors, newline)
        ):
            return self._socket.makefile(
                mode,
                buffering,
                encoding=encoding,
                errors=errors,
                newline=newline,
            )
        # Let the real socket create the unbuffered SocketIO first.  Besides
        # providing the platform-specific raw I/O implementation, this
        # increments its internal file-reference count.  urllib deliberately
        # closes h.sock after parsing headers, so retaining that reference is
        # required for the response body to remain readable.
        raw_socket_file = self._socket.makefile("rb", buffering=0)
        raw = _DeadlineSocketReader(
            self._socket,
            raw_socket_file,  # type: ignore[arg-type]
            deadline_monotonic=self._deadline_monotonic,
            monotonic=self._monotonic,
            operation_timeout=self._operation_timeout,
        )
        if buffering == 0:
            return raw
        buffer_size = io.DEFAULT_BUFFER_SIZE if buffering in {None, -1} else buffering
        return io.BufferedReader(raw, buffer_size=buffer_size)

    def __getattr__(self, name: str) -> object:
        return getattr(self._socket, name)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """Resolve each allowed hostname once and pin all requests to that result."""

    def __init__(
        self,
        spec: ProviderSpec,
        *,
        initial_host: str,
        initial_port: int,
        initial_addresses: tuple[_ResolvedAddress, ...],
        deadline_monotonic: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self.spec = spec
        self.deadline_monotonic = deadline_monotonic
        self.monotonic = monotonic
        self._addresses: dict[tuple[str, int], tuple[_ResolvedAddress, ...]] = {
            (_normalized_host(initial_host), initial_port): initial_addresses
        }

    def https_open(self, req):  # type: ignore[no-untyped-def]
        checked = validate_public_url(
            req.full_url, allowed_hosts=self.spec.allowed_feed_hosts
        )
        if checked is None or not checked.startswith("https://"):
            raise MarketInformationFetchError("UNSAFE_ENDPOINT")
        parsed = urllib.parse.urlsplit(checked)
        hostname = _normalized_host(parsed.hostname or "")
        port = parsed.port or 443
        key = (hostname, port)
        addresses = self._addresses.get(key)
        if addresses is None:
            addresses = _assert_public_dns(
                hostname,
                port,
                deadline_monotonic=self.deadline_monotonic,
                monotonic=self.monotonic,
            )
            self._addresses[key] = addresses

        def connection_factory(authority: str, **kwargs: object) -> _PinnedHTTPSConnection:
            try:
                authority_url = urllib.parse.urlsplit(f"https://{authority}")
                authority_host = _normalized_host(authority_url.hostname or "")
                authority_port = authority_url.port or 443
            except ValueError:
                raise MarketInformationFetchError("UNSAFE_ENDPOINT") from None
            if (authority_host, authority_port) != key:
                raise MarketInformationFetchError("UNSAFE_ENDPOINT")
            return _PinnedHTTPSConnection(
                authority,
                pinned_addresses=addresses,
                deadline_monotonic=self.deadline_monotonic,
                monotonic=self.monotonic,
                **kwargs,
            )

        return self.do_open(connection_factory, req, context=self._context)


class OfficialFeedFetcher:
    """Bounded HTTPS fetcher for the immutable provider registry above."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.monotonic = monotonic

    def fetch(
        self,
        spec: ProviderSpec,
        *,
        etag: str | None,
        last_modified: str | None,
        user_agent: str,
        deadline_monotonic: float | None = None,
    ) -> FetchResponse:
        _deadline_remaining(deadline_monotonic, self.monotonic)
        checked = validate_public_url(spec.endpoint, allowed_hosts=spec.allowed_feed_hosts)
        if checked is None or not checked.startswith("https://"):
            raise MarketInformationFetchError("UNSAFE_ENDPOINT")
        parsed = urllib.parse.urlsplit(checked)
        hostname = _normalized_host(parsed.hostname or "")
        port = parsed.port or 443
        addresses = _assert_public_dns(
            hostname,
            port,
            deadline_monotonic=deadline_monotonic,
            monotonic=self.monotonic,
        )
        headers = {
            "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml",
            "User-Agent": user_agent,
        }
        if etag:
            headers["If-None-Match"] = etag[:500]
        if last_modified:
            headers["If-Modified-Since"] = last_modified[:500]
        request = urllib.request.Request(checked, headers=headers, method="GET")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoUnsafeRedirects(spec),
            _PinnedHTTPSHandler(
                spec,
                initial_host=hostname,
                initial_port=port,
                initial_addresses=addresses,
                deadline_monotonic=deadline_monotonic,
                monotonic=self.monotonic,
            ),
        )
        try:
            remaining = _deadline_remaining(deadline_monotonic, self.monotonic)
            operation_timeout = self.timeout_seconds
            if remaining is not None:
                operation_timeout = min(operation_timeout, remaining)
            with opener.open(request, timeout=operation_timeout) as response:
                status = int(response.status)
                content_type = str(response.headers.get("Content-Type", "")).lower()
                if not any(marker in content_type for marker in ("xml", "rss", "atom")):
                    raise MarketInformationFetchError("UNEXPECTED_CONTENT_TYPE")
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        if int(declared) > MAX_FEED_BYTES:
                            raise MarketInformationFetchError("RESPONSE_TOO_LARGE")
                    except ValueError:
                        raise MarketInformationFetchError("INVALID_RESPONSE") from None
                chunks: list[bytes] = []
                received_bytes = 0
                while received_bytes <= MAX_FEED_BYTES:
                    remaining = _deadline_remaining(
                        deadline_monotonic, self.monotonic
                    )
                    if remaining is not None:
                        # CPython's urllib response normally exposes this
                        # socket.  When an alternate response object does not,
                        # the before/after checks still enforce a cooperative
                        # cap; the documented OS-resolver/socket limitation
                        # remains explicit in the status contract.
                        try:
                            response.fp.raw._sock.settimeout(  # type: ignore[attr-defined]
                                min(self.timeout_seconds, remaining)
                            )
                        except (AttributeError, OSError):
                            pass
                    chunk = response.read(
                        min(_READ_CHUNK_BYTES, MAX_FEED_BYTES + 1 - received_bytes)
                    )
                    _deadline_remaining(deadline_monotonic, self.monotonic)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received_bytes += len(chunk)
                body = b"".join(chunks)
                if len(body) > MAX_FEED_BYTES:
                    raise MarketInformationFetchError("RESPONSE_TOO_LARGE")
                return FetchResponse(
                    status,
                    {str(k).lower(): str(v) for k, v in response.headers.items()},
                    body,
                    datetime.now(UTC),
                )
        except urllib.error.HTTPError as exc:
            _deadline_remaining(deadline_monotonic, self.monotonic)
            if exc.code == 304:
                return FetchResponse(
                    304,
                    {str(k).lower(): str(v) for k, v in exc.headers.items()},
                    b"",
                    datetime.now(UTC),
                )
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
            raise MarketInformationFetchError(
                f"HTTP_{exc.code}",
                http_status=int(exc.code),
                retry_after_seconds=retry_after,
            ) from None
        except MarketInformationFetchError:
            raise
        except urllib.error.URLError as exc:
            _deadline_remaining(deadline_monotonic, self.monotonic)
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise MarketInformationFetchError("SOCKET_TIMEOUT") from None
            raise MarketInformationFetchError("NETWORK_ERROR") from None
        except (TimeoutError, socket.timeout):
            _deadline_remaining(deadline_monotonic, self.monotonic)
            raise MarketInformationFetchError("SOCKET_TIMEOUT") from None
        except (OSError, ValueError):
            raise MarketInformationFetchError("FETCH_FAILED") from None


def _retry_after_seconds(value: object, *, now: datetime | None = None) -> int | None:
    if not isinstance(value, str) or not value or len(value) > 100:
        return None
    try:
        seconds = int(value)
        return max(0, min(seconds, 86_400))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        delta = int((parsed.astimezone(UTC) - (now or datetime.now(UTC))).total_seconds())
        return max(0, min(delta, 86_400))
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class MarketInformationItem:
    canonical_key: str
    provider_id: str
    provider_name: str
    source_name: str
    title_original: str
    language: str
    category: str
    related_symbols: tuple[str, ...]
    original_url: str | None
    published_at: datetime | None
    collected_at: datetime
    provider_item_id: str | None
    source_document_id: str | None
    published_precision: PublishedPrecision
    is_sample: bool = False

    @property
    def info_id(self) -> str:
        namespace = "sample" if self.is_sample else "live"
        return "mi_" + hashlib.sha256(
            f"{namespace}:{self.canonical_key}".encode("utf-8")
        ).hexdigest()[:32]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ElementTree.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in element:
        if _local_name(child.tag) in wanted and child.text:
            return child.text
    return ""


def _entry_link(element: ElementTree.Element) -> str:
    for child in element:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        relation = child.attrib.get("rel", "alternate")
        if href and relation in {"alternate", ""}:
            return href
        if child.text:
            return child.text
    return ""


def _parse_published(value: str) -> tuple[datetime | None, PublishedPrecision]:
    text = value.strip()
    if not text:
        return None, PublishedPrecision.UNKNOWN
    if _DATE_ONLY.fullmatch(text):
        try:
            return datetime.fromisoformat(text).replace(tzinfo=UTC), PublishedPrecision.DATE
        except ValueError:
            return None, PublishedPrecision.UNKNOWN
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None, PublishedPrecision.UNKNOWN
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, PublishedPrecision.UNKNOWN
    return parsed.astimezone(UTC), PublishedPrecision.DATETIME


def _canonical_key(
    spec: ProviderSpec,
    *,
    provider_item_id: str | None,
    document_id: str | None,
    original_url: str | None,
) -> str:
    if document_id:
        return f"{spec.institution_id}:document:{document_id.lower()}"
    if provider_item_id:
        return f"{spec.provider_id}:item:{provider_item_id}"
    if original_url:
        return f"{spec.institution_id}:url:{original_url}"
    raise DomainValidationError("시장정보의 안정 식별자를 확인할 수 없습니다.")


def parse_official_feed(
    spec: ProviderSpec,
    body: bytes,
    *,
    collected_at: datetime,
) -> tuple[MarketInformationItem, ...]:
    """Parse bounded RSS/Atom without resolving entities or reading bodies."""

    aware_datetime(collected_at, field_name="collected_at")
    if len(body) > MAX_FEED_BYTES:
        raise DomainValidationError("시장정보 XML을 안전하게 처리할 수 없습니다.")

    class _UnsafeXmlDeclaration(Exception):
        pass

    def reject_declaration(*_args: object) -> None:
        raise _UnsafeXmlDeclaration

    try:
        declaration_parser = expat.ParserCreate()
        declaration_parser.StartDoctypeDeclHandler = reject_declaration
        declaration_parser.EntityDeclHandler = reject_declaration
        declaration_parser.UnparsedEntityDeclHandler = reject_declaration
        declaration_parser.ExternalEntityRefHandler = reject_declaration
        declaration_parser.Parse(body, True)
        root = ElementTree.fromstring(body)
    except (
        _UnsafeXmlDeclaration,
        expat.ExpatError,
        ElementTree.ParseError,
        RecursionError,
        ValueError,
    ):
        raise DomainValidationError("시장정보 XML을 안전하게 처리할 수 없습니다.") from None
    nodes = 0
    text_size = 0
    for node in root.iter():
        nodes += 1
        if node.text:
            text_size += len(node.text)
        if nodes > MAX_XML_NODES or text_size > MAX_XML_TEXT:
            raise DomainValidationError("시장정보 XML을 안전하게 처리할 수 없습니다.")
    stack: list[tuple[ElementTree.Element, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > 64:
            raise DomainValidationError("시장정보 XML을 안전하게 처리할 수 없습니다.")
        stack.extend((child, depth + 1) for child in node)
    candidates = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    items: list[MarketInformationItem] = []
    for entry in candidates[:MAX_FEED_ITEMS]:
        try:
            title = _safe_text(_child_text(entry, "title"), limit=800)
            raw_url = _entry_link(entry)
            original_url = validate_public_url(raw_url, allowed_hosts=spec.allowed_link_hosts)
            raw_item_id = _child_text(entry, "guid", "id")
            provider_item_id = (
                _safe_text(raw_item_id, limit=300, required=False) or original_url
            )
            if provider_item_id and _CONTROL.search(provider_item_id):
                provider_item_id = None
            published_text = _child_text(entry, "pubDate", "published", "updated", "date")
            published_at, precision = _parse_published(published_text)
            # An accession is a strong identity only when the feed identifier
            # or official link carries it.  A title can coincidentally contain
            # the same digit pattern and must not merge unrelated documents.
            evidence = " ".join((raw_item_id, raw_url))
            accession = _ACCESSION.search(evidence)
            document_id = accession.group(0) if accession else None
            key = _canonical_key(
                spec,
                provider_item_id=provider_item_id,
                document_id=document_id,
                original_url=original_url,
            )
            items.append(
                MarketInformationItem(
                    canonical_key=key,
                    provider_id=spec.provider_id,
                    provider_name=spec.provider_name,
                    source_name=spec.source_name,
                    title_original=title,
                    language="en",
                    category=spec.category,
                    related_symbols=(),
                    original_url=original_url,
                    published_at=published_at,
                    collected_at=collected_at.astimezone(UTC),
                    provider_item_id=provider_item_id,
                    source_document_id=document_id,
                    published_precision=precision,
                )
            )
        except DomainValidationError:
            # One malformed feed item must not discard other valid metadata.
            continue
    return tuple(items)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _decode_json_list(value: str) -> list[object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError, RecursionError):
        raise DomainValidationError("저장된 시장정보를 안전하게 읽을 수 없습니다.") from None
    if not isinstance(parsed, list):
        raise DomainValidationError("저장된 시장정보를 안전하게 읽을 수 없습니다.")
    return parsed


def _stored_aware_datetime(value: object, epoch_us: object) -> datetime | None:
    """Return a trustworthy stored timestamp, rejecting mismatched precision data."""

    if not isinstance(value, str) or isinstance(epoch_us, bool) or not isinstance(
        epoch_us, int
    ):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        aware_datetime(parsed, field_name="stored_at")
        if _utc_epoch_microseconds(parsed, field_name="stored_at") != epoch_us:
            return None
    except (TypeError, ValueError, OverflowError, DomainValidationError):
        return None
    return parsed.astimezone(UTC)


def _calendar_year_later(value: datetime) -> datetime | None:
    """Add one calendar year; 29 February expires on 28 February next year."""

    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        try:
            return value.replace(year=value.year + 1, month=2, day=28)
        except ValueError:
            return None


def _retention_basis(
    first_seen_at: object,
    first_seen_at_us: object,
    collected_at: object,
    collected_at_us: object,
) -> tuple[datetime, bool] | None:
    exact = _stored_aware_datetime(first_seen_at, first_seen_at_us)
    if exact is not None:
        return exact, False
    estimated = _stored_aware_datetime(collected_at, collected_at_us)
    if estimated is not None:
        return estimated, True
    return None


def _retention_expiry_us(
    first_seen_at: object,
    first_seen_at_us: object,
    collected_at: object,
    collected_at_us: object,
) -> int | None:
    basis = _retention_basis(
        first_seen_at, first_seen_at_us, collected_at, collected_at_us
    )
    if basis is None:
        return None
    expires_at = _calendar_year_later(basis[0])
    if expires_at is None:
        return None
    return _utc_epoch_microseconds(
        expires_at, field_name="retention.expires_at"
    )


def _safe_source_count(value: object) -> int:
    if not isinstance(value, str):
        return 0
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError, RecursionError):
        return 0
    return len(decoded) if isinstance(decoded, list) else 0


def _retention_metadata(row: Mapping[str, object], *, now: datetime) -> dict[str, object]:
    basis = _retention_basis(
        row["first_seen_at"],
        row["first_seen_at_us"],
        row["collected_at"],
        row["collected_at_us"],
    )
    if basis is None:
        return {
            "first_collected_at": None,
            "first_collected_at_estimated": False,
            "expires_at": None,
            "retention_status": "CALCULATION_UNAVAILABLE",
            "retention_period": RETENTION_PERIOD_LABEL,
        }
    first_collected_at, estimated = basis
    expires_at = _calendar_year_later(first_collected_at)
    if expires_at is None:
        return {
            "first_collected_at": first_collected_at.isoformat(),
            "first_collected_at_estimated": estimated,
            "expires_at": None,
            "retention_status": "CALCULATION_UNAVAILABLE",
            "retention_period": RETENTION_PERIOD_LABEL,
        }
    if now >= expires_at:
        status = "EXPIRED"
    elif expires_at <= now + timedelta(days=RETENTION_UPCOMING_DAYS):
        status = "EXPIRING_SOON"
    else:
        status = "WITHIN_RETENTION"
    return {
        "first_collected_at": first_collected_at.isoformat(),
        "first_collected_at_estimated": estimated,
        "expires_at": expires_at.isoformat(),
        "retention_status": status,
        "retention_period": RETENTION_PERIOD_LABEL,
    }


class MarketInformationRepository:
    """Transactional read model stored beside the existing market records."""

    def __init__(self, store: MarketDataStore) -> None:
        self.store = store
        self.store.initialize()

    def ensure_provider_rows(self, specs: Iterable[ProviderSpec], *, now: datetime) -> None:
        aware_datetime(now, field_name="now")
        now_utc = now.astimezone(UTC)
        now_us = _utc_epoch_microseconds(now_utc, field_name="now")
        connection = self.store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for spec in specs:
                initial = "NEEDS_CONFIGURATION" if spec.requires_sec_contact else "NEVER_RUN"
                connection.execute(
                    """INSERT OR IGNORE INTO market_information_provider_state
                       (provider_id, institution_id, provider_name, source_name, status, cadence_seconds,
                        consecutive_failures, checked_not_modified, updated_at, updated_at_us)
                       VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
                    (
                        spec.provider_id,
                        spec.institution_id,
                        spec.provider_name,
                        spec.source_name,
                        initial,
                        spec.cadence_seconds,
                        now_utc.isoformat(),
                        now_us,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise DomainValidationError("시장정보 공급자 상태를 준비할 수 없습니다.") from None
        finally:
            connection.close()

    def state(self, provider_id: str):  # type: ignore[no-untyped-def]
        connection = self.store._connect()
        try:
            return connection.execute(
                "SELECT * FROM market_information_provider_state WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
        finally:
            connection.close()

    def claim(self, provider_id: str, owner: str, *, now: datetime, seconds: int = 120) -> bool:
        now_us = _utc_epoch_microseconds(now, field_name="now")
        expires = now_us + seconds * 1_000_000
        connection = self.store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM market_information_refresh_claims WHERE expires_at_us <= ?",
                (now_us,),
            )
            cursor = connection.execute(
                """INSERT OR IGNORE INTO market_information_refresh_claims
                   (provider_id, owner_token, expires_at_us) VALUES (?, ?, ?)""",
                (provider_id, owner, expires),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            connection.rollback()
            raise DomainValidationError("시장정보 갱신 잠금을 확인할 수 없습니다.") from None
        finally:
            connection.close()

    def release(self, provider_id: str, owner: str) -> None:
        connection = self.store._connect()
        try:
            connection.execute(
                "DELETE FROM market_information_refresh_claims WHERE provider_id=? AND owner_token=?",
                (provider_id, owner),
            )
            connection.commit()
        finally:
            connection.close()

    def save_items(self, items: Iterable[MarketInformationItem], *, now: datetime) -> int:
        now_utc = now.astimezone(UTC)
        now_us = _utc_epoch_microseconds(now_utc, field_name="now")
        inserted = 0
        connection = self.store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for item in items:
                if item.category not in _CATEGORIES:
                    raise DomainValidationError("시장정보 분류를 확인할 수 없습니다.")
                existing = connection.execute(
                    "SELECT * FROM market_information WHERE info_id=?",
                    (item.info_id,),
                ).fetchone()
                source = {
                    "provider_id": item.provider_id,
                    "source_name": item.source_name,
                    "title_original": item.title_original,
                    "original_url": item.original_url,
                    "published_at": _iso(item.published_at),
                    "published_precision": item.published_precision.value,
                    "provider_item_id": item.provider_item_id,
                    "source_document_id": item.source_document_id,
                }
                if existing is None:
                    symbols = sorted(set(item.related_symbols))
                    connection.execute(
                        """INSERT INTO market_information
                           (info_id, canonical_key, provider_id, institution_id, provider_name, source_name,
                            title_original, language, category, related_symbols_json,
                            original_url, published_at, published_at_us, collected_at,
                            collected_at_us, provider_item_id, source_document_id,
                            published_precision, is_sample, sources_json, first_seen_at,
                            first_seen_at_us, updated_at, updated_at_us)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            item.info_id,
                            ("sample:" if item.is_sample else "live:")
                            + item.canonical_key,
                            item.provider_id,
                            PROVIDER_BY_ID[item.provider_id].institution_id,
                            item.provider_name,
                            item.source_name,
                            item.title_original,
                            item.language,
                            item.category,
                            json.dumps(symbols, ensure_ascii=False),
                            item.original_url,
                            _iso(item.published_at),
                            (
                                _utc_epoch_microseconds(item.published_at, field_name="published_at")
                                if item.published_at is not None
                                else None
                            ),
                            _iso(item.collected_at),
                            _utc_epoch_microseconds(item.collected_at, field_name="collected_at"),
                            item.provider_item_id,
                            item.source_document_id,
                            item.published_precision.value,
                            int(item.is_sample),
                            json.dumps([source], ensure_ascii=False, sort_keys=True),
                            now_utc.isoformat(),
                            now_us,
                            now_utc.isoformat(),
                            now_us,
                        ),
                    )
                    inserted += 1
                    continue
                sources = _decode_json_list(existing["sources_json"])
                source_digest = hashlib.sha256(
                    json.dumps(
                        source,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                identity = (
                    source["provider_id"],
                    source["provider_item_id"] or source["original_url"],
                    source_digest,
                )
                known = {
                    (
                        candidate.get("provider_id"),
                        candidate.get("provider_item_id") or candidate.get("original_url"),
                        hashlib.sha256(
                            json.dumps(
                                candidate,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                    )
                    for candidate in sources
                    if isinstance(candidate, dict)
                }
                if identity not in known:
                    sources.append(source)
                connection.execute(
                    """UPDATE market_information
                       SET sources_json=?, updated_at=?, updated_at_us=?
                       WHERE info_id=?""",
                    (
                        json.dumps(sources, ensure_ascii=False, sort_keys=True),
                        now_utc.isoformat(),
                        now_us,
                        existing["info_id"],
                    ),
                )
            connection.commit()
            return inserted
        except DomainValidationError:
            connection.rollback()
            raise
        except Exception:
            connection.rollback()
            raise DomainValidationError(
                "시장정보 갱신을 저장하지 못했습니다. 기존 정상 자료는 유지됩니다."
            ) from None
        finally:
            connection.close()

    def update_state(
        self,
        spec: ProviderSpec,
        *,
        status: str,
        now: datetime,
        success: bool,
        next_allowed_at: datetime,
        etag: str | None = None,
        last_modified: str | None = None,
        error_code: str | None = None,
        http_status: int | None = None,
        not_modified: bool = False,
        preserve_validators: bool = False,
        count_failure: bool = True,
    ) -> None:
        state = self.state(spec.provider_id)
        if state is None:
            raise DomainValidationError("시장정보 공급자 상태를 확인할 수 없습니다.")
        failures = (
            0
            if success
            else int(state["consecutive_failures"]) + (1 if count_failure else 0)
        )
        if preserve_validators:
            etag = state["etag"]
            last_modified = state["last_modified"]
        now_utc = now.astimezone(UTC)
        now_us = _utc_epoch_microseconds(now_utc, field_name="now")
        next_utc = next_allowed_at.astimezone(UTC)
        connection = self.store._connect()
        try:
            connection.execute(
                """UPDATE market_information_provider_state
                   SET status=?, last_attempt_at=?, last_attempt_at_us=?,
                       last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,
                       last_success_at_us=CASE WHEN ? THEN ? ELSE last_success_at_us END,
                       next_allowed_at=?, next_allowed_at_us=?,
                       etag=?, last_modified=?,
                       error_code=?, consecutive_failures=?, last_http_status=?,
                       checked_not_modified=?, updated_at=?, updated_at_us=?
                   WHERE provider_id=?""",
                (
                    status,
                    now_utc.isoformat(),
                    now_us,
                    int(success),
                    now_utc.isoformat(),
                    int(success),
                    now_us,
                    next_utc.isoformat(),
                    _utc_epoch_microseconds(next_utc, field_name="next_allowed_at"),
                    etag,
                    last_modified,
                    error_code,
                    failures,
                    http_status,
                    int(not_modified),
                    now_utc.isoformat(),
                    now_us,
                    spec.provider_id,
                ),
            )
            connection.commit()
        except Exception:
            raise DomainValidationError("시장정보 공급자 상태를 저장할 수 없습니다.") from None
        finally:
            connection.close()

    @staticmethod
    def _encode_cursor(
        *, snapshot_us: int, sort: str, filter_key: str, last_id: str
    ) -> str:
        raw = json.dumps(
            {
                "v": 1,
                "snapshot_us": snapshot_us,
                "sort": sort,
                "filter_key": filter_key,
                "last_id": last_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(value: str) -> dict[str, object]:
        try:
            if not value or len(value) > 500:
                raise ValueError
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or payload.get("v") != 1
                or not isinstance(payload.get("snapshot_us"), int)
                or payload.get("sort") not in _SORTS
                or not isinstance(payload.get("filter_key"), str)
                or not isinstance(payload.get("last_id"), str)
            ):
                raise ValueError
            return payload
        except (ValueError, TypeError, json.JSONDecodeError, RecursionError):
            raise DomainValidationError("시장정보 페이지 커서를 확인할 수 없습니다.") from None

    @staticmethod
    def _register_retention_functions(connection: object) -> None:
        # sqlite3 evaluates these helpers inside aggregate/keyset queries, so
        # only the requested preview rows are materialized in Python.
        connection.create_function("wealthy_retention_expiry_us", 4, _retention_expiry_us)
        connection.create_function("wealthy_source_count", 1, _safe_source_count)

    @staticmethod
    def _materialize_information_row(
        row: Mapping[str, object], *, retention_at: datetime
    ) -> dict[str, object]:
        symbols = [str(value) for value in _decode_json_list(str(row["related_symbols_json"]))]
        spec = PROVIDER_BY_ID.get(str(row["provider_id"]))
        allowed = spec.allowed_link_hosts if spec else frozenset()
        url = validate_public_url(row["original_url"], allowed_hosts=allowed)
        raw_sources = _decode_json_list(str(row["sources_json"]))
        sources: list[dict[str, object]] = []
        for raw in raw_sources:
            if not isinstance(raw, dict):
                continue
            source_provider_id = str(raw.get("provider_id") or row["provider_id"])
            source_spec = PROVIDER_BY_ID.get(source_provider_id)
            source_allowed = (
                source_spec.allowed_link_hosts if source_spec else frozenset()
            )
            source_url = validate_public_url(
                raw.get("original_url"), allowed_hosts=source_allowed
            )
            sources.append(
                {
                    "provider_id": source_provider_id,
                    "provider_name": (
                        source_spec.provider_name
                        if source_spec is not None
                        else str(row["provider_name"])
                    ),
                    "source_name": str(raw.get("source_name") or row["source_name"]),
                    "title_original": str(
                        raw.get("title_original") or row["title_original"]
                    ),
                    "original_url": source_url,
                    "link_status": "SAFE" if source_url else "UNAVAILABLE",
                    "published_at": raw.get("published_at"),
                    "published_precision": raw.get("published_precision") or "UNKNOWN",
                    "provider_item_id": raw.get("provider_item_id"),
                    "source_document_id": raw.get("source_document_id"),
                }
            )
        result: dict[str, object] = {
            "info_id": str(row["info_id"]),
            "provider_id": str(row["provider_id"]),
            "institution_id": str(row["institution_id"]),
            "provider_name": str(row["provider_name"]),
            "source_name": str(row["source_name"]),
            "title_original": str(row["title_original"]),
            "language": str(row["language"]),
            "category": str(row["category"]),
            "related_symbols": symbols,
            "holding_status": "UNKNOWN",
            "original_url": url,
            "link_status": "SAFE" if url else "UNAVAILABLE",
            "published_at": row["published_at"],
            "collected_at": str(row["collected_at"]),
            "provider_item_id": row["provider_item_id"],
            "source_document_id": row["source_document_id"],
            "published_precision": str(row["published_precision"]),
            "is_sample": bool(row["is_sample"]),
            "sources": sources,
        }
        retention = _retention_metadata(row, now=retention_at)
        result.update(
            {
                "first_collected_at": retention["first_collected_at"],
                "first_collected_estimated": retention[
                    "first_collected_at_estimated"
                ],
                "retention_expires_at": retention["expires_at"],
                "retention_status": retention["retention_status"],
                "retention_period": retention["retention_period"],
            }
        )
        return result

    def list_items(
        self,
        *,
        now: datetime,
        q: str = "",
        category: str = "ALL",
        related: str = "ALL",
        provider: str = "",
        sort: str = "PUBLISHED_DESC",
        cursor: str = "",
        limit: int = 30,
        snapshot: str = "",
    ) -> dict[str, object]:
        aware_datetime(now, field_name="now")
        if category != "ALL" and category not in _CATEGORIES:
            raise DomainValidationError("시장정보 분류 필터를 확인할 수 없습니다.")
        if related not in _RELATED_FILTERS or sort not in _SORTS:
            raise DomainValidationError("시장정보 필터를 확인할 수 없습니다.")
        if provider and provider not in PROVIDER_BY_ID:
            raise DomainValidationError("시장정보 출처 필터를 확인할 수 없습니다.")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise DomainValidationError("시장정보 페이지 크기는 1~100이어야 합니다.")
        query = _safe_text(q, limit=100, required=False).casefold()
        filter_key = hashlib.sha256(
            json.dumps(
                {
                    "q": query,
                    "category": category,
                    "related": related,
                    "provider": provider,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if snapshot:
            try:
                snapshot_at = datetime.fromisoformat(snapshot)
                aware_datetime(snapshot_at, field_name="snapshot")
            except (TypeError, ValueError, OverflowError, DomainValidationError):
                raise DomainValidationError("시장정보 스냅샷 시각을 확인할 수 없습니다.") from None
        else:
            snapshot_at = now
        snapshot_us = _utc_epoch_microseconds(snapshot_at, field_name="snapshot")
        last_id = ""
        if cursor:
            decoded = self._decode_cursor(cursor)
            if (
                decoded["sort"] != sort
                or int(decoded["snapshot_us"]) != snapshot_us
                or decoded["filter_key"] != filter_key
            ):
                raise DomainValidationError("시장정보 페이지 커서와 스냅샷이 일치하지 않습니다.")
            last_id = str(decoded["last_id"])

        where = ["first_seen_at_us <= ?"]
        parameters: list[object] = [snapshot_us]
        if category != "ALL":
            where.append("category = ?")
            parameters.append(category)
        if provider:
            where.append("provider_id = ?")
            parameters.append(provider)
        if related == "RELATED":
            where.append("related_symbols_json <> '[]'")
        elif related == "UNCONFIRMED":
            where.append("related_symbols_json = '[]'")
        if query:
            where.append(
                "instr(lower(title_original || ' ' || source_name || ' ' || "
                "provider_name || ' ' || related_symbols_json), ?) > 0"
            )
            parameters.append(query)
        base_where = " AND ".join(where)

        connection = self.store._connect()
        try:
            if last_id:
                anchor = connection.execute(
                    f"SELECT published_at_us, collected_at_us, info_id "  # nosec: fixed fragments
                    f"FROM market_information WHERE {base_where} AND info_id = ?",
                    (*parameters, last_id),
                ).fetchone()
                if anchor is None:
                    raise DomainValidationError("시장정보 페이지 커서를 확인할 수 없습니다.")
                if sort == "PUBLISHED_DESC":
                    if anchor["published_at_us"] is None:
                        where.append(
                            "(published_at_us IS NULL AND "
                            "(collected_at_us < ? OR "
                            "(collected_at_us = ? AND info_id > ?)))"
                        )
                        parameters.extend(
                            (anchor["collected_at_us"], anchor["collected_at_us"], last_id)
                        )
                    else:
                        where.append(
                            "(published_at_us IS NULL OR "
                            "(published_at_us IS NOT NULL AND "
                            "(published_at_us < ? OR "
                            "(published_at_us = ? AND "
                            "(collected_at_us < ? OR "
                            "(collected_at_us = ? AND info_id > ?))))))"
                        )
                        parameters.extend(
                            (
                                anchor["published_at_us"],
                                anchor["published_at_us"],
                                anchor["collected_at_us"],
                                anchor["collected_at_us"],
                                last_id,
                            )
                        )
                else:
                    where.append(
                        "(collected_at_us < ? OR "
                        "(collected_at_us = ? AND info_id > ?))"
                    )
                    parameters.extend(
                        (anchor["collected_at_us"], anchor["collected_at_us"], last_id)
                    )
            order_by = (
                "(published_at_us IS NULL) ASC, published_at_us DESC, "
                "collected_at_us DESC, info_id ASC"
                if sort == "PUBLISHED_DESC"
                else "collected_at_us DESC, info_id ASC"
            )
            rows = connection.execute(
                f"SELECT * FROM market_information WHERE {' AND '.join(where)} "  # nosec: fixed fragments
                f"ORDER BY {order_by} LIMIT ?",
                (*parameters, limit + 1),
            ).fetchall()
        finally:
            connection.close()
        has_more = len(rows) > limit
        selected_rows = rows[:limit]
        selected = [
            self._materialize_information_row(row, retention_at=snapshot_at)
            for row in selected_rows
        ]
        next_cursor = (
            self._encode_cursor(
                snapshot_us=snapshot_us,
                sort=sort,
                filter_key=filter_key,
                last_id=str(selected[-1]["info_id"]),
            )
            if has_more and selected
            else None
        )
        return {
            "items": selected,
            "next_cursor": next_cursor,
            "snapshot": snapshot_at.astimezone(UTC).isoformat(),
            "has_more": has_more,
        }

    @staticmethod
    def _encode_retention_cursor(
        *, snapshot_us: int, last_expiry_us: int, last_id: str
    ) -> str:
        raw = json.dumps(
            {
                "v": 1,
                "kind": "RETENTION_EXPIRED",
                "snapshot_us": snapshot_us,
                "last_expiry_us": last_expiry_us,
                "last_id": last_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_retention_cursor(value: str) -> dict[str, object]:
        try:
            if not value or len(value) > 500:
                raise ValueError
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or payload.get("v") != 1
                or payload.get("kind") != "RETENTION_EXPIRED"
                or not isinstance(payload.get("snapshot_us"), int)
                or not isinstance(payload.get("last_expiry_us"), int)
                or not isinstance(payload.get("last_id"), str)
            ):
                raise ValueError
            return payload
        except (ValueError, TypeError, json.JSONDecodeError, RecursionError):
            raise DomainValidationError("보존 미리보기 페이지 커서를 확인할 수 없습니다.") from None

    def retention_preview(
        self,
        *,
        now: datetime,
        status: str = "EXPIRED",
        cursor: str = "",
        limit: int = 20,
        snapshot: str = "",
    ) -> dict[str, object]:
        """Return aggregate retention state and a read-only expired-item page."""

        aware_datetime(now, field_name="now")
        if status != "EXPIRED":
            raise DomainValidationError("보존 미리보기 상태 필터를 확인할 수 없습니다.")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise DomainValidationError("보존 미리보기 페이지 크기는 1~100이어야 합니다.")
        if snapshot:
            try:
                snapshot_at = datetime.fromisoformat(snapshot)
                aware_datetime(snapshot_at, field_name="snapshot")
            except (TypeError, ValueError, OverflowError, DomainValidationError):
                raise DomainValidationError("보존 미리보기 스냅샷 시각을 확인할 수 없습니다.") from None
        else:
            snapshot_at = now
        snapshot_at = snapshot_at.astimezone(UTC)
        snapshot_us = _utc_epoch_microseconds(snapshot_at, field_name="snapshot")
        upcoming_us = _utc_epoch_microseconds(
            snapshot_at + timedelta(days=RETENTION_UPCOMING_DAYS),
            field_name="retention.upcoming_at",
        )
        last_expiry_us: int | None = None
        last_id = ""
        if cursor:
            decoded = self._decode_retention_cursor(cursor)
            if int(decoded["snapshot_us"]) != snapshot_us:
                raise DomainValidationError(
                    "보존 미리보기 페이지 커서와 스냅샷이 일치하지 않습니다."
                )
            last_expiry_us = int(decoded["last_expiry_us"])
            last_id = str(decoded["last_id"])

        connection = self.store._connect()
        try:
            self._register_retention_functions(connection)
            summary = connection.execute(
                """WITH retention AS (
                       SELECT wealthy_retention_expiry_us(
                                  first_seen_at, first_seen_at_us,
                                  collected_at, collected_at_us
                              ) AS expires_at_us,
                              wealthy_source_count(sources_json) AS source_count
                       FROM market_information
                       WHERE first_seen_at_us <= ?
                   )
                   SELECT COUNT(*) AS total_information_count,
                          COALESCE(SUM(source_count), 0) AS total_source_record_count,
                          COALESCE(SUM(CASE WHEN expires_at_us IS NULL THEN 1 ELSE 0 END), 0)
                              AS uncomputable_count,
                          COALESCE(SUM(CASE WHEN expires_at_us <= ? THEN 1 ELSE 0 END), 0)
                              AS expired_count,
                          COALESCE(SUM(CASE WHEN expires_at_us > ? AND expires_at_us <= ?
                                           THEN 1 ELSE 0 END), 0)
                              AS expiring_soon_count,
                          COALESCE(SUM(CASE WHEN expires_at_us > ? THEN 1 ELSE 0 END), 0)
                              AS within_retention_count
                   FROM retention""",
                (
                    snapshot_us,
                    snapshot_us,
                    snapshot_us,
                    upcoming_us,
                    upcoming_us,
                ),
            ).fetchone()
            page_parameters: list[object] = [snapshot_us, snapshot_us]
            page_where = [
                "first_seen_at_us <= ?",
                "retention_expires_at_us IS NOT NULL",
                "retention_expires_at_us <= ?",
            ]
            if last_expiry_us is not None:
                page_where.append(
                    "(retention_expires_at_us > ? OR "
                    "(retention_expires_at_us = ? AND info_id > ?))"
                )
                page_parameters.extend((last_expiry_us, last_expiry_us, last_id))
            rows = connection.execute(
                f"""WITH retention AS (
                       SELECT info_id, provider_id, source_name, title_original,
                              first_seen_at, first_seen_at_us,
                              collected_at, collected_at_us,
                              wealthy_retention_expiry_us(
                                     first_seen_at, first_seen_at_us,
                                     collected_at, collected_at_us
                                 ) AS retention_expires_at_us
                       FROM market_information
                   )
                   SELECT * FROM retention
                   WHERE {' AND '.join(page_where)}
                   ORDER BY retention_expires_at_us ASC, info_id ASC
                   LIMIT ?""",  # nosec: fixed SQL fragments only
                (*page_parameters, limit + 1),
            ).fetchall()
        except DomainValidationError:
            raise
        except Exception:
            raise DomainValidationError("보존 상태를 안전하게 계산할 수 없습니다.") from None
        finally:
            connection.close()

        has_more = len(rows) > limit
        selected = rows[:limit]
        items: list[dict[str, object]] = []
        for row in selected:
            metadata = _retention_metadata(row, now=snapshot_at)
            items.append(
                {
                    "info_id": str(row["info_id"]),
                    "provider_id": str(row["provider_id"]),
                    "source_name": str(row["source_name"]),
                    "title_original": str(row["title_original"]),
                    "first_collected_at": metadata["first_collected_at"],
                    "retention_expires_at": metadata["expires_at"],
                    "retention_status": metadata["retention_status"],
                    "first_collected_estimated": metadata[
                        "first_collected_at_estimated"
                    ],
                }
            )
        next_cursor = None
        if has_more and selected:
            expiry_value = selected[-1]["retention_expires_at_us"]
            if isinstance(expiry_value, int):
                next_cursor = self._encode_retention_cursor(
                    snapshot_us=snapshot_us,
                    last_expiry_us=expiry_value,
                    last_id=str(selected[-1]["info_id"]),
                )
        return {
            "summary": {
                "total_information_count": int(summary["total_information_count"]),
                "total_source_record_count": int(summary["total_source_record_count"]),
                "expired_count": int(summary["expired_count"]),
                "expiring_soon_count": int(summary["expiring_soon_count"]),
                "uncomputable_count": int(summary["uncomputable_count"]),
                "within_retention_count": int(summary["within_retention_count"]),
                "checked_at": snapshot_at.isoformat(),
                "information_count_unit": "INFORMATION_ITEM",
                "source_record_count_unit": "SOURCE_RECORD",
            },
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "snapshot": snapshot_at.isoformat(),
            "deletion_enabled": False,
            "notice": (
                "현재는 삭제 대상 확인만 지원합니다.\n"
                "실제 삭제 및 자동 정리 기능은 활성화되어 있지 않습니다."
            ),
        }

    def provider_statuses(self, *, now: datetime) -> tuple[dict[str, object], ...]:
        now_us = _utc_epoch_microseconds(now, field_name="now")
        connection = self.store._connect()
        try:
            rows = connection.execute(
                """SELECT state.*,
                          (SELECT item.collected_at
                             FROM market_information AS item
                            WHERE item.provider_id = state.provider_id
                            ORDER BY item.collected_at_us DESC, item.info_id ASC
                            LIMIT 1) AS current_data_as_of
                     FROM market_information_provider_state AS state
                    ORDER BY state.provider_id"""
            ).fetchall()
        finally:
            connection.close()
        result: list[dict[str, object]] = []
        for row in rows:
            status = str(row["status"])
            next_allowed_us = (
                int(row["next_allowed_at_us"])
                if row["next_allowed_at_us"] is not None
                else None
            )
            success_recorded = row["last_success_at_us"] is not None
            timed_out = status == "TIMED_OUT" or row["error_code"] in {
                "COLLECTION_TIMEOUT",
                "SOCKET_TIMEOUT",
            }
            retry_wait = (
                next_allowed_us is not None
                and next_allowed_us > now_us
                and status not in {"OK", "NOT_MODIFIED"}
            )
            retry_allowed = (
                status != "NEEDS_CONFIGURATION"
                and (next_allowed_us is None or next_allowed_us <= now_us)
            )
            result.append({
                "provider_id": str(row["provider_id"]),
                "institution_id": str(row["institution_id"]),
                "provider_name": str(row["provider_name"]),
                "source_name": str(row["source_name"]),
                "status": status,
                "this_collection_result": status,
                "cadence_seconds": int(row["cadence_seconds"]),
                "last_attempt_at": row["last_attempt_at"],
                "last_success_at": row["last_success_at"],
                "next_allowed_at": row["next_allowed_at"],
                "next_retry_at": (
                    row["next_allowed_at"]
                    if status in {"FAILED", "TIMED_OUT"}
                    else None
                ),
                "error_code": row["error_code"],
                "retry_wait": retry_wait,
                "retry_allowed": retry_allowed,
                "retry_after_seconds": (
                    max(0, (next_allowed_us - now_us + 999_999) // 1_000_000)
                    if retry_wait and next_allowed_us is not None
                    else 0
                ),
                "stale": (
                    row["last_success_at_us"] is not None
                    and now_us - int(row["last_success_at_us"])
                    > int(row["cadence_seconds"]) * 2 * 1_000_000
                ),
                "not_modified": bool(row["checked_not_modified"]),
                "timed_out": timed_out,
                "success_recorded": success_recorded,
                "current_data_as_of": row["current_data_as_of"],
                "using_previous_data": bool(
                    success_recorded and (status in {"FAILED", "TIMED_OUT"} or timed_out)
                ),
            })
        return tuple(result)


class MarketInformationService:
    """Provider-isolated refresh orchestration shared by auto and manual paths."""

    def __init__(
        self,
        repository: MarketInformationRepository,
        *,
        fetcher: FeedFetcher | None = None,
        sec_user_agent: Callable[[], str | None] | None = None,
        owner_guard: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        collection_timeout_seconds: float = DEFAULT_COLLECTION_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(collection_timeout_seconds, (int, float)) or not (
            0 < float(collection_timeout_seconds) < 120
        ):
            raise DomainValidationError("시장정보 전체 수집 제한시간을 확인할 수 없습니다.")
        self.repository = repository
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.collection_timeout_seconds = float(collection_timeout_seconds)
        self.fetcher = fetcher or OfficialFeedFetcher(monotonic=monotonic)
        self.sec_user_agent = sec_user_agent or (lambda: None)
        self.owner_guard = owner_guard or (lambda: None)
        self.clock = clock
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._background_enabled = False
        self._last_sec_request_monotonic = 0.0
        self._last_refresh_result: dict[str, object] | None = None
        self.repository.ensure_provider_rows(PROVIDER_SPECS, now=self.clock())

    @property
    def refreshing(self) -> bool:
        return self._refresh_lock.locked()

    def _configured_user_agent(self, spec: ProviderSpec) -> str | None:
        if not spec.requires_sec_contact:
            return "WealthyLocalMarketInfo/0.3 (local metadata reader)"
        try:
            value = self.sec_user_agent()
        except Exception:
            return None
        if not valid_sec_user_agent(value):
            return None
        return str(value)

    def _require_single_owner(self) -> None:
        try:
            self.owner_guard()
        except DomainValidationError:
            raise _LocalOwnerGuardFailed from None

    def refresh(self, *, manual: bool = False) -> dict[str, object]:
        # Before any provider work, let the established local-owner guard
        # raise its normal safe domain error directly.
        self.owner_guard()
        started = self.clock()
        aware_datetime(started, field_name="refresh.started_at")
        if not self._refresh_lock.acquire(blocking=False):
            return {
                "status": "ALREADY_RUNNING",
                "new_items": 0,
                "started_at": started.isoformat(),
                "finished_at": started.isoformat(),
                "provider_results": (),
            }
        deadline_monotonic = self.monotonic() + self.collection_timeout_seconds
        owner = secrets.token_urlsafe(18)
        outcomes: list[str] = []
        provider_results: list[dict[str, object]] = []
        inserted_total = 0

        def record(
            spec: ProviderSpec,
            result: str,
            *,
            error_code: str | None = None,
            previous_data_used: bool = False,
        ) -> None:
            outcomes.append(result)
            provider_results.append(
                {
                    "provider_id": spec.provider_id,
                    "result": result,
                    "error_code": error_code,
                    "using_previous_data": previous_data_used,
                }
            )

        try:
            for spec in PROVIDER_SPECS:
                self._require_single_owner()
                now = self.clock()
                state = self.repository.state(spec.provider_id)
                if state is None:
                    record(spec, "FAILED", error_code="STATE_UNAVAILABLE")
                    continue
                if state["next_allowed_at_us"] is not None and _utc_epoch_microseconds(now, field_name="now") < int(state["next_allowed_at_us"]):
                    record(
                        spec,
                        "WAITING",
                        previous_data_used=bool(state["last_success_at"]),
                    )
                    continue
                if self.monotonic() >= deadline_monotonic:
                    # The provider was never started.  Keep its persisted
                    # last-attempt/result intact; only this run records that
                    # the global budget prevented an attempt.
                    record(
                        spec,
                        "NOT_STARTED_TIME_LIMIT",
                        error_code="COLLECTION_TIME_LIMIT_REACHED",
                        previous_data_used=bool(state["last_success_at"]),
                    )
                    continue
                user_agent = self._configured_user_agent(spec)
                if user_agent is None:
                    self.repository.update_state(
                        spec,
                        status="NEEDS_CONFIGURATION",
                        now=now,
                        success=False,
                        next_allowed_at=now,
                        error_code="SEC_CONTACT_REQUIRED",
                        preserve_validators=True,
                        count_failure=False,
                    )
                    record(
                        spec,
                        "NEEDS_CONFIGURATION",
                        error_code="SEC_CONTACT_REQUIRED",
                        previous_data_used=bool(state["last_success_at"]),
                    )
                    continue
                if not self.repository.claim(spec.provider_id, owner, now=now):
                    record(spec, "ALREADY_RUNNING")
                    continue
                owner_failed = False
                try:
                    # Another process can complete this provider between our
                    # optimistic state read and successful claim.  Re-read
                    # under the claim so that its new cooldown and validators
                    # become authoritative before any network request.
                    self._require_single_owner()
                    claim_checked_at = self.clock()
                    aware_datetime(
                        claim_checked_at, field_name="refresh.claim_checked_at"
                    )
                    claimed_state = self.repository.state(spec.provider_id)
                    if claimed_state is None:
                        record(spec, "FAILED", error_code="STATE_UNAVAILABLE")
                        continue
                    if (
                        claimed_state["next_allowed_at_us"] is not None
                        and _utc_epoch_microseconds(
                            claim_checked_at, field_name="claim_checked_at"
                        )
                        < int(claimed_state["next_allowed_at_us"])
                    ):
                        record(
                            spec,
                            "WAITING",
                            previous_data_used=bool(claimed_state["last_success_at"]),
                        )
                        continue
                    state = claimed_state
                    _deadline_remaining(deadline_monotonic, self.monotonic)
                    if spec.institution_id == "sec":
                        remaining = 0.11 - (
                            self.monotonic() - self._last_sec_request_monotonic
                        )
                        if remaining > 0:
                            budget = _deadline_remaining(
                                deadline_monotonic, self.monotonic
                            )
                            if budget is not None and remaining >= budget:
                                raise MarketInformationFetchError(
                                    "COLLECTION_TIMEOUT"
                                )
                            self.sleeper(remaining)
                        self._last_sec_request_monotonic = self.monotonic()
                    response = self.fetcher.fetch(
                        spec,
                        etag=state["etag"],
                        last_modified=state["last_modified"],
                        user_agent=user_agent,
                        deadline_monotonic=deadline_monotonic,
                    )
                    _deadline_remaining(deadline_monotonic, self.monotonic)
                    received = response.received_at
                    aware_datetime(received, field_name="received_at")
                    if response.status == 304:
                        self.repository.update_state(
                            spec,
                            status="NOT_MODIFIED",
                            now=received,
                            success=True,
                            next_allowed_at=received + timedelta(seconds=spec.cadence_seconds),
                            etag=response.headers.get("etag") or state["etag"],
                            last_modified=response.headers.get("last-modified") or state["last_modified"],
                            http_status=304,
                            not_modified=True,
                        )
                        record(spec, "NOT_MODIFIED")
                        continue
                    if response.status != 200:
                        raise MarketInformationFetchError(
                            f"HTTP_{response.status}", http_status=response.status
                        )
                    parsed = parse_official_feed(spec, response.body, collected_at=received)
                    _deadline_remaining(deadline_monotonic, self.monotonic)
                    self._require_single_owner()
                    inserted_total += self.repository.save_items(parsed, now=received)
                    self.repository.update_state(
                        spec,
                        status="OK",
                        now=received,
                        success=True,
                        next_allowed_at=received + timedelta(seconds=spec.cadence_seconds),
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                        http_status=200,
                    )
                    record(spec, "OK")
                except _LocalOwnerGuardFailed:
                    owner_failed = True
                    raise
                except Exception as exc:
                    failure_now = self.clock()
                    state_after = self.repository.state(spec.provider_id)
                    failures = int(state_after["consecutive_failures"]) + 1 if state_after else 1
                    delay = min(spec.cadence_seconds * (2 ** min(failures, 4)), 21_600)
                    if isinstance(exc, MarketInformationFetchError) and exc.retry_after_seconds is not None:
                        delay = max(delay, exc.retry_after_seconds)
                    code = (
                        exc.code
                        if isinstance(exc, MarketInformationFetchError)
                        else "INVALID_FEED"
                        if isinstance(exc, DomainValidationError)
                        else "FETCH_FAILED"
                    )
                    http_status = exc.http_status if isinstance(exc, MarketInformationFetchError) else None
                    timed_out = code in {"COLLECTION_TIMEOUT", "SOCKET_TIMEOUT"}
                    if timed_out:
                        delay = _TIMEOUT_RETRY_SECONDS
                    self.repository.update_state(
                        spec,
                        status="TIMED_OUT" if timed_out else "FAILED",
                        now=failure_now,
                        success=False,
                        next_allowed_at=failure_now + timedelta(seconds=delay),
                        error_code=code,
                        http_status=http_status,
                        preserve_validators=True,
                        count_failure=not timed_out,
                    )
                    record(
                        spec,
                        "TIMED_OUT" if timed_out else "FAILED",
                        error_code=code,
                        previous_data_used=bool(
                            state_after is not None and state_after["last_success_at"]
                        ),
                    )
                finally:
                    # A mid-request W4 owner failure is fail-closed: do not
                    # perform even claim-cleanup writes until the state is
                    # single-owner again.  The short claim expires safely.
                    if not owner_failed:
                        self.repository.release(spec.provider_id, owner)
        except _LocalOwnerGuardFailed:
            raise DomainValidationError(
                "로컬 단일 사용자 상태를 확인할 수 없습니다."
            ) from None
        finally:
            finished = self.clock()
            self._refresh_lock.release()
        incomplete = [
            value for value in outcomes if value == "NOT_STARTED_TIME_LIMIT"
        ]
        attempted = [
            value
            for value in outcomes
            if value
            not in {"WAITING", "NOT_STARTED_TIME_LIMIT", "NEEDS_CONFIGURATION"}
        ]
        needs_configuration = any(
            value == "NEEDS_CONFIGURATION" for value in outcomes
        )
        successes = [value for value in attempted if value in {"OK", "NOT_MODIFIED"}]
        if not attempted:
            status = (
                "TIMED_OUT"
                if incomplete
                else "NEEDS_CONFIGURATION"
                if needs_configuration
                else "RATE_LIMITED"
            )
        elif len(successes) == len(attempted) and not incomplete:
            status = "OK"
        elif successes:
            status = "PARTIAL"
        elif all(value == "TIMED_OUT" for value in attempted):
            status = "TIMED_OUT"
        else:
            status = "FAILED"
        result = {
            "status": status,
            "new_items": inserted_total,
            "started_at": started.astimezone(UTC).isoformat(),
            "finished_at": finished.astimezone(UTC).isoformat(),
            "manual": bool(manual),
            "provider_results": tuple(provider_results),
            "failed_provider_ids": tuple(
                item["provider_id"]
                for item in provider_results
                if item["result"] == "FAILED"
            ),
            "timed_out_provider_ids": tuple(
                item["provider_id"]
                for item in provider_results
                if item["result"] == "TIMED_OUT"
            ),
            "incomplete_provider_ids": tuple(
                item["provider_id"]
                for item in provider_results
                if item["result"] == "NOT_STARTED_TIME_LIMIT"
            ),
            "using_previous_data": any(
                bool(item["using_previous_data"]) for item in provider_results
            ),
        }
        self._last_refresh_result = result
        return result

    def list_items(self, **filters: object) -> dict[str, object]:
        return self.repository.list_items(now=self.clock(), **filters)

    def retention_preview(self, **filters: object) -> dict[str, object]:
        return self.repository.retention_preview(now=self.clock(), **filters)

    def status(self) -> dict[str, object]:
        now = self.clock()
        raw_providers = self.repository.provider_statuses(now=now)
        latest_results = {
            str(item["provider_id"]): item
            for item in (
                self._last_refresh_result.get("provider_results", ())
                if self._last_refresh_result is not None
                else ()
            )
            if isinstance(item, dict) and isinstance(item.get("provider_id"), str)
        }
        providers = tuple(
            {
                **item,
                "this_collection_result": str(
                    latest_results[str(item["provider_id"])]["result"]
                ),
                "this_collection_error_code": latest_results[
                    str(item["provider_id"])
                ].get("error_code"),
                "using_previous_data": bool(
                    latest_results[str(item["provider_id"])].get(
                        "using_previous_data", False
                    )
                ),
            }
            if str(item["provider_id"]) in latest_results
            else {
                **item,
                "this_collection_result": "NOT_RUN_IN_PROCESS",
                "this_collection_error_code": None,
            }
            for item in raw_providers
        )
        last_success = max(
            (str(item["last_success_at"]) for item in providers if item["last_success_at"]),
            default=None,
        )
        last_attempt = max(
            (str(item["last_attempt_at"]) for item in providers if item["last_attempt_at"]),
            default=None,
        )
        current_data_as_of = max(
            (
                str(item["current_data_as_of"])
                for item in providers
                if item["current_data_as_of"]
            ),
            default=None,
        )
        configured = [item for item in providers if item["status"] != "NEEDS_CONFIGURATION"]
        unconfigured = [item for item in providers if item["status"] == "NEEDS_CONFIGURATION"]
        failed = [item for item in configured if item["status"] == "FAILED"]
        timed_out = [item for item in configured if item["timed_out"]]
        successful = [
            item for item in configured if item["status"] in {"OK", "NOT_MODIFIED"}
        ]
        if not providers:
            overall_state = "NO_SOURCES"
        elif not configured:
            overall_state = "NEEDS_CONFIGURATION"
        elif successful and (failed or timed_out or unconfigured):
            overall_state = "PARTIAL"
        elif timed_out and len(timed_out) == len(configured):
            overall_state = "TIMED_OUT"
        elif failed and len(failed) == len(configured):
            overall_state = "FAILED"
        elif successful:
            overall_state = "OK"
        else:
            overall_state = "WAITING"
        next_retry_at = min(
            (
                str(item["next_retry_at"])
                for item in providers
                if item["next_retry_at"] is not None
            ),
            default=None,
        )
        current_result = (
            str(self._last_refresh_result["status"])
            if self._last_refresh_result is not None
            else "NOT_RUN_IN_PROCESS"
        )
        incomplete_provider_ids = (
            tuple(self._last_refresh_result.get("incomplete_provider_ids", ()))
            if self._last_refresh_result is not None
            else ()
        )
        return {
            "automatic_state": (
                "RUNNING"
                if self.refreshing
                else "ENABLED_WHILE_BACKEND_RUNNING"
                if self._background_enabled
                else "STOPPED"
            ),
            "refreshing": self.refreshing,
            "last_attempt_at": last_attempt,
            "last_success_at": last_success,
            "current_data_as_of": current_data_as_of,
            "current_result": current_result,
            "overall_state": overall_state,
            "partial": (
                bool(successful) and bool(failed or timed_out or unconfigured)
            )
            or current_result == "PARTIAL",
            "failed_provider_ids": (
                tuple(self._last_refresh_result.get("failed_provider_ids", ()))
                if self._last_refresh_result is not None
                else tuple(str(item["provider_id"]) for item in failed)
            ),
            "timed_out_provider_ids": (
                tuple(self._last_refresh_result.get("timed_out_provider_ids", ()))
                if self._last_refresh_result is not None
                else tuple(str(item["provider_id"]) for item in timed_out)
            ),
            "incomplete_provider_ids": incomplete_provider_ids,
            "using_previous_data": any(
                bool(item["using_previous_data"]) for item in providers
            ),
            "retry_allowed": (
                not self.refreshing
                and any(bool(item["retry_allowed"]) for item in providers)
            ),
            "next_retry_at": next_retry_at,
            "collection_timeout_seconds": self.collection_timeout_seconds,
            "timeout_limitations": (
                "전체 제한은 연결·TLS·청크 읽기에 협력적으로 적용됩니다. "
                "운영체제 DNS 해석 호출 자체는 진행 중 강제 취소할 수 없습니다."
            ),
            "providers": providers,
            "runtime_notice": "백엔드 실행 중에만 자동 갱신합니다. PC 종료·절전 중 실행을 보장하지 않습니다.",
        }

    def start_background(self, *, poll_seconds: int = 30) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._background_enabled = True

        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.refresh(manual=False)
                except Exception:
                    # Provider/status errors are persisted without terminating the server.
                    pass
                self._stop.wait(max(10, poll_seconds))

        self._thread = threading.Thread(target=run, name="market-information-refresh", daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.collection_timeout_seconds + 5)
        self._thread = None
        self._background_enabled = False
