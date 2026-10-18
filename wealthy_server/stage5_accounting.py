"""Stage 5: read-only historical-cost paper accounting and NAV performance.

Sources: owner-scoped immutable simulated fills, funding/FX events and SHA-
verified market-cache references. This does not modify cash, settle a dividend,
book a fee, claim actual tax paid, or modify Stage4 trading authorizations.

Returns:
- native-currency realized/unrealized *GROSS* P/L with average cost;
- USD stock-price vs FX holding gain ONLY when acquisition rate was actually
  captured at each buy (and sale FX at each sell);
- annualized XIRR from actual dated deposits only if span >= seven days;
- true flow-free chain linked snapshot returns; TWR is UNAVAILABLE across
  external contributions lacking a pre/post-valuation boundary;
- candidate dividend entitlements, not confirmed payable cash.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from zoneinfo import ZoneInfo

from .stage1_market_store import MarketStore
from .stage5_tax import research_comparison,foreign_ordinary_scenario,domestic_isa_scenario

ENGINE="stage5-accounting-v1"
KST=ZoneInfo("Asia/Seoul")
NY=ZoneInfo("America/New_York")
FEE_RATE_ASSUMED=Decimal("0.001")
SECONDS_PER_DAY=86400


def D(raw,*,max_abs=Decimal("100000000000000")):
    if raw is None or isinstance(raw,bool):
        raise ValueError("missing accounting amount")
    try:n=Decimal(str(raw))
    except (TypeError,InvalidOperation,ValueError):
        raise ValueError("bad accounting amount") from None
    if not n.is_finite() or abs(n)>max_abs:
        raise ValueError("nonfinite or excessive accounting amount")
    return n


def s(n):
    return format(n.normalize(),"f") if n is not None else None


def stamp(text):
    x=datetime.fromisoformat(text)
    if x.tzinfo is None:raise ValueError("unaware ledger timestamp")
    return x.astimezone(timezone.utc)


def _close(obs,kind,symbol,now):
    if (not isinstance(obs,dict) or obs.get("status") not in ("ok","stale")
        or obs.get("kind")!=kind or obs.get("symbol")!=symbol
        or obs.get("tradeable") is not False
        or obs.get("use_for_paper_execution") is not False
        or not isinstance(obs.get("data"),dict)
        or not isinstance(obs.get("sha256"),str)):
        return None
    try:
        verified=stamp(obs["last_verified_utc"])
        age=(now-verified).total_seconds()
        as_of=date.fromisoformat(obs["as_of"])
        daylag=(now.astimezone(KST).date()-as_of).days
        if not 0<=age<=72*3600 or not 0<=daylag<=7:return None
        payload=obs["data"]
        if kind=="FX":
            if payload.get("pair")!="USD/KRW" or payload.get("executable") is not False:
                return None
            amount=D(payload.get("rate"))
            if not Decimal(100)<=amount<=Decimal(10000):return None
        else:
            if payload.get("symbol")!=symbol:
                return None
            if payload.get("currency")!=("KRW" if symbol.isdigit() else "USD"):
                return None
            amount=D(payload.get("price"))
            if amount<=0:return None
        return amount
    except (KeyError,ValueError,TypeError,OverflowError):
        return None


def _basis(orders,metadata,events_fx,now):
    """Replay average-cost positions + sale P/L with acquisition-rate evidence.

    Metadata never represents an actual posting of fees or taxes. Unknown
    historical FX marks are NOT replaced by the latest FX rate.
    """
    positions={}
    closed=[]
    turnover={"USD":Decimal(0),"KRW":Decimal(0)}
    bysymbol={}
    for order in orders:
        if stamp(order["timestamp"])>now+timedelta(minutes=1):
            raise ValueError("future-dated simulated trade")
        symbol=order["symbol"]
        currency=order["currency"]
        if currency!=("KRW" if symbol.isdigit() else "USD"):
            raise ValueError("trade currency conflicts with ticker")
        qty=order["quantity"]
        if type(qty) is not int or not 0<qty<=100000:
            raise ValueError("invalid simulated quantity")
        price=D(order["price"])
        if price<=0:raise ValueError("invalid historical unit price")
        turnover[currency]+=price*qty
        bucket=bysymbol.setdefault(symbol,{"symbol":symbol,"currency":currency,
            "quantity":0,"native_cost":Decimal(0),
            "krw_cost":Decimal(0),"acquisition_fx_complete":True,
            "realized_native":Decimal(0),
            "realized_price_krw":Decimal(0),"realized_fx_krw":Decimal(0),
            "realized_fx_complete":True,"trades":0})
        if bucket["currency"]!=currency:raise ValueError("historical currency conflict")
        bucket["trades"]+=1
        meta=metadata.get(order["id"],{})
        rate_raw=meta.get("foreign_exchange_rate")
        if not rate_raw:
            rate_raw=events_fx.get(order["id"]+":fx")
        entry_fx=None
        if currency=="USD" and rate_raw is not None:
            entry_fx=D(rate_raw)
            if not 100<=entry_fx<=10000:
                raise ValueError("invalid historic trade FX evidence")
        if order["side"]=="BUY":
            if bucket["quantity"]==0:
                bucket["native_cost"]=Decimal(0)
                bucket["krw_cost"]=Decimal(0)
                bucket["acquisition_fx_complete"]=True
            bucket["native_cost"]+=price*qty
            if currency=="USD":
                if entry_fx is None:
                    bucket["acquisition_fx_complete"]=False
                elif bucket["acquisition_fx_complete"]:
                    bucket["krw_cost"]+=price*qty*entry_fx
            else:
                bucket["krw_cost"]+=price*qty
            bucket["quantity"]+=qty
        elif order["side"]=="SELL":
            owned=bucket["quantity"]
            if qty>owned:raise ValueError("negative historic inventory")
            avg=bucket["native_cost"]/owned
            # Fractional average cost can be repeating (e.g. 64,000 / 6).
            # Allocate the *total cost* proportionally to avoid cumulative
            # pseudo-cents. A final liquidation consumes the exact remainder.
            precision=Decimal("0.00000001")
            basis=(bucket["native_cost"] if qty==owned else
                (bucket["native_cost"]*qty/owned).quantize(
                    precision,rounding=ROUND_HALF_UP))
            profit=price*qty-basis
            bucket["realized_native"]+=profit
            sale_price_effect=profit if currency=="KRW" else None
            sale_fx_effect=None
            if currency=="USD":
                if (bucket["acquisition_fx_complete"] and entry_fx is not None):
                    acquisition_rate=bucket["krw_cost"]/bucket["native_cost"]
                    sale_price_effect=profit*acquisition_rate
                    sale_fx_effect=price*qty*(entry_fx-acquisition_rate)
                    bucket["realized_price_krw"]+=sale_price_effect
                    bucket["realized_fx_krw"]+=sale_fx_effect
                else:
                    bucket["realized_fx_complete"]=False
            else:
                bucket["realized_price_krw"]+=profit
            if bucket["acquisition_fx_complete"]:
                reduction=(bucket["krw_cost"] if qty==owned else
                    (bucket["krw_cost"]*qty/owned).quantize(
                        precision,rounding=ROUND_HALF_UP))
                bucket["krw_cost"]-=reduction
            bucket["native_cost"]-=basis
            bucket["quantity"]-=qty
            closed.append({"symbol":symbol,"side":"SELL","date":order["timestamp"],
                "accounting_currency":currency,"quantity":qty,
                "native_gross_gain":s(profit),
                "krw_price_effect":s(sale_price_effect),
                "krw_fx_effect":s(sale_fx_effect),
                "gross_sale_gain_krw":s((sale_price_effect or Decimal(0))+
                    (sale_fx_effect or Decimal(0))) if sale_price_effect is not None else None,
                "krw_gain_split_verified":sale_price_effect is not None})
            if bucket["quantity"]==0:
                bucket["native_cost"]=Decimal(0)
                bucket["krw_cost"]=Decimal(0)
                bucket["acquisition_fx_complete"]=True
        else:raise ValueError("unknown simulated trade side")
    return bysymbol,closed,turnover


def _samples(orders,div_ref,now):
    """Unpaid dividend ex-date indicators; never attribute confirmed cash."""
    if not div_ref or div_ref.get("status") not in ("ok","stale"):
        return []
    data=div_ref.get("data",{})
    if data.get("cash_applied") is not False or data.get("payment_dates_verified") is not False:
        return []
    symbol=data.get("symbol")
    market_zone=KST if symbol.isdigit() else NY
    signals=[]
    try:
        for item in data.get("events",[]):
            ex=date.fromisoformat(item["date"])
            if ex>now.astimezone(market_zone).date():continue
            amount=D(item["amount_per_share"])
            if amount<=0:continue
            owned=0
            for order in orders:
                if order["symbol"]!=symbol:continue
                trade_date=stamp(order["timestamp"]).astimezone(market_zone).date()
                # At ex-date opening, new holders generally do not receive it.
                if trade_date<ex:
                    owned+=order["quantity"]*(1 if order["side"]=="BUY" else -1)
            if owned<0:raise ValueError("negative as-of-exdate inventory")
            if owned:
                signals.append({"symbol":symbol,"ex_date":ex.isoformat(),
                    "quantity_before_ex_date":owned,
                    "per_share":s(amount),"potential_gross":s(amount*owned),
                    "currency":data["currency"],"source_url":div_ref["source_url"],
                    "evidence_hash":div_ref["sha256"],
                    "status":"UNCONFIRMED_PAYMENT","payment_date":None,
                    "cash_booked":False})
    except (KeyError,ValueError,TypeError,OverflowError):
        return []
    return signals[-24:]


def xirr(flows,valuation,at):
    """Robust annualized IRR for DATE-accurate deposit flows and terminal NAV.

    Net contributions only; internal account transfers and FX are excluded.
    Minimum seven elapsed days avoids deceptive same-day annualizations.
    """
    nav=D(valuation)
    if nav<=0:return {"status":"unavailable","reason":"NONPOSITIVE_TERMINAL_NAV","pct":None}
    starting=[]
    for value,when in flows:
        value=D(value)
        when=stamp(when) if isinstance(when,str) else when.astimezone(timezone.utc)
        if when>at+timedelta(minutes=1):
            return {"status":"unavailable","reason":"FUTURE_DATED_CASHFLOW","pct":None}
        if value>0:starting.append((-float(value),when))
    if not starting:return {"status":"unavailable","reason":"NO_DEPOSIT_HISTORY","pct":None}
    first=min(t for _,t in starting)
    days=(at-first).total_seconds()/SECONDS_PER_DAY
    if days<7:
        return {"status":"unavailable","reason":"LESS_THAN_SEVEN_DAYS","pct":None}
    normalized=[(flow,(t-first).total_seconds()/(365.25*SECONDS_PER_DAY))
                for flow,t in starting]
    normalized.append((float(nav),days/365.25))
    def npv(rate):
        if rate<=-1:return math.inf
        try:return sum(c*math.exp(-math.log1p(rate)*years) for c,years in normalized)
        except (ValueError,OverflowError):return math.inf
    low=-0.9999;high=100.0
    left,right=npv(low),npv(high)
    if not math.isfinite(left) or not math.isfinite(right) or left*right>0:
        return {"status":"unavailable","reason":"IRR_NOT_BRACKETED","pct":None}
    for _ in range(130):
        mid=(low+high)/2
        if npv(mid)>0:low=mid
        else:high=mid
    ans=(low+high)/2
    if not math.isfinite(ans):
        return {"status":"unavailable","reason":"NUMERIC_IRR_FAILURE","pct":None}
    return {"status":"ok","pct":round(ans*100,6),"period_days":round(days,1),
            "method":"annualized money-weighted XIRR, dated funding and terminal net NAV",
            "does_not_predict_future":True}


def _snapshot_digest(owner,day,observed,nav,sources,ledger_hash):
    raw=json.dumps([owner,day,observed,nav,sources,ledger_hash],
                   ensure_ascii=False,separators=(",",":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _ledger_digest(owner,raw_orders,raw_meta,cash_events,transfers,accounting,corporate):
    groups=(raw_orders,raw_meta,cash_events,transfers,accounting,corporate)
    if sum(len(group) for group in groups)>50000:
        raise ValueError("too many transactions for bounded accounting")
    def normalized(rows):
        values=[list(row) for row in rows]
        return sorted(values,key=lambda row:json.dumps(
            row,ensure_ascii=False,separators=(",",":")))
    doc={"owner":owner,"groups":[normalized(rows) for rows in groups]}
    return hashlib.sha256(json.dumps(doc,ensure_ascii=False,sort_keys=True,
        separators=(",",":")).encode()).hexdigest()


def _current_ledger_digest(db,owner):
    """Read one owner within an existing SQLite transaction and hash all
    accounting inputs, including transaction timestamps and fee metadata."""
    orders=db.execute("""SELECT portfolio,request_id,symbol,side,quantity,
      price,currency,source,as_of,created_at
      FROM virtual_orders WHERE owner=?""",(owner,)).fetchall()
    meta=db.execute("""SELECT portfolio,order_key,foreign_exchange_rate,
      fee_amount,tax_amount,costs_currency,source
      FROM stage1_trade_metadata WHERE owner=?""",(owner,)).fetchall()
    cash=db.execute("""SELECT event_key,kind,amount_krw,amount_usd,rate,created_utc
      FROM stage1_events WHERE owner=?""",(owner,)).fetchall()
    transfers=db.execute("""SELECT source,destination,amount_krw
      FROM portfolio_transfers WHERE owner=?""",(owner,)).fetchall()
    accounting=db.execute("""SELECT portfolio,event_key,category,amount,currency,
      status,reference FROM stage1_accounting_events WHERE owner=?""",(owner,)).fetchall()
    corp=db.execute("""SELECT symbol,category,status FROM stage1_corporate_events
      WHERE owner=?""",(owner,)).fetchall()
    return _ledger_digest(owner,orders,meta,cash,transfers,accounting,corp)


def _tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS stage5_nav_snapshots(
      owner TEXT NOT NULL,as_of_day TEXT NOT NULL,
      observed_utc TEXT NOT NULL,nav_krw TEXT NOT NULL,
      sources_sha256 TEXT NOT NULL,ledger_sha256 TEXT,
      snapshot_sha256 TEXT,
      PRIMARY KEY(owner,as_of_day))""")
    # Existing Stage5 preview DBs can have only the earlier five columns.
    # Do not rewrite old rows: missing digests remain explicitly untrusted.
    names={row[1] for row in db.execute("PRAGMA table_info(stage5_nav_snapshots)")}
    for key in ("ledger_sha256","snapshot_sha256"):
        if key not in names:
            db.execute(f"ALTER TABLE stage5_nav_snapshots ADD COLUMN {key} TEXT")


