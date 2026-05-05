"""PR3 — exit-off runtime tests (plan_exit_off_modes_v2.txt §13 PR3, §14.2).

Scope:
  - exit B counter increments on confirmed legs while state == ST_COUNTING_ZZ_LEGS;
  - median-check does NOT fire in exit B (median_stop_triggered all-zero);
  - КРИТИЧЕСКИЙ КЕЙС reset+confirm same-bar (R3 §11.6) for BOTH exit A and exit B;
  - shared FSM_STATE_NAMES module exposes canonical tuples (§7.4).

These tests intentionally use lightweight duck-typed fixtures that include
exit_off_* fields, so they can drive ``apply()`` without going through the
full WF Grid loader.  Existing test_daily_reset / test_wp5_zigzag_fsm
fixtures do not carry exit_off_* fields and continue to use the default
"exit A" path via getattr-fallback in apply().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagFSMState,
    ZigZagGlobalStats,
    ZigZagPerBar,
    apply,
)


# ---------------------------------------------------------------------------
# Lightweight duck-typed config doubles with exit_off_* fields
# ---------------------------------------------------------------------------

@dataclass
class _ToggleDouble:
    enabled: bool = True


@dataclass
class _TriggersDouble:
    candidate_threshold: _ToggleDouble = field(default_factory=_ToggleDouble)
    confirmed_median: _ToggleDouble = field(default_factory=_ToggleDouble)


@dataclass
class _ZigZagDouble:
    daily_reset: bool = False
    local_window: int = 5


@dataclass
class _LifecycleDouble:
    freeze_confirmed_legs: int = 5
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"
    exit_off_mode: str = "exit A"
    exit_off_zz_leg_count: Optional[int] = None


@dataclass
class _FilterCfgDouble:
    zigzag: _ZigZagDouble = field(default_factory=_ZigZagDouble)
    triggers: _TriggersDouble = field(default_factory=_TriggersDouble)
    lifecycle: _LifecycleDouble = field(default_factory=_LifecycleDouble)


def _make_filter_cfg(
    *,
    a_enabled: bool = True,
    b_enabled: bool = True,
    freeze_confirmed_legs: int = 0,
    exit_off_mode: str = "exit A",
    exit_off_zz_leg_count: Optional[int] = None,
) -> _FilterCfgDouble:
    return _FilterCfgDouble(
        triggers=_TriggersDouble(
            candidate_threshold=_ToggleDouble(enabled=a_enabled),
            confirmed_median=_ToggleDouble(enabled=b_enabled),
        ),
        lifecycle=_LifecycleDouble(
            freeze_confirmed_legs=freeze_confirmed_legs,
            exit_off_mode=exit_off_mode,
            exit_off_zz_leg_count=exit_off_zz_leg_count,
        ),
    )


def _make_global_stats(
    *,
    global_median: float = 0.05,
    candidate_trigger_threshold: float = 0.05,
    reversal_threshold: float = 0.01,
) -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=reversal_threshold,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=global_median,
        candidate_trigger_threshold=candidate_trigger_threshold,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
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
    return ZigZagPerBar(
        candidate_height_pct=candidate_height_pct,
        confirm_event=confirm_event,
        confirmed_leg_idx_at_t=confirmed_leg_idx_at_t,
        last_confirmed_leg_height_pct=last_confirmed_leg_height_pct,
        local_median_N=local_median_N,
        local_median_available=local_median_available,
    )


def _run(*, trend, per_bar, daily_reset_event, cfg=None, stats=None):
    return apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=cfg if cfg is not None else _make_filter_cfg(),
        zigzag_global_stats=stats if stats is not None else _make_global_stats(),
        per_bar=per_bar,
        daily_reset_event=daily_reset_event,
    )


# ===========================================================================
# §7.4 Shared FSM_STATE_NAMES module
# ===========================================================================

class TestSharedFSMStateNames:
    """Plan §7.4 / §13 PR3: shared module exposes canonical FSM tuples in
    the canonical order from the plan. Tuple-equality (not set-equality)
    is enforced so any reorder is a regression."""

    def test_module_imports(self):
        from supertrend_optimizer.core import _fsm_state_names as m
        assert isinstance(m.FSM_STATE_NAMES, tuple)
        assert isinstance(m.ACTIVE_LIFECYCLE_STATES, tuple)

    def test_fsm_state_names_canonical_order(self):
        """Plan §7.4 canonical order — tuple-equality, not set."""
        from supertrend_optimizer.core._fsm_state_names import FSM_STATE_NAMES
        assert FSM_STATE_NAMES == (
            "OFF",
            "WAIT_FIRST_ST_FLIP",
            "ST_ACTIVE_FREEZE",
            "ST_ACTIVE_MONITORING",
            "ST_COUNTING_ZZ_LEGS",
            "ST_STOPPING",
        )

    def test_active_lifecycle_states_canonical_order(self):
        """Plan §7.4 canonical order — tuple-equality, not set."""
        from supertrend_optimizer.core._fsm_state_names import ACTIVE_LIFECYCLE_STATES
        assert ACTIVE_LIFECYCLE_STATES == (
            "ST_ACTIVE_FREEZE",
            "ST_ACTIVE_MONITORING",
            "ST_COUNTING_ZZ_LEGS",
        )

    def test_zigzag_st_filter_uses_shared_names(self):
        """Single-source-of-truth check: zigzag_st_filter._FSM_STATE_NAMES
        derives every name from the shared tuple (plan §13 PR3)."""
        from supertrend_optimizer.core._fsm_state_names import FSM_STATE_NAMES
        from supertrend_optimizer.core.zigzag_st_filter import _FSM_STATE_NAMES
        assert set(_FSM_STATE_NAMES.values()) == set(FSM_STATE_NAMES), (
            "zigzag_st_filter._FSM_STATE_NAMES drift from shared FSM_STATE_NAMES; "
            f"local: {sorted(_FSM_STATE_NAMES.values())}, "
            f"shared: {sorted(FSM_STATE_NAMES)}"
        )


# ===========================================================================
# Default behaviour: no exit_off keys (Group A baseline)
# ===========================================================================

class TestExitOffDefaultPath:
    """Plan §9.1: default config (no exit_off keys) preserves exit A semantics."""

    def test_default_exit_off_mode_echo_is_exit_a(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        result = _run(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )
        eom = result.filter_diagnostics["exit_off_mode"]
        assert all(v == "exit A" for v in eom)

    def test_default_exit_off_zz_leg_count_echo_is_minus_one(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        result = _run(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )
        ec = result.filter_diagnostics["exit_off_zz_leg_count"]
        assert (ec == -1).all()

    def test_default_zz_legs_since_lifecycle_start_all_sentinel(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        result = _run(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )
        zz = result.filter_diagnostics["zz_legs_since_lifecycle_start"]
        assert (zz == -1).all()

    def test_default_zz_leg_stop_triggered_all_zero(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        result = _run(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )
        st = result.filter_diagnostics["zz_leg_stop_triggered"]
        assert (st == 0).all()


# ===========================================================================
# Exit B runtime: counter increments + no median-check
# ===========================================================================

class TestExitBCounterIncrements:
    """Plan §13 PR3: exit B counter grows on confirmed legs in
    state_at_bar_start == ST_COUNTING_ZZ_LEGS."""

    def test_counter_increments_after_lifecycle_start(self):
        """After OFF→WAIT (candidate_threshold) and WAIT→ST_COUNTING_ZZ_LEGS
        (ST flip) on bar 1 (start, zz_legs=0), each confirm_event in COUNTING
        state increments by +1.

        Bars: 5
            bar 0: trend=-1
            bar 1: trend=+1, candidate_height=0.06 (>threshold 0.05)
                   → OFF→WAIT (candidate_threshold), WAIT→COUNTING (ST flip)
                   zz_legs=0 (M3: lifecycle start bar)
            bar 2: confirm=1 (state_at_bar_start=COUNTING → zz_legs=1)
            bar 3: confirm=0 (no change → zz_legs=1)
            bar 4: confirm=1 (state_at_bar_start=COUNTING → zz_legs=2)
        """
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 1], dtype=np.int8),
        )
        cfg = _make_filter_cfg(
            exit_off_mode="exit B",
            exit_off_zz_leg_count=10,  # large, threshold not reached
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
            cfg=cfg,
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        zz = result.filter_diagnostics["zz_legs_since_lifecycle_start"]

        assert states[1] == "ST_COUNTING_ZZ_LEGS"
        assert zz[1] == 0  # M3: bar of lifecycle start
        assert zz[2] == 1
        assert zz[3] == 1
        assert zz[4] == 2

    def test_median_check_does_not_fire_in_exit_b(self):
        """Plan §4.3: in exit B, local_median_N < global_median MUST NOT
        trigger ST_STOPPING; median_stop_triggered all-zero."""
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 1], dtype=np.int8),
            local_median_N=np.array([np.nan, np.nan, 0.001, np.nan, 0.001]),
            local_median_available=np.array([False, False, True, False, True]),
        )
        cfg = _make_filter_cfg(
            exit_off_mode="exit B",
            exit_off_zz_leg_count=10,
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
            cfg=cfg,
            stats=_make_global_stats(global_median=0.05),  # ≫ local
        )
        median_arr = result.filter_diagnostics["median_stop_triggered"]
        assert (median_arr == 0).all(), (
            "median_stop_triggered must be all-zero in exit B; "
            f"got {list(median_arr)}"
        )

    def test_exit_b_skips_freeze_and_monitoring_states(self):
        """Plan §4.2 / X3: in exit B state never visits FREEZE/MONITORING."""
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 1], dtype=np.int8),
        )
        cfg = _make_filter_cfg(
            exit_off_mode="exit B",
            exit_off_zz_leg_count=10,
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
            cfg=cfg,
        )
        states = set(result.filter_diagnostics["trade_filter_state"])
        assert "ST_ACTIVE_FREEZE" not in states
        assert "ST_ACTIVE_MONITORING" not in states


# ===========================================================================
# КРИТИЧЕСКИЙ КЕЙС §14.2 / R3 §11.6 — reset+confirm same-bar
# ===========================================================================

class TestResetGateExitA:
    """R3: on a reset bar with confirm_event==1, confirmed_legs_since_start
    must NOT receive a spurious +1 over the wiped sentinel.

    Setup (n=4, freeze=10 to stay in FREEZE):
        bar 0: trend=-1
        bar 1: trend=+1 (ST flip → WAIT → FREEZE, confirmed_legs=0)
        bar 2: trend=+1, confirm=1, no reset
                state_at_bar_start=FREEZE → confirmed_legs += 1 → 1
        bar 3: trend=+1, confirm=1, RESET=1
                state_at_bar_start=FREEZE (snapshot before wipe)
                wipe → state=OFF, confirmed_legs=-1
                reset-gate skips increment
                EXPECTED: confirmed_legs[3] == -1 (NOT 0)
    """

    def test_reset_plus_confirm_does_not_inc_confirmed_legs(self):
        n = 4
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 1], dtype=np.int8),
        )
        cfg = _make_filter_cfg(
            freeze_confirmed_legs=10,
            exit_off_mode="exit A",
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, False, True]),
            cfg=cfg,
        )
        diag = result.filter_diagnostics

        assert diag["trade_filter_state"][1] == "ST_ACTIVE_FREEZE"
        assert diag["confirmed_legs_since_start"][1] == 0
        assert diag["confirmed_legs_since_start"][2] == 1, (
            "Bar 2: no reset, confirm=1 → counter must increment"
        )
        assert diag["trade_filter_state"][3] == "OFF"
        assert diag["confirmed_legs_since_start"][3] == -1, (
            "R3 §11.6 invariant violated: reset-bar with confirm=1 "
            f"must wipe counter to -1, got {diag['confirmed_legs_since_start'][3]}"
        )
        assert diag["median_stop_triggered"][3] == 0


class TestResetGateExitB:
    """R3 for exit B: same scenario but ST_COUNTING_ZZ_LEGS counter."""

    def test_reset_plus_confirm_does_not_inc_zz_legs(self):
        n = 4
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 1], dtype=np.int8),
        )
        cfg = _make_filter_cfg(
            exit_off_mode="exit B",
            exit_off_zz_leg_count=10,  # high, no threshold trigger
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, False, True]),
            cfg=cfg,
        )
        diag = result.filter_diagnostics

        assert diag["trade_filter_state"][1] == "ST_COUNTING_ZZ_LEGS"
        assert diag["zz_legs_since_lifecycle_start"][1] == 0
        assert diag["zz_legs_since_lifecycle_start"][2] == 1, (
            "Bar 2: no reset, confirm=1 → zz_legs must increment"
        )
        assert diag["trade_filter_state"][3] == "OFF"
        assert diag["zz_legs_since_lifecycle_start"][3] == -1, (
            "R3 §11.6 invariant violated: reset-bar with confirm=1 "
            f"must wipe zz_legs to -1, got "
            f"{diag['zz_legs_since_lifecycle_start'][3]}"
        )
        assert diag["zz_leg_stop_triggered"][3] == 0


# ===========================================================================
# Echo arrays §11.3 E1, E2
# ===========================================================================

class TestExitOffEcho:
    """E1, E2: echo arrays are constants matching resolved config."""

    def test_exit_b_echoes_mode_and_count(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        cfg = _make_filter_cfg(
            exit_off_mode="exit B",
            exit_off_zz_leg_count=7,
        )
        result = _run(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
            cfg=cfg,
        )
        eom = result.filter_diagnostics["exit_off_mode"]
        ec = result.filter_diagnostics["exit_off_zz_leg_count"]
        assert all(v == "exit B" for v in eom)
        assert (ec == 7).all()

    def test_exit_a_echoes_mode_and_sentinel_count(self):
        n = 3
        per_bar = _make_per_bar(n=n)
        cfg = _make_filter_cfg(exit_off_mode="exit A")
        result = _run(
            trend=np.zeros(n, dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
            cfg=cfg,
        )
        eom = result.filter_diagnostics["exit_off_mode"]
        ec = result.filter_diagnostics["exit_off_zz_leg_count"]
        assert all(v == "exit A" for v in eom)
        assert (ec == -1).all()
