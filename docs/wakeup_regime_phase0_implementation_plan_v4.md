# Wakeup Regime Phase 0 - Implementation Plan v4

## 1. Цель

Реализовать прототип `wakeup_regime` для `donor` tester и проверить гипотезу:

```text
свежий price impulse + ATR expansion + volume expansion
-> немедленный вход в направлении ZigZag candidate leg
-> ограниченное wakeup window
-> остановка новых входов, когда impulse устарел или структура сжалась
```

Phase 0 не является production runtime для WF Grid. Код при этом пишется как reusable core, потому что текущий donor и `wf_grid` используют общий config/runtime слой.

Главный критерий успеха: single-config tester способен запустить `zigzag.mode: D`, открыть позиции без ожидания SuperTrend flip, показать `wakeup_starts_count > 0`, корректно закрывать wakeup window через Exit C и не изменить поведение старых modes/exits.

## 2. Scope

Разрешено менять:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/backtest.py
donor/supertrend_optimizer/engine/run.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/testing/signal_events.py
donor/supertrend_optimizer/io/excel_tester.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/cli/tester.py
donor tests
wf_grid reject/schema tests that exercise the shared donor validator
```

Разрешены точечные изменения `wf_grid` только там, где он уже вызывает shared donor config validation. Runtime support Mode D в `wf_grid` запрещен.

Запрещено в Phase 0:

```text
WF Grid runtime support for Mode D
WF export wakeup fields
parallel runtime transport changes
WF OOS validation
production effective-config source tracking
force_flat
trade-level exit_reason redesign beyond compatibility additions
```

## 3. Existing Architecture Constraints

Текущая система уже имеет плотные контракты:

- `wf_grid/config/loader.py` delegates validation to `donor/supertrend_optimizer/core/trade_filter_config.py` with `caller_pipeline="wf_grid"`.
- `zigzag_st_filter.apply()` owns FSM, positions and diagnostics for modes `A`, `B`, `C`, `A+B`, `C+B`.
- Tester summary and Excel expect common diagnostics keys such as `trade_filter_state`, `trade_filter_trigger_source`, `filter_allowed_entry`, `filter_block_reason`, `median_stop_triggered`.
- Existing signal events are ST-flip-oriented, while Mode D entry is candidate-leg-oriented.
- Existing standalone volume filter uses `VolumeRuntime`; wakeup volume expansion is a separate entry component and must not reuse or be blocked by the old `trade_filter.volume` gate.

Phase 0 implementation must preserve these contracts.

## 4. Architectural Principle

Mode D must be isolated from the legacy FSM loop.

`apply()` flow:

```text
apply()
  pre-loop common setup:
    - validate trend/close/index
    - infer daily_reset_event
    - infer time_filter events
    - compute combined_reset_event
    - compute ZigZagPerBar
    - resolve mode from ZigZagGlobalStats

  if resolved_mode == "D":
    return _run_wakeup_fsm(...)

  else:
    run existing legacy FSM loop unchanged for A/B/C/A+B/C+B
