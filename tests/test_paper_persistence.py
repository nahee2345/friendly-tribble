from __future__ import annotations

import copy
import multiprocessing
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from asset_assistant.domain import DomainValidationError
from asset_assistant.paper import (
    Currency,
    Candidate,
    CandidateFailureReason,
    CandidateSet,
    CandidateSetState,
    ApprovalTarget,
    ApprovalType,
    PaperBudgetVersion,
    PaperBlockedError,
    PaperEngine,
    PaperPortfolio,
    PaperStrategyKind,
    PaperStrategyVersion,
    Side,
    StrategyApplication,
    TradePurpose,
    _PaperState,
    trade_purpose_version_hash,
    candidate_set_content_hash,
)
from tests.test_paper import PaperFixture
from asset_assistant.paper_persistence import (
    PAPER_CODEC_VERSION,
    PAPER_SCHEMA_REVISION,
    PaperCommandEnvelope,
    PaperPersistenceError,
    SQLitePaperRepository,
    _STATE_FIELD_NAMES,
    _decode,
    _encode,
    validate_paper_schema_connection,
)


UTC = timezone.utc
NOW = datetime(2028, 1, 2, 15, 0, 0, 123456, tzinfo=UTC)
OWNER = "local-owner"
PORTFOLIO = "paper-portfolio"


def _owner_guard(value: str | None) -> None:
    if value != OWNER:
        raise RuntimeError("owner rejected")


def _create_portfolio(engine: PaperEngine) -> PaperPortfolio:
    return engine.create_portfolio(
        portfolio_id=PORTFOLIO,
        owner_ref=OWNER,
        created_at=NOW,
        genesis_entry_id="genesis-fixture",
    )


def _multiprocess_same_receipt(path_text: str, start, queue) -> None:
    path = Path(path_text)
    start.wait(10)
    try:
        with SQLitePaperRepository(
            path,
            owner_id=OWNER,
            portfolio_id=PORTFOLIO,
            owner_guard=_owner_guard,
        ) as repository:
            result = repository.execute(
                PaperCommandEnvelope(
                    OWNER,
                    PORTFOLIO,
                    "shared.command.0001",
                    "CREATE_PORTFOLIO",
                    {"fixture": "same-request"},
                    0,
                ),
                _create_portfolio,
            )
            queue.put(("ok", result.historical_replay, result.result_revision))
    except PaperPersistenceError as exc:
        queue.put(("error", exc.code))


def _multiprocess_stale_revision(path_text: str, key: str, start, queue) -> None:
    path = Path(path_text)
    start.wait(10)
    try:
        with SQLitePaperRepository(
            path,
            owner_id=OWNER,
            portfolio_id=PORTFOLIO,
            owner_guard=_owner_guard,
        ) as repository:
            result = repository.execute(
                PaperCommandEnvelope(
                    OWNER,
                    PORTFOLIO,
                    key,
                    "CREATE_PORTFOLIO",
                    {"fixture": key},
                    0,
                ),
                _create_portfolio,
            )
            queue.put(("ok", result.result_revision))
    except PaperPersistenceError as exc:
        queue.put(("error", exc.code))


def _crash_worker(path_text: str, stage: str) -> None:
    def fault_hook(current: str) -> None:
        if current == stage:
            os.kill(os.getpid(), signal.SIGKILL)

    with SQLitePaperRepository(
        Path(path_text),
        owner_id=OWNER,
        portfolio_id=PORTFOLIO,
        owner_guard=_owner_guard,
        fault_hook=fault_hook,
    ) as repository:
        repository.execute(
            PaperCommandEnvelope(
                OWNER,
                PORTFOLIO,
                f"crash.{stage}.0001",
                "CREATE_PORTFOLIO",
                {"stage": stage},
                0,
            ),
            _create_portfolio,
        )


