from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from asset_assistant.automation import (
    AutomationService,
    AutomationSummary,
    REPORT_SECTION_TITLES,
    run,
)
from asset_assistant.domain import DomainValidationError
from asset_assistant.gui_service import render_report_for_display
from asset_assistant.kr_providers import EcosSeriesRequest
from asset_assistant.market_data import (
    CollectionIssue,
    CollectionResult,
    CollectionStatus,
    MarketDataStore,
    NormalizedRecord,
    RecordKind,
)
from asset_assistant.strategy import StrategyKind


NOW = datetime(2026, 10, 11, 6, 5, tzinfo=timezone.utc)


def record(*, provider: str = "bls", published_at: datetime | None = NOW) -> NormalizedRecord:
    return NormalizedRecord(
        provider=provider,
        kind=RecordKind.MACRO,
        record_id="CUUR0000SA0:2026-08-01",
        instrument_id="CUUR0000SA0",
        market="US",
        currency=None,
        effective_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        published_at=published_at,
        collected_at=NOW,
        unit="INDEX_1982_84_100",
        value="324.1",
        source_url="https://api.bls.gov/publicAPI/v1/timeseries/data/",
        metadata={"publication_time_basis": "FIRST_OBSERVED_AT_COLLECTION"},
    )


def kis_records(days: int = 126) -> tuple[NormalizedRecord, ...]:
    records: list[NormalizedRecord] = []
    first = NOW - timedelta(days=days - 1)
    for index in range(days):
        observed = first + timedelta(days=index)
        for metric, value, unit in (
            ("price", str(100 + index), "KRW"),
            ("volume", str(1000 + index), "SHARES"),
            ("trading_value", str(1000000 + index), "KRW"),
        ):
            records.append(
                NormalizedRecord(
                    provider="kis",
                    kind=RecordKind.PRICE_VOLUME,
                    record_id=f"paper:UN:005930:{observed.isoformat()}:{metric}",
                    instrument_id="005930",
                    market="KRX_NXT_CONSOLIDATED",
                    currency="KRW" if unit == "KRW" else None,
                    effective_at=observed,
                    published_at=observed,
                    collected_at=NOW,
                    unit=unit,
                    value=value,
                    source_url="https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price",
                    metadata={
                        "metric": metric,
                        "environment": "paper",
                        "time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                    },
                )
            )
    return tuple(records)


class FakeBls:
    def __init__(self, result: CollectionResult) -> None:
        self.result = result
        self.calls: list[tuple[str, ...]] = []

    def collect(self, targets: tuple[str, ...]) -> CollectionResult:
        self.calls.append(targets)
        return self.result


class MarketStoreTests(unittest.TestCase):
    def test_collection_preserves_provenance_and_failed_update_keeps_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            good = CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),))
            first_id = store.save_collection(good)
            failed = CollectionResult(
                "bls",
                NOW + timedelta(hours=1),
                NOW + timedelta(hours=1),
                CollectionStatus.ERROR,
                issues=(CollectionIssue("TIMEOUT", "갱신 시간 초과"),),
            )
            second_id = store.save_collection(failed)

            self.assertGreater(second_id, first_id)
            rows = store.latest_records(provider="bls")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["value_text"], "324.1")
            self.assertEqual(rows[0]["currency"], None)
            self.assertEqual(rows[0]["effective_at"], "2026-08-01T00:00:00+00:00")
            self.assertEqual(store.provider_summaries()[0]["status"], "ERROR")

    def test_same_provider_record_is_deduplicated_but_correction_is_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            original = record()
            store.save_collection(
                CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (original,))
            )
            later = NOW + timedelta(days=1)
            duplicate = replace(original, published_at=later, collected_at=later)
            duplicate_run = store.save_collection(
                CollectionResult("bls", later, later, CollectionStatus.OK, (duplicate,))
            )
            self.assertEqual(len(store.latest_records(provider="bls")), 1)
            latest_run = next(
                row for row in store.provider_summaries() if row["provider"] == "bls"
            )
            self.assertEqual(latest_run["id"], duplicate_run)
            self.assertEqual(latest_run["record_count"], 0)

            corrected = replace(duplicate, value="324.2", revised=True)
            store.save_collection(
                CollectionResult("bls", later, later, CollectionStatus.OK, (corrected,))
            )
            rows = store.latest_records(provider="bls")
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["value_text"], "324.2")

    def test_cutoff_excludes_future_effective_or_publication_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            store.save_collection(
                CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),))
            )
            before_publication = NOW - timedelta(minutes=1)
            self.assertEqual(
                store.latest_records(
                    provider="bls",
                    cutoff_at=before_publication,
                    require_known_publication_time=True,
                ),
                (),
            )
            after_publication = NOW + timedelta(minutes=1)
            self.assertEqual(
                len(
                    store.latest_records(
                        provider="bls",
                        cutoff_at=after_publication,
                        require_known_publication_time=True,
                    )
                ),
                1,
            )

    def test_cutoff_and_order_use_actual_instants_across_utc_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            cutoff = datetime(
                2026, 10, 11, 16, 0, tzinfo=timezone(timedelta(hours=9))
            )  # 07:00 UTC
            future = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
            future_record = NormalizedRecord(
                provider="bls",
                kind=RecordKind.MACRO,
                record_id="future-offset",
                instrument_id="FUTURE",
                market="US",
                currency=None,
                effective_at=future,
                published_at=future,
                collected_at=future,
                unit="INDEX",
                value="1",
                source_url="https://api.bls.gov/publicAPI/v1/timeseries/data/",
            )
            store.save_collection(
                CollectionResult("bls", future, future, CollectionStatus.OK, (future_record,))
            )
            self.assertEqual(
                store.latest_records(
                    provider="bls",
                    cutoff_at=cutoff,
                    require_known_publication_time=True,
                ),
                (),
            )

            ordered_store = MarketDataStore(Path(directory) / "ordered.sqlite3")
            later_instant = datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)
            earlier_instant = datetime(
                2026, 10, 11, 16, 0, tzinfo=timezone(timedelta(hours=9))
            )
            ordered = (
                replace(
                    future_record,
                    record_id="later-instant",
                    instrument_id="LATER",
                    effective_at=later_instant,
                    published_at=later_instant,
                    collected_at=later_instant,
                ),
                replace(
                    future_record,
                    record_id="earlier-instant",
                    instrument_id="EARLIER",
                    effective_at=earlier_instant,
                    published_at=earlier_instant,
                    collected_at=earlier_instant,
                ),
            )
            ordered_store.save_collection(
                CollectionResult(
                    "bls", later_instant, later_instant, CollectionStatus.OK, ordered
                )
            )
            identifiers = [
                row["instrument_id"]
                for row in ordered_store.latest_records(provider="bls")
            ]
            self.assertEqual(identifiers, ["LATER", "EARLIER"])

    def test_provider_summary_cutoff_uses_actual_instants_across_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            before_cutoff = datetime(2026, 10, 11, 6, 30, tzinfo=timezone.utc)
            cutoff = datetime(
                2026, 10, 11, 16, 0, tzinfo=timezone(timedelta(hours=9))
            )  # 07:00 UTC
            after_cutoff = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
            first_run = store.save_collection(
                CollectionResult(
                    "bls",
                    before_cutoff,
                    before_cutoff,
                    CollectionStatus.OK,
                    (),
                )
            )
            store.save_collection(
                CollectionResult(
                    "bls",
                    after_cutoff,
                    after_cutoff,
                    CollectionStatus.ERROR,
                    issues=(CollectionIssue("FUTURE_FAILURE", "future fixture"),),
                )
            )

            summaries = store.provider_summaries(cutoff_at=cutoff)

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["id"], first_run)
            self.assertEqual(summaries[0]["status"], "OK")

    def test_cutoff_excludes_records_observed_only_after_report_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            future_collection = NOW + timedelta(days=1)
            late_observation = replace(
                record(),
                record_id="late-observation",
                published_at=NOW - timedelta(days=1),
                collected_at=future_collection,
            )
            store.save_collection(
                CollectionResult(
                    "bls",
                    future_collection,
                    future_collection,
                    CollectionStatus.OK,
                    (late_observation,),
                )
            )

            self.assertEqual(
                store.latest_records(
                    provider="bls",
                    cutoff_at=NOW,
                    require_known_publication_time=True,
                ),
                (),
            )

    @unittest.skipIf(os.name == "nt", "POSIX 파일 모드 검사")
    def test_database_file_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            MarketDataStore(path).provider_summaries()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_provider_daily_call_budget_is_persistent_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            for _ in range(8):
                self.assertTrue(
                    store.reserve_provider_calls(
                        "bls",
                        "2026-10-11",
                        requested_calls=3,
                        daily_limit=25,
                        updated_at=NOW,
                    )
                )
            self.assertFalse(
                store.reserve_provider_calls(
                    "bls",
                    "2026-10-11",
                    requested_calls=3,
                    daily_limit=25,
                    updated_at=NOW,
                )
            )
            self.assertTrue(
                store.reserve_provider_calls(
                    "bls",
                    "2026-10-12",
                    requested_calls=3,
                    daily_limit=25,
                    updated_at=NOW,
                )
            )

    def test_job_claim_is_atomic_and_duplicate_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            scheduled = datetime(2026, 10, 11, 15, 0, tzinfo=timezone(timedelta(hours=9)))
            self.assertTrue(store.claim_job("collect", scheduled, NOW))
            self.assertFalse(store.claim_job("collect", scheduled, NOW))
            store.finish_job(
                "collect", scheduled, finished_at=NOW, status="OK", detail_code="OK"
            )
            rows = store.job_rows()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "OK")

    def test_failed_and_stale_running_jobs_can_be_retried_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            scheduled_error = datetime(
                2026, 10, 10, 15, 0, tzinfo=timezone(timedelta(hours=9))
            )
            self.assertTrue(store.claim_job("collect", scheduled_error, NOW))
            store.finish_job(
                "collect",
                scheduled_error,
                finished_at=NOW,
                status="ERROR",
                detail_code="SAFE_FAILURE",
            )
            self.assertTrue(
                store.claim_job("collect", scheduled_error, NOW + timedelta(minutes=1))
            )
            store.finish_job(
                "collect",
                scheduled_error,
                finished_at=NOW + timedelta(minutes=2),
                status="OK",
                detail_code="RECOVERED_OK",
            )

            scheduled_stale = scheduled_error + timedelta(days=1)
            self.assertTrue(store.claim_job("collect", scheduled_stale, NOW))
            self.assertFalse(
                store.claim_job("collect", scheduled_stale, NOW + timedelta(minutes=30))
            )
            self.assertTrue(
                store.claim_job("collect", scheduled_stale, NOW + timedelta(hours=3))
            )
            store.finish_job(
                "collect",
                scheduled_stale,
                finished_at=NOW + timedelta(hours=3, minutes=1),
                status="OK",
                detail_code="STALE_RECOVERED_OK",
            )

            attempts = [dict(row) for row in reversed(store.job_attempt_rows())]
            self.assertEqual(
                [(row["status"], row["detail_code"]) for row in attempts],
                [
                    ("ERROR", "SAFE_FAILURE"),
                    ("OK", "RECOVERED_OK"),
                    ("ABANDONED_STALE", "STALE_RETRY"),
                    ("OK", "STALE_RECOVERED_OK"),
                ],
            )
            self.assertEqual(
                attempts[0]["scheduled_for"], scheduled_error.isoformat()
            )
            self.assertEqual(
                attempts[1]["scheduled_for"], scheduled_error.isoformat()
            )
            self.assertEqual(
                attempts[2]["scheduled_for"], scheduled_stale.isoformat()
            )
            self.assertEqual(
                attempts[3]["scheduled_for"], scheduled_stale.isoformat()
            )

    def test_operation_lease_prevents_overlap_and_recovers_after_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            self.assertTrue(
                store.acquire_operation_lease("market_collection", "owner-a", acquired_at=NOW)
            )
            self.assertFalse(
                store.acquire_operation_lease("market_collection", "owner-b", acquired_at=NOW)
            )
            self.assertTrue(store.operation_lease_active("market_collection", at=NOW))
            after_expiry = NOW + timedelta(hours=3)
            self.assertTrue(
                store.acquire_operation_lease(
                    "market_collection", "owner-b", acquired_at=after_expiry
                )
            )
            store.release_operation_lease("market_collection", "owner-a")
            self.assertTrue(
                store.operation_lease_active("market_collection", at=after_expiry)
            )
            store.release_operation_lease("market_collection", "owner-b")
            self.assertFalse(
                store.operation_lease_active("market_collection", at=after_expiry)
            )

    def test_monthly_snapshot_stores_only_summary_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            inserted = store.save_monthly_snapshot(
                month="2026-10",
                realm="MANUAL_ACTUAL",
                as_of=NOW,
                investment_value=None,
                complete=False,
                source_bytes=b"fixture-not-real-finance",
                created_at=NOW,
            )
            duplicate = store.save_monthly_snapshot(
                month="2026-10",
                realm="MANUAL_ACTUAL",
                as_of=NOW,
                investment_value=None,
                complete=False,
                source_bytes=b"fixture-not-real-finance",
                created_at=NOW,
            )
            self.assertTrue(inserted)
            self.assertFalse(duplicate)
            self.assertNotIn(b"fixture-not-real-finance", Path(store.path).read_bytes())

    def test_strategy_versions_are_append_only_and_assessments_keep_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            candidate_record = replace(record(), instrument_id="005930")
            store.save_collection(
                CollectionResult(
                    "bls", NOW, NOW, CollectionStatus.OK, (candidate_record,)
                )
            )
            stored_id = int(store.latest_records(provider="bls")[0]["id"])
            config = {"weights": {"MOMENTUM": "100"}}
            self.assertTrue(
                store.save_strategy_version(
                    version_name="v1", config=config, created_at=NOW
                )
            )
            self.assertFalse(
                store.save_strategy_version(
                    version_name="v1", config=config, created_at=NOW
                )
            )
            with self.assertRaises(DomainValidationError):
                store.save_strategy_version(
                    version_name="v1",
                    config={"weights": {"QUALITY": "100"}},
                    created_at=NOW,
                )
            assessment_id = store.save_candidate_assessment(
                strategy_version="v1",
                instrument_id="005930",
                cutoff_at=NOW,
                status="UNAVAILABLE",
                score=None,
                reasons=("가격 이력 부족",),
                source_record_ids=(stored_id,),
                created_at=NOW,
            )
            row = store.candidate_assessment_rows()[0]
            self.assertEqual(row["id"], assessment_id)
            self.assertEqual(json.loads(row["source_record_ids_json"]), [stored_id])
            with self.assertRaises(DomainValidationError):
                store.save_candidate_assessment(
                    strategy_version="v1",
                    instrument_id="005930",
                    cutoff_at=NOW,
                    status="UNAVAILABLE",
                    score=None,
                    reasons=("자료 부족",),
                    source_record_ids=(999999,),
                    created_at=NOW,
                )
            for invalid_ids in ((stored_id, stored_id), (0,), (-1,)):
                with self.subTest(source_record_ids=invalid_ids):
                    with self.assertRaises(DomainValidationError):
                        store.save_candidate_assessment(
                            strategy_version="v1",
                            instrument_id="005930",
                            cutoff_at=NOW,
                            status="UNAVAILABLE",
                            score=None,
                            reasons=("자료 부족",),
                            source_record_ids=invalid_ids,
                            created_at=NOW,
                        )

            with self.assertRaises(DomainValidationError):
                store.save_candidate_assessment(
                    strategy_version="unregistered-version",
                    instrument_id="005930",
                    cutoff_at=NOW,
                    status="UNAVAILABLE",
                    score=None,
                    reasons=("자료 부족",),
                    source_record_ids=(stored_id,),
                    created_at=NOW,
                )

    def test_candidate_assessment_rejects_sources_after_its_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            store.save_strategy_version(
                version_name="cutoff-v1",
                config={"weights": {"MOMENTUM": "100"}},
                created_at=NOW,
            )
            future = NOW + timedelta(days=1)
            candidate_record = replace(record(), instrument_id="005930")
            future_records = (
                replace(
                    candidate_record,
                    record_id="future-effective",
                    effective_at=future,
                ),
                replace(
                    candidate_record,
                    record_id="future-publication",
                    published_at=future,
                    collected_at=future,
                ),
                replace(
                    candidate_record,
                    record_id="future-collection",
                    collected_at=future,
                ),
            )
            store.save_collection(
                CollectionResult(
                    "bls",
                    NOW,
                    future,
                    CollectionStatus.OK,
                    future_records,
                )
            )
            source_ids = {
                str(row["provider_record_id"]): int(row["id"])
                for row in store.latest_records(provider="bls")
            }

            for record_id in (
                "future-effective",
                "future-publication",
                "future-collection",
            ):
                with self.subTest(record_id=record_id):
                    with self.assertRaises(DomainValidationError):
                        store.save_candidate_assessment(
                            strategy_version="cutoff-v1",
                            instrument_id="005930",
                            cutoff_at=NOW,
                            status="UNAVAILABLE",
                            score=None,
                            reasons=("미래 자료 차단",),
                            source_record_ids=(source_ids[record_id],),
                            created_at=NOW,
                        )

    def test_candidate_assessment_rejects_invalid_audit_state_and_wrong_instrument(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            source = replace(record(), instrument_id="005930")
            store.save_collection(
                CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (source,))
            )
            source_id = int(store.latest_records(provider="bls")[0]["id"])
            store.save_strategy_version(
                version_name="audit-v1",
                config={"weights": {"QUALITY": "100"}},
                created_at=NOW,
            )
            base = {
                "strategy_version": "audit-v1",
                "instrument_id": "005930",
                "cutoff_at": NOW,
                "status": "ELIGIBLE",
                "score": Decimal("80"),
                "reasons": ("검증된 fixture 근거",),
                "source_record_ids": (source_id,),
                "created_at": NOW,
            }
            self.assertGreater(store.save_candidate_assessment(**base), 0)

            invalid_variants = (
                {"created_at": NOW - timedelta(seconds=1)},
                {"status": "UNKNOWN"},
                {"score": Decimal("100.01")},
                {"score": None},
                {"source_record_ids": ()},
                {"reasons": ()},
                {"status": "UNAVAILABLE", "score": Decimal("1")},
                {"instrument_id": "000660"},
            )
            for changes in invalid_variants:
                with self.subTest(changes=changes):
                    with self.assertRaises(DomainValidationError):
                        store.save_candidate_assessment(**(base | changes))

            unavailable = base | {
                "status": "UNAVAILABLE",
                "score": None,
                "source_record_ids": (),
                "reasons": ("자료 부족",),
            }
            self.assertGreater(store.save_candidate_assessment(**unavailable), 0)

    def test_report_rejects_missing_or_future_collection_run_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            current_run = store.save_collection(
                CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),))
            )
            valid_id = store.save_report(
                report_date="2026-10-11",
                created_at=NOW,
                status="OK",
                markdown="# fixture report",
                source_run_ids=(current_run,),
            )
            self.assertGreater(valid_id, 0)

            with self.assertRaises(DomainValidationError):
                store.save_report(
                    report_date="2026-10-11",
                    created_at=NOW,
                    status="PARTIAL",
                    markdown="# missing provenance",
                    source_run_ids=(999999,),
                )

            future = NOW + timedelta(days=1)
            future_run = store.save_collection(
                CollectionResult("bls", future, future, CollectionStatus.OK, ())
            )
            with self.assertRaises(DomainValidationError):
                store.save_report(
                    report_date="2026-10-11",
                    created_at=NOW,
                    status="PARTIAL",
                    markdown="# future provenance",
                    source_run_ids=(future_run,),
                )


