"""
Unified backtest execution engine.

This module provides a single entry point for running backtests,
used by both optimizer and tester.

DD-01: Trades считаются на полной истории (full history).
Warmup применяется ПОСЛЕ backtest только для метрик.
"""

import warnings
import numpy as np
import pandas as pd
from typing import Optional, Union

from supertrend_optimizer.core.amplitude_filter import compute_amplitude_filter
from supertrend_optimizer.core.backtest import (
    compute_trend_only,
    generate_positions,
    run_backtest_fast,
)
from supertrend_optimizer.core.zigzag_filter import compute_zigzag_filter
from supertrend_optimizer.core.calculator import (
    calculate_atr_rma,
    calculate_true_range,
)
from supertrend_optimizer.core.filters import (
    compute_allow_entry,
    compute_atr_pct,
    compute_filtered_reason,
    compute_volatility_pass,
    compute_volume_pass,
    entry_bar_for_decision,
)
from supertrend_optimizer.core.metrics import calculate_all_metrics
from supertrend_optimizer.core.trades import extract_trades
from supertrend_optimizer.engine.result import BacktestResult
from supertrend_optimizer.utils.constants import (
    FILTER_REASON_AMP_BELOW_THRESHOLD,
    FILTER_REASON_AMP_SEPARATION_FAIL,
    FILTER_REASON_AMP_WARMUP,
    FILTER_REASON_ATR_ABOVE_MAX,
    FILTER_REASON_ATR_BELOW_MIN,
    FILTER_REASON_ATR_FLOOR_BELOW,
    FILTER_REASON_ATR_NAN,
    FILTER_REASON_BOTH,
    FILTER_REASON_OK,
    FILTER_REASON_VOL_ABOVE_MAX,
    FILTER_REASON_VOL_BELOW_MIN,
    FILTER_REASON_VOL_MA_INVALID,
    FILTER_REASON_VOL_NAN,
    FILTER_REASON_ZZ_ARMED_WAITING,
    FILTER_REASON_ZZ_EXPIRED_NEW_PIVOT,
    FILTER_REASON_ZZ_EXPIRED_TIME,
    FILTER_REASON_ZZ_LOCKED_SAME_LEG,
    FILTER_REASON_ZZ_NOT_ARMED,
    FILTER_REASON_ZZ_PATHOLOGICAL,
    FILTER_REASON_ZZ_REGIME_OFF,
    FILTER_REASON_ZZ_WARMUP,
    INVALID_METRIC_VALUE,
)
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.time_utils import resolve_warmup_bars


# ---------------------------------------------------------------------------
# Session-reset helpers (plan §1.7, §3.4.2)
# ---------------------------------------------------------------------------


def _infer_session_ids(index) -> np.ndarray:
    """
    Calendar-day based session IDs (§1.7, plan v2.0).

    Rules:
    - tz-aware DatetimeIndex: tz_localize(None).normalize() preserves the
      wall-clock local date without shifting to UTC. Using tz_convert(None)
      would shift Moscow 23:55 to UTC 20:55 and break midnight boundary.
    - tz-naive DatetimeIndex: normalize() at midnight of the naive date.
    - Non-DatetimeIndex: returns all zeros (single session).
    - Non-monotonic DatetimeIndex: fallback to all zeros + WARN (§3.4.2a).
      Ensures session_reset never sees a rewind in session_ids.

    Returns:
        np.ndarray of shape (N,), dtype int64.  Monotonically non-decreasing
        for monotonic DatetimeIndex; constant zero for non-monotonic / non-datetime.
    """
    n = len(index)
    if not isinstance(index, pd.DatetimeIndex):
        return np.zeros(n, dtype=np.int64)
    if not index.is_monotonic_increasing:
        warnings.warn(
            "zigzag: non-monotonic DatetimeIndex; session_reset disabled "
            "(single-session mode)",
            stacklevel=2,
        )
        return np.zeros(n, dtype=np.int64)
    if index.tz is not None:
        normalized = index.tz_localize(None).normalize()
    else:
        normalized = index.normalize()
    return normalized.astype("int64").values


def _collapse_reasons_zz_volume(
    zz_reason: np.ndarray,
    vol_reason: np.ndarray,
    zz_allow: np.ndarray,
    flow_pass: np.ndarray,
) -> np.ndarray:
    """
    Reason-collapse for ``zigzag_and_volume`` (plan §1.6 both-collapse table,
    §3.4.3).

    Per-bar rules (same shape as amplitude_and_volume collapse):
        zz ok,  vol ok   → ok
        zz ok,  vol fail → reason from volume
        zz fail, vol ok  → reason from ZigZag (zz_*)
        zz fail, vol fail→ FILTER_REASON_BOTH

    Args:
        zz_reason:  object array (N,) of ZigZag reason strings.
        vol_reason: object array (N,) of volume reason strings.
        zz_allow:   bool array (N,) — ZigZag allow_entry.
        flow_pass:  bool array (N,) — volume pass.
    """
    assert zz_reason.shape == vol_reason.shape == zz_allow.shape == flow_pass.shape
    zz_fail = zz_reason != FILTER_REASON_OK
    vol_fail = vol_reason != FILTER_REASON_OK
    out = np.full(zz_reason.shape, FILTER_REASON_OK, dtype=object)
    only_zz = zz_fail & ~vol_fail
    only_vol = ~zz_fail & vol_fail
    both_fail = zz_fail & vol_fail
    out[only_zz] = zz_reason[only_zz]
    out[only_vol] = vol_reason[only_vol]
    out[both_fail] = FILTER_REASON_BOTH
    return out


