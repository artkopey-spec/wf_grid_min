"""Executed legs on observed calendar boundaries and their window economics."""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DateExecution:
    """Slice-aligned eligibility at open and obligatory liquidation at close."""

    entry: np.ndarray
    boundary: np.ndarray

    def __post_init__(self):
        for values in (self.entry, self.boundary):
            if values.ndim != 1 or values.dtype != np.bool_:
                raise ValueError("date_filter masks must be one-dimensional boolean arrays")
        if self.entry.shape != self.boundary.shape or np.any(self.entry & self.boundary):
            raise ValueError("date_filter masks have inconsistent lengths or overlapping phases")

    def slice(self, start, end):
        if start < 0 or end > len(self.entry) or len(self.entry) != len(self.boundary):
            raise ValueError("date_filter slice does not match the OHLC range")
        return DateExecution(self.entry[start:end], self.boundary[start:end])

    def at_open(self, bar, current, target):
        if target == current or self.entry[bar]:
            return target
        return 0


def executed_legs(positions, dates, opens, closes, index, trend, offset, commission):
    """positions is exposure after open; a boundary liquidates it at close."""
    after_close = positions.copy()
    after_close[dates.boundary] = 0
    previous = np.zeros_like(positions)
    previous[1:] = after_close[:-1]
    changed = previous != positions
    starts = np.flatnonzero(changed & (positions != 0))
    open_exits = changed & (previous != 0)
    close_exits = dates.boundary & (positions != 0)
    mark = np.zeros_like(close_exits)
    if len(mark):
        mark[-1] = after_close[-1] != 0
    # A reversal on the final open can close one trade and mark another.
    bars, kinds = np.nonzero(np.column_stack((open_exits, close_exits, mark)))
    phases = np.array(["open", "close", "mark_open"])[kinds]
    side = positions[starts]
    price = np.where(kinds == 1, closes[bars], opens[bars])
    gross = np.round(side * ((price - opens[starts]) / opens[starts]) * 100, 6)
    sides = 1 + (kinds != 2).astype(int)
    # Match the journal's six-decimal arithmetic even when the fee per
    # side has more precision. Legacy open reversals book both sides at
    # exit, add this trade's entry fee, then transfer the new entry side.
    reversal = (kinds == 0) & (positions[bars] != 0)
    side_pct = commission * 100
    exit_sides = (kinds != 2).astype(int) + reversal
    exit_fee = np.round(exit_sides * side_pct, 6)
    fee = np.round(exit_fee + side_pct, 6)
    net = np.round(np.round(gross - exit_fee, 6) - side_pct, 6)
    fee[reversal] = np.round(fee[reversal] - side_pct, 6)
    net[reversal] = np.round(net[reversal] + side_pct, 6)
    color = np.where(trend[np.maximum(starts - 1, 0)] == 1, "GREEN", "RED")
    return pd.DataFrame({
        "trade_id": np.arange(1, len(starts) + 1),
        "direction": np.where(side == 1, "LONG", "SHORT"),
        "entry_index": starts, "entry_global_index": starts + offset,
        "entry_time": index.take(starts), "entry_price": np.round(opens[starts], 6),
        "exit_index": bars, "exit_global_index": bars + offset,
        "exit_time": index.take(bars), "exit_price": np.round(price, 6),
        "exit_phase": phases, "bars_held": bars - starts,
        "commission_sides": sides, "commission_pct": fee, "gross_pnl_pct": gross,
        "net_pnl_pct": net,
        "supertrend_color": np.where(starts == 0, "UNKNOWN", color),
    })


def project_window(legs, opens, closes, commission, start=0):
    """Project only trades entered in the sample onto its N-1 slots."""
    selected = legs.loc[legs["entry_index"] >= start]
    entry = selected["entry_index"].to_numpy(dtype=int) - start
    exit_bar = selected["exit_index"].to_numpy(dtype=int) - start
    phase = selected["exit_phase"].to_numpy()
    side = np.where(selected["direction"] == "LONG", 1, -1)
    n = len(opens) - start
    delta = np.zeros(n + 1, dtype=int)
    np.add.at(delta, entry, side)
    np.add.at(delta, exit_bar + (phase != "open"), -side)
    position = np.cumsum(delta[:-1])
    if n < 2:
        return np.empty(0), position
    prices = np.asarray(opens[start:], dtype=np.float64)
    right = prices[1:].copy()
    forced = exit_bar[phase == "close"]
    interior = forced[forced < n - 1]
    right[interior] = closes[start + interior]
    returns = position[:-1] * (right / prices[:-1] - 1)
    np.subtract.at(returns, np.maximum(0, entry - 1), commission)
    np.subtract.at(returns, np.maximum(0, exit_bar[phase == "open"] - 1), commission)
    np.subtract.at(returns, interior, commission)
    if np.any(forced == n - 1):
        terminal = position[-1] * (closes[-1] / prices[-1] - 1) - commission
        returns[-1] = (1 + returns[-1]) * (1 + terminal) - 1
    return returns, position
