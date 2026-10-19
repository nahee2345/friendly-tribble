"""Stage10 deterministic offline research tests; no account or HTTP access."""
from datetime import date,timedelta
from wealthy_server.stage10_offline_research import analyze,research_gate,unavailable_card

def series(n=100):
    d=date(2025,1,1)
    return [{"date":str(d+timedelta(days=i)),"close":100+i} for i in range(n)]

def test_valid_analysis_remains_nontrading():
    result=analyze(series(),as_of="2025-04-20")
    assert result["status"]=="ok" and result["trend"]=="up"
    assert result["tradeable"] is False and result["execution_authorized"] is False
    assert research_gate(result,executable_quote_verified=True,approval=True)["tradeable"] is False

def test_future_and_duplicate_rejected():
    assert analyze(series(),as_of="2025-02-01")["status"]=="unavailable"
    rows=series();rows[-1]["date"]=rows[-2]["date"]
    assert analyze(rows)["reason"]=="invalid_or_future_quote"

def test_bad_prices_and_short_series_rejected():
    assert analyze(series(20))["reason"]=="insufficient_history"
    rows=series();rows[20]["close"]=float("nan")
    assert analyze(rows)["status"]=="unavailable"
    rows=series();rows[20]["close"]=-100
    assert analyze(rows)["status"]=="unavailable"

def test_no_quote_fails_closed():
    r=analyze(series())
    assert research_gate(r)["decision"]=="WAIT_FOR_DATA"
    assert research_gate(r,executable_quote_verified=True)["decision"]=="REVIEW_PENDING"

def test_no_fabrication_for_empty_dashboard():
    r=unavailable_card("KOSPI","source unreachable")
    assert r["status"]=="unavailable" and r["items"]==[]
    assert "source unreachable" in r["reason"]
