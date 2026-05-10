"""Volume-filter metrics and transport runtime."""

from __future__ import annotations

from dataclasses import dataclass
import warnings

import numpy as np
import pandas as pd


# Regime codes.
REGIME_WARMUP = 0
REGIME_BASELINE_ZERO = 1
REGIME_LOW = 2
REGIME_NORMAL = 3
REGIME_HIGH = 4

# Block-reason codes.
BLOCK_NONE = 0
BLOCK_WARMUP = 1
BLOCK_BASELINE_ZERO = 2
BLOCK_BELOW_BASELINE = 3
BLOCK_ABOVE_BASELINE = 4

# Initial-direction codes.
DIR_SHORT = -1
DIR_UNKNOWN = 0
DIR_LONG = 1

_PER_BAR_ARRAY_FIELDS = (
    "short_median_volume",
    "baseline_median_volume",
    "median_relative_volume",
    "volume_regime",
    "volume_condition_allowed",
    "volume_condition_block_reason",
    "volume_initial_direction",
)


@dataclass(frozen=True)
class VolumeRuntime:
    short_median_volume: np.ndarray
    baseline_median_volume: np.ndarray
    median_relative_volume: np.ndarray
    volume_regime: np.ndarray
    volume_condition_allowed: np.ndarray
    volume_condition_block_reason: np.ndarray
    volume_initial_direction: np.ndarray
    absolute_offset: int
    reference_length: int
    filter_config_snapshot: dict

    def __post_init__(self) -> None:
        for name in _PER_BAR_ARRAY_FIELDS:
            arr = np.asarray(getattr(self, name))
            if len(arr) != self.reference_length:
                raise ValueError(
                    f"{name} length {len(arr)} != reference_length "
                    f"{self.reference_length}"
                )
            arr.setflags(write=False)
            object.__setattr__(self, name, arr)

    def __setstate__(self, state: dict) -> None:
        for name, value in state.items():
            object.__setattr__(self, name, value)
        self.__post_init__()

    def slice(self, start: int, end: int) -> "VolumeRuntime":
        if not (0 <= start <= end <= self.reference_length):
            raise ValueError(
                "VolumeRuntime.slice bounds must satisfy "
                f"0 <= start <= end <= {self.reference_length}; "
                f"got start={start}, end={end}"
            )

        return VolumeRuntime(
            short_median_volume=self.short_median_volume[start:end],
            baseline_median_volume=self.baseline_median_volume[start:end],
            median_relative_volume=self.median_relative_volume[start:end],
            volume_regime=self.volume_regime[start:end],
            volume_condition_allowed=self.volume_condition_allowed[start:end],
            volume_condition_block_reason=(
                self.volume_condition_block_reason[start:end]
            ),
            volume_initial_direction=self.volume_initial_direction[start:end],
            absolute_offset=self.absolute_offset + start,
            reference_length=end - start,
            filter_config_snapshot=self.filter_config_snapshot,
        )


