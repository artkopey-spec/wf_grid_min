"""
Phase 3 smoke tests for RFC v3.1 ZigZag Readiness Logic.

Scope: minimal verification that the Phase 3 plumbing is wired and produces
values with correct shape / dtype / semantics.  DOES NOT cover TP-01..TP-45
test plan in depth — those are expected to be added in subsequent test-plan
implementation tasks (§9 §11.7).

Only smoke-level invariants tested here:
  1. New enum constants exist and have expected integer values.
  2. ZigZagFilterResult carries the new per-bar arrays with len == N.
  3. _compute_ready_a_array is vectorised and respects the warmup gate (fix D1).
  4. _run_contour_b_fsm latches (does not reset on session_reset) — plumbing only.
  5. structural_reset_event[t] fires on a long pathological-span recovery.
  6. readiness_on[t] == (enabled_A and ready_A[t]) | (enabled_B and ready_B[t]).
  7. Legacy call-site of _compute_allow_entry_and_reason(regime_state=...) still works.

Non-goals (deferred to §9 test plan):
  - TP-01..TP-11 full scenarios.
  - TP-38 legacy-parity snapshot.
  - TP-40/TP-41 property tests.
"""

from __future__ import annotations

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    ARM_SRC_A,
    ARM_SRC_B,
    ARM_SRC_BOTH,
    ARM_SRC_NONE,
    DISARM_EVT_NONE,
    FIRED_NO_REGIME_OFF,
    LEG_DIR_DOWN,
    LEG_DIR_UNKNOWN,
    LEG_DIR_UP,
    READY_B_OFF,
    READY_B_ON,
    ZigZagFilterResult,
    _compute_allow_entry_and_reason,
    _compute_ready_a_array,
    _confirmed_zigzag_pass,
    _run_contour_b_fsm,
    compute_zigzag_filter,
)
from supertrend_optimizer.utils.constants import (
    FILTER_REASON_OK,
    FILTER_REASON_WHITELIST,
    FILTER_REASON_ZZ_REGIME_OFF,
    FILTER_REASON_ZZ_WARMUP,
)


# ---------------------------------------------------------------------------
# 1. Enum constants
# ---------------------------------------------------------------------------


class TestEnumConstants:
    def test_fired_no_regime_off_added(self):
        assert FIRED_NO_REGIME_OFF == 6  # RFC §4.5, §7.1

    def test_arm_source_values(self):
        assert ARM_SRC_NONE == 0
        assert ARM_SRC_A == 1
        assert ARM_SRC_B == 2
        assert ARM_SRC_BOTH == 3

    def test_ready_b_values(self):
        assert READY_B_OFF == 0
        assert READY_B_ON == 1

    def test_disarm_event_base_is_fired_none(self):
        assert DISARM_EVT_NONE == 0


# ---------------------------------------------------------------------------
# 2. FILTER_REASON_WHITELIST hard invariant (G-03)
# ---------------------------------------------------------------------------


class TestWhitelistUnchanged:
    def test_no_new_readiness_reasons_in_whitelist(self):
        # RFC §7.2, G-03: readiness-block detail strings are DIAGNOSTIC ONLY
        # and MUST NOT leak into filtered_reason whitelist.
        for forbidden in ("not_ready_A", "not_ready_B", "not_ready_both",
                          "disarm_b_regime_off", "zz_not_ready_a", "zz_not_ready_b"):
            assert forbidden not in FILTER_REASON_WHITELIST


# ---------------------------------------------------------------------------
# 3. _compute_ready_a_array semantics (fix D1)
# ---------------------------------------------------------------------------


class TestComputeReadyA:
    def test_warmup_in_formula_not_just_branch(self):
        # Fix D1: even if cand_height_pct >= global_p80 is True, ready_A MUST
        # be False when n_legs_before < min_legs_global (warmup part of formula).
        N = 5
        leg_direction = np.full(N, LEG_DIR_UP, dtype=np.int8)
        cand_height_pct = np.full(N, 0.10, dtype=np.float64)
        global_p80 = np.full(N, 0.05, dtype=np.float64)  # cand > p80 everywhere
        n_legs_before = np.array([0, 10, 30, 49, 50], dtype=np.int64)
        pathological = np.zeros(N, dtype=bool)

        ready_a = _compute_ready_a_array(
            leg_direction=leg_direction,
            cand_height_pct=cand_height_pct,
            global_p80=global_p80,
            n_legs_before=n_legs_before,
            pathological=pathological,
            min_legs_global=50,
        )
        # Only the last bar (n=50) satisfies warmup.
        assert not ready_a[0]
        assert not ready_a[1]
        assert not ready_a[2]
        assert not ready_a[3]
        assert ready_a[4]

    def test_pathological_forces_false(self):
        N = 3
        leg_direction = np.full(N, LEG_DIR_UP, dtype=np.int8)
        cand_height_pct = np.full(N, 0.10, dtype=np.float64)
        global_p80 = np.full(N, 0.05, dtype=np.float64)
        n_legs_before = np.full(N, 100, dtype=np.int64)
        pathological = np.array([False, True, False])

        ready_a = _compute_ready_a_array(
            leg_direction=leg_direction,
            cand_height_pct=cand_height_pct,
            global_p80=global_p80,
            n_legs_before=n_legs_before,
            pathological=pathological,
            min_legs_global=50,
        )
        assert ready_a[0]
        assert not ready_a[1]  # pathological gate wins
        assert ready_a[2]

    def test_unknown_leg_direction_forces_false(self):
        N = 2
        leg_direction = np.array([LEG_DIR_UNKNOWN, LEG_DIR_DOWN], dtype=np.int8)
        cand_height_pct = np.full(N, 0.10, dtype=np.float64)
        global_p80 = np.full(N, 0.05, dtype=np.float64)
        n_legs_before = np.full(N, 100, dtype=np.int64)
        pathological = np.zeros(N, dtype=bool)

        ready_a = _compute_ready_a_array(
            leg_direction=leg_direction,
            cand_height_pct=cand_height_pct,
            global_p80=global_p80,
            n_legs_before=n_legs_before,
            pathological=pathological,
            min_legs_global=50,
        )
        assert not ready_a[0]  # UNKNOWN direction
        assert ready_a[1]

    def test_nan_p80_forces_false(self):
        N = 2
        leg_direction = np.full(N, LEG_DIR_UP, dtype=np.int8)
        cand_height_pct = np.full(N, 0.10, dtype=np.float64)
        global_p80 = np.array([np.nan, 0.05], dtype=np.float64)
        n_legs_before = np.full(N, 100, dtype=np.int64)
        pathological = np.zeros(N, dtype=bool)

        ready_a = _compute_ready_a_array(
            leg_direction=leg_direction,
            cand_height_pct=cand_height_pct,
            global_p80=global_p80,
            n_legs_before=n_legs_before,
            pathological=pathological,
            min_legs_global=50,
        )
        assert not ready_a[0]
        assert ready_a[1]


