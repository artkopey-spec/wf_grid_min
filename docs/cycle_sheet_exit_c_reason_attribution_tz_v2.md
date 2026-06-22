# TZ v2: Cycle Sheet Exit-C Reason Attribution

## 0. Runtime Invariants

The implementation relies on these runtime facts from
`donor/supertrend_optimizer/core/zigzag_st_filter.py`:

- `wakeup_exit_reason` is written at most once per bar/cycle event
  (`:3105`, `:3425-3472`, `:3662`).
- A reason on the first `OFF` bar corresponds to `close_position`; the
  `block_new_entries` path keeps the reason inside the active segment
  (`:3480-3487`).
- Reset and Exit-C are mutually exclusive on the same bar through the
  `not is_reset` guard (`:3426`).

`donor TESTER/tests/conftest.py:33-36` fixes namespace resolution so tests in
`donor TESTER/tests/` import the mutable `donor/.../excel_tester.py`.

## 1. Goal

In the tester `cycle` sheet, completed Mode D wakeup cycles must show the raw
Exit-C termination reason selected by runtime diagnostics:

- `cycle_take_profit`
- `ttl`
- `no_fresh_candidate`
- `local_median_stop`
- `cycle_trade_limit`

instead of the generic `FSM_OFF`.

If the selected Exit-C reason is recorded on the first `OFF` bar immediately
after the active segment, the ZigZag-leg metrics for that cycle must include
confirm events from that `OFF` bar. Segment/OHLC metrics must remain based only
on the active segment.

This is a tester export attribution change only. It must not change runtime
FSM behavior or public export schemas.

## 2. Scope

Change only:

- `donor/supertrend_optimizer/io/excel_tester.py`
- tests in `donor TESTER/tests/test_xlsx_cycle_sheet.py`

Allowed additions in `excel_tester.py`:

- private module-level constant `_EXIT_C_REASONS`
- private helper `_find_cycle_exit_c_reason(...)`

Do not change:

- `CYCLE_SHEET_COLUMNS`
- volume cycle sheet columns
- runtime/FSM implementation
- `zigzag_st_filter.py`
- public API
- `wf_grid` export behavior

## 3. Current Code Facts

`_build_cycle_sheet_df(...)` currently:

- requires these diagnostics:
  - `trade_filter_state`
  - `trade_filter_trigger_source`
  - `candidate_trigger_threshold`
  - `zigzag_reversal_threshold`
  - `local_window`
  - `daily_reset_event`
  - `candidate_leg_direction`
- reads `time_filter_reset_event` optionally;
- computes ZigZag per-bar metrics before `end_reason`;
- uses `[start_bar, end_bar]` for current cycle metrics;
- sets `end_reason` as:
  - `daily_reset` if `daily_reset_event[off_bar] == 1`;
  - `time_filter_reset` if `time_filter_reset_event[off_bar] == 1`;
  - otherwise `FSM_OFF`.

The v2 implementation must preserve existing behavior unless Mode D attribution
is explicitly enabled by valid optional diagnostics.

## 4. Optional Diagnostics

Read these diagnostics with `.get(...)` only:

- `zigzag_mode`
- `wakeup_exit_reason`

Do not add either key to `required_keys`.

Exit-C attribution is enabled only when all conditions are true:

- `zigzag_mode` is present;
- `zigzag_mode` can be safely materialized as an array with at least one item;
- `str(zigzag_mode[0]) == "D"`;
- `wakeup_exit_reason` is present;
- `wakeup_exit_reason` can be safely materialized as an array-like object.

If any condition fails, attribution is disabled and legacy behavior remains:

- `daily_reset`
- `time_filter_reset`
- otherwise `FSM_OFF`

Malformed optional diagnostics must disable attribution instead of raising.

## 5. Exit-C Reasons

Add:

```python
_EXIT_C_REASONS = frozenset({
    "cycle_take_profit",
    "ttl",
    "no_fresh_candidate",
    "local_median_stop",
    "cycle_trade_limit",
})
```

Do not include `reset`, `opposite_st_flip`, `none`, wakeup position actions, or
trade-level mapped names such as `wakeup_exit_ttl`.

The cycle sheet must report the raw runtime diagnostic value, for example
`ttl`, not a mapped trade-level value.

## 6. Helper Contract

Add:

```python
def _find_cycle_exit_c_reason(
    wakeup_exit_reason_arr: np.ndarray,
    start_bar: int,
    off_bar: int,
    n: int,
) -> tuple[str | None, int | None]:
    ...
```

