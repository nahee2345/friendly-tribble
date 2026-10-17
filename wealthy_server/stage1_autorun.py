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
                   quote_provider=verified_quote, fx_provider=current_fx):
    day = today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    prefs = preferences.get(owner)
    if prefs['us_weight'] < 50 or prefs['etf_weight'] < 50:
        return {'status': 'skipped', 'traded': False,
                'reason': '미국·ETF 선호 비중이 각각 50% 이상이어야 합니다.'}
    state = ledger.state(owner, day)
    accounts = portfolios.list(owner)['accounts']
    default = next((a for a in accounts if a['id'] == 'default'
                    and a['kind'] == 'GENERAL_OVERSEAS'), None)
    if default is None:
        return {'status': 'skipped', 'traded': False,
                'reason': '기본 해외주식 가상계좌가 없습니다.'}
    request_id = f'auto:{day:%Y%m%d}:etf'
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
    cap = Decimal(state['initial_krw']) * Decimal('0.05')
    symbol = None
    price = None
    evidence = None
    for ticker, name, kind, thesis, issuer in CANDIDATES:
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
