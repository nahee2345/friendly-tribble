"""Opt-in PostgreSQL integration checks for the private web service.

The normal unittest discovery run skips this module unless
``TEST_DATABASE_URL`` names a PostgreSQL database.  A unique schema is used
for every run so the checks never read or replace application tables.
"""

from __future__ import annotations

import os
import hashlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from asset_assistant.domain import DomainValidationError
from wealthy_server.database import Database
from wealthy_server.models import StrategyTargetVersion
from wealthy_server.repositories import (
    AuthRepository,
    JobRepository,
    MarketRecordInput,
    MarketRepository,
    ReportRepository,
    ScenarioRepository,
    StrategyTargetRepository,
)
from wealthy_server.security import PasswordManager
from wealthy_server.strategy_targets import TargetStrategyKind, TargetStrategyMix


def _valid_weights(*, index: str = "30", value: str = "10") -> dict[str, str]:
    return {
        TargetStrategyKind.LONG_TERM_INDEX.value: index,
        TargetStrategyKind.VALUE.value: value,
        TargetStrategyKind.QUALITY_GROWTH.value: "10",
        TargetStrategyKind.GROWTH.value: "10",
        TargetStrategyKind.HIGH_DIVIDEND.value: "10",
        TargetStrategyKind.DIVIDEND_GROWTH.value: "10",
        TargetStrategyKind.MOMENTUM.value: "10",
        TargetStrategyKind.ASSET_ALLOCATION.value: "10",
    }


def _test_database_url() -> str:
    direct = os.environ.get("TEST_DATABASE_URL", "")
    if direct:
        return direct
    raw_path = os.environ.get("TEST_DATABASE_URL_FILE", "")
    if not raw_path:
        return ""
    try:
        source = open(raw_path, "rb")
        with source:
            payload = source.read(4097)
        value = payload.decode("utf-8").strip()
    except (OSError, UnicodeError, ValueError):
        return ""
    return value if len(payload) <= 4096 and "\x00" not in value else ""


_DATABASE_URL = _test_database_url()
try:
    _IS_POSTGRES = bool(_DATABASE_URL) and make_url(_DATABASE_URL).drivername.startswith(
        "postgresql"
    )
except Exception:
    _IS_POSTGRES = False


