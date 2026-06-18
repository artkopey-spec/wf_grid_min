# Implementation Plan v2: Tester Diagnostics v2

## 0. Назначение документа

Документ задает самостоятельный, готовый к реализации план добавления opt-in Excel diagnostics v2 для одиночного tester-прогона в canonical exporter:

```text
donor/supertrend_optimizer/io/excel_tester.py
```

Цель реализации - добавить аналитический слой поверх уже полученных результатов tester/backtest без изменения торгового движка, сделок, позиций, signal events, filter runtime и engine metrics.

Ключевой принцип: diagnostics v2 - это read-only export/reporting feature. Если v2 выключен, существующий workbook должен остаться неизменным по структуре и значениям.

## 1. Итоговый scope первой реализации

Первая реализация делится на безопасные инкременты. Не реализовывать все сложные листы в одном изменении.

### 1.1. A0 - foundation and no-op gate

Обязательный первый checkpoint:

```text
new diagnostics_v2 module
explicit disabled-by-default exporter args
config/CLI/batch wiring with defaults disabled
sheet registry
build-all-then-write orchestration
parity locks for legacy workbook
no v2 sheets when disabled
```

На этом checkpoint допускаются только пустые/служебные v2 builders, если они не вызываются на disabled path.

### 1.2. A0.5 - cycle-mapping foundation

Cycle helper parity is a foundation step between A0 and A1, implemented as WP3. It is listed separately because it is not a user-facing sheet phase, but it is required before full `Run_Health`, `Trade_Analytics` and `Cycle_Summary`.

### 1.3. A1 - low-risk core sheets

Первый функциональный набор после cycle-mapping foundation:

```text
Index
Reproducibility
Run_Health
FilterDiagnostics_sampled
```

Эти листы не требуют новой интерпретации PnL/attribution и должны доказать безопасность архитектуры. `Run_Health` может включать `Cycle map coverage` только после готовности общего cycle-mapping helper. До этого такая проверка должна быть `SKIP (cycle_map not available)`.

### 1.4. A2 - trade and drawdown analytics

Второй функциональный набор:

```text
Trade_Analytics
Equity_Drawdown
Cycle_Summary
Cost_Sensitivity
```

Перед началом A2 должен быть зафиксирован общий cycle-mapping contract, основанный на существующей логике старого `cycle` sheet.

### 1.5. A3 - interpretive sheets

Третий функциональный набор:

```text
Filter_Funnel
Filter_Attribution
Dashboard
Remediation
```

Эти листы наиболее рискованны, потому что легко создать ложную причинность или unsupported recommendations. Они реализуются только после готовности source sheets из A1/A2.

### 1.6. Out of scope

В первую реализацию не входит:

```text
changes in runner.py / backtest.py / signal_events.py behavior
changes in position/trade calculation
changes in filter runtime logic
v2 rollout to wf_grid/export/xlsx_writer.py
v2 rollout to donor zigzag exporter
YAML threshold override
statistical significance / p-values / bootstrap / Monte Carlo
full code-level lookahead audit
full raw per-bar diagnostics export beyond sampled sheet
```

## 2. Affected modules

Primary implementation:

```text
donor/supertrend_optimizer/io/diagnostics_v2.py
donor/supertrend_optimizer/io/excel_tester.py
```

CLI/config wiring:

```text
donor/supertrend_optimizer/cli/tester.py
donor TESTER/run_batch_tester.py
```

Tests:

```text
donor TESTER/tests/test_diagnostics_v2.py
donor TESTER/tests/test_diagnostics_v2_export.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_phase2_tester_import_smoke.py
donor TESTER/tests/test_tester_config_sheet.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
wf_grid/tests/test_wp9_diagnostics_export.py
wf_grid/tests/test_wakeup_mode_d_entry.py
```

No production behavior changes expected in:

```text
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/testing/signal_events.py
donor/supertrend_optimizer/core/backtest.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/volume_only_filter.py
wf_grid/export/xlsx_writer.py
```

Exception: if cycle helpers must be shared to prevent duplication, extract read-only helper code without changing legacy sheet output. This extraction must be protected by legacy cycle parity tests before and after the move.

## 3. Exporter contract

Extend `export_tester_results`:

```python
export_diagnostics_v2: bool = False
diagnostics_v2_flags: Mapping[str, bool] | None = None
```

Defaults must preserve existing behavior.

