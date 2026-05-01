"""
Timeframe detection and annualization logic.

This module provides functions to detect data frequency from timestamps
and compute periods_per_year based on annualization basis.

Key principle: detect_timeframe() returns RAW STATS ONLY.
Annualization is the caller's responsibility.
"""

import math
import logging
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Any, Optional, Union

from supertrend_optimizer.utils.enums import (
    MarketType,
    AnnualizationBasis,
    MARKET_DEFAULT_ANNUALIZATION,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimeframeStats:
    """
    Raw timeframe statistics from timestamp analysis.

    Does NOT include periods_per_year — that's computed separately
    based on AnnualizationBasis.

    Fields:
        bars_per_active_day_median: Median (or mean when inferred_from=="ratio")
            bars per active trading day.
            - inferred_from=="median": true statistical median of per-day bar counts.
            - inferred_from=="ratio": simple mean (total_bars / active_days_count),
              used as fallback when active_days_count < min_active_days_for_median.
              The field name is intentionally kept consistent so callers need not
              branch on inferred_from, but be aware of the semantic difference.
            float('nan') when inferred_from == "insufficient_data".
        bars_per_calendar_day_mean: Total bars / calendar days span.
            float('nan') when inferred_from == "insufficient_data".
        calendar_days_span: (max_date - min_date).days + 1.
            Always >= 1. Computed from min/max of index, not first/last element,
            so result is correct regardless of index sort order.
        active_days_count: Number of distinct dates with ≥1 bar.
        total_bars: len(index)
        is_intraday: bars_per_active_day_median > 1 + eps.
            False when inferred_from == "insufficient_data".
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
    1. Count bars per date using dict (O(n)).
       Dates are derived from wall-clock time:
       - tz-aware index: normalized to midnight in its own timezone, then .date().
       - tz-naive index: .date() directly.
    2. active_days_count = len(date_counts)
    3. calendar_days_span = (max_date - min_date).days + 1
       Uses min/max of all dates — correct regardless of index sort order.
    4. If active_days_count < 2:
         return TimeframeStats with inferred_from="insufficient_data",
         bars_per_* = float('nan') (not interpretable).
    5. If active_days_count >= min_active_days_for_median:
         bars_per_active_day_median = median(date_counts.values())
         inferred_from = "median"
       Else:
         bars_per_active_day_median = total_bars / active_days_count
         inferred_from = "ratio"  (mean, not true median — see TimeframeStats docstring)
    6. bars_per_calendar_day_mean = total_bars / calendar_days_span
    7. is_intraday = bars_per_active_day_median > _INTRADAY_THRESHOLD

    Args:
        index: DatetimeIndex to analyze. May be unsorted. May be tz-aware.
        min_active_days_for_median: Minimum active days to use median (default: 3).
            Must be >= 1.

    Returns:
        TimeframeStats with raw statistics

    Raises:
        TypeError: If index is not a DatetimeIndex
        ValueError: If index is empty, contains NaT values, or
            min_active_days_for_median < 1
    """
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError(
            f"detect_timeframe requires pd.DatetimeIndex, "
            f"got {type(index).__name__}"
        )

    if min_active_days_for_median < 1:
        raise ValueError(
            f"min_active_days_for_median must be >= 1, "
            f"got {min_active_days_for_median}"
        )

    n = len(index)
    if n == 0:
        raise ValueError("Empty DatetimeIndex")

    if index.isna().any():
        raise ValueError(
            "DatetimeIndex contains NaT values. "
            "Remove or fill NaT before calling detect_timeframe()."
        )

    # O(n): count bars per wall-clock date.
    #
    # For tz-aware indexes, .date() would return the UTC date, which is wrong
    # for non-UTC timezones (e.g. America/New_York 2024-01-15 23:00 UTC ==
    # 2024-01-15 18:00 NY, but the UTC date differs from the wall-clock date
    # when crossing midnight UTC).
    #
    # Fix: normalize to midnight *in the index's own timezone* first, then
    # extract .date(). normalize() is tz-aware — it anchors to 00:00 in the
    # local (wall-clock) timezone, not UTC.
    # normalize() is tz-aware: anchors to 00:00 in the index's own timezone,
    # not UTC. Works correctly for both tz-naive and tz-aware indexes.
    local_dates = index.normalize()

    date_counts: dict = {}
    for ts in local_dates:
        d = ts.date()
        date_counts[d] = date_counts.get(d, 0) + 1

    active_days_count = len(date_counts)
    total_bars = n

    # Derive calendar span from min/max of all dates — not index[0]/index[-1].
    # This is correct even when the index is unsorted.
    all_dates = date_counts.keys()
    min_date = min(all_dates)
    max_date = max(all_dates)
    calendar_days_span = (max_date - min_date).days + 1
    if calendar_days_span < 1:
        raise ValueError(
            f"calendar_days_span={calendar_days_span} must be >= 1 "
            f"(min_date={min_date}, max_date={max_date}). "
            f"This indicates an internal logic error — please report."
        )

    # Insufficient data: return NaN for computed stats, False for is_intraday.
    if active_days_count < 2:
        return TimeframeStats(
            bars_per_active_day_median=float("nan"),
            bars_per_calendar_day_mean=float("nan"),
            calendar_days_span=calendar_days_span,
            active_days_count=active_days_count,
            total_bars=n,
            is_intraday=False,
            inferred_from="insufficient_data",
        )

    # Compute median or ratio
    counts_array = np.array(list(date_counts.values()), dtype=np.float64)

    if active_days_count >= min_active_days_for_median:
        bars_per_active_day_median = float(np.median(counts_array))
        inferred_from = "median"
    else:
        # Fallback: mean (ratio). See TimeframeStats docstring for semantics.
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
      - Why 252: Typical trading days per year for US markets
        (~365 - 104 weekend - 9 holidays). For non-US markets the actual
        number may differ (Japan ~245, India ~248, etc.). If precision matters,
        pass an explicit annualization_factor instead of relying on auto-detection.

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
        explicit_basis: Explicit annualization_basis from config (or None).
            Case-insensitive and whitespace-stripped before parsing
            (e.g. "CALENDAR", "Calendar", " trading " all accepted).
        market: MarketType from config (or None)

    Returns:
        AnnualizationBasis

    Raises:
        ValueError: If explicit_basis is not a valid AnnualizationBasis value.
    """
    if explicit_basis is not None:
        normalized = explicit_basis.strip().lower()
        try:
            return AnnualizationBasis(normalized)
        except ValueError:
            valid = [e.value for e in AnnualizationBasis]
            raise ValueError(
                f"Invalid annualization_basis: {explicit_basis!r}. "
                f"Valid values (case-insensitive): {valid}."
            )
    if market is not None:
        return MARKET_DEFAULT_ANNUALIZATION[market]
    return AnnualizationBasis.CALENDAR


def coerce_annualization_config_value(config_value: Any) -> Union[int, str]:
    """
    Normalize ``annualization_factor`` / ``periods_per_year`` for tester CLI only.

    For the general WF-grid pipeline use ``resolve_periods_per_year_from_config()``.
    This helper is used by the tester CLI to pre-validate the raw YAML/CLI value
    and return either an integer override or the literal string ``"auto"``.

    Rules:
        - bool is rejected.
        - int is accepted as-is.
        - float is accepted only if finite and a whole number.
        - str ``"auto"`` (case-insensitive, stripped) is accepted.
        - str representing an integer is accepted.
        - everything else raises ValueError.

    Returns:
        int — explicit override (literal periods per year, skip auto-detection).
        str ``"auto"`` — run auto-detection in ``resolve_periods_per_year_from_config()``.

    Raises:
        ValueError: if the value cannot represent an integer override or ``"auto"``.
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
    config_value: "int | float | str",
    index: pd.DatetimeIndex,
    explicit_basis: Optional[str] = None,
    market: Optional[MarketType] = None,
) -> float:
    """
    Master resolution function for periods_per_year.

    Handles both explicit numeric overrides and auto-detection.

    Type normalisation (applied before branching):
    - bool is rejected (YAML ``true``/``false`` would be silently wrong).
    - int or float → fixed override (e.g. YAML ``252`` or ``252.0``).
    - str numeric (``"252"``, ``"252.0"``) → converted to number, then fixed.
    - str ``"auto"`` (case-insensitive) → auto-detection branch.
    - Any other type / value → ValueError.

    Numeric validation (applied to all numeric paths):
    - Must be finite (rejects nan, inf, -inf).
    - Must be positive (rejects 0 and negative values).
    - String representations of non-finite values ("inf", "nan", "-inf")
      are rejected before int() conversion to avoid OverflowError.

    Logic:
    - numeric → skip auto detection, ignore annualization_basis,
                emit logger.info(), return float(config_value).
    - "auto"  → detect_timeframe → resolve_annualization_basis →
                resolve_periods_per_year.

    Args:
        config_value: annualization_factor / periods_per_year from config.
            Accepted: positive finite int, float, str numeric, or "auto".
        index: DatetimeIndex for auto-detection.
        explicit_basis: Optional annualization_basis from config.
        market: Optional MarketType from config.

    Returns:
        periods_per_year as float (always positive and finite).

    Raises:
        ValueError: If config_value is invalid, non-finite, non-positive,
                    or auto-detection fails.
    """
    # ── Normalise type ────────────────────────────────────────────────────────
    # bool must be checked before int because bool is a subclass of int.
    if isinstance(config_value, bool):
        raise ValueError(
            f"annualization_factor must be a number or 'auto', got bool: {config_value!r}. "
            "Check your YAML config — true/false are not valid values here."
        )

    if isinstance(config_value, str):
        stripped = config_value.strip()
        if stripped.lower() == "auto":
            config_value = "auto"
        else:
            try:
                as_float = float(stripped)
            except ValueError:
                raise ValueError(
                    f"annualization_factor string '{config_value}' is not a number "
                    f"and not 'auto'. Valid values: positive integer, float, or 'auto'."
                )
            # Reject non-finite strings ("inf", "-inf", "nan") before int()
            # to avoid OverflowError on int(float('inf')).
            if not math.isfinite(as_float):
                raise ValueError(
                    f"annualization_factor string '{config_value}' is not finite. "
                    f"Valid values: positive integer, float, or 'auto'."
                )
            # Preserve int semantics if the string represents a whole number
            config_value = int(as_float) if as_float == int(as_float) else as_float

    # ── Dispatch ──────────────────────────────────────────────────────────────
    if isinstance(config_value, (int, float)):
        value = float(config_value)
        # Validate: must be finite and positive
        if not math.isfinite(value):
            raise ValueError(
                f"annualization_factor must be a finite positive number, got: {value!r}. "
                f"NaN and inf are not valid annualization factors."
            )
        if value <= 0:
            raise ValueError(
                f"annualization_factor must be positive, got: {value!r}. "
                f"A non-positive value would produce incorrect Sharpe / annualized metrics."
            )
        if explicit_basis is not None:
            logger.info(
                "annualization_factor=%s (numeric) overrides "
                "annualization_basis=%r; basis ignored.",
                value,
                explicit_basis,
            )
        return value

    if config_value == "auto":
        # Auto detection
        stats = detect_timeframe(index)
        basis = resolve_annualization_basis(explicit_basis, market)
        return resolve_periods_per_year(stats, basis)

    raise ValueError(
        f"annualization_factor must be a number or 'auto', got: {config_value!r}"
    )