```

The legacy loop must not run for Mode D. `_try_lifecycle_start_from_off()` must not receive `"D"`. This avoids accidental interaction with WAIT, legacy median stop, Exit B and old volume gate.

Mode D can share small helpers only when the helper has a narrow, tested invariant:

- open-to-open position write;
- common reset wipe;
- common diagnostics allocation;
- common trade-mode direction guard.

Do not refactor the whole legacy loop during Phase 0.

## 5. Config Model

### 5.1 Target YAML

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

### 5.2 Mandatory Phase 0 Restrictions

`candidate_trigger_threshold` is required for the existing ZigZag global/per-bar pipeline, but Mode D does not use it for entry.

To avoid hidden legacy quantile failures in Phase 0:

```text
Mode D requires candidate_trigger_threshold to be numeric.
Mode D rejects candidate_trigger_threshold: "auto".
Mode D rejects candidate_trigger_quantile.
```

`structural_compression.local_window`:

```text
Phase 0 accepts only null.
null means use trade_filter.zigzag.local_window and the already computed local_median_N.
Non-null local_window is rejected with ConfigError.
```

This avoids adding a second local median stream in Phase 0.

### 5.3 Dataclasses

Add dataclasses in `trade_filter_config.py`:

```text
TradeFilterWakeupRegimeConfig(enabled, entry, exit)
TradeFilterWakeupEntryConfig(candidate_height, candidate_age, atr_expansion, volume_expansion)
TradeFilterWakeupCandidateHeightConfig(enabled, quantile)
TradeFilterWakeupCandidateAgeConfig(enabled, max_bars)
TradeFilterWakeupAtrExpansionConfig(enabled, short_window, long_window, min_ratio)
TradeFilterWakeupVolumeExpansionConfig(enabled, short_window, baseline_window, min_ratio)
TradeFilterWakeupExitConfig(ttl, no_fresh_candidate, structural_compression, action)
TradeFilterWakeupTtlExitConfig(enabled, bars)
TradeFilterWakeupNoFreshCandidateExitConfig(enabled, quantile, max_age_bars, timeout_bars)
TradeFilterWakeupStructuralCompressionExitConfig(enabled, min_cycle_age_bars, min_confirmed_legs, local_window, global_median_multiplier)
TradeFilterWakeupExitActionConfig(mode)
```

All numeric fields default to `None`. Defaults must not silently encode strategy parameters. Validator enforces presence for enabled components.

Add to `TradeFilterConfig`:

```text
wakeup_regime: Optional[TradeFilterWakeupRegimeConfig] = None
```

Extend:

- `_VALID_ZIGZAG_MODES` with `"D"`;
- lifecycle literal validation with `"exit C"`;
- `build_trade_filter_config_from_raw`;
- `TRADE_FILTER_ALLOWED_KEYS`;
- `__all__`.

## 6. Config Validation

Add `_validate_wakeup_regime_block(tf, errors, raw_user_keys, caller_pipeline, error_keys)`.

### 6.1 Caller Gate

```text
caller_pipeline == "tester":
  Mode D + exit C + wakeup_regime allowed

caller_pipeline == "wf_grid":
  zigzag.mode == "D" -> ConfigError
  lifecycle.exit_off_mode == "exit C" -> ConfigError
  trade_filter.wakeup_regime present -> ConfigError
```

This is an explicit exception to "donor only": shared validator must protect `wf_grid`.

### 6.2 Cross-field Guards

```text
zigzag.mode == "D" requires lifecycle.exit_off_mode == "exit C"
zigzag.mode == "D" requires raw lifecycle.exit_off_mode key to be present
zigzag.mode == "D" requires wakeup_regime.enabled == true
zigzag.mode == "D" rejects candidate_trigger_threshold == "auto"
zigzag.mode == "D" rejects candidate_trigger_quantile

lifecycle.exit_off_mode == "exit C" requires zigzag.mode == "D"
exit C rejects exit_off_zz_leg_count
exit C rejects exit_b_immediate_off
exit C rejects legacy triggers block

zigzag.mode != "D" rejects wakeup_regime when wakeup_regime is present
```

Old modes `A`, `B`, `C`, `A+B`, `C+B` remain valid with Exit A/B exactly as today.

### 6.3 Field Validation

Validate strictly:

```text
enabled: bool
quantile: finite float, 0 < q < 1
bars, max_bars, max_age_bars, timeout_bars, min_cycle_age_bars,
short_window, long_window, baseline_window: int >= 1
min_confirmed_legs: int >= 0
min_ratio, global_median_multiplier: finite float > 0
atr_expansion.long_window >= atr_expansion.short_window
volume_expansion.baseline_window >= volume_expansion.short_window
structural_compression.local_window: must be null in Phase 0
exit.action.mode: exactly "block_new_entries"
```

Enabled component rules:

```text
enabled entry component:
  all required fields must be present and valid

disabled entry component:
  runtime treats condition as passed
  validator may allow absent numeric fields

enabled exit component:
  all required fields must be present and valid

disabled exit component:
  runtime never triggers it
  validator may allow absent numeric fields
```

## 7. Global Stats

Extend `ZigZagGlobalStats`:

```text
wakeup_entry_candidate_height_threshold: Optional[float] = None
wakeup_no_fresh_candidate_height_threshold: Optional[float] = None
```

Compute only for Mode D and only when the corresponding wakeup component is enabled:

```text
wakeup_entry_candidate_height_threshold =
  quantile(confirmed_heights_pct, wakeup_regime.entry.candidate_height.quantile)

wakeup_no_fresh_candidate_height_threshold =
  quantile(confirmed_heights_pct, wakeup_regime.exit.no_fresh_candidate.quantile)
