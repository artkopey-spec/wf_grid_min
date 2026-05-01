"""
RFC v3.1 Phase 6 — Legacy-parity snapshot + default/legacy divergence.

Covers:
  * TP-38 `legacy_parity_config_stable`   — deterministic snapshot for the
                                            §6.7 `legacy_parity` preset.
  * TP-39 `default_v3_1_differs_from_legacy_parity`
                                          — on the same fixture, the
                                            default v3.1 cfg (A=on, B=off) and
                                            legacy_parity cfg (A=off, B=on,
                                            open_ratio=0.0, close_ratio=0.0)
                                            produce strictly different
                                            armament / allow_entry footprints.
  * TP-24 `contour_b_degenerate_parity`   — under legacy_parity, `ready_b`
                                            latches on the first valid
                                            confirm-bar and does not drop on
                                            normal non-negative medians.

Scope (RFC Phase 6, §11.7):
  * Read-only regression tests over `compute_zigzag_filter`.
  * No new xfails.
  * Snapshot values are baked into this file; any intentional change to
    default v3.1 / legacy_parity semantics has to update the snapshot
    block below explicitly (manual diff review, RFC §11.8 pt.4).

The fixture is a deterministic synthetic OHLC walk (fixed RNG seed) with
multi-session boundaries.  Large enough (N=1500, 3 sessions × 500 bars) to
exercise several legs / regime transitions while keeping the run fast.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    FIRED_NONE,
    LegRecord,
    ZigZagFilterResult,
    compute_zigzag_filter,
)


# ---------------------------------------------------------------------------
# Deterministic synthetic fixture
# ---------------------------------------------------------------------------


_FIXTURE_SEED: int = 20260423           # frozen; do not bump without snapshot review
_FIXTURE_N: int = 1500                  # 3 sessions × 500 bars
_FIXTURE_SESSION_LEN: int = 500


def _build_fixture() -> Dict[str, np.ndarray]:
    """
    Deterministic OHLC / session / st_trend arrays.

    - close: random walk with drift, scaled to cross enough to produce legs.
    - high/low: close ± small noise.
    - session_ids: 3 consecutive sessions of 500 bars.
    - st_trend: toggles by sign of 10-bar EMA slope, mapped to {-1, +1}.
    """
    rng = np.random.default_rng(_FIXTURE_SEED)
    N = _FIXTURE_N

    steps = rng.normal(loc=0.0, scale=0.6, size=N)
    close = 100.0 + np.cumsum(steps)
    close = np.maximum(close, 1.0)

    high_noise = rng.uniform(0.05, 0.8, N)
    low_noise = rng.uniform(0.05, 0.8, N)
    high = close + high_noise
    low = close - low_noise
    open_p = close.copy()

    session_ids = np.repeat(
        np.arange(N // _FIXTURE_SESSION_LEN, dtype=np.int64),
        _FIXTURE_SESSION_LEN,
    )
    assert session_ids.shape == (N,)

    ema = np.empty(N, dtype=np.float64)
    alpha = 2.0 / (10.0 + 1.0)
    ema[0] = close[0]
    for i in range(1, N):
        ema[i] = alpha * close[i] + (1.0 - alpha) * ema[i - 1]
    slope = np.concatenate([[0.0], np.diff(ema)])
    st_trend = np.where(slope >= 0.0, 1, -1).astype(np.int8)

    return dict(
        high=high,
        low=low,
        close=close,
        open_prices=open_p,
        session_ids=session_ids,
        st_trend=st_trend,
    )


# ---------------------------------------------------------------------------
# Config presets
# ---------------------------------------------------------------------------


def _default_cfg_v31() -> Dict[str, Any]:
    """
    RFC v3.1 default (§6.5): Contour A on (primary), Contour B off.

    Matches the shape produced by `cli/tester._validate_zigzag_section` so
    the test stays aligned with what the CLI normalises at merge time.
    """
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
            contour_a=dict(enabled=True, p80_quantile=0.80),
            contour_b=dict(enabled=False, local_k=5, open_ratio=1.5, close_ratio=1.0),
        ),
    )


def _legacy_parity_cfg() -> Dict[str, Any]:
    """
    RFC v3.1 §6.7 `legacy_parity` preset: A=off, B=on, open_ratio=0.0,
    close_ratio=0.0.  Not bit-exact legacy v2, but the maximally-close
    debug preset compatible with the §6.6 validator.
    """
    cfg = _default_cfg_v31()
    cfg["readiness"]["contour_a"]["enabled"] = False
    cfg["readiness"]["contour_b"]["enabled"] = True
    cfg["readiness"]["contour_b"]["open_ratio"] = 0.0
    cfg["readiness"]["contour_b"]["close_ratio"] = 0.0
    return cfg


# ---------------------------------------------------------------------------
# Aggregate extraction helpers
# ---------------------------------------------------------------------------


def _reason_counts(res: ZigZagFilterResult) -> Dict[str, int]:
    """Histogram of per-bar `reason` values (string → count)."""
    out: Dict[str, int] = {}
    for r in res.reason.tolist():
        key = str(r)
        out[key] = out.get(key, 0) + 1
    return out


def _fired_counts(res: ZigZagFilterResult) -> Dict[int, int]:
    """Histogram of LegRecord.fired values across all confirmed legs."""
    out: Dict[int, int] = {}
    for lg in res.legs:
        out[int(lg.fired)] = out.get(int(lg.fired), 0) + 1
    return out


def _array_hash(arr: np.ndarray) -> str:
    """Stable short hash of an array's raw bytes."""
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()[:16]


