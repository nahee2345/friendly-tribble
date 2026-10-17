from __future__ import annotations

import unittest
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from wealthy_server.app import CSRF_COOKIE, create_app
from wealthy_server.config import ServerSettings
from wealthy_server.database import Database
from wealthy_server.repositories import AuthRepository
from wealthy_server.security import PasswordManager


class FakeMarketInformationService:
    def __init__(self) -> None:
        self.filters: dict[str, object] = {}
        self.refresh_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    def start_background(self) -> None:
        self.start_calls += 1

    def stop_background(self) -> None:
        self.stop_calls += 1

    def list_items(self, **filters: object) -> dict[str, object]:
        self.filters = filters
        return {
            "items": [
                {
                    "info_id": "mi_fixture",
                    "provider_id": "federal_reserve",
                    "institution_id": "federal_reserve",
                    "provider_name": "Federal Reserve",
                    "source_name": "Official RSS",
                    "title_original": "<script>not executable</script>",
                    "language": "en",
                    "category": "MARKET",
                    "related_symbols": [],
                    "holding_status": "UNKNOWN",
                    "original_url": "https://www.federalreserve.gov/example.htm",
                    "link_status": "SAFE",
                    "published_at": None,
                    "collected_at": "2026-10-13T06:00:00+00:00",
                    "provider_item_id": "fixture-id",
                    "source_document_id": None,
                    "published_precision": "UNKNOWN",
                    "is_sample": False,
                    "sources": [],
                }
            ],
            "next_cursor": None,
            "snapshot": "2026-10-13T06:00:00+00:00",
            "has_more": False,
        }

    def status(self) -> dict[str, object]:
        return {
            "automatic_state": "STOPPED",
            "refreshing": False,
            "last_success_at": None,
            "partial": False,
            "providers": [],
        }

    def retention_preview(self, **filters: object) -> dict[str, object]:
        self.filters.update({f"retention_{key}": value for key, value in filters.items()})
        return {
            "summary": {
                "total_information_count": 2,
                "total_source_record_count": 3,
                "expired_count": 1,
                "expiring_soon_count": 0,
                "uncomputable_count": 0,
                "within_retention_count": 1,
                "checked_at": "2026-10-13T06:00:00+00:00",
                "information_count_unit": "INFORMATION_ITEM",
                "source_record_count_unit": "SOURCE_RECORD",
            },
            "items": [
                {
                    "info_id": "expired-fixture",
                    "provider_id": "federal_reserve",
                    "source_name": "Official RSS",
                    "title_original": "Expired metadata",
                    "first_collected_at": "2025-10-13T06:00:00+00:00",
                    "retention_expires_at": "2026-10-13T06:00:00+00:00",
                    "retention_status": "EXPIRED",
                    "first_collected_estimated": False,
                }
            ],
            "next_cursor": None,
            "has_more": False,
            "snapshot": "2026-10-13T06:00:00+00:00",
            "deletion_enabled": False,
            "notice": (
                "현재는 삭제 대상 확인만 지원합니다.\n"
                "실제 삭제 및 자동 정리 기능은 활성화되어 있지 않습니다."
            ),
        }

    def refresh(self, *, manual: bool = False) -> dict[str, object]:
        self.refresh_calls += 1
        return {
            "status": "OK",
            "new_items": 0,
            "started_at": "2026-10-13T06:00:00+00:00",
            "finished_at": "2026-10-13T06:00:01+00:00",
            "manual": manual,
        }


class WebMarketInformationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Database("sqlite+pysqlite:///:memory:")
        self.market = FakeMarketInformationService()
        settings = ServerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            master_key=b"m" * 32,
            secure_cookies=False,
            allowed_hosts=("testserver",),
        )
        self.app = create_app(
            settings,
            database=self.database,
            market_information_service=self.market,
            clock=lambda: datetime(2026, 10, 13, 6, 0, tzinfo=UTC),
        )
        AuthRepository(self.database.session_factory).create_user(
            "admin",
            PasswordManager().hash("fixture password phrase 123"),
            is_admin=True,
        )
        self.client = TestClient(self.app, base_url="http://testserver")

    def tearDown(self) -> None:
        self.client.close()
        self.database.dispose()

    def login(self) -> None:
        response = self.client.post(
            "/api/v1/login",
            headers={"Origin": "http://testserver"},
            json={"username": "admin", "password": "fixture password phrase 123"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def mutation_headers(self) -> dict[str, str]:
        return {
            "Origin": "http://testserver",
            "X-CSRF-Token": self.client.cookies.get(CSRF_COOKIE),
        }

    def test_list_and_status_require_authentication_and_keep_external_text_as_data(self) -> None:
        self.assertEqual(self.client.get("/api/v1/market-information").status_code, 401)
        self.assertEqual(
            self.client.get("/api/v1/market-information/status").status_code, 401
        )
        self.login()
        response = self.client.get(
            "/api/v1/market-information",
            params={
                "q": "Federal",
                "category": "MARKET",
                "related": "ALL",
                "provider": "federal_reserve",
                "sort": "PUBLISHED_DESC",
                "limit": 25,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["title_original"], "<script>not executable</script>")
        self.assertEqual(item["holding_status"], "UNKNOWN")
        self.assertEqual(self.market.filters["q"], "Federal")
        self.assertEqual(
            self.client.get("/api/v1/market-information/status").status_code, 200
        )

    def test_manual_refresh_requires_csrf_and_does_not_echo_input(self) -> None:
        self.login()
        denied = self.client.post("/api/v1/market-information/refresh")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.market.refresh_calls, 0)
        accepted = self.client.post(
            "/api/v1/market-information/refresh", headers=self.mutation_headers()
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue(accepted.json()["manual"])
        self.assertEqual(self.market.refresh_calls, 1)

    def test_retention_preview_is_authenticated_paged_and_read_only(self) -> None:
        denied = self.client.get("/api/v1/market-information/retention")
        self.assertEqual(denied.status_code, 401)
        self.login()
        response = self.client.get(
            "/api/v1/market-information/retention",
            params={"status": "EXPIRED", "limit": 10},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["summary"]["total_information_count"], 2)
        self.assertEqual(payload["summary"]["total_source_record_count"], 3)
        self.assertFalse(payload["deletion_enabled"])
        self.assertEqual(payload["items"][0]["retention_status"], "EXPIRED")
        self.assertEqual(self.market.filters["retention_status"], "EXPIRED")
        self.assertEqual(self.market.filters["retention_limit"], 10)
        self.assertEqual(
            self.client.delete(
                "/api/v1/market-information/retention",
                headers=self.mutation_headers(),
            ).status_code,
            405,
        )

    def test_query_bounds_fail_without_traceback_or_raw_value(self) -> None:
        self.login()
        marker = "110-123-456789"
        response = self.client.get(
            "/api/v1/market-information",
            params={"q": marker * 20, "limit": 101},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(marker, response.text)
        self.assertNotIn("456789", response.text)
        self.assertNotIn("Traceback", response.text)

    def test_explicit_serve_lifecycle_starts_and_stops_background_service(self) -> None:
        database = Database("sqlite+pysqlite:///:memory:")
        market = FakeMarketInformationService()
        settings = ServerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            master_key=b"n" * 32,
            secure_cookies=False,
            allowed_hosts=("testserver",),
        )
        app = create_app(
            settings,
            database=database,
            market_information_service=market,
            market_information_auto_start=True,
        )
        try:
            with TestClient(app, base_url="http://testserver"):
                self.assertEqual(market.start_calls, 1)
                self.assertEqual(market.stop_calls, 0)
            self.assertEqual(market.stop_calls, 1)
        finally:
            database.dispose()


if __name__ == "__main__":
    unittest.main()
