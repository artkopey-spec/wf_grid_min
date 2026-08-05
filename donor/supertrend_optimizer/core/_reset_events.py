"""Shared reset-event and trend-transition helpers for trade filters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.core.trade_filter_config import (
    is_time_filter_enabled,
    is_volume_enabled,
    is_zigzag_enabled,
)
from supertrend_optimizer.utils.exceptions import ConfigError


def _readonly_bool(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=bool)
    result.setflags(write=False)
    return result


def _validate_active_index(
    index: Optional[pd.Index],
    n: int,
    *,
    source: str,
) -> pd.DatetimeIndex:
    if not isinstance(index, pd.DatetimeIndex):
        raise ConfigError(
            f"{source}=true requires DatetimeIndex; "
            f"got {type(index).__name__}"
        )
    if len(index) != n:
        raise ConfigError(f"{source}: index length {len(index)} != n={n}")
    if not index.is_monotonic_increasing:
        raise ConfigError(
            f"{source}=true requires monotonic-increasing DatetimeIndex; "
            "got non-monotonic"
        )
    return index


@dataclass(frozen=True)
class TimeWindowSchedule:
    """Canonical sorted collection of half-open minute-of-day windows."""

    windows_minutes: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        previous_end = -1
        for window in self.windows_minutes:
            if (
                not isinstance(window, tuple)
                or len(window) != 2
                or isinstance(window[0], bool)
                or isinstance(window[1], bool)
                or not isinstance(window[0], int)
                or not isinstance(window[1], int)
            ):
                raise ConfigError(
                    "time_filter schedule windows must be (start_minute, end_minute)"
                )
            start, end = window
            if not (0 <= start < end <= 24 * 60):
                raise ConfigError(
                    "time_filter schedule requires 0 <= start < end <= 1440"
                )
            if start <= previous_end:
                raise ConfigError(
                    "time_filter schedule windows must be sorted, non-overlapping, "
                    "and non-touching"
                )
            previous_end = end


@dataclass(frozen=True)
class TimeFilterRuntime:
    """Precomputed time-window membership and calendar-cycle resets."""

    reference_length: int
    schedule: TimeWindowSchedule
    in_window: np.ndarray
    reset_event: np.ndarray

    def __post_init__(self) -> None:
        if self.reference_length < 0:
            raise ConfigError("time_filter runtime reference_length must be >= 0")
        for name, values in (
            ("in_window", self.in_window),
            ("reset_event", self.reset_event),
        ):
            if values.ndim != 1 or len(values) != self.reference_length:
                raise ConfigError(
                    f"time_filter runtime {name} must be 1-D with length "
                    f"{self.reference_length}"
                )
            if values.dtype != bool:
                raise ConfigError(f"time_filter runtime {name} must have bool dtype")
            values.setflags(write=False)
        if self.reference_length and self.reset_event[0]:
            raise ConfigError("time_filter runtime reset_event[0] must be false")

    def slice(self, start: int, end: int) -> "TimeFilterRuntime":
        """Return a slice-local runtime; its first bar can never be a reset."""
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end < start
            or end > self.reference_length
        ):
            raise ConfigError(
                "time_filter runtime slice requires "
                f"0 <= start <= end <= {self.reference_length}"
            )
        in_window = _readonly_bool(self.in_window[start:end])
        reset_event = np.asarray(self.reset_event[start:end], dtype=bool).copy()
        if len(reset_event):
            reset_event[0] = False
        return TimeFilterRuntime(
            reference_length=end - start,
            schedule=self.schedule,
            in_window=in_window,
            reset_event=_readonly_bool(reset_event),
        )


@dataclass(frozen=True)
class ResetEvents:
    daily_reset_enabled: bool
    daily_reset_event: np.ndarray
    time_filter_enabled: bool
    time_filter_in_window: np.ndarray
    time_filter_reset_event: np.ndarray
    combined_reset_event: np.ndarray


def _infer_daily_reset_event(
    index: Optional[pd.Index],
    n: int,
    *,
    enabled: bool,
    source: str = "trade_filter.zigzag.daily_reset",
) -> np.ndarray:
    """Return a bool[n] mask where a new local calendar day starts."""
    if not enabled:
        return np.zeros(n, dtype=bool)

    active_index = _validate_active_index(index, n, source=source)
    normalized = (
        active_index.tz_localize(None).normalize()
        if active_index.tz is not None
        else active_index.normalize()
    )
    days = normalized.astype("int64").to_numpy()
    event = np.zeros(n, dtype=bool)
    if n >= 2:
        event[1:] = days[1:] != days[:-1]
    return event


def build_time_filter_runtime(
    index: Optional[pd.Index],
    n: int,
    *,
    enabled: bool,
    schedule: TimeWindowSchedule,
) -> TimeFilterRuntime:
    """Build membership and reset masks from calendar-cycle identity.

    A cycle is ``(local calendar date, window index)``. A reset is emitted
    when the previous bar belonged to a cycle and the current bar does not
    belong to that same cycle.
    """
    if not enabled:
        return TimeFilterRuntime(
            reference_length=n,
            schedule=schedule,
            in_window=_readonly_bool(np.ones(n, dtype=bool)),
            reset_event=_readonly_bool(np.zeros(n, dtype=bool)),
        )
    if not schedule.windows_minutes:
        raise ConfigError(
            "active time_filter has no resolved window/windows schedule"
        )

    active_index = _validate_active_index(
        index, n, source="trade_filter.time_filter.enabled"
    )
    idx_naive = (
        active_index.tz_localize(None)
        if active_index.tz is not None
        else active_index
    )
    minutes_of_day = np.asarray(
        idx_naive.hour * 60 + idx_naive.minute, dtype=np.int16
    )
    window_id = np.full(n, -1, dtype=np.int16)
    for number, (start, end) in enumerate(schedule.windows_minutes):
        window_id[(minutes_of_day >= start) & (minutes_of_day < end)] = number
    in_window = window_id >= 0

    reset_event = np.zeros(n, dtype=bool)
    if n >= 2:
        normalized = idx_naive.normalize().astype("int64").to_numpy()
        reset_event[1:] = (window_id[:-1] >= 0) & (
            (window_id[1:] != window_id[:-1])
            | (normalized[1:] != normalized[:-1])
        )

    return TimeFilterRuntime(
        reference_length=n,
        schedule=schedule,
        in_window=_readonly_bool(in_window),
        reset_event=_readonly_bool(reset_event),
    )


def build_legacy_time_filter_runtime(
    index: Optional[pd.Index],
    n: int,
    *,
    enabled: bool,
    schedule: TimeWindowSchedule,
) -> TimeFilterRuntime:
    """Migration-only comparator using the former True-to-False reset rule."""
    calendar_runtime = build_time_filter_runtime(
        index, n, enabled=enabled, schedule=schedule
    )
    legacy_reset = np.zeros(n, dtype=bool)
    if n >= 2:
        legacy_reset[1:] = (
            calendar_runtime.in_window[:-1]
            & ~calendar_runtime.in_window[1:]
        )
    return TimeFilterRuntime(
        reference_length=n,
        schedule=schedule,
        in_window=calendar_runtime.in_window,
        reset_event=_readonly_bool(legacy_reset),
    )


def _infer_time_filter_events(
    index: Optional[pd.Index],
    n: int,
    *,
    enabled: bool,
    start_h: int,
    start_m: int,
    end_h: int,
    end_m: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility adapter for one window using calendar-cycle semantics."""
    schedule = TimeWindowSchedule(
        ((start_h * 60 + start_m, end_h * 60 + end_m),)
    )
    runtime = build_time_filter_runtime(
        index, n, enabled=enabled, schedule=schedule
    )
    return runtime.in_window, runtime.reset_event


