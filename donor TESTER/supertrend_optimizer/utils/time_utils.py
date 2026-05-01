"""
Time utilities for warmup-in-time conversion.

Provides functions to convert time-based warmup (e.g., "7d", "48h", "180m") 
to bar-based warmup using median timestamp delta.
"""

import re
from dataclasses import dataclass
from typing import Union, Optional
import pandas as pd
import numpy as np


def parse_time_string(time_str: str) -> pd.Timedelta:
    """
    Parse time string to pandas Timedelta.
    
    Supported formats:
    - "7d" or "7D" → 7 days
    - "48h" or "48H" → 48 hours
    - "180m" or "180M" → 180 minutes
    - "3600s" or "3600S" → 3600 seconds
    
    Args:
        time_str: Time string (e.g., "7d", "48h", "180m")
        
    Returns:
        pd.Timedelta object
        
    Raises:
        ValueError: If format is invalid or value is non-positive
        
    Examples:
        >>> parse_time_string("7d")
        Timedelta('7 days 00:00:00')
        >>> parse_time_string("48h")
        Timedelta('2 days 00:00:00')
        >>> parse_time_string("180m")
        Timedelta('0 days 03:00:00')
    """
    if not isinstance(time_str, str):
        raise ValueError(f"time_str must be a string, got {type(time_str).__name__}")
    
    # Match pattern: number + unit (d/h/m/s)
    pattern = r'^(\d+(?:\.\d+)?)\s*([dhmsHDMS])$'
    match = re.match(pattern, time_str.strip())
    
    if not match:
        raise ValueError(
            f"Invalid time format: '{time_str}'. "
            f"Expected format: <number><unit>, where unit is d/h/m/s (e.g., '7d', '48h', '180m')"
        )
    
    value_str, unit = match.groups()
    value = float(value_str)
    
    if value <= 0:
        raise ValueError(f"Time value must be positive, got {value}")
    
    # Convert to pandas Timedelta
    unit_lower = unit.lower()
    if unit_lower == 'd':
        return pd.Timedelta(days=value)
    elif unit_lower == 'h':
        return pd.Timedelta(hours=value)
    elif unit_lower == 'm':
        return pd.Timedelta(minutes=value)
    elif unit_lower == 's':
        return pd.Timedelta(seconds=value)
    else:
        # Should never reach here due to regex
        raise ValueError(f"Unknown unit: {unit}")


def calculate_median_timedelta(index: pd.DatetimeIndex) -> pd.Timedelta:
    """
    Calculate median time delta between consecutive timestamps.
    
    Handles gaps (weekends, holidays) by using median instead of mean.
    
    Args:
        index: DatetimeIndex with at least 2 timestamps
        
    Returns:
        Median time delta as pd.Timedelta
        
    Raises:
        ValueError: If index has fewer than 2 timestamps
        TypeError: If index is not a DatetimeIndex
        
    Examples:
        >>> index = pd.date_range("2023-01-01", periods=100, freq="5min")
        >>> calculate_median_timedelta(index)
        Timedelta('0 days 00:05:00')
    """
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError(f"index must be pd.DatetimeIndex, got {type(index).__name__}")
    
    if len(index) < 2:
        raise ValueError(f"index must have at least 2 timestamps, got {len(index)}")
    
    # Calculate deltas between consecutive timestamps
    deltas = index[1:] - index[:-1]
    
    # Return median delta
    median_delta = deltas.median()
    
    return median_delta


def convert_time_to_bars(
    time_str: str,
    index: pd.DatetimeIndex
) -> int:
    """
    Convert time-based warmup to bar-based warmup.
    
    Uses median timestamp delta to estimate bars per time unit.
    
    Args:
        time_str: Time string (e.g., "7d", "48h", "180m")
        index: DatetimeIndex to calculate median delta from
        
    Returns:
        Number of bars (rounded up to nearest integer, minimum 1)
        
    Raises:
        ValueError: If time_str is invalid or index has < 2 timestamps
        TypeError: If index is not a DatetimeIndex
        
    Examples:
        >>> index = pd.date_range("2023-01-01", periods=100, freq="5min")
        >>> convert_time_to_bars("1h", index)
        12  # 1 hour / 5 min = 12 bars
        >>> convert_time_to_bars("1d", index)
        288  # 24 hours / 5 min = 288 bars
    """
    # Parse time string to Timedelta
    time_delta = parse_time_string(time_str)
    
    # Calculate median delta between bars
    median_delta = calculate_median_timedelta(index)
    
    # Convert to bars (round up)
    bars = time_delta / median_delta
    bars_int = int(np.ceil(bars))
    
    # Ensure at least 1 bar
    return max(1, bars_int)


