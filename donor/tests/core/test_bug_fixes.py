"""
Regression tests covering all bug fixes applied to core modules.

BUG-01  CLOSE_TO_CLOSE removed (look-ahead bias)
BUG-02  sum_pnl_pct bar-level vs trade-level documented
BUG-03  Commission conservation: opening + reversal split unified
BUG-04  Equity floor — equity never goes negative
BUG-05  num_trades divergence documented
BUG-06  NaN / Inf / non-positive price validation
BUG-07  supertrend_color always present in every trade row
BUG-08  Commission timing note (docstring only — no logic change)
BUG-09  win_rate returned as percent (0-100), not fraction (0-1)
BUG-10  Dead code removed from _build_trade
BUG-11  check_early_exit: ValueError on negative max_drawdown
BUG-12  calculate_final_bands: 3-branch TradingView formula
"""

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.calculator import (
    calculate_final_bands,
    calculate_supertrend,
)
from supertrend_optimizer.core.backtest import (
    generate_positions,
    calculate_returns,
    calculate_equity_curve,
    check_early_exit,
    run_backtest_fast,
)
from supertrend_optimizer.core.metrics import (
    calculate_trade_stats_from_positions,
    calculate_all_metrics,
    calculate_sum_pnl_percent,
)
from supertrend_optimizer.core.trades import extract_trades
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE, MAX_VALID_METRIC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prices(n: int = 30, start: float = 100.0, step: float = 0.5) -> np.ndarray:
    """Monotonically increasing prices — useful for deterministic trend."""
    return np.array([start + i * step for i in range(n)], dtype=np.float64)


def _flat_prices(n: int = 20, price: float = 100.0) -> np.ndarray:
    return np.full(n, price, dtype=np.float64)


# ===========================================================================
# BUG-01: CLOSE_TO_CLOSE removed
# ===========================================================================

class TestBug01CloseToCloseRemoved:

    def test_enum_has_only_open_to_open(self):
        """ExecutionModel must contain only OPEN_TO_OPEN."""
        members = [m.value for m in ExecutionModel]
        assert members == ["open_to_open"], (
            f"Unexpected ExecutionModel members: {members}. "
            "CLOSE_TO_CLOSE must be absent."
        )

    def test_generate_positions_rejects_unknown_model(self):
        """generate_positions must raise ValueError for non-OPEN_TO_OPEN."""
        trend = np.array([0, 0, 1, 1, -1], dtype=np.int8)

        class _Fake:
            value = "close_to_close"
            def __ne__(self, other): return True
            def __eq__(self, other): return False

        with pytest.raises(ValueError, match="CLOSE_TO_CLOSE"):
            generate_positions(trend, "revers", _Fake())

    def test_calculate_returns_rejects_unknown_model(self):
        """calculate_returns must raise ValueError for non-OPEN_TO_OPEN."""
        prices = _make_prices(5)
        positions = np.array([0, 1, 1, 0, 0], dtype=np.int8)

        class _Fake:
            value = "close_to_close"
            def __ne__(self, other): return True
            def __eq__(self, other): return False

        with pytest.raises(ValueError, match="CLOSE_TO_CLOSE"):
            calculate_returns(prices, positions, 0.001, _Fake())

    def test_extract_trades_rejects_close_to_close_string(self):
        """extract_trades must raise ValueError when model='close_to_close'."""
        positions = np.array([0, 1, 1, 0], dtype=np.int8)
        returns = np.array([0.0, 0.01, 0.0], dtype=np.float64)
        prices = np.array([100.0, 100.0, 101.0, 101.0])
        index = pd.RangeIndex(4)

        with pytest.raises(ValueError, match="CLOSE_TO_CLOSE"):
            extract_trades(
                positions, returns, prices, index,
                commission_rate=0.001,
                execution_model="close_to_close",
            )


# ===========================================================================
# BUG-04: Equity floor
# ===========================================================================

