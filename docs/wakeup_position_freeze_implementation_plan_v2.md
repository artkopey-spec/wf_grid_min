# Implementation Plan: Position Freeze for wakeup_regime (v2)

## 1. Goal

Add `position_freeze` to `trade_filter.wakeup_regime` in Mode D so that an
already opened wakeup position is not immediately closed or reversed by a short
lived opposite SuperTrend flip.

The feature protects only this case:

```text
Mode D wakeup-cycle is active
held_pos != 0
SuperTrend flips opposite to held_pos
the flip happens inside the configured min-hold window
```

The ignored flip is released after the window only if SuperTrend is still
opposite to the held position. If SuperTrend realigns before release, the
pending ignored flip is cleared.

This is a short-term whipsaw filter, not a guaranteed drawdown reducer. For
`long` / `short`, the feature delays a flat exit; this helps only when the
opposite ST flip reverts quickly and hurts when the flip is a real reversal.
For `both` / `revers`, the feature delays a reverse. The sign and size of the
effect must be validated by separate whipsaw and confirmed-opposite buckets.

## 2. Non-goals

Do not change non-D modes: `A`, `B`, `C`, `A+B`, `C+B`.

Do not change the meaning or enum value of existing
`ZigZagFSMState.ST_ACTIVE_FREEZE`. That state is the active wakeup lifecycle
state, not the new feature.

Do not allow `wakeup_regime` or `position_freeze` in `wf_grid`. WF Grid must
continue to reject Mode D / exit C / wakeup_regime configs.

Do not treat an ignored opposite flip as a trade close reason.

Do not block `exit-C` (`ttl` / `no_fresh_candidate`) with position freeze.
Exit-C remains higher priority.

## 3. Current Architecture Anchors

Implementation is in the active donor package:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/io/excel_tester.py
```

Tester resolves `supertrend_optimizer.*` from `donor/`, not from
`donor TESTER/supertrend_optimizer`. Runtime and shared config changes belong
to `donor/`.

`wf_grid.config.schema` and `wf_grid.config.loader` re-export / delegate to the
shared donor config module. Any schema change in `trade_filter_config.py` is
visible to both Tester and WF Grid, so pipeline gates must remain explicit.

Mode D internal ST flip logic is currently inside `apply()` near:

```text
wakeup_internal_st_flip_this_bar = (...)
if wakeup_internal_st_flip_this_bar:
    ...
    _apply_mode_d_internal_st_flip(...)
```

Position decisions are open-to-open:

```text
decision at close(t) -> filtered_positions[t + 1]
```

Any new freeze decision must preserve this ordering.

The freeze window is counted in decision-bar indexes. Trade reports such as
`bars_held` are execution-layer outputs and are shifted by the open-to-open
position write. Tests and characterization must validate both the decision
bar behavior and the resulting `filtered_positions[t + 1]` / trade duration.

## 4. Config Contract

### 4.1 YAML Shape

```yaml
trade_filter:
  wakeup_regime:
    position_freeze:
      enabled: true
      min_hold_bars: 3
      apply_to: internal_opposite_st_flip
      release_action: apply_if_still_opposite
```

### 4.2 Dataclasses

Add:

```python
@dataclass
class TradeFilterWakeupPositionFreezeConfig:
    enabled: object = False
    min_hold_bars: object = None
    apply_to: object = None
    release_action: object = None
```

Extend:

```python
@dataclass
class TradeFilterWakeupRegimeConfig:
    enabled: object = False
    lock_cycle_direction: object = False
    entry: TradeFilterWakeupEntryConfig = field(...)
    exit: TradeFilterWakeupExitConfig = field(...)
    position_freeze: TradeFilterWakeupPositionFreezeConfig = field(
        default_factory=TradeFilterWakeupPositionFreezeConfig
    )
```

### 4.3 Strict Schema

Update `TRADE_FILTER_ALLOWED_KEYS`:

```text
trade_filter.wakeup_regime:
  enabled, lock_cycle_direction, entry, exit, position_freeze

trade_filter.wakeup_regime.position_freeze:
  enabled, min_hold_bars, apply_to, release_action
```

Update `build_trade_filter_config_from_raw()` to parse
`wakeup_raw.get("position_freeze")`.

### 4.4 Validation

Validate `position_freeze` only when the `wakeup_regime` block is present.

Required rules:

```text
position_freeze.enabled must be bool if present.

