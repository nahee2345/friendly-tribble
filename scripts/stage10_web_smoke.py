#!/usr/bin/env python3
"""Read-only localhost readiness diagnosis. Does not log in or touch account data."""
import re
import sys
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError, URLError

BASE="http://127.0.0.1:8000"
opener=build_opener(ProxyHandler({}))
failures=[]
def request(path):
    try:
        with opener.open(Request(BASE+path,headers={"Cache-Control":"no-cache","User-Agent":"WealthyLocalSmoke/1.0"}),timeout=5) as resp:
            return resp.status,resp.headers.get("Content-Type",""),resp.read(250000)
    except HTTPError as e:
        return e.code,e.headers.get("Content-Type",""),e.read(2000)
    except (URLError,TimeoutError,OSError) as e:
        failures.append("LOCAL_SERVER_UNREACHABLE")
        return 0,"",b""

print("Wealthy Stage10 HTTP smoke (localhost only, read-only)")
status,ct,html=request("/dashboard")
if status!=200 or b"<html" not in html.lower():
    failures.append("DASHBOARD_HTML_NOT_SERVED")
print("dashboard:",status,ct.split(";")[0])
assets=[]
if status==200:
    decoded=html.decode("utf-8","replace")
    for s in re.findall(r'(?:src|href)="(/assets/[^"]+)"',decoded):
        if s not in assets:assets.append(s)
    if not assets:failures.append("NO_BUILT_JS_OR_CSS_ASSETS")
    for path in assets[:10]:
        n,t,b=request(path)
        valid=n==200 and len(b)>0 and ("javascript" in t or "css" in t or path.endswith((".js",".css")))
        if not valid:failures.append("BAD_FRONTEND_ASSET")
        print("asset:",path.rsplit("/",1)[-1],n,t.split(";")[0])
for path in ("/stage1","/api/v1/stage1/hub","/api/v1/stage1/securities","/api/v1/stage1/screen","/api/v1/stage1/trade-control"):
    n,t,b=request(path)
    if path=="/stage1" and n!=200:failures.append("STAGE1_ROUTE_UNAVAILABLE")
    if path.startswith("/api/") and n not in (401,403):
        failures.append("AUTH_GUARD_OR_ROUTE_UNEXPECTED")
    print(path,":",n,"(401/403 expected without login)" if path.startswith("/api/") else "")
print("RESULT:", "PASS unauthenticated routing and assets" if not failures else "FAIL "+",".join(sorted(set(failures))))
print("Authenticated UI, market feeds, balance and restart persistence require a separate real-session test.")
sys.exit(0 if not failures else 2)
