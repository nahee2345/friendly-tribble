from __future__ import annotations

import unittest
import tempfile
from decimal import Decimal
from pathlib import Path

from asset_assistant.allocation import review_allocation
from asset_assistant.automation import AutomationService
from asset_assistant.domain import DomainValidationError
from asset_assistant.market_data import MarketDataStore
from asset_assistant.scenario import load_scenario
from asset_assistant.strategy import StrategyKind, StrategyMix
from asset_assistant.strategy_ui import (
    STRATEGY_DETAILS,
    STRATEGY_DISPLAY_NAMES,
    STRATEGY_ORDER,
    validate_strategy_form,
)


def _weights(first: object, second: object = "0") -> dict[StrategyKind, object]:
    values: dict[StrategyKind, object] = {strategy: "0" for strategy in STRATEGY_ORDER}
    values[StrategyKind.BROAD_MARKET] = first
    values[StrategyKind.QUALITY] = second
    return values


class StrategyFormTests(unittest.TestCase):
    def test_exact_total_boundaries_control_save_and_guidance(self) -> None:
        cases = (
            ("95", "0", False, Decimal("95"), "5%를 추가해야 합니다."),
            ("100", "5", False, Decimal("105"), "5%를 줄여야 합니다."),
            ("99.99", "0", False, Decimal("99.99"), "0.01%를 추가해야 합니다."),
            ("100", "0.01", False, Decimal("100.01"), "0.01%를 줄여야 합니다."),
            ("100", "0", True, Decimal("100"), "합계 100%입니다. 저장할 수 있습니다."),
        )
        for first, second, expected_save, expected_total, expected_guidance in cases:
            with self.subTest(first=first, second=second):
                state = validate_strategy_form(_weights(first, second))
                self.assertEqual(state.can_save, expected_save)
                self.assertEqual(state.total, expected_total)
                self.assertEqual(state.guidance, expected_guidance)

    def test_invalid_entry_types_and_ranges_are_blocked_without_echo(self) -> None:
        for invalid in (
            "-0.01",
            "100.01",
            "",
            "not-a-number",
            "1e-999999999",
            "0." + "0" * 128,
            25.0,
            True,
        ):
            with self.subTest(invalid=repr(invalid)):
                state = validate_strategy_form(_weights(invalid, "0"))
                self.assertFalse(state.can_save)
                self.assertIsNone(state.total)
                self.assertTrue(state.errors)
                if str(invalid):
                    self.assertNotIn(str(invalid), " ".join(state.errors))
                with self.assertRaises(DomainValidationError):
                    state.to_strategy_mix("blocked")

    def test_decimal_text_is_not_rounded_through_float(self) -> None:
        state = validate_strategy_form(_weights("33.333333333333333333", "66.666666666666666667"))
        self.assertTrue(state.can_save)
        self.assertEqual(state.total, Decimal("100.000000000000000000"))
        mix = state.to_strategy_mix("decimal-v1")
        self.assertIsInstance(mix, StrategyMix)
        self.assertEqual(mix.weights[StrategyKind.BROAD_MARKET], Decimal("33.333333333333333333"))

    def test_long_decimal_total_is_not_rounded_to_one_hundred(self) -> None:
        cases = (
            ("99.99999999999999999999999999999", "0"),
            (
                "99.999999999999999999999999999991",
                "0.000000000000000000000000000008",
            ),
        )
        for first, second in cases:
            with self.subTest(first=first, second=second):
                state = validate_strategy_form(_weights(first, second))
                self.assertFalse(state.can_save)
                self.assertIsNotNone(state.total)
                self.assertLess(state.total, Decimal("100"))
                with self.assertRaises(DomainValidationError):
                    StrategyMix(
                        "must-not-round",
                        {
                            StrategyKind.BROAD_MARKET: Decimal(first),
                            StrategyKind.QUALITY: Decimal(second),
                        },
                    )

    def test_python_float_is_rejected_by_form_and_domain_model(self) -> None:
        self.assertFalse(validate_strategy_form(_weights(100.0)).can_save)
        with self.assertRaises(DomainValidationError):
            StrategyMix("float-v1", {StrategyKind.BROAD_MARKET: 100.0})  # type: ignore[dict-item]

    def test_missing_or_extra_strategy_is_rejected(self) -> None:
        missing = _weights("100")
        del missing[StrategyKind.BOND_CASH]
        extra: dict[object, object] = dict(_weights("100"))
        extra["UNKNOWN"] = "0"
        for values in (missing, extra):
            state = validate_strategy_form(values)
            self.assertFalse(state.can_save)
            self.assertIsNone(state.total)
            self.assertEqual(state.errors, ("8개 전략 비중을 빠짐없이 입력해야 합니다.",))

    def test_weights_are_read_only_and_strategy_mix_revalidates(self) -> None:
        state = validate_strategy_form(_weights("100"))
        self.assertTrue(state.can_save)
        with self.assertRaises(TypeError):
            state.weights[StrategyKind.BROAD_MARKET] = Decimal("0")  # type: ignore[index]
        self.assertEqual(sum(state.to_strategy_mix("immutable-v1").weights.values()), Decimal("100"))

    def test_metadata_covers_current_names_without_policy_proposals(self) -> None:
        self.assertEqual(tuple(STRATEGY_DETAILS), STRATEGY_ORDER)
        self.assertEqual(tuple(STRATEGY_DISPLAY_NAMES), STRATEGY_ORDER)
        self.assertEqual(
            tuple(detail.display_name for detail in STRATEGY_DETAILS.values()),
            (
                "광범위 지수",
                "퀄리티",
                "가치",
                "배당성장",
                "저변동성",
                "추세·모멘텀",
                "위성 테마·섹터",
                "채권·현금성",
            ),
        )
        rendered = repr(STRATEGY_DETAILS)
        for prohibited in ("목표 금액", "예시 포트폴리오", "다음 변경 예정일", "매수 수량", "목표가"):
            self.assertNotIn(prohibited, rendered)

    def test_strategy_version_changes_do_not_change_allocation_policy_result(self) -> None:
        root = Path(__file__).resolve().parents[1]
        scenario = load_scenario(root / "examples" / "offline_sample.json")
        before = review_allocation(scenario.ledger, year=scenario.allocation_year)
        with tempfile.TemporaryDirectory() as directory:
            service = AutomationService(
                store=MarketDataStore(Path(directory) / "strategy.sqlite3")
            )
            service.set_strategy_mix(
                "policy-invariant-a",
                {StrategyKind.BROAD_MARKET: Decimal("100")},
            )
            middle = review_allocation(scenario.ledger, year=scenario.allocation_year)
            service.set_strategy_mix(
                "policy-invariant-b",
                {
                    StrategyKind.QUALITY: Decimal("40"),
                    StrategyKind.VALUE: Decimal("60"),
                },
            )
            after = review_allocation(scenario.ledger, year=scenario.allocation_year)

        self.assertEqual(before, middle)
        self.assertEqual(before, after)
        self.assertEqual(before.satellite_cap.cap_ratio, Decimal("0.10"))


if __name__ == "__main__":
    unittest.main()
