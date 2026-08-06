from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.reversal_confirmation_filter import (
    ReversalConfirmationAttempt,
    ReversalConfirmationSummary,
    apply,
)
from supertrend_optimizer.core.trade_filter_decision import (
    merge_filter_execution_metadata,
)
from supertrend_optimizer.engine.result import BacktestResult


def test_metadata_schema_and_summary_invariants() -> None:
    result = apply(
        trend=np.asarray([1, 1, -1, -1, -1], dtype=np.int8),
        trade_mode="revers",
        confirm_bars=1,
        index=pd.date_range("2026-01-01", periods=5, freq="min"),
    )
    assert result.summary.started == 1
    assert result.summary.confirmed == result.summary.executed == 1
    result.summary.validate()
    for key, arr in result.execution_metadata.items():
        assert arr.ndim == 1
        assert len(arr) == 5
        if key.endswith(("event_id", "origin_bar", "decision_bar")):
            assert arr.dtype == np.int64
        else:
            assert arr.dtype == np.int8


def test_metadata_merge_rejects_conflicts_and_unknown_namespaces() -> None:
    reset = {
        "daily_reset_event": np.zeros(2, dtype=np.int8),
        "time_filter_reset_event": np.zeros(2, dtype=np.int8),
    }
    assert merge_filter_execution_metadata(reset) == reset
    with pytest.raises(ValueError, match="conflict"):
        merge_filter_execution_metadata(reset, reset)
    with pytest.raises(ValueError, match="unknown"):
        merge_filter_execution_metadata(
            {"not_namespaced": np.zeros(2, dtype=np.int8)}
        )


def _backtest_result(**overrides) -> BacktestResult:
    values = {
        "atr_period": 2,
        "multiplier": 1.0,
        "trade_mode": "revers",
        "commission": 0.0,
        "warmup": 0,
        "returns": np.zeros(1),
        "equity_curve": np.ones(2),
        "positions": np.zeros(2, dtype=np.int8),
        "trend": np.ones(2, dtype=np.int8),
        "metrics": {},
        "early_exit": False,
        "exit_bar": None,
        "exit_drawdown": None,
        "trades_df": None,
        "n_bars_original": 2,
    }
    values.update(overrides)
    return BacktestResult(**values)


def _attempt(event_id: int) -> ReversalConfirmationAttempt:
    return ReversalConfirmationAttempt(
        event_id=event_id,
        source_signal_bar=0,
        source_signal_time=pd.Timestamp("2026-01-01"),
        source_position=1,
        source_raw_trend=-1,
        confirmation_bar=None,
        confirmation_time=None,
        execution_bar=None,
        execution_time=None,
        confirmation_status="cancelled",
        confirmation_cancel_reason="dataset_end",
    )


def test_event_and_summary_invariants_do_not_depend_on_metadata_presence() -> None:
    with pytest.raises(ValueError, match="attempt IDs must be unique"):
        _backtest_result(
            reversal_confirmation_events=(_attempt(0), _attempt(0)),
        )
    with pytest.raises(ValueError, match="started != confirmed"):
        _backtest_result(
            reversal_confirmation_summary=ReversalConfirmationSummary(
                started=1,
                confirmed=0,
                cancelled=0,
                executed=0,
                cancelled_by_time_reset=0,
                cancelled_by_boundary=0,
            ),
        )