def twr_from_verified_snapshots(rows,flow_rows):
    if len(rows)<2:return {"status":"unavailable","pct":None,
                           "reason":"AT_LEAST_TWO_VERIFIED_DAILY_NAVS_REQUIRED"}
    gross=Decimal(1);segments=0;crossed=[]
    for left,right in zip(rows,rows[1:]):
        if right["observed_utc"]<=left["observed_utc"]:
            return {"status":"unavailable","pct":None,"reason":"OUT_OF_ORDER_NAV"}
        a=D(left["nav_krw"]);b=D(right["nav_krw"])
        if a<=0 or b<0:return {"status":"unavailable","pct":None,"reason":"INVALID_NAV"}
        start=stamp(left["observed_utc"]);end=stamp(right["observed_utc"])
        contributions=[key for key,created,amount in flow_rows if
                       start<stamp(created)<=end and D(amount)!=0]
        if contributions:
            crossed+=contributions
            continue
        gross*=b/a;segments+=1
    if crossed:
        return {"status":"incomplete","pct":None,
                "reason":"EXTERNAL_FLOW_WITHOUT_BEFORE_AFTER_NAV",
                "flow_intervals":len(crossed),"flow_free_segments":segments}
    return {"status":"ok","pct":round(float((gross-1)*100),6),
            "flow_free_segments":segments,"method":"chained NAV returns with zero external flow within each interval",
            "annualized":False}


