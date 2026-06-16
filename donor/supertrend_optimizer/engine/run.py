"""
Unified backtest execution engine.

This module provides a single entry point for running backtests,
used by both optimizer and tester.

DD-01: Trades считаются на доступной истории.
    - При early_exit=False: полная исходная история.
    - При early_exit=True: история усечена до exit_bar включительно.
Warmup применяется ПОСЛЕ backtest только к ratio-метрикам (sharpe/sortino/cagr/max_drawdown).

WP7 changes
-----------
- ``run_single_backtest`` accepts optional ``trade_filter_config``,
  ``zigzag_global_stats``, ``global_offset`` and forwards them to
  ``run_backtest_fast``.
- ``run_backtest_fast`` now returns ``RawBacktestArtifacts`` (§8.2); the
  legacy 7-tuple unpack is replaced by attribute access.
- ``attach_trade_filter_diagnostics`` is called after ``extract_trades``
  when ``filter_diagnostics`` is present (§8.3 / §8.4).
- ``BacktestResult`` receives ``filter_diagnostics`` (§8.5).
"""

import warnings
import numpy as np
import pandas as pd
from typing import Any, Optional, Union

from supertrend_optimizer.core.backtest import run_backtest_fast, RawBacktestArtifacts
from supertrend_optimizer.core.metrics import calculate_all_metrics
from supertrend_optimizer.core.trades import extract_trades
from supertrend_optimizer.core.filter_trade_diagnostics import (
    attach_trade_filter_diagnostics,
)
from supertrend_optimizer.engine.result import BacktestResult
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.time_utils import resolve_warmup_bars
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE, MAX_VALID_METRIC