```

Min-leg guard:

```text
if Mode D and at least one enabled wakeup component needs a quantile:
  required_legs = max(trade_filter.zigzag.local_window, 10)
  if n_legs_total < required_legs:
    ConfigError
```

Legacy `candidate_trigger_threshold` still materializes because current pipeline requires it, but for Mode D it must be numeric and must not impose auto-quantile failures.

## 8. Runtime Data Plumbing

Existing `run_backtest_fast()` already receives OHLC. Mode D needs `high`, `low` and optional `volume` in `apply()`.

Extend signatures backward-compatibly:

```text
apply(..., high: Optional[np.ndarray] = None, low: Optional[np.ndarray] = None, volume: Optional[np.ndarray] = None)
run_backtest_fast(...) passes high, low, volume to apply
run_single_backtest(...) accepts optional volume only if not already available from caller
run_period(...) reads df["volume"] when present and forwards it
```

Rules:

```text
high/low are required only when Mode D and atr_expansion.enabled == true
volume is required only when Mode D and volume_expansion.enabled == true
old callers without volume continue to work when volume_expansion is disabled
```

Validation inside Mode D setup:

```text
high, low, close length match
volume length matches close when volume is provided
high/low finite and > 0 when ATR component is enabled
volume finite and >= 0 when volume component is enabled
```

## 9. Wakeup Runtime Metrics

Add pure helpers in `zigzag_st_filter.py`:

```text
_compute_wakeup_atr_ratio(high, low, close, short_window, long_window) -> np.ndarray
_compute_wakeup_volume_ratio(volume, short_window, baseline_window) -> np.ndarray
```

ATR:

- use existing `calculate_true_range`;
- use existing `calculate_atr_rma` semantics when enough bars are available;
- if `n < long_window`, return all NaN and let `atr_ok=false`;
- `atr_ok=false` for bars `< long_window - 1`;
- non-finite ratio gives `atr_ok=false`.

Volume:

- wakeup volume is independent from `trade_filter.volume` and `VolumeRuntime`;
- use rolling mean in Phase 0 unless config later adds aggregation;
- warmup bars with unavailable baseline give `volume_ok=false`;
- baseline `<= 0` gives `volume_ok=false`;
- non-finite ratio gives `volume_ok=false`.

Disabled entry components always pass, even if their data arrays are absent.

## 10. Mode D Entry

Mode D entry is evaluated only from `OFF`.

Entry condition is AND of enabled components:

```text
candidate_height_ok:
  disabled -> true
  enabled -> finite candidate_height_pct >= wakeup_entry_candidate_height_threshold

candidate_age_ok:
  disabled -> true
  enabled -> candidate_age_bars != -1 and candidate_age_bars <= max_bars

candidate_direction_ok:
  candidate_leg_direction in (+1, -1)

trade_mode_ok:
  _trade_mode_allows_direction(candidate_leg_direction, trade_mode)

atr_ok:
  disabled -> true
  enabled -> finite atr_ratio >= min_ratio

volume_ok:
  disabled -> true
  enabled -> finite volume_ratio >= min_ratio
```

Additional gates:

```text
combined_reset_event[t] blocks entry
time_filter_in_window[t] == false blocks entry
state must be OFF at bar start
old trade_filter.volume gate must not block Mode D
legacy candidate_trigger_threshold must not block Mode D
```

On entry at close(t):

```text
state = ST_ACTIVE_FREEZE
held_pos = candidate_leg_direction
wakeup_cycle_age_bars = 0
wakeup_bars_since_fresh_candidate = 0 if fresh_candidate_ok else 1
wakeup_confirmed_legs_since_start = 0
wakeup_regime_active = 1
trade_filter_trigger_source = "wakeup_regime"
```

Position model remains open-to-open:

```text
decision at close(t)
position active at open(t+1)
```

## 11. Mode D FSM States

Mode D uses only:

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

State semantics:

```text
OFF:
  no wakeup window, no position
  counters = -1

ST_ACTIVE_FREEZE:
  wakeup window open
  position held in candidate direction
  same-direction ST flip has no effect
  opposite ST flip closes position and returns OFF

ST_STOPPING:
  wakeup window is no longer accepting entries
  current position is held
  new entries and reversals forbidden
  opposite ST flip closes position and returns OFF
