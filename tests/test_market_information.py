from __future__ import annotations

import http.client
import multiprocessing
import socket
import sqlite3
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from asset_assistant.domain import DomainValidationError
from asset_assistant.market_data import (
    SCHEMA_VERSION,
    MarketDataStore,
    _utc_epoch_microseconds,
)
from asset_assistant.market_information import (
    FetchResponse,
    MarketInformationFetchError,
    MarketInformationItem,
    MarketInformationRepository,
    MarketInformationService,
    OfficialFeedFetcher,
    PROVIDER_BY_ID,
    PublishedPrecision,
    _DeadlineSocketProxy,
    _NoUnsafeRedirects,
    _PinnedHTTPSConnection,
    _PinnedHTTPSHandler,
    _assert_public_dns,
    parse_official_feed,
    validate_public_url,
)


BASE_TIME = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
TEMP_ROOT = "/dev/shm" if Path("/dev/shm").is_dir() else None


class MutableClock:
    def __init__(self, value: datetime = BASE_TIME) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class MutableMonotonic:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class SequenceFetcher:
    """Network-free feed adapter whose outcomes are supplied by each test."""

    def __init__(self, *outcomes: FetchResponse | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def fetch(
        self,
        spec,
        *,
        etag: str | None,
        last_modified: str | None,
        user_agent: str,
        deadline_monotonic: float | None = None,
    ) -> FetchResponse:
        with self._lock:
            self.calls.append(
                {
                    "provider_id": spec.provider_id,
                    "etag": etag,
                    "last_modified": last_modified,
                    "user_agent": user_agent,
                    "deadline_monotonic": deadline_monotonic,
                }
            )
            if not self.outcomes:
                raise AssertionError("fixture fetch outcome exhausted")
            outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BlockingFetcher:
    def __init__(self, response: FetchResponse) -> None:
        self.response = response
        self.entered = threading.Event()
        self.release = threading.Event()
        self.call_count = 0
        self._lock = threading.Lock()

    def fetch(self, spec, **kwargs) -> FetchResponse:  # type: ignore[no-untyped-def]
        del spec, kwargs
        with self._lock:
            self.call_count += 1
        self.entered.set()
        if not self.release.wait(5):
            raise AssertionError("blocking fixture was not released")
        return self.response


class DeadlineAdvancingFetcher:
    """Return one success, then advance beyond the supplied run deadline."""

    def __init__(self, monotonic: MutableMonotonic) -> None:
        self.monotonic = monotonic
        self.calls: list[tuple[str, float | None]] = []

    def fetch(self, spec, **kwargs) -> FetchResponse:  # type: ignore[no-untyped-def]
        deadline = kwargs.get("deadline_monotonic")
        self.calls.append((spec.provider_id, deadline))
        if len(self.calls) == 1:
            return FetchResponse(
                200,
                {},
                rss_body(guid="within-budget"),
                BASE_TIME,
            )
        if isinstance(deadline, (int, float)):
            self.monotonic.value = float(deadline) + 0.001
        return FetchResponse(
            200,
            {},
            rss_body(
                title="Must not be committed after timeout",
                guid="after-budget",
            ),
            BASE_TIME,
        )


class TimeoutAdvancingFetcher:
    """Consume the run budget on the first call without performing I/O."""

    def __init__(self, monotonic: MutableMonotonic) -> None:
        self.monotonic = monotonic
        self.calls: list[str] = []

    def fetch(self, spec, **kwargs) -> FetchResponse:  # type: ignore[no-untyped-def]
        self.calls.append(spec.provider_id)
        deadline = kwargs.get("deadline_monotonic")
        if isinstance(deadline, (int, float)):
            self.monotonic.value = float(deadline) + 0.001
        raise MarketInformationFetchError("COLLECTION_TIMEOUT")


class PausingStateRepository(MarketInformationRepository):
    def __init__(self, store, ready, resume) -> None:  # type: ignore[no-untyped-def]
        super().__init__(store)
        self.ready = ready
        self.resume = resume
        self.paused = False

    def state(self, provider_id: str):  # type: ignore[no-untyped-def]
        current = super().state(provider_id)
        if provider_id == "bea" and not self.paused:
            self.paused = True
            self.ready.set()
            if not self.resume.wait(10):
                raise AssertionError("cross-process refresh fixture timed out")
        return current


def cross_process_refresh_worker(db_path: str, ready, resume, result_queue) -> None:  # type: ignore[no-untyped-def]
    try:
        with patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"],),
        ):
            fetcher = SequenceFetcher(
                FetchResponse(200, {}, rss_body(guid="worker-duplicate"), BASE_TIME)
            )
            repository = PausingStateRepository(
                MarketDataStore(Path(db_path)), ready, resume
            )
            service = MarketInformationService(
                repository, fetcher=fetcher, clock=MutableClock()
            )
            result = service.refresh(manual=False)
            result_queue.put(
                {
                    "status": result["status"],
                    "fetch_calls": len(fetcher.calls),
                    "error": None,
                }
            )
    except Exception as exc:
        result_queue.put(
            {
                "status": None,
                "fetch_calls": None,
                "error": type(exc).__name__,
            }
        )


def rss_body(
    *,
    title: str = "Gross Domestic Product Release",
    guid: str = "bea-release-1",
    link: str = "https://apps.bea.gov/news/2026/release-1.htm",
    published: str | None = "Tue, 13 Oct 2026 05:30:00 +0000",
) -> bytes:
    published_xml = f"<pubDate>{published}</pubDate>" if published is not None else ""
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<rss version='2.0'><channel><title>BEA</title><item>"
        f"<title>{title}</title><guid>{guid}</guid><link>{link}</link>"
        f"{published_xml}<description>본문은 저장하지 않습니다.</description>"
        "</item></channel></rss>"
    ).encode("utf-8")


def item(
    key: str,
    *,
    provider_id: str = "bea",
    title: str | None = None,
    category: str = "ECONOMY",
    published_at: datetime | None = BASE_TIME,
    collected_at: datetime = BASE_TIME,
    related_symbols: tuple[str, ...] = (),
    source_name: str | None = None,
    original_url: str | None = None,
    is_sample: bool = False,
) -> MarketInformationItem:
    spec = PROVIDER_BY_ID[provider_id]
    return MarketInformationItem(
        canonical_key=key,
        provider_id=provider_id,
        provider_name=spec.provider_name,
        source_name=source_name or spec.source_name,
        title_original=title or key,
        language="en",
        category=category,
        related_symbols=related_symbols,
        original_url=original_url or spec.endpoint,
        published_at=published_at,
        collected_at=collected_at,
        provider_item_id=key,
        source_document_id=None,
        published_precision=(
            PublishedPrecision.DATETIME
            if published_at is not None
            else PublishedPrecision.UNKNOWN
        ),
        is_sample=is_sample,
    )


