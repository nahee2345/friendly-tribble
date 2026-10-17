from __future__ import annotations

import os
import contextlib
import io
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from asset_assistant.market_data import MarketDataStore
from asset_assistant.market_information import (
    MarketInformationItem,
    MarketInformationRepository,
    PublishedPrecision,
)
from asset_assistant.paper import PaperBudgetVersion, PaperStrategyKind
from asset_assistant.paper_persistence import (
    PaperCommandEnvelope,
    SQLitePaperRepository,
)
from tools.web_local_backup import (
    BACKUP_KEY_ITEM,
    LocalBackupError,
    backup_local_databases_v2,
    backup_quiesce,
    backup_local_databases,
    coordinated_backup_writer,
    load_or_create_backup_key,
    restore_local_databases,
    restore_local_databases_v2,
    validate_local_backup_v2,
    main,
)
from wealthy_server.database import Database
from wealthy_server.local_credentials import LOCAL_SECRET_SERVICE
from wealthy_server.repositories import AuthRepository


TEMP_ROOT = Path("/dev/shm") if Path("/dev/shm").is_dir() else None


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class PlaintextFileKeyring(FakeKeyring):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def set_password(self, service: str, username: str, password: str) -> None:
        self.path.write_text(password, encoding="utf-8")
        super().set_password(service, username, password)


class NullKeyring(FakeKeyring):
    pass


class FailingKeyring(FakeKeyring):
    def __init__(self) -> None:
        super().__init__()
        self.accessed = False

    def get_password(self, service: str, username: str) -> str | None:
        self.accessed = True
        raise RuntimeError("private backend detail")


