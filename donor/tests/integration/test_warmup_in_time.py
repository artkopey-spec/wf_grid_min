"""
Integration tests for warmup-in-time functionality.

Tests warmup_time parameter with different data frequencies (1m, 5m, 1h)
and with gaps (weekends).
"""

import pytest
import pandas as pd
import numpy as np
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel
from tests.fixtures.data_generator import make_intraday_ohlc, make_daily_ohlc


class TestWarmupInTime:
    """Tests for warmup_time parameter."""
    
    def test_warmup_time_1h_bars(self):
        """Test warmup_time with 1h bars."""
        # Generate 1h data (7 days = 168 bars)
        df = make_intraday_ohlc(
            n=200,
            seed=42,
            start_datetime="2023-01-01 00:00:00",
            freq="1h"
        )
        
        # Run with warmup_time="24h" (should be 24 bars)
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=None,
            warmup_time="24h",
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        
        # Check that warmup was applied correctly
        assert result.warmup == 24, f"Expected warmup=24, got {result.warmup}"
        
        # Check that metrics are valid
        assert "sharpe" in result.metrics
        assert "sortino" in result.metrics
    
    def test_warmup_time_5min_bars(self):
        """Test warmup_time with 5min bars."""
        # Generate 5min data
        df = make_intraday_ohlc(
            n=500,
            seed=42,
            start_datetime="2023-01-01 09:30:00",
            freq="5min"
        )
        
        # Run with warmup_time="1h" (should be 12 bars)
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=None,
            warmup_time="1h",
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        
        # Check that warmup was applied correctly
        assert result.warmup == 12, f"Expected warmup=12, got {result.warmup}"
    
    def test_warmup_time_1min_bars(self):
        """Test warmup_time with 1min bars."""
        # Generate 1min data
        df = make_intraday_ohlc(
            n=1000,
            seed=42,
            start_datetime="2023-01-01 09:30:00",
            freq="1min"
        )
        
        # Run with warmup_time="15m" (should be 15 bars)
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=None,
            warmup_time="15m",
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        
        # Check that warmup was applied correctly
        assert result.warmup == 15, f"Expected warmup=15, got {result.warmup}"
    
    def test_warmup_time_with_gaps_weekends(self):
        """Test warmup_time with weekday-only data (gaps on weekends)."""
        # Generate daily weekday-only data
        df = make_daily_ohlc(n_bars=100, seed=42, start_date="2023-01-02")  # Monday
        
        # Run with warmup_time="7d" (should be 7 bars, median delta = 1 day)
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=None,
            warmup_time="7d",
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        
        # Check that warmup was applied correctly
        # Median delta is 1 day (weekdays), so 7d = 7 bars
        assert result.warmup == 7, f"Expected warmup=7, got {result.warmup}"
    
    def test_warmup_time_short_period(self):
        """Test warmup_time with very short period (rounds up to 1 bar)."""
        # Generate 1h data
        df = make_intraday_ohlc(
            n=100,
            seed=42,
            start_datetime="2023-01-01 00:00:00",
            freq="1h"
        )
        
        # Run with warmup_time="1m" (1 min / 1 hour = 0.0167 → rounds up to 1)
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=None,
            warmup_time="1m",
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        
        # Check that warmup was rounded up to 1 bar
        assert result.warmup == 1, f"Expected warmup=1, got {result.warmup}"
    
    def test_auto_warmup_with_warmup_time(self):
        """Test auto_warmup with warmup_time."""
        # Generate 5min data
        df = make_intraday_ohlc(
            n=500,
            seed=42,
            start_datetime="2023-01-01 09:30:00",
            freq="5min"
        )
        
        # Run with warmup_time="30m" (6 bars) and auto_warmup=True
        # Should use max(6, 14) = 14
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=None,
            warmup_time="30m",
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            auto_warmup=True
        )
        
        # Check that auto_warmup applied max(6, 14) = 14
        assert result.warmup == 14, f"Expected warmup=14 (auto_warmup), got {result.warmup}"
    
    def test_auto_warmup_with_warmup_time_larger_than_atr(self):
        """Test auto_warmup with warmup_time > atr_period."""
        # Generate 5min data
        df = make_intraday_ohlc(
            n=500,
            seed=42,
            start_datetime="2023-01-01 09:30:00",
            freq="5min"
        )
        
        # Run with warmup_time="2h" (24 bars) and auto_warmup=True
        # Should use max(24, 14) = 24
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=None,
            warmup_time="2h",
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            auto_warmup=True
        )
        
        # Check that auto_warmup applied max(24, 14) = 24
        assert result.warmup == 24, f"Expected warmup=24, got {result.warmup}"
    
    def test_warmup_period_and_warmup_time_mutually_exclusive(self):
        """Test that providing both warmup_period and warmup_time raises ValueError."""
        # Generate 5min data
        df = make_intraday_ohlc(
            n=100,
            seed=42,
            start_datetime="2023-01-01 09:30:00",
            freq="5min"
        )
        
        # Should raise ValueError
        with pytest.raises(ValueError, match="Cannot specify both"):
            run_single_backtest(
                open_prices=df["open"].values,
                high=df["high"].values,
                low=df["low"].values,
                close=df["close"].values,
                index=df.index,
                atr_period=14,
                multiplier=3.0,
                trade_mode="revers",
                commission=0.001,
                warmup_period=20,
                warmup_time="1h",
                periods_per_year=252.0,
                min_trades_required=1,
                extract_trades_flag=True,
                execution_model=ExecutionModel.OPEN_TO_OPEN
            )
    
    def test_warmup_period_takes_precedence_over_none(self):
        """Test that warmup_period works when warmup_time is None."""
        # Generate 5min data
        df = make_intraday_ohlc(
            n=500,
            seed=42,
            start_datetime="2023-01-01 09:30:00",
            freq="5min"
        )
        
        # Run with warmup_period=20, warmup_time=None
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=20,
            warmup_time=None,
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        
        # Check that warmup_period was used
        assert result.warmup == 20, f"Expected warmup=20, got {result.warmup}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

