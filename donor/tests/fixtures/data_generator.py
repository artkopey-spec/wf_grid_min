"""
Deterministic test data generators for golden baseline tests.

These generators produce synthetic OHLC data with known properties
to lock down numeric behavior across refactorings.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta


def make_daily_ohlc(
    n_bars: int = 500,
    seed: int = 42,
    start_date: str = "2020-01-01",
    base_price: float = 100.0,
    volatility: float = 0.02,
) -> pd.DataFrame:
    """
    Generate deterministic daily OHLC data for testing.
    
    Creates realistic-looking price data with:
    - Consistent seed for reproducibility
    - Daily frequency (trading days only, Mon-Fri)
    - OHLC relationships preserved (high >= max(open, close), etc.)
    
    Args:
        n_bars: Number of bars to generate
        seed: Random seed for reproducibility
        start_date: Starting date (YYYY-MM-DD)
        base_price: Starting price level
        volatility: Daily volatility (std dev of returns)
        
    Returns:
        DataFrame with DatetimeIndex and columns: open, high, low, close
    """
    rng = np.random.RandomState(seed)
    
    # Generate trading days (Mon-Fri only)
    start = pd.Timestamp(start_date)
    dates = pd.bdate_range(start=start, periods=n_bars, freq='B')
    
    # Generate returns
    returns = rng.normal(0, volatility, n_bars)
    
    # Generate prices from returns
    close_prices = base_price * np.cumprod(1 + returns)
    
    # Generate intraday OHLC with realistic relationships
    # Open = previous close + small gap
    open_prices = np.zeros(n_bars)
    open_prices[0] = base_price
    open_prices[1:] = close_prices[:-1] * (1 + rng.normal(0, volatility * 0.3, n_bars - 1))
    
    # High/Low as max/min of open/close + some noise
    high_prices = np.maximum(open_prices, close_prices) * (1 + np.abs(rng.normal(0, volatility * 0.5, n_bars)))
    low_prices = np.minimum(open_prices, close_prices) * (1 - np.abs(rng.normal(0, volatility * 0.5, n_bars)))
    
    # Ensure OHLC relationships
    high_prices = np.maximum(high_prices, np.maximum(open_prices, close_prices))
    low_prices = np.minimum(low_prices, np.minimum(open_prices, close_prices))
    
    df = pd.DataFrame({
        'open': open_prices,
        'high': high_prices,
        'low': low_prices,
        'close': close_prices,
    }, index=dates)
    
    return df


def make_intraday_ohlc(
    n: int = 1000,
    seed: int = 42,
    start_datetime: str = "2023-01-01 09:30:00",
    freq: str = "5min",
    base_price: float = 100.0,
    volatility: float = 0.01,
) -> pd.DataFrame:
    """
    Generate deterministic intraday OHLC data for testing.
    
    Creates realistic-looking intraday price data with:
    - Consistent seed for reproducibility
    - Sub-daily frequency (e.g., 5min, 15min, 1h)
    - OHLC relationships preserved
    
    Args:
        n: Number of bars to generate
        seed: Random seed for reproducibility
        start_datetime: Starting datetime (YYYY-MM-DD HH:MM:SS)
        freq: Pandas frequency string (e.g., "5min", "15min", "1h")
        base_price: Starting price level
        volatility: Bar volatility (std dev of returns per bar)
        
    Returns:
        DataFrame with DatetimeIndex and columns: open, high, low, close
    """
    rng = np.random.RandomState(seed)
    
    # Generate datetime index
    index = pd.date_range(start=start_datetime, periods=n, freq=freq)
    
    # Generate returns
    returns = rng.normal(0, volatility, n)
    
    # Generate prices from returns
    close_prices = base_price * np.cumprod(1 + returns)
    
    # Generate intraday OHLC with realistic relationships
    # Open = previous close + small gap
    open_prices = np.zeros(n)
    open_prices[0] = base_price
    open_prices[1:] = close_prices[:-1] * (1 + rng.normal(0, volatility * 0.3, n - 1))
    
    # High/Low as max/min of open/close + some noise
    high_prices = np.maximum(open_prices, close_prices) * (1 + np.abs(rng.normal(0, volatility * 0.5, n)))
    low_prices = np.minimum(open_prices, close_prices) * (1 - np.abs(rng.normal(0, volatility * 0.5, n)))
    
    # Ensure OHLC relationships
    high_prices = np.maximum(high_prices, np.maximum(open_prices, close_prices))
    low_prices = np.minimum(low_prices, np.minimum(open_prices, close_prices))
    
    df = pd.DataFrame({
        'open': open_prices,
        'high': high_prices,
        'low': low_prices,
        'close': close_prices,
    }, index=index)
    
    return df


def make_daily_ohlc_full_calendar(
    n_bars: int = 365,
    seed: int = 43,
    start_date: str = "2020-01-01",
    base_price: float = 100.0,
    volatility: float = 0.02,
) -> pd.DataFrame:
    """
    Generate daily OHLC data for EVERY calendar day (crypto-like).
    
    Similar to make_daily_ohlc but includes weekends/holidays.
    Used for testing calendar-basis annualization.
    
    Args:
        n_bars: Number of bars to generate
        seed: Random seed for reproducibility
        start_date: Starting date (YYYY-MM-DD)
        base_price: Starting price level
        volatility: Daily volatility
        
    Returns:
        DataFrame with DatetimeIndex (all calendar days) and OHLC columns
    """
    rng = np.random.RandomState(seed)
    
    # Generate ALL calendar days (including weekends)
    start = pd.Timestamp(start_date)
    dates = pd.date_range(start=start, periods=n_bars, freq='D')
    
    # Generate returns
    returns = rng.normal(0, volatility, n_bars)
    
    # Generate prices
    close_prices = base_price * np.cumprod(1 + returns)
    
    open_prices = np.zeros(n_bars)
    open_prices[0] = base_price
    open_prices[1:] = close_prices[:-1] * (1 + rng.normal(0, volatility * 0.3, n_bars - 1))
    
    high_prices = np.maximum(open_prices, close_prices) * (1 + np.abs(rng.normal(0, volatility * 0.5, n_bars)))
    low_prices = np.minimum(open_prices, close_prices) * (1 - np.abs(rng.normal(0, volatility * 0.5, n_bars)))
    
    high_prices = np.maximum(high_prices, np.maximum(open_prices, close_prices))
    low_prices = np.minimum(low_prices, np.minimum(open_prices, close_prices))
    
    df = pd.DataFrame({
        'open': open_prices,
        'high': high_prices,
        'low': low_prices,
        'close': close_prices,
    }, index=dates)
    
    return df

