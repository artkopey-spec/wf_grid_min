"""
Backtesting module for SuperTrend strategy.

This module implements position generation and backtesting logic.
"""

from typing import Optional

import numpy as np
from supertrend_optimizer.core.calculator import calculate_supertrend
from supertrend_optimizer.core.filters import entry_bar_for_decision
from supertrend_optimizer.utils.enums import ExecutionModel


def compute_trend_only(
    atr: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    multiplier: float,
    atr_period: int,
) -> np.ndarray:
    """
    Single Source of Truth (SSOT) for SuperTrend direction (§3.4.0 plan v2.0).

    Returns the trend array (int8, {-1, 0, +1}) using the SAME underlying
    algorithm as run_backtest_fast — by delegating to calculate_supertrend
    with precomputed_atr.  This guarantees byte-for-byte identity between
    the ZigZag branch and run_backtest_fast on the same input.

    Args:
        atr: Precomputed ATR array (required). Passed as precomputed_atr
             to calculate_supertrend — must correspond to (high, low, close,
             atr_period).
        high: High prices array.
        low: Low prices array.
        close: Close prices array.
        multiplier: ATR multiplier.
        atr_period: ATR period. Must equal the period that produced `atr`.
                    Required for calculate_trend_direction warmup logic.

    Returns:
        trend: np.ndarray of shape (N,), dtype int8, values in {-1, 0, +1}.
    """
    trend, _supertrend = calculate_supertrend(
        high=high,
        low=low,
        close=close,
        atr_period=int(atr_period),
        multiplier=multiplier,
        precomputed_atr=atr,
    )
    return trend


def generate_positions(
    trend: np.ndarray,
    trade_mode: str,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN
) -> np.ndarray:
    """
    Generate trading positions based on trend.
    
    Lag semantics depend on ExecutionModel:
    
    OPEN_TO_OPEN:
    - Signal at bar t → execute at bar t+1 open
    - 1-bar lag: positions[t+1] = trend[t]
    - positions[0] = 0 (no position on first bar)
    
    CLOSE_TO_CLOSE:
    - Signal at bar t → execute at bar t close (same bar)
    - 0-bar lag: positions[t] = trend[t]
    - positions[0] = trend[0] (position on first bar)
    
    Args:
        trend: Trend direction array (1 = uptrend, -1 = downtrend)
        trade_mode: Trading mode ("revers"/"both", "long", or "short")
        execution_model: Execution model (OPEN_TO_OPEN or CLOSE_TO_CLOSE)
        
    Returns:
        Positions array (dtype int8):
        - 0 = no position
        - 1 = long position
        - -1 = short position
        
    Raises:
        ValueError: If trade_mode is not one of: "revers", "both", "long", "short"
    """
    n = len(trend)
    positions = np.zeros(n, dtype=np.int8)
    
    # Validate trade_mode
    valid_modes = {"revers", "both", "long", "short"}
    if trade_mode not in valid_modes:
        raise ValueError(f"trade_mode must be one of {valid_modes}, got: {trade_mode}")
    
    # Apply lag and trade mode logic based on execution model
    if execution_model == ExecutionModel.OPEN_TO_OPEN:
        # 1-bar lag: positions[1:] = trend[:-1]
        # positions[0] = 0 (already initialized)
        if trade_mode in {"revers", "both"}:
            positions[1:] = trend[:-1]
        elif trade_mode == "long":
            positions[1:] = np.where(trend[:-1] == 1, 1, 0)
        elif trade_mode == "short":
            positions[1:] = np.where(trend[:-1] == -1, -1, 0)
    
    elif execution_model == ExecutionModel.CLOSE_TO_CLOSE:
        # 0-bar lag: positions[t] = trend[t]
        if trade_mode in {"revers", "both"}:
            positions[:] = trend
        elif trade_mode == "long":
            positions[:] = np.where(trend == 1, 1, 0)
        elif trade_mode == "short":
            positions[:] = np.where(trend == -1, -1, 0)
    
    else:
        raise ValueError(f"Unknown ExecutionModel: {execution_model}")
    
    return positions


