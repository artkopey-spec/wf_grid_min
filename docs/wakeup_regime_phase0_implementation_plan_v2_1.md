# Wakeup Regime Phase 0 Implementation Plan v2.1

## 1. Goal

Implement a tester-only Phase 0 prototype of `wakeup_regime` in the donor
SuperTrend/ZigZag filter.

The prototype validates this trading hypothesis:

```text
fresh ZigZag candidate impulse + ATR expansion + volume expansion
-> immediate entry in the candidate leg direction
-> limited wakeup supervision window
-> window shutdown by Exit C when impulse ages or structure compresses
```

Phase 0 is not a WF Grid feature. It is a reusable donor-core prototype with a
hard caller gate that prevents unsupported runtime use outside the tester.

## 2. Source Of Truth

This document is the implementation source of truth for Phase 0.

Wakeup global-stat thresholds are config-derived semantic fields:

```text
wakeup_entry_candidate_height_threshold
wakeup_no_fresh_candidate_height_threshold
```

Do not introduce hard-coded global-stat fields such as `wakeup_p65_height` or
`wakeup_p60_height`. The default example quantiles are `0.65` and `0.60`, but
the implementation must materialize thresholds from the configured quantiles.

## 3. Scope And Boundaries

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

`donor/supertrend_optimizer/core/trade_filter_config.py` is shared by donor
tester and WF Grid. Therefore any schema extension must include this gate:

```text
caller_pipeline="tester"  -> Mode D / exit C / wakeup_regime may be valid
caller_pipeline="wf_grid" -> Mode D / exit C / wakeup_regime rejected
```

Adding `wakeup_regime` to the global whitelist makes the keys known to WF Grid,
so the validator gate is mandatory. Unknown-key rejection is no longer enough
once the whitelist is extended.

## 4. Effort And Delivery Expectation

This is an invasive FSM change, not a small config-only feature.

Expected scope for one engineer:

```text
1.5-3 weeks implementation + test hardening + review
```

Largest risk area:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py apply() loop
```

The config schema, validation, and global stats work are comparatively low
risk. The FSM insertion and diagnostics contracts are high risk.

## 5. Baseline

Before implementation:

1. Run current donor tester baseline tests.
2. Run targeted existing ZigZag/filter tests.
3. Run the strict diagnostics dtype/keyset contract test.
4. Record current passing set and existing unrelated failures.

Preserve behavior for:

```text
ZigZag modes: A, B, C, A+B, C+B
exit modes: exit A, exit B
exit_b_immediate_off
time_filter
daily_reset
trade_filter.volume
old apply() callers without high/low/volume
```

## 6. Target Config Shape

Supported tester config:

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
ZigZag global-stats pipeline requires materialized threshold fields. Mode D
must not use it as an entry gate.

## 7. Config Schema

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

`exit.action.mode` has one supported value in Phase 0:

```text
block_new_entries
```

Keep this simple. Do not add a `force_flat` schema key in Phase 0. If a config
contains `force_flat`, it should be rejected by the existing unknown-key
mechanism.

Extend:

```text
build_trade_filter_config_from_raw
TRADE_FILTER_ALLOWED_KEYS
__all__
```

Whitelist all nested wakeup keys:

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

## 8. Config Validation

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

## 9. Global Stats

File:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
```

Extend frozen `ZigZagGlobalStats` with defaulted fields:

```text
wakeup_entry_candidate_height_threshold: float = NaN
wakeup_no_fresh_candidate_height_threshold: float = NaN
```

Materialize only when needed:

```text
zigzag.mode == "D"
and the corresponding wakeup component is enabled
```

Formulas:

```text
wakeup_entry_candidate_height_threshold =
  quantile(confirmed_heights_pct, wakeup_regime.entry.candidate_height.quantile)

wakeup_no_fresh_candidate_height_threshold =
  quantile(confirmed_heights_pct, wakeup_regime.exit.no_fresh_candidate.quantile)
```

Minimum confirmed legs:

```text
min_legs_for_wakeup_quantile = max(zigzag.local_window, 10)
```

If an enabled wakeup quantile needs the population and:

```text
n_legs_total < min_legs_for_wakeup_quantile
```