class MarketInformationParsingTests(unittest.TestCase):
    def test_rss_preserves_datetime_date_and_unknown_precision(self) -> None:
        spec = PROVIDER_BY_ID["bea"]
        body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item><title>GDP &amp; income</title><guid>bea-1</guid>
            <link>https://apps.bea.gov/news/2026/gdp.htm</link>
            <pubDate>Tue, 13 Oct 2026 01:30:00 -0400</pubDate></item>
          <item><title>Industry accounts</title><guid>bea-2</guid>
            <link>https://apps.bea.gov/news/2026/industry.htm</link>
            <date>2026-10-12</date></item>
          <item><title>Schedule notice</title><guid>bea-3</guid>
            <link>https://apps.bea.gov/news/2026/schedule.htm</link></item>
        </channel></rss>"""

        parsed = parse_official_feed(spec, body, collected_at=BASE_TIME)

        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0].title_original, "GDP & income")
        self.assertEqual(parsed[0].published_at, datetime(2026, 10, 13, 5, 30, tzinfo=UTC))
        self.assertIs(parsed[0].published_precision, PublishedPrecision.DATETIME)
        self.assertEqual(parsed[1].published_at, datetime(2026, 10, 12, tzinfo=UTC))
        self.assertIs(parsed[1].published_precision, PublishedPrecision.DATE)
        self.assertIsNone(parsed[2].published_at)
        self.assertIs(parsed[2].published_precision, PublishedPrecision.UNKNOWN)
        self.assertTrue(all(value.collected_at == BASE_TIME for value in parsed))

    def test_atom_namespace_extracts_accession_and_stable_id(self) -> None:
        spec = PROVIDER_BY_ID["sec_edgar"]
        body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>10-K filing 0000123456-26-000001</title>
            <id>urn:sec:0000123456-26-000001</id>
            <updated>2026-10-13T01:02:03-04:00</updated>
            <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/123456/0000123456-26-000001-index.html" />
          </entry>
        </feed>"""

        first = parse_official_feed(spec, body, collected_at=BASE_TIME)[0]
        second = parse_official_feed(
            spec, body, collected_at=BASE_TIME + timedelta(minutes=1)
        )[0]

        self.assertEqual(first.source_document_id, "0000123456-26-000001")
        self.assertEqual(first.canonical_key, "sec:document:0000123456-26-000001")
        self.assertEqual(first.info_id, second.info_id)
        self.assertEqual(first.published_at, datetime(2026, 10, 13, 5, 2, 3, tzinfo=UTC))
        self.assertIs(first.published_precision, PublishedPrecision.DATETIME)
        self.assertNotEqual(first.info_id, replace(first, is_sample=True).info_id)

    def test_accession_shaped_title_alone_does_not_merge_documents(self) -> None:
        spec = PROVIDER_BY_ID["sec_press"]
        body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item>
          <title>Reference number 0000123456-26-000001 in a press title</title>
          <guid>press-release-2026-99</guid>
          <link>https://www.sec.gov/newsroom/press-releases/2026-99</link>
        </item></channel></rss>"""

        parsed = parse_official_feed(spec, body, collected_at=BASE_TIME)[0]

        self.assertIsNone(parsed.source_document_id)
        self.assertEqual(parsed.canonical_key, "sec_press:item:press-release-2026-99")

    def test_malformed_and_dtd_xml_are_rejected_without_input_echo(self) -> None:
        spec = PROVIDER_BY_ID["bea"]
        marker = "sensitive-fixture-marker"
        payloads = (
            f"<rss><channel><item><title>{marker}</title>".encode(),
            (
                "<!DOCTYPE rss [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"
                f"<rss><channel><item><title>{marker}&xxe;</title></item></channel></rss>"
            ).encode(),
        )
        for payload in payloads:
            with self.subTest(payload=payload[:20]):
                with self.assertRaises(DomainValidationError) as raised:
                    parse_official_feed(spec, payload, collected_at=BASE_TIME)
                self.assertNotIn(marker, str(raised.exception))
                self.assertNotIn("/etc/passwd", str(raised.exception))

    def test_utf16_doctype_and_entity_are_rejected_at_parser_level(self) -> None:
        spec = PROVIDER_BY_ID["bea"]
        marker = "utf16-sensitive-marker"
        variants = (
            ("utf-16", "UTF-16"),
            ("utf-16le", "UTF-16LE"),
            ("utf-16be", "UTF-16BE"),
        )
        for codec, declaration in variants:
            with self.subTest(codec=codec):
                unsafe = (
                    f'<?xml version="1.0" encoding="{declaration}"?>'
                    '<!DOCTYPE r [<!ENTITY a "X">]>'
                    f"<rss><channel><item><title>{marker}&a;</title>"
                    "<guid>g</guid></item></channel></rss>"
                ).encode(codec)
                with self.assertRaises(DomainValidationError) as raised:
                    parse_official_feed(spec, unsafe, collected_at=BASE_TIME)
                self.assertNotIn(marker, str(raised.exception))

                safe = (
                    f'<?xml version="1.0" encoding="{declaration}"?>'
                    "<rss><channel><item><title>Safe UTF feed</title>"
                    "<guid>safe</guid></item></channel></rss>"
                ).encode(codec)
                parsed = parse_official_feed(spec, safe, collected_at=BASE_TIME)
                self.assertEqual(parsed[0].title_original, "Safe UTF feed")

    def test_oversized_and_overdeep_xml_are_rejected(self) -> None:
        spec = PROVIDER_BY_ID["bea"]
        oversized = b"<rss>" + b"x" * (2 * 1024 * 1024) + b"</rss>"
        overdeep = ("<n>" * 66 + "</n>" * 66).encode()
        for payload in (oversized, overdeep):
            with self.subTest(size=len(payload)):
                with self.assertRaises(DomainValidationError):
                    parse_official_feed(spec, payload, collected_at=BASE_TIME)

    def test_public_url_structure_allows_only_registered_safe_destinations(self) -> None:
        allowed = frozenset({"www.sec.gov"})
        self.assertEqual(
            validate_public_url(
                "https://www.sec.gov/news/release#fragment", allowed_hosts=allowed
            ),
            "https://www.sec.gov/news/release",
        )
        for unsafe in (
            "javascript:alert(1)",
            "file:///etc/passwd",
            "https://user:password@www.sec.gov/news",
            "https://localhost/news",
            "http://127.0.0.1/news",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/news",
            "https://www.sec.gov:444/news",
            "https://evil.example/news",
            "https://www.sec.gov/news\nX-Test: value",
        ):
            with self.subTest(url=unsafe):
                self.assertIsNone(validate_public_url(unsafe, allowed_hosts=allowed))

    def test_fetcher_blocks_private_dns_before_open_and_unsafe_redirect(self) -> None:
        spec = PROVIDER_BY_ID["bea"]
        with patch(
            "asset_assistant.market_information.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
        ), patch(
            "asset_assistant.market_information.urllib.request.build_opener"
        ) as opener:
            with self.assertRaises(MarketInformationFetchError) as raised:
                OfficialFeedFetcher().fetch(
                    spec,
                    etag=None,
                    last_modified=None,
                    user_agent="fixture",
                )
            self.assertEqual(raised.exception.code, "UNSAFE_DESTINATION")
            opener.assert_not_called()

        handler = _NoUnsafeRedirects(spec)
        with self.assertRaises(MarketInformationFetchError) as redirect:
            handler.redirect_request(
                None,
                None,
                302,
                "redirect",
                {},
                "http://127.0.0.1/private",
            )
        self.assertEqual(redirect.exception.code, "UNSAFE_REDIRECT")

    def test_dns_result_is_not_used_after_collection_deadline(self) -> None:
        monotonic = MutableMonotonic()

        def after_resolve() -> float:
            current = monotonic.value
            monotonic.value += 2.0
            return current

        public = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            )
        ]
        with patch(
            "asset_assistant.market_information.socket.getaddrinfo",
            return_value=public,
        ):
            with self.assertRaises(MarketInformationFetchError) as raised:
                _assert_public_dns(
                    "apps.bea.gov",
                    443,
                    deadline_monotonic=1.0,
                    monotonic=after_resolve,
                )
        self.assertEqual(raised.exception.code, "COLLECTION_TIMEOUT")

    def test_fetcher_distinguishes_socket_timeout_from_other_network_error(self) -> None:
        spec = PROVIDER_BY_ID["bea"]
        public = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            )
        ]
        cases = (
            (urllib.error.URLError(socket.timeout("fixture")), "SOCKET_TIMEOUT"),
            (urllib.error.URLError(OSError("fixture")), "NETWORK_ERROR"),
        )
        for failure, expected in cases:
            with self.subTest(expected=expected), patch(
                "asset_assistant.market_information.socket.getaddrinfo",
                return_value=public,
            ), patch(
                "asset_assistant.market_information.urllib.request.build_opener"
            ) as build_opener:
                build_opener.return_value.open.side_effect = failure
                with self.assertRaises(MarketInformationFetchError) as raised:
                    OfficialFeedFetcher().fetch(
                        spec,
                        etag=None,
                        last_modified=None,
                        user_agent="fixture",
                    )
            self.assertEqual(raised.exception.code, expected)

    def test_fetcher_pins_first_public_dns_answer_and_keeps_tls_hostname(self) -> None:
        spec = PROVIDER_BY_ID["bea"]
        public = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            )
        ]
        rebound = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 443),
            )
        ]
        with patch(
            "asset_assistant.market_information.socket.getaddrinfo",
            side_effect=(public, rebound),
        ) as resolver:
            addresses = _assert_public_dns("apps.bea.gov", 443)
            handler = _PinnedHTTPSHandler(
                spec,
                initial_host="apps.bea.gov",
                initial_port=443,
                initial_addresses=addresses,
            )
            captured: list[_PinnedHTTPSConnection] = []

            def capture_connection(factory, request, **kwargs):  # type: ignore[no-untyped-def]
                connection = factory(
                    request.host, timeout=1.0, context=kwargs["context"]
                )
                captured.append(connection)
                return connection

            handler.do_open = capture_connection  # type: ignore[method-assign]
            handler.https_open(urllib.request.Request(spec.endpoint))

        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(captured[0].host, "apps.bea.gov")
        self.assertEqual(captured[0].pinned_addresses[0][3], ("8.8.8.8", 443))
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)

        class FakeSocket:
            def __init__(self) -> None:
                self.connected_to = None
                self.closed = False

            def settimeout(self, _timeout) -> None:  # type: ignore[no-untyped-def]
                pass

            def setsockopt(self, *_args) -> None:  # type: ignore[no-untyped-def]
                pass

            def bind(self, _address) -> None:  # type: ignore[no-untyped-def]
                pass

            def connect(self, address) -> None:  # type: ignore[no-untyped-def]
                self.connected_to = address

            def close(self) -> None:
                self.closed = True

        class FakeTlsContext:
            def __init__(self) -> None:
                self.server_hostname = None

            def wrap_socket(self, raw_socket, *, server_hostname):  # type: ignore[no-untyped-def]
                self.server_hostname = server_hostname
                return raw_socket

        raw_socket = FakeSocket()
        tls_context = FakeTlsContext()
        connection = _PinnedHTTPSConnection(
            "apps.bea.gov",
            pinned_addresses=addresses,
            timeout=1.0,
            context=tls_context,
        )
        with patch(
            "asset_assistant.market_information.socket.socket",
            return_value=raw_socket,
        ):
            connection.connect()
        self.assertEqual(raw_socket.connected_to, ("8.8.8.8", 443))
        self.assertEqual(tls_context.server_hostname, "apps.bea.gov")
        self.assertIsInstance(connection.sock, _DeadlineSocketProxy)

    def test_absolute_deadline_stops_slow_drip_headers_and_body(self) -> None:
        class SlowDripSocket:
            def __init__(self, monotonic: MutableMonotonic) -> None:
                self.monotonic = monotonic
                self.timeout: float | None = None
                self.timeouts: list[float | None] = []

            def settimeout(self, timeout: float | None) -> None:
                self.timeout = timeout
                self.timeouts.append(timeout)

            def recv_into(self, buffer) -> int:  # type: ignore[no-untyped-def]
                delay = 0.4
                if self.timeout is not None and self.timeout <= delay:
                    self.monotonic.value += self.timeout
                    raise socket.timeout("fixture absolute deadline")
                self.monotonic.value += delay
                buffer[0] = ord("x")
                return 1

            def readinto(self, buffer) -> int:  # type: ignore[no-untyped-def]
                return self.recv_into(buffer)

            def makefile(self, mode="rb", buffering=0):  # type: ignore[no-untyped-def]
                self.assert_makefile_arguments = (mode, buffering)
                return self

            def close(self) -> None:
                pass

            def fileno(self) -> int:
                return 1

        for operation in ("header", "body"):
            with self.subTest(operation=operation):
                monotonic = MutableMonotonic()
                slow_socket = SlowDripSocket(monotonic)
                proxy = _DeadlineSocketProxy(
                    slow_socket,  # type: ignore[arg-type]
                    deadline_monotonic=1.0,
                    monotonic=monotonic,
                    operation_timeout=10.0,
                )
                stream = proxy.makefile("rb")
                with self.assertRaises(MarketInformationFetchError) as raised:
                    if operation == "header":
                        stream.readline()  # type: ignore[attr-defined]
                    else:
                        stream.read(64 * 1024)  # type: ignore[attr-defined]
                self.assertEqual(raised.exception.code, "COLLECTION_TIMEOUT")
                self.assertAlmostEqual(monotonic.value, 1.0)
                self.assertGreaterEqual(len(slow_socket.timeouts), 3)
                self.assertEqual(slow_socket.timeouts[0], 1.0)
                self.assertLess(slow_socket.timeouts[-1], slow_socket.timeouts[0])
                stream.close()  # type: ignore[attr-defined]

    def test_official_fetcher_enforces_deadline_through_loopback_tls(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-slow-tls-", dir=TEMP_ROOT
        ) as root:
            root_path = Path(root)
            key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
            name = x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, "apps.bea.gov")]
            )
            valid_from = datetime.now(UTC) - timedelta(minutes=1)
            certificate = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(valid_from)
                .not_valid_after(valid_from + timedelta(days=1))
                .add_extension(
                    x509.SubjectAlternativeName([x509.DNSName("apps.bea.gov")]),
                    critical=False,
                )
                .add_extension(
                    x509.BasicConstraints(ca=True, path_length=None),
                    critical=True,
                )
                .sign(key, hashes.SHA256())
            )
            cert_path = root_path / "fixture-cert.pem"
            key_path = root_path / "fixture-key.pem"
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            key_path.chmod(0o600)

            public = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 443),
                )
            ]
            for operation in ("header", "body"):
                with self.subTest(operation=operation):
                    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(1)
                    port = int(listener.getsockname()[1])
                    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    server_context.load_cert_chain(cert_path, key_path)
                    server_names: list[str] = []
                    requests: list[bytes] = []
                    server_context.set_servername_callback(
                        lambda _socket, server_name, _context: server_names.append(
                            str(server_name)
                        )
                    )

                    def serve_slowly() -> None:
                        try:
                            raw, _address = listener.accept()
                            with raw, server_context.wrap_socket(
                                raw, server_side=True
                            ) as tls:
                                request = b""
                                while b"\r\n\r\n" not in request:
                                    part = tls.recv(4_096)
                                    if not part:
                                        return
                                    request += part
                                requests.append(request)
                                headers = (
                                    b"HTTP/1.1 200 OK\r\n"
                                    b"Content-Type: application/rss+xml\r\n"
                                    b"Content-Length: 100000\r\n\r\n"
                                )
                                if operation == "header":
                                    payload = headers
                                else:
                                    tls.sendall(headers + b"<rss>")
                                    payload = b"x" * 100
                                for byte in payload:
                                    tls.sendall(bytes((byte,)))
                                    time.sleep(0.1)
                        except (
                            BrokenPipeError,
                            ConnectionResetError,
                            OSError,
                            ssl.SSLError,
                        ):
                            pass
                        finally:
                            listener.close()

                    server = threading.Thread(target=serve_slowly, daemon=True)
                    server.start()
                    client_context = ssl.create_default_context(
                        cafile=str(cert_path)
                    )

                    def connect_loopback(
                        connection,
                        _address,
                        timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                        source_address=None,
                    ):  # type: ignore[no-untyped-def]
                        del connection, _address
                        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                            client.settimeout(timeout)
                        if source_address is not None:
                            client.bind(source_address)
                        client.connect(("127.0.0.1", port))
                        return client

                    started = time.monotonic()
                    with patch(
                        "asset_assistant.market_information.socket.getaddrinfo",
                        return_value=public,
                    ), patch.object(
                        _PinnedHTTPSConnection,
                        "_connect_pinned",
                        connect_loopback,
                    ), patch.object(
                        http.client,
                        "_create_https_context",
                        return_value=client_context,
                    ):
                        with self.assertRaises(
                            MarketInformationFetchError
                        ) as raised:
                            OfficialFeedFetcher(timeout_seconds=10).fetch(
                                PROVIDER_BY_ID["bea"],
                                etag=None,
                                last_modified=None,
                                user_agent="fixture",
                                deadline_monotonic=started + 0.6,
                            )
                    elapsed = time.monotonic() - started
                    server.join(timeout=2)

                    self.assertEqual(raised.exception.code, "COLLECTION_TIMEOUT")
                    self.assertLess(elapsed, 1.5)
                    self.assertEqual(server_names, ["apps.bea.gov"])
                    self.assertIn(b"Host: apps.bea.gov", requests[0])
                    self.assertFalse(server.is_alive())

    def test_redirect_host_is_resolved_once_then_pinned(self) -> None:
        spec = PROVIDER_BY_ID["bea"]
        initial = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        ]
        redirected = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("9.9.9.9", 443))
        ]
        rebound = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]
        with patch(
            "asset_assistant.market_information.socket.getaddrinfo",
            side_effect=(initial, redirected, rebound),
        ) as resolver:
            addresses = _assert_public_dns("apps.bea.gov", 443)
            handler = _PinnedHTTPSHandler(
                spec,
                initial_host="apps.bea.gov",
                initial_port=443,
                initial_addresses=addresses,
            )
            captured: list[_PinnedHTTPSConnection] = []

            def capture_connection(factory, request, **kwargs):  # type: ignore[no-untyped-def]
                connection = factory(
                    request.host, timeout=1.0, context=kwargs["context"]
                )
                captured.append(connection)
                return connection

            handler.do_open = capture_connection  # type: ignore[method-assign]
            redirect = urllib.request.Request("https://www.bea.gov/news/feed.xml")
            handler.https_open(redirect)
            handler.https_open(redirect)

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual([value.host for value in captured], ["www.bea.gov"] * 2)
        self.assertTrue(
            all(value.pinned_addresses[0][3] == ("9.9.9.9", 443) for value in captured)
        )


class MarketInformationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="wealthy-market-info-", dir=TEMP_ROOT
        )
        self.store = MarketDataStore(Path(self.temporary.name) / "market.sqlite3")
        self.repository = MarketInformationRepository(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_deduplicates_documents_and_preserves_source_revisions(self) -> None:
        canonical = "sec:document:0000123456-26-000001"
        first = item(
            canonical,
            provider_id="sec_edgar",
            title="Original filing title",
            category="FILING",
            original_url="https://www.sec.gov/Archives/edgar/data/123456/filing.htm",
        )
        revised = replace(first, title_original="Corrected filing title")
        second_source = item(
            canonical,
            provider_id="sec_press",
            title="SEC release about the filing",
            category="MARKET",
            source_name="SEC Press Releases RSS",
            original_url="https://www.sec.gov/newsroom/press-releases/2026-1",
        )

        self.assertEqual(self.repository.save_items((first,), now=BASE_TIME), 1)
        self.assertEqual(self.repository.save_items((first,), now=BASE_TIME), 0)
        self.assertEqual(
            self.repository.save_items((revised,), now=BASE_TIME + timedelta(seconds=1)),
            0,
        )
        self.assertEqual(
            self.repository.save_items(
                (second_source,), now=BASE_TIME + timedelta(seconds=2)
            ),
            0,
        )
        # Replaying an already observed revision must not add another source row.
        self.repository.save_items((revised,), now=BASE_TIME + timedelta(seconds=3))

        listed = self.repository.list_items(
            now=BASE_TIME + timedelta(seconds=4), limit=10
        )
        self.assertEqual(len(listed["items"]), 1)
        stored = listed["items"][0]
        self.assertEqual(stored["info_id"], first.info_id)
        self.assertEqual(len(stored["sources"]), 3)
        self.assertEqual(
            {source["title_original"] for source in stored["sources"]},
            {
                "Original filing title",
                "Corrected filing title",
                "SEC release about the filing",
            },
        )
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT first_seen_at_us FROM market_information WHERE info_id=?",
                (first.info_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            int(row["first_seen_at_us"]),
            _utc_epoch_microseconds(BASE_TIME, field_name="test.first_seen"),
        )

    def test_sort_filters_pagination_and_cursor_filter_binding(self) -> None:
        values = (
            item(
                "bea:new",
                title="Latest GDP release",
                category="ECONOMY",
                published_at=BASE_TIME,
                collected_at=BASE_TIME + timedelta(minutes=3),
                related_symbols=("SPY",),
            ),
            item(
                "fed:older",
                provider_id="federal_reserve",
                title="Earlier policy release",
                category="MARKET",
                published_at=BASE_TIME - timedelta(hours=1),
                collected_at=BASE_TIME + timedelta(minutes=2),
            ),
            item(
                "bea:unknown-time",
                title="Publication schedule",
                category="ECONOMY",
                published_at=None,
                collected_at=BASE_TIME + timedelta(minutes=4),
            ),
        )
        self.repository.save_items(
            values, now=BASE_TIME + timedelta(minutes=5)
        )
        listed = self.repository.list_items(
            now=BASE_TIME + timedelta(minutes=6), limit=10
        )
        self.assertEqual(
            [entry["title_original"] for entry in listed["items"]],
            ["Latest GDP release", "Earlier policy release", "Publication schedule"],
        )
        collected = self.repository.list_items(
            now=BASE_TIME + timedelta(minutes=6),
            sort="COLLECTED_DESC",
            limit=10,
        )
        self.assertEqual(
            [entry["title_original"] for entry in collected["items"]],
            ["Publication schedule", "Latest GDP release", "Earlier policy release"],
        )
        self.assertEqual(
            len(
                self.repository.list_items(
                    now=BASE_TIME + timedelta(minutes=6), category="MARKET"
                )["items"]
            ),
            1,
        )
        self.assertEqual(
            len(
                self.repository.list_items(
                    now=BASE_TIME + timedelta(minutes=6), provider="bea"
                )["items"]
            ),
            2,
        )
        self.assertEqual(
            len(
                self.repository.list_items(
                    now=BASE_TIME + timedelta(minutes=6), related="RELATED"
                )["items"]
            ),
            1,
        )
        self.assertEqual(
            len(
                self.repository.list_items(
                    now=BASE_TIME + timedelta(minutes=6), q="gDp"
                )["items"]
            ),
            1,
        )

        first_page = self.repository.list_items(
            now=BASE_TIME + timedelta(minutes=6), limit=1
        )
        self.assertTrue(first_page["has_more"])
        self.assertIsNotNone(first_page["next_cursor"])
        second_page = self.repository.list_items(
            now=BASE_TIME + timedelta(minutes=7),
            limit=1,
            cursor=str(first_page["next_cursor"]),
            snapshot=str(first_page["snapshot"]),
        )
        self.assertNotEqual(
            first_page["items"][0]["info_id"], second_page["items"][0]["info_id"]
        )
        with self.assertRaises(DomainValidationError):
            self.repository.list_items(
                now=BASE_TIME + timedelta(minutes=7),
                q="changed-filter",
                limit=1,
                cursor=str(first_page["next_cursor"]),
                snapshot=str(first_page["snapshot"]),
            )

    def test_sample_and_live_namespaces_can_preserve_the_same_document(self) -> None:
        live = item("bea:same-document", title="Official metadata")
        sample = replace(live, is_sample=True)

        self.assertEqual(self.repository.save_items((live,), now=BASE_TIME), 1)
        self.assertEqual(
            self.repository.save_items(
                (sample,), now=BASE_TIME + timedelta(microseconds=1)
            ),
            1,
        )
        listed = self.repository.list_items(
            now=BASE_TIME + timedelta(seconds=1), limit=10
        )

        self.assertEqual(len(listed["items"]), 2)
        self.assertEqual(
            {entry["is_sample"] for entry in listed["items"]}, {False, True}
        )
        self.assertEqual(len({entry["info_id"] for entry in listed["items"]}), 2)

    def test_retention_boundaries_and_recollection_keep_original_expiry(self) -> None:
        first_collected = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
        expires_at = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
        document = item(
            "bea:retention-boundary",
            collected_at=first_collected,
            published_at=first_collected,
        )
        self.repository.save_items((document,), now=first_collected)

        before_window = self.repository.list_items(
            now=expires_at - timedelta(days=30, microseconds=1), limit=10
        )
        self.assertEqual(
            before_window["items"][0]["retention_status"], "WITHIN_RETENTION"
        )
        at_thirty_days = self.repository.retention_preview(
            now=expires_at - timedelta(days=30), limit=10
        )
        listed = self.repository.list_items(
            now=expires_at - timedelta(days=30), limit=10
        )
        self.assertEqual(at_thirty_days["summary"]["expiring_soon_count"], 1)
        self.assertEqual(listed["items"][0]["retention_status"], "EXPIRING_SOON")
        self.assertEqual(
            listed["items"][0]["retention_expires_at"], expires_at.isoformat()
        )

        # A later observation adds source metadata but must not restart the
        # one-calendar-year retention clock.
        later = first_collected + timedelta(days=200)
        self.repository.save_items(
            (replace(document, title_original="Re-observed metadata"),), now=later
        )
        at_expiry = self.repository.retention_preview(now=expires_at, limit=10)
        self.assertEqual(at_expiry["summary"]["expired_count"], 1)
        self.assertEqual(at_expiry["items"][0]["retention_status"], "EXPIRED")
        self.assertEqual(
            at_expiry["items"][0]["retention_expires_at"], expires_at.isoformat()
        )
        self.assertEqual(at_expiry["summary"]["total_information_count"], 1)
        self.assertEqual(at_expiry["summary"]["total_source_record_count"], 2)

    def test_retention_estimation_unavailable_and_database_paging(self) -> None:
        old = datetime(2024, 1, 15, 9, 0, tzinfo=UTC)
        for index in range(3):
            observed = old + timedelta(days=index)
            self.repository.save_items(
                (
                    item(
                        f"bea:retention-page-{index}",
                        collected_at=observed,
                        published_at=observed,
                    ),
                ),
                now=observed,
            )
        connection = self.store._connect()
        try:
            rows = connection.execute(
                "SELECT info_id FROM market_information ORDER BY first_seen_at_us"
            ).fetchall()
            estimated_id = str(rows[0]["info_id"])
            unavailable_id = str(rows[1]["info_id"])
            connection.execute(
                "UPDATE market_information SET first_seen_at=?, first_seen_at_us=? WHERE info_id=?",
                ("invalid", -1, estimated_id),
            )
            connection.execute(
                """UPDATE market_information
                   SET first_seen_at=?, first_seen_at_us=?, collected_at=?, collected_at_us=?
                   WHERE info_id=?""",
                ("invalid", -1, "invalid", -1, unavailable_id),
            )
            connection.commit()
        finally:
            connection.close()

        checked_at = datetime(2026, 2, 1, tzinfo=UTC)
        first = self.repository.retention_preview(now=checked_at, limit=1)
        visible = self.repository.list_items(now=checked_at, limit=10)
        unavailable = next(
            entry for entry in visible["items"] if entry["info_id"] == unavailable_id
        )
        self.assertEqual(first["summary"]["total_information_count"], 3)
        self.assertEqual(first["summary"]["uncomputable_count"], 1)
        self.assertEqual(unavailable["retention_status"], "CALCULATION_UNAVAILABLE")
        self.assertIsNone(unavailable["retention_expires_at"])
        self.assertTrue(first["has_more"])
        self.assertTrue(first["items"][0]["first_collected_estimated"])
        # The cursor snapshot fixes the eligible row set.  A later insert must
        # neither change the summary nor appear in subsequent pages.
        after_snapshot = checked_at + timedelta(minutes=30)
        self.repository.save_items(
            (
                item(
                    "bea:after-retention-snapshot",
                    collected_at=after_snapshot,
                    published_at=after_snapshot,
                ),
            ),
            now=after_snapshot,
        )
        second = self.repository.retention_preview(
            now=checked_at + timedelta(hours=1),
            limit=1,
            cursor=str(first["next_cursor"]),
            snapshot=str(first["snapshot"]),
        )
        self.assertEqual(second["summary"], first["summary"])
        self.assertFalse(second["has_more"])
        self.assertNotEqual(first["items"][0]["info_id"], second["items"][0]["info_id"])
        self.assertNotIn(unavailable_id, {entry["info_id"] for entry in first["items"] + second["items"]})
        self.assertFalse(first["deletion_enabled"])
        self.assertIn("실제 삭제", first["notice"])

    def test_retention_leap_day_uses_calendar_year(self) -> None:
        leap_day = datetime(2024, 2, 29, 8, 15, tzinfo=UTC)
        self.repository.save_items(
            (
                item(
                    "bea:leap-day",
                    collected_at=leap_day,
                    published_at=leap_day,
                ),
            ),
            now=leap_day,
        )
        result = self.repository.list_items(
            now=datetime(2025, 2, 1, tzinfo=UTC), limit=10
        )
        self.assertEqual(
            result["items"][0]["retention_expires_at"],
            datetime(2025, 2, 28, 8, 15, tzinfo=UTC).isoformat(),
        )


class MarketInformationServiceTests(unittest.TestCase):
    def test_total_collection_deadline_preserves_success_and_previous_data(self) -> None:
        monotonic = MutableMonotonic()
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-total-timeout-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (
                PROVIDER_BY_ID["bea"],
                PROVIDER_BY_ID["federal_reserve"],
                PROVIDER_BY_ID["bls_cpi"],
            ),
        ):
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            fetcher = DeadlineAdvancingFetcher(monotonic)
            service = MarketInformationService(
                repository,
                fetcher=fetcher,
                clock=MutableClock(),
                monotonic=monotonic,
                collection_timeout_seconds=10,
            )
            prior = item(
                "prior-fed",
                provider_id="federal_reserve",
                title="Previous valid Federal Reserve item",
                collected_at=BASE_TIME - timedelta(hours=1),
                original_url=(
                    "https://www.federalreserve.gov/newsevents/pressreleases/"
                    "monetary20261013a.htm"
                ),
            )
            repository.save_items((prior,), now=BASE_TIME - timedelta(hours=1))
            repository.update_state(
                PROVIDER_BY_ID["federal_reserve"],
                status="OK",
                now=BASE_TIME - timedelta(hours=1),
                success=True,
                next_allowed_at=BASE_TIME,
            )
            prior_not_started = item(
                "prior-bls-cpi",
                provider_id="bls_cpi",
                title="Previous valid BLS CPI item",
                collected_at=BASE_TIME - timedelta(hours=2),
                original_url="https://www.bls.gov/news.release/cpi.nr0.htm",
            )
            repository.save_items(
                (prior_not_started,), now=BASE_TIME - timedelta(hours=2)
            )
            repository.update_state(
                PROVIDER_BY_ID["bls_cpi"],
                status="OK",
                now=BASE_TIME - timedelta(hours=2),
                success=True,
                next_allowed_at=BASE_TIME,
            )

            result = service.refresh(manual=True)
            listed = repository.list_items(now=BASE_TIME, limit=20)
            status = service.status()

            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(result["timed_out_provider_ids"], ("federal_reserve",))
            self.assertEqual(result["incomplete_provider_ids"], ("bls_cpi",))
            self.assertTrue(result["using_previous_data"])
            self.assertEqual(fetcher.calls[0][1], 10.0)
            self.assertEqual(fetcher.calls[1][1], 10.0)
            self.assertEqual(repository.state("bea")["status"], "OK")
            self.assertEqual(repository.state("federal_reserve")["status"], "TIMED_OUT")
            # The global limit did not mutate the provider's previous
            # successful persisted state or invent a new attempt timestamp.
            self.assertEqual(repository.state("bls_cpi")["status"], "OK")
            self.assertEqual(
                repository.state("bls_cpi")["last_attempt_at"],
                (BASE_TIME - timedelta(hours=2)).isoformat(),
            )
            titles = {entry["title_original"] for entry in listed["items"]}
            self.assertIn("Gross Domestic Product Release", titles)
            self.assertIn("Previous valid Federal Reserve item", titles)
            self.assertNotIn("Must not be committed after timeout", titles)
            self.assertEqual(status["current_result"], "PARTIAL")
            self.assertEqual(
                set(status["timed_out_provider_ids"]),
                {"federal_reserve"},
            )
            self.assertEqual(status["incomplete_provider_ids"], ("bls_cpi",))
            by_provider = {
                entry["provider_id"]: entry for entry in status["providers"]
            }
            self.assertEqual(by_provider["bls_cpi"]["status"], "OK")
            self.assertEqual(
                by_provider["bls_cpi"]["this_collection_result"],
                "NOT_STARTED_TIME_LIMIT",
            )
            self.assertEqual(
                by_provider["bls_cpi"]["this_collection_error_code"],
                "COLLECTION_TIME_LIMIT_REACHED",
            )
            self.assertTrue(by_provider["bls_cpi"]["using_previous_data"])
            self.assertTrue(status["using_previous_data"])
            self.assertTrue(status["retry_allowed"])
            self.assertIn("DNS", status["timeout_limitations"])

    def test_sec_without_local_contact_isolated_without_network_or_failure_count(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-sec-config-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["sec_press"],),
        ):
            fetcher = SequenceFetcher()
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            service = MarketInformationService(
                repository,
                fetcher=fetcher,
                sec_user_agent=lambda: None,
                clock=MutableClock(),
            )

            result = service.refresh(manual=True)
            state = repository.state("sec_press")

            self.assertEqual(result["status"], "NEEDS_CONFIGURATION")
            self.assertEqual(fetcher.calls, [])
            self.assertEqual(state["status"], "NEEDS_CONFIGURATION")
            self.assertEqual(state["consecutive_failures"], 0)
            self.assertEqual(service.status()["overall_state"], "NEEDS_CONFIGURATION")
            self.assertFalse(service.status()["partial"])

    def test_unconfigured_sec_is_excluded_from_all_timeout_aggregate(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-timeout-sec-config-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"], PROVIDER_BY_ID["sec_press"]),
        ):
            fetcher = SequenceFetcher(
                MarketInformationFetchError("SOCKET_TIMEOUT")
            )
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            service = MarketInformationService(
                repository,
                fetcher=fetcher,
                sec_user_agent=lambda: None,
                clock=MutableClock(),
            )

            result = service.refresh(manual=True)

            self.assertEqual(result["status"], "TIMED_OUT")
            self.assertEqual(result["timed_out_provider_ids"], ("bea",))
            self.assertEqual(fetcher.calls[0]["provider_id"], "bea")
            by_provider = {
                entry["provider_id"]: entry
                for entry in result["provider_results"]
            }
            self.assertEqual(by_provider["sec_press"]["result"], "NEEDS_CONFIGURATION")
            self.assertEqual(service.status()["current_result"], "TIMED_OUT")

    def test_cooldown_is_checked_before_global_deadline(self) -> None:
        monotonic = MutableMonotonic()
        clock = MutableClock()
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-cooldown-before-deadline-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"], PROVIDER_BY_ID["federal_reserve"]),
        ):
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            waiting_spec = PROVIDER_BY_ID["federal_reserve"]
            fetcher = TimeoutAdvancingFetcher(monotonic)
            service = MarketInformationService(
                repository,
                fetcher=fetcher,
                clock=clock,
                monotonic=monotonic,
                collection_timeout_seconds=10,
            )
            prior_success = BASE_TIME - timedelta(minutes=5)
            repository.update_state(
                waiting_spec,
                status="OK",
                now=prior_success,
                success=True,
                next_allowed_at=BASE_TIME + timedelta(hours=1),
            )
            state_before = repository.state(waiting_spec.provider_id)

            result = service.refresh(manual=True)
            state_after = repository.state(waiting_spec.provider_id)

            self.assertEqual(result["status"], "TIMED_OUT")
            self.assertEqual(fetcher.calls, ["bea"])
            self.assertEqual(result["incomplete_provider_ids"], ())
            by_provider = {
                entry["provider_id"]: entry
                for entry in result["provider_results"]
            }
            self.assertEqual(by_provider["federal_reserve"]["result"], "WAITING")
            self.assertEqual(state_after, state_before)

    def test_304_uses_validators_and_counts_as_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wealthy-market-304-", dir=TEMP_ROOT) as root:
            clock = MutableClock()
            fetcher = SequenceFetcher(
                FetchResponse(304, {}, b"", BASE_TIME)
            )
            with patch(
                "asset_assistant.market_information.PROVIDER_SPECS",
                (PROVIDER_BY_ID["bea"],),
            ):
                repository = MarketInformationRepository(
                    MarketDataStore(Path(root) / "market.sqlite3")
                )
                service = MarketInformationService(
                    repository, fetcher=fetcher, clock=clock
                )
                spec = PROVIDER_BY_ID["bea"]
                repository.update_state(
                    spec,
                    status="OK",
                    now=BASE_TIME - timedelta(hours=1),
                    success=True,
                    next_allowed_at=BASE_TIME,
                    etag='"fixture-etag"',
                    last_modified="Tue, 13 Oct 2026 05:00:00 GMT",
                )
                previous_data_at = BASE_TIME - timedelta(hours=2)
                repository.save_items(
                    (
                        item(
                            "not-modified-basis",
                            collected_at=previous_data_at,
                        ),
                    ),
                    now=previous_data_at,
                )
                result = service.refresh(manual=True)

            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["new_items"], 0)
            self.assertEqual(fetcher.calls[0]["etag"], '"fixture-etag"')
            self.assertEqual(
                fetcher.calls[0]["last_modified"],
                "Tue, 13 Oct 2026 05:00:00 GMT",
            )
            state = repository.state("bea")
            self.assertEqual(state["status"], "NOT_MODIFIED")
            self.assertEqual(state["last_http_status"], 304)
            self.assertEqual(state["etag"], '"fixture-etag"')
            provider = service.status()["providers"][0]
            self.assertEqual(provider["last_success_at"], BASE_TIME.isoformat())
            self.assertEqual(
                provider["current_data_as_of"], previous_data_at.isoformat()
            )
            self.assertEqual(
                service.status()["current_data_as_of"], previous_data_at.isoformat()
            )

    def test_socket_timeout_is_not_reported_as_generic_failure(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-socket-timeout-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"],),
        ):
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            service = MarketInformationService(
                repository,
                fetcher=SequenceFetcher(
                    MarketInformationFetchError("SOCKET_TIMEOUT")
                ),
                clock=MutableClock(),
            )
            result = service.refresh(manual=True)
            status = service.status()

            self.assertEqual(result["status"], "TIMED_OUT")
            self.assertEqual(result["timed_out_provider_ids"], ("bea",))
            self.assertEqual(result["failed_provider_ids"], ())
            self.assertEqual(repository.state("bea")["status"], "TIMED_OUT")
            self.assertEqual(status["overall_state"], "TIMED_OUT")
            self.assertEqual(status["timed_out_provider_ids"], ("bea",))
            self.assertEqual(status["failed_provider_ids"], ())

    def test_failures_are_isolated_and_set_retry_cooldown(self) -> None:
        cases = (
            (MarketInformationFetchError("HTTP_403", http_status=403), "HTTP_403", 403, 7_200),
            (
                MarketInformationFetchError(
                    "HTTP_429", http_status=429, retry_after_seconds=10_000
                ),
                "HTTP_429",
                429,
                10_000,
            ),
            (MarketInformationFetchError("HTTP_500", http_status=500), "HTTP_500", 500, 7_200),
            (MarketInformationFetchError("TIMEOUT_OR_NETWORK"), "TIMEOUT_OR_NETWORK", None, 7_200),
            (FetchResponse(200, {}, b"<rss>", BASE_TIME), "INVALID_FEED", None, 7_200),
        )
        for index, (outcome, code, http_status, minimum_delay) in enumerate(cases):
            with self.subTest(code=code), tempfile.TemporaryDirectory(
                prefix=f"wealthy-market-failure-{index}-", dir=TEMP_ROOT
            ) as root, patch(
                "asset_assistant.market_information.PROVIDER_SPECS",
                (PROVIDER_BY_ID["bea"],),
            ):
                fetcher = SequenceFetcher(outcome)
                repository = MarketInformationRepository(
                    MarketDataStore(Path(root) / "market.sqlite3")
                )
                service = MarketInformationService(
                    repository, fetcher=fetcher, clock=MutableClock()
                )
                result = service.refresh(manual=True)
                state = repository.state("bea")

                self.assertEqual(result["status"], "FAILED")
                self.assertEqual(state["status"], "FAILED")
                self.assertEqual(state["error_code"], code)
                self.assertEqual(state["last_http_status"], http_status)
                next_allowed = datetime.fromisoformat(state["next_allowed_at"])
                self.assertGreaterEqual(
                    (next_allowed - BASE_TIME).total_seconds(), minimum_delay
                )

    def test_one_provider_failure_does_not_discard_another_provider_success(self) -> None:
        federal_body = rss_body(
            title="Federal Reserve policy release",
            guid="fed-release-1",
            link="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm",
        )
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-partial-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"], PROVIDER_BY_ID["federal_reserve"]),
        ):
            fetcher = SequenceFetcher(
                MarketInformationFetchError("HTTP_403", http_status=403),
                FetchResponse(200, {}, federal_body, BASE_TIME),
            )
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            service = MarketInformationService(
                repository, fetcher=fetcher, clock=MutableClock()
            )

            result = service.refresh(manual=False)
            listed = repository.list_items(now=BASE_TIME, limit=10)

            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(result["new_items"], 1)
            self.assertEqual(len(fetcher.calls), 2)
            self.assertEqual(repository.state("bea")["status"], "FAILED")
            self.assertEqual(repository.state("federal_reserve")["status"], "OK")
            self.assertEqual(
                [value["title_original"] for value in listed["items"]],
                ["Federal Reserve policy release"],
            )

    def test_unexpected_provider_failure_is_isolated_without_error_text(self) -> None:
        federal_body = rss_body(
            title="Federal Reserve policy release",
            guid="fed-release-unexpected",
            link="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm",
        )
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-unexpected-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"], PROVIDER_BY_ID["federal_reserve"]),
        ):
            fetcher = SequenceFetcher(
                RuntimeError("private-fixture-marker"),
                FetchResponse(200, {}, federal_body, BASE_TIME),
            )
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            service = MarketInformationService(
                repository, fetcher=fetcher, clock=MutableClock()
            )

            result = service.refresh(manual=False)
            state = repository.state("bea")

            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(result["new_items"], 1)
            self.assertEqual(state["error_code"], "FETCH_FAILED")
            self.assertNotIn("private-fixture-marker", str(result))

    def test_mid_refresh_single_owner_failure_stops_before_metadata_write(self) -> None:
        calls = 0

        def owner_guard() -> None:
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise DomainValidationError("private-owner-marker")

        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-owner-guard-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"],),
        ):
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            service = MarketInformationService(
                repository,
                fetcher=SequenceFetcher(
                    FetchResponse(200, {}, rss_body(), BASE_TIME)
                ),
                owner_guard=owner_guard,
                clock=MutableClock(),
            )

            with self.assertRaises(DomainValidationError) as raised:
                service.refresh(manual=True)
            listed = repository.list_items(now=BASE_TIME, limit=10)

            self.assertEqual(str(raised.exception), "로컬 단일 사용자 상태를 확인할 수 없습니다.")
            self.assertNotIn("private-owner-marker", str(raised.exception))
            self.assertEqual(listed["items"], [])
            self.assertEqual(repository.state("bea")["status"], "NEVER_RUN")

    def test_success_sets_cooldown_and_second_refresh_makes_no_request(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wealthy-market-cooldown-", dir=TEMP_ROOT) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"],),
        ):
            fetcher = SequenceFetcher(
                FetchResponse(200, {}, rss_body(), BASE_TIME)
            )
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            service = MarketInformationService(
                repository, fetcher=fetcher, clock=MutableClock()
            )

            first = service.refresh(manual=True)
            second = service.refresh(manual=True)

            self.assertEqual(first["status"], "OK")
            self.assertEqual(first["new_items"], 1)
            self.assertEqual(second["status"], "RATE_LIMITED")
            self.assertEqual(len(fetcher.calls), 1)

    def test_concurrent_refresh_allows_only_one_fetch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wealthy-market-concurrent-", dir=TEMP_ROOT) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"],),
        ):
            fetcher = BlockingFetcher(
                FetchResponse(200, {}, rss_body(), BASE_TIME)
            )
            repository = MarketInformationRepository(
                MarketDataStore(Path(root) / "market.sqlite3")
            )
            service = MarketInformationService(
                repository, fetcher=fetcher, clock=MutableClock()
            )
            first_result: list[dict[str, object]] = []
            worker = threading.Thread(
                target=lambda: first_result.append(service.refresh(manual=False))
            )
            worker.start()
            self.assertTrue(fetcher.entered.wait(5))

            competing = service.refresh(manual=True)
            fetcher.release.set()
            worker.join(5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(competing["status"], "ALREADY_RUNNING")
            self.assertEqual(fetcher.call_count, 1)
            self.assertEqual(first_result[0]["status"], "OK")

    def test_cross_process_claim_rereads_cooldown_before_fetch(self) -> None:
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-process-race-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"],),
        ):
            db_path = Path(root) / "market.sqlite3"
            repository = MarketInformationRepository(MarketDataStore(db_path))
            primary_fetcher = SequenceFetcher(
                FetchResponse(200, {}, rss_body(guid="primary-refresh"), BASE_TIME)
            )
            primary = MarketInformationService(
                repository, fetcher=primary_fetcher, clock=MutableClock()
            )
            ready = context.Event()
            resume = context.Event()
            results = context.Queue()
            worker = context.Process(
                target=cross_process_refresh_worker,
                args=(str(db_path), ready, resume, results),
            )
            worker.start()
            try:
                self.assertTrue(ready.wait(10), "competing process did not read state")
                primary_result = primary.refresh(manual=False)
                resume.set()
                competing_result = results.get(timeout=15)
                worker.join(10)
                self.assertFalse(worker.is_alive())
            finally:
                resume.set()
                if worker.is_alive():
                    worker.terminate()
                    worker.join(5)
                results.close()
                results.join_thread()

            self.assertEqual(primary_result["status"], "OK")
            self.assertEqual(len(primary_fetcher.calls), 1)
            self.assertIsNone(competing_result["error"])
            self.assertEqual(competing_result["status"], "RATE_LIMITED")
            self.assertEqual(competing_result["fetch_calls"], 0)
            connection = repository.store._connect()
            try:
                remaining_claims = connection.execute(
                    "SELECT count(*) FROM market_information_refresh_claims"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(remaining_claims, 0)

    def test_background_refresh_uses_saved_cooldown_and_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="wealthy-market-background-", dir=TEMP_ROOT
        ) as root, patch(
            "asset_assistant.market_information.PROVIDER_SPECS",
            (PROVIDER_BY_ID["bea"],),
        ):
            store = MarketDataStore(Path(root) / "market.sqlite3")
            repository = MarketInformationRepository(store)
            first_fetcher = SequenceFetcher(
                FetchResponse(200, {}, rss_body(), BASE_TIME)
            )
            first = MarketInformationService(
                repository, fetcher=first_fetcher, clock=MutableClock()
            )
            first.start_background(poll_seconds=10)
            for _ in range(100):
                if repository.state("bea")["status"] == "OK":
                    break
                threading.Event().wait(0.01)
            first.stop_background()

            self.assertEqual(len(first_fetcher.calls), 1)
            self.assertEqual(first.status()["automatic_state"], "STOPPED")

            # A process restart at the same instant reads the saved cadence
            # and does not replay missed slots or issue another request.
            second_fetcher = SequenceFetcher()
            second = MarketInformationService(
                MarketInformationRepository(store),
                fetcher=second_fetcher,
                clock=MutableClock(),
            )
            second.start_background(poll_seconds=10)
            threading.Event().wait(0.05)
            second.stop_background()
            self.assertEqual(second_fetcher.calls, [])


class MarketInformationSchemaTests(unittest.TestCase):
    def test_v2_database_migrates_to_v3_without_losing_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wealthy-market-v2-", dir=TEMP_ROOT) as root:
            path = Path(root) / "market.sqlite3"
            instant_us = _utc_epoch_microseconds(BASE_TIME, field_name="fixture.time")
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE schema_meta(version INTEGER NOT NULL);
                    INSERT INTO schema_meta(version) VALUES (2);
                    CREATE TABLE collection_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider TEXT NOT NULL, started_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL, finished_at_us INTEGER NOT NULL,
                        status TEXT NOT NULL, issue_codes_json TEXT NOT NULL,
                        record_count INTEGER NOT NULL
                    );
                    CREATE TABLE market_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id INTEGER NOT NULL, provider TEXT NOT NULL,
                        kind TEXT NOT NULL, provider_record_id TEXT NOT NULL,
                        instrument_id TEXT NOT NULL, market TEXT NOT NULL,
                        currency TEXT, effective_at TEXT NOT NULL,
                        effective_at_us INTEGER NOT NULL, published_at TEXT,
                        published_at_us INTEGER, collected_at TEXT NOT NULL,
                        collected_at_us INTEGER NOT NULL, unit TEXT NOT NULL,
                        value_text TEXT, revised INTEGER NOT NULL,
                        source_url TEXT NOT NULL, metadata_json TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    """INSERT INTO collection_runs
                       (provider, started_at, finished_at, finished_at_us, status,
                        issue_codes_json, record_count)
                       VALUES ('fixture', ?, ?, ?, 'OK', '[]', 1)""",
                    (BASE_TIME.isoformat(), BASE_TIME.isoformat(), instant_us),
                )
                connection.execute(
                    """INSERT INTO market_records
                       (run_id, provider, kind, provider_record_id, instrument_id,
                        market, currency, effective_at, effective_at_us, published_at,
                        published_at_us, collected_at, collected_at_us, unit,
                        value_text, revised, source_url, metadata_json, content_sha256)
                       VALUES (1, 'fixture', 'MACRO', 'row-1', 'SERIES', 'US',
                               'USD', ?, ?, ?, ?, ?, ?, 'index', '1', 0,
                               'https://example.gov/source', '{}', 'digest')""",
                    (
                        BASE_TIME.isoformat(),
                        instant_us,
                        BASE_TIME.isoformat(),
                        instant_us,
                        BASE_TIME.isoformat(),
                        instant_us,
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            MarketDataStore(path).initialize()
            migrated = sqlite3.connect(path)
            try:
                version = migrated.execute(
                    "SELECT version FROM schema_meta"
                ).fetchone()[0]
                record = migrated.execute(
                    "SELECT provider_record_id, collected_at_us FROM market_records"
                ).fetchone()
                tables = {
                    value[0]
                    for value in migrated.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                migrated.close()

            self.assertEqual(version, SCHEMA_VERSION)
            self.assertEqual(record, ("row-1", instant_us))
            self.assertTrue(
                {
                    "market_information",
                    "market_information_provider_state",
                    "market_information_refresh_claims",
                }.issubset(tables)
            )

    def test_unsupported_version_is_rejected_before_schema_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wealthy-market-unsupported-", dir=TEMP_ROOT) as root:
            path = Path(root) / "market.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE schema_meta(version INTEGER NOT NULL);
                    INSERT INTO schema_meta(version) VALUES (99);
                    CREATE TABLE sentinel(value TEXT NOT NULL);
                    INSERT INTO sentinel(value) VALUES ('keep-me');
                    """
                )
                connection.commit()
                before_tables = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                )
            finally:
                connection.close()

            with self.assertRaises(DomainValidationError):
                MarketDataStore(path).initialize()

            after = sqlite3.connect(path)
            try:
                after_tables = tuple(
                    row[0]
                    for row in after.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                )
                version = after.execute("SELECT version FROM schema_meta").fetchone()[0]
                sentinel = after.execute("SELECT value FROM sentinel").fetchone()[0]
            finally:
                after.close()
            self.assertEqual(after_tables, before_tables)
            self.assertEqual(version, 99)
            self.assertEqual(sentinel, "keep-me")


if __name__ == "__main__":
    unittest.main()
