"""
Tester runner module.

This module orchestrates the testing process using existing core modules.
NO math logic here - only orchestration.

WP-T4 additions (Phase 2 — legacy runner filter integration)
------------------------------------------------------------
- ``run_period`` / ``run_all_periods`` accept optional ``trade_filter_config``,
  ``zigzag_global_stats``, ``global_offset`` and wire them into
  ``run_single_backtest`` (plan §6.1).
- ``run_all_periods`` materialises ``zigzag_global_stats`` from the full ``df``
  when enabled and stats not supplied by caller (plan §7.3).
- Per-period ``period_global_offset = len(df) - n_period`` computed in
  ``run_all_periods`` and forwarded to ``run_period`` (plan §4.1).
- ``PeriodResult`` gains two optional fields:
  ``filter_diagnostics``         — bar-level dict spec §13, or None
  ``filter_diagnostics_summary`` — per-period aggregate dict, or None
- Disabled path (``trade_filter_config is None`` or ``enabled=False``):
  both new fields are ``None``; all other metrics are bit-identical to the
  pre-Phase-2 baseline.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §6, §7.3
Spec reference: Appendix A v1.1 §10, §12, §13
"""

import math
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from supertrend_optimizer.core._fsm_state_names import (
    FSM_STATE_NAMES as _FSM_STATES,
    ACTIVE_LIFECYCLE_STATES as _ACTIVE_LIFECYCLE_STATES,
    STANDALONE_VOLUME_ACTIVE_STATES as _VOLUME_ACTIVE_STATES,
    STANDALONE_VOLUME_STATE_NAMES as _VOLUME_STATES,
)
from supertrend_optimizer.core.backtest import generate_positions
from supertrend_optimizer.core.metrics import calculate_all_metrics
from supertrend_optimizer.core.trade_filter_config import (
    is_trade_filter_enabled,
    is_volume_enabled,
    is_zigzag_enabled,
    resolve_trade_filter_mode_in_place,
)
from supertrend_optimizer.core.volume_metrics import VolumeRuntime
from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
from supertrend_optimizer.data.loader import load_ohlc_csv
from supertrend_optimizer.data.validator import validate_ohlc_data
from supertrend_optimizer.engine.result import BacktestResult
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError


@dataclass
class PeriodResult:
    """
    Container for single period backtest results.

    This is a thin wrapper over BacktestResult with period-specific metadata.
    DD-01: Trades are extracted from full history (via BacktestResult.trades_df).

    WP-T4 additions (Phase 2):

    filter_diagnostics:
        Bar-level ZigZag ST diagnostic arrays forwarded from
        ``BacktestResult.filter_diagnostics``.  ``None`` on the disabled path.
        When present every array satisfies ``len(arr) == len(result.positions)``
        (invariant enforced by ``BacktestResult.__post_init__``; carried here
        by reference, not copied).

    filter_diagnostics_summary:
        Per-period aggregate dict built by ``_build_filter_diagnostics_summary``
        immediately after ``run_single_backtest``.  ``None`` on the disabled
        path.  Structure: see plan §3.3.2.
    """
    period_label: str           # "100%", "75%", "50%", "33%", "25%"
    n_bars: int                 # Number of bars in this period
    result: BacktestResult      # Full backtest result

    # WP-T4 — ZigZag ST filter diagnostics (plan §3.3 / §6.1).
    # Both are None on the disabled path (plan §3.3.3).
    filter_diagnostics: Optional[Dict[str, np.ndarray]] = field(default=None)
    filter_diagnostics_summary: Optional[Dict[str, Any]] = field(default=None)
    filter_config_snapshot: Optional[dict] = None

    # Convenience accessors (delegate to result)
    @property
    def metrics(self) -> Dict[str, Any]:
        return self.result.metrics

    @property
    def atr_period(self) -> int:
        return self.result.atr_period

    @property
    def multiplier(self) -> float:
        return self.result.multiplier

    @property
    def trade_mode(self) -> str:
        return self.result.trade_mode

    @property
    def commission(self) -> float:
        return self.result.commission

    @property
    def warmup(self) -> int:
        return self.result.warmup

    @property
    def effective_warmup(self) -> int:
        return self.result.effective_warmup

    @property
    def trades_df(self) -> Optional[pd.DataFrame]:
        return self.result.trades_df


# Period configuration: (label, fraction)
# Order is intentional: do not sort or reorder.
# 0.33 is used intentionally (not 1/3) for deterministic slicing.
PERIOD_SPLITS = [
    ("100%", 1.00),
    ("75%", 0.75),
    ("50%", 0.50),
    ("33%", 0.33),  # intentional: 0.33, not 1/3
    ("25%", 0.25),
]


# ---------------------------------------------------------------------------
# WP-T4 private helpers — filter_diagnostics_summary construction
# ---------------------------------------------------------------------------

def _echo_thresholds(
    trade_filter_config: Any,
    zigzag_global_stats: Any,
) -> Dict[str, Any]:
    """Build the ``thresholds`` sub-dict for filter_diagnostics_summary.

    All scalar values are echoed from the validated config + materialised stats.
    ``candidate_trigger_source`` is REQUIRED (owner decision v0.5.1 §15 #7).

    Plan reference: §3.3.2 (thresholds block).
    """
    zz = trade_filter_config.zigzag
    lc = trade_filter_config.lifecycle
    return {
        "reversal_threshold": float(zz.reversal_threshold),
        "candidate_trigger_threshold": float(
            zigzag_global_stats.candidate_trigger_threshold
        ),
        "candidate_trigger_quantile": (
            float(zigzag_global_stats.candidate_trigger_quantile)
            if zigzag_global_stats.candidate_trigger_quantile is not None
            else None
        ),
        "candidate_trigger_source": zigzag_global_stats.candidate_trigger_source,
        "global_median": float(zigzag_global_stats.global_median),
        "local_window": int(zz.local_window),
        "freeze_confirmed_legs": int(lc.freeze_confirmed_legs),
        "exit_off_mode": str(getattr(lc, "exit_off_mode", "exit A")),
        "exit_off_zz_leg_count": (
            int(getattr(lc, "exit_off_zz_leg_count"))
            if str(getattr(lc, "exit_off_mode", "exit A")) == "exit B"
            and isinstance(getattr(lc, "exit_off_zz_leg_count", None), int)
            and not isinstance(getattr(lc, "exit_off_zz_leg_count", None), bool)
            else -1
        ),
        # Plan v3 §8 / §2.1: echo exit_b_immediate_off flag for Excel + parity
        "exit_b_immediate_off": bool(getattr(lc, "exit_b_immediate_off", False)),
        "zigzag_mode": getattr(zigzag_global_stats, "zigzag_mode", ""),
        "candidate_duration_gate_enabled": bool(
            getattr(zigzag_global_stats, "candidate_duration_gate_enabled", False)
        ),
        "candidate_duration_max_bars": (
            int(getattr(zigzag_global_stats, "candidate_duration_max_bars"))
            if getattr(zigzag_global_stats, "candidate_duration_max_bars", None) is not None
            else -1
        ),
    }