class TestBug04EquityFloor:

    def test_equity_never_negative(self):
        """Return of -1.5 must not produce negative equity."""
        returns = np.array([0.05, -1.5, 0.02])
        equity = calculate_equity_curve(returns)
        assert np.all(equity > 0), f"Equity has non-positive values: {equity}"

    def test_equity_floor_value(self):
        """After a total-loss bar equity must be at least 1e-10."""
        returns = np.array([-1.0, 0.0])  # equity → 0 exactly
        equity = calculate_equity_curve(returns)
        assert equity[1] >= 1e-10

    def test_equity_normal_unaffected(self):
        """Normal returns must not be clamped."""
        returns = np.array([0.01, -0.005, 0.02, -0.01])
        equity = calculate_equity_curve(returns)
        expected = np.cumprod(1.0 + returns)
        np.testing.assert_allclose(equity[1:], expected, rtol=1e-12)

    def test_equity_length_preserved(self):
        """len(equity) == len(returns) + 1 even when floor is applied."""
        returns = np.array([0.1, -2.0, 0.1])
        equity = calculate_equity_curve(returns)
        assert len(equity) == len(returns) + 1


# ===========================================================================
# BUG-06: NaN / Inf / non-positive price validation
# ===========================================================================

class TestBug06PriceValidation:

    def test_nan_in_close_raises(self):
        n = 10
        high = _make_prices(n)
        low = high - 1
        close = high.copy()
        close[5] = np.nan
        with pytest.raises(ValueError, match="NaN or Inf"):
            calculate_supertrend(high, low, close, atr_period=3, multiplier=2.0)

    def test_inf_in_high_raises(self):
        n = 10
        high = _make_prices(n)
        high[3] = np.inf
        low = high - 1
        close = high - 0.5
        with pytest.raises(ValueError, match="NaN or Inf"):
            calculate_supertrend(high, low, close, atr_period=3, multiplier=2.0)

    def test_zero_price_raises(self):
        n = 10
        high = _make_prices(n)
        low = high - 1
        close = high.copy()
        close[4] = 0.0
        with pytest.raises(ValueError, match="non-positive"):
            calculate_supertrend(high, low, close, atr_period=3, multiplier=2.0)

    def test_negative_price_raises(self):
        n = 10
        high = _make_prices(n)
        low = high - 1
        low[2] = -1.0
        close = high - 0.5
        with pytest.raises(ValueError, match="non-positive"):
            calculate_supertrend(high, low, close, atr_period=3, multiplier=2.0)

    def test_nan_open_prices_raises_in_run_backtest_fast(self):
        n = 20
        close = _make_prices(n)
        high = close + 1
        low = close - 1
        open_p = close.copy()
        open_p[7] = np.nan
        with pytest.raises(ValueError, match="NaN or Inf"):
            run_backtest_fast(
                open_prices=open_p, high=high, low=low, close=close,
                atr_period=3, multiplier=2.0, trade_mode="revers",
                commission=0.001, early_exit_enabled=False,
                early_exit_max_drawdown=0.5, early_exit_check_bars=0,
            )

    def test_valid_prices_pass_through(self):
        """Verify that valid prices do NOT raise."""
        n = 20
        close = _make_prices(n)
        high = close + 0.5
        low = close - 0.5
        open_p = close - 0.1
        # Should not raise
        calculate_supertrend(high, low, close, atr_period=3, multiplier=2.0)


# ===========================================================================
# BUG-07: supertrend_color always present
# ===========================================================================

