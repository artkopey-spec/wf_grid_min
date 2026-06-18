# Implementation Plan: Tester-Only Wakeup Cycle Trade Limit (Mode D)

This plan is based on `docs/tester_cycle_trade_limit_tz.md` and the current
code shape in `donor/supertrend_optimizer` and `wf_grid`.

## 1. Goal

Add a tester-only opt-in limit for the number of real position openings inside
one active Mode D `wakeup_regime` lifecycle.

The feature is active only when all are true:

```yaml
trade_filter:
  enabled: true
  zigzag:
    mode: D
  wakeup_regime:
    enabled: true
    exit:
      max_trades_per_cycle:
        enabled: true
```

When the current cycle reaches `max_trades`, the existing Exit C machinery ends
the cycle using `trade_filter.wakeup_regime.exit.action.mode`.

The count is per wakeup cycle, not global across the run. A later cycle can
start after the strategy returns to `OFF` and passes the full wakeup entry gate
again.

## 2. Non-Goals

- Do not change modes `A`, `B`, `C`, `A+B`, or `C+B`.
- Do not change candidate leg direction calculation.
- Do not add wf_grid runtime support for `wakeup_regime`.
- Do not pass `caller_pipeline` into `apply()`.
- Do not change the open-to-open position contract.
- Do not change behavior when the block is absent or `enabled: false`.
- Do not add per-flip suppression or a `blocked_cycle_trade_limit` action.
- Do not add summary aggregates such as
  `wakeup_exit_cycle_trade_limit_count` in v1.

## 3. Current Architecture Anchors

Config and validation:

- `donor/supertrend_optimizer/core/trade_filter_config.py`
  - `TradeFilterWakeupExitConfig`
  - `TradeFilterWakeupRegimeConfig`
  - `TRADE_FILTER_ALLOWED_KEYS`
  - `build_trade_filter_config_from_raw`
  - `_validate_wakeup_regime_block`
  - `__all__`
- `wf_grid/config/loader.py`
  - `_ALLOWED_KEYS` mirror only, so wf_grid rejects the config as unsupported
    wakeup, not as an unknown key.

Runtime:

- `donor/supertrend_optimizer/core/zigzag_st_filter.py`
  - `_allocate_apply_arrays`
  - `_finalize_apply_result`
  - `_wakeup_runtime_off`
  - `_effective_wakeup_trade_mode`
  - Mode D wakeup start branch
  - Mode D Exit C block for `ttl` and `no_fresh_candidate`
  - internal ST flip handling
  - position freeze release path
  - `ST_STOPPING -> OFF` normalization and close-on-opposite-flip paths

Diagnostics and export:

- `donor/supertrend_optimizer/core/filter_trade_diagnostics.py`
  - `_wakeup_exit_reason_at`
  - `_wakeup_position_action_at`
- `donor/supertrend_optimizer/io/excel_tester.py`
  - `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`
  - cycle sheet already counts trades per cycle from extracted trades.

Tests to extend:

- `wf_grid/tests/test_wp2_config_trade_filter.py`
- `wf_grid/tests/test_wakeup_mode_d_entry.py`
- `wf_grid/tests/test_xlsx_export.py`
- `wf_grid/tests/test_pr5_schema_contract.py`
- `donor TESTER/tests/test_xlsx_cycle_sheet.py` if the cycle-sheet contract
  needs direct regression coverage.

## 4. Config Contract

### YAML Shape

```yaml
trade_filter:
  wakeup_regime:
    exit:
      max_trades_per_cycle:
        enabled: true
        max_trades: 5
      ttl:
        enabled: true
        bars: 90
      no_fresh_candidate:
        enabled: false
      action:
        mode: block_new_entries
```

### Dataclasses

Add:

```python
@dataclass
class TradeFilterWakeupMaxTradesPerCycleConfig:
    enabled: object = False
    max_trades: object = None
```

Extend:

```python
@dataclass
class TradeFilterWakeupExitConfig:
    ttl: TradeFilterWakeupTtlExitConfig = field(...)
    no_fresh_candidate: TradeFilterWakeupNoFreshCandidateExitConfig = field(...)
    max_trades_per_cycle: TradeFilterWakeupMaxTradesPerCycleConfig = field(
        default_factory=TradeFilterWakeupMaxTradesPerCycleConfig
    )
    action: TradeFilterWakeupExitActionConfig = field(...)
```