# ---------------------------------------------------------------------------
# 4. _run_contour_b_fsm: empty legs → all-False plumbing
# ---------------------------------------------------------------------------


class TestContourBFsmPlumbing:
    def test_empty_legs_returns_all_false(self):
        ready_b = _run_contour_b_fsm(
            legs=[], snapshots=[],
            confirm_heights_global_median=[],
            n_bars=20, open_ratio=1.5, close_ratio=1.0, local_k=5,
        )
        assert ready_b.shape == (20,)
        assert ready_b.dtype == bool
        assert not ready_b.any()


# ---------------------------------------------------------------------------
# 5. structural_reset_event (§5.8, fix B-03)
# ---------------------------------------------------------------------------


class TestStructuralResetEvent:
    def test_long_pathological_span_with_surviving_pivot_state_does_not_emit(
        self,
    ):
        """
        RP-1 Block 1-E / RFC v3.1 §5.8 condition (3).

        Recovery after a long pathological span where pivot state
        SURVIVED the span (cur_leg_dir stayed UP, last_pivot_bar kept its
        pre-pathology value) is a "seamless continuation" and MUST NOT
        emit `structural_reset_event` per the RFC.

        Prior to RP-1E the block emitted on this fixture (span ≥ min +
        not-session-reset was enough); RP-1E adds the precondition
        `last_pivot_bar < 0 OR cur_leg_dir == LEG_DIR_UNKNOWN` evaluated
        at entry to bar t.
        """
        # Construct OHLC with a 5-bar NaN span in the middle (min_span = 3).
        # Bar 0 seeds last_pivot_bar=0 from open; bars 1..7 rise
        # monotonically, so at bar 8 entry cur_leg_dir == LEG_DIR_UP
        # (set in §1.1.3 on bar 0 via the dominating-bar branch).  After
        # bars 8..12 (pathological, frozen state) the recovery bar 13
        # enters with last_pivot_bar=0 AND cur_leg_dir=LEG_DIR_UP —
        # condition (3) is FALSE.
        N = 20
        high = np.linspace(100.0, 110.0, N)
        low = high - 1.0
        open_p = (high + low) / 2.0
        # NaN-spike bars 8..12 inclusive (5 pathological bars).
        for t in range(8, 13):
            high[t] = np.nan
            low[t] = np.nan
            open_p[t] = np.nan
        session_ids = np.zeros(N, dtype=np.int64)

        res = _confirmed_zigzag_pass(
            high=high, low=low, open_prices=open_p,
            session_ids=session_ids, reversal_threshold=0.005,
            structural_reset_min_span=3,
        )
        # Recovery bar is 13 (first valid after span 8..12).
        assert res.pathological[8:13].all()
        assert not res.pathological[13]
        # RP-1E: state survived → event does NOT fire.
        assert not bool(res.structural_reset_event[13]), (
            "RP-1E: recovery with surviving pivot state must NOT emit "
            "structural_reset_event (RFC §5.8 condition 3 — "
            "pivot_state_reinitialized_on_entry is False)."
        )
        # And definitely not on mid-span either.
        assert not res.structural_reset_event[8:13].any()

    def test_short_span_does_not_emit_event(self):
        # Only 2 pathological bars (< default min_span = 3) → no event.
        N = 10
        high = np.linspace(100.0, 105.0, N)
        low = high - 1.0
        open_p = (high + low) / 2.0
        high[5] = np.nan
        high[6] = np.nan
        low[5] = np.nan
        low[6] = np.nan
        open_p[5] = np.nan
        open_p[6] = np.nan
        session_ids = np.zeros(N, dtype=np.int64)

        res = _confirmed_zigzag_pass(
            high=high, low=low, open_prices=open_p,
            session_ids=session_ids, reversal_threshold=0.005,
            structural_reset_min_span=3,
        )
        assert not res.structural_reset_event.any()


# ---------------------------------------------------------------------------
# 5.1 RP-1 Block 1-E — structural_reset_event precondition tightening
# ---------------------------------------------------------------------------


