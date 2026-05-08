"""
Grid enumeration — deterministic grid point generation.

§4.1  Canonical multiplier via integer-backed ticks:
    tick = round(mult / step)
    canonical_mult = tick * step

This eliminates cross-platform float accumulation drift.
All downstream layers (grid_point_id, backtest calls, export) use the same
canonical_mult produced here — single source of truth per §4.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wf_grid.config.schema import GridConfig


@dataclass(frozen=True)
class GridPoint:
    """Single grid search parameter combination (§2.1)."""

    atr_period: int
    multiplier: float       # canonical multiplier (integer-backed tick)
    trade_mode: str
    grid_point_id: str


def _canonical_multiplier(mult_raw: float, step: float) -> float:
    """
    Convert a raw multiplier float to its canonical integer-tick form.

    Formula: tick = round(mult_raw / step); canonical = tick * step.

    This is the single conversion point used for every multiplier value
    in the grid.  Calling it with the same inputs on any platform produces
    the same canonical float, because round() is deterministic and the
    integer multiplication avoids accumulation error.
    """
    tick = round(mult_raw / step)
    return tick * step


def enumerate_grid(config: "GridConfig") -> list[GridPoint]:
    """
    Generate all (atr_period, multiplier, trade_mode) combinations
    in a deterministic, reproducible order.

    Multipliers are enumerated via integer ticks, not float accumulation,
    guaranteeing cross-platform stability of grid_point_id values.

    Order: atr_period ASC → multiplier ASC → trade_mode (insertion order
    matching config, or single mode string).

    Parameters
    ----------
    config:
        Validated GridConfig.

    Returns
    -------
    list[GridPoint]
        All grid points.  Length = |atr_range| * |mult_ticks| * |trade_modes|.
    """
    opt = config.optimization

    atr_min, atr_max = int(opt.atr_period_range[0]), int(opt.atr_period_range[1])
    mult_min = float(opt.multiplier_range[0])
    mult_max = float(opt.multiplier_range[1])
    step = float(opt.multiplier_step)

    # Resolve trade modes: config stores a single string; downstream can use
    # a list if needed, but for Phase A the config field is a single mode.
    trade_modes = _resolve_trade_modes(opt.trade_mode)

    # Build canonical tick range for multipliers.
    tick_min = round(mult_min / step)
    tick_max = round(mult_max / step)

    atr_period_step = int(opt.atr_period_step)

    points: list[GridPoint] = []
    for atr in _atr_values(atr_min, atr_max, atr_period_step):
        for tick in range(tick_min, tick_max + 1):
            canonical_mult = tick * step
            for mode in trade_modes:
                gid = f"atr{atr}_m{canonical_mult:.2f}_{mode}"
                points.append(GridPoint(
                    atr_period=atr,
                    multiplier=canonical_mult,
                    trade_mode=mode,
                    grid_point_id=gid,
                ))

    return points


def _atr_values(atr_min: int, atr_max: int, atr_period_step: int) -> list[int]:
    """Return sorted list of ATR period values for the grid.

    Generates atr_min, atr_min + step, atr_min + 2*step, … while <= atr_max,
    then appends atr_max if it was not already included.  This guarantees
    atr_max is always present (the "tail" may be shorter than step).

    Returns an empty list when atr_min > atr_max (defensive guard; the loader
    rejects such configs with ConfigError before this is reached).
    """
    if atr_min > atr_max:
        return []
    values = list(range(atr_min, atr_max + 1, atr_period_step))
    if not values or values[-1] != atr_max:
        values.append(atr_max)
    return values


def _resolve_trade_modes(trade_mode_cfg: str) -> list[str]:
    """Return ordered list of trade modes from config value (single string)."""
    return [trade_mode_cfg]
