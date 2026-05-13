"""
WP-T4 — Tester runner integration (legacy) tests.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §6, §7.3
Spec reference: Appendix A v1.1 §10, §12, §13

Contract pinned by these tests:

1. run_period wires trade_filter_config / zigzag_global_stats / global_offset
   into run_single_backtest; BacktestResult.filter_diagnostics is not None.
2. PeriodResult.filter_diagnostics is forwarded from BacktestResult.
3. PeriodResult.filter_diagnostics_summary is built and has the required keys.
4. filter_diagnostics arrays each have length == len(result.positions).
5. filter_diagnostics_summary satisfies sanity invariants §3.3.2.
6. run_period fail-fast: enabled filter + zigzag_global_stats=None -> ConfigError.
7. run_all_periods materialises stats from df when not supplied (plan §7.3).
8. run_all_periods reuses the same stats object across all 5 slices.
9. period_global_offset = len(df) - n_period is correctly computed.
10. Disabled path: filter_diagnostics=None, filter_diagnostics_summary=None;
    metrics bit-identical to pre-Phase-2 baseline.
11. filter_diagnostics appears before (precedes) the returns sequence —
    sanity that the filter runs BEFORE metrics are calculated (Test #5).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers — synthetic OHLC dataset
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DONOR_ROOT = REPO_ROOT / "donor"


def _make_synthetic_ohlc(n: int = 600, seed: int = 42) -> pd.DataFrame:
    """Build a minimal synthetic OHLC DataFrame with realistic price action.

    Using a simple random-walk with a gradual upward drift so the SuperTrend
    indicator produces a meaningful, non-trivial trend series.

    n >= 200 gives the ZigZag engine enough bars to find confirmed legs.
    """
    rng = np.random.default_rng(seed)

    # Geometric random walk close prices
    log_returns = rng.normal(loc=0.0003, scale=0.012, size=n)
    close = 100.0 * np.exp(np.cumsum(log_returns))

    # Synthesise open / high / low around close
    noise = rng.uniform(0.001, 0.005, size=n)
    high = close * (1.0 + noise)
    low = close * (1.0 - noise)
    open_ = close * (1.0 + rng.uniform(-0.002, 0.002, size=n))
    open_ = np.clip(open_, low, high)

    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close},
        index=idx,
    )


def _make_trade_filter_config(
    reversal_threshold: float = 0.04,
    local_window: int = 20,
    candidate_trigger_threshold: float = 0.4,
    freeze_confirmed_legs: int = 3,
) -> Any:
    """Build a minimal valid enabled TradeFilterConfig."""
    from supertrend_optimizer.core.trade_filter_config import (
        TradeFilterConfig,
        TradeFilterDiagnosticsConfig,
        TradeFilterLifecycleConfig,
        TradeFilterTriggerToggleConfig,
        TradeFilterTriggersConfig,
        TradeFilterZigZagConfig,
    )

    return TradeFilterConfig(
        enabled=True,
        type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            enabled=True,
            reversal_threshold=reversal_threshold,
            local_window=local_window,
            candidate_trigger_threshold=candidate_trigger_threshold,
            candidate_trigger_quantile=None,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=freeze_confirmed_legs,
            stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
        ),
        diagnostics=TradeFilterDiagnosticsConfig(
            export_state_columns=True,
            export_trigger_columns=True,
        ),
    )


def _build_stats(df: pd.DataFrame, cfg: Any) -> Any:
    """Materialise ZigZagGlobalStats from the full df."""
    from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats

    return build_zigzag_global_stats(
        close=df["close"].values,
        trade_filter_config=cfg,
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def df_600() -> pd.DataFrame:
    return _make_synthetic_ohlc(n=600, seed=42)


@pytest.fixture(scope="module")
def cfg_enabled() -> Any:
    return _make_trade_filter_config()


@pytest.fixture(scope="module")
def stats_600(df_600: pd.DataFrame, cfg_enabled: Any) -> Any:
    return _build_stats(df_600, cfg_enabled)


@pytest.fixture(scope="module")
def period_result_enabled(df_600: pd.DataFrame, cfg_enabled: Any, stats_600: Any):
    """PeriodResult for the full 600-bar slice with filter enabled."""
    from supertrend_optimizer.testing.runner import run_period

    return run_period(
        df=df_600,
        atr_period=14,
        multiplier=3.0,
        trade_mode="revers",
        commission=0.001,
        warmup_period=30,
        periods_per_year=252.0,
        trade_filter_config=cfg_enabled,
        zigzag_global_stats=stats_600,
        global_offset=0,
    )


@pytest.fixture(scope="module")
def period_result_disabled(df_600: pd.DataFrame):
    """PeriodResult for the full 600-bar slice with filter disabled (None)."""
    from supertrend_optimizer.testing.runner import run_period

    return run_period(
        df=df_600,
        atr_period=14,
        multiplier=3.0,
        trade_mode="revers",
        commission=0.001,
        warmup_period=30,
        periods_per_year=252.0,
        trade_filter_config=None,
        zigzag_global_stats=None,
        global_offset=0,
    )


# ---------------------------------------------------------------------------
# Group 1: run_period — enabled path
# ---------------------------------------------------------------------------

class TestRunPeriodEnabledPath:
    """Test #5 (filter before returns), #10 (diagnostics length), #9 (stats)."""

    def test_filter_diagnostics_not_none_on_enabled_path(
        self, period_result_enabled
    ) -> None:
        """Test #5 variant: filter ran — filter_diagnostics is not None."""
        assert period_result_enabled.filter_diagnostics is not None, (
            "Enabled filter path must populate filter_diagnostics."
        )

    def test_filter_diagnostics_forwarded_from_backtest_result(
        self, period_result_enabled
    ) -> None:
        """PeriodResult.filter_diagnostics is the SAME object as BacktestResult."""
        assert (
            period_result_enabled.filter_diagnostics
            is period_result_enabled.result.filter_diagnostics
        )

    def test_filter_diagnostics_summary_not_none(
        self, period_result_enabled
    ) -> None:
        assert period_result_enabled.filter_diagnostics_summary is not None

    def test_filter_diagnostics_length_equals_positions(
        self, period_result_enabled
    ) -> None:
        """Test #10: every filter_diagnostics array has len == len(positions)."""
        n_pos = len(period_result_enabled.result.positions)
        diag = period_result_enabled.filter_diagnostics
        for key, arr in diag.items():
            assert len(arr) == n_pos, (
                f"filter_diagnostics[{key!r}] length {len(arr)} != "
                f"positions length {n_pos}."
            )

    def test_filter_diagnostics_required_keyset_present(
        self, period_result_enabled
    ) -> None:
        """Spec §13 minimum keyset must be present."""
        required = {
            "trade_filter_enabled",
            "trade_filter_state",
            "trade_filter_trigger_source",
            "zigzag_reversal_threshold",
            "candidate_height_pct",
            "candidate_trigger_threshold",
            "local_median_N",
            "local_median_available",
            "local_window",
            "global_median",
            "global_stats_available",
            "confirmed_legs_since_start",
            "freeze_confirmed_legs",
            "median_stop_triggered",
            "stopping_started_at_index",
            "filter_allowed_entry",
            "filter_block_reason",
            "trade_filter_state_code",
            "st_flip_dir",
        }
        actual = set(period_result_enabled.filter_diagnostics.keys())
        missing = required - actual
        assert not missing, (
            f"filter_diagnostics missing required spec §13 keys: {missing}"
        )

    def test_filter_before_returns_invariant(
        self, period_result_enabled
    ) -> None:
        """Test #5: filter positions differ from raw positions on at least 1 bar.

        This confirms the filter actually ran (modified some position), not that
        it was bypassed.  With 600 bars of random-walk data and a low reversal
        threshold, some entries will be blocked.
        """
        from supertrend_optimizer.core.backtest import generate_positions
        from supertrend_optimizer.utils.enums import ExecutionModel

        positions_raw = generate_positions(
            period_result_enabled.result.trend,
            period_result_enabled.result.trade_mode,
            ExecutionModel.OPEN_TO_OPEN,
        )
        positions_filtered = period_result_enabled.result.positions
        diff = int(np.sum(positions_raw != positions_filtered))
        assert diff > 0, (
            "Filter did not change any positions on the enabled path. "
            "Either the filter is not wired or the synthetic data has no "
            "ST flips. Check run_period parameter forwarding."
        )

    def test_n_bars_correct(self, period_result_enabled, df_600) -> None:
        assert period_result_enabled.n_bars == len(df_600)

    def test_period_label_empty_initially(self) -> None:
        """run_period sets period_label=''; caller sets the final label."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats

        df = _make_synthetic_ohlc(n=400, seed=7)
        cfg = _make_trade_filter_config()
        stats = _build_stats(df, cfg)
        r = run_period(
            df=df, atr_period=10, multiplier=2.5,
            trade_mode="revers", commission=0.001,
            trade_filter_config=cfg, zigzag_global_stats=stats,
        )
        assert r.period_label == ""


# ---------------------------------------------------------------------------
# Group 2: filter_diagnostics_summary structure and invariants
# ---------------------------------------------------------------------------

class TestFilterDiagnosticsSummaryStructure:
    """Test summary keys and sanity invariants (plan §3.3.2)."""

    def test_top_level_keys_present(self, period_result_enabled) -> None:
        s = period_result_enabled.filter_diagnostics_summary
        # Core keys that must always be present (superset check — new exit-off
        # and gate keys were added in PR5/PR6 without removing the legacy ones).
        required = {"mode", "thresholds", "global_offset", "counters", "bars_in_state"}
        assert required.issubset(set(s.keys())), (
            f"Required top-level keys missing: {required - set(s.keys())}"
        )

    def test_mode_value(self, period_result_enabled) -> None:
        assert period_result_enabled.filter_diagnostics_summary["mode"] == "zigzag_st_mode"

    def test_global_offset_zero_for_full_slice(self, period_result_enabled) -> None:
        assert period_result_enabled.filter_diagnostics_summary["global_offset"] == 0

    def test_thresholds_keys_and_types(
        self, period_result_enabled, cfg_enabled, stats_600
    ) -> None:
        t = period_result_enabled.filter_diagnostics_summary["thresholds"]
        assert "reversal_threshold" in t
        assert "candidate_trigger_threshold" in t
        assert "candidate_trigger_source" in t
        assert t["candidate_trigger_source"] in ("explicit", "quantile")
        assert "global_median" in t
        assert "local_window" in t
        assert "freeze_confirmed_legs" in t
        assert isinstance(t["reversal_threshold"], float)
        assert isinstance(t["candidate_trigger_threshold"], float)
        assert isinstance(t["local_window"], int)

    def test_thresholds_candidate_trigger_source_required(
        self, period_result_enabled
    ) -> None:
        """Owner decision v0.5.1 §15 #7: candidate_trigger_source is REQUIRED."""
        t = period_result_enabled.filter_diagnostics_summary["thresholds"]
        assert "candidate_trigger_source" in t
        assert t["candidate_trigger_source"] is not None

    def test_counters_keys_present(self, period_result_enabled) -> None:
        c = period_result_enabled.filter_diagnostics_summary["counters"]
        required = {
            "raw_st_flips", "passed_entry_signals", "blocked_entry_signals",
            "blocked_filter_off", "blocked_waiting_first", "blocked_trade_mode",
            "blocked_local_median", "blocked_invalid_stats", "blocked_stopping",
            "lifecycle_starts", "median_stop_triggered", "exits_opposite_flip",
            # docs/time_filter_plan_v1_final.txt §7.6
            "time_filter_enabled", "time_filter_reset_count",
            "time_filter_bars_in_window", "time_filter_bars_out_window",
        }
        assert required.issubset(set(c.keys()))

    def test_sanity_invariant_1_passed_plus_blocked_eq_raw_flips(
        self, period_result_enabled
    ) -> None:
        """plan §3.3.2 sanity invariant 1."""
        c = period_result_enabled.filter_diagnostics_summary["counters"]
        assert (
            c["passed_entry_signals"] + c["blocked_entry_signals"]
            == c["raw_st_flips"]
        ), (
            f"Invariant 1 violated: passed={c['passed_entry_signals']} + "
            f"blocked={c['blocked_entry_signals']} != raw={c['raw_st_flips']}"
        )

    def test_sanity_invariant_2_sum_blocked_eq_blocked_entry_signals(
        self, period_result_enabled
    ) -> None:
        """plan §3.3.2 sanity invariant 2."""
        c = period_result_enabled.filter_diagnostics_summary["counters"]
        sum_blocked = (
            c["blocked_filter_off"]
            + c["blocked_waiting_first"]
            + c["blocked_trade_mode"]
            + c["blocked_local_median"]
            + c["blocked_invalid_stats"]
            + c["blocked_stopping"]
        )
        assert sum_blocked == c["blocked_entry_signals"], (
            f"Invariant 2 violated: sum(blocked_*)={sum_blocked} != "
            f"blocked_entry_signals={c['blocked_entry_signals']}"
        )

    def test_sanity_invariant_3_lifecycle_starts_from_state_transitions(
        self, period_result_enabled
    ) -> None:
        """plan §3.3.2 sanity invariant 3 (cross-source with state array).

        After PR5, lifecycle_starts uses ACTIVE_LIFECYCLE_STATES (all 4 active
        states: ST_ACTIVE_FREEZE, ST_ACTIVE_MONITORING, ST_COUNTING_ZZ_LEGS,
        ST_STOPPING), not only the WAIT_FIRST → ST_ACTIVE_FREEZE transition.
        The invariant: lifecycle_starts == number of transitions from any
        non-active state to any active state (including the first bar if active).
        """
        from supertrend_optimizer.core._fsm_state_names import ACTIVE_LIFECYCLE_STATES
        c = period_result_enabled.filter_diagnostics_summary["counters"]
        state_arr = period_result_enabled.filter_diagnostics["trade_filter_state"]
        active_set = set(ACTIVE_LIFECYCLE_STATES)

        active_mask = np.array([s in active_set for s in state_arr], dtype=bool)
        lifecycle_starts = int(len(active_mask) > 0 and active_mask[0])
        if len(active_mask) > 1:
            lifecycle_starts += int(np.sum(active_mask[1:] & ~active_mask[:-1]))

        assert c["lifecycle_starts"] == lifecycle_starts, (
            f"Invariant 3: lifecycle_starts={c['lifecycle_starts']} != "
            f"state-array active-transitions={lifecycle_starts}"
        )

    def test_sanity_invariant_4_median_stop_triggered_from_bar_mask(
        self, period_result_enabled
    ) -> None:
        """plan §3.3.2 sanity invariant 4 (cross-source with bar-level mask)."""
        c = period_result_enabled.filter_diagnostics_summary["counters"]
        expected = int(
            np.sum(period_result_enabled.filter_diagnostics["median_stop_triggered"])
        )
        assert c["median_stop_triggered"] == expected, (
            f"Invariant 4: counter={c['median_stop_triggered']} != "
            f"bar-level sum={expected}"
        )

    def test_sanity_invariant_5_exits_opposite_flip_from_trades(
        self, period_result_enabled
    ) -> None:
        """plan §3.3.2 sanity invariant 5 (cross-source with trades)."""
        c = period_result_enabled.filter_diagnostics_summary["counters"]
        trades = period_result_enabled.result.trades_df
        if trades is not None and not trades.empty and "exit_reason" in trades.columns:
            expected = int(
                (trades["exit_reason"] == "filter_stopping_opposite_flip").sum()
            )
        else:
            expected = 0
        assert c["exits_opposite_flip"] == expected

    def test_sanity_invariant_6_bars_in_state_sum_eq_n_positions(
        self, period_result_enabled
    ) -> None:
        """plan §3.3.2 sanity invariant 6."""
        s = period_result_enabled.filter_diagnostics_summary
        bars_sum = sum(s["bars_in_state"].values())
        n_pos = len(period_result_enabled.result.positions)
        assert bars_sum == n_pos, (
            f"Invariant 6: sum(bars_in_state)={bars_sum} != n_positions={n_pos}"
        )

    def test_bars_in_state_all_six_keys_present(
        self, period_result_enabled
    ) -> None:
        """After PR5 (exit B), FSM has 6 states including ST_COUNTING_ZZ_LEGS."""
        from supertrend_optimizer.core._fsm_state_names import FSM_STATE_NAMES
        bis = period_result_enabled.filter_diagnostics_summary["bars_in_state"]
        assert set(bis.keys()) == set(FSM_STATE_NAMES), (
            f"bars_in_state keys mismatch:\n"
            f"  expected (FSM_STATE_NAMES): {sorted(FSM_STATE_NAMES)}\n"
            f"  observed: {sorted(bis.keys())}"
        )


