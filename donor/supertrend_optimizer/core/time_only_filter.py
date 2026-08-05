"""Thin Time-only adapter over the baseline SuperTrend target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from supertrend_optimizer.core._reset_events import TimeFilterRuntime
from supertrend_optimizer.core.trade_filter_decision import (
    build_filter_execution_metadata,
    decide_trade_filter_entry,
)
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError


@dataclass(frozen=True)
class TimeOnlyFilterResult:
    positions: np.ndarray
    filter_diagnostics: Optional[dict[str, np.ndarray]]
    execution_metadata: dict[str, np.ndarray]
    filter_config_snapshot: dict


def _target_from_trend(trend: np.ndarray, trade_mode: str) -> np.ndarray:
    if trade_mode not in {"revers", "both", "long", "short"}:
        raise ValueError(
            "trade_mode must be one of {'revers', 'both', 'long', 'short'}, "
            f"got: {trade_mode}"
        )
    target = np.asarray(trend, dtype=np.int8).copy()
    if trade_mode == "long":
        target[target != 1] = 0
    elif trade_mode == "short":
        target[target != -1] = 0
    return target


def apply(
    *,
    trend: np.ndarray,
    trade_mode: str,
    time_filter_runtime: TimeFilterRuntime,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    collect_filter_diagnostics: bool = True,
) -> TimeOnlyFilterResult:
    """Build lagged positions with Time as the only trade-filter capability."""
    if execution_model != ExecutionModel.OPEN_TO_OPEN:
        raise ValueError(
            f"ExecutionModel.{execution_model} is not supported. "
            "Only OPEN_TO_OPEN is allowed."
        )

    trend_arr = np.asarray(trend)
    if trend_arr.ndim != 1:
        raise ValueError("trend must be a 1-D array")
    n = len(trend_arr)
    if time_filter_runtime.reference_length != n:
        raise ConfigError(
            "time_filter_runtime reference_length "
            f"{time_filter_runtime.reference_length} != n={n}"
        )

    target = _target_from_trend(trend_arr, trade_mode)
    inside = time_filter_runtime.in_window
    time_reset = time_filter_runtime.reset_event
    daily_reset = np.zeros(n, dtype=bool)
    combined_reset = time_reset

    positions = np.zeros(n, dtype=np.int8)
    allowed_entry = np.zeros(n, dtype=np.int8)
    block_reason = np.full(n, "none", dtype=object)
    forced_exit = np.zeros(n, dtype=np.int8)
    reentry_pending = False

    for t in range(n):
        current = int(positions[t])
        current_target = int(target[t])
        raw_change = current_target != 0 and (
            t == 0 or current_target != int(target[t - 1])
        )
        cycle_open = bool(inside[t]) and (
            t == 0 or not bool(inside[t - 1]) or bool(time_reset[t])
        )
        if current_target == 0:
            reentry_pending = False

        entry_attempt = current_target != 0 and (
            raw_change or cycle_open or reentry_pending
        )
        decision = decide_trade_filter_entry(
            entry_attempt_event=entry_attempt,
            time_filter_in_window=bool(inside[t]),
            combined_reset_event=bool(combined_reset[t]),
            daily_reset_event=False,
            time_filter_reset_event=bool(time_reset[t]),
        )
        block_reason[t] = decision.reason

        if time_reset[t]:
            next_position = 0
            if t + 1 < n and current != 0:
                forced_exit[t] = 1
            if inside[t] and current_target != 0:
                reentry_pending = True
        elif current_target == 0:
            next_position = 0
        elif decision.entry_allowed:
            next_position = current_target
            allowed_entry[t] = 1
            reentry_pending = False
        else:
            next_position = current

        if t + 1 < n:
            positions[t + 1] = np.int8(next_position)

    execution_metadata = build_filter_execution_metadata(
        daily_reset_event=daily_reset,
        time_filter_reset_event=time_reset,
    )
    diagnostics: Optional[dict[str, np.ndarray]]
    if collect_filter_diagnostics:
        diagnostics = {
            "trade_filter_enabled": np.ones(n, dtype=np.int8),
            "trade_filter_mode": np.full(n, "time_only", dtype=object),
            "filter_allowed_entry": allowed_entry,
            "filter_block_reason": block_reason,
            "daily_reset_event": execution_metadata["daily_reset_event"],
            "time_filter_enabled": np.ones(n, dtype=np.int8),
            "time_filter_in_window": np.asarray(inside, dtype=np.int8),
            "time_filter_reset_event": execution_metadata[
                "time_filter_reset_event"
            ],
            "time_filter_forced_exit": forced_exit,
        }
    else:
        diagnostics = None

    return TimeOnlyFilterResult(
        positions=positions,
        filter_diagnostics=diagnostics,
        execution_metadata=execution_metadata,
        filter_config_snapshot={
            "trade_filter_enabled": True,
            "mode": "time_only",
            "time_filter_enabled": True,
            "canonical_windows_minutes": tuple(
                time_filter_runtime.schedule.windows_minutes
            ),
        },
    )


__all__ = ["TimeOnlyFilterResult", "apply"]