If position_freeze.enabled == true:
  wakeup_regime.enabled must be true
  zigzag.mode must be D
  min_hold_bars must be int >= 1
  apply_to must equal "internal_opposite_st_flip"
  release_action must equal "apply_if_still_opposite"
```

No relaxed no-op rule outside Mode D:

```text
wf_grid continues to reject any wakeup_regime block.
tester rejects wakeup_regime outside mode D.
tester rejects position_freeze.enabled=true unless wakeup_regime.enabled=true.
```

`position_freeze.enabled=false` is allowed only as a harmless nested setting
inside an otherwise valid Mode D wakeup config. It must not weaken existing
Mode D / wakeup validation.

### 4.5 Baseline Configs

Baseline runs must use:

```yaml
position_freeze:
  enabled: false
```

Do not represent baseline as `min_hold_bars: 0`. Validation deliberately
rejects `min_hold_bars < 1` when the feature is enabled.

Adding Mode D diagnostic arrays changes the `filter_diagnostics` keyset and
the `FilterDiagnostics_100` / `filters_summary` export schema even when
`position_freeze.enabled=false`. This is an intentional schema migration:

```text
positions, returns, equity, trades, and existing diagnostics must stay equal.
new position_freeze diagnostics are appended with default values.
Mode D golden/snapshot/export fixtures must be updated deliberately.
```

## 5. Runtime State

Add runtime state near `held_pos`:

```text
pos_freeze_until   : int = -1
pos_freeze_pending : bool = False
```

Do not keep a separate `pos_freeze_dir` in v2. The authoritative position is
`held_pos`. If `held_pos` becomes `0`, or the active wakeup-cycle ends, all
position-freeze state is cleared.

Helper operations:

```text
_clear_position_freeze()
  pos_freeze_until = -1
  pos_freeze_pending = False

_set_position_freeze_window(t, min_hold_bars)
  pos_freeze_until = t + min_hold_bars
  pos_freeze_pending = False

_position_freeze_active_for_bar(t)
  enabled and held_pos != 0 and t <= pos_freeze_until
```

`position_freeze_active` diagnostics must mean: "could position_freeze intercept
an opposite internal flip on this bar". Therefore the entry/reverse bar `t0`
itself records inactive; the protected bars are `t0 + 1` through
`t0 + min_hold_bars`, inclusive.

## 6. Diagnostic Arrays

Add arrays in `_init_apply_arrays()` near wakeup arrays:

```text
position_freeze_active_arr                  int8, default 0
position_freeze_bars_left_arr               int64, default 0
position_freeze_ignored_opposite_st_flip_arr int8, default 0
position_freeze_release_action_arr          object, default "none"
```

Export these arrays in `filter_diagnostics` for Mode D diagnostics. In Mode D,
export them even when the feature is disabled, with default values. In non-D
modes, they can remain absent because `position_freeze` is a wakeup-only
diagnostic.

`position_freeze_bars_left` should be:

```text
max(pos_freeze_until - t + 1, 0) when active on this bar
0 otherwise
```

Release action values:

```text
none
noop_st_realigned
noop_invalid_lock_state
applied_flat_on_disallowed_st_flip
applied_reverse_on_st_flip
applied_restore_allowed_position_on_st_flip
```

The `restore` value is defensive; normal release after an opposite pending flip
should usually produce `flat` or `reverse`, or `noop` if ST realigned.
`noop_invalid_lock_state` is also defensive; it clears stale pending state when
`lock_cycle_direction=true` but `cycle_direction` is unexpectedly not `+1/-1`.

## 7. Helper for Effective Trade Mode

Add a single helper and use it both for normal Mode D internal flips and for
freeze release:

```python
def _effective_wakeup_trade_mode(
    *,
    raw_trade_mode: str,
    wakeup_lock_cycle_direction: bool,
    cycle_direction: int,
) -> str | None:
    if wakeup_lock_cycle_direction:
        if cycle_direction == +1:
            return "long"
        if cycle_direction == -1:
            return "short"
        return None
    return raw_trade_mode
