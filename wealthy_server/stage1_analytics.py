"""No-key delayed-price research and transparent historical strategy diagnostics.

This module never executes orders. Daily Stooq CSV is an *unofficial*
research source. Missing or stale data cause an explicit unavailable status.
No invented fundamentals, dividends, forecast return or live broker quotes.
"""
from __future__ import annotations
import csv
import io
import math
import re
import statistics
import urllib.request
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,11}$")
STRATEGIES = ("index", "value", "quality_growth", "growth", "high_dividend",
              "dividend_growth", "momentum", "asset_allocation")


def fetch_daily_history(symbol: str, *, today=None, opener=None) -> dict:
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        return {"status": "unavailable", "reason": "INVALID_SYMBOL", "symbol": symbol}
    now = today or datetime.now(timezone.utc).date()
    beginning = now - timedelta(days=550)
    url = (f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
           f"&d1={beginning:%Y%m%d}&d2={now:%Y%m%d}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "WealthyPaperResearch/1.0"})
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=8) as resp:
            raw = resp.read(500_001)
        if len(raw) > 500_000:
            raise ValueError("oversized")
        rows = []
        for row in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
            day = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            close = Decimal(row["Close"])
            if not close.is_finite() or close <= 0 or close > 1_000_000:
                raise ValueError("bad close")
            if day > now:
                raise ValueError("future data")
            rows.append((day, close))
        rows.sort(key=lambda pair: pair[0])
        if len(rows) < 65 or len({r[0] for r in rows}) != len(rows):
            raise ValueError("too little or duplicate data")
        age = (now - rows[-1][0]).days
        if not 0 <= age <= 5:
            raise ValueError("stale")
        return {"status": "ok", "symbol": symbol, "source": "Stooq delayed daily close",
                "currency": "USD", "prices": [{"date": d.isoformat(), "close": str(v)}
                                             for d, v in rows],
                "as_of": rows[-1][0].isoformat(),
                "notice": "종가 기반 연구용 자료. 배당·세금·수수료·슬리피지 미반영."}
    except (OSError, ValueError, KeyError, UnicodeError, InvalidOperation,
            TimeoutError, csv.Error, TypeError):
        return {"status": "unavailable", "reason": "HISTORY_UNVERIFIED", "symbol": symbol}


def _change(before: Decimal, after: Decimal) -> float:
    return float((after / before - 1) * 100)


def research_summary(history: dict, *, fees_bps: int = 0) -> dict:
    if history.get("status") != "ok":
        return {"status": "unavailable", "reason": history.get("reason", "MISSING_HISTORY")}
    if (not isinstance(fees_bps,int) or isinstance(fees_bps,bool)
            or not 0 <= fees_bps <= 1000):
        return {"status": "unavailable", "reason": "INVALID_FEE_ASSUMPTION"}
    try:
        prices = [Decimal(row["close"]) for row in history["prices"]]
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return {"status": "unavailable", "reason": "INVALID_HISTORY"}
    if len(prices) < 65 or any(not p.is_finite() or p <= 0 for p in prices):
        return {"status": "unavailable", "reason": "INVALID_HISTORY"}
    closes = [float(p) for p in prices]
    daily = [closes[i] / closes[i-1] - 1 for i in range(1, len(closes))]
    vol = statistics.stdev(daily) * math.sqrt(252) * 100
    peak = closes[0]
    drawdowns = []
    for value in closes:
        peak = max(peak, value)
        drawdowns.append((value / peak - 1) * 100)
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    momentum = _change(prices[-min(61,len(prices))], prices[-1])
    trend = "uptrend" if closes[-1] > ma20 > ma60 else "mixed_or_down"
    # One-action-per-day next-close test with no lookahead: signal at t-1
    # is executed at t close. A simple friction assumption is shown explicitly.
    benchmark_start = closes[61]
    benchmark_end = closes[-1]
    benchmark = (benchmark_end / benchmark_start - 1) * 100
    equity, invested, trades = 1.0, False, 0
    for i in range(61, len(closes)):
        signal = closes[i-1] > sum(closes[i-20:i])/20 and (
            sum(closes[i-20:i])/20 > sum(closes[i-60:i])/60)
        # Trading at next close. Position held from previous close earns t return.
        if invested:
            equity *= closes[i]/closes[i-1]
        if signal != invested:
            equity *= 1 - (fees_bps/10000)
            trades += 1
            invested = signal
    backtest = round((equity - 1)*100, 3)
    # Signal taxonomy: unavailable fundamentals are never scored as neutral.
    covered = {"momentum": {"available": True, "signal": trend,
                             "sixteen_week_return_pct": round(momentum, 3)},
               "index": {"available": False, "reason": "지수 편입·구성 검증 자료 없음"},
               "value": {"available": False, "reason": "PER/PBR 재무자료 없음"},
               "quality_growth": {"available": False, "reason": "ROE/재무성장 자료 없음"},
               "growth": {"available": False, "reason": "매출·이익 성장 자료 없음"},
               "high_dividend": {"available": False, "reason": "배당수익률 검증 자료 없음"},
               "dividend_growth": {"available": False, "reason": "배당성장 연혁 자료 없음"},
               "asset_allocation": {"available": False, "reason": "포트폴리오 종목별 상관관계 검증 필요"}}
    return {"status": "ok", "symbol": history.get("symbol"), "as_of": history.get("as_of"),
            "source": history.get("source"), "observations": len(prices),
            "last_close": str(prices[-1]), "currency": "USD",
            "momentum_60_sessions_pct": round(momentum,3),
            "annualized_volatility_pct": round(vol,3),
            "max_drawdown_pct": round(min(drawdowns),3),
            "ma20": round(ma20,4), "ma60": round(ma60,4),
            "trend": trend, "strategies": covered,
            "backtest": {"strategy": "MA20 > MA60 and close > MA20; next-close simulation",
                         "return_pct": backtest, "buy_hold_return_pct": round(benchmark,3),
                         "turnovers": trades, "fee_bps_per_trade": fees_bps,
                         "assumptions": "종가 데이터, 익일 종가 반영. 배당·환율·세금·슬리피지 제외."},
            "investment_recommendation": False,
            "disclaimer": "과거 성과는 미래 수익의 근거가 아닙니다. 자동매매 신호와 분리."}
