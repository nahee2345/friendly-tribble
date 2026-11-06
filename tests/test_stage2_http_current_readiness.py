"""P0 local authenticated HTTP checks under the immutable release denial.

Historical PR18 tests expected approved PAPER fills before owner NAV proofs
existed. This new test instead proves current production gate does not write
anything, including after an approval has been created. No provider network.
"""
from fastapi.testclient import TestClient

from test_qa_p0_api_ledger import local_api, funds


def _mutations(paper,owner):
    with paper._db() as db:
        tables = ["virtual_orders","stage1_turnover_claims",
                  "stage1_trade_metadata","stage4_execution_claims"]
        result = {}
        for table in tables:
            found=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (table,)).fetchone()
            result[table]=db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE owner=?",(owner,)).fetchone()[0] if found else 0
        result["fx_events"]=db.execute("""SELECT COUNT(*) FROM stage1_events
                     WHERE owner=? AND kind='virtual_fx'""",(owner,)).fetchone()[0]
        return result


def test_authenticated_http_order_and_opening_nav_fail_closed_without_evidence(local_api):
    client,post,paper,app,root,secrets,frontend,market,quote,owner=local_api
    funds(post,paper,owner)
    before=_mutations(paper,owner)
    order = {"portfolio":"kr-base","symbol":"069500","side":"BUY",
             "quantity":1,"request_id":"http-unverified-001"}
    denied=post("/api/v1/stage1/order",order)
    assert denied.status_code==409,denied.text
    assert denied.json()["error"]["code"]=="UNVERIFIED_SESSION_OPENING_NAV"
    verify=post("/api/v1/stage1/opening-nav/verify",{})
    assert verify.status_code==409,verify.text
    readiness=client.get("/api/v1/stage1/paper-execution-readiness")
    assert readiness.status_code==200
    assert readiness.json()["verified"] is False
    assert _mutations(paper,owner)==before

    no_csrf=client.post("/api/v1/stage1/order",
                        headers={"Origin":"http://localhost"},json=order)
    assert no_csrf.status_code==403
    anonymous=TestClient(app,base_url="http://localhost")
    try:
        forbidden=anonymous.post("/api/v1/stage1/order",
            headers={"Origin":"http://localhost"},json=order)
        assert forbidden.status_code==401
    finally:
        anonymous.close()
    assert _mutations(paper,owner)==before
