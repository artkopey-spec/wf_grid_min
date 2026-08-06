from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core._reset_events import (
    TimeFilterRuntime,
    TimeWindowSchedule,
    build_time_filter_runtime,
)
from supertrend_optimizer.core.reversal_confirmation_filter import apply
from supertrend_optimizer.core.time_only_filter import apply as apply_time_only
from supertrend_optimizer.utils.exceptions import ConfigError


def _run(index, trend, runtime, *, confirm_bars=1):
    return apply(
        trend=np.asarray(trend, dtype=np.int8),
        trade_mode="revers",
        confirm_bars=confirm_bars,
        index=index,
        time_filter_runtime=runtime,
    )


def test_t01_combined_confirmation_inside_same_window() -> None:
    index = pd.date_range("2026-01-01 09:00", periods=5, freq="min")
    runtime = build_time_filter_runtime(
        index,
        len(index),
        enabled=True,
        schedule=TimeWindowSchedule(((9 * 60, 10 * 60),)),
    )
    result = _run(index, [1, 1, -1, -1, -1], runtime)
    assert result.attempts[0].execution_bar == 4


def test_t03_confirmation_cannot_execute_in_another_window() -> None:
    index = pd.DatetimeIndex(
        [
            "2026-01-01 09:00",
            "2026-01-01 09:01",
            "2026-01-01 09:02",
            "2026-01-01 09:03",
            "2026-01-01 10:00",
        ]
    )
    runtime = build_time_filter_runtime(
        index,
        len(index),
        enabled=True,
        schedule=TimeWindowSchedule(
            ((9 * 60, 9 * 60 + 30), (10 * 60, 10 * 60 + 30))
        ),
    )
    result = _run(index, [1, 1, -1, -1, -1], runtime)
    assert result.attempts[0].confirmation_cancel_reason == (
        "execution_outside_window"
    )


def test_t02_confirmation_inside_but_execution_outside_window() -> None:
    index = pd.date_range("2026-01-01 09:27", periods=5, freq="min")
    runtime = build_time_filter_runtime(
        index,
        len(index),
        enabled=True,
        schedule=TimeWindowSchedule(((9 * 60, 9 * 60 + 31),)),
    )
    result = _run(index, [1, 1, -1, -1, -1], runtime)
    assert result.attempts[0].confirmation_cancel_reason == (
        "execution_outside_window"
    )


def test_t04_confirmation_cannot_execute_in_same_window_next_day() -> None:
    index = pd.DatetimeIndex(
        [
            "2026-01-01 09:00",
            "2026-01-01 09:01",
            "2026-01-01 09:02",
            "2026-01-01 09:03",
            "2026-01-02 09:04",
        ]
    )
    runtime = build_time_filter_runtime(
        index,
        len(index),
        enabled=True,
        schedule=TimeWindowSchedule(((9 * 60, 10 * 60),)),
    )
    result = _run(index, [1, 1, -1, -1, -1], runtime)
    assert result.attempts[0].confirmation_cancel_reason == (
        "execution_outside_window"
    )


def test_t05_reset_cancels_pending_and_forces_one_flat_action() -> None:
    index = pd.date_range("2026-01-01 09:00", periods=6, freq="min")
    runtime = TimeFilterRuntime(
        reference_length=6,
        schedule=TimeWindowSchedule(((9 * 60, 10 * 60),)),
        in_window=np.ones(6, dtype=bool),
        reset_event=np.asarray([0, 0, 0, 1, 0, 0], dtype=bool),
    )
    result = _run(index, [1, 1, -1, -1, -1, -1], runtime, confirm_bars=2)
    assert result.attempts[0].confirmation_cancel_reason == "time_filter_reset"
    np.testing.assert_array_equal(result.positions, [0, 1, 1, 1, 0, -1])


def test_standalone_pending_crosses_midnight() -> None:
    index = pd.DatetimeIndex(
        [
            "2026-01-01 23:58",
            "2026-01-01 23:59",
            "2026-01-02 00:00",
            "2026-01-02 00:01",
            "2026-01-02 00:02",
        ]
    )
    result = apply(
        trend=np.asarray([1, 1, -1, -1, -1], dtype=np.int8),
        trade_mode="revers",
        confirm_bars=1,
        index=index,
    )
    assert result.attempts[0].confirmation_status == "confirmed"


def test_duplicate_timestamps_keep_bar_index_linkage() -> None:
    index = pd.DatetimeIndex(
        [
            "2026-01-01 09:00",
            "2026-01-01 09:01",
            "2026-01-01 09:02",
            "2026-01-01 09:02",
            "2026-01-01 09:03",
        ]
    )
    result = apply(
        trend=np.asarray([1, 1, -1, -1, -1], dtype=np.int8),
        trade_mode="revers",
        confirm_bars=1,
        index=index,
    )
    assert result.attempts[0].source_signal_bar == 2
    assert result.attempts[0].confirmation_bar == 3
    assert result.attempts[0].execution_bar == 4