def resolve_warmup_bars(
    warmup_period: Union[int, None],
    warmup_time: Union[str, None],
    index: pd.DatetimeIndex,
    atr_period: int,
    auto_warmup: bool = False
) -> int:
    """
    Resolve final warmup in bars from warmup_period or warmup_time.
    
    Priority:
    1. If warmup_period is provided (int): use it directly
    2. If warmup_time is provided (str): convert to bars
    3. If neither provided: warmup_bars = 0
    4. Apply max(warmup_bars, atr_period) if auto_warmup is True
    
    Args:
        warmup_period: Warmup in bars (explicit)
        warmup_time: Warmup in time (e.g., "7d", "48h")
        index: DatetimeIndex for time-to-bars conversion
        atr_period: ATR period (used if auto_warmup=True)
        auto_warmup: If True, ensure warmup >= atr_period
        
    Returns:
        Final warmup in bars
        
    Raises:
        ValueError: If both warmup_period and warmup_time are provided,
                    or if warmup_time is invalid
        TypeError: If index is not a DatetimeIndex
        
    Examples:
        >>> index = pd.date_range("2023-01-01", periods=100, freq="5min")
        >>> resolve_warmup_bars(warmup_period=20, warmup_time=None, index=index, atr_period=14)
        20
        >>> resolve_warmup_bars(warmup_period=None, warmup_time="1h", index=index, atr_period=14)
        14  # max(12, 14) with auto_warmup=True
    """
    # Validate mutual exclusivity
    if warmup_period is not None and warmup_time is not None:
        raise ValueError(
            "Cannot specify both warmup_period and warmup_time. "
            "Provide only one or neither."
        )
    
    # Resolve warmup_bars
    if warmup_period is not None:
        # Explicit warmup in bars
        if not isinstance(warmup_period, int) or warmup_period < 0:
            raise ValueError(f"warmup_period must be non-negative int, got {warmup_period}")
        warmup_bars = warmup_period
    elif warmup_time is not None:
        # Convert time to bars
        warmup_bars = convert_time_to_bars(warmup_time, index)
    else:
        # No warmup specified
        warmup_bars = 0
    
    # NOTE: auto_warmup here is ONLY a safety guard: ensures warmup >= atr_period.
    # Variant A warmup (10% of n, clamped to [100, 400], + atr_period_max rule)
    # is resolved by the orchestrator via apply_auto_warmup_to_config() BEFORE
    # any backtest call.  By the time we reach this function, warmup_period
    # already holds the final Variant-A value; auto_warmup just enforces the
    # floor once more for safety.
    if auto_warmup and atr_period is not None:
        warmup_bars = max(warmup_bars, atr_period)

    return warmup_bars


# ============================================================================
# Walk-Forward Duration Parsing (added for WF validation)
# ============================================================================

def parse_duration_string(duration_str: str) -> tuple[float, str]:
    """
    Parse duration string into (value, unit) tuple.
    
    Supported formats:
    - "3y", "2Y"     → (3.0, "y") years
    - "6mo", "6MO"   → (6.0, "mo") months
    - "90d", "90D"   → (90.0, "d") days
    - "48h", "48H"   → (48.0, "h") hours
    - "180m"         → (180.0, "m") minutes
    - "3600s"        → (3600.0, "s") seconds
    - "500bars"      → (500.0, "bars") explicit bar count
    - "1bar"         → (1.0, "bars") normalized to "bars"
    
    Args:
        duration_str: Duration string
        
    Returns:
        Tuple of (value, unit)
        
    Raises:
        ValueError: If format is invalid or mo/y has fractional value
        
    Examples:
        >>> parse_duration_string("3y")
        (3.0, 'y')
        >>> parse_duration_string("6mo")
        (6.0, 'mo')
        >>> parse_duration_string("500bars")
        (500.0, 'bars')
    """
    if not isinstance(duration_str, str):
        raise ValueError(f"duration_str must be string, got {type(duration_str).__name__}")
    
    s = duration_str.strip().lower()
    
    # Pattern: number + unit (y, mo, d, h, m, s, bars/bar)
    pattern = r'^(\d+(?:\.\d+)?)\s*(y|mo|d|h|m|s|bars?)$'
    match = re.match(pattern, s)
    
    if not match:
        raise ValueError(
            f"Invalid duration format: '{duration_str}'. "
            f"Expected: Ny, Nmo, Nd, Nh, Nm, Ns, or Nbars "
            f"(e.g., '3y', '6mo', '90d', '48h', '180m', '500bars')"
        )
    
    value = float(match.group(1))
    unit = match.group(2)
    
    # Normalize "bar" to "bars"
    if unit == "bar":
        unit = "bars"
    
    # Validate: mo and y must be integers (no fractional months/years)
    if unit in ("mo", "y"):
        if value != int(value):
            raise ValueError(
                f"Fractional {unit} not supported: '{duration_str}'. "
                f"Use integer values for months (mo) and years (y)."
            )
    
    if value <= 0:
        raise ValueError(f"Duration value must be positive, got {value}")
    
    return (value, unit)


