"""
SuperTrend calculator module.

This module implements SuperTrend indicator calculation strictly following
TradingView formula (1:1 match required).
"""

import numpy as np


def calculate_true_range(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray
) -> np.ndarray:
    """
    Calculate True Range using vectorized NumPy operations.
    
    Formula:
    TR[0] = high[0] - low[0]
    TR[i] = max(
        high[i] - low[i],
        abs(high[i] - close[i-1]),
        abs(low[i] - close[i-1])
    )
    
    Args:
        high: High prices array
        low: Low prices array
        close: Close prices array
        
    Returns:
        True Range array
    """
    n = len(high)
    tr = np.zeros(n, dtype=np.float64)
    
    # TR[0] = high[0] - low[0]
    tr[0] = high[0] - low[0]
    
    # For i >= 1: vectorized calculation
    hl_diff = high[1:] - low[1:]
    hc_diff = np.abs(high[1:] - close[:-1])
    lc_diff = np.abs(low[1:] - close[:-1])
    
    tr[1:] = np.maximum(np.maximum(hl_diff, hc_diff), lc_diff)
    
    return tr


def calculate_atr_rma(
    tr: np.ndarray,
    period: int
) -> np.ndarray:
    """
    Calculate ATR using Wilder's RMA (Rolling Moving Average) method.
    
    Formula:
    First valid ATR:
    atr[period-1] = mean(tr[0:period])
    
    Next:
    atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    
    Initial values:
    atr[0:period-1] = atr[period-1]
    
    Args:
        tr: True Range array
        period: ATR period
        
    Returns:
        ATR array
        
    Raises:
        ValueError: If len(tr) < period
    """
    n = len(tr)
    
    # Validate input
    if n < period:
        raise ValueError(f"TR array length ({n}) must be >= period ({period})")
    
    atr = np.zeros(n, dtype=np.float64)
    
    # First valid ATR at index (period - 1)
    first_atr = np.mean(tr[0:period])
    
    # Fill initial values (0 to period-2) with first ATR
    atr[0:period] = first_atr
    
    # Calculate remaining ATR values using Wilder's RMA
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    
    return atr


def calculate_basic_bands(
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    multiplier: float
) -> tuple[np.ndarray, np.ndarray]:
    """
    Calculate basic SuperTrend bands.
    
    Formula:
    hl2 = (high + low) / 2
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr
    
    Args:
        high: High prices array
        low: Low prices array
        atr: ATR array
        multiplier: ATR multiplier
        
    Returns:
        Tuple of (upper_basic, lower_basic)
    """
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr
    
    return upper_basic, lower_basic