```

If the helper returns `None` during normal internal flip handling, do not apply
the flip. If it returns `None` during freeze release, clear
`pos_freeze_pending` and write
`position_freeze_release_action = "noop_invalid_lock_state"` so stale pending
state cannot survive past the release boundary.

This helper is required to prevent `raw_trade_mode=both/revers` from producing
a reverse while `lock_cycle_direction=true`.

When applying release, derive both values from the same helper context:

```text
effective_trade_mode = long/short/raw mode
effective_direction = cycle_direction when lock_cycle_direction=true
effective_direction = wakeup_active_direction otherwise
```

## 8. Apply Loop Order

Inside each bar `t`, preserve the existing broad order:

```text
0. Capture state_at_bar_start / held_pos_at_bar_start.
1. Apply daily/time reset wipe.
2. Detect ST flip and compute mode primitives.
3. Mode dispatcher can start Mode D wakeup from OFF.
4. Mode D wakeup runtime:
   4.1 update wakeup age / fresh candidate state
   4.2 evaluate exit-C ttl / no_fresh_candidate
   4.3 if exit-C fired, do not run internal flip or release
   4.4 handle internal ST flip always; intercept only when t <= pos_freeze_until
   4.5 clear pending on ST realignment only when t <= pos_freeze_until
   4.6 if t > pos_freeze_until, handle position_freeze release if due
   4.7 write wakeup diagnostics for this bar
5. Legacy/non-D FSM transitions continue as before.
6. Set a new position_freeze window after a real open/reverse.
7. Clear freeze state if held_pos is flat or wakeup active state ended.
8. Compute and write filtered_positions[t + 1].
9. Persist state diagnostics.
```

The actual code should place 4.4 / 4.5 / 4.6 around the existing
`wakeup_internal_st_flip_this_bar` block, before `wakeup_position_action_arr[t]`
is written.

Normal Mode D internal ST flip handling must remain active when
`position_freeze` is disabled, before a freeze window exists, and after the
window has expired. The freeze window gates only the intercept branch and
pending realignment cleanup; it does not gate the baseline flip handler.

The new window should be set after all state/held position mutations for the
bar are known, but before `filtered_positions[t + 1]` is written.

The pending-state writers are intentionally mutually exclusive by window
boundary:

```text
t <= pos_freeze_until:
  intercept can set pos_freeze_pending=True
  realignment can clear pos_freeze_pending=False
  release must not run

t > pos_freeze_until:
  intercept must not run
  early realignment cleanup must not run
  release is the only pending-state writer
```

If a new same-direction ST flip arrives on the first release bar while pending
is still true, process the real ST flip first through normal internal flip
logic. Then release sees `trend[t] == held_pos` and records
`noop_st_realigned`, or finds pending already cleared. This preserves the real
event and avoids applying release before same-bar evidence.

## 9. Intercept Logic

Definitions:

```text
position_freeze_enabled =
  mode_d_enabled
  and wakeup_regime.enabled is true
  and position_freeze.enabled is true

freeze_active =
  position_freeze_enabled
  and state_at_bar_start == ST_ACTIVE_FREEZE
  and state == ST_ACTIVE_FREEZE
  and held_pos != 0
  and t <= pos_freeze_until

opposite_to_position =
  flip_dir != 0
  and held_pos != 0
  and flip_dir == -held_pos
```

When `wakeup_internal_st_flip_this_bar` is true:

```text
if freeze_active and opposite_to_position:
    held_pos unchanged
    wakeup_active_direction unchanged
    wakeup_position_action_this_bar = "position_freeze_ignored_opposite_st_flip"
    pos_freeze_pending = True
    position_freeze_ignored_opposite_st_flip_arr[t] = 1
else:
    effective_trade_mode = _effective_wakeup_trade_mode(...)
    if effective_trade_mode is not None:
        apply existing _apply_mode_d_internal_st_flip(...)
```

The intercept decision is based on `flip_dir` relative to `held_pos`, not on raw
`trade_mode`. This is intentional.

## 10. Pending Realignment

If a pending ignored flip exists and SuperTrend realigns with the held position
before release:

```text
if position_freeze_enabled
   and pos_freeze_pending
   and t <= pos_freeze_until
   and state == ST_ACTIVE_FREEZE
   and held_pos != 0
   and trend[t] == held_pos:
       pos_freeze_pending = False
```

Do not write `position_freeze_release_action = noop_st_realigned` at this point
unless the window has actually expired. Early realignment is not a release; it
is cleanup of a stale pending opposite flip.

## 11. Release Logic

Release is checked only while the wakeup-cycle is still active.

```text
release_due =
  position_freeze_enabled
  and state_at_bar_start == ST_ACTIVE_FREEZE
  and state == ST_ACTIVE_FREEZE
  and held_pos != 0
  and pos_freeze_pending
  and t > pos_freeze_until
  and not is_reset
  and not wakeup_exit_c_triggered_this_bar
