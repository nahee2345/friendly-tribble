from datetime import date,timedelta
from wealthy_server.stage10_backtest import replay

def history(n=100,reverse=False):
    start=date(2025,1,1)
    return [{"date":(start+timedelta(days=i)).isoformat(),
             "close":str(200-i if reverse else 100+i)} for i in range(n)]

def test_uptrend_and_costs():
    no_fee=replay(history(),fee_bps=0,slippage_bps=0)
    fees=replay(history(),fee_bps=10,slippage_bps=5)
    assert no_fee["status"]=="ok" and fees["status"]=="ok"
    assert fees["tradeable"] is False and fees["real_fills"] is False
    assert float(fees["final_equity"])<float(no_fee["final_equity"])
    assert fees["transactions"]>=1
def test_downtrend_keeps_cash():
    r=replay(history(reverse=True))
    assert r["status"]=="ok" and r["transactions"]==0
    assert r["final_equity"]=="1000000.00"
def test_invalid_sequence_and_price():
    rows=history();rows[-1]["date"]=rows[-2]["date"]
    assert replay(rows)["status"]=="unavailable"
    rows=history();rows[70]["close"]="NaN"
    assert replay(rows)["status"]=="unavailable"
def test_short_history_refused():
    assert replay(history(50))["status"]=="unavailable"
def test_bad_costs_refused():
    assert replay(history(),fee_bps=-1)["status"]=="unavailable"
    assert replay(history(),slippage_bps=True)["status"]=="unavailable"
