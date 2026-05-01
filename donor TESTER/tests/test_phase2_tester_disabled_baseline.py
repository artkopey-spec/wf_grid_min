"""
Tests #2/#3 — Disabled parity: absent block AND enabled=false produce baseline-identical results.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #2, #3
Spec reference: Appendix A v1.1 §11.1, §17.1.1

Contract:
- trade_filter block absent (trade_filter_config=None) → filter_diagnostics=None,
  metrics identical to the run with explicit enabled=False.
- trade_filter.enabled=False → same as absent; bit-identical positions, metrics, trades.
- Both paths must be equal to each other (cross-parity).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_synthetic_ohlc(n: int = 500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.010, n)))
    noise = rng.uniform(0.001, 0.003, size=n)
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.clip(close * (1 + rng.uniform(-0.002, 0.002, size=n)), low, high)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def _run(df: pd.DataFrame, tf_cfg=None):
    from supertrend_optimizer.testing.runner import run_all_periods
    from supertrend_optimizer.utils.enums import ExecutionModel
    return run_all_periods(
        df=df, atr_period=14, multiplier=3.0, trade_mode="revers",
        commission=0.001, warmup_period=20,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=tf_cfg,
    )


def _disabled_cfg():
    from supertrend_optimizer.core.trade_filter_config import TradeFilterConfig
    return TradeFilterConfig(enabled=False, type=None, zigzag=None,
                             triggers=None, lifecycle=None, diagnostics=None)


# ---------------------------------------------------------------------------
# Test #2 — absent trade_filter block → bit-identical to disabled
# ---------------------------------------------------------------------------

class TestAbsentBlockEqualsDisabled:
    """Absent trade_filter_config (None) must produce same results as explicit disabled."""

    def test_absent_produces_none_filter_diagnostics(self) -> None:
        df = _make_synthetic_ohlc()
        results = _run(df, tf_cfg=None)
        for r in results:
            assert r.filter_diagnostics is None, (
                "Absent trade_filter must produce filter_diagnostics=None (plan §11.1)"
            )

    def test_absent_produces_none_filter_diagnostics_summary(self) -> None:
        df = _make_synthetic_ohlc()
        results = _run(df, tf_cfg=None)
        for r in results:
            assert r.filter_diagnostics_summary is None

    def test_absent_vs_disabled_metrics_identical(self) -> None:
        """Absent block and enabled=False must give bit-identical metrics."""
        df = _make_synthetic_ohlc()
        res_absent = _run(df, tf_cfg=None)
        res_disabled = _run(df, tf_cfg=_disabled_cfg())

        for r_a, r_d in zip(res_absent, res_disabled):
            for key in ("num_trades", "sum_pnl_pct", "win_rate"):
                va = r_a.metrics.get(key)
                vd = r_d.metrics.get(key)
                if va is None or vd is None:
                    continue
                assert va == pytest.approx(vd, rel=1e-9), (
                    f"Metric {key!r}: absent={va} != disabled={vd}"
                )

    def test_absent_vs_disabled_positions_identical(self) -> None:
        df = _make_synthetic_ohlc()
        res_absent = _run(df, tf_cfg=None)
        res_disabled = _run(df, tf_cfg=_disabled_cfg())

        for r_a, r_d in zip(res_absent, res_disabled):
            np.testing.assert_array_equal(
                r_a.result.positions,
                r_d.result.positions,
                err_msg="Positions differ between absent and disabled filter path",
            )

    def test_absent_vs_disabled_trades_shape_identical(self) -> None:
        df = _make_synthetic_ohlc()
        res_absent = _run(df, tf_cfg=None)
        res_disabled = _run(df, tf_cfg=_disabled_cfg())

        for r_a, r_d in zip(res_absent, res_disabled):
            t_a = r_a.result.trades_df
            t_d = r_d.result.trades_df
            shape_a = t_a.shape if t_a is not None else (0, 0)
            shape_d = t_d.shape if t_d is not None else (0, 0)
            assert shape_a == shape_d, (
                f"Trades shape: absent={shape_a} != disabled={shape_d}"
            )


# ---------------------------------------------------------------------------
# Test #3 — enabled=False → identical to absent block
# ---------------------------------------------------------------------------

class TestEnabledFalseEqualsAbsent:
    """trade_filter.enabled=False must be bit-identical to absent block (plan §11.1)."""

    def test_disabled_flag_produces_none_filter_diagnostics(self) -> None:
        df = _make_synthetic_ohlc()
        results = _run(df, tf_cfg=_disabled_cfg())
        for r in results:
            assert r.filter_diagnostics is None

    def test_disabled_flag_produces_none_summary(self) -> None:
        df = _make_synthetic_ohlc()
        results = _run(df, tf_cfg=_disabled_cfg())
        for r in results:
            assert r.filter_diagnostics_summary is None

    def test_disabled_two_consecutive_runs_identical(self) -> None:
        """Two disabled runs must be deterministic (bit-identical metrics)."""
        df = _make_synthetic_ohlc()
        r1 = _run(df, tf_cfg=_disabled_cfg())
        r2 = _run(df, tf_cfg=_disabled_cfg())

        for pr1, pr2 in zip(r1, r2):
            np.testing.assert_array_equal(pr1.result.positions, pr2.result.positions)
            for key in ("num_trades", "sum_pnl_pct"):
                v1 = pr1.metrics.get(key)
                v2 = pr2.metrics.get(key)
                if v1 is not None and v2 is not None:
                    assert v1 == pytest.approx(v2, rel=1e-12)

    def test_disabled_trades_have_no_filter_columns(self) -> None:
        """Trades from disabled run must not have filter diagnostic columns."""
        df = _make_synthetic_ohlc()
        results = _run(df, tf_cfg=_disabled_cfg())
        for r in results:
            t = r.result.trades_df
            if t is None or t.empty:
                continue
            for col in ("entry_filter_state", "entry_trigger_source", "exit_reason"):
                assert col not in t.columns, (
                    f"Disabled trades must not have filter column {col!r}"
                )