Keep raw values as `object` until validation. Do not coerce strings or ints into
bools.

### Strict Schema

Update shared allowed keys:

```text
trade_filter.wakeup_regime.exit:
  ttl, no_fresh_candidate, max_trades_per_cycle, action

trade_filter.wakeup_regime.exit.max_trades_per_cycle:
  enabled, max_trades
```

Update `wf_grid/config/loader.py` with the same allowed-key mirror. This is
only a schema-recognition change; wf_grid must still reject any wakeup config by
its existing unsupported-pipeline policy.

### Validation

In `_validate_wakeup_regime_block`:

- Missing `max_trades_per_cycle` means `enabled=False`, `max_trades=None`.
- `enabled` must be a strict bool when present.
- If `enabled is True`, `max_trades` is required and must be `int >= 1`.
- Reject `max_trades` values such as `0`, `2.5`, `"5"`, and `None` when enabled.
- Reject unknown keys under `max_trades_per_cycle`.
- `enabled=True` with `wakeup_regime.enabled=False` passes type validation and
  is runtime no-op; do not add a separate error for this case.
- Count the limit as an enabled exit condition in the existing "at least one
  enabled exit condition" rule.

## 5. Runtime State

Add loop-local state near `cycle_direction`:

```python
cycle_trade_count = 0
```

Required invariants:

```text
outside an active/closing wakeup cycle: 0
wakeup start: 1
ST_ACTIVE_FREEZE: number of real openings in the current cycle
ST_STOPPING after block_new_entries: preserved until actual OFF
actual OFF: 0
```

Introduce a small reset helper for per-cycle runtime:

```python
def _reset_wakeup_cycle_runtime():
    cycle_direction = 0
    cycle_trade_count = 0
    pos_freeze_until = -1
    pos_freeze_pending = False
    (
        wakeup_cycle_age,
        wakeup_bars_since_fresh,
        wakeup_active_direction,
        wakeup_exit_c_fired,
    ) = _wakeup_runtime_off()
```

Because Python scoping makes a nested mutating helper awkward, implement this as
either a tiny local assignment block used consistently, or a helper returning the
new tuple of values. The important contract is one logical reset operation at
all actual `OFF` sites.

Actual `OFF` sites to cover:

1. init before the loop;
2. combined daily/time reset wipe;
3. Exit C `close_position`;
4. `ST_STOPPING + cur_pos == 0` normalization;
5. `ST_STOPPING` close on opposite ST flip;
6. any future Mode D branch that sets the wakeup lifecycle to `OFF`.

Do not reset `cycle_trade_count` when Exit C `block_new_entries` moves the
cycle to `ST_STOPPING`.

## 6. Counting Rules

Counting must run regardless of `collect_filter_diagnostics`.

Set count on wakeup start:

```python
wakeup_started_this_bar = False

# in the actual OFF -> ST_ACTIVE_FREEZE wakeup start branch
wakeup_started_this_bar = True
cycle_trade_count = 1
```

After internal flip handling and position-freeze release handling have had a
chance to set `wakeup_position_action_this_bar`, increment once:

```python
if (
    not wakeup_started_this_bar
    and state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
    and wakeup_position_action_this_bar
       in {"restore_allowed_position_on_st_flip", "reverse_on_st_flip"}
):
    cycle_trade_count += 1
```

This single rule covers normal internal ST flips and position-freeze release,
because release writes the same existing action strings.

Do not increment for:

- `flat_on_disallowed_st_flip`;
- `position_freeze_ignored_opposite_st_flip`;
- `exit_ttl`;
- `exit_no_fresh_candidate`;
- `exit_cycle_trade_limit`;
- `exit_reset`;
- `none`.

## 7. Exit C Refactor

The current Exit C block handles `ttl` and `no_fresh_candidate` inline. Add the
new trigger without duplicating the action-mode transition body.

Recommended helper:

```python
def _wakeup_exit_action_for_reason(reason: str) -> str:
    if reason == "ttl":
        return "exit_ttl"
    if reason == "no_fresh_candidate":
        return "exit_no_fresh_candidate"
    if reason == "cycle_trade_limit":
        return "exit_cycle_trade_limit"
    raise AssertionError(reason)
```