raise `ConfigError`, consistent with existing quantile initialization failures.

Old modes must not fail because of wakeup quantile checks.

Add wakeup thresholds and quantiles to `metadata["config_snapshot"]`.

## 10. Runtime Data Plumbing

Wakeup ATR needs `high/low`. Wakeup volume needs raw `volume`. Existing
`trade_filter.volume` uses `VolumeRuntime`, but wakeup volume must not depend
on `trade_filter.volume`.

Extend the full call chain:

```text
testing.runner.run_period(..., wakeup_volume=None)
engine.run.run_single_backtest(..., volume=None)
core.backtest.run_backtest_fast(..., volume=None)
core.zigzag_st_filter.apply(..., high=None, low=None, volume=None)
```

The tester source of raw volume is the loaded DataFrame:

```text
df["volume"].to_numpy()
```

Rules:

```text
high/low required only when mode D and atr_expansion.enabled=true
volume required only when mode D and volume_expansion.enabled=true
old callers without high/low/volume continue to work
```

Validation:

```text
high/low/close length match
volume length matches close when required
high/low finite and positive
volume finite and non-negative
```

Add a helper such as:

```text
is_wakeup_volume_required(trade_filter_config)
validate_wakeup_regime_data(df, trade_filter_config)
```

Do not change legacy `trade_filter.volume` behavior.

## 11. ATR And Volume Reset Semantics

Phase 0 decision:

```text
ATR ratio and wakeup volume ratio are full-slice causal rolling series.
They do not reset on daily_reset or time_filter reset.
```

Rationale:

```text
combined_reset_event wipes ZigZag candidate state and the FSM lifecycle.
It does not restart market-wide volatility/volume context in Phase 0.
```

Implication:

```text
ATR/volume warmup is measured from the start of the current data slice,
not from the most recent reset event.
```

This must be covered by at least one reset test so future work cannot silently
change the semantics.

## 12. Runtime Metrics

Do not duplicate formulas ad hoc inside the FSM loop.

Use existing helpers:

```text
calculate_true_range
calculate_atr_rma
existing rolling aggregate helper from volume_metrics, or a shared extracted helper
```

Add small helpers:

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

## 13. Mode D FSM Design

Mode D must not be implemented by extending the legacy lifecycle helper with
many new parameters. Keep legacy and wakeup entry decisions separate.

Add precomputed booleans:

```text
resolved_mode == "D" -> is_mode_d
lifecycle.exit_off_mode == "exit C" -> is_exit_c
is_wakeup_window_open -> wakeup_regime_active == 1
```

Mode D window states:

```text
OFF
ST_ACTIVE_FREEZE
ST_STOPPING
```

Mode D does not use:

```text
WAIT_FIRST_ST_FLIP
ST_ACTIVE_MONITORING
ST_COUNTING_ZZ_LEGS
```

Mode D must not use legacy:

```text
candidate_component_ok as entry gate
candidate_trigger_threshold as entry gate
legacy trade_filter.volume volume_allowed gate
FREEZE -> MONITORING transition
median-stop lifecycle
exit B leg counting
_update_held_pos reversal path
```

## 14. Required FSM Gate Map

Before editing the FSM loop, create and review a local checklist covering every
legacy path that must be gated or bypassed.

Minimum gate map:

1. **Combined reset wipe**
   - If Mode D wakeup window was open before the wipe, write
     `wakeup_exit_reason = "reset"`.
   - Reset wakeup counters and active flag.

2. **Legacy mode primitives**
   - Existing `candidate_threshold_ok`, `candidate_component_ok`,
     `confirmed_median_ok`, `b_component_ok` may still be computed for legacy
     diagnostics.
   - They must not drive Mode D entry.

3. **Lifecycle start dispatcher**
   - If `is_mode_d`, call `_evaluate_wakeup_entry(...)`.
   - Else call existing `_try_lifecycle_start_from_off(...)`.
   - Do not expand `_try_lifecycle_start_from_off(...)` with wakeup ATR/volume
     parameters.

4. **Immediate candidate diagnostics**
   - Keep `immediate_candidate_entry_*` as legacy Mode C diagnostics.
   - Do not overload them for Mode D.
   - Mode D evidence is in `wakeup_entry_*` diagnostics and summary counters.