class AccountingEngine:
    def __init__(self,portfolios,market:MarketStore):
        self.portfolios=portfolios
        self.market=market

    def report(self,owner,*,now=None):
        now=now or datetime.now(timezone.utc)
        if not isinstance(now,datetime) or now.tzinfo is None:
            raise ValueError("timezone required")
        now=now.astimezone(timezone.utc)
        with self.portfolios._db() as db:
            db.execute("BEGIN")
            accounts=db.execute("SELECT id,name,kind FROM portfolios WHERE owner=? ORDER BY id",
                                (owner,)).fetchall()
            raw_orders=db.execute("""SELECT portfolio,request_id,symbol,side,quantity,
                  price,currency,source,as_of,created_at
                  FROM virtual_orders WHERE owner=? ORDER BY created_at,request_id""",
                  (owner,)).fetchall()
            raw_meta=db.execute("""SELECT portfolio,order_key,foreign_exchange_rate,
                  fee_amount,tax_amount,costs_currency,source
                  FROM stage1_trade_metadata WHERE owner=?""",(owner,)).fetchall()
            cash_events=db.execute("""SELECT event_key,kind,amount_krw,amount_usd,
                  rate,created_utc FROM stage1_events WHERE owner=?
                  ORDER BY created_utc,event_key""",(owner,)).fetchall()
            transfers=db.execute("""SELECT source,destination,amount_krw
                FROM portfolio_transfers WHERE owner=?""",(owner,)).fetchall()
            accounting=db.execute("""SELECT portfolio,event_key,category,amount,
                currency,status,reference FROM stage1_accounting_events
                WHERE owner=? ORDER BY observed_at_utc,event_key""",(owner,)).fetchall()
            corporate=db.execute("""SELECT symbol,category,status FROM
                stage1_corporate_events WHERE owner=?""",(owner,)).fetchall()
            _tables(db)
            nav_rows=db.execute("""SELECT as_of_day,observed_utc,nav_krw,
              sources_sha256,ledger_sha256,snapshot_sha256
              FROM stage5_nav_snapshots WHERE owner=?
              ORDER BY as_of_day DESC LIMIT 180""",(owner,)).fetchall()
            ledger_hash=_ledger_digest(owner,raw_orders,raw_meta,cash_events,
                                      transfers,accounting,corporate)
            db.execute("COMMIT")
        # A later-dated cash event or invalid exchange can never be counted
        # in an earlier NAV/XIRR. Fail closed instead of time-travelling cash.
        for key,kind,krw,usd,rate,created in cash_events:
            if stamp(created)>now+timedelta(minutes=1):
                raise ValueError("future-dated funding or exchange event")
            D(krw);D(usd)
            if kind=="virtual_fx" and rate is not None:
                observed_rate=D(rate)
                if not Decimal("100")<=observed_rate<=Decimal("10000"):
                    raise ValueError("invalid historical exchange rate")
        event_map={key:rate for key,kind,_,_,rate,_ in cash_events
                   if kind=="virtual_fx" and rate is not None}
        metadata={(account,request):{"foreign_exchange_rate":rate,
                    "fee_amount":fee,"tax_amount":tax,"costs_currency":costs_cur,
                    "source":source}
                  for account,request,rate,fee,tax,costs_cur,source in raw_meta}
        byaccount=defaultdict(list)
        for account,key,sym,side,qty,price,currency,source,as_of,created in raw_orders:
            byaccount[account].append({"id":key,"symbol":sym,"side":side,
                "quantity":qty,"price":price,"currency":currency,
                "source":source,"as_of":as_of,"timestamp":created})
        active_symbols={order["symbol"] for orders in byaccount.values()
                        for order in orders}
        pairs=[("FX","USD/KRW")]+[("PRICE",symbol) for symbol in active_symbols]+[
            ("DIVIDENDS",symbol) for symbol in active_symbols]
        observed=self.market.latest_many(pairs[:96],now=now)
        fx=_close(observed.get(("FX","USD/KRW")),"FX","USD/KRW",now)
        problems=[]
        holdings=[];realizations=[];realized_sales=[];potential_dividends=[]
        native_totals={"USD":{"realized":Decimal(0),"unrealized":Decimal(0)},
                       "KRW":{"realized":Decimal(0),"unrealized":Decimal(0)}}
        position_total=Decimal(0)
        cash_krw=Decimal(0);cash_usd=Decimal(0)
        fees={"known_reported":0,"unknown_trade_count":0,
              "trade_cost_records_unposted":0}
        observed_costs={"KRW":{"fee":Decimal(0),"tax":Decimal(0)},
                        "USD":{"fee":Decimal(0),"tax":Decimal(0)}}
        observed_cost_receipts=[]
        all_trades=0
        acc_event_krw=sum((D(amount) for _,kind,amount,_,_,_
                           in cash_events if kind in ("deposit","virtual_fx")),Decimal(0))
        acc_event_usd=sum((D(amount) for _,_,_,amount,_,_
                           in cash_events),Decimal(0))
        for ident,name,kind in accounts:
            account_orders=byaccount[ident]
            account_metadata={request:metadata.get((ident,request),{})
                              for request in [o["id"] for o in account_orders]}
            lots,closed,turnover=_basis(account_orders,account_metadata,event_map,now)
            realized_sales.extend([{**row,"account":ident,"account_kind":kind}
                                  for row in closed])
            all_trades+=len(account_orders)
            for order in account_orders:
                meta=account_metadata.get(order["id"],{})
                if meta.get("fee_amount") is None or meta.get("tax_amount") is None:
                    fees["unknown_trade_count"]+=1
                else:
                    fees["known_reported"]+=1
                if meta.get("fee_amount") is not None or meta.get("tax_amount") is not None:
                    fees["trade_cost_records_unposted"]+=1
                    denomination=meta.get("costs_currency")
                    if denomination not in ("KRW","USD"):
                        raise ValueError("recorded fee/tax without currency")
                    fee=D(meta["fee_amount"]) if meta.get("fee_amount") is not None else None
                    tax=D(meta["tax_amount"]) if meta.get("tax_amount") is not None else None
                    if (fee is not None and fee<0) or (tax is not None and tax<0):
                        raise ValueError("negative fee or withholding record")
                    if fee is not None:observed_costs[denomination]["fee"]+=fee
                    if tax is not None:observed_costs[denomination]["tax"]+=tax
                    observed_cost_receipts.append({"order_key":order["id"],
                        "account":ident,"currency":denomination,
                        "fee":s(fee),"tax":s(tax),"cash_posted":False})
            krw=acc_event_krw if ident=="default" else Decimal(0)
            usd=acc_event_usd if ident=="default" else Decimal(0)
            for source,destination,amount in transfers:
                if source==ident:krw-=D(amount)
                if destination==ident:krw+=D(amount)
            for order in account_orders:
                signed=D(order["price"])*D(order["quantity"])
                side=1 if order["side"]=="SELL" else -1
                if order["currency"]=="USD":usd+=side*signed
                else:krw+=side*signed
            cash_krw+=krw;cash_usd+=usd
            if usd and fx is None:
                problems.append(ident+": USD 현금 환율 미확인")
            for symbol,lot in lots.items():
                native_totals[lot["currency"]]["realized"]+=lot["realized_native"]
                if lot["quantity"]<0:raise ValueError("negative inventory")
                if not lot["quantity"]:continue
                # Corporate-action dividend entitlement evidence should not
                # disappear when a separate price feed is unavailable.
                potential_dividends+=_samples(account_orders,
                    observed.get(("DIVIDENDS",symbol)),now)
                current=_close(observed.get(("PRICE",symbol)),"PRICE",symbol,now)
                if current is None:
                    problems.append(symbol+": 검증된 최신 종가 없음")
                    continue
                currency=lot["currency"]
                if currency=="USD" and fx is None:
                    problems.append(symbol+": USD/KRW 환율 없음")
                    continue
                quantity=lot["quantity"]
                value=current*quantity
                unreal=value-lot["native_cost"]
                native_totals[currency]["unrealized"]+=unreal
                conversion=fx if currency=="USD" else Decimal(1)
                position_total+=value*conversion
                market_effect=None;fx_effect=None;fx_split_status="not_applicable"
                if currency=="USD":
                    fx_split_status="missing_acquisition_fx"
                    if lot["acquisition_fx_complete"] and lot["native_cost"]>0:
                        entry_rate=lot["krw_cost"]/lot["native_cost"]
                        market_effect=unreal*entry_rate
                        fx_effect=value*(fx-entry_rate)
                        fx_split_status="available"
                row={"account":ident,"account_kind":kind,"symbol":symbol,
                     "currency":currency,"quantity":quantity,
                     "avg_cost_native":s(lot["native_cost"]/quantity),
                     "cost_basis_native":s(lot["native_cost"]),
                     "price_native":s(current),"value_native":s(value),
                     "value_krw":s(value*conversion),
                     "unrealized_native":s(unreal),
                     "unrealized_krw_total":s(unreal if currency=="KRW"
                                                else value*fx-lot["krw_cost"]
                                                if fx_split_status=="available" else None),
                     "market_price_effect_krw":s(market_effect),
                     "fx_effect_krw":s(fx_effect),
                     "fx_split_status":fx_split_status,
                     "price_as_of":observed[("PRICE",symbol)]["as_of"],
                     "price_source_url":observed[("PRICE",symbol)]["source_url"],
                     "price_evidence_hash":observed[("PRICE",symbol)]["sha256"],
                     "gross_only":True}
                holdings.append(row)

            for symbol,lot in lots.items():
                if lot["realized_native"]==0 and not any(x["symbol"]==symbol for x in closed):
                    continue
                currency=lot["currency"]
                known=currency=="KRW" or lot["realized_fx_complete"]
                realizations.append({"account":ident,"symbol":symbol,
                    "currency":currency,"gross_realized_native":s(lot["realized_native"]),
                    "split_krw_confirmed":known,
                    "realized_price_effect_krw":s(lot["realized_price_krw"]) if known else None,
                    "realized_fx_effect_krw":s(lot["realized_fx_krw"]) if known and currency=="USD" else None,
                    "fees_and_tax_complete":False})
        if fx is None and cash_usd:problems.append("USD 현금: USD/KRW 환율 미확인")
        if len(pairs)>96:problems.append("종목별 기준자료 상한 초과")
        if corporate:
            # Stage1 has action evidence but does not adjust inventory/cost
            # after splits/mergers. Even RECONCILED metadata cannot prove
            # that quantities in virtual_orders were transformed correctly.
            problems.append("기업 분할·합병 등 권리변경 발생: 재고·원가 조정 엔진 미연결")
        full=not problems
        nav=cash_krw+(cash_usd*fx if fx is not None else Decimal(0))+position_total
        if nav<=0:problems.append("양수의 전체 가상자산 평가금액 필요")
        full=not problems
        flows=[(key,created,amount) for key,kind,amount,usd,rate,created
               in cash_events if kind=="deposit"]
        deposited=sum((D(amount) for _,_,amount in flows),Decimal(0))
        result_xirr=xirr([(amount,created) for _,created,amount in flows],
                         nav,now) if full else {"status":"unavailable","pct":None,
                                                 "reason":"NAV_INCOMPLETE"}
        history_valid=all(ledger_ref is not None and digest is not None
            and digest==_snapshot_digest(owner,day,observed_at,amount,
                                        sources,ledger_ref)
            for day,observed_at,amount,sources,ledger_ref,digest in nav_rows)
        if history_valid:
            twr=twr_from_verified_snapshots([
                {"as_of_day":day,"observed_utc":observed_at,"nav_krw":amount,
                 "sources_sha256":sources}
                for day,observed_at,amount,sources,_,_ in reversed(nav_rows)],flows)
        else:
            twr={"status":"unavailable","pct":None,
                 "reason":"NAV_SNAPSHOT_INTEGRITY_FAILED"}
        nav_history=[]
        if history_valid:
            for as_day,observed_at,amount,_,_,_ in reversed(nav_rows):
                observed_time=stamp(observed_at)
                prior_contributed=sum((D(v) for _,created,v in flows
                     if stamp(created)<=observed_time),Decimal(0))
                nav_history.append({"date":as_day,"value_krw":amount,
                    "contributed_krw":float(prior_contributed),
                    "complete":True,"verified_source_digest":True})
        outstanding=[{"account":account,"event":key,"category":category,
                      "amount":amount,"currency":currency,"status":status,
                      "cash_posted":False,"reference":reference}
                     for account,key,category,amount,currency,status,reference
                     in accounting]
        if outstanding:
            # Existing schema records do not affect _money. Never double-book
            # known accounting rows as cash or pretend net return is final.
            fees["trade_cost_records_unposted"]+=len(outstanding)
        cash_flows={"deposits_krw":s(deposited),
                    "deposit_count":len(flows),
                    "internal_transfers_excluded":len(transfers),
                    "virtual_fx_excluded":sum(kind=="virtual_fx"
                        for _,kind,_,_,_,_ in cash_events)}
        # Today's spot FX can give an illustrative trade turnover scenario,
        # but cannot replace historical fill-date FX for settled fee records.
        native_turnover_usd=sum((D(x["price"])*D(x["quantity"])
                 for orders in byaccount.values() for x in orders
                 if x["currency"]=="USD"),Decimal(0))
        native_turnover_krw=sum((D(x["price"])*D(x["quantity"])
                 for orders in byaccount.values() for x in orders
                 if x["currency"]=="KRW"),Decimal(0))
        fee_proxy=(native_turnover_krw+native_turnover_usd*fx)*FEE_RATE_ASSUMED if fx is not None else None
        tax=research_comparison(holdings)
        # Sale-year annual tax examples are separate from *unrealized* future
        # sale scenarios. Never add them as accrued or paid liability.
        sale_year=now.astimezone(KST).year
        recent=[x for x in realized_sales
                if stamp(x["date"]).astimezone(KST).year==sale_year]
        overseas=[x for x in recent if x["account_kind"]=="GENERAL_OVERSEAS"
                  and x["accounting_currency"]=="USD"]
        if overseas and all(x["gross_sale_gain_krw"] is not None for x in overseas):
            projected=sum((D(x["gross_sale_gain_krw"]) for x in overseas),Decimal(0))
            tax["overseas_general_realized"]=foreign_ordinary_scenario(projected)
            tax["overseas_general_realized"]["sale_year"]=sale_year
            tax["overseas_general_realized"]["historical_fx_covered"]=True
        else:
            tax["overseas_general_realized"]={"status":"unavailable",
                "reason":"SELL_FX_OR_REALIZED_FOREIGN_GAIN_MISSING" if overseas
                          else "NO_YEARLY_FOREIGN_SALES",
                "not_actual_tax":True}
        domestic_eligible=[x for x in recent if x["symbol"]=="360750"
                           and x["accounting_currency"]=="KRW"]
        if domestic_eligible:
            realized_gains=sum((D(x["gross_sale_gain_krw"])
                          for x in domestic_eligible),Decimal(0))
            tax["domestic_realized_illustration"]=domestic_isa_scenario(realized_gains)
            tax["domestic_realized_illustration"]["sale_year"]=sale_year
            tax["domestic_realized_illustration"]["isa_term_not_verified"]=True
        else:
            tax["domestic_realized_illustration"]=None
        return {"engine_version":ENGINE,"status":"complete_gross_valuation" if full
                else "incomplete","as_of_utc":now.isoformat(),
                "valuation_complete":full,"market_valuation_krw":s(nav) if full else None,
                "cash_krw":s(cash_krw),"cash_usd":s(cash_usd),
                "reference_usd_krw":s(fx),
                "contributions_krw":s(deposited),
                "gross_change_after_contributions_krw":s(nav-deposited) if full else None,
                "gross_change_pct":s((nav/deposited-1)*100) if full and deposited>0 else None,
                "native_gross_pnl":{cur:{"realized":s(values["realized"]),
                      "unrealized":s(values["unrealized"])}
                    for cur,values in native_totals.items()},
                "positions":holdings,"realizations":realizations,
                "realized_sales":realized_sales[:250],
                "potential_dividends":potential_dividends,
                "dividend_cash_posted_krw":None,
                "recorded_unposted_accounting_events":outstanding[:100],
                "accounting_event_count":len(outstanding),
                "fee_analysis":{"actual_fee_amount_complete":False,
                     "assumed_rate_each_side_pct":"0.1",
                     "illustrative_trade_fees_krw":s(fee_proxy),
                     "fee_records_and_history":fees,
                     "recorded_unposted_native":{currency:{
                         "fee":s(vals["fee"]),"tax":s(vals["tax"])}
                         for currency,vals in observed_costs.items()},
                     "recorded_trade_cost_receipts":observed_cost_receipts[:100],
                     "not_deducted_from_cash":True},
                "cash_flows":cash_flows,
                "ledger_sha256":ledger_hash,
                "nav_history":nav_history,
                "xirr":result_xirr,"twr":twr,
                "tax_illustrations":tax,
                "missing":problems,
                "source_evidence":[{"kind":x["kind"],"symbol":x["symbol"],
                       "source_url":x["source_url"],"sha256":x["sha256"],
                       "as_of":x["as_of"]}
                    for x in observed.values() if x.get("status") in ("ok","stale")
                    and x.get("sha256")],
                "cost_basis_method":"moving weighted average, simulated fills only",
                "fee_tax_actual_status":"UNKNOWN_NOT_NETTED",
                "cashflow_affected_returns":"XIRR from dated deposits; TWR only if flow-free NAV observations",
                "never_posts_cash":True,"simulated_only":True,
                "warnings":["원장 표시 손익은 수수료·실제 배당금·원천징수 전 총손익입니다.",
                  "거래 FX 근거가 없는 미국주식의 원화 매입원가/환차손익은 미평가합니다.",
                  "실제 배당 지급일이 확인되지 않아 배당락일 권리 가능성만 표시합니다.",
                  "비용 시나리오와 ISA 비교는 세무 신고·세후 실적이 아닙니다."]}

    def capture_daily_nav(self,owner,*,now=None):
        """Immutable one-per-KST-day fully verified NAV. No fabricated history."""
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None:raise ValueError("timezone required")
        local=now.astimezone(KST)
        if local.hour<16:
            return {"status":"not_due","date":local.date().isoformat()}
        # Cache/report values can move between request and transaction; the
        # immutable report is a documented sampled state, NOT a trade event.
        report=self.report(owner,now=now)
        if not report["valuation_complete"]:
            return {"status":"incomplete","reason":report["missing"],"saved":False}
        values=[(x["kind"],x["symbol"],x["sha256"])
                for x in report["source_evidence"]]
        evidence_hash=hashlib.sha256(json.dumps(sorted(values),
             separators=(",",":")).encode()).hexdigest()
        captured_at=now.astimezone(timezone.utc).isoformat()
        day=local.date().isoformat()
        snapshot_digest=_snapshot_digest(owner,day,captured_at,
              report["market_valuation_krw"],evidence_hash,report["ledger_sha256"])
        with self.portfolios._db() as db:
            db.execute("BEGIN IMMEDIATE")
            _tables(db)
            if _current_ledger_digest(db,owner)!=report["ledger_sha256"]:
                db.execute("COMMIT")
                return {"status":"ledger_changed","saved":False,
                        "reason":["평가 중 가상 거래·입금·회계 자료가 변경되어 재조회가 필요합니다."]}
            db.execute("""INSERT OR IGNORE INTO stage5_nav_snapshots
                (owner,as_of_day,observed_utc,nav_krw,sources_sha256,
                 ledger_sha256,snapshot_sha256)
                VALUES(?,?,?,?,?,?,?)""",
                (owner,day,captured_at,report["market_valuation_krw"],
                 evidence_hash,report["ledger_sha256"],snapshot_digest))
            inserted=db.execute("SELECT changes()").fetchone()[0]==1
            db.execute("COMMIT")
        return {"status":"saved" if inserted else "already_saved",
                "date":local.date().isoformat(),"saved":inserted,
                "source_digest":evidence_hash,"simulated_only":True}

