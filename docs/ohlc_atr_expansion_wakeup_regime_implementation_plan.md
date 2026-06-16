# Implementation Plan: OHLC-based ATR Expansion for Mode D Wakeup Regime

## Goal

Implement the new runtime contract from `docs/tz_ohlc_atr_expansion_wakeup_regime.md`:

```text
ZigZag pivot/height calculation remains close-only.
Mode D wakeup ATR expansion uses OHLC runtime data.
```

`trade_filter.wakeup_regime.entry.atr_expansion` must stop using the close-only surrogate:

```python
calculate_true_range(close, close, close)
```

and must calculate the ratio from real OHLC True Range:

```python
tr = calculate_true_range(high, low, close)
short_atr = _atr_rma_or_nan(tr, short_window)
long_atr = _atr_rma_or_nan(tr, long_window)
ratio = short_atr / long_atr
```

No config schema changes are required. No compatibility flag for the old close-only behavior should be added.

## Current Code Map

Core implementation files:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/backtest.py
donor/supertrend_optimizer/engine/run.py
donor/supertrend_optimizer/core/calculator.py
```

Focused test files:

```text
wf_grid/tests/test_wakeup_runtime_ratios.py
wf_grid/tests/test_wakeup_mode_d_entry.py
wf_grid/tests/test_wakeup_volume_plumbing.py
wf_grid/tests/test_wp7_backtest_integration.py
wf_grid/tests/test_wp10_finalization.py
wf_grid/tests/test_wp11_rollback_hardening.py
wf_grid/tests/test_e2e_real_data.py
```

Important current behavior to replace:

```text
zigzag_st_filter._compute_wakeup_atr_ratio(close, short_window, long_window)
  currently calls calculate_true_range(close, close, close)

zigzag_st_filter.apply(...)
  currently has close but no high/low parameters
  currently requires only close when atr_expansion.enabled=true

run_backtest_fast(...)
  receives high/low/close
  currently passes only close into zigzag_st_filter.apply(...)

run_single_backtest(...)
  already receives high/low/close and passes them to run_backtest_fast(...)
```

## Work Packages

### WP0. Baseline and Scope Guard

Run a focused baseline before editing:

```bash
python -m pytest wf_grid/tests/test_wakeup_runtime_ratios.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
python -m pytest wf_grid/tests/test_wp10_finalization.py wf_grid/tests/test_wp11_rollback_hardening.py -q
```

Expected: some existing tests encode the old close-only apply contract and will need intentional updates. Capture any unrelated failures before changing code.

### WP1. Change `_compute_wakeup_atr_ratio` Contract

In `donor/supertrend_optimizer/core/zigzag_st_filter.py`, change the helper signature:

```python
def _compute_wakeup_atr_ratio(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    short_window: int,
    long_window: int,
) -> np.ndarray:
```

Implementation rules:

```text
convert high/low/close via np.asarray(..., dtype=np.float64)
keep ConfigError when short_window < 1 or long_window < 1
return all-NaN ratio when n < long_window
calculate TR via calculate_true_range(high_f, low_f, close_f)
use _atr_rma_or_nan for both ATR windows
divide only where short_atr finite, long_atr finite, and long_atr > 0
force ratio[:long_window - 1] = np.nan
```

Do not call `calculate_atr_rma` directly inside this helper.

### WP2. Add Runtime OHLC Validation for Wakeup ATR

Add small internal helpers in `zigzag_st_filter.py`, near the existing runtime array helpers:

```text
_require_wakeup_ohlc_array(name, values, n) -> np.ndarray
_validate_wakeup_ohlc(high, low, close) -> None
```

Required validation when `atr_expansion.enabled=true`:

```text
high is not None
low is not None
close is not None
each array is 1-D
each array length equals n
all values are finite
high >= low for every bar
```

Failure type: `ConfigError`.

Recommended message stem:

```text
apply() Mode D wakeup atr_expansion requires high, low, and close OHLC arrays
```

The helper can add details such as missing name, wrong ndim, wrong length, non-finite values, or `high < low`, but the error should remain easy to connect to the Mode D ATR contract.

### WP3. Extend `apply()` API Without Changing ZigZag Semantics

In `zigzag_st_filter.apply(...)`, add trailing optional parameters:

```python
high: Optional[np.ndarray] = None
low: Optional[np.ndarray] = None
```

Keep `close` behavior for ZigZag:

```text
per_bar is None:
  close is still required for compute_zigzag_per_bar(...)
  compute_zigzag_per_bar continues to use close only

