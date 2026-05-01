"""
CSV data loading module.

This module handles loading OHLC data from CSV files.

Exception contract:
    FileNotFoundError  — CSV file does not exist.
    ValueError         — Bad data: missing/unparseable datetime column,
                         missing OHLC columns, invalid timezone, DST conflict,
                         non-DatetimeIndex after all parse attempts,
                         or suspiciously few columns after autodetect.
    pd.errors.ParserError — Low-level CSV parse failure (bad delimiter etc.).
    pytz.exceptions.AmbiguousTimeError / NonExistentTimeError — propagated
        when the caller explicitly uses a DST-sensitive timezone and naive
        timestamps collide with a clock transition. Catch these if you need
        a lenient policy (e.g. ambiguous='NaT').
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Minimum number of columns expected in a valid OHLC CSV
# (datetime + open + high + low + close = 5, but index-as-datetime gives 4).
_MIN_EXPECTED_COLUMNS = 4


def load_ohlc_csv(
    path: str,
    tz: Optional[str] = None,
    sep: Optional[str] = None,
    date_format: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load OHLC data from a CSV file.

    Supports both daily and intraday data (hours/minutes/seconds).

    Datetime column detection (after lowercasing all column names):
    1. If both 'date' and 'time' columns are present, they are combined
       into a single datetime index: ``date + ' ' + time``.
    2. Otherwise the first matching column from
       ['datetime', 'date', 'time', 'timestamp'] is used.
    3. If none of the above are found, the existing index is tried.
    4. If the result is still not a DatetimeIndex, a ValueError is raised —
       the function never silently returns a non-datetime index.

    DST policy for tz_localize:
    - ambiguous='raise' (default) — raises AmbiguousTimeError on clock-fall-back.
    - nonexistent='raise' (default) — raises NonExistentTimeError on spring-forward.
    Callers that need a lenient policy should pre-process timestamps before
    passing to this function, or handle the exception and retry with
    ambiguous='NaT' / nonexistent='shift_forward'.

    Args:
        path: Path to the CSV file.
        tz: Optional timezone for localization/conversion
            (e.g. 'UTC', 'America/New_York').
            - None  → timestamps remain as-is (naive or aware).
            - str   → naive timestamps are localized; aware timestamps
                      are converted to this timezone.
        sep: CSV column separator. None (default) enables automatic
            delimiter detection (sep=None, engine='python'). For large
            files or reliability, pass an explicit separator such as ','
            or '\\t'. Automatic detection may fail on files that use a
            semicolon separator with decimal commas (European locale).
            When None, the result is checked for a minimum number of
            columns (>= 4) and a ValueError is raised if the parsed
            DataFrame looks malformed.
        date_format: Optional strftime format string passed to
            ``pd.to_datetime`` (e.g. ``'%Y-%m-%d %H:%M:%S'``).
            When None, pandas infers the format. Providing an explicit
            format is faster and avoids day/month order ambiguity on
            international CSV files (e.g. ``'%d/%m/%Y'`` for DD/MM/YYYY).

            **Important — combined date+time columns**: when the CSV
            has separate 'date' and 'time' columns, they are concatenated
            as ``"<date> <time>"`` before parsing.  If you supply
            ``date_format``, it must match the *combined* string, not
            just the date part.  For example, if date="2024-03-15" and
            time="14:30:00", use ``date_format='%Y-%m-%d %H:%M:%S'``.

    Returns:
        DataFrame with OHLC data:
        - Columns: open, high, low, close (lowercase float64).
        - Index: DatetimeIndex sorted ascending
                 (timezone-naive or aware depending on ``tz``).

    Raises:
        FileNotFoundError: If CSV file does not exist.
        ValueError: If a DatetimeIndex cannot be constructed, required
            OHLC columns are missing, timezone is invalid, or the
            autodetected delimiter produced a malformed DataFrame.
        pd.errors.ParserError: If CSV parsing fails.
    """
    # ── Read CSV ──────────────────────────────────────────────────────────────
    if sep is None:
        df = pd.read_csv(path, sep=None, engine="python")
        # Sanity-check: if autodetect chose the wrong delimiter, the DataFrame
        # will have far fewer columns than a valid OHLC file requires.
        if df.shape[1] < _MIN_EXPECTED_COLUMNS:
            raise ValueError(
                f"After automatic delimiter detection, '{path}' has only "
                f"{df.shape[1]} column(s) (need >= {_MIN_EXPECTED_COLUMNS}). "
                f"The delimiter was likely detected incorrectly. "
                f"Pass an explicit sep= argument (e.g. sep=';' or sep=',') "
                f"to disable autodetection. Detected columns: {list(df.columns)}."
            )
    else:
        df = pd.read_csv(path, sep=sep)

    # Normalise column names to lowercase
    df.columns = df.columns.str.lower()

    # ── Build DatetimeIndex ───────────────────────────────────────────────────
    cols = set(df.columns)

    if "date" in cols and "time" in cols:
        # Combine separate date + time columns into a single timestamp.
        # Both columns are dropped from the DataFrame after becoming the index.
        combined = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
        df = df.drop(columns=["date", "time"])
        df.index = pd.to_datetime(combined, format=date_format, errors="raise")
        df.index.name = "datetime"
    else:
        # Single datetime column: first match wins.
        _DATETIME_COLS = ["datetime", "date", "time", "timestamp"]
        matched_col = next((c for c in _DATETIME_COLS if c in cols), None)

        if matched_col is not None:
            df[matched_col] = pd.to_datetime(
                df[matched_col], format=date_format, errors="raise"
            )
            df = df.set_index(matched_col)
        else:
            # No recognised column — try the existing index
            if not isinstance(df.index, pd.DatetimeIndex):
                try:
                    df.index = pd.to_datetime(
                        df.index, format=date_format, errors="raise"
                    )
                except (ValueError, TypeError) as exc:
                    available = list(df.columns)
                    raise ValueError(
                        f"Cannot build a DatetimeIndex from '{path}'. "
                        f"None of {_DATETIME_COLS} found in columns {available}, "
                        f"and the existing index is not parseable as datetime. "
                        f"Original error: {exc}"
                    ) from exc

    # Final guard: contract requires DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(
            f"Failed to produce a DatetimeIndex for '{path}'. "
            f"Index type is {type(df.index).__name__}. "
            f"Columns available: {list(df.columns)}."
        )

    # ── Timezone handling ─────────────────────────────────────────────────────
    # DST policy: ambiguous='raise', nonexistent='raise' (strict by default).
    # Callers that need lenient handling should catch AmbiguousTimeError /
    # NonExistentTimeError and retry with their preferred policy.
    if tz is not None:
        if df.index.tz is None:
            df.index = df.index.tz_localize(
                tz, ambiguous="raise", nonexistent="raise"
            )
        else:
            df.index = df.index.tz_convert(tz)

    # ── Sort ──────────────────────────────────────────────────────────────────
    if not df.index.is_monotonic_increasing:
        logger.warning(
            "DatetimeIndex from '%s' is not sorted — sorting applied. "
            "Consider fixing the source file to avoid this overhead.",
            path,
        )
    df = df.sort_index()

    # ── Validate OHLC columns ─────────────────────────────────────────────────
    required_columns = ["open", "high", "low", "close"]
    missing_columns = [c for c in required_columns if c not in df.columns]
    if missing_columns:
        raise ValueError(
            f"Missing required OHLC columns: {missing_columns}. "
            f"Available columns: {list(df.columns)}."
        )

    # ── Convert OHLC to float64 ───────────────────────────────────────────────
    for col in required_columns:
        try:
            df[col] = df[col].astype("float64")
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Column '{col}' cannot be converted to float64. "
                f"Check for non-numeric values in '{path}'. "
                f"Original error: {exc}"
            ) from exc

    return df
