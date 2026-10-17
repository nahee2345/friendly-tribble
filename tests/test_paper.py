from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import DefaultContext, Decimal, DivisionByZero, Overflow, Underflow, localcontext

from asset_assistant.domain import DataRealm, DomainValidationError
from asset_assistant.paper import (
    APPROVAL_LIFETIME,
    INITIAL_PAPER_CASH_KRW,
    LARGE_TRADE_THRESHOLD_KRW,
    ApprovalState,
    ApprovalTarget,
    ApprovalType,
    AttemptResult,
    Candidate,
    CandidateFailureReason,
    CandidateSet,
    CandidateSetState,
    CompletionKind,
    CostBasisMethod,
    Currency,
    ExecutionRun,
    GateDecision,
    GateStatus,
    InMemoryPaperRepository,
    InstrumentClassification,
    InstrumentType,
    NewRootPurposeTarget,
    PaperBlockedError,
    PaperBudgetVersion,
    PaperEngine,
    PaperExecutionPolicy,
    PaperFill,
    PaperPortfolio,
    PriceCondition,
    PriceConditionOperator,
    PaperRiskAssessment,
    PaperRiskContext,
    PaperScope,
    PaperStrategyKind,
    PaperStrategyVersion,
    PaperValuationSnapshot,
    ProposalState,
    PurposeChangeResult,
    ReservationRequest,
    ReservationState,
    RiskDenominatorBasis,
    RiskPolicyInputs,
    RiskRole,
    RunState,
    SatelliteLossGate,
    Side,
    SplitCheckResult,
    StrategyApplication,
    TradeAmounts,
    TradeProposal,
    TradePurpose,
    VerifiedFxRate,
    VerifiedPrice,
    VerifiedSessionWindow,
    assess_paper_risk,
    calculate_trade_amounts,
    candidate_set_content_hash,
    canonical_hash,
    trade_proposal_content_hash,
    trade_purpose_version_hash,
    utc_epoch_microseconds,
)


UTC = timezone.utc
NOW = datetime(2028, 1, 2, 15, 0, tzinfo=UTC)
SESSION = date(2028, 1, 2)
KST_DAY = date(2028, 1, 3)


def krw_amounts(gross: int | str | Decimal, cost: int | str | Decimal = 0) -> TradeAmounts:
    gross_value = Decimal(str(gross))
    cost_value = Decimal(str(cost))
    with localcontext() as context:
        context.prec = 128
        total = gross_value + cost_value
        return TradeAmounts(
            gross_value,
            cost_value,
            total,
            Currency.KRW,
            gross_value,
            cost_value,
            total,
        )


def observed_fill(
    fill_id: str,
    proposal: TradeProposal,
    reservation_id: str,
    quantity: int,
    amounts: TradeAmounts,
    filled_at: datetime,
    *,
    price_as_of: datetime | None = None,
    verified: bool = True,
) -> PaperFill:
    with localcontext() as context:
        context.prec = 128
        unit_price = amounts.gross_trade_currency / Decimal(quantity)
    return PaperFill(
        fill_id,
        proposal.proposal_id,
        reservation_id,
        quantity,
        amounts,
        filled_at,
        observed_price=VerifiedPrice(
            proposal.instrument_id,
            unit_price,
            amounts.cash_currency,
            price_as_of or filled_at,
            f"quote-{fill_id}",
            verified,
        ),
        observation_cutoff_at=filled_at,
        observed_fx_rate=(
            VerifiedFxRate(
                amounts.cash_currency,
                amounts.fx_krw_per_unit,
                proposal.pricing_fx_rate.as_of,
                proposal.pricing_fx_rate.source_record_id,
                verified,
            )
            if amounts.cash_currency is not Currency.KRW
            and amounts.fx_krw_per_unit is not None
            and proposal.pricing_fx_rate is not None
            else None
        ),
    )


def confirmed_execution_policy(*, sell_applies: bool = False) -> PaperExecutionPolicy:
    return PaperExecutionPolicy(
        policy_version="policy-v1",
        monthly_budget_krw=Decimal("10000000"),
        monthly_budget_month="2028-01",
        monthly_budget_applies_to_buy=False,
        monthly_budget_applies_to_sell=sell_applies,
        per_order_limit_krw=Decimal("10000000"),
        cost_model_confirmed=True,
        fx_model_confirmed=True,
        cost_basis_method=CostBasisMethod.SPECIFIC_LOT,
    )


def confirmed_risk_policy() -> RiskPolicyInputs:
    return RiskPolicyInputs(
        "risk-v1",
        RiskDenominatorBasis.CURRENT_PAPER_NAV,
        None,
        True,
        True,
        True,
        True,
        SatelliteLossGate(
            GateDecision.passed(),
            "risk-v1",
            NOW,
            "satellite-loss-fixture",
        ),
    )


