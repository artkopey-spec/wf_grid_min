from __future__ import annotations

import numpy as np

from supertrend_optimizer.core.zigzag_st_filter import (
    _compute_wakeup_atr_ratio,
    _compute_wakeup_volume_ratio,
)


def test_wakeup_atr_ratio_short_data_returns_all_nan_without_value_error():
    close = np.array([10.0, 11.0, 12.0])

    ratio = _compute_wakeup_atr_ratio(
        close, short_window=2, long_window=5
    )

    assert ratio.dtype == np.float64
    assert np.isnan(ratio).all()


def test_wakeup_atr_ratio_masks_bars_before_long_window():
    close = np.arange(10.0, 18.0, dtype=np.float64)

    ratio = _compute_wakeup_atr_ratio(
        close, short_window=2, long_window=4
    )

    assert np.isnan(ratio[:3]).all()
    np.testing.assert_allclose(
        ratio[3:],
        np.array([
            1.1666666666666667,
            1.1538461538461537,
            1.1272727272727272,
            1.1004366812227073,
            1.0774125132555674,
        ]),
    )


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
