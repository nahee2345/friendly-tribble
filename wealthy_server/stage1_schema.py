"""Stage1 ledger schema v1: non-destructive, versioned, preflight-backed.

This module exclusively owns Stage1's SQLite schema registry. It never
modifies legacy orders, transfers, deposits or authentication records.

Compatibility rules:
* Unknown future schema versions refuse to open (no silent downgrade).
* A populated legacy database gets an online, verified pre-migration copy.
* Schema changes commit atomically; source data is not rewritten.
* A nonempty migration backup is never overwritten.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

CURRENT_VERSION = 1
_MIGRATION_LOCK = threading.RLock()
LEGACY_TABLES = ("stage1_events", "portfolio_transfers", "virtual_orders", "portfolios")


def _existing_version(db: sqlite3.Connection) -> int:
    table = db.execute("""SELECT name FROM sqlite_master
                          WHERE type='table' AND name='stage1_schema_registry'""").fetchone()
    if table is None:
        return 0
    row = db.execute("SELECT schema_version FROM stage1_schema_registry WHERE id=1").fetchone()
    if row is None or type(row[0]) is not int or row[0] < 1:
        raise ValueError("Invalid Stage1 schema registry; refusing migration.")
    return row[0]


def _has_prior_data(db: sqlite3.Connection) -> bool:
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in LEGACY_TABLES:
        if table in tables and db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            return True
    return False


def _checked_backup_dir(location: Path) -> Path:
    folder = location.parent.parent / "backups"
    if folder.is_symlink():
        raise ValueError("Pre-migration backup directory is a symlink.")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = folder.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Backup directory is not a directory.")
    if os.name == "posix" and (
        info.st_uid != os.getuid() or info.st_mode & 0o077
    ):
        raise ValueError("Unsafe pre-migration backup directory permissions.")
    return folder


def _preflight_backup(db: sqlite3.Connection, location: Path) -> tuple[str, str]:
    folder = _checked_backup_dir(location)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"stage1-premigration-v0-{stamp}.sqlite3"
    fd, temp_path = tempfile.mkstemp(prefix=".stage1-migrate-", dir=folder)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        os.close(fd)
        fd = -1
        with closing(sqlite3.connect(temp_path)) as destination:
            db.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if check is None or check[0] != "ok":
                raise ValueError("Pre-migration backup integrity check failed.")
        digest = hashlib.sha256()
        with open(temp_path, "rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
            os.fsync(file.fileno())
        # Exclusive creation avoids overwriting an existing recovery point.
        target = folder / filename
        os.link(temp_path, target, follow_symlinks=False)
        folder_fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(folder_fd)
        finally:
            os.close(folder_fd)
        return (filename, digest.hexdigest())
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _create_v1(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE stage1_schema_registry (
        id INTEGER PRIMARY KEY CHECK(id=1),
        schema_version INTEGER NOT NULL CHECK(schema_version>=1),
        migrated_at_utc TEXT NOT NULL,
        backup_filename TEXT,
        backup_sha256 TEXT
    )""")
    db.execute("""CREATE TABLE stage1_accounting_events (
        owner TEXT NOT NULL,
        event_key TEXT NOT NULL,
        portfolio TEXT NOT NULL,
        category TEXT NOT NULL
            CHECK(category IN ('DIVIDEND','DIVIDEND_TAX','INTEREST',
                               'TRADING_FEE','TRADING_TAX','FX_FEE','ADJUSTMENT')),
        amount TEXT NOT NULL,
        currency TEXT NOT NULL CHECK(currency IN ('KRW','USD')),
        observed_at_utc TEXT NOT NULL,
        reference TEXT NOT NULL,
        evidence_source TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'RECORDED'
            CHECK(status IN ('RECORDED','PENDING_RECONCILIATION','RECONCILED')),
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY(owner,event_key)
    )""")
    db.execute("""CREATE TABLE stage1_trade_metadata (
        owner TEXT NOT NULL,
        portfolio TEXT NOT NULL,
        order_key TEXT NOT NULL,
        quote_as_of TEXT,
        foreign_exchange_rate TEXT,
        fee_amount TEXT,
        tax_amount TEXT,
        costs_currency TEXT CHECK(costs_currency IN ('KRW','USD') OR costs_currency IS NULL),
        source TEXT NOT NULL,
        observed_at_utc TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY(owner,portfolio,order_key)
    )""")
    db.execute("""CREATE TABLE stage1_corporate_events (
        owner TEXT NOT NULL,
        event_key TEXT NOT NULL,
        symbol TEXT NOT NULL,
        category TEXT NOT NULL
            CHECK(category IN ('SPLIT','REVERSE_SPLIT','MERGER','DELISTING','SPINOFF')),
        effective_date TEXT NOT NULL,
        terms_json TEXT NOT NULL,
        evidence_source TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING_RECONCILIATION'
            CHECK(status IN ('PENDING_RECONCILIATION','RECONCILED')),
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY(owner,event_key)
    )""")
    db.execute("""CREATE INDEX idx_stage1_accounting_date
                  ON stage1_accounting_events(owner,observed_at_utc)""")
    db.execute("""CREATE INDEX idx_stage1_corporate_symbol
                  ON stage1_corporate_events(owner,symbol,effective_date)""")
    db.execute("""CREATE INDEX idx_stage1_orders_history
                  ON virtual_orders(owner,portfolio,created_at,request_id)""")
    db.execute("""CREATE INDEX idx_stage1_transfer_history
                  ON portfolio_transfers(owner,created_at)""")


def ensure_schema(db: sqlite3.Connection, location: Path) -> int:
    """Ensure schema v1 on an open Stage1 connection.

    Called by Portfolios._db, not from Stage1Ledger.state. Existing live
    client processes must be stopped before replacing any underlying SQLite
    file; this function never restores or overwrites the source file.
    """
    with _MIGRATION_LOCK:
        current = _existing_version(db)
        if current > CURRENT_VERSION:
            raise ValueError(f"Stage1 schema v{current} newer than supported v{CURRENT_VERSION}.")
        if current == CURRENT_VERSION:
            return current
        has_data = _has_prior_data(db)
        backup_file, backup_sha = _preflight_backup(db, location) if has_data else (None, None)
        db.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have migrated while this process was preparing its
            # recovery copy; recheck under the SQLite write lock.
            current = _existing_version(db)
            if current > CURRENT_VERSION:
                raise ValueError("Future Stage1 schema encountered during migration.")
            if current == 0:
                _create_v1(db)
                db.execute("""INSERT INTO stage1_schema_registry
                    (id,schema_version,migrated_at_utc,backup_filename,backup_sha256)
                    VALUES (1,1,?,?,?)""",
                    (datetime.now(timezone.utc).isoformat(),backup_file,backup_sha))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        return CURRENT_VERSION


def version_status(db: sqlite3.Connection) -> dict:
    """Read-only schema overview without leaking absolute filesystem paths."""
    version = _existing_version(db)
    if version == 0:
        return {"schema_version": 0, "current_version": CURRENT_VERSION,
                "migration_complete": False, "backup_created": False}
    row = db.execute("""SELECT backup_filename,backup_sha256,migrated_at_utc
                        FROM stage1_schema_registry WHERE id=1""").fetchone()
    return {"schema_version": version, "current_version": CURRENT_VERSION,
            "migration_complete": version == CURRENT_VERSION,
            "backup_created": bool(row[0]), "pre_migration_sha256": row[1],
            "migrated_at_utc": row[2]}
