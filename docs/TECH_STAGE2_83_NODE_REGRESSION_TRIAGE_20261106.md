# 기술팀 Stage2 기존 83개 시험 — node ID 단위 실패 원인 대조 (2026-11-06)

**공유 대상: 총괄팀 + QA팀** (디자인팀 참고). 이 문서는 자동 판정이 아닌 **실제 GitHub Actions의 83개 개별 testcase ID와 첫 예외**를 짝지은 초기 기술팀 분류입니다. 같은 항목의 두 날짜 실패는 합산하지 않습니다.

## 대조 고정 조건
- Ubuntu 24.04.5, Python 3.12, 동일 `pyproject.toml` blob `70013a9d29fd822aba1b61fa5df36b377cc808bc`, pytest/freezegun test-only clock, 격리 tmp SQLite.
- 고정 UTC: **2026-10-19T10:00:00Z** / **2026-11-06T10:00:00Z**. 4개의 Stage4~7 원본 파일에서 83개 testcase ID 유지. 수용 기대값·skip·테스트 목록은 변경하지 않음.
- 이전 기준 **`dc92c6021f1bbd9c71b44c4accdcf0b02c64dd22`**: **83/0**, **64/19**.
- PR #29 **`41295d54850c9fc6066c85a6578e0c9bbffe6f0f`**: **41/42**, **36/47**.
- PR #32 **`eac7d7388e20d8cbb7c06a4e1d2174fc281e6d78`**: **41/42**, **36/47**. PR29→PR32 기준 추가 실패 0.
- 기술팀 진단수정 **`c274566c60cbdc4ca35e9b2cba91592acc2d3bdc`**: **43/40**, **38/45** (두 개 실패 감소); **P0 안전성 대상 47 PASS**.
- 원문/재현: [동일조건 기준 6-job Run 42163535168](https://github.com/nahee2345/friendly-tribble/actions/runs/42163535168), [수정 후 9-job Run 42164106844](https://github.com/nahee2345/friendly-tribble/actions/runs/42164106844). 각 job에 testcase 첫 예외·전체 JUnit XML·pip freeze 아티팩트.
- `P`=통과, `F`=실패. 열 순서 각각 10/19, 11/06.

## root cause 요약
- **구형 fixture가 명시적 거래 허용 상태를 만들지 않음:** 첫 공통 예외 `PAPER_CONTROL_STATE_UNVERIFIED`인 31건. `stage1_portfolios.py`의 **`f56365a71325`**에서 writer 체크 강화. 원래 테스트가 실제 승인·NAV·허용을 준비하지 않은 부분은 **아직 새 계약 수용 아님**. 실거래 차단은 의도적, 저수준 별도 synthetic 허용 fixture를 명시적으로 구성해야 함.
- **실행 전 NAV/release 차단:** Stage4 `session_unverified` 5건(원래는 다른 실행/차단 이유 기대). `stage4_portfolio.py` **`a6ac6d8aaad4`**에서 우선 방어. 이 중 이미 원장 이상이 있다는 2건은 무결성 우선 상태가 가려지는 **진단 제품 결함**으로 확인하여 `ff97417e5ad4`, `957e16be3f9e`에서 **수정**, 이후 두 날짜 결과 실제 F→P.
- **빈 결과 연쇄 IndexError 5건:** 시험 자체가 `skipped[0]` 또는 `proposed_orders[0]` 등 빈 결과 인덱싱. 직접 제품 API가 IndexError를 발생했다고 아직 입증되지 않음. 소스의 preview/runner/status 경로를 분리해 추가 조사할 것.
- **원본 기준에서도 11/06에만 19건 FAIL:** `audit(now=2026-10-19)`처럼 과거 고정 감사시각과 새 이벤트 기록시각을 서로 다르게 생성, 25일 월납입·오프라인 종가 기준 시각과 겹칠 수 있음. `INVALID_FUNDING_EVENT` 등 첫 원장 이슈는 **시각 불일치의 직접 증거**이며 앱의 현재 시각 기록 정책을 변경하거나 증거 없는 +350,000원을 제거하지 않음.
- **의존성 증거:** 동일 pyproject 제약과 동시 GitHub Actions Ubuntu image. `pip-freeze.txt`를 아티팩트별 대조 가능하나 전체 transitive 의존성 버전의 완전 일치를 이 글에서 확정하지 않음.
- **승인/차단 6건(과거 QA18):** Stage4~7의 83개 node ID에 들어 있지 않음. 별도 `tests/test_qa_p0_api_ledger.py`에서 pre-NAV 체결 성공 계약을 기대; 신규 현행 401/403/409 no-write 수용시험과 분리 OPEN.

## 전체 node ID 정합표 (테스트 파일·함수 원본 이름 보존)

| 테스트 node ID | 기준 10/19·11/06 | PR29 10/19·11/06 | PR32 10/19·11/06 | 수정 후 10/19·11/06 | 첫 원인 분류 | 최초 변경 추적 |
|---|---|---|---|---|---|---|
| `tests.test_stage4_portfolio::test_allocations_obey_total_daily_cap_across_multiple_sleeves` | P/F | F/F | F/F | F/F | 운영 NAV gate와 구형 성공 계약 | `a6ac6d8aaad4` |
| `tests.test_stage4_portfolio::test_claimed_after_crash_is_not_replayed` | P/P | F/F | F/F | P/P | 제품 결함·무결성 차단 진단 가림 | `a6ac6d8aaad4` |
| `tests.test_stage4_portfolio::test_claimed_state_reports_manual_reconciliation_required` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage4_portfolio::test_concurrent_external_buys_rechecked_against_symbol_concentration` | P/F | F/F | F/F | F/F | 후속 IndexError·빈 결과 연쇄(미확정) | `a6ac6d8aaad4(후속, 확정 전)` |
| `tests.test_stage4_portfolio::test_corrupt_cached_history_prevents_quant_trade_signal` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage4_portfolio::test_default_pause_does_not_execute_planned_buy` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage4_portfolio::test_domestic_isa_routing_only_for_approved_etf` | P/F | F/F | F/F | F/F | 후보/정책 의존·미확정 | `dc92c602→41295d548 범위(미확정)` |
| `tests.test_stage4_portfolio::test_external_paper_emergency_pause_at_execution_boundary` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage4_portfolio::test_fresh_fx_deviation_blocks_usd_buy` | P/F | F/F | F/F | F/F | 후속 IndexError·빈 결과 연쇄(미확정) | `a6ac6d8aaad4(후속, 확정 전)` |
| `tests.test_stage4_portfolio::test_independently_reverified_price_deviation_rejected_without_claim` | P/F | F/F | F/F | F/F | 후속 IndexError·빈 결과 연쇄(미확정) | `a6ac6d8aaad4(후속, 확정 전)` |
| `tests.test_stage4_portfolio::test_legacy_manual_buy_consume_same_day_portfolio_spending_budget` | P/F | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage4_portfolio::test_missing_fx_entire_global_plan_blocks_and_no_provider_call` | P/F | F/F | F/F | F/F | 운영 NAV gate와 구형 성공 계약 | `a6ac6d8aaad4` |
| `tests.test_stage4_portfolio::test_missing_held_security_price_blocks_buy_until_valued` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage4_portfolio::test_one_owner_paper_execution_idempotent_and_within_cap` | P/F | F/F | F/F | F/F | 운영 NAV gate와 구형 성공 계약 | `a6ac6d8aaad4` |
| `tests.test_stage4_portfolio::test_policy_change_between_plan_and_fresh_quote_blocks_fill` | P/F | F/F | F/F | F/F | 후속 IndexError·빈 결과 연쇄(미확정) | `a6ac6d8aaad4(후속, 확정 전)` |
| `tests.test_stage4_portfolio::test_sell_notional_above_300k_requires_approval_not_auto_fill` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage4_portfolio::test_stop_loss_review_and_sell_stops_same_sleeve_reentry` | P/F | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage4_portfolio::test_unknown_held_security_prevents_guessing_market_exposure` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage4_portfolio::test_weekend_blocks_execution_even_if_friday_reference_is_fresh` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_cash_only_and_foreign_only_do_not_claim_isa_tax_savings` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_concurrent_cash_event_invalidates_daily_nav_capture` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_corporate_event_even_marked_reconciled_stops_unadjusted_inventory` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_daily_nav_chart_uses_real_dated_funding_not_reversed_cash_flow` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_daily_nav_snapshot_idempotent_owner_scoped_and_no_backfill` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_daily_report_card_uses_verified_stage5_gross_only_not_settled_dividend` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_dividend_without_pre_exdate_holdings_is_not_counted` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_fee_receipts_have_explicit_unposted_currency_and_native_totals` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_initial_cash_only_gross_nav_and_unknown_net_costs` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_invalid_simulated_sell_inventory_fails_closed` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_isa_comparison_does_not_misclassify_domestic_equity_etf_gain` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_isa_scenario_domestic_foreign_listed_exclusion_and_allowance` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_missing_held_quote_makes_nav_and_xirr_unavailable` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_mutated_daily_nav_is_detected_and_twr_not_claimed` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_owner_isolation_rejects_other_owner_and_never_moves_cash` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_partial_exit_moving_average_realized_native` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_realized_usd_fx_split_from_both_proven_acquisition_and_exit_rates` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_realized_usd_sale_missing_fx_source_does_not_fabricate_conversion` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_recorded_paper_fee_does_not_silently_reduce_cash` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_sale_year_overseas_gain_comparison_only_with_known_trade_fx` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_twr_only_links_verified_flow_free_nav_intervals` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage5_accounting::test_unconfirmed_dividend_exdate_belongs_to_pre_exdate_holder_only` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_usd_manual_purchases_without_fx_are_never_revalued_at_today_buy_rate` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_usd_purchase_fx_split_exact_and_reconcile_original_cash` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage5_accounting::test_xirr_protects_same_day_annualization_and_deposit_timing` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_30_session_signal_only_uses_prior_closes_no_lookahead` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_correlated_risk_notification_cites_both_prices_and_deduplicates` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage6_risk::test_cross_currency_portfolio_refuses_historical_krw_volatility` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage6_risk::test_full_eight_strategy_backtest_is_explicitly_unavailable` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_incorrect_duplicate_dates_are_not_accepted_by_market_store` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_index_overlap_warning_is_not_fictitious_constituent_percentage` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage6_risk::test_korean_history_mismatched_meta_fails_without_fabricating` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_korean_one_year_collector_has_sufficient_65_daily_closes` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_large_observed_drawdown_vetoes_new_auto_buy` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_missing_or_bad_sha_history_rejects_candidate_but_not_cash` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_review_does_not_write_orders_or_change_owner_portfolio` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_risk_engine_receives_no_broker_or_network_dependency` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_risk_low_volatility_returns_are_source_backed` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_rule_has_no_fitted_weights_and_no_realized_nav_claim` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_same_currency_pairwise_return_correlation` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage6_risk::test_source_based_high_risk_alert_deduplicates_and_is_owner_scoped` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage6_risk::test_stage4_candidate_risk_veto_is_enforced_when_quant_signal_exists` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_stage4_rechecks_risk_snapshot_hash_after_external_change` | P/F | F/F | F/F | F/F | 후속 IndexError·빈 결과 연쇄(미확정) | `a6ac6d8aaad4(후속, 확정 전)` |
| `tests.test_stage6_risk::test_stale_verified_reference_fails_closed` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage6_risk::test_stress_is_scenario_only_and_requires_held_quote` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage7_release::test_audit_owner_ledger_limit_is_bounded_to_50000_rows` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage7_release::test_backup_can_be_verified_without_restore_source_overwrite` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage7_release::test_backup_path_traversal_symlink_and_file_permissions_blocked` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage7_release::test_claim_with_real_simulated_order_is_consistent` | P/F | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage7_release::test_corrupted_backup_fails_without_touching_active_paper_db` | P/F | P/F | P/F | P/F | 시계/월납입·고정 감사시각 | `초기 fixture; 변경 SHA 별도 확인` |
| `tests.test_stage7_release::test_cross_account_funding_fx_buy_sale_and_sqlite_checks` | P/F | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage7_release::test_daily_nav_snapshot_sha_mutation_blocks_new_investments` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage7_release::test_default_funded_accounts_are_healthy_and_unpaused_is_not_assumed` | P/F | P/F | P/F | P/F | 시계/월납입·고정 감사시각 | `초기 fixture; 변경 SHA 별도 확인` |
| `tests.test_stage7_release::test_executed_claim_without_real_order_is_not_trusted` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage7_release::test_invalid_timestamp_and_nonfinite_order_value_are_detected` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage7_release::test_negative_position_from_tampered_order_is_blocked` | P/P | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |
| `tests.test_stage7_release::test_old_five_column_daily_nav_is_warning_and_audit_does_not_migrate` | P/F | P/F | P/F | P/F | 시계/월납입·고정 감사시각 | `초기 fixture; 변경 SHA 별도 확인` |
| `tests.test_stage7_release::test_orphan_trade_metadata_blocks_simulated_buy` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage7_release::test_overspent_virtual_account_cash_blocks_unsafe_auto_restarts` | P/P | F/F | F/F | P/P | 제품 결함·무결성 차단 진단 가림 | `a6ac6d8aaad4` |
| `tests.test_stage7_release::test_sqlite_readonly_audit_does_not_change_paper_table_counts` | P/F | P/F | P/F | P/F | 시계/월납입·고정 감사시각 | `초기 fixture; 변경 SHA 별도 확인` |
| `tests.test_stage7_release::test_stopped_worker_wait_timeout_does_not_create_second_scheduler` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage7_release::test_unreconciled_stage4_claim_blocks_replay_and_does_not_auto_fix` | P/P | P/P | P/P | P/P | 일관 통과 | `해당 없음` |
| `tests.test_stage7_release::test_unsigned_old_nav_produces_warning_not_false_corroboration` | P/F | P/F | P/F | P/F | 시계/월납입·고정 감사시각 | `초기 fixture; 변경 SHA 별도 확인` |
| `tests.test_stage7_release::test_user_isolation_no_cross_owner_orders_or_claims` | P/F | F/F | F/F | F/F | 구형 fixture·명시적 해제 누락 | `f56365a71325` |

## 최초 오류/증거가 있는 실패 node ID

다음 표는 원본 JUnit의 **해당 testcase 자체 첫 예외 요약**입니다. 위 정합표의 P/F 분류와 함께 사용하며 하위 연쇄 실패의 근본 원인으로 단정하지 않습니다.

| 테스트 node ID | PR29 10/19 첫 예외 또는 PR29 11/06 첫 예외 | 현재 판정 |
|---|---|---|
| `tests.test_stage4_portfolio::test_allocations_obey_total_daily_cap_across_multiple_sleeves` | AssertionError: {'status': 'session_unverified', 'reason': '검증된 미국 세션 시작 자산·시세·환율 증거와 통합 20% 거래한도 원자 연결이 없어 PAPER 주문을 보류합니다.', 'executed': [], 'skipped': [], ...} assert  | 미종결 |
| `tests.test_stage4_portfolio::test_claimed_after_crash_is_not_replayed` | AssertionError: assert 'session_unverified' == 'integrity_blocked'      - integrity_blocked   + session_unverified | 미종결 (diagnostic fix P 확인) |
| `tests.test_stage4_portfolio::test_concurrent_external_buys_rechecked_against_symbol_concentration` | IndexError: list index out of range | 미종결 |
| `tests.test_stage4_portfolio::test_domestic_isa_routing_only_for_approved_etf` | assert 0 == 1  +  where 0 = len([]) | 미종결 |
| `tests.test_stage4_portfolio::test_fresh_fx_deviation_blocks_usd_buy` | IndexError: list index out of range | 미종결 |
| `tests.test_stage4_portfolio::test_independently_reverified_price_deviation_rejected_without_claim` | IndexError: list index out of range | 미종결 |
| `tests.test_stage4_portfolio::test_legacy_manual_buy_consume_same_day_portfolio_spending_budget` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage4_portfolio::test_missing_fx_entire_global_plan_blocks_and_no_provider_call` | AssertionError: assert 'session_unverified' == 'blocked'      - blocked   + session_unverified | 미종결 |
| `tests.test_stage4_portfolio::test_missing_held_security_price_blocks_buy_until_valued` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage4_portfolio::test_one_owner_paper_execution_idempotent_and_within_cap` | AssertionError: assert 'session_unverified' == 'completed'      - completed   + session_unverified | 미종결 |
| `tests.test_stage4_portfolio::test_policy_change_between_plan_and_fresh_quote_blocks_fill` | IndexError: list index out of range | 미종결 |
| `tests.test_stage4_portfolio::test_sell_notional_above_300k_requires_approval_not_auto_fill` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage4_portfolio::test_stop_loss_review_and_sell_stops_same_sleeve_reentry` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage4_portfolio::test_unknown_held_security_prevents_guessing_market_exposure` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_cash_only_and_foreign_only_do_not_claim_isa_tax_savings` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_corporate_event_even_marked_reconciled_stops_unadjusted_inventory` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_dividend_without_pre_exdate_holdings_is_not_counted` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_fee_receipts_have_explicit_unposted_currency_and_native_totals` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_isa_comparison_does_not_misclassify_domestic_equity_etf_gain` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_missing_held_quote_makes_nav_and_xirr_unavailable` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_owner_isolation_rejects_other_owner_and_never_moves_cash` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_partial_exit_moving_average_realized_native` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_realized_usd_fx_split_from_both_proven_acquisition_and_exit_rates` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_realized_usd_sale_missing_fx_source_does_not_fabricate_conversion` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_recorded_paper_fee_does_not_silently_reduce_cash` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_sale_year_overseas_gain_comparison_only_with_known_trade_fx` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_unconfirmed_dividend_exdate_belongs_to_pre_exdate_holder_only` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_usd_manual_purchases_without_fx_are_never_revalued_at_today_buy_rate` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage5_accounting::test_usd_purchase_fx_split_exact_and_reconcile_original_cash` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage6_risk::test_correlated_risk_notification_cites_both_prices_and_deduplicates` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage6_risk::test_cross_currency_portfolio_refuses_historical_krw_volatility` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage6_risk::test_index_overlap_warning_is_not_fictitious_constituent_percentage` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage6_risk::test_same_currency_pairwise_return_correlation` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage6_risk::test_source_based_high_risk_alert_deduplicates_and_is_owner_scoped` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage6_risk::test_stage4_rechecks_risk_snapshot_hash_after_external_change` | IndexError: list index out of range | 미종결 |
| `tests.test_stage6_risk::test_stress_is_scenario_only_and_requires_held_quote` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage7_release::test_claim_with_real_simulated_order_is_consistent` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage7_release::test_corrupted_backup_fails_without_touching_active_paper_db` | AssertionError: assert 'blocked' == 'healthy'      - healthy   + blocked | 미종결 |
| `tests.test_stage7_release::test_cross_account_funding_fx_buy_sale_and_sqlite_checks` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage7_release::test_default_funded_accounts_are_healthy_and_unpaused_is_not_assumed` | AssertionError: {'status': 'blocked', 'engine_version': 'stage7-readonly-audit-v1', 'read_only': True, 'repair_performed': False, ...} assert 'blocked' == 'healthy'       | 미종결 |
| `tests.test_stage7_release::test_invalid_timestamp_and_nonfinite_order_value_are_detected` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage7_release::test_negative_position_from_tampered_order_is_blocked` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |
| `tests.test_stage7_release::test_old_five_column_daily_nav_is_warning_and_audit_does_not_migrate` | AssertionError: {'status': 'blocked', 'engine_version': 'stage7-readonly-audit-v1', 'read_only': True, 'repair_performed': False, ...} assert 'blocked' == 'warning'       | 미종결 |
| `tests.test_stage7_release::test_overspent_virtual_account_cash_blocks_unsafe_auto_restarts` | AssertionError: assert 'session_unverified' == 'integrity_blocked'      - integrity_blocked   + session_unverified | 미종결 (diagnostic fix P 확인) |
| `tests.test_stage7_release::test_sqlite_readonly_audit_does_not_change_paper_table_counts` | AssertionError: assert 'blocked' == 'healthy'      - healthy   + blocked | 미종결 |
| `tests.test_stage7_release::test_unsigned_old_nav_produces_warning_not_false_corroboration` | AssertionError: assert 'blocked' == 'warning'      - warning   + blocked | 미종결 |
| `tests.test_stage7_release::test_user_isolation_no_cross_owner_orders_or_claims` | ValueError: 긴급 중지: PAPER_CONTROL_STATE_UNVERIFIED | 미종결 |

## 실패 대응 우선순위와 담당

1. **기술팀**: 실제 손상/중복 체결·승인·한도/인증 결함(최상위), Stage4 상태 가림 2건 수정 완료. 확인된 결과가 P/F인지와 테스트 범위가 코드 테스트인지 E2E인지 엄격히 구분.
2. **기술팀 + QA팀**: 나머지 31건 pause fixture는 원본 assertion을 수정하지 않고 **별도 격리 테스트 환경에 명시적 허용과 정확한 synthetic 출처·승인**을 만든 뒤 독립 검증. `readiness` 글로벌 OFF는 사용하지 않음.
3. **기술팀**: 시각 19건 월납입 25일 경계·audit `now`과 event `created_utc` 동기화로 비교, 프로덕션 timestamp 미변경.
4. **QA팀**: 동일 SHA 비교/초기 fixture 수정에 대한 독립 평가. `IndexError`는 API 반환 시 raw 예외 여부 확인, 미검증 source는 강제 409/no-write 유지.
5. **총괄팀**: 구형 성공 기대 6건/Stage4 런타임 차단 정책의 재수용 여부 결정(근거 없는 기존 테스트 녹색화 불가).

**상태:** Stage2 전체 NOT PASSED. 83개 검사의 두 날짜 경로에 아직 40/45 FAIL, 외부 실제 시세/FX NAV 증거·독립 전체 QA·실제 인증 Chrome 미완료. 자동 PAPER OFF, `readiness.verified=false`, Draft, main/운영 DB/실주문 미변경.

## 후속: 안전정책에 맞춰 test-only PAPER fixture 명시적으로 허용 — 2026-11-06

**같은 원본 83개 test node ID·원본 assert 문자열·skip 없이** Stage4, Stage5, Stage6, Stage7의 오프라인 synthetic 거래 fixture에 `set_paused(owner,p,False,"isolated ... test-only opt-in")`를 추가했습니다. **실제 운영 readiness=false·PAPER OFF는 유지**하며, 배포 환경 DB나 시장 증거는 절대 변경하지 않았습니다. 의도된 default pause 시험 및 무결성·임계값 assertion은 수정하지 않았습니다.

- 제품 상태 진단 우선순위 수정만 반영한 `c274566c...`: 10/19 **43 PASS / 40 FAIL**, 11/06 **38 PASS / 45 FAIL**.
- fixture 명시적 허용 반영한 코드 `c999075bba5dfb70d8d6f85ea3d1804a5355aaa0`: 10/19 **72 PASS / 11 FAIL**, 11/06 **64 PASS / 19 FAIL**. 이전 코드/fixture와 동일하게 비교해서 제품 코드가 모두 고쳐졌다는 뜻은 아닙니다.
- 안전 차단 대상 재검증: **47 PASS**, 상태 `readiness.verified=false` 유지.
- [후속 CI Run 42164537736](https://github.com/nahee2345/friendly-tribble/actions/runs/42164537736): 기준/PR29/PR32는 **고정 원본 테스트**, triage-current만 **fixture가 명시적으로 허용된 수정 버전**임을 구분해 해석해야 합니다. 미완료 FAIL은 계속 빨간색 유지.

### 현행 fixture에서 여전히 실패한 19개 고유 node ID
`F`=FAIL, `P`=PASS. **두 시간의 F를 더하지 않습니다.**

| Node ID | 10/19 | 11/06 | 첫 장애 / 분류 |
|---|---|---|---|
| `tests.test_stage4_portfolio::test_allocations_obey_total_daily_cap_across_multiple_sleeves` | F | F | 검증 전 거래 성공을 가정한 Stage4 런타임 계약 또는 시간 불일치 |
| `tests.test_stage4_portfolio::test_concurrent_external_buys_rechecked_against_symbol_concentration` | F | F | planner/runner 초기 차단의 빈 skipped/order 추종(연쇄, API raw 예외인지 별도 확인) |
| `tests.test_stage4_portfolio::test_domestic_isa_routing_only_for_approved_etf` | F | F | 후속 정책 상태/계획 후보 미검증 |
| `tests.test_stage4_portfolio::test_fresh_fx_deviation_blocks_usd_buy` | F | F | planner/runner 초기 차단의 빈 skipped/order 추종(연쇄, API raw 예외인지 별도 확인) |
| `tests.test_stage4_portfolio::test_independently_reverified_price_deviation_rejected_without_claim` | F | F | planner/runner 초기 차단의 빈 skipped/order 추종(연쇄, API raw 예외인지 별도 확인) |
| `tests.test_stage4_portfolio::test_legacy_manual_buy_consume_same_day_portfolio_spending_budget` | F | F | 후속 정책 상태/계획 후보 미검증 |
| `tests.test_stage4_portfolio::test_missing_fx_entire_global_plan_blocks_and_no_provider_call` | F | F | 검증 전 거래 성공을 가정한 Stage4 런타임 계약 또는 시간 불일치 |
| `tests.test_stage4_portfolio::test_one_owner_paper_execution_idempotent_and_within_cap` | F | F | 검증 전 거래 성공을 가정한 Stage4 런타임 계약 또는 시간 불일치 |
| `tests.test_stage4_portfolio::test_policy_change_between_plan_and_fresh_quote_blocks_fill` | F | F | planner/runner 초기 차단의 빈 skipped/order 추종(연쇄, API raw 예외인지 별도 확인) |
| `tests.test_stage4_portfolio::test_stop_loss_review_and_sell_stops_same_sleeve_reentry` | F | F | 후속 정책 상태/계획 후보 미검증 |
| `tests.test_stage6_risk::test_stage4_rechecks_risk_snapshot_hash_after_external_change` | F | F | planner/runner 초기 차단의 빈 skipped/order 추종(연쇄, API raw 예외인지 별도 확인) |
| `tests.test_stage7_release::test_claim_with_real_simulated_order_is_consistent` | P | F | 원본 감사 NOW(10/19) 대 새로운 원장 created_utc(11/06) 시각 불일치 |
| `tests.test_stage7_release::test_corrupted_backup_fails_without_touching_active_paper_db` | P | F | 원본 감사 NOW(10/19) 대 새로운 원장 created_utc(11/06) 시각 불일치 |
| `tests.test_stage7_release::test_cross_account_funding_fx_buy_sale_and_sqlite_checks` | P | F | 원본 감사 NOW(10/19) 대 새로운 원장 created_utc(11/06) 시각 불일치 |
| `tests.test_stage7_release::test_default_funded_accounts_are_healthy_and_unpaused_is_not_assumed` | P | F | 원본 감사 NOW(10/19) 대 새로운 원장 created_utc(11/06) 시각 불일치 |
| `tests.test_stage7_release::test_old_five_column_daily_nav_is_warning_and_audit_does_not_migrate` | P | F | 원본 감사 NOW(10/19) 대 새로운 원장 created_utc(11/06) 시각 불일치 |
| `tests.test_stage7_release::test_sqlite_readonly_audit_does_not_change_paper_table_counts` | P | F | 원본 감사 NOW(10/19) 대 새로운 원장 created_utc(11/06) 시각 불일치 |
| `tests.test_stage7_release::test_unsigned_old_nav_produces_warning_not_false_corroboration` | P | F | 원본 감사 NOW(10/19) 대 새로운 원장 created_utc(11/06) 시각 불일치 |
| `tests.test_stage7_release::test_user_isolation_no_cross_owner_orders_or_claims` | P | F | 원본 감사 NOW(10/19) 대 새로운 원장 created_utc(11/06) 시각 불일치 |

**개선된 node ID 29개:** 아래는 동일 testcase ID에 대해 fixture 명시적 허용 전 F에서 허용 후 P로 바뀐 항목이며, 기대값 삭제/skip/완화가 없습니다.

- `tests.test_stage4_portfolio::test_missing_held_security_price_blocks_buy_until_valued`
- `tests.test_stage4_portfolio::test_sell_notional_above_300k_requires_approval_not_auto_fill`
- `tests.test_stage4_portfolio::test_unknown_held_security_prevents_guessing_market_exposure`
- `tests.test_stage5_accounting::test_cash_only_and_foreign_only_do_not_claim_isa_tax_savings`
- `tests.test_stage5_accounting::test_corporate_event_even_marked_reconciled_stops_unadjusted_inventory`
- `tests.test_stage5_accounting::test_dividend_without_pre_exdate_holdings_is_not_counted`
- `tests.test_stage5_accounting::test_fee_receipts_have_explicit_unposted_currency_and_native_totals`
- `tests.test_stage5_accounting::test_isa_comparison_does_not_misclassify_domestic_equity_etf_gain`
- `tests.test_stage5_accounting::test_missing_held_quote_makes_nav_and_xirr_unavailable`
- `tests.test_stage5_accounting::test_owner_isolation_rejects_other_owner_and_never_moves_cash`
- `tests.test_stage5_accounting::test_partial_exit_moving_average_realized_native`
- `tests.test_stage5_accounting::test_realized_usd_fx_split_from_both_proven_acquisition_and_exit_rates`
- `tests.test_stage5_accounting::test_realized_usd_sale_missing_fx_source_does_not_fabricate_conversion`
- `tests.test_stage5_accounting::test_recorded_paper_fee_does_not_silently_reduce_cash`
- `tests.test_stage5_accounting::test_sale_year_overseas_gain_comparison_only_with_known_trade_fx`
- `tests.test_stage5_accounting::test_unconfirmed_dividend_exdate_belongs_to_pre_exdate_holder_only`
- `tests.test_stage5_accounting::test_usd_manual_purchases_without_fx_are_never_revalued_at_today_buy_rate`
- `tests.test_stage5_accounting::test_usd_purchase_fx_split_exact_and_reconcile_original_cash`
- `tests.test_stage6_risk::test_correlated_risk_notification_cites_both_prices_and_deduplicates`
- `tests.test_stage6_risk::test_cross_currency_portfolio_refuses_historical_krw_volatility`
- `tests.test_stage6_risk::test_index_overlap_warning_is_not_fictitious_constituent_percentage`
- `tests.test_stage6_risk::test_same_currency_pairwise_return_correlation`
- `tests.test_stage6_risk::test_source_based_high_risk_alert_deduplicates_and_is_owner_scoped`
- `tests.test_stage6_risk::test_stress_is_scenario_only_and_requires_held_quote`
- `tests.test_stage7_release::test_claim_with_real_simulated_order_is_consistent`
- `tests.test_stage7_release::test_cross_account_funding_fx_buy_sale_and_sqlite_checks`
- `tests.test_stage7_release::test_invalid_timestamp_and_nonfinite_order_value_are_detected`
- `tests.test_stage7_release::test_negative_position_from_tampered_order_is_blocked`
- `tests.test_stage7_release::test_user_isolation_no_cross_owner_orders_or_claims`

**미종결/인계:** Stage4 검증 NAV 없이 정상 체결 기대 계약은 정책상 허용 불가. 외부 시세·환율 source 및 로그인 HTTP→Stage4 E2E와 구형 6건 성공 기대를 QA 독립적으로 수용할지 결정해야 합니다. 11/06 역사적 NOW/created_utc 시차는 수신 시각 조작이나 테스트 무효화로 덮지 않고, 동일 시각의 안전한 synthetic fixture/달력 경계 케이스를 추가해 확인합니다.