# ---------------------------------------------------------------------------
# Group 3: fail-fast (run_period enabled + zigzag_global_stats=None)
# ---------------------------------------------------------------------------

class TestRunPeriodFailFast:
    """plan §7.2 — fail-closed when stats absent on enabled path."""

    def test_raises_config_error_when_stats_none(self, df_600, cfg_enabled) -> None:
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.utils.exceptions import ConfigError

        with pytest.raises(ConfigError, match="zigzag_global_stats required"):
            run_period(
                df=df_600,
                atr_period=14,
                multiplier=3.0,
                trade_mode="revers",
                commission=0.001,
                trade_filter_config=cfg_enabled,
                zigzag_global_stats=None,  # must raise
            )

    def test_no_raise_when_disabled_config_and_stats_none(self, df_600) -> None:
        """Disabled path: stats=None is fine."""
        from supertrend_optimizer.testing.runner import run_period

        r = run_period(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            trade_filter_config=None,
            zigzag_global_stats=None,
        )
        assert r.filter_diagnostics is None
        assert r.filter_diagnostics_summary is None


# ---------------------------------------------------------------------------
# Group 4: disabled path (baseline identical)
# ---------------------------------------------------------------------------

class TestRunPeriodDisabledPath:
    """plan §3.3.3 — disabled path is bit-identical baseline."""

    def test_filter_diagnostics_none_on_disabled_path(
        self, period_result_disabled
    ) -> None:
        assert period_result_disabled.filter_diagnostics is None

    def test_filter_diagnostics_summary_none_on_disabled_path(
        self, period_result_disabled
    ) -> None:
        assert period_result_disabled.filter_diagnostics_summary is None

    def test_disabled_has_same_num_trades_as_no_filter_call(
        self, df_600
    ) -> None:
        """Two disabled calls produce identical metrics."""
        from supertrend_optimizer.testing.runner import run_period

        r1 = run_period(
            df=df_600, atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
        )
        r2 = run_period(
            df=df_600, atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            trade_filter_config=None, zigzag_global_stats=None,
        )
        assert r1.metrics["num_trades"] == r2.metrics["num_trades"]
        assert r1.metrics["sum_pnl_pct"] == pytest.approx(r2.metrics["sum_pnl_pct"])

    def test_disabled_result_has_no_filter_columns_in_trades(
        self, period_result_disabled
    ) -> None:
        """Disabled-path trades_df must not contain filter columns."""
        trades = period_result_disabled.result.trades_df
        if trades is not None and not trades.empty:
            filter_cols = {"entry_filter_state", "entry_trigger_source", "exit_reason"}
            assert filter_cols.isdisjoint(set(trades.columns)), (
                f"Disabled-path trades_df has unexpected filter columns: "
                f"{filter_cols & set(trades.columns)}"
            )


