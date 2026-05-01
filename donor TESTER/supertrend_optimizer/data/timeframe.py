"""
Timeframe detection and annualization logic.

This module provides functions to detect data frequency from timestamps
and compute periods_per_year based on annualization basis.

Key principle: detect_timeframe() returns RAW STATS ONLY.
Annualization is the caller's responsibility.
"""

import logging
import math
import pandas as pd
import numpy as np
from dataclasses import dataclass
from collections import defaultdict
from typing import Any, Optional, Union

from supertrend_optimizer.utils.enums import (
    MarketType,
    AnnualizationBasis,
    MARKET_DEFAULT_ANNUALIZATION,
)


@dataclass(frozen=True)
class TimeframeStats:
    """
    Raw timeframe statistics from timestamp analysis.
    
    Does NOT include periods_per_year — that's computed separately
    based on AnnualizationBasis.
    
    Fields:
        bars_per_active_day_median: Median bars per active trading day
        bars_per_calendar_day_mean: Total bars / calendar days span
        calendar_days_span: (last_date - first_date).days + 1
        active_days_count: Number of distinct dates with ≥1 bar
        total_bars: len(index)
        is_intraday: bars_per_active_day_median > 1 + eps
        inferred_from: "median" | "ratio" | "insufficient_data"
    """
    bars_per_active_day_median: float
    bars_per_calendar_day_mean: float
    calendar_days_span: int
    active_days_count: int
    total_bars: int
    is_intraday: bool
    inferred_from: str  # "median" | "ratio" | "insufficient_data"


# Threshold for detecting intraday data
_INTRADAY_THRESHOLD = 1.0 + 1e-9


def detect_timeframe(
    index: pd.DatetimeIndex,
    min_active_days_for_median: int = 3,
) -> TimeframeStats:
    """
    Compute raw timeframe statistics from a DatetimeIndex.
    
    Does NOT compute periods_per_year — that is the caller's
    responsibility based on AnnualizationBasis.
    
    Algorithm (O(n)):
    1. Extract calendar dates from index (single pass).
    2. calendar_days_span = (last_date - first_date).days + 1
    3. Count bars per date using dict (O(n)).
    4. active_days_count = len(date_counts)
    5. If active_days_count < 2:
         return TimeframeStats with inferred_from="insufficient_data",
         bars_per_* = total_bars (best-effort).
    6. If active_days_count >= min_active_days_for_median:
         bars_per_active_day_median = median(date_counts.values())
         inferred_from = "median"
       Else:
         bars_per_active_day_median = total_bars / active_days_count
         inferred_from = "ratio"
    7. bars_per_calendar_day_mean = total_bars / calendar_days_span
    8. is_intraday = bars_per_active_day_median > _INTRADAY_THRESHOLD
    
    Args:
        index: DatetimeIndex to analyze
        min_active_days_for_median: Minimum active days to use median (default: 3)
        
    Returns:
        TimeframeStats with raw statistics
        
    Raises:
        TypeError: If index is not a DatetimeIndex
        ValueError: If index is empty
    """
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError(
            f"detect_timeframe requires pd.DatetimeIndex, "
            f"got {type(index).__name__}"
        )
    
    n = len(index)
    if n == 0:
        raise ValueError("Empty DatetimeIndex")
    
    # O(n): extract dates
    first_date = index[0].date()
    last_date = index[-1].date()
    calendar_days_span = (last_date - first_date).days + 1
    
    # O(n): count bars per date (avoid pandas groupby for performance)
    date_counts = defaultdict(int)
    for ts in index:
        date_counts[ts.date()] += 1
    
    active_days_count = len(date_counts)
    total_bars = n
    
    # Insufficient data check
    if active_days_count < 2:
        return TimeframeStats(
            bars_per_active_day_median=float(n),
            bars_per_calendar_day_mean=float(n) / max(calendar_days_span, 1),
            calendar_days_span=calendar_days_span,
            active_days_count=active_days_count,
            total_bars=n,
            is_intraday=n > _INTRADAY_THRESHOLD,
            inferred_from="insufficient_data",
        )
    
    # Compute median or ratio
    counts_array = np.array(list(date_counts.values()), dtype=np.float64)
    
    if active_days_count >= min_active_days_for_median:
        bars_per_active_day_median = float(np.median(counts_array))
        inferred_from = "median"
    else:
        bars_per_active_day_median = total_bars / active_days_count
        inferred_from = "ratio"
    
    # Calendar mean (includes zero-bar days implicitly)
    bars_per_calendar_day_mean = total_bars / calendar_days_span
    
    is_intraday = bars_per_active_day_median > _INTRADAY_THRESHOLD
    
    return TimeframeStats(
        bars_per_active_day_median=bars_per_active_day_median,
        bars_per_calendar_day_mean=bars_per_calendar_day_mean,
        calendar_days_span=calendar_days_span,
        active_days_count=active_days_count,
        total_bars=total_bars,
        is_intraday=is_intraday,
        inferred_from=inferred_from,
    )


