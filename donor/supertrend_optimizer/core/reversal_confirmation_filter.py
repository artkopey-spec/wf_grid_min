"""Open-to-open SuperTrend reversal confirmation runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.core._reset_events import TimeFilterRuntime
from supertrend_optimizer.core.position_target import target_from_trend
from supertrend_optimizer.core.time_entry_transition import (
    TimeEntryInputs,
    TimeEntryState,
    decide_time_entry,
)
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError


TRANSITION_NONE = 0
TRANSITION_FLAT_RAW_ENTRY = 1
TRANSITION_TIME_WINDOW_REENTRY = 2
TRANSITION_TIME_FORCED_CLOSE = 3
TRANSITION_CONFIRMED_CHANGE = 4


class ReversalConfirmationState(str, Enum):
    ARMED = "ARMED"
    PENDING = "PENDING"
    WAIT_SOURCE_RETURN = "WAIT_SOURCE_RETURN"


@dataclass(frozen=True)
class ReversalConfirmationAttempt:
    event_id: int
    source_signal_bar: int
    source_signal_time: pd.Timestamp
    source_position: int
    source_raw_trend: int
    confirmation_bar: Optional[int]
    confirmation_time: Optional[pd.Timestamp]
    execution_bar: Optional[int]
    execution_time: Optional[pd.Timestamp]
    confirmation_status: str
    confirmation_cancel_reason: Optional[str]


@dataclass(frozen=True)
class ReversalConfirmationSummary:
    started: int
    confirmed: int
    cancelled: int
    executed: int
    cancelled_by_time_reset: int
    cancelled_by_boundary: int
    active_pending: int = 0

    def validate(self) -> None:
        if self.started != self.confirmed + self.cancelled:
            raise ValueError("reversal summary invariant: started != confirmed + cancelled")
        if self.confirmed != self.executed:
            raise ValueError("reversal summary invariant: confirmed != executed")
        if self.active_pending != 0:
            raise ValueError("reversal summary invariant: active_pending != 0")


@dataclass(frozen=True)
class ReversalConfirmationResult:
    positions: np.ndarray
    filter_diagnostics: Optional[dict[str, np.ndarray]]
    execution_metadata: dict[str, np.ndarray]
    attempts: tuple[ReversalConfirmationAttempt, ...]
    summary: ReversalConfirmationSummary
    filter_config_snapshot: dict


def _validate_inputs(
    trend: np.ndarray,
    index: pd.Index,
    confirm_bars: int,
    execution_model: ExecutionModel,
    time_filter_runtime: Optional[TimeFilterRuntime],
) -> tuple[np.ndarray, pd.DatetimeIndex]:
    if execution_model != ExecutionModel.OPEN_TO_OPEN:
        raise ValueError(
            f"ExecutionModel.{execution_model} is not supported. "
            "Only OPEN_TO_OPEN is allowed."
        )
    trend_arr = np.asarray(trend)
    if trend_arr.ndim != 1:
        raise ValueError("trend must be a 1-D array")
    if not isinstance(index, pd.DatetimeIndex):
        raise ConfigError(
            "reversal_confirmation.enabled=true requires DatetimeIndex; "
            f"got {type(index).__name__}"
        )
    if len(index) != len(trend_arr):
        raise ConfigError(
            f"reversal_confirmation: index length {len(index)} != n={len(trend_arr)}"
        )
    if not index.is_monotonic_increasing:
        raise ConfigError(
            "reversal_confirmation.enabled=true requires "
            "monotonic-increasing DatetimeIndex; got non-monotonic"
        )
    if (
        isinstance(confirm_bars, bool)
        or not isinstance(confirm_bars, int)
        or not 1 <= confirm_bars <= 1_000_000
    ):
        raise ConfigError(
            "reversal_confirmation confirm_bars must be an integer in [1, 1000000]"
        )
    if (
        time_filter_runtime is not None
        and time_filter_runtime.reference_length != len(trend_arr)
    ):
        raise ConfigError(
            "time_filter_runtime reference_length "
            f"{time_filter_runtime.reference_length} != n={len(trend_arr)}"
        )
    return np.asarray(trend_arr, dtype=np.int8), index


def _time_identity(
    index: pd.DatetimeIndex,
    runtime: TimeFilterRuntime,
) -> list[Optional[tuple[int, int]]]:
    local = index.tz_localize(None) if index.tz is not None else index
    minute = np.asarray(local.hour * 60 + local.minute)
    window_id = np.full(len(index), -1, dtype=np.int16)
    for number, (start, end) in enumerate(runtime.schedule.windows_minutes):
        window_id[(minute >= start) & (minute < end)] = number
    days = local.normalize().astype("int64").to_numpy()
    return [
        (int(days[t]), int(window_id[t])) if window_id[t] >= 0 else None
        for t in range(len(index))
    ]


def apply(
    *,
    trend: np.ndarray,
    trade_mode: str,
    confirm_bars: int,
    index: pd.Index,
    time_filter_runtime: Optional[TimeFilterRuntime] = None,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    collect_filter_diagnostics: bool = True,
    date_execution=None,
) -> ReversalConfirmationResult:
    """Apply standalone or time-combined confirmation in one forward O(n) loop."""
    trend_arr, dt_index = _validate_inputs(
        trend,
        index,
        confirm_bars,
        execution_model,
        time_filter_runtime,
    )
    n = len(trend_arr)
    target = target_from_trend(trend_arr, trade_mode)
    combined = time_filter_runtime is not None
    inside = (
        time_filter_runtime.in_window
        if combined
        else np.ones(n, dtype=bool)
    )
    time_reset = (
        time_filter_runtime.reset_event
        if combined
        else np.zeros(n, dtype=bool)
    )
    identities = (
        _time_identity(dt_index, time_filter_runtime)
        if combined
        else None
    )

    positions = np.zeros(n, dtype=np.int8)
    metadata = {
        "daily_reset_event": np.zeros(n, dtype=np.int8),
        "time_filter_reset_event": np.asarray(time_reset, dtype=np.int8).copy(),
        "reversal_confirmation_execution_event_id": np.full(n, -1, dtype=np.int64),
        "reversal_confirmation_execution_origin_bar": np.full(n, -1, dtype=np.int64),
        "reversal_confirmation_execution_decision_bar": np.full(n, -1, dtype=np.int64),
        "reversal_confirmation_execution_transition_kind": np.zeros(n, dtype=np.int8),
        "reversal_confirmation_confirmed_close": np.zeros(n, dtype=np.int8),
        "reversal_confirmation_confirmed_open": np.zeros(n, dtype=np.int8),
    }
    allowed_entry = np.zeros(n, dtype=np.int8)
    block_reason = np.full(n, "none", dtype=object)
    forced_exit = np.zeros(n, dtype=np.int8)
    state_trace = np.full(n, ReversalConfirmationState.ARMED.value, dtype=object)

    attempts: list[ReversalConfirmationAttempt] = []
    state = ReversalConfirmationState.ARMED
    pending: Optional[dict[str, object]] = None
    next_event_id = 0
    time_state = TimeEntryState()

    def finish_cancel(reason: str) -> None:
        nonlocal pending, state
        if pending is None:
            raise RuntimeError(
                "reversal confirmation internal invariant: "
                "cancel requested without pending attempt"
            )
        attempts.append(
            ReversalConfirmationAttempt(
                event_id=int(pending["event_id"]),
                source_signal_bar=int(pending["source_signal_bar"]),
                source_signal_time=pending["source_signal_time"],
                source_position=int(pending["source_position"]),
                source_raw_trend=int(pending["source_raw_trend"]),
                confirmation_bar=None,
                confirmation_time=None,
                execution_bar=None,
                execution_time=None,
                confirmation_status="cancelled",
                confirmation_cancel_reason=reason,
            )
        )
        pending = None
        state = (
            ReversalConfirmationState.WAIT_SOURCE_RETURN
            if reason in {"trend_reverted", "trend_unready"}
            else ReversalConfirmationState.ARMED
        )

    for t in range(n):
        current = int(positions[t])
        if date_execution is not None and date_execution.boundary[t]:
            if pending is not None:
                finish_cancel("date_filter_close")
            state = ReversalConfirmationState.ARMED
            current = 0
        current_target = int(target[t])
        previous_target = int(target[t - 1]) if t else 0
        previous_inside = bool(inside[t - 1]) if t else False
        next_position = current

        if combined:
            time_decision = decide_time_entry(
                time_state,
                TimeEntryInputs(
                    t=t,
                    current_position=current,
                    current_target=current_target,
                    previous_target=previous_target,
                    in_window=bool(inside[t]),
                    previous_in_window=previous_inside,
                    time_reset=bool(time_reset[t]),
                ),
            )
            entry_allowed = time_decision.entry_allowed
            next_reentry_pending = time_decision.next_reentry_pending
            block_reason[t] = time_decision.reason
        else:
            entry_allowed = current_target != 0
            next_reentry_pending = False

        if time_reset[t]:
            if pending is not None:
                finish_cancel("time_filter_reset")
            next_position = 0
            if t + 1 < n and current != 0:
                forced_exit[t] = 1
                metadata["reversal_confirmation_execution_origin_bar"][t + 1] = t
                metadata["reversal_confirmation_execution_decision_bar"][t + 1] = t
                metadata[
                    "reversal_confirmation_execution_transition_kind"
                ][t + 1] = TRANSITION_TIME_FORCED_CLOSE
            time_state = TimeEntryState(
                reentry_pending=next_reentry_pending
            )
        else:
            if pending is not None:
                source_position = int(pending["source_position"])
                cancel_reason: Optional[str] = None
                if combined and not inside[t]:
                    cancel_reason = "outside_window"
                elif int(trend_arr[t]) == 0:
                    cancel_reason = "trend_unready"
                elif int(trend_arr[t]) != -source_position:
                    cancel_reason = "trend_reverted"

                if cancel_reason is not None:
                    finish_cancel(cancel_reason)
                else:
                    pending["confirmation_count"] = (
                        int(pending["confirmation_count"]) + 1
                    )
                    if int(pending["confirmation_count"]) >= confirm_bars:
                        execution_bar = t + 1
                        if execution_bar >= n:
                            finish_cancel("execution_unavailable")
                        elif combined and (
                            identities is None
                            or identities[t] is None
                            or identities[execution_bar] != identities[t]
                        ):
                            finish_cancel("execution_outside_window")
                        else:
                            event_id = int(pending["event_id"])
                            execution_position = (
                                -source_position
                                if trade_mode in {"revers", "both"}
                                else 0
                            )
                            if date_execution is not None:
                                execution_position = date_execution.at_open(
                                    execution_bar, current, execution_position
                                )
                            next_position = execution_position
                            attempts.append(
                                ReversalConfirmationAttempt(
                                    event_id=event_id,
                                    source_signal_bar=int(
                                        pending["source_signal_bar"]
                                    ),
                                    source_signal_time=pending[
                                        "source_signal_time"
                                    ],
                                    source_position=source_position,
                                    source_raw_trend=int(
                                        pending["source_raw_trend"]
                                    ),
                                    confirmation_bar=t,
                                    confirmation_time=pd.Timestamp(dt_index[t]),
                                    execution_bar=execution_bar,
                                    execution_time=pd.Timestamp(
                                        dt_index[execution_bar]
                                    ),
                                    confirmation_status="confirmed",
                                    confirmation_cancel_reason=None,
                                )
                            )
                            metadata[
                                "reversal_confirmation_execution_event_id"
                            ][execution_bar] = event_id
                            metadata[
                                "reversal_confirmation_execution_origin_bar"
                            ][execution_bar] = int(pending["source_signal_bar"])
                            metadata[
                                "reversal_confirmation_execution_decision_bar"
                            ][execution_bar] = t
                            metadata[
                                "reversal_confirmation_execution_transition_kind"
                            ][execution_bar] = TRANSITION_CONFIRMED_CHANGE
                            metadata[
                                "reversal_confirmation_confirmed_close"
                            ][execution_bar] = 1
                            if execution_position != 0:
                                metadata[
                                    "reversal_confirmation_confirmed_open"
                                ][execution_bar] = 1
                            pending = None
                            state = ReversalConfirmationState.ARMED

            if pending is None:
                if state == ReversalConfirmationState.WAIT_SOURCE_RETURN:
                    if current != 0 and int(trend_arr[t]) == current:
                        state = ReversalConfirmationState.ARMED
                elif current == 0:
                    if current_target == 0:
                        next_position = 0
                    elif entry_allowed:
                        execution_bar = t + 1
                        same_cycle_execution = (
                            not combined
                            or (
                                execution_bar < n
                                and identities is not None
                                and identities[t] is not None
                                and identities[execution_bar] == identities[t]
                            )
                        )
                        if (execution_bar < n and same_cycle_execution and
                                (date_execution is None or date_execution.entry[execution_bar])):
                            next_position = current_target
                            allowed_entry[t] = 1
                            kind = (
                                TRANSITION_TIME_WINDOW_REENTRY
                                if time_state.reentry_pending
                                else TRANSITION_FLAT_RAW_ENTRY
                            )
                            metadata[
                                "reversal_confirmation_execution_origin_bar"
                            ][execution_bar] = t
                            metadata[
                                "reversal_confirmation_execution_decision_bar"
                            ][execution_bar] = t
                            metadata[
                                "reversal_confirmation_execution_transition_kind"
                            ][execution_bar] = kind
                        elif combined and not same_cycle_execution:
                            block_reason[t] = (
                                "execution_unavailable"
                                if execution_bar >= n
                                else "execution_outside_window"
                            )
                else:
                    raw_edge = t == 0 or int(trend_arr[t]) != int(trend_arr[t - 1])
                    if (
                        state == ReversalConfirmationState.ARMED
                        and raw_edge
                        and int(trend_arr[t]) == -current
                        and (not combined or inside[t])
                    ):
                        pending = {
                            "event_id": next_event_id,
                            "source_signal_bar": t,
                            "source_signal_time": pd.Timestamp(dt_index[t]),
                            "source_position": current,
                            "source_raw_trend": int(trend_arr[t]),
                            "confirmation_count": 0,
                        }
                        next_event_id += 1
                        state = ReversalConfirmationState.PENDING

            time_state = TimeEntryState(
                reentry_pending=next_reentry_pending
            )

        state_trace[t] = state.value
        if t + 1 < n:
            positions[t + 1] = np.int8(next_position)

    if pending is not None:
        finish_cancel("dataset_end")

    confirmed = sum(a.confirmation_status == "confirmed" for a in attempts)
    cancelled = len(attempts) - confirmed
    boundary_reasons = {
        "outside_window",
        "execution_unavailable",
        "execution_outside_window",
        "dataset_end",
    }
    summary = ReversalConfirmationSummary(
        started=len(attempts),
        confirmed=confirmed,
        cancelled=cancelled,
        executed=confirmed,
        cancelled_by_time_reset=sum(
            a.confirmation_cancel_reason == "time_filter_reset" for a in attempts
        ),
        cancelled_by_boundary=sum(
            a.confirmation_cancel_reason in boundary_reasons for a in attempts
        ),
    )
    summary.validate()

    diagnostics: Optional[dict[str, np.ndarray]]
    if collect_filter_diagnostics:
        diagnostics = {
            "trade_filter_enabled": np.ones(n, dtype=np.int8),
            "trade_filter_mode": np.full(
                n,
                "reversal_and_time" if combined else "reversal_only",
                dtype=object,
            ),
            "filter_allowed_entry": allowed_entry,
            "filter_block_reason": block_reason,
            "daily_reset_event": metadata["daily_reset_event"],
            "time_filter_enabled": np.full(n, int(combined), dtype=np.int8),
            "time_filter_in_window": np.asarray(inside, dtype=np.int8),
            "time_filter_reset_event": metadata["time_filter_reset_event"],
            "time_filter_forced_exit": forced_exit,
            "reversal_confirmation_state": state_trace,
        }
    else:
        diagnostics = None

    return ReversalConfirmationResult(
        positions=positions,
        filter_diagnostics=diagnostics,
        execution_metadata=metadata,
        attempts=tuple(attempts),
        summary=summary,
        filter_config_snapshot={
            "trade_filter_enabled": True,
            "mode": "reversal_and_time" if combined else "reversal_only",
            "reversal_confirmation_enabled": True,
            "confirm_bars": confirm_bars,
            "time_filter_enabled": combined,
            "canonical_windows_minutes": (
                tuple(time_filter_runtime.schedule.windows_minutes)
                if combined
                else ()
            ),
            "boundary_policy": (
                "same_local_date_window" if combined else "standalone"
            ),
        },
    )


__all__ = [
    "ReversalConfirmationAttempt",
    "ReversalConfirmationResult",
    "ReversalConfirmationState",
    "ReversalConfirmationSummary",
    "TRANSITION_CONFIRMED_CHANGE",
    "TRANSITION_FLAT_RAW_ENTRY",
    "TRANSITION_NONE",
    "TRANSITION_TIME_FORCED_CLOSE",
    "TRANSITION_TIME_WINDOW_REENTRY",
    "apply",
]
