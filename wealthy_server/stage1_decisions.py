"""Explainable paper-investment proposal; no real-order routing.

All suggested actions are hypotheses, not certain-profit predictions.
Only verified delayed daily closes may enter an executable proposal.
Unverified fundamental/news factors explicitly remain unscored.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo

from .stage1_discovery import CANDIDATES
from .stage1_domestic import CHOICES
from .stage1_quotes import verified_quote
from .stage1_research import current_fx

# Manually characterized exposures, not financial signals. Codes are maintained.
CATALOG = (
    ("VOO", "Vanguard S&P500", "US", "ETF", ("index","asset_allocation"), "미국 대형주"),
    ("VTI", "Vanguard Total Market", "US", "ETF", ("index","asset_allocation"), "미국 주식시장 전체"),
    ("QQQM", "Invesco Nasdaq 100", "US", "ETF", ("growth","momentum"), "미국 대형 성장주"),
    ("SCHD", "Schwab Dividend", "US", "ETF", ("high_dividend","dividend_growth"), "미국 배당주"),
    ("VXUS", "Vanguard ex-US", "US", "ETF", ("asset_allocation",), "미국 외 주식"),
    ("AAPL", "Apple", "US", "STOCK", ("quality_growth","growth"), "정보기술·소비재"),
    ("MSFT", "Microsoft", "US", "STOCK", ("quality_growth","growth"), "소프트웨어·클라우드"),
    ("069500", "KODEX 200", "KR", "ETF", ("index","asset_allocation"), "국내 대형주"),
    ("360750", "TIGER 미국S&P500", "KR", "ETF", ("index","asset_allocation"), "국내 상장 미국주식 ETF"),
)
# Only a classification proxy. Financial ratios, payout and valuation are unknown.
THRESHOLD = Decimal("0.08")


def _valid_price(quote, code, currency):
    if (quote.get("status") != "ok" or quote.get("tradeable") is not True
            or quote.get("symbol") != code or quote.get("currency") != currency
            or not quote.get("as_of") or not quote.get("source")):
        return None
    try:
        p = Decimal(str(quote["price"]))
        if not p.is_finite() or p <= 0 or p.as_tuple().exponent < -4:
            return None
        return p
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None


def reserve_ratio(weights):
    """Deterministic conservative cash-buffer heuristic, not a forecast."""
    if not isinstance(weights, dict) or not {'momentum','index'} <= set(weights):
        raise ValueError("invalid strategy policy")
    return min(40, max(15, 25 + weights["momentum"] // 10 - weights["index"] // 10))


def proposal(owner, portfolios, preference_store, strategy_store, *,
             quote_provider=verified_quote, fx_provider=current_fx):
    preference = preference_store.get(owner)
    weights = strategy_store.get(owner)["weights"]
    accounts = {a["id"]: a for a in portfolios.list(owner)["accounts"]}
    fx = fx_provider()
    try:
        rate = Decimal(str(fx["usd_krw"]))
        if fx.get("status") != "ok" or fx.get("executable") is not False or not 100 <= rate <= 10000:
            raise ValueError
    except (KeyError, ValueError, TypeError, InvalidOperation):
        rate = None
    # Cash reserve is a conservative, deterministic *suggestion* from policy,
    # not an economic forecast or assured optimal allocation.
    reserve_pct = reserve_ratio(weights)
    target_krw = sum(Decimal(a["krw_cash"]) for a in accounts.values())
    if rate is not None:
        target_krw += sum(Decimal(a["usd_cash"]) * rate for a in accounts.values())
    positions = []
    for account in accounts.values():
        for h in account["holdings"]:
            positions.append({"account":account["id"], "symbol":h["symbol"],
                              "quantity":h["quantity"]})
    allowed_isas = {"069500", "360750"}
    def evaluate(item):
        code, name, region, kind, tags, exposure = item
        quote = quote_provider(code)
        currency = "USD" if region == "US" else "KRW"
        price = _valid_price(quote, code, currency)
        if region == "US":
            account_id = "default"
        elif code in allowed_isas and region == "KR" and kind == "ETF":
            # ISA preferred for eligible KR-listed ETFs; if sufficient space exists.
            account_id = "isa-base" if preference["etf_weight"] >= 50 else "kr-base"
        else:
            account_id = "kr-base"
        market_weight = preference["us_weight"] if region == "US" else preference["kr_weight"]
        class_weight = preference["etf_weight"] if kind == "ETF" else preference["stock_weight"]
        # Strategy *category alignment* only. Do NOT equate dividend tags with
        # actual yields or growth tags with verified fundamentals.
        alignment = sum(weights[t] for t in tags)
        priority = round(market_weight * class_weight * (1 + alignment / 100) / 100, 2)
        has_position = any(h["symbol"] == code and h["account"] == account_id for h in positions)
        cash = accounts.get(account_id)
        cash_available = Decimal(cash["krw_cash"]) if cash else Decimal(0)
        if currency == "USD" and cash:
            cash_available += Decimal(cash["usd_cash"]) * rate if rate is not None else Decimal(0)
        max_budget_krw = min(cash_available, target_krw * THRESHOLD)
        quantity = int(max_budget_krw // (price * rate if currency == "USD" and rate is not None else price)) if price is not None and (currency == "KRW" or rate is not None) else 0
        quantity = min(quantity, 1) # cap per-candidate first review; not a portfolio optimization
        status = ("검토 가능" if price is not None and quantity > 0 and market_weight > 0 and
                  class_weight > 0 and cash is not None else "보류")
        return {
            "symbol":code, "name":name, "region":region, "kind":kind,
            "account":account_id, "exposure":exposure,
            "priority_score":priority, "status":status, "quantity_suggestion": quantity if status=="검토 가능" else 0,
            "price":str(price) if price is not None else None, "currency":currency,
            "as_of":quote.get("as_of"), "source":quote.get("source"),
            "holding":has_position,
            "decision": "보유 관찰" if has_position else ("매수 검토" if status=="검토 가능" else "현금 유지"),
            "holding_horizon": "중장기 6~24개월 검토" if kind=="ETF" else "3~12개월 정기 재검토",
            "exit_review": "월 1회 비중 점검, 가격 하락 -7% 또는 상승 +15% 시 가상 매도 재검토",
            "why": [
                f"설정된 {region} 시장 목표비중 {market_weight}%·{kind} 목표비중 {class_weight}%를 반영",
                f"적용된 분류 전략: {', '.join(tags)} (합계 {alignment}%). 과거 재무성과 검증 점수가 아님",
                f"노출 시장: {exposure}",
            ],
            "risks": ["지연 종가와 실제 체결가 차이", "가격 하락과 변동성",
                      "배당·PER·ROE·개별 기업 전망은 추가 자료 검증 필요"],
        }
    with ThreadPoolExecutor(max_workers=4) as pool:
        items = list(pool.map(evaluate, CATALOG))
    items.sort(key=lambda x:(x["status"]!="검토 가능", -x["priority_score"], x["symbol"]))
    return {
        "status":"ok", "generated_kst":datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "mode":"SIMULATED_RESEARCH", "account_routing":"미국상장 주식→미국계좌, 국내 ETF→ISA 우선",
        "reserve_percent":reserve_pct, "reserve_target_krw":str(target_krw * reserve_pct / 100),
        "portfolio_cash_krw_equivalent":str(target_krw) if rate is not None else None,
        "fx_valid":rate is not None,
        "strategy_weights":weights,"market_weights":{"US":preference["us_weight"],"KR":preference["kr_weight"]},
        "etf_weight":preference["etf_weight"],"recommendations":items,
        "coverage_limitations":["전략 연관도는 사용자가 정한 비중과 종목 유형 분류만 사용",
                              "실제 수익 예측이나 최적 종목 확정 아님",
                              "재무제표·배당·기업가치 및 신뢰 가능한 뉴스 영향 검증 전에는 해당 요소 평가하지 않음",
                              "환율이 검증되지 않으면 달러환산 매수수량 제시 안 함"],
        "no_live_orders":True,
    }
