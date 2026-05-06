"""
Unit/integration tests for A4: step_executor + runner.

Covers prepend invariants §W.9 (A–H), fallback paths §W.5, trade-level
override §W.4.2 step 10, and runner error handling.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

from wf_grid.config.loader import load_grid_config, resolve_periods_per_year, ConfigError
from wf_grid.config.schema import GridConfig, INVALID_METRIC_VALUE, MAX_VALID_METRIC
from wf_grid.grid.enumeration import GridPoint
from wf_grid.wf.step_executor import (
    StepResult,
    execute_oos_step,
    execute_train_step,
    _apply_trade_level_override,
    _defensive_fallback,
)
from wf_grid.wf.runner import run_wf_for_grid_point, compute_prepend_bars

from wf_grid.status.status_model import StepStatus, assign_step_status

from supertrend_optimizer.utils.time_utils import WFWindowSlice, make_walk_forward_slices
from supertrend_optimizer.utils.warmup import calculate_warmup


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def _make_config(tmp_path, warmup_period=0, warmup_auto=False,
                 min_trades=1, min_meaningful_bars=5) -> GridConfig:
    yaml_text = f"""\
data:
  file_path: data.csv
  periods_per_year: 252
optimization:
  atr_period_range: [5, 10]
  multiplier_range: [2.0, 3.0]
  multiplier_step: 0.5
  trade_mode: both
backtest:
  commission: 0.0002
  min_trades_required: {min_trades}
  early_exit_enabled: false
  early_exit_max_drawdown: 0.50
  early_exit_check_bars: 50
validation:
  warmup_period: {warmup_period}
  warmup_period_auto: {"true" if warmup_auto else "false"}
  walk_forward:
    train_size: "200bars"
    test_size: "50bars"
status:
  min_meaningful_bars: {min_meaningful_bars}
"""
    path = _write_yaml(tmp_path, yaml_text)
    cfg = load_grid_config(path)
    cfg.resolved_periods_per_year = 252.0
    return cfg


def _make_trending_data(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLC data with a trend (ensures some trades occur)."""
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    close = np.maximum(close, 10.0)
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.randn(n) * 0.3
    open_ = np.maximum(open_, low + 0.01)
    idx = pd.date_range("2020-01-01", periods=n, freq="1D")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
    }, index=idx)


def _make_grid_point(atr: int = 5, mult: float = 2.0, mode: str = "both") -> GridPoint:
    gid = f"atr{atr}_m{mult:.2f}_{mode}"
    return GridPoint(atr_period=atr, multiplier=mult, trade_mode=mode, grid_point_id=gid)


# ===========================================================================
# Invariant A — Exact OOS window length (no early exit)
# ===========================================================================

class TestInvariantA:
    def test_exact_oos_trim_length(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]  # step 1 has test_start > 0 → has prepend room
        prepend_req = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        result = execute_oos_step(gp, wf, data["open"].values, data["high"].values,
                                  data["low"].values, data["close"].values,
                                  data.index, cfg, prepend_req)

        expected_oos_bars = wf.test_end_idx - wf.test_start_idx
        # N bars → N-1 returns (Invariant A)
        assert result.effective_oos_bars == expected_oos_bars - 1


# ===========================================================================
# Invariant B — WF boundaries unchanged
# ===========================================================================