# ---------------------------------------------------------------------------
# Group 5: run_all_periods — stats materialisation and global_offset
# ---------------------------------------------------------------------------

class TestRunAllPeriodsFilterIntegration:
    """Test #9 (stats reuse), global_offset, and all-periods enabled path."""

    def test_stats_materialised_when_not_supplied(
        self, df_600, cfg_enabled
    ) -> None:
        """Test #9: run_all_periods materialises stats from df (plan §7.3)."""
        from supertrend_optimizer.testing.runner import run_all_periods

        results = run_all_periods(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=cfg_enabled,
            zigzag_global_stats=None,  # <-- must be materialised inside
        )
        assert len(results) == 5
        for r in results:
            assert r.filter_diagnostics is not None, (
                f"Period {r.period_label}: filter_diagnostics should be set."
            )
            assert r.filter_diagnostics_summary is not None

    def test_stats_reused_same_thresholds_across_periods(
        self, df_600, cfg_enabled, stats_600
    ) -> None:
        """Test #9: all periods share the same materialised thresholds."""
        from supertrend_optimizer.testing.runner import run_all_periods

        results = run_all_periods(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=cfg_enabled,
            zigzag_global_stats=stats_600,  # pre-supplied
        )
        thresholds_list = [
            r.filter_diagnostics_summary["thresholds"]
            for r in results
        ]
        first = thresholds_list[0]
        for t in thresholds_list[1:]:
            assert t["candidate_trigger_threshold"] == pytest.approx(
                first["candidate_trigger_threshold"]
            ), "candidate_trigger_threshold must be identical across all slices."
            assert t["global_median"] == pytest.approx(first["global_median"])

    def test_global_offset_per_period_correct(
        self, df_600, cfg_enabled, stats_600
    ) -> None:
        """period_global_offset = len(df) - n_period (plan §4.1)."""
        from supertrend_optimizer.testing.runner import run_all_periods, PERIOD_SPLITS

        n_total = len(df_600)
        results = run_all_periods(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=cfg_enabled,
            zigzag_global_stats=stats_600,
        )
        expected_offsets = {
            label: n_total - max(1, math.floor(n_total * frac))
            for label, frac in PERIOD_SPLITS
        }
        for r in results:
            expected = expected_offsets[r.period_label]
            actual = r.filter_diagnostics_summary["global_offset"]
            assert actual == expected, (
                f"Period {r.period_label}: global_offset={actual}, "
                f"expected={expected}."
            )

    def test_global_offset_100pct_is_zero(
        self, df_600, cfg_enabled, stats_600
    ) -> None:
        """100% slice uses the full df → global_offset = 0."""
        from supertrend_optimizer.testing.runner import run_all_periods

        results = run_all_periods(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=cfg_enabled,
            zigzag_global_stats=stats_600,
        )
        result_100 = next(r for r in results if r.period_label == "100%")
        assert result_100.filter_diagnostics_summary["global_offset"] == 0

    def test_all_periods_labels_correct(
        self, df_600, cfg_enabled, stats_600
    ) -> None:
        from supertrend_optimizer.testing.runner import run_all_periods

        results = run_all_periods(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=cfg_enabled,
            zigzag_global_stats=stats_600,
        )
        assert [r.period_label for r in results] == [
            "100%", "75%", "50%", "33%", "25%"
        ]

    def test_run_all_periods_can_skip_legacy_tail_splits(
        self, df_600, cfg_enabled, stats_600
    ) -> None:
        from supertrend_optimizer.testing.runner import run_all_periods

        baseline = run_all_periods(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=cfg_enabled,
            zigzag_global_stats=stats_600,
        )
        fast = run_all_periods(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=cfg_enabled,
            zigzag_global_stats=stats_600,
            include_period_splits=False,
        )

        assert [r.period_label for r in fast] == ["100%"]
        assert fast[0].n_bars == len(df_600)
        assert fast[0].metrics == baseline[0].metrics

    def test_disabled_path_run_all_periods_baseline_identical(
        self, df_600
    ) -> None:
        """Disabled run_all_periods: no filter fields populated."""
        from supertrend_optimizer.testing.runner import run_all_periods

        results = run_all_periods(
            df=df_600,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=30,
            trade_filter_config=None,
            zigzag_global_stats=None,
        )
        for r in results:
            assert r.filter_diagnostics is None, (
                f"Disabled period {r.period_label}: filter_diagnostics should be None."
            )
            assert r.filter_diagnostics_summary is None


