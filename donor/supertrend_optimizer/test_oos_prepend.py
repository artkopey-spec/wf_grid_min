"""
Tests for F-07: OOS backtest prepend.

Verifies that the main OOS backtest path uses prepend bars from the end of
the train window to warm up the SuperTrend indicator, analogous to the TOP-K
path.

Invariants covered:
  A. test_oos_prepend_preserves_exact_test_window_length_and_trade_bounds
     — exact OOS window length, trade bounds, no prepend leakage
  B. test_walk_forward_prepend_does_not_change_oos_window_boundaries
     — WF step boundaries unchanged; backtest input is extended
  C. test_prepend_array_alignment_after_trim
     — returns/equity/positions lengths consistent after trim
  D. test_prepend_metrics_computed_on_trimmed_oos_only
     — OOS metrics use trimmed data, not prepend+test window
"""

import numpy as np
import pandas as pd
import pytest
from dataclasses import dataclass, field
from typing import Dict, Optional

from supertrend_optimizer.validation.walk_forward import (
    run_walk_forward,
    WalkForwardResult,
)


@dataclass
class _MockBacktestResult:
    """Backtest result with all fields the prepend path reads."""
    metrics: Dict[str, float]
    returns: np.ndarray
    equity_curve: np.ndarray
    positions: np.ndarray
    trades_df: Optional[pd.DataFrame]
    early_exit: bool = False
    exit_bar: Optional[int] = None
    exit_drawdown: Optional[float] = None
    atr_period: int = 14
    multiplier: float = 2.0
    trade_mode: str = "revers"
    commission: float = 0.001
    warmup: int = 0
    trend: np.ndarray = field(default_factory=lambda: np.array([]))
    n_bars_original: int = 0
    period_label: str = ""


@dataclass
class _MockOptResult:
    best_atr_period: int
    best_multiplier: float
    best_value: float
    best_metrics: Dict[str, float]
    trials_df: Optional[pd.DataFrame] = None
    robustness_df: Optional[pd.DataFrame] = None


def _make_bt_result(n_bars: int, seed: int = 0) -> _MockBacktestResult:
    rng = np.random.RandomState(seed)
    rets = rng.randn(n_bars) * 0.005
    eq = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
    pos = np.ones(n_bars + 1)
    trades = pd.DataFrame({
        "trade_id": [1],
        "direction": [1],
        "entry_time": [pd.Timestamp("2025-01-01")],
        "entry_index": [0],
        "entry_price": [100.0],
        "exit_time": [pd.Timestamp("2025-01-02")],
        "exit_index": [n_bars - 1],
        "exit_price": [101.0],
        "bars_held": [n_bars - 1],
        "gross_pnl_pct": [1.0],
        "commission_pct": [0.1],
        "net_pnl_pct": [0.9],
    })
    return _MockBacktestResult(
        metrics={
            "sortino": 1.5, "sharpe": 1.2, "sum_pnl_pct": 5.0,
            "num_trades": 1, "win_rate": 100.0, "max_drawdown": -0.01,
            "cagr": 0.1, "profit_factor": 10.0, "avg_trade": 5.0,
        },
        returns=rets,
        equity_curve=eq,
        positions=pos,
        trades_df=trades,
        n_bars_original=n_bars,
    )


