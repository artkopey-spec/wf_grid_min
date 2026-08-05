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

from supertrend_optimizer.data.validator import _check_no_duplicate_lowercase_columns

logger = logging.getLogger(__name__)

# Minimum number of columns expected in a valid OHLC CSV
# (datetime + open + high + low + close = 5, but index-as-datetime gives 4).
_MIN_EXPECTED_COLUMNS = 4


def _normalize_numeric_datetime_part(series: pd.Series, width: int) -> pd.Series:
    """Normalize FINAM-style numeric date/time parts after pandas int inference.

    CSV values like ``092400`` are read as ``int64`` ``92400``, and
    ``astype(str)`` then yields ``\"92400\"`` which ``pd.to_datetime`` rejects.
    Pure digit strings are zero-padded to ``width`` (8 for YYYYMMDD, 6 for HHMMSS).
    Non-numeric formats (``2024-03-15``, ``14:30:00``) are left unchanged.
    """
    s = series.astype(str).str.strip()
    # Float artifacts from numeric CSV inference: ``92400.0`` → ``92400``
    s = s.str.replace(r"\.0$", "", regex=True)
    numeric = s.str.fullmatch(r"\d+")
    return s.where(~numeric.fillna(False), s.str.zfill(width))


def load_ohlc_csv(
    path: str,
    tz: Optional[str] = None,
    sep: Optional[str] = None,
    date_format: Optional[str] = None,
    *,
    duplicate_lowercase_mode: str = "info",
) -> pd.DataFrame:
    """
    Load OHLC data from a CSV file.

    Supports both daily and intraday data (hours/minutes/seconds).

    Datetime column detection (after lowercasing all column names):
    1. If both 'date' and 'time' columns are present, they are combined
       into a single datetime index: ``date + ' ' + time``.
    2. Otherwise the first matching column from
       ['datetime', 'date', 'time', 'timestamp'] is used.
    3. A leading unnamed, non-numeric column is accepted as the serialized
       DatetimeIndex produced by ``DataFrame.to_csv(index=True)``.
    4. If none of the above are found, an existing DatetimeIndex is preserved.
    5. If the result is still not a DatetimeIndex, a ValueError is raised —
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
        duplicate_lowercase_mode: Collision policy for source columns which
            become identical after lowercase normalization. ``"info"`` keeps
            the tolerant TESTER behavior; ``"raise"`` is used by strict
            pipelines to reject ambiguous input before normalization.

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

    _check_no_duplicate_lowercase_columns(
        list(df.columns), path, mode=duplicate_lowercase_mode
    )

    # Normalise column names: lowercase and strip angle-bracket wrappers
    # (e.g. "<DATE>;<TIME>;<OPEN>..." exports from trading terminals).
    df.columns = df.columns.str.lower().str.strip("<>")

    # ── Build DatetimeIndex ───────────────────────────────────────────────────
    cols = set(df.columns)

    if "date" in cols and "time" in cols:
        # Combine separate date + time columns into a single timestamp.
        # Both columns are dropped from the DataFrame after becoming the index.
        # Zero-pad numeric FINAM parts so morning times like 09:24 survive
        # pandas int inference (092400 → 92400 → \"92400\").
        date_s = _normalize_numeric_datetime_part(df["date"], width=8)
        time_s = _normalize_numeric_datetime_part(df["time"], width=6)
        combined = date_s + " " + time_s
        df = df.drop(columns=["date", "time"])
        df.index = pd.to_datetime(combined, format=date_format, errors="raise")
        df.index.name = "datetime"
    else:
        # Single datetime column: first match wins.
        _DATETIME_COLS = ["datetime", "date", "time", "timestamp"]
        matched_col = next((c for c in _DATETIME_COLS if c in cols), None)
        if (
            matched_col is None
            and len(df.columns) > 0
            and str(df.columns[0]).startswith("unnamed:")
            and not pd.api.types.is_numeric_dtype(df.iloc[:, 0])
        ):
            # pandas writes an unnamed header for a nameless DatetimeIndex.
            # Accept only a non-numeric leading column so an accidental row
            # counter cannot silently become nanoseconds since epoch.
            matched_col = df.columns[0]

        if matched_col is not None:
            df[matched_col] = pd.to_datetime(
                df[matched_col], format=date_format, errors="raise"
            )
            df = df.set_index(matched_col)
        else:
            # pd.read_csv normally creates a RangeIndex.  Converting that
            # integer index with pd.to_datetime would silently produce epoch
            # nanoseconds, so only an index which is already datetime is valid.
            if not isinstance(df.index, pd.DatetimeIndex):
                available = list(df.columns)
                raise ValueError(
                    f"Cannot build a DatetimeIndex from '{path}'. "
                    f"None of {_DATETIME_COLS} or a serialized unnamed index "
                    f"was found in columns {available}."
                )

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