```

No internal reversals in Phase 0.

## 12. Wakeup Counters

`wakeup_cycle_age_bars`:

```text
trigger bar: 0
next open-window bar: 1
increments in ST_ACTIVE_FREEZE and ST_STOPPING
OFF/reset: -1
```

`wakeup_confirmed_legs_since_start`:

```text
trigger bar: 0
increments on confirm_event only after the wakeup window has opened
increments in ST_ACTIVE_FREEZE and ST_STOPPING
OFF/reset: -1
```

`wakeup_bars_since_fresh_candidate`:

```text
trigger bar:
  0 if fresh_candidate_ok on trigger bar
  1 otherwise

each next open-window bar:
  if fresh_candidate_ok: 0
  else: previous + 1

OFF/reset: -1
```

This rule is fixed. Tests must not allow both 0 and 1 on the same trigger scenario.

## 13. Exit C

Exit C is the only lifecycle exit for wakeup window except reset and opposite ST flip.

Exit C conditions are OR with priority:

```text
1. ttl
2. no_fresh_candidate
3. structural_compression
```

### 13.1 TTL

```text
ttl_triggered = ttl.enabled and wakeup_cycle_age_bars >= ttl.bars
```

### 13.2 No Fresh Candidate

Fresh candidate:

```text
candidate_height_pct >= wakeup_no_fresh_candidate_height_threshold
AND candidate_age_bars != -1
AND candidate_age_bars <= no_fresh_candidate.max_age_bars
```

Unavailable candidate data means `fresh_candidate_ok=false`.

Trigger:

```text
no_fresh_candidate_triggered =
  no_fresh_candidate.enabled
  AND wakeup_bars_since_fresh_candidate >= timeout_bars
```

### 13.3 Structural Compression

```text
structural_triggered =
  structural_compression.enabled
  AND wakeup_cycle_age_bars >= min_cycle_age_bars
  AND wakeup_confirmed_legs_since_start >= min_confirmed_legs
  AND local_median_available[t]
  AND finite local_median_N[t]
  AND local_median_N[t] < global_median * global_median_multiplier
```

If `local_median_N` is unavailable, structural compression does not trigger.

### 13.4 Exit C Transition Rules

Exit C trigger arrays and `wakeup_exit_reason` are written only on the transition bar from `ST_ACTIVE_FREEZE` to `ST_STOPPING` or `OFF`.

```text
if Exit C triggers and current position exists:
  state = ST_STOPPING
  wakeup_exit_reason = selected reason
  do not close position immediately

if Exit C triggers and no current position exists:
  state = OFF
  counters = -1
  wakeup_exit_reason = selected reason
```

While already in `ST_STOPPING`, counters may continue to increment, but Exit C trigger arrays must not be emitted again.

## 14. Reset and Time Filter

`combined_reset_event` includes daily reset and time-filter reset.

On reset bar:

```text
if wakeup window was active:
  wakeup_exit_reason = "reset"

state = OFF
held_pos = 0
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
wakeup_confirmed_legs_since_start = -1
```

Reset bar cannot open a new wakeup window.

When outside `time_filter_in_window`, Mode D cannot open a new wakeup window. Existing reset semantics remain driven by the already computed time-filter reset event.

## 15. Opposite ST Flip

For Mode D:

```text
same-direction ST flip:
  no effect

opposite ST flip in ST_ACTIVE_FREEZE:
  close position at open(t+1)
  state = OFF
  wakeup_exit_reason = "opposite_st_flip"
  counters reset to -1 after close decision

opposite ST flip in ST_STOPPING:
  close position at open(t+1)
  state = OFF
  wakeup_exit_reason = "opposite_st_flip"
  counters reset to -1 after close decision
