# Implementation Plan v1: Diagnostics v2 for tester run 9/10

Основано на `docs/diagnostics_9_10_tz_v2_final.md`.

## 1. Цель

Добавить opt-in Excel diagnostics v2 для одиночного tester-прогона в canonical
exporter:

```text
donor/supertrend_optimizer/io/excel_tester.py
```

Первая реализация покрывает только Фазу A:

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

Новые листы строятся только из уже готовых результатов прогона: `period_results`,
`pr_100`, `df`, `trades_100`, `signals_df`, `fd_100`,
`filter_diagnostics_summary`, `run_metadata`, `trade_filter_config`,
`config_yaml_snapshot`.

Ключевой контракт: exporter не меняет движок, сделки, позиции, signal events,
filter runtime и engine metrics.

## 2. Не-цели

- Не менять `runner.py`, `signal_events.py`, расчет позиций, расчет сделок и
  логику фильтра.
- Не реализовывать v2-листы в `wf_grid/export/xlsx_writer.py`.
- Не реализовывать v2-листы в `donor zigzag/supertrend_optimizer/io/excel_tester.py`.
- Не удалять и не переименовывать legacy sheets.
- Не переносить Phase B/C в первый scope.
- Не добавлять YAML override для thresholds.
- Не делать полный code-level lookahead audit.
- Не выдавать proxy-метрики Filter_Attribution за realized PnL.

## 3. Затрагиваемые модули

Primary implementation:

```text
donor/supertrend_optimizer/io/diagnostics_v2.py
donor/supertrend_optimizer/io/excel_tester.py
```

CLI/config wiring:

```text
donor/supertrend_optimizer/cli/tester.py
donor TESTER/run_batch_tester.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_phase2_tester_import_smoke.py
donor TESTER/tests/test_tester_config_sheet.py
```

Existing parity/coverage tests to extend:

```text
donor TESTER/tests/test_xlsx_cycle_sheet.py
wf_grid/tests/test_wp9_diagnostics_export.py
wf_grid/tests/test_wakeup_mode_d_entry.py
```

New focused test module:

```text
donor TESTER/tests/test_diagnostics_v2.py
```

No production changes expected in:

```text
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/testing/signal_events.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/backtest.py
wf_grid/export/xlsx_writer.py
```

## 4. Architecture

Add a diagnostics module:

```python
# donor/supertrend_optimizer/io/diagnostics_v2.py
@dataclass(frozen=True)
class DiagnosticsV2Context:
    period_results: list[Any]
    pr_100: Any | None
    df: pd.DataFrame
    trades_100: pd.DataFrame
    signals_df: pd.DataFrame
    fd_100: Mapping[str, Any] | None
    filter_diagnostics_summary: Any
    run_metadata: Mapping[str, Any]
    trade_filter_config: Any
    config_yaml_snapshot: Mapping[str, Any] | None
    cycle_map: pd.DataFrame
    thresholds: pd.DataFrame
```

The context factory owns:

```text
find pr_100
normalize missing/empty DataFrame inputs
expose column checks
derive cycle_map once
provide threshold constants
provide consistent SKIP/missing behavior
```

`excel_tester.py` should stay a writer/orchestrator:

```text
compute filter_enabled and v2 gate
build DiagnosticsV2Context
call diagnostics_v2 build functions
write returned DataFrames in fixed order
apply minimal Excel formatting
```

All calculations live in `diagnostics_v2.py`. Existing cycle-sheet code remains
in `excel_tester.py` until parity is locked; v2 gets a separate
`derive_trade_cycle_map` helper that must match existing cycle semantics.

## 5. Exporter Contract

Extend `export_tester_results` signature:

```python
export_diagnostics_v2: bool = False
diagnostics_v2_flags: dict[str, bool] | None = None
```

Default must preserve legacy workbook.

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

Default child flags:

```text
Phase A: enabled
Phase B: disabled
Phase C: disabled
```

Disabled-path invariants:

```text
no v2 sheets
same existing sheet order
same existing headers
same existing cell values
same timestamp/metadata behavior
no calls to v2 builders
```

## 6. Sheet Order

Do not move existing sheets. When v2 is enabled, append v2 sheets after current
legacy and existing optional diagnostics sheets:

```text
Tester_Config
Summary
Metrics_*
Trades_*
Signals
false start
FilterDiagnostics_100
ZigZag_Trigger_Events
filters_summary
cycle
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

Phase B/C sheet names are reserved but not written unless explicitly enabled:

```text
Returns_Calendar
Exit_Quality
False_Start_2
Data_Dictionary
Robustness
```

## 7. Build/Write Contract

For every v2 sheet:

```python
_empty_<sheet>_df(...) -> pd.DataFrame
_build_<sheet>_df(ctx: DiagnosticsV2Context) -> pd.DataFrame
_write_<sheet>_sheet(writer: pd.ExcelWriter, df: pd.DataFrame) -> None
```

Build functions must:

```text
return fixed column order
handle normal/empty/missing inputs
not write Excel
not mutate ctx inputs
not call engine/backtest/signal engine
not read filesystem
```

Simple writer helpers may be shared, but empty/build functions stay separate
and unit-testable.

## 8. Core Utilities

### 8.1 Status vocabulary

Use only:

```text
PASS
WARN
FAIL
SKIP
INFO
```

Never emit `PASS` for checks that cannot be proven from current output data.

### 8.2 Threshold constants

Add:

```python
DIAGNOSTICS_V2_THRESHOLDS
```

Columns:

```text
flag
operator
value
unit
description
```

Defaults from the TZ: `pf_weak`, `median_negative`, `false_start_high`,
`avg_trade_too_small`, `cost_fragile`, `low_filter_coverage`,
`dd_duration_high`, `cycle_overtrade`, `giveback_high`.

### 8.3 Forward returns

Add:

```python
compute_forward_returns(df, event_index, horizons=(1, 3, 5, 10), price_col="close")
```

This is always raw close-to-close fixed-horizon proxy. It is not directional
realized PnL.

### 8.4 Cycle map

Add:

```python
derive_trade_cycle_map(fd_100, trades_100) -> pd.DataFrame
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

The helper must reuse the same active-state and segment-boundary rules as the
existing `cycle` sheet. Before changing shared cycle helpers, lock old `cycle`
sheet parity with tests.

## 9. Work Packages

### WP0 - Baseline and parity locks

Tasks:

```text
capture current disabled workbook sheet order and headers
capture current enabled diagnostics workbook sheet order and headers
lock old cycle sheet header/order
lock old false start sheet header/order
add test that export_diagnostics_v2 default is absent and no v2 builders run
```

Exit criteria:

```text
legacy tests pass before implementation starts
parity tests fail if existing sheets change
```

### WP1 - Diagnostics module skeleton

Files:

```text
donor/supertrend_optimizer/io/diagnostics_v2.py
donor TESTER/tests/test_diagnostics_v2.py
```

Tasks:

```text
add DiagnosticsV2Context and build_diagnostics_v2_context
add constants for sheet order and child flag defaults
add DIAGNOSTICS_V2_THRESHOLDS
add status helpers and missing-column helpers
add empty DataFrame builders for all Phase A sheets
add shared simple writer helper
```

Tests:

```text
context finds pr_100 by period_label
context normalizes missing trades/signals/df to empty DataFrames
context exposes fd_100 and filter_diagnostics_summary from pr_100
child flags default Phase A true and Phase B/C false
disabled child flag removes only that sheet
threshold table has required columns and flags
```

### WP2 - Exporter v2 gate and sheet orchestration

Files:

```text
donor/supertrend_optimizer/io/excel_tester.py
donor/supertrend_optimizer/cli/tester.py
donor TESTER/run_batch_tester.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_phase2_tester_import_smoke.py
```

Tasks:

```text
extend export_tester_results signature with export_diagnostics_v2
extend signature with diagnostics_v2_flags
compute top-level v2 gate after pr_100 discovery
append v2 sheets after current optional diagnostics/cycle block
wire direct API tests first
add tester config/CLI key export.diagnostics_v2 default false
add optional export.diagnostics_v2_flags mapping if current config loader supports it cleanly
pass the new values from cli/tester.py and donor TESTER/run_batch_tester.py
```

Tests:

```text
export_diagnostics_v2=False writes no v2 sheets
filter disabled writes no v2 sheets and does not call v2 builders
export_diagnostics=False writes no v2 sheets
pr_100 missing writes no v2 sheets
enabled v2 path appends Phase A sheets after current existing sheets
child flag disables one sheet without changing order of the remaining v2 sheets
Phase B/C flags default to no sheets
```

### WP3 - Reproducibility, Index, Run_Health

Files:

```text
donor/supertrend_optimizer/io/diagnostics_v2.py
donor TESTER/tests/test_diagnostics_v2.py
```

Tasks:

```text
implement _build_index_df from planned sheet records
implement _build_reproducibility_df from run_metadata + df + report_generator_version
implement _build_run_health_df with only verifiable checks
ensure exporter never gathers git/hash itself
ensure warmup semantics are INFO, not FAIL on entry_index >= warmup
```

Run_Health checks:

```text
Summary vs Trades
Filter states length
Filter states sum
Duplicate timestamps
OHLCV NaN
Timezone consistency
Trade index bounds
Signal before entry only when signal index exists
Execution price sanity only when provable
Commission sanity
Cycle map coverage
Warmup semantics as INFO
```

Tests:

```text
missing metadata produces missing values, not guesses
rows count / first timestamp / last timestamp / timezone derive from df
Summary vs Trades PASS within tolerance
missing signal index produces SKIP
warmup never produces FAIL solely from min entry index
duplicate timestamps FAIL
OHLCV NaN WARN
```

### WP4 - Trade_Analytics and cycle map

Tasks:

```text
implement derive_trade_cycle_map
implement _build_trade_analytics_df
reuse one cycle_map in Trade_Analytics, Cycle_Summary, Run_Health
clip exit_index to available df range
calculate MFE/MAE direction-aware
calculate edge_ratio, r_multiple, giveback, exit_efficiency
derive entry hour/day/month/weekday from df index when possible
mark invalid/missing indexes via data_quality_status
```

Tests:

```text
empty trades returns fixed columns
invalid entry_index marks invalid_entry_index and NaN metrics
exit_index before entry or beyond df is clipped and flagged
long MFE/MAE formulas
short MFE/MAE formulas
time_to_mfe_bars and time_to_mae_bars offsets
cycle fields are joined from derive_trade_cycle_map
cycle map handles missing fd_100, outside_cycle, invalid_entry_index
cycle map matches existing cycle sheet segment boundaries on zigzag and volume-only fixtures
```

### WP5 - Cycle_Summary and Equity_Drawdown

Tasks:

```text
implement _build_cycle_summary_df with two blocks in one sheet
implement _build_equity_drawdown_df with equity, episodes, worst 10
include note that trade-equity DD excludes intratrade mark-to-market DD
avoid requiring equality with engine MaxDD
```

Tests:

```text
cycle overview aggregates net_pnl_pct and positive trade pct
trade number in cycle block computes count/mean/median/win_rate
no separate Trade_Number_In_Cycle sheet is written
equity is cumulative sum of trades_100.net_pnl_pct
drawdown episode start/bottom/recovery/depth/duration
open unrecovered drawdown status is marked
worst 10 block is sorted by depth
empty trades returns fixed empty/summary layout
```

### WP6 - Filter_Funnel and Filter_Attribution

Tasks:

```text
implement _build_filter_funnel_df as independent gate summary, not sequential funnel
never compute percent from previous gate
keep rows for missing source columns with SKIP notes
implement optional first-blocking block only with explicit reason or documented priority
implement compute_forward_returns
implement _build_filter_attribution_df
classify allowed entries and blocked candidate/attempt events
write mandatory proxy disclaimer
avoid banned names: Saved PnL, Lost PnL, Net filter value
```

Tests:

```text
missing gate columns keep SKIP rows
pass/fail counts use declared denominator rules
no % from previous gate columns exist
compute_forward_returns returns NaN for missing/out-of-range horizons
allowed events come from actual trades entry_index
blocked universe excludes ordinary non-candidate bars
explicit block reason mapping wins over wakeup gate fallback
unknown blocked events map to Blocked by unknown/other
sheet notes include fixed-horizon close-to-close proxy disclaimer
```

### WP7 - Dashboard, Cost_Sensitivity, Remediation

