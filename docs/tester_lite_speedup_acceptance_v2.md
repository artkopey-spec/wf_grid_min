# Tester Lite Speedup Acceptance v2

Date: 2026-06-16

## Scope

Representative lite config:

```text
C:\3.1_wf_grid\config tester\modeD_atr_freeze_001_long_sw030_lw0400_mr16_ttl005_hold02.yaml
```

Path:

```text
run_configs_tester_parallel.py -> tester legacy path
jobs=1
period=false
export.diagnostics=false
export.signals=false
export.false_start=false
export.cycle=false
export.trades=false
```

## Lite Metrics Equality

Baseline artifact:

```text
_bench\wp0_baseline\modeD_atr_freeze_001_baseline.xlsx
```

Current artifact:

```text
_bench\final_latency_r3\0001_modeD_atr_freeze_001_long_sw030_lw0400_mr16_ttl005_hold02_20260616_145549_903_22908.xlsx
```

Metrics from `Metrics_100`:

| Metric | Baseline | Current | Match |
|---|---:|---:|---|
| Num Trades | 1595 | 1595 | yes |
| Sum PnL % | 9.51 | 9.51 | yes |
| Win Rate | 46.2069 | 46.2069 | yes |
| Profit Factor | 1.1412 | 1.1412 | yes |
| Avg Trade | 0.006 | 0.006 | yes |
| Sharpe | 0.0451 | 0.0451 | yes |
| Sortino | 0.0742 | 0.0742 | yes |
| CAGR | 0.0001 | 0.0001 | yes |
| Max Drawdown | -0.0536 | -0.0536 | yes |

## Latency

| Variant | jobs | configs | repeat 1 sec | repeat 2 sec | repeat 3 sec | median sec/config |
|---|---:|---:|---:|---:|---:|---:|
| before | 1 | 1 | 6.678132 | | | 6.678132 |
| after diagnostics gating | 1 | 1 | 3.669 | 3.575 | 3.543 | 3.575 |

Smoke logs confirm:

```text
Filter diagnostics collection: False (legacy)
```

## Regression Tests

Command:

```powershell
python -m pytest wf_grid/tests/test_tester_lite_speedup_cli.py wf_grid/tests/test_zigzag_apply_characterization.py wf_grid/tests/test_w5_standalone_volume.py wf_grid/tests/test_wp9_diagnostics_export.py wf_grid/tests/test_wp10_finalization.py -q
```

Result:

```text
190 passed, 76 warnings
```

Warnings are existing pandas fragmentation `PerformanceWarning` messages from
`wf_grid\bucket\median_matrix_builder.py`.

## Notes

The single-config target of about 5 seconds was met for this representative
lite config on the measured path.

Throughput / `--jobs` selection is not covered by this note; it requires the
separate 20-40 config benchmark from the plan.
