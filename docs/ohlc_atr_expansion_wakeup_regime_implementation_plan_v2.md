# Implementation Plan v2: OHLC-based ATR Expansion for Mode D Wakeup Regime

## 1. Goal

Implement the runtime contract:

```text
ZigZag pivot/height calculation remains close-only.
Mode D wakeup ATR expansion uses OHLC runtime data.
```

The component:

```text
trade_filter.wakeup_regime.entry.atr_expansion
```

must calculate its runtime ratio from real OHLC True Range:

```python
tr = calculate_true_range(high, low, close)
short_atr = _atr_rma_or_nan(tr, short_window)
long_atr = _atr_rma_or_nan(tr, long_window)
ratio = short_atr / long_atr
```

The old close-only surrogate:

```python
calculate_true_range(close, close, close)
```

must be removed from the wakeup ATR path. No silent fallback to close-only
behavior is allowed.

No config schema change is required. Do not add compatibility flags, source
selectors, smoothing selectors, price normalization, or new diagnostic fields.

## 2. Current Architecture

Core code paths:

```text
run_single_backtest(...)
-> run_backtest_fast(...)
-> calculate_supertrend(high, low, close, ...)
-> zigzag_st_filter.apply(...)
-> Mode D wakeup entry evaluation
```

Relevant implementation files:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/backtest.py
donor/supertrend_optimizer/engine/run.py
donor/supertrend_optimizer/core/calculator.py
donor/supertrend_optimizer/data/validator.py
```

Current important facts:

```text
zigzag_st_filter._compute_wakeup_atr_ratio(close, short_window, long_window)
  currently computes True Range from close/close/close.

zigzag_st_filter.apply(...)
  currently receives close, volume, and other runtime arrays, but not high/low.

run_backtest_fast(...)
  already receives high, low, close.
  currently passes only close to zigzag_st_filter.apply(...).

run_single_backtest(...)
  already receives high, low, close and passes them to run_backtest_fast(...).

calculate_supertrend(...)
  already uses high, low, close and performs its own ValueError-based validation.
```

This task is a runtime plumbing and calculation change, not a config model change.

Repository scope must be confirmed before implementation because the workspace
contains parallel donor trees:

```text
donor/
donor TESTER/
donor zigzag/
```

The implementation target is `donor/supertrend_optimizer/...`. Before editing,
confirm whether `donor TESTER` and `donor zigzag` are historical fixtures,
separately importable packages, or CI-covered mirrors. If they are importable
or CI-covered mirrors, either mirror the relevant contract change there or
explicitly mark them out-of-scope with the reason. Do not leave package copies
silently divergent.

## 3. Behavioral Contract

### 3.1. ZigZag Remains Close-only

The following must continue to use only `close` or supplied `per_bar` data:

```text
ZigZag candidates
pivots
leg heights
candidate_height_pct
local_median_N
confirmed-leg calculations
other close/per_bar-derived ZigZag diagnostics
```

Adding `high` and `low` to `apply()` does not make ZigZag OHLC-based.

### 3.2. Wakeup ATR Uses OHLC

When all of the following are true:

```text
trade_filter.enabled=true
trade_filter.zigzag.enabled=true
trade_filter.zigzag.mode resolved to D
trade_filter.wakeup_regime.enabled=true
trade_filter.wakeup_regime.entry.atr_expansion.enabled=true
```

then `wakeup_entry_atr_ratio` must be computed from:

```python
calculate_true_range(high, low, close)
```

and not from any close-only approximation.

### 3.3. Warmup and Entry Gate

Warmup behavior remains unchanged:

```text
ratio[:long_window - 1] = NaN
```

The warmup mask boundary is unchanged, but seeded ATR values will change
systematically. In the close-only surrogate, `TR[0]` was usually `0.0`; with
OHLC True Range, `TR[0] = high[0] - low[0]`. This changes the RMA seed and can
change ratio values throughout the series. Treat characterization and baseline
review as mandatory, not optional.

Entry gate remains fail-closed:

```python
atr_ok = math.isfinite(ratio[t]) and ratio[t] >= min_ratio
```

Execution timing remains unchanged:

```text
signal/evaluation happens at bar t
position write/open happens at bar t + 1
```

### 3.4. Error Contract

Direct `zigzag_st_filter.apply()` contract:

```text
If atr_expansion.enabled=true, apply() requires high, low, and close arrays.
Missing or invalid OHLC runtime arrays must raise ConfigError.
```

Production `run_backtest_fast()` contract:

```text
run_backtest_fast() still calls calculate_supertrend(high, low, close, ...)
before zigzag_st_filter.apply(...).
Errors raised by calculate_supertrend() remain ValueError.
Do not rewrite the production SuperTrend/data validation contract as part
of this task.
```

This distinction is intentional. Tests must not require `ConfigError` from
`run_backtest_fast()` for invalid price arrays that are rejected earlier by
SuperTrend or data loading validation.

## 4. Validation Contract

When `atr_expansion.enabled=true`, direct `apply()` must validate the wakeup
OHLC runtime arrays:

```text
high is not None
low is not None
close is not None
each array is 1-D
each array length equals n
all values are finite
high >= low for every bar
```

Failure type:

```text
ConfigError
```

Recommended message stem:

```text
apply() Mode D wakeup atr_expansion requires high, low, and close OHLC arrays
```

Validation differs by layer today and must be treated explicitly:

```text
data validator:
  validates required OHLC columns, finite/positive values, high >= low,
  and stronger candle-shape rules such as high/open/close and low/open/close
  consistency where implemented.