def apply_entry_filters(
    positions_raw: np.ndarray,
    allow_entry: Optional[np.ndarray],
    trade_mode: str,
    execution_model: ExecutionModel,
) -> np.ndarray:
    """
    Apply per-bar entry filter mask to a raw positions array.

    This is the post-``generate_positions`` stage (ADR-1 of filters_plan_v3):
    the mask ``allow_entry`` is consulted only when opening or reversing a
    position. Closes (``target == 0``) are never blocked. Position
    continuations (``target == prev``) pass through unchanged.

    Contract (plan §4.4–§4.6):
        - ``allow_entry is None`` → return ``positions_raw`` unchanged (same
          object reference). Guarantees bit-identical regression vs. pre-filter
          code paths.
        - ``positions_final[0] == positions_raw[0]`` always (warmup exception,
          invariant 2 in §4.6).
        - Close transitions (``positions_raw[k] == 0``) never blocked.
        - Open/reverse transitions consult
          ``allow_entry[entry_bar_for_decision(k, execution_model)]``.
        - Blocked opens become ``positions_final[k] = 0`` (no phantom sign,
          invariant 4).
        - ``trade_mode == "both"`` handled identically to ``"revers"``.
        - ``prev`` is taken from ``positions_final[k-1]`` (state after prior
          filter decisions), not ``positions_raw[k-1]``. A block therefore
          allows a clean retry on the next bar.

    Args:
        positions_raw: Positions array (int8) from ``generate_positions``.
        allow_entry: Per-bar boolean mask; ``True`` at index ``d`` means an
            open whose decision bar is ``d`` may proceed. ``None`` disables
            filtering entirely and produces a bit-identical pass-through.
        trade_mode: One of ``"long" | "short" | "revers" | "both"``.
        execution_model: ``OPEN_TO_OPEN`` or ``CLOSE_TO_CLOSE``.

    Returns:
        ``positions_final`` array, dtype int8, same length as input. When
        ``allow_entry is None`` the exact same object as ``positions_raw`` is
        returned (no copy).

    Raises:
        ValueError: when array shapes disagree or ``trade_mode`` is unknown.
    """
    if allow_entry is None:
        return positions_raw

    # Validate trade_mode — duplicates generate_positions' validation by design:
    # this stage is independent and may be called standalone from tests.
    valid_modes = {"revers", "both", "long", "short"}
    if trade_mode not in valid_modes:
        raise ValueError(
            f"trade_mode must be one of {valid_modes}, got: {trade_mode!r}"
        )
    allow_entry_arr = np.asarray(allow_entry, dtype=bool)
    if allow_entry_arr.shape != positions_raw.shape:
        raise ValueError(
            f"apply_entry_filters: allow_entry shape {allow_entry_arr.shape} "
            f"!= positions_raw shape {positions_raw.shape}"
        )
    if execution_model not in (
        ExecutionModel.OPEN_TO_OPEN,
        ExecutionModel.CLOSE_TO_CLOSE,
    ):
        raise ValueError(f"Unknown ExecutionModel: {execution_model!r}")

    n = positions_raw.shape[0]
    # positions_final[0] = positions_raw[0] (warmup exception, §4.6 inv. 2)
    positions_final = positions_raw.copy()

    for k in range(1, n):
        prev = int(positions_final[k - 1])
        target = int(positions_raw[k])

        if target == prev:
            # No change: continuation of existing state (or flat stays flat).
            continue

        if target == 0:
            # Close: never blocked by filter.
            positions_final[k] = 0
            continue

        # Open or reverse: consult allow_entry at the decision bar.
        dec = entry_bar_for_decision(k, execution_model)
        if allow_entry_arr[dec]:
            positions_final[k] = target
        else:
            positions_final[k] = 0

    return positions_final


