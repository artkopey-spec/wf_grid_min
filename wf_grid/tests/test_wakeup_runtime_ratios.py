from __future__ import annotations

import numpy as np

from supertrend_optimizer.core.calculator import calculate_atr_rma, calculate_true_range
from supertrend_optimizer.core.zigzag_st_filter import (
    _compute_wakeup_atr_ratio,
    _compute_wakeup_volume_ratio,
)


def _expected_atr_ratio(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    short_window: int,
    long_window: int,
) -> np.ndarray:
    tr = calculate_true_range(high, low, close)
    out = np.full(len(close), np.nan, dtype=np.float64)
    if len(close) < long_window:
        return out
    short_atr = (
        calculate_atr_rma(tr, short_window)
        if len(close) >= short_window else out.copy()
    )
    long_atr = calculate_atr_rma(tr, long_window)
    valid = np.isfinite(short_atr) & np.isfinite(long_atr) & (long_atr > 0.0)
    np.divide(short_atr, long_atr, out=out, where=valid)
    out[:long_window - 1] = np.nan
    return out


def test_wakeup_atr_ratio_short_data_returns_all_nan_without_value_error():
    close = np.array([10.0, 11.0, 12.0])
    high = close + 1.0
    low = close - 1.0

    ratio = _compute_wakeup_atr_ratio(
        high, low, close, short_window=2, long_window=5
    )

    assert ratio.dtype == np.float64
    assert np.isnan(ratio).all()


def test_wakeup_atr_ratio_masks_bars_before_long_window():
    close = np.arange(10.0, 18.0, dtype=np.float64)
    high = close + np.array([0.5, 0.75, 1.0, 1.25, 1.0, 0.75, 0.5, 1.5])
    low = close - np.array([0.5, 0.25, 0.75, 1.0, 1.25, 0.5, 0.75, 1.0])

    ratio = _compute_wakeup_atr_ratio(
        high, low, close, short_window=2, long_window=4
    )
    expected = _expected_atr_ratio(
        high, low, close, short_window=2, long_window=4
    )

    assert np.isnan(ratio[:3]).all()
    np.testing.assert_allclose(ratio, expected)


def test_wakeup_atr_ratio_reacts_to_wide_candles_with_flat_close():
    close = np.full(8, 100.0, dtype=np.float64)
    high = np.array([101.0, 101.2, 101.5, 102.0, 103.0, 104.0, 105.5, 107.0])
    low = np.array([99.0, 98.8, 98.5, 98.0, 97.0, 96.0, 94.5, 93.0])

    ratio = _compute_wakeup_atr_ratio(
        high, low, close, short_window=2, long_window=4
    )
    expected = _expected_atr_ratio(
        high, low, close, short_window=2, long_window=4
    )

    np.testing.assert_allclose(ratio, expected)
    assert np.isfinite(ratio[3:]).all()
    assert ratio[-1] > ratio[3]


def test_wakeup_atr_ratio_includes_gap_true_range_terms():
    close = np.array([100.0, 110.0, 90.0, 95.0, 120.0], dtype=np.float64)
    high = np.array([101.0, 111.0, 91.0, 96.0, 121.0], dtype=np.float64)
    low = np.array([99.0, 109.0, 89.0, 94.0, 119.0], dtype=np.float64)
    tr = calculate_true_range(high, low, close)

    ratio = _compute_wakeup_atr_ratio(
        high, low, close, short_window=2, long_window=3
    )

    assert tr[2] == abs(low[2] - close[1])
    assert tr[4] == abs(high[4] - close[3])
    np.testing.assert_allclose(
        ratio,
        _expected_atr_ratio(high, low, close, short_window=2, long_window=3),
    )


def test_wakeup_atr_ratio_non_positive_long_atr_stays_nan():
    close = np.full(5, 100.0, dtype=np.float64)
    high = close.copy()
    low = close.copy()

    ratio = _compute_wakeup_atr_ratio(
        high, low, close, short_window=2, long_window=3
    )

    assert np.isnan(ratio).all()


def test_wakeup_volume_ratio_short_data_returns_all_nan():
    ratio = _compute_wakeup_volume_ratio(
        np.array([10.0, 20.0]), short_window=2, baseline_window=3
    )

    assert ratio.dtype == np.float64
    assert np.isnan(ratio).all()


def test_wakeup_volume_ratio_uses_rolling_mean_and_baseline_window_mask():
    volume = np.array([1.0, 1.0, 2.0, 2.0, 4.0], dtype=np.float64)

    ratio = _compute_wakeup_volume_ratio(
        volume, short_window=2, baseline_window=3
    )

    expected = np.array([
        np.nan,
        np.nan,
        1.5 / (4.0 / 3.0),
        2.0 / (5.0 / 3.0),
        3.0 / (8.0 / 3.0),
    ])
    np.testing.assert_allclose(ratio, expected)


def test_wakeup_volume_ratio_non_positive_baseline_stays_nan():
    volume = np.array([0.0, 0.0, 0.0, 2.0], dtype=np.float64)

    ratio = _compute_wakeup_volume_ratio(
        volume, short_window=2, baseline_window=3
    )

    assert np.isnan(ratio[2])
    assert np.isfinite(ratio[3])
