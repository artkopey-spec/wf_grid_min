# OHLC ATR Expansion Wakeup Regime Implementation Notes

Date: 2026-06-15

## Scope

Implemented runtime contract:

- ZigZag pivot/height calculation remains close-only.
- Mode D wakeup `entry.atr_expansion` uses OHLC True Range:
  `calculate_true_range(high, low, close)`.
- No compatibility flag or close-only fallback was added.

## Baseline And Drift Surfaces Checked

Artifacts inventoried before baseline updates:

- `wf_grid/baseline/fingerprint.py`
- `tests/baseline/baseline_v0.json`
- `tests/baseline/baseline_v0.pre_2026-04-28.json`
- `wf_grid/tests/golden_snapshots/*.json`
- `wf_grid/tests/test_zigzag_apply_characterization.py`

No baseline or golden snapshot files were updated in this pass.

## Mirror Tree Scope

`donor TESTER` does not contain its own
`supertrend_optimizer/core/zigzag_st_filter.py`; its direct `apply()` tests
import the shared implementation from `donor/supertrend_optimizer/...`.
The reviewed tester-side close-only invariance coverage does not enable
`wakeup_regime` or Mode D ATR expansion, so no mirror code change is required
there for this feature.

`donor zigzag` uses a separate historical architecture centered on
`supertrend_optimizer/core/zigzag_filter.py`, not `zigzag_st_filter.py`.
That tree has no Mode D wakeup ATR path, so it is out of scope for the OHLC
wakeup ATR runtime plumbing change.

## Real-data Characterization

Data: first 2000 bars from `data.csv`.

Configuration: focused Mode D wakeup ATR runtime with
`short_window=2`, `long_window=5`, `min_ratio=1.0`.

Current OHLC runtime:

- finite `wakeup_entry_atr_ratio` values: 1996
- `wakeup_entry_atr_ok` bars: 822
- entries opened by this focused config: 0
- position changes: 0
- backtest completed: true

Close-only surrogate comparator, calculated out-of-band for drift review only:

- finite ratio values: 1996
- ATR-ok bars: 794
- finite bars where OHLC ratio differs from close-surrogate ratio: 1996

Conclusion: Mode D ATR gate behavior intentionally drifts. Old `min_ratio`
values tuned against the close-only surrogate should not be treated as
transferable.

## Performance Smoke

Data: first 2000 bars from `data.csv`.

Trials: 20 paired `run_backtest_fast` calls.

- Mode D ATR disabled mean: 0.030986 s
- Mode D ATR enabled mean: 0.034085 s
- relative overhead: 10.0%

The absolute overhead in this smoke is about 3 ms per 2000-bar backtest and is
acceptable for this change. No caching or prevalidation optimization was added.