def add_duration(ts: pd.Timestamp, value: float, unit: str) -> pd.Timestamp:
    """
    Add duration to timestamp.
    
    For d/h/m/s: uses pd.Timedelta (exact)
    For mo/y: uses pd.DateOffset (calendar-aware)
    
    Args:
        ts: Base timestamp
        value: Duration value (must be int for mo/y)
        unit: Duration unit (y, mo, d, h, m, s)
        
    Returns:
        New timestamp = ts + duration
        
    Raises:
        ValueError: If unit is "bars" (not time-based) or unknown
        
    Examples:
        >>> add_duration(pd.Timestamp("2023-01-15"), 2, "y")
        Timestamp('2025-01-15 00:00:00')
        >>> add_duration(pd.Timestamp("2023-01-15"), 3, "mo")
        Timestamp('2023-04-15 00:00:00')
    """
    if unit == "bars":
        raise ValueError("Cannot add 'bars' duration to timestamp. Use bar-based logic.")
    
    if unit == "y":
        return ts + pd.DateOffset(years=int(value))
    elif unit == "mo":
        return ts + pd.DateOffset(months=int(value))
    elif unit == "d":
        return ts + pd.Timedelta(days=value)
    elif unit == "h":
        return ts + pd.Timedelta(hours=value)
    elif unit == "m":
        return ts + pd.Timedelta(minutes=value)
    elif unit == "s":
        return ts + pd.Timedelta(seconds=value)
    else:
        raise ValueError(f"Unknown unit: '{unit}'")


def sub_duration(ts: pd.Timestamp, value: float, unit: str) -> pd.Timestamp:
    """
    Subtract duration from timestamp.
    
    For d/h/m/s: uses pd.Timedelta (exact)
    For mo/y: uses pd.DateOffset (calendar-aware)
    
    Args:
        ts: Base timestamp
        value: Duration value (must be int for mo/y)
        unit: Duration unit (y, mo, d, h, m, s)
        
    Returns:
        New timestamp = ts - duration
        
    Raises:
        ValueError: If unit is "bars" (not time-based) or unknown
        
    Examples:
        >>> sub_duration(pd.Timestamp("2025-01-15"), 2, "y")
        Timestamp('2023-01-15 00:00:00')
        >>> sub_duration(pd.Timestamp("2023-04-15"), 3, "mo")
        Timestamp('2023-01-15 00:00:00')
    """
    if unit == "bars":
        raise ValueError("Cannot subtract 'bars' duration from timestamp. Use bar-based logic.")
    
    if unit == "y":
        return ts - pd.DateOffset(years=int(value))
    elif unit == "mo":
        return ts - pd.DateOffset(months=int(value))
    elif unit == "d":
        return ts - pd.Timedelta(days=value)
    elif unit == "h":
        return ts - pd.Timedelta(hours=value)
    elif unit == "m":
        return ts - pd.Timedelta(minutes=value)
    elif unit == "s":
        return ts - pd.Timedelta(seconds=value)
    else:
        raise ValueError(f"Unknown unit: '{unit}'")