```

If `release_due`:

```text
st_now = trend[t]

if st_now == held_pos:
    pos_freeze_pending = False
    position_freeze_release_action_arr[t] = "noop_st_realigned"
else:
    effective_trade_mode = _effective_wakeup_trade_mode(...)
    if effective_trade_mode is None:
        pos_freeze_pending = False
        position_freeze_release_action_arr[t] = "noop_invalid_lock_state"
    else:
        held_pos, wakeup_active_direction, action =
            _apply_mode_d_internal_st_flip(
                held_pos=held_pos,
                wakeup_active_direction=effective_direction,
                flip_dir=st_now,
                trade_mode=effective_trade_mode,
            )
        pos_freeze_pending = False
        position_freeze_release_action_arr[t] = "applied_" + action
        wakeup_position_action_this_bar = action
```

When `lock_cycle_direction=true`, `effective_direction` remains
`cycle_direction`; release must not reverse against the locked cycle.

Expected behavior by mode:

```text
raw long:
  LONG + ST SHORT ignored during window.
  release with ST SHORT -> flat_on_disallowed_st_flip.

raw short:
  SHORT + ST LONG ignored during window.
  release with ST LONG -> flat_on_disallowed_st_flip.

raw both/revers without lock_cycle_direction:
  opposite ignored during window.
  release with ST still opposite -> reverse_on_st_flip.

raw both/revers with lock_cycle_direction=true:
  cycle_direction +1 behaves as long.
  cycle_direction -1 behaves as short.
  release with ST opposite -> flat_on_disallowed_st_flip, not reverse.
```

## 12. Window Setup

Set a new window only after a real open or real reverse.

Do not infer a new window from any generic `held_pos: 0 -> non-zero` transition.
In `long` / `short`, `restore_allowed_position_on_st_flip` can restore the
allowed side after a temporary flat and would otherwise look like a new open.
Restore is not a new freeze-protected entry.

Use explicit same-bar events:

```text
real_opened =
  wakeup_started_this_bar
  and held_pos != 0
  and state == ST_ACTIVE_FREEZE

real_reversed =
  wakeup_position_action_this_bar == "reverse_on_st_flip"
  and held_pos != 0
  and state == ST_ACTIVE_FREEZE

restore =
  wakeup_position_action_this_bar == "restore_allowed_position_on_st_flip"