def _summary(res: ZigZagFilterResult) -> Dict[str, Any]:
    """
    High-level deterministic summary used both for the merge snapshot and
    for the divergence check.  Kept intentionally small so the snapshot
    stays stable under non-semantic refactors.
    """
    return {
        "n_bars": int(res.allow_entry.shape[0]),
        "n_legs": int(len(res.legs)),
        "n_allow_entry": int(res.allow_entry.sum()),
        "n_armed_bars": int(res.armed.sum()),
        "n_ready_a": int(res.ready_a.sum()),
        "n_ready_b": int(res.ready_b.sum()),
        "n_readiness_on": int(res.readiness_on.sum()),
        "n_structural_reset": int(res.structural_reset_event.sum()),
        "n_legs_fired_not_none": sum(
            1 for lg in res.legs if int(lg.fired) != FIRED_NONE
        ),
        "n_legs_armed_by_candidate": sum(
            1 for lg in res.legs if bool(lg.armed_by_candidate)
        ),
        "allow_entry_hash": _array_hash(res.allow_entry),
        "armed_hash": _array_hash(res.armed),
    }


# ---------------------------------------------------------------------------
# Baked snapshot values (RFC §11.8 pt.4 — update with manual diff review)
# ---------------------------------------------------------------------------
#
# The values below are captured from a single authoritative run of this
# test file against the deterministic fixture + the §6.7 `legacy_parity`
# cfg.  They act as a merge-stability guard:
#
#   * Any semantic change in core zigzag / readiness / armament logic
#     will flip one of the numbers or the array hash → CI flags a visible
#     diff → maintainer must acknowledge & bump the snapshot.
#
#   * Non-semantic refactors (variable renames, inlining, doc changes)
#     do not touch these numbers.
#
# Do NOT bump these values without a matching RFC §11.7 diff-review note.

_LEGACY_PARITY_SNAPSHOT: Dict[str, Any] = {
    "n_bars": 1500,
    "n_legs": 747,
    "n_allow_entry": 92,
    "n_armed_bars": 1031,
    # Note: `ready_a` is computed unconditionally (the raw FSM output) and
    # only gated to `readiness_on` via `enabled_a`.  Under legacy_parity
    # `enabled_a=False` so the raw value leaks but is never consumed;
    # `readiness_on` below is the effective signal.
    "n_ready_a": 240,
    "n_ready_b": 1490,
    "n_readiness_on": 1490,
    "n_structural_reset": 0,
    "n_legs_fired_not_none": 0,
    "n_legs_armed_by_candidate": 376,
    "allow_entry_hash": "0f3d39395c7f75d7",
    "armed_hash": "ba3dfb3ec423a89e",
}