@unittest.skipUnless(
    _IS_POSTGRES,
    "TEST_DATABASE_URL PostgreSQL 전용 통합 DB가 설정되지 않아 건너뜁니다.",
)
class PostgreSQLIntegrationTests(unittest.TestCase):
    """Exercise behavior which SQLite cannot faithfully prove."""

    @classmethod
    def setUpClass(cls) -> None:
        # The name is generated here and contains only trusted ASCII, so it is
        # safe to quote as an identifier below.
        cls.schema = f"wealthy_test_{uuid4().hex}"
        cls.admin_engine = create_engine(_DATABASE_URL, future=True, pool_pre_ping=True)
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))

        parsed = make_url(_DATABASE_URL)
        existing_options = parsed.query.get("options", "")
        search_path_option = f"-csearch_path={cls.schema}"
        options = " ".join(value for value in (existing_options, search_path_option) if value)
        isolated_url = parsed.update_query_dict({"options": options}).render_as_string(
            hide_password=False
        )
        cls.database = Database(isolated_url)
        cls.database.create_schema()
        cls.auth = AuthRepository(cls.database.session_factory)
        password_hash = PasswordManager().hash("postgres-integration-fixture-pass")
        cls.user_a = cls.auth.create_user(f"pg-a-{uuid4().hex}", password_hash)
        cls.user_b = cls.auth.create_user(f"pg-b-{uuid4().hex}", password_hash)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "database"):
            cls.database.dispose()
        if hasattr(cls, "admin_engine") and hasattr(cls, "schema"):
            # Drop only the randomly generated schema owned by this test run.
            with cls.admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
            cls.admin_engine.dispose()

    def test_owner_scoped_scenarios_reports_and_targets(self) -> None:
        now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
        scenarios = ScenarioRepository(self.database.session_factory)
        reports = ReportRepository(self.database.session_factory)
        targets = StrategyTargetRepository(self.database.session_factory)

        snapshot = scenarios.save(
            self.user_a.id,
            realm="SAMPLE",
            payload={"title": "owner-a-fixture"},
            effective_at=now,
            created_at=now,
        )
        self.assertEqual(scenarios.latest(self.user_a.id).id, snapshot.id)  # type: ignore[union-attr]
        self.assertIsNone(scenarios.latest(self.user_b.id))

        report = reports.save(
            self.user_a.id,
            scenario_snapshot_id=snapshot.id,
            report_markdown="# isolated fixture",
            status="COMPLETE",
            data_cutoff_at=now,
            generated_at=now,
        )
        self.assertIsNotNone(reports.get(self.user_a.id, report.id))
        self.assertIsNone(reports.get(self.user_b.id, report.id))
        with self.assertRaises(DomainValidationError):
            reports.save(
                self.user_b.id,
                scenario_snapshot_id=snapshot.id,
                report_markdown="# cross-owner fixture",
                status="COMPLETE",
                data_cutoff_at=now,
                generated_at=now,
            )

        targets.save(
            self.user_a.id,
            version_name="owner-a-target-v1",
            mix=TargetStrategyMix.from_strings(_valid_weights()),
            created_at=now,
        )
        self.assertIsNotNone(targets.current(self.user_a.id))
        self.assertIsNone(targets.current(self.user_b.id))

    def test_market_cutoff_preserves_postgresql_microseconds_and_offsets(self) -> None:
        repository = MarketRepository(self.database.session_factory)
        cutoff = datetime(2026, 10, 11, 6, 0, 0, 123456, tzinfo=UTC)
        same_in_kst = cutoff.astimezone(timezone(timedelta(hours=9)))
        run = repository.start_run(provider="fed", started_at=cutoff - timedelta(seconds=1))
        repository.append_records(
            run.id,
            (
                MarketRecordInput(
                    kind="NEWS",
                    provider_record_id="exact-offset-fixture",
                    instrument_id=None,
                    effective_at=same_in_kst,
                    published_at=same_in_kst,
                    collected_at=same_in_kst,
                    source_url="https://www.federalreserve.gov/fixture-exact",
                    value={"title": "exact fixture"},
                ),
                MarketRecordInput(
                    kind="NEWS",
                    provider_record_id="future-one-microsecond-fixture",
                    instrument_id=None,
                    effective_at=cutoff + timedelta(microseconds=1),
                    published_at=cutoff + timedelta(microseconds=1),
                    collected_at=cutoff + timedelta(microseconds=1),
                    source_url="https://www.federalreserve.gov/fixture-future",
                    value={"title": "future fixture"},
                ),
            ),
        )
        repository.finish_run(
            run.id, status="SUCCEEDED", finished_at=cutoff + timedelta(seconds=1)
        )

        latest = repository.latest(
            provider="fed", kind="NEWS", instrument_id=None, cutoff_at=cutoff
        )
        self.assertIsNotNone(latest)
        self.assertEqual(latest.provider_record_id, "exact-offset-fixture")  # type: ignore[union-attr]
        self.assertEqual(latest.effective_at, cutoff)  # type: ignore[union-attr]

    def test_strategy_versions_are_append_only(self) -> None:
        repository = StrategyTargetRepository(self.database.session_factory)
        now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
        first = repository.save(
            self.user_a.id,
            version_name="immutable-target-v1",
            mix=TargetStrategyMix.from_strings(_valid_weights()),
            created_at=now,
        )
        second_weights = _valid_weights(index="20", value="20")
        second = repository.save(
            self.user_a.id,
            version_name="immutable-target-v2",
            mix=TargetStrategyMix.from_strings(second_weights),
            created_at=now + timedelta(seconds=1),
        )
        self.assertNotEqual(first.id, second.id)

        with self.database.session_factory() as session:
            stored_first = session.get(StrategyTargetVersion, first.id)
            stored_second = session.get(StrategyTargetVersion, second.id)
            assert stored_first is not None and stored_second is not None
            self.assertEqual(stored_first.weights, _valid_weights())
            self.assertEqual(stored_second.weights, second_weights)

        with self.assertRaises(DomainValidationError), self.database.session_factory.begin() as session:
            stored = session.get(StrategyTargetVersion, first.id)
            assert stored is not None
            stored.version_name = "mutation-must-fail"
            session.flush()

    def test_concurrent_job_claim_has_one_winner_per_owner_slot(self) -> None:
        jobs = JobRepository(self.database.session_factory)
        scheduled = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
        barrier = threading.Barrier(2)

        def claim(worker: str) -> int | None:
            barrier.wait(timeout=10)
            item = jobs.claim(
                self.user_b.id,
                job_kind="concurrent-fixture",
                scheduled_for=scheduled,
                worker_id=worker,
                now=scheduled,
                lease_for=timedelta(minutes=15),
            )
            return None if item is None else item.id

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(claim, ("pg-worker-a", "pg-worker-b")))
        winners = tuple(value for value in results if value is not None)
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(jobs.list(self.user_b.id)), 1)

    def test_concurrent_rotating_login_names_obey_client_budget(self) -> None:
        now = datetime(2026, 10, 11, 6, 30, tzinfo=UTC)
        client_fingerprint = hashlib.sha256(b"shared-proxy-fixture").hexdigest()
        barrier = threading.Barrier(12)

        def reserve(index: int) -> int | None:
            barrier.wait(timeout=10)
            return self.auth.claim_login_attempt(
                username_fingerprint=hashlib.sha256(
                    f"rotating-name-{index}".encode("ascii")
                ).hexdigest(),
                client_fingerprint=client_fingerprint,
                attempted_at=now,
                since=now - timedelta(minutes=15),
                prune_before=now - timedelta(hours=24),
                max_pair_failures=5,
                max_client_failures=10,
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = tuple(pool.map(reserve, range(12)))
        self.assertEqual(sum(value is not None for value in results), 10)
        self.assertEqual(sum(value is None for value in results), 2)


if __name__ == "__main__":
    unittest.main()
