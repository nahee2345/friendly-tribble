from __future__ import annotations

import unittest
from decimal import Decimal

from asset_assistant.calculations import (
    allocate_monthly_surplus,
    display_won,
    planned_monthly_living_expense,
    recalculate_next_year_goal,
    required_month_end_contribution,
    simulate_month_end_cashflows,
)
from asset_assistant.domain import DomainValidationError, PolicyStatus
from asset_assistant.satellite import assess_satellite_loss


class CashflowTests(unittest.TestCase):
    def test_planned_living_expense_examples(self) -> None:
        self.assertEqual(planned_monthly_living_expense(2028), Decimal("850000"))
        self.assertEqual(planned_monthly_living_expense(2043), Decimal("2350000"))
        self.assertEqual(planned_monthly_living_expense(2064), Decimal("4450000"))

    def test_plan_year_outside_range_is_not_invented(self) -> None:
        with self.assertRaises(DomainValidationError):
            planned_monthly_living_expense(2027)

    def test_positive_surplus_preserves_every_won(self) -> None:
        result = allocate_monthly_surplus(
            net_income_krw="2585469", essential_expenses_krw="850000"
        )
        self.assertEqual(result.surplus_krw, Decimal("1735469"))
        self.assertEqual(result.investment_krw, Decimal("1388375"))
        self.assertEqual(result.protected_savings_krw, Decimal("347094"))
        self.assertEqual(
            result.investment_krw + result.protected_savings_krw,
            result.surplus_krw,
        )
        self.assertEqual(result.rounding_policy_status, PolicyStatus.PROPOSED_UNCONFIRMED)

    def test_one_won_surplus_never_exceeds_available_cash(self) -> None:
        result = allocate_monthly_surplus(
            net_income_krw="850001", essential_expenses_krw="850000"
        )
        self.assertEqual(result.investment_krw, Decimal("0"))
        self.assertEqual(result.protected_savings_krw, Decimal("1"))

    def test_zero_and_negative_surplus_do_not_create_negative_investment(self) -> None:
        zero = allocate_monthly_surplus(
            net_income_krw="850000", essential_expenses_krw="850000"
        )
        negative = allocate_monthly_surplus(
            net_income_krw="800000", essential_expenses_krw="850000"
        )
        self.assertEqual(zero.investment_krw, Decimal("0"))
        self.assertEqual(zero.protected_savings_krw, Decimal("0"))
        self.assertEqual(negative.investment_krw, Decimal("0"))
        self.assertEqual(negative.protected_savings_krw, Decimal("0"))
        self.assertEqual(negative.shortfall_krw, Decimal("50000"))


class GoalTests(unittest.TestCase):
    def test_user_recalculation_formula(self) -> None:
        result = recalculate_next_year_goal(
            actual_year_end_assets_krw="4000000",
            planned_next_year_investment_krw="12000000",
        )
        self.assertEqual(result.recalculated_base_krw, Decimal("16600000.00"))
        self.assertEqual(result.five_percent_assumption_line_krw, Decimal("16500000.000"))
        self.assertEqual(result.warning_line_krw, Decimal("14110000.0000"))

    def test_partial_year_required_payment_reproduces_policy_example(self) -> None:
        payment = required_month_end_contribution(
            initial_assets_krw="4000000",
            target_assets_krw="14000000",
            annual_total_return="0.06",
            months=7,
        )
        rounded_payment = display_won(payment)
        self.assertEqual(rounded_payment, Decimal("1388375"))
        projection = simulate_month_end_cashflows(
            initial_assets_krw="4000000",
            annual_total_return="0.06",
            contributions_krw=[rounded_payment] * 7,
        )
        self.assertLess(
            abs(projection.ending_balance_krw - Decimal("14000000")),
            Decimal("5"),
        )

    def test_total_return_api_has_no_separate_dividend_addition(self) -> None:
        projection = simulate_month_end_cashflows(
            initial_assets_krw="1000000",
            annual_total_return="0.06",
            contributions_krw=["0"] * 12,
        )
        self.assertEqual(display_won(projection.ending_balance_krw), Decimal("1060000"))

    def test_float_money_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            recalculate_next_year_goal(
                actual_year_end_assets_krw=4_000_000.0,
                planned_next_year_investment_krw="12000000",
            )

    def test_krw_scientific_notation_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            recalculate_next_year_goal(
                actual_year_end_assets_krw="4e6",
                planned_next_year_investment_krw="12000000",
            )


class SatelliteTests(unittest.TestCase):
    def test_budget_and_boundaries(self) -> None:
        below = assess_satellite_loss(
            expected_annual_new_investment_krw="12000000", measured_loss_krw="599999"
        )
        half = assess_satellite_loss(
            expected_annual_new_investment_krw="12000000", measured_loss_krw="600000"
        )
        full = assess_satellite_loss(
            expected_annual_new_investment_krw="12000000", measured_loss_krw="1200000"
        )
        self.assertEqual(below.annual_risk_budget_krw, Decimal("1200000.00"))
        self.assertEqual(below.pause_threshold_krw, Decimal("600000.0000"))
        self.assertEqual(below.state, "BELOW_ALERT_THRESHOLD")
        self.assertEqual(half.state, "PAUSED_REQUIRES_REVIEW")
        self.assertEqual(full.state, "LOCKED_UNTIL_YEAR_END")

    def test_odd_half_threshold_is_not_rounded_down(self) -> None:
        below = assess_satellite_loss(
            expected_annual_new_investment_krw="10000010", measured_loss_krw="500000"
        )
        tiny = assess_satellite_loss(
            expected_annual_new_investment_krw="10", measured_loss_krw="0"
        )
        self.assertEqual(below.pause_threshold_krw, Decimal("500000.5000"))
        self.assertEqual(below.state, "BELOW_ALERT_THRESHOLD")
        self.assertEqual(tiny.state, "BELOW_ALERT_THRESHOLD")

    def test_fractional_boundary_example_keeps_exact_decimal_judgment(self) -> None:
        result = assess_satellite_loss(
            expected_annual_new_investment_krw="12000005",
            measured_loss_krw="600000",
        )
        self.assertEqual(result.annual_risk_budget_krw, Decimal("1200000.50"))
        self.assertEqual(result.pause_threshold_krw, Decimal("600000.2500"))
        self.assertEqual(result.state, "BELOW_ALERT_THRESHOLD")

        paused = assess_satellite_loss(
            expected_annual_new_investment_krw="12000005",
            measured_loss_krw="600001",
        )
        just_below_lock = assess_satellite_loss(
            expected_annual_new_investment_krw="12000005",
            measured_loss_krw="1200000",
        )
        locked = assess_satellite_loss(
            expected_annual_new_investment_krw="12000005",
            measured_loss_krw="1200001",
        )
        self.assertEqual(paused.state, "PAUSED_REQUIRES_REVIEW")
        self.assertEqual(just_below_lock.state, "PAUSED_REQUIRES_REVIEW")
        self.assertEqual(locked.state, "LOCKED_UNTIL_YEAR_END")

    def test_zero_or_missing_budget_does_not_create_permission(self) -> None:
        zero = assess_satellite_loss(
            expected_annual_new_investment_krw="0", measured_loss_krw="0"
        )
        missing = assess_satellite_loss(
            expected_annual_new_investment_krw="12000000", measured_loss_krw=None
        )
        self.assertEqual(zero.state, "BUDGET_ZERO_BLOCKED")
        self.assertEqual(missing.state, "CALCULATION_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
