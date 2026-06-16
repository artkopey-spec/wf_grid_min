# Implementation Plan v2: Tester Lite Speedup Without Trading Logic Changes

## 1. Цель

Ускорить обработку одного lite-config в `run_configs_tester_parallel.py` за счет
отключения дорогого сбора per-bar filter diagnostics, когда пользователь не
экспортирует артефакты, которым эти diagnostics нужны.

Целевой ориентир:

```text
single lite-config: примерно 7-10 sec -> около 5 sec на том же железе
```

Главный инвариант:

```text
positions / filtered_positions
returns
equity_curve
trades_df
metrics
XLSX formats when relevant export flags are enabled
```

не должны измениться.

Если целевой ориентир 5 sec не достигнут, но время `apply()` существенно
снижено, задача считается технически успешной только при условии, что новый
bottleneck измерен и зафиксирован отдельно.

## 2. Lite Mode Definition

Lite mode для tester legacy path означает:

```python
export.diagnostics == False
export.signals == False
export.cycle == False
export.false_start == False
export.trades == False
```

В этом режиме разрешено не собирать per-bar `filter_diagnostics`.

Не вводить partial diagnostics. Режим строго бинарный:

```text
full diagnostics dict
или
filter_diagnostics=None
```

## 3. Scope

В scope входят:

1. Очистка и упрощение allocation/finalization path в
   `donor/supertrend_optimizer/core/zigzag_st_filter.py::apply`.
2. Добавление `collect_filter_diagnostics: bool = True`.
3. Прокидывание флага по tester legacy call chain.
4. Отключение allocations, object label materialization, per-bar writes и final
   diagnostics construction в lite mode.
5. Сохранение `filter_config_snapshot` независимо от per-bar diagnostics.
6. Условная замена `deep=True` на `deep=False` в parallel worker только если
   profiling покажет заметную цену копирования и mutation hash-test докажет
   безопасность.
7. Измерение latency/throughput и подбор `--jobs`.

Не входят:

```text
FSM semantic changes
SuperTrend / ZigZag / ATR / volume formula changes
extract_trades changes
trades_df schema changes when export.trades=true
trade-level metric override changes
ZigZagGlobalStats / ATR / SuperTrend runtime caching
vectorization / numba / cython
multiprocessing architecture changes beyond --jobs recommendation
equal-blocks optimization
```

## 4. Architecture Touchpoints

Основной tester legacy path:

```text
donor/supertrend_optimizer/cli/tester.py::run_backtest_with_df
-> donor/supertrend_optimizer/testing/runner.py::run_all_periods
-> donor/supertrend_optimizer/testing/runner.py::run_period
-> donor/supertrend_optimizer/engine/run.py::run_single_backtest
-> donor/supertrend_optimizer/core/backtest.py::run_backtest_fast
-> donor/supertrend_optimizer/core/zigzag_st_filter.py::apply
```

Important current-code facts:

```text
run_backtest_fast is in core/backtest.py.
apply() owns lifecycle-aware filtered_positions.
apply() currently has stale direct diagnostic allocations that are shadowed later.
apply() currently has duplicated diagnostics construction near the end.
trigger_source_arr[t] is read by Mode D / wakeup runtime logic.
ZigZag+volume path currently materializes volume labels more than once.
volume-only path has its own apply() and currently always returns diagnostics.
run_configs_tester_parallel.py currently uses _WORKER_DF.copy(deep=True).
```

Direct/internal callers must keep old behavior by default. Therefore all new
parameters default to `True`.

## 5. Export Contract

In `run_backtest_with_df`, after CLI/config merge, compute:

```python
collect_filter_diagnostics = (
    params["export"]["diagnostics"]
    or params["export"]["signals"]
    or params["export"]["cycle"]
    or params["export"]["trades"]
)
```

Rationale:

```text
diagnostics=true -> full diagnostics for diagnostics sheets
signals=true -> full diagnostics for filter columns in Signals
cycle=true -> full diagnostics for cycle sheet
trades=true -> full diagnostics for trade-level filter columns and exit_reason
false_start-only -> no diagnostics required by current exporter
all flags false -> no diagnostics
```

`export.trades=true` is mandatory full diagnostics mode. Do not create a reduced
trade diagnostics subset.

