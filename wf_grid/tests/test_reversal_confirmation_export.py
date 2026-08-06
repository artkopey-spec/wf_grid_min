from __future__ import annotations

import numpy as np
import pandas as pd

from supertrend_optimizer.core.reversal_confirmation_filter import apply
from supertrend_optimizer.testing.reversal_confirmation_export import (
    EXECUTION_SIGNAL_COLUMNS,
    REVERSAL_EVENT_COLUMNS,
    build_execution_signals,
    build_reversal_confirmation_events,
)
from supertrend_optimizer.io.excel_tester import _prepare_legacy_trades_df


def _result():
    return apply(
        trend=np.asarray([1, 1, -1, -1, -1], dtype=np.int8),
        trade_mode="revers",
        confirm_bars=1,
        index=pd.date_range("2026-01-01", periods=5, freq="min"),
    )


def test_execution_signals_are_close_before_open_with_exact_schema() -> None:
    result = _result()
    index = pd.date_range("2026-01-01", periods=5, freq="min")
    frame = build_execution_signals(
        positions=result.positions,
        execution_prices=np.arange(100.0, 105.0),
        index=index,
        execution_metadata=result.execution_metadata,
    )
    assert list(frame.columns) == EXECUTION_SIGNAL_COLUMNS
    reversal = frame[frame["execution_bar"] == 4]
    assert list(reversal["event_type"]) == ["close", "open"]
    assert list(reversal["reversal_confirmation_event_id"]) == [0, 0]
    assert list(reversal["execution_price"]) == [104.0, 104.0]
    assert str(frame["reversal_confirmation_event_id"].dtype) == "Int64"


def test_attempt_export_has_exact_schema_and_nullable_bars() -> None:
    frame = build_reversal_confirmation_events(_result().attempts)
    assert list(frame.columns) == REVERSAL_EVENT_COLUMNS
    assert str(frame["confirmation_bar"].dtype) == "Int64"
    assert str(frame["execution_bar"].dtype) == "Int64"


def test_empty_active_trades_keep_reversal_linkage_columns() -> None:
    frame = _prepare_legacy_trades_df(
        pd.DataFrame(),
        filter_enabled=True,
        filter_diagnostics_available=False,
        reversal_active=True,
    )
    assert "Entry Reversal Confirmation Event ID" in frame.columns
    assert "Exit Reversal Confirmation Event ID" in frame.columns
    assert "Trade Close Reason" in frame.columns
