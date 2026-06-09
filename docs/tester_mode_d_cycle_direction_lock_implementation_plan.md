# Implementation Plan: Tester Mode D Cycle Direction Lock

Source TZ: `docs/tester_mode_d_cycle_direction_lock_tz_v2.md`

## Goal

Add tester-only opt-in behavior for `trade_filter.zigzag.mode: D`:

```yaml
trade_filter:
  wakeup_regime:
    lock_cycle_direction: true
```

When enabled, an active Mode D wakeup cycle keeps the direction captured on the
actual wakeup start bar from `candidate_leg_direction[t_start]`. Internal
SuperTrend flips may flat or restore the position, but must not reverse the
cycle direction.

`wf_grid` runtime behavior must remain unchanged. Mode D, Exit C, and
`wakeup_regime` stay rejected for `caller_pipeline="wf_grid"`.

## Existing Code Touchpoints

- `donor/supertrend_optimizer/core/trade_filter_config.py`
  - `TradeFilterWakeupRegimeConfig`
  - `build_trade_filter_config_from_raw`
  - `TRADE_FILTER_ALLOWED_KEYS["trade_filter.wakeup_regime"]`
  - `_validate_wakeup_regime_block`
  - `validate_trade_filter(..., caller_pipeline=...)`
- `donor/supertrend_optimizer/core/zigzag_st_filter.py`
  - `_make_apply_arrays`
  - `_finalize_apply_result`
  - `_apply_mode_d_internal_st_flip`
  - main `apply()` Mode D wakeup lifecycle block
- `donor/supertrend_optimizer/io/excel_tester.py`
  - `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`
- `wf_grid/tests/test_pr5_schema_contract.py`
  - `_EXCEL_PER_BAR_HEADERS_SNAPSHOT`
- Primary tests:
  - `wf_grid/tests/test_wp2_config_trade_filter.py`
  - `wf_grid/tests/test_wakeup_mode_d_entry.py`
  - `wf_grid/tests/test_xlsx_export.py`
  - `wf_grid/tests/test_pr5_schema_contract.py`

## Phase 1: Config Schema And Validation

1. Add `lock_cycle_direction: object = False` to
   `TradeFilterWakeupRegimeConfig`.
2. Add `"lock_cycle_direction"` to
   `TRADE_FILTER_ALLOWED_KEYS["trade_filter.wakeup_regime"]`.
3. In `build_trade_filter_config_from_raw`, read:

   ```python
   lock_cycle_direction=wakeup_raw.get("lock_cycle_direction", False)
   ```

4. In `_validate_wakeup_regime_block`, validate
   `trade_filter.wakeup_regime.lock_cycle_direction` as strict bool when
   `wakeup_regime` is present. This validation must run even when
   `wakeup_regime.enabled` is `False`.
5. Do not add a validation error that requires
   `wakeup_regime.enabled=true` when `lock_cycle_direction=true`.
6. Keep unknown sibling key rejection unchanged.
7. Keep tester/wf separation in validation only:
   - tester accepts Mode D + Exit C + wakeup + lock
   - `wf_grid` still rejects Mode D / Exit C / wakeup, including wakeup with lock
   - do not pass `caller_pipeline` into `apply()`

## Phase 2: Runtime State

1. In `apply()`, derive a local boolean such as:

   ```python
   wakeup_lock_cycle_direction = (
       mode_d_enabled
       and getattr(wakeup_regime_cfg, "lock_cycle_direction", False) is True
   )
   ```

2. Add loop-local state:

   ```python
   cycle_direction = 0
   ```

3. On actual wakeup start:

   ```text
   state_at_bar_start == OFF
   state becomes ST_ACTIVE_FREEZE
   trigger_source == wakeup_regime
   ```

   capture:

   ```python
   cycle_direction = cand_dir_t
   wakeup_active_direction = cycle_direction
   held_pos = cycle_direction
   ```

4. Keep `cycle_direction` unchanged while the locked wakeup cycle is active.
   Do not update it from later `candidate_leg_direction`, `st_flip_dir`,
   `trend`, `held_pos`, or confirmed ZZ leg direction.

## Phase 3: Internal ST Flip Behavior

1. Preserve existing behavior for `lock_cycle_direction == False`.
   The current `_apply_mode_d_internal_st_flip(..., trade_mode=trade_mode)`
   path remains the legacy path.
2. For `lock_cycle_direction == True`, call the same helper with an effective
   direction mode derived from `cycle_direction`:

   ```python
   effective_trade_mode = "long" if cycle_direction == +1 else "short"
   ```

3. Only apply the locked ST flip branch when:

   ```text
   state_at_bar_start == ST_ACTIVE_FREEZE
   state == ST_ACTIVE_FREEZE
   not reset
   not Exit C triggered on this bar
   flip_dir != 0
   cycle_direction in {-1, +1}
   ```