class TestInvariantB:
    def test_wf_boundaries_unchanged(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        result = execute_oos_step(gp, wf, data["open"].values, data["high"].values,
                                  data["low"].values, data["close"].values,
                                  data.index, cfg, prepend)

        assert result.test_start_idx == wf.test_start_idx
        assert result.test_end_idx == wf.test_end_idx


# ===========================================================================
# Invariant D — No prepend leakage into metrics (warmup=0)
# ===========================================================================

class TestInvariantD:
    def test_metrics_warmup_zero_on_trimmed(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        result = execute_oos_step(gp, wf, data["open"].values, data["high"].values,
                                  data["low"].values, data["close"].values,
                                  data.index, cfg, prepend)

        # warmup_effective should be 0 for canonical prepend path
        assert result.warmup_effective == 0
        assert result.used_prepend is True


# ===========================================================================
# Invariant E — No prepend leakage into trades
# ===========================================================================

class TestInvariantE:
    def test_no_prepend_trades_in_oos_layer(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        result = execute_oos_step(gp, wf, data["open"].values, data["high"].values,
                                  data["low"].values, data["close"].values,
                                  data.index, cfg, prepend)

        if result.oos_trades_df is not None and len(result.oos_trades_df) > 0:
            # All entry_index must be >= 0 (rebased)
            assert (result.oos_trades_df["entry_index"] >= 0).all()


# ===========================================================================
# Invariant F — Extended input longer than OOS
# ===========================================================================

class TestInvariantF:
    def test_extended_input_longer_when_prepend_positive(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        result = execute_oos_step(gp, wf, data["open"].values, data["high"].values,
                                  data["low"].values, data["close"].values,
                                  data.index, cfg, prepend)

        assert result.prepend_bars_applied > 0
        oos_window = wf.test_end_idx - wf.test_start_idx
        # effective_oos_bars = N-1 returns for N OOS bars, which is < oos_window + prepend
        assert result.used_prepend is True


# ===========================================================================
# Invariant G — Trade-level override authoritative after trim
# ===========================================================================

class TestInvariantG:
    def test_trade_level_override_after_trim(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0, min_trades=1)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        result = execute_oos_step(gp, wf, data["open"].values, data["high"].values,
                                  data["low"].values, data["close"].values,
                                  data.index, cfg, prepend)

        if result.oos_trades_df is not None and len(result.oos_trades_df) > 0:
            # num_trades must match len(oos_trades_df) — trade-level authoritative
            assert result.metrics["num_trades"] == len(result.oos_trades_df)
            # sum_pnl_pct must match sum of OOS trades, not bar-level
            expected_sum = result.oos_trades_df["net_pnl_pct"].sum()
            assert result.metrics["sum_pnl_pct"] == pytest.approx(expected_sum, abs=1e-10)

    def test_trade_level_override_empty_trades(self, tmp_path):
        """When oos_trades_df is empty after filtering, metrics should be zeroed/invalidated."""
        metrics: Dict[str, Any] = {"sharpe": 1.0, "sortino": 1.0, "cagr": 0.1}
        _apply_trade_level_override(metrics, pd.DataFrame(), min_trades_required=3)

        assert metrics["num_trades"] == 0
        assert metrics["sum_pnl_pct"] == 0.0
        assert metrics["sharpe"] == INVALID_METRIC_VALUE
        assert metrics["sortino"] == INVALID_METRIC_VALUE
        assert metrics["cagr"] == INVALID_METRIC_VALUE
        assert metrics["avg_trade"] == INVALID_METRIC_VALUE

    def test_trade_level_override_none_trades(self, tmp_path):
        metrics: Dict[str, Any] = {"sharpe": 1.0}
        _apply_trade_level_override(metrics, None, min_trades_required=3)
        assert metrics["num_trades"] == 0


# ===========================================================================
# Invariant H — Prepend consistency across grid points
# ===========================================================================

class TestInvariantH:
    def test_prepend_same_across_grid_points(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        gp1 = _make_grid_point(atr=5, mult=2.0)
        gp2 = _make_grid_point(atr=10, mult=3.0)

        r1 = execute_oos_step(gp1, wf, data["open"].values, data["high"].values,
                              data["low"].values, data["close"].values,
                              data.index, cfg, prepend)
        r2 = execute_oos_step(gp2, wf, data["open"].values, data["high"].values,
                              data["low"].values, data["close"].values,
                              data.index, cfg, prepend)

        assert r1.prepend_bars_applied == r2.prepend_bars_applied
        assert r1.prepend_bars_requested == r2.prepend_bars_requested


# ===========================================================================
# Prepend zero — canonical path, not fallback (§W.10.1)
# ===========================================================================

class TestPrependZero:
    def test_prepend_zero_canonical_not_fallback(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        # Use step 0 but with prepend_bars_requested=0 to force prepend_applied=0
        wf = slices[0]

        result = execute_oos_step(gp, wf, data["open"].values, data["high"].values,
                                  data["low"].values, data["close"].values,
                                  data.index, cfg, prepend_bars_requested=0)

        assert result.prepend_bars_applied == 0
        assert result.used_prepend is True  # canonical path, not fallback
        assert result.used_legacy_oos_path is False
        assert result.used_defensive_fallback is False


# ===========================================================================
# Trade-level override: profit_factor edge cases
# ===========================================================================

class TestTradeOverridePF:
    def test_profit_factor_no_losses(self):
        trades = pd.DataFrame({"net_pnl_pct": [1.0, 2.0, 0.5]})
        metrics: Dict[str, Any] = {}
        _apply_trade_level_override(metrics, trades, min_trades_required=1)
        assert metrics["profit_factor"] == MAX_VALID_METRIC

    def test_profit_factor_all_losses(self):
        trades = pd.DataFrame({"net_pnl_pct": [-1.0, -2.0]})
        metrics: Dict[str, Any] = {}
        _apply_trade_level_override(metrics, trades, min_trades_required=1)
        assert metrics["profit_factor"] == 0.0

    def test_profit_factor_breakeven(self):
        trades = pd.DataFrame({"net_pnl_pct": [0.0, 0.0]})
        metrics: Dict[str, Any] = {}
        _apply_trade_level_override(metrics, trades, min_trades_required=1)
        assert metrics["profit_factor"] == INVALID_METRIC_VALUE

    def test_min_trades_guard_invalidates_ratios(self):
        trades = pd.DataFrame({"net_pnl_pct": [1.0]})
        metrics: Dict[str, Any] = {"sharpe": 2.0, "sortino": 3.0, "cagr": 0.5}
        _apply_trade_level_override(metrics, trades, min_trades_required=3)
        assert metrics["sharpe"] == INVALID_METRIC_VALUE
        assert metrics["sortino"] == INVALID_METRIC_VALUE
        assert metrics["cagr"] == INVALID_METRIC_VALUE


# ===========================================================================
# Train path (§W.6)
# ===========================================================================

class TestTrainPath:
    def test_train_no_prepend(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[0]

        result = execute_train_step(
            gp, wf, data["open"].values, data["high"].values,
            data["low"].values, data["close"].values,
            data.index, cfg,
        )

        assert result.used_prepend is False
        assert result.prepend_bars_applied == 0
        assert result.test_start_idx == wf.train_start_idx
        assert result.test_end_idx == wf.train_end_idx


# ===========================================================================
# Runner — error handling
# ===========================================================================

class TestRunnerErrorHandling:
    def test_runtime_error_produces_error_result(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )

        # atr_period=1 is invalid (< 2) — run_single_backtest will raise
        bad_gp = GridPoint(atr_period=1, multiplier=2.0,
                           trade_mode="both", grid_point_id="atr1_m2.00_both")

        results = run_wf_for_grid_point(
            bad_gp, slices[:1], data, cfg, prepend_bars_requested=10,
        )

        assert len(results) == 1
        r = results[0]
        assert r.error_message is not None
        assert r.error_type is not None
        assert r.metrics["num_trades"] == 0
        assert r.metrics["sharpe"] == INVALID_METRIC_VALUE

    def test_runner_returns_one_result_per_slice(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )

        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        results = run_wf_for_grid_point(gp, slices, data, cfg, prepend)
        assert len(results) == len(slices)

    def test_runner_error_result_has_no_error_on_valid(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()

        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })

        results = run_wf_for_grid_point(gp, slices[:1], data, cfg, prepend)
        assert results[0].error_message is None
        assert results[0].error_type is None


# ===========================================================================
# FIX-1.1 — Defensive fallback always forces effective_oos_bars=0
# ===========================================================================

class _FakeExtResult:
    """Minimal mock for the backtest result object passed to _defensive_fallback."""

    def __init__(self, returns=None, trades_df=None, metrics=None,
                 effective_warmup=0, warmup=0, early_exit=False):
        self.returns = returns
        self.trades_df = trades_df
        self.metrics = metrics if metrics is not None else {
            "num_trades": 5, "sum_pnl_pct": 0.1, "sharpe": 1.2,
            "effective_warmup": effective_warmup,
        }
        self.effective_warmup = effective_warmup
        self.warmup = warmup
        self.early_exit = early_exit
        self.equity_curve = None
        self.positions = None


def _make_wf_slice(step_index=0, test_start=100, test_end=150,
                   train_start=0, train_end=100):
    return WFWindowSlice(
        step_index=step_index,
        train_start_idx=train_start,
        train_end_idx=train_end,
        test_start_idx=test_start,
        test_end_idx=test_end,
    )


class TestDefensiveFallbackForcedZero:
    """FIX-1.1: _defensive_fallback must always set effective_oos_bars=0."""

    def test_defensive_fallback_effective_oos_bars_is_zero(self):
        ext = _FakeExtResult(returns=np.arange(50, dtype=float))
        gp = _make_grid_point()
        wf = _make_wf_slice()
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=20, prepend_bars_applied=20)
        assert result.effective_oos_bars == 0

    def test_defensive_fallback_none_returns_effective_oos_bars_zero(self):
        ext = _FakeExtResult(returns=None)
        gp = _make_grid_point()
        wf = _make_wf_slice()
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=10, prepend_bars_applied=10)
        assert result.effective_oos_bars == 0

    def test_defensive_fallback_boundary_equals_len_returns(self):
        """oos_boundary == len(ext_returns) triggers defensive fallback → oos_bars=0."""
        ext = _FakeExtResult(returns=np.arange(20, dtype=float))
        gp = _make_grid_point()
        wf = _make_wf_slice(test_start=20, test_end=40)
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=20, prepend_bars_applied=20)
        assert result.effective_oos_bars == 0
        assert result.used_defensive_fallback is True

    def test_defensive_fallback_yields_insufficient_bars_status(self, tmp_path):
        """effective_oos_bars=0 from defensive fallback → assign_step_status → insufficient_bars."""
        cfg = _make_config(tmp_path, min_meaningful_bars=5)
        ext = _FakeExtResult(returns=np.arange(100, dtype=float))
        gp = _make_grid_point()
        wf = _make_wf_slice()
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=20, prepend_bars_applied=20)

        status = assign_step_status(result.metrics, result.effective_oos_bars, cfg)
        assert status == StepStatus.INSUFFICIENT_BARS

    def test_defensive_fallback_excluded_from_ok_mask(self, tmp_path):
        """Defensive fallback step must be excluded from ok mask used by aggregation."""
        cfg = _make_config(tmp_path, min_meaningful_bars=5)
        ext = _FakeExtResult(returns=np.arange(100, dtype=float))
        gp = _make_grid_point()
        wf = _make_wf_slice()
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=20, prepend_bars_applied=20)

        status = assign_step_status(result.metrics, result.effective_oos_bars, cfg)
        assert status != StepStatus.OK

    def test_defensive_fallback_warning_logged(self, caplog):
        """_defensive_fallback must emit a warning log."""
        import logging
        ext = _FakeExtResult(returns=np.arange(10, dtype=float))
        gp = _make_grid_point()
        wf = _make_wf_slice(step_index=3)
        with caplog.at_level(logging.WARNING):
            _defensive_fallback(ext, gp, wf, prepend_bars_requested=5, prepend_bars_applied=5)
        assert any("Defensive fallback triggered for step 3" in m for m in caplog.messages)
        assert any("metrics/trades invalidated" in m for m in caplog.messages)

    def test_defensive_fallback_num_trades_is_zero(self):
        """FIX-4: num_trades must be 0 in fallback result."""
        ext = _FakeExtResult(returns=np.arange(50, dtype=float),
                             metrics={"num_trades": 42, "sum_pnl_pct": 5.0,
                                      "win_rate": 0.6, "avg_trade": 0.1,
                                      "profit_factor": 1.5, "sharpe": 1.2,
                                      "sortino": 1.3, "cagr": 0.2,
                                      "max_drawdown": -0.1})
        gp = _make_grid_point()
        wf = _make_wf_slice()
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=20, prepend_bars_applied=20)
        assert result.metrics["num_trades"] == 0

    def test_defensive_fallback_ratio_metrics_invalidated(self):
        """FIX-4: ratio/composite metrics must be INVALID_METRIC_VALUE in fallback."""
        from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE
        ext = _FakeExtResult(returns=np.arange(50, dtype=float),
                             metrics={"num_trades": 42, "sum_pnl_pct": 5.0,
                                      "win_rate": 0.6, "avg_trade": 0.1,
                                      "profit_factor": 1.5, "sharpe": 1.2,
                                      "sortino": 1.3, "cagr": 0.2,
                                      "max_drawdown": -0.1})
        gp = _make_grid_point()
        wf = _make_wf_slice()
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=20, prepend_bars_applied=20)
        for key in ("profit_factor", "sharpe", "sortino", "cagr", "max_drawdown"):
            assert result.metrics[key] == INVALID_METRIC_VALUE, f"{key} should be INVALID_METRIC_VALUE"

    def test_defensive_fallback_oos_trades_df_is_none(self):
        """FIX-4: oos_trades_df must be None in fallback result."""
        import pandas as pd
        fake_trades = pd.DataFrame({"trade_id": [1, 2], "pnl": [0.01, -0.02]})
        ext = _FakeExtResult(returns=np.arange(50, dtype=float), trades_df=fake_trades)
        gp = _make_grid_point()
        wf = _make_wf_slice()
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=20, prepend_bars_applied=20)
        assert result.oos_trades_df is None

    def test_defensive_fallback_sum_pnl_and_count_metrics_zeroed(self):
        """FIX-4: sum_pnl_pct, win_rate, avg_trade must be 0 in fallback."""
        ext = _FakeExtResult(returns=np.arange(50, dtype=float),
                             metrics={"num_trades": 42, "sum_pnl_pct": 5.0,
                                      "win_rate": 0.6, "avg_trade": 0.1,
                                      "profit_factor": 1.5, "sharpe": 1.2,
                                      "sortino": 1.3, "cagr": 0.2,
                                      "max_drawdown": -0.1})
        gp = _make_grid_point()
        wf = _make_wf_slice()
        result = _defensive_fallback(ext, gp, wf, prepend_bars_requested=20, prepend_bars_applied=20)
        assert result.metrics["sum_pnl_pct"] == INVALID_METRIC_VALUE
        assert result.metrics["win_rate"] == INVALID_METRIC_VALUE
        assert result.metrics["avg_trade"] == INVALID_METRIC_VALUE


