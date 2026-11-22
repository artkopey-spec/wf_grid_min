"""Generic trade-level diagnostics attachment for trade filters."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
from supertrend_optimizer.utils.exceptions import ConfigError


def attach_trade_filter_diagnostics(
    trades_df: Any,
    heavy_diagnostics: Dict[str, np.ndarray] | None,
    *,
    execution_metadata: Dict[str, np.ndarray] | None = None,
) -> Any:
    """Attach filter diagnostics columns to an extracted trades DataFrame.

    Common reset fields are sufficient. ZigZag-specific and volume-specific
    fields are consumed only when present.
    """
    diagnostics = heavy_diagnostics or {}
    metadata = execution_metadata or {}
    # Backward compatibility: older callers pass compact reset metadata as the
    # second positional argument. New callers pass the namespaces separately.
    state_arr = diagnostics.get("trade_filter_state")
    trigger_arr = diagnostics.get("trade_filter_trigger_source")
    daily_reset_arr = metadata.get(
        "daily_reset_event", diagnostics.get("daily_reset_event")
    )
    time_reset_arr = metadata.get(
        "time_filter_reset_event", diagnostics.get("time_filter_reset_event")
    )
    reversal_event_arr = metadata.get(
        "reversal_confirmation_execution_event_id"
    )
    reversal_close_arr = metadata.get(
        "reversal_confirmation_confirmed_close"
    )
    reversal_open_arr = metadata.get(
        "reversal_confirmation_confirmed_open"
    )
    imm_triggered_arr = diagnostics.get("exit_b_immediate_off_triggered")
    volume_reason_arr = diagnostics.get("volume_condition_block_reason")
    block_reason_arr = diagnostics.get("filter_block_reason")
    wakeup_exit_reason_arr = diagnostics.get("wakeup_exit_reason")
    wakeup_position_action_arr = diagnostics.get("wakeup_position_action")
    state_at_bar_start_arr = diagnostics.get("state_at_bar_start")

    length_source = next(
        (
            values
            for values in (
                state_arr,
                daily_reset_arr,
                time_reset_arr,
                reversal_event_arr,
                block_reason_arr,
            )
            if values is not None
        ),
        None,
    )
    if length_source is None:
        raise ConfigError(
            "attach_trade_filter_diagnostics requires at least one common "
            "state/reset/reason array"
        )
    n_diag = len(length_source)
    pending_exit_idx = n_diag - 1
    entry_filter_states = []
    entry_trigger_sources = []
    exit_reasons = []
    wakeup_cycle_exit_reasons = []
    wakeup_position_actions = []
    entry_volume_block_reasons = []
    entry_reversal_event_ids: list[int | None] = []
    exit_reversal_event_ids: list[int | None] = []

    for row in trades_df.itertuples(index=False):
        entry_index = int(row.entry_index)
        exit_index = int(row.exit_index)
        entry_signal_idx = max(entry_index - 1, 0)
        exit_signal_idx = max(exit_index - 1, 0)
        entry_event_id = _event_id_at(
            reversal_event_arr,
            reversal_open_arr,
            entry_index,
        )
        exit_event_id = (
            _event_id_at(
                reversal_event_arr,
                reversal_close_arr,
                exit_index,
            )
            if exit_index > entry_index
            else None
        )
        entry_reversal_event_ids.append(entry_event_id)
        exit_reversal_event_ids.append(exit_event_id)

        if state_arr is not None and entry_signal_idx < len(state_arr):
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
            str(state_arr[exit_signal_idx])
            if state_arr is not None and exit_signal_idx < len(state_arr)
            else "UNKNOWN"
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

        if reset_at_exit:
            exit_reasons.append("filter_daily_reset")
        elif time_reset_at_exit:
            exit_reasons.append("filter_time_reset")
        elif exit_event_id is not None:
            exit_reasons.append("reversal_confirmation_confirmed")
        elif wakeup_exit_reason is not None:
            exit_reasons.append(wakeup_exit_reason)
        elif exit_index >= pending_exit_idx and getattr(row, "exit_phase", "mark_open") == "mark_open":
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
    if state_arr is not None:
        out["entry_filter_state"] = entry_filter_states
    if trigger_arr is not None:
        out["entry_trigger_source"] = entry_trigger_sources
    if volume_reason_arr is not None:
        out["entry_volume_block_reason"] = entry_volume_block_reasons
    if wakeup_exit_reason_arr is not None:
        out["wakeup_cycle_exit_reason"] = wakeup_cycle_exit_reasons
    if wakeup_position_action_arr is not None:
        out["wakeup_position_action"] = wakeup_position_actions
    out["exit_reason"] = exit_reasons
    if reversal_event_arr is not None:
        import pandas as pd

        out["entry_reversal_confirmation_event_id"] = pd.array(
            entry_reversal_event_ids, dtype="Int64"
        )
        out["exit_reversal_confirmation_event_id"] = pd.array(
            exit_reversal_event_ids, dtype="Int64"
        )
        out["exit_reason"] = out["exit_reason"].astype("string")
    return out


def _flag_at(arr: Any, idx: int) -> bool:
    return arr is not None and idx < len(arr) and int(arr[idx]) == 1


def _event_id_at(event_arr: Any, flag_arr: Any, idx: int) -> int | None:
    if (
        event_arr is None
        or flag_arr is None
        or idx < 0
        or idx >= len(event_arr)
        or idx >= len(flag_arr)
        or int(flag_arr[idx]) != 1
    ):
        return None
    event_id = int(event_arr[idx])
    return event_id if event_id >= 0 else None


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
