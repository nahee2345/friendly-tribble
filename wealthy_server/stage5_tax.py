"""Stage 5 paper-tax ILLUSTRATIONS; never posts tax, books dividends or files returns.

As-of 2026 research reference assumptions; real withholding for Korean overseas
index ETFs uses the tax-base NAV increment and may differ from market gain.
The ISA account's term, contribution/withdrawal status, allowance category,
total eligible offset and foreign withholding are not tracked here.
Therefore scenarios are comparisons ONLY, not incurred or payable tax.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION="2026-illustration-v1"
# Historical model assumptions, explicitly NOT a 2026 user-specific tax ruling.
DOMESTIC_OTHER_ETF_RATE=Decimal("0.154")
ISA_EXCESS_RATE=Decimal("0.099")
ISA_STANDARD_ALLOWANCE=Decimal("2000000")
ISA_PREFERENTIAL_ALLOWANCE=Decimal("4000000")
OVERSEAS_CAPITAL_GAIN_RATE=Decimal("0.22")
OVERSEAS_ANNUAL_DEDUCTION=Decimal("2500000")
# Asset tax categories must be *audited* instrument by instrument.
TAX_CLASS={
    "069500":"DOMESTIC_EQUITY_ETF",
    "360750":"DOMESTIC_FOREIGN_EQUITY_ETF",
}
RESEARCH_URLS=[
    "https://korea.nabo.go.kr/board/file/down.do?fid=33319156",
    "https://www.samsungfund.com/etf/pension/isa.do",
]
WARNING=("시나리오 계산용이며 신고·납부·원천징수 확정액이 아닙니다. "
         "실제 과세표준기준가, 종합소득, 세제 변경, 가입자 자격, ISA 만기, "
         "다른 금융기관 이익·손실과 외국납부세액은 별도 확인해야 합니다.")


def money(value):
    if value is None or isinstance(value,bool):
        raise ValueError("missing/nonfinite tax scenario")
    try: n=Decimal(str(value))
    except (ValueError,InvalidOperation,TypeError):
        raise ValueError("invalid tax scenario") from None
    if not n.is_finite() or abs(n)>Decimal("1000000000000"):
        raise ValueError("invalid magnitude")
    return n


def _krw(value):
    return str(value.quantize(Decimal("1"),rounding=ROUND_HALF_UP))


def instrument_class(symbol,account_kind):
    if not isinstance(symbol,str):return "UNKNOWN"
    if account_kind=="GENERAL_OVERSEAS" and not symbol.isdigit():
        return "FOREIGN_LISTED"
    if account_kind in ("GENERAL_DOMESTIC","ISA_SIMULATED"):
        return TAX_CLASS.get(symbol,"UNKNOWN")
    return "UNKNOWN"


def domestic_isa_scenario(eligible_gain_krw,*,allowance_type="standard"):
    """Compare same eligible Korean-listed, foreign-assets ETF profit under assumptions.

    Profit supplied is a PROXY market gain; there is no proof that it equals
    tax-base NAV change, actual ISA lifetime net gain, or future realizations.
    """
    gain=money(eligible_gain_krw)
    if allowance_type not in ("standard","preferential"):
        raise ValueError("unknown illustrative ISA allowance category")
    allowance=(ISA_STANDARD_ALLOWANCE if allowance_type=="standard"
               else ISA_PREFERENTIAL_ALLOWANCE)
    positive=max(Decimal(0),gain)
    ordinary=(positive*DOMESTIC_OTHER_ETF_RATE).quantize(Decimal("1"),rounding=ROUND_HALF_UP)
    isa=(max(Decimal(0),positive-allowance)*ISA_EXCESS_RATE).quantize(
        Decimal("1"),rounding=ROUND_HALF_UP)
    return {"scope":"KOREAN_LISTED_FOREIGN_EQUITY_ETF_ONLY",
            "status":"illustrative_only","taxable_gain_proxy_krw":_krw(gain),
            "isa_allowance_type":allowance_type,
            "isa_allowance_assumed_krw":_krw(allowance),
            "general_scenario_tax_krw":_krw(ordinary),
            "isa_scenario_tax_krw":_krw(isa),
            "general_scenario_after_tax_gain_krw":_krw(gain-ordinary),
            "isa_scenario_after_tax_gain_krw":_krw(gain-isa),
            "potential_isa_saving_krw":_krw(ordinary-isa),
            "general_rate_assumed_pct":"15.4","isa_excess_rate_assumed_pct":"9.9",
            "deduction_not_verified":True,"not_actual_tax":True,
            "sources":RESEARCH_URLS,"warning":WARNING}


def foreign_ordinary_scenario(total_annual_gain_krw):
    """US-listed gain tax illustration ONLY, not comparable with domestic ISA."""
    gain=money(total_annual_gain_krw)
    positive=max(Decimal(0),gain-OVERSEAS_ANNUAL_DEDUCTION)
    approximate=(positive*OVERSEAS_CAPITAL_GAIN_RATE).quantize(
        Decimal("1"),rounding=ROUND_HALF_UP)
    return {"scope":"FOREIGN_LISTED_GENERAL_ACCOUNT_ONLY",
            "status":"illustrative_only","annual_krw_gain_proxy":_krw(gain),
            "deduction_assumed_krw":_krw(OVERSEAS_ANNUAL_DEDUCTION),
            "rate_assumed_pct":"22",
            "general_scenario_tax_krw":_krw(approximate),
            "general_scenario_after_tax_gain_krw":_krw(gain-approximate),
            "isa_eligible":False,"isa_comparison":None,"not_actual_tax":True,
            "warning":("해외상장 주식·ETF는 국내 중개형 ISA 직접 매매 대상이 아닙니다. "
                       "연간 다른 해외주식 매도손익·매수/매도일 적용환율·기타 공제 "
                       "정보가 없으므로 예상 신고세액이 아닙니다."),
            "sources":RESEARCH_URLS}


def research_comparison(positions,*,allowance_type="standard"):
    """All inputs are trade-ledger-reconciled display positions.

    Only KR domestic-listed overseas ETF gains enter the ISA comparison.
    Cash dividends, general-stock capital gains and foreign listed products
    MUST NOT be silently mixed into this taxable proxy.
    """
    gain=Decimal(0);sources=[];excluded=[];unknown=[]
    for row in positions:
        if not isinstance(row,dict):
            raise ValueError("invalid position")
        category=instrument_class(row.get("symbol"),row.get("account_kind"))
        estimated=row.get("unrealized_native")
        if category=="DOMESTIC_FOREIGN_EQUITY_ETF":
            if row.get("currency")!="KRW" or estimated is None:
                unknown.append(row.get("symbol"))
                continue
            gain+=money(estimated)
            sources.append(row["symbol"])
        elif category=="DOMESTIC_EQUITY_ETF":
            excluded.append({"symbol":row.get("symbol"),"reason":"국내주식형 ETF 매매차익은 일반계좌 과세 대상과 구분"})
        elif category=="FOREIGN_LISTED":
            excluded.append({"symbol":row.get("symbol"),"reason":"해외상장 종목은 국내 ISA에 직접 편입 불가"})
        else:
            excluded.append({"symbol":row.get("symbol"),"reason":"상품별 과세유형 미검증"})
    if unknown:
        return {"status":"incomplete","missing":sorted(set(unknown)),
                "domestic_isa":None,"foreign_isa_eligible":False,
                "warning":"해외지수 국내상장 ETF 평가손익 일부를 검증할 수 없어 비교 보류."}
    scenario=domestic_isa_scenario(gain,allowance_type=allowance_type)
    scenario["included_symbols"]=sorted(set(sources))
    scenario["basis"]="보유종목을 현재 지연 종가로 모두 매도한다는 가정, 실현되지 않은 손익"
    return {"status":"illustrative_only","domestic_isa":scenario,
            "excluded":excluded,"foreign_isa_eligible":False,
            "not_paid_or_posted":True,"warning":WARNING}