class TestRP1EStructuralResetPrecondition:
    """
    RP-1 Block 1-E — RFC v3.1 §5.8 condition (3).

    Verifies the tightened precondition:

        structural_reset_event[t] == True  iff  all of:
            (1) recovery (prior bar pathological, current bar valid),
            (2) pathological_span >= structural_reset_min_span,
            (3) pivot_state_reinitialized_on_entry
                == (last_pivot_bar < 0) OR (cur_leg_dir == LEG_DIR_UNKNOWN),
            (4) NOT session_reset_event[t].

    Negative case (state survived) is covered by
    `TestStructuralResetEvent.test_long_pathological_span_with_surviving_pivot_state_does_not_emit`.
    This class adds the positive case (state reinitialized on entry),
    a session_reset-wins case, and a structural invariant on
    `leg_direction[t]` on actual emit.
    """

    def test_recovery_with_reinitialized_state_on_entry_emits_event(self):
        """
        Pathological span starting from bar 0 → at recovery bar
        last_pivot_bar == -1 AND cur_leg_dir == LEG_DIR_UNKNOWN on entry
        → condition (3) TRUE → event MUST fire.
        """
        # Bars 0..4 pathological (span=5 ≥ min=3), bar 5 is the first
        # valid bar.  On entry to bar 5, pivot state has never been
        # seeded (last_pivot_bar still -1, cur_leg_dir still UNKNOWN).
        N = 15
        high = np.linspace(100.0, 110.0, N)
        low = high - 1.0
        open_p = (high + low) / 2.0
        for t in range(0, 5):
            high[t] = np.nan
            low[t] = np.nan
            open_p[t] = np.nan
        session_ids = np.zeros(N, dtype=np.int64)

        res = _confirmed_zigzag_pass(
            high=high, low=low, open_prices=open_p,
            session_ids=session_ids, reversal_threshold=0.005,
            structural_reset_min_span=3,
        )
        assert res.pathological[0:5].all()
        assert not res.pathological[5]
        assert bool(res.structural_reset_event[5]), (
            "RP-1E: recovery with reinitialized pivot state on entry "
            "(last_pivot_bar < 0 AND cur_leg_dir == UNKNOWN) MUST emit "
            "structural_reset_event."
        )
        # Exactly one emit in the whole run.
        assert int(res.structural_reset_event.sum()) == 1
        # No emit on any prior bar (all pathological).
        assert not res.structural_reset_event[0:5].any()

    def test_session_reset_on_recovery_bar_suppresses_structural_reset(self):
        """
        If session_reset_event[t] fires on the same recovery bar, the
        de-duplication guard `not session_reset_event[t]` MUST win:
        structural_reset_event[t] stays False (§4.5 / §8.3.9).
        """
        # Pathological bars 0..4, recovery on bar 5 with session
        # boundary between bar 4 and bar 5.  Session reset clears state
        # AND resets pathological_span to 0 on the new-session bar — so
        # structurally the span gate also fails.  We assert the
        # observable invariant: structural_reset_event[5] is False.
        N = 15
        high = np.linspace(100.0, 110.0, N)
        low = high - 1.0
        open_p = (high + low) / 2.0
        for t in range(0, 5):
            high[t] = np.nan
            low[t] = np.nan
            open_p[t] = np.nan
        session_ids = np.zeros(N, dtype=np.int64)
        # New session starts at bar 5 (the recovery bar).
        session_ids[5:] = 1

        res = _confirmed_zigzag_pass(
            high=high, low=low, open_prices=open_p,
            session_ids=session_ids, reversal_threshold=0.005,
            structural_reset_min_span=3,
        )
        assert bool(res.session_reset_event[5])
        assert not bool(res.structural_reset_event[5]), (
            "RP-1E: session_reset_event on the same bar MUST suppress "
            "structural_reset_event (no double-fire)."
        )

    def test_emit_forces_leg_direction_unknown_on_that_bar_if_no_trigger(self):
        """
        On actual structural_reset emit, the side-effect reset clears
        `cur_leg_dir = LEG_DIR_UNKNOWN` AND `last_pivot_bar = -1`.
        Step 1.1.2 then re-seeds `last_pivot_bar = t` from `open[t]`.
        If §1.1.3 triggers do NOT fire on the same bar, `leg_direction[t]`
        should still be UNKNOWN in the per-bar snapshot.

        We pick a recovery bar where neither up nor down trigger hits
        (bar range is narrow relative to reversal_threshold).
        """
        # Same pattern as the positive case: pathological from bar 0,
        # recovery on bar 5, but with a narrow price range so that
        # §1.1.3 on bar 5 cannot trigger a direction flip from the
        # freshly-seeded last_pivot_pr = open[5].
        N = 10
        high = np.full(N, 100.0)
        low = np.full(N, 99.99)
        open_p = np.full(N, 99.995)
        for t in range(0, 5):
            high[t] = np.nan
            low[t] = np.nan
            open_p[t] = np.nan
        session_ids = np.zeros(N, dtype=np.int64)

        res = _confirmed_zigzag_pass(
            high=high, low=low, open_prices=open_p,
            session_ids=session_ids, reversal_threshold=0.005,
            structural_reset_min_span=3,
        )
        assert bool(res.structural_reset_event[5])
        # RFC invariant preserved: leg_direction[t] == UNKNOWN on actual
        # structural reset when §1.1.3 did not flip the direction.
        assert int(res.leg_direction[5]) == int(LEG_DIR_UNKNOWN), (
            f"RP-1E: on actual structural_reset emit without §1.1.3 "
            f"trigger, leg_direction[t] must stay UNKNOWN; got "
            f"{int(res.leg_direction[5])}."
        )

    def test_event_mask_matches_explicit_four_condition_formula(self):
        """
        Property-style structural check on a mixed fixture:
        `structural_reset_event[t] == True` on every bar reachable by
        the 4-condition RFC formula and ONLY on those bars.

        We cross-check via: any bar where event fires must satisfy
        (pathological[t-1] AND NOT pathological[t]) AND NOT
        session_reset_event[t].  The pivot-state-on-entry piece is
        validated indirectly: the counter-example in
        `test_long_pathological_span_with_surviving_pivot_state_does_not_emit`
        + positive case here prove the gate.
        """
        N = 30
        high = np.linspace(100.0, 103.0, N)  # narrow range
        low = high - 0.01
        open_p = (high + low) / 2.0
        # Two pathological spans: bars 0..4 (positive trigger) and
        # 15..17 (too short for min_span=4).
        for t in (*range(0, 5), *range(15, 18)):
            high[t] = np.nan
            low[t] = np.nan
            open_p[t] = np.nan
        session_ids = np.zeros(N, dtype=np.int64)

        res = _confirmed_zigzag_pass(
            high=high, low=low, open_prices=open_p,
            session_ids=session_ids, reversal_threshold=0.005,
            structural_reset_min_span=4,
        )
        fired_mask = res.structural_reset_event
        for t in range(N):
            if not fired_mask[t]:
                continue
            # (1) recovery transition
            assert t > 0 and bool(res.pathological[t - 1]) and not bool(
                res.pathological[t]
            ), (
                f"structural_reset_event fired on bar {t} but recovery "
                "transition (1) is not satisfied."
            )
            # (4) session_reset dedup
            assert not bool(res.session_reset_event[t]), (
                f"structural_reset_event fired on bar {t} simultaneously "
                "with session_reset_event (dedup violated)."
            )
        # Bar 5 is the only valid emit site under this fixture.
        assert int(fired_mask.sum()) == 1
        assert bool(fired_mask[5])
        # Short span at 15..17 must NOT emit on bar 18.
        assert not bool(fired_mask[18])


# ---------------------------------------------------------------------------
# 6. Backward-compat of _compute_allow_entry_and_reason(regime_state=...)
# ---------------------------------------------------------------------------


class TestDecisionLayerLegacyCompat:
    def test_legacy_regime_state_kwarg_still_works(self):
        # Legacy call path (used by unit tests in test_zigzag_opus_invariants.py)
        # passes regime_state=; this MUST still produce sensible output.
        N = 5
        allow, reason = _compute_allow_entry_and_reason(
            n_bars=N,
            pathological=np.zeros(N, dtype=bool),
            n_legs_before=np.full(N, 50, dtype=np.int64),
            leg_direction=np.full(N, LEG_DIR_UP, dtype=np.int8),
            regime_state=np.full(N, 0, dtype=np.int8),  # REGIME_CLOSED
            armed=np.zeros(N, dtype=bool),
            armed_side=np.zeros(N, dtype=np.int8),
            one_shot=np.zeros(N, dtype=bool),
            timeout_expired_on_bar=np.zeros(N, dtype=bool),
            new_pivot_disarm_on_bar=np.zeros(N, dtype=bool),
            st_flip_on_bar=np.zeros(N, dtype=bool),
            min_legs_global=10,
        )
        # regime_state == CLOSED maps to NOT readiness_on → zz_regime_off.
        assert all(r == FILTER_REASON_ZZ_REGIME_OFF for r in reason)
        assert not allow.any()


# ---------------------------------------------------------------------------
# 7. compute_zigzag_filter: end-to-end plumbing and array shapes
# ---------------------------------------------------------------------------


