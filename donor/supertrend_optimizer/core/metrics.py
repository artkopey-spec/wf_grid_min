"""
Metrics calculation module for SuperTrend strategy.

This module implements performance metrics calculation.

CONTRACT: Simple Returns (2026-02-09)
=====================================
Trade-based metrics (sum_pnl_pct, avg_trade, win_rate, profit_factor, num_trades)
are calculated from trades_df using SIMPLE ENTRY/EXIT RETURNS:

    sum_pnl_pct = sum(trades_df["net_pnl_pct"])
    
    where net_pnl_pct = simple return (entry → exit price) − commission
                      = (exit_price - entry_price) / entry_price * 100  (LONG)
                      = (entry_price - exit_price) / entry_price * 100  (SHORT)

IMPORTANT:
- sum_pnl_pct is NOT compound equity return (not from equity curve)
- Trade metrics are calculated on FULL history (no warmup applied)
- Warmup only affects ratio metrics (sharpe, sortino, cagr, max_drawdown)
- Source of truth: trades_df["net_pnl_pct"], not bar-level returns

This ensures alignment between Optimizer and Tester.
"""

import numpy as np
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE, MAX_VALID_METRIC


def calculate_sum_pnl_percent(
    returns: np.ndarray
) -> float:
    """
    Calculate Sum PnL % (bar-level, without compounding).

    Formula:
        Sum PnL % = sum(returns) * 100

    This is the simple arithmetic sum of per-bar returns expressed as a
    percentage.  It is NOT:
    - the compound equity return  ( = equity[-1] / equity[0] - 1 )
    - the sum of per-trade simple returns from entry/exit prices

    IMPORTANT — two-world contract:
        When called from calculate_all_metrics() this function produces a
        PRELIMINARY bar-level estimate.  In run_single_backtest() that value
        is OVERWRITTEN with the trade-level sum (sum of trades_df.net_pnl_pct)
        when extract_trades_flag=True.

        If extract_trades_flag=False (optimizer mode) the bar-level value
        remains.  It will differ from the tester's trade-level value due to:
        1. Compounding effect on multi-bar trades.
        2. Difference in how per-bar commission is distributed vs per-trade.

        Consumers should treat the bar-level value as an approximate rank
        signal, not as a precise P&L figure.

    Args:
        returns: Bar-level returns array (commission already included)

    Returns:
        Sum PnL % value, or INVALID_METRIC_VALUE if calculation fails
    """
    # Check for empty array
    if len(returns) == 0:
        return INVALID_METRIC_VALUE
    
    # Calculate sum of returns
    sum_returns = np.sum(returns)
    
    # Convert to percentage
    result = sum_returns * 100.0
    
    # Check for nan/inf
    if not np.isfinite(result):
        return INVALID_METRIC_VALUE
    
    return float(result)


def calculate_sharpe_ratio(
    returns: np.ndarray,
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.0
) -> float:
    """
    Calculate Sharpe Ratio.
    
    Formula:
    Sharpe = (mean(returns) - rf_per_period) / std(returns) * sqrt(periods_per_year)
    
    where:
    rf_per_period = risk_free_rate / periods_per_year
    
    Args:
        returns: Returns array
        periods_per_year: Number of periods per year (default: 252 for daily)
        risk_free_rate: Annual risk-free rate (default: 0.0)
        
    Returns:
        Sharpe Ratio value, or INVALID_METRIC_VALUE if calculation fails
    """
    # Check minimum length
    if len(returns) < 2:
        return INVALID_METRIC_VALUE
    
    # Calculate risk-free rate per period
    rf_per_period = risk_free_rate / periods_per_year
    
    # Calculate mean and std
    mean_return = np.mean(returns)
    std_return = np.std(returns, ddof=1)
    
    # F-22: treat near-zero std (< 1e-12) as degenerate case
    if std_return < 1e-12:
        # All returns are identical: strategy has no variability
        if mean_return > rf_per_period:
            return MAX_VALID_METRIC   # perfectly consistent positive excess return
        return INVALID_METRIC_VALUE   # flat or below risk-free

    # Calculate Sharpe ratio
    sharpe = (mean_return - rf_per_period) / std_return * np.sqrt(periods_per_year)
    
    # Check for nan/inf
    if not np.isfinite(sharpe):
        return INVALID_METRIC_VALUE
    
    return float(sharpe)


