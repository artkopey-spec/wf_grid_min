"""
Data validation module.

This module handles validation of OHLC data.
"""

from typing import Optional
import pandas as pd
import numpy as np

from supertrend_optimizer.utils.exceptions import DataValidationError


def validate_ohlc_data(df: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    """
    Validate OHLC data.
    
    Supports both daily and intraday data (hours/minutes/seconds).
    
    Args:
        df: DataFrame with OHLC data (DatetimeIndex with daily or sub-daily timestamps)
        config: Optional configuration dictionary (not used for basic validation)
        
    Returns:
        Validated and cleaned DataFrame
        
    Raises:
        DataValidationError: If data validation fails
    """
    # Create a copy to avoid mutating the input DataFrame
    df = df.copy()
    
    # Check for required columns
    required_columns = ['open', 'high', 'low', 'close']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise DataValidationError(f"Missing required columns: {missing_columns}")
    
    # Check for NaN or inf in OHLC columns
    for col in required_columns:
        if df[col].isna().any():
            raise DataValidationError(f"Column '{col}' contains NaN values")
        if np.isinf(df[col]).any():
            raise DataValidationError(f"Column '{col}' contains inf values")
    
    # Check that all prices are positive
    for col in required_columns:
        if (df[col] <= 0).any():
            raise DataValidationError(f"Column '{col}' contains non-positive values")
    
    # Check OHLC relationships: high >= max(open, close) and low <= min(open, close)
    max_oc = df[['open', 'close']].max(axis=1)
    min_oc = df[['open', 'close']].min(axis=1)
    
    if (df['high'] < max_oc).any():
        raise DataValidationError("High price must be >= max(open, close)")
    
    if (df['low'] > min_oc).any():
        raise DataValidationError("Low price must be <= min(open, close)")
    
    # If index is datetime, ensure it's monotonically increasing
    if isinstance(df.index, pd.DatetimeIndex):
        if not df.index.is_monotonic_increasing:
            df = df.sort_index()
    
    # Remove duplicates by index, keep='last'
    df = df[~df.index.duplicated(keep='last')]
    
    return df