def _bars_in_state_histogram(
    filter_diagnostics: Dict[str, np.ndarray],
) -> Dict[str, int]:
    """Count bars in each FSM state from bar-level diagnostics.

    Plan reference: §3.3.2 (bars_in_state; sanity invariant 6).
    """
    state_arr = filter_diagnostics["trade_filter_state"]
    states = list(_FSM_STATES)
    if any(np.any(state_arr == s) for s in _VOLUME_STATES if s != "OFF"):
        states.extend(s for s in _VOLUME_STATES if s not in states)
    return {s: int(np.sum(state_arr == s)) for s in states}


def _compute_summary_counters(
    positions_raw: np.ndarray,
    positions_filtered: np.ndarray,
    filter_diagnostics: Dict[str, np.ndarray],
    trades_df: Optional[pd.DataFrame],
) -> Dict[str, int]:
    """Compute the ``counters`` sub-dict for filter_diagnostics_summary.

    ``positions_raw`` is the pre-filter positions array (from
    ``generate_positions``); used to derive ``raw_st_flips`` independently
    of the filter so that sanity invariant 1 is a real check.

    Plan reference: §3.3.2 (counters block + sanity invariants 1–5).
    """
    # raw_st_flips: count of bars where positions_raw changes direction from
    # a non-zero previous value.  Equivalent to the signal rows produced by
    # build_signal_events (plan §8.2).
    prev_pos = positions_raw[:-1]
    curr_pos = positions_raw[1:]
    raw_st_flips = int(np.sum((curr_pos != prev_pos) & (prev_pos != 0)))

    # passed / blocked from bar-level arrays
    passed_entry_signals = int(
        np.sum(filter_diagnostics["filter_allowed_entry"])
    )
    block_reason = filter_diagnostics["filter_block_reason"]

    blocked_filter_off = int(np.sum(block_reason == "filter_off"))
    blocked_waiting_first = int(
        np.sum(block_reason == "waiting_for_allowed_st_flip")
    )
    blocked_trade_mode = int(
        np.sum(block_reason == "trade_mode_disallowed_flip")
    )
    blocked_local_median = int(
        np.sum(block_reason == "local_median_unavailable")
    )
    # invalid_stats and insufficient_global_stats are aggregated together
    blocked_invalid_stats = int(
        np.sum(
            (block_reason == "invalid_stats")
            | (block_reason == "insufficient_global_stats")
        )
    )
    blocked_stopping = int(
        np.sum(block_reason == "stopping_mode_no_new_entries")
    )

    blocked_entry_signals = (
        blocked_filter_off
        + blocked_waiting_first
        + blocked_trade_mode
        + blocked_local_median
        + blocked_invalid_stats
        + blocked_stopping
    )

    # lifecycle_starts: transitions from inactive states into lifecycle-active
    # states.  Uses shared ACTIVE_LIFECYCLE_STATES (plan §7.4).
    state_arr = filter_diagnostics["trade_filter_state"]
    lifecycle_active = np.zeros(len(state_arr), dtype=bool)
    for _s in _ACTIVE_LIFECYCLE_STATES:
        lifecycle_active |= (state_arr == _s)
    for _s in _VOLUME_ACTIVE_STATES:
        lifecycle_active |= (state_arr == _s)
    lifecycle_starts = int(len(state_arr) > 0 and lifecycle_active[0])
    if len(state_arr) > 1:
        lifecycle_starts += int(
            np.sum(lifecycle_active[1:] & ~lifecycle_active[:-1])
        )

    # median_stop_triggered: cross-source from bar-level int8 mask
    median_stop_triggered = int(
        np.sum(filter_diagnostics["median_stop_triggered"])
    )
    zz_leg_t = filter_diagnostics.get("zz_leg_stop_triggered")
    zz_leg_stop_triggered = (
        int(np.sum(zz_leg_t == 1)) if zz_leg_t is not None else 0
    )
    daily_reset_event = filter_diagnostics.get("daily_reset_event")
    daily_reset_count = (
        int(np.sum(daily_reset_event == 1))
        if daily_reset_event is not None else 0
    )

    # docs/time_filter_plan_v1_final.txt §6.2
    time_filter_reset_event = filter_diagnostics.get("time_filter_reset_event")
    time_filter_reset_count = (
        int(np.sum(time_filter_reset_event == 1))
        if time_filter_reset_event is not None else 0
    )
    time_filter_in_window = filter_diagnostics.get("time_filter_in_window")
    time_filter_bars_in_window = (
        int(np.sum(time_filter_in_window == 1))
        if time_filter_in_window is not None else 0
    )
    time_filter_bars_out_window = (
        int(np.sum(time_filter_in_window == 0))
        if time_filter_in_window is not None else 0
    )
    time_filter_enabled_arr = filter_diagnostics.get("time_filter_enabled")
    time_filter_enabled = (
        bool(int(time_filter_enabled_arr[0]))
        if time_filter_enabled_arr is not None and len(time_filter_enabled_arr) > 0
        else False
    )

    # exits_opposite_flip: from trade-level diagnostics
    exits_opposite_flip = 0
    if trades_df is not None and not trades_df.empty:
        if "exit_reason" in trades_df.columns:
            exits_opposite_flip = int(
                (trades_df["exit_reason"] == "filter_stopping_opposite_flip").sum()
            )

    immediate_used = filter_diagnostics.get("immediate_candidate_entry_used")
    immediate_reasons = filter_diagnostics.get(
        "immediate_candidate_entry_block_reason"
    )
    immediate_entries_count = (
        int(np.sum(immediate_used == 1)) if immediate_used is not None else 0
    )
    immediate_blocked_reasons = frozenset({
        "duration_gate_failed",
        "unknown_candidate_direction",
        "trade_mode_disallows_direction",
    })
    immediate_entries_blocked_count = (
        sum(1 for r in np.asarray(immediate_reasons) if r in immediate_blocked_reasons)
        if immediate_reasons is not None else 0
    )

    return {
        "raw_st_flips": raw_st_flips,
        "passed_entry_signals": passed_entry_signals,
        "blocked_entry_signals": blocked_entry_signals,
        "blocked_filter_off": blocked_filter_off,
        "blocked_waiting_first": blocked_waiting_first,
        "blocked_trade_mode": blocked_trade_mode,
        "blocked_local_median": blocked_local_median,
        "blocked_invalid_stats": blocked_invalid_stats,
        "blocked_stopping": blocked_stopping,
        "lifecycle_starts": lifecycle_starts,
        "median_stop_triggered": median_stop_triggered,
        "zz_leg_stop_triggered": zz_leg_stop_triggered,
        "daily_reset_count": daily_reset_count,
        "exits_opposite_flip": exits_opposite_flip,
        "immediate_entries_count": immediate_entries_count,
        "immediate_entries_blocked_count": immediate_entries_blocked_count,
        # docs/time_filter_plan_v1_final.txt §6.2
        "time_filter_enabled": time_filter_enabled,
        "time_filter_reset_count": time_filter_reset_count,
        "time_filter_bars_in_window": time_filter_bars_in_window,
        "time_filter_bars_out_window": time_filter_bars_out_window,
    }