def _build_simple_cfg(**over) -> dict:
    cfg = dict(
        reversal_threshold=0.005,
        min_legs_global=0,
        q_strong=0.80,
        k_local=5,
        entry_side="counter_trend",
        arm_timeout_bars_since_extreme=24,
        arm_timeout_bars_hard=78,
        structural_reset_min_span=3,
        readiness=dict(
            contour_a=dict(enabled=True, p80_quantile=0.80),
            contour_b=dict(enabled=False, local_k=5, open_ratio=1.5, close_ratio=1.0),
        ),
    )
    cfg.update(over)
    return cfg


def _sample_inputs(N: int = 50):
    high = 100.0 + np.cumsum(np.random.RandomState(42).uniform(-0.3, 0.3, N))
    low = high - 1.0
    close = (high + low) / 2
    open_p = close.copy()
    session_ids = np.zeros(N, dtype=np.int64)
    st_trend = np.ones(N, dtype=np.int8)
    return high, low, close, open_p, session_ids, st_trend


class TestComputeZigZagFilterPlumbing:
    def test_result_has_all_new_fields_with_correct_shape(self):
        N = 50
        inputs = _sample_inputs(N)
        res = compute_zigzag_filter(*inputs, cfg=_build_simple_cfg())

        assert isinstance(res, ZigZagFilterResult)
        # RFC §7.1: new per-bar arrays exist and have length N.
        for name, dtype in [
            ("ready_a", bool),
            ("ready_b", bool),
            ("readiness_on", bool),
            ("arm_source", np.int8),
            ("arm_source_for_decision", np.int8),
            ("cand_leg_id", np.int64),
            ("disarm_event", np.int8),
            ("structural_reset_event", bool),
        ]:
            arr = getattr(res, name)
            assert arr.shape == (N,), f"{name}: expected shape ({N},), got {arr.shape}"
            assert arr.dtype == dtype, f"{name}: expected dtype {dtype}, got {arr.dtype}"
        # readiness_block_reason is object dtype (strings).
        assert res.readiness_block_reason.shape == (N,)

    def test_readiness_on_equals_enabled_combined(self):
        N = 50
        inputs = _sample_inputs(N)
        res = compute_zigzag_filter(*inputs, cfg=_build_simple_cfg())
        # A=on, B=off: readiness_on == ready_a, ready_b all False.
        assert not res.ready_b.any()
        assert np.array_equal(res.readiness_on, res.ready_a)

    def test_both_contours_disabled_readiness_all_false(self):
        cfg = _build_simple_cfg()
        cfg["readiness"]["contour_a"]["enabled"] = False
        cfg["readiness"]["contour_b"]["enabled"] = False
        N = 30
        inputs = _sample_inputs(N)
        # Both disabled is a valid tester/debug mode (§6.6).  The "A=off, B=off"
        # UserWarning fires in cli/tester._validate_zigzag_section, NOT here —
        # core.compute_zigzag_filter just honors the already-normalised cfg.
        res = compute_zigzag_filter(*inputs, cfg=cfg)
        assert not res.readiness_on.any()
        assert not res.allow_entry.any()

    def test_reasons_remain_in_whitelist(self):
        # G-03: public filtered_reason[t] must stay inside FILTER_REASON_WHITELIST.
        N = 80
        inputs = _sample_inputs(N)
        res = compute_zigzag_filter(*inputs, cfg=_build_simple_cfg())
        for r in res.reason:
            assert r in FILTER_REASON_WHITELIST, (
                f"reason {r!r} leaked outside FILTER_REASON_WHITELIST (G-03 violation)"
            )

    def test_cand_leg_id_matches_pass_result(self):
        # RFC §5.2: cand_leg_id is -1 when leg_direction == UNKNOWN, monotonic otherwise.
        N = 80
        inputs = _sample_inputs(N)
        res = compute_zigzag_filter(*inputs, cfg=_build_simple_cfg())
        # Where leg_direction == UNKNOWN, cand_leg_id == -1.
        mask_unknown = (res.leg_direction == LEG_DIR_UNKNOWN)
        assert (res.cand_leg_id[mask_unknown] == -1).all()
        # Monotonic non-decreasing on non-UNKNOWN bars (counter only increments).
        known_ids = res.cand_leg_id[~mask_unknown]
        if known_ids.size > 1:
            diffs = np.diff(known_ids)
            assert (diffs >= 0).all(), "cand_leg_id must be monotonic non-decreasing"


# ---------------------------------------------------------------------------
# RP-1 Block 1-A — RFC v3.1 §4.7 fix D4
# ---------------------------------------------------------------------------
#
# Baseline 0.B.2 finding F-1: `is_post_confirm` in `_unified_armament_fsm`
# was compared against the *post-switch* `cur_cand_id` (cand_leg_id_arr[t]
# is already the new id on a confirm_bar) instead of the pre-flip session
# id.  Combined with the missing pre-confirm → post-confirm transition in
# the `owns_the_confirming_leg` block, a YES_SHOT on the same confirm_bar
# as the owning leg could not land on the owning LegRecord.
#
# TP-33/34/35 are codified in rfc_v3.1_remediation_plan.txt (lines ~265-280)
# as the RP-1 Block 1-A exit tests.
# ---------------------------------------------------------------------------


from supertrend_optimizer.core.zigzag_filter import (  # noqa: E402
    ARM_SRC_B as _ARM_SRC_B,
    ARM_SRC_BOTH as _ARM_SRC_BOTH,
    FIRED_NO_NEW_PIVOT as _FIRED_NO_NEW_PIVOT,
    FIRED_NONE as _FIRED_NONE,
    FIRED_YES_SHOT as _FIRED_YES_SHOT,
)


def _block1a_fixture(seed: int):
    """Deterministic OHLC + st_trend fixture, same shape as the property
    sweep in test_zigzag_readiness_properties._build_fixture."""
    rng = np.random.default_rng(seed)
    n_sessions, session_len = 3, 400
    N = n_sessions * session_len

    steps = rng.normal(loc=0.0, scale=0.6, size=N)
    close = 100.0 + np.cumsum(steps)
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0.05, 0.8, N)
    low = close - rng.uniform(0.05, 0.8, N)
    open_p = close.copy()

    session_ids = np.repeat(
        np.arange(n_sessions, dtype=np.int64), session_len
    )

    ema = np.empty(N, dtype=np.float64)
    alpha = 2.0 / (10.0 + 1.0)
    ema[0] = close[0]
    for i in range(1, N):
        ema[i] = alpha * close[i] + (1.0 - alpha) * ema[i - 1]
    slope = np.concatenate([[0.0], np.diff(ema)])
    st_trend = np.where(slope >= 0.0, 1, -1).astype(np.int8)

    return dict(
        high=high, low=low, close=close, open_prices=open_p,
        session_ids=session_ids, st_trend=st_trend,
    )


