"""
Test #13 — OPEN_TO_OPEN indexing pinned.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #13
Spec reference: Appendix A v1.1 §10, §15.4

Contract (OPEN_TO_OPEN execution model):
1. entry_signal_idx = max(entry_index - 1, 0)  — decision bar for the entry
2. exit_signal_idx  = max(exit_index  - 1, 0)  — decision bar for the exit
3. ST_STOPPING on decision bar → exit_reason="filter_stopping_opposite_flip"
   even if execution bar is already beyond STOPPING.
4. Edge-case: exit_index >= len(positions)-1 → exit_reason="pending_open_trade_at_end"
   (trade still open at last bar).
5. filter_allowed_entry[entry_signal_idx] == 1 for every trade that was entered.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


def _make_synthetic_ohlc(n: int = 600, seed: int = 31) -> pd.DataFrame:
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


def _run(df: pd.DataFrame, tf_cfg=None):
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


class TestOpenToOpenIndexing:
    """OPEN_TO_OPEN signal bar indexing contract (#13)."""

    def test_entry_signal_bar_formula(self) -> None:
        """entry_signal_idx = max(entry_index - 1, 0) for every trade."""
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()
        r = _run(df, tf_cfg=tf_cfg)

        trades = r.result.trades_df
        if trades is None or trades.empty:
            pytest.skip("No trades in this run")

        fd = r.filter_diagnostics
        assert fd is not None
        allowed = fd["filter_allowed_entry"]

        for _, row in trades.iterrows():
            ei = row.get("entry_index")
            if ei is None or (isinstance(ei, float) and np.isnan(ei)):
                continue
            signal_bar = max(int(ei) - 1, 0)
            # The signal bar must have been allowed
            assert allowed[signal_bar] == 1, (
                f"Trade entry_index={ei}: signal_bar={signal_bar} "
                f"has filter_allowed_entry=0. "
                "OPEN_TO_OPEN indexing formula violated."
            )

    def test_exit_signal_bar_formula_for_stopping_exits(self) -> None:
        """filter_stopping_opposite_flip trades: exit decision bar must have
        state_at_bar_start in {ST_STOPPING}. trade_filter_state may already be
        OFF after the same-bar close is applied."""
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()
        r = _run(df, tf_cfg=tf_cfg)

        trades = r.result.trades_df
        if trades is None or trades.empty:
            pytest.skip("No trades")

        if "exit_reason" not in trades.columns:
            pytest.skip("No exit_reason column (disabled path?)")

        fd = r.filter_diagnostics
        state_arr = fd.get("trade_filter_state") if fd else None
        state_at_start_arr = fd.get("state_at_bar_start") if fd else None
        if state_arr is None or state_at_start_arr is None:
            pytest.skip("No trade_filter_state/state_at_bar_start")

        n = len(state_arr)
        stopping_trades = trades[trades["exit_reason"] == "filter_stopping_opposite_flip"]
        if stopping_trades.empty:
            pytest.skip("No filter_stopping_opposite_flip trades in this run")

        for _, row in stopping_trades.iterrows():
            xi = row.get("exit_index")
            if xi is None or (isinstance(xi, float) and np.isnan(xi)):
                continue
            signal_bar = max(int(xi) - 1, 0)
            if signal_bar >= n:
                continue  # edge of array
            state_at_start = _state_name(state_at_start_arr[signal_bar])
            state_after = str(state_arr[signal_bar])
            assert state_at_start == "ST_STOPPING", (
                f"filter_stopping_opposite_flip trade exit_index={xi}: "
                f"state_at_bar_start at signal_bar={signal_bar} is "
                f"{state_at_start!r}, expected ST_STOPPING "
                f"(trade_filter_state after bar is {state_after!r}). "
                "OPEN_TO_OPEN exit indexing formula violated."
            )

    def test_pending_open_trade_exit_reason_for_last_bar(self) -> None:
        """If a trade is open at the last bar (exit_index >= len-1), exit_reason
        must be 'pending_open_trade_at_end' (plan §13 audit-fix defensive >=)."""
        df = _make_synthetic_ohlc()
        tf_cfg = _make_enabled_cfg()
        r = _run(df, tf_cfg=tf_cfg)

        trades = r.result.trades_df
        if trades is None or trades.empty:
            pytest.skip("No trades")
        if "exit_reason" not in trades.columns:
            pytest.skip("No exit_reason column")

        n = len(r.result.positions)
        last_bar_trades = trades[trades["exit_index"] >= n - 1]
        if last_bar_trades.empty:
            pytest.skip("No trades open at last bar")

        for _, row in last_bar_trades.iterrows():
            assert row["exit_reason"] == "pending_open_trade_at_end", (
                f"Trade with exit_index={row['exit_index']} (at last bar n={n}) "
                f"has exit_reason={row['exit_reason']!r}, expected 'pending_open_trade_at_end'"
            )

    def test_entry_index_positive_for_all_trades(self) -> None:
        """entry_index >= 1 for all OPEN_TO_OPEN trades (entry is on open of next bar)."""
        df = _make_synthetic_ohlc()
        r = _run(df, tf_cfg=None)

        trades = r.result.trades_df
        if trades is None or trades.empty:
            pytest.skip("No trades in disabled run")

        invalid = trades[trades["entry_index"] < 1]
        assert invalid.empty, (
            f"OPEN_TO_OPEN trades with entry_index < 1: {invalid[['trade_id','entry_index']]}"
        )


def _state_name(value) -> str:
    if isinstance(value, str):
        return value
    return {
        0: "OFF",
        1: "WAIT_FIRST_ST_FLIP",
        2: "ST_ACTIVE_FREEZE",
        3: "ST_ACTIVE_MONITORING",
        4: "ST_STOPPING",
        5: "ST_COUNTING_ZZ_LEGS",
    }.get(int(value), "UNKNOWN")
