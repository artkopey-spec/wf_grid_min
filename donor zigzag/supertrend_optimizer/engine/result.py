"""
Backtest result data structure.

This module defines the unified result container for all backtest operations.
"""

from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    """
    Unified result container for backtest operations.
    
    CONTRACT: Simple Returns (2026-02-09)
    ======================================
    Trade-based metrics in `metrics` dict use SIMPLE ENTRY/EXIT RETURNS:
    
        metrics["sum_pnl_pct"] = sum(trades_df["net_pnl_pct"])
        
        where net_pnl_pct = simple return (entry → exit price) − commission
                          = (exit_price - entry_price) / entry_price * 100  (LONG)
                          = (entry_price - exit_price) / entry_price * 100  (SHORT)
    
    Trade metrics (num_trades, win_rate, sum_pnl_pct, avg_trade, profit_factor):
        - Calculated from trades_df (when extract_trades_flag=True)
        - Based on FULL history (no warmup applied)
        - Source of truth: trades_df["net_pnl_pct"]
        - win_rate is in PERCENT (0.0–100.0), not fraction (0.0–1.0)
    
    Ratio metrics (sharpe, sortino, cagr):
        - Calculated AFTER warmup (first warmup bars excluded)
        - Based on bar-level returns and equity curve
        - Set to INVALID_METRIC_VALUE (-999) when num_trades < min_trades_required

    Equity-based risk metric (max_drawdown):
        - Calculated AFTER warmup
        - NOT invalidated by min_trades_required — only when equity curve is empty/invalid
        - DD at 0 trades = 0.0 (flat curve); downstream consumers filter via num_trades >= 1
    
    Warmup semantics:
        - warmup: requested warmup in bars (resolved from warmup_period or warmup_time)
        - effective_warmup: actual warmup applied to ratio metrics after safety-cap
          (may be less than warmup if warmup > len(returns) - 2)
        - Use effective_warmup to understand what slice was actually used for Sharpe/Sortino/CAGR/DD
    
    DD-01: Trades считаются на полной истории (full history).
    Trades — это события входа/выхода стратегии.
    Warmup — это артефакт стабилизации индикатора.
    Поэтому по умолчанию trades считаются на полной истории.
    
    Attributes:
        atr_period: ATR period parameter
        multiplier: ATR multiplier parameter
        trade_mode: Trading mode ("revers", "long", "short")
        commission: Commission rate per operation
        warmup: Requested warmup period in bars (resolved from warmup_period/warmup_time).
                May differ from effective_warmup if safety-cap was triggered.
        effective_warmup: Actual warmup applied to ratio metrics after safety-cap.
                          Always <= warmup. Use this to know the real slice used for
                          Sharpe/Sortino/CAGR/DD calculation.
        
        returns: Returns array (may be truncated if early_exit=True)
        equity_curve: Equity curve array (length = len(returns) + 1)
        positions: Positions array (length = len(returns) + 1)
        trend: Trend array (length = len(returns) + 1)
        
        metrics: Dictionary with all calculated metrics (see CONTRACT above)
        
        early_exit: True if early exit was triggered
        exit_bar: Bar index where early exit occurred (or None)
        exit_drawdown: Drawdown value at exit bar (or None)
        
        trades_df: DataFrame with all trades (extracted from full history)
                   Columns: trade_id, direction, entry_time, entry_index, entry_price,
                           exit_time, exit_index, exit_price, bars_held,
                           gross_pnl_pct, commission_pct, net_pnl_pct
        
        n_bars_original: Length of original input data (before any truncation)
        period_label: Period label for display ("100%", "50%", "30%", etc.)

        filter_diagnostics: dict | None. Присутствует всегда для mode != "none".
            Обязательные ключи (ДОЛЖНЫ быть):
              • "mode"        : str, значение filters.mode.
              • "thresholds"  : dict, плоские параметры фильтра.
              • "counters"    : dict с семью ключами (raw_entry_signals,
                                passed_entry_signals, blocked_entry_signals,
                                blocked_by_volatility, blocked_by_volume,
                                blocked_by_both, blocked_by_vol_ma_invalid).
              • "allow_entry"     : np.ndarray[bool], shape (positions.shape[0],)
                                    — плановая декларация §3.7: итоговый массив
                                    разрешения входа, decision-bar aligned.
              • "filtered_reason" : np.ndarray[object/str], shape (positions.shape[0],)
                                    — reason после collapse (финальная причина
                                    блокировки или FILTER_REASON_OK).
            Разрешённые mode-specific ключи:
              • amplitude (mode ∈ {amplitude, amplitude_and_volume}):
                  amp_n, amp_threshold, atr_amp, separation.
              • zigzag (mode ∈ {zigzag, zigzag_and_volume}):
                  zz_leg_direction, zz_cand_height_pct, zz_last_pivot_price,
                  zz_last_pivot_bar_idx, zz_global_median, zz_global_p80,
                  zz_local_median, zz_n_legs_before, zz_n_legs_since_regime_open,
                  zz_regime_state, zz_armed, zz_armed_side,
                  zz_n_bars_since_extreme, zz_n_bars_since_arm, zz_legs.
            dtype контракт: float64 (float), int64 (int), bool, int8 (enum).
            Amplitude-ключи и zz-ключи взаимно эксклюзивны (§3.0 план v2.0).
        atr: The ATR array used by the engine. Length always matches
            positions/trend — truncated synchronously when early_exit triggers.
            Stored so that downstream consumers (signal_events, Excel export) can
            reuse it without re-computing. None for legacy results.
    """
    # Parameters
    atr_period: int
    multiplier: float
    trade_mode: str
    commission: float
    warmup: int
    
    # Backtest arrays (may be truncated if early_exit=True)
    returns: np.ndarray
    equity_curve: np.ndarray
    positions: np.ndarray
    trend: np.ndarray
    
    # Metrics
    metrics: dict
    
    # Early exit info
    early_exit: bool
    exit_bar: Optional[int]
    exit_drawdown: Optional[float]
    
    # Trades (DD-01: extracted from full history by default)
    trades_df: Optional[pd.DataFrame]
    
    # Metadata
    n_bars_original: int
    period_label: str = ""
    effective_warmup: int = 0

    # Filter support (PR 4 — §5.1)
    filter_diagnostics: Optional[dict] = field(default=None)
    atr: Optional[np.ndarray] = field(default=None)

