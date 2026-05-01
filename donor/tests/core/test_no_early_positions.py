"""
Tests to verify that positions do not open before ATR period stabilization.

This prevents premature entries when SuperTrend is not yet stable.
"""

import pytest
import numpy as np
import pandas as pd
from tests.fixtures.data_generator import make_intraday_ohlc
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel


class TestNoEarlyPositions:
    """Tests to ensure no positions open before atr_period."""
    
    def test_no_positions_before_atr_period_open_to_open(self):
        """
        Test that positions array has zeros before atr_period (OPEN_TO_OPEN).
        
        For OPEN_TO_OPEN with 1-bar lag:
        - positions[0] = 0 (always)
        - positions[1:atr_period] should be 0 (no trend before stabilization)
        - positions[atr_period] can be non-zero (first possible entry)
        """
        # Generate intraday data (enough bars for testing)
        atr_period = 14
        n_bars = 100
        df = make_intraday_ohlc(n=n_bars, seed=42, start_datetime="2023-01-01 09:30:00", freq="1h")
        
        # Run backtest in long mode with OPEN_TO_OPEN
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=atr_period,
            multiplier=3.0,
            trade_mode="long",
            commission=0.001,
            warmup_period=0,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252,
            min_trades_required=1,
            extract_trades_flag=False,
            caller_mode="test",
            execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        
        # Verify: positions[0:atr_period] should all be 0
        positions = result.positions
        assert len(positions) == n_bars, f"Expected {n_bars} positions, got {len(positions)}"
        
        # Check that all positions before atr_period are 0
        early_positions = positions[:atr_period]
        assert np.all(early_positions == 0), (
            f"Found non-zero positions before atr_period={atr_period}. "
            f"positions[0:{atr_period}] = {early_positions}"
        )
        
        # Verify that positions CAN be non-zero starting from atr_period
        # (not required, but if there's a long signal, it should appear)
        # This is just a sanity check that the fix didn't break everything
        assert len(positions) > atr_period, "Not enough bars to check post-atr_period behavior"
    
    def test_no_trades_before_atr_period_open_to_open(self):
        """
        Test that no trades open before atr_period (OPEN_TO_OPEN).
        
        For OPEN_TO_OPEN:
        - First possible entry_index >= atr_period
        """
        # Generate intraday data
        atr_period = 14
        n_bars = 100
        df = make_intraday_ohlc(n=n_bars, seed=42, start_datetime="2023-01-01 09:30:00", freq="1h")
        
        # Run backtest in long mode with OPEN_TO_OPEN
        result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=atr_period,
            multiplier=3.0,
            trade_mode="long",
            commission=0.001,
            warmup_period=0,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252,
            min_trades_required=1,
            extract_trades_flag=True,
            caller_mode="test",
            execution_model=ExecutionModel.OPEN_TO_OPEN
        )
        
        # Check trades
        trades_df = result.trades_df
        
        if trades_df is not None and len(trades_df) > 0:
            # Verify: all entry_index >= atr_period
            min_entry_index = trades_df["entry_index"].min()
            assert min_entry_index >= atr_period, (
                f"Found trade with entry_index={min_entry_index} < atr_period={atr_period}. "
                f"First few trades:\n{trades_df[['trade_id', 'entry_index', 'entry_time']].head()}"
            )
            
            print(f"✓ All {len(trades_df)} trades have entry_index >= {atr_period}")
            print(f"  First entry_index: {min_entry_index}")
        else:
            # No trades is acceptable (depends on data/parameters)
            print("No trades generated (acceptable for this test)")
    
    def test_close_to_close_raises_value_error(self):
        """
        CLOSE_TO_CLOSE was removed due to look-ahead bias.
        Passing an unknown execution_model string must raise ValueError
        at the generate_positions / calculate_returns level.
        """
        from supertrend_optimizer.core.backtest import generate_positions

        trend = np.zeros(20, dtype=np.int8)
        trend[10:] = 1

        # Simulate passing the removed "close_to_close" string cast to the enum type
        # The enum no longer contains CLOSE_TO_CLOSE, so we pass a fake object that
        # is != OPEN_TO_OPEN to trigger the guard.
        class _FakeModel:
            value = "close_to_close"
            def __ne__(self, other): return True
            def __eq__(self, other): return False

        with pytest.raises(ValueError, match="CLOSE_TO_CLOSE"):
            generate_positions(trend, "revers", _FakeModel())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