The helper must:

- search the inclusive window `[start_bar, min(off_bar, n - 1)]`;
- additionally clamp to `len(wakeup_exit_reason_arr) - 1`;
- search only values whose string form is in `_EXIT_C_REASONS`;
- skip `None`, missing, `NaN`, `none`, and unknown values;
- return `(reason, reason_bar)` only when exactly one Exit-C match is found;
- return `(None, None)` when there are zero matches;
- return `(None, None)` when there is more than one match;
- return `(None, None)` for invalid bounds, empty arrays, scalar diagnostics,
  too-short arrays, or values that cannot be safely inspected;
- never raise on malformed or synthetic diagnostics.

This helper is fail-closed attribution. It must not replay runtime priority or
choose an arbitrary reason when the diagnostic window is ambiguous.

## 7. Per-Cycle Computation Order

For every completed cycle from `_completed_cycle_segments(state_arr)`:

1. Compute `start_bar` and `end_bar` from the completed active segment.
2. Compute `off_bar = end_bar + 1`.
3. Resolve optional raw Exit-C attribution with `_find_cycle_exit_c_reason(...)`
   only when attribution is enabled.
4. Determine `end_reason`.
5. Determine the ZigZag `leg_interval`.
6. Compute ZigZag-leg metrics from `leg_interval`.
7. Compute OHLC/segment metrics from `segment_interval`.
8. Append the row.

Do not compute ZigZag-leg metrics before `off_bar`, raw attribution, and
`end_reason` are known.

## 8. End Reason Priority

For completed cycles, determine `end_reason` strictly in this order:

1. `daily_reset`, if `off_bar < n` and `daily_reset_event[off_bar] == 1`;
2. `time_filter_reset`, if `off_bar < n`, `time_filter_reset_event` is present,
   and `time_filter_reset_event[off_bar] == 1`;
3. raw Exit-C reason from `_find_cycle_exit_c_reason(...)`;
4. otherwise `FSM_OFF`.

Reset priority intentionally differs from trade-level diagnostic mapping. This
cycle sheet contract must remain:

```text
daily_reset > time_filter_reset > Exit-C > FSM_OFF
```

If a reset and an Exit-C reason are both present on `off_bar`, the row reason is
the reset reason and the Exit-C reason must not affect leg interval extension.

## 9. Separate Segment and Leg Intervals

Use two distinct variables and do not reuse one interval for both purposes:

```text
segment_interval = [start_bar, end_bar]
leg_interval = [start_bar, end_bar] by default
```

`segment_interval` is the only source for:

- cycle start/end datetimes;
- bars in cycle;
- `Start bar index`;
- `End bar index`;
- start/end price;
- cycle high/low;
- cycle size;
- max move;
- max drawdown;
- trade counting and final trade metrics.

`leg_interval` is the only source for ZigZag-leg metrics:

- `Ног ZigZag в цикле`
- `Медиана ног`
- `Ног выше порога триггера кандидата`
- `Макс. высота ноги`
- `Доля ног выше порога, %`

## 10. Leg Interval Extension

Default:

```text
leg_interval = [start_bar, end_bar]
```

Extend to:

```text
leg_interval = [start_bar, off_bar]
```

only when all conditions are true:

- attribution is enabled;
- `_find_cycle_exit_c_reason(...)` found exactly one raw Exit-C reason;
- that raw Exit-C reason is selected as `end_reason`;
- `exit_reason_bar == off_bar`;
- `off_bar < n`.

Do not extend `leg_interval` when:

- attribution is disabled;
- no raw Exit-C reason is found;
- multiple raw Exit-C reasons are found;
- the raw Exit-C reason is inside `[start_bar, end_bar]`;
- `daily_reset` or `time_filter_reset` wins over Exit-C;
- `off_bar >= n`.

The extension must affect only the five ZigZag-leg fields listed in section 9.

## 11. Implementation Notes

Recommended local structure inside `_build_cycle_sheet_df`:

