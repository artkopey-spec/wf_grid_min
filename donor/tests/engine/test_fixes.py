"""
Regression tests for engine fixes (FIX-1 through FIX-13).

Covers:
- FIX-2: profit_factor is MAX_VALID_METRIC, not np.inf, when no losses
- FIX-3: warmup_time + non-DatetimeIndex raises ValueError
- FIX-5: invalid parameter values raise ValueError
- FIX-8: early_exit_enabled=True with check_bars=0 warns, never triggers
- FIX-9: trades_df schema validation (missing column, NaN rows)
- FIX-10: BacktestResult.__post_init__ validates invariants
- early_exit array invariants and trades scope
"""

import warnings

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.engine.result import BacktestResult
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE, MAX_VALID_METRIC
from tests.fixtures.data_generator import make_daily_ohlc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal(n: int = 100, seed: int = 42) -> dict:
    """Return keyword args for run_single_backtest with n daily bars."""
    df = make_daily_ohlc(n_bars=n, seed=seed)
    return dict(
        open_prices=df["open"].values,
        high=df["high"].values,
        low=df["low"].values,
        close=df["close"].values,
        index=df.index,
        atr_period=10,
        multiplier=2.0,
        trade_mode="revers",
        commission=0.001,
        warmup_period=10,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        periods_per_year=252.0,
        min_trades_required=1,
        extract_trades_flag=True,
        caller_mode="test",
    )


def _make_monotone(n: int = 80) -> dict:
    """Return keyword args for a monotonically increasing price series.

    Guarantees: every LONG trade is profitable (no losses possible).
    """
    prices = np.linspace(100.0, 200.0, n)
    index = pd.date_range("2023-01-01", periods=n, freq="D")
    return dict(
        open_prices=prices,
        high=prices * 1.001,
        low=prices * 0.999,
        close=prices,
        index=index,
        atr_period=5,
        multiplier=0.1,
        trade_mode="long",
        commission=0.0,
        warmup_period=5,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        periods_per_year=252.0,
        min_trades_required=1,
        extract_trades_flag=True,
        caller_mode="test",
    )


# ---------------------------------------------------------------------------
# FIX-2: profit_factor unified — MAX_VALID_METRIC, not np.inf
# ---------------------------------------------------------------------------

class TestProfitFactorUnified:
    """profit_factor must equal MAX_VALID_METRIC (9999.0) when no losses (F-08)."""

    def test_profit_factor_max_valid_metric_when_no_losses(self):
        """Deterministic all-winning scenario: profit_factor == MAX_VALID_METRIC."""
        result = run_single_backtest(**_make_monotone())

        if result.trades_df is None or len(result.trades_df) == 0:
            pytest.skip("No trades in monotone scenario")

        trades_df = result.trades_df
        has_losses = (trades_df["net_pnl_pct"] < 0).any()
        has_profits = (trades_df["net_pnl_pct"] > 0).any()

        if not (has_profits and not has_losses):
            pytest.skip("Monotone scenario did not produce all-winning trades")

        assert result.metrics["profit_factor"] == MAX_VALID_METRIC, (
            f"Expected MAX_VALID_METRIC ({MAX_VALID_METRIC}), "
            f"got {result.metrics['profit_factor']}"
        )
        assert result.metrics["profit_factor"] != np.inf, (
            "profit_factor must NOT be np.inf (breaks optimizer ranking cap)"
        )

    def test_profit_factor_extract_false_is_finite(self):
        """Bar-level path (extract_trades_flag=False) must also return finite profit_factor."""
        kwargs = _make_monotone()
        kwargs["extract_trades_flag"] = False
        result = run_single_backtest(**kwargs)
        pf = result.metrics["profit_factor"]
        # Either a normal ratio, 9999.0 or INVALID — never np.inf
        assert np.isfinite(pf) or pf == INVALID_METRIC_VALUE, (
            f"profit_factor with extract_trades_flag=False must be finite or INVALID, got {pf}"
        )


# ---------------------------------------------------------------------------
# FIX-3: warmup_time + non-DatetimeIndex → ValueError
# ---------------------------------------------------------------------------

