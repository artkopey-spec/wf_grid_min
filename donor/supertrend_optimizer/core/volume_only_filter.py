"""Standalone volume filter runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.core._fsm_state_names import STANDALONE_VOLUME_STATE_NAMES
from supertrend_optimizer.core._reset_events import (
    _infer_daily_reset_event,
    _infer_time_filter_events,
    detect_st_flip,
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


_STATE_NAMES = {
    VolumeOnlyState.OFF: STANDALONE_VOLUME_STATE_NAMES[0],
    VolumeOnlyState.ACTIVE_LONG: STANDALONE_VOLUME_STATE_NAMES[1],
    VolumeOnlyState.ACTIVE_SHORT: STANDALONE_VOLUME_STATE_NAMES[2],
}


@dataclass(frozen=True)
class VolumeOnlyFilterResult:
    positions: np.ndarray
    filter_diagnostics: dict
    filter_config_snapshot: dict


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

    daily_reset_event = _resolve_daily_reset_event(
        daily_reset_event=daily_reset_event,
        index=index,
        n=n,
        trade_filter_config=trade_filter_config,
    )
    time_filter_in_window, time_filter_reset_event = _resolve_time_filter_events(
        time_filter_events=time_filter_events,
        index=index,
        n=n,
        trade_filter_config=trade_filter_config,
    )
    time_filter_cfg = getattr(trade_filter_config, "time_filter", None)
    time_filter_enabled = bool(getattr(time_filter_cfg, "enabled", False))

    volume_block_reason_labels = materialize_volume_block_reason(
        volume_block_reason_codes
    )
    volume_regime_labels = materialize_volume_regime(volume_regime_codes)
    volume_direction_labels = materialize_volume_initial_direction(
        volume_direction_codes
    )

    positions = np.zeros(n, dtype=np.int8)
    state_arr = np.full(n, _STATE_NAMES[VolumeOnlyState.OFF], dtype=object)
    block_reason_arr = np.full(n, "none", dtype=object)

    state = VolumeOnlyState.OFF
    mode = str(volume_runtime.filter_config_snapshot.get("volume_filter_mode", ""))
    threshold = float(
        volume_runtime.filter_config_snapshot.get("volume_threshold_ratio", np.nan)
    )
    lookback = int(
        volume_runtime.filter_config_snapshot.get(
            "volume_direction_lookback_bars", 1
        )
    )

    for t in range(n):
        if daily_reset_event[t]:
            state = VolumeOnlyState.OFF
            block_reason_arr[t] = "daily_reset"
        elif time_filter_reset_event[t]:
            state = VolumeOnlyState.OFF
            block_reason_arr[t] = "time_filter_reset"
        elif _volume_reversal(mode, median_relative_volume[t], threshold, state):
            state = VolumeOnlyState.OFF
            block_reason_arr[t] = "volume_reversal"
        else:
            prev_trend = int(trend_arr[t - 1]) if t > 0 else 0
            flip_dir = detect_st_flip(prev_trend, int(trend_arr[t]))
            if state != VolumeOnlyState.OFF:
                if flip_dir != 0 and not _trade_mode_allows_direction(
                    flip_dir, trade_mode
                ):
                    state = VolumeOnlyState.OFF
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
                if not bool(time_filter_in_window[t]):
                    block_reason_arr[t] = "time_filter_out_of_window"
                elif direction == 0 and global_t < lookback:
                    block_reason_arr[t] = "volume_direction_warmup"
                elif direction == 0:
                    block_reason_arr[t] = "volume_unknown_direction"
                elif not _trade_mode_allows_direction(direction, trade_mode):
                    block_reason_arr[t] = "volume_trade_mode_disallowed_direction"
                elif not bool(volume_allowed[t]):
                    block_reason_arr[t] = volume_block_reason_labels[t]
                else:
                    state = _state_from_direction(direction)

        state_arr[t] = _STATE_NAMES[state]
        if t + 1 < n:
            positions[t + 1] = np.int8(_state_position(state))

    diagnostics = {
        "trade_filter_state": state_arr,
        "filter_block_reason": block_reason_arr,
        "volume_regime": volume_regime_labels,
        "volume_condition_allowed": volume_allowed,
        "volume_condition_block_reason": volume_block_reason_labels,
        "volume_initial_direction": volume_direction_labels,
        "median_relative_volume": median_relative_volume,
        "daily_reset_event": np.asarray(daily_reset_event, dtype=np.int8),
        "time_filter_enabled": np.full(
            n, np.int8(1 if time_filter_enabled else 0), dtype=np.int8
        ),
        "time_filter_in_window": np.asarray(time_filter_in_window, dtype=np.int8),
        "time_filter_reset_event": np.asarray(time_filter_reset_event, dtype=np.int8),
    }
    return VolumeOnlyFilterResult(
        positions=positions,
        filter_diagnostics=diagnostics,
        filter_config_snapshot=volume_runtime.filter_config_snapshot,
    )


def _resolve_daily_reset_event(
    *,
    daily_reset_event: Optional[np.ndarray],
    index: Optional[pd.Index],
    n: int,
    trade_filter_config,
) -> np.ndarray:
    if daily_reset_event is None:
        zigzag_cfg = getattr(trade_filter_config, "zigzag", None)
        enabled = bool(getattr(zigzag_cfg, "daily_reset", False))
        return _infer_daily_reset_event(index, n, enabled=enabled)
    arr = np.asarray(daily_reset_event, dtype=bool)
    if arr.ndim != 1 or len(arr) != n:
        raise ConfigError(
            f"apply() daily_reset_event must be 1-D bool of length n={n}"
        )
    return arr


def _resolve_time_filter_events(
    *,
    time_filter_events: Optional[tuple[np.ndarray, np.ndarray]],
    index: Optional[pd.Index],
    n: int,
    trade_filter_config,
) -> tuple[np.ndarray, np.ndarray]:
    if time_filter_events is None:
        time_filter_cfg = getattr(trade_filter_config, "time_filter", None)
        enabled = bool(getattr(time_filter_cfg, "enabled", False))
        return _infer_time_filter_events(
            index,
            n,
            enabled=enabled,
            start_h=int(getattr(time_filter_cfg, "_start_hour", 0) or 0),
            start_m=int(getattr(time_filter_cfg, "_start_minute", 0) or 0),
            end_h=int(getattr(time_filter_cfg, "_end_hour", 0) or 0),
            end_m=int(getattr(time_filter_cfg, "_end_minute", 0) or 0),
        )

    in_window_raw, reset_raw = time_filter_events
    in_window = np.asarray(in_window_raw, dtype=bool)
    reset = np.asarray(reset_raw, dtype=bool)
    if in_window.ndim != 1 or len(in_window) != n:
        raise ConfigError(
            f"apply() time_filter_events[0] must be 1-D bool of length n={n}"
        )
    if reset.ndim != 1 or len(reset) != n:
        raise ConfigError(
            f"apply() time_filter_events[1] must be 1-D bool of length n={n}"
        )
    return in_window, reset


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


def _state_position(state: VolumeOnlyState) -> int:
    if state == VolumeOnlyState.ACTIVE_LONG:
        return 1
    if state == VolumeOnlyState.ACTIVE_SHORT:
        return -1
    return 0


def _volume_reversal(
    mode: str,
    median_relative_volume: float,
    threshold: float,
    state: VolumeOnlyState,
) -> bool:
    if state == VolumeOnlyState.OFF:
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