```

If:

```text
position_freeze_enabled and (real_opened or real_reversed) and not restore
```

then:

```text
pos_freeze_until = t + min_hold_bars
pos_freeze_pending = False
```

Boundary semantics:

```text
entry/reverse at t0
protected bars: t0 + 1 through t0 + min_hold_bars, inclusive
first unprotected bar: t0 + min_hold_bars + 1
```

Example:

```text
t0 = 1
min_hold_bars = 3
pos_freeze_until = 4
flips at 2, 3, 4 are ignored
flip at 5 is processed normally
```

Do not set a new window for `restore_allowed_position_on_st_flip` when it only
restores the same allowed side after a temporary flat. A new window is for a
new non-zero position or a true sign change.

If release applies `reverse_on_st_flip` without `lock_cycle_direction`, the
same bar qualifies as `real_reversed` and starts a fresh window for the new
position.

## 12.1 Decision-Bar Window vs Execution-Layer Bars Held

Freeze windows are measured on decision bars because `apply()` mutates
`held_pos` at close(t) and writes `filtered_positions[t + 1]`.

Example:

```text
t0 decision opens LONG
filtered_positions[t0 + 1] becomes +1
min_hold_bars = 3
protected decision bars: t0 + 1, t0 + 2, t0 + 3
first unprotected decision bar: t0 + 4
```

Tests must assert both:

```text
held_pos / wakeup_position_action on decision bars
filtered_positions[t + 1] and extracted trade bars_held on execution layer
```

Characterization must not assume `min_hold_bars` maps one-to-one to the
reported `Bars Held <= H` bucket without this open-to-open shift.

## 13. Cleanup Rules

Clear `pos_freeze_*` immediately when any of these happens:

```text
combined daily/time reset
held_pos becomes 0
state becomes OFF
state becomes WAIT_FIRST_ST_FLIP
state becomes ST_STOPPING
wakeup_exit_c_triggered_this_bar
end of active wakeup-cycle
```

Important `block_new_entries` rule:

```text
exit-C with action block_new_entries can leave the market position open while
state moves to ST_STOPPING. Position freeze must be cleared and must not apply
release in ST_STOPPING.
```

Important `close_position` rule:

```text
exit-C with action close_position sets held_pos=0 and state=OFF. Position
freeze must be cleared before the next bar.
```

## 14. Trade Close Reason Safety

`wakeup_position_action_this_bar` can contain:

```text
position_freeze_ignored_opposite_st_flip
```

This value is a per-bar diagnostic only. It is not a close action.

Do not map it in `_wakeup_position_action_at()` to an `exit_reason`.

Allowed close-reason mapping remains only for actions that actually close or
reverse a trade:

```text
reverse_on_st_flip -> wakeup_reverse_on_st_flip
flat_on_disallowed_st_flip -> wakeup_flat_on_disallowed_st_flip
```

Release events that actually apply `flat` or `reverse` should set
`wakeup_position_action_this_bar` to the existing action, so trade close reason
stays compatible. Release attribution is handled separately via
`position_freeze_release_action`.

A release bar can close or reverse a trade even when `st_flip_dir == 0`,
because the actual ST flip happened earlier inside the protected window. This
is expected. Downstream logic must not rely on an invariant that every
`wakeup_flat_on_disallowed_st_flip` or `wakeup_reverse_on_st_flip` close reason
has `st_flip_dir != 0` on the same bar. Use `position_freeze_release_action` to
distinguish normal ST-flip actions from release-applied actions.

## 15. Summary and Export

### 15.1 Per-bar Diagnostics

Add display names in `excel_tester.py`:

```text
position_freeze_active -> Wakeup Position Freeze Active
position_freeze_bars_left -> Wakeup Position Freeze Bars Left
position_freeze_ignored_opposite_st_flip -> Wakeup Position Freeze Ignored Opposite ST Flip
position_freeze_release_action -> Wakeup Position Freeze Release Action
```

### 15.2 Runner Summary

In `testing/runner.py`, count:

```text
wakeup_position_freeze_ignored_opposite_st_flip_count
wakeup_position_freeze_release_flat_count
wakeup_position_freeze_release_reverse_count
wakeup_position_freeze_release_noop_count
```

Counts come from:

```text
position_freeze_ignored_opposite_st_flip
position_freeze_release_action
```

not from `wakeup_position_action` alone.

### 15.3 filters_summary Sheet

Add rows / columns for:

```text
Wakeup Position Freeze Ignored Opposite ST Flip
Wakeup Position Freeze Release Flat
Wakeup Position Freeze Release Reverse
Wakeup Position Freeze Release Noop
```

Do not collapse release counters into the existing wakeup ST flip counters.
Both views are useful:

```text
wakeup_flat_on_disallowed_st_flip_count:
  total flat actions, including normal and release-applied flats

wakeup_position_freeze_release_flat_count:
  subset caused specifically by release after freeze
```

Release counters are subsets of existing wakeup action counters when release
applies `flat` or `reverse`. Do not sum existing wakeup counters and release
counters as if they were disjoint event groups.

## 16. Implementation Work Packages

### WP1 - Config Schema and Validation

Files:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/tests/core/test_wakeup_config_dataclasses.py or a new donor core config test
wf_grid/tests/test_wp2_config_trade_filter.py for wf_grid rejection
donor TESTER/tests/test_wp_t2_load_tester_config.py as integration coverage only
```

Tasks:

```text
add dataclass
parse raw YAML
extend strict whitelist
validate enabled/min_hold_bars/apply_to/release_action
preserve wf_grid rejection of wakeup_regime
preserve tester Mode D requirements
```

### WP2 - Runtime Helpers and Disabled Baseline

Files:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
wf_grid/tests/test_wakeup_mode_d_entry.py or new donor TESTER runtime test
```

Tasks:

```text
add freeze runtime state
add clear/set/effective-trade-mode helpers
export default diagnostics in Mode D
prove enabled=false keeps positions/returns/equity/trades/existing diagnostics
identical while appending new default diagnostic arrays
update Mode D snapshot/export fixtures deliberately
```

### WP3 - Intercept and Release

Tasks:

```text
intercept opposite internal ST flips while active
set pending on ignored flips
clear pending on early ST realignment
enforce pending writer order: intercept/realign only inside window, release
only after window
release only in active ST_ACTIVE_FREEZE wakeup state
honor exit-C priority
honor lock_cycle_direction
exclude restore_allowed_position_on_st_flip from new-window setup
clear freeze on flat/OFF/ST_STOPPING
```

Refactoring decision for v2:

```text
Do not extract the whole Mode D per-bar runtime into a new class/function in
this implementation pass. The apply() loop is order-sensitive and already has
many state_at_bar_start guards; a large refactor would increase blast radius.