def _normalise_override(values: Any, *, name: str, n: int) -> np.ndarray:
    result = np.asarray(values, dtype=bool)
    if result.ndim != 1 or len(result) != n:
        raise ConfigError(f"apply() {name} must be 1-D bool of length n={n}")
    return result


def _schedule_from_config(time_filter_config: Any) -> TimeWindowSchedule:
    windows = tuple(getattr(time_filter_config, "_windows_minutes", ()) or ())
    if windows:
        return TimeWindowSchedule(windows)

    legacy = tuple(
        getattr(time_filter_config, field, None)
        for field in (
            "_start_hour",
            "_start_minute",
            "_end_hour",
            "_end_minute",
        )
    )
    if any(value is None for value in legacy):
        raise ConfigError(
            "apply(): time_filter.enabled=True but resolver fields are None; "
            "ensure resolve_time_filter_in_place() is called before apply()"
        )
    start_h, start_m, end_h, end_m = (int(value) for value in legacy)
    return TimeWindowSchedule(
        ((start_h * 60 + start_m, end_h * 60 + end_m),)
    )


def resolve_time_filter_runtime(
    *,
    trade_filter_config: Any,
    index: Optional[pd.Index],
    n: int,
    runtime: Optional[TimeFilterRuntime] = None,
) -> TimeFilterRuntime:
    """Validate a supplied runtime or build one from canonical config."""
    time_filter_config = getattr(trade_filter_config, "time_filter", None)
    enabled = is_time_filter_enabled(trade_filter_config)
    schedule = (
        _schedule_from_config(time_filter_config)
        if enabled
        else TimeWindowSchedule(())
    )
    if runtime is None:
        return build_time_filter_runtime(
            index, n, enabled=enabled, schedule=schedule
        )
    if runtime.reference_length != n:
        raise ConfigError(
            "time_filter_runtime reference_length "
            f"{runtime.reference_length} != n={n}"
        )
    if runtime.schedule != schedule:
        raise ConfigError(
            "time_filter_runtime schedule does not match resolved config"
        )
    return runtime