def resolve_periods_per_year(
    stats: TimeframeStats,
    basis: AnnualizationBasis,
) -> float:
    """
    Compute periods_per_year from TimeframeStats + AnnualizationBasis.
    
    Raises ValueError if stats.inferred_from == "insufficient_data".
    
    CALENDAR: bars_per_calendar_day_mean × 365.25
      - Uses mean over full calendar span (includes weekends/holidays).
      - Appropriate for 24/7 assets (crypto, forex).
      - Why 365.25: Average calendar days per year (accounting for leap years).
    
    TRADING: bars_per_active_day_median × 252
      - Uses median over active trading days only.
      - Appropriate for assets with weekends/holidays (stocks, futures).
      - Why 252: Typical trading days per year (~365 - 104 weekend - 9 holidays).
    
    Args:
        stats: TimeframeStats from detect_timeframe()
        basis: AnnualizationBasis (CALENDAR or TRADING)
        
    Returns:
        periods_per_year as float
        
    Raises:
        ValueError: If insufficient data to infer periods_per_year
    """
    if stats.inferred_from == "insufficient_data":
        raise ValueError(
            f"Not enough data to infer periods_per_year automatically "
            f"(active_days={stats.active_days_count}, need ≥2). "
            f"Set an explicit integer value for annualization_factor / "
            f"periods_per_year in config."
        )
    
    if basis == AnnualizationBasis.CALENDAR:
        return stats.bars_per_calendar_day_mean * 365.25
    elif basis == AnnualizationBasis.TRADING:
        return stats.bars_per_active_day_median * 252.0
    else:
        raise ValueError(f"Unknown AnnualizationBasis: {basis}")


def resolve_annualization_basis(
    explicit_basis: Optional[str],
    market: Optional[MarketType],
) -> AnnualizationBasis:
    """
    Resolve annualization basis from explicit config or market default.
    
    Priority:
    1. If explicit_basis is set → use it.
    2. Elif market is set → use MARKET_DEFAULT_ANNUALIZATION[market].
    3. Else → default CALENDAR.
    
    Args:
        explicit_basis: Explicit annualization_basis from config (or None)
        market: MarketType from config (or None)
        
    Returns:
        AnnualizationBasis
    """
    if explicit_basis is not None:
        return AnnualizationBasis(explicit_basis)
    if market is not None:
        return MARKET_DEFAULT_ANNUALIZATION[market]
    return AnnualizationBasis.CALENDAR