per_bar is supplied:
  ZigZag per-bar computation remains skipped
  atr_expansion.enabled=true still requires high/low/close runtime arrays
```

Update the docstring from the old broad "apply is close-only" language to:

```text
ZigZag pivot/height calculation is close-only.
Mode D wakeup ATR expansion uses high/low/close runtime arrays.
```

### WP4. Replace Mode D ATR Initialization

In the Mode D initialization block of `apply()`, replace the current close-only path:

```python
close_arr = _require_1d_len("close", close, n)
wakeup_atr_ratio = _compute_wakeup_atr_ratio(close_arr, ...)
```

with:

```python
high_arr = _require_wakeup_ohlc_array("high", high, n)
low_arr = _require_wakeup_ohlc_array("low", low, n)
close_arr = _require_wakeup_ohlc_array("close", close, n)
_validate_wakeup_ohlc(high_arr, low_arr, close_arr)
wakeup_atr_ratio = _compute_wakeup_atr_ratio(
    high_arr,
    low_arr,
    close_arr,
    int(getattr(wakeup_atr_cfg, "short_window")),
    int(getattr(wakeup_atr_cfg, "long_window")),
)
```

Preserve current entry check semantics:

```text
atr_ok = math.isfinite(ratio[t]) and ratio[t] >= min_ratio
```

This preserves warmup fail-closed behavior and execution shift: signal at `t`, position write at `t + 1`.

### WP5. Wire `high/low` Through `run_backtest_fast`

In `donor/supertrend_optimizer/core/backtest.py`, update the `_zz_apply(...)` call:

```python
filter_result = _zz_apply(
    high=high,
    low=low,
    close=close,
    ...
)
```

`run_single_backtest` already passes high/low/close to `run_backtest_fast`, so it should not need a signature change. Verify the path:

```text
run_single_backtest
-> run_backtest_fast
-> zigzag_st_filter.apply
-> _compute_wakeup_atr_ratio
```

The same OHLC arrays must arrive without slicing, reordering, or realignment.

### WP6. Update and Add Tests

#### Unit: ATR Ratio

Update `wf_grid/tests/test_wakeup_runtime_ratios.py`:

```text
_compute_wakeup_atr_ratio now receives high, low, close
expected value is built from calculate_true_range(high, low, close)
warmup still masks ratio[:long_window - 1]
short data still returns all NaN
```

Add explicit cases:

```text
wide candles:
  flat or near-flat close
  expanding high-low range
  ratio reacts to intrabar range

gaps:
  TR includes abs(high[t] - close[t - 1])
  TR includes abs(low[t] - close[t - 1])

zero/invalid long ATR:
  ratio remains NaN where long_atr is non-finite or <= 0