def _compute_volume_summary_counters(
    filter_diagnostics: Dict[str, np.ndarray],
) -> Dict[str, Any]:
    state = np.asarray(filter_diagnostics.get("trade_filter_state", []))
    block = np.asarray(filter_diagnostics.get("filter_block_reason", []))
    regime = np.asarray(filter_diagnostics.get("volume_regime", []))
    direction = np.asarray(filter_diagnostics.get("volume_initial_direction", []))
    median_relative = np.asarray(
        filter_diagnostics.get("median_relative_volume", []), dtype=np.float64
    )
    volume_reasons = {
        "volume_direction_warmup",
        "volume_unknown_direction",
        "volume_trade_mode_disallowed_direction",
        "volume_warmup",
        "volume_baseline_zero",
        "volume_below_baseline",
        "volume_above_baseline",
    }
    blocked = np.isin(block, list(volume_reasons)) if len(block) else np.array([], dtype=bool)
    active = np.isin(state, list(_VOLUME_ACTIVE_STATES)) if len(state) else np.array([], dtype=bool)
    starts = int(len(active) > 0 and bool(active[0]))
    if len(active) > 1:
        starts += int(np.sum(active[1:] & ~active[:-1]))
    finite = median_relative[np.isfinite(median_relative)] if len(median_relative) else []
    return {
        "n_volume_blocked_start_attempts": int(np.sum(blocked)),
        "n_volume_blocked_start_attempts_long": int(np.sum(blocked & (direction == "long"))) if len(direction) == len(blocked) else 0,
        "n_volume_blocked_start_attempts_short": int(np.sum(blocked & (direction == "short"))) if len(direction) == len(blocked) else 0,
        "n_volume_blocked_start_attempts_unknown_direction": int(np.sum(blocked & (direction == "unknown"))) if len(direction) == len(blocked) else 0,
        "n_volume_warmup_blocked_start_attempts": int(np.sum(block == "volume_warmup")) if len(block) else 0,
        "n_volume_below_baseline_blocked_start_attempts": int(np.sum(block == "volume_below_baseline")) if len(block) else 0,
        "n_volume_above_baseline_blocked_start_attempts": int(np.sum(block == "volume_above_baseline")) if len(block) else 0,
        "n_volume_baseline_zero_blocked_start_attempts": int(np.sum(block == "volume_baseline_zero")) if len(block) else 0,
        "n_volume_direction_warmup_blocked_start_attempts": int(np.sum(block == "volume_direction_warmup")) if len(block) else 0,
        "n_volume_unknown_direction_blocked_start_attempts": int(np.sum(block == "volume_unknown_direction")) if len(block) else 0,
        "n_volume_trade_mode_disallowed_direction_blocked_start_attempts": int(np.sum(block == "volume_trade_mode_disallowed_direction")) if len(block) else 0,
        "n_volume_low_regime_bars": int(np.sum(regime == "low_volume")) if len(regime) else 0,
        "n_volume_normal_regime_bars": int(np.sum(regime == "normal_volume")) if len(regime) else 0,
        "n_volume_high_regime_bars": int(np.sum(regime == "high_volume")) if len(regime) else 0,
        "avg_median_relative_volume": float(np.mean(finite)) if len(finite) else None,
        "n_volume_started_cycles": starts,
    }


