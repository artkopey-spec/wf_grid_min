"""Generic trade-level diagnostics attachment for trade filters."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from supertrend_optimizer.utils.exceptions import ConfigError


def attach_trade_filter_diagnostics(
    trades_df: Any,
    filter_diagnostics: Dict[str, np.ndarray],
) -> Any:
    """Attach filter diagnostics columns to an extracted trades DataFrame.

    ``trade_filter_state`` is required.  ZigZag-specific and volume-specific
    fields are consumed only when present.
    """
    state_arr = filter_diagnostics.get("trade_filter_state")
    if state_arr is None:
        raise ConfigError(
            "attach_trade_filter_diagnostics: 'trade_filter_state' key "
            "missing from filter_diagnostics"
        )

    trigger_arr = filter_diagnostics.get("trade_filter_trigger_source")
    daily_reset_arr = filter_diagnostics.get("daily_reset_event")
    time_reset_arr = filter_diagnostics.get("time_filter_reset_event")
    imm_triggered_arr = filter_diagnostics.get("exit_b_immediate_off_triggered")
    volume_reason_arr = filter_diagnostics.get("volume_condition_block_reason")
    block_reason_arr = filter_diagnostics.get("filter_block_reason")
    wakeup_exit_reason_arr = filter_diagnostics.get("wakeup_exit_reason")

    n_diag = len(state_arr)
    pending_exit_idx = n_diag - 1
    entry_filter_states = []
    entry_trigger_sources = []
    exit_reasons = []
    entry_volume_block_reasons = []

    for row in trades_df.itertuples(index=False):
        entry_index = int(row.entry_index)
        exit_index = int(row.exit_index)
        entry_signal_idx = max(entry_index - 1, 0)
        exit_signal_idx = max(exit_index - 1, 0)

        if entry_signal_idx < n_diag:
            entry_filter_states.append(str(state_arr[entry_signal_idx]))
        else:
            entry_filter_states.append("UNKNOWN")

        if trigger_arr is not None and entry_signal_idx < len(trigger_arr):
            entry_trigger_sources.append(str(trigger_arr[entry_signal_idx]))
        else:
            entry_trigger_sources.append("none")

        if volume_reason_arr is not None and entry_signal_idx < len(volume_reason_arr):
            entry_volume_block_reasons.append(str(volume_reason_arr[entry_signal_idx]))
        else:
            entry_volume_block_reasons.append("none")

        fsm_at_exit = (
            str(state_arr[exit_signal_idx]) if exit_signal_idx < n_diag else "UNKNOWN"
        )
        reset_at_exit = _flag_at(daily_reset_arr, exit_signal_idx)
        time_reset_at_exit = _flag_at(time_reset_arr, exit_signal_idx)
        imm_at_exit = _flag_at(imm_triggered_arr, exit_signal_idx)
        wakeup_exit_reason = _wakeup_exit_reason_at(
            wakeup_exit_reason_arr,
            exit_signal_idx,
        )
        block_at_exit = (
            str(block_reason_arr[exit_signal_idx])
            if block_reason_arr is not None and exit_signal_idx < len(block_reason_arr)
            else "none"
        )

        if wakeup_exit_reason is not None:
            exit_reasons.append(wakeup_exit_reason)
        elif reset_at_exit:
            exit_reasons.append("filter_daily_reset")
        elif time_reset_at_exit:
            exit_reasons.append("filter_time_reset")
        elif exit_index >= pending_exit_idx:
            exit_reasons.append("pending_open_trade_at_end")
        elif imm_at_exit:
            exit_reasons.append("filter_exit_b_immediate_off")
        elif block_at_exit == "volume_reversal":
            exit_reasons.append("filter_volume_reversal")
        elif block_at_exit == "trade_mode_forced_exit":
            exit_reasons.append("filter_trade_mode_forced_exit")
        elif fsm_at_exit == "ST_STOPPING":
            exit_reasons.append("filter_stopping_opposite_flip")
        else:
            exit_reasons.append("st_flip")

    out = trades_df.copy()
    out["entry_filter_state"] = entry_filter_states
    out["entry_trigger_source"] = entry_trigger_sources
    if volume_reason_arr is not None:
        out["entry_volume_block_reason"] = entry_volume_block_reasons
    out["exit_reason"] = exit_reasons
    return out


def _flag_at(arr: Any, idx: int) -> bool:
    return arr is not None and idx < len(arr) and int(arr[idx]) == 1


def _wakeup_exit_reason_at(arr: Any, idx: int) -> str | None:
    if arr is None or idx >= len(arr):
        return None
    return {
        "ttl": "wakeup_exit_ttl",
        "no_fresh_candidate": "wakeup_exit_no_fresh_candidate",
        "reset": "wakeup_exit_reset",
        "opposite_st_flip": "wakeup_exit_opposite_st_flip",
    }.get(str(arr[idx]))


__all__ = ["attach_trade_filter_diagnostics"]