def resolve_reset_events(
    *,
    trade_filter_config: Any,
    index: Optional[pd.Index],
    n: int,
    owner: str,
    daily_reset_event: Optional[np.ndarray] = None,
    time_filter_events: Optional[tuple[np.ndarray, np.ndarray]] = None,
) -> ResetEvents:
    """Resolve all reset masks through the single production path."""
    if owner not in {"zigzag", "volume"}:
        raise ConfigError(f"unsupported reset-event owner: {owner!r}")

    owner_config = getattr(trade_filter_config, owner, None)
    if hasattr(trade_filter_config, "enabled"):
        owner_enabled = (
            is_zigzag_enabled(trade_filter_config)
            if owner == "zigzag"
            else is_volume_enabled(trade_filter_config)
        )
    else:
        owner_enabled = True
    daily_reset_enabled = (
        owner_enabled
        and owner_config is not None
        and getattr(owner_config, "daily_reset", None) is True
    )
    if owner == "zigzag":
        volume_config = getattr(trade_filter_config, "volume", None)
        daily_reset_enabled = daily_reset_enabled or (
            is_volume_enabled(trade_filter_config)
            and getattr(volume_config, "daily_reset", None) is True
        )

    if daily_reset_event is None:
        daily_event = _infer_daily_reset_event(
            index,
            n,
            enabled=daily_reset_enabled,
            source=f"trade_filter.{owner}.daily_reset",
        )
    else:
        daily_event = _normalise_override(
            daily_reset_event, name="daily_reset_event", n=n
        )

    time_filter_config = getattr(trade_filter_config, "time_filter", None)
    if hasattr(trade_filter_config, "enabled"):
        time_filter_enabled = is_time_filter_enabled(trade_filter_config)
    else:
        time_filter_enabled = (
            time_filter_config is not None
            and getattr(time_filter_config, "enabled", None) is True
        )

    if time_filter_events is None:
        schedule = (
            _schedule_from_config(time_filter_config)
            if time_filter_enabled
            else TimeWindowSchedule(())
        )
        time_runtime = build_time_filter_runtime(
            index, n, enabled=time_filter_enabled, schedule=schedule
        )
        time_in_window = time_runtime.in_window
        time_reset_event = time_runtime.reset_event
    else:
        try:
            raw_in_window, raw_reset_event = time_filter_events
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                "apply() time_filter_events must contain "
                "(in_window, reset_event)"
            ) from exc
        time_in_window = _normalise_override(
            raw_in_window, name="time_filter_events[0]", n=n
        )
        time_reset_event = _normalise_override(
            raw_reset_event, name="time_filter_events[1]", n=n
        )

    combined = np.asarray(daily_event | time_reset_event, dtype=bool)
    return ResetEvents(
        daily_reset_enabled=daily_reset_enabled,
        daily_reset_event=_readonly_bool(daily_event),
        time_filter_enabled=time_filter_enabled,
        time_filter_in_window=_readonly_bool(time_in_window),
        time_filter_reset_event=_readonly_bool(time_reset_event),
        combined_reset_event=_readonly_bool(combined),
    )


def detect_st_flip(prev_trend: int, curr_trend: int) -> int:
    """Return +1/-1 for tradable SuperTrend flips, otherwise 0."""
    if prev_trend in (1, -1) and curr_trend in (1, -1) and prev_trend != curr_trend:
        return curr_trend
    return 0


__all__ = [
    "ResetEvents",
    "TimeFilterRuntime",
    "TimeWindowSchedule",
    "_infer_daily_reset_event",
    "_infer_time_filter_events",
    "build_legacy_time_filter_runtime",
    "build_time_filter_runtime",
    "detect_st_flip",
    "resolve_reset_events",
    "resolve_time_filter_runtime",
]