Top-level v2 gate:

```text
filter_enabled == True
export_diagnostics == True
export_diagnostics_v2 == True
pr_100 exists
```

`filter_enabled`:

```text
is_zigzag_enabled(trade_filter_config) or is_volume_enabled(trade_filter_config)
```

If the gate is false:

```text
do not import heavy diagnostics builders lazily inside the write path
do not build DiagnosticsV2Context
do not call v2 builders
do not add v2 sheets
do not mutate run_metadata
do not add default v2 fields to Tester_Config
do not change existing sheet order, headers, rows, values, formatting or timestamps
```

## 4. Config and CLI schema

### 4.1. YAML schema

Recognized `export` keys after this change:

```text
diagnostics: bool
signals: bool
false_start: bool
cycle: bool
trades: bool
false_start_max_bars: int >= 1
diagnostics_v2: bool
diagnostics_v2_flags: mapping[str, bool]
```

Defaults:

```text
export.diagnostics_v2 = false
export.diagnostics_v2_flags = {}
```

Compatibility rule:

```text
Top-level export.* keeps the current permissive semantics.
Unknown top-level export keys are ignored, as they are today.
Only export.diagnostics_v2_flags is strict.
```

Unknown keys inside `export.diagnostics_v2_flags` must fail fast with a clear config error. String/int/null values must not be coerced to bool.

### 4.2. Valid child flags

Phase A flags:

```text
index
reproducibility
dashboard
run_health
trade_analytics
equity_drawdown
filter_funnel
filter_attribution
cycle_summary
cost_sensitivity
remediation
filter_diagnostics_sampled
```

Reserved disabled flags:

```text
returns_calendar
exit_quality
false_start_2
data_dictionary
robustness
```

Default resolution:

```text
Phase A flags default true when diagnostics_v2 gate is true
reserved flags default false
explicit false disables only that sheet
explicit true for reserved flags is accepted only after the sheet is implemented
```

Until a reserved sheet is implemented, explicit true for it must fail fast instead of silently writing nothing.

### 4.3. Shared resolver for diagnostics collection

Extend and move the existing CLI resolver into one shared place used by both CLI and batch runner:

```python
resolve_collect_filter_diagnostics(
    export_config: Mapping[str, Any],
    *,
    preserve_legacy_batch_default: bool = False,
) -> bool
```

It must return true if any exported feature needs `filter_diagnostics`:

```text
export.diagnostics
export.signals
export.cycle
export.trades
export.diagnostics_v2
```

Use this resolver in:

```text
donor/supertrend_optimizer/cli/tester.py
donor TESTER/run_batch_tester.py
```

Current behavior note:

```text
The CLI already resolves diagnostics collection conditionally.
The batch runner currently relies on run_all_periods default collect_filter_diagnostics=True.
When wiring the shared resolver into batch, preserve existing batch behavior when diagnostics_v2=False: collection remains True.
When diagnostics_v2=True, collection must also be True.
A later explicit cleanup may make batch conditional, but that is a separate compatibility change.
```

This prevents CLI and batch behavior from diverging accidentally while avoiding a hidden legacy batch regression.

## 5. Workbook sheet order

Existing sheets keep their current order. v2 sheets are appended after all current legacy and optional diagnostics sheets.

Current existing order is owned by `excel_tester.py`; do not hardcode a different legacy order in diagnostics_v2.

When v2 is enabled, append enabled v2 sheets in this order:

```text
Index
Reproducibility
Dashboard
Run_Health
Trade_Analytics
Equity_Drawdown
Filter_Funnel
Filter_Attribution
Cycle_Summary
Cost_Sensitivity
Remediation
FilterDiagnostics_sampled
```

Reserved names for later phases:

```text
Returns_Calendar
Exit_Quality
False_Start_2
Data_Dictionary
Robustness
```

`Index` must not become the first workbook sheet. `Tester_Config` remains first whenever it is first today.

## 6. Build-all-then-write architecture

`excel_tester.py` remains the workbook orchestrator:

```text
resolve v2 gate
resolve child flags
build DiagnosticsV2Context only when gate is true
call diagnostics_v2.build_enabled_v2_sheets(ctx, flags)
write returned sheet payloads in fixed order
apply generic Excel formatting
```

`diagnostics_v2.py` owns all v2 calculations.

