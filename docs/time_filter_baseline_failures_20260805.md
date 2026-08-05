# Resolved baseline failures for the Time filter rollout

Date: 2026-08-05

Command:

```text
python -m pytest -q -m "not slow" --tb=no
```

Final result after remediation:

```text
3688 passed, 3 skipped, 72 deselected
```

The previously classified set of 66 failures is fully resolved. The sections
below retain the historical classification and document the remediation scope.

## A. Current FINAM CSV read through obsolete direct pandas path — 5

The tests bypass `load_ohlc_csv()` and call `pd.read_csv(..., index_col=0)`
against the current separate `<DATE>`/`<TIME>` file. The resulting frame has
an invalid/non-datetime index or becomes empty after the legacy date filter.

FAILED wf_grid/tests/test_parallel_execution.py::TestPackDataRoundTrip::test_real_data_csv_smoke
FAILED wf_grid/tests/test_parallel_execution.py::TestWorkerSingleGridPointParity::test_worker_path_matches_sequential
FAILED wf_grid/tests/test_parallel_execution.py::TestWorkerSideImmutability::test_zigzag_stats_hardened_after_init_worker
FAILED wf_grid/tests/test_parallel_execution.py::TestWorkerLoggingViaStepResult::test_step_runtime_error_round_trips_through_worker_path
FAILED wf_grid/tests/test_parallel_execution.py::TestWorkerLoggingViaStepResult::test_step_error_survives_spawned_worker_pickle_boundary

## B. Annualization validation pre-empts the asserted validation — 2

The synthetic fixtures contain one active day while `periods_per_year` is
automatic. Resolution fails before the tests reach their intended
DatetimeIndex/duplicate-column assertions.

FAILED wf_grid/tests/test_orchestrator.py::TestOrchestratorDataValidation::test_range_index_strict_fails_via_orchestrator
FAILED wf_grid/tests/test_w1_lowercase_columns.py::test_wf_grid_volume_case_collision_raises_before_overwrite

## C. Walk-forward fixture sizes no longer form a valid window — 9

The small synthetic datasets and their train/test minimums are inconsistent,
so orchestration returns `No valid Walk-Forward windows could be generated`
before the asserted regression behavior.

FAILED wf_grid/tests/test_w8_wf_volume_integration.py::test_run_grid_pipeline_volume_sequential_and_parallel_summaries_match
FAILED wf_grid/tests/test_w9_regression_matrix.py::test_regression_matrix_sequential[baseline]
FAILED wf_grid/tests/test_w9_regression_matrix.py::test_regression_matrix_sequential[zigzag_only]
FAILED wf_grid/tests/test_w9_regression_matrix.py::test_regression_matrix_sequential[standalone_volume]
FAILED wf_grid/tests/test_w9_regression_matrix.py::test_regression_matrix_sequential[zigzag_plus_volume]
FAILED wf_grid/tests/test_w9_regression_matrix.py::test_regression_matrix_parallel[baseline]
FAILED wf_grid/tests/test_w9_regression_matrix.py::test_regression_matrix_parallel[zigzag_only]
FAILED wf_grid/tests/test_w9_regression_matrix.py::test_regression_matrix_parallel[standalone_volume]
FAILED wf_grid/tests/test_w9_regression_matrix.py::test_regression_matrix_parallel[zigzag_plus_volume]

## D. Pre-existing mojibake expectations in Excel tests — 50

The production exporter exposes valid Cyrillic column names. These tests
contain corrupted mojibake literals, so column/constant comparisons fail.
This is the pre-existing unrelated exporter/test-encoding defect explicitly
outside the Time feature scope.