def run_single_backtest(
    open_prices: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    index: pd.Index,
    atr_period: int,
    multiplier: float,
    trade_mode: str,
    commission: float,
    warmup_period: Union[int, None] = None,
    warmup_time: Union[str, None] = None,
    early_exit_enabled: bool = False,
    early_exit_max_drawdown: float = 0.5,
    early_exit_check_bars: int = 0,
    periods_per_year: float = 252.0,
    min_trades_required: int = 3,
    extract_trades_flag: bool = True,
    caller_mode: str = "",
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    auto_warmup: bool = False,
    precomputed_atr: "Optional[np.ndarray]" = None,
    # NEW (WP7 / Phase 1) — ZigZag ST filter
    trade_filter_config: Any = None,
    zigzag_global_stats: Any = None,
    volume_runtime: Any = None,
    global_offset: int = 0,
    volume: Optional[np.ndarray] = None,
    *,
    collect_filter_diagnostics: bool = True,
) -> BacktestResult:
    """
    Run single backtest with unified interface.
    
    This function is the single entry point for all backtest operations.
    It combines SuperTrend calculation, position generation, returns calculation,
    metrics calculation, and trades extraction.
    
    INVARIANTS (must remain true):
    1. Warmup is applied AFTER backtest (backtest runs on full data)
    2. Trade stats are calculated on AVAILABLE history after truncation:
       - early_exit=False: full input history
       - early_exit=True:  history truncated to exit_bar (inclusive)
    3. Early-exit truncates ALL arrays (returns/equity/positions/trend) at exit_bar
    4. Optimizer and tester call the same run function

    DD-01: Trades считаются на доступной истории.
    При early_exit=False это полная исходная история.
    При early_exit=True все массивы усечены до exit_bar — trades отражают
    только этот укороченный период. n_bars_original хранит исходную длину.
    
    Args:
        open_prices: Open prices array
        high: High prices array
        low: Low prices array
        close: Close prices array
        index: DataFrame index (datetime or integer)
        atr_period: ATR period
        multiplier: ATR multiplier
        trade_mode: Trading mode ("revers", "long", "short")
        commission: Commission rate per operation
        warmup_period: Warmup period in bars (applied AFTER backtest for metrics only).
                       Mutually exclusive with warmup_time.
        warmup_time: Warmup period in time (e.g., "7d", "48h", "180m").
                     Converted to bars using median timestamp delta.
                     Mutually exclusive with warmup_period.
        early_exit_enabled: Whether to check for early exit (default: False)
        early_exit_max_drawdown: Maximum allowed drawdown for early exit (default: 0.5)
        early_exit_check_bars: Number of bars to check for early exit (default: 0)
        periods_per_year: Periods per year for annualization (default: 252.0)
        min_trades_required: Minimum trades required for valid metrics (default: 3)
        extract_trades_flag: Whether to extract trades table (default: True)
        caller_mode: "optimizer" | "tester" | "" - for warnings
        execution_model: Must be ExecutionModel.OPEN_TO_OPEN (only supported value).
                         CLOSE_TO_CLOSE was removed due to look-ahead bias.
        auto_warmup: Safety guard — if True, enforce warmup >= atr_period.
                     This does NOT implement Variant A (10 % n, clamp 100..400).
                     Variant A is resolved by the orchestrator before calling this
                     function, via apply_auto_warmup_to_config().
        
    Returns:
        BacktestResult with all data and metrics
        
    Raises:
        ValueError: If input arrays have incompatible lengths, or if both warmup_period
                    and warmup_time are provided, or if warmup_time is used with a
                    non-DatetimeIndex, or if required parameters are out of valid range
    """
    # Guard: warn if tester runs with early_exit enabled
    if early_exit_enabled and caller_mode == "tester":
        warnings.warn(
            "Tester is running with early_exit enabled; "
            "this changes historical tester semantics. "
            "Arrays will be truncated at exit_bar.",
            stacklevel=2,
        )

    # Guard: warn if early exit is enabled but check_bars=0 (exit will never trigger)
    if early_exit_enabled and early_exit_check_bars <= 0:
        warnings.warn(
            "early_exit_enabled=True but early_exit_check_bars=0; "
            "early exit will never trigger because no bars are checked. "
            "Set early_exit_check_bars > 0 to enable the feature.",
            stacklevel=2,
        )
    
    # Store original length
    n_bars_original = len(open_prices)
    
    # Validate input arrays
    if not (len(open_prices) == len(high) == len(low) == len(close) == len(index)):
        raise ValueError(
            f"Input arrays must have same length: "
            f"open={len(open_prices)}, high={len(high)}, low={len(low)}, "
            f"close={len(close)}, index={len(index)}"
        )
    if volume is not None and len(volume) != n_bars_original:
        raise ValueError(
            f"Volume array must have same length as prices: "
            f"volume={len(volume)}, open={n_bars_original}"
        )

    # Validate minimum data length: need at least 2 bars to produce 1 return
    if n_bars_original < 2:
        raise ValueError(
            f"Input data must contain at least 2 bars, got {n_bars_original}. "
            "A minimum of 2 bars is required to produce one return."
        )

    # Validate strategy parameters
    if not isinstance(atr_period, int) or atr_period < 2:
        raise ValueError(
            f"atr_period must be an integer >= 2, got {atr_period!r}. "
            "ATR requires at least 2 bars to compute a meaningful range."
        )
    if not np.isfinite(multiplier) or multiplier <= 0:
        raise ValueError(
            f"multiplier must be a finite positive number, got {multiplier!r}."
        )
    if not np.isfinite(commission) or commission < 0:
        raise ValueError(
            f"commission must be a finite non-negative number, got {commission!r}."
        )
    if not np.isfinite(periods_per_year) or periods_per_year <= 0:
        raise ValueError(
            f"periods_per_year must be a finite positive number, got {periods_per_year!r}. "
            "Typical values: 252 (daily stocks), 365 (daily crypto), 8760 (hourly crypto)."
        )
    if not isinstance(min_trades_required, int) or min_trades_required < 0:
        raise ValueError(
            f"min_trades_required must be a non-negative integer, got {min_trades_required!r}."
        )
    if not isinstance(early_exit_check_bars, int) or early_exit_check_bars < 0:
        raise ValueError(
            f"early_exit_check_bars must be a non-negative integer, got {early_exit_check_bars!r}."
        )

    # Resolve warmup in bars (convert warmup_time to bars if needed)
    # Guard: warmup_time requires a DatetimeIndex to compute bar duration.
    # Passing a non-DatetimeIndex would cause pd.DatetimeIndex(index) to create
    # synthetic nanosecond-epoch timestamps, producing a meaningless warmup value.
    if warmup_time is not None and not isinstance(index, pd.DatetimeIndex):
        raise ValueError(
            f"warmup_time='{warmup_time}' requires a DatetimeIndex, "
            f"but index is {type(index).__name__}. "
            "Use warmup_period (in bars) for non-datetime indexes."
        )
    warmup_bars = resolve_warmup_bars(
        warmup_period=warmup_period,
        warmup_time=warmup_time,
        index=index if isinstance(index, pd.DatetimeIndex) else pd.DatetimeIndex([]),
        atr_period=atr_period,
        auto_warmup=auto_warmup
    )
    
    # Step 1: Run backtest (may truncate arrays if early_exit=True).
    # Returns RawBacktestArtifacts (WP7); legacy 7-tuple unpack replaced.
    # F-21b: pass precomputed_atr when available to skip redundant ATR.
    _artifacts = run_backtest_fast(
        open_prices=open_prices,
        high=high,
        low=low,
        close=close,
        atr_period=atr_period,
        multiplier=multiplier,
        trade_mode=trade_mode,
        commission=commission,
        early_exit_enabled=early_exit_enabled,
        early_exit_max_drawdown=early_exit_max_drawdown,
        early_exit_check_bars=early_exit_check_bars,
        execution_model=execution_model,
        precomputed_atr=precomputed_atr,
        trade_filter_config=trade_filter_config,
        zigzag_global_stats=zigzag_global_stats,
        volume_runtime=volume_runtime,
        global_offset=global_offset,
        index=index,                          # NEW: for daily_reset
        volume=volume,
        collect_filter_diagnostics=collect_filter_diagnostics,
    )
    returns = _artifacts.returns
    equity_curve = _artifacts.equity_curve
    trend = _artifacts.trend
    positions = _artifacts.positions
    early_exit = _artifacts.early_exit
    exit_bar = _artifacts.exit_bar
    exit_dd = _artifacts.exit_drawdown
    filter_config_snapshot = _artifacts.filter_config_snapshot
    
    # Step 2: Handle truncation for execution_prices and index (if early_exit=True)
    # run_backtest_fast already truncated returns/equity/positions/trend
    # We need to truncate execution_prices and index to match
    # execution_prices = open or close depending on execution_model
    if execution_model != ExecutionModel.OPEN_TO_OPEN:
        raise ValueError(
            f"ExecutionModel.{execution_model} is not supported. "
            "Only OPEN_TO_OPEN is allowed. "
            "CLOSE_TO_CLOSE was removed due to look-ahead bias."
        )
    execution_prices_full = open_prices
    
    if early_exit and exit_bar is not None:
        # Truncate to match positions length (which is len(returns) + 1)
        execution_prices_for_trades = execution_prices_full[:exit_bar + 1]
        index_for_trades = index[:exit_bar + 1]
    else:
        execution_prices_for_trades = execution_prices_full
        index_for_trades = index
    
    # Verify invariant: array lengths after truncation.
    # Using explicit ValueError instead of assert so checks survive python -O.
    if not (len(equity_curve) == len(positions) == len(trend)):
        raise ValueError(
            f"[BUG] Array length mismatch after run_backtest_fast: "
            f"equity={len(equity_curve)}, positions={len(positions)}, trend={len(trend)}. "
            "This is an internal invariant violation — please report."
        )
    if len(equity_curve) != len(returns) + 1:
        raise ValueError(
            f"[BUG] Equity length {len(equity_curve)} != returns length {len(returns)} + 1. "
            "This is an internal invariant violation — please report."
        )
    if len(execution_prices_for_trades) != len(positions):
        raise ValueError(
            f"[BUG] execution_prices_for_trades length {len(execution_prices_for_trades)} "
            f"!= positions length {len(positions)}. "
            "This is an internal invariant violation — please report."
        )
    if len(index_for_trades) != len(positions):
        raise ValueError(
            f"[BUG] index_for_trades length {len(index_for_trades)} "
            f"!= positions length {len(positions)}. "
            "This is an internal invariant violation — please report."
        )
    
    # Step 3: Calculate metrics (warmup already resolved in warmup_bars)
    # warmup_bars already includes auto_warmup logic (max(warmup_bars, atr_period))
    # metrics["effective_warmup"] may be < warmup_bars if safety-cap was triggered
    metrics = calculate_all_metrics(
        returns=returns,
        equity_curve=equity_curve,
        positions=positions,
        warmup_period=warmup_bars,
        periods_per_year=periods_per_year,
        min_trades_required=min_trades_required
    )
    
    # Step 4: Extract trades (DD-01: from available history)
    # early_exit=False → full input history; early_exit=True → truncated to exit_bar
    trades_df = None
    if extract_trades_flag:
        trades_df = extract_trades(
            positions=positions,
            returns=returns,
            execution_prices=execution_prices_for_trades,
            index=index_for_trades,
            commission_rate=commission,
            trend=trend,
            execution_model=execution_model.value
        )
        
        # Step 4.2: Attach trade-level filter diagnostics (WP7 / §8.3, §8.4).
        # Called on extended-slice indices BEFORE any OOS trim/rebase.
        # Adds entry_filter_state, entry_trigger_source, exit_reason columns.
        if (
            _artifacts.filter_diagnostics is not None
            and trades_df is not None
            and len(trades_df) > 0
        ):
            trades_df = attach_trade_filter_diagnostics(
                trades_df, _artifacts.filter_diagnostics
            )

        # Step 4.5: Recalculate trade-based metrics from trades_df.
        # Source of truth: simple entry/exit returns (net_pnl_pct), not compound bar-level returns.
        # These values overwrite the preliminary estimates from calculate_all_metrics (Step 3).
        #
        # SEMANTIC CONTRACT:
        #   win_rate  — PERCENT (0.0–100.0), not fraction (0.0–1.0)
        #   sum_pnl_pct — sum of simple per-trade returns, not compound equity return
        #   profit_factor — MAX_VALID_METRIC (9999.0) when zero losses (F-08: capped for
        #                   rankability; consistent with bar-level path in metrics.py)

        # Guard: validate trades_df schema before accessing net_pnl_pct
        if trades_df is not None and len(trades_df) > 0:
            if "net_pnl_pct" not in trades_df.columns:
                raise ValueError(
                    "[BUG] extract_trades returned a DataFrame without 'net_pnl_pct' column. "
                    f"Columns present: {list(trades_df.columns)}. "
                    "This is an internal contract violation — please report."
                )
            nan_count = trades_df["net_pnl_pct"].isna().sum()
            if nan_count > 0:
                warnings.warn(
                    f"trades_df['net_pnl_pct'] contains {nan_count} NaN value(s). "
                    "These rows will be dropped before computing trade metrics. "
                    "Check extract_trades for data quality issues.",
                    stacklevel=2,
                )
                trades_df = trades_df.dropna(subset=["net_pnl_pct"])

        if trades_df is not None and len(trades_df) > 0:
            num_trades = len(trades_df)
            
            # win_rate: percentage of trades with positive net PnL (0.0–100.0)
            winning_trades = (trades_df['net_pnl_pct'] > 0).sum()
            win_rate = (winning_trades / num_trades * 100.0) if num_trades > 0 else 0.0
            if not (0.0 <= win_rate <= 100.0):
                raise ValueError(
                    f"[BUG] win_rate {win_rate} out of expected range [0, 100]. "
                    "This is an internal invariant violation — please report."
                )
            
            # sum_pnl_pct: simple sum of per-trade net returns
            sum_pnl_pct = trades_df['net_pnl_pct'].sum()
            
            avg_trade = sum_pnl_pct / num_trades if num_trades > 0 else 0.0
            
            # profit_factor: gross_profit / abs(gross_loss)
            profits = trades_df.loc[trades_df['net_pnl_pct'] > 0, 'net_pnl_pct'].sum()
            losses = trades_df.loc[trades_df['net_pnl_pct'] < 0, 'net_pnl_pct'].sum()
            
            if losses < 0:  # Has losses
                profit_factor = profits / abs(losses)
            elif profits > 0:  # Only profits, no losses — cap at MAX_VALID_METRIC (F-08)
                profit_factor = MAX_VALID_METRIC
            else:  # All breakeven: no profit, no loss
                profit_factor = INVALID_METRIC_VALUE
            
            # Update metrics dictionary with recalculated values.
            # Ratio metrics (sharpe, sortino, cagr, max_drawdown) are kept from Step 3
            # because they depend on warmup and bar-level returns, not trades_df.
            metrics['num_trades'] = num_trades
            metrics['win_rate'] = win_rate
            metrics['sum_pnl_pct'] = sum_pnl_pct
            metrics['avg_trade'] = avg_trade
            metrics['profit_factor'] = profit_factor
            metrics['net_pnl_pct'] = avg_trade
            
            # Re-apply min_trades_required guard for ratio metrics after trade count update.
            # Step 3 used bar-level num_trades estimate; now we have the exact count from trades_df.
            # max_drawdown is equity-based and not invalidated by trade count.
            if num_trades < min_trades_required:
                metrics['sharpe'] = INVALID_METRIC_VALUE
                metrics['sortino'] = INVALID_METRIC_VALUE
                metrics['cagr'] = INVALID_METRIC_VALUE
        else:
            # trades_df is empty but was requested - set to zero/invalid
            # max_drawdown retains value from calculate_all_metrics (equity-based).
            metrics['num_trades'] = 0
            metrics['win_rate'] = 0.0
            metrics['sum_pnl_pct'] = 0.0
            metrics['avg_trade'] = INVALID_METRIC_VALUE
            metrics['profit_factor'] = INVALID_METRIC_VALUE
            metrics['net_pnl_pct'] = INVALID_METRIC_VALUE
            metrics['sharpe'] = INVALID_METRIC_VALUE
            metrics['sortino'] = INVALID_METRIC_VALUE
            metrics['cagr'] = INVALID_METRIC_VALUE
    
    # Step 5: Create result
    # effective_warmup may be < warmup_bars if safety-cap was triggered in calculate_all_metrics.
    # Use get (not pop) to avoid silently mutating the dict; remove the key afterwards
    # so it is not duplicated between BacktestResult.effective_warmup and metrics dict.
    effective_warmup = metrics.get("effective_warmup", warmup_bars)
    metrics.pop("effective_warmup", None)
    result = BacktestResult(
        atr_period=atr_period,
        multiplier=multiplier,
        trade_mode=trade_mode,
        commission=commission,
        warmup=warmup_bars,
        effective_warmup=effective_warmup,
        returns=returns,
        equity_curve=equity_curve,
        positions=positions,
        trend=trend,
        metrics=metrics,
        early_exit=early_exit,
        exit_bar=exit_bar,
        exit_drawdown=exit_dd,
        trades_df=trades_df,
        n_bars_original=n_bars_original,
        period_label="",
        filter_diagnostics=_artifacts.filter_diagnostics,
        filter_config_snapshot=filter_config_snapshot,
    )
    
    return result
