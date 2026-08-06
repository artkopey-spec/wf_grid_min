from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.reversal_confirmation_filter import apply


def _run(trend, *, mode="revers", confirm_bars=1):
    return apply(
        trend=np.asarray(trend, dtype=np.int8),
        trade_mode=mode,
        confirm_bars=confirm_bars,
        index=pd.date_range("2026-01-01", periods=len(trend), freq="h"),
    )


def test_g01_flat_entry_needs_no_attempt() -> None:
    result = _run([1, 1, 1])
    np.testing.assert_array_equal(result.positions, [0, 1, 1])
    assert result.attempts == ()


def test_g02_l02_l03_flat_reentry_needs_no_attempt() -> None:
    result = _run([1, 1, -1, -1, -1, -1, 1, 1], mode="long")
    np.testing.assert_array_equal(result.positions, [0, 1, 1, 1, 0, 0, 0, 1])
    assert len(result.attempts) == 1


@pytest.mark.parametrize(
    ("trend", "expected"),
    [
        ([1, 1, -1, -1, -1], [0, 1, 1, 1, -1]),
        ([-1, -1, 1, 1, 1], [0, -1, -1, -1, 1]),
    ],
)
def test_r01_r02_confirmation_executes_at_t0_plus_two(trend, expected) -> None:
    result = _run(trend)
    np.testing.assert_array_equal(result.positions, expected)
    attempt = result.attempts[0]
    assert (attempt.source_signal_bar, attempt.confirmation_bar, attempt.execution_bar) == (
        2,
        3,
        4,
    )


def test_r03_n3_executes_at_t0_plus_four() -> None:
    result = _run([1, 1, -1, -1, -1, -1, -1], confirm_bars=3)
    assert result.attempts[0].execution_bar == 6
    np.testing.assert_array_equal(result.positions, [0, 1, 1, 1, 1, 1, -1])


def test_r04_r05_cancel_and_wait_for_source_return() -> None:
    result = _run([1, 1, -1, 1, 1, 1, 1], confirm_bars=2)
    assert len(result.attempts) == 1
    assert result.attempts[0].confirmation_cancel_reason == "trend_reverted"
    np.testing.assert_array_equal(result.positions, [0, 1, 1, 1, 1, 1, 1])


def test_r06_fresh_edge_after_source_return_rearms() -> None:
    result = _run([1, 1, -1, 1, 1, -1, -1, -1], confirm_bars=1)
    assert [a.event_id for a in result.attempts] == [0, 1]
    assert result.attempts[0].confirmation_status == "cancelled"
    assert result.attempts[1].confirmation_status == "confirmed"


def test_r07_execution_bar_can_start_fresh_opposite_attempt() -> None:
    result = _run([1, 1, -1, -1, 1, 1, 1])
    assert [attempt.source_signal_bar for attempt in result.attempts] == [2, 4]
    assert [attempt.execution_bar for attempt in result.attempts] == [4, 6]


def test_long_confirmation_closes_without_open_short() -> None:
    result = _run([1, 1, -1, -1, -1], mode="long")
    np.testing.assert_array_equal(result.positions, [0, 1, 1, 1, 0])
    execution = result.attempts[0].execution_bar
    assert result.execution_metadata[
        "reversal_confirmation_confirmed_close"
    ][execution] == 1
    assert result.execution_metadata[
        "reversal_confirmation_confirmed_open"
    ][execution] == 0


def test_boundary_reasons_distinguish_dataset_end_and_no_execution() -> None:
    unfinished = _run([1, 1, -1], confirm_bars=2)
    assert unfinished.attempts[0].confirmation_cancel_reason == "dataset_end"
    no_execution = _run([1, 1, -1, -1], confirm_bars=1)
    assert no_execution.attempts[0].confirmation_cancel_reason == (
        "execution_unavailable"
    )


def test_both_and_revers_are_bit_exact() -> None:
    trend = [1, 1, -1, -1, -1, 1, 1, 1]
    both = _run(trend, mode="both")
    revers = _run(trend, mode="revers")
    np.testing.assert_array_equal(both.positions, revers.positions)
    assert both.attempts == revers.attempts


def test_s01_short_mode_confirmed_close_is_exact_mirror() -> None:
    long_side = _run([1, 1, -1, -1, -1], mode="long")
    short_side = _run([-1, -1, 1, 1, 1], mode="short")
    np.testing.assert_array_equal(short_side.positions, -long_side.positions)
