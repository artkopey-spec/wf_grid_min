from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
import time

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.volume_metrics import (
    BLOCK_BASELINE_ZERO,
    BLOCK_ABOVE_BASELINE,
    BLOCK_BELOW_BASELINE,
    BLOCK_NONE,
    BLOCK_WARMUP,
    DIR_LONG,
    DIR_SHORT,
    DIR_UNKNOWN,
    REGIME_BASELINE_ZERO,
    REGIME_HIGH,
    REGIME_LOW,
    REGIME_NORMAL,
    REGIME_WARMUP,
    VolumeRuntime,
    _warn_if_volume_baseline_window_large,
    build_volume_global_metrics,
    materialize_volume_block_reason,
    materialize_volume_initial_direction,
    materialize_volume_regime,
)


_PER_BAR_FIELDS = (
    "short_median_volume",
    "baseline_median_volume",
    "median_relative_volume",
    "volume_regime",
    "volume_condition_allowed",
    "volume_condition_block_reason",
    "volume_initial_direction",
)


def _cfg(**overrides):
    data = {
        "enabled": True,
        "mode": "volume_A",
        "short_window": 2,
        "baseline_window": 4,
        "threshold_ratio": 1.1,
        "regime_low_ratio": 0.8,
        "regime_high_ratio": 1.2,
        "direction_lookback_bars": 2,
        "aggregation": "median",
        "baseline_session": SimpleNamespace(enabled=False, window=None),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _runtime(volume, close=None, **cfg_overrides) -> VolumeRuntime:
    if close is None:
        close = np.arange(len(volume), dtype=np.float64)
    return build_volume_global_metrics(volume, close, _cfg(**cfg_overrides))


def test_default_100k_smoke_finishes_under_three_seconds():
    n = 100_000
    volume = np.linspace(1_000, 10_000, n, dtype=np.float64)
    close = np.linspace(100, 200, n, dtype=np.float64)
    cfg = _cfg(short_window=20, baseline_window=500)

    started = time.perf_counter()
    runtime = build_volume_global_metrics(volume, close, cfg)
    elapsed = time.perf_counter() - started

    assert runtime.reference_length == n
    assert elapsed < 3.0


def test_volume_runtime_schema_has_expected_fields_and_no_prohibited_names():
    names = {field.name for field in fields(VolumeRuntime)}

    assert "volume_allowed_per_bar" not in names
    assert "volume_block_reason_per_bar" not in names
    assert not [name for name in names if name.endswith("_code")]
    assert names == set(_PER_BAR_FIELDS) | {
        "absolute_offset",
        "reference_length",
        "filter_config_snapshot",
    }


def test_baseline_zero_regime_and_block_reason():
    rt = _runtime([0, 0, 0, 0, 10, 10], close=[1, 2, 3, 4, 5, 6])

    assert rt.volume_regime[3] == REGIME_BASELINE_ZERO
    assert rt.volume_condition_block_reason[3] == BLOCK_BASELINE_ZERO
    assert rt.volume_condition_allowed[3] == np.False_


def test_all_zero_volume_is_warmup_then_baseline_zero():
    rt = _runtime([0, 0, 0, 0, 0, 0])

    assert np.all(rt.volume_regime[:3] == REGIME_WARMUP)
    assert np.all(rt.volume_regime[3:] == REGIME_BASELINE_ZERO)
    assert np.all(rt.volume_condition_block_reason[3:] == BLOCK_BASELINE_ZERO)


def test_len_shorter_than_baseline_window_is_all_warmup():
    rt = _runtime([10, 20, 30], baseline_window=5)

    assert np.all(rt.volume_regime == REGIME_WARMUP)
    assert np.all(rt.volume_condition_block_reason == BLOCK_WARMUP)
    assert not np.any(rt.volume_condition_allowed)


def test_integer_volume_dtype_builds_float_medians():
    rt = _runtime(np.array([1, 2, 3, 4, 5, 6], dtype=np.int64))

    assert rt.short_median_volume.dtype == np.float64
    assert rt.baseline_median_volume.dtype == np.float64
    assert rt.median_relative_volume.dtype == np.float64


def test_missing_aggregation_field_preserves_median_behavior():
    cfg = SimpleNamespace(
        mode="volume_A",
        short_window=2,
        baseline_window=3,
        threshold_ratio=1.1,
        regime_low_ratio=0.8,
        regime_high_ratio=1.2,
        direction_lookback_bars=2,
    )

    rt = build_volume_global_metrics(
        [1, 10, 100, 1000],
        [1, 2, 3, 4],
        cfg,
    )

    np.testing.assert_allclose(
        rt.short_median_volume,
        [np.nan, 5.5, 55.0, 550.0],
        equal_nan=True,
    )
    np.testing.assert_allclose(
        rt.baseline_median_volume,
        [np.nan, np.nan, 10.0, 100.0],
        equal_nan=True,
    )


def test_volume_aggregation_mean_uses_rolling_mean():
    rt = _runtime(
        [1, 10, 100, 1000],
        close=[1, 2, 3, 4],
        short_window=2,
        baseline_window=3,
        aggregation="mean",
    )

    np.testing.assert_allclose(
        rt.short_median_volume,
        [np.nan, 5.5, 55.0, 550.0],
        equal_nan=True,
    )
    np.testing.assert_allclose(
        rt.baseline_median_volume,
        [np.nan, np.nan, 37.0, 370.0],
        equal_nan=True,
    )
    assert rt.median_relative_volume[2] == pytest.approx(55.0 / 37.0)


def test_volume_aggregation_median_uses_rolling_median():
    rt = _runtime(
        [1, 10, 100, 1000],
        close=[1, 2, 3, 4],
        short_window=2,
        baseline_window=3,
        aggregation="median",
    )

    np.testing.assert_allclose(
        rt.short_median_volume,
        [np.nan, 5.5, 55.0, 550.0],
        equal_nan=True,
    )
    np.testing.assert_allclose(
        rt.baseline_median_volume,
        [np.nan, np.nan, 10.0, 100.0],
        equal_nan=True,
    )


def test_tradingview_sma_volume_regression_without_baseline_session():
    volume = np.arange(1.0, 621.0, dtype=np.float64)
    close = np.linspace(100.0, 120.0, len(volume), dtype=np.float64)
    pos = 599

    rt = build_volume_global_metrics(
        volume,
        close,
        _cfg(
            short_window=30,
            baseline_window=600,
            aggregation="mean",
            baseline_session=SimpleNamespace(enabled=False, window=None),
        ),
    )

    assert rt.short_median_volume[pos] == pytest.approx(585.5, abs=1e-12)
    assert rt.baseline_median_volume[pos] == pytest.approx(300.5, abs=1e-12)
    assert rt.median_relative_volume[pos] == pytest.approx(585.5 / 300.5)


def test_volume_baseline_session_requires_datetime_index():
    baseline_session = SimpleNamespace(
        enabled=True,
        window="09:00-19:00",
        _start_hour=9,
        _start_minute=0,
        _end_hour=19,
        _end_minute=0,
    )

    with pytest.raises(ValueError, match="requires DatetimeIndex"):
        build_volume_global_metrics(
            [1, 2, 3],
            [1, 2, 3],
            _cfg(baseline_window=2, baseline_session=baseline_session),
        )


def test_volume_baseline_session_rolls_over_compressed_active_only_bars():
    baseline_session = SimpleNamespace(
        enabled=True,
        window="09:00-19:00",
        _start_hour=9,
        _start_minute=0,
        _end_hour=19,
        _end_minute=0,
    )
    index = pd.DatetimeIndex([
        "2026-01-01T08:59:00+03:00",
        "2026-01-01T09:00:00+03:00",
        "2026-01-01T09:01:00+03:00",
        "2026-01-01T19:00:00+03:00",
        "2026-01-02T09:00:00+03:00",
    ])

    rt = build_volume_global_metrics(
        [10, 1, 2, 100, 3],
        [1, 2, 3, 4, 5],
        _cfg(
            short_window=1,
            baseline_window=3,
            threshold_ratio=1.0,
            baseline_session=baseline_session,
        ),
        index=index,
    )

    np.testing.assert_allclose(
        rt.baseline_median_volume,
        [np.nan, np.nan, np.nan, np.nan, 2.0],
        equal_nan=True,
    )
    assert rt.volume_condition_block_reason[:4].tolist() == [BLOCK_WARMUP] * 4
    assert rt.volume_condition_block_reason[4] == BLOCK_NONE


def test_volume_baseline_session_uses_mean_on_compressed_active_only_bars():
    baseline_session = SimpleNamespace(
        enabled=True,
        window="09:00-19:00",
        _start_hour=9,
        _start_minute=0,
        _end_hour=19,
        _end_minute=0,
    )
    index = pd.DatetimeIndex([
        "2026-01-01T09:00:00+03:00",
        "2026-01-01T09:01:00+03:00",
        "2026-01-01T19:00:00+03:00",
        "2026-01-02T09:00:00+03:00",
    ])

    rt = build_volume_global_metrics(
        [1, 10, 1000, 100],
        [1, 2, 3, 4],
        _cfg(
            short_window=1,
            baseline_window=3,
            aggregation="mean",
            baseline_session=baseline_session,
        ),
        index=index,
    )

    np.testing.assert_allclose(
        rt.baseline_median_volume,
        [np.nan, np.nan, np.nan, 37.0],
        equal_nan=True,
    )
    assert rt.filter_config_snapshot["volume_aggregation"] == "mean"


def test_very_large_finite_values_do_not_overflow_relative_volume():
    rt = _runtime(np.full(8, 1e300, dtype=np.float64))

    assert np.isfinite(rt.median_relative_volume[3:]).all()
    assert np.allclose(rt.median_relative_volume[3:], 1.0)
    assert np.all(rt.volume_regime[3:] == REGIME_NORMAL)


def test_volume_a_and_b_allowed_and_block_reason_directions():
    volume = [10, 10, 10, 10, 40, 40, 40, 40]
    close = [10, 11, 12, 13, 14, 13, 12, 11]
    a = _runtime(volume, close=close, threshold_ratio=1.5, regime_high_ratio=1.5)
    b = _runtime(
        volume,
        close=close,
        mode="volume_B",
        threshold_ratio=1.5,
        regime_high_ratio=1.5,
    )

    assert a.volume_condition_allowed[4] == np.True_
    assert b.volume_condition_allowed[4] == np.False_
    assert b.volume_condition_block_reason[4] == BLOCK_ABOVE_BASELINE
    assert a.volume_initial_direction[2] == DIR_LONG
    assert a.volume_initial_direction[-1] == DIR_SHORT


def test_low_volume_maps_to_low_regime_and_below_baseline_reason():
    rt = _runtime([100, 100, 100, 100, 10, 10], threshold_ratio=0.8)

    assert rt.volume_regime[-1] == REGIME_LOW
    assert rt.volume_condition_allowed[-1] == np.False_
    assert rt.volume_condition_block_reason[-1] == BLOCK_BELOW_BASELINE


def test_equal_close_momentum_and_ties_are_unknown_direction():
    rt = _runtime([10, 20, 30, 40, 50], close=[100, 100, 100, 100, 100])

    assert np.all(rt.volume_initial_direction == DIR_UNKNOWN)


def test_materializers_return_expected_strings():
    assert materialize_volume_regime(
        np.array([
            REGIME_WARMUP,
            REGIME_BASELINE_ZERO,
            REGIME_LOW,
            REGIME_NORMAL,
            REGIME_HIGH,
        ])
    ).tolist() == [
        "volume_warmup",
        "volume_baseline_zero",
        "low_volume",
        "normal_volume",
        "high_volume",
    ]
    assert materialize_volume_block_reason(
        np.array([
            BLOCK_NONE,
            BLOCK_WARMUP,
            BLOCK_BASELINE_ZERO,
            BLOCK_BELOW_BASELINE,
            BLOCK_ABOVE_BASELINE,
        ])
    ).tolist() == [
        "none",
        "volume_warmup",
        "volume_baseline_zero",
        "volume_below_baseline",
        "volume_above_baseline",
    ]
    assert materialize_volume_initial_direction(
        np.array([DIR_SHORT, DIR_UNKNOWN, DIR_LONG])
    ).tolist() == ["short", "unknown", "long"]


def test_length_mismatch_raises_value_error():
    with pytest.raises(ValueError, match="length mismatch"):
        build_volume_global_metrics([1, 2, 3], [1, 2], _cfg())


def test_per_bar_arrays_are_read_only():
    rt = _runtime([10, 20, 30, 40, 50])

    for name in _PER_BAR_FIELDS:
        assert getattr(rt, name).flags.writeable is False
    with pytest.raises(ValueError):
        rt.volume_regime[0] = REGIME_HIGH


def test_slice_uses_views_offsets_and_snapshot_identity():
    rt = _runtime([10, 20, 30, 40, 50, 60])
    sliced = rt.slice(1, 5)

    assert sliced.absolute_offset == 1
    assert sliced.reference_length == 4
    assert sliced.filter_config_snapshot is rt.filter_config_snapshot
    for name in _PER_BAR_FIELDS:
        assert np.shares_memory(getattr(rt, name), getattr(sliced, name))
        assert getattr(sliced, name).flags.writeable is False


def test_chained_slices_share_memory_with_original_parent():
    rt = _runtime([10, 20, 30, 40, 50, 60, 70])
    chained = rt.slice(1, 6).slice(2, 4)

    assert chained.absolute_offset == 3
    assert chained.reference_length == 2
    for name in _PER_BAR_FIELDS:
        assert np.shares_memory(getattr(rt, name), getattr(chained, name))


def test_empty_slice_is_valid_and_preserves_snapshot_identity():
    rt = _runtime([10, 20, 30, 40])
    empty = rt.slice(2, 2)

    assert empty.reference_length == 0
    assert empty.absolute_offset == 2
    assert empty.filter_config_snapshot is rt.filter_config_snapshot
    for name in _PER_BAR_FIELDS:
        assert len(getattr(empty, name)) == 0
        assert getattr(empty, name).flags.writeable is False


@pytest.mark.parametrize("start,end", [(-1, 1), (2, 1), (0, 5)])
def test_slice_bounds_violation_raises_value_error(start, end):
    rt = _runtime([10, 20, 30, 40])

    with pytest.raises(ValueError, match="slice bounds"):
        rt.slice(start, end)


def test_filter_config_snapshot_has_exact_keys_and_values():
    cfg = _cfg(
        mode="volume_B",
        short_window=3,
        baseline_window=5,
        threshold_ratio=0.9,
        regime_low_ratio=0.7,
        regime_high_ratio=1.3,
        direction_lookback_bars=4,
    )

    rt = build_volume_global_metrics(
        np.arange(1, 8, dtype=np.float64),
        np.arange(10, 17, dtype=np.float64),
        cfg,
    )

    assert rt.filter_config_snapshot == {
        "volume_filter_enabled": True,
        "volume_filter_mode": "volume_B",
        "volume_aggregation": "median",
        "volume_short_window": 3,
        "volume_baseline_window": 5,
        "volume_baseline_session_enabled": False,
        "volume_baseline_session_window": None,
        "volume_threshold_ratio": 0.9,
        "volume_regime_low_ratio": 0.7,
        "volume_regime_high_ratio": 1.3,
        "volume_direction_lookback_bars": 4,
    }


def test_baseline_window_warning_emits_once():
    cfg = _cfg(baseline_window=60)

    with pytest.warns(RuntimeWarning, match="baseline_window") as record:
        _warn_if_volume_baseline_window_large(cfg, 100)

    assert len(record) == 1