Tasks:

```text
implement _build_cost_sensitivity_df
implement _build_dashboard_df from already-built Phase A outputs
implement _build_remediation_df from Phase A outputs
mark Cost_Sensitivity as simplified per-trade model
use gross_pnl_pct when available, otherwise proxy_from_net
use bps per side units
omit tick scenarios unless run_metadata["tick_size"] exists
ensure Dashboard excludes p-values, confidence intervals, statistical significance
ensure Remediation emits SKIP instead of unsupported recommendations
```

Tests:

```text
actual cost scenario computes current metrics
commission/slippage bps scenarios compute stressed_net_pnl_pct
gross_pnl_pct missing uses proxy_from_net
profit factor/win rate/max_dd/per_trade_sharpe definitions are stable
breakeven_bps_per_side is computed or SKIP when impossible
Dashboard KPI values match source sheets
Dashboard statistical significance fields are absent
Remediation rows use thresholds and confidence
missing source metric produces SKIP row, not factual suggestion
```

### WP8 - FilterDiagnostics_sampled and Excel formatting

Tasks:

```text
implement _build_filter_diagnostics_sampled_df
sample first 200, last 200, and windows around trade entry/exit indexes
deduplicate sampled row indexes
cap default output at 2000 rows
preserve original diagnostic key names without display rename
write sample strategy metadata on sheet
add generic autofilter/freeze/header formatting for v2 sheets
add Excel row-limit guards where relevant
```

Tests:

```text
missing fd_100 produces skipped/empty behavior per child flag contract
first/last rows included
trade-neighborhood rows included
sample size <= 2000 by default
columns keep raw fd_100 key names
sample strategy appears on sheet
large fd_100 does not materialize full diagnostics DataFrame except sampled rows
```

### WP9 - Integration, golden parity, performance

Tasks:

```text
add workbook integration tests for every gate matrix row
add v2 sheet presence/order tests
add legacy multi-period sheet presence test
add old cycle sheet unchanged test
add old false start unchanged test
add static test that v2 implementation does not import/call engine runner/signal_events
add smoke benchmark or documented measurement for performance budget
```

Performance smoke:

```text
baseline export time
v2 Phase A export time
baseline xlsx size
v2 xlsx size
peak memory if practical
FilterDiagnostics_sampled row count
```

Acceptance thresholds:

```text
export time overhead <= +30%
file size overhead <= +25% without full raw diagnostics
additional peak memory <= +300 MB
sampled diagnostics <= 2000 rows
```

## 10. Required Test Inventory

Unit tests:

```text
build_diagnostics_v2_context
resolve_diagnostics_v2_flags
_build_index_df
_build_reproducibility_df
_build_dashboard_df
_build_run_health_df
compute_forward_returns
_build_filter_funnel_df
_build_filter_attribution_df
_build_trade_analytics_df
derive_trade_cycle_map
_build_cycle_summary_df
_build_equity_drawdown_df
_build_cost_sensitivity_df
_build_remediation_df
_build_filter_diagnostics_sampled_df
```

Integration tests:

```text
disabled filter path: no v2 sheets
export_diagnostics_v2=False: no v2 sheets
export_diagnostics=False: no v2 sheets
enabled v2 path: Phase A sheet presence/order
child flags disable individual sheets
Phase B/C default disabled
legacy Metrics_* and Trades_* sheets remain present
existing cycle sheet remains unchanged
existing false start sheet remains unchanged in Phase A
v2 sheets do not require engine module changes
```

Golden/parity tests:

```text
disabled-path workbook structure remains bit-identical
existing cycle sheet header/order remains unchanged
existing false start sheet remains unchanged
Tester_Config remains first sheet
Index is appended after existing sheets, not moved to workbook front
```

## 11. Verification Commands

Focused:

```powershell
python -m pytest "donor TESTER/tests/test_diagnostics_v2.py" -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
python -m pytest "donor TESTER/tests/test_tester_config_sheet.py" -q
```

Nearby regression:

```powershell
python -m pytest wf_grid/tests/test_wp9_diagnostics_export.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_xlsx_export.py -q
```

Broader non-slow:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

Static checks:

```powershell
rg "signal_events|run_backtest|backtest|runner" donor/supertrend_optimizer/io/diagnostics_v2.py
```

The static check should have no prohibited runtime calls. Imports from typing or
comments are acceptable only if tests explicitly whitelist them.

## 12. Implementation Order

```text
1. Add parity tests for current disabled path, old cycle, old false start.
2. Add diagnostics_v2.py skeleton, flags, thresholds, context factory.
3. Extend export_tester_results signature with v2 args defaulting to disabled.
4. Add v2 gate and prove default/no-op paths are unchanged.
5. Add sheet registry and v2 append order.
6. Wire tester config/CLI/run_batch flags with default false.
7. Implement Reproducibility, Index, Run_Health.
8. Implement compute_forward_returns.
9. Implement derive_trade_cycle_map with existing cycle parity fixtures.
10. Implement Trade_Analytics.
11. Implement Cycle_Summary and Equity_Drawdown.
12. Implement Filter_Funnel and Filter_Attribution.
13. Implement Cost_Sensitivity.
14. Implement Dashboard from built Phase A outputs.
15. Implement Remediation.
16. Implement FilterDiagnostics_sampled.
17. Add Excel formatting and row guards for v2 sheets.
18. Add full integration sheet-order tests.
19. Add benchmark/smoke measurement.
20. Run focused, nearby, then broader non-slow verification.
```

## 13. Delivery Scope

Recommended review slices:

```text
PR 1: parity locks, diagnostics_v2 skeleton, context, flags, exporter no-op gate.
PR 2: Reproducibility, Index, Run_Health, forward returns, cycle map.
PR 3: Trade_Analytics, Cycle_Summary, Equity_Drawdown.
PR 4: Filter_Funnel, Filter_Attribution, Cost_Sensitivity.
PR 5: Dashboard, Remediation, FilterDiagnostics_sampled, formatting, benchmark.
```

Estimated effort: 5-8 engineering days, excluding unexpected regressions in the
existing Excel/cycle tests.

If implemented as one branch, each PR boundary above still acts as a checkpoint:
do not start the next block until focused tests for the current block are green.

## 14. Main Risks And Guardrails

```text
Risk: disabled workbook changes unintentionally.
Guardrail: parity tests before touching exporter; v2 defaults false.

Risk: diagnostics_v2 duplicates or mutates engine logic.
Guardrail: v2 builds only from supplied outputs; static no-engine-call test.

Risk: cycle map diverges from existing cycle sheet.
Guardrail: derive_trade_cycle_map parity tests against current cycle fixtures.

Risk: Filter_Funnel implies false sequential causality.
Guardrail: no % from previous gate; every row states denominator rule.

Risk: Filter_Attribution is read as realized PnL.
Guardrail: proxy disclaimer and banned metric names test.

Risk: Run_Health overclaims PASS.
Guardrail: unverifiable checks return SKIP, warmup is INFO.

Risk: v2 sheets bloat files or memory.
Guardrail: sampled raw diagnostics only, row guards, benchmark.

Risk: CLI enables v2 accidentally.
Guardrail: config defaults false and import-smoke signature tests.
```

## 15. Acceptance Checklist

Implementation is complete when:

```text
export_diagnostics_v2 defaults to False
disabled path workbook remains bit-identical
filter disabled writes no v2 sheets
export_diagnostics=False writes no v2 sheets
enabled v2 path appends Phase A sheets in fixed order
child flags control individual v2 sheets
Phase B/C sheets are reserved and disabled by default
legacy Metrics_* and Trades_* sheets remain
Tester_Config remains first sheet
existing cycle and false start sheets remain unchanged
DiagnosticsV2Context is the single source for pr_100/trades_100/fd_100/cycle_map
all Phase A build functions are unit-tested on normal/empty/missing inputs
Run_Health uses only verifiable PASS/FAIL checks
Trade_Analytics and Cycle_Summary use one derive_trade_cycle_map
Filter_Attribution is explicitly marked as fixed-horizon proxy
Cost_Sensitivity is explicitly marked as simplified per-trade model
FilterDiagnostics_sampled is capped at 2000 rows by default
no v2 code calls backtest/signal engine
performance budget is measured or documented
focused and nearby regression tests pass
```