```

No reverse position is opened on the opposite flip.

## 16. Diagnostics Contract

Mode D must emit a full diagnostics dict compatible with tester summary and Excel.

### 16.1 Common Keys Required for All ZigZag Enabled Modes

Mode D must include these keys with the same dtype family as legacy:

```text
trade_filter_state                         object
trade_filter_state_code                    int64
trade_filter_trigger_source                object
confirmed_legs_since_start                 int64
st_flip_dir                                int8
trade_filter_enabled                       int8
zigzag_reversal_threshold                  float64
candidate_height_pct                       float64
candidate_trigger_threshold                float64
local_median_N                             float64
local_median_available                     int8
local_window                               int64
global_median                              float64
global_stats_available                     int8
freeze_confirmed_legs                      int64, sentinel -1 for Mode D
median_stop_triggered                      int8, always 0 for Mode D
stopping_started_at_index                  int64
filter_allowed_entry                       int8
filter_block_reason                        object
exit_off_mode                              object, "exit C"
exit_off_zz_leg_count                      int64, -1
zz_legs_since_lifecycle_start              int64, -1
zz_leg_stop_triggered                      int8, 0
exit_b_immediate_off_triggered             int8, 0
exit_b_immediate_off_config                int8, 0
daily_reset_enabled                        int8
daily_reset_event                          int8
time_filter_enabled                        int8
time_filter_in_window                      int8
time_filter_reset_event                    int8
zigzag_mode                                object, "D"
candidate_age_bars                         int64
candidate_leg_direction                    int8
candidate_duration_gate_enabled            int8, 0
candidate_duration_max_bars                int64, -1
candidate_duration_gate_passed             int8, 1
candidate_threshold_ok                     int8, sentinel 0 or legacy-compatible non-entry value
candidate_component_ok                     int8, sentinel 0 or wakeup candidate-height ok
confirmed_median_ok                        int8, 0
b_component_ok                             int8, 0
immediate_allowed                          int8, wakeup direction/trade_mode gate
immediate_candidate_entry_used             int8, 0 for Mode D unless intentionally mapped
immediate_candidate_entry_block_reason     object, "mode_not_c" or Mode D-specific sentinel
```

### 16.2 Wakeup Keys

Add:

```text
wakeup_regime_active                       int8
wakeup_entry_all_ok                        int8
wakeup_entry_candidate_height_ok           int8
wakeup_entry_candidate_age_ok              int8
wakeup_entry_candidate_direction_ok        int8
wakeup_entry_atr_ok                        int8
wakeup_entry_volume_ok                     int8
wakeup_entry_candidate_height_value        float64
wakeup_entry_candidate_height_threshold    float64
wakeup_entry_candidate_age_bars            int64
wakeup_entry_candidate_leg_direction       int8
wakeup_entry_atr_ratio                     float64
wakeup_entry_volume_ratio                  float64
wakeup_cycle_age_bars                      int64
wakeup_bars_since_fresh_candidate          int64
wakeup_confirmed_legs_since_start          int64
wakeup_exit_ttl_triggered                  int8
wakeup_exit_no_fresh_candidate_triggered   int8
wakeup_exit_structural_compression_triggered int8
wakeup_exit_reason                         object
```

`wakeup_exit_reason` values:

```text
none
ttl
no_fresh_candidate
structural_compression
reset
opposite_st_flip
```

## 17. Summary and Excel

### 17.1 Tester Summary

Extend `_build_filter_diagnostics_summary` mode-aware, not by assuming legacy-only keys.

For Mode D add:

```text
wakeup_starts_count =
  sum(trade_filter_trigger_source == "wakeup_regime")

wakeup_exit_ttl_count =
  sum(wakeup_exit_ttl_triggered)

wakeup_exit_no_fresh_candidate_count =
  sum(wakeup_exit_no_fresh_candidate_triggered)

wakeup_exit_structural_compression_count =
  sum(wakeup_exit_structural_compression_triggered)

wakeup_exit_reset_count =
  sum(wakeup_exit_reason == "reset")

wakeup_exit_opposite_st_flip_count =
  sum(wakeup_exit_reason == "opposite_st_flip")

wakeup_bars_active =
  sum(wakeup_regime_active)
```

Threshold echo:

```text
wakeup_entry_candidate_height_threshold
wakeup_no_fresh_candidate_height_threshold
wakeup_entry_candidate_height_quantile
wakeup_no_fresh_candidate_quantile
```

Config echo:

```text
zigzag_mode = "D"
exit_off_mode = "exit C"
wakeup_enabled = true
wakeup_candidate_age_max_bars
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

### 17.2 Excel

Add wakeup fields to `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`.

Add wakeup summary fields to `filters_summary`.

`ZigZag_Trigger_Events` must include rows where:

```text
trade_filter_trigger_source == "wakeup_regime"
```

These rows do not require ST flip. Triggered Lifecycle Start is true when state at trigger bar is `ST_ACTIVE_FREEZE` or `ST_STOPPING`.

## 18. Signal Events and Trade Diagnostics

Existing signal events are based on ST color changes. Mode D wakeup entries are not necessarily ST color changes.

Phase 0 requirement:

