"""
ZigZag filter for SuperTrend entry filtering (v2.0).

Implements a causal (confirmed) ZigZag pivot detector combined with a
volatility-regime gate and one-shot armed entry mechanism.

Public API
----------
compute_zigzag_filter(high, low, close, open_prices, session_ids,
                      st_trend, cfg) -> ZigZagFilterResult

Decision-bar aligned contract (§1.6):
    All returned arrays are decision-bar aligned.
    The function does NOT accept execution_model and does NOT shift arrays.
    The single shift decision→execution is done ONLY in apply_entry_filters
    (core/backtest.py).

Enum constants (int8) — internal, defined here, NOT in constants.py
--------------------------------------------------------------------
Leg direction:
    LEG_DIR_UNKNOWN = 0
    LEG_DIR_UP      = 1
    LEG_DIR_DOWN    = -1

Regime state:
    REGIME_CLOSED      = 0
    REGIME_OPEN_GRACE  = 1
    REGIME_OPEN_ACTIVE = 2

Armed side:
    ARMED_SIDE_NONE  = 0
    ARMED_SIDE_LONG  = 1
    ARMED_SIDE_SHORT = -1

Fired type:
    FIRED_NONE            = 0   # not yet fired / not armed
    FIRED_YES_SHOT        = 1
    FIRED_NO_NEW_PIVOT    = 2
    FIRED_NO_TIMEOUT_SOFT = 3
    FIRED_NO_TIMEOUT_HARD = 4
    FIRED_SESSION_RESET   = 5
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from sortedcontainers import SortedList

import numpy as np

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
# Enum constants (int8 values — do NOT change without updating tests)
# ---------------------------------------------------------------------------

# Leg direction
LEG_DIR_UNKNOWN: int = 0
LEG_DIR_UP: int = 1
LEG_DIR_DOWN: int = -1

# Volatility regime state
REGIME_CLOSED: int = 0
REGIME_OPEN_GRACE: int = 1
REGIME_OPEN_ACTIVE: int = 2

# Armed side
ARMED_SIDE_NONE: int = 0
ARMED_SIDE_LONG: int = 1
ARMED_SIDE_SHORT: int = -1

# Fired type
FIRED_NONE: int = 0
FIRED_YES_SHOT: int = 1
FIRED_NO_NEW_PIVOT: int = 2
FIRED_NO_TIMEOUT_SOFT: int = 3
FIRED_NO_TIMEOUT_HARD: int = 4
FIRED_SESSION_RESET: int = 5
# RFC v3.1 §4.5, §8.3.5: NEW — Contour B deactivation disarm.
# Fires when armed-session has arm_source ∈ {B, BOTH} and ready_B transitions True→False
# on a confirm_bar (fix D8: includes BOTH).  NOT exposed via FILTER_REASON_WHITELIST
# (invariant G-03) — surfaced only through zz_disarm_event diagnostic.
FIRED_NO_REGIME_OFF: int = 6

# RFC v3.1 §4.1, §5.3: arm_source enum (per-bar int8).
# Indicates which readiness contour was active at the moment an armed-session was created.
ARM_SRC_NONE: int = 0
ARM_SRC_A: int = 1
ARM_SRC_B: int = 2
ARM_SRC_BOTH: int = 3

# RFC v3.1 §5.6: Contour B latched-readiness state enum.
READY_B_OFF: int = 0
READY_B_ON: int = 1

# RFC v3.1 §7.1: zz_disarm_event per-bar mirror of FIRED_* (diagnostic).
# Same int codes as FIRED_*; zeroed on bars without disarm.
DISARM_EVT_NONE: int = FIRED_NONE
DISARM_EVT_YES_SHOT: int = FIRED_YES_SHOT
DISARM_EVT_NO_NEW_PIVOT: int = FIRED_NO_NEW_PIVOT
DISARM_EVT_TIMEOUT_SOFT: int = FIRED_NO_TIMEOUT_SOFT
DISARM_EVT_TIMEOUT_HARD: int = FIRED_NO_TIMEOUT_HARD
DISARM_EVT_SESSION_RESET: int = FIRED_SESSION_RESET
DISARM_EVT_NO_REGIME_OFF: int = FIRED_NO_REGIME_OFF

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegRecord:
    """
    Immutable record of a single confirmed ZigZag leg.

    Causality invariant: confirm_bar > end_bar (pivot always in the past).
    H_pct invariant: height_pct > 0.
    shot_bar invariant: shot_bar >= confirm_bar, or -1.
    arm_bar invariant: arm_bar == confirm_bar, or -1.
    """
    leg_id: int
    start_bar: int
    end_bar: int
    confirm_bar: int
    start_price: float
    end_price: float
    direction: int           # LEG_DIR_UP | LEG_DIR_DOWN
    height_pct: float        # > 0
    length_bars: int         # end_bar - start_bar
    confirm_lag_bars: int    # confirm_bar - end_bar, >= 1

    # Statistics snapshot BEFORE adding this leg (§1.4a step 1)
    n_legs_before: int
    global_median_at_confirm: float
    global_p80_at_confirm: float
    local_median_at_confirm: float  # NaN if n_legs_before < K_local

    # Regime on confirm_bar (after step 3 §1.4a)
    regime_state_at_confirm: int    # REGIME_*
    opened_regime: bool             # True if closed→grace on this confirm
    closed_regime: bool             # True if active→closed on this confirm

    # Armament (filled in step 6 §1.4a and §1.5)
    is_strong: bool
    armed_side: int          # ARMED_SIDE_*
    arm_bar: int             # confirm_bar if armed, -1 otherwise

    # Disarm result (filled when armed leg is disarmed)
    fired: int               # FIRED_* (NONE while active; *_SHOT | NO_* | SESSION_RESET)
    shot_bar: int            # decision-bar of shot, -1 if not fired

    # Trade linkage: filled ONLY in io/excel_tester.py, always None in core
    trade_id_if_fired: Optional[int] = field(default=None)

    # RFC v3.1 §7.5 (fix B-02): Phase 5 additive fields.
    # All default to neutral values so legacy call-sites / test fixtures
    # constructing LegRecord directly keep working without modification.
    #
    # arm_source            : ARM_SRC_* at the time the armed-session of this
    #                         leg was created (independent of pre/post).
    # armed_by_candidate    : True iff armed-session started strictly BEFORE
    #                         confirm_bar (any contour; fix B-02).
    # pre_confirm_arm_bar   : decision-bar of pre-confirm armament start, or -1.
    # pre_confirm_shot_bar  : decision-bar of pre-confirm shot, or -1.
    arm_source: int = 0
    armed_by_candidate: bool = False
    pre_confirm_arm_bar: int = -1
    pre_confirm_shot_bar: int = -1

    # RFC v3.1 §7.6 / fix N-01: private candidate-leg id captured at the
    # moment of confirm.  Used by the Phase 5 two-step trade↔leg linker.
    # Underscore prefix keeps this out of the public Excel / JSON surface
    # while still being readable from io-layer code.
    _cand_leg_id_at_confirm: int = -1


@dataclass(frozen=True)
class ZigZagFilterResult:
    """
    Result of compute_zigzag_filter.  All arrays are decision-bar aligned (§1.6).

    RFC v3.1 (§7.1) additive diagnostic arrays are included as fields with defaults
    so existing call-sites continue to work without modification.
    """
    allow_entry: np.ndarray              # (N,) bool
    reason: np.ndarray                   # (N,) object  (str from whitelist)

    # Per-bar diagnostics
    leg_direction: np.ndarray            # (N,) int8, LEG_DIR_*
    cand_height_pct: np.ndarray          # (N,) float64, NaN before first pivot
    last_pivot_price: np.ndarray         # (N,) float64, NaN before first pivot
    last_pivot_bar_idx: np.ndarray       # (N,) int64, -1 before first pivot
    global_median: np.ndarray           # (N,) float64, NaN before 1st leg
    global_p80: np.ndarray              # (N,) float64, NaN before 1st leg
    local_median: np.ndarray            # (N,) float64, NaN before K_local legs
    n_legs_before: np.ndarray           # (N,) int64
    regime_state: np.ndarray            # (N,) int8, REGIME_* (telemetry-only, §5.6)
    n_legs_since_regime_open: np.ndarray # (N,) int64
    armed: np.ndarray                   # (N,) bool
    armed_side: np.ndarray              # (N,) int8, ARMED_SIDE_*
    n_bars_since_extreme: np.ndarray    # (N,) int64, -1 when armed=False
    n_bars_since_arm: np.ndarray        # (N,) int64, -1 when armed=False

    # Per-leg list (immutable snapshot)
    legs: Tuple[LegRecord, ...]

    # ------------- RFC v3.1 additive diagnostics (§7.1) -------------
    # All default to empty arrays so pre-v3.1 dataclass constructions remain
    # valid (existing tests that build ZigZagFilterResult manually keep working).

    # Readiness per bar
    ready_a: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    ready_b: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    readiness_on: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))

    # arm_source per bar and at decision time (§5.4 fix D6).
    # Values: ARM_SRC_NONE / ARM_SRC_A / ARM_SRC_B / ARM_SRC_BOTH
    arm_source: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))
    arm_source_for_decision: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))

    # Monotonic candidate-leg id per bar (RFC v3.1 §5.2, §7.1).
    cand_leg_id: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))

    # Readiness block diagnostic string (§7.1 fix N-03).  DIAGNOSTIC ONLY —
    # not in FILTER_REASON_WHITELIST.  Values:
    #   "ok" | "not_ready_A" | "not_ready_B" | "not_ready_both"
    #   | "both_disabled" | "warmup" | "pathological" | "disarm_b_regime_off"
    readiness_block_reason: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=object))

    # Per-bar mirror of FIRED_* (§7.1).
    disarm_event: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))

    # Structural reset event (RFC v3.1 §5.8, fix B-03).
    structural_reset_event: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))


# ---------------------------------------------------------------------------
# Internal: confirmed ZigZag pass (§1.1 + §1.2 + §1.7)
# ---------------------------------------------------------------------------


@dataclass
class _PartialLeg:
    """
    Partially-filled leg record produced by _confirmed_zigzag_pass.
    Statistics / regime / armament fields are filled in later stages
    (§1.4a steps 1–6 are split across Stages 2/3/4 of the implementation).
    """
    leg_id: int
    start_bar: int
    end_bar: int
    confirm_bar: int
    start_price: float
    end_price: float
    direction: int          # LEG_DIR_UP | LEG_DIR_DOWN
    height_pct: float       # > 0
    length_bars: int        # end_bar - start_bar
    confirm_lag_bars: int   # confirm_bar - end_bar, >= 1

    # RFC v3.1 §5.2, §7.6 (N-01): private linkage field — the zz_cand_leg_id
    # counter value captured at the moment this leg was confirmed (i.e. the id
    # of the candidate that materialised into this confirmed leg).
    # Used in Phase 5 trade↔leg two-step linkage. NOT part of public LegRecord yet.
    cand_leg_id_at_confirm: int = -1


@dataclass
class _ZigZagPassResult:
    """Output of _confirmed_zigzag_pass (Stage 2, §1.1 + §1.2 + §1.7)."""
    legs: List[_PartialLeg]

    # Per-bar arrays (decision-bar aligned; length == N)
    leg_direction: np.ndarray         # int8,   LEG_DIR_*  (current leg direction at end of bar)
    cand_height_pct: np.ndarray       # float64, |running_extreme - last_pivot| / last_pivot, NaN if undef
    last_pivot_price: np.ndarray      # float64, price of last_confirmed_pivot, NaN if undef
    last_pivot_bar_idx: np.ndarray    # int64,   bar_idx of last_confirmed_pivot, -1 if undef
    pathological: np.ndarray          # bool,   True when bar is NaN/inf/invalid (§1.8)

    # Confirmed-pivot event flags (per bar, used downstream in stages 3/4):
    confirm_event: np.ndarray         # bool,   True on bars where a pivot was confirmed
    session_reset_event: np.ndarray   # bool,   True on bars where a session reset fired

    # RFC v3.1 §5.2: monotonic candidate-leg id per bar.
    # -1 when leg_direction == UNKNOWN.  Increments on every leg_direction
    # change (UNKNOWN→UP/DOWN, UP→DOWN, DOWN→UP).  NOT reset on session_reset.
    # Not used for decisions until Phase 3; plumbed here for linkage (Phase 5).
    zz_cand_leg_id: np.ndarray        # int64,  -1 when UNKNOWN

    # RFC v3.1 §5.8 (fix B-03): structural reset event.
    # True on bar t iff all:
    #   - pathological[t-1] == True AND pathological[t] == False (recovery),
    #   - prior contiguous pathological-span length >= structural_reset_min_span,
    #   - pivot state is re-initialised on t (last_pivot_bar < 0 on entry, i.e.
    #     recovery starts from scratch — not a seamless continuation of the
    #     pre-pathological running_extreme).
    # On such bars: leg_direction is forced to UNKNOWN, pivot state clears,
    # and downstream armament FSM disarms any active session with
    # FIRED_SESSION_RESET (reuse; no new FIRED_* enum — §4.5, §8.3.9).
    structural_reset_event: np.ndarray  # bool


def _is_bar_pathological(h: float, l: float) -> bool:
    """
    Pathological OHLC bar (§1.8):
      - NaN or inf in high or low
      - high < low
      - non-positive high/low (negative price makes percentages meaningless)
    """
    if not (math.isfinite(h) and math.isfinite(l)):
        return True
    if h < l:
        return True
    if h <= 0.0 or l <= 0.0:
        return True
    return False


def _confirmed_zigzag_pass(
    high: np.ndarray,
    low: np.ndarray,
    open_prices: np.ndarray,
    session_ids: np.ndarray,
    reversal_threshold: float,
    structural_reset_min_span: int = 3,
) -> _ZigZagPassResult:
    """
    Causal confirmed-ZigZag pass (§1.1, §1.2, §1.7, §1.8 pathological handling).

    Emits confirmed pivots in strict causal order.  A pivot at bar E is
    confirmed on bar C > E only once price reverses by `reversal_threshold`
    against the running extreme.  Session boundaries (session_ids[t] !=
    session_ids[t-1]) reset the operational state but never produce a pivot.

    This function implements steps 1.1.1 – 1.1.5 plus leg registration (§1.2).
    It does NOT fill statistics, regime or armament fields on LegRecord —
    those are added in Stages 3 and 4.

    Invariants (checked by §6.1 tests):
      - confirm_bar > end_bar for every emitted leg.
      - height_pct > 0 for every emitted leg.
      - At most one pivot is emitted per bar.
      - On a pathological bar: state does NOT advance, no pivot emitted,
        pathological[t] = True, running_extreme NOT updated.
    """
    n = int(len(high))
    assert len(low) == n and len(open_prices) == n and len(session_ids) == n

    r = float(reversal_threshold)

    # Per-bar output arrays
    leg_direction = np.full(n, LEG_DIR_UNKNOWN, dtype=np.int8)
    cand_height_pct = np.full(n, np.nan, dtype=np.float64)
    last_pivot_price = np.full(n, np.nan, dtype=np.float64)
    last_pivot_bar_idx = np.full(n, -1, dtype=np.int64)
    pathological = np.zeros(n, dtype=bool)
    confirm_event = np.zeros(n, dtype=bool)
    session_reset_event = np.zeros(n, dtype=bool)
    # RFC v3.1 §5.2: monotonic candidate-leg id per bar.
    zz_cand_leg_id = np.full(n, -1, dtype=np.int64)
    # RFC v3.1 §5.8 (fix B-03): structural reset event.
    structural_reset_event = np.zeros(n, dtype=bool)
    # Contiguous pathological-span counter (ticks while pathological, resets to 0
    # on the first non-pathological bar of a new span).  Used on recovery to
    # decide whether to emit structural_reset_event (§5.8 trigger).
    pathological_span: int = 0
    _struct_reset_min = max(1, int(structural_reset_min_span))

    legs: List[_PartialLeg] = []

    # State variables (§1.1)
    last_pivot_bar: int = -1
    last_pivot_pr: float = float("nan")
    cur_leg_dir: int = LEG_DIR_UNKNOWN
    run_ext_bar: int = -1
    run_ext_pr: float = float("nan")
    # bar_idx and price of the pivot BEFORE last_confirmed_pivot (needed for start_bar of emitted legs)
    prev_pivot_bar: int = -1
    prev_pivot_pr: float = float("nan")
    next_leg_id: int = 0
    # RFC v3.1 §5.2: candidate-leg id counter (monotonic, NOT reset on session_reset).
    cur_cand_leg_id: int = -1

    for t in range(n):
        h_t = float(high[t])
        l_t = float(low[t])

        # ---- STEP 1.1.1: session reset (MUST run BEFORE pathological freeze) ----
        # Calendar-day boundary is derived from session_ids[], which is OHLC-agnostic.
        # Even on a pathological bar (NaN/inf OHLC) the session boundary is real and
        # operational state must be cleared; otherwise the first valid bar of the new
        # day will not re-seed last_pivot from open[t] (§1.1.2) and the armed leg of
        # the previous session will never receive FIRED_SESSION_RESET (§1.5/§1.7).
        if t > 0 and session_ids[t] != session_ids[t - 1]:
            session_reset_event[t] = True
            cur_leg_dir = LEG_DIR_UNKNOWN
            run_ext_bar = -1
            run_ext_pr = float("nan")
            last_pivot_bar = -1
            last_pivot_pr = float("nan")
            prev_pivot_bar = -1
            prev_pivot_pr = float("nan")
            pathological_span = 0  # new session → counter clears (§5.8 N/A)
            # Armed/one_shot are managed in Stage 4 — nothing to do here.

        # ---- PATHOLOGICAL BAR (§1.1 final, §1.8) ----
        is_path = _is_bar_pathological(h_t, l_t)
        if is_path:
            pathological[t] = True
            pathological_span += 1
            # State FROZEN: do not update running_extreme, do not confirm,
            # do not advance timers.  Write snapshot of previous state.
            leg_direction[t] = cur_leg_dir
            last_pivot_price[t] = last_pivot_pr
            last_pivot_bar_idx[t] = last_pivot_bar
            # cand_height_pct requires both last_pivot and running_extreme
            if (last_pivot_bar >= 0 and math.isfinite(last_pivot_pr)
                    and last_pivot_pr > 0.0 and math.isfinite(run_ext_pr)):
                cand_height_pct[t] = abs(run_ext_pr - last_pivot_pr) / last_pivot_pr
            # Frozen candidate-leg id (RFC v3.1 §5.2).
            if cur_leg_dir != LEG_DIR_UNKNOWN:
                zz_cand_leg_id[t] = cur_cand_leg_id
            continue

        # ---- STRUCTURAL RESET (§5.8, fix B-03 / RP-1 Block 1-E) ----
        # RFC v3.1 §5.8 lists THREE triggering conditions (all must hold):
        #   (1) recovery transition: prior bar was pathological AND current
        #       bar is valid.  Structurally enforced: pathological bars
        #       `continue` above and do not reach this block; any bar that
        #       makes it here is valid-after-pathological (or valid-after-valid,
        #       in which case `pathological_span == 0` and the span gate
        #       below fails).
        #   (2) span threshold: contiguous pathological-span ≥
        #       `structural_reset_min_span`.
        #   (3) pivot state re-initialised on ENTRY to bar t: either
        #       `last_pivot_bar < 0` (no pivot seeded yet) or
        #       `cur_leg_dir == LEG_DIR_UNKNOWN` (no leg direction
        #       established).  "Seamless continuation" — i.e. a recovery
        #       bar where the old running_extreme and pivots survived the
        #       pathological span — does NOT qualify as a structural
        #       reset per the RFC framing.
        #
        # Plus de-duplication vs `session_reset_event[t]`:
        # session_reset already cleared state on the same bar — we don't
        # double-fire the event (§4.5 / §8.3.9).
        #
        # IMPORTANT (RP-1E): condition (3) is evaluated against the state
        # AT ENTRY TO BAR t, BEFORE the side-effect reset inside this
        # block.  A state that is UNKNOWN/-1 only because the
        # structural_reset block itself cleared it does NOT count as a
        # valid precondition.
        pivot_state_reinitialized_on_entry = (
            last_pivot_bar < 0 or cur_leg_dir == LEG_DIR_UNKNOWN
        )
        if (pathological_span >= _struct_reset_min
                and pivot_state_reinitialized_on_entry
                and not session_reset_event[t]):
            structural_reset_event[t] = True
            # Side-effect reset: preserved for symmetry with session_reset
            # and to guarantee `cur_leg_dir = LEG_DIR_UNKNOWN` immediately
            # after emit.  When condition (3) held because
            # `cur_leg_dir == LEG_DIR_UNKNOWN` already, this is a no-op on
            # that field; the remaining state (running_extreme, prev_pivot)
            # still needs the explicit clear so step 1.1.2 re-seeds from
            # `open[t]`.
            cur_leg_dir = LEG_DIR_UNKNOWN
            run_ext_bar = -1
            run_ext_pr = float("nan")
            last_pivot_bar = -1
            last_pivot_pr = float("nan")
            prev_pivot_bar = -1
            prev_pivot_pr = float("nan")
            # cand_leg_id counter is monotonic — it does NOT reset (fix D9).
            # Armed / one_shot disarm handled downstream in unified armament FSM.
        # Reset span counter on any non-pathological bar (whether event fired or not).
        pathological_span = 0

        # ---- STEP 1.1.2: initialise first pivot from open[t] ----
        if last_pivot_bar < 0:
            o_t = float(open_prices[t])
            if _is_bar_pathological(o_t, o_t):
                # open itself is NaN/inf/non-positive — treat as pathological
                pathological[t] = True
                leg_direction[t] = cur_leg_dir
                last_pivot_price[t] = last_pivot_pr
                last_pivot_bar_idx[t] = last_pivot_bar
                continue
            last_pivot_bar = t
            last_pivot_pr = o_t
            run_ext_bar = t
            run_ext_pr = o_t
            cur_leg_dir = LEG_DIR_UNKNOWN
            # Fall through to steps 1.1.3–1.1.5 on THIS bar (plan §1.1.1 note).

        # ---- STEP 1.1.3: determine leg_direction when UNKNOWN ----
        if cur_leg_dir == LEG_DIR_UNKNOWN:
            up_trig = last_pivot_pr * (1.0 + r)
            dn_trig = last_pivot_pr * (1.0 - r)
            up_hit = h_t > up_trig
            dn_hit = l_t < dn_trig
            if up_hit and not dn_hit:
                cur_leg_dir = LEG_DIR_UP
                cur_cand_leg_id += 1  # UNKNOWN → UP: new candidate (RFC v3.1 §5.2)
                run_ext_bar = t
                run_ext_pr = h_t
            elif dn_hit and not up_hit:
                cur_leg_dir = LEG_DIR_DOWN
                cur_cand_leg_id += 1  # UNKNOWN → DOWN: new candidate
                run_ext_bar = t
                run_ext_pr = l_t
            elif up_hit and dn_hit:
                # Dominating bar: choose side further from pivot (§1.1.3)
                up_move = (h_t - last_pivot_pr) / last_pivot_pr
                dn_move = (last_pivot_pr - l_t) / last_pivot_pr
                if up_move >= dn_move:
                    cur_leg_dir = LEG_DIR_UP
                    cur_cand_leg_id += 1  # UNKNOWN → UP: new candidate
                    run_ext_bar = t
                    run_ext_pr = h_t
                else:
                    cur_leg_dir = LEG_DIR_DOWN
                    cur_cand_leg_id += 1  # UNKNOWN → DOWN: new candidate
                    run_ext_bar = t
                    run_ext_pr = l_t
            # else: both False → cur_leg_dir stays UNKNOWN; fall through.
        else:
            # ---- STEP 1.1.4: update running_extreme (strict >, <) ----
            if cur_leg_dir == LEG_DIR_UP:
                if h_t > run_ext_pr:
                    run_ext_bar = t
                    run_ext_pr = h_t
            else:  # LEG_DIR_DOWN
                if l_t < run_ext_pr:
                    run_ext_bar = t
                    run_ext_pr = l_t

            # ---- STEP 1.1.5: check reversal and confirm pivot ----
            # Fix A (plan v2.0.1): skip reversal check if running_extreme was
            # updated on THIS bar in step 1.1.4.  Guarantees confirm_bar > end_bar
            # as a STRUCTURAL invariant (see §1.2 proof in plan v2.0.1).
            if run_ext_bar == t:
                pass  # defer reversal check to next bar
            elif cur_leg_dir == LEG_DIR_UP:
                if l_t <= run_ext_pr * (1.0 - r):
                    # Confirm high pivot at run_ext_bar.
                    end_bar = run_ext_bar
                    end_price = run_ext_pr
                    confirm_bar = t
                    # Previous pivot (start of this leg).
                    # For the very first leg of a session/dataset, the
                    # "start" is the initial last_pivot (from §1.1.2) — it is
                    # not a "previous confirmed" pivot but we use it as
                    # start_bar/start_price per §1.2.
                    start_bar = last_pivot_bar
                    start_price = last_pivot_pr
                    height_pct = (end_price - start_price) / start_price
                    if height_pct > 0.0:
                        # Capture the candidate-leg id BEFORE switching direction
                        # (RFC v3.1 §5.2, §7.6 N-01).
                        _cid_at_confirm = cur_cand_leg_id
                        legs.append(_PartialLeg(
                            leg_id=next_leg_id,
                            start_bar=start_bar,
                            end_bar=end_bar,
                            confirm_bar=confirm_bar,
                            start_price=start_price,
                            end_price=end_price,
                            direction=LEG_DIR_UP,
                            height_pct=height_pct,
                            length_bars=end_bar - start_bar,
                            confirm_lag_bars=confirm_bar - end_bar,
                            cand_leg_id_at_confirm=_cid_at_confirm,
                        ))
                        next_leg_id += 1
                        confirm_event[t] = True
                        # Shift pivot chain only when leg is valid (height > 0).
                        prev_pivot_bar = last_pivot_bar
                        prev_pivot_pr = last_pivot_pr
                        last_pivot_bar = end_bar
                        last_pivot_pr = end_price
                        cur_leg_dir = LEG_DIR_DOWN
                        cur_cand_leg_id += 1  # UP → DOWN: new candidate
                        run_ext_bar = t
                        run_ext_pr = l_t
                    # else: numerical artefact (flat bar) — preserve state so
                    # next bar can continue extending the same running extreme.
            else:  # LEG_DIR_DOWN
                if h_t >= run_ext_pr * (1.0 + r):
                    end_bar = run_ext_bar
                    end_price = run_ext_pr
                    confirm_bar = t
                    start_bar = last_pivot_bar
                    start_price = last_pivot_pr
                    height_pct = (start_price - end_price) / start_price
                    if height_pct > 0.0:
                        # Capture the candidate-leg id BEFORE switching direction
                        # (RFC v3.1 §5.2, §7.6 N-01).
                        _cid_at_confirm = cur_cand_leg_id
                        legs.append(_PartialLeg(
                            leg_id=next_leg_id,
                            start_bar=start_bar,
                            end_bar=end_bar,
                            confirm_bar=confirm_bar,
                            start_price=start_price,
                            end_price=end_price,
                            direction=LEG_DIR_DOWN,
                            height_pct=height_pct,
                            length_bars=end_bar - start_bar,
                            confirm_lag_bars=confirm_bar - end_bar,
                            cand_leg_id_at_confirm=_cid_at_confirm,
                        ))
                        next_leg_id += 1
                        confirm_event[t] = True
                        # Shift pivot chain only when leg is valid (height > 0).
                        prev_pivot_bar = last_pivot_bar
                        prev_pivot_pr = last_pivot_pr
                        last_pivot_bar = end_bar
                        last_pivot_pr = end_price
                        cur_leg_dir = LEG_DIR_UP
                        cur_cand_leg_id += 1  # DOWN → UP: new candidate
                        run_ext_bar = t
                        run_ext_pr = h_t
                    # else: numerical artefact (flat bar) — preserve state so
                    # next bar can continue extending the same running extreme.

        # ---- Per-bar snapshot (at END of bar t) ----
        leg_direction[t] = cur_leg_dir
        last_pivot_price[t] = last_pivot_pr
        last_pivot_bar_idx[t] = last_pivot_bar
        if (last_pivot_bar >= 0 and math.isfinite(last_pivot_pr)
                and last_pivot_pr > 0.0 and math.isfinite(run_ext_pr)):
            cand_height_pct[t] = abs(run_ext_pr - last_pivot_pr) / last_pivot_pr
        # RFC v3.1 §5.2: candidate-leg id snapshot.
        if cur_leg_dir != LEG_DIR_UNKNOWN:
            zz_cand_leg_id[t] = cur_cand_leg_id
        # else: stays -1 (initialized)

    return _ZigZagPassResult(
        legs=legs,
        leg_direction=leg_direction,
        cand_height_pct=cand_height_pct,
        last_pivot_price=last_pivot_price,
        last_pivot_bar_idx=last_pivot_bar_idx,
        pathological=pathological,
        confirm_event=confirm_event,
        session_reset_event=session_reset_event,
        zz_cand_leg_id=zz_cand_leg_id,
        structural_reset_event=structural_reset_event,
    )


# ---------------------------------------------------------------------------
# Internal: causal expanding statistics (§1.3, §1.4a step 1)
# ---------------------------------------------------------------------------


@dataclass
class _LegStatsSnapshot:
    """
    Statistics snapshot taken on the confirm_bar of a leg, BEFORE adding
    the leg to the expanding set (§1.4a step 1).

    Causality invariant (§1.3, G.2.2):
      n_legs_before == number of legs with confirm_bar < current leg's
      confirm_bar.  The current leg itself is NOT included in the snapshot;
      it is added to the expanding set in step 4 of §1.4a.
    """
    n_legs_before: int
    global_median: float      # NaN if n_legs_before == 0
    global_p80: float         # NaN if n_legs_before == 0
    local_median: float       # NaN if n_legs_before < k_local


def _percentile_sorted(sorted_values: List[float], q: float) -> float:
    """
    Linear-interpolation percentile (numpy-compatible, method='linear')
    over an already-sorted list.  q in [0, 1].
    """
    n = len(sorted_values)
    if n == 0:
        return float("nan")
    if n == 1:
        return float(sorted_values[0])
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo]) * (1.0 - frac) + float(sorted_values[hi]) * frac


def _median_sorted(sorted_values: List[float]) -> float:
    n = len(sorted_values)
    if n == 0:
        return float("nan")
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_values[mid])
    return 0.5 * (float(sorted_values[mid - 1]) + float(sorted_values[mid]))


def _build_causal_statistics(
    legs: List[_PartialLeg],
    k_local: int,
    q_strong: float = 0.80,
) -> List[_LegStatsSnapshot]:
    """
    Build per-leg statistics snapshots taken ON confirm_bar BEFORE adding
    the current leg to the expanding set (§1.3, §1.4a step 1).

    Returns a list of length == len(legs).  snapshot[i] corresponds to
    legs[i] and reflects the state of the expanding-set at the moment just
    before leg i is added.

    Implementation notes:
      - Expanding sorted list of height_pct values maintained as SortedList
        (sortedcontainers): add() is O(log L) amortised, index access O(log L).
        Total complexity O(L log L) — satisfies plan §3.1.
      - Local window: last K_local height values kept in a deque; local_median
        computed by sorting the deque snapshot (K_local fixed and small, so
        O(K_local log K_local) is fine).
    """
    snapshots: List[_LegStatsSnapshot] = []
    sorted_heights: SortedList = SortedList()   # global expanding, O(log L) add
    local_window: deque[float] = deque(maxlen=k_local)  # last k_local heights

    for leg in legs:
        # SNAPSHOT: statistics BEFORE adding this leg (§1.4a step 1).
        n_before = len(sorted_heights)
        if n_before == 0:
            g_med = float("nan")
            g_p80 = float("nan")
        else:
            g_med = _median_sorted(sorted_heights)
            g_p80 = _percentile_sorted(sorted_heights, q_strong)
        if n_before < k_local:
            l_med = float("nan")
        else:
            # Sort a k_local-size snapshot for median (O(k_local log k_local))
            local_sorted = sorted(local_window)
            l_med = _median_sorted(local_sorted)
        snapshots.append(_LegStatsSnapshot(
            n_legs_before=n_before,
            global_median=g_med,
            global_p80=g_p80,
            local_median=l_med,
        ))

        # ADD leg to expanding set (§1.4a step 4 — but structurally the
        # "add" happens HERE right after snapshot, because all downstream
        # consumers of sorted_heights see the snapshot stored above).
        sorted_heights.add(float(leg.height_pct))
        local_window.append(float(leg.height_pct))

    return snapshots


def _broadcast_stats_to_bars(
    legs: List[_PartialLeg],
    snapshots: List[_LegStatsSnapshot],
    n_bars: int,
    k_local: int,
    q_strong: float = 0.80,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Build per-bar statistic arrays (decision-bar aligned, §1.3 causality).

    A leg with confirm_bar = c contributes to statistics on bars > c
    (strictly — on bar c itself the leg is NOT yet in the set; per §1.3
    causality invariant G.2.2).

    Returns
    -------
    (global_median, global_p80, local_median, n_legs_before) — each
    np.ndarray of shape (n_bars,), float64 / int64.
    """
    g_median = np.full(n_bars, np.nan, dtype=np.float64)
    g_p80 = np.full(n_bars, np.nan, dtype=np.float64)
    l_median = np.full(n_bars, np.nan, dtype=np.float64)
    n_before = np.zeros(n_bars, dtype=np.int64)

    # Walk through legs in confirm_bar order (legs are already in that order
    # by construction of _confirmed_zigzag_pass).  For each leg i with
    # confirm_bar c, between the previous confirm_bar and c we see the
    # "pre-leg-i" snapshot; from bar c+1 onward we see "post-leg-i" state
    # which is the snapshot of leg i+1 (if it exists).
    #
    # Maintain sorted_heights as SortedList (O(log L) add) and advance a bar
    # pointer — same logic as _build_causal_statistics, O(N log L) total.
    sorted_heights: SortedList = SortedList()
    local_window: deque[float] = deque(maxlen=k_local)
    leg_idx = 0
    L = len(legs)

    for t in range(n_bars):
        # Incorporate any legs whose confirm_bar < t (causality: strict <).
        while leg_idx < L and legs[leg_idx].confirm_bar < t:
            sorted_heights.add(float(legs[leg_idx].height_pct))
            local_window.append(float(legs[leg_idx].height_pct))
            leg_idx += 1

        nb = len(sorted_heights)
        n_before[t] = nb
        if nb == 0:
            # g_median[t], g_p80[t] stay NaN
            pass
        else:
            g_median[t] = _median_sorted(sorted_heights)
            g_p80[t] = _percentile_sorted(sorted_heights, q_strong)
        if nb >= k_local:
            local_sorted = sorted(local_window)
            l_median[t] = _median_sorted(local_sorted)
        # else: l_median[t] stays NaN

    return g_median, g_p80, l_median, n_before


