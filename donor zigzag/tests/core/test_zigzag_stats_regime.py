"""
Tests for ZigZag causal statistics and regime state machine
(plan v2.0.1 §6.1 D, E; core modules §1.3, §1.4, §1.4a steps 1–4).

Stage 3 coverage (subset — opus-reserved tests deferred to parent session):
  §6.1 D (statistics + causality):
    - test_local_median_nan_until_k_local
    - test_global_p80_with_single_leg_no_crash
    - test_snapshot_before_adding_leg_on_confirm_bar
    - test_global_median_p80_numpy_equivalent
    - test_broadcast_stats_causality   (bar c does NOT contain leg c)

  §6.1 E (regime machine):
    - test_regime_initial_closed
    - test_regime_open_on_strong_leg
    - test_regime_grace_does_not_close
    - test_activate_on_k_local_legs
    - test_regime_close_after_grace_local_lt_global
    - test_regime_persists_across_sessions

DEFERRED (opus-reserved by user instruction):
  - test_leg_at_t_not_in_stats_at_t_enters_at_t_plus_1  → Stage 4 parent run

DEFERRED to Stage 4 (requires reason / compute_zigzag_filter):
  - test_regime_warmup_zz_warmup
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    LEG_DIR_DOWN,
    LEG_DIR_UP,
    REGIME_CLOSED,
    REGIME_OPEN_ACTIVE,
    REGIME_OPEN_GRACE,
    _broadcast_regime_to_bars,
    _broadcast_stats_to_bars,
    _build_causal_statistics,
    _confirmed_zigzag_pass,
    _LegRegimeInfo,
    _LegStatsSnapshot,
    _PartialLeg,
    _run_regime_state_machine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_leg(leg_id: int, height_pct: float, confirm_bar: int,
            direction: int = LEG_DIR_UP) -> _PartialLeg:
    """Tiny factory — fills minimum required fields for stats / regime tests."""
    start_bar = max(0, confirm_bar - 3)
    end_bar = confirm_bar - 1
    return _PartialLeg(
        leg_id=leg_id,
        start_bar=start_bar,
        end_bar=end_bar,
        confirm_bar=confirm_bar,
        start_price=100.0,
        end_price=100.0 * (1.0 + height_pct) if direction == LEG_DIR_UP
        else 100.0 * (1.0 - height_pct),
        direction=direction,
        height_pct=float(height_pct),
        length_bars=end_bar - start_bar,
        confirm_lag_bars=1,
    )


# ===========================================================================
# §6.1 D — Statistics & causality
# ===========================================================================


class TestCausalStatistics:

    def test_local_median_nan_until_k_local(self):
        # 4 legs, k_local=5 → local_median is NaN for the snapshot of the
        # 1st, 2nd, 3rd, 4th leg (n_before < 5) and for the 5th (n_before==4<5).
        legs = [_mk_leg(i, 0.01 + 0.001 * i, confirm_bar=10 + i) for i in range(6)]
        snaps = _build_causal_statistics(legs, k_local=5)
        assert len(snaps) == 6
        # leg 0: n_before = 0  → NaN
        # leg 1: n_before = 1  → NaN
        # ...
        # leg 4: n_before = 4  → NaN
        # leg 5: n_before = 5  → local_median is defined
        for i in range(5):
            assert math.isnan(snaps[i].local_median), (
                f"leg {i}: n_before={snaps[i].n_legs_before} should yield NaN"
            )
        assert not math.isnan(snaps[5].local_median)

    def test_global_p80_with_single_leg_no_crash(self):
        # Only one leg confirmed so far → p80 of 1-element set is that element.
        legs = [_mk_leg(0, 0.02, confirm_bar=5), _mk_leg(1, 0.04, confirm_bar=10)]
        snaps = _build_causal_statistics(legs, k_local=5)
        # leg 0 snapshot: n_before = 0 → NaN
        assert math.isnan(snaps[0].global_median)
        assert math.isnan(snaps[0].global_p80)
        # leg 1 snapshot: n_before = 1 → both equal to 0.02
        assert snaps[1].global_median == pytest.approx(0.02)
        assert snaps[1].global_p80 == pytest.approx(0.02)

    def test_snapshot_before_adding_leg_on_confirm_bar(self):
        # Legs with ascending heights; snapshot on leg i must reflect the
        # median/p80 of legs [0..i-1] — i.e. BEFORE leg i is added.
        heights = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=5)

        # snapshot for leg 5: before-set = heights[0..4] = [0.01..0.05]
        assert snaps[5].n_legs_before == 5
        # numpy comparison for median/p80
        ref = np.array(heights[:5])
        assert snaps[5].global_median == pytest.approx(float(np.median(ref)))
        assert snaps[5].global_p80 == pytest.approx(
            float(np.quantile(ref, 0.80, method="linear"))
        )

    def test_global_median_p80_numpy_equivalent(self):
        # Random-ish set; compare our percentile helper to numpy's linear.
        heights = [0.005, 0.012, 0.021, 0.008, 0.030, 0.015, 0.050, 0.003, 0.011]
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=5)
        # Check snapshot on leg i against numpy over heights[:i]
        for i in range(1, len(heights)):
            ref = np.array(heights[:i])
            assert snaps[i].global_median == pytest.approx(float(np.median(ref)))
            assert snaps[i].global_p80 == pytest.approx(
                float(np.quantile(ref, 0.80, method="linear"))
            )

    def test_broadcast_stats_causality(self):
        # Leg with confirm_bar = 10 must NOT be in the stats on bar 10,
        # but MUST be in stats on bar 11.
        legs = [_mk_leg(0, 0.02, confirm_bar=10)]
        g_med, g_p80, l_med, n_before = _broadcast_stats_to_bars(
            legs, _build_causal_statistics(legs, k_local=5),
            n_bars=15, k_local=5,
        )
        # Before and on confirm_bar: leg not counted
        assert n_before[0] == 0
        assert n_before[10] == 0
        assert math.isnan(g_med[10])
        assert math.isnan(g_p80[10])
        # Strictly after confirm_bar: leg counted
        assert n_before[11] == 1
        assert g_med[11] == pytest.approx(0.02)
        assert g_p80[11] == pytest.approx(0.02)


# ===========================================================================
# §6.1 E — Regime state machine (subset not requiring reason/)
# ===========================================================================


class TestRegimeStateMachine:

    def test_regime_initial_closed(self):
        # Empty leg set → no regime infos; broadcast gives all-CLOSED.
        state_arr, counter_arr = _broadcast_regime_to_bars([], [], n_bars=10)
        assert np.all(state_arr == REGIME_CLOSED)
        assert np.all(counter_arr == 0)

    def test_regime_open_on_strong_leg(self):
        # Plan §1.4: "closed → open_grace if H_pct >= global_p80".  This fires
        # as soon as p80 is defined (n_before >= 1).  To have a first-leg
        # trigger control, we build a STRICTLY DECREASING bootstrap followed
        # by a strong spike: each bootstrap leg is smaller than the previous
        # p80, so no open fires until the spike.
        heights = [0.050, 0.040, 0.030, 0.020, 0.010, 0.500]
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=5)
        infos = _run_regime_state_machine(legs, snaps, k_local=5)
        assert len(infos) == 6
        # leg 0: n_before=0 → p80 NaN → no open.
        assert not infos[0].is_strong
        assert infos[0].state_at_confirm == REGIME_CLOSED
        # legs 1..4: each h < p80 of prior set → no open.
        for i in range(1, 5):
            assert not infos[i].opened_regime, f"leg {i} should not open"
            assert infos[i].state_at_confirm == REGIME_CLOSED
        # leg 5 (h=0.5): much larger than p80 of prior set → open_grace.
        assert infos[5].is_strong
        assert infos[5].state_at_confirm == REGIME_OPEN_GRACE
        assert infos[5].opened_regime
        assert infos[5].n_legs_since_regime_open == 1  # trigger counts as 1

    def test_regime_grace_does_not_close(self):
        # Once in grace, weak legs should NOT close the regime (plan §1.4:
        # open_grace → closed is impossible — grace protects).
        heights = [0.050, 0.040, 0.030, 0.020, 0.010,   # decreasing bootstrap
                   0.500,   # → open_grace (trigger)
                   0.001, 0.001]   # weak legs during grace
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=5)
        infos = _run_regime_state_machine(legs, snaps, k_local=5)
        # Leg 5: opens grace (n_since=1)
        assert infos[5].state_at_confirm == REGIME_OPEN_GRACE
        assert infos[5].n_legs_since_regime_open == 1
        # Legs 6, 7: still grace (counter grows, state unchanged)
        assert infos[6].state_at_confirm == REGIME_OPEN_GRACE
        assert infos[6].n_legs_since_regime_open == 2
        assert infos[7].state_at_confirm == REGIME_OPEN_GRACE
        assert infos[7].n_legs_since_regime_open == 3

    def test_activate_on_k_local_legs(self):
        # After K_local legs since regime-open, state becomes ACTIVE.  We use
        # k_local=3 for brevity.  Decreasing bootstrap prevents earlier opens.
        heights = [0.050, 0.040, 0.030, 0.020, 0.010,
                   0.500,   # open
                   0.020, 0.020]
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=3)
        infos = _run_regime_state_machine(legs, snaps, k_local=3)
        # Leg 5: opens grace (n_since=1)
        assert infos[5].state_at_confirm == REGIME_OPEN_GRACE
        assert infos[5].n_legs_since_regime_open == 1
        # Leg 6: grace (n_since=2)
        assert infos[6].state_at_confirm == REGIME_OPEN_GRACE
        assert infos[6].n_legs_since_regime_open == 2
        # Leg 7: counter reaches 3 (k_local) → ACTIVATE
        assert infos[7].state_at_confirm == REGIME_OPEN_ACTIVE
        assert infos[7].n_legs_since_regime_open == 3

    def test_regime_close_after_grace_local_lt_global(self):
        # Active state closes when local_median (last k_local legs) drops
        # below global_median (expanding).  We build: decreasing bootstrap,
        # strong trigger, several strong legs to reach ACTIVE, then very
        # weak legs to drive local_median below global_median.
        heights = [
            0.050, 0.040, 0.030, 0.020, 0.010,   # decreasing bootstrap
            0.500,                                # open
            0.500, 0.500,                         # reach active at k_local=3
            0.001, 0.001, 0.001,                  # very weak → local < global
        ]
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=3)
        infos = _run_regime_state_machine(legs, snaps, k_local=3)
        assert infos[7].state_at_confirm == REGIME_OPEN_ACTIVE
        closed_found = any(info.closed_regime for info in infos[8:])
        assert closed_found, (
            f"Expected closed_regime transition among legs 8..10, "
            f"got {[i.state_at_confirm for i in infos[8:]]}"
        )
        for info in infos[8:]:
            if info.closed_regime:
                assert info.state_at_confirm == REGIME_CLOSED
                assert info.n_legs_since_regime_open == 0
                break

    def test_regime_persists_across_sessions(self):
        # Regime state machine is session-agnostic: _run_regime_state_machine
        # only sees legs in confirm order.  Once a leg opens the regime,
        # the state propagates to subsequent legs.  Session_reset affects
        # operational state (armed, etc.) but never regime_state (§1.4
        # invariant, §G.1.4).
        heights = [0.050, 0.040, 0.030, 0.020, 0.010, 0.500,   # open on leg 5
                   0.020, 0.020, 0.020, 0.020, 0.020]          # post-open legs
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=5)
        infos = _run_regime_state_machine(legs, snaps, k_local=5)
        # Leg 5 opens regime.
        assert infos[5].state_at_confirm == REGIME_OPEN_GRACE
        # None of the later legs close it back to CLOSED.
        for info in infos[6:]:
            assert info.state_at_confirm in (REGIME_OPEN_GRACE, REGIME_OPEN_ACTIVE)

    def test_nan_safe_no_close_on_nan(self):
        # If local_median is NaN (warmup), close check must NOT fire.
        heights = [0.05, 0.10]   # only 2 legs
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=5)
        infos = _run_regime_state_machine(legs, snaps, k_local=5)
        # Neither leg should close (can't be, state starts CLOSED anyway).
        assert not any(info.closed_regime for info in infos)


# ===========================================================================
# Broadcast to bars (per-bar regime_state and n_legs_since_regime_open)
# ===========================================================================


class TestBroadcastRegime:

    def test_regime_state_propagates_to_next_bars(self):
        heights = [0.050, 0.040, 0.030, 0.020, 0.010, 0.500]
        legs = [_mk_leg(i, h, confirm_bar=10 + i) for i, h in enumerate(heights)]
        snaps = _build_causal_statistics(legs, k_local=5)
        infos = _run_regime_state_machine(legs, snaps, k_local=5)
        state_arr, counter_arr = _broadcast_regime_to_bars(legs, infos, n_bars=30)
        # Before first confirm: CLOSED
        assert state_arr[0] == REGIME_CLOSED
        # After leg 5 (confirm_bar=15): GRACE (confirm_bar <= t triggers)
        assert state_arr[15] == REGIME_OPEN_GRACE
        assert state_arr[20] == REGIME_OPEN_GRACE
        # counter = 1 from leg 5 confirm onward (no later legs)
        assert counter_arr[15] == 1
        assert counter_arr[29] == 1


# ===========================================================================
# §6.1 D' — q_strong parametrization (Fix 2, audit §3.4 / plan §2)
# ===========================================================================


class TestQStrongParameterization:
    """
    Verifies that q_strong is wired through to _build_causal_statistics and
    _broadcast_stats_to_bars so that global_p80 actually changes when q_strong
    changes (Fix 2: was hardcoded as 0.80 in both functions).

    These tests would have failed before Fix 2, proving the bug.
    """

    # Known heights with clear percentile spread.
    _HEIGHTS = [0.010, 0.020, 0.030, 0.040, 0.050,
                0.060, 0.070, 0.080, 0.090, 0.100]

    def _legs(self):
        return [_mk_leg(i, h, confirm_bar=10 + i)
                for i, h in enumerate(self._HEIGHTS)]

    def test_build_stats_q50_vs_q95_differ(self):
        legs = self._legs()
        snaps_50 = _build_causal_statistics(legs, k_local=5, q_strong=0.50)
        snaps_95 = _build_causal_statistics(legs, k_local=5, q_strong=0.95)
        # On the last snapshot (all 9 previous heights visible):
        # p50 ≈ median(0.01..0.09) = 0.05; p95 = 0.095*-ish
        assert snaps_50[-1].global_p80 < snaps_95[-1].global_p80, (
            "global_p80 must be larger for q_strong=0.95 than q_strong=0.50"
        )

    def test_build_stats_q_matches_numpy(self):
        legs = self._legs()
        for q in (0.50, 0.70, 0.90):
            snaps = _build_causal_statistics(legs, k_local=5, q_strong=q)
            # snapshot at index i sees heights[:i]
            for i in range(1, len(self._HEIGHTS)):
                ref = np.array(self._HEIGHTS[:i])
                expected = float(np.quantile(ref, q, method="linear"))
                assert snaps[i].global_p80 == pytest.approx(expected, rel=1e-9), (
                    f"q={q}, leg={i}: got {snaps[i].global_p80}, expected {expected}"
                )

    def test_broadcast_q50_vs_q95_differ(self):
        legs = self._legs()
        snaps = _build_causal_statistics(legs, k_local=5, q_strong=0.80)
        _, p80_50, _, _ = _broadcast_stats_to_bars(
            legs, snaps, n_bars=30, k_local=5, q_strong=0.50)
        _, p80_95, _, _ = _broadcast_stats_to_bars(
            legs, snaps, n_bars=30, k_local=5, q_strong=0.95)
        # After all legs are confirmed (bar 20+), p50 < p95.
        assert p80_50[25] < p80_95[25], (
            "broadcast: global_p80 must be larger for q_strong=0.95 than 0.50"
        )

    def test_broadcast_q_matches_numpy(self):
        legs = self._legs()
        snaps = _build_causal_statistics(legs, k_local=5, q_strong=0.80)
        for q in (0.50, 0.80, 0.95):
            _, g_p80, _, _ = _broadcast_stats_to_bars(
                legs, snaps, n_bars=30, k_local=5, q_strong=q)
            # Bar 25: all 10 legs confirmed (last confirm_bar=19 < 25).
            ref = np.array(self._HEIGHTS)
            expected = float(np.quantile(ref, q, method="linear"))
            assert g_p80[25] == pytest.approx(expected, rel=1e-9), (
                f"broadcast bar 25, q={q}: got {g_p80[25]}, expected {expected}"
            )

    def test_default_q_is_0_80_backwards_compat(self):
        """Omitting q_strong must give same result as q_strong=0.80."""
        legs = self._legs()
        snaps_default = _build_causal_statistics(legs, k_local=5)
        snaps_explicit = _build_causal_statistics(legs, k_local=5, q_strong=0.80)
        for i, (sd, se) in enumerate(zip(snaps_default, snaps_explicit)):
            vd, ve = sd.global_p80, se.global_p80
            if math.isnan(vd) and math.isnan(ve):
                continue  # both NaN (first leg, no history yet) — equal
            assert vd == pytest.approx(ve), (
                f"Default q_strong changed backwards-compat at leg {i}: "
                f"default={vd}, explicit={ve}"
            )
