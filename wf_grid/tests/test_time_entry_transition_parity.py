from __future__ import annotations

import itertools

from supertrend_optimizer.core.time_entry_transition import (
    TimeEntryInputs,
    TimeEntryState,
    decide_time_entry,
)


def test_time_transition_truth_space_is_deterministic() -> None:
    for values in itertools.product((False, True), repeat=4):
        in_window, previous_in_window, reset, pending = values
        first = decide_time_entry(
            TimeEntryState(pending),
            TimeEntryInputs(
                t=1,
                current_position=1,
                current_target=1,
                previous_target=1,
                in_window=in_window,
                previous_in_window=previous_in_window,
                time_reset=reset,
            ),
        )
        second = decide_time_entry(
            TimeEntryState(pending),
            TimeEntryInputs(
                t=1,
                current_position=1,
                current_target=1,
                previous_target=1,
                in_window=in_window,
                previous_in_window=previous_in_window,
                time_reset=reset,
            ),
        )
        assert first == second


def test_reset_forces_flat_and_marks_reentry() -> None:
    decision = decide_time_entry(
        TimeEntryState(False),
        TimeEntryInputs(3, 1, -1, -1, True, True, True),
    )
    assert decision.forced_flat is True
    assert decision.forced_exit is True
    assert decision.next_reentry_pending is True
    assert decision.reason == "time_filter_reset"
