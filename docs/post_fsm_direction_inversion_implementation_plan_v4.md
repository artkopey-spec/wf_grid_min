# Post-FSM Direction Inversion for Mode D Wakeup

**Document:** Implementation Plan v4  
**Scope:** tester-only Mode D / wakeup experiment  
**Status:** ready for implementation  

## 1. Goal

Add a `direction_mode` config option for `trade_filter.wakeup_regime.entry` that can invert the final FSM-produced position stream after the Mode D wakeup FSM has finished.

The feature is intentionally post-FSM:

```text
FSM normal positions -> optional post-FSM sign inversion -> returns/trades/metrics
```

The FSM, wakeup entry logic, exits, stops, cycle lifecycle, ST-flip handling, diagnostics schema, XLSX export, and metrics code must remain unchanged.

## 2. Non-Goals

Do not change:

- `donor/supertrend_optimizer/core/backtest.py`
- `donor/supertrend_optimizer/core/trades.py`
- `donor/supertrend_optimizer/core/metrics.py`
- XLSX export code
- diagnostics schema
- wakeup exits/stops/cycle/ST-flip logic
- WF Grid legality rules

Do not add:

- `wakeup_direction_mode` to per-bar diagnostics
- a new XLSX echo field
- a new metric field
- semantic gating specific to `direction_mode`

## 3. Config Contract

### 3.1 Dataclass

File:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
```

Add to `TradeFilterWakeupEntryConfig`:

```python
direction_mode: object = "normal"
```

Add a short local comment:

```python
# Stored under entry for config structure; applied post-FSM to the full position stream.
```

The type must be `object`, not `str`, so raw non-string YAML values can reach validation and be rejected consistently.

### 3.2 Parser

In `build_trade_filter_config_from_raw`, pass the raw value into `TradeFilterWakeupEntryConfig`:

```python
direction_mode=entry_raw.get("direction_mode", "normal")
```

Missing key means default `"normal"`.

### 3.3 Allowlists

Add `"direction_mode"` to both allowlists:

```python
TRADE_FILTER_ALLOWED_KEYS["trade_filter.wakeup_regime.entry"]
```

and:

```python
wf_grid/config/loader.py::_ALLOWED_KEYS["trade_filter.wakeup_regime.entry"]
```

This preserves the existing drift-guard contract. It does not make `wakeup_regime` legal in WF Grid, because WF Grid rejection is enforced by caller-pipeline validation, not by the allowlist.

Do not change `wf_grid/config/schema.py`; it re-exports the shared dataclasses.

## 4. Validation

### 4.1 Validator Shape

Add a small internal validator in `trade_filter_config.py`, for example:

```python
def _validate_wakeup_direction_mode(value: object, errors: list[str]) -> None:
    ...
```

Rules:

- non-`str` value -> validation error
- string not equal to `"normal"` or `"inverse"` -> validation error
- absent raw key -> no validation error; parser default remains `"normal"`

Use `_append_validation_error(...)` and a machine-readable key:

```text
wakeup_direction_mode_invalid
```

Add the key to the module's canonical validation/failure-key collection if required by the existing pattern.

Suggested messages:

```text
trade_filter.wakeup_regime.entry.direction_mode must be a string, got <type> (<value>)
trade_filter.wakeup_regime.entry.direction_mode must be 'normal' or 'inverse', got <value>
```

`bool`, `int`, and explicit `None` are all invalid because they are not strings.

### 4.2 Placement

In `_validate_wakeup_regime_block`, validate `direction_mode` only when this raw key is present:

```python
direction_mode_key = entry_key + ("direction_mode",)
if direction_mode_key in raw_user_keys:
    _validate_wakeup_direction_mode(
        getattr(entry, "direction_mode", "normal"),
        errors,
    )
```

Place this check after `entry = wakeup.entry` is available, but before any early return caused by invalid `trade_filter.wakeup_regime.enabled`.

This ensures `direction_mode` is independently reported when the user provided it, even if another wakeup-level field is malformed.

### 4.3 Existing Semantic Gates

Keep all existing gates unchanged:

- `wakeup_regime` is allowed only with `trade_filter.zigzag.mode: D`
- `wakeup_regime.enabled=true` requires Mode D
- WF Grid rejects any `wakeup_regime`, including one that contains `direction_mode`

Do not add a new rule like "`direction_mode: inverse` requires Mode D"; the existing `wakeup_regime` legality rules already cover the supported path.

## 5. Runtime Helper

### 5.1 Public Helper

File:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
```

Add a public helper:

```python
def apply_wakeup_direction_mode(
    positions: np.ndarray,
    trade_filter_config: Any,
    *,
    mode_d_enabled: bool,
) -> np.ndarray:
```

Add it to `__all__`.

### 5.2 Helper Contract

Invert only when all conditions are true:

- `mode_d_enabled is True`
- `trade_filter_config.wakeup_regime.enabled is True`
- `trade_filter_config.wakeup_regime.entry.direction_mode == "inverse"`

Behavior:

- inverse path returns a new array equal to `-positions`
- no-op path returns the same object, `result is positions`
- input array is never mutated
- `config=None` does not raise
- missing `wakeup_regime`, missing `entry`, or missing `direction_mode` does not raise
- helper does not resolve ZigZag mode from config; it trusts the `mode_d_enabled` argument

Expected dtype:

- `np.int8` positions over `{-1, 0, 1}` remain `np.int8` after inversion

## 6. Single Runtime Call Site

Insert the helper call in `apply()` after the FSM `for t` loop has fully ended and before:

```python
if not diag_enabled:
```

The call must be at function indentation level, not inside the loop:

```python
filtered_positions = apply_wakeup_direction_mode(
    filtered_positions,
    trade_filter_config,
    mode_d_enabled=mode_d_enabled,
)
```

This one call site covers both result branches:

- `collect_filter_diagnostics=False`
- `collect_filter_diagnostics=True`

## 7. Diagnostics Contract

Diagnostics remain pre-inversion FSM diagnostics.

Runtime outputs become:

- `result.positions`: post-inversion when `direction_mode == "inverse"`
- `result.filter_diagnostics`: pre-inversion FSM trace

This is intentional. Direction-sensitive diagnostic fields such as `wakeup_active_direction` or `wakeup_entry_candidate_leg_direction` must remain equal between normal and inverse runs on the same fixture.

Do not add or rename diagnostic keys.

## 8. Tests

### 8.1 Helper Unit Tests

Add tests in `wf_grid/tests/test_wakeup_mode_d_entry.py` or a focused new file next to it.

Use:

```python
inp = np.array([0, 1, 1, -1, -1, 0], dtype=np.int8)
```

Assertions:

- inverse result is `[0, -1, -1, 1, 1, 0]`
- result dtype is `np.int8`
- zeros remain zeros
- input array is unchanged
- inverse returns a new object: `result is not inp`
- no-op returns the same object: `result is inp`

No-op cases:

- `direction_mode == "normal"`
- `mode_d_enabled=False`
- `wakeup_regime.enabled=False`
- `trade_filter_config=None`
- missing `wakeup_regime`
- missing `entry`
- missing `direction_mode`

### 8.2 Config Tests

Extend:

```text
wf_grid/tests/test_wp2_config_trade_filter.py
```

Assertions:

- wakeup allowlist drift-guard includes `direction_mode`
- absent key gives `tf.wakeup_regime.entry.direction_mode == "normal"`
- `"normal"` is accepted
- `"inverse"` is accepted
- `"invers"` is rejected
- `"x"` is rejected
- `1` is rejected
- `true` is rejected
- explicit `None` is rejected
- rejected cases include error key `wakeup_direction_mode_invalid`
- WF Grid still rejects `wakeup_regime` when nested `direction_mode` is present

WF Grid rejection tests must assert presence of the unsupported-pipeline error or key, not an exact single-error list.

### 8.3 Runtime Parity Tests

Add lightweight `apply()` wiring tests:

- absent `direction_mode` equals explicit `"normal"`
- `collect_filter_diagnostics=True` preserves current positions
- `collect_filter_diagnostics=False` preserves current positions
- diagnostics keyset is unchanged

### 8.4 Integration Mirror Test

Add one API-level test without XLSX export, OOS filtering, or segmentation.

Use an existing Mode D wakeup fixture from `wf_grid/tests/test_wakeup_mode_d_entry.py` where practical.

Run conditions:

- same synthetic data
- same Mode D wakeup config
- one run with `direction_mode == "normal"`
- one run with `direction_mode == "inverse"`
- `commission=0.0`
- `early_exit_enabled=False`

Anti-vacuous guards:

```python
assert np.count_nonzero(positions_normal) >= 1
assert len(trades_normal) >= 1
```

Mirror assertions:

- `np.array_equal(positions_inverse, -positions_normal)`
- `np.abs(positions_inverse).sum() == np.abs(positions_normal).sum()`
- trade count is equal
- `entry_index` matches pairwise
- `exit_index` matches pairwise
- `direction` is inverted pairwise, `LONG <-> SHORT`
- `gross_pnl_pct_inverse == -gross_pnl_pct_normal`, with tolerance for existing rounding
- selected diagnostics arrays are equal normal vs inverse, especially a direction-sensitive field

The mirror assertions are valid only with `early_exit_enabled=False`. Do not later enable early exit in this test.

## 9. Pre-Implementation Downstream Check

Before writing the integration mirror test, read and confirm the current downstream assumptions:

- `extract_trades()` derives trade boundaries from position transitions
- `closed_leg_trade_economics()` computes LONG and SHORT PnL anti-symmetrically when `commission=0.0`
- no side-specific commission asymmetry is involved in this test

No production code changes are expected in `trades.py`.

## 10. Manual Slow Regression

Keep the real-data AC regression outside the normal test suite.

Manual run requirements:

- verify `data.csv` SHA-256 before comparing metrics
- use `config_tester.yaml`
- force `commission=0.0`
- force `early_exit.enabled=false`
- compare metrics only after dataset hash matches the pinned snapshot

This regression is not a replacement for the lightweight automated mirror test.

## 11. Verification Commands

Minimum:

```powershell
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
```

If the integration test is placed in a separate file:

```powershell
python -m pytest wf_grid/tests/test_wakeup_direction_mode_integration.py -q
```

Optional broader FSM safety check:

```powershell
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py -q
```

## 12. Main Invariants

- Default behavior remains bit-identical.
- `direction_mode: normal` remains bit-identical.
- `direction_mode: inverse` only inverts final `positions`.
- Diagnostics remain pre-inversion.
- WF Grid still rejects `wakeup_regime`.
- No-op helper returns the same positions object.
- Inverse helper returns a new positions array.
- Integration mirror test must not pass vacuously on all-zero positions.

## 13. Deferred Refactoring

Do not include these in this implementation:

- remove allowlist duplication between shared config and WF Grid loader
- move `direction_mode` to a semantically cleaner config location

Both are valid tech-debt items, but they are outside this small tester-only experiment.