class TestBug07SupertrendColorAlwaysPresent:

    def _run_extract(self, positions, returns, prices, trend=None):
        index = pd.RangeIndex(len(positions))
        return extract_trades(
            positions=positions,
            returns=returns,
            execution_prices=prices,
            index=index,
            commission_rate=0.001,
            trend=trend,
            execution_model="open_to_open",
        )

    def test_color_present_when_trend_provided(self):
        """Every trade row must have supertrend_color when trend is given."""
        positions = np.array([0, 1, 1, 1, -1, -1, 0], dtype=np.int8)
        returns = np.zeros(6)
        prices = np.full(7, 100.0)
        trend = np.array([0, 0, 1, 1, -1, -1, -1], dtype=np.int8)

        df = self._run_extract(positions, returns, prices, trend=trend)
        assert len(df) > 0
        assert 'supertrend_color' in df.columns
        assert not df['supertrend_color'].isna().any()

    def test_color_unknown_when_trend_is_none(self):
        """When trend=None every trade must have 'UNKNOWN'."""
        positions = np.array([0, 1, 1, 0], dtype=np.int8)
        returns = np.zeros(3)
        prices = np.full(4, 100.0)

        df = self._run_extract(positions, returns, prices, trend=None)
        assert len(df) > 0
        assert all(df['supertrend_color'] == 'UNKNOWN')

    def test_color_unknown_for_first_trade_entry_idx_0(self):
        """
        Edge case: if entry_idx == 0, signal_idx = -1 → UNKNOWN.
        (Positions[0] != 0 forces an entry at index 0.)
        """
        # Directly call _build_trade via extract_trades with positions[0]=1
        positions = np.array([1, 1, 0], dtype=np.int8)
        returns = np.zeros(2)
        prices = np.array([100.0, 101.0, 101.0])
        trend = np.array([1, 1, 1], dtype=np.int8)

        df = self._run_extract(positions, returns, prices, trend=trend)
        assert len(df) > 0
        # entry_idx == 0 → signal_idx == -1 → 'UNKNOWN'
        assert df.iloc[0]['supertrend_color'] == 'UNKNOWN'


# ===========================================================================
# BUG-09: win_rate as percent (0-100)
# ===========================================================================

class TestBug09WinRatePercent:

    def test_win_rate_all_winning(self):
        """All profitable bars → win_rate == 100.0."""
        positions = np.array([0, 1, 1, 1, 0], dtype=np.int8)
        returns = np.array([0.0, 0.01, 0.02, 0.0])
        num_trades, win_rate = calculate_trade_stats_from_positions(positions, returns)
        assert num_trades == 1
        assert win_rate == pytest.approx(100.0)

    def test_win_rate_no_winning(self):
        """All losing bars → win_rate == 0.0."""
        positions = np.array([0, 1, 1, 0], dtype=np.int8)
        returns = np.array([0.0, -0.01, -0.02])
        num_trades, win_rate = calculate_trade_stats_from_positions(positions, returns)
        assert num_trades == 1
        assert win_rate == pytest.approx(0.0)

    def test_win_rate_half(self):
        """Two trades, one profitable → win_rate == 50.0."""
        # trade 1: bars [1,2) → returns[1] = 0.01 (win)
        # trade 2: bars [3,4) → returns[3] = -0.01 (loss)
        positions = np.array([0, 1, 0, -1, 0], dtype=np.int8)
        returns = np.array([0.0, 0.01, 0.0, -0.01])
        num_trades, win_rate = calculate_trade_stats_from_positions(positions, returns)
        assert num_trades == 2
        assert win_rate == pytest.approx(50.0)

    def test_win_rate_is_in_0_100_range(self):
        """win_rate must always be in [0, 100] or INVALID_METRIC_VALUE."""
        positions = np.array([0, 1, 1, -1, -1, 0], dtype=np.int8)
        returns = np.array([0.0, 0.01, -0.005, 0.02, 0.0])
        _, win_rate = calculate_trade_stats_from_positions(positions, returns)
        if win_rate != INVALID_METRIC_VALUE:
            assert 0.0 <= win_rate <= 100.0

    def test_win_rate_no_trades(self):
        """No trades → win_rate == INVALID_METRIC_VALUE."""
        positions = np.zeros(5, dtype=np.int8)
        returns = np.zeros(4)
        _, win_rate = calculate_trade_stats_from_positions(positions, returns)
        assert win_rate == INVALID_METRIC_VALUE


# ===========================================================================
# BUG-11: check_early_exit raises on negative max_drawdown
# ===========================================================================