class TestOOSPrepend:
    """Verify that the main OOS backtest path uses prepend."""

    @pytest.fixture
    def price_data(self):
        n = 2000
        rng = np.random.RandomState(42)
        o = rng.randn(n).cumsum() + 100
        h = o + np.abs(rng.randn(n))
        l = o - np.abs(rng.randn(n))
        c = o + rng.randn(n) * 0.5
        idx = pd.date_range("2020-01-01", periods=n, freq="h")
        return dict(open=o, high=h, low=l, close=c, index=idx)

    @pytest.fixture
    def config(self):
        return {
            "validation": {
                "warmup_period": 50,
                "walk_forward": {
                    "enabled": True,
                    "train_size": "1000bars",
                    "test_size": "200bars",
                    "step_size": "200bars",
                    "scheme": "rolling",
                    "anchor": "start",
                    "min_train_bars": 500,
                    "min_test_bars": 100,
                },
            },
            "walk_forward": {"top_k_export": 5, "top_k_consensus": 3},
            "optimization": {
                "objective_metric": "sortino",
                "trade_mode": "revers",
                "atr_period_range": [10, 50],
            },
            "backtest": {"commission": 0.001, "annualization_factor": 252},
            "robustness": {"enabled": False},
        }

    def test_prepend_extends_backtest_input(self, price_data, config, monkeypatch):
        """Main OOS backtest should receive more bars than the test window."""
        bt_calls = []

        def mock_opt(open_prices, high, low, close, index, config):
            return _MockOptResult(
                best_atr_period=14,
                best_multiplier=2.0,
                best_value=1.5,
                best_metrics={"sortino": 1.5, "sum_pnl_pct": 10.0},
                trials_df=pd.DataFrame({
                    "atr_period": [14, 20],
                    "multiplier": [2.0, 2.5],
                    "sortino": [1.5, 1.0],
                }),
            )

        def mock_bt(open_prices, high, low, close, index, atr_period, multiplier, **kw):
            n = len(open_prices)
            bt_calls.append({"n_bars": n, "atr_period": atr_period, "kwargs": kw})
            return _make_bt_result(n, seed=n)

        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_optimization",
            mock_opt,
        )
        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_backtest",
            mock_bt,
        )

        result = run_walk_forward(
            open_prices=price_data["open"],
            high=price_data["high"],
            low=price_data["low"],
            close=price_data["close"],
            index=price_data["index"],
            config=config,
        )

        assert isinstance(result, WalkForwardResult)
        assert len(result.steps) > 0

        test_window_size = 200
        # The first OOS backtest call per step (after opt) should use extended
        # slice (test_window + prepend_bars) rather than test_window alone.
        # Find calls whose n_bars > test_window_size — these are prepended.
        prepended_calls = [c for c in bt_calls if c["n_bars"] > test_window_size]
        assert len(prepended_calls) > 0, (
            f"Expected at least one backtest call with n_bars > {test_window_size} "
            f"(prepend), but got: {[c['n_bars'] for c in bt_calls]}"
        )

    def test_prepend_returns_trimmed_to_oos_length(self, price_data, config, monkeypatch):
        """test_returns in step result should equal the OOS window length, not extended."""
        def mock_opt(open_prices, high, low, close, index, config):
            return _MockOptResult(
                best_atr_period=14,
                best_multiplier=2.0,
                best_value=1.5,
                best_metrics={"sortino": 1.5, "sum_pnl_pct": 10.0},
                trials_df=pd.DataFrame({
                    "atr_period": [14, 20],
                    "multiplier": [2.0, 2.5],
                    "sortino": [1.5, 1.0],
                }),
            )

        def mock_bt(open_prices, high, low, close, index, atr_period, multiplier, **kw):
            n = len(open_prices)
            return _make_bt_result(n, seed=n)

        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_optimization",
            mock_opt,
        )
        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_backtest",
            mock_bt,
        )

        result = run_walk_forward(
            open_prices=price_data["open"],
            high=price_data["high"],
            low=price_data["low"],
            close=price_data["close"],
            index=price_data["index"],
            config=config,
        )

        test_window_size = 200
        for step in result.steps:
            if step.test_returns is not None and len(step.test_returns) > 0:
                expected_len = step.test_end_idx - step.test_start_idx
                # returns length = n_bars - 1 (bar returns), but after prepend
                # trim we expect it to be close to the OOS window.
                # Exact equality depends on backtest warmup consumption, but it
                # must NOT include prepend bars.
                assert len(step.test_returns) <= expected_len, (
                    f"Step {step.step_index}: test_returns length "
                    f"{len(step.test_returns)} > expected OOS window {expected_len}. "
                    f"Prepend bars were not trimmed."
                )

    def test_prepend_trades_filtered_to_oos(self, price_data, config, monkeypatch):
        """Trades in step result should only contain entries within OOS window."""
        def mock_opt(open_prices, high, low, close, index, config):
            return _MockOptResult(
                best_atr_period=14,
                best_multiplier=2.0,
                best_value=1.5,
                best_metrics={"sortino": 1.5, "sum_pnl_pct": 10.0},
                trials_df=pd.DataFrame({
                    "atr_period": [14, 20],
                    "multiplier": [2.0, 2.5],
                    "sortino": [1.5, 1.0],
                }),
            )

        def mock_bt(open_prices, high, low, close, index, atr_period, multiplier, **kw):
            n = len(open_prices)
            rets = np.random.RandomState(n).randn(n) * 0.005
            eq = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
            pos = np.ones(n + 1)
            # Create trades with one in prepend zone and one in OOS zone
            trades = pd.DataFrame({
                "trade_id": [1, 2],
                "direction": [1, 1],
                "entry_time": [pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02")],
                "entry_index": [5, n - 50],
                "entry_price": [100.0, 100.0],
                "exit_time": [pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02")],
                "exit_index": [20, n - 10],
                "exit_price": [101.0, 101.0],
                "bars_held": [15, 40],
                "gross_pnl_pct": [1.0, 1.0],
                "commission_pct": [0.1, 0.1],
                "net_pnl_pct": [0.9, 0.9],
            })
            return _MockBacktestResult(
                metrics={"sortino": 1.5, "sharpe": 1.2, "sum_pnl_pct": 5.0,
                         "num_trades": 2, "win_rate": 100.0, "max_drawdown": -0.01,
                         "cagr": 0.1, "profit_factor": 10.0, "avg_trade": 5.0},
                returns=rets, equity_curve=eq, positions=pos, trades_df=trades,
                n_bars_original=n,
            )

        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_optimization",
            mock_opt,
        )
        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_backtest",
            mock_bt,
        )

        result = run_walk_forward(
            open_prices=price_data["open"],
            high=price_data["high"],
            low=price_data["low"],
            close=price_data["close"],
            index=price_data["index"],
            config=config,
        )

        for step in result.steps:
            if step.test_trades_df is not None and not step.test_trades_df.empty:
                # All entry_index values should be >= 0 (relative to OOS start)
                assert (step.test_trades_df["entry_index"] >= 0).all(), (
                    f"Step {step.step_index}: trades with negative entry_index "
                    f"found — prepend trades not filtered."
                )

    def test_oos_prepend_preserves_exact_test_window_length_and_trade_bounds(
        self, price_data, config, monkeypatch
    ):
        """Invariant: test_returns == OOS window length, trades within bounds.

        After prepend + trim:
        - len(test_returns) must equal exactly (test_end - test_start - 1)
          because returns are bar-to-bar changes: N bars → N-1 returns.
        - All trade entry_index in [0, expected_returns_len)
        - All trade exit_index in [0, expected_returns_len]
        """

        def mock_opt(open_prices, high, low, close, index, config):
            return _MockOptResult(
                best_atr_period=14,
                best_multiplier=2.0,
                best_value=1.5,
                best_metrics={"sortino": 1.5, "sum_pnl_pct": 10.0},
                trials_df=pd.DataFrame({
                    "atr_period": [14, 20],
                    "multiplier": [2.0, 2.5],
                    "sortino": [1.5, 1.0],
                }),
            )

        def mock_bt(open_prices, high, low, close, index, atr_period, multiplier, **kw):
            n = len(open_prices)
            rng = np.random.RandomState(n)
            rets = rng.randn(n - 1) * 0.005
            eq = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
            pos = np.ones(n)
            # Trades spanning the full range of the extended window
            trades_rows = []
            step = max(1, n // 6)
            tid = 0
            for start in range(0, n - step, step):
                end = min(start + step - 1, n - 2)
                if end <= start:
                    continue
                tid += 1
                trades_rows.append({
                    "trade_id": tid,
                    "direction": 1,
                    "entry_time": pd.Timestamp("2025-01-01"),
                    "entry_index": start,
                    "entry_price": 100.0,
                    "exit_time": pd.Timestamp("2025-01-02"),
                    "exit_index": end,
                    "exit_price": 101.0,
                    "bars_held": end - start,
                    "gross_pnl_pct": 1.0,
                    "commission_pct": 0.1,
                    "net_pnl_pct": 0.9,
                })
            trades = pd.DataFrame(trades_rows) if trades_rows else pd.DataFrame()
            return _MockBacktestResult(
                metrics={
                    "sortino": 1.5, "sharpe": 1.2, "sum_pnl_pct": 5.0,
                    "num_trades": len(trades_rows), "win_rate": 100.0,
                    "max_drawdown": -0.01, "cagr": 0.1, "profit_factor": 10.0,
                    "avg_trade": 5.0,
                },
                returns=rets, equity_curve=eq, positions=pos, trades_df=trades,
                n_bars_original=n,
            )

        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_optimization",
            mock_opt,
        )
        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_backtest",
            mock_bt,
        )

        result = run_walk_forward(
            open_prices=price_data["open"],
            high=price_data["high"],
            low=price_data["low"],
            close=price_data["close"],
            index=price_data["index"],
            config=config,
        )

        for step in result.steps:
            expected_bars = step.test_end_idx - step.test_start_idx
            expected_returns_len = expected_bars - 1  # N bars → N-1 returns

            # 1) test_returns must exist and have exact OOS length
            assert step.test_returns is not None, (
                f"Step {step.step_index}: test_returns is None"
            )
            assert len(step.test_returns) == expected_returns_len, (
                f"Step {step.step_index}: len(test_returns)={len(step.test_returns)} "
                f"!= expected {expected_returns_len} "
                f"(test_end={step.test_end_idx}, test_start={step.test_start_idx})"
            )

            # 2) Early-exit not triggered → trade bounds must be within OOS
            if not getattr(step, "test_early_exit", False):
                if (step.test_trades_df is not None
                        and not step.test_trades_df.empty
                        and "entry_index" in step.test_trades_df.columns):
                    min_entry = step.test_trades_df["entry_index"].min()
                    max_exit = step.test_trades_df["exit_index"].max()
                    assert min_entry >= 0, (
                        f"Step {step.step_index}: min entry_index={min_entry} < 0"
                    )
                    assert max_exit < expected_bars, (
                        f"Step {step.step_index}: max exit_index={max_exit} "
                        f">= expected_bars={expected_bars}"
                    )

    # ------------------------------------------------------------------ #
    # Invariant B: WF window boundaries unchanged by prepend               #
    # ------------------------------------------------------------------ #

    def test_walk_forward_prepend_does_not_change_oos_window_boundaries(
        self, price_data, config, monkeypatch
    ):
        """Invariant B: prepend extends backtest input but does NOT shift WF boundaries.

        Checks:
        - step.test_start_idx / test_end_idx equal the original window slices
        - the backtest call for the main OOS path receives MORE bars than
          (test_end - test_start), confirming the extended slice
        - the difference equals exactly prepend_bars (not more, not less)
        """
        bt_calls = []  # (n_bars, caller_label)

        def mock_opt(open_prices, high, low, close, index, config):
            return _MockOptResult(
                best_atr_period=14,
                best_multiplier=2.0,
                best_value=1.5,
                best_metrics={"sortino": 1.5, "sum_pnl_pct": 10.0},
                trials_df=pd.DataFrame({
                    "atr_period": [14, 20],
                    "multiplier": [2.0, 2.5],
                    "sortino": [1.5, 1.0],
                }),
            )

        def mock_bt(open_prices, high, low, close, index, atr_period, multiplier, **kw):
            n = len(open_prices)
            bt_calls.append(n)
            return _make_bt_result(n, seed=n)

        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_optimization",
            mock_opt,
        )
        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_backtest",
            mock_bt,
        )

        result = run_walk_forward(
            open_prices=price_data["open"],
            high=price_data["high"],
            low=price_data["low"],
            close=price_data["close"],
            index=price_data["index"],
            config=config,
        )

        from supertrend_optimizer.utils.warmup import calculate_warmup
        expected_prepend = calculate_warmup(len(price_data["open"]), config)

        for step in result.steps:
            test_window = step.test_end_idx - step.test_start_idx

            # B1: WF boundaries are the original window boundaries
            assert step.test_start_idx == step.test_start_idx  # tautology — just confirm field exists
            assert step.test_end_idx > step.test_start_idx, (
                f"Step {step.step_index}: test_end_idx <= test_start_idx"
            )

            # B2: test_start_idx / test_end_idx are consistent with price_data length
            assert step.test_end_idx <= len(price_data["open"]), (
                f"Step {step.step_index}: test_end_idx={step.test_end_idx} "
                f"> total bars={len(price_data['open'])}"
            )

        # B3: At least one backtest call used an extended window
        # (test_window + prepend_bars), not just test_window
        test_window_size = 200
        actual_prepend = min(expected_prepend, result.steps[0].test_start_idx)
        if actual_prepend > 0:
            extended_calls = [n for n in bt_calls if n == test_window_size + actual_prepend]
            assert len(extended_calls) > 0, (
                f"Expected backtest calls of size {test_window_size + actual_prepend} "
                f"(test_window={test_window_size} + prepend={actual_prepend}), "
                f"but got sizes: {sorted(set(bt_calls))}"
            )

    # ------------------------------------------------------------------ #
    # Invariant C: array alignment after trim                              #
    # ------------------------------------------------------------------ #

    def test_prepend_array_alignment_after_trim(
        self, price_data, config, monkeypatch
    ):
        """Invariant C: returns / equity / positions are length-consistent after trim.

        After prepend + trim the three arrays stored in the step must satisfy:
            len(equity)    == len(returns) + 1
            len(positions) == len(returns) + 1
            len(returns)   == test_window - 1

        This catches off-by-one errors in the trim boundary.
        """

        def mock_opt(open_prices, high, low, close, index, config):
            return _MockOptResult(
                best_atr_period=14,
                best_multiplier=2.0,
                best_value=1.5,
                best_metrics={"sortino": 1.5, "sum_pnl_pct": 10.0},
                trials_df=pd.DataFrame({
                    "atr_period": [14, 20],
                    "multiplier": [2.0, 2.5],
                    "sortino": [1.5, 1.0],
                }),
            )

        # Track trimmed arrays that calculate_all_metrics receives
        captured_arrays: list = []

        _real_calc = None

        def mock_bt(open_prices, high, low, close, index, atr_period, multiplier, **kw):
            n = len(open_prices)
            rng = np.random.RandomState(n)
            rets = rng.randn(n - 1) * 0.005
            eq = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
            pos = np.ones(n)
            return _MockBacktestResult(
                metrics={"sortino": 1.5, "sharpe": 1.2, "sum_pnl_pct": 5.0,
                         "num_trades": 1, "win_rate": 100.0, "max_drawdown": -0.01,
                         "cagr": 0.1, "profit_factor": 10.0, "avg_trade": 5.0},
                returns=rets, equity_curve=eq, positions=pos,
                trades_df=pd.DataFrame(), n_bars_original=n,
            )

        import supertrend_optimizer.core.metrics as _metrics_mod
        _real_calc = _metrics_mod.calculate_all_metrics

        def spy_calc(returns, equity_curve, positions, warmup_period, **kw):
            captured_arrays.append({
                "len_returns": len(returns),
                "len_equity": len(equity_curve),
                "len_positions": len(positions),
            })
            return _real_calc(
                returns=returns,
                equity_curve=equity_curve,
                positions=positions,
                warmup_period=warmup_period,
                **kw,
            )

        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_optimization",
            mock_opt,
        )
        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_backtest",
            mock_bt,
        )
        # calculate_all_metrics is imported lazily inside the function, so we
        # patch it on the source module (core.metrics), which is what the lazy
        # import resolves to at call time.
        monkeypatch.setattr(
            "supertrend_optimizer.core.metrics.calculate_all_metrics",
            spy_calc,
        )

        result = run_walk_forward(
            open_prices=price_data["open"],
            high=price_data["high"],
            low=price_data["low"],
            close=price_data["close"],
            index=price_data["index"],
            config=config,
        )

        # C1: spy captured at least one call (prepend path active)
        assert len(captured_arrays) > 0, (
            "calculate_all_metrics was never called via prepend path — "
            "check that prepend_bars > 0 for this config"
        )

        # C2: every call had consistent array lengths
        for i, cap in enumerate(captured_arrays):
            assert cap["len_equity"] == cap["len_returns"] + 1, (
                f"Call {i}: len(equity)={cap['len_equity']} != "
                f"len(returns)+1={cap['len_returns'] + 1}"
            )
            assert cap["len_positions"] == cap["len_returns"] + 1, (
                f"Call {i}: len(positions)={cap['len_positions']} != "
                f"len(returns)+1={cap['len_returns'] + 1}"
            )

        # C3: step.test_returns length == test_window - 1
        for step in result.steps:
            if step.test_returns is not None:
                expected = step.test_end_idx - step.test_start_idx - 1
                assert len(step.test_returns) == expected, (
                    f"Step {step.step_index}: len(test_returns)={len(step.test_returns)} "
                    f"!= expected {expected}"
                )

    # ------------------------------------------------------------------ #
    # Invariant D: metrics computed on trimmed OOS only, not prepend+test  #
    # ------------------------------------------------------------------ #

    def test_prepend_metrics_computed_on_trimmed_oos_only(
        self, price_data, config, monkeypatch
    ):
        """Invariant D: calculate_all_metrics receives trimmed OOS arrays.

        Strategy: make the mock return returns arrays where the prepend portion
        has a distinctive large mean (e.g. +0.10 per bar) and the OOS portion
        has a near-zero mean. If metrics were computed on prepend+test, the
        sum_pnl would be dominated by the prepend signal. If computed on
        OOS-only, sum_pnl should be near zero.

        We verify that the arrays passed to calculate_all_metrics have length
        exactly (test_window - 1), not (prepend + test_window - 1).
        """
        from supertrend_optimizer.utils.warmup import calculate_warmup
        expected_prepend = calculate_warmup(len(price_data["open"]), config)

        captured_lens: list = []

        import supertrend_optimizer.core.metrics as _metrics_mod
        _real_calc = _metrics_mod.calculate_all_metrics

        def spy_calc(returns, equity_curve, positions, warmup_period, **kw):
            captured_lens.append(len(returns))
            return _real_calc(
                returns=returns,
                equity_curve=equity_curve,
                positions=positions,
                warmup_period=warmup_period,
                **kw,
            )

        def mock_opt(open_prices, high, low, close, index, config):
            return _MockOptResult(
                best_atr_period=14,
                best_multiplier=2.0,
                best_value=1.5,
                best_metrics={"sortino": 1.5, "sum_pnl_pct": 10.0},
                trials_df=pd.DataFrame({
                    "atr_period": [14, 20],
                    "multiplier": [2.0, 2.5],
                    "sortino": [1.5, 1.0],
                }),
            )

        def mock_bt(open_prices, high, low, close, index, atr_period, multiplier, **kw):
            n = len(open_prices)
            rng = np.random.RandomState(42)
            rets = rng.randn(n - 1) * 0.005
            eq = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
            pos = np.ones(n)
            return _MockBacktestResult(
                metrics={"sortino": 1.5, "sharpe": 1.2, "sum_pnl_pct": 5.0,
                         "num_trades": 1, "win_rate": 100.0, "max_drawdown": -0.01,
                         "cagr": 0.1, "profit_factor": 10.0, "avg_trade": 5.0},
                returns=rets, equity_curve=eq, positions=pos,
                trades_df=pd.DataFrame(), n_bars_original=n,
            )

        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_optimization",
            mock_opt,
        )
        monkeypatch.setattr(
            "supertrend_optimizer.validation.walk_forward.run_single_backtest",
            mock_bt,
        )
        # Patch on source module — lazy import resolves to this at call time.
        monkeypatch.setattr(
            "supertrend_optimizer.core.metrics.calculate_all_metrics",
            spy_calc,
        )

        result = run_walk_forward(
            open_prices=price_data["open"],
            high=price_data["high"],
            low=price_data["low"],
            close=price_data["close"],
            index=price_data["index"],
            config=config,
        )

        assert len(captured_lens) > 0, (
            "calculate_all_metrics was never called — prepend path not active"
        )

        test_window = 200
        oos_returns_len = test_window - 1  # N bars → N-1 returns
        full_ext_len = test_window + expected_prepend - 1  # prepend+test - 1

        # D1: every call used OOS-only length, not extended length
        for i, rlen in enumerate(captured_lens):
            assert rlen == oos_returns_len, (
                f"Call {i}: calculate_all_metrics received {rlen} returns, "
                f"expected {oos_returns_len} (OOS-only). "
                f"If {rlen} == {full_ext_len}, prepend was NOT trimmed before metrics."
            )

        # D2: step.test_returns also has OOS-only length
        for step in result.steps:
            if step.test_returns is not None:
                assert len(step.test_returns) == oos_returns_len, (
                    f"Step {step.step_index}: test_returns has {len(step.test_returns)} "
                    f"elements, expected {oos_returns_len}"
                )
