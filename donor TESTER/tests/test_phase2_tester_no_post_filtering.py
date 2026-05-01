"""
Test #7 — No post-filtering: extract_trades always called with filtered_positions;
no post-extract deletion of trade rows.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #7
Spec reference: Appendix A v1.1 §10, §16

Contract:
- In the enabled path, positions fed to extract_trades already carry filter decisions
  (positions array IS the filtered positions).
- The trade count in the enabled path MUST be <= disabled path trade count
  (filter can only block entries, never add new ones).
- Trades in the enabled path each have a valid entry_filter_state.
- No "phantom" trades exist: every trade's entry bar had filter_allowed_entry=True.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


def _make_synthetic_ohlc(n: int = 600, seed: int = 99) -> pd.DataFrame:
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


def _run_period(df, tf_cfg=None):
    from supertrend_optimizer.testing.runner import run_period
    from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
    from supertrend_optimizer.utils.enums import ExecutionModel
    stats = None
    if tf_cfg is not None and tf_cfg.enabled:
        stats = build_zigzag_global_stats(df["close"].values, tf_cfg)
    return run_period(
        df=df, atr_period=14, multiplier=3.0,
        trade_mode="revers", commission=0.001,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=tf_cfg,
        zigzag_global_stats=stats,
    )


class TestNoPostFiltering:
    """Verify that filter runs before extract_trades, no post-hoc deletion (plan §10 #7)."""

    def test_enabled_trade_count_leq_disabled(self) -> None:
        """Filter can only block entries → enabled trades ≤ disabled trades."""
        df = _make_synthetic_ohlc()
        r_disabled = _run_period(df, tf_cfg=None)
        r_enabled = _run_period(df, tf_cfg=_make_enabled_cfg())

        n_disabled = r_disabled.metrics.get("num_trades", 0)
        n_enabled = r_enabled.metrics.get("num_trades", 0)
        assert n_enabled <= n_disabled, (
            f"Enabled filter should not ADD trades: disabled={n_disabled}, "
            f"enabled={n_enabled}. Post-filtering detected?"
        )

    def test_every_trade_entry_had_filter_allowed(self) -> None:
        """Every trade entry bar must have filter_allowed_entry=True (no phantom trades)."""
        df = _make_synthetic_ohlc()
        r = _run_period(df, tf_cfg=_make_enabled_cfg())

        trades = r.result.trades_df
        if trades is None or trades.empty:
            pytest.skip("No trades in enabled run for this synthetic data")

        fd = r.filter_diagnostics
        assert fd is not None
        allowed = fd["filter_allowed_entry"]  # 1=allowed, 0=blocked

        # For each trade: entry_signal_bar = max(entry_index - 1, 0)
        for _, trow in trades.iterrows():
            ei = trow.get("entry_index")
            if ei is None or (isinstance(ei, float) and np.isnan(ei)):
                continue
            signal_bar = max(int(ei) - 1, 0)
            assert allowed[signal_bar] == 1, (
                f"Trade {trow.get('trade_id')}: entry_index={ei}, "
                f"signal_bar={signal_bar} has filter_allowed_entry=0 "
                "(phantom trade — filter ran AFTER extract_trades)"
            )

    def test_positions_array_is_filtered_before_trades_extraction(self) -> None:
        """Enabled positions must differ from raw positions (filter ran before extract)."""
        from supertrend_optimizer.core.backtest import generate_positions
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = _make_synthetic_ohlc()
        r = _run_period(df, tf_cfg=_make_enabled_cfg())

        raw_positions = generate_positions(
            r.result.trend, r.result.trade_mode, ExecutionModel.OPEN_TO_OPEN
        )
        filtered_positions = r.result.positions

        diff_count = int(np.sum(raw_positions != filtered_positions))
        assert diff_count > 0, (
            "Filter did not change any positions — either filter bypass or "
            "no ST flips blocked. Check that positions are filtered BEFORE extract_trades."
        )

    def test_no_filter_columns_in_disabled_trades(self) -> None:
        """Disabled path: trade rows must not have filter columns (no residual filter code)."""
        df = _make_synthetic_ohlc()
        r = _run_period(df, tf_cfg=None)
        trades = r.result.trades_df
        if trades is None or trades.empty:
            return
        for col in ("entry_filter_state", "entry_trigger_source", "exit_reason"):
            assert col not in trades.columns