class TestBug11EarlyExitValidation:

    def test_negative_max_drawdown_raises(self):
        equity = np.array([1.0, 0.9, 0.8])
        with pytest.raises(ValueError, match="max_drawdown must be >= 0"):
            check_early_exit(equity, max_drawdown=-0.1, check_bars=3)

    def test_zero_max_drawdown_never_fires_on_bar0(self):
        """Bar 0 always has dd=0 → threshold 0 never fires on bar 0."""
        equity = np.array([1.0, 0.99, 0.98])
        is_exit, bar, _ = check_early_exit(equity, max_drawdown=0.0, check_bars=3)
        assert is_exit
        # Bar 0 cannot be exit_bar because dd[0] = 0 and 0 < -0 is False
        assert bar != 0

    def test_positive_max_drawdown_works(self):
        equity = np.array([1.0, 1.1, 0.5])  # dd at bar 2 ≈ -0.545
        is_exit, bar, dd = check_early_exit(equity, max_drawdown=0.5, check_bars=3)
        assert is_exit
        assert bar == 2
        assert dd < -0.5


# ===========================================================================
# BUG-12: calculate_final_bands — 3-branch TradingView formula
# ===========================================================================

class TestBug12FinalBands:

    def test_upper_band_clamp_when_close_breaks_above(self):
        """
        When close[i-1] > upper_final[i-1] (breakout above upper band),
        the new upper_final must be max(upper_basic[i], upper_final[i-1]).

        Scenario (i=2):
            upper_basic = [110, 108, 105]
            close        = [100, 115, 106]   ← close[1]=115 > upper_final[1]=108
            upper_final[2] should be max(105, 108) = 108   (TradingView)
                          NOT 105 (old broken two-branch logic)
        """
        upper_basic = np.array([110.0, 108.0, 105.0])
        lower_basic = np.array([90.0,  92.0,  95.0])
        close       = np.array([100.0, 115.0, 106.0])

        upper_final, _ = calculate_final_bands(upper_basic, lower_basic, close)

        assert upper_final[0] == pytest.approx(110.0)
        assert upper_final[1] == pytest.approx(108.0)
        # TradingView: max(105, 108) = 108  (NOT 105)
        assert upper_final[2] == pytest.approx(108.0), (
            "upper_final[2] should be max(upper_basic, prev_upper_final)=108, "
            f"got {upper_final[2]}"
        )

    def test_lower_band_clamp_when_close_breaks_below(self):
        """
        When close[i-1] < lower_final[i-1] (breakout below lower band),
        the new lower_final must be min(lower_basic[i], lower_final[i-1]).
        """
        upper_basic = np.array([110.0, 108.0, 106.0])
        lower_basic = np.array([90.0,  92.0,  94.0])
        close       = np.array([100.0, 85.0,  93.0])   # close[1]=85 < lower_final[1]=90

        _, lower_final = calculate_final_bands(upper_basic, lower_basic, close)

        assert lower_final[0] == pytest.approx(90.0)
        assert lower_final[1] == pytest.approx(92.0)
        # TradingView: min(94, 92) = 92  (band must NOT rise after breakdown)
        assert lower_final[2] == pytest.approx(92.0), (
            "lower_final[2] should be min(lower_basic, prev_lower_final)=92, "
            f"got {lower_final[2]}"
        )

    def test_upper_band_tightens_when_basic_below_prev(self):
        """Branch B: basic < prev → take basic (normal tightening)."""
        upper_basic = np.array([110.0, 108.0, 106.0])
        lower_basic = np.array([90.0,  92.0,  94.0])
        close       = np.array([100.0, 100.0, 100.0])  # no breakout

        upper_final, _ = calculate_final_bands(upper_basic, lower_basic, close)

        # Each step tightens (basic < prev): should follow basic
        assert upper_final[0] == pytest.approx(110.0)
        assert upper_final[1] == pytest.approx(108.0)
        assert upper_final[2] == pytest.approx(106.0)

    def test_upper_band_holds_when_basic_above_prev(self):
        """Branch C: basic >= prev and no breakout → keep previous."""
        upper_basic = np.array([100.0, 105.0, 107.0])
        lower_basic = np.array([80.0,  82.0,  84.0])
        close       = np.array([90.0,  90.0,  90.0])

        upper_final, _ = calculate_final_bands(upper_basic, lower_basic, close)

        assert upper_final[0] == pytest.approx(100.0)
        assert upper_final[1] == pytest.approx(100.0)  # 105 > 100 → keep 100
        assert upper_final[2] == pytest.approx(100.0)  # 107 > 100 → keep 100

    def test_lower_band_holds_when_basic_below_prev(self):
        """Branch C (lower): basic <= prev and no breakdown → keep previous."""
        upper_basic = np.array([120.0, 122.0, 124.0])
        lower_basic = np.array([90.0,  88.0,  86.0])
        close       = np.array([100.0, 100.0, 100.0])

        _, lower_final = calculate_final_bands(upper_basic, lower_basic, close)

        assert lower_final[0] == pytest.approx(90.0)
        assert lower_final[1] == pytest.approx(90.0)  # 88 < 90 → keep 90
        assert lower_final[2] == pytest.approx(90.0)  # 86 < 90 → keep 90


