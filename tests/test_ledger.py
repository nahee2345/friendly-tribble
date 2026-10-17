from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from asset_assistant.domain import (
    Account,
    AssetClass,
    DataRealm,
    DeclaredAssetPlan,
    DomainValidationError,
    PositionRecord,
    Purpose,
    ValueStatus,
)
from asset_assistant.ledger import PortfolioLedger


NOW = datetime(2028, 1, 1, tzinfo=timezone.utc)


def account(account_id: str, purpose: Purpose, realm: DataRealm = DataRealm.SAMPLE) -> Account:
    return Account(account_id, account_id, "TEST", realm, purpose)


def position(
    position_id: str,
    account_id: str,
    amount: str | None,
    *,
    asset_class: AssetClass = AssetClass.UNCLASSIFIED,
    status: ValueStatus = ValueStatus.KNOWN,
) -> PositionRecord:
    return PositionRecord(
        position_id,
        account_id,
        position_id,
        asset_class,
        None if amount is None else Decimal(amount),
        NOW,
        status,
        "test fixture",
    )


class LedgerTests(unittest.TestCase):
    def test_protected_and_emergency_money_is_excluded_from_investment(self) -> None:
        accounts = [
            account("investment", Purpose.INVESTMENT),
            account("protected", Purpose.PROTECTED_SAVINGS),
            account("emergency", Purpose.EMERGENCY),
        ]
        positions = [
            position("i", "investment", "1000000", asset_class=AssetClass.CORE_EQUITY),
            position("p", "protected", "2000000"),
            position("e", "emergency", "3000000"),
        ]
        ledger = PortfolioLedger(
            realm=DataRealm.SAMPLE, accounts=accounts, positions=positions
        )
        self.assertEqual(ledger.investment_value().amount_krw, Decimal("1000000"))
        self.assertEqual(ledger.aggregate().amount_krw, Decimal("6000000"))

    def test_declared_plan_is_reconciled_not_auto_filled(self) -> None:
        ledger = PortfolioLedger(
            realm=DataRealm.SAMPLE,
            accounts=[account("investment", Purpose.INVESTMENT)],
            positions=[position("i", "investment", "3990000")],
        )
        result = ledger.reconcile(DeclaredAssetPlan(Decimal("4000000")))
        self.assertFalse(result.matches)
        self.assertEqual(result.detailed_krw, Decimal("3990000"))
        self.assertEqual(result.difference_krw, Decimal("10000"))

    def test_empty_ledger_is_missing_not_known_zero(self) -> None:
        ledger = PortfolioLedger(
            realm=DataRealm.MANUAL_ACTUAL,
            accounts=[],
            positions=[],
        )
        aggregate = ledger.investment_value()
        self.assertEqual(aggregate.amount_krw, Decimal("0"))
        self.assertFalse(aggregate.complete)
        self.assertFalse(ledger.reconcile(DeclaredAssetPlan(Decimal("4000000"))).complete)

    def test_missing_is_not_silently_treated_as_zero(self) -> None:
        ledger = PortfolioLedger(
            realm=DataRealm.SAMPLE,
            accounts=[account("investment", Purpose.INVESTMENT)],
            positions=[
                position("known", "investment", "100"),
                position("missing", "investment", None, status=ValueStatus.MISSING),
            ],
        )
        aggregate = ledger.investment_value()
        self.assertEqual(aggregate.amount_krw, Decimal("100"))
        self.assertFalse(aggregate.complete)
        self.assertEqual(aggregate.missing_position_ids, ("missing",))

    def test_realms_cannot_be_mixed(self) -> None:
        with self.assertRaises(DomainValidationError):
            PortfolioLedger(
                realm=DataRealm.SAMPLE,
                accounts=[
                    account(
                        "paper",
                        Purpose.INVESTMENT,
                        DataRealm.MANUAL_PAPER_SNAPSHOT,
                    )
                ],
                positions=[],
            )

    def test_non_known_value_must_not_carry_a_fake_amount(self) -> None:
        with self.assertRaises(DomainValidationError):
            position("missing", "investment", "0", status=ValueStatus.MISSING)


if __name__ == "__main__":
    unittest.main()

