"""
Data loading and validation package.

Public API:
    Loader
        load_ohlc_csv           — load OHLC data from a CSV file.

    Validator
        validate_ohlc_data      — validate and optionally clean OHLC data.

    Timeframe / annualization
        TimeframeStats          — frozen dataclass with raw frequency stats.
        detect_timeframe        — compute TimeframeStats from a DatetimeIndex.
        resolve_periods_per_year          — stats + basis → float.
        resolve_periods_per_year_from_config  — master config resolution.
        resolve_annualization_basis       — explicit_basis / market → enum.
        validate_market_vs_timeframe      — sanity-check warnings list.
"""

from .loader import load_ohlc_csv
from .validator import validate_ohlc_data
from .timeframe import (
    TimeframeStats,
    detect_timeframe,
    resolve_periods_per_year,
    resolve_periods_per_year_from_config,
    resolve_annualization_basis,
    validate_market_vs_timeframe,
)

__all__ = [
    # loader
    "load_ohlc_csv",
    # validator
    "validate_ohlc_data",
    # timeframe
    "TimeframeStats",
    "detect_timeframe",
    "resolve_periods_per_year",
    "resolve_periods_per_year_from_config",
    "resolve_annualization_basis",
    "validate_market_vs_timeframe",
]
