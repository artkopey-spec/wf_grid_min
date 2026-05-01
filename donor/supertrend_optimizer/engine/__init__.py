"""
Engine module - unified backtest execution.
"""

from supertrend_optimizer.engine.result import BacktestResult
from supertrend_optimizer.engine.run import run_single_backtest

__all__ = ["BacktestResult", "run_single_backtest"]