```python
segment_interval = slice(start_bar, end_bar + 1)
off_bar = end_bar + 1

exit_c_reason = None
exit_reason_bar = None
if exit_c_attribution_enabled:
    exit_c_reason, exit_reason_bar = _find_cycle_exit_c_reason(
        wakeup_exit_reason_arr,
        start_bar,
        off_bar,
        n,
    )

if off_bar < n and int(daily_reset_arr[off_bar]) == 1:
    end_reason = "daily_reset"
elif (
    off_bar < n
    and time_filter_reset_arr is not None
    and int(time_filter_reset_arr[off_bar]) == 1
):
    end_reason = "time_filter_reset"
elif exit_c_reason is not None:
    end_reason = exit_c_reason
else:
    end_reason = "FSM_OFF"

leg_end_bar = end_bar
if (
    exit_c_reason is not None
    and end_reason == exit_c_reason
    and exit_reason_bar == off_bar
    and off_bar < n
):
    leg_end_bar = off_bar
leg_interval = slice(start_bar, leg_end_bar + 1)
```

The exact code may differ, but the behavior and interval separation must match.

## 12. Test Plan

Add tests in `donor TESTER/tests/test_xlsx_cycle_sheet.py`.

Use existing test style:

- monkeypatch `compute_zigzag_per_bar`;
- monkeypatch `compute_confirmed_legs_reset_aware` when needed;
- manually set `confirm_event` and `last_confirmed_leg_height_pct`;
- do not depend on real ZigZag behavior from synthetic prices;
- avoid fragile duplicated mojibake column literals when a local alias or
  existing expected-columns helper is clearer.

### Required Cases

1. `close_position + local_median_stop` on `off_bar`
   - Mode D enabled;
   - raw reason is `local_median_stop`;
   - `End bar index == off_bar - 1`;
   - bars in cycle remain active-segment length;
   - OHLC metrics remain based on `[start_bar, end_bar]`;
   - confirm leg on `off_bar` is counted in ZigZag-leg metrics.

2. `block_new_entries + local_median_stop` inside active segment
   - Mode D enabled;
   - raw reason is `local_median_stop`;
   - `leg_interval` is not extended;
   - confirm leg on `off_bar` is not counted.

3. Raw reason coverage
   - `ttl`;
   - `no_fresh_candidate`;
   - `cycle_trade_limit`;
   - `cycle_take_profit`;
   - each appears as raw `end_reason`.

4. `daily_reset` priority
   - Mode D enabled;
   - raw Exit-C reason also present;
   - result is `daily_reset`;
   - if raw Exit-C is on `off_bar`, `off_bar` confirm leg is not counted.

5. `time_filter_reset` priority
   - Mode D enabled;
   - raw Exit-C reason also present;
   - no daily reset;
   - result is `time_filter_reset`;
   - if raw Exit-C is on `off_bar`, `off_bar` confirm leg is not counted.

6. Non-D guard
   - `wakeup_exit_reason` is present;
   - `zigzag_mode != "D"`;
   - result remains legacy `FSM_OFF`;
   - confirm leg on `off_bar` is not counted.

7. Legacy guard without `wakeup_exit_reason`
   - `zigzag_mode == "D"` may be present;
   - no `wakeup_exit_reason`;
   - result remains legacy `FSM_OFF`;
   - confirm leg on `off_bar` is not counted.

8. Multiple Exit-C reasons in lookup window
   - Mode D enabled;
   - two Exit-C matches in `[start_bar, off_bar]`;
   - no exception;
   - result is `FSM_OFF`;
   - leg interval is not extended.

9. Short or malformed optional diagnostics
   - Mode D/malformed `zigzag_mode` or short `wakeup_exit_reason`;
   - no exception;
   - legacy behavior remains.

10. Direct helper guards
    - empty array returns `(None, None)`;
    - invalid bounds return `(None, None)`;
    - short array does not raise;
    - one valid match returns `(reason, bar)`;
    - multiple matches return `(None, None)`.

## 13. Verification

Run:

```bash
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
```

If failures indicate nearby export behavior, also run the relevant existing
cycle/export tests that exercise tester result export.

Do not run broad unrelated suites unless needed.

## 14. Acceptance Criteria

- Mode D Exit-C cycles show raw Exit-C reason instead of `FSM_OFF`.
- Reason priority is exactly:

```text
daily_reset > time_filter_reset > Exit-C > FSM_OFF
```

- Non-D behavior does not change.
- Legacy behavior without `wakeup_exit_reason` does not change.
- Malformed optional diagnostics do not raise.
- OHLC metrics and `End bar index` are never extended to `off_bar`.
- Confirm leg on `off_bar` is counted only when selected Exit-C reason is on
  `off_bar` and no reset reason wins.
- Multiple Exit-C reasons in one lookup window fail closed to `FSM_OFF`.
- `CYCLE_SHEET_COLUMNS` does not change.
- Runtime/FSM and `wf_grid` export are untouched.
