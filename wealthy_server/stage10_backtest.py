"""Offline historical strategy replay with explicit friction and no order routing."""
from datetime import date
from decimal import Decimal, InvalidOperation

def replay(rows, *, fee_bps=10, slippage_bps=5, initial_cash="1000000"):
    """20/60 close crossover, signal at t-1, hypothetical execution at t close."""
    def reject(reason):
        return {"status":"unavailable","reason":reason,"tradeable":False}
    if (not isinstance(rows,(list,tuple)) or len(rows)<65 or len(rows)>10000
            or isinstance(fee_bps,bool) or isinstance(slippage_bps,bool)
            or not isinstance(fee_bps,(int,float)) or not isinstance(slippage_bps,(int,float))
            or not 0<=fee_bps<=500 or not 0<=slippage_bps<=500):
        return reject("invalid_history_or_cost")
    try:
        cash=Decimal(initial_cash)
        if not cash.is_finite() or cash<=0:return reject("invalid_cash")
        data=[];prev=None
        for item in rows:
            d=date.fromisoformat(item["date"])
            p=Decimal(str(item["close"]))
            if not p.is_finite() or p<=0 or (prev is not None and d<=prev):
                return reject("invalid_price_or_sequence")
            prev=d;data.append((d,p))
        rate=(Decimal(str(fee_bps))+Decimal(str(slippage_bps)))/Decimal(10000)
        if rate>=1:return reject("invalid_cost")
        initial=cash;units=Decimal(0);transactions=0;peak=initial;max_dd=Decimal(0)
        curve=[]
        for i,(day,price) in enumerate(data):
            if i>=60:
                prior=[p for _,p in data[i-60:i]]
                short=sum(prior[-20:])/20
                long=sum(prior)/60
                want=short>long
                if want and units==0:
                    units=cash/(price*(1+rate));cash=Decimal(0);transactions+=1
                elif not want and units>0:
                    cash=units*price*(1-rate);units=Decimal(0);transactions+=1
            equity=cash+units*price
            peak=max(peak,equity);max_dd=min(max_dd,equity/peak-1)
            curve.append({"date":day.isoformat(),"equity":str(equity.quantize(Decimal("0.01")))})
        equity=cash+units*data[-1][1]
        return {"status":"ok","engine":"offline-20-60-tminus1","tradeable":False,
                "real_fills":False,"transactions":transactions,
                "assumed_fee_bps":fee_bps,"assumed_slippage_bps":slippage_bps,
                "initial_cash":str(initial),"final_equity":str(equity.quantize(Decimal("0.01"))),
                "return_pct":round(float((equity/initial-1)*100),3),
                "max_drawdown_pct":round(float(max_dd*100),3),
                "curve":curve,
                "limitations":["no dividends","no tax","no FX","no split adjustment",
                               "no survivorship correction","not an executable fill model"]}
    except (KeyError,TypeError,ValueError,InvalidOperation,OverflowError,ZeroDivisionError):
        return reject("invalid_input")