# ---------------------------------------------------------------------------
# Filter diagnostics helpers (plan §5.2, §5.3, §5.4)
# ---------------------------------------------------------------------------


def _default_filters_cfg() -> dict:
    """Normalised ``filters_cfg`` equivalent to ``{"mode": "none"}``."""
    return {
        "mode": "none",
        "volatility": {"min_atr_pct": None, "max_atr_pct": None},
        "amplitude": {
            "n": 20,
            "min_separation": None,
            "lookback": 500,
            "q": 0.60,
            "atr_period": 14,
            "atr_floor": 0.0,
        },
        "volume": {
            "volume_column": "Volume",
            "volume_ma_column": "Volume MA",
            "min_ratio": None,
            "max_ratio": None,
        },
        # ZigZag filter defaults (plan v2.0 §2, §3.5.2)
        "zigzag": {
            "reversal_threshold": 0.005,
            "min_legs_global": 50,
            "q_strong": 0.80,
            "k_local": 5,
            "entry_side": "counter_trend",
            "arm_timeout_bars_since_extreme": 24,
            "arm_timeout_bars_hard": 78,
        },
    }


def _thresholds_for_diagnostics(
    filters_cfg: dict,
    global_volume_ma_mean: Optional[float] = None,
) -> dict:
    """Flatten the thresholds sub-dict matching ``filter_diagnostics`` §5.1.

    Column name (``volume_ma_column``) is stored as the user supplied it
    (original casing) so that ``filters_summary`` in Excel reflects the
    config verbatim.  Data lookup is always done via ``df[col.lower()]`` at
    the CLI layer, so preserving case here carries no behavioral risk.

    New semantics (volume filter):
        * ``volume_column`` is NOT part of diagnostics anymore — the new
          filter is based solely on ``volume_ma`` and a dataset-level
          baseline ``global_volume_ma_mean``.
        * ``global_volume_ma_mean`` is a float (may be NaN when no valid
          volume_ma values exist → fail-closed).
    """
    v = filters_cfg.get("volatility", {}) or {}
    f = filters_cfg.get("volume", {}) or {}
    a = filters_cfg.get("amplitude", {}) or {}
    vol_ma_col = f.get("volume_ma_column")
    out = {
        "min_atr_pct": v.get("min_atr_pct"),
        "max_atr_pct": v.get("max_atr_pct"),
        "min_ratio": f.get("min_ratio"),
        "max_ratio": f.get("max_ratio"),
        "volume_ma_column": vol_ma_col if isinstance(vol_ma_col, str) else None,
        "global_volume_ma_mean": (
            float(global_volume_ma_mean)
            if global_volume_ma_mean is not None
            else None
        ),
    }
    # Mode-specific sections (plan §3.0 — keys mutually exclusive between
    # amp-modes and zz-modes).
    mode = filters_cfg.get("mode", "none")
    # Amplitude section (v1.3). Echoed verbatim for amp-modes; absent for
    # other modes (otherwise it would leak cross-mode, violating §3.0).
    if mode in ("amplitude", "amplitude_and_volume") and a:
        out["amplitude"] = {
            "n": a.get("n"),
            "min_separation": a.get("min_separation"),
            "lookback": a.get("lookback"),
            "q": a.get("q"),
            "atr_period": a.get("atr_period"),
            "atr_floor": a.get("atr_floor"),
        }
    # ZigZag section (v2.0, plan §3.4.5).  Echoed verbatim for mode ∈
    # {zigzag, zigzag_and_volume}; absent for other modes.
    if mode in ("zigzag", "zigzag_and_volume"):
        z = filters_cfg.get("zigzag", {}) or {}
        out["zigzag"] = {
            "reversal_threshold": z.get("reversal_threshold"),
            "min_legs_global": z.get("min_legs_global"),
            "q_strong": z.get("q_strong"),
            "k_local": z.get("k_local"),
            "entry_side": z.get("entry_side"),
            "arm_timeout_bars_since_extreme": z.get("arm_timeout_bars_since_extreme"),
            "arm_timeout_bars_hard": z.get("arm_timeout_bars_hard"),
        }
    return out


def _empty_counters() -> dict:
    return {
        "raw_entry_signals": 0,
        "passed_entry_signals": 0,
        "blocked_entry_signals": 0,
        "blocked_by_volatility": 0,
        "blocked_by_volume": 0,
        "blocked_by_both": 0,
        "blocked_by_vol_ma_invalid": 0,
    }


