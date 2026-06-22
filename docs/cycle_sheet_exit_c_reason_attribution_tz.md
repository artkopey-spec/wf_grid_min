# TZ: Cycle Sheet Exit-C Reason Attribution

> Superseded by `docs/cycle_sheet_exit_c_reason_attribution_tz_v2.md`.
> Implement v2 only; this v1 document is retained for historical context.

## 1. Goal

In Mode D, the `cycle` sheet must show the real wakeup-cycle termination reason:

- `local_median_stop`
- `ttl`
- `no_fresh_candidate`
- `cycle_trade_limit`
- `cycle_take_profit`

instead of the catch-all `FSM_OFF`.

If an Exit-C condition with `action.mode = close_position` fires on the first
`OFF` bar after the active segment, the confirm leg on that bar must be included
in the cycle ZigZag leg metrics.

## 2. Scope

Change only:

- `donor/supertrend_optimizer/io/excel_tester.py`
- function `_build_cycle_sheet_df`

Allowed additions in the same module:

- private module-level constant `_EXIT_C_REASONS`
- private helper `_find_cycle_exit_c_reason(...)`

Do not change:

- runtime/FSM: `zigzag_st_filter.py`
- `CYCLE_SHEET_COLUMNS`
- public API
- `wf_grid` export

This is tester-only and Mode-D-only.

## 3. Optional Diagnostics

Read these keys optionally through `filter_diagnostics.get(...)`:

- `zigzag_mode`
- `wakeup_exit_reason`

Do not add either key to `required_keys`.

Exit-C attribution is enabled only when all conditions are true:

- `zigzag_mode` is present;
- `str(zigzag_mode[0]) == "D"`;
- `wakeup_exit_reason` is present.

If any condition is false, behavior remains unchanged: `daily_reset`,
`time_filter_reset`, otherwise `FSM_OFF`.

## 4. Exit-C Reasons

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

Export must not replay or reinterpret runtime priority. It only reads the raw
reason already selected by runtime.

Current runtime priority is:

```text
cycle_take_profit > ttl > no_fresh_candidate > local_median_stop > cycle_trade_limit
```

## 5. Reason Lookup

For each completed cycle:

- `segment_interval = [start_bar, end_bar]`
- `off_bar = end_bar + 1`
- `reason_window = [start_bar, min(off_bar, n - 1)]`

Within `reason_window`, find bars where `wakeup_exit_reason` is in
`_EXIT_C_REASONS`.

Fail-safe rule:

- exactly 1 match: use this raw reason;
- 0 matches: skip Exit-C attribution;
- more than 1 match: skip Exit-C attribution;
- export never raises on malformed or synthetic diagnostics.

Helper contract:

```python
def _find_cycle_exit_c_reason(
    wakeup_exit_reason_arr: np.ndarray,
    start_bar: int,
    off_bar: int,
    n: int,
) -> tuple[str | None, int | None]:
    ...
```

Return `(reason, reason_bar)` only when exactly one Exit-C reason is found.
Otherwise return `(None, None)`.

The helper must clamp the lookup end to `min(off_bar, n - 1)`.

## 6. End Reason Priority

Determine `end_reason` in this order:

1. `daily_reset`, if `off_bar < n` and `daily_reset_event[off_bar] == 1`;
2. `time_filter_reset`, if `off_bar < n`, `time_filter_reset_event` is present,
   and `time_filter_reset_event[off_bar] == 1`;
3. raw Exit-C reason from `reason_window`;
4. otherwise `FSM_OFF`.

`reset` is not an Exit-C reason and must not be included in
`_EXIT_C_REASONS`.

## 7. Computation Order

Inside `_build_cycle_sheet_df`, for every completed cycle:

1. Compute `off_bar`.
2. Compute `reason_window`.
3. Determine `end_reason` and `exit_reason_bar`.
4. Then compute ZigZag leg metrics.

Keep these intervals separate:

- `segment_interval = slice(start_bar, end_bar + 1)` for OHLC, bar count,
  start/end price, high/low, size, max move, drawdown, and `End bar index`.
- `leg_interval` for ZigZag leg metrics only.

## 8. Leg Interval Extension

Default:

```text
leg_interval = [start_bar, end_bar]
```

Exception:

If the Exit-C reason is found exactly on `off_bar`, then:

```text
leg_interval = [start_bar, off_bar]
```

This applies to any Exit-C reason found on `off_bar`, not only
`local_median_stop`.

This extension affects only:

- `Ног ZigZag в цикле`
- `Медиана ног`
- `Ног выше порога триггера кандидата`
- `Макс. высота ноги`
- `Доля ног выше порога, %`

OHLC metrics always remain strictly based on `[start_bar, end_bar]`.

Do not extend:

- `Баров в цикле`
- `End bar index`
- `Цена начала`
- `Цена конца`
- `High цикла`
- `Low цикла`
- `Размер цикла, %`
- `Макс. движение по циклу, %`
- `Макс. просадка внутри цикла, %`

## 9. Tests

Add tests in `donor TESTER/tests/test_xlsx_cycle_sheet.py`.

Use the existing test style:

- monkeypatch `compute_zigzag_per_bar`;
- set `confirm_event` and `last_confirmed_leg_height_pct` manually;
- do not depend on real ZigZag behavior from synthetic prices.

Cover:

1. `close_position + local_median_stop`: reason is on `off_bar`;
   `End bar index = off_bar - 1`; confirm leg on `off_bar` is counted; OHLC
   metrics are unchanged.
2. `block_new_entries + local_median_stop`: reason is inside the active segment;
   `leg_interval` is not extended.
3. `ttl`, `no_fresh_candidate`, `cycle_trade_limit`, `cycle_take_profit`: raw
   reason is written to `Причина завершения`.
4. `daily_reset` beats wakeup reason.
5. `time_filter_reset` beats wakeup reason when there is no `daily_reset`.
6. Non-D guard: `wakeup_exit_reason` is present but `zigzag_mode != "D"`:
   result remains `FSM_OFF`.
7. Legacy guard: no `wakeup_exit_reason`: old behavior remains.
8. Multiple Exit-C reasons in the lookup window: result remains `FSM_OFF`, with
   no exception.

## 10. Acceptance Criteria

- Exit-C cycles in Mode D show the raw reason instead of `FSM_OFF`.
- Priority is `daily_reset > time_filter_reset > Exit-C > FSM_OFF`.
- Non-D, legacy, and `wf_grid` behavior does not change.
- For `close_position`, the confirm leg on `off_bar` is included in leg metrics.
- OHLC metrics, `End bar index`, `Баров в цикле`, prices, and drawdown are not
  extended to `off_bar`.
- `CYCLE_SHEET_COLUMNS` does not change.
- Existing `test_xlsx_cycle_sheet.py` tests remain green, plus the new cases.

## 11. Main Implementation Risks

- Accidentally extending OHLC metrics to `off_bar`.
- Treating hand-crafted non-D diagnostics with `wakeup_exit_reason` as Mode D.
- Failing or choosing an arbitrary reason when synthetic diagnostics contain
  multiple Exit-C reasons in one cycle window.
- Recomputing leg metrics before `end_reason`, which would make the
  `off_bar` extension awkward or incorrect.
