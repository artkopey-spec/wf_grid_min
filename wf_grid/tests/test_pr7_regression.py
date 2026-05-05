"""
PR7 — Regression / fingerprint tests for exit-off modes.

Implements split-test pattern §9.4 (plan_exit_off_modes_v2.txt):

  Group A (bit-identical assertions):
    positions, filtered_positions, state_arr, confirmed_legs_since_start,
    median_stop_triggered, filter_block_reason, filter_allowed_entry.
    These must be bit-identical across (default, exit A explicit, exit B
    in the counting stage BEFORE threshold is reached).

  Group B (superset / sentinel assertions — checked SEPARATELY):
    exit_off_mode_arr      → "exit A" for default and exit A; "exit B" for exit B
    exit_off_zz_leg_count_arr → -1 for default/exit A; N for exit B
    zz_legs_since_lifecycle_start_arr → -1 sentinel on default/exit A; counter on exit B
    zz_leg_stop_triggered_arr → 0 everywhere for default/exit A; 1 on stop bar for exit B

R6 — Backward compat: default config (no exit_off fields) == exit A explicit (Group A).
R7 — Exit A vs Exit B differ in Group B arrays.
R8 — Sentinel values pin (§9.4 Group B invariants for default config).

Anti-drift rule (§9.4 / §14 CI-rule):
  Do NOT compare full filter_diagnostics dict.
  Always split into Group A and Group B assertions.
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
from wf_grid.tests._diagnostics_assertions import (
    assert_baseline_fingerprint,
    assert_diagnostics_superset,
    EXIT_B_SENTINEL_MAP,
)


# ---------------------------------------------------------------------------
# Shared helpers (self-contained, independent from other test files)
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
class _LifecycleDefault:
    """Default lifecycle — NO exit_off fields (simulates pre-PR1 config).
    apply() reads exit_off_mode via getattr(..., 'exit A') → "exit A" path."""
    freeze_confirmed_legs: int = 0
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"
    # intentionally NO exit_off_mode / exit_off_zz_leg_count fields


@dataclass
class _LifecycleExitA:
    """Explicit exit A — fields present with canonical values."""
    freeze_confirmed_legs: int = 0
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"
    exit_off_mode: str = "exit A"
    exit_off_zz_leg_count: Optional[int] = None  # sentinel -1 via getattr


@dataclass
class _LifecycleExitB:
    freeze_confirmed_legs: int = 0
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"
    exit_off_mode: str = "exit B"
    exit_off_zz_leg_count: int = 2


@dataclass
class _FilterCfgDefault:
    zigzag: _ZigZagDouble = field(default_factory=_ZigZagDouble)
    triggers: _TriggersDouble = field(default_factory=_TriggersDouble)
    lifecycle: _LifecycleDefault = field(default_factory=_LifecycleDefault)


@dataclass
class _FilterCfgExitA:
    zigzag: _ZigZagDouble = field(default_factory=_ZigZagDouble)
    triggers: _TriggersDouble = field(default_factory=_TriggersDouble)
    lifecycle: _LifecycleExitA = field(default_factory=_LifecycleExitA)


@dataclass
class _FilterCfgExitB:
    zigzag: _ZigZagDouble = field(default_factory=_ZigZagDouble)
    triggers: _TriggersDouble = field(default_factory=_TriggersDouble)
    lifecycle: _LifecycleExitB = field(default_factory=_LifecycleExitB)


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


def _make_per_bar(*, n: int, candidate_height_pct=None, confirm_event=None) -> ZigZagPerBar:
    if candidate_height_pct is None:
        candidate_height_pct = np.full(n, np.nan, dtype=np.float64)
    if confirm_event is None:
        confirm_event = np.zeros(n, dtype=np.int8)
    return ZigZagPerBar(
        candidate_height_pct=candidate_height_pct,
        confirm_event=confirm_event,
        confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
        last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
        local_median_N=np.full(n, np.nan, dtype=np.float64),
        local_median_available=np.zeros(n, dtype=bool),
        candidate_age_bars=np.full(n, -1, dtype=np.int64),
        candidate_leg_direction=np.zeros(n, dtype=np.int8),
    )


def _run(*, trend, per_bar, cfg, daily_reset_event=None):
    n = len(trend)
    if daily_reset_event is None:
        daily_reset_event = np.zeros(n, dtype=bool)
    return apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=_make_stats(),
        per_bar=per_bar,
        daily_reset_event=daily_reset_event,
    )


# ---------------------------------------------------------------------------
# Canonical 8-bar scenario used across R6 and R7
# ---------------------------------------------------------------------------
#
# bar 0: trend=-1                         → OFF
# bar 1: trend=+1, candidate=0.06         → WAIT → FREEZE/COUNTING (lifecycle start)
# bar 2: confirm=1                        → confirmed_legs_since_start=1; exit A: FREEZE (freeze=0 → MONITORING); exit B: zz=1
# bar 3: confirm=1                        → exit A: MONITORING; exit B: zz=2 → THRESHOLD → ST_STOPPING
# bar 4: trend=-1 (opp flip, pos held)   → exit A: MONITORING; exit B: position closed
# bars 5-7: trend=-1

_N = 8
_TREND = np.array([-1, 1, 1, 1, -1, -1, -1, -1], dtype=np.int64)
_CAND  = np.array([np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan])
_CONF  = np.array([0, 0, 1, 1, 0, 0, 0, 0], dtype=np.int8)


def _per_bar():
    return _make_per_bar(n=_N, candidate_height_pct=_CAND, confirm_event=_CONF)


# ---------------------------------------------------------------------------
# R6 — Backward compat: Group A (bit-identical) + Group B (sentinels)
# ---------------------------------------------------------------------------

class TestR6BackwardCompat:
    """R6 §9.4: default config (no exit_off fields) is bit-identical to exit A
    explicit config in Group A arrays.  Group B arrays have expected sentinels."""

    def _results(self):
        pb = _per_bar()
        r_def  = _run(trend=_TREND, per_bar=pb, cfg=_FilterCfgDefault())
        pb2 = _per_bar()
        r_ea   = _run(trend=_TREND, per_bar=pb2, cfg=_FilterCfgExitA())
        return r_def, r_ea

    # -- Group A (bit-identical) --

    def test_r6a_positions_bit_identical(self):
        r_def, r_ea = self._results()
        np.testing.assert_array_equal(
            r_def.positions, r_ea.positions,
            err_msg="Group A: positions differ between default and exit A explicit",
        )

    def test_r6a_state_arr_bit_identical(self):
        r_def, r_ea = self._results()
        assert list(r_def.filter_diagnostics["trade_filter_state"]) == \
               list(r_ea.filter_diagnostics["trade_filter_state"]), (
            "Group A: trade_filter_state differs between default and exit A explicit"
        )

    def test_r6a_filter_block_reason_bit_identical(self):
        r_def, r_ea = self._results()
        np.testing.assert_array_equal(
            r_def.filter_diagnostics["filter_block_reason"],
            r_ea.filter_diagnostics["filter_block_reason"],
            err_msg="Group A: filter_block_reason differs between default and exit A explicit",
        )

    def test_r6a_filter_allowed_entry_bit_identical(self):
        r_def, r_ea = self._results()
        np.testing.assert_array_equal(
            r_def.filter_diagnostics["filter_allowed_entry"],
            r_ea.filter_diagnostics["filter_allowed_entry"],
            err_msg="Group A: filter_allowed_entry differs between default and exit A explicit",
        )

    def test_r6a_confirmed_legs_since_start_bit_identical(self):
        r_def, r_ea = self._results()
        np.testing.assert_array_equal(
            r_def.filter_diagnostics["confirmed_legs_since_start"],
            r_ea.filter_diagnostics["confirmed_legs_since_start"],
            err_msg="Group A: confirmed_legs_since_start differs",
        )

    # -- Group B (sentinel checks on default) --

    def test_r6b_default_exit_off_mode_echo(self):
        """Group B: default config → exit_off_mode array echoes 'exit A'."""
        r_def, _ = self._results()
        arr = np.asarray(r_def.filter_diagnostics["exit_off_mode"])
        assert all(v == "exit A" for v in arr), (
            f"Group B: expected all 'exit A', got {set(arr)}"
        )

    def test_r6b_default_exit_off_zz_leg_count_sentinel(self):
        """Group B: default config → exit_off_zz_leg_count echoes -1 (sentinel)."""
        r_def, _ = self._results()
        arr = np.asarray(r_def.filter_diagnostics["exit_off_zz_leg_count"])
        assert all(int(v) == -1 for v in arr), (
            f"Group B: expected all -1, got {set(arr)}"
        )

    def test_r6b_default_zz_legs_since_start_sentinel(self):
        """Group B: default config → zz_legs_since_lifecycle_start is -1 sentinel
        everywhere (exit A never increments this counter)."""
        r_def, _ = self._results()
        arr = np.asarray(r_def.filter_diagnostics["zz_legs_since_lifecycle_start"])
        assert all(int(v) == -1 for v in arr), (
            f"Group B: expected all -1 (exit A sentinel), got {set(arr)}"
        )

    def test_r6b_default_zz_leg_stop_triggered_zero(self):
        """Group B: default config → zz_leg_stop_triggered is 0 everywhere."""
        r_def, _ = self._results()
        arr = np.asarray(r_def.filter_diagnostics["zz_leg_stop_triggered"])
        assert all(int(v) == 0 for v in arr), (
            f"Group B: expected all 0, got {set(arr)}"
        )

    def test_r6b_exit_a_explicit_echo_same_sentinels(self):
        """Group B: exit A explicit → same sentinels as default."""
        _, r_ea = self._results()
        arr_mode  = np.asarray(r_ea.filter_diagnostics["exit_off_mode"])
        arr_count = np.asarray(r_ea.filter_diagnostics["exit_off_zz_leg_count"])
        arr_legs  = np.asarray(r_ea.filter_diagnostics["zz_legs_since_lifecycle_start"])
        arr_stop  = np.asarray(r_ea.filter_diagnostics["zz_leg_stop_triggered"])
        assert all(v == "exit A" for v in arr_mode), f"mode: {set(arr_mode)}"
        assert all(int(v) == -1 for v in arr_count), f"count: {set(arr_count)}"
        assert all(int(v) == -1 for v in arr_legs),  f"legs: {set(arr_legs)}"
        assert all(int(v) == 0  for v in arr_stop),  f"stop: {set(arr_stop)}"

    # -- §9.4 helper-based assertions (plan-canonical pattern) --

    def test_r6_helper_group_a_bit_identical(self):
        """§9.4: assert_baseline_fingerprint covers Group A in one call."""
        r_def, r_ea = self._results()
        assert_baseline_fingerprint(r_def, r_ea, context="R6")

    def test_r6_helper_group_b_sentinels_default(self):
        """§9.4: assert_diagnostics_superset confirms Group B sentinels for default config."""
        r_def, _ = self._results()
        assert_diagnostics_superset(r_def, context="R6-default")


# ---------------------------------------------------------------------------
# R7 — Exit B Group B assertions (counter, threshold, stop)
# ---------------------------------------------------------------------------

class TestR7ExitBGroupB:
    """R7 §9.4 Group B: exit B (count=2) produces distinct Group B arrays.

    Scenario (8 bars, from _TREND/_CAND/_CONF above):
      bar 0: OFF
      bar 1: candidate=0.06 → lifecycle starts → ST_COUNTING_ZZ_LEGS (zz=0)
      bar 2: confirm → zz=1
      bar 3: confirm → zz=2 = count → THRESHOLD: ST_STOPPING, zz_leg_stop_triggered=1
      bar 4+: trend=-1 → opposite flip while holding → position closed, state→OFF
    """

    def _result(self):
        pb = _per_bar()
        return _run(trend=_TREND, per_bar=pb, cfg=_FilterCfgExitB())

    def test_r7_exit_b_mode_echo(self):
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["exit_off_mode"])
        assert all(v == "exit B" for v in arr), f"mode: {set(arr)}"

    def test_r7_exit_b_count_echo(self):
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["exit_off_zz_leg_count"])
        assert all(int(v) == 2 for v in arr), f"count: {set(arr)}"

    def test_r7_zz_legs_counter_increments(self):
        """zz_legs_since_lifecycle_start starts at 0 on lifecycle-start bar,
        increments on each confirm, is -1 (sentinel) on OFF bars."""
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["zz_legs_since_lifecycle_start"], dtype=int)
        # bar 1: lifecycle starts → zz=0
        assert arr[1] == 0, f"bar 1 expected zz=0, got {arr[1]}"
        # bar 2: first confirm → zz=1
        assert arr[2] == 1, f"bar 2 expected zz=1, got {arr[2]}"
        # bar 3: second confirm → zz=2, threshold reached (but still in ST_STOPPING this bar)
        assert arr[3] == 2, f"bar 3 expected zz=2, got {arr[3]}"

    def test_r7_zz_leg_stop_triggered_on_threshold_bar(self):
        """zz_leg_stop_triggered=1 exactly on the bar where threshold is reached."""
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["zz_leg_stop_triggered"], dtype=int)
        assert arr[3] == 1, f"bar 3 (threshold bar) expected 1, got {arr[3]}"
        # bars before threshold: 0
        assert all(arr[i] == 0 for i in [0, 1, 2]), (
            f"Before threshold bars not 0: {arr[:3]}"
        )

    def test_r7_exit_b_stop_triggered_by_zz_legs_not_median(self):
        """Group B invariant: in exit B the stop is triggered by zz-leg count
        (zz_leg_stop_triggered=1), NOT by median (median_stop_triggered must be 0
        on the threshold bar)."""
        r_eb = self._result()
        zz_stop   = np.asarray(r_eb.filter_diagnostics["zz_leg_stop_triggered"], dtype=int)
        med_stop  = np.asarray(r_eb.filter_diagnostics["median_stop_triggered"], dtype=int)
        # bar 3: zz-leg stop = 1, median stop = 0
        assert zz_stop[3]  == 1, f"exit B bar 3 zz_leg_stop_triggered expected 1, got {zz_stop[3]}"
        assert med_stop[3] == 0, f"exit B bar 3 median_stop_triggered expected 0, got {med_stop[3]}"

    def test_r7_states_before_threshold_match_exit_a(self):
        """Before the threshold bar, exit B states are identical to exit A
        (both in ST_COUNTING_ZZ_LEGS vs ST_ACTIVE_MONITORING — different labels
        but both are valid lifecycle states).

        Weaker invariant: both bars 0 and 1 match OFF / WAIT between modes.
        """
        pb_b = _per_bar()
        pb_a = _per_bar()
        r_eb = _run(trend=_TREND, per_bar=pb_b, cfg=_FilterCfgExitB())
        r_ea = _run(trend=_TREND, per_bar=pb_a, cfg=_FilterCfgExitA())
        state_eb = list(r_eb.filter_diagnostics["trade_filter_state"])
        state_ea = list(r_ea.filter_diagnostics["trade_filter_state"])
        # bar 0: both OFF
        assert state_eb[0] == "OFF" == state_ea[0]
        # bar 1: both WAIT (first candidate flip) OR lifecycle started
        # (they both transit from WAIT the same way on bar 1)
        assert state_eb[1] in ("WAIT_FIRST_ST_FLIP", "ST_COUNTING_ZZ_LEGS")
        assert state_ea[1] in ("WAIT_FIRST_ST_FLIP", "ST_ACTIVE_MONITORING")


# ---------------------------------------------------------------------------
# R8 — Sentinel pin: default config produces exactly 4 new exit-off per-bar
#      arrays, all with sentinel values (§9.4 Group B invariant table)
# ---------------------------------------------------------------------------

class TestR6cGroupAAdditions:
    """R6c §9.4: additional Group A bit-identical checks for default vs exit A.
    Covers filtered_positions (= positions from apply result) and
    median_stop_triggered (plan §9.4 explicit list)."""

    def _results(self):
        pb = _per_bar()
        r_def = _run(trend=_TREND, per_bar=pb, cfg=_FilterCfgDefault())
        pb2 = _per_bar()
        r_ea  = _run(trend=_TREND, per_bar=pb2, cfg=_FilterCfgExitA())
        return r_def, r_ea

    def test_r6c_filtered_positions_bit_identical(self):
        """filtered_positions (= ZigZagSTFilterResult.positions) must be
        bit-identical between default and exit A explicit."""
        r_def, r_ea = self._results()
        np.testing.assert_array_equal(
            r_def.positions, r_ea.positions,
            err_msg="Group A: filtered_positions differ between default and exit A explicit",
        )

    def test_r6c_median_stop_triggered_bit_identical(self):
        """median_stop_triggered array must be identical: exit A uses the same
        median stop logic in both default and explicit exit A mode."""
        r_def, r_ea = self._results()
        np.testing.assert_array_equal(
            r_def.filter_diagnostics["median_stop_triggered"],
            r_ea.filter_diagnostics["median_stop_triggered"],
            err_msg="Group A: median_stop_triggered differs between default and exit A explicit",
        )

    def test_r6c_trade_filter_trigger_source_bit_identical(self):
        r_def, r_ea = self._results()
        np.testing.assert_array_equal(
            r_def.filter_diagnostics["trade_filter_trigger_source"],
            r_ea.filter_diagnostics["trade_filter_trigger_source"],
            err_msg="Group A: trade_filter_trigger_source differs",
        )


class TestR8SentinelPin:
    """R8: pin the sentinel values for the 4 new exit-off arrays on default
    config (plan §9.4 Group B / §2 table).

    These values must NOT change without an explicit plan update.
    """

    @pytest.fixture(scope="class")
    def default_diag(self):
        pb = _per_bar()
        r = _run(trend=_TREND, per_bar=pb, cfg=_FilterCfgDefault())
        return r.filter_diagnostics

    def test_r8_exit_off_mode_key_present(self, default_diag):
        assert "exit_off_mode" in default_diag

    def test_r8_exit_off_zz_leg_count_key_present(self, default_diag):
        assert "exit_off_zz_leg_count" in default_diag

    def test_r8_zz_legs_since_lifecycle_start_key_present(self, default_diag):
        assert "zz_legs_since_lifecycle_start" in default_diag

    def test_r8_zz_leg_stop_triggered_key_present(self, default_diag):
        assert "zz_leg_stop_triggered" in default_diag

    def test_r8_exit_off_mode_sentinel_is_exit_a(self, default_diag):
        arr = np.asarray(default_diag["exit_off_mode"])
        assert set(arr) == {"exit A"}, f"expected only 'exit A', got {set(arr)}"

    def test_r8_exit_off_zz_leg_count_sentinel_is_minus_one(self, default_diag):
        arr = np.asarray(default_diag["exit_off_zz_leg_count"], dtype=int)
        assert np.all(arr == -1), f"expected all -1, got unique: {np.unique(arr)}"

    def test_r8_zz_legs_since_lifecycle_start_sentinel_is_minus_one(self, default_diag):
        """exit A never uses the zz-legs counter → stays at -1 sentinel."""
        arr = np.asarray(default_diag["zz_legs_since_lifecycle_start"], dtype=int)
        assert np.all(arr == -1), f"expected all -1, got unique: {np.unique(arr)}"

    def test_r8_zz_leg_stop_triggered_sentinel_is_zero(self, default_diag):
        """exit A never fires zz_leg_stop_triggered → stays at 0."""
        arr = np.asarray(default_diag["zz_leg_stop_triggered"], dtype=int)
        assert np.all(arr == 0), f"expected all 0, got unique: {np.unique(arr)}"

    def test_r8_helper_group_b_superset(self):
        """§9.4: assert_diagnostics_superset covers all Group B sentinels in one call."""
        pb = _per_bar()
        r = _run(trend=_TREND, per_bar=pb, cfg=_FilterCfgDefault())
        assert_diagnostics_superset(r, context="R8-sentinel-pin")


# ---------------------------------------------------------------------------
# §14.2 Missing scenarios: multiple lifecycle cycles + no-confirm lifecycle
# ---------------------------------------------------------------------------

class TestMultipleLifecycleCycles:
    """§14.2: exit B, count=2, with two lifecycle cycles in sequence.

    Scenario (14 bars):
      Cycle 1 (bars 0-6):
        bar 0: trend=-1
        bar 1: trend=+1, candidate=0.06 → lifecycle start → ST_COUNTING (zz=0)
        bar 2: confirm → zz=1
        bar 3: confirm → zz=2 → THRESHOLD → ST_STOPPING, zz_leg_stop=1
        bar 4: trend=-1 (opp flip) → close, back to OFF
        bars 5-6: trend=-1, OFF
      Cycle 2 (bars 7-13):
        bar 7: trend=+1, candidate=0.06 → new lifecycle start
        bar 8: confirm → zz=1
        bar 9: confirm → zz=2 → THRESHOLD again → ST_STOPPING, zz_leg_stop=1
        bar 10: trend=-1 → close, OFF
        bars 11-13: OFF
    """

    _N = 14
    _TREND = np.array([-1, 1, 1, 1, -1, -1, -1, 1, 1, 1, -1, -1, -1, -1], dtype=np.int64)
    _CAND  = np.array([np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan,
                       0.06,   np.nan, np.nan, np.nan, np.nan, np.nan, np.nan])
    _CONF  = np.array([0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0], dtype=np.int8)

    def _result(self):
        n = self._N
        pb = ZigZagPerBar(
            candidate_height_pct=self._CAND,
            confirm_event=self._CONF,
            confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
            local_median_N=np.full(n, np.nan, dtype=np.float64),
            local_median_available=np.zeros(n, dtype=bool),
            candidate_age_bars=np.full(n, -1, dtype=np.int64),
            candidate_leg_direction=np.zeros(n, dtype=np.int8),
        )
        daily_reset = np.zeros(n, dtype=bool)
        return apply(
            trend=self._TREND,
            trade_mode="both",
            trade_filter_config=_FilterCfgExitB(),
            zigzag_global_stats=_make_stats(),
            per_bar=pb,
            daily_reset_event=daily_reset,
        )

    def test_second_lifecycle_counter_resets_to_zero(self):
        """After ST_STOPPING→OFF, a new lifecycle start resets zz_legs_since=0."""
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["zz_legs_since_lifecycle_start"], dtype=int)
        # Second lifecycle start at bar 7 → zz=0
        assert arr[7] == 0, f"bar 7 (2nd lifecycle start) expected zz=0, got {arr[7]}"
        # bar 8: first confirm → zz=1
        assert arr[8] == 1, f"bar 8 expected zz=1, got {arr[8]}"

    def test_zz_leg_stop_triggered_fires_twice(self):
        """zz_leg_stop_triggered=1 on both threshold bars (bar 3 and bar 9)."""
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["zz_leg_stop_triggered"], dtype=int)
        assert arr[3] == 1, f"bar 3 (1st threshold) expected 1, got {arr[3]}"
        assert arr[9] == 1, f"bar 9 (2nd threshold) expected 1, got {arr[9]}"
        # Off bars between cycles must be 0
        assert all(arr[i] == 0 for i in [4, 5, 6, 7, 8]), (
            f"Between-cycle bars expected 0: {arr[4:9]}"
        )

    def test_off_bars_reset_counter_to_sentinel(self):
        """After lifecycle ends (→OFF), zz_legs_since=-1 sentinel."""
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["zz_legs_since_lifecycle_start"], dtype=int)
        # bar 0: OFF → -1
        assert arr[0] == -1
        # bars 5-6: between cycles → -1
        for i in [5, 6]:
            assert arr[i] == -1, f"bar {i} (between cycles) expected -1, got {arr[i]}"


class TestLifecycleWithoutConfirms:
    """§14.2: lifecycle starts but no confirm events occur.

    Verifies that zz_legs_since_lifecycle_start stays at 0 throughout
    the lifecycle (counter never increments without confirms).

    Scenario (8 bars, exit B count=3):
      bar 0: trend=-1
      bar 1: trend=+1, candidate=0.06 → lifecycle start → ST_COUNTING (zz=0)
      bars 2-6: no confirm events → zz stays at 0
      bar 7: trend=-1 → opposite flip (exits if position held)
    """

    _N = 8
    _TREND = np.array([-1, 1, 1, 1, 1, 1, 1, -1], dtype=np.int64)
    _CAND  = np.array([np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan])
    _CONF  = np.zeros(8, dtype=np.int8)  # no confirms

    def _result(self):
        pb = ZigZagPerBar(
            candidate_height_pct=self._CAND,
            confirm_event=self._CONF,
            confirmed_leg_idx_at_t=np.full(self._N, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(self._N, np.nan, dtype=np.float64),
            local_median_N=np.full(self._N, np.nan, dtype=np.float64),
            local_median_available=np.zeros(self._N, dtype=bool),
            candidate_age_bars=np.full(self._N, -1, dtype=np.int64),
            candidate_leg_direction=np.zeros(self._N, dtype=np.int8),
        )
        cfg = _FilterCfgExitB()
        cfg.lifecycle.exit_off_zz_leg_count = 3
        return apply(
            trend=self._TREND,
            trade_mode="both",
            trade_filter_config=cfg,
            zigzag_global_stats=_make_stats(),
            per_bar=pb,
            daily_reset_event=np.zeros(self._N, dtype=bool),
        )

    def test_no_confirms_counter_stays_zero_during_lifecycle(self):
        """With no confirm events, zz_legs_since stays 0 throughout lifecycle."""
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["zz_legs_since_lifecycle_start"], dtype=int)
        state_arr = r.filter_diagnostics["trade_filter_state"]
        for i in range(1, self._N):
            if state_arr[i] == "ST_COUNTING_ZZ_LEGS":
                assert arr[i] == 0, (
                    f"bar {i} in ST_COUNTING_ZZ_LEGS, no confirms → expected zz=0, got {arr[i]}"
                )

    def test_no_confirms_no_stop_triggered(self):
        """Without confirms, the threshold is never reached → zz_leg_stop_triggered=0."""
        r = self._result()
        arr = np.asarray(r.filter_diagnostics["zz_leg_stop_triggered"], dtype=int)
        assert np.all(arr == 0), (
            f"No confirms: expected all zz_leg_stop_triggered=0, got {np.unique(arr)}"
        )

    def test_no_confirms_exit_b_count1_immediate_threshold(self):
        """With count=1, even no confirms (only candidate trigger) can cross threshold.
        But if lifecycle starts via candidate_trigger and count=1, the first ZZ confirm
        is needed to trigger. Without confirm, no stop fires even with count=1."""
        cfg = _FilterCfgExitB()
        cfg.lifecycle.exit_off_zz_leg_count = 1
        pb = ZigZagPerBar(
            candidate_height_pct=self._CAND,
            confirm_event=self._CONF,  # no confirms
            confirmed_leg_idx_at_t=np.full(self._N, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(self._N, np.nan, dtype=np.float64),
            local_median_N=np.full(self._N, np.nan, dtype=np.float64),
            local_median_available=np.zeros(self._N, dtype=bool),
            candidate_age_bars=np.full(self._N, -1, dtype=np.int64),
            candidate_leg_direction=np.zeros(self._N, dtype=np.int8),
        )
        r = apply(
            trend=self._TREND, trade_mode="both",
            trade_filter_config=cfg, zigzag_global_stats=_make_stats(),
            per_bar=pb, daily_reset_event=np.zeros(self._N, dtype=bool),
        )
        arr = np.asarray(r.filter_diagnostics["zz_leg_stop_triggered"], dtype=int)
        assert np.all(arr == 0), (
            "count=1 but no confirms: expected no stop triggered, got "
            f"{np.unique(arr)}"
        )
