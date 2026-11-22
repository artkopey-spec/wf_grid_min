"""Standalone volume filter runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.core._fsm_state_names import STANDALONE_VOLUME_STATE_NAMES
from supertrend_optimizer.core._reset_events import (
    detect_st_flip,
    resolve_reset_events,
)
from supertrend_optimizer.core.trade_filter_decision import (
    build_filter_execution_metadata,
    decide_trade_filter_entry,
)
from supertrend_optimizer.core.volume_metrics import (
    VolumeRuntime,
    materialize_volume_block_reason,
    materialize_volume_initial_direction,
    materialize_volume_regime,
)
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError


class VolumeOnlyState(IntEnum):
    OFF = 0
    ACTIVE_LONG = 1
    ACTIVE_SHORT = -1
    SUPPRESSED_LONG = 2
    SUPPRESSED_SHORT = -2


_STATE_NAMES = {
    VolumeOnlyState.OFF: STANDALONE_VOLUME_STATE_NAMES[0],
    VolumeOnlyState.ACTIVE_LONG: STANDALONE_VOLUME_STATE_NAMES[1],
    VolumeOnlyState.ACTIVE_SHORT: STANDALONE_VOLUME_STATE_NAMES[2],
    VolumeOnlyState.SUPPRESSED_LONG: STANDALONE_VOLUME_STATE_NAMES[3],
    VolumeOnlyState.SUPPRESSED_SHORT: STANDALONE_VOLUME_STATE_NAMES[4],
}


@dataclass(frozen=True)
class VolumeOnlyFilterResult:
    positions: np.ndarray
    filter_diagnostics: Optional[dict]
    filter_config_snapshot: dict
    execution_metadata: dict[str, np.ndarray]


def apply(
    *,
    open_prices: np.ndarray,
    close: np.ndarray,
    trend: np.ndarray,
    trade_mode: str,
    trade_filter_config,
    volume_runtime: VolumeRuntime,
    execution_model=ExecutionModel.OPEN_TO_OPEN,
    index: Optional[pd.Index] = None,
    daily_reset_event: Optional[np.ndarray] = None,
    time_filter_events: Optional[tuple[np.ndarray, np.ndarray]] = None,
    collect_filter_diagnostics: bool = True,
    date_execution=None,
) -> VolumeOnlyFilterResult:
    if execution_model != ExecutionModel.OPEN_TO_OPEN:
        raise ValueError(
            f"ExecutionModel.{execution_model} is not supported. "
            "Only OPEN_TO_OPEN is allowed."
        )
    if volume_runtime is None:
        raise RuntimeError("volume_runtime required when trade_filter.volume.enabled=true")
    if trade_mode not in {"long", "short", "both", "revers"}:
        raise ValueError(
            "trade_mode must be one of {'long', 'short', 'both', 'revers'}, "
            f"got: {trade_mode}"
        )

    open_arr = np.asarray(open_prices)
    close_arr = np.asarray(close)
    trend_arr = np.asarray(trend, dtype=np.int64)
    if open_arr.ndim != 1 or close_arr.ndim != 1 or trend_arr.ndim != 1:
        raise ValueError("open_prices, close, and trend must be 1-D arrays")
    n = int(trend_arr.shape[0])
    if len(open_arr) != n or len(close_arr) != n:
        raise ValueError(
            f"length mismatch: open={len(open_arr)}, close={len(close_arr)}, trend={n}"
        )

    volume_allowed = np.asarray(volume_runtime.volume_condition_allowed, dtype=bool)
    volume_block_reason_codes = np.asarray(volume_runtime.volume_condition_block_reason)
    volume_regime_codes = np.asarray(volume_runtime.volume_regime)
    volume_direction_codes = np.asarray(volume_runtime.volume_initial_direction)
    median_relative_volume = np.asarray(
        volume_runtime.median_relative_volume, dtype=np.float64
    )
    _volume_arrays = {
        "volume_condition_allowed": volume_allowed,
        "volume_condition_block_reason": volume_block_reason_codes,
        "volume_regime": volume_regime_codes,
        "volume_initial_direction": volume_direction_codes,
        "median_relative_volume": median_relative_volume,
    }
    for name, arr in _volume_arrays.items():
        if arr.ndim != 1 or len(arr) != n:
            raise ValueError(f"volume_runtime.{name} must be 1-D with length {n}")

    reset_events = resolve_reset_events(
        trade_filter_config=trade_filter_config,
        index=index,
        n=n,
        owner="volume",
        daily_reset_event=daily_reset_event,
        time_filter_events=time_filter_events,
    )
    daily_reset_enabled = reset_events.daily_reset_enabled
    daily_reset_event = reset_events.daily_reset_event
    time_filter_enabled = reset_events.time_filter_enabled
    time_filter_in_window = reset_events.time_filter_in_window
    time_filter_reset_event = reset_events.time_filter_reset_event
    effective_mode = "volume_and_time" if time_filter_enabled else "volume_only"
    execution_metadata = build_filter_execution_metadata(
        daily_reset_event=daily_reset_event,
        time_filter_reset_event=time_filter_reset_event,
    )

    diag_enabled = bool(collect_filter_diagnostics)
    volume_block_reason_labels = (
        materialize_volume_block_reason(volume_block_reason_codes)
        if diag_enabled
        else None
    )
    volume_regime_labels = (
        materialize_volume_regime(volume_regime_codes)
        if diag_enabled
        else None
    )
    volume_direction_labels = (
        materialize_volume_initial_direction(volume_direction_codes)
        if diag_enabled
        else None
    )

    positions = np.zeros(n, dtype=np.int8)
    state_arr = (
        np.full(n, _STATE_NAMES[VolumeOnlyState.OFF], dtype=object)
        if diag_enabled
        else None
    )
    block_reason_arr = (
        np.full(n, "none", dtype=object)
        if diag_enabled
        else None
    )
    cycle_initial_direction_arr = (
        np.full(n, "unknown", dtype=object)
        if diag_enabled
        else None
    )
    cycle_direction_gate_passed_arr = (
        np.zeros(n, dtype=np.int8)
        if diag_enabled
        else None
    )

    state = VolumeOnlyState.OFF
    volume_cfg = getattr(trade_filter_config, "volume", None)
    cycle_direction_gate = bool(getattr(volume_cfg, "cycle_direction_gate", False))
    mode = str(volume_runtime.filter_config_snapshot.get("volume_filter_mode", ""))
    threshold = float(
        volume_runtime.filter_config_snapshot.get("volume_threshold_ratio", np.nan)
    )
    exit_threshold = volume_runtime.filter_config_snapshot.get(
        "volume_exit_hysteresis_ratio", None
    )
    if exit_threshold is None:
        exit_threshold = threshold
    exit_threshold = float(exit_threshold)
    exit_freeze_bars = volume_runtime.filter_config_snapshot.get(
        "volume_exit_freeze_bars", None
    )
    if exit_freeze_bars is None:
        exit_freeze_bars = 0
    exit_freeze_bars = int(exit_freeze_bars)
    lookback = int(
        volume_runtime.filter_config_snapshot.get(
            "volume_direction_lookback_bars", 1
        )
    )

    active_age_bars = 0
    cycle_initial_direction = 0
    for t in range(n):
        state_at_bar_start = state
        flip_dir = 0
        shared_decision = decide_trade_filter_entry(
            entry_attempt_event=state_at_bar_start == VolumeOnlyState.OFF,
            time_filter_in_window=bool(time_filter_in_window[t]),
            combined_reset_event=bool(reset_events.combined_reset_event[t]),
            daily_reset_event=bool(daily_reset_event[t]),
            time_filter_reset_event=bool(time_filter_reset_event[t]),
        )
        if daily_reset_event[t]:
            state = VolumeOnlyState.OFF
            cycle_initial_direction = 0
            if diag_enabled:
                block_reason_arr[t] = "daily_reset"
        elif time_filter_reset_event[t]:
            state = VolumeOnlyState.OFF
            cycle_initial_direction = 0
            if diag_enabled:
                block_reason_arr[t] = "time_filter_reset"
        elif (
            active_age_bars >= exit_freeze_bars
            and _volume_reversal(mode, median_relative_volume[t], exit_threshold, state)
        ):
            state = VolumeOnlyState.OFF
            cycle_initial_direction = 0
            if diag_enabled:
                block_reason_arr[t] = "volume_reversal"
        else:
            prev_trend = int(trend_arr[t - 1]) if t > 0 else 0
            flip_dir = detect_st_flip(prev_trend, int(trend_arr[t]))
            if state != VolumeOnlyState.OFF:
                if cycle_direction_gate:
                    if _is_suppressed_state(state):
                        if diag_enabled:
                            block_reason_arr[t] = "volume_cycle_direction_mismatch"
                    elif flip_dir != 0 and flip_dir != _state_cycle_direction(state):
                        if diag_enabled:
                            block_reason_arr[t] = "volume_cycle_direction_mismatch"
                    elif flip_dir != 0:
                        state = _state_from_direction(_state_cycle_direction(state))
                elif flip_dir != 0 and not _trade_mode_allows_direction(
                    flip_dir, trade_mode
                ):
                    state = VolumeOnlyState.OFF
                    cycle_initial_direction = 0
                    if diag_enabled:
                        block_reason_arr[t] = "trade_mode_forced_exit"
                elif (
                    flip_dir != 0
                    and _state_position(state) != flip_dir
                    and _trade_mode_allows_direction(flip_dir, trade_mode)
                ):
                    state = _state_from_direction(flip_dir)
                else:
                    pass
            else:
                direction = int(volume_direction_codes[t])
                global_t = t + int(volume_runtime.absolute_offset)
                if shared_decision.entry_blocked_outside:
                    if diag_enabled:
                        block_reason_arr[t] = "time_filter_out_of_window"
                elif direction == 0 and global_t < lookback:
                    if diag_enabled:
                        block_reason_arr[t] = "volume_direction_warmup"
                elif direction == 0:
                    if diag_enabled:
                        block_reason_arr[t] = "volume_unknown_direction"
                elif (
                    not cycle_direction_gate
                    and not _trade_mode_allows_direction(direction, trade_mode)
                ):
                    if diag_enabled:
                        block_reason_arr[t] = "volume_trade_mode_disallowed_direction"
                elif not bool(volume_allowed[t]):
                    if diag_enabled:
                        block_reason_arr[t] = volume_block_reason_labels[t]
                else:
                    if cycle_direction_gate and not _trade_mode_allows_direction(
                        direction, trade_mode
                    ):
                        state = _suppressed_state_from_direction(direction)
                        cycle_initial_direction = direction
                        if diag_enabled:
                            block_reason_arr[t] = "volume_cycle_direction_mismatch"
                    else:
                        state = _state_from_direction(direction)
                        cycle_initial_direction = direction

        if diag_enabled:
            state_arr[t] = _STATE_NAMES[state]
            cycle_initial_direction_arr[t] = _direction_label(
                cycle_initial_direction if _is_cycle_state(state) else 0
            )
            cycle_direction_gate_passed_arr[t] = np.int8(
                1 if _is_tradable_state(state) else 0
            )
        if t + 1 < n:
            target = _state_position(state)
            if date_execution is not None:
                current = 0 if date_execution.boundary[t] else positions[t]
                # The observed volume cycle survives a calendar close. Only a
                # cycle start or an eligible ST flip can request another entry.
                if target and state == state_at_bar_start and flip_dir != target:
                    target = current
                target = date_execution.at_open(t + 1, current, target)
            positions[t + 1] = np.int8(target)
        if _is_cycle_state(state):
            active_age_bars += 1
        else:
            active_age_bars = 0

    if not diag_enabled:
        return VolumeOnlyFilterResult(
            positions=positions,
            filter_diagnostics=None,
            filter_config_snapshot=volume_runtime.filter_config_snapshot,
            execution_metadata=execution_metadata,
        )

    diagnostics = {
        "trade_filter_state": state_arr,
        "filter_block_reason": block_reason_arr,
        "cycle_initial_direction": cycle_initial_direction_arr,
        "cycle_direction_gate_enabled": np.full(
            n, np.int8(1 if cycle_direction_gate else 0), dtype=np.int8
        ),
        "cycle_direction_gate_passed": cycle_direction_gate_passed_arr,
        "volume_regime": volume_regime_labels,
        "volume_condition_allowed": volume_allowed,
        "volume_condition_block_reason": volume_block_reason_labels,
        "volume_initial_direction": volume_direction_labels,
        "median_relative_volume": median_relative_volume,
        "daily_reset_enabled": np.full(n, int(daily_reset_enabled), dtype=np.int8),
        "daily_reset_event": np.asarray(daily_reset_event, dtype=np.int8),
        "time_filter_enabled": np.full(
            n, np.int8(1 if time_filter_enabled else 0), dtype=np.int8
        ),
        "time_filter_in_window": np.asarray(time_filter_in_window, dtype=np.int8),
        "time_filter_reset_event": np.asarray(time_filter_reset_event, dtype=np.int8),
    }
    if time_filter_enabled:
        diagnostics.update(
            {
                "trade_filter_enabled": np.ones(n, dtype=np.int8),
                "trade_filter_mode": np.full(n, effective_mode, dtype=object),
                "filter_allowed_entry": np.concatenate(
                    (
                        np.asarray(
                            (positions[1:] != 0)
                            & (positions[1:] != positions[:-1]),
                            dtype=np.int8,
                        ),
                        np.zeros(1, dtype=np.int8),
                    )
                ),
            }
        )
        time_forced_exit = np.zeros(n, dtype=np.int8)
        if n >= 2:
            time_forced_exit[:-1] = np.asarray(
                time_filter_reset_event[:-1]
                & ~daily_reset_event[:-1]
                & (positions[:-1] != 0)
                & (positions[1:] == 0),
                dtype=np.int8,
            )
        if date_execution is not None:
            time_forced_exit[date_execution.boundary] = 0
        diagnostics["time_filter_forced_exit"] = time_forced_exit
    return VolumeOnlyFilterResult(
        positions=positions,
        filter_diagnostics=diagnostics,
        filter_config_snapshot=volume_runtime.filter_config_snapshot,
        execution_metadata=execution_metadata,
    )


def _trade_mode_allows_direction(direction: int, trade_mode: str) -> bool:
    if direction == 1:
        return trade_mode in {"long", "both", "revers"}
    if direction == -1:
        return trade_mode in {"short", "both", "revers"}
    return False


def _state_from_direction(direction: int) -> VolumeOnlyState:
    if direction > 0:
        return VolumeOnlyState.ACTIVE_LONG
    return VolumeOnlyState.ACTIVE_SHORT


def _suppressed_state_from_direction(direction: int) -> VolumeOnlyState:
    if direction > 0:
        return VolumeOnlyState.SUPPRESSED_LONG
    return VolumeOnlyState.SUPPRESSED_SHORT


def _is_cycle_state(state: VolumeOnlyState) -> bool:
    return state != VolumeOnlyState.OFF


def _is_tradable_state(state: VolumeOnlyState) -> bool:
    return state in {VolumeOnlyState.ACTIVE_LONG, VolumeOnlyState.ACTIVE_SHORT}


def _is_suppressed_state(state: VolumeOnlyState) -> bool:
    return state in {VolumeOnlyState.SUPPRESSED_LONG, VolumeOnlyState.SUPPRESSED_SHORT}


def _state_position(state: VolumeOnlyState) -> int:
    if state == VolumeOnlyState.ACTIVE_LONG:
        return 1
    if state == VolumeOnlyState.ACTIVE_SHORT:
        return -1
    return 0


def _state_cycle_direction(state: VolumeOnlyState) -> int:
    if state in {VolumeOnlyState.ACTIVE_LONG, VolumeOnlyState.SUPPRESSED_LONG}:
        return 1
    if state in {VolumeOnlyState.ACTIVE_SHORT, VolumeOnlyState.SUPPRESSED_SHORT}:
        return -1
    return 0


def _direction_label(direction: int) -> str:
    if direction > 0:
        return "long"
    if direction < 0:
        return "short"
    return "unknown"


def _volume_reversal(
    mode: str,
    median_relative_volume: float,
    threshold: float,
    state: VolumeOnlyState,
) -> bool:
    if not _is_cycle_state(state):
        return False
    if not np.isfinite(median_relative_volume) or not np.isfinite(threshold):
        return False
    if mode == "volume_A":
        return bool(median_relative_volume < threshold)
    if mode == "volume_B":
        return bool(median_relative_volume > threshold)
    return False


__all__ = [
    "VolumeOnlyFilterResult",
    "VolumeOnlyState",
    "apply",
]
