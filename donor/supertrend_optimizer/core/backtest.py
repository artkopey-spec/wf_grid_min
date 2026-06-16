"""
Backtesting module for SuperTrend strategy.

This module implements position generation and backtesting logic.

WP7 changes
-----------
- ``run_backtest_fast`` now returns ``RawBacktestArtifacts`` instead of
  the legacy 7-tuple.  All active call-sites in ``donor/**`` have been
  migrated.  Inactive subtrees (``donor TESTER/``, ``donor zigzag/``)
  are declared **Mode B** (quarantine) — see §8.1.1 of the plan.
- On the enabled ZigZag ST filter path, ``generate_positions`` is NOT
  called; the FSM in ``zigzag_st_filter.apply(...)`` is the sole source
  of truth for ``filtered_positions`` (plan §2.2, §8.3).
- Early-exit synchronous truncation of ``filter_diagnostics`` (§8.2
  rule 7): when ``early_exit=True`` the diagnostic arrays are sliced to
  the same ``[:exit_bar+1]`` extent as ``positions`` / ``trend`` before
  returning ``RawBacktestArtifacts``.

Plan reference:  §2.2, §8.1, §8.2, §8.3.
Spec reference:  Appendix A v1.1 §10, §13, §16, §17.1, §17.6, §17.13.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np

from supertrend_optimizer.core.calculator import calculate_supertrend
from supertrend_optimizer.core.trade_filter_config import (
    is_trade_filter_enabled,
    is_volume_enabled,
    is_zigzag_enabled,
)
from supertrend_optimizer.utils.enums import ExecutionModel


@dataclass
class RawBacktestArtifacts:
    """Transport dataclass from ``run_backtest_fast`` to ``run_single_backtest``.

    Replaces the legacy 7-tuple return value.  Identical fields on the
    disabled path (``filter_diagnostics=None``); on the enabled path
    ``filter_diagnostics`` carries the ZigZag ST FSM per-bar arrays
    already trimmed to the same length as ``positions`` (including any
    early-exit truncation, §8.2 rule 7).

    Plan reference:  §3.2, §8.2.
    Spec reference:  Appendix A v1.1 §10, §13.
    """
    returns: np.ndarray
    equity_curve: np.ndarray
    trend: np.ndarray
    positions: np.ndarray
    early_exit: bool
    exit_bar: "Optional[int]"
    exit_drawdown: "Optional[float]"
    filter_diagnostics: "Optional[Dict[str, np.ndarray]]" = field(default=None)
    filter_config_snapshot: Optional[dict] = None


def generate_positions(
    trend: np.ndarray,
    trade_mode: str,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN
) -> np.ndarray:
    """
    Generate trading positions based on trend.

    Execution model: OPEN_TO_OPEN only.
    - Signal at bar t → execute at bar t+1 open
    - 1-bar lag: positions[t+1] = trend[t]
    - positions[0] = 0 (no position on first bar, lag shifts everything right)

    CLOSE_TO_CLOSE has been removed.  It had an inherent look-ahead bias
    (signal derived from close[t], execution also at close[t]).

    Args:
        trend: Trend direction array (1 = uptrend, -1 = downtrend, 0 = warmup)
        trade_mode: Trading mode ("revers"/"both", "long", or "short")
        execution_model: Must be ExecutionModel.OPEN_TO_OPEN (only supported value)

    Returns:
        Positions array (dtype int8):
        - 0 = no position
        - 1 = long position
        - -1 = short position

    Raises:
        ValueError: If trade_mode is invalid or execution_model is not OPEN_TO_OPEN
    """
    n = len(trend)
    positions = np.zeros(n, dtype=np.int8)

    # Validate trade_mode
    valid_modes = {"revers", "both", "long", "short"}
    if trade_mode not in valid_modes:
        raise ValueError(f"trade_mode must be one of {valid_modes}, got: {trade_mode}")

    # Validate execution model
    if execution_model != ExecutionModel.OPEN_TO_OPEN:
        raise ValueError(
            f"ExecutionModel.{execution_model} is not supported. "
            "Only OPEN_TO_OPEN is allowed. "
            "CLOSE_TO_CLOSE was removed due to look-ahead bias."
        )

    # 1-bar lag: positions[1:] = trend[:-1], positions[0] = 0 (already set)
    if trade_mode in {"revers", "both"}:
        positions[1:] = trend[:-1]
    elif trade_mode == "long":
        positions[1:] = np.where(trend[:-1] == 1, 1, 0)
    elif trade_mode == "short":
        positions[1:] = np.where(trend[:-1] == -1, -1, 0)

    return positions


def calculate_returns(
    open_prices: np.ndarray,
    positions: np.ndarray,
    commission: float,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    close_prices: np.ndarray | None = None
) -> np.ndarray:
    """
    Calculate returns with commission costs.

    Execution model: OPEN_TO_OPEN only.
    - PnL basis: open[i+1] / open[i] - 1
    - positions[i] holds the position active during bar i, capturing
      the open[i] → open[i+1] price move.

    Commission is applied on every position change:
    - 0 → ±1 or ±1 → 0  counts as ONE operation  (abs(diff) = 1)
    - +1 → -1 or -1 → +1  (reversal) counts as TWO  (abs(diff) = 2)

    Commission formula:
        commission_costs[i] = abs(positions[i+1] - positions[i]) * commission

    Commission timing note:
        commission_costs[i] is debited from returns[i], even though the
        physical execution of the transition positions[i] → positions[i+1]
        takes place at the open of bar i+1.  This one-bar shift is a standard
        simplification in vectorized backtesting; it preserves total PnL
        correctness while keeping the implementation O(n).

    **IMPORTANT**: This commission model does not account for:
    - Bid-ask spread (especially for intraday)
    - Slippage modeling
    - Market impact or time-of-day effects

    Args:
        open_prices: Open prices array
        positions: Positions array (0, 1, or -1)
        commission: Commission rate per operation
        execution_model: Must be ExecutionModel.OPEN_TO_OPEN (only supported value)
        close_prices: Unused; kept for API compatibility (ignored)

    Returns:
        Returns array (length = len(open_prices) - 1)

    Raises:
        ValueError: If execution_model is not OPEN_TO_OPEN
    """
    if execution_model != ExecutionModel.OPEN_TO_OPEN:
        raise ValueError(
            f"ExecutionModel.{execution_model} is not supported. "
            "Only OPEN_TO_OPEN is allowed. "
            "CLOSE_TO_CLOSE was removed due to look-ahead bias."
        )

    n = len(open_prices)
    returns_len = n - 1

    # Price returns: open[i+1] / open[i] - 1
    price_changes = open_prices[1:] / open_prices[:-1] - 1.0

    # Apply positions (using first returns_len positions)
    price_ret = price_changes * positions[:returns_len]

    # Commission costs: abs(diff(positions)) * commission
    position_changes = np.diff(positions[:returns_len + 1])
    commission_costs = np.abs(position_changes) * commission

    return price_ret - commission_costs


def calculate_equity_curve(
    returns: np.ndarray
) -> np.ndarray:
    """
    Calculate equity curve from returns.

    Equity curve starts at 1.0 and compounds returns:
        equity[0] = 1.0
        equity[i] = equity[i-1] * (1 + returns[i-1])

    Floor protection:
        If any return is <= -1.0 (loss >= 100 %), the cumulative product can
        reach zero or go negative, which breaks downstream calculations
        (division by zero in drawdown, invalid CAGR).  Any equity value that
        falls to <= 0 is clamped to EQUITY_FLOOR = 1e-10 so that:
        - len(equity) stays len(returns) + 1 (invariant preserved)
        - max_drawdown remains finite
        - CAGR returns INVALID_METRIC_VALUE (equity[-1] ~ 1e-10 ≈ 0 triggers
          the <= 0 guard in calculate_cagr)

        A WARNING should be issued upstream when returns < -1 appear, as this
        signals either extreme leverage, bad data, or a commission larger than
        the position's value.

    Args:
        returns: Returns array

    Returns:
        Equity curve array (length = len(returns) + 1)
    """
    _EQUITY_FLOOR = 1e-10

    n = len(returns)
    equity = np.zeros(n + 1, dtype=np.float64)

    equity[0] = 1.0
    equity[1:] = np.cumprod(1.0 + returns)

    # Clamp non-positive values to floor to prevent division-by-zero downstream
    if np.any(equity <= 0):
        equity = np.maximum(equity, _EQUITY_FLOOR)

    return equity


def check_early_exit(
    equity_curve: np.ndarray,
    max_drawdown: float,
    check_bars: int
) -> tuple[bool, int | None, float | None]:
    """
    Check for early exit condition based on drawdown.

    Early exit is triggered when the running drawdown first exceeds the
    threshold within the first check_bars bars of the equity curve.

    Drawdown at bar i:
        dd[i] = equity[i] / cummax(equity[0:i+1]) - 1

    Trigger condition:
        dd[i] < -max_drawdown   (max_drawdown is a positive threshold,
                                 e.g. 0.5 means −50 %)

    Edge-case — max_drawdown = 0:
        bar 0 always has dd[0] = equity[0] / equity[0] − 1 = 0.0, so the
        condition 0 < -0 is False and bar 0 never triggers early exit.
        The first bar where ANY decline occurs (dd[i] < 0) will trigger it.
        If equity is flat or monotonically increasing, early exit never fires.

    Edge-case — max_drawdown < 0:
        Raises ValueError; negative threshold has no meaning.

    Args:
        equity_curve: Equity curve array (equity[0] should be 1.0)
        max_drawdown: Maximum allowed drawdown as a positive fraction
                      (e.g., 0.5 for −50 %).  Must be >= 0.
        check_bars: Number of bars to check (from bar 0)

    Returns:
        Tuple of (is_early_exit, exit_bar, exit_drawdown):
        - is_early_exit: True if early exit was triggered
        - exit_bar: Bar index in equity_curve where exit occurred (or None)
        - exit_drawdown: Drawdown value at exit bar, always <= 0 (or None)

    Raises:
        ValueError: If max_drawdown < 0
    """
    if max_drawdown < 0:
        raise ValueError(
            f"max_drawdown must be >= 0, got {max_drawdown}. "
            "Pass a positive threshold, e.g. 0.5 for a −50 % limit."
        )
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
    precomputed_atr: "Optional[np.ndarray]" = None,
    # NEW (WP7 / Phase 1) — ZigZag ST filter
    trade_filter_config: Any = None,
    zigzag_global_stats: Any = None,
    volume_runtime: Any = None,
    global_offset: int = 0,
    index: Any = None,                        # NEW: trailing optional for daily_reset
    volume: Any = None,
    *,
    collect_filter_diagnostics: bool = True,
) -> RawBacktestArtifacts:
    """Run fast backtest without DataFrame overhead.

    Returns a ``RawBacktestArtifacts`` dataclass (WP7) instead of the
    legacy 7-tuple so callers can be migrated explicitly and a grep-gate
    can enforce the absence of stale 7-value unpacks (plan §8.1.1).

    Disabled / None path is **bit-identical** to the legacy behaviour —
    same positions, returns, equity, and ``filter_diagnostics=None``.

    Enabled path (``trade_filter_config.enabled=True``):
    - ``generate_positions`` is NOT called.
    - ``zigzag_st_filter.apply(...)`` is the sole source of
      ``filtered_positions`` (plan §2.2, §8.3).
    - ``filter_diagnostics`` carries the FSM per-bar arrays trimmed to
      the same length as ``positions`` (§8.2 rule 7).

    Args
    ----
    open_prices, high, low, close : np.ndarray
        OHLC price arrays of the same length.
    atr_period : int
        ATR look-back period.
    multiplier : float
        ATR multiplier for SuperTrend bands.
    trade_mode : str
        ``"revers"`` / ``"both"`` / ``"long"`` / ``"short"``.
    commission : float
        Commission rate per operation.
    early_exit_enabled : bool
        Whether to check for maximum-drawdown early exit.
    early_exit_max_drawdown : float
        Maximum allowed drawdown fraction (positive, e.g. 0.5 = -50 %).
    early_exit_check_bars : int
        Number of bars from bar 0 to check for early exit.
    execution_model : ExecutionModel
        Must be ``OPEN_TO_OPEN`` (only supported value).
    precomputed_atr : np.ndarray, optional
        Pre-calculated ATR array (skip redundant ATR calculation, F-21b).
    trade_filter_config : Any, optional
        Duck-typed ``TradeFilterConfig`` (WP2-validated).  ``None`` or
        ``enabled=False`` → disabled path.
    zigzag_global_stats : Any, optional
        ``ZigZagGlobalStats`` computed once on the full dataset (WP3).
    global_offset : int, optional
        Bar offset of the slice start in the full dataset (metadata only).

    Returns
    -------
    RawBacktestArtifacts
        All arrays are already truncated when ``early_exit=True``.
        ``filter_diagnostics`` is ``None`` on the disabled path and a
        ``dict[str, np.ndarray]`` (same length as ``positions``) on the
        enabled path.
    """
    # Validate open_prices (high/low/close are validated inside calculate_supertrend)
    if not np.all(np.isfinite(open_prices)):
        raise ValueError(
            "'open_prices' contains NaN or Inf values. "
            "Clean the input data before calling run_backtest_fast."
        )
    if np.any(open_prices <= 0):
        raise ValueError(
            "'open_prices' contains non-positive values (<= 0). "
            "All price values must be strictly positive."
        )

    # Step 1: Calculate SuperTrend (F-21b: pass precomputed_atr if available)
    trend, supertrend = calculate_supertrend(
        high, low, close, atr_period, multiplier, precomputed_atr=precomputed_atr
    )

    # Step 2: Positions — enabled path uses FSM; disabled path uses generate_positions.
    filter_diagnostics: "Optional[Dict[str, np.ndarray]]" = None
    filter_config_snapshot: Optional[dict] = None
    zigzag_enabled = is_zigzag_enabled(trade_filter_config)
    volume_enabled = is_volume_enabled(trade_filter_config)
    filter_enabled = zigzag_enabled or volume_enabled
    zigzag_cfg = getattr(trade_filter_config, "zigzag", None)
    volume_cfg = getattr(trade_filter_config, "volume", None)
    subfilters_explicitly_disabled = (
        is_trade_filter_enabled(trade_filter_config)
        and getattr(zigzag_cfg, "enabled", None) is False
        and (volume_cfg is None or getattr(volume_cfg, "enabled", None) is False)
    )
    if subfilters_explicitly_disabled:
        raise RuntimeError(
            "at least one trade subfilter must be enabled when "
            "trade_filter.enabled=true"
        )
    if zigzag_enabled and zigzag_global_stats is None:
        raise RuntimeError(
            "zigzag_global_stats required when trade_filter.zigzag.enabled=true"
        )
    if volume_enabled and volume_runtime is None:
        raise RuntimeError("volume_runtime required when trade_filter.volume.enabled=true")

    if zigzag_enabled:
        # Lazy import to avoid circular dependency at module load time.
        from supertrend_optimizer.core.zigzag_st_filter import apply as _zz_apply
        filter_result = _zz_apply(
            close=close,
            high=high,
            low=low,
            trend=trend,
            trade_mode=trade_mode,
            trade_filter_config=trade_filter_config,
            zigzag_global_stats=zigzag_global_stats,
            open_prices=open_prices,
            global_offset=global_offset,
            execution_model=execution_model,
            index=index,                      # NEW: propogate for daily_reset
            volume_runtime=volume_runtime if volume_enabled else None,
            volume=volume,
            collect_filter_diagnostics=collect_filter_diagnostics,
        )
        positions = filter_result.positions
        filter_diagnostics = filter_result.filter_diagnostics
        filter_config_snapshot = getattr(filter_result, "filter_config_snapshot", None)
    elif volume_enabled:
        from supertrend_optimizer.core.volume_only_filter import apply as _volume_apply

        filter_result = _volume_apply(
            open_prices=open_prices,
            close=close,
            trend=trend,
            trade_mode=trade_mode,
            trade_filter_config=trade_filter_config,
            volume_runtime=volume_runtime,
            execution_model=execution_model,
            index=index,
            collect_filter_diagnostics=collect_filter_diagnostics,
        )
        positions = filter_result.positions
        filter_diagnostics = filter_result.filter_diagnostics
        filter_config_snapshot = filter_result.filter_config_snapshot
    else:
        # Disabled path — bit-identical to pre-WP7 baseline.
        positions = generate_positions(trend, trade_mode, execution_model)

    # Step 3: Calculate returns (open-to-open price basis)
    returns = calculate_returns(
        open_prices,
        positions,
        commission,
        execution_model=execution_model,
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

        # Step 5a: Truncate arrays if early exit triggered (§8.2 rule 7).
        if early_exit:
            if exit_bar is None:
                raise ValueError("exit_bar must be set when early_exit=True")
            if not (0 <= exit_bar < len(equity_curve)):
                raise ValueError(
                    f"exit_bar {exit_bar} out of range [0, {len(equity_curve)})"
                )

            returns = returns[:exit_bar]
            equity_curve = equity_curve[:exit_bar + 1]
            positions = positions[:exit_bar + 1]
            trend = trend[:exit_bar + 1]

            # Synchronous truncation of filter_diagnostics (§8.2 rule 7):
            # every diagnostic array is trimmed to the SAME length as
            # ``positions`` (= exit_bar + 1).
            if filter_diagnostics is not None:
                filter_diagnostics = {
                    k: v[:exit_bar + 1] for k, v in filter_diagnostics.items()
                }

            # Verify invariants
            if not (len(equity_curve) == len(positions) == len(trend)):
                raise ValueError(
                    f"Length mismatch: equity={len(equity_curve)}, "
                    f"positions={len(positions)}, trend={len(trend)}"
                )
            if len(equity_curve) != len(returns) + 1:
                raise ValueError(
                    f"Equity length {len(equity_curve)} != "
                    f"returns length {len(returns)} + 1"
                )
    else:
        early_exit = False
        exit_bar = None
        exit_dd = None

    return RawBacktestArtifacts(
        returns=returns,
        equity_curve=equity_curve,
        trend=trend,
        positions=positions,
        early_exit=early_exit,
        exit_bar=exit_bar,
        exit_drawdown=exit_dd,
        filter_diagnostics=filter_diagnostics,
        filter_config_snapshot=filter_config_snapshot,
    )
