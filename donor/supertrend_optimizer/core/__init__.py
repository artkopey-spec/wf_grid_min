"""
Core calculation modules for SuperTrend Optimizer.
"""

from .calculator import (
    calculate_true_range,
    calculate_atr_rma,
    calculate_basic_bands,
    calculate_final_bands,
    calculate_trend_direction,
    calculate_supertrend_value,
    calculate_supertrend,
)

from .backtest import (
    generate_positions,
    calculate_returns,
    calculate_equity_curve,
    check_early_exit,
    run_backtest_fast,
)

from .metrics import (
    calculate_sum_pnl_percent,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_max_drawdown,
    calculate_cagr,
    calculate_trade_stats_from_positions,
    calculate_all_metrics,
)

__all__ = [
    # Calculator functions
    "calculate_true_range",
    "calculate_atr_rma",
    "calculate_basic_bands",
    "calculate_final_bands",
    "calculate_trend_direction",
    "calculate_supertrend_value",
    "calculate_supertrend",
    # Backtest functions
    "generate_positions",
    "calculate_returns",
    "calculate_equity_curve",
    "check_early_exit",
    "run_backtest_fast",
    # Metrics functions
    "calculate_sum_pnl_percent",
    "calculate_sharpe_ratio",
    "calculate_sortino_ratio",
    "calculate_max_drawdown",
    "calculate_cagr",
    "calculate_trade_stats_from_positions",
    "calculate_all_metrics",
]

