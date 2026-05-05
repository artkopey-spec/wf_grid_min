"""PR4 — exit B threshold + same-bar guard tests
(plan_exit_off_modes_v2.txt §13 PR4, §14.2, §14.3 G1/G2).

Scope:
  - main runtime exit B (count=3) — counter, threshold, state transitions;
  - boundary count=1 — ST-flip path AND immediate-entry path (§14.2 граничный);
  - freeze_confirmed_legs is ignored in exit B (§4.2);
  - daily reset at multiple lifecycle stages (counter wipe to -1);
  - counter freeze in ST_STOPPING (gate state_at_bar_start);
  - same-bar guard property tests G1, G2 (§11.7, §14.3).
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
# Lightweight fixtures (duplicated from test_pr3_exit_off_runtime to keep
# the PR-stage test files independent and self-contained).
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
    mode: Optional[str] = None  # None → legacy resolution


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


def _make_cfg(
    *,
    a_enabled: bool = True,
    b_enabled: bool = True,
    freeze_confirmed_legs: int = 0,
    exit_off_mode: str = "exit B",
    exit_off_zz_leg_count: Optional[int] = 3,
    mode: Optional[str] = None,
) -> _FilterCfgDouble:
    return _FilterCfgDouble(
        zigzag=_ZigZagDouble(mode=mode),
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


def _make_stats(
    *,
    global_median: float = 0.05,
    candidate_trigger_threshold: float = 0.05,
    reversal_threshold: float = 0.01,
    zigzag_mode: str = "A",
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
        zigzag_mode=zigzag_mode,
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


def _run(*, trend, per_bar, daily_reset_event=None, cfg=None, stats=None,
         trade_mode: str = "both"):
    n = len(trend)
    if daily_reset_event is None:
        daily_reset_event = np.zeros(n, dtype=bool)
    return apply(
        trend=trend,
        trade_mode=trade_mode,
        trade_filter_config=cfg if cfg is not None else _make_cfg(),
        zigzag_global_stats=stats if stats is not None else _make_stats(),
        per_bar=per_bar,
        daily_reset_event=daily_reset_event,
    )


# ===========================================================================
# §14.2: основной runtime exit B (count=3)
# ===========================================================================

class TestExitBMainRuntimeCount3:
    """Plan §14.2: основной exit B, count=3.

    Setup (n=8, ST-flip at K=1, 3 confirmed legs after K):
        bar 0: trend=-1
        bar 1: trend=+1, candidate=0.06 → OFF→WAIT, WAIT→COUNTING (zz=0)
        bar 2: confirm=1                 → zz=1
        bar 3: no confirm                 → zz=1
        bar 4: confirm=1                 → zz=2
        bar 5: confirm=1                 → zz=3 → THRESHOLD: ST_STOPPING,
                                            zz_leg_stop_triggered=1
        bar 6: trend=-1 (opposite ST flip on a held position) → close
        bar 7: trend=-1
    """

    def _build(self, *, n=8):
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]
            ),
            confirm_event=np.array([0, 0, 1, 0, 1, 1, 0, 0], dtype=np.int8),
        )
        trend = np.array([-1, 1, 1, 1, 1, 1, -1, -1], dtype=np.int64)
        cfg = _make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=3)
        return self._run_apply(trend, per_bar, cfg)

    def _run_apply(self, trend, per_bar, cfg):
        return _run(trend=trend, per_bar=per_bar, cfg=cfg)

    def test_zz_legs_progression_K_to_K3(self):
        result = self._build()
        zz = result.filter_diagnostics["zz_legs_since_lifecycle_start"]
        assert zz[1] == 0   # M3: bar of lifecycle start
        assert zz[2] == 1   # +1 after first confirm
        assert zz[3] == 1   # no confirm
        assert zz[4] == 2   # +1
        assert zz[5] == 3   # +1, threshold reached

    def test_threshold_bar_state_is_stopping(self):
        result = self._build()
        states = list(result.filter_diagnostics["trade_filter_state"])
        assert states[5] == "ST_STOPPING"

    def test_zz_leg_stop_triggered_only_at_threshold_bar(self):
        result = self._build()
        st = result.filter_diagnostics["zz_leg_stop_triggered"]
        assert st[5] == 1
        assert st.sum() == 1, (
            f"zz_leg_stop_triggered must fire exactly once; got {list(st)}"
        )

    def test_median_stop_triggered_all_zero(self):
        result = self._build()
        ms = result.filter_diagnostics["median_stop_triggered"]
        assert (ms == 0).all()

    def test_no_freeze_or_monitoring_states_visited(self):
        result = self._build()
        states = set(result.filter_diagnostics["trade_filter_state"])
        assert "ST_ACTIVE_FREEZE" not in states
        assert "ST_ACTIVE_MONITORING" not in states


# ===========================================================================
# §14.2 same-bar guard G2: no close/reverse on threshold bar
# ===========================================================================

class TestThresholdBarSameBarGuard:
    """G2 §11.7 / §14.3: on the threshold bar (zz_leg_stop_triggered==1)
    filtered_positions[t+1] must equal cur_pos (no close, no reverse).
    Actual close happens only on the next opposite ST flip.
    """

    def test_no_close_on_threshold_bar(self):
        n = 8
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]
            ),
            confirm_event=np.array([0, 0, 1, 0, 1, 1, 0, 0], dtype=np.int8),
        )
        trend = np.array([-1, 1, 1, 1, 1, 1, -1, -1], dtype=np.int64)
        result = _run(
            trend=trend,
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=3),
        )
        positions = result.positions
        # bar 5 is the threshold bar; held position at start is +1 (long).
        # filtered_positions[6] (open of bar 6) must remain +1, not 0.
        assert positions[5] == 1, (
            f"Long position must be held at threshold bar; got {positions[5]}"
        )
        assert positions[6] == 1, (
            f"On threshold bar (5), filtered_positions[t+1] must equal "
            f"cur_pos (1). Got positions[6]={positions[6]}; whole "
            f"positions={list(positions)}"
        )

    def test_close_happens_on_subsequent_opposite_flip(self):
        """After threshold, next opposite ST flip must close (positions[t+1]=0)."""
        n = 8
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]
            ),
            confirm_event=np.array([0, 0, 1, 0, 1, 1, 0, 0], dtype=np.int8),
        )
        trend = np.array([-1, 1, 1, 1, 1, 1, -1, -1], dtype=np.int64)
        result = _run(
            trend=trend,
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=3),
        )
        positions = result.positions
        # bar 6 has trend flip +1→-1 in ST_STOPPING with cur_pos=+1 → close
        # filtered_positions[7] must be 0
        assert positions[7] == 0, (
            f"Opposite ST flip at bar 6 in ST_STOPPING must close position; "
            f"positions={list(positions)}"
        )

    def test_state_arr_off_after_close(self):
        """After opposite-flip close, state at the NEXT bar should be OFF."""
        n = 8
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]
            ),
            confirm_event=np.array([0, 0, 1, 0, 1, 1, 0, 0], dtype=np.int8),
        )
        trend = np.array([-1, 1, 1, 1, 1, 1, -1, -1], dtype=np.int64)
        result = _run(
            trend=trend,
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=3),
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        # bar 6 is the closing bar (state at end of 6 = OFF after close)
        assert states[6] == "OFF", (
            f"After opposite flip closure, state must be OFF; states={states}"
        )


# ===========================================================================
# §14.2: граничный count=1, ST-flip path
# ===========================================================================

# ===========================================================================
# §14.2: граничный count=1, immediate-entry path (mode C)
# ===========================================================================

class TestExitBBoundaryCount1Immediate:
    """count=1 + mode C (immediate-entry path).

    Plan §14.2:
        counter=0 на баре K (immediate start),
        первая подтверждённая нога ПОСЛЕ K → ST_COUNTING→ST_STOPPING,
        lifecycle НЕ стирается normalisation'ом (same-bar guard §5 шаг 11).

    Setup (n=5, mode C):
        bar 0: candidate_height=0.06, cand_leg_dir=+1
               → candidate_component_ok=True AND immediate_allowed=True
               → OFF → ST_COUNTING_ZZ_LEGS, zz=0, held_pos=+1, positions[1]=+1
        bar 1: confirm=1
               → state_at_bar_start=ST_COUNTING → zz=1 ≥ 1 → THRESHOLD
               → ST_STOPPING, zz_leg_stop_triggered=1
               → cur_pos=+1 → same-bar guard: positions[2]=+1 (not 0)
        bar 2: trend continues, no opposite flip → positions[3]=+1
        bar 3: trend=-1 (opposite flip) → close → positions[4]=0
        bar 4: OFF
    """

    def _build(self):
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([0.06, np.nan, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 1, 0, 0, 0], dtype=np.int8),
            candidate_leg_direction=np.array([1, 0, 0, 0, 0], dtype=np.int8),
        )
        # Mode C: candidate_component_ok requires height >= threshold (0.05 here).
        # immediate_allowed requires cand_dir_t in {-1, +1}.
        stats = _make_stats(
            zigzag_mode="C",
            candidate_trigger_threshold=0.05,
        )
        cfg = _make_cfg(
            exit_off_mode="exit B",
            exit_off_zz_leg_count=1,
            freeze_confirmed_legs=999,  # irrelevant in exit B
        )
        return _run(
            trend=np.array([1, 1, 1, -1, -1], dtype=np.int64),
            per_bar=per_bar,
            cfg=cfg,
            stats=stats,
        )

    def test_lifecycle_starts_immediately_with_zz_zero(self):
        """bar 0: immediate → ST_COUNTING_ZZ_LEGS, zz=0 (M3)."""
        result = self._build()
        diag = result.filter_diagnostics
        assert diag["trade_filter_state"][0] == "ST_COUNTING_ZZ_LEGS"
        assert diag["zz_legs_since_lifecycle_start"][0] == 0

    def test_threshold_fires_on_first_confirm_leg_after_start(self):
        """bar 1: confirm=1, state_at_bar_start=COUNTING → zz=1 ≥ 1 → ST_STOPPING."""
        result = self._build()
        diag = result.filter_diagnostics
        assert diag["zz_legs_since_lifecycle_start"][1] == 1
        assert diag["zz_leg_stop_triggered"][1] == 1
        assert diag["trade_filter_state"][1] == "ST_STOPPING"

    def test_no_normalisation_to_off_on_threshold_bar(self):
        """same-bar guard prevents ST_STOPPING + cur_pos→OFF normalisation
        on threshold bar; lifecycle survives as ST_STOPPING."""
        result = self._build()
        diag = result.filter_diagnostics
        # threshold bar = 1; state must remain ST_STOPPING (not OFF)
        assert diag["trade_filter_state"][1] == "ST_STOPPING", (
            f"Same-bar normalisation guard failed: state at threshold bar "
            f"is '{diag['trade_filter_state'][1]}', expected 'ST_STOPPING'"
        )

    def test_position_held_after_threshold_bar(self):
        """G2: positions[2] must equal cur_pos[1] (no close on threshold bar)."""
        result = self._build()
        positions = result.positions
        # positions[1] = held_pos from immediate entry = +1
        # positions[2] must be +1 (same-bar guard active)
        assert positions[1] == 1, f"Position at threshold bar entry: {list(positions)}"
        assert positions[2] == 1, (
            f"G2 violation: positions[2]={positions[2]} ≠ cur_pos[1]={positions[1]}; "
            f"same-bar guard failed. Full positions: {list(positions)}"
        )

    def test_close_on_subsequent_opposite_flip(self):
        """After threshold, opposite ST flip at bar 3 closes position."""
        result = self._build()
        positions = result.positions
        # bar 3: trend=-1 (opposite of held +1 in ST_STOPPING) → close
        assert positions[4] == 0, (
            f"Expected close on opposite flip at bar 3; "
            f"positions={list(positions)}"
        )

    def test_no_freeze_or_monitoring_states(self):
        """X3: exit B never visits FREEZE/MONITORING."""
        result = self._build()
        states = set(result.filter_diagnostics["trade_filter_state"])
        assert "ST_ACTIVE_FREEZE" not in states
        assert "ST_ACTIVE_MONITORING" not in states


# ===========================================================================
# §14.2: граничный count=1, ST-flip path
# ===========================================================================

class TestExitBBoundaryCount1STFlip:
    """count=1: first leg after K → ST_STOPPING.

    Setup (n=5):
        bar 0: trend=-1
        bar 1: candidate=0.06, trend=+1 → OFF→WAIT, WAIT→COUNTING (zz=0)
        bar 2: confirm=1                 → zz=1 → THRESHOLD
        bar 3: trend=-1                  → opposite flip closes
        bar 4: trend=-1
    """

    def test_first_leg_triggers_stopping(self):
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 0], dtype=np.int8),
        )
        result = _run(
            trend=np.array([-1, 1, 1, -1, -1], dtype=np.int64),
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=1),
        )
        zz = result.filter_diagnostics["zz_legs_since_lifecycle_start"]
        st = result.filter_diagnostics["zz_leg_stop_triggered"]
        states = list(result.filter_diagnostics["trade_filter_state"])

        assert zz[1] == 0
        assert zz[2] == 1
        assert st[2] == 1
        assert states[2] == "ST_STOPPING"

    def test_position_at_t_plus_1_held_after_threshold(self):
        """G2: at threshold bar, filtered_positions[t+1] == cur_pos."""
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 0], dtype=np.int8),
        )
        result = _run(
            trend=np.array([-1, 1, 1, -1, -1], dtype=np.int64),
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=1),
        )
        positions = result.positions
        assert positions[2] == 1
        assert positions[3] == 1, (
            f"filtered_positions[3] must equal cur_pos[2]=1, got {positions[3]}"
        )


# ===========================================================================
# §14.2: freeze ignored in exit B
# ===========================================================================

class TestFreezeIgnoredInExitB:
    """§4.2: freeze_confirmed_legs has NO effect in exit B."""

    def test_high_freeze_does_not_block_exit_b_threshold(self):
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 0], dtype=np.int8),
        )
        cfg = _make_cfg(
            exit_off_mode="exit B",
            exit_off_zz_leg_count=1,
            freeze_confirmed_legs=999,  # would block forever in exit A
        )
        result = _run(
            trend=np.array([-1, 1, 1, -1, -1], dtype=np.int64),
            per_bar=per_bar,
            cfg=cfg,
        )
        st = result.filter_diagnostics["zz_leg_stop_triggered"]
        assert st[2] == 1, (
            f"freeze=999 must NOT block exit B threshold; "
            f"zz_leg_stop_triggered={list(st)}"
        )


# ===========================================================================
# §14.2: counter freeze in ST_STOPPING
# ===========================================================================

class TestCounterFrozenInStopping:
    """§4.2: in ST_STOPPING, the gate state_at_bar_start == ST_COUNTING_ZZ_LEGS
    is false, so confirm events do NOT increment the counter further."""

    def test_confirm_in_stopping_does_not_increment(self):
        """count=2 → after threshold (zz=2), additional confirms keep state
        ST_STOPPING (not COUNTING) so zz_legs is frozen at 2."""
        n = 7
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan]
            ),
            # confirms at 2,3 (build to threshold), then more at 4,5 (must NOT inc)
            confirm_event=np.array([0, 0, 1, 1, 1, 1, 0], dtype=np.int8),
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=2),
        )
        zz = result.filter_diagnostics["zz_legs_since_lifecycle_start"]
        states = list(result.filter_diagnostics["trade_filter_state"])

        assert states[3] == "ST_STOPPING", f"states={states}"
        assert zz[3] == 2, "threshold bar"
        # Bars 4, 5 — additional confirms in ST_STOPPING
        assert states[4] == "ST_STOPPING"
        assert states[5] == "ST_STOPPING"
        assert zz[4] == 2, (
            f"counter must be frozen in ST_STOPPING; got zz[4]={zz[4]}"
        )
        assert zz[5] == 2, (
            f"counter must be frozen in ST_STOPPING; got zz[5]={zz[5]}"
        )


# ===========================================================================
# §14.2: daily reset at multiple lifecycle stages
# ===========================================================================

class TestDailyResetInExitB:
    """Daily reset wipes state→OFF and zz_legs→-1 regardless of stage."""

    def test_reset_at_counting_stage(self):
        """Reset while in ST_COUNTING_ZZ_LEGS → state=OFF, zz_legs=-1."""
        n = 4
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0], dtype=np.int8),
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, False, True]),
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=10),
        )
        diag = result.filter_diagnostics
        assert diag["trade_filter_state"][2] == "ST_COUNTING_ZZ_LEGS"
        assert diag["trade_filter_state"][3] == "OFF"
        assert diag["zz_legs_since_lifecycle_start"][3] == -1

    def test_reset_at_stopping_stage(self):
        """Reset while in ST_STOPPING → state=OFF, zz_legs=-1."""
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 0], dtype=np.int8),
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            daily_reset_event=np.array([False, False, False, True, False]),
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=1),
        )
        diag = result.filter_diagnostics
        assert diag["trade_filter_state"][2] == "ST_STOPPING"
        assert diag["trade_filter_state"][3] == "OFF"
        assert diag["zz_legs_since_lifecycle_start"][3] == -1


# ===========================================================================
# Property tests §14.3 G1 / G2 / S4 invariants
# ===========================================================================

class TestSameBarGuardInvariants:
    """§11.7 / §14.3: G1, G2 invariants verified across multiple scenarios."""

    @pytest.mark.parametrize("count,n_legs", [(1, 1), (2, 2), (3, 3)])
    def test_g1_one_shot_per_lifecycle(self, count, n_legs):
        """G1 / S4: zz_leg_stop_triggered count <= lifecycle_starts in any run."""
        n = 8
        confirms = np.zeros(n, dtype=np.int8)
        # Place exactly n_legs confirm events after lifecycle start (bar 1)
        for i in range(n_legs):
            confirms[2 + i] = 1
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]
            ),
            confirm_event=confirms,
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=count),
        )
        st = result.filter_diagnostics["zz_leg_stop_triggered"]
        # one lifecycle in this dataset → max one trigger
        assert int(st.sum()) <= 1, (
            f"G1: zz_leg_stop_triggered.sum()={st.sum()} exceeds "
            f"lifecycle_starts (=1). triggers={list(st)}"
        )

    def test_g2_no_close_or_reverse_on_threshold_bar(self):
        """G2 §11.7: ∀t such that zz_leg_stop_triggered[t]==1,
        filtered_positions[t+1] ∈ {filtered_positions[t], 0 при OFF}."""
        n = 6
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 1, 0, 0], dtype=np.int8),
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=2),
        )
        st = result.filter_diagnostics["zz_leg_stop_triggered"]
        positions = result.positions

        for t in range(n - 1):
            if st[t] == 1:
                assert positions[t + 1] == positions[t], (
                    f"G2 violation at t={t}: zz_leg_stop_triggered=1 but "
                    f"filtered_positions[{t+1}]={positions[t+1]} != "
                    f"filtered_positions[{t}]={positions[t]}"
                )

    def test_no_state_off_after_threshold_bar_until_opposite_flip(self):
        """Threshold-bar same-bar normalisation must NOT push state to OFF
        on the threshold bar itself (guard §5 шаг 11)."""
        n = 5
        per_bar = _make_per_bar(
            n=n,
            candidate_height_pct=np.array([np.nan, 0.06, np.nan, np.nan, np.nan]),
            confirm_event=np.array([0, 0, 1, 0, 0], dtype=np.int8),
        )
        result = _run(
            trend=np.array([-1, 1, 1, 1, 1], dtype=np.int64),
            per_bar=per_bar,
            cfg=_make_cfg(exit_off_mode="exit B", exit_off_zz_leg_count=1),
        )
        states = list(result.filter_diagnostics["trade_filter_state"])
        st = result.filter_diagnostics["zz_leg_stop_triggered"]

        # Threshold bar must be ST_STOPPING (not OFF — guard works).
        threshold_bar = int(np.argmax(st))
        assert st[threshold_bar] == 1
        assert states[threshold_bar] == "ST_STOPPING", (
            f"State at threshold bar must be ST_STOPPING (not OFF after "
            f"normalisation guard); got {states[threshold_bar]} at t={threshold_bar}"
        )