Important: build all enabled v2 DataFrames before writing any v2 sheet. Dashboard, Remediation and Index must read from the already-built payload cache, not independently recompute source metrics.

Required objects:

```python
@dataclass(frozen=True)
class DiagnosticsV2Context:
    period_results: list[Any]
    pr_100: Any
    df: pd.DataFrame
    trades_100: pd.DataFrame
    signals_df: pd.DataFrame
    fd_100: Mapping[str, Any] | None
    filter_diagnostics_summary: Mapping[str, Any] | None
    run_metadata: Mapping[str, Any]
    trade_filter_config: Any
    config_yaml_snapshot: Mapping[str, Any] | None
    cycle_map: pd.DataFrame
    thresholds: pd.DataFrame

@dataclass(frozen=True)
class DiagnosticsV2Sheet:
    name: str
    phase: str
    df: pd.DataFrame
    status: str
    primary_source: str
    notes: str
```

The context factory owns:

```text
find pr_100
normalize missing df/trades/signals to empty DataFrames
provide column/type checks
derive cycle_map exactly once after the cycle helper is implemented
provide an empty cycle_map with explicit unavailable status before that checkpoint
provide threshold constants
provide consistent missing/SKIP behavior
```

`frozen=True` protects field assignment only. It does not make pandas DataFrames immutable. Unit tests must verify that representative builders do not mutate `ctx.df`, `ctx.trades_100`, `ctx.signals_df` or `ctx.cycle_map`.

Build functions must:

```text
return DataFrame with fixed columns
handle normal, empty and missing inputs
not write Excel
not mutate inputs
not read filesystem
not call runner/backtest/signal_events
not compute trading decisions
```

## 7. Status vocabulary

Allowed status values:

```text
PASS
WARN
FAIL
SKIP
INFO
```

Rules:

```text
PASS only when proven from supplied output data
FAIL only for a concrete invariant violation
WARN for suspicious but not invalid data
SKIP for missing/ambiguous source data
INFO for descriptive facts without pass/fail meaning
```

Never use PASS for "not checked".

## 8. Threshold constants

Add:

```python
DIAGNOSTICS_V2_THRESHOLDS
```

Required columns:

```text
flag
operator
value
unit
description
```

Initial flags:

```text
pf_weak
median_negative
false_start_high
avg_trade_too_small
cost_fragile
low_filter_coverage
dd_duration_high
cycle_overtrade
giveback_high
```

Thresholds are constants in Phase A. No YAML override in this implementation.

## 9. Cycle mapping contract

Cycle mapping is the highest-risk shared concept. Do not implement it as a second independent FSM interpretation.

Required helper:

```python
derive_trade_cycle_map(
    fd_100: Mapping[str, Any] | None,
    trades_100: pd.DataFrame,
    *,
    df: pd.DataFrame | None = None,
    mode: str | None = None,
) -> pd.DataFrame
```

Output columns:

```text
trade_id
cycle_id
trade_idx_in_cycle
cycle_age_at_entry
cycle_trade_count_at_entry
is_in_cycle
cycle_start_index
cycle_end_index
mapping_status
```

Allowed `mapping_status`:

```text
mapped
outside_cycle
missing_entry_index
invalid_entry_index
missing_fd_100
unsupported_mode
missing_required_columns
```

Implementation rule:

```text
Use the same active-state and segment-boundary rules as the existing cycle sheet.
For zigzag mode, preserve current entry-signal mapping semantics: entry_signal_bar = max(entry_index - 1, 0).
For volume-only mode, preserve current volume cycle segment semantics, including open/suppressed cycles.
```

Preferred implementation:

```text
extract shared read-only cycle segment helpers from excel_tester.py
keep old cycle sheet output byte/row compatible
use the same extracted helpers in old cycle builder and v2 cycle map
```

If extraction is too risky, implement the v2 helper by calling existing internal helpers and lock parity with tests. Do not reimplement cycle boundaries from scratch.

## 10. Sheet contracts

### 10.1. Index

Columns:

```text
Sheet
Phase
Purpose
Status
Primary source
Notes
```

Build from the final sheet payload cache after all enabled sheets are built. Include disabled/skipped sheets only if useful for navigation; do not imply that reserved sheets exist.

Index payload grows per phase. WP4 may list only A1 sheets; WP5/WP6 must extend the Index rows as A2/A3 sheets are implemented, using the same payload-cache source of truth.

### 10.2. Reproducibility