# Historical name (kept for backward-compatibility with downstream consumers).
# In legacy modes (volatility, volatility_and_volume) this frozenset contains
# ATR-only reasons. In amplitude modes (v1.3, patch §D.1) it is extended with
# amp reasons so that amp-side blockings aggregate into the same
# ``blocked_by_volatility`` bucket without breaking counters shape. Display
# label in Excel summary is mode-dependent (see io/excel_tester).
_ATR_REASONS = frozenset({
    FILTER_REASON_ATR_BELOW_MIN,
    FILTER_REASON_ATR_ABOVE_MAX,
    FILTER_REASON_ATR_NAN,
    # Amplitude-side reasons (v1.3).
    FILTER_REASON_AMP_WARMUP,
    FILTER_REASON_AMP_SEPARATION_FAIL,
    FILTER_REASON_AMP_BELOW_THRESHOLD,
    FILTER_REASON_ATR_FLOOR_BELOW,
    # ZigZag-side reasons (v2.0).  Semantically these are "blocked_by_structure"
    # but the counters bucket name is kept as blocked_by_volatility for BC
    # (plan §3.4.1).
    FILTER_REASON_ZZ_WARMUP,
    FILTER_REASON_ZZ_REGIME_OFF,
    FILTER_REASON_ZZ_NOT_ARMED,
    FILTER_REASON_ZZ_ARMED_WAITING,
    FILTER_REASON_ZZ_EXPIRED_TIME,
    FILTER_REASON_ZZ_EXPIRED_NEW_PIVOT,
    FILTER_REASON_ZZ_LOCKED_SAME_LEG,
    FILTER_REASON_ZZ_PATHOLOGICAL,
})
_VOL_REASONS = frozenset({
    FILTER_REASON_VOL_BELOW_MIN,
    FILTER_REASON_VOL_ABOVE_MAX,
    # vol_nan is a defensive safety-net code in _volume_reason_array that is
    # unreachable on well-formed inputs (new semantics always produce
    # vol_ma_invalid instead). Including it here means that if it somehow
    # fires, the signal is attributed to "blocked_by_volume" rather than
    # disappearing into an untracked bucket — intentional design choice.
    FILTER_REASON_VOL_NAN,
    # vol_ma_invalid is counted in its own breakdown bucket (blocked_by_vol_ma_invalid),
    # not here, per plan §5.1.
})


def _compute_filter_diagnostics(
    positions_raw: np.ndarray,
    allow_entry: Optional[np.ndarray],
    filtered_reason: Optional[np.ndarray],
    execution_model: ExecutionModel,
    oos_boundary: Optional[int],
    effective_len: int,
) -> dict:
    """
    Build ``counters`` sub-dict per plan §5.2–§5.4.

    Args:
        positions_raw: Positions from ``generate_positions``, already truncated
            to ``effective_len`` bars (to honour early_exit).
        allow_entry: Per-bar mask used by the engine. ``None`` ↔ mode=none.
            Must have length ``>= effective_len``.
        filtered_reason: Per-bar reason-string array from
            ``compute_filtered_reason``. ``None`` allowed when mode=none.
            Must have length ``>= effective_len``.
        execution_model: OPEN_TO_OPEN / CLOSE_TO_CLOSE.
        oos_boundary: If set (equal_blocks), only attribute events whose
            *decision* bar is >= ``oos_boundary`` (plan §5.3). Otherwise all
            decision bars contribute.
        effective_len: How many bars to consider (``len(positions_truncated)``
            — handles early_exit per §5.4).

    Returns:
        ``counters`` dict with the seven keys from §5.1.
    """
    counters = _empty_counters()
    n = min(effective_len, positions_raw.shape[0])
    if allow_entry is not None and allow_entry.shape[0] < n:
        raise ValueError(
            f"_compute_filter_diagnostics: allow_entry length {allow_entry.shape[0]} "
            f"< effective_len {n}"
        )

    def _decision_in_oos(dec: int) -> bool:
        if oos_boundary is None:
            return True
        return dec >= oos_boundary

    # --- C2C warmup exception: raw_entry_signals gets +1 at bar 0 ----------
    # §5.2: "+ 1 if execution_model == C2C AND positions_raw[0] != 0".
    # For equal_blocks, bar 0 of the segment is in the prepend area, so its
    # decision bar is 0 which is < oos_boundary when oos_boundary > 0 → NOT
    # counted. That matches §5.3 semantics.
    if execution_model == ExecutionModel.CLOSE_TO_CLOSE and n > 0:
        if int(positions_raw[0]) != 0 and _decision_in_oos(0):
            counters["raw_entry_signals"] += 1
            counters["passed_entry_signals"] += 1  # warmup exception — unfiltered

    # --- Main loop (bars k in [1, n)) ---------------------------------------
    for k in range(1, n):
        prev_raw = int(positions_raw[k - 1])
        tgt = int(positions_raw[k])

        if tgt == 0:
            continue  # close-transitions are not open attempts
        if tgt == prev_raw:
            continue  # continuation — not a new open

        # Open or reverse event at k.
        dec = entry_bar_for_decision(k, execution_model)
        if not _decision_in_oos(dec):
            continue

        counters["raw_entry_signals"] += 1

        if allow_entry is None:
            # mode == none: everything passes.
            counters["passed_entry_signals"] += 1
            continue

        if bool(allow_entry[dec]):
            counters["passed_entry_signals"] += 1
            continue

        counters["blocked_entry_signals"] += 1
        # Attribute reason.
        reason = (
            str(filtered_reason[dec]) if filtered_reason is not None else ""
        )
        if reason == FILTER_REASON_BOTH:
            counters["blocked_by_both"] += 1
        elif reason == FILTER_REASON_VOL_MA_INVALID:
            counters["blocked_by_vol_ma_invalid"] += 1
        elif reason in _ATR_REASONS:
            counters["blocked_by_volatility"] += 1
        elif reason in _VOL_REASONS:
            counters["blocked_by_volume"] += 1
        # A blocked event must always have a non-OK reason; if attribution
        # somehow fails the totals still add up (per-reason sum ≤ blocked
        # total). We do not silently drop it.

    return counters


