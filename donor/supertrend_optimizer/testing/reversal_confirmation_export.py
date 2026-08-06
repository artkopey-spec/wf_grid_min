"""Adapters for active reversal-confirmation tester exports."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from supertrend_optimizer.core.reversal_confirmation_filter import (
    TRANSITION_CONFIRMED_CHANGE,
    TRANSITION_FLAT_RAW_ENTRY,
    TRANSITION_TIME_FORCED_CLOSE,
    TRANSITION_TIME_WINDOW_REENTRY,
)


EXECUTION_SIGNAL_COLUMNS = [
    "execution_signal_id",
    "reversal_confirmation_event_id",
    "event_type",
    "side",
    "transition_reason",
    "execution_bar",
    "execution_time",
    "execution_price",
    "origin_bar",
    "origin_time",
    "decision_bar",
    "decision_time",
    "confirmation_status",
]
REVERSAL_EVENT_COLUMNS = [
    "event_id",
    "source_signal_bar",
    "source_signal_time",
    "source_position",
    "source_raw_trend",
    "confirmation_bar",
    "confirmation_time",
    "execution_bar",
    "execution_time",
    "confirmation_status",
    "confirmation_cancel_reason",
]


def _nullable_event_id(value: int) -> int | None:
    return int(value) if int(value) >= 0 else None


def build_execution_signals(
    *,
    positions: np.ndarray,
    execution_prices: np.ndarray,
    index: pd.Index,
    execution_metadata: Mapping[str, np.ndarray],
) -> pd.DataFrame:
    """Build actual close-before-open position transitions."""
    positions_arr = np.asarray(positions, dtype=np.int8)
    prices = np.asarray(execution_prices)
    if (
        positions_arr.ndim != 1
        or prices.ndim != 1
        or len(positions_arr) != len(prices)
        or len(index) != len(positions_arr)
    ):
        raise ValueError("execution signal inputs must be aligned 1-D arrays")

    event_ids = execution_metadata[
        "reversal_confirmation_execution_event_id"
    ]
    origin = execution_metadata[
        "reversal_confirmation_execution_origin_bar"
    ]
    decision = execution_metadata[
        "reversal_confirmation_execution_decision_bar"
    ]
    kinds = execution_metadata[
        "reversal_confirmation_execution_transition_kind"
    ]
    reason_names = {
        TRANSITION_FLAT_RAW_ENTRY: "flat_raw_entry",
        TRANSITION_TIME_WINDOW_REENTRY: "time_window_reentry",
        TRANSITION_TIME_FORCED_CLOSE: "time_forced_close",
        TRANSITION_CONFIRMED_CHANGE: "confirmed_change",
    }
    rows: list[dict] = []

    def append_row(
        execution_bar: int,
        event_type: str,
        side_value: int,
    ) -> None:
        kind = int(kinds[execution_bar])
        origin_bar = int(origin[execution_bar])
        decision_bar = int(decision[execution_bar])
        event_id = _nullable_event_id(event_ids[execution_bar])
        rows.append(
            {
                "execution_signal_id": len(rows),
                "reversal_confirmation_event_id": event_id,
                "event_type": event_type,
                "side": "long" if side_value == 1 else "short",
                "transition_reason": reason_names.get(kind, "position_change"),
                "execution_bar": execution_bar,
                "execution_time": pd.Timestamp(index[execution_bar]),
                "execution_price": float(prices[execution_bar]),
                "origin_bar": origin_bar,
                "origin_time": (
                    pd.Timestamp(index[origin_bar]) if origin_bar >= 0 else pd.NaT
                ),
                "decision_bar": decision_bar,
                "decision_time": (
                    pd.Timestamp(index[decision_bar])
                    if decision_bar >= 0
                    else pd.NaT
                ),
                "confirmation_status": (
                    "confirmed"
                    if kind == TRANSITION_CONFIRMED_CHANGE
                    else "not_required"
                ),
            }
        )

    for execution_bar in range(1, len(positions_arr)):
        old = int(positions_arr[execution_bar - 1])
        new = int(positions_arr[execution_bar])
        if old == new:
            continue
        if old != 0:
            append_row(execution_bar, "close", old)
        if new != 0:
            append_row(execution_bar, "open", new)

    frame = pd.DataFrame(rows, columns=EXECUTION_SIGNAL_COLUMNS)
    if len(frame):
        frame["reversal_confirmation_event_id"] = pd.array(
            frame["reversal_confirmation_event_id"], dtype="Int64"
        )
    else:
        frame["reversal_confirmation_event_id"] = pd.array([], dtype="Int64")
    return frame


def build_reversal_confirmation_events(
    attempts: Iterable[object],
) -> pd.DataFrame:
    """Return the normative one-row-per-attempt schema."""
    rows = [asdict(attempt) for attempt in attempts]
    frame = pd.DataFrame(rows, columns=REVERSAL_EVENT_COLUMNS)
    for column in ("confirmation_bar", "execution_bar"):
        frame[column] = pd.array(frame[column], dtype="Int64")
    if len(frame):
        frame["event_id"] = frame["event_id"].astype("int64")
        frame["source_signal_bar"] = frame["source_signal_bar"].astype("int64")
        frame["source_position"] = frame["source_position"].astype("int8")
        frame["source_raw_trend"] = frame["source_raw_trend"].astype("int8")
    return frame


__all__ = [
    "EXECUTION_SIGNAL_COLUMNS",
    "REVERSAL_EVENT_COLUMNS",
    "build_execution_signals",
    "build_reversal_confirmation_events",
]