# ---------------------------------------------------------------------------
# Internal: regime state machine (§1.4, §1.4a step 3)
# ---------------------------------------------------------------------------


@dataclass
class _LegRegimeInfo:
    """
    Regime-related info computed on confirm_bar of a leg (§1.4a step 3).

    `state_at_confirm` is the regime state AFTER the three transition checks
    (close-check → open-check → activate-check) on the confirm_bar.
    """
    state_at_confirm: int         # REGIME_*
    opened_regime: bool           # closed → open_grace fired on this confirm
    closed_regime: bool           # open_active → closed fired on this confirm
    n_legs_since_regime_open: int  # counter AFTER this leg is processed
    is_strong: bool               # height_pct >= global_p80_snapshot


def _run_regime_state_machine(
    legs: List[_PartialLeg],
    snapshots: List[_LegStatsSnapshot],
    k_local: int,
) -> List[_LegRegimeInfo]:
    """
    Apply §1.4 transitions per leg, in confirm_bar order (§1.4a step 3).

    Transition order (hard requirement, plan §1.4a):
      (a) close-check:  open_active → closed  if local_median < global_median
      (b) open-check:   closed → open_grace   if H_pct >= global_p80
      (c) activate-check: open_grace → open_active  if the incoming leg is
          the K_local-th since regime opened (counter +1 includes the
          just-confirmed leg, per §1.4a step 3(c)).

    Counter n_legs_since_regime_open:
      - Incremented on each confirm when state ∈ {grace, active}
      - Reset to 0 on transition to closed
      - Trigger leg of closed→open_grace counts as 1 (immediately after open)
    """
    assert len(legs) == len(snapshots)

    out: List[_LegRegimeInfo] = []
    state = REGIME_CLOSED
    n_since_open = 0

    for leg, snap in zip(legs, snapshots):
        h_pct = float(leg.height_pct)
        g_p80 = snap.global_p80
        g_med = snap.global_median
        l_med = snap.local_median

        opened = False
        closed = False

        # (a) close-check: active → closed
        if state == REGIME_OPEN_ACTIVE:
            # NaN comparisons are all False → no close on NaN
            if (not math.isnan(l_med)) and (not math.isnan(g_med)) and (l_med < g_med):
                state = REGIME_CLOSED
                n_since_open = 0
                closed = True

        # (b) open-check: closed → open_grace
        if state == REGIME_CLOSED:
            if (not math.isnan(g_p80)) and (h_pct >= g_p80):
                state = REGIME_OPEN_GRACE
                n_since_open = 0   # will be set to 1 below (trigger leg counts as 1)
                opened = True

        # (c) activate-check: grace → active
        # Counter "n_legs_since_regime_open" is incremented to include the
        # CURRENT leg (trigger leg counts as 1, §1.4a step 3(c)).
        if state in (REGIME_OPEN_GRACE, REGIME_OPEN_ACTIVE):
            n_since_open += 1

        if state == REGIME_OPEN_GRACE and n_since_open >= k_local:
            state = REGIME_OPEN_ACTIVE

        is_strong = (not math.isnan(g_p80)) and (h_pct >= g_p80)

        out.append(_LegRegimeInfo(
            state_at_confirm=state,
            opened_regime=opened,
            closed_regime=closed,
            n_legs_since_regime_open=n_since_open,
            is_strong=is_strong,
        ))

    return out