def validate_market_vs_timeframe(
    market: MarketType,
    stats: TimeframeStats,
    suppress_warnings: bool = False,
) -> list[str]:
    """
    Return list of warning strings if market expectations conflict with
    observed data. Does NOT change any result.

    Warnings emitted ONLY if:
    - calendar_days_span >= 30 (sufficient data), AND
    - suppress_warnings is False.

    Thresholds are intentionally lenient to reduce false positives on
    datasets with half-days, ADR listings, multi-session markets, or
    data gaps.

    Args:
        market: MarketType from config
        stats: TimeframeStats from detect_timeframe()
        suppress_warnings: If True, always return empty list (useful in tests
            or when caller handles diagnostics externally).

    Returns:
        List of warning strings (empty if no warnings)
    """
    if suppress_warnings:
        return []

    warnings_list = []

    # Only emit warnings for datasets with >= 30 calendar days
    if stats.calendar_days_span < 30:
        return warnings_list

    active_ratio = stats.active_days_count / stats.calendar_days_span

    if market == MarketType.STOCKS:
        # Expect ~71% active days (5/7 for weekdays).
        # Threshold 0.85 (not 0.71) to tolerate half-days, ADR,
        # extended sessions, and sparse data gaps.
        if active_ratio > 0.85:
            warnings_list.append(
                f"market=stocks but {active_ratio:.0%} of calendar days have bars "
                f"(expected ≤85% for stocks with weekends). "
                f"Consider market=crypto if data is 24/7."
            )

    if market == MarketType.CRYPTO:
        # Expect ~100% active days.
        # Threshold 0.85 (not 0.90) to tolerate exchange outages,
        # delisted periods, or data gaps without false positives.
        if active_ratio < 0.85:
            warnings_list.append(
                f"market=crypto but only {active_ratio:.0%} of calendar days have bars "
                f"(expected ≥85% for crypto with near-365-day trading). "
                f"Crypto typically trades 365 days/year."
            )

    return warnings_list
