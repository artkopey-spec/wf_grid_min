# ZigZag Apply Diagnostics Audit v2

Date: 2026-06-16

Target:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py::apply
```

This audit supports tester lite speedup WP1. It classifies current arrays before
diagnostic gating. WP2 may remove duplicate/dead construction, but must keep
`collect_filter_diagnostics=True` behavior unchanged.

## Summary

At WP1 time, `apply()` had one active helper allocation path via
`_allocate_apply_arrays()`, but also had stale direct diagnostic allocations
before that helper. The helper values shadowed those direct allocations.

At WP1 time, `apply()` also built a manual `filter_diagnostics_out` near the end
and then returned through `_finalize_apply_result()`. The manual dict was dead.

At WP1 time, runtime reads from a diagnostic array were limited to:

```text
trigger_source_arr[t] -> wakeup_started_this_bar
trigger_source_arr[t] -> real_opened / position_freeze branch
```

No other optional diagnostic array read was found in runtime decisions.

Post-WP3 update: these reads now use local scalar state
`trigger_source_this_bar` / `wakeup_started_this_bar`. `trigger_source_arr[t]`
is a diagnostics write only.

## Always-On Runtime Arrays

| name | created at | written in main loop | read in main loop | affects positions | decision | notes |
|---|---|---:|---:|---:|---|---|
| filtered_positions | `_allocate_apply_arrays()` | yes | yes | yes | always-on runtime | Position output and current open position source. |
| trend_arr | `np.asarray(trend)` | no | yes | yes | always-on runtime | Drives ST flips. |
| confirm_event | `per_bar.confirm_event` | no | yes | yes | always-on runtime | Drives confirmed-leg counters. |
| cand_height | `per_bar.candidate_height_pct` | no | yes | yes | always-on runtime | Candidate height gate and wakeup checks. |
| local_median_N | `per_bar.local_median_N` | no | yes | yes | always-on runtime | Median stop logic. |
| local_median_available | `per_bar.local_median_available` | no | yes | yes | always-on runtime | Median validity gate. |
| cand_age_bars | `per_bar.candidate_age_bars` | no | yes | yes | always-on runtime | Duration gate and wakeup freshness. |
| cand_leg_dir | `per_bar.candidate_leg_direction` | no | yes | yes | always-on runtime | Lifecycle direction. |
| daily_reset_event | argument/inferred reset events | no | yes | yes | always-on runtime | Reset gate. |
| time_filter_in_window | inferred/resolved time filter | no | yes | yes | always-on runtime | Entry gate. |
| time_filter_reset_event | inferred/resolved time filter | no | yes | yes | always-on runtime | Reset gate. |
| combined_reset_event | reset event composition | no | yes | yes | always-on runtime | Daily/time reset wipe. |
| volume_condition_allowed_runtime | volume runtime | no | yes | yes | always-on runtime when volume enabled | Lifecycle start volume gate. |
| wakeup_atr_ratio | Mode D wakeup runtime | no | yes | yes | always-on runtime when enabled | Wakeup entry gate. |
| wakeup_volume_ratio | Mode D wakeup runtime | no | yes | yes | always-on runtime when enabled | Wakeup entry gate. |
| high_arr | Mode D wakeup OHLC runtime | no | indirect | yes | always-on runtime when ATR wakeup enabled | Used to compute `wakeup_atr_ratio`. |
| low_arr | Mode D wakeup OHLC runtime | no | indirect | yes | always-on runtime when ATR wakeup enabled | Used to compute `wakeup_atr_ratio`. |
| close_arr | Mode D wakeup OHLC runtime | no | indirect | yes | always-on runtime when ATR wakeup enabled | Used to compute `wakeup_atr_ratio`. |

## Optional Diagnostic Arrays From `_allocate_apply_arrays()`

These arrays are currently allocated and written for diagnostics. They may be
gated only after WP3 removes the `trigger_source_arr[t]` runtime reads.

| name | created at | written in main loop | read in main loop | affects positions | decision | notes |
|---|---|---:|---:|---:|---|---|
| state_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Export `trade_filter_state`. |
| state_code_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Export `trade_filter_state_code`. |
| trigger_source_arr | `_allocate_apply_arrays()` | yes | yes | yes, currently | move to scalar, then optional diagnostics | Runtime read is the WP3 blocker. |
| confirmed_legs_since_start_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar counter is runtime source. |
| st_flip_dir_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | `flip_dir` scalar is runtime source. |
| trade_filter_enabled_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| reversal_threshold_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| ctt_diag_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| local_window_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| global_median_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| global_stats_available_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| freeze_confirmed_legs_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| median_stop_triggered_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors median stop event. |
| stopping_started_at_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors `_stopping_start` scalar. |
| filter_allowed_entry_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors position entry write. |
| filter_block_reason_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Export-only reason labels. |
| exit_off_mode_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| exit_off_zz_leg_count_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| zz_legs_since_lifecycle_start_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar counter is runtime source. |
| zz_leg_stop_triggered_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors exit-B event. |
| exit_b_immediate_off_triggered_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors immediate-off event. |
| exit_b_immediate_off_config_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| candidate_threshold_ok_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar primitive is runtime source. |
| candidate_component_ok_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar primitive is runtime source. |
| confirmed_median_ok_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar primitive is runtime source. |
| b_component_ok_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar primitive is runtime source. |
| immediate_allowed_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar primitive is runtime source. |
| candidate_duration_gate_passed_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar primitive is runtime source. |
| state_at_bar_start_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar snapshot is runtime source. |
| held_pos_at_bar_start_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar snapshot is runtime source. |
| confirmed_legs_at_bar_start_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar snapshot is runtime source. |
| zigzag_mode_arr | `_allocate_apply_arrays()` | no | finalization only | no | optional diagnostics | Used only to decide Mode D diagnostics keys. |
| candidate_duration_gate_enabled_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| candidate_duration_max_bars_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| immediate_used_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors lifecycle start path. |
| immediate_block_reason_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Export-only reason labels. |
| wakeup_regime_active_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mode D diagnostics. |
| wakeup_entry_all_ok_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | `_record_wakeup_entry_diagnostics`. |
| wakeup_entry_candidate_height_ok_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_candidate_age_ok_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_candidate_direction_ok_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_trade_mode_ok_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_atr_ok_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_volume_ok_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_candidate_height_value_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_candidate_height_threshold_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_candidate_age_bars_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_candidate_leg_direction_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_atr_ratio_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_entry_volume_ratio_arr | `_allocate_apply_arrays()` | yes, helper | no | no | optional diagnostics | Export-only. |
| wakeup_cycle_age_bars_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar age is runtime source. |
| wakeup_bars_since_fresh_candidate_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar age is runtime source. |
| wakeup_exit_ttl_triggered_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors scalar exit reason. |
| wakeup_exit_no_fresh_candidate_triggered_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors scalar exit reason. |
| wakeup_exit_close_triggered_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors scalar action. |
| wakeup_exit_action_mode_arr | `_allocate_apply_arrays()` | slice init | no | no | optional diagnostics | Constant mode for Mode D. |
| wakeup_exit_reason_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Export-only reason labels. |
| wakeup_position_action_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Export-only action labels. |
| wakeup_active_direction_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar direction is runtime source. |
| wakeup_lock_cycle_direction_config_arr | `_allocate_apply_arrays()` | no | no | no | optional diagnostics | Constant broadcast. |
| position_freeze_active_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Scalar freeze state is runtime source. |
| position_freeze_bars_left_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Derived from scalar freeze state. |
| position_freeze_ignored_opposite_st_flip_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Mirrors scalar branch. |
| position_freeze_release_action_arr | `_allocate_apply_arrays()` | yes | no | no | optional diagnostics | Export-only action labels. |

## Volume Runtime And Labels

| name | created at | written in main loop | read in main loop | affects positions | decision | notes |
|---|---|---:|---:|---:|---|---|
| volume_regime_runtime | `volume_runtime.regime` | no | no | no | optional diagnostics label source | Numeric runtime currently only materialized for export. |
| volume_condition_allowed_runtime | `volume_runtime.condition_allowed` | no | yes | yes | always-on runtime when volume enabled | Must remain available for lifecycle gate. |
| volume_median_relative_runtime | `volume_runtime.median_relative_volume` | no | no | no | optional diagnostics | Export-only numeric array. |
| volume_condition_block_reason_labels | materialized labels | no | yes | no direct position effect | optional diagnostics after gated read | Currently read only to build diagnostic block reason when volume blocks entry. |
| volume_regime_labels | materialized labels | no | no | no | optional diagnostics | Export-only labels. |
| volume_initial_direction_labels | materialized labels | no | no | no | optional diagnostics | Export-only labels. |
| volume_arrays.regime_labels | `_materialize_apply_volume_runtime()` | no | finalization only | no | optional diagnostics | Duplicates older inline materialization. |
| volume_arrays.condition_allowed | `_materialize_apply_volume_runtime()` | no | finalization only | no | optional diagnostics/runtime echo | Same data as runtime allowed array. |
| volume_arrays.condition_block_reason_labels | `_materialize_apply_volume_runtime()` | no | finalization only | no | optional diagnostics | Label source for final diagnostics. |
| volume_arrays.initial_direction_labels | `_materialize_apply_volume_runtime()` | no | finalization only | no | optional diagnostics | Label source for final diagnostics. |
| volume_arrays.median_relative | `_materialize_apply_volume_runtime()` | no | finalization only | no | optional diagnostics | Numeric export echo. |

## Stale Direct Allocations To Remove In WP2

The following direct allocations in `apply()` are shadowed by
`_allocate_apply_arrays()` and do not feed the returned result:

```text
state_arr
state_code_arr
trigger_source_arr
confirmed_legs_since_start_arr
st_flip_dir_arr
trade_filter_enabled_arr
reversal_threshold_arr
ctt_diag_arr
local_window_arr
global_median_arr
global_stats_available_arr
freeze_confirmed_legs_arr
median_stop_triggered_arr
stopping_started_at_arr
filter_allowed_entry_arr
filter_block_reason_arr
exit_off_mode_arr
exit_off_zz_leg_count_arr
zz_legs_since_lifecycle_start_arr
zz_leg_stop_triggered_arr
exit_b_immediate_off_triggered_arr
exit_b_immediate_off_config_arr
candidate_threshold_ok_arr
candidate_component_ok_arr
confirmed_median_ok_arr
b_component_ok_arr
immediate_allowed_arr
candidate_duration_gate_passed_arr
zigzag_mode_arr
candidate_duration_gate_enabled_arr
candidate_duration_max_bars_arr
immediate_used_arr
immediate_block_reason_arr
```

## Dead Finalization To Remove In WP2

The manual `filter_diagnostics_out` built near the end of `apply()` is dead
because the function returns `_finalize_apply_result(...)`. WP2 can remove this
manual dict without changing returned diagnostics.

## Stop Rule Result

WP1 stop rule result:

```text
Only trigger_source_arr[t] is read by runtime decisions.
WP3 must decouple it before diagnostics gating.
```

Post-WP3 update:

```text
No runtime decision reads trigger_source_arr[t].
Diagnostics gating in WP4/WP5 can treat trigger_source_arr as optional.
```