def run_single_backtest(
    open_prices: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    index: pd.Index,
    atr_period: int,
    multiplier: float,
    trade_mode: str,
    commission: float,
    warmup_period: Union[int, None] = None,
    warmup_time: Union[str, None] = None,
    early_exit_enabled: bool = False,
    early_exit_max_drawdown: float = 0.5,
    early_exit_check_bars: int = 0,
    periods_per_year: float = 252.0,
    min_trades_required: int = 3,
    extract_trades_flag: bool = True,
    caller_mode: str = "",
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    auto_warmup: bool = False,
    precomputed_atr: "np.ndarray | None" = None,
    # --- filter support (plan §7.5) ---
    filters_cfg: Optional[dict] = None,
    volume_ma: Optional[np.ndarray] = None,
    global_volume_ma_mean: Optional[float] = None,
    oos_boundary: Optional[int] = None,
) -> BacktestResult:
    """
    Run single backtest with unified interface.
    
    This function is the single entry point for all backtest operations.
    It combines SuperTrend calculation, position generation, returns calculation,
    metrics calculation, and trades extraction.
    
    INVARIANTS (must remain true):
    1. Warmup is applied AFTER backtest (backtest runs on full data)
    2. Trade stats (num_trades, win_rate) are calculated on FULL history
    3. Early-exit truncates arrays at exit_bar
    4. Optimizer and tester call the same run function
    
    DD-01: Trades считаются на полной истории (full history).
    Trades — это события входа/выхода стратегии.
    Warmup — это артефакт стабилизации индикатора.
    Поэтому по умолчанию trades считаются на полной истории.
    
    Args:
        open_prices: Open prices array
        high: High prices array
        low: Low prices array
        close: Close prices array
        index: DataFrame index (datetime or integer)
        atr_period: ATR period
        multiplier: ATR multiplier
        trade_mode: Trading mode ("revers", "long", "short")
        commission: Commission rate per operation
        warmup_period: Warmup period in bars (applied AFTER backtest for metrics only).
                       Mutually exclusive with warmup_time.
        warmup_time: Warmup period in time (e.g., "7d", "48h", "180m").
                     Converted to bars using median timestamp delta.
                     Mutually exclusive with warmup_period.
        early_exit_enabled: Whether to check for early exit (default: False)
        early_exit_max_drawdown: Maximum allowed drawdown for early exit (default: 0.5)
        early_exit_check_bars: Number of bars to check for early exit (default: 0)
        periods_per_year: Periods per year for annualization (default: 252.0)
        min_trades_required: Minimum trades required for valid metrics (default: 3)
        extract_trades_flag: Whether to extract trades table (default: True)
        caller_mode: "optimizer" | "tester" | "" - for warnings
        execution_model: Execution model (OPEN_TO_OPEN or CLOSE_TO_CLOSE, default: OPEN_TO_OPEN)
        auto_warmup: Safety guard — if True, enforce warmup >= atr_period.
                     This does NOT implement Variant A (10 % n, clamp 100..400).
                     Variant A is resolved by the orchestrator before calling this
                     function, via apply_auto_warmup_to_config().
        
    Returns:
        BacktestResult with all data and metrics
        
    Raises:
        ValueError: If input arrays have incompatible lengths, or if both warmup_period 
                    and warmup_time are provided
    """
    # Guard: warn if tester runs with early_exit enabled
    if early_exit_enabled and caller_mode == "tester":
        warnings.warn(
            "Tester is running with early_exit enabled; "
            "this changes historical tester semantics. "
            "Arrays will be truncated at exit_bar.",
            stacklevel=2
        )
    
    # Store original length
    n_bars_original = len(open_prices)
    
    # Validate input arrays
    if not (len(open_prices) == len(high) == len(low) == len(close) == len(index)):
        raise ValueError(
            f"Input arrays must have same length: "
            f"open={len(open_prices)}, high={len(high)}, low={len(low)}, "
            f"close={len(close)}, index={len(index)}"
        )
    
    # Resolve warmup in bars (convert warmup_time to bars if needed)
    warmup_bars = resolve_warmup_bars(
        warmup_period=warmup_period,
        warmup_time=warmup_time,
        index=index if isinstance(index, pd.DatetimeIndex) else pd.DatetimeIndex(index),
        atr_period=atr_period,
        auto_warmup=auto_warmup
    )
    
    # Step 1a: Resolve ATR — use precomputed when available, else compute now.
    # Used both by ``run_backtest_fast`` (via ``precomputed_atr``) and by the
    # filter stage (via ``compute_atr_pct``). Storing it on the result lets
    # downstream consumers (signal_events, Excel export) re-use the same array.
    if precomputed_atr is not None:
        atr_full = np.asarray(precomputed_atr, dtype=np.float64)
    else:
        atr_full = calculate_atr_rma(
            calculate_true_range(high, low, close), atr_period
        )

    # Step 1b: Normalise filters_cfg and compute per-bar masks.
    filters_cfg_norm = filters_cfg if filters_cfg is not None else _default_filters_cfg()
    mode = filters_cfg_norm.get("mode", "none")

    atr_pct_full = compute_atr_pct(atr_full, close)

    amp_needs_volume = mode == "amplitude_and_volume"
    legacy_needs_volume = mode in ("volume", "volatility_and_volume")
    zz_needs_volume = mode == "zigzag_and_volume"
    if amp_needs_volume or legacy_needs_volume or zz_needs_volume:
        if volume_ma is None:
            raise ValueError(
                f"filters.mode == {mode!r} requires a volume_ma array"
            )
        if global_volume_ma_mean is None:
            raise ValueError(
                f"filters.mode == {mode!r} requires global_volume_ma_mean "
                f"(computed once on the full dataset by the CLI layer)"
            )
        volume_ma_arr = np.asarray(volume_ma, dtype=np.float64)
        if volume_ma_arr.shape[0] != len(close):
            raise ValueError(
                "volume_ma array must have the same length as close"
            )
    else:
        volume_ma_arr = None

    vol_cfg = filters_cfg_norm.get("volatility", {}) or {}
    flow_cfg = filters_cfg_norm.get("volume", {}) or {}
    amp_cfg = filters_cfg_norm.get("amplitude", {}) or {}

    thresholds_full = _thresholds_for_diagnostics(
        filters_cfg_norm, global_volume_ma_mean=global_volume_ma_mean
    )

    # Diagnostic arrays written onto BacktestResult for amp-modes (patch §J).
    # None for non-amp modes.
    amp_n_full: Optional[np.ndarray] = None
    amp_threshold_full: Optional[np.ndarray] = None
    atr_amp_full: Optional[np.ndarray] = None  # dedicated amp ATR (patch §F2)
    sep_full: Optional[np.ndarray] = None       # separation array (patch §F5)
    # ZigZag diagnostic payload (v2.0).  None for non-zz modes; otherwise
    # a ZigZagFilterResult from compute_zigzag_filter (§3.4.3).
    zz_result = None

    if mode == "none":
        allow_entry_full = None
        filtered_reason_full = None
    elif mode in ("volatility", "volume", "volatility_and_volume"):
        vol_pass = compute_volatility_pass(
            atr_pct_full,
            vol_cfg.get("min_atr_pct"),
            vol_cfg.get("max_atr_pct"),
        )
        if legacy_needs_volume:
            flow_pass = compute_volume_pass(
                volume_ma_arr,
                float(global_volume_ma_mean),
                flow_cfg.get("min_ratio"),
                flow_cfg.get("max_ratio"),
            )
        else:
            flow_pass = np.ones_like(vol_pass, dtype=bool)

        allow_entry_full = compute_allow_entry(mode, vol_pass, flow_pass)
        filtered_reason_full = compute_filtered_reason(
            mode,
            atr_pct_full,
            volume_ma_arr,
            float(global_volume_ma_mean) if global_volume_ma_mean is not None else None,
            thresholds_full,
        )
    elif mode in ("amplitude", "amplitude_and_volume"):
        # Pre-compute dedicated ATR for the amplitude guardrail (patch §A).
        # Method: Wilder's RMA, period from filters.amplitude.atr_period,
        # independent from strategy atr_period. We reuse the strategy TR to
        # avoid a second TR computation — TR is strategy-independent.
        amp_atr_period = int(amp_cfg.get("atr_period", 14))
        if amp_atr_period == atr_period:
            atr_amp = atr_full
        else:
            tr = calculate_true_range(high, low, close)
            atr_amp = calculate_atr_rma(tr, amp_atr_period)
        atr_amp_full = atr_amp  # stored for diagnostics / signal_events (patch §F2)

        amp_allow, amp_reason, amp_n_full, amp_threshold_full, sep_full = (
            compute_amplitude_filter(
                np.asarray(high, dtype=np.float64),
                np.asarray(low, dtype=np.float64),
                np.asarray(close, dtype=np.float64),
                atr_amp,
                amp_cfg,
            )
        )

        if mode == "amplitude":
            allow_entry_full = amp_allow
            filtered_reason_full = amp_reason
        else:
            # amplitude_and_volume: AND amp with volume; combine reasons with
            # legacy "both" collapse (patch §E.1). Volume-side reason is
            # derived via the volume-only path of compute_filtered_reason.
            flow_pass = compute_volume_pass(
                volume_ma_arr,
                float(global_volume_ma_mean),
                flow_cfg.get("min_ratio"),
                flow_cfg.get("max_ratio"),
            )
            vol_reason = compute_filtered_reason(
                "volume",
                atr_pct_full,
                volume_ma_arr,
                float(global_volume_ma_mean),
                thresholds_full,
            )
            allow_entry_full = amp_allow & flow_pass

            amp_fail = amp_reason != FILTER_REASON_OK
            vol_fail = vol_reason != FILTER_REASON_OK
            combined = np.full(amp_reason.shape, FILTER_REASON_OK, dtype=object)
            only_amp = amp_fail & ~vol_fail
            only_vol = ~amp_fail & vol_fail
            both_fail = amp_fail & vol_fail
            combined[only_amp] = amp_reason[only_amp]
            combined[only_vol] = vol_reason[only_vol]
            combined[both_fail] = FILTER_REASON_BOTH
            filtered_reason_full = combined
    elif mode in ("zigzag", "zigzag_and_volume"):
        # Plan v2.0 §3.4.3.
        # 1. Trend via compute_trend_only (SSOT §3.4.0) — reuses atr_full.
        trend_arr = compute_trend_only(
            atr=atr_full, high=high, low=low, close=close,
            multiplier=multiplier, atr_period=atr_period,
        )
        # 2. Session IDs (§1.7, §3.4.2).  Non-datetime index → single session.
        session_ids = _infer_session_ids(index)
        # 3. ZigZag filter — decision-bar aligned (§1.6).
        zz_cfg = filters_cfg_norm.get("zigzag", {}) or {}
        zz_result = compute_zigzag_filter(
            high=np.asarray(high, dtype=np.float64),
            low=np.asarray(low, dtype=np.float64),
            close=np.asarray(close, dtype=np.float64),
            open_prices=np.asarray(open_prices, dtype=np.float64),
            session_ids=session_ids,
            st_trend=trend_arr,
            cfg=zz_cfg,
        )
        # 4. Combine with volume for zigzag_and_volume.
        if mode == "zigzag":
            allow_entry_full = zz_result.allow_entry
            filtered_reason_full = zz_result.reason
        else:
            flow_pass = compute_volume_pass(
                volume_ma_arr,
                float(global_volume_ma_mean),
                flow_cfg.get("min_ratio"),
                flow_cfg.get("max_ratio"),
            )
            vol_reason = compute_filtered_reason(
                "volume",
                atr_pct_full,
                volume_ma_arr,
                float(global_volume_ma_mean),
                thresholds_full,
            )
            allow_entry_full = zz_result.allow_entry & flow_pass
            filtered_reason_full = _collapse_reasons_zz_volume(
                zz_reason=zz_result.reason,
                vol_reason=vol_reason,
                zz_allow=zz_result.allow_entry,
                flow_pass=flow_pass,
            )
    else:
        raise ValueError(f"run_single_backtest: unknown filter mode {mode!r}")

    # Step 2: Run backtest (may truncate arrays if early_exit=True)
    # F-21b: pass precomputed_atr when available to skip redundant ATR calculation
    returns, equity_curve, trend, positions, early_exit, exit_bar, exit_dd = run_backtest_fast(
        open_prices=open_prices,
        high=high,
        low=low,
        close=close,
        atr_period=atr_period,
        multiplier=multiplier,
        trade_mode=trade_mode,
        commission=commission,
        early_exit_enabled=early_exit_enabled,
        early_exit_max_drawdown=early_exit_max_drawdown,
        early_exit_check_bars=early_exit_check_bars,
        execution_model=execution_model,
        precomputed_atr=atr_full,
        allow_entry=allow_entry_full,
    )
    
    # Step 3: Handle truncation for execution_prices and index (if early_exit=True)
    # run_backtest_fast already truncated returns/equity/positions/trend
    # We need to truncate execution_prices and index to match
    # execution_prices = open or close depending on execution_model
    if execution_model == ExecutionModel.OPEN_TO_OPEN:
        execution_prices_full = open_prices
    elif execution_model == ExecutionModel.CLOSE_TO_CLOSE:
        execution_prices_full = close
    else:
        raise ValueError(f"Unknown ExecutionModel: {execution_model}")
    
    if early_exit and exit_bar is not None:
        # Truncate to match positions length (which is len(returns) + 1).
        # atr_full is also truncated here so that result.atr always has the
        # same length as result.positions / result.trend (Fix M-2).
        execution_prices_for_trades = execution_prices_full[:exit_bar + 1]
        index_for_trades = index[:exit_bar + 1]
        atr_full = atr_full[:exit_bar + 1]
    else:
        execution_prices_for_trades = execution_prices_full
        index_for_trades = index

    # Verify invariant: array lengths after truncation
    assert len(equity_curve) == len(positions) == len(trend), \
        f"Length mismatch: equity={len(equity_curve)}, positions={len(positions)}, trend={len(trend)}"
    assert len(equity_curve) == len(returns) + 1, \
        f"Equity length {len(equity_curve)} != returns length {len(returns)} + 1"
    assert len(execution_prices_for_trades) == len(positions), \
        f"execution_prices_for_trades length {len(execution_prices_for_trades)} != positions length {len(positions)}"
    assert len(index_for_trades) == len(positions), \
        f"index_for_trades length {len(index_for_trades)} != positions length {len(positions)}"
    assert len(atr_full) == len(positions), \
        f"atr length {len(atr_full)} != positions length {len(positions)}"
    
    # Step 3: Calculate metrics (warmup already resolved in warmup_bars)
    # warmup_bars already includes auto_warmup logic (max(warmup_bars, atr_period))
    # metrics["effective_warmup"] may be < warmup_bars if safety-cap was triggered
    metrics = calculate_all_metrics(
        returns=returns,
        equity_curve=equity_curve,
        positions=positions,
        warmup_period=warmup_bars,
        periods_per_year=periods_per_year,
        min_trades_required=min_trades_required
    )
    
    # Step 5: Extract trades (DD-01: from full history)
    trades_df = None
    if extract_trades_flag:
        trades_df = extract_trades(
            positions=positions,
            returns=returns,
            execution_prices=execution_prices_for_trades,
            index=index_for_trades,
            commission_rate=commission,
            trend=trend,
            execution_model=execution_model.value
        )
        
        # Step 5.5: Recalculate trade-based metrics from trades_df.
        # Source of truth: simple entry/exit returns (net_pnl_pct), not compound bar-level returns.
        # These values overwrite the preliminary estimates from calculate_all_metrics (Step 3).
        #
        # SEMANTIC CONTRACT:
        #   win_rate  — PERCENT (0.0–100.0), not fraction (0.0–1.0)
        #   sum_pnl_pct — sum of simple per-trade returns, not compound equity return
        #   profit_factor — np.inf when zero losses (all-winning scenario)
        if trades_df is not None and len(trades_df) > 0:
            num_trades = len(trades_df)
            
            # win_rate: percentage of trades with positive net PnL (0.0–100.0)
            winning_trades = (trades_df['net_pnl_pct'] > 0).sum()
            win_rate = (winning_trades / num_trades * 100.0) if num_trades > 0 else 0.0
            assert 0.0 <= win_rate <= 100.0, f"win_rate must be in [0, 100], got {win_rate}"
            
            # sum_pnl_pct: simple sum of per-trade net returns
            sum_pnl_pct = trades_df['net_pnl_pct'].sum()
            
            avg_trade = sum_pnl_pct / num_trades if num_trades > 0 else 0.0
            
            # profit_factor: gross_profit / abs(gross_loss)
            profits = trades_df.loc[trades_df['net_pnl_pct'] > 0, 'net_pnl_pct'].sum()
            losses = trades_df.loc[trades_df['net_pnl_pct'] < 0, 'net_pnl_pct'].sum()
            
            if losses < 0:  # Has losses
                profit_factor = profits / abs(losses)
            elif profits > 0:  # Only profits, no losses
                profit_factor = np.inf
            else:  # No trades or all breakeven
                profit_factor = INVALID_METRIC_VALUE
            
            # Update metrics dictionary with recalculated values.
            # Ratio metrics (sharpe, sortino, cagr, max_drawdown) are kept from Step 3
            # because they depend on warmup and bar-level returns, not trades_df.
            metrics['num_trades'] = num_trades
            metrics['win_rate'] = win_rate
            metrics['sum_pnl_pct'] = sum_pnl_pct
            metrics['avg_trade'] = avg_trade
            metrics['profit_factor'] = profit_factor
            metrics['net_pnl_pct'] = avg_trade
            
            # Re-apply min_trades_required guard for ratio metrics after trade count update.
            # Step 3 used bar-level num_trades estimate; now we have the exact count from trades_df.
            # max_drawdown is equity-based and not invalidated by trade count.
            if num_trades < min_trades_required:
                metrics['sharpe'] = INVALID_METRIC_VALUE
                metrics['sortino'] = INVALID_METRIC_VALUE
                metrics['cagr'] = INVALID_METRIC_VALUE
        else:
            # trades_df is empty but was requested - set to zero/invalid
            # max_drawdown retains value from calculate_all_metrics (equity-based).
            metrics['num_trades'] = 0
            metrics['win_rate'] = 0.0
            metrics['sum_pnl_pct'] = 0.0
            metrics['avg_trade'] = INVALID_METRIC_VALUE
            metrics['profit_factor'] = INVALID_METRIC_VALUE
            metrics['net_pnl_pct'] = INVALID_METRIC_VALUE
            metrics['sharpe'] = INVALID_METRIC_VALUE
            metrics['sortino'] = INVALID_METRIC_VALUE
            metrics['cagr'] = INVALID_METRIC_VALUE
    
    # Step 6: Build filter_diagnostics (plan §5). Always populated — a
    # ``mode == "none"`` run yields zero-blocked counters, not a ``None``
    # payload.
    #
    # We need ``positions_raw`` (before ``apply_entry_filters``) to count
    # open/reverse attempts. ``run_backtest_fast`` returns post-filter
    # positions only, so we regenerate the raw array here from the same
    # (possibly truncated) ``trend``. Because ``generate_positions`` is
    # deterministic and its output at index k depends only on ``trend[k]``
    # (C2C) or ``trend[k-1]`` (O2O), ``generate_positions(trend_truncated)``
    # is identical to ``generate_positions(trend_full)[:len(trend_truncated)]``.
    positions_raw_full = generate_positions(trend, trade_mode, execution_model)
    assert positions_raw_full.shape[0] == positions.shape[0], (
        f"positions_raw length {positions_raw_full.shape[0]} "
        f"!= positions length {positions.shape[0]}"
    )

    counters = _compute_filter_diagnostics(
        positions_raw=positions_raw_full,
        allow_entry=(
            allow_entry_full[: positions.shape[0]]
            if allow_entry_full is not None else None
        ),
        filtered_reason=(
            filtered_reason_full[: positions.shape[0]]
            if filtered_reason_full is not None else None
        ),
        execution_model=execution_model,
        oos_boundary=oos_boundary,
        effective_len=positions.shape[0],
    )

    filter_diagnostics = {
        "mode": mode,
        "thresholds": thresholds_full,
        "counters": counters,
    }
    # Amplitude-specific diagnostic arrays (v1.3, patch §J). Truncated to
    # match the (possibly early-exited) positions length.
    if amp_n_full is not None:
        filter_diagnostics["amp_n"] = amp_n_full[: positions.shape[0]]
    if amp_threshold_full is not None:
        filter_diagnostics["amp_threshold"] = amp_threshold_full[: positions.shape[0]]
    if atr_amp_full is not None:
        # Dedicated amplitude ATR (Wilder, filters.amplitude.atr_period).
        # Stored so that build_signal_events can use the same array as the
        # engine without falling back to strategy ATR (patch §F2).
        filter_diagnostics["atr_amp"] = atr_amp_full[: positions.shape[0]]
    # separation array (patch §F5): -1 for incomplete/NaN windows.
    # Stored so that signal_events does not need to recompute it.
    if mode in ("amplitude", "amplitude_and_volume"):
        filter_diagnostics["separation"] = sep_full[: positions.shape[0]]

    # ZigZag-specific diagnostic arrays (v2.0, plan §3.4.4).  Truncated to
    # match positions length after possible early_exit.
    if zz_result is not None:
        n = positions.shape[0]
        filter_diagnostics["zz_leg_direction"]            = zz_result.leg_direction[:n]
        filter_diagnostics["zz_cand_height_pct"]          = zz_result.cand_height_pct[:n]
        filter_diagnostics["zz_last_pivot_price"]         = zz_result.last_pivot_price[:n]
        filter_diagnostics["zz_last_pivot_bar_idx"]       = zz_result.last_pivot_bar_idx[:n]
        filter_diagnostics["zz_global_median"]            = zz_result.global_median[:n]
        filter_diagnostics["zz_global_p80"]               = zz_result.global_p80[:n]
        filter_diagnostics["zz_local_median"]             = zz_result.local_median[:n]
        filter_diagnostics["zz_n_legs_before"]            = zz_result.n_legs_before[:n]
        filter_diagnostics["zz_regime_state"]             = zz_result.regime_state[:n]
        filter_diagnostics["zz_n_legs_since_regime_open"] = zz_result.n_legs_since_regime_open[:n]
        filter_diagnostics["zz_armed"]                    = zz_result.armed[:n]
        filter_diagnostics["zz_armed_side"]               = zz_result.armed_side[:n]
        filter_diagnostics["zz_n_bars_since_extreme"]     = zz_result.n_bars_since_extreme[:n]
        filter_diagnostics["zz_n_bars_since_arm"]         = zz_result.n_bars_since_arm[:n]

        # RFC v3.1 §7.1 — new diagnostic keys (Phase 4).  Additive only;
        # existing keys above remain bit-exact (merge-blocker G-03).  These
        # arrays are always produced by compute_zigzag_filter (defaults to
        # zero-length ndarrays when mode != zigzag), so truncation to n is
        # safe.  Downstream consumers may read or ignore them freely.
        filter_diagnostics["zz_ready_a"]                  = zz_result.ready_a[:n]
        filter_diagnostics["zz_ready_b"]                  = zz_result.ready_b[:n]
        filter_diagnostics["zz_readiness_on"]             = zz_result.readiness_on[:n]
        filter_diagnostics["zz_arm_source"]               = zz_result.arm_source[:n]
        filter_diagnostics["zz_cand_leg_id"]              = zz_result.cand_leg_id[:n]
        filter_diagnostics["zz_readiness_block_reason"]   = zz_result.readiness_block_reason[:n]
        filter_diagnostics["zz_disarm_event"]             = zz_result.disarm_event[:n]
        filter_diagnostics["structural_reset_event"]      = zz_result.structural_reset_event[:n]

        # Legs: only those whose confirm_bar fits within positions length.
        filter_diagnostics["zz_legs"] = tuple(
            lg for lg in zz_result.legs if lg.confirm_bar < n
        )
        # D2: DatetimeIndex needed by _write_legs_sheet for timestamp columns.
        # `index` is the pd.Index parameter of run_single_backtest.
        filter_diagnostics["zz_index"] = index
        # D1: execution_model needed by _link_trades_to_legs for correct shift logic.
        filter_diagnostics["execution_model"] = execution_model.value

    # Plan §3.7: allow_entry and filtered_reason are the canonical per-bar
    # decision arrays required by build_signal_events for any active filter
    # mode.  Store them truncated to positions length (same as zz_* arrays).
    # For mode=none both are None and we omit the keys entirely so that
    # downstream .get("allow_entry") returns None as expected.
    if allow_entry_full is not None:
        filter_diagnostics["allow_entry"] = allow_entry_full[: positions.shape[0]]
    if filtered_reason_full is not None:
        filter_diagnostics["filtered_reason"] = filtered_reason_full[: positions.shape[0]]

    # Step 7: Create result
    # effective_warmup may be < warmup_bars if safety-cap was triggered in calculate_all_metrics
    effective_warmup = metrics.pop("effective_warmup", warmup_bars)
    result = BacktestResult(
        atr_period=atr_period,
        multiplier=multiplier,
        trade_mode=trade_mode,
        commission=commission,
        warmup=warmup_bars,
        effective_warmup=effective_warmup,
        returns=returns,
        equity_curve=equity_curve,
        positions=positions,
        trend=trend,
        metrics=metrics,
        early_exit=early_exit,
        exit_bar=exit_bar,
        exit_drawdown=exit_dd,
        trades_df=trades_df,
        n_bars_original=n_bars_original,
        period_label="",
        filter_diagnostics=filter_diagnostics,
        atr=atr_full,
    )

    return result

