from __future__ import annotations

import numpy as np
import pandas as pd

from supertrend_optimizer.core.filter_trade_diagnostics import (
    attach_trade_filter_diagnostics,
)
from supertrend_optimizer.core.reversal_confirmation_filter import apply
from supertrend_optimizer.core.trades import extract_trades


def _runtime_result():
    return apply(
        trend=np.asarray([1, 1, -1, -1, -1], dtype=np.int8),
        trade_mode="revers",
        confirm_bars=1,
        index=pd.date_range("2026-01-01", periods=5, freq="min"),
        collect_filter_diagnostics=False,
    )


def test_confirmed_reversal_links_close_and_open_trade() -> None:
    result = _runtime_result()
    trades = extract_trades(
        positions=result.positions,
        returns=np.zeros(4),
        execution_prices=np.arange(100.0, 105.0),
        index=pd.date_range("2026-01-01", periods=5, freq="min"),
        commission_rate=0.0,
    )
    enriched = attach_trade_filter_diagnostics(
        trades,
        None,
        execution_metadata=result.execution_metadata,
    )
    assert enriched.loc[0, "exit_reversal_confirmation_event_id"] == 0
    assert enriched.loc[0, "exit_reason"] == "reversal_confirmation_confirmed"
    assert enriched.loc[1, "entry_reversal_confirmation_event_id"] == 0
    assert str(enriched["entry_reversal_confirmation_event_id"].dtype) == "Int64"
    assert pd.isna(enriched.loc[1, "exit_reversal_confirmation_event_id"])
    assert enriched.loc[1, "exit_reason"] == "pending_open_trade_at_end"


def test_reset_reason_precedes_reversal_reason_m01() -> None:
    result = _runtime_result()
    metadata = {key: value.copy() for key, value in result.execution_metadata.items()}
    metadata["time_filter_reset_event"][3] = 1
    trades = pd.DataFrame([{"entry_index": 1, "exit_index": 4}])
    enriched = attach_trade_filter_diagnostics(
        trades,
        None,
        execution_metadata=metadata,
    )
    assert enriched.loc[0, "exit_reason"] == "filter_time_reset"
