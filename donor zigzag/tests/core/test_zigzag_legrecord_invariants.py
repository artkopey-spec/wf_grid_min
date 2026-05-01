"""
Phase 5 invariant guard: LegRecord legacy-field sanity.

Protects inv 8.1.10 / 8.3.7 (RFC v3.1 §8.1, §8.3) while Phase 5 adds new
fields to LegRecord and plumbs pre-confirm data through the armament FSM:

  8.1.10  legacy field NAMES and post-confirm semantics of
          arm_bar / shot_bar / fired are unchanged; the valid-range of
          trade_id_if_fired is widened only (by fix N-06).
  8.3.7   LegRecord.arm_bar and LegRecord.shot_bar cannot be < confirm_bar
          (legacy preserved).

The test runs compute_zigzag_filter end-to-end on a synthetic fixture and
verifies the invariants hold on EVERY produced leg, including pre-confirm
armed sessions (where we expect the legacy fields to stay at -1, never to
carry the pre-confirm arm/shot bars — those data live in
pre_confirm_arm_bar / pre_confirm_shot_bar in Phase 5).
"""
from __future__ import annotations

import numpy as np

from supertrend_optimizer.core.zigzag_filter import (
    FIRED_NONE,
    FIRED_YES_SHOT,
    LEG_DIR_UP,
    compute_zigzag_filter,
)


def _synthetic_inputs(n: int = 400, seed: int = 7):
    rng = np.random.RandomState(seed)
    # Generate a moderately volatile random walk so some legs confirm and
    # some pre-confirm shots may occur under default A=on/B=off.
    steps = rng.uniform(-0.6, 0.6, n)
    close = 100.0 + np.cumsum(steps)
    high = close + rng.uniform(0.1, 0.6, n)
    low = close - rng.uniform(0.1, 0.6, n)
    open_p = close + rng.uniform(-0.1, 0.1, n)
    # Flip supertrend periodically so armed sessions can fire.
    st_trend = np.where((np.arange(n) // 13) % 2 == 0, +1, -1).astype(np.int8)
    session_ids = np.zeros(n, dtype=np.int64)
    return high, low, close, open_p, session_ids, st_trend


def _cfg() -> dict:
    return dict(
        reversal_threshold=0.005,
        min_legs_global=5,
        k_local=5,
        entry_side="counter_trend",
        arm_timeout_bars_since_extreme=24,
        arm_timeout_bars_hard=78,
        structural_reset_min_span=3,
        readiness=dict(
            contour_a=dict(enabled=True, p80_quantile=0.80),
            contour_b=dict(enabled=False, local_k=5,
                           open_ratio=1.5, close_ratio=1.0),
        ),
    )


def test_inv_8_1_10_legacy_field_names_present():
    # Every LegRecord exposes the legacy field names (attribute access).
    res = compute_zigzag_filter(*_synthetic_inputs(), cfg=_cfg())
    for lg in res.legs:
        for name in ("leg_id", "start_bar", "end_bar", "confirm_bar",
                     "direction", "height_pct", "arm_bar", "shot_bar",
                     "fired", "trade_id_if_fired"):
            assert hasattr(lg, name), f"legacy field {name!r} lost on LegRecord"


def test_inv_8_3_7_arm_bar_never_below_confirm_bar():
    res = compute_zigzag_filter(*_synthetic_inputs(), cfg=_cfg())
    for lg in res.legs:
        cb = int(lg.confirm_bar)
        ab = int(lg.arm_bar)
        assert ab == -1 or ab >= cb, (
            f"leg {lg.leg_id}: arm_bar={ab} < confirm_bar={cb} "
            f"(inv 8.3.7 violated)"
        )


def test_inv_8_3_7_shot_bar_never_below_confirm_bar():
    res = compute_zigzag_filter(*_synthetic_inputs(), cfg=_cfg())
    for lg in res.legs:
        cb = int(lg.confirm_bar)
        sb = int(lg.shot_bar)
        assert sb == -1 or sb >= cb, (
            f"leg {lg.leg_id}: shot_bar={sb} < confirm_bar={cb} "
            f"(inv 8.3.7 violated)"
        )


def test_inv_8_1_10_fired_is_known_enum():
    # FIRED_NONE..FIRED_NO_REGIME_OFF range (0..6) per RFC §4.5.
    res = compute_zigzag_filter(*_synthetic_inputs(), cfg=_cfg())
    for lg in res.legs:
        f = int(lg.fired)
        assert 0 <= f <= 6, f"leg {lg.leg_id}: fired={f} out of enum range"


def test_yes_shot_requires_shot_bar_ge_confirm_bar():
    # If a leg fired YES_SHOT through the legacy post-confirm path, its
    # shot_bar MUST be >= confirm_bar (inv 8.3.7 tightened for fired=YES).
    res = compute_zigzag_filter(*_synthetic_inputs(), cfg=_cfg())
    for lg in res.legs:
        if int(lg.fired) == int(FIRED_YES_SHOT):
            assert int(lg.shot_bar) >= int(lg.confirm_bar), (
                f"leg {lg.leg_id}: YES_SHOT but shot_bar={lg.shot_bar} "
                f"< confirm_bar={lg.confirm_bar}"
            )


def test_non_fired_leg_has_no_shot_bar():
    res = compute_zigzag_filter(*_synthetic_inputs(), cfg=_cfg())
    for lg in res.legs:
        if int(lg.fired) == int(FIRED_NONE):
            assert int(lg.shot_bar) == -1, (
                f"leg {lg.leg_id}: fired=NONE but shot_bar={lg.shot_bar}"
            )