class TestExecuteOosStepDefensiveFallbackIntegration:
    """Integration: execute_oos_step triggers defensive fallback → oos_bars=0."""

    def test_none_arrays_trigger_defensive_fallback(self, tmp_path, monkeypatch):
        """When run_single_backtest returns None arrays, execute_oos_step
        must go through defensive fallback and produce effective_oos_bars=0."""
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()
        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]

        fake_result = _FakeExtResult(returns=None, trades_df=None)

        import wf_grid.wf.step_executor as mod
        monkeypatch.setattr(mod, "run_single_backtest", lambda **kw: fake_result)

        result = execute_oos_step(
            gp, wf, data["open"].values, data["high"].values,
            data["low"].values, data["close"].values,
            data.index, cfg, prepend_bars_requested=20,
        )
        assert result.used_defensive_fallback is True
        assert result.effective_oos_bars == 0

    def test_canonical_path_no_fallback_has_positive_oos_bars(self, tmp_path):
        """Regression: canonical path (no fallback) must have effective_oos_bars > 0."""
        cfg = _make_config(tmp_path, warmup_period=0)
        data = _make_trending_data(500)
        gp = _make_grid_point()
        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })
        result = execute_oos_step(
            gp, wf, data["open"].values, data["high"].values,
            data["low"].values, data["close"].values,
            data.index, cfg, prepend,
        )
        assert result.used_defensive_fallback is False
        assert result.effective_oos_bars > 0