calculate_supertrend:
  validates finite and positive high/low/close values.
  does not own the full data-level OHLC shape contract.

direct zigzag_st_filter.apply:
  currently validates only the runtime arrays it directly needs.
  for wakeup ATR it must validate finite high/low/close and high >= low.
```

Therefore the new `apply()` validation is not merely a duplicate in every
production path. It may be the first `high >= low` guard for callers that bypass
the data loader. Do not introduce a stricter `high >= close >= low` rule in
`apply()` unless the shared data validator and production validation contract
are changed in the same feature. Different OHLC definitions across layers are
not allowed.

This task intentionally tightens the direct `apply()` ATR path for `close`:
when ATR expansion is enabled, `close` must now be finite because it is part of
the OHLC True Range contract. Existing synthetic tests that pass dirty `close`
values with ATR enabled must be updated to either supply valid OHLC data or
expect `ConfigError`.

When `atr_expansion.enabled=false`, missing `high` or `low` is not an error.
If `high` and `low` are passed while ATR expansion is disabled, they must not
affect ZigZag/FSM behavior.

## 5. Non-goals

Do not implement:

```text
tr_source config
smoothing config
normalize_by_price
compatibility mode for close-only wakeup ATR
new diagnostic fields
automatic min_ratio migration
automatic parameter retuning
logging for high == low ratio
changes to calculate_supertrend validation semantics
changes to data loader validation semantics
```

The existing diagnostic field remains:

```text
wakeup_entry_atr_ratio
```

Its semantics change from close-surrogate ATR ratio to OHLC True Range ATR
ratio.

## 6. Work Packages

### WP0. Baseline, Inventory, and Scope Guard

Before editing, run focused baseline tests:

```bash
python -m pytest wf_grid/tests/test_wakeup_runtime_ratios.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wakeup_volume_plumbing.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
python -m pytest wf_grid/tests/test_wp10_finalization.py wf_grid/tests/test_wp11_rollback_hardening.py -q
```

Also inventory stale close-only/signature assumptions:

```bash
rg -n "no high|no low|has_high|has_low|close-only contract|must not accept.*high|must not accept.*low|inspect.signature" wf_grid/tests donor/tests
rg -n "calculate_true_range\\(close|_compute_wakeup_atr_ratio\\(" donor wf_grid
```

Inventory all direct `apply()` call sites, including mirrored donor trees:

```bash
rg -n "zigzag_st_filter\\.apply|from .*zigzag_st_filter import apply|import apply|\\bapply\\(" wf_grid donor "donor TESTER" "donor zigzag" -g "*.py"
```

For each direct call site, classify it as one of:

```text
atr_expansion enabled -> must pass high/low/close
atr_expansion disabled -> no OHLC required
test intentionally verifies missing OHLC -> expects ConfigError
unrelated apply function -> ignore
historical/out-of-scope donor copy -> document reason
```

Inventory baseline and fingerprint surfaces:

```bash
rg -n "baseline|fingerprint|golden|snapshot|wakeup_entry_atr_ratio|atr_expansion" wf_grid tests donor "donor TESTER" "donor zigzag" -g "*.py" -g "*.json" -g "*.yml" -g "*.md"
```

Classify possible baseline updates before changing code. Mode D ATR behavior
will intentionally drift; baseline updates must not be discovered accidentally
at the end.

Classify results into:

```text
tests that must be updated for the new contract
tests that should remain unchanged because they guard pure ZigZag close-only code
stale comments/docstrings only
unrelated references
baseline/fingerprint artifacts that may intentionally drift
mirror-tree files that are in-scope or explicitly out-of-scope
```

Do not start implementation until stale anti-drift tests, direct `apply()` call
sites, baseline surfaces, and mirror-tree scope are identified. This prevents
accidental patching around old contradictions.

### WP1. Refactor Runtime Array Validation

In `donor/supertrend_optimizer/core/zigzag_st_filter.py`, keep validation small
and local. Avoid creating several diverging validators.

Prefer one array helper plus one tiny cross-array check, rather than several
almost-identical validators. Add or adapt helpers near existing runtime array
helpers:

```python
def _require_wakeup_ohlc_array(name: str, values: object, n: int) -> np.ndarray:
    ...