class LocalSQLiteBackupTests(unittest.TestCase):
    def _fixture_databases(self, root: Path) -> tuple[Path, Path]:
        market = root / "market.sqlite3"
        store = MarketDataStore(market)
        store.get_setting("fixture.missing")
        store.set_setting(
            "fixture.value",
            {"mode": "SAMPLE", "value": "12.30"},
            updated_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        )
        MarketInformationRepository(store).save_items(
            (
                MarketInformationItem(
                    canonical_key="federal_reserve:item:fixture-release",
                    provider_id="federal_reserve",
                    provider_name="Federal Reserve",
                    source_name="Official RSS fixture",
                    title_original="Fixture official release metadata",
                    language="en",
                    category="MARKET",
                    related_symbols=(),
                    original_url="https://www.federalreserve.gov/example.htm",
                    published_at=datetime(2026, 10, 11, 11, 0, tzinfo=UTC),
                    collected_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
                    provider_item_id="fixture-release",
                    source_document_id=None,
                    published_precision=PublishedPrecision.DATETIME,
                ),
            ),
            now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        )

        web = root / "web.sqlite3"
        database = Database.for_local_file(web)
        database.create_local_schema()
        database.dispose()
        return market, web

    def _fixture_v2_databases(
        self,
        root: Path,
        *,
        paper_owner_override: str | None = None,
    ) -> tuple[Path, Path, Path, str, object]:
        market, web = self._fixture_databases(root)
        database = Database.for_local_file(web)
        database.create_local_schema()
        user = AuthRepository(database.session_factory).create_user(
            "backup-fixture-owner",
            "fixture-password-hash-never-used-for-login",
        )
        owner_id = str(user.id)
        database.dispose()

        paper_owner = paper_owner_override or owner_id
        paper = root / "paper.sqlite3"

        def owner_guard(candidate: str | None = None) -> None:
            if candidate is not None and candidate != paper_owner:
                raise RuntimeError("fixture owner mismatch")

        with SQLitePaperRepository(
            paper,
            owner_id=paper_owner,
            portfolio_id="paper-fixture-portfolio",
            owner_guard=owner_guard,
            forbidden_paths=(market, web),
            clock=lambda: datetime(2026, 10, 16, 6, 0, tzinfo=UTC),
        ) as repository:
            result = repository.execute(
                PaperCommandEnvelope(
                    owner_id=paper_owner,
                    portfolio_id="paper-fixture-portfolio",
                    idempotency_key="backup-fixture-command-0001",
                    command_type="CREATE_PORTFOLIO",
                    request_payload={"fixture": True},
                    expected_revision=0,
                ),
                lambda engine: engine.create_portfolio(
                    portfolio_id="paper-fixture-portfolio",
                    owner_ref=paper_owner,
                    created_at=datetime(2026, 10, 16, 6, 0, tzinfo=UTC),
                    genesis_entry_id="paper-fixture-genesis",
                ),
            )
            self.assertFalse(result.historical_replay)
            snapshot = repository.snapshot()
        return market, web, paper, owner_id, snapshot

    def test_key_uses_dedicated_os_store_item_and_is_stable(self) -> None:
        backend = FakeKeyring()
        first = load_or_create_backup_key(backend, trusted_test_backend=True)
        second = load_or_create_backup_key(backend, trusted_test_backend=True)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)
        self.assertIn((LOCAL_SECRET_SERVICE, BACKUP_KEY_ITEM), backend.values)

    def test_unapproved_backup_key_backends_are_rejected_before_access(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as raw:
            plaintext = Path(raw) / "must-not-be-created.json"
            backends = (
                PlaintextFileKeyring(plaintext),
                NullKeyring(),
                FailingKeyring(),
            )
            for backend in backends:
                with self.subTest(backend=type(backend).__name__):
                    with self.assertRaises(LocalBackupError) as caught:
                        load_or_create_backup_key(backend)
                    self.assertNotIn("private backend detail", str(caught.exception))
                    self.assertEqual(backend.values, {})
                    self.assertFalse(getattr(backend, "accessed", False))
                    self.assertFalse(plaintext.exists())

    def test_cli_parser_does_not_echo_unknown_sensitive_argument(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
            main(["backup", "--acct", "110-123-456789"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertNotIn("110-123-456789", error.getvalue())
        self.assertNotIn("456789", error.getvalue())

    def test_encrypted_online_backup_and_new_directory_restore(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as raw:
            root = Path(raw)
            market, web = self._fixture_databases(root)
            backup = root / "local.wbackup"
            key = os.urandom(32)

            created = backup_local_databases(market, web, backup, key)

            self.assertEqual(created["status"], "OK")
            self.assertFalse(created["plaintext_backup_written"])
            raw_backup = backup.read_bytes()
            self.assertNotIn(b"fixture.value", raw_backup)
            if os.name != "nt":
                self.assertEqual(backup.stat().st_mode & 0o077, 0)

            destination = root / "restored"
            restored = restore_local_databases(backup, destination, key)
            self.assertEqual(restored["status"], "OK")
            self.assertTrue(restored["backup_authenticated"])
            self.assertFalse(restored["operating_databases_replaced"])
            restored_market = destination / "market-data.sqlite3"
            restored_web = destination / "web-state.sqlite3"
            self.assertTrue(restored_market.is_file())
            self.assertTrue(restored_web.is_file())
            connection = sqlite3.connect(restored_market)
            try:
                value = connection.execute(
                    "SELECT value_json FROM app_settings WHERE key='fixture.value'"
                ).fetchone()
                self.assertIsNotNone(value)
                self.assertIn("SAMPLE", value[0])
                market_info = connection.execute(
                    "SELECT title_original, collected_at_us FROM market_information"
                ).fetchone()
                self.assertEqual(market_info[0], "Fixture official release metadata")
                self.assertIsInstance(market_info[1], int)
            finally:
                connection.close()
            if os.name != "nt":
                self.assertEqual(restored_market.stat().st_mode & 0o077, 0)
                self.assertEqual(restored_web.stat().st_mode & 0o077, 0)

    def test_tamper_wrong_key_nonempty_target_and_overwrite_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as raw:
            root = Path(raw)
            market, web = self._fixture_databases(root)
            backup = root / "local.wbackup"
            key = os.urandom(32)
            backup_local_databases(market, web, backup, key)

            with self.assertRaises(LocalBackupError):
                backup_local_databases(market, web, backup, key)
            with self.assertRaises(LocalBackupError):
                restore_local_databases(backup, root / "wrong-key", os.urandom(32))
            self.assertFalse((root / "wrong-key").exists())

            changed = bytearray(backup.read_bytes())
            changed[len(changed) // 2] ^= 1
            tampered = root / "tampered.wbackup"
            tampered.write_bytes(changed)
            with self.assertRaises(LocalBackupError):
                restore_local_databases(tampered, root / "tampered-target", key)
            self.assertFalse((root / "tampered-target").exists())

            occupied = root / "occupied"
            occupied.mkdir()
            (occupied / "keep.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaises(LocalBackupError):
                restore_local_databases(backup, occupied, key)
            self.assertEqual(
                (occupied / "keep.txt").read_text(encoding="utf-8"), "preserve"
            )

    def test_v2_three_database_backup_restore_preserves_paper_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as raw:
            root = Path(raw)
            market, web, paper, owner_id, expected_state = self._fixture_v2_databases(
                root
            )
            backup = root / "local-v2.wbackup"
            lock = root / "backup-coordination.lock"
            key = os.urandom(32)

            with backup_quiesce(lock, trusted_test_barrier=True) as proof:
                created = backup_local_databases_v2(
                    market,
                    web,
                    paper,
                    backup,
                    key,
                    quiesce=proof,
                )

            self.assertEqual(created["status"], "OK")
            self.assertEqual(
                created["format"], "WEALTHY-LOCAL-SQLITE-AES256-GCM-V2"
            )
            self.assertEqual(
                created["entries"],
                [
                    "market-data.sqlite3",
                    "web-state.sqlite3",
                    "paper-state.sqlite3",
                ],
            )
            self.assertEqual(
                created["consistency"], "FIXTURE_QUIESCED_NOT_OPERATIONAL"
            )
            self.assertFalse(created["operational_recovery_point"])
            checked = validate_local_backup_v2(backup, key)
            self.assertEqual(
                checked["recovery_point_id"], created["recovery_point_id"]
            )
            self.assertFalse(checked["operational_recovery_point"])
            self.assertNotIn(b"paper-fixture-owner", backup.read_bytes())

            destination = root / "restored-v2"
            restored = restore_local_databases_v2(backup, destination, key)
            self.assertTrue(restored["backup_authenticated"])
            self.assertTrue(restored["owner_binding_verified"])
            self.assertFalse(restored["operating_databases_replaced"])
            self.assertFalse(restored["operational_recovery_point"])
            self.assertEqual(restored["files"], created["entries"])
            self.assertEqual(
                restored["recovery_point_id"], created["recovery_point_id"]
            )
            restored_paper = destination / "paper-state.sqlite3"
            connection = sqlite3.connect(restored_paper)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM paper_state_fields"
                    ).fetchone()[0],
                    21,
                )
            finally:
                connection.close()

            def owner_guard(candidate: str | None = None) -> None:
                if candidate is not None and candidate != owner_id:
                    raise RuntimeError("fixture owner mismatch")

            with SQLitePaperRepository(
                restored_paper,
                owner_id=owner_id,
                portfolio_id="paper-fixture-portfolio",
                owner_guard=owner_guard,
                forbidden_paths=(
                    destination / "market-data.sqlite3",
                    destination / "web-state.sqlite3",
                ),
            ) as repository:
                self.assertEqual(repository.snapshot(), expected_state)

            # A byte-valid restore is not sufficient: the next domain
            # decision must be the same as an uninterrupted source.  Apply
            # one identical, deterministic mutation to both isolated copies.
            allocations = tuple(
                (
                    strategy,
                    Decimal("4000000")
                    if strategy is PaperStrategyKind.LONG_TERM_INDEX
                    else Decimal("0"),
                )
                for strategy in PaperStrategyKind
            )
            budget = PaperBudgetVersion(
                "backup-next-budget",
                "policy-fixture",
                allocations,
                datetime(2026, 10, 16, 6, 1, tzinfo=UTC),
            )

            def apply_next(path: Path, forbidden: tuple[Path, ...]):
                with SQLitePaperRepository(
                    path,
                    owner_id=owner_id,
                    portfolio_id="paper-fixture-portfolio",
                    owner_guard=owner_guard,
                    forbidden_paths=forbidden,
                    clock=lambda: datetime(2026, 10, 16, 6, 2, tzinfo=UTC),
                ) as repository:
                    result = repository.execute(
                        PaperCommandEnvelope(
                            owner_id=owner_id,
                            portfolio_id="paper-fixture-portfolio",
                            idempotency_key="backup-fixture-command-0002",
                            command_type="SAVE_BUDGET",
                            request_payload={"version": "backup-next-budget"},
                            expected_revision=1,
                        ),
                        lambda engine: engine.save_budget_version(budget),
                    )
                    return result, repository.snapshot()

            source_result, source_state = apply_next(paper, (market, web))
            restored_result, restored_state = apply_next(
                restored_paper,
                (
                    destination / "market-data.sqlite3",
                    destination / "web-state.sqlite3",
                ),
            )
            self.assertEqual(source_result.value, restored_result.value)
            self.assertEqual(source_result.result_revision, restored_result.result_revision)
            self.assertEqual(source_state, restored_state)
            if os.name != "nt":
                self.assertEqual(backup.stat().st_mode & 0o077, 0)
                for name in created["entries"]:
                    self.assertEqual((destination / name).stat().st_mode & 0o077, 0)

    def test_v2_requires_active_exclusive_quiesce_capability(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as raw:
            root = Path(raw)
            market, web, paper, _, _ = self._fixture_v2_databases(root)
            key = os.urandom(32)
            lock = root / "backup-coordination.lock"

            with self.assertRaises(LocalBackupError):
                with backup_quiesce(lock):
                    self.fail("operational V2 must remain unavailable")
            with self.assertRaises(LocalBackupError):
                backup_local_databases_v2(
                    market,
                    web,
                    paper,
                    root / "missing-proof.wbackup",
                    key,
                    quiesce=None,
                )
            with backup_quiesce(lock, trusted_test_barrier=True) as expired:
                pass
            with self.assertRaises(LocalBackupError):
                backup_local_databases_v2(
                    market,
                    web,
                    paper,
                    root / "expired-proof.wbackup",
                    key,
                    quiesce=expired,
                )
            with coordinated_backup_writer(lock):
                with self.assertRaises(LocalBackupError):
                    with backup_quiesce(lock, trusted_test_barrier=True):
                        self.fail("exclusive lock must not be granted")
            self.assertFalse((root / "missing-proof.wbackup").exists())
            self.assertFalse((root / "expired-proof.wbackup").exists())

    def test_v2_owner_schema_tamper_and_authenticated_bundle_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as raw:
            root = Path(raw)
            key = os.urandom(32)

            mismatch_root = root / "owner-mismatch"
            mismatch_root.mkdir()
            market, web, paper, _, _ = self._fixture_v2_databases(
                mismatch_root,
                paper_owner_override="independent-fixture-owner",
            )
            with backup_quiesce(
                mismatch_root / "coordination.lock",
                trusted_test_barrier=True,
            ) as proof:
                with self.assertRaises(LocalBackupError) as caught:
                    backup_local_databases_v2(
                        market,
                        web,
                        paper,
                        mismatch_root / "must-not-exist.wbackup",
                        key,
                        quiesce=proof,
                    )
            self.assertNotIn("independent-fixture-owner", str(caught.exception))
            self.assertFalse((mismatch_root / "must-not-exist.wbackup").exists())

            schema_root = root / "schema-mismatch"
            schema_root.mkdir()
            market, web, paper, _, _ = self._fixture_v2_databases(schema_root)
            connection = sqlite3.connect(paper)
            try:
                connection.execute(
                    "UPDATE paper_schema_ledger SET checksum_sha256=?",
                    ("0" * 64,),
                )
                connection.commit()
            finally:
                connection.close()
            with backup_quiesce(
                schema_root / "coordination.lock",
                trusted_test_barrier=True,
            ) as proof:
                with self.assertRaises(LocalBackupError):
                    backup_local_databases_v2(
                        market,
                        web,
                        paper,
                        schema_root / "must-not-exist.wbackup",
                        key,
                        quiesce=proof,
                    )
            self.assertFalse((schema_root / "must-not-exist.wbackup").exists())

            binding_root = root / "binding-mismatch"
            binding_root.mkdir()
            market, web, paper, _, _ = self._fixture_v2_databases(binding_root)
            connection = sqlite3.connect(paper)
            try:
                connection.execute(
                    "UPDATE paper_owner_bindings SET binding_hash=?",
                    ("f" * 64,),
                )
                connection.commit()
            finally:
                connection.close()
            with backup_quiesce(
                binding_root / "coordination.lock",
                trusted_test_barrier=True,
            ) as proof:
                with self.assertRaises(LocalBackupError):
                    backup_local_databases_v2(
                        market,
                        web,
                        paper,
                        binding_root / "must-not-exist.wbackup",
                        key,
                        quiesce=proof,
                    )
            self.assertFalse((binding_root / "must-not-exist.wbackup").exists())

            state_root = root / "paper-state-corrupt"
            state_root.mkdir()
            market, web, paper, _, _ = self._fixture_v2_databases(state_root)
            connection = sqlite3.connect(paper)
            try:
                connection.execute(
                    "UPDATE paper_state_fields SET payload_hash=? "
                    "WHERE field_order=0",
                    ("e" * 64,),
                )
                connection.commit()
            finally:
                connection.close()
            with backup_quiesce(
                state_root / "coordination.lock",
                trusted_test_barrier=True,
            ) as proof:
                with self.assertRaises(LocalBackupError):
                    backup_local_databases_v2(
                        market,
                        web,
                        paper,
                        state_root / "must-not-exist.wbackup",
                        key,
                        quiesce=proof,
                    )
            self.assertFalse((state_root / "must-not-exist.wbackup").exists())

            valid_root = root / "authenticated-tamper"
            valid_root.mkdir()
            market, web, paper, _, _ = self._fixture_v2_databases(valid_root)
            backup = valid_root / "valid.wbackup"
            with backup_quiesce(
                valid_root / "coordination.lock",
                trusted_test_barrier=True,
            ) as proof:
                backup_local_databases_v2(
                    market, web, paper, backup, key, quiesce=proof
                )
            changed = bytearray(backup.read_bytes())
            changed[len(changed) // 2] ^= 1
            tampered = valid_root / "tampered.wbackup"
            tampered.write_bytes(changed)
            os.chmod(tampered, 0o600)
            with self.assertRaises(LocalBackupError):
                restore_local_databases_v2(
                    tampered,
                    valid_root / "tampered-restore",
                    key,
                )
            with self.assertRaises(LocalBackupError):
                restore_local_databases_v2(
                    backup,
                    valid_root / "wrong-key-restore",
                    os.urandom(32),
                )
            self.assertFalse((valid_root / "tampered-restore").exists())
            self.assertFalse((valid_root / "wrong-key-restore").exists())


if __name__ == "__main__":
    unittest.main()
