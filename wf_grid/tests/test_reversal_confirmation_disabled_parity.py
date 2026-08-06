from __future__ import annotations

import numpy as np

from supertrend_optimizer.core.backtest import generate_positions
from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    TradeFilterReversalConfirmationConfig,
    resolve_trade_filter_mode,
)


def test_disabled_reversal_template_does_not_change_mode_or_positions() -> None:
    config = TradeFilterConfig(
        enabled=False,
        reversal_confirmation=TradeFilterReversalConfirmationConfig(
            enabled=False,
            confirm_bars=None,
        ),
    )
    assert resolve_trade_filter_mode(config) == "disabled"
    trend = np.asarray([0, 1, -1, 1], dtype=np.int8)
    np.testing.assert_array_equal(
        generate_positions(trend, "revers"),
        [0, 0, 1, -1],
    )
