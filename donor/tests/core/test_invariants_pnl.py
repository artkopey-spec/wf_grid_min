"""
Invariant test: Trade metrics from run_single_backtest must match trades_df aggregates.

This test ensures that after the simple returns migration, metrics returned by
run_single_backtest() are correctly calculated from trades_df using simple entry/exit returns.

NEW INVARIANTS (source of truth = trades_df["net_pnl_pct"]):
1. metrics["sum_pnl_pct"] == trades_df["net_pnl_pct"].sum()
2. metrics["num_trades"] == len(trades_df)
3. metrics["win_rate"] == (trades_df["net_pnl_pct"] > 0).mean() * 100
4. metrics["avg_trade"] == sum_pnl_pct / num_trades (or INVALID_METRIC_VALUE if num_trades==0)
5. metrics["profit_factor"] == sum(profits) / sum(abs(losses))
   - If no losses but has profits: MAX_VALID_METRIC (9999.0) — F-08 cap
   - If no trades or all zero: INVALID_METRIC_VALUE
"""

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE, MAX_VALID_METRIC
from tests.fixtures.data_generator import make_daily_ohlc


def test_pnl_invariant_no_trades():
    """Test PnL invariant when there are no trades (flat prices, no signals)."""
    # Create flat price data that won't generate any trades
    n_bars = 50
    flat_price = 100.0
    
    open_prices = np.full(n_bars, flat_price, dtype=np.float64)
    high_prices = np.full(n_bars, flat_price, dtype=np.float64)
    low_prices = np.full(n_bars, flat_price, dtype=np.float64)
    close_prices = np.full(n_bars, flat_price, dtype=np.float64)
    index = pd.date_range('2020-01-01', periods=n_bars, freq='D')
    
    # Run backtest with very wide stops to avoid trades
    result = run_single_backtest(
        open_prices=open_prices,
        high=high_prices,
        low=low_prices,
        close=close_prices,
        index=index,
        atr_period=10,
        multiplier=10.0,  # Very wide stops
        trade_mode="long",
        commission=0.001,
        warmup_period=10,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        periods_per_year=252,
        min_trades_required=1,
        extract_trades_flag=True,
        caller_mode="test"
    )
    
    # If no trades generated, verify metrics are set correctly
    if result.trades_df is None or len(result.trades_df) == 0:
        assert result.metrics['num_trades'] == 0, \
            f"num_trades should be 0, got {result.metrics['num_trades']}"
        
        assert result.metrics['sum_pnl_pct'] == 0.0, \
            f"sum_pnl_pct should be 0.0, got {result.metrics['sum_pnl_pct']}"
        
        assert result.metrics['win_rate'] == 0.0, \
            f"win_rate should be 0.0, got {result.metrics['win_rate']}"
        
        assert result.metrics['avg_trade'] == INVALID_METRIC_VALUE, \
            f"avg_trade should be INVALID_METRIC_VALUE, got {result.metrics['avg_trade']}"
        
        assert result.metrics['profit_factor'] == INVALID_METRIC_VALUE, \
            f"profit_factor should be INVALID_METRIC_VALUE, got {result.metrics['profit_factor']}"
    else:
        # If trades were generated (unlikely with flat prices), skip this test
        pytest.skip(f"Flat prices generated {len(result.trades_df)} trade(s), test not applicable")


