"""Offline deterministic tests for no-key historical research."""
from datetime import date, timedelta
from decimal import Decimal

from wealthy_server.stage1_analytics import research_summary, fetch_daily_history


def make_history(n=140):
    start = date(2026, 4, 1)
    return {"status": "ok", "symbol": "VOO", "as_of": (start + timedelta(days=n-1)).isoformat(),
            "source": "test fixture", "prices": [
                {"date": (start + timedelta(days=i)).isoformat(),
                 "close": str(Decimal("100") + Decimal(i) / 10)}
                for i in range(n)]}


def test_research_risk_metrics_and_backtest():
    result = research_summary(make_history(), fees_bps=10)
    assert result["status"] == "ok"
    assert result["observations"] == 140
    assert result["annualized_volatility_pct"] >= 0
    assert result["max_drawdown_pct"] <= 0
    assert result["backtest"]["turnovers"] >= 0
    assert result["strategies"]["momentum"]["available"]
    assert not result["strategies"]["value"]["available"]
    assert result["investment_recommendation"] is False


def test_history_missing_or_corrupt_returns_unavailable():
    assert research_summary({"status": "unavailable"})["status"] == "unavailable"
    bad = make_history()
    bad["prices"][3]["close"] = "NaN"
    assert research_summary(bad)["status"] == "unavailable"


def test_history_invalid_symbols_are_not_fetched():
    assert fetch_daily_history("../../etc/passwd")["status"] == "unavailable"
    assert fetch_daily_history("005930")["status"] == "unavailable"
