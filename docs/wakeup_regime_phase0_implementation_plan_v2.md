# Wakeup Regime Phase 0 Implementation Plan v2

## 1. Goal

Implement a Phase 0 prototype of `wakeup_regime` for the donor tester path.

The prototype must validate the trading hypothesis:

```text
fresh price impulse + ATR expansion + volume expansion
-> immediate entry in the ZigZag candidate leg direction
-> limited supervised wakeup window
-> window shutdown when impulse ages or structure compresses
```

Phase 0 is tester-only. It must not enable wakeup behavior in WF Grid, WF export,
parallel WF runtime, or OOS validation.

## 2. Scope And Boundaries

Allowed implementation area:

```text
donor/supertrend_optimizer/...
donor TESTER tests
targeted shared-schema tests required by donor changes
```

Do not implement Phase 0 runtime behavior in:

```text
wf_grid WF execution
wf_grid export
parallel runtime transport
WF OOS validation
production effective-config source tracking
```

Important boundary:

`donor/supertrend_optimizer/core/trade_filter_config.py` is a shared schema module
used by both donor tester and WF Grid. Therefore Phase 0 may extend the shared
schema only with an explicit caller gate:

```text
caller_pipeline="tester"  -> Mode D / exit C / wakeup_regime accepted
caller_pipeline="wf_grid" -> Mode D / exit C / wakeup_regime rejected
```

This gate is mandatory. Without it, Phase 0 can accidentally expose unsupported
wakeup config to WF Grid.

## 3. Baseline

Before implementation:

1. Run the current donor tester baseline tests.
2. Run targeted existing ZigZag/filter tests that protect shared behavior.
3. Record the current passing set and any existing unrelated failures.

The implementation must preserve behavior for:

```text
ZigZag modes: A, B, C, A+B, C+B
exit modes: exit A, exit B
existing time_filter behavior
existing daily_reset behavior
existing trade_filter.volume gate behavior
old apply() callers without high/low/volume
```

## 4. Target Config Shape

Example supported Phase 0 config:

```yaml
trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    enabled: true
    mode: D
    reversal_threshold: 0.005
    candidate_trigger_threshold: 0.012
    local_window: 5
    global_stats_source: full_dataset

  lifecycle:
    exit_off_mode: "exit C"

  wakeup_regime:
    enabled: true

    entry:
      candidate_height:
        enabled: true
        quantile: 0.65

      candidate_age:
        enabled: true
        max_bars: 10

      atr_expansion:
        enabled: true
        short_window: 5
        long_window: 60
        min_ratio: 1.3

      volume_expansion:
        enabled: true
        short_window: 5
        baseline_window: 60
        min_ratio: 1.3

    exit:
      ttl:
        enabled: true
        bars: 45

      no_fresh_candidate:
        enabled: true
        quantile: 0.60
        max_age_bars: 15
        timeout_bars: 20

      structural_compression:
        enabled: true
        min_cycle_age_bars: 15
        min_confirmed_legs: 2
        local_window: null
        global_median_multiplier: 0.9

      action:
        mode: block_new_entries
```

`candidate_trigger_threshold` remains required for Mode D because the existing
ZigZag stats/materialization pipeline requires it. Mode D must not use it as
an entry gate.

## 5. Config Schema

File:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
```

Add:

```text
_VALID_ZIGZAG_MODES += "D"
exit_off_mode literal += "exit C"
TradeFilterConfig.wakeup_regime
```

Add dataclasses:

```text
TradeFilterWakeupRegimeConfig
TradeFilterWakeupEntryConfig
TradeFilterWakeupCandidateHeightConfig
TradeFilterWakeupCandidateAgeConfig
TradeFilterWakeupAtrExpansionConfig
TradeFilterWakeupVolumeExpansionConfig
TradeFilterWakeupExitConfig
TradeFilterWakeupTtlExitConfig
TradeFilterWakeupNoFreshCandidateExitConfig
TradeFilterWakeupStructuralCompressionExitConfig
TradeFilterWakeupExitActionConfig
```

Extend:

```text
build_trade_filter_config_from_raw
TRADE_FILTER_ALLOWED_KEYS
__all__
```

Whitelist all nested keys under:

```text
trade_filter.wakeup_regime
trade_filter.wakeup_regime.entry
trade_filter.wakeup_regime.entry.candidate_height
trade_filter.wakeup_regime.entry.candidate_age
trade_filter.wakeup_regime.entry.atr_expansion
trade_filter.wakeup_regime.entry.volume_expansion
trade_filter.wakeup_regime.exit
trade_filter.wakeup_regime.exit.ttl
trade_filter.wakeup_regime.exit.no_fresh_candidate
trade_filter.wakeup_regime.exit.structural_compression
trade_filter.wakeup_regime.exit.action
```

## 6. Config Validation

Add a dedicated `_validate_wakeup_regime_block(...)`.

Caller gate:

```text
caller_pipeline="tester":
  Mode D / exit C / wakeup_regime may be valid

caller_pipeline="wf_grid":
  Mode D rejected
  exit C rejected
  wakeup_regime rejected
```

Cross-field guards:

```text
zigzag.mode == "D" requires raw lifecycle.exit_off_mode == "exit C"
zigzag.mode == "D" requires raw wakeup_regime.enabled == true
lifecycle.exit_off_mode == "exit C" requires zigzag.mode == "D"
exit C + exit_off_zz_leg_count is forbidden
exit C + exit_b_immediate_off is forbidden
force_flat is not supported in Phase 0 and must be rejected if present
```

Mode D still requires:

```text
zigzag.reversal_threshold
zigzag.candidate_trigger_threshold
zigzag.local_window
```

Field validation:

```text
all enabled fields: bool only
quantile: numeric finite and 0 < q < 1
windows / bars / max_age_bars / timeout_bars: int >= 1
min_confirmed_legs: int >= 0
min_ratio / global_median_multiplier: finite > 0
volume_expansion.baseline_window >= volume_expansion.short_window
structural_compression.local_window: null or int >= 1
exit.action.mode: exactly "block_new_entries"
```

Disabled component behavior:

```text
entry component disabled -> passes at runtime
exit component disabled  -> cannot trigger
```

Unknown or non-finite runtime data for an enabled entry component must fail
closed and block entry.

## 7. Global Stats

File:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
```

Extend `ZigZagGlobalStats` with semantic wakeup threshold fields:

```text
wakeup_entry_candidate_height_threshold
wakeup_no_fresh_candidate_height_threshold
```

Do not hard-code field names such as `p65` or `p60`. Thresholds must be derived
from the config quantiles:

```text
wakeup_entry_candidate_height_threshold =
  quantile(confirmed_heights_pct, wakeup_regime.entry.candidate_height.quantile)

wakeup_no_fresh_candidate_height_threshold =
  quantile(confirmed_heights_pct, wakeup_regime.exit.no_fresh_candidate.quantile)
```

Apply wakeup quantile checks only when:

```text
zigzag.mode == "D"
and the corresponding wakeup component is enabled
```

Minimum confirmed legs:

```text
min_legs_for_wakeup_quantile = max(zigzag.local_window, 10)
```

If enabled wakeup quantile materialization needs the population and:

```text
n_legs_total < min_legs_for_wakeup_quantile
```

raise `ConfigError`, consistent with existing quantile initialization failures.

Old modes must not fail because of wakeup quantile checks.

## 8. Runtime Inputs And Data Validation

Extend `apply()` backward-compatibly:

```python
high: Optional[np.ndarray] = None
low: Optional[np.ndarray] = None
volume: Optional[np.ndarray] = None
```

Extend `run_backtest_fast(...)` backward-compatibly:

```python
volume: Optional[np.ndarray] = None
```

Wire runtime data:

```text
run_period -> run_single_backtest/run_backtest_fast -> zigzag_st_filter.apply
```

Rules:

```text
high/low required only when mode D and atr_expansion.enabled=true
volume required only when mode D and volume_expansion.enabled=true
old callers without high/low/volume continue to work
```

Validate:

```text
high/low/close length match
volume length matches close when required
high/low finite and positive
volume finite and non-negative
```

The existing `trade_filter.volume` data validator is not enough for wakeup
volume. Add a wakeup-specific volume data check or extend the existing validator
without changing legacy volume behavior.

## 9. Runtime Metrics

Do not duplicate formulas ad hoc inside the FSM loop.

Use existing helpers where possible:

```text
calculate_true_range
calculate_atr_rma
existing rolling aggregate helper from volume_metrics, or a shared extracted helper
```

Add small runtime helpers:

```text
_compute_wakeup_atr_ratio(...)
_compute_wakeup_volume_ratio(...)
_evaluate_wakeup_entry(...)
_evaluate_exit_c(...)
```

ATR expansion:

```text
TR = calculate_true_range(high, low, close)
ATR_short = calculate_atr_rma(TR, short_window)
ATR_long = calculate_atr_rma(TR, long_window)
atr_ratio = ATR_short / ATR_long
```

Entry ATR rule:

```text
bar_index < long_window - 1 -> atr_ok=false
ratio NaN/Inf/non-finite -> atr_ok=false
atr_ok = atr_ratio >= min_ratio
```

Volume expansion:

```text
volume_short = rolling aggregate(volume, short_window)
volume_baseline = rolling aggregate(volume, baseline_window)
volume_ratio = volume_short / volume_baseline
```

Entry volume rule:

```text
warmup -> volume_ok=false
baseline <= 0 -> volume_ok=false
ratio NaN/Inf/non-finite -> volume_ok=false
volume_ok = volume_ratio >= min_ratio
```

## 10. Entry Mode D

Mode D entry is a separate branch. It must not use legacy
`candidate_component_ok`, `candidate_trigger_threshold`, or legacy
`volume_allowed`.

Entry is allowed only when all are true:

```text
state_at_bar_start == OFF
not combined_reset_event[t]
time_filter_in_window[t]
candidate_height_ok
candidate_age_ok
candidate_direction_ok
trade_mode_allows_candidate_direction
atr_ok or atr component disabled
volume_ok or volume component disabled
```

Candidate rules:

```text
candidate_height_ok =
  candidate_height_pct finite
  and candidate_height_pct >= wakeup_entry_candidate_height_threshold

candidate_age_ok =
  candidate_age_bars != -1
  and candidate_age_bars <= max_bars

candidate_direction_ok =
  candidate_leg_direction in {-1, +1}
```

On trigger:

```text
state = ST_ACTIVE_FREEZE
held_pos = candidate_leg_direction
trade_filter_trigger_source = "wakeup_regime"
wakeup_regime_active = 1
wakeup_cycle_age_bars = 0
wakeup_confirmed_legs_since_start = 0
wakeup_bars_since_fresh_candidate = 0 if fresh_candidate_ok else 1
```

Mode D does not enter `WAIT_FIRST_ST_FLIP`.

Open-to-open model remains unchanged:

```text
trigger detected at close(t)
position active at open(t+1)
```

## 11. Mode D FSM Semantics

For Mode D, explicitly bypass:

```text
WAIT_FIRST_ST_FLIP
legacy FREEZE -> MONITORING transition
legacy median-stop lifecycle
exit B leg counting
legacy ST flip reversal update
legacy trade_filter.volume entry gate
```

Wakeup window states:

```text
OFF
ST_ACTIVE_FREEZE
ST_STOPPING
```

`ST_ACTIVE_MONITORING` is not used by Mode D.

Position behavior:

```text
UP candidate   -> long
DOWN candidate -> short
same-direction / neutral ST behavior -> hold
opposite ST flip -> close, not reverse
new entries are possible only after returning to OFF
```

Mode D must never reverse inside an active wakeup window.

## 12. Wakeup Counters

Add runtime counters:

```text
wakeup_cycle_age_bars
wakeup_confirmed_legs_since_start
wakeup_bars_since_fresh_candidate
```

Sentinels:

```text
OFF/reset -> -1
trigger bar -> 0
```

Ordering:

```text
trigger bar has wakeup_cycle_age_bars = 0
Exit C is not evaluated on the trigger bar
next bar has wakeup_cycle_age_bars = 1
ttl.bars = N triggers when wakeup_cycle_age_bars >= N
```

Increment counters while the wakeup window is open:

```text
ST_ACTIVE_FREEZE
ST_STOPPING
```

`confirm_event` increments `wakeup_confirmed_legs_since_start` only when:

```text
wakeup window was already open at bar start
not combined_reset_event[t]
```

## 13. Exit C

Exit C is the only lifecycle exit for the wakeup window, excluding:

```text
combined reset
opposite ST flip position close
```

Exit C is evaluated only while a wakeup window is open:

```text
ST_ACTIVE_FREEZE
ST_STOPPING
```

Exit C conditions are OR with fixed priority:

```text
1. ttl
2. no_fresh_candidate
3. structural_compression
```

TTL:

```text
ttl_triggered =
  ttl.enabled
  and wakeup_cycle_age_bars >= ttl.bars
```

No fresh candidate:

```text
fresh_candidate_ok =
  candidate_height_pct finite
  and candidate_height_pct >= wakeup_no_fresh_candidate_height_threshold
  and candidate_age_bars != -1
  and candidate_age_bars <= max_age_bars

on each open-window bar:
  if fresh_candidate_ok:
    wakeup_bars_since_fresh_candidate = 0
  else:
    wakeup_bars_since_fresh_candidate += 1

no_fresh_candidate_triggered =
  no_fresh_candidate.enabled
  and wakeup_bars_since_fresh_candidate >= timeout_bars
```

Structural compression:

```text
effective_local_window =
  structural_compression.local_window
  or trade_filter.zigzag.local_window

structural_triggered =
  structural_compression.enabled
  and wakeup_cycle_age_bars >= min_cycle_age_bars
  and wakeup_confirmed_legs_since_start >= min_confirmed_legs
  and local_median_N is available and finite
  and local_median_N < global_median * global_median_multiplier
```

If `local_median_N` is unavailable, structural compression does not trigger.

On Exit C:

```text
wakeup_exit_reason = selected reason
new entries forbidden
reversals forbidden
```

If current position exists:

```text
state = ST_STOPPING
hold position until opposite ST flip
```

If no current position exists:

```text
state = OFF
wakeup counters reset to -1
```

`force_flat` is out of scope and rejected by config validation.

## 14. Reset Behavior

On `combined_reset_event[t]`:

```text
if wakeup window was open:
  wakeup_exit_reason = "reset"

state = OFF
held_pos = 0
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
wakeup_confirmed_legs_since_start = -1
```

The reset bar cannot open a new wakeup window.

## 15. Opposite ST Flip Behavior

While Mode D wakeup window is open:

```text
opposite ST flip closes the current position
wakeup_exit_reason = "opposite_st_flip"
state becomes OFF after the close is materialized
counters reset to -1 after returning to OFF
```

Same-direction flips and neutral transitions do not change `held_pos`.

No Mode D internal reversal is allowed.

## 16. Diagnostics

Add per-bar diagnostics:

```text
wakeup_regime_active
wakeup_entry_all_ok

wakeup_entry_candidate_height_ok
wakeup_entry_candidate_age_ok
wakeup_entry_candidate_direction_ok
wakeup_entry_trade_mode_ok
wakeup_entry_atr_ok
wakeup_entry_volume_ok

wakeup_entry_candidate_height_value
wakeup_entry_candidate_height_threshold
wakeup_entry_candidate_age_bars
wakeup_entry_candidate_leg_direction
wakeup_entry_atr_ratio
wakeup_entry_volume_ratio

wakeup_cycle_age_bars
wakeup_bars_since_fresh_candidate
wakeup_confirmed_legs_since_start

wakeup_exit_ttl_triggered
wakeup_exit_no_fresh_candidate_triggered
wakeup_exit_structural_compression_triggered
wakeup_exit_reason
```