def _block1a_cfg(*, a: bool = True, b: bool = False,
                 b_open: float = 1.5, b_close: float = 1.0):
    return dict(
        reversal_threshold=0.005,
        min_legs_global=0,
        q_strong=0.80,
        k_local=5,
        entry_side="counter_trend",
        arm_timeout_bars_since_extreme=24,
        arm_timeout_bars_hard=78,
        structural_reset_min_span=3,
        readiness=dict(
            contour_a=dict(enabled=a, p80_quantile=0.80),
            contour_b=dict(
                enabled=b, local_k=5,
                open_ratio=b_open, close_ratio=b_close,
            ),
        ),
    )


_BLOCK1A_SEEDS = tuple(range(10_000, 10_050))


class TestBlock1APreConfirmTransition:
    """
    RP-1 Block 1-A exit tests for RFC v3.1 §4.7 fix D4 and §4.6 D3.

    These tests operate on a deterministic fixture sweep and assert
    behaviour of the pre-confirm → post-confirm transition at the
    owning-leg confirm bar.  Two of the three tests (TP-33 / TP-34) are
    regression tests that must hold across the fix; TP-35 is the critical
    one that specifically distinguishes pre- vs post-fix behaviour.
    """

    # ---- TP-33 -------------------------------------------------------
    def test_tp33_b_sourced_pre_confirm_armament(self):
        """
        TP-33 `b_sourced_pre_confirm_armament` — with only contour B
        enabled (legacy_parity-style: A off, B on), a pre-confirm
        armament originating from ready_B must flush back to the owning
        leg with `arm_source ∈ {ARM_SRC_B, ARM_SRC_BOTH}` and
        `armed_by_candidate == True`.  Guards the plumbing of B-sourced
        pre-confirm flushes (RFC §7.5 fix B-02).
        """
        cfg = _block1a_cfg(a=False, b=True, b_open=0.0, b_close=0.0)
        found = []
        for seed in _BLOCK1A_SEEDS:
            inp = _block1a_fixture(seed)
            res = compute_zigzag_filter(cfg=cfg, **inp)
            for lg in res.legs:
                if (lg.armed_by_candidate
                        and lg.arm_source in (_ARM_SRC_B, _ARM_SRC_BOTH)):
                    found.append((seed, lg.leg_id,
                                  lg.pre_confirm_arm_bar, lg.arm_source))
                    break
            if found:
                break

        assert found, (
            "TP-33: expected at least one leg with armed_by_candidate=True "
            "AND arm_source in {{B, BOTH}} across the B-only sweep; got 0. "
            "This indicates B-sourced pre-confirm flushes are broken."
        )

    # ---- TP-34 -------------------------------------------------------
    def test_tp34_pre_confirm_session_survives_intervening_confirm(self):
        """
        TP-34 `pre_confirm_session_survives_intervening_confirm` —
        a pre-confirm session must NOT be disarmed with
        FIRED_NO_NEW_PIVOT on the confirm_bar of its OWN candidate
        (RFC §4.6 fix D3, `owns_the_confirming_leg` guard).

        Check the per-bar diagnostic mirror `res.disarm_event[t]`
        (RFC §7.1): for any leg `lg` that flushed a pre-confirm
        session onto itself (armed_by_candidate == True), the
        disarm event AT ITS OWN confirm_bar must not be
        FIRED_NO_NEW_PIVOT.  Only one session is active at a time
        (cur_armed is a scalar), so disarm_event[lg.confirm_bar]
        reflects that session's fate on its own confirm.

        Note: `lg.fired == FIRED_NO_NEW_PIVOT` is not a violation of
        D3 — it is the EXPECTED outcome for a session that survives
        its own confirm, transitions to post-confirm ownership via
        the RP-1 Block 1-A fix, and is later disarmed with
        FIRED_NO_NEW_PIVOT on a SUBSEQUENT leg's confirm.  That
        NO_NEW_PIVOT happens at a different bar than lg.confirm_bar.
        """
        cfg = _block1a_cfg(a=True, b=False)
        violations = []
        inspected = 0
        for seed in _BLOCK1A_SEEDS:
            inp = _block1a_fixture(seed)
            res = compute_zigzag_filter(cfg=cfg, **inp)
            for lg in res.legs:
                if lg.armed_by_candidate:
                    inspected += 1
                    cb = lg.confirm_bar
                    if 0 <= cb < res.disarm_event.shape[0]:
                        if res.disarm_event[cb] == _FIRED_NO_NEW_PIVOT:
                            violations.append(
                                f"seed={seed} leg_id={lg.leg_id} "
                                f"confirm_bar={cb} "
                                f"disarm_event[{cb}]=FIRED_NO_NEW_PIVOT "
                                f"(D3 guard broken: session was disarmed "
                                f"on its OWN owning-leg confirm)"
                            )

        assert inspected > 0, (
            "TP-34: no legs with armed_by_candidate=True across the sweep "
            "— fixture pool is too narrow to exercise the guard"
        )
        assert not violations, (
            "TP-34: `owns_the_confirming_leg` guard (RFC §4.6 D3) is "
            "broken — a pre-confirm session was disarmed with "
            "FIRED_NO_NEW_PIVOT on its own owning-leg confirm_bar.\n"
            + "\n".join(violations[:10])
        )

    # ---- TP-35 -------------------------------------------------------
    def test_tp35_post_confirm_shot_from_pre_confirm_session(self):
        """
        TP-35 `post_confirm_shot_from_pre_confirm_session` — the
        critical RP-1 Block 1-A regression test (RFC v3.1 §4.7 fix D4).

        When an armed pre-confirm session's own candidate confirms at
        bar t and the ST-flip fires on the same bar, the session must
        transition to post-confirm ownership in-flight and the YES_SHOT
        must land on the owning LegRecord:

            lg.armed_by_candidate == True
            lg.fired              == FIRED_YES_SHOT
            lg.shot_bar           == lg.confirm_bar

        Pre-fix (`is_post_confirm` compared against post-switch
        cur_cand_id AND cur_is_pre_confirm never cleared on owning
        confirm) such a leg is impossible to produce: every pre-confirm
        session on its own confirm_bar stays pre-confirm, and
        `_disarm_session` in the pre-confirm branch does NOT call
        `_record_leg_disarm`, so `fired` stays FIRED_NONE and
        `shot_bar` stays -1.
        """
        cfg = _block1a_cfg(a=True, b=False)
        hits = []
        for seed in _BLOCK1A_SEEDS:
            inp = _block1a_fixture(seed)
            res = compute_zigzag_filter(cfg=cfg, **inp)
            for lg in res.legs:
                if (lg.armed_by_candidate
                        and lg.fired == _FIRED_YES_SHOT
                        and lg.shot_bar == lg.confirm_bar):
                    hits.append((seed, lg.leg_id, lg.confirm_bar,
                                 lg.shot_bar, lg.pre_confirm_shot_bar))

        assert hits, (
            "TP-35: expected at least one leg with "
            "armed_by_candidate=True, fired=FIRED_YES_SHOT, "
            "shot_bar==confirm_bar across the A-only sweep; got 0. "
            "This is the signature of the is_post_confirm / transition "
            "defect (RFC §4.7 D4 / baseline 0.B.2 F-1)."
        )
        for seed, leg_id, confirm_bar, shot_bar, pre_shot in hits[:5]:
            assert shot_bar == confirm_bar, (
                f"seed={seed} leg_id={leg_id}: shot_bar={shot_bar} "
                f"!= confirm_bar={confirm_bar}"
            )
            assert pre_shot == -1, (
                f"seed={seed} leg_id={leg_id}: pre_confirm_shot_bar="
                f"{pre_shot} must be -1 for a post-confirm shot"
                " (RFC §7.5 — pre_confirm_shot_bar is pre-confirm only)"
            )
        _ = _FIRED_NONE  # keep import referenced


