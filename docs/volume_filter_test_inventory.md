# Volume Filter Test Inventory

Stage: W10. Created before W2.A.

The inventory was built with the commands required by
`docs/volume_filter_implementation_plan_tz_v5.txt`.

## Status Legend

- `PRESERVED`: keep existing ZigZag behavior and compatibility expectations.
- `UPDATE`: extend in later stages for the split ZigZag/Volume contract.
- `REMOVE`: remove after migration if it becomes obsolete.
- `NEW_VOLUME`: add new tests or coverage for Volume-only behavior.

## Discovery Contract

Mandatory tests must live in pytest-discovered paths:

- `wf_grid/tests`
- `donor TESTER/tests`

Slow performance smoke tests must be marked:

- `slow_perf`

Golden characterization and W3 100k performance smoke coverage are part of
the default discovered suite unless explicitly marked slow by a later stage.

## Command 1

```text
rg -n "TradeFilterZigZagConfig\(" -- wf_grid donor 'donor TESTER'
```

| Match | Status |
| --- | --- |
| `donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py:122` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py:346` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_determinism_normalized.py:50` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:53` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_close_only_invariance.py:54` | PRESERVED |
| `donor TESTER/tests/test_phase2_wp_t7_excel_export.py:152` | PRESERVED |
| `donor TESTER/tests/test_phase2_wp_t5_equal_blocks_gate.py:59` | PRESERVED |
| `donor TESTER/tests/test_phase2_wp_t4_runner_integration.py:90` | PRESERVED |
| `donor TESTER/tests/test_phase2_wp_t4_runner_integration.py:750` | PRESERVED |
| `donor TESTER/tests/test_phase2_wp_t4_cli_wiring.py:65` | PRESERVED |
| `donor TESTER/tests/test_phase2_time_filter.py:269` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_warmup_filter.py:44` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_trigger_reconstruction.py:51` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_no_post_filtering.py:45` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_trade_mode_narrowing.py:42` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_trade_diagnostics_attachment.py:64` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_stopping_semantics.py:42` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_indexing_open_to_open.py:45` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_global_stats_init_failure.py:42` | PRESERVED |
| `wf_grid/tests/test_daily_reset.py:271` | PRESERVED |
| `wf_grid/tests/test_parallel_execution.py:141` | PRESERVED |
| `wf_grid/tests/test_v3_9_regression.py:198` | PRESERVED |
| `wf_grid/tests/test_v3_9_regression.py:216` | PRESERVED |
| `wf_grid/tests/test_v3_9_regression.py:306` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:675` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:1251` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:1322` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:1332` | PRESERVED |
| `wf_grid/tests/test_pr5_schema_contract.py:901` | PRESERVED |
| `wf_grid/tests/test_pr5_schema_contract.py:1009` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:808` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:813` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:887` | PRESERVED |
| `wf_grid/tests/test_wp3_zigzag_global_stats.py:103` | PRESERVED |
| `donor/supertrend_optimizer/testing/fixtures.py:251` | PRESERVED |
| `donor/supertrend_optimizer/core/trade_filter_config.py:419` | UPDATE |

## Command 2

```text
rg -n "validate_trade_filter|_validate_trade_filter|trade_filter\.enabled" -- wf_grid donor 'donor TESTER'
```

| Match | Status |
| --- | --- |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:2` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:19` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:22` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:38` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:142` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:158` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:175` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:195` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:219` | PRESERVED |
| `donor TESTER/tests/test_phase2_caller_pipeline_whitelist.py:229` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_disabled_baseline.py:10` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_disabled_baseline.py:123` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_import_smoke.py:30` | PRESERVED |
| `donor TESTER/tests/test_wp_t2_load_tester_config.py:20` | PRESERVED |
| `donor TESTER/tests/test_wp_t2_load_tester_config.py:499` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_cli_backward_compat.py:11` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_cli_backward_compat.py:75` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_cli_backward_compat.py:86` | PRESERVED |
| `wf_grid/tests/test_daily_reset.py:333` | PRESERVED |
| `wf_grid/tests/test_daily_reset.py:418` | PRESERVED |
| `wf_grid/config/loader.py:293` | UPDATE |
| `wf_grid/config/loader.py:319` | UPDATE |
| `wf_grid/config/loader.py:555` | UPDATE |
| `wf_grid/config/loader.py:565` | UPDATE |
| `wf_grid/config/loader.py:569` | UPDATE |
| `wf_grid/config/loader.py:574` | UPDATE |
| `wf_grid/config/loader.py:581` | UPDATE |
| `wf_grid/config/loader.py:587` | UPDATE |
| `wf_grid/config/loader.py:596` | UPDATE |
| `wf_grid/config/loader.py:601` | UPDATE |
| `wf_grid/config/loader.py:602` | UPDATE |
| `wf_grid/config/loader.py:994` | UPDATE |
| `wf_grid/tests/test_parallel_execution.py:170` | PRESERVED |
| `wf_grid/tests/test_parallel_execution.py:994` | PRESERVED |
| `wf_grid/tests/test_parallel_execution.py:995` | PRESERVED |
| `wf_grid/tests/test_parallel_execution.py:999` | PRESERVED |
| `wf_grid/tests/test_parallel_execution.py:1010` | PRESERVED |
| `wf_grid/tests/test_parallel_execution.py:1011` | PRESERVED |
| `wf_grid/tests/test_parallel_execution.py:1032` | PRESERVED |
| `wf_grid/tests/test_parallel_execution.py:1033` | PRESERVED |
| `wf_grid/tests/test_w2_0_loader_phase_pipeline.py:110` | PRESERVED |
| `wf_grid/tests/test_w2_0_loader_phase_pipeline.py:152` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:28` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:115` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:216` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:272` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:296` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:299` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:309` | PRESERVED |
| `wf_grid/tests/test_time_filter.py:312` | PRESERVED |
| `wf_grid/tests/test_w1_lowercase_columns.py:184` | PRESERVED |
| `wf_grid/tests/test_w1_lowercase_columns.py:185` | PRESERVED |
| `wf_grid/tests/test_wp8_wf_oos_integration.py:177` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:122` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:133` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:138` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:222` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:1101` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:1127` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:1359` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:1368` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:1507` | PRESERVED |
| `wf_grid/tests/test_wp2_config_trade_filter.py:1521` | PRESERVED |
| `wf_grid/tests/test_wp10_finalization.py:296` | PRESERVED |
| `wf_grid/tests/test_wp10_finalization.py:582` | PRESERVED |
| `wf_grid/tests/test_wp11_rollback_hardening.py:12` | PRESERVED |
| `wf_grid/tests/test_wp11_rollback_hardening.py:24` | PRESERVED |
| `wf_grid/tests/test_wp11_rollback_hardening.py:161` | PRESERVED |
| `wf_grid/tests/test_wp11_rollback_hardening.py:626` | PRESERVED |
| `wf_grid/tests/test_wp11_rollback_hardening.py:647` | PRESERVED |
| `donor/supertrend_optimizer/cli/tester.py:49` | UPDATE |
| `donor/supertrend_optimizer/cli/tester.py:395` | UPDATE |
| `donor/supertrend_optimizer/cli/tester.py:422` | UPDATE |
| `donor/supertrend_optimizer/cli/tester.py:423` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:85` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:120` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:181` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:401` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:406` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:461` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:496` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:539` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:550` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:556` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:570` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:606` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:622` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:627` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:728` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:777` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:1039` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:1234` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:1258` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:1261` | UPDATE |
| `donor/supertrend_optimizer/core/trade_filter_config.py:1289` | UPDATE |
| `donor/supertrend_optimizer/core/zigzag_st_filter.py:2385` | PRESERVED |

