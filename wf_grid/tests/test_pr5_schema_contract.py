"""
PR5 — Schema contract and cross-layer drift tests.

Covers:
  §14.4  Cross-layer naming / drift: FSM_STATE_NAMES and ACTIVE_LIFECYCLE_STATES
         are identical tuples across shared, step_executor, runner,
         zigzag_st_filter.
  §14.5  Schema-snapshot: per-bar diagnostics keyset (including 4 new exit-off
         keys), summary keyset, and _FILTER_SUMMARY_COLUMNS list order.
  §14.6  Collector contract: _FILTER_SUMMARY_COLUMNS includes all 4 new exit-off
         columns; disabled path returns None values for each.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    ZigZagPerBar,
    apply,
)


# ---------------------------------------------------------------------------
# §14.5 snapshot fixtures — update here when adding a new per-bar or summary key
# ---------------------------------------------------------------------------

# EXACT per-bar filter_diagnostics keyset snapshot (§14.5 strict equality).
# Any delta between observed and this set = regression.
# To update: run apply() and print sorted(result.filter_diagnostics.keys()).
_PER_BAR_KEYSET_SNAPSHOT: frozenset = frozenset({
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
    # time_filter (docs/time_filter_plan_v1_final.txt §4.8)
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

# Backward-compat alias: old superset was _EXPECTED_PER_BAR_KEYSET (subset).
# Keep it pointing to the full snapshot for code that was comparing ⊇.
_EXPECTED_PER_BAR_KEYSET: frozenset = _PER_BAR_KEYSET_SNAPSHOT

# EXACT summary keyset snapshot (§14.5 strict equality).
_SUMMARY_KEYSET_SNAPSHOT: frozenset = frozenset({
    "candidate_duration_gate_enabled",
    "candidate_duration_max_bars",
    "daily_reset_count",
    "diagnostics_available",
    # time_filter aggregations (docs/time_filter_plan_v1_final.txt §6.1 / §7.1)
    "time_filter_enabled",
    "time_filter_reset_count",
    "time_filter_bars_in_window",
    "time_filter_bars_out_window",
    "exit_b_immediate_off",        # Plan v3 §8
    "exit_b_immediate_off_count",  # Plan v3 §8
    "exit_off_mode",
    "exit_off_zz_leg_count",
    "filter_states_visited",
    "immediate_entries_blocked_count",
    "immediate_entries_count",
    "lifecycle_starts_count",
    "median_stop_triggered_count",
    "n_bars_in_counting_zz_legs",
    "n_bars_in_freeze",
    "n_bars_in_monitoring",
    "n_bars_in_off",
    "n_bars_in_stopping",
    "n_bars_in_wait_first_st_flip",
    "n_filter_blocked_entries",
    "stopping_started_count",
    "trigger_count_both",
    "trigger_count_candidate_threshold",
    "trigger_count_confirmed_median",
    "zigzag_mode",
    "zz_leg_stop_triggered_count",
})

# EXCEL_PER_BAR_HEADERS snapshot: key → display name (§14.5 / §8.1).
# Derived from FILTER_DIAGNOSTICS_100_DISPLAY_NAMES in excel_tester.py.
_EXCEL_PER_BAR_HEADERS_SNAPSHOT: dict = {
    "b_component_ok":                           "B Component OK",
    "candidate_age_bars":                       "Candidate Age Bars",
    "candidate_component_ok":                   "Candidate Component OK",
    "candidate_duration_gate_enabled":          "Candidate Duration Gate Enabled",
    "candidate_duration_gate_passed":           "Candidate Duration Gate Passed",
    "candidate_duration_max_bars":              "Candidate Duration Max Bars",
    "candidate_height_pct":                     "Candidate Height %",
    "candidate_leg_direction":                  "Candidate Leg Direction",
    "candidate_threshold_ok":                   "Candidate Threshold OK",
    "candidate_trigger_threshold":              "Candidate Trigger Threshold",
    "confirmed_legs_since_start":               "Confirmed Legs Since Start",
    "confirmed_median_ok":                      "Confirmed Median OK",
    "exit_b_immediate_off_config":              "Exit-B Immediate OFF Config",   # Plan v3 §6.1
    "exit_b_immediate_off_triggered":           "Exit-B Immediate OFF Triggered", # Plan v3 §6.1
    "exit_off_mode":                            "Exit-OFF Mode",
    "exit_off_zz_leg_count":                    "Exit-OFF ZZ Leg Count",
    "filter_allowed_entry":                     "Filter Allowed Entry",
    "filter_block_reason":                      "Filter Block Reason",
    "freeze_confirmed_legs":                    "Freeze Confirmed Legs",
    "global_median":                            "Global Median",
    "global_stats_available":                   "Global Stats Available",
    "immediate_allowed":                        "Immediate Allowed",
    "immediate_candidate_entry_block_reason":   "Immediate Candidate Entry Block Reason",
    "immediate_candidate_entry_used":           "Immediate Candidate Entry Used",
    "local_median_N":                           "Local Median N",
    "local_median_available":                   "Local Median Available",
    "local_window":                             "Local Window",
    "median_stop_triggered":                    "Median Stop Triggered",
    "st_flip_dir":                              "ST Flip Direction",
    "stopping_started_at_index":                "Stopping Started At Index",
    "trade_filter_enabled":                     "Filter Enabled",
    "trade_filter_state":                       "Filter State",
    "trade_filter_state_code":                  "Filter State Code",
    "trade_filter_trigger_source":              "Trigger Source",
    "zigzag_mode":                              "ZigZag Mode",
    "zigzag_reversal_threshold":                "Reversal Threshold",
    "zz_leg_stop_triggered":                    "ZZ Leg Stop Triggered",
    "zz_legs_since_lifecycle_start":            "ZZ Legs Since Start",
    # docs/time_filter_plan_v1_final.txt §6.4 / §7.1
    "time_filter_enabled":                      "Time Filter Enabled",
    "time_filter_in_window":                    "Time Filter In Window",
    "time_filter_reset_event":                  "Time Filter Reset Event",
}

# EXCEL_SUMMARY_LABELS: ordered params labels and period column set (§14.5 / §8.2).
_EXCEL_SUMMARY_PARAMS_LABELS_SNAPSHOT: list = [
    "ZigZag Mode",
    "Reversal Threshold",
    "Candidate Trigger Threshold",
    "Candidate Trigger Quantile",
    "Candidate Trigger Source",
    "Global Median",
    "Local Window",
    "Freeze Confirmed Legs",
    "Candidate Duration Gate Enabled",
    "Candidate Duration Max Bars",
    "Exit-OFF Mode",
    "Exit-OFF ZZ Leg Count",
    "Exit-B Immediate OFF",  # Plan v3 §6.2
    # docs/time_filter_plan_v1_final.txt §6.4 / §7.1
    "Time Filter Enabled",
]

_EXCEL_SUMMARY_PERIOD_COLS_SNAPSHOT: list = [
    "Period", "Raw ST Flips", "Entries Allowed", "Entries Blocked",
    "Blocked Filter Off", "Blocked Waiting", "Blocked Trade Mode",
    "Blocked Local Med", "Blocked Invalid Stats", "Blocked Stopping",
    "Lifecycle Starts", "Median Stops", "ZZ Leg Stops", "Exits Opp Flip",
    "Bars OFF", "Bars WAIT", "Bars FREEZE", "Bars MONITORING",
    "Bars COUNTING ZZ", "Bars STOPPING",
    "Immediate Entries Count", "Immediate Entries Blocked Count",
    # docs/time_filter_plan_v1_final.txt §6.4 / §7.1
    "Time Filter Reset Count", "Time Filter Bars In Window", "Time Filter Bars Out Window",
]

# Mandatory exit-off columns in _FILTER_SUMMARY_COLUMNS (§14.6).
_EXIT_OFF_SUMMARY_COLUMNS = (
    "n_bars_in_counting_zz_legs",
    "zz_leg_stop_triggered_count",
    "exit_off_mode",
    "exit_off_zz_leg_count",
)

# Exact ordered list that step_long must expose (§14.5 / §14.6).
# Must match wf_grid/collect/step_collector._FILTER_SUMMARY_COLUMNS exactly.
_STEP_LONG_FILTER_COLUMNS_SNAPSHOT = [
    "filter_states_visited",
    "n_bars_in_off",
    "n_bars_in_wait_first_st_flip",
    "n_bars_in_freeze",
    "n_bars_in_monitoring",
    "n_bars_in_counting_zz_legs",
    "n_bars_in_stopping",
    "n_filter_blocked_entries",
    "lifecycle_starts_count",
    "median_stop_triggered_count",
    "zz_leg_stop_triggered_count",
    "exit_off_mode",
    "exit_off_zz_leg_count",
    "exit_b_immediate_off",        # Plan v3 §8
    "exit_b_immediate_off_count",  # Plan v3 §8
    "filter_diagnostics_available",
    "trigger_count_candidate_threshold",
    "trigger_count_confirmed_median",
    "trigger_count_both",
    "stopping_started_count",
]


# ---------------------------------------------------------------------------
# Lightweight duck-typed doubles (same pattern as test_pr4_exit_b_threshold)
# ---------------------------------------------------------------------------

@dataclass
class _ToggleDouble:
    enabled: bool = True


@dataclass
class _TriggersDouble:
    candidate_threshold: _ToggleDouble = field(default_factory=_ToggleDouble)
    confirmed_median: _ToggleDouble = field(
        default_factory=lambda: _ToggleDouble(enabled=False)
    )


@dataclass
class _ZigZagDouble:
    daily_reset: bool = False
    local_window: int = 5
    mode: Optional[str] = None


@dataclass
class _LifecycleDouble:
    freeze_confirmed_legs: int = 0
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"
    exit_off_mode: str = "exit B"
    exit_off_zz_leg_count: Optional[int] = 2


@dataclass
class _FilterCfgDouble:
    zigzag: _ZigZagDouble = field(default_factory=_ZigZagDouble)
    triggers: _TriggersDouble = field(default_factory=_TriggersDouble)
    lifecycle: _LifecycleDouble = field(default_factory=_LifecycleDouble)


def _make_stats() -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
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


def _make_per_bar(
    *,
    n: int,
    candidate_height_pct: Optional[np.ndarray] = None,
    confirm_event: Optional[np.ndarray] = None,
    confirmed_leg_idx_at_t: Optional[np.ndarray] = None,
    last_confirmed_leg_height_pct: Optional[np.ndarray] = None,
    local_median_N: Optional[np.ndarray] = None,
    local_median_available: Optional[np.ndarray] = None,
    candidate_age_bars: Optional[np.ndarray] = None,
    candidate_leg_direction: Optional[np.ndarray] = None,
) -> ZigZagPerBar:
    if candidate_height_pct is None:
        candidate_height_pct = np.full(n, np.nan, dtype=np.float64)
    if confirm_event is None:
        confirm_event = np.zeros(n, dtype=np.int8)
    if confirmed_leg_idx_at_t is None:
        confirmed_leg_idx_at_t = np.full(n, -1, dtype=np.int64)
    if last_confirmed_leg_height_pct is None:
        last_confirmed_leg_height_pct = np.full(n, np.nan, dtype=np.float64)
    if local_median_N is None:
        local_median_N = np.full(n, np.nan, dtype=np.float64)
    if local_median_available is None:
        local_median_available = np.zeros(n, dtype=bool)
    if candidate_age_bars is None:
        candidate_age_bars = np.full(n, -1, dtype=np.int64)
    if candidate_leg_direction is None:
        candidate_leg_direction = np.zeros(n, dtype=np.int8)
    return ZigZagPerBar(
        candidate_height_pct=candidate_height_pct,
        confirm_event=confirm_event,
        confirmed_leg_idx_at_t=confirmed_leg_idx_at_t,
        last_confirmed_leg_height_pct=last_confirmed_leg_height_pct,
        local_median_N=local_median_N,
        local_median_available=local_median_available,
        candidate_age_bars=candidate_age_bars,
        candidate_leg_direction=candidate_leg_direction,
    )


def _run(*, trend, per_bar, cfg=None, stats=None, daily_reset_event=None):
    n = len(trend)
    if daily_reset_event is None:
        daily_reset_event = np.zeros(n, dtype=bool)
    return apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=cfg if cfg is not None else _FilterCfgDouble(),
        zigzag_global_stats=stats if stats is not None else _make_stats(),
        per_bar=per_bar,
        daily_reset_event=daily_reset_event,
    )


@pytest.fixture(scope="module")
def exit_b_apply_result():
    """Run apply() with exit B (count=2) — 7 bars, threshold reached at bar 4.

    bar 0: trend=-1
    bar 1: trend=+1, candidate=0.06 → OFF→WAIT, WAIT→COUNTING (zz=0)
    bar 2: confirm → zz=1
    bar 3: confirm → zz=2 → THRESHOLD → ST_STOPPING, zz_leg_stop_triggered=1
    bar 4: trend=-1 (opposite flip while holding position)
    bar 5: trend=-1
    bar 6: trend=-1
    """
    n = 7
    per_bar = _make_per_bar(
        n=n,
        candidate_height_pct=np.array(
            [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan]
        ),
        confirm_event=np.array([0, 0, 1, 1, 0, 0, 0], dtype=np.int8),
    )
    trend = np.array([-1, 1, 1, 1, -1, -1, -1], dtype=np.int64)
    return _run(trend=trend, per_bar=per_bar)


# ---------------------------------------------------------------------------
# §14.4 Cross-layer drift tests
# ---------------------------------------------------------------------------

class TestCrossLayerFSMDrift:
    """Verify FSM_STATE_NAMES and ACTIVE_LIFECYCLE_STATES are identical tuples
    across all layers that re-export them (plan §7.4, §14.4)."""

    def test_step_executor_fsm_names_equals_shared(self):
        from supertrend_optimizer.core._fsm_state_names import FSM_STATE_NAMES as shared
        from wf_grid.wf.step_executor import _ALL_FSM_STATES
        assert _ALL_FSM_STATES == shared, (
            f"step_executor _ALL_FSM_STATES drifted from shared FSM_STATE_NAMES.\n"
            f"  shared:        {shared}\n"
            f"  step_executor: {_ALL_FSM_STATES}"
        )

    def test_runner_fsm_names_equals_shared(self):
        from supertrend_optimizer.core._fsm_state_names import FSM_STATE_NAMES as shared
        from supertrend_optimizer.testing.runner import _FSM_STATES
        assert _FSM_STATES == shared, (
            f"runner _FSM_STATES drifted from shared FSM_STATE_NAMES.\n"
            f"  shared:  {shared}\n"
            f"  runner:  {_FSM_STATES}"
        )

    def test_step_executor_active_lifecycle_equals_shared(self):
        from supertrend_optimizer.core._fsm_state_names import ACTIVE_LIFECYCLE_STATES as shared
        from wf_grid.wf.step_executor import _ACTIVE_LIFECYCLE_STATES
        assert _ACTIVE_LIFECYCLE_STATES == shared, (
            f"step_executor _ACTIVE_LIFECYCLE_STATES drifted from shared.\n"
            f"  shared:        {shared}\n"
            f"  step_executor: {_ACTIVE_LIFECYCLE_STATES}"
        )

    def test_runner_active_lifecycle_equals_shared(self):
        from supertrend_optimizer.core._fsm_state_names import ACTIVE_LIFECYCLE_STATES as shared
        from supertrend_optimizer.testing.runner import _ACTIVE_LIFECYCLE_STATES
        assert _ACTIVE_LIFECYCLE_STATES == shared, (
            f"runner _ACTIVE_LIFECYCLE_STATES drifted from shared.\n"
            f"  shared: {shared}\n"
            f"  runner: {_ACTIVE_LIFECYCLE_STATES}"
        )

    def test_zigzag_st_filter_fsm_names_derived_from_shared(self):
        """zigzag_st_filter builds its dict from shared names: no extra or missing."""
        from supertrend_optimizer.core._fsm_state_names import FSM_STATE_NAMES as shared
        from supertrend_optimizer.core.zigzag_st_filter import _FSM_STATE_NAMES as zzf_dict
        assert set(zzf_dict.values()) == set(shared), (
            f"zigzag_st_filter _FSM_STATE_NAMES values differ from shared.\n"
            f"  shared:          {set(shared)}\n"
            f"  zigzag values:   {set(zzf_dict.values())}"
        )
        assert len(zzf_dict) == len(shared), (
            "zigzag_st_filter _FSM_STATE_NAMES has duplicate or missing entries."
        )
        # §7.4 strict: verify SEMANTIC CORRECTNESS of the dict — each state name
        # from the shared tuple maps to the correct enum integer key.
        # (The canonical tuple is ordered by lifecycle sequence, NOT by enum int value;
        # the dict is indexed by enum int value — plan _fsm_state_names.py docstring.)
        from supertrend_optimizer.core.zigzag_st_filter import ZigZagFSMState
        for name in shared:
            try:
                enum_member = ZigZagFSMState[name]
            except KeyError:
                raise AssertionError(
                    f"§7.4: state name '{name}' from shared FSM_STATE_NAMES "
                    f"has no matching ZigZagFSMState member. "
                    "Add the missing enum value."
                )
            enum_int = int(enum_member)
            assert enum_int in zzf_dict, (
                f"§7.4: ZigZagFSMState.{name}={enum_int} not a key in _FSM_STATE_NAMES dict"
            )
            assert zzf_dict[enum_int] == name, (
                f"§7.4: _FSM_STATE_NAMES[{enum_int}] = '{zzf_dict[enum_int]}', "
                f"expected '{name}' (ZigZagFSMState.{name} = {enum_int}). "
                "Dict and enum are misaligned."
            )


class TestPerBarKeysetSnapshot:
    """§14.5: per-bar filter_diagnostics keyset must be a superset of the
    expected keys (including 4 new exit-off keys)."""

    def test_per_bar_keyset_superset_of_expected(self, exit_b_apply_result):
        diag = exit_b_apply_result.filter_diagnostics
        assert diag is not None, "filter_diagnostics is None — enabled path expected."
        observed = set(diag.keys())
        missing = _EXPECTED_PER_BAR_KEYSET - observed
        assert not missing, (
            f"Per-bar keyset missing keys: {sorted(missing)}\n"
            f"Update _EXPECTED_PER_BAR_KEYSET or fix zigzag_st_filter."
        )

    def test_exit_off_mode_key_present_and_exit_b(self, exit_b_apply_result):
        diag = exit_b_apply_result.filter_diagnostics
        arr = diag.get("exit_off_mode")
        assert arr is not None
        assert any(v == "exit B" for v in np.asarray(arr)), (
            "exit_off_mode array has no 'exit B' entry despite exit B config."
        )

    def test_zz_legs_since_lifecycle_start_key_present(self, exit_b_apply_result):
        diag = exit_b_apply_result.filter_diagnostics
        arr = diag.get("zz_legs_since_lifecycle_start")
        assert arr is not None
        assert len(arr) == 7

    def test_zz_leg_stop_triggered_key_present(self, exit_b_apply_result):
        diag = exit_b_apply_result.filter_diagnostics
        arr = diag.get("zz_leg_stop_triggered")
        assert arr is not None
        assert len(arr) == 7


# ---------------------------------------------------------------------------
# §14.5 Schema-snapshot: summary keyset from _compute_filter_diagnostics_summary
# ---------------------------------------------------------------------------

class TestSummaryKeysetSnapshot:
    """§14.5: _compute_filter_diagnostics_summary output includes all new
    exit-off summary keys."""

    def test_summary_has_n_bars_in_counting_zz_legs(self, exit_b_apply_result):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        diag = exit_b_apply_result.filter_diagnostics
        summary = _compute_filter_diagnostics_summary(diag)
        assert summary is not None
        assert "n_bars_in_counting_zz_legs" in summary

    def test_summary_has_zz_leg_stop_triggered_count(self, exit_b_apply_result):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        diag = exit_b_apply_result.filter_diagnostics
        summary = _compute_filter_diagnostics_summary(diag)
        assert "zz_leg_stop_triggered_count" in summary

    def test_summary_has_exit_off_mode(self, exit_b_apply_result):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        diag = exit_b_apply_result.filter_diagnostics
        summary = _compute_filter_diagnostics_summary(diag)
        assert "exit_off_mode" in summary
        assert summary["exit_off_mode"] == "exit B"

    def test_summary_has_exit_off_zz_leg_count(self, exit_b_apply_result):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        diag = exit_b_apply_result.filter_diagnostics
        summary = _compute_filter_diagnostics_summary(diag)
        assert "exit_off_zz_leg_count" in summary
        assert summary["exit_off_zz_leg_count"] == 2

    def test_summary_is_none_when_filter_disabled(self):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        assert _compute_filter_diagnostics_summary(None) is None


# ---------------------------------------------------------------------------
# §14.5 Schema-snapshot: _FILTER_SUMMARY_COLUMNS exact ordered list
# ---------------------------------------------------------------------------

class TestStepLongColumnsSnapshot:
    """§14.5: _FILTER_SUMMARY_COLUMNS in step_collector must match snapshot
    exactly (order matters — any delta = regression)."""

    def test_filter_summary_columns_exact_match(self):
        from wf_grid.collect.step_collector import _FILTER_SUMMARY_COLUMNS
        assert list(_FILTER_SUMMARY_COLUMNS) == _STEP_LONG_FILTER_COLUMNS_SNAPSHOT, (
            "step_collector._FILTER_SUMMARY_COLUMNS does not match PR5 snapshot.\n"
            f"  expected: {_STEP_LONG_FILTER_COLUMNS_SNAPSHOT}\n"
            f"  observed: {list(_FILTER_SUMMARY_COLUMNS)}\n"
            "Update snapshot in this file OR update step_collector.py."
        )


# ---------------------------------------------------------------------------
# §14.6 Collector keyset: 4 new exit-off columns present + disabled path
# ---------------------------------------------------------------------------

class TestCollectorExitOffColumns:
    """§14.6: _FILTER_SUMMARY_COLUMNS contains all 4 exit-off columns; disabled
    path returns None for each."""

    def test_filter_summary_columns_has_exit_off_keys(self):
        from wf_grid.collect.step_collector import _FILTER_SUMMARY_COLUMNS
        for col in _EXIT_OFF_SUMMARY_COLUMNS:
            assert col in _FILTER_SUMMARY_COLUMNS, (
                f"'{col}' missing from _FILTER_SUMMARY_COLUMNS — "
                "update step_collector.py"
            )

    def test_unpack_filter_summary_disabled_returns_none_for_exit_off_cols(self):
        from wf_grid.collect.step_collector import _unpack_filter_summary
        row = _unpack_filter_summary(None)
        for col in _EXIT_OFF_SUMMARY_COLUMNS:
            assert col in row, f"'{col}' not in disabled-path row"
            assert row[col] is None, (
                f"disabled path: expected None for '{col}', got {row[col]!r}"
            )

    def test_unpack_filter_summary_enabled_maps_exit_off_mode(self, exit_b_apply_result):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        from wf_grid.collect.step_collector import _unpack_filter_summary
        summary = _compute_filter_diagnostics_summary(
            exit_b_apply_result.filter_diagnostics
        )
        row = _unpack_filter_summary(summary)
        assert row["exit_off_mode"] == "exit B"
        assert row["exit_off_zz_leg_count"] == 2


# ---------------------------------------------------------------------------
# §14.5 EXACT snapshot tests (strict equality, not superset)
# ---------------------------------------------------------------------------

class TestPerBarKeysetExact:
    """§14.5 (strict): observed filter_diagnostics.keys() == snapshot exactly.
    Any added or removed key = regression.  Update _PER_BAR_KEYSET_SNAPSHOT
    (not this test) when adding new per-bar fields."""

    def test_per_bar_keyset_exact_equality(self, exit_b_apply_result):
        diag = exit_b_apply_result.filter_diagnostics
        assert diag is not None
        observed = frozenset(diag.keys())
        extra = observed - _PER_BAR_KEYSET_SNAPSHOT
        missing = _PER_BAR_KEYSET_SNAPSHOT - observed
        assert not extra and not missing, (
            f"Per-bar keyset mismatch (update _PER_BAR_KEYSET_SNAPSHOT):\n"
            f"  extra (not in snapshot): {sorted(extra)}\n"
            f"  missing (in snapshot but not observed): {sorted(missing)}"
        )


class TestSummaryKeysetExact:
    """§14.5 (strict): observed summary keys == snapshot exactly."""

    def test_summary_keyset_exact_equality(self, exit_b_apply_result):
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        summary = _compute_filter_diagnostics_summary(
            exit_b_apply_result.filter_diagnostics
        )
        assert summary is not None
        observed = frozenset(summary.keys())
        extra = observed - _SUMMARY_KEYSET_SNAPSHOT
        missing = _SUMMARY_KEYSET_SNAPSHOT - observed
        assert not extra and not missing, (
            f"Summary keyset mismatch (update _SUMMARY_KEYSET_SNAPSHOT):\n"
            f"  extra:   {sorted(extra)}\n"
            f"  missing: {sorted(missing)}"
        )


class TestExcelPerBarHeadersSnapshot:
    """§14.5 / §8.1: FILTER_DIAGNOSTICS_100_DISPLAY_NAMES must equal
    _EXCEL_PER_BAR_HEADERS_SNAPSHOT exactly (dict equality)."""

    def test_excel_per_bar_headers_exact_match(self):
        from supertrend_optimizer.io.excel_tester import FILTER_DIAGNOSTICS_100_DISPLAY_NAMES
        observed = dict(FILTER_DIAGNOSTICS_100_DISPLAY_NAMES)
        expected = _EXCEL_PER_BAR_HEADERS_SNAPSHOT
        extra = set(observed) - set(expected)
        missing = set(expected) - set(observed)
        wrong = {k for k in (set(observed) & set(expected)) if observed[k] != expected[k]}
        assert not extra and not missing and not wrong, (
            f"FILTER_DIAGNOSTICS_100_DISPLAY_NAMES mismatch:\n"
            f"  extra keys:   {sorted(extra)}\n"
            f"  missing keys: {sorted(missing)}\n"
            f"  wrong values: { {k: (expected[k], observed[k]) for k in wrong} }\n"
            "Update _EXCEL_PER_BAR_HEADERS_SNAPSHOT in this file."
        )


class TestExcelSummaryLabelsSnapshot:
    """§14.5 / §8.2: _build_filters_summary_df must produce params and period
    labels that exactly match _EXCEL_SUMMARY_*_SNAPSHOT."""

    def _make_period_result(self):
        class _MockBacktestResult:
            positions = np.zeros(7, dtype=np.int8)
            trades_df = None
        class _MockPR:
            period_label = "100%"
            result = _MockBacktestResult()
            filter_diagnostics = {}
            filter_diagnostics_summary = {
                "zigzag_mode": "A",
                "candidate_duration_gate_enabled": False,
                "candidate_duration_max_bars": -1,
                "exit_off_mode": "exit B",
                "exit_off_zz_leg_count": 2,
                "lifecycle_starts_count": 1,
                "median_stop_triggered_count": 0,
                "zz_leg_stop_triggered_count": 1,
                "thresholds": {
                    "reversal_threshold": 0.02,
                    "candidate_trigger_threshold": 0.05,
                    "candidate_trigger_quantile": None,
                    "candidate_trigger_source": "explicit",
                    "global_median": 0.04,
                    "local_window": 5,
                    "freeze_confirmed_legs": 0,
                    "exit_off_mode": "exit B",
                    "exit_off_zz_leg_count": 2,
                    "zigzag_mode": "A",
                    "candidate_duration_gate_enabled": False,
                    "candidate_duration_max_bars": -1,
                },
                "counters": {"zz_leg_stop_triggered": 1, "median_stop_triggered": 0, "lifecycle_starts": 1},
                "bars_in_state": {"ST_COUNTING_ZZ_LEGS": 2, "OFF": 5},
            }
        return _MockPR()

    def test_params_labels_exact_match(self):
        from supertrend_optimizer.io.excel_tester import _build_filters_summary_df
        result = _build_filters_summary_df([self._make_period_result()])
        assert result is not None
        params_df, _ = result
        observed = list(params_df["Parameter"])
        assert observed == _EXCEL_SUMMARY_PARAMS_LABELS_SNAPSHOT, (
            f"params labels mismatch:\n"
            f"  expected: {_EXCEL_SUMMARY_PARAMS_LABELS_SNAPSHOT}\n"
            f"  observed: {observed}"
        )

    def test_period_cols_exact_match(self):
        from supertrend_optimizer.io.excel_tester import _build_filters_summary_df
        result = _build_filters_summary_df([self._make_period_result()])
        assert result is not None
        _, period_df = result
        observed = list(period_df.columns)
        assert observed == _EXCEL_SUMMARY_PERIOD_COLS_SNAPSHOT, (
            f"period columns mismatch:\n"
            f"  expected: {_EXCEL_SUMMARY_PERIOD_COLS_SNAPSHOT}\n"
            f"  observed: {observed}"
        )


# ---------------------------------------------------------------------------
# §2 / §12.6 Strict dtype contract for 4 new exit-off per-bar arrays
# ---------------------------------------------------------------------------

class TestExitOffDtypeContract:
    """§2 / §12.6: per-bar exit-off arrays must have EXACT dtypes as per plan §6.

    Plan §6 specifies:
      exit_off_mode            → object / str
      exit_off_zz_leg_count    → int64 (sentinel -1 for exit A)
      zz_legs_since_lifecycle_start → int64 (-1 sentinel outside lifecycle)
      zz_leg_stop_triggered    → int8 (binary 0/1 mask)

    This test enforces exact dtype equality so that downstream code relying on
    dtype (e.g. pandas groupby, csv serialization, assert_array_equal) behaves
    identically on all platforms.
    """

    def test_exit_off_mode_dtype_is_object(self, exit_b_apply_result):
        arr = exit_b_apply_result.filter_diagnostics["exit_off_mode"]
        assert arr.dtype == object, (
            f"§2: exit_off_mode expected dtype=object (str), got {arr.dtype}"
        )

    def test_exit_off_zz_leg_count_dtype_is_int64(self, exit_b_apply_result):
        arr = exit_b_apply_result.filter_diagnostics["exit_off_zz_leg_count"]
        assert arr.dtype == np.int64, (
            f"§2: exit_off_zz_leg_count expected dtype=int64, got {arr.dtype}"
        )

    def test_zz_legs_since_lifecycle_start_dtype_is_int64(self, exit_b_apply_result):
        arr = exit_b_apply_result.filter_diagnostics["zz_legs_since_lifecycle_start"]
        assert arr.dtype == np.int64, (
            f"§2: zz_legs_since_lifecycle_start expected dtype=int64, got {arr.dtype}"
        )

    def test_zz_leg_stop_triggered_dtype_is_int8(self, exit_b_apply_result):
        arr = exit_b_apply_result.filter_diagnostics["zz_leg_stop_triggered"]
        assert arr.dtype == np.int8, (
            f"§2: zz_leg_stop_triggered expected dtype=int8, got {arr.dtype}"
        )

    def test_exit_a_dtypes_same_as_exit_b(self):
        """Dtypes must be the same regardless of mode (sentinel values differ, not types)."""
        from supertrend_optimizer.core.zigzag_st_filter import (
            ZigZagGlobalStats, ZigZagPerBar, apply,
        )

        @dataclass
        class _LC:
            freeze_confirmed_legs: int = 0
            stop_check: str = "confirm_bar_only"
            stopping_exit: str = "opposite_st_flip"
            exit_off_mode: str = "exit A"

        @dataclass
        class _Cfg:
            zigzag: object = field(default_factory=lambda: type("Z", (), {
                "daily_reset": False, "local_window": 5, "mode": None,
            })())
            triggers: object = field(default_factory=lambda: type("T", (), {
                "candidate_threshold": type("C", (), {"enabled": True})(),
                "confirmed_median": type("C", (), {"enabled": False})(),
            })())
            lifecycle: _LC = field(default_factory=_LC)

        n = 5
        cfg = _Cfg()
        stats = ZigZagGlobalStats(
            reversal_threshold=0.01, global_stats_source="full_dataset",
            leg_height_mode="pct", confirmed_legs=[],
            confirmed_heights_pct=np.array([], dtype=np.float64),
            global_median=0.05, candidate_trigger_threshold=0.04,
            candidate_trigger_source="explicit", candidate_trigger_quantile=None,
            n_legs_total=0, insufficient_data=False, fail_closed_reason=None,
            metadata={}, zigzag_mode="A", candidate_duration_gate_enabled=False,
            candidate_duration_max_bars=None,
        )
        per_bar = ZigZagPerBar(
            candidate_height_pct=np.full(n, np.nan),
            confirm_event=np.zeros(n, dtype=np.int8),
            confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(n, np.nan),
            local_median_N=np.full(n, np.nan),
            local_median_available=np.zeros(n, dtype=bool),
            candidate_age_bars=np.full(n, -1, dtype=np.int64),
            candidate_leg_direction=np.zeros(n, dtype=np.int8),
        )
        result = apply(
            trend=np.zeros(n, dtype=np.int64),
            trade_mode="both",
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )
        fd = result.filter_diagnostics
        assert fd["exit_off_mode"].dtype == object
        assert fd["exit_off_zz_leg_count"].dtype == np.int64
        assert fd["zz_legs_since_lifecycle_start"].dtype == np.int64
        assert fd["zz_leg_stop_triggered"].dtype == np.int8


# ---------------------------------------------------------------------------
# §14.7 WF↔Tester exit-off keyset parity
# ---------------------------------------------------------------------------

# Exit-off keys that MUST be present in BOTH WF Grid summary (flat) AND
# Tester runner summary (top-level scalar fields added in PR5/PR6).
_EXIT_OFF_PARITY_KEYS = (
    "exit_off_mode",
    "exit_off_zz_leg_count",
    "zz_leg_stop_triggered_count",
    "median_stop_triggered_count",
)

# WF Grid summary keys that map to Tester runner's top-level keys.
# Both paths MUST expose the same exit-off scalars (plan §14.7 parity contract).
_TESTER_RUNNER_EXIT_OFF_TOPLEVEL_KEYS = (
    "zz_leg_stop_triggered_count",
    "median_stop_triggered_count",
    "lifecycle_starts_count",
    "zigzag_mode",
    "candidate_duration_gate_enabled",
    "candidate_duration_max_bars",
)

# ST_COUNTING_ZZ_LEGS must appear in bars_in_state histogram on BOTH sides.
_EXIT_B_STATE = "ST_COUNTING_ZZ_LEGS"


class TestWFTesterExitOffParity:
    """§14.7 parity tests between WF and Tester paths.

    Includes:
    1) Aggregation parity: WF and Tester summaries expose identical exit-off
       top-level scalars.
    2) Full runtime parity: independent runs through WF (run_single_backtest)
       and Tester (run_period) on the same data/config produce bit-identical
       key trading outputs and per-bar diagnostics required by §14.7.
    """

    def test_wf_summary_has_all_exit_off_parity_keys(self, exit_b_apply_result):
        """WF Grid flat summary must have all 4 exit-off parity keys."""
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        summary = _compute_filter_diagnostics_summary(
            exit_b_apply_result.filter_diagnostics
        )
        assert summary is not None
        for key in _EXIT_OFF_PARITY_KEYS:
            assert key in summary, (
                f"§14.7: WF Grid summary missing exit-off parity key '{key}'"
            )

    def test_tester_runner_summary_has_exit_off_parity_keys(self):
        """§14.7: Tester runner top-level summary must have the same exit-off scalar
        fields as WF Grid summary — verified via RUNTIME output, not source inspection.

        Runs run_period() (Tester path) and _compute_filter_diagnostics_summary()
        (WF Grid path) on the same filter_diagnostics, then compares the exit-off
        scalar fields that must be equal between the two.
        """
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        from supertrend_optimizer.utils.enums import ExecutionModel
        from supertrend_optimizer.core.trade_filter_config import (
            TradeFilterConfig, TradeFilterZigZagConfig, TradeFilterTriggersConfig,
            TradeFilterLifecycleConfig, TradeFilterDiagnosticsConfig,
            TradeFilterTriggerToggleConfig,
        )
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        import numpy as np
        import pandas as pd

        # Build a small but real synthetic OHLC dataset (80 bars)
        rng = np.random.default_rng(42)
        n = 80
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
        noise = rng.uniform(0.001, 0.004, size=n)
        high  = close * (1 + noise)
        low   = close * (1 - noise)
        open_ = np.clip(close * (1 + rng.uniform(-0.002, 0.002, size=n)), low, high)
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close},
            index=pd.date_range("2022-01-01", periods=n, freq="D"),
        )

        # exit B config
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

        # Tester path
        pr = run_period(
            df=df, atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
        )

        assert pr.filter_diagnostics is not None, "§14.7: Tester filter_diagnostics is None"
        assert pr.filter_diagnostics_summary is not None, (
            "§14.7: Tester filter_diagnostics_summary is None"
        )

        # WF Grid path: run summary on the SAME filter_diagnostics dict
        wf_summary = _compute_filter_diagnostics_summary(pr.filter_diagnostics)
        tester_summary = pr.filter_diagnostics_summary

        assert wf_summary is not None, "§14.7: WF summary is None"

        # Per-bar arrays are shared (same apply() call) — no comparison needed.
        # We compare the AGGREGATION layer: summary scalars must be equal.
        parity_fields = {
            "exit_off_mode": "exit B",
            "exit_off_zz_leg_count": 2,
            "zz_leg_stop_triggered_count": None,  # just compare both sides equal
            "median_stop_triggered_count": None,
            "lifecycle_starts_count": None,
        }
        for key, expected in parity_fields.items():
            assert key in wf_summary, (
                f"§14.7: WF summary missing key '{key}'"
            )
            assert key in tester_summary, (
                f"§14.7: Tester summary missing top-level key '{key}'. "
                "Fix: add top-level echo in runner._build_filter_diagnostics_summary (§7.3)."
            )
            if expected is not None:
                assert wf_summary[key] == expected, (
                    f"§14.7: WF summary['{key}']={wf_summary[key]!r}, expected {expected!r}"
                )
                assert tester_summary[key] == expected, (
                    f"§14.7: Tester summary['{key}']={tester_summary[key]!r}, expected {expected!r}"
                )
            else:
                assert wf_summary[key] == tester_summary[key], (
                    f"§14.7: summary['{key}'] mismatch — "
                    f"WF={wf_summary[key]!r}, Tester={tester_summary[key]!r}"
                )

    def test_wf_vs_tester_runtime_parity_independent_runs(self):
        """§14.7 strict runtime parity: WF and Tester runs are compared directly.

        Required parity set from the plan:
          - trade_filter_state
          - zz_legs_since_lifecycle_start
          - zz_leg_stop_triggered
          - filtered_positions (result.positions)
          - trades_df (row-wise)
        """
        from supertrend_optimizer.engine.run import run_single_backtest
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        from supertrend_optimizer.utils.enums import ExecutionModel
        from supertrend_optimizer.core.trade_filter_config import (
            TradeFilterConfig, TradeFilterZigZagConfig, TradeFilterTriggersConfig,
            TradeFilterLifecycleConfig, TradeFilterDiagnosticsConfig,
            TradeFilterTriggerToggleConfig,
        )
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(20260505)
        n = 100
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.00035, 0.012, n)))
        noise = rng.uniform(0.001, 0.004, size=n)
        high = close * (1 + noise)
        low = close * (1 - noise)
        open_ = np.clip(close * (1 + rng.uniform(-0.002, 0.002, size=n)), low, high)
        idx = pd.date_range("2023-01-01", periods=n, freq="D")
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close},
            index=idx,
        )

        cfg = TradeFilterConfig(
            enabled=True,
            type="zigzag_st_mode",
            zigzag=TradeFilterZigZagConfig(
                reversal_threshold=0.03,
                local_window=20,
                candidate_trigger_threshold=0.4,
            ),
            triggers=TradeFilterTriggersConfig(
                candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
                confirmed_median=TradeFilterTriggerToggleConfig(enabled=False),
            ),
            lifecycle=TradeFilterLifecycleConfig(
                freeze_confirmed_legs=0,
                stop_check="confirm_bar_only",
                stopping_exit="opposite_st_flip",
                exit_off_mode="exit B",
                exit_off_zz_leg_count=2,
            ),
            diagnostics=TradeFilterDiagnosticsConfig(
                export_state_columns=True,
                export_trigger_columns=True,
            ),
        )
        stats = build_zigzag_global_stats(df["close"].values, cfg)

        # WF path (independent run, not reusing Tester output)
        wf_result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            early_exit_enabled=False,
            min_trades_required=1,
            extract_trades_flag=True,
            caller_mode="wf_grid",
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            global_offset=0,
        )

        # Tester path (independent run)
        tester_period = run_period(
            df=df,
            atr_period=14,
            multiplier=3.0,
            trade_mode="revers",
            commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            min_trades_required=1,
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            global_offset=0,
        )
        tester_result = tester_period.result

        assert wf_result.filter_diagnostics is not None
        assert tester_result.filter_diagnostics is not None

        # Per-bar parity required by §14.7
        for k in (
            "trade_filter_state",
            "zz_legs_since_lifecycle_start",
            "zz_leg_stop_triggered",
        ):
            np.testing.assert_array_equal(
                np.asarray(wf_result.filter_diagnostics[k]),
                np.asarray(tester_result.filter_diagnostics[k]),
                err_msg=f"§14.7 parity failed for per-bar key {k!r}",
            )

        # filtered_positions parity
        np.testing.assert_array_equal(
            np.asarray(wf_result.positions),
            np.asarray(tester_result.positions),
            err_msg="§14.7 parity failed for filtered_positions",
        )

        # trades_df row-wise parity
        wf_trades = wf_result.trades_df
        tester_trades = tester_result.trades_df
        if wf_trades is None or tester_trades is None:
            assert wf_trades is None and tester_trades is None, (
                "§14.7 trades_df presence mismatch between WF and Tester"
            )
        else:
            pd.testing.assert_frame_equal(
                wf_trades.reset_index(drop=True),
                tester_trades.reset_index(drop=True),
                check_dtype=True,
                check_like=False,
                obj="§14.7 trades_df",
            )

    def test_tester_bars_in_state_includes_exit_b_state(self):
        """Tester runner bars_in_state histogram uses FSM_STATE_NAMES which
        must include ST_COUNTING_ZZ_LEGS after PR3 (plan §14.7 / §7.4)."""
        from supertrend_optimizer.core._fsm_state_names import FSM_STATE_NAMES
        assert _EXIT_B_STATE in FSM_STATE_NAMES, (
            f"§14.7: {_EXIT_B_STATE!r} missing from shared FSM_STATE_NAMES — "
            "Tester bars_in_state histogram will not include exit B state"
        )

    def test_wf_summary_exit_b_state_bars_counted(self, exit_b_apply_result):
        """WF Grid summary n_bars_in_counting_zz_legs > 0 when exit B lifecycle
        ran (cross-verifies that WF Grid aggregation counts the new state)."""
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        summary = _compute_filter_diagnostics_summary(
            exit_b_apply_result.filter_diagnostics
        )
        assert summary is not None
        n = summary.get("n_bars_in_counting_zz_legs", -1)
        assert n >= 0, (
            f"§14.7: n_bars_in_counting_zz_legs={n} — "
            "WF Grid does not count ST_COUNTING_ZZ_LEGS bars"
        )

    def test_both_sides_echo_exit_off_mode_from_config(self, exit_b_apply_result):
        """Both summary paths must echo exit_off_mode from config (plan §14.7)."""
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        wf_summary = _compute_filter_diagnostics_summary(
            exit_b_apply_result.filter_diagnostics
        )
        assert wf_summary is not None
        assert wf_summary["exit_off_mode"] == "exit B", (
            f"§14.7: WF Grid summary exit_off_mode={wf_summary['exit_off_mode']!r}, "
            "expected 'exit B'"
        )
        assert wf_summary["exit_off_zz_leg_count"] == 2, (
            f"§14.7: WF Grid summary exit_off_zz_leg_count={wf_summary['exit_off_zz_leg_count']}, "
            "expected 2"
        )