Then keep one transition block:

```python
if wakeup_exit_c_reason_this_bar is not None:
    wakeup_exit_reason_this_bar = wakeup_exit_c_reason_this_bar
    wakeup_position_action_this_bar = _wakeup_exit_action_for_reason(
        wakeup_exit_c_reason_this_bar
    )
    wakeup_exit_c_fired = True
    wakeup_exit_c_triggered_this_bar = True
    ...
```

Priority must be:

```text
ttl > no_fresh_candidate > cycle_trade_limit
```

Cycle limit condition:

```python
(
    max_trades_per_cycle_enabled
    and cycle_trade_count >= max_trades_per_cycle_max_trades
)
```

The check remains at the beginning of an active bar:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
state == ST_ACTIVE_FREEZE
not wakeup_exit_c_fired
not is_reset
```

This ensures the `N+1` opening cannot happen: after the `N`th opening is counted
on decision bar `t`, the next active bar checks Exit C before internal ST flip
handling.

## 8. Action Mode Behavior

For `exit.action.mode: block_new_entries`:

- set `state = ST_STOPPING`;
- preserve `cycle_trade_count`;
- set `wakeup_exit_c_fired = True` so Exit C does not fire again;
- allow existing `ST_STOPPING` logic to reach actual `OFF`, where the reset
  helper clears the count.

For `exit.action.mode: close_position`:

- set `state = OFF`;
- set `held_pos = 0`;
- set `wakeup_exit_close_triggered_arr[t] = 1` under diagnostics;
- reset per-cycle runtime immediately, including `cycle_trade_count`;
- preserve open-to-open behavior, so `positions[t + 1] = 0` when `t + 1 < n`.

Position freeze remains lower priority than Exit C. If cycle limit fires on a
bar, do not run internal ST flip handling or freeze release on that bar.

## 9. Diagnostic Arrays

Add arrays in `_allocate_apply_arrays`:

```python
"wakeup_cycle_trade_count_arr": np.full(n, -1, dtype=np.int64)
"wakeup_exit_cycle_trade_limit_triggered_arr": np.zeros(n, dtype=np.int8)
"wakeup_cycle_trade_limit_config_arr": np.full(
    n,
    int(max_trades_per_cycle_max_trades) if enabled else 0,
    dtype=np.int64,
)
```

Extend `_allocate_apply_arrays` signature with explicit runtime-derived values:

```python
wakeup_cycle_trade_limit_enabled: bool
wakeup_cycle_trade_limit_max_trades: int
```

Pass the same local runtime values used by the Exit C condition. Do not
re-derive a separate diagnostic predicate inside `_allocate_apply_arrays`.

Expose the three fields only in the existing Mode-D-gated block in
`_finalize_apply_result`:

```text
wakeup_cycle_trade_count
wakeup_exit_cycle_trade_limit_triggered
wakeup_cycle_trade_limit_config
```

Diagnostic write rules:

```python
if diag_enabled:
    if state in (ST_ACTIVE_FREEZE, ST_STOPPING):
        wakeup_cycle_trade_count_arr[t] = cycle_trade_count
    else:
        wakeup_cycle_trade_count_arr[t] = -1
```

The diagnostic sentinel is `-1` in `OFF`; the runtime scalar is `0` outside the
cycle.

When the limit fires:

```python
if diag_enabled:
    wakeup_exit_cycle_trade_limit_triggered_arr[t] = np.int8(1)
```

## 10. Trade-Level Mapping

Extend `donor/supertrend_optimizer/core/filter_trade_diagnostics.py`:

```python
_wakeup_exit_reason_at:
    "cycle_trade_limit" -> "wakeup_exit_cycle_trade_limit"

_wakeup_position_action_at:
    "exit_cycle_trade_limit" -> "wakeup_exit_cycle_trade_limit"
