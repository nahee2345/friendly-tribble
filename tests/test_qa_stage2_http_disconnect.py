"""QA-only TCP transport failure after isolated verified PAPER writer commit.

The HTTP endpoint exists ONLY in a child test server. It does not expose a
production route or override runtime readiness=false.
"""
from __future__ import annotations
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

from wealthy_server.stage1_portfolios import Portfolios

FIXTURE=Path(__file__).with_name("test_stage2_verified_opening_nav_and_atomic_paper.py")
spec=importlib.util.spec_from_file_location("qa_transport_fixture", FIXTURE)
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

SERVER=r"""
import importlib.util, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_verified_execution import execute_verified_paper
fpath=Path(sys.argv[2])
spec=importlib.util.spec_from_file_location("qa_child_transport_fixture",fpath)
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
mode=sys.argv[3]
dbpath=sys.argv[1]
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path!='/qa-isolated-execute':
            self.send_error(404)
            return
        amount=int(self.headers.get("Content-Length","0"))
        body=json.loads(self.rfile.read(amount))
        assert body=={"request_id":"qa-http-lost-after-commit"}
        value=execute_verified_paper(
            Portfolios(dbpath,require_verified_p0=True),
            owner=fixture.OWNER,account="kr-base",symbol="069500",side="BUY",
            quantity=1,request_id=body["request_id"],quote=fixture.KR,
            session_date=fixture.DAY,now=fixture.NOW)
        if mode=="lost":
            # Actual TCP connection closes after SQLite COMMIT, before headers.
            os._exit(23)
        out=json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(out)))
        self.end_headers()
        self.wfile.write(out)
    def log_message(self,*args):
        pass
server=HTTPServer(("127.0.0.1",0),Handler)
print(server.server_port,flush=True)
server.handle_request()
server.server_close()
"""


def _launch(db,mode):
    process=subprocess.Popen(
        [sys.executable,"-c",SERVER,str(db),str(FIXTURE),mode],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    port_line=process.stdout.readline().strip()
    assert port_line.isdigit(), (
        f"child did not start, status={process.poll()} line={port_line!r}")
    return process,int(port_line)


def _http_request(port):
    body=json.dumps({"request_id":"qa-http-lost-after-commit"}).encode()
    req=urllib.request.Request(
        f"http://127.0.0.1:{port}/qa-isolated-execute",data=body,
        headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=8) as response:
        return response.status,json.loads(response.read())


def test_real_tcp_response_disconnected_after_commit_then_restarted_no_dup(tmp_path):
    p,_,_=fixture.ready(tmp_path)
    first,port=_launch(p.path,"lost")
    try:
        try:
            _http_request(port)
        except (http.client.RemoteDisconnected,ConnectionResetError,EOFError):
            pass
        else:
            raise AssertionError("response should be lost after COMMIT")
        assert first.wait(timeout=8)==23
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=5)
    with p._db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM virtual_orders WHERE owner=?",
            (fixture.OWNER,)).fetchone()[0]==1
    second,newport=_launch(p.path,"replayed")
    try:
        status,result=_http_request(newport)
        assert status==200
        assert result["status"]=="already_executed"
        assert second.wait(timeout=8)==0
    finally:
        if second.poll() is None:
            second.kill()
            second.wait(timeout=5)
    with Portfolios(p.path)._db() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute(
            "SELECT COUNT(*) FROM virtual_orders WHERE owner=?",
            (fixture.OWNER,)).fetchone()[0]==1
        assert db.execute(
            "SELECT COUNT(*) FROM stage1_turnover_claims WHERE owner=?",
            (fixture.OWNER,)).fetchone()[0]==1
