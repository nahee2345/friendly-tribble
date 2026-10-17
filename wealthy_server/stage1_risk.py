"""Conservative daily-close paper exit rule for held ETF positions.

Not an intraday stop order. Runs at the *available delayed close* only.
All orders remain simulated; a stale/unverified quote blocks evaluation.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .stage1_quotes import verified_quote

# Fixed conservative thresholds until an audited strategy configuration exists.
STOP_LOSS = Decimal('-0.07')
TAKE_PROFIT = Decimal('0.15')


def paper_exit_review(owner, portfolios, *, today=None, quote_provider=verified_quote):
    day=today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    events=[]
    accounts=portfolios.list(owner)['accounts']
    for account in accounts:
        historical=portfolios.orders(owner,account['id'])['orders']
        for holding in account['holdings']:
            symbol=holding['symbol']
            qty=int(holding['quantity'])
            if qty<=0 or qty>100000: continue
            relevant=list(reversed([o for o in historical if o['symbol']==symbol]))
            units=0
            cost=Decimal('0')
            for o in relevant:
                n=int(o['quantity'])
                p=Decimal(o['price'])
                if o['side']=='BUY':
                    cost+=p*n
                    units+=n
                else:
                    if n>units: raise ValueError('historical negative inventory')
                    cost-=cost*n/units
                    units-=n
            if units!=qty or units==0 or cost<=0:
                events.append({'account':account['id'],'symbol':symbol,
                               'status':'skipped','reason':'매수원가 불일치'})
                continue
            avg=cost/units
            q=quote_provider(symbol)
            kind=q.get('currency')
            expected='USD' if account['kind']=='GENERAL_OVERSEAS' else 'KRW'
            if (q.get('status')!='ok' or q.get('tradeable') is not True
                    or q.get('symbol')!=symbol or kind!=expected):
                events.append({'account':account['id'],'symbol':symbol,
                               'status':'skipped','reason':'기준 종가 미확인'})
                continue
            price=Decimal(q['price'])
            change=price/avg-1
            if change>STOP_LOSS and change<TAKE_PROFIT: continue
            key=f'exit:{day:%Y%m%d}:{symbol}'
            try:
                portfolios.trade(owner,account['id'],symbol,'SELL',qty,key,q)
                events.append({'account':account['id'],'symbol':symbol,
                               'status':'executed','side':'SELL','quantity':qty,
                               'reason':'기준 종가 목표손익 도달','return_pct':str(change*100),
                               'as_of':q.get('as_of')})
            except (ValueError, KeyError, ArithmeticError):
                events.append({'account':account['id'],'symbol':symbol,
                               'status':'skipped','reason':'매도 검증 거부'})
    return {'events':events,'live_broker_connected':False}
