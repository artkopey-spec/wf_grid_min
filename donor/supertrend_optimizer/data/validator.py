"""
Data validation module.

This module handles validation of OHLC data.

Exception contract:
    DataValidationError — data integrity failure (missing columns,
        bad values, OHLC relationship violations).
        In strict mode also raised for unsorted index and duplicate
        timestamps.

Strict mode vs default mode:
    Default (strict=False):
    - Unsorted index → sort silently + WARNING log.
    - Duplicate timestamps → deduplicate (keep last) + WARNING log.

    Strict (strict=True):
    - Non-DatetimeIndex → DataValidationError immediately.
      Sort order and duplicate checks require DatetimeIndex; a non-
      DatetimeIndex in strict mode means the caller cannot guarantee
      input quality, so the pipeline must not proceed.
    - Unsorted index → DataValidationError immediately.
    - Duplicate timestamps → DataValidationError immediately.
    Use strict=True in production pipelines where input data quality
    must be guaranteed by the upstream feed, not silently fixed here.
"""

import logging
from collections import defaultdict
from typing import Optional

import numpy as np
import pandas as pd

from supertrend_optimizer.utils.exceptions import DataValidationError
from supertrend_optimizer.core.trade_filter_config import is_volume_enabled

logger = logging.getLogger(__name__)

# Maximum number of duplicate timestamps to show in the warning log.
_MAX_DUPE_SAMPLES = 5


def _check_no_duplicate_lowercase_columns(
    columns,
    source,
    *,
    mode: str,
) -> None:
    """Detect columns that collide after lowercase normalization."""
    if mode not in {"raise", "info"}:
        raise ValueError(f"mode must be 'raise' or 'info', got {mode!r}")

    groups: dict[str, list[object]] = defaultdict(list)
    for col in columns:
        groups[str(col).lower()].append(col)

    collisions = [
        originals for originals in groups.values()
        if len(set(map(str, originals))) > 1
    ]
    if not collisions:
        return

    rendered = ", ".join(str(group) for group in collisions)
    message = (
        f"duplicate lowercase columns in {source}: {rendered}. "
        "Rename colliding columns before lowercase normalization."
    )
    if mode == "raise":
        raise DataValidationError(message)
    logger.info(message)