# ---------------------------------------------------------------------------
# RP-1 Block 1-B — RFC v3.1 §4.2 within-bar ordering
# ---------------------------------------------------------------------------
#
# After the Block 1-A fixes (D4 / §4.7), the 12-step within-bar ordering in
# `_unified_armament_fsm` must remain identical to RFC v3.1 §4.2:
#
#   1  session_reset                  7  B-deactivation disarm
#   2  structural_reset               8  ready_A / readiness_on[t]
#   3  pathological freeze + skip     9  armament creation
#   4  confirm pivot + owning-leg    10  decision snapshot (pre-flip, D6)
#   5  one_shot reset on dir change  11  ST flip → YES_SHOT disarm
#   6  ready_B step (precomputed)    12  timers / extreme update
#
# LX-01 is a structural check on the *source text* of the function: it
# guarantees the canonical `# §4.2 step N: ...` markers are present, in
# order, exactly once each.  It is intentionally line-number agnostic.
#
# TP-36 is a runtime behavioural check that the decision snapshot at
# step 10 contains the PRE-disarm (armed=True) state on a YES_SHOT bar,
# i.e. that step 10 runs BEFORE step 11 (RFC §5.4 fix D6).
# ---------------------------------------------------------------------------


import inspect as _inspect  # noqa: E402
import re as _re  # noqa: E402

from supertrend_optimizer.core import zigzag_filter as _zz_module  # noqa: E402
from supertrend_optimizer.core.zigzag_filter import (  # noqa: E402
    ARMED_SIDE_NONE as _ARMED_SIDE_NONE,
    ARM_SRC_NONE as _ARM_SRC_NONE,
)


class TestLX01WithinBarOrdering:
    """
    LX-01 `within_bar_ordering_structural_check` — the 12 canonical RFC
    §4.2 step markers must be present in `_unified_armament_fsm` in
    strictly increasing order 1..12, each exactly once.

    Checks the source of `_unified_armament_fsm` via `inspect.getsource`
    (not a whole-file grep), so stray markers anywhere else in the module
    cannot mask missing / mis-ordered markers inside the function.
    """

    _MARKER_RE = _re.compile(r"#\s*§4\.2\s+step\s+(\d+)\b")

    def _collect_step_markers(self):
        src = _inspect.getsource(_zz_module._unified_armament_fsm)
        markers = []
        for lineno, line in enumerate(src.splitlines(), start=1):
            m = self._MARKER_RE.search(line)
            if m:
                markers.append((lineno, int(m.group(1)), line.strip()))
        return markers

    def test_all_12_step_markers_present(self):
        markers = self._collect_step_markers()
        step_ids = [s for _, s, _ in markers]
        assert sorted(set(step_ids)) == list(range(1, 13)), (
            "LX-01: expected steps 1..12 each exactly once in "
            "`_unified_armament_fsm`, got "
            f"{sorted(step_ids)} (duplicates/missing are both failures)"
        )
        assert len(step_ids) == 12, (
            "LX-01: expected exactly 12 step markers, got "
            f"{len(step_ids)} (ids: {step_ids})"
        )

    def test_step_markers_strictly_increasing(self):
        markers = self._collect_step_markers()
        step_ids = [s for _, s, _ in markers]
        assert step_ids == sorted(step_ids), (
            "LX-01: §4.2 step markers in `_unified_armament_fsm` must "
            "appear in strictly non-decreasing order; found ordering "
            f"{step_ids}"
        )
        assert step_ids == list(range(1, 13)), (
            "LX-01: §4.2 step markers must appear in canonical order "
            f"1..12; found {step_ids}"
        )


class TestTP36DecisionSnapshotOnShotBar:
    """
    TP-36 `arm_source_snapshot_on_shot_bar` — RFC §5.4 / fix D6.

    `ZigZagFilterResult` exposes only one of the three decision-time
    snapshot arrays publicly:

        res.arm_source_for_decision  (§7.1 diagnostic)

    The other two (`armed_for_decision` / `armed_side_for_decision`)
    are consumed internally by `_compute_allow_entry_and_reason` and
    are not part of the public surface.  Runtime mirrors (`res.armed`,
    `res.armed_side`) hold the END-of-bar (post-disarm) state and are
    False/NONE on a shot bar by design — they MUST NOT be used to
    verify the D6 snapshot.

    Core TP-36 assertion therefore operates on the one D6 signal that
    is unambiguously observable:

        ∀ lg with fired == FIRED_YES_SHOT and shot_bar >= 0:
            res.arm_source_for_decision[shot_bar] != ARM_SRC_NONE
            res.arm_source_for_decision[shot_bar] == lg.arm_source
                (when lg.arm_source != ARM_SRC_NONE)

    Regression failure mode: if RFC §4.2 step 10 is re-ordered AFTER
    step 11, `_disarm_session` sets `cur_arm_source = ARM_SRC_NONE`
    BEFORE the snapshot write, and `arm_source_for_decision[sb]`
    collapses to ARM_SRC_NONE on every shot bar.
    """

    def test_snapshot_captures_pre_disarm_state_on_shot_bar(self):
        cfg = _block1a_cfg(a=True, b=False)
        inspected = 0
        none_violations = []
        mismatch_violations = []
        for seed in _BLOCK1A_SEEDS:
            inp = _block1a_fixture(seed)
            res = compute_zigzag_filter(cfg=cfg, **inp)
            for lg in res.legs:
                if lg.fired != _FIRED_YES_SHOT:
                    continue
                sb = lg.shot_bar
                if sb < 0 or sb >= res.arm_source_for_decision.shape[0]:
                    continue
                inspected += 1
                src_snap = int(res.arm_source_for_decision[sb])
                if src_snap == _ARM_SRC_NONE:
                    none_violations.append(
                        f"seed={seed} leg_id={lg.leg_id} shot_bar={sb}: "
                        f"arm_source_for_decision[{sb}]=NONE "
                        "(expected A/B/BOTH — snapshot was cleared by "
                        "ST-flip disarm; §4.2 step 10 must run BEFORE "
                        "step 11, fix D6)"
                    )
                if (lg.arm_source != _ARM_SRC_NONE
                        and src_snap != int(lg.arm_source)):
                    mismatch_violations.append(
                        f"seed={seed} leg_id={lg.leg_id} shot_bar={sb}: "
                        f"arm_source_for_decision[{sb}]={src_snap} "
                        f"!= lg.arm_source={int(lg.arm_source)} "
                        "(snapshot does not match the session's "
                        "arm_source at shot time)"
                    )

        assert inspected > 0, (
            "TP-36: no FIRED_YES_SHOT legs with a valid shot_bar across "
            "the sweep; fixture pool is too narrow to exercise step 10"
        )
        assert not none_violations, (
            "TP-36: decision snapshot (§4.2 step 10) does not contain "
            "pre-disarm arm_source on the shot bar — this is the D6 "
            "regression signature.\n" + "\n".join(none_violations[:10])
        )
        assert not mismatch_violations, (
            "TP-36: decision snapshot on the shot bar does not match "
            "the owning leg's arm_source (flushed or post-confirm "
            "recorded).\n" + "\n".join(mismatch_violations[:10])
        )
        _ = _ARMED_SIDE_NONE  # keep imported symbol referenced


