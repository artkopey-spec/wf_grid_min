"""Shared entry gate and reset-priority decision for trade filters."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TradeFilterDecision:
    entry_allowed: bool
    entry_blocked_outside: bool
    reason: str


def decide_trade_filter_entry(
    *,
    entry_attempt_event: bool,
    time_filter_in_window: bool,
    combined_reset_event: bool,
    daily_reset_event: bool,
    time_filter_reset_event: bool,
) -> TradeFilterDecision:
    """Apply the shared reset priority and Time entry gate."""
    if daily_reset_event:
        return TradeFilterDecision(False, False, "daily_reset")
    if time_filter_reset_event:
        return TradeFilterDecision(False, False, "time_filter_reset")

    entry_allowed = (
        entry_attempt_event
        and time_filter_in_window
        and not combined_reset_event
    )
    entry_blocked_outside = (
        entry_attempt_event
        and not time_filter_in_window
        and not combined_reset_event
    )
    return TradeFilterDecision(
        entry_allowed=entry_allowed,
        entry_blocked_outside=entry_blocked_outside,
        reason="time_filter_out_of_window"
        if entry_blocked_outside
        else "none",
    )


def build_filter_execution_metadata(
    *,
    daily_reset_event: np.ndarray,
    time_filter_reset_event: np.ndarray,
) -> dict[str, np.ndarray]:
    """Return the compact reset causes required for trade exit attribution."""
    daily = np.asarray(daily_reset_event, dtype=np.int8)
    time = np.asarray(time_filter_reset_event, dtype=np.int8)
    if daily.ndim != 1 or time.ndim != 1 or len(daily) != len(time):
        raise ValueError("reset execution metadata arrays must be aligned 1-D arrays")
    return {
        "daily_reset_event": daily,
        "time_filter_reset_event": time,
    }


_RESET_METADATA_KEYS = frozenset(
    {"daily_reset_event", "time_filter_reset_event"}
)
_REVERSAL_METADATA_KEYS = frozenset(
    {
        "reversal_confirmation_execution_event_id",
        "reversal_confirmation_execution_origin_bar",
        "reversal_confirmation_execution_decision_bar",
        "reversal_confirmation_execution_transition_kind",
        "reversal_confirmation_confirmed_close",
        "reversal_confirmation_confirmed_open",
    }
)


def merge_filter_execution_metadata(
    *metadata_maps: dict[str, np.ndarray] | None,
) -> dict[str, np.ndarray]:
    """Merge validated compact metadata without allowing key replacement."""
    merged: dict[str, np.ndarray] = {}
    expected_length: int | None = None
    allowed = _RESET_METADATA_KEYS | _REVERSAL_METADATA_KEYS
    for metadata in metadata_maps:
        if metadata is None:
            continue
        for key, values in metadata.items():
            if key not in allowed:
                raise ValueError(f"unknown filter execution metadata key: {key}")
            if key in merged:
                raise ValueError(f"filter execution metadata key conflict: {key}")
            arr = np.asarray(values)
            if arr.ndim != 1:
                raise ValueError(f"filter execution metadata {key} must be 1-D")
            if expected_length is None:
                expected_length = len(arr)
            elif len(arr) != expected_length:
                raise ValueError(
                    f"filter execution metadata {key} length {len(arr)} "
                    f"!= {expected_length}"
                )
            expected_dtype = (
                np.int64
                if key.endswith(("event_id", "origin_bar", "decision_bar"))
                else np.int8
            )
            if arr.dtype != expected_dtype:
                raise ValueError(
                    f"filter execution metadata {key} dtype {arr.dtype} "
                    f"!= {np.dtype(expected_dtype)}"
                )
            merged[key] = arr
    return merged


__all__ = [
    "TradeFilterDecision",
    "build_filter_execution_metadata",
    "decide_trade_filter_entry",
    "merge_filter_execution_metadata",
]