Sources:

```text
run_metadata
df
config_yaml_snapshot
report_generator_version constant
```

Exporter must not gather git/hash itself.

Missing metadata behavior:

```text
value = missing
status = SKIP or INFO depending on field
```

Rows must include at minimum:

```text
report_generator_version
rows_count
first_timestamp
last_timestamp
timezone
config_path
csv_path
data_hash if supplied
config_hash if supplied
git_commit if supplied
dirty_worktree if supplied
```

Current upstream metadata is sparse. The sheet is considered valid if unsupported fields are emitted as `missing` with `SKIP`/`INFO`, but the first implementation must not pretend that git/hash/version data was collected.

Optional CLI/batch metadata enrichment is allowed outside the exporter:

```text
python_version
pandas_version
openpyxl_version
run_started_at
command_line
git_commit
branch
dirty_worktree
config_hash
data_hash
```

If metadata enrichment is implemented, it must be implemented in a shared CLI/batch helper so Reproducibility fields do not diverge by entrypoint. Do not enrich only CLI or only batch.

If this enrichment is not implemented, tests must assert the expected `missing` behavior. The exporter itself must still not gather git/hash or read files.

### 10.3. Run_Health

Checks must be individually defined with input columns, tolerance and fallback status.

Initial checks:

```text
Summary vs Trades net PnL
Filter diagnostics array length consistency
Filter diagnostics all arrays same length
Duplicate timestamps
OHLCV NaN
Timezone consistency
Trade index bounds
Entry/exit index order
Signal before entry only when signal index can be proven
Execution price sanity only when entry/exit price and OHLC range are available
Commission sanity
Cycle map coverage
Warmup facts as INFO
```

`Cycle map coverage` is enabled only after the cycle-mapping helper is implemented. Before that checkpoint the row must be present as `SKIP` with a note such as `cycle_map not available in this implementation slice`.

Warmup must never FAIL solely because `min(entry_index) >= warmup`. That is an informational fact unless a separate invariant is provably violated.

### 10.4. FilterDiagnostics_sampled

Purpose: lightweight raw diagnostics access without writing full `FilterDiagnostics_100`.

Rules:

```text
source = fd_100
include first 200 rows
include last 200 rows
include windows around trade entry_index and exit_index
deduplicate row indexes
cap default output at 2000 rows
preserve original diagnostic key names
write sample metadata rows or columns
do not materialize full len(df) x all_keys DataFrame
```

If `fd_100` is missing:

```text
write fixed-column SKIP sheet when child flag is enabled
do not fail export
```

### 10.5. Trade_Analytics

Sources:

```text
trades_100
df
cycle_map
```

Rules:

```text
preserve raw trade ids
validate entry_index and exit_index
clip exit_index to available df range only after recording a quality flag
calculate MFE/MAE direction-aware
derive entry time buckets from df.index when possible
join cycle fields from the single cycle_map
```

Quality statuses:

```text
ok
missing_entry_index
invalid_entry_index
missing_exit_index
invalid_exit_index
exit_before_entry
exit_clipped
missing_ohlc
unsupported_direction
```

### 10.6. Equity_Drawdown

Definition:

```text
trade_equity = cumulative sum of trades_100.net_pnl_pct
```

Required note:

```text
Trade-equity drawdown excludes intratrade mark-to-market drawdown and may differ from engine/bar-level MaxDD.
```

Blocks:

```text
Equity by trade
Drawdown episodes
Worst 10 drawdowns
```

Do not require equality with engine `max_drawdown`.

### 10.7. Cycle_Summary

Sources:

```text
cycle_map
trades_100
fd_100
```

Blocks:

```text
Cycle overview
Trade number in cycle
```

No separate `Trade_Number_In_Cycle` sheet.

If cycle_map is missing or unsupported:

```text
write fixed layout with SKIP rows
```

### 10.8. Cost_Sensitivity

Model:

```text
simplified per-trade cost stress
unit = bps per side
round_trip_cost_pct = 2 * bps_per_side / 100
```

If `gross_pnl_pct` exists:

```text
stressed_net_pnl_pct = gross_pnl_pct - round_trip_cost_pct
cost_model_status = gross_available
```

If only `net_pnl_pct` exists:

```text
stressed_net_pnl_pct = net_pnl_pct - additional_round_trip_cost_pct
cost_model_status = proxy_from_net
```