```

The exit reason should be observable at trade level as:

```text
wakeup_cycle_exit_reason = cycle_trade_limit
exit_reason = wakeup_exit_cycle_trade_limit
```

Do not map `position_freeze_ignored_opposite_st_flip` differently as part of
this feature.

## 11. Excel And Export

Add display names in `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`:

```python
"wakeup_cycle_trade_count": "Wakeup Cycle Trade Count"
"wakeup_exit_cycle_trade_limit_triggered": (
    "Wakeup Exit Cycle Trade Limit Triggered"
)
"wakeup_cycle_trade_limit_config": "Wakeup Cycle Trade Limit Config"
```

Update the frozen per-bar header snapshot in
`wf_grid/tests/test_pr5_schema_contract.py`.

Do not add new summary counters in v1. Existing summary, false-start, and
filters-summary builders should pass through new reason/action values or ignore
them safely. If a hard whitelist fails, extend it minimally.

Cycle-sheet verification should use the existing trades-per-cycle column: the
maximum active/closing `wakeup_cycle_trade_count` for a cycle segment should
match the number of trades whose `entry_index` falls in that segment.

## 12. Lite Mode

The implementation must keep behavior identical with and without diagnostics.

Requirements:

- `collect_filter_diagnostics=False` must not call `_allocate_apply_arrays`.
- `result.filter_diagnostics is None`.
- `positions` must be bit-for-bit identical to a diagnostic run when the same
  limit scenario actually fires.

Guardrail:

```text
cycle_trade_count, Exit C trigger decisions, scalar reason/action strings, and
state transitions must be loop-local runtime logic, never read from diagnostic
arrays.
```

## 13. Work Packages

### WP1 - Config Schema And Validation

Files:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
wf_grid/config/loader.py
wf_grid/tests/test_wp2_config_trade_filter.py
```

Tasks:

```text
add max-trades dataclass
extend exit dataclass
parse raw block
extend allowed-key maps in donor and wf_grid mirror
validate strict bool and int>=1
include limit in "at least one enabled exit condition"
export dataclass in __all__
prove wf_grid rejects wakeup as unsupported, not unknown key
```

### WP2 - Runtime Plumbing And Disabled Baseline

Files:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
wf_grid/tests/test_wakeup_mode_d_entry.py
```

Tasks:

```text
derive wakeup_cycle_trade_limit_enabled and max_trades locals
add cycle_trade_count runtime scalar
add/reset per-cycle runtime at all actual OFF sites
set count=1 on wakeup start
add enabled=false/no-block byte-for-byte regression test for positions
```

### WP3 - Exit C Trigger

Tasks:

```text
refactor common Exit C transition body
add cycle_trade_limit after ttl and no_fresh_candidate priority checks
set reason/action strings
honor block_new_entries and close_position action modes
ensure no internal ST flip or freeze release runs after trigger
cover max_trades=1 and max_trades=3
```

### WP4 - Counting Opens

Tasks:

```text
increment once from scalar wakeup_position_action_this_bar
count restore_allowed_position_on_st_flip
count reverse_on_st_flip
count position-freeze release when it writes one of those actions
do not count ignored freeze flips or flats
preserve ST_STOPPING count until actual OFF
```

### WP5 - Diagnostics And Trade Mapping

Files:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/io/excel_tester.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_xlsx_export.py
```

Tasks:

```text
add three Mode-D-only diagnostic arrays
update dtype/keyset tests
update action/reason allowed-value tests
add trade-level exit_reason mapping tests
add Excel display names and header snapshot
verify non-Mode-D diagnostics do not include new fields
```

### WP6 - Lite Parity And Matrix Coverage

Tasks:

```text
spy that _allocate_apply_arrays is not called in lite mode
compare diagnostic vs lite positions on a firing scenario
run matrix:
  max_trades: 1, 3, 5
  action.mode: block_new_entries, close_position
  lock_cycle_direction: false, true
include position_freeze ignored/release cases
```

## 14. Required Tests

Config:

```text
T1 absent block -> enabled=false and no-op
T2 enabled=true, max_trades=5 accepted
T3 enabled=true without max_trades rejected
T4 max_trades in {0, 2.5, "5", None} rejected when enabled
T5 enabled in {"true", 1, None} rejected
T6 unknown sibling under max_trades_per_cycle rejected
T7 only enabled limit satisfies "at least one enabled exit condition"
T8 enabled=true with wakeup_regime.enabled=false accepted as runtime no-op
T9 wf_grid rejects with wakeup_regime unsupported, not unknown key
```

