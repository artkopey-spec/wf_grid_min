"""
ZigZag ST trade filter — Phase 1 implementation (WP3 + WP4 + WP5 scope).

This module is the single home of the ZigZag ST filter logic that will, in
later work packages, host:

- causal ZigZag pass with ``zigzag.reversal_threshold`` from config
  (close-only, plan §3.3 / Appendix A v1.1 §3.4);
- confirmed legs;
- candidate-leg height;
- full-dataset ``ZigZagGlobalStats``;
- per-slice rolling ``local_median_N`` over causal slice-local history;
- ST flip detection from ``trend`` (only ``+1 ↔ -1``);
- FSM ``OFF -> WAIT_FIRST_ST_FLIP -> ST_ACTIVE_FREEZE ->
  ST_ACTIVE_MONITORING -> ST_STOPPING -> OFF``;
- stateful construction of ``filtered_positions``;
- per-bar diagnostics arrays;
- optional internal leg records for tests.

What is implemented now
-----------------------
- WP3: full-dataset confirmed legs, ``ZigZagGlobalStats``, init failure
  via ``ConfigError`` before WF execution.
- WP4: causal per-bar engine producing ``candidate_height_pct``,
  ``confirm_event``, ``confirmed_leg_idx_at_t``,
  ``last_confirmed_leg_height_pct``, ``local_median_N`` and
  ``local_median_available`` arrays.
- WP5: FSM (``OFF`` / ``WAIT_FIRST_ST_FLIP`` / ``ST_ACTIVE_FREEZE`` /
  ``ST_ACTIVE_MONITORING`` / ``ST_STOPPING``) and ``apply(...)`` builder
  that produces ``filtered_positions`` directly as a stateful pass.
- WP6: ST flip detection (``detect_st_flip``) and ``OPEN_TO_OPEN`` event
  ordering pinned via dedicated unit tests.  Only ``+1 ↔ -1`` is a
  tradable flip; ``0 -> ±1`` and ``±1 -> 0`` are non-tradable
  initialization / de-init transitions (plan §5.5 / spec §17.14).
  WP6 still deliberately does NOT touch backtest / orchestrator / WF
  runtime, ``calculate_returns``, ``extract_trades`` (read-only pinning
  test only), metrics or ``RawBacktestArtifacts`` — those land in WP7+.

Plan reference:  WP3, WP4, WP5, WP6, §3.3, §5, §5.5, §6, §8.4, §12.
Spec reference:  Appendix A v1.1 §3.1..§3.4, §3.3, §4..§9, §10, §12,
                 §14, §15.1..§15.3, §15.4, §15.7, §16, §17.3..§17.6,
                 §17.7..§17.14.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.core._block_reason import select_block_reason
from supertrend_optimizer.core._reset_events import (
    _infer_daily_reset_event,
    _infer_time_filter_events,
    detect_st_flip,
)
from supertrend_optimizer.core.calculator import calculate_atr_rma, calculate_true_range
from supertrend_optimizer.utils.exceptions import ConfigError

if TYPE_CHECKING:
    from supertrend_optimizer.core.volume_metrics import VolumeRuntime

# Module-level logger (ТЗ v3 §11: INFO once per run for Mode B + enabled gate).
_logger = logging.getLogger(__name__)


__all__ = [
    "ConfirmedLeg",
    "ZigZagGlobalStats",
    "ZigZagPerBar",
    "ZigZagFSMState",
    "ZigZagSTFilterResult",
    "detect_confirmed_legs_close_only",
    "compute_confirmed_legs_reset_aware",
    "build_zigzag_global_stats",
    "compute_zigzag_per_bar",
    "detect_st_flip",
    "apply",
    "attach_trade_filter_diagnostics",
]


# ---------------------------------------------------------------------------
# Dataclasses (plan §3.2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfirmedLeg:
    """Immutable record of a single confirmed ZigZag leg (plan §3.2).

    Causality:
        ``confirm_bar > end_bar`` (the pivot is always in the past once a
        reversal of at least ``reversal_threshold`` is observed).

    Units:
        ``height_pct`` is a fraction of price — NOT a percent in the 0..100
        scale.  Despite the ``_pct`` suffix v1.1 stores all height metrics as
        fractions (Appendix A v1.1 §3.2 last paragraph).
    """
    start_bar: int
    end_bar: int
    confirm_bar: int
    start_price: float
    end_price: float
    direction: int           # +1 (UP) or -1 (DOWN)
    height_pct: float        # fraction of price; > 0


@dataclass(frozen=True)
class ZigZagGlobalStats:
    """Global ZigZag statistics computed once on the full validated dataset.

    Plan §3.2 / §4.3 / §12.  Always produced from the full input series and
    NEVER per-WF-step or train-window only.

    All height / threshold scalars are fractions of price (Appendix A v1.1
    §3.2).  Percent strings are forbidden upstream by config validation.
    """
    reversal_threshold: float
    global_stats_source: str
    leg_height_mode: str
    confirmed_legs: List[ConfirmedLeg]
    confirmed_heights_pct: np.ndarray
    global_median: float
    candidate_trigger_threshold: float
    candidate_trigger_source: str          # "explicit" | "quantile"
    candidate_trigger_quantile: Optional[float]
    n_legs_total: int
    insufficient_data: bool
    fail_closed_reason: Optional[str]
    metadata: dict = field(default_factory=dict)
    # v3 fields — WP-V3-2 (ТЗ v3 §5)
    # Defaults preserve backward compat for all existing callers that do not
    # pass these fields.  Production path (build_zigzag_global_stats) always
    # populates them explicitly.
    zigzag_mode: str = "A"
    candidate_duration_gate_enabled: bool = False
    candidate_duration_max_bars: Optional[int] = None
    # Wakeup Phase 0 fields. Defaults keep legacy construction compatible.
    wakeup_entry_candidate_height_threshold: Optional[float] = None
    wakeup_no_fresh_candidate_height_threshold: Optional[float] = None


@dataclass(frozen=True)
class ZigZagPerBar:
    """Per-bar diagnostic arrays from the causal close-only ZigZag engine.

    Plan §3.2 / WP4.  All arrays are aligned with the input ``close`` series
    (same length).  Heights are stored as fractions, never percents — the
    ``_pct`` suffix preserves cross-WP nomenclature only (Appendix A v1.1
    §3.2 last paragraph).

    Fields
    ------
    candidate_height_pct : np.ndarray (float64)
        Per-bar candidate-leg height (fraction of last confirmed pivot price)
        evaluated at the END of bar ``t`` after any pivot confirmation on the
        same bar (Appendix A v1.1 §3.4.3).  ``NaN`` until the first leg
        direction is established by the close-only ZigZag bootstrap.
    confirm_event : np.ndarray (int8, 0/1)
        ``1`` only on bars where a new confirmed leg was emitted; ``0`` else.
    confirmed_leg_idx_at_t : np.ndarray (int64)
        Index of the most recently confirmed leg whose ``confirm_bar <= t``;
        ``-1`` before any leg is confirmed.
    last_confirmed_leg_height_pct : np.ndarray (float64)
        Height (fraction) of the leg referenced by ``confirmed_leg_idx_at_t``;
        ``NaN`` before any leg is confirmed.
    local_median_N : np.ndarray (float64)
        Median over the last ``N = local_window`` confirmed legs in the
        causal slice-local history (Appendix A v1.1 §6, §15.1).  ``NaN``
        until at least ``local_window`` confirmed legs are available.
        Independent of any FSM lifecycle — WP4 has no notion of lifecycle
        start.
    local_median_available : np.ndarray (bool)
        ``True`` iff ``local_median_N[t]`` is a finite, well-defined value.
    candidate_age_bars : np.ndarray (int64)  [v3 WP-V3-3]
        Age of the current candidate leg: ``t - last_pivot_bar + 1``.
        ``-1`` (UNKNOWN) on pre-bootstrap, reset, pathological and UNKNOWN
        direction bars (ТЗ v3 §6.1, §2).
    candidate_leg_direction : np.ndarray (int8)  [v3 WP-V3-3]
        Direction of the current candidate leg: ``+1`` UP, ``-1`` DOWN,
        ``0`` UNKNOWN.  ``0`` on the same UNKNOWN cases as
        ``candidate_age_bars`` (ТЗ v3 §6.1, §6.2).
    """
    candidate_height_pct: np.ndarray
    confirm_event: np.ndarray
    confirmed_leg_idx_at_t: np.ndarray
    last_confirmed_leg_height_pct: np.ndarray
    local_median_N: np.ndarray
    local_median_available: np.ndarray
    # v3 WP-V3-3 fields (ТЗ v3 §6).  Optional for backward-compat with legacy
    # call sites that construct ZigZagPerBar directly (without going through
    # ``compute_zigzag_per_bar``).  When ``None``, ``apply()`` allocates
    # UNKNOWN-default arrays (age=-1, direction=0); production path always
    # passes real arrays via ``compute_zigzag_per_bar``.
    candidate_age_bars: Optional[np.ndarray] = None
    candidate_leg_direction: Optional[np.ndarray] = None


# ---------------------------------------------------------------------------
# Close-only ZigZag pass (Appendix A v1.1 §3.4 / plan §3.3) — shared engine
# used by both ``detect_confirmed_legs_close_only`` (WP3) and
# ``compute_zigzag_per_bar`` (WP4).
# ---------------------------------------------------------------------------

# Direction constants — internal to this module.
_LEG_DIR_UNKNOWN: int = 0
_LEG_DIR_UP: int = 1
_LEG_DIR_DOWN: int = -1


class _CloseOnlyPassResult(NamedTuple):
    """Internal output of ``_run_close_only_zigzag_pass``.

    Per-bar arrays are length ``len(close)``.  ``legs`` is the chronological
    list of confirmed legs.  All height metrics are fractions.
    """
    legs: List[ConfirmedLeg]
    candidate_height_pct: np.ndarray
    confirm_event: np.ndarray
    confirmed_leg_idx_at_t: np.ndarray
    last_confirmed_leg_height_pct: np.ndarray
    # v3 WP-V3-3 fields
    candidate_age_bars: np.ndarray    # int64; -1 = UNKNOWN
    candidate_leg_direction: np.ndarray  # int8; 0 = UNKNOWN, +1 = UP, -1 = DOWN


def _validate_close_and_threshold(
    close: np.ndarray, reversal_threshold: float, *, fn_name: str
) -> tuple[Optional[np.ndarray], float]:
    """Shared input validation used by WP3 / WP4 entry points.

    Returns ``(arr, r)`` if the inputs are usable, or ``(None, r)`` if the
    close array is empty / 1-bar / non-positive seed (in which case both WP3
    and WP4 return empty / NaN-filled outputs without raising).

    A bad ``reversal_threshold`` is always a hard ``ConfigError``; config
    validation should have caught this upstream but the helper must not
    silently produce phantom legs.
    """
    r = float(reversal_threshold)
    if not math.isfinite(r) or r <= 0.0 or r >= 1.0:
        raise ConfigError(
            f"{fn_name} requires reversal_threshold in (0, 1), "
            f"got {reversal_threshold!r}"
        )

    if close is None:
        return None, r
    arr = np.asarray(close)
    if arr.ndim != 1 or arr.size == 0:
        return None, r
    return arr, r


def _run_close_only_zigzag_pass(
    close: np.ndarray,
    reversal_threshold: float,
    *,
    daily_reset_event: "Optional[np.ndarray]" = None,
) -> _CloseOnlyPassResult:
    """Single causal close-only ZigZag pass.

    Implements the formula from Appendix A v1.1 §3.4 / plan §3.3 and emits
    the per-bar arrays needed by both the WP3 confirmed-leg helper and the
    WP4 per-bar engine.  Sharing this single pass guarantees the WP3, WP4
    and (future) WP5 fixtures see exactly the same legs / heights / confirm
    bars.

    Inputs are assumed already validated (see
    ``_validate_close_and_threshold``).  This helper only handles the case
    where ``close`` is a non-empty 1-D array; the caller short-circuits the
    empty case.

    All per-bar arrays are end-of-bar snapshots — i.e. on a confirm bar
    ``t``, the leg from ``last_pivot`` to ``run_ext_bar`` is emitted FIRST,
    a new candidate leg is started, and the per-bar fields reflect the new
    candidate (this matches the donor reference convention and keeps
    contour-A / contour-B semantics consistent).
    """
    arr = np.asarray(close)
    n = arr.shape[0]
    r = float(reversal_threshold)

    candidate_height_pct = np.full(n, np.nan, dtype=np.float64)
    confirm_event = np.zeros(n, dtype=np.int8)
    confirmed_leg_idx_at_t = np.full(n, -1, dtype=np.int64)
    last_confirmed_leg_height_pct = np.full(n, np.nan, dtype=np.float64)
    # v3 WP-V3-3: candidate age and direction arrays
    candidate_age_bars = np.full(n, -1, dtype=np.int64)
    candidate_leg_direction = np.zeros(n, dtype=np.int8)

    legs: List[ConfirmedLeg] = []

    c0 = float(arr[0])
    if not math.isfinite(c0) or c0 <= 0.0:
        # Without a positive seed the first-pivot bootstrap cannot start;
        # the per-bar arrays remain at their NaN / -1 / 0 defaults.
        return _CloseOnlyPassResult(
            legs=legs,
            candidate_height_pct=candidate_height_pct,
            confirm_event=confirm_event,
            confirmed_leg_idx_at_t=confirmed_leg_idx_at_t,
            last_confirmed_leg_height_pct=last_confirmed_leg_height_pct,
            candidate_age_bars=candidate_age_bars,
            candidate_leg_direction=candidate_leg_direction,
        )

    # Bootstrap state — running min/max of close while the leg direction is
    # not yet determined.
    run_min_bar = 0
    run_min_price = c0
    run_max_bar = 0
    run_max_price = c0

    direction = _LEG_DIR_UNKNOWN
    last_pivot_bar = 0
    last_pivot_price = c0
    run_ext_bar = 0
    run_ext_price = c0

    last_confirmed_idx = -1
    last_confirmed_height = float("nan")

    # Per-bar snapshot at t=0: direction is UNKNOWN → no candidate yet.
    # All defaults (NaN / -1 / 0) are already correct for index 0.

    for t in range(1, n):
        c = float(arr[t])

        # Calendar-day boundary is OHLC-agnostic. Even on a NaN/inf bar the
        # boundary is real and operational candidate state must be cleared;
        # otherwise the first valid bar of the new day will not re-seed.
        # Confirmed-leg history intentionally survives the reset (§0.7).
        if daily_reset_event is not None and daily_reset_event[t]:
            direction = _LEG_DIR_UNKNOWN
            if math.isfinite(c) and c > 0.0:
                run_min_bar = t
                run_min_price = c
                run_max_bar = t
                run_max_price = c
                last_pivot_bar = t
                last_pivot_price = c
                run_ext_bar = t
                run_ext_price = c
            else:
                run_min_bar = t
                run_min_price = float("nan")
                run_max_bar = t
                run_max_price = float("nan")
                last_pivot_bar = t
                last_pivot_price = float("nan")
                run_ext_bar = t
                run_ext_price = float("nan")

        if not math.isfinite(c) or c <= 0.0:
            # Pathological close — preserve the previous bar's snapshot in
            # the cumulative fields and skip state mutation.  OHLC
            # validation should make this unreachable in practice.
            confirmed_leg_idx_at_t[t] = last_confirmed_idx
            last_confirmed_leg_height_pct[t] = last_confirmed_height
            # candidate_height_pct[t] stays NaN — pathological bars cannot
            # be relied upon for triggers.
            continue

        confirmed_this_bar = False

        if direction == _LEG_DIR_UNKNOWN:
            if (
                not math.isfinite(run_min_price)
                or not math.isfinite(run_max_price)
                or run_min_price <= 0.0
                or run_max_price <= 0.0
            ):
                run_min_bar = t
                run_min_price = c
                run_max_bar = t
                run_max_price = c
                last_pivot_bar = t
                last_pivot_price = c
                run_ext_bar = t
                run_ext_price = c
                confirmed_leg_idx_at_t[t] = last_confirmed_idx
                last_confirmed_leg_height_pct[t] = last_confirmed_height
                continue

            if c > run_max_price:
                run_max_price = c
                run_max_bar = t
            if c < run_min_price:
                run_min_price = c
                run_min_bar = t

            up_hit = run_min_price > 0.0 and c >= run_min_price * (1.0 + r)
            dn_hit = run_max_price > 0.0 and c <= run_max_price * (1.0 - r)

            if up_hit and not dn_hit:
                direction = _LEG_DIR_UP
                last_pivot_bar = run_min_bar
                last_pivot_price = run_min_price
                run_ext_bar = t
                run_ext_price = c
            elif dn_hit and not up_hit:
                direction = _LEG_DIR_DOWN
                last_pivot_bar = run_max_bar
                last_pivot_price = run_max_price
                run_ext_bar = t
                run_ext_price = c
            elif up_hit and dn_hit:
                # Dominating bar: pick the side with the larger relative move
                # from its respective extreme.
                up_move = (c - run_min_price) / run_min_price
                dn_move = (run_max_price - c) / run_max_price
                if up_move >= dn_move:
                    direction = _LEG_DIR_UP
                    last_pivot_bar = run_min_bar
                    last_pivot_price = run_min_price
                else:
                    direction = _LEG_DIR_DOWN
                    last_pivot_bar = run_max_bar
                    last_pivot_price = run_max_price
                run_ext_bar = t
                run_ext_price = c
            # else: stay UNKNOWN, accumulate bootstrap range.

        elif direction == _LEG_DIR_UP:
            if c > run_ext_price:
                run_ext_price = c
                run_ext_bar = t
            elif c <= run_ext_price * (1.0 - r):
                end_bar = run_ext_bar
                end_price = run_ext_price
                start_bar = last_pivot_bar
                start_price = last_pivot_price
                if (
                    start_price > 0.0
                    and end_bar > start_bar
                    and math.isfinite(start_price)
                    and math.isfinite(end_price)
                ):
                    height = (end_price - start_price) / start_price
                    if math.isfinite(height) and height > 0.0:
                        legs.append(
                            ConfirmedLeg(
                                start_bar=start_bar,
                                end_bar=end_bar,
                                confirm_bar=t,
                                start_price=start_price,
                                end_price=end_price,
                                direction=_LEG_DIR_UP,
                                height_pct=height,
                            )
                        )
                        confirmed_this_bar = True
                        last_confirmed_idx = len(legs) - 1
                        last_confirmed_height = height
                last_pivot_bar = end_bar
                last_pivot_price = end_price
                direction = _LEG_DIR_DOWN
                run_ext_bar = t
                run_ext_price = c

        else:  # direction == _LEG_DIR_DOWN
            if c < run_ext_price:
                run_ext_price = c
                run_ext_bar = t
            elif c >= run_ext_price * (1.0 + r):
                end_bar = run_ext_bar
                end_price = run_ext_price
                start_bar = last_pivot_bar
                start_price = last_pivot_price
                if (
                    start_price > 0.0
                    and end_bar > start_bar
                    and math.isfinite(start_price)
                    and math.isfinite(end_price)
                ):
                    height = (start_price - end_price) / start_price
                    if math.isfinite(height) and height > 0.0:
                        legs.append(
                            ConfirmedLeg(
                                start_bar=start_bar,
                                end_bar=end_bar,
                                confirm_bar=t,
                                start_price=start_price,
                                end_price=end_price,
                                direction=_LEG_DIR_DOWN,
                                height_pct=height,
                            )
                        )
                        confirmed_this_bar = True
                        last_confirmed_idx = len(legs) - 1
                        last_confirmed_height = height
                last_pivot_bar = end_bar
                last_pivot_price = end_price
                direction = _LEG_DIR_UP
                run_ext_bar = t
                run_ext_price = c

        # End-of-bar per-bar snapshot.
        if confirmed_this_bar:
            confirm_event[t] = 1
        confirmed_leg_idx_at_t[t] = last_confirmed_idx
        last_confirmed_leg_height_pct[t] = last_confirmed_height

        if direction != _LEG_DIR_UNKNOWN and last_pivot_price > 0.0:
            # candidate_height_pct[t] = abs(run_ext - last_pivot) / last_pivot
            # — fraction of last confirmed pivot price (§3.4.3).  After a
            # same-bar confirm this reflects the NEW candidate leg.
            candidate_height_pct[t] = (
                abs(run_ext_price - last_pivot_price) / last_pivot_price
            )
            # v3 WP-V3-3: candidate age and direction (ТЗ v3 §6)
            candidate_age_bars[t] = t - last_pivot_bar + 1
            candidate_leg_direction[t] = np.int8(direction)

    return _CloseOnlyPassResult(
        legs=legs,
        candidate_height_pct=candidate_height_pct,
        confirm_event=confirm_event,
        confirmed_leg_idx_at_t=confirmed_leg_idx_at_t,
        last_confirmed_leg_height_pct=last_confirmed_leg_height_pct,
        candidate_age_bars=candidate_age_bars,
        candidate_leg_direction=candidate_leg_direction,
    )


def detect_confirmed_legs_close_only(
    close: np.ndarray,
    reversal_threshold: float,
) -> List[ConfirmedLeg]:
    """Detect confirmed ZigZag legs from a close-only price stream.

    The detector is intentionally close-only (Appendix A v1.1 §3.4):

    - no ``high`` / ``low`` wick-based pivots;
    - no derived streams such as ``hlc3`` / ``ohlc4``;
    - ``reversal_threshold`` is read from caller (it ultimately comes from
      ``trade_filter.zigzag.reversal_threshold`` in config) and is
      interpreted as a *fraction* of the candidate extremum, not as a
      percent.

    A pivot is confirmed when the close reverses from the running candidate
    extremum by at least ``reversal_threshold``:

    - in an UP leg: pivot at running max once ``close[t] <= run_ext * (1 - r)``;
    - in a DOWN leg: pivot at running min once ``close[t] >= run_ext * (1 + r)``.

    Confirmed-leg height is stored as a fraction of the *start* pivot price
    (Appendix A v1.1 §3.4.2)::

        height_pct = abs(end_pivot_price - start_pivot_price) / start_pivot_price

    Parameters
    ----------
    close : np.ndarray
        1-D float array of close prices.  OHLC validation is the caller's
        responsibility (orchestrator runs it before stats are built).
    reversal_threshold : float
        Numeric fraction in ``(0, 1)``.

    Returns
    -------
    list[ConfirmedLeg]
        Confirmed legs in chronological order.  May be empty if the dataset
        is too short or never reverses by ``reversal_threshold``.
    """
    arr, r = _validate_close_and_threshold(
        close, reversal_threshold, fn_name="detect_confirmed_legs_close_only"
    )
    if arr is None:
        return []
    return list(_run_close_only_zigzag_pass(arr, r).legs)


def compute_confirmed_legs_reset_aware(
    close: np.ndarray,
    reversal_threshold: float,
    *,
    daily_reset_event: np.ndarray,
) -> List[ConfirmedLeg]:
    """Return reset-aware confirmed ZigZag legs from the shared close-only pass."""
    arr, r = _validate_close_and_threshold(
        close, reversal_threshold, fn_name="compute_confirmed_legs_reset_aware"
    )
    if arr is None:
        return []
    pass_result = _run_close_only_zigzag_pass(
        arr, r, daily_reset_event=daily_reset_event
    )
    return list(pass_result.legs)


# ---------------------------------------------------------------------------
# Causal per-bar ZigZag engine (WP4 — plan §3.2 / Appendix A v1.1 §3, §6)
# ---------------------------------------------------------------------------

def compute_zigzag_per_bar(
    close: np.ndarray,
    reversal_threshold: float,
    local_window: int,
    *,
    daily_reset_event: "Optional[np.ndarray]" = None,
) -> ZigZagPerBar:
    """Compute per-bar candidate / confirmed / rolling-median diagnostic
    arrays from a close-only price stream.

    This is the WP4 deliverable.  It feeds the FSM (WP5+) but does NOT itself
    contain any FSM, ST flip detection, ``positions`` builder or runtime
    integration — those live in later work packages.

    Implementation
    --------------
    - Uses the SAME close-only ZigZag formula as
      ``detect_confirmed_legs_close_only``; both call
      ``_run_close_only_zigzag_pass`` internally so the WP3 / WP4 / WP5
      shared fixture observes bit-identical confirmed legs, confirm bars,
      and heights.
    - ``candidate_height_pct[t]`` is the end-of-bar candidate-leg height as
      a fraction of the last confirmed pivot price (Appendix A v1.1 §3.4.3).
      ``NaN`` until the bootstrap establishes a leg direction.
    - ``confirm_event[t] = 1`` iff a new confirmed leg was emitted on bar
      ``t``; ``0`` otherwise.
    - ``local_median_N[t]`` is the median of the last ``N = local_window``
      confirmed legs whose ``confirm_bar <= t`` (causal slice-local
      history, Appendix A v1.1 §6, §15.1).  ``local_median_available[t]``
      is ``True`` iff at least ``local_window`` confirmed legs are
      available.  Independent of any FSM lifecycle: WP4 has no notion of
      ``freeze_confirmed_legs`` or lifecycle start.

    Parameters
    ----------
    close : np.ndarray
        1-D close price array (executed slice — typically
        ``ext_arrays`` for OOS or the train slice).
    reversal_threshold : float
        Numeric fraction in ``(0, 1)`` from
        ``trade_filter.zigzag.reversal_threshold``.
    local_window : int
        ``zigzag.local_window`` — the rolling window size ``N`` for
        ``local_median_N``.  Must be ``>= 1``.

    Returns
    -------
    ZigZagPerBar
        Per-bar diagnostic arrays of length ``len(close)``.
    """
    if isinstance(local_window, bool) or not isinstance(local_window, int):
        raise ConfigError(
            f"compute_zigzag_per_bar requires int local_window >= 1, "
            f"got {local_window!r}"
        )
    if local_window < 1:
        raise ConfigError(
            f"compute_zigzag_per_bar requires local_window >= 1, got {local_window}"
        )

    arr, r = _validate_close_and_threshold(
        close, reversal_threshold, fn_name="compute_zigzag_per_bar"
    )

    if arr is None:
        # Empty / None input → return empty arrays.  Length-zero arrays keep
        # downstream length invariants intact.
        empty_f = np.empty(0, dtype=np.float64)
        empty_i = np.empty(0, dtype=np.int64)
        empty_e = np.empty(0, dtype=np.int8)
        empty_b = np.empty(0, dtype=bool)
        return ZigZagPerBar(
            candidate_height_pct=empty_f,
            confirm_event=empty_e,
            confirmed_leg_idx_at_t=empty_i,
            last_confirmed_leg_height_pct=empty_f.copy(),
            local_median_N=empty_f.copy(),
            local_median_available=empty_b,
            candidate_age_bars=empty_i.copy(),
            candidate_leg_direction=empty_e.copy(),
        )

    pass_result = _run_close_only_zigzag_pass(
        arr, r, daily_reset_event=daily_reset_event
    )

    n = arr.shape[0]
    local_median_N = np.full(n, np.nan, dtype=np.float64)
    local_median_available = np.zeros(n, dtype=bool)

    if pass_result.legs:
        heights = np.array(
            [leg.height_pct for leg in pass_result.legs], dtype=np.float64
        )
        m = heights.shape[0]
        median_by_leg_idx = np.full(m, np.nan, dtype=np.float64)
        median_available_by_leg_idx = np.zeros(m, dtype=bool)
        for idx in range(local_window - 1, m):
            median = float(np.median(heights[idx - local_window + 1 : idx + 1]))
            if math.isfinite(median):
                median_by_leg_idx[idx] = median
                median_available_by_leg_idx[idx] = True

        idx_at_t = pass_result.confirmed_leg_idx_at_t
        valid_t = idx_at_t >= 0
        if np.any(valid_t):
            valid_idx = idx_at_t[valid_t]
            available = median_available_by_leg_idx[valid_idx]
            valid_positions = np.flatnonzero(valid_t)
            available_positions = valid_positions[available]
            local_median_N[available_positions] = median_by_leg_idx[
                valid_idx[available]
            ]
            local_median_available[available_positions] = True

    return ZigZagPerBar(
        candidate_height_pct=pass_result.candidate_height_pct,
        confirm_event=pass_result.confirm_event,
        confirmed_leg_idx_at_t=pass_result.confirmed_leg_idx_at_t,
        last_confirmed_leg_height_pct=pass_result.last_confirmed_leg_height_pct,
        local_median_N=local_median_N,
        local_median_available=local_median_available,
        candidate_age_bars=pass_result.candidate_age_bars,
        candidate_leg_direction=pass_result.candidate_leg_direction,
    )

# ---------------------------------------------------------------------------
# Full-dataset global stats (plan WP3 / Appendix A v1.1 §12)
# ---------------------------------------------------------------------------

def _extract_zigzag_field(zigzag_cfg: Any, name: str, default: Any = None) -> Any:
    """Return ``zigzag_cfg.<name>`` if present, else ``default``.

    Done via ``getattr`` so the donor module does not import the wf_grid
    config dataclass type — only the duck-typed shape is required.
    """
    return getattr(zigzag_cfg, name, default)


# Mode resolution is defined in the config layer (trade_filter_config.py) so
# that the config loader and the runtime filter share the same implementation
# without the loader depending on this runtime module.
from supertrend_optimizer.core.trade_filter_config import (  # noqa: E402
    resolve_zigzag_mode as _resolve_zigzag_mode,
)


def build_zigzag_global_stats(
    close: np.ndarray,
    trade_filter_config: Any,
) -> ZigZagGlobalStats:
    """Build full-dataset ``ZigZagGlobalStats`` from a close-only series.

    This function is the WP3 deliverable.  It is intentionally limited to:

    1. Detect confirmed legs on the full dataset using the close-only ZigZag
       formula (plan §3.3 / Appendix A v1.1 §3.4).
    2. Compute ``confirmed_heights_pct`` (fractions) and
       ``global_median = median(confirmed_heights_pct)``.
    3. Materialize ``candidate_trigger_threshold``:

       - numeric ``->`` source ``"explicit"``;
       - ``"auto"`` ``->``
         ``np.quantile(confirmed_heights_pct, q=quantile, method="linear")``
         and source ``"quantile"``.

       For the auto branch the minimum sample is
       ``max(zigzag.local_window, 10)`` per Appendix A v1.1 §12.2.

    4. Raise ``ConfigError`` BEFORE WF execution on any of the four init
       failures from Appendix A v1.1 §12.3:

       - no confirmed legs (empty population for ``global_median``);
       - ``global_median`` is NaN or Inf;
       - ``auto`` and ``n_legs_total < min_legs_for_quantile``;
       - the materialized threshold is NaN or Inf.

    Anti-drift (WP3): no FSM, no per-bar arrays, no orchestrator wiring.

    Parameters
    ----------
    close : np.ndarray
        1-D close price array for the full validated dataset.
    trade_filter_config : Any
        Duck-typed object exposing ``zigzag.reversal_threshold``,
        ``zigzag.local_window``, ``zigzag.candidate_trigger_threshold`` and
        ``zigzag.candidate_trigger_quantile`` (typically a
        ``wf_grid.config.schema.TradeFilterConfig`` after WP2 validation).

    Returns
    -------
    ZigZagGlobalStats
        Successfully materialised stats.  ``insufficient_data`` is always
        ``False`` and ``fail_closed_reason`` is always ``None`` on success;
        all init-failure paths raise ``ConfigError`` instead.
    """
    if trade_filter_config is None:
        raise ConfigError(
            "build_zigzag_global_stats requires a trade_filter_config; got None"
        )

    zigzag_cfg = _extract_zigzag_field(trade_filter_config, "zigzag")
    if zigzag_cfg is None:
        raise ConfigError(
            "build_zigzag_global_stats requires trade_filter_config.zigzag; got None"
        )

    reversal_threshold = _extract_zigzag_field(zigzag_cfg, "reversal_threshold")
    if reversal_threshold is None:
        raise ConfigError(
            "trade_filter.zigzag.reversal_threshold is required to build "
            "ZigZagGlobalStats"
        )
    try:
        reversal_threshold_f = float(reversal_threshold)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"trade_filter.zigzag.reversal_threshold must be numeric, "
            f"got {reversal_threshold!r}"
        ) from exc
    if not math.isfinite(reversal_threshold_f) or not (
        0.0 < reversal_threshold_f < 1.0
    ):
        raise ConfigError(
            "trade_filter.zigzag.reversal_threshold must be a finite fraction in "
            f"(0, 1); got {reversal_threshold_f!r}"
        )

    local_window_raw = _extract_zigzag_field(zigzag_cfg, "local_window", 5)
    if isinstance(local_window_raw, bool) or not isinstance(local_window_raw, int):
        raise ConfigError(
            f"trade_filter.zigzag.local_window must be int >= 1, "
            f"got {local_window_raw!r}"
        )
    if local_window_raw < 1:
        raise ConfigError(
            f"trade_filter.zigzag.local_window must be int >= 1, got {local_window_raw}"
        )
    local_window = int(local_window_raw)

    global_stats_source = _extract_zigzag_field(
        zigzag_cfg, "global_stats_source", "full_dataset"
    )
    leg_height_mode = _extract_zigzag_field(zigzag_cfg, "leg_height_mode", "pct")

    # v3: resolve mode and gate (WP-V3-2, ТЗ v3 §5)
    mode_raw = _extract_zigzag_field(zigzag_cfg, "mode")
    raw_triggers_present = getattr(trade_filter_config, "_raw_triggers_present", None)
    if raw_triggers_present is None:
        triggers_cfg = getattr(trade_filter_config, "triggers", None)
    else:
        triggers_cfg = (
            getattr(trade_filter_config, "triggers", None)
            if bool(raw_triggers_present) else None
        )
    resolved_mode: str = _resolve_zigzag_mode(mode_raw, triggers_cfg)

    gate_cfg = _extract_zigzag_field(zigzag_cfg, "candidate_duration_gate")
    gate_enabled: bool = bool(getattr(gate_cfg, "enabled", False)) if gate_cfg is not None else False
    if gate_enabled:
        _mb = getattr(gate_cfg, "max_bars", None)
        gate_max_bars: Optional[int] = int(_mb) if _mb is not None else None
    else:
        gate_max_bars = None

    confirmed_legs = detect_confirmed_legs_close_only(close, reversal_threshold_f)
    n_legs_total = len(confirmed_legs)

    if n_legs_total == 0:
        raise ConfigError(
            "ZigZag global stats initialisation failed: no confirmed legs detected "
            f"on the full dataset (reversal_threshold={reversal_threshold_f}). "
            "Increase data length or relax the threshold; see Appendix A v1.1 §12.3."
        )

    confirmed_heights_pct = np.asarray(
        [leg.height_pct for leg in confirmed_legs], dtype=np.float64
    )

    global_median = float(np.median(confirmed_heights_pct))
    if not math.isfinite(global_median):
        raise ConfigError(
            "ZigZag global stats initialisation failed: global_median is "
            f"{global_median!r}; see Appendix A v1.1 §12.3."
        )

    # Materialise candidate_trigger_threshold per §12.1 / §12.2.
    ctt_raw = _extract_zigzag_field(zigzag_cfg, "candidate_trigger_threshold")
    ctq_raw = _extract_zigzag_field(zigzag_cfg, "candidate_trigger_quantile")

    threshold_mode: str
    candidate_trigger_source: str
    candidate_trigger_quantile: Optional[float]
    materialized_threshold: float
    min_legs_for_quantile: Optional[int] = None

    if isinstance(ctt_raw, str) and ctt_raw == "auto":
        # Auto branch — quantile-based materialisation from full-dataset legs.
        if ctq_raw is None:
            raise ConfigError(
                "trade_filter.zigzag.candidate_trigger_quantile must be provided "
                "when candidate_trigger_threshold is 'auto'"
            )
        try:
            quantile_f = float(ctq_raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                "trade_filter.zigzag.candidate_trigger_quantile must be numeric, "
                f"got {ctq_raw!r}"
            ) from exc
        if not math.isfinite(quantile_f) or not (0.0 < quantile_f < 1.0):
            raise ConfigError(
                "trade_filter.zigzag.candidate_trigger_quantile must be in (0, 1), "
                f"got {quantile_f!r}"
            )

        min_legs_for_quantile = max(local_window, 10)
        if n_legs_total < min_legs_for_quantile:
            raise ConfigError(
                "ZigZag global stats initialisation failed: auto "
                "candidate_trigger_threshold requires at least "
                f"{min_legs_for_quantile} confirmed legs (max(local_window={local_window}, "
                f"10)), got {n_legs_total}; see Appendix A v1.1 §12.2 / §12.3."
            )

        # method="linear" is pinned to keep the materialisation
        # numpy-version-stable across runs.
        materialized_threshold = float(
            np.quantile(confirmed_heights_pct, q=quantile_f, method="linear")
        )
        threshold_mode = "auto"
        candidate_trigger_source = "quantile"
        candidate_trigger_quantile = quantile_f
    elif isinstance(ctt_raw, str):
        # Any non-"auto" string is a config-level error; defensive net here.
        raise ConfigError(
            "trade_filter.zigzag.candidate_trigger_threshold must be a numeric "
            f"fraction or the literal 'auto', got {ctt_raw!r}"
        )
    elif ctt_raw is None:
        # Absent threshold cannot be materialised; surface as an init failure
        # consistent with the §12.3 keyset (NaN/Inf materialised threshold).
        raise ConfigError(
            "ZigZag global stats initialisation failed: "
            "trade_filter.zigzag.candidate_trigger_threshold is required when "
            "the filter is enabled (must be a numeric fraction or 'auto')"
        )
    else:
        try:
            ctt_f = float(ctt_raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                "trade_filter.zigzag.candidate_trigger_threshold must be numeric "
                f"or 'auto', got {ctt_raw!r}"
            ) from exc
        if not math.isfinite(ctt_f) or not (0.0 < ctt_f < 1.0):
            raise ConfigError(
                "trade_filter.zigzag.candidate_trigger_threshold must be a finite "
                f"fraction in (0, 1), got {ctt_f!r}"
            )
        materialized_threshold = ctt_f
        threshold_mode = "explicit"
        candidate_trigger_source = "explicit"
        candidate_trigger_quantile = None

    if not math.isfinite(materialized_threshold):
        raise ConfigError(
            "ZigZag global stats initialisation failed: materialized "
            f"candidate_trigger_threshold is {materialized_threshold!r}; "
            "see Appendix A v1.1 §12.3."
        )

    def _materialize_wakeup_quantile_threshold(q_raw: object, field_path: str) -> float:
        try:
            q_f = float(q_raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"{field_path} must be numeric in (0, 1), got {q_raw!r}"
            ) from exc
        if not math.isfinite(q_f) or not (0.0 < q_f < 1.0):
            raise ConfigError(f"{field_path} must be in (0, 1), got {q_f!r}")

        required_legs = max(local_window, 10)
        if n_legs_total < required_legs:
            raise ConfigError(
                "ZigZag global stats initialisation failed: wakeup quantile "
                f"{field_path} requires at least {required_legs} confirmed legs "
                f"(max(local_window={local_window}, 10)), got {n_legs_total}."
            )

        threshold = float(np.quantile(confirmed_heights_pct, q=q_f, method="linear"))
        if not math.isfinite(threshold):
            raise ConfigError(
                "ZigZag global stats initialisation failed: materialized "
                f"{field_path} threshold is {threshold!r}."
            )
        return threshold

    wakeup_entry_candidate_height_threshold: Optional[float] = None
    wakeup_no_fresh_candidate_height_threshold: Optional[float] = None
    wakeup_cfg = getattr(trade_filter_config, "wakeup_regime", None)
    candidate_height_cfg = None
    no_fresh_cfg = None
    if resolved_mode == "D" and wakeup_cfg is not None:
        wakeup_entry = getattr(wakeup_cfg, "entry", None)
        candidate_height_cfg = getattr(wakeup_entry, "candidate_height", None)
        if getattr(candidate_height_cfg, "enabled", False) is True:
            wakeup_entry_candidate_height_threshold = (
                _materialize_wakeup_quantile_threshold(
                    getattr(candidate_height_cfg, "quantile", None),
                    "trade_filter.wakeup_regime.entry.candidate_height.quantile",
                )
            )

        wakeup_exit = getattr(wakeup_cfg, "exit", None)
        no_fresh_cfg = getattr(wakeup_exit, "no_fresh_candidate", None)
        if getattr(no_fresh_cfg, "enabled", False) is True:
            wakeup_no_fresh_candidate_height_threshold = (
                _materialize_wakeup_quantile_threshold(
                    getattr(no_fresh_cfg, "quantile", None),
                    "trade_filter.wakeup_regime.exit.no_fresh_candidate.quantile",
                )
            )

    metadata: dict = {
        "candidate_trigger_source": candidate_trigger_source,
        "candidate_trigger_threshold_mode": threshold_mode,
        "candidate_trigger_quantile": candidate_trigger_quantile,
        "min_legs_for_quantile": min_legs_for_quantile,
        "wakeup_entry_candidate_height_threshold": (
            wakeup_entry_candidate_height_threshold
        ),
        "wakeup_no_fresh_candidate_height_threshold": (
            wakeup_no_fresh_candidate_height_threshold
        ),
        "n_legs_total": n_legs_total,
        "config_snapshot": {
            "reversal_threshold": reversal_threshold_f,
            "local_window": local_window,
            "global_stats_source": global_stats_source,
            "leg_height_mode": leg_height_mode,
            "candidate_trigger_threshold": ctt_raw,
            "candidate_trigger_quantile": ctq_raw,
            # v3 fields (WP-V3-2, ТЗ v3 §5)
            "zigzag_mode": resolved_mode,
            "candidate_duration_gate": {
                "enabled": gate_enabled,
                "max_bars": gate_max_bars,
            },
            "wakeup_entry_candidate_height_quantile": (
                getattr(candidate_height_cfg, "quantile", None)
                if resolved_mode == "D" and wakeup_cfg is not None
                else None
            ),
            "wakeup_no_fresh_candidate_quantile": (
                getattr(no_fresh_cfg, "quantile", None)
                if resolved_mode == "D" and wakeup_cfg is not None
                else None
            ),
        },
    }

    return ZigZagGlobalStats(
        reversal_threshold=reversal_threshold_f,
        global_stats_source=str(global_stats_source),
        leg_height_mode=str(leg_height_mode),
        confirmed_legs=list(confirmed_legs),
        confirmed_heights_pct=confirmed_heights_pct,
        global_median=global_median,
        candidate_trigger_threshold=materialized_threshold,
        candidate_trigger_source=candidate_trigger_source,
        candidate_trigger_quantile=candidate_trigger_quantile,
        n_legs_total=n_legs_total,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata=metadata,
        zigzag_mode=resolved_mode,
        candidate_duration_gate_enabled=gate_enabled,
        candidate_duration_max_bars=gate_max_bars,
        wakeup_entry_candidate_height_threshold=wakeup_entry_candidate_height_threshold,
        wakeup_no_fresh_candidate_height_threshold=(
            wakeup_no_fresh_candidate_height_threshold
        ),
    )


# ===========================================================================
# WP5 — FSM and filtered_positions builder
# (plan §5, Appendix A v1.1 §4..§9, §14, §15.2, §15.3, §15.7, §17.7..§17.12)
# ===========================================================================

class ZigZagFSMState(IntEnum):
    """Five canonical FSM states (plan §5.1 / Appendix A v1.1 §4).

    Numeric codes are stable internal identifiers — diagnostics expose the
    string names via :data:`_FSM_STATE_NAMES` for human-readable arrays.

    ST_COUNTING_ZZ_LEGS (exit-off mode B only) replaces FREEZE/MONITORING for
    leg-count stopping (docs/plan_exit_off_modes.txt).
    """
    OFF = 0
    WAIT_FIRST_ST_FLIP = 1
    ST_ACTIVE_FREEZE = 2
    ST_ACTIVE_MONITORING = 3
    ST_STOPPING = 4
    ST_COUNTING_ZZ_LEGS = 5


# Single source of truth: state name strings live in
# ``supertrend_optimizer.core._fsm_state_names.FSM_STATE_NAMES`` (plan
# §7.4 canonical order). Locally we build an ``enum_value -> name`` map
# by iterating the shared tuple and looking up the enum via ``getattr``.
# Any rename in the shared tuple is automatically picked up; a typo or
# orphaned name fails fast with AttributeError at import time.
from supertrend_optimizer.core._fsm_state_names import (  # noqa: E402
    FSM_STATE_NAMES as _SHARED_FSM_STATE_NAMES,
)

_FSM_STATE_NAMES: Dict[int, str] = {
    int(getattr(ZigZagFSMState, _name)): _name
    for _name in _SHARED_FSM_STATE_NAMES
}


# Trigger-source codes — diagnostics-friendly and cheap to compare.
_TRIGGER_SOURCE_NONE: str = "none"
_TRIGGER_SOURCE_A: str = "candidate_threshold"
_TRIGGER_SOURCE_B: str = "confirmed_median"
_TRIGGER_SOURCE_BOTH: str = "both"
_TRIGGER_SOURCE_WAKEUP: str = "wakeup_regime"

# ТЗ v3 §10.4 — complete ``immediate_candidate_entry_block_reason`` whitelist.
# ``_IMM_REASON_FILTER_OFF`` (priority 2 in spec) is whitelist-compatible but
# is UNREACHABLE from the enabled ``apply()`` path: the disabled-filter path in
# ``backtest.run_single_backtest`` never calls ``apply()`` and returns
# ``filter_diagnostics=None``, so the immediate-diagnostics arrays are never
# created for a disabled filter.  Therefore only priorities 1, 3–9 can appear
# inside ``apply()``.
_IMM_REASON_DAILY_RESET: str = "daily_reset"
_IMM_REASON_FILTER_OFF: str = "filter_off"          # unreachable from apply()
_IMM_REASON_STATE_NOT_OFF: str = "state_not_off"
_IMM_REASON_MODE_NOT_C: str = "mode_not_c"
_IMM_REASON_HEIGHT_GATE_FAILED: str = "height_gate_failed"
_IMM_REASON_DURATION_GATE_FAILED: str = "duration_gate_failed"
_IMM_REASON_UNKNOWN_DIR: str = "unknown_candidate_direction"
_IMM_REASON_TRADE_MODE_DISALLOWS: str = "trade_mode_disallows_direction"
_IMM_REASON_NONE: str = "none"


_VALID_TRADE_MODES: tuple = ("long", "short", "both", "revers")


@dataclass(frozen=True)
class ZigZagSTFilterResult:
    """Output of the WP5 FSM builder (plan §3.2).

    Attributes
    ----------
    positions : np.ndarray
        ``filtered_positions`` — the lifecycle-aware position stream
        produced by the FSM directly (NOT a post-mask over raw
        ``generate_positions`` output).  Same dtype and length as the
        input ``positions`` argument to :func:`apply`.
    filter_diagnostics : dict[str, np.ndarray]
        Per-bar diagnostic arrays of length ``len(positions)``.  WP5 fills
        the FSM-relevant subset; WP7+ may extend this with additional
        keys.  Length invariants are owned by callers (orchestrator).
    internal_legs : list[ConfirmedLeg] | None
        Optional confirmed-leg list reflected back from the per-bar
        engine.  WP5 leaves this ``None`` and lets callers populate it
        when they have the legs handy (plan §3.2 — "internal_legs ... |
        None").
    """
    positions: np.ndarray
    filter_diagnostics: Dict[str, np.ndarray]
    internal_legs: Optional[List[ConfirmedLeg]] = None
    filter_config_snapshot: Optional[dict] = None


# ---------------------------------------------------------------------------
# WP6 — ST flip detection (plan §5.5 / WP6, spec §3.3, §17.13–§17.14).
# ---------------------------------------------------------------------------

def _is_first_flip_allowed(flip_dir: int, trade_mode: str) -> bool:
    """First ST flip allowance in ``WAIT_FIRST_ST_FLIP`` (plan §5.2 / spec §9).

    Only the first allowed flip leaves WAIT into ST_ACTIVE_FREEZE.
    Disallowed flips are silently skipped — the FSM keeps waiting for the
    next allowed flip.
    """
    if flip_dir == 0:
        return False
    if trade_mode == "long":
        return flip_dir == +1
    if trade_mode == "short":
        return flip_dir == -1
    if trade_mode in ("both", "revers"):
        return True
    raise ConfigError(
        f"Unsupported trade_mode for ZigZag ST filter: {trade_mode!r}; "
        f"expected one of {_VALID_TRADE_MODES}"
    )


def _trade_mode_allows_direction(direction: int, trade_mode: str) -> bool:
    """ТЗ v3 §7.6 ``trade_mode_allows`` — direction-aware allowance for the
    Mode C immediate-entry primitive ``immediate_allowed``.

    ::

        dir +1 allowed by long, both, revers
        dir -1 allowed by short, both, revers

    Any other direction (including ``0``/UNKNOWN) is disallowed.
    """
    if direction == +1:
        return trade_mode in ("long", "both", "revers")
    if direction == -1:
        return trade_mode in ("short", "both", "revers")
    return False


def _is_component_enabled(component: object) -> bool:
    return component is not None and getattr(component, "enabled", None) is True


def _require_1d_len(name: str, values: object, n: int) -> np.ndarray:
    if values is None:
        raise ConfigError(f"apply() requires {name} for Mode D wakeup component")
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1 or arr.shape[0] != n:
        raise ConfigError(
            f"apply() {name} must be 1-D with length n={n}; "
            f"got shape={arr.shape}"
        )
    return arr


_WAKEUP_OHLC_ERROR_STEM = (
    "apply() Mode D wakeup atr_expansion requires high, low, and close OHLC arrays"
)


def _require_wakeup_ohlc_array(name: str, values: object, n: int) -> np.ndarray:
    if values is None:
        raise ConfigError(f"{_WAKEUP_OHLC_ERROR_STEM}; missing {name}")
    try:
        arr = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"{_WAKEUP_OHLC_ERROR_STEM}; {name} must be numeric"
        ) from exc
    if arr.ndim != 1 or arr.shape[0] != n:
        raise ConfigError(
            f"{_WAKEUP_OHLC_ERROR_STEM}; {name} must be 1-D with length n={n}; "
            f"got shape={arr.shape}"
        )
    if not np.all(np.isfinite(arr)):
        raise ConfigError(f"{_WAKEUP_OHLC_ERROR_STEM}; {name} contains NaN or Inf")
    return arr


def _validate_wakeup_ohlc(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> None:
    if np.any(high < low):
        raise ConfigError(f"{_WAKEUP_OHLC_ERROR_STEM}; high must be >= low")


def _resolve_mode_d_wakeup_entry(
    trade_filter_config: Any,
) -> tuple[object, object, object, object]:
    wakeup = getattr(trade_filter_config, "wakeup_regime", None)
    if wakeup is None or getattr(wakeup, "enabled", None) is not True:
        raise ConfigError(
            "apply() Mode D requires trade_filter.wakeup_regime.enabled=True"
        )
    entry = getattr(wakeup, "entry", None)
    if entry is None:
        raise ConfigError("apply() Mode D requires trade_filter.wakeup_regime.entry")
    return (
        getattr(entry, "candidate_height", None),
        getattr(entry, "candidate_age", None),
        getattr(entry, "atr_expansion", None),
        getattr(entry, "volume_expansion", None),
    )


def _resolve_mode_d_wakeup_exit(
    trade_filter_config: Any,
) -> tuple[object, object, object]:
    wakeup = getattr(trade_filter_config, "wakeup_regime", None)
    exit_cfg = getattr(wakeup, "exit", None) if wakeup is not None else None
    if exit_cfg is None:
        raise ConfigError("apply() Mode D requires trade_filter.wakeup_regime.exit")
    return (
        getattr(exit_cfg, "ttl", None),
        getattr(exit_cfg, "no_fresh_candidate", None),
        getattr(exit_cfg, "action", None),
    )


def _wakeup_entry_component_ok(
    *,
    component: object,
    value: float,
    threshold: object,
) -> bool:
    if not _is_component_enabled(component):
        return True
    if threshold is None:
        raise ConfigError("apply() Mode D wakeup threshold is not materialized")
    threshold_f = float(threshold)
    if not math.isfinite(threshold_f):
        raise ConfigError("apply() Mode D wakeup threshold must be finite")
    return math.isfinite(value) and value >= threshold_f


class _WakeupEntryEvaluation(NamedTuple):
    all_ok: bool
    height_ok: bool
    age_ok: bool
    direction_ok: bool
    trade_mode_ok: bool
    atr_ok: bool
    volume_ok: bool
    candidate_age: int
    atr_value: Optional[float]
    volume_value: Optional[float]


class _WakeupRuntimeState(NamedTuple):
    cycle_age: int
    bars_since_fresh: int
    active_direction: int
    exit_c_fired: bool


def _wakeup_runtime_off() -> _WakeupRuntimeState:
    return _WakeupRuntimeState(
        cycle_age=-1,
        bars_since_fresh=-1,
        active_direction=0,
        exit_c_fired=False,
    )


def _evaluate_wakeup_entry(
    *,
    candidate_height: float,
    candidate_age: int,
    candidate_direction: int,
    trade_mode: str,
    candidate_height_cfg: object,
    candidate_age_cfg: object,
    atr_cfg: object,
    volume_cfg: object,
    candidate_height_threshold: object,
    wakeup_atr_ratio: "Optional[np.ndarray]",
    wakeup_volume_ratio: "Optional[np.ndarray]",
    t: int,
) -> _WakeupEntryEvaluation:
    height_ok = _wakeup_entry_component_ok(
        component=candidate_height_cfg,
        value=candidate_height,
        threshold=candidate_height_threshold,
    )
    if _is_component_enabled(candidate_age_cfg):
        age_ok = (
            candidate_age > 0
            and candidate_age <= int(getattr(candidate_age_cfg, "max_bars"))
        )
    else:
        age_ok = True

    atr_value: Optional[float] = None
    if _is_component_enabled(atr_cfg):
        if wakeup_atr_ratio is None:
            raise ConfigError(
                "apply() Mode D requires wakeup ATR ratio when "
                "atr_expansion is enabled"
            )
        atr_value = float(wakeup_atr_ratio[t])
        atr_ok = (
            math.isfinite(atr_value)
            and atr_value >= float(getattr(atr_cfg, "min_ratio"))
        )
    else:
        atr_ok = True
        if wakeup_atr_ratio is not None:
            atr_value = float(wakeup_atr_ratio[t])

    volume_value: Optional[float] = None
    if _is_component_enabled(volume_cfg):
        if wakeup_volume_ratio is None:
            raise ConfigError(
                "apply() Mode D requires wakeup volume ratio when "
                "volume_expansion is enabled"
            )
        volume_value = float(wakeup_volume_ratio[t])
        volume_ok = (
            math.isfinite(volume_value)
            and volume_value >= float(getattr(volume_cfg, "min_ratio"))
        )
    else:
        volume_ok = True
        if wakeup_volume_ratio is not None:
            volume_value = float(wakeup_volume_ratio[t])

    direction_ok = candidate_direction in (-1, +1)
    trade_mode_ok = _trade_mode_allows_direction(candidate_direction, trade_mode)
    return _WakeupEntryEvaluation(
        all_ok=(
            height_ok
            and age_ok
            and direction_ok
            and trade_mode_ok
            and atr_ok
            and volume_ok
        ),
        height_ok=height_ok,
        age_ok=age_ok,
        direction_ok=direction_ok,
        trade_mode_ok=trade_mode_ok,
        atr_ok=atr_ok,
        volume_ok=volume_ok,
        candidate_age=candidate_age,
        atr_value=atr_value,
        volume_value=volume_value,
    )


def _record_wakeup_entry_diagnostics(
    *,
    arrays: Dict[str, np.ndarray],
    t: int,
    evaluation: _WakeupEntryEvaluation,
    candidate_height: float,
    candidate_height_threshold: object,
    candidate_direction: int,
) -> None:
    arrays["wakeup_entry_all_ok_arr"][t] = np.int8(1 if evaluation.all_ok else 0)
    arrays["wakeup_entry_candidate_height_ok_arr"][t] = np.int8(
        1 if evaluation.height_ok else 0
    )
    arrays["wakeup_entry_candidate_age_ok_arr"][t] = np.int8(
        1 if evaluation.age_ok else 0
    )
    arrays["wakeup_entry_candidate_direction_ok_arr"][t] = np.int8(
        1 if evaluation.direction_ok else 0
    )
    arrays["wakeup_entry_trade_mode_ok_arr"][t] = np.int8(
        1 if evaluation.trade_mode_ok else 0
    )
    arrays["wakeup_entry_atr_ok_arr"][t] = np.int8(1 if evaluation.atr_ok else 0)
    arrays["wakeup_entry_volume_ok_arr"][t] = np.int8(
        1 if evaluation.volume_ok else 0
    )
    arrays["wakeup_entry_candidate_height_value_arr"][t] = candidate_height
    if candidate_height_threshold is not None:
        arrays["wakeup_entry_candidate_height_threshold_arr"][t] = float(
            candidate_height_threshold
        )
    arrays["wakeup_entry_candidate_age_bars_arr"][t] = max(
        0,
        int(evaluation.candidate_age),
    )
    arrays["wakeup_entry_candidate_leg_direction_arr"][t] = np.int8(
        candidate_direction
    )
    if evaluation.atr_value is not None:
        arrays["wakeup_entry_atr_ratio_arr"][t] = evaluation.atr_value
    if evaluation.volume_value is not None:
        arrays["wakeup_entry_volume_ratio_arr"][t] = evaluation.volume_value


def _is_wakeup_fresh_candidate(
    *,
    no_fresh_cfg: object,
    active_direction: int,
    candidate_direction: int,
    candidate_age: int,
    candidate_height: float,
    threshold: object,
) -> bool:
    if not _is_component_enabled(no_fresh_cfg):
        return False
    if threshold is None:
        raise ConfigError(
            "apply() Mode D no_fresh_candidate threshold is not materialized"
        )
    threshold_f = float(threshold)
    if not math.isfinite(threshold_f):
        raise ConfigError(
            "apply() Mode D no_fresh_candidate threshold must be finite"
        )
    return (
        candidate_direction == active_direction
        and candidate_age > 0
        and candidate_age <= int(getattr(no_fresh_cfg, "max_age_bars"))
        and math.isfinite(candidate_height)
        and candidate_height >= threshold_f
    )


# ---------------------------------------------------------------------------
# FSM input validation
# ---------------------------------------------------------------------------

def _validate_apply_inputs(
    *,
    trend: np.ndarray,
    per_bar: ZigZagPerBar,
    zigzag_global_stats: ZigZagGlobalStats,
    trade_filter_config: Any,
    trade_mode: str,
) -> int:
    """Cross-input length and type validation; returns ``n``."""
    if trade_filter_config is None:
        raise ConfigError("apply() requires a non-None trade_filter_config")
    if zigzag_global_stats is None:
        raise ConfigError("apply() requires a non-None zigzag_global_stats")
    if per_bar is None:
        raise ConfigError("apply() requires a non-None per_bar")
    if not isinstance(trade_mode, str):
        raise ConfigError(
            f"apply() requires str trade_mode, got {type(trade_mode).__name__}"
        )
    if trade_mode not in _VALID_TRADE_MODES:
        raise ConfigError(
            f"Unsupported trade_mode for ZigZag ST filter: {trade_mode!r}; "
            f"expected one of {_VALID_TRADE_MODES}"
        )

    trend_arr = np.asarray(trend)
    if trend_arr.ndim != 1:
        raise ConfigError(
            f"apply() requires 1-D trend array; got trend.ndim={trend_arr.ndim}"
        )
    n = trend_arr.shape[0]
    for name, arr in (
        ("candidate_height_pct", per_bar.candidate_height_pct),
        ("confirm_event", per_bar.confirm_event),
        ("confirmed_leg_idx_at_t", per_bar.confirmed_leg_idx_at_t),
        ("last_confirmed_leg_height_pct", per_bar.last_confirmed_leg_height_pct),
        ("local_median_N", per_bar.local_median_N),
        ("local_median_available", per_bar.local_median_available),
    ):
        if arr.shape[0] != n:
            raise ConfigError(
                f"apply() length mismatch: per_bar.{name} has {arr.shape[0]} "
                f"bars, expected {n}"
            )
    return n


def _update_held_pos(held_pos: int, flip_dir: int, trade_mode: str) -> int:
    """Update the FSM-tracked held position after a tradable ST flip.

    In active states (FREEZE / MONITORING) subsequent ST flips update the
    held position according to ``trade_mode``:

    - ``both`` / ``revers``: any flip changes held_pos to ``flip_dir``.
    - ``long``:  only long flips (``+1``) update held_pos; short flips
      set held_pos to ``0`` (exit without reverse).
    - ``short``: symmetric to ``long`` — only short flips (``-1``) keep a
      short; long flips exit to flat.

    ``flip_dir == 0`` is a no-op (non-tradable transition).
    """
    if flip_dir == 0:
        return held_pos
    if trade_mode in ("both", "revers"):
        return flip_dir
    if trade_mode == "long":
        return +1 if flip_dir == +1 else 0
    if trade_mode == "short":
        return -1 if flip_dir == -1 else 0
    return held_pos


def _apply_mode_d_internal_st_flip(
    held_pos: int,
    wakeup_active_direction: int,
    flip_dir: int,
    trade_mode: str,
) -> tuple[int, int, str]:
    if flip_dir == 0:
        return held_pos, wakeup_active_direction, "none"
    if held_pos != 0 and flip_dir == held_pos:
        return held_pos, wakeup_active_direction, "none"
    if trade_mode in ("both", "revers"):
        return flip_dir, flip_dir, "reverse_on_st_flip"
    if trade_mode == "long":
        if flip_dir == -1:
            return 0, +1, "flat_on_disallowed_st_flip"
        return +1, +1, "restore_allowed_position_on_st_flip"
    if trade_mode == "short":
        if flip_dir == +1:
            return 0, -1, "flat_on_disallowed_st_flip"
        return -1, -1, "restore_allowed_position_on_st_flip"
    return held_pos, wakeup_active_direction, "none"


def _effective_wakeup_trade_mode(
    *,
    raw_trade_mode: str,
    wakeup_lock_cycle_direction: bool,
    cycle_direction: int,
) -> str | None:
    if wakeup_lock_cycle_direction:
        if cycle_direction == +1:
            return "long"
        if cycle_direction == -1:
            return "short"
        return None
    return raw_trade_mode


def _resolve_trigger_toggles(trade_filter_config: Any) -> tuple[bool, bool]:
    """Read ``triggers.candidate_threshold.enabled`` and
    ``triggers.confirmed_median.enabled`` flags.

    Defaults to ``False`` if a sub-block is missing — but config
    validation (WP2) already requires both blocks when filter is enabled,
    so this is purely a defensive net.
    """
    triggers_cfg = getattr(trade_filter_config, "triggers", None)
    if triggers_cfg is None:
        return False, False
    cand = getattr(triggers_cfg, "candidate_threshold", None)
    conf = getattr(triggers_cfg, "confirmed_median", None)
    a_enabled = bool(getattr(cand, "enabled", False)) if cand is not None else False
    b_enabled = bool(getattr(conf, "enabled", False)) if conf is not None else False
    return a_enabled, b_enabled


class _LifecycleStartFromOffResult(NamedTuple):
    state: ZigZagFSMState
    trigger_source: str
    held_pos: int
    confirmed_legs_since_start: int
    zz_legs_since_lifecycle_start: int
    immediate_used_this_bar: bool


def _try_lifecycle_start_from_off(
    *,
    t: int,
    state: ZigZagFSMState,
    resolved_mode: str,
    candidate_component_ok: bool,
    b_component_ok: bool,
    immediate_allowed: bool,
    is_exit_b: bool,
    cand_dir_t: int,
    volume_allowed: bool = True,
) -> "Optional[_LifecycleStartFromOffResult]":
    """Resolve mode-specific lifecycle starts from OFF only."""
    if state != ZigZagFSMState.OFF:
        return None
    if not volume_allowed:
        return None

    if resolved_mode == "A":
        if candidate_component_ok:
            return _wait_lifecycle_start_from_off(_TRIGGER_SOURCE_A)
        return None

    if resolved_mode == "B":
        if b_component_ok:
            return _wait_lifecycle_start_from_off(_TRIGGER_SOURCE_B)
        return None

    if resolved_mode == "C":
        if candidate_component_ok and immediate_allowed:
            return _immediate_lifecycle_start_from_off(
                is_exit_b=is_exit_b,
                cand_dir_t=cand_dir_t,
                trigger_source=_TRIGGER_SOURCE_A,
            )
        return None

    if resolved_mode == "A+B":
        a_fired = candidate_component_ok
        b_fired = b_component_ok
        if a_fired and b_fired:
            return _wait_lifecycle_start_from_off(_TRIGGER_SOURCE_BOTH)
        if a_fired:
            return _wait_lifecycle_start_from_off(_TRIGGER_SOURCE_A)
        if b_fired:
            return _wait_lifecycle_start_from_off(_TRIGGER_SOURCE_B)
        return None

    if resolved_mode == "C+B":
        c_fired = candidate_component_ok
        b_fired = b_component_ok
        if c_fired and immediate_allowed:
            return _immediate_lifecycle_start_from_off(
                is_exit_b=is_exit_b,
                cand_dir_t=cand_dir_t,
                trigger_source=_TRIGGER_SOURCE_BOTH if b_fired else _TRIGGER_SOURCE_A,
            )
        if c_fired and (not immediate_allowed) and b_fired:
            return _wait_lifecycle_start_from_off(_TRIGGER_SOURCE_BOTH)
        if (not c_fired) and b_fired:
            return _wait_lifecycle_start_from_off(_TRIGGER_SOURCE_B)
        return None

    raise ConfigError(
        f"apply(): unknown resolved ZigZag mode {resolved_mode!r}; "
        f"expected one of: A, B, C, A+B, C+B"
    )


def _wait_lifecycle_start_from_off(trigger_source: str) -> _LifecycleStartFromOffResult:
    return _LifecycleStartFromOffResult(
        state=ZigZagFSMState.WAIT_FIRST_ST_FLIP,
        trigger_source=trigger_source,
        held_pos=0,
        confirmed_legs_since_start=-1,
        zz_legs_since_lifecycle_start=-1,
        immediate_used_this_bar=False,
    )


def _immediate_lifecycle_start_from_off(
    *,
    is_exit_b: bool,
    cand_dir_t: int,
    trigger_source: str,
) -> _LifecycleStartFromOffResult:
    if is_exit_b:
        return _LifecycleStartFromOffResult(
            state=ZigZagFSMState.ST_COUNTING_ZZ_LEGS,
            trigger_source=trigger_source,
            held_pos=cand_dir_t,
            confirmed_legs_since_start=-1,
            zz_legs_since_lifecycle_start=0,
            immediate_used_this_bar=True,
        )
    return _LifecycleStartFromOffResult(
        state=ZigZagFSMState.ST_ACTIVE_FREEZE,
        trigger_source=trigger_source,
        held_pos=cand_dir_t,
        confirmed_legs_since_start=0,
        zz_legs_since_lifecycle_start=-1,
        immediate_used_this_bar=True,
    )


def _resolve_freeze_confirmed_legs(trade_filter_config: Any) -> int:
    """Read ``lifecycle.freeze_confirmed_legs`` (plan §5 / spec §4.3)."""
    lifecycle_cfg = getattr(trade_filter_config, "lifecycle", None)
    if lifecycle_cfg is None:
        raise ConfigError(
            "apply() requires trade_filter_config.lifecycle for freeze "
            "counter; missing lifecycle block"
        )
    raw = getattr(lifecycle_cfg, "freeze_confirmed_legs", None)
    if raw is None:
        raise ConfigError(
            "apply() requires lifecycle.freeze_confirmed_legs; got None"
        )
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(
            f"lifecycle.freeze_confirmed_legs must be int >= 0, got {raw!r}"
        )
    if raw < 0:
        raise ConfigError(
            f"lifecycle.freeze_confirmed_legs must be int >= 0, got {raw}"
        )
    return int(raw)


# ---------------------------------------------------------------------------
# WP7 — Public entry: apply(...)
# (plan §5, §8.3 / spec Appendix A v1.1 §4..§9, §10, §14, §15.2, §15.3,
#  §15.7, §16, §17.1, §17.7..§17.14)
# ---------------------------------------------------------------------------

class _ApplyResetEvents(NamedTuple):
    daily_reset_enabled: bool
    daily_reset_event: np.ndarray
    time_filter_enabled: bool
    time_filter_in_window: np.ndarray
    time_filter_reset_event: np.ndarray
    combined_reset_event: np.ndarray


class _ApplyVolumeRuntimeArrays(NamedTuple):
    condition_allowed: Optional[np.ndarray]
    condition_block_reason: Optional[np.ndarray]
    regime: Optional[np.ndarray]
    initial_direction: Optional[np.ndarray]
    median_relative: Optional[np.ndarray]
    condition_block_reason_labels: Optional[np.ndarray]
    regime_labels: Optional[np.ndarray]
    initial_direction_labels: Optional[np.ndarray]


def _precompute_apply_length(
    *,
    per_bar: "Optional[ZigZagPerBar]",
    trend: np.ndarray,
    close: "Optional[np.ndarray]",
) -> int:
    if per_bar is not None:
        return int(per_bar.candidate_height_pct.shape[0])
    if trend is not None:
        return int(np.asarray(trend).shape[0])
    if close is not None:
        return int(np.asarray(close).shape[0])
    raise ConfigError("apply() requires at least one of: per_bar, trend, close")


def _resolve_apply_reset_events(
    *,
    trade_filter_config: Any,
    index: "Optional[pd.Index]",
    n: int,
    daily_reset_event: "Optional[np.ndarray]",
    time_filter_events: "Optional[tuple[np.ndarray, np.ndarray]]",
) -> _ApplyResetEvents:
    _zz_cfg = _extract_zigzag_field(trade_filter_config, "zigzag")
    _vol_cfg = getattr(trade_filter_config, "volume", None)
    daily_reset_enabled = bool(
        _extract_zigzag_field(_zz_cfg, "daily_reset", False)
    ) if _zz_cfg is not None else False
    daily_reset_enabled = daily_reset_enabled or bool(
        getattr(_vol_cfg, "daily_reset", False)
    )

    if daily_reset_event is None:
        daily_reset_event_out = _infer_daily_reset_event(
            index, n, enabled=daily_reset_enabled
        )
    else:
        daily_reset_event_out = np.asarray(daily_reset_event, dtype=bool)
        if daily_reset_event_out.ndim != 1:
            raise ConfigError(
                f"apply() daily_reset_event must be 1-D, "
                f"got ndim={daily_reset_event_out.ndim}"
            )
        if daily_reset_event_out.shape[0] != n:
            raise ConfigError(
                f"apply() daily_reset_event length "
                f"{daily_reset_event_out.shape[0]} != n={n}"
            )

    _tfl_cfg = getattr(trade_filter_config, "time_filter", None)
    _tfl_enabled = (
        bool(getattr(_tfl_cfg, "enabled", False)) if _tfl_cfg is not None else False
    )

    if time_filter_events is None:
        _tfl_sh = getattr(_tfl_cfg, "_start_hour", None) if _tfl_cfg is not None else None
        _tfl_sm = getattr(_tfl_cfg, "_start_minute", None) if _tfl_cfg is not None else None
        _tfl_eh = getattr(_tfl_cfg, "_end_hour", None) if _tfl_cfg is not None else None
        _tfl_em = getattr(_tfl_cfg, "_end_minute", None) if _tfl_cfg is not None else None
        if _tfl_enabled and any(x is None for x in (_tfl_sh, _tfl_sm, _tfl_eh, _tfl_em)):
            raise ConfigError(
                "apply(): time_filter.enabled=True but resolver fields are None; "
                "ensure resolve_time_filter_in_place() is called before apply()"
            )
        time_filter_in_window, time_filter_reset_event_out = _infer_time_filter_events(
            index, n,
            enabled=_tfl_enabled,
            start_h=int(_tfl_sh) if _tfl_sh is not None else 0,
            start_m=int(_tfl_sm) if _tfl_sm is not None else 0,
            end_h=int(_tfl_eh) if _tfl_eh is not None else 0,
            end_m=int(_tfl_em) if _tfl_em is not None else 0,
        )
    else:
        _raw_in_w, _raw_reset = time_filter_events
        time_filter_in_window = np.asarray(_raw_in_w, dtype=bool)
        time_filter_reset_event_out = np.asarray(_raw_reset, dtype=bool)
        if time_filter_in_window.ndim != 1 or time_filter_in_window.shape[0] != n:
            raise ConfigError(
                f"apply() time_filter_events[0] (in_window) must be 1-D bool "
                f"of length n={n}"
            )
        if time_filter_reset_event_out.ndim != 1 or time_filter_reset_event_out.shape[0] != n:
            raise ConfigError(
                f"apply() time_filter_events[1] (reset_event) must be 1-D bool "
                f"of length n={n}"
            )

    return _ApplyResetEvents(
        daily_reset_enabled=daily_reset_enabled,
        daily_reset_event=daily_reset_event_out,
        time_filter_enabled=_tfl_enabled,
        time_filter_in_window=time_filter_in_window,
        time_filter_reset_event=time_filter_reset_event_out,
        combined_reset_event=daily_reset_event_out | time_filter_reset_event_out,
    )


def _materialize_apply_volume_runtime(
    volume_runtime: "Optional[VolumeRuntime]",
    *,
    n: int,
) -> _ApplyVolumeRuntimeArrays:
    if volume_runtime is None:
        return _ApplyVolumeRuntimeArrays(
            None, None, None, None, None, None, None, None
        )

    from supertrend_optimizer.core.volume_metrics import (
        materialize_volume_block_reason,
        materialize_volume_initial_direction,
        materialize_volume_regime,
    )

    condition_allowed = np.asarray(
        volume_runtime.volume_condition_allowed, dtype=bool
    )
    condition_block_reason = np.asarray(
        volume_runtime.volume_condition_block_reason
    )
    regime = np.asarray(volume_runtime.volume_regime)
    initial_direction = np.asarray(volume_runtime.volume_initial_direction)
    median_relative = np.asarray(
        volume_runtime.median_relative_volume, dtype=np.float64
    )
    _volume_arrays = {
        "volume_condition_allowed": condition_allowed,
        "volume_condition_block_reason": condition_block_reason,
        "volume_regime": regime,
        "volume_initial_direction": initial_direction,
        "median_relative_volume": median_relative,
    }
    for _name, _arr in _volume_arrays.items():
        if _arr.ndim != 1 or _arr.shape[0] != n:
            raise ConfigError(
                f"apply() volume_runtime.{_name} must be 1-D with length "
                f"n={n}; got shape={_arr.shape}"
            )

    return _ApplyVolumeRuntimeArrays(
        condition_allowed=condition_allowed,
        condition_block_reason=condition_block_reason,
        regime=regime,
        initial_direction=initial_direction,
        median_relative=median_relative,
        condition_block_reason_labels=materialize_volume_block_reason(
            condition_block_reason
        ),
        regime_labels=materialize_volume_regime(regime),
        initial_direction_labels=materialize_volume_initial_direction(
            initial_direction
        ),
    )


def _atr_rma_or_nan(tr: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(tr), np.nan, dtype=np.float64)
    if len(tr) < period:
        return out
    return calculate_atr_rma(tr, period)


def _rolling_mean_or_nan(values: np.ndarray, window: int) -> np.ndarray:
    values_f = np.asarray(values, dtype=np.float64)
    n = len(values_f)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < window:
        return out
    finite = np.isfinite(values_f)
    clean = np.where(finite, values_f, 0.0)
    csum = np.cumsum(np.insert(clean, 0, 0.0))
    ccount = np.cumsum(np.insert(finite.astype(np.int64), 0, 0))
    sums = csum[window:] - csum[:-window]
    counts = ccount[window:] - ccount[:-window]
    valid = counts == window
    out[window - 1:] = np.where(valid, sums / float(window), np.nan)
    return out


def _compute_wakeup_atr_ratio(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    short_window: int,
    long_window: int,
) -> np.ndarray:
    high_f = np.asarray(high, dtype=np.float64)
    low_f = np.asarray(low, dtype=np.float64)
    close_f = np.asarray(close, dtype=np.float64)
    n = len(close_f)
    if short_window < 1 or long_window < 1:
        raise ConfigError("wakeup ATR ratio windows must be >= 1")

    ratio = np.full(n, np.nan, dtype=np.float64)
    if n < long_window:
        return ratio

    tr = calculate_true_range(high_f, low_f, close_f)
    short_atr = _atr_rma_or_nan(tr, short_window)
    long_atr = _atr_rma_or_nan(tr, long_window)
    valid = np.isfinite(short_atr) & np.isfinite(long_atr) & (long_atr > 0.0)
    np.divide(short_atr, long_atr, out=ratio, where=valid)
    ratio[:long_window - 1] = np.nan
    return ratio


def _compute_wakeup_volume_ratio(
    volume: np.ndarray,
    short_window: int,
    baseline_window: int,
) -> np.ndarray:
    volume_f = np.asarray(volume, dtype=np.float64)
    n = len(volume_f)
    if short_window < 1 or baseline_window < 1:
        raise ConfigError("wakeup volume ratio windows must be >= 1")

    ratio = np.full(n, np.nan, dtype=np.float64)
    if n < baseline_window:
        return ratio

    short_mean = _rolling_mean_or_nan(volume_f, short_window)
    baseline_mean = _rolling_mean_or_nan(volume_f, baseline_window)
    valid = (
        np.isfinite(short_mean)
        & np.isfinite(baseline_mean)
        & (baseline_mean > 0.0)
    )
    np.divide(short_mean, baseline_mean, out=ratio, where=valid)
    ratio[:baseline_window - 1] = np.nan
    return ratio


def _allocate_apply_arrays(
    *,
    n: int,
    zigzag_global_stats: ZigZagGlobalStats,
    candidate_trigger_threshold: float,
    global_median: float,
    freeze_confirmed_legs: int,
    trade_filter_config: Any,
    exit_off_mode_echo: str,
    exit_off_zz_leg_echo: int,
    exit_b_immediate_off_flag: bool,
    resolved_mode: str,
    gate_enabled: bool,
    gate_max_bars: int,
    wakeup_lock_cycle_direction: bool,
) -> Dict[str, np.ndarray]:
    try:
        _zcfg = _extract_zigzag_field(trade_filter_config, "zigzag")
        _lw_val = int(_extract_zigzag_field(_zcfg, "local_window") or 0)
    except Exception:
        _lw_val = 0

    return {
        "filtered_positions": np.zeros(n, dtype=np.int8),
        "state_arr": np.full(
            n, _FSM_STATE_NAMES[int(ZigZagFSMState.OFF)], dtype=object
        ),
        "state_code_arr": np.full(n, int(ZigZagFSMState.OFF), dtype=np.int64),
        "trigger_source_arr": np.full(n, _TRIGGER_SOURCE_NONE, dtype=object),
        "confirmed_legs_since_start_arr": np.full(n, -1, dtype=np.int64),
        "st_flip_dir_arr": np.zeros(n, dtype=np.int8),
        "trade_filter_enabled_arr": np.ones(n, dtype=np.int8),
        "reversal_threshold_arr": np.full(
            n, float(zigzag_global_stats.reversal_threshold), dtype=np.float64
        ),
        "ctt_diag_arr": np.full(
            n, float(candidate_trigger_threshold), dtype=np.float64
        ),
        "local_window_arr": np.full(n, _lw_val, dtype=np.int64),
        "global_median_arr": np.full(n, float(global_median), dtype=np.float64),
        "global_stats_available_arr": np.ones(n, dtype=np.int8),
        "freeze_confirmed_legs_arr": np.full(
            n, int(freeze_confirmed_legs), dtype=np.int64
        ),
        "median_stop_triggered_arr": np.zeros(n, dtype=np.int8),
        "stopping_started_at_arr": np.full(n, -1, dtype=np.int64),
        "filter_allowed_entry_arr": np.zeros(n, dtype=np.int8),
        "filter_block_reason_arr": np.full(n, "none", dtype=object),
        "exit_off_mode_arr": np.full(n, exit_off_mode_echo, dtype=object),
        "exit_off_zz_leg_count_arr": np.full(
            n, np.int64(exit_off_zz_leg_echo), dtype=np.int64
        ),
        "zz_legs_since_lifecycle_start_arr": np.full(n, -1, dtype=np.int64),
        "zz_leg_stop_triggered_arr": np.zeros(n, dtype=np.int8),
        "exit_b_immediate_off_triggered_arr": np.zeros(n, dtype=np.int8),
        "exit_b_immediate_off_config_arr": np.full(
            n, np.int8(1 if exit_b_immediate_off_flag else 0), dtype=np.int8
        ),
        "candidate_threshold_ok_arr": np.zeros(n, dtype=np.int8),
        "candidate_component_ok_arr": np.zeros(n, dtype=np.int8),
        "confirmed_median_ok_arr": np.zeros(n, dtype=np.int8),
        "b_component_ok_arr": np.zeros(n, dtype=np.int8),
        "immediate_allowed_arr": np.zeros(n, dtype=np.int8),
        "candidate_duration_gate_passed_arr": np.zeros(n, dtype=np.int8),
        "state_at_bar_start_arr": np.full(
            n, int(ZigZagFSMState.OFF), dtype=np.int64
        ),
        "held_pos_at_bar_start_arr": np.zeros(n, dtype=np.int8),
        "confirmed_legs_at_bar_start_arr": np.full(n, -1, dtype=np.int64),
        "zigzag_mode_arr": np.full(n, resolved_mode, dtype=object),
        "candidate_duration_gate_enabled_arr": np.full(
            n, np.int8(1 if gate_enabled else 0), dtype=np.int8
        ),
        "candidate_duration_max_bars_arr": np.full(
            n, gate_max_bars, dtype=np.int64
        ),
        "immediate_used_arr": np.zeros(n, dtype=np.int8),
        "immediate_block_reason_arr": np.full(n, "mode_not_c", dtype=object),
        "wakeup_regime_active_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_all_ok_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_candidate_height_ok_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_candidate_age_ok_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_candidate_direction_ok_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_trade_mode_ok_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_atr_ok_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_volume_ok_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_candidate_height_value_arr": np.full(
            n, np.nan, dtype=np.float64
        ),
        "wakeup_entry_candidate_height_threshold_arr": np.full(
            n, np.nan, dtype=np.float64
        ),
        "wakeup_entry_candidate_age_bars_arr": np.zeros(n, dtype=np.int64),
        "wakeup_entry_candidate_leg_direction_arr": np.zeros(n, dtype=np.int8),
        "wakeup_entry_atr_ratio_arr": np.full(n, np.nan, dtype=np.float64),
        "wakeup_entry_volume_ratio_arr": np.full(n, np.nan, dtype=np.float64),
        "wakeup_cycle_age_bars_arr": np.full(n, -1, dtype=np.int64),
        "wakeup_bars_since_fresh_candidate_arr": np.full(n, -1, dtype=np.int64),
        "wakeup_exit_ttl_triggered_arr": np.zeros(n, dtype=np.int8),
        "wakeup_exit_no_fresh_candidate_triggered_arr": np.zeros(n, dtype=np.int8),
        "wakeup_exit_close_triggered_arr": np.zeros(n, dtype=np.int8),
        "wakeup_exit_action_mode_arr": np.full(n, "none", dtype=object),
        "wakeup_exit_reason_arr": np.full(n, "none", dtype=object),
        "wakeup_position_action_arr": np.full(n, "none", dtype=object),
        "wakeup_active_direction_arr": np.zeros(n, dtype=np.int8),
        "wakeup_lock_cycle_direction_config_arr": np.full(
            n,
            np.int8(1 if wakeup_lock_cycle_direction else 0),
            dtype=np.int8,
        ),
        "position_freeze_active_arr": np.zeros(n, dtype=np.int8),
        "position_freeze_bars_left_arr": np.zeros(n, dtype=np.int64),
        "position_freeze_ignored_opposite_st_flip_arr": np.zeros(
            n, dtype=np.int8
        ),
        "position_freeze_release_action_arr": np.full(
            n, "none", dtype=object
        ),
    }


def _finalize_apply_result(
    *,
    n: int,
    arrays: Dict[str, np.ndarray],
    cand_height: np.ndarray,
    local_median_N: np.ndarray,
    local_median_available: np.ndarray,
    cand_age_bars: np.ndarray,
    cand_leg_dir: np.ndarray,
    reset_events: _ApplyResetEvents,
    volume_runtime: "Optional[VolumeRuntime]",
    volume_arrays: _ApplyVolumeRuntimeArrays,
) -> "ZigZagSTFilterResult":
    filter_diagnostics_out: Dict[str, np.ndarray] = {
        "trade_filter_state": arrays["state_arr"],
        "trade_filter_state_code": arrays["state_code_arr"],
        "trade_filter_trigger_source": arrays["trigger_source_arr"],
        "confirmed_legs_since_start": arrays["confirmed_legs_since_start_arr"],
        "st_flip_dir": arrays["st_flip_dir_arr"],
        "trade_filter_enabled": arrays["trade_filter_enabled_arr"],
        "zigzag_reversal_threshold": arrays["reversal_threshold_arr"],
        "candidate_height_pct": np.asarray(cand_height, dtype=np.float64),
        "candidate_trigger_threshold": arrays["ctt_diag_arr"],
        "local_median_N": np.asarray(local_median_N, dtype=np.float64),
        "local_median_available": np.asarray(local_median_available, dtype=np.int8),
        "local_window": arrays["local_window_arr"],
        "global_median": arrays["global_median_arr"],
        "global_stats_available": arrays["global_stats_available_arr"],
        "freeze_confirmed_legs": arrays["freeze_confirmed_legs_arr"],
        "median_stop_triggered": arrays["median_stop_triggered_arr"],
        "stopping_started_at_index": arrays["stopping_started_at_arr"],
        "filter_allowed_entry": arrays["filter_allowed_entry_arr"],
        "filter_block_reason": arrays["filter_block_reason_arr"],
        "exit_off_mode": arrays["exit_off_mode_arr"],
        "exit_off_zz_leg_count": arrays["exit_off_zz_leg_count_arr"],
        "zz_legs_since_lifecycle_start": arrays["zz_legs_since_lifecycle_start_arr"],
        "zz_leg_stop_triggered": arrays["zz_leg_stop_triggered_arr"],
        "exit_b_immediate_off_triggered": arrays[
            "exit_b_immediate_off_triggered_arr"
        ],
        "exit_b_immediate_off_config": arrays["exit_b_immediate_off_config_arr"],
        "daily_reset_enabled": np.full(
            n, int(reset_events.daily_reset_enabled), dtype=np.int8
        ),
        "daily_reset_event": np.asarray(reset_events.daily_reset_event, dtype=np.int8),
        "time_filter_enabled": np.full(
            n,
            np.int8(1 if reset_events.time_filter_enabled else 0),
            dtype=np.int8,
        ),
        "time_filter_in_window": np.asarray(
            reset_events.time_filter_in_window, dtype=np.int8
        ),
        "time_filter_reset_event": np.asarray(
            reset_events.time_filter_reset_event, dtype=np.int8
        ),
        "candidate_threshold_ok": arrays["candidate_threshold_ok_arr"],
        "candidate_component_ok": arrays["candidate_component_ok_arr"],
        "confirmed_median_ok": arrays["confirmed_median_ok_arr"],
        "b_component_ok": arrays["b_component_ok_arr"],
        "immediate_allowed": arrays["immediate_allowed_arr"],
        "candidate_duration_gate_passed": arrays[
            "candidate_duration_gate_passed_arr"
        ],
        "state_at_bar_start": arrays["state_at_bar_start_arr"],
        "held_pos_at_bar_start": arrays["held_pos_at_bar_start_arr"],
        "confirmed_legs_at_bar_start": arrays["confirmed_legs_at_bar_start_arr"],
        "zigzag_mode": arrays["zigzag_mode_arr"],
        "candidate_age_bars": cand_age_bars,
        "candidate_leg_direction": cand_leg_dir,
        "candidate_duration_gate_enabled": arrays[
            "candidate_duration_gate_enabled_arr"
        ],
        "candidate_duration_max_bars": arrays[
            "candidate_duration_max_bars_arr"
        ],
        "immediate_candidate_entry_used": arrays["immediate_used_arr"],
        "immediate_candidate_entry_block_reason": arrays[
            "immediate_block_reason_arr"
        ],
    }
    if volume_runtime is not None:
        filter_diagnostics_out.update(
            {
                "volume_regime": volume_arrays.regime_labels,
                "volume_condition_allowed": volume_arrays.condition_allowed,
                "volume_condition_block_reason": (
                    volume_arrays.condition_block_reason_labels
                ),
                "volume_initial_direction": volume_arrays.initial_direction_labels,
                "median_relative_volume": volume_arrays.median_relative,
            }
        )
    zigzag_mode_arr = arrays.get("zigzag_mode_arr")
    if (
        zigzag_mode_arr is not None
        and len(zigzag_mode_arr) > 0
        and str(zigzag_mode_arr[0]) == "D"
    ):
        filter_diagnostics_out.update(
            {
                "wakeup_regime_active": arrays["wakeup_regime_active_arr"],
                "wakeup_entry_all_ok": arrays["wakeup_entry_all_ok_arr"],
                "wakeup_entry_candidate_height_ok": arrays[
                    "wakeup_entry_candidate_height_ok_arr"
                ],
                "wakeup_entry_candidate_age_ok": arrays[
                    "wakeup_entry_candidate_age_ok_arr"
                ],
                "wakeup_entry_candidate_direction_ok": arrays[
                    "wakeup_entry_candidate_direction_ok_arr"
                ],
                "wakeup_entry_trade_mode_ok": arrays[
                    "wakeup_entry_trade_mode_ok_arr"
                ],
                "wakeup_entry_atr_ok": arrays["wakeup_entry_atr_ok_arr"],
                "wakeup_entry_volume_ok": arrays["wakeup_entry_volume_ok_arr"],
                "wakeup_entry_candidate_height_value": arrays[
                    "wakeup_entry_candidate_height_value_arr"
                ],
                "wakeup_entry_candidate_height_threshold": arrays[
                    "wakeup_entry_candidate_height_threshold_arr"
                ],
                "wakeup_entry_candidate_age_bars": arrays[
                    "wakeup_entry_candidate_age_bars_arr"
                ],
                "wakeup_entry_candidate_leg_direction": arrays[
                    "wakeup_entry_candidate_leg_direction_arr"
                ],
                "wakeup_entry_atr_ratio": arrays["wakeup_entry_atr_ratio_arr"],
                "wakeup_entry_volume_ratio": arrays[
                    "wakeup_entry_volume_ratio_arr"
                ],
                "wakeup_cycle_age_bars": arrays["wakeup_cycle_age_bars_arr"],
                "wakeup_bars_since_fresh_candidate": arrays[
                    "wakeup_bars_since_fresh_candidate_arr"
                ],
                "wakeup_exit_ttl_triggered": arrays[
                    "wakeup_exit_ttl_triggered_arr"
                ],
                "wakeup_exit_no_fresh_candidate_triggered": arrays[
                    "wakeup_exit_no_fresh_candidate_triggered_arr"
                ],
                "wakeup_exit_close_triggered": arrays[
                    "wakeup_exit_close_triggered_arr"
                ],
                "wakeup_exit_action_mode": arrays["wakeup_exit_action_mode_arr"],
                "wakeup_exit_reason": arrays["wakeup_exit_reason_arr"],
                "wakeup_position_action": arrays["wakeup_position_action_arr"],
                "wakeup_active_direction": arrays["wakeup_active_direction_arr"],
                "wakeup_lock_cycle_direction_config": arrays[
                    "wakeup_lock_cycle_direction_config_arr"
                ],
                "position_freeze_active": arrays["position_freeze_active_arr"],
                "position_freeze_bars_left": arrays[
                    "position_freeze_bars_left_arr"
                ],
                "position_freeze_ignored_opposite_st_flip": arrays[
                    "position_freeze_ignored_opposite_st_flip_arr"
                ],
                "position_freeze_release_action": arrays[
                    "position_freeze_release_action_arr"
                ],
            }
        )

    return ZigZagSTFilterResult(
        positions=arrays["filtered_positions"],
        filter_diagnostics=filter_diagnostics_out,
        internal_legs=None,
        filter_config_snapshot=(
            volume_runtime.filter_config_snapshot if volume_runtime is not None else None
        ),
    )


def apply(
    *,
    trend: np.ndarray,
    trade_mode: str,
    trade_filter_config: Any,
    zigzag_global_stats: ZigZagGlobalStats,
    # close is required unless per_bar is supplied directly (test override).
    close: "Optional[np.ndarray]" = None,
    # open_prices accepted for run_backtest_fast API symmetry; not used here.
    open_prices: "Optional[np.ndarray]" = None,
    global_offset: int = 0,
    execution_model: Any = None,
    # Optional test override: inject a pre-computed ZigZagPerBar instead of
    # computing it from ``close``.  Use only in unit tests.
    per_bar: "Optional[ZigZagPerBar]" = None,
    # NEW (daily_reset): DatetimeIndex for calendar-day reset inference.
    index: "Optional[pd.Index]" = None,
    # NEW (daily_reset): test-override for injecting reset event array directly.
    daily_reset_event: "Optional[np.ndarray]" = None,
    # NEW (time_filter): test-override — inject (in_window, reset_event) arrays.
    # When None, resolved from trade_filter_config.time_filter via
    # _infer_time_filter_events (docs/time_filter_plan_v1_final.txt §4.1).
    time_filter_events: "Optional[tuple[np.ndarray, np.ndarray]]" = None,
    volume_runtime: "Optional[VolumeRuntime]" = None,
    volume: "Optional[np.ndarray]" = None,
    high: "Optional[np.ndarray]" = None,
    low: "Optional[np.ndarray]" = None,
) -> "ZigZagSTFilterResult":
    """Run the ZigZag ST FSM and build ``filtered_positions``.

    ZigZag pivot/height calculation is close-only. Mode D wakeup ATR
    expansion uses high/low/close runtime arrays.

    FSM is the **behavioural source of truth** for ``filtered_positions``
    (plan §2.2, §5.4).  ``generate_positions`` is NOT called on the
    enabled path — ST flips drive ``held_pos`` which is maintained by the
    FSM directly (§8.3, §17.13).

    Parameters
    ----------
    trend : np.ndarray  (1-D, ints in {-1, 0, +1})
        SuperTrend direction at each bar.  Only ``+1 ↔ -1`` transitions
        are tradable flips (plan §5.5 / spec §17.14).
    trade_mode : str
        One of ``"long"``, ``"short"``, ``"both"``, ``"revers"``.
    trade_filter_config : Any
        Duck-typed config with ``triggers`` and ``lifecycle`` sub-blocks.
        Typically a ``wf_grid.config.schema.TradeFilterConfig`` after WP2
        validation.
    zigzag_global_stats : ZigZagGlobalStats
        WP3 full-dataset stats.  Provides ``global_median``,
        ``candidate_trigger_threshold``, ``reversal_threshold``, and
        ``local_window`` (extracted from config path on the call site).
    close : np.ndarray, optional
        Close price array.  Required when ``per_bar`` is ``None``.
        Passed to ``compute_zigzag_per_bar`` internally. Also required with
        ``high``/``low`` when Mode D wakeup ATR expansion is enabled.
    high, low : np.ndarray, optional
        Runtime OHLC arrays for Mode D wakeup ATR expansion. They are not
        used by ZigZag pivot/height calculations.
    open_prices : np.ndarray, optional
        Accepted for ``run_backtest_fast`` API symmetry (§8.3.1) but
        not used inside ZigZag pivot/height calculations.
    global_offset : int
        Metadata only (passed through for diagnostics, not used in
        pivot/height formula per §4.3).
    execution_model : Any
        Accepted for API symmetry; not used in Phase 1.
    per_bar : ZigZagPerBar, optional
        **Test override only.**  If supplied, ``close`` is not required
        and ``compute_zigzag_per_bar`` is skipped.  Production callers
        must always supply ``close`` instead.

    Returns
    -------
    ZigZagSTFilterResult
        ``positions`` — FSM-driven filtered position stream (dtype int8);
        ``filter_diagnostics`` — per-bar arrays
            (``trade_filter_state``, ``trade_filter_state_code``,
             ``trade_filter_trigger_source``, ``confirmed_legs_since_start``,
             ``st_flip_dir``);
        ``internal_legs`` — ``None`` (plan §3.2).

    Notes
    -----
    Plan reference:  §2.2, §5, §5.4, §5.5, §8.3.
    Spec  reference: Appendix A v1.1 §4..§9, §10, §14, §15, §16, §17.
    """
    # ------------------------------------------------------------------
    # 1) Pre-compute n из самого надёжного источника (план v3 §6.1 / §0.14).
    #    Приоритет: per_bar (test-override) > trend > close.
    # ------------------------------------------------------------------
    n_pre = _precompute_apply_length(per_bar=per_bar, trend=trend, close=close)
    reset_events = _resolve_apply_reset_events(
        trade_filter_config=trade_filter_config,
        index=index,
        n=n_pre,
        daily_reset_event=daily_reset_event,
        time_filter_events=time_filter_events,
    )
    daily_reset_enabled = reset_events.daily_reset_enabled
    daily_reset_event = reset_events.daily_reset_event
    time_filter_in_window = reset_events.time_filter_in_window
    time_filter_reset_event = reset_events.time_filter_reset_event
    combined_reset_event = reset_events.combined_reset_event
    _tfl_enabled = reset_events.time_filter_enabled

    # ------------------------------------------------------------------
    # 2) Resolve daily_reset_enabled из config (план v3 §6.1).
    # ------------------------------------------------------------------
    _zz_cfg = _extract_zigzag_field(trade_filter_config, "zigzag")
    _vol_cfg = getattr(trade_filter_config, "volume", None)
    daily_reset_enabled = bool(
        _extract_zigzag_field(_zz_cfg, "daily_reset", False)
    ) if _zz_cfg is not None else False
    daily_reset_enabled = daily_reset_enabled or bool(
        getattr(_vol_cfg, "daily_reset", False)
    )

    # ------------------------------------------------------------------
    # 3) Resolve daily_reset_event (план v3 §6.1 / §0.15).
    # ------------------------------------------------------------------
    if daily_reset_event is None:
        daily_reset_event = _infer_daily_reset_event(
            index, n_pre, enabled=daily_reset_enabled
        )
    else:
        # §0.15: нормализуем override через np.asarray + ndim check
        daily_reset_event = np.asarray(daily_reset_event, dtype=bool)
        if daily_reset_event.ndim != 1:
            raise ConfigError(
                f"apply() daily_reset_event must be 1-D, "
                f"got ndim={daily_reset_event.ndim}"
            )
        if daily_reset_event.shape[0] != n_pre:
            raise ConfigError(
                f"apply() daily_reset_event length "
                f"{daily_reset_event.shape[0]} != n={n_pre}"
            )

    # ------------------------------------------------------------------
    # 3b) Resolve time_filter events (docs/time_filter_plan_v1_final.txt §4.1).
    # ------------------------------------------------------------------
    _tfl_cfg = getattr(trade_filter_config, "time_filter", None)
    _tfl_enabled = bool(getattr(_tfl_cfg, "enabled", False)) if _tfl_cfg is not None else False

    if time_filter_events is None:
        # Production path: infer from config resolver fields.
        _tfl_sh = getattr(_tfl_cfg, "_start_hour", None) if _tfl_cfg is not None else None
        _tfl_sm = getattr(_tfl_cfg, "_start_minute", None) if _tfl_cfg is not None else None
        _tfl_eh = getattr(_tfl_cfg, "_end_hour", None) if _tfl_cfg is not None else None
        _tfl_em = getattr(_tfl_cfg, "_end_minute", None) if _tfl_cfg is not None else None
        if _tfl_enabled and any(x is None for x in (_tfl_sh, _tfl_sm, _tfl_eh, _tfl_em)):
            raise ConfigError(
                "apply(): time_filter.enabled=True but resolver fields are None; "
                "ensure resolve_time_filter_in_place() is called before apply()"
            )
        time_filter_in_window, time_filter_reset_event = _infer_time_filter_events(
            index, n_pre,
            enabled=_tfl_enabled,
            start_h=int(_tfl_sh) if _tfl_sh is not None else 0,
            start_m=int(_tfl_sm) if _tfl_sm is not None else 0,
            end_h=int(_tfl_eh) if _tfl_eh is not None else 0,
            end_m=int(_tfl_em) if _tfl_em is not None else 0,
        )
    else:
        # Test-override path: normalize both arrays.
        _raw_in_w, _raw_reset = time_filter_events
        time_filter_in_window = np.asarray(_raw_in_w, dtype=bool)
        time_filter_reset_event = np.asarray(_raw_reset, dtype=bool)
        if time_filter_in_window.ndim != 1 or time_filter_in_window.shape[0] != n_pre:
            raise ConfigError(
                f"apply() time_filter_events[0] (in_window) must be 1-D bool "
                f"of length n={n_pre}"
            )
        if time_filter_reset_event.ndim != 1 or time_filter_reset_event.shape[0] != n_pre:
            raise ConfigError(
                f"apply() time_filter_events[1] (reset_event) must be 1-D bool "
                f"of length n={n_pre}"
            )

    # ------------------------------------------------------------------
    # 3c) Combined reset mask (docs/time_filter_plan_v1_final.txt §4.2).
    # Priority: daily_reset > time_filter_reset > all others.
    # ZigZag passes receive combined_reset_event so candidate-state is
    # correctly wiped on both reset sources (§4.3 — critical correctness fix).
    # ------------------------------------------------------------------
    combined_reset_event: np.ndarray = daily_reset_event | time_filter_reset_event

    if per_bar is None:
        trend_probe = np.asarray(trend)
        if trend_probe.ndim == 1 and int(trend_probe.shape[0]) != n_pre:
            raise ConfigError(
                f"[BUG] apply() pre-computed n={n_pre} != "
                f"trend n={int(trend_probe.shape[0])}"
            )
        if close is not None:
            close_probe = np.asarray(close)
            if close_probe.ndim == 1 and int(close_probe.shape[0]) != n_pre:
                raise ConfigError(
                    f"[BUG] apply() pre-computed n={n_pre} != "
                    f"close n={int(close_probe.shape[0])}"
                )

    # ------------------------------------------------------------------
    # 4) Compute per_bar if not supplied (production path).
    # ------------------------------------------------------------------
    if per_bar is None:
        if close is None:
            raise ConfigError(
                "apply() requires either 'close' (production path) or "
                "'per_bar' (test override) to be provided"
            )
        reversal_threshold = float(
            getattr(
                getattr(zigzag_global_stats, "reversal_threshold", None),
                "__float__",
                lambda: zigzag_global_stats.reversal_threshold,
            )()
            if callable(getattr(zigzag_global_stats, "reversal_threshold", None))
            else zigzag_global_stats.reversal_threshold
        )
        # local_window comes from trade_filter_config.zigzag.local_window
        zigzag_cfg = getattr(trade_filter_config, "zigzag", None)
        if zigzag_cfg is None:
            raise ConfigError(
                "apply() requires trade_filter_config.zigzag for per_bar "
                "computation; missing zigzag block"
            )
        local_window = getattr(zigzag_cfg, "local_window", None)
        if local_window is None:
            raise ConfigError(
                "apply() requires trade_filter_config.zigzag.local_window "
                "for per_bar computation; got None"
            )
        per_bar = compute_zigzag_per_bar(
            close=np.asarray(close, dtype=np.float64),
            reversal_threshold=reversal_threshold,
            local_window=int(local_window),
            # §4.3: combined_reset_event (not bare daily_reset_event) so
            # ZigZag candidate-state is wiped on both reset sources.
            daily_reset_event=combined_reset_event,
        )

    # ------------------------------------------------------------------
    # 5) Validate inputs and resolve config.
    #    §0.14: sanity — pre-computed n_pre должен совпасть с n_validated.
    # ------------------------------------------------------------------
    n = _validate_apply_inputs(
        trend=trend, per_bar=per_bar,
        zigzag_global_stats=zigzag_global_stats,
        trade_filter_config=trade_filter_config,
        trade_mode=trade_mode,
    )
    if n != n_pre:
        raise ConfigError(
            f"[BUG] apply() pre-computed n={n_pre} != "
            f"validated n={n}"
        )

    volume_condition_allowed_runtime = None
    volume_condition_block_reason_runtime = None
    volume_regime_runtime = None
    volume_initial_direction_runtime = None
    volume_median_relative_runtime = None
    volume_condition_block_reason_labels = None
    volume_regime_labels = None
    volume_initial_direction_labels = None
    if volume_runtime is not None:
        from supertrend_optimizer.core.volume_metrics import (
            materialize_volume_block_reason,
            materialize_volume_initial_direction,
            materialize_volume_regime,
        )

        volume_condition_allowed_runtime = np.asarray(
            volume_runtime.volume_condition_allowed, dtype=bool
        )
        volume_condition_block_reason_runtime = np.asarray(
            volume_runtime.volume_condition_block_reason
        )
        volume_regime_runtime = np.asarray(volume_runtime.volume_regime)
        volume_initial_direction_runtime = np.asarray(
            volume_runtime.volume_initial_direction
        )
        volume_median_relative_runtime = np.asarray(
            volume_runtime.median_relative_volume, dtype=np.float64
        )
        _volume_arrays = {
            "volume_condition_allowed": volume_condition_allowed_runtime,
            "volume_condition_block_reason": volume_condition_block_reason_runtime,
            "volume_regime": volume_regime_runtime,
            "volume_initial_direction": volume_initial_direction_runtime,
            "median_relative_volume": volume_median_relative_runtime,
        }
        for _name, _arr in _volume_arrays.items():
            if _arr.ndim != 1 or _arr.shape[0] != n:
                raise ConfigError(
                    f"apply() volume_runtime.{_name} must be 1-D with length "
                    f"n={n}; got shape={_arr.shape}"
                )
        volume_condition_block_reason_labels = materialize_volume_block_reason(
            volume_condition_block_reason_runtime
        )
        volume_regime_labels = materialize_volume_regime(volume_regime_runtime)
        volume_initial_direction_labels = materialize_volume_initial_direction(
            volume_initial_direction_runtime
        )
    volume_arrays = _materialize_apply_volume_runtime(volume_runtime, n=n)

    a_enabled, b_enabled = _resolve_trigger_toggles(trade_filter_config)
    freeze_confirmed_legs = _resolve_freeze_confirmed_legs(trade_filter_config)

    # Exit-off mode B: ZZ leg count stop (docs/plan_exit_off_modes.txt)
    _lc_apply = getattr(trade_filter_config, "lifecycle", None)
    is_exit_b = False
    exit_off_zz_target = -1
    exit_off_mode_echo = "exit A"
    exit_off_zz_leg_echo = -1
    if _lc_apply is not None:
        _eom_v = getattr(_lc_apply, "exit_off_mode", "exit A")
        if _eom_v == "exit B":
            is_exit_b = True
            _c_v = getattr(_lc_apply, "exit_off_zz_leg_count", None)
            if isinstance(_c_v, int) and not isinstance(_c_v, bool) and _c_v >= 1:
                exit_off_zz_target = int(_c_v)
            exit_off_mode_echo = "exit B"
            if isinstance(_c_v, int) and not isinstance(_c_v, bool):
                exit_off_zz_leg_echo = int(_c_v)
            else:
                exit_off_zz_leg_echo = -1
        elif _eom_v == "exit C":
            exit_off_mode_echo = "exit C"
            exit_off_zz_leg_echo = -1

    if is_exit_b and exit_off_zz_target < 1:
        raise ConfigError(
            "apply(): exit_off_mode='exit B' requires exit_off_zz_leg_count >= 1; "
            f"got {getattr(_lc_apply, 'exit_off_zz_leg_count', None)!r}. "
            "Ensure trade filter config is validated before calling apply()."
        )

    # exit_b_immediate_off: runtime fail-fast (Plan v3 §4.1).
    # Use `is not False` (identity, not equality) so int 0 is rejected:
    # 0 == False is True in Python, but 0 is not False is True.
    _imm_raw = getattr(_lc_apply, "exit_b_immediate_off", False) if _lc_apply is not None else False
    if is_exit_b and not isinstance(_imm_raw, bool):
        raise ConfigError(
            "apply(): exit_b_immediate_off must be bool when exit_off_mode='exit B'; "
            f"got {type(_imm_raw).__name__!r} ({_imm_raw!r}). "
            "Ensure trade filter config is validated before calling apply()."
        )
    if (not is_exit_b) and _imm_raw is not False:
        raise ConfigError(
            "apply(): exit_b_immediate_off must be False when exit_off_mode != 'exit B'; "
            f"got {_imm_raw!r}. "
            "Ensure trade filter config is validated before calling apply()."
        )
    exit_b_immediate_off_flag: bool = bool(_imm_raw) if is_exit_b else False

    candidate_trigger_threshold = float(zigzag_global_stats.candidate_trigger_threshold)
    global_median = float(zigzag_global_stats.global_median)
    if not math.isfinite(candidate_trigger_threshold) or not math.isfinite(global_median):
        raise ConfigError(
            "apply() requires finite zigzag_global_stats.candidate_trigger_threshold "
            "and zigzag_global_stats.global_median; got "
            f"candidate_trigger_threshold={candidate_trigger_threshold!r}, "
            f"global_median={global_median!r}"
        )

    trend_arr = np.asarray(trend, dtype=np.int64)
    cand_height = per_bar.candidate_height_pct
    confirm_event = per_bar.confirm_event
    local_median_N = per_bar.local_median_N
    local_median_available = per_bar.local_median_available

    # ------------------------------------------------------------------
    # 3) Output arrays — existing + §13 full keyset (WP9).
    # ------------------------------------------------------------------
    filtered_positions = np.zeros(n, dtype=np.int8)
    state_arr = np.full(n, _FSM_STATE_NAMES[int(ZigZagFSMState.OFF)], dtype=object)
    state_code_arr = np.full(n, int(ZigZagFSMState.OFF), dtype=np.int64)
    trigger_source_arr = np.full(n, _TRIGGER_SOURCE_NONE, dtype=object)
    confirmed_legs_since_start_arr = np.full(n, -1, dtype=np.int64)
    st_flip_dir_arr = np.zeros(n, dtype=np.int8)

    # §13 constant arrays (scalars broadcast to per-bar)
    _rev_thr = float(zigzag_global_stats.reversal_threshold)
    _ctt_val = float(candidate_trigger_threshold)
    _gm_val = float(global_median)
    _fcl_val = int(freeze_confirmed_legs)
    try:
        _zcfg = _extract_zigzag_field(trade_filter_config, "zigzag")
        _lw_val = int(_extract_zigzag_field(_zcfg, "local_window") or 0)
    except Exception:
        _lw_val = 0

    trade_filter_enabled_arr = np.ones(n, dtype=np.int8)
    reversal_threshold_arr = np.full(n, _rev_thr, dtype=np.float64)
    ctt_diag_arr = np.full(n, _ctt_val, dtype=np.float64)
    local_window_arr = np.full(n, _lw_val, dtype=np.int64)
    global_median_arr = np.full(n, _gm_val, dtype=np.float64)
    global_stats_available_arr = np.ones(n, dtype=np.int8)
    freeze_confirmed_legs_arr = np.full(n, _fcl_val, dtype=np.int64)

    # §13 per-bar arrays computed in the FSM loop
    median_stop_triggered_arr = np.zeros(n, dtype=np.int8)
    stopping_started_at_arr = np.full(n, -1, dtype=np.int64)
    filter_allowed_entry_arr = np.zeros(n, dtype=np.int8)
    filter_block_reason_arr = np.full(n, "none", dtype=object)

    exit_off_mode_arr = np.full(n, exit_off_mode_echo, dtype=object)
    exit_off_zz_leg_count_arr = np.full(
        n, np.int64(exit_off_zz_leg_echo), dtype=np.int64
    )
    zz_legs_since_lifecycle_start_arr = np.full(n, -1, dtype=np.int64)
    zz_leg_stop_triggered_arr = np.zeros(n, dtype=np.int8)

    # Plan v3 §4.2: exit_b_immediate_off diagnostics — ALWAYS allocated
    # (filled with zeros when exit_b_immediate_off_flag is False).
    exit_b_immediate_off_triggered_arr = np.zeros(n, dtype=np.int8)
    exit_b_immediate_off_config_arr = np.full(
        n, np.int8(1 if exit_b_immediate_off_flag else 0), dtype=np.int8
    )

    # ------------------------------------------------------------------
    # WP-V3-4: Runtime primitives & snapshots (ТЗ v3 §7, §10.2, §10.3).
    # Resolve effective ZigZag mode and duration gate from materialised
    # ``zigzag_global_stats`` (set by WP-V3-2 loader / build_global_stats).
    # ``apply()`` itself does NOT re-resolve mode — it consumes the value.
    # ------------------------------------------------------------------
    resolved_mode = str(getattr(zigzag_global_stats, "zigzag_mode", "A") or "A")
    gate_enabled = bool(
        getattr(zigzag_global_stats, "candidate_duration_gate_enabled", False)
    )
    _gate_max_bars = getattr(zigzag_global_stats, "candidate_duration_max_bars", None)
    gate_max_bars = int(_gate_max_bars) if (gate_enabled and _gate_max_bars is not None) else -1
    pure_mode_b = (resolved_mode == "B")

    # ТЗ v3 §11 / WP-V3-4 P6: Mode B + enabled gate must emit exactly one
    # INFO log per ``apply()`` invocation (gate is materialised but does
    # not influence Mode B runtime decisions).
    if pure_mode_b and gate_enabled:
        _logger.info(
            "ZigZag-ST: trade_filter.zigzag.mode=B is active with an "
            "enabled candidate_duration_gate (max_bars=%d); the gate is "
            "kept in metadata for diagnostics only and does NOT affect "
            "Mode B runtime decisions (ТЗ v3 §8.2).",
            gate_max_bars,
        )

    # Per-bar v3 primitive arrays (§10.2 + §10.3).  All length n, int8.
    candidate_threshold_ok_arr = np.zeros(n, dtype=np.int8)
    candidate_component_ok_arr = np.zeros(n, dtype=np.int8)
    confirmed_median_ok_arr = np.zeros(n, dtype=np.int8)
    b_component_ok_arr = np.zeros(n, dtype=np.int8)
    immediate_allowed_arr = np.zeros(n, dtype=np.int8)
    candidate_duration_gate_passed_arr = np.zeros(n, dtype=np.int8)

    # WP-V3-4 P7 / ТЗ v3 §7, §9: snapshot arrays (immutable values captured
    # at the START of every bar step, before any FSM transition — including
    # the daily-reset wipe — can mutate them).  Naming follows the canonical
    # field names from ТЗ v3 §7: state_at_bar_start / held_pos_at_bar_start
    # / confirmed_legs_at_bar_start.
    state_at_bar_start_arr = np.full(
        n, int(ZigZagFSMState.OFF), dtype=np.int64,
    )
    held_pos_at_bar_start_arr = np.zeros(n, dtype=np.int8)
    confirmed_legs_at_bar_start_arr = np.full(n, -1, dtype=np.int64)

    # Per-bar v3 candidate arrays (sourced from WP-V3-3 ``ZigZagPerBar``).
    # Backward-compat: legacy call sites may pass ``ZigZagPerBar`` without
    # the v3 fields populated → fall back to UNKNOWN defaults (age=-1,
    # direction=0).  Production path always supplies real arrays via
    # ``compute_zigzag_per_bar``.
    cand_age_bars = per_bar.candidate_age_bars
    if cand_age_bars is None:
        cand_age_bars = np.full(n, -1, dtype=np.int64)
    cand_leg_dir = per_bar.candidate_leg_direction
    if cand_leg_dir is None:
        cand_leg_dir = np.zeros(n, dtype=np.int8)

    mode_d_enabled = resolved_mode == "D"
    wakeup_candidate_height_cfg = None
    wakeup_candidate_age_cfg = None
    wakeup_atr_cfg = None
    wakeup_volume_cfg = None
    wakeup_ttl_cfg = None
    wakeup_no_fresh_cfg = None
    wakeup_action_cfg = None
    wakeup_atr_ratio = None
    wakeup_volume_ratio = None
    wakeup_entry_candidate_height_threshold = None
    wakeup_no_fresh_candidate_height_threshold = None
    wakeup_exit_action_mode = "none"
    if mode_d_enabled:
        (
            wakeup_candidate_height_cfg,
            wakeup_candidate_age_cfg,
            wakeup_atr_cfg,
            wakeup_volume_cfg,
        ) = _resolve_mode_d_wakeup_entry(trade_filter_config)
        (
            wakeup_ttl_cfg,
            wakeup_no_fresh_cfg,
            wakeup_action_cfg,
        ) = _resolve_mode_d_wakeup_exit(trade_filter_config)
        wakeup_entry_candidate_height_threshold = getattr(
            zigzag_global_stats,
            "wakeup_entry_candidate_height_threshold",
            None,
        )
        wakeup_no_fresh_candidate_height_threshold = getattr(
            zigzag_global_stats,
            "wakeup_no_fresh_candidate_height_threshold",
            None,
        )
        wakeup_exit_action_mode = str(
            getattr(wakeup_action_cfg, "mode", None) or "none"
        )
        if _is_component_enabled(wakeup_atr_cfg):
            high_arr = _require_wakeup_ohlc_array("high", high, n)
            low_arr = _require_wakeup_ohlc_array("low", low, n)
            close_arr = _require_wakeup_ohlc_array("close", close, n)
            _validate_wakeup_ohlc(high_arr, low_arr, close_arr)
            wakeup_atr_ratio = _compute_wakeup_atr_ratio(
                high_arr,
                low_arr,
                close_arr,
                int(getattr(wakeup_atr_cfg, "short_window")),
                int(getattr(wakeup_atr_cfg, "long_window")),
            )
        if _is_component_enabled(wakeup_volume_cfg):
            volume_arr = _require_1d_len("volume", volume, n)
            wakeup_volume_ratio = _compute_wakeup_volume_ratio(
                volume_arr,
                int(getattr(wakeup_volume_cfg, "short_window")),
                int(getattr(wakeup_volume_cfg, "baseline_window")),
            )

    wakeup_regime_cfg = getattr(trade_filter_config, "wakeup_regime", None)
    wakeup_lock_cycle_direction = (
        mode_d_enabled
        and wakeup_regime_cfg is not None
        and getattr(wakeup_regime_cfg, "enabled", False) is True
        and getattr(wakeup_regime_cfg, "lock_cycle_direction", False) is True
    )
    position_freeze_cfg = (
        getattr(wakeup_regime_cfg, "position_freeze", None)
        if wakeup_regime_cfg is not None else None
    )
    position_freeze_enabled = (
        mode_d_enabled
        and wakeup_regime_cfg is not None
        and getattr(wakeup_regime_cfg, "enabled", False) is True
        and position_freeze_cfg is not None
        and getattr(position_freeze_cfg, "enabled", False) is True
    )
    position_freeze_min_hold_bars = (
        int(getattr(position_freeze_cfg, "min_hold_bars", 0) or 0)
        if position_freeze_enabled else 0
    )

    # WP-V3-7: §10.2 additional diagnostic arrays (enabled filter path only).
    # Disabled-filter path never calls apply(), so these arrays never appear
    # in the disabled baseline — satisfying the "no new arrays for disabled
    # path" requirement.
    zigzag_mode_arr = np.full(n, resolved_mode, dtype=object)
    # int8: 1 = gate enabled, 0 = gate disabled.
    candidate_duration_gate_enabled_arr = np.full(
        n, np.int8(1 if gate_enabled else 0), dtype=np.int8
    )
    # int64: -1 when gate disabled (§5 representation boundary), else max_bars.
    # ``gate_max_bars`` is already -1 when gate is disabled (resolved above).
    candidate_duration_max_bars_arr = np.full(n, gate_max_bars, dtype=np.int64)
    immediate_used_arr = np.zeros(n, dtype=np.int8)
    # Default "mode_not_c" is overwritten per-bar inside the loop.
    immediate_block_reason_arr = np.full(n, "mode_not_c", dtype=object)
    _apply_arrays = _allocate_apply_arrays(
        n=n,
        zigzag_global_stats=zigzag_global_stats,
        candidate_trigger_threshold=candidate_trigger_threshold,
        global_median=global_median,
        freeze_confirmed_legs=freeze_confirmed_legs,
        trade_filter_config=trade_filter_config,
        exit_off_mode_echo=exit_off_mode_echo,
        exit_off_zz_leg_echo=exit_off_zz_leg_echo,
        exit_b_immediate_off_flag=exit_b_immediate_off_flag,
        resolved_mode=resolved_mode,
        gate_enabled=gate_enabled,
        gate_max_bars=gate_max_bars,
        wakeup_lock_cycle_direction=wakeup_lock_cycle_direction,
    )
    filtered_positions = _apply_arrays["filtered_positions"]
    state_arr = _apply_arrays["state_arr"]
    state_code_arr = _apply_arrays["state_code_arr"]
    trigger_source_arr = _apply_arrays["trigger_source_arr"]
    confirmed_legs_since_start_arr = _apply_arrays[
        "confirmed_legs_since_start_arr"
    ]
    st_flip_dir_arr = _apply_arrays["st_flip_dir_arr"]
    trade_filter_enabled_arr = _apply_arrays["trade_filter_enabled_arr"]
    reversal_threshold_arr = _apply_arrays["reversal_threshold_arr"]
    ctt_diag_arr = _apply_arrays["ctt_diag_arr"]
    local_window_arr = _apply_arrays["local_window_arr"]
    global_median_arr = _apply_arrays["global_median_arr"]
    global_stats_available_arr = _apply_arrays["global_stats_available_arr"]
    freeze_confirmed_legs_arr = _apply_arrays["freeze_confirmed_legs_arr"]
    median_stop_triggered_arr = _apply_arrays["median_stop_triggered_arr"]
    stopping_started_at_arr = _apply_arrays["stopping_started_at_arr"]
    filter_allowed_entry_arr = _apply_arrays["filter_allowed_entry_arr"]
    filter_block_reason_arr = _apply_arrays["filter_block_reason_arr"]
    exit_off_mode_arr = _apply_arrays["exit_off_mode_arr"]
    exit_off_zz_leg_count_arr = _apply_arrays["exit_off_zz_leg_count_arr"]
    zz_legs_since_lifecycle_start_arr = _apply_arrays[
        "zz_legs_since_lifecycle_start_arr"
    ]
    zz_leg_stop_triggered_arr = _apply_arrays["zz_leg_stop_triggered_arr"]
    exit_b_immediate_off_triggered_arr = _apply_arrays[
        "exit_b_immediate_off_triggered_arr"
    ]
    exit_b_immediate_off_config_arr = _apply_arrays[
        "exit_b_immediate_off_config_arr"
    ]
    candidate_threshold_ok_arr = _apply_arrays["candidate_threshold_ok_arr"]
    candidate_component_ok_arr = _apply_arrays["candidate_component_ok_arr"]
    confirmed_median_ok_arr = _apply_arrays["confirmed_median_ok_arr"]
    b_component_ok_arr = _apply_arrays["b_component_ok_arr"]
    immediate_allowed_arr = _apply_arrays["immediate_allowed_arr"]
    candidate_duration_gate_passed_arr = _apply_arrays[
        "candidate_duration_gate_passed_arr"
    ]
    state_at_bar_start_arr = _apply_arrays["state_at_bar_start_arr"]
    held_pos_at_bar_start_arr = _apply_arrays["held_pos_at_bar_start_arr"]
    confirmed_legs_at_bar_start_arr = _apply_arrays[
        "confirmed_legs_at_bar_start_arr"
    ]
    zigzag_mode_arr = _apply_arrays["zigzag_mode_arr"]
    candidate_duration_gate_enabled_arr = _apply_arrays[
        "candidate_duration_gate_enabled_arr"
    ]
    candidate_duration_max_bars_arr = _apply_arrays[
        "candidate_duration_max_bars_arr"
    ]
    immediate_used_arr = _apply_arrays["immediate_used_arr"]
    immediate_block_reason_arr = _apply_arrays["immediate_block_reason_arr"]
    wakeup_regime_active_arr = _apply_arrays["wakeup_regime_active_arr"]
    wakeup_cycle_age_bars_arr = _apply_arrays["wakeup_cycle_age_bars_arr"]
    wakeup_bars_since_fresh_candidate_arr = _apply_arrays[
        "wakeup_bars_since_fresh_candidate_arr"
    ]
    wakeup_exit_ttl_triggered_arr = _apply_arrays[
        "wakeup_exit_ttl_triggered_arr"
    ]
    wakeup_exit_no_fresh_candidate_triggered_arr = _apply_arrays[
        "wakeup_exit_no_fresh_candidate_triggered_arr"
    ]
    wakeup_exit_close_triggered_arr = _apply_arrays[
        "wakeup_exit_close_triggered_arr"
    ]
    wakeup_exit_action_mode_arr = _apply_arrays["wakeup_exit_action_mode_arr"]
    wakeup_exit_reason_arr = _apply_arrays["wakeup_exit_reason_arr"]
    wakeup_position_action_arr = _apply_arrays["wakeup_position_action_arr"]
    wakeup_active_direction_arr = _apply_arrays["wakeup_active_direction_arr"]
    position_freeze_active_arr = _apply_arrays["position_freeze_active_arr"]
    position_freeze_bars_left_arr = _apply_arrays[
        "position_freeze_bars_left_arr"
    ]
    position_freeze_ignored_opposite_st_flip_arr = _apply_arrays[
        "position_freeze_ignored_opposite_st_flip_arr"
    ]
    position_freeze_release_action_arr = _apply_arrays[
        "position_freeze_release_action_arr"
    ]
    if mode_d_enabled:
        wakeup_exit_action_mode_arr[:] = wakeup_exit_action_mode

    state = ZigZagFSMState.OFF
    confirmed_legs_since_start = -1   # -1 = lifecycle never started
    zz_legs_since_lifecycle_start = -1
    held_pos: int = 0                 # FSM-owned position (§2.2 / §5.4)
    _stopping_start: int = -1         # bar index when STOPPING lifecycle started
    (
        wakeup_cycle_age,
        wakeup_bars_since_fresh,
        wakeup_active_direction,
        wakeup_exit_c_fired,
    ) = _wakeup_runtime_off()
    cycle_direction = 0
    pos_freeze_until = -1
    pos_freeze_pending = False

    # ------------------------------------------------------------------
    # 4) Main FSM loop.
    # ------------------------------------------------------------------
    for t in range(n):
        just_reached_exit_b_threshold = False
        wakeup_exit_c_triggered_this_bar = False
        wakeup_exit_reason_this_bar = "none"
        wakeup_position_action_this_bar = "none"
        wakeup_internal_st_flip_this_bar = False
        wakeup_active_direction_for_diag = 0
        # ----- §9 step 0: Capture immutable bar-start snapshots ----------
        # WP-V3-4 P7 / ТЗ v3 §9 step 0: snapshots are captured BEFORE the
        # daily-reset wipe (§9 step 1) so that on a reset bar the snapshot
        # still reflects the PRE-reset state/held_pos/counter.  Subsequent
        # same-bar mutations of ``state``/``held_pos``/``confirmed_legs_
        # since_start`` (reset, OFF→WAIT, WAIT→FREEZE, etc.) cannot leak
        # back into the snapshot views.
        state_at_bar_start = state
        held_pos_at_bar_start = held_pos
        confirmed_legs_at_bar_start = confirmed_legs_since_start
        state_at_bar_start_arr[t] = int(state_at_bar_start)
        held_pos_at_bar_start_arr[t] = np.int8(held_pos_at_bar_start)
        confirmed_legs_at_bar_start_arr[t] = confirmed_legs_at_bar_start

        # ----- §9 step 1: combined-reset wipe (after snapshots) ----------
        # combined_reset_event covers both daily_reset and time_filter_reset
        # (docs/time_filter_plan_v1_final.txt §4.4).
        if combined_reset_event[t]:
            if mode_d_enabled and state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE:
                wakeup_exit_reason_this_bar = "reset"
                wakeup_position_action_this_bar = "exit_reset"
            state = ZigZagFSMState.OFF
            confirmed_legs_since_start = -1
            zz_legs_since_lifecycle_start = -1
            held_pos = 0
            _stopping_start = -1
            cycle_direction = 0
            (
                wakeup_cycle_age,
                wakeup_bars_since_fresh,
                wakeup_active_direction,
                wakeup_exit_c_fired,
            ) = _wakeup_runtime_off()
            pos_freeze_until = -1
            pos_freeze_pending = False

        # ----- Detect events at close(t) --------------------------------
        prev_trend = int(trend_arr[t - 1]) if t > 0 else 0
        curr_trend = int(trend_arr[t])
        flip_dir = detect_st_flip(prev_trend, curr_trend)
        st_flip_dir_arr[t] = flip_dir

        c_h = float(cand_height[t]) if not np.isnan(cand_height[t]) else float("nan")

        confirmed = bool(confirm_event[t] == 1)
        median_valid = bool(local_median_available[t]) and math.isfinite(
            float(local_median_N[t])
        )

        # ------------------------------------------------------------------
        # WP-V3-4: Compute mode-agnostic primitives §7.  These describe the
        # *eligibility* of each component independently of the legacy
        # ``a_enabled``/``b_enabled`` toggles (which still drive the
        # existing FSM until Mode dispatcher lands in WP-V3-5).  Reset bar
        # forces every primitive false (§7.7).
        # ------------------------------------------------------------------
        # §4.5: is_reset driven by combined_reset_event (daily OR time_filter).
        is_reset = bool(combined_reset_event[t])

        # §7.1 candidate_threshold_ok
        candidate_threshold_ok = (
            (not is_reset)
            and math.isfinite(c_h)
            and c_h >= candidate_trigger_threshold
        )

        # §7.2 duration_ok
        if not gate_enabled:
            duration_ok = True
        else:
            age = int(cand_age_bars[t])
            duration_ok = (
                (not is_reset)
                and age > 0
                and age <= gate_max_bars
            )

        # §7.3 candidate_component_ok
        candidate_component_ok = candidate_threshold_ok and (
            True if pure_mode_b else duration_ok
        )

        # §7.4 confirmed_median_ok — mode-agnostic B condition.  Note: this
        # primitive does NOT consume the legacy ``b_enabled`` toggle; the
        # Mode dispatcher in WP-V3-5 will gate it via ``resolved_mode``.
        confirmed_median_condition = (
            confirmed
            and median_valid
            and float(local_median_N[t]) >= global_median
        )
        confirmed_median_ok = (not is_reset) and confirmed_median_condition

        # §7.5 b_component_ok
        b_component_ok = confirmed_median_ok

        # §7.6 immediate_allowed (reset bar forces false per §7.7)
        cand_dir_t = int(cand_leg_dir[t])
        immediate_allowed = (
            (not is_reset)
            and cand_dir_t in (-1, +1)
            and _trade_mode_allows_direction(cand_dir_t, trade_mode)
        )

        # §10.3 candidate_duration_gate_passed
        # - Gate disabled → always 1.
        # - Pure Mode B → always 1, even when gate enabled (gate has no
        #   runtime effect in Mode B; ТЗ v3 §8.2 / §10.3 special case).
        # - Otherwise mirrors duration_ok.
        if not gate_enabled or pure_mode_b:
            cand_duration_gate_passed = True
        else:
            cand_duration_gate_passed = duration_ok

        candidate_threshold_ok_arr[t] = np.int8(1 if candidate_threshold_ok else 0)
        candidate_component_ok_arr[t] = np.int8(1 if candidate_component_ok else 0)
        confirmed_median_ok_arr[t] = np.int8(1 if confirmed_median_ok else 0)
        b_component_ok_arr[t] = np.int8(1 if b_component_ok else 0)
        immediate_allowed_arr[t] = np.int8(1 if immediate_allowed else 0)
        candidate_duration_gate_passed_arr[t] = np.int8(
            1 if cand_duration_gate_passed else 0
        )

        wakeup_entry_all_ok = False
        if mode_d_enabled:
            wakeup_entry = _evaluate_wakeup_entry(
                candidate_height=c_h,
                candidate_age=int(cand_age_bars[t]),
                candidate_direction=cand_dir_t,
                trade_mode=trade_mode,
                candidate_height_cfg=wakeup_candidate_height_cfg,
                candidate_age_cfg=wakeup_candidate_age_cfg,
                atr_cfg=wakeup_atr_cfg,
                volume_cfg=wakeup_volume_cfg,
                candidate_height_threshold=wakeup_entry_candidate_height_threshold,
                wakeup_atr_ratio=wakeup_atr_ratio,
                wakeup_volume_ratio=wakeup_volume_ratio,
                t=t,
            )
            wakeup_entry_all_ok = wakeup_entry.all_ok
            _record_wakeup_entry_diagnostics(
                arrays=_apply_arrays,
                t=t,
                evaluation=wakeup_entry,
                candidate_height=c_h,
                candidate_height_threshold=wakeup_entry_candidate_height_threshold,
                candidate_direction=cand_dir_t,
            )

        # ------------------------------------------------------------------
        # WP-V3-5: Unified mode dispatcher (§8 Mode Semantics + §9 step 2).
        # Mode-specific OFF transitions ONLY when state_at_bar_start == OFF
        # AND not on a daily-reset bar (§9 step 2).  Outside OFF, repeated
        # triggers are silently suppressed (§8 / §15.3).  D3 invariant:
        # trigger_source != "none" iff actual OFF departure on a non-reset
        # bar.
        # ------------------------------------------------------------------
        immediate_used_this_bar = False
        volume_blocked_lifecycle_start = False
        # §4.6: entry allowed only when FSM is OFF, not on reset bar, AND
        # inside the time_filter window. When time_filter is disabled,
        # in_window is all-ones (short-circuit), so no behaviour change.
        if state_at_bar_start == ZigZagFSMState.OFF and not is_reset and bool(time_filter_in_window[t]):
            if mode_d_enabled:
                if wakeup_entry_all_ok:
                    state = ZigZagFSMState.ST_ACTIVE_FREEZE
                    held_pos = cand_dir_t
                    confirmed_legs_since_start = -1
                    zz_legs_since_lifecycle_start = -1
                    trigger_source_arr[t] = _TRIGGER_SOURCE_WAKEUP
            else:
                volume_allowed = (
                    volume_condition_allowed_runtime is None
                    or bool(volume_condition_allowed_runtime[t])
                )
                lifecycle_start = _try_lifecycle_start_from_off(
                    t=t,
                    state=state_at_bar_start,
                    resolved_mode=resolved_mode,
                    candidate_component_ok=candidate_component_ok,
                    b_component_ok=b_component_ok,
                    immediate_allowed=immediate_allowed,
                    is_exit_b=is_exit_b,
                    cand_dir_t=cand_dir_t,
                    volume_allowed=volume_allowed,
                )
                if lifecycle_start is None and not volume_allowed:
                    volume_blocked_lifecycle_start = (
                        _try_lifecycle_start_from_off(
                            t=t,
                            state=state_at_bar_start,
                            resolved_mode=resolved_mode,
                            candidate_component_ok=candidate_component_ok,
                            b_component_ok=b_component_ok,
                            immediate_allowed=immediate_allowed,
                            is_exit_b=is_exit_b,
                            cand_dir_t=cand_dir_t,
                            volume_allowed=True,
                        )
                        is not None
                    )
                if lifecycle_start is not None:
                    state = lifecycle_start.state
                    held_pos = lifecycle_start.held_pos
                    confirmed_legs_since_start = (
                        lifecycle_start.confirmed_legs_since_start
                    )
                    zz_legs_since_lifecycle_start = (
                        lifecycle_start.zz_legs_since_lifecycle_start
                    )
                    trigger_source_arr[t] = lifecycle_start.trigger_source
                    immediate_used_this_bar = lifecycle_start.immediate_used_this_bar

        if mode_d_enabled:
            wakeup_state_active = state == ZigZagFSMState.ST_ACTIVE_FREEZE
            wakeup_started_this_bar = trigger_source_arr[t] == _TRIGGER_SOURCE_WAKEUP
            if wakeup_started_this_bar:
                if wakeup_lock_cycle_direction:
                    cycle_direction = cand_dir_t
                    wakeup_active_direction = cycle_direction
                else:
                    cycle_direction = 0
                    wakeup_active_direction = cand_dir_t
            wakeup_fresh_active_direction = wakeup_active_direction
            wakeup_fresh_candidate = _is_wakeup_fresh_candidate(
                no_fresh_cfg=wakeup_no_fresh_cfg,
                active_direction=wakeup_fresh_active_direction,
                candidate_direction=cand_dir_t,
                candidate_age=int(cand_age_bars[t]),
                candidate_height=c_h,
                threshold=wakeup_no_fresh_candidate_height_threshold,
            )
            if wakeup_started_this_bar:
                wakeup_cycle_age = 0
                wakeup_bars_since_fresh = 0 if wakeup_fresh_candidate else 1
                wakeup_exit_c_fired = False
            elif (
                state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
                and wakeup_state_active
                and not is_reset
            ):
                wakeup_cycle_age = max(0, wakeup_cycle_age + 1)
                if wakeup_fresh_candidate:
                    wakeup_bars_since_fresh = 0
                elif wakeup_bars_since_fresh >= 0:
                    wakeup_bars_since_fresh += 1
            elif not wakeup_state_active:
                (
                    wakeup_cycle_age,
                    wakeup_bars_since_fresh,
                    wakeup_active_direction,
                    wakeup_exit_c_fired,
                ) = _wakeup_runtime_off()

            if wakeup_state_active:
                wakeup_regime_active_arr[t] = np.int8(1)
                wakeup_cycle_age_bars_arr[t] = int(wakeup_cycle_age)
                wakeup_bars_since_fresh_candidate_arr[t] = int(
                    wakeup_bars_since_fresh
                )
                wakeup_active_direction_for_diag = wakeup_active_direction

            if (
                wakeup_state_active
                and state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
                and not wakeup_exit_c_fired
                and not is_reset
            ):
                wakeup_exit_c_reason_this_bar = None
                if (
                    _is_component_enabled(wakeup_ttl_cfg)
                    and wakeup_cycle_age >= int(getattr(wakeup_ttl_cfg, "bars"))
                ):
                    wakeup_exit_ttl_triggered_arr[t] = np.int8(1)
                    wakeup_exit_c_reason_this_bar = "ttl"
                elif (
                    _is_component_enabled(wakeup_no_fresh_cfg)
                    and wakeup_bars_since_fresh
                    >= int(getattr(wakeup_no_fresh_cfg, "timeout_bars"))
                ):
                    wakeup_exit_no_fresh_candidate_triggered_arr[t] = np.int8(1)
                    wakeup_exit_c_reason_this_bar = "no_fresh_candidate"

                if wakeup_exit_c_reason_this_bar is not None:
                    wakeup_exit_reason_this_bar = wakeup_exit_c_reason_this_bar
                    wakeup_exit_c_fired = True
                    wakeup_exit_c_triggered_this_bar = True
                    if wakeup_exit_c_reason_this_bar == "ttl":
                        wakeup_position_action_this_bar = "exit_ttl"
                    elif wakeup_exit_c_reason_this_bar == "no_fresh_candidate":
                        wakeup_position_action_this_bar = (
                            "exit_no_fresh_candidate"
                        )
                    if wakeup_exit_action_mode == "block_new_entries":
                        state = ZigZagFSMState.ST_STOPPING
                    elif wakeup_exit_action_mode == "close_position":
                        wakeup_exit_close_triggered_arr[t] = np.int8(1)
                        state = ZigZagFSMState.OFF
                        held_pos = 0
                        cycle_direction = 0
                        pos_freeze_until = -1
                        pos_freeze_pending = False
                        (
                            wakeup_cycle_age,
                            wakeup_bars_since_fresh,
                            wakeup_active_direction,
                            wakeup_exit_c_fired,
                        ) = _wakeup_runtime_off()

            wakeup_internal_st_flip_this_bar = (
                state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
                and state == ZigZagFSMState.ST_ACTIVE_FREEZE
                and not is_reset
                and not wakeup_exit_c_triggered_this_bar
                and flip_dir != 0
            )
            if wakeup_internal_st_flip_this_bar:
                freeze_active = (
                    position_freeze_enabled
                    and state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
                    and state == ZigZagFSMState.ST_ACTIVE_FREEZE
                    and held_pos != 0
                    and t <= pos_freeze_until
                )
                opposite_to_position = (
                    flip_dir != 0
                    and held_pos != 0
                    and flip_dir == -held_pos
                )
                if freeze_active and opposite_to_position:
                    wakeup_position_action_this_bar = (
                        "position_freeze_ignored_opposite_st_flip"
                    )
                    pos_freeze_pending = True
                    position_freeze_ignored_opposite_st_flip_arr[t] = np.int8(1)
                else:
                    effective_trade_mode = _effective_wakeup_trade_mode(
                        raw_trade_mode=trade_mode,
                        wakeup_lock_cycle_direction=wakeup_lock_cycle_direction,
                        cycle_direction=cycle_direction,
                    )
                    if effective_trade_mode is not None:
                        effective_direction = (
                            cycle_direction
                            if wakeup_lock_cycle_direction
                            else wakeup_active_direction
                        )
                        (
                            held_pos,
                            wakeup_active_direction,
                            wakeup_position_action_this_bar,
                        ) = _apply_mode_d_internal_st_flip(
                            held_pos=held_pos,
                            wakeup_active_direction=effective_direction,
                            flip_dir=flip_dir,
                            trade_mode=effective_trade_mode,
                        )
                        if wakeup_lock_cycle_direction:
                            wakeup_active_direction = cycle_direction
                wakeup_active_direction_for_diag = wakeup_active_direction

            if (
                position_freeze_enabled
                and pos_freeze_pending
                and t <= pos_freeze_until
                and state == ZigZagFSMState.ST_ACTIVE_FREEZE
                and held_pos != 0
                and curr_trend == held_pos
            ):
                pos_freeze_pending = False

            release_due = (
                position_freeze_enabled
                and state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
                and state == ZigZagFSMState.ST_ACTIVE_FREEZE
                and held_pos != 0
                and pos_freeze_pending
                and t > pos_freeze_until
                and not is_reset
                and not wakeup_exit_c_triggered_this_bar
            )
            if release_due:
                st_now = curr_trend
                if st_now == held_pos:
                    pos_freeze_pending = False
                    position_freeze_release_action_arr[t] = "noop_st_realigned"
                else:
                    effective_trade_mode = _effective_wakeup_trade_mode(
                        raw_trade_mode=trade_mode,
                        wakeup_lock_cycle_direction=wakeup_lock_cycle_direction,
                        cycle_direction=cycle_direction,
                    )
                    if effective_trade_mode is None:
                        pos_freeze_pending = False
                        position_freeze_release_action_arr[t] = (
                            "noop_invalid_lock_state"
                        )
                    else:
                        effective_direction = (
                            cycle_direction
                            if wakeup_lock_cycle_direction
                            else wakeup_active_direction
                        )
                        (
                            held_pos,
                            wakeup_active_direction,
                            release_action,
                        ) = _apply_mode_d_internal_st_flip(
                            held_pos=held_pos,
                            wakeup_active_direction=effective_direction,
                            flip_dir=st_now,
                            trade_mode=effective_trade_mode,
                        )
                        if wakeup_lock_cycle_direction:
                            wakeup_active_direction = cycle_direction
                        pos_freeze_pending = False
                        if release_action == "none":
                            position_freeze_release_action_arr[t] = (
                                "noop_st_realigned"
                            )
                        else:
                            position_freeze_release_action_arr[t] = (
                                "applied_" + release_action
                            )
                            wakeup_position_action_this_bar = release_action
                        wakeup_active_direction_for_diag = wakeup_active_direction

            if (
                position_freeze_enabled
                and state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
                and state == ZigZagFSMState.ST_ACTIVE_FREEZE
                and held_pos != 0
                and t <= pos_freeze_until
            ):
                position_freeze_active_arr[t] = np.int8(1)
                position_freeze_bars_left_arr[t] = max(
                    int(pos_freeze_until) - t + 1, 0
                )

            wakeup_exit_reason_arr[t] = wakeup_exit_reason_this_bar
            wakeup_position_action_arr[t] = wakeup_position_action_this_bar
            wakeup_active_direction_arr[t] = np.int8(wakeup_active_direction_for_diag)

        # WP-V3-7: §10.4 immediate_candidate_entry_block_reason priority.
        # Computed AFTER the dispatcher so ``immediate_used_this_bar`` is
        # already set.
        #
        # Full whitelist from ТЗ v3 §10.4 (priority order):
        #   1. daily_reset
        #   2. filter_off         ← UNREACHABLE from apply(): the disabled-filter
        #                           path in backtest.run_single_backtest never
        #                           calls apply(), so filter_diagnostics=None and
        #                           no immediate arrays exist for disabled filters.
        #   3. state_not_off
        #   4. mode_not_c
        #   5. height_gate_failed
        #   6. duration_gate_failed
        #   7. unknown_candidate_direction
        #   8. trade_mode_disallows_direction
        #   9. none               ← only when immediate entry actually used
        if is_reset:
            _imm_reason = _IMM_REASON_DAILY_RESET
        elif state_at_bar_start != ZigZagFSMState.OFF:
            _imm_reason = _IMM_REASON_STATE_NOT_OFF
        elif resolved_mode not in ("C", "C+B"):
            _imm_reason = _IMM_REASON_MODE_NOT_C
        elif not candidate_threshold_ok:
            _imm_reason = _IMM_REASON_HEIGHT_GATE_FAILED
        elif gate_enabled and not duration_ok:
            _imm_reason = _IMM_REASON_DURATION_GATE_FAILED
        elif cand_dir_t == 0:
            _imm_reason = _IMM_REASON_UNKNOWN_DIR
        elif not _trade_mode_allows_direction(cand_dir_t, trade_mode):
            _imm_reason = _IMM_REASON_TRADE_MODE_DISALLOWS
        else:
            # All checks passed → immediate entry succeeded.
            _imm_reason = _IMM_REASON_NONE
        immediate_used_arr[t] = np.int8(1 if immediate_used_this_bar else 0)
        immediate_block_reason_arr[t] = _imm_reason

        # ----- FSM transitions (canonical order, plan §5.2 / §5.5) -----

        # 2) WAIT → FREEZE (allowed ST flip).  Same-bar trigger + allowed
        #    flip is the canonical "trigger first, then flip" path (§5.5).
        #    Mode C immediate entry skips WAIT entirely and writes FREEZE
        #    directly above; same-bar opposite ST flip on a Mode C entry bar
        #    cannot reach this branch (state is already FREEZE, not WAIT).
        if state == ZigZagFSMState.WAIT_FIRST_ST_FLIP and flip_dir != 0:
            if _is_first_flip_allowed(flip_dir, trade_mode):
                if is_exit_b:
                    state = ZigZagFSMState.ST_COUNTING_ZZ_LEGS
                    zz_legs_since_lifecycle_start = 0
                    confirmed_legs_since_start = -1
                else:
                    state = ZigZagFSMState.ST_ACTIVE_FREEZE
                    confirmed_legs_since_start = 0
                held_pos = flip_dir   # lifecycle entry position (§5.2)

        # 3) Increment confirmed_legs_since_start on confirm bars in
        #    active states.  Gated on ``state_at_bar_start`` so the
        #    same-bar lifecycle start (WAIT → FREEZE) does NOT count its
        #    own coincident confirm event (spec §4.3 / plan §3.3 step 6).
        #
        # Reset-gate (plan_exit_off_modes_v2.txt §5 step 7, invariant R3 §11.6):
        # ``not is_reset`` ensures that on a reset bar with confirm_event[t]==1
        # the counter does NOT receive a spurious +1 over the wiped sentinel
        # (-1 → 0). Required for BOTH counters — exit A and exit B — even when
        # confirm_event cannot currently coincide with daily_reset upstream;
        # the gate makes invariant R3 §11.6 explicit and protects against
        # future ZigZag pipeline changes (PR3 §13).
        if (
            confirmed
            and not is_reset
            and state_at_bar_start in (
                ZigZagFSMState.ST_ACTIVE_FREEZE,
                ZigZagFSMState.ST_ACTIVE_MONITORING,
            )
        ):
            confirmed_legs_since_start += 1

        if (
            confirmed
            and not is_reset
            and state_at_bar_start == ZigZagFSMState.ST_COUNTING_ZZ_LEGS
        ):
            zz_legs_since_lifecycle_start += 1

        if not is_exit_b and not mode_d_enabled:
            # 4) FREEZE → MONITORING once freeze_confirmed_legs legs have
            #    accumulated.  ``freeze_confirmed_legs == 0`` → immediate
            #    transition (counter=0 >= 0).
            if state == ZigZagFSMState.ST_ACTIVE_FREEZE:
                if confirmed_legs_since_start >= freeze_confirmed_legs:
                    state = ZigZagFSMState.ST_ACTIVE_MONITORING

            # 5) MONITORING → STOPPING on confirm-bar median check (§4.4 /
            #    §15.7 fail-closed).  Gated on ``state_at_bar_start`` so the
            #    confirm-bar that completes FREEZE → MONITORING does NOT fire
            #    stop-check on the same bar (spec §17.16).
            if (
                state == ZigZagFSMState.ST_ACTIVE_MONITORING
                and state_at_bar_start == ZigZagFSMState.ST_ACTIVE_MONITORING
                and confirmed
            ):
                if median_valid:
                    if float(local_median_N[t]) < global_median:
                        state = ZigZagFSMState.ST_STOPPING
                        median_stop_triggered_arr[t] = np.int8(1)  # §13 (WP9)
                    # else stay in MONITORING
                else:
                    state = ZigZagFSMState.ST_STOPPING  # fail-closed

        if (
            is_exit_b
            and state == ZigZagFSMState.ST_COUNTING_ZZ_LEGS
            and exit_off_zz_target >= 1
            and zz_legs_since_lifecycle_start >= exit_off_zz_target
        ):
            # Plan v3 §4.3: zz_leg_stop_triggered fires in BOTH modes
            # (legacy and immediate-off) — invariant for backward compat.
            zz_leg_stop_triggered_arr[t] = np.int8(1)
            if exit_b_immediate_off_flag:
                # Immediate OFF: lifecycle terminates as OFF on bar t.
                # OFF-invariants (same sentinel values as standard OFF init).
                exit_b_immediate_off_triggered_arr[t] = np.int8(1)
                state = ZigZagFSMState.OFF
                confirmed_legs_since_start = -1
                zz_legs_since_lifecycle_start = -1
                held_pos = 0
                # just_reached_exit_b_threshold stays False — only used by
                # legacy ST_STOPPING path (§4.3 — guard against same-bar close).
            else:
                state = ZigZagFSMState.ST_STOPPING
                just_reached_exit_b_threshold = True

        # 6) Update held_pos for ST flips while in active states.
        #    Only applies when the state was ALREADY active at bar_start
        #    (lifecycle start bar sets held_pos in step 2 above; we must
        #    not double-apply on the same bar).
        if (
            flip_dir != 0
            and not mode_d_enabled
            and state in (
                ZigZagFSMState.ST_ACTIVE_FREEZE,
                ZigZagFSMState.ST_ACTIVE_MONITORING,
                ZigZagFSMState.ST_COUNTING_ZZ_LEGS,
            )
            and state_at_bar_start in (
                ZigZagFSMState.ST_ACTIVE_FREEZE,
                ZigZagFSMState.ST_ACTIVE_MONITORING,
                ZigZagFSMState.ST_COUNTING_ZZ_LEGS,
            )
        ):
            held_pos = _update_held_pos(held_pos, flip_dir, trade_mode)

        if mode_d_enabled:
            real_opened = (
                trigger_source_arr[t] == _TRIGGER_SOURCE_WAKEUP
                and held_pos != 0
                and state == ZigZagFSMState.ST_ACTIVE_FREEZE
            )
            real_reversed = (
                wakeup_position_action_this_bar == "reverse_on_st_flip"
                and held_pos != 0
                and state == ZigZagFSMState.ST_ACTIVE_FREEZE
            )
            restored = (
                wakeup_position_action_this_bar
                == "restore_allowed_position_on_st_flip"
            )
            if (
                position_freeze_enabled
                and (real_opened or real_reversed)
                and not restored
            ):
                pos_freeze_until = t + position_freeze_min_hold_bars
                pos_freeze_pending = False

            if (
                wakeup_exit_c_triggered_this_bar
                or held_pos == 0
                or state in (
                    ZigZagFSMState.OFF,
                    ZigZagFSMState.WAIT_FIRST_ST_FLIP,
                    ZigZagFSMState.ST_STOPPING,
                )
            ):
                pos_freeze_until = -1
                pos_freeze_pending = False

        # ----- Compute filtered_positions[t+1] --------------------------
        # Decision at close(t), execution at open(t+1) (§5.5 / §17.13).
        cur_pos = filtered_positions[t]  # position in effect at open(t)

        # Normalise ST_STOPPING + no open position → OFF immediately.
        # Must happen BEFORE the t+1 position-write block so that on the
        # final bar (t == n-1) state_arr still records "OFF" per spec
        # §4.5, §14.17 and §15.7 (fail-closed: "if no open position, state
        # transitions to OFF immediately").
        if state == ZigZagFSMState.ST_STOPPING and cur_pos == 0:
            if not (is_exit_b and just_reached_exit_b_threshold):
                state = ZigZagFSMState.OFF
                confirmed_legs_since_start = -1
                zz_legs_since_lifecycle_start = -1
                held_pos = 0
                cycle_direction = 0

        if t + 1 < n:
            if state in (ZigZagFSMState.OFF, ZigZagFSMState.WAIT_FIRST_ST_FLIP):
                next_pos = np.int8(0)

            elif state in (
                ZigZagFSMState.ST_ACTIVE_FREEZE,
                ZigZagFSMState.ST_ACTIVE_MONITORING,
                ZigZagFSMState.ST_COUNTING_ZZ_LEGS,
            ):
                # FSM-owned position — NOT a passthrough of raw ST output
                # (plan §2.2 / §5.4; fixes WP5 P1 note).
                next_pos = np.int8(held_pos)

            else:  # ST_STOPPING — only reachable here when cur_pos != 0
                # Hold; close only on opposite ST flip (§4.5 / §17.21..§17.23).
                # No new entries, no reverses.  Exit B: no close on threshold bar.
                if just_reached_exit_b_threshold or (
                    mode_d_enabled and wakeup_exit_c_triggered_this_bar
                ):
                    next_pos = np.int8(cur_pos)
                elif flip_dir != 0 and (
                    (cur_pos > 0 and flip_dir == -1)
                    or (cur_pos < 0 and flip_dir == +1)
                ):
                    next_pos = np.int8(0)
                    state = ZigZagFSMState.OFF
                    confirmed_legs_since_start = -1
                    zz_legs_since_lifecycle_start = -1
                    held_pos = 0
                    cycle_direction = 0
                else:
                    next_pos = np.int8(cur_pos)

            filtered_positions[t + 1] = next_pos

            # §13 WP9: filter_allowed_entry
            if next_pos != 0 and (cur_pos == 0 or next_pos != cur_pos):
                filter_allowed_entry_arr[t] = np.int8(1)

        # §13 WP9: filter_block_reason — emitted only on a concrete
        # decision event that the filter actually blocked.  Priority order
        # (highest first; only one reason is set per bar):
        #
        #  1. local_median_unavailable — confirm-bar in MONITORING where
        #     fail-closed (unavailable/NaN/Inf median) fires MONITORING →
        #     STOPPING per §15.7 / §4.4.  Gated on state_at_bar_start ==
        #     ST_ACTIVE_MONITORING so the same-bar FREEZE→MONITORING
        #     transition does NOT emit this reason (§17.16).
        #  2. stopping_mode_no_new_entries — ST_STOPPING + flip that could
        #     have opened a new entry but is blocked by state (§4.5, §8.5).
        #  3. filter_off — OFF + flip blocked by state (§4.1, §8.1).
        #  4. trade_mode_disallowed_flip — WAIT + flip that arrived but
        #     is not allowed by trade_mode (§4.2, §9).
        #
        # Reasons NOT emitted at bar level (see priority spec in apply()
        # docstring for rationale):
        #  - "waiting_for_allowed_st_flip": passive WAIT bars without a
        #    flip are not a "blocked decision"; visible via trade-level
        #    entry_filter_state instead.
        #  - "invalid_stats": warmup / data-absent bars are noise, not
        #    blockages; covered by per-bar NaN diagnostics.
        #  - "insufficient_global_stats": ConfigError at init (§12.3).

        # Priority chain (docs/time_filter_plan_v1_final.txt §4.7):
        # 0: daily_reset
        # 1: time_filter_reset  ← only when daily_reset == 0
        # 2: local_median_unavailable
        # 3: stopping_mode_no_new_entries
        # 4: filter_off
        # 5: trade_mode_disallowed_flip

        # Priority 0: daily_reset (highest).
        if daily_reset_event[t]:
            filter_block_reason_arr[t] = "daily_reset"

        # Priority 1: time_filter_reset (only when daily_reset == 0).
        elif time_filter_reset_event[t]:
            filter_block_reason_arr[t] = "time_filter_reset"

        # Priority 2: local_median_unavailable.
        elif (
            state_at_bar_start == ZigZagFSMState.ST_ACTIVE_MONITORING
            and confirmed
            and not median_valid
        ):
            filter_block_reason_arr[t] = "local_median_unavailable"

        elif volume_blocked_lifecycle_start:
            zigzag_reason = "none"
            if flip_dir != 0:
                if state == ZigZagFSMState.ST_STOPPING:
                    zigzag_reason = "stopping_mode_no_new_entries"
                elif state == ZigZagFSMState.OFF:
                    zigzag_reason = "filter_off"
                elif state == ZigZagFSMState.WAIT_FIRST_ST_FLIP:
                    zigzag_reason = "trade_mode_disallowed_flip"
            filter_block_reason_arr[t] = select_block_reason(
                zigzag_reason,
                volume_condition_block_reason_labels[t],
            )

        # Priority 3-5: flip-based reasons (only when not already set
        # above and a flip event occurred on this bar).
        elif flip_dir != 0:
            if state == ZigZagFSMState.ST_STOPPING:
                filter_block_reason_arr[t] = "stopping_mode_no_new_entries"
            elif state == ZigZagFSMState.OFF:
                filter_block_reason_arr[t] = "filter_off"
            elif state == ZigZagFSMState.WAIT_FIRST_ST_FLIP:
                filter_block_reason_arr[t] = "trade_mode_disallowed_flip"

        # ----- Persist diagnostics for this bar -------------------------
        state_code_arr[t] = int(state)
        state_arr[t] = _FSM_STATE_NAMES[int(state)]
        confirmed_legs_since_start_arr[t] = confirmed_legs_since_start
        zz_legs_since_lifecycle_start_arr[t] = zz_legs_since_lifecycle_start

        # §13 WP9: stopping_started_at_index
        if state == ZigZagFSMState.ST_STOPPING:
            if _stopping_start < 0:
                _stopping_start = t
            stopping_started_at_arr[t] = _stopping_start
        else:
            if state_at_bar_start == ZigZagFSMState.ST_STOPPING:
                _stopping_start = -1  # reset after leaving STOPPING
            stopping_started_at_arr[t] = -1

    filter_diagnostics_out: Dict[str, np.ndarray] = {
        # --- existing keys (WP5–WP7) ---
        "trade_filter_state": state_arr,
        "trade_filter_state_code": state_code_arr,
        "trade_filter_trigger_source": trigger_source_arr,
        "confirmed_legs_since_start": confirmed_legs_since_start_arr,
        "st_flip_dir": st_flip_dir_arr,
        # --- §13 full keyset additions (WP9) ---
        "trade_filter_enabled": trade_filter_enabled_arr,
        "zigzag_reversal_threshold": reversal_threshold_arr,
        "candidate_height_pct": np.asarray(cand_height, dtype=np.float64),
        "candidate_trigger_threshold": ctt_diag_arr,
        "local_median_N": np.asarray(local_median_N, dtype=np.float64),
        "local_median_available": np.asarray(local_median_available, dtype=np.int8),
        "local_window": local_window_arr,
        "global_median": global_median_arr,
        "global_stats_available": global_stats_available_arr,
        "freeze_confirmed_legs": freeze_confirmed_legs_arr,
        "median_stop_triggered": median_stop_triggered_arr,
        "stopping_started_at_index": stopping_started_at_arr,
        "filter_allowed_entry": filter_allowed_entry_arr,
        "filter_block_reason": filter_block_reason_arr,
        "exit_off_mode": exit_off_mode_arr,
        "exit_off_zz_leg_count": exit_off_zz_leg_count_arr,
        "zz_legs_since_lifecycle_start": zz_legs_since_lifecycle_start_arr,
        "zz_leg_stop_triggered": zz_leg_stop_triggered_arr,
        # Plan v3 §4.7: always-present (zeros when flag is False).
        "exit_b_immediate_off_triggered": exit_b_immediate_off_triggered_arr,
        "exit_b_immediate_off_config": exit_b_immediate_off_config_arr,
        "daily_reset_enabled": np.full(n, int(daily_reset_enabled), dtype=np.int8),
        "daily_reset_event": np.asarray(daily_reset_event, dtype=np.int8),
        # --- time_filter diagnostics (docs/time_filter_plan_v1_final.txt §4.8) ---
        # Always-present when trade_filter.enabled=true.
        # When time_filter.enabled=false: enabled=0, in_window=all-ones, reset=all-zeros.
        "time_filter_enabled": np.full(n, np.int8(1 if _tfl_enabled else 0), dtype=np.int8),
        "time_filter_in_window": np.asarray(time_filter_in_window, dtype=np.int8),
        "time_filter_reset_event": np.asarray(time_filter_reset_event, dtype=np.int8),
        # --- v3 WP-V3-4: runtime primitives + bar-start snapshots ---
        # ТЗ v3 §10.2 / §10.3.  Existing key ``candidate_height_pct`` is
        # already exported above (WP9); the threshold-derived primitive is
        # added here.
        "candidate_threshold_ok": candidate_threshold_ok_arr,
        "candidate_component_ok": candidate_component_ok_arr,
        "confirmed_median_ok": confirmed_median_ok_arr,
        "b_component_ok": b_component_ok_arr,
        "immediate_allowed": immediate_allowed_arr,
        "candidate_duration_gate_passed": candidate_duration_gate_passed_arr,
        # WP-V3-4 P7 / ТЗ v3 §7, §9: per-bar immutable snapshots captured
        # at bar start (BEFORE daily-reset and any FSM transition).
        # Canonical names per ТЗ v3 §7; consumed by WP-V3-5 mode dispatcher
        # and WP-V3-6 FSM ordering hardening.
        "state_at_bar_start": state_at_bar_start_arr,
        "held_pos_at_bar_start": held_pos_at_bar_start_arr,
        "confirmed_legs_at_bar_start": confirmed_legs_at_bar_start_arr,
        # --- v3 WP-V3-7: additional §10.2 immediate diagnostics ---
        # candidate_height_pct already exported above (WP9 key).
        # candidate_age_bars / candidate_leg_direction echoed from per_bar.
        "zigzag_mode": zigzag_mode_arr,
        "candidate_age_bars": cand_age_bars,
        "candidate_leg_direction": cand_leg_dir,
        "candidate_duration_gate_enabled": candidate_duration_gate_enabled_arr,
        "candidate_duration_max_bars": candidate_duration_max_bars_arr,
        "immediate_candidate_entry_used": immediate_used_arr,
        "immediate_candidate_entry_block_reason": immediate_block_reason_arr,
    }
    if volume_runtime is not None:
        filter_diagnostics_out.update(
            {
                "volume_regime": volume_regime_labels,
                "volume_condition_allowed": volume_condition_allowed_runtime,
                "volume_condition_block_reason": volume_condition_block_reason_labels,
                "volume_initial_direction": volume_initial_direction_labels,
                "median_relative_volume": volume_median_relative_runtime,
            }
        )

    return _finalize_apply_result(
        n=n,
        arrays=_apply_arrays,
        cand_height=cand_height,
        local_median_N=local_median_N,
        local_median_available=local_median_available,
        cand_age_bars=cand_age_bars,
        cand_leg_dir=cand_leg_dir,
        reset_events=reset_events,
        volume_runtime=volume_runtime,
        volume_arrays=volume_arrays,
    )


# ---------------------------------------------------------------------------
# WP7 — Trade-level diagnostics helper (plan §8.3 / §8.4 / spec §10, §13).
# ---------------------------------------------------------------------------

def attach_trade_filter_diagnostics(
    trades_df: "Any",
    filter_diagnostics: Dict[str, np.ndarray],
) -> "Any":
    """Backward-compatible wrapper around the canonical diagnostics helper."""
    from supertrend_optimizer.core.filter_trade_diagnostics import (
        attach_trade_filter_diagnostics as _attach_trade_filter_diagnostics,
    )

    return _attach_trade_filter_diagnostics(trades_df, filter_diagnostics)
