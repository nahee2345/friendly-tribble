"""A real localhost Wealthy local-runtime app on disposable files only.

This deliberately uses the production single-local-user guard. No live credentials,
broker, market-provider connection, system keyring or customer data are used.
"""
from __future__ import annotations
import os
from pathlib import Path
from wealthy_server.database import Database
from wealthy_server.local_credentials import LocalSecretStore
from wealthy_server.local_runtime import WEB_STATE_FILENAME, build_local_application
from wealthy_server.repositories import AuthRepository
from wealthy_server.security import PasswordManager

class FakeKeyring:
    def __init__(self):
        self.values = {}
    def get_password(self, service, username):
        return self.values.get((service, username))
    def set_password(self, service, username, password):
        self.values[(service, username)] = password
    def delete_password(self, service, username):
        self.values.pop((service, username), None)

work = Path(os.environ["DESIGN_E2E_DATA_DIR"]).resolve()
if not work.is_dir() or not str(work).startswith("/tmp/wealthy-design-e2e-"):
    raise RuntimeError("Only a fresh /tmp/wealthy-design-e2e-* directory is allowed")
db_path = work / WEB_STATE_FILENAME
db = Database.for_local_file(db_path)
db.create_local_schema()
auth = AuthRepository(db.session_factory)
auth.create_user("design_a", PasswordManager().hash("design test account alpha 2026"), is_admin=True)
db.dispose()
app = build_local_application(
    data_dir=work, market_database=work / "market.sqlite3",
    frontend_dir=Path("web/frontend/dist").resolve(),
    secret_store=LocalSecretStore(FakeKeyring(), trusted_test_backend=True),
)

# Model an unexpected second local user only AFTER the actual local app is built.
# This preserves production initialization and tests fail-closed request handling.
if os.environ.get("DESIGN_E2E_TWO_USERS") == "1":
    AuthRepository(app.state.database.session_factory).create_user(
        "design_b", PasswordManager().hash("design test account bravo 2026"),
        is_admin=False,
    )
