"""
WP4 — Unit tests: causal ZigZag per-bar engine.

Covers exactly the WP4 contract:

- ``ZigZagPerBar`` dataclass shape;
- ``compute_zigzag_per_bar`` shares the close-only ZigZag formula with
  ``detect_confirmed_legs_close_only`` (one helper, three WPs);
- per-bar arrays match the shared fixture's confirmed legs / confirm bars /
  heights;
- ``candidate_height_pct[t]`` is a fraction of the last confirmed pivot
  price and is stable on identical consecutive closes;
- ``confirm_event[t] = 1`` only on confirm bars;
- ``local_median_N`` is unavailable until ``local_window`` confirmed legs
  accumulate, then becomes available on / after confirm bars;
- ``local_median_N`` uses the causal slice-local confirmed-leg history,
  NOT a lifecycle-gated subset (WP4 has no FSM);
- close-only anti-drift: no ``high`` / ``low`` / ``hlc3`` / ``ohlc4`` in
  the formula path;
- WP3 confirmed-leg helper output stays bit-identical (same shared pass).

WP4 deliberately does NOT touch FSM, ST flip detection, ``positions``
builder, ``apply(...)``, runtime / WF integration — those land in WP5+.

Spec reference:  Appendix A v1.1 §3.1..§3.4, §5, §6, §15.1, §15.7, §17.3, §17.4.
Plan reference:  WP4, §3.3.
"""

from __future__ import annotations

