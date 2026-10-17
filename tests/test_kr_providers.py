from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from asset_assistant.credential_store import CredentialStore, SERVICE_NAME
from asset_assistant.kr_providers import (
    ConnectivityDemoResult,
    EcosProvider,
    EcosSeriesRequest,
    KisQuoteProvider,
    OpenDartProvider,
)
from asset_assistant.market_data import CollectionStatus, RecordKind
from asset_assistant.providers import ProviderError


FIXED_NOW = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)


class MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class FakeClient:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request_json(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        before_attempt = kwargs.get("before_attempt")
        if before_attempt is not None:
            before_attempt()
        if not self.responses:
            raise AssertionError("unexpected provider call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def credential_store(**values: str) -> CredentialStore:
    backend = MemoryBackend()
    store = CredentialStore(backend, trusted_test_backend=True)
    for combined, value in values.items():
        provider, field = combined.split("__", 1)
        backend.values[(SERVICE_NAME, f"{provider}:{field}")] = value
    return store


def kis_quote_payload(**changes: str) -> dict[str, Any]:
    output = {
        "stck_prpr": "71200",
        "acml_vol": "1234567",
        "acml_tr_pbmn": "88000000000",
    }
    output.update(changes)
    return {"rt_cd": "0", "msg_cd": "MCA00000", "output": output}


class KisQuoteProviderTests(unittest.TestCase):
    app_key = "K" * 24
    app_secret = "S" * 48
    token = "T" * 80

    def _store(self, environment: str = "paper") -> CredentialStore:
        return credential_store(
            **{
                f"kis_{environment}__app_key": self.app_key,
                f"kis_{environment}__app_secret": self.app_secret,
            }
        )

    def test_quote_success_has_price_volume_value_and_memory_token_cache(self) -> None:
        sleeps: list[float] = []
        fake = FakeClient(
            {"access_token": self.token, "expires_in": 86400},
            kis_quote_payload(),
            kis_quote_payload(stck_prpr="71300"),
        )
        provider = KisQuoteProvider(
            self._store(),
            client=fake,
            environment="paper",
            clock=lambda: FIXED_NOW,
            monotonic=lambda: 0.0,
            sleeper=sleeps.append,
        )

        first = provider.collect_quotes(("005930",))
        second = provider.collect_quotes(("005930",))

        self.assertEqual(first.status, CollectionStatus.OK)
        self.assertEqual(second.status, CollectionStatus.OK)
        self.assertEqual(len(first.records), 3)
        self.assertEqual(
            {record.metadata["metric"] for record in first.records},
            {"price", "volume", "trading_value"},
        )
        self.assertEqual({record.kind for record in first.records}, {RecordKind.PRICE_VOLUME})
        self.assertEqual({record.metadata["environment"] for record in first.records}, {"paper"})
        self.assertEqual([record.value for record in first.records], ["71200", "1234567", "88000000000"])
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(fake.calls[0]["method"], "POST")
        self.assertEqual(fake.calls[0]["body"]["appsecret"], self.app_secret)
        self.assertIn("openapivts.koreainvestment.com:29443", fake.calls[0]["url"])
        self.assertEqual(fake.calls[1]["headers"]["authorization"], f"Bearer {self.token}")
        self.assertNotIn(self.token, repr(first))
        self.assertNotIn(self.app_secret, repr(first))
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(delay >= 1.0 for delay in sleeps))

    def test_live_and_paper_are_distinct_and_no_trade_or_portfolio_surface_exists(self) -> None:
        live = KisQuoteProvider(self._store("live"), environment="live", client=FakeClient())
        paper = KisQuoteProvider(self._store(), environment="paper", client=FakeClient())
        self.assertEqual(live.host, "openapi.koreainvestment.com")
        self.assertEqual(live.port, 9443)
        self.assertEqual(paper.host, "openapivts.koreainvestment.com")
        self.assertEqual(paper.port, 29443)
        public_callables = {
            name.lower()
            for name in dir(KisQuoteProvider)
            if not name.startswith("_") and callable(getattr(KisQuoteProvider, name))
        }
        for forbidden in ("order", "account", "balance", "buy", "sell"):
            self.assertFalse(any(forbidden in name for name in public_callables))

    def test_missing_or_rejected_auth_is_generic_and_secret_free(self) -> None:
        missing = KisQuoteProvider(
            credential_store(), client=FakeClient(), clock=lambda: FIXED_NOW
        ).collect_quotes(("005930",))
        self.assertEqual(missing.status, CollectionStatus.ERROR)
        self.assertEqual(missing.issues[0].code, "CONFIGURATION_REQUIRED")

        secret_marker = "DO-NOT-LEAK-456789"
        rejected = KisQuoteProvider(
            self._store(),
            client=FakeClient(
                {
                    "rt_cd": "1",
                    "msg_cd": "EGW00103",
                    "msg1": secret_marker,
                }
            ),
            clock=lambda: FIXED_NOW,
        ).collect_quotes(("005930",))
        self.assertEqual(rejected.issues[0].code, "AUTHENTICATION_FAILED")
        self.assertNotIn(secret_marker, repr(rejected))
        self.assertNotIn("456789", repr(rejected))

    def test_rate_timeout_and_partial_collection_do_not_copy_provider_details(self) -> None:
        marker = "private/query/token-456789"
        fake = FakeClient(
            {"access_token": self.token, "expires_in": 3600},
            kis_quote_payload(),
            ProviderError("RATE_LIMITED", marker, retryable=True),
        )
        result = KisQuoteProvider(
            self._store(),
            client=fake,
            clock=lambda: FIXED_NOW,
            monotonic=lambda: 0.0,
            sleeper=lambda _: None,
        ).collect_quotes(("005930", "069500"))
        self.assertEqual(result.status, CollectionStatus.PARTIAL)
        self.assertEqual(len(result.records), 3)
        self.assertEqual(result.issues[0].code, "RATE_LIMITED")
        self.assertNotIn(marker, repr(result))

        timeout = KisQuoteProvider(
            self._store(),
            client=FakeClient(ProviderError("NETWORK_ERROR", marker, retryable=True)),
            clock=lambda: FIXED_NOW,
        ).collect_quotes(("005930",))
        self.assertEqual(timeout.status, CollectionStatus.ERROR)
        self.assertEqual(timeout.issues[0].code, "NETWORK_ERROR")
        self.assertNotIn("456789", repr(timeout))


class OpenDartProviderTests(unittest.TestCase):
    api_key = "D" * 40

    def _store(self) -> CredentialStore:
        return credential_store(opendart__api_key=self.api_key)

    def test_disclosures_are_paginated_and_keep_receipt_correction_metadata(self) -> None:
        fake = FakeClient(
            {
                "status": "000",
                "page_no": 1,
                "total_page": 2,
                "list": [
                    {
                        "corp_code": "00126380",
                        "corp_name": "검증기업",
                        "stock_code": "005930",
                        "corp_cls": "Y",
                        "report_nm": "[정정] 사업보고서",
                        "rcept_no": "20260401000001",
                        "flr_nm": "검증기업",
                        "rcept_dt": "20260401",
                        "rm": "정정",
                    }
                ],
            },
            {
                "status": "000",
                "page_no": 2,
                "total_page": 2,
                "list": [
                    {
                        "corp_code": "00126380",
                        "corp_name": "검증기업",
                        "stock_code": "005930",
                        "corp_cls": "Y",
                        "report_nm": "분기보고서",
                        "rcept_no": "20260515000002",
                        "flr_nm": "검증기업",
                        "rcept_dt": "20260515",
                        "rm": "",
                    }
                ],
            },
        )
        result = OpenDartProvider(
            self._store(), client=fake, clock=lambda: FIXED_NOW
        ).collect_disclosures(("00126380",), begin_date="20260101", end_date="20261011")

        self.assertEqual(result.status, CollectionStatus.OK)
        self.assertEqual(len(result.records), 2)
        self.assertTrue(result.records[0].revised)
        self.assertFalse(result.records[1].revised)
        self.assertEqual(result.records[0].metadata["reception_number"], "20260401000001")
        self.assertEqual(result.records[0].metadata["publication_time_basis"], "FIRST_OBSERVED_AT_COLLECTION")
        self.assertEqual(len(fake.calls), 2)
        first_query = parse_qs(urlsplit(fake.calls[0]["url"]).query)
        self.assertEqual(first_query["corp_code"], ["00126380"])
        self.assertEqual(first_query["page_no"], ["1"])
        self.assertEqual(first_query["crtfc_key"], [self.api_key])
        self.assertNotIn(self.api_key, repr(result))

    def test_full_financials_distinguish_cfs_quarter_and_cumulative_currency(self) -> None:
        fake = FakeClient(
            {
                "status": "000",
                "list": [
                    {
                        "rcept_no": "20261010000001",
                        "reprt_code": "11014",
                        "bsns_year": "2026",
                        "corp_code": "00126380",
                        "sj_div": "IS",
                        "sj_nm": "연결 손익계산서",
                        "account_id": "ifrs-full_Revenue",
                        "account_nm": "수익",
                        "account_detail": "",
                        "thstrm_nm": "제 57 기 3분기",
                        "thstrm_amount": "10,000",
                        "thstrm_add_amount": "31,000",
                        "ord": "1",
                        "currency": "KRW",
                        "fs_div": "CFS",
                    },
                    {
                        "rcept_no": "20261010000001",
                        "reprt_code": "11014",
                        "bsns_year": "2026",
                        "corp_code": "00126380",
                        "sj_div": "BS",
                        "sj_nm": "연결 재무상태표",
                        "account_id": "ifrs-full_Assets",
                        "account_nm": "자산총계",
                        "account_detail": "",
                        "thstrm_nm": "제 57 기 3분기말",
                        "thstrm_amount": "99,000",
                        "thstrm_add_amount": "-",
                        "ord": "2",
                        "currency": "KRW",
                        "fs_div": "CFS",
                    },
                ],
            }
        )
        result = OpenDartProvider(
            self._store(), client=fake, clock=lambda: FIXED_NOW
        ).collect_full_financials(
            ("00126380",),
            business_year="2026",
            report_code="11014",
            financial_division="CFS",
        )

        self.assertEqual(result.status, CollectionStatus.OK)
        self.assertEqual(len(result.records), 3)
        self.assertEqual(
            [record.metadata["amount_basis"] for record in result.records],
            ["CURRENT_OR_QUARTER", "CUMULATIVE", "CURRENT_OR_QUARTER"],
        )
        self.assertEqual([record.value for record in result.records], ["10000", "31000", "99000"])
        self.assertEqual({record.currency for record in result.records}, {"KRW"})
        self.assertEqual({record.metadata["financial_division"] for record in result.records}, {"CFS"})
        query = parse_qs(urlsplit(fake.calls[0]["url"]).query)
        self.assertEqual(query["fs_div"], ["CFS"])
        self.assertEqual(query["reprt_code"], ["11014"])

    def test_auth_rate_and_timeout_fail_without_response_or_secret_leak(self) -> None:
        missing = OpenDartProvider(
            credential_store(), client=FakeClient(), clock=lambda: FIXED_NOW
        ).collect_disclosures(("00126380",), begin_date="20260101", end_date="20261011")
        self.assertEqual(missing.issues[0].code, "CONFIGURATION_REQUIRED")

        marker = f"{self.api_key}/private/path/456789"
        limited = OpenDartProvider(
            self._store(),
            client=FakeClient({"status": "020", "message": marker}),
            clock=lambda: FIXED_NOW,
        ).collect_disclosures(("00126380",), begin_date="20260101", end_date="20261011")
        self.assertEqual(limited.issues[0].code, "RATE_LIMITED")
        self.assertNotIn(marker, repr(limited))
        self.assertNotIn(self.api_key, repr(limited))

        timed_out = OpenDartProvider(
            self._store(),
            client=FakeClient(ProviderError("NETWORK_ERROR", marker, retryable=True)),
            clock=lambda: FIXED_NOW,
        ).collect_full_financials(
            ("00126380",),
            business_year="2026",
            report_code="11011",
            financial_division="OFS",
        )
        self.assertEqual(timed_out.issues[0].code, "NETWORK_ERROR")
        self.assertNotIn("456789", repr(timed_out))


class EcosProviderTests(unittest.TestCase):
    api_key = "E" * 32
    request = EcosSeriesRequest("200Y101", "A", "2020", "2023", "10101")

    @staticmethod
    def payload(value: str = "2407575.8") -> dict[str, Any]:
        return {
            "StatisticSearch": {
                "list_total_count": 1,
                "row": [
                    {
                        "STAT_CODE": "200Y101",
                        "STAT_NAME": "주요지표(연간지표)",
                        "ITEM_CODE1": "10101",
                        "ITEM_NAME1": "국내총생산(명목, 원화표시)",
                        "ITEM_CODE2": None,
                        "ITEM_NAME2": None,
                        "ITEM_CODE3": None,
                        "ITEM_NAME3": None,
                        "ITEM_CODE4": None,
                        "ITEM_NAME4": None,
                        "UNIT_NAME": "십억원",
                        "TIME": "2023",
                        "DATA_VALUE": value,
                        "WGT": "",
                    }
                ],
            }
        }

    def _store(self, key: str | None = None) -> CredentialStore:
        return credential_store(**({"ecos__api_key": key or self.api_key}))

    def test_operational_statistics_require_user_key_and_preserve_unit_period_source(self) -> None:
        fake = FakeClient(self.payload())
        result = EcosProvider(
            self._store(), client=fake, clock=lambda: FIXED_NOW
        ).collect_statistics((self.request,))

        self.assertEqual(result.status, CollectionStatus.OK)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.provider, "ecos")
        self.assertEqual(record.kind, RecordKind.MACRO)
        self.assertEqual(record.unit, "십억원")
        self.assertEqual(record.value, "2407575.8")
        self.assertEqual(record.metadata["period"], "2023")
        self.assertEqual(record.metadata["cycle"], "A")
        self.assertEqual(record.instrument_id, "200Y101:10101")
        self.assertFalse(record.metadata["revision_flag_available"])
        self.assertIn(f"/{self.api_key}/", fake.calls[0]["url"])
        self.assertIn("/%3F/%3F/%3F", fake.calls[0]["url"])
        self.assertNotIn(self.api_key, repr(result))

    def test_operational_statistics_are_paced_and_stop_at_run_call_cap(self) -> None:
        sleeps: list[float] = []
        fake = FakeClient(self.payload(), self.payload())
        result = EcosProvider(
            self._store(),
            client=fake,
            clock=lambda: FIXED_NOW,
            monotonic=lambda: 0.0,
            sleeper=sleeps.append,
            max_logical_calls_per_run=1,
        ).collect_statistics((self.request, self.request))

        self.assertEqual(result.status, CollectionStatus.PARTIAL)
        self.assertEqual(len(result.records), 1)
        self.assertEqual([issue.code for issue in result.issues], ["RUN_CALL_LIMIT"])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(sleeps, [])

        paced_fake = FakeClient(self.payload(), self.payload())
        paced = EcosProvider(
            self._store(),
            client=paced_fake,
            clock=lambda: FIXED_NOW,
            monotonic=lambda: 0.0,
            sleeper=sleeps.append,
        ).collect_statistics((self.request, self.request))
        self.assertEqual(paced.status, CollectionStatus.OK)
        self.assertEqual(len(paced_fake.calls), 2)
        self.assertEqual(sleeps, [0.25])

    def test_sample_key_is_confined_to_nonpersistable_connectivity_demo(self) -> None:
        operational_client = FakeClient()
        operational = EcosProvider(
            self._store("sample"), client=operational_client, clock=lambda: FIXED_NOW
        ).collect_statistics((self.request,))
        self.assertEqual(operational.status, CollectionStatus.ERROR)
        self.assertEqual(operational.issues[0].code, "CONFIGURATION_REQUIRED")
        self.assertEqual(operational_client.calls, [])

        demo_client = FakeClient(self.payload())
        demo = EcosProvider(
            credential_store(), client=demo_client, clock=lambda: FIXED_NOW
        ).connectivity_demo()
        self.assertIsInstance(demo, ConnectivityDemoResult)
        self.assertTrue(demo.ok)
        self.assertFalse(demo.persistable)
        self.assertEqual(demo.observed_rows, 1)
        self.assertFalse(hasattr(demo, "records"))
        self.assertIn("/sample/", demo_client.calls[0]["url"])
        self.assertIn("운영 자료로 저장하지 않습니다", demo.message)

    def test_auth_rate_timeout_partial_and_sensitive_provider_text_are_sanitized(self) -> None:
        marker = f"{self.api_key}/local/private/456789"
        auth = EcosProvider(
            self._store(),
            client=FakeClient({"RESULT": {"CODE": "INFO-100", "MESSAGE": marker}}),
            clock=lambda: FIXED_NOW,
        ).collect_statistics((self.request,))
        self.assertEqual(auth.issues[0].code, "AUTHENTICATION_FAILED")
        self.assertNotIn(marker, repr(auth))

        limited_payload = EcosProvider(
            self._store(),
            client=FakeClient({"RESULT": {"CODE": "ERROR-602", "MESSAGE": marker}}),
            clock=lambda: FIXED_NOW,
        ).collect_statistics((self.request,))
        self.assertEqual(limited_payload.issues[0].code, "RATE_LIMITED")
        self.assertNotIn(marker, repr(limited_payload))

        requests = (
            self.request,
            EcosSeriesRequest("200Y101", "A", "2022", "2023", "10102"),
        )
        partial = EcosProvider(
            self._store(),
            client=FakeClient(
                self.payload(),
                ProviderError("RATE_LIMITED", marker, retryable=True),
            ),
            clock=lambda: FIXED_NOW,
        ).collect_statistics(requests)
        self.assertEqual(partial.status, CollectionStatus.PARTIAL)
        self.assertEqual(len(partial.records), 1)
        self.assertEqual(partial.issues[0].code, "RATE_LIMITED")
        self.assertNotIn("456789", repr(partial))

        timeout = EcosProvider(
            self._store(),
            client=FakeClient(ProviderError("NETWORK_ERROR", marker, retryable=True)),
            clock=lambda: FIXED_NOW,
        ).collect_statistics((self.request,))
        self.assertEqual(timeout.status, CollectionStatus.ERROR)
        self.assertEqual(timeout.issues[0].code, "NETWORK_ERROR")
        self.assertNotIn(self.api_key, repr(timeout))


if __name__ == "__main__":
    unittest.main()