def find_index_for_timestamp(
    index: pd.DatetimeIndex,
    ts: pd.Timestamp,
    side: str = "left"
) -> int:
    """
    Find index position for timestamp using searchsorted.
    
    Args:
        index: DatetimeIndex
        ts: Target timestamp
        side: "left" or "right" for searchsorted
        
    Returns:
        Index position (clamped to valid range [0, len(index)])
        
    Examples:
        >>> index = pd.date_range("2023-01-01", periods=10, freq="D")
        >>> find_index_for_timestamp(index, pd.Timestamp("2023-01-05"), "left")
        4
    """
    pos = index.searchsorted(ts, side=side)
    # Clamp to valid range
    return max(0, min(pos, len(index)))


def duration_to_bars_via_index(
    start_idx: int,
    index: pd.Index,
    duration_str: str
) -> int:
    """
    Calculate number of bars for duration starting from start_idx.
    
    For bar-based durations ("Nbars"): 
        Returns int(value) directly. No DatetimeIndex required.
    
    For time-based durations (y, mo, d, h, m, s): 
        Requires DatetimeIndex. Uses searchsorted to find end index.
    
    Args:
        start_idx: Starting index in data
        index: Data index (DatetimeIndex required for time-based)
        duration_str: Duration string (e.g., "3y", "6mo", "500bars")
        
    Returns:
        Number of bars (>= 1)
        
    Raises:
        ValueError: If time-based duration with non-DatetimeIndex,
                    or if start_idx out of range
                    
    Examples:
        >>> index = pd.date_range("2023-01-01", periods=365, freq="D")
        >>> duration_to_bars_via_index(0, index, "30d")
        30
        >>> duration_to_bars_via_index(0, index, "1mo")
        31
        >>> index_range = pd.RangeIndex(0, 1000)
        >>> duration_to_bars_via_index(0, index_range, "500bars")
        500
    """
    value, unit = parse_duration_string(duration_str)
    
    # Bar-based: direct return, no DatetimeIndex required
    if unit == "bars":
        return int(value)
    
    # Time-based: requires DatetimeIndex
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError(
            f"Time-based duration '{duration_str}' requires DatetimeIndex, "
            f"got {type(index).__name__}. Use 'Nbars' format instead."
        )
    
    if start_idx < 0 or start_idx >= len(index):
        raise ValueError(f"start_idx {start_idx} out of range [0, {len(index)})")
    
    start_ts = index[start_idx]
    end_ts = add_duration(start_ts, value, unit)
    
    # Find end index via searchsorted
    end_idx = index.searchsorted(end_ts, side="left")
    
    # Calculate bars
    bars = end_idx - start_idx
    
    # Ensure at least 1 bar
    return max(1, bars)


# ============================================================================
# Walk-Forward Window Generation
# ============================================================================

@dataclass
class WFWindowSlice:
    """Represents a single Walk-Forward window with train and test slices."""
    step_index: int
    train_start_idx: int
    train_end_idx: int      # exclusive
    test_start_idx: int
    test_end_idx: int       # exclusive
    train_start_time: Optional[pd.Timestamp] = None
    train_end_time: Optional[pd.Timestamp] = None   # inclusive (last bar)
    test_start_time: Optional[pd.Timestamp] = None
    test_end_time: Optional[pd.Timestamp] = None    # inclusive (last bar)


