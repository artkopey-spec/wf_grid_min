"""Shared test fixtures for exit_b_immediate_off (Plan v3 §10.2).

Single source of fixture data + duck-typed config doubles, used by BOTH
``wf_grid/tests/test_pr_exit_b_immediate_off.py`` and
``donor TESTER/tests/test_phase2_exit_b_immediate_off.py``.

This module exists specifically so the cross-branch parity test (§10.2.G)
runs on the SAME inputs in both branches; any drift between branches must
appear here rather than be hidden by branch-local copy/paste.

Plan reference: docs/Plan exit_b_immediate_off v3.txt §10.2 (intro, G).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    ZigZagPerBar,
)


# ---------------------------------------------------------------------------
# Duck-typed config doubles (single source of truth — both branches import these)
# ---------------------------------------------------------------------------

@dataclass
class ImmToggleDouble:
    enabled: bool = True


@dataclass
class ImmTriggersDouble:
    candidate_threshold: ImmToggleDouble = field(default_factory=ImmToggleDouble)
    confirmed_median: ImmToggleDouble = field(
        default_factory=lambda: ImmToggleDouble(enabled=False)
    )


@dataclass
class ImmZigZagDouble:
    daily_reset: bool = False
    local_window: int = 5
    mode: Optional[str] = None


@dataclass
class ImmLifecycleDouble:
    freeze_confirmed_legs: int = 0
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"
    exit_off_mode: str = "exit B"
    exit_off_zz_leg_count: Optional[int] = 2
    exit_b_immediate_off: object = False


@dataclass
class ImmFilterCfgDouble:
    zigzag: ImmZigZagDouble = field(default_factory=ImmZigZagDouble)
    triggers: ImmTriggersDouble = field(default_factory=ImmTriggersDouble)
    lifecycle: ImmLifecycleDouble = field(default_factory=ImmLifecycleDouble)


# ---------------------------------------------------------------------------
# Factories — apply()-level fixtures (per-bar arrays + config double)
# ---------------------------------------------------------------------------

def make_imm_cfg(
    *,
    exit_off_mode: str = "exit B",
    exit_off_zz_leg_count: Optional[int] = 2,
    exit_b_immediate_off: object = False,
    mode: Optional[str] = None,
    a_enabled: bool = True,
    b_enabled: bool = False,
) -> ImmFilterCfgDouble:
    return ImmFilterCfgDouble(
        zigzag=ImmZigZagDouble(mode=mode),
        triggers=ImmTriggersDouble(
            candidate_threshold=ImmToggleDouble(enabled=a_enabled),
            confirmed_median=ImmToggleDouble(enabled=b_enabled),
        ),
        lifecycle=ImmLifecycleDouble(
            exit_off_mode=exit_off_mode,
            exit_off_zz_leg_count=exit_off_zz_leg_count,
            exit_b_immediate_off=exit_b_immediate_off,
        ),
    )


def make_imm_stats(*, zigzag_mode: str = "A") -> ZigZagGlobalStats:
    """Default ZigZagGlobalStats used in §10.2 apply() tests."""
    return ZigZagGlobalStats(
        reversal_threshold=0.01,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=0.05,
        candidate_trigger_threshold=0.04,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
        zigzag_mode=zigzag_mode,
        candidate_duration_gate_enabled=False,
        candidate_duration_max_bars=None,
    )


def make_imm_per_bar(
    *,
    n: int,
    candidate_height_pct: Optional[np.ndarray] = None,
    confirm_event: Optional[np.ndarray] = None,
) -> ZigZagPerBar:
    if candidate_height_pct is None:
        candidate_height_pct = np.full(n, np.nan, dtype=np.float64)
    if confirm_event is None:
        confirm_event = np.zeros(n, dtype=np.int8)
    return ZigZagPerBar(
        candidate_height_pct=candidate_height_pct,
        confirm_event=confirm_event,
        confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
        last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
        local_median_N=np.full(n, np.nan, dtype=np.float64),
        local_median_available=np.zeros(n, dtype=bool),
        candidate_age_bars=np.full(n, -1, dtype=np.int64),
        candidate_leg_direction=np.zeros(n, dtype=np.int8),
    )


# Threshold scenario (used by §10.2 A/B/F/H/I)
# n=7; threshold at t=3 (zz=2 with count=2)
def imm_scenario_threshold_no_flip(
    *,
    immediate_off: bool,
    n: int = 7,
    mode: Optional[str] = None,
    a_enabled: bool = True,
    b_enabled: bool = False,
) -> Tuple[np.ndarray, ZigZagPerBar, ImmFilterCfgDouble, ZigZagGlobalStats, np.ndarray]:
    """Threshold at t=3, no flip on threshold bar, no flip after."""
    per_bar = make_imm_per_bar(
        n=n,
        candidate_height_pct=np.array(
            [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan]
        ),
        confirm_event=np.array([0, 0, 1, 1, 0, 0, 0], dtype=np.int8),
    )
    trend = np.array([-1, 1, 1, 1, 1, 1, 1], dtype=np.int64)
    cfg = make_imm_cfg(
        exit_off_mode="exit B",
        exit_off_zz_leg_count=2,
        exit_b_immediate_off=immediate_off,
        mode=mode,
        a_enabled=a_enabled,
        b_enabled=b_enabled,
    )
    return trend, per_bar, cfg, make_imm_stats(), np.zeros(n, dtype=bool)


def imm_scenario_threshold_with_flip(
    *,
    immediate_off: bool,
    n: int = 7,
) -> Tuple[np.ndarray, ZigZagPerBar, ImmFilterCfgDouble, ZigZagGlobalStats, np.ndarray]:
    """Threshold at t=3 AND opposite ST flip on the SAME bar t=3."""
    per_bar = make_imm_per_bar(
        n=n,
        candidate_height_pct=np.array(
            [np.nan, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan]
        ),
        confirm_event=np.array([0, 0, 1, 1, 0, 0, 0], dtype=np.int8),
    )
    trend = np.array([-1, 1, 1, -1, -1, -1, -1], dtype=np.int64)
    cfg = make_imm_cfg(
        exit_off_mode="exit B",
        exit_off_zz_leg_count=2,
        exit_b_immediate_off=immediate_off,
    )
    return trend, per_bar, cfg, make_imm_stats(), np.zeros(n, dtype=bool)


def imm_scenario_threshold_with_daily_reset(
    *,
    immediate_off: bool,
    reset_at: int = 3,
    n: int = 7,
) -> Tuple[np.ndarray, ZigZagPerBar, ImmFilterCfgDouble, ZigZagGlobalStats, np.ndarray]:
    """Threshold scenario where daily_reset fires on the threshold bar."""
    trend, per_bar, cfg, stats, _ = imm_scenario_threshold_no_flip(
        immediate_off=immediate_off, n=n
    )
    dr = np.zeros(n, dtype=bool)
    dr[reset_at] = True
    return trend, per_bar, cfg, stats, dr


# Position of the threshold bar in scenarios above (count=2 → zz reaches 2 at t=3)
IMM_THRESHOLD_T = 3


# ---------------------------------------------------------------------------
# Cross-branch parity fixture (§10.2.G) — synthetic OHLC + real TradeFilterConfig
# ---------------------------------------------------------------------------

# Seed and parameters are pinned here so wf_grid and donor TESTER pipelines
# consume an identical input. Any drift = test failure on parity.
PARITY_SEED = 20260506
PARITY_N_BARS = 100
PARITY_ATR = 14
PARITY_MULT = 3.0


def make_parity_ohlc(seed: int = PARITY_SEED, n: int = PARITY_N_BARS) -> pd.DataFrame:
    """Synthetic OHLC frame used by both branches in §10.2.G parity test."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.00035, 0.012, n)))
    noise = rng.uniform(0.001, 0.004, size=n)
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.clip(close * (1 + rng.uniform(-0.002, 0.002, size=n)), low, high)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close}, index=idx
    )