def validate_ohlc_data(
    df: pd.DataFrame,
    config: Optional[dict] = None,
    strict: bool = False,
) -> pd.DataFrame:
    """
    Validate and optionally clean OHLC data.

    Supports both daily and intraday data (hours/minutes/seconds).

    Checks performed (in order):
    0. DataFrame is not empty.
    1. Required columns present: open, high, low, close.
    2. All OHLC columns have numeric dtype.
    3. No NaN in any OHLC column.
    4. No inf in any OHLC column.
    5. All OHLC values > 0 (non-positive prices are invalid).
    6. high >= low (explicit invariant).
    7. high >= max(open, close).
    8. low  <= min(open, close).
    9. If DatetimeIndex and not monotonically increasing:
       - strict=False → sort + WARNING.
       - strict=True  → DataValidationError.
    10. Duplicate timestamps:
       - strict=False → keep last + WARNING with count and sample timestamps.
       - strict=True  → DataValidationError with count.

    Note on deduplication (strict=False):
        keep='last' means the last row *in current sort order* is retained.
        After step 9 the index is sorted ascending, so 'last' is the
        chronologically last duplicate in the dataset. If the original data
        was unsorted the retained row depends on pandas sort stability
        (mergesort). Log output includes sample timestamps to aid diagnosis.

    Args:
        df: DataFrame with OHLC data (DatetimeIndex recommended).
        config: Reserved for future use (e.g. allow_zero_prices for
            synthetic datasets, instrument-specific overrides).
            Currently unused; do not rely on it having any effect.
        strict: If True, raise DataValidationError instead of silently
            fixing sort order or duplicate timestamps.

    Returns:
        Validated and cleaned DataFrame (copy of input).

    Raises:
        DataValidationError: If any validation check fails.
    """
    # Work on a copy to avoid mutating the caller's DataFrame
    df = df.copy()

    # ── 0. Empty check ────────────────────────────────────────────────────────
    if df.empty:
        raise DataValidationError(
            "DataFrame is empty — cannot validate OHLC data. "
            "Ensure the input data contains at least one row."
        )

    # ── 1. Required columns ───────────────────────────────────────────────────
    required_columns = ["open", "high", "low", "close"]
    missing_columns = [c for c in required_columns if c not in df.columns]
    if missing_columns:
        hint = ""
        upper_matches = [c for c in required_columns if c.upper() in df.columns or c.capitalize() in df.columns]
        if upper_matches:
            hint = (
                f" Column names are case-sensitive; found similar columns with different "
                f"casing: {[c for c in df.columns if c.lower() in required_columns]}. "
                f"Rename to lowercase 'open', 'high', 'low', 'close'."
            )
        raise DataValidationError(
            f"Missing required columns: {missing_columns}. "
            f"Available columns: {list(df.columns)}.{hint}"
        )

    # ── 2. Numeric dtype check ────────────────────────────────────────────────
    # Must be done before NaN/inf checks to prevent TypeError from numpy ufuncs
    # when the column contains strings or other non-numeric objects.
    for col in required_columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise DataValidationError(
                f"Column '{col}' has non-numeric dtype: {df[col].dtype}. "
                f"All OHLC columns must be numeric (float64 or int). "
                f"Convert the column before calling validate_ohlc_data()."
            )

    # ── 3. NaN check ──────────────────────────────────────────────────────────
    for col in required_columns:
        if df[col].isna().any():
            raise DataValidationError(f"Column '{col}' contains NaN values")

    # ── 4. Inf check ──────────────────────────────────────────────────────────
    for col in required_columns:
        if np.isinf(df[col]).any():
            raise DataValidationError(f"Column '{col}' contains inf values")

    # ── 5. Positive prices ────────────────────────────────────────────────────
    for col in required_columns:
        if (df[col] <= 0).any():
            raise DataValidationError(
                f"Column '{col}' contains non-positive values. "
                f"All OHLC prices must be > 0."
            )

    # ── 6. high >= low (explicit, for readability and direct diagnosis) ───────
    if (df["high"] < df["low"]).any():
        n_bad = int((df["high"] < df["low"]).sum())
        raise DataValidationError(
            f"high < low in {n_bad} row(s). High price must be >= low price."
        )

    # ── 7+8. OHLC envelope relationships ─────────────────────────────────────
    max_oc = df[["open", "close"]].max(axis=1)
    min_oc = df[["open", "close"]].min(axis=1)

    if (df["high"] < max_oc).any():
        raise DataValidationError("High price must be >= max(open, close)")

    if (df["low"] > min_oc).any():
        raise DataValidationError("Low price must be <= min(open, close)")

    # ── 9. Sort order ─────────────────────────────────────────────────────────
    if strict and not isinstance(df.index, pd.DatetimeIndex):
        raise DataValidationError(
            f"strict=True requires a DatetimeIndex for sort-order and duplicate "
            f"validation, but the DataFrame has index type "
            f"'{type(df.index).__name__}'. "
            f"Convert the index to DatetimeIndex before calling validate_ohlc_data() "
            f"with strict=True, or use strict=False to skip these checks."
        )

    if isinstance(df.index, pd.DatetimeIndex):
        if not df.index.is_monotonic_increasing:
            if strict:
                raise DataValidationError(
                    "DatetimeIndex is not monotonically increasing. "
                    "Sort the data before validation, or use strict=False to "
                    "allow automatic sorting."
                )
            logger.warning(
                "DatetimeIndex is not monotonically increasing — sorting applied. "
                "This may indicate a data quality issue in the upstream feed."
            )
            df = df.sort_index()

    # ── 10. Duplicate timestamps ───────────────────────────────────────────────
    if df.index.duplicated().any():
        dupe_mask = df.index.duplicated(keep=False)
        n_dupes = int(df.index.duplicated().sum())
        if strict:
            raise DataValidationError(
                f"DatetimeIndex contains {n_dupes} duplicate timestamp(s). "
                f"Remove duplicates before validation, or use strict=False to "
                f"allow automatic deduplication (keep='last')."
            )
        # Collect sample timestamps for diagnostics (unique duped timestamps only)
        dupe_timestamps = df.index[dupe_mask].unique()
        sample = list(dupe_timestamps[:_MAX_DUPE_SAMPLES])
        sample_str = ", ".join(str(ts) for ts in sample)
        if len(dupe_timestamps) > _MAX_DUPE_SAMPLES:
            sample_str += f", ... ({len(dupe_timestamps) - _MAX_DUPE_SAMPLES} more)"
        logger.warning(
            "Removing %d duplicate timestamp(s) from index (keep='last'). "
            "Duplicate timestamps are typically a data feed error — "
            "verify the source data. "
            "Affected timestamps: [%s].",
            n_dupes,
            sample_str,
        )
        df = df[~df.index.duplicated(keep="last")]

    return df


def validate_volume_filter_data(
    df: pd.DataFrame,
    trade_filter_config,
) -> pd.DataFrame:
    """Validate the volume column required by enabled volume trade filters."""
    if not is_volume_enabled(trade_filter_config):
        return df

    if "volume" not in df.columns:
        raise DataValidationError(
            "trade_filter.volume requires a 'volume' column in input data"
        )

    volume = df["volume"]
    if not pd.api.types.is_numeric_dtype(volume):
        raise DataValidationError(
            "trade_filter.volume column 'volume' must be numeric, "
            f"got dtype {volume.dtype}"
        )
    if volume.isna().any():
        raise DataValidationError(
            "trade_filter.volume column 'volume' contains NaN values"
        )
    if np.isinf(volume).any():
        raise DataValidationError(
            "trade_filter.volume column 'volume' contains inf values"
        )
    if (volume < 0).any():
        raise DataValidationError(
            "trade_filter.volume column 'volume' contains negative values"
        )

    return df