# ===========================================================================
# BUG-03: Commission conservation
# ===========================================================================

class TestBug03CommissionConservation:

    def _bar_level_total_comm(self, positions, commission_rate):
        """Compute total commission the same way calculate_returns does."""
        n = len(positions)
        returns_len = n - 1
        pos_changes = np.diff(positions[:returns_len + 1])
        return float(np.sum(np.abs(pos_changes)) * commission_rate * 100.0)

    def test_simple_long_trade_conservation(self):
        """flat → long → flat: trade commission == bar-level commission."""
        positions = np.array([0, 1, 1, 1, 0], dtype=np.int8)
        returns = np.zeros(4)
        prices = np.full(5, 100.0)
        index = pd.RangeIndex(5)
        commission_rate = 0.001

        df = extract_trades(
            positions, returns, prices, index, commission_rate,
            execution_model="open_to_open",
        )
        expected = self._bar_level_total_comm(positions, commission_rate)
        actual = df['commission_pct'].sum()
        assert abs(actual - expected) < 1e-4, (
            f"Commission mismatch: trades={actual:.6f}%, bars={expected:.6f}%"
        )

    def test_reversal_commission_conservation(self):
        """long → short reversal: total commission conserved across both trades."""
        positions = np.array([0, 1, 1, -1, -1, 0], dtype=np.int8)
        returns = np.zeros(5)
        prices = np.full(6, 100.0)
        index = pd.RangeIndex(6)
        commission_rate = 0.001

        df = extract_trades(
            positions, returns, prices, index, commission_rate,
            execution_model="open_to_open",
        )
        assert len(df) == 2
        expected = self._bar_level_total_comm(positions, commission_rate)
        actual = df['commission_pct'].sum()
        assert abs(actual - expected) < 1e-4

    def test_reversal_split_equal(self):
        """
        Reversal [0, 1, -1, 0]: commission breakdown across two trades.

        Bar-level commissions:
          bar 0: |1-0|  * rate = 1 * 0.001  → 0.1%  (entry of trade 1, from flat)
          bar 1: |-1-1| * rate = 2 * 0.001  → 0.2%  (reversal bar, in trade 1 interval)
          bar 2: |0-(-1)| * rate = 1 * 0.001 → 0.1% (exit of trade 2)

        After unified attribution:
          Trade 1 [entry=1, exit=2):
            _build_trade raw: commission_per_bar[1:2] = 0.002 → 0.2%
            Adj-1 (from flat): commission_per_bar[0] = 0.001 → +0.1% → total 0.3%
            F-16 reversal split: −0.1% → final 0.2%
          Trade 2 [entry=2, exit=3):
            _build_trade raw: commission_per_bar[2:3] = 0.001 → 0.1%
            F-16 reversal split: +0.1% → final 0.2%

          Total: 0.2 + 0.2 = 0.4% == bar total (0.1 + 0.2 + 0.1) * 100 = 0.4%
        """
        positions = np.array([0, 1, -1, 0], dtype=np.int8)
        returns = np.zeros(3)
        prices = np.full(4, 100.0)
        index = pd.RangeIndex(4)
        commission_rate = 0.001

        df = extract_trades(
            positions, returns, prices, index, commission_rate,
            execution_model="open_to_open",
        )
        assert len(df) == 2
        # Conservation: total commission == bar-level total
        bar_total = (1 + 2 + 1) * commission_rate * 100.0  # 0.4%
        assert df['commission_pct'].sum() == pytest.approx(bar_total, abs=1e-5)
        # Each trade gets 0.2% (0.4% / 2, symmetric split)
        assert df.iloc[0]['commission_pct'] == pytest.approx(0.2, abs=1e-5)
        assert df.iloc[1]['commission_pct'] == pytest.approx(0.2, abs=1e-5)

    def test_flat_to_long_to_reversal_conservation(self):
        """flat → long → reversal → short: total commission conserved."""
        positions = np.array([0, 0, 1, 1, -1, -1, 0], dtype=np.int8)
        returns = np.zeros(6)
        prices = np.full(7, 100.0)
        index = pd.RangeIndex(7)
        commission_rate = 0.0015

        df = extract_trades(
            positions, returns, prices, index, commission_rate,
            execution_model="open_to_open",
        )
        expected = self._bar_level_total_comm(positions, commission_rate)
        actual = df['commission_pct'].sum()
        assert abs(actual - expected) < 1e-4