Before implementing this formula, verify the current false-start exporter:

```text
io/excel_tester.py false-start sheet must not require filter_diagnostics when
export.false_start=true and export.signals=false.
```

If this verification fails, stop and update the formula and tests before
continuing. There must be one source of truth for the export formula.

## 6. Volume-Only Decision

This implementation must not leave volume-only behavior ambiguous.

Required decision:

```text
Implement collect_filter_diagnostics for volume_only_filter.apply() as well.
```

Reason:

```text
The user-facing contract is lite mode, not only ZigZag lite mode.
If volume-only configs are present in the benchmark set, they must return
filter_diagnostics=None when no export flag requires diagnostics.
```

Allowed volume-only disabled-diagnostics behavior:

```text
positions are computed exactly as before
filter_diagnostics=None
filter_config_snapshot is preserved
no object label arrays are materialized only for export
```

If a volume-only runtime value is required for positions, it remains always-on.
Only export-only arrays and labels are gated.

## 7. Core Invariants

1. `collect_filter_diagnostics=True` preserves current behavior:
   same diagnostics keyset, dtype, length and values.
2. `collect_filter_diagnostics=False` returns `filter_diagnostics=None`.
3. `positions` from `True` and `False` are byte-for-byte equal for the same
   representative config.
4. Lite metrics and trade count are equal before/after optimization.
5. `export.trades=true` forces full diagnostics.
6. `filter_config_snapshot` is preserved even when diagnostics are disabled.
7. No dummy arrays are allowed in disabled diagnostics mode.
8. No runtime decision may read an optional diagnostic array.

## 8. Work Packages

### WP0. Baseline Profiling And Safety Snapshot

Before code changes, record a baseline on at least one representative lite config.

Measure:

```text
total run_backtest_with_df time
run_all_periods time
run_single_backtest time
zigzag_st_filter.apply time
extract_trades time
XLSX export time
```

Use the same config/data for before/after comparisons.

Record:

```text
CPU
RAM
storage type if known
OS
Python version
pandas/numpy versions
commit hash
config path
data.csv fingerprint
export flags
```

Go/no-go rule:

```text
If diagnostics-related work inside zigzag_st_filter.apply() is not a meaningful
share of total runtime, do not continue directly into gating as a performance
task. As a practical threshold, if the removable diagnostics overhead appears
to be below roughly 30-40% of single-config latency, pause and record the real
bottleneck before implementation continues.
```

Known out-of-scope candidates that may dominate runtime:

```text
build_zigzag_global_stats
calculate_supertrend
extract_trades
XLSX writer overhead
```

This task may still proceed as cleanup/refactor work, but the performance goal
must be re-scoped if WP0 shows that diagnostics are not the main bottleneck.

### WP1. Audit `apply()` Runtime Reads And Writes

Create:

```text
docs/zigzag_apply_diagnostics_audit_v2.md
```

The audit must classify every array in `zigzag_st_filter.apply()`:

```text
name
created at
written in main loop: yes/no
read in main loop: yes/no
affects positions: yes/no
decision: always-on runtime / optional diagnostics
notes
```

Minimum always-on runtime data:

```text
filtered_positions
scalar FSM state: state, held_pos, confirmed_legs_since_start,
  zz_legs_since_lifecycle_start, _stopping_start when needed
scalar wakeup / freeze state: wakeup_cycle_age, wakeup_bars_since_fresh,
  wakeup_active_direction, wakeup_exit_c_fired, cycle_direction,
  pos_freeze_until, pos_freeze_pending
trend_arr, confirm_event, cand_height, local_median_N,
  local_median_available, cand_age_bars, cand_leg_dir
daily_reset_event, time_filter_in_window, time_filter_reset_event,
  combined_reset_event
volume_condition_allowed_runtime and numeric/code volume runtime values
wakeup_atr_ratio, wakeup_volume_ratio and wakeup thresholds
```

Known runtime reads to remove before gating:

```text
trigger_source_arr[t] -> wakeup_started_this_bar
trigger_source_arr[t] -> real_opened / position_freeze branch
```

Stop rule:

```text
If any other diagnostic array is read by runtime logic, do not gate it until
the read is moved to local scalar state and covered by position equality tests.
```

### WP2. Clean The Current `apply()` Structure

