from __future__ import annotations

import json
import io
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select

from asset_assistant.credential_store import (
    SERVICE_NAME,
    CredentialStore,
    CredentialStoreError,
)
from asset_assistant.domain import DomainValidationError
from asset_assistant.market_information import PROVIDER_BY_ID
from wealthy_server.app import CSRF_COOKIE
from wealthy_server.database import Database
from wealthy_server.local_credentials import (
    LOCAL_MASTER_KEY_ITEM,
    LOCAL_SECRET_SERVICE,
    LocalCredentialRepository,
    LocalSecretStore,
)
from wealthy_server.local_runtime import (
    WEB_STATE_FILENAME,
    build_local_application,
    build_parser,
    _create_admin,
    _run_task,
    one_process_lock,
)
from wealthy_server.models import WebSession
from wealthy_server.repositories import AuthRepository
from wealthy_server.security import PasswordManager


SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "offline_sample.json"
LOCAL_TABLES = {
    "login_attempts",
    "migration_ledger",
    "scenario_snapshots",
    "strategy_target_versions",
    "users",
    "web_sessions",
}
FORBIDDEN_DUPLICATE_TABLES = {
    "collection_runs",
    "job_attempts",
    "job_runs",
    "market_records",
    "monthly_asset_snapshots",
    "provider_secrets",
    "push_subscriptions",
    "report_snapshots",
}


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class FailingKeyring(FakeKeyring):
    def __init__(self) -> None:
        super().__init__()
        self.accessed = False

    def get_password(self, service: str, username: str) -> str | None:
        self.accessed = True
        raise RuntimeError("private backend detail")


class CountingKeyring(FakeKeyring):
    def __init__(self) -> None:
        super().__init__()
        self.get_count = 0

    def get_password(self, service: str, username: str) -> str | None:
        self.get_count += 1
        return super().get_password(service, username)


class ProviderReadFailingKeyring(FakeKeyring):
    def get_password(self, service: str, username: str) -> str | None:
        if service == SERVICE_NAME:
            raise RuntimeError("private provider backend detail")
        return super().get_password(service, username)


class ToggleFailingKeyring(FakeKeyring):
    def __init__(self) -> None:
        super().__init__()
        self.fail_writes = False

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail_writes:
            raise RuntimeError(f"private write detail {password}")
        super().set_password(service, username, password)


class PlaintextFileKeyring(FakeKeyring):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def set_password(self, service: str, username: str, password: str) -> None:
        self.path.write_text(password, encoding="utf-8")
        super().set_password(service, username, password)


class NullKeyring(FakeKeyring):
    pass


def _temporary_directory():
    shared_memory = Path("/dev/shm")
    return tempfile.TemporaryDirectory(dir=shared_memory if shared_memory.is_dir() else None)


