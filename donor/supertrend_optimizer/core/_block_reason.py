"""Common trade-filter block-reason priority selection."""

from __future__ import annotations


_PRIORITY = {
    "daily_reset": 1,
    "time_filter_reset": 2,
    "time_filter_out_of_window": 3,
    "local_median_unavailable": 3,
    "volume_direction_warmup": 4,
    "volume_unknown_direction": 5,
    "volume_trade_mode_disallowed_direction": 6,
    "volume_warmup": 7,
    "volume_baseline_zero": 8,
    "volume_below_baseline": 9,
    "volume_above_baseline": 10,
    "trade_mode_disallowed_flip": 11,
    "stopping_mode_no_new_entries": 12,
    "filter_off": 13,
}


def select_block_reason(zigzag_reason: str, volume_reason: str) -> str:
    """Return the higher-priority non-``none`` block reason."""
    z = str(zigzag_reason or "none")
    v = str(volume_reason or "none")
    if z == "none":
        return v
    if v == "none":
        return z
    return z if _PRIORITY.get(z, 999) <= _PRIORITY.get(v, 999) else v


__all__ = ["select_block_reason"]