# ---------------------------------------------------------------------------
# TP-38 — legacy_parity snapshot stability
# ---------------------------------------------------------------------------


class TestTP38LegacyParitySnapshot:
    """
    TP-38 (§9.2): legacy_parity cfg produces a stable, reproducible
    footprint on a fixed fixture (snapshot-test, G-06).
    """

    def test_merge_stability_against_baked_snapshot(self):
        inp = _build_fixture()
        cfg = _legacy_parity_cfg()
        res = compute_zigzag_filter(cfg=cfg, **inp)
        summary = _summary(res)
        assert summary == _LEGACY_PARITY_SNAPSHOT, (
            "legacy_parity snapshot drift — if this is intentional, "
            "bump _LEGACY_PARITY_SNAPSHOT with RFC §11.7 manual review. "
            f"Diff: got {summary}"
        )

    def test_two_runs_are_bitwise_identical(self):
        # RFC G-06: "Bit-exact snapshot трейдов/legs через merge".
        # `compute_zigzag_filter` must be a pure function on (inputs, cfg).
        inp = _build_fixture()
        cfg = _legacy_parity_cfg()
        r1 = compute_zigzag_filter(cfg=cfg, **inp)
        r2 = compute_zigzag_filter(cfg=copy.deepcopy(cfg), **inp)
        np.testing.assert_array_equal(r1.allow_entry, r2.allow_entry)
        np.testing.assert_array_equal(r1.reason, r2.reason)
        np.testing.assert_array_equal(r1.ready_a, r2.ready_a)
        np.testing.assert_array_equal(r1.ready_b, r2.ready_b)
        np.testing.assert_array_equal(r1.readiness_on, r2.readiness_on)
        np.testing.assert_array_equal(r1.armed, r2.armed)
        np.testing.assert_array_equal(r1.arm_source, r2.arm_source)
        np.testing.assert_array_equal(r1.cand_leg_id, r2.cand_leg_id)
        assert len(r1.legs) == len(r2.legs)
        for a, b in zip(r1.legs, r2.legs):
            # Dataclass `==` compares float NaN by identity-of-value
            # (NaN != NaN), so we flatten to a NaN-aware tuple.
            def _key(lg: LegRecord):
                parts = []
                for name in lg.__dataclass_fields__:
                    v = getattr(lg, name)
                    if isinstance(v, float) and np.isnan(v):
                        parts.append(("__NaN__", name))
                    else:
                        parts.append((name, v))
                return tuple(parts)
            assert _key(a) == _key(b)


# ---------------------------------------------------------------------------
# TP-24 — contour B degenerate-parity latches on first valid confirm
# ---------------------------------------------------------------------------


class TestTP24ContourBDegenerateParity:
    """
    TP-24 (§6.7 / §4.1 fix): with open_ratio=0 and close_ratio=0 the FSM
    turns `ready_b` ON at the first valid confirm-bar and keeps it ON
    while medians stay non-negative (impossible to go negative by
    invariant 8.1.2: height_pct > 0 ⇒ medians ≥ 0).
    """

    def test_ready_b_latches_on_first_confirm_bar(self):
        inp = _build_fixture()
        cfg = _legacy_parity_cfg()
        res = compute_zigzag_filter(cfg=cfg, **inp)

        ready_b = res.ready_b
        assert ready_b.any(), "legacy_parity should produce ready_b=True at least once"

        first_on = int(np.argmax(ready_b))
        # Before activation — all False.
        assert not ready_b[:first_on].any()
        # After activation — stays True until the end (no deactivation on
        # non-negative medians under legacy_parity).
        assert ready_b[first_on:].all(), (
            "ready_b must not deactivate under legacy_parity on normal "
            "non-negative medians (RFC §6.7)."
        )

    def test_readiness_on_is_gated_by_ready_b_only(self):
        # Under legacy_parity enabled_a=False, so the effective readiness_on
        # MUST equal ready_b regardless of the raw ready_a telemetry value
        # (raw ready_a is computed unconditionally by core — see §7.1).
        inp = _build_fixture()
        cfg = _legacy_parity_cfg()
        res = compute_zigzag_filter(cfg=cfg, **inp)
        assert np.array_equal(res.readiness_on, res.ready_b)


