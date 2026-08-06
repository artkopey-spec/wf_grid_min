"""Canonical mapping from raw SuperTrend values to allowed positions."""

from __future__ import annotations

import numpy as np


VALID_TRADE_MODES = frozenset({"revers", "both", "long", "short"})


def target_from_trend(trend: np.ndarray, trade_mode: str) -> np.ndarray:
    """Return the canonical int8 target for a one-dimensional trend array."""
    trend_arr = np.asarray(trend)
    if trend_arr.ndim != 1:
        raise ValueError("trend must be a 1-D array")
    if trade_mode not in VALID_TRADE_MODES:
        raise ValueError(
            "trade_mode must be one of {'revers', 'both', 'long', 'short'}, "
            f"got: {trade_mode}"
        )
    target = np.asarray(trend_arr, dtype=np.int8).copy()
    if trade_mode == "long":
        target[target != 1] = 0
    elif trade_mode == "short":
        target[target != -1] = 0
    return target


__all__ = ["VALID_TRADE_MODES", "target_from_trend"]
