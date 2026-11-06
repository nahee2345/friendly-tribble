"""Ephemeral auth-enabled Wealthy browser fixture for CI only.

Uses a new temporary SQLite file supplied by DESIGN_E2E_DATA_DIR.
No actual user DB, Secret Service, real brokerage, KIS, or live orders.
"""
from __future__ import annotations

import os
from pathlib import Path
from wealthy_server.app import create_app
from wealthy_server.config import ServerSettings
from wealthy_server.database import Database
from wealthy_server.repositories import AuthRepository
from wealthy_server.security import PasswordManager

work = Path(os.environ["DESIGN_E2E_DATA_DIR"]).resolve()
if not work.is_dir() or not str(work).startswith("/tmp/"):
    raise RuntimeError("Browser E2E data directory must be a separate /tmp directory")
db = Database("sqlite+pysqlite:///" + str(work / "web-state.sqlite3"))
settings = ServerSettings(
    database_url="sqlite+pysqlite:///" + str(work / "web-state.sqlite3"),
    master_key=b"d" * 32,
    secure_cookies=False,
    allowed_hosts=("127.0.0.1", "localhost", "testserver"),
)
app = create_app(settings, database=db, frontend_dir=Path("web/frontend/dist").resolve())
auth = AuthRepository(db.session_factory)
pw = PasswordManager()
for username, password in [
    ("design_a", "design test account alpha 2026"),
    ("design_b", "design test account bravo 2026"),
]:
    auth.create_user(username, pw.hash(password), is_admin=False)
