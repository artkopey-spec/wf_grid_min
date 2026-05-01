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


def validate_filter_columns(df: pd.DataFrame, filters_cfg: dict) -> None:
    """
    Validate that the volume-MA column required by the filter config exists
    in the DataFrame.

    No-op when ``filters_cfg["mode"] == "none"``.
    For ``mode in {"volume", "volatility_and_volume"}`` the function checks
    that the configured ``volume_ma_column`` (case-insensitive) is present in
    ``df.columns``.

    New semantics:
        * Only ``volume_ma_column`` is required — the new volume filter
          computes ``ratio = volume_ma / global_mean(volume_ma)``.
        * ``volume_column`` is accepted in the config for backwards
          compatibility but NOT required (no error if missing).

    Column values are not validated here — NaN / non-positive values are
    handled fail-closed inside the engine.

    Args:
        df: DataFrame that has already been through ``validate_ohlc_data``.
        filters_cfg: Normalised filters config dict (output of
            ``_validate_filters_config``).

    Raises:
        DataValidationError: If ``volume_ma_column`` is missing from the
            DataFrame.
    """
    mode = filters_cfg.get("mode", "none")
    if mode not in ("volume", "volatility_and_volume", "amplitude_and_volume",
                    "zigzag_and_volume"):
        return

    df_columns_lower = {c.lower() for c in df.columns}

    volume_cfg = filters_cfg.get("volume", {})
    volume_ma_column = volume_cfg.get("volume_ma_column", "Volume MA")

    if not isinstance(volume_ma_column, str) or not volume_ma_column.strip():
        raise DataValidationError(
            f"Filter mode {mode!r} requires a non-empty volume_ma_column "
            f"in filters_cfg['volume'], got: {volume_ma_column!r}"
        )

    if volume_ma_column.lower() not in df_columns_lower:
        available = sorted(df.columns.tolist())
        raise DataValidationError(
            f"Filter mode {mode!r} requires volume-MA column "
            f"{volume_ma_column!r} which is missing from the CSV. "
            f"Available columns: {available}"
        )

