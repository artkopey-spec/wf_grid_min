# W9 Regression Matrix

Release artifact for the volume-filter integration regression matrix.

## Matrix

| Mode              | Sequential          | Parallel            | Tester legacy       | Tester equal_blocks |
|-------------------|---------------------|---------------------|---------------------|---------------------|
| baseline          | PASS: bit-identical | PASS: bit-identical | PASS: bit-identical | PASS: OK            |
| zigzag-only       | PASS: golden diff   | PASS: golden diff   | PASS: golden diff   | PASS: ConfigError   |
| standalone-volume | PASS: new fixture   | PASS: new fixture   | PASS: new fixture   | PASS: ConfigError   |
| zigzag+volume     | PASS: new fixture   | PASS: new fixture   | PASS: new fixture   | PASS: ConfigError   |

Notes:

- Baseline WF path is checked against direct no-filter and disabled-filter arrays.
- ZigZag-only is checked against W4.1 golden snapshots.
- Standalone-volume and ZigZag+volume verify volume columns, volume snapshots, and sequential/parallel summary parity.
- F9 production CSV snapshot is marked `recommended` in W4.1 and remains
  intentionally deferred until an anonymized production slice is available.

## Test Runs

| Command | Result |
|---------|--------|
| `python -m pytest wf_grid/tests/test_w5_standalone_volume.py wf_grid/tests/test_w6_diagnostics_snapshot.py wf_grid/tests/test_pr5_schema_contract.py wf_grid/tests/test_w9_regression_matrix.py -q` | PASS: 79 passed |
| `python -m pytest "donor TESTER/tests/test_w7_tester_volume_integration.py" "donor TESTER/tests/test_phase2_wp_t6_signal_events_filter.py" wf_grid/tests/test_w2b_volume_config.py -q` | PASS: 52 passed |
| `python -m pytest wf_grid/tests/test_wp1_baseline_capture.py -q` | PASS: 1 passed after refreshing `tests/baseline/baseline_v0.json` |
| `python -m pytest -q` | PASS: 3196 passed, 4 skipped |

## W9 Invariants

- PASS: no volume columns when volume is disabled.
- PASS: no volume snapshot when volume is disabled.
- PASS: no ZigZag-specific columns in standalone-volume.
- PASS: `filter_config_snapshot` survives sequential and parallel paths.
- PASS: parallel and sequential volume summaries match.
- PASS: volume counters are non-negative and contain no NaN values.
- PASS: existing ZigZag suite remains green in the full WF default run.
- PASS: mandatory W4.1 golden and W3 volume smoke tests are discovered by pytest.

## Acceptance Checklist

- PASS: v4 remains untouched for history.
- PASS: `trade_filter.enabled` no longer controls ZigZag dispatch directly.
- PASS: runtime helpers use strict post-validation semantics.
- PASS: resolvers preserve malformed raw values for validation.
- PASS: `trade_filter.type` is legacy ZigZag marker only.
- PASS: W2.C2 is documented as a breaking migration.
- PASS: loader follows phase order in wf_grid and tester.
- PASS: config loader does not validate volume data.
- PASS: baseline/no-filter path is bit-identical after refreshing the baseline fingerprint.
- PASS: ZigZag-only path is bit-identical to current ZigZag runtime.
- PASS: standalone volume does not build/use ZigZag stats.
- PASS: ZigZag+volume gates only lifecycle starts from OFF.
- PASS: volume params do not enter grid search or `grid_point_id`.
- PASS: `VolumeRuntime` arrays use TZ-aligned names without `_code`.
- PASS: `VolumeRuntime` categorical arrays are int8.
- PASS: `filter_diagnostics["volume_*"]` categorical arrays are strings.
- PASS: `VolumeRuntime` per-bar arrays are read-only.
- PASS: `VolumeRuntime.slice()` uses views and passes memory-sharing tests.
- PASS: W4.1 golden snapshots run in default pytest discovery.
- PASS: W3 100k performance smoke runs in default pytest discovery.
- PASS: slow perf tests are marked `slow_perf`.
- PASS: per-bar diagnostics are retained in wf_grid only behind `export.retain_per_bar_filter_diagnostics`.
- PASS: strip preserves `filter_diagnostics_summary` and `filter_config_snapshot`.
- PASS: export/fingerprint/collectors do not leak inactive subfilter fields.
- PASS: generic trade diagnostics helper supports ZigZag, standalone-volume and ZigZag+volume.
- PASS: tester standalone-volume path does not require ZigZag diagnostics.
- PASS: tester loader logs lowercase collision without raising.
- PASS: wf_grid raises on lowercase collision before lowercasing.
- PASS: equal_blocks + `trade_filter.enabled=true` is rejected for all enabled subfilter modes.
- PASS: full regression matrix is documented.
- PASS: audit fixes F1/A1/D1/D3 are covered by targeted tests.
- DEFERRED: F9 production CSV snapshot is post-merge/recommended, not mandatory.