Allowed `wakeup_exit_reason` values:

```text
none
ttl
no_fresh_candidate
structural_compression
reset
opposite_st_flip
```

Update explicit Excel display names in:

```text
donor/supertrend_optimizer/io/excel_tester.py
```

Do not rely only on automatic dict iteration for readable exports.

## 17. Tester Summary

Extend tester summary counters:

```text
wakeup_starts_count
wakeup_exit_ttl_count
wakeup_exit_no_fresh_candidate_count
wakeup_exit_structural_compression_count
wakeup_exit_reset_count
wakeup_exit_opposite_st_flip_count
wakeup_bars_active
```

Expose in:

```text
filter_diagnostics_summary
filters_summary sheet
```

Summary counts should be derived from bar diagnostics. Trade-level
`exit_reason` does not need to be expanded in Phase 0 unless explicitly tested.

## 18. Signal Events And Trade Diagnostics

Keep existing signal/trade exports backward-compatible.

If a new block reason or trigger source is consumed by signal exports, update
the explicit mapping. Otherwise, keep wakeup-specific proof in diagnostics and
summary.

Trade-level `exit_reason` may remain legacy-compatible in Phase 0. If expanded,
add dedicated tests for wakeup exit reason mapping.

## 19. Tests

### 19.1 Config Schema And Caller Gate

```text
tester accepts D + exit C + wakeup_regime.enabled=true
tester rejects D without raw exit C
tester rejects D without raw wakeup_regime.enabled=true
tester rejects exit C with mode != D
tester rejects exit C + exit_off_zz_leg_count
tester rejects exit C + exit_b_immediate_off
tester rejects force_flat
wf_grid rejects Mode D
wf_grid rejects exit C
wf_grid rejects wakeup_regime
old modes/exits remain accepted
```

### 19.2 Config Field Validation

Cover invalid types/ranges for every new field:

```text
enabled values
quantiles
windows
bars
ratios
global_median_multiplier
action.mode
volume baseline_window < short_window
structural_compression.local_window
```

### 19.3 Global Stats

```text
entry candidate-height threshold computed from configured quantile
no-fresh threshold computed from configured quantile
min-leg failure only applies to enabled Mode D wakeup quantiles
old modes do not fail because of wakeup quantile checks
```

### 19.4 Runtime Metrics

```text
ATR ratio uses TR + RMA helper
ATR warmup blocks until long_window - 1
ATR NaN/Inf/non-finite blocks
volume ratio uses rolling aggregate helper
volume warmup blocks
volume baseline zero blocks
volume NaN/Inf/non-finite blocks
disabled entry components pass
```

### 19.5 Entry Mode D

```text
candidate height gates entry
candidate age gates entry
candidate direction gates entry
trade mode gates direction
ATR gate gates entry
volume gate gates entry
all components combine as AND
candidate_age == -1 blocks
candidate_direction == 0 blocks
time_filter out of window blocks
combined reset bar blocks
legacy candidate_trigger_threshold does not gate Mode D entry
legacy trade_filter.volume gate does not block Mode D entry
```

### 19.6 FSM And Position Behavior

```text
Mode D enters immediately from OFF
no WAIT_FIRST_ST_FLIP
UP candidate opens long
DOWN candidate opens short
position appears at t+1 under open-to-open model
same-direction ST behavior holds
opposite ST flip closes position
no internal reversals
no ST_ACTIVE_MONITORING for Mode D
legacy median-stop does not close wakeup window
```

### 19.7 Wakeup Counters

```text
trigger bar counters are 0
next bar age is 1
confirm_event increments confirmed legs only after window is already open
OFF/reset counters are -1
ST_STOPPING continues age/no-fresh counters
TTL boundary has no off-by-one
```

### 19.8 Exit C

