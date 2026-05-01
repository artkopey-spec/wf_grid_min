"""
Custom exceptions for SuperTrend Optimizer.

Exception contract by module
────────────────────────────
data.loader (load_ohlc_csv):
    FileNotFoundError       — CSV file not found (built-in, not subclassed).
    ValueError              — Cannot build DatetimeIndex, missing OHLC columns,
                              bad float conversion, invalid timezone.
    pd.errors.ParserError   — Low-level CSV parse failure.
    pytz AmbiguousTimeError / NonExistentTimeError — DST conflict during
                              tz_localize (propagated, not wrapped).

data.validator (validate_ohlc_data):
    DataValidationError     — Any OHLC integrity failure, or sort/duplicate
                              violations in strict mode.

data.timeframe:
    TypeError               — Pre-condition failure: wrong argument type
                              (e.g. non-DatetimeIndex passed to detect_timeframe).
    ValueError              — Pre-condition failure: empty index, NaT in index,
                              insufficient data, unknown AnnualizationBasis,
                              invalid annualization_factor value.

Rationale for using ValueError (not DataValidationError) in timeframe:
    timeframe functions are statistical utilities, not data validators.
    Their contracts are about algorithmic pre-conditions, not data quality.
    Callers that need to unify error handling should catch both ValueError
    and DataValidationError (or their common base SuperTrendOptimizerError
    for the latter).
"""


class SuperTrendOptimizerError(Exception):
    """Base exception for all SuperTrend Optimizer errors."""
    pass


class DataValidationError(SuperTrendOptimizerError):
    """Raised when OHLC data fails integrity validation."""
    pass


class ConfigError(SuperTrendOptimizerError):
    """Raised when configuration is invalid or missing."""
    pass
