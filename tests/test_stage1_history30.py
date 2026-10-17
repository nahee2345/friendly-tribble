from datetime import datetime,timezone,timedelta
from io import BytesIO
import json
from urllib.parse import unquote

from wealthy_server.stage1_history30 import last_30_days

class FakeResponse:
    def __init__(self, data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self,size):return self.data

def provider(symbol, currency, *,now):
    ts=[int((now-timedelta(days=20)).timestamp()),int((now-timedelta(days=1)).timestamp())]
    data={"chart":{"result":[{"meta":{"symbol":symbol,"currency":currency},
        "timestamp":ts,"indicators":{"quote":[{"close":[100,110]}]}}]}}
    return lambda request,timeout:FakeResponse(json.dumps(data).encode())

def test_us_recent_close_is_factual():
    now=datetime(2026,10,17,tzinfo=timezone.utc)
    result=last_30_days("US","AAPL",now=now,opener=provider("AAPL","USD",now=now))
    assert result["status"]=="ok"
    assert result["change_pct"]==10
    assert len(result["points"])==2

def test_wrong_symbol_or_currency_fails_closed():
    now=datetime(2026,10,17,tzinfo=timezone.utc)
    result=last_30_days("US","AAPL",now=now,opener=provider("MSFT","USD",now=now))
    assert result["status"]=="unavailable"
    kr=last_30_days("KR","005930",now=now,opener=provider("005930.KS","KRW",now=now))
    assert kr["status"]=="ok" and kr["market"]=="KR"

def test_no_arbitrary_url_or_missing_prices():
    assert last_30_days("US","../../secret")["status"]=="unavailable"
