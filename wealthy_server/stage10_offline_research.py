"""Stage 10 offline, deterministic research. Never enables orders or creates prices."""
from datetime import date
from decimal import Decimal, InvalidOperation
from math import sqrt
from statistics import pstdev

VERSION = "10.0-research-1"

def _price(value):
    try:
        if isinstance(value,bool):return None
        n=Decimal(str(value))
        return float(n) if n.is_finite() and n>0 else None
    except (TypeError,ValueError,InvalidOperation,OverflowError):
        return None

def analyze(closes, *, as_of=None):
    """Input: ordered records {'date':'YYYY-MM-DD','close':positive}; no network."""
    unavailable=lambda reason:{"status":"unavailable","reason":reason,"tradeable":False}
    if not isinstance(closes,(list,tuple)) or len(closes)>20000:
        return unavailable("invalid_history")
    try:
        boundary=date.fromisoformat(as_of) if isinstance(as_of,str) else as_of
        if boundary is not None and not isinstance(boundary,date):
            return unavailable("invalid_as_of")
        days=[];prices=[]
        for item in closes:
            if not isinstance(item,dict):return unavailable("invalid_record")
            d=date.fromisoformat(item["date"])
            p=_price(item.get("close"))
            if p is None or (days and d<=days[-1]) or (boundary and d>boundary):
                return unavailable("invalid_or_future_quote")
            days.append(d);prices.append(p)
    except (KeyError,ValueError,TypeError):
        return unavailable("invalid_date")
    if len(prices)<65:return unavailable("insufficient_history")
    returns=[prices[i]/prices[i-1]-1 for i in range(1,len(prices))]
    vol=pstdev(returns[-63:])*sqrt(252)
    peak=prices[0];drawdown=0.
    for p in prices:
        peak=max(peak,p);drawdown=min(drawdown,p/peak-1)
    ma20=sum(prices[-20:])/20;ma60=sum(prices[-60:])/60
    trend="up" if prices[-1]>ma20>ma60 else "down" if prices[-1]<ma20<ma60 else "mixed"
    return {"status":"ok","as_of":days[-1].isoformat(),"observations":len(prices),
            "ma20":round(ma20,6),"ma60":round(ma60,6),
            "momentum_60_pct":round(100*(prices[-1]/prices[-61]-1),3),
            "volatility_annual_pct":round(100*vol,3),
            "max_drawdown_pct":round(100*drawdown,3),
            "trend":trend,"tradeable":False,"execution_authorized":False}

def research_gate(result, *, executable_quote_verified=False, approval=False):
    """Read-only study decision, not a paper or broker trade permission."""
    if not isinstance(result,dict) or result.get("status")!="ok":
        return {"decision":"WAIT_FOR_DATA","tradeable":False}
    if result.get("tradeable") is not False or result.get("execution_authorized") is not False:
        return {"decision":"WAIT_FOR_REVIEW","tradeable":False}
    if not executable_quote_verified:
        return {"decision":"WAIT_FOR_DATA","tradeable":False}
    if result["volatility_annual_pct"]>55 or result["max_drawdown_pct"]< -30:
        return {"decision":"RISK_REVIEW","tradeable":False}
    if result["trend"]=="up":
        return {"decision":"RESEARCH_CANDIDATE" if approval else "REVIEW_PENDING","tradeable":False}
    return {"decision":"OBSERVE","tradeable":False}

def unavailable_card(label, error=None):
    """Empty market/news area gets an explanation, never a fabricated quote."""
    if not isinstance(label,str) or not label.strip():raise ValueError("label")
    return {"label":label,"status":"unavailable","items":[],
            "message":"자료를 확인할 수 없습니다. 데이터 공급원과 마지막 조회 시각을 확인하세요.",
            "reason":str(error or "no_valid_observation")[:120]}
