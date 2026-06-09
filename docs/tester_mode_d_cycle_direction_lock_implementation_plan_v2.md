# Implementation Plan v2: Tester Mode D Cycle Direction Lock

This document is self-contained and ready for implementation.

## Goal

Add tester-only opt-in behavior for `trade_filter.zigzag.mode: D`:

```yaml
trade_filter:
  wakeup_regime:
    lock_cycle_direction: true
```

When enabled, an active Mode D wakeup cycle keeps the direction captured on the
actual wakeup start bar from:

```text
per_bar.candidate_leg_direction[t_start]
```

Internal SuperTrend flips may flat or restore the position, but must not reverse
the cycle direction while the current wakeup lifecycle is still alive.

`wf_grid` runtime behavior must remain unchanged. Mode D, Exit C, and
`wakeup_regime` remain rejected for `caller_pipeline="wf_grid"`.

## Non-Goals

- Do not change Mode A/B/C/A+B/C+B semantics.
- Do not change candidate leg direction calculation.
- Do not add WF runtime support for Mode D.
- Do not pass `caller_pipeline` into `apply()`.
- Do not add new `wakeup_position_action` values.
- Do not turn the lock into a global `trade_mode`.
- Do not block future cycles of the opposite direction after the current cycle
  reaches `OFF`.
- Do not weaken existing Mode D validation requiring
  `wakeup_regime.enabled: true`.

## Current Code Touchpoints

Use the actual current code structure:

- `donor/supertrend_optimizer/core/trade_filter_config.py`
  - `TradeFilterWakeupRegimeConfig`
  - `TRADE_FILTER_ALLOWED_KEYS["trade_filter.wakeup_regime"]`
  - `build_trade_filter_config_from_raw`
  - `_validate_wakeup_regime_block`
  - `_validate_phase0_mode_d_cross_fields`
  - `_validate_caller_pipeline_gate`
- `donor/supertrend_optimizer/core/zigzag_st_filter.py`
  - `_allocate_apply_arrays`
  - `_finalize_apply_result`
  - `_apply_mode_d_internal_st_flip`
  - `apply()` Mode D wakeup lifecycle block
- `donor/supertrend_optimizer/io/excel_tester.py`
  - `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`
- `donor/supertrend_optimizer/core/filter_trade_diagnostics.py`
  - only inspect existing action mapping; do not change unless a test proves
    a real contract gap.
- `wf_grid/tests/test_pr5_schema_contract.py`
  - `_EXCEL_PER_BAR_HEADERS_SNAPSHOT`
- Primary tests:
  - `wf_grid/tests/test_wp2_config_trade_filter.py`
  - `wf_grid/tests/test_wakeup_mode_d_entry.py`
  - `wf_grid/tests/test_xlsx_export.py`
  - `wf_grid/tests/test_pr5_schema_contract.py`

Important: there is no `_make_apply_arrays` function in the current code. The
diagnostic array must be added through `_allocate_apply_arrays` and then exposed
from `_finalize_apply_result`.

## Core Runtime Invariants

When `lock_cycle_direction` is disabled, legacy Mode D behavior must be byte-for
behavior preserved:

- `trade_mode: both` / `revers` may reverse on internal ST flip.
- `wakeup_active_direction` may change to the ST flip direction.
- `wakeup_position_action` may be `reverse_on_st_flip`.

When `lock_cycle_direction` is enabled:

- `cycle_direction` is captured exactly once at actual wakeup start.
- `cycle_direction` is only `0`, `+1`, or `-1`.
- `cycle_direction == 0` means there is no active locked wakeup lifecycle.
- `cycle_direction` must not be updated from later candidate direction, ST
  trend, `held_pos`, confirmed ZZ leg direction, or ST flip direction.
- While the locked lifecycle is active, `wakeup_active_direction` must reflect
  `cycle_direction`, not the most recent ST flip direction.
- Opposite internal ST flip flats the position.
- Same-direction internal ST flip restores the locked-side position.
- Internal ST flip handling for lock mode is not applied in `ST_STOPPING`.
- For Exit C `action.mode: block_new_entries`, preserve `cycle_direction` while
  the lifecycle is in `ST_STOPPING`; clear it only on actual `OFF`.

## Phase 0: Pre-Implementation Characterization

Before changing runtime behavior, make sure existing legacy expectations are
covered or add characterization tests where coverage is missing:

1. Mode D, `trade_mode: both` or `revers`, no lock:
   - wakeup starts long;
   - opposite ST flip reverses to short;
   - `wakeup_position_action == "reverse_on_st_flip"`;
   - `wakeup_active_direction == -1`.
