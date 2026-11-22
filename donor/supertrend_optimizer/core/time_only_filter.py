"""Thin Time-only adapter over the baseline SuperTrend target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from supertrend_optimizer.core._reset_events import TimeFilterRuntime
from supertrend_optimizer.core.trade_filter_decision import (
    build_filter_execution_metadata,
)
from supertrend_optimizer.core.position_target import target_from_trend
from supertrend_optimizer.core.time_entry_transition import (
    TimeEntryInputs,
    TimeEntryState,
    decide_time_entry,
)
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError


@dataclass(frozen=True)
class TimeOnlyFilterResult:
    positions: np.ndarray
    filter_diagnostics: Optional[dict[str, np.ndarray]]
    execution_metadata: dict[str, np.ndarray]
    filter_config_snapshot: dict


def apply(
    *,
    trend: np.ndarray,
    trade_mode: str,
    time_filter_runtime: TimeFilterRuntime,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    collect_filter_diagnostics: bool = True,
    date_execution=None,
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

    target = target_from_trend(trend_arr, trade_mode)
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
        if date_execution is not None and date_execution.boundary[t]:
            current = 0
        current_target = int(target[t])
        decision = decide_time_entry(
            TimeEntryState(reentry_pending=reentry_pending),
            TimeEntryInputs(
                t=t,
                current_position=current,
                current_target=current_target,
                previous_target=int(target[t - 1]) if t else 0,
                in_window=bool(inside[t]),
                previous_in_window=bool(inside[t - 1]) if t else False,
                time_reset=bool(combined_reset[t]),
            ),
        )
        block_reason[t] = decision.reason

        if decision.forced_flat:
            next_position = 0
            if t + 1 < n and decision.forced_exit:
                forced_exit[t] = 1
        elif current_target == 0:
            next_position = 0
        elif decision.entry_allowed:
            next_position = current_target
            allowed_entry[t] = 1
        else:
            next_position = current
        reentry_pending = decision.next_reentry_pending

        if t + 1 < n:
            if date_execution is not None:
                next_position = date_execution.at_open(t + 1, current, next_position)
                if not date_execution.entry[t + 1]:
                    allowed_entry[t] = 0
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