def _build_filter_diagnostics_summary(
    result: BacktestResult,
    trade_filter_config: Any,
    zigzag_global_stats: Any,
    global_offset: int,
) -> Optional[Dict[str, Any]]:
    """Build per-period filter_diagnostics_summary from a completed BacktestResult.

    Returns ``None`` immediately on the disabled path (``trade_filter_config``
    is ``None`` or ``not trade_filter_config.enabled``).

    ``positions_raw`` is regenerated locally — it is the pre-filter positions
    array used to count ``raw_st_flips``.  It lives ONLY inside this function
    and is NOT attached to ``BacktestResult`` or ``PeriodResult`` (plan §6.3,
    §3.3.5).

    Plan reference: §3.3.2, §6.3
    """
    if not is_trade_filter_enabled(trade_filter_config):
        return None
    if result.filter_diagnostics is None:
        # Should not happen on the enabled path; guard defensively.
        return None
    volume_counters = (
        _compute_volume_summary_counters(result.filter_diagnostics)
        if "volume_regime" in result.filter_diagnostics else {}
    )
    if not is_zigzag_enabled(trade_filter_config):
        return {
            "mode": "volume_only",
            "global_offset": global_offset,
            "counters": volume_counters,
            "bars_in_state": _bars_in_state_histogram(result.filter_diagnostics),
            **volume_counters,
        }

    # Regenerate pre-filter positions locally (deterministic; plan §3.3.5).
    positions_raw = generate_positions(
        result.trend,
        result.trade_mode,
        ExecutionModel.OPEN_TO_OPEN,
    )

    thresholds = _echo_thresholds(trade_filter_config, zigzag_global_stats)
    counters = _compute_summary_counters(
        positions_raw=positions_raw,
        positions_filtered=result.positions,
        filter_diagnostics=result.filter_diagnostics,
        trades_df=result.trades_df,
    )
    counters.update(volume_counters)
    bars_in_state = _bars_in_state_histogram(result.filter_diagnostics)

    zigzag_mode = str(getattr(zigzag_global_stats, "zigzag_mode", "") or "")
    gate_enabled = bool(
        getattr(zigzag_global_stats, "candidate_duration_gate_enabled", False)
    )
    gate_max_bars = getattr(zigzag_global_stats, "candidate_duration_max_bars", None)
    gate_max_bars_out = int(gate_max_bars) if gate_max_bars is not None else -1

    return {
        "mode": "zigzag_st_mode",
        "zigzag_mode": zigzag_mode,
        "candidate_duration_gate_enabled": gate_enabled,
        "candidate_duration_max_bars": gate_max_bars_out,
        "immediate_entries_count": counters.get("immediate_entries_count", 0),
        "immediate_entries_blocked_count": counters.get(
            "immediate_entries_blocked_count", 0
        ),
        "lifecycle_starts_count": counters.get("lifecycle_starts", 0),
        "median_stop_triggered_count": counters.get("median_stop_triggered", 0),
        "zz_leg_stop_triggered_count": counters.get("zz_leg_stop_triggered", 0),
        # §7.3 / §14.7: also expose exit-off echo scalars at top level
        # so WF Grid and Tester summary shapes are equivalent (not just in thresholds).
        "exit_off_mode": thresholds.get("exit_off_mode", "exit A"),
        "exit_off_zz_leg_count": thresholds.get("exit_off_zz_leg_count", -1),
        # Plan v3 §8 / §2.1: echo immediate-off flag at top level (mirrors wf_grid)
        "exit_b_immediate_off": thresholds.get("exit_b_immediate_off", False),
        "thresholds": thresholds,
        "global_offset": global_offset,
        "counters": counters,
        "bars_in_state": bars_in_state,
        **volume_counters,
    }


def _slice_volume_runtime_for_period(
    volume_runtime: Optional[VolumeRuntime],
    *,
    global_offset: int,
    n_bars: int,
) -> Optional[VolumeRuntime]:
    if volume_runtime is None:
        return None
    if volume_runtime.reference_length == n_bars:
        return volume_runtime
    return volume_runtime.slice(global_offset, global_offset + n_bars)