def make_walk_forward_slices(
    index: pd.Index,
    train_size: str,
    test_size: str,
    step_size: Optional[str] = None,
    scheme: str = "rolling",
    anchor: str = "start",
    min_train_bars: int = 500,
    min_test_bars: int = 100
) -> list[WFWindowSlice]:
    """
    Generate Walk-Forward window slices.
    
    For time-based durations (y, mo, d, h, m, s):
        Uses timestamp arithmetic + searchsorted to find boundaries.
        Requires DatetimeIndex.
    
    For bar-based durations (bars):
        Uses direct bar counting. Works with any Index.
    
    Args:
        index: Data index (DatetimeIndex for time-based durations)
        train_size: Train window size (e.g., "3y", "500bars")
        test_size: Test window size (e.g., "6mo", "100bars")
        step_size: Step size (default: test_size)
        scheme: "rolling" or "expanding"
        anchor: "start" or "end" (only "start" implemented in PR2)
        min_train_bars: Minimum bars in train window
        min_test_bars: Minimum bars in test window
        
    Returns:
        List of WFWindowSlice objects
        
    Raises:
        ValueError: If insufficient data, invalid durations, or constraints violated
        NotImplementedError: If anchor != "start" (not yet implemented)
        
    Examples:
        >>> index = pd.date_range("2020-01-01", periods=1000, freq="D")
        >>> windows = make_walk_forward_slices(
        ...     index, "500bars", "100bars", scheme="rolling"
        ... )
        >>> len(windows) > 0
        True
    """
    # Validate anchor
    if anchor not in ["start", "end"]:
        raise ValueError(f"anchor must be 'start' or 'end', got '{anchor}'")
    
    n = len(index)
    is_datetime = isinstance(index, pd.DatetimeIndex)
    
    # Parse durations
    train_value, train_unit = parse_duration_string(train_size)
    test_value, test_unit = parse_duration_string(test_size)
    
    if step_size is None:
        step_value, step_unit = test_value, test_unit
    else:
        step_value, step_unit = parse_duration_string(step_size)
    
    # Validate: time-based durations require DatetimeIndex
    for dur_name, unit in [("train_size", train_unit), ("test_size", test_unit), 
                            ("step_size", step_unit)]:
        if unit != "bars" and not is_datetime:
            raise ValueError(
                f"Time-based {dur_name} requires DatetimeIndex, "
                f"got {type(index).__name__}. Use 'Nbars' format instead."
            )
    
    # Check: all durations must be same type (bars or time-based)
    all_bars = all(u == "bars" for u in [train_unit, test_unit, step_unit])
    all_time = all(u != "bars" for u in [train_unit, test_unit, step_unit])
    
    if not (all_bars or all_time):
        raise ValueError(
            "Cannot mix bar-based and time-based durations. "
            "Use all 'Nbars' or all time-based (y/mo/d/h/m/s)."
        )
    
    # Validate scheme
    if scheme not in ["rolling", "expanding"]:
        raise ValueError(f"scheme must be 'rolling' or 'expanding', got '{scheme}'")
    
    # Generate windows based on anchor and duration type
    if anchor == "start":
        if all_bars:
            windows = _make_slices_bars_anchor_start(
                index=index,
                n=n,
                is_datetime=is_datetime,
                train_bars=int(train_value),
                test_bars=int(test_value),
                step_bars=int(step_value),
                scheme=scheme,
                min_train_bars=min_train_bars,
                min_test_bars=min_test_bars
            )
        else:
            windows = _make_slices_time_anchor_start(
                index=index,
                n=n,
                train_value=train_value, train_unit=train_unit,
                test_value=test_value, test_unit=test_unit,
                step_value=step_value, step_unit=step_unit,
                scheme=scheme,
                min_train_bars=min_train_bars,
                min_test_bars=min_test_bars
            )
    else:  # anchor == "end"
        if all_bars:
            windows = _make_slices_bars_anchor_end(
                index=index,
                n=n,
                is_datetime=is_datetime,
                train_bars=int(train_value),
                test_bars=int(test_value),
                step_bars=int(step_value),
                scheme=scheme,
                min_train_bars=min_train_bars,
                min_test_bars=min_test_bars
            )
        else:
            windows = _make_slices_time_anchor_end(
                index=index,
                n=n,
                train_value=train_value, train_unit=train_unit,
                test_value=test_value, test_unit=test_unit,
                step_value=step_value, step_unit=step_unit,
                scheme=scheme,
                min_train_bars=min_train_bars,
                min_test_bars=min_test_bars
            )
    
    if len(windows) == 0:
        raise ValueError(
            "No valid Walk-Forward windows could be generated. "
            "Check train_size, test_size, min_train_bars, min_test_bars."
        )
    
    return windows


