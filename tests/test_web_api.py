from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from wealthy_server.app import CSRF_COOKIE, RequestBodyLimitMiddleware, create_app
from wealthy_server.config import ServerSettings
from wealthy_server.database import Database
from wealthy_server.models import ProviderSecret, PushSubscription
from wealthy_server.repositories import AuthRepository
from wealthy_server.security import PasswordManager


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "offline_sample.json"


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Database("sqlite+pysqlite:///:memory:")
        self.settings = ServerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            master_key=b"t" * 32,
            secure_cookies=False,
            allowed_hosts=("testserver",),
            vapid_public_key="BFixturePublicKey_abcdefghijklmnopqrstuvwxyz0123456789",
            vapid_private_key=b"v" * 32,
        )
        self.app = create_app(
            self.settings,
            database=self.database,
            clock=lambda: datetime(2026, 10, 11, 7, 0, tzinfo=UTC),
        )
        self.auth = AuthRepository(self.database.session_factory)
        self.password = "correct horse battery staple"
        self.user = self.auth.create_user(
            "admin", PasswordManager().hash(self.password), is_admin=True
        )
        self.client = TestClient(self.app, base_url="http://testserver")
        response = self.client.post(
            "/api/v1/login",
            headers={"Origin": "http://testserver"},
            json={"username": "admin", "password": self.password},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def tearDown(self) -> None:
        self.client.close()
        self.database.dispose()

    def mutation_headers(self) -> dict[str, str]:
        return {
            "Origin": "http://testserver",
            "X-CSRF-Token": self.client.cookies.get(CSRF_COOKIE),
        }

    def test_login_limits_fixed_and_rotating_names_without_enumeration_path(self) -> None:
        headers = {"Origin": "http://testserver"}
        for _ in range(5):
            response = self.client.post(
                "/api/v1/login",
                headers=headers,
                json={"username": "admin", "password": "wrong password"},
            )
            self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/v1/login",
                headers=headers,
                json={"username": "admin", "password": "wrong password"},
            ).status_code,
            429,
        )

        # A fresh app proves that rotating unknown names cannot avoid the
        # independent client budget.
        database = Database("sqlite+pysqlite:///:memory:")
        app = create_app(self.settings, database=database, clock=lambda: datetime(2026, 10, 11, 7, 0, tzinfo=UTC))
        client = TestClient(app, base_url="http://testserver")
        try:
            for index in range(10):
                response = client.post(
                    "/api/v1/login",
                    headers=headers,
                    json={"username": f"unknown-{index}", "password": "wrong password"},
                )
                self.assertEqual(response.status_code, 401)
            limited = client.post(
                "/api/v1/login",
                headers=headers,
                json={"username": "another-name", "password": "wrong password"},
            )
            self.assertEqual(limited.status_code, 429)
            self.assertNotIn("another-name", limited.text)
        finally:
            client.close()
            database.dispose()

        self.auth.create_user(
            "timing-known",
            PasswordManager().hash("another correct password phrase"),
            is_admin=False,
        )
        with patch.object(PasswordManager, "verify", return_value=False) as verify:
            known = self.client.post(
                "/api/v1/login",
                headers=headers,
                json={"username": "timing-known", "password": "wrong password"},
            )
            unknown = self.client.post(
                "/api/v1/login",
                headers=headers,
                json={"username": "other-unknown", "password": "wrong password"},
            )
        self.assertEqual((known.status_code, unknown.status_code), (401, 401))
        self.assertEqual(verify.call_count, 2)

    def test_request_body_limits_fixed_length_streamed_and_upload_payload(self) -> None:
        oversized = self.client.post(
            "/api/v1/login",
            headers={"Origin": "http://testserver", "Content-Type": "application/json"},
            content=b"x" * (2 * 1024 * 1024 + 1),
        )
        self.assertEqual(oversized.status_code, 413)
        self.assertNotIn("Traceback", oversized.text)

        upload = self.client.post(
            "/api/v1/scenarios/upload",
            headers=self.mutation_headers(),
            files={
                "file": (
                    "large.json",
                    b" " * (self.settings.upload_limit_bytes + 1),
                    "application/json",
                )
            },
        )
        self.assertEqual(upload.status_code, 413)

        sent: list[dict[str, object]] = []
        chunks = iter(
            (
                {"type": "http.request", "body": b"abc", "more_body": True},
                {"type": "http.request", "body": b"def", "more_body": False},
            )
        )

        async def receive() -> dict[str, object]:
            return next(chunks)

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        async def consume(scope, receive_call, send_call) -> None:  # type: ignore[no-untyped-def]
            while True:
                message = await receive_call()
                if not message.get("more_body", False):
                    break
            await send_call({"type": "http.response.start", "status": 204, "headers": []})
            await send_call({"type": "http.response.body", "body": b""})

        middleware = RequestBodyLimitMiddleware(consume, max_bytes=5)
        asyncio.run(
            middleware(
                {"type": "http", "method": "POST", "path": "/", "headers": []},  # type: ignore[arg-type]
                receive,  # type: ignore[arg-type]
                send,  # type: ignore[arg-type]
            )
        )
        self.assertEqual(sent[0]["status"], 413)

    def test_sample_dashboard_and_seven_section_report_are_real_engine_results(self) -> None:
        created = self.client.post(
            "/api/v1/scenarios/sample", headers=self.mutation_headers()
        )
        self.assertEqual(created.status_code, 200, created.text)
        dashboard = self.client.get("/api/v1/dashboard").json()
        self.assertEqual(dashboard["data_scope"], "SAMPLE")
        self.assertIsInstance(dashboard["investable_total_krw"], str)
        self.assertNotEqual(dashboard["investable_total_krw"], "0")
        self.assertTrue(dashboard["sample"])

        report_id = created.json()["report_id"]
        report = self.client.get(f"/api/v1/reports/{report_id}")
        self.assertEqual(report.status_code, 200)
        markdown = report.json()["markdown"]
        for index in range(1, 8):
            self.assertIn(f"## {index})", markdown)
        self.assertIn("주문안: 없음", markdown)

    def test_csrf_and_other_owner_report_access_are_blocked(self) -> None:
        self.assertEqual(self.client.post("/api/v1/scenarios/sample").status_code, 403)
        created = self.client.post(
            "/api/v1/scenarios/sample", headers=self.mutation_headers()
        ).json()
        other = self.auth.create_user(
            "other", PasswordManager().hash("another correct password"), is_admin=False
        )
        other_client = TestClient(self.app, base_url="http://testserver")
        try:
            logged_in = other_client.post(
                "/api/v1/login",
                headers={"Origin": "http://testserver"},
                json={"username": "other", "password": "another correct password"},
            )
            self.assertEqual(logged_in.status_code, 200)
            denied = other_client.get(f"/api/v1/reports/{created['report_id']}")
            self.assertEqual(denied.status_code, 404)
            self.assertNotIn(self.user.id, denied.text)
        finally:
            other_client.close()

    def test_raw_upload_keeps_duplicate_key_and_hostile_json_defenses(self) -> None:
        marker = "110-123-456789"
        duplicate = b'{"schema_version":1,"schema_version":1}'
        for payload in (
            duplicate,
            ("[" * 1200 + "0" + "]" * 1200).encode(),
            ("{" + '"n":' + "9" * 5000 + "}").encode(),
            ("{\"note\":\"" + marker + "\"}").encode(),
        ):
            response = self.client.post(
                "/api/v1/scenarios/upload",
                headers=self.mutation_headers(),
                files={"file": ("input.json", payload, "application/json")},
            )
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(marker, response.text)
            self.assertNotIn("456789", response.text)
            self.assertNotIn("Traceback", response.text)

    def test_credentials_are_encrypted_and_never_returned(self) -> None:
        secret = "fixture-secret-value-not-a-real-key"
        response = self.client.put(
            "/api/v1/credentials/kis/paper",
            headers=self.mutation_headers(),
            json={"values": {"app_key": "fixture-public-id", "app_secret": secret}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(secret, response.text)
        status = self.client.get("/api/v1/credentials")
        self.assertEqual(status.status_code, 200)
        self.assertNotIn(secret, status.text)
        with self.database.session_factory() as session:
            rows = tuple(session.scalars(select(ProviderSecret)))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(secret.encode() not in row.ciphertext for row in rows))

    def test_target_allocation_is_separate_exact_and_append_only(self) -> None:
        weights = {
            "LONG_TERM_INDEX": "30",
            "VALUE": "10",
            "QUALITY_GROWTH": "10",
            "GROWTH": "10",
            "HIGH_DIVIDEND": "10",
            "DIVIDEND_GROWTH": "10",
            "MOMENTUM": "10",
            "ASSET_ALLOCATION": "10",
        }
        preview = self.client.post(
            "/api/v1/strategy-targets/preview",
            json={"weights": weights},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.json()["valid"])
        self.assertIsNone(preview.json()["target_amounts_krw"])

        loaded = self.client.post(
            "/api/v1/scenarios/sample", headers=self.mutation_headers()
        )
        self.assertEqual(loaded.status_code, 200, loaded.text)
        preview = self.client.post(
            "/api/v1/strategy-targets/preview", json={"weights": weights}
        )
        self.assertEqual(
            preview.json()["target_amounts_krw"]["LONG_TERM_INDEX"], "1200000"
        )

        forged = self.client.post(
            "/api/v1/strategy-targets/preview",
            json={"weights": weights, "total_assets_krw": "999999999"},
        )
        self.assertEqual(forged.status_code, 422)
        saved = self.client.post(
            "/api/v1/strategy-targets",
            headers=self.mutation_headers(),
            json={"version": "target-v1", "weights": weights},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["scope"], "TOTAL_INVESTMENT_ASSET_TARGET")
        duplicate = self.client.post(
            "/api/v1/strategy-targets",
            headers=self.mutation_headers(),
            json={"version": "target-v1", "weights": weights},
        )
        self.assertEqual(duplicate.status_code, 400)
        invalid = dict(weights)
        invalid["LONG_TERM_INDEX"] = "29.9"
        invalid_preview = self.client.post(
            "/api/v1/strategy-targets/preview",
            json={"weights": invalid},
        )
        self.assertFalse(invalid_preview.json()["valid"])

    def test_default_validation_errors_do_not_echo_secret_or_traceback(self) -> None:
        marker = "secret-credential-value"
        response = self.client.put(
            "/api/v1/credentials/kis/paper",
            headers=self.mutation_headers(),
            content=json.dumps({"values": {"unexpected": marker}}),
        )
        self.assertIn(response.status_code, {400, 422})
        self.assertNotIn(marker, response.text)
        self.assertNotIn("Traceback", response.text)

    def test_password_change_revokes_current_session(self) -> None:
        response = self.client.post(
            "/api/v1/password",
            headers=self.mutation_headers(),
            json={
                "current_password": self.password,
                "new_password": "new correct password phrase",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.get("/api/v1/session").json()["authenticated"])

    def test_private_api_headers_and_no_registration_route(self) -> None:
        response = self.client.get("/api/v1/status")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.json()["orders"], "NOT_IMPLEMENTED")
        self.assertEqual(self.client.post("/api/v1/register").status_code, 404)

    def test_push_capability_is_encrypted_and_can_be_removed_without_echo(self) -> None:
        endpoint = "https://push.example.invalid/capability-secret-value"
        stored = self.client.post(
            "/api/v1/push/subscriptions",
            headers=self.mutation_headers(),
            json={
                "endpoint": endpoint,
                "keys": {"p256dh": "fixture-p256dh", "auth": "fixture-auth"},
                "device_label": "검증 기기",
            },
        )
        self.assertEqual(stored.status_code, 200, stored.text)
        self.assertNotIn("capability-secret", stored.text)
        with self.database.session_factory() as session:
            row = session.scalar(select(PushSubscription))
        self.assertIsNotNone(row)
        self.assertNotIn(endpoint.encode(), row.encrypted_payload)
        removed = self.client.request(
            "DELETE",
            "/api/v1/push/subscriptions",
            headers=self.mutation_headers(),
            json={"endpoint": endpoint},
        )
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["removed"])


if __name__ == "__main__":
    unittest.main()