def calculate_sortino_ratio(
    returns: np.ndarray,
    periods_per_year: float = 252.0,
    target_return: float = 0.0
) -> float:
    """
    Calculate Sortino Ratio using full-sample downside deviation.

    Formula (annualised):
        Sortino = (mean(r) - MAR) / DD * sqrt(P)

    where:
        d_i = min(r_i - MAR, 0)        for every i in 1..N
        DD  = sqrt( (1/N) * sum(d_i^2) )
        MAR = target_return (minimum acceptable return)
        P   = periods_per_year

    Key difference from the naive implementation: d_i is computed over ALL N
    observations (not just the negative subset), and the denominator uses N
    (population), not N-1 (sample).

    Edge cases → INVALID_METRIC_VALUE (-999.0):
        - len(returns) < 2
        - no downside observations (all r_i >= MAR)
        - DD < eps  (near-zero downside risk → would produce +inf)
        - result is NaN or +/-inf

    Args:
        returns: Returns array
        periods_per_year: Number of periods per year (default: 252 for daily)
        target_return: Target return threshold / MAR (default: 0.0)

    Returns:
        Annualised Sortino Ratio, or INVALID_METRIC_VALUE if incalculable.
    """
    if len(returns) < 2:
        return INVALID_METRIC_VALUE

    r = np.asarray(returns, dtype=np.float64)

    diff = r - target_return
    downside = np.minimum(diff, 0.0)

    if not np.any(downside < 0.0):
        return INVALID_METRIC_VALUE

    downside_dev = np.sqrt(np.mean(downside ** 2))

    eps = 1e-12
    if downside_dev < eps:
        return INVALID_METRIC_VALUE

    mean_return = np.mean(r)
    sortino = (mean_return - target_return) / downside_dev * np.sqrt(periods_per_year)

    if not np.isfinite(sortino):
        return INVALID_METRIC_VALUE

    return float(sortino)


def calculate_max_drawdown(
    equity_curve: np.ndarray
) -> float:
    """
    Calculate Maximum Drawdown.
    
    Maximum drawdown is the largest peak-to-trough decline in equity.
    
    Formula:
    peak = cumulative_max(equity_curve)
    drawdown = equity_curve / peak - 1
    max_dd = min(drawdown)  # negative number or 0
    
    Args:
        equity_curve: Equity curve array
        
    Returns:
        Maximum drawdown value (negative or 0), or INVALID_METRIC_VALUE if calculation fails
    """
    # Check for empty array
    if len(equity_curve) == 0:
        return INVALID_METRIC_VALUE
    
    # Calculate running peak
    peak = np.maximum.accumulate(equity_curve)
    
    # Calculate drawdown at each point
    drawdown = equity_curve / peak - 1.0
    
    # Maximum drawdown is the minimum value (most negative)
    max_dd = np.min(drawdown)
    
    # Check for nan/inf
    if not np.isfinite(max_dd):
        return INVALID_METRIC_VALUE
    
    return float(max_dd)


def calculate_cagr(
    equity_curve: np.ndarray,
    periods_per_year: float = 252.0
) -> float:
    """
    Calculate Compound Annual Growth Rate (CAGR).
    
    CAGR represents the geometric mean growth rate per year.
    
    Formula:
    years = (len(equity_curve) - 1) / periods_per_year
    CAGR = (equity_curve[-1] / equity_curve[0]) ** (1 / years) - 1
    
    Args:
        equity_curve: Equity curve array
        periods_per_year: Number of periods per year (default: 252 for daily)
        
    Returns:
        CAGR value, or INVALID_METRIC_VALUE if calculation fails
    """
    # Check for empty array
    if len(equity_curve) == 0:
        return INVALID_METRIC_VALUE
    
    # Check for non-positive equity values
    if equity_curve[0] <= 0 or equity_curve[-1] <= 0:
        return INVALID_METRIC_VALUE
    
    # Calculate number of years
    years = (len(equity_curve) - 1) / periods_per_year
    
    # Check for non-positive years
    if years <= 0:
        return INVALID_METRIC_VALUE
    
    # Calculate CAGR
    cagr = (equity_curve[-1] / equity_curve[0]) ** (1.0 / years) - 1.0
    
    # Check for nan/inf
    if not np.isfinite(cagr):
        return INVALID_METRIC_VALUE
    
    return float(cagr)