```text
ZigZag_Trigger_Events must show wakeup trigger bars.
FilterDiagnostics_100 must show wakeup trigger bars.
signal_events may remain ST-flip-oriented, but must not crash on trigger_source == "wakeup_regime".
```

If signal export is extended in Phase 0, add a distinct event type such as `wakeup_entry_trigger` instead of pretending it is an ST flip.

Trade diagnostics:

- `entry_trigger_source` should pick up `"wakeup_regime"` through `trade_filter_trigger_source`.
- Existing trade-level `exit_reason` values remain backward-compatible.
- If necessary, map Mode D opposite flip exits to the existing `"filter_stopping_opposite_flip"` or a documented wakeup-specific value, with tests.

## 19. Tests

### 19.1 Config and Caller Gate

```text
tester accepts D + exit C + wakeup_regime.enabled=true
tester rejects D without raw exit_off_mode="exit C"
tester rejects D without wakeup_regime.enabled=true
tester rejects exit C with mode != D
tester rejects exit C + exit_off_zz_leg_count
tester rejects exit C + exit_b_immediate_off
tester rejects D + candidate_trigger_threshold="auto"
tester rejects D + candidate_trigger_quantile
tester rejects structural_compression.local_window non-null
wf_grid rejects Mode D
wf_grid rejects exit C
wf_grid rejects wakeup_regime
old modes/exits remain accepted
```

### 19.2 Wakeup Config Fields

```text
enabled non-bool rejected
quantile outside (0,1) rejected
windows/bars < 1 rejected
min_confirmed_legs < 0 rejected
min_ratio <= 0 rejected
atr long_window < short_window rejected
volume baseline_window < short_window rejected
exit.action.mode != block_new_entries rejected
disabled components can omit numeric parameters
enabled components require numeric parameters
```

### 19.3 Global Stats

```text
wakeup entry threshold computed from configured quantile
wakeup no-fresh threshold computed from configured quantile
disabled quantile component does not compute threshold
min-leg guard applies only to enabled Mode D wakeup quantile components
old modes do not fail because of wakeup quantile checks
Mode D numeric candidate_trigger_threshold still materializes legacy field
```

### 19.4 Runtime Metrics

```text
ATR ratio uses calculate_true_range + RMA-compatible calculation
n < long_window gives all atr_ok=false, no uncaught ValueError
ATR warmup blocks until long_window - 1
ATR NaN/Inf ratio gives atr_ok=false
volume warmup gives volume_ok=false
volume baseline zero gives volume_ok=false
volume NaN/Inf gives volume_ok=false
disabled entry component always passes
disabled component with missing data still passes
```

### 19.5 Entry Mode D

```text
candidate_height gates entry
candidate_age gates entry
candidate_direction gates entry
trade_mode gates direction
ATR gates entry
volume gates entry
all enabled components combine as AND
candidate_age == -1 blocks
candidate_direction == 0 blocks
time_filter outside window blocks
combined_reset bar blocks
legacy candidate_trigger_threshold does not gate Mode D entry
legacy trade_filter.volume gate does not block Mode D
```

### 19.6 FSM and Position Behavior

```text
Mode D enters immediately from OFF
Mode D never enters WAIT_FIRST_ST_FLIP
UP candidate opens long
DOWN candidate opens short
position appears at t+1
same-direction ST flip holds
opposite ST flip closes without reverse
no ST_ACTIVE_MONITORING for Mode D
no ST_COUNTING_ZZ_LEGS for Mode D
legacy FSM loop is not executed for Mode D
```

### 19.7 Counters and Exit C

```text
trigger bar age = 0
next bar age = 1
wakeup_bars_since_fresh_candidate trigger semantics fixed: 0 if fresh else 1
confirmed legs increment only after window is open
TTL triggers at age >= bars
no_fresh triggers at bars_since_fresh >= timeout_bars
structural compression triggers only when all conditions are true
priority ttl > no_fresh_candidate > structural_compression
Exit C with position enters ST_STOPPING
Exit C without position returns OFF
Exit C trigger arrays emit only on transition bar, not every ST_STOPPING bar
ST_STOPPING forbids entries and reversals
ST_STOPPING holds until opposite ST flip
disabled exit component never triggers
```

### 19.8 Reset

```text
combined_reset closes active wakeup window
reset writes wakeup_exit_reason="reset" only when it closes active window
reset clears counters to -1
reset bar cannot open a new wakeup window
```

