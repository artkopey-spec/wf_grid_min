"""
WF step executor — canonical OOS prepend path + legacy/defensive fallbacks.

Full spec: docs/appendix_w_warmup_prepend_spec.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING, Union

import numpy as np
import pandas as pd

from supertrend_optimizer.core._fsm_state_names import (
    FSM_STATE_NAMES as _ALL_FSM_STATES,
    ACTIVE_LIFECYCLE_STATES as _ACTIVE_LIFECYCLE_STATES,
    STANDALONE_VOLUME_ACTIVE_STATES as _VOLUME_ACTIVE_STATES,
    STANDALONE_VOLUME_STATE_NAMES as _VOLUME_STATES,
)
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.core.metrics import calculate_all_metrics
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE, MAX_VALID_METRIC
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.time_utils import WFWindowSlice

from wf_grid.config.loader import ConfigError

if TYPE_CHECKING:
    from wf_grid.config.schema import GridConfig
    from wf_grid.grid.enumeration import GridPoint
    from supertrend_optimizer.core.volume_metrics import VolumeRuntime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step result dataclass
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Result of a single WF step execution (OOS or train).

    Contains metrics, trades, diagnostic fields, and optional raw arrays.
    This is the authoritative OOS-only output after trim (§W.7.2).

    Indexing contract
    -----------------
    wf_step : int
        1-based export/display label for this WF step.
        S1 in summary corresponds to wf_step=1, S2 to wf_step=2, etc.
        The +1 normalisation is applied at StepResult construction time.
        NOTE: wf_slice.step_index is 0-based (donor internal index) and
        must NOT be used as wf_step without adding 1.
    """

    grid_point_id: str
    wf_step: int
    test_start_idx: int
    test_end_idx: int

    metrics: Dict[str, Any]
    oos_trades_df: Optional[pd.DataFrame]

    # Diagnostics (§W.8)
    prepend_bars_requested: int
    prepend_bars_applied: int
    used_prepend: bool
    used_legacy_oos_path: bool
    used_defensive_fallback: bool
    oos_boundary_index: int
    warmup_used: int
    warmup_effective: int
    effective_oos_bars: int

    early_exit: bool = False

    # Optional error info (per reviewer's note on A3)
    error_message: Optional[str] = None
    error_type: Optional[str] = None

    # WP8 — OOS-trimmed filter diagnostics (plan §WP8 step 3).
    # Keys and lengths aligned to the OOS slice after oos_boundary trim.
    # None when trade_filter is disabled or absent.
    filter_diagnostics_oos: Optional[Dict[str, Any]] = None

    # WP9 — Summary counts computed from filter_diagnostics_oos.
    # Keys: diagnostics_available, trigger_count_candidate_threshold,
    # trigger_count_confirmed_median, trigger_count_both,
    # median_stop_triggered_count, stopping_started_count.
    # None when trade_filter is disabled or absent.
    filter_diagnostics_summary: Optional[Dict[str, Any]] = None
    filter_config_snapshot: Optional[dict] = None


# ---------------------------------------------------------------------------
# WP9: filter diagnostics summary helper
# ---------------------------------------------------------------------------

