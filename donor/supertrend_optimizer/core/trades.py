"""
Trades extraction module.

This module extracts detailed trade-by-trade information from backtest results.

CONTRACT: Simple Entry/Exit Returns (2026-02-09)
=================================================
Each trade's PnL is calculated using SIMPLE RETURN from entry to exit price:

    LONG:  gross_pnl_pct = (exit_price - entry_price) / entry_price * 100
    SHORT: gross_pnl_pct = (entry_price - exit_price) / entry_price * 100
    
    net_pnl_pct = gross_pnl_pct - commission_pct

This is NOT compound return (not sum of bar-level returns).
This ensures that sum(trades_df["net_pnl_pct"]) represents the simple sum
of individual trade returns, which is the source of truth for sum_pnl_pct metric.
"""

import numpy as np
from typing import List, Dict, Any
import pandas as pd


def extract_trades(
    positions: np.ndarray,
    returns: np.ndarray,
    execution_prices: np.ndarray,
    index: pd.Index,
    commission_rate: float,
    trend: np.ndarray | None = None,
    execution_model: str = "open_to_open"
) -> pd.DataFrame:
    """
    Extract detailed trades table from backtest results.

    A trade is a continuous period where position is constant and != 0.
    Reversal (+1 -> -1) = close trade + open new trade.

    Execution model: open_to_open only.  The parameter is kept for API
    compatibility but any value other than "open_to_open" raises ValueError.

    Input arrays synchronized with backtest:
    - returns: length = n
    - positions: length = n + 1 (includes position after last return bar)
    - execution_prices: length = n + 1 (open prices)
    - index: length = n + 1
    - trend: length = n + 1 (optional, SuperTrend direction: 1=uptrend/green, -1=downtrend/red)

    Commission is calculated using forward diff (same as backtest):
    - commission_per_bar[i] = abs(positions[i+1] - positions[i]) * commission_rate
    - For trade: sum commission_per_bar over trade interval [entry_idx, exit_idx)

    Trade interval is [entry_idx, exit_idx) — half-open, exit_idx not included.

    The main loop iterates over ALL n+1 position slots (indices 0..n), so every
    transition — including positions[n-1] → positions[n] (the last one) — is
    detected correctly.  A trade that opens at index n has bars_held=0 and
    zero price PnL (no returns exist beyond index n); it is recorded as a
    "pending" trade so that commission attribution and F-16 reversal split
    remain consistent with the backtest engine.

    Args:
        positions: Positions array (length = len(returns) + 1)
        returns: Returns array (with commission already applied)
        execution_prices: Execution prices array (open prices, length = len(returns) + 1)
        index: DataFrame index (datetime or integer, length = len(returns) + 1)
        commission_rate: Commission rate per operation
        trend: SuperTrend direction array (optional, length = len(returns) + 1)
        execution_model: Must be "open_to_open" (only supported value)

    Returns:
        DataFrame with trades, columns:
        - trade_id, direction, entry_time, entry_index, entry_price,
        - exit_time, exit_index, exit_price, bars_held,
        - gross_pnl_pct, commission_pct, net_pnl_pct, supertrend_color
    """
    n = len(returns)

    # Verify array lengths (synchronized with backtest)
    if len(positions) != n + 1:
        raise ValueError(
            f"positions length {len(positions)} != returns length + 1 ({n + 1})"
        )
    if len(execution_prices) != len(positions):
        raise ValueError(
            f"execution_prices length {len(execution_prices)} != positions length {len(positions)}"
        )
    if len(index) != len(positions):
        raise ValueError(
            f"index length {len(index)} != positions length {len(positions)}"
        )
    if trend is not None and len(trend) != len(positions):
        raise ValueError(
            f"trend length {len(trend)} != positions length {len(positions)}"
        )

    if execution_model != "open_to_open":
        raise ValueError(
            f"execution_model='{execution_model}' is not supported. "
            "Only 'open_to_open' is allowed. "
            "CLOSE_TO_CLOSE was removed due to look-ahead bias."
        )

    if n == 0:
        columns = [
            'trade_id', 'direction', 'entry_time', 'entry_index', 'entry_price',
            'exit_time', 'exit_index', 'exit_price', 'bars_held',
            'gross_pnl_pct', 'commission_pct', 'net_pnl_pct'
        ]
        if trend is not None:
            columns.append('supertrend_color')
        return pd.DataFrame(columns=columns)

    # Pre-calculate commission per bar using forward diff (synchronized with backtest).
    # commission_per_bar[i] covers the transition positions[i] → positions[i+1],
    # which corresponds to bar i in returns (length = n, indices 0..n-1).
    commission_per_bar = np.zeros(n, dtype=np.float64)
    for i in range(n):
        commission_per_bar[i] = abs(positions[i + 1] - positions[i]) * commission_rate

    # -----------------------------------------------------------------------
    # Main trade-extraction loop.
    #
    # Iterates over ALL n+1 position slots (i = 0 .. n) so that EVERY
    # transition — including the final positions[n-1] → positions[n] — is
    # handled uniformly.  No separate edge-case block is needed.
    #
    # When i == n the slot has no corresponding return bar; a trade opened
    # here will have bars_held = 0 (pending trade).
    # -----------------------------------------------------------------------
    trades: list = []
    trade_id = 0

    in_trade = False
    entry_idx = 0
    current_position = 0

    for i in range(n + 1):
        pos = positions[i]
        prev_pos = positions[i - 1] if i > 0 else 0

        if pos != prev_pos:
            # Close the running trade at bar i
            if in_trade and current_position != 0:
                trade_id += 1
                trade = _build_trade(
                    trade_id=trade_id,
                    direction="LONG" if current_position == 1 else "SHORT",
                    entry_idx=entry_idx,
                    exit_idx=i,
                    returns=returns,
                    commission_per_bar=commission_per_bar,
                    execution_prices=execution_prices,
                    index=index,
                    trend=trend,
                    execution_model=execution_model,
                )
                trades.append(trade)

            # Open a new trade if the new position is non-zero
            if pos != 0:
                in_trade = True
                entry_idx = i
                current_position = pos
            else:
                in_trade = False
                current_position = 0

    # Close the last trade if it is still open after the full loop
    # (position was held constant all the way through to bar n).
    if in_trade and current_position != 0:
        trade_id += 1
        trade = _build_trade(
            trade_id=trade_id,
            direction="LONG" if current_position == 1 else "SHORT",
            entry_idx=entry_idx,
            exit_idx=n,
            returns=returns,
            commission_per_bar=commission_per_bar,
            execution_prices=execution_prices,
            index=index,
            trend=trend,
            execution_model=execution_model,
        )
        trades.append(trade)

    # -----------------------------------------------------------------------
    # Unified commission post-processing (single pass).
    #
    # Two adjustments must be made after the main loop; combining them into
    # one pass eliminates the risk of double-counting that existed when two
    # separate loops ran sequentially.
    #
    # Adjustment 1 — Opening commission (flat → position transition):
    #   When a trade opens from flat (positions[entry-1] == 0) the
    #   commission for that entry bar lives at commission_per_bar[entry-1],
    #   which is OUTSIDE the trade interval [entry, exit).  Move it inside
    #   so that sum(trades.commission_pct) == sum(bar-level commission)*100.
    #
    # Adjustment 2 — Reversal commission split (F-16):
    #   At a ±1 → ∓1 reversal bar the position diff is ±2, so
    #   commission_per_bar[reversal_bar] == 2 × commission_rate.
    #   That bar falls inside the CLOSING trade's interval, charging the
    #   full 2× to the closer while the opener gets nothing.
    #   Fix: transfer exactly 1× rate from the closing trade to the
    #   opening trade.  Portfolio-level total commission is unchanged.
    #
    # These two conditions are MUTUALLY EXCLUSIVE:
    #   - Adj-1 fires when positions[entry-1] == 0  (came from flat)
    #   - Adj-2 fires when positions[entry-1] != 0  (came from opposite side)
    # Therefore running them in one loop with an elif is safe.
    # -----------------------------------------------------------------------
    for i, trade in enumerate(trades):
        entry = trade['entry_index']

        # --- Adjustment 1: opening commission from flat ---
        if entry > 0 and (entry - 1) < n and positions[entry - 1] == 0:
            opening_comm_pct = commission_per_bar[entry - 1] * 100.0
            trade['commission_pct'] = round(trade['commission_pct'] + opening_comm_pct, 6)
            trade['net_pnl_pct'] = round(trade['net_pnl_pct'] - opening_comm_pct, 6)
            # gross_pnl is price-based → unchanged; net↓ + comm↑ cancel out

        # --- Adjustment 2: reversal split (F-16) ---
        elif i > 0:
            closing = trades[i - 1]
            exit_idx = closing['exit_index']
            # Must be a back-to-back transition (same bar)
            if exit_idx == entry:
                # Guard: exit_idx must be an interior index of positions
                if 0 < exit_idx < len(positions):
                    pos_before = positions[exit_idx - 1]
                    pos_after = positions[exit_idx]
                    # Both non-zero and opposite signs → reversal
                    if (pos_before != 0 and pos_after != 0
                            and (pos_before > 0) != (pos_after > 0)):
                        half_comm_pct = commission_rate * 100.0
                        closing['commission_pct'] = round(
                            closing['commission_pct'] - half_comm_pct, 6)
                        closing['net_pnl_pct'] = round(
                            closing['net_pnl_pct'] + half_comm_pct, 6)
                        trade['commission_pct'] = round(
                            trade['commission_pct'] + half_comm_pct, 6)
                        trade['net_pnl_pct'] = round(
                            trade['net_pnl_pct'] - half_comm_pct, 6)

    # Conservation invariant: sum of per-trade commission must equal the sum
    # of all bar-level commission costs (both expressed in percent).
    if trades and commission_rate > 0:
        total_bar_comm_pct = float(np.sum(commission_per_bar) * 100.0)
        total_trade_comm_pct = sum(t['commission_pct'] for t in trades)
        if not abs(total_trade_comm_pct - total_bar_comm_pct) < 1e-4:
            raise ValueError(
                f"Commission conservation violated: "
                f"trades={total_trade_comm_pct:.6f}%, "
                f"bars={total_bar_comm_pct:.6f}%"
            )
    
    # Convert to DataFrame
    if trades:
        df = pd.DataFrame(trades)
    else:
        columns = [
            'trade_id', 'direction', 'entry_time', 'entry_index', 'entry_price',
            'exit_time', 'exit_index', 'exit_price', 'bars_held',
            'gross_pnl_pct', 'commission_pct', 'net_pnl_pct'
        ]
        if trend is not None:
            columns.append('supertrend_color')
        df = pd.DataFrame(columns=columns)
    
    return df