def _frontend(root: Path) -> Path:
    frontend = root / "한글 frontend with spaces"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text(
        '<!doctype html><html lang="ko"><div id="root">Wealthy</div></html>',
        encoding="utf-8",
    )
    (frontend / "assets" / "app.js").write_text(
        "globalThis.wealthyFixture=true;", encoding="utf-8"
    )
    (frontend / "sw.js").write_text("legacy", encoding="utf-8")
    (frontend / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    return frontend


def _table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


def _run_sqlite_subprocess(path: Path, action: str) -> str:
    """Exercise the live DB from a genuinely separate POSIX lock owner."""

    script = r"""
import os
import sqlite3

path = os.environ.pop("WEALTHY_TEST_SQLITE_PATH")
action = os.environ.pop("WEALTHY_TEST_SQLITE_ACTION")
connection = sqlite3.connect(path, timeout=5.0)
try:
    if action == "read_users":
        value = connection.execute("SELECT count(*) FROM users").fetchone()[0]
    elif action == "count_sessions":
        value = connection.execute("SELECT count(*) FROM web_sessions").fetchone()[0]
    elif action == "insert_second_user":
        connection.execute(
            "INSERT INTO users "
            "(id, username, password_hash, is_admin, is_active, created_at, password_changed_at) "
            "SELECT '00000000-0000-0000-0000-000000000002', "
            "'fixture-second', password_hash, 0, 1, created_at, password_changed_at "
            "FROM users LIMIT 1"
        )
        connection.commit()
        value = connection.execute("SELECT count(*) FROM users").fetchone()[0]
    elif action == "delete_second_user":
        connection.execute(
            "DELETE FROM users WHERE id = '00000000-0000-0000-0000-000000000002'"
        )
        connection.commit()
        value = connection.execute("SELECT count(*) FROM users").fetchone()[0]
    else:
        raise RuntimeError("unsupported fixture action")
    print(int(value))
finally:
    connection.close()
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("AMENT_PREFIX_PATH", None)
    environment["WEALTHY_TEST_SQLITE_PATH"] = str(path)
    environment["WEALTHY_TEST_SQLITE_ACTION"] = action
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        close_fds=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError("isolated SQLite fixture process failed")
    return completed.stdout.strip()


def _run_backup_restore_subprocess(
    market: Path,
    web_state: Path,
    backup: Path,
    destination: Path,
) -> str:
    """Back up a live server from outside its POSIX lock-owning process."""

    script = r"""
import os
import sqlite3
from pathlib import Path

from tools.web_local_backup import backup_local_databases, restore_local_databases

market = Path(os.environ.pop("WEALTHY_TEST_MARKET_PATH"))
web_state = Path(os.environ.pop("WEALTHY_TEST_WEB_PATH"))
backup = Path(os.environ.pop("WEALTHY_TEST_BACKUP_PATH"))
destination = Path(os.environ.pop("WEALTHY_TEST_RESTORE_PATH"))
key = bytes(range(32))
backup_local_databases(market, web_state, backup, key)
restore_local_databases(backup, destination, key)
connection = sqlite3.connect(destination / "web-state.sqlite3")
try:
    print(int(connection.execute("SELECT count(*) FROM web_sessions").fetchone()[0]))
finally:
    connection.close()
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("AMENT_PREFIX_PATH", None)
    environment.update(
        {
            "WEALTHY_TEST_MARKET_PATH": str(market),
            "WEALTHY_TEST_WEB_PATH": str(web_state),
            "WEALTHY_TEST_BACKUP_PATH": str(backup),
            "WEALTHY_TEST_RESTORE_PATH": str(destination),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        close_fds=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError("isolated online backup fixture process failed")
    return completed.stdout.strip()


def _deleted_sqlite_descriptors(path: Path) -> list[str]:
    descriptor_root = Path("/proc/self/fd")
    if not descriptor_root.is_dir():
        return []
    deleted_targets = {
        f"{path} (deleted)",
        f"{path}-wal (deleted)",
        f"{path}-shm (deleted)",
    }
    found: list[str] = []
    for descriptor in descriptor_root.iterdir():
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        if target in deleted_targets:
            found.append(target)
    return sorted(found)


def _assert_owner_only_sqlite_files(test_case: unittest.TestCase, path: Path) -> None:
    if os.name == "nt":
        return
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        test_case.assertTrue(candidate.is_file(), f"missing SQLite file: {suffix!r}")
        test_case.assertFalse(candidate.is_symlink())
        test_case.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)


def _create_single_local_user(
    data_dir: Path,
    *,
    username: str = "admin",
    password: str = "fixture password only 12345",
):
    """Prepare the exact one-user state required before local server startup."""

    data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(data_dir, 0o700)
    database = Database.for_local_file(data_dir / WEB_STATE_FILENAME)
    try:
        database.create_local_schema()
        return AuthRepository(database.session_factory).create_user(
            username,
            PasswordManager().hash(password),
            is_admin=True,
        )
    finally:
        database.dispose()


class LocalRuntimePersistenceTests(unittest.TestCase):
    def test_packaged_entrypoints_expose_only_local_web_runtime(self) -> None:
        root = Path(__file__).resolve().parents[1]
        metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
        scripts = metadata.split("[project.scripts]", 1)[1].split(
            "[tool.setuptools", 1
        )[0]
        self.assertIn('wealthy-local = "wealthy_server.local_runtime:main"', scripts)
        self.assertNotIn("wealthy-web =", scripts)
        self.assertNotIn("wealthy-admin =", scripts)
        self.assertNotIn("wealthy-worker =", scripts)

    def test_separate_databases_permissions_pragmas_and_restart_persistence(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw) / "한글 local state #1"
            market_parent = Path(raw) / "market data"
            market_parent.mkdir()
            market = market_parent / "market #1.sqlite3"
            frontend = _frontend(Path(raw))
            backend = FakeKeyring()
            secrets = LocalSecretStore(backend, trusted_test_backend=True)
            created = _create_single_local_user(root, username="local-admin")

            first = build_local_application(
                data_dir=root,
                market_database=market,
                frontend_dir=frontend,
                secret_store=secrets,
            )
            web_state = root / WEB_STATE_FILENAME
            self.assertNotEqual(web_state.resolve(), market.resolve())
            web_tables = set(inspect(first.state.database.engine).get_table_names())
            self.assertEqual(web_tables, LOCAL_TABLES)
            self.assertTrue(FORBIDDEN_DUPLICATE_TABLES.isdisjoint(web_tables))
            self.assertTrue({"market_records", "report_snapshots", "job_runs"}.issubset(_table_names(market)))
            self.assertNotIn("users", _table_names(market))
            pragmas = first.state.database.sqlite_pragmas()
            self.assertEqual(pragmas["foreign_keys"], 1)
            self.assertEqual(pragmas["busy_timeout"], 5000)
            self.assertEqual(str(pragmas["journal_mode"]).lower(), "wal")
            self.assertEqual(pragmas["synchronous"], 2)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(market.stat().st_mode), 0o600)
                _assert_owner_only_sqlite_files(self, web_state)

                # A pooled checkout repairs an accidentally widened sidecar
                # before the connection is returned to application code.
                for suffix in ("-wal", "-shm"):
                    os.chmod(Path(f"{web_state}{suffix}"), 0o644)
                with patch(
                    "wealthy_server.database.os.open",
                    side_effect=AssertionError("live SQLite check must not open a file"),
                ):
                    with first.state.database.engine.connect():
                        _assert_owner_only_sqlite_files(self, web_state)

            AuthRepository(first.state.database.session_factory).change_password(
                created.id,
                PasswordManager().hash("fixture replacement password 12345"),
                changed_at=datetime(2026, 10, 11, 7, 0, tzinfo=UTC),
            )
            _assert_owner_only_sqlite_files(self, web_state)
            master_before = backend.values[(LOCAL_SECRET_SERVICE, LOCAL_MASTER_KEY_ITEM)]
            first.state.database.dispose()

            second = build_local_application(
                data_dir=root,
                market_database=market,
                frontend_dir=frontend,
                secret_store=secrets,
            )
            try:
                _assert_owner_only_sqlite_files(self, web_state)
                restored = AuthRepository(second.state.database.session_factory).get_user_by_id(
                    created.id
                )
                self.assertIsNotNone(restored)
                self.assertEqual(restored.username, "local-admin")
                _assert_owner_only_sqlite_files(self, web_state)
                self.assertEqual(
                    backend.values[(LOCAL_SECRET_SERVICE, LOCAL_MASTER_KEY_ITEM)],
                    master_before,
                )
            finally:
                second.state.database.dispose()

    @unittest.skipIf(
        os.name == "nt" or not Path("/proc/self/fd").is_dir(),
        "POSIX /proc descriptor regression is Linux-specific",
    )
    def test_external_sqlite_close_preserves_live_wal_visibility_and_recovery(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw)
            data = root / "data"
            market = root / "market.sqlite3"
            frontend = _frontend(root)
            password = "fixture password only 12345"
            _create_single_local_user(data, username="admin", password=password)
            app = build_local_application(
                data_dir=data,
                market_database=market,
                frontend_dir=frontend,
                secret_store=LocalSecretStore(
                    FakeKeyring(), trusted_test_backend=True
                ),
            )
            web_state = data / WEB_STATE_FILENAME
            client = TestClient(app, base_url="http://localhost")
            try:
                logged_in = client.post(
                    "/api/v1/login",
                    headers={"Origin": "http://localhost"},
                    json={"username": "admin", "password": password},
                )
                self.assertEqual(logged_in.status_code, 200, logged_in.text)

                sidecars = (Path(f"{web_state}-wal"), Path(f"{web_state}-shm"))
                before_identity = tuple(
                    (candidate.stat().st_dev, candidate.stat().st_ino)
                    for candidate in sidecars
                )
                self.assertEqual(_run_sqlite_subprocess(web_state, "read_users"), "1")
                self.assertEqual(
                    tuple(
                        (candidate.stat().st_dev, candidate.stat().st_ino)
                        for candidate in sidecars
                    ),
                    before_identity,
                )
                self.assertEqual(_deleted_sqlite_descriptors(web_state), [])
                _assert_owner_only_sqlite_files(self, web_state)

                # These commits occur after the external process has closed
                # its connection.  They must remain visible through the live
                # WAL rather than diverging into an unlinked sidecar.
                for _ in range(3):
                    logged_in = client.post(
                        "/api/v1/login",
                        headers={"Origin": "http://localhost"},
                        json={"username": "admin", "password": password},
                    )
                    self.assertEqual(logged_in.status_code, 200, logged_in.text)

                with app.state.database.session_factory() as session:
                    server_sessions = int(
                        session.scalar(select(func.count(WebSession.id))) or 0
                    )
                self.assertEqual(server_sessions, 4)
                self.assertEqual(
                    int(_run_sqlite_subprocess(web_state, "count_sessions")),
                    server_sessions,
                )

                restored_sessions = int(
                    _run_backup_restore_subprocess(
                        market,
                        web_state,
                        root / "live.wbackup",
                        root / "restored",
                    )
                )
                self.assertEqual(restored_sessions, server_sessions)
                self.assertEqual(_deleted_sqlite_descriptors(web_state), [])
                _assert_owner_only_sqlite_files(self, web_state)

                self.assertEqual(
                    _run_sqlite_subprocess(web_state, "insert_second_user"), "2"
                )
                rejected = client.get("/api/v1/session")
                self.assertEqual(rejected.status_code, 400, rejected.text)
                self.assertIn("로컬 사용자 상태", rejected.text)
                self.assertNotIn("fixture-second", rejected.text)
                self.assertNotIn(str(root), rejected.text)

                self.assertEqual(
                    _run_sqlite_subprocess(web_state, "delete_second_user"), "1"
                )
                recovered = client.get("/api/v1/session")
                self.assertEqual(recovered.status_code, 200, recovered.text)
                self.assertTrue(recovered.json()["authenticated"])
                self.assertEqual(_deleted_sqlite_descriptors(web_state), [])
                _assert_owner_only_sqlite_files(self, web_state)
            finally:
                client.close()
                app.state.database.dispose()

    def test_market_and_web_state_must_not_be_same_file(self) -> None:
        with _temporary_directory() as raw:
            data = Path(raw) / "data"
            data.mkdir()
            frontend = _frontend(Path(raw))
            with self.assertRaises(DomainValidationError):
                build_local_application(
                    data_dir=data,
                    market_database=data / WEB_STATE_FILENAME,
                    frontend_dir=frontend,
                    secret_store=LocalSecretStore(
                        FakeKeyring(), trusted_test_backend=True
                    ),
                )

    @unittest.skipIf(os.name == "nt", "hard-link identity check is POSIX-specific")
    def test_market_hard_link_to_web_state_is_rejected_without_modification(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw)
            data = root / "data"
            frontend = _frontend(root)
            secrets = LocalSecretStore(FakeKeyring(), trusted_test_backend=True)
            original_market = root / "market.sqlite3"
            _create_single_local_user(data)
            app = build_local_application(
                data_dir=data,
                market_database=original_market,
                frontend_dir=frontend,
                secret_store=secrets,
            )
            app.state.database.dispose()
            web_state = data / WEB_STATE_FILENAME
            linked_market = root / "linked-market.sqlite3"
            os.link(web_state, linked_market)
            original_inode = web_state.stat().st_ino
            with self.assertRaises(DomainValidationError):
                build_local_application(
                    data_dir=data,
                    market_database=linked_market,
                    frontend_dir=frontend,
                    secret_store=secrets,
                )
            self.assertEqual(web_state.stat().st_ino, original_inode)
            self.assertEqual(linked_market.stat().st_ino, original_inode)

    def test_keyring_failure_has_no_plaintext_fallback(self) -> None:
        store = LocalSecretStore(FailingKeyring(), trusted_test_backend=True)
        with self.assertRaises(CredentialStoreError) as caught:
            store.load_or_create_master_key()
        self.assertNotIn("private backend detail", str(caught.exception))
        self.assertFalse(store.status()[0])

    def test_unapproved_injected_keyrings_are_rejected_before_access(self) -> None:
        with _temporary_directory() as raw:
            plaintext = Path(raw) / "must-not-be-created.json"
            backends = (
                PlaintextFileKeyring(plaintext),
                NullKeyring(),
                FailingKeyring(),
            )
            for backend in backends:
                with self.subTest(backend=type(backend).__name__):
                    store = LocalSecretStore(backend)
                    with self.assertRaises(CredentialStoreError) as caught:
                        store.load_or_create_master_key()
                    self.assertNotIn("private backend detail", str(caught.exception))
                    self.assertEqual(store.status(), (False, "UNAVAILABLE"))
                    self.assertEqual(backend.values, {})
                    self.assertFalse(getattr(backend, "accessed", False))
                    self.assertFalse(plaintext.exists())

    def test_trusted_memory_keyring_remains_available_for_fixtures(self) -> None:
        backend = FakeKeyring()
        store = LocalSecretStore(backend, trusted_test_backend=True)
        self.assertEqual(len(store.load_or_create_master_key()), 32)
        self.assertEqual(store.status(), (True, "OS_KEYRING"))

    def test_provider_keyring_snapshot_is_passive_after_startup(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw)
            data = root / "data"
            user = _create_single_local_user(data)
            backend = CountingKeyring()
            app = build_local_application(
                data_dir=data,
                market_database=root / "market.sqlite3",
                frontend_dir=_frontend(root),
                secret_store=LocalSecretStore(
                    backend, trusted_test_backend=True
                ),
                clock=lambda: datetime(2026, 10, 11, 7, 0, tzinfo=UTC),
            )
            reads_after_startup = backend.get_count
            credentials = app.state.repositories["credentials"]
            client = TestClient(app, base_url="http://localhost")
            logged_in = client.post(
                "/api/v1/login",
                headers={"Origin": "http://localhost"},
                json={
                    "username": "admin",
                    "password": "fixture password only 12345",
                },
            )
            self.assertEqual(logged_in.status_code, 200, logged_in.text)
            for _ in range(3):
                status = credentials.status(
                    user.id,
                    provider="sec",
                    environment="default",
                    secret_name="user_agent",
                )
                self.assertFalse(status.configured)
                storage = credentials.storage_status()
                self.assertEqual(storage["state"], "AVAILABLE_CONFIRMED")
                self.assertFalse(storage["safe_recheck_allowed"])
                app.state.market_information_service.status()
                self.assertEqual(client.get("/api/v1/credentials").status_code, 200)
                self.assertEqual(
                    client.get("/api/v1/market-information/status").status_code,
                    200,
                )
            with patch(
                "asset_assistant.market_information.PROVIDER_SPECS",
                (PROVIDER_BY_ID["sec_press"],),
            ):
                for _ in range(3):
                    result = app.state.market_information_service.refresh(
                        manual=False
                    )
                    self.assertEqual(result["status"], "NEEDS_CONFIGURATION")
            self.assertEqual(backend.get_count, reads_after_startup)
            client.close()
            app.state.database.dispose()

    def test_confirmed_provider_store_failure_blocks_startup_without_fallback(self) -> None:
        backend = ProviderReadFailingKeyring()
        repository = LocalCredentialRepository(
            CredentialStore(backend, trusted_test_backend=True),
            owner_guard=lambda _owner=None: None,
            clock=lambda: datetime(2026, 10, 11, 7, 0, tzinfo=UTC),
        )
        with self.assertRaises(CredentialStoreError) as caught:
            repository.prime()
        self.assertNotIn("private provider backend detail", str(caught.exception))
        self.assertEqual(
            repository.storage_status(),
            {
                "available": False,
                "confirmed": True,
                "state": "UNAVAILABLE_CONFIRMED",
                "backend": "UNAVAILABLE",
                "plaintext_fallback": False,
                "checked_at": "2026-10-11T07:00:00+00:00",
                "safe_recheck_allowed": False,
            },
        )

    def test_explicit_backend_failure_updates_only_confirmed_cached_status(self) -> None:
        backend = ToggleFailingKeyring()
        repository = LocalCredentialRepository(
            CredentialStore(backend, trusted_test_backend=True),
            owner_guard=lambda _owner=None: None,
            clock=lambda: datetime(2026, 10, 11, 7, 0, tzinfo=UTC),
        )
        repository.prime()
        backend.fail_writes = True
        marker = "private-secret-tail-456789"
        with self.assertRaises(CredentialStoreError) as caught:
            repository.upsert(
                "owner",
                provider="kis",
                environment="paper",
                secret_name="app_key",
                plaintext=marker,
            )
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn("456789", str(caught.exception))
        status = repository.storage_status()
        self.assertFalse(status["available"])
        self.assertTrue(status["confirmed"])
        self.assertEqual(status["state"], "UNAVAILABLE_CONFIRMED")
        self.assertFalse(status["safe_recheck_allowed"])
        self.assertNotIn(marker, str(status))

    def test_local_state_with_multiple_users_fails_closed(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw)
            data = root / "data"
            frontend = _frontend(root)
            backend = FakeKeyring()
            secrets = LocalSecretStore(backend, trusted_test_backend=True)
            market = root / "market.sqlite3"
            first = _create_single_local_user(data, username="first")
            app = build_local_application(
                data_dir=data,
                market_database=market,
                frontend_dir=frontend,
                secret_store=secrets,
            )
            repository = AuthRepository(app.state.database.session_factory)
            password_hash = PasswordManager().hash("fixture password only 12345")
            client = TestClient(app, base_url="http://localhost")
            try:
                logged_in = client.post(
                    "/api/v1/login",
                    headers={"Origin": "http://localhost"},
                    json={
                        "username": "first",
                        "password": "fixture password only 12345",
                    },
                )
                self.assertEqual(logged_in.status_code, 200, logged_in.text)
                mutation_headers = {
                    "Origin": "http://localhost",
                    "X-CSRF-Token": client.cookies.get(CSRF_COOKIE),
                }
                imported = client.post(
                    "/api/v1/scenarios/sample", headers=mutation_headers
                )
                self.assertEqual(imported.status_code, 200, imported.text)
                report_id = imported.json()["report_id"]
                configured = client.put(
                    "/api/v1/credentials/kis/paper",
                    headers=mutation_headers,
                    json={
                        "values": {
                            "app_key": "fixture-app-key",
                            "app_secret": "fixture-app-secret",
                        }
                    },
                )
                self.assertEqual(configured.status_code, 200, configured.text)

                repository.create_user("second", password_hash)
                for response in (
                    client.post(
                        "/api/v1/login",
                        headers={"Origin": "http://localhost"},
                        json={
                            "username": "first",
                            "password": "fixture password only 12345",
                        },
                    ),
                    client.get("/api/v1/session"),
                    client.get("/api/v1/reports"),
                    client.get(f"/api/v1/reports/{report_id}"),
                    client.get("/api/v1/credentials"),
                    client.get("/api/v1/jobs"),
                ):
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn("로컬 사용자 상태", response.text)
                    self.assertNotIn("first", response.text)
                    self.assertNotIn("second", response.text)
                    self.assertNotIn(str(root), response.text)

                for operation in (
                    lambda: app.state.service.reports.list(first.id),
                    lambda: app.state.repositories["jobs"].list(first.id),
                    lambda: app.state.repositories["credentials"].status(
                        first.id,
                        provider="kis",
                        environment="paper",
                        secret_name="app_key",
                    ),
                ):
                    with self.assertRaises(DomainValidationError) as caught:
                        operation()
                    message = str(caught.exception)
                    self.assertNotIn("first", message)
                    self.assertNotIn("second", message)
                    self.assertNotIn(str(root), message)
            finally:
                client.close()
            app.state.database.dispose()
            before = dict(backend.values)
            with self.assertRaises(DomainValidationError) as caught:
                build_local_application(
                    data_dir=data,
                    market_database=market,
                    frontend_dir=frontend,
                    secret_store=secrets,
                )
            self.assertEqual(backend.values, before)
            self.assertNotIn("first", str(caught.exception))
            self.assertNotIn("second", str(caught.exception))
            self.assertNotIn(str(root), str(caught.exception))

    def test_local_state_without_user_fails_before_keyring_access(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw)
            data = root / "data"
            data.mkdir(mode=0o700)
            database = Database.for_local_file(data / WEB_STATE_FILENAME)
            database.create_local_schema()
            database.dispose()
            backend = FakeKeyring()
            with self.assertRaises(DomainValidationError) as caught:
                build_local_application(
                    data_dir=data,
                    market_database=root / "market.sqlite3",
                    frontend_dir=_frontend(root),
                    secret_store=LocalSecretStore(
                        backend, trusted_test_backend=True
                    ),
                )
            self.assertEqual(backend.values, {})
            self.assertNotIn(str(root), str(caught.exception))

    def test_one_process_lock_rejects_second_owner(self) -> None:
        with _temporary_directory() as raw:
            data = Path(raw)
            with one_process_lock(data):
                with self.assertRaises(DomainValidationError):
                    with one_process_lock(data):
                        self.fail("second lock must not be acquired")

    def test_cli_contract_requires_explicit_data_and_market_paths(self) -> None:
        parser = build_parser()
        serve = parser.parse_args(
            [
                "serve",
                "--data-dir",
                "/tmp/local-data",
                "--market-database",
                "/tmp/market.sqlite3",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ]
        )
        self.assertEqual((serve.host, serve.port), ("127.0.0.1", 8000))
        task = parser.parse_args(
            [
                "run-task",
                "collect",
                "--data-dir",
                "/tmp/local-data",
                "--market-database",
                "/tmp/market.sqlite3",
            ]
        )
        self.assertEqual(task.task, "collect")

    def test_create_admin_is_tty_only_append_once_and_never_stores_password(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw)
            data = root / "data"
            market = root / "market.sqlite3"
            args = build_parser().parse_args(
                [
                    "create-admin",
                    "--data-dir",
                    str(data),
                    "--market-database",
                    str(market),
                ]
            )
            backend = FakeKeyring()
            secrets = LocalSecretStore(backend, trusted_test_backend=True)
            password = "fixture password only 12345"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("wealthy_server.local_runtime.sys.stdin.isatty", return_value=True),
                patch("wealthy_server.local_runtime.input", return_value="local-admin"),
                patch(
                    "wealthy_server.local_runtime.getpass.getpass",
                    side_effect=(password, password),
                ),
                patch("wealthy_server.local_runtime.sys.stdout", stdout),
                patch("wealthy_server.local_runtime.sys.stderr", stderr),
            ):
                self.assertEqual(_create_admin(args, secret_store=secrets), 0)
            self.assertNotIn(password, stdout.getvalue() + stderr.getvalue())
            web_state = data / WEB_STATE_FILENAME
            connection = sqlite3.connect(web_state)
            try:
                rows = connection.execute(
                    "SELECT username, password_hash FROM users"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "local-admin")
            self.assertNotIn(password, rows[0][1])

            # Existing state is rejected before any new value is read and the
            # original row remains unchanged.
            stderr = io.StringIO()
            with (
                patch("wealthy_server.local_runtime.sys.stdin.isatty", return_value=True),
                patch(
                    "wealthy_server.local_runtime.input",
                    side_effect=AssertionError("must not prompt"),
                ),
                patch("wealthy_server.local_runtime.sys.stderr", stderr),
            ):
                self.assertEqual(_create_admin(args, secret_store=secrets), 2)
            self.assertNotIn("local-admin", stderr.getvalue())
            connection = sqlite3.connect(web_state)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            finally:
                connection.close()

    def test_create_admin_non_tty_does_not_create_state_database(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw)
            data = root / "data"
            args = build_parser().parse_args(
                [
                    "create-admin",
                    "--data-dir",
                    str(data),
                    "--market-database",
                    str(root / "market.sqlite3"),
                ]
            )
            with (
                patch("wealthy_server.local_runtime.sys.stdin.isatty", return_value=False),
                patch("wealthy_server.local_runtime.sys.stderr", io.StringIO()),
            ):
                self.assertEqual(
                    _create_admin(
                        args,
                        secret_store=LocalSecretStore(
                            FakeKeyring(), trusted_test_backend=True
                        ),
                    ),
                    2,
                )
            self.assertFalse((data / WEB_STATE_FILENAME).exists())

    def test_run_task_uses_explicit_market_store_without_network(self) -> None:
        with _temporary_directory() as raw:
            root = Path(raw)
            args = build_parser().parse_args(
                [
                    "run-task",
                    "collect",
                    "--data-dir",
                    str(root / "data"),
                    "--market-database",
                    str(root / "market.sqlite3"),
                ]
            )
            observed: dict[str, object] = {}

            class FakeAutomationService:
                def __init__(self, *, store) -> None:  # type: ignore[no-untyped-def]
                    observed["path"] = store.path

                def run_scheduled(self, task: str):  # type: ignore[no-untyped-def]
                    observed["task"] = task
                    return SimpleNamespace(status="OK")

            with patch(
                "wealthy_server.local_runtime.AutomationService",
                FakeAutomationService,
            ), patch("wealthy_server.local_runtime.sys.stdout", io.StringIO()):
                self.assertEqual(_run_task(args), 0)
            self.assertEqual(observed["path"], root / "market.sqlite3")
            self.assertEqual(observed["task"], "collect")
            self.assertFalse((root / "data" / WEB_STATE_FILENAME).exists())


class LocalRuntimeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = _temporary_directory()
        cls.root = Path(cls.temporary.name)
        cls.backend = FakeKeyring()
        cls.secret_store = LocalSecretStore(cls.backend, trusted_test_backend=True)
        cls.frontend = _frontend(cls.root)
        cls.password = "fixture password only 12345"
        cls.user = _create_single_local_user(
            cls.root / "private data",
            username="admin",
            password=cls.password,
        )
        cls.app = build_local_application(
            data_dir=cls.root / "private data",
            market_database=cls.root / "market.sqlite3",
            frontend_dir=cls.frontend,
            secret_store=cls.secret_store,
            clock=lambda: datetime(2026, 10, 11, 7, 0, tzinfo=UTC),
        )
        cls.client = TestClient(cls.app, base_url="http://localhost")
        logged_in = cls.client.post(
            "/api/v1/login",
            headers={"Origin": "http://localhost"},
            json={"username": "admin", "password": cls.password},
        )
        if logged_in.status_code != 200:
            raise AssertionError(logged_in.text)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.app.state.database.dispose()
        cls.temporary.cleanup()

    def mutation_headers(self) -> dict[str, str]:
        return {
            "Origin": "http://localhost",
            "X-CSRF-Token": self.client.cookies.get(CSRF_COOKIE),
        }

    def test_same_origin_static_spa_and_request_boundaries(self) -> None:
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("Wealthy", self.client.get("/dashboard").text)
        asset = self.client.get("/assets/app.js")
        self.assertEqual(asset.status_code, 200)
        self.assertNotIn("text/html", asset.headers.get("content-type", ""))
        self.assertEqual(self.client.get("/assets/missing.js").status_code, 404)
        self.assertEqual(self.client.get("/%2e%2e/etc/passwd").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/not-present").status_code, 404)
        for legacy in ("/sw.js", "/manifest.webmanifest"):
            response = self.client.get(legacy)
            self.assertEqual(response.status_code, 410)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(
            self.client.get("/api/health", headers={"Host": "example.invalid"}).status_code,
            400,
        )
        bad_origin = self.client.post(
            "/api/v1/login",
            headers={"Origin": "https://example.invalid"},
            json={"username": "admin", "password": self.password},
        )
        self.assertEqual(bad_origin.status_code, 403)
        self.assertEqual(self.client.post("/api/v1/scenarios/sample").status_code, 403)

    def test_local_contracts_mask_user_text_and_never_invent_actual_strategy(self) -> None:
        marker = "계좌 110-123-456789"
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        payload["scenario_label"] = marker
        payload["accounts"][0]["alias"] = marker
        payload["positions"][0]["name"] = marker
        payload["assumptions"][0] = marker
        uploaded = self.client.post(
            "/api/v1/scenarios/upload",
            headers=self.mutation_headers(),
            files={
                "file": (
                    "scenario.json",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
            },
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)

        portfolio = self.client.get("/api/v1/portfolio")
        self.assertEqual(portfolio.status_code, 200, portfolio.text)
        value = portfolio.json()
        self.assertEqual(
            set(value),
            {
                "data_scope",
                "as_of",
                "stale",
                "investable_total_krw",
                "accounts",
                "positions",
                "asset_class_totals",
                "monthly_history",
                "actual_strategy",
            },
        )
        self.assertEqual(value["actual_strategy"]["status"], "UNAVAILABLE")
        self.assertFalse(value["actual_strategy"]["double_count_checked"])
        self.assertNotIn(marker, portfolio.text)
        self.assertNotIn("456789", portfolio.text)

        weights = {
            "LONG_TERM_INDEX": "100",
            "VALUE": "0",
            "QUALITY_GROWTH": "0",
            "GROWTH": "0",
            "HIGH_DIVIDEND": "0",
            "DIVIDEND_GROWTH": "0",
            "MOMENTUM": "0",
            "ASSET_ALLOCATION": "0",
        }
        saved = self.client.post(
            "/api/v1/strategy-targets",
            headers=self.mutation_headers(),
            json={"version": marker, "weights": weights},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        history = self.client.get("/api/v1/strategy-targets/history")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()["items"]), 1)
        self.assertNotIn(marker, history.text)
        self.assertNotIn("456789", history.text)

        notifications = self.client.get("/api/v1/notifications")
        self.assertEqual(notifications.status_code, 200)
        self.assertEqual(
            notifications.json()["browser_notifications"], "PAGE_OPEN_ONLY"
        )
        self.assertTrue(notifications.json()["items"])
        self.assertEqual(
            set(notifications.json()["items"][0]),
            {"id", "kind", "title", "message", "created_at", "status"},
        )

    def test_report_history_preserves_scope_and_manual_generation_without_rewriting_old_rows(self) -> None:
        sample = self.client.post(
            "/api/v1/scenarios/sample", headers=self.mutation_headers()
        )
        self.assertEqual(sample.status_code, 200, sample.text)

        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        payload["realm"] = "MANUAL_ACTUAL"
        payload["scenario_label"] = "수동 실제자료 검증"
        manual = self.client.post(
            "/api/v1/scenarios/upload",
            headers=self.mutation_headers(),
            files={
                "file": (
                    "manual.json",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
            },
        )
        self.assertEqual(manual.status_code, 200, manual.text)

        historical_id = self.app.state.market_store.save_report(
            report_date="2026-10-10",
            created_at=datetime(2026, 10, 10, 7, 0, tzinfo=UTC),
            status="OK",
            markdown="# 과거 메타데이터 없는 보고서",
            source_run_ids=(),
        )
        historical_before = dict(
            self.app.state.market_store.report_row(historical_id)
        )
        listing = self.client.get("/api/v1/reports")
        self.assertEqual(listing.status_code, 200, listing.text)
        by_id = {item["id"]: item for item in listing.json()["items"]}

        sample_item = by_id[sample.json()["report_id"]]
        manual_item = by_id[manual.json()["report_id"]]
        self.assertEqual(
            (sample_item["data_scope"], sample_item["generation_kind"]),
            ("SAMPLE", "MANUAL"),
        )
        self.assertEqual(
            (manual_item["data_scope"], manual_item["generation_kind"]),
            ("MANUAL_ACTUAL", "MANUAL"),
        )
        self.assertIsNotNone(sample_item["data_cutoff_at"])
        self.assertIsNotNone(manual_item["data_cutoff_at"])
        self.assertIsNone(by_id[historical_id]["data_scope"])
        self.assertIsNone(by_id[historical_id]["generation_kind"])
        self.assertIsNone(by_id[historical_id]["data_cutoff_at"])

        for report_id, expected_scope in (
            (sample.json()["report_id"], "SAMPLE"),
            (manual.json()["report_id"], "MANUAL_ACTUAL"),
        ):
            detail = self.client.get(f"/api/v1/reports/{report_id}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["data_scope"], expected_scope)
            self.assertEqual(detail.json()["generation_kind"], "MANUAL")

        # The legacy row remains byte-for-byte in the immutable report table;
        # absence of metadata is represented as unknown rather than inferred.
        old_detail = self.client.get(f"/api/v1/reports/{historical_id}")
        self.assertEqual(old_detail.status_code, 200, old_detail.text)
        self.assertIsNone(old_detail.json()["data_scope"])
        self.assertIsNone(old_detail.json()["generation_kind"])
        self.assertEqual(
            dict(self.app.state.market_store.report_row(historical_id)),
            historical_before,
        )

    def test_credentials_use_existing_keyring_mapping_without_api_exposure(self) -> None:
        secret = "fixture-secret-not-real"
        public = "fixture-public-not-real"
        stored = self.client.put(
            "/api/v1/credentials/kis/paper",
            headers=self.mutation_headers(),
            json={"values": {"app_key": public, "app_secret": secret}},
        )
        self.assertEqual(stored.status_code, 200, stored.text)
        self.assertNotIn(secret, stored.text)
        self.assertNotIn(public, stored.text)
        self.assertEqual(
            self.backend.values[(SERVICE_NAME, "kis_paper:app_secret")], secret
        )
        status_response = self.client.get("/api/v1/credentials")
        self.assertEqual(status_response.status_code, 200)
        storage = status_response.json()["storage"]
        self.assertTrue(storage["available"])
        self.assertTrue(storage["confirmed"])
        self.assertEqual(storage["state"], "AVAILABLE_CONFIRMED")
        self.assertFalse(storage["safe_recheck_allowed"])
        self.assertFalse(storage["plaintext_fallback"])
        self.assertNotIn(secret, status_response.text)
        self.assertNotIn(public, status_response.text)
        removed = self.client.delete(
            "/api/v1/credentials/kis/paper", headers=self.mutation_headers()
        )
        self.assertEqual(removed.status_code, 200)
        self.assertNotIn((SERVICE_NAME, "kis_paper:app_secret"), self.backend.values)

    def test_external_push_routes_are_disabled(self) -> None:
        status = self.client.get("/api/v1/push/public-key")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "DISABLED_LOCAL_ONLY")
        response = self.client.post(
            "/api/v1/push/subscriptions",
            headers=self.mutation_headers(),
            json={
                "endpoint": "https://push.example.invalid/subscription",
                "keys": {"p256dh": "fixture", "auth": "fixture"},
                "device_label": "fixture",
            },
        )
        self.assertEqual(response.status_code, 410)


if __name__ == "__main__":
    unittest.main()
