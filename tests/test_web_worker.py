from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from asset_assistant.market_data import (
    CollectionResult,
    CollectionStatus,
    NormalizedRecord,
    RecordKind,
)
from wealthy_server.collection import persist_collection_result
from wealthy_server.database import Database
from wealthy_server.repositories import MarketRepository
from wealthy_server.worker import due_slots


class WebWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Database("sqlite+pysqlite:///:memory:")
        self.database.create_schema()

    def tearDown(self) -> None:
        self.database.dispose()

    def test_seoul_slots_and_restart_window_are_explicit(self) -> None:
        before = datetime(2026, 10, 11, 5, 59, 59, tzinfo=UTC)  # 14:59:59 KST
        prior_collect = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
        prior_report = datetime(2026, 10, 10, 7, 0, tzinfo=UTC)
        self.assertEqual(
            due_slots(before),
            (("collect", prior_collect), ("report", prior_report)),
        )
        at_collect = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
        self.assertEqual(
            due_slots(at_collect),
            (("report", prior_report), ("collect", at_collect)),
        )
        at_report = datetime(2026, 10, 11, 7, 0, tzinfo=UTC)
        self.assertEqual(
            due_slots(at_report),
            (("collect", at_collect), ("report", at_report)),
        )

    def test_fixture_collection_is_normalized_idempotent_and_no_future_cutoff(self) -> None:
        repository = MarketRepository(self.database.session_factory)
        cutoff = datetime(2026, 10, 11, 6, 0, 0, 500000, tzinfo=UTC)
        record = NormalizedRecord(
            provider="bls",
            kind=RecordKind.MACRO,
            record_id="fixture-series:2026-09-01",
            instrument_id="FIXTURE_SERIES",
            market="US",
            currency=None,
            effective_at=cutoff - timedelta(days=40),
            published_at=cutoff,
            collected_at=cutoff,
            unit="FIXTURE_UNIT",
            value="123.45",
            source_url="https://api.bls.gov/fixture",
            metadata={"fixture": True},
        )
        result = CollectionResult(
            "bls",
            cutoff - timedelta(seconds=1),
            cutoff,
            CollectionStatus.OK,
            (record,),
        )
        persist_collection_result(repository, result, initiated_by=None)
        persist_collection_result(repository, result, initiated_by=None)
        exact = repository.latest(
            provider="bls",
            kind="MACRO",
            instrument_id="FIXTURE_SERIES",
            cutoff_at=cutoff,
        )
        self.assertIsNotNone(exact)
        self.assertEqual(exact.value, "123.45")
        self.assertIsNone(
            repository.latest(
                provider="bls",
                kind="MACRO",
                instrument_id="FIXTURE_SERIES",
                cutoff_at=cutoff - timedelta(microseconds=1),
            )
        )


if __name__ == "__main__":
    unittest.main()
