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
from typing import Any, Dict, List, NamedTuple, Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.utils.exceptions import ConfigError

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


def _infer_daily_reset_event(
    index: "Optional[pd.Index]",
    n: int,
    *,
    enabled: bool,
) -> np.ndarray:
    """Calendar-day reset event mask (bool[n]).

    Source: алгоритм нормализации заимствован 1:1 из
    ``donor zigzag/engine/run.py:_infer_session_ids`` (plan v3 §4 / §0.9).
    Отличия от донора:
    - возвращает bool[]-event, а не int64[]-session_ids;
    - short-circuit при enabled=False (baseline bit-identity, §0.5);
    - non-monotonic / non-datetime → ConfigError (fail-closed, §0.3/§0.4).

    event[t] == True означает «бар t — первый бар нового календарного дня».
    event[0] всегда False — нет «предыдущего бара».
    """
    # §0.5: short-circuit ДО любых проверок индекса
    if not enabled:
        return np.zeros(n, dtype=bool)

    # §0.4: type-gate — enabled=True требует DatetimeIndex
    if not isinstance(index, pd.DatetimeIndex):
        raise ConfigError(
            "trade_filter.zigzag.daily_reset=true requires DatetimeIndex; "
            f"got {type(index).__name__}"
        )
    if len(index) != n:
        raise ConfigError(
            f"daily_reset: index length {len(index)} != n={n}"
        )

    # §0.3: monotonic-gate — нарушение = баг данных
    if not index.is_monotonic_increasing:
        raise ConfigError(
            "trade_filter.zigzag.daily_reset=true requires "
            "monotonic-increasing DatetimeIndex; got non-monotonic"
        )

    # Нормализация — 1:1 из donor zigzag/engine/run.py:98-102.
    # CRITICAL: tz_localize(None), не tz_convert(None) — иначе
    # MSK 23:55 сместится в UTC 20:55 и граница полуночи сломается.
    if index.tz is not None:
        normalized = index.tz_localize(None).normalize()
    else:
        normalized = index.normalize()

    days = normalized.astype("int64").to_numpy()
    event = np.zeros(n, dtype=bool)
    if n >= 2:
        event[1:] = days[1:] != days[:-1]
    # event[0] всегда False — нет «предыдущего дня»
    return event


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
        # Pre-extract heights for vectorised median over a sliding window.
        heights = np.array(
            [leg.height_pct for leg in pass_result.legs], dtype=np.float64
        )
        for t in range(n):
            idx = int(pass_result.confirmed_leg_idx_at_t[t])
            if idx < 0:
                continue
            count = idx + 1
            if count < local_window:
                continue
            window_heights = heights[idx - local_window + 1 : idx + 1]
            median = float(np.median(window_heights))
            if math.isfinite(median):
                local_median_N[t] = median
                local_median_available[t] = True
            # Non-finite median is left as NaN / available=False — fail-closed
            # for downstream ST_ACTIVE_MONITORING (Appendix A v1.1 §15.7).

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

    metadata: dict = {
        "candidate_trigger_source": candidate_trigger_source,
        "candidate_trigger_threshold_mode": threshold_mode,
        "candidate_trigger_quantile": candidate_trigger_quantile,
        "min_legs_for_quantile": min_legs_for_quantile,
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


# ---------------------------------------------------------------------------
# WP6 — ST flip detection (plan §5.5 / WP6, spec §3.3, §17.13–§17.14).
# ---------------------------------------------------------------------------

def detect_st_flip(prev_trend: int, curr_trend: int) -> int:
    """Return the ST flip direction at close(t).

    Public WP6 contract for ST flip detection from ``trend``.  The only
    tradable flips are between ``+1`` and ``-1``.  ``0 -> ±1`` is an
    initialization transition (SuperTrend bootstrap) and is **not** a
    tradable flip; ``±1 -> 0`` and ``0 -> 0`` are likewise non-tradable.

    Parameters
    ----------
    prev_trend : int
        SuperTrend direction at ``close(t-1)`` (``-1``, ``0`` or ``+1``).
    curr_trend : int
        SuperTrend direction at ``close(t)`` (``-1``, ``0`` or ``+1``).

    Returns
    -------
    int
        - ``+1``: long flip   (prev=-1, curr=+1)
        - ``-1``: short flip  (prev=+1, curr=-1)
        - ``0``:  no flip / non-tradable transition

    Notes
    -----
    Plan reference:  §5.5, WP6.
    Spec  reference: Appendix A v1.1 §3.3, §17.13–§17.14.
    """
    if prev_trend in (1, -1) and curr_trend in (1, -1) and prev_trend != curr_trend:
        return curr_trend
    return 0


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
) -> "ZigZagSTFilterResult":
    """Run the ZigZag ST FSM and build ``filtered_positions``.

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
        Passed to ``compute_zigzag_per_bar`` internally.
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
    if per_bar is not None:
        n_pre = int(per_bar.candidate_height_pct.shape[0])
    elif trend is not None:
        n_pre = int(np.asarray(trend).shape[0])
    elif close is not None:
        n_pre = int(np.asarray(close).shape[0])
    else:
        raise ConfigError(
            "apply() requires at least one of: per_bar, trend, close"
        )

    # ------------------------------------------------------------------
    # 2) Resolve daily_reset_enabled из config (план v3 §6.1).
    # ------------------------------------------------------------------
    _zz_cfg = _extract_zigzag_field(trade_filter_config, "zigzag")
    daily_reset_enabled = bool(
        _extract_zigzag_field(_zz_cfg, "daily_reset", False)
    ) if _zz_cfg is not None else False

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
            daily_reset_event=daily_reset_event,   # NEW (plan v3 §6.1)
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

    state = ZigZagFSMState.OFF
    confirmed_legs_since_start = -1   # -1 = lifecycle never started
    zz_legs_since_lifecycle_start = -1
    held_pos: int = 0                 # FSM-owned position (§2.2 / §5.4)
    _stopping_start: int = -1         # bar index when STOPPING lifecycle started

    # ------------------------------------------------------------------
    # 4) Main FSM loop.
    # ------------------------------------------------------------------
    for t in range(n):
        just_reached_exit_b_threshold = False
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

        # ----- §9 step 1: daily-reset wipe (after snapshots) -------------
        if daily_reset_event[t]:
            state = ZigZagFSMState.OFF
            confirmed_legs_since_start = -1
            zz_legs_since_lifecycle_start = -1
            held_pos = 0
            _stopping_start = -1

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
        is_reset = bool(daily_reset_event[t])

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

        # ------------------------------------------------------------------
        # WP-V3-5: Unified mode dispatcher (§8 Mode Semantics + §9 step 2).
        # Mode-specific OFF transitions ONLY when state_at_bar_start == OFF
        # AND not on a daily-reset bar (§9 step 2).  Outside OFF, repeated
        # triggers are silently suppressed (§8 / §15.3).  D3 invariant:
        # trigger_source != "none" iff actual OFF departure on a non-reset
        # bar.
        # ------------------------------------------------------------------
        immediate_used_this_bar = False
        if state_at_bar_start == ZigZagFSMState.OFF and not is_reset:
            if resolved_mode == "A":
                # §8.1: candidate_component_ok -> WAIT, source=candidate_threshold.
                if candidate_component_ok:
                    state = ZigZagFSMState.WAIT_FIRST_ST_FLIP
                    trigger_source_arr[t] = _TRIGGER_SOURCE_A

            elif resolved_mode == "B":
                # §8.2: b_component_ok -> WAIT, source=confirmed_median.
                # Duration gate is materialised but does NOT influence
                # Mode B runtime decisions (one-shot INFO logged at init).
                if b_component_ok:
                    state = ZigZagFSMState.WAIT_FIRST_ST_FLIP
                    trigger_source_arr[t] = _TRIGGER_SOURCE_B

            elif resolved_mode == "C":
                # §8.3: candidate_component_ok AND immediate_allowed
                #   -> FREEZE or ST_COUNTING_ZZ_LEGS (exit B), held_pos=candidate_leg_direction, used=1.
                # Pure C blocked by unknown direction/trade_mode stays OFF —
                # NO WAIT fallback (§8.3 last paragraph).
                if candidate_component_ok and immediate_allowed:
                    if is_exit_b:
                        state = ZigZagFSMState.ST_COUNTING_ZZ_LEGS
                        held_pos = cand_dir_t
                        zz_legs_since_lifecycle_start = 0
                        confirmed_legs_since_start = -1
                    else:
                        state = ZigZagFSMState.ST_ACTIVE_FREEZE
                        held_pos = cand_dir_t
                        confirmed_legs_since_start = 0
                    trigger_source_arr[t] = _TRIGGER_SOURCE_A
                    immediate_used_this_bar = True
                # else: stays OFF, source remains "none".

            elif resolved_mode == "A+B":
                # §8.4 table.  Gate-blocked A is not an A-source for
                # trigger_source (candidate_component_ok already enforces
                # gate, so a "false" component is naturally not counted).
                a_fired = candidate_component_ok
                b_fired = b_component_ok
                if a_fired and b_fired:
                    state = ZigZagFSMState.WAIT_FIRST_ST_FLIP
                    trigger_source_arr[t] = _TRIGGER_SOURCE_BOTH
                elif a_fired:
                    state = ZigZagFSMState.WAIT_FIRST_ST_FLIP
                    trigger_source_arr[t] = _TRIGGER_SOURCE_A
                elif b_fired:
                    state = ZigZagFSMState.WAIT_FIRST_ST_FLIP
                    trigger_source_arr[t] = _TRIGGER_SOURCE_B
                # else: OFF stays OFF, source remains "none".

            elif resolved_mode == "C+B":
                # §8.5 table.  C has priority over B; B-rescue applies when
                # immediate is blocked by direction/trade_mode but B fired.
                # Gate-blocked C is NOT a candidate source for trigger_source
                # (the false candidate_component_ok value already excludes it).
                c_fired = candidate_component_ok
                b_fired = b_component_ok
                if c_fired and immediate_allowed:
                    # OFF -> FREEZE or ST_COUNTING_ZZ_LEGS (exit B); trigger_source = "both" if B also fired.
                    if is_exit_b:
                        state = ZigZagFSMState.ST_COUNTING_ZZ_LEGS
                        held_pos = cand_dir_t
                        zz_legs_since_lifecycle_start = 0
                        confirmed_legs_since_start = -1
                    else:
                        state = ZigZagFSMState.ST_ACTIVE_FREEZE
                        held_pos = cand_dir_t
                        confirmed_legs_since_start = 0
                    trigger_source_arr[t] = (
                        _TRIGGER_SOURCE_BOTH if b_fired else _TRIGGER_SOURCE_A
                    )
                    immediate_used_this_bar = True
                elif c_fired and (not immediate_allowed) and b_fired:
                    # B-rescue: immediate blocked, B fired → OFF -> WAIT.
                    state = ZigZagFSMState.WAIT_FIRST_ST_FLIP
                    trigger_source_arr[t] = _TRIGGER_SOURCE_BOTH
                elif (not c_fired) and b_fired:
                    # B alone (gate-blocked C is not a candidate source).
                    state = ZigZagFSMState.WAIT_FIRST_ST_FLIP
                    trigger_source_arr[t] = _TRIGGER_SOURCE_B
                # else: OFF stays OFF (covers both
                #   {C true, immediate blocked, B false}  and
                #   {C false, B false} rows of §8.5).
            else:
                raise ConfigError(
                    f"apply(): unknown resolved ZigZag mode {resolved_mode!r}; "
                    f"expected one of: A, B, C, A+B, C+B"
                )

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

        if not is_exit_b:
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
                if just_reached_exit_b_threshold:
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

        # Priority 0: daily_reset (highest).
        if daily_reset_event[t]:
            filter_block_reason_arr[t] = "daily_reset"

        # Priority 1: local_median_unavailable (set before flip checks so
        # flip checks cannot overwrite it).
        elif (
            state_at_bar_start == ZigZagFSMState.ST_ACTIVE_MONITORING
            and confirmed
            and not median_valid
        ):
            filter_block_reason_arr[t] = "local_median_unavailable"

        # Priority 2-4: flip-based reasons (only when not already set
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

    return ZigZagSTFilterResult(
        positions=filtered_positions,
        filter_diagnostics=filter_diagnostics_out,
        internal_legs=None,
    )


# ---------------------------------------------------------------------------
# WP7 — Trade-level diagnostics helper (plan §8.3 / §8.4 / spec §10, §13).
# ---------------------------------------------------------------------------

def attach_trade_filter_diagnostics(
    trades_df: "Any",
    filter_diagnostics: Dict[str, np.ndarray],
) -> "Any":
    """Attach per-trade filter diagnostics columns to ``trades_df``.

    Called inside ``run_single_backtest`` after ``extract_trades``, on the
    extended-slice indices BEFORE any OOS trim or rebase.

    Indexing rule (``OPEN_TO_OPEN``, plan §8.4):
    - ``entry_signal_idx = max(entry_index - 1, 0)``  (close-decision bar)
    - ``exit_signal_idx  = max(exit_index  - 1, 0)``  (close-decision bar)
    - A trade whose ``exit_index`` is the last position slot may be either
      a real close on the final transition or a still-open trade emitted by
      ``extract_trades``' pending fallback.  Exit diagnostics are checked
      before the pending sentinel so final-slot reset exits stay visible.

    Columns added
    -------------
    entry_filter_state : str
        ``trade_filter_state`` on the close-decision bar for entry.
    entry_trigger_source : str
        ``trade_filter_trigger_source`` on the close-decision bar for entry.
    exit_reason : str
        Priority order (highest → lowest, §5.2 Plan v3):

        1. ``"filter_daily_reset"``          — daily_reset_event[exit_signal_idx]==1
        2. ``"pending_open_trade_at_end"``   — exit_index >= n_diag - 1
        3. ``"filter_exit_b_immediate_off"`` — exit_b_immediate_off_triggered
                                               [exit_signal_idx]==1 (new, Plan v3)
        4. ``"filter_stopping_opposite_flip"`` — FSM was in ST_STOPPING (legacy)
        5. ``"st_flip"``                     — fallback

        Backward compat: if ``exit_b_immediate_off_triggered`` is absent from
        ``filter_diagnostics``, priority #3 is silently skipped (imm_at_exit=False
        for all trades); priorities 1/2/4/5 remain unchanged (§5.3 / §10.3.F).

    Parameters
    ----------
    trades_df : pd.DataFrame
        Output of ``extract_trades`` with ``entry_index`` and
        ``exit_index`` columns (execution bars, 0-based).
    filter_diagnostics : dict[str, np.ndarray]
        Per-bar diagnostic arrays from ``apply()``, length ==
        ``len(positions)`` (already truncated for early-exit if
        applicable).

    Returns
    -------
    pd.DataFrame
        Copy of ``trades_df`` with three additional columns appended.

    Notes
    -----
    Plan reference:  §8.3, §8.4, §8.4.1.
    Spec  reference: Appendix A v1.1 §10, §13, §15.4, §17.13.
    """
    import pandas as _pd

    state_arr = filter_diagnostics.get("trade_filter_state")
    trigger_arr = filter_diagnostics.get("trade_filter_trigger_source")
    daily_reset_arr = filter_diagnostics.get("daily_reset_event")
    # Plan v3 §5.1: backward compat — absent key → imm_at_exit always False
    imm_triggered_arr = filter_diagnostics.get("exit_b_immediate_off_triggered")
    if state_arr is None:
        raise ConfigError(
            "attach_trade_filter_diagnostics: 'trade_filter_state' key "
            "missing from filter_diagnostics"
        )
    n_diag = len(state_arr)
    # Pending-trade sentinel: extract_trades also uses exit_idx=n for trades
    # that remain open after the loop.  The same index can be a real close on
    # the final transition, so concrete exit diagnostics take priority below.
    pending_exit_idx = n_diag - 1

    entry_filter_states = []
    entry_trigger_sources = []
    exit_reasons = []

    for row in trades_df.itertuples(index=False):
        entry_index = int(row.entry_index)
        exit_index = int(row.exit_index)

        # Entry side
        entry_signal_idx = max(entry_index - 1, 0)
        if entry_signal_idx < n_diag:
            entry_filter_states.append(str(state_arr[entry_signal_idx]))
        else:
            entry_filter_states.append("UNKNOWN")

        if trigger_arr is not None and entry_signal_idx < len(trigger_arr):
            entry_trigger_sources.append(str(trigger_arr[entry_signal_idx]))
        else:
            entry_trigger_sources.append("none")

        # Exit side
        exit_signal_idx = max(exit_index - 1, 0)
        if exit_signal_idx < n_diag:
            fsm_at_exit = str(state_arr[exit_signal_idx])
        else:
            fsm_at_exit = "UNKNOWN"
        reset_at_exit = (
            daily_reset_arr is not None
            and exit_signal_idx < len(daily_reset_arr)
            and int(daily_reset_arr[exit_signal_idx]) == 1
        )
        # Plan v3 §5.1: only True when array present AND bar flag==1
        imm_at_exit = (
            imm_triggered_arr is not None
            and exit_signal_idx < len(imm_triggered_arr)
            and int(imm_triggered_arr[exit_signal_idx]) == 1
        )

        # Priority chain (§5.2 Plan v3):
        if reset_at_exit:
            exit_reasons.append("filter_daily_reset")
        elif exit_index >= pending_exit_idx:
            # Priority #2 wins over immediate-off: if the exit execution bar
            # is the last position slot, the trade is classified as pending
            # regardless of whether immediate-off was triggered (§5.2 rationale
            # and §10.3.E / §10.3.G).
            exit_reasons.append("pending_open_trade_at_end")
        elif imm_at_exit:
            exit_reasons.append("filter_exit_b_immediate_off")
        elif fsm_at_exit == "ST_STOPPING":
            exit_reasons.append("filter_stopping_opposite_flip")
        else:
            exit_reasons.append("st_flip")

    result = trades_df.copy()
    result["entry_filter_state"] = entry_filter_states
    result["entry_trigger_source"] = entry_trigger_sources
    result["exit_reason"] = exit_reasons
    return result