5. **WAIT -> FREEZE**
   - Mode D must never enter WAIT.
   - Existing WAIT transition remains legacy-only.

6. **Confirmed-leg counters**
   - Existing `confirmed_legs_since_start` is not authoritative for Mode D.
   - `wakeup_confirmed_legs_since_start` is the Mode D lifecycle counter.
   - Existing counter must not push Mode D into MONITORING.

7. **FREEZE -> MONITORING**
   - Gate with `not is_mode_d`.

8. **Median-stop lifecycle**
   - Gate with `not is_mode_d`.

9. **Exit B leg-count lifecycle**
   - Must remain impossible for Mode D by validation and runtime guards.

10. **ST flip update in active states**
    - Gate legacy `_update_held_pos(...)` with `not is_mode_d`.
    - Add Mode D opposite-flip close logic instead.

11. **Position write for t+1**
    - For Mode D active window, write `held_pos` unless opposite flip has closed
      the position.
    - For Mode D close, write `0` at `t+1` under the existing open-to-open model.

12. **ST_STOPPING normalization**
    - If Mode D reaches `ST_STOPPING` with no current position, return to OFF and
      reset wakeup counters.

13. **Block reasons**
    - Reuse existing `stopping_mode_no_new_entries` for blocked entries while
      Mode D is in `ST_STOPPING`.
    - Do not add new block-reason strings unless signal-event mappings and tests
      are updated.

14. **Diagnostics persist**
    - Persist wakeup diagnostics after all same-bar state transitions are known.

No implementation should start until this map is converted into concrete code
touch points with tests.

## 15. Entry Mode D

Mode D entry is allowed only when all are true:

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

Open-to-open model remains unchanged:

```text
trigger detected at close(t)
position active at open(t+1)
```

## 16. Mode D ST Flip Semantics

This is new behavior, not just a bypass of legacy reversal logic.

While Mode D wakeup window is open:

```text
same-direction ST flip -> hold
neutral / initialization transition -> hold
opposite ST flip -> close position, not reverse
```

Opposite flip handling must work from both:

```text
ST_ACTIVE_FREEZE
ST_STOPPING
```

On opposite flip:

```text
wakeup_exit_reason = "opposite_st_flip"
held_pos = 0
state = OFF after close is materialized
wakeup counters reset to -1 after returning to OFF
```

Under open-to-open execution:

```text
opposite flip detected at close(t)
position becomes flat at open(t+1)
```

If this logic is omitted and `_update_held_pos` is merely disabled, Mode D
positions can hang until Exit C. Tests must catch that failure.

## 17. Wakeup Counters

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

Increment counters while wakeup window is open:

```text
ST_ACTIVE_FREEZE
ST_STOPPING
```

`confirm_event` increments `wakeup_confirmed_legs_since_start` only when:

```text
wakeup window was already open at bar start
not combined_reset_event[t]
```

Existing `confirmed_legs_since_start` may remain populated for legacy
diagnostics, but it is ignored by Mode D lifecycle logic.

## 18. Exit C

Exit C is the only lifecycle exit for the wakeup window, excluding:

```text
combined reset
opposite ST flip position close
```

Exit C is evaluated while a wakeup window is open:

```text
ST_ACTIVE_FREEZE
ST_STOPPING
```

Exit C is not evaluated on the trigger bar.

Exit conditions are OR with fixed priority:

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

## 19. Reset Behavior

On `combined_reset_event[t]`:

```text
if wakeup window was open at bar start:
  wakeup_exit_reason = "reset"

state = OFF
held_pos = 0
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
wakeup_confirmed_legs_since_start = -1
```

The reset bar cannot open a new wakeup window.

ATR/volume ratios do not restart warmup after reset in Phase 0.

## 20. Diagnostics Contract

Decision:

```text
wakeup_* diagnostics are emitted only when resolved_mode == "D".
```

Rationale:

```text
Old modes keep their existing strict diagnostics keyset.
Mode D has an explicit extended diagnostics keyset.
```

Update the strict diagnostics contract tests to support:

```text
base ZigZag diagnostics keyset for A/B/C/A+B/C+B
base + wakeup extension keyset for D
```