class AutomationTests(unittest.TestCase):
    def test_ecos_persistent_safety_budget_blocks_a_fourth_full_run(self) -> None:
        class Credentials:
            def get_secret(self, provider: str, field: str) -> str | None:
                if (provider, field) == ("ecos", "api_key"):
                    return "E" * 32
                return None

        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            service = AutomationService(
                store=store,
                credential_store=Credentials(),  # type: ignore[arg-type]
                clock=lambda: NOW,
            )
            service.set_ecos_series(
                EcosSeriesRequest("200Y101", "A", "2020", "2023", "10101")
            )
            store.set_setting("providers.enabled", ["ecos"], updated_at=NOW)
            failed = CollectionResult(
                "ecos",
                NOW,
                NOW,
                CollectionStatus.ERROR,
                issues=(CollectionIssue("NETWORK_ERROR", "fixture"),),
            )
            with patch(
                "asset_assistant.automation.EcosProvider.collect_statistics",
                return_value=failed,
            ) as collect:
                summaries = [service.collect() for _ in range(4)]

            self.assertEqual(collect.call_count, 3)
            self.assertTrue(all(summary.status == "ERROR" for summary in summaries))
            self.assertFalse(
                store.reserve_provider_calls(
                    "ecos",
                    NOW.astimezone(timezone(timedelta(hours=9))).date().isoformat(),
                    requested_calls=1,
                    daily_limit=900,
                    updated_at=NOW,
                )
            )

    def make_service(self, directory: str, result: CollectionResult) -> AutomationService:
        store = MarketDataStore(Path(directory) / "market.sqlite3")
        store.set_setting("providers.enabled", ["bls"], updated_at=NOW)
        return AutomationService(
            store=store,
            bls_provider=FakeBls(result),  # type: ignore[arg-type]
            clock=lambda: NOW,
        )

    def test_collect_then_seven_section_report_without_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),))
            service = self.make_service(directory, result)
            summary = service.collect()
            report = service.generate_report(now=NOW)

            self.assertEqual(summary.status, "OK")
            positions = [report.index(f"## {title}") for title in REPORT_SECTION_TITLES]
            self.assertEqual(positions, sorted(positions))
            self.assertIn("324.1", report)
            self.assertIn("기준 2026-08-01", report)
            self.assertIn("미입력(0원으로 간주하지 않음)", report)
            self.assertIn("제안 주문안 없음", report)
            self.assertIn("미래 가격·수익률·확률을 생성하지 않았습니다", report)
            self.assertNotRegex(report, r"\b\d+\s*주\b")

    def test_overlapping_manual_collection_is_stopped_before_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),))
            service = self.make_service(directory, result)
            self.assertTrue(
                service.store.acquire_operation_lease(
                    "market_collection", "other-process", acquired_at=NOW
                )
            )

            summary = service.collect()

            self.assertEqual(summary.status, "ERROR")
            self.assertIn("중복 호출", summary.message)
            self.assertEqual(service.bls_provider.calls, [])  # type: ignore[attr-defined]

    def test_report_is_partial_while_manual_collection_lease_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),))
            service = self.make_service(directory, result)
            service._collect_unlocked()
            self.assertTrue(
                service.store.acquire_operation_lease(
                    "market_collection", "other-process", acquired_at=NOW
                )
            )

            report = service.generate_report(now=NOW)

            self.assertIn("공식자료 상태: PARTIAL", report)
            self.assertIn("시장자료 수집이 아직 진행 중", report)

    def test_partial_failure_is_reported_and_previous_record_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(
                directory,
                CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),)),
            )
            service.collect()
            service.bls_provider = FakeBls(  # type: ignore[assignment]
                CollectionResult(
                    "bls",
                    NOW,
                    NOW,
                    CollectionStatus.ERROR,
                    issues=(CollectionIssue("RATE_LIMITED", "호출 제한"),),
                )
            )
            failed = service.collect()
            report = service.generate_report(now=NOW)
            self.assertEqual(failed.status, "ERROR")
            self.assertIn("이전 정상 자료는 보존", report)
            self.assertIn("324.1", report)
            source_runs = json.loads(service.store.latest_report()["source_run_ids_json"])
            record_run = service.store.latest_records(provider="bls")[0]["run_id"]
            latest_status_run = service.store.provider_summaries()[0]["id"]
            self.assertIn(record_run, source_runs)
            self.assertIn(latest_status_run, source_runs)

    def test_provider_status_separates_last_good_data_from_failed_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            good_record = record()
            store.save_collection(
                CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (good_record,))
            )
            failed_at = NOW + timedelta(hours=1)
            store.save_collection(
                CollectionResult(
                    "bls",
                    failed_at,
                    failed_at,
                    CollectionStatus.ERROR,
                    issues=(CollectionIssue("NETWORK_ERROR", "fixture"),),
                )
            )
            store.set_setting("providers.enabled", ["bls"], updated_at=failed_at)
            service = AutomationService(store=store, clock=lambda: failed_at)

            status = next(row for row in service.provider_statuses() if row.code == "bls")

            self.assertEqual(status.state, "ERROR")
            self.assertEqual(status.last_effective, good_record.effective_at.isoformat())
            self.assertEqual(status.last_collected, good_record.collected_at.isoformat())
            self.assertEqual(status.last_attempted, failed_at.isoformat())
            report = service.generate_report(now=failed_at)
            self.assertIn(f"정상자료 수집 {good_record.collected_at.isoformat()}", report)
            self.assertIn(f"최근 갱신 시도 {failed_at.isoformat()}", report)

    def test_report_sanitizes_external_unit_and_deduplicates_corrected_news(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            malicious = replace(
                record(provider="ecos"),
                record_id="macro-safe-output",
                instrument_id="SAFE_MACRO",
                unit="<img src=x>|**fake**",
            )
            store.save_collection(
                CollectionResult("ecos", NOW, NOW, CollectionStatus.OK, (malicious,))
            )
            first_news = NormalizedRecord(
                provider="fed_news",
                kind=RecordKind.NEWS,
                record_id="stable-guid",
                instrument_id="FEDERAL_RESERVE_PRESS_RELEASE",
                market="US_POLICY",
                currency=None,
                effective_at=NOW - timedelta(hours=2),
                published_at=NOW - timedelta(hours=2),
                collected_at=NOW - timedelta(hours=1),
                unit="ARTICLE_METADATA",
                source_url="https://www.federalreserve.gov/newsevents/pressreleases/test.htm",
                metadata={"title": "Original title"},
            )
            corrected_news = replace(
                first_news,
                collected_at=NOW,
                revised=True,
                metadata={
                    "title": (
                        "Minutes of the Federal Open Market Committee, "
                        "September 15-16, 2026"
                    )
                },
            )
            injected_news = replace(
                first_news,
                record_id="injection-guid",
                collected_at=NOW,
                metadata={
                    "title": (
                        "<tag> & **bold** | [link](https://example.invalid)\n"
                        "## injected"
                    )
                },
            )
            store.save_collection(
                CollectionResult(
                    "fed_news",
                    NOW - timedelta(hours=1),
                    NOW - timedelta(hours=1),
                    CollectionStatus.OK,
                    (first_news,),
                )
            )
            store.save_collection(
                CollectionResult(
                    "fed_news",
                    NOW,
                    NOW,
                    CollectionStatus.OK,
                    (corrected_news, injected_news),
                )
            )
            store.set_setting("providers.enabled", ["ecos", "fed_news"], updated_at=NOW)
            report = AutomationService(store=store, clock=lambda: NOW).generate_report(now=NOW)

            self.assertNotIn("<img", report)
            self.assertNotIn("|**fake**", report)
            self.assertIn("&lt;img", report)
            self.assertIn(
                "Minutes of the Federal Open Market Committee, September 15-16, 2026",
                report,
            )
            self.assertNotIn("September \\[계좌번호 형태 마스킹\\]", report)
            self.assertNotIn("Original title", report)
            self.assertIn("&lt;tag&gt; &amp;", report)
            self.assertIn(r"\*\*bold\*\*", report)
            self.assertIn(r"\|", report)
            self.assertIn(r"\[link\]\(https://example.invalid\)", report)
            self.assertNotIn("<tag>", report)
            self.assertNotIn("\n## injected", report)
            self.assertIn("제안 주문안 없음", report)

    def test_automation_report_cannot_overwrite_its_sqlite_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            MarketDataStore(database).provider_summaries()
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                run(
                    [
                        "--database",
                        str(database),
                        "report",
                        "--output",
                        str(database),
                    ]
                )
            self.assertEqual(raised.exception.code, 2)
            self.assertTrue(database.read_bytes().startswith(b"SQLite format 3"))
            self.assertNotIn(str(database), stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_scheduled_job_does_not_run_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeBls(CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),)))
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            store.set_setting("providers.enabled", ["bls"], updated_at=NOW)
            service = AutomationService(
                store=store,
                bls_provider=fake,  # type: ignore[arg-type]
                clock=lambda: NOW,
            )
            first = service.run_scheduled("collect", now=NOW)
            second = service.run_scheduled("collect", now=NOW)
            self.assertEqual(first.status, "OK")
            self.assertEqual(second.status, "SKIPPED_DUPLICATE")
            self.assertEqual(len(fake.calls), 1)

    def test_scheduled_collect_reassesses_candidates_and_marks_failure_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            service = AutomationService(store=store, clock=lambda: NOW)
            service.set_strategy_mix(
                "scheduled-v1", {StrategyKind.MOMENTUM: Decimal("100")}
            )
            collected = AutomationSummary("OK", "수집 완료")

            with (
                patch.object(service, "collect", return_value=collected),
                patch.object(
                    service,
                    "assess_configured_candidates",
                    return_value=(),
                ) as assess,
            ):
                completed = service.run_scheduled("collect", now=NOW)

            self.assertEqual(completed.status, "OK")
            assess.assert_called_once_with(cutoff_at=NOW)

        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            service = AutomationService(store=store, clock=lambda: NOW)
            service.set_strategy_mix(
                "scheduled-v1", {StrategyKind.MOMENTUM: Decimal("100")}
            )
            with (
                patch.object(service, "collect", return_value=collected),
                patch.object(
                    service,
                    "assess_configured_candidates",
                    side_effect=DomainValidationError("민감 원문 fixture"),
                ),
            ):
                partial = service.run_scheduled("collect", now=NOW)

            self.assertEqual(partial.status, "PARTIAL")
            self.assertNotIn("민감 원문", partial.message)
            self.assertEqual(store.job_rows()[0]["status"], "PARTIAL")

    def test_scheduled_collect_assesses_just_collected_records_not_later_records(self) -> None:
        scheduled = datetime(
            2026, 10, 12, 15, 0, tzinfo=timezone(timedelta(hours=9))
        )
        collected = scheduled + timedelta(seconds=3)
        after_collection = scheduled + timedelta(seconds=4)
        later = scheduled.replace(hour=17)
        clock = [scheduled]
        provider_calls: list[tuple[str, ...]] = []

        def observations(at: datetime, price: str) -> tuple[NormalizedRecord, ...]:
            return tuple(
                NormalizedRecord(
                    provider="kis",
                    kind=RecordKind.PRICE_VOLUME,
                    record_id=f"paper:UN:005930:{at.isoformat()}:{metric}",
                    instrument_id="005930",
                    market="KRX_NXT_CONSOLIDATED",
                    currency="KRW" if unit == "KRW" else None,
                    effective_at=at,
                    published_at=at,
                    collected_at=at,
                    unit=unit,
                    value=value,
                    source_url=(
                        "https://openapivts.koreainvestment.com:29443/"
                        "uapi/domestic-stock/v1/quotations/inquire-price"
                    ),
                    metadata={"metric": metric, "environment": "paper"},
                )
                for metric, value, unit in (
                    ("price", price, "KRW"),
                    ("volume", "1000", "SHARES"),
                    ("trading_value", "70000000", "KRW"),
                )
            )

        current_records = observations(collected, "71000")
        later_records = observations(later, "99000")

        class FakeKis:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def collect_quotes(self, targets: tuple[str, ...]) -> CollectionResult:
                provider_calls.append(targets)
                clock[0] = after_collection
                return CollectionResult(
                    "kis",
                    scheduled,
                    collected,
                    CollectionStatus.OK,
                    current_records,
                )

        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            store.save_collection(
                CollectionResult("kis", later, later, CollectionStatus.OK, later_records)
            )
            store.set_setting("providers.enabled", ["kis"], updated_at=scheduled)
            store.set_setting(
                "provider.targets.kis", ["005930"], updated_at=scheduled
            )
            store.set_setting(
                "provider.kis.environment", "paper", updated_at=scheduled
            )
            service = AutomationService(store=store, clock=lambda: clock[0])
            service.set_strategy_mix(
                "scheduled-current-data-v1",
                {StrategyKind.BROAD_MARKET: Decimal("100")},
            )

            with patch("asset_assistant.automation.KisQuoteProvider", FakeKis):
                first = service.run_scheduled("collect", now=scheduled)
                second = service.run_scheduled("collect", now=scheduled)

            self.assertEqual(first.status, "OK")
            self.assertEqual(second.status, "SKIPPED_DUPLICATE")
            self.assertEqual(provider_calls, [("005930",)])

            assessment = store.candidate_assessment_rows(limit=1)[0]
            source_ids = json.loads(assessment["source_record_ids_json"])
            self.assertEqual(len(source_ids), 3)
            self.assertEqual(
                datetime.fromisoformat(assessment["cutoff_at"]), after_collection
            )
            self.assertLessEqual(
                datetime.fromisoformat(assessment["cutoff_at"]),
                datetime.fromisoformat(assessment["created_at"]),
            )
            all_rows = {int(row["id"]): row for row in store.latest_records(provider="kis")}
            self.assertEqual(
                {datetime.fromisoformat(all_rows[value]["collected_at"]) for value in source_ids},
                {collected},
            )
            self.assertNotIn(
                later,
                {
                    datetime.fromisoformat(all_rows[value]["collected_at"])
                    for value in source_ids
                },
            )
            self.assertEqual(
                datetime.fromisoformat(store.job_rows(limit=1)[0]["scheduled_for"]),
                scheduled,
            )

    def test_scheduled_collect_without_strategy_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            service = AutomationService(store=store, clock=lambda: NOW)
            with patch.object(
                service,
                "collect",
                return_value=AutomationSummary("OK", "수집 완료"),
            ):
                summary = service.run_scheduled("collect", now=NOW)
            self.assertEqual(summary.status, "OK")
            self.assertEqual(store.candidate_assessment_rows(), ())

    def test_report_marks_running_15h_collection_as_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_time = datetime(
                2026, 10, 11, 16, 0, tzinfo=timezone(timedelta(hours=9))
            )
            collected = report_time - timedelta(minutes=90)
            stored_record = replace(
                record(), published_at=collected, collected_at=collected
            )
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            store.set_setting("providers.enabled", ["bls"], updated_at=collected)
            store.save_collection(
                CollectionResult(
                    "bls", collected, collected, CollectionStatus.OK, (stored_record,)
                )
            )
            scheduled_collect = datetime(
                2026, 10, 11, 15, 0, tzinfo=timezone(timedelta(hours=9))
            )
            self.assertTrue(store.claim_job("collect", scheduled_collect, scheduled_collect))
            service = AutomationService(store=store, clock=lambda: report_time)

            report = service.generate_report(now=report_time)

            self.assertIn("공식자료 상태: PARTIAL", report)
            self.assertIn("수집 작업이 아직 완료되지 않아", report)
            self.assertEqual(store.latest_report()["status"], "PARTIAL")

    def test_report_marks_failed_or_partial_15h_collection_as_partial(self) -> None:
        report_time = datetime(
            2026, 10, 11, 16, 0, tzinfo=timezone(timedelta(hours=9))
        )
        scheduled_collect = report_time.replace(hour=15)
        for collect_status in ("ERROR", "PARTIAL"):
            with self.subTest(collect_status=collect_status):
                with tempfile.TemporaryDirectory() as directory:
                    store = MarketDataStore(Path(directory) / "market.sqlite3")
                    store.set_setting(
                        "providers.enabled", ["bls"], updated_at=report_time
                    )
                    stored_record = replace(
                        record(),
                        published_at=scheduled_collect,
                        collected_at=scheduled_collect,
                    )
                    store.save_collection(
                        CollectionResult(
                            "bls",
                            scheduled_collect,
                            scheduled_collect,
                            CollectionStatus.OK,
                            (stored_record,),
                        )
                    )
                    self.assertTrue(
                        store.claim_job(
                            "collect", scheduled_collect, scheduled_collect
                        )
                    )
                    store.finish_job(
                        "collect",
                        scheduled_collect,
                        finished_at=scheduled_collect + timedelta(minutes=5),
                        status=collect_status,
                        detail_code=f"TEST_{collect_status}",
                    )
                    service = AutomationService(
                        store=store, clock=lambda: report_time
                    )

                    report = service.generate_report(
                        now=report_time,
                        scheduled=True,
                        scheduled_for=report_time,
                    )

                    self.assertIn("공식자료 상태: PARTIAL", report)
                    self.assertIn(
                        f"수집 작업 상태가 {collect_status}", report
                    )
                    self.assertEqual(store.latest_report()["status"], "PARTIAL")

    def test_scheduled_report_without_15h_job_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_time = datetime(
                2026, 10, 11, 16, 0, tzinfo=timezone(timedelta(hours=9))
            )
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            store.set_setting("providers.enabled", ["bls"], updated_at=report_time)
            stored_record = replace(
                record(), published_at=report_time, collected_at=report_time
            )
            store.save_collection(
                CollectionResult(
                    "bls", report_time, report_time, CollectionStatus.OK, (stored_record,)
                )
            )
            service = AutomationService(store=store, clock=lambda: report_time)

            summary = service.run_scheduled("report", now=report_time)

            self.assertEqual(summary.status, "PARTIAL")
            self.assertIn("15:00 수집 작업 실행 이력이 없어", service.latest_report())
            duplicate = service.run_scheduled("report", now=report_time)
            self.assertEqual(duplicate.status, "SKIPPED_DUPLICATE")
            self.assertIsNone(duplicate.report)

    def test_catchup_report_uses_historical_cutoff_but_manual_report_uses_now(self) -> None:
        scheduled_for = datetime(
            2026, 10, 10, 16, 0, tzinfo=timezone(timedelta(hours=9))
        )
        current = scheduled_for + timedelta(hours=18)
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            at_cutoff = replace(
                record(),
                record_id="catchup-cutoff",
                value="111.1",
                published_at=scheduled_for,
                collected_at=scheduled_for,
            )
            after_cutoff = replace(
                at_cutoff,
                value="222.2",
                revised=True,
                published_at=current - timedelta(hours=1),
                collected_at=current - timedelta(hours=1),
            )
            store.save_collection(
                CollectionResult(
                    "bls",
                    scheduled_for,
                    scheduled_for,
                    CollectionStatus.OK,
                    (at_cutoff,),
                )
            )
            store.save_collection(
                CollectionResult(
                    "bls",
                    current - timedelta(hours=1),
                    current - timedelta(hours=1),
                    CollectionStatus.OK,
                    (after_cutoff,),
                )
            )
            store.set_setting("providers.enabled", ["bls"], updated_at=current)
            service = AutomationService(store=store, clock=lambda: current)

            catchup = service.generate_report(
                now=current,
                scheduled=True,
                scheduled_for=scheduled_for,
            )
            catchup_row = store.latest_report()

            self.assertIn("111.1", catchup)
            self.assertNotIn("222.2", catchup)
            self.assertIn(f"자료 컷오프: {scheduled_for.isoformat()}", catchup)
            self.assertIn("역사적 컷오프", catchup)
            self.assertIn("15:00 수집 작업 실행 이력이 없어", catchup)
            self.assertEqual(catchup_row["report_date"], "2026-10-10")
            self.assertEqual(catchup_row["created_at"], current.isoformat())
            self.assertEqual(
                store.report_metadata(int(catchup_row["id"])),
                {
                    "data_scope": "AUTOMATED_MARKET",
                    "generation_kind": "SCHEDULED",
                    "data_cutoff_at": scheduled_for.isoformat(),
                },
            )

            manual = service.generate_report(now=current)
            self.assertIn("222.2", manual)
            self.assertIn(f"자료 컷오프: {current.isoformat()}", manual)
            manual_row = store.latest_report()
            self.assertEqual(
                store.report_metadata(int(manual_row["id"])),
                {
                    "data_scope": "AUTOMATED_MARKET",
                    "generation_kind": "MANUAL",
                    "data_cutoff_at": current.isoformat(),
                },
            )

    def test_candidate_assessment_error_marks_report_and_scheduled_job_partial(self) -> None:
        report_time = datetime(
            2026, 10, 11, 16, 0, tzinfo=timezone(timedelta(hours=9))
        )
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            stored_record = replace(
                record(), published_at=report_time, collected_at=report_time
            )
            store.save_collection(
                CollectionResult(
                    "bls", report_time, report_time, CollectionStatus.OK, (stored_record,)
                )
            )
            store.set_setting("providers.enabled", ["bls"], updated_at=report_time)
            store.set_setting(
                "strategy.current",
                {"version": "broken", "weights": "not-an-object"},
                updated_at=report_time,
            )
            scheduled_collect = report_time.replace(hour=15)
            self.assertTrue(
                store.claim_job("collect", scheduled_collect, scheduled_collect)
            )
            store.finish_job(
                "collect",
                scheduled_collect,
                finished_at=scheduled_collect + timedelta(minutes=1),
                status="OK",
                detail_code="OK",
            )
            service = AutomationService(store=store, clock=lambda: report_time)

            summary = service.run_scheduled("report", now=report_time)

            self.assertEqual(summary.status, "PARTIAL")
            self.assertIn("공식자료 상태: PARTIAL", summary.report)
            self.assertIn("평가를 중지했습니다", summary.report)
            self.assertEqual(store.latest_report()["status"], "PARTIAL")
            report_job = store.job_status("report", report_time)
            self.assertIsNotNone(report_job)
            self.assertEqual(report_job["status"], "PARTIAL")

    def test_before_due_catchup_claims_previous_day_without_blocking_today(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeBls(
                CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (record(),))
            )
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            store.set_setting("providers.enabled", ["bls"], updated_at=NOW)
            morning = datetime(
                2026, 10, 11, 9, 0, tzinfo=timezone(timedelta(hours=9))
            )
            afternoon = datetime(
                2026, 10, 11, 15, 0, tzinfo=timezone(timedelta(hours=9))
            )
            service = AutomationService(
                store=store,
                bls_provider=fake,  # type: ignore[arg-type]
                clock=lambda: morning,
            )

            self.assertEqual(service.run_scheduled("collect", now=morning).status, "OK")
            self.assertEqual(service.run_scheduled("collect", now=afternoon).status, "OK")

            scheduled_dates = {
                datetime.fromisoformat(row["scheduled_for"]).astimezone(
                    timezone(timedelta(hours=9))
                ).date().isoformat()
                for row in store.job_rows()
            }
            self.assertEqual(scheduled_dates, {"2026-10-10", "2026-10-11"})
            self.assertEqual(len(fake.calls), 2)

            report_summary = service.run_scheduled("report", now=morning)
            self.assertEqual(report_summary.report_date, "2026-10-10")

    def test_unknown_cli_argument_does_not_echo_secret_like_value(self) -> None:
        sensitive = "secret-110-123-456789"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            run(["status", "--unknown", sensitive])
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn(sensitive, stderr.getvalue())
        self.assertNotIn("456789", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_stored_invalid_kis_target_is_excluded_and_never_disclosed(self) -> None:
        sensitive = "계좌 110-123-456789"
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            stored_observation = NormalizedRecord(
                provider="kis",
                kind=RecordKind.PRICE_VOLUME,
                record_id="paper:fixture:invalid-target:price",
                instrument_id=sensitive,
                market="KRX_NXT_CONSOLIDATED",
                currency="KRW",
                effective_at=NOW,
                published_at=NOW,
                collected_at=NOW,
                unit="KRW",
                value="70000",
                source_url="https://example.invalid/fixture",
                metadata={"metric": "price", "environment": "paper"},
            )
            store.save_collection(
                CollectionResult(
                    "kis", NOW, NOW, CollectionStatus.OK, (stored_observation,)
                )
            )
            store.set_setting("providers.enabled", ["kis"], updated_at=NOW)
            # Deliberately bypass AutomationService.set_targets to model an old
            # or locally modified settings DB.
            store.set_setting("provider.targets.kis", [sensitive], updated_at=NOW)
            service = AutomationService(store=store, clock=lambda: NOW)
            service.set_strategy_mix(
                "n1-fixture", {StrategyKind.BROAD_MARKET: Decimal("100")}
            )

            self.assertEqual(
                service.assess_configured_candidates(cutoff_at=NOW), ()
            )
            with patch("asset_assistant.automation.KisQuoteProvider") as provider:
                collection = service.collect()
            provider.assert_not_called()
            report = service.generate_report(now=NOW)
            displayed = "\n".join(
                line.text for line in render_report_for_display(report)
            )
            error_text = " ".join(
                (
                    collection.message,
                    *(issue.message for result in collection.provider_results for issue in result.issues),
                )
            )

            self.assertEqual(collection.status, "ERROR")
            self.assertIn("공식자료 상태: PARTIAL", report)
            self.assertIn("KIS 저장 대상 설정에서 형식 위반 값을 제외했습니다", report)
            self.assertIn("[계좌번호 형태 마스킹]", displayed)
            self.assertEqual(store.candidate_assessment_rows(), ())
            for output in (report, displayed, error_text):
                self.assertNotIn(sensitive, output)
                self.assertNotIn("456789", output)
                self.assertNotIn("Traceback", output)

    def test_bls_user_target_label_uses_user_text_masking(self) -> None:
        sensitive = "110-123-456789"
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            user_series = replace(
                record(),
                record_id="user-series:fixture",
                instrument_id=sensitive,
            )
            store.save_collection(
                CollectionResult("bls", NOW, NOW, CollectionStatus.OK, (user_series,))
            )
            store.set_setting("providers.enabled", ["bls"], updated_at=NOW)
            store.set_setting("provider.targets.bls", [sensitive], updated_at=NOW)

            report = AutomationService(store=store, clock=lambda: NOW).generate_report(
                now=NOW
            )
            displayed = "\n".join(
                line.text for line in render_report_for_display(report)
            )

            self.assertIn("[계좌번호 형태 마스킹]", displayed)
            for output in (report, displayed):
                self.assertNotIn(sensitive, output)
                self.assertNotIn("456789", output)

    def test_configured_strategy_assesses_only_cutoff_records_and_report_traces_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            result = CollectionResult(
                "kis", NOW, NOW, CollectionStatus.OK, kis_records()
            )
            store.save_collection(result)
            store.set_setting("providers.enabled", ["kis"], updated_at=NOW)
            store.set_setting("provider.targets.kis", ["005930"], updated_at=NOW)
            store.set_setting(
                "strategy.policy_verified_instruments", ["005930"], updated_at=NOW
            )
            service = AutomationService(store=store, clock=lambda: NOW)
            service.set_strategy_mix(
                "momentum-v1", {StrategyKind.MOMENTUM: Decimal("100")}
            )

            assessments = service.assess_configured_candidates(cutoff_at=NOW)
            self.assertEqual(len(assessments), 1)
            self.assertIsNone(assessments[0].total_score)
            self.assertEqual(assessments[0].status.value, "UNAVAILABLE")
            self.assertEqual(len(assessments[0].source_record_ids), 3)
            available_records = {
                int(row["id"]): int(row["run_id"])
                for row in store.latest_records(
                    provider="kis", cutoff_at=NOW
                )
            }
            self.assertTrue(
                set(assessments[0].source_record_ids).issubset(available_records)
            )
            persisted_assessment = store.candidate_assessment_rows()[0]
            self.assertEqual(
                json.loads(persisted_assessment["source_record_ids_json"]),
                list(assessments[0].source_record_ids),
            )
            report = service.generate_report(now=NOW)
            self.assertIn("005930: UNAVAILABLE", report)
            self.assertIn("출처 레코드", report)
            self.assertIn("공식 종가·수정주가로 표시하지 않습니다", report)
            self.assertIn("최소 126개 시점 가격 필요", report)
            self.assertGreaterEqual(len(store.candidate_assessment_rows()), 2)
            report_source_runs = set(
                json.loads(store.latest_report()["source_run_ids_json"])
            )
            self.assertTrue(
                {
                    available_records[record_id]
                    for record_id in assessments[0].source_record_ids
                }.issubset(report_source_runs)
            )

    def test_report_snapshot_includes_every_candidate_source_run_beyond_display_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            targets: list[str] = []
            expected_run_ids: set[int] = set()
            first = NOW - timedelta(minutes=40)
            for index in range(35):
                instrument = f"{index:06d}"
                targets.append(instrument)
                observed = first + timedelta(minutes=index)
                records = tuple(
                    NormalizedRecord(
                        provider="kis",
                        kind=RecordKind.PRICE_VOLUME,
                        record_id=f"paper:UN:{instrument}:{observed.isoformat()}:{metric}",
                        instrument_id=instrument,
                        market="KRX_NXT_CONSOLIDATED",
                        currency="KRW" if unit == "KRW" else None,
                        effective_at=observed,
                        published_at=observed,
                        collected_at=observed,
                        unit=unit,
                        value=value,
                        source_url="https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price",
                        metadata={"metric": metric, "environment": "paper"},
                    )
                    for metric, value, unit in (
                        ("price", "100", "KRW"),
                        ("volume", "10", "SHARES"),
                        ("trading_value", "1000", "KRW"),
                    )
                )
                expected_run_ids.add(
                    store.save_collection(
                        CollectionResult(
                            "kis", observed, observed, CollectionStatus.OK, records
                        )
                    )
                )
            store.set_setting("providers.enabled", ["kis"], updated_at=NOW)
            store.set_setting("provider.targets.kis", targets, updated_at=NOW)
            service = AutomationService(store=store, clock=lambda: NOW)
            service.set_strategy_mix(
                "many-candidates-v1", {StrategyKind.QUALITY: Decimal("100")}
            )

            service.generate_report(now=NOW)

            report_run_ids = set(
                json.loads(store.latest_report()["source_run_ids_json"])
            )
            self.assertTrue(expected_run_ids.issubset(report_run_ids))

    def test_candidate_does_not_mix_partial_kis_observations_from_same_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            older = NOW - timedelta(minutes=30)

            def observation(metric: str, value: str, unit: str, at: datetime) -> NormalizedRecord:
                return NormalizedRecord(
                    provider="kis",
                    kind=RecordKind.PRICE_VOLUME,
                    record_id=f"paper:UN:005930:{at.isoformat()}:{metric}",
                    instrument_id="005930",
                    market="KRX_NXT_CONSOLIDATED",
                    currency="KRW" if unit == "KRW" else None,
                    effective_at=at,
                    published_at=at,
                    collected_at=at,
                    unit=unit,
                    value=value,
                    source_url=(
                        "https://openapivts.koreainvestment.com:29443/"
                        "uapi/domestic-stock/v1/quotations/inquire-price"
                    ),
                    metadata={
                        "metric": metric,
                        "environment": "paper",
                        "time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                    },
                )

            old_records = (
                observation("price", "70000", "KRW", older),
                observation("volume", "1000", "SHARES", older),
                observation("trading_value", "70000000", "KRW", older),
            )
            newest_price = observation("price", "71000", "KRW", NOW)
            store.save_collection(
                CollectionResult("kis", older, older, CollectionStatus.OK, old_records)
            )
            store.save_collection(
                CollectionResult(
                    "kis",
                    NOW,
                    NOW,
                    CollectionStatus.PARTIAL,
                    (newest_price,),
                    (CollectionIssue("MISSING_FIELD", "fixture"),),
                )
            )
            store.set_setting("provider.targets.kis", ["005930"], updated_at=NOW)
            service = AutomationService(store=store, clock=lambda: NOW)
            service.set_strategy_mix(
                "same-observation-v1", {StrategyKind.QUALITY: Decimal("100")}
            )

            assessment = service.assess_configured_candidates(cutoff_at=NOW)[0]

            self.assertEqual(assessment.metrics.latest_price, Decimal("71000"))
            self.assertIsNone(assessment.metrics.latest_volume)
            self.assertIsNone(assessment.metrics.latest_trading_value)
            self.assertEqual(len(assessment.source_record_ids), 1)
            selected = {
                int(row["id"]): row
                for row in store.latest_records(provider="kis", cutoff_at=NOW)
            }
            self.assertEqual(
                selected[assessment.source_record_ids[0]]["provider_record_id"],
                newest_price.record_id,
            )

    def test_kis_paper_and_live_observations_never_mix_in_assessment_or_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            older = NOW - timedelta(minutes=30)

            def observation(
                environment: str,
                metric: str,
                value: str,
                unit: str,
                at: datetime,
            ) -> NormalizedRecord:
                host = (
                    "openapivts.koreainvestment.com:29443"
                    if environment == "paper"
                    else "openapi.koreainvestment.com:9443"
                )
                return NormalizedRecord(
                    provider="kis",
                    kind=RecordKind.PRICE_VOLUME,
                    record_id=f"{environment}:UN:005930:{at.isoformat()}:{metric}",
                    instrument_id="005930",
                    market="KRX_NXT_CONSOLIDATED",
                    currency="KRW" if unit == "KRW" else None,
                    effective_at=at,
                    published_at=at,
                    collected_at=at,
                    unit=unit,
                    value=value,
                    source_url=(
                        f"https://{host}/uapi/domestic-stock/v1/quotations/inquire-price"
                    ),
                    metadata={
                        "metric": metric,
                        "environment": environment,
                        "time_basis": "FIRST_OBSERVED_AT_COLLECTION",
                    },
                )

            paper_records = (
                observation("paper", "price", "70000", "KRW", older),
                observation("paper", "volume", "1000", "SHARES", older),
                observation("paper", "trading_value", "70000000", "KRW", older),
            )
            live_price = observation("live", "price", "71000", "KRW", NOW)
            store.save_collection(
                CollectionResult("kis", older, older, CollectionStatus.OK, paper_records)
            )
            store.save_collection(
                CollectionResult(
                    "kis",
                    NOW,
                    NOW,
                    CollectionStatus.PARTIAL,
                    (live_price,),
                    (CollectionIssue("MISSING_FIELD", "fixture"),),
                )
            )
            store.set_setting("provider.targets.kis", ["005930"], updated_at=NOW)
            store.set_setting("providers.enabled", ["kis"], updated_at=NOW)
            service = AutomationService(store=store, clock=lambda: NOW)
            service.set_kis_environment("live")
            service.set_strategy_mix(
                "environment-bound-v1", {StrategyKind.QUALITY: Decimal("100")}
            )

            assessment = service.assess_configured_candidates(cutoff_at=NOW)[0]
            report = service.generate_report(now=NOW)

            self.assertEqual(assessment.metrics.latest_price, Decimal("71000"))
            self.assertIsNone(assessment.metrics.latest_volume)
            self.assertIsNone(assessment.metrics.latest_trading_value)
            self.assertEqual(len(assessment.source_record_ids), 1)
            self.assertIn("환경 live", report)
            self.assertIn("71000 KRW", report)
            self.assertNotIn("1000 SHARES", report)
            self.assertNotIn("70000000 KRW", report)
            self.assertNotIn("환경 paper", report)

    def test_previous_day_success_is_stale_and_disabled_provider_stays_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            previous = NOW - timedelta(days=1)
            old_record = NormalizedRecord(
                provider="bls",
                kind=RecordKind.MACRO,
                record_id="old",
                instrument_id="CUUR0000SA0",
                market="US",
                currency=None,
                effective_at=previous,
                published_at=previous,
                collected_at=previous,
                unit="INDEX_1982_84_100",
                value="1",
                source_url="https://api.bls.gov/publicAPI/v1/timeseries/data/",
            )
            store.save_collection(
                CollectionResult("bls", previous, previous, CollectionStatus.OK, (old_record,))
            )
            store.set_setting("providers.enabled", ["bls"], updated_at=NOW)
            service = AutomationService(store=store, clock=lambda: NOW)
            row = next(value for value in service.provider_statuses() if value.code == "bls")
            self.assertEqual(row.state, "STALE")
            service.set_provider_enabled("bls", False)
            row = next(value for value in service.provider_statuses() if value.code == "bls")
            self.assertEqual(row.state, "DISABLED")

    def test_provider_status_does_not_mix_future_run_or_record_across_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            cutoff = datetime(
                2026, 10, 11, 16, 0, tzinfo=timezone(timedelta(hours=9))
            )  # 07:00 UTC
            before_cutoff = datetime(2026, 10, 11, 6, 30, tzinfo=timezone.utc)
            after_cutoff = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
            past_record = replace(
                record(),
                effective_at=before_cutoff - timedelta(days=1),
                published_at=before_cutoff,
                collected_at=before_cutoff,
            )
            future_record = replace(
                record(),
                record_id="future-status-record",
                effective_at=after_cutoff,
                published_at=after_cutoff,
                collected_at=after_cutoff,
                value="999999",
            )
            store.save_collection(
                CollectionResult(
                    "bls",
                    before_cutoff,
                    before_cutoff,
                    CollectionStatus.OK,
                    (past_record,),
                )
            )
            store.save_collection(
                CollectionResult(
                    "bls",
                    after_cutoff,
                    after_cutoff,
                    CollectionStatus.PARTIAL,
                    (future_record,),
                    (CollectionIssue("FUTURE_FAILURE", "future fixture"),),
                )
            )
            store.set_setting("providers.enabled", ["bls"], updated_at=before_cutoff)
            service = AutomationService(store=store, clock=lambda: cutoff)

            status = next(
                row for row in service.provider_statuses(now=cutoff) if row.code == "bls"
            )

            self.assertEqual(status.state, "OK")
            self.assertEqual(status.last_collected, before_cutoff.isoformat())
            self.assertEqual(status.last_attempted, before_cutoff.isoformat())
            self.assertEqual(
                status.last_effective,
                (before_cutoff - timedelta(days=1)).isoformat(),
            )
            self.assertNotIn("999999", status.message)

    def test_structured_provider_and_kis_environment_settings_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            service = AutomationService(store=store, clock=lambda: NOW)
            service.set_kis_environment("live")
            service.set_targets("kis", ("005930",))
            service.set_sec_concepts(("Assets", "Revenues"))
            service.set_ecos_series(
                EcosSeriesRequest("200Y101", "A", "2020", "2023", "10101")
            )
            service.set_opendart_financial_request(
                corp_code="00126380",
                business_year="2026",
                report_code="11014",
                financial_division="CFS",
            )
            self.assertEqual(store.get_setting("provider.kis.environment"), "live")
            self.assertEqual(store.get_setting("provider.sec.concepts"), ["Assets", "Revenues"])
            self.assertEqual(store.get_setting("provider.targets.ecos")[0]["cycle"], "A")
            self.assertEqual(
                store.get_setting("provider.opendart.financial_requests")[0]["financial_division"],
                "CFS",
            )
            self.assertIn("ecos", service.enabled_providers())
            with self.assertRaises(DomainValidationError):
                service.set_targets("fed_news", ("untrusted-target",))
            with self.assertRaises(DomainValidationError):
                service.set_targets("ecos", ("not-structured",))


if __name__ == "__main__":
    unittest.main()
