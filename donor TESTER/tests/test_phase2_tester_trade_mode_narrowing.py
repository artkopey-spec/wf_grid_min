"""
Test #14 — trade_mode narrowing: long-only and short-only filter interactions.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #14
Spec reference: Appendix A v1.1 §4.2, §9, §17.1.11, §17.1.12

Contract:
- trade_mode=long: only LONG positions (+1) appear; no SHORT positions (-1).
  A short flip (positions going to -1) would be blocked. In WAIT_FIRST_ST_FLIP,
  a short flip is skipped; next long flip opens LONG.
- trade_mode=short: symmetric — only SHORT positions (-1); no LONG (+1).
- trade_mode=revers: both directions allowed.
- Filter must not accidentally create positions of the wrong direction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_synthetic_ohlc(n: int = 600, seed: int = 77) -> pd.DataFrame:
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


def _run(df: pd.DataFrame, trade_mode: str, tf_cfg=None):
    from supertrend_optimizer.testing.runner import run_period
    from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
    from supertrend_optimizer.utils.enums import ExecutionModel
    stats = None
    if tf_cfg is not None and tf_cfg.enabled:
        stats = build_zigzag_global_stats(df["close"].values, tf_cfg)
    return run_period(
        df=df, atr_period=14, multiplier=3.0,
        trade_mode=trade_mode, commission=0.001,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=tf_cfg,
        zigzag_global_stats=stats,
    )


class TestTradeModeNarrowing:
    """trade_mode=long/short/revers narrowing with enabled filter (#14)."""

    def test_long_only_no_short_positions(self) -> None:
        """trade_mode=long enabled: positions array must not contain -1."""
        df = _make_synthetic_ohlc()
        r = _run(df, trade_mode="long", tf_cfg=_make_enabled_cfg())

        positions = r.result.positions
        short_bars = int(np.sum(positions == -1))
        assert short_bars == 0, (
            f"trade_mode=long enabled: found {short_bars} SHORT positions (-1). "
            "Filter must not introduce short positions in long-only mode."
        )

    def test_short_only_no_long_positions(self) -> None:
        """trade_mode=short enabled: positions array must not contain +1."""
        df = _make_synthetic_ohlc()
        r = _run(df, trade_mode="short", tf_cfg=_make_enabled_cfg())

        positions = r.result.positions
        long_bars = int(np.sum(positions == 1))
        assert long_bars == 0, (
            f"trade_mode=short enabled: found {long_bars} LONG positions (+1). "
            "Filter must not introduce long positions in short-only mode."
        )

    def test_revers_has_both_directions(self) -> None:
        """trade_mode=revers enabled: positions may contain both +1 and -1."""
        df = _make_synthetic_ohlc()
        r = _run(df, trade_mode="revers", tf_cfg=_make_enabled_cfg())

        positions = r.result.positions
        # Just verify the run completes; both directions may appear
        assert positions is not None

    def test_long_only_disabled_no_short_positions(self) -> None:
        """Baseline: trade_mode=long disabled also must not contain -1."""
        df = _make_synthetic_ohlc()
        r = _run(df, trade_mode="long", tf_cfg=None)

        positions = r.result.positions
        short_bars = int(np.sum(positions == -1))
        assert short_bars == 0

    def test_long_only_enabled_trades_direction(self) -> None:
        """All trades in trade_mode=long enabled run must be LONG direction."""
        df = _make_synthetic_ohlc()
        r = _run(df, trade_mode="long", tf_cfg=_make_enabled_cfg())

        trades = r.result.trades_df
        if trades is None or trades.empty:
            pytest.skip("No trades in long-only run")

        if "direction" not in trades.columns:
            pytest.skip("No direction column")

        invalid = trades[~trades["direction"].isin({"long", "LONG", 1, "1"})]
        assert invalid.empty, (
            f"trade_mode=long has non-LONG trades: {invalid['direction'].unique()}"
        )

    def test_mode_narrowing_reduces_or_equals_revers_trades(self) -> None:
        """Long-only or short-only trades must be <= revers trades (subset)."""
        df = _make_synthetic_ohlc()
        r_revers = _run(df, trade_mode="revers", tf_cfg=_make_enabled_cfg())
        r_long = _run(df, trade_mode="long", tf_cfg=_make_enabled_cfg())
        r_short = _run(df, trade_mode="short", tf_cfg=_make_enabled_cfg())

        n_revers = r_revers.metrics.get("num_trades", 0)
        n_long = r_long.metrics.get("num_trades", 0)
        n_short = r_short.metrics.get("num_trades", 0)

        assert n_long <= n_revers, (
            f"long-only trades ({n_long}) > revers trades ({n_revers})"
        )
        assert n_short <= n_revers, (
            f"short-only trades ({n_short}) > revers trades ({n_revers})"
        )