Before adding gating, make `apply()` have one authoritative allocation and
one authoritative diagnostics finalization path.

Required cleanup:

1. Remove the stale direct allocation block that creates diagnostics arrays and
   is later shadowed by `_allocate_apply_arrays()`.
2. Remove the dead/manual `filter_diagnostics_out` construction near the end of
   `apply()` if the result is returned through `_finalize_apply_result()`.
3. Keep `collect_filter_diagnostics=True` behavior identical after cleanup.
4. Add/keep regression tests proving diagnostics keyset/dtype/value stability.

This WP is not a behavior change.

### WP3. Decouple `trigger_source` Runtime From Diagnostics Storage

Inside the main loop, introduce local per-bar state:

```python
trigger_source_this_bar = _TRIGGER_SOURCE_NONE
wakeup_started_this_bar = False
```

Runtime rules:

```text
When Mode D wakeup starts:
  trigger_source_this_bar = _TRIGGER_SOURCE_WAKEUP
  wakeup_started_this_bar = True

When non-D lifecycle start happens:
  trigger_source_this_bar = lifecycle_start.trigger_source

All runtime decisions read trigger_source_this_bar / wakeup_started_this_bar.
No runtime decision reads trigger_source_arr[t].
```

Diagnostics rule:

```python
if diag_enabled:
    trigger_source_arr[t] = trigger_source_this_bar
```

Update position-freeze logic:

```text
real_opened uses trigger_source_this_bar == _TRIGGER_SOURCE_WAKEUP
```

Acceptance:

```text
collect_filter_diagnostics=True keeps the same trade_filter_trigger_source values
Mode D / wakeup positions remain unchanged
```

### WP4. Split Runtime Storage From Diagnostic Storage

Preferred shape:

```python
filtered_positions = np.zeros(n, dtype=np.int8)
diagnostic_arrays = (
    _allocate_diagnostic_arrays(...)
    if collect_filter_diagnostics
    else None
)
diag_enabled = diagnostic_arrays is not None
```

Allowed alternative:

```text
Keep _allocate_apply_arrays() but make it diagnostics-only and do not return
filtered_positions from it.
```

Required:

```text
No dummy arrays in disabled mode.
No optional diagnostic array is referenced when diag_enabled is False.
Positions are always stored in filtered_positions.
```

### WP5. Gate ZigZag Diagnostic Writes And Label Materialization

When `diag_enabled=False`, do not allocate or write:

```text
state_arr, state_code_arr, trigger_source_arr
confirmed_legs_since_start_arr
st_flip_dir_arr
trade_filter_enabled_arr
constant broadcast diagnostic arrays
median_stop_triggered_arr
stopping_started_at_arr
filter_allowed_entry_arr
filter_block_reason_arr
exit_off diagnostic arrays
candidate primitive diagnostic arrays
bar-start snapshot arrays
zigzag mode/gate/immediate diagnostic arrays
wakeup diagnostic arrays
position-freeze diagnostic arrays
```

Always-on runtime values remain unguarded when they affect positions.

For ZigZag+volume:

```text
Keep numeric/code arrays required for runtime.
Materialize human-readable volume labels only when diag_enabled=True.
Do not materialize volume label arrays twice.
```

Required order for ZigZag+volume consolidation:

```text
1. Identify runtime-required volume arrays separately from export labels.
2. Keep volume_condition_allowed_runtime and numeric/code arrays always-on when
   they affect lifecycle decisions.
3. Gate the in-loop diagnostic block-reason label read under diag_enabled.
4. Use one materialization helper/source for enabled diagnostics finalization.
5. Only then remove the old inline materialization block.
```

Do not remove inline volume materialization until any in-loop reads of
human-readable labels have either been gated or replaced with runtime-safe code.

### WP6. Gate Final Diagnostics Construction

Disabled path returns directly:

```python
return ZigZagSTFilterResult(
    positions=filtered_positions,
    filter_diagnostics=None,
    internal_legs=None,
    filter_config_snapshot=(
        volume_runtime.filter_config_snapshot
        if volume_runtime is not None else None
    ),
)
```

Enabled path uses one diagnostics builder.

Enabled path must preserve:

```text
same keyset
same dtype
same values
same conditional Mode D wakeup keys
same volume keys when volume_runtime is present
```