Tick scenarios are allowed only when `run_metadata["tick_size"]` exists.

Current CLI/batch metadata does not provide `tick_size`. Therefore tick scenarios are expected to be absent/SKIP in the initial implementation unless an upstream caller explicitly supplies `run_metadata["tick_size"]`.

Metrics:

```text
scenario
unit
net_pnl_pct
avg_trade_pct
median_trade_pct
profit_factor
win_rate
max_dd_trade_equity
per_trade_sharpe
breakeven_bps_per_side
cost_model_status
notes
```

### 10.9. Filter_Funnel

This is not a sequential funnel. It is an independent gate summary.

Rules:

```text
never compute percent from previous gate
each row states its denominator
missing source columns remain visible as SKIP rows
do not imply causal order unless the source has explicit order
```

Columns:

```text
gate
source_column
passed_count
failed_count
denominator
pass_pct_of_denominator
status
notes
```

Mode availability matrix:

```text
zigzag mode D:
  wakeup_* gates may be available
  immediate_candidate_* gates may be available
  zigzag candidate/median gates may be available

zigzag non-D:
  zigzag candidate/median gates may be available
  wakeup_* gates are expected SKIP

volume-only:
  volume_* gates may be available
  cycle_direction_gate columns may be available
  zigzag and wakeup gates are expected SKIP
```

A3 tests must cover both zigzag and volume-only paths. A volume-only run with mostly SKIP interpretive rows is acceptable when the source columns are absent.

### 10.10. Filter_Attribution

This sheet is a fixed-horizon close-to-close proxy, not realized PnL.

Mandatory disclaimer row/note:

```text
All attribution values are fixed-horizon close-to-close proxies. They are not realized PnL and do not prove saved or lost profit.
```

Banned metric names:

```text
Saved PnL
Lost PnL
Net filter value
```

Allowed event universes:

```text
allowed_entries:
  actual trades from trades_100.entry_index

blocked_signal_events:
  rows in signals_df where filter_decision indicates blocked
  or filter_block_reason is non-empty

blocked_fd_attempts:
  only when fd_100 has explicit attempt markers:
    filter_allowed_entry == 0 with non-empty filter_block_reason
    or st_flip_dir != 0 with non-empty filter_block_reason
```

If none of these explicit universes exists:

```text
write SKIP rows
do not infer blocked candidates from ordinary non-candidate bars
```

Block reason priority:

```text
explicit signal filter_block_reason
explicit fd_100 filter_block_reason
wakeup component failure only when documented by explicit wakeup_*_ok columns
unknown/other
```

Mode availability matrix:

```text
zigzag mode D:
  allowed entries from trades_100
  blocked signal events from signals_df if exported
  blocked fd attempts from filter_allowed_entry/st_flip_dir/filter_block_reason
  wakeup component failures only when explicit wakeup_*_ok columns exist

zigzag non-D:
  same as above without wakeup component attribution

volume-only:
  allowed entries from trades_100
  blocked events only from explicit volume/filter block columns
  wakeup and zigzag candidate attribution are SKIP
```

Forward returns helper:

```python
compute_forward_returns(df, event_index, horizons=(1, 3, 5, 10), price_col="close")
```

Rules:

```text
raw close-to-close
no direction unless explicitly requested by a caller and labeled as directional proxy
NaN for out-of-range horizons
NaN for missing close values
```

### 10.11. Dashboard

Dashboard must be built from the already-built payload cache:

```text
Run_Health
Trade_Analytics
Equity_Drawdown
Filter_Funnel
Filter_Attribution
Cycle_Summary
Cost_Sensitivity
```

It must not recompute source metrics independently.

Forbidden in Phase A dashboard:

```text
p-values
confidence intervals
statistical significance
causal claims about filter value
```

### 10.12. Remediation

Remediation must be conservative.

Columns:

```text
Priority
Symptom
Detection metric
Observed
Threshold
Likely cause
Parameter family
Suggested action
Source sheet
Confidence
Status
```

If source metric is missing or proxy-only:

```text
Status = SKIP or WARN
Confidence = low
Suggested action must not be factual/imperative
```

No recommendation may be emitted from an unsupported or missing source metric.

## 11. Excel writing and formatting

Use a small generic writer helper for v2 sheets:

```text
write DataFrame
freeze header row
apply autofilter
safe column widths
strip timezone from datetimes before writing
guard Excel row limit
```

