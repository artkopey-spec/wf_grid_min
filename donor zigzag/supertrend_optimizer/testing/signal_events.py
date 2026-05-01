"""
Signal events builder for SuperTrend Tester.

Builds a DataFrame of signal events (ST color changes) from a completed
legacy-path backtest result for export to the Signals sheet.
"""

import numpy as np
import pandas as pd
from typing import List, Optional

from supertrend_optimizer.core.calculator import calculate_true_range, calculate_atr_rma
from supertrend_optimizer.core.filters import (
    compute_allow_entry,
    compute_atr_pct,
    compute_filtered_reason,
    compute_volatility_pass,
    compute_volume_pass,
)
from supertrend_optimizer.utils.constants import FILTER_REASON_WHITELIST
from supertrend_optimizer.utils.enums import ExecutionModel


_NA = "N/A"  # sentinel for close-event filter columns


def build_signal_events(
    df: pd.DataFrame,
    trend: np.ndarray,
    atr_period: int,
    trade_mode: str,
    execution_model: ExecutionModel,
    # --- filter support (plan §7.9) ---
    atr: Optional[np.ndarray] = None,
    close: Optional[np.ndarray] = None,
    volume_ma: Optional[np.ndarray] = None,
    global_volume_ma_mean: Optional[float] = None,
    filters_cfg: Optional[dict] = None,
    # --- unified diagnostics dict (plan §3.7, v2.0) ---
    filter_diagnostics: Optional[dict] = None,
    # --- amplitude diagnostics (v1.3, patch §J) — deprecated, prefer filter_diagnostics ---
    amp_n_arr: Optional[np.ndarray] = None,
    amp_threshold_arr: Optional[np.ndarray] = None,
    atr_amp_arr: Optional[np.ndarray] = None,
    sep_arr: Optional[np.ndarray] = None,  # separation from filter_diagnostics (patch §F5)
) -> pd.DataFrame:
    """
    Build a DataFrame of SuperTrend signal events for the Signals sheet.

    v1 contract: This function is designed for the legacy 100% path only.
    It assumes ``trend`` is NOT truncated by early-exit
    (i.e. ``len(trend) == len(df)``).  Equal-blocks and early-exit scenarios
    are out of scope.

    Body % and Range % use ``open[t]`` as denominator (canonical candle
    analysis convention). This is intentional and independent of execution
    model.

    T+1 for open_to_open = return from exec_price (open[t+1]) to close[t+1],
    i.e. the intra-bar move on the execution bar itself, NOT the bar after
    execution.

    Signal bar definition: bar ``t`` where ``trend[t] != trend[t-1]`` and
    ``trend[t-1] in {1, -1}``.  Transitions from 0 → ±1 (ATR stabilisation)
    are skipped.

    Direction for T+k forward returns:
    - open_signal / long_open_signal / short_open_signal:
        direction = side of the position being opened (+1 long, -1 short)
    - close_signal / long_close_signal / short_close_signal:
        direction = side of the position being *closed* (post-exit same-side
        return; positive means price continued in the old direction → early
        exit; negative means the exit was correct).

    Args:
        df: Full OHLC DataFrame (100% period, ``len(df) == len(trend)``).
        trend: Trend array from ``BacktestResult.trend``.  Values: 0, 1, -1.
        atr_period: ATR period used for the backtest.
        trade_mode: ``"long"`` / ``"short"`` / ``"revers"`` / ``"both"``.
        execution_model: ``ExecutionModel.OPEN_TO_OPEN`` or
            ``ExecutionModel.CLOSE_TO_CLOSE``.
        atr: Pre-computed ATR array from ``BacktestResult.atr``.  If ``None``,
            ATR is recomputed locally (backward-compatible path).
        close: Close prices array (required for ``atr_pct`` computation).
            If ``None``, falls back to ``df["close"].values``.
        volume_ma: Volume-MA array (required only when ``filters_cfg["mode"]
            in {"volume", "volatility_and_volume"}``).
        global_volume_ma_mean: Dataset-level baseline scalar used for
            ``volume_ratio = volume_ma / global_volume_ma_mean``. Required
            when ``volume_ma`` is used. Must match the value produced by the
            engine for the same dataset (SSOT — computed in ``cli/tester.py``
            via ``compute_global_volume_ma_mean``).
        filters_cfg: Normalised filters config dict.  ``None`` ↔ mode=none.
            When mode=none all filter columns get ``"N/A"`` (close rows) or
            the computed values (open rows) — specifically ``allow_entry=True``
            and ``filtered_reason="ok"``.
        filter_diagnostics: Optional unified diagnostics dict from the engine
            (plan §3.0 / §3.7).  When provided, all mode-specific per-bar
            arrays (amp_*, zz_*) are read from it; per-field kwargs are
            accepted as deprecated fallbacks (filter_diagnostics wins).
            For mode ∈ {zigzag, zigzag_and_volume}: REQUIRED. Absence raises
            ValueError (plan §3.7).

    Returns:
        DataFrame with 17 + 7 = 24 columns (original 17 + filter columns).
        Rows are ordered by bar index in ``df`` (ascending ``t``).
        For revers/both, the close row precedes its paired open row at the
        same ``Signal Time``.  May be empty (headers present, zero rows) if
        trend never changes.

        New filter columns (schema v1):
        - ``atr_pct``: ATR / close × 100 at signal bar (float or NaN).
          Always in **percent** regardless of filter mode (legacy display contract).
        - ``volume_ratio``: ``volume_ma[t] / global_volume_ma_mean`` at signal bar.
        - ``volatility_pass``: bool / ``"N/A"`` for close rows.
          In amp modes reinterpreted as amplitude-side pass (amp_valid AND amp_ok
          AND not_dead). Patch §J, schema v2.
        - ``volume_pass``: bool / ``"N/A"`` for close rows.
        - ``allow_entry``: bool / ``"N/A"`` for close rows.
        - ``filtered_reason``: reason string / ``"N/A"`` for close rows.
        - ``entry_bar_index``: int — execution bar (O2O: ``t+1``, C2C: ``t``).

        Amplitude diagnostic columns (schema v2, patch §J):
        Present in all modes; ``"N/A"`` in legacy/none modes and on close rows.
        - ``amp_n``: rolling amplitude value at signal bar.
        - ``amp_threshold``: rolling percentile threshold at signal bar.
        - ``separation``: |idx_max - idx_min| within the amp window.
        - ``amp_valid``: bool, separation >= min_separation.
        - ``amp_ok``: bool, amp_n > amp_threshold (strict).
        - ``not_dead``: bool, atr/close > atr_floor.

    Raises:
        ValueError: If ``len(trend) != len(df)``, trend contains values
            outside ``{-1, 0, 1}``, or ``trade_mode`` is not one of
            ``"long"``, ``"short"``, ``"revers"``, ``"both"``.
    """
    _VALID_TRADE_MODES = {"long", "short", "revers", "both"}
    if trade_mode not in _VALID_TRADE_MODES:
        raise ValueError(
            f"trade_mode must be one of {sorted(_VALID_TRADE_MODES)}, got {trade_mode!r}"
        )

    if len(trend) != len(df):
        raise ValueError(
            f"len(trend)={len(trend)} must equal len(df)={len(df)}"
        )

    unexpected = set(np.unique(trend)) - {-1, 0, 1}
    if unexpected:
        raise ValueError(
            f"trend contains unexpected values: {unexpected}"
        )

    # Extract price arrays
    open_arr = df["open"].values.astype(np.float64)
    high_arr = df["high"].values.astype(np.float64)
    low_arr = df["low"].values.astype(np.float64)
    close_arr = df["close"].values.astype(np.float64)
    index = df.index

    n = len(df)

    # Resolve ATR: use pre-computed from engine if provided; else compute locally.
    # Back-compat: callers that don't pass ``atr`` still work correctly.
    if atr is None:
        tr = calculate_true_range(high_arr, low_arr, close_arr)
        atr_arr = calculate_atr_rma(tr, atr_period)
    else:
        atr_arr = np.asarray(atr, dtype=np.float64)

    close_for_pct = (
        np.asarray(close, dtype=np.float64) if close is not None else close_arr
    )

    # ── filter_diagnostics (plan §3.7): extract per-field arrays when dict is provided.
    # Per-field kwargs are overridden by filter_diagnostics (BC: existing callers that
    # pass kwargs directly keep working; new callers pass filter_diagnostics).
    _fd = filter_diagnostics or {}
    if _fd:
        # Amplitude arrays: filter_diagnostics has priority over per-field kwargs.
        if _fd.get("amp_n") is not None:
            amp_n_arr = _fd["amp_n"]
        if _fd.get("amp_threshold") is not None:
            amp_threshold_arr = _fd["amp_threshold"]
        if _fd.get("atr_amp") is not None:
            atr_amp_arr = _fd["atr_amp"]
        if _fd.get("separation") is not None:
            sep_arr = _fd["separation"]

    # Compute per-bar filter arrays (vectorised) — used inside the row loop.
    filters_cfg_norm = filters_cfg if filters_cfg is not None else {
        "mode": "none",
        "volatility": {"min_atr_pct": None, "max_atr_pct": None},
        "volume": {
            "volume_column": "Volume",
            "volume_ma_column": "Volume MA",
            "min_ratio": None,
            "max_ratio": None,
        },
    }
    filter_mode = filters_cfg_norm.get("mode", "none")
    vol_cfg = filters_cfg_norm.get("volatility", {}) or {}
    flow_cfg = filters_cfg_norm.get("volume", {}) or {}

    # ZigZag modes require filter_diagnostics (plan §3.7).
    _is_zz_mode = filter_mode in ("zigzag", "zigzag_and_volume")
    if _is_zz_mode and not _fd:
        raise ValueError(
            f"build_signal_events: mode={filter_mode!r} requires filter_diagnostics "
            f"with zz_* keys (see plan §3.0). Per-field kwargs are NOT "
            f"accepted for zz modes."
        )

    atr_pct_arr = compute_atr_pct(atr_arr, close_for_pct)

    if filter_mode in ("volume", "volatility_and_volume", "amplitude_and_volume",
                       "zigzag_and_volume"):
        if volume_ma is None:
            raise ValueError(
                f"build_signal_events: filter mode {filter_mode!r} requires "
                f"a volume_ma array"
            )
        if global_volume_ma_mean is None:
            raise ValueError(
                f"build_signal_events: filter mode {filter_mode!r} requires "
                f"global_volume_ma_mean (dataset-level baseline)"
            )
        vol_ma_arr = np.asarray(volume_ma, dtype=np.float64)
        from supertrend_optimizer.core.filters import compute_volume_ratio
        vol_ratio_arr = compute_volume_ratio(vol_ma_arr, float(global_volume_ma_mean))
    else:
        vol_ma_arr = None
        vol_ratio_arr = np.full(n, float("nan"), dtype=np.float64)

    if filter_mode == "none":
        # No filtering: every open event passes, reasons are all "ok".
        v_pass_arr = np.ones(n, dtype=bool)
        f_pass_arr = np.ones(n, dtype=bool)
        allow_arr = np.ones(n, dtype=bool)
        reason_arr = np.full(n, "ok", dtype=object)
    elif filter_mode in ("amplitude", "amplitude_and_volume"):
        # Amplitude modes: allow_arr / reason_arr come from the pre-computed
        # amp arrays supplied by the engine. Derive them here so that
        # allow_entry and filtered_reason columns are correct in Signals.
        from supertrend_optimizer.core.amplitude_filter import compute_amplitude_filter
        amp_cfg_local = filters_cfg_norm.get("amplitude") or {}
        # atr_amp for the guardrail: use atr_amp_arr if supplied by caller
        # (preferred — comes from filter_diagnostics["atr_amp"]).
        # Otherwise recompute with the correct amplitude ATR period so that
        # the result matches the engine exactly, even when the amplitude
        # atr_period differs from the strategy atr_period (patch §F2).
        if atr_amp_arr is not None:
            _atr_for_amp = np.asarray(atr_amp_arr, dtype=np.float64)
        else:
            _amp_atr_period = int(amp_cfg_local.get("atr_period", 14))
            if _amp_atr_period == atr_period:
                _atr_for_amp = atr_arr
            else:
                _tr_local = calculate_true_range(
                    np.asarray(df["high"].values, dtype=np.float64),
                    np.asarray(df["low"].values, dtype=np.float64),
                    close_for_pct,
                )
                _atr_for_amp = calculate_atr_rma(_tr_local, _amp_atr_period)
        amp_allow, amp_reason, _, _, _ = compute_amplitude_filter(
            np.asarray(df["high"].values, dtype=np.float64),
            np.asarray(df["low"].values, dtype=np.float64),
            close_for_pct,
            _atr_for_amp,
            amp_cfg_local,
        )
        if filter_mode == "amplitude":
            v_pass_arr = amp_allow
            f_pass_arr = np.ones(n, dtype=bool)
            allow_arr = amp_allow
            reason_arr = amp_reason
        else:
            # amplitude_and_volume: combine with volume side.
            f_pass_arr = compute_volume_pass(
                vol_ma_arr,
                float(global_volume_ma_mean),
                flow_cfg.get("min_ratio"),
                flow_cfg.get("max_ratio"),
            )
            vol_reason = compute_filtered_reason(
                "volume",
                atr_pct_arr,
                vol_ma_arr,
                float(global_volume_ma_mean) if global_volume_ma_mean is not None else None,
                {
                    "min_atr_pct": None, "max_atr_pct": None,
                    "min_ratio": flow_cfg.get("min_ratio"),
                    "max_ratio": flow_cfg.get("max_ratio"),
                },
            )
            from supertrend_optimizer.utils.constants import FILTER_REASON_OK, FILTER_REASON_BOTH
            allow_arr = amp_allow & f_pass_arr
            amp_fail = amp_reason != FILTER_REASON_OK
            vol_fail = vol_reason != FILTER_REASON_OK
            combined = np.full(n, FILTER_REASON_OK, dtype=object)
            combined[amp_fail & ~vol_fail] = amp_reason[amp_fail & ~vol_fail]
            combined[~amp_fail & vol_fail] = vol_reason[~amp_fail & vol_fail]
            combined[amp_fail & vol_fail] = FILTER_REASON_BOTH
            v_pass_arr = amp_allow
            reason_arr = combined
    elif _is_zz_mode:
        # ZigZag / zigzag_and_volume: allow_entry and filtered_reason come
        # directly from filter_diagnostics (the engine is the SSOT).
        # plan §3.7: these are already decision-bar aligned.
        _zz_allow = _fd.get("allow_entry")
        _zz_reason = _fd.get("filtered_reason")
        if _zz_allow is None or _zz_reason is None:
            raise ValueError(
                "build_signal_events: filter_diagnostics is missing 'allow_entry' "
                "or 'filtered_reason' for zz mode. Ensure run_single_backtest ran "
                "with the same filters_cfg."
            )
        allow_arr = np.asarray(_zz_allow, dtype=bool)
        reason_arr = np.asarray(_zz_reason, dtype=object)
        # v_pass_arr = zz-side pass (allow_entry itself in zz-pure mode).
        v_pass_arr = allow_arr.copy()
        if filter_mode == "zigzag_and_volume":
            f_pass_arr = compute_volume_pass(
                vol_ma_arr,
                float(global_volume_ma_mean),
                flow_cfg.get("min_ratio"),
                flow_cfg.get("max_ratio"),
            )
        else:
            f_pass_arr = np.ones(n, dtype=bool)
    else:
        v_pass_arr = compute_volatility_pass(
            atr_pct_arr,
            vol_cfg.get("min_atr_pct"),
            vol_cfg.get("max_atr_pct"),
        )
        if filter_mode in ("volume", "volatility_and_volume"):
            f_pass_arr = compute_volume_pass(
                vol_ma_arr,
                float(global_volume_ma_mean),
                flow_cfg.get("min_ratio"),
                flow_cfg.get("max_ratio"),
            )
        else:
            f_pass_arr = np.ones(n, dtype=bool)
        allow_arr = compute_allow_entry(filter_mode, v_pass_arr, f_pass_arr)
        thresholds = {
            "min_atr_pct": vol_cfg.get("min_atr_pct"),
            "max_atr_pct": vol_cfg.get("max_atr_pct"),
            "min_ratio": flow_cfg.get("min_ratio"),
            "max_ratio": flow_cfg.get("max_ratio"),
        }
        reason_arr = compute_filtered_reason(
            filter_mode,
            atr_pct_arr,
            vol_ma_arr,
            float(global_volume_ma_mean) if global_volume_ma_mean is not None else None,
            thresholds,
        )

    # ── ZigZag diagnostic arrays (v2.0, plan §3.7) ─────────────────────────
    # Read the 9 Priority-1 per-bar arrays from filter_diagnostics.
    # In non-zz modes: all zz columns become _NA.
    if _is_zz_mode:
        def _zz_arr(key):
            v = _fd.get(key)
            return np.asarray(v, dtype=np.float64 if key in (
                "zz_cand_height_pct", "zz_global_median", "zz_global_p80",
                "zz_local_median",
            ) else (np.int64 if key == "zz_n_legs_before" else np.int8)
            ) if v is not None else None

        _zz_leg_dir    = _zz_arr("zz_leg_direction")
        _zz_cand_h_pct = _zz_arr("zz_cand_height_pct")
        _zz_glob_med   = _zz_arr("zz_global_median")
        _zz_glob_p80   = _zz_arr("zz_global_p80")
        _zz_loc_med    = _zz_arr("zz_local_median")
        _zz_n_legs     = _zz_arr("zz_n_legs_before")
        _zz_regime     = _zz_arr("zz_regime_state")
        _zz_armed      = _fd.get("zz_armed")
        if _zz_armed is not None:
            # Per filter_diagnostics contract (§3.0): zz_armed is bool.
            # Keep dtype=bool explicitly; do NOT coerce to int8.
            _zz_armed = np.asarray(_zz_armed, dtype=bool)
        _zz_armed_side = _zz_arr("zz_armed_side")
        # Phase 5 (RFC v3.1 §7.4) — readiness + arm_source per-bar arrays.
        # Added by Phase 4 diagnostics plumbing in run.py (§7.1).
        _zz_ready_a = _fd.get("zz_ready_a")
        if _zz_ready_a is not None:
            _zz_ready_a = np.asarray(_zz_ready_a, dtype=bool)
        _zz_ready_b = _fd.get("zz_ready_b")
        if _zz_ready_b is not None:
            _zz_ready_b = np.asarray(_zz_ready_b, dtype=bool)
        _zz_arm_source = _fd.get("zz_arm_source")
        if _zz_arm_source is not None:
            _zz_arm_source = np.asarray(_zz_arm_source, dtype=np.int8)
    else:
        (_zz_leg_dir, _zz_cand_h_pct, _zz_glob_med, _zz_glob_p80,
         _zz_loc_med, _zz_n_legs, _zz_regime, _zz_armed,
         _zz_armed_side, _zz_ready_a, _zz_ready_b,
         _zz_arm_source) = (None,) * 12

    # ── Amplitude diagnostic arrays (v1.3, patch §J) ──────────────────────
    # amp_n_arr / amp_threshold_arr come from filter_diagnostics["amp_n"] /
    # filter_diagnostics["amp_threshold"] computed by run_single_backtest.
    # atr_amp_arr: the dedicated amplitude ATR (Wilder, atr_period from
    # filters.amplitude.atr_period), also from the engine's pre-computation.
    # When not in amp mode, all six derived arrays default to NaN / False.
    _is_amp_mode = filter_mode in ("amplitude", "amplitude_and_volume")

    if _is_amp_mode and amp_n_arr is not None and amp_threshold_arr is not None:
        _amp_n = np.asarray(amp_n_arr, dtype=np.float64)
        _amp_thr = np.asarray(amp_threshold_arr, dtype=np.float64)

        # Derive amp_ok: strict amp_n > amp_threshold (mirrors core logic).
        with np.errstate(invalid="ignore"):
            _amp_ok = (
                np.isfinite(_amp_n)
                & np.isfinite(_amp_thr)
                & (_amp_n > _amp_thr)
            )

        # Derive not_dead from atr_amp_arr (fraction, not percent — patch §F7).
        amp_cfg = (filters_cfg_norm.get("amplitude") or {})
        _atr_floor = float(amp_cfg.get("atr_floor", 0.0))
        if atr_amp_arr is not None:
            _atr_amp = np.asarray(atr_amp_arr, dtype=np.float64)
            with np.errstate(divide="ignore", invalid="ignore"):
                _atr_frac = np.where(
                    np.isfinite(_atr_amp) & np.isfinite(close_for_pct) & (close_for_pct != 0.0),
                    _atr_amp / close_for_pct,
                    np.nan,
                )
            _not_dead = np.isfinite(_atr_frac) & (_atr_frac > _atr_floor)
        else:
            _not_dead = np.zeros(n, dtype=bool)

        # amp_valid: separation >= min_separation.
        # Prefer sep_arr from filter_diagnostics (engine SSOT, patch §F5).
        # Fall back to recompute only when sep_arr was not supplied.
        _amp_n_param = int(amp_cfg.get("n", 20))
        _min_sep_cfg = amp_cfg.get("min_separation")
        _min_sep = int(_min_sep_cfg) if _min_sep_cfg is not None else max(1, _amp_n_param // 3)

        if sep_arr is not None:
            _sep_full = np.asarray(sep_arr, dtype=np.int64)
        elif n >= _amp_n_param:
            from numpy.lib.stride_tricks import sliding_window_view
            _hw = sliding_window_view(high_arr, window_shape=_amp_n_param)
            _lw = sliding_window_view(low_arr, window_shape=_amp_n_param)
            _idx_max = np.argmax(_hw, axis=1)
            _idx_min = np.argmin(_lw, axis=1)
            _sep_vals = np.abs(_idx_max - _idx_min).astype(np.int64)
            _sep_full = np.full(n, -1, dtype=np.int64)
            _sep_full[_amp_n_param - 1:] = _sep_vals
        else:
            _sep_full = np.full(n, -1, dtype=np.int64)
        _amp_valid = _sep_full >= _min_sep
    else:
        _amp_n = np.full(n, np.nan)
        _amp_thr = np.full(n, np.nan)
        _sep_full = np.full(n, -1, dtype=np.int64)
        _amp_valid = np.zeros(n, dtype=bool)
        _amp_ok = np.zeros(n, dtype=bool)
        _not_dead = np.zeros(n, dtype=bool)

    # Normalise trade_mode: "both" is semantically identical to "revers"
    effective_mode = "revers" if trade_mode == "both" else trade_mode

    is_o2o = (execution_model == ExecutionModel.OPEN_TO_OPEN)

    rows: List[dict] = []

    for t in range(1, n):
        prev = int(trend[t - 1])
        curr = int(trend[t])

        # Skip: no change, or transition from neutral (0 → ±1)
        if curr == prev or prev == 0:
            continue

        # ── Signal bar characteristics ──
        signal_time = index[t]
        signal_bar_index = t

        o_t = open_arr[t]
        h_t = high_arr[t]
        l_t = low_arr[t]
        c_t = close_arr[t]
        atr_prev = atr_arr[t - 1]

        body = abs(c_t - o_t)
        rng = h_t - l_t

        body_pct = body / o_t * 100.0 if o_t != 0 else float("nan")
        range_pct = rng / o_t * 100.0 if o_t != 0 else float("nan")
        body_atr = body / atr_prev if atr_prev != 0 else float("nan")
        range_atr = rng / atr_prev if atr_prev != 0 else float("nan")

        st_before = "GREEN" if prev == 1 else "RED"
        st_after = "GREEN" if curr == 1 else "RED"

        # ── Execution and entry bar ──
        if is_o2o:
            exec_bar = t + 1
        else:
            exec_bar = t

        # entry_bar_index is the execution bar (plan §7.9 table).
        entry_bar_idx = exec_bar

        # Filter values at the decision bar (= signal bar t, plan §4.2).
        sig_atr_pct = float(atr_pct_arr[t])
        sig_vol_ratio = float(vol_ratio_arr[t])
        sig_f_pass = bool(f_pass_arr[t])
        sig_allow = bool(allow_arr[t])
        sig_reason = str(reason_arr[t])

        # volatility_pass: in amp modes = amplitude-side pass (patch §J).
        if _is_amp_mode:
            sig_v_pass = bool(_amp_valid[t] and _amp_ok[t] and _not_dead[t])
        else:
            sig_v_pass = bool(v_pass_arr[t])

        # Amplitude diagnostic values at signal bar t.
        sig_amp_n = float(_amp_n[t]) if np.isfinite(_amp_n[t]) else float("nan")
        sig_amp_thr = float(_amp_thr[t]) if np.isfinite(_amp_thr[t]) else float("nan")
        sig_sep = int(_sep_full[t]) if _sep_full[t] >= 0 else _NA
        sig_amp_valid = bool(_amp_valid[t]) if _is_amp_mode else _NA
        sig_amp_ok = bool(_amp_ok[t]) if _is_amp_mode else _NA
        sig_not_dead = bool(_not_dead[t]) if _is_amp_mode else _NA
        # For non-amp modes, amp diagnostic floats also become _NA.
        if not _is_amp_mode:
            sig_amp_n = _NA
            sig_amp_thr = _NA
            sig_sep = _NA

        # ZigZag diagnostic values at signal bar t (Priority-1, plan §5.1).
        def _zz_val(arr, *, as_float=False, as_int=False, as_bool=False):
            if arr is None:
                return _NA
            v = arr[t]
            if as_float:
                return float(v) if np.isfinite(float(v)) else float("nan")
            if as_bool:
                return bool(v)
            if as_int:
                return int(v)
            return int(v)

        if _is_zz_mode:
            sig_zz_leg_dir     = _zz_val(_zz_leg_dir, as_int=True)
            sig_zz_cand_h_pct  = _zz_val(_zz_cand_h_pct, as_float=True)
            sig_zz_glob_med    = _zz_val(_zz_glob_med, as_float=True)
            sig_zz_glob_p80    = _zz_val(_zz_glob_p80, as_float=True)
            sig_zz_loc_med     = _zz_val(_zz_loc_med, as_float=True)
            sig_zz_n_legs      = _zz_val(_zz_n_legs, as_int=True)
            sig_zz_regime      = _zz_val(_zz_regime, as_int=True)
            sig_zz_armed       = _zz_val(_zz_armed, as_bool=True)
            sig_zz_armed_side  = _zz_val(_zz_armed_side, as_int=True)
            # Phase 5 (RFC v3.1 §7.4).
            sig_zz_ready_a     = _zz_val(_zz_ready_a, as_bool=True)
            sig_zz_ready_b     = _zz_val(_zz_ready_b, as_bool=True)
            sig_zz_arm_source  = _zz_val(_zz_arm_source, as_int=True)
        else:
            (sig_zz_leg_dir, sig_zz_cand_h_pct, sig_zz_glob_med, sig_zz_glob_p80,
             sig_zz_loc_med, sig_zz_n_legs, sig_zz_regime, sig_zz_armed,
             sig_zz_armed_side, sig_zz_ready_a, sig_zz_ready_b,
             sig_zz_arm_source) = (_NA,) * 12

        if exec_bar < n:
            exec_price = open_arr[exec_bar] if is_o2o else close_arr[exec_bar]
        else:
            exec_price = float("nan")

        # ── Forward returns T+1, T+2, T+3 ──
        def forward_return(k: int, direction: int) -> float:
            target = exec_bar + k
            if np.isnan(exec_price) or target >= n:
                return float("nan")
            return direction * (close_arr[target] - exec_price) / exec_price * 100.0

        # ── Build rows per trade_mode ──
        # prev == 1  (green→red):  long close / short open
        # prev == -1 (red→green):  long open  / short close

        # Filter kwargs for open rows (plan §7.9 + patch §J schema v2).
        # Close rows keep all filter columns as _NA.
        # Both open and close share ``entry_bar_index``.
        open_filter_kw = dict(
            atr_pct=sig_atr_pct,
            volume_ratio=sig_vol_ratio,
            volatility_pass=sig_v_pass,
            volume_pass=sig_f_pass,
            allow_entry=sig_allow,
            filtered_reason=sig_reason,
            entry_bar_index=entry_bar_idx,
            # amplitude diagnostic columns (schema v2)
            amp_n=sig_amp_n,
            amp_threshold=sig_amp_thr,
            separation=sig_sep,
            amp_valid=sig_amp_valid,
            amp_ok=sig_amp_ok,
            not_dead=sig_not_dead,
            # zigzag diagnostic columns (v2.0, plan §5.1)
            zz_leg_direction=sig_zz_leg_dir,
            zz_cand_height_pct=sig_zz_cand_h_pct,
            zz_global_median=sig_zz_glob_med,
            zz_global_p80=sig_zz_glob_p80,
            zz_local_median=sig_zz_loc_med,
            zz_n_legs=sig_zz_n_legs,
            zz_regime_state=sig_zz_regime,
            zz_armed=sig_zz_armed,
            zz_armed_side=sig_zz_armed_side,
            # Phase 5 (§7.4)
            zz_ready_a=sig_zz_ready_a,
            zz_ready_b=sig_zz_ready_b,
            zz_arm_source=sig_zz_arm_source,
        )
        close_filter_kw = dict(
            atr_pct=_NA,
            volume_ratio=_NA,
            volatility_pass=_NA,
            volume_pass=_NA,
            allow_entry=_NA,
            filtered_reason=_NA,
            entry_bar_index=entry_bar_idx,
            # amplitude diagnostic columns (schema v2) — _NA on close rows
            amp_n=_NA,
            amp_threshold=_NA,
            separation=_NA,
            amp_valid=_NA,
            amp_ok=_NA,
            not_dead=_NA,
            # zigzag diagnostic columns — _NA on close rows (plan §3.7)
            zz_leg_direction=_NA,
            zz_cand_height_pct=_NA,
            zz_global_median=_NA,
            zz_global_p80=_NA,
            zz_local_median=_NA,
            zz_n_legs=_NA,
            zz_regime_state=_NA,
            zz_armed=_NA,
            zz_armed_side=_NA,
            # Phase 5 (§7.4) — _NA on close rows.
            zz_ready_a=_NA,
            zz_ready_b=_NA,
            zz_arm_source=_NA,
        )

        if effective_mode == "long":
            if prev == 1:
                # green→red: long CLOSE
                event_type = "close_signal"
                direction_str = "LONG"
                direction_val = +1  # post-exit same-side
                filter_kw = close_filter_kw
            else:
                # red→green: long OPEN
                event_type = "open_signal"
                direction_str = "LONG"
                direction_val = +1
                filter_kw = open_filter_kw

            rows.append(_make_row(
                signal_time, signal_bar_index, event_type, direction_str,
                st_before, st_after, is_reversal=False,
                exec_price=exec_price,
                signal_open_price=o_t,
                signal_close_price=c_t,
                body_pct=body_pct, range_pct=range_pct,
                body_atr=body_atr, range_atr=range_atr,
                t1=forward_return(1, direction_val),
                t2=forward_return(2, direction_val),
                t3=forward_return(3, direction_val),
                **filter_kw,
            ))

        elif effective_mode == "short":
            if prev == -1:
                # red→green: short CLOSE
                event_type = "close_signal"
                direction_str = "SHORT"
                direction_val = -1  # post-exit same-side
                filter_kw = close_filter_kw
            else:
                # green→red: short OPEN
                event_type = "open_signal"
                direction_str = "SHORT"
                direction_val = -1
                filter_kw = open_filter_kw

            rows.append(_make_row(
                signal_time, signal_bar_index, event_type, direction_str,
                st_before, st_after, is_reversal=False,
                exec_price=exec_price,
                signal_open_price=o_t,
                signal_close_price=c_t,
                body_pct=body_pct, range_pct=range_pct,
                body_atr=body_atr, range_atr=range_atr,
                t1=forward_return(1, direction_val),
                t2=forward_return(2, direction_val),
                t3=forward_return(3, direction_val),
                **filter_kw,
            ))

        else:
            # revers / both: 2 rows — close first, then open
            if prev == 1:
                # green→red: long close + short open
                close_event = "long_close_signal"
                close_dir_str = "LONG"
                close_dir_val = +1  # post-exit same-side
                open_event = "short_open_signal"
                open_dir_str = "SHORT"
                open_dir_val = -1
            else:
                # red→green: short close + long open
                close_event = "short_close_signal"
                close_dir_str = "SHORT"
                close_dir_val = -1  # post-exit same-side
                open_event = "long_open_signal"
                open_dir_str = "LONG"
                open_dir_val = +1

            # close row first
            rows.append(_make_row(
                signal_time, signal_bar_index, close_event, close_dir_str,
                st_before, st_after, is_reversal=True,
                exec_price=exec_price,
                signal_open_price=o_t,
                signal_close_price=c_t,
                body_pct=body_pct, range_pct=range_pct,
                body_atr=body_atr, range_atr=range_atr,
                t1=forward_return(1, close_dir_val),
                t2=forward_return(2, close_dir_val),
                t3=forward_return(3, close_dir_val),
                **close_filter_kw,
            ))
            # open row second
            rows.append(_make_row(
                signal_time, signal_bar_index, open_event, open_dir_str,
                st_before, st_after, is_reversal=True,
                exec_price=exec_price,
                signal_open_price=o_t,
                signal_close_price=c_t,
                body_pct=body_pct, range_pct=range_pct,
                body_atr=body_atr, range_atr=range_atr,
                t1=forward_return(1, open_dir_val),
                t2=forward_return(2, open_dir_val),
                t3=forward_return(3, open_dir_val),
                **open_filter_kw,
            ))

    if rows:
        result_df = pd.DataFrame(rows)
    else:
        result_df = pd.DataFrame(columns=list(_COLUMN_NAMES))

    # Append median-normalized ratio columns at the end (vectorised, post-build).
    # median() ignores NaN by default; guarded against median=0 and median=NaN.
    for src_key, ratio_key in (
        ("signal_body_pct",  "signal_body_pct_median_ratio"),
        ("signal_range_pct", "signal_range_pct_median_ratio"),
    ):
        if len(result_df) == 0:
            result_df[ratio_key] = pd.Series(dtype="float64")
            continue
        median_val = result_df[src_key].median()
        if pd.isna(median_val) or median_val == 0.0:
            result_df[ratio_key] = float("nan")
        else:
            result_df[ratio_key] = result_df[src_key] / median_val

    return result_df


# Internal column names (snake_case) — mapped to display names in excel_tester.
# Schema v2 (patch §J): 6 amplitude diagnostic columns added at the end.
# In legacy / none modes these columns are always _NA.
_COLUMN_NAMES = (
    "signal_time",
    "signal_bar_index",
    "event_type",
    "direction",
    "st_color_before",
    "st_color_after",
    "is_reversal",
    "exec_price",
    "signal_open_price",
    "signal_close_price",
    "signal_body_pct",
    "signal_range_pct",
    "signal_body_atr",
    "signal_range_atr",
    "t1_return_pct",
    "t2_return_pct",
    "t3_return_pct",
    # filter columns (plan §7.9)
    "atr_pct",
    "volume_ratio",
    "volatility_pass",
    "volume_pass",
    "allow_entry",
    "filtered_reason",
    "entry_bar_index",
    # amplitude diagnostic columns (v1.3, patch §J, schema v2)
    "amp_n",
    "amp_threshold",
    "separation",
    "amp_valid",
    "amp_ok",
    "not_dead",
    # zigzag diagnostic columns (v2.0, plan §5.1) — _NA in non-zz modes
    "zz_leg_direction",
    "zz_cand_height_pct",
    "zz_global_median",
    "zz_global_p80",
    "zz_local_median",
    "zz_n_legs",
    "zz_regime_state",
    "zz_armed",
    "zz_armed_side",
    # Phase 5 (RFC v3.1 §7.4 / fix N-10) — readiness + arm-source columns.
    "zz_ready_a",
    "zz_ready_b",
    "zz_arm_source",
)


def _make_row(
    signal_time,
    signal_bar_index: int,
    event_type: str,
    direction: str,
    st_before: str,
    st_after: str,
    is_reversal: bool,
    exec_price: float,
    signal_open_price: float,
    signal_close_price: float,
    body_pct: float,
    range_pct: float,
    body_atr: float,
    range_atr: float,
    t1: float,
    t2: float,
    t3: float,
    # --- filter columns (plan §7.9) ---
    atr_pct=_NA,
    volume_ratio=_NA,
    volatility_pass=_NA,
    volume_pass=_NA,
    allow_entry=_NA,
    filtered_reason=_NA,
    entry_bar_index=None,
    # --- amplitude diagnostic columns (v1.3, patch §J, schema v2) ---
    amp_n=_NA,
    amp_threshold=_NA,
    separation=_NA,
    amp_valid=_NA,
    amp_ok=_NA,
    not_dead=_NA,
    # --- zigzag diagnostic columns (v2.0, plan §5.1) ---
    zz_leg_direction=_NA,
    zz_cand_height_pct=_NA,
    zz_global_median=_NA,
    zz_global_p80=_NA,
    zz_local_median=_NA,
    zz_n_legs=_NA,
    zz_regime_state=_NA,
    zz_armed=_NA,
    zz_armed_side=_NA,
    # Phase 5 (RFC v3.1 §7.4)
    zz_ready_a=_NA,
    zz_ready_b=_NA,
    zz_arm_source=_NA,
) -> dict:
    return {
        "signal_time": signal_time,
        "signal_bar_index": signal_bar_index,
        "event_type": event_type,
        "direction": direction,
        "st_color_before": st_before,
        "st_color_after": st_after,
        "is_reversal": is_reversal,
        "exec_price": exec_price,
        "signal_open_price": signal_open_price,
        "signal_close_price": signal_close_price,
        "signal_body_pct": body_pct,
        "signal_range_pct": range_pct,
        "signal_body_atr": body_atr,
        "signal_range_atr": range_atr,
        "t1_return_pct": t1,
        "t2_return_pct": t2,
        "t3_return_pct": t3,
        # filter columns
        "atr_pct": atr_pct,
        "volume_ratio": volume_ratio,
        "volatility_pass": volatility_pass,
        "volume_pass": volume_pass,
        "allow_entry": allow_entry,
        "filtered_reason": filtered_reason,
        "entry_bar_index": entry_bar_index,
        # amplitude diagnostic columns (schema v2)
        "amp_n": amp_n,
        "amp_threshold": amp_threshold,
        "separation": separation,
        "amp_valid": amp_valid,
        "amp_ok": amp_ok,
        "not_dead": not_dead,
        # zigzag diagnostic columns (v2.0, plan §5.1)
        "zz_leg_direction": zz_leg_direction,
        "zz_cand_height_pct": zz_cand_height_pct,
        "zz_global_median": zz_global_median,
        "zz_global_p80": zz_global_p80,
        "zz_local_median": zz_local_median,
        "zz_n_legs": zz_n_legs,
        "zz_regime_state": zz_regime_state,
        "zz_armed": zz_armed,
        "zz_armed_side": zz_armed_side,
        # Phase 5 (RFC v3.1 §7.4) — readiness + arm_source.
        "zz_ready_a": zz_ready_a,
        "zz_ready_b": zz_ready_b,
        "zz_arm_source": zz_arm_source,
    }
