"""
Opus-reserved GATE tests for ZigZag filter (plan v2.0.1 §6.1 and §10 risks).

These tests encode structural invariants whose breakage would silently
corrupt trading decisions.  They are deliberately written as strict
black-box assertions against the public `compute_zigzag_filter` contract
(where possible) and minimal unit checks on `_run_armament_state_machine`
(where a specific internal pathway is required).

Tests in this file (per user instruction, opus-reserved):

  1. test_confirm_bar_reason_priority          — §10.10 GATE (§1.6 branch order)
  2. test_one_shot_reset_on_new_pivot          — §10.11 GATE (§1.4a step 5)
  3. test_stflip_zero_to_one_not_counted       — §G.2.11
  4. test_invariant_blocked_iff_reason_not_ok  — §G.1.2 (GATE)
  5. test_leg_at_t_not_in_stats_at_t_enters_at_t_plus_1  — §G.2.2

See plan §8 "Порядок реализации" — the failure of any one of these blocks
the release of Stage 4.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    ARMED_SIDE_LONG,
    ARMED_SIDE_NONE,
    ARMED_SIDE_SHORT,
    FIRED_NO_NEW_PIVOT,
    FIRED_NONE,
    FIRED_YES_SHOT,
    LEG_DIR_DOWN,
    LEG_DIR_UNKNOWN,
    LEG_DIR_UP,
    REGIME_OPEN_ACTIVE,
    REGIME_OPEN_GRACE,
    _compute_allow_entry_and_reason,
    _LegRegimeInfo,
    _LegStatsSnapshot,
    _PartialLeg,
    _run_armament_state_machine,
    compute_zigzag_filter,
)
from supertrend_optimizer.utils.constants import (
    FILTER_REASON_OK,
    FILTER_REASON_ZZ_ARMED_WAITING,
    FILTER_REASON_ZZ_EXPIRED_NEW_PIVOT,
    FILTER_REASON_ZZ_EXPIRED_TIME,
    FILTER_REASON_ZZ_LOCKED_SAME_LEG,
    FILTER_REASON_ZZ_NOT_ARMED,
    FILTER_REASON_ZZ_PATHOLOGICAL,
    FILTER_REASON_ZZ_REGIME_OFF,
    FILTER_REASON_ZZ_WARMUP,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mk_leg(
    leg_id: int, direction: int, confirm_bar: int,
    height_pct: float = 0.05, start_bar: int = 0, end_bar: int | None = None,
) -> _PartialLeg:
    if end_bar is None:
        end_bar = confirm_bar - 1
    start_price = 100.0
    end_price = (start_price * (1.0 + height_pct) if direction == LEG_DIR_UP
                 else start_price * (1.0 - height_pct))
    return _PartialLeg(
        leg_id=leg_id,
        start_bar=start_bar,
        end_bar=end_bar,
        confirm_bar=confirm_bar,
        start_price=start_price,
        end_price=end_price,
        direction=direction,
        height_pct=height_pct,
        length_bars=end_bar - start_bar,
        confirm_lag_bars=confirm_bar - end_bar,
    )


def _mk_snap(n_before: int, g_med: float = 0.01, g_p80: float = 0.02,
             l_med: float = float("nan")) -> _LegStatsSnapshot:
    return _LegStatsSnapshot(
        n_legs_before=n_before, global_median=g_med,
        global_p80=g_p80, local_median=l_med,
    )


def _mk_reg(state: int = REGIME_OPEN_GRACE, is_strong: bool = True,
            opened: bool = False, n_since: int = 1) -> _LegRegimeInfo:
    return _LegRegimeInfo(
        state_at_confirm=state,
        opened_regime=opened,
        closed_regime=False,
        n_legs_since_regime_open=n_since,
        is_strong=is_strong,
    )


# ===========================================================================
# 1. test_confirm_bar_reason_priority  (§10.10 GATE, §1.6 branch order)
# ===========================================================================


class TestConfirmBarReasonPriority:
    """On the confirm_bar of a NEW leg that disarms an OLD armed leg via
    NO_NEW_PIVOT, the decision-bar reason MUST be zz_expired_new_pivot —
    NOT zz_not_armed.  This encodes plan §1.6 mandatory branch order and
    guards against plan §10.10 risk."""

    def test_confirm_bar_reason_priority_unit(self):
        # Two legs: leg 0 (UP) armed on bar 5; leg 1 (DOWN) confirms on bar 10
        # → NO_NEW_PIVOT disarm on bar 10.  On the SAME bar 10 leg 1 could
        # attempt to arm (it's also strong + grace regime), so we must verify
        # the reason attribution respects branch priority even when armed=True
        # on the confirm-bar.
        N = 30
        legs = [
            _mk_leg(0, LEG_DIR_UP,   confirm_bar=5,  height_pct=0.05),
            _mk_leg(1, LEG_DIR_DOWN, confirm_bar=10, height_pct=0.05, start_bar=5),
        ]
        snaps = [_mk_snap(50), _mk_snap(51)]
        regs = [_mk_reg(REGIME_OPEN_GRACE, is_strong=True),
                _mk_reg(REGIME_OPEN_GRACE, is_strong=True)]

        leg_outs, arr = _run_armament_state_machine(
            legs=legs, regime_infos=regs, snapshots=snaps,
            st_trend=np.zeros(N, dtype=np.int8),
            high=np.zeros(N), low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=np.zeros(N, dtype=bool),
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        # Sanity: leg 0 disarmed by NO_NEW_PIVOT on bar 10.
        assert leg_outs[0].fired == FIRED_NO_NEW_PIVOT
        # Sanity: new_pivot_disarm flag is set on bar 10.
        assert arr.new_pivot_disarm_on_this_bar[10]
        # Sanity: leg 1 arms on bar 10 (strong + grace + warmup OK).
        assert leg_outs[1].arm_bar == 10
        assert arr.armed[10]   # armed=True on the confirm bar

        # Decision: on bar 10 even though armed=True, reason MUST be
        # zz_expired_new_pivot (branch 5 beats branch 6/7/8).
        allow, reason = _compute_allow_entry_and_reason(
            n_bars=N,
            pathological=np.zeros(N, dtype=bool),
            n_legs_before=np.full(N, 50, dtype=np.int64),
            leg_direction=np.full(N, LEG_DIR_UP, dtype=np.int8),
            regime_state=np.full(N, REGIME_OPEN_GRACE, dtype=np.int8),
            armed=arr.armed,
            armed_side=arr.armed_side,
            one_shot=arr.one_shot_fired_current_leg,
            timeout_expired_on_bar=arr.timeout_expired_on_this_bar,
            new_pivot_disarm_on_bar=arr.new_pivot_disarm_on_this_bar,
            st_flip_on_bar=arr.st_flip_on_this_bar,
            min_legs_global=10,
        )
        assert reason[10] == FILTER_REASON_ZZ_EXPIRED_NEW_PIVOT, (
            f"expected zz_expired_new_pivot on confirm_bar with old armed disarm; "
            f"got {reason[10]!r}.  Branch order violation — see plan §10.10."
        )
        assert not allow[10]

    def test_confirm_bar_reason_priority_with_simultaneous_flip(self):
        # Extreme case: on the confirm_bar, a NEW leg arms AND st_flip fires
        # on the same bar.  Branch order still mandates zz_expired_new_pivot.
        N = 30
        legs = [
            _mk_leg(0, LEG_DIR_UP,   confirm_bar=5),
            _mk_leg(1, LEG_DIR_UP,   confirm_bar=10, start_bar=5),
        ]
        snaps = [_mk_snap(50), _mk_snap(51)]
        regs = [_mk_reg(), _mk_reg()]
        # Construct trend that triggers a st_flip at bar 10 for armed SHORT.
        trend = np.zeros(N, dtype=np.int8)
        trend[:10] = +1
        trend[10:] = -1
        _, arr = _run_armament_state_machine(
            legs=legs, regime_infos=regs, snapshots=snaps,
            st_trend=trend, high=np.zeros(N), low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=np.zeros(N, dtype=bool),
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        assert arr.new_pivot_disarm_on_this_bar[10]

        allow, reason = _compute_allow_entry_and_reason(
            n_bars=N,
            pathological=np.zeros(N, dtype=bool),
            n_legs_before=np.full(N, 50, dtype=np.int64),
            leg_direction=np.full(N, LEG_DIR_UP, dtype=np.int8),
            regime_state=np.full(N, REGIME_OPEN_GRACE, dtype=np.int8),
            armed=arr.armed,
            armed_side=arr.armed_side,
            one_shot=arr.one_shot_fired_current_leg,
            timeout_expired_on_bar=arr.timeout_expired_on_this_bar,
            new_pivot_disarm_on_bar=arr.new_pivot_disarm_on_this_bar,
            st_flip_on_bar=arr.st_flip_on_this_bar,
            min_legs_global=10,
        )
        # Regardless of any armed+flip configuration, branch order (§1.6)
        # says NEW_PIVOT disarm reason wins.
        assert reason[10] == FILTER_REASON_ZZ_EXPIRED_NEW_PIVOT
        assert not allow[10]


# ===========================================================================
# 2. test_one_shot_reset_on_new_pivot  (§10.11 GATE, §1.4a step 5, §G.2.3)
# ===========================================================================


class TestOneShotResetOnNewPivot:
    """After a YES_SHOT, one_shot_fired_current_leg stays True until the NEXT
    confirm_bar, at which point plan §1.4a step 5 resets it to False for the
    new logical leg.  Failure to reset would silently block all future entries
    after the first successful shot."""

    def test_one_shot_reset_allows_new_armament(self):
        # Leg 0: UP, confirms at bar 5, armed SHORT.  Trend flips at bar 7
        # (+1 at bar 6, -1 at bar 7) → YES_SHOT.
        # Leg 1: UP, confirms at bar 15.  Must be able to arm again because
        # one_shot_fired should have been reset on bar 15 (new confirm).
        N = 40
        legs = [
            _mk_leg(0, LEG_DIR_UP, confirm_bar=5),
            _mk_leg(1, LEG_DIR_UP, confirm_bar=15, start_bar=5),
        ]
        snaps = [_mk_snap(50), _mk_snap(51)]
        regs = [_mk_reg(), _mk_reg()]
        trend = np.zeros(N, dtype=np.int8)
        trend[:7] = +1
        trend[7:] = -1

        leg_outs, arr = _run_armament_state_machine(
            legs=legs, regime_infos=regs, snapshots=snaps,
            st_trend=trend, high=np.zeros(N), low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=np.zeros(N, dtype=bool),
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        # Leg 0 must have fired YES_SHOT on bar 7.
        assert leg_outs[0].fired == FIRED_YES_SHOT, (
            f"precondition: leg 0 must YES_SHOT; got fired={leg_outs[0].fired}"
        )
        assert leg_outs[0].shot_bar == 7
        # Between bar 7 and bar 15: one_shot must be True (locks re-entry).
        assert np.all(arr.one_shot_fired_current_leg[8:15]), (
            "one_shot must remain True until next confirm"
        )
        # On bar 15 (confirm of leg 1): one_shot must be reset → leg 1 arms.
        assert not arr.one_shot_fired_current_leg[15], (
            "§1.4a step 5 violated: one_shot not reset on new confirm"
        )
        assert leg_outs[1].arm_bar == 15, (
            "leg 1 failed to arm after one_shot reset — plan §10.11 GATE violated"
        )
        assert arr.armed[15]

    def test_one_shot_locks_until_next_confirm(self):
        # Negative control for the first invariant: between YES_SHOT and the
        # next confirm, decision reason must be zz_locked_same_leg (not
        # zz_not_armed), confirming that one_shot remained True.
        N = 40
        legs = [
            _mk_leg(0, LEG_DIR_UP, confirm_bar=5),
            _mk_leg(1, LEG_DIR_UP, confirm_bar=20, start_bar=5),
        ]
        snaps = [_mk_snap(50), _mk_snap(51)]
        regs = [_mk_reg(), _mk_reg()]
        trend = np.zeros(N, dtype=np.int8)
        trend[:7] = +1
        trend[7:] = -1

        leg_outs, arr = _run_armament_state_machine(
            legs=legs, regime_infos=regs, snapshots=snaps,
            st_trend=trend, high=np.zeros(N), low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=np.zeros(N, dtype=bool),
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        assert leg_outs[0].fired == FIRED_YES_SHOT

        allow, reason = _compute_allow_entry_and_reason(
            n_bars=N,
            pathological=np.zeros(N, dtype=bool),
            n_legs_before=np.full(N, 50, dtype=np.int64),
            leg_direction=np.full(N, LEG_DIR_UP, dtype=np.int8),
            regime_state=np.full(N, REGIME_OPEN_GRACE, dtype=np.int8),
            armed=arr.armed,
            armed_side=arr.armed_side,
            one_shot=arr.one_shot_fired_current_leg,
            timeout_expired_on_bar=arr.timeout_expired_on_this_bar,
            new_pivot_disarm_on_bar=arr.new_pivot_disarm_on_this_bar,
            st_flip_on_bar=arr.st_flip_on_this_bar,
            min_legs_global=10,
        )
        # Bars 8..19 (exclusive 20 which is next confirm): armed=False AND
        # one_shot=True → reason zz_locked_same_leg.
        for d in range(8, 20):
            assert reason[d] == FILTER_REASON_ZZ_LOCKED_SAME_LEG, (
                f"bar {d}: expected zz_locked_same_leg, got {reason[d]!r}"
            )


# ===========================================================================
# 3. test_stflip_zero_to_one_not_counted  (§G.2.11)
# ===========================================================================


class TestStFlipZeroToOneNotCounted:
    """Plan §1.6: the transition trend[d-1]==0 → trend[d]==+1 is
    ATR-stabilisation, NOT a flip.  Armed LONG on this transition MUST NOT
    fire; reason must be zz_armed_waiting (still waiting for a real flip)."""

    def test_stflip_zero_to_plus_one_not_counted_unit(self):
        # Armed LONG leg at bar 5; trend: 0 up to bar 9, +1 from bar 10.
        N = 30
        legs = [_mk_leg(0, LEG_DIR_DOWN, confirm_bar=5)]   # DOWN leg → armed LONG
        snaps = [_mk_snap(50)]
        regs = [_mk_reg(REGIME_OPEN_ACTIVE, is_strong=True, n_since=5)]
        trend = np.zeros(N, dtype=np.int8)
        trend[10:] = +1    # 0 → +1 transition at bar 10

        leg_outs, arr = _run_armament_state_machine(
            legs=legs, regime_infos=regs, snapshots=snaps,
            st_trend=trend, high=np.zeros(N), low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=np.zeros(N, dtype=bool),
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        # Precondition: leg armed LONG on bar 5
        assert arr.armed[5]
        assert arr.armed_side[5] == ARMED_SIDE_LONG

        # GATE: on bar 10 (0 → +1) NO shot must fire.
        assert not arr.st_flip_on_this_bar[10], (
            "§G.2.11 violated: 0 → +1 transition counted as flip"
        )
        assert leg_outs[0].fired != FIRED_YES_SHOT
        # Still armed on bar 10 (not disarmed).
        assert arr.armed[10]

        # Decision: reason must be zz_armed_waiting (we are armed, no flip yet).
        allow, reason = _compute_allow_entry_and_reason(
            n_bars=N,
            pathological=np.zeros(N, dtype=bool),
            n_legs_before=np.full(N, 50, dtype=np.int64),
            leg_direction=np.full(N, LEG_DIR_DOWN, dtype=np.int8),
            regime_state=np.full(N, REGIME_OPEN_ACTIVE, dtype=np.int8),
            armed=arr.armed,
            armed_side=arr.armed_side,
            one_shot=arr.one_shot_fired_current_leg,
            timeout_expired_on_bar=arr.timeout_expired_on_this_bar,
            new_pivot_disarm_on_bar=arr.new_pivot_disarm_on_this_bar,
            st_flip_on_bar=arr.st_flip_on_this_bar,
            min_legs_global=10,
        )
        assert reason[10] == FILTER_REASON_ZZ_ARMED_WAITING
        assert not allow[10]

    def test_stflip_real_minus_one_to_plus_one_DOES_count(self):
        # Positive control: -1 → +1 on armed LONG DOES fire YES_SHOT.
        N = 30
        legs = [_mk_leg(0, LEG_DIR_DOWN, confirm_bar=5)]
        snaps = [_mk_snap(50)]
        regs = [_mk_reg(REGIME_OPEN_ACTIVE, is_strong=True, n_since=5)]
        trend = np.array([-1] * 10 + [+1] * (N - 10), dtype=np.int8)

        leg_outs, arr = _run_armament_state_machine(
            legs=legs, regime_infos=regs, snapshots=snaps,
            st_trend=trend, high=np.zeros(N), low=np.zeros(N),
            pathological=np.zeros(N, dtype=bool),
            session_reset_event=np.zeros(N, dtype=bool),
            min_legs_global=10,
            arm_timeout_bars_since_extreme=1000,
            arm_timeout_bars_hard=1000,
        )
        assert leg_outs[0].fired == FIRED_YES_SHOT
        assert leg_outs[0].shot_bar == 10
        assert arr.st_flip_on_this_bar[10]


# ===========================================================================
# 4. test_invariant_blocked_iff_reason_not_ok  (§G.1.2 hard, GATE)
# ===========================================================================


class TestInvariantBlockedIffReasonNotOk:
    """
    §G.1.2 (safety-net §1.8):
        blocked[d] == True  ⇔  reason[d] != "ok"
        blocked[d] == False ⇔  reason[d] == "ok"
    Both directions must hold for every bar on every input.
    Any violation exposes a masking bug in _compute_allow_entry_and_reason.
    """

    def _run_synthetic(self, N: int, seed: int,
                       with_nans: bool = False,
                       with_session_resets: bool = False) -> tuple:
        rng = np.random.default_rng(seed)
        close = 100.0 + np.cumsum(rng.normal(0, 1.0, N))
        high = close + rng.uniform(0.1, 1.5, N)
        low = close - rng.uniform(0.1, 1.5, N)
        open_p = close + rng.normal(0, 0.2, N)

        if with_nans:
            nan_positions = rng.choice(N, size=max(1, N // 50), replace=False)
            high[nan_positions] = float("nan")

        session_ids = np.zeros(N, dtype=np.int64)
        if with_session_resets:
            session_ids[N // 3:] = 1
            session_ids[2 * N // 3:] = 2

        # Trend with a mix of +1/-1/0 including warmup
        trend = rng.choice([-1, 0, 1], size=N).astype(np.int8)
        trend[:5] = 0  # simulate ATR warmup

        cfg = dict(
            reversal_threshold=0.005, min_legs_global=5, q_strong=0.80,
            k_local=5, entry_side="counter_trend",
            arm_timeout_bars_since_extreme=20, arm_timeout_bars_hard=50,
        )
        res = compute_zigzag_filter(
            high=high.astype(np.float64), low=low.astype(np.float64),
            close=close.astype(np.float64), open_prices=open_p.astype(np.float64),
            session_ids=session_ids, st_trend=trend, cfg=cfg,
        )
        return res

    def _check(self, res) -> None:
        allow = res.allow_entry
        reason = res.reason
        # Direction 1: blocked → reason != ok
        blocked_mask = ~allow
        if np.any(blocked_mask):
            bad = (reason[blocked_mask] == FILTER_REASON_OK)
            assert not np.any(bad), (
                f"§G.1.2 violated: {int(bad.sum())} bars have "
                f"allow_entry=False AND reason=='ok'"
            )
        # Direction 2: not blocked → reason == ok
        allowed_mask = allow
        if np.any(allowed_mask):
            bad = (reason[allowed_mask] != FILTER_REASON_OK)
            assert not np.any(bad), (
                f"§G.1.2 violated: {int(bad.sum())} bars have "
                f"allow_entry=True AND reason!='ok'"
            )

    @pytest.mark.parametrize("seed", [0, 1, 7, 42, 123, 2026])
    def test_invariant_on_random_inputs(self, seed):
        res = self._run_synthetic(N=500, seed=seed)
        self._check(res)

    def test_invariant_with_nan_bars(self):
        res = self._run_synthetic(N=500, seed=9, with_nans=True)
        # Ensure at least one pathological bar actually exists
        assert np.any(res.reason == FILTER_REASON_ZZ_PATHOLOGICAL), (
            "setup: expected some pathological bars"
        )
        self._check(res)

    def test_invariant_with_session_resets(self):
        res = self._run_synthetic(N=600, seed=17, with_session_resets=True)
        self._check(res)


# ===========================================================================
# 5. test_leg_at_t_not_in_stats_at_t_enters_at_t_plus_1  (§G.2.2 causality)
# ===========================================================================


class TestLegCausalityStats:
    """
    §G.2.2 (causality of expanding statistics):
        n_legs_before[c]   does NOT include a leg whose confirm_bar == c
        n_legs_before[c+1] does include that leg.

    Same for global_median, global_p80, local_median — computed from the
    set {l : l.confirm_bar < t}, strictly.
    """

    def test_single_leg_causality(self):
        # Build a price path with a single known confirmed leg; check
        # n_legs_before and global_median at bar c and c+1.
        # Simple up-leg: seed at 100, reach 110, reverse to 108 (confirms).
        bars_high = [100.2, 102.0, 105.0, 110.0, 109.5, 109.0]
        bars_low = [99.8, 99.9, 101.5, 104.0, 108.0, 107.5]
        # Extend to 20 bars of flat noise
        N = 20
        high = np.array(bars_high + [108.0] * (N - len(bars_high)), dtype=np.float64)
        low = np.array(bars_low + [107.5] * (N - len(bars_low)), dtype=np.float64)
        close = (high + low) / 2
        open_p = close.copy()
        session_ids = np.zeros(N, dtype=np.int64)
        trend = np.ones(N, dtype=np.int8)
        cfg = dict(
            reversal_threshold=0.01, min_legs_global=0, q_strong=0.80,
            k_local=5, entry_side="counter_trend",
            arm_timeout_bars_since_extreme=100, arm_timeout_bars_hard=200,
        )
        res = compute_zigzag_filter(high, low, close, open_p, session_ids, trend, cfg)
        assert len(res.legs) >= 1, "setup: expected at least one confirmed leg"
        leg = res.legs[0]
        c = leg.confirm_bar

        # GATE: on bar c the leg is NOT yet in the stats set.
        assert res.n_legs_before[c] == 0, (
            f"§G.2.2 violated: n_legs_before[{c}]={res.n_legs_before[c]}, "
            f"expected 0 (leg with confirm_bar==c must NOT be counted on c)"
        )
        assert math.isnan(res.global_median[c])
        assert math.isnan(res.global_p80[c])

        # On bar c+1 the leg IS counted.
        if c + 1 < N:
            assert res.n_legs_before[c + 1] == 1, (
                f"§G.2.2 violated: n_legs_before[{c+1}]={res.n_legs_before[c+1]}, "
                f"expected 1"
            )
            assert res.global_median[c + 1] == pytest.approx(leg.height_pct)
            assert res.global_p80[c + 1] == pytest.approx(leg.height_pct)

    def test_multileg_causality_on_each_confirm(self):
        # Build a longer price path with multiple legs; assert the invariant
        # at every confirm_bar.
        N = 60
        rng = np.random.default_rng(42)
        # Oscillating price to produce many legs
        t = np.arange(N)
        oscillation = 100.0 + 5.0 * np.sin(t * 0.6) + rng.normal(0, 0.5, N)
        high = oscillation + 1.0
        low = oscillation - 1.0
        close = oscillation
        open_p = close.copy()
        session_ids = np.zeros(N, dtype=np.int64)
        trend = np.ones(N, dtype=np.int8)
        cfg = dict(
            reversal_threshold=0.005, min_legs_global=0, q_strong=0.80,
            k_local=5, entry_side="counter_trend",
            arm_timeout_bars_since_extreme=100, arm_timeout_bars_hard=200,
        )
        res = compute_zigzag_filter(high, low, close, open_p, session_ids, trend, cfg)
        assert len(res.legs) >= 3, (
            f"setup: expected at least 3 legs, got {len(res.legs)}"
        )

        for i, leg in enumerate(res.legs):
            c = leg.confirm_bar
            # n_legs_before[c] must equal i (number of legs with confirm_bar < c)
            # where i is the position in the legs tuple because legs are
            # confirm-bar-ordered.
            expected = i
            assert res.n_legs_before[c] == expected, (
                f"leg {i} (confirm_bar={c}): n_legs_before[c]={res.n_legs_before[c]}, "
                f"expected {expected}. §G.2.2 violated."
            )
            # Next bar includes this leg.
            if c + 1 < N:
                # Account for possible further legs that may have confirm_bar == c+1.
                # The invariant is that THIS leg is counted strictly on c+1,
                # so n_legs_before[c+1] >= i+1.
                assert res.n_legs_before[c + 1] >= expected + 1, (
                    f"leg {i} (confirm_bar={c}): "
                    f"n_legs_before[c+1]={res.n_legs_before[c+1]}, "
                    f"expected >= {expected + 1}"
                )

    def test_snapshot_on_legrecord_matches_pre_add_state(self):
        # LegRecord.n_legs_before must equal the pre-add count, same as
        # broadcast n_legs_before[c].  Double-check consistency.
        N = 50
        rng = np.random.default_rng(7)
        oscillation = 100.0 + 3.0 * np.sin(np.arange(N) * 0.8) + rng.normal(0, 0.3, N)
        high = oscillation + 1.0
        low = oscillation - 1.0
        close = oscillation
        open_p = close.copy()
        session_ids = np.zeros(N, dtype=np.int64)
        trend = np.ones(N, dtype=np.int8)
        cfg = dict(
            reversal_threshold=0.005, min_legs_global=0, q_strong=0.80,
            k_local=5, entry_side="counter_trend",
            arm_timeout_bars_since_extreme=100, arm_timeout_bars_hard=200,
        )
        res = compute_zigzag_filter(high, low, close, open_p, session_ids, trend, cfg)
        for i, leg in enumerate(res.legs):
            assert leg.n_legs_before == i, (
                f"leg {i}: LegRecord.n_legs_before={leg.n_legs_before}, "
                f"must equal i={i} (pre-add count)"
            )
            assert leg.n_legs_before == res.n_legs_before[leg.confirm_bar]


# ===========================================================================
# 6. test_unknown_leg_direction_reason_is_warmup  (FIX 3 — §1.1.3)
# ===========================================================================


class TestUnknownLegDirectionWarmup:
    """
    §1.1.3: когда leg_direction == LEG_DIR_UNKNOWN (в начале серии или после
    session_reset с достаточным n_legs_before), reason должен быть zz_warmup,
    а не zz_not_armed / другой.
    """

    def test_unknown_direction_reason_is_warmup_direct(self):
        """Прямой вызов _compute_allow_entry_and_reason с UNKNOWN направлением."""
        from supertrend_optimizer.core.zigzag_filter import (
            _compute_allow_entry_and_reason,
            LEG_DIR_UNKNOWN,
            LEG_DIR_UP,
        )
        N = 10
        # n_legs_before достаточно (> min_legs_global), но leg_direction == UNKNOWN
        leg_dir = np.full(N, LEG_DIR_UP, dtype=np.int8)
        leg_dir[:3] = LEG_DIR_UNKNOWN  # первые 3 бара — UNKNOWN

        allow, reason = _compute_allow_entry_and_reason(
            n_bars=N,
            pathological=np.zeros(N, dtype=bool),
            n_legs_before=np.full(N, 100, dtype=np.int64),
            leg_direction=leg_dir,
            regime_state=np.full(N, REGIME_OPEN_ACTIVE, dtype=np.int8),
            armed=np.ones(N, dtype=bool),
            armed_side=np.full(N, ARMED_SIDE_LONG, dtype=np.int8),
            one_shot=np.zeros(N, dtype=bool),
            timeout_expired_on_bar=np.zeros(N, dtype=bool),
            new_pivot_disarm_on_bar=np.zeros(N, dtype=bool),
            st_flip_on_bar=np.ones(N, dtype=bool),
            min_legs_global=5,
        )
        # UNKNOWN барам → warmup
        for d in range(3):
            assert reason[d] == FILTER_REASON_ZZ_WARMUP, (
                f"bar {d}: leg_direction=UNKNOWN, n_legs_before=100 → "
                f"expected zz_warmup (§1.1.3), got {reason[d]!r}"
            )
            assert not allow[d]
        # Остальные → ok (все ворота пройдены)
        for d in range(3, N):
            assert reason[d] == FILTER_REASON_OK, (
                f"bar {d}: expected ok, got {reason[d]!r}"
            )

    def test_unknown_direction_initial_bars_via_public_api(self):
        """
        Через публичный API: первые бары до первой смены направления
        имеют leg_direction == UNKNOWN → reason == zz_warmup (§1.1.3).
        """
        N = 100
        # Монотонный рост → первые несколько баров leg_direction=UNKNOWN
        # (до тех пор, пока не определится cur_leg_dir)
        high = np.linspace(100.0, 200.0, N)
        low = high - 0.5
        close = high - 0.25
        open_p = close.copy()
        session_ids = np.zeros(N, dtype=np.int64)
        trend = np.ones(N, dtype=np.int8)
        cfg = dict(
            reversal_threshold=0.005,
            min_legs_global=0,  # убираем warmup по n_legs
            q_strong=0.80, k_local=5, entry_side="counter_trend",
            arm_timeout_bars_since_extreme=1000, arm_timeout_bars_hard=1000,
        )
        res = compute_zigzag_filter(high, low, close, open_p, session_ids, trend, cfg)
        # Первый бар — всегда UNKNOWN (нет данных о направлении на bar 0)
        assert res.leg_direction[0] == LEG_DIR_UNKNOWN, (
            f"bar 0 должен быть UNKNOWN, got {res.leg_direction[0]}"
        )
        # reason[0] должен быть zz_warmup (§1.1.3)
        assert res.reason[0] == FILTER_REASON_ZZ_WARMUP, (
            f"bar 0: leg_direction=UNKNOWN → ожидали zz_warmup, got {res.reason[0]!r}"
        )
        # Все бары с UNKNOWN → warmup
        unknown_mask = (res.leg_direction == LEG_DIR_UNKNOWN)
        if np.any(unknown_mask):
            bad = (res.reason[unknown_mask] != FILTER_REASON_ZZ_WARMUP)
            assert not np.any(bad), (
                f"Барыʻ с UNKNOWN leg_direction имеют не-warmup reason: "
                f"{set(res.reason[unknown_mask][bad].tolist())}"
            )


# ===========================================================================
# 7. test_at_most_one_yes_shot_per_shot_bar  (FIX 10)
# ===========================================================================


class TestAtMostOneYesShotPerShotBar:
    """
    Инвариант linkage trade↔leg (FIX 10):
    Каждый shot_bar может встречаться не более одного раза среди ног с
    fired=FIRED_YES_SHOT.  Дубликаты shot_bar означали бы двусмысленную
    привязку трейда к ноге.
    """

    def _run_zz(self, N: int, seed: int, reversal_threshold: float = 0.005):
        rng = np.random.default_rng(seed)
        close = 100.0 + np.cumsum(rng.normal(0, 1.5, N))
        high = close + rng.uniform(0.1, 2.0, N)
        low = close - rng.uniform(0.1, 2.0, N)
        open_p = close + rng.normal(0, 0.2, N)
        session_ids = np.zeros(N, dtype=np.int64)
        trend = rng.choice([-1, 1], size=N).astype(np.int8)
        trend[:10] = 0
        cfg = dict(
            reversal_threshold=reversal_threshold,
            min_legs_global=5,
            q_strong=0.80,
            k_local=5,
            entry_side="counter_trend",
            arm_timeout_bars_since_extreme=30,
            arm_timeout_bars_hard=100,
        )
        return compute_zigzag_filter(high, low, close, open_p, session_ids, trend, cfg)

    @pytest.mark.parametrize("seed", [0, 1, 7, 42, 123, 2026])
    def test_at_most_one_yes_shot_per_shot_bar(self, seed):
        """Не должно быть дублирующихся shot_bar среди FIRED_YES_SHOT ног."""
        res = self._run_zz(N=800, seed=seed)
        shot_bars = [lg.shot_bar for lg in res.legs if lg.fired == FIRED_YES_SHOT]
        duplicates = [b for b in set(shot_bars) if shot_bars.count(b) > 1]
        assert len(duplicates) == 0, (
            f"seed={seed}: дублирующиеся shot_bar найдены: {duplicates}. "
            f"Нарушение инварианта ≤1 YES_SHOT на shot_bar."
        )

    def test_at_most_one_yes_shot_with_session_resets(self):
        """Проверка с session_resets: ноги не должны дублировать shot_bar."""
        N = 500
        rng = np.random.default_rng(99)
        close = 100.0 + np.cumsum(rng.normal(0, 1.5, N))
        high = close + rng.uniform(0.1, 2.0, N)
        low = close - rng.uniform(0.1, 2.0, N)
        open_p = close.copy()
        session_ids = np.zeros(N, dtype=np.int64)
        session_ids[N // 3:] = 1
        session_ids[2 * N // 3:] = 2
        trend = rng.choice([-1, 1], size=N).astype(np.int8)
        trend[:10] = 0

        cfg = dict(
            reversal_threshold=0.005, min_legs_global=3, q_strong=0.80,
            k_local=5, entry_side="counter_trend",
            arm_timeout_bars_since_extreme=30, arm_timeout_bars_hard=100,
        )
        res = compute_zigzag_filter(high, low, close, open_p, session_ids, trend, cfg)
        shot_bars = [lg.shot_bar for lg in res.legs if lg.fired == FIRED_YES_SHOT]
        duplicates = [b for b in set(shot_bars) if shot_bars.count(b) > 1]
        assert len(duplicates) == 0, (
            f"Дублирующиеся shot_bar при session_resets: {duplicates}"
        )