import inspect
import math
import re
from pathlib import Path
from typing import List

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ConfirmedLeg,
    ZigZagPerBar,
    compute_zigzag_per_bar,
    detect_confirmed_legs_close_only,
)
from supertrend_optimizer.utils.exceptions import ConfigError
from wf_grid.tests.zigzag_st_close_only_fixture import (
    MANY_LEG_SAWTOOTH,
    SIMPLE_ZIGZAG,
    _FEW_LEGS_CLOSE,
    _FEW_LEGS_R,
    _FLAT_CLOSE,
    _FLAT_R,
    CloseOnlyFixture,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expected_confirm_bars(fix: CloseOnlyFixture) -> List[int]:
    return [leg.confirm_bar for leg in fix.expected_legs]


def _per_bar_for_fixture(
    fix: CloseOnlyFixture, *, local_window: int
) -> ZigZagPerBar:
    return compute_zigzag_per_bar(
        fix.close, reversal_threshold=fix.reversal_threshold,
        local_window=local_window,
    )


def _expected_candidate_height_simple_zigzag() -> np.ndarray:
    """Manually-traced candidate_height_pct for SIMPLE_ZIGZAG (r=0.02).

    Walk-through (end-of-bar snapshot, post-confirm):
        t=0..1  bootstrap, direction=UNKNOWN  -> NaN
        t=2     direction=UP, last_pivot=(0,100), run_ext=(2,102)
                cand = (102-100)/100 = 0.02
        t=3     confirm leg 0; new pivot=(2,102), run_ext=(3,99)
                cand = (102-99)/102 = 3/102
        t=4     hold;   cand = 3/102
        t=5     confirm leg 1; pivot=(3,99), run_ext=(5,105)
                cand = (105-99)/99 = 6/99
        t=6     confirm leg 2; pivot=(5,105), run_ext=(6,102)
                cand = (105-102)/105 = 3/105
        t=7     confirm leg 3; pivot=(6,102), run_ext=(7,108)
                cand = (108-102)/102 = 6/102
        t=8     confirm leg 4; pivot=(7,108), run_ext=(8,103)
                cand = (108-103)/108 = 5/108
    """
    return np.array(
        [
            np.nan,
            np.nan,
            2.0 / 100.0,
            3.0 / 102.0,
            3.0 / 102.0,
            6.0 / 99.0,
            3.0 / 105.0,
            6.0 / 102.0,
            5.0 / 108.0,
        ],
        dtype=np.float64,
    )


# ---------------------------------------------------------------------------
# Dataclass shape — fields and order match the WP4 plan §3.2 contract.
# ---------------------------------------------------------------------------

class TestZigZagPerBarDataclass:

    def test_zigzag_per_bar_fields(self):
        fields = list(ZigZagPerBar.__dataclass_fields__.keys())
        assert fields == [
            "candidate_height_pct",
            "confirm_event",
            "confirmed_leg_idx_at_t",
            "last_confirmed_leg_height_pct",
            "local_median_N",
            "local_median_available",
        ]

    def test_zigzag_per_bar_is_frozen(self):
        per_bar = compute_zigzag_per_bar(
            SIMPLE_ZIGZAG.close,
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            local_window=3,
        )
        with pytest.raises(Exception):
            per_bar.candidate_height_pct = np.zeros(1)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Shared close-only ZigZag formula — WP3 helper and WP4 engine see the same
# legs / confirm bars / heights on the shared fixture (plan §3.3 step 7).
# ---------------------------------------------------------------------------

class TestSharedCloseOnlyFormula:

    @pytest.mark.parametrize(
        "fix",
        [SIMPLE_ZIGZAG, MANY_LEG_SAWTOOTH],
        ids=lambda f: f.name,
    )
    def test_per_bar_confirm_bars_match_fixture(self, fix: CloseOnlyFixture):
        per_bar = _per_bar_for_fixture(fix, local_window=3)
        confirm_bars = np.flatnonzero(per_bar.confirm_event == 1).tolist()
        assert confirm_bars == _expected_confirm_bars(fix)

    @pytest.mark.parametrize(
        "fix",
        [SIMPLE_ZIGZAG, MANY_LEG_SAWTOOTH],
        ids=lambda f: f.name,
    )
    def test_per_bar_legs_match_wp3_helper(self, fix: CloseOnlyFixture):
        wp3_legs = detect_confirmed_legs_close_only(
            fix.close, fix.reversal_threshold
        )
        per_bar = _per_bar_for_fixture(fix, local_window=3)

        last_idx = int(per_bar.confirmed_leg_idx_at_t[-1])
        assert last_idx + 1 == len(wp3_legs) == len(fix.expected_legs)

        # last_confirmed_leg_height_pct on each confirm bar matches the
        # corresponding leg height (round-tripped through the per-bar arrays).
        for k, leg in enumerate(fix.expected_legs):
            assert per_bar.confirm_event[leg.confirm_bar] == 1
            assert per_bar.confirmed_leg_idx_at_t[leg.confirm_bar] == k
            assert per_bar.last_confirmed_leg_height_pct[leg.confirm_bar] == \
                pytest.approx(leg.height_pct, rel=0, abs=1e-15)
            assert wp3_legs[k] == ConfirmedLeg(
                start_bar=leg.start_bar,
                end_bar=leg.end_bar,
                confirm_bar=leg.confirm_bar,
                start_price=leg.start_price,
                end_price=leg.end_price,
                direction=leg.direction,
                height_pct=leg.height_pct,
            )

    def test_simple_zigzag_candidate_height_pct(self):
        """Per-bar candidate_height_pct matches the manually-traced
        end-of-bar fractions on SIMPLE_ZIGZAG.
        """
        per_bar = _per_bar_for_fixture(SIMPLE_ZIGZAG, local_window=3)
        expected = _expected_candidate_height_simple_zigzag()

        # Bars 0..1 are bootstrap → NaN.
        assert np.isnan(per_bar.candidate_height_pct[0])
        assert np.isnan(per_bar.candidate_height_pct[1])
        # Bars 2..n-1 must match exactly.
        np.testing.assert_allclose(
            per_bar.candidate_height_pct[2:],
            expected[2:],
            rtol=0,
            atol=1e-15,
        )

    def test_simple_zigzag_confirmed_leg_idx_at_t(self):
        per_bar = _per_bar_for_fixture(SIMPLE_ZIGZAG, local_window=3)
        # Walking SIMPLE_ZIGZAG: legs confirm at t=3,5,6,7,8.
        # confirmed_leg_idx_at_t reflects the most recent leg whose
        # confirm_bar <= t (or -1 before any).
        expected = np.array([-1, -1, -1, 0, 0, 1, 2, 3, 4], dtype=np.int64)
        np.testing.assert_array_equal(per_bar.confirmed_leg_idx_at_t, expected)

    def test_simple_zigzag_last_confirmed_height_advances_only_on_confirm(self):
        per_bar = _per_bar_for_fixture(SIMPLE_ZIGZAG, local_window=3)
        # Pre-first-confirm bars are NaN.
        for t in range(3):
            assert np.isnan(per_bar.last_confirmed_leg_height_pct[t])
        # From t=3 the value monotonically advances exactly on confirm bars
        # and is otherwise carried forward unchanged.
        prev = float("nan")
        legs = SIMPLE_ZIGZAG.expected_legs
        leg_iter = iter(legs)
        next_leg = next(leg_iter, None)
        for t in range(3, len(SIMPLE_ZIGZAG.close)):
            value = per_bar.last_confirmed_leg_height_pct[t]
            if next_leg is not None and t == next_leg.confirm_bar:
                assert value == pytest.approx(next_leg.height_pct,
                                              rel=0, abs=1e-15)
                prev = value
                next_leg = next(leg_iter, None)
            else:
                assert value == pytest.approx(prev, rel=0, abs=1e-15)


# ---------------------------------------------------------------------------
# confirm_event semantics (Appendix A v1.1 §3.4 / plan §3.3 step 3).
# ---------------------------------------------------------------------------

class TestConfirmEvent:

    @pytest.mark.parametrize(
        "fix",
        [SIMPLE_ZIGZAG, MANY_LEG_SAWTOOTH],
        ids=lambda f: f.name,
    )
    def test_confirm_event_is_one_only_on_confirm_bars(
        self, fix: CloseOnlyFixture
    ):
        per_bar = _per_bar_for_fixture(fix, local_window=3)
        assert per_bar.confirm_event.dtype == np.int8
        ones = set(np.flatnonzero(per_bar.confirm_event == 1).tolist())
        assert ones == set(_expected_confirm_bars(fix))
        # All other bars are exactly 0 (no negative / spurious values).
        zero_mask = per_bar.confirm_event != 1
        assert np.all(per_bar.confirm_event[zero_mask] == 0)

    def test_no_confirm_event_on_flat_close(self):
        per_bar = compute_zigzag_per_bar(
            _FLAT_CLOSE, reversal_threshold=_FLAT_R, local_window=3
        )
        assert per_bar.confirm_event.sum() == 0
        assert np.all(per_bar.confirmed_leg_idx_at_t == -1)
        assert np.all(np.isnan(per_bar.last_confirmed_leg_height_pct))


# ---------------------------------------------------------------------------
# local_median_N — causal slice-local rolling median over confirmed legs.
# Spec §6, §15.1, §15.7;  plan §3.3 steps 5 & 6.
# ---------------------------------------------------------------------------

class TestLocalMedianN:

    def test_unavailable_until_local_window_confirmed_legs(self):
        """SIMPLE_ZIGZAG has 5 confirmed legs; with local_window=5 the
        median is unavailable on every bar except the 5th confirm bar.
        """
        per_bar = _per_bar_for_fixture(SIMPLE_ZIGZAG, local_window=5)
        # First 5 legs confirm at t=3,5,6,7,8 — so only t=8 has >=5 legs.
        for t in range(8):
            assert not per_bar.local_median_available[t]
            assert np.isnan(per_bar.local_median_N[t])
        assert per_bar.local_median_available[8]
        expected_median = float(
            np.median(SIMPLE_ZIGZAG.expected_heights_pct)
        )
        assert per_bar.local_median_N[8] == pytest.approx(
            expected_median, rel=0, abs=1e-15
        )

    def test_available_on_confirm_bar_with_sufficient_history(self):
        """With local_window=3 SIMPLE_ZIGZAG becomes available exactly on
        the third confirm bar (t=6) and stays available afterwards.
        """
        per_bar = _per_bar_for_fixture(SIMPLE_ZIGZAG, local_window=3)
        # t<6: <3 confirmed legs → unavailable.
        for t in range(6):
            assert not per_bar.local_median_available[t]
            assert np.isnan(per_bar.local_median_N[t])
        # t>=6: available, equals median of last 3 confirmed-leg heights.
        heights = [leg.height_pct for leg in SIMPLE_ZIGZAG.expected_legs]
        for t, expected_idx in [(6, 2), (7, 3), (8, 4)]:
            window = heights[expected_idx - 2 : expected_idx + 1]
            assert per_bar.local_median_available[t]
            assert per_bar.local_median_N[t] == pytest.approx(
                float(np.median(window)), rel=0, abs=1e-15
            )

    def test_local_window_one_available_immediately_on_first_confirm(self):
        per_bar = _per_bar_for_fixture(SIMPLE_ZIGZAG, local_window=1)
        first_confirm_bar = SIMPLE_ZIGZAG.expected_legs[0].confirm_bar
        for t in range(first_confirm_bar):
            assert not per_bar.local_median_available[t]
        for t in range(first_confirm_bar, len(SIMPLE_ZIGZAG.close)):
            assert per_bar.local_median_available[t]
            # With N=1 median equals the most recent confirmed-leg height.
            idx = int(per_bar.confirmed_leg_idx_at_t[t])
            assert per_bar.local_median_N[t] == pytest.approx(
                SIMPLE_ZIGZAG.expected_legs[idx].height_pct,
                rel=0, abs=1e-15,
            )

    def test_local_median_N_is_slice_local_not_lifecycle_gated(self):
        """The running median over the last ``N=local_window`` confirmed
        legs uses the FULL slice-local confirmed-leg history — every leg
        whose ``confirm_bar <= t`` is eligible.

        WP4 has no notion of FSM lifecycle / freeze — this is the intended
        behaviour per Appendix A v1.1 §6 & §15.1.  Verified by manually
        recomputing the median from the leg list at the latest confirm bar.
        """
        fix = MANY_LEG_SAWTOOTH
        local_window = 5
        per_bar = _per_bar_for_fixture(fix, local_window=local_window)
        heights = [leg.height_pct for leg in fix.expected_legs]

        for k, leg in enumerate(fix.expected_legs):
            t = leg.confirm_bar
            if k + 1 < local_window:
                assert not per_bar.local_median_available[t]
                continue
            # Window covers legs [k - N + 1 ... k] — slice-local, full
            # history visible to the rolling median.
            window = heights[k - local_window + 1 : k + 1]
            assert per_bar.local_median_available[t]
            assert per_bar.local_median_N[t] == pytest.approx(
                float(np.median(window)), rel=0, abs=1e-15
            )

    def test_local_median_N_does_not_change_between_confirm_bars(self):
        """Between two consecutive confirm bars the rolling median is
        constant — the confirmed-leg history does not advance, so neither
        does the median.
        """
        fix = MANY_LEG_SAWTOOTH
        per_bar = _per_bar_for_fixture(fix, local_window=3)
        for k in range(len(fix.expected_legs) - 1):
            t_now = fix.expected_legs[k].confirm_bar
            t_next = fix.expected_legs[k + 1].confirm_bar
            if not per_bar.local_median_available[t_now]:
                continue
            for t in range(t_now, t_next):
                assert per_bar.local_median_available[t] == \
                    per_bar.local_median_available[t_now]
                if per_bar.local_median_available[t]:
                    assert per_bar.local_median_N[t] == pytest.approx(
                        per_bar.local_median_N[t_now], rel=0, abs=1e-15
                    )


# ---------------------------------------------------------------------------
# Identical consecutive close → no spurious candidate movement
# (Appendix A v1.1 §3.4).
# ---------------------------------------------------------------------------

class TestIdenticalConsecutiveClose:

    def test_repeated_close_does_not_advance_candidate_height(self):
        """Long stretch of identical closes inside an established direction
        must keep ``candidate_height_pct`` strictly constant.
        """
        # 100 -> 102 to set UP direction at r=0.02, then a flat plateau.
        close = np.array(
            [100.0, 101.5, 102.0, 102.0, 102.0, 102.0, 102.0],
            dtype=np.float64,
        )
        per_bar = compute_zigzag_per_bar(
            close, reversal_threshold=0.02, local_window=3
        )
        # First two bars bootstrap → NaN.
        assert np.isnan(per_bar.candidate_height_pct[0])
        assert np.isnan(per_bar.candidate_height_pct[1])
        # From t=2 the candidate is established at (102-100)/100 = 0.02 and
        # the flat plateau cannot advance it.
        for t in range(2, len(close)):
            assert per_bar.candidate_height_pct[t] == pytest.approx(
                0.02, rel=0, abs=1e-15
            )
            assert per_bar.confirm_event[t] == 0

    def test_repeated_close_does_not_emit_phantom_confirm(self):
        close = np.array([100.0] * 20, dtype=np.float64)
        per_bar = compute_zigzag_per_bar(
            close, reversal_threshold=0.05, local_window=3
        )
        assert per_bar.confirm_event.sum() == 0
        # No leg → no confirmed leg ever materialises.
        assert np.all(per_bar.confirmed_leg_idx_at_t == -1)
        assert np.all(np.isnan(per_bar.candidate_height_pct))


# ---------------------------------------------------------------------------
# Close-only anti-drift: the per-bar engine signature/path must not depend
# on high / low / hlc3 / ohlc4 (plan §3.3 / spec §17.3).
# ---------------------------------------------------------------------------

class TestCloseOnlyAntiDrift:

    def test_compute_zigzag_per_bar_signature_is_close_only(self):
        sig = inspect.signature(compute_zigzag_per_bar)
        assert list(sig.parameters.keys()) == [
            "close", "reversal_threshold", "local_window", "daily_reset_event",
        ]

    def test_module_executable_lines_have_no_high_low_hlc3_ohlc4(self):
        """Mirror of the WP3 grep gate — the close-only formula path must
        not reference ``high`` / ``low`` / ``hlc3`` / ``ohlc4`` in
        executable code.  Module / function docstrings are stripped before
        scanning.
        """
        import supertrend_optimizer.core.zigzag_st_filter as zzmod

        text = Path(zzmod.__file__).read_text(encoding="utf-8")
        # Strip all triple-quoted strings (module / class / function
        # docstrings).
        stripped = re.sub(
            r'"""[\s\S]*?"""', "", text, flags=re.MULTILINE
        )
        stripped = re.sub(
            r"'''[\s\S]*?'''", "", stripped, flags=re.MULTILINE
        )
        # Strip inline comments.
        executable_lines: List[str] = []
        for line in stripped.splitlines():
            code = line.split("#", 1)[0]
            if code.strip():
                executable_lines.append(code)
        joined = "\n".join(executable_lines).lower()
        assert "high" not in joined or "height" in joined  # height is allowed
        # We need a stricter check: look for "high" tokens that are not
        # part of "height".
        for token in ("hlc3", "ohlc4"):
            assert token not in joined, (
                f"Close-only contract violated: '{token}' appears in "
                f"executable code path."
            )
        # Match `high` / `low` as standalone identifiers, not substrings.
        bad_tokens = re.findall(r"\b(?:high|low)\b", joined)
        # `low` legitimately appears in numpy literal `low_` etc.  We only
        # forbid bare ``high`` / ``low`` identifiers; reject if found.
        assert bad_tokens == [], (
            f"Close-only contract violated: bare high/low tokens "
            f"found in executable code: {bad_tokens}"
        )


# ---------------------------------------------------------------------------
# Edge cases & input validation.
# ---------------------------------------------------------------------------

class TestInputValidation:

    def test_invalid_reversal_threshold_raises(self):
        for bad in (0.0, 1.0, -0.1, math.nan, math.inf, -math.inf):
            with pytest.raises(ConfigError):
                compute_zigzag_per_bar(
                    SIMPLE_ZIGZAG.close,
                    reversal_threshold=bad,
                    local_window=3,
                )

    @pytest.mark.parametrize("bad_window", [0, -1, -10])
    def test_invalid_local_window_raises(self, bad_window: int):
        with pytest.raises(ConfigError):
            compute_zigzag_per_bar(
                SIMPLE_ZIGZAG.close,
                reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
                local_window=bad_window,
            )

    @pytest.mark.parametrize("bad_window", [True, False, 3.0, "3", None])
    def test_local_window_must_be_int(self, bad_window):
        with pytest.raises(ConfigError):
            compute_zigzag_per_bar(
                SIMPLE_ZIGZAG.close,
                reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
                local_window=bad_window,  # type: ignore[arg-type]
            )

    def test_empty_close_returns_zero_length_arrays(self):
        per_bar = compute_zigzag_per_bar(
            np.array([], dtype=np.float64),
            reversal_threshold=0.01,
            local_window=3,
        )
        assert per_bar.candidate_height_pct.shape == (0,)
        assert per_bar.confirm_event.shape == (0,)
        assert per_bar.confirmed_leg_idx_at_t.shape == (0,)
        assert per_bar.last_confirmed_leg_height_pct.shape == (0,)
        assert per_bar.local_median_N.shape == (0,)
        assert per_bar.local_median_available.shape == (0,)

    def test_none_close_returns_zero_length_arrays(self):
        per_bar = compute_zigzag_per_bar(
            None, reversal_threshold=0.01, local_window=3,  # type: ignore[arg-type]
        )
        assert per_bar.candidate_height_pct.shape == (0,)

    def test_single_bar_close_yields_no_confirms(self):
        per_bar = compute_zigzag_per_bar(
            np.array([100.0], dtype=np.float64),
            reversal_threshold=0.02,
            local_window=3,
        )
        assert per_bar.confirm_event.tolist() == [0]
        assert per_bar.confirmed_leg_idx_at_t.tolist() == [-1]
        assert np.isnan(per_bar.candidate_height_pct[0])

    def test_few_legs_fixture_yields_one_leg(self):
        per_bar = compute_zigzag_per_bar(
            _FEW_LEGS_CLOSE,
            reversal_threshold=_FEW_LEGS_R,
            local_window=3,
        )
        assert per_bar.confirm_event.sum() == 1
        # local_median_N stays unavailable (1 leg < local_window=3).
        assert not per_bar.local_median_available.any()


# ---------------------------------------------------------------------------
# Output array dtype / shape contract.
# ---------------------------------------------------------------------------

class TestOutputContract:

    def test_array_shapes_and_dtypes(self):
        per_bar = compute_zigzag_per_bar(
            SIMPLE_ZIGZAG.close,
            reversal_threshold=SIMPLE_ZIGZAG.reversal_threshold,
            local_window=3,
        )
        n = len(SIMPLE_ZIGZAG.close)
        assert per_bar.candidate_height_pct.shape == (n,)
        assert per_bar.candidate_height_pct.dtype == np.float64
        assert per_bar.confirm_event.shape == (n,)
        assert per_bar.confirm_event.dtype == np.int8
        assert per_bar.confirmed_leg_idx_at_t.shape == (n,)
        assert per_bar.confirmed_leg_idx_at_t.dtype == np.int64
        assert per_bar.last_confirmed_leg_height_pct.shape == (n,)
        assert per_bar.last_confirmed_leg_height_pct.dtype == np.float64
        assert per_bar.local_median_N.shape == (n,)
        assert per_bar.local_median_N.dtype == np.float64
        assert per_bar.local_median_available.shape == (n,)
        assert per_bar.local_median_available.dtype == bool


# ---------------------------------------------------------------------------
# Anti-drift gates — WP4 must not silently advance into WP5+ behaviours.
# ---------------------------------------------------------------------------

class TestAntiDrift:

    def test_module_does_not_export_runtime_artifacts_yet(self):
        """WP4 ships ``compute_zigzag_per_bar`` and ``ZigZagPerBar``; WP5
        ships FSM and ``apply(...)``.  Runtime backtest artifacts remain
        deferred to WP7+.
        """
        import supertrend_optimizer.core.zigzag_st_filter as zzmod

        forbidden_until_wp7_plus = {
            "RawBacktestArtifacts",
        }
        public = {name for name in dir(zzmod) if not name.startswith("_")}
        assert public.isdisjoint(forbidden_until_wp7_plus), (
            f"Module must not yet expose: {public & forbidden_until_wp7_plus}"
        )

    def test_compute_zigzag_per_bar_does_not_take_lifecycle_args(self):
        """Plan §3.3 step 5: ``local_median_N`` is independent of lifecycle
        start.  The signature must NOT carry FSM / freeze / lifecycle args.
        """
        sig = inspect.signature(compute_zigzag_per_bar)
        names = list(sig.parameters.keys())
        forbidden = {
            "freeze_confirmed_legs",
            "lifecycle_start",
            "fsm_state",
            "trend",
            "positions",
        }
        assert set(names).isdisjoint(forbidden)