Do not change formatting of existing sheets.

Do not use v2 writer helpers for legacy sheets in the same implementation unless a parity test proves no change.

## 12. Performance and memory contract

Reference dataset class:

```text
~300k rows x ~80 diagnostic columns
```

Budgets:

```text
Phase A export time overhead <= +30% vs baseline
XLSX size overhead <= +25% without full raw diagnostics
additional peak memory <= +300 MB
FilterDiagnostics_sampled <= 2000 rows by default
```

Algorithmic guardrails:

```text
no full fd_100 DataFrame except sampled rows
prefer numpy arrays for per-bar counts
prefer trades-sized DataFrames for trade analytics
materialize only columns required by a sheet
do not copy df unless needed for a small output
```

Add a mandatory smoke benchmark in the final hardening PR. If exact peak memory is impractical, document export time, file size and sampled row count at minimum. Do not mark final acceptance complete without a recorded performance measurement.

## 13. Static safety checks

`diagnostics_v2.py` must not call trading engine/backtest/signal generation.

Use AST/static tests rather than broad grep.

Forbidden runtime imports/calls in `diagnostics_v2.py`:

```text
supertrend_optimizer.testing.runner.run_all_periods
supertrend_optimizer.testing.runner.run_period
supertrend_optimizer.testing.signal_events.build_signal_events
supertrend_optimizer.core.backtest
supertrend_optimizer.engine.run
```

Allowed:

```text
typing imports
plain Any annotations
calling read-only helper functions explicitly approved for cycle parity
```

The static test must fail only on real prohibited imports/calls, not comments or docstrings.

Scope the AST check narrowly:

```text
detect ast.Import and ast.ImportFrom for forbidden modules
detect direct ast.Call names for known forbidden functions if imported
detect attribute calls on explicitly imported forbidden modules
do not attempt to prove absence of all dynamic getattr/importlib cases in Phase A
```

This keeps the static guard useful without turning it into a broad security analyzer.

## 14. Coordination and rollback

### 14.1. Parallel engine/filter work

Before WP0 baseline locks are captured, check whether parallel work is changing:

```text
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/volume_only_filter.py
donor/supertrend_optimizer/io/excel_tester.py
```

Known high-risk overlap:

```text
cycle trade limit / Mode D FSM / exit reason work
```

Rule:

```text
Do not treat WP0 golden snapshots as durable while overlapping engine/filter work is unmerged.
Choose a merge order, then rebase the second stream and recapture WP0 baselines from the final code.
```

### 14.2. Reproducible baselines

WP0 baselines should be reproducible artifacts, not one-off manual snapshots.

Required:

```text
fixture inputs checked into tests or generated deterministically
helper that regenerates expected workbook structure/header/value summaries
clear command for recapturing baselines after intentional legacy changes
review note explaining why any baseline recapture is expected
```

### 14.3. Rollback strategy

Rollback is feature-flag based:

```text
export_diagnostics_v2 defaults false
disabling export.diagnostics_v2 removes all v2 sheets
legacy sheets and engine behavior remain available without migration
```

If an enabled v2 path causes production issues:

```text
turn export.diagnostics_v2 off
keep legacy diagnostics enabled if needed
fix v2 behind the flag
do not hotfix by changing legacy sheet semantics
```

## 15. Work packages

### WP0 - Baseline locks

Tasks:

```text
capture current disabled workbook sheet order and headers
capture current enabled diagnostics workbook sheet order and headers
lock old cycle sheet header/order and representative values
lock old false start sheet header/order
add test that v2 defaults false
add test that disabled v2 path does not call v2 context/builders
```

Exit criteria:

```text
legacy tests pass before implementation
parity tests fail on existing sheet order/header/value changes
```

### WP1 - Config and collection resolver

Tasks:

```text
extend export config schema with diagnostics_v2 and diagnostics_v2_flags
validate child flags strictly
add resolve_diagnostics_v2_flags
add shared resolve_collect_filter_diagnostics with caller policy
use shared resolver in CLI and batch runner without reducing batch collection
ensure Tester_Config does not gain implicit v2 defaults on disabled path
```

Tests:

```text
default diagnostics_v2 is false
unknown top-level export key keeps current permissive behavior
unknown v2 flag fails
non-bool v2 flag fails
reserved true flag fails until implemented
CLI and batch pass collect_filter_diagnostics consistently
batch diagnostics collection behavior is unchanged when diagnostics_v2=False
disabled workbook Tester_Config unchanged
```

