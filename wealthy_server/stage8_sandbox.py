"""Stage8 offline 4,000,000 KRW ONE-CYCLE investment demonstration.

Important trust boundary:
* A throwaway sqlite3 file and synthetic reference quotes are used.
* Only the existing Stage4/6/7 simulator chooses and records sample fills.
* No live provider, user account, broker API, AI or live market data is read.
* This DOES NOT bypass the actual market/weekend check or Stage4 5% cap.
* Nothing is copied from this demo to a customer's paper ledger or history.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .stage1_bootstrap import initialize_paper_accounts
from .stage1_controls import set_paused
from .stage1_discovery import PreferenceStore
from .stage1_market_store import MarketStore, validated_snapshot
from .stage1_paper import INITIAL_KRW, MONTHLY_KRW, Stage1Ledger
from .stage1_portfolios import Portfolios
from .stage1_strategy_policy import DEFAULT_WEIGHTS, StrategyPolicy
from .stage4_portfolio import PortfolioEngine
from .stage7_release import audit_paper_ledger

UTC=timezone.utc
KST=ZoneInfo("Asia/Seoul")
DEMO_OWNER="sandbox-stage8-only"
SYMBOLS={"VOO":{"hypothetical_close":"80","market":"US","kind":"ETF",
                 "name":"Vanguard S&P 500 ETF"},
         "360750":{"hypothetical_close":"20000","market":"KR","kind":"ETF",
                  "name":"TIGER 미국S&P500"}}
DEMO_FX="1400"
DEMO_WEIGHT={"index":100,**{name:0 for name in DEFAULT_WEIGHTS if name!="index"}}
DEMO_PREFERENCE={"us_weight":75,"kr_weight":25,"etf_weight":100,"stock_weight":0}


def next_simulated_session(now=None):
    """Next business session at 19:00 KST; NOT real exchange opening hours."""
    current=(now or datetime.now(UTC)).astimezone(KST)
    candidate=current.date()
    if current.time()>=time(19,0):
        candidate+=timedelta(days=1)
    while candidate.weekday()>=5:
        candidate+=timedelta(days=1)
    simulated=datetime.combine(candidate,time(19,0),tzinfo=KST).astimezone(UTC)
    close_day=candidate-timedelta(days=1)
    while close_day.weekday()>=5:
        close_day-=timedelta(days=1)
    return simulated,close_day


def _fixture_history(symbol,close,reference):
    # Smooth, moderate volatility, 126 sessions. Never call this market data.
    days=[];day=close
    while len(days)<130:
        if day.weekday()<5:
            days.append(day)
        day-=timedelta(days=1)
    days.reverse()
    end=float(reference)
    values=[]
    for i,date_ in enumerate(days):
        ratio=0.84+0.16*i/(len(days)-1)
        # exactly match independently provided synthetic "close" at the last day
        value=round(end*ratio,4)
        values.append({"date":date_.isoformat(),"close":str(value)})
    values[-1]["close"]=str(reference)
    return {"status":"ok","symbol":symbol,
            "currency":"KRW" if symbol.isdigit() else "USD",
            "as_of":close.isoformat(),"prices":values}


def _quote(symbol,close):
    amount=SYMBOLS[symbol]["hypothetical_close"]
    return {"status":"ok","tradeable":True,"symbol":symbol,
            "price":amount,"currency":"KRW" if symbol.isdigit() else "USD",
            "as_of":close.isoformat(),"source":"STAGE8_SYNTHETIC_SANDBOX_QUOTE"}


def _record(cache,kind,symbol,payload,now):
    if kind=="FX":
        provider="FRANKFURTER"
        url="https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW"
    elif symbol.isdigit():
        provider="YAHOO_UNOFFICIAL"
        url="https://query1.finance.yahoo.com/v8/finance/chart/"+symbol+".KS?range=1y&interval=1d"
    else:
        provider="STOOQ"
        url=("https://stooq.com/q/d/l/?s="+symbol.lower()+
             ".us&i=d&d1=20250101&d2="+now.strftime("%Y%m%d"))
    # Strict source validators are reused purely as consistency checks.
    # These URLs have NOT been fetched. All payload values are synthetic.
    cache.save(validated_snapshot(kind,symbol,provider,url,payload,now=now))


def _accounts(portfolios):
    return [{"account_id":a["id"],"kind":a["kind"],
             "cash_krw":a["krw_cash"],"cash_usd":a["usd_cash"],
             "positions":[{"symbol":x["symbol"],"quantity":x["quantity"]}
                          for x in a["holdings"]]}
            for a in portfolios.list(DEMO_OWNER)["accounts"]]


def _uncommitted_scenario(run_utc,close):
    with TemporaryDirectory(prefix="wealthy-stage8-sandbox-") as tmp:
        db_file=Path(tmp)/"stage1-paper.sqlite3"
        ledger=Stage1Ledger(db_file)
        paper=Portfolios(db_file)
        # Choose the first of the month to test INITIAL deposit only;
        # the later scheduled 350k is verified separately.
        initialize_paper_accounts(DEMO_OWNER,ledger,paper,
            today=run_utc.astimezone(KST).date().replace(day=1))
        preferences=PreferenceStore(db_file)
        preferences.save(DEMO_OWNER,75,100)
        policy=StrategyPolicy(paper)
        policy.save(DEMO_OWNER,DEMO_WEIGHT)
        cache=MarketStore.for_portfolios(paper)
        fx={"status":"ok","executable":False,
            "usd_krw":DEMO_FX,"as_of":close.isoformat()}
        _record(cache,"FX","USD/KRW",fx,run_utc)
        for symbol,data in SYMBOLS.items():
            _record(cache,"PRICE",symbol,_quote(symbol,close),run_utc)
            _record(cache,"HISTORY",symbol,
                    _fixture_history(symbol,close,data["hypothetical_close"]),run_utc)
        prices={symbol:_quote(symbol,close) for symbol in SYMBOLS}
        engine=PortfolioEngine(paper,preferences,cache,policy=policy,
                               quote_provider=lambda s:prices[s].copy(),
                               fx_provider=lambda:dict(fx))
        starting=_accounts(paper)
        start_balance=sum(Decimal(x["cash_krw"]) for x in starting)
        assert start_balance==INITIAL_KRW
        assert all(not x["positions"] for x in starting)
        # Even if user activates the isolated mock engine, weekends must not
        # record trades. The real weekend behavior is never overridden.
        set_paused(DEMO_OWNER,paper,False,"isolated Stage8 test only")
        weekend=run_utc.astimezone(KST).date()-timedelta(days=2)
        while weekend.weekday()!=6:
            weekend-=timedelta(days=1)
        weekend_time=datetime.combine(weekend,time(19),tzinfo=KST).astimezone(UTC)
        closed=engine.run(DEMO_OWNER,now=weekend_time)
        assert closed["status"]=="market_closed"
        assert not any(x["positions"] for x in _accounts(paper))
        plan=engine.preview(DEMO_OWNER,now=run_utc)
        result=engine.run(DEMO_OWNER,now=run_utc)
        after=_accounts(paper)
        integrity=audit_paper_ledger(DEMO_OWNER,paper,now=run_utc)
        if integrity["status"]=="blocked":
            raise ValueError("Stage8 simulation ledger failed Stage7 preflight")
        plans={x["request_id"]:x for x in plan["proposed_orders"]}
        spent=sum((Decimal(plans[x["request_id"]]["notional_krw"])
                   for x in result.get("executed",[]) if x.get("side")=="BUY"),
                  Decimal(0))
        maximum=Decimal(plan["daily_spend_cap_krw"])
        if spent>maximum or spent>Decimal("200000"):
            raise ValueError("Stage8 scenario exceeded 5% risk budget")
        if result.get("status")!="completed":
            raise ValueError("Stage8 scenario did not complete: "+str(result.get("status")))
        if not result.get("executed"):
            raise ValueError("Stage8 scenario had no eligible illustrative order")
        check=engine.run(DEMO_OWNER,now=run_utc)
        if check.get("executed"):
            raise ValueError("Stage8 repeat triggered duplicate simulated fill")
        return {
            "status":"completed_sandbox_only",
            "trust_label":"합성 시세를 사용한 격리 테스트 · 실제 종가/투자추천/사용자 거래 아님",
            "scenario_for_kst":run_utc.astimezone(KST).date().isoformat(),
            "hypothetical_reference_as_of":close.isoformat(),
            "initial_cash_krw":str(INITIAL_KRW),
            "next_monthly_deposit_krw":str(MONTHLY_KRW),
            "strategy_example":{"weights":DEMO_WEIGHT,
                "allocation":DEMO_PREFERENCE,
                "note":"100% 지수전략·미국 75%/한국 25%·ETF 100%는 검증용 임시 설정으로 실제 사용자의 설정이 아닙니다."},
            "starting_accounts":starting,
            "market_closed_check":{"status":closed["status"],
                "orders_executed":len(closed["executed"])},
            "weekday_replay":{"status":result["status"],
                "risk_budget_krw":str(maximum),
                "estimated_spend_krw":str(spent),
                "orders":[{"symbol":x["symbol"],"side":x["side"],
                           "account":x["account"],"quantity":x["quantity"],
                           "krw_notional":plans[x["request_id"]]["notional_krw"],
                           "hypothetical_unit_price":SYMBOLS[x["symbol"]]["hypothetical_close"],
                           "unit_currency":"KRW" if x["symbol"].isdigit() else "USD",
                           "strategy_score":next((i["strategy_score"]
                               for i in plan["proposed_orders"]
                               if i["symbol"]==x["symbol"]),None),
                           "coverage_pct":next((i["coverage_pct"]
                               for i in plan["proposed_orders"]
                               if i["symbol"]==x["symbol"]),None)}
                          for x in result["executed"]],
                "skipped":result.get("skipped",[]),
                "ending_accounts":after,
                "integrity":integrity["status"],
                "repeat_run_new_orders":len(check.get("executed",[])),
                "funding_reconciled":start_balance==INITIAL_KRW},
            "provider_network_used":False,
            "ai_used":False,"broker_used":False,
            "active_user_ledger_touched":False,
            "simulated_orders_exported":False,
            "price_fixture":{"VOO":{"USD":"80"},
                             "360750":{"KRW":"20000"},"USD/KRW":"1400"},
            "important":["이 결과는 인공 과거가격과 임의 환율을 넣어 거래 엔진 안전성을 시험한 것입니다.",
                         "실제 지연 시세가 확보되기 전에는 동일 종목에 대한 주문을 보장하거나 권유할 수 없습니다.",
                         "주말에는 0건이며, 다음 거래일에도 현금·시세·리스크·사용자 설정에 따라 0건일 수 있습니다.",
                         "당일 매수 최대 5% 정책을 유지하므로 400만 원을 하루에 전액 투자하지 않습니다.",
                         "기존 사용자의 투자 기록을 초기화하거나 체결을 만들지 않습니다."]}


@lru_cache(maxsize=1)
def sandbox_preview():
    """One scenario per server process. Cache immutable facts, not live data."""
    run,close=next_simulated_session()
    return _uncommitted_scenario(run,close)