def test_b05_sparse_index_keeps_bar_index_linkage() -> None:
    index = pd.DatetimeIndex(
        [
            "2026-01-01 09:00",
            "2026-01-01 09:07",
            "2026-01-01 09:23",
            "2026-01-01 09:41",
            "2026-01-01 09:59",
        ]
    )
    result = apply(
        trend=np.asarray([1, 1, -1, -1, -1], dtype=np.int8),
        trade_mode="revers",
        confirm_bars=1,
        index=index,
    )
    assert result.attempts[0].execution_bar == 4
    assert result.attempts[0].execution_time == index[4]


def test_t07_reset_then_zero_target_does_not_reenter() -> None:
    index = pd.date_range("2026-01-01 09:00", periods=5, freq="min")
    runtime = TimeFilterRuntime(
        reference_length=5,
        schedule=TimeWindowSchedule(((9 * 60, 10 * 60),)),
        in_window=np.ones(5, dtype=bool),
        reset_event=np.asarray([0, 0, 1, 0, 0], dtype=bool),
    )
    result = _run(index, [1, 1, 1, 0, 0], runtime)
    np.testing.assert_array_equal(result.positions, [0, 1, 1, 0, 0])


def test_t08_combined_matches_time_only_when_no_reversal_occurs() -> None:
    index = pd.date_range("2026-01-01 09:00", periods=6, freq="min")
    runtime = build_time_filter_runtime(
        index,
        len(index),
        enabled=True,
        schedule=TimeWindowSchedule(((9 * 60, 10 * 60),)),
    )
    trend = np.ones(6, dtype=np.int8)
    combined = _run(index, trend, runtime)
    time_only = apply_time_only(
        trend=trend,
        trade_mode="revers",
        time_filter_runtime=runtime,
    )
    np.testing.assert_array_equal(combined.positions, time_only.positions)


def test_combined_initial_entry_cannot_execute_outside_window() -> None:
    index = pd.DatetimeIndex(
        [
            "2026-01-01 09:29",
            "2026-01-01 09:30",
            "2026-01-01 09:31",
        ]
    )
    runtime = build_time_filter_runtime(
        index,
        len(index),
        enabled=True,
        schedule=TimeWindowSchedule(((9 * 60, 9 * 60 + 30),)),
    )
    result = _run(index, [1, 1, 1], runtime)
    np.testing.assert_array_equal(result.positions, [0, 0, 0])
    assert result.filter_diagnostics["filter_allowed_entry"][0] == 0
    assert (
        result.filter_diagnostics["filter_block_reason"][0]
        == "execution_outside_window"
    )


def test_combined_reentry_cannot_execute_in_another_window() -> None:
    index = pd.DatetimeIndex(
        [
            "2026-01-01 09:00",
            "2026-01-01 09:01",
            "2026-01-01 10:00",
            "2026-01-01 10:29",
            "2026-01-01 10:30",
        ]
    )
    runtime = TimeFilterRuntime(
        reference_length=len(index),
        schedule=TimeWindowSchedule(
            ((9 * 60, 9 * 60 + 30), (10 * 60, 10 * 60 + 30))
        ),
        in_window=np.ones(len(index), dtype=bool),
        reset_event=np.asarray([0, 0, 1, 0, 0], dtype=bool),
    )
    result = _run(index, [1, 1, 1, 1, 1], runtime)
    np.testing.assert_array_equal(result.positions, [0, 1, 1, 0, 0])
    assert result.filter_diagnostics["filter_allowed_entry"][3] == 0
    assert (
        result.filter_diagnostics["filter_block_reason"][3]
        == "execution_outside_window"
    )


def test_i02_timezone_aware_dst_fallback_is_processed_by_bar_index() -> None:
    index = pd.date_range(
        "2026-10-25 00:29",
        periods=5,
        freq="min",
        tz="UTC",
    ).tz_convert("Europe/Berlin")
    result = apply(
        trend=np.asarray([1, 1, -1, -1, -1], dtype=np.int8),
        trade_mode="revers",
        confirm_bars=1,
        index=index,
    )
    assert result.attempts[0].execution_bar == 4


def test_i03_non_monotonic_index_fails_fast() -> None:
    index = pd.DatetimeIndex(
        ["2026-01-01 09:00", "2026-01-01 09:02", "2026-01-01 09:01"]
    )
    with pytest.raises(ConfigError, match="non-monotonic"):
        apply(
            trend=np.asarray([1, 1, -1], dtype=np.int8),
            trade_mode="revers",
            confirm_bars=1,
            index=index,
        )
