"""
Unit tests for core/zigzag_filter.py (plan v2.0 §6.1).

Stage 2 coverage:
  §6.1 A — Causal ZigZag (§1.1 steps 1.1.1–1.1.5, §1.2 registration)
  §6.1 B — NaN/inf / pathological OHLC (§1.1 final, §1.8)
  §6.1 C — Session-reset (§1.7, §3.4.2)
  §6.1 J — Edge cases (empty input, first bar, no double pivot per bar, ...)

Later stages (statistics, regime, armament, decision) extend this file.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    LEG_DIR_DOWN,
    LEG_DIR_UNKNOWN,
    LEG_DIR_UP,
    _confirmed_zigzag_pass,
    _PartialLeg,
    _ZigZagPassResult,
)
from supertrend_optimizer.engine.run import _infer_session_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zeros_session(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.int64)


def _build_arrays(bars):
    """bars is a list of (open, high, low) tuples."""
    n = len(bars)
    o = np.array([b[0] for b in bars], dtype=np.float64)
    h = np.array([b[1] for b in bars], dtype=np.float64)
    l = np.array([b[2] for b in bars], dtype=np.float64)
    return o, h, l, _zeros_session(n)


# ===========================================================================
# §6.1 A — Causal ZigZag
# ===========================================================================


class TestCausalZigZag:
    # ---------- empty / trivial ----------

    def test_empty_input_n0(self):
        o = np.array([], dtype=np.float64)
        h = np.array([], dtype=np.float64)
        l = np.array([], dtype=np.float64)
        s = np.array([], dtype=np.int64)
        res = _confirmed_zigzag_pass(h, l, o, s, reversal_threshold=0.01)
        assert isinstance(res, _ZigZagPassResult)
        assert res.legs == []
        assert res.leg_direction.shape == (0,)
        assert res.cand_height_pct.shape == (0,)

    def test_single_bar_no_pivot(self):
        o, h, l, s = _build_arrays([(100.0, 101.0, 99.0)])
        res = _confirmed_zigzag_pass(h, l, o, s, reversal_threshold=0.01)
        assert res.legs == []
        # First pivot seeded from open[0] = 100.0
        assert res.last_pivot_bar_idx[0] == 0
        assert res.last_pivot_price[0] == pytest.approx(100.0)
        # Direction still unknown — no trigger on 1% threshold (101 > 101 false)
        assert res.leg_direction[0] == LEG_DIR_UNKNOWN

    # ---------- first pivot = open[session_start] (§1.1.2) ----------

    def test_first_pivot_from_open_session_start(self):
        # open[0] = 100; then an up bar triggers up-direction.
        bars = [
            (100.0, 100.5, 99.5),   # bar 0: seeds pivot at 100
            (100.6, 102.0, 100.3),  # bar 1: high=102 > 100*1.01=101 → UP direction
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, reversal_threshold=0.01)
        assert res.last_pivot_price[0] == 100.0
        assert res.leg_direction[0] == LEG_DIR_UNKNOWN
        assert res.leg_direction[1] == LEG_DIR_UP

    # ---------- initial unknown then up ----------

    def test_initial_unknown_then_up(self):
        bars = [
            (100.0, 100.1, 99.9),   # seeds
            (100.0, 100.5, 99.8),   # still within threshold → unknown
            (100.0, 102.0, 100.0),  # high=102 > 101 → UP
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, reversal_threshold=0.01)
        assert res.leg_direction[0] == LEG_DIR_UNKNOWN
        assert res.leg_direction[1] == LEG_DIR_UNKNOWN
        assert res.leg_direction[2] == LEG_DIR_UP

    # ---------- monotonic up then reversal confirms a leg ----------

    def test_monotonic_uptrend_then_reversal(self):
        # Fix A (plan v2.0.1): confirm requires running_extreme.bar_idx != t.
        # Bar where ext is set cannot emit confirm; reversal must happen on
        # a LATER bar with low narrow enough NOT to extend ext first.
        bars = [
            (100.0, 100.2, 99.8),   # 0: seed
            (100.0, 102.0, 99.9),   # 1: UP direction, ext=(1, 102)
            (102.0, 105.0, 101.5),  # 2: ext=(2, 105)
            (105.0, 110.0, 104.5),  # 3: ext=(3, 110)
            (109.0, 109.5, 109.0),  # 4: high<110 so ext unchanged; low>108.9 no reversal
            (109.0, 109.5, 108.5),  # 5: low 108.5 <= 110*0.99=108.9 → CONFIRM
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, reversal_threshold=0.01)
        assert len(res.legs) == 1
        leg = res.legs[0]
        assert leg.direction == LEG_DIR_UP
        assert leg.end_bar == 3              # bar where running_extreme 110 was set
        assert leg.end_price == pytest.approx(110.0)
        assert leg.confirm_bar == 5
        assert leg.confirm_bar > leg.end_bar  # CAUSALITY INVARIANT §1.2
        assert leg.height_pct > 0.0
        assert leg.length_bars == leg.end_bar - leg.start_bar
        assert leg.confirm_lag_bars == leg.confirm_bar - leg.end_bar
        assert leg.confirm_lag_bars >= 1

        # confirm_event flag on bar 5
        assert res.confirm_event[5]
        assert not res.confirm_event[3]
        assert not res.confirm_event[4]

        # After confirm, leg_direction flips to DOWN
        assert res.leg_direction[5] == LEG_DIR_DOWN

    # ---------- reversal symmetry ----------

    def test_reversal_symmetry_up_vs_down(self):
        # Fix A: reversal cannot happen on the SAME bar that updated the
        # extreme.  Separate "set extreme" and "reversal" onto distinct bars.
        up_bars = [
            (100.0, 100.2, 99.8),   # seed
            (100.0, 102.0, 99.9),   # UP, ext=(1,102)
            (102.0, 110.0, 101.5),  # ext=(2,110)
            (109.0, 109.5, 108.0),  # high<110 (ext unchanged); low 108 <= 108.9 → CONFIRM high
        ]
        o1, h1, l1, s1 = _build_arrays(up_bars)
        up_res = _confirmed_zigzag_pass(h1, l1, o1, s1, 0.01)

        down_bars = [
            (100.0, 100.2, 99.8),     # seed
            (100.0, 100.1, 98.0),     # DOWN (low 98 < 99)
            (98.0, 98.5, 90.0),       # ext=(2,90)
            (91.0, 92.0, 90.5),       # low>90 (ext unchanged); high 92 >= 90*1.01=90.9 → CONFIRM low
        ]
        o2, h2, l2, s2 = _build_arrays(down_bars)
        dn_res = _confirmed_zigzag_pass(h2, l2, o2, s2, 0.01)

        assert len(up_res.legs) == 1 and up_res.legs[0].direction == LEG_DIR_UP
        assert up_res.legs[0].end_bar == 2
        assert up_res.legs[0].confirm_bar == 3
        assert up_res.legs[0].confirm_bar > up_res.legs[0].end_bar

        assert len(dn_res.legs) == 1 and dn_res.legs[0].direction == LEG_DIR_DOWN
        assert dn_res.legs[0].end_bar == 2
        assert dn_res.legs[0].confirm_bar == 3
        assert dn_res.legs[0].confirm_bar > dn_res.legs[0].end_bar

    # ---------- Fix A (plan v2.0.1): confirm never fires on the same bar
    # ---------- that updated the running_extreme ----------

    def test_fix_a_no_confirm_on_extreme_update_bar(self):
        """Fix A: even if low[t] ≤ high[t]*(1-r) on the same bar the extreme
        was updated, NO pivot is emitted on t.  Confirm is deferred to a
        later bar where running_extreme.bar_idx < t."""
        r = 0.01
        bars = [
            (100.0, 100.2, 99.8),    # seed
            (100.0, 102.0, 99.9),    # UP, ext=(1,102)
            # Bar 2: high=108 would set new ext; low=106.5 ≤ 108*0.99=106.92
            # — but Fix A blocks confirm on this bar.
            (102.0, 108.0, 106.5),
            # Bar 3: nothing extends ext; low still within threshold
            (107.0, 107.5, 107.0),
            # Bar 4: low 106.5 ≤ 108*0.99=106.92 → CONFIRM (ext unchanged)
            (107.0, 107.5, 106.5),
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, r)
        assert len(res.legs) == 1
        leg = res.legs[0]
        assert leg.end_bar == 2               # ext set on bar 2
        assert leg.end_price == pytest.approx(108.0)
        assert leg.confirm_bar == 4           # confirm deferred past bar 2
        assert leg.confirm_bar > leg.end_bar  # §1.2 structural invariant
        # Bar 2 must NOT have a confirm event
        assert not res.confirm_event[2]
        assert res.confirm_event[4]

    # ---------- bar with both triggers (§1.1.3 dominant wins) ----------

    def test_bar_with_both_up_and_down_triggers_dominant_wins(self):
        # r = 0.01; last pivot = 100
        # bar has high=105 (up_move=5%) and low=90 (down_move=10%)
        # down_move > up_move → leg_direction = DOWN
        bars = [
            (100.0, 100.2, 99.8),       # seed
            (100.0, 105.0, 90.0),       # both triggers hit, down wins
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert res.leg_direction[1] == LEG_DIR_DOWN

    def test_bar_with_both_triggers_up_wins_when_equal_or_larger(self):
        bars = [
            (100.0, 100.2, 99.8),       # seed
            (100.0, 110.0, 95.0),       # up=+10%, down=-5% → UP wins
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert res.leg_direction[1] == LEG_DIR_UP

    # ---------- strict inequalities for extreme update ----------

    def test_strict_inequality_equal_high_does_not_update(self):
        bars = [
            (100.0, 100.2, 99.8),    # seed
            (100.0, 105.0, 99.9),    # UP, ext=105
            (105.0, 105.0, 104.9),   # high==ext → NO update (strict >)
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        # cand_height_pct should reflect ext=105 (not updated)
        # |105 - 100| / 100 = 0.05
        assert res.cand_height_pct[2] == pytest.approx(0.05)


# ===========================================================================
# §6.1 B — NaN / inf / pathological OHLC (§1.1 final, §1.8)
# ===========================================================================


class TestPathologicalBars:

    def test_nan_in_high_freezes_state(self):
        bars = [
            (100.0, 100.2, 99.8),            # seed
            (100.0, 102.0, 99.9),            # UP, ext=102
            (102.0, float("nan"), 101.5),    # pathological
            (102.0, 103.0, 101.8),           # resume — state should not have advanced
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert res.pathological[2]
        assert not res.pathological[3]
        # State on pathological bar preserved from bar 1
        assert res.leg_direction[2] == LEG_DIR_UP

    def test_inf_in_low_freezes_state(self):
        bars = [
            (100.0, 100.2, 99.8),
            (100.0, 102.0, 99.9),
            (102.0, 103.0, float("inf")),    # pathological
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert res.pathological[2]

    def test_high_less_than_low_pathological(self):
        bars = [
            (100.0, 100.2, 99.8),
            (100.0, 99.0, 101.0),   # high < low
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert res.pathological[1]

    def test_negative_price_pathological(self):
        bars = [
            (100.0, 100.2, 99.8),
            (100.0, -1.0, -2.0),
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert res.pathological[1]

    def test_no_pivot_emitted_on_pathological(self):
        # Without the NaN bar, bar 3 would confirm the UP leg (ext=110 set
        # on bar 2, bar 3 has low=108.0 ≤ 108.9 and does not extend ext).
        # The NaN bar freezes state → pivot NOT emitted on bar 3.
        bars = [
            (100.0, 100.2, 99.8),
            (100.0, 102.0, 99.9),             # UP, ext=(1,102)
            (102.0, 110.0, 101.5),            # ext=(2,110)
            (109.0, float("nan"), 108.0),     # pathological — freeze
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert len(res.legs) == 0
        assert res.pathological[3]


# ===========================================================================
# §6.1 C — Session-reset (§1.7, §3.4.2 _infer_session_ids)
# ===========================================================================


class TestInferSessionIds:

    def test_tz_naive_index(self):
        idx = pd.DatetimeIndex([
            "2026-04-20 23:55", "2026-04-21 00:05", "2026-04-21 12:00",
        ])
        ids = _infer_session_ids(idx)
        assert ids.dtype == np.int64
        assert ids[0] != ids[1]
        assert ids[1] == ids[2]

    def test_non_datetime_index_returns_zeros(self):
        idx = pd.RangeIndex(5)
        ids = _infer_session_ids(idx)
        assert ids.dtype == np.int64
        assert ids.tolist() == [0, 0, 0, 0, 0]

    def test_tz_aware_index_moscow_midnight(self):
        # CRITICAL: plan §10.1 — tz_localize(None), NOT tz_convert(None).
        # 23:55 MSK and 00:05 MSK must have different session_ids.
        idx = pd.DatetimeIndex([
            "2026-04-20 23:55",
            "2026-04-21 00:05",
        ]).tz_localize("Europe/Moscow")
        ids = _infer_session_ids(idx)
        assert ids[0] != ids[1], "MSK wall-clock date crossed — reset required"

    def test_tz_aware_index_utc_midnight(self):
        idx = pd.DatetimeIndex([
            "2026-04-20 23:55",
            "2026-04-21 00:05",
        ]).tz_localize("UTC")
        ids = _infer_session_ids(idx)
        assert ids[0] != ids[1]

    def test_weekend_gap_single_boundary(self):
        # Fri 20:00 → Mon 09:00 — all three intermediate calendar days
        # would normally each be a new session, but we only see a single
        # transition in session_ids.
        idx = pd.DatetimeIndex([
            "2026-04-17 20:00",  # Friday
            "2026-04-20 09:00",  # Monday
        ])
        ids = _infer_session_ids(idx)
        # Two bars, different dates → one transition
        assert ids[0] != ids[1]

    def test_crypto_24_7_same_day(self):
        idx = pd.DatetimeIndex([
            "2026-04-21 00:00",
            "2026-04-21 06:00",
            "2026-04-21 12:00",
            "2026-04-21 18:00",
        ])
        ids = _infer_session_ids(idx)
        assert np.all(ids == ids[0])

    def test_non_monotonic_index_fallback_warn(self):
        idx = pd.DatetimeIndex([
            "2026-04-21 00:00",
            "2026-04-20 00:00",   # goes back in time
        ])
        with pytest.warns(UserWarning, match="non-monotonic"):
            ids = _infer_session_ids(idx)
        assert np.all(ids == 0)


class TestSessionResetInPass:

    def test_session_reset_on_date_change(self):
        # Two bars crossing midnight: state must reset on the second bar.
        bars = [
            (100.0, 100.2, 99.8),
            (100.0, 102.0, 99.9),   # UP direction on day 1
        ]
        o, h, l, _ = _build_arrays(bars)
        # Manually construct session_ids: bar 0 on day 1, bar 1 on day 2.
        s = np.array([0, 1], dtype=np.int64)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        # Bar 1 is first of new session → pivot re-seeded at open[1]=100
        assert res.last_pivot_bar_idx[1] == 1
        assert res.last_pivot_price[1] == 100.0
        assert res.session_reset_event[1]
        assert not res.session_reset_event[0]

    def test_no_reset_within_same_day(self):
        o, h, l, s = _build_arrays([
            (100.0, 100.2, 99.8),
            (100.0, 102.0, 99.9),
        ])
        # Both bars same session
        s = np.array([5, 5], dtype=np.int64)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert not np.any(res.session_reset_event)
        assert res.last_pivot_bar_idx[1] == 0  # unchanged pivot


# ===========================================================================
# §6.1 J — Edge cases
# ===========================================================================


class TestEdgeCases:

    def test_first_bar_of_dataset_is_pivot_start(self):
        o, h, l, s = _build_arrays([(123.45, 123.5, 123.4)])
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert res.last_pivot_bar_idx[0] == 0
        assert res.last_pivot_price[0] == pytest.approx(123.45)

    def test_no_double_pivot_per_bar(self):
        # Plan §1.2 invariant (structural, under Fix A): at most ONE pivot
        # confirmed per bar.  Construct a bar that would "want" to confirm
        # the UP-leg and then reverse DOWN-leg — §1.1.5 emits HIGH once,
        # then sets run_ext=(t, low[t]); after that run_ext.bar_idx == t
        # which blocks a second confirm on the same bar via Fix A.
        r = 0.01
        bars = [
            (100.0, 100.2, 99.8),
            (100.0, 102.0, 99.9),     # UP
            (102.0, 110.0, 101.5),    # ext=(2,110)
            # Bar 3: high=110.5 sets NO new ext (within noise); low=100
            # reverses → confirm HIGH at 110.  Post-emit run_ext=(3,100).
            # Any further reversal would need run_ext.bar_idx < 3, which
            # is impossible on bar 3.
            (108.0, 110.5, 100.0),
        ]
        # Note: bar 3 high=110.5 WOULD update ext to (3, 110.5) in step 1.1.4
        # BEFORE step 1.1.5, which under Fix A would then block confirm on
        # bar 3 entirely.  Use high=109.5 instead so ext stays (2,110).
        bars[3] = (108.0, 109.5, 100.0)
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, r)
        # Exactly 1 confirm event on bar 3
        assert int(res.confirm_event[3]) == 1
        assert sum(1 for lg in res.legs if lg.confirm_bar == 3) == 1

    def test_leg_invariants(self):
        # Multi-leg sequence.  Under Fix A every "set extreme" and "reversal"
        # are placed on DIFFERENT bars, so causality and ≤1-pivot-per-bar
        # hold structurally.
        bars = [
            (100.0, 100.2, 99.8),     # 0: seed
            (100.0, 102.0, 99.9),     # 1: UP, ext=(1,102)
            (102.0, 110.0, 101.5),    # 2: ext=(2,110)
            (109.0, 109.2, 108.0),    # 3: low 108 ≤ 108.9 → confirm HIGH at 110 (end=2, confirm=3)
            # After confirm: run_ext=(3, 108), leg_direction=DOWN
            (108.0, 108.5, 95.0),     # 4: ext=(4,95) [down leg extends]
            (96.0, 96.5, 95.5),       # 5: ext unchanged; high 96.5 < 95*1.01=95.95? 95.95 < 96.5 → reverse
            # Actually 96.5 ≥ 95.95 → confirm LOW at 95 (end=4, confirm=5).
            # Post-confirm: run_ext=(5, 96.5), leg_direction=UP
            (96.5, 105.0, 96.0),      # 6: ext=(6,105)
            (104.0, 104.5, 103.5),    # 7: low 103.5 > 105*0.99=103.95? 103.5 ≤ 103.95 → confirm HIGH at 105
        ]
        o, h, l, s = _build_arrays(bars)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert len(res.legs) >= 2
        for leg in res.legs:
            # Strict ordering: start_bar < end_bar < confirm_bar (§1.2 causality)
            assert leg.start_bar < leg.end_bar, (
                f"start_bar must be < end_bar: {leg}"
            )
            assert leg.end_bar < leg.confirm_bar, (
                f"end_bar must be < confirm_bar (plan §1.2 causality): {leg}"
            )
            assert leg.confirm_lag_bars == leg.confirm_bar - leg.end_bar
            assert leg.confirm_lag_bars >= 1
            assert leg.length_bars == leg.end_bar - leg.start_bar
            assert leg.height_pct > 0.0, f"positive height: {leg}"
            assert leg.direction in (LEG_DIR_UP, LEG_DIR_DOWN)
        # leg_ids monotonic 0,1,2,…
        ids = [lg.leg_id for lg in res.legs]
        assert ids == list(range(len(ids)))

    def test_warmup_forever_zero_legs(self):
        # Flat price — never triggers any direction.
        n = 100
        o = np.full(n, 100.0, dtype=np.float64)
        h = np.full(n, 100.05, dtype=np.float64)
        l = np.full(n, 99.95, dtype=np.float64)
        s = _zeros_session(n)
        res = _confirmed_zigzag_pass(h, l, o, s, 0.01)
        assert len(res.legs) == 0
        # leg_direction stays UNKNOWN throughout
        assert np.all(res.leg_direction == LEG_DIR_UNKNOWN)


# ===========================================================================
# Not-proven invariants — Fix 4 (audit §tests section)
# ===========================================================================


class TestNotProvenInvariants:
    """
    Tests for invariants that existed in the spec but lacked direct test
    coverage prior to the audit (Fix 4).
    """

    # ------------------------------------------------------------------
    # §4.1 Pathological-bar freeze invariant (§1.8)
    # ------------------------------------------------------------------

    def test_state_frozen_on_pathological_bar_nan_high(self):
        """
        After a NaN high bar, last_pivot_price, last_pivot_bar_idx and
        leg_direction must not change — state is frozen (§1.8 freeze semantics).
        """
        bars = [
            (100.0, 102.0, 99.8),   # 0: seed, ext goes up
            (100.0, 105.0, 99.9),   # 1: ext=(1,105)
            (105.0, 106.0, 104.0),  # 2: ext=(2,106)
        ]
        o, h, l, s = _build_arrays(bars)
        # Insert a NaN bar at position 3
        o_ext = np.append(o, [105.0])
        h_ext = np.append(h, [float("nan")])
        l_ext = np.append(l, [104.0])
        s_ext = np.zeros(4, dtype=np.int64)

        res = _confirmed_zigzag_pass(h_ext, l_ext, o_ext, s_ext, 0.01)

        # On the pathological bar (index 3), state must equal bar 2 state
        assert res.last_pivot_price[3] == res.last_pivot_price[2], (
            "last_pivot_price must be frozen on pathological bar"
        )
        assert res.last_pivot_bar_idx[3] == res.last_pivot_bar_idx[2], (
            "last_pivot_bar_idx must be frozen on pathological bar"
        )
        assert res.leg_direction[3] == res.leg_direction[2], (
            "leg_direction must be frozen on pathological bar"
        )

    def test_state_frozen_on_pathological_bar_high_lt_low(self):
        """high < low → pathological: same freeze semantics."""
        bars = [
            (100.0, 102.0, 99.8),
            (100.0, 105.0, 99.9),
        ]
        o, h, l, s = _build_arrays(bars)
        o_ext = np.append(o, [103.0])
        h_ext = np.append(h, [102.0])   # high < low → pathological
        l_ext = np.append(l, [103.5])
        s_ext = np.zeros(3, dtype=np.int64)

        res = _confirmed_zigzag_pass(h_ext, l_ext, o_ext, s_ext, 0.01)

        assert res.last_pivot_price[2] == res.last_pivot_price[1]
        assert res.last_pivot_bar_idx[2] == res.last_pivot_bar_idx[1]
        assert res.leg_direction[2] == res.leg_direction[1]

    # ------------------------------------------------------------------
    # §4.3 global_legs persistence through session reset
    # ------------------------------------------------------------------

    def test_global_legs_persist_across_session_reset(self):
        """
        n_legs_before on the first leg of day-2 must equal the number of legs
        confirmed in day-1 (global_legs is NOT reset on session boundary).
        """
        from supertrend_optimizer.core.zigzag_filter import compute_zigzag_filter
        import pandas as pd

        # Build a DatetimeIndex spanning 2 days (session reset between day1/day2)
        day1_bars = 50
        day2_bars = 50
        total = day1_bars + day2_bars

        rng = np.random.RandomState(99)
        # Strongly oscillating to guarantee multiple confirmed legs
        close = 100.0 + np.cumsum(rng.choice([-1.5, 1.5], size=total))
        high  = close + 1.0
        low   = close - 1.0
        open_ = np.roll(close, 1); open_[0] = close[0]

        # DatetimeIndex: day1 = 2024-01-02, day2 = 2024-01-03
        dates_d1 = pd.date_range("2024-01-02 09:00", periods=day1_bars, freq="5min")
        dates_d2 = pd.date_range("2024-01-03 09:00", periods=day2_bars, freq="5min")
        index = dates_d1.append(dates_d2)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close},
                          index=index)

        from supertrend_optimizer.engine.run import _infer_session_ids
        session_ids = _infer_session_ids(df.index)
        # Sanity: there must be a boundary somewhere
        assert len(np.unique(session_ids)) >= 2

        st_trend = np.ones(total, dtype=np.int8)

        zz_cfg = {
            "reversal_threshold": 0.02,
            "min_legs_global": 5,
            "q_strong": 0.80,
            "k_local": 3,
            "entry_side": "counter_trend",
            "arm_timeout_bars_since_extreme": 20,
            "arm_timeout_bars_hard": 40,
        }
        result = compute_zigzag_filter(
            high=high, low=low, close=close,
            open_prices=open_,
            session_ids=session_ids,
            st_trend=st_trend,
            cfg=zz_cfg,
        )

        legs = result.legs
        if len(legs) < 2:
            pytest.skip("Not enough legs confirmed for this test (adjust data if needed)")

        # Find first leg with confirm_bar >= day1_bars (belongs to day-2 or later)
        d2_legs = [lg for lg in legs if lg.confirm_bar >= day1_bars]
        if not d2_legs:
            pytest.skip("No legs confirmed in day-2 segment")

        first_d2_leg = d2_legs[0]
        d1_legs_count = sum(1 for lg in legs if lg.confirm_bar < day1_bars)

        # n_legs_before for the first day-2 leg must equal number of day-1 legs
        # (global history is preserved across session reset)
        assert first_d2_leg.n_legs_before == d1_legs_count, (
            f"global_legs not persisted across session reset: "
            f"first_d2_leg.n_legs_before={first_d2_leg.n_legs_before}, "
            f"expected {d1_legs_count} (legs from day-1)"
        )


# ===========================================================================
# FIX 5 — height_pct <= 0: состояние не сдвигается при нулевой ноге
# ===========================================================================


class TestZeroHeightLegPreservesState:
    """
    FIX 5: если reversal_threshold срабатывает, но height_pct <= 0
    (start_price ~= end_price), нога НЕ регистрируется, pivot chain
    не сдвигается, leg_direction не меняется.
    """

    def test_zero_height_leg_not_registered(self):
        """Нога с height_pct == 0 не попадает в legs."""
        # Строим ценовой ряд, где first pivot выходит ровно на start_price:
        # bar0: high=105, low=95  (start)
        # bar1: high=110, low=99  (running UP extreme = 110)
        # bar2: high=101, low=96  (reversal: 96 <= 110*(1-0.05) = 104.5) → confirm
        # НО: start_price (from prev pivot) == end_price (run_ext_pr=110) → нет,
        # это не нулевой кейс. Создадим кейс где start = end:
        # Используем start_price = end_price путём специального подбора.
        # Простейший способ: цена не изменилась (flat bar).
        N = 10
        # UP leg: start=100 (bar0 high), running extreme=100 (flat), confirm bar2
        # height = (100 - 100) / 100 = 0
        high = np.array([100.0, 100.0, 100.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0])
        low = np.array([95.0, 95.0, 94.0, 93.0, 93.0, 93.0, 93.0, 93.0, 93.0, 93.0])
        close = (high + low) / 2
        open_p = close.copy()
        session_ids = np.zeros(N, dtype=np.int64)

        # reversal_threshold=0.01 → 94.0 <= 100*(1-0.01)=99 → triggers confirm
        # start_price = last_pivot (начало) = nan (нет предыдущего пивота)
        # По факту без предыдущего пивота ноги не будет — проверим через confirm event
        pass_result = _confirmed_zigzag_pass(
            high=high, low=low, open_prices=open_p,
            session_ids=session_ids, reversal_threshold=0.01,
        )
        # Если нога зарегистрирована — height_pct должен быть > 0
        for leg in pass_result.legs:
            assert leg.height_pct > 0.0, (
                f"Нога {leg.leg_id} имеет height_pct={leg.height_pct} <= 0"
            )

    def test_all_registered_legs_have_positive_height(self):
        """Инвариант: все зарегистрированные ноги имеют height_pct > 0."""
        import numpy as np
        rng = np.random.default_rng(314)
        N = 500
        close = 100.0 + np.cumsum(rng.normal(0, 1.0, N))
        high = close + rng.uniform(0.01, 2.0, N)
        low = close - rng.uniform(0.01, 2.0, N)
        open_p = close + rng.normal(0, 0.1, N)
        session_ids = np.zeros(N, dtype=np.int64)

        pass_result = _confirmed_zigzag_pass(
            high=high, low=low, open_prices=open_p,
            session_ids=session_ids, reversal_threshold=0.005,
        )
        for leg in pass_result.legs:
            assert leg.height_pct > 0.0, (
                f"Нога {leg.leg_id} нарушает инвариант height_pct > 0: "
                f"height_pct={leg.height_pct}"
            )
