"""
Bucket assignment utilities for BucketMatrix_Median.

Реимплементация donor'овских helpers из scoring/aggregation.py
(не существует в 3.0) с donor-parity контрактом.

Public API
----------
apply_param_buckets(df, atr_bucket_step, mult_bucket_step) -> pd.DataFrame
generate_full_bucket_grid(config) -> list[tuple[int, int]]
compute_expected_bucket_sizes(config) -> dict[tuple[int, int], int]
format_atr_range(atr_bucket, atr_step) -> str
format_mult_range(mult_bucket_ticks, mult_step) -> str
format_bucket_label(atr_bucket, mult_bucket_ticks, atr_step, mult_step) -> str
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
from wf_grid.grid.enumeration import enumerate_grid

if TYPE_CHECKING:
    from wf_grid.config.schema import GridConfig

# En-dash (U+2013) — обязательный разделитель в bucket labels.
_EN_DASH = "\u2013"


# ---------------------------------------------------------------------------
# Bucket assignment
# ---------------------------------------------------------------------------

def apply_param_buckets(
    df: pd.DataFrame,
    atr_bucket_step: int,
    mult_bucket_step: float,
) -> pd.DataFrame:
    """Add ``atr_bucket`` and ``mult_bucket_ticks`` columns to *df*.

    Formula (donor-parity):
        atr_bucket = round(atr_period / atr_bucket_step) * atr_bucket_step
        mult_bucket_ticks = round(multiplier / mult_bucket_step)

    Parameters
    ----------
    df:
        DataFrame with ``atr_period`` (int) and ``multiplier`` (float) columns.
    atr_bucket_step:
        ATR bucket width (positive integer).
    mult_bucket_step:
        Multiplier bucket width (positive float).

    Returns
    -------
    pd.DataFrame
        Copy of *df* with ``atr_bucket`` and ``mult_bucket_ticks`` columns added
        (integer types).  Original DataFrame is not mutated.
    """
    result = df.copy()
    result["atr_bucket"] = (
        (result["atr_period"] / atr_bucket_step).round().astype(int) * atr_bucket_step
    )
    result["mult_bucket_ticks"] = (
        (result["multiplier"] / mult_bucket_step).round().astype(int)
    )
    return result


# ---------------------------------------------------------------------------
# Full bucket grid generation
# ---------------------------------------------------------------------------

def generate_full_bucket_grid(config: "GridConfig") -> list[tuple[int, int]]:
    """Return every (atr_bucket, mult_bucket_ticks) implied by config ranges.

    Iterates every discrete (atr_period, multiplier) point in the search space
    using the same bucket formula as apply_param_buckets — zero drift (DR-1).

    Multipliers are enumerated via integer ticks (like enumerate_grid) to avoid
    float accumulation drift.

    Returns
    -------
    list of (atr_bucket, mult_bucket_ticks) tuples, sorted:
        atr_bucket ASC, then mult_bucket_ticks ASC.
    Empty list when config ranges are degenerate.
    """
    opt = config.optimization
    bk = config.bucket

    atr_min = int(opt.atr_period_range[0])
    atr_max = int(opt.atr_period_range[1])
    mult_min = float(opt.multiplier_range[0])
    mult_max = float(opt.multiplier_range[1])
    mult_step = float(opt.multiplier_step)
    atr_bucket_step = int(bk.atr_bucket_step)
    mult_bucket_step = float(bk.mult_bucket_step)

    if atr_min > atr_max or mult_min > mult_max or mult_step <= 0:
        return []

    # Enumerate all multiplier tick values (same method as enumerate_grid)
    tick_min = round(mult_min / mult_step)
    tick_max = round(mult_max / mult_step)

    seen: set[tuple[int, int]] = set()
    result: list[tuple[int, int]] = []

    for atr in range(atr_min, atr_max + 1):
        for tick in range(tick_min, tick_max + 1):
            canonical_mult = tick * mult_step
            if canonical_mult > mult_max + 1e-9:
                break
            ab = round(atr / atr_bucket_step) * atr_bucket_step
            mt = round(canonical_mult / mult_bucket_step)
            key = (int(ab), int(mt))
            if key not in seen:
                seen.add(key)
                result.append(key)

    return sorted(result)


# ---------------------------------------------------------------------------
# Expected bucket sizes
# ---------------------------------------------------------------------------

def compute_expected_bucket_sizes(
    config: "GridConfig",
) -> dict[tuple[int, int], int]:
    """Return count of physical grid points mapping to each bucket.

    Enumerates grid points by ``optimization.multiplier_step`` (grid resolution),
    groups by ``bucket.atr_bucket_step`` / ``bucket.mult_bucket_step``
    (bucket resolution).

    Invariant: sum(sizes.values()) == len(enumerate_grid(config))
    for single trade_mode configs.  Violation → ValueError.

    Returns
    -------
    dict mapping (atr_bucket, mult_bucket_ticks) -> int count.
    Empty dict when config ranges are degenerate.
    """
    opt = config.optimization
    bk = config.bucket

    atr_min = int(opt.atr_period_range[0])
    atr_max = int(opt.atr_period_range[1])
    mult_min = float(opt.multiplier_range[0])
    mult_max = float(opt.multiplier_range[1])
    mult_step = float(opt.multiplier_step)
    atr_bucket_step = int(bk.atr_bucket_step)
    mult_bucket_step = float(bk.mult_bucket_step)

    if atr_min > atr_max or mult_min > mult_max or mult_step <= 0:
        return {}

    # Enumerate by grid resolution (optimization.multiplier_step)
    tick_min = round(mult_min / mult_step)
    tick_max = round(mult_max / mult_step)

    sizes: dict[tuple[int, int], int] = {}

    for atr in range(atr_min, atr_max + 1):
        for tick in range(tick_min, tick_max + 1):
            canonical_mult = tick * mult_step
            if canonical_mult > mult_max + 1e-9:
                break
            ab = round(atr / atr_bucket_step) * atr_bucket_step
            mt = round(canonical_mult / mult_bucket_step)
            key = (int(ab), int(mt))
            sizes[key] = sizes.get(key, 0) + 1

    # Validate invariant: sum(sizes) == total grid points (single trade_mode)
    total_size = sum(sizes.values())
    grid_points = enumerate_grid(config)  # noqa: F821 — imported at module level
    trade_modes_count = len(set(p.trade_mode for p in grid_points))
    expected_single_mode = len(grid_points) // max(trade_modes_count, 1)

    if total_size != expected_single_mode:
        raise ValueError(
            f"bucket_size invariant violated: sum(bucket_sizes)={total_size} "
            f"!= expected grid points per mode={expected_single_mode}. "
            f"Check optimization and bucket config ranges."
        )

    return sizes


# ---------------------------------------------------------------------------
# Format helpers — en-dash (U+2013) as separator, .1f mult precision
# ---------------------------------------------------------------------------

def format_atr_range(atr_bucket: int, atr_step: int) -> str:
    """Format ATR bucket as human-readable range.

    Contract: ``"{atr_bucket}–{atr_bucket + atr_step}"`` with en-dash U+2013.

    Examples
    --------
    >>> format_atr_range(10, 2)
    '10–12'
    >>> format_atr_range(5, 1)
    '5–6'
    """
    return f"{atr_bucket}{_EN_DASH}{atr_bucket + atr_step}"


def format_mult_range(mult_bucket_ticks: int, mult_step: float) -> str:
    """Format multiplier bucket as human-readable range.

    Contract: ``"{mt*step:.1f}–{(mt+1)*step:.1f}"`` with en-dash U+2013,
    always 1 decimal place.

    Examples
    --------
    >>> format_mult_range(10, 0.2)
    '2.0–2.2'
    >>> format_mult_range(20, 0.5)
    '10.0–10.5'
    """
    lo = mult_bucket_ticks * mult_step
    hi = (mult_bucket_ticks + 1) * mult_step
    return f"{lo:.1f}{_EN_DASH}{hi:.1f}"


def format_bucket_label(
    atr_bucket: int,
    mult_bucket_ticks: int,
    atr_step: int,
    mult_step: float,
) -> str:
    """Format full bucket label.

    Contract: ``"ATR {atr_range}, M {mult_range}"`` with en-dash U+2013.

    Examples
    --------
    >>> format_bucket_label(10, 10, 2, 0.2)
    'ATR 10–12, M 2.0–2.2'
    """
    atr_range = format_atr_range(atr_bucket, atr_step)
    mult_range = format_mult_range(mult_bucket_ticks, mult_step)
    return f"ATR {atr_range}, M {mult_range}"
