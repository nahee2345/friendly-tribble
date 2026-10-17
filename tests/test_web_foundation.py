from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from asset_assistant.domain import DomainValidationError
from wealthy_server.database import Database
from wealthy_server.models import ProviderSecret, ReportSnapshot, StrategyTargetVersion
from wealthy_server.repositories import (
    AuthRepository,
    CredentialRepository,
    JobRepository,
    MarketRecordInput,
    MarketRepository,
    ReportRepository,
    ScenarioRepository,
    SessionRepository,
    StrategyTargetRepository,
)
from wealthy_server.security import (
    PasswordManager,
    SecretVault,
    hash_token,
    issue_session_tokens,
    token_matches,
)
from wealthy_server.strategy_targets import (
    MappingStatus,
    TargetStrategyKind,
    TargetStrategyMix,
    strategy_catalog,
)


def valid_target_weights() -> dict[str, str]:
    return {
        TargetStrategyKind.LONG_TERM_INDEX.value: "30",
        TargetStrategyKind.VALUE.value: "10",
        TargetStrategyKind.QUALITY_GROWTH.value: "10",
        TargetStrategyKind.GROWTH.value: "10",
        TargetStrategyKind.HIGH_DIVIDEND.value: "10",
        TargetStrategyKind.DIVIDEND_GROWTH.value: "10",
        TargetStrategyKind.MOMENTUM.value: "10",
        TargetStrategyKind.ASSET_ALLOCATION.value: "10",
    }


class SecurityTests(unittest.TestCase):
    def test_password_hash_is_argon2id_and_never_contains_plaintext(self) -> None:
        password = "local-test-password!"
        manager = PasswordManager()
        encoded = manager.hash(password)
        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertNotIn(password, encoded)
        self.assertTrue(manager.verify(encoded, password))
        self.assertFalse(manager.verify(encoded, "wrong-password"))
        self.assertFalse(manager.verify("malformed", password))

    def test_random_session_tokens_are_only_compared_as_hashes(self) -> None:
        first = issue_session_tokens()
        second = issue_session_tokens()
        self.assertNotEqual(first.session_token, second.session_token)
        self.assertNotEqual(first.csrf_token, second.csrf_token)
        self.assertNotIn(first.session_token, first.session_token_hash)
        self.assertNotIn(first.csrf_token, first.csrf_token_hash)
        self.assertTrue(token_matches(first.session_token, first.session_token_hash))
        self.assertFalse(token_matches(second.session_token, first.session_token_hash))

    def test_aes_gcm_binds_secret_to_owner_and_supports_rotation(self) -> None:
        old_key = bytes(range(32))
        new_key = bytes(reversed(range(32)))
        old_vault = SecretVault({"old": old_key}, active_key_version="old")
        encrypted = old_vault.encrypt(
            "fixture-secret-only",
            owner_id="owner-a",
            provider="kis",
            environment="paper",
            secret_name="app-secret",
        )
        self.assertNotIn(b"fixture-secret-only", encrypted.ciphertext)
        self.assertNotIn("fixture-secret-only", repr(encrypted))
        with self.assertRaises(DomainValidationError):
            old_vault.decrypt_for_provider_adapter(
                encrypted,
                owner_id="owner-b",
                provider="kis",
                environment="paper",
                secret_name="app-secret",
            )

        rotating = SecretVault({"old": old_key, "new": new_key}, active_key_version="new")
        rotated = rotating.rotate(
            encrypted,
            owner_id="owner-a",
            provider="kis",
            environment="paper",
            secret_name="app-secret",
        )
        self.assertEqual(rotated.key_version, "new")
        self.assertEqual(
            rotating.decrypt_for_provider_adapter(
                rotated,
                owner_id="owner-a",
                provider="kis",
                environment="paper",
                secret_name="app-secret",
            ),
            "fixture-secret-only",
        )


