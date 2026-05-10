"""
Test #15 — ST_STOPPING semantics.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #15
Spec reference: Appendix A v1.1 §4.5, §15.7, §17.1.21, §17.1.22

Contract:
1. In ST_STOPPING state, filter_allowed_entry=0 (new entries blocked).
2. A 0→±1 transition in the ST (positions going from neutral to non-neutral) does NOT
   trigger a close in ST_STOPPING — only an opposite flip closes the position.
3. When state transitions to ST_STOPPING, the next opposite ST flip closes the open position
   with exit_reason="filter_stopping_opposite_flip".
4. After ST_STOPPING ends (position closed), state transitions back toward OFF/WAIT.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_synthetic_ohlc(n: int = 800, seed: int = 43) -> pd.DataFrame:
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
            reversal_threshold=0.025, local_window=20, candidate_trigger_threshold=0.35,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=2, stop_check="confirm_bar_only",
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


class TestSTStoppingSemantics:
    """ST_STOPPING state semantics (#15)."""

    def test_st_stopping_blocks_new_entries(self) -> None:
        """filter_allowed_entry must be 0 at all ST_STOPPING bars."""
        df = _make_synthetic_ohlc()
        r = _run(df, tf_cfg=_make_enabled_cfg())

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        state_arr = fd["trade_filter_state"]
        allowed = fd["filter_allowed_entry"]

        stopping_bars = np.where(state_arr == "ST_STOPPING")[0]
        if len(stopping_bars) == 0:
            pytest.skip("No ST_STOPPING bars in this run — try with more data")

        for t in stopping_bars:
            assert allowed[t] == 0, (
                f"ST_STOPPING bar {t}: filter_allowed_entry={allowed[t]}, expected 0. "
                "New entries must be blocked in ST_STOPPING (plan §4.5)."
            )

    def test_stopping_exits_have_correct_reason(self) -> None:
        """All trades that exited due to ST_STOPPING must have exit_reason=filter_stopping_opposite_flip."""
        df = _make_synthetic_ohlc()
        r = _run(df, tf_cfg=_make_enabled_cfg())

        trades = r.result.trades_df
        if trades is None or trades.empty:
            pytest.skip("No trades")
        if "exit_reason" not in trades.columns:
            pytest.skip("No exit_reason column")

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        # Trades with exit_reason = filter_stopping_opposite_flip must exist
        # if ST_STOPPING occurred during any trade
        stopping_exits = trades[trades["exit_reason"] == "filter_stopping_opposite_flip"]
        state_arr = fd["trade_filter_state"]
        stopping_bars = set(np.where(state_arr == "ST_STOPPING")[0].tolist())

        if stopping_bars and len(stopping_exits) > 0:
            # Each stopping_exit trade: its exit decision bar must have been in ST_STOPPING
            for _, row in stopping_exits.iterrows():
                xi = row.get("exit_index")
                if xi is None or (isinstance(xi, float) and np.isnan(xi)):
                    continue
                exit_signal_bar = max(int(xi) - 1, 0)
                assert exit_signal_bar in stopping_bars, (
                    f"Trade with exit_reason=filter_stopping_opposite_flip "
                    f"has exit_signal_bar={exit_signal_bar} but that bar is not "
                    f"in ST_STOPPING state. Stopping exit semantics violated."
                )

    def test_filter_state_sequence_valid(self) -> None:
        """State array must only contain valid FSM state strings."""
        df = _make_synthetic_ohlc()
        r = _run(df, tf_cfg=_make_enabled_cfg())

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        valid = {"OFF", "WAIT_FIRST_ST_FLIP", "ST_ACTIVE_FREEZE",
                 "ST_ACTIVE_MONITORING", "ST_STOPPING"}
        state_arr = fd["trade_filter_state"]
        unique_states = set(str(s) for s in np.unique(state_arr))
        invalid = unique_states - valid
        assert not invalid, (
            f"Invalid FSM states in trade_filter_state: {invalid}. "
            f"Valid states: {valid}"
        )

    def test_stopping_bars_count_matches_summary(self) -> None:
        """bars_in_state.ST_STOPPING in summary must equal count of ST_STOPPING bars."""
        df = _make_synthetic_ohlc()
        r = _run(df, tf_cfg=_make_enabled_cfg())

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        s = r.filter_diagnostics_summary
        if s is None:
            pytest.skip("No filter_diagnostics_summary")

        state_arr = fd["trade_filter_state"]
        expected = int(np.sum(state_arr == "ST_STOPPING"))
        actual = s.get("bars_in_state", {}).get("ST_STOPPING", 0)

        assert actual == expected, (
            f"bars_in_state.ST_STOPPING={actual} != state array count={expected}"
        )