def _make_slices_bars_anchor_start(
    index: pd.Index,
    n: int,
    is_datetime: bool,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    scheme: str,
    min_train_bars: int,
    min_test_bars: int
) -> list[WFWindowSlice]:
    """Generate bar-based slices with anchor='start'."""
    windows: list[WFWindowSlice] = []
    step_idx = 0
    
    train_start = 0
    
    while True:
        # Calculate train_end based on scheme
        if scheme == "expanding":
            train_start = 0
            train_end = train_bars + step_idx * step_bars
        else:  # rolling
            train_end = train_start + train_bars
        
        # Calculate test window
        test_start = train_end
        test_end = test_start + test_bars
        
        # Check bounds
        if test_end > n:
            break
        
        # Check minimum bars
        actual_train = train_end - train_start
        actual_test = test_end - test_start
        
        if actual_train < min_train_bars:
            if step_idx == 0:
                raise ValueError(
                    f"Insufficient data for train: {actual_train} bars < min_train_bars={min_train_bars}"
                )
            break
        
        if actual_test < min_test_bars:
            break
        
        # Create slice
        slice_obj = WFWindowSlice(
            step_index=step_idx,
            train_start_idx=train_start,
            train_end_idx=train_end,
            test_start_idx=test_start,
            test_end_idx=test_end
        )
        
        # Add timestamps if datetime index
        if is_datetime:
            slice_obj.train_start_time = index[train_start]
            slice_obj.train_end_time = index[train_end - 1]
            slice_obj.test_start_time = index[test_start]
            slice_obj.test_end_time = index[test_end - 1]
        
        windows.append(slice_obj)
        
        # Move for next iteration (rolling only)
        if scheme == "rolling":
            train_start += step_bars
        
        step_idx += 1
        
        # Safety: prevent infinite loop
        if step_idx > 10000:
            raise ValueError("Too many WF steps (>10000). Check configuration.")
    
    return windows


def _make_slices_time_anchor_start(
    index: pd.Index,
    n: int,
    train_value: float, train_unit: str,
    test_value: float, test_unit: str,
    step_value: float, step_unit: str,
    scheme: str,
    min_train_bars: int,
    min_test_bars: int
) -> list[WFWindowSlice]:
    """Generate time-based slices with anchor='start'."""
    index_dt: pd.DatetimeIndex = index  # type: ignore
    windows: list[WFWindowSlice] = []
    step_idx = 0
    
    train_start_idx = 0
    train_start_ts = index_dt[0]
    
    while True:
        # Calculate train_end based on scheme
        if scheme == "expanding":
            # Expanding: train starts at index[0], grows by step each iteration
            train_start_idx = 0
            train_start_ts = index_dt[0]
            
            # train_end_ts = index[0] + train_size + step_size * step_idx
            train_end_ts = add_duration(index_dt[0], train_value, train_unit)
            for _ in range(step_idx):
                train_end_ts = add_duration(train_end_ts, step_value, step_unit)
        else:
            # Rolling: train_end_ts = train_start_ts + train_size
            train_end_ts = add_duration(train_start_ts, train_value, train_unit)
        
        # Find train_end_idx via searchsorted
        train_end_idx = find_index_for_timestamp(index_dt, train_end_ts, "left")
        
        # Calculate test boundaries
        test_start_idx = train_end_idx
        if test_start_idx >= n:
            break
        
        test_start_ts = index_dt[test_start_idx]
        test_end_ts = add_duration(test_start_ts, test_value, test_unit)
        test_end_idx = find_index_for_timestamp(index_dt, test_end_ts, "left")
        
        # Clamp test_end to data bounds
        if test_end_idx > n:
            test_end_idx = n
        
        # Check minimum bars
        actual_train = train_end_idx - train_start_idx
        actual_test = test_end_idx - test_start_idx
        
        if actual_train < min_train_bars:
            if step_idx == 0:
                raise ValueError(
                    f"Insufficient data for train: {actual_train} bars < min_train_bars={min_train_bars}"
                )
            break
        
        if actual_test < min_test_bars:
            break
        
        # Create slice
        slice_obj = WFWindowSlice(
            step_index=step_idx,
            train_start_idx=train_start_idx,
            train_end_idx=train_end_idx,
            test_start_idx=test_start_idx,
            test_end_idx=test_end_idx,
            train_start_time=index_dt[train_start_idx],
            train_end_time=index_dt[train_end_idx - 1],
            test_start_time=index_dt[test_start_idx],
            test_end_time=index_dt[test_end_idx - 1]
        )
        
        windows.append(slice_obj)
        
        # Move for next iteration (rolling only)
        if scheme == "rolling":
            train_start_ts = add_duration(train_start_ts, step_value, step_unit)
            train_start_idx = find_index_for_timestamp(index_dt, train_start_ts, "left")
        
        step_idx += 1
        
        # Safety: prevent infinite loop
        if step_idx > 10000:
            raise ValueError("Too many WF steps (>10000). Check configuration.")
    
    return windows