# ===========================================================================
# FIX-1.3 — resolved_periods_per_year must not silently fallback to 252
# ===========================================================================

class TestResolvedPeriodsPerYearGuard:
    """FIX-1.3: ConfigError when resolved_periods_per_year is None."""

    def test_execute_oos_step_raises_on_none_ppy(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        cfg.resolved_periods_per_year = None
        data = _make_trending_data(500)
        gp = _make_grid_point()
        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        with pytest.raises(ConfigError, match="resolve_periods_per_year"):
            execute_oos_step(
                gp, wf, data["open"].values, data["high"].values,
                data["low"].values, data["close"].values,
                data.index, cfg, prepend_bars_requested=10,
            )

    def test_execute_train_step_raises_on_none_ppy(self, tmp_path):
        cfg = _make_config(tmp_path, warmup_period=0)
        cfg.resolved_periods_per_year = None
        data = _make_trending_data(500)
        gp = _make_grid_point()
        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[0]
        with pytest.raises(ConfigError, match="resolve_periods_per_year"):
            execute_train_step(
                gp, wf, data["open"].values, data["high"].values,
                data["low"].values, data["close"].values,
                data.index, cfg,
            )

    def test_execute_oos_step_works_with_resolved_ppy(self, tmp_path):
        """Regression: resolved_periods_per_year set → no error."""
        cfg = _make_config(tmp_path, warmup_period=0)
        assert cfg.resolved_periods_per_year == 252.0
        data = _make_trending_data(500)
        gp = _make_grid_point()
        slices = make_walk_forward_slices(
            data.index, "200bars", "50bars", scheme="rolling",
            min_train_bars=100, min_test_bars=10,
        )
        wf = slices[1]
        prepend = calculate_warmup(len(data), {
            "optimization": {"atr_period_range": [5, 10]},
            "validation": {"warmup_period": 0},
        })
        result = execute_oos_step(
            gp, wf, data["open"].values, data["high"].values,
            data["low"].values, data["close"].values,
            data.index, cfg, prepend,
        )
        assert result.effective_oos_bars > 0


# ===========================================================================
# §10.6  Summary keys: exit_b_immediate_off echo + count (Plan v3 §8)
# ===========================================================================

class TestFilterSummaryImmediateOffKeys:
    """§10.6 smoke: _compute_filter_diagnostics_summary correctly echoes
    exit_b_immediate_off (from config_arr[0]) and computes
    exit_b_immediate_off_count (sum of triggered_arr == 1).
    """

    def _call(self, filter_diagnostics: dict) -> dict:
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        return _compute_filter_diagnostics_summary(filter_diagnostics)

    def test_flag_true_echoed_in_summary(self):
        n = 8
        fd = {
            "exit_b_immediate_off_config": np.ones(n, dtype=np.int8),
            "exit_b_immediate_off_triggered": np.zeros(n, dtype=np.int8),
        }
        summary = self._call(fd)
        assert summary.get("exit_b_immediate_off") is True

    def test_flag_false_echoed_in_summary(self):
        n = 8
        fd = {
            "exit_b_immediate_off_config": np.zeros(n, dtype=np.int8),
            "exit_b_immediate_off_triggered": np.zeros(n, dtype=np.int8),
        }
        summary = self._call(fd)
        assert summary.get("exit_b_immediate_off") is False

    def test_count_equals_sum_of_triggered(self):
        n = 10
        triggered = np.zeros(n, dtype=np.int8)
        triggered[3] = 1
        triggered[7] = 1
        fd = {
            "exit_b_immediate_off_config": np.ones(n, dtype=np.int8),
            "exit_b_immediate_off_triggered": triggered,
        }
        summary = self._call(fd)
        assert summary.get("exit_b_immediate_off_count") == 2

    def test_absent_arrays_produce_no_keys(self):
        summary = self._call({})
        assert "exit_b_immediate_off" not in summary
        assert "exit_b_immediate_off_count" not in summary

    def test_summary_cols_snapshot_includes_new_keys(self):
        """Contract snapshot: _FILTER_SUMMARY_COLUMNS must contain new keys (Plan v3 §8)."""
        from wf_grid.collect.step_collector import _FILTER_SUMMARY_COLUMNS
        assert "exit_b_immediate_off" in _FILTER_SUMMARY_COLUMNS
        assert "exit_b_immediate_off_count" in _FILTER_SUMMARY_COLUMNS