### 19.9 Diagnostics, Summary, Export

```text
Mode D diagnostics include full common keyset
Mode D diagnostics include all wakeup fields
all arrays have length n
dtypes match common diagnostics contract
FilterDiagnostics_100 includes wakeup display names
ZigZag_Trigger_Events includes wakeup_regime trigger rows
filter_diagnostics_summary includes wakeup counters, thresholds and config echo
wakeup_starts_count equals count(trigger_source == "wakeup_regime")
wakeup_bars_active equals sum(wakeup_regime_active)
legacy modes keep their existing diagnostics keyset and values
```

### 19.10 Regression

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
standalone volume unchanged
zigzag + old volume gate unchanged
old apply callers without volume still work
wf_grid reject tests pass
```

## 20. Implementation Order

1. Capture green baseline for current targeted tests.
2. Add config dataclasses, parser, whitelist and `__all__`.
3. Add caller gate and full wakeup validation.
4. Add config tests including `wf_grid` reject tests.
5. Extend `ZigZagGlobalStats` with wakeup thresholds.
6. Add global stats tests.
7. Add runtime data plumbing for `high`, `low`, `volume`.
8. Add ATR and wakeup volume helper tests.
9. Add common diagnostics allocation helper for Mode D-compatible keyset.
10. Add `apply()` dispatch to `_run_wakeup_fsm` before legacy loop.
11. Implement `_evaluate_wakeup_entry`.
12. Implement `_run_wakeup_fsm` entry and open-to-open position behavior.
13. Add entry/FSM/position tests.
14. Implement wakeup counters.
15. Add counter boundary tests.
16. Implement Exit C transition logic.
17. Add Exit C tests.
18. Implement reset and opposite ST flip semantics.
19. Add reset/opposite flip tests.
20. Extend summary and Excel display names.
21. Add diagnostics/summary/export tests.
22. Add `ZigZag_Trigger_Events` wakeup trigger rows.
23. Add signal/trade diagnostics compatibility tests.
24. Run legacy regression tests.
25. Run single-config tester verification.

## 21. Final Verification Commands

Minimum targeted verification:

```text
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py -q
python -m pytest wf_grid/tests/test_wp4_zigzag_per_bar.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
```

Add new donor wakeup tests to this set once created.

Single-config tester acceptance:

```text
zigzag.mode = D
lifecycle.exit_off_mode = exit C
wakeup_regime.enabled = true
wakeup_starts_count > 0
positions follow candidate_leg_direction
no WAIT_FIRST_ST_FLIP before entry
Exit C blocks new entries
opposite ST flip closes without reverse
legacy modes/exits unchanged
wf_grid rejects Phase 0 wakeup config
```

## 22. Main Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Mode D leaks into `wf_grid` | Shared caller gate plus `wf_grid` reject tests |
| Legacy FSM changes regress A/B/C modes | Early dispatch to `_run_wakeup_fsm`; legacy loop unchanged |
| Diagnostics summary crashes on missing keys | Mode D emits full common keyset with sentinels |
| `candidate_trigger_threshold: auto` creates hidden Mode D failures | Reject auto in Mode D Phase 0 |
| `structural_compression.local_window` requires second median stream | Reject non-null override in Phase 0 |
| Wakeup volume conflicts with old volume filter | Separate wakeup volume helper; old `VolumeRuntime` ignored for Mode D entry |
| ATR helper crashes on short data | Return all-NaN ratio and fail closed |
| Exit C counts repeat in ST_STOPPING | Emit trigger arrays only on transition bar |
| Signal export misses non-ST wakeup triggers | Require `ZigZag_Trigger_Events` wakeup rows; keep signal events compatible |
| Off-by-one in counters | Boundary tests for trigger bar, next bar, TTL and no-fresh timeout |

## 23. Definition of Done

Phase 0 is done when:

- Mode D config is accepted only in tester and rejected by `wf_grid`.
- Mode D enters immediately from OFF in candidate direction.
- Mode D does not execute legacy FSM loop.
- Exit C blocks new entries and holds existing position until opposite ST flip.
- Reset and time filter semantics remain compatible with existing behavior.
- Diagnostics, summary and Excel exports are complete enough to explain every wakeup entry and Exit C.
- Legacy modes and existing volume behavior pass regression tests.
- Single-config tester run produces meaningful wakeup counters and no export crashes.