def build_volume_global_metrics(volume, close, volume_cfg) -> VolumeRuntime:
    volume_arr = np.asarray(volume)
    close_arr = np.asarray(close, dtype=np.float64)
    if len(volume_arr) != len(close_arr):
        raise ValueError(
            f"volume and close length mismatch: {len(volume_arr)} != {len(close_arr)}"
        )

    short_window = int(volume_cfg.short_window)
    baseline_window = int(volume_cfg.baseline_window)
    threshold_ratio = float(volume_cfg.threshold_ratio)
    regime_low_ratio = float(volume_cfg.regime_low_ratio)
    regime_high_ratio = float(volume_cfg.regime_high_ratio)
    lookback_bars = int(volume_cfg.direction_lookback_bars)
    mode = volume_cfg.mode
    if mode not in ("volume_A", "volume_B"):
        raise ValueError(f"unsupported volume filter mode: {mode!r}")
    if short_window < 1 or baseline_window < 1 or lookback_bars < 1:
        raise ValueError("volume windows and direction lookback must be >= 1")

    volume_f = volume_arr.astype(np.float64, copy=False)
    short_median = (
        pd.Series(volume_f)
        .rolling(window=short_window, min_periods=short_window)
        .median()
        .to_numpy(dtype=np.float64)
    )
    baseline_median = (
        pd.Series(volume_f)
        .rolling(window=baseline_window, min_periods=baseline_window)
        .median()
        .to_numpy(dtype=np.float64)
    )

    relative = np.full(len(volume_f), np.nan, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(
            short_median,
            baseline_median,
            out=relative,
            where=baseline_median != 0,
        )

    warmup = np.isnan(short_median) | np.isnan(baseline_median)
    baseline_zero = (~np.isnan(baseline_median)) & (baseline_median == 0)

    regime = np.full(len(volume_f), REGIME_NORMAL, dtype=np.int8)
    regime[relative < regime_low_ratio] = REGIME_LOW
    regime[relative > regime_high_ratio] = REGIME_HIGH
    regime[warmup] = REGIME_WARMUP
    regime[baseline_zero] = REGIME_BASELINE_ZERO

    allowed = np.zeros(len(volume_f), dtype=bool)
    valid_decision = ~(warmup | baseline_zero | np.isnan(relative))
    if mode == "volume_A":
        allowed[valid_decision] = relative[valid_decision] >= threshold_ratio
    else:
        allowed[valid_decision] = relative[valid_decision] <= threshold_ratio

    block_reason = np.full(len(volume_f), BLOCK_NONE, dtype=np.int8)
    blocked_decision = valid_decision & ~allowed
    if mode == "volume_A":
        block_reason[blocked_decision] = BLOCK_BELOW_BASELINE
    else:
        block_reason[blocked_decision] = BLOCK_ABOVE_BASELINE
    block_reason[warmup] = BLOCK_WARMUP
    block_reason[baseline_zero] = BLOCK_BASELINE_ZERO

    direction = np.full(len(close_arr), DIR_UNKNOWN, dtype=np.int8)
    shifted = np.full(len(close_arr), np.nan, dtype=np.float64)
    shifted[lookback_bars:] = close_arr[:-lookback_bars]
    delta = close_arr - shifted
    direction[delta > 0] = DIR_LONG
    direction[delta < 0] = DIR_SHORT

    snapshot = {
        "volume_filter_enabled": True,
        "volume_filter_mode": mode,
        "volume_short_window": volume_cfg.short_window,
        "volume_baseline_window": volume_cfg.baseline_window,
        "volume_threshold_ratio": volume_cfg.threshold_ratio,
        "volume_regime_low_ratio": volume_cfg.regime_low_ratio,
        "volume_regime_high_ratio": volume_cfg.regime_high_ratio,
        "volume_direction_lookback_bars": volume_cfg.direction_lookback_bars,
    }

    return VolumeRuntime(
        short_median_volume=short_median,
        baseline_median_volume=baseline_median,
        median_relative_volume=relative,
        volume_regime=regime,
        volume_condition_allowed=allowed,
        volume_condition_block_reason=block_reason,
        volume_initial_direction=direction,
        absolute_offset=0,
        reference_length=len(volume_f),
        filter_config_snapshot=snapshot,
    )


def materialize_volume_regime(codes: np.ndarray) -> np.ndarray:
    return _materialize_codes(
        codes,
        {
            REGIME_WARMUP: "volume_warmup",
            REGIME_BASELINE_ZERO: "volume_baseline_zero",
            REGIME_LOW: "low_volume",
            REGIME_NORMAL: "normal_volume",
            REGIME_HIGH: "high_volume",
        },
        "unknown_volume_regime",
    )


def materialize_volume_block_reason(codes: np.ndarray) -> np.ndarray:
    return _materialize_codes(
        codes,
        {
            BLOCK_NONE: "none",
            BLOCK_WARMUP: "volume_warmup",
            BLOCK_BASELINE_ZERO: "volume_baseline_zero",
            BLOCK_BELOW_BASELINE: "volume_below_baseline",
            BLOCK_ABOVE_BASELINE: "volume_above_baseline",
        },
        "unknown_volume_block_reason",
    )


def materialize_volume_initial_direction(codes: np.ndarray) -> np.ndarray:
    return _materialize_codes(
        codes,
        {
            DIR_SHORT: "short",
            DIR_UNKNOWN: "unknown",
            DIR_LONG: "long",
        },
        "unknown_volume_initial_direction",
    )


def _materialize_codes(codes: np.ndarray, labels: dict[int, str], fallback: str) -> np.ndarray:
    arr = np.asarray(codes)
    out = np.empty(arr.shape, dtype=object)
    out[...] = fallback
    for code, label in labels.items():
        out[arr == code] = label
    return out


def _warn_if_volume_baseline_window_large(volume_cfg, data_length: int) -> None:
    baseline_window = getattr(volume_cfg, "baseline_window", None)
    if baseline_window is None:
        return
    if baseline_window > 0.5 * data_length:
        warnings.warn(
            "trade_filter.volume.baseline_window is greater than 50% of the "
            "validated data length; early volume metrics will spend a large "
            "share of the run in warmup",
            RuntimeWarning,
            stacklevel=2,
        )


__all__ = [
    "VolumeRuntime",
    "REGIME_WARMUP",
    "REGIME_BASELINE_ZERO",
    "REGIME_LOW",
    "REGIME_NORMAL",
    "REGIME_HIGH",
    "BLOCK_NONE",
    "BLOCK_WARMUP",
    "BLOCK_BASELINE_ZERO",
    "BLOCK_BELOW_BASELINE",
    "BLOCK_ABOVE_BASELINE",
    "DIR_SHORT",
    "DIR_UNKNOWN",
    "DIR_LONG",
    "build_volume_global_metrics",
    "materialize_volume_regime",
    "materialize_volume_block_reason",
    "materialize_volume_initial_direction",
]