Required scoped helpers:
  _effective_wakeup_trade_mode(...)
  _clear_position_freeze(...)
  _set_position_freeze_window(...)
  _record_position_freeze_diagnostics(...)

Optional follow-up after green tests:
  extract Mode D runtime into a structured helper once behavior is pinned by
  the new regression suite.
```

### WP4 - Diagnostics, Trade Reasons, Export

Files:

```text
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/io/excel_tester.py
```

Tasks:

```text
do not map ignored flip to exit_reason
add release counters from position_freeze_release_action
add FilterDiagnostics_100 display names
add filters_summary fields
```

### WP5 - Regression and Characterization Runs

Tasks:

```text
run targeted unit tests
run tester Mode D smoke
run existing Mode D snapshot/export tests and update expected columns
run comparison grid for min_hold_bars
inspect early Bars Held <= 3 bucket, whipsaw/confirmed-opposite buckets, and
release counters
```

### WP6 - Duration and Ownership Estimate

Expected implementation effort for one engineer:

```text
WP1 config schema/validation:        0.5-1 day
WP2 runtime helpers/baseline:        1 day
WP3 intercept/release/order tests:   2-3 days
WP4 diagnostics/export/summary:      1 day
WP5 characterization and analysis:   1-2 days
buffer for apply() regressions:      1 day
```

Total: approximately 6-9 working days, plus any extra time needed for long
batch runs or interpreting unstable EV results.

## 17. Required Tests

### Config Tests

```text
T1 absent position_freeze block -> enabled=False default.
T2 enabled=true requires min_hold_bars int >= 1.
T3 enabled=true requires apply_to="internal_opposite_st_flip".
T4 enabled=true requires release_action="apply_if_still_opposite".
T5 enabled=true outside Mode D -> ConfigError.
T6 enabled=true with wakeup_regime.enabled=false -> ConfigError.
T7 wf_grid still rejects wakeup_regime/position_freeze.
T8 baseline is enabled=false, not min_hold_bars=0.
```

### Runtime Tests

```text
T9 enabled=false -> positions/returns/equity/trades and existing diagnostics
    match baseline; new freeze diagnostics are default-valued appended fields.
T10 entry at t0, opposite flip at t0+1 inside window -> held_pos unchanged
    and filtered_positions[t0+2] still carries the original position.
T11 min_hold_bars=1, opposite flip exactly at pos_freeze_until -> ignored.
T12 opposite flip at pos_freeze_until+1 -> normal behavior.
T13 pending ignored flip, ST realigns before expiry -> pending cleared, no release.
T14 expiry with ST realigned -> release noop if pending still exists.
T15 long/short release still opposite -> flat_on_disallowed_st_flip.
T16 both/revers release still opposite -> reverse_on_st_flip and new window.
T17 lock_cycle_direction + raw both/revers -> flat, not reverse.
T18 exit-C ttl on expiry bar -> exit-C wins, release does not fire.
T19 exit-C block_new_entries clears freeze and no release in ST_STOPPING.
T20 exit-C close_position clears freeze when held_pos becomes 0.
T21 daily_reset/time_filter_reset clears freeze.
T22 last-bar entry does not write out of bounds and does not create bogus release.
T23 restore_allowed_position_on_st_flip does not start a new freeze window.
T24 pending exists and a new ST flip arrives on the first release bar -> normal
    internal flip processing and release ordering are deterministic.
T25 lock_cycle_direction=true with cycle_direction=0 at release clears pending
    with noop_invalid_lock_state.
T26 extracted trade bars_held matches the documented decision/execution shift.
```

### Trade Diagnostics Tests

```text
T27 ignored opposite flip does not become exit_reason.
T28 release-applied flat/reverse keeps existing close reason even if st_flip_dir == 0.
T29 position_freeze_release_action distinguishes release flat/reverse/noop.
```

### Export and Summary Tests

```text
T30 Mode D diagnostics include new arrays with stable dtype/defaults.
T31 FilterDiagnostics_100 contains display columns appended after wakeup fields.
T32 filters_summary contains freeze counters.
T33 release counters are computed from position_freeze_release_action.
T34 release counters are documented/tested as subsets, not disjoint totals.
```

## 18. Characterization Grid

Run baseline as `enabled=false`.

Then run:

```yaml
position_freeze:
  enabled: true
  min_hold_bars: [2, 3, 4, 5, 6]
