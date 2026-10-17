"""Conservative domestic ETF simulated trading, never real brokerage access.

Daily reference closes can be stale or absent. The only automated entries are
known Korean-listed ETFs on a short audited allowlist. Fail closed otherwise.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo

from .stage1_quotes import verified_quote

CHOICES = (
    ('kr-base', '069500', 'KODEX 200', '국내 대형주 지수 ETF'),
    ('isa-base', '360750', 'TIGER 미국S&P500', '국내 상장 미국 대형주 지수 ETF; ISA 적격성은 계좌 조건에 따라 확인 필요'),
)


def paper_run_domestic(owner, portfolios, *, today=None, quote_provider=verified_quote,
                       preference=None):
    day = today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    results = []
    account_map = {row['id']: row for row in portfolios.list(owner)['accounts']}
    if preference is not None and (preference.get('kr_weight', 0) == 0
                                   or preference.get('etf_weight', 0) == 0):
        return {'status':'skipped','results':[],
                'reason':'한국시장 또는 ETF 투자 비율이 0%로 지정됨',
                'live_broker_connected':False}
    for account_id, symbol, name, thesis in CHOICES:
        account = account_map.get(account_id)
        if not account:
            results.append({'account':account_id, 'status':'skipped', 'reason':'가상계좌 미존재'})
            continue
        key = f'auto:{day:%Y%m%d}:{account_id}'
        # A stable idempotency key prevents more than one decision/day/account.
        orders = portfolios.orders(owner, account_id)['orders']
        if any(o['side']=='SELL' and o['id'].startswith(f'exit:{day:%Y%m%d}:')
               for o in orders):
            results.append({'account':account_id,'status':'skipped','reason':'금일 매도 후 재매수 제한'})
            continue
        if any(item['id'] == key for item in orders):
            results.append({'account':account_id, 'status':'already_executed','symbol':symbol})
            continue
        q = quote_provider(symbol)
        if (q.get('symbol') != symbol or q.get('status') != 'ok'
            or q.get('tradeable') is not True or q.get('currency') != 'KRW'):
            results.append({'account':account_id,'status':'skipped',
                            'symbol':symbol,'reason':'국내 ETF 종가 미확인'})
            continue
        try:
            price = Decimal(str(q['price']))
            if not price.is_finite() or price <= 0:
                raise ValueError
            holding = next((int(h['quantity']) for h in account['holdings']
                            if h['symbol'] == symbol), 0)
            cash = Decimal(str(account['krw_cash']))
            # No leverage. One share daily, only if within 20% of account
            # starting allocation and sufficient actual virtual cash.
            limit = Decimal('80000')
            if price <= min(cash, limit) and (holding + 1) * price <= Decimal('200000'):
                portfolios.trade(owner, account_id, symbol, 'BUY', 1, key, q)
                results.append({'account':account_id,'status':'executed','side':'BUY',
                    'symbol':symbol,'quantity':1,'price':str(price),
                    'as_of':q['as_of'],'thesis':thesis,'source':q['source']})
            else:
                results.append({'account':account_id,'status':'skipped',
                   'symbol':symbol,'reason':'현금 부족 또는 계좌별 보유평가액 20만원 한도'})
        except (ValueError, InvalidOperation, KeyError, ArithmeticError):
            results.append({'account':account_id,'status':'skipped',
                            'symbol':symbol,'reason':'주문 검증 실패'})
    return {'status':'completed','results':results, 'live_broker_connected':False}