def _validate_wakeup_ohlc(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> None:
    ...
```

Implementation requirements:

```text
convert via np.asarray(..., dtype=np.float64)
reject None
reject non-1-D arrays
reject length mismatch
reject non-finite values
reject high < low
raise ConfigError with Mode D wakeup atr_expansion context
```

It is acceptable to reuse the existing `_require_1d_len()` internally if that
keeps error behavior clear. Do not duplicate similar validation logic in many
places.

The finite check is an intentional direct-`apply()` contract tightening for the
ATR path. Include this in tests and acceptance criteria.

### WP2. Change `_compute_wakeup_atr_ratio` Contract

Change the helper signature:

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
use n = len(close_f)
preserve ConfigError when short_window < 1 or long_window < 1
return all-NaN ratio when n < long_window
calculate TR with calculate_true_range(high_f, low_f, close_f)
use _atr_rma_or_nan for both short and long ATR
divide only where short_atr finite, long_atr finite, and long_atr > 0
force ratio[:long_window - 1] = np.nan
```

Do not call `calculate_atr_rma()` directly in this helper.

Do not keep any branch that calls:

```python
calculate_true_range(close, close, close)
```

for wakeup ATR.

### WP3. Extend `apply()` API

Extend `zigzag_st_filter.apply(...)` with optional OHLC runtime parameters.

Do not reorder existing parameters. Use the smallest signature diff. Append the
new optional keyword-only parameters after the existing runtime parameters:

```python
...
volume_runtime: Optional[VolumeRuntime] = None,
volume: Optional[np.ndarray] = None,
high: Optional[np.ndarray] = None,
low: Optional[np.ndarray] = None,
```

Rationale:

```text
All parameters remain keyword-only, so production callers are not broken.
Appending avoids unnecessary signature churn and noisy review diffs.
```

Update the docstring to say:

```text
ZigZag pivot/height calculation is close-only.
Mode D wakeup ATR expansion uses high/low/close runtime arrays.
```

Keep existing `per_bar` behavior:

```text
per_bar is None:
  close is required for compute_zigzag_per_bar(...)
  compute_zigzag_per_bar remains close-only

per_bar is supplied:
  close is not required for ZigZag construction
  atr_expansion.enabled=true still requires high/low/close runtime arrays
```

### WP4. Replace Mode D ATR Initialization

In the Mode D initialization block of `apply()`, replace the close-only path:

```python
close_arr = _require_1d_len("close", close, n)
wakeup_atr_ratio = _compute_wakeup_atr_ratio(close_arr, ...)
```

with OHLC validation and calculation:

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

Preserve:

```text
current min_ratio gate
warmup fail-closed behavior
signal at t / position write at t + 1
existing wakeup_entry_atr_ratio diagnostic field
```

### WP5. Wire `high` and `low` Through `run_backtest_fast`

In `donor/supertrend_optimizer/core/backtest.py`, update the `_zz_apply(...)`
call:

```python
filter_result = _zz_apply(
    high=high,
    low=low,
    close=close,
    ...
)
```

Do not slice, reorder, copy, or realign the arrays in this change. The same
runtime OHLC objects received by `run_backtest_fast()` should reach `apply()`.

`run_single_backtest()` already passes high/low/close to `run_backtest_fast()`.
No signature change is required there.

### WP6. Update Unit Tests for ATR Ratio

Update `wf_grid/tests/test_wakeup_runtime_ratios.py`.

Required changes:

```text
_compute_wakeup_atr_ratio now receives high, low, close
expected values are built from calculate_true_range(high, low, close)
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

Do not assert that OHLC ratio is always greater than close-only ratio. That is
not mathematically guaranteed for all series.

### WP7. Update Direct `apply()` Tests

Update `wf_grid/tests/test_wakeup_mode_d_entry.py`.

Error contract tests:

```text
atr_expansion.enabled=true and missing high -> ConfigError
missing low -> ConfigError
missing close -> ConfigError
wrong length high/low/close -> ConfigError
non-1-D high/low/close -> ConfigError
NaN or Inf in high/low/close -> ConfigError
high < low -> ConfigError
```

Rewrite the old close-only requirement test into a full OHLC requirement test.

Success contract tests:

```text
existing tests with atr_expansion.enabled=true pass high, low, close
per_bar-supplied path still requires OHLC for ATR expansion
wakeup_entry_atr_ratio equals OHLC-based expected ratio
entry can open only after warmup and threshold pass
```

Add one direct `apply()` test with:

```text
per_bar supplied
trend fixed
close-derived ZigZag fields fixed by per_bar
atr_expansion.enabled=true
high/low/close supplied
OHLC-based ratio finite after warmup
entry opens only when ratio[t] >= min_ratio
```

### WP8. Update Plumbing Tests

Update `wf_grid/tests/test_wakeup_volume_plumbing.py` or add a focused file:

```text
wf_grid/tests/test_wakeup_ohlc_atr_plumbing.py
```

Required spy/monkeypatch test:

```text
run_backtest_fast -> zigzag_st_filter.apply
apply receives the exact high object
apply receives the exact low object
apply receives the exact close object
volume plumbing remains unchanged
```

Replace stale expectations such as:

```python
assert captured["has_high"] is False
assert captured["has_low"] is False
```

with:

```python
assert captured["high"] is high
assert captured["low"] is low
assert captured["close"] is close
```

### WP9. Migrate Anti-drift Tests

Do not delete anti-drift tests. Rewrite them to match the new contract.

Files to update at minimum:

```text
wf_grid/tests/test_wp7_backtest_integration.py
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

Backtest-level invariants:

```text
Do not compare positions, trade_filter_state, st_flip_dir, or FSM fields when
high/low are distorted through run_backtest_fast/run_single_backtest.

Reason: high/low legitimately affect SuperTrend, and SuperTrend affects trend,
FSM transitions, positions, and downstream diagnostics.
```

Backtest-level tests may compare only close/per_bar-derived ZigZag diagnostics,
and only when:

```text
early_exit is disabled
diagnostic lengths match
both runs produced filter_diagnostics
comparison keys are known to be close/per_bar-derived
```

Recommended backtest-level comparison keys:

```text
candidate_height_pct
local_median_N
```

Direct `apply()` invariant can be stronger when:

```text
trend is fixed
close or per_bar is fixed
atr_expansion.enabled=false
high/low are varied
```

Then these should remain unchanged:

```text
positions
trade_filter_state
confirmed_legs_since_start
st_flip_dir
close/per_bar-derived ZigZag diagnostics
```

When `atr_expansion.enabled=true`, changing high/low is allowed to change:

```text
wakeup_entry_atr_ratio
wakeup_entry_atr_ok
entry decision
positions
FSM state after the first changed decision
```

### WP10. End-to-End Backtest Coverage

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

Add a positive two-run backtest-level test for the core feature:

```text
Mode D active
atr_expansion.enabled=true
same close
same config
same data length
run A uses narrow high/low
run B uses wider or otherwise different high/low
wakeup_entry_atr_ratio differs after warmup
candidate_height_pct remains identical
local_median_N remains identical
```

Do not require positions or FSM states to remain identical after the first
changed ATR decision. Once the ATR gate differs, downstream state may
legitimately diverge.

Do not require this test to preserve previous trade counts or positions.
This feature intentionally changes behavior.

### WP11. Baseline Drift and Characterization

This change intentionally alters Mode D ATR gate behavior. Old `min_ratio`
values tuned against close-only surrogate ATR are not guaranteed to transfer.

This work package is mandatory before final acceptance because OHLC True Range
changes both intrabar range handling and RMA seed values.

Before final acceptance, run one representative real-data characterization:

```text
same config
same data
before vs after
capture number of finite wakeup_entry_atr_ratio values
capture number of wakeup_entry_atr_ok bars
capture number of entries/trades
capture whether final backtest completes
```

Also list concrete artifacts checked for drift:

```text
wf_grid/baseline/fingerprint.py
tests/baseline/*.json
wf_grid/tests/golden_snapshots/*.json
any test fixture or snapshot that stores wakeup_entry_atr_ratio, positions,
trade counts, or Mode D diagnostics
```

If golden/fingerprint tests fail only because Mode D ATR behavior changed,
update baselines only after recording this as intentional behavior drift.

Do not auto-retune `min_ratio` in this task.

### WP12. Performance Smoke

The new path adds:

```text
direct apply OHLC validation:
  finite scan for high
  finite scan for low
  finite scan for close
  high >= low scan

ATR calculation:
one True Range calculation
two ATR RMA calculations
one ratio array
```

per `apply()` call when `atr_expansion.enabled=true`.

Add a lightweight performance smoke or manual benchmark note for a representative
grid/WF workload:

```text
atr_expansion disabled runtime
atr_expansion enabled runtime
relative overhead
data length
number of trials or steps
```

The benchmark must include validation cost because production `run_backtest_fast`
already calls `calculate_supertrend()` before `apply()`, and the new direct
`apply()` checks add extra O(n) scans. If the overhead is material, decide in a
separate optimization task whether production callers may pass prevalidated
runtime arrays or whether wakeup ATR ratio should be cached/precomputed.

No optimization is required unless overhead is clearly material. If overhead is
material, defer a separate optimization task for precomputing or caching the
wakeup ATR ratio at the pipeline/runtime layer.

Do not add caching in this feature unless tests show the simple implementation
is too slow.

### WP13. Documentation and Comments

Update stale comments/docstrings that say:

```text
apply() is close-only
apply() must not receive high/low
high/low must not be passed to apply()
```

to the narrower contract:

```text
ZigZag pivot/height calculation is close-only.
Mode D wakeup ATR expansion uses OHLC runtime data.
```

Keep comments precise. Do not add broad architectural prose inside hot code.

## 7. Test Gate

Focused gate:

```bash
python -m pytest wf_grid/tests/test_wakeup_runtime_ratios.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wakeup_volume_plumbing.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
python -m pytest wf_grid/tests/test_wp10_finalization.py wf_grid/tests/test_wp11_rollback_hardening.py -q
```

Broader ZigZag and config gate:

```bash
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py -q
python -m pytest wf_grid/tests/test_wp4_zigzag_per_bar.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp6_st_flip_event_ordering.py -q
python -m pytest wf_grid/tests/test_wp8_wf_oos_integration.py -q
python -m pytest wf_grid/tests/test_e2e_real_data.py --collect-only -q
```

If WP0 determines that `donor TESTER` or `donor zigzag` are in-scope for this
feature or CI-covered, add their affected tests to the focused gate before
implementation is accepted.

If time permits:

```bash
python -m pytest wf_grid/tests -m "not slow" -q
```

Performance smoke:

```text
Run one representative grid/WF timing comparison with atr_expansion disabled
and enabled. Record result in implementation notes or PR summary.
```

## 8. Acceptance Criteria

1. With `atr_expansion.enabled=true`, `wakeup_entry_atr_ratio` is based on
   `calculate_true_range(high, low, close)`.
2. No wakeup ATR code path calls `calculate_true_range(close, close, close)`.
3. `_compute_wakeup_atr_ratio()` accepts `high`, `low`, `close`.
4. `_compute_wakeup_atr_ratio()` uses `_atr_rma_or_nan()`.
5. `run_backtest_fast()` passes `high` and `low` into `zigzag_st_filter.apply()`.
6. Direct `apply()` with enabled ATR and missing OHLC raises `ConfigError`.
7. Direct `apply()` with invalid OHLC shape/length/finite/high-low checks raises
   `ConfigError`.
8. Production SuperTrend/data validation error semantics are not rewritten.
9. Warmup is preserved: first `long_window - 1` ratio values are `NaN`.
10. Entry check remains `math.isfinite(ratio[t]) and ratio[t] >= min_ratio`.
11. Execution shift remains signal at `t`, position write at `t + 1`.
12. ZigZag pivot/height calculation remains close-only.
13. Anti-drift tests are rewritten for the new contract, not removed.
14. Plumbing tests prove exact high/low/close pass-through.
15. A positive two-run test proves high/low changes can change
   `wakeup_entry_atr_ratio` while close-derived ZigZag diagnostics stay stable.
16. End-to-end backtest with Mode D ATR and real OHLC passes.
17. Direct `apply()` call sites are inventoried and migrated or classified.
18. `donor TESTER` and `donor zigzag` scope is explicitly resolved.
19. Intentional finite-`close` tightening on the ATR path is tested or
   documented by an expected `ConfigError`.
20. Intentional baseline drift is characterized.
21. Baseline/fingerprint/golden surfaces are inventoried before updates.
22. Performance overhead, including validation scans, is measured or explicitly
   recorded as acceptable.
23. Focused test gate is green.

## 9. Realistic Scope and Effort

Expected implementation size:

```text
core calculation/API change: small
call-site and baseline inventory: medium
test migration: medium to large
anti-drift rewrite: medium
characterization/performance smoke: medium
```

Realistic effort:

```text
1.0-1.5 developer days for core code and primary focused tests
0.5-1.0 developer day for call-site/mirror-tree/baseline inventory
0.5-1.0 developer day for broader test cleanup, characterization, and baseline review
```

If WP0 inventory is clean, total effort is roughly 2.0-2.5 developer days. If
direct call sites, mirror trees, or baseline snapshots require broader migration,
expect 2.5-3.0 developer days.

Main schedule risk is not the core code. The main schedule risk is stale direct
`apply()` callers, mirror-tree scope, and intentional behavior drift in backtest
outputs.

## 10. Main Risks and Mitigations

### Risk 1. Hidden close-only fallback remains

Mitigation:

```text
rg for calculate_true_range(close, close, close)
unit tests compare against calculate_true_range(high, low, close)
wide-candle and gap tests fail if close-only surrogate is used
```

### Risk 2. Direct `apply()` call sites fail unexpectedly

Mitigation:

```text
WP0 inventories all direct apply call sites
each call site is classified as OHLC required, ATR disabled, expected ConfigError,
or out-of-scope
focused tests include migrated call sites
```

### Risk 3. Mirror donor trees diverge

Mitigation:

```text
WP0 explicitly resolves donor TESTER and donor zigzag scope
in-scope mirrors are updated or tested
out-of-scope mirrors are documented as historical fixtures
```

### Risk 4. High/low leak into ZigZag pivot/height

Mitigation:

```text
keep high/low usage isolated to wakeup ATR initialization
keep compute_zigzag_per_bar signature and implementation close-only
direct apply invariant with fixed trend/per_bar and atr_expansion disabled
positive two-run test checks ratio changes while close-derived ZigZag diagnostics stay stable
```

### Risk 5. Old anti-drift tests are deleted instead of rewritten

Mitigation:

```text
WP9 requires migration, not deletion
new tests assert the narrower close-only ZigZag contract
signature tests now allow high/low but document why
```

### Risk 6. Backtest-level tests compare fields that can legitimately change

Mitigation:

```text
only compare close/per_bar-derived diagnostics at backtest level
compare stronger FSM invariants only in direct apply with fixed trend
disable early_exit in invariance tests
assert diagnostic lengths match before comparing arrays
```

### Risk 7. Error contract confusion

Mitigation:

```text
direct apply invalid runtime OHLC -> ConfigError
production calculate_supertrend/data validation -> existing ValueError behavior
tests are written at the correct layer
```

### Risk 8. Validation contract tightens direct `apply()` behavior

Mitigation:

```text
finite high/low/close is explicitly accepted as intentional for ATR expansion
dirty-close direct apply tests are migrated or changed to expect ConfigError
validation matrix documents differences across data validator, SuperTrend, and apply
```

### Risk 9. Runtime overhead in grid/WF

Mitigation:

```text
measure representative overhead including validation scans
avoid premature caching
create follow-up optimization only if overhead is material
```

### Risk 10. Old min_ratio values are treated as transferable

Mitigation:

```text
document intentional behavior drift
characterize before/after
do not auto-retune in this task
plan a separate grid sweep for OHLC-based min_ratio values
```

### Risk 11. OHLC validity differs across layers

Mitigation:

```text
document current validation matrix
do not silently add high >= close >= low only in apply
confirm production callers either pass through data validation or accept apply's
focused high >= low guard for wakeup ATR
```
