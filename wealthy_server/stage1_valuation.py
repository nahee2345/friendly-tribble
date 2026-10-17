"""Read-only marked-to-market report for Stage 1 paper portfolios.

Cash flows (deposits/transfers/FX) do not count as investment profit. A report
is incomplete unless EVERY open position has a validated quote. Prices are
daily reference closes, not broker execution or real-time marks.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo

from .stage1_quotes import verified_quote
from .stage1_research import current_fx


def _dec(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Nonfinite price/amount")
    return number


def portfolio_report(owner, portfolios, ledger, *,
                     quote_provider=verified_quote,
                     fx_provider=current_fx, today=None):
    accounts = portfolios.list(owner)["accounts"]
    cash_flow = ledger.state(owner, today)
    fx = fx_provider()
    fx_ok = fx.get("status") == "ok" and fx.get("executable") is False
    rate = _dec(fx["usd_krw"]) if fx_ok else None
    if rate is not None and not 100 <= rate <= 10000:
        rate = None

    positions = []
    missing = []
    realized_usd = Decimal("0")
    realized_krw = Decimal("0")
    unrealized_usd = Decimal("0")
    unrealized_krw = Decimal("0")
    total_krw = Decimal("0")
    complete = rate is not None
    marked_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    quote_cache = {}

    for account in accounts:
        account_value = _dec(account["krw_cash"])
        usd_cash = _dec(account["usd_cash"])
        if rate is not None:
            account_value += usd_cash * rate
        elif usd_cash != 0:
            complete = False
            missing.append({"account": account["id"], "symbol": "USD/KRW", "reason": "환율 미확인"})

        orders = portfolios.orders(owner, account["id"],limit=100000)["orders"]
        # LIFO timestamp output -> FIFO for cost-basis reconstruction.
        by_symbol = {}
        for order in reversed(orders):
            symbol = order["symbol"]
            currency = order["currency"]
            quantity = int(order["quantity"])
            price = _dec(order["price"])
            key = (symbol, currency)
            position = by_symbol.setdefault(key, {"units": 0, "cost": Decimal("0"), "realized": Decimal("0")})
            if order["side"] == "BUY":
                position["cost"] += price * quantity
                position["units"] += quantity
            elif order["side"] == "SELL":
                if quantity > position["units"]:
                    raise ValueError("Invalid historic sell quantity")
                avg_cost = position["cost"] / position["units"]
                basis = avg_cost * quantity
                position["realized"] += price * quantity - basis
                position["cost"] -= basis
                position["units"] -= quantity
            else:
                raise ValueError("Unknown order side")

        for (symbol, currency), position in by_symbol.items():
            if currency == "USD":
                realized_usd += position["realized"]
            elif currency == "KRW":
                realized_krw += position["realized"]
            else:
                raise ValueError("Unknown denomination")
            if not position["units"]:
                continue
            if symbol not in quote_cache:
                quote_cache[symbol] = quote_provider(symbol)
            quote = quote_cache[symbol]
            if (quote.get("status") != "ok" or quote.get("tradeable") is not True
                    or quote.get("symbol") != symbol or quote.get("currency") != currency):
                complete = False
                missing.append({"account": account["id"], "symbol": symbol, "reason": "유효한 기준 종가 없음"})
                positions.append({"account": account["id"], "symbol": symbol,
                                  "quantity": position["units"], "currency": currency,
                                  "price": None, "value": None, "pnl": None})
                continue
            price = _dec(quote["price"])
            if price <= 0:
                raise ValueError("Invalid quote")
            market_value = price * position["units"]
            pnl = market_value - position["cost"]
            if currency == "USD":
                unrealized_usd += pnl
                if rate is None:
                    complete = False
                else:
                    account_value += market_value * rate
            else:
                unrealized_krw += pnl
                account_value += market_value
            positions.append({
                "account": account["id"], "symbol": symbol, "quantity": position["units"],
                "currency": currency, "price": str(price), "value": str(market_value),
                "pnl": str(pnl), "as_of": quote.get("as_of"),
                "cost_basis": str(position["cost"]),
            })
        total_krw += account_value

    contributions = sum(int(e["krw"]) for e in cash_flow["events"] if e["kind"] == "deposit")
    result = {
        "complete": complete,
        "as_of": marked_at,
        "rate_usd_krw": str(rate) if rate is not None else None,
        "rate_source": fx.get("source"),
        "contributions_krw": contributions,
        "portfolio_value_krw": str(total_krw) if complete else None,
        "return_krw": str(total_krw - Decimal(contributions)) if complete else None,
        "return_pct": str((total_krw / Decimal(contributions) - 1) * 100)
            if complete and contributions > 0 else None,
        "realized_krw": str(realized_krw),
        "realized_usd": str(realized_usd),
        "unrealized_krw": str(unrealized_krw) if complete else None,
        "unrealized_usd": str(unrealized_usd) if complete else None,
        "positions": positions,
        "missing": missing,
        "disclaimer": "지연 종가 가상평가. 미입수 가격은 수익으로 추정하지 않음. 세금·환전수수료·배당은 별도.",
    }
    return result
