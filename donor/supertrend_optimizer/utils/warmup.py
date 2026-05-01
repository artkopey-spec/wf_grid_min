"""
Warmup period calculation utilities.

This module provides functions for calculating warmup periods
used in both optimizer and tester.
"""

import copy
from typing import Any, Dict

from supertrend_optimizer.utils.constants import (
    DEFAULT_WARMUP_FRACTION,
    MIN_AUTO_WARMUP,
    MAX_AUTO_WARMUP,
    DEFAULT_ATR_PERIOD_MAX,
)
from supertrend_optimizer.utils.exceptions import ConfigError


def clamp(value: int, min_val: int, max_val: int) -> int:
    """
    Clamp value to [min_val, max_val] range.
    
    Args:
        value: Value to clamp
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        
    Returns:
        Clamped value
    """
    return max(min_val, min(value, max_val))


def calculate_warmup(n: int, cfg: Dict[str, Any]) -> int:
    """
    Calculate warmup period based on config and data length.
    
    Formula:
        base = max(cfg.validation.warmup_period, atr_period_max)

        if warmup_period_auto:
            auto = clamp(int(n * 0.10), 100, 400)
            auto = max(auto, atr_period_max)
            warmup = max(base, auto)
        else:
            warmup = base
    
    Args:
        n: Number of data points
        cfg: Configuration dictionary
        
    Returns:
        Calculated warmup period
    """
    opt = cfg.get("optimization", {})
    atr_range = opt.get("atr_period_range")

    if atr_range is not None:
        if not isinstance(atr_range, (list, tuple)) or len(atr_range) < 2:
            raise ConfigError(
                f"optimization.atr_period_range must be a list [min, max] with two "
                f"elements, got: {atr_range!r}. "
                "Example: atr_period_range: [5, 55]"
            )
        try:
            atr_period_max = int(atr_range[1])
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"optimization.atr_period_range[1] (max) must be an integer, "
                f"got: {atr_range[1]!r}"
            ) from exc
    else:
        atr_period_max = DEFAULT_ATR_PERIOD_MAX

    warmup_period = cfg.get("validation", {}).get("warmup_period", 0)
    base = max(warmup_period, atr_period_max)
    
    if cfg.get("validation", {}).get("warmup_period_auto", False):
        auto = clamp(int(n * DEFAULT_WARMUP_FRACTION), MIN_AUTO_WARMUP, MAX_AUTO_WARMUP)
        auto = max(auto, atr_period_max)
        warmup = max(base, auto)  # FIX: Use max() instead of addition to match tester logic
    else:
        warmup = base
    
    return warmup


def apply_auto_warmup_to_config(cfg: Dict[str, Any], n: int) -> Dict[str, Any]:
    """
    Return a shallow-copied config with ``validation.warmup_period`` resolved.

    The original *cfg* is **never mutated**.  When
    ``validation.warmup_period_auto`` is ``True``, the Variant-A formula is
    applied (10 % of *n*, clamped to [MIN_AUTO_WARMUP, MAX_AUTO_WARMUP], at
    least ``atr_period_max``).  Otherwise the existing ``warmup_period`` is
    kept as-is.

    This function is the single source of truth for warmup resolution so that
    direct callers of :func:`run_walk_forward` or
    :func:`run_single_optimization` get the same behaviour as the CLI entry
    point.

    Parameters
    ----------
    cfg:
        Full configuration dictionary.
    n:
        Number of bars in the dataset (used for the 10 % calculation).

    Returns
    -------
    Dict[str, Any]
        Effective config with ``validation.warmup_period`` set to the
        resolved value.

    Examples
    --------
    >>> cfg = {"validation": {"warmup_period": 0, "warmup_period_auto": True},
    ...        "optimization": {"atr_period_range": [10, 20]}}
    >>> result = apply_auto_warmup_to_config(cfg, n=2000)
    >>> result["validation"]["warmup_period"]  # max(20, clamp(200, 100, 400)) -> 200
    200
    >>> cfg["validation"]["warmup_period"]  # original untouched
    0
    """
    resolved = calculate_warmup(n, cfg)
    cfg_eff = copy.copy(cfg)
    cfg_eff["validation"] = {**cfg.get("validation", {}), "warmup_period": resolved}
    return cfg_eff


def calculate_warmup_tester(n: int, atr_period: int, warmup_period_auto: bool = False) -> int:
    """
    Calculate warmup period for tester CLI only.

    Simplified version that takes a single atr_period instead of a full config dict.

    Args:
        n: Number of data points.
        atr_period: ATR period used as the minimum warmup.
        warmup_period_auto: If True, use auto-warmup (10 % of n, clamped to
            [MIN_AUTO_WARMUP, MAX_AUTO_WARMUP], at least atr_period).

    Returns:
        Calculated warmup period.
    """
    if warmup_period_auto:
        auto = clamp(int(n * DEFAULT_WARMUP_FRACTION), MIN_AUTO_WARMUP, MAX_AUTO_WARMUP)
        return max(auto, atr_period)
    return atr_period