trade_mode: [long, short, both]
```

Do not run `revers` as a separate core grid dimension unless a caller-specific
path proves it differs from `both`. In `_apply_mode_d_internal_st_flip`,
`both` and `revers` are equivalent for this feature.

For `lock_cycle_direction=true`, explicitly run raw `both` to confirm that
release cannot reverse against the locked cycle. Add `revers` only as a cheap
smoke if the external config surface still exposes it.

Metrics:

```text
Sum PnL %
Profit Factor
Max Drawdown
Avg Trade %
Bars Held <= 3: count, Sum PnL %, Win Rate
flat_on_disallowed_st_flip: count, Sum PnL %
reverse_on_st_flip: count, Sum PnL %
wakeup_exit_ttl: count, Sum PnL %
position_freeze ignored/release_flat/release_reverse/release_noop counts
whipsaw bucket: ignored flip realigned before/at release, count and PnL
confirmed-opposite bucket: release applied flat/reverse, count, PnL, DD impact
```

Success criteria:

```text
Bars Held <= 3 loss improves after accounting for the open-to-open shift.
whipsaw bucket improves enough to justify the confirmed-opposite cost.
flat_on_disallowed bucket improves or its DD cost is explicitly understood.
Max Drawdown does not grow materially; for long/short this is a primary risk.
TTL/no_fresh bucket does not degrade materially.
EV by min_hold_bars is stable, not a single lucky point.
release counters explain the behavioral delta.
```

## 19. Main Risks and Guardrails

### Risk: false trade close reasons

Guardrail:

```text
ignored flip is diagnostic-only
no close-reason mapping for position_freeze_ignored_opposite_st_flip
```

### Risk: stale pending after exit-C

Guardrail:

```text
release requires state_at_bar_start == ST_ACTIVE_FREEZE and state == ST_ACTIVE_FREEZE
clear freeze on ST_STOPPING/OFF/held_pos=0
```

### Risk: off-by-one in protected window

Guardrail:

```text
entry/reverse bar inactive
protected bars are t0+1 through t0+H
unit tests cover t0, t0+H, t0+H+1
tests assert both decision-bar held_pos and execution-layer filtered_positions/bars_held
```

### Risk: restore starts a new window by accident

Guardrail:

```text
window setup is based on wakeup_started_this_bar or reverse_on_st_flip
restore_allowed_position_on_st_flip is explicitly excluded
```

### Risk: pending writers race inside apply()

Guardrail:

```text
intercept and realignment are allowed only when t <= pos_freeze_until
release is allowed only when t > pos_freeze_until
release clears pending even on invalid lock state
```

### Risk: lock_cycle_direction accidentally reverses

Guardrail:

```text
single _effective_wakeup_trade_mode helper
lock maps cycle_direction to long/short
raw both/revers ignored for release under lock
```

### Risk: schema/export drift

Guardrail:

```text
Mode D exports default arrays even when feature disabled
new columns are appended and baseline/export fixtures are updated deliberately
summary counters come from release_action arrays
Excel display names are tested
```

### Risk: EV is negative in long/short

Guardrail:

```text
measure whipsaw realignment bucket separately from confirmed-opposite bucket
Max Drawdown is a primary acceptance metric, not a secondary sanity check
```

## 20. Final Acceptance Checklist

Implementation is complete only when:

```text
old behavior is unchanged with position_freeze.enabled=false
existing Mode D schemas/goldens are intentionally migrated for appended diagnostics
wf_grid still rejects wakeup_regime and position_freeze
tester accepts position_freeze only in valid Mode D wakeup configs
ignored opposite flips do not close trades
release happens only in active wakeup-cycle state
exit-C remains higher priority than freeze release
lock_cycle_direction cannot produce reverse through release
restore_allowed_position_on_st_flip does not open a new freeze window
pending mutation order is covered by tests
decision-bar window semantics are reconciled with execution-layer bars_held
new diagnostics and summary counters are exported and tested
targeted boundary tests pass
characterization grid has been run and reviewed
```