def calculate_trade_stats_from_positions(
    positions: np.ndarray,
    returns: np.ndarray
) -> tuple[int, float]:
    """
    Calculate trade statistics from positions and bar-level returns.

    A trade is a continuous period when position != 0.
    Trade closes when position changes direction or becomes 0.
    Trade return = sum of bar-level returns during the trade
                   (commission already included in each return bar).

    win_rate is returned as a PERCENT (0.0 – 100.0), not a fraction.

    WARNING — num_trades divergence:
        This function operates on positions[:len(returns)] and therefore
        does NOT see the position slot at index len(returns) (the "pending"
        position after the last return bar).  extract_trades() in trades.py
        iterates over ALL n+1 position slots and may count one additional
        "pending" trade (bars_held=0) that opens on the last bar.

        Consequence: in some edge cases this function returns
        num_trades = extract_trades() count − 1.

        In run_single_backtest() with extract_trades_flag=True, the value
        from this function is OVERWRITTEN by len(trades_df), so the
        discrepancy only matters when extract_trades_flag=False (optimizer
        mode).  Downstream consumers should be aware.

    WARNING — trade return approximation:
        Trade return = sum(returns[entry..exit]) approximates the true
        compounded return = prod(1 + returns[entry..exit]) − 1.
        The approximation error grows with trade length and per-bar
        volatility.  For typical commission rates and daily data the
        error is small (< 0.5 % on a 50-bar trade with 0.1 %/bar moves).

    Args:
        positions: Positions array (can be len(returns) or len(returns)+1)
        returns: Returns array (with commission already applied)

    Returns:
        Tuple of (num_trades, win_rate):
        - num_trades: Total number of completed trades
        - win_rate: Percent of profitable trades (0.0–100.0),
                    or INVALID_METRIC_VALUE if num_trades == 0
    """
    # Check for empty returns
    if len(returns) == 0:
        return (0, INVALID_METRIC_VALUE)
    
    # Align positions with returns (handle both cases)
    positions_for_returns = positions[:len(returns)]
    
    # Track trades
    num_trades = 0
    profitable_trades = 0
    in_trade = False
    current_trade_return = 0.0
    prev_position = 0
    
    for i in range(len(returns)):
        pos = positions_for_returns[i]
        
        # Check if we're starting a new trade
        if pos != 0 and not in_trade:
            # Starting new trade
            in_trade = True
            current_trade_return = returns[i]
            prev_position = pos
        elif in_trade:
            # We're in a trade
            if pos == 0 or pos != prev_position:
                # Trade is closing
                num_trades += 1
                if current_trade_return > 0:
                    profitable_trades += 1
                
                # Check if new trade starts immediately
                if pos != 0:
                    # New trade starts
                    current_trade_return = returns[i]
                    prev_position = pos
                    in_trade = True
                else:
                    # No position
                    in_trade = False
                    current_trade_return = 0.0
                    prev_position = 0
            else:
                # Continue current trade
                current_trade_return += returns[i]
                prev_position = pos
    
    # Close last trade if still open
    if in_trade:
        num_trades += 1
        if current_trade_return > 0:
            profitable_trades += 1
    
    # win_rate as PERCENT (0.0–100.0) — consistent with run.py contract
    if num_trades > 0:
        win_rate = (profitable_trades / num_trades) * 100.0
    else:
        win_rate = INVALID_METRIC_VALUE

    return (num_trades, win_rate)