FAILED wf_grid/tests/test_time_filter.py::TestExcelEndReasonTimeFilter::test_end_reason_time_filter_reset_when_no_daily_reset
FAILED wf_grid/tests/test_time_filter.py::TestExcelEndReasonTimeFilter::test_daily_reset_priority_over_time_filter_in_end_reason
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_cycle_sheet_constants_contract
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_mode_d_exit_c_close_position_off_bar_extends_only_leg_metrics
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_mode_d_exit_c_inside_active_segment_does_not_extend_leg_interval
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_mode_d_exit_c_raw_reason_coverage[ttl]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_mode_d_exit_c_raw_reason_coverage[no_fresh_candidate]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_mode_d_exit_c_raw_reason_coverage[cycle_trade_limit]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_mode_d_exit_c_raw_reason_coverage[cycle_take_profit]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_mode_d_resets_override_exit_c_and_prevent_off_bar_leg_extension[daily_reset_event-daily_reset]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_mode_d_resets_override_exit_c_and_prevent_off_bar_leg_extension[time_filter_reset_event-time_filter_reset]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_exit_c_attribution_guards_keep_legacy_fsm_off[non_d_guard]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_exit_c_attribution_guards_keep_legacy_fsm_off[legacy_without_reason]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_exit_c_multiple_reasons_fail_closed_to_fsm_off
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_exit_c_malformed_optional_diagnostics_disable_attribution[zigzag_mode0-wakeup_exit_reason0]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_exit_c_malformed_optional_diagnostics_disable_attribution[zigzag_mode1-wakeup_exit_reason1]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_exit_c_malformed_optional_diagnostics_disable_attribution[zigzag_mode2-local_median_stop]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_headers_contract_and_empty_builder_output
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_completed_cycles_skip_unfinished_tail
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_post_close_fsm_boundary_uses_off_bar_only_for_reason
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_scalar_guards_return_empty_and_do_not_call_zigzag_helpers[<lambda>0]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_scalar_guards_return_empty_and_do_not_call_zigzag_helpers[<lambda>1]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_scalar_guards_return_empty_and_do_not_call_zigzag_helpers[<lambda>2]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_scalar_guards_return_empty_and_do_not_call_zigzag_helpers[<lambda>3]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_scalar_guards_return_empty_and_do_not_call_zigzag_helpers[<lambda>4]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_direction_matrix_ignores_st_flip_fallback[candidate_threshold-1--1-+]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_direction_matrix_ignores_st_flip_fallback[both--1-1--]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_direction_matrix_ignores_st_flip_fallback[confirmed_median-1--1--]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_direction_matrix_ignores_st_flip_fallback[none-1--1-]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_direction_matrix_ignores_st_flip_fallback[unexpected--1-1-]
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_trades_mapping_uses_entry_signal_idx_and_skips_nan
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_wakeup_cycle_trade_count_matches_cycle_sheet_trade_count
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_positive_trades_percent_scale_and_non_finite_guard
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_cycle_final_result_is_nan_when_cycle_has_no_trades
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_cycle_final_result_nan_pnl_is_guarded
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_cycle_final_result_missing_pnl_column_is_nan
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_active_starts_at_zero_and_adjacent_cycles
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_confirmed_leg_membership_and_threshold_strictness
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_non_uniform_candidate_threshold_preserves_rows
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_empty_legs_metrics
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_percent_metrics_direction_variants
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_alignment_mismatch_uses_aligned_prefix
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_volume_cycle_alignment_uses_shortest_optional_arrays_without_index_errors
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_volume_cycle_volume_regime_nan_is_not_string_nan
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_daily_reset_inside_cycle_does_not_end_cycle
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_both_uses_candidate_direction_not_confirmed_leg
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_non_finite_ohlc_preserves_row_with_nan_metrics
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_acceptance_enabled_export_without_daily_reset_event_writes_empty_cycle
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_write_cycle_sheet_autofilter_and_datetime_format
FAILED donor TESTER/tests/test_xlsx_cycle_sheet.py::test_enabled_export_writes_cycle_final_result_numeric_to_xlsx

## Rollout conclusion

The complete non-slow regression suite is green. There is no accepted failure
baseline remaining; any future failure requires investigation and remediation
rather than a manifest update by default.
