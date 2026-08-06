from __future__ import annotations

import numpy as np
import pandas as pd

from supertrend_optimizer.core.reversal_confirmation_filter import apply


def test_confirm_bars_upper_boundary_uses_short_fixture() -> None:
    result = apply(
        trend=np.asarray([1, 1, -1, -1], dtype=np.int8),
        trade_mode="revers",
        confirm_bars=1_000_000,
        index=pd.date_range("2026-01-01", periods=4, freq="min"),
        collect_filter_diagnostics=False,
    )
    assert result.attempts[0].confirmation_cancel_reason == "dataset_end"
    assert result.summary.active_pending == 0
