"""
Backtest result data structure.

This module defines the unified result container for all backtest operations.

WP7 additions
-------------
- ``RawBacktestArtifacts``: dataclass transport from ``run_backtest_fast``
  to ``run_single_backtest``, replacing the legacy 7-tuple return.
- ``BacktestResult.filter_diagnostics``: optional per-bar diagnostic dict
  populated in the enabled ZigZag ST filter path (``None`` on disabled path).

Plan reference:  §3.2, §8.1, §8.2, §8.5.
Spec reference:  Appendix A v1.1 §10, §13, §17.6.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
import numpy as np
import pandas as pd
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE

# Re-exported here for import convenience; canonical definition is in
# core/backtest.py (avoids circular import between core/ and engine/).
from supertrend_optimizer.core.backtest import RawBacktestArtifacts  # noqa: F401


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
        - Based on AVAILABLE history (see DD-01 note below)
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

    DD-01: Trades считаются на доступной истории.
        - Warmup применяется ТОЛЬКО к ratio-метрикам (sharpe/sortino/cagr/max_drawdown).
        - Warmup — артефакт стабилизации индикатора; он не фильтрует trades.
        - При early_exit=False: trades считаются на полной исходной истории.
        - При early_exit=True: все массивы (returns, equity_curve, positions, trend)
          усечены до exit_bar. trades_df отражает только усечённую историю.
          n_bars_original хранит исходную длину до truncation для справки.

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

        returns: Returns array. Length = n_bars_original - 1 normally;
                 truncated to exit_bar if early_exit=True.
        equity_curve: Equity curve array (length = len(returns) + 1)
        positions: Positions array (length = len(returns) + 1)
        trend: Trend array (length = len(returns) + 1)

        metrics: Dictionary with all calculated metrics (see CONTRACT above).
                 Key reference:
                   sharpe, sortino, cagr      — ratio metrics (warmup-adjusted)
                   max_drawdown               — equity-based DD (warmup-adjusted)
                   num_trades                 — total trades in available history
                   win_rate                   — profitable trades, PERCENT 0–100
                   sum_pnl_pct                — sum of per-trade simple net returns, %
                   avg_trade                  — avg per-trade simple net return, %
                   profit_factor              — gross_profit / abs(gross_loss);
                                                MAX_VALID_METRIC if no losses (F-08)
                   net_pnl_pct                — ALIAS for avg_trade (per-trade average,
                                                NOT total equity PnL); kept for
                                                backward compatibility with Excel reports

        early_exit: True if early exit was triggered
        exit_bar: Bar index in equity_curve where early exit occurred (or None)
        exit_drawdown: Drawdown value at exit bar, always <= 0.0 (or None)

        trades_df: DataFrame with trades extracted from available history.
                   When early_exit=False: full input history.
                   When early_exit=True: history truncated at exit_bar.
                   Columns: trade_id, direction, entry_time, entry_index, entry_price,
                           exit_time, exit_index, exit_price, bars_held,
                           gross_pnl_pct, commission_pct, net_pnl_pct

        n_bars_original: Length of original input data before any truncation.
                         Use to detect early exit: early_exit implies
                         len(returns) < n_bars_original - 1.
        period_label: Period label for display ("100%", "50%", "30%", etc.)
        filter_diagnostics: Per-bar ZigZag ST filter diagnostic arrays (WP7).
                            ``None`` on disabled path.  When present, every
                            array satisfies ``len(arr) == len(positions)``.
                            This invariant is checked in ``__post_init__``
                            *after* any early-exit truncation (§8.2 rule 6).
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
    
    # Trades (DD-01: extracted from available history; truncated if early_exit=True)
    trades_df: Optional[pd.DataFrame]
    
    # Metadata
    n_bars_original: int
    period_label: str = ""
    effective_warmup: int = 0

    # ZigZag ST filter diagnostics (WP7).  None on disabled path.
    filter_diagnostics: "Optional[Dict[str, np.ndarray]]" = field(default=None)
    filter_config_snapshot: Optional[dict] = None

    def __post_init__(self) -> None:
        """Validate internal invariants after construction.

        Raises ValueError if any of the following are violated:
        - equity_curve, positions, trend must all have the same length
        - len(equity_curve) == len(returns) + 1
        - early_exit=True implies exit_bar is not None
        - filter_diagnostics (when present): every array length == len(positions)
          (invariant checked after early-exit truncation, §8.2 rule 6)
        """
        eq_len = len(self.equity_curve)
        pos_len = len(self.positions)
        trend_len = len(self.trend)
        ret_len = len(self.returns)

        if not (eq_len == pos_len == trend_len):
            raise ValueError(
                f"BacktestResult invariant violated: "
                f"equity_curve length {eq_len} != positions length {pos_len} "
                f"or trend length {trend_len}. All three must be equal."
            )
        if eq_len != ret_len + 1:
            raise ValueError(
                f"BacktestResult invariant violated: "
                f"equity_curve length {eq_len} != len(returns) + 1 = {ret_len + 1}."
            )
        if self.early_exit and self.exit_bar is None:
            raise ValueError(
                "BacktestResult invariant violated: "
                "early_exit=True but exit_bar is None. "
                "exit_bar must be set when early exit was triggered."
            )
        if self.filter_diagnostics is not None:
            for key, arr in self.filter_diagnostics.items():
                if len(arr) != pos_len:
                    raise ValueError(
                        f"BacktestResult invariant violated: "
                        f"filter_diagnostics[{key!r}] length {len(arr)} "
                        f"!= positions length {pos_len}. "
                        "All diagnostic arrays must match positions length."
                    )
            # §12.6 strict dtype contract for new exit-off arrays (when present).
            # This catches silent dtype drift early at result construction time.
            # Pre-existing exit-off keys keep ValueError for backward compat.
            _expected_dtypes_v_err = {
                "exit_off_mode": object,
                "exit_off_zz_leg_count": np.int64,
                "zz_legs_since_lifecycle_start": np.int64,
                "zz_leg_stop_triggered": np.int8,
            }
            for _k, _dt in _expected_dtypes_v_err.items():
                if _k not in self.filter_diagnostics:
                    continue
                _arr = np.asarray(self.filter_diagnostics[_k])
                if _arr.dtype != _dt:
                    raise ValueError(
                        f"BacktestResult dtype contract violated: "
                        f"filter_diagnostics[{_k!r}] dtype {_arr.dtype} != expected {_dt}."
                    )

            # Plan v3 §7: exit_b_immediate_off arrays — strict int8 → ConfigError
            # (per plan: "Несоответствие dtype → ConfigError на этапе
            # конструкции BacktestResult"). Imported lazily to avoid a hard
            # dependency at module import time.
            _expected_dtypes_cfg_err = {
                "exit_b_immediate_off_triggered": np.int8,
                "exit_b_immediate_off_config": np.int8,
            }
            _imm_violations = [
                (_k, np.asarray(self.filter_diagnostics[_k]).dtype, _dt)
                for _k, _dt in _expected_dtypes_cfg_err.items()
                if _k in self.filter_diagnostics
                and np.asarray(self.filter_diagnostics[_k]).dtype != _dt
            ]
            if _imm_violations:
                from supertrend_optimizer.utils.exceptions import ConfigError
                _msg = "; ".join(
                    f"filter_diagnostics[{_k!r}] dtype {_obs} != expected {_dt}"
                    for _k, _obs, _dt in _imm_violations
                )
                raise ConfigError(
                    f"BacktestResult dtype contract violated (Plan v3 §7): {_msg}"
                )
