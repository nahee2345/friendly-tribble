# QA Stage2 — isolated execution and legacy 24-failure triage (2026-11-04)

**Scope:** Ubuntu local Wealthy, before Windows/KIS. QA-owned branch based on tech PR #27 `1ed3b0a0fce742fad90816597b1c09dfc9c214e7`. No operating SQLite, keyring, broker, real money, or deployment.

## A. Independent QA execution package

- `tests/test_qa_stage2_independent_stop.py` is a QA-authored test, not a re-label of technical CI. Uses only pytest `tmp_path` disposable databases.
- Missing stop control, other-owner enabled, explicitly paused, and corrupted stop row: direct trade rejected; ledger/event/claim row counts unchanged.
- Explicit stop after a previous enable survives **a new Python interpreter process**, again with no writes. This is a subprocess restart test, **not** SIGKILL during a commit, WAL crash, or complete application restart.
- With explicit control enabled but NAV not verified, Stage4 returns `session_unverified` and produces no order/claim; readiness stays false.
- `.github/workflows/qa-stage2-independent-20261104.yml` runs the independent tests on Ubuntu 24.04/Python 3.12 and, in a separate step, existing technical session reservation/turnover tests.
- Evidence contract: pin commit SHA + workflow run URL, pytest summary, HTTP status (when applicable), before/after row counts, OS/Python/SQLite. **Until this new QA workflow has a completed run, independent PASS is NOT VERIFIED.** Testing a no-fill gate is not 20% three-order-path authorization.

## B. Previous full regression: actual 24 FAIL extracted from GitHub Actions

**Source:** https://github.com/nahee2345/friendly-tribble/actions/runs/40273285517 — job "Existing historical fixture acceptance - do not suppress failures", 2026-10-26. **85 PASS / 24 FAIL**. Classification is a *diagnostic hypothesis*, not a deletion/waiver of a failure.

| Component | Failed | Evidence from actual log | Classification / next isolation |
|---|---:|---|---|
| Stage5 accounting | 5 | Expected 4,000,000 → observed 4,350,000; expected 4,025,000 → 4,375,000; discrepancy exactly +350,000 | **Time-dependent monthly contribution strongly supported**; instrument 25th auto funding timestamp and freeze time *before imports*; confirm that initial 4m and 350k monthly remain separate legitimate entries |
| Stage7 release/audit | 8 | Audit returned `blocked` where cases expected `healthy` or `warning` | **Possible downstream dated-funding integrity block**, but individual audit issue codes must be captured; cannot assert all 8 are time-only |
| Stage4 portfolio | 10 | `integrity_blocked` and `INVALID_FUNDING_ALLOCATION`, missing candidate lists causing `IndexError`, no order where expected | **Likely cascade from failed audit/plan**; individual cases may also reveal actual product regression; capture audit issues per fixture |
| Stage6 risk | 1 | Empty candidate/order list → `IndexError` for risk snapshot recheck case | **Likely cascade from Stage4 block**; prove whether risk validation itself executes |

**24 failures categorized for investigation:** strong time-dependent symptoms **5**; audit/plan cascade candidates **19**; independently verified product defects **0 established by these logs**; independent environment failures **0 supported by the logs**. **This is NOT 5 proven-only-time + 19 exempt. Every failed assertion remains OPEN** until exact baseline/candidate fixed-clock comparison and current-clock safe behavior have evidence.

Breakdown by failed test names:

**Stage5 (5)**:
- `test_initial_cash_only_gross_nav_and_unknown_net_costs`
- `test_usd_purchase_fx_split_exact_and_reconcile_original_cash`
- `test_daily_nav_chart_uses_real_dated_funding_not_reversed_cash_flow`
- `test_daily_report_card_uses_verified_stage5_gross_only_not_settled_dividend`
- `test_fee_receipts_have_explicit_unposted_currency_and_native_totals`

**Stage7 (8)**:
- `test_default_funded_accounts_are_healthy_and_unpaused_is_not_assumed`
- `test_cross_account_funding_fx_buy_sale_and_sqlite_checks`
- `test_sqlite_readonly_audit_does_not_change_paper_table_counts`
- `test_claim_with_real_simulated_order_is_consistent`
- `test_unsigned_old_nav_produces_warning_not_false_corroboration`
- `test_corrupted_backup_fails_without_touching_active_paper_db`
- `test_user_isolation_no_cross_owner_orders_or_claims`
- `test_old_five_column_daily_nav_is_warning_and_audit_does_not_migrate`

**Stage4 (10)**:
- `test_one_owner_paper_execution_idempotent_and_within_cap`
- `test_independently_reverified_price_deviation_rejected_without_claim`
- `test_missing_fx_entire_global_plan_blocks_and_no_provider_call`
- `test_domestic_isa_routing_only_for_approved_etf`
- `test_stop_loss_review_and_sell_stops_same_sleeve_reentry`
- `test_allocations_obey_total_daily_cap_across_multiple_sleeves`
- `test_legacy_manual_buy_consume_same_day_portfolio_spending_budget`
- `test_policy_change_between_plan_and_fresh_quote_blocks_fill`
- `test_fresh_fx_deviation_blocks_usd_buy`
- `test_concurrent_external_buys_rechecked_against_symbol_concentration`

**Stage6 (1)**:
- `test_stage4_rechecks_risk_snapshot_hash_after_external_change`

## C. Follow-up acceptance matrix — executable upon integrated tech SHA

These tests MUST use a new candidate that unifies HTTP manual order, Stage4, USD FX+BUY behind a verified session NAV and ONE owner/session claim.

| ID | Input or fault injection | Pass criterion |
|---|---|---|
| CAP-20 | Fixture verified NAV 4m; BUY 300k + SELL 500k across paths/accounts; extra 1 KRW | Exactly 800k used; extra request rejected without DB delta; Stage4 separate 5% cap retained |
| APPROVE | 299999/300000/300001, 24h equality, quote or FX increases, approval type mismatch | Exact threshold/expiration, recheck approval in commit; no bypass |
| IDEMPOTENT | same owner/key same/different payload, second account reused key, lost HTTP response | one order/receipt; mismatch fails; no unrelated approval consumed |
| PARALLEL | 2 independent OS processes, conflicting writer, owner A/B separate, BUY+SELL cross-path race | SQLite serializes; combined cap never exceeded; no cross-user exposure |
| CRASH | child SIGKILL before reserve, after reserve/FX/order, before/after COMMIT | uncommitted effects all rollback; committed effects one receipt, UNKNOWN retained until reconciled |
| RECOVERY | restart, WAL/SHM, backup/restore, UNKNOWN/RESERVED/CLAIMED | no auto retry/cancel/duplicate and no missing or orphaned rows |
| STOP | missing/paused/null/bad control, 401/403, unverified/stale NAV or FX, market calendar uncertain | no order/FX/claim; visible UI no false permission |

At each stage, capture request/response, approval row, turnover sessions/claims, virtual_orders, stage1_events, stage4_execution_claims and receipts before/after. Do not mix synthetic 4m with live starting NAV. Prior Stage4 5% conservative cap stays; domestic session policy undecided means domestic auto order blocked. Test cases for the *new integrated interface* must be committed against that interface and run in fresh independent CI, not counted now.

## D. Current report status
- **Created** QA-owned test file + independent workflow + this failure triage.
- Tech PR #27 targeted Python 74 PASS, frontend 4 PASS; this does not count as independent QA.
- No unsupported production changes, no full 24-failure waiver; PR Draft and all PAPER auto trading/merge/deploy still blocked.
