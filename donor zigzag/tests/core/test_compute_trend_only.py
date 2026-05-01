"""Tests for compute_trend_only — SSOT for SuperTrend direction (plan v2.0 §3.4.0).

GATE test: test_trend_byte_equal_to_backtest_trend must be green
before Stage 2 of the ZigZag filter implementation begins.
"""
import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.backtest import (
    compute_trend_only,
    run_backtest_fast,
)
from supertrend_optimizer.core.calculator import calculate_atr_rma, calculate_true_range
from supertrend_optimizer.utils.enums import ExecutionModel


def _make_data(n: int, seed: int):
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, n))
    high = close + rng.uniform(0.1, 1.5, n)
    low = close - rng.uniform(0.1, 1.5, n)
    open_ = close + rng.normal(0, 0.3, n)
    return open_.astype(np.float64), high.astype(np.float64), low.astype(np.float64), close.astype(np.float64)


def _compute_atr(high, low, close, atr_period):
    tr = calculate_true_range(high, low, close)
    return calculate_atr_rma(tr, atr_period)


class TestComputeTrendOnlyByteEqual:
    """GATE: compute_trend_only must be byte-identical to run_backtest_fast.trend.

    Plan §3.4.0 invariant: without this guarantee, the ZigZag branch and
    the amplitude/legacy branch could diverge by 1 bar → different
    armament decisions → different trades.
    """

    @pytest.mark.parametrize(
        "n,seed,atr_period,multiplier",
        [
            (500, 42, 14, 2.0),
            (1000, 7, 10, 3.0),
            (2000, 123, 21, 1.5),
        ],
    )
    def test_trend_byte_equal_to_backtest_trend(
        self, n, seed, atr_period, multiplier,
    ):
        """compute_trend_only(atr, ...) == run_backtest_fast(..., precomputed_atr=atr).trend."""
        open_, high, low, close = _make_data(n, seed)
        atr = _compute_atr(high, low, close, atr_period)

        trend_ssot = compute_trend_only(
            atr=atr, high=high, low=low, close=close,
            multiplier=multiplier, atr_period=atr_period,
        )

        # Use run_backtest_fast with same precomputed_atr
        _ret, _eq, trend_bt, _pos, _ee, _eb, _ed = run_backtest_fast(
            open_prices=open_, high=high, low=low, close=close,
            atr_period=atr_period, multiplier=multiplier,
            trade_mode="revers", commission=0.0,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=10,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            precomputed_atr=atr,
        )

        assert trend_ssot.shape == trend_bt.shape
        assert trend_ssot.dtype == trend_bt.dtype
        # Byte-equal: trend arrays must match element-wise.
        np.testing.assert_array_equal(trend_ssot, trend_bt)

    def test_trend_dtype_is_int8(self):
        open_, high, low, close = _make_data(300, 1)
        atr = _compute_atr(high, low, close, 14)
        trend = compute_trend_only(
            atr=atr, high=high, low=low, close=close,
            multiplier=2.0, atr_period=14,
        )
        assert trend.dtype == np.int8

    def test_trend_values_in_minus1_0_plus1(self):
        open_, high, low, close = _make_data(300, 5)
        atr = _compute_atr(high, low, close, 14)
        trend = compute_trend_only(
            atr=atr, high=high, low=low, close=close,
            multiplier=2.0, atr_period=14,
        )
        unique_vals = set(np.unique(trend).tolist())
        assert unique_vals.issubset({-1, 0, 1})


class TestRunBacktestFastUsesComputeTrendOnly:
    """Архитектурная гарантия §3.4.0: run_backtest_fast должен вызывать compute_trend_only."""

    def test_run_backtest_fast_calls_compute_trend_only(self, monkeypatch):
        import supertrend_optimizer.core.backtest as bt

        called = {"n": 0}
        real = bt.compute_trend_only

        def spy(*a, **kw):
            called["n"] += 1
            return real(*a, **kw)

        monkeypatch.setattr(bt, "compute_trend_only", spy)

        open_, high, low, close = _make_data(200, 99)
        atr = _compute_atr(high, low, close, 14)
        run_backtest_fast(
            open_prices=open_, high=high, low=low, close=close,
            atr_period=14, multiplier=2.0,
            trade_mode="revers", commission=0.0,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=10,
            precomputed_atr=atr,
        )
        assert called["n"] >= 1, "run_backtest_fast не вызвал compute_trend_only"

    def test_run_backtest_fast_calls_compute_trend_only_no_precomputed_atr(self, monkeypatch):
        """Проверка пути без precomputed_atr — ATR вычисляется внутри перед вызовом."""
        import supertrend_optimizer.core.backtest as bt

        called = {"n": 0}
        real = bt.compute_trend_only

        def spy(*a, **kw):
            called["n"] += 1
            return real(*a, **kw)

        monkeypatch.setattr(bt, "compute_trend_only", spy)

        open_, high, low, close = _make_data(200, 55)
        run_backtest_fast(
            open_prices=open_, high=high, low=low, close=close,
            atr_period=14, multiplier=2.0,
            trade_mode="revers", commission=0.0,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=10,
            precomputed_atr=None,
        )
        assert called["n"] >= 1, "run_backtest_fast без precomputed_atr не вызвал compute_trend_only"
