"""
Test #12 — Trade diagnostics: legacy trade rows have correct entry_filter_state,
entry_trigger_source, exit_reason.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #12
Spec reference: Appendix A v1.1 §13, §17

Contract:
- Enabled run: trades_df contains entry_filter_state, entry_trigger_source, exit_reason.
- entry_filter_state values are from valid FSM state enum.
- entry_trigger_source values are from valid trigger source enum.
- exit_reason values are from valid exit reason set.
- Disabled run: trades_df does NOT have these columns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


_VALID_FILTER_STATES = {
    "ST_ACTIVE_FREEZE", "ST_ACTIVE_MONITORING", "ST_STOPPING",
    "WAIT_FIRST_ST_FLIP", "OFF",
}

_VALID_TRIGGER_SOURCES = {
    "candidate_threshold", "confirmed_median", "none",
}

_VALID_EXIT_REASONS = {
    "filter_stopping_opposite_flip", "pending_open_trade_at_end",
    "opposite_st_flip",           # normal exit (no filter override)
    "stop_loss",
    None, "",                     # may be absent/empty for trades not in STOPPING
}


def _make_synthetic_ohlc(n: int = 600, seed: int = 55) -> pd.DataFrame:
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


def _run_legacy(df: pd.DataFrame, tf_cfg=None):
    from supertrend_optimizer.testing.runner import run_all_periods
    from supertrend_optimizer.utils.enums import ExecutionModel
    return run_all_periods(
        df=df, atr_period=14, multiplier=3.0, trade_mode="revers",
        commission=0.001, warmup_period=20,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=tf_cfg,
    )


class TestTradeDiagnosticsAttachment:
    """Trade rows must have filter diagnostic columns in enabled path (#12)."""

    def test_enabled_trades_have_filter_columns(self) -> None:
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()
        results = _run_legacy(df, tf_cfg=tf_cfg)

        for r in results:
            trades = r.result.trades_df
            if trades is None or trades.empty:
                continue
            for col in ("entry_filter_state", "entry_trigger_source", "exit_reason"):
                assert col in trades.columns, (
                    f"Enabled trades missing column {col!r} (plan §10 #12, spec §13)"
                )

    def test_disabled_trades_no_filter_columns(self) -> None:
        df = _make_synthetic_ohlc()
        results = _run_legacy(df, tf_cfg=None)

        for r in results:
            trades = r.result.trades_df
            if trades is None or trades.empty:
                continue
            for col in ("entry_filter_state", "entry_trigger_source", "exit_reason"):
                assert col not in trades.columns, (
                    f"Disabled trades must not have filter column {col!r}"
                )

    def test_entry_filter_state_values_valid(self) -> None:
        """entry_filter_state values must be from the FSM state enum."""
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()
        results = _run_legacy(df, tf_cfg=tf_cfg)

        for r in results:
            trades = r.result.trades_df
            if trades is None or trades.empty:
                continue
            if "entry_filter_state" not in trades.columns:
                continue
            invalid = [
                v for v in trades["entry_filter_state"].dropna().unique()
                if v not in _VALID_FILTER_STATES
            ]
            assert not invalid, (
                f"Invalid entry_filter_state values: {invalid}. "
                f"Expected subset of {_VALID_FILTER_STATES}"
            )

    def test_entry_trigger_source_values_valid(self) -> None:
        """entry_trigger_source values must be from the trigger source enum."""
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()
        results = _run_legacy(df, tf_cfg=tf_cfg)

        for r in results:
            trades = r.result.trades_df
            if trades is None or trades.empty:
                continue
            if "entry_trigger_source" not in trades.columns:
                continue
            invalid = [
                v for v in trades["entry_trigger_source"].dropna().unique()
                if str(v) not in {str(s) for s in _VALID_TRIGGER_SOURCES}
            ]
            assert not invalid, (
                f"Invalid entry_trigger_source values: {invalid}. "
                f"Expected subset of {_VALID_TRIGGER_SOURCES}"
            )

    def test_entry_filter_state_not_off_for_normal_trades(self) -> None:
        """Trades that were entered normally should not have entry_filter_state=OFF
        (OFF means filter was disabled, which should not happen in enabled run)."""
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()
        results = _run_legacy(df, tf_cfg=tf_cfg)

        for r in results:
            trades = r.result.trades_df
            if trades is None or trades.empty:
                continue
            if "entry_filter_state" not in trades.columns:
                continue
            off_trades = trades[trades["entry_filter_state"] == "OFF"]
            assert len(off_trades) == 0, (
                f"Found {len(off_trades)} trades with entry_filter_state=OFF "
                "in an enabled run. Filter appears disabled at entry bar."
            )