class TestWarmupTimeNonDatetimeIndex:
    """warmup_time requires DatetimeIndex; other index types must raise ValueError."""

    def test_range_index_raises(self):
        df = make_daily_ohlc(n_bars=100, seed=1)
        kwargs = _make_minimal(n=100, seed=1)
        kwargs["index"] = pd.RangeIndex(100)  # override to non-datetime
        kwargs["warmup_period"] = None
        kwargs["warmup_time"] = "7d"

        with pytest.raises(ValueError, match="warmup_time.*DatetimeIndex"):
            run_single_backtest(**kwargs)

    def test_int64_index_raises(self):
        kwargs = _make_minimal(n=100, seed=2)
        kwargs["index"] = pd.Index(np.arange(100, dtype=np.int64))
        kwargs["warmup_period"] = None
        kwargs["warmup_time"] = "24h"

        with pytest.raises(ValueError, match="warmup_time.*DatetimeIndex"):
            run_single_backtest(**kwargs)

    def test_datetime_index_ok(self):
        """warmup_time with DatetimeIndex must NOT raise."""
        kwargs = _make_minimal(n=200, seed=3)
        kwargs["warmup_period"] = None
        kwargs["warmup_time"] = "5d"
        result = run_single_backtest(**kwargs)
        assert result.warmup >= 1


# ---------------------------------------------------------------------------
# FIX-5: Parameter validation
# ---------------------------------------------------------------------------

class TestParameterValidation:
    """Invalid parameter values must raise ValueError before any computation."""

    @pytest.mark.parametrize("atr_period", [0, 1, -5])
    def test_atr_period_too_small(self, atr_period):
        kwargs = _make_minimal()
        kwargs["atr_period"] = atr_period
        with pytest.raises(ValueError, match="atr_period"):
            run_single_backtest(**kwargs)

    @pytest.mark.parametrize("multiplier", [0.0, -1.0, -0.001])
    def test_multiplier_non_positive(self, multiplier):
        kwargs = _make_minimal()
        kwargs["multiplier"] = multiplier
        with pytest.raises(ValueError, match="multiplier"):
            run_single_backtest(**kwargs)

    @pytest.mark.parametrize("commission", [-0.001, -1.0])
    def test_commission_negative(self, commission):
        kwargs = _make_minimal()
        kwargs["commission"] = commission
        with pytest.raises(ValueError, match="commission"):
            run_single_backtest(**kwargs)

    @pytest.mark.parametrize("ppy", [0, -252, float("nan"), float("inf"), -float("inf")])
    def test_periods_per_year_invalid(self, ppy):
        kwargs = _make_minimal()
        kwargs["periods_per_year"] = ppy
        with pytest.raises(ValueError, match="periods_per_year"):
            run_single_backtest(**kwargs)

    @pytest.mark.parametrize("mtr", [-1, -10])
    def test_min_trades_required_negative(self, mtr):
        kwargs = _make_minimal()
        kwargs["min_trades_required"] = mtr
        with pytest.raises(ValueError, match="min_trades_required"):
            run_single_backtest(**kwargs)

    @pytest.mark.parametrize("ecb", [-1, -100])
    def test_early_exit_check_bars_negative(self, ecb):
        kwargs = _make_minimal()
        kwargs["early_exit_check_bars"] = ecb
        with pytest.raises(ValueError, match="early_exit_check_bars"):
            run_single_backtest(**kwargs)

    def test_too_few_bars_raises(self):
        """Single bar must raise ValueError (need >= 2 bars for 1 return)."""
        kwargs = _make_minimal(n=1)
        with pytest.raises(ValueError, match="at least 2 bars"):
            run_single_backtest(**kwargs)

    def test_empty_arrays_raises(self):
        """Zero-length arrays must raise ValueError."""
        index = pd.DatetimeIndex([])
        with pytest.raises(ValueError):
            run_single_backtest(
                open_prices=np.array([]),
                high=np.array([]),
                low=np.array([]),
                close=np.array([]),
                index=index,
                atr_period=10,
                multiplier=2.0,
                trade_mode="revers",
                commission=0.001,
                periods_per_year=252.0,
            )


# ---------------------------------------------------------------------------
# FIX-8: Warning when early_exit_enabled=True but check_bars=0
# ---------------------------------------------------------------------------