def run_period(
    df: pd.DataFrame,
    atr_period: int,
    multiplier: float,
    trade_mode: str,
    commission: float,
    warmup_period: int = 0,
    warmup_time: Optional[str] = None,
    periods_per_year: float = 252.0,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    auto_warmup: bool = False,
    # NOTE: min_trades_required=1 is a backward-compatible default for
    # direct/internal calls. The CLI tester path overrides this value from
    # config_tester.yaml, where the effective user-facing default is 5.
    min_trades_required: int = 1,
    # WP-T4 (Phase 2) — ZigZag ST filter integration (plan §6.1)
    trade_filter_config: Any = None,
    zigzag_global_stats: Any = None,
    volume_runtime: Optional[VolumeRuntime] = None,
    global_offset: int = 0,
) -> PeriodResult:
    """
    Run backtest on a single period (DataFrame slice).

    Uses unified engine - delegates to run_single_backtest().
    DD-01: Trades are extracted from full history.

    WP-T4 (Phase 2): accepts optional ZigZag ST filter parameters forwarded to
    ``run_single_backtest``.  Disabled path (``trade_filter_config=None`` or
    ``enabled=False``) is bit-identical to the pre-Phase-2 baseline (plan §3.3.3).

    Args:
        df: OHLC DataFrame slice
        atr_period: ATR period
        multiplier: ATR multiplier
        trade_mode: Trading mode ("revers", "long", "short")
        commission: Commission rate per operation
        warmup_period: Additional warmup period
        periods_per_year: Periods per year for annualization (can be float for auto-detected)
        execution_model: Execution model (only OPEN_TO_OPEN is supported)
        auto_warmup: If True, enforce warmup >= atr_period inside the engine
        min_trades_required: Minimum trades for valid ratio metrics (sharpe/sortino/cagr)
        trade_filter_config: Optional TradeFilterConfig (plan §6.1).
        zigzag_global_stats: Pre-materialised ZigZagGlobalStats for the full dataset
            (plan §7.2).  REQUIRED when ``trade_filter_config.enabled=True``.
            Callers are responsible for materialising stats before slicing (per-spec
            §12: global_median is a full-dataset statistic, not slice-local).
        global_offset: Absolute start index of this slice in the full df
            (``len(full_df) - n_period``).  Forwarded to ``run_single_backtest``
            for FSM diagnostics alignment (plan §4.1).

    Returns:
        PeriodResult with backtest result and optional filter diagnostics.

    Raises:
        ConfigError: If ``trade_filter_config.enabled=True`` and
            ``zigzag_global_stats`` is ``None`` (plan §7.2 fail-closed rule).
    """
    # WP-T4 fail-fast: enabled filter requires pre-materialised stats (plan §7.2).
    zigzag_enabled = is_zigzag_enabled(trade_filter_config)
    volume_enabled = is_volume_enabled(trade_filter_config)
    filter_enabled = zigzag_enabled or volume_enabled
    if zigzag_enabled and zigzag_global_stats is None:
        raise ConfigError(
            "zigzag_global_stats required when trade_filter is enabled; "
            "materialise stats with build_zigzag_global_stats(close, config) "
            "BEFORE calling run_period (plan §7.2 / spec §12)."
        )

    # Extract numpy arrays
    open_prices = df["open"].values
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    index = df.index

    n_bars = len(df)
    period_volume_runtime = _slice_volume_runtime_for_period(
        volume_runtime,
        global_offset=global_offset,
        n_bars=n_bars,
    )

    # Run backtest using unified engine (no early exit for tester)
    result = run_single_backtest(
        open_prices=open_prices,
        high=high,
        low=low,
        close=close,
        index=index,
        atr_period=atr_period,
        multiplier=multiplier,
        trade_mode=trade_mode,
        commission=commission,
        warmup_period=warmup_period if warmup_time is None else None,
        warmup_time=warmup_time,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        periods_per_year=periods_per_year,
        min_trades_required=min_trades_required,
        extract_trades_flag=True,
        caller_mode="tester",
        execution_model=execution_model,
        auto_warmup=auto_warmup,
        # WP-T4 new params forwarded to engine (plan §6.1)
        trade_filter_config=trade_filter_config if filter_enabled else None,
        zigzag_global_stats=zigzag_global_stats if zigzag_enabled else None,
        volume_runtime=period_volume_runtime,
        global_offset=global_offset if filter_enabled else 0,
    )

    # Validate warmup vs data length
    if result.warmup >= len(result.returns):
        raise ValueError(
            f"Warmup ({result.warmup}) >= number of bars ({len(result.returns)}). "
            "Cannot run tester on this period."
        )

    # WP-T4: build tester-side summary (plan §6.3); None on disabled path.
    summary = _build_filter_diagnostics_summary(
        result=result,
        trade_filter_config=trade_filter_config,
        zigzag_global_stats=zigzag_global_stats,
        global_offset=global_offset,
    )

    return PeriodResult(
        period_label="",  # Will be set by caller
        n_bars=n_bars,
        result=result,
        filter_diagnostics=result.filter_diagnostics,
        filter_diagnostics_summary=summary,
        filter_config_snapshot=result.filter_config_snapshot,
    )


def run_all_periods(
    df: pd.DataFrame,
    atr_period: int,
    multiplier: float,
    trade_mode: str,
    commission: float,
    warmup_period: int = 0,
    warmup_time: Optional[str] = None,
    periods_per_year: float = 252.0,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    auto_warmup: bool = False,
    min_trades_required: int = 1,
    # WP-T4 (Phase 2) — ZigZag ST filter integration (plan §6.1 / §7.3)
    trade_filter_config: Any = None,
    zigzag_global_stats: Any = None,
    volume_runtime: Optional[VolumeRuntime] = None,
) -> List[PeriodResult]:
    """
    Run backtest on all periods (100%, 75%, 50%, 33%, 25%).

    Takes already loaded and validated DataFrame.
    Useful for tests where you can pass synthetic data.

    WP-T4 (Phase 2): accepts optional ZigZag ST filter parameters.
    When ``trade_filter_config.enabled=True`` and ``zigzag_global_stats`` is
    ``None``, this function materialises stats from the full ``df`` ONCE before
    iterating slices (plan §7.3):

        zigzag_global_stats = build_zigzag_global_stats(
            close=df["close"].values,
            trade_filter_config=trade_filter_config,
        )

    The materialised stats are then reused for all 5 period slices (plan §3.2).
    Disabled path (``trade_filter_config=None`` or ``enabled=False``) is
    bit-identical to the pre-Phase-2 baseline.

    Args:
        df: OHLC DataFrame (already loaded and validated)
        atr_period: ATR period
        multiplier: ATR multiplier
        trade_mode: Trading mode ("revers", "long", "short")
        commission: Commission rate per operation
        warmup_period: Additional warmup period
        periods_per_year: Periods per year for annualization (can be float for auto-detected)
        execution_model: Execution model (only OPEN_TO_OPEN is supported)
        auto_warmup: If True, enforce warmup >= atr_period inside the engine
        min_trades_required: Minimum trades for valid ratio metrics (sharpe/sortino/cagr)
        trade_filter_config: Optional TradeFilterConfig (plan §6.1).
        zigzag_global_stats: Optional pre-materialised ZigZagGlobalStats.  If
            ``None`` and filter is enabled, materialised here from ``df`` (plan §7.3).

    Returns:
        List of PeriodResult (5 elements: 100%, 75%, 50%, 33%, 25%)
    """
    n_total = len(df)

    # WP-T4 §7.3 — materialise stats once from the full df when not supplied.
    zigzag_enabled = is_zigzag_enabled(trade_filter_config)
    filter_enabled = zigzag_enabled or is_volume_enabled(trade_filter_config)
    if zigzag_enabled and zigzag_global_stats is None:
        triggers_marker = getattr(trade_filter_config, "_raw_triggers_present", None)
        raw_user_keys = (
            frozenset({("trade_filter", "triggers")})
            if triggers_marker is True else frozenset()
        )
        resolve_trade_filter_mode_in_place(trade_filter_config, raw_user_keys)
        zigzag_global_stats = build_zigzag_global_stats(
            close=df["close"].values,
            trade_filter_config=trade_filter_config,
        )

    results = []

    for period_label, fraction in PERIOD_SPLITS:
        # Calculate slice size (guard: at least 1 bar)
        n_period = max(1, math.floor(n_total * fraction))

        # Get slice (last n_period bars)
        df_slice = df.iloc[-n_period:]

        # WP-T4 §4.1 — per-period global_offset for FSM diagnostics alignment.
        period_global_offset = n_total - n_period
        period_volume_runtime = (
            volume_runtime.slice(period_global_offset, period_global_offset + n_period)
            if volume_runtime is not None
            else None
        )

        # Run period
        result = run_period(
            df=df_slice,
            atr_period=atr_period,
            multiplier=multiplier,
            trade_mode=trade_mode,
            commission=commission,
            warmup_period=warmup_period,
            warmup_time=warmup_time,
            periods_per_year=periods_per_year,
            execution_model=execution_model,
            auto_warmup=auto_warmup,
            min_trades_required=min_trades_required,
            trade_filter_config=trade_filter_config,
            zigzag_global_stats=zigzag_global_stats,
            volume_runtime=period_volume_runtime,
            global_offset=period_global_offset,
        )

        # Set period label
        result.period_label = period_label

        results.append(result)

    return results