def _build_trade(
    trade_id: int,
    direction: str,
    entry_idx: int,
    exit_idx: int,
    returns: np.ndarray,
    commission_per_bar: np.ndarray,
    execution_prices: np.ndarray,
    index: pd.Index,
    trend: np.ndarray | None = None,
    execution_model: str = "open_to_open"
) -> Dict[str, Any]:
    """
    Build a single trade dictionary.

    Trade interval is [entry_idx, exit_idx) — half-open.

    Entry price  = execution_prices[entry_idx]
    Exit price   = execution_prices[exit_idx]   (always valid; caller guarantees
                   exit_idx < len(execution_prices))

    Gross PnL    = simple return from entry/exit prices (not compounded)
    Commission   = sum of commission_per_bar[entry_idx : exit_idx]
    Net PnL      = Gross PnL − Commission

    SuperTrend color:
        Signal bar = entry_idx − 1  (OPEN_TO_OPEN: signal at close of previous
        bar, execution at open of entry bar).
        Always written to trade_dict:
        - 'GREEN'   trend[signal_idx] == 1  (uptrend)
        - 'RED'     trend[signal_idx] == -1 (downtrend)
        - 'UNKNOWN' trend is None OR signal_idx is out of bounds
    """
    # Entry
    entry_price = execution_prices[entry_idx]
    entry_time = index[entry_idx]

    # Exit — exit_idx is guaranteed < len(execution_prices) by the caller
    if exit_idx >= len(execution_prices):
        raise ValueError(
            f"exit_idx {exit_idx} >= len(execution_prices) {len(execution_prices)}; "
            "this is a bug in extract_trades"
        )
    exit_price = execution_prices[exit_idx]
    exit_time = index[exit_idx]

    # Bars held
    bars_held = exit_idx - entry_idx

    # Gross PnL % — simple return from entry/exit prices
    position = 1 if direction == "LONG" else -1
    if position > 0:
        total_return = (exit_price - entry_price) / entry_price
    else:
        total_return = (entry_price - exit_price) / entry_price
    gross_pnl_pct = total_return * 100.0

    # Commission % — sum of commission per bar in trade interval [entry, exit)
    commission_pct = float(np.sum(commission_per_bar[entry_idx:exit_idx]) * 100.0)

    # Net PnL %
    net_pnl_pct = gross_pnl_pct - commission_pct

    trade_dict: Dict[str, Any] = {
        'trade_id': trade_id,
        'direction': direction,
        'entry_time': entry_time,
        'entry_index': entry_idx,
        'entry_price': round(entry_price, 6),
        'exit_time': exit_time,
        'exit_index': exit_idx,
        'exit_price': round(exit_price, 6),
        'bars_held': bars_held,
        'gross_pnl_pct': round(gross_pnl_pct, 6),
        'commission_pct': round(commission_pct, 6),
        'net_pnl_pct': round(net_pnl_pct, 6),
    }

    # SuperTrend color — always written (BUG-07 fix)
    # OPEN_TO_OPEN: signal bar is entry_idx - 1 (signal formed at close of
    # the bar before execution).
    if trend is not None:
        signal_idx = entry_idx - 1
        if 0 <= signal_idx < len(trend):
            trade_dict['supertrend_color'] = 'GREEN' if trend[signal_idx] == 1 else 'RED'
        else:
            trade_dict['supertrend_color'] = 'UNKNOWN'
    else:
        trade_dict['supertrend_color'] = 'UNKNOWN'

    return trade_dict