class TestEarlyExitCheckBarsZeroWarning:
    """early_exit_enabled=True + check_bars=0 must warn, and exit must never fire."""

    def test_warns_when_check_bars_zero(self):
        kwargs = _make_minimal(n=200)
        kwargs["early_exit_enabled"] = True
        kwargs["early_exit_check_bars"] = 0

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = run_single_backtest(**kwargs)

        messages = [str(w.message) for w in caught]
        assert any("early_exit_check_bars=0" in m for m in messages), (
            f"Expected warning about check_bars=0, got: {messages}"
        )

    def test_exit_never_triggers_when_check_bars_zero(self):
        kwargs = _make_minimal(n=200)
        kwargs["early_exit_enabled"] = True
        kwargs["early_exit_check_bars"] = 0
        kwargs["early_exit_max_drawdown"] = 0.01  # very tight threshold

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = run_single_backtest(**kwargs)

        assert result.early_exit is False, (
            "early_exit should be False when check_bars=0, regardless of threshold"
        )
        assert result.exit_bar is None


# ---------------------------------------------------------------------------
# FIX-9: trades_df schema validation
# ---------------------------------------------------------------------------

class TestTradesSchemaValidation:
    """Invalid or NaN-containing trades_df must be handled gracefully."""

    def test_nan_in_net_pnl_pct_triggers_warning_and_drops(self, monkeypatch):
        """If extract_trades returns NaN in net_pnl_pct, a warning is issued
        and the NaN rows are dropped before computing metrics."""
        import supertrend_optimizer.engine.run as run_module

        original_extract = run_module.extract_trades

        def patched_extract(*args, **kwargs):
            df = original_extract(*args, **kwargs)
            if df is not None and len(df) > 0:
                df = df.copy()
                df.loc[df.index[0], "net_pnl_pct"] = float("nan")
            return df

        monkeypatch.setattr(run_module, "extract_trades", patched_extract)

        kwargs = _make_minimal(n=300, seed=7)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = run_single_backtest(**kwargs)

        nan_warnings = [w for w in caught if "NaN" in str(w.message)]
        assert nan_warnings, "Expected a warning about NaN in net_pnl_pct"

        if result.trades_df is not None:
            assert not result.trades_df["net_pnl_pct"].isna().any(), (
                "NaN rows must be dropped from trades_df before metrics computation"
            )

    def test_missing_net_pnl_pct_column_raises(self, monkeypatch):
        """If extract_trades returns a DataFrame without net_pnl_pct column,
        a ValueError must be raised."""
        import supertrend_optimizer.engine.run as run_module

        original_extract = run_module.extract_trades

        def patched_extract(*args, **kwargs):
            df = original_extract(*args, **kwargs)
            if df is not None and len(df) > 0:
                df = df.drop(columns=["net_pnl_pct"])
            return df

        monkeypatch.setattr(run_module, "extract_trades", patched_extract)

        kwargs = _make_minimal(n=300, seed=8)
        with pytest.raises(ValueError, match="net_pnl_pct"):
            run_single_backtest(**kwargs)


# ---------------------------------------------------------------------------
# FIX-10: BacktestResult.__post_init__ invariant checks
# ---------------------------------------------------------------------------

class TestBacktestResultPostInit:
    """BacktestResult must reject mismatched array lengths at construction time."""

    def _valid_kwargs(self) -> dict:
        returns = np.zeros(10)
        equity = np.ones(11)
        positions = np.zeros(11, dtype=np.int8)
        trend = np.zeros(11, dtype=np.int8)
        return dict(
            atr_period=10,
            multiplier=2.0,
            trade_mode="revers",
            commission=0.001,
            warmup=0,
            returns=returns,
            equity_curve=equity,
            positions=positions,
            trend=trend,
            metrics={},
            early_exit=False,
            exit_bar=None,
            exit_drawdown=None,
            trades_df=None,
            n_bars_original=11,
        )

    def test_valid_construction_succeeds(self):
        BacktestResult(**self._valid_kwargs())

    def test_equity_positions_mismatch_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["positions"] = np.zeros(12, dtype=np.int8)  # wrong length
        with pytest.raises(ValueError, match="invariant"):
            BacktestResult(**kwargs)

    def test_equity_returns_mismatch_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["returns"] = np.zeros(9)  # should be 10 for equity len=11
        with pytest.raises(ValueError, match="invariant"):
            BacktestResult(**kwargs)

    def test_early_exit_true_without_exit_bar_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["early_exit"] = True
        kwargs["exit_bar"] = None
        with pytest.raises(ValueError, match="exit_bar"):
            BacktestResult(**kwargs)

    def test_early_exit_true_with_exit_bar_succeeds(self):
        kwargs = self._valid_kwargs()
        kwargs["early_exit"] = True
        kwargs["exit_bar"] = 5
        kwargs["exit_drawdown"] = -0.3
        BacktestResult(**kwargs)


