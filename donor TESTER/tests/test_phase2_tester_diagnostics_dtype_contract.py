"""
Test #25 — filter_diagnostics keyset/dtype contract (spec §13).

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §10 #25
Spec reference: Appendix A v1.1 §3.3.1, §3.3.3, §3.3.4, spec v1.1 §13

Contract (audit-fix v0.5 + daily_reset plan v3 + v3 diagnostics + exit-off mode):
(a) STRICT set equality: set(filter_diagnostics.keys()) == EXPECTED_KEYSET (41 keys exactly).
(b) Shape == (len(positions),) for each array.
(c) dtype per §3.3.4:
    - float64 keys: candidate_height_pct, candidate_trigger_threshold,
      zigzag_reversal_threshold, local_median_N, global_median,
      confirmed_legs_since_start (or int64), freeze_confirmed_legs (int),
      stopping_started_at_index (int or -1)
    - int/bool keys: trade_filter_enabled, local_median_available, global_stats_available,
      median_stop_triggered, filter_allowed_entry, trade_filter_state_code, st_flip_dir
    - str/object keys: trade_filter_state, trade_filter_trigger_source, filter_block_reason
(d) Values from spec enum-whitelist for trade_filter_trigger_source and filter_block_reason.
(e) Mutual-exclusion: disabled run → filter_diagnostics is None AND filter_diagnostics_summary is None.
(f) Regression-gate: 2.0-TESTER legacy keys must NOT exist in enabled-run dict.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


# Exact keyset produced by zigzag_st_filter.apply → run_period (WP9 §13 + v3 + exit-off).
EXPECTED_KEYSET = frozenset({
    "b_component_ok",
    "candidate_age_bars",
    "candidate_component_ok",
    "candidate_duration_gate_enabled",
    "candidate_duration_gate_passed",
    "candidate_duration_max_bars",
    "candidate_height_pct",
    "candidate_leg_direction",
    "candidate_threshold_ok",
    "candidate_trigger_threshold",
    "confirmed_legs_at_bar_start",
    "confirmed_legs_since_start",
    "confirmed_median_ok",
    "daily_reset_enabled",
    "daily_reset_event",
    # docs/time_filter_plan_v1_final.txt §7.4
    "time_filter_enabled",
    "time_filter_in_window",
    "time_filter_reset_event",
    "exit_b_immediate_off_config",
    "exit_b_immediate_off_triggered",
    "exit_off_mode",
    "exit_off_zz_leg_count",
    "filter_allowed_entry",
    "filter_block_reason",
    "freeze_confirmed_legs",
    "global_median",
    "global_stats_available",
    "held_pos_at_bar_start",
    "immediate_allowed",
    "immediate_candidate_entry_block_reason",
    "immediate_candidate_entry_used",
    "local_median_N",
    "local_median_available",
    "local_window",
    "median_stop_triggered",
    "st_flip_dir",
    "state_at_bar_start",
    "stopping_started_at_index",
    "trade_filter_enabled",
    "trade_filter_state",
    "trade_filter_state_code",
    "trade_filter_trigger_source",
    "zigzag_mode",
    "zigzag_reversal_threshold",
    "zz_leg_stop_triggered",
    "zz_legs_since_lifecycle_start",
})

# 2.0-TESTER legacy keys that MUST NOT appear
FORBIDDEN_LEGACY_KEYS = frozenset({
    "mode", "thresholds", "counters",
    "allow_entry", "filtered_reason",
    "zz_st_armed", "zz_st_locked_bull", "zz_st_locked_bear",
    "zz_st_expired", "zz_st_regime",
})

_VALID_TRIGGER_SOURCES = {"candidate_threshold", "confirmed_median", "none"}
_VALID_BLOCK_REASONS = {
    "filter_off", "waiting_first_st_flip", "trade_mode_mismatch",
    "local_median_unavailable", "invalid_stats", "stopping_state",
    "daily_reset",
    # docs/time_filter_plan_v1_final.txt §7.4
    "time_filter_reset",
    "none",  # allowed (no block = entry passed; engine sentinel for "not blocked")
    "",      # also allowed (empty string variant)
}


def _make_synthetic_ohlc(n: int = 600, seed: int = 63) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    noise = rng.uniform(0.001, 0.004, size=n)
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.clip(close * (1 + rng.uniform(-0.002, 0.002, size=n)), low, high)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def _make_enabled_cfg(*, daily_reset: bool = False):
    from supertrend_optimizer.core.trade_filter_config import (
        TradeFilterConfig, TradeFilterZigZagConfig, TradeFilterTriggersConfig,
        TradeFilterLifecycleConfig, TradeFilterDiagnosticsConfig,
        TradeFilterTriggerToggleConfig,
    )
    return TradeFilterConfig(
        enabled=True, type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            reversal_threshold=0.03,
            local_window=20,
            candidate_trigger_threshold=0.4,
            daily_reset=daily_reset,
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


def _run_enabled(df, *, daily_reset: bool = False):
    from supertrend_optimizer.testing.runner import run_period
    from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
    from supertrend_optimizer.utils.enums import ExecutionModel
    cfg = _make_enabled_cfg(daily_reset=daily_reset)
    stats = build_zigzag_global_stats(df["close"].values, cfg)
    return run_period(
        df=df, atr_period=14, multiplier=3.0,
        trade_mode="revers", commission=0.001,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
    )


class TestDiagnosticsDtypeContract:
    """Strict spec §13 keyset + dtype contract (#25)."""

    def test_exact_keyset_equality(self) -> None:
        """(a) set(filter_diagnostics.keys()) must equal EXPECTED_KEYSET exactly."""
        df = _make_synthetic_ohlc()
        r = _run_enabled(df)

        actual = set(r.filter_diagnostics.keys())
        missing = EXPECTED_KEYSET - actual
        extra = actual - EXPECTED_KEYSET

        assert not missing, f"filter_diagnostics missing keys: {missing}"
        assert not extra, (
            f"filter_diagnostics has unexpected extra keys: {extra}. "
            "These may be 2.0-TESTER legacy keys or undocumented additions."
        )

    def test_shapes_equal_positions_length(self) -> None:
        """(b) Every array must have shape == (len(positions),)."""
        df = _make_synthetic_ohlc()
        r = _run_enabled(df)

        n_pos = len(r.result.positions)
        for key, arr in r.filter_diagnostics.items():
            assert len(arr) == n_pos, (
                f"filter_diagnostics[{key!r}] length {len(arr)} != "
                f"positions length {n_pos}"
            )

    def test_object_array_keys_present_with_string_values(self) -> None:
        """(c) String/object keys must have dtype=object and contain strings."""
        df = _make_synthetic_ohlc()
        r = _run_enabled(df)
        fd = r.filter_diagnostics

        for key in (
            "trade_filter_state",
            "trade_filter_trigger_source",
            "filter_block_reason",
            "exit_off_mode",
            "zigzag_mode",
        ):
            assert key in fd, f"Missing key: {key}"
            arr = fd[key]
            assert arr.dtype == object or np.issubdtype(arr.dtype, np.str_), (
                f"{key}: expected object dtype, got {arr.dtype}"
            )
            # Must contain strings, not numeric values
            sample = str(arr[0])
            assert isinstance(sample, str), f"{key}[0] is not a string: {type(arr[0])}"

    def test_numeric_array_keys_have_numeric_dtype(self) -> None:
        """(c) Numeric keys must have numeric dtype (not object)."""
        df = _make_synthetic_ohlc()
        r = _run_enabled(df)
        fd = r.filter_diagnostics

        numeric_keys = (
            "candidate_height_pct", "candidate_trigger_threshold",
            "zigzag_reversal_threshold", "local_median_N", "global_median",
            "confirmed_legs_since_start", "freeze_confirmed_legs",
        )
        for key in numeric_keys:
            if key not in fd:
                continue
            arr = fd[key]
            assert np.issubdtype(arr.dtype, np.number) or arr.dtype == object, (
                f"{key}: expected numeric dtype, got {arr.dtype}"
            )

    def test_trigger_source_values_from_whitelist(self) -> None:
        """(d) trade_filter_trigger_source values must be in valid enum."""
        df = _make_synthetic_ohlc()
        r = _run_enabled(df)
        arr = r.filter_diagnostics["trade_filter_trigger_source"]
        unique = set(str(v) for v in np.unique(arr))
        invalid = unique - _VALID_TRIGGER_SOURCES
        assert not invalid, (
            f"Invalid trigger_source values: {invalid}. "
            f"Valid: {_VALID_TRIGGER_SOURCES}"
        )

    def test_filter_block_reason_values_from_whitelist(self) -> None:
        """(d) filter_block_reason values must be in valid enum."""
        df = _make_synthetic_ohlc()
        r = _run_enabled(df)
        arr = r.filter_diagnostics["filter_block_reason"]
        unique = set(str(v) for v in np.unique(arr))
        invalid = unique - _VALID_BLOCK_REASONS
        assert not invalid, (
            f"Invalid filter_block_reason values: {invalid}. "
            f"Valid: {_VALID_BLOCK_REASONS}"
        )

    def test_daily_reset_keys_present(self) -> None:
        """daily_reset_enabled and daily_reset_event are always present (plan v3)."""
        df = _make_synthetic_ohlc()
        r = _run_enabled(df)
        fd = r.filter_diagnostics

        assert "daily_reset_enabled" in fd
        assert "daily_reset_event" in fd
        n_pos = len(r.result.positions)
        assert len(fd["daily_reset_enabled"]) == n_pos
        assert len(fd["daily_reset_event"]) == n_pos
        assert np.all(fd["daily_reset_enabled"] == 0)
        assert np.all(fd["daily_reset_event"] == 0)

    def test_daily_reset_enabled_produces_block_reason_and_summary(self) -> None:
        """Enabled daily_reset emits concrete bar-level reason and summary count."""
        df = _make_synthetic_ohlc(n=80)
        r = _run_enabled(df, daily_reset=True)
        fd = r.filter_diagnostics

        assert np.all(fd["daily_reset_enabled"] == 1)
        assert int(np.sum(fd["daily_reset_event"] == 1)) > 0
        assert "daily_reset" in {str(v) for v in np.unique(fd["filter_block_reason"])}
        assert r.filter_diagnostics_summary is not None
        assert (
            r.filter_diagnostics_summary["counters"]["daily_reset_count"]
            == int(np.sum(fd["daily_reset_event"] == 1))
        )

    def test_disabled_mutual_exclusion(self) -> None:
        """(e) Disabled run: both filter_diagnostics and filter_diagnostics_summary are None."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = _make_synthetic_ohlc()
        r = run_period(
            df=df, atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=None,
        )
        assert r.filter_diagnostics is None, (
            "Disabled run must have filter_diagnostics=None"
        )
        assert r.filter_diagnostics_summary is None, (
            "Disabled run must have filter_diagnostics_summary=None"
        )

    def test_no_legacy_2_0_tester_keys(self) -> None:
        """(f) Regression gate: 2.0-TESTER legacy keys must not exist in enabled dict."""
        df = _make_synthetic_ohlc()
        r = _run_enabled(df)

        actual_keys = set(r.filter_diagnostics.keys())
        found_legacy = actual_keys & FORBIDDEN_LEGACY_KEYS
        assert not found_legacy, (
            f"Found 2.0-TESTER legacy keys in filter_diagnostics: {found_legacy}. "
            "These must NOT be present in Phase 2 output."
        )


class TestExitOffExactDtypeContract:
    """§2 / §12.6 (plan_exit_off_modes_v2.txt): STRICT exact-dtype check for 4 new
    exit-off per-bar arrays.

    Plan §6 requires:
      exit_off_mode                    → dtype=object  (str echo)
      exit_off_zz_leg_count            → dtype=int64   (echo, sentinel -1 for exit A)
      zz_legs_since_lifecycle_start    → dtype=int64   (-1 sentinel outside lifecycle)
      zz_leg_stop_triggered            → dtype=int8    (binary 0/1 flag)

    Unlike TestDiagnosticsDtypeContract.test_numeric_array_keys_have_numeric_dtype
    which only checks np.issubdtype(..., np.number), these tests enforce the EXACT
    dtype required by §2 to prevent silent platform-dependent widening (e.g. int32
    on some Windows builds) or erroneous narrowing.
    """

    def _run_exit_a(self):
        df = _make_synthetic_ohlc()
        return _run_enabled(df)

    def _run_exit_b(self):
        """Run with exit B (count=2) to ensure counting state is reached."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        from supertrend_optimizer.utils.enums import ExecutionModel
        from supertrend_optimizer.core.trade_filter_config import (
            TradeFilterConfig, TradeFilterZigZagConfig, TradeFilterTriggersConfig,
            TradeFilterLifecycleConfig, TradeFilterDiagnosticsConfig,
            TradeFilterTriggerToggleConfig,
        )
        df = _make_synthetic_ohlc()
        cfg = TradeFilterConfig(
            enabled=True, type="zigzag_st_mode",
            zigzag=TradeFilterZigZagConfig(
                reversal_threshold=0.03, local_window=20,
                candidate_trigger_threshold=0.4,
            ),
            triggers=TradeFilterTriggersConfig(
                candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
                confirmed_median=TradeFilterTriggerToggleConfig(enabled=False),
            ),
            lifecycle=TradeFilterLifecycleConfig(
                freeze_confirmed_legs=0, stop_check="confirm_bar_only",
                stopping_exit="opposite_st_flip",
                exit_off_mode="exit B",
                exit_off_zz_leg_count=2,
            ),
            diagnostics=TradeFilterDiagnosticsConfig(
                export_state_columns=True, export_trigger_columns=True,
            ),
        )
        stats = build_zigzag_global_stats(df["close"].values, cfg)
        return run_period(
            df=df, atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
        )

    def test_exit_off_mode_exact_dtype_object_exit_a(self):
        r = self._run_exit_a()
        arr = r.filter_diagnostics["exit_off_mode"]
        assert arr.dtype == object, (
            f"§2: exit_off_mode (exit A) expected dtype=object, got {arr.dtype}"
        )

    def test_exit_off_mode_exact_dtype_object_exit_b(self):
        r = self._run_exit_b()
        arr = r.filter_diagnostics["exit_off_mode"]
        assert arr.dtype == object, (
            f"§2: exit_off_mode (exit B) expected dtype=object, got {arr.dtype}"
        )

    def test_exit_off_zz_leg_count_exact_dtype_int64_exit_a(self):
        r = self._run_exit_a()
        arr = r.filter_diagnostics["exit_off_zz_leg_count"]
        assert arr.dtype == np.int64, (
            f"§2: exit_off_zz_leg_count (exit A) expected dtype=int64, got {arr.dtype}"
        )

    def test_exit_off_zz_leg_count_exact_dtype_int64_exit_b(self):
        r = self._run_exit_b()
        arr = r.filter_diagnostics["exit_off_zz_leg_count"]
        assert arr.dtype == np.int64, (
            f"§2: exit_off_zz_leg_count (exit B) expected dtype=int64, got {arr.dtype}"
        )

    def test_zz_legs_since_lifecycle_start_exact_dtype_int64(self):
        """Both exit A and exit B must produce int64 (sentinel -1 vs counters)."""
        r_a = self._run_exit_a()
        r_b = self._run_exit_b()
        for label, r in (("exit A", r_a), ("exit B", r_b)):
            arr = r.filter_diagnostics["zz_legs_since_lifecycle_start"]
            assert arr.dtype == np.int64, (
                f"§2: zz_legs_since_lifecycle_start ({label}) expected int64, got {arr.dtype}"
            )

    def test_zz_leg_stop_triggered_exact_dtype_int8(self):
        """Both exit A (all-zero) and exit B must produce int8 flag array."""
        r_a = self._run_exit_a()
        r_b = self._run_exit_b()
        for label, r in (("exit A", r_a), ("exit B", r_b)):
            arr = r.filter_diagnostics["zz_leg_stop_triggered"]
            assert arr.dtype == np.int8, (
                f"§2: zz_leg_stop_triggered ({label}) expected int8, got {arr.dtype}"
            )

    def test_exit_a_sentinel_values_correct_dtype(self):
        """exit A sentinel: exit_off_zz_leg_count==-1, zz_legs_since_lifecycle_start==-1."""
        r = self._run_exit_a()
        fd = r.filter_diagnostics
        zz_count = fd["exit_off_zz_leg_count"]
        zz_legs  = fd["zz_legs_since_lifecycle_start"]
        assert zz_count.dtype == np.int64 and np.all(zz_count == -1), (
            f"§2: exit_off_zz_leg_count sentinel wrong: dtype={zz_count.dtype}, "
            f"values={set(zz_count.tolist())}"
        )
        assert zz_legs.dtype == np.int64 and np.all(zz_legs == -1), (
            f"§2: zz_legs_since_lifecycle_start sentinel wrong: dtype={zz_legs.dtype}, "
            f"values={set(zz_legs.tolist())}"
        )