def test_pnl_invariant_single_long_trade():
    """Test PnL invariant with a single long trade scenario."""
    # Generate data that should produce at least one trade
    df = make_daily_ohlc(n_bars=200, seed=42)
    
    # Run backtest
    result = run_single_backtest(
        open_prices=df["open"].values,
        high=df["high"].values,
        low=df["low"].values,
        close=df["close"].values,
        index=df.index,
        atr_period=14,
        multiplier=3.0,
        trade_mode="long",  # LONG only mode
        commission=0.001,
        warmup_period=14,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        periods_per_year=252,
        min_trades_required=1,
        extract_trades_flag=True,
        caller_mode="test"
    )
    
    # Verify trades exist
    assert result.trades_df is not None, "trades_df should not be None"
    assert len(result.trades_df) > 0, "trades_df should not be empty"
    
    trades_df = result.trades_df
    metrics = result.metrics
    
    # Invariant 1: sum_pnl_pct == sum of net_pnl_pct from trades
    expected_sum_pnl = trades_df['net_pnl_pct'].sum()
    assert np.isclose(metrics['sum_pnl_pct'], expected_sum_pnl, atol=1e-6), \
        f"sum_pnl_pct mismatch: metrics={metrics['sum_pnl_pct']:.6f}, trades={expected_sum_pnl:.6f}"
    
    # Invariant 2: num_trades == len(trades_df)
    assert metrics['num_trades'] == len(trades_df), \
        f"num_trades mismatch: metrics={metrics['num_trades']}, trades={len(trades_df)}"
    
    # Invariant 3: win_rate == (net_pnl_pct > 0).mean() * 100
    expected_win_rate = (trades_df['net_pnl_pct'] > 0).mean() * 100.0
    assert np.isclose(metrics['win_rate'], expected_win_rate, atol=1e-9), \
        f"win_rate mismatch: metrics={metrics['win_rate']:.6f}, expected={expected_win_rate:.6f}"
    
    # Invariant 4: avg_trade == sum_pnl_pct / num_trades
    expected_avg_trade = expected_sum_pnl / len(trades_df)
    assert np.isclose(metrics['avg_trade'], expected_avg_trade, atol=1e-6), \
        f"avg_trade mismatch: metrics={metrics['avg_trade']:.6f}, expected={expected_avg_trade:.6f}"
    
    # Invariant 5: profit_factor calculation
    profits = trades_df.loc[trades_df['net_pnl_pct'] > 0, 'net_pnl_pct'].sum()
    losses = trades_df.loc[trades_df['net_pnl_pct'] < 0, 'net_pnl_pct'].sum()
    
    if losses < 0:
        expected_profit_factor = profits / abs(losses)
        assert np.isclose(metrics['profit_factor'], expected_profit_factor, atol=1e-6), \
            f"profit_factor mismatch: metrics={metrics['profit_factor']:.6f}, expected={expected_profit_factor:.6f}"
    elif profits > 0:
        assert metrics['profit_factor'] == MAX_VALID_METRIC, \
            f"profit_factor should be MAX_VALID_METRIC ({MAX_VALID_METRIC}) when no losses, got {metrics['profit_factor']}"
    else:
        assert metrics['profit_factor'] == INVALID_METRIC_VALUE, \
            f"profit_factor should be INVALID_METRIC_VALUE when no trades, got {metrics['profit_factor']}"


def test_pnl_invariant_reversal():
    """Test PnL invariant with position reversal (revers mode)."""
    # Generate data for reversal strategy
    df = make_daily_ohlc(n_bars=300, seed=99)
    
    # Run backtest in revers mode (can go long or short)
    result = run_single_backtest(
        open_prices=df["open"].values,
        high=df["high"].values,
        low=df["low"].values,
        close=df["close"].values,
        index=df.index,
        atr_period=14,
        multiplier=3.0,
        trade_mode="revers",  # Reversal mode
        commission=0.001,
        warmup_period=14,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        periods_per_year=252,
        min_trades_required=1,
        extract_trades_flag=True,
        caller_mode="test"
    )
    
    # Verify trades exist
    assert result.trades_df is not None, "trades_df should not be None"
    assert len(result.trades_df) > 0, "trades_df should not be empty"
    
    trades_df = result.trades_df
    metrics = result.metrics
    
    # Invariant 1: sum_pnl_pct
    expected_sum_pnl = trades_df['net_pnl_pct'].sum()
    assert np.isclose(metrics['sum_pnl_pct'], expected_sum_pnl, atol=1e-6), \
        f"sum_pnl_pct mismatch: metrics={metrics['sum_pnl_pct']:.6f}, trades={expected_sum_pnl:.6f}"
    
    # Invariant 2: num_trades
    assert metrics['num_trades'] == len(trades_df), \
        f"num_trades mismatch: metrics={metrics['num_trades']}, trades={len(trades_df)}"
    
    # Invariant 3: win_rate
    expected_win_rate = (trades_df['net_pnl_pct'] > 0).mean() * 100.0
    assert np.isclose(metrics['win_rate'], expected_win_rate, atol=1e-9), \
        f"win_rate mismatch: metrics={metrics['win_rate']:.6f}, expected={expected_win_rate:.6f}"
    
    # Invariant 4: avg_trade
    expected_avg_trade = expected_sum_pnl / len(trades_df)
    assert np.isclose(metrics['avg_trade'], expected_avg_trade, atol=1e-6), \
        f"avg_trade mismatch: metrics={metrics['avg_trade']:.6f}, expected={expected_avg_trade:.6f}"
    
    # Invariant 5: profit_factor
    profits = trades_df.loc[trades_df['net_pnl_pct'] > 0, 'net_pnl_pct'].sum()
    losses = trades_df.loc[trades_df['net_pnl_pct'] < 0, 'net_pnl_pct'].sum()
    
    if losses < 0:
        expected_profit_factor = profits / abs(losses)
        assert np.isclose(metrics['profit_factor'], expected_profit_factor, atol=1e-6), \
            f"profit_factor mismatch: metrics={metrics['profit_factor']:.6f}, expected={expected_profit_factor:.6f}"
    elif profits > 0:
        assert metrics['profit_factor'] == MAX_VALID_METRIC, \
            f"profit_factor should be MAX_VALID_METRIC ({MAX_VALID_METRIC}) when no losses, got {metrics['profit_factor']}"
    else:
        assert metrics['profit_factor'] == INVALID_METRIC_VALUE, \
            f"profit_factor should be INVALID_METRIC_VALUE when no trades, got {metrics['profit_factor']}"


