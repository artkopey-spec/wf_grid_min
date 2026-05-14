"""
Signal events builder for SuperTrend Tester.

Builds a DataFrame of signal events (ST color changes) from a completed
legacy-path backtest result for export to the Signals sheet.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional

from supertrend_optimizer.core.calculator import calculate_true_range, calculate_atr_rma
from supertrend_optimizer.utils.enums import ExecutionModel

# ---------------------------------------------------------------------------
# WP-T6 constants (plan §8.2)
# ---------------------------------------------------------------------------

_NA_FILTER = "N/A"  # sentinel for close-rows and disabled path (plan §9.1.1)

# Four filter columns added to Signals when filter_diagnostics is not None.
# Position: after "is_reversal", before "exec_price" (plan §9.5).
_FILTER_COLUMN_NAMES = (
    "filter_state_at_signal",
    "filter_decision",
    "filter_block_reason",
    "filter_trigger_source",
)

# Maps filter_block_reason string (spec §13) → filter_decision string (plan §8.2).
# Used only when filter_allowed_entry[t] == 0 (entry was blocked).
_BLOCK_REASON_TO_DECISION: Dict[str, str] = {
    "none":                         "entry_allowed",          # guarded: should be entry_allowed
    "filter_off":                   "entry_blocked_filter_off",
    "waiting_for_allowed_st_flip":  "entry_blocked_waiting_first",
    "trade_mode_disallowed_flip":   "entry_blocked_trade_mode",
    "local_median_unavailable":     "entry_blocked_local_median",
    "invalid_stats":                "entry_blocked_invalid_stats",
    "insufficient_global_stats":    "entry_blocked_invalid_stats",
    "stopping_mode_no_new_entries": "entry_blocked_stopping",
    # docs/time_filter_plan_v1_final.txt §6.3
    "time_filter_reset":            "entry_blocked_time_filter_reset",
    "time_filter_out_of_window":    "entry_blocked_time_filter_out_of_window",
    "volume_direction_warmup":      "entry_blocked_volume_direction_warmup",
    "volume_unknown_direction":     "entry_blocked_volume_unknown_direction",
    "volume_trade_mode_disallowed_direction": (
        "entry_blocked_volume_trade_mode_disallowed_direction"
    ),
    "volume_cycle_direction_mismatch": (
        "entry_blocked_volume_cycle_direction_mismatch"
    ),
    "volume_warmup":                "entry_blocked_volume_warmup",
    "volume_baseline_zero":         "entry_blocked_volume_baseline_zero",
    "volume_below_baseline":        "entry_blocked_volume_below_baseline",
    "volume_above_baseline":        "entry_blocked_volume_above_baseline",
    "volume_reversal":              "entry_blocked_volume_reversal",
}


def build_signal_events(
    df: pd.DataFrame,
    trend: np.ndarray,
    atr_period: int,
    trade_mode: str,
    execution_model: ExecutionModel,
    # WP-T6 (plan §8.2): optional bar-level filter diagnostics from BacktestResult.
    # None (disabled path) → bit-identical output, no filter columns added.
    # not None (enabled path) → 4 filter columns appended after "is_reversal".
    filter_diagnostics: Optional[Dict[str, np.ndarray]] = None,
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
        filter_diagnostics: Optional bar-level diagnostics dict from
            ``BacktestResult.filter_diagnostics`` (spec §13 keyset).
            ``None`` (disabled path) → output is bit-identical to pre-WP-T6
            baseline: no filter columns, same row count and values.
            ``not None`` (enabled path) → 4 filter columns are appended after
            ``is_reversal`` and before ``exec_price`` (plan §9.5):
            ``filter_state_at_signal``, ``filter_decision``,
            ``filter_block_reason``, ``filter_trigger_source``.
            Close-rows get the ``"N/A"`` sentinel for all 4 columns (plan
            §9.1.1). Initialization flips (``prev==0``) are NOT injected
            (plan §8.2 audit-fix v0.3).

    Returns:
        Disabled path (``filter_diagnostics=None``): DataFrame with 17 base
        columns + 2 ratio columns = 19 columns total.
        Enabled path (``filter_diagnostics is not None``): DataFrame with 17
        base columns + 4 filter columns + 2 ratio columns = 23 columns total.
        Rows are ordered by bar index in ``df`` (ascending ``t``). For
        revers/both, the close row precedes its paired open row at the same
        ``Signal Time``.  May be empty (headers present, zero rows).

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

    # Compute ATR independently of engine (isolation: engine may use precomputed_atr)
    tr = calculate_true_range(high_arr, low_arr, close_arr)
    atr = calculate_atr_rma(tr, atr_period)

    # Normalise trade_mode: "both" is semantically identical to "revers"
    effective_mode = "revers" if trade_mode == "both" else trade_mode

    is_o2o = (execution_model == ExecutionModel.OPEN_TO_OPEN)

    # WP-T6: pre-extract filter arrays for O(1) per-bar lookup inside the loop.
    filter_enabled = filter_diagnostics is not None
    if filter_enabled:
        _fd_state = filter_diagnostics["trade_filter_state"]
        _fd_allowed = filter_diagnostics.get("filter_allowed_entry")
        if _fd_allowed is None:
            _fd_allowed = filter_diagnostics.get("volume_condition_allowed")
        _fd_reason = filter_diagnostics["filter_block_reason"]
        _fd_trigger = filter_diagnostics.get("trade_filter_trigger_source")
        _default_trigger = (
            "volume" if "volume_regime" in filter_diagnostics else "none"
        )

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
        atr_prev = atr[t - 1]

        body = abs(c_t - o_t)
        rng = h_t - l_t

        body_pct = body / o_t * 100.0 if o_t != 0 else float("nan")
        range_pct = rng / o_t * 100.0 if o_t != 0 else float("nan")
        body_atr = body / atr_prev if atr_prev != 0 else float("nan")
        range_atr = rng / atr_prev if atr_prev != 0 else float("nan")

        st_before = "GREEN" if prev == 1 else "RED"
        st_after = "GREEN" if curr == 1 else "RED"

        # ── Execution bar and exec_price ──
        if is_o2o:
            exec_bar = t + 1
        else:
            exec_bar = t

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

        # WP-T6: compute filter fields once per signal bar (shared by both revers rows).
        # Lookup is on decision bar t, not execution bar t+1 (plan §8.2 rule 3).
        if filter_enabled:
            _open_fld = _lookup_open_filter_fields(
                _fd_state,
                _fd_allowed,
                _fd_reason,
                _fd_trigger,
                t,
                default_trigger=_default_trigger,
            )
            _close_fld = _na_filter_fields()

        if effective_mode == "long":
            if prev == 1:
                # green→red: long CLOSE
                event_type = "close_signal"
                direction_str = "LONG"
                direction_val = +1  # post-exit same-side
            else:
                # red→green: long OPEN
                event_type = "open_signal"
                direction_str = "LONG"
                direction_val = +1

            row = _make_row(
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
            )
            if filter_enabled:
                # long close_signal = closing a trade → N/A; open_signal = entry → lookup
                row.update(_close_fld if event_type == "close_signal" else _open_fld)
            rows.append(row)

        elif effective_mode == "short":
            if prev == -1:
                # red→green: short CLOSE
                event_type = "close_signal"
                direction_str = "SHORT"
                direction_val = -1  # post-exit same-side
            else:
                # green→red: short OPEN
                event_type = "open_signal"
                direction_str = "SHORT"
                direction_val = -1

            row = _make_row(
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
            )
            if filter_enabled:
                # short close_signal = closing a trade → N/A; open_signal = entry → lookup
                row.update(_close_fld if event_type == "close_signal" else _open_fld)
            rows.append(row)

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

            # close row first (plan §9.1.1: close-rows → "N/A" for all filter cols)
            close_row = _make_row(
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
            )
            if filter_enabled:
                close_row.update(_close_fld)
            rows.append(close_row)

            # open row second (filter decision lookup at t)
            open_row = _make_row(
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
            )
            if filter_enabled:
                open_row.update(_open_fld)
            rows.append(open_row)

    # ── Build DataFrame ──
    # Column layout (filter-enabled): base_cols[:is_reversal+1] + filter_cols + base_cols[is_reversal+1:]
    # Ratio cols are always appended last (post-build vectorised step below).
    _base_cols = list(_COLUMN_NAMES)
    if filter_enabled:
        _insert_at = _base_cols.index("is_reversal") + 1
        _ordered_cols = _base_cols[:_insert_at] + list(_FILTER_COLUMN_NAMES) + _base_cols[_insert_at:]
    else:
        _ordered_cols = _base_cols

    if rows:
        result_df = pd.DataFrame(rows)
        if filter_enabled:
            result_df = result_df[_ordered_cols]
    else:
        result_df = pd.DataFrame(columns=_ordered_cols)

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


# ---------------------------------------------------------------------------
# WP-T6 helpers
# ---------------------------------------------------------------------------

def _na_filter_fields() -> dict:
    """Return the N/A sentinel dict for close-rows (plan §9.1.1)."""
    return {
        "filter_state_at_signal": _NA_FILTER,
        "filter_decision":        _NA_FILTER,
        "filter_block_reason":    _NA_FILTER,
        "filter_trigger_source":  _NA_FILTER,
    }


def _lookup_open_filter_fields(
    fd_state: np.ndarray,
    fd_allowed: Optional[np.ndarray],
    fd_reason: np.ndarray,
    fd_trigger: Optional[np.ndarray],
    t: int,
    *,
    default_trigger: str = "none",
) -> dict:
    """Look up filter fields at bar t for an open/entry signal row (plan §8.2).

    ZigZag diagnostics provide ``filter_allowed_entry`` and
    ``trade_filter_trigger_source``. Standalone volume does not, so use
    ``volume_condition_allowed`` when present and a stable trigger sentinel.
    """
    state = str(fd_state[t])
    reason = str(fd_reason[t])
    allowed = int(fd_allowed[t]) if fd_allowed is not None else int(reason == "none")
    trigger = str(fd_trigger[t]) if fd_trigger is not None else default_trigger

    if allowed == 1:
        decision = "entry_allowed"
    else:
        decision = _BLOCK_REASON_TO_DECISION.get(reason, f"entry_blocked_{reason}")

    return {
        "filter_state_at_signal": state,
        "filter_decision":        decision,
        "filter_block_reason":    reason,
        "filter_trigger_source":  trigger,
    }


# Internal column names (snake_case) — mapped to display names in excel_tester
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
    }
