from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from asset_assistant.automation import AutomationService
from asset_assistant.domain import StrategyVersionConflictError
from asset_assistant.market_data import (
    CollectionResult,
    CollectionStatus,
    MarketDataStore,
    NormalizedRecord,
    RecordKind,
)
from asset_assistant.strategy import StrategyKind


NOW = datetime(2026, 10, 11, 15, 30, tzinfo=timezone(timedelta(hours=9)))


def r3_variants() -> tuple[str, ...]:
    ascii_digit_variants = (
        "110－123－456789",
        "110ㅡ123ㅡ456789",
        "110·123·456789",
        "110・123・456789",
        "110．123．456789",
        "110:123:456789",
        "110'123'456789",
        "110#123#456789",
    )
    fullwidth_table = str.maketrans("0123456789", "０１２３４５６７８９")
    return (
        *ascii_digit_variants,
        *(value.translate(fullwidth_table) for value in ascii_digit_variants),
        "１１０-１２３-４５６７８９",
        "110ㅡ１２３·456789",
    )


class StrategyWorkspaceTests(unittest.TestCase):
    def _service(self, root: Path) -> AutomationService:
        return AutomationService(
            store=MarketDataStore(root / "market.sqlite3"),
            clock=lambda: NOW,
        )

    def test_empty_workspace_reports_actual_unconnected_and_missing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = self._service(Path(directory)).strategy_workspace_snapshot(
                now=NOW
            )

            self.assertIsNone(snapshot.current_mix)
            self.assertEqual(snapshot.assessments, ())
            self.assertEqual(
                {row.strategy for row in snapshot.input_states}, set(StrategyKind)
            )
            self.assertTrue(
                all(row.state in {"연결됨", "미연결", "결측"} for row in snapshot.input_states)
            )
            momentum_rows = [
                row
                for row in snapshot.input_states
                if row.strategy is StrategyKind.MOMENTUM
            ]
            self.assertTrue(momentum_rows)
            self.assertTrue(all(row.state == "미연결" for row in momentum_rows))

    def test_snapshot_exposes_immutable_mix_assessment_and_source_times_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            service.set_provider_enabled("kis", True)
            service.set_targets("kis", ("005930",))
            service.store.set_setting(
                "strategy.policy_verified_instruments", ["005930"], updated_at=NOW
            )
            version = "장기 전략 계좌 110-123-456789"
            service.set_strategy_mix(
                version,
                {StrategyKind.BROAD_MARKET: Decimal("100")},
            )

            effective = NOW - timedelta(minutes=5)
            collected = NOW - timedelta(minutes=4)
            records = tuple(
                NormalizedRecord(
                    provider="kis",
                    kind=RecordKind.PRICE_VOLUME,
                    record_id=f"005930-{metric}",
                    instrument_id="005930",
                    market="KRX",
                    currency="KRW",
                    effective_at=effective,
                    published_at=effective,
                    collected_at=collected,
                    unit=unit,
                    value=value,
                    source_url="https://example.invalid/kis",
                    metadata={"metric": metric, "environment": "paper"},
                )
                for metric, unit, value in (
                    ("price", "KRW", "73000"),
                    ("volume", "SHARES", "1000000"),
                    ("trading_value", "KRW", "73000000000"),
                )
            )
            service.store.save_collection(
                CollectionResult(
                    provider="kis",
                    started_at=collected,
                    finished_at=collected,
                    status=CollectionStatus.OK,
                    records=records,
                )
            )
            source_ids = tuple(
                int(row["id"])
                for row in service.store.latest_records(provider="kis", cutoff_at=NOW)
            )
            service.store.save_candidate_assessment(
                strategy_version=version,
                instrument_id="005930",
                cutoff_at=NOW,
                status="UNAVAILABLE",
                score=None,
                reasons=("저장 당시 통합 근거 계좌 110-123-456789",),
                source_record_ids=source_ids,
                created_at=NOW,
            )

            snapshot = service.strategy_workspace_snapshot(now=NOW)

            self.assertIsNotNone(snapshot.current_mix)
            assert snapshot.current_mix is not None
            self.assertNotIn("110-123-456789", snapshot.current_mix.version_display)
            self.assertNotIn("456789", snapshot.current_mix.version_display)
            self.assertIn("마스킹", snapshot.current_mix.version_display)
            self.assertEqual(snapshot.current_mix.saved_at, NOW.isoformat(timespec="seconds"))
            self.assertEqual(len(snapshot.current_mix.weights), len(StrategyKind))
            weights = {row.strategy: row.weight for row in snapshot.current_mix.weights}
            self.assertEqual(weights[StrategyKind.BROAD_MARKET], Decimal("100"))
            self.assertEqual(weights[StrategyKind.VALUE], Decimal("0"))

            self.assertEqual(len(snapshot.assessments), 1)
            assessment = snapshot.assessments[0]
            self.assertEqual(assessment.status, "UNAVAILABLE")
            self.assertIsNone(assessment.score)
            self.assertEqual(assessment.source_record_ids, source_ids)
            self.assertEqual(len(assessment.source_timings), 3)
            self.assertEqual(
                {timing.effective_at for timing in assessment.source_timings},
                {effective.isoformat(timespec="seconds")},
            )
            self.assertEqual(
                {timing.collected_at for timing in assessment.source_timings},
                {collected.isoformat(timespec="seconds")},
            )
            visible = "\n".join(
                (
                    assessment.strategy_version_display,
                    assessment.instrument_display,
                    *assessment.combined_evidence_constraints,
                )
            )
            self.assertNotIn("110-123-456789", visible)
            self.assertNotIn("456789", visible)
            self.assertIn("마스킹", visible)

            low_volatility = [
                row
                for row in snapshot.input_states
                if row.strategy is StrategyKind.LOW_VOLATILITY
            ]
            self.assertEqual(low_volatility[0].state, "연결됨")
            self.assertEqual(low_volatility[1].state, "결측")
            broad = [
                row
                for row in snapshot.input_states
                if row.strategy is StrategyKind.BROAD_MARKET
            ]
            self.assertEqual(broad[0].state, "미연결")
            self.assertEqual(broad[-1].state, "연결됨")

    def test_service_reports_new_immutable_versions_without_overwriting_prior_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            first = {StrategyKind.BROAD_MARKET: Decimal("100")}
            second = {
                StrategyKind.QUALITY: Decimal("40"),
                StrategyKind.VALUE: Decimal("60"),
            }

            self.assertTrue(service.set_strategy_mix("immutable-a", first))
            row = service.store.strategy_version_row("immutable-a")
            assert row is not None
            original = str(row["config_json"])
            self.assertFalse(service.set_strategy_mix("immutable-a", first))
            self.assertTrue(service.set_strategy_mix("immutable-b", second))
            self.assertFalse(service.set_strategy_mix("immutable-a", first))

            unchanged = service.store.strategy_version_row("immutable-a")
            assert unchanged is not None
            self.assertEqual(str(unchanged["config_json"]), original)
            self.assertIsNotNone(service.store.strategy_version_row("immutable-b"))
            current = service.current_strategy_mix()
            assert current is not None
            self.assertEqual(current.version, "immutable-b")

    def test_same_name_different_mix_raises_typed_conflict_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            original_weights = {
                StrategyKind.QUALITY: Decimal("60"),
                StrategyKind.VALUE: Decimal("40"),
            }
            conflicting_weights = {
                StrategyKind.QUALITY: Decimal("10"),
                StrategyKind.MOMENTUM: Decimal("90"),
            }
            self.assertTrue(service.set_strategy_mix("probe-v1", original_weights))
            before = service.store.strategy_version_row("probe-v1")
            assert before is not None
            before_config = str(before["config_json"])

            with self.assertRaises(StrategyVersionConflictError):
                service.set_strategy_mix("probe-v1", conflicting_weights)

            after = service.store.strategy_version_row("probe-v1")
            assert after is not None
            self.assertEqual(str(after["config_json"]), before_config)
            current = service.current_strategy_mix()
            assert current is not None
            self.assertEqual(current.version, "probe-v1")
            self.assertEqual(dict(current.weights), original_weights)
            self.assertFalse(service.set_strategy_mix("probe-v1", original_weights))

    def test_all_r3_variants_are_masked_in_strategy_version_display(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            for index, sensitive in enumerate(r3_variants()):
                with self.subTest(index=index):
                    raw_version = f"전략 {index} {sensitive}"
                    self.assertTrue(
                        service.set_strategy_mix(
                            raw_version,
                            {StrategyKind.BROAD_MARKET: Decimal("100")},
                        )
                    )
                    snapshot = service.strategy_workspace_snapshot(now=NOW)
                    assert snapshot.current_mix is not None
                    visible = snapshot.current_mix.version_display
                    self.assertIn("[계좌번호 형태 마스킹]", visible)
                    self.assertNotIn(sensitive, visible)
                    self.assertNotIn("456789", visible)
                    self.assertNotIn("４５６７８９", visible)


if __name__ == "__main__":
    unittest.main()