def calculate_profit_factor(
    positions: np.ndarray,
    returns: np.ndarray
) -> float:
    """
    Calculate Profit Factor.
    
    Profit Factor = gross_profit / gross_loss
    
    A trade is a period when position != 0.
    Trade closes when position changes or becomes 0.
    Trade return = sum of returns during the trade (commission already included).
    
    Args:
        positions: Positions array (can be len(returns) or len(returns)+1)
        returns: Returns array (with commission already applied)
        
    Returns:
        Profit Factor value, or INVALID_METRIC_VALUE if calculation fails
    """
    # Check for empty returns
    if len(returns) == 0:
        return INVALID_METRIC_VALUE
    
    # Align positions with returns
    positions_for_returns = positions[:len(returns)]
    
    # Track trades
    gross_profit = 0.0
    gross_loss = 0.0
    in_trade = False
    current_trade_return = 0.0
    prev_position = 0
    
    for i in range(len(returns)):
        pos = positions_for_returns[i]
        
        # Check if we're starting a new trade
        if pos != 0 and not in_trade:
            # Starting new trade
            in_trade = True
            current_trade_return = returns[i]
            prev_position = pos
        elif in_trade:
            # We're in a trade
            if pos == 0 or pos != prev_position:
                # Trade is closing
                if current_trade_return > 0:
                    gross_profit += current_trade_return
                elif current_trade_return < 0:
                    gross_loss += abs(current_trade_return)
                
                # Check if new trade starts immediately
                if pos != 0:
                    # New trade starts
                    current_trade_return = returns[i]
                    prev_position = pos
                    in_trade = True
                else:
                    # No position
                    in_trade = False
                    current_trade_return = 0.0
                    prev_position = 0
            else:
                # Continue current trade
                current_trade_return += returns[i]
                prev_position = pos
    
    # Close last trade if still open
    if in_trade:
        if current_trade_return > 0:
            gross_profit += current_trade_return
        elif current_trade_return < 0:
            gross_loss += abs(current_trade_return)
    
    # Calculate profit factor
    # F-08: When gross_loss == 0 and gross_profit > 0, the strategy had only
    # winning trades — a legitimately excellent result, not an error.
    # Cap at 9999.0 instead of returning INVALID to keep it rankable.
    if gross_loss == 0:
        if gross_profit > 0:
            return 9999.0
        return INVALID_METRIC_VALUE
    
    if gross_profit == 0:
        # No winning trades
        return 0.0
    
    profit_factor = gross_profit / gross_loss
    
    # Check for nan/inf
    if not np.isfinite(profit_factor):
        return INVALID_METRIC_VALUE
    
    return float(profit_factor)


def calculate_avg_trade(
    sum_pnl_pct: float,
    num_trades: int
) -> float:
    """
    Calculate Average Trade P&L %.
    
    Formula:
    Avg Trade = sum_pnl_pct / num_trades
    
    Args:
        sum_pnl_pct: Sum P&L % value
        num_trades: Number of trades
        
    Returns:
        Average trade value, or INVALID_METRIC_VALUE if calculation fails
    """
    # Check for invalid inputs
    if num_trades == 0:
        return INVALID_METRIC_VALUE
    
    if sum_pnl_pct == INVALID_METRIC_VALUE:
        return INVALID_METRIC_VALUE
    
    avg_trade = sum_pnl_pct / num_trades
    
    # Check for nan/inf
    if not np.isfinite(avg_trade):
        return INVALID_METRIC_VALUE
    
    return float(avg_trade)