def run_all_periods_from_csv(
    csv_path: str,
    atr_period: int,
    multiplier: float,
    trade_mode: str,
    commission: float,
    warmup_period: int = 0,
    periods_per_year: float = 252.0,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN
) -> List[PeriodResult]:
    """
    Run backtest on all periods from CSV file.
    
    This is the main entry point for CLI - handles data loading.
    
    Args:
        csv_path: Path to OHLC CSV file
        atr_period: ATR period
        multiplier: ATR multiplier
        trade_mode: Trading mode ("revers", "long", "short")
        commission: Commission rate per operation
        warmup_period: Additional warmup period
        periods_per_year: Periods per year for annualization (can be float for auto-detected)
        execution_model: Execution model (only OPEN_TO_OPEN is supported)
        
    Returns:
        List of PeriodResult (5 elements: 100%, 75%, 50%, 33%, 25%)
    """
    # Load and validate data (single point of data loading)
    df = load_ohlc_csv(csv_path)
    df = validate_ohlc_data(df)
    
    # Run all periods
    return run_all_periods(
        df=df,
        atr_period=atr_period,
        multiplier=multiplier,
        trade_mode=trade_mode,
        commission=commission,
        warmup_period=warmup_period,
        periods_per_year=periods_per_year,
        execution_model=execution_model
    )


# =============================================================================
# Equal Blocks Segmentation — Phase 1 (MVP: prepend_history only)
# =============================================================================


@dataclass
class SegmentResult:
    """
    Container for a single equal-blocks segment result.

    CONTRACT:
    - segment_metrics is the ONLY source of truth for metrics.
      It contains metrics for the TARGET segment only (not the extended slice).
    - segment_trades_df contains trades whose entry falls inside the target
      segment (entry_index rebased to segment coordinates: 0 = first bar of
      segment).
    - BacktestResult is NOT stored here to avoid the extended_result.metrics
      vs segment_metrics confusion and to avoid keeping large numpy arrays.
    """
    segment_label: str          # "S1", "S2", ...
    segment_index: int          # 0-based
    n_parts: int                # total number of segments
    range_label: str            # "0–20%", "20–40%", ...
    start_bar: int              # absolute index in full df (inclusive)
    end_bar: int                # absolute index in full df (exclusive)
    n_bars: int                 # bars in target segment
    prepend_bars: int           # warmup bars prepended (0 for S1 or isolated)

    # Strategy parameters (scalar — copied from BacktestResult)
    atr_period: int
    multiplier: float
    trade_mode: str
    commission: float

    # Segment-only data
    segment_metrics: Dict[str, Any]
    segment_trades_df: Optional[pd.DataFrame]

    # Dates for Summary sheet (None when index is not DatetimeIndex)
    start_date: Optional[pd.Timestamp]
    end_date: Optional[pd.Timestamp]
    # effective_warmup from the underlying extended-slice BacktestResult (ratio metrics;
    # may be below requested warmup when the safety-cap triggers on that slice).
    ext_slice_effective_warmup: int = 0


def build_equal_block_slices(
    n_total: int,
    n_parts: int,
) -> List[Tuple[str, int, int]]:
    """
    Divide [0, n_total) into n_parts equal consecutive segments.

    Rules:
    - block_size = n_total // n_parts
    - Segments 0..N-2: [i*block_size, (i+1)*block_size)
    - Last segment:    [(N-1)*block_size, n_total)  — absorbs the remainder
    - Labels: "S1", "S2", ..., "SN"

    Args:
        n_total: Total number of bars.
        n_parts: Number of segments (must be >= 2 and <= n_total).

    Returns:
        List of (label, start_idx, end_idx) tuples.

    Raises:
        ValueError: If n_parts < 2 or n_total < n_parts.
    """
    if n_parts < 2:
        raise ValueError(f"n_parts must be >= 2, got {n_parts}")
    if n_total < n_parts:
        raise ValueError(
            f"Not enough bars ({n_total}) for equal_blocks segmentation with "
            f"n_parts={n_parts}. Need at least {n_parts} bars."
        )

    block_size = n_total // n_parts
    slices: List[Tuple[str, int, int]] = []

    for i in range(n_parts):
        label = f"S{i + 1}"
        start = i * block_size
        end = (i + 1) * block_size if i < n_parts - 1 else n_total
        slices.append((label, start, end))

    return slices


