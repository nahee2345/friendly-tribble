"""Seven no-key paper report cards; all missing data stays explicitly unknown.

This generates a current on-demand *preview*. It is not an automatically
completed 16:00 market report unless separately stored after that time.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal

from .stage1_controls import status as trade_status, list_approvals
from .stage1_diary import list_daily_paper_reports


def generate_cards(owner, portfolios, ledger, preference_store, strategy_policy, *,
                   today=None, accounting_engine=None):
    now=datetime.now(ZoneInfo("Asia/Seoul"))
    paper=ledger.state(owner,today or now.date())
    accounts=portfolios.list(owner)["accounts"]
    weights=strategy_policy.get(owner)["weights"]
    prefs=preference_store.get(owner)
    execution=trade_status(owner,portfolios)
    approvals=list_approvals(owner,portfolios)["items"]
    diaries=list_daily_paper_reports(owner,portfolios,limit=1)["items"]
    krw=sum((Decimal(a["krw_cash"]) for a in accounts),Decimal(0))
    usd=sum((Decimal(a["usd_cash"]) for a in accounts),Decimal(0))
    held=[{"account":a["id"],"symbol":h["symbol"],"quantity":h["quantity"]}
          for a in accounts for h in a["holdings"]]
    initial=int(paper["initial_krw"])
    contributions=sum(int(e["krw"]) for e in paper["events"] if e["kind"]=="deposit")
    cards=[
        {"id":1,"title":"오늘의 요약","status":"검증 대기",
         "result":"가상 주문 중지" if execution["paused"] else "가상 거래 검토 허용",
         "detail":"시장 지수·금융 뉴스의 정량 영향은 현재 카드에서 확인 불가",
         "data":{"paused":execution["paused"],"reason":execution["reason"]}},
        {"id":2,"title":"모의자산 현황","status":"부분 확인",
         "result":f"원화현금 {krw:,.0f}원 · 달러현금 {usd:,.2f}달러 · 보유종목 {len(held)}개",
         "detail":"검증된 시세·환율 없이 전체 평가금액과 손익을 임의로 계산하지 않음",
         "data":{"cash_krw":str(krw),"cash_usd":str(usd),
                 "contributed_krw":contributions,"positions":held}},
        {"id":3,"title":"목표 대비","status":"설정 확인",
         "result":f"미국 {prefs['us_weight']}% / 한국 {prefs['kr_weight']}% · ETF {prefs['etf_weight']}%",
         "detail":"8개 전략별 원금 기준 예산(가상 분배 목표). 실제 종목별 전략 귀속과 투자비중은 검증 전.",
         "data":{"strategy_budget_krw":{k:initial*v//100 for k,v in weights.items()},
                 "weights":weights}},
        {"id":4,"title":"배당","status":"데이터 미연결",
         "result":"확인된 배당 지급 기록 없음",
         "detail":"배당 정보가 없다고 배당이 0원이라고 단정할 수 없음",
         "data":{"verified_dividend_krw":None}},
        {"id":5,"title":"위험 상태","status":"설정 확인",
         "result":"긴급 중지" if execution["paused"] else "모의투자 실행 가능 상태",
         "detail":"지연시세 및 거래한도 재검증 필요. 포트폴리오 산업 집중도는 미계산.",
         "data":{"paused":execution["paused"],"holdings":len(held)}},
        {"id":6,"title":"거래 및 판단 검토","status":"기록 확인",
         "result":f"최근 보고서 {len(diaries)}건 · 승인 대기 {sum(x['status']=='PENDING' for x in approvals)}건",
         "detail":"거래 근거와 사후 실제 관측 비교는 외부 시장자료 검증 후 가능",
         "data":{"recent_report":diaries[0] if diaries else None}},
        {"id":7,"title":"주의사항과 다음 확인","status":"추가 확인",
         "result":"지연시세 검증·승인 대기·원장 재확인",
         "detail":"16시 확정 보고서가 아닌 현재 시각의 요청 시점 미리보기.",
         "data":{"as_of_kst":now.isoformat(),"needs_provider":True}},
    ]
    # Stage5 is optional so earlier standalone Stage1 tests retain the
    # original cash-only cards. Never treat ex-date evidence as a payout.
    if accounting_engine is not None:
        try:
            a=accounting_engine.report(owner)
            if a["valuation_complete"]:
                cards[1]["status"]="검증된 총평가 · 미정산 비용 별도"
                cards[1]["result"]=(
                    "가상 총자산 ₩"+format(Decimal(a["market_valuation_krw"]),",.0f")
                    +" · 입금 대비 세전손익 ₩"+
                    format(Decimal(a["gross_change_after_contributions_krw"]),",.0f"))
                cards[1]["detail"]="검증 공개 종가·환율 기준. 수수료·세금·실제 배당 입금 전 총손익."
                cards[1]["data"]["valuation_krw"]=a["market_valuation_krw"]
                cards[1]["data"]["gross_change_krw"]=a["gross_change_after_contributions_krw"]
            else:
                cards[1]["detail"]="회계평가 자료 누락: "+("; ".join(a["missing"][:3]) or "검증 대기")
            cards[3]["status"]="배당락일 연구·지급 미검증"
            cards[3]["result"]=f"배당락일 권리 가능성 {len(a['potential_dividends'])}건 · 지급 확인 0건"
            cards[3]["detail"]="실제 지급 여부 미검증. 입금액 0원으로 단정하거나 잔액에 반영하지 않음."
            cards[3]["data"]["possible_entitlements"]=len(a["potential_dividends"])
            cards[3]["data"]["verified_dividend_krw"]=None
            cards[5]["data"]["xirr_annual_pct"]=a["xirr"]["pct"]
            cards[5]["data"]["twr_observed_pct"]=a["twr"]["pct"]
            if a["xirr"]["status"]=="ok":
                cards[5]["detail"]+=f" · 입금일 반영 XIRR 연환산 {a['xirr']['pct']:.2f}% (세전·가상)"
            cards[6]["detail"]+=" · 거래비용 추정/세후 시나리오는 실제 납부나 입금이 아닙니다."
        except (ValueError,KeyError,ArithmeticError):
            cards[1]["detail"]="정밀 손익원장 미검증. 기초 현금 자료만 표시."
    return {"kind":"PREVIEW","generated_kst":now.isoformat(),
            "for_date":(today or now.date()).isoformat(),"cards":cards,
            "simulated_only":True,
            "note":"외부 데이터 없이 계산 가능한 카드만 표시; 배당/세금/시장정보는 연결 전 미확인."}


def store_daily_cards_after_16(owner,portfolios,ledger,preference_store,strategy_policy,*,
                               now=None,accounting_engine=None):
    """Persist one owner/day snapshot after 16 KST; mark late snapshots explicitly."""
    stamp=now or datetime.now(ZoneInfo("Asia/Seoul"))
    if stamp.tzinfo is None:
        raise ValueError("timezone required")
    stamp=stamp.astimezone(ZoneInfo("Asia/Seoul"))
    if stamp.hour<16:
        return {"status":"not_due","report_date":stamp.date().isoformat()}
    report_date=stamp.date().isoformat()
    with portfolios._db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS stage1_seven_card_reports (
          owner TEXT NOT NULL, report_date TEXT NOT NULL,
          generated_kst TEXT NOT NULL, delayed INTEGER NOT NULL,
          snapshot_json TEXT NOT NULL, PRIMARY KEY(owner,report_date)
        )""")
        prior=db.execute("SELECT generated_kst,delayed FROM stage1_seven_card_reports WHERE owner=? AND report_date=?",
                         (owner,report_date)).fetchone()
        if prior:
            return {"status":"already_saved","report_date":report_date,
                    "generated_kst":prior[0],"delayed":bool(prior[1])}
    snapshot=generate_cards(owner,portfolios,ledger,preference_store,strategy_policy,
                            today=stamp.date(),accounting_engine=accounting_engine)
    snapshot["kind"]="SCHEDULED_SNAPSHOT"
    snapshot["generated_kst"]=stamp.isoformat()
    delayed=stamp.hour>16 or (stamp.hour==16 and stamp.minute>=30)
    snapshot["late_generation"]=delayed
    with portfolios._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT OR IGNORE INTO stage1_seven_card_reports
          (owner,report_date,generated_kst,delayed,snapshot_json) VALUES(?,?,?,?,?)""",
          (owner,report_date,stamp.isoformat(),int(delayed),json.dumps(snapshot,ensure_ascii=False)))
        db.execute("COMMIT")
    return {"status":"saved","report_date":report_date,
            "generated_kst":stamp.isoformat(),"delayed":delayed}


def list_saved_daily_cards(owner,portfolios,limit=30):
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError("invalid report limit")
    with portfolios._db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS stage1_seven_card_reports (
          owner TEXT NOT NULL, report_date TEXT NOT NULL,
          generated_kst TEXT NOT NULL, delayed INTEGER NOT NULL,
          snapshot_json TEXT NOT NULL, PRIMARY KEY(owner,report_date)
        )""")
        rows=db.execute("""SELECT snapshot_json FROM stage1_seven_card_reports
                           WHERE owner=? ORDER BY report_date DESC LIMIT ?""",(owner,limit)).fetchall()
    return {"items":[json.loads(row[0]) for row in rows]}
