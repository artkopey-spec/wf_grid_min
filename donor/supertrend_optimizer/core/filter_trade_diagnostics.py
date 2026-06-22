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
    wakeup_position_action_arr = filter_diagnostics.get("wakeup_position_action")
    state_at_bar_start_arr = filter_diagnostics.get("state_at_bar_start")

    n_diag = len(state_arr)
    pending_exit_idx = n_diag - 1
    entry_filter_states = []
    entry_trigger_sources = []
    exit_reasons = []
    wakeup_cycle_exit_reasons = []
    wakeup_position_actions = []
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
        fsm_at_exit_start = (
            _state_name_at(state_at_bar_start_arr, exit_signal_idx)
            if state_at_bar_start_arr is not None
            else "UNKNOWN"
        )
        reset_at_exit = _flag_at(daily_reset_arr, exit_signal_idx)
        time_reset_at_exit = _flag_at(time_reset_arr, exit_signal_idx)
        imm_at_exit = _flag_at(imm_triggered_arr, exit_signal_idx)
        wakeup_exit_reason = _wakeup_exit_reason_at(
            wakeup_exit_reason_arr,
            exit_signal_idx,
        )
        wakeup_position_action = _wakeup_position_action_at(
            wakeup_position_action_arr,
            exit_signal_idx,
        )
        wakeup_cycle_exit_reasons.append(
            _raw_diag_value_at(wakeup_exit_reason_arr, exit_signal_idx)
        )
        wakeup_position_actions.append(
            _raw_diag_value_at(wakeup_position_action_arr, exit_signal_idx)
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
        elif wakeup_position_action is not None:
            exit_reasons.append(wakeup_position_action)
        elif fsm_at_exit == "ST_STOPPING" or fsm_at_exit_start == "ST_STOPPING":
            exit_reasons.append("filter_stopping_opposite_flip")
        else:
            exit_reasons.append("st_flip")

    out = trades_df.copy()
    out["entry_filter_state"] = entry_filter_states
    out["entry_trigger_source"] = entry_trigger_sources
    if volume_reason_arr is not None:
        out["entry_volume_block_reason"] = entry_volume_block_reasons
    if wakeup_exit_reason_arr is not None:
        out["wakeup_cycle_exit_reason"] = wakeup_cycle_exit_reasons
    if wakeup_position_action_arr is not None:
        out["wakeup_position_action"] = wakeup_position_actions
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
        "local_median_stop": "wakeup_exit_local_median_stop",
        "cycle_trade_limit": "wakeup_exit_cycle_trade_limit",
        "cycle_take_profit": "wakeup_exit_cycle_take_profit",
        "reset": "wakeup_exit_reset",
        "opposite_st_flip": "wakeup_exit_opposite_st_flip",
    }.get(str(arr[idx]))


def _wakeup_position_action_at(arr: Any, idx: int) -> str | None:
    if arr is None or idx >= len(arr):
        return None
    return {
        "reverse_on_st_flip": "wakeup_reverse_on_st_flip",
        "flat_on_disallowed_st_flip": "wakeup_flat_on_disallowed_st_flip",
        "exit_local_median_stop": "wakeup_exit_local_median_stop",
        "exit_cycle_trade_limit": "wakeup_exit_cycle_trade_limit",
        "exit_cycle_take_profit": "wakeup_exit_cycle_take_profit",
    }.get(str(arr[idx]))


def _raw_diag_value_at(arr: Any, idx: int) -> str:
    if arr is None or idx >= len(arr):
        return "none"
    return str(arr[idx])


def _state_name_at(arr: Any, idx: int) -> str:
    if arr is None or idx >= len(arr):
        return "UNKNOWN"
    value = arr[idx]
    if isinstance(value, str):
        return value
    try:
        return {
            0: "OFF",
            1: "WAIT_FIRST_ST_FLIP",
            2: "ST_ACTIVE_FREEZE",
            3: "ST_ACTIVE_MONITORING",
            4: "ST_STOPPING",
            5: "ST_COUNTING_ZZ_LEGS",
        }[int(value)]
    except (KeyError, TypeError, ValueError):
        return "UNKNOWN"


__all__ = ["attach_trade_filter_diagnostics"]