def calculate_returns(
    open_prices: np.ndarray,
    positions: np.ndarray,
    commission: float,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    close_prices: np.ndarray | None = None
) -> np.ndarray:
    """
    Calculate returns with commission costs.
    
    ExecutionModel controls both PnL calculation and implied execution price:
    
    OPEN_TO_OPEN (default):
    - Signal at bar t → execute at bar t+1 open
    - PnL basis: open[i+1] / open[i] - 1
    - positions[i] uses open[i] → open[i+1] return
    
    CLOSE_TO_CLOSE:
    - Signal at bar t → execute at bar t close (same bar)
    - PnL basis: close[i+1] / close[i] - 1
    - positions[i] uses close[i] → close[i+1] return
    
    Commission is applied on every position change:
    - 0 -> 1, 1 -> 0, 0 -> -1, -1 -> 0
    - 1 -> -1 counts as TWO operations (abs(diff) = 2)
    
    Commission cost:
    commission_costs = abs(diff(positions)) * commission
    
    Final returns:
    returns = price_ret - commission_costs
    
    **IMPORTANT**: This commission model is simplified and may not reflect
    realistic intraday trading costs. Future extensions should consider:
    - Bid-ask spread (especially for intraday)
    - Slippage modeling
    - Market impact
    - Time-of-day effects
    
    Args:
        open_prices: Open prices array
        positions: Positions array (0, 1, or -1)
        commission: Commission rate per operation
        execution_model: Execution model (OPEN_TO_OPEN or CLOSE_TO_CLOSE)
        close_prices: Close prices array (required if execution_model=CLOSE_TO_CLOSE)
        
    Returns:
        Returns array (length = len(open_prices) - 1)
        
    Raises:
        ValueError: If execution_model=CLOSE_TO_CLOSE and close_prices is None
    """
    n = len(open_prices)
    
    # Length of returns array
    returns_len = n - 1
    
    # Select price series based on execution model
    if execution_model == ExecutionModel.OPEN_TO_OPEN:
        price_series = open_prices
    elif execution_model == ExecutionModel.CLOSE_TO_CLOSE:
        if close_prices is None:
            raise ValueError(
                "close_prices must be provided when execution_model=CLOSE_TO_CLOSE"
            )
        if len(close_prices) != n:
            raise ValueError(
                f"close_prices length {len(close_prices)} != open_prices length {n}"
            )
        price_series = close_prices
    else:
        raise ValueError(f"Unknown ExecutionModel: {execution_model}")
    
    # Calculate price returns: price[i+1] / price[i] - 1
    price_changes = price_series[1:] / price_series[:-1] - 1.0
    
    # Apply positions (using first returns_len positions)
    price_ret = price_changes * positions[:returns_len]
    
    # Calculate position changes (diff)
    # diff(positions) has length = len(positions) - 1
    # We need first returns_len changes
    position_changes = np.diff(positions[:returns_len + 1])
    
    # Commission costs: abs(diff) * commission
    commission_costs = np.abs(position_changes) * commission
    
    # Final returns
    returns = price_ret - commission_costs
    
    return returns


def calculate_equity_curve(
    returns: np.ndarray
) -> np.ndarray:
    """
    Calculate equity curve from returns.
    
    Equity curve starts at 1.0 and compounds returns:
    equity[0] = 1.0
    equity[i] = equity[i-1] * (1 + returns[i-1])
    
    Args:
        returns: Returns array
        
    Returns:
        Equity curve array (length = len(returns) + 1)
    """
    n = len(returns)
    equity = np.zeros(n + 1, dtype=np.float64)
    
    # Initial equity
    equity[0] = 1.0
    
    # Cumulative product: equity[1:] = cumprod(1 + returns)
    equity[1:] = np.cumprod(1.0 + returns)
    
    return equity


def check_early_exit(
    equity_curve: np.ndarray,
    max_drawdown: float,
    check_bars: int
) -> tuple[bool, int | None, float | None]:
    """
    Check for early exit condition based on drawdown.
    
    Early exit is triggered if drawdown exceeds threshold
    within the first check_bars bars.
    
    Drawdown at bar i:
    dd[i] = equity[i] / max(equity[0:i+1]) - 1
    
    Args:
        equity_curve: Equity curve array
        max_drawdown: Maximum allowed drawdown (positive value, e.g., 0.5 for -50%)
        check_bars: Number of bars to check
        
    Returns:
        Tuple of (is_early_exit, exit_bar, exit_drawdown):
        - is_early_exit: True if early exit triggered
        - exit_bar: Bar index where early exit occurred (or None)
        - exit_drawdown: Drawdown value at exit bar (or None)
    """
    n = len(equity_curve)
    
    # Limit check to available bars
    bars_to_check = min(check_bars, n)
    
    # Calculate running maximum using cumulative maximum
    equity_slice = equity_curve[:bars_to_check]
    running_max = np.maximum.accumulate(equity_slice)
    
    # Calculate drawdown: equity[i] / running_max[i] - 1
    drawdown = equity_slice / running_max - 1.0
    
    # Check if any drawdown exceeds threshold
    # Condition: dd[i] < -max_drawdown
    early_exit_mask = drawdown < -max_drawdown
    
    if np.any(early_exit_mask):
        # Find first occurrence
        exit_bar = np.argmax(early_exit_mask)
        exit_drawdown = drawdown[exit_bar]
        return (True, int(exit_bar), float(exit_drawdown))
    else:
        return (False, None, None)


