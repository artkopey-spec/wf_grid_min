"""Pure transition kernel shared by time-only and combined runtimes."""

from __future__ import annotations

from dataclasses import dataclass

from supertrend_optimizer.core.trade_filter_decision import (
    decide_trade_filter_entry,
)


@dataclass(frozen=True)
class TimeEntryState:
    reentry_pending: bool = False


@dataclass(frozen=True)
class TimeEntryInputs:
    t: int
    current_position: int
    current_target: int
    previous_target: int
    in_window: bool
    previous_in_window: bool
    time_reset: bool


@dataclass(frozen=True)
class TimeEntryDecision:
    entry_attempt: bool
    entry_allowed: bool
    forced_flat: bool
    forced_exit: bool
    next_reentry_pending: bool
    reason: str


def decide_time_entry(
    state: TimeEntryState,
    inputs: TimeEntryInputs,
) -> TimeEntryDecision:
    """Evaluate one time-filter decision without owning the positions array."""
    reentry_pending = bool(state.reentry_pending)
    if inputs.current_target == 0:
        reentry_pending = False

    raw_change = inputs.current_target != 0 and (
        inputs.t == 0 or inputs.current_target != inputs.previous_target
    )
    cycle_open = inputs.in_window and (
        inputs.t == 0
        or not inputs.previous_in_window
        or inputs.time_reset
    )
    entry_attempt = inputs.current_target != 0 and (
        raw_change or cycle_open or reentry_pending
    )
    gate = decide_trade_filter_entry(
        entry_attempt_event=entry_attempt,
        time_filter_in_window=inputs.in_window,
        combined_reset_event=inputs.time_reset,
        daily_reset_event=False,
        time_filter_reset_event=inputs.time_reset,
    )

    next_reentry_pending = reentry_pending
    if inputs.time_reset:
        next_reentry_pending = inputs.in_window and inputs.current_target != 0
    elif inputs.current_target == 0:
        next_reentry_pending = False
    elif gate.entry_allowed:
        next_reentry_pending = False

    return TimeEntryDecision(
        entry_attempt=entry_attempt,
        entry_allowed=bool(gate.entry_allowed),
        forced_flat=bool(inputs.time_reset),
        forced_exit=bool(inputs.time_reset and inputs.current_position != 0),
        next_reentry_pending=bool(next_reentry_pending),
        reason=gate.reason,
    )


__all__ = [
    "TimeEntryDecision",
    "TimeEntryInputs",
    "TimeEntryState",
    "decide_time_entry",
]
