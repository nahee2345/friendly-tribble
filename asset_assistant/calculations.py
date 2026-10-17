"""PROJECT_RULES의 확정 계산식을 구현한 순수 함수 모음."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, localcontext

from .domain import DomainValidationError, PolicyStatus, exact_decimal, krw_amount


INVESTMENT_SHARE = Decimal("0.80")
PROTECTED_SHARE = Decimal("0.20")
DISPLAY_WON = Decimal("1")


@dataclass(frozen=True)
class CashflowAllocation:
    net_income_krw: Decimal
    essential_expenses_krw: Decimal
    surplus_krw: Decimal
    investment_krw: Decimal
    protected_savings_krw: Decimal
    shortfall_krw: Decimal
    rounding_policy: str
    rounding_policy_status: PolicyStatus


@dataclass(frozen=True)
class GoalRecalculation:
    recalculated_base_krw: Decimal
    five_percent_assumption_line_krw: Decimal
    warning_line_krw: Decimal


@dataclass(frozen=True)
class MonthlyProjection:
    monthly_rate: Decimal
    balances_krw: tuple[Decimal, ...]

    @property
    def ending_balance_krw(self) -> Decimal:
        return self.balances_krw[-1]


def planned_monthly_living_expense(year: int) -> Decimal:
    """2028~2064 계획 구간의 월 생활비를 반환합니다."""

    if isinstance(year, bool) or not isinstance(year, int):
        raise DomainValidationError("year는 정수여야 합니다.")
    if not 2028 <= year <= 2064:
        raise DomainValidationError("생활비 계획식은 2028~2064 구간에만 정의되어 있습니다.")
    return Decimal(850_000 + 100_000 * (year - 2028))


def allocate_monthly_surplus(
    *, net_income_krw: object, essential_expenses_krw: object
) -> CashflowAllocation:
    """양의 잉여금만 80:20으로 배정합니다.

    원 단위 반올림은 아직 사용자 확정 전입니다. 첫 OFFLINE 구현은 투자 몫을
    내림하고 나머지를 보호저축에 두어 합계 초과를 막는 제안 기본값을 명시합니다.
    """

    income = krw_amount(net_income_krw, field_name="net_income_krw")
    expenses = krw_amount(essential_expenses_krw, field_name="essential_expenses_krw")
    surplus = income - expenses
    rounding_name = "투자 80% 원 단위 내림, 잔여 전액 보호저축 배정"
    if surplus <= 0:
        return CashflowAllocation(
            net_income_krw=income,
            essential_expenses_krw=expenses,
            surplus_krw=surplus,
            investment_krw=Decimal("0"),
            protected_savings_krw=Decimal("0"),
            shortfall_krw=-surplus,
            rounding_policy=rounding_name,
            rounding_policy_status=PolicyStatus.PROPOSED_UNCONFIRMED,
        )
    investment = (surplus * INVESTMENT_SHARE).quantize(DISPLAY_WON, rounding=ROUND_DOWN)
    protected = surplus - investment
    return CashflowAllocation(
        net_income_krw=income,
        essential_expenses_krw=expenses,
        surplus_krw=surplus,
        investment_krw=investment,
        protected_savings_krw=protected,
        shortfall_krw=Decimal("0"),
        rounding_policy=rounding_name,
        rounding_policy_status=PolicyStatus.PROPOSED_UNCONFIRMED,
    )


def recalculate_next_year_goal(
    *, actual_year_end_assets_krw: object, planned_next_year_investment_krw: object
) -> GoalRecalculation:
    actual = krw_amount(actual_year_end_assets_krw, field_name="actual_year_end_assets_krw")
    planned = krw_amount(
        planned_next_year_investment_krw,
        field_name="planned_next_year_investment_krw",
    )
    base = actual * Decimal("1.06") + planned * Decimal("1.03")
    minimum = actual * Decimal("1.05") + planned * Decimal("1.025")
    warning = base * Decimal("0.85")
    return GoalRecalculation(base, minimum, warning)


def effective_monthly_rate(annual_total_return: object) -> Decimal:
    annual = exact_decimal(annual_total_return, field_name="annual_total_return")
    if annual <= Decimal("-1"):
        raise DomainValidationError("연 총수익률은 -100%보다 커야 합니다.")
    with localcontext() as context:
        context.prec = 50
        return (Decimal("1") + annual) ** (Decimal("1") / Decimal(12)) - Decimal("1")


def simulate_month_end_cashflows(
    *,
    initial_assets_krw: object,
    annual_total_return: object,
    contributions_krw: tuple[object, ...] | list[object],
    withdrawals_krw: tuple[object, ...] | list[object] | None = None,
) -> MonthlyProjection:
    """총수익률을 사용해 월말 납입/외부인출을 계산합니다.

    배당률 인수를 별도로 받지 않으므로 총수익률과 배당을 이중 가산할 수 없습니다.
    계산 중에는 원 단위로 조기 반올림하지 않습니다.
    """

    initial = krw_amount(initial_assets_krw, field_name="initial_assets_krw")
    contributions = tuple(
        krw_amount(value, field_name=f"contributions_krw[{index}]")
        for index, value in enumerate(contributions_krw)
    )
    if withdrawals_krw is None:
        withdrawals = (Decimal("0"),) * len(contributions)
    else:
        withdrawals = tuple(
            krw_amount(value, field_name=f"withdrawals_krw[{index}]")
            for index, value in enumerate(withdrawals_krw)
        )
    if len(contributions) != len(withdrawals):
        raise DomainValidationError("납입과 외부인출의 월 개수가 같아야 합니다.")

    rate = effective_monthly_rate(annual_total_return)
    balance = initial
    balances: list[Decimal] = []
    with localcontext() as context:
        context.prec = 50
        for contribution, withdrawal in zip(contributions, withdrawals, strict=True):
            balance = balance * (Decimal("1") + rate) + contribution - withdrawal
            balances.append(+balance)
    if not balances:
        balances.append(initial)
    return MonthlyProjection(rate, tuple(balances))


def required_month_end_contribution(
    *,
    initial_assets_krw: object,
    target_assets_krw: object,
    annual_total_return: object,
    months: int,
) -> Decimal:
    """동일한 월말 납입액을 역산하며 계산 중 원 단위 반올림은 하지 않습니다."""

    initial = krw_amount(initial_assets_krw, field_name="initial_assets_krw")
    target = krw_amount(target_assets_krw, field_name="target_assets_krw")
    if isinstance(months, bool) or not isinstance(months, int) or months <= 0:
        raise DomainValidationError("months는 1 이상의 정수여야 합니다.")
    rate = effective_monthly_rate(annual_total_return)
    with localcontext() as context:
        context.prec = 50
        growth = (Decimal("1") + rate) ** months
        if rate == 0:
            payment = (target - initial) / Decimal(months)
        else:
            annuity_factor = (growth - Decimal("1")) / rate
            payment = (target - initial * growth) / annuity_factor
    return max(payment, Decimal("0"))


def display_won(value: Decimal) -> Decimal:
    """보고서 표시만 원 단위 반올림합니다. 검사 계산에는 사용하지 않습니다."""

    return value.quantize(DISPLAY_WON, rounding=ROUND_HALF_UP)

