from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from supertrend_optimizer.core.reversal_confirmation_filter import apply


def test_short_sequences_preserve_core_invariants() -> None:
    for trend in itertools.product((-1, 0, 1), repeat=5):
        result = apply(
            trend=np.asarray(trend, dtype=np.int8),
            trade_mode="revers",
            confirm_bars=1,
            index=pd.date_range("2026-01-01", periods=5, freq="min"),
            collect_filter_diagnostics=False,
        )
        assert set(np.unique(result.positions)) <= {-1, 0, 1}
        assert result.summary.started == (
            result.summary.confirmed + result.summary.cancelled
        )
        assert result.summary.confirmed == result.summary.executed
        ids = [attempt.event_id for attempt in result.attempts]
        assert len(ids) == len(set(ids))
        for attempt in result.attempts:
            for bar in (
                attempt.source_signal_bar,
                attempt.confirmation_bar,
                attempt.execution_bar,
            ):
                assert bar is None or 0 <= bar < len(trend)


def test_d01_diagnostics_toggle_cannot_change_execution() -> None:
    kwargs = {
        "trend": np.asarray([1, 1, -1, -1, -1, 1], dtype=np.int8),
        "trade_mode": "revers",
        "confirm_bars": 1,
        "index": pd.date_range("2026-01-01", periods=6, freq="min"),
    }
    enabled = apply(**kwargs, collect_filter_diagnostics=True)
    disabled = apply(**kwargs, collect_filter_diagnostics=False)
    np.testing.assert_array_equal(enabled.positions, disabled.positions)
    assert enabled.attempts == disabled.attempts
    assert enabled.summary == disabled.summary
    assert enabled.filter_diagnostics is not None
    assert disabled.filter_diagnostics is None
