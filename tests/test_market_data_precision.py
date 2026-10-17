from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from asset_assistant.domain import DomainValidationError
from asset_assistant.market_data import (
    CollectionResult,
    CollectionStatus,
    MarketDataStore,
    NormalizedRecord,
    RecordKind,
)


CUTOFF = datetime(2026, 10, 12, 6, 0, 0, 500_000, tzinfo=timezone.utc)


def market_record(record_id: str, observed_at: datetime) -> NormalizedRecord:
    return NormalizedRecord(
        provider="fixture",
        kind=RecordKind.PRICE_VOLUME,
        record_id=record_id,
        instrument_id="005930",
        market="KRX",
        currency="KRW",
        effective_at=observed_at,
        published_at=observed_at,
        collected_at=observed_at,
        unit="KRW",
        value="70000",
        source_url="https://example.invalid/official-fixture",
    )


class MarketDataMicrosecondPrecisionTests(unittest.TestCase):
    def test_latest_records_includes_exact_cutoff_and_excludes_later_microseconds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            same_instant_other_offset = CUTOFF.astimezone(
                timezone(timedelta(hours=9))
            )
            records = (
                market_record("exact-other-offset", same_instant_other_offset),
                market_record("future-1us", CUTOFF + timedelta(microseconds=1)),
                market_record("future-100us", CUTOFF + timedelta(microseconds=100)),
            )
            store.save_collection(
                CollectionResult(
                    "fixture",
                    CUTOFF,
                    CUTOFF + timedelta(microseconds=100),
                    CollectionStatus.OK,
                    records,
                )
            )

            selected = store.latest_records(
                provider="fixture",
                cutoff_at=CUTOFF,
                require_known_publication_time=True,
            )

            self.assertEqual(
                [row["provider_record_id"] for row in selected],
                ["exact-other-offset"],
            )

    def test_source_validation_uses_the_same_microsecond_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            records = (
                market_record("exact", CUTOFF),
                market_record("future-1us", CUTOFF + timedelta(microseconds=1)),
                market_record("future-100us", CUTOFF + timedelta(microseconds=100)),
            )
            run_id = store.save_collection(
                CollectionResult(
                    "fixture",
                    CUTOFF,
                    CUTOFF + timedelta(microseconds=100),
                    CollectionStatus.OK,
                    records,
                )
            )
            ids = {
                row["provider_record_id"]: int(row["id"])
                for row in store.latest_records(provider="fixture")
            }
            store.save_strategy_version(
                version_name="precision-v1",
                config={"weights": {"BROAD_MARKET": "100"}},
                created_at=CUTOFF,
            )

            assessment_id = store.save_candidate_assessment(
                strategy_version="precision-v1",
                instrument_id="005930",
                cutoff_at=CUTOFF,
                status="UNAVAILABLE",
                score=None,
                reasons=("fixture",),
                source_record_ids=(ids["exact"],),
                created_at=CUTOFF,
            )
            self.assertGreater(assessment_id, 0)
            self.assertEqual(
                store.run_ids_for_record_ids((ids["exact"],), cutoff_at=CUTOFF),
                (run_id,),
            )

            for record_id in ("future-1us", "future-100us"):
                with self.subTest(record_id=record_id):
                    with self.assertRaises(DomainValidationError):
                        store.save_candidate_assessment(
                            strategy_version="precision-v1",
                            instrument_id="005930",
                            cutoff_at=CUTOFF,
                            status="UNAVAILABLE",
                            score=None,
                            reasons=("future fixture",),
                            source_record_ids=(ids[record_id],),
                            created_at=CUTOFF,
                        )
                    with self.assertRaises(DomainValidationError):
                        store.run_ids_for_record_ids(
                            (ids[record_id],), cutoff_at=CUTOFF
                        )

    def test_run_selection_and_report_provenance_are_microsecond_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            exact_run = store.save_collection(
                CollectionResult(
                    "fixture", CUTOFF, CUTOFF, CollectionStatus.OK, ()
                )
            )
            future = CUTOFF + timedelta(microseconds=1)
            future_run = store.save_collection(
                CollectionResult(
                    "fixture", future, future, CollectionStatus.OK, ()
                )
            )

            summaries = store.provider_summaries(
                cutoff_at=CUTOFF.astimezone(timezone(timedelta(hours=9)))
            )
            self.assertEqual([int(row["id"]) for row in summaries], [exact_run])
            self.assertGreater(
                store.save_report(
                    report_date="2026-10-12",
                    created_at=CUTOFF,
                    status="OK",
                    markdown="# fixture",
                    source_run_ids=(exact_run,),
                ),
                0,
            )
            with self.assertRaises(DomainValidationError):
                store.save_report(
                    report_date="2026-10-12",
                    created_at=CUTOFF,
                    status="PARTIAL",
                    markdown="# future fixture",
                    source_run_ids=(future_run,),
                )

    def test_v1_database_is_migrated_without_deleting_existing_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE schema_meta (version INTEGER NOT NULL);
                    INSERT INTO schema_meta(version) VALUES (1);
                    CREATE TABLE collection_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('OK','PARTIAL','ERROR')),
                        issue_codes_json TEXT NOT NULL,
                        record_count INTEGER NOT NULL
                    );
                    CREATE TABLE market_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id INTEGER NOT NULL REFERENCES collection_runs(id),
                        provider TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        provider_record_id TEXT NOT NULL,
                        instrument_id TEXT NOT NULL,
                        market TEXT NOT NULL,
                        currency TEXT,
                        effective_at TEXT NOT NULL,
                        published_at TEXT,
                        collected_at TEXT NOT NULL,
                        unit TEXT NOT NULL,
                        value_text TEXT,
                        revised INTEGER NOT NULL CHECK(revised IN (0,1)),
                        source_url TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        UNIQUE(provider, kind, provider_record_id, content_sha256)
                    );
                    """
                )
                equivalent_offset = CUTOFF.astimezone(
                    timezone(timedelta(hours=9))
                ).isoformat()
                connection.execute(
                    """INSERT INTO collection_runs
                       (provider, started_at, finished_at, status,
                        issue_codes_json, record_count)
                       VALUES ('fixture', ?, ?, 'OK', '[]', 1)""",
                    (equivalent_offset, equivalent_offset),
                )
                connection.execute(
                    """INSERT INTO market_records
                       (run_id, provider, kind, provider_record_id, instrument_id,
                        market, currency, effective_at, published_at, collected_at,
                        unit, value_text, revised, source_url, metadata_json,
                        content_sha256)
                       VALUES (1, 'fixture', 'PRICE_VOLUME', 'legacy', '005930',
                               'KRX', 'KRW', ?, ?, ?, 'KRW', '70000', 0,
                               'https://example.invalid/official-fixture', '{}', ?)""",
                    (equivalent_offset, equivalent_offset, equivalent_offset, "0" * 64),
                )
                connection.commit()
            finally:
                connection.close()

            selected = MarketDataStore(path).latest_records(
                provider="fixture",
                cutoff_at=CUTOFF,
                require_known_publication_time=True,
            )
            self.assertEqual(
                [row["provider_record_id"] for row in selected], ["legacy"]
            )

            migrated = sqlite3.connect(path)
            try:
                self.assertEqual(
                    migrated.execute("SELECT version FROM schema_meta").fetchone()[0],
                    3,
                )
                self.assertIsNotNone(
                    migrated.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='market_information'"
                    ).fetchone()
                )
                row = migrated.execute(
                    """SELECT effective_at_us, published_at_us, collected_at_us
                       FROM market_records WHERE provider_record_id='legacy'"""
                ).fetchone()
                self.assertTrue(all(value is not None for value in row))
                self.assertEqual(
                    migrated.execute("SELECT COUNT(*) FROM market_records").fetchone()[0],
                    1,
                )
            finally:
                migrated.close()


if __name__ == "__main__":
    unittest.main()