# ---------------------------------------------------------------------------
# TP-39 — default v3.1 vs legacy_parity: strict divergence
# ---------------------------------------------------------------------------


class TestTP39DefaultDiffersFromLegacyParity:
    """
    TP-39 (§9.2, G-06): on the same fixture, default v3.1 (A=on, B=off) and
    legacy_parity (A=off, B=on, 0.0/0.0) produce strictly different
    footprints.  Prevents accidental regressions that would silently
    restore legacy behaviour under the default.
    """

    def test_summaries_are_not_equal(self):
        inp = _build_fixture()
        s_default = _summary(
            compute_zigzag_filter(cfg=_default_cfg_v31(), **inp)
        )
        s_legacy = _summary(
            compute_zigzag_filter(cfg=_legacy_parity_cfg(), **inp)
        )
        assert s_default != s_legacy

    def test_readiness_sources_are_different(self):
        # Raw `ready_a` / `ready_b` arrays are ALWAYS computed (diagnostic
        # telemetry, §7.1) regardless of `enabled_*`.  Divergence is
        # expressed through the effective `readiness_on` which equals the
        # enabled contour's raw array.
        inp = _build_fixture()
        r_default = compute_zigzag_filter(cfg=_default_cfg_v31(), **inp)
        r_legacy = compute_zigzag_filter(cfg=_legacy_parity_cfg(), **inp)

        # Default (A=on, B=off): readiness_on follows ready_a.
        assert np.array_equal(r_default.readiness_on, r_default.ready_a)
        # Legacy-parity (A=off, B=on): readiness_on follows ready_b.
        assert np.array_equal(r_legacy.readiness_on, r_legacy.ready_b)
        # And the two effective signals must differ on this fixture.
        assert not np.array_equal(r_default.readiness_on, r_legacy.readiness_on)

    def test_allow_entry_and_armed_differ(self):
        # G-06 fix B-01: "Число trades и/или распределение PnL-баров
        # строго различно".  Here we compare allow_entry and armed
        # footprints directly: at least one must differ.
        inp = _build_fixture()
        r_default = compute_zigzag_filter(cfg=_default_cfg_v31(), **inp)
        r_legacy = compute_zigzag_filter(cfg=_legacy_parity_cfg(), **inp)

        allow_differs = not np.array_equal(r_default.allow_entry, r_legacy.allow_entry)
        armed_differs = not np.array_equal(r_default.armed, r_legacy.armed)
        assert allow_differs or armed_differs, (
            "default v3.1 and legacy_parity MUST produce different "
            "allow_entry or armed footprints on the shared fixture (TP-39)."
        )

    def test_leg_lists_differ_in_armament_lifecycle(self):
        # legs themselves (confirm bars, heights, medians) are largely the
        # same — those are driven by confirmed-ZigZag pass, not readiness.
        # Armament lifecycle (fired, arm_bar, shot_bar, armed_by_candidate)
        # is where the two configs diverge.
        inp = _build_fixture()
        r_default = compute_zigzag_filter(cfg=_default_cfg_v31(), **inp)
        r_legacy = compute_zigzag_filter(cfg=_legacy_parity_cfg(), **inp)

        assert len(r_default.legs) == len(r_legacy.legs)

        fired_default = tuple(int(lg.fired) for lg in r_default.legs)
        fired_legacy = tuple(int(lg.fired) for lg in r_legacy.legs)
        armed_by_cand_default = tuple(bool(lg.armed_by_candidate) for lg in r_default.legs)
        armed_by_cand_legacy = tuple(bool(lg.armed_by_candidate) for lg in r_legacy.legs)

        assert (
            fired_default != fired_legacy
            or armed_by_cand_default != armed_by_cand_legacy
        ), (
            "default v3.1 must diverge from legacy_parity in at least one "
            "of (fired, armed_by_candidate) across the leg list."
        )