### WP7. Add `collect_filter_diagnostics` Signatures

Add a keyword-only trailing parameter:

```python
*, collect_filter_diagnostics: bool = True
```

Required functions:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py::apply
donor/supertrend_optimizer/core/volume_only_filter.py::apply
donor/supertrend_optimizer/core/backtest.py::run_backtest_fast
donor/supertrend_optimizer/engine/run.py::run_single_backtest
donor/supertrend_optimizer/testing/runner.py::run_period
donor/supertrend_optimizer/testing/runner.py::run_all_periods
```

Also inspect direct callers for signature compatibility. No behavior change is
required for these paths in this task because the new parameter defaults to
`True`; only update tests or monkeypatches if the new kw-only signature requires
it:

```text
wf_grid/wf/step_executor.py
donor/supertrend_optimizer/walk_forward.py
tests with monkeypatched run_single_backtest/run_period
```

Do not insert the new parameter before existing positional parameters.

### WP8. CLI Formula And Runner Wiring

In `run_backtest_with_df`:

1. Compute `collect_filter_diagnostics` from export flags.
2. Pass it into `run_all_periods`.
3. Keep equal-blocks behavior unchanged.
4. Log the resolved value once in the tester run log.

Required tester cases:

```text
all export flags false -> collect_filter_diagnostics=False
export.diagnostics=true -> True
export.signals=true -> True
export.cycle=true -> True
export.trades=true -> True
export.false_start=true only -> False
```

Downstream smoke gate for the new state combination:

```text
trade_filter enabled
collect_filter_diagnostics=False
BacktestResult.filter_diagnostics is None
```

Run this through the full tester legacy path:

```text
run_backtest_with_df
run_all_periods / run_period
export_tester_results
parallel summary row creation
```

with all export flags false. The run must finish without requiring any
diagnostics-dependent sheet, summary, or signal artifact. This gate specifically
protects downstream code from the previously uncommon combination:

```text
filter enabled + filter_diagnostics=None
```

Also document explicitly:

```text
equal_blocks path is not optimized by this feature.
WF/direct run_single_backtest callers keep collect_filter_diagnostics=True unless
they explicitly opt in later.
```

The tester log must print the resolved `collect_filter_diagnostics` value and
the active path (`legacy` or `equal_blocks`) once per run.

Downstream audit checklist:

```text
donor/supertrend_optimizer/io/excel_tester.py
donor/supertrend_optimizer/testing/signal_events.py
donor/supertrend_optimizer/testing/runner.py summaries
wf_grid diagnostics summary/export consumers
run_configs_tester_parallel.py summary output
```

For each consumer, verify whether it checks `filter_diagnostics is not None`
instead of assuming:

```text
trade filter enabled -> diagnostics dict exists
```

### WP9. Volume-Only Gating

Update `volume_only_filter.apply()`:

```text
positions logic remains unchanged
state scalar and cycle direction scalar remain always-on
diagnostic arrays are allocated only when collect_filter_diagnostics=True
filter_config_snapshot is always returned
```

Disabled path:

```python
VolumeOnlyFilterResult(
    positions=positions,
    filter_diagnostics=None,
    filter_config_snapshot=volume_runtime.filter_config_snapshot,
)
```

If type annotations currently require `dict`, update them to optional dict.

### WP10. Conditional Shallow DataFrame Copy

This WP is optional and must run only after WP0 profiling and the performance
measurement pass.

Go/no-go rule:

```text
If DataFrame deep copy is not a visible bottleneck, for example below roughly
5-10% of single-config runtime, leave deep=True unchanged.
```

Before changing worker copy behavior, add or run a representative mutation test:

```python
before_hash = pd.util.hash_pandas_object(_WORKER_DF, index=True).sum()
before_shape = _WORKER_DF.shape
before_dtypes = tuple(map(str, _WORKER_DF.dtypes))

# run one representative config through run_backtest_with_df