def filter_trades_to_segment(
    trades_df: Optional[pd.DataFrame],
    oos_boundary: int,
) -> Optional[pd.DataFrame]:
    """
    Return a copy of trades_df keeping only trades whose entry falls inside
    the target segment.

    CONTRACT: trade belongs to segment iff entry_index >= oos_boundary
    (coordinates of the extended slice: 0 = first bar of extended slice,
    oos_boundary = first bar of target segment).

    NOTE: This function does NOT rebase entry_index / exit_index.
    The caller is responsible for rebasing after filtering.

    INDEX CONTRACT (after caller rebases by subtracting oos_boundary):
    - entry_index is always in [0, n_bars): entry is guaranteed inside the
      target segment by the filter above.
    - exit_index MAY be >= n_bars: a trade that opens inside the segment can
      close on a bar that belongs to the next segment. This is intentional —
      the trade is attributed to the segment where it was entered, and its
      full PnL is counted. Consumers of segment_trades_df must not treat
      exit_index >= n_bars as an error.

    Args:
        trades_df: Full trades DataFrame from run_single_backtest(), or None.
        oos_boundary: First bar index of the target segment within the
                      extended slice (= prepend_bars).

    Returns:
        Filtered copy, or None if trades_df is None.
        Returns empty DataFrame with original columns if no trades match.
    """
    if trades_df is None:
        return None

    if trades_df.empty or "entry_index" not in trades_df.columns:
        return trades_df.copy()

    return trades_df[trades_df["entry_index"] >= oos_boundary].copy()


def recalc_trade_metrics(
    metrics: Dict[str, Any],
    trades_df: Optional[pd.DataFrame],
    min_trades_required: int,
) -> None:
    """
    Recalculate trade-based metrics from trades_df and apply min_trades_required
    guard for ratio metrics. Mutates `metrics` in-place.

    This is the SINGLE point of min_trades_required enforcement for the
    equal_blocks path. It is an exact replica of Step 5.5 in engine/run.py
    (lines 210–263), adapted as a standalone function.

    LOCAL CONTRACT (equal_blocks path only):
    - calculate_all_metrics() is called with min_trades_required=1 to get raw
      ratio metrics without premature invalidation.
    - This function applies the final min_trades_required guard using the
      exact trade count from trades_df (not bar-level estimate).
    - Ratio metrics (sharpe, sortino, cagr) remain valid iff
      len(trades_df) >= min_trades_required.
    - max_drawdown is NOT invalidated by min_trades_required (equity-based metric).

    SEMANTIC CONTRACT (matches engine/run.py Step 5.5):
    - win_rate: PERCENT (0.0–100.0), not fraction
    - sum_pnl_pct: sum of simple per-trade net returns (not compound equity)
    - profit_factor: np.inf when zero losses, INVALID_METRIC_VALUE when
                     zero trades or all breakeven

    Args:
        metrics: Metrics dict to mutate in-place.
        trades_df: Segment-filtered trades DataFrame (already rebased), or None.
        min_trades_required: Minimum trades for valid ratio metrics.
    """
    import numpy as np

    if trades_df is not None and len(trades_df) > 0:
        num_trades = len(trades_df)

        winning_trades = (trades_df["net_pnl_pct"] > 0).sum()
        win_rate = (winning_trades / num_trades * 100.0) if num_trades > 0 else 0.0

        sum_pnl_pct = trades_df["net_pnl_pct"].sum()
        avg_trade = sum_pnl_pct / num_trades if num_trades > 0 else 0.0

        profits = trades_df.loc[trades_df["net_pnl_pct"] > 0, "net_pnl_pct"].sum()
        losses = trades_df.loc[trades_df["net_pnl_pct"] < 0, "net_pnl_pct"].sum()

        if losses < 0:
            profit_factor = profits / abs(losses)
        elif profits > 0:
            profit_factor = np.inf
        else:
            profit_factor = INVALID_METRIC_VALUE

        metrics["num_trades"] = num_trades
        metrics["win_rate"] = win_rate
        metrics["sum_pnl_pct"] = sum_pnl_pct
        metrics["avg_trade"] = avg_trade
        metrics["profit_factor"] = profit_factor
        metrics["net_pnl_pct"] = avg_trade

        # Final min_trades_required guard — applied once, using exact count.
        # max_drawdown is equity-based and not invalidated by trade count.
        if num_trades < min_trades_required:
            metrics["sharpe"] = INVALID_METRIC_VALUE
            metrics["sortino"] = INVALID_METRIC_VALUE
            metrics["cagr"] = INVALID_METRIC_VALUE

    else:
        # Empty or None trades_df — trade-based and ratio metrics invalid.
        # max_drawdown is equity-based; it remains as set by calculate_all_metrics.
        metrics["num_trades"] = 0
        metrics["win_rate"] = 0.0
        metrics["sum_pnl_pct"] = 0.0
        metrics["avg_trade"] = INVALID_METRIC_VALUE
        metrics["profit_factor"] = INVALID_METRIC_VALUE
        metrics["net_pnl_pct"] = INVALID_METRIC_VALUE
        metrics["sharpe"] = INVALID_METRIC_VALUE
        metrics["sortino"] = INVALID_METRIC_VALUE
        metrics["cagr"] = INVALID_METRIC_VALUE


