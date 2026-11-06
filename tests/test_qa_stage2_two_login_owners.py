"""QA-only two-user authenticated HTTP isolation on an ephemeral local app."""
from fastapi.testclient import TestClient

from wealthy_server.repositories import AuthRepository
from wealthy_server.security import PasswordManager
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_paper import Stage1Ledger

from test_qa_p0_api_ledger import local_api


def test_two_real_login_sessions_cannot_read_each_others_paper_orders(local_api):
    first_client, _, paper, app, _, _, _, _, _, owner_a = local_api
    initialize_paper_accounts(
        owner_a, Stage1Ledger(paper.path), paper,
        today=__import__("datetime").date(2026, 10, 25))
    key = "qa-first-user-unique-fill"
    with paper._db() as db:
        db.execute(
            "INSERT INTO virtual_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (owner_a, "kr-base", key, "069500", "BUY", 1, "10000",
             "KRW", "qa-synthetic-only", "2026-10-25",
             "2026-10-25T10:00:00+00:00"))
    first_view = first_client.get(
        "/api/v1/stage1/portfolios/kr-base/orders")
    assert first_view.status_code == 200, first_view.text
    assert key in first_view.text

    password_b = "qa-second-user-isolated-password-123"
    user_b = AuthRepository(app.state.database.session_factory).create_user(
        "qa-isolated-second", PasswordManager().hash(password_b),
        is_admin=False)
    second_client = TestClient(app, base_url="http://localhost")
    try:
        response = second_client.post(
            "/api/v1/login", headers={"Origin": "http://localhost"},
            json={"username": "qa-isolated-second",
                  "password": password_b})
        assert response.status_code == 200, response.text
        second_view = second_client.get(
            "/api/v1/stage1/portfolios/kr-base/orders")
        assert second_view.status_code in (200, 404), second_view.text
        assert key not in second_view.text
        with paper._db() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM virtual_orders WHERE owner=?",
                (user_b.id,)).fetchone()[0] == 0
        # Logging in as B does not change A's independent cookies or scope.
        again_a = first_client.get(
            "/api/v1/stage1/portfolios/kr-base/orders")
        assert again_a.status_code == 200, again_a.text
        assert key in again_a.text
    finally:
        second_client.close()