```text
TTL triggers
no_fresh_candidate triggers
structural_compression triggers
priority: ttl > no_fresh_candidate > structural_compression
Exit C with position enters ST_STOPPING
Exit C with no position returns OFF
ST_STOPPING holds until opposite ST flip
ST_STOPPING forbids new entries/reversals
```

### 19.9 Reset

```text
combined reset closes wakeup window
reset writes wakeup_exit_reason="reset" when it closes active wakeup window
reset clears counters to -1
reset bar cannot open new wakeup window
```

### 19.10 Diagnostics And Summary

```text
all wakeup diagnostics present
diagnostics length equals n
expected dtype/object values
FilterDiagnostics_100 display names include wakeup fields
filter_diagnostics_summary includes wakeup counters
filters_summary includes wakeup counters
wakeup_starts_count matches trigger source count
wakeup_bars_active matches wakeup_regime_active sum
```

### 19.11 Regression

```text
Mode A unchanged
Mode B unchanged
Mode C unchanged
Mode A+B unchanged
Mode C+B unchanged
exit A unchanged
exit B unchanged
exit_b_immediate_off unchanged
time_filter unchanged
daily_reset unchanged
trade_filter.volume unchanged
old apply() callers without high/low/volume still work
```

## 20. Final Verification

Run targeted suites:

```text
donor TESTER config/schema tests
donor TESTER ZigZag runtime tests
donor TESTER Excel/summary tests
shared wf_grid schema tests touched by trade_filter_config.py
existing ZigZag FSM/global stats regression tests
existing volume gate regression tests
```

Run a single-config tester scenario:

```text
zigzag.mode: D
lifecycle.exit_off_mode: "exit C"
wakeup_regime.enabled: true
```

Success criteria:

```text
wakeup_starts_count > 0
entry happens without waiting for ST flip
positions follow candidate_leg_direction
Exit C closes wakeup window
ST_STOPPING blocks new entries and reversals
opposite ST flip closes without reversing
old modes/exits are unchanged
wf_grid rejects Phase 0 wakeup config
```

## 21. Implementation Order

1. Baseline tests and current-state notes.
2. Config schema dataclasses and whitelist.
3. Caller gate and full config validation.
4. Config tests.
5. Global stats fields and conditional quantile materialization.
6. Global stats tests.
7. Runtime data plumbing and validation.
8. Wakeup metric helpers.
9. Entry Mode D helper and tests.
10. FSM Mode D bypass and position behavior.
11. Wakeup counters.
12. Exit C lifecycle.
13. Reset and opposite ST flip semantics.
14. Diagnostics fields.
15. Tester summary and Excel display names.
16. Regression tests for old behavior.
17. Single-config tester verification.

## 22. Out Of Scope

Do not implement in Phase 0:

```text
WF Grid runtime support for Mode D
WF export support for wakeup fields
parallel runtime transport changes
WF OOS validation
force_flat
production effective-config source tracking
trade-level exit_reason expansion unless separately tested
full Excel polish beyond required diagnostics/summary fields
signal_events redesign
```

## 23. Main Risks And Mitigations

Risk:

```text
Mode D accidentally becomes accepted by WF Grid.
```

Mitigation:

```text
caller_pipeline gate plus wf_grid reject tests.
```

Risk:

```text
Mode D leaks into legacy FSM paths and gets WAIT, MONITORING, median-stop,
or internal reversals.
```

Mitigation:

```text
explicit Mode D bypass tests for every forbidden legacy path.
```

Risk:

```text
Exit C counters have off-by-one behavior.
```

Mitigation:

```text
pin trigger bar age=0, next bar age=1, and TTL boundary tests.
```

Risk:

```text
ATR/volume wakeup metrics diverge from existing engine semantics.
```

Mitigation:

```text
reuse existing helpers and test warmup/non-finite behavior.
```

Risk:

```text
Runtime works but tester evidence is incomplete.
```

Mitigation:

```text
explicit diagnostics, summary, and Excel contract tests.
```