class _PersistentEngineFacade:
    """Test-only facade that gives every P1 mutation its own command envelope."""

    _READ_ONLY = frozenset({"large_approval_group_amount", "large_approval_group_target"})

    def __init__(self, repository: SQLitePaperRepository) -> None:
        self.repository = repository
        self.sequence = repository.snapshot_with_revision().revision

    @property
    def snapshot(self) -> _PaperState:
        return self.repository.snapshot()

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_") or not hasattr(PaperEngine, name):
            raise AttributeError(name)

        def invoke(*args: Any, **kwargs: Any) -> Any:
            if name in self._READ_ONLY:
                class ReadOnlyRepository:
                    def snapshot(inner_self) -> _PaperState:
                        return self.repository.snapshot()

                    def transact(inner_self, operation):
                        raise AssertionError("read-only PAPER query attempted mutation")

                return getattr(PaperEngine(ReadOnlyRepository()), name)(*args, **kwargs)
            before = self.repository.snapshot_with_revision()
            self.sequence += 1
            envelope = PaperCommandEnvelope(
                OWNER,
                PORTFOLIO,
                f"fixture.command.{self.sequence:06d}",
                name.upper(),
                (args, kwargs),
                before.revision,
            )
            return self.repository.execute(
                envelope,
                lambda engine: getattr(engine, name)(*args, **kwargs),
            ).value

        return invoke


def _populate_base(repository: SQLitePaperRepository) -> _PersistentEngineFacade:
    engine = _PersistentEngineFacade(repository)
    engine.create_portfolio(
        portfolio_id=PORTFOLIO,
        owner_ref=OWNER,
        created_at=NOW,
        genesis_entry_id="genesis-fixture",
    )
    allocations = tuple(
        (
            kind,
            Decimal("4000000")
            if kind is PaperStrategyKind.LONG_TERM_INDEX
            else Decimal("0"),
        )
        for kind in PaperStrategyKind
    )
    engine.save_budget_version(PaperBudgetVersion("budget-v1", "policy-v1", allocations, NOW))
    engine.save_strategy_draft(PaperStrategyVersion("strategy-v1", "strategy-hash-v1", NOW))
    engine.apply_strategy_version(
        StrategyApplication("strategy-application-v1", "strategy-v1", "approval-fixture", NOW)
    )
    purpose_hash = trade_purpose_version_hash(
        trade_purpose_id="purpose-v1",
        stable_root_trade_purpose_id="root-1",
        purpose_version="v1",
        strategy_id=PaperStrategyKind.LONG_TERM_INDEX,
        strategy_version_ref="strategy-v1",
        budget_version_ref="budget-v1",
        policy_version_ref="policy-v1",
        intended_effect="fixture-purpose",
    )
    engine.create_trade_purpose(
        TradePurpose(
            "purpose-v1",
            "root-1",
            "v1",
            purpose_hash,
            PaperStrategyKind.LONG_TERM_INDEX,
            "strategy-v1",
            "budget-v1",
            "policy-v1",
            "fixture-purpose",
            NOW,
        )
    )
    return engine


class SQLitePaperPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(dir="/dev/shm", prefix="paper-persistence-test.")
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "paper-state.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def repository(self, **kwargs: Any) -> SQLitePaperRepository:
        return SQLitePaperRepository(
            self.path,
            owner_id=OWNER,
            portfolio_id=PORTFOLIO,
            owner_guard=_owner_guard,
            **kwargs,
        )

    @staticmethod
    def create_envelope(*, key: str = "create.command.0001", expected: int = 0, payload: Any = None) -> PaperCommandEnvelope:
        return PaperCommandEnvelope(
            OWNER,
            PORTFOLIO,
            key,
            "CREATE_PORTFOLIO",
            {"fixture": "create"} if payload is None else payload,
            expected,
        )

    def initialize_empty_database(self) -> None:
        with self.repository() as repository:
            self.assertEqual(repository.snapshot_with_revision().revision, 0)

    def test_create_restart_exact_21_fields_and_schema(self) -> None:
        with self.repository(clock=lambda: NOW) as repository:
            engine = _populate_base(repository)
            before = engine.snapshot
            revision = repository.snapshot_with_revision().revision
            self.assertEqual(len(_STATE_FIELD_NAMES), 21)
            self.assertEqual(tuple(before.__dict__), _STATE_FIELD_NAMES)
            row_count = repository._connection.execute(
                "SELECT count(*) FROM paper_state_fields"
            ).fetchone()[0]
            self.assertEqual(row_count, 21)
            ledger_row = repository._connection.execute(
                "SELECT codec_version FROM paper_schema_ledger WHERE revision=?",
                (PAPER_SCHEMA_REVISION,),
            ).fetchone()
            self.assertEqual(ledger_row[0], PAPER_CODEC_VERSION)
            validate_paper_schema_connection(repository._connection)
        with self.repository() as restarted:
            after = restarted.snapshot_with_revision()
            self.assertEqual(after.revision, revision)
            self.assertEqual(after.state, before)
            self.assertEqual(after.state.cash[Currency.KRW], Decimal("4000000"))
            self.assertEqual(after.state.portfolio.created_at.microsecond, 123456)

    def test_populated_21_field_state_restart_preserves_next_decisions(self) -> None:
        with self.repository(clock=lambda: NOW + timedelta(hours=2)) as repository:
            persistent_engine = _populate_base(repository)
            fixture = PaperFixture.__new__(PaperFixture)
            fixture.repository = repository
            fixture.engine = persistent_engine
            fixture._risk_contexts = {}
            fixture.buy_lot(
                proposal_id="persisted-opening-buy",
                instrument="PERSIST",
                quantity=10,
                gross=100_000,
                lot_id="persisted-lot",
            )
            sell, candidate_approval = fixture.add_proposal(
                "persisted-unknown-sell",
                instrument="PERSIST",
                gross=60_000,
                quantity=6,
                side=Side.SELL,
            )
            unknown_reservation = fixture.reserve(
                sell,
                candidate_approval,
                sell_lots=(("persisted-lot", 6),),
            )
            run_id, attempt_id = fixture.begin_attempt(sell)
            fixture.engine.mark_reservation_unknown(
                unknown_reservation.reservation_id,
                run_id=run_id,
                attempt_event_id=attempt_id,
                at=NOW + timedelta(minutes=2),
                reason_code="FIXTURE_RESULT_UNKNOWN",
            )

            clearance_candidates = (Candidate(1, "FAIL-FIRST"), Candidate(2, "NEXT"))
            clearance_hash = candidate_set_content_hash(
                set_id="clearance-set",
                version="v1",
                trade_purpose_id="purpose-v1",
                stable_root_trade_purpose_id="root-1",
                candidates=clearance_candidates,
                evidence_cutoff=NOW,
                created_at=NOW,
            )
            fixture.engine.create_candidate_set(
                CandidateSet(
                    "clearance-set",
                    "v1",
                    clearance_hash,
                    "purpose-v1",
                    "root-1",
                    clearance_candidates,
                    NOW,
                    NOW,
                )
            )
            fixture.engine.transition_candidate_set(
                "clearance-set", CandidateSetState.VALIDATED
            )
            fixture.engine.transition_candidate_set(
                "clearance-set", CandidateSetState.WAITING_SET_APPROVAL
            )
            fixture.engine.grant_approval(
                approval_id="clearance-approval",
                approval_type=ApprovalType.CANDIDATE_SET,
                target=ApprovalTarget("clearance-set", "v1", clearance_hash),
                approved_by_user_id="fixture-user",
                approved_at=NOW,
                audit_ref="clearance-approval-audit",
            )
            fixture.engine.transition_candidate_set(
                "clearance-set",
                CandidateSetState.SET_APPROVED,
                approval_id="clearance-approval",
                transitioned_at=NOW,
            )
            next_candidate = fixture.engine.next_candidate(
                set_id="clearance-set",
                current_rank=1,
                reason=CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE,
                evidence_verified=True,
                approved_permanent_reasons=frozenset(
                    {CandidateFailureReason.PERMANENT_POLICY_INELIGIBLE}
                ),
                evidence_ref="fixture-permanent-evidence",
                verified_at=NOW,
            )
            self.assertEqual(next_candidate.rank, 2)
            before = repository.snapshot_with_revision()
            self.assertEqual(len(before.state.__dict__), 21)
            self.assertTrue(all(bool(getattr(before.state, name)) for name in _STATE_FIELD_NAMES))

        with self.repository(clock=lambda: NOW + timedelta(hours=3)) as restarted:
            after = restarted.snapshot_with_revision()
            self.assertEqual(after, before)
            fixture.repository = restarted
            fixture.engine = _PersistentEngineFacade(restarted)
            fixture._risk_contexts = {}
            second_sell, second_candidate_approval = fixture.add_proposal(
                "post-restart-overlap",
                instrument="PERSIST",
                gross=50_000,
                quantity=5,
                side=Side.SELL,
                session=sell.us_session_date + timedelta(days=1),
                kst_day=sell.kst_intent_date + timedelta(days=1),
            )
            with self.assertRaises(PaperBlockedError):
                fixture.reserve(
                    second_sell,
                    second_candidate_approval,
                    sell_lots=(("persisted-lot", 5),),
                )

    def test_codec_canonical_decimal_and_all_state_fields(self) -> None:
        value = (Decimal("123.4500"), NOW, {("x", 1): [True, None]})
        restored = _decode(_encode(value))
        self.assertEqual(restored, (Decimal("123.45"), NOW, {("x", 1): [True, None]}))
        state = _PaperState()
        restored_state = _decode(_encode(state))
        self.assertEqual(restored_state, state)
        self.assertEqual(tuple(restored_state.__dict__), _STATE_FIELD_NAMES)
        with self.assertRaises(DomainValidationError):
            _encode(0.1)

    def test_receipt_is_checked_before_revision_and_different_fingerprint_conflicts(self) -> None:
        callback_calls = 0

        def callback(engine: PaperEngine) -> PaperPortfolio:
            nonlocal callback_calls
            callback_calls += 1
            return _create_portfolio(engine)

        with self.repository(clock=lambda: NOW) as repository:
            envelope = self.create_envelope()
            first = repository.execute(envelope, callback)
            replay = repository.execute(
                self.create_envelope(expected=999),
                callback,
            )
            self.assertFalse(first.historical_replay)
            self.assertTrue(replay.historical_replay)
            self.assertEqual(callback_calls, 1)
            self.assertEqual(first.value, replay.value)
            self.assertEqual(first.completed_at, replay.completed_at)
            with self.assertRaises(PaperPersistenceError) as conflict:
                repository.execute(
                    self.create_envelope(expected=1, payload={"fixture": "changed"}),
                    callback,
                )
            self.assertEqual(conflict.exception.code, "IDEMPOTENCY_KEY_CONFLICT")
            self.assertEqual(repository.snapshot_with_revision().revision, 1)

    def test_stale_revision_and_failed_callback_leave_no_receipt_or_state(self) -> None:
        with self.repository() as repository:
            with self.assertRaises(PaperPersistenceError) as stale:
                repository.execute(self.create_envelope(expected=1), _create_portfolio)
            self.assertEqual(stale.exception.code, "PAPER_REVISION_CONFLICT")
            self.assertEqual(repository.snapshot_with_revision().revision, 0)
            receipts = repository._connection.execute(
                "SELECT count(*) FROM paper_command_receipts"
            ).fetchone()[0]
            self.assertEqual(receipts, 0)

            def failing(engine: PaperEngine) -> None:
                _create_portfolio(engine)
                raise RuntimeError("fixture callback failure")

            with self.assertRaises(RuntimeError):
                repository.execute(
                    self.create_envelope(key="create.command.0002"),
                    failing,
                )
            self.assertEqual(repository.snapshot_with_revision().revision, 0)
            self.assertEqual(
                repository._connection.execute("SELECT count(*) FROM paper_command_receipts").fetchone()[0],
                0,
            )

    def test_owner_scope_binding_and_guard_precede_receipt_disclosure(self) -> None:
        allowed = True

        def guarded(value: str | None) -> None:
            if not allowed or value != OWNER:
                raise RuntimeError("fixture rejection")

        with SQLitePaperRepository(
            self.path,
            owner_id=OWNER,
            portfolio_id=PORTFOLIO,
            owner_guard=guarded,
        ) as repository:
            repository.execute(self.create_envelope(), _create_portfolio)
            allowed = False
            with self.assertRaises(PaperPersistenceError) as denied:
                repository.execute(self.create_envelope(expected=999), _create_portfolio)
            self.assertEqual(denied.exception.code, "PAPER_OWNER_SCOPE_REJECTED")
        with self.assertRaises(PaperPersistenceError) as wrong_owner:
            SQLitePaperRepository(
                self.path,
                owner_id="other-owner",
                portfolio_id=PORTFOLIO,
                owner_guard=lambda value: None,
            )
        self.assertEqual(wrong_owner.exception.code, "PAPER_OWNER_MISMATCH")

    def test_mutation_guard_blocks_only_new_mutation(self) -> None:
        blocked = False
        guard_calls = 0

        def mutation_guard() -> None:
            nonlocal guard_calls
            guard_calls += 1
            if blocked:
                raise DomainValidationError("fixture disk latch")

        with self.repository(mutation_guard=mutation_guard) as repository:
            envelope = self.create_envelope()
            repository.execute(envelope, _create_portfolio)
            blocked = True
            replay = repository.execute(envelope, _create_portfolio)
            self.assertTrue(replay.historical_replay)
            self.assertEqual(guard_calls, 1)
            with self.assertRaises(PaperPersistenceError) as denied:
                repository.execute(
                    PaperCommandEnvelope(
                        OWNER,
                        PORTFOLIO,
                        "new.command.0002",
                        "SAVE_STRATEGY_DRAFT",
                        {"fixture": "new"},
                        1,
                    ),
                    lambda engine: engine.save_strategy_draft(
                        PaperStrategyVersion("strategy-v2", "hash-v2", NOW)
                    ),
                )
            self.assertEqual(denied.exception.code, "PAPER_MUTATION_BLOCKED")
            self.assertEqual(repository.snapshot_with_revision().revision, 1)

    def test_missing_projection_and_unsupported_schema_fail_closed(self) -> None:
        with self.repository() as repository:
            repository.execute(self.create_envelope(), _create_portfolio)
        connection = sqlite3.connect(self.path)
        connection.execute(
            "DELETE FROM paper_state_fields WHERE field_name='audits'"
        )
        connection.commit()
        connection.close()
        with self.assertRaises(PaperPersistenceError) as corrupt:
            self.repository()
        self.assertEqual(corrupt.exception.code, "PAPER_STATE_CORRUPT")

        other_path = self.directory / "unsupported.sqlite3"
        with SQLitePaperRepository(
            other_path,
            owner_id=OWNER,
            portfolio_id=PORTFOLIO,
            owner_guard=_owner_guard,
        ):
            pass
        connection = sqlite3.connect(other_path)
        connection.execute("UPDATE paper_schema_ledger SET schema_version=999")
        connection.commit()
        connection.close()
        with self.assertRaises(PaperPersistenceError) as unsupported:
            SQLitePaperRepository(
                other_path,
                owner_id=OWNER,
                portfolio_id=PORTFOLIO,
                owner_guard=_owner_guard,
            )
        self.assertEqual(unsupported.exception.code, "PAPER_SCHEMA_UNSUPPORTED")

    def test_guard_tables_reject_duplicate_claim_and_fill_rows(self) -> None:
        with self.repository() as repository:
            repository.execute(self.create_envelope(), _create_portfolio)
        connection = sqlite3.connect(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO paper_state_fields(owner_id,portfolio_id,field_name,field_order,payload_json,payload_hash,revision) "
                "SELECT owner_id,portfolio_id,field_name,field_order,payload_json,payload_hash,revision FROM paper_state_fields LIMIT 1"
            )
        connection.close()

    def test_database_wal_shm_permissions_and_external_reader_do_not_break_locks(self) -> None:
        with self.repository() as repository:
            repository.execute(self.create_envelope(), _create_portfolio)
            repository._connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{self.path}{suffix}")
                self.assertTrue(candidate.exists())
                if os.name != "nt":
                    self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('SELECT count(*) FROM paper_owner_bindings').fetchone(); c.close()",
                    str(self.path),
                ],
                check=True,
                env={key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "AMENT_PREFIX_PATH"}},
            )
            self.assertTrue(Path(f"{self.path}-wal").exists())
            self.assertTrue(Path(f"{self.path}-shm").exists())
            deleted = []
            if os.name != "nt" and Path("/proc/self/fd").exists():
                for descriptor in Path("/proc/self/fd").iterdir():
                    try:
                        target = os.readlink(descriptor)
                    except OSError:
                        continue
                    if str(self.path) in target and target.endswith(" (deleted)"):
                        deleted.append(target)
            self.assertEqual(deleted, [])
            os.chmod(self.path, 0o644)
            repository.snapshot()
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_symlink_hardlink_and_forbidden_path_are_rejected(self) -> None:
        target = self.directory / "target.sqlite3"
        target.write_bytes(b"")
        os.chmod(target, 0o600)
        symlink = self.directory / "symlink.sqlite3"
        symlink.symlink_to(target)
        with self.assertRaises(PaperPersistenceError) as symlink_error:
            SQLitePaperRepository(
                symlink,
                owner_id=OWNER,
                portfolio_id=PORTFOLIO,
                owner_guard=_owner_guard,
            )
        self.assertEqual(symlink_error.exception.code, "PAPER_PATH_UNSAFE")
        hardlink = self.directory / "hardlink.sqlite3"
        os.link(target, hardlink)
        with self.assertRaises(PaperPersistenceError) as hardlink_error:
            SQLitePaperRepository(
                hardlink,
                owner_id=OWNER,
                portfolio_id=PORTFOLIO,
                owner_guard=_owner_guard,
            )
        self.assertEqual(hardlink_error.exception.code, "PAPER_PATH_UNSAFE")
        fresh = self.directory / "fresh.sqlite3"
        with self.assertRaises(PaperPersistenceError) as forbidden_error:
            SQLitePaperRepository(
                fresh,
                owner_id=OWNER,
                portfolio_id=PORTFOLIO,
                owner_guard=_owner_guard,
                forbidden_paths=(fresh,),
            )
        self.assertEqual(forbidden_error.exception.code, "PAPER_PATH_UNSAFE")

    def test_multiprocess_same_request_is_committed_once(self) -> None:
        self.initialize_empty_database()
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        queue = context.Queue()
        processes = [
            context.Process(
                target=_multiprocess_same_receipt,
                args=(str(self.path), start, queue),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(20)
            self.assertEqual(process.exitcode, 0)
        results = sorted(queue.get(timeout=2) for _ in processes)
        self.assertEqual(results, [("ok", False, 1), ("ok", True, 1)])
        with self.repository() as repository:
            self.assertEqual(repository.snapshot_with_revision().revision, 1)
            self.assertEqual(
                repository._connection.execute("SELECT count(*) FROM paper_command_receipts").fetchone()[0],
                1,
            )

    def test_multiprocess_cas_allows_only_one_distinct_command(self) -> None:
        self.initialize_empty_database()
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        queue = context.Queue()
        processes = [
            context.Process(
                target=_multiprocess_stale_revision,
                args=(str(self.path), f"distinct.command.000{index}", start, queue),
            )
            for index in (1, 2)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(20)
            self.assertEqual(process.exitcode, 0)
        results = [queue.get(timeout=2) for _ in processes]
        self.assertEqual(sum(item[0] == "ok" for item in results), 1)
        self.assertIn(("error", "PAPER_REVISION_CONFLICT"), results)

    def test_sigkill_before_commit_rolls_back_and_after_commit_replays(self) -> None:
        for stage, expected_revision in (("before_commit", 0), ("after_commit", 1)):
            with self.subTest(stage=stage):
                path = self.directory / f"{stage}.sqlite3"
                with SQLitePaperRepository(
                    path,
                    owner_id=OWNER,
                    portfolio_id=PORTFOLIO,
                    owner_guard=_owner_guard,
                ):
                    pass
                context = multiprocessing.get_context("spawn")
                process = context.Process(target=_crash_worker, args=(str(path), stage))
                process.start()
                process.join(20)
                self.assertEqual(process.exitcode, -signal.SIGKILL)
                with SQLitePaperRepository(
                    path,
                    owner_id=OWNER,
                    portfolio_id=PORTFOLIO,
                    owner_guard=_owner_guard,
                ) as repository:
                    self.assertEqual(repository.snapshot_with_revision().revision, expected_revision)
                    integrity = repository._connection.execute("PRAGMA integrity_check").fetchone()[0]
                    self.assertEqual(integrity, "ok")
                    key = f"crash.{stage}.0001"
                    replay = repository.execute(
                        PaperCommandEnvelope(
                            OWNER,
                            PORTFOLIO,
                            key,
                            "CREATE_PORTFOLIO",
                            {"stage": stage},
                            0,
                        ),
                        _create_portfolio,
                    )
                    self.assertEqual(replay.result_revision, 1)
                    self.assertEqual(replay.historical_replay, stage == "after_commit")

    def test_enospc_fault_and_busy_are_safe_and_atomic(self) -> None:
        def full_fault(stage: str) -> None:
            if stage == "before_commit":
                raise OSError(28, "fixture sensitive path must not escape")

        with self.repository(fault_hook=full_fault) as repository:
            with self.assertRaises(PaperPersistenceError) as full:
                repository.execute(self.create_envelope(), _create_portfolio)
            self.assertEqual(full.exception.code, "PAPER_STORAGE_FULL")
            self.assertNotIn(str(self.path), str(full.exception))
            self.assertEqual(repository.snapshot_with_revision().revision, 0)
            self.assertEqual(
                repository._connection.execute("SELECT count(*) FROM paper_command_receipts").fetchone()[0],
                0,
            )

        with self.repository(busy_timeout_ms=50) as repository:
            blocker = sqlite3.connect(self.path, isolation_level=None)
            blocker.execute("PRAGMA busy_timeout=50")
            blocker.execute("BEGIN IMMEDIATE")
            try:
                with self.assertRaises(PaperPersistenceError) as busy:
                    repository.execute(self.create_envelope(), _create_portfolio)
                self.assertEqual(busy.exception.code, "PAPER_STORAGE_BUSY")
            finally:
                blocker.rollback()
                blocker.close()
            self.assertEqual(repository.snapshot_with_revision().revision, 0)

    def test_result_and_state_corruption_are_not_replayed(self) -> None:
        with self.repository() as repository:
            repository.execute(self.create_envelope(), _create_portfolio)
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE paper_command_receipts SET result_json='null' WHERE idempotency_key='create.command.0001'"
        )
        connection.commit()
        connection.close()
        # Corrupt receipts may be rejected during repository initialization,
        # before a command can be executed. Both paths must fail closed.
        with self.assertRaises(PaperPersistenceError) as corrupt:
            with self.repository() as repository:
                repository.execute(self.create_envelope(expected=999), _create_portfolio)
        self.assertEqual(corrupt.exception.code, "PAPER_RECONCILIATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