## Command 3

```text
rg -n "zigzag_st_filter\.apply|from.*zigzag_st_filter.*apply" -- wf_grid donor 'donor TESTER'
```

| Match | Status |
| --- | --- |
| `donor/supertrend_optimizer/core/backtest.py:13` | PRESERVED |
| `donor/supertrend_optimizer/core/backtest.py:333` | PRESERVED |
| `donor/supertrend_optimizer/core/backtest.py:399` | PRESERVED |
| `wf_grid/tests/test_pr_exit_b_immediate_off.py:30` | PRESERVED |
| `wf_grid/tests/test_wp11_rollback_hardening.py:46` | PRESERVED |
| `wf_grid/tests/test_wp10_finalization.py:47` | PRESERVED |
| `donor TESTER/tests/test_phase2_exit_b_immediate_off.py:21` | PRESERVED |
| `donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py:32` | PRESERVED |

## Remove Candidates

No `REMOVE` matches in the W10 scan.

## New Volume Coverage To Add Later

| Area | Status |
| --- | --- |
| Volume-only schema and validation tests | NEW_VOLUME |
| Volume data validation tests | NEW_VOLUME |
| Volume metrics edge-case tests | NEW_VOLUME |
| Volume runtime integration tests for wf_grid and Tester | NEW_VOLUME |
| W3 100k performance smoke test | NEW_VOLUME |