after_hash = pd.util.hash_pandas_object(_WORKER_DF, index=True).sum()
after_shape = _WORKER_DF.shape
after_dtypes = tuple(map(str, _WORKER_DF.dtypes))
```

Representative configs:

```text
lite ZigZag config
Mode D / wakeup config
ZigZag+volume config
volume-only config if supported by target config set
time_filter/daily_reset config if present in target set
```

Only if copy overhead is worth optimizing and hash, shape and dtypes match,
change:

```python
df = _WORKER_DF.copy(deep=True)
```

to:

```python
df = _WORKER_DF.copy(deep=False)
```

Do not remove `.copy()` in this task.

Test requirement:

```text
worker passes a different DataFrame object
contents are equal
copy is called with deep=False
_WORKER_DF is unchanged after representative task execution
```

Prefer a small DataFrame subclass or local wrapper for the `deep=False` assertion.
Avoid global monkeypatching of pandas internals across the full pipeline.

If this WP is skipped because ROI is low, record the decision in benchmark notes.

## 9. Tests

### Mandatory Focused Tests

```powershell
python -m pytest wf_grid/tests/test_wp4_zigzag_per_bar.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_tester_parallel_runner.py -q
```

### Branch Coverage Tests

```powershell
python -m pytest wf_grid/tests/test_wakeup_ohlc_atr_backtest.py -q
python -m pytest wf_grid/tests/test_wakeup_volume_plumbing.py -q
python -m pytest wf_grid/tests/test_time_filter.py -q
python -m pytest wf_grid/tests/test_daily_reset.py -q
python -m pytest wf_grid/tests/test_w5_standalone_volume.py -q
python -m pytest wf_grid/tests/test_w8_wf_volume_integration.py -q
```

### New Or Updated Tests

Required:

```text
1. zigzag apply True returns previous diagnostics keyset/dtypes/values.
2. zigzag apply False returns filter_diagnostics is None.
3. zigzag apply True vs False positions are equal.
4. Mode D/wakeup True vs False positions are equal.
5. ZigZag+volume True vs False positions are equal and snapshot preserved.
6. volume-only True vs False positions are equal and snapshot preserved.
7. run_single_backtest True vs False:
   diagnostics differs as expected;
   metrics and trades count match.
8. early_exit + diagnostics disabled keeps array lengths valid.
9. export.trades=true with other export flags false keeps trade filter columns.
10. export.trades=true with zero trades keeps expected trades sheet headers.
11. CLI export formula cases from WP8.
12. include_period_splits=False still propagates collect_filter_diagnostics.
13. full lite export smoke: filter enabled, all export flags false,
    filter_diagnostics=None, export_tester_results succeeds.
14. false_start-only export smoke proves no diagnostics are required.
15. period=false and period=true benchmark/smoke cases are recorded separately.
16. parallel summary row creation does not read diagnostics.
17. parallel worker shallow copy test and mutation hash-test, only if WP10 is
    implemented.
```

Diagnostics comparisons must compare raw arrays/DataFrames, not rendered XLSX
cell formatting.

## 10. Golden Acceptance

### Lite Metrics Equality

On 5-10 real lite configs, compare before/after:

```text
num_trades
sum_pnl_pct
win_rate
profit_factor
avg_trade
sharpe
sortino
cagr
max_drawdown
```

Expected: exact equality of raw values after identical serialization boundary.

### Diagnostics Regression

Use one representative diagnostics-enabled config.

Compare:

```text
filter_diagnostics keyset
dtype for every key
length for every key
values for every key
normalized XLSX sheet contents for diagnostics-dependent sheets
```

Do not require byte-identical XLSX zip files.

### Trades Regression

Case:

```text
export.trades=true
all other export flags false
```

Verify:

```text
Trades sheet exists
trade filter columns match
entry_filter_state / entry_trigger_source / exit_reason values match
wakeup/volume columns match when present
trade-level metrics match
zero-trades header behavior is preserved
```

### Positions Invariant

For representative configs:

```python
np.testing.assert_array_equal(result_true.positions, result_false.positions)
```

This is the primary acceptance gate.

## 11. Performance Measurement

Measure `period=false` and `period=true` separately if both modes are used by
real tester configs. They have different numbers of period slices and therefore
different numbers of `apply()` calls per config.

Latency table:

| Variant | jobs | configs | repeat 1 sec | repeat 2 sec | repeat 3 sec | median sec/config |
|---|---:|---:|---:|---:|---:|---:|
| before | 1 | 1 | | | | |
| after apply cleanup | 1 | 1 | | | | |
| after diagnostics gating | 1 | 1 | | | | |
| after shallow copy | 1 | 1 | | | | |

Throughput table:

| Variant | jobs | configs | repeat 1 configs/min | repeat 2 configs/min | repeat 3 configs/min | median configs/min |
|---|---:|---:|---:|---:|---:|---:|
| before | 4 | 20-40 | | | | |
| after | 4 | 20-40 | | | | |
| after | 6 | 20-40 | | | | |
| after | 8 | 20-40 | | | | |
| after | 10 | 20-40 | | | | |

Command template:

```powershell
python .\run_configs_tester_parallel.py `
  --jobs 8 `
  --configs-dir .\configs_lite_benchmark `
  --output-dir .\_bench\jobs_8_run_1 `
  --csv .\data.csv `
  --glob "*.yml"
```

