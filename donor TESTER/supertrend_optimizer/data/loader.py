"""
CSV data loading module.

This module handles loading OHLC data from CSV files.
"""

from typing import Optional
import pandas as pd


def load_ohlc_csv(path: str, tz: Optional[str] = None) -> pd.DataFrame:
    """
    Load OHLC data from CSV file.
    
    Supports both daily and intraday data (hours/minutes/seconds).
    
    Args:
        path: Path to the CSV file
        tz: Optional timezone for localization/conversion (e.g., 'UTC', 'America/New_York').
            If None, timestamps remain timezone-naive.
            If provided, naive timestamps are localized; aware timestamps are converted.
        
    Returns:
        DataFrame with OHLC data (columns: open, high, low, close in lowercase)
        Index is DatetimeIndex (timezone-naive or aware depending on tz parameter)
        
    Raises:
        FileNotFoundError: If CSV file does not exist
        ValueError: If required columns are missing or timezone is invalid
        pd.errors.ParserError: If CSV parsing fails
    """
    # Read CSV with automatic delimiter detection
    df = pd.read_csv(path, sep=None, engine="python")
    
    # Convert column names to lowercase for consistency
    df.columns = df.columns.str.lower()
    
    # Try to parse datetime column and set as index
    datetime_columns = ['datetime', 'date', 'time', 'timestamp']
    for col in datetime_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
            df = df.set_index(col)
            break
    
    # If index is already datetime, ensure it's DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        # Try to convert index to datetime if possible
        try:
            df.index = pd.to_datetime(df.index)
        except (ValueError, TypeError):
            # If conversion fails, keep original index
            pass
    
    # Handle timezone if requested
    if tz is not None and isinstance(df.index, pd.DatetimeIndex):
        if df.index.tz is None:
            # Naive timestamps: localize to specified timezone
            df.index = df.index.tz_localize(tz)
        else:
            # Aware timestamps: convert to specified timezone
            df.index = df.index.tz_convert(tz)
    
    # Sort by index (time) in ascending order
    df = df.sort_index()
    
    # Check for required columns
    required_columns = ['open', 'high', 'low', 'close']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")
    
    # Convert OHLC columns to float64
    for col in required_columns:
        df[col] = df[col].astype('float64')
    
    return df