def coerce_annualization_config_value(config_value: Any) -> Union[int, str]:
    """
    Normalize ``annualization_factor`` / ``periods_per_year`` from YAML or CLI.

    Returns:
        int — explicit override (literal periods per year, skip auto-detection)
        str ``\"auto\"`` — run auto-detection in ``resolve_periods_per_year_from_config()``

    Raises:
        ValueError: if the value cannot represent an integer override or ``auto``.
    """
    if isinstance(config_value, bool):
        raise ValueError(
            "annualization_factor / periods_per_year must be an integer, "
            "'auto', or a string that parses to an integer; boolean is not allowed"
        )
    if isinstance(config_value, int):
        return config_value
    if isinstance(config_value, float):
        if not math.isfinite(config_value):
            raise ValueError(
                f"annualization_factor / periods_per_year must be finite, got: {config_value!r}"
            )
        rounded = round(config_value)
        if abs(config_value - rounded) > 1e-9:
            raise ValueError(
                f"annualization_factor / periods_per_year must be a whole number, got: {config_value!r}"
            )
        return int(rounded)
    if isinstance(config_value, str):
        s = config_value.strip().lower()
        if s == "auto":
            return "auto"
        try:
            return int(s, 10)
        except ValueError as exc:
            raise ValueError(
                "annualization_factor / periods_per_year must be 'auto' or an "
                f"integer string, got: {config_value!r}"
            ) from exc
    raise ValueError(
        "annualization_factor / periods_per_year must be int, whole-number "
        f"float, 'auto', or integer string; got {type(config_value).__name__}: {config_value!r}"
    )


def resolve_periods_per_year_from_config(
    config_value: Any,
    index: pd.DatetimeIndex,
    explicit_basis: Optional[str] = None,
    market: Optional[MarketType] = None,
) -> float:
    """
    Master resolution function for periods_per_year.
    
    Handles both explicit integer overrides and auto-detection.
    
    Logic:
    - int, whole float, or decimal string → skip auto detection, ignore
      annualization_basis, emit logging.info(), return float(value)
    - ``\"auto\"`` (str, any case after strip) →
        stats = detect_timeframe(index)
        basis = resolve_annualization_basis(explicit_basis, market)
        return resolve_periods_per_year(stats, basis)
    
    Args:
        config_value: annualization_factor / periods_per_year from config or CLI
        index: DatetimeIndex for auto-detection
        explicit_basis: Optional annualization_basis from config
        market: Optional MarketType from config
        
    Returns:
        periods_per_year as float
        
    Raises:
        ValueError: If auto-detection fails (insufficient data) or literal value is invalid
    """
    normalized = coerce_annualization_config_value(config_value)
    if isinstance(normalized, int):
        if explicit_basis is not None:
            logging.info(
                f"annualization_factor={normalized} (int) overrides "
                f"annualization_basis={explicit_basis}; basis ignored."
            )
        return float(normalized)

    stats = detect_timeframe(index)
    basis = resolve_annualization_basis(explicit_basis, market)
    return resolve_periods_per_year(stats, basis)


def validate_market_vs_timeframe(
    market: MarketType,
    stats: TimeframeStats,
) -> list[str]:
    """
    Return list of warning strings if market expectations
    conflict with observed data. Does NOT change any result.
    
    Warnings emitted ONLY if calendar_days_span ≥ 30 (sufficient data).
    
    Args:
        market: MarketType from config
        stats: TimeframeStats from detect_timeframe()
        
    Returns:
        List of warning strings (empty if no warnings)
    """
    warnings = []
    
    # Only emit warnings for datasets with ≥30 calendar days
    if stats.calendar_days_span < 30:
        return warnings
    
    active_ratio = stats.active_days_count / stats.calendar_days_span
    
    if market == MarketType.STOCKS:
        # Expect ~71% active days (5/7 for weekdays)
        if active_ratio > 0.85:
            warnings.append(
                f"market=stocks but {active_ratio:.0%} of calendar days have bars "
                f"(expected ~71% for stocks with weekends). "
                f"Consider market=crypto if data is 24/7."
            )
    
    if market == MarketType.CRYPTO:
        # Expect ~100% active days
        if active_ratio < 0.90:
            warnings.append(
                f"market=crypto but only {active_ratio:.0%} of calendar days have bars. "
                f"Crypto typically trades 365 days/year."
            )
    
    return warnings

