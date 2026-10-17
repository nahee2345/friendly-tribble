"""User-triggered conservative one-step US ETF simulated purchase.

No real broker access, predictive AI, or unattended scheduler.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_UP

from .stage1_discovery import CANDIDATES
from .stage1_quotes import verified_quote
from .stage1_research import current_fx


def paper_run_once(owner, ledger, portfolios, preferences, *, today=None,
                   quote_provider=verified_quote, fx_provider=current_fx,
                   strategy_weights=None):
    day = today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    prefs = preferences.get(owner)
    if prefs['us_weight'] == 0 or prefs['etf_weight'] == 0:
        return {'status': 'skipped', 'traded': False,
                'reason': '미국시장 또는 ETF 투자 비중이 0%입니다.'}
    state = ledger.state(owner, day)
    accounts = portfolios.list(owner)['accounts']
    default = next((a for a in accounts if a['id'] == 'default'
                    and a['kind'] == 'GENERAL_OVERSEAS'), None)
    if default is None:
        return {'status': 'skipped', 'traded': False,
                'reason': '기본 해외주식 가상계좌가 없습니다.'}
    request_id = f'auto:{day:%Y%m%d}:etf'
    if any(o['side']=='SELL' and o['id'].startswith(f'exit:{day:%Y%m%d}:')
           for o in portfolios.orders(owner,'default')['orders']):
        return {'status':'skipped','traded':False,
                'reason':'오늘 매도된 계좌는 당일 재매수를 하지 않습니다.'}
    if any(o['id'] == request_id for o in portfolios.orders(owner, 'default')['orders']):
        return {'status': 'already_executed', 'traded': False,
                'reason': '오늘 가상 ETF 주문이 이미 기록되었습니다.'}
    fx = fx_provider()
    if fx.get('status') != 'ok' or fx.get('executable') is not False:
        return {'status': 'skipped', 'traded': False,
                'reason': '검증된 참고 환율이 없습니다.'}
    try:
        rate = Decimal(str(fx['usd_krw']))
        if not rate.is_finite() or not 100 <= rate <= 10000:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return {'status': 'skipped', 'traded': False, 'reason': '환율 검증 실패'}
    # Reserve a minimum portion of this virtual account's actual cash.
    # The retained amount is not available for another entry; no leverage.
    from .stage1_decisions import reserve_ratio
    weights_for_reserve = strategy_weights if strategy_weights is not None else {
        'index':30, 'momentum':10}
    reserve = Decimal(reserve_ratio(weights_for_reserve)) / Decimal(100)
    available_krw = Decimal(default['krw_cash'])
    available_usd = Decimal(default['usd_cash'])
    gross_cash_value = available_krw + (available_usd * rate)
    spendable = gross_cash_value * (Decimal(1) - reserve)
    cap = min(Decimal(state['initial_krw']) * Decimal('0.05'), spendable)
    symbol = None
    price = None
    evidence = None
    # Selection uses policy category weights where available, not an assumed
    # forecast of which ETF will make the most money. Invalid policy fails closed.
    ordered_candidates = list(CANDIDATES)
    if strategy_weights is not None:
        from .stage1_strategy_policy import DEFAULT_WEIGHTS
        if (not isinstance(strategy_weights, dict) or
            set(strategy_weights) != set(DEFAULT_WEIGHTS) or
            any(type(v) is not int or not 0<=v<=100 for v in strategy_weights.values()) or
            sum(strategy_weights.values()) != 100):
            return {'status':'skipped','traded':False,'reason':'전략 비중의 무결성 검증 실패'}
        tags = {
            'VOO':('index','asset_allocation'), 'VTI':('index','asset_allocation'),
            'QQQM':('growth','momentum'), 'SCHD':('high_dividend','dividend_growth'),
            'VXUS':('asset_allocation',)
        }
        ordered_candidates.sort(key=lambda c:
            -sum(strategy_weights.get(t,0) for t in tags.get(c[0],())))
    for ticker, name, kind, thesis, issuer in ordered_candidates:
        if kind != 'ETF':
            continue
        quote = quote_provider(ticker)
        if (quote.get('tradeable') is not True or quote.get('status') != 'ok'
                or quote.get('symbol') != ticker or quote.get('currency') != 'USD'):
            continue
        try:
            p = Decimal(str(quote['price']))
        except (ValueError, KeyError, TypeError):
            continue
        if not p.is_finite() or p <= 0 or p * rate > cap:
            continue
        symbol, price = ticker, p
        evidence = {'issuer': issuer, 'reason': thesis, 'quote': quote, 'fx': fx}
        break
    if symbol is None:
        return {'status': 'skipped', 'traded': False,
                'reason': '한도 내에서 시세가 검증된 ETF가 없습니다.'}
    try:
        result = portfolios.auto_buy_usd_with_fx(
            owner, symbol, evidence['quote'], str(rate), request_id)
    except (ValueError, KeyError, ArithmeticError):
        return {'status': 'skipped', 'traded': False,
                'reason': '잔액·시세·원장 검증으로 주문이 거부되었습니다.'}
    if result['status'] == 'already_executed':
        return {'status': 'already_executed', 'traded': False,
                'reason': '동일 날짜 주문이 이미 처리됐습니다.'}
    return {'status': 'executed', 'traded': True, 'symbol': symbol,
            'quantity': 1, 'price_usd': str(price),
            'reference_fx': str(rate), 'evidence': evidence,
            'accounts': result['accounts'],
            'notice': '일간 종가 기준 가상 체결. 예측형 AI 투자 추천은 아닙니다.'}
