"""Shared reset-event and trend-transition helpers for trade filters."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.utils.exceptions import ConfigError


def _infer_daily_reset_event(
    index: Optional[pd.Index],
    n: int,
    *,
    enabled: bool,
) -> np.ndarray:
    """Return a bool[n] mask where a new calendar day starts."""
    if not enabled:
        return np.zeros(n, dtype=bool)

    if not isinstance(index, pd.DatetimeIndex):
        raise ConfigError(
            "trade_filter.zigzag.daily_reset=true requires DatetimeIndex; "
            f"got {type(index).__name__}"
        )
    if len(index) != n:
        raise ConfigError(f"daily_reset: index length {len(index)} != n={n}")
    if not index.is_monotonic_increasing:
        raise ConfigError(
            "trade_filter.zigzag.daily_reset=true requires "
            "monotonic-increasing DatetimeIndex; got non-monotonic"
        )

    if index.tz is not None:
        normalized = index.tz_localize(None).normalize()
    else:
        normalized = index.normalize()

    days = normalized.astype("int64").to_numpy()
    event = np.zeros(n, dtype=bool)
    if n >= 2:
        event[1:] = days[1:] != days[:-1]
    return event


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
    """Return time-window membership and True-to-False reset-event masks."""
    if not enabled:
        return np.ones(n, dtype=bool), np.zeros(n, dtype=bool)

    if not isinstance(index, pd.DatetimeIndex):
        raise ConfigError(
            "trade_filter.time_filter.enabled=true requires DatetimeIndex; "
            f"got {type(index).__name__}"
        )
    if len(index) != n:
        raise ConfigError(f"time_filter: index length {len(index)} != n={n}")
    if not index.is_monotonic_increasing:
        raise ConfigError(
            "trade_filter.time_filter.enabled=true requires "
            "monotonic-increasing DatetimeIndex; got non-monotonic"
        )

    if index.tz is not None:
        idx_naive = index.tz_localize(None)
    else:
        idx_naive = index

    minutes_of_day = idx_naive.hour * 60 + idx_naive.minute
    start_total = start_h * 60 + start_m
    end_total = end_h * 60 + end_m
    in_window = np.asarray(
        (minutes_of_day >= start_total) & (minutes_of_day < end_total),
        dtype=bool,
    )

    reset_event = np.zeros(n, dtype=bool)
    if n >= 2:
        reset_event[1:] = in_window[:-1] & ~in_window[1:]
    return in_window, reset_event


def detect_st_flip(prev_trend: int, curr_trend: int) -> int:
    """Return +1/-1 for tradable SuperTrend flips, otherwise 0."""
    if prev_trend in (1, -1) and curr_trend in (1, -1) and prev_trend != curr_trend:
        return curr_trend
    return 0


__all__ = [
    "_infer_daily_reset_event",
    "_infer_time_filter_events",
    "detect_st_flip",
]
