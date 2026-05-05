"""
PR6 — Excel export contract tests (plan_exit_off_modes_v2.txt §8, §14.4).

Covers:
  §8.1  FILTER_DIAGNOSTICS_100_DISPLAY_NAMES: 4 new exit-off per-bar headers
        present with correct display names.
  §8.2  _build_filters_summary_df params_rows: "Exit-OFF Mode" and
        "Exit-OFF ZZ Leg Count" rows present.
  §8.3  ZZ Leg Stop Events / ZZ Leg Stops in period_rows.
  §14.4 Cross-layer: display names consistent with per-bar key names from
        _EXPECTED_PER_BAR_KEYSET (no typo drift between layers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# §8.1 Snapshot: expected display names for the 4 exit-off per-bar keys
# (plan §8.1 — single source of truth for this test file)
# ---------------------------------------------------------------------------

_EXIT_OFF_PER_BAR_DISPLAY_NAMES = {
    "exit_off_mode":                 "Exit-OFF Mode",
    "exit_off_zz_leg_count":         "Exit-OFF ZZ Leg Count",
    "zz_legs_since_lifecycle_start": "ZZ Legs Since Start",
    "zz_leg_stop_triggered":         "ZZ Leg Stop Triggered",
}

# Expected params labels in _build_filters_summary_df (§8.2)
_EXIT_OFF_PARAMS_LABELS = ("Exit-OFF Mode", "Exit-OFF ZZ Leg Count")

# Expected period row labels (§8.3)
_EXIT_OFF_PERIOD_LABELS = ("ZZ Leg Stop Events",)  # "ZZ Leg Stops" is in the second df block
_EXIT_OFF_PERIOD_LABELS_ALT = ("ZZ Leg Stops",)


# ---------------------------------------------------------------------------
# §8.1 Header map tests
# ---------------------------------------------------------------------------

class TestExitOffDisplayNames:
    """FILTER_DIAGNOSTICS_100_DISPLAY_NAMES must contain 4 exit-off entries
    with exact display strings per plan §8.1."""

    def test_all_four_exit_off_keys_present(self):
        from supertrend_optimizer.io.excel_tester import FILTER_DIAGNOSTICS_100_DISPLAY_NAMES
        missing = set(_EXIT_OFF_PER_BAR_DISPLAY_NAMES) - set(FILTER_DIAGNOSTICS_100_DISPLAY_NAMES)
        assert not missing, (
            f"Missing exit-off keys in FILTER_DIAGNOSTICS_100_DISPLAY_NAMES: {sorted(missing)}"
        )

    def test_display_names_exact_match(self):
        from supertrend_optimizer.io.excel_tester import FILTER_DIAGNOSTICS_100_DISPLAY_NAMES
        for key, expected_label in _EXIT_OFF_PER_BAR_DISPLAY_NAMES.items():
            actual = FILTER_DIAGNOSTICS_100_DISPLAY_NAMES.get(key)
            assert actual == expected_label, (
                f"Display name mismatch for {key!r}:\n"
                f"  expected: {expected_label!r}\n"
                f"  actual:   {actual!r}"
            )


# ---------------------------------------------------------------------------
# §8.2 / §8.3 filters_summary_df params and period rows
# ---------------------------------------------------------------------------

def _make_period_result_double(
    *,
    exit_off_mode: str = "exit B",
    exit_off_zz_leg_count: int = 3,
    zz_leg_stop_triggered_count: int = 2,
    n_bars_in_counting_zz_legs: int = 15,
):
    """Build a minimal duck-typed PeriodResult for _build_filters_summary_df."""

    class _MockBacktestResult:
        positions = np.zeros(10, dtype=np.int8)
        trades_df = None

    class _MockPeriodResult:
        period_label = "100%"
        result = _MockBacktestResult()
        filter_diagnostics = {}
        filter_diagnostics_summary = {
            "zigzag_mode": "A",
            "candidate_duration_gate_enabled": False,
            "candidate_duration_max_bars": -1,
            "exit_off_mode": exit_off_mode,
            "exit_off_zz_leg_count": exit_off_zz_leg_count,
            "lifecycle_starts_count": 1,
            "median_stop_triggered_count": 0,
            "zz_leg_stop_triggered_count": zz_leg_stop_triggered_count,
            "n_bars_in_counting_zz_legs": n_bars_in_counting_zz_legs,
            "thresholds": {
                "reversal_threshold": 0.02,
                "candidate_trigger_threshold": 0.05,
                "candidate_trigger_quantile": None,
                "candidate_trigger_source": "explicit",
                "global_median": 0.04,
                "local_window": 5,
                "freeze_confirmed_legs": 0,
                "exit_off_mode": exit_off_mode,
                "exit_off_zz_leg_count": exit_off_zz_leg_count,
                "zigzag_mode": "A",
                "candidate_duration_gate_enabled": False,
                "candidate_duration_max_bars": -1,
            },
            "counters": {
                "zz_leg_stop_triggered": zz_leg_stop_triggered_count,
                "median_stop_triggered": 0,
                "lifecycle_starts": 1,
            },
            "bars_in_state": {
                "ST_COUNTING_ZZ_LEGS": n_bars_in_counting_zz_legs,
                "OFF": 5,
            },
        }

    return _MockPeriodResult()


class TestFiltersSummaryExitOffParams:
    """§8.2: _build_filters_summary_df params_rows must include
    'Exit-OFF Mode' and 'Exit-OFF ZZ Leg Count' entries."""

    def _get_params_df(self, **kwargs):
        from supertrend_optimizer.io.excel_tester import _build_filters_summary_df
        pr = _make_period_result_double(**kwargs)
        result = _build_filters_summary_df([pr])
        assert result is not None, "_build_filters_summary_df returned None"
        params_df, _ = result
        return params_df

    def test_exit_off_mode_row_present(self):
        params_df = self._get_params_df(exit_off_mode="exit B")
        labels = set(params_df["Parameter"].tolist())
        assert "Exit-OFF Mode" in labels, (
            f"'Exit-OFF Mode' missing from params_df. Labels: {sorted(labels)}"
        )

    def test_exit_off_zz_leg_count_row_present(self):
        params_df = self._get_params_df(exit_off_zz_leg_count=3)
        labels = set(params_df["Parameter"].tolist())
        assert "Exit-OFF ZZ Leg Count" in labels, (
            f"'Exit-OFF ZZ Leg Count' missing from params_df. Labels: {sorted(labels)}"
        )

    def test_exit_off_mode_value_exit_b(self):
        params_df = self._get_params_df(exit_off_mode="exit B")
        row = params_df[params_df["Parameter"] == "Exit-OFF Mode"]
        assert not row.empty
        assert row.iloc[0]["Value"] == "exit B"

    def test_exit_off_mode_value_exit_a(self):
        params_df = self._get_params_df(exit_off_mode="exit A", exit_off_zz_leg_count=-1)
        row = params_df[params_df["Parameter"] == "Exit-OFF Mode"]
        assert not row.empty
        assert row.iloc[0]["Value"] == "exit A"

    def test_exit_off_zz_leg_count_value(self):
        params_df = self._get_params_df(exit_off_zz_leg_count=5)
        row = params_df[params_df["Parameter"] == "Exit-OFF ZZ Leg Count"]
        assert not row.empty
        assert int(row.iloc[0]["Value"]) == 5


class TestFiltersSummaryZZLegPeriodRows:
    """§8.3: _build_filters_summary_df period rows include ZZ Leg Stop Events."""

    def _get_period_df(self, **kwargs):
        from supertrend_optimizer.io.excel_tester import _build_filters_summary_df
        pr = _make_period_result_double(**kwargs)
        result = _build_filters_summary_df([pr])
        assert result is not None
        _, period_df = result
        return period_df

    def test_zz_leg_stop_column_present(self):
        """Period rows contain at least one ZZ stop label (§8.3 / plan table §2)."""
        period_df = self._get_period_df(zz_leg_stop_triggered_count=2)
        has_events = "ZZ Leg Stop Events" in period_df.columns
        has_stops = "ZZ Leg Stops" in period_df.columns
        assert has_events or has_stops, (
            f"Neither 'ZZ Leg Stop Events' nor 'ZZ Leg Stops' found.\n"
            f"Columns: {list(period_df.columns)}"
        )

    def test_zz_leg_stop_value_matches_count(self):
        period_df = self._get_period_df(zz_leg_stop_triggered_count=3)
        col = "ZZ Leg Stop Events" if "ZZ Leg Stop Events" in period_df.columns else "ZZ Leg Stops"
        assert int(period_df.iloc[0][col]) == 3


class TestExitOffExcelCrossLayer:
    """§14.4: per-bar key names in display map must match those expected in
    per-bar diagnostics keyset (no typo drift between layers)."""

    def test_display_map_keys_are_valid_per_bar_keys(self):
        """Every key in _EXIT_OFF_PER_BAR_DISPLAY_NAMES must be a valid per-bar key
        (i.e. present in filter_diagnostics output from exit B apply)."""
        from supertrend_optimizer.core.zigzag_st_filter import (
            ZigZagGlobalStats, ZigZagPerBar, apply,
        )

        n = 7
        per_bar = ZigZagPerBar(
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 1, 0, 0, 0], dtype=np.int8),
            confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
            local_median_N=np.full(n, np.nan, dtype=np.float64),
            local_median_available=np.zeros(n, dtype=bool),
            candidate_age_bars=np.full(n, -1, dtype=np.int64),
            candidate_leg_direction=np.zeros(n, dtype=np.int8),
        )
        trend = np.array([-1, 1, 1, 1, -1, -1, -1], dtype=np.int64)

        @dataclass
        class _Trig:
            class candidate_threshold:
                enabled = True
            class confirmed_median:
                enabled = False

        @dataclass
        class _LC:
            freeze_confirmed_legs: int = 0
            stop_check: str = "confirm_bar_only"
            stopping_exit: str = "opposite_st_flip"
            exit_off_mode: str = "exit B"
            exit_off_zz_leg_count: int = 2

        @dataclass
        class _ZZ:
            daily_reset: bool = False
            local_window: int = 5
            mode: Optional[str] = None

        @dataclass
        class _Cfg:
            zigzag: _ZZ = field(default_factory=_ZZ)
            triggers: _Trig = field(default_factory=_Trig)
            lifecycle: _LC = field(default_factory=_LC)

        stats = ZigZagGlobalStats(
            reversal_threshold=0.01,
            global_stats_source="full_dataset",
            leg_height_mode="pct",
            confirmed_legs=[],
            confirmed_heights_pct=np.array([], dtype=np.float64),
            global_median=0.05,
            candidate_trigger_threshold=0.04,
            candidate_trigger_source="explicit",
            candidate_trigger_quantile=None,
            n_legs_total=0,
            insufficient_data=False,
            fail_closed_reason=None,
            metadata={},
            zigzag_mode="A",
            candidate_duration_gate_enabled=False,
            candidate_duration_max_bars=None,
        )

        result = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=_Cfg(),
            zigzag_global_stats=stats,
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )

        observed_keys = set(result.filter_diagnostics.keys())
        for key in _EXIT_OFF_PER_BAR_DISPLAY_NAMES:
            assert key in observed_keys, (
                f"Key {key!r} referenced in FILTER_DIAGNOSTICS_100_DISPLAY_NAMES "
                f"is not produced by apply(). Typo in display map?\n"
                f"Observed keys: {sorted(observed_keys)}"
            )