Update dtype/object expectations for the wakeup extension.

Mode D wakeup diagnostics:

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

Update explicit Excel display names:

```text
donor/supertrend_optimizer/io/excel_tester.py
FILTER_DIAGNOSTICS_100_DISPLAY_NAMES
```

Do not rely only on automatic dict iteration for readable exports.

## 21. Tester Summary And Thresholds Contract

Extend summary counters:

```text
wakeup_starts_count
wakeup_exit_ttl_count
wakeup_exit_no_fresh_candidate_count
wakeup_exit_structural_compression_count
wakeup_exit_reset_count
wakeup_exit_opposite_st_flip_count
wakeup_bars_active
```

Extend `thresholds` / filter summary payload with:

```text
wakeup_entry_candidate_height_quantile
wakeup_entry_candidate_height_threshold
wakeup_no_fresh_candidate_quantile
wakeup_no_fresh_candidate_height_threshold
wakeup_atr_short_window
wakeup_atr_long_window
wakeup_atr_min_ratio
wakeup_volume_short_window
wakeup_volume_baseline_window
wakeup_volume_min_ratio
wakeup_ttl_bars
wakeup_no_fresh_max_age_bars
wakeup_no_fresh_timeout_bars
wakeup_structural_min_cycle_age_bars
wakeup_structural_min_confirmed_legs
wakeup_structural_local_window
wakeup_structural_global_median_multiplier
wakeup_exit_action_mode
```

Expose in:

```text
filter_diagnostics_summary
filters_summary sheet
```

Summary counts must be derived from bar diagnostics, not from trade-level
`exit_reason`.

Trade-level `exit_reason` does not need to be expanded in Phase 0 unless a
dedicated test is added.

## 22. Signal Events And Trade Diagnostics

Keep existing signal/trade exports backward-compatible.

Do not add new `filter_block_reason` strings for wakeup unless:

```text
testing/signal_events.py mapping is updated
tests are added for the new strings
```

`trade_filter_trigger_source = "wakeup_regime"` is allowed for Mode D entries.
If signal/event exports display trigger sources, they should pass this string
through without special interpretation.

## 23. Tests

### 23.1 Config Schema And Caller Gate

```text
tester accepts D + exit C + wakeup_regime.enabled=true
tester rejects D without raw exit C
tester rejects D without raw wakeup_regime.enabled=true
tester rejects exit C with mode != D
tester rejects exit C + exit_off_zz_leg_count
tester rejects exit C + exit_b_immediate_off
tester rejects unknown force_flat key
wf_grid rejects Mode D
wf_grid rejects exit C
wf_grid rejects wakeup_regime
old modes/exits remain accepted
```

### 23.2 Config Field Validation

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

### 23.3 Global Stats

```text
entry candidate-height threshold computed from configured quantile
no-fresh threshold computed from configured quantile
min-leg failure only applies to enabled Mode D wakeup quantiles
old modes do not fail because of wakeup quantile checks
metadata includes wakeup threshold snapshot
```

### 23.4 Runtime Plumbing And Data Validation

```text
run_period passes raw volume when wakeup volume is required
run_single_backtest accepts/passes volume
run_backtest_fast accepts/passes volume
apply accepts high/low/volume
missing volume fails only when wakeup volume gate enabled
missing high/low fails only when wakeup ATR gate enabled
old callers without new args still work
```

### 23.5 Runtime Metrics

```text
ATR ratio uses TR + RMA helper
ATR warmup blocks until long_window - 1
ATR NaN/Inf/non-finite blocks
volume ratio uses rolling aggregate helper
volume warmup blocks
volume baseline zero blocks
volume NaN/Inf/non-finite blocks
ATR/volume do not reset warmup after combined reset
disabled entry components pass
```

### 23.6 Entry Mode D

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

### 23.7 FSM Gate Map Tests

Add one bypass test for each forbidden legacy path:

```text
Mode D never enters WAIT_FIRST_ST_FLIP
Mode D never enters ST_ACTIVE_MONITORING
Mode D never enters ST_COUNTING_ZZ_LEGS
Mode D does not trigger median_stop_triggered
Mode D does not use exit B leg-count stop
Mode D does not call legacy reversal behavior on ST flip
```