2. Existing long-only and short-only Mode D internal ST flip behavior:
   - disallowed flip flats;
   - allowed flip restores;
   - action values are existing values.
3. Existing trade-level diagnostics mapping:
   - `reverse_on_st_flip -> wakeup_reverse_on_st_flip`;
   - `flat_on_disallowed_st_flip -> wakeup_flat_on_disallowed_st_flip`;
   - `restore_allowed_position_on_st_flip -> st_flip`.

Do not change these contracts as part of the lock implementation unless the
implementation exposes a real failing contract and the change is explicitly
covered by tests.

## Phase 1: Config Schema And Validation

1. Add the field:

   ```python
   lock_cycle_direction: object = False
   ```

   to `TradeFilterWakeupRegimeConfig`.

2. Add `"lock_cycle_direction"` to:

   ```python
   TRADE_FILTER_ALLOWED_KEYS["trade_filter.wakeup_regime"]
   ```

3. In `build_trade_filter_config_from_raw`, preserve raw value without coercion:

   ```python
   lock_cycle_direction=wakeup_raw.get("lock_cycle_direction", False)
   ```

4. In `_validate_wakeup_regime_block`, validate
   `trade_filter.wakeup_regime.lock_cycle_direction` as a strict bool whenever
   the key is present:

   ```text
   valid: true, false
   invalid: "true", 1, null, [], {}
   ```

   This validation must happen even when `wakeup_regime.enabled` is `False`.

5. Do not add a new validation rule saying
   `lock_cycle_direction=true requires wakeup_regime.enabled=true`.

6. Do not weaken the existing Mode D cross-field rule:

   ```text
   trade_filter.zigzag.mode='D'
     requires trade_filter.wakeup_regime.enabled: true
   ```

   Therefore, a full Mode D config with:

   ```yaml
   wakeup_regime:
     enabled: false
     lock_cycle_direction: true
   ```

   is not an accepted no-op. It should still receive the existing
   `mode_d_requires_wakeup_enabled` validation error after the bool value itself
   has been accepted as a valid bool.

7. Keep tester/WF separation in validation only:
   - tester accepts Mode D + Exit C + enabled wakeup + lock;
   - `wf_grid` still rejects Mode D;
   - `wf_grid` still rejects Exit C;
   - `wf_grid` still rejects `wakeup_regime`, including wakeup with lock.

## Phase 2: Runtime Flag And State

In `apply()`, derive a local bool after Mode D wakeup config is resolved and
before `_allocate_apply_arrays` is called:

```python
wakeup_regime_cfg = getattr(trade_filter_config, "wakeup_regime", None)
wakeup_lock_cycle_direction = (
    mode_d_enabled
    and wakeup_regime_cfg is not None
    and getattr(wakeup_regime_cfg, "enabled", False) is True
    and getattr(wakeup_regime_cfg, "lock_cycle_direction", False) is True
)
```

Add loop-local state near the existing wakeup runtime state:

```python
cycle_direction = 0
```

Clear this state only at explicit lifecycle end sites. Do not use
`_wakeup_runtime_off()` calls as the clearing pattern: two current
`ST_STOPPING -> OFF` transitions do not call `_wakeup_runtime_off()` on the same
bar.

The required clearing rule is:

```python
cycle_direction = 0
```

at every branch that actually sets the lifecycle to `OFF`, and nowhere else.
Do not clear `cycle_direction` simply because state is not `ST_ACTIVE_FREEZE`;
`ST_STOPPING` must preserve the value until actual `OFF`.

## Phase 3: Wakeup Start

The actual wakeup start is the existing OFF departure:

```text
state_at_bar_start == OFF
not reset
time_filter permits entry
mode_d_enabled
wakeup_entry_all_ok
state becomes ST_ACTIVE_FREEZE
trigger_source_arr[t] == wakeup_regime
```

On that transition:

```python
state = ZigZagFSMState.ST_ACTIVE_FREEZE
held_pos = cand_dir_t
trigger_source_arr[t] = _TRIGGER_SOURCE_WAKEUP
```

Add lock behavior in the same branch:

```python
if wakeup_lock_cycle_direction:
    cycle_direction = cand_dir_t
    wakeup_active_direction = cycle_direction
else:
    cycle_direction = 0
```

No extra guard for `cand_dir_t == 0` is needed in the start branch because
`_evaluate_wakeup_entry` already requires:

```text
candidate_direction in {-1, +1}
trade_mode allows candidate_direction
```