4. Expected outcomes:
   - flip with `cycle_direction` restores `held_pos` to the locked direction
   - flip against `cycle_direction` flats `held_pos`
   - no reverse position is created inside the cycle
   - `wakeup_active_direction` remains equal to `cycle_direction`
   - reuse existing actions:
     - `flat_on_disallowed_st_flip`
     - `restore_allowed_position_on_st_flip`

## Phase 4: OFF And Reset Paths

Reset `cycle_direction = 0` anywhere the Mode D wakeup lifecycle actually
leaves active context:

1. combined reset / daily reset / time filter reset
2. Exit C `action.mode: close_position`
3. `ST_STOPPING + cur_pos == 0` normalization to `OFF`
4. ST_STOPPING close on opposite flip that sets `state = OFF`
5. any other Mode D path that sets state to `OFF` and clears wakeup runtime

For Exit C `action.mode: block_new_entries`, keep `cycle_direction` while in
`ST_STOPPING`; clear it only when the lifecycle actually reaches `OFF`.

## Phase 5: Freshness Reference

Do not add a new freshness helper parameter.

Under lock, freshness should use the locked direction because
`wakeup_active_direction` is kept equal to `cycle_direction`. Make sure the
existing `wakeup_fresh_active_direction` logic cannot temporarily switch to a
later candidate direction or opposite ST flip direction after the cycle starts.

## Phase 6: Diagnostics And Export Contract

1. Add apply array:

   ```python
   "wakeup_lock_cycle_direction_config_arr": np.full(
       n, np.int8(1 if wakeup_lock_cycle_direction else 0), dtype=np.int8
   )
   ```

2. Add `wakeup_lock_cycle_direction_config` only inside the existing Mode-D
   gated diagnostics block in `_finalize_apply_result`.
3. Do not add this field to non-Mode-D diagnostics.
4. Add display name:

   ```python
   "wakeup_lock_cycle_direction_config": "Wakeup Lock Cycle Direction Config"
   ```

   in `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`.
5. Update `_EXCEL_PER_BAR_HEADERS_SNAPSHOT` in
   `wf_grid/tests/test_pr5_schema_contract.py`.
6. Do not modify `xlsx_writer.py`, `scoring.py`,
   `filter_trade_diagnostics.py`, `test_pr6_excel_contract.py`, or
   `test_wp9_diagnostics_export.py` unless a test proves a real contract gap.

## Phase 7: Tests

Add or extend tests in `wf_grid/tests/test_wp2_config_trade_filter.py`:

1. absent `lock_cycle_direction` parses/defaults to `False`
2. `true` accepted for tester
3. `false` accepted for tester
4. non-bool values rejected: `"true"`, `1`, `null`
5. `true` with `wakeup_regime.enabled: false` accepted as no-op after strict bool validation
6. unknown sibling keys still rejected
7. `wf_grid` rejects wakeup with lock through existing pipeline gate

Add or extend tests in `wf_grid/tests/test_wakeup_mode_d_entry.py`:

1. legacy `lock=false`: `both`/`revers` still reverses on opposite ST flip
2. long lock:
   - start from `candidate_leg_direction[t_start] = +1`
   - opposite ST flip flats
   - next same-direction ST flip restores long
   - no short position appears inside the cycle
3. short lock: symmetric case for `-1`
4. `trade_mode: long` rejects short candidate start
5. `trade_mode: short` rejects long candidate start
6. candidate direction `0` does not start cycle
7. same-bar opposite ST flip on start bar does not override start direction
8. Exit C beats same-bar ST flip
9. no-fresh-candidate uses locked reference under lock
10. no-fresh-candidate legacy reference changes when lock is false
11. ST_STOPPING does not apply lock ST flip handling and clears cycle only on actual OFF
12. Mode D diagnostics include `wakeup_lock_cycle_direction_config`
13. non-Mode-D diagnostics do not include the new wakeup field

Add or extend export/contract tests:

1. retained per-bar export passes through `wakeup_lock_cycle_direction_config`
2. `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES` has the new display name
3. PR5 snapshot exact equality is updated
4. downstream action values for `both`/`revers` under lock may include:
   - `flat_on_disallowed_st_flip`
   - `restore_allowed_position_on_st_flip`

## Phase 8: Verification Commands

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

## Non-Goals

- Do not change Mode A/B/C/A+B/C+B semantics.
- Do not change candidate leg direction calculation.
- Do not add WF runtime support for Mode D.
- Do not add new `wakeup_position_action` values.
- Do not turn lock into a global `trade_mode`.
- Do not block future cycles of the opposite direction after the current cycle ends.
- Do not add separate guards for invariants already provided by existing flow:
  - candidate direction `0` entry block
  - same-bar opposite ST flip on start bar
  - Exit C priority over ST flip
