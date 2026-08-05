from __future__ import annotations

import pickle
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core._reset_events import (
    TimeFilterRuntime,
    TimeWindowSchedule,
    build_legacy_time_filter_runtime,
    build_time_filter_runtime,
    resolve_reset_events,
)
from supertrend_optimizer.utils.exceptions import ConfigError


SCHEDULE = TimeWindowSchedule(((9 * 60, 12 * 60), (16 * 60, 18 * 60)))


@pytest.mark.parametrize(
    ("timestamps", "expected_in_window", "expected_reset"),
    [
        (
            ["2026-03-06 08:59", "2026-03-06 09:00"],
            [False, True],
            [False, False],
        ),
        (
            ["2026-03-06 11:59", "2026-03-06 12:00"],
            [True, False],
            [False, True],
        ),
        (
            ["2026-03-06 11:59", "2026-03-06 16:00"],
            [True, True],
            [False, True],
        ),
        (
            ["2026-03-06 17:59", "2026-03-07 16:00"],
            [True, True],
            [False, True],
        ),
        (
            ["2026-03-06 12:00", "2026-03-06 16:00"],
            [False, True],
            [False, False],
        ),
        (
            ["2026-03-06 09:00", "2026-03-06 09:00"],
            [True, True],
            [False, False],
        ),
    ],
)
def test_calendar_cycle_oracle(
    timestamps: list[str],
    expected_in_window: list[bool],
    expected_reset: list[bool],
) -> None:
    index = pd.DatetimeIndex(timestamps)
    runtime = build_time_filter_runtime(
        index, len(index), enabled=True, schedule=SCHEDULE
    )
    np.testing.assert_array_equal(runtime.in_window, expected_in_window)
    np.testing.assert_array_equal(runtime.reset_event, expected_reset)


def test_calendar_cycle_uses_local_wall_clock_and_ignores_seconds() -> None:
    index = pd.DatetimeIndex(
        ["2026-03-06 09:00:59", "2026-03-06 12:00:01"],
        tz="Europe/Moscow",
    )
    runtime = build_time_filter_runtime(
        index, len(index), enabled=True, schedule=SCHEDULE
    )
    np.testing.assert_array_equal(runtime.in_window, [True, False])
    np.testing.assert_array_equal(runtime.reset_event, [False, True])


def test_disabled_runtime_short_circuits_index_validation() -> None:
    runtime = build_time_filter_runtime(
        pd.RangeIndex(3), 3, enabled=False, schedule=TimeWindowSchedule(())
    )
    np.testing.assert_array_equal(runtime.in_window, [True, True, True])
    np.testing.assert_array_equal(runtime.reset_event, [False, False, False])


def test_active_runtime_validates_index_contract() -> None:
    with pytest.raises(ConfigError, match="DatetimeIndex"):
        build_time_filter_runtime(
            pd.RangeIndex(2), 2, enabled=True, schedule=SCHEDULE
        )
    with pytest.raises(ConfigError, match="monotonic"):
        build_time_filter_runtime(
            pd.DatetimeIndex(["2026-03-06 10:00", "2026-03-06 09:00"]),
            2,
            enabled=True,
            schedule=SCHEDULE,
        )


def test_runtime_slice_clears_only_boundary_reset() -> None:
    index = pd.DatetimeIndex(
        [
            "2026-03-06 11:59",
            "2026-03-06 16:00",
            "2026-03-06 17:59",
            "2026-03-07 16:00",
        ]
    )
    full = build_time_filter_runtime(
        index, len(index), enabled=True, schedule=SCHEDULE
    )
    sliced = full.slice(1, 4)
    assert isinstance(sliced, TimeFilterRuntime)
    np.testing.assert_array_equal(sliced.in_window, full.in_window[1:4])
    np.testing.assert_array_equal(sliced.reset_event, [False, False, True])
    assert not sliced.in_window.flags.writeable
    assert not sliced.reset_event.flags.writeable


def test_runtime_pickle_round_trip_and_compact_payload() -> None:
    index = pd.date_range("2026-03-06 09:00", periods=10_000, freq="min")
    runtime = build_time_filter_runtime(
        index, len(index), enabled=True, schedule=SCHEDULE
    )
    payload = pickle.dumps(runtime)
    restored = pickle.loads(payload)
    np.testing.assert_array_equal(restored.in_window, runtime.in_window)
    np.testing.assert_array_equal(restored.reset_event, runtime.reset_event)
    assert not restored.reset_event[0]
    assert len(payload) <= 3 * len(index) + 65_536


def test_dual_run_dense_parity_and_accepted_gap_delta() -> None:
    dense = pd.date_range("2026-03-06 08:59", "2026-03-06 18:00", freq="min")
    calendar = build_time_filter_runtime(
        dense, len(dense), enabled=True, schedule=SCHEDULE
    )
    legacy = build_legacy_time_filter_runtime(
        dense, len(dense), enabled=True, schedule=SCHEDULE
    )
    np.testing.assert_array_equal(calendar.in_window, legacy.in_window)
    np.testing.assert_array_equal(calendar.reset_event, legacy.reset_event)

    gap = pd.DatetimeIndex(["2026-03-06 11:59", "2026-03-06 16:00"])
    calendar_gap = build_time_filter_runtime(
        gap, len(gap), enabled=True, schedule=SCHEDULE
    )
    legacy_gap = build_legacy_time_filter_runtime(
        gap, len(gap), enabled=True, schedule=SCHEDULE
    )
    np.testing.assert_array_equal(calendar_gap.reset_event, [False, True])
    np.testing.assert_array_equal(legacy_gap.reset_event, [False, False])


@dataclass
class _Component:
    enabled: bool
    daily_reset: bool


@dataclass
class _Time:
    enabled: bool = False
    _windows_minutes: tuple[tuple[int, int], ...] = ()


@dataclass
class _TradeFilter:
    enabled: bool = True
    zigzag: _Component = field(
        default_factory=lambda: _Component(enabled=False, daily_reset=True)
    )
    volume: _Component = field(
        default_factory=lambda: _Component(enabled=True, daily_reset=False)
    )
    time_filter: _Time = field(default_factory=_Time)


def test_volume_owner_does_not_activate_inactive_zigzag_daily_reset() -> None:
    events = resolve_reset_events(
        trade_filter_config=_TradeFilter(),
        index=pd.RangeIndex(2),
        n=2,
        owner="volume",
    )
    assert events.daily_reset_enabled is False
    assert not events.daily_reset_event.any()


def test_shared_override_validation_and_combined_mask() -> None:
    events = resolve_reset_events(
        trade_filter_config=_TradeFilter(),
        index=None,
        n=3,
        owner="volume",
        daily_reset_event=[0, 1, 0],
        time_filter_events=([1, 1, 0], [0, 0, 1]),
    )
    np.testing.assert_array_equal(events.combined_reset_event, [False, True, True])
    with pytest.raises(ConfigError, match=r"time_filter_events\[0\]"):
        resolve_reset_events(
            trade_filter_config=_TradeFilter(),
            index=None,
            n=3,
            owner="volume",
            time_filter_events=([True], [False, False, False]),
        )
