from __future__ import annotations

import fcntl
import hashlib
import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from asset_assistant.storage_maintenance import (
    DEFAULT_LOW_SPACE_BYTES,
    DEFAULT_SPACE_CHECK_INTERVAL_SECONDS,
    FIXTURE_MARKER_CONTENT,
    FIXTURE_MARKER_NAME,
    REFERENCE_HOLD_TABLE,
    TEMPORARY_RETENTION_MICROSECONDS,
    BackupManifestEntry,
    FixtureCleanupCapability,
    SpaceGate,
    StorageMaintenanceError,
    StorageMaintenanceService,
    TemporaryManifestEntry,
)


NOW = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class MemoryAuditRecorder:
    def __init__(self, *, fail_begin: bool = False, fail_complete: bool = False) -> None:
        self.fail_begin = fail_begin
        self.fail_complete = fail_complete
        self.intents: list[tuple[str, int]] = []
        self.completions: list[tuple[object, object, int]] = []

    def begin(self, *, category: str, started_at_us: int) -> object:
        if self.fail_begin:
            raise RuntimeError("fixture audit begin failure with sensitive detail")
        token = len(self.intents) + 1
        self.intents.append((category, started_at_us))
        return token

    def complete(self, token: object, *, result: object, finished_at_us: int) -> None:
        if self.fail_complete:
            raise RuntimeError("fixture audit completion failure with sensitive detail")
        self.completions.append((token, result, finished_at_us))


def epoch_us(value: datetime) -> int:
    delta = value.astimezone(UTC) - EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _temporary_cleanup_worker(
    root_value: str,
    entry: TemporaryManifestEntry,
    ready: multiprocessing.synchronize.Event,
    start: multiprocessing.synchronize.Event,
    output: multiprocessing.queues.Queue,
) -> None:
    root = Path(root_value)
    capability = FixtureCleanupCapability.issue(root)
    service = StorageMaintenanceService(
        capability,
        market_database=root / "market.sqlite3",
        temporary_directory=root / "managed-temp",
        backup_directory=root / "managed-backups",
        space_gate=SpaceGate(capability),
        audit_recorder=MemoryAuditRecorder(),
    )
    ready.set()
    start.wait(5)
    result = service.cleanup_temporary_files((entry,), now=NOW)
    output.put(
        (
            result.status,
            result.deleted_count,
            result.failure_count,
            result.reason_codes,
        )
    )


class StorageMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            dir="/dev/shm", prefix="codex-storage-maintenance-"
        )
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        marker = self.root / FIXTURE_MARKER_NAME
        marker.write_bytes(FIXTURE_MARKER_CONTENT)
        os.chmod(marker, 0o600)
        self.managed_temp = self.root / "managed-temp"
        self.managed_backups = self.root / "managed-backups"
        self.managed_temp.mkdir(mode=0o700)
        self.managed_backups.mkdir(mode=0o700)
        self.market_database = self.root / "market.sqlite3"
        self._initialize_market_database(reference_registry=True)
        self.capability = FixtureCleanupCapability.issue(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _initialize_market_database(self, *, reference_registry: bool) -> None:
        connection = sqlite3.connect(self.market_database)
        try:
            connection.execute(
                """CREATE TABLE market_information (
                    info_id TEXT PRIMARY KEY,
                    first_seen_at TEXT,
                    first_seen_at_us INTEGER,
                    collected_at TEXT,
                    collected_at_us INTEGER
                )"""
            )
            if reference_registry:
                connection.execute(
                    f"""CREATE TABLE {REFERENCE_HOLD_TABLE} (
                        info_id TEXT NOT NULL,
                        active INTEGER NOT NULL CHECK(active IN (0, 1)),
                        reference_kind TEXT NOT NULL,
                        PRIMARY KEY(info_id, reference_kind)
                    )"""
                )
            connection.commit()
        finally:
            connection.close()
        os.chmod(self.market_database, 0o600)

    def _service(
        self, *, statvfs=None, audit_recorder: MemoryAuditRecorder | None = None
    ) -> StorageMaintenanceService:
        gate = SpaceGate(
            self.capability,
            statvfs=statvfs or os.statvfs,
        )
        return StorageMaintenanceService(
            self.capability,
            market_database=self.market_database,
            temporary_directory=self.managed_temp,
            backup_directory=self.managed_backups,
            space_gate=gate,
            audit_recorder=audit_recorder or MemoryAuditRecorder(),
        )

    def _insert_market_item(
        self,
        info_id: str,
        *,
        first_seen: datetime,
        collected: datetime | None = None,
        first_seen_us: int | None = None,
    ) -> None:
        collected = collected or first_seen
        connection = sqlite3.connect(self.market_database)
        try:
            connection.execute(
                """INSERT INTO market_information
                   (info_id, first_seen_at, first_seen_at_us,
                    collected_at, collected_at_us)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    info_id,
                    first_seen.isoformat(),
                    epoch_us(first_seen) if first_seen_us is None else first_seen_us,
                    collected.isoformat(),
                    epoch_us(collected),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _market_ids(self) -> set[str]:
        connection = sqlite3.connect(self.market_database)
        try:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT info_id FROM market_information"
                ).fetchall()
            }
        finally:
            connection.close()

    @staticmethod
    def _stat_values(available: int, *, inodes: int = 1000):
        return SimpleNamespace(
            f_bavail=available,
            f_frsize=1,
            f_blocks=DEFAULT_LOW_SPACE_BYTES * 4,
            f_favail=inodes,
        )

    def _temporary_entry(
        self,
        path: Path,
        *,
        artifact_id: str,
        state: str = "COMPLETED",
        completed_at_us: int | None = None,
    ) -> TemporaryManifestEntry:
        info = path.lstat()
        return TemporaryManifestEntry(
            artifact_id=artifact_id,
            relative_name=path.name,
            state=state,  # type: ignore[arg-type]
            completed_at_us=(
                epoch_us(NOW) - TEMPORARY_RETENTION_MICROSECONDS
                if completed_at_us is None and state == "COMPLETED"
                else completed_at_us
            ),
            device=info.st_dev,
            inode=info.st_ino,
        )

    def _backup_entry(
        self,
        name: str,
        *,
        backup_id: str,
        created_at_us: int,
        payload: bytes,
    ) -> BackupManifestEntry:
        path = self.managed_backups / name
        path.write_bytes(payload)
        os.chmod(path, 0o600)
        info = path.stat(follow_symlinks=False)
        return BackupManifestEntry(
            backup_id=backup_id,
            relative_name=name,
            created_at_us=created_at_us,
            validation_state="VERIFIED",
            bundle_format="WEALTHY-LOCAL-SQLITE-AES256-GCM-V2",
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload),
            device=info.st_dev,
            inode=info.st_ino,
        )

    def test_fixture_capability_rejects_outside_or_changed_marker(self) -> None:
        with self.assertRaises(StorageMaintenanceError):
            FixtureCleanupCapability.issue(Path("/dev/shm"))
        marker = self.root / FIXTURE_MARKER_NAME
        marker.write_bytes(b"changed")
        with self.assertRaises(StorageMaintenanceError):
            self.capability.assert_valid()

    def test_space_gate_latches_low_space_and_requires_explicit_recovery(self) -> None:
        states = [
            self._stat_values(DEFAULT_LOW_SPACE_BYTES),
            self._stat_values(DEFAULT_LOW_SPACE_BYTES - 1),
            self._stat_values(DEFAULT_LOW_SPACE_BYTES * 2),
            self._stat_values(DEFAULT_LOW_SPACE_BYTES * 2),
            self._stat_values(DEFAULT_LOW_SPACE_BYTES * 2),
        ]

        def fake_statvfs(_path: Path):
            return states.pop(0)

        gate = SpaceGate(self.capability, statvfs=fake_statvfs)
        exact = gate.inspect(now=NOW, planned_write_bytes=1)
        self.assertFalse(exact.low_space)
        self.assertFalse(exact.large_operation_allowed)
        self.assertTrue(exact.paper_mutation_allowed)
        self.assertFalse(exact.write_block_latched)

        low = gate.inspect(now=NOW)
        self.assertTrue(low.low_space)
        self.assertFalse(low.paper_mutation_allowed)
        self.assertTrue(low.write_block_latched)

        restored_space = gate.inspect(now=NOW)
        self.assertFalse(restored_space.low_space)
        self.assertFalse(restored_space.paper_mutation_allowed)
        self.assertTrue(gate.write_block_latched)
        self.assertFalse(
            gate.try_recover(
                now=NOW, integrity_ok=False, reconciliation_ok=True
            )
        )
        self.assertTrue(gate.write_block_latched)
        self.assertTrue(
            gate.try_recover(now=NOW, integrity_ok=True, reconciliation_ok=True)
        )
        self.assertFalse(gate.write_block_latched)
        self.assertEqual(DEFAULT_SPACE_CHECK_INTERVAL_SECONDS, 300)

    def test_space_gate_zero_inodes_and_write_failure_are_latched(self) -> None:
        values = self._stat_values(DEFAULT_LOW_SPACE_BYTES * 2, inodes=0)
        gate = SpaceGate(self.capability, statvfs=lambda _path: values)
        self.assertTrue(gate.inspect(now=NOW).low_space)
        with self.assertRaises(StorageMaintenanceError):
            gate.try_recover(now=NOW, integrity_ok=1, reconciliation_ok=True)  # type: ignore[arg-type]

        healthy = self._stat_values(DEFAULT_LOW_SPACE_BYTES * 2)
        other = SpaceGate(self.capability, statvfs=lambda _path: healthy)
        other.note_write_failure("SQLITE_FULL")
        self.assertFalse(other.inspect(now=NOW).paper_mutation_allowed)
        self.assertTrue(
            other.try_recover(now=NOW, integrity_ok=True, reconciliation_ok=True)
        )

    def test_low_space_allows_exact_market_cleanup_and_latches_paper_writes(self) -> None:
        first_seen = datetime(2025, 10, 16, 6, 0, tzinfo=UTC)
        self._insert_market_item("expired", first_seen=first_seen)
        low = self._stat_values(DEFAULT_LOW_SPACE_BYTES - 1)
        service = self._service(statvfs=lambda _path: low)

        result = service.cleanup_expired_market_information(now=NOW)

        self.assertEqual(result.status, "OK")
        self.assertEqual(result.deleted_count, 1)
        self.assertNotIn("expired", self._market_ids())
        self.assertTrue(service.space_gate.write_block_latched)

    def test_market_cleanup_uses_exact_calendar_year_and_recollection_does_not_extend(self) -> None:
        first_seen = datetime(2025, 10, 16, 6, 0, tzinfo=UTC)
        self._insert_market_item(
            "recollected",
            first_seen=first_seen,
            collected=NOW - timedelta(seconds=1),
        )
        service = self._service()

        before = service.cleanup_expired_market_information(
            now=NOW - timedelta(microseconds=1)
        )
        self.assertEqual(before.deleted_count, 0)
        self.assertIn("recollected", self._market_ids())

        at_boundary = service.cleanup_expired_market_information(now=NOW)
        self.assertEqual(at_boundary.deleted_count, 1)
        self.assertNotIn("recollected", self._market_ids())

    def test_market_cleanup_leap_day_unknown_basis_and_reference_hold(self) -> None:
        leap = datetime(2024, 2, 29, 12, 0, tzinfo=UTC)
        self._insert_market_item("leap", first_seen=leap)
        uncertain = datetime(2024, 1, 1, tzinfo=UTC)
        self._insert_market_item(
            "uncertain",
            first_seen=uncertain,
            first_seen_us=epoch_us(uncertain) + 1,
        )
        held = datetime(2024, 1, 2, tzinfo=UTC)
        self._insert_market_item("held", first_seen=held)
        connection = sqlite3.connect(self.market_database)
        try:
            connection.execute(
                f"INSERT INTO {REFERENCE_HOLD_TABLE} VALUES (?, 1, ?)",
                ("held", "REPORT"),
            )
            connection.commit()
        finally:
            connection.close()
        service = self._service()

        before = service.cleanup_expired_market_information(
            now=datetime(2025, 2, 28, 11, 59, 59, 999999, tzinfo=UTC)
        )
        self.assertEqual(before.deleted_count, 0)
        self.assertIn("leap", self._market_ids())

        at_boundary = service.cleanup_expired_market_information(
            now=datetime(2025, 2, 28, 12, 0, tzinfo=UTC)
        )
        self.assertEqual(at_boundary.deleted_count, 1)
        self.assertEqual(self._market_ids(), {"held", "uncertain"})

    def test_market_cleanup_blocks_without_reference_registry(self) -> None:
        self.market_database.unlink()
        self._initialize_market_database(reference_registry=False)
        self._insert_market_item(
            "expired", first_seen=datetime(2024, 1, 1, tzinfo=UTC)
        )
        service = self._service()

        result = service.cleanup_expired_market_information(now=NOW)

        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.reason_codes, ("REFERENCE_REGISTRY_UNAVAILABLE",))
        self.assertIn("expired", self._market_ids())

    def test_market_cleanup_middle_failure_rolls_back_without_sensitive_detail(self) -> None:
        first_seen = datetime(2024, 1, 1, tzinfo=UTC)
        self._insert_market_item("one", first_seen=first_seen)
        self._insert_market_item("two", first_seen=first_seen)
        connection = sqlite3.connect(self.market_database)
        try:
            connection.execute(
                """CREATE TRIGGER fixture_delete_failure
                   BEFORE DELETE ON market_information
                   WHEN OLD.info_id = 'two'
                   BEGIN
                       SELECT RAISE(ABORT, 'fixture row detail');
                   END"""
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(StorageMaintenanceError) as raised:
            self._service().cleanup_expired_market_information(now=NOW)

        self.assertEqual(self._market_ids(), {"one", "two"})
        self.assertNotIn("fixture row detail", str(raised.exception))
        self.assertNotIn(str(self.root), str(raised.exception))

    def test_temporary_cleanup_enforces_age_completion_and_preserves_unregistered(self) -> None:
        exact = self.managed_temp / "exact.tmp"
        too_new = self.managed_temp / "new.tmp"
        active = self.managed_temp / "active.tmp"
        unregistered = self.managed_temp / "user-note.txt"
        for path in (exact, too_new, active, unregistered):
            path.write_bytes(b"fixture")
            os.chmod(path, 0o600)
        entries = (
            self._temporary_entry(exact, artifact_id="exact"),
            self._temporary_entry(
                too_new,
                artifact_id="new",
                completed_at_us=(
                    epoch_us(NOW) - TEMPORARY_RETENTION_MICROSECONDS + 1
                ),
            ),
            self._temporary_entry(
                active,
                artifact_id="active",
                state="ACTIVE",
                completed_at_us=None,
            ),
        )

        result = self._service().cleanup_temporary_files(entries, now=NOW)

        self.assertEqual(result.status, "OK")
        self.assertEqual(result.deleted_count, 1)
        self.assertEqual(result.skipped_count, 2)
        self.assertFalse(exact.exists())
        self.assertTrue(too_new.exists())
        self.assertTrue(active.exists())
        self.assertTrue(unregistered.exists())

    def test_temporary_cleanup_rejects_symlink_hardlink_and_locked_file(self) -> None:
        protected = self.root / "protected.txt"
        protected.write_bytes(b"protected")
        os.chmod(protected, 0o600)
        symlink = self.managed_temp / "link.tmp"
        symlink.symlink_to(protected)

        hardlink = self.managed_temp / "hard.tmp"
        hardlink_peer = self.managed_temp / "hard-peer.tmp"
        hardlink.write_bytes(b"hard")
        os.chmod(hardlink, 0o600)
        os.link(hardlink, hardlink_peer)

        locked = self.managed_temp / "locked.tmp"
        locked.write_bytes(b"locked")
        os.chmod(locked, 0o600)
        entries = (
            self._temporary_entry(symlink, artifact_id="symlink"),
            self._temporary_entry(hardlink, artifact_id="hardlink"),
            self._temporary_entry(locked, artifact_id="locked"),
        )
        with locked.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self._service().cleanup_temporary_files(entries, now=NOW)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(result.failure_count, 3)
        self.assertIn("FILE_IDENTITY_MISMATCH", result.reason_codes)
        self.assertIn("FILE_IN_USE", result.reason_codes)
        self.assertEqual(protected.read_bytes(), b"protected")
        self.assertTrue(hardlink.exists())
        self.assertTrue(hardlink_peer.exists())
        self.assertTrue(locked.exists())
        self.assertNotIn(str(self.root), repr(result))
        self.assertNotIn("locked.tmp", repr(result))

    def test_temporary_cleanup_blocks_duplicate_manifest_identity(self) -> None:
        path = self.managed_temp / "duplicate.tmp"
        path.write_bytes(b"fixture")
        os.chmod(path, 0o600)
        entry = self._temporary_entry(path, artifact_id="one")
        duplicate = TemporaryManifestEntry(
            artifact_id="two",
            relative_name=entry.relative_name,
            state=entry.state,
            completed_at_us=entry.completed_at_us,
            device=entry.device,
            inode=entry.inode,
        )

        result = self._service().cleanup_temporary_files(
            (entry, duplicate), now=NOW
        )

        self.assertEqual(result.status, "BLOCKED")
        self.assertTrue(path.exists())

    def test_cleanup_audit_records_only_safe_category_counts_and_status(self) -> None:
        path = self.managed_temp / "audited.tmp"
        path.write_bytes(b"fixture")
        os.chmod(path, 0o600)
        entry = self._temporary_entry(path, artifact_id="private-artifact-name")
        recorder = MemoryAuditRecorder()

        result = self._service(audit_recorder=recorder).cleanup_temporary_files(
            (entry,), now=NOW
        )

        self.assertEqual(result.deleted_count, 1)
        self.assertEqual(recorder.intents, [("TEMPORARY", epoch_us(NOW))])
        self.assertEqual(len(recorder.completions), 1)
        recorded = recorder.completions[0][1]
        self.assertEqual(recorded.category, "TEMPORARY")
        self.assertEqual(recorded.deleted_count, 1)
        serialized = repr(recorder.intents + recorder.completions)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn(entry.relative_name, serialized)
        self.assertNotIn(entry.artifact_id, serialized)

    def test_cleanup_audit_is_fail_closed_before_delete_and_never_silent_after(self) -> None:
        before = self.managed_temp / "before.tmp"
        before.write_bytes(b"fixture")
        os.chmod(before, 0o600)
        before_entry = self._temporary_entry(before, artifact_id="before")
        with self.assertRaises(StorageMaintenanceError) as begin_error:
            self._service(
                audit_recorder=MemoryAuditRecorder(fail_begin=True)
            ).cleanup_temporary_files((before_entry,), now=NOW)
        self.assertTrue(before.exists())
        self.assertNotIn("sensitive", str(begin_error.exception))

        after = self.managed_temp / "after.tmp"
        after.write_bytes(b"fixture")
        os.chmod(after, 0o600)
        after_entry = self._temporary_entry(after, artifact_id="after")
        recorder = MemoryAuditRecorder(fail_complete=True)
        with self.assertRaises(StorageMaintenanceError) as completion_error:
            self._service(audit_recorder=recorder).cleanup_temporary_files(
                (after_entry,), now=NOW
            )
        self.assertFalse(after.exists())
        self.assertEqual(len(recorder.intents), 1)
        self.assertEqual(recorder.intents[0][0], "TEMPORARY")
        self.assertNotIn("sensitive", str(completion_error.exception))

    @unittest.skipUnless(hasattr(os, "fork"), "별도 프로세스 flock 검증은 POSIX 전용")
    def test_concurrent_temporary_cleanup_deletes_registered_file_once(self) -> None:
        path = self.managed_temp / "race.tmp"
        path.write_bytes(b"fixture")
        os.chmod(path, 0o600)
        entry = self._temporary_entry(path, artifact_id="race")
        context = multiprocessing.get_context("fork")
        ready = [context.Event(), context.Event()]
        start = context.Event()
        output = context.Queue()
        processes = [
            context.Process(
                target=_temporary_cleanup_worker,
                args=(str(self.root), entry, ready[index], start, output),
            )
            for index in range(2)
        ]
        for process in processes:
            process.start()
        for signal in ready:
            self.assertTrue(signal.wait(5))
        start.set()
        results = [output.get(timeout=5), output.get(timeout=5)]
        for process in processes:
            process.join(5)
            self.assertEqual(process.exitcode, 0)
        output.close()

        self.assertEqual(sum(item[1] for item in results), 1)
        self.assertFalse(path.exists())
        self.assertTrue(all(item[0] in {"OK", "PARTIAL"} for item in results))

    def test_backup_cleanup_blocks_without_policy_and_protects_only_recovery_point(self) -> None:
        only = self._backup_entry(
            "only.wbackup", backup_id="only", created_at_us=1, payload=b"only"
        )
        service = self._service()

        unset = service.cleanup_backups(
            (only,), keep_count=None, newly_verified_backup_id="only", now=NOW
        )
        self.assertEqual(unset.status, "BLOCKED")
        self.assertEqual(unset.reason_codes, ("RETENTION_POLICY_UNSET",))
        self.assertTrue((self.managed_backups / only.relative_name).exists())

        sole = service.cleanup_backups(
            (only,), keep_count=1, newly_verified_backup_id="only", now=NOW
        )
        self.assertEqual(sole.status, "NOTHING_TO_DO")
        self.assertTrue((self.managed_backups / only.relative_name).exists())

    def test_backup_cleanup_requires_current_verified_backup_and_deletes_only_manifest_old(self) -> None:
        old = self._backup_entry(
            "old.wbackup", backup_id="old", created_at_us=1, payload=b"old"
        )
        new = self._backup_entry(
            "new.wbackup", backup_id="new", created_at_us=2, payload=b"new"
        )
        release_zip = self.managed_backups / "wealthy-source.zip"
        release_zip.write_bytes(b"not a managed database backup")
        os.chmod(release_zip, 0o600)

        result = self._service().cleanup_backups(
            (old, new), keep_count=1, newly_verified_backup_id="new", now=NOW
        )

        self.assertEqual(result.status, "OK")
        self.assertEqual(result.deleted_count, 1)
        self.assertFalse((self.managed_backups / old.relative_name).exists())
        self.assertTrue((self.managed_backups / new.relative_name).exists())
        self.assertTrue(release_zip.exists())

    def test_backup_cleanup_fails_closed_on_tamper_duplicate_or_stale_new(self) -> None:
        old = self._backup_entry(
            "old.wbackup", backup_id="old", created_at_us=1, payload=b"old"
        )
        new = self._backup_entry(
            "new.wbackup", backup_id="new", created_at_us=2, payload=b"new"
        )
        (self.managed_backups / new.relative_name).write_bytes(b"bad")
        os.chmod(self.managed_backups / new.relative_name, 0o600)

        tampered = self._service().cleanup_backups(
            (old, new), keep_count=1, newly_verified_backup_id="new", now=NOW
        )
        self.assertEqual(tampered.status, "BLOCKED")
        self.assertIn("MANIFEST_INTEGRITY_FAILED", tampered.reason_codes)
        self.assertTrue((self.managed_backups / old.relative_name).exists())

        # Recreate an internally consistent manifest and prove an old backup
        # cannot be presented as the newly validated recovery point.
        (self.managed_backups / new.relative_name).write_bytes(b"new")
        os.chmod(self.managed_backups / new.relative_name, 0o600)
        new = self._backup_entry(
            "new.wbackup", backup_id="new", created_at_us=2, payload=b"new"
        )
        stale = self._service().cleanup_backups(
            (old, new), keep_count=1, newly_verified_backup_id="old", now=NOW
        )
        self.assertEqual(stale.status, "BLOCKED")
        self.assertEqual(stale.reason_codes, ("NEW_BACKUP_NOT_CURRENT",))
        self.assertTrue((self.managed_backups / old.relative_name).exists())


if __name__ == "__main__":
    unittest.main()