def _compute_filter_diagnostics_summary(
    filter_diagnostics: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Compute a concise summary dict from a filter_diagnostics keyset.

    Required §10.6.4 fields:
        filter_states_visited, n_bars_in_off, n_bars_in_freeze,
        n_bars_in_monitoring, n_bars_in_stopping, n_filter_blocked_entries,
        lifecycle_starts_count, median_stop_triggered_count.

    Additional informational fields (not required by §10.6.4):
        filter_diagnostics_available, trigger_count_*, stopping_started_count.

    Returns None when filter_diagnostics is None (filter disabled).
    """
    if filter_diagnostics is None:
        return None

    summary: Dict[str, Any] = {"diagnostics_available": True}

    # ------------------------------------------------------------------
    # §10.6.4 state-based summary
    # ------------------------------------------------------------------
    state_arr = filter_diagnostics.get("trade_filter_state")
    if state_arr is not None:
        state_np = np.asarray(state_arr)
        n = len(state_np)

        summary["n_bars_in_off"] = int(np.sum(state_np == "OFF"))
        summary["n_bars_in_wait_first_st_flip"] = int(
            np.sum(state_np == "WAIT_FIRST_ST_FLIP")
        )
        summary["n_bars_in_freeze"] = int(np.sum(state_np == "ST_ACTIVE_FREEZE"))
        summary["n_bars_in_monitoring"] = int(np.sum(state_np == "ST_ACTIVE_MONITORING"))
        summary["n_bars_in_counting_zz_legs"] = int(
            np.sum(state_np == "ST_COUNTING_ZZ_LEGS")
        )
        summary["n_bars_in_stopping"] = int(np.sum(state_np == "ST_STOPPING"))

        # filter_states_visited: comma-joined sorted list of observed states
        known_states = tuple(dict.fromkeys(_ALL_FSM_STATES + _VOLUME_STATES))
        visited = sorted(s for s in known_states if np.any(state_np == s))
        summary["filter_states_visited"] = ",".join(visited) if visited else ""

        # lifecycle_starts_count: transitions INTO active lifecycle states.
        # Uses shared ACTIVE_LIFECYCLE_STATES so adding a new active state
        # only requires one edit (in shared) — plan §7.4.
        lifecycle_active = np.zeros(n, dtype=bool)
        for _s in _ACTIVE_LIFECYCLE_STATES:
            lifecycle_active |= (state_np == _s)
        for _s in _VOLUME_ACTIVE_STATES:
            lifecycle_active |= (state_np == _s)
        lifecycle_starts = int(n > 0 and lifecycle_active[0])
        if n > 1:
            lifecycle_starts += int(
                np.sum(lifecycle_active[1:] & ~lifecycle_active[:-1])
            )
        summary["lifecycle_starts_count"] = lifecycle_starts

    # §10.6.4 n_filter_blocked_entries
    block_arr = filter_diagnostics.get("filter_block_reason")
    if block_arr is not None:
        summary["n_filter_blocked_entries"] = int(
            np.sum(np.asarray(block_arr) != "none")
        )

    # §10.6.4 median_stop_triggered_count
    median_stop_arr = filter_diagnostics.get("median_stop_triggered")
    if median_stop_arr is not None:
        summary["median_stop_triggered_count"] = int(np.sum(median_stop_arr == 1))

    zz_leg_arr = filter_diagnostics.get("zz_leg_stop_triggered")
    if zz_leg_arr is not None:
        summary["zz_leg_stop_triggered_count"] = int(np.sum(np.asarray(zz_leg_arr) == 1))

    eom_arr = filter_diagnostics.get("exit_off_mode")
    if eom_arr is not None:
        _e = np.asarray(eom_arr)
        summary["exit_off_mode"] = str(_e[0]) if len(_e) > 0 else ""

    eoz_arr = filter_diagnostics.get("exit_off_zz_leg_count")
    if eoz_arr is not None:
        _z = np.asarray(eoz_arr)
        summary["exit_off_zz_leg_count"] = int(_z[0]) if len(_z) > 0 else -1

    # Plan v3 §8: echo exit_b_immediate_off flag + per-period count
    imm_cfg_arr = filter_diagnostics.get("exit_b_immediate_off_config")
    if imm_cfg_arr is not None:
        _ic = np.asarray(imm_cfg_arr)
        summary["exit_b_immediate_off"] = bool(_ic[0]) if len(_ic) > 0 else False
    imm_trig_arr = filter_diagnostics.get("exit_b_immediate_off_triggered")
    if imm_trig_arr is not None:
        summary["exit_b_immediate_off_count"] = int(
            np.sum(np.asarray(imm_trig_arr) == 1)
        )

    # ------------------------------------------------------------------
    # Additional informational fields (not required by §10.6.4)
    # ------------------------------------------------------------------
    trigger_arr = filter_diagnostics.get("trade_filter_trigger_source")
    if trigger_arr is not None:
        trigger_np = np.asarray(trigger_arr)
        summary["trigger_count_candidate_threshold"] = int(
            np.sum(trigger_np == "candidate_threshold")
        )
        summary["trigger_count_confirmed_median"] = int(
            np.sum(trigger_np == "confirmed_median")
        )
        summary["trigger_count_both"] = int(np.sum(trigger_np == "both"))

    stopping_arr = filter_diagnostics.get("stopping_started_at_index")
    if stopping_arr is not None:
        unique_starts = {int(x) for x in stopping_arr if int(x) >= 0}
        summary["stopping_started_count"] = len(unique_starts)

    daily_reset_arr = filter_diagnostics.get("daily_reset_event")
    if daily_reset_arr is not None:
        summary["daily_reset_count"] = int(
            np.sum(np.asarray(daily_reset_arr) == 1)
        )

    # docs/time_filter_plan_v1_final.txt §6.1
    tf_enabled_arr = filter_diagnostics.get("time_filter_enabled")
    if tf_enabled_arr is not None:
        _tfe = np.asarray(tf_enabled_arr)
        summary["time_filter_enabled"] = bool(int(_tfe[0])) if len(_tfe) > 0 else False
    tf_reset_arr = filter_diagnostics.get("time_filter_reset_event")
    if tf_reset_arr is not None:
        summary["time_filter_reset_count"] = int(np.sum(np.asarray(tf_reset_arr) == 1))
    tf_in_w_arr = filter_diagnostics.get("time_filter_in_window")
    if tf_in_w_arr is not None:
        _tf_w = np.asarray(tf_in_w_arr)
        summary["time_filter_bars_in_window"] = int(np.sum(_tf_w == 1))
        summary["time_filter_bars_out_window"] = int(np.sum(_tf_w == 0))

    # ------------------------------------------------------------------
    # WP-V3-8: immediate entries counts (§11.3)
    # ------------------------------------------------------------------
    _IMM_BLOCKED_REASONS = frozenset({
        "duration_gate_failed",
        "unknown_candidate_direction",
        "trade_mode_disallows_direction",
    })
    imm_used_arr = filter_diagnostics.get("immediate_candidate_entry_used")
    if imm_used_arr is not None:
        summary["immediate_entries_count"] = int(
            np.sum(np.asarray(imm_used_arr) == 1)
        )
    imm_reason_arr = filter_diagnostics.get("immediate_candidate_entry_block_reason")
    if imm_reason_arr is not None:
        summary["immediate_entries_blocked_count"] = sum(
            1 for r in np.asarray(imm_reason_arr) if r in _IMM_BLOCKED_REASONS
        )

    # ZigZag mode and gate config (§11.3 params section)
    zigzag_mode_arr_raw = filter_diagnostics.get("zigzag_mode")
    if zigzag_mode_arr_raw is not None:
        arr = np.asarray(zigzag_mode_arr_raw)
        summary["zigzag_mode"] = str(arr[0]) if len(arr) > 0 else ""

    gate_en_arr_raw = filter_diagnostics.get("candidate_duration_gate_enabled")
    if gate_en_arr_raw is not None:
        arr = np.asarray(gate_en_arr_raw)
        summary["candidate_duration_gate_enabled"] = bool(int(arr[0])) if len(arr) > 0 else False

    gate_mb_arr_raw = filter_diagnostics.get("candidate_duration_max_bars")
    if gate_mb_arr_raw is not None:
        arr = np.asarray(gate_mb_arr_raw)
        summary["candidate_duration_max_bars"] = int(arr[0]) if len(arr) > 0 else -1

    if "volume_regime" in filter_diagnostics:
        summary.update(_compute_volume_diagnostics_summary(filter_diagnostics))

    return summary


def _compute_volume_diagnostics_summary(
    filter_diagnostics: Dict[str, Any],
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
    blocked_mask = np.isin(block, list(volume_reasons)) if len(block) else np.array([], dtype=bool)
    active = np.isin(state, list(_VOLUME_ACTIVE_STATES)) if len(state) else np.array([], dtype=bool)
    starts = int(len(active) > 0 and bool(active[0]))
    if len(active) > 1:
        starts += int(np.sum(active[1:] & ~active[:-1]))

    finite = median_relative[np.isfinite(median_relative)] if len(median_relative) else []
    return {
        "n_volume_blocked_start_attempts": int(np.sum(blocked_mask)),
        "n_volume_blocked_start_attempts_long": int(
            np.sum(blocked_mask & (direction == "long"))
        ) if len(direction) == len(blocked_mask) else 0,
        "n_volume_blocked_start_attempts_short": int(
            np.sum(blocked_mask & (direction == "short"))
        ) if len(direction) == len(blocked_mask) else 0,
        "n_volume_blocked_start_attempts_unknown_direction": int(
            np.sum(blocked_mask & (direction == "unknown"))
        ) if len(direction) == len(blocked_mask) else 0,
        "n_volume_warmup_blocked_start_attempts": int(
            np.sum(block == "volume_warmup")
        ) if len(block) else 0,
        "n_volume_below_baseline_blocked_start_attempts": int(np.sum(block == "volume_below_baseline")) if len(block) else 0,
        "n_volume_above_baseline_blocked_start_attempts": int(np.sum(block == "volume_above_baseline")) if len(block) else 0,
        "n_volume_baseline_zero_blocked_start_attempts": int(np.sum(block == "volume_baseline_zero")) if len(block) else 0,
        "n_volume_direction_warmup_blocked_start_attempts": int(
            np.sum(block == "volume_direction_warmup")
        ) if len(block) else 0,
        "n_volume_unknown_direction_blocked_start_attempts": int(np.sum(block == "volume_unknown_direction")) if len(block) else 0,
        "n_volume_trade_mode_disallowed_direction_blocked_start_attempts": int(
            np.sum(block == "volume_trade_mode_disallowed_direction")
        ) if len(block) else 0,
        "n_volume_low_regime_bars": int(np.sum(regime == "low_volume")) if len(regime) else 0,
        "n_volume_normal_regime_bars": int(np.sum(regime == "normal_volume")) if len(regime) else 0,
        "n_volume_high_regime_bars": int(np.sum(regime == "high_volume")) if len(regime) else 0,
        "avg_median_relative_volume": (
            float(np.mean(finite)) if len(finite) else None
        ),
        "n_volume_started_cycles": starts,
    }


# ---------------------------------------------------------------------------
# OOS canonical prepend path (§W.4.2)
# ---------------------------------------------------------------------------

def execute_oos_step(
    grid_point: "GridPoint",
    wf_slice: WFWindowSlice,
    full_open: np.ndarray,
    full_high: np.ndarray,
    full_low: np.ndarray,
    full_close: np.ndarray,
    full_index: pd.Index,
    config: "GridConfig",
    prepend_bars_requested: int,
    zigzag_global_stats: Any = None,
    volume_runtime: "Optional[VolumeRuntime]" = None,
) -> StepResult:
    """
    Execute one OOS WF step with canonical prepend path (§W.4.2).

    Falls back to legacy or defensive path when needed (§W.5).
    """
    test_start = wf_slice.test_start_idx
    test_end = wf_slice.test_end_idx
    if config.resolved_periods_per_year is None:
        raise ConfigError(
            "resolved_periods_per_year is None — call "
            "resolve_periods_per_year(config, data) in pipeline before execute_oos_step()"
        )
    periods_per_year = config.resolved_periods_per_year
    min_trades = config.backtest.min_trades_required

    # --- Step 2: per-step clamp ---
    prepend_applied = min(prepend_bars_requested, test_start)

    # --- Step 3: form extended slice ---
    ext_start = test_start - prepend_applied
    ext_open = full_open[ext_start:test_end]
    ext_high = full_high[ext_start:test_end]
    ext_low = full_low[ext_start:test_end]
    ext_close = full_close[ext_start:test_end]
    ext_index = full_index[ext_start:test_end]
    sliced_volume_runtime = (
        volume_runtime.slice(ext_start, test_end)
        if volume_runtime is not None
        else None
    )

    # --- Step 4: run backtest on extended slice ---
    # early_exit is hardcoded False for OOS steps regardless of config.
    # Enabling it would truncate donor arrays to exit_bar while test_start_idx/
    # test_end_idx still reference the full declared window — causing OOS metrics
    # to silently cover a shorter horizon than declared (horizon distortion).
    # Config-level validation in loader.py already rejects early_exit_enabled=True,
    # but this guard is a second line of defence.
    # WP8: resolve trade_filter_config from config; compute global_offset for this slice.
    trade_filter_config = getattr(config, "trade_filter", None)
    ext_result = run_single_backtest(
        open_prices=ext_open,
        high=ext_high,
        low=ext_low,
        close=ext_close,
        index=ext_index,
        atr_period=grid_point.atr_period,
        multiplier=grid_point.multiplier,
        trade_mode=grid_point.trade_mode,
        commission=config.backtest.commission,
        warmup_period=config.validation.warmup_period,
        early_exit_enabled=False,
        early_exit_max_drawdown=config.backtest.early_exit_max_drawdown,
        early_exit_check_bars=config.backtest.early_exit_check_bars,
        periods_per_year=periods_per_year,
        min_trades_required=min_trades,
        extract_trades_flag=True,
        auto_warmup=True,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=trade_filter_config,
        zigzag_global_stats=zigzag_global_stats,
        volume_runtime=sliced_volume_runtime,
        global_offset=ext_start,
    )
    if ext_result.early_exit:
        raise RuntimeError(
            "OOS step: early_exit triggered despite early_exit_enabled=False. "
            "This is a donor contract violation — investigate run_single_backtest."
        )

    # --- Step 5: extract extended arrays ---
    ext_returns = ext_result.returns
    ext_equity = ext_result.equity_curve
    ext_positions = ext_result.positions
    ext_trades_df = ext_result.trades_df

    # --- Step 5 guard: None arrays → defensive fallback ---
    if ext_returns is None or ext_equity is None or ext_positions is None:
        logger.warning(
            "Defensive fallback (None arrays): gp=%s step=%d",
            grid_point.grid_point_id, wf_slice.step_index,
        )
        return _defensive_fallback(
            ext_result, grid_point, wf_slice, prepend_bars_requested, prepend_applied,
        )

    # --- Step 5b: trim filter_diagnostics to OOS slice (plan §WP8 step 3) ---
    # force-flat does NOT modify filter_diagnostics (plan §WP8 step 4).
    # The trim is applied after oos_boundary is known (step 6 below), but
    # we derive oos_boundary = prepend_applied here for clarity.
    _ext_filter_diag = ext_result.filter_diagnostics  # None when disabled

    # --- Step 6: OOS boundary + guard ---
    oos_boundary = prepend_applied

    if oos_boundary >= len(ext_returns):
        logger.warning(
            "Defensive fallback (boundary): gp=%s step=%d oos_boundary=%d len_ext=%d",
            grid_point.grid_point_id, wf_slice.step_index,
            oos_boundary, len(ext_returns),
        )
        return _defensive_fallback(
            ext_result, grid_point, wf_slice, prepend_bars_requested, prepend_applied,
        )

    # --- Step 6b: slice filter_diagnostics to OOS ---
    filter_diagnostics_oos: Optional[Dict[str, Any]] = None
    if _ext_filter_diag is not None:
        filter_diagnostics_oos = {
            k: v[oos_boundary:] for k, v in _ext_filter_diag.items()
        }

    # --- Step 7: trim to OOS-only ---
    oos_returns = ext_returns[oos_boundary:]
    oos_equity = ext_equity[oos_boundary:]
    oos_positions = ext_positions[oos_boundary:]

    # --- Step 7b: OOS boundary force-flat (plan §4.4, OOS1) ---
    # OOS starts flat.  If a carry-in trade (entry_index < oos_boundary
    # AND exit_index >= oos_boundary) spills exposure into the OOS slice,
    # its bar-level economy (positions/returns) is zeroed on the OOS side,
    # and the OOS equity curve is rebuilt from the adjusted returns.
    # Trade-level filter (entry_index >= oos_boundary) stays where it is
    # in Step 9 — force-flat is applied on bar level only, so bar and trade
    # economics describe the SAME OOS-originated exposure.
    oos_returns, oos_positions, oos_equity = _apply_oos_force_flat(
        oos_returns=oos_returns,
        oos_positions=oos_positions,
        oos_equity=oos_equity,
        ext_trades_df=ext_trades_df,
        oos_boundary=oos_boundary,
    )

    # --- Step 8: recompute OOS ratio metrics (warmup_period=0) ---
    oos_bar_metrics = calculate_all_metrics(
        returns=oos_returns,
        equity_curve=oos_equity,
        positions=oos_positions,
        warmup_period=0,
        periods_per_year=periods_per_year,
        min_trades_required=min_trades,
    )
    # Remove effective_warmup from dict (will be 0 for canonical path)
    oos_bar_metrics.pop("effective_warmup", None)

    # --- Step 9: filter trades to OOS-only ---
    if ext_trades_df is not None and len(ext_trades_df) > 0:
        oos_trades_df = ext_trades_df[ext_trades_df["entry_index"] >= oos_boundary].copy()
        oos_trades_df["entry_index"] = oos_trades_df["entry_index"] - oos_boundary
        oos_trades_df["exit_index"] = oos_trades_df["exit_index"] - oos_boundary
    else:
        oos_trades_df = ext_trades_df  # None or empty

    # --- Step 10: trade-level override (§W.4.2 step 10) ---
    _apply_trade_level_override(oos_bar_metrics, oos_trades_df, min_trades)

    # --- Step 11: assemble result ---
    return StepResult(
        grid_point_id=grid_point.grid_point_id,
        wf_step=wf_slice.step_index + 1,
        test_start_idx=test_start,
        test_end_idx=test_end,
        metrics=oos_bar_metrics,
        oos_trades_df=oos_trades_df,
        prepend_bars_requested=prepend_bars_requested,
        prepend_bars_applied=prepend_applied,
        used_prepend=True,
        used_legacy_oos_path=False,
        used_defensive_fallback=False,
        oos_boundary_index=oos_boundary,
        warmup_used=config.validation.warmup_period,
        warmup_effective=0,
        effective_oos_bars=len(oos_returns),
        early_exit=ext_result.early_exit,
        filter_diagnostics_oos=filter_diagnostics_oos,
        filter_diagnostics_summary=_compute_filter_diagnostics_summary(filter_diagnostics_oos),
        filter_config_snapshot=ext_result.filter_config_snapshot,
    )


# ---------------------------------------------------------------------------
# Train path (§W.6)
# ---------------------------------------------------------------------------

def execute_train_step(
    grid_point: "GridPoint",
    wf_slice: WFWindowSlice,
    full_open: np.ndarray,
    full_high: np.ndarray,
    full_low: np.ndarray,
    full_close: np.ndarray,
    full_index: pd.Index,
    config: "GridConfig",
    zigzag_global_stats: Any = None,
    volume_runtime: "Optional[VolumeRuntime]" = None,
) -> StepResult:
    """Execute one train WF step (§W.6). No prepend."""
    train_start = wf_slice.train_start_idx
    train_end = wf_slice.train_end_idx
    if config.resolved_periods_per_year is None:
        raise ConfigError(
            "resolved_periods_per_year is None — call "
            "resolve_periods_per_year(config, data) in pipeline before execute_train_step()"
        )
    periods_per_year = config.resolved_periods_per_year

    train_open = full_open[train_start:train_end]
    train_high = full_high[train_start:train_end]
    train_low = full_low[train_start:train_end]
    train_close = full_close[train_start:train_end]
    train_index = full_index[train_start:train_end]
    sliced_volume_runtime = (
        volume_runtime.slice(train_start, train_end)
        if volume_runtime is not None
        else None
    )

    trade_filter_config = getattr(config, "trade_filter", None)
    result = run_single_backtest(
        open_prices=train_open,
        high=train_high,
        low=train_low,
        close=train_close,
        index=train_index,
        atr_period=grid_point.atr_period,
        multiplier=grid_point.multiplier,
        trade_mode=grid_point.trade_mode,
        commission=config.backtest.commission,
        warmup_period=config.validation.warmup_period,
        early_exit_enabled=False,
        periods_per_year=periods_per_year,
        min_trades_required=config.backtest.min_trades_required,
        extract_trades_flag=True,
        auto_warmup=True,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=trade_filter_config,
        zigzag_global_stats=zigzag_global_stats,
        volume_runtime=sliced_volume_runtime,
        global_offset=train_start,
    )

    metrics = dict(result.metrics)
    eff_warmup = metrics.pop("effective_warmup", result.effective_warmup)

    # Train: no OOS trim needed; filter_diagnostics covers the full train slice.
    train_filter_diag: Optional[Dict[str, Any]] = result.filter_diagnostics

    return StepResult(
        grid_point_id=grid_point.grid_point_id,
        wf_step=wf_slice.step_index + 1,
        test_start_idx=train_start,
        test_end_idx=train_end,
        metrics=metrics,
        oos_trades_df=result.trades_df,
        prepend_bars_requested=0,
        prepend_bars_applied=0,
        used_prepend=False,
        used_legacy_oos_path=False,
        used_defensive_fallback=False,
        oos_boundary_index=0,
        warmup_used=config.validation.warmup_period,
        warmup_effective=eff_warmup,
        effective_oos_bars=len(result.returns) if result.returns is not None else 0,
        early_exit=False,
        filter_diagnostics_oos=train_filter_diag,
        filter_diagnostics_summary=_compute_filter_diagnostics_summary(train_filter_diag),
        filter_config_snapshot=result.filter_config_snapshot,
    )


# ---------------------------------------------------------------------------
# Trade-level override logic (§W.4.2 step 10)
# ---------------------------------------------------------------------------

def _apply_trade_level_override(
    metrics: Dict[str, Any],
    oos_trades_df: Optional[pd.DataFrame],
    min_trades_required: int,
) -> None:
    """Override trade-level metrics from filtered OOS trades (in-place)."""

    if oos_trades_df is not None and len(oos_trades_df) > 0:
        num_trades = len(oos_trades_df)
        pnl_col = oos_trades_df["net_pnl_pct"]
        sum_pnl = float(pnl_col.sum())
        winning = int((pnl_col > 0).sum())
        win_rate = (winning / num_trades) * 100.0

        profits = float(pnl_col[pnl_col > 0].sum())
        losses = float(pnl_col[pnl_col < 0].sum())
        if losses < 0:
            profit_factor = profits / abs(losses)
        elif profits > 0:
            profit_factor = MAX_VALID_METRIC
        else:
            profit_factor = INVALID_METRIC_VALUE

        avg_trade = sum_pnl / num_trades

        metrics["num_trades"] = num_trades
        metrics["sum_pnl_pct"] = sum_pnl
        metrics["win_rate"] = win_rate
        metrics["profit_factor"] = profit_factor
        metrics["avg_trade"] = avg_trade
        metrics["net_pnl_pct"] = avg_trade

        if num_trades < min_trades_required:
            metrics["sharpe"] = INVALID_METRIC_VALUE
            metrics["sortino"] = INVALID_METRIC_VALUE
            metrics["cagr"] = INVALID_METRIC_VALUE

    else:
        # Empty OOS trades
        metrics["num_trades"] = 0
        metrics["sum_pnl_pct"] = 0.0
        metrics["win_rate"] = 0.0
        metrics["avg_trade"] = INVALID_METRIC_VALUE
        metrics["profit_factor"] = INVALID_METRIC_VALUE
        metrics["net_pnl_pct"] = INVALID_METRIC_VALUE
        metrics["sharpe"] = INVALID_METRIC_VALUE
        metrics["sortino"] = INVALID_METRIC_VALUE
        metrics["cagr"] = INVALID_METRIC_VALUE


# ---------------------------------------------------------------------------
# OOS boundary force-flat (§4.4 of the fix plan, OOS1)
# ---------------------------------------------------------------------------

# equity floor — kept in sync with donor.backtest.calculate_equity_curve
_EQUITY_FLOOR = 1e-10


def _apply_oos_force_flat(
    oos_returns: np.ndarray,
    oos_positions: np.ndarray,
    oos_equity: np.ndarray,
    ext_trades_df: Optional[pd.DataFrame],
    oos_boundary: int,
) -> "tuple[np.ndarray, np.ndarray, np.ndarray]":
    """Zero-out carry-in bar-level exposure on the OOS side.

    A **carry-in trade** is one whose ``entry_index < oos_boundary`` and
    ``exit_index >= oos_boundary``: it opened during prepend and its
    exposure or exit commission spills into the OOS slice.

    For such trades we force-flat the OOS starting state on bar level:
    local indices ``[0, exit_local)`` — where
    ``exit_local = max(carry_in exit_index) - oos_boundary`` — have their
    ``oos_returns`` and ``oos_positions`` zeroed, and the OOS equity curve
    is rebuilt from the adjusted returns with ``equity[0] = 1.0``.

    Trade-level filter (``entry_index >= oos_boundary``) is not touched —
    it stays in its existing location further down the pipeline.  Bar-
    level force-flat plus the existing trade filter describe the same
    OOS-originated exposure, closing the bar/trade mismatch (§4.4).

    The function is pure: inputs are not mutated; copies are returned.

    Parameters
    ----------
    oos_returns, oos_positions, oos_equity:
        Arrays already trimmed to the OOS slice (i.e. after
        ``[oos_boundary:]`` slicing).
    ext_trades_df:
        Trades DataFrame as produced by ``run_single_backtest`` on the
        extended slice.  Indices here are relative to the extended slice,
        so ``oos_boundary`` is used to distinguish prepend from OOS.
    oos_boundary:
        Local boundary inside the extended slice (== prepend_applied).

    Returns
    -------
    (oos_returns, oos_positions, oos_equity): copies, force-flat applied.
    """
    # Always return copies so caller never aliases internal arrays.
    r = np.asarray(oos_returns, dtype=np.float64).copy()
    p = np.asarray(oos_positions).copy()
    e_in = np.asarray(oos_equity, dtype=np.float64).copy()

    # No trades -> nothing to force-flat
    if ext_trades_df is None or len(ext_trades_df) == 0:
        return r, p, e_in

    # Defensive: required columns must exist
    if "entry_index" not in ext_trades_df.columns or "exit_index" not in ext_trades_df.columns:
        return r, p, e_in

    # Identify carry-in trades (entered in prepend, exit at/after boundary)
    carry_mask = (
        (ext_trades_df["entry_index"] < oos_boundary)
        & (ext_trades_df["exit_index"] >= oos_boundary)
    )
    if not bool(carry_mask.any()):
        return r, p, e_in

    max_exit_ext = int(ext_trades_df.loc[carry_mask, "exit_index"].max())
    # exit_local is EXCLUSIVE: local bar indices [0, exit_local) were held
    # by a carry-in position.  On bar `exit_local` the position has already
    # transitioned to flat (or to a new OOS-originated trade), so we must
    # NOT zero it out.
    exit_local = max_exit_ext - oos_boundary

    if exit_local <= 0:
        # carry-in closes at the boundary itself — its closing commission
        # sits in returns[oos_boundary - 1] which is NOT in the OOS slice,
        # so the OOS bar arrays already contain only OOS-originated data.
        return r, p, e_in

    # Zero carry-in tail on bar level.
    zr_end = min(exit_local, r.shape[0])
    if zr_end > 0:
        r[:zr_end] = 0.0
    zp_end = min(exit_local, p.shape[0])
    if zp_end > 0:
        p[:zp_end] = 0

    # Rebuild equity curve from adjusted returns.  equity_curve length
    # equals len(returns) + 1 by donor convention; equity[0] = 1.0.
    n_eq = e_in.shape[0]
    new_equity = np.empty(n_eq, dtype=np.float64)
    if n_eq == 0:
        return r, p, new_equity
    new_equity[0] = 1.0
    if n_eq > 1:
        body_len = min(n_eq - 1, r.shape[0])
        if body_len > 0:
            new_equity[1:1 + body_len] = np.cumprod(1.0 + r[:body_len])
        if body_len < n_eq - 1:
            # Defensive: keep invariant len(equity) == len(returns)+1,
            # pad any tail with the last cumulative value (should not occur
            # in canonical runs — returns shorter than positions-1).
            fill = new_equity[body_len] if body_len > 0 else 1.0
            new_equity[1 + body_len:] = fill

    if np.any(new_equity <= 0):
        new_equity = np.maximum(new_equity, _EQUITY_FLOOR)

    return r, p, new_equity


# ---------------------------------------------------------------------------
# Defensive fallback (§W.5.2)
# ---------------------------------------------------------------------------

def _defensive_fallback(
    ext_result,
    grid_point: "GridPoint",
    wf_slice: WFWindowSlice,
    prepend_bars_requested: int,
    prepend_bars_applied: int,
) -> StepResult:
    """Build StepResult for a defensive fallback (no OOS trim possible).

    effective_oos_bars is forced to 0 so the step always receives
    status ``insufficient_bars`` and is excluded from ok mask,
    aggregation, gates, scoring, and ranking.  Metrics and trades from
    the extended run may be contaminated (include prepend bars), so
    they must never be treated as valid OOS data.
    """
    metrics = dict(ext_result.metrics)
    eff_warmup = metrics.pop("effective_warmup", ext_result.effective_warmup)

    logger.warning(
        "Defensive fallback triggered for step %d (gp=%s): "
        "effective_oos_bars forced to 0, metrics/trades invalidated",
        wf_slice.step_index, grid_point.grid_point_id,
    )

    # Invalidate all metrics that could be mistaken for valid OOS data.
    # num_trades is zeroed (integer count — 0 trades is semantically correct for a
    # step that could not produce an OOS slice).  All other metrics get
    # INVALID_METRIC_VALUE so downstream display shows "no data" rather than
    # a misleading "zero result".  aggregator §5.3 converts INVALID → NaN before
    # any aggregation, so pipeline correctness is unaffected.
    metrics["num_trades"] = 0
    metrics["sum_pnl_pct"] = INVALID_METRIC_VALUE
    metrics["win_rate"] = INVALID_METRIC_VALUE
    metrics["avg_trade"] = INVALID_METRIC_VALUE
    metrics["profit_factor"] = INVALID_METRIC_VALUE
    metrics["sharpe"] = INVALID_METRIC_VALUE
    metrics["sortino"] = INVALID_METRIC_VALUE
    metrics["cagr"] = INVALID_METRIC_VALUE
    metrics["max_drawdown"] = INVALID_METRIC_VALUE

    return StepResult(
        grid_point_id=grid_point.grid_point_id,
        wf_step=wf_slice.step_index + 1,
        test_start_idx=wf_slice.test_start_idx,
        test_end_idx=wf_slice.test_end_idx,
        metrics=metrics,
        oos_trades_df=None,
        prepend_bars_requested=prepend_bars_requested,
        prepend_bars_applied=prepend_bars_applied,
        used_prepend=False,
        used_legacy_oos_path=False,
        used_defensive_fallback=True,
        oos_boundary_index=prepend_bars_applied,
        warmup_used=ext_result.warmup,
        warmup_effective=eff_warmup,
        effective_oos_bars=0,
        early_exit=ext_result.early_exit,
        filter_diagnostics_oos=None,
        filter_diagnostics_summary=None,
        filter_config_snapshot=getattr(ext_result, "filter_config_snapshot", None),
    )