def _make_slices_bars_anchor_end(
    index: pd.Index,
    n: int,
    is_datetime: bool,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    scheme: str,
    min_train_bars: int,
    min_test_bars: int
) -> list[WFWindowSlice]:
    """Generate bar-based slices with anchor='end' (backward iteration, then reverse)."""
    windows: list[WFWindowSlice] = []
    step_idx = 0
    
    test_end = n
    
    while True:
        # Calculate test_start
        test_start = test_end - test_bars
        
        # Calculate train boundaries
        train_end = test_start
        
        if scheme == "expanding":
            train_start = 0
        else:  # rolling
            train_start = train_end - train_bars
        
        # Check bounds
        if train_start < 0:
            break
        
        # Check minimum bars
        actual_train = train_end - train_start
        actual_test = test_end - test_start
        
        if actual_train < min_train_bars or actual_test < min_test_bars:
            break
        
        # Create slice
        slice_obj = WFWindowSlice(
            step_index=step_idx,
            train_start_idx=train_start,
            train_end_idx=train_end,
            test_start_idx=test_start,
            test_end_idx=test_end
        )
        
        # Add timestamps if datetime index
        if is_datetime:
            slice_obj.train_start_time = index[train_start]
            slice_obj.train_end_time = index[train_end - 1]
            slice_obj.test_start_time = index[test_start]
            slice_obj.test_end_time = index[test_end - 1]
        
        windows.append(slice_obj)
        
        # Move backward
        test_end -= step_bars
        step_idx += 1
        
        # Safety: prevent infinite loop
        if step_idx > 10000:
            raise ValueError("Too many WF steps (>10000). Check configuration.")
    
    # Reverse to chronological order and re-index
    windows.reverse()
    for i, w in enumerate(windows):
        w.step_index = i
    
    return windows


def _make_slices_time_anchor_end(
    index: pd.Index,
    n: int,
    train_value: float, train_unit: str,
    test_value: float, test_unit: str,
    step_value: float, step_unit: str,
    scheme: str,
    min_train_bars: int,
    min_test_bars: int
) -> list[WFWindowSlice]:
    """Generate time-based slices with anchor='end' (backward iteration, then reverse)."""
    index_dt: pd.DatetimeIndex = index  # type: ignore
    windows: list[WFWindowSlice] = []
    step_idx = 0
    
    test_end_idx = n
    test_end_ts = index_dt[-1]  # Last timestamp
    
    while True:
        # Calculate test_start: test_start_ts = test_end_ts - test_size
        test_start_ts = sub_duration(test_end_ts, test_value, test_unit)
        test_start_idx = find_index_for_timestamp(index_dt, test_start_ts, "left")
        
        # Calculate train boundaries
        train_end_idx = test_start_idx
        
        if train_end_idx <= 0:
            break
        
        if scheme == "expanding":
            train_start_idx = 0
        else:  # rolling
            train_end_ts = index_dt[train_end_idx - 1] if train_end_idx > 0 else index_dt[0]
            train_start_ts = sub_duration(train_end_ts, train_value, train_unit)
            train_start_idx = find_index_for_timestamp(index_dt, train_start_ts, "left")
        
        # Check bounds
        if train_start_idx < 0 or train_end_idx <= train_start_idx:
            break
        
        # Check minimum bars
        actual_train = train_end_idx - train_start_idx
        actual_test = test_end_idx - test_start_idx
        
        if actual_train < min_train_bars or actual_test < min_test_bars:
            break
        
        # Create slice
        slice_obj = WFWindowSlice(
            step_index=step_idx,
            train_start_idx=train_start_idx,
            train_end_idx=train_end_idx,
            test_start_idx=test_start_idx,
            test_end_idx=test_end_idx,
            train_start_time=index_dt[train_start_idx],
            train_end_time=index_dt[train_end_idx - 1],
            test_start_time=index_dt[test_start_idx],
            test_end_time=index_dt[test_end_idx - 1]
        )
        
        windows.append(slice_obj)
        
        # Move backward: test_end_ts -= step_size
        test_end_ts = sub_duration(test_end_ts, step_value, step_unit)
        test_end_idx = find_index_for_timestamp(index_dt, test_end_ts, "right")
        
        # Safety: ensure progress (test_end_idx must decrease)
        if test_end_idx >= len(index_dt):
            test_end_idx = len(index_dt) - 1
        
        step_idx += 1
        
        # Safety: prevent infinite loop
        if step_idx > 10000:
            raise ValueError("Too many WF steps (>10000). Check configuration.")
    
    # Reverse to chronological order and re-index
    windows.reverse()
    for i, w in enumerate(windows):
        w.step_index = i
    
    return windows