Runtime:

```text
T10 max_trades=3 block_new_entries: third opening counted, next active bar
    fires cycle_trade_limit, no fourth opening
T11 max_trades=1 fires on the next active cycle bar for long and both/revers
T12 close_position sets positions[t+1]=0 and wakeup_exit_close_triggered=1
T13 block_new_entries from flat checks reason/action/positions instead of a
    brittle state assertion
T14 ttl and no_fresh_candidate beat cycle_trade_limit on the same bar
T15 after actual OFF, a new entry gate starts a new count at 1
T16 daily/time reset clears count and closes the cycle
T17 lock_cycle_direction=true still counts restores and cuts off correctly
T18 position_freeze ignored flip does not count; release restore/reverse counts
T19 enabled=false positions match previous behavior
T20 ST_STOPPING preserves diagnostic count until actual OFF
```

Observability:

```text
T21 active/closing max wakeup_cycle_trade_count matches cycle-sheet trade count
T22 Mode D diagnostics include the three new fields with dtypes:
    int64, int8, int64
T23 non-Mode-D diagnostics do not include the new fields
T24 retained per-bar export passes through the fields
T25 trade-level limit exit maps to wakeup_exit_cycle_trade_limit
```

Lite:

```text
T26 diagnostic and lite positions are identical on a firing limit scenario
T27 collect_filter_diagnostics=False returns filter_diagnostics=None
T28 collect_filter_diagnostics=False does not call _allocate_apply_arrays
T29 lite matrix covers max_trades x action.mode x lock_cycle_direction
```

## 15. Verification Commands

Focused:

```powershell
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_xlsx_export.py -q
python -m pytest wf_grid/tests/test_pr5_schema_contract.py -q
```

Nearby regression slice:

```powershell
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py wf_grid/tests/test_wp4_zigzag_per_bar.py wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp6_st_flip_event_ordering.py wf_grid/tests/test_wp7_backtest_integration.py -q
```

Broader non-slow suite if time allows:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

## 16. Implementation Order

1. Add config dataclass, parser, allowed keys, validation, and tests.
2. Add wf_grid allowed-key mirror and rejection test.
3. Derive runtime `enabled/max_trades` locals and pass them to array allocation.
4. Add diagnostic arrays and Mode-D-only finalization output.
5. Add `cycle_trade_count` runtime state and reset it at every actual OFF site.
6. Set count to `1` on actual wakeup start.
7. Refactor common Exit C transition body.
8. Add `cycle_trade_limit` as the third-priority Exit C trigger.
9. Add post-action counting by `wakeup_position_action_this_bar`.
10. Add trade-level mapping and Excel display names.
11. Add runtime, lite-parity, export, and cycle-sheet tests.
12. Run focused verification, then nearby regression slice.

## 17. Main Risks

1. Accidentally changing legacy behavior when the block is absent or disabled.
2. Resetting `cycle_trade_count` too early in `ST_STOPPING`.
3. Missing one actual `OFF` site and leaking count into the next cycle.
4. Counting from diagnostic arrays, which would break lite mode.
5. Counting ignored position-freeze flips or flats as new openings.
6. Letting internal ST flip handling run after cycle limit fires.
7. Adding diagnostics to non-Mode-D outputs.
8. Breaking exact Excel header snapshots.
9. Creating a summary counter that the TZ explicitly excludes from v1.

## 18. Final Acceptance Checklist

Implementation is complete when:

```text
config validation matches the TZ
wf_grid still rejects wakeup_regime as unsupported
enabled=false/no-block positions are unchanged
cycle_trade_count is runtime-local and lite-safe
max_trades=1 and max_trades=3 scenarios behave deterministically
ttl/no_fresh priority beats cycle_trade_limit
block_new_entries preserves count until actual OFF
close_position closes at positions[t+1]
position_freeze and lock_cycle_direction interactions are covered
Mode D diagnostics/export include exactly the new fields
non-Mode-D diagnostics do not include them
trade-level exit_reason maps to wakeup_exit_cycle_trade_limit
cycle-sheet trade counts agree with wakeup_cycle_trade_count
focused tests and nearby regressions pass
```