# ---------------------------------------------------------------------------
# Group 6: WP-T1 baseline still green (disabled run unchanged)
# ---------------------------------------------------------------------------

class TestDisabledBaselineNotBroken:
    """Guard that WP-T4 additions do not break the WP-T1 snapshot values."""

    def test_wp_t1_snapshot_num_trades_100pct(self, df_600) -> None:
        """Disabled run_all_periods must reproduce WP-T1 num_trades for 100%."""
        from supertrend_optimizer.testing.runner import run_all_periods

        # Use the reference dataset from the WP-T1 baseline suite.
        ref_csv = REPO_ROOT / "donor TESTER" / "tests" / "baselines" / "data.csv"
        if not ref_csv.exists():
            pytest.skip("Reference dataset not available.")

        # Load exactly as the tester does.
        from supertrend_optimizer.data.loader import load_ohlc_csv
        from supertrend_optimizer.data.validator import validate_ohlc_data

        df_ref = validate_ohlc_data(load_ohlc_csv(str(ref_csv)))

        results = run_all_periods(
            df=df_ref,
            atr_period=7,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            warmup_period=50,
            periods_per_year=252.0,
        )
        result_100 = next(r for r in results if r.period_label == "100%")

        # Pin from WP-T1 EXPECTED_100PCT constant in test_wp_t1_baseline_capture.py
        assert result_100.metrics["num_trades"] == 4636
        assert result_100.metrics["sum_pnl_pct"] == pytest.approx(-312.253933, rel=1e-4)


