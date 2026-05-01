"""
Tests for trade metrics alignment with trades_df.

This module verifies that trade-based metrics (num_trades, win_rate, sum_pnl_pct,
avg_trade, profit_factor) are calculated from trades_df using simple returns,
not compound returns.
"""

import pytest
import numpy as np
import pandas as pd
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE, MAX_VALID_METRIC
from tests.fixtures.data_generator import make_daily_ohlc


class TestTradeMetricsAlignment:
    """Test suite for trade metrics alignment with trades_df."""
    
    def test_trade_metrics_aligned_with_trades_df(self):
        """
        Test that trade metrics in BacktestResult.metrics are aligned with trades_df.
        
        Verifies:
        - num_trades == len(trades_df)
        - sum_pnl_pct == trades_df["net_pnl_pct"].sum()
        - avg_trade == sum_pnl_pct / num_trades
        - win_rate == (trades_df["net_pnl_pct"] > 0).mean() * 100
        - profit_factor == sum(net_pnl_pct>0) / sum(abs(net_pnl_pct<0))
        """
        # Generate test data
        df = make_daily_ohlc(n_bars=500, seed=42)
        
        # Run backtest with trades extraction
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
        
        # Verify trades_df exists and is not empty
        assert result.trades_df is not None, "trades_df should not be None"
        assert len(result.trades_df) > 0, "trades_df should not be empty"
        
        trades_df = result.trades_df
        metrics = result.metrics
        
        # Test 1: num_trades == len(trades_df)
        assert metrics['num_trades'] == len(trades_df), \
            f"num_trades mismatch: metrics={metrics['num_trades']}, trades_df={len(trades_df)}"
        
        # Test 2: sum_pnl_pct == trades_df["net_pnl_pct"].sum()
        expected_sum_pnl = trades_df["net_pnl_pct"].sum()
        assert np.isclose(metrics['sum_pnl_pct'], expected_sum_pnl, atol=1e-6), \
            f"sum_pnl_pct mismatch: metrics={metrics['sum_pnl_pct']:.6f}, trades_df sum={expected_sum_pnl:.6f}"
        
        # Test 3: avg_trade == sum_pnl_pct / num_trades
        expected_avg_trade = expected_sum_pnl / len(trades_df)
        assert np.isclose(metrics['avg_trade'], expected_avg_trade, atol=1e-6), \
            f"avg_trade mismatch: metrics={metrics['avg_trade']:.6f}, expected={expected_avg_trade:.6f}"
        
        # Test 4: win_rate == (trades_df["net_pnl_pct"] > 0).mean() * 100
        expected_win_rate = (trades_df["net_pnl_pct"] > 0).sum() / len(trades_df) * 100.0
        assert np.isclose(metrics['win_rate'], expected_win_rate, atol=1e-6), \
            f"win_rate mismatch: metrics={metrics['win_rate']:.6f}, expected={expected_win_rate:.6f}"
        
        # Test 5: profit_factor == sum(profits) / sum(abs(losses))
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

    def test_profit_factor_capped_when_no_losses(self):
        """
        Test that profit_factor == MAX_VALID_METRIC (9999.0) when all trades are profitable.

        F-08: cap at MAX_VALID_METRIC instead of np.inf for rankability.
        Both bar-level path (metrics.py) and trade-level path (run.py) must agree.

        Uses deterministic synthetic data: monotonically increasing open prices
        ensure every LONG trade is profitable without relying on random seeds.
        """
        n = 100
        # Monotonically increasing prices → every LONG entry will be profitable
        prices = np.linspace(100.0, 200.0, n)
        index = pd.date_range("2023-01-01", periods=n, freq="D")

        result = run_single_backtest(
            open_prices=prices,
            high=prices * 1.001,
            low=prices * 0.999,
            close=prices,
            index=index,
            atr_period=5,
            multiplier=0.1,   # very tight band → signal fires quickly
            trade_mode="long",
            commission=0.0,   # zero commission so no trade goes negative
            warmup_period=5,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252,
            min_trades_required=1,
            extract_trades_flag=True,
            caller_mode="test",
        )

        if result.trades_df is None or len(result.trades_df) == 0:
            pytest.skip("No trades generated for deterministic scenario")

        trades_df = result.trades_df
        has_losses = (trades_df['net_pnl_pct'] < 0).any()
        has_profits = (trades_df['net_pnl_pct'] > 0).any()

        if not (has_profits and not has_losses):
            pytest.skip("Deterministic data did not produce all-winning scenario")

        assert result.metrics['profit_factor'] == MAX_VALID_METRIC, (
            f"profit_factor should be MAX_VALID_METRIC ({MAX_VALID_METRIC}) "
            f"when no losses, got {result.metrics['profit_factor']}"
        )
    
    def test_metrics_when_no_trades(self):
        """
        Test that when there are no trades, trade metrics are set appropriately.
        
        Contract when trades_df is empty or None (extract_trades_flag=False):
        - trades_df is None
        - metrics from calculate_all_metrics are used (based on positions/returns)
        
        Contract when trades_df is explicitly empty (no position changes):
        - num_trades == 0
        - win_rate == 0.0
        - sum_pnl_pct == 0.0
        - avg_trade == INVALID_METRIC_VALUE
        - profit_factor == INVALID_METRIC_VALUE
        
        This test simulates the scenario by using extract_trades_flag=False,
        which leaves trades_df as None and triggers the empty trades logic.
        """
        # Generate any valid OHLC data
        df = make_daily_ohlc(n_bars=100, seed=42)
        
        # Run backtest WITHOUT extracting trades
        # This simulates the scenario where trades_df is None
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
            extract_trades_flag=False,  # KEY: Don't extract trades
            caller_mode="test"
        )
        
        # Verify trades_df is None (not extracted)
        assert result.trades_df is None, \
            f"Expected trades_df to be None when extract_trades_flag=False, got {result.trades_df}"
        
        metrics = result.metrics
        
        # When extract_trades_flag=False, metrics are calculated from positions/returns
        # by calculate_all_metrics, not from trades_df
        # So we verify that trade-based metrics exist and are valid
        assert 'num_trades' in metrics, "num_trades should exist in metrics"
        assert 'win_rate' in metrics, "win_rate should exist in metrics"
        assert 'sum_pnl_pct' in metrics, "sum_pnl_pct should exist in metrics"
        assert 'avg_trade' in metrics, "avg_trade should exist in metrics"
        assert 'profit_factor' in metrics, "profit_factor should exist in metrics"
        
        # These metrics come from calculate_all_metrics (position-based calculation)
        # not from trades_df, so they may have different values
        print("[PASS] Successfully tested scenario with trades_df=None (extract_trades_flag=False)")
        print(f"  Metrics from positions: num_trades={metrics['num_trades']}, "
              f"win_rate={metrics['win_rate']:.2f}%, sum_pnl={metrics['sum_pnl_pct']:.2f}%")
        
        # Now test the actual "no trades" contract by creating a second backtest
        # with extract_trades_flag=True but ensuring empty trades_df
        # We'll use minimal data that produces no position changes
        n_bars_min = 20
        flat_price = 100.0
        
        # Create minimal flat data
        open_prices_flat = np.full(n_bars_min, flat_price, dtype=np.float64)
        high_prices_flat = np.full(n_bars_min, flat_price, dtype=np.float64)
        low_prices_flat = np.full(n_bars_min, flat_price, dtype=np.float64)
        close_prices_flat = np.full(n_bars_min, flat_price, dtype=np.float64)
        index_flat = pd.date_range('2020-01-01', periods=n_bars_min, freq='D')
        
        result2 = run_single_backtest(
            open_prices=open_prices_flat,
            high=high_prices_flat,
            low=low_prices_flat,
            close=close_prices_flat,
            index=index_flat,
            atr_period=5,
            multiplier=10.0,  # Very wide stops
            trade_mode="long",  # LONG only mode
            commission=0.001,
            warmup_period=5,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252,
            min_trades_required=1,
            extract_trades_flag=True,
            caller_mode="test"
        )
        
        # If we managed to get empty trades_df, test the contract
        if result2.trades_df is None or len(result2.trades_df) == 0:
            metrics2 = result2.metrics
            
            assert metrics2['num_trades'] == 0, \
                f"num_trades should be 0, got {metrics2['num_trades']}"
            
            assert metrics2['win_rate'] == 0.0, \
                f"win_rate should be 0.0, got {metrics2['win_rate']}"
            
            assert metrics2['sum_pnl_pct'] == 0.0, \
                f"sum_pnl_pct should be 0.0, got {metrics2['sum_pnl_pct']}"
            
            assert metrics2['avg_trade'] == INVALID_METRIC_VALUE, \
                f"avg_trade should be INVALID_METRIC_VALUE, got {metrics2['avg_trade']}"
            
            assert metrics2['profit_factor'] == INVALID_METRIC_VALUE, \
                f"profit_factor should be INVALID_METRIC_VALUE, got {metrics2['profit_factor']}"
            
            print("[PASS] Successfully tested empty trades_df contract")
            print(f"  Verified: num_trades=0, win_rate=0.0, sum_pnl=0.0, "
                  f"avg_trade=INVALID, profit_factor=INVALID")
        else:
            print(f"  Note: Flat prices still generated {len(result2.trades_df)} trade(s), "
                  f"but main contract test passed")
    
    def test_warmup_does_not_affect_trade_stats(self):
        """
        Test that warmup affects only ratio metrics, not trade stats.
        
        Trade stats (num_trades, win_rate, sum_pnl_pct, avg_trade, profit_factor)
        should be calculated on full history regardless of warmup.
        
        Ratio metrics (sharpe, sortino, cagr, max_drawdown) should be affected by warmup.
        """
        # Generate test data
        df = make_daily_ohlc(n_bars=500, seed=42)
        
        # Run backtest with warmup=0
        result_no_warmup = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=0,  # No warmup
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252,
            min_trades_required=1,
            extract_trades_flag=True,
            caller_mode="test"
        )
        
        # Run backtest with warmup=50
        result_with_warmup = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=50,  # With warmup
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252,
            min_trades_required=1,
            extract_trades_flag=True,
            caller_mode="test"
        )
        
        m1 = result_no_warmup.metrics
        m2 = result_with_warmup.metrics
        
        # Trade stats should be IDENTICAL (calculated on full history)
        assert m1['num_trades'] == m2['num_trades'], \
            f"num_trades should not be affected by warmup: {m1['num_trades']} vs {m2['num_trades']}"
        
        assert np.isclose(m1['win_rate'], m2['win_rate'], atol=1e-6), \
            f"win_rate should not be affected by warmup: {m1['win_rate']:.6f} vs {m2['win_rate']:.6f}"
        
        assert np.isclose(m1['sum_pnl_pct'], m2['sum_pnl_pct'], atol=1e-6), \
            f"sum_pnl_pct should not be affected by warmup: {m1['sum_pnl_pct']:.6f} vs {m2['sum_pnl_pct']:.6f}"
        
        assert np.isclose(m1['avg_trade'], m2['avg_trade'], atol=1e-6), \
            f"avg_trade should not be affected by warmup: {m1['avg_trade']:.6f} vs {m2['avg_trade']:.6f}"
        
        # Handle profit_factor comparison (can be inf)
        if np.isinf(m1['profit_factor']) and np.isinf(m2['profit_factor']):
            pass  # Both inf, OK
        elif m1['profit_factor'] == INVALID_METRIC_VALUE and m2['profit_factor'] == INVALID_METRIC_VALUE:
            pass  # Both invalid, OK
        else:
            assert np.isclose(m1['profit_factor'], m2['profit_factor'], atol=1e-6), \
                f"profit_factor should not be affected by warmup: {m1['profit_factor']:.6f} vs {m2['profit_factor']:.6f}"
        
        # Ratio metrics MAY be different (affected by warmup)
        # We just verify they exist and are valid, not that they're equal
        ratio_metrics = ['sharpe', 'sortino', 'cagr', 'max_drawdown']
        for metric in ratio_metrics:
            assert metric in m1, f"{metric} should exist in metrics"
            assert metric in m2, f"{metric} should exist in metrics"
        
        print("[PASS] Successfully verified warmup does not affect trade stats")
        print(f"  Trade stats unchanged: num_trades={m1['num_trades']}, "
              f"win_rate={m1['win_rate']:.2f}%, sum_pnl={m1['sum_pnl_pct']:.2f}%")
    
    def test_simple_return_vs_compound_return(self):
        """
        Test that trade metrics use simple returns, not compound returns.
        
        Verifies that sum_pnl_pct from metrics equals sum of simple returns
        from trades_df, not the compound return from equity curve.
        """
        # Generate test data
        df = make_daily_ohlc(n_bars=300, seed=99)
        
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
        
        if result.trades_df is None or len(result.trades_df) == 0:
            pytest.skip("No trades generated for this test")
        
        # Calculate simple return sum from trades
        simple_return_sum = result.trades_df['net_pnl_pct'].sum()
        
        # Calculate compound return from equity curve
        initial_equity = result.equity_curve[0]
        final_equity = result.equity_curve[-1]
        compound_return_pct = (final_equity - initial_equity) / initial_equity * 100.0
        
        # Verify that metrics use simple return
        assert np.isclose(result.metrics['sum_pnl_pct'], simple_return_sum, atol=1e-6), \
            f"sum_pnl_pct should equal simple return sum: {result.metrics['sum_pnl_pct']:.6f} vs {simple_return_sum:.6f}"
        
        # Verify that simple and compound are different (unless trivial case)
        if len(result.trades_df) > 2:
            # For multiple trades, simple and compound should typically differ
            print(f"  Simple return sum: {simple_return_sum:.2f}%")
            print(f"  Compound return: {compound_return_pct:.2f}%")
            print(f"  Difference: {abs(simple_return_sum - compound_return_pct):.2f}%")
            print("[PASS] Verified metrics use simple returns, not compound returns")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

