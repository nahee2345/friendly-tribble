"""QA PR18: FastAPI -> persisted PAPER SQLite integration on disposable files.

Never uses an Ubuntu operating DB, real broker, external quotes or secrets.
All expected fills are test-only simulated PAPER ledger rows.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import sqlite3

import pytest
from fastapi.testclient import TestClient

from wealthy_server.app import CSRF_COOKIE
from wealthy_server.database import Database
from wealthy_server.local_credentials import LocalSecretStore
from wealthy_server.local_runtime import WEB_STATE_FILENAME, build_local_application
from wealthy_server.repositories import AuthRepository
from wealthy_server.security import PasswordManager
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_controls import set_paused
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_bootstrap import initialize_paper_accounts


class FakeKeyring:
    def __init__(self):
        self.values={}
    def get_password(self,service,username):
        return self.values.get((service,username))
    def set_password(self,service,username,password):
        self.values[(service,username)]=password
    def delete_password(self,service,username):
        self.values.pop((service,username),None)


@pytest.fixture
def local_api(tmp_path,monkeypatch):
    root=tmp_path/"secure-test-state"
    root.mkdir(mode=0o700)
    frontend=tmp_path/"built-frontend"
    frontend.mkdir()
    (frontend/"index.html").write_text("<html><body>fixture</body></html>")
    database=Database.for_local_file(root/WEB_STATE_FILENAME)
    database.create_local_schema()
    pwd="fixture-only-long-password-12345"
    user=AuthRepository(database.session_factory).create_user(
        "qa-local",PasswordManager().hash(pwd),is_admin=True)
    database.dispose()
    secrets=LocalSecretStore(FakeKeyring(),trusted_test_backend=True)
    market=tmp_path/"fixture-market.sqlite3"
    app=build_local_application(data_dir=root,market_database=market,
                                frontend_dir=frontend,secret_store=secrets)
    client=TestClient(app,base_url="http://localhost")
    login=client.post("/api/v1/login",headers={"Origin":"http://localhost"},
                      json={"username":"qa-local","password":pwd})
    assert login.status_code==200,login.text
    def post(path,payload=None):
        headers={"Origin":"http://localhost",
                 "X-CSRF-Token":client.cookies.get(CSRF_COOKIE)}
        return client.post(path,headers=headers,json=payload or {})
    paper=Portfolios(root/"stage1-paper.sqlite3")
    # No broker, no provider fetch. Network quotes are monkeypatched in tests.
    quote={"symbol":"069500","currency":"KRW","price":"310000",
           "status":"ok","tradeable":True,"source":"offline QA",
           "as_of":"2026-10-25"}
    monkeypatch.setattr("wealthy_server.stage1_quotes.verified_quote",lambda symbol:quote)
    yield client,post,paper,app,root,secrets,frontend,market,quote,user.id
    client.close()
    app.state.database.dispose()


def funds(post,paper,owner):
    initialize_paper_accounts(owner,Stage1Ledger(paper.path),paper,today=date(2026,10,25))
    from wealthy_server.stage1_controls import set_paused
    set_paused(owner,paper,False,"QA fixture paper only")
    accounts=paper.list(owner)["accounts"]
    # User PAPER initial allocation may be split; transfer more from default
    # only when available. Never fabricate deposits after app initialization.
    kr=next((a for a in accounts if a["id"]=="kr-base"),None)
    assert kr is not None
    if float(kr["krw_cash"])<310000:
        movement=post("/api/v1/stage1/transfer",{"source":"default",
          "destination":"kr-base","amount_krw":600000,
          "request_id":"qa-transfer-init-0001"})
        assert movement.status_code==200,movement.text


def approval_order(post,request_id="qa-approve-order-0001"):
    return post("/api/v1/stage1/order",{"portfolio":"kr-base",
        "symbol":"069500","side":"BUY","quantity":1,"request_id":request_id})


def test_authenticated_approval_then_one_actual_sqlite_fill(local_api):
    client,post,paper,app,root,secrets,frontend,market,quote,owner=local_api
    funds(post,paper,owner)
    before=paper.orders(owner,"kr-base")["orders"]
    assert before==[]
    initial=approval_order(post)
    assert initial.status_code==200,initial.text
    assert initial.json()["status"]=="approval_pending"
    assert paper.orders(owner,"kr-base")["orders"]==[]
    decision=post("/api/v1/stage1/approvals/decide",
                  {"request_id":"qa-approve-order-0001","approve":True})
    assert decision.status_code==200,decision.text
    assert decision.json()["items"][0]["status"]=="APPROVED"
    sent=approval_order(post)
    assert sent.status_code==200,sent.text
    orders=client.get("/api/v1/stage1/portfolios/kr-base/orders")
    assert orders.status_code==200,orders.text
    matches=[x for x in orders.json()["orders"]
             if x["id"]=="qa-approve-order-0001"]
    assert len(matches)==1
    assert (matches[0]["symbol"],matches[0]["side"],matches[0]["quantity"])==(
        "069500","BUY",1)
    again=approval_order(post)
    assert again.status_code==200,again.text
    assert again.json()["status"]=="already_executed"
    assert len(paper.orders(owner,"kr-base")["orders"])==1
    with sqlite3.connect(paper.path) as db:
        assert db.execute("SELECT COUNT(*) FROM virtual_orders WHERE owner=?",
                          (owner,)).fetchone()[0]==1


def test_rejected_and_expired_approval_never_fill(local_api):
    _client,post,paper,_app,_root,_sec,_front,_market,_quote,owner=local_api
    funds(post,paper,owner)
    for key,approve in (("qa-order-rejected-001",False),
                        ("qa-order-expired-001",True)):
        assert approval_order(post,key).json()["status"]=="approval_pending"
        assert post("/api/v1/stage1/approvals/decide",
                    {"request_id":key,"approve":approve}).status_code==200
        if approve:
            with sqlite3.connect(paper.path) as db:
                db.execute("""UPDATE stage1_approvals SET expires_at=?
                    WHERE owner=? AND request_id=?""",
                    ("2020-01-01T00:00:00+00:00",owner,key))
                db.commit()
        denied=approval_order(post,key)
        assert denied.status_code==200,denied.text
        assert denied.json()["status"]=="approval_required"
        assert paper.orders(owner,"kr-base")["orders"]==[]


def test_emergency_stop_blocks_api_then_restarts_without_duplicate(local_api):
    client,post,paper,app,root,secrets,front,market,quote,owner=local_api
    funds(post,paper,owner)
    key="qa-order-restart-0001"
    assert approval_order(post,key).json()["status"]=="approval_pending"
    assert post("/api/v1/stage1/approvals/decide",
                {"request_id":key,"approve":True}).status_code==200
    set_paused(owner,paper,True,"qa emergency stop")
    stopped=approval_order(post,key)
    assert stopped.status_code==409,stopped.text
    assert paper.orders(owner,"kr-base")["orders"]==[]
    set_paused(owner,paper,False,"qa restart")
    result=approval_order(post,key)
    assert result.status_code==200,result.text
    assert len(paper.orders(owner,"kr-base")["orders"])==1
    app.state.database.dispose()
    client.close()
    reopened=build_local_application(data_dir=root,market_database=market,
                                     frontend_dir=front,secret_store=secrets)
    try:
        assert len(Portfolios(paper.path).orders(owner,"kr-base")["orders"])==1
    finally:
        reopened.state.database.dispose()


def test_same_id_parallel_direct_ledger_commit_creates_only_one_row(local_api):
    _client,_post,paper,_app,_root,_sec,_front,_market,quote,owner=local_api
    funds(_post,paper,owner)
    request="qa-parallel-order-001"
    def attempt(_):
        return paper.trade(owner,"kr-base","069500","BUY",1,request,quote)
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(attempt,range(3)))
    assert len(paper.orders(owner,"kr-base")["orders"])==1