### 23.8 Position Behavior

```text
Mode D enters immediately from OFF
UP candidate opens long
DOWN candidate opens short
position appears at t+1 under open-to-open model
same-direction ST behavior holds
opposite ST flip closes from ST_ACTIVE_FREEZE
opposite ST flip closes from ST_STOPPING
opposite ST flip does not reverse
```

### 23.9 Wakeup Counters

```text
trigger bar counters are 0
next bar age is 1
confirm_event increments wakeup confirmed legs only after window is already open
OFF/reset counters are -1
ST_STOPPING continues age/no-fresh counters
TTL boundary has no off-by-one
legacy confirmed_legs_since_start does not drive Mode D lifecycle
```

### 23.10 Exit C

```text
TTL triggers
no_fresh_candidate triggers
structural_compression triggers
priority: ttl > no_fresh_candidate > structural_compression
Exit C with position enters ST_STOPPING
Exit C with no position returns OFF
ST_STOPPING holds until opposite ST flip
ST_STOPPING forbids new entries/reversals
Exit C is not evaluated on trigger bar
```

### 23.11 Reset

```text
combined reset closes wakeup window
reset writes wakeup_exit_reason="reset" when it closes active wakeup window
reset clears counters to -1
reset bar cannot open new wakeup window
ATR/volume warmup does not restart after reset
```

### 23.12 Diagnostics And Summary

```text
old modes keep base diagnostics keyset
Mode D gets base + wakeup diagnostics keyset
wakeup diagnostics length equals n
wakeup diagnostics dtype/object expectations are pinned
FilterDiagnostics_100 display names include wakeup fields
filter_diagnostics_summary includes wakeup counters
thresholds summary includes wakeup thresholds/config
filters_summary includes wakeup counters and thresholds
wakeup_starts_count matches trigger source count
wakeup_bars_active matches wakeup_regime_active sum
```

### 23.13 Regression

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

## 24. Final Verification

Run targeted suites:

```text
donor TESTER config/schema tests
donor TESTER diagnostics dtype/keyset contract
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
old modes keep base diagnostics keyset
Mode D has extended diagnostics keyset
wf_grid rejects Phase 0 wakeup config
```

## 25. Implementation Order

1. Baseline tests and current-state notes.
2. Convert the FSM gate map into concrete code touch points.
3. Config schema dataclasses and whitelist.
4. Caller gate and full config validation.
5. Config tests, including wf_grid rejects.
6. Global stats fields and conditional quantile materialization.
7. Global stats tests.
8. Runtime data plumbing through all signatures.
9. Wakeup-specific data validation.
10. Wakeup metric helpers.
11. Entry Mode D helper and tests.
12. FSM Mode D bypass and opposite-flip close behavior.
13. Wakeup counters.
14. Exit C lifecycle.
15. Reset behavior and ATR/volume reset-semantics tests.
16. Mode D diagnostics extension and strict keyset tests.
17. Tester thresholds/counters summary.
18. Excel display names and filters_summary.
19. Regression tests for old behavior.
20. Single-config tester verification.

## 26. Out Of Scope

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
ATR/volume reset-aware rolling metrics
```

## 27. Main Risks And Mitigations

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
exit B counting, or internal reversals.
```

Mitigation:

```text
mandatory FSM gate map plus one bypass test per legacy path.
```

Risk:

```text
Opposite ST flip from ST_ACTIVE_FREEZE does not close, or reverses.
```

Mitigation:

```text
explicit additive Mode D close logic and tests from FREEZE and STOPPING.
```

Risk:

```text
Strict diagnostics keyset/dtype contract breaks unexpectedly.
```

Mitigation:

```text
conditional base vs Mode D extended keyset tests.
```

Risk:

```text
Raw volume does not reach apply().
```

Mitigation:

```text
explicit full plumbing chain tests.
```

Risk:

```text
ATR/volume reset semantics drift.
```

Mitigation:

```text
Phase 0 full-slice causal decision and reset tests.
```

Risk:

```text
Runtime works but tester evidence is incomplete.
```

Mitigation:

```text
explicit diagnostics, thresholds summary, counters summary, and Excel tests.
```