### WP2 - Diagnostics module skeleton and exporter gate

Tasks:

```text
add diagnostics_v2.py
add DiagnosticsV2Context
add DiagnosticsV2Sheet
add sheet registry and fixed v2 order
add threshold constants
add empty DataFrame builders for Phase A sheets
extend export_tester_results args
wire top-level v2 gate
append enabled v2 sheets after existing sheets
```

Tests:

```text
export_diagnostics_v2=False writes no v2 sheets
filter disabled writes no v2 sheets and does not build context
export_diagnostics=False writes no v2 sheets
missing pr_100 writes no v2 sheets
enabled v2 appends sheets after current sheets
child false removes only that sheet
```

### WP3 - Cycle helper parity

Tasks:

```text
extract or reuse existing cycle segment helpers
implement derive_trade_cycle_map from shared helper
cover zigzag and volume-only modes
preserve entry_signal_bar = max(entry_index - 1, 0)
do not change old cycle sheet
```

Tests:

```text
old cycle sheet unchanged
zigzag segment boundaries match old cycle sheet
volume-only segment boundaries match old cycle sheet
invalid/missing entry_index statuses
outside cycle status
missing fd_100 status
```

### WP4 - A1 core sheets

Tasks:

```text
implement Reproducibility
implement Run_Health with verifiable checks only
implement FilterDiagnostics_sampled
implement Index from payload cache
add generic v2 writer formatting
```

Tests:

```text
missing metadata is missing/SKIP, not guessed
df row count and timestamps derive from df
duplicate timestamps FAIL
OHLCV NaN WARN
missing signal index SKIP
warmup row INFO
cycle map coverage PASS/SKIP follows cycle_map availability
sample first/last rows
sample trade neighborhoods
sample size <= 2000
sample preserves raw fd_100 keys
Index reflects written/skipped sheets
```

### WP5 - A2 trade/drawdown/cost sheets

Tasks:

```text
implement Trade_Analytics
implement Equity_Drawdown
implement Cycle_Summary
implement Cost_Sensitivity
```

Tests:

```text
empty trades fixed columns/layout
long MFE/MAE formulas
short MFE/MAE formulas
exit clipping flagged
cycle fields joined from cycle_map
trade-equity cumulative sum
DD episodes open/recovered statuses
worst 10 sorted by depth
gross cost model and proxy_from_net model
breakeven bps SKIP when impossible
```

### WP6 - A3 funnel/attribution/dashboard/remediation

Tasks:

```text
implement Filter_Funnel as independent gate summary
implement Filter_Attribution only from explicit event universes
implement Dashboard from payload cache
implement Remediation from payload cache and thresholds
```

Tests:

```text
no percent-from-previous columns
missing gate columns remain SKIP rows
blocked universe excludes ordinary bars
signals_df explicit block reason wins
unknown blocked events map to unknown/other
zigzag mode D availability matrix tested
volume-only SKIP behavior tested
proxy disclaimer present
banned attribution names absent
Dashboard has no p-values/significance
Remediation SKIP when metric missing
```

### WP7 - Integration and performance

Tasks:

```text
gate matrix workbook integration tests
v2 sheet order tests
legacy multi-period sheet presence tests
old cycle unchanged tests
old false start unchanged tests
AST static no-engine-call test
mandatory smoke benchmark measurement
```

Gate matrix:

```text
filter disabled
export_diagnostics=False
export_diagnostics_v2=False
pr_100 missing
enabled all Phase A
one child flag false
reserved flag true
```

## 16. Verification commands

Focused:

```powershell
python -m pytest "donor TESTER/tests/test_diagnostics_v2.py" -q
python -m pytest "donor TESTER/tests/test_diagnostics_v2_export.py" -q
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
python -m pytest "donor TESTER/tests/test_tester_config_sheet.py" -q
```

Nearby regression:

```powershell
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
python -m pytest "donor TESTER/tests/test_phase2_tester_import_smoke.py" -q
python -m pytest wf_grid/tests/test_wp9_diagnostics_export.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_xlsx_export.py -q
```

Broader:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

## 17. Delivery checkpoints

Recommended PR boundaries:

