from __future__ import annotations

import base64
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.web_backup_crypto import (
    BackupCryptoError,
    decrypt_verified_file_to_sink,
    encrypt_stream_to_temporary,
    load_key,
    verify_encrypted_file,
)
from tools.web_backup import main as backup_main
from tools.web_migration_plan import inspect
from tools.web_secret_setup import SECRET_NAMES, _safe_directory, _write_secret_set, build_values


class WebOperationsTests(unittest.TestCase):
    def test_production_allowed_hosts_preserve_internal_healthcheck(self) -> None:
        root = Path(__file__).resolve().parents[1]
        production = (root / "deploy/compose.production.yaml").read_text(
            encoding="utf-8"
        )
        local = (root / "deploy/compose.yaml").read_text(encoding="utf-8")

        expected = (
            'WEALTHY_ALLOWED_HOSTS: '
            '"${WEALTHY_DOMAIN:?WEALTHY_DOMAIN must be set},localhost,127.0.0.1"'
        )
        self.assertEqual(production.count(expected), 2)
        self.assertIn("http://127.0.0.1:8000/api/health", local)

    def test_both_caddy_configs_limit_api_request_bodies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in ("Caddyfile.local", "Caddyfile.production.example"):
            content = (root / "deploy" / name).read_text(encoding="utf-8")
            api = content.split("handle /api/*", 1)[1].split("handle /healthz", 1)[0]
            self.assertIn("request_body", api)
            self.assertIn("max_size 2MB", api)

    def test_operation_parser_does_not_echo_unknown_sensitive_value(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
            backup_main(["--acct", "110-123-456789"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertNotIn("110-123-456789", error.getvalue())
        self.assertNotIn("456789", error.getvalue())

    def test_secret_set_uses_distinct_keys_and_owner_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = _safe_directory(Path(raw) / "secrets")
            values = build_values()
            _write_secret_set(directory, values)

            self.assertEqual(set(values), set(SECRET_NAMES))
            master = load_key(directory / "master_key")
            backup = load_key(directory / "backup_key")
            self.assertEqual(len(master), 32)
            self.assertEqual(len(backup), 32)
            self.assertNotEqual(master, backup)
            self.assertNotIn(master, (directory / "database_url").read_bytes())
            if os.name != "nt":
                for name in SECRET_NAMES:
                    self.assertEqual((directory / name).stat().st_mode & 0o077, 0)

    def test_encrypted_dump_round_trip_and_tamper_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            destination = directory / "backup.wbak"
            key = os.urandom(32)
            plaintext = b"PGDMP\x01private fixture bytes\x00" * 100
            temporary, digest, size = encrypt_stream_to_temporary(
                io.BytesIO(plaintext), destination, key
            )
            os.replace(temporary, destination)

            self.assertGreater(size, len(plaintext))
            self.assertNotIn(plaintext[:16], destination.read_bytes())
            self.assertEqual(verify_encrypted_file(destination, key), digest)
            restored = io.BytesIO()
            decrypt_verified_file_to_sink(destination, key, restored)
            self.assertEqual(restored.getvalue(), plaintext)

            changed = bytearray(destination.read_bytes())
            changed[len(changed) // 2] ^= 1
            destination.write_bytes(changed)
            with self.assertRaises(BackupCryptoError):
                verify_encrypted_file(destination, key)

    def test_migration_plan_reads_and_hashes_without_importing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "fixture.sqlite3"
            connection = sqlite3.connect(source)
            connection.executescript(
                """
                CREATE TABLE schema_meta(version INTEGER NOT NULL);
                INSERT INTO schema_meta(version) VALUES (2);
                CREATE TABLE monthly_asset_snapshots(
                    id INTEGER PRIMARY KEY,
                    realm TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL
                );
                INSERT INTO monthly_asset_snapshots(realm, source_sha256)
                    VALUES ('MANUAL_ACTUAL', 'fixture-hash');
                """
            )
            connection.commit()
            connection.close()
            before = source.read_bytes()

            plan = inspect(source)

            self.assertEqual(plan["status"], "INSPECTED_ONLY")
            self.assertTrue(plan["contains_manual_actual_snapshot"])
            self.assertFalse(plan["postgres_import_performed"])
            self.assertFalse(plan["automatic_actual_import_allowed"])
            self.assertEqual(
                plan["tables"]["monthly_asset_snapshots"]["rows"], 1
            )
            self.assertEqual(source.read_bytes(), before)

    def test_key_loader_rejects_world_readable_key(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX permission check")
        with tempfile.TemporaryDirectory() as raw:
            key_path = Path(raw) / "backup_key"
            key_path.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
            key_path.chmod(0o644)
            with self.assertRaises(BackupCryptoError):
                load_key(key_path)


if __name__ == "__main__":
    unittest.main()