# ===========================================================================
# §10.6 / Plan v3 §8 smoke: runner echoes exit_b_immediate_off in summary
# ===========================================================================

class TestRunnerEchoesImmediateOffFlag:
    """§10.6 smoke: _echo_thresholds and _build_filter_diagnostics_summary
    correctly propagate exit_b_immediate_off from the config.
    """

    def _make_enabled_cfg(self, exit_b_immediate_off: bool):
        from supertrend_optimizer.core.trade_filter_config import (
            TradeFilterConfig, TradeFilterZigZagConfig, TradeFilterTriggersConfig,
            TradeFilterLifecycleConfig, TradeFilterDiagnosticsConfig,
            TradeFilterTriggerToggleConfig,
        )
        return TradeFilterConfig(
            enabled=True, type="zigzag_st_mode",
            zigzag=TradeFilterZigZagConfig(
                enabled=True,
                reversal_threshold=0.03, local_window=20,
                candidate_trigger_threshold=0.4,
            ),
            triggers=TradeFilterTriggersConfig(
                candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
                confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
            ),
            lifecycle=TradeFilterLifecycleConfig(
                freeze_confirmed_legs=3, stop_check="confirm_bar_only",
                stopping_exit="opposite_st_flip",
                exit_off_mode="exit B",
                exit_off_zz_leg_count=2,
                exit_b_immediate_off=exit_b_immediate_off,
            ),
            diagnostics=TradeFilterDiagnosticsConfig(
                export_state_columns=True, export_trigger_columns=True,
            ),
        )

    def _make_stats(self, tf_cfg, df):
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        return build_zigzag_global_stats(df["close"].values, tf_cfg)

    def _make_df(self, n=200):
        rng = np.random.default_rng(77)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
        noise = rng.uniform(0.001, 0.004, n)
        idx = pd.date_range("2021-01-01", periods=n, freq="D")
        return pd.DataFrame({
            "open": close * (1 - noise / 2),
            "high": close * (1 + noise),
            "low": close * (1 - noise),
            "close": close,
        }, index=idx)

    def test_echo_thresholds_flag_true(self):
        from supertrend_optimizer.testing.runner import _echo_thresholds
        tf_cfg = self._make_enabled_cfg(True)
        df = self._make_df()
        stats = self._make_stats(tf_cfg, df)
        thr = _echo_thresholds(tf_cfg, stats)
        assert thr.get("exit_b_immediate_off") is True

    def test_echo_thresholds_flag_false(self):
        from supertrend_optimizer.testing.runner import _echo_thresholds
        tf_cfg = self._make_enabled_cfg(False)
        df = self._make_df()
        stats = self._make_stats(tf_cfg, df)
        thr = _echo_thresholds(tf_cfg, stats)
        assert thr.get("exit_b_immediate_off") is False

    def test_summary_top_level_flag_true(self):
        """filter_diagnostics_summary top-level has exit_b_immediate_off==True."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.utils.enums import ExecutionModel
        tf_cfg = self._make_enabled_cfg(True)
        df = self._make_df()
        stats = self._make_stats(tf_cfg, df)
        pr = run_period(
            df=df, atr_period=14, multiplier=3.0, trade_mode="revers",
            commission=0.001, execution_model=ExecutionModel.OPEN_TO_OPEN,
            min_trades_required=1, trade_filter_config=tf_cfg,
            zigzag_global_stats=stats, global_offset=0,
        )
        assert pr.filter_diagnostics_summary is not None
        assert pr.filter_diagnostics_summary.get("exit_b_immediate_off") is True

    def test_summary_top_level_flag_false(self):
        """filter_diagnostics_summary top-level has exit_b_immediate_off==False."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.utils.enums import ExecutionModel
        tf_cfg = self._make_enabled_cfg(False)
        df = self._make_df()
        stats = self._make_stats(tf_cfg, df)
        pr = run_period(
            df=df, atr_period=14, multiplier=3.0, trade_mode="revers",
            commission=0.001, execution_model=ExecutionModel.OPEN_TO_OPEN,
            min_trades_required=1, trade_filter_config=tf_cfg,
            zigzag_global_stats=stats, global_offset=0,
        )
        assert pr.filter_diagnostics_summary is not None
        assert pr.filter_diagnostics_summary.get("exit_b_immediate_off") is False