Select the median-best stable `--jobs` only if failure rate does not increase.

## 12. Implementation Order

Recommended commits:

1. `test/audit: profile and classify diagnostics runtime`
   - Record baseline breakdown.
   - Add `docs/zigzag_apply_diagnostics_audit_v2.md`.
   - Classify arrays and runtime reads.

2. `refactor: clean zigzag apply diagnostics structure`
   - Remove stale allocations.
   - Remove dead diagnostics builder.
   - Keep enabled diagnostics byte-for-byte stable.

3. `refactor: decouple trigger source from diagnostics arrays`
   - Add local trigger source variables.
   - Remove runtime reads from `trigger_source_arr`.
   - Add Mode D/wakeup equality coverage.

4. `perf: gate filter diagnostics collection`
   - Add `collect_filter_diagnostics`.
   - Gate ZigZag allocations, writes, volume labels and finalization.
   - Gate volume-only diagnostics.
   - Thread flag through call chain.

5. `perf: use shallow dataframe copy after mutation proof` optional
   - Run only if profiling shows DataFrame deep copy is worth optimizing.
   - Add mutation hash-test.
   - Replace worker copy with `deep=False`.
   - Update worker test.
   - If skipped, record the reason and leave `deep=True`.

6. `bench: record tester lite jobs recommendation`
   - Add benchmark tables and recommended `--jobs`.

## 13. Rollback Plan

If positions differ:

```text
Rollback diagnostics gating commit.
Keep audit/cleanup/trigger-source refactor only if their own equality tests pass.
Find remaining runtime read from an optional diagnostic array.
```

If `export.trades=true` breaks:

```text
Check CLI formula.
Check runner receives collect_filter_diagnostics=True.
Check attach_trade_filter_diagnostics receives full diagnostics.
Rollback gating commit if needed.
```

If diagnostics XLSX breaks:

```text
Check collect_filter_diagnostics=True path.
Compare diagnostics keyset/dtype/values.
Rollback diagnostics gating, not shallow copy.
```

If volume-only breaks:

```text
Rollback volume-only gating portion.
Keep ZigZag gating only if all ZigZag acceptance gates pass.
```

If shallow copy proves unsafe:

```text
Rollback shallow-copy commit only.
Keep diagnostics gating if tests and acceptance are green.
```

## 14. Definition Of Done

Done when:

1. Baseline profiling is recorded and the go/no-go decision is documented.
2. `docs/zigzag_apply_diagnostics_audit_v2.md` exists.
3. `apply()` has one allocation path and one diagnostics finalization path.
4. Runtime logic does not read optional diagnostic arrays.
5. `collect_filter_diagnostics=False` returns `filter_diagnostics=None` in
   ZigZag, ZigZag+volume and volume-only lite modes.
6. `collect_filter_diagnostics=True` preserves current diagnostics output.
7. `positions` match for `True` and `False`.
8. Lite metrics and trade count match before/after.
9. `export.trades=true` preserves trade filter columns and values.
10. Diagnostics-enabled XLSX content is preserved.
11. Mandatory pytest tests are green.
12. Shallow copy is either left as `deep=True` with a recorded low-ROI decision,
    or changed to `deep=False` and protected by mutation hash-test.
13. Performance tables are recorded.
14. Recommended `--jobs` is selected.
15. No changes were made to trading formulas, `extract_trades`, metric formulas,
    or enabled-export XLSX formats.