def calculate_final_bands(
    upper_basic: np.ndarray,
    lower_basic: np.ndarray,
    close: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """
    Calculate final SuperTrend bands using iterative logic.

    Strict 1:1 match with TradingView PineScript SuperTrend formula:

        up = nz(up[1], basicUp)
        up := close[1] > up[1] ? math.max(basicUp, up[1]) : basicUp < up[1] ? basicUp : up[1]

        dn = nz(dn[1], basicDn)
        dn := close[1] < dn[1] ? math.min(basicDn, dn[1]) : basicDn > dn[1] ? basicDn : dn[1]

    THREE-branch logic per band (i >= 1):

    Upper band:
        Branch A: close[i-1] > upper_final[i-1]
                  → upper_final[i] = max(upper_basic[i], upper_final[i-1])
                    (price broke above the band: clamp up so band cannot DROP)
        Branch B: upper_basic[i] < upper_final[i-1]   (and not A)
                  → upper_final[i] = upper_basic[i]
                    (tighter basic band replaces the old one)
        Branch C: else
                  → upper_final[i] = upper_final[i-1]
                    (keep previous band unchanged)

    Lower band (mirror):
        Branch A: close[i-1] < lower_final[i-1]
                  → lower_final[i] = min(lower_basic[i], lower_final[i-1])
                    (price broke below the band: clamp down so band cannot RISE)
        Branch B: lower_basic[i] > lower_final[i-1]   (and not A)
                  → lower_final[i] = lower_basic[i]
        Branch C: else
                  → lower_final[i] = lower_final[i-1]

    The previous (two-branch) implementation omitted the max/min clamp in
    Branch A, causing the band to incorrectly snap inward immediately after a
    breakout. This diverged from TradingView and could produce wrong trend
    switches at high-volatility reversals.

    Args:
        upper_basic: Basic upper band array
        lower_basic: Basic lower band array
        close: Close prices array

    Returns:
        Tuple of (upper_final, lower_final)
    """
    n = len(upper_basic)
    upper_final = np.zeros(n, dtype=np.float64)
    lower_final = np.zeros(n, dtype=np.float64)

    # Initialization
    upper_final[0] = upper_basic[0]
    lower_final[0] = lower_basic[0]

    # Iterative calculation for i >= 1
    for i in range(1, n):
        # --- Upper final band (three branches) ---
        if close[i - 1] > upper_final[i - 1]:
            # Branch A: price broke above → band must not drop below previous
            upper_final[i] = max(upper_basic[i], upper_final[i - 1])
        elif upper_basic[i] < upper_final[i - 1]:
            # Branch B: tighter basic band
            upper_final[i] = upper_basic[i]
        else:
            # Branch C: keep previous
            upper_final[i] = upper_final[i - 1]

        # --- Lower final band (three branches, mirror) ---
        if close[i - 1] < lower_final[i - 1]:
            # Branch A: price broke below → band must not rise above previous
            lower_final[i] = min(lower_basic[i], lower_final[i - 1])
        elif lower_basic[i] > lower_final[i - 1]:
            # Branch B: higher basic band
            lower_final[i] = lower_basic[i]
        else:
            # Branch C: keep previous
            lower_final[i] = lower_final[i - 1]

    return upper_final, lower_final


def calculate_trend_direction(
    close: np.ndarray,
    upper_final: np.ndarray,
    lower_final: np.ndarray,
    atr_period: int
) -> np.ndarray:
    """
    Calculate SuperTrend direction.
    
    Initial trend is defined at bar (atr_period - 1).
    
    Trend values:
    1 = uptrend
    -1 = downtrend
    
    Args:
        close: Close prices array
        upper_final: Final upper band array
        lower_final: Final lower band array
        atr_period: ATR period
        
    Returns:
        Trend direction array (dtype int8)
        
    Raises:
        ValueError: If atr_period < 2 or if close length < atr_period
    """
    n = len(close)
    
    # Validate inputs
    if atr_period < 2:
        raise ValueError("atr_period must be >= 2 for trend initialization logic")
    if n < atr_period:
        raise ValueError("close length must be >= atr_period")
    
    trend = np.zeros(n, dtype=np.int8)
    
    # Determine initial trend at bar (atr_period - 1)
    idx = atr_period - 1
    
    if close[idx] > upper_final[idx - 1]:
        initial_trend = 1
    elif close[idx] < lower_final[idx - 1]:
        initial_trend = -1
    else:
        # When close is between bands, use current band position
        if close[idx] > lower_final[idx]:
            initial_trend = 1
        else:
            initial_trend = -1
    
    # No trend before stabilization:
    # bars [0 .. atr_period-2] => 0 (neutral, no position)
    # bar  [atr_period-1]      => initial_trend (first defined trend)
    trend[0:atr_period - 1] = 0
    trend[atr_period - 1] = initial_trend
    
    # Calculate trend for remaining bars (i >= atr_period)
    for i in range(atr_period, n):
        if close[i] > upper_final[i - 1]:
            trend[i] = 1
        elif close[i] < lower_final[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    
    return trend


def calculate_supertrend_value(
    lower_final: np.ndarray,
    upper_final: np.ndarray,
    trend: np.ndarray
) -> np.ndarray:
    """
    Calculate SuperTrend indicator value.
    
    Formula:
    supertrend[i] = lower_final[i] if trend[i] == 1 (uptrend)
    supertrend[i] = upper_final[i] if trend[i] == -1 (downtrend)
    
    Args:
        lower_final: Final lower band array
        upper_final: Final upper band array
        trend: Trend direction array (1 or -1)
        
    Returns:
        SuperTrend values array (dtype float64)
    """
    supertrend = np.where(trend == 1, lower_final, upper_final)
    return supertrend.astype(np.float64)


def calculate_supertrend(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr_period: int,
    multiplier: float,
    precomputed_atr: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Calculate SuperTrend indicator.

    This function strictly follows TradingView SuperTrend formula.
    NO SIMPLIFICATIONS ALLOWED.

    Input validation:
        All price arrays must contain only finite, positive values.
        NaN or Inf values cause silent propagation through all downstream
        calculations.  Non-positive prices cause division-by-zero in
        returns calculation.  Both conditions raise ValueError here so
        the error is caught as early as possible.

    Args:
        high: High prices array  (all values must be finite and > 0)
        low: Low prices array    (all values must be finite and > 0)
        close: Close prices array (all values must be finite and > 0)
        atr_period: ATR period
        multiplier: ATR multiplier
        precomputed_atr: Optional pre-calculated ATR array (F-21b).
            When provided, TR and ATR calculation steps are skipped,
            saving O(n_bars × atr_period) work per trial.
            Must have the same length as high/low/close.

    Returns:
        Tuple of (trend, supertrend) where:
        - trend: Trend direction (1 = uptrend, -1 = downtrend, 0 = warmup)
        - supertrend: SuperTrend line values

    Raises:
        ValueError: If any input array contains NaN, Inf, or non-positive values.
    """
    # --- Input validation ---
    for name, arr in (("high", high), ("low", low), ("close", close)):
        if not np.all(np.isfinite(arr)):
            raise ValueError(
                f"'{name}' contains NaN or Inf values. "
                "Clean the input data before calling calculate_supertrend."
            )
        if np.any(arr <= 0):
            raise ValueError(
                f"'{name}' contains non-positive values (<= 0). "
                "All price values must be strictly positive."
            )

    if precomputed_atr is not None:
        # F-21b: skip TR and ATR computation when cache is available
        atr = precomputed_atr
    else:
        # Step 1: Calculate True Range
        tr = calculate_true_range(high, low, close)
        # Step 2: Calculate ATR using Wilder's RMA
        atr = calculate_atr_rma(tr, atr_period)
    
    # Step 3: Calculate basic bands
    upper_basic, lower_basic = calculate_basic_bands(high, low, atr, multiplier)
    
    # Step 4: Calculate final bands
    upper_final, lower_final = calculate_final_bands(upper_basic, lower_basic, close)
    
    # Step 5: Calculate trend direction
    trend = calculate_trend_direction(close, upper_final, lower_final, atr_period)
    
    # Step 6: Calculate SuperTrend value
    supertrend = calculate_supertrend_value(lower_final, upper_final, trend)
    
    return trend, supertrend