def run_backtest_fast(
    open_prices: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr_period: int,
    multiplier: float,
    trade_mode: str,
    commission: float,
    early_exit_enabled: bool,
    early_exit_max_drawdown: float,
    early_exit_check_bars: int,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    precomputed_atr: "np.ndarray | None" = None,
    allow_entry: Optional[np.ndarray] = None,
) -> tuple[
    np.ndarray,      # returns
    np.ndarray,      # equity_curve
    np.ndarray,      # trend
    np.ndarray,      # positions
    bool,            # early_exit
    int | None,      # exit_bar
    float | None     # exit_drawdown
]:
    """
    Run fast backtest without DataFrame overhead.
    
    This function combines all backtest steps:
    1. Calculate SuperTrend indicator
    2. Generate positions based on trend and trade mode
    3. Calculate returns with commission
    4. Calculate equity curve
    5. Check for early exit (if enabled)
    
    **IMPORTANT: Early Exit Behavior**
    
    If early_exit is triggered:
    - All returned arrays are TRUNCATED to the exit point
    - returns: length = exit_bar
    - equity_curve: length = exit_bar + 1
    - positions: length = exit_bar + 1
    - trend: length = exit_bar + 1
    
    If early_exit is NOT triggered (or disabled):
    - returns: length = len(open_prices) - 1
    - equity_curve: length = len(open_prices)
    - positions: length = len(open_prices)
    - trend: length = len(open_prices)
    
    This ensures all downstream calculations (metrics, trades, exports)
    automatically reflect only data up to the early exit point.
    
    Args:
        open_prices: Open prices array
        high: High prices array
        low: Low prices array
        close: Close prices array
        atr_period: ATR period
        multiplier: ATR multiplier
        trade_mode: Trading mode ("revers"/"both", "long", or "short")
        commission: Commission rate per operation
        early_exit_enabled: Whether to check for early exit
        early_exit_max_drawdown: Maximum allowed drawdown for early exit
        early_exit_check_bars: Number of bars to check for early exit
        execution_model: Execution model (OPEN_TO_OPEN or CLOSE_TO_CLOSE)
        
    Returns:
        Tuple of (returns, equity_curve, trend, positions, early_exit, exit_bar, exit_drawdown)
        Note: Array lengths depend on early_exit status (see above)
    """
    # Step 1: SSOT trend (plan §3.4.0) — delegate to compute_trend_only so that
    # this path and the ZigZag branch are architecturally unified.
    if precomputed_atr is not None:
        _atr_for_trend = precomputed_atr
    else:
        from supertrend_optimizer.core.calculator import calculate_atr_rma, calculate_true_range
        _atr_for_trend = calculate_atr_rma(calculate_true_range(high, low, close), atr_period)
    trend = compute_trend_only(
        atr=_atr_for_trend,
        high=high,
        low=low,
        close=close,
        multiplier=multiplier,
        atr_period=atr_period,
    )
    
    # Step 2: Generate positions (lag semantics depend on execution_model)
    positions = generate_positions(trend, trade_mode, execution_model)

    # Step 2b: Apply entry filters (ADR-1). When allow_entry is None this is
    # a zero-cost pass-through preserving bit-identical legacy behaviour.
    positions = apply_entry_filters(
        positions, allow_entry, trade_mode, execution_model
    )

    # Step 3: Calculate returns (price series depends on execution_model)
    returns = calculate_returns(
        open_prices,
        positions,
        commission,
        execution_model=execution_model,
        close_prices=close
    )
    
    # Step 4: Calculate equity curve
    equity_curve = calculate_equity_curve(returns)
    
    # Step 5: Check for early exit
    if early_exit_enabled:
        early_exit, exit_bar, exit_dd = check_early_exit(
            equity_curve,
            early_exit_max_drawdown,
            early_exit_check_bars
        )
        
        # Step 5a: Truncate arrays if early exit triggered
        if early_exit:
            # exit_bar is index in equity_curve where exit occurred
            # Truncate all arrays to exit point
            if exit_bar is None:
                raise ValueError("exit_bar must be set when early_exit=True")
            if not (0 <= exit_bar < len(equity_curve)):
                raise ValueError(
                    f"exit_bar {exit_bar} out of range [0, {len(equity_curve)})"
                )
            
            returns = returns[:exit_bar]
            equity_curve = equity_curve[:exit_bar + 1]  # +1 because equity has len(returns)+1
            positions = positions[:exit_bar + 1]
            trend = trend[:exit_bar + 1]
            
            # Verify invariants
            if not (len(equity_curve) == len(positions) == len(trend)):
                raise ValueError(
                    f"Length mismatch: equity={len(equity_curve)}, "
                    f"positions={len(positions)}, trend={len(trend)}"
                )
            if len(equity_curve) != len(returns) + 1:
                raise ValueError(
                    f"Equity length {len(equity_curve)} != returns length {len(returns)} + 1"
                )
    else:
        early_exit = False
        exit_bar = None
        exit_dd = None
    
    return (returns, equity_curve, trend, positions, early_exit, exit_bar, exit_dd)