def test_pnl_invariant_multiple_trades():
    """Test PnL invariant with multiple separate trades."""
    # Generate data with enough bars for multiple trades
    df = make_daily_ohlc(n_bars=500, seed=123)
    
    # Run backtest
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
        warmup_period=14,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        periods_per_year=252,
        min_trades_required=1,
        extract_trades_flag=True,
        caller_mode="test"
    )
    
    # Verify trades exist
    assert result.trades_df is not None, "trades_df should not be None"
    assert len(result.trades_df) > 0, "trades_df should not be empty"
    
    trades_df = result.trades_df
    metrics = result.metrics
    
    # Invariant 1: sum_pnl_pct
    expected_sum_pnl = trades_df['net_pnl_pct'].sum()
    assert np.isclose(metrics['sum_pnl_pct'], expected_sum_pnl, atol=1e-6), \
        f"sum_pnl_pct mismatch: metrics={metrics['sum_pnl_pct']:.6f}, trades={expected_sum_pnl:.6f}"
    
    # Invariant 2: num_trades
    assert metrics['num_trades'] == len(trades_df), \
        f"num_trades mismatch: metrics={metrics['num_trades']}, trades={len(trades_df)}"
    
    # Invariant 3: win_rate
    expected_win_rate = (trades_df['net_pnl_pct'] > 0).mean() * 100.0
    assert np.isclose(metrics['win_rate'], expected_win_rate, atol=1e-9), \
        f"win_rate mismatch: metrics={metrics['win_rate']:.6f}, expected={expected_win_rate:.6f}"
    
    # Invariant 4: avg_trade
    expected_avg_trade = expected_sum_pnl / len(trades_df)
    assert np.isclose(metrics['avg_trade'], expected_avg_trade, atol=1e-6), \
        f"avg_trade mismatch: metrics={metrics['avg_trade']:.6f}, expected={expected_avg_trade:.6f}"
    
    # Invariant 5: profit_factor
    profits = trades_df.loc[trades_df['net_pnl_pct'] > 0, 'net_pnl_pct'].sum()
    losses = trades_df.loc[trades_df['net_pnl_pct'] < 0, 'net_pnl_pct'].sum()
    
    if losses < 0:
        expected_profit_factor = profits / abs(losses)
        assert np.isclose(metrics['profit_factor'], expected_profit_factor, atol=1e-6), \
            f"profit_factor mismatch: metrics={metrics['profit_factor']:.6f}, expected={expected_profit_factor:.6f}"
    elif profits > 0:
        assert metrics['profit_factor'] == MAX_VALID_METRIC, \
            f"profit_factor should be MAX_VALID_METRIC ({MAX_VALID_METRIC}) when no losses, got {metrics['profit_factor']}"
    else:
        assert metrics['profit_factor'] == INVALID_METRIC_VALUE, \
            f"profit_factor should be INVALID_METRIC_VALUE when no trades, got {metrics['profit_factor']}"

    # Additional check: verify we have multiple trades
    assert len(trades_df) >= 3, \
        f"Expected at least 3 trades for this test, got {len(trades_df)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
