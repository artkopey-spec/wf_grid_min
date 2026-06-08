from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np
import pandas as pd

from supertrend_optimizer.core import zigzag_st_filter
from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.testing import runner


def _ohlc_arrays(n: int = 8):
    open_prices = np.linspace(10.0, 17.0, n, dtype=np.float64)
    high = open_prices + 1.0
    low = open_prices - 1.0
    close = open_prices + 0.25
    return open_prices, high, low, close


def _ohlc_df() -> pd.DataFrame:
    open_prices, high, low, close = _ohlc_arrays()
    return pd.DataFrame(
        {
            "open": open_prices,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.arange(100, 108, dtype=np.float64),
        },
        index=pd.date_range("2026-01-01", periods=len(open_prices), freq="D"),
    )


def test_volume_plumbing_parameters_are_trailing_optional():
    for func in (run_backtest_fast, run_single_backtest, zigzag_st_filter.apply):
        param = inspect.signature(func).parameters["volume"]
        assert param.default is None


def test_run_backtest_fast_legacy_call_without_volume_still_works():
    open_prices, high, low, close = _ohlc_arrays()

    artifacts = run_backtest_fast(
        open_prices=open_prices,
        high=high,
        low=low,
        close=close,
        atr_period=2,
        multiplier=2.0,
        trade_mode="both",
        commission=0.0,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
    )

    assert len(artifacts.positions) == len(open_prices)
    assert artifacts.filter_diagnostics is None


def test_run_period_passes_volume_values_to_engine(monkeypatch):
    captured = {}

    def fake_run_single_backtest(**kwargs):
        captured["volume"] = kwargs.get("volume")
        return SimpleNamespace(
            returns=np.zeros(3, dtype=np.float64),
            warmup=0,
            filter_diagnostics=None,
            filter_config_snapshot=None,
        )

    monkeypatch.setattr(runner, "run_single_backtest", fake_run_single_backtest)
    df = _ohlc_df()

    result = runner.run_period(
        df=df,
        atr_period=2,
        multiplier=2.0,
        trade_mode="both",
        commission=0.0,
    )

    np.testing.assert_array_equal(captured["volume"], df["volume"].values)
    assert result.filter_diagnostics is None


def test_run_backtest_fast_passes_raw_volume_to_zigzag_apply(monkeypatch):
    captured = {}

    def fake_apply(**kwargs):
        captured["volume"] = kwargs.get("volume")
        captured["has_high"] = "high" in kwargs
        captured["has_low"] = "low" in kwargs
        return SimpleNamespace(
            positions=np.zeros(len(kwargs["trend"]), dtype=np.int8),
            filter_diagnostics=None,
            filter_config_snapshot=None,
        )

    monkeypatch.setattr(zigzag_st_filter, "apply", fake_apply)
    open_prices, high, low, close = _ohlc_arrays()
    volume = np.arange(len(open_prices), dtype=np.float64)
    trade_filter_config = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(enabled=True),
        volume=SimpleNamespace(enabled=False),
    )

    run_backtest_fast(
        open_prices=open_prices,
        high=high,
        low=low,
        close=close,
        atr_period=2,
        multiplier=2.0,
        trade_mode="both",
        commission=0.0,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        trade_filter_config=trade_filter_config,
        zigzag_global_stats=object(),
        volume=volume,
    )

    assert captured["volume"] is volume
    assert captured["has_high"] is False
    assert captured["has_low"] is False