def run_equal_blocks(
    df: pd.DataFrame,
    n_parts: int,
    warmup_period: int,
    atr_period: int,
    multiplier: float,
    trade_mode: str,
    commission: float,
    periods_per_year: float = 252.0,
    execution_model: ExecutionModel = ExecutionModel.OPEN_TO_OPEN,
    min_trades_required: int = 5,
    export_trades: bool = True,
    # WP-T5 (Phase 2) — defensive guard for direct/internal calls (plan §4.2).
    trade_filter_config: Any = None,
) -> List[SegmentResult]:
    """
    Run backtests on N equal consecutive segments with prepend_history warmup.

    Each segment is extended backwards by min(warmup_period, seg_start) bars
    to allow indicator warmup ("prepend"). The backtest runs on the extended
    slice, but metrics and trades are calculated for the target segment only.

    CONTRACTS:
    - Uses run_single_backtest() directly (not run_period()) to control warmup.
    - calculate_all_metrics() called with min_trades_required=1 to get raw
      ratio metrics; final guard applied exclusively by recalc_trade_metrics().
    - Equity is NOT renormalized when slicing (equity[0] may be != 1.0).
    - For S1 (prepend=0): engine receives warmup_period to protect ratio
      metrics from unstable ATR. All other segments: warmup_period=0.
    - Trade indices are rebased to segment coordinates after filtering:
      entry_index is in [0, n_bars); exit_index MAY be >= n_bars when the
      trade closes on a bar that belongs to the next segment. This is by
      design — the trade is attributed to the segment of entry.

    WP-T5 (Phase 2): Phase 2 does NOT support the enabled ZigZag filter in
    equal_blocks mode (plan §4.2).  If ``trade_filter_config.enabled=True``
    is passed to a direct call, a ``ConfigError`` is raised immediately, BEFORE
    any slicing or backtest computation.  This is a defense-in-depth guard;
    the primary gate lives in ``load_tester_config`` / ``run_batch_tester.py``
    (WP-T2 config gate).

    Args:
        df: Full OHLC DataFrame (already loaded and validated).
        n_parts: Number of equal segments (>= 2).
        warmup_period: Warmup period in bars (from calculate_warmup_tester).
        atr_period: ATR period for SuperTrend.
        multiplier: ATR multiplier for SuperTrend.
        trade_mode: Trading mode ("revers", "long", "short").
        commission: Commission rate per operation.
        periods_per_year: Periods per year for annualization.
        execution_model: Execution model (only OPEN_TO_OPEN is supported).
        min_trades_required: Minimum trades for valid ratio metrics.
        export_trades: Whether to extract and export trades per segment.
        trade_filter_config: Optional TradeFilterConfig (WP-T5 guard only).
            ``None`` or ``enabled=False`` → bit-identical disabled baseline.
            ``enabled=True`` → ``ConfigError`` immediately (plan §4.2).

    Returns:
        List of SegmentResult (one per segment, ordered S1..SN).

    Raises:
        ConfigError: If ``trade_filter_config.enabled=True`` (plan §4.2).
        ValueError: If n_parts < 2 or not enough bars.
    """
    # WP-T5 fail-fast gate — BEFORE any slicing or backtest (plan §4.2).
    if is_trade_filter_enabled(trade_filter_config):
        raise ConfigError(
            "equal_blocks segmentation is not supported with "
            "trade_filter.enabled=true; use 'legacy' segmentation instead. "
            "zigzag_st_mode is supported only with segmentation.mode=legacy."
        )

    n_total = len(df)
    slices = build_equal_block_slices(n_total, n_parts)
    segments: List[SegmentResult] = []

    full_open = df["open"].values
    full_high = df["high"].values
    full_low = df["low"].values
    full_close = df["close"].values
    full_index = df.index

    for i, (label, seg_start, seg_end) in enumerate(slices):
        prepend = min(warmup_period, seg_start)
        ext_start = seg_start - prepend

        ext_open = full_open[ext_start:seg_end]
        ext_high = full_high[ext_start:seg_end]
        ext_low = full_low[ext_start:seg_end]
        ext_close = full_close[ext_start:seg_end]
        ext_index = full_index[ext_start:seg_end]

        # S1 (prepend=0): pass warmup to engine so ratio metrics exclude
        # unstable ATR bars. S2..SN: warmup consumed by prepend.
        if prepend == 0 and warmup_period > 0:
            engine_warmup = warmup_period
        else:
            engine_warmup = 0

        ext_result = run_single_backtest(
            open_prices=ext_open,
            high=ext_high,
            low=ext_low,
            close=ext_close,
            index=ext_index,
            atr_period=atr_period,
            multiplier=multiplier,
            trade_mode=trade_mode,
            commission=commission,
            warmup_period=engine_warmup,
            early_exit_enabled=False,
            periods_per_year=periods_per_year,
            # Raw ratio metrics — guard applied by recalc_trade_metrics only
            min_trades_required=1,
            extract_trades_flag=export_trades,
            caller_mode="tester",
            execution_model=execution_model,
            auto_warmup=False,
        )

        # --- Compute segment-only metrics ---
        oos_boundary = prepend
        if oos_boundary > 0:
            oos_returns = ext_result.returns[oos_boundary:]
            # NO equity renormalization — CAGR uses ratio, max_dd uses running peak
            oos_equity = ext_result.equity_curve[oos_boundary:]
            oos_positions = ext_result.positions[oos_boundary:]

            segment_metrics = calculate_all_metrics(
                returns=oos_returns,
                equity_curve=oos_equity,
                positions=oos_positions,
                warmup_period=0,
                periods_per_year=periods_per_year,
                min_trades_required=1,
            )
        else:
            # S1: extended slice IS the target segment; engine already applied
            # warmup to ratio metrics via engine_warmup.
            segment_metrics = ext_result.metrics.copy()

        # --- Filter trades to segment only ---
        segment_trades = filter_trades_to_segment(ext_result.trades_df, oos_boundary)

        # Rebase trade indices to segment coordinates (0 = first bar of segment)
        if (segment_trades is not None
                and not segment_trades.empty
                and oos_boundary > 0
                and "entry_index" in segment_trades.columns):
            segment_trades["entry_index"] = segment_trades["entry_index"] - oos_boundary
            segment_trades["exit_index"] = segment_trades["exit_index"] - oos_boundary

        # --- Single point of min_trades_required enforcement ---
        recalc_trade_metrics(segment_metrics, segment_trades, min_trades_required)

        segment_metrics.pop("effective_warmup", None)

        # --- Dates for Summary ---
        if isinstance(full_index, pd.DatetimeIndex):
            start_date = full_index[seg_start]
            end_date = full_index[seg_end - 1]
        else:
            start_date = None
            end_date = None

        segments.append(SegmentResult(
            segment_label=label,
            segment_index=i,
            n_parts=n_parts,
            range_label=f"{seg_start / n_total * 100:.0f}\u2013{seg_end / n_total * 100:.0f}%",
            start_bar=seg_start,
            end_bar=seg_end,
            n_bars=seg_end - seg_start,
            prepend_bars=prepend,
            atr_period=atr_period,
            multiplier=multiplier,
            trade_mode=trade_mode,
            commission=commission,
            segment_metrics=segment_metrics,
            segment_trades_df=segment_trades,
            start_date=start_date,
            end_date=end_date,
            ext_slice_effective_warmup=int(ext_result.effective_warmup),
        ))

    return segments
