from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    TradeFilterReversalConfirmationConfig,
    TradeFilterTimeFilterConfig,
    TradeFilterVolumeConfig,
    TradeFilterZigZagConfig,
    validate_trade_filter,
)
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.exceptions import ConfigError


def _validated_config() -> TradeFilterConfig:
    config = TradeFilterConfig(
        enabled=True,
        zigzag=TradeFilterZigZagConfig(enabled=False),
        volume=TradeFilterVolumeConfig(enabled=False),
        time_filter=TradeFilterTimeFilterConfig(enabled=False),
        reversal_confirmation=TradeFilterReversalConfirmationConfig(
            enabled=True,
            confirm_bars=1,
        ),
    )
    errors: list[str] = []
    validate_trade_filter(
        config,
        errors,
        frozenset(
            {
                ("trade_filter",),
                ("trade_filter", "enabled"),
            }
        ),
        caller_pipeline="single",
    )
    assert errors == []
    return config


def _run(
    config: TradeFilterConfig,
    *,
    caller_mode: str = "single",
    early_exit_enabled: bool = False,
):
    close = 100.0 + np.sin(np.arange(20))
    return run_single_backtest(
        open_prices=np.r_[close[0], close[:-1]],
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        index=pd.date_range("2026-01-01", periods=20, freq="min"),
        atr_period=2,
        multiplier=1.0,
        trade_mode="revers",
        commission=0.0,
        warmup_period=0,
        min_trades_required=0,
        caller_mode=caller_mode,
        early_exit_enabled=early_exit_enabled,
        trade_filter_config=config,
    )


def test_direct_single_returns_consistent_reversal_transport() -> None:
    result = _run(_validated_config())
    assert result.filter_execution_metadata is not None
    assert result.reversal_confirmation_events is not None
    assert result.reversal_confirmation_summary is not None


def test_invalid_caller_is_rejected_before_supertrend(monkeypatch) -> None:
    import supertrend_optimizer.core.backtest as backtest

    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr(backtest, "calculate_supertrend", forbidden)
    with pytest.raises(ConfigError):
        _run(_validated_config(), caller_mode="optimizer")
    assert called is False


def test_early_exit_is_rejected() -> None:
    with pytest.raises(
        ConfigError, match="reversal_confirmation_early_exit_unsupported"
    ):
        _run(_validated_config(), early_exit_enabled=True)