class StrategyTargetTests(unittest.TestCase):
    def test_exact_eight_way_mix_and_optional_amounts(self) -> None:
        mix = TargetStrategyMix.from_strings(valid_target_weights())
        with self.assertRaises(TypeError):
            mix.weights[TargetStrategyKind.VALUE] = Decimal("20")  # type: ignore[index]
        self.assertIsNone(mix.target_amounts(None))
        amounts = mix.target_amounts("4000000")
        assert amounts is not None
        self.assertEqual(amounts[TargetStrategyKind.LONG_TERM_INDEX], Decimal("1200000"))
        self.assertEqual(sum(amounts.values(), Decimal(0)), Decimal("4000000"))
        self.assertEqual(len(mix.as_strings()), 8)

    def test_float_missing_and_non_exact_total_are_rejected(self) -> None:
        weights = valid_target_weights()
        weights[TargetStrategyKind.LONG_TERM_INDEX.value] = 30.0  # type: ignore[assignment]
        with self.assertRaises(DomainValidationError):
            TargetStrategyMix.from_strings(weights)

        missing = valid_target_weights()
        missing.pop(TargetStrategyKind.GROWTH.value)
        with self.assertRaises(DomainValidationError):
            TargetStrategyMix.from_strings(missing)

        below = valid_target_weights()
        below[TargetStrategyKind.LONG_TERM_INDEX.value] = "29.999999999999999999"
        with self.assertRaises(DomainValidationError):
            TargetStrategyMix.from_strings(below)
        decimal_values = {kind: Decimal("12.5") for kind in TargetStrategyKind}
        with self.assertRaises(DomainValidationError):
            TargetStrategyMix(decimal_values)  # type: ignore[arg-type]

    def test_catalog_is_honest_about_partial_and_unavailable_mappings(self) -> None:
        entries = strategy_catalog()
        self.assertEqual({entry.kind for entry in entries}, set(TargetStrategyKind))
        by_kind = {entry.kind: entry for entry in entries}
        self.assertEqual(by_kind[TargetStrategyKind.GROWTH].mapping_status, MappingStatus.UNAVAILABLE)
        self.assertEqual(
            by_kind[TargetStrategyKind.QUALITY_GROWTH].mapping_status, MappingStatus.PARTIAL
        )


class RepositoryIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Database("sqlite+pysqlite:///:memory:")
        self.database.create_schema()
        self.auth = AuthRepository(self.database.session_factory)
        password_hash = PasswordManager().hash("repository-test-pass")
        self.user_a = self.auth.create_user("admin-a", password_hash)
        self.user_b = self.auth.create_user("admin-b", password_hash)

    def tearDown(self) -> None:
        self.database.dispose()

    def test_scenario_report_and_strategy_reads_are_owner_scoped(self) -> None:
        now = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)
        scenarios = ScenarioRepository(self.database.session_factory)
        scenario_a = scenarios.save(
            self.user_a.id,
            realm="SAMPLE",
            payload={"title": "fixture-a"},
            effective_at=now,
            created_at=now,
        )
        scenarios.save(
            self.user_b.id,
            realm="SAMPLE",
            payload={"title": "fixture-b"},
            effective_at=now,
            created_at=now + timedelta(seconds=1),
        )
        self.assertEqual(scenarios.latest(self.user_a.id).id, scenario_a.id)  # type: ignore[union-attr]
        self.assertEqual(len(scenarios.list(self.user_b.id)), 1)

        reports = ReportRepository(self.database.session_factory)
        report_a = reports.save(
            self.user_a.id,
            report_markdown="# fixture report A\n\n1. first\n2. second\n",
            status="COMPLETE",
            data_cutoff_at=now,
            generated_at=now,
            scenario_snapshot_id=scenario_a.id,
        )
        self.assertEqual(report_a.report_markdown, "# fixture report A\n\n1. first\n2. second\n")
        self.assertIsNone(reports.get(self.user_b.id, report_a.id))
        with self.assertRaises(DomainValidationError):
            reports.save(
                self.user_b.id,
                report_markdown="# cross-owner",
                status="COMPLETE",
                data_cutoff_at=now,
                scenario_snapshot_id=scenario_a.id,
            )

        targets = StrategyTargetRepository(self.database.session_factory)
        targets.save(
            self.user_a.id,
            version_name="target-v1",
            mix=TargetStrategyMix.from_strings(valid_target_weights()),
            created_at=now,
        )
        self.assertIsNotNone(targets.current(self.user_a.id))
        self.assertIsNone(targets.current(self.user_b.id))

    def test_report_and_strategy_rows_are_append_only(self) -> None:
        now = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)
        targets = StrategyTargetRepository(self.database.session_factory)
        target = targets.save(
            self.user_a.id,
            version_name="immutable-v1",
            mix=TargetStrategyMix.from_strings(valid_target_weights()),
            created_at=now,
        )
        with self.assertRaises(DomainValidationError), self.database.session_factory.begin() as session:
            stored = session.get(StrategyTargetVersion, target.id)
            assert stored is not None
            stored.version_name = "changed"
            session.flush()

        report = ReportRepository(self.database.session_factory).save(
            self.user_a.id,
            report_markdown="# immutable",
            status="COMPLETE",
            data_cutoff_at=now,
            generated_at=now,
        )
        with self.assertRaises(DomainValidationError), self.database.session_factory.begin() as session:
            stored_report = session.get(ReportSnapshot, report.id)
            assert stored_report is not None
            session.delete(stored_report)
            session.flush()

    def test_session_and_encrypted_credentials_do_not_cross_owners(self) -> None:
        now = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)
        sessions = SessionRepository(self.database.session_factory)
        issued = issue_session_tokens()
        sessions.create(
            self.user_a.id,
            token_hash=issued.session_token_hash,
            csrf_hash=issued.csrf_token_hash,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        self.assertIsNotNone(sessions.get(self.user_a.id, issued.session_token_hash, now=now))
        self.assertIsNone(sessions.get(self.user_b.id, issued.session_token_hash, now=now))
        self.assertEqual(sessions.authenticate(issued.session_token_hash, now=now).owner_id, self.user_a.id)  # type: ignore[union-attr]

        vault = SecretVault({"v1": bytes(range(32))}, active_key_version="v1")
        credentials = CredentialRepository(self.database.session_factory, vault)
        credentials.upsert(
            self.user_a.id,
            provider="kis",
            environment="paper",
            secret_name="app-secret",
            plaintext="owner-a-fixture-secret",
            now=now,
        )
        self.assertTrue(
            credentials.status(
                self.user_a.id,
                provider="kis",
                environment="paper",
                secret_name="app-secret",
            ).configured
        )
        self.assertFalse(
            credentials.status(
                self.user_b.id,
                provider="kis",
                environment="paper",
                secret_name="app-secret",
            ).configured
        )
        with self.assertRaises(DomainValidationError):
            credentials.decrypt_for_provider_adapter(
                self.user_b.id,
                provider="kis",
                environment="paper",
                secret_name="app-secret",
            )
        with self.database.session_factory() as session:
            stored = session.scalar(select(ProviderSecret).where(ProviderSecret.owner_id == self.user_a.id))
            assert stored is not None
            self.assertNotIn(b"owner-a-fixture-secret", stored.ciphertext)

        rotating_vault = SecretVault(
            {"v1": bytes(range(32)), "v2": bytes(reversed(range(32)))},
            active_key_version="v2",
        )
        rotating_credentials = CredentialRepository(
            self.database.session_factory, rotating_vault
        )
        status = rotating_credentials.rotate(
            self.user_a.id,
            provider="kis",
            environment="paper",
            secret_name="app-secret",
            now=now + timedelta(seconds=1),
        )
        self.assertEqual(status.key_version, "v2")
        self.assertEqual(
            rotating_credentials.decrypt_for_provider_adapter(
                self.user_a.id,
                provider="kis",
                environment="paper",
                secret_name="app-secret",
            ),
            "owner-a-fixture-secret",
        )

    def test_job_claim_is_owner_scoped_and_slot_is_idempotent(self) -> None:
        jobs = JobRepository(self.database.session_factory)
        scheduled = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)
        first = jobs.claim(
            self.user_a.id,
            job_kind="collect",
            scheduled_for=scheduled,
            worker_id="worker-a",
            now=scheduled,
            lease_for=timedelta(minutes=15),
        )
        self.assertIsNotNone(first)
        duplicate = jobs.claim(
            self.user_a.id,
            job_kind="collect",
            scheduled_for=scheduled,
            worker_id="worker-b",
            now=scheduled + timedelta(minutes=1),
            lease_for=timedelta(minutes=15),
        )
        self.assertIsNone(duplicate)
        other_owner = jobs.claim(
            self.user_b.id,
            job_kind="collect",
            scheduled_for=scheduled,
            worker_id="worker-b",
            now=scheduled,
            lease_for=timedelta(minutes=15),
        )
        self.assertIsNotNone(other_owner)

    def test_market_record_cutoff_is_microsecond_exact(self) -> None:
        market = MarketRepository(self.database.session_factory)
        cutoff = datetime(2026, 10, 11, 6, 0, 0, 123456, tzinfo=UTC)
        run = market.start_run(provider="fed", started_at=cutoff - timedelta(seconds=1))
        records = market.append_records(
            run.id,
            (
                MarketRecordInput(
                    kind="NEWS",
                    provider_record_id="at-cutoff",
                    instrument_id=None,
                    effective_at=cutoff,
                    published_at=cutoff,
                    collected_at=cutoff,
                    source_url="https://www.federalreserve.gov/fixture-at-cutoff",
                    value={"title": "fixture"},
                ),
                MarketRecordInput(
                    kind="NEWS",
                    provider_record_id="future-microsecond",
                    instrument_id=None,
                    effective_at=cutoff + timedelta(microseconds=1),
                    published_at=cutoff + timedelta(microseconds=1),
                    collected_at=cutoff + timedelta(microseconds=1),
                    source_url="https://www.federalreserve.gov/fixture-future",
                    value={"title": "future fixture"},
                ),
            ),
        )
        market.finish_run(run.id, status="SUCCEEDED", finished_at=cutoff + timedelta(seconds=1))
        latest = market.latest(
            provider="fed", kind="NEWS", instrument_id=None, cutoff_at=cutoff
        )
        self.assertIsNotNone(latest)
        self.assertEqual(latest.provider_record_id, "at-cutoff")  # type: ignore[union-attr]
        self.assertEqual(len(records), 2)


if __name__ == "__main__":
    unittest.main()
