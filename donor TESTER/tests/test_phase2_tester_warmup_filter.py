"""
Test #26 — Warmup × filter parametric.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #26
Spec reference: Appendix A v1.1 §15.1, §17.6

Contract (parametrised over warmup_period in [0, 50, 200]):
1. FSM starts at bar 0 regardless of warmup: trade_filter_enabled[0] == 1.
2. filter_diagnostics length == len(positions) for every warmup variant.
3. Two consecutive runs with the same warmup produce bit-identical positions/trades.
4. Metrics (num_trades, sum_pnl_pct) are computed AFTER warmup (canonical donor behavior).

Guard: protects against accidental FSM shift to effective_warmup (plan §15.1 violation).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


def _make_synthetic_ohlc(n: int = 800, seed: int = 88) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    noise = rng.uniform(0.001, 0.004, size=n)
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.clip(close * (1 + rng.uniform(-0.002, 0.002, size=n)), low, high)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def _make_enabled_cfg():
    from supertrend_optimizer.core.trade_filter_config import (
        TradeFilterConfig, TradeFilterZigZagConfig, TradeFilterTriggersConfig,
        TradeFilterLifecycleConfig, TradeFilterDiagnosticsConfig,
        TradeFilterTriggerToggleConfig,
    )
    return TradeFilterConfig(
        enabled=True, type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            enabled=True,
            reversal_threshold=0.03, local_window=20, candidate_trigger_threshold=0.4,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=3, stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
        ),
        diagnostics=TradeFilterDiagnosticsConfig(
            export_state_columns=True, export_trigger_columns=True,
        ),
    )


def _run(df: pd.DataFrame, warmup_period: int, tf_cfg=None):
    from supertrend_optimizer.testing.runner import run_period
    from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
    from supertrend_optimizer.utils.enums import ExecutionModel
    stats = None
    if tf_cfg is not None and tf_cfg.enabled:
        stats = build_zigzag_global_stats(df["close"].values, tf_cfg)
    return run_period(
        df=df, atr_period=14, multiplier=3.0,
        trade_mode="revers", commission=0.001,
        warmup_period=warmup_period,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=tf_cfg,
        zigzag_global_stats=stats,
    )


class TestWarmupFilter:
    """Warmup × filter parametric tests (#26)."""

    @pytest.mark.parametrize("warmup", [0, 50, 200])
    def test_fsm_starts_at_bar_zero_regardless_of_warmup(self, warmup: int) -> None:
        """trade_filter_enabled[0] == 1 for any warmup (plan §15.1)."""
        df = _make_synthetic_ohlc()
        r = _run(df, warmup_period=warmup, tf_cfg=_make_enabled_cfg())

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip(f"No filter_diagnostics for warmup={warmup}")

        enabled_arr = fd["trade_filter_enabled"]
        assert enabled_arr[0] == 1, (
            f"warmup={warmup}: trade_filter_enabled[0]={enabled_arr[0]}, expected 1. "
            "FSM must start at bar 0 regardless of warmup (plan §15.1)."
        )

    @pytest.mark.parametrize("warmup", [0, 50, 200])
    def test_diagnostics_length_matches_positions(self, warmup: int) -> None:
        """filter_diagnostics length == len(positions) for each warmup."""
        df = _make_synthetic_ohlc()
        r = _run(df, warmup_period=warmup, tf_cfg=_make_enabled_cfg())

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip(f"No filter_diagnostics for warmup={warmup}")

        n_pos = len(r.result.positions)
        for key, arr in fd.items():
            assert len(arr) == n_pos, (
                f"warmup={warmup}: filter_diagnostics[{key!r}] len={len(arr)} "
                f"!= positions len={n_pos}"
            )

    @pytest.mark.parametrize("warmup", [0, 50, 200])
    def test_two_consecutive_runs_deterministic(self, warmup: int) -> None:
        """Two runs with the same warmup must produce bit-identical positions."""
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()

        r1 = _run(df, warmup_period=warmup, tf_cfg=tf_cfg)
        r2 = _run(df, warmup_period=warmup, tf_cfg=tf_cfg)

        np.testing.assert_array_equal(
            r1.result.positions, r2.result.positions,
            err_msg=f"warmup={warmup}: positions not deterministic between runs"
        )

    def test_warmup_affects_metric_window_not_fsm(self) -> None:
        """Warmup affects which bars are counted for metrics, not the FSM start.

        With a larger warmup, fewer trades are counted (early trades excluded).
        But filter_diagnostics still starts at bar 0.
        """
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()

        r0 = _run(df, warmup_period=0, tf_cfg=tf_cfg)
        r200 = _run(df, warmup_period=200, tf_cfg=tf_cfg)

        # Positions should differ (different warmup → different metric window)
        # or at least the metric values differ (warmup excludes early trades)
        n0 = r0.metrics.get("num_trades", 0)
        n200 = r200.metrics.get("num_trades", 0)

        # With warmup=200, num_trades should be <= warmup=0 (fewer bars counted)
        assert n200 <= n0, (
            f"warmup=200 has more trades ({n200}) than warmup=0 ({n0}). "
            "Larger warmup should exclude more early trades."
        )

        # But FSM still starts at 0 in both cases
        for warmup, r in [(0, r0), (200, r200)]:
            fd = r.filter_diagnostics
            if fd is not None:
                assert fd["trade_filter_enabled"][0] == 1, (
                    f"warmup={warmup}: FSM must start at bar 0"
                )

    def test_disabled_warmup_variants_stable(self) -> None:
        """Disabled path with any warmup must produce stable results."""
        df = _make_synthetic_ohlc()
        for warmup in [0, 50, 200]:
            r = _run(df, warmup_period=warmup, tf_cfg=None)
            assert r.filter_diagnostics is None
            assert r.result.positions is not None