Add tests to lock that invariant, but do not duplicate the runtime guard unless
a test proves the current invariant is broken.

Same-bar opposite ST flip on the start bar must not override the start
direction. This is already structurally protected by the internal ST flip gate:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
```

On the start bar, `state_at_bar_start == OFF`.

## Phase 4: Freshness Reference

Do not add a new freshness helper parameter and do not add a new lock-specific
freshness branch.

Freshness must remain an emergent consequence of the existing reference:

```text
freshness reference = wakeup_active_direction
```

Implementation requirements:

1. On wakeup start under lock, assign:

   ```python
   wakeup_active_direction = cycle_direction
   ```

2. Under lock, the internal ST flip handler must never assign
   `wakeup_active_direction = flip_dir`.
3. Under lock, after any internal ST flip, reassert:

   ```python
   wakeup_active_direction = cycle_direction
   ```

The existing freshness code can then continue to use its current
`wakeup_fresh_active_direction` logic. Tests must prove the locked behavior and
the legacy `lock=false` behavior, but the implementation must avoid introducing
a second source of truth for freshness.

## Phase 5: Internal ST Flip Behavior

Preserve legacy behavior when `wakeup_lock_cycle_direction` is false:

```python
held_pos, wakeup_active_direction, wakeup_position_action_this_bar = (
    _apply_mode_d_internal_st_flip(
        held_pos=held_pos,
        wakeup_active_direction=wakeup_active_direction,
        flip_dir=flip_dir,
        trade_mode=trade_mode,
    )
)
```

For lock mode, apply locked ST flip handling only when all are true:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
state == ST_ACTIVE_FREEZE
not reset
Exit C did not trigger on this bar
flip_dir != 0
cycle_direction in {-1, +1}
```

Use the existing helper with an effective one-sided trade mode:

```python
effective_trade_mode = "long" if cycle_direction == +1 else "short"
held_pos, _active_direction, wakeup_position_action_this_bar = (
    _apply_mode_d_internal_st_flip(
        held_pos=held_pos,
        wakeup_active_direction=cycle_direction,
        flip_dir=flip_dir,
        trade_mode=effective_trade_mode,
    )
)
wakeup_active_direction = cycle_direction
```

Expected outcomes:

- `cycle_direction == +1`, `flip_dir == -1`:
  - `held_pos = 0`;
  - action `flat_on_disallowed_st_flip`;
  - no short position.
- `cycle_direction == +1`, `flip_dir == +1`:
  - `held_pos = +1`;
  - action `restore_allowed_position_on_st_flip`.
- `cycle_direction == -1`, `flip_dir == +1`:
  - `held_pos = 0`;
  - action `flat_on_disallowed_st_flip`;
  - no long position.
- `cycle_direction == -1`, `flip_dir == -1`:
  - `held_pos = -1`;
  - action `restore_allowed_position_on_st_flip`.

Do not add new action strings.

## Phase 6: OFF, Reset, And ST_STOPPING

Clear `cycle_direction = 0` only when the locked wakeup lifecycle actually ends.
The current OFF/reset sites that must be covered are:

1. Combined reset wipe:
   - daily reset;
   - time filter reset.
2. Exit C `action.mode: close_position`.
3. `ST_STOPPING + cur_pos == 0` normalization to `OFF`.
4. `ST_STOPPING` close on opposite flip that sets `state = OFF`.
5. Any future Mode D branch that sets `state = OFF` and clears wakeup runtime.

Implement the reset explicitly at those sites. In the current code, the first
two sites already clear wakeup runtime on the same bar, while the two
`ST_STOPPING -> OFF` sites set `state = OFF` directly and rely on later
per-bar logic to clear wakeup runtime. `cycle_direction` must not inherit that
one-bar delay.

For Exit C `action.mode: block_new_entries`:

- transition to `ST_STOPPING`;
- preserve `cycle_direction`;
- do not run locked internal ST flip handling in `ST_STOPPING`;
- clear `cycle_direction` only when the lifecycle reaches actual `OFF`.

Do not modify the existing `elif not wakeup_state_active:` branch solely for
`cycle_direction`. Let legacy wakeup runtime behavior remain as-is there. The
new state should be controlled by the explicit OFF-site assignments above.

Diagnostics:

- Keep existing `wakeup_regime_active` semantics unless a test proves the TZ
  requires changing it.
- For locked `ST_STOPPING`, no new runtime behavior is required. `cycle_direction`
  may remain as residual cycle context until actual `OFF`, while existing
  wakeup runtime fields may continue to follow legacy clearing behavior.