# ---------------------------------------------------------------------------
# RP-1 Block 1-C — flush pre-confirm metadata → LegRecord
# ---------------------------------------------------------------------------
#
# After RP-1A the owning-leg transition works correctly; Block 1-C verifies
# (and, where needed, tightens) the downstream flush from the pre-confirm
# session ledger into LegRecord fields (RFC v3.1 §7.5 fix B-02).
#
# Scenarios enumerated in the plan:
#   1. pre-confirm arm → confirm (no shot)            — regression
#   2. pre-confirm arm → pre-confirm shot → confirm   — RP-1C fix target
#   3. pre-confirm arm → same-bar/post confirm shot   — TP-35 (RP-1A)
#   4. orphan candidate (pre-confirm but no confirm)  — must NOT synthesise
#
# §8.3.10 invariant:
#   ∀ lg (fired != FIRED_NONE):  (pre_confirm_arm_bar != -1) ⇔ armed_by_candidate
# The strong form (all legs, any fired) is exercised by TP-41 in
# test_zigzag_readiness_properties.py; a local scoped check is included
# for completeness.
# ---------------------------------------------------------------------------


class TestRP1CFlushTrace:
    """
    RP-1 Block 1-C tests.  Shares `_block1a_fixture` / `_block1a_cfg` /
    `_BLOCK1A_SEEDS` with the Block 1-A / 1-B harness so the same
    deterministic sweep exercises all three blocks.
    """

    # ---- Scenario 1 --------------------------------------------------
    def test_scenario1_pre_confirm_arm_then_confirm_no_shot(self):
        """
        Pre-confirm session starts at bar s, reaches confirm_bar WITHOUT a
        pre-confirm YES_SHOT (the session either survives to post-confirm
        ownership or was disarmed for a non-shot reason in between).
        Flush must still set:

            armed_by_candidate       == True
            pre_confirm_arm_bar      >= 0
            pre_confirm_arm_bar      <  confirm_bar
            arm_source               != ARM_SRC_NONE

        and `pre_confirm_shot_bar` must be -1 (no pre-confirm shot
        occurred).  This is the classical §7.5 fix B-02 path and must
        keep working after the Block 1-C shot-bar propagation change.
        """
        cfg = _block1a_cfg(a=True, b=False)
        hits = []
        for seed in _BLOCK1A_SEEDS:
            inp = _block1a_fixture(seed)
            res = compute_zigzag_filter(cfg=cfg, **inp)
            for lg in res.legs:
                if (lg.armed_by_candidate
                        and lg.pre_confirm_arm_bar >= 0
                        and lg.pre_confirm_shot_bar == -1):
                    hits.append((seed, lg))

        assert hits, (
            "Scenario 1: expected at least one leg with a pre-confirm "
            "arm but no pre-confirm shot across the sweep; got 0"
        )
        for seed, lg in hits[:20]:
            assert lg.pre_confirm_arm_bar < lg.confirm_bar, (
                f"seed={seed} leg_id={lg.leg_id}: "
                f"pre_confirm_arm_bar={lg.pre_confirm_arm_bar} must be "
                f"< confirm_bar={lg.confirm_bar} (§7.5 — pre-confirm "
                "means strictly before confirm)"
            )
            assert lg.arm_source != _ARM_SRC_NONE, (
                f"seed={seed} leg_id={lg.leg_id}: "
                f"arm_source={lg.arm_source} must be non-NONE when "
                f"armed_by_candidate=True (§7.5 flush must adopt "
                "ledger arm_source)"
            )

    # ---- Scenario 2 (RP-1C fix target) -------------------------------
    def test_scenario2_pre_confirm_shot_then_confirm(self):
        """
        Pre-confirm session starts at bar s, a ST-flip fires a YES_SHOT
        at bar t_shot (s <= t_shot < confirm_bar), the session is
        disarmed, and the owning leg still confirms at bar c (c > t_shot).

        After RP-1C the owning LegRecord must carry the pre-confirm
        shot bar on its own Phase-5 field:

            armed_by_candidate    == True
            pre_confirm_arm_bar   in [0, confirm_bar)
            pre_confirm_shot_bar  in [pre_confirm_arm_bar, confirm_bar)
            arm_source            != ARM_SRC_NONE

        Pre-RP-1C this test is impossible to satisfy: core never wrote
        `pre_confirm_shot_bar`, it was filled only in the io-layer
        `_link_trades_to_legs` after the core result was already
        materialised.

        Note on legacy `fired` / `shot_bar`: those track the POST-confirm
        cycle, which is orthogonal to the pre-confirm cycle.  After a
        pre-confirm shot + subsequent confirm, one_shot is reset on the
        direction change (§4.2 step 5) and a NEW post-confirm armament
        can legitimately start at confirm_bar and later be disarmed by
        NO_NEW_PIVOT / YES_SHOT / timeout.  So `fired` / `shot_bar`
        may hold ANY value without breaking the pre-confirm
        semantics — they are asserted separately by TP-40 (arm_bar
        invariant) and TP-34 (D3 guard).
        """
        cfg = _block1a_cfg(a=True, b=False)
        hits = []
        for seed in _BLOCK1A_SEEDS:
            inp = _block1a_fixture(seed)
            res = compute_zigzag_filter(cfg=cfg, **inp)
            for lg in res.legs:
                if lg.pre_confirm_shot_bar >= 0:
                    hits.append((seed, lg))

        assert hits, (
            "Scenario 2: expected at least one leg with "
            "pre_confirm_shot_bar >= 0 across the sweep; got 0.  "
            "This is the RP-1C core-propagation signature."
        )
        for seed, lg in hits[:20]:
            assert lg.armed_by_candidate is True, (
                f"seed={seed} leg_id={lg.leg_id}: "
                "pre_confirm_shot_bar set but armed_by_candidate=False "
                "(flush/ledger consistency broken)"
            )
            assert 0 <= lg.pre_confirm_arm_bar < lg.confirm_bar, (
                f"seed={seed} leg_id={lg.leg_id}: "
                f"pre_confirm_arm_bar={lg.pre_confirm_arm_bar} must "
                f"be in [0, confirm_bar={lg.confirm_bar})"
            )
            assert lg.pre_confirm_arm_bar <= lg.pre_confirm_shot_bar, (
                f"seed={seed} leg_id={lg.leg_id}: "
                f"pre_confirm_shot_bar={lg.pre_confirm_shot_bar} must "
                f">= pre_confirm_arm_bar={lg.pre_confirm_arm_bar}"
            )
            assert lg.pre_confirm_shot_bar < lg.confirm_bar, (
                f"seed={seed} leg_id={lg.leg_id}: "
                f"pre_confirm_shot_bar={lg.pre_confirm_shot_bar} must "
                f"be STRICTLY less than confirm_bar={lg.confirm_bar} "
                "(§7.5 — pre-confirm means before confirm)"
            )
            assert lg.arm_source != _ARM_SRC_NONE, (
                f"seed={seed} leg_id={lg.leg_id}: arm_source=NONE on "
                "a pre-confirm-shot leg; flush must adopt the ledger "
                "arm_source (§7.5 B-02)"
            )
        _ = _FIRED_NONE  # keep import referenced

    # ---- Scenario 3 reference (covered by TP-35 in Block 1-A) --------
    # No new assertion here; TP-35 in TestBlock1APreConfirmTransition
    # already guards the pre→post transition + same-bar shot landing on
    # the owning LegRecord via shot_bar == confirm_bar.

    # ---- Scenario 4 --------------------------------------------------
    def test_scenario4_no_synthetic_leg_from_orphan_candidate(self):
        """
        An orphan pre-confirm session (its candidate never confirms a
        leg) must NOT cause a synthetic LegRecord to appear.  The flush
        iterates only over the `legs` list produced by
        `_confirmed_zigzag_pass`, so orphan ledger entries are silently
        discarded.

        Observable contract: leg_ids are a contiguous 0..len(legs)-1
        sequence; no leg has armed_by_candidate=True without a
        corresponding confirmed pivot (i.e. the leg's own
        start_bar / end_bar / confirm_bar are all non-negative and
        consistent with the source pass).
        """
        cfg = _block1a_cfg(a=True, b=False)
        orphan_sightings = []
        for seed in _BLOCK1A_SEEDS[:10]:
            inp = _block1a_fixture(seed)
            res = compute_zigzag_filter(cfg=cfg, **inp)

            leg_ids = [int(lg.leg_id) for lg in res.legs]
            expected = list(range(len(res.legs)))
            assert leg_ids == expected, (
                f"seed={seed}: leg_ids are not contiguous 0..N-1; got "
                f"{leg_ids[:10]}{'...' if len(leg_ids) > 10 else ''} — "
                "indicates a synthetic leg was inserted"
            )

            for lg in res.legs:
                if lg.armed_by_candidate:
                    if not (0 <= lg.start_bar < lg.end_bar < lg.confirm_bar):
                        orphan_sightings.append(
                            f"seed={seed} leg_id={lg.leg_id}: "
                            f"start={lg.start_bar} end={lg.end_bar} "
                            f"confirm={lg.confirm_bar} "
                            "— armed_by_candidate=True on a "
                            "malformed leg (synthetic insertion?)"
                        )

        assert not orphan_sightings, (
            "Scenario 4: synthetic / malformed legs detected — flush "
            "must NEVER create a LegRecord post-hoc.\n"
            + "\n".join(orphan_sightings[:10])
        )

    # ---- §8.3.10 scoped invariant -----------------------------------
    def test_invariant_8_3_10_scoped_to_rp1c_sweep(self):
        """
        RFC §8.3.10 (TP-41): ∀ lg with fired != FIRED_NONE:
            (pre_confirm_arm_bar != -1)  ⇔  armed_by_candidate

        Scoped to the RP-1C fixture sweep so Block 1-C explicitly
        carries its own §8.3.10 evidence (the global 400-fixture
        sweep lives in test_zigzag_readiness_properties.py).  The
        local sweep also exercises the strong form (all legs,
        regardless of `fired`) because the flush sets both fields
        atomically.
        """
        cfg = _block1a_cfg(a=True, b=False)
        strong_violations = []
        rfc_violations = []
        inspected_rfc = 0
        for seed in _BLOCK1A_SEEDS:
            inp = _block1a_fixture(seed)
            res = compute_zigzag_filter(cfg=cfg, **inp)
            for lg in res.legs:
                lhs = (int(lg.pre_confirm_arm_bar) != -1)
                rhs = bool(lg.armed_by_candidate)
                if lhs != rhs:
                    strong_violations.append(
                        f"seed={seed} leg_id={lg.leg_id} "
                        f"pre_confirm_arm_bar={lg.pre_confirm_arm_bar} "
                        f"armed_by_candidate={lg.armed_by_candidate}"
                    )
                if int(lg.fired) != int(_FIRED_NONE):
                    inspected_rfc += 1
                    if lhs != rhs:
                        rfc_violations.append(
                            f"seed={seed} leg_id={lg.leg_id} "
                            f"fired={lg.fired} lhs={lhs} rhs={rhs}"
                        )

        assert not strong_violations, (
            "§8.3.10 strong form (all legs) violated — the pre-confirm "
            "flush did not set both fields atomically.\n"
            + "\n".join(strong_violations[:10])
        )
        assert not rfc_violations, (
            "§8.3.10 RFC form (fired != FIRED_NONE) violated.\n"
            + "\n".join(rfc_violations[:10])
        )
        assert inspected_rfc > 0, (
            "§8.3.10 scoped sweep inspected 0 legs with fired != "
            "FIRED_NONE; fixture pool too narrow to exercise the "
            "literal RFC scope"
        )