class PaperFixture:
    def __init__(self) -> None:
        self.repository = InMemoryPaperRepository()
        self.engine = PaperEngine(self.repository)
        self._risk_contexts: dict[str, PaperRiskContext] = {}
        self.engine.create_portfolio(
            portfolio_id="paper-portfolio",
            owner_ref="local-owner",
            created_at=NOW,
            genesis_entry_id="genesis",
        )
        allocations = tuple(
            (
                strategy,
                Decimal("4000000")
                if strategy is PaperStrategyKind.LONG_TERM_INDEX
                else Decimal("0"),
            )
            for strategy in PaperStrategyKind
        )
        self.engine.save_budget_version(
            PaperBudgetVersion("budget-v1", "policy-v1", allocations, NOW)
        )
        self.engine.save_strategy_draft(
            PaperStrategyVersion("strategy-v1", "strategy-hash-v1", NOW)
        )
        self.engine.apply_strategy_version(
            StrategyApplication("strategy-apply-v1", "strategy-v1", "approval-ref", NOW)
        )
        purpose_hash = trade_purpose_version_hash(
            trade_purpose_id="purpose-v1",
            stable_root_trade_purpose_id="root-1",
            purpose_version="v1",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            strategy_version_ref="strategy-v1",
            budget_version_ref="budget-v1",
            policy_version_ref="policy-v1",
            intended_effect="가상 장기투자 검증",
        )
        self.engine.create_trade_purpose(
            TradePurpose(
                trade_purpose_id="purpose-v1",
                stable_root_trade_purpose_id="root-1",
                purpose_version="v1",
                purpose_version_hash=purpose_hash,
                strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
                strategy_version_ref="strategy-v1",
                budget_version_ref="budget-v1",
                policy_version_ref="policy-v1",
                intended_effect="가상 장기투자 검증",
                created_at=NOW,
            )
        )

    @staticmethod
    def _valuation_fx_rates(state, valuation_at: datetime) -> dict[Currency, VerifiedFxRate]:
        if not (
            state.cash.get(Currency.USD, Decimal("0"))
            or any(
                lot.currency is Currency.USD and lot.remaining_quantity
                for lot in state.lots.values()
            )
        ):
            return {}
        fx_value = next(
            (
                proposal.pricing_fx_rate.krw_per_unit
                for proposal in state.proposals.values()
                if proposal.pricing_fx_rate is not None
            ),
            Decimal("1300"),
        )
        return {
            Currency.USD: VerifiedFxRate(
                Currency.USD,
                fx_value,
                valuation_at,
                "fixture-valuation-fx",
                True,
            )
        }

    def open_session(self, session: date) -> None:
        if session in self.engine.snapshot.daily_limits:
            return
        state = self.engine.snapshot
        valuation_at = max(
            (NOW,)
            + tuple(item.acquired_at for item in state.lots.values())
            + tuple(item.occurred_at for item in state.ledger_entries)
            + tuple(item.recorded_at for item in state.ledger_entries)
        )
        prices = {
            lot.instrument_id: VerifiedPrice(
                lot.instrument_id,
                lot.unit_cost,
                lot.currency,
                valuation_at,
                f"price-{lot.instrument_id}",
                True,
            )
            for lot in state.lots.values()
            if lot.remaining_quantity
        }
        snapshot_id = f"opening-{session.isoformat()}"
        self.engine.value_portfolio(
            snapshot_id=snapshot_id,
            as_of=valuation_at,
            cutoff_at=valuation_at,
            prices=prices,
            fx_rates=self._valuation_fx_rates(state, valuation_at),
        )
        self.engine.open_daily_limit(
            session=VerifiedSessionWindow(
                session,
                f"calendar-{session}",
                valuation_at,
                f"session-evidence-{session}",
                True,
            ),
            opening_valuation_snapshot_id=snapshot_id,
        )

    def add_proposal(
        self,
        proposal_id: str,
        *,
        instrument: str = "TEST",
        gross: int = 100_000,
        cost: int = 0,
        quantity: int = 1,
        session: date = SESSION,
        kst_day: date = KST_DAY,
        intent: str | None = None,
        purpose_id: str = "purpose-v1",
        purpose_version: str = "v1",
        root_id: str = "root-1",
        side: Side = Side.BUY,
        expires_at: datetime = NOW + timedelta(days=2),
        amounts: TradeAmounts | None = None,
    ) -> tuple[TradeProposal, str]:
        set_id = f"set-{proposal_id}"
        purpose = self.engine.snapshot.purposes[purpose_id]
        candidate_at = max(NOW, purpose.created_at)
        candidates = (Candidate(1, instrument),)
        set_hash = candidate_set_content_hash(
            set_id=set_id,
            version="v1",
            trade_purpose_id=purpose_id,
            stable_root_trade_purpose_id=root_id,
            candidates=candidates,
            evidence_cutoff=candidate_at,
            created_at=candidate_at,
        )
        candidate_set = CandidateSet(
            set_id,
            "v1",
            set_hash,
            purpose_id,
            root_id,
            candidates,
            candidate_at,
            candidate_at,
        )
        self.engine.create_candidate_set(candidate_set)
        self.engine.transition_candidate_set(set_id, CandidateSetState.VALIDATED)
        self.engine.transition_candidate_set(set_id, CandidateSetState.WAITING_SET_APPROVAL)
        approval_id = f"candidate-approval-{proposal_id}"
        self.engine.grant_approval(
            approval_id=approval_id,
            approval_type=ApprovalType.CANDIDATE_SET,
            target=ApprovalTarget(set_id, "v1", set_hash),
            approved_by_user_id="user",
            approved_at=candidate_at,
            audit_ref=f"audit-{approval_id}",
        )
        self.engine.transition_candidate_set(
            set_id,
            CandidateSetState.SET_APPROVED,
            approval_id=approval_id,
            transitioned_at=candidate_at,
        )
        proposal_amounts = amounts or krw_amounts(gross, cost)
        pricing_fx = (
            VerifiedFxRate(
                proposal_amounts.cash_currency,
                proposal_amounts.fx_krw_per_unit,
                candidate_at,
                f"proposal-fx-{proposal_id}",
                True,
            )
            if proposal_amounts.cash_currency is not Currency.KRW
            and proposal_amounts.fx_krw_per_unit is not None
            else None
        )
        with localcontext() as context:
            context.prec = 128
            condition_price = (
                proposal_amounts.gross_trade_currency / Decimal(quantity)
            )
        price_condition = PriceCondition(
            (
                PriceConditionOperator.AT_OR_BELOW
                if side is Side.BUY
                else PriceConditionOperator.AT_OR_ABOVE
            ),
            condition_price,
            proposal_amounts.cash_currency,
            f"condition-evidence-{proposal_id}",
            candidate_at,
        )
        proposal_hash = trade_proposal_content_hash(
            proposal_id=proposal_id,
            version=purpose_version,
            candidate_set_id=set_id,
            candidate_rank=1,
            trade_purpose_id=purpose_id,
            purpose_version=purpose_version,
            stable_root_trade_purpose_id=root_id,
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            trade_intent_id=intent or f"intent-{proposal_id}",
            instrument_id=instrument,
            side=side,
            quantity=quantity,
            amounts=proposal_amounts,
            us_session_date=session,
            kst_intent_date=kst_day,
            expires_at=expires_at,
            pricing_fx_rate=pricing_fx,
            pricing_cutoff_at=candidate_at if pricing_fx is not None else None,
            price_condition=price_condition,
        )
        proposal = TradeProposal(
            proposal_id,
            purpose_version,
            proposal_hash,
            set_id,
            1,
            purpose_id,
            purpose_version,
            root_id,
            PaperStrategyKind.LONG_TERM_INDEX,
            intent or f"intent-{proposal_id}",
            instrument,
            side,
            quantity,
            proposal_amounts,
            session,
            kst_day,
            expires_at,
            pricing_fx_rate=pricing_fx,
            pricing_cutoff_at=candidate_at if pricing_fx is not None else None,
            price_condition=price_condition,
        )
        self.engine.create_proposal(proposal)
        return proposal, approval_id

    def grant_large(self, proposal_id: str, *, suffix: str = "") -> str:
        approval_id = f"large-approval-{proposal_id}{suffix}"
        self.engine.grant_approval(
            approval_id=approval_id,
            approval_type=ApprovalType.LARGE_TRADE,
            target=self.engine.large_approval_group_target(proposal_id),
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref=f"audit-{approval_id}",
        )
        return approval_id

    def risk_context(
        self,
        proposal: TradeProposal,
        *,
        refresh: bool = False,
    ) -> PaperRiskContext:
        cached = self._risk_contexts.get(proposal.proposal_id)
        if cached is not None and not refresh:
            return cached
        state = self.engine.snapshot
        valuation_at = max(
            (NOW,)
            + tuple(item.acquired_at for item in state.lots.values())
            + tuple(item.occurred_at for item in state.ledger_entries)
            + tuple(item.recorded_at for item in state.ledger_entries)
        )
        prices = {
            lot.instrument_id: VerifiedPrice(
                lot.instrument_id,
                lot.unit_cost,
                lot.currency,
                valuation_at,
                f"risk-price-{lot.instrument_id}",
                True,
            )
            for lot in state.lots.values()
            if lot.remaining_quantity
        }
        valuation = self.engine.value_portfolio(
            snapshot_id=(
                f"risk-{proposal.proposal_id}-{len(state.valuations)}"
                if refresh
                else f"risk-{proposal.proposal_id}"
            ),
            as_of=valuation_at,
            cutoff_at=valuation_at,
            prices=prices,
            fx_rates=self._valuation_fx_rates(state, valuation_at),
        )
        instruments = {
            item.instrument_id for item in self.engine.snapshot.proposals.values()
        } | {proposal.instrument_id}
        classifications = tuple(
            InstrumentClassification(
                instrument,
                InstrumentType.BROAD_MARKET_ETF,
                RiskRole.CORE,
                "risk-v1",
                "test-evidence",
                True,
            )
            for instrument in sorted(instruments)
        )
        context = PaperRiskContext(valuation, classifications, confirmed_risk_policy())
        self._risk_contexts[proposal.proposal_id] = context
        return context

    def reserve(
        self,
        proposal: TradeProposal,
        candidate_approval_id: str,
        *,
        reservation_id: str | None = None,
        large_approval_id: str | None = None,
        sell_lots: tuple[tuple[str, int], ...] = (),
        policy: PaperExecutionPolicy | None = None,
        risk_context: PaperRiskContext | None = None,
    ):
        if proposal.us_session_date not in self.engine.snapshot.daily_limits:
            self.open_session(proposal.us_session_date)
        resolved_risk_context = risk_context or self.risk_context(proposal)
        reservation_at = max(
            NOW + timedelta(minutes=1),
            resolved_risk_context.valuation.as_of,
            resolved_risk_context.valuation.cutoff_at,
        )
        assert proposal.price_condition is not None
        self.engine.evaluate_proposal_price_condition(
            proposal_id=proposal.proposal_id,
            observed_price=VerifiedPrice(
                proposal.instrument_id,
                proposal.price_condition.threshold,
                proposal.amounts.cash_currency,
                reservation_at,
                f"condition-check-{proposal.proposal_id}",
                True,
            ),
            observation_cutoff_at=reservation_at,
            evaluated_at=reservation_at,
        )
        return self.engine.reserve_proposal(
            ReservationRequest(
                reservation_id or f"reservation-{proposal.proposal_id}",
                proposal.proposal_id,
                reservation_at,
                sell_lots,
            ),
            execution_policy=policy or confirmed_execution_policy(),
            risk_context=resolved_risk_context,
            candidate_approval_id=candidate_approval_id,
            large_approval_id=large_approval_id,
        )

    def begin_attempt(
        self,
        proposal: TradeProposal,
        *,
        suffix: str = "",
    ) -> tuple[str, str]:
        run_id = f"fill-run-{proposal.proposal_id}{suffix}"
        event_id = f"fill-attempt-{proposal.proposal_id}{suffix}"
        included = ((proposal.proposal_id, proposal.version),)
        reservation = next(
            item
            for item in self.engine.snapshot.reservations.values()
            if item.proposal_id == proposal.proposal_id
            and item.state is ReservationState.ACTIVE
        )
        attempt_at = max(NOW + timedelta(minutes=1), reservation.created_at)
        self.engine.create_execution_run(
            ExecutionRun(
                run_id,
                "paper-portfolio",
                proposal.us_session_date,
                included,
                canonical_hash(included),
                attempt_at,
                NOW + timedelta(hours=1),
                RunState.READY,
            ),
            gates=GateDecision.passed(),
        )
        self.engine.record_execution_attempt(
            run_id=run_id,
            event_id=event_id,
            proposal_id=proposal.proposal_id,
            attempted_at=attempt_at,
        )
        return run_id, event_id

    def buy_lot(
        self,
        *,
        proposal_id: str = "opening-buy",
        instrument: str = "TEST",
        quantity: int = 10,
        gross: int = 100_000,
        session: date = date(2028, 1, 1),
        lot_id: str = "lot-1",
    ) -> str:
        proposal, candidate_approval = self.add_proposal(
            proposal_id,
            instrument=instrument,
            gross=gross,
            quantity=quantity,
            session=session,
            kst_day=session + timedelta(days=1),
        )
        large_approval = (
            self.grant_large(proposal.proposal_id)
            if proposal.amounts.approval_judgment_krw >= LARGE_TRADE_THRESHOLD_KRW
            else None
        )
        reservation = self.reserve(
            proposal,
            candidate_approval,
            large_approval_id=large_approval,
        )
        run_id, event_id = self.begin_attempt(proposal)
        self.engine.record_fill(
            observed_fill(
                f"fill-{proposal_id}",
                proposal,
                reservation.reservation_id,
                quantity,
                krw_amounts(gross),
                NOW + timedelta(minutes=2),
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=self.risk_context(proposal),
            run_id=run_id,
            attempt_event_id=event_id,
            lot_id=lot_id,
        )
        self.engine.confirm_attempt(
            run_id=run_id,
            event_id=event_id,
            result=AttemptResult.FILL_RECORDED,
            confirmed_at=NOW + timedelta(minutes=3),
        )
        return lot_id


@dataclass(frozen=True)
class _UncheckedTradeAmounts(TradeAmounts):
    def __post_init__(self) -> None:
        pass


class _ApprovalTargetSubclass(ApprovalTarget):
    pass


@dataclass(frozen=True)
class _UncheckedPriceCondition(PriceCondition):
    def __post_init__(self) -> None:
        pass

    def is_met(self, price: Decimal) -> bool:
        return True


@dataclass(frozen=True)
class _UncheckedGateDecision(GateDecision):
    def __post_init__(self) -> None:
        pass

    @property
    def permits_execution(self) -> bool:
        return True


@dataclass(frozen=True)
class _UncheckedVerifiedFxRate(VerifiedFxRate):
    def __post_init__(self) -> None:
        pass


class PaperInputAndRealmTests(unittest.TestCase):
    def test_datetime_and_adversarial_iterables_fail_closed_without_raw_values(self) -> None:
        class EvilIterable:
            def __iter__(self):
                raise RuntimeError("SENSITIVE_ITERATOR_VALUE")

        valuation = PaperValuationSnapshot(
            "safe-valuation",
            "paper-portfolio",
            NOW,
            NOW,
            GateStatus.PASS,
            Decimal("4000000"),
            Decimal("4000000"),
            Decimal("0"),
            Decimal("0"),
            (),
        )
        included = (("proposal", "v1"),)
        invalid_factories = (
            lambda: PaperPortfolio("paper", "owner", "not-a-time"),  # type: ignore[arg-type]
            lambda: utc_epoch_microseconds(None),  # type: ignore[arg-type]
            lambda: CandidateSet(
                "set",
                "v1",
                "unused-hash",
                "purpose",
                "root",
                EvilIterable(),  # type: ignore[arg-type]
                NOW,
                NOW,
            ),
            lambda: ExecutionRun(
                "run",
                "paper-portfolio",
                SESSION,
                EvilIterable(),  # type: ignore[arg-type]
                "unused-hash",
                NOW,
                NOW + timedelta(hours=1),
                RunState.READY,
            ),
            lambda: ExecutionRun(
                "run",
                "paper-portfolio",
                SESSION,
                included,
                canonical_hash(included),
                NOW,
                NOW + timedelta(hours=1),
                RunState.READY,
                attempts=EvilIterable(),  # type: ignore[arg-type]
            ),
            lambda: PaperValuationSnapshot(
                "bad-values",
                "paper-portfolio",
                NOW,
                NOW,
                GateStatus.PASS,
                Decimal("4000000"),
                Decimal("4000000"),
                Decimal("0"),
                Decimal("0"),
                EvilIterable(),  # type: ignore[arg-type]
            ),
            lambda: PaperValuationSnapshot(
                "bad-reasons",
                "paper-portfolio",
                NOW,
                NOW,
                GateStatus.UNKNOWN,
                None,
                None,
                None,
                None,
                (),
                EvilIterable(),  # type: ignore[arg-type]
            ),
            lambda: PaperRiskContext(
                valuation,
                EvilIterable(),  # type: ignore[arg-type]
                confirmed_risk_policy(),
            ),
        )
        for index, factory in enumerate(invalid_factories):
            with self.subTest(index=index):
                with self.assertRaises(DomainValidationError) as blocked:
                    factory()
                message = str(blocked.exception)
                self.assertNotIn("SENSITIVE_ITERATOR_VALUE", message)
                self.assertNotIn("Traceback", message)

    def test_public_mapping_inputs_fail_closed_without_mutating_state(self) -> None:
        class EvilMapping(dict):
            def items(self):
                raise RuntimeError("SENSITIVE_RAW_MAPPING_VALUE")

        fixture = PaperFixture()
        before = fixture.engine.snapshot
        for prices, fx_rates in (
            (None, {}),
            (7, {}),
            ({}, None),
            ({}, 7),
            ({"TEST": "bad-price"}, {}),
            ({}, {Currency.USD: "bad-rate"}),
            (
                {
                    "TEST": VerifiedPrice(
                        "OTHER", "1", Currency.KRW, NOW, "price-source", True
                    )
                },
                {},
            ),
            (
                {},
                {
                    Currency.KRW: VerifiedFxRate(
                        Currency.USD, "1300", NOW, "fx-source", True
                    )
                },
            ),
            (EvilMapping({"TEST": "hidden"}), {}),
        ):
            with self.subTest(prices=type(prices).__name__, fx=type(fx_rates).__name__):
                with self.assertRaises(DomainValidationError) as blocked:
                    fixture.engine.value_portfolio(
                        snapshot_id="invalid-public-mapping",
                        as_of=NOW,
                        cutoff_at=NOW,
                        prices=prices,  # type: ignore[arg-type]
                        fx_rates=fx_rates,  # type: ignore[arg-type]
                    )
                self.assertNotIn("SENSITIVE_RAW_MAPPING_VALUE", str(blocked.exception))
                self.assertEqual(fixture.engine.snapshot, before)

        valuation = PaperValuationSnapshot(
            "input-risk",
            "paper-portfolio",
            NOW,
            NOW,
            GateStatus.PASS,
            Decimal("4000000"),
            Decimal("4000000"),
            Decimal("0"),
            Decimal("0"),
            (),
        )
        classification = InstrumentClassification(
            "TEST",
            InstrumentType.COMMON_STOCK,
            RiskRole.CORE,
            "risk-v1",
            "evidence",
            True,
        )
        invalid_risk_inputs = (
            (None, None),
            (7, None),
            ({"TEST": "bad-classification"}, None),
            ({"TEST": replace(classification, instrument_id="OTHER")}, None),
            (EvilMapping({"TEST": classification}), None),
            ({"TEST": classification}, 7),
            ({"TEST": classification}, {"TEST": "invalid-amount"}),
            ({"TEST": classification}, EvilMapping({"TEST": 1})),
        )
        for classifications, exposures in invalid_risk_inputs:
            with self.subTest(
                classifications=type(classifications).__name__,
                exposures=type(exposures).__name__,
            ):
                with self.assertRaises(DomainValidationError) as blocked:
                    assess_paper_risk(
                        valuation=valuation,
                        classifications=classifications,  # type: ignore[arg-type]
                        proposed_instrument_id="TEST",
                        proposed_buy_gross_krw="1",
                        policy=confirmed_risk_policy(),
                        active_buy_exposures_krw=exposures,  # type: ignore[arg-type]
                    )
                self.assertNotIn("SENSITIVE_RAW_MAPPING_VALUE", str(blocked.exception))

        valid_classifications = {"TEST": classification}
        for valuation_input, policy_input in (
            (None, confirmed_risk_policy()),
            (valuation, None),
        ):
            with self.subTest(
                valuation=type(valuation_input).__name__,
                policy=type(policy_input).__name__,
            ):
                with self.assertRaises(DomainValidationError) as blocked:
                    assess_paper_risk(
                        valuation=valuation_input,  # type: ignore[arg-type]
                        classifications=valid_classifications,
                        proposed_instrument_id="TEST",
                        proposed_buy_gross_krw="1",
                        policy=policy_input,  # type: ignore[arg-type]
                    )
                self.assertNotIn("Traceback", str(blocked.exception))

    def test_next_candidate_invalid_approved_reason_collection_is_safe(self) -> None:
        fixture = PaperFixture()
        for malformed in (None, 7, "not-a-collection"):
            with self.subTest(kind=type(malformed).__name__):
                with self.assertRaises(DomainValidationError) as blocked:
                    fixture.engine.next_candidate(
                        set_id="missing-set",
                        current_rank=1,
                        reason=CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE,
                        evidence_verified=True,
                        approved_permanent_reasons=malformed,  # type: ignore[arg-type]
                    )
                self.assertNotIn("Traceback", str(blocked.exception))

    def test_unhashable_public_identifiers_are_safe_and_atomic(self) -> None:
        fixture = PaperFixture()
        fixture.add_proposal("identifier-shape")
        before = fixture.engine.snapshot
        invalid_id: list[str] = ["SENSITIVE_IDENTIFIER_VALUE"]
        calls = (
            lambda: fixture.engine.transition_candidate_set(
                invalid_id,  # type: ignore[arg-type]
                CandidateSetState.VALIDATED,
            ),
            lambda: fixture.engine.cancel_approval(
                invalid_id,  # type: ignore[arg-type]
                cancelled_at=NOW,
                audit_id="invalid-id-cancel-audit",
            ),
            lambda: fixture.engine.expire_proposal(
                invalid_id,  # type: ignore[arg-type]
                expired_at=NOW,
                evidence_ref="invalid-id-expiry-evidence",
                audit_id="invalid-id-expiry-audit",
            ),
            lambda: fixture.engine.release_reservation(
                invalid_id,  # type: ignore[arg-type]
                released_at=NOW,
                reason_code="INVALID_ID_PROBE",
            ),
            lambda: fixture.engine.close_execution_window(
                invalid_id,  # type: ignore[arg-type]
                ended_at=NOW,
            ),
            lambda: fixture.engine.large_approval_group_amount(
                invalid_id,  # type: ignore[arg-type]
            ),
            lambda: fixture.engine.large_approval_group_target(
                invalid_id,  # type: ignore[arg-type]
            ),
        )
        for index, call in enumerate(calls):
            with self.subTest(index=index):
                with self.assertRaises(DomainValidationError) as blocked:
                    call()
                message = str(blocked.exception)
                self.assertNotIn("SENSITIVE_IDENTIFIER_VALUE", message)
                self.assertNotIn("Traceback", message)
                self.assertEqual(fixture.engine.snapshot, before)

    def test_decimal_context_is_independent_from_mutated_default_context(self) -> None:
        expected = calculate_trade_amounts(
            quantity=2,
            unit_price="123456789.5",
            currency=Currency.KRW,
            maximum_cost="0",
            fx_krw_per_unit=None,
        )
        saved = {
            "prec": DefaultContext.prec,
            "rounding": DefaultContext.rounding,
            "Emin": DefaultContext.Emin,
            "Emax": DefaultContext.Emax,
            "capitals": DefaultContext.capitals,
            "clamp": DefaultContext.clamp,
            "traps": DefaultContext.traps.copy(),
        }
        try:
            DefaultContext.prec = 3
            DefaultContext.Emin = -77
            DefaultContext.Emax = 77
            DefaultContext.clamp = 1
            DefaultContext.traps[DivisionByZero] = False
            DefaultContext.traps[Overflow] = False
            DefaultContext.traps[Underflow] = True
            self.assertEqual(
                calculate_trade_amounts(
                    quantity=2,
                    unit_price="123456789.5",
                    currency=Currency.KRW,
                    maximum_cost="0",
                    fx_krw_per_unit=None,
                ),
                expected,
            )
            self.assertEqual(
                calculate_trade_amounts(
                    quantity=1,
                    unit_price="1E+100",
                    currency=Currency.KRW,
                    maximum_cost="0",
                    fx_krw_per_unit=None,
                ).amounts.gross_krw,
                Decimal("1E+100"),
            )
        finally:
            DefaultContext.prec = saved["prec"]
            DefaultContext.rounding = saved["rounding"]
            DefaultContext.Emin = saved["Emin"]
            DefaultContext.Emax = saved["Emax"]
            DefaultContext.capitals = saved["capitals"]
            DefaultContext.clamp = saved["clamp"]
            for signal, enabled in saved["traps"].items():
                DefaultContext.traps[signal] = enabled

    def test_malformed_collection_inputs_are_safe_domain_errors(self) -> None:
        allocations = tuple(
            (strategy, Decimal("0")) for strategy in PaperStrategyKind
        )
        malformed_calls = (
            lambda: GateDecision(GateStatus.PASS, None),  # type: ignore[arg-type]
            lambda: PaperRiskAssessment(
                GateStatus.UNKNOWN,
                None,
                None,
                None,
                None,  # type: ignore[arg-type]
            ),
            lambda: PaperBudgetVersion(
                "bad-budget-none",
                "policy-v1",
                None,  # type: ignore[arg-type]
                NOW,
            ),
            lambda: PaperBudgetVersion(
                "bad-budget-shape",
                "policy-v1",
                ["not-a-pair"],  # type: ignore[list-item]
                NOW,
            ),
            lambda: ReservationRequest(
                "bad-reservation-none",
                "proposal",
                NOW,
                None,  # type: ignore[arg-type]
            ),
            lambda: ReservationRequest(
                "bad-reservation-shape",
                "proposal",
                NOW,
                ["not-a-pair"],  # type: ignore[list-item]
            ),
        )
        self.assertEqual(len(allocations), 8)
        for call in malformed_calls:
            with self.subTest(call=call):
                with self.assertRaises(DomainValidationError) as raised:
                    call()
                self.assertNotIn("Traceback", str(raised.exception))

    def test_decimal_hash_and_trade_calculation_ignore_global_context_precision(self) -> None:
        material = {"amount": Decimal("123456789.1200"), "small": Decimal("0.0000012300")}
        expected_hash = canonical_hash(material)
        expected_amounts = calculate_trade_amounts(
            quantity=7,
            unit_price="12345.6789",
            currency=Currency.USD,
            maximum_cost="0.123456",
            fx_krw_per_unit="1321.98765",
        )
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(canonical_hash(material), expected_hash)
            actual_amounts = calculate_trade_amounts(
                quantity=7,
                unit_price="12345.6789",
                currency=Currency.USD,
                maximum_cost="0.123456",
                fx_krw_per_unit="1321.98765",
            )
        self.assertEqual(actual_amounts, expected_amounts)
        self.assertEqual(
            actual_amounts.amounts.gross_trade_currency,
            Decimal("86419.7523"),
        )

    def test_decimal_context_hash_types_and_overprecision_fail_closed(self) -> None:
        direct = krw_amounts("123456789")
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(krw_amounts("123456789"), direct)
            self.assertNotEqual(canonical_hash(Decimal("1")), canonical_hash("1"))
            self.assertNotEqual(canonical_hash(("x",)), canonical_hash(["x"]))
            self.assertEqual(canonical_hash(Decimal("1.00")), canonical_hash(Decimal("1E0")))
        oversized = ("1" * 129)
        with self.assertRaises(DomainValidationError) as blocked:
            calculate_trade_amounts(
                quantity=1,
                unit_price=oversized,
                currency=Currency.KRW,
                maximum_cost="0",
                fx_krw_per_unit=None,
            )
        self.assertNotIn(oversized, str(blocked.exception))
        self.assertEqual(len(canonical_hash(Decimal("1E+1000000"))), 64)

        cyclic: list[object] = []
        cyclic.append(cyclic)
        invalid_hash_inputs = (10**5000, cyclic, "\ud800")
        for invalid in invalid_hash_inputs:
            with self.subTest(kind=type(invalid).__name__):
                with self.assertRaises(DomainValidationError) as invalid_hash:
                    canonical_hash(invalid)
                self.assertNotIn("Traceback", str(invalid_hash.exception))

        with self.assertRaises(DomainValidationError) as extreme_exponent:
            RiskPolicyInputs(
                "risk-v1",
                RiskDenominatorBasis.SESSION_OPENING_PAPER_NAV,
                Decimal("1E+1000126"),
                True,
                True,
                True,
                True,
            )
        self.assertNotIn("1E+1000126", str(extreme_exponent.exception))

    def test_hashed_collections_are_normalized_and_content_mutation_is_rejected(self) -> None:
        mutable_candidates = [Candidate(1, "FIRST")]
        set_hash = candidate_set_content_hash(
            set_id="immutable-set",
            version="v1",
            trade_purpose_id="purpose-v1",
            stable_root_trade_purpose_id="root-1",
            candidates=tuple(mutable_candidates),
            evidence_cutoff=NOW,
            created_at=NOW,
        )
        candidate_set = CandidateSet(
            "immutable-set",
            "v1",
            set_hash,
            "purpose-v1",
            "root-1",
            mutable_candidates,  # type: ignore[arg-type]
            NOW,
            NOW,
        )
        mutable_candidates.append(Candidate(2, "SECOND"))
        self.assertEqual(candidate_set.candidates, (Candidate(1, "FIRST"),))
        with self.assertRaises(DomainValidationError):
            replace(candidate_set, candidates=(Candidate(1, "CHANGED"),))

        mutable_included = [["proposal", "v1"]]
        run = ExecutionRun(
            "immutable-run",
            "paper-portfolio",
            SESSION,
            mutable_included,  # type: ignore[arg-type]
            canonical_hash((("proposal", "v1"),)),
            NOW,
            NOW + timedelta(hours=1),
            RunState.READY,
        )
        mutable_included[0][0] = "changed"
        self.assertEqual(run.included_proposals, (("proposal", "v1"),))

        fixture = PaperFixture()
        proposal, _approval = fixture.add_proposal("hash-bound")
        with self.assertRaises(DomainValidationError):
            replace(proposal, quantity=2)
        with self.assertRaises(DomainValidationError):
            replace(proposal, us_session_date=datetime(2028, 1, 2, tzinfo=UTC))

        with self.assertRaises(DomainValidationError):
            ExecutionRun(
                "string-pair-run",
                "paper-portfolio",
                SESSION,
                ["ab"],  # type: ignore[list-item]
                canonical_hash((("a", "b"),)),
                NOW,
                NOW + timedelta(hours=1),
                RunState.READY,
            )

        base_context = fixture.risk_context(proposal)
        mutable_classifications = list(base_context.classifications)
        normalized_context = PaperRiskContext(
            base_context.valuation,
            mutable_classifications,  # type: ignore[arg-type]
            base_context.policy,
        )
        mutable_classifications.append(base_context.classifications[0])
        self.assertEqual(
            normalized_context.classifications,
            base_context.classifications,
        )
        with self.assertRaises(DomainValidationError):
            PaperRiskContext(
                base_context.valuation,
                None,  # type: ignore[arg-type]
                base_context.policy,
            )

    def test_canonical_hash_wraps_adversarial_mapping_and_dataclass_class(self) -> None:
        class EvilMapping(dict):
            def items(self):
                raise RuntimeError("SENSITIVE_RAW_MAPPING_VALUE")

        @dataclass
        class NoDefaults:
            value: int

        for value in (EvilMapping({"safe": 1}), NoDefaults):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(DomainValidationError) as blocked:
                    canonical_hash(value)
                self.assertNotIn("SENSITIVE_RAW_MAPPING_VALUE", str(blocked.exception))
                self.assertNotIn("NoDefaults", str(blocked.exception))

    def test_runtime_bool_and_enum_inputs_are_fail_closed(self) -> None:
        with self.assertRaises(DomainValidationError):
            VerifiedPrice("X", "1", Currency.KRW, NOW, "source", "true")  # type: ignore[arg-type]
        with self.assertRaises(DomainValidationError):
            VerifiedSessionWindow(SESSION, "calendar", NOW, "evidence", 1)  # type: ignore[arg-type]
        with self.assertRaises(DomainValidationError):
            krw_amounts(1).__class__(
                Decimal("1"),
                Decimal("0"),
                Decimal("1"),
                "KRW",  # type: ignore[arg-type]
                Decimal("1"),
                Decimal("0"),
                Decimal("1"),
            )
        fixture = PaperFixture()
        fixture.add_proposal("invalid-transition-state")
        before = fixture.engine.snapshot
        for invalid_state in ([], {}):
            with self.subTest(invalid_state=type(invalid_state).__name__):
                with self.assertRaises(DomainValidationError):
                    fixture.engine.transition_candidate_set(
                        "set-invalid-transition-state",
                        invalid_state,  # type: ignore[arg-type]
                    )
                self.assertEqual(fixture.engine.snapshot, before)

    def test_repository_commit_does_not_expose_mutable_candidate_state(self) -> None:
        repository = InMemoryPaperRepository()
        escaped: list[object] = []

        def operation(state):
            state.cash[Currency.KRW] = Decimal("1")
            escaped.append(state)
            return state.cash

        returned = repository.transact(operation)
        escaped[0].cash[Currency.KRW] = Decimal("999")  # type: ignore[attr-defined]
        returned[Currency.KRW] = Decimal("777")
        self.assertEqual(repository.snapshot().cash[Currency.KRW], Decimal("1"))

    def test_genesis_is_virtual_4000000_and_created_once(self) -> None:
        fixture = PaperFixture()
        state = fixture.engine.snapshot
        self.assertEqual(state.portfolio.scope, PaperScope.INTERNAL_PAPER)
        self.assertEqual(state.cash[Currency.KRW], INITIAL_PAPER_CASH_KRW)
        self.assertEqual(len(state.ledger_entries), 1)
        with self.assertRaisesRegex(PaperBlockedError, "안전 정책"):
            fixture.engine.create_portfolio(
                portfolio_id="second",
                owner_ref="owner",
                created_at=NOW,
                genesis_entry_id="second-genesis",
            )
        self.assertEqual(len(fixture.engine.snapshot.ledger_entries), 1)

    def test_internal_paper_scope_does_not_accept_existing_realms(self) -> None:
        with self.assertRaises(DomainValidationError):
            PaperPortfolio(
                "paper",
                "owner",
                NOW,
                scope=DataRealm.MANUAL_PAPER_SNAPSHOT,  # type: ignore[arg-type]
            )

    def test_float_nan_infinity_negative_quantity_and_naive_time_are_rejected(self) -> None:
        for value in (1.5, "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(DomainValidationError):
                calculate_trade_amounts(
                    quantity=1,
                    unit_price=value,
                    currency=Currency.USD,
                    maximum_cost="0",
                    fx_krw_per_unit="1300",
                )
        with self.assertRaises(DomainValidationError):
            calculate_trade_amounts(
                quantity=-1,
                unit_price="1",
                currency=Currency.KRW,
                maximum_cost="0",
                fx_krw_per_unit=None,
            )
        with self.assertRaises(DomainValidationError):
            PaperPortfolio("paper", "owner", datetime(2028, 1, 1))
        for invalid_time in (None, "SENSITIVE_RAW_TIME_VALUE"):
            with self.subTest(time_type=type(invalid_time).__name__):
                with self.assertRaises(DomainValidationError) as portfolio_time:
                    PaperPortfolio(
                        "paper",
                        "owner",
                        invalid_time,  # type: ignore[arg-type]
                    )
                self.assertNotIn("SENSITIVE_RAW_TIME_VALUE", str(portfolio_time.exception))
                with self.assertRaises(DomainValidationError) as epoch_time:
                    utc_epoch_microseconds(invalid_time)  # type: ignore[arg-type]
                self.assertNotIn("SENSITIVE_RAW_TIME_VALUE", str(epoch_time.exception))

        class HostileDateTime(datetime):
            def astimezone(self, tz=None):
                return datetime(2028, 1, 1)

        hostile = HostileDateTime(2028, 1, 1, tzinfo=UTC)
        for operation in (
            lambda: PaperPortfolio("paper", "owner", hostile),
            lambda: utc_epoch_microseconds(hostile),
        ):
            with self.assertRaises(DomainValidationError) as safe_error:
                operation()
            self.assertNotIn("offset-naive", str(safe_error.exception))

    def test_utc_epoch_microseconds_preserves_offsets_and_precision(self) -> None:
        same = datetime(2028, 1, 1, 9, 0, 0, 123456, tzinfo=timezone(timedelta(hours=9)))
        utc = datetime(2028, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
        self.assertEqual(utc_epoch_microseconds(same), utc_epoch_microseconds(utc))
        self.assertEqual(utc_epoch_microseconds(utc + timedelta(microseconds=1)), utc_epoch_microseconds(utc) + 1)

    def test_missing_price_cost_and_fx_are_unknown_not_zero(self) -> None:
        price_missing = calculate_trade_amounts(
            quantity=1,
            unit_price=None,
            currency=Currency.KRW,
            maximum_cost="0",
            fx_krw_per_unit=None,
        )
        cost_missing = calculate_trade_amounts(
            quantity=1,
            unit_price="100",
            currency=Currency.KRW,
            maximum_cost=None,
            fx_krw_per_unit=None,
        )
        fx_missing = calculate_trade_amounts(
            quantity=1,
            unit_price="100",
            currency=Currency.USD,
            maximum_cost="0",
            fx_krw_per_unit=None,
        )
        self.assertEqual(price_missing.decision.status, GateStatus.UNKNOWN)
        self.assertEqual(cost_missing.decision.status, GateStatus.UNKNOWN)
        self.assertEqual(fx_missing.decision.status, GateStatus.UNKNOWN)
        self.assertIsNone(price_missing.amounts)
        self.assertIsNone(cost_missing.amounts)
        self.assertIsNone(fx_missing.amounts)


class PaperStrategyBudgetLedgerTests(unittest.TestCase):
    def test_manual_ledger_events_preserve_causal_order_and_rollback(self) -> None:
        fixture = PaperFixture()
        before_fx = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as fx_error:
            fixture.engine.record_fx(
                event_id="backdated-fx",
                from_currency=Currency.KRW,
                from_amount="1300",
                to_currency=Currency.USD,
                to_amount="1",
                occurred_at=NOW - timedelta(microseconds=1),
                fx_rate=VerifiedFxRate(
                    Currency.USD,
                    "1300",
                    NOW - timedelta(microseconds=1),
                    "backdated-fx-source",
                    True,
                ),
                cutoff_at=NOW - timedelta(microseconds=1),
                cost_krw="0",
                cost_model_confirmed=True,
            )
        self.assertEqual(fx_error.exception.code, "LEDGER_EVENT_BEFORE_PORTFOLIO")
        self.assertEqual(fixture.engine.snapshot, before_fx)

        fixture.buy_lot()
        before_dividend = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as dividend_error:
            fixture.engine.record_dividend(
                entry_id="pre-acquisition-dividend",
                strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
                instrument_id="TEST",
                amount="1",
                currency=Currency.KRW,
                occurred_at=NOW + timedelta(minutes=1),
                recorded_at=NOW + timedelta(minutes=4),
                source_ref="verified-entitlement",
            )
        self.assertEqual(dividend_error.exception.code, "DIVIDEND_POSITION_NOT_FOUND")
        self.assertEqual(fixture.engine.snapshot, before_dividend)

        fixture.engine.record_dividend(
            entry_id="causal-dividend",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            instrument_id="TEST",
            amount="10",
            currency=Currency.KRW,
            occurred_at=NOW + timedelta(minutes=4),
            recorded_at=NOW + timedelta(minutes=4),
            source_ref="verified-entitlement",
        )
        before_correction = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as correction_error:
            fixture.engine.correct_ledger_entry(
                correction_entry_id="backdated-correction",
                corrects_entry_id="causal-dividend",
                amount="-1",
                occurred_at=NOW + timedelta(minutes=3),
                recorded_at=NOW + timedelta(minutes=5),
                source_ref="verified-correction",
            )
        self.assertEqual(
            correction_error.exception.code,
            "LEDGER_CORRECTION_TIME_INVALID",
        )
        self.assertEqual(fixture.engine.snapshot, before_correction)

    def test_audit_ids_are_globally_unique_and_collision_rolls_back(self) -> None:
        fixture = PaperFixture()
        fixture.engine.grant_approval(
            approval_id="audit-approval-one",
            approval_type=ApprovalType.CANDIDATE_SET,
            target=ApprovalTarget("target-one", "v1", "hash-one"),
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref="shared-audit-id",
        )
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as collision:
            fixture.engine.grant_approval(
                approval_id="audit-approval-two",
                approval_type=ApprovalType.CANDIDATE_SET,
                target=ApprovalTarget("target-two", "v1", "hash-two"),
                approved_by_user_id="user",
                approved_at=NOW,
                audit_ref="shared-audit-id",
            )
        self.assertEqual(collision.exception.code, "AUDIT_ID_DUPLICATE")
        self.assertEqual(fixture.engine.snapshot, before)

    def test_trade_purpose_rejects_missing_strategy_and_budget_references(self) -> None:
        fixture = PaperFixture()
        purpose_hash = trade_purpose_version_hash(
            trade_purpose_id="missing-refs",
            stable_root_trade_purpose_id="root-1",
            purpose_version="v2",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            strategy_version_ref="missing-strategy",
            budget_version_ref="missing-budget",
            policy_version_ref="policy-v1",
            intended_effect="같은 목적",
            parent_trade_purpose_id="purpose-v1",
        )
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.engine.create_trade_purpose(
                TradePurpose(
                    "missing-refs",
                    "root-1",
                    "v2",
                    purpose_hash,
                    PaperStrategyKind.LONG_TERM_INDEX,
                    "missing-strategy",
                    "missing-budget",
                    "policy-v1",
                    "같은 목적",
                    NOW,
                    parent_trade_purpose_id="purpose-v1",
                )
            )
        self.assertEqual(blocked.exception.code, "STRATEGY_VERSION_NOT_FOUND")

    def test_strategy_budget_isolated_and_released_amount_is_reusable(self) -> None:
        fixture = PaperFixture()
        allocations = tuple(
            (
                strategy,
                Decimal("100000")
                if strategy is PaperStrategyKind.LONG_TERM_INDEX
                else Decimal("1000000")
                if strategy is PaperStrategyKind.VALUE
                else Decimal("0"),
            )
            for strategy in PaperStrategyKind
        )
        fixture.engine.save_budget_version(
            PaperBudgetVersion("budget-small", "policy-v1", allocations, NOW)
        )
        purpose_hash = trade_purpose_version_hash(
            trade_purpose_id="purpose-small",
            stable_root_trade_purpose_id="root-1",
            purpose_version="v2",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            strategy_version_ref="strategy-v1",
            budget_version_ref="budget-small",
            policy_version_ref="policy-v1",
            intended_effect="가상 장기투자 검증",
            parent_trade_purpose_id="purpose-v1",
        )
        fixture.engine.create_trade_purpose(
            TradePurpose(
                "purpose-small",
                "root-1",
                "v2",
                purpose_hash,
                PaperStrategyKind.LONG_TERM_INDEX,
                "strategy-v1",
                "budget-small",
                "policy-v1",
                "가상 장기투자 검증",
                NOW,
                parent_trade_purpose_id="purpose-v1",
            )
        )
        first, first_approval = fixture.add_proposal(
            "budget-first",
            gross=100_000,
            purpose_id="purpose-small",
            purpose_version="v2",
        )
        first_reservation = fixture.reserve(first, first_approval)
        over, over_approval = fixture.add_proposal(
            "budget-over",
            instrument="OTHER",
            gross=1,
            session=SESSION + timedelta(days=1),
            kst_day=KST_DAY + timedelta(days=1),
            purpose_id="purpose-small",
            purpose_version="v2",
        )
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.reserve(over, over_approval)
        self.assertEqual(blocked.exception.code, "STRATEGY_BUDGET_EXCEEDED")
        fixture.engine.release_reservation(
            first_reservation.reservation_id,
            released_at=NOW + timedelta(minutes=2),
            reason_code="USER_CANCELLED",
        )
        fixture.reserve(over, over_approval)

    def test_strategy_draft_and_exact_14_day_apply_boundary(self) -> None:
        fixture = PaperFixture()
        fixture.engine.save_strategy_draft(
            PaperStrategyVersion("s2", "hash-2", NOW, "strategy-v1")
        )
        with self.assertRaises(PaperBlockedError) as before:
            fixture.engine.apply_strategy_version(
                StrategyApplication("a2-before", "s2", "approval-2", NOW + timedelta(days=14) - timedelta(microseconds=1))
            )
        self.assertEqual(before.exception.code, "STRATEGY_CHANGE_INTERVAL_NOT_ELAPSED")
        fixture.engine.apply_strategy_version(
            StrategyApplication("a2", "s2", "approval-2", NOW + timedelta(days=14))
        )
        self.assertEqual(len(fixture.engine.snapshot.strategy_versions), 2)
        self.assertEqual(len(fixture.engine.snapshot.strategy_applications), 2)

    def test_strategy_budget_is_versioned_and_not_monthly_budget_or_cash_transfer(self) -> None:
        fixture = PaperFixture()
        budget = PaperBudgetVersion(
            "budget-v2",
            "policy-v1",
            tuple(
                (
                    strategy,
                    Decimal("2500000")
                    if strategy is PaperStrategyKind.LONG_TERM_INDEX
                    else Decimal("1000000")
                    if strategy is PaperStrategyKind.VALUE
                    else Decimal("0"),
                )
                for strategy in PaperStrategyKind
            ),
            NOW,
        )
        fixture.engine.save_budget_version(budget)
        state = fixture.engine.snapshot
        self.assertEqual(budget.total_krw, Decimal("3500000"))
        self.assertEqual(state.cash[Currency.KRW], Decimal("4000000"))
        with self.assertRaises(PaperBlockedError):
            fixture.engine.save_budget_version(budget)

    def test_correction_appends_and_preserves_original(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot()
        dividend = fixture.engine.record_dividend(
            entry_id="dividend-1",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            instrument_id="TEST",
            amount="10",
            currency=Currency.KRW,
            occurred_at=NOW + timedelta(minutes=4),
            recorded_at=NOW + timedelta(minutes=4),
            source_ref="verified-dividend-input",
        )
        fixture.engine.correct_ledger_entry(
            correction_entry_id="correction-1",
            corrects_entry_id="dividend-1",
            amount="-1",
            occurred_at=NOW + timedelta(minutes=5),
            recorded_at=NOW + timedelta(minutes=5),
            source_ref="test-correction",
        )
        entries = fixture.engine.snapshot.ledger_entries
        self.assertEqual(dividend.amount, Decimal("10"))
        self.assertEqual(entries[-2].entry_id, "dividend-1")
        self.assertEqual(entries[-1].corrects_entry_id, "dividend-1")
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.engine.correct_ledger_entry(
                correction_entry_id="genesis-correction",
                corrects_entry_id="genesis",
                amount="1",
                occurred_at=NOW + timedelta(minutes=6),
                recorded_at=NOW + timedelta(minutes=6),
                source_ref="forbidden-genesis-correction",
            )
        self.assertEqual(blocked.exception.code, "GENESIS_CORRECTION_FORBIDDEN")
        self.assertEqual(fixture.engine.snapshot.cash, before.cash)
        self.assertEqual(fixture.engine.snapshot.ledger_entries, before.ledger_entries)

    def test_fx_cost_is_a_separate_append_only_ledger_entry(self) -> None:
        fixture = PaperFixture()
        fixture.engine.record_fx(
            event_id="fx-with-cost",
            from_currency=Currency.KRW,
            from_amount="1310",
            to_currency=Currency.USD,
            to_amount="1",
            occurred_at=NOW,
            fx_rate=VerifiedFxRate(Currency.USD, "1300", NOW, "fx-source", True),
            cutoff_at=NOW,
            cost_krw="10",
            cost_model_confirmed=True,
        )
        state = fixture.engine.snapshot
        self.assertEqual(state.cash[Currency.KRW], Decimal("3998690"))
        self.assertEqual(state.cash[Currency.USD], Decimal("1"))
        entries = state.ledger_entries[-3:]
        self.assertEqual(
            tuple(item.entry_id for item in entries),
            ("fx-with-cost:debit", "fx-with-cost:credit", "fx-with-cost:fee"),
        )
        self.assertEqual(
            tuple(item.amount for item in entries),
            (Decimal("-1300"), Decimal("1"), Decimal("-10")),
        )


class PaperValuationRiskTests(unittest.TestCase):
    def test_missing_position_price_or_fx_makes_entire_nav_unknown(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot()
        valued_at = NOW + timedelta(minutes=3)
        result = fixture.engine.value_portfolio(
            snapshot_id="missing-price",
            as_of=valued_at,
            cutoff_at=valued_at,
            prices={},
            fx_rates={},
        )
        self.assertEqual(result.status, GateStatus.UNKNOWN)
        self.assertIsNone(result.nav_krw)
        self.assertIsNone(result.cash_krw)
        self.assertIn("PRICE_UNAVAILABLE", result.reason_codes)

        fixture.engine.record_fx(
            event_id="fx-1",
            from_currency=Currency.KRW,
            from_amount="1300",
            to_currency=Currency.USD,
            to_amount="1",
            occurred_at=NOW,
            fx_rate=VerifiedFxRate(Currency.USD, "1300", NOW, "fx-source", True),
            cutoff_at=NOW,
            cost_krw="0",
            cost_model_confirmed=True,
        )
        missing_fx = fixture.engine.value_portfolio(
            snapshot_id="missing-fx",
            as_of=valued_at,
            cutoff_at=valued_at,
            prices={
                "TEST": VerifiedPrice(
                    "TEST", "10000", Currency.KRW, valued_at, "quote-1", True
                )
            },
            fx_rates={},
        )
        self.assertEqual(missing_fx.status, GateStatus.UNKNOWN)
        self.assertIn("FX_UNAVAILABLE_USD", missing_fx.reason_codes)

    def test_unrealized_gain_is_separate_from_cash(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot(gross=100_000, quantity=10)
        valued_at = NOW + timedelta(minutes=3)
        valuation = fixture.engine.value_portfolio(
            snapshot_id="valuation",
            as_of=valued_at,
            cutoff_at=valued_at,
            prices={"TEST": VerifiedPrice("TEST", "20000", Currency.KRW, valued_at, "quote", True)},
            fx_rates={},
        )
        self.assertEqual(valuation.cash_krw, Decimal("3900000"))
        self.assertEqual(valuation.position_value_krw, Decimal("200000"))
        self.assertEqual(valuation.unrealized_pnl_krw, Decimal("100000"))
        self.assertEqual(valuation.nav_krw, Decimal("4100000"))

    def _valuation(self, instrument_values: tuple[tuple[str, Decimal], ...], nav: str = "4000000") -> PaperValuationSnapshot:
        total = sum((value for _, value in instrument_values), Decimal("0"))
        return PaperValuationSnapshot(
            "risk-snapshot",
            "paper-portfolio",
            NOW,
            NOW,
            GateStatus.PASS,
            Decimal(nav),
            Decimal(nav) - total,
            total,
            Decimal("0"),
            instrument_values,
        )

    def _classification(self, instrument: str, kind: InstrumentType, role: RiskRole) -> InstrumentClassification:
        return InstrumentClassification(instrument, kind, role, "risk-v1", "evidence", True)

    def test_common_stock_etf_broad_etf_and_satellite_boundaries(self) -> None:
        cases = (
            ("STOCK", InstrumentType.COMMON_STOCK, RiskRole.CORE, 200_000),
            ("ETF", InstrumentType.ETF, RiskRole.CORE, 1_600_000),
            ("BROAD", InstrumentType.BROAD_MARKET_ETF, RiskRole.CORE, 2_000_000),
            ("SAT", InstrumentType.ETF, RiskRole.SATELLITE, 400_000),
        )
        for instrument, kind, role, cap in cases:
            with self.subTest(instrument=instrument):
                classification = self._classification(instrument, kind, role)
                exact = assess_paper_risk(
                    valuation=self._valuation(()),
                    classifications={instrument: classification},
                    proposed_instrument_id=instrument,
                    proposed_buy_gross_krw=cap,
                    policy=confirmed_risk_policy(),
                )
                over = assess_paper_risk(
                    valuation=self._valuation(()),
                    classifications={instrument: classification},
                    proposed_instrument_id=instrument,
                    proposed_buy_gross_krw=cap + 1,
                    policy=confirmed_risk_policy(),
                )
                self.assertTrue(exact.permits_buy)
                self.assertFalse(over.permits_buy)

    def test_current_nav_caps_use_post_cost_denominator_but_opening_nav_is_fixed(self) -> None:
        stock = self._classification(
            "STOCK",
            InstrumentType.COMMON_STOCK,
            RiskRole.CORE,
        )
        no_cost = assess_paper_risk(
            valuation=self._valuation(()),
            classifications={"STOCK": stock},
            proposed_instrument_id="STOCK",
            proposed_buy_gross_krw="200000",
            proposed_maximum_cost_krw="0",
            policy=confirmed_risk_policy(),
        )
        with_cost = assess_paper_risk(
            valuation=self._valuation(()),
            classifications={"STOCK": stock},
            proposed_instrument_id="STOCK",
            proposed_buy_gross_krw="200000",
            proposed_maximum_cost_krw="1",
            policy=confirmed_risk_policy(),
        )
        opening_policy = replace(
            confirmed_risk_policy(),
            denominator_basis=RiskDenominatorBasis.SESSION_OPENING_PAPER_NAV,
            session_opening_nav_krw=Decimal("4000000.5"),
        )
        fixed_opening = assess_paper_risk(
            valuation=self._valuation((), nav="4000000.5"),
            classifications={"STOCK": stock},
            proposed_instrument_id="STOCK",
            proposed_buy_gross_krw="200000",
            proposed_maximum_cost_krw="1",
            policy=opening_policy,
        )
        self.assertTrue(no_cost.permits_buy)
        self.assertEqual(with_cost.denominator_krw, Decimal("3999999"))
        self.assertIn("INSTRUMENT_CONCENTRATION_LIMIT_EXCEEDED", with_cost.reason_codes)
        self.assertTrue(fixed_opening.permits_buy)
        self.assertEqual(fixed_opening.denominator_krw, Decimal("4000000.5"))

    def test_existing_satellite_overage_blocks_only_new_satellite_buy(self) -> None:
        valuation = self._valuation((("SAT", Decimal("400001")),))
        classifications = {
            "SAT": self._classification("SAT", InstrumentType.ETF, RiskRole.SATELLITE),
            "CORE": self._classification("CORE", InstrumentType.COMMON_STOCK, RiskRole.CORE),
        }
        core = assess_paper_risk(
            valuation=valuation,
            classifications=classifications,
            proposed_instrument_id="CORE",
            proposed_buy_gross_krw="1",
            policy=confirmed_risk_policy(),
        )
        satellite = assess_paper_risk(
            valuation=valuation,
            classifications=classifications,
            proposed_instrument_id="SAT",
            proposed_buy_gross_krw="1",
            policy=confirmed_risk_policy(),
        )
        self.assertTrue(core.permits_buy)
        self.assertIn("SATELLITE_LIMIT_EXCEEDED", satellite.reason_codes)

    def test_unclassified_is_counted_once_as_satellite_and_new_buy_is_blocked(self) -> None:
        valuation = self._valuation((("UNKNOWN", Decimal("200000")),))
        classification = self._classification(
            "UNKNOWN",
            InstrumentType.UNCLASSIFIED,
            RiskRole.UNCLASSIFIED_ASSUMED_SATELLITE,
        )
        result = assess_paper_risk(
            valuation=valuation,
            classifications={"UNKNOWN": classification},
            proposed_instrument_id="UNKNOWN",
            proposed_buy_gross_krw="1",
            policy=confirmed_risk_policy(),
        )
        self.assertEqual(result.status, GateStatus.BLOCKED)
        self.assertEqual(result.satellite_ratio, Decimal("200001") / Decimal("4000000"))
        self.assertIn("UNCLASSIFIED_ASSUMED_SATELLITE_BUY_BLOCKED", result.reason_codes)

    def test_prohibited_and_unconfirmed_policies_fail_closed(self) -> None:
        valuation = self._valuation(())
        prohibited = self._classification("LEV", InstrumentType.PROHIBITED, RiskRole.INELIGIBLE)
        result = assess_paper_risk(
            valuation=valuation,
            classifications={"LEV": prohibited},
            proposed_instrument_id="LEV",
            proposed_buy_gross_krw="1",
            policy=confirmed_risk_policy(),
        )
        self.assertIn("PROHIBITED_PRODUCT", result.reason_codes)
        unknown_policy = RiskPolicyInputs("risk", None, None, None, None, None, None)
        missing = assess_paper_risk(
            valuation=valuation,
            classifications={"SAT": self._classification("SAT", InstrumentType.ETF, RiskRole.SATELLITE)},
            proposed_instrument_id="SAT",
            proposed_buy_gross_krw="1",
            policy=unknown_policy,
        )
        self.assertEqual(missing.status, GateStatus.UNKNOWN)
        self.assertIn("PAPER_RISK_DENOMINATOR_REQUIRED", missing.reason_codes)

        for field_name, reason in (
            ("mdd_policy_confirmed", "MDD_POLICY_INPUT_REQUIRED"),
            ("industry_policy_confirmed", "INDUSTRY_POLICY_INPUT_REQUIRED"),
            ("company_policy_confirmed", "COMPANY_POLICY_INPUT_REQUIRED"),
        ):
            with self.subTest(field_name=field_name):
                policy = replace(confirmed_risk_policy(), **{field_name: None})
                result = assess_paper_risk(
                    valuation=valuation,
                    classifications={
                        "CORE": self._classification(
                            "CORE",
                            InstrumentType.COMMON_STOCK,
                            RiskRole.CORE,
                        )
                    },
                    proposed_instrument_id="CORE",
                    proposed_buy_gross_krw="1",
                    policy=policy,
                )
                self.assertEqual(result.status, GateStatus.UNKNOWN)
                self.assertIn(reason, result.reason_codes)

    def test_satellite_loss_policy_missing_blocks_only_satellite_buy(self) -> None:
        policy = RiskPolicyInputs(
            "risk-v1",
            RiskDenominatorBasis.CURRENT_PAPER_NAV,
            None,
            None,
            True,
            True,
            True,
        )
        valuation = self._valuation(())
        satellite = assess_paper_risk(
            valuation=valuation,
            classifications={"SAT": self._classification("SAT", InstrumentType.ETF, RiskRole.SATELLITE)},
            proposed_instrument_id="SAT",
            proposed_buy_gross_krw="1",
            policy=policy,
        )
        core = assess_paper_risk(
            valuation=valuation,
            classifications={"CORE": self._classification("CORE", InstrumentType.COMMON_STOCK, RiskRole.CORE)},
            proposed_instrument_id="CORE",
            proposed_buy_gross_krw="1",
            policy=policy,
        )
        self.assertIn("SATELLITE_LOSS_POLICY_INPUT_REQUIRED", satellite.reason_codes)
        self.assertEqual(satellite.status, GateStatus.UNKNOWN)
        self.assertTrue(core.permits_buy)

    def test_satellite_loss_gate_preserves_unknown_blocked_and_pass_states(self) -> None:
        valuation = self._valuation(())
        classification = {
            "SAT": self._classification(
                "SAT",
                InstrumentType.ETF,
                RiskRole.SATELLITE,
            )
        }
        cases = (
            (GateDecision.unknown("LOSS_DATA_UNAVAILABLE"), GateStatus.UNKNOWN),
            (GateDecision.blocked("SATELLITE_LOSS_LOCKED"), GateStatus.BLOCKED),
            (GateDecision.passed(), GateStatus.PASS),
        )
        for decision, expected in cases:
            with self.subTest(expected=expected):
                policy = replace(
                    confirmed_risk_policy(),
                    satellite_loss_gate=SatelliteLossGate(
                        decision,
                        "risk-v1",
                        NOW,
                        "satellite-loss-evidence",
                    ),
                )
                result = assess_paper_risk(
                    valuation=valuation,
                    classifications=classification,
                    proposed_instrument_id="SAT",
                    proposed_buy_gross_krw="1",
                    policy=policy,
                )
                self.assertEqual(result.status, expected)


class PaperLimitReservationFillTests(unittest.TestCase):
    def test_price_condition_is_typed_held_and_rechecked_after_approval(self) -> None:
        fixture = PaperFixture()
        proposal, candidate_approval = fixture.add_proposal(
            "price-condition-buy",
            gross=10_000,
        )
        fixture.open_session(SESSION)
        risk_context = fixture.risk_context(proposal)
        check_at = NOW + timedelta(minutes=1)
        failed = fixture.engine.evaluate_proposal_price_condition(
            proposal_id=proposal.proposal_id,
            observed_price=VerifiedPrice(
                proposal.instrument_id,
                "10001",
                Currency.KRW,
                check_at,
                "price-too-high",
                True,
            ),
            observation_cutoff_at=check_at,
            evaluated_at=check_at,
        )
        self.assertEqual(failed.decision.status, GateStatus.BLOCKED)
        self.assertEqual(
            fixture.engine.snapshot.proposals[proposal.proposal_id].state,
            ProposalState.TEMPORARILY_HELD,
        )
        with self.assertRaises(PaperBlockedError) as held:
            fixture.engine.reserve_proposal(
                ReservationRequest(
                    "held-price-reservation",
                    proposal.proposal_id,
                    check_at,
                ),
                execution_policy=confirmed_execution_policy(),
                risk_context=risk_context,
                candidate_approval_id=candidate_approval,
            )
        self.assertEqual(held.exception.code, "PROPOSAL_NOT_RESERVABLE")
        passed = fixture.engine.evaluate_proposal_price_condition(
            proposal_id=proposal.proposal_id,
            observed_price=VerifiedPrice(
                proposal.instrument_id,
                "10000",
                Currency.KRW,
                check_at + timedelta(microseconds=1),
                "price-at-threshold",
                True,
            ),
            observation_cutoff_at=check_at + timedelta(microseconds=1),
            evaluated_at=check_at + timedelta(microseconds=1),
        )
        self.assertTrue(passed.decision.permits_execution)
        reservation = fixture.engine.reserve_proposal(
            ReservationRequest(
                "passed-price-reservation",
                proposal.proposal_id,
                check_at + timedelta(microseconds=1),
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=risk_context,
            candidate_approval_id=candidate_approval,
        )
        self.assertEqual(reservation.state, ReservationState.ACTIVE)
        self.assertEqual(len(fixture.engine.snapshot.price_condition_checks), 2)

    def test_price_condition_checks_reject_time_rollback_and_equal_time_conflict(self) -> None:
        fixture = PaperFixture()
        proposal, candidate_approval = fixture.add_proposal(
            "price-condition-order",
            gross=10_000,
        )
        fixture.open_session(SESSION)
        risk_context = fixture.risk_context(proposal)
        latest_at = NOW + timedelta(minutes=2)
        fixture.engine.evaluate_proposal_price_condition(
            proposal_id=proposal.proposal_id,
            observed_price=VerifiedPrice(
                proposal.instrument_id,
                "10001",
                Currency.KRW,
                latest_at,
                "latest-price-too-high",
                True,
            ),
            observation_cutoff_at=latest_at,
            evaluated_at=latest_at,
        )
        blocked_snapshot = fixture.engine.snapshot

        with self.assertRaises(PaperBlockedError) as backdated:
            fixture.engine.evaluate_proposal_price_condition(
                proposal_id=proposal.proposal_id,
                observed_price=VerifiedPrice(
                    proposal.instrument_id,
                    "10000",
                    Currency.KRW,
                    latest_at - timedelta(microseconds=1),
                    "backdated-pass",
                    True,
                ),
                observation_cutoff_at=latest_at - timedelta(microseconds=1),
                evaluated_at=latest_at - timedelta(microseconds=1),
            )
        self.assertEqual(
            backdated.exception.code,
            "PRICE_CONDITION_TIME_ROLLBACK",
        )
        self.assertEqual(fixture.engine.snapshot, blocked_snapshot)

        with self.assertRaises(PaperBlockedError) as conflict:
            fixture.engine.evaluate_proposal_price_condition(
                proposal_id=proposal.proposal_id,
                observed_price=VerifiedPrice(
                    proposal.instrument_id,
                    "10000",
                    Currency.KRW,
                    latest_at,
                    "same-time-conflicting-pass",
                    True,
                ),
                observation_cutoff_at=latest_at,
                evaluated_at=latest_at,
            )
        self.assertEqual(
            conflict.exception.code,
            "PRICE_CONDITION_TIME_CONFLICT",
        )
        self.assertEqual(fixture.engine.snapshot, blocked_snapshot)

        later_at = latest_at + timedelta(microseconds=1)
        passed = fixture.engine.evaluate_proposal_price_condition(
            proposal_id=proposal.proposal_id,
            observed_price=VerifiedPrice(
                proposal.instrument_id,
                "10000",
                Currency.KRW,
                later_at,
                "causal-later-pass",
                True,
            ),
            observation_cutoff_at=later_at,
            evaluated_at=later_at,
        )
        self.assertTrue(passed.decision.permits_execution)
        reservation = fixture.engine.reserve_proposal(
            ReservationRequest(
                "causal-price-reservation",
                proposal.proposal_id,
                later_at,
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=risk_context,
            candidate_approval_id=candidate_approval,
        )
        self.assertEqual(reservation.state, ReservationState.ACTIVE)

    def test_sell_fill_below_minimum_price_is_blocked_without_mutation(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot(quantity=10, gross=100_000)
        proposal, candidate_approval = fixture.add_proposal(
            "sell-price-condition",
            instrument="TEST",
            gross=60_000,
            quantity=4,
            side=Side.SELL,
        )
        reservation = fixture.reserve(
            proposal,
            candidate_approval,
            sell_lots=(("lot-1", 4),),
        )
        run_id, event_id = fixture.begin_attempt(proposal)
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.engine.record_fill(
                observed_fill(
                    "sell-below-condition",
                    proposal,
                    reservation.reservation_id,
                    4,
                    krw_amounts(56_000),
                    NOW + timedelta(minutes=2),
                ),
                execution_policy=confirmed_execution_policy(),
                risk_context=fixture.risk_context(proposal),
                run_id=run_id,
                attempt_event_id=event_id,
            )
        self.assertEqual(blocked.exception.code, "PRICE_CONDITION_NOT_MET")
        self.assertEqual(fixture.engine.snapshot, before)

    def _usd_fill_case(self, suffix: str):
        fixture = PaperFixture()
        fixture.engine.record_fx(
            event_id=f"usd-funding-{suffix}",
            from_currency=Currency.KRW,
            from_amount="14300",
            to_currency=Currency.USD,
            to_amount="11",
            occurred_at=NOW,
            fx_rate=VerifiedFxRate(Currency.USD, "1300", NOW, "usd-funding-rate", True),
            cutoff_at=NOW,
            cost_krw="0",
            cost_model_confirmed=True,
        )
        result = calculate_trade_amounts(
            quantity=1,
            unit_price="10",
            currency=Currency.USD,
            maximum_cost="1",
            fx_krw_per_unit="1300",
        )
        assert result.amounts is not None
        proposal, candidate = fixture.add_proposal(
            f"usd-{suffix}",
            instrument="USD-ASSET",
            quantity=1,
            amounts=result.amounts,
        )
        reservation = fixture.reserve(proposal, candidate)
        run_id, event_id = fixture.begin_attempt(proposal)
        fill = observed_fill(
            f"usd-fill-{suffix}",
            proposal,
            reservation.reservation_id,
            1,
            result.amounts,
            NOW + timedelta(minutes=2),
        )
        return fixture, proposal, reservation, run_id, event_id, fill

    def test_usd_fill_uses_fixed_verified_fx_and_rejects_invalid_provenance(self) -> None:
        fixture, proposal, _reservation, run_id, event_id, fill = self._usd_fill_case("ok")
        fixture.engine.record_fill(
            fill,
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(proposal),
            run_id=run_id,
            attempt_event_id=event_id,
            lot_id="usd-lot",
        )
        state = fixture.engine.snapshot
        self.assertEqual(state.cash[Currency.USD], Decimal("0"))
        self.assertEqual(state.lots["usd-lot"].remaining_cost, Decimal("11"))
        self.assertEqual(
            tuple(entry.amount for entry in state.ledger_entries[-2:]),
            (Decimal("-10"), Decimal("-1")),
        )

        def invalid_fx(case: str, mutate):
            case_fixture, case_proposal, _reservation, case_run, case_event, case_fill = self._usd_fill_case(case)
            bad_fill = replace(case_fill, observed_fx_rate=mutate(case_fill.observed_fx_rate, case_fill))
            with self.assertRaises(PaperBlockedError) as blocked:
                case_fixture.engine.record_fill(
                    bad_fill,
                    execution_policy=confirmed_execution_policy(),
                    risk_context=case_fixture.risk_context(case_proposal),
                    run_id=case_run,
                    attempt_event_id=case_event,
                    lot_id=f"bad-lot-{case}",
                )
            self.assertEqual(blocked.exception.code, "VERIFIED_FILL_FX_REQUIRED")
            self.assertNotIn(bad_fill.fill_id, case_fixture.engine.snapshot.fills)

        invalid_fx("missing", lambda _fx, _fill: None)
        invalid_fx("unverified", lambda fx, _fill: replace(fx, verified=False))
        invalid_fx("source", lambda fx, _fill: replace(fx, source_record_id="other-source"))
        invalid_fx("as-of", lambda fx, _fill: replace(fx, as_of=fx.as_of + timedelta(microseconds=1)))
        invalid_fx(
            "future",
            lambda fx, case_fill: replace(
                fx,
                as_of=case_fill.observation_cutoff_at + timedelta(microseconds=1),
            ),
        )

    def test_fill_requires_run_attempt_and_immutable_policy_and_risk_context(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal("fill-gates", gross=10_000)
        reservation = fixture.reserve(proposal, candidate)
        fill = observed_fill(
            "fill-gates-event",
            proposal,
            reservation.reservation_id,
            1,
            krw_amounts(10_000),
            NOW + timedelta(minutes=2),
        )
        with self.assertRaises(PaperBlockedError) as no_run:
            fixture.engine.record_fill(
                fill,
                execution_policy=confirmed_execution_policy(),
                risk_context=fixture.risk_context(proposal),
                run_id="missing-run",
                attempt_event_id="missing-attempt",
            )
        self.assertEqual(no_run.exception.code, "RUNNING_EXECUTION_RUN_REQUIRED")

        run_id, event_id = fixture.begin_attempt(proposal)
        changed_policy = replace(
            confirmed_execution_policy(),
            per_order_limit_krw=Decimal("9999999"),
        )
        with self.assertRaises(PaperBlockedError) as policy_changed:
            fixture.engine.record_fill(
                fill,
                execution_policy=changed_policy,
                risk_context=fixture.risk_context(proposal),
                run_id=run_id,
                attempt_event_id=event_id,
            )
        self.assertEqual(policy_changed.exception.code, "EXECUTION_POLICY_CHANGED")

        original_context = fixture.risk_context(proposal)
        changed_classification = replace(
            original_context.classifications[0],
            evidence_ref="different-evidence",
        )
        changed_context = PaperRiskContext(
            original_context.valuation,
            (changed_classification,),
            original_context.policy,
        )
        with self.assertRaises(PaperBlockedError) as risk_changed:
            fixture.engine.record_fill(
                fill,
                execution_policy=confirmed_execution_policy(),
                risk_context=changed_context,
                run_id=run_id,
                attempt_event_id=event_id,
            )
        self.assertEqual(risk_changed.exception.code, "RISK_CONTEXT_CHANGED")
        self.assertNotIn(fill.fill_id, fixture.engine.snapshot.fills)

    def test_generated_fill_and_fee_ledger_ids_cannot_collide(self) -> None:
        for collision_kind in ("fill", "fee"):
            with self.subTest(collision_kind=collision_kind):
                fixture = PaperFixture()
                fixture.buy_lot()
                fixture.engine.record_dividend(
                    entry_id=f"{collision_kind}:collision",
                    strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
                    instrument_id="TEST",
                    amount="5",
                    currency=Currency.KRW,
                    occurred_at=NOW + timedelta(minutes=4),
                    recorded_at=NOW + timedelta(minutes=4),
                    source_ref="verified-dividend",
                )
                proposal, candidate = fixture.add_proposal(
                    f"ledger-collision-{collision_kind}",
                    instrument=f"COLLISION-{collision_kind}",
                    gross=10_000,
                    cost=1 if collision_kind == "fee" else 0,
                )
                reservation = fixture.reserve(proposal, candidate)
                run_id, event_id = fixture.begin_attempt(proposal)
                before = fixture.engine.snapshot
                with self.assertRaises(PaperBlockedError) as duplicate:
                    fixture.engine.record_fill(
                        observed_fill(
                            "collision",
                            proposal,
                            reservation.reservation_id,
                            1,
                            krw_amounts(
                                10_000,
                                1 if collision_kind == "fee" else 0,
                            ),
                            NOW + timedelta(minutes=5),
                        ),
                        execution_policy=confirmed_execution_policy(),
                        risk_context=fixture.risk_context(proposal),
                        run_id=run_id,
                        attempt_event_id=event_id,
                        lot_id=f"collision-lot-{collision_kind}",
                    )
                self.assertEqual(
                    duplicate.exception.code,
                    "LEDGER_ENTRY_ID_DUPLICATE",
                )
                self.assertEqual(fixture.engine.snapshot, before)

    def test_sell_realized_pnl_and_fee_are_separate_from_unrealized_value(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot(quantity=10, gross=100_000)
        sell, candidate = fixture.add_proposal(
            "realized-sell",
            instrument="TEST",
            gross=60_000,
            cost=1_000,
            quantity=4,
            side=Side.SELL,
        )
        reservation = fixture.reserve(sell, candidate, sell_lots=(("lot-1", 4),))
        run_id, event_id = fixture.begin_attempt(sell)
        recorded = fixture.engine.record_fill(
            observed_fill(
                "realized-fill",
                sell,
                reservation.reservation_id,
                4,
                krw_amounts(60_000, 1_000),
                NOW + timedelta(minutes=2),
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(sell),
            run_id=run_id,
            attempt_event_id=event_id,
        )
        self.assertEqual(recorded.realized_pnl_trade_currency, Decimal("19000"))
        state = fixture.engine.snapshot
        self.assertEqual(state.lots["lot-1"].remaining_quantity, 6)
        self.assertEqual(state.cash[Currency.KRW], Decimal("3959000"))
        self.assertEqual(
            [entry.event_type.value for entry in state.ledger_entries[-2:]],
            ["SELL", "FEE"],
        )
        final_sell, final_candidate = fixture.add_proposal(
            "realized-sell-final",
            instrument="TEST",
            gross=90_000,
            quantity=6,
            side=Side.SELL,
            session=SESSION + timedelta(days=1),
            kst_day=KST_DAY + timedelta(days=1),
        )
        final_reservation = fixture.reserve(
            final_sell,
            final_candidate,
            sell_lots=(("lot-1", 6),),
        )
        final_run, final_event = fixture.begin_attempt(final_sell)
        fixture.engine.record_fill(
            observed_fill(
                "realized-fill-final",
                final_sell,
                final_reservation.reservation_id,
                6,
                krw_amounts(90_000),
                NOW + timedelta(minutes=3),
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(final_sell),
            run_id=final_run,
            attempt_event_id=final_event,
        )
        exhausted = fixture.engine.snapshot.lots["lot-1"]
        self.assertEqual(exhausted.remaining_quantity, 0)
        self.assertEqual(exhausted.remaining_cost, Decimal("0"))

    def test_buy_and_sell_share_one_session_gross_limit(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot(
            proposal_id="limit-opening",
            instrument="HELD",
            quantity=10,
            gross=400_000,
        )
        buy, buy_approval = fixture.add_proposal(
            "limit-buy",
            instrument="BUY",
            gross=400_000,
        )
        sell, sell_approval = fixture.add_proposal(
            "limit-sell",
            instrument="HELD",
            gross=400_000,
            quantity=10,
            side=Side.SELL,
        )
        fixture.reserve(
            buy,
            buy_approval,
            large_approval_id=fixture.grant_large(buy.proposal_id),
        )
        fixture.reserve(
            sell,
            sell_approval,
            large_approval_id=fixture.grant_large(sell.proposal_id),
            sell_lots=(("lot-1", 10),),
        )
        limit = fixture.engine.snapshot.daily_limits[SESSION]
        self.assertEqual(limit.active_reserved_gross_krw, Decimal("800000"))
        extra, extra_approval = fixture.add_proposal(
            "limit-extra",
            instrument="EXTRA",
            gross=1,
        )
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.reserve(extra, extra_approval)
        self.assertEqual(blocked.exception.code, "DAILY_GROSS_LIMIT_EXCEEDED")

    def test_available_cash_shortage_blocks_without_mutation(self) -> None:
        fixture = PaperFixture()
        fixture.engine.record_fx(
            event_id="cash-shift",
            from_currency=Currency.KRW,
            from_amount="3900000",
            to_currency=Currency.USD,
            to_amount="3000",
            occurred_at=NOW,
            fx_rate=VerifiedFxRate(Currency.USD, "1300", NOW, "fx-source", True),
            cutoff_at=NOW,
            cost_krw="0",
            cost_model_confirmed=True,
        )
        proposal, approval = fixture.add_proposal("cash-short", gross=200_000)
        valuation = fixture.engine.value_portfolio(
            snapshot_id="cash-short-risk",
            as_of=NOW,
            cutoff_at=NOW,
            prices={},
            fx_rates={
                Currency.USD: VerifiedFxRate(
                    Currency.USD,
                    "1300",
                    NOW,
                    "fx-source",
                    True,
                )
            },
        )
        fixture.engine.open_daily_limit(
            session=VerifiedSessionWindow(
                SESSION,
                "cash-calendar",
                NOW,
                "cash-session-evidence",
                True,
            ),
            opening_valuation_snapshot_id=valuation.snapshot_id,
        )
        risk_context = PaperRiskContext(
            valuation,
            (
                InstrumentClassification(
                    proposal.instrument_id,
                    InstrumentType.BROAD_MARKET_ETF,
                    RiskRole.CORE,
                    "risk-v1",
                    "cash-risk-evidence",
                    True,
                ),
            ),
            confirmed_risk_policy(),
        )
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.reserve(proposal, approval, risk_context=risk_context)
        self.assertEqual(blocked.exception.code, "INSUFFICIENT_AVAILABLE_CASH")
        after = fixture.engine.snapshot
        self.assertEqual(after.cash, before.cash)
        self.assertEqual(after.reservations, before.reservations)

    def test_large_trade_approval_is_required_at_exact_boundary_and_for_split_group(self) -> None:
        for gross, required in ((299_999, False), (300_000, True), (300_001, True)):
            with self.subTest(gross=gross):
                fixture = PaperFixture()
                proposal, candidate = fixture.add_proposal(f"boundary-{gross}", gross=gross)
                if required:
                    with self.assertRaises(PaperBlockedError) as blocked:
                        fixture.reserve(proposal, candidate)
                    self.assertEqual(blocked.exception.code, "LARGE_TRADE_APPROVAL_REQUIRED")
                    fixture.reserve(
                        proposal,
                        candidate,
                        large_approval_id=fixture.grant_large(proposal.proposal_id),
                    )
                else:
                    fixture.reserve(proposal, candidate)

        split_fixture = PaperFixture()
        first, first_approval = split_fixture.add_proposal(
            "split-first",
            instrument="SPLIT",
            gross=149_999,
            intent="split-intent",
        )
        split_fixture.add_proposal(
            "split-second",
            instrument="SPLIT",
            gross=150_001,
            intent="split-intent",
        )
        self.assertEqual(
            split_fixture.engine.large_approval_group_amount(first.proposal_id),
            Decimal("300000"),
        )
        with self.assertRaises(PaperBlockedError) as blocked:
            split_fixture.reserve(first, first_approval)
        self.assertEqual(blocked.exception.code, "LARGE_TRADE_APPROVAL_REQUIRED")

        causal_fixture = PaperFixture()
        causal_proposal, causal_candidate = causal_fixture.add_proposal(
            "large-causal",
            gross=300_000,
        )
        causal_fixture.engine.grant_approval(
            approval_id="large-causal-backdated",
            approval_type=ApprovalType.LARGE_TRADE,
            target=causal_fixture.engine.large_approval_group_target(
                causal_proposal.proposal_id
            ),
            approved_by_user_id="user",
            approved_at=NOW - timedelta(microseconds=1),
            audit_ref="large-causal-backdated-audit",
        )
        with self.assertRaises(PaperBlockedError) as backdated:
            causal_fixture.reserve(
                causal_proposal,
                causal_candidate,
                large_approval_id="large-causal-backdated",
            )
        self.assertEqual(
            backdated.exception.code,
            "LARGE_TRADE_APPROVAL_BEFORE_EVIDENCE",
        )
        self.assertFalse(causal_fixture.engine.snapshot.reservations)

    def test_initial_nav_daily_limit_exact_and_one_won_over(self) -> None:
        fixture = PaperFixture()
        fixture.open_session(SESSION)
        limit = fixture.engine.snapshot.daily_limits[SESSION]
        self.assertEqual(limit.limit_krw, Decimal("800000"))
        proposal, candidate = fixture.add_proposal("exact", gross=800_000)
        large = fixture.grant_large("exact")
        fixture.reserve(proposal, candidate, large_approval_id=large)
        extra, extra_candidate = fixture.add_proposal("one-over", instrument="OTHER", gross=1)
        # group target is under 300k, so daily cap is the rejecting gate.
        with self.assertRaises(PaperBlockedError) as raised:
            fixture.reserve(extra, extra_candidate)
        self.assertEqual(raised.exception.code, "DAILY_GROSS_LIMIT_EXCEEDED")

    def test_fractional_twenty_percent_limit_uses_frozen_floor_contract(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot()
        fixture.engine.record_dividend(
            entry_id="fractional-limit-dividend",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            instrument_id="TEST",
            amount="1",
            currency=Currency.KRW,
            occurred_at=NOW + timedelta(minutes=4),
            recorded_at=NOW + timedelta(minutes=4),
            source_ref="verified-dividend",
        )
        fixture.open_session(SESSION)
        limit = fixture.engine.snapshot.daily_limits[SESSION]
        self.assertEqual(limit.opening_nav_krw, Decimal("4000001"))
        self.assertEqual(limit.limit_krw, Decimal("800000"))

        exact, exact_approval = fixture.add_proposal(
            "fractional-limit-exact",
            instrument="FRACTIONAL-EXACT",
            gross=800_000,
        )
        fixture.reserve(
            exact,
            exact_approval,
            large_approval_id=fixture.grant_large(exact.proposal_id),
        )
        one_over, one_over_approval = fixture.add_proposal(
            "fractional-limit-over",
            instrument="FRACTIONAL-OVER",
            gross=1,
        )
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.reserve(one_over, one_over_approval)
        self.assertEqual(blocked.exception.code, "DAILY_GROSS_LIMIT_EXCEEDED")

    def test_opening_nav_snapshot_is_rejected_after_material_state_change(self) -> None:
        fixture = PaperFixture()
        fixture.engine.value_portfolio(
            snapshot_id="stale-opening",
            as_of=NOW,
            cutoff_at=NOW,
            prices={},
            fx_rates={},
        )
        fixture.engine.record_fx(
            event_id="opening-state-change",
            from_currency=Currency.KRW,
            from_amount="1300",
            to_currency=Currency.USD,
            to_amount="1",
            occurred_at=NOW,
            fx_rate=VerifiedFxRate(Currency.USD, "1300", NOW, "fx-opening", True),
            cutoff_at=NOW,
            cost_krw="0",
            cost_model_confirmed=True,
        )
        with self.assertRaises(PaperBlockedError) as stale:
            fixture.engine.open_daily_limit(
                session=VerifiedSessionWindow(
                    SESSION,
                    "calendar-stale",
                    NOW,
                    "verified-session",
                    True,
                ),
                opening_valuation_snapshot_id="stale-opening",
            )
        self.assertEqual(stale.exception.code, "FRESH_OPENING_NAV_SNAPSHOT_REQUIRED")
        self.assertNotIn(SESSION, fixture.engine.snapshot.daily_limits)

    def test_same_session_opening_nav_is_immutable_across_kst_midnight_or_restart(self) -> None:
        fixture = PaperFixture()
        fixture.open_session(SESSION)
        restarted = PaperEngine(fixture.repository)
        same = restarted.open_daily_limit(
            session=VerifiedSessionWindow(
                SESSION,
                f"calendar-{SESSION}",
                NOW,
                "same-session-evidence",
                True,
            ),
            opening_valuation_snapshot_id=f"opening-{SESSION.isoformat()}",
        )
        self.assertEqual(same.limit_krw, Decimal("800000"))
        proposal, candidate = fixture.add_proposal(
            "restart-after-fill",
            instrument="RESTART",
            gross=10_000,
        )
        reservation = fixture.reserve(proposal, candidate)
        run_id, event_id = fixture.begin_attempt(proposal)
        fixture.engine.record_fill(
            observed_fill(
                "restart-after-fill-event",
                proposal,
                reservation.reservation_id,
                1,
                krw_amounts(10_000),
                NOW + timedelta(minutes=2),
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(proposal),
            run_id=run_id,
            attempt_event_id=event_id,
            lot_id="restart-after-fill-lot",
        )
        after_fill_restart = PaperEngine(fixture.repository).open_daily_limit(
            session=VerifiedSessionWindow(
                SESSION,
                f"calendar-{SESSION}",
                NOW,
                "post-fill-restart-evidence",
                True,
            ),
            opening_valuation_snapshot_id=f"opening-{SESSION.isoformat()}",
        )
        self.assertEqual(after_fill_restart.executed_gross_krw, Decimal("10000"))
        restarted.value_portfolio(
            snapshot_id="different-opening",
            as_of=NOW + timedelta(minutes=2),
            cutoff_at=NOW + timedelta(minutes=2),
            prices={
                "RESTART": VerifiedPrice(
                    "RESTART",
                    "10000",
                    Currency.KRW,
                    NOW + timedelta(minutes=2),
                    "restart-quote",
                    True,
                )
            },
            fx_rates={},
        )
        with self.assertRaises(PaperBlockedError) as changed:
            restarted.open_daily_limit(
                session=VerifiedSessionWindow(
                    SESSION,
                    f"calendar-{SESSION}",
                    NOW + timedelta(minutes=2),
                    "changed-session-evidence",
                    True,
                ),
                opening_valuation_snapshot_id="different-opening",
            )
        self.assertEqual(changed.exception.code, "SESSION_OPENING_LIMIT_IMMUTABLE")

    def test_concurrent_reservations_do_not_double_spend_daily_limit(self) -> None:
        fixture = PaperFixture()
        fixture.open_session(SESSION)
        proposals = []
        for index in range(2):
            proposal, candidate = fixture.add_proposal(
                f"concurrent-{index}",
                instrument=f"I{index}",
                gross=500_000,
            )
            proposals.append((proposal, candidate, fixture.grant_large(proposal.proposal_id)))
        barrier = threading.Barrier(2)

        def reserve(item):
            barrier.wait()
            try:
                fixture.reserve(item[0], item[1], large_approval_id=item[2])
                return "OK"
            except PaperBlockedError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, proposals))
        self.assertEqual(results.count("OK"), 1)
        self.assertEqual(results.count("DAILY_GROSS_LIMIT_EXCEEDED"), 1)
        limit = fixture.engine.snapshot.daily_limits[SESSION]
        self.assertEqual(limit.active_reserved_gross_krw, Decimal("500000"))

    def test_fill_moves_reserved_to_executed_once_and_partial_fill_only(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal("partial", gross=100_000, quantity=10)
        reservation = fixture.reserve(proposal, candidate)
        run_id, event_id = fixture.begin_attempt(proposal)
        fixture.engine.record_fill(
            observed_fill("fill-4", proposal, reservation.reservation_id, 4, krw_amounts(40_000), NOW + timedelta(minutes=2)),
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(proposal),
            run_id=run_id,
            attempt_event_id=event_id,
            lot_id="lot-4",
        )
        state = fixture.engine.snapshot
        self.assertEqual(state.cash[Currency.KRW], Decimal("3960000"))
        self.assertEqual(state.reservations[reservation.reservation_id].remaining_cash_amount, Decimal("60000"))
        self.assertEqual(state.daily_limits[SESSION].executed_gross_krw, Decimal("40000"))
        self.assertEqual(state.daily_limits[SESSION].active_reserved_gross_krw, Decimal("60000"))
        with self.assertRaises(PaperBlockedError) as false_no_fill:
            fixture.engine.confirm_attempt(
                run_id=run_id,
                event_id=event_id,
                result=AttemptResult.NO_FILL,
                confirmed_at=NOW + timedelta(minutes=2, seconds=1),
            )
        self.assertEqual(
            false_no_fill.exception.code,
            "NO_FILL_AFTER_RECORDED_FILL_FORBIDDEN",
        )
        fixture.engine.record_fill(
            observed_fill("fill-6", proposal, reservation.reservation_id, 6, krw_amounts(60_000), NOW + timedelta(minutes=3)),
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(proposal, refresh=True),
            run_id=run_id,
            attempt_event_id=event_id,
            lot_id="lot-6",
        )
        state = fixture.engine.snapshot
        self.assertEqual(state.cash[Currency.KRW], Decimal("3900000"))
        self.assertEqual(state.daily_limits[SESSION].executed_gross_krw, Decimal("100000"))
        self.assertEqual(state.daily_limits[SESSION].active_reserved_gross_krw, Decimal("0"))
        self.assertEqual(state.reservations[reservation.reservation_id].state, ReservationState.CONSUMED)
        with self.assertRaises(PaperBlockedError) as retroactive_confirmation:
            fixture.engine.confirm_attempt(
                run_id=run_id,
                event_id=event_id,
                result=AttemptResult.FILL_RECORDED,
                confirmed_at=NOW + timedelta(minutes=3) - timedelta(microseconds=1),
            )
        self.assertEqual(
            retroactive_confirmation.exception.code,
            "ATTEMPT_CONFIRMATION_TIME_INVALID",
        )
        fixture.engine.confirm_attempt(
            run_id=run_id,
            event_id=event_id,
            result=AttemptResult.FILL_RECORDED,
            confirmed_at=NOW + timedelta(minutes=3),
        )

    def test_sell_partial_fill_preserves_lot_and_remaining_quantity_reservation(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot(quantity=10, gross=100_000)
        proposal, candidate = fixture.add_proposal(
            "sell-partial-quantity",
            instrument="TEST",
            gross=60_000,
            quantity=6,
            side=Side.SELL,
        )
        reservation = fixture.reserve(
            proposal,
            candidate,
            sell_lots=(("lot-1", 6),),
        )
        run_id, event_id = fixture.begin_attempt(proposal)
        fixture.engine.record_fill(
            observed_fill(
                "sell-partial-four",
                proposal,
                reservation.reservation_id,
                4,
                krw_amounts(40_000),
                NOW + timedelta(minutes=2),
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(proposal),
            run_id=run_id,
            attempt_event_id=event_id,
        )
        partial_state = fixture.engine.snapshot
        self.assertEqual(
            partial_state.lots["lot-1"].remaining_quantity,
            6,
        )
        self.assertEqual(
            partial_state.lots["lot-1"].remaining_cost,
            Decimal("60000"),
        )
        self.assertEqual(
            partial_state.reservations[
                reservation.reservation_id
            ].remaining_sell_lot_quantities,
            (("lot-1", 2),),
        )
        self.assertEqual(
            partial_state.lots["lot-1"].remaining_quantity
            - sum(
                quantity
                for lot_id, quantity in partial_state.reservations[
                    reservation.reservation_id
                ].remaining_sell_lot_quantities
                if lot_id == "lot-1"
            ),
            4,
        )
        self.assertEqual(
            partial_state.daily_limits[SESSION].executed_gross_krw,
            Decimal("40000"),
        )
        self.assertEqual(
            partial_state.daily_limits[SESSION].active_reserved_gross_krw,
            Decimal("20000"),
        )
        fixture.engine.finalize_partial_fill(
            run_id=run_id,
            event_id=event_id,
            confirmed_at=NOW + timedelta(minutes=3),
            evidence_ref="verified-partial-sell",
            audit_id="sell-partial-finalized-audit",
        )
        finalized = fixture.engine.snapshot
        partial_audit = next(
            item
            for item in finalized.audits
            if item.audit_id == "sell-partial-finalized-audit"
        )
        self.assertEqual(partial_audit.related_ref, reservation.reservation_id)
        self.assertEqual(partial_audit.evidence_ref, "verified-partial-sell")
        self.assertEqual(
            finalized.reservations[reservation.reservation_id].state,
            ReservationState.RELEASED,
        )
        self.assertEqual(
            finalized.reservations[
                reservation.reservation_id
            ].remaining_sell_lot_quantities,
            (),
        )
        self.assertEqual(finalized.lots["lot-1"].remaining_quantity, 6)
        self.assertEqual(
            finalized.daily_limits[SESSION].active_reserved_gross_krw,
            Decimal("0"),
        )

        later_session = SESSION + timedelta(days=1)
        later_kst_day = KST_DAY + timedelta(days=1)
        over, over_candidate = fixture.add_proposal(
            "sell-after-partial-over",
            instrument="TEST",
            gross=70_000,
            quantity=7,
            side=Side.SELL,
            session=later_session,
            kst_day=later_kst_day,
        )
        with self.assertRaises(PaperBlockedError) as over_quantity:
            fixture.reserve(
                over,
                over_candidate,
                sell_lots=(("lot-1", 7),),
            )
        self.assertEqual(
            over_quantity.exception.code,
            "INSUFFICIENT_AVAILABLE_QUANTITY",
        )
        exact, exact_candidate = fixture.add_proposal(
            "sell-after-partial-exact",
            instrument="TEST",
            gross=60_000,
            quantity=6,
            side=Side.SELL,
            session=later_session,
            kst_day=later_kst_day,
        )
        exact_reservation = fixture.reserve(
            exact,
            exact_candidate,
            sell_lots=(("lot-1", 6),),
        )
        self.assertEqual(
            exact_reservation.remaining_sell_lot_quantities,
            (("lot-1", 6),),
        )

    def test_exact_common_stock_cap_remains_valid_across_partial_fills(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal(
            "exact-partial-cap",
            instrument="COMMON",
            gross=200_000,
            quantity=2,
        )

        def common_context(*, refresh: bool = False) -> PaperRiskContext:
            base = fixture.risk_context(proposal, refresh=refresh)
            return PaperRiskContext(
                base.valuation,
                (
                    InstrumentClassification(
                        "COMMON",
                        InstrumentType.COMMON_STOCK,
                        RiskRole.CORE,
                        "risk-v1",
                        "common-stock-evidence",
                        True,
                    ),
                ),
                base.policy,
            )

        reservation = fixture.reserve(
            proposal,
            candidate,
            risk_context=common_context(),
        )
        run_id, event_id = fixture.begin_attempt(proposal)
        fixture.engine.record_fill(
            observed_fill(
                "exact-partial-first",
                proposal,
                reservation.reservation_id,
                1,
                krw_amounts(100_000),
                NOW + timedelta(minutes=2),
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=common_context(),
            run_id=run_id,
            attempt_event_id=event_id,
            lot_id="exact-partial-lot-1",
        )
        fixture.engine.record_fill(
            observed_fill(
                "exact-partial-second",
                proposal,
                reservation.reservation_id,
                1,
                krw_amounts(100_000),
                NOW + timedelta(minutes=3),
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=common_context(refresh=True),
            run_id=run_id,
            attempt_event_id=event_id,
            lot_id="exact-partial-lot-2",
        )
        self.assertEqual(
            fixture.engine.snapshot.proposals[proposal.proposal_id].state,
            ProposalState.PAPER_RECORDED,
        )

    def test_fill_requires_current_state_valuation_and_accepts_fresh_revaluation(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot()
        proposal, candidate = fixture.add_proposal(
            "stale-fill-risk",
            instrument="FRESH",
            gross=10_000,
        )
        old_context = fixture.risk_context(proposal)
        reservation = fixture.reserve(
            proposal,
            candidate,
            risk_context=old_context,
        )
        run_id, event_id = fixture.begin_attempt(proposal)
        fixture.engine.record_dividend(
            entry_id="stale-risk-dividend",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            instrument_id="TEST",
            amount="1",
            currency=Currency.KRW,
            occurred_at=NOW + timedelta(minutes=3),
            recorded_at=NOW + timedelta(minutes=3),
            source_ref="verified-dividend",
        )
        fill = observed_fill(
            "fresh-risk-fill",
            proposal,
            reservation.reservation_id,
            1,
            krw_amounts(10_000),
            NOW + timedelta(minutes=4),
        )
        with self.assertRaises(PaperBlockedError) as stale:
            fixture.engine.record_fill(
                fill,
                execution_policy=confirmed_execution_policy(),
                risk_context=old_context,
                run_id=run_id,
                attempt_event_id=event_id,
            )
        self.assertEqual(stale.exception.code, "FRESH_RISK_SNAPSHOT_REQUIRED")
        fixture.engine.record_fill(
            fill,
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(proposal, refresh=True),
            run_id=run_id,
            attempt_event_id=event_id,
        )
        self.assertIn(fill.fill_id, fixture.engine.snapshot.fills)

    def test_duplicate_fill_id_is_rejected_without_double_decrement(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal("dup-fill", gross=10_000)
        reservation = fixture.reserve(proposal, candidate)
        run_id, event_id = fixture.begin_attempt(proposal)
        fill = observed_fill("same-fill", proposal, reservation.reservation_id, 1, krw_amounts(10_000), NOW + timedelta(minutes=2))
        fixture.engine.record_fill(
            fill,
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(proposal),
            run_id=run_id,
            attempt_event_id=event_id,
        )
        with self.assertRaises(PaperBlockedError) as duplicate:
            fixture.engine.record_fill(
                fill,
                execution_policy=confirmed_execution_policy(),
                risk_context=fixture.risk_context(proposal),
                run_id=run_id,
                attempt_event_id=event_id,
            )
        self.assertEqual(duplicate.exception.code, "DUPLICATE_FILL_ID")
        self.assertEqual(fixture.engine.snapshot.cash[Currency.KRW], Decimal("3990000"))

    def test_unknown_retains_cash_daily_and_sell_quantity_reservations(self) -> None:
        fixture = PaperFixture()
        fixture.buy_lot()
        sell, candidate = fixture.add_proposal(
            "unknown-sell",
            instrument="TEST",
            gross=60_000,
            quantity=6,
            side=Side.SELL,
            session=SESSION,
            kst_day=KST_DAY,
        )
        reservation = fixture.reserve(sell, candidate, sell_lots=(("lot-1", 6),))
        run_id, event_id = fixture.begin_attempt(sell)
        fixture.engine.mark_reservation_unknown(
            reservation.reservation_id,
            run_id=run_id,
            attempt_event_id=event_id,
            at=NOW + timedelta(minutes=2),
            reason_code="RESULT_NOT_CONFIRMED",
        )
        state = fixture.engine.snapshot
        self.assertEqual(state.reservations[reservation.reservation_id].remaining_sell_lot_quantities, (("lot-1", 6),))
        self.assertEqual(state.daily_limits[SESSION].active_reserved_gross_krw, Decimal("60000"))
        with self.assertRaises(PaperBlockedError) as release:
            fixture.engine.release_reservation(
                reservation.reservation_id,
                released_at=NOW + timedelta(minutes=3),
                reason_code="unsafe",
            )
        self.assertEqual(release.exception.code, "RECONCILIATION_REQUIRED_RESERVATION_RETAINED")
        second_sell, second_candidate = fixture.add_proposal(
            "unknown-second-sell",
            instrument="TEST",
            gross=50_000,
            quantity=5,
            side=Side.SELL,
            session=SESSION + timedelta(days=1),
            kst_day=KST_DAY + timedelta(days=1),
        )
        with self.assertRaises(PaperBlockedError) as second_blocked:
            fixture.reserve(
                second_sell,
                second_candidate,
                sell_lots=(("lot-1", 5),),
            )
        self.assertEqual(
            second_blocked.exception.code,
            "INSUFFICIENT_AVAILABLE_QUANTITY",
        )

    def test_unknown_buy_keeps_cash_reserved_and_blocks_second_buy(self) -> None:
        fixture = PaperFixture()
        fixture.engine.record_fx(
            event_id="unknown-cash-funding",
            from_currency=Currency.KRW,
            from_amount="3900000",
            to_currency=Currency.USD,
            to_amount="3000",
            occurred_at=NOW,
            fx_rate=VerifiedFxRate(
                Currency.USD,
                "1300",
                NOW,
                "unknown-cash-fx",
                True,
            ),
            cutoff_at=NOW,
            cost_krw="0",
            cost_model_confirmed=True,
        )
        first, first_approval = fixture.add_proposal(
            "unknown-cash-first",
            instrument="CASH-FIRST",
            gross=80_000,
        )
        first_reservation = fixture.reserve(first, first_approval)
        run_id, event_id = fixture.begin_attempt(first)
        fixture.engine.mark_reservation_unknown(
            first_reservation.reservation_id,
            run_id=run_id,
            attempt_event_id=event_id,
            at=NOW + timedelta(minutes=2),
            reason_code="RESULT_NOT_CONFIRMED",
        )
        state = fixture.engine.snapshot
        self.assertEqual(
            state.reservations[first_reservation.reservation_id].remaining_cash_amount,
            Decimal("80000"),
        )
        second, second_approval = fixture.add_proposal(
            "unknown-cash-second",
            instrument="CASH-SECOND",
            gross=30_000,
            session=SESSION + timedelta(days=1),
            kst_day=KST_DAY + timedelta(days=1),
        )
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.reserve(second, second_approval)
        self.assertEqual(blocked.exception.code, "INSUFFICIENT_AVAILABLE_CASH")

    def _sell_concurrency(self, first: int, second: int) -> list[str]:
        fixture = PaperFixture()
        fixture.buy_lot()
        proposals = []
        for index, quantity in enumerate((first, second)):
            proposal, candidate = fixture.add_proposal(
                f"sell-{index}",
                instrument="TEST",
                gross=quantity * 10_000,
                quantity=quantity,
                side=Side.SELL,
                session=SESSION + timedelta(days=index),
                kst_day=KST_DAY + timedelta(days=index),
                intent=f"sell-intent-{index}",
            )
            proposals.append((proposal, candidate, quantity))
        barrier = threading.Barrier(2)

        def reserve(item):
            barrier.wait()
            try:
                fixture.reserve(item[0], item[1], sell_lots=(("lot-1", item[2]),))
                return "OK"
            except PaperBlockedError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(reserve, proposals))

    def test_concurrent_sell_6_plus_5_rejects_one_but_6_plus_4_allows_both(self) -> None:
        over = self._sell_concurrency(6, 5)
        exact = self._sell_concurrency(6, 4)
        self.assertEqual(over.count("OK"), 1)
        self.assertEqual(over.count("INSUFFICIENT_AVAILABLE_QUANTITY"), 1)
        self.assertEqual(exact.count("OK"), 2)

    def test_unconfirmed_execution_policy_blocks_reservation(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal("policy-missing", gross=10_000)
        missing = PaperExecutionPolicy("policy-v1", None, None, None, None, None, None, None, None)
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.reserve(proposal, candidate, policy=missing)
        self.assertEqual(blocked.exception.code, "COST_POLICY_INPUT_REQUIRED")

    def test_each_unconfirmed_execution_policy_input_fails_closed(self) -> None:
        cases = (
            ("cost_model_confirmed", None, "COST_POLICY_INPUT_REQUIRED"),
            ("monthly_budget_applies_to_buy", None, "MONTHLY_BUDGET_APPLICABILITY_REQUIRED"),
            ("per_order_limit_krw", None, "PER_ORDER_LIMIT_POLICY_INPUT_REQUIRED"),
        )
        for field_name, value, expected_code in cases:
            with self.subTest(field_name=field_name):
                fixture = PaperFixture()
                proposal, candidate = fixture.add_proposal(
                    f"missing-{field_name}",
                    gross=10_000,
                )
                policy = replace(confirmed_execution_policy(), **{field_name: value})
                with self.assertRaises(PaperBlockedError) as blocked:
                    fixture.reserve(proposal, candidate, policy=policy)
                self.assertEqual(blocked.exception.code, expected_code)

        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal("monthly-required", gross=10_000)
        monthly_missing = replace(
            confirmed_execution_policy(),
            monthly_budget_applies_to_buy=True,
            monthly_budget_krw=None,
            monthly_budget_month=None,
        )
        with self.assertRaises(PaperBlockedError) as monthly:
            fixture.reserve(proposal, candidate, policy=monthly_missing)
        self.assertEqual(monthly.exception.code, "MONTHLY_BUDGET_INPUT_REQUIRED")

        fixture = PaperFixture()
        usd_result = calculate_trade_amounts(
            quantity=1,
            unit_price="10",
            currency=Currency.USD,
            maximum_cost="0",
            fx_krw_per_unit="1300",
        )
        usd_proposal, usd_candidate = fixture.add_proposal(
            "fx-policy-required",
            gross=13_000,
            amounts=usd_result.amounts,
        )
        with self.assertRaises(PaperBlockedError) as fx_missing:
            fixture.reserve(
                usd_proposal,
                usd_candidate,
                policy=replace(confirmed_execution_policy(), fx_model_confirmed=None),
            )
        self.assertEqual(fx_missing.exception.code, "FX_POLICY_INPUT_REQUIRED")

        fixture = PaperFixture()
        fixture.buy_lot()
        sell, sell_candidate = fixture.add_proposal(
            "basis-required",
            instrument="TEST",
            gross=10_000,
            quantity=1,
            side=Side.SELL,
        )
        with self.assertRaises(PaperBlockedError) as basis_missing:
            fixture.reserve(
                sell,
                sell_candidate,
                sell_lots=(("lot-1", 1),),
                policy=replace(confirmed_execution_policy(), cost_basis_method=None),
            )
        self.assertEqual(basis_missing.exception.code, "COST_BASIS_POLICY_INPUT_REQUIRED")

    def test_monthly_budget_counts_active_reservations_across_sessions(self) -> None:
        fixture = PaperFixture()
        policy = replace(
            confirmed_execution_policy(),
            monthly_budget_applies_to_buy=True,
            monthly_budget_krw=Decimal("100000"),
        )
        first, first_candidate = fixture.add_proposal("monthly-first", gross=60_000)
        fixture.reserve(first, first_candidate, policy=policy)
        second, second_candidate = fixture.add_proposal(
            "monthly-second",
            instrument="OTHER",
            gross=40_001,
            session=SESSION + timedelta(days=1),
            kst_day=KST_DAY + timedelta(days=1),
        )
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.reserve(second, second_candidate, policy=policy)
        self.assertEqual(blocked.exception.code, "MONTHLY_BUDGET_EXCEEDED")

    def test_monthly_budget_keeps_partial_fill_after_remainder_release(self) -> None:
        fixture = PaperFixture()
        policy = replace(
            confirmed_execution_policy(),
            monthly_budget_applies_to_buy=True,
            monthly_budget_krw=Decimal("100000"),
        )
        proposal, candidate = fixture.add_proposal(
            "monthly-partial",
            gross=100_000,
            quantity=10,
        )
        reservation = fixture.reserve(proposal, candidate, policy=policy)
        run_id, event_id = fixture.begin_attempt(proposal)
        fixture.engine.record_fill(
            observed_fill(
                "monthly-fill-4",
                proposal,
                reservation.reservation_id,
                4,
                krw_amounts(40_000),
                NOW + timedelta(minutes=2),
            ),
            execution_policy=policy,
            risk_context=fixture.risk_context(proposal),
            run_id=run_id,
            attempt_event_id=event_id,
        )
        fixture.engine.finalize_partial_fill(
            run_id=run_id,
            event_id=event_id,
            confirmed_at=NOW + timedelta(minutes=3),
            evidence_ref="partial-fill-confirmation",
            audit_id="audit-monthly-partial",
        )
        over, over_candidate = fixture.add_proposal(
            "monthly-after-partial-over",
            instrument="OVER",
            gross=60_001,
            session=SESSION + timedelta(days=1),
            kst_day=KST_DAY + timedelta(days=1),
        )
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.reserve(over, over_candidate, policy=policy)
        self.assertEqual(blocked.exception.code, "MONTHLY_BUDGET_EXCEEDED")
        exact, exact_candidate = fixture.add_proposal(
            "monthly-after-partial-exact",
            instrument="EXACT",
            gross=60_000,
            session=SESSION + timedelta(days=2),
            kst_day=KST_DAY + timedelta(days=2),
        )
        fixture.reserve(exact, exact_candidate, policy=policy)

    def test_concurrent_risk_reservations_cannot_bypass_common_stock_cap(self) -> None:
        fixture = PaperFixture()
        proposals: list[tuple[TradeProposal, str, PaperRiskContext]] = []
        for index in range(2):
            proposal, candidate = fixture.add_proposal(
                f"risk-concurrent-{index}",
                instrument="SAME-STOCK",
                gross=120_000,
                session=SESSION + timedelta(days=index),
                kst_day=KST_DAY + timedelta(days=index),
            )
            valuation = fixture.engine.value_portfolio(
                snapshot_id=f"risk-concurrent-snapshot-{index}",
                as_of=NOW,
                cutoff_at=NOW,
                prices={},
                fx_rates={},
            )
            context = PaperRiskContext(
                valuation,
                (
                    InstrumentClassification(
                        "SAME-STOCK",
                        InstrumentType.COMMON_STOCK,
                        RiskRole.CORE,
                        "risk-v1",
                        f"risk-evidence-{index}",
                        True,
                    ),
                ),
                confirmed_risk_policy(),
            )
            proposals.append((proposal, candidate, context))
        barrier = threading.Barrier(2)

        def reserve(item: tuple[TradeProposal, str, PaperRiskContext]) -> str:
            barrier.wait()
            try:
                fixture.reserve(item[0], item[1], risk_context=item[2])
                return "OK"
            except PaperBlockedError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, proposals))
        self.assertEqual(results.count("OK"), 1)
        self.assertEqual(results.count("RISK_GATE_NOT_PASS"), 1)

    def test_fill_cannot_use_price_from_before_reservation(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal("retro", gross=10_000)
        reservation = fixture.reserve(proposal, candidate)
        run_id, event_id = fixture.begin_attempt(proposal)
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.engine.record_fill(
                observed_fill("retro-fill", proposal, reservation.reservation_id, 1, krw_amounts(10_000), NOW),
                execution_policy=confirmed_execution_policy(),
                risk_context=fixture.risk_context(proposal),
                run_id=run_id,
                attempt_event_id=event_id,
            )
        self.assertEqual(blocked.exception.code, "FILL_OUTSIDE_EXECUTION_WINDOW")


class PaperApprovalPurposeTests(unittest.TestCase):
    def test_bound_approval_expiry_releases_no_attempt_reservation_atomically(self) -> None:
        fixture = PaperFixture()
        proposal, candidate_approval = fixture.add_proposal(
            "approval-expiry-lifecycle",
            gross=10_000,
        )
        reservation = fixture.reserve(proposal, candidate_approval)
        expired = fixture.engine.expire_approval(
            candidate_approval,
            expired_at=NOW + APPROVAL_LIFETIME,
            evidence_ref="deterministic-expiry-boundary",
            audit_id="approval-expiry-audit",
        )
        state = fixture.engine.snapshot
        self.assertEqual(expired.state, ApprovalState.EXPIRED)
        self.assertEqual(
            state.reservations[reservation.reservation_id].state,
            ReservationState.RELEASED,
        )
        self.assertEqual(
            state.proposals[proposal.proposal_id].state,
            ProposalState.EXPIRED,
        )
        self.assertEqual(
            state.candidate_sets[proposal.candidate_set_id].state,
            CandidateSetState.EXPIRED,
        )
        self.assertEqual(
            state.daily_limits[SESSION].active_reserved_gross_krw,
            Decimal("0"),
        )
        terminal_snapshot = fixture.engine.snapshot
        terminal_calls = (
            lambda: fixture.engine.cancel_approval(
                candidate_approval,
                cancelled_at=NOW + APPROVAL_LIFETIME + timedelta(microseconds=1),
                audit_id="cancel-expired-approval",
            ),
            lambda: fixture.engine.invalidate_approval(
                candidate_approval,
                invalidated_at=NOW + APPROVAL_LIFETIME + timedelta(microseconds=1),
                reason_code="TARGET_CHANGED",
                audit_id="invalidate-expired-approval",
            ),
            lambda: fixture.engine.expire_approval(
                candidate_approval,
                expired_at=NOW + APPROVAL_LIFETIME + timedelta(microseconds=1),
                evidence_ref="repeat-expiry",
                audit_id="repeat-expiry-audit",
            ),
        )
        for operation in terminal_calls:
            with self.subTest(operation=operation), self.assertRaises(PaperBlockedError):
                operation()
            self.assertEqual(fixture.engine.snapshot, terminal_snapshot)

    def test_unknown_result_time_is_approval_state_floor(self) -> None:
        for action in ("cancel", "invalidate", "expire"):
            with self.subTest(action=action):
                fixture = PaperFixture()
                proposal, approval_id = fixture.add_proposal(
                    f"unknown-approval-floor-{action}",
                    gross=10_000,
                )
                reservation = fixture.reserve(proposal, approval_id)
                run_id, event_id = fixture.begin_attempt(proposal)
                unknown_at = (
                    NOW + timedelta(hours=25)
                    if action == "expire"
                    else NOW + timedelta(minutes=30)
                )
                fixture.engine.mark_reservation_unknown(
                    reservation.reservation_id,
                    run_id=run_id,
                    attempt_event_id=event_id,
                    at=unknown_at,
                    reason_code="RESULT_NOT_CONFIRMED",
                )

                def change_approval(at: datetime, suffix: str) -> None:
                    if action == "cancel":
                        fixture.engine.cancel_approval(
                            approval_id,
                            cancelled_at=at,
                            audit_id=f"unknown-cancel-{suffix}",
                        )
                    elif action == "invalidate":
                        fixture.engine.invalidate_approval(
                            approval_id,
                            invalidated_at=at,
                            reason_code="TARGET_CHANGED",
                            audit_id=f"unknown-invalidate-{suffix}",
                        )
                    else:
                        fixture.engine.expire_approval(
                            approval_id,
                            expired_at=at,
                            evidence_ref="deterministic-expiry",
                            audit_id=f"unknown-expire-{suffix}",
                        )

                before = fixture.engine.snapshot
                with self.assertRaises(PaperBlockedError) as backdated:
                    change_approval(
                        unknown_at - timedelta(microseconds=1),
                        "before",
                    )
                self.assertEqual(
                    backdated.exception.code,
                    "APPROVAL_STATE_TIME_INVALID",
                )
                self.assertEqual(fixture.engine.snapshot, before)
                change_approval(unknown_at, "exact")
                expected = {
                    "cancel": ApprovalState.CANCELLED,
                    "invalidate": ApprovalState.INVALIDATED,
                    "expire": ApprovalState.EXPIRED,
                }[action]
                self.assertEqual(
                    fixture.engine.snapshot.approvals[approval_id].state,
                    expected,
                )

    def test_cancel_or_invalidate_bound_approval_releases_reservation_atomically(self) -> None:
        for action in ("cancel", "invalidate"):
            with self.subTest(action=action):
                fixture = PaperFixture()
                proposal, candidate = fixture.add_proposal(f"approval-{action}", gross=100_000)
                reservation = fixture.reserve(proposal, candidate)
                if action == "cancel":
                    fixture.engine.cancel_approval(
                        candidate,
                        cancelled_at=NOW + timedelta(minutes=2),
                        audit_id="cancel-audit",
                    )
                    expected_state = ApprovalState.CANCELLED
                else:
                    fixture.engine.invalidate_approval(
                        candidate,
                        invalidated_at=NOW + timedelta(minutes=2),
                        reason_code="TARGET_CHANGED",
                        audit_id="invalidate-audit",
                    )
                    expected_state = ApprovalState.INVALIDATED
                state = fixture.engine.snapshot
                self.assertEqual(state.approvals[candidate].state, expected_state)
                self.assertEqual(state.reservations[reservation.reservation_id].state, ReservationState.RELEASED)
                self.assertEqual(state.daily_limits[SESSION].active_reserved_gross_krw, Decimal("0"))
                self.assertEqual(state.proposals[proposal.proposal_id].state, ProposalState.CANCELLED)

    def test_terminal_approval_holds_ready_run_and_cannot_be_reactivated(self) -> None:
        for action in ("cancel", "invalidate", "expire"):
            with self.subTest(action=action):
                fixture = PaperFixture()
                proposal, approval_id = fixture.add_proposal(
                    f"ready-run-{action}",
                    gross=10_000,
                )
                reservation = fixture.reserve(proposal, approval_id)
                included = ((proposal.proposal_id, proposal.version),)
                run = ExecutionRun(
                    f"approval-run-{action}",
                    "paper-portfolio",
                    SESSION,
                    included,
                    canonical_hash(included),
                    NOW,
                    NOW + timedelta(hours=30),
                    RunState.READY,
                )
                fixture.engine.create_execution_run(
                    run,
                    gates=GateDecision.passed(),
                )
                changed_at = (
                    NOW + APPROVAL_LIFETIME
                    if action == "expire"
                    else NOW + timedelta(minutes=2)
                )
                if action == "cancel":
                    fixture.engine.cancel_approval(
                        approval_id,
                        cancelled_at=changed_at,
                        audit_id="ready-run-cancel-audit",
                    )
                    expected_proposal_state = ProposalState.CANCELLED
                elif action == "invalidate":
                    fixture.engine.invalidate_approval(
                        approval_id,
                        invalidated_at=changed_at,
                        reason_code="TARGET_CHANGED",
                        audit_id="ready-run-invalidate-audit",
                    )
                    expected_proposal_state = ProposalState.CANCELLED
                else:
                    fixture.engine.expire_approval(
                        approval_id,
                        expired_at=changed_at,
                        evidence_ref="expiry-boundary",
                        audit_id="ready-run-expire-audit",
                    )
                    expected_proposal_state = ProposalState.EXPIRED
                state = fixture.engine.snapshot
                self.assertEqual(
                    state.runs[run.execution_run_id].state,
                    RunState.TEMPORARILY_HELD,
                )
                self.assertEqual(
                    state.reservations[reservation.reservation_id].state,
                    ReservationState.RELEASED,
                )
                self.assertEqual(
                    state.proposals[proposal.proposal_id].state,
                    expected_proposal_state,
                )
                before_reactivation = fixture.engine.snapshot
                with self.assertRaises(PaperBlockedError) as blocked:
                    fixture.engine.reactivate_execution_run(
                        run.execution_run_id,
                        explicitly_requested=True,
                        gates=GateDecision.passed(),
                        requested_at=changed_at + timedelta(microseconds=1),
                        evidence_ref="explicit-recheck",
                        audit_id=f"invalid-reactivation-{action}",
                    )
                self.assertEqual(
                    blocked.exception.code,
                    "EXECUTION_RUN_GATES_NOT_PASS",
                )
                self.assertEqual(fixture.engine.snapshot, before_reactivation)
                closed = fixture.engine.close_execution_window(
                    run.execution_run_id,
                    ended_at=NOW + timedelta(hours=30),
                )
                self.assertEqual(closed.state, RunState.MISSED_WINDOW)
                self.assertEqual(
                    fixture.engine.snapshot.proposals[proposal.proposal_id].state,
                    expected_proposal_state,
                )

    def test_reservation_release_cannot_precede_reservation_creation(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal(
            "backdated-reservation-release",
            gross=10_000,
        )
        reservation = fixture.reserve(proposal, candidate)
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as backdated:
            fixture.engine.release_reservation(
                reservation.reservation_id,
                released_at=reservation.created_at - timedelta(microseconds=1),
                reason_code="USER_CANCELLED",
            )
        self.assertEqual(
            backdated.exception.code,
            "RESERVATION_STATE_TIME_INVALID",
        )
        self.assertEqual(fixture.engine.snapshot, before)

    def test_reservation_release_cannot_precede_later_run_hold(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal(
            "release-before-run-hold",
            gross=10_000,
        )
        reservation = fixture.reserve(proposal, candidate)
        included = ((proposal.proposal_id, proposal.version),)
        run = ExecutionRun(
            "release-hold-run",
            "paper-portfolio",
            SESSION,
            included,
            canonical_hash(included),
            NOW,
            NOW + timedelta(hours=1),
            RunState.READY,
        )
        fixture.engine.create_execution_run(run, gates=GateDecision.passed())
        fixture.engine.hold_execution_run(
            run.execution_run_id,
            held_at=NOW + timedelta(minutes=10),
            reason_code="PRICE_CONDITION_NOT_MET",
            evidence_ref="verified-hold",
            audit_id="release-hold-audit",
        )
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as backdated:
            fixture.engine.release_reservation(
                reservation.reservation_id,
                released_at=NOW + timedelta(minutes=5),
                reason_code="USER_CANCELLED",
            )
        self.assertEqual(
            backdated.exception.code,
            "RESERVATION_STATE_TIME_INVALID",
        )
        self.assertEqual(fixture.engine.snapshot, before)

    def test_approved_candidate_set_terminal_transitions_require_atomic_lifecycle(self) -> None:
        for target_state in (
            CandidateSetState.EXPIRED,
            CandidateSetState.REGENERATION_REQUIRED,
        ):
            with self.subTest(target_state=target_state):
                fixture = PaperFixture()
                proposal, candidate = fixture.add_proposal(
                    f"generic-transition-{target_state.value}",
                    gross=10_000,
                )
                fixture.reserve(proposal, candidate)
                included = ((proposal.proposal_id, proposal.version),)
                fixture.engine.create_execution_run(
                    ExecutionRun(
                        f"generic-transition-run-{target_state.value}",
                        "paper-portfolio",
                        SESSION,
                        included,
                        canonical_hash(included),
                        NOW,
                        NOW + timedelta(hours=1),
                        RunState.READY,
                    ),
                    gates=GateDecision.passed(),
                )
                before = fixture.engine.snapshot
                with self.assertRaises(PaperBlockedError) as blocked:
                    fixture.engine.transition_candidate_set(
                        proposal.candidate_set_id,
                        target_state,
                    )
                self.assertEqual(
                    blocked.exception.code,
                    "INVALID_CANDIDATE_SET_TRANSITION",
                )
                self.assertEqual(fixture.engine.snapshot, before)

    def test_approval_and_reconciliation_times_follow_closed_run_causality(self) -> None:
        for action in ("cancel", "invalidate"):
            with self.subTest(action=action):
                fixture = PaperFixture()
                proposal, approval_id = fixture.add_proposal(
                    f"closed-run-{action}",
                    gross=10_000,
                )
                reservation = fixture.reserve(proposal, approval_id)
                run_id, event_id = fixture.begin_attempt(proposal)
                window_end = NOW + timedelta(hours=1)
                fixture.engine.close_execution_window(run_id, ended_at=window_end)

                def change_approval(at: datetime) -> None:
                    if action == "cancel":
                        fixture.engine.cancel_approval(
                            approval_id,
                            cancelled_at=at,
                            audit_id=f"closed-run-cancel-{utc_epoch_microseconds(at)}",
                        )
                    else:
                        fixture.engine.invalidate_approval(
                            approval_id,
                            invalidated_at=at,
                            reason_code="TARGET_CHANGED",
                            audit_id=f"closed-run-invalidate-{utc_epoch_microseconds(at)}",
                        )

                with self.assertRaises(PaperBlockedError) as before_close:
                    change_approval(window_end - timedelta(microseconds=1))
                self.assertEqual(
                    before_close.exception.code,
                    "APPROVAL_STATE_TIME_INVALID",
                )

                approval_changed_at = window_end + timedelta(hours=1)
                change_approval(approval_changed_at)
                with self.assertRaises(PaperBlockedError) as before_approval_change:
                    fixture.engine.reconcile_reservation_no_fill(
                        reservation.reservation_id,
                        run_id=run_id,
                        attempt_event_id=event_id,
                        reconciled_at=approval_changed_at - timedelta(microseconds=1),
                        evidence_ref="verified-no-fill",
                        audit_id="retroactive-reconcile",
                    )
                self.assertEqual(
                    before_approval_change.exception.code,
                    "RECONCILIATION_TIME_INVALID",
                )
                fixture.engine.reconcile_reservation_no_fill(
                    reservation.reservation_id,
                    run_id=run_id,
                    attempt_event_id=event_id,
                    reconciled_at=approval_changed_at,
                    evidence_ref="verified-no-fill",
                    audit_id="causal-reconcile",
                )
                self.assertEqual(
                    fixture.engine.snapshot.reservations[reservation.reservation_id].released_at,
                    approval_changed_at,
                )

    def _group_amount(self, values: tuple[int, ...]) -> Decimal:
        fixture = PaperFixture()
        first_id = ""
        for index, value in enumerate(values):
            proposal, _ = fixture.add_proposal(
                f"aggregate-{index}",
                instrument="SAME",
                gross=value,
                intent="same-intent",
            )
            first_id = first_id or proposal.proposal_id
        return fixture.engine.large_approval_group_amount(first_id)

    def test_large_trade_exact_boundary_and_split_aggregation(self) -> None:
        self.assertEqual(self._group_amount((299_999,)), Decimal("299999"))
        self.assertLess(self._group_amount((299_999,)), LARGE_TRADE_THRESHOLD_KRW)
        self.assertEqual(self._group_amount((300_000,)), LARGE_TRADE_THRESHOLD_KRW)
        self.assertGreater(self._group_amount((300_001,)), LARGE_TRADE_THRESHOLD_KRW)
        self.assertEqual(self._group_amount((149_999, 150_001)), LARGE_TRADE_THRESHOLD_KRW)

    def test_same_root_descendant_versions_keep_large_trade_total(self) -> None:
        fixture = PaperFixture()
        first, _ = fixture.add_proposal("v1", instrument="SAME", gross=200_000, intent="same")
        purpose_v2_hash = trade_purpose_version_hash(
            trade_purpose_id="purpose-v2",
            stable_root_trade_purpose_id="root-1",
            purpose_version="v2",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            strategy_version_ref="strategy-v1",
            budget_version_ref="budget-v1",
            policy_version_ref="policy-v1",
            intended_effect="가상 장기투자 검증",
            parent_trade_purpose_id="purpose-v1",
        )
        fixture.engine.create_trade_purpose(
            TradePurpose(
                trade_purpose_id="purpose-v2",
                stable_root_trade_purpose_id="root-1",
                purpose_version="v2",
                purpose_version_hash=purpose_v2_hash,
                strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
                strategy_version_ref="strategy-v1",
                budget_version_ref="budget-v1",
                policy_version_ref="policy-v1",
                intended_effect="가상 장기투자 검증",
                created_at=NOW + timedelta(minutes=1),
                parent_trade_purpose_id="purpose-v1",
            )
        )
        fixture.add_proposal(
            "v2",
            instrument="SAME",
            gross=100_000,
            intent="same",
            purpose_id="purpose-v2",
            purpose_version="v2",
        )
        self.assertEqual(fixture.engine.large_approval_group_amount(first.proposal_id), Decimal("300000"))

    def test_approval_expiry_exact_boundary_type_and_target_are_enforced(self) -> None:
        fixture = PaperFixture()
        target = ApprovalTarget("target", "v1", "hash")
        fixture.engine.grant_approval(
            approval_id="approval-before",
            approval_type=ApprovalType.CANDIDATE_SET,
            target=target,
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref="audit-before",
        )
        valid = fixture.engine.bind_approval(
            approval_id="approval-before",
            expected_type=ApprovalType.CANDIDATE_SET,
            expected_target=target,
            consumer_ref="target",
            now=NOW + APPROVAL_LIFETIME - timedelta(microseconds=1),
        )
        self.assertEqual(valid.state, ApprovalState.BOUND)
        for suffix, now in (("exact", NOW + APPROVAL_LIFETIME), ("after", NOW + APPROVAL_LIFETIME + timedelta(microseconds=1))):
            fixture.engine.grant_approval(
                approval_id=f"approval-{suffix}",
                approval_type=ApprovalType.CANDIDATE_SET,
                target=target,
                approved_by_user_id="user",
                approved_at=NOW,
                audit_ref=f"audit-{suffix}",
            )
            with self.assertRaises(PaperBlockedError) as expired:
                fixture.engine.bind_approval(
                    approval_id=f"approval-{suffix}",
                    expected_type=ApprovalType.CANDIDATE_SET,
                    expected_target=target,
                    consumer_ref="target",
                    now=now,
                )
            self.assertEqual(expired.exception.code, "APPROVAL_EXPIRED")
        with self.assertRaises(PaperBlockedError) as wrong_type:
            fixture.engine.bind_approval(
                approval_id="approval-before",
                expected_type=ApprovalType.LARGE_TRADE,
                expected_target=target,
                consumer_ref="target",
                now=NOW + timedelta(hours=1),
            )
        self.assertEqual(wrong_type.exception.code, "APPROVAL_TYPE_MISMATCH")
        with self.assertRaises(PaperBlockedError) as wrong_target:
            fixture.engine.bind_approval(
                approval_id="approval-before",
                expected_type=ApprovalType.CANDIDATE_SET,
                expected_target=ApprovalTarget("target", "v2", "changed"),
                consumer_ref="target",
                now=NOW + timedelta(hours=1),
            )
        self.assertEqual(wrong_target.exception.code, "APPROVAL_TARGET_MISMATCH")

    def _new_root_fixture(self, *, target_policy_version: str = "policy-v1"):
        fixture = PaperFixture()
        purpose_hash = trade_purpose_version_hash(
            trade_purpose_id="purpose-new",
            stable_root_trade_purpose_id="root-2",
            purpose_version="v1",
            strategy_id=PaperStrategyKind.VALUE,
            strategy_version_ref="strategy-v1",
            budget_version_ref="budget-v1",
            policy_version_ref="policy-v1",
            intended_effect="새로운 가상 목적",
            changed_from_root_id="root-1",
        )
        target = NewRootPurposeTarget(
            "root-1",
            "root-2",
            "purpose-new",
            "v1",
            purpose_hash,
            "evidence-ref",
            "evidence-hash",
            "실제 경제적 목적 변경",
            SplitCheckResult.PASS,
            "split-input-hash",
            target_policy_version,
            NOW,
            NOW - timedelta(minutes=1),
        )
        fixture.engine.grant_approval(
            approval_id="new-root-approval",
            approval_type=ApprovalType.NEW_ROOT_PURPOSE,
            target=ApprovalTarget("purpose-new", "v1", purpose_hash),
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref="new-root-grant-audit",
            new_root_target=target,
        )
        return fixture, target

    def test_three_approval_types_cannot_be_exchanged(self) -> None:
        fixture = PaperFixture()
        target = ApprovalTarget("generic-target", "v1", "generic-hash")
        fixture.engine.grant_approval(
            approval_id="candidate-type",
            approval_type=ApprovalType.CANDIDATE_SET,
            target=target,
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref="candidate-type-audit",
        )
        fixture.engine.grant_approval(
            approval_id="large-type",
            approval_type=ApprovalType.LARGE_TRADE,
            target=target,
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref="large-type-audit",
        )
        for approval_id, wrong_type in (
            ("candidate-type", ApprovalType.NEW_ROOT_PURPOSE),
            ("large-type", ApprovalType.CANDIDATE_SET),
        ):
            with self.subTest(approval_id=approval_id), self.assertRaises(PaperBlockedError) as blocked:
                fixture.engine.bind_approval(
                    approval_id=approval_id,
                    expected_type=wrong_type,
                    expected_target=target,
                    consumer_ref="consumer",
                    now=NOW + timedelta(seconds=1),
                )
            self.assertEqual(blocked.exception.code, "APPROVAL_TYPE_MISMATCH")

        root_fixture, root_target = self._new_root_fixture()
        with self.assertRaises(PaperBlockedError) as root_wrong_type:
            root_fixture.engine.bind_approval(
                approval_id="new-root-approval",
                expected_type=ApprovalType.LARGE_TRADE,
                expected_target=ApprovalTarget(
                    root_target.trade_purpose_id,
                    root_target.purpose_version,
                    root_target.purpose_version_hash,
                ),
                consumer_ref="consumer",
                now=NOW + timedelta(seconds=1),
            )
        self.assertEqual(root_wrong_type.exception.code, "APPROVAL_TYPE_MISMATCH")

    def test_new_root_concurrent_consumption_has_exactly_one_winner(self) -> None:
        fixture, target = self._new_root_fixture()
        barrier = threading.Barrier(2)

        def consume(index: int) -> str:
            barrier.wait()
            try:
                fixture.engine.consume_new_root_approval(
                    approval_id="new-root-approval",
                    expected_target=target,
                    purpose_change_result=PurposeChangeResult.DIFFERENT,
                    strategy_id=PaperStrategyKind.VALUE,
                    strategy_version_ref="strategy-v1",
                    budget_version_ref="budget-v1",
                    policy_version_ref="policy-v1",
                    intended_effect="새로운 가상 목적",
                    created_at=NOW + timedelta(seconds=1),
                    audit_id=f"consume-audit-{index}",
                )
                return "OK"
            except PaperBlockedError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, (1, 2)))
        self.assertEqual(results.count("OK"), 1)
        self.assertEqual(len(fixture.engine.snapshot.purposes), 2)
        self.assertEqual(fixture.engine.snapshot.approvals["new-root-approval"].state, ApprovalState.CONSUMED)

    def test_new_root_failure_injection_rolls_back_approval_purpose_and_audit(self) -> None:
        fixture, target = self._new_root_fixture()
        before = fixture.engine.snapshot

        def fail() -> None:
            raise RuntimeError("synthetic rollback")

        with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
            fixture.engine.consume_new_root_approval(
                approval_id="new-root-approval",
                expected_target=target,
                purpose_change_result=PurposeChangeResult.DIFFERENT,
                strategy_id=PaperStrategyKind.VALUE,
                strategy_version_ref="strategy-v1",
                budget_version_ref="budget-v1",
                policy_version_ref="policy-v1",
                intended_effect="새로운 가상 목적",
                created_at=NOW + timedelta(seconds=1),
                audit_id="consume-audit",
                fault_injector=fail,
            )
        after = fixture.engine.snapshot
        self.assertEqual(after.approvals["new-root-approval"].state, ApprovalState.APPROVED)
        self.assertNotIn("purpose-new", after.purposes)
        self.assertEqual(len(after.audits), len(before.audits))

    def test_new_root_expiry_split_and_content_hash_boundaries(self) -> None:
        valid_fixture, valid_target = self._new_root_fixture()
        purpose = valid_fixture.engine.consume_new_root_approval(
            approval_id="new-root-approval",
            expected_target=valid_target,
            purpose_change_result=PurposeChangeResult.DIFFERENT,
            strategy_id=PaperStrategyKind.VALUE,
            strategy_version_ref="strategy-v1",
            budget_version_ref="budget-v1",
            policy_version_ref="policy-v1",
            intended_effect="새로운 가상 목적",
            created_at=NOW + APPROVAL_LIFETIME - timedelta(microseconds=1),
            audit_id="valid-boundary-audit",
        )
        self.assertEqual(purpose.stable_root_trade_purpose_id, "root-2")

        expired_fixture, expired_target = self._new_root_fixture()
        with self.assertRaises(PaperBlockedError) as expired:
            expired_fixture.engine.consume_new_root_approval(
                approval_id="new-root-approval",
                expected_target=expired_target,
                purpose_change_result=PurposeChangeResult.DIFFERENT,
                strategy_id=PaperStrategyKind.VALUE,
                strategy_version_ref="strategy-v1",
                budget_version_ref="budget-v1",
                policy_version_ref="policy-v1",
                intended_effect="새로운 가상 목적",
                created_at=NOW + APPROVAL_LIFETIME,
                audit_id="expired-boundary-audit",
            )
        self.assertEqual(expired.exception.code, "APPROVAL_EXPIRED")

        policy_fixture, policy_target = self._new_root_fixture(
            target_policy_version="different-policy",
        )
        with self.assertRaises(PaperBlockedError) as policy_mismatch:
            policy_fixture.engine.consume_new_root_approval(
                approval_id="new-root-approval",
                expected_target=policy_target,
                purpose_change_result=PurposeChangeResult.DIFFERENT,
                strategy_id=PaperStrategyKind.VALUE,
                strategy_version_ref="strategy-v1",
                budget_version_ref="budget-v1",
                policy_version_ref="policy-v1",
                intended_effect="새로운 가상 목적",
                created_at=NOW + timedelta(seconds=1),
                audit_id="policy-mismatch-audit",
            )
        self.assertEqual(
            policy_mismatch.exception.code,
            "NEW_ROOT_POLICY_VERSION_MISMATCH",
        )

        for split_result in (SplitCheckResult.FAIL, SplitCheckResult.UNKNOWN):
            fixture, target = self._new_root_fixture()
            unsafe_target = replace(target, split_check_result=split_result)
            with self.subTest(split_result=split_result), self.assertRaises(PaperBlockedError) as blocked:
                fixture.engine.consume_new_root_approval(
                    approval_id="new-root-approval",
                    expected_target=unsafe_target,
                    purpose_change_result=PurposeChangeResult.DIFFERENT,
                    strategy_id=PaperStrategyKind.VALUE,
                    strategy_version_ref="strategy-v1",
                    budget_version_ref="budget-v1",
                    policy_version_ref="policy-v1",
                    intended_effect="새로운 가상 목적",
                    created_at=NOW + timedelta(seconds=1),
                    audit_id="split-block-audit",
                )
            self.assertEqual(blocked.exception.code, "ROOT_OVERLAP_SPLIT_CHECK_NOT_PASS")

    def test_same_or_unproven_purpose_and_failed_split_check_do_not_create_root(self) -> None:
        for result, expected in (
            (PurposeChangeResult.SAME, "SAME_PURPOSE_MUST_KEEP_STABLE_ROOT"),
            (PurposeChangeResult.UNPROVEN, "PURPOSE_CHANGE_UNPROVEN"),
        ):
            fixture, target = self._new_root_fixture()
            with self.subTest(result=result), self.assertRaises(PaperBlockedError) as blocked:
                fixture.engine.consume_new_root_approval(
                    approval_id="new-root-approval",
                    expected_target=target,
                    purpose_change_result=result,
                    strategy_id=PaperStrategyKind.VALUE,
                    strategy_version_ref="strategy-v1",
                    budget_version_ref="budget-v1",
                    policy_version_ref="policy-v1",
                    intended_effect="새로운 가상 목적",
                    created_at=NOW + timedelta(seconds=1),
                    audit_id="consume-audit",
                )
            self.assertEqual(blocked.exception.code, expected)
            self.assertNotIn("purpose-new", fixture.engine.snapshot.purposes)


class PaperCandidateRunDateTests(unittest.TestCase):
    def test_proposal_expiry_releases_only_when_no_attempt_is_open(self) -> None:
        no_attempt_fixture = PaperFixture()
        no_attempt, no_attempt_approval = no_attempt_fixture.add_proposal(
            "expiry-no-attempt",
            gross=10_000,
            expires_at=NOW + timedelta(minutes=30),
        )
        no_attempt_reservation = no_attempt_fixture.reserve(
            no_attempt,
            no_attempt_approval,
        )
        expired = no_attempt_fixture.engine.expire_proposal(
            no_attempt.proposal_id,
            expired_at=NOW + timedelta(minutes=30),
            evidence_ref="clock-boundary",
            audit_id="expiry-no-attempt-audit",
        )
        state = no_attempt_fixture.engine.snapshot
        self.assertEqual(expired.state, ProposalState.EXPIRED)
        self.assertEqual(
            state.reservations[no_attempt_reservation.reservation_id].state,
            ReservationState.RELEASED,
        )
        self.assertEqual(
            state.daily_limits[SESSION].active_reserved_gross_krw,
            Decimal("0"),
        )

        unknown_fixture = PaperFixture()
        unknown, unknown_approval = unknown_fixture.add_proposal(
            "expiry-open-attempt",
            gross=10_000,
            expires_at=NOW + timedelta(minutes=30),
        )
        unknown_reservation = unknown_fixture.reserve(unknown, unknown_approval)
        run_id, _event_id = unknown_fixture.begin_attempt(unknown)
        needs_reconciliation = unknown_fixture.engine.expire_proposal(
            unknown.proposal_id,
            expired_at=NOW + timedelta(minutes=30),
            evidence_ref="clock-boundary",
            audit_id="expiry-open-attempt-audit",
        )
        unknown_state = unknown_fixture.engine.snapshot
        self.assertEqual(
            needs_reconciliation.state,
            ProposalState.NEEDS_RECONCILIATION,
        )
        self.assertEqual(
            unknown_state.reservations[unknown_reservation.reservation_id].state,
            ReservationState.NEEDS_RECONCILIATION,
        )
        self.assertEqual(
            unknown_state.runs[run_id].state,
            RunState.RECONCILIATION_REQUIRED,
        )

    def test_proposal_expiry_cannot_precede_unknown_result_state(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal(
            "expiry-after-late-unknown",
            gross=10_000,
            expires_at=NOW + timedelta(days=2),
        )
        reservation = fixture.reserve(proposal, candidate)
        run_id, event_id = fixture.begin_attempt(proposal)
        unknown_at = NOW + timedelta(days=3)
        fixture.engine.mark_reservation_unknown(
            reservation.reservation_id,
            run_id=run_id,
            attempt_event_id=event_id,
            at=unknown_at,
            reason_code="RESULT_NOT_CONFIRMED",
        )
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as backdated:
            fixture.engine.expire_proposal(
                proposal.proposal_id,
                expired_at=unknown_at - timedelta(microseconds=1),
                evidence_ref="backdated-expiry",
                audit_id="backdated-proposal-expiry",
            )
        self.assertEqual(
            backdated.exception.code,
            "PROPOSAL_STATE_TIME_INVALID",
        )
        self.assertEqual(fixture.engine.snapshot, before)
        expired = fixture.engine.expire_proposal(
            proposal.proposal_id,
            expired_at=unknown_at,
            evidence_ref="causal-expiry",
            audit_id="causal-proposal-expiry",
        )
        self.assertEqual(expired.state, ProposalState.NEEDS_RECONCILIATION)

    def test_candidate_set_rejection_is_audited_and_never_advances_rank(self) -> None:
        fixture = PaperFixture()
        candidates = (Candidate(1, "FIRST"), Candidate(2, "SECOND"))
        set_hash = candidate_set_content_hash(
            set_id="candidate-reject-set",
            version="v1",
            trade_purpose_id="purpose-v1",
            stable_root_trade_purpose_id="root-1",
            candidates=candidates,
            evidence_cutoff=NOW,
            created_at=NOW,
        )
        fixture.engine.create_candidate_set(
            CandidateSet(
                "candidate-reject-set",
                "v1",
                set_hash,
                "purpose-v1",
                "root-1",
                candidates,
                NOW,
                NOW,
            )
        )
        fixture.engine.transition_candidate_set(
            "candidate-reject-set",
            CandidateSetState.VALIDATED,
        )
        fixture.engine.transition_candidate_set(
            "candidate-reject-set",
            CandidateSetState.WAITING_SET_APPROVAL,
        )
        with self.assertRaises(PaperBlockedError) as unaudited:
            fixture.engine.transition_candidate_set(
                "candidate-reject-set",
                CandidateSetState.REJECTED,
            )
        self.assertEqual(
            unaudited.exception.code,
            "CANDIDATE_REJECTION_REQUIRES_AUDIT_METHOD",
        )
        rejected = fixture.engine.reject_candidate_set(
            "candidate-reject-set",
            rejected_at=NOW,
            rejected_by_user_ref="local-user",
            evidence_ref="explicit-user-decision",
            audit_id="candidate-reject-audit",
        )
        self.assertEqual(rejected.state, CandidateSetState.REJECTED)
        self.assertFalse(fixture.engine.snapshot.proposals)
        audit = fixture.engine.snapshot.audits[-1]
        self.assertEqual(audit.related_ref, "candidate-reject-set")
        self.assertEqual(audit.actor_ref, "local-user")
        self.assertIsNone(
            fixture.engine.next_candidate(
                set_id="candidate-reject-set",
                current_rank=1,
                reason=CandidateFailureReason.APPROVAL_REJECTED,
                evidence_verified=True,
                approved_permanent_reasons=frozenset(
                    {CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE}
                ),
            )
        )

    def test_large_trade_rejection_cancels_first_and_requires_new_set(self) -> None:
        fixture = PaperFixture()
        proposal, _candidate_approval = fixture.add_proposal(
            "large-rejected",
            gross=300_000,
        )
        updated_set = fixture.engine.reject_large_trade_proposal(
            proposal.proposal_id,
            rejected_at=NOW + timedelta(minutes=1),
            rejected_by_user_ref="local-user",
            audit_id="large-rejected-audit",
        )
        state = fixture.engine.snapshot
        self.assertEqual(updated_set.state, CandidateSetState.REGENERATION_REQUIRED)
        self.assertEqual(
            state.proposals[proposal.proposal_id].state,
            ProposalState.CANCELLED,
        )
        audit = state.audits[-1]
        self.assertEqual(audit.related_ref, proposal.proposal_id)
        self.assertEqual(audit.actor_ref, "local-user")
        self.assertEqual(audit.reason_code, "LARGE_TRADE_REJECTED")
        self.assertIsNone(
            fixture.engine.next_candidate(
                set_id=proposal.candidate_set_id,
                current_rank=1,
                reason=CandidateFailureReason.APPROVAL_REJECTED,
                evidence_verified=True,
                approved_permanent_reasons=frozenset(
                    {CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE}
                ),
            )
        )
        replacement_candidates = (Candidate(1, "REPLACEMENT"),)
        replacement_hash = candidate_set_content_hash(
            set_id="large-rejected-v2",
            version="v2",
            trade_purpose_id="purpose-v1",
            stable_root_trade_purpose_id="root-1",
            candidates=replacement_candidates,
            evidence_cutoff=NOW + timedelta(minutes=1),
            created_at=NOW + timedelta(minutes=1),
            regeneration_of=proposal.candidate_set_id,
        )
        fixture.engine.create_candidate_set(
            CandidateSet(
                "large-rejected-v2",
                "v2",
                replacement_hash,
                "purpose-v1",
                "root-1",
                replacement_candidates,
                NOW + timedelta(minutes=1),
                NOW + timedelta(minutes=1),
                regeneration_of=proposal.candidate_set_id,
            )
        )
        self.assertEqual(
            fixture.engine.snapshot.candidate_sets[proposal.candidate_set_id].state,
            CandidateSetState.SUPERSEDED,
        )

    def test_large_trade_rejection_is_forbidden_after_approval_or_reservation(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal(
            "large-rejection-after-reservation",
            gross=300_000,
        )
        large = fixture.grant_large(proposal.proposal_id)
        reservation = fixture.reserve(
            proposal,
            candidate,
            large_approval_id=large,
        )
        before = fixture.engine.snapshot
        for rejected_at in (
            reservation.created_at - timedelta(microseconds=1),
            reservation.created_at,
        ):
            with self.subTest(rejected_at=rejected_at), self.assertRaises(
                PaperBlockedError
            ) as rejected:
                fixture.engine.reject_large_trade_proposal(
                    proposal.proposal_id,
                    rejected_at=rejected_at,
                    rejected_by_user_ref="local-user",
                    audit_id=f"late-rejection-{utc_epoch_microseconds(rejected_at)}",
                )
            self.assertEqual(
                rejected.exception.code,
                "APPROVAL_REJECTION_AFTER_RESERVATION_FORBIDDEN",
            )
            self.assertEqual(fixture.engine.snapshot, before)

    def _ranked_fixture(self, suffix: str):
        fixture = PaperFixture()
        set_id = f"active-ranked-{suffix}"
        candidates = (Candidate(1, "FIRST"), Candidate(2, "SECOND"))
        set_hash = candidate_set_content_hash(
            set_id=set_id,
            version="v1",
            trade_purpose_id="purpose-v1",
            stable_root_trade_purpose_id="root-1",
            candidates=candidates,
            evidence_cutoff=NOW,
            created_at=NOW,
        )
        fixture.engine.create_candidate_set(
            CandidateSet(
                set_id,
                "v1",
                set_hash,
                "purpose-v1",
                "root-1",
                candidates,
                NOW,
                NOW,
            )
        )
        fixture.engine.transition_candidate_set(set_id, CandidateSetState.VALIDATED)
        fixture.engine.transition_candidate_set(
            set_id,
            CandidateSetState.WAITING_SET_APPROVAL,
        )
        approval_id = f"active-ranked-approval-{suffix}"
        fixture.engine.grant_approval(
            approval_id=approval_id,
            approval_type=ApprovalType.CANDIDATE_SET,
            target=ApprovalTarget(set_id, "v1", set_hash),
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref=f"active-ranked-audit-{suffix}",
        )
        fixture.engine.transition_candidate_set(
            set_id,
            CandidateSetState.SET_APPROVED,
            approval_id=approval_id,
            transitioned_at=NOW,
        )

        def proposal(rank: int, instrument: str) -> TradeProposal:
            proposal_id = f"active-ranked-{suffix}-{rank}"
            amounts = krw_amounts(10_000)
            condition = PriceCondition(
                PriceConditionOperator.AT_OR_BELOW,
                Decimal("10000"),
                Currency.KRW,
                f"active-ranked-condition-{suffix}-{rank}",
                NOW,
            )
            proposal_hash = trade_proposal_content_hash(
                proposal_id=proposal_id,
                version="v1",
                candidate_set_id=set_id,
                candidate_rank=rank,
                trade_purpose_id="purpose-v1",
                purpose_version="v1",
                stable_root_trade_purpose_id="root-1",
                strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
                trade_intent_id=f"active-ranked-intent-{suffix}",
                instrument_id=instrument,
                side=Side.BUY,
                quantity=1,
                amounts=amounts,
                us_session_date=SESSION,
                kst_intent_date=KST_DAY,
                expires_at=NOW + timedelta(days=1),
                price_condition=condition,
            )
            return TradeProposal(
                proposal_id,
                "v1",
                proposal_hash,
                set_id,
                rank,
                "purpose-v1",
                "v1",
                "root-1",
                PaperStrategyKind.LONG_TERM_INDEX,
                f"active-ranked-intent-{suffix}",
                instrument,
                Side.BUY,
                1,
                amounts,
                SESSION,
                KST_DAY,
                NOW + timedelta(days=1),
                price_condition=condition,
            )

        return fixture, approval_id, proposal(1, "FIRST"), proposal(2, "SECOND")

    def test_active_or_filled_first_rank_cannot_clear_for_second_rank(self) -> None:
        approved = frozenset({CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE})
        for case in ("active", "filled"):
            with self.subTest(case=case):
                fixture, approval_id, first, second = self._ranked_fixture(case)
                fixture.engine.create_proposal(first)
                reservation = fixture.reserve(first, approval_id)
                if case == "filled":
                    run_id, event_id = fixture.begin_attempt(first)
                    fixture.engine.record_fill(
                        observed_fill(
                            f"ranked-fill-{case}",
                            first,
                            reservation.reservation_id,
                            1,
                            krw_amounts(10_000),
                            NOW + timedelta(minutes=2),
                        ),
                        execution_policy=confirmed_execution_policy(),
                        risk_context=fixture.risk_context(first),
                        run_id=run_id,
                        attempt_event_id=event_id,
                        lot_id=f"ranked-lot-{case}",
                    )
                with self.assertRaises(PaperBlockedError) as blocked:
                    fixture.engine.next_candidate(
                        set_id=first.candidate_set_id,
                        current_rank=1,
                        reason=CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE,
                        evidence_verified=True,
                        approved_permanent_reasons=approved,
                        evidence_ref="verified-permanent-policy-evidence",
                        verified_at=NOW + timedelta(minutes=3),
                    )
                self.assertEqual(
                    blocked.exception.code,
                    "PRIOR_CANDIDATE_ALREADY_ACTIVE_OR_EXECUTED",
                )
                with self.assertRaises(PaperBlockedError):
                    fixture.engine.create_proposal(second)

    def test_cleared_rank_cannot_be_recreated_after_next_rank_is_opened(self) -> None:
        fixture, approval_id, first, second = self._ranked_fixture("late-first")
        next_candidate = fixture.engine.next_candidate(
            set_id=first.candidate_set_id,
            current_rank=1,
            reason=CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE,
            evidence_verified=True,
            approved_permanent_reasons=frozenset(
                {CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE}
            ),
            evidence_ref="verified-permanent-policy-evidence",
            verified_at=NOW,
        )
        self.assertEqual(next_candidate.instrument_id, second.instrument_id)
        fixture.engine.create_proposal(second)
        with self.assertRaises(PaperBlockedError) as cleared:
            fixture.engine.create_proposal(first)
        self.assertEqual(cleared.exception.code, "CANDIDATE_RANK_ALREADY_CLEARED")
        fixture.reserve(second, approval_id)
        self.assertEqual(
            fixture.engine.snapshot.proposals[second.proposal_id].state,
            ProposalState.READY,
        )

    def test_only_verified_approved_permanent_reason_allows_next_rank(self) -> None:
        fixture = PaperFixture()
        ranked_candidates = (Candidate(1, "FIRST"), Candidate(2, "SECOND"))
        ranked_hash = candidate_set_content_hash(
            set_id="ranked",
            version="v1",
            trade_purpose_id="purpose-v1",
            stable_root_trade_purpose_id="root-1",
            candidates=ranked_candidates,
            evidence_cutoff=NOW,
            created_at=NOW,
        )
        candidates = CandidateSet(
            "ranked",
            "v1",
            ranked_hash,
            "purpose-v1",
            "root-1",
            ranked_candidates,
            NOW,
            NOW,
        )
        fixture.engine.create_candidate_set(candidates)
        fixture.engine.transition_candidate_set("ranked", CandidateSetState.VALIDATED)
        fixture.engine.transition_candidate_set(
            "ranked", CandidateSetState.WAITING_SET_APPROVAL
        )
        fixture.engine.grant_approval(
            approval_id="ranked-approval",
            approval_type=ApprovalType.CANDIDATE_SET,
            target=ApprovalTarget("ranked", "v1", ranked_hash),
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref="ranked-approval-audit",
        )
        fixture.engine.transition_candidate_set(
            "ranked",
            CandidateSetState.SET_APPROVED,
            approval_id="ranked-approval",
            transitioned_at=NOW,
        )
        second_amounts = krw_amounts(10_000)
        second_condition = PriceCondition(
            PriceConditionOperator.AT_OR_BELOW,
            Decimal("10000"),
            Currency.KRW,
            "ranked-second-condition",
            NOW,
        )
        second_hash = trade_proposal_content_hash(
            proposal_id="ranked-second",
            version="v1",
            candidate_set_id="ranked",
            candidate_rank=2,
            trade_purpose_id="purpose-v1",
            purpose_version="v1",
            stable_root_trade_purpose_id="root-1",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            trade_intent_id="ranked-intent",
            instrument_id="SECOND",
            side=Side.BUY,
            quantity=1,
            amounts=second_amounts,
            us_session_date=SESSION,
            kst_intent_date=KST_DAY,
            expires_at=NOW + timedelta(days=1),
            price_condition=second_condition,
        )
        second_proposal = TradeProposal(
            "ranked-second",
            "v1",
            second_hash,
            "ranked",
            2,
            "purpose-v1",
            "v1",
            "root-1",
            PaperStrategyKind.LONG_TERM_INDEX,
            "ranked-intent",
            "SECOND",
            Side.BUY,
            1,
            second_amounts,
            SESSION,
            KST_DAY,
            NOW + timedelta(days=1),
            price_condition=second_condition,
        )
        with self.assertRaises(PaperBlockedError) as direct_second:
            fixture.engine.create_proposal(second_proposal)
        self.assertEqual(
            direct_second.exception.code,
            "PRIOR_CANDIDATE_PERMANENT_FAILURE_REQUIRED",
        )
        disallowed = {
            CandidateFailureReason.APPROVAL_WAITING,
            CandidateFailureReason.APPROVAL_REJECTED,
            CandidateFailureReason.PRICE_CONDITION_NOT_MET,
            CandidateFailureReason.MISSED_WINDOW,
            CandidateFailureReason.DATA_TEMPORARILY_UNAVAILABLE,
            CandidateFailureReason.RESULT_UNKNOWN,
            CandidateFailureReason.TEMPORARY_HALT,
        }
        approved = frozenset({CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE})
        for reason in disallowed:
            with self.subTest(reason=reason):
                self.assertIsNone(
                    fixture.engine.next_candidate(
                        set_id="ranked",
                        current_rank=1,
                        reason=reason,
                        evidence_verified=True,
                        approved_permanent_reasons=approved,
                    )
                )
        self.assertIsNone(
            fixture.engine.next_candidate(
                set_id="ranked",
                current_rank=1,
                reason=CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE,
                evidence_verified=False,
                approved_permanent_reasons=approved,
            )
        )
        next_candidate = fixture.engine.next_candidate(
            set_id="ranked",
            current_rank=1,
            reason=CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE,
            evidence_verified=True,
            approved_permanent_reasons=approved,
            evidence_ref="verified-permanent-policy-evidence",
            verified_at=NOW,
        )
        self.assertEqual(next_candidate.instrument_id, "SECOND")
        fixture.engine.create_proposal(second_proposal)
        self.assertIn(second_proposal.proposal_id, fixture.engine.snapshot.proposals)

    def _run_fixture(self, run_id: str = "run"):
        fixture = PaperFixture()
        first, first_approval = fixture.add_proposal("run-p1", instrument="I1", gross=10_000)
        second, second_approval = fixture.add_proposal("run-p2", instrument="I2", gross=10_000)
        fixture.reserve(first, first_approval)
        fixture.reserve(second, second_approval)
        included = ((first.proposal_id, first.version), (second.proposal_id, second.version))
        run = ExecutionRun(
            run_id,
            "paper-portfolio",
            SESSION,
            included,
            canonical_hash(included),
            NOW,
            NOW + timedelta(hours=1),
            RunState.READY,
        )
        fixture.engine.create_execution_run(run, gates=GateDecision.passed())
        return fixture, run

    def test_zero_attempt_run_is_missed_window(self) -> None:
        fixture, run = self._run_fixture()
        closed = fixture.engine.close_execution_window(run.execution_run_id, ended_at=NOW + timedelta(hours=1))
        self.assertEqual(closed.state, RunState.MISSED_WINDOW)
        self.assertEqual(closed.attempt_count, 0)
        state = fixture.engine.snapshot
        self.assertTrue(
            all(
                reservation.state is ReservationState.RELEASED
                for reservation in state.reservations.values()
            )
        )
        self.assertEqual(state.daily_limits[SESSION].active_reserved_gross_krw, Decimal("0"))
        self.assertTrue(
            all(
                proposal.state is ProposalState.MISSED_WINDOW
                for proposal in state.proposals.values()
            )
        )
        with self.assertRaises(PaperBlockedError):
            fixture.engine.reactivate_execution_run(run.execution_run_id, explicitly_requested=True, gates=GateDecision.passed())

    def test_one_confirmed_attempt_is_completed_partial_not_run_missed(self) -> None:
        fixture, run = self._run_fixture()
        fixture.engine.record_execution_attempt(
            run_id=run.execution_run_id,
            event_id="attempt-1",
            proposal_id="run-p1",
            attempted_at=NOW + timedelta(minutes=1),
        )
        # Retry of the same durable event is idempotent.
        retry = fixture.engine.record_execution_attempt(
            run_id=run.execution_run_id,
            event_id="attempt-1",
            proposal_id="run-p1",
            attempted_at=NOW + timedelta(minutes=1),
        )
        self.assertEqual(retry.attempt_count, 1)
        with self.assertRaises(PaperBlockedError) as conflict:
            fixture.engine.record_execution_attempt(
                run_id=run.execution_run_id,
                event_id="attempt-1",
                proposal_id="run-p1",
                attempted_at=NOW + timedelta(minutes=2),
            )
        self.assertEqual(conflict.exception.code, "ATTEMPT_EVENT_ID_REUSE_FORBIDDEN")
        fixture.engine.confirm_attempt(
            run_id=run.execution_run_id,
            event_id="attempt-1",
            result=AttemptResult.NO_FILL,
            confirmed_at=NOW + timedelta(minutes=3),
        )
        closed = fixture.engine.close_execution_window(run.execution_run_id, ended_at=NOW + timedelta(hours=1))
        self.assertEqual(closed.state, RunState.COMPLETED)
        self.assertEqual(closed.completion_kind, CompletionKind.PARTIAL)
        self.assertEqual(fixture.engine.snapshot.proposals["run-p2"].state, ProposalState.MISSED_WINDOW)
        self.assertEqual(
            fixture.engine.snapshot.reservations["reservation-run-p2"].state,
            ReservationState.RELEASED,
        )

    def test_unknown_attempt_requires_explicit_reconciliation(self) -> None:
        fixture, run = self._run_fixture()
        fixture.engine.record_execution_attempt(
            run_id=run.execution_run_id,
            event_id="attempt-unknown",
            proposal_id="run-p1",
            attempted_at=NOW + timedelta(minutes=1),
        )
        fixture.engine.mark_reservation_unknown(
            "reservation-run-p1",
            run_id=run.execution_run_id,
            attempt_event_id="attempt-unknown",
            at=NOW + timedelta(minutes=2),
            reason_code="RESULT_NOT_CONFIRMED",
        )
        immediately_held = fixture.engine.snapshot.runs[run.execution_run_id]
        self.assertEqual(immediately_held.state, RunState.RECONCILIATION_REQUIRED)
        self.assertIsNone(immediately_held.ended_at)
        with self.assertRaises(PaperBlockedError) as later_attempt:
            fixture.engine.record_execution_attempt(
                run_id=run.execution_run_id,
                event_id="attempt-after-unknown",
                proposal_id="run-p2",
                attempted_at=NOW + timedelta(minutes=3),
            )
        self.assertEqual(later_attempt.exception.code, "EXECUTION_RUN_NOT_ATTEMPTABLE")
        closed = fixture.engine.close_execution_window(run.execution_run_id, ended_at=NOW + timedelta(hours=1))
        self.assertEqual(closed.state, RunState.RECONCILIATION_REQUIRED)
        closed_state = fixture.engine.snapshot
        self.assertEqual(
            closed_state.proposals["run-p2"].state,
            ProposalState.MISSED_WINDOW,
        )
        self.assertEqual(
            closed_state.reservations["reservation-run-p2"].state,
            ReservationState.RELEASED,
        )
        with self.assertRaises(PaperBlockedError):
            fixture.engine.reactivate_execution_run(run.execution_run_id, explicitly_requested=True, gates=GateDecision.passed())
        with self.assertRaises(PaperBlockedError):
            fixture.engine.reconcile_execution_run(
                run.execution_run_id,
                evidence_ref="premature",
                reconciled_at=NOW + timedelta(hours=1, minutes=1),
            )
        fixture.engine.reconcile_reservation_no_fill(
            "reservation-run-p1",
            run_id=run.execution_run_id,
            attempt_event_id="attempt-unknown",
            evidence_ref="explicit-no-fill",
            audit_id="reconcile-reservation-audit",
            reconciled_at=NOW + timedelta(hours=1, minutes=2),
        )
        reservation_audit = next(
            item
            for item in fixture.engine.snapshot.audits
            if item.audit_id == "reconcile-reservation-audit"
        )
        self.assertEqual(reservation_audit.related_ref, "reservation-run-p1")
        self.assertEqual(reservation_audit.evidence_ref, "explicit-no-fill")
        reconciled = fixture.engine.reconcile_execution_run(
            run.execution_run_id,
            evidence_ref="explicit-reconciliation",
            reconciled_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(reconciled.state, RunState.RECONCILED)
        run_audit = fixture.engine.snapshot.audits[-1]
        self.assertEqual(run_audit.event_type, "EXECUTION_RUN_RECONCILED")
        self.assertEqual(run_audit.related_ref, run.execution_run_id)
        self.assertEqual(run_audit.evidence_ref, "explicit-reconciliation")
        self.assertFalse(
            any(
                reservation.state
                in {
                    ReservationState.ACTIVE,
                    ReservationState.UNKNOWN,
                    ReservationState.NEEDS_RECONCILIATION,
                }
                for reservation in fixture.engine.snapshot.reservations.values()
            )
        )

    def test_reconciliation_before_window_end_releases_unattempted_reservation(self) -> None:
        fixture, run = self._run_fixture("early-reconciliation-run")
        fixture.engine.record_execution_attempt(
            run_id=run.execution_run_id,
            event_id="early-reconciliation-attempt",
            proposal_id="run-p1",
            attempted_at=NOW + timedelta(minutes=1),
        )
        fixture.engine.mark_reservation_unknown(
            "reservation-run-p1",
            run_id=run.execution_run_id,
            attempt_event_id="early-reconciliation-attempt",
            at=NOW + timedelta(minutes=2),
            reason_code="RESULT_NOT_CONFIRMED",
        )
        fixture.engine.reconcile_reservation_no_fill(
            "reservation-run-p1",
            run_id=run.execution_run_id,
            attempt_event_id="early-reconciliation-attempt",
            reconciled_at=NOW + timedelta(minutes=3),
            evidence_ref="verified-no-fill",
            audit_id="early-reservation-reconcile-audit",
        )
        reconciled = fixture.engine.reconcile_execution_run(
            run.execution_run_id,
            evidence_ref="explicit-early-run-reconciliation",
            reconciled_at=NOW + timedelta(minutes=4),
        )
        self.assertEqual(reconciled.state, RunState.RECONCILED)
        self.assertEqual(reconciled.completion_kind, CompletionKind.PARTIAL)
        state = fixture.engine.snapshot
        self.assertEqual(
            state.reservations["reservation-run-p2"].state,
            ReservationState.RELEASED,
        )
        self.assertEqual(
            state.proposals["run-p2"].state,
            ProposalState.CANCELLED,
        )
        self.assertEqual(
            state.daily_limits[SESSION].active_reserved_gross_krw,
            Decimal("0"),
        )

    def test_user_stop_persists_and_needs_explicit_reactivation_and_gate(self) -> None:
        fixture, run = self._run_fixture()
        stopped = fixture.engine.stop_execution_run(
            run.execution_run_id,
            stopped_at=NOW + timedelta(minutes=2),
            reason_code="USER_REQUESTED",
            evidence_ref="local-user-action",
            audit_id="run-user-stop-audit",
        )
        self.assertEqual(stopped.state, RunState.USER_STOPPED)
        self.assertEqual(stopped.state_reason_code, "USER_REQUESTED")
        self.assertEqual(stopped.state_evidence_ref, "local-user-action")
        self.assertEqual(stopped.state_changed_at, NOW + timedelta(minutes=2))
        self.assertTrue(
            all(
                proposal.state is ProposalState.USER_STOPPED
                for proposal in fixture.engine.snapshot.proposals.values()
            )
        )
        restarted = PaperEngine(fixture.repository)
        self.assertEqual(restarted.snapshot.runs[run.execution_run_id].state, RunState.USER_STOPPED)
        with self.assertRaises(PaperBlockedError):
            restarted.reactivate_execution_run(run.execution_run_id, explicitly_requested=False, gates=GateDecision.passed())
        with self.assertRaises(PaperBlockedError):
            restarted.reactivate_execution_run(run.execution_run_id, explicitly_requested=True, gates=GateDecision.blocked("GATE"))
        resumed = restarted.reactivate_execution_run(
            run.execution_run_id,
            explicitly_requested=True,
            gates=GateDecision.passed(),
            requested_at=NOW + timedelta(minutes=3),
            evidence_ref="explicit-user-resume",
            audit_id="run-user-resume-audit",
        )
        self.assertEqual(resumed.state, RunState.READY)
        self.assertTrue(
            all(
                proposal.state is ProposalState.READY
                for proposal in restarted.snapshot.proposals.values()
            )
        )

    def test_user_stop_with_unresolved_attempt_requires_reconciliation_without_release(self) -> None:
        fixture, run = self._run_fixture("stop-open-attempt-run")
        fixture.engine.record_execution_attempt(
            run_id=run.execution_run_id,
            event_id="stop-open-attempt",
            proposal_id="run-p1",
            attempted_at=NOW + timedelta(minutes=1),
        )
        stopped = fixture.engine.stop_execution_run(
            run.execution_run_id,
            stopped_at=NOW + timedelta(minutes=2),
            reason_code="USER_REQUESTED",
            evidence_ref="explicit-user-stop",
            audit_id="stop-open-attempt-audit",
        )
        self.assertEqual(stopped.state, RunState.RECONCILIATION_REQUIRED)
        self.assertEqual(stopped.terminal_reason, "USER_STOPPED_RESULT_UNKNOWN")
        state = fixture.engine.snapshot
        self.assertEqual(
            state.proposals["run-p1"].state,
            ProposalState.PAPER_EXECUTING,
        )
        self.assertEqual(
            state.reservations["reservation-run-p1"].state,
            ReservationState.ACTIVE,
        )
        self.assertEqual(
            state.reservations["reservation-run-p2"].state,
            ReservationState.ACTIVE,
        )

        fixture.engine.mark_reservation_unknown(
            "reservation-run-p1",
            run_id=run.execution_run_id,
            attempt_event_id="stop-open-attempt",
            at=NOW + timedelta(minutes=3),
            reason_code="RESULT_NOT_CONFIRMED_AFTER_STOP",
        )
        fixture.engine.reconcile_reservation_no_fill(
            "reservation-run-p1",
            run_id=run.execution_run_id,
            attempt_event_id="stop-open-attempt",
            reconciled_at=NOW + timedelta(minutes=4),
            evidence_ref="verified-no-fill-after-stop",
            audit_id="stop-open-reservation-reconcile-audit",
        )
        reconciled = fixture.engine.reconcile_execution_run(
            run.execution_run_id,
            evidence_ref="explicit-stop-run-reconciliation",
            reconciled_at=NOW + timedelta(minutes=5),
        )
        self.assertEqual(reconciled.state, RunState.RECONCILED)
        final_state = fixture.engine.snapshot
        self.assertEqual(
            final_state.reservations["reservation-run-p1"].state,
            ReservationState.RELEASED,
        )
        self.assertEqual(
            final_state.reservations["reservation-run-p2"].state,
            ReservationState.RELEASED,
        )
        self.assertEqual(
            final_state.daily_limits[SESSION].active_reserved_gross_krw,
            Decimal("0"),
        )

    def test_temporary_hold_preserves_reason_evidence_reservations_and_restart(self) -> None:
        fixture, run = self._run_fixture("held-run")
        held = fixture.engine.hold_execution_run(
            run.execution_run_id,
            held_at=NOW + timedelta(minutes=2),
            reason_code="PRICE_CONDITION_NOT_MET",
            evidence_ref="verified-price-check",
            audit_id="held-run-audit",
        )
        self.assertEqual(held.state, RunState.TEMPORARILY_HELD)
        self.assertEqual(held.state_reason_code, "PRICE_CONDITION_NOT_MET")
        self.assertEqual(held.state_evidence_ref, "verified-price-check")
        state = fixture.engine.snapshot
        self.assertTrue(
            all(
                proposal.state is ProposalState.TEMPORARILY_HELD
                for proposal in state.proposals.values()
            )
        )
        self.assertTrue(
            all(
                reservation.state is ReservationState.ACTIVE
                for reservation in state.reservations.values()
            )
        )
        restarted = PaperEngine(fixture.repository)
        self.assertEqual(
            restarted.snapshot.runs[run.execution_run_id].state,
            RunState.TEMPORARILY_HELD,
        )
        with self.assertRaises(PaperBlockedError):
            restarted.reactivate_execution_run(
                run.execution_run_id,
                explicitly_requested=True,
                gates=GateDecision.blocked("PRICE_STILL_OUTSIDE_CONDITION"),
            )
        rechecked_at = NOW + timedelta(minutes=2, microseconds=1)
        first_held = restarted.snapshot.proposals["run-p1"]
        assert first_held.price_condition is not None
        first_recheck = restarted.evaluate_proposal_price_condition(
            proposal_id=first_held.proposal_id,
            observed_price=VerifiedPrice(
                first_held.instrument_id,
                first_held.price_condition.threshold,
                first_held.amounts.cash_currency,
                rechecked_at,
                "price-recheck-after-hold-p1",
                True,
            ),
            observation_cutoff_at=rechecked_at,
            evaluated_at=rechecked_at,
        )
        self.assertEqual(first_recheck.decision.status, GateStatus.PASS)
        self.assertEqual(
            restarted.snapshot.proposals[first_held.proposal_id].state,
            ProposalState.READY,
        )
        self.assertEqual(
            restarted.snapshot.runs[run.execution_run_id].state,
            RunState.TEMPORARILY_HELD,
        )
        partially_rechecked = restarted.snapshot
        with self.assertRaises(PaperBlockedError) as incomplete_recheck:
            restarted.reactivate_execution_run(
                run.execution_run_id,
                explicitly_requested=True,
                gates=GateDecision.passed(),
                requested_at=NOW + timedelta(minutes=3),
                evidence_ref="only-one-proposal-rechecked",
                audit_id="incomplete-recheck-audit",
            )
        self.assertEqual(
            incomplete_recheck.exception.code,
            "EXECUTION_RUN_GATES_NOT_PASS",
        )
        self.assertEqual(restarted.snapshot, partially_rechecked)
        second_held = restarted.snapshot.proposals["run-p2"]
        assert second_held.price_condition is not None
        second_recheck = restarted.evaluate_proposal_price_condition(
            proposal_id=second_held.proposal_id,
            observed_price=VerifiedPrice(
                second_held.instrument_id,
                second_held.price_condition.threshold,
                second_held.amounts.cash_currency,
                rechecked_at,
                "price-recheck-after-hold-p2",
                True,
            ),
            observation_cutoff_at=rechecked_at,
            evaluated_at=rechecked_at,
        )
        self.assertEqual(second_recheck.decision.status, GateStatus.PASS)
        resumed = restarted.reactivate_execution_run(
            run.execution_run_id,
            explicitly_requested=True,
            gates=GateDecision.passed(),
            requested_at=NOW + timedelta(minutes=3),
            evidence_ref="condition-rechecked",
            audit_id="held-run-resume-audit",
        )
        self.assertEqual(resumed.state, RunState.READY)
        self.assertTrue(
            all(
                proposal.state is ProposalState.READY
                for proposal in restarted.snapshot.proposals.values()
            )
        )

    def test_price_recheck_cannot_precede_later_run_hold(self) -> None:
        fixture, run = self._run_fixture("price-causal-run")
        held_at = NOW + timedelta(minutes=10)
        fixture.engine.hold_execution_run(
            run.execution_run_id,
            held_at=held_at,
            reason_code="PRICE_CONDITION_NOT_MET",
            evidence_ref="later-price-evidence",
            audit_id="price-causal-hold-audit",
        )
        proposal = fixture.engine.snapshot.proposals["run-p1"]
        assert proposal.price_condition is not None
        backdated_at = NOW + timedelta(minutes=5)
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as rollback:
            fixture.engine.evaluate_proposal_price_condition(
                proposal_id=proposal.proposal_id,
                observed_price=VerifiedPrice(
                    proposal.instrument_id,
                    proposal.price_condition.threshold,
                    proposal.amounts.cash_currency,
                    backdated_at,
                    "backdated-price-evidence",
                    True,
                ),
                observation_cutoff_at=backdated_at,
                evaluated_at=backdated_at,
            )
        self.assertEqual(
            rollback.exception.code,
            "PRICE_CONDITION_TIME_ROLLBACK",
        )
        self.assertEqual(fixture.engine.snapshot, before)

    def test_partial_run_can_hold_stop_and_reactivate_only_pending_proposal(self) -> None:
        fixture, run = self._run_fixture("partial-lifecycle-run")
        fixture.engine.record_execution_attempt(
            run_id=run.execution_run_id,
            event_id="partial-lifecycle-first-attempt",
            proposal_id="run-p1",
            attempted_at=NOW + timedelta(minutes=1),
        )
        fixture.engine.confirm_attempt(
            run_id=run.execution_run_id,
            event_id="partial-lifecycle-first-attempt",
            result=AttemptResult.NO_FILL,
            confirmed_at=NOW + timedelta(minutes=2),
        )
        held = fixture.engine.hold_execution_run(
            run.execution_run_id,
            held_at=NOW + timedelta(minutes=3),
            reason_code="PENDING_PROPOSAL_GATE_CHANGED",
            evidence_ref="pending-gate-evidence",
            audit_id="partial-lifecycle-hold-audit",
        )
        self.assertEqual(held.state, RunState.TEMPORARILY_HELD)
        state = fixture.engine.snapshot
        self.assertEqual(state.proposals["run-p1"].state, ProposalState.CANCELLED)
        self.assertEqual(
            state.proposals["run-p2"].state,
            ProposalState.TEMPORARILY_HELD,
        )
        pending = state.proposals["run-p2"]
        assert pending.price_condition is not None
        rechecked_at = NOW + timedelta(minutes=4)
        fixture.engine.evaluate_proposal_price_condition(
            proposal_id=pending.proposal_id,
            observed_price=VerifiedPrice(
                pending.instrument_id,
                pending.price_condition.threshold,
                pending.amounts.cash_currency,
                rechecked_at,
                "partial-run-pending-price",
                True,
            ),
            observation_cutoff_at=rechecked_at,
            evaluated_at=rechecked_at,
        )
        resumed = fixture.engine.reactivate_execution_run(
            run.execution_run_id,
            explicitly_requested=True,
            gates=GateDecision.passed(),
            requested_at=NOW + timedelta(minutes=5),
            evidence_ref="all-pending-gates-rechecked",
            audit_id="partial-lifecycle-resume-audit",
        )
        self.assertEqual(resumed.state, RunState.RUNNING)
        self.assertEqual(
            fixture.engine.snapshot.proposals["run-p1"].state,
            ProposalState.CANCELLED,
        )
        stopped = fixture.engine.stop_execution_run(
            run.execution_run_id,
            stopped_at=NOW + timedelta(minutes=6),
            reason_code="USER_REQUESTED",
            evidence_ref="explicit-user-stop",
            audit_id="partial-lifecycle-stop-audit",
        )
        self.assertEqual(stopped.state, RunState.USER_STOPPED)
        resumed_again = fixture.engine.reactivate_execution_run(
            run.execution_run_id,
            explicitly_requested=True,
            gates=GateDecision.passed(),
            requested_at=NOW + timedelta(minutes=7),
            evidence_ref="explicit-user-resume",
            audit_id="partial-lifecycle-second-resume-audit",
        )
        self.assertEqual(resumed_again.state, RunState.RUNNING)
        final_state = fixture.engine.snapshot
        self.assertEqual(
            final_state.proposals["run-p1"].state,
            ProposalState.CANCELLED,
        )
        self.assertEqual(
            final_state.proposals["run-p2"].state,
            ProposalState.READY,
        )

    def test_attempt_cannot_precede_explicit_run_reactivation(self) -> None:
        fixture, run = self._run_fixture("attempt-after-reactivation-run")
        fixture.engine.hold_execution_run(
            run.execution_run_id,
            held_at=NOW + timedelta(minutes=10),
            reason_code="PRICE_CONDITION_NOT_MET",
            evidence_ref="hold-evidence",
            audit_id="attempt-reactivation-hold-audit",
        )
        rechecked_at = NOW + timedelta(minutes=11)
        for proposal_id in ("run-p1", "run-p2"):
            proposal = fixture.engine.snapshot.proposals[proposal_id]
            assert proposal.price_condition is not None
            fixture.engine.evaluate_proposal_price_condition(
                proposal_id=proposal_id,
                observed_price=VerifiedPrice(
                    proposal.instrument_id,
                    proposal.price_condition.threshold,
                    proposal.amounts.cash_currency,
                    rechecked_at,
                    f"reactivation-price-{proposal_id}",
                    True,
                ),
                observation_cutoff_at=rechecked_at,
                evaluated_at=rechecked_at,
            )
        reactivated_at = NOW + timedelta(minutes=20)
        fixture.engine.reactivate_execution_run(
            run.execution_run_id,
            explicitly_requested=True,
            gates=GateDecision.passed(),
            requested_at=reactivated_at,
            evidence_ref="all-gates-rechecked",
            audit_id="attempt-reactivation-resume-audit",
        )
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as rollback:
            fixture.engine.record_execution_attempt(
                run_id=run.execution_run_id,
                event_id="backdated-after-reactivation",
                proposal_id="run-p1",
                attempted_at=NOW + timedelta(minutes=15),
            )
        self.assertEqual(rollback.exception.code, "ATTEMPT_TIME_ROLLBACK")
        self.assertEqual(fixture.engine.snapshot, before)
        accepted = fixture.engine.record_execution_attempt(
            run_id=run.execution_run_id,
            event_id="causal-after-reactivation",
            proposal_id="run-p1",
            attempted_at=reactivated_at,
        )
        self.assertEqual(accepted.state, RunState.RUNNING)

    def test_partial_fill_time_is_floor_for_unknown_transition(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal(
            "partial-fill-unknown-floor",
            gross=100_000,
            quantity=10,
        )
        reservation = fixture.reserve(proposal, candidate)
        run_id, event_id = fixture.begin_attempt(proposal)
        fill_at = NOW + timedelta(minutes=10)
        fixture.engine.record_fill(
            observed_fill(
                "partial-fill-before-unknown",
                proposal,
                reservation.reservation_id,
                4,
                krw_amounts(40_000),
                fill_at,
            ),
            execution_policy=confirmed_execution_policy(),
            risk_context=fixture.risk_context(proposal),
            run_id=run_id,
            attempt_event_id=event_id,
        )
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as rollback:
            fixture.engine.mark_reservation_unknown(
                reservation.reservation_id,
                run_id=run_id,
                attempt_event_id=event_id,
                at=NOW + timedelta(minutes=5),
                reason_code="RESULT_NOT_CONFIRMED",
            )
        self.assertEqual(
            rollback.exception.code,
            "RESERVATION_STATE_TIME_INVALID",
        )
        self.assertEqual(fixture.engine.snapshot, before)
        fixture.engine.mark_reservation_unknown(
            reservation.reservation_id,
            run_id=run_id,
            attempt_event_id=event_id,
            at=fill_at,
            reason_code="RESULT_NOT_CONFIRMED",
        )
        self.assertEqual(
            fixture.engine.snapshot.runs[run_id].state,
            RunState.RECONCILIATION_REQUIRED,
        )

    def test_reactivation_rechecks_new_large_trade_aggregate(self) -> None:
        fixture = PaperFixture()
        proposal, candidate = fixture.add_proposal(
            "large-recheck-first",
            instrument="AGGREGATE",
            gross=100_000,
            intent="aggregate-intent",
        )
        fixture.reserve(proposal, candidate)
        included = ((proposal.proposal_id, proposal.version),)
        run = ExecutionRun(
            "large-recheck-run",
            "paper-portfolio",
            SESSION,
            included,
            canonical_hash(included),
            NOW,
            NOW + timedelta(hours=1),
            RunState.READY,
        )
        fixture.engine.create_execution_run(run, gates=GateDecision.passed())
        fixture.engine.hold_execution_run(
            run.execution_run_id,
            held_at=NOW + timedelta(minutes=2),
            reason_code="MANUAL_RECHECK",
            evidence_ref="manual-recheck-evidence",
            audit_id="large-recheck-hold-audit",
        )
        held_proposal = fixture.engine.snapshot.proposals[proposal.proposal_id]
        assert held_proposal.price_condition is not None
        rechecked_at = NOW + timedelta(minutes=3)
        fixture.engine.evaluate_proposal_price_condition(
            proposal_id=held_proposal.proposal_id,
            observed_price=VerifiedPrice(
                held_proposal.instrument_id,
                held_proposal.price_condition.threshold,
                held_proposal.amounts.cash_currency,
                rechecked_at,
                "large-recheck-price",
                True,
            ),
            observation_cutoff_at=rechecked_at,
            evaluated_at=rechecked_at,
        )
        fixture.add_proposal(
            "large-recheck-second",
            instrument="AGGREGATE",
            gross=200_000,
            intent="aggregate-intent",
        )
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as blocked:
            fixture.engine.reactivate_execution_run(
                run.execution_run_id,
                explicitly_requested=True,
                gates=GateDecision.passed(),
                requested_at=NOW + timedelta(minutes=4),
                evidence_ref="aggregate-rechecked",
                audit_id="large-recheck-resume-audit",
            )
        self.assertEqual(blocked.exception.code, "EXECUTION_RUN_GATES_NOT_PASS")
        self.assertEqual(fixture.engine.snapshot, before)

    def test_stop_and_close_obey_window_and_later_confirmation_time(self) -> None:
        fixture, run = self._run_fixture("close-causal-run")
        before_stop = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as late_stop:
            fixture.engine.stop_execution_run(
                run.execution_run_id,
                stopped_at=run.window_end,
                reason_code="USER_REQUESTED",
                evidence_ref="late-stop",
                audit_id="late-stop-audit",
            )
        self.assertEqual(
            late_stop.exception.code,
            "EXECUTION_RUN_STATE_TIME_INVALID",
        )
        self.assertEqual(fixture.engine.snapshot, before_stop)

        fixture.engine.record_execution_attempt(
            run_id=run.execution_run_id,
            event_id="late-confirm-attempt",
            proposal_id="run-p1",
            attempted_at=NOW + timedelta(minutes=1),
        )
        confirmed_at = NOW + timedelta(hours=2)
        fixture.engine.confirm_attempt(
            run_id=run.execution_run_id,
            event_id="late-confirm-attempt",
            result=AttemptResult.NO_FILL,
            confirmed_at=confirmed_at,
        )
        before_close = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as backdated_close:
            fixture.engine.close_execution_window(
                run.execution_run_id,
                ended_at=run.window_end,
            )
        self.assertEqual(
            backdated_close.exception.code,
            "EXECUTION_RUN_STATE_TIME_INVALID",
        )
        self.assertEqual(fixture.engine.snapshot, before_close)
        closed = fixture.engine.close_execution_window(
            run.execution_run_id,
            ended_at=confirmed_at,
        )
        self.assertEqual(closed.state, RunState.COMPLETED)
        self.assertEqual(closed.ended_at, confirmed_at)

    def test_kst_or_us_session_overlap_blocks_every_new_proposal(self) -> None:
        fixture = PaperFixture()
        first, first_approval = fixture.add_proposal(
            "date-first",
            instrument="SAME",
            gross=10_000,
            intent="intent-one",
        )
        fixture.reserve(first, first_approval)
        same_session, same_session_approval = fixture.add_proposal(
            "same-session",
            instrument="SAME",
            gross=10_000,
            kst_day=date(2028, 1, 4),
            intent="intent-two",
        )
        with self.assertRaises(PaperBlockedError) as us_conflict:
            fixture.reserve(same_session, same_session_approval)
        self.assertEqual(us_conflict.exception.code, "SAME_INSTRUMENT_DATE_CONFLICT")
        same_kst, same_kst_approval = fixture.add_proposal(
            "same-kst",
            instrument="SAME",
            gross=10_000,
            session=date(2028, 1, 3),
            kst_day=KST_DAY,
            intent="intent-three",
        )
        with self.assertRaises(PaperBlockedError) as kst_conflict:
            fixture.reserve(same_kst, same_kst_approval)
        self.assertEqual(kst_conflict.exception.code, "SAME_INSTRUMENT_DATE_CONFLICT")
        partial, partial_approval = fixture.add_proposal(
            "same-intent",
            instrument="SAME",
            gross=10_000,
            intent="intent-one",
        )
        with self.assertRaises(PaperBlockedError) as same_intent_conflict:
            fixture.reserve(partial, partial_approval)
        self.assertEqual(
            same_intent_conflict.exception.code,
            "SAME_INSTRUMENT_DATE_CONFLICT",
        )


class PaperFinalInvariantRegressionTests(unittest.TestCase):
    def test_safety_critical_value_objects_and_public_boundaries_are_exact_types(self) -> None:
        fixture = PaperFixture()
        proposal, candidate_approval = fixture.add_proposal("typed-boundary")
        unchecked = _UncheckedTradeAmounts(
            Decimal("100000"),
            Decimal("0"),
            Decimal("1"),
            Currency.KRW,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )
        with self.assertRaises(DomainValidationError):
            replace(proposal, amounts=unchecked)
        unchecked_condition = _UncheckedPriceCondition(
            PriceConditionOperator.AT_OR_BELOW,
            Decimal("1"),
            Currency.KRW,
            "unchecked-condition",
            NOW,
        )
        with self.assertRaises(DomainValidationError):
            replace(proposal, price_condition=unchecked_condition)

        reservation = fixture.reserve(proposal, candidate_approval)
        run_id, event_id = fixture.begin_attempt(proposal)
        valid_fill = observed_fill(
            "typed-fill",
            proposal,
            reservation.reservation_id,
            1,
            krw_amounts(100_000),
            NOW + timedelta(minutes=2),
        )
        with self.assertRaises(DomainValidationError):
            replace(valid_fill, amounts=unchecked)

        before = fixture.engine.snapshot
        invalid_calls = (
            lambda: fixture.engine.create_proposal(object()),  # type: ignore[arg-type]
            lambda: fixture.engine.reserve_proposal(  # type: ignore[arg-type]
                object(),
                execution_policy=confirmed_execution_policy(),
                risk_context=fixture.risk_context(proposal),
                candidate_approval_id=candidate_approval,
            ),
            lambda: fixture.engine.record_fill(  # type: ignore[arg-type]
                object(),
                execution_policy=confirmed_execution_policy(),
                risk_context=fixture.risk_context(proposal),
                run_id=run_id,
                attempt_event_id=event_id,
            ),
            lambda: fixture.engine.create_execution_run(  # type: ignore[arg-type]
                object(),
                gates=GateDecision.passed(),
            ),
        )
        for invalid_call in invalid_calls:
            with self.subTest(invalid_call=invalid_call), self.assertRaises(
                DomainValidationError
            ):
                invalid_call()
            self.assertEqual(fixture.engine.snapshot, before)

    def test_reactivation_rejects_gate_subclass_that_claims_pass(self) -> None:
        fixture = PaperFixture()
        proposal, candidate_approval = fixture.add_proposal("typed-reactivation")
        fixture.reserve(proposal, candidate_approval)
        included = ((proposal.proposal_id, proposal.version),)
        run = ExecutionRun(
            "typed-reactivation-run",
            "paper-portfolio",
            proposal.us_session_date,
            included,
            canonical_hash(included),
            NOW + timedelta(minutes=1),
            NOW + timedelta(hours=1),
            RunState.READY,
        )
        fixture.engine.create_execution_run(run, gates=GateDecision.passed())
        fixture.engine.hold_execution_run(
            run.execution_run_id,
            held_at=NOW + timedelta(minutes=2),
            reason_code="PRICE_CONDITION_NOT_MET",
            evidence_ref="typed-gate-hold",
            audit_id="typed-gate-hold-audit",
        )
        before = fixture.engine.snapshot
        hostile_gate = _UncheckedGateDecision(
            GateStatus.BLOCKED,
            ("BLOCKED_BUT_PROPERTY_RETURNS_TRUE",),
        )
        with self.assertRaises(DomainValidationError):
            fixture.engine.reactivate_execution_run(
                run.execution_run_id,
                explicitly_requested=True,
                gates=hostile_gate,
                requested_at=NOW + timedelta(minutes=3),
                evidence_ref="hostile-gate",
                audit_id="hostile-gate-audit",
            )
        self.assertEqual(fixture.engine.snapshot, before)

    def test_valuation_pair_iterator_error_is_safe(self) -> None:
        class HostilePair:
            def __iter__(self):
                raise RuntimeError("SENSITIVE_RAW_VALUE")

        with self.assertRaises(DomainValidationError) as invalid:
            PaperValuationSnapshot(
                "hostile-valuation",
                "paper-portfolio",
                NOW,
                NOW,
                GateStatus.PASS,
                Decimal("4000000"),
                Decimal("4000000"),
                Decimal("0"),
                Decimal("0"),
                (HostilePair(),),  # type: ignore[arg-type]
            )
        self.assertNotIn("SENSITIVE_RAW_VALUE", str(invalid.exception))

    def test_fx_event_rejects_unchecked_rate_subclass_without_mutation(self) -> None:
        fixture = PaperFixture()
        unchecked_rate = _UncheckedVerifiedFxRate(
            Currency.USD,
            Decimal("1300"),
            NOW,
            "unchecked-fx",
            True,
        )
        before = fixture.engine.snapshot
        with self.assertRaises(DomainValidationError):
            fixture.engine.record_fx(
                event_id="unchecked-fx-event",
                from_currency=Currency.KRW,
                from_amount=Decimal("1300"),
                to_currency=Currency.USD,
                to_amount=Decimal("1"),
                occurred_at=NOW,
                fx_rate=unchecked_rate,
                cutoff_at=NOW,
                cost_krw=Decimal("0"),
                cost_model_confirmed=True,
            )
        self.assertEqual(fixture.engine.snapshot, before)

    def test_approval_target_type_rejection_is_atomic(self) -> None:
        fixture = PaperFixture()
        before = fixture.engine.snapshot
        invalid_targets = (
            None,
            _ApprovalTargetSubclass("candidate", "v1", "hash"),
        )
        for index, target in enumerate(invalid_targets):
            with self.subTest(target=type(target).__name__), self.assertRaises(
                DomainValidationError
            ):
                fixture.engine.grant_approval(
                    approval_id=f"invalid-target-{index}",
                    approval_type=ApprovalType.CANDIDATE_SET,
                    target=target,  # type: ignore[arg-type]
                    approved_by_user_id="user",
                    approved_at=NOW,
                    audit_ref=f"invalid-target-audit-{index}",
                )
            self.assertEqual(fixture.engine.snapshot, before)

        class HostileTarget:
            def __eq__(self, other):
                raise RuntimeError("SENSITIVE_TARGET")

            def __ne__(self, other):
                raise RuntimeError("SENSITIVE_TARGET")

        fixture.engine.grant_approval(
            approval_id="bind-target-approval",
            approval_type=ApprovalType.CANDIDATE_SET,
            target=ApprovalTarget("bind-target", "v1", "bind-hash"),
            approved_by_user_id="user",
            approved_at=NOW,
            audit_ref="bind-target-audit",
        )
        before_bind = fixture.engine.snapshot
        with self.assertRaises(DomainValidationError) as safe_error:
            fixture.engine.bind_approval(
                approval_id="bind-target-approval",
                expected_type=ApprovalType.CANDIDATE_SET,
                expected_target=HostileTarget(),  # type: ignore[arg-type]
                consumer_ref="consumer",
                now=NOW,
            )
        self.assertNotIn("SENSITIVE_TARGET", str(safe_error.exception))
        self.assertEqual(fixture.engine.snapshot, before_bind)

    def test_candidate_regeneration_has_one_atomic_successor(self) -> None:
        fixture = PaperFixture()
        proposal, _approval = fixture.add_proposal(
            "regeneration-parent",
            gross=300_000,
        )
        fixture.engine.reject_large_trade_proposal(
            proposal.proposal_id,
            rejected_at=NOW + timedelta(minutes=1),
            rejected_by_user_ref="local-user",
            audit_id="regeneration-parent-rejected",
        )
        barrier = threading.Barrier(2)

        def child(set_id: str, instrument_id: str) -> CandidateSet:
            candidates = (Candidate(1, instrument_id),)
            created_at = NOW + timedelta(minutes=2)
            return CandidateSet(
                set_id,
                "v2",
                candidate_set_content_hash(
                    set_id=set_id,
                    version="v2",
                    trade_purpose_id="purpose-v1",
                    stable_root_trade_purpose_id="root-1",
                    candidates=candidates,
                    evidence_cutoff=created_at,
                    created_at=created_at,
                    regeneration_of=proposal.candidate_set_id,
                ),
                "purpose-v1",
                "root-1",
                candidates,
                created_at,
                created_at,
                regeneration_of=proposal.candidate_set_id,
            )

        children = (
            child("regeneration-child-a", "CHILD-A"),
            child("regeneration-child-b", "CHILD-B"),
        )

        def create(candidate_set: CandidateSet) -> str:
            barrier.wait()
            try:
                fixture.engine.create_candidate_set(candidate_set)
            except PaperBlockedError as exc:
                return exc.code
            return "CREATED"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(create, children))
        self.assertCountEqual(
            outcomes,
            ("CREATED", "REGENERATION_NOT_REQUESTED"),
        )
        state = fixture.engine.snapshot
        self.assertEqual(
            state.candidate_sets[proposal.candidate_set_id].state,
            CandidateSetState.SUPERSEDED,
        )
        self.assertEqual(
            sum(child_set.set_id in state.candidate_sets for child_set in children),
            1,
        )

    def test_strategy_application_requires_current_lineage_and_actual_change(self) -> None:
        fixture = PaperFixture()
        fixture.engine.save_strategy_draft(
            PaperStrategyVersion("lineage-a", "hash-a", NOW, "strategy-v1")
        )
        fixture.engine.save_strategy_draft(
            PaperStrategyVersion("lineage-stale", "hash-stale", NOW, "strategy-v1")
        )
        fixture.engine.apply_strategy_version(
            StrategyApplication(
                "lineage-apply-a",
                "lineage-a",
                "approval-a",
                NOW + timedelta(days=14),
            )
        )
        fixture.engine.save_strategy_draft(
            PaperStrategyVersion(
                "lineage-b",
                "hash-b",
                NOW + timedelta(days=14),
                "lineage-a",
            )
        )
        for application, expected_code in (
            (
                StrategyApplication(
                    "lineage-repeat",
                    "lineage-a",
                    "approval-repeat",
                    NOW + timedelta(days=28),
                ),
                "STRATEGY_VERSION_ALREADY_APPLIED",
            ),
            (
                StrategyApplication(
                    "lineage-stale-apply",
                    "lineage-stale",
                    "approval-stale",
                    NOW + timedelta(days=28),
                ),
                "STALE_STRATEGY_VERSION",
            ),
        ):
            before = fixture.engine.snapshot
            with self.subTest(expected_code=expected_code), self.assertRaises(
                PaperBlockedError
            ) as blocked:
                fixture.engine.apply_strategy_version(application)
            self.assertEqual(blocked.exception.code, expected_code)
            self.assertEqual(fixture.engine.snapshot, before)
        fixture.engine.apply_strategy_version(
            StrategyApplication(
                "lineage-apply-b",
                "lineage-b",
                "approval-b",
                NOW + timedelta(days=28),
            )
        )
        self.assertEqual(
            fixture.engine.snapshot.strategy_applications[-1].strategy_version_id,
            "lineage-b",
        )

    def test_strategy_and_trade_purpose_descendants_cannot_precede_parents(self) -> None:
        repository = InMemoryPaperRepository()
        engine = PaperEngine(repository)
        engine.create_portfolio(
            portfolio_id="lineage-portfolio",
            owner_ref="local-owner",
            created_at=NOW,
            genesis_entry_id="lineage-genesis",
        )
        engine.save_strategy_draft(
            PaperStrategyVersion(
                "future-parent",
                "future-parent-hash",
                NOW + timedelta(minutes=2),
            )
        )
        before = engine.snapshot
        with self.assertRaises(PaperBlockedError) as time_rollback:
            engine.save_strategy_draft(
                PaperStrategyVersion(
                    "backdated-child",
                    "backdated-child-hash",
                    NOW + timedelta(minutes=1),
                    "future-parent",
                )
            )
        self.assertEqual(
            time_rollback.exception.code,
            "STRATEGY_VERSION_TIME_ROLLBACK",
        )
        self.assertEqual(engine.snapshot, before)
        engine.save_strategy_draft(
            PaperStrategyVersion(
                "valid-child",
                "valid-child-hash",
                NOW + timedelta(minutes=3),
                "future-parent",
            )
        )
        before_apply = engine.snapshot
        with self.assertRaises(PaperBlockedError) as non_root_first:
            engine.apply_strategy_version(
                StrategyApplication(
                    "invalid-first-application",
                    "valid-child",
                    "approval",
                    NOW + timedelta(minutes=4),
                )
            )
        self.assertEqual(
            non_root_first.exception.code,
            "INITIAL_STRATEGY_VERSION_MUST_BE_ROOT",
        )
        self.assertEqual(engine.snapshot, before_apply)

        fixture = PaperFixture()
        parent_hash = trade_purpose_version_hash(
            trade_purpose_id="purpose-v2-later",
            stable_root_trade_purpose_id="root-1",
            purpose_version="v2",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            strategy_version_ref="strategy-v1",
            budget_version_ref="budget-v1",
            policy_version_ref="policy-v1",
            intended_effect="같은 목적 v2",
            parent_trade_purpose_id="purpose-v1",
        )
        fixture.engine.create_trade_purpose(
            TradePurpose(
                "purpose-v2-later",
                "root-1",
                "v2",
                parent_hash,
                PaperStrategyKind.LONG_TERM_INDEX,
                "strategy-v1",
                "budget-v1",
                "policy-v1",
                "같은 목적 v2",
                NOW + timedelta(minutes=10),
                parent_trade_purpose_id="purpose-v1",
            )
        )
        child_hash = trade_purpose_version_hash(
            trade_purpose_id="purpose-v3-backdated",
            stable_root_trade_purpose_id="root-1",
            purpose_version="v3",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            strategy_version_ref="strategy-v1",
            budget_version_ref="budget-v1",
            policy_version_ref="policy-v1",
            intended_effect="같은 목적 v3",
            parent_trade_purpose_id="purpose-v2-later",
        )
        before_purpose = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as purpose_rollback:
            fixture.engine.create_trade_purpose(
                TradePurpose(
                    "purpose-v3-backdated",
                    "root-1",
                    "v3",
                    child_hash,
                    PaperStrategyKind.LONG_TERM_INDEX,
                    "strategy-v1",
                    "budget-v1",
                    "policy-v1",
                    "같은 목적 v3",
                    NOW + timedelta(minutes=5),
                    parent_trade_purpose_id="purpose-v2-later",
                )
            )
        self.assertEqual(
            purpose_rollback.exception.code,
            "TRADE_PURPOSE_TIME_ROLLBACK",
        )
        self.assertEqual(fixture.engine.snapshot, before_purpose)

    def test_regenerated_candidate_set_cannot_predate_rejection(self) -> None:
        fixture = PaperFixture()
        proposal, _approval = fixture.add_proposal(
            "regeneration-time-parent",
            gross=300_000,
        )
        fixture.engine.reject_large_trade_proposal(
            proposal.proposal_id,
            rejected_at=NOW + timedelta(minutes=10),
            rejected_by_user_ref="local-user",
            audit_id="regeneration-time-rejection",
        )
        candidates = (Candidate(1, "BACKDATED-CHILD"),)
        created_at = NOW + timedelta(minutes=1)
        backdated = CandidateSet(
            "regeneration-time-child",
            "v2",
            candidate_set_content_hash(
                set_id="regeneration-time-child",
                version="v2",
                trade_purpose_id="purpose-v1",
                stable_root_trade_purpose_id="root-1",
                candidates=candidates,
                evidence_cutoff=created_at,
                created_at=created_at,
                regeneration_of=proposal.candidate_set_id,
            ),
            "purpose-v1",
            "root-1",
            candidates,
            created_at,
            created_at,
            regeneration_of=proposal.candidate_set_id,
        )
        before = fixture.engine.snapshot
        with self.assertRaises(PaperBlockedError) as rollback:
            fixture.engine.create_candidate_set(backdated)
        self.assertEqual(
            rollback.exception.code,
            "REGENERATED_SET_TIME_ROLLBACK",
        )
        self.assertEqual(fixture.engine.snapshot, before)

    def test_portfolio_creation_is_causal_floor_for_versions_and_purposes(self) -> None:
        fixture = PaperFixture()
        backdated = NOW - timedelta(microseconds=1)
        allocations = fixture.engine.snapshot.budgets["budget-v1"].allocations_krw
        purpose_hash = trade_purpose_version_hash(
            trade_purpose_id="backdated-purpose",
            stable_root_trade_purpose_id="root-1",
            purpose_version="v2",
            strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
            strategy_version_ref="strategy-v1",
            budget_version_ref="budget-v1",
            policy_version_ref="policy-v1",
            intended_effect="동일 목적의 소급 입력 차단",
            parent_trade_purpose_id="purpose-v1",
        )
        cases = (
            (
                lambda: fixture.engine.save_budget_version(
                    PaperBudgetVersion(
                        "backdated-budget",
                        "policy-v1",
                        allocations,
                        backdated,
                    )
                ),
                "BUDGET_VERSION_BEFORE_PORTFOLIO",
            ),
            (
                lambda: fixture.engine.save_strategy_draft(
                    PaperStrategyVersion(
                        "backdated-strategy",
                        "backdated-hash",
                        backdated,
                        "strategy-v1",
                    )
                ),
                "STRATEGY_VERSION_BEFORE_PORTFOLIO",
            ),
            (
                lambda: fixture.engine.apply_strategy_version(
                    StrategyApplication(
                        "backdated-application",
                        "strategy-v1",
                        "backdated-approval",
                        backdated,
                    )
                ),
                "STRATEGY_APPLICATION_BEFORE_PORTFOLIO",
            ),
            (
                lambda: fixture.engine.create_trade_purpose(
                    TradePurpose(
                        "backdated-purpose",
                        "root-1",
                        "v2",
                        purpose_hash,
                        PaperStrategyKind.LONG_TERM_INDEX,
                        "strategy-v1",
                        "budget-v1",
                        "policy-v1",
                        "동일 목적의 소급 입력 차단",
                        backdated,
                        parent_trade_purpose_id="purpose-v1",
                    )
                ),
                "TRADE_PURPOSE_BEFORE_PORTFOLIO",
            ),
        )
        for operation, expected_code in cases:
            before = fixture.engine.snapshot
            with self.subTest(expected_code=expected_code), self.assertRaises(
                PaperBlockedError
            ) as blocked:
                operation()
            self.assertEqual(blocked.exception.code, expected_code)
            self.assertEqual(fixture.engine.snapshot, before)

    def test_complete_fill_appends_price_and_cost_remainder_release(self) -> None:
        cases = (
            ("lower-gross", krw_amounts(90_000, 1_000), Decimal("10000")),
            ("lower-cost", krw_amounts(100_000, 1_000), Decimal("0")),
        )
        for suffix, fill_amounts, expected_daily_release in cases:
            with self.subTest(suffix=suffix):
                fixture = PaperFixture()
                proposal, candidate_approval = fixture.add_proposal(
                    f"remainder-{suffix}",
                    gross=100_000,
                    cost=5_000,
                )
                reservation = fixture.reserve(proposal, candidate_approval)
                run_id, event_id = fixture.begin_attempt(proposal)
                fixture.engine.record_fill(
                    observed_fill(
                        f"remainder-fill-{suffix}",
                        proposal,
                        reservation.reservation_id,
                        1,
                        fill_amounts,
                        NOW + timedelta(minutes=2),
                    ),
                    execution_policy=confirmed_execution_policy(),
                    risk_context=fixture.risk_context(proposal),
                    run_id=run_id,
                    attempt_event_id=event_id,
                    lot_id=f"remainder-lot-{suffix}",
                )
                state = fixture.engine.snapshot
                release = state.ledger_entries[-1]
                self.assertEqual(release.event_type.value, "RELEASE")
                self.assertEqual(release.amount, expected_daily_release)
                self.assertEqual(
                    release.source_ref,
                    "PAPER_COMPLETE_FILL_REMAINDER_RELEASE",
                )
                self.assertEqual(
                    state.audits[-1].event_type,
                    "RESERVATION_REMAINDER_RELEASED",
                )
                self.assertEqual(
                    state.reservations[reservation.reservation_id].state,
                    ReservationState.CONSUMED,
                )
                self.assertEqual(
                    state.daily_limits[proposal.us_session_date].active_reserved_gross_krw,
                    Decimal("0"),
                )


if __name__ == "__main__":
    unittest.main()