def make_parity_trade_filter_config(*, exit_b_immediate_off: bool):
    """Real TradeFilterConfig (not duck-typed double) used by both branches.

    Required for run_single_backtest / run_period that inspect dataclass fields
    via attribute access on the validated object.
    """
    from supertrend_optimizer.core.trade_filter_config import (
        TradeFilterConfig, TradeFilterZigZagConfig, TradeFilterTriggersConfig,
        TradeFilterLifecycleConfig, TradeFilterDiagnosticsConfig,
        TradeFilterTriggerToggleConfig,
    )
    return TradeFilterConfig(
        enabled=True,
        type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            reversal_threshold=0.03,
            local_window=20,
            candidate_trigger_threshold=0.4,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=False),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=0,
            stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
            exit_off_mode="exit B",
            exit_off_zz_leg_count=2,
            exit_b_immediate_off=exit_b_immediate_off,
        ),
        diagnostics=TradeFilterDiagnosticsConfig(
            export_state_columns=True,
            export_trigger_columns=True,
        ),
    )


__all__ = [
    "ImmToggleDouble",
    "ImmTriggersDouble",
    "ImmZigZagDouble",
    "ImmLifecycleDouble",
    "ImmFilterCfgDouble",
    "make_imm_cfg",
    "make_imm_stats",
    "make_imm_per_bar",
    "imm_scenario_threshold_no_flip",
    "imm_scenario_threshold_with_flip",
    "imm_scenario_threshold_with_daily_reset",
    "IMM_THRESHOLD_T",
    "PARITY_SEED",
    "PARITY_N_BARS",
    "PARITY_ATR",
    "PARITY_MULT",
    "make_parity_ohlc",
    "make_parity_trade_filter_config",
]
