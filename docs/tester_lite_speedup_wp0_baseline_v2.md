# Tester Lite Speedup WP0 Baseline v2

Date: 2026-06-16

## Scope

Representative lite config:

```text
C:\3.1_wf_grid\config tester\modeD_atr_freeze_001_long_sw030_lw0400_mr16_ttl005_hold02.yaml
```

Data:

```text
C:\3.1_wf_grid\data.csv
rows: 297219
size_bytes: 17676893
sha256: 69e0bef879845e300628ea8c4fd7ceb309d363cdff6265f850f38a115a8e4a88
```

Export flags:

```text
diagnostics=false
signals=false
false_start=false
cycle=false
trades=false
```

Path:

```text
legacy tester, period=false, trade_filter.enabled=true, zigzag_st_mode, mode D
```

## Environment

```text
commit: 3681a00bd8da84a7fa471f63dfee6816b66d104f
OS: Windows-11-10.0.26200-SP0
Python: 3.14.5
pandas: 3.0.3
numpy: 2.4.6
CPU: Intel64 Family 6 Model 154 Stepping 4, GenuineIntel
RAM: unavailable from sandbox; Win32_ComputerSystem access denied
storage type: unavailable from sandbox; Get-Volume access denied
```

## Timing

One warm, instrumented run through `run_backtest_with_df`:

| Component | Calls | Total sec |
|---|---:|---:|
| CSV load+validate | 1 | 1.981334 |
| run_backtest_with_df total | 1 | 6.678132 |
| run_all_periods | 1 | 5.747335 |
| run_single_backtest | 1 | 5.651516 |
| run_backtest_fast | 1 | 5.075604 |
| zigzag_st_filter.apply | 1 | 4.669086 |
| extract_trades | 1 | 0.507530 |
| XLSX export_tester_results | 1 | 0.642900 |

Output artifact:

```text
C:\3.1_wf_grid\_bench\wp0_baseline\modeD_atr_freeze_001_baseline.xlsx
```

## Go/No-Go

Go.

`zigzag_st_filter.apply` is 4.669086 sec of 6.678132 sec total
(`~69.9%` of the single lite-config runtime). Diagnostics cleanup/gating remains
a meaningful performance target.

## WP10 DataFrame Copy Decision

Measured after WP8 wiring on the same `data.csv`:

| Operation | Median sec | Repeats |
|---|---:|---:|
| `df.copy(deep=True)` | 0.012509 | 20 |
| `df.copy(deep=False)` | 0.000059 | 20 |

Decision: leave `run_configs_tester_parallel.py` at `deep=True`.

Rationale: `0.012509 sec` is far below the plan threshold of roughly 5-10% of
single-config latency, so shallow copy is not a visible bottleneck in this
benchmark path.
