"""
Test #30 — ZigZag_Trigger_Events reconstruction invariant.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #30
Spec reference: Appendix A v1.1 §9.2.1, spec §13, §15.3

Contract (per plan audit-fix v0.5):
Forward invariant:
    For each t where trade_filter_trigger_source[t] != "none":
        state_arr[t-1] == "OFF"  (the trigger bar follows an OFF bar)
        OR t == 0 (first bar)

Reverse invariant:
    For each t where state_arr[t-1] == "OFF" AND state_arr[t] != "OFF":
        trade_filter_trigger_source[t] != "none"
        (OFF → non-OFF transition marks a lifecycle start, which requires a trigger)

Additional:
- confirmed_legs_since_start delta heuristic must NOT be used for trigger reconstruction.
- All trigger_source != "none" rows must correspond exactly to OFF→non-OFF transitions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


def _make_synthetic_ohlc(n: int = 800, seed: int = 93) -> pd.DataFrame:
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


def _run(df: pd.DataFrame):
    from supertrend_optimizer.testing.runner import run_period
    from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
    from supertrend_optimizer.utils.enums import ExecutionModel
    cfg = _make_enabled_cfg()
    stats = build_zigzag_global_stats(df["close"].values, cfg)
    return run_period(
        df=df, atr_period=14, multiplier=3.0,
        trade_mode="revers", commission=0.001,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
    )


class TestTriggerReconstruction:
    """ZigZag_Trigger_Events reconstruction invariant (#30)."""

    def test_forward_trigger_preceded_by_off_state(self) -> None:
        """Forward invariant: each trigger bar must follow an OFF bar (or be bar 0)."""
        df = _make_synthetic_ohlc()
        r = _run(df)

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        state_arr = fd["trade_filter_state"]
        trigger_source = fd["trade_filter_trigger_source"]
        n = len(state_arr)

        violations = []
        for t in range(n):
            if str(trigger_source[t]) == "none":
                continue
            if t == 0:
                continue  # bar 0 is always OK
            prev_state = str(state_arr[t - 1])
            if prev_state != "OFF":
                violations.append(
                    f"  bar={t}: trigger_source={trigger_source[t]!r} "
                    f"but state[t-1]={prev_state!r} (expected OFF)"
                )

        assert not violations, (
            "Forward trigger invariant violated (plan §9.2.1):\n"
            + "\n".join(violations[:10])
            + (f"\n  ... ({len(violations)} total)" if len(violations) > 10 else "")
        )

    def test_reverse_off_to_non_off_implies_trigger(self) -> None:
        """Reverse invariant: OFF→non-OFF transition must have a trigger at that bar."""
        df = _make_synthetic_ohlc()
        r = _run(df)

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        state_arr = fd["trade_filter_state"]
        trigger_source = fd["trade_filter_trigger_source"]
        n = len(state_arr)

        violations = []
        for t in range(1, n):
            prev = str(state_arr[t - 1])
            curr = str(state_arr[t])
            if prev == "OFF" and curr != "OFF":
                ts = str(trigger_source[t])
                if ts == "none":
                    violations.append(
                        f"  bar={t}: OFF→{curr} transition but "
                        f"trigger_source='none' (expected a trigger)"
                    )

        assert not violations, (
            "Reverse trigger invariant violated (plan §9.2.1):\n"
            + "\n".join(violations[:10])
            + (f"\n  ... ({len(violations)} total)" if len(violations) > 10 else "")
        )

    def test_trigger_count_matches_off_transitions(self) -> None:
        """Number of trigger bars must equal number of OFF→non-OFF transitions."""
        df = _make_synthetic_ohlc()
        r = _run(df)

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        state_arr = fd["trade_filter_state"]
        trigger_source = fd["trade_filter_trigger_source"]
        n = len(state_arr)

        n_triggers = int(np.sum(trigger_source != "none"))
        # Count OFF→non-OFF transitions (starting at bar 1)
        n_transitions = sum(
            1 for t in range(1, n)
            if str(state_arr[t - 1]) == "OFF" and str(state_arr[t]) != "OFF"
        )
        # Bar 0 trigger (if any) counts separately
        if n > 0 and str(trigger_source[0]) != "none":
            n_transitions += 1

        assert n_triggers == n_transitions, (
            f"Trigger count ({n_triggers}) != OFF→non-OFF transitions ({n_transitions}). "
            "Reconstruction formula is not consistent with state_arr transitions."
        )

    def test_build_zigzag_trigger_events_df_uses_trigger_source(self) -> None:
        """_build_zigzag_trigger_events_df must use trigger_source != 'none' as marker."""
        from supertrend_optimizer.io.excel_tester import _build_zigzag_trigger_events_df

        df = _make_synthetic_ohlc()
        r = _run(df)

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        trigger_source = fd["trade_filter_trigger_source"]
        expected_count = int(np.sum(trigger_source != "none"))

        events_df = _build_zigzag_trigger_events_df(fd)
        assert len(events_df) == expected_count, (
            f"_build_zigzag_trigger_events_df returned {len(events_df)} rows, "
            f"expected {expected_count} (count of trigger_source != 'none')"
        )

    def test_trigger_bars_in_trigger_events_match_state(self) -> None:
        """Each row in ZigZag_Trigger_Events must correspond to an OFF→non-OFF bar."""
        from supertrend_optimizer.io.excel_tester import _build_zigzag_trigger_events_df

        df = _make_synthetic_ohlc()
        r = _run(df)

        fd = r.filter_diagnostics
        if fd is None:
            pytest.skip("No filter_diagnostics")

        state_arr = fd["trade_filter_state"]
        n = len(state_arr)

        events_df = _build_zigzag_trigger_events_df(fd)
        if events_df.empty:
            pytest.skip("No trigger events in this run")

        for _, row in events_df.iterrows():
            t = int(row["Trigger Bar"])
            if t == 0:
                continue  # bar 0 is OK
            prev = str(state_arr[t - 1])
            assert prev == "OFF", (
                f"Trigger at bar {t}: state[t-1]={prev!r}, expected OFF. "
                "ZigZag_Trigger_Events row does not correspond to OFF→non-OFF transition."
            )