```

#### Direct `apply()` Error Contract

Add or update tests in `wf_grid/tests/test_wakeup_mode_d_entry.py`:

```text
atr_expansion.enabled=true and missing high -> ConfigError
missing low -> ConfigError
missing close -> ConfigError
wrong length high/low/close -> ConfigError
non-1-D array -> ConfigError
NaN or Inf in OHLC -> ConfigError
high < low -> ConfigError
```

Update the current `test_mode_d_atr_component_requires_close_array` into a full OHLC requirement test.

#### Direct `apply()` Success Contract

Update existing tests that enable ATR to pass `high`, `low`, and `close`.

Add one direct `apply()` test where:

```text
per_bar is supplied
close-derived ZigZag fields are fixed by per_bar
atr_expansion.enabled=true
high/low/close are supplied
wakeup_entry_atr_ratio equals OHLC-based expected ratio
entry can open only after warmup and threshold pass
```

#### Plumbing

Add a spy/monkeypatch test around `run_backtest_fast -> zigzag_st_filter.apply`:

```text
apply receives the exact high object/array
apply receives the exact low object/array
apply receives the exact close object/array
```

Candidate location:

```text
wf_grid/tests/test_wakeup_volume_plumbing.py
```

or a new focused file:

```text
wf_grid/tests/test_wakeup_ohlc_atr_plumbing.py
```

#### End-to-End Backtest

Add a focused `run_backtest_fast` test:

```text
Mode D active
atr_expansion.enabled=true
real OHLC passed
backtest completes
filter_diagnostics["wakeup_entry_atr_ratio"] exists
diagnostic length equals positions length
finite ratio exists after warmup when TR is non-zero
```

#### Close-Only ZigZag Invariant

Rewrite anti-drift tests in:

```text
wf_grid/tests/test_wp10_finalization.py
wf_grid/tests/test_wp11_rollback_hardening.py
```

Remove old assertions:

```python
assert "high" not in inspect.signature(apply).parameters
assert "low" not in inspect.signature(apply).parameters
```

Replace with:

```text
apply may accept high/low.
ZigZag pivot/height diagnostics remain close-only.
Mode D wakeup ATR expansion is OHLC-based.
```

Backtest-level invariant must only compare close-derived ZigZag diagnostics, because distorted high/low can legitimately change SuperTrend and Mode D ATR:

```text
candidate_height_pct
local_median_N
other proven close/per_bar-only ZigZag diagnostics
```

Direct `apply()` invariant can be stronger when:

```text
trend is fixed
close/per_bar is fixed
atr_expansion.enabled=false
high/low are varied
```

Then positions, FSM state, and ZigZag diagnostics should remain unchanged.

### WP7. Documentation and Comments

Update stale comments/docstrings that say:

```text
apply() is close-only
```

to the narrower contract:

```text
ZigZag pivot/height calculation is close-only.
Mode D wakeup ATR expansion uses OHLC runtime data.
```

Do not add:

```text
tr_source
smoothing
normalize_by_price
compatibility mode for old close-only behavior
new diagnostic fields
```

## Test Gate

Focused gate after implementation:

```bash
python -m pytest wf_grid/tests/test_wakeup_runtime_ratios.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wakeup_volume_plumbing.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
python -m pytest wf_grid/tests/test_wp10_finalization.py wf_grid/tests/test_wp11_rollback_hardening.py -q
```

Broader gate:

```bash
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py -q
python -m pytest wf_grid/tests/test_wp4_zigzag_per_bar.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp8_wf_oos_integration.py -q
python -m pytest wf_grid/tests/test_e2e_real_data.py --collect-only -q
```

If time permits:

```bash
python -m pytest wf_grid/tests -m "not slow" -q
```

## Acceptance Criteria

1. With `atr_expansion.enabled=true`, ratio is based on `calculate_true_range(high, low, close)`.
2. No silent fallback to `calculate_true_range(close, close, close)` remains.
3. `_compute_wakeup_atr_ratio` uses `_atr_rma_or_nan`.
4. `run_backtest_fast` passes `high` and `low` into `zigzag_st_filter.apply`.
5. Direct `apply()` with enabled ATR and missing/invalid OHLC raises `ConfigError`.
6. Warmup is preserved: first `long_window - 1` ratio values are `NaN`.
7. Entry check stays `math.isfinite(ratio[t]) and ratio[t] >= min_ratio`.
8. Execution shift is unchanged: signal on `t`, position opens on `t + 1`.
9. ZigZag pivot/height calculation remains close-only.
10. Anti-drift tests are rewritten for the new contract, not deleted.
11. End-to-end backtest with Mode D ATR and real OHLC passes.
12. Relevant focused tests are green.

## Main Risks

```text
1. Accidentally letting high/low influence ZigZag per-bar calculations.
2. Leaving a hidden close-only fallback in tests or helper call sites.
3. Breaking direct apply() tests that use per_bar override and atr_expansion enabled.
4. Over-tightening backtest-level invariants even though high/low legitimately affect SuperTrend.
5. Forgetting to update anti-drift tests that currently forbid high/low in apply().
```

Mitigation:

```text
keep high/low usage isolated to wakeup ATR initialization
use explicit OHLC validation only when atr_expansion.enabled=true
preserve per_bar override semantics for ZigZag
compare only close-derived diagnostics in high/low distortion tests
add plumbing tests for high/low pass-through
```