## Phase 7: Diagnostics And Export Contract

Add an apply array in `_allocate_apply_arrays`.

Use the exact runtime boolean derived in `apply()`. Do not derive an independent
diagnostic predicate inside `_allocate_apply_arrays`; the diagnostic echo must
match the runtime branch selector by construction.

Extend `_allocate_apply_arrays` with a keyword-only argument:

```python
wakeup_lock_cycle_direction: bool
```

Pass the local `wakeup_lock_cycle_direction` computed in `apply()` into
`_allocate_apply_arrays`.

Inside `_allocate_apply_arrays`, add:

```python
"wakeup_lock_cycle_direction_config_arr": np.full(
    n,
    np.int8(1 if wakeup_lock_cycle_direction else 0),
    dtype=np.int8,
)
```

Expose the field only inside the existing Mode-D-gated diagnostics block in
`_finalize_apply_result`:

```python
"wakeup_lock_cycle_direction_config": arrays[
    "wakeup_lock_cycle_direction_config_arr"
],
```

Do not add this field to non-Mode-D diagnostics.

Invariant:

```text
wakeup_lock_cycle_direction_config == int(wakeup_lock_cycle_direction)
```

for every bar in a Mode D run. Tests must cover:

- lock enabled -> every value is `1`;
- lock disabled or absent -> every value is `0`;
- non-Mode-D -> field is absent.

Do not add the field to the local `filter_diagnostics_out` dictionary assembled
near the end of `apply()`. That dictionary is not the live return path; the
actual returned diagnostics are built in `_finalize_apply_result`.

Add display name:

```python
"wakeup_lock_cycle_direction_config": "Wakeup Lock Cycle Direction Config"
```

to `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`.

Update `_EXCEL_PER_BAR_HEADERS_SNAPSHOT` in:

```text
wf_grid/tests/test_pr5_schema_contract.py
```

Do not modify these files unless a failing test proves a real contract gap:

- `wf_grid/export/xlsx_writer.py`
- `wf_grid/ranking/scoring.py`
- `donor/supertrend_optimizer/core/filter_trade_diagnostics.py`
- `wf_grid/tests/test_pr6_excel_contract.py`
- `wf_grid/tests/test_wp9_diagnostics_export.py`

Known trade-level diagnostics contract:

```text
restore_allowed_position_on_st_flip -> st_flip
```

This is existing behavior. The lock feature may make this action appear for
`trade_mode: both` / `revers`, but the mapping itself is not part of this
feature unless explicitly changed and tested.

Also verify downstream summaries and aggregations that consume
`wakeup_position_action` or trade-level `exit_reason`. The expected change under
lock is a distribution shift, not a schema change or crash:

- `reverse_on_st_flip` becomes less common for `both` / `revers`;
- `flat_on_disallowed_st_flip` and `restore_allowed_position_on_st_flip` may
  appear for `both` / `revers`;
- `restore_allowed_position_on_st_flip` keeps the existing trade-level fallback
  behavior.

## Phase 8: Tests

### Config Tests

Add or extend tests in `wf_grid/tests/test_wp2_config_trade_filter.py`:

1. `lock_cycle_direction` absent parses/defaults to `False`.
2. `lock_cycle_direction: true` accepted for tester when Mode D wakeup is fully
   valid and enabled.
3. `lock_cycle_direction: false` accepted for tester.
4. Non-bool values rejected:
   - `"true"`;
   - `1`;
   - `null`.
5. With Mode D and `wakeup_regime.enabled: false`,
   `lock_cycle_direction: true` is a valid bool but the config is still rejected
   by the existing `mode_d_requires_wakeup_enabled` rule.
6. Unknown sibling keys under `trade_filter.wakeup_regime` still rejected.
7. `wf_grid` rejects wakeup with lock through existing pipeline gate:
   - `mode_d_unsupported_pipeline`;
   - `exit_c_unsupported_pipeline`;
   - `wakeup_regime_unsupported_pipeline`.

### Runtime Tests

Add or extend tests in `wf_grid/tests/test_wakeup_mode_d_entry.py`:

1. Legacy `lock=false`: `both` / `revers` still reverses on opposite ST flip.
2. Long lock:
   - start from `candidate_leg_direction[t_start] = +1`;
   - opposite ST flip flats;
   - next same-direction ST flip restores long;
   - no short position appears inside the cycle;
   - `wakeup_active_direction` remains `+1` while active;
   - `positions[t + 1]` follows the open-to-open contract:
     `+1` after start, `0` after flat, `+1` after restore.