def _broadcast_regime_to_bars(
    legs: List[_PartialLeg],
    regime_infos: List[_LegRegimeInfo],
    n_bars: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build decision-bar aligned regime_state and n_legs_since_regime_open
    arrays.

    Semantics: on bar t the regime state is whatever was active AFTER the
    last confirm_bar c <= t (if any).  Before the first confirmed leg the
    state is REGIME_CLOSED.

    On the confirm_bar itself, the "after step 3" state is used — i.e.
    the new state reflects the transitions triggered by the leg confirmed
    on this bar.  This is consistent with §1.4a: steps 1..6 are atomic
    within one bar and external consumers see the post-step-3 state.
    """
    state_arr = np.full(n_bars, REGIME_CLOSED, dtype=np.int8)
    counter_arr = np.zeros(n_bars, dtype=np.int64)

    cur_state = REGIME_CLOSED
    cur_counter = 0
    leg_idx = 0
    L = len(legs)

    for t in range(n_bars):
        # Apply any confirms whose confirm_bar == t (or earlier, though
        # earlier can't exist since we march forward monotonically).
        while leg_idx < L and legs[leg_idx].confirm_bar <= t:
            cur_state = regime_infos[leg_idx].state_at_confirm
            cur_counter = regime_infos[leg_idx].n_legs_since_regime_open
            leg_idx += 1
        state_arr[t] = cur_state
        counter_arr[t] = cur_counter

    return state_arr, counter_arr


# ---------------------------------------------------------------------------
# Internal: armament state machine (§1.4a step 6, §1.5 A/B/C/D)
# ---------------------------------------------------------------------------


@dataclass
class _LegArmamentInfo:
    """
    Per-leg armament outcome — filled by _run_armament_state_machine.

    Phase 5 (RFC v3.1 §7.5) extends with pre-confirm lifecycle fields.
    Defaults keep the legacy-only sites valid.
    """
    armed_side: int      # ARMED_SIDE_*  (NONE if leg did not arm)
    arm_bar: int         # confirm_bar if armed (post-confirm), -1 otherwise
    fired: int           # FIRED_* final state of the armed cycle
    shot_bar: int        # decision-bar of the shot (post-confirm), -1 if not fired
    # Phase 5 additions (defaults = no pre-confirm activity).
    arm_source: int = 0                # ARM_SRC_* at armament creation
    armed_by_candidate: bool = False   # True iff session started pre-confirm
    pre_confirm_arm_bar: int = -1      # pre-confirm start bar, or -1
    pre_confirm_shot_bar: int = -1     # pre-confirm shot bar, or -1


@dataclass
class _ArmamentPerBarArrays:
    """
    Per-bar armament diagnostics & decision-support flags, built during
    _run_armament_state_machine.  All arrays are decision-bar aligned.
    """
    armed: np.ndarray                    # (N,) bool
    armed_side: np.ndarray               # (N,) int8, ARMED_SIDE_*
    n_bars_since_extreme: np.ndarray     # (N,) int64, -1 when armed=False
    n_bars_since_arm: np.ndarray         # (N,) int64, -1 when armed=False
    one_shot_fired_current_leg: np.ndarray   # (N,) bool

    # Decision-support event flags (§1.6 ordering):
    timeout_expired_on_this_bar: np.ndarray  # (N,) bool  — soft/hard timeout fired on t
    new_pivot_disarm_on_this_bar: np.ndarray # (N,) bool  — armed was disarmed by new pivot on t
    st_flip_on_this_bar: np.ndarray          # (N,) bool  — YES_SHOT fired on t (decision grant)

    # Decision-time snapshot of armed state, taken BEFORE §1.5 D (YES_SHOT disarm).
    # This is what §1.6 branch-ordering must consult: on a YES_SHOT bar the
    # armed session is closed at the END of the bar, but for the allow_entry
    # decision the bar itself was evaluated WHILE armed=True AND st_flip=True.
    # Without this snapshot, ``armed[t]`` after disarm is False → §1.6 branch
    # "armed==False" triggers and forces reason=zz_locked_same_leg, making
    # reason=ok on a shot bar unreachable (audit BLOCKER 1).
    armed_for_decision: np.ndarray           # (N,) bool
    armed_side_for_decision: np.ndarray      # (N,) int8, ARMED_SIDE_*


def _st_flip_toward(side: int, trend: np.ndarray, d: int) -> bool:
    """
    st_flip_toward(side, d) per plan §1.6.

    Returns True iff on decision-bar d the SuperTrend direction just flipped
    TOWARD the armed side.  trend[d-1] == 0 is treated as ATR-stabilisation
    (NOT a flip).  d == 0 never flips.
    """
    if d == 0:
        return False
    prev = int(trend[d - 1])
    curr = int(trend[d])
    if prev == 0:
        return False
    if side == ARMED_SIDE_LONG:
        return curr == +1 and prev == -1
    if side == ARMED_SIDE_SHORT:
        return curr == -1 and prev == +1
    return False


def _run_armament_state_machine(
    legs: List[_PartialLeg],
    regime_infos: List[_LegRegimeInfo],
    snapshots: List[_LegStatsSnapshot],
    st_trend: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    pathological: np.ndarray,
    session_reset_event: np.ndarray,
    min_legs_global: int,
    arm_timeout_bars_since_extreme: int,
    arm_timeout_bars_hard: int,
) -> Tuple[List[_LegArmamentInfo], _ArmamentPerBarArrays]:
    """
    Full armament state machine (plan §1.4a step 6 + §1.5 steps A/B/C/D).

    Armament rules (§1.4a step 6 — counter_trend / A1):
      - UP leg   → armed_side = ARMED_SIDE_SHORT.
      - DOWN leg → armed_side = ARMED_SIDE_LONG.
      - Conditions: regime ∈ {grace, active} AND is_strong AND
                    n_legs_before >= min_legs_global AND one_shot clear.

    Between confirm_bars (§1.5):
      A. Update armed_leg_extreme (price + reset n_bars_since_extreme=0),
         otherwise n_bars_since_extreme += 1.
      B. n_bars_since_arm += 1.
      C. Check timeouts — hard first, then soft.
      D. Check shot: armed + st_flip_toward(armed_side, t).

    Disarm types:
      YES_SHOT         — §1.5 D
      NO_NEW_PIVOT     — §1.4a step 2 (old armed disarmed on new confirm)
      NO_TIMEOUT_SOFT  — §1.5 C (n_bars_since_extreme > arm_timeout_bars_since_extreme)
      NO_TIMEOUT_HARD  — §1.5 C (n_bars_since_arm > arm_timeout_bars_hard)
      SESSION_RESET    — §1.7 (armed at session boundary)

    After ANY disarm, one_shot_fired_current_leg = True; it is reset to False
    on the next confirm_bar (§1.4a step 5) and on session_reset.
    """
    N = int(len(st_trend))

    # Per-bar arrays
    armed = np.zeros(N, dtype=bool)
    armed_side = np.full(N, ARMED_SIDE_NONE, dtype=np.int8)
    n_bars_since_extreme = np.full(N, -1, dtype=np.int64)
    n_bars_since_arm = np.full(N, -1, dtype=np.int64)
    one_shot = np.zeros(N, dtype=bool)

    timeout_on_bar = np.zeros(N, dtype=bool)
    new_pivot_disarm_on_bar = np.zeros(N, dtype=bool)
    st_flip_on_bar = np.zeros(N, dtype=bool)

    # Decision-time snapshot: armed state BEFORE §1.5 D disarm on bar t.
    armed_for_decision = np.zeros(N, dtype=bool)
    armed_side_for_decision = np.full(N, ARMED_SIDE_NONE, dtype=np.int8)

    # Per-leg outputs (one _LegArmamentInfo per leg)
    leg_outs: List[_LegArmamentInfo] = [
        _LegArmamentInfo(
            armed_side=ARMED_SIDE_NONE,
            arm_bar=-1,
            fired=FIRED_NONE,
            shot_bar=-1,
        ) for _ in legs
    ]

    # Mutable armed-session state
    cur_armed = False
    cur_side = ARMED_SIDE_NONE
    cur_leg_idx = -1           # index into `legs` of the currently-armed leg
    cur_arm_bar = -1
    cur_ext_price = float("nan")
    cur_ext_bar = -1
    cur_n_extreme = -1
    cur_n_arm = -1
    cur_one_shot = False

    # Index into legs (next leg whose confirm_bar we will hit)
    # Legs are sorted by confirm_bar ascending (produced by _confirmed_zigzag_pass).
    leg_iter_idx = 0
    L = len(legs)

    def _disarm(leg_idx: int, fired_code: int, shot_bar: int = -1) -> None:
        """Close the armed-session: record outcome on the leg, reset state."""
        nonlocal cur_armed, cur_side, cur_leg_idx, cur_arm_bar, cur_ext_price
        nonlocal cur_ext_bar, cur_n_extreme, cur_n_arm, cur_one_shot
        if 0 <= leg_idx < L:
            leg_outs[leg_idx] = _LegArmamentInfo(
                armed_side=leg_outs[leg_idx].armed_side,
                arm_bar=leg_outs[leg_idx].arm_bar,
                fired=fired_code,
                shot_bar=shot_bar,
            )
        cur_armed = False
        cur_side = ARMED_SIDE_NONE
        cur_leg_idx = -1
        cur_arm_bar = -1
        cur_ext_price = float("nan")
        cur_ext_bar = -1
        cur_n_extreme = -1
        cur_n_arm = -1
        cur_one_shot = True   # lock one_shot on the current (not-yet-confirmed) leg

    for t in range(N):
        # ---- SESSION RESET (§1.7) ----
        # If session_reset_event[t] is set, operational state clears.
        # An armed leg present on the boundary gets fired = SESSION_RESET.
        if session_reset_event[t]:
            if cur_armed and cur_leg_idx >= 0:
                _disarm(cur_leg_idx, FIRED_SESSION_RESET, shot_bar=-1)
            cur_one_shot = False  # §1.7: one_shot cleared on new session

        # ---- PATHOLOGICAL (§1.8) ----
        # State FROZEN: do not advance timers or check anything.
        if pathological[t]:
            if cur_armed:
                armed[t] = True
                armed_side[t] = cur_side
                n_bars_since_extreme[t] = cur_n_extreme
                n_bars_since_arm[t] = cur_n_arm
            one_shot[t] = cur_one_shot
            continue

        # ---- CONFIRM-BAR logic (§1.4a steps 2, 4, 5, 6) ----
        # Check whether any leg has confirm_bar == t.  At most one per bar
        # (invariant §1.2).
        confirm_here = (leg_iter_idx < L and legs[leg_iter_idx].confirm_bar == t)

        if confirm_here:
            leg_i = leg_iter_idx
            leg = legs[leg_i]
            reg = regime_infos[leg_i]
            snap = snapshots[leg_i]

            # STEP 2 (§1.4a): disarm old armed by NO_NEW_PIVOT
            if cur_armed and cur_leg_idx >= 0:
                _disarm(cur_leg_idx, FIRED_NO_NEW_PIVOT, shot_bar=-1)
                new_pivot_disarm_on_bar[t] = True

            # STEP 5: reset one_shot for the NEXT leg (the one starting at t+1)
            cur_one_shot = False

            # STEP 6: attempt to arm the just-confirmed leg
            is_strong = reg.is_strong
            regime_ok = reg.state_at_confirm in (REGIME_OPEN_GRACE, REGIME_OPEN_ACTIVE)
            warmup_ok = snap.n_legs_before >= int(min_legs_global)

            if is_strong and regime_ok and warmup_ok:
                new_side = (ARMED_SIDE_SHORT if leg.direction == LEG_DIR_UP
                            else ARMED_SIDE_LONG)
                cur_armed = True
                cur_side = new_side
                cur_leg_idx = leg_i
                cur_arm_bar = t
                cur_ext_price = float(leg.end_price)
                cur_ext_bar = int(leg.end_bar)
                cur_n_extreme = t - int(leg.end_bar)
                cur_n_arm = 0
                # Record on the leg: armed_side + arm_bar
                leg_outs[leg_i] = _LegArmamentInfo(
                    armed_side=new_side,
                    arm_bar=t,
                    fired=FIRED_NONE,
                    shot_bar=-1,
                )
            leg_iter_idx += 1
            # Note: we fall through to the D-check below so that a flip on
            # the same bar as the confirm could grant a shot.  Plan §1.5 D
            # / §1.6 allows this in principle; armed=True was just set.

        # ---- §1.5 A, B (between-confirm updates) ----
        # Apply only on bars where no confirm fired AND we are armed.
        if cur_armed and not confirm_here:
            # A. Update armed_leg_extreme
            if cur_side == ARMED_SIDE_SHORT:  # up-leg → watching higher highs
                h_t = float(high[t])
                if math.isfinite(h_t) and h_t > cur_ext_price:
                    cur_ext_price = h_t
                    cur_ext_bar = t
                    cur_n_extreme = 0
                else:
                    cur_n_extreme += 1
            elif cur_side == ARMED_SIDE_LONG:  # down-leg → watching lower lows
                l_t = float(low[t])
                if math.isfinite(l_t) and l_t < cur_ext_price:
                    cur_ext_price = l_t
                    cur_ext_bar = t
                    cur_n_extreme = 0
                else:
                    cur_n_extreme += 1
            # B. Bars since arm
            cur_n_arm += 1

            # C. Timeouts — hard first, then soft
            if cur_n_arm > int(arm_timeout_bars_hard):
                _disarm(cur_leg_idx, FIRED_NO_TIMEOUT_HARD, shot_bar=-1)
                timeout_on_bar[t] = True
            elif cur_n_extreme > int(arm_timeout_bars_since_extreme):
                _disarm(cur_leg_idx, FIRED_NO_TIMEOUT_SOFT, shot_bar=-1)
                timeout_on_bar[t] = True

        # ---- DECISION SNAPSHOT (BEFORE §1.5 D disarm) ----
        # §1.6 requires: on bar t the allow_entry decision sees the armed
        # state that existed AT THE MOMENT st_flip is checked — i.e. before
        # YES_SHOT resets cur_armed.  Without this snapshot, allow_entry=True
        # is unreachable on any shot_bar (audit BLOCKER 1).
        armed_for_decision[t] = cur_armed
        armed_side_for_decision[t] = cur_side if cur_armed else ARMED_SIDE_NONE

        # ---- §1.5 D / §1.6: check shot (st_flip toward armed_side) ----
        if cur_armed:
            if _st_flip_toward(cur_side, st_trend, t):
                _disarm(cur_leg_idx, FIRED_YES_SHOT, shot_bar=t)
                st_flip_on_bar[t] = True

        # ---- SNAPSHOT per-bar ----
        if cur_armed:
            armed[t] = True
            armed_side[t] = cur_side
            n_bars_since_extreme[t] = cur_n_extreme
            n_bars_since_arm[t] = cur_n_arm
        one_shot[t] = cur_one_shot

    arr = _ArmamentPerBarArrays(
        armed=armed,
        armed_side=armed_side,
        n_bars_since_extreme=n_bars_since_extreme,
        n_bars_since_arm=n_bars_since_arm,
        one_shot_fired_current_leg=one_shot,
        timeout_expired_on_this_bar=timeout_on_bar,
        new_pivot_disarm_on_this_bar=new_pivot_disarm_on_bar,
        st_flip_on_this_bar=st_flip_on_bar,
        armed_for_decision=armed_for_decision,
        armed_side_for_decision=armed_side_for_decision,
    )
    return leg_outs, arr


# ---------------------------------------------------------------------------
# RFC v3.1 Phase 1 stubs — to be implemented in Phase 3
# ---------------------------------------------------------------------------


def _compute_ready_a_array(
    leg_direction: np.ndarray,
    cand_height_pct: np.ndarray,
    global_p80: np.ndarray,
    n_legs_before: np.ndarray,
    pathological: np.ndarray,
    min_legs_global: int,
) -> np.ndarray:
    """
    Contour A (per-bar, stateless) readiness (RFC v3.1 §4.1, fix D1).

        ready_A[t] = (leg_direction[t] != UNKNOWN)
                     AND isfinite(global_p80[t])
                     AND isfinite(cand_height_pct[t])
                     AND (cand_height_pct[t] >= global_p80[t])
                     AND (n_legs_before[t] >= min_legs_global)
                     AND NOT pathological[t]

    Warmup gate is part of the formula (fix D1) — not only in decision branch.
    `p80_quantile` is baked into `global_p80[t]` by the upstream statistics
    pipeline, so this function does not take it as a parameter.
    """
    n = int(len(leg_direction))
    assert len(cand_height_pct) == n
    assert len(global_p80) == n
    assert len(n_legs_before) == n
    assert len(pathological) == n

    direction_ok = (np.asarray(leg_direction, dtype=np.int8) != LEG_DIR_UNKNOWN)
    p80_finite = np.isfinite(np.asarray(global_p80, dtype=np.float64))
    cand_finite = np.isfinite(np.asarray(cand_height_pct, dtype=np.float64))
    # NaN-safe >=: np.greater_equal with NaN returns False.  cand_finite &
    # p80_finite guards already exclude NaNs, but we compute explicitly for
    # clarity.
    height_ok = np.greater_equal(
        np.asarray(cand_height_pct, dtype=np.float64),
        np.asarray(global_p80, dtype=np.float64),
    )
    warmup_ok = (np.asarray(n_legs_before, dtype=np.int64) >= int(min_legs_global))
    path_ok = ~np.asarray(pathological, dtype=bool)

    ready_a = direction_ok & p80_finite & cand_finite & height_ok & warmup_ok & path_ok
    return ready_a.astype(bool, copy=False)


def _run_contour_b_fsm(
    legs: List[_PartialLeg],
    snapshots: List[_LegStatsSnapshot],
    confirm_heights_global_median: List[float],
    n_bars: int,
    open_ratio: float,
    close_ratio: float,
    local_k: int,
) -> np.ndarray:
    """
    Contour B (latched FSM over confirmed legs) readiness (RFC v3.1 §4.1, §5.6).

    On each confirm_bar c_i of leg i:
        ratio = local_median_k / global_median
        if ratio >= open_ratio   → ready_B := True
        elif ratio < close_ratio → ready_B := False
        else                      → ready_B unchanged

    Broadcast:
        ready_B[t] := latest ready_B set on any confirm_bar c_i <= t.
        False before the first confirm that evaluates the rule (or when the
        first ratio is not finite).

    NOT reset on session_reset (fix N-14) — FSM lives across calendar days.

    Parameters
    ----------
    legs : list of _PartialLeg, ordered by confirm_bar ascending.
    snapshots : list of _LegStatsSnapshot, aligned 1:1 with `legs`.
        snapshot[i] holds the pre-leg-i global_median / local_median (per §1.3
        causality).  For Contour B the ratio is computed from these "at
        confirm" snapshot values.  NOTE: snapshot.local_median is NaN when
        `n_legs_before < k_local`; in that case the ratio is NaN and the
        `unchanged` branch fires (ready_B stays at previous value, initially
        False).
    confirm_heights_global_median : list of float, len == len(legs)
        Reserved for future use (currently unused — API shape accepts it for
        forward-compat with a potential post-confirm median variant).
        Not used in Phase 3.
    """
    _ = confirm_heights_global_median  # placeholder, unused
    assert len(legs) == len(snapshots)

    ready_b_arr = np.zeros(int(n_bars), dtype=bool)
    if n_bars <= 0 or not legs:
        return ready_b_arr

    # Walk legs in confirm_bar order.  Between confirm_bars we hold whatever
    # state was set on the last confirm.  Initial state = False.
    cur_state: bool = False

    # Bar pointer — next un-written bar index.
    write_from: int = 0
    L = len(legs)

    for i in range(L):
        leg = legs[i]
        snap = snapshots[i]
        c_bar = int(leg.confirm_bar)
        if c_bar < 0 or c_bar >= n_bars:
            continue

        # Broadcast prior state onto bars [write_from, c_bar) — exclusive of c_bar:
        # ready_B changes can only be observed STARTING from the confirm_bar
        # (not before it).  Strictly speaking §4.1 says the FSM updates on
        # confirm_bar; the new value is visible FROM c_bar onwards.
        if write_from < c_bar:
            ready_b_arr[write_from:c_bar] = cur_state

        # Evaluate FSM rule at c_bar using snapshot values (local_median_k
        # / global_median taken "at confirm" per §1.3).
        g_med = float(snap.global_median)
        l_med = float(snap.local_median)
        if (math.isfinite(g_med) and math.isfinite(l_med) and g_med > 0.0):
            ratio = l_med / g_med
            if ratio >= float(open_ratio):
                cur_state = True
            elif ratio < float(close_ratio):
                cur_state = False
            # else: unchanged
        # If ratio not finite (e.g. local_median NaN because n_legs_before <
        # local_k), fall through: cur_state unchanged.

        # Write cur_state starting at c_bar.
        write_from = c_bar

    # Tail: broadcast last state to the remaining bars.
    if write_from < n_bars:
        ready_b_arr[write_from:] = cur_state

    return ready_b_arr


def _unified_armament_fsm(
    legs: List[_PartialLeg],
    pass_result: _ZigZagPassResult,
    global_p80: np.ndarray,
    n_legs_before: np.ndarray,
    ready_a: np.ndarray,
    ready_b: np.ndarray,
    enabled_a: bool,
    enabled_b: bool,
    st_trend: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    min_legs_global: int,
    arm_timeout_bars_since_extreme: int,
    arm_timeout_bars_hard: int,
) -> Tuple[List["_LegArmamentInfo"], "_ArmamentPerBarArrays", np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Unified armament FSM — replaces legacy `_run_armament_state_machine`.

    Implements RFC v3.1 §4.2 within-bar ordering (12 steps) plus §4.3–§4.7
    semantics:

    - Armament can start **pre-confirm** under any enabled contour (§4.4).
    - `arm_source` ∈ {NONE, A, B, BOTH} reflects which contour(s) gated the
      armament creation (§4.1, §5.4).
    - One-shot is tied to the **candidate** leg (§5.5): it is cleared on
      leg_direction change (confirm-emitted elsewhere), on session_reset,
      and on structural_reset.
    - `FIRED_NO_NEW_PIVOT` fires ONLY on a cand_leg_id change that is not
      simultaneously the confirm of the owning candidate (§4.6 fix D3).
    - `FIRED_NO_REGIME_OFF` fires on a confirm_bar where ready_B transitions
      True→False, for sessions with `arm_source ∈ {B, BOTH}` (§4.5, §8.3.5/8 fix D8).
    - `FIRED_SESSION_RESET` fires on both session_reset_event and
      structural_reset_event (§4.5, §8.3.9 — reuse, no new enum).
    - Decision snapshot (`armed_for_decision` / `arm_source_for_decision`)
      captures the armed state BEFORE the YES_SHOT disarm of the current bar
      (§5.4 fix D6).

    Returns a 6-tuple:
        (leg_armament_infos, per_bar_arrays,
         arm_source_runtime, arm_source_for_decision,
         regime_off_disarm_on_bar, disarm_event)
    where `leg_armament_infos` mirrors the legacy `_LegArmamentInfo` list
    (one entry per leg) and `per_bar_arrays` is a `_ArmamentPerBarArrays`.
    Post-confirm semantics (§7.5): `arm_bar`, `shot_bar`, `fired` on the
    LegRecord reflect ONLY post-confirm armament cycles.  Pre-confirm data
    is preserved internally here but not yet surfaced on LegRecord (that is
    Phase 5 scope).
    """
    N = int(len(st_trend))
    assert len(high) == N and len(low) == N
    assert len(ready_a) == N and len(ready_b) == N
    assert len(global_p80) == N and len(n_legs_before) == N

    path = pass_result.pathological
    session_reset = pass_result.session_reset_event
    struct_reset = pass_result.structural_reset_event
    leg_direction = pass_result.leg_direction
    cand_leg_id_arr = pass_result.zz_cand_leg_id

    # Per-bar arrays (legacy-shaped)
    armed = np.zeros(N, dtype=bool)
    armed_side = np.full(N, ARMED_SIDE_NONE, dtype=np.int8)
    n_bars_since_extreme = np.full(N, -1, dtype=np.int64)
    n_bars_since_arm = np.full(N, -1, dtype=np.int64)
    one_shot = np.zeros(N, dtype=bool)

    timeout_on_bar = np.zeros(N, dtype=bool)
    new_pivot_disarm_on_bar = np.zeros(N, dtype=bool)
    st_flip_on_bar = np.zeros(N, dtype=bool)

    armed_for_decision = np.zeros(N, dtype=bool)
    armed_side_for_decision = np.full(N, ARMED_SIDE_NONE, dtype=np.int8)

    # New per-bar arrays (RFC v3.1)
    arm_source_runtime = np.full(N, ARM_SRC_NONE, dtype=np.int8)
    arm_source_for_decision = np.full(N, ARM_SRC_NONE, dtype=np.int8)
    # B-deactivation disarm flag (surfaces as §7.3 branch 7 zz_regime_off).
    regime_off_disarm_on_bar = np.zeros(N, dtype=bool)
    # Per-bar mirror of FIRED_* (zz_disarm_event, §7.1).
    disarm_event = np.full(N, DISARM_EVT_NONE, dtype=np.int8)

    # Per-leg outputs (legacy — post-confirm armament cycle ONLY).
    leg_outs: List[_LegArmamentInfo] = [
        _LegArmamentInfo(
            armed_side=ARMED_SIDE_NONE,
            arm_bar=-1,
            fired=FIRED_NONE,
            shot_bar=-1,
        ) for _ in legs
    ]

    # cand_leg_id → owning leg_index lookup (§4.3).  Populated lazily as
    # legs confirm; used by NO_NEW_PIVOT skip rule (§4.6 fix D3).
    cand_to_leg: dict = {}
    for li, lg in enumerate(legs):
        cid = int(lg.cand_leg_id_at_confirm)
        if cid >= 0:
            cand_to_leg[cid] = li

    # RFC v3.1 §7.5 / fix B-02: pre-confirm armament session ledger.
    # Keyed by cur_session_cand_id → (pre_confirm_arm_bar, arm_source,
    # pre_confirm_shot_bar).  Populated on pre-confirm armament
    # creation (shot_bar = -1 until observed); updated on pre-confirm
    # YES_SHOT in step 11 (RP-1 Block 1-C); flushed onto leg_outs
    # after the per-bar loop finishes.  Keeps Phase 5 plumbing purely
    # additive without touching Phase 3 armament semantics.
    #
    # First-write-wins on BOTH arm_bar and shot_bar — the first complete
    # pre-confirm arm→shot cycle within a candidate's window is what
    # gets surfaced on the owning LegRecord.  Subsequent arm→shot
    # cycles for the same candidate (only reachable through
    # session_reset / struct_reset mid-candidate) are intentionally
    # discarded so §8.3.10 remains a clean equivalence on the owning
    # leg.
    pre_confirm_session_by_cand: dict = {}

    # Mutable armed-session state
    cur_armed = False
    cur_side = ARMED_SIDE_NONE
    cur_arm_source = ARM_SRC_NONE
    cur_session_cand_id = -1   # cand_leg_id at arm_bar_session (§4.3 linkage)
    cur_owning_leg_idx = -1    # -1 until owning leg confirms (post-confirm it's known)
    cur_arm_bar = -1           # bar where the armed-session started
    cur_is_pre_confirm = False # True iff session started strictly before owning confirm
    cur_ext_price = float("nan")
    cur_ext_bar = -1
    cur_n_extreme = -1
    cur_n_arm = -1
    cur_one_shot = False  # belongs to the CURRENT candidate leg

    # Previous leg_direction (for one_shot reset on direction change).
    prev_leg_dir = LEG_DIR_UNKNOWN
    # Previous ready_b (for FSM deactivation detection at confirm_bar).
    prev_ready_b = False

    leg_iter_idx = 0
    L = len(legs)

    def _record_leg_armament_post_confirm(
        leg_idx: int, side: int, arm_bar: int, arm_source: int,
    ) -> None:
        """Post-confirm armament: fill legacy arm_side/arm_bar + Phase 5 arm_source."""
        if 0 <= leg_idx < L:
            prev = leg_outs[leg_idx]
            leg_outs[leg_idx] = _LegArmamentInfo(
                armed_side=side,
                arm_bar=arm_bar,
                fired=prev.fired,
                shot_bar=prev.shot_bar,
                arm_source=arm_source,
                armed_by_candidate=prev.armed_by_candidate,
                pre_confirm_arm_bar=prev.pre_confirm_arm_bar,
                pre_confirm_shot_bar=prev.pre_confirm_shot_bar,
            )

    def _record_leg_disarm(leg_idx: int, fired_code: int, shot_bar: int) -> None:
        """Post-confirm disarm: fill legacy fired/shot_bar for the leg (Phase 5 fields preserved)."""
        if 0 <= leg_idx < L:
            prev = leg_outs[leg_idx]
            leg_outs[leg_idx] = _LegArmamentInfo(
                armed_side=prev.armed_side,
                arm_bar=prev.arm_bar,
                fired=fired_code,
                shot_bar=shot_bar,
                arm_source=prev.arm_source,
                armed_by_candidate=prev.armed_by_candidate,
                pre_confirm_arm_bar=prev.pre_confirm_arm_bar,
                pre_confirm_shot_bar=prev.pre_confirm_shot_bar,
            )

    def _disarm_session(fired_code: int, shot_bar: int, bar_t: int) -> None:
        """Close the armed-session: record disarm on owning leg if known, reset state."""
        nonlocal cur_armed, cur_side, cur_arm_source, cur_session_cand_id
        nonlocal cur_owning_leg_idx, cur_arm_bar, cur_is_pre_confirm
        nonlocal cur_ext_price, cur_ext_bar, cur_n_extreme, cur_n_arm, cur_one_shot

        # Post-confirm disarm data is written to the owning leg only if the
        # armament cycle is a post-confirm cycle (arm_bar_session >= owning
        # confirm_bar).  Pre-confirm disarms stay internal in Phase 3 and are
        # not surfaced on LegRecord (that is Phase 5 scope).
        if (not cur_is_pre_confirm) and cur_owning_leg_idx >= 0:
            _record_leg_disarm(cur_owning_leg_idx, fired_code, shot_bar)
        # Per-bar event flag mirror (diagnostic, §7.1).
        if 0 <= bar_t < N:
            disarm_event[bar_t] = int(fired_code)
        cur_armed = False
        cur_side = ARMED_SIDE_NONE
        cur_arm_source = ARM_SRC_NONE
        cur_session_cand_id = -1
        cur_owning_leg_idx = -1
        cur_arm_bar = -1
        cur_is_pre_confirm = False
        cur_ext_price = float("nan")
        cur_ext_bar = -1
        cur_n_extreme = -1
        cur_n_arm = -1
        # one_shot: True on disarm, clears on (a) leg_direction change,
        # (b) session_reset, (c) structural_reset (§5.5).
        cur_one_shot = True

    for t in range(N):
        # =========================================================
        # §4.2 step 1: session_reset handling (runs BEFORE pathological)
        # =========================================================
        if bool(session_reset[t]):
            if cur_armed:
                _disarm_session(FIRED_SESSION_RESET, shot_bar=-1, bar_t=t)
            cur_one_shot = False  # §5.7

        # =========================================================
        # §4.2 step 2: structural_reset handling
        # =========================================================
        if bool(struct_reset[t]):
            if cur_armed:
                # Reuse FIRED_SESSION_RESET per §4.5 / §8.3.9.
                _disarm_session(FIRED_SESSION_RESET, shot_bar=-1, bar_t=t)
            cur_one_shot = False  # §5.5 (c)

        # =========================================================
        # §4.2 step 3: pathological — freeze, skip the rest
        # =========================================================
        if bool(path[t]):
            if cur_armed:
                armed[t] = True
                armed_side[t] = cur_side
                arm_source_runtime[t] = cur_arm_source
                n_bars_since_extreme[t] = cur_n_extreme
                n_bars_since_arm[t] = cur_n_arm
            one_shot[t] = cur_one_shot
            armed_for_decision[t] = cur_armed
            armed_side_for_decision[t] = cur_side if cur_armed else ARMED_SIDE_NONE
            arm_source_for_decision[t] = cur_arm_source if cur_armed else ARM_SRC_NONE
            prev_leg_dir = int(leg_direction[t])
            # ready_B not updated on pathological (no confirm possible there).
            continue

        # =========================================================
        # §4.2 step 4: confirm-bar pivot processing
        # =========================================================
        confirm_here = (leg_iter_idx < L and int(legs[leg_iter_idx].confirm_bar) == t)
        confirming_leg_idx = -1
        confirming_prev_cand_id = -1
        if confirm_here:
            confirming_leg_idx = leg_iter_idx
            # The cand_leg_id that materialised into this confirmed leg.
            confirming_prev_cand_id = int(legs[leg_iter_idx].cand_leg_id_at_confirm)
            leg_iter_idx += 1

        # cand_leg_id change detection (for NO_NEW_PIVOT — §4.6).
        cur_cand_id = int(cand_leg_id_arr[t])
        cand_changed = (cur_armed
                        and cur_session_cand_id >= 0
                        and cur_cand_id != cur_session_cand_id)
        # Fix D3: if the cand_id change is due to our session's own candidate
        # confirming (i.e. the confirming_leg's prev-cand == session cand),
        # the armed-session becomes post-confirm on the owning leg and does
        # NOT get NO_NEW_PIVOT-disarmed.  Instead the session transitions
        # from pre-confirm to post-confirm state.
        owns_the_confirming_leg = (confirm_here
                                   and cur_armed
                                   and confirming_prev_cand_id == cur_session_cand_id
                                   and cur_owning_leg_idx < 0)

        if cand_changed and not owns_the_confirming_leg:
            _disarm_session(FIRED_NO_NEW_PIVOT, shot_bar=-1, bar_t=t)
            new_pivot_disarm_on_bar[t] = True

        # If our session's candidate just confirmed, link it as owning leg
        # and transition the armament cycle from pre-confirm to post-confirm
        # (RFC v3.1 §4.7 fix D4 / RP-1 Block 1-A).
        #
        # Legacy arm_bar on the owning leg stays -1 (§7.5: legacy arm_bar is
        # set only via _record_leg_armament_post_confirm on a NEW post-confirm
        # armament; the pre-confirm arm_bar is preserved in
        # pre_confirm_arm_bar via the flush at the end of the FSM loop).
        #
        # Rolling cur_session_cand_id to the post-flip cand prevents a
        # spurious FIRED_NO_NEW_PIVOT on subsequent bars where
        # cur_cand_id != old_session_cand_id (pre-flip).  After the
        # transition the session is anchored by cur_owning_leg_idx, and
        # the next NO_NEW_PIVOT firing will be the *next* confirm of a
        # different leg (whose confirming_prev_cand_id != cur_cand_id).
        if owns_the_confirming_leg and confirming_leg_idx >= 0:
            cur_owning_leg_idx = confirming_leg_idx
            cur_is_pre_confirm = False
            cur_session_cand_id = cur_cand_id

        # =========================================================
        # §4.2 step 5: one_shot reset on leg_direction change
        # =========================================================
        cur_leg_dir_t = int(leg_direction[t])
        if cur_leg_dir_t != prev_leg_dir and cur_leg_dir_t != LEG_DIR_UNKNOWN:
            cur_one_shot = False

        # =========================================================
        # §4.2 step 6: update ready_B FSM — already precomputed in ready_b[t]
        # §4.2 step 7: B-deactivation → disarm arm_source ∈ {B, BOTH}
        # =========================================================
        # B-deactivation is observed as a True→False transition on any bar
        # (not only confirm_bars).  ready_B by construction only changes on
        # confirm_bars (§8.3.4), so this fires there in practice.
        b_deactivated_now = (enabled_b and prev_ready_b and not bool(ready_b[t]))
        if b_deactivated_now and cur_armed and cur_arm_source in (ARM_SRC_B, ARM_SRC_BOTH):
            _disarm_session(FIRED_NO_REGIME_OFF, shot_bar=-1, bar_t=t)
            regime_off_disarm_on_bar[t] = True

        # =========================================================
        # §4.2 step 8: compute readiness_on[t]
        # =========================================================
        ra = bool(ready_a[t]) if enabled_a else False
        rb = bool(ready_b[t]) if enabled_b else False
        readiness_on_t = ra or rb

        # =========================================================
        # §4.2 step 9: armament creation
        # =========================================================
        cand_side = ARMED_SIDE_NONE
        if cur_leg_dir_t == LEG_DIR_UP:
            cand_side = ARMED_SIDE_SHORT
        elif cur_leg_dir_t == LEG_DIR_DOWN:
            cand_side = ARMED_SIDE_LONG

        # Check §4.4 preconditions.
        can_arm = (
            readiness_on_t
            and cand_side != ARMED_SIDE_NONE
            and (not cur_armed)
            and (not cur_one_shot)
            and (not bool(session_reset[t]))
            and (not bool(struct_reset[t]))
            and cur_cand_id >= 0
        )
        if can_arm:
            cur_armed = True
            cur_side = cand_side
            # arm_source per §4.1 / §4.4.
            if ra and rb:
                cur_arm_source = ARM_SRC_BOTH
            elif ra:
                cur_arm_source = ARM_SRC_A
            else:
                cur_arm_source = ARM_SRC_B
            cur_session_cand_id = cur_cand_id
            cur_arm_bar = t
            # Pre/post-confirm classification (RFC v3.1 §4.7 fix D4 / RP-1
            # Block 1-A).  A NEW armament on a confirm_bar naturally binds
            # to the just-confirmed leg as the owning leg; everywhere else
            # it is pre-confirm.
            #
            # Historical note: earlier versions compared
            # confirming_prev_cand_id (pre-flip cand that materialised into
            # the confirmed leg) against the *post-switch* cur_cand_id
            # (cand_leg_id_arr[t] at a confirm_bar already holds the new
            # candidate id because _confirmed_zigzag_pass increments
            # cur_cand_leg_id inside the confirm block).  Those two values
            # are never equal on a valid confirm_bar, so the predicate
            # always evaluated False and every on-confirm armament was
            # mis-classified as pre-confirm (baseline 0.B.2 finding F-1).
            # This is closed here by using confirm_here directly.
            is_post_confirm = bool(confirm_here)
            cur_is_pre_confirm = not is_post_confirm
            if is_post_confirm:
                cur_owning_leg_idx = confirming_leg_idx
                _record_leg_armament_post_confirm(
                    cur_owning_leg_idx, cur_side, t, cur_arm_source,
                )
            else:
                cur_owning_leg_idx = -1  # owning leg does not exist yet
                # Phase 5: remember pre-confirm session metadata keyed by
                # candidate-leg-id.  Later (after the FSM loop) we flush
                # this onto the owning LegRecord so io-layer can do the
                # two-step linkage with correct armed_by_candidate /
                # pre_confirm_arm_bar / arm_source.
                # First-write-wins: if multiple pre-confirm sessions hit the
                # same candidate (can only happen if prior one was disarmed
                # then re-armed via one_shot reset), keep the earliest start.
                if cur_session_cand_id not in pre_confirm_session_by_cand:
                    pre_confirm_session_by_cand[cur_session_cand_id] = (
                        int(cur_arm_bar), int(cur_arm_source), -1,
                    )

            # Initialise armed_leg_extreme from pass-level running extreme (§4.4 fix N-02):
            # - post-confirm: owning leg's end_price is the natural extreme.
            # - pre-confirm: use pass_result last_pivot (running extreme) if available;
            #   falls back to current bar's high/low for the armed side.
            if is_post_confirm and confirming_leg_idx >= 0:
                owning = legs[confirming_leg_idx]
                cur_ext_price = float(owning.end_price)
                cur_ext_bar = int(owning.end_bar)
            else:
                # Pre-confirm: seed from the pass-level last_pivot (the extreme
                # currently being tracked — this is what run_ext_pr holds).
                # Fall back to this bar's extreme price for the armed side.
                if cur_side == ARMED_SIDE_SHORT:
                    cur_ext_price = float(high[t])
                else:
                    cur_ext_price = float(low[t])
                cur_ext_bar = t
            cur_n_extreme = max(0, t - cur_ext_bar)
            cur_n_arm = 0

        # =========================================================
        # §4.2 step 10: decision snapshot (BEFORE ST-flip disarm)
        # =========================================================
        armed_for_decision[t] = cur_armed
        armed_side_for_decision[t] = cur_side if cur_armed else ARMED_SIDE_NONE
        arm_source_for_decision[t] = cur_arm_source if cur_armed else ARM_SRC_NONE

        # =========================================================
        # §4.2 step 11: ST flip → YES_SHOT disarm
        # =========================================================
        if cur_armed:
            if _st_flip_toward(cur_side, st_trend, t):
                # Determine shot_bar recorded on owning leg:
                #  - post-confirm session: shot_bar = t (legacy-ness preserved).
                #  - pre-confirm session: §7.5 keeps the legacy shot_bar == -1;
                #    pre-confirm shot data lives in pre_confirm_shot_bar.  It
                #    is recorded here into the pre-confirm session ledger so
                #    the flush at the end of the FSM loop can propagate it
                #    onto the owning LegRecord (RP-1 Block 1-C).  cand_id must
                #    be captured BEFORE `_disarm_session`, which resets
                #    `cur_session_cand_id` to -1.
                if not cur_is_pre_confirm:
                    _disarm_session(FIRED_YES_SHOT, shot_bar=t, bar_t=t)
                else:
                    pre_shot_cid = int(cur_session_cand_id)
                    _disarm_session(FIRED_YES_SHOT, shot_bar=-1, bar_t=t)
                    entry = pre_confirm_session_by_cand.get(pre_shot_cid)
                    if entry is not None and int(entry[2]) == -1:
                        pre_confirm_session_by_cand[pre_shot_cid] = (
                            int(entry[0]), int(entry[1]), int(t),
                        )
                st_flip_on_bar[t] = True

        # =========================================================
        # §4.2 step 12: timers / extreme update (only if still armed)
        # =========================================================
        if cur_armed and not confirm_here:
            # A. extreme price
            if cur_side == ARMED_SIDE_SHORT:
                h_t = float(high[t])
                if math.isfinite(h_t) and h_t > cur_ext_price:
                    cur_ext_price = h_t
                    cur_ext_bar = t
                    cur_n_extreme = 0
                else:
                    cur_n_extreme += 1
            elif cur_side == ARMED_SIDE_LONG:
                l_t = float(low[t])
                if math.isfinite(l_t) and l_t < cur_ext_price:
                    cur_ext_price = l_t
                    cur_ext_bar = t
                    cur_n_extreme = 0
                else:
                    cur_n_extreme += 1
            # B. bars since arm
            cur_n_arm += 1

            # C. Timeouts — hard first, then soft.
            if cur_n_arm > int(arm_timeout_bars_hard):
                _disarm_session(FIRED_NO_TIMEOUT_HARD, shot_bar=-1, bar_t=t)
                timeout_on_bar[t] = True
            elif cur_n_extreme > int(arm_timeout_bars_since_extreme):
                _disarm_session(FIRED_NO_TIMEOUT_SOFT, shot_bar=-1, bar_t=t)
                timeout_on_bar[t] = True

        # =========================================================
        # Per-bar snapshot writes
        # =========================================================
        if cur_armed:
            armed[t] = True
            armed_side[t] = cur_side
            arm_source_runtime[t] = cur_arm_source
            n_bars_since_extreme[t] = cur_n_extreme
            n_bars_since_arm[t] = cur_n_arm
        one_shot[t] = cur_one_shot

        prev_leg_dir = cur_leg_dir_t
        prev_ready_b = bool(ready_b[t])

    # RFC v3.1 §7.5 / fix B-02: flush pre-confirm session ledger onto owning
    # LegRecords.  A leg whose cand_leg_id_at_confirm matches a recorded
    # pre-confirm session is marked armed_by_candidate=True and receives the
    # pre_confirm_arm_bar.  If no post-confirm armament happened on the same
    # leg (arm_source still NONE), the pre-confirm arm_source is adopted.
    # Post-confirm armament ALWAYS wins the arm_source (legacy precedence).
    for li, lg in enumerate(legs):
        cid = int(lg.cand_leg_id_at_confirm)
        meta = pre_confirm_session_by_cand.get(cid)
        if meta is None:
            continue
        pre_arm_bar, pre_arm_source, pre_shot_bar = meta
        prev = leg_outs[li]
        effective_arm_source = prev.arm_source if prev.arm_source != ARM_SRC_NONE else int(pre_arm_source)
        # pre_confirm_shot_bar: adopt the ledger value iff the core
        # hasn't written one yet (defensive — in Phase 3 nothing
        # writes this field before the flush, but we keep the guard
        # so a future refactor that pre-populates `prev` is not
        # silently clobbered).  The io-layer `_link_trades_to_legs`
        # may OVERWRITE this value later via `dataclasses.replace`
        # when it links an actual trade; that remains the io-layer's
        # two-step linkage responsibility and is out of scope here.
        if prev.pre_confirm_shot_bar != -1:
            effective_pre_shot_bar = int(prev.pre_confirm_shot_bar)
        else:
            effective_pre_shot_bar = int(pre_shot_bar)
        leg_outs[li] = _LegArmamentInfo(
            armed_side=prev.armed_side,
            arm_bar=prev.arm_bar,
            fired=prev.fired,
            shot_bar=prev.shot_bar,
            arm_source=effective_arm_source,
            armed_by_candidate=True,
            pre_confirm_arm_bar=int(pre_arm_bar),
            pre_confirm_shot_bar=effective_pre_shot_bar,
        )

    arr = _ArmamentPerBarArrays(
        armed=armed,
        armed_side=armed_side,
        n_bars_since_extreme=n_bars_since_extreme,
        n_bars_since_arm=n_bars_since_arm,
        one_shot_fired_current_leg=one_shot,
        timeout_expired_on_this_bar=timeout_on_bar,
        new_pivot_disarm_on_this_bar=new_pivot_disarm_on_bar,
        st_flip_on_this_bar=st_flip_on_bar,
        armed_for_decision=armed_for_decision,
        armed_side_for_decision=armed_side_for_decision,
    )
    return (
        leg_outs, arr,
        arm_source_runtime, arm_source_for_decision,
        regime_off_disarm_on_bar, disarm_event,
    )


# ---------------------------------------------------------------------------
# Internal: allow_entry / reason attribution (§1.6 branch ordering, §1.8 safety-net)
# ---------------------------------------------------------------------------


def _compute_allow_entry_and_reason(
    n_bars: int,
    pathological: np.ndarray,
    n_legs_before: np.ndarray,
    leg_direction: np.ndarray,
    armed: np.ndarray,
    armed_side: np.ndarray,
    one_shot: np.ndarray,
    timeout_expired_on_bar: np.ndarray,
    new_pivot_disarm_on_bar: np.ndarray,
    st_flip_on_bar: np.ndarray,
    min_legs_global: int,
    readiness_on: Optional[np.ndarray] = None,
    regime_off_disarm_on_bar: Optional[np.ndarray] = None,
    regime_state: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute allow_entry and reason per bar, applying §7.3 branch ordering (RFC v3.1,
    fix B-04).  Safety-net is applied at the end.

    Branch order (exactly):
       1. pathological[d]                                 → zz_pathological
       2. leg_direction[d] == LEG_DIR_UNKNOWN             → zz_warmup
       3. n_legs_before[d] < min_legs_global              → zz_warmup
       4. NOT readiness_on[d]                             → zz_regime_off   (aggregate)
       5. timeout_expired_on_bar[d]                       → zz_expired_time
       6. new_pivot_disarm_on_bar[d]                      → zz_expired_new_pivot
       7. regime_off_disarm_on_bar[d]   (B-only disarm)   → zz_regime_off
       8. armed[d] == False:
            if one_shot[d]                                → zz_locked_same_leg
            else                                          → zz_not_armed
       9. NOT st_flip_on_bar[d]                           → zz_armed_waiting
      10. ok                                              → allow_entry = True

    Safety-net: `reason == ok AND NOT allow_entry → zz_pathological`.
    Whitelist reasons identical to legacy (no new public reason codes, G-03).

    Backward-compat shims (internal API only):
      - If `readiness_on` is None but legacy `regime_state` is provided, the
        aggregate is synthesised as `regime_state != REGIME_CLOSED` (this
        matches legacy branch §1.6 step 4 exactly).
      - If `regime_off_disarm_on_bar` is None, it defaults to all-False.
    These shims keep older unit tests (that called this function directly
    with `regime_state=`) working without changes.
    """
    del armed_side  # unused — kept in signature for call-site compatibility
    if readiness_on is None:
        if regime_state is None:
            raise TypeError(
                "_compute_allow_entry_and_reason: either readiness_on or "
                "regime_state (legacy backward-compat) must be provided."
            )
        rs = np.asarray(regime_state, dtype=np.int8)
        readiness_on = (rs != REGIME_CLOSED)
    if regime_off_disarm_on_bar is None:
        regime_off_disarm_on_bar = np.zeros(n_bars, dtype=bool)
    allow_entry = np.zeros(n_bars, dtype=bool)
    reason = np.empty(n_bars, dtype=object)

    for d in range(n_bars):
        if bool(pathological[d]):
            reason[d] = FILTER_REASON_ZZ_PATHOLOGICAL
            continue
        if int(leg_direction[d]) == LEG_DIR_UNKNOWN:
            reason[d] = FILTER_REASON_ZZ_WARMUP
            continue
        if int(n_legs_before[d]) < int(min_legs_global):
            reason[d] = FILTER_REASON_ZZ_WARMUP
            continue
        if not bool(readiness_on[d]):
            reason[d] = FILTER_REASON_ZZ_REGIME_OFF
            continue
        if bool(timeout_expired_on_bar[d]):
            reason[d] = FILTER_REASON_ZZ_EXPIRED_TIME
            continue
        if bool(new_pivot_disarm_on_bar[d]):
            reason[d] = FILTER_REASON_ZZ_EXPIRED_NEW_PIVOT
            continue
        if bool(regime_off_disarm_on_bar[d]):
            reason[d] = FILTER_REASON_ZZ_REGIME_OFF
            continue
        if not bool(armed[d]):
            if bool(one_shot[d]):
                reason[d] = FILTER_REASON_ZZ_LOCKED_SAME_LEG
            else:
                reason[d] = FILTER_REASON_ZZ_NOT_ARMED
            continue
        if not bool(st_flip_on_bar[d]):
            reason[d] = FILTER_REASON_ZZ_ARMED_WAITING
            continue
        allow_entry[d] = True
        reason[d] = FILTER_REASON_OK

    blocked_but_ok = (~allow_entry) & (reason == FILTER_REASON_OK)
    if np.any(blocked_but_ok):
        reason[blocked_but_ok] = FILTER_REASON_ZZ_PATHOLOGICAL

    return allow_entry, reason


# ---------------------------------------------------------------------------
# Internal: assemble final LegRecord tuple from partial + snapshots + regime + armament
# ---------------------------------------------------------------------------


def _assemble_leg_records(
    legs: List[_PartialLeg],
    snapshots: List[_LegStatsSnapshot],
    regime_infos: List[_LegRegimeInfo],
    arm_infos: List[_LegArmamentInfo],
) -> Tuple[LegRecord, ...]:
    assert len(legs) == len(snapshots) == len(regime_infos) == len(arm_infos)
    out: List[LegRecord] = []
    for leg, snap, reg, arm in zip(legs, snapshots, regime_infos, arm_infos):
        out.append(LegRecord(
            leg_id=leg.leg_id,
            start_bar=leg.start_bar,
            end_bar=leg.end_bar,
            confirm_bar=leg.confirm_bar,
            start_price=leg.start_price,
            end_price=leg.end_price,
            direction=leg.direction,
            height_pct=leg.height_pct,
            length_bars=leg.length_bars,
            confirm_lag_bars=leg.confirm_lag_bars,
            n_legs_before=snap.n_legs_before,
            global_median_at_confirm=snap.global_median,
            global_p80_at_confirm=snap.global_p80,
            local_median_at_confirm=snap.local_median,
            regime_state_at_confirm=reg.state_at_confirm,
            opened_regime=reg.opened_regime,
            closed_regime=reg.closed_regime,
            is_strong=reg.is_strong,
            armed_side=arm.armed_side,
            arm_bar=arm.arm_bar,
            fired=arm.fired,
            shot_bar=arm.shot_bar,
            trade_id_if_fired=None,   # io-layer responsibility (§3.8.3a)
            # Phase 5 (§7.5): pre-confirm lifecycle plumbing.
            arm_source=int(arm.arm_source),
            armed_by_candidate=bool(arm.armed_by_candidate),
            pre_confirm_arm_bar=int(arm.pre_confirm_arm_bar),
            pre_confirm_shot_bar=int(arm.pre_confirm_shot_bar),
            # Private linkage key (N-01): cand_leg_id captured at confirm.
            _cand_leg_id_at_confirm=int(leg.cand_leg_id_at_confirm),
        ))
    return tuple(out)


# ---------------------------------------------------------------------------
# Public entry point — compute_zigzag_filter
# ---------------------------------------------------------------------------


def compute_zigzag_filter(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    open_prices: np.ndarray,
    session_ids: np.ndarray,
    st_trend: np.ndarray,
    cfg: dict,
) -> ZigZagFilterResult:
    """
    Compute the ZigZag entry filter (decision-bar aligned).

    RFC v3.1 orchestrates entry decisions through a two-contour readiness
    FSM feeding a unified armament state-machine:

    * **Contour A** (§4.1, candidate-based, per-bar stateless):
        ``ready_a[t] = (cand_height_pct[t] >= global_p80[t])
                       AND n_legs_before[t] >= min_legs_global``
      — armament may start **before** a pivot is confirmed.

    * **Contour B** (§4.1, confirmed-regime-based, latched FSM):
        updates only on confirm bars; turns on when
        ``local_median_k / global_median >= open_ratio`` and off when
        that ratio drops below ``close_ratio`` (§4.5 / §8.3.5).

    Effective readiness (§4.2) is the OR of the enabled contours:
    ``readiness_on = (enabled_A AND ready_a) OR (enabled_B AND ready_b)``.

    **Within-bar ordering (§4.2, normative):**
        1. advance ZigZag pass (pivot detection, structural_reset_event).
        2. compute ``ready_a[t]`` (stateless).
        3. if confirm_bar[t], update latched ``ready_b[t]`` and regime
           telemetry.
        4. run unified armament FSM: B-deactivation disarms; then try
           arm / shot / disarm in priority order (§4.3 owning-leg rule;
           §4.6 pre-confirm session survives intervening confirm;
           §4.7 pre-/post-confirm shot with ``armed_by_candidate`` tag).
        5. compute ``allow_entry[t]`` and ``reason[t]`` via §7.3 branch
           ordering (warmup > readiness > timeout > disarm > armed_waiting
           > ok; safety-net `zz_pathological` at the end).

    **Config schema (§6.3 / §6.5, post-normalisation in cli/tester):**

    ::

        filters.zigzag:
          reversal_threshold: float > 0
          min_legs_global: int (warmup floor)
          k_local: int (local median window)
          q_strong: float in (0, 1) (legacy, maps to readiness.contour_a.p80_quantile)
          entry_side: "counter_trend" (only supported)
          arm_timeout_bars_since_extreme: int (soft)
          arm_timeout_bars_hard: int (hard)
          structural_reset_min_span: int (§5.8, pathological-span trigger)
          readiness:
            contour_a: {enabled: bool, p80_quantile: float}
            contour_b: {enabled: bool, local_k: int,
                        open_ratio: float, close_ratio: float}

    If the ``readiness`` block is absent (legacy cfg), ``cli/tester``
    auto-migrates to ``A=on, B=off`` with a deprecation warning (§6.4).

    **Diagnostics (§7.1) returned on ``ZigZagFilterResult``:**

        - existing (v2.0): ``leg_direction, cand_height_pct,
          last_pivot_price, last_pivot_bar_idx, global_median, global_p80,
          local_median, n_legs_before, regime_state,
          n_legs_since_regime_open, armed, armed_side,
          n_bars_since_extreme, n_bars_since_arm``.
        - new (v3.1): ``ready_a, ready_b, readiness_on, arm_source,
          arm_source_for_decision, cand_leg_id, readiness_block_reason,
          disarm_event, structural_reset_event``.

    ``zz_cand_leg_id`` is monotonic across session boundaries and is
    resolved to ``LegRecord._cand_leg_id_at_confirm`` at confirm-time for
    two-step trade↔leg linkage (§7.6, fix N-01).

    Parameters
    ----------
    high, low, close, open_prices : np.ndarray, shape (N,), float64
    session_ids : np.ndarray, shape (N,), int64
        Calendar-day session identifiers (§1.7).  Session boundaries
        trigger SESSION_RESET disarm but do NOT reset latched ``ready_b``
        (fix N-14).
    st_trend : np.ndarray, shape (N,), int8 {-1, 0, +1}
        SuperTrend direction from ``compute_trend_only`` (SSOT §3.4.0).
    cfg : dict
        Validated ``filters.zigzag`` section (schema above).

    Returns
    -------
    ZigZagFilterResult
        All per-bar arrays are decision-bar aligned (§1.6).  The single
        decision→execution shift is performed only in
        ``apply_entry_filters`` (``core/backtest.py``).

    Raises
    ------
    ValueError
        If ``entry_side != "counter_trend"`` (§9: only counter_trend
        implemented).
    """
    # --- Coerce inputs ---
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    open_prices = np.asarray(open_prices, dtype=np.float64)
    session_ids = np.asarray(session_ids, dtype=np.int64)
    st_trend = np.asarray(st_trend, dtype=np.int8)
    N = int(len(high))
    assert len(low) == len(close) == len(open_prices) == len(session_ids) == N
    assert len(st_trend) == N, "st_trend length must equal N"

    # --- cfg parsing (fail-closed on missing keys; default where possible) ---
    reversal_threshold = float(cfg["reversal_threshold"])
    min_legs_global = int(cfg.get("min_legs_global", 50))
    k_local = int(cfg.get("k_local", 5))
    arm_timeout_extreme = int(cfg.get("arm_timeout_bars_since_extreme", 24))
    arm_timeout_hard = int(cfg.get("arm_timeout_bars_hard", 78))
    # q_strong: configurable percentile for global_p80 / strong-leg test (§2).
    _q_strong = float(cfg.get("q_strong", 0.80))
    # entry_side: only "counter_trend" is implemented (plan §2, §9).
    entry_side = str(cfg.get("entry_side", "counter_trend"))
    if entry_side != "counter_trend":
        raise ValueError(
            f"compute_zigzag_filter: entry_side={entry_side!r} not supported; "
            f"only 'counter_trend' is implemented (plan §2, §9)."
        )

    # --- RFC v3.1 readiness block parsing (§6.3, §6.5) ---
    # Defaults match cli/tester._validate_zigzag_section normalised output.
    # Structural reset span for §5.8 (fix B-03).
    structural_reset_min_span = int(cfg.get("structural_reset_min_span", 3))
    _readiness_cfg = cfg.get("readiness", {}) or {}
    _contour_a_cfg = _readiness_cfg.get("contour_a", {}) or {}
    _contour_b_cfg = _readiness_cfg.get("contour_b", {}) or {}
    enabled_a: bool = bool(_contour_a_cfg.get("enabled", True))
    enabled_b: bool = bool(_contour_b_cfg.get("enabled", False))
    # p80_quantile overrides q_strong when present (migration rule §6.4.2).
    # cli validator sets readiness.contour_a.p80_quantile in the normalised
    # cfg; if absent (e.g. direct call by tests using only legacy cfg), fall
    # back to q_strong.
    p80_quantile = float(_contour_a_cfg.get("p80_quantile", _q_strong))
    b_local_k = int(_contour_b_cfg.get("local_k", 5))
    b_open_ratio = float(_contour_b_cfg.get("open_ratio", 1.5))
    b_close_ratio = float(_contour_b_cfg.get("close_ratio", 1.0))

    # --- Empty N == 0 short-circuit ---
    if N == 0:
        return ZigZagFilterResult(
            allow_entry=np.zeros(0, dtype=bool),
            reason=np.empty(0, dtype=object),
            leg_direction=np.zeros(0, dtype=np.int8),
            cand_height_pct=np.zeros(0, dtype=np.float64),
            last_pivot_price=np.zeros(0, dtype=np.float64),
            last_pivot_bar_idx=np.zeros(0, dtype=np.int64),
            global_median=np.zeros(0, dtype=np.float64),
            global_p80=np.zeros(0, dtype=np.float64),
            local_median=np.zeros(0, dtype=np.float64),
            n_legs_before=np.zeros(0, dtype=np.int64),
            regime_state=np.zeros(0, dtype=np.int8),
            n_legs_since_regime_open=np.zeros(0, dtype=np.int64),
            armed=np.zeros(0, dtype=bool),
            armed_side=np.zeros(0, dtype=np.int8),
            n_bars_since_extreme=np.zeros(0, dtype=np.int64),
            n_bars_since_arm=np.zeros(0, dtype=np.int64),
            legs=tuple(),
        )

    # --- Pass 1: causal ZigZag (§1.1 + §1.2 + §1.7 + §1.8 + §5.8) ---
    pass_result = _confirmed_zigzag_pass(
        high=high, low=low, open_prices=open_prices,
        session_ids=session_ids, reversal_threshold=reversal_threshold,
        structural_reset_min_span=structural_reset_min_span,
    )
    legs = pass_result.legs

    # --- Pass 2: causal statistics (§1.3) — per leg snapshots + per bar ---
    # p80_quantile is the effective percentile for Contour A (RFC v3.1 §4.1),
    # mapped from readiness.contour_a.p80_quantile with q_strong fallback.
    snapshots = _build_causal_statistics(legs, k_local=k_local, q_strong=p80_quantile)
    g_median, g_p80, l_median, n_before = _broadcast_stats_to_bars(
        legs, snapshots, n_bars=N, k_local=k_local, q_strong=p80_quantile,
    )

    # --- Pass 3: regime state machine (§1.4 + §1.4a step 3) — telemetry-only (§5.6) ---
    # Legacy regime_state is retained for backward-compat on LegRecord and
    # zz_regime_state diagnostics; decision-layer no longer consumes it.
    regime_infos = _run_regime_state_machine(legs, snapshots, k_local=k_local)
    regime_state_arr, n_legs_since_open_arr = _broadcast_regime_to_bars(
        legs, regime_infos, n_bars=N,
    )

    # --- Pass 4a: Contour A readiness (RFC v3.1 §4.1, fix D1) ---
    ready_a = _compute_ready_a_array(
        leg_direction=pass_result.leg_direction,
        cand_height_pct=pass_result.cand_height_pct,
        global_p80=g_p80,
        n_legs_before=n_before,
        pathological=pass_result.pathological,
        min_legs_global=min_legs_global,
    )

    # --- Pass 4b: Contour B latched FSM readiness (RFC v3.1 §4.1) ---
    ready_b = _run_contour_b_fsm(
        legs=legs,
        snapshots=snapshots,
        confirm_heights_global_median=[],
        n_bars=N,
        open_ratio=b_open_ratio,
        close_ratio=b_close_ratio,
        local_k=b_local_k,
    )

    # --- Pass 4c: unified armament FSM (RFC v3.1 §4.2–§4.7) ---
    (arm_infos, arm_arrays,
     arm_source_runtime, arm_source_for_decision,
     regime_off_disarm_on_bar, disarm_event) = _unified_armament_fsm(
        legs=legs,
        pass_result=pass_result,
        global_p80=g_p80,
        n_legs_before=n_before,
        ready_a=ready_a,
        ready_b=ready_b,
        enabled_a=enabled_a,
        enabled_b=enabled_b,
        st_trend=st_trend,
        high=high,
        low=low,
        min_legs_global=min_legs_global,
        arm_timeout_bars_since_extreme=arm_timeout_extreme,
        arm_timeout_bars_hard=arm_timeout_hard,
    )

    # --- Pass 5: aggregate readiness and decision (§7.3) ---
    # readiness_on[t] = (enabled_A AND ready_A[t]) OR (enabled_B AND ready_B[t])
    eff_ready_a = (ready_a & bool(enabled_a)) if enabled_a else np.zeros(N, dtype=bool)
    eff_ready_b = (ready_b & bool(enabled_b)) if enabled_b else np.zeros(N, dtype=bool)
    readiness_on = (eff_ready_a | eff_ready_b).astype(bool, copy=False)

    allow_entry, reason = _compute_allow_entry_and_reason(
        n_bars=N,
        pathological=pass_result.pathological,
        n_legs_before=n_before,
        leg_direction=pass_result.leg_direction,
        readiness_on=readiness_on,
        armed=arm_arrays.armed_for_decision,
        armed_side=arm_arrays.armed_side_for_decision,
        one_shot=arm_arrays.one_shot_fired_current_leg,
        timeout_expired_on_bar=arm_arrays.timeout_expired_on_this_bar,
        new_pivot_disarm_on_bar=arm_arrays.new_pivot_disarm_on_this_bar,
        regime_off_disarm_on_bar=regime_off_disarm_on_bar,
        st_flip_on_bar=arm_arrays.st_flip_on_this_bar,
        min_legs_global=min_legs_global,
    )

    # --- Diagnostic: readiness_block_reason[t] (§7.1 fix N-03) ---
    # DIAGNOSTIC ONLY, not in FILTER_REASON_WHITELIST.
    readiness_block_reason = _compute_readiness_block_reason(
        n_bars=N,
        pathological=pass_result.pathological,
        leg_direction=pass_result.leg_direction,
        n_legs_before=n_before,
        min_legs_global=min_legs_global,
        ready_a=ready_a,
        ready_b=ready_b,
        enabled_a=enabled_a,
        enabled_b=enabled_b,
        regime_off_disarm_on_bar=regime_off_disarm_on_bar,
    )

    # --- Assemble final LegRecord tuple ---
    leg_records = _assemble_leg_records(legs, snapshots, regime_infos, arm_infos)

    return ZigZagFilterResult(
        allow_entry=allow_entry,
        reason=reason,
        leg_direction=pass_result.leg_direction,
        cand_height_pct=pass_result.cand_height_pct,
        last_pivot_price=pass_result.last_pivot_price,
        last_pivot_bar_idx=pass_result.last_pivot_bar_idx,
        global_median=g_median,
        global_p80=g_p80,
        local_median=l_median,
        n_legs_before=n_before,
        regime_state=regime_state_arr,
        n_legs_since_regime_open=n_legs_since_open_arr,
        armed=arm_arrays.armed,
        armed_side=arm_arrays.armed_side,
        n_bars_since_extreme=arm_arrays.n_bars_since_extreme,
        n_bars_since_arm=arm_arrays.n_bars_since_arm,
        legs=leg_records,
        # RFC v3.1 additive diagnostics
        ready_a=ready_a,
        ready_b=ready_b,
        readiness_on=readiness_on,
        arm_source=arm_source_runtime,
        arm_source_for_decision=arm_source_for_decision,
        cand_leg_id=pass_result.zz_cand_leg_id,
        readiness_block_reason=readiness_block_reason,
        disarm_event=disarm_event,
        structural_reset_event=pass_result.structural_reset_event,
    )


# ---------------------------------------------------------------------------
# Internal: readiness_block_reason diagnostic (§7.1 fix N-03)
# ---------------------------------------------------------------------------


def _compute_readiness_block_reason(
    n_bars: int,
    pathological: np.ndarray,
    leg_direction: np.ndarray,
    n_legs_before: np.ndarray,
    min_legs_global: int,
    ready_a: np.ndarray,
    ready_b: np.ndarray,
    enabled_a: bool,
    enabled_b: bool,
    regime_off_disarm_on_bar: np.ndarray,
) -> np.ndarray:
    """
    Per-bar diagnostic string describing why `readiness_on[t]` was / wasn't true.

    RFC v3.1 §7.1 (fix N-03).  DIAGNOSTIC ONLY — values are NOT in
    FILTER_REASON_WHITELIST and MUST NOT appear in public filtered_reason[t].

    Value set:
      "ok"                  — readiness_on[t] == True (whichever contour fired)
      "pathological"        — bar frozen
      "warmup"              — leg_direction == UNKNOWN or n_legs_before < min_legs
      "both_disabled"       — A=off, B=off (tester debug mode, §6.6)
      "not_ready_A"         — only A enabled and ready_A[t] == False
      "not_ready_B"         — only B enabled and ready_B[t] == False
      "not_ready_both"      — both enabled, both False
      "disarm_b_regime_off" — B-deactivation disarm fired on t (higher priority)
    """
    out = np.empty(n_bars, dtype=object)
    for d in range(n_bars):
        if bool(pathological[d]):
            out[d] = "pathological"
            continue
        if int(leg_direction[d]) == LEG_DIR_UNKNOWN or int(n_legs_before[d]) < int(min_legs_global):
            out[d] = "warmup"
            continue
        if bool(regime_off_disarm_on_bar[d]):
            out[d] = "disarm_b_regime_off"
            continue
        if not enabled_a and not enabled_b:
            out[d] = "both_disabled"
            continue
        ra = bool(ready_a[d]) if enabled_a else False
        rb = bool(ready_b[d]) if enabled_b else False
        if ra or rb:
            out[d] = "ok"
            continue
        if enabled_a and enabled_b:
            out[d] = "not_ready_both"
        elif enabled_a:
            out[d] = "not_ready_A"
        else:
            out[d] = "not_ready_B"
    return out