# ===========================================================================
# OPEN_TO_OPEN lag invariant (regression)
# ===========================================================================

class TestOpenToOpenLag:

    def test_positions_1bar_lag(self):
        """positions[t+1] == trend[t] for every t (OPEN_TO_OPEN)."""
        trend = np.array([0, 0, 1, 1, -1, -1, 1], dtype=np.int8)
        positions = generate_positions(trend, "revers", ExecutionModel.OPEN_TO_OPEN)
        assert positions[0] == 0
        np.testing.assert_array_equal(positions[1:], trend[:-1])

    def test_no_future_leak_at_signal_bar(self):
        """Position on bar t must reflect trend[t-1], not trend[t]."""
        trend = np.array([0, 0, 0, 1, -1, 1], dtype=np.int8)
        positions = generate_positions(trend, "revers", ExecutionModel.OPEN_TO_OPEN)
        # positions[3] = trend[2] = 0, NOT trend[3] = 1
        assert positions[3] == 0
        # positions[4] = trend[3] = 1, NOT trend[4] = -1
        assert positions[4] == 1


# ===========================================================================
# Equity length invariant
# ===========================================================================

class TestEquityLengthInvariant:

    def test_normal_flow(self):
        returns = np.array([0.01, -0.005, 0.02])
        equity = calculate_equity_curve(returns)
        assert len(equity) == len(returns) + 1
        assert equity[0] == pytest.approx(1.0)

    def test_empty_returns(self):
        returns = np.array([], dtype=np.float64)
        equity = calculate_equity_curve(returns)
        assert len(equity) == 1
        assert equity[0] == pytest.approx(1.0)

    def test_after_early_exit_truncation(self):
        """Invariant len(equity) == len(returns) + 1 holds after truncation."""
        returns_full = np.array([0.01, -0.8, 0.01, 0.01, 0.01])
        equity_full = calculate_equity_curve(returns_full)
        is_exit, bar, _ = check_early_exit(equity_full, 0.5, len(equity_full))
        assert is_exit and bar is not None
        returns_trunc = returns_full[:bar]
        equity_trunc = equity_full[:bar + 1]
        assert len(equity_trunc) == len(returns_trunc) + 1


# ===========================================================================
# ATR initialisation regression
# ===========================================================================

class TestATRInitialisation:

    def test_seed_equals_mean_of_first_period_tr(self):
        from supertrend_optimizer.core.calculator import calculate_atr_rma, calculate_true_range
        n, period = 20, 5
        close = _make_prices(n)
        high  = close + 1.0
        low   = close - 1.0
        tr  = calculate_true_range(high, low, close)
        atr = calculate_atr_rma(tr, period)
        assert atr[period - 1] == pytest.approx(np.mean(tr[:period]))

    def test_backfill_equals_seed(self):
        from supertrend_optimizer.core.calculator import calculate_atr_rma
        tr = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
        period = 3
        atr = calculate_atr_rma(tr, period)
        seed = atr[period - 1]
        for i in range(period - 1):
            assert atr[i] == pytest.approx(seed)