3. Short lock:
   - symmetric case for `-1`;
   - no long position appears inside the cycle;
   - `wakeup_active_direction` remains `-1` while active;
   - `positions[t + 1]` follows the open-to-open contract:
     `-1` after start, `0` after flat, `-1` after restore.
4. `trade_mode: long` rejects short candidate start.
5. `trade_mode: short` rejects long candidate start.
6. Candidate direction `0` does not start a cycle.
7. Same-bar opposite ST flip on start bar does not override start direction.
8. Exit C beats same-bar ST flip:
   - Exit C action wins;
   - locked ST flip action is not applied on that bar.
9. `no_fresh_candidate` under lock uses the locked direction reference.
10. Legacy `no_fresh_candidate` reference still changes when lock is false and
    `both` / `revers` reverses active direction.
11. `ST_STOPPING` with Exit C `block_new_entries`:
    - lock ST flip handling is not applied;
    - `cycle_direction` is preserved until actual `OFF`;
    - cycle context is cleared on actual `OFF`.
12. Mode D diagnostics include `wakeup_lock_cycle_direction_config`.
13. Non-Mode-D diagnostics do not include `wakeup_lock_cycle_direction_config`.
14. Mode D diagnostics exact keyset is updated intentionally:
    - build an expected Mode D wakeup-diagnostics key set;
    - include `wakeup_lock_cycle_direction_config`;
    - assert no other unexpected `wakeup_*` diagnostics were added.
15. Under lock, `both` / `revers` action values may include:
    - `flat_on_disallowed_st_flip`;
    - `restore_allowed_position_on_st_flip`.
16. Same-direction restore under lock may emit
    `restore_allowed_position_on_st_flip` even when the position was already in
    the locked direction; this is inherited from the existing helper and is
    accepted behavior.

### Export And Contract Tests

Add or extend tests:

1. Retained per-bar export passes through
   `wakeup_lock_cycle_direction_config`.
2. `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES` has the new display name.
3. PR5 snapshot exact equality is updated.
4. Trade-level diagnostics mapping for `restore_allowed_position_on_st_flip`
   remains the existing expected behavior unless intentionally changed.
5. Summary / aggregation tests cover the new possible action distribution for
   `both` / `revers` under lock.
6. Diagnostic echo tests prove the runtime bool and exported config field cannot
   diverge:
   - lock true -> all `1`;
   - lock false / absent -> all `0`.

## Phase 9: Verification Commands

Run focused tests first:

```powershell
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_xlsx_export.py -q
python -m pytest wf_grid/tests/test_pr5_schema_contract.py -q
```

Then run the nearby regression slice:

```powershell
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py wf_grid/tests/test_wp4_zigzag_per_bar.py wf_grid/tests/test_wp5_zigzag_fsm.py -q
```

If time allows, run the broader non-slow suite:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

## Implementation Order

1. Add config field, raw loader propagation, whitelist, and strict bool
   validation.
2. Add config tests and run focused config suite.
3. Derive `wakeup_lock_cycle_direction` in `apply()`.
4. Pass `wakeup_lock_cycle_direction` into `_allocate_apply_arrays` and add the
   diagnostics output without relying on the dead local `filter_diagnostics_out`
   dictionary in `apply()`.
5. Add `cycle_direction` runtime state.
6. Capture `cycle_direction` on actual wakeup start.
7. Freeze freshness indirectly by preserving `wakeup_active_direction` under
   lock; do not add a separate freshness branch.
8. Split legacy and locked internal ST flip handling.
9. Add explicit `cycle_direction = 0` at all four current OFF/reset sites.
10. Add runtime tests for long lock, short lock, legacy no-lock behavior, Exit C
    priority, freshness, and ST_STOPPING.
11. Add diagnostics/export contract tests.
12. Run focused and regression verification commands.

## Main Risks

1. Accidentally changing legacy `lock=false` Mode D behavior.
2. Weakening the existing Mode D validation that requires enabled wakeup.
3. Clearing `cycle_direction` too early in `ST_STOPPING`.
4. Missing one OFF/reset site and leaking stale `cycle_direction` into a future
   cycle.
5. Reintroducing a second freshness source instead of relying on frozen
   `wakeup_active_direction`.
6. Adding `wakeup_lock_cycle_direction_config` to non-Mode-D diagnostics.
7. Breaking exact Excel display-name snapshots.
8. Misreading `restore_allowed_position_on_st_flip` as a new trade-level exit
   reason instead of preserving its current mapping.
