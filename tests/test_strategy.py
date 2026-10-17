from __future__ import annotations

import unittest
from decimal import Decimal

from asset_assistant.domain import DomainValidationError
from asset_assistant.strategy import (
    AssessmentStatus,
    CandidateInput,
    StrategyKind,
    StrategyMix,
    check_combined_exposures,
    evaluate_candidate,
)


class StrategyTests(unittest.TestCase):
    def test_mix_must_equal_exactly_one_hundred_and_is_not_asset_allocation(self) -> None:
        mix = StrategyMix(
            "v1",
            {StrategyKind.QUALITY: Decimal("60"), StrategyKind.VALUE: Decimal("40")},
        )
        self.assertEqual(sum(mix.weights.values()), Decimal("100"))
        with self.assertRaises(DomainValidationError):
            StrategyMix("bad", {StrategyKind.QUALITY: Decimal("99.99")})

    def test_missing_inputs_are_unavailable_not_zero(self) -> None:
        mix = StrategyMix("v1", {StrategyKind.MOMENTUM: Decimal("100")})
        assessment = evaluate_candidate(CandidateInput("TEST"), mix)
        self.assertEqual(assessment.status, AssessmentStatus.UNAVAILABLE)
        self.assertIsNone(assessment.total_score)
        self.assertIn("자료 부족", assessment.counter_reasons[0])

    def test_reproducible_metrics_and_weighted_score(self) -> None:
        prices = tuple(Decimal(index) for index in range(100, 226))
        volumes = tuple(Decimal("100") for _ in range(20)) + (Decimal("200"),)
        candidate = CandidateInput(
            "TEST",
            prices=prices,
            volumes=volumes,
            trading_values=(Decimal("123456789"),),
            quality_score=Decimal("80"),
            disclosure_or_news_risk=False,
            product_policy_verified=True,
        )
        mix = StrategyMix(
            "v1",
            {StrategyKind.QUALITY: Decimal("50"), StrategyKind.MOMENTUM: Decimal("50")},
        )
        assessment = evaluate_candidate(candidate, mix)
        self.assertEqual(assessment.status, AssessmentStatus.ELIGIBLE)
        self.assertEqual(assessment.metrics.relative_volume_20d, Decimal("2"))
        self.assertEqual(assessment.metrics.latest_trading_value, Decimal("123456789"))
        self.assertIsNotNone(assessment.metrics.annualized_volatility)
        self.assertIsNotNone(assessment.total_score)

    def test_prohibited_product_is_excluded_before_score(self) -> None:
        mix = StrategyMix("v1", {StrategyKind.QUALITY: Decimal("100")})
        assessment = evaluate_candidate(
            CandidateInput("LEVERAGED", quality_score=Decimal("100"), prohibited_product=True),
            mix,
        )
        self.assertEqual(assessment.status, AssessmentStatus.EXCLUDED)
        self.assertIsNone(assessment.total_score)

    def test_complete_metrics_do_not_bypass_unverified_product_policy(self) -> None:
        mix = StrategyMix("v1", {StrategyKind.QUALITY: Decimal("100")})
        assessment = evaluate_candidate(
            CandidateInput(
                "UNVERIFIED",
                quality_score=Decimal("90"),
                disclosure_or_news_risk=False,
            ),
            mix,
        )
        self.assertEqual(assessment.status, AssessmentStatus.UNAVAILABLE)
        self.assertIsNone(assessment.total_score)
        self.assertTrue(any("금지상품" in reason for reason in assessment.counter_reasons))

    def test_duplicate_instrument_exposure_is_aggregated_across_strategies(self) -> None:
        checked = check_combined_exposures(
            {
                StrategyKind.QUALITY: {"STOCK": Decimal("3"), "ETF": Decimal("25")},
                StrategyKind.VALUE: {"STOCK": Decimal("3"), "ETF": Decimal("20")},
                StrategyKind.SATELLITE_THEME: {"SAT": Decimal("10.01")},
            },
            etf_instruments=frozenset({"ETF"}),
            satellite_instruments=frozenset({"SAT"}),
        )
        self.assertEqual(checked.instrument_weights["STOCK"], Decimal("6"))
        self.assertTrue(any("기본 한도 5% 초과" in value for value in checked.violations))
        self.assertTrue(any("기본 한도 40% 초과" in value for value in checked.violations))
        self.assertTrue(any("위성 합산 비중 10% 초과" in value for value in checked.violations))

    def test_only_explicitly_approved_broad_index_etf_gets_fifty_percent_cap(self) -> None:
        at_limits = check_combined_exposures(
            {
                StrategyKind.BROAD_MARKET: {
                    "NORMAL_ETF": Decimal("40"),
                    "APPROVED_INDEX": Decimal("50"),
                }
            },
            etf_instruments=frozenset({"NORMAL_ETF", "APPROVED_INDEX"}),
            satellite_instruments=frozenset(),
            approved_broad_index_etfs=frozenset({"APPROVED_INDEX"}),
        )
        self.assertEqual(at_limits.violations, ())

        exceeded = check_combined_exposures(
            {
                StrategyKind.BROAD_MARKET: {
                    "NORMAL_ETF": Decimal("40.01"),
                    "APPROVED_INDEX": Decimal("50.01"),
                }
            },
            etf_instruments=frozenset({"NORMAL_ETF", "APPROVED_INDEX"}),
            satellite_instruments=frozenset(),
            approved_broad_index_etfs=frozenset({"APPROVED_INDEX"}),
        )
        self.assertTrue(
            any("NORMAL_ETF" in value and "40%" in value for value in exceeded.violations)
        )
        self.assertTrue(
            any("APPROVED_INDEX" in value and "50%" in value for value in exceeded.violations)
        )

        with self.assertRaises(DomainValidationError):
            check_combined_exposures(
                {StrategyKind.BROAD_MARKET: {"NOT_AN_ETF": Decimal("1")}},
                etf_instruments=frozenset(),
                satellite_instruments=frozenset(),
                approved_broad_index_etfs=frozenset({"NOT_AN_ETF"}),
            )


if __name__ == "__main__":
    unittest.main()
