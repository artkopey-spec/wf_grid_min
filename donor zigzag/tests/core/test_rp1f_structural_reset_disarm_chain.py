"""
RP-1 Block 1-F — disarm-chain from structural_reset_event.

Verifies the defensive contract in `_unified_armament_fsm` step 2
(RFC v3.1 §4.2 step 2 / §4.5 / §5.5 / §8.3.9):

    If `structural_reset_event[t] == True`:
      1. an active armed-session is disarmed with
         `disarm_event[t] == FIRED_SESSION_RESET`;
      2. `cur_one_shot` is cleared (§5.5(c));
      3. `armed[t+1] == False` unless a NEW armament hits the bar;
      4. a new armament is possible after the reset once
         readiness / cand_side / cand_id conditions are met;
      5. if a B-deactivation is simultaneously possible on bar t,
         structural_reset wins: `disarm_event[t] == FIRED_SESSION_RESET`,
         NOT `FIRED_NO_REGIME_OFF`;
      6. a pre-confirm armed-session disarmed by structural_reset does
         NOT create a synthetic LegRecord;
      7. a post-confirm owning-session disarmed by structural_reset
         records `FIRED_SESSION_RESET` on the owning LegRecord.

These tests call `_unified_armament_fsm` directly with hand-crafted
`_ZigZagPassResult` fixtures.  This is necessary because RP-1E tightened
the `_confirmed_zigzag_pass` emit precondition so that, under normal
pipeline flow, `structural_reset_event[t]` cannot fire while a session is
armed (cur_leg_dir would have to be UNKNOWN on entry, which is
incompatible with having armed).  Step 2 therefore remains a
defensive-only branch; these tests pin the invariant so any future
refactor that re-enables the scenario keeps the disarm chain intact.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    ARMED_SIDE_LONG,
    ARMED_SIDE_NONE,
    ARMED_SIDE_SHORT,
    ARM_SRC_B,
    ARM_SRC_BOTH,
    ARM_SRC_NONE,
    DISARM_EVT_NONE,
    FIRED_NONE,
    FIRED_NO_REGIME_OFF,
    FIRED_SESSION_RESET,
    LEG_DIR_DOWN,
    LEG_DIR_UNKNOWN,
    LEG_DIR_UP,
    _PartialLeg,
    _ZigZagPassResult,
    _unified_armament_fsm,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_pass_result(
    n: int,
    *,
    leg_direction: np.ndarray,
    cand_leg_id: np.ndarray,
    struct_reset_at: List[int] = (),
    session_reset_at: List[int] = (),
    pathological_at: List[int] = (),
    confirm_at: List[int] = (),
    legs: List[_PartialLeg] = None,
) -> _ZigZagPassResult:
    """Construct a minimal `_ZigZagPassResult` for FSM unit tests."""
    confirm = np.zeros(n, dtype=bool)
    for t in confirm_at:
        confirm[t] = True
    session = np.zeros(n, dtype=bool)
    for t in session_reset_at:
        session[t] = True
    struct = np.zeros(n, dtype=bool)
    for t in struct_reset_at:
        struct[t] = True
    path = np.zeros(n, dtype=bool)
    for t in pathological_at:
        path[t] = True
    return _ZigZagPassResult(
        legs=list(legs) if legs is not None else [],
        leg_direction=leg_direction.astype(np.int8),
        cand_height_pct=np.full(n, np.nan, dtype=np.float64),
        last_pivot_price=np.full(n, np.nan, dtype=np.float64),
        last_pivot_bar_idx=np.full(n, -1, dtype=np.int64),
        pathological=path,
        confirm_event=confirm,
        session_reset_event=session,
        zz_cand_leg_id=cand_leg_id.astype(np.int64),
        structural_reset_event=struct,
    )


def _run_fsm(
    n: int,
    *,
    pass_result: _ZigZagPassResult,
    ready_a: np.ndarray = None,
    ready_b: np.ndarray = None,
    enabled_a: bool = False,
    enabled_b: bool = True,
    st_trend: np.ndarray = None,
    high: np.ndarray = None,
    low: np.ndarray = None,
) -> Tuple:
    """Invoke `_unified_armament_fsm` with sensible defaults."""
    if ready_a is None:
        ready_a = np.zeros(n, dtype=bool)
    if ready_b is None:
        ready_b = np.ones(n, dtype=bool)
    if st_trend is None:
        # No ST flips by default (all +1 → never flips toward SHORT).
        st_trend = np.full(n, +1, dtype=np.int8)
    if high is None:
        high = np.full(n, 100.0, dtype=np.float64)
    if low is None:
        low = np.full(n, 99.0, dtype=np.float64)
    global_p80 = np.full(n, 1.0, dtype=np.float64)
    n_legs_before = np.zeros(n, dtype=np.int64)
    return _unified_armament_fsm(
        legs=pass_result.legs,
        pass_result=pass_result,
        global_p80=global_p80,
        n_legs_before=n_legs_before,
        ready_a=ready_a,
        ready_b=ready_b,
        enabled_a=enabled_a,
        enabled_b=enabled_b,
        st_trend=st_trend,
        high=high,
        low=low,
        min_legs_global=0,
        arm_timeout_bars_since_extreme=10_000,
        arm_timeout_bars_hard=10_000,
    )


# ---------------------------------------------------------------------------
# LX-28 — full disarm chain from structural_reset
# ---------------------------------------------------------------------------


class TestLX28FullDisarmChainFromStructuralReset:
    """
    LX-28 — requirements (1)..(4) on a single fixture:
      - active armed session + structural_reset[t] →
        disarm_event[t] == FIRED_SESSION_RESET;
      - `one_shot[t] == False` after the same-bar override in step 2
        (§5.5(c));
      - `armed[t] == False` (disarm happened on the same bar);
      - `armed[t+1] == False` unless re-armament actually hit;
      - new armament is possible on t+k once
        readiness / cand_side / cand_id permit.
    """

    def test_disarm_chain_and_rearm_after_reset(self):
        n = 10
        # Candidate id 0 covers bars 2..9 (one leg direction throughout).
        cand = np.full(n, -1, dtype=np.int64)
        cand[2:] = 0
        ldir = np.full(n, LEG_DIR_UNKNOWN, dtype=np.int8)
        ldir[2:] = LEG_DIR_UP  # cand_side == SHORT
        pr = _make_pass_result(
            n,
            leg_direction=ldir,
            cand_leg_id=cand,
            struct_reset_at=[5],
        )
        ready_b = np.zeros(n, dtype=bool)
        ready_b[2:] = True  # readiness on from bar 2

        (
            leg_outs,
            arr,
            arm_src_rt,
            arm_src_dec,
            regime_off_bar,
            disarm_event,
        ) = _run_fsm(n, pass_result=pr, ready_b=ready_b)

        # Session armed at bar 2 (pre-confirm, no legs in fixture).
        assert arr.armed[2] and not arr.armed[1]
        # Armament persists 2..4.
        assert all(arr.armed[t] for t in range(2, 5))
        # (1) FIRED_SESSION_RESET on the struct-reset bar.
        assert int(disarm_event[5]) == int(FIRED_SESSION_RESET)
        # (3) armed[5] cleared on the same bar; stays False on 6 unless
        #     re-armed — in this fixture readiness is still on AND
        #     leg_direction is still UP, so FSM re-arms on bar 6 (which
        #     also proves requirement (4)).
        assert not arr.armed[5], "disarm must clear armed[t]"
        # (2) one_shot cleared at end of bar 5 (step 2 override wins
        #     over `_disarm_session`'s `cur_one_shot = True`).
        assert not arr.one_shot_fired_current_leg[5], (
            "§5.5(c): one_shot must be False on structural_reset bar — "
            "otherwise re-armament would be blocked."
        )
        # (4) re-armament on bar 6.
        assert arr.armed[6], (
            "re-armament after structural_reset must succeed when "
            "readiness/cand_side/cand_id allow it."
        )
        # Sanity: no disarm events on any bar other than 5.
        assert int(disarm_event[4]) == int(DISARM_EVT_NONE)
        assert int(disarm_event[6]) == int(DISARM_EVT_NONE)

    def test_no_rearm_when_readiness_drops(self):
        """
        (3) variant: if readiness is OFF on t+1, no re-arm → armed[t+1]
        stays False.  Isolates the armed→not-armed transition from
        re-arm.
        """
        n = 8
        cand = np.full(n, -1, dtype=np.int64)
        cand[2:] = 0
        ldir = np.full(n, LEG_DIR_UNKNOWN, dtype=np.int8)
        ldir[2:] = LEG_DIR_UP
        pr = _make_pass_result(
            n,
            leg_direction=ldir,
            cand_leg_id=cand,
            struct_reset_at=[5],
        )
        ready_b = np.zeros(n, dtype=bool)
        ready_b[2:5] = True  # drops at the struct-reset bar onwards

        (_, arr, _, _, _, disarm_event) = _run_fsm(
            n, pass_result=pr, ready_b=ready_b
        )
        assert arr.armed[2] and arr.armed[4]
        assert int(disarm_event[5]) == int(FIRED_SESSION_RESET)
        assert not arr.armed[5] and not arr.armed[6] and not arr.armed[7]


# ---------------------------------------------------------------------------
# LX-02 — structural_reset wins over FIRED_NO_REGIME_OFF
# ---------------------------------------------------------------------------


class TestLX02StructuralResetWinsOverNoRegimeOff:
    """
    LX-02 — requirement (5): on a bar where both a structural reset and
    a B-deactivation would fire, structural_reset must win — the
    `disarm_event[t]` must equal `FIRED_SESSION_RESET`, never
    `FIRED_NO_REGIME_OFF`.  `regime_off_disarm_on_bar[t]` must be False
    (the `cur_armed` gate in step 7 sees `False` after step 2 already
    disarmed).
    """

    def test_struct_reset_beats_b_deactivation_same_bar(self):
        n = 8
        cand = np.full(n, -1, dtype=np.int64)
        cand[1:] = 0
        ldir = np.full(n, LEG_DIR_UNKNOWN, dtype=np.int8)
        ldir[1:] = LEG_DIR_UP
        pr = _make_pass_result(
            n,
            leg_direction=ldir,
            cand_leg_id=cand,
            struct_reset_at=[3],
        )
        ready_b = np.zeros(n, dtype=bool)
        # Ready_B on at arm time, drops OFF on the struct-reset bar (3).
        ready_b[1:3] = True
        ready_b[3:] = False

        (_, arr, _, _, regime_off_bar, disarm_event) = _run_fsm(
            n, pass_result=pr, ready_b=ready_b
        )
        # Session armed (pre-confirm, ARM_SRC_B) at bar 1.
        assert arr.armed[1] and arr.armed[2]
        # Both triggers would apply on bar 3; struct_reset must win.
        assert int(disarm_event[3]) == int(FIRED_SESSION_RESET), (
            f"struct_reset must win over B-deactivation; got "
            f"disarm_event[3]={int(disarm_event[3])}, expected "
            f"{int(FIRED_SESSION_RESET)}"
        )
        assert int(disarm_event[3]) != int(FIRED_NO_REGIME_OFF)
        assert not bool(regime_off_bar[3]), (
            "regime_off_disarm_on_bar[t] must be False — step 7 sees "
            "cur_armed=False after step 2 already disarmed."
        )


# ---------------------------------------------------------------------------
# RP-1F req (6) — pre-confirm session + struct_reset → no synthetic leg
# ---------------------------------------------------------------------------


class TestRP1FPreConfirmStructuralReset:
    """
    Requirement (6): a pre-confirm armed-session disarmed by
    structural_reset must NOT produce a synthetic LegRecord.  The FSM
    pre-confirm ledger may still carry the orphan cand_id, but the flush
    at the end of the FSM loop iterates `legs` — orphan ledger entries
    have no matching leg and are silently discarded.

    Observable contract: `leg_outs` has the same length as the input
    legs list and no spurious entries appear.
    """

    def test_no_synthetic_leg_when_orphan_cand_disarmed_by_struct_reset(
        self,
    ):
        n = 8
        cand = np.full(n, -1, dtype=np.int64)
        cand[2:] = 0  # orphan candidate 0 — never confirms in this fixture
        ldir = np.full(n, LEG_DIR_UNKNOWN, dtype=np.int8)
        ldir[2:] = LEG_DIR_UP
        # Explicitly no legs → ledger entry for cand 0 cannot find an
        # owning leg at flush time.
        pr = _make_pass_result(
            n,
            leg_direction=ldir,
            cand_leg_id=cand,
            struct_reset_at=[5],
            legs=[],
        )
        ready_b = np.ones(n, dtype=bool)

        (leg_outs, arr, _, _, _, disarm_event) = _run_fsm(
            n, pass_result=pr, ready_b=ready_b
        )
        # Pre-confirm arm existed.
        assert arr.armed[2]
        # Structural reset disarm fired.
        assert int(disarm_event[5]) == int(FIRED_SESSION_RESET)
        # NO synthetic legs.
        assert len(leg_outs) == 0, (
            "Pre-confirm session disarmed by structural_reset must NOT "
            f"create a synthetic LegRecord; got {len(leg_outs)} legs."
        )


# ---------------------------------------------------------------------------
# RP-1F req (7) — post-confirm owning session + struct_reset
# ---------------------------------------------------------------------------


class TestRP1FPostConfirmStructuralReset:
    """
    Requirement (7): a post-confirm owning session disarmed by
    structural_reset must write `FIRED_SESSION_RESET` with
    `shot_bar == -1` onto the owning LegRecord (§7.5 — owning-leg
    disarm path in `_disarm_session`).

    The owning session is established via the §4.7/D4 transition at the
    confirm bar of the owning leg: pre-confirm session whose
    cand_leg_id_at_confirm matches the confirmed leg flips to
    post-confirm ownership on the confirm bar.
    """

    def test_owning_leg_records_fired_session_reset(self):
        # Layout:
        #   bar 0: UNKNOWN (init)
        #   bar 1: cand 0 starts (leg_dir=UP).  Arm at bar 1 (pre-confirm).
        #   bar 3: confirm_event for leg 0 (cand_leg_id_at_confirm=0).
        #          §4.7 D4 transition → session becomes post-confirm
        #          owning leg 0.
        #   bar 5: structural_reset → disarm.  Must write
        #          FIRED_SESSION_RESET on leg 0.
        n = 8
        cand = np.full(n, -1, dtype=np.int64)
        cand[1:] = 0
        ldir = np.full(n, LEG_DIR_UNKNOWN, dtype=np.int8)
        ldir[1:] = LEG_DIR_UP
        legs = [
            _PartialLeg(
                leg_id=0,
                start_bar=0,
                end_bar=2,
                confirm_bar=3,
                start_price=100.0,
                end_price=110.0,
                direction=LEG_DIR_UP,
                height_pct=0.10,
                length_bars=2,
                confirm_lag_bars=1,
                cand_leg_id_at_confirm=0,
            ),
        ]
        pr = _make_pass_result(
            n,
            leg_direction=ldir,
            cand_leg_id=cand,
            struct_reset_at=[5],
            confirm_at=[3],
            legs=legs,
        )
        ready_b = np.ones(n, dtype=bool)

        (leg_outs, arr, _, _, _, disarm_event) = _run_fsm(
            n, pass_result=pr, ready_b=ready_b
        )
        # Session armed pre-confirm at bar 1 and rides through confirm.
        assert arr.armed[1] and arr.armed[2] and arr.armed[3]
        # Still armed on bar 4 (post-confirm).
        assert arr.armed[4]
        # Structural reset on bar 5 fires.
        assert int(disarm_event[5]) == int(FIRED_SESSION_RESET)
        # Owning leg captures FIRED_SESSION_RESET with shot_bar=-1.
        assert len(leg_outs) == 1
        assert int(leg_outs[0].fired) == int(FIRED_SESSION_RESET), (
            f"owning leg must record FIRED_SESSION_RESET on post-confirm "
            f"structural_reset disarm; got fired={int(leg_outs[0].fired)}"
        )
        assert int(leg_outs[0].shot_bar) == -1, (
            f"structural_reset disarm carries shot_bar=-1 (no shot "
            f"happened); got shot_bar={int(leg_outs[0].shot_bar)}"
        )
        # armed_by_candidate should be True (pre-confirm arm happened
        # before the leg's own confirm_bar; the §7.5 flush wrote the
        # ledger entry onto the leg).
        assert bool(leg_outs[0].armed_by_candidate)

    def test_pre_confirm_session_does_not_write_owning_leg_on_struct_reset(
        self,
    ):
        """
        Dual to the test above: if structural_reset disarms BEFORE the
        owning leg's confirm_bar (i.e. session is still pre-confirm),
        the leg's `fired` field must stay `FIRED_NONE`.  The §7.5 flush
        only surfaces `pre_confirm_arm_bar` / `pre_confirm_shot_bar` /
        `armed_by_candidate`, never `fired` / `shot_bar` (which belong
        to the post-confirm cycle).
        """
        n = 10
        cand = np.full(n, -1, dtype=np.int64)
        cand[1:] = 0
        ldir = np.full(n, LEG_DIR_UNKNOWN, dtype=np.int8)
        ldir[1:] = LEG_DIR_UP
        legs = [
            _PartialLeg(
                leg_id=0,
                start_bar=0,
                end_bar=7,
                confirm_bar=8,  # confirm AFTER struct_reset
                start_price=100.0,
                end_price=110.0,
                direction=LEG_DIR_UP,
                height_pct=0.10,
                length_bars=7,
                confirm_lag_bars=1,
                cand_leg_id_at_confirm=0,
            ),
        ]
        pr = _make_pass_result(
            n,
            leg_direction=ldir,
            cand_leg_id=cand,
            struct_reset_at=[4],
            confirm_at=[8],
            legs=legs,
        )
        ready_b = np.ones(n, dtype=bool)

        (leg_outs, _, _, _, _, disarm_event) = _run_fsm(
            n, pass_result=pr, ready_b=ready_b
        )
        assert int(disarm_event[4]) == int(FIRED_SESSION_RESET)
        # Leg 0 is still pre-confirm at bar 4 — no `fired` write.
        assert int(leg_outs[0].fired) == int(FIRED_NONE), (
            "pre-confirm session disarmed by structural_reset must NOT "
            f"write `fired` on the (future) owning leg; got "
            f"fired={int(leg_outs[0].fired)}"
        )
        assert int(leg_outs[0].shot_bar) == -1
        # armed_by_candidate comes from the flush: the ledger still
        # carries the pre-confirm arm_bar for cand 0.
        assert bool(leg_outs[0].armed_by_candidate)
        assert int(leg_outs[0].pre_confirm_arm_bar) == 1


# ---------------------------------------------------------------------------
# Defensive property: struct_reset takes priority in the step-ordering
# over NO_NEW_PIVOT as well (cand_id change + struct_reset on same bar).
# ---------------------------------------------------------------------------


class TestRP1FStructResetPriorityAgainstOtherDisarms:
    """
    Secondary property: structural_reset is in step 2 of RFC §4.2 and
    runs BEFORE step 4 (confirm-bar / NO_NEW_PIVOT) and step 7
    (B-deactivation).  On any bar where both a structural reset and a
    same-bar `cand_leg_id` change (typically NO_NEW_PIVOT) would
    normally fire, structural_reset must win.
    """

    def test_struct_reset_beats_no_new_pivot_same_bar(self):
        # Arm on cand 0 (bar 1..), then on bar 3 cand flips to 1
        # (simulating an owning leg that is NOT ours confirming),
        # AND structural_reset[3] = True.  Step 2 must fire first.
        n = 8
        cand = np.full(n, -1, dtype=np.int64)
        cand[1:3] = 0
        cand[3:] = 1
        ldir = np.full(n, LEG_DIR_UNKNOWN, dtype=np.int8)
        ldir[1:] = LEG_DIR_UP
        pr = _make_pass_result(
            n,
            leg_direction=ldir,
            cand_leg_id=cand,
            struct_reset_at=[3],
        )
        ready_b = np.ones(n, dtype=bool)

        (_, arr, _, _, _, disarm_event) = _run_fsm(
            n, pass_result=pr, ready_b=ready_b
        )
        assert arr.armed[1] and arr.armed[2]
        assert int(disarm_event[3]) == int(FIRED_SESSION_RESET)
        # And cur_armed is False going into step 4, so no
        # new_pivot_disarm_on_bar flag should be set.
        assert not bool(arr.new_pivot_disarm_on_this_bar[3])