def calculate_all_metrics(
    returns: np.ndarray,
    equity_curve: np.ndarray,
    positions: np.ndarray,
    warmup_period: int,
    periods_per_year: float = 252.0,
    min_trades_required: int = 3
) -> dict:
    """
    Calculate all performance metrics with warmup period.
    
    CONTRACT: Warmup and Trade Metrics
    -----------------------------------
    - Trade metrics (num_trades, win_rate, sum_pnl_pct, avg_trade, profit_factor):
      Calculated on FULL history (no warmup applied)
      
    - Ratio metrics (sharpe, sortino, cagr):
      Calculated AFTER warmup (first warmup_period bars excluded)
      ALL set to INVALID_METRIC_VALUE when num_trades < min_trades_required

    - Equity-based risk metric (max_drawdown):
      Calculated AFTER warmup. NOT invalidated by min_trades_required.
      INVALID only when equity curve is empty or contains NaN/Inf.
      Note: DD at low trade counts reflects individual events, not strategy
      statistics. Downstream consumers filter via dd_observed_count/ratio.
    
    NOTE — preliminary vs final trade metrics:
        This function computes trade metrics (num_trades, win_rate, sum_pnl_pct,
        avg_trade, profit_factor) from bar-level returns.  These are PRELIMINARY
        estimates.  In run_single_backtest() they are OVERWRITTEN by exact values
        derived from trades_df when extract_trades_flag=True (tester mode).

        Key differences between bar-level (here) and trade-level (run.py):
        - sum_pnl_pct: bar-level = sum(returns)*100 vs trade-level = sum(net_pnl_pct)
        - win_rate: both return PERCENT (0.0–100.0) after BUG-09 fix
        - num_trades: bar-level may miss the pending trade at the last position slot
        - profit_factor: bar-level uses summed per-bar returns per trade vs
          trade-level uses simple entry/exit price returns

        In optimizer mode (extract_trades_flag=False) the bar-level values are the
        final values.  They are suitable for ranking but not for exact reporting.
    
    Args:
        returns: Returns array (full backtest)
        equity_curve: Equity curve array (length = len(returns) + 1)
        positions: Positions array
        warmup_period: Number of bars to exclude from ratio metrics calculation
        periods_per_year: Number of periods per year (default: 252 for daily)
        min_trades_required: Minimum number of trades required for valid metrics
        
    Returns:
        Dictionary with metrics:
        - sharpe, sortino, sum_pnl_pct, max_drawdown, cagr, win_rate, num_trades,
          profit_factor, avg_trade
        - effective_warmup: actual warmup applied after safety-cap (may be < warmup_period)
    """
    import logging as _logging
    _metrics_logger = _logging.getLogger(__name__)

    # FIX 1: Safety-cap warmup so returns_eff always has at least 2 bars.
    # This prevents INVALID ratio metrics when auto_warmup (up to 400) exceeds
    # a short OOS window (~252 bars).
    min_bars_for_ratio = 2
    max_allowed_warmup = max(0, len(returns) - min_bars_for_ratio)
    effective_warmup = warmup_period
    if warmup_period > max_allowed_warmup:
        _metrics_logger.warning(
            "warmup_cap triggered: warmup_period=%d reduced to %d "
            "(len(returns)=%d, need at least %d bars after warmup)",
            warmup_period, max_allowed_warmup, len(returns), min_bars_for_ratio,
        )
        effective_warmup = max_allowed_warmup

    # Apply warmup: exclude first effective_warmup bars for ratio metrics
    returns_eff = returns[effective_warmup:]
    equity_eff = equity_curve[effective_warmup:]
    
    # Calculate trade stats on FULL history (no warmup) - ALWAYS
    num_trades, win_rate = calculate_trade_stats_from_positions(positions, returns)
    profit_factor = calculate_profit_factor(positions, returns)
    sum_pnl_pct = calculate_sum_pnl_percent(returns)
    avg_trade = calculate_avg_trade(sum_pnl_pct, num_trades)
    
    # Check if returns_eff is empty (edge case: warmup >= len(returns))
    if len(returns_eff) == 0:
        metrics = {
            "sharpe": INVALID_METRIC_VALUE,
            "sortino": INVALID_METRIC_VALUE,
            "sum_pnl_pct": sum_pnl_pct,
            "max_drawdown": INVALID_METRIC_VALUE,
            "cagr": INVALID_METRIC_VALUE,
            "win_rate": win_rate,
            "num_trades": num_trades,
            "profit_factor": profit_factor,
            "avg_trade": avg_trade,
            "effective_warmup": effective_warmup,
        }
        # net_pnl_pct is a BACKWARD-COMPAT alias for avg_trade (per-trade average net PnL %).
        # It is NOT total equity PnL. Kept for Excel report column compatibility.
        metrics["net_pnl_pct"] = metrics.get("avg_trade", INVALID_METRIC_VALUE)
        return metrics

    # Calculate ratio metrics with warmup applied
    sharpe = calculate_sharpe_ratio(returns_eff, periods_per_year)
    sortino = calculate_sortino_ratio(returns_eff, periods_per_year)
    max_dd = calculate_max_drawdown(equity_eff)
    cagr = calculate_cagr(equity_eff, periods_per_year)
    
    # Check minimum trades requirement (applies to full history num_trades).
    # When there are too few trades, statistical ratio metrics are unreliable.
    # max_drawdown is equity-based and remains valid regardless of trade count.
    if num_trades < min_trades_required:
        sharpe = INVALID_METRIC_VALUE
        sortino = INVALID_METRIC_VALUE
        cagr = INVALID_METRIC_VALUE
    
    metrics = {
        "sharpe": sharpe,
        "sortino": sortino,
        "sum_pnl_pct": sum_pnl_pct,
        "max_drawdown": max_dd,
        "cagr": cagr,
        "win_rate": win_rate,
        "num_trades": num_trades,
        "profit_factor": profit_factor,
        "avg_trade": avg_trade,
        "effective_warmup": effective_warmup,
    }
    
    # net_pnl_pct is a BACKWARD-COMPAT alias for avg_trade (per-trade average net PnL %).
    # It is NOT total equity PnL. Kept for Excel report column compatibility.
    metrics["net_pnl_pct"] = metrics.get("avg_trade", INVALID_METRIC_VALUE)

    return metrics