```text
PR 1: WP0-WP2 foundation, no-op v2 gate, config resolver, empty registry
PR 2: WP3 cycle helper parity
PR 3: WP4 A1 core sheets and formatting
PR 4: WP5 A2 trade/drawdown/cost sheets
PR 5: WP6 A3 interpretive sheets
PR 6: WP7 integration, benchmark, hardening
```

Do not start a later PR until focused tests for the previous PR are green.

Estimated effort:

```text
foundation: 1.5-2 days
cycle parity: 1.5-3 days
A1 sheets: 2-3 days
A2 sheets: 3-5 days
A3 sheets: 3-5 days
integration/performance hardening: 1.5-3 days
total realistic range: 12-21 engineering days
```

The range excludes unexpected regressions in existing Excel/cycle tests.

## 18. Main risks and guardrails

Risk:

```text
breaking existing configs by making top-level export.* strict
```

Guardrail:

```text
preserve current permissive top-level export behavior
make only diagnostics_v2_flags strict
add compatibility tests for ignored unknown top-level export keys
```

Risk:

```text
batch runner loses legacy diagnostics after resolver unification
```

Guardrail:

```text
lock current batch behavior when diagnostics_v2=False
pass collect_filter_diagnostics explicitly only after regression coverage
test CLI and batch call sites separately
```

Risk:

```text
parallel cycle/Mode D engine work invalidates baselines
```

Guardrail:

```text
coordinate merge order
recapture reproducible WP0 baselines after engine/filter changes
do not mix baseline recapture with diagnostics feature code
```

Risk:

```text
disabled workbook changes unintentionally
```

Guardrail:

```text
parity tests before exporter edits
v2 defaults false
no implicit v2 metadata in Tester_Config
```

Risk:

```text
cycle map diverges from old cycle sheet
```

Guardrail:

```text
shared or reused cycle segment helpers
zigzag and volume-only parity tests
no independent FSM interpretation
```

Risk:

```text
Filter_Attribution implies realized PnL or false causality
```

Guardrail:

```text
explicit event universe only
proxy disclaimer
banned metric-name tests
SKIP when universe is ambiguous
```

Risk:

```text
CLI and batch collect different diagnostics
```

Guardrail:

```text
single shared resolve_collect_filter_diagnostics
tests for both call sites
```

Risk:

```text
v2 sheets bloat memory or XLSX size
```

Guardrail:

```text
sampled raw diagnostics only
array-based aggregation
row caps
benchmark measurement
```

Risk:

```text
Run_Health overclaims PASS/FAIL
```

Guardrail:

```text
per-check source/tolerance/fallback rules
SKIP for missing data
INFO for warmup facts
```

Risk:

```text
scope creep stalls delivery
```

Guardrail:

```text
A0/A1/A2/A3 split
PR checkpoints
do not mix interpretive sheets with foundation
```

## 19. Final acceptance checklist

Implementation is complete when:

```text
export_diagnostics_v2 defaults false
disabled path workbook remains unchanged by parity tests
top-level export.* keeps current permissive compatibility
diagnostics_v2_flags are strictly validated
filter disabled writes no v2 sheets
export_diagnostics=False writes no v2 sheets
missing pr_100 writes no v2 sheets
enabled v2 appends implemented Phase A sheets in fixed order
child flags control individual implemented sheets
reserved true flags fail fast until implemented
Tester_Config remains first and unchanged on disabled path
legacy Metrics_* and Trades_* remain present
old cycle sheet remains unchanged
old false start sheet remains unchanged
CLI and batch use one diagnostics collection resolver
batch legacy diagnostics behavior is regression-locked
DiagnosticsV2Context is the single source for pr_100/trades_100/fd_100/cycle_map
representative builders are tested not to mutate context DataFrames
Dashboard, Remediation and Index are built from payload cache
Run_Health uses only verifiable PASS/FAIL checks
cycle map uses existing/shared cycle semantics
Filter_Attribution is explicitly proxy-only
A3 SKIP behavior is tested for zigzag and volume-only source-column differences
Cost_Sensitivity is explicitly simplified per-trade model
tick scenarios are absent/SKIP unless tick_size is supplied
Reproducibility missing fields are explicit missing/SKIP unless upstream metadata is supplied
FilterDiagnostics_sampled is capped at 2000 rows by default
diagnostics_v2.py does not call engine/backtest/signal generation
performance budget is measured by mandatory smoke benchmark
focused and nearby regression tests pass
```