# ---------------------------------------------------------------------------
# Early-exit array invariants (integration)
# ---------------------------------------------------------------------------

class TestEarlyExitArrayInvariants:
    """When early exit fires, arrays must be truncated and internally consistent."""

    def _run_with_early_exit(self, n: int = 500, seed: int = 42) -> "BacktestResult":
        df = make_daily_ohlc(n_bars=n, seed=seed, volatility=0.05)
        return run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=10,
            multiplier=1.5,
            trade_mode="revers",
            commission=0.002,
            warmup_period=10,
            early_exit_enabled=True,
            early_exit_max_drawdown=0.05,   # tight — should fire on volatile data
            early_exit_check_bars=n,
            periods_per_year=252.0,
            min_trades_required=1,
            extract_trades_flag=True,
            caller_mode="test",
        )

    def test_early_exit_truncates_arrays(self):
        result = self._run_with_early_exit()
        if not result.early_exit:
            pytest.skip("Early exit did not fire with these params; adjust seed/threshold")

        n_orig = result.n_bars_original
        # Truncated arrays must be shorter than full history
        assert len(result.returns) < n_orig - 1, (
            "returns must be shorter than full history after early exit"
        )

    def test_early_exit_length_invariants(self):
        result = self._run_with_early_exit()
        if not result.early_exit:
            pytest.skip("Early exit did not fire with these params")

        eq_len = len(result.equity_curve)
        ret_len = len(result.returns)
        pos_len = len(result.positions)
        trend_len = len(result.trend)

        assert eq_len == ret_len + 1, (
            f"equity_curve length {eq_len} must equal len(returns)+1={ret_len+1}"
        )
        assert eq_len == pos_len == trend_len, (
            f"equity/positions/trend must be equal length: {eq_len}/{pos_len}/{trend_len}"
        )

    def test_early_exit_trades_within_scope(self):
        result = self._run_with_early_exit()
        if not result.early_exit:
            pytest.skip("Early exit did not fire with these params")

        if result.trades_df is None or len(result.trades_df) == 0:
            return  # nothing to check

        # All trade entry/exit indexes must be within the truncated history
        max_allowed_idx = result.exit_bar
        assert result.trades_df["entry_index"].max() <= max_allowed_idx, (
            "Trade entry_index exceeds exit_bar — trades are from beyond truncation point"
        )
        # exit_index can equal exit_bar (last position closed at truncation)
        assert result.trades_df["exit_index"].max() <= max_allowed_idx + 1, (
            "Trade exit_index exceeds exit_bar+1"
        )

    def test_early_exit_result_has_exit_fields(self):
        result = self._run_with_early_exit()
        if not result.early_exit:
            pytest.skip("Early exit did not fire with these params")

        assert result.exit_bar is not None
        assert result.exit_drawdown is not None
        assert result.exit_drawdown <= 0.0, "exit_drawdown must be non-positive"
        assert 0 <= result.exit_bar < result.n_bars_original


# ---------------------------------------------------------------------------
# Smoke test: valid run produces consistent BacktestResult
# ---------------------------------------------------------------------------

def test_run_single_backtest_smoke():
    """Smoke test: run_single_backtest returns a BacktestResult with all expected keys."""
    result = run_single_backtest(**_make_minimal(n=300, seed=0))

    # Check all expected metric keys exist
    required_keys = {
        "sharpe", "sortino", "cagr", "max_drawdown",
        "num_trades", "win_rate", "sum_pnl_pct", "avg_trade",
        "profit_factor", "net_pnl_pct",
    }
    missing = required_keys - set(result.metrics.keys())
    assert not missing, f"Missing metric keys: {missing}"

    # Check fundamental invariants
    assert len(result.equity_curve) == len(result.returns) + 1
    assert len(result.equity_curve) == len(result.positions) == len(result.trend)
    assert 0.0 <= result.metrics["win_rate"] <= 100.0
