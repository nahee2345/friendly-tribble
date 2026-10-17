from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from asset_assistant.allocation import (
    SatelliteCapStatus,
    allocation_policy_for_year,
    review_allocation,
)
from asset_assistant.domain import (
    Account,
    AssetClass,
    DataRealm,
    PositionRecord,
    Purpose,
    UnconfirmedPolicyError,
)
from asset_assistant.ledger import PortfolioLedger


NOW = datetime(2028, 1, 1, tzinfo=timezone.utc)


def allocation_ledger(values: dict[AssetClass, int]) -> PortfolioLedger:
    acct = Account("sample", "가상", "TEST", DataRealm.SAMPLE, Purpose.INVESTMENT)
    positions = [
        PositionRecord(
            f"p-{asset_class.value}",
            "sample",
            asset_class.value,
            asset_class,
            Decimal(value),
            NOW,
            source="test fixture",
        )
        for asset_class, value in values.items()
    ]
    return PortfolioLedger(realm=DataRealm.SAMPLE, accounts=[acct], positions=positions)


class AllocationTests(unittest.TestCase):
    def test_initial_whole_portfolio_and_country_targets(self) -> None:
        policy = allocation_policy_for_year(2028)
        targets = policy.whole_portfolio_targets
        self.assertEqual(targets[AssetClass.CORE_EQUITY], Decimal("0.6300"))
        self.assertEqual(targets[AssetClass.CORE_BOND], Decimal("0.2250"))
        self.assertEqual(targets[AssetClass.CORE_CASH], Decimal("0.0450"))
        self.assertEqual(targets[AssetClass.SATELLITE], Decimal("0.10"))
        self.assertEqual(sum(targets.values()), Decimal("1.0000"))
        self.assertEqual(policy.country_targets["US"], Decimal("0.441000"))
        self.assertEqual(policy.country_targets["KR"], Decimal("0.189000"))

    def test_phase_boundaries_and_unconfirmed_last_phase(self) -> None:
        self.assertEqual(allocation_policy_for_year(2040).year_start, 2028)
        self.assertEqual(allocation_policy_for_year(2041).year_start, 2041)
        self.assertEqual(allocation_policy_for_year(2053).year_start, 2053)
        self.assertEqual(allocation_policy_for_year(2061).year_end, 2061)
        with self.assertRaises(UnconfirmedPolicyError):
            allocation_policy_for_year(2062)

    def test_exact_target_allocation(self) -> None:
        ledger = allocation_ledger(
            {
                AssetClass.CORE_EQUITY: 2_520_000,
                AssetClass.CORE_BOND: 900_000,
                AssetClass.CORE_CASH: 180_000,
                AssetClass.SATELLITE: 400_000,
            }
        )
        review = review_allocation(ledger, year=2028)
        self.assertTrue(review.complete)
        self.assertTrue(all(line.deviation_percentage_points == 0 for line in review.lines))
        self.assertTrue(all(line.proposed_review is False for line in review.lines))
        self.assertEqual(len(review.lines), 3)
        self.assertEqual(review.satellite_cap.actual_ratio, Decimal("0.10"))
        self.assertEqual(review.satellite_cap.status, SatelliteCapStatus.AT_CAP)

    def test_core_proposed_five_percentage_point_boundary_uses_core_denominator(self) -> None:
        def equity_line(equity: int):
            ledger = allocation_ledger(
                {
                    AssetClass.CORE_EQUITY: equity,
                    AssetClass.CORE_BOND: 100_000 - equity,
                    AssetClass.CORE_CASH: 0,
                    AssetClass.SATELLITE: 10_000,
                }
            )
            return review_allocation(ledger, year=2028).lines[0]

        self.assertTrue(equity_line(64_999).proposed_review)
        self.assertTrue(equity_line(65_000).proposed_review)
        self.assertFalse(equity_line(65_001).proposed_review)

    def test_satellite_cap_boundaries_are_separate_from_core_review(self) -> None:
        cases = (
            (0, SatelliteCapStatus.BELOW_CAP, Decimal("0")),
            (100_000, SatelliteCapStatus.AT_CAP, Decimal("0.10")),
            (100_100, SatelliteCapStatus.EXCEEDED, Decimal("0.1001")),
            (149_900, SatelliteCapStatus.EXCEEDED, Decimal("0.1499")),
        )
        for satellite, expected_status, expected_ratio in cases:
            with self.subTest(satellite=satellite):
                review = review_allocation(
                    allocation_ledger(
                        {
                            AssetClass.CORE_EQUITY: 1_000_000 - satellite,
                            AssetClass.CORE_BOND: 0,
                            AssetClass.CORE_CASH: 0,
                            AssetClass.SATELLITE: satellite,
                        }
                    ),
                    year=2028,
                )
                self.assertEqual(review.satellite_cap.actual_ratio, expected_ratio)
                self.assertEqual(review.satellite_cap.status, expected_status)
                self.assertNotIn(AssetClass.SATELLITE, {line.asset_class for line in review.lines})

    def test_unclassified_asset_prevents_exact_percentages(self) -> None:
        ledger = allocation_ledger({AssetClass.UNCLASSIFIED: 1_000_000})
        review = review_allocation(ledger, year=2028)
        self.assertFalse(review.complete)
        self.assertTrue(all(line.actual_ratio is None for line in review.lines))
        self.assertEqual(review.satellite_cap.status, SatelliteCapStatus.UNAVAILABLE)

    def test_zero_portfolio_does_not_divide_by_zero(self) -> None:
        ledger = allocation_ledger({AssetClass.CORE_EQUITY: 0})
        review = review_allocation(ledger, year=2028)
        self.assertFalse(review.complete)
        self.assertTrue(all(line.actual_ratio is None for line in review.lines))
        self.assertEqual(review.satellite_cap.status, SatelliteCapStatus.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
