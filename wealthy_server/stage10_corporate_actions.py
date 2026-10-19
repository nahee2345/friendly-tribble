"""Read-only preview of verified split and dividend evidence; never posts cash or shares."""
from datetime import date
from decimal import Decimal, InvalidOperation

def _decimal(value):
    try:
        if isinstance(value, bool):
            return None
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except (ValueError, TypeError, InvalidOperation):
        return None

def preview(symbol, quantity, events, *, currency, as_of):
    if not isinstance(symbol, str) or not symbol or not isinstance(events, (list, tuple)) or len(events) > 1000:
        raise ValueError('invalid_symbol_or_events')
    if currency not in ('USD', 'KRW'):
        raise ValueError('invalid_currency')
    cutoff = date.fromisoformat(as_of)
    initial = _decimal(quantity)
    if initial is None:
        raise ValueError('invalid_quantity')
    seen = set()
    rows = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('invalid_event')
        event_id = event.get('event_id')
        kind = event.get('kind')
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128 or event_id in seen:
            raise ValueError('duplicate_or_invalid_event_id')
        seen.add(event_id)
        if event.get('symbol') != symbol or event.get('currency') != currency:
            raise ValueError('security_mismatch')
        day = date.fromisoformat(event['effective_date'])
        if day > cutoff:
            raise ValueError('future_event')
        amount = _decimal(event.get('amount'))
        if kind not in ('SPLIT', 'DIVIDEND') or amount is None:
            raise ValueError('invalid_corporate_action')
        if kind == 'SPLIT' and amount > 1000:
            raise ValueError('implausible_split')
        if kind == 'DIVIDEND' and amount > 1000000:
            raise ValueError('implausible_dividend')
        if not isinstance(event.get('source_url'), str) or not event['source_url'].startswith('https://'):
            raise ValueError('missing_source')
        rows.append((day, event_id, kind, amount))
    rows.sort(key=lambda x: (x[0], x[1]))
    shares = initial
    potential_dividends = Decimal(0)
    for _, _, kind, amount in rows:
        if kind == 'SPLIT':
            shares *= amount
        else:
            potential_dividends += shares * amount
    return {'status': 'preview_only', 'symbol': symbol, 'currency': currency,
            'initial_quantity': str(initial), 'potential_quantity': str(shares),
            'potential_gross_dividends': str(potential_dividends),
            'events_reviewed': len(rows), 'verified_payment': False,
            'cash_or_holdings_modified': False,
            'warning': '배당락일과 분할 정보만으로 실제 지급액 및 정산을 확정하지 않습니다.'}
