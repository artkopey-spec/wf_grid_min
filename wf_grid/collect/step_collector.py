"""
Step collector — assembles step_oos_long and step_train_long DataFrames.

§2.2  step_oos_long schema:
    identity:     grid_point_id, wf_step
    window:       test_start_idx, test_end_idx
    status:       step_status
    metrics:      sum_pnl_pct, sharpe, sortino, max_drawdown, cagr,
                  win_rate, num_trades, profit_factor, avg_trade
    diagnostics:  prepend_bars_requested, prepend_bars_applied,
                  used_prepend, used_legacy_oos_path, oos_boundary_index,
                  warmup_used, warmup_effective, effective_oos_bars
    error info:   error_message, error_type

§11.1 invariants validated after collection:
    - Completeness: |rows| == expected_grid_size * wf_steps_per_point
    - Uniqueness:   (grid_point_id, wf_step) pairs are distinct

Schema parity (§11.1 / §14.18 / §17.1.1):
    Filter summary columns are an OPT-IN extension.  They are appended to
    the base schema ONLY when at least one StepResult in the collected run
    has filter_diagnostics_summary is not None (i.e. trade_filter was
    enabled for that run).  When filter is disabled or absent, exports
    contain ONLY the base columns, preserving baseline schema parity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

from wf_grid.status.status_model import StepStatus, assign_step_status
from wf_grid.wf.step_executor import StepResult

if TYPE_CHECKING:
    from wf_grid.grid.enumeration import GridPoint


# ---------------------------------------------------------------------------
# Base column lists (no filter summary block)
# ---------------------------------------------------------------------------

# Column order: identity → window → status → metrics → diagnostics → errors
_OOS_COLUMNS_BASE: List[str] = [
    # identity (§2.2)
    "grid_point_id",
    "atr_period",
    "multiplier",
    "trade_mode",
    "wf_step",
    # window
    "test_start_idx",
    "test_end_idx",
    # status
    "step_status",
    # OOS metrics (§2.2, §5.1)
    "sum_pnl_pct",
    "sharpe",
    "sortino",
    "max_drawdown",
    "cagr",
    "win_rate",
    "num_trades",
    "profit_factor",
    "avg_trade",
    # prepend diagnostics (§W.8)
    "prepend_bars_requested",
    "prepend_bars_applied",
    "used_prepend",
    "used_legacy_oos_path",
    "used_defensive_fallback",
    "oos_boundary_index",
    "warmup_used",
    "warmup_effective",
    "effective_oos_bars",
    # early exit
    "early_exit",
    # error info (for runtime_error / invalid steps)
    "error_message",
    "error_type",
]

# Column list for step_train_long base — same identity/window/status/metrics/
# errors, no prepend diagnostics (train never uses prepend per §W.6).
_TRAIN_COLUMNS_BASE: List[str] = [
    "grid_point_id",
    "atr_period",
    "multiplier",
    "trade_mode",
    "wf_step",
    "train_start_idx",
    "train_end_idx",
    "step_status",
    "sum_pnl_pct",
    "sharpe",
    "sortino",
    "max_drawdown",
    "cagr",
    "win_rate",
    "num_trades",
    "profit_factor",
    "avg_trade",
    "warmup_used",
    "warmup_effective",
    "effective_train_bars",
    "early_exit",
    "error_message",
    "error_type",
]

# ---------------------------------------------------------------------------
# Filter summary extension columns (appended only when filter was enabled)
# ---------------------------------------------------------------------------

# Shared by both OOS and train exports.  Order: §10.6.4 required fields
# first, then additional informational fields.
_ZIGZAG_FILTER_SUMMARY_COLUMNS: List[str] = [
    # §10.6.4 required
    "filter_states_visited",
    "n_bars_in_off",
    "n_bars_in_wait_first_st_flip",
    "n_bars_in_freeze",
    "n_bars_in_monitoring",
    "n_bars_in_counting_zz_legs",
    "n_bars_in_stopping",
    "n_filter_blocked_entries",
    "lifecycle_starts_count",
    "median_stop_triggered_count",
    # exit-off mode B (docs/plan_exit_off_modes.txt)
    "zz_leg_stop_triggered_count",
    "wakeup_exit_cycle_take_profit_count",
    "exit_off_mode",
    "exit_off_zz_leg_count",
    # Plan v3: exit_b_immediate_off echo + per-period fired count
    "exit_b_immediate_off",
    "exit_b_immediate_off_count",
    # additional informational
    "filter_diagnostics_available",
    "trigger_count_candidate_threshold",
    "trigger_count_confirmed_median",
    "trigger_count_both",
    "stopping_started_count",
]

_ZIGZAG_DISCRIMINATOR_KEYS = frozenset(
    {
        "trigger_count_candidate_threshold",
        "trigger_count_confirmed_median",
        "trigger_count_both",
        "median_stop_triggered_count",
        "zz_leg_stop_triggered_count",
        "wakeup_exit_cycle_take_profit_count",
        "exit_off_mode",
        "exit_off_zz_leg_count",
        "exit_b_immediate_off",
        "exit_b_immediate_off_count",
        "stopping_started_count",
    }
)

_VOLUME_FILTER_SUMMARY_COLUMNS: List[str] = [
    "n_volume_blocked_start_attempts",
    "n_volume_blocked_start_attempts_long",
    "n_volume_blocked_start_attempts_short",
    "n_volume_blocked_start_attempts_unknown_direction",
    "n_volume_warmup_blocked_start_attempts",
    "n_volume_below_baseline_blocked_start_attempts",
    "n_volume_above_baseline_blocked_start_attempts",
    "n_volume_baseline_zero_blocked_start_attempts",
    "n_volume_direction_warmup_blocked_start_attempts",
    "n_volume_unknown_direction_blocked_start_attempts",
    "n_volume_trade_mode_disallowed_direction_blocked_start_attempts",
    "n_volume_cycle_direction_mismatch_blocked_bars",
    "n_volume_low_regime_bars",
    "n_volume_normal_regime_bars",
    "n_volume_high_regime_bars",
    "avg_median_relative_volume",
    "n_volume_started_cycles",
    "n_volume_suppressed_cycles",
]

_FILTER_SUMMARY_COLUMNS: List[str] = (
    _ZIGZAG_FILTER_SUMMARY_COLUMNS + _VOLUME_FILTER_SUMMARY_COLUMNS
)

# Back-compat aliases: callers that reference the old monolithic names still
# get the correct combined list.
_OOS_COLUMNS: List[str] = _OOS_COLUMNS_BASE + _FILTER_SUMMARY_COLUMNS
_TRAIN_COLUMNS: List[str] = _TRAIN_COLUMNS_BASE + _FILTER_SUMMARY_COLUMNS


class CollectionError(ValueError):
    """Raised when collected DataFrame fails invariant checks."""


def _unpack_filter_summary(summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten filter_diagnostics_summary into step_oos/train_long row columns.

    §10.6.4 required fields come first; additional informational fields follow.
    Returns a dict keyed by _FILTER_SUMMARY_COLUMNS with None values when
    the filter is disabled (summary is None).
    """
    if summary is None:
        return {k: None for k in _FILTER_SUMMARY_COLUMNS}

    # Map summary dict keys → column names (with one rename for diagnostics_available)
    _summary_key_map = {
        "filter_states_visited": "filter_states_visited",
        "n_bars_in_off": "n_bars_in_off",
        "n_bars_in_wait_first_st_flip": "n_bars_in_wait_first_st_flip",
        "n_bars_in_freeze": "n_bars_in_freeze",
        "n_bars_in_monitoring": "n_bars_in_monitoring",
        "n_bars_in_counting_zz_legs": "n_bars_in_counting_zz_legs",
        "n_bars_in_stopping": "n_bars_in_stopping",
        "n_filter_blocked_entries": "n_filter_blocked_entries",
        "lifecycle_starts_count": "lifecycle_starts_count",
        "median_stop_triggered_count": "median_stop_triggered_count",
        "zz_leg_stop_triggered_count": "zz_leg_stop_triggered_count",
        "wakeup_exit_cycle_take_profit_count": (
            "wakeup_exit_cycle_take_profit_count"
        ),
        "exit_off_mode": "exit_off_mode",
        "exit_off_zz_leg_count": "exit_off_zz_leg_count",
        "exit_b_immediate_off": "exit_b_immediate_off",
        "exit_b_immediate_off_count": "exit_b_immediate_off_count",
        "diagnostics_available": "filter_diagnostics_available",
        "trigger_count_candidate_threshold": "trigger_count_candidate_threshold",
        "trigger_count_confirmed_median": "trigger_count_confirmed_median",
        "trigger_count_both": "trigger_count_both",
        "stopping_started_count": "stopping_started_count",
    }
    _summary_key_map.update({k: k for k in _VOLUME_FILTER_SUMMARY_COLUMNS})
    row: Dict[str, Any] = {k: None for k in _FILTER_SUMMARY_COLUMNS}
    for src_key, col_name in _summary_key_map.items():
        if src_key in summary:
            row[col_name] = summary[src_key]
    return row


def _has_filter_summary(grid_results: Dict[str, List[StepResult]]) -> bool:
    """Return True if at least one StepResult carries a filter_diagnostics_summary.

    Used to decide whether to append _FILTER_SUMMARY_COLUMNS to the export
    schema.  When the filter is disabled or absent for the entire run, no
    filter columns are emitted (baseline schema parity — §11.1 / §14.18 /
    §17.1.1).
    """
    for step_results in grid_results.values():
        for sr in step_results:
            if getattr(sr, "filter_diagnostics_summary", None) is not None:
                return True
    return False


def _filter_summary_columns_for(
    grid_results: Dict[str, List[StepResult]],
) -> List[str]:
    include_zigzag = False
    include_volume = False
    for step_results in grid_results.values():
        for sr in step_results:
            summary = getattr(sr, "filter_diagnostics_summary", None)
            if not summary:
                continue
            if any(k in summary for k in _ZIGZAG_DISCRIMINATOR_KEYS):
                include_zigzag = True
            if any(k in summary for k in _VOLUME_FILTER_SUMMARY_COLUMNS):
                include_volume = True
    cols: List[str] = []
    if include_zigzag:
        cols.extend(_ZIGZAG_FILTER_SUMMARY_COLUMNS)
    if include_volume:
        cols.extend(_VOLUME_FILTER_SUMMARY_COLUMNS)
    return cols


def collect_oos_steps(
    grid_results: Dict[str, List[StepResult]],
    config,
    expected_n_steps: Optional[int] = None,
    grid_points: Optional[List["GridPoint"]] = None,
) -> pd.DataFrame:
    """
    Assemble step_oos_long DataFrame from grid execution results.

    Parameters
    ----------
    grid_results:
        Mapping grid_point_id → list of StepResult (one per WF step).
    config:
        Validated GridConfig (used to call assign_step_status).
    expected_n_steps:
        Expected number of WF steps per grid point.  If provided, used
        to validate completeness.  Inferred from max list length if None.
    grid_points:
        Optional list of GridPoint objects; used to populate structured
        identity columns (atr_period, multiplier, trade_mode) directly
        instead of relying on grid_point_id string parsing.

    Returns
    -------
    pd.DataFrame
        step_oos_long with schema per §2.2, ordered by (grid_point_id, wf_step).
        Filter summary columns are appended only when trade_filter was enabled
        for this run (schema parity — §11.1 / §14.18).

    Raises
    ------
    CollectionError
        If uniqueness or completeness invariants are violated.
    """
    gp_lookup = _build_gp_lookup(grid_points)
    filter_columns = _filter_summary_columns_for(grid_results)
    include_filter = bool(filter_columns)
    columns = _OOS_COLUMNS_BASE + filter_columns

    rows = []

    for gp_id, step_results in grid_results.items():
        gp_identity = gp_lookup.get(gp_id, {})
        for sr in step_results:
            # Assign step_status (A3 logic).
            # Per reviewer note: runtime_error / invalid are already set in
            # StepResult by the runner; we must NOT overwrite them with
            # assign_step_status (which only handles ok/no_trades/insufficient_bars).
            if sr.error_message is not None:
                # Runner already tagged this as runtime_error
                status = StepStatus.RUNTIME_ERROR
            else:
                status = assign_step_status(
                    metrics=sr.metrics,
                    effective_oos_bars=sr.effective_oos_bars,
                    config=config,
                )

            row: Dict[str, Any] = {
                "grid_point_id": gp_id,
                "atr_period": gp_identity.get("atr_period"),
                "multiplier": gp_identity.get("multiplier"),
                "trade_mode": gp_identity.get("trade_mode"),
                "wf_step": sr.wf_step,
                "test_start_idx": sr.test_start_idx,
                "test_end_idx": sr.test_end_idx,
                "step_status": status.value,
                # metrics
                # Hard-required metrics (sum_pnl_pct, max_drawdown, num_trades)
                # MUST NOT silently default.  A missing key in sr.metrics indicates
                # a runner contract violation and must propagate as NaN so that
                # validate_metric_contracts() fails the ok-step closed (§4.3, M3).
                "sum_pnl_pct": sr.metrics.get("sum_pnl_pct"),
                "sharpe": sr.metrics.get("sharpe"),
                "sortino": sr.metrics.get("sortino"),
                "max_drawdown": sr.metrics.get("max_drawdown"),
                "cagr": sr.metrics.get("cagr"),
                # win_rate is a soft metric; NaN is legitimate "no data".
                "win_rate": sr.metrics.get("win_rate"),
                # num_trades is hard-required — no silent default.
                "num_trades": sr.metrics.get("num_trades"),
                "profit_factor": sr.metrics.get("profit_factor"),
                "avg_trade": sr.metrics.get("avg_trade"),
                # prepend diagnostics — all fields per §W.8
                "prepend_bars_requested": sr.prepend_bars_requested,
                "prepend_bars_applied": sr.prepend_bars_applied,
                "used_prepend": sr.used_prepend,
                "used_legacy_oos_path": sr.used_legacy_oos_path,
                "used_defensive_fallback": sr.used_defensive_fallback,
                "oos_boundary_index": sr.oos_boundary_index,
                "warmup_used": sr.warmup_used,
                "warmup_effective": sr.warmup_effective,
                "effective_oos_bars": sr.effective_oos_bars,
                "early_exit": sr.early_exit,
                # error info
                "error_message": sr.error_message,
                "error_type": sr.error_type,
            }
            if include_filter:
                row.update(
                    _unpack_filter_summary(
                        getattr(sr, "filter_diagnostics_summary", None)
                    )
                )
            rows.append(row)

    df = pd.DataFrame(rows, columns=columns)

    _validate_oos_invariants(df, grid_results, expected_n_steps)

    df = df.sort_values(["grid_point_id", "wf_step"]).reset_index(drop=True)
    return df


def collect_train_steps(
    grid_results: Dict[str, List[StepResult]],
    config,
    expected_n_steps: Optional[int] = None,
    grid_points: Optional[List["GridPoint"]] = None,
) -> pd.DataFrame:
    """
    Assemble step_train_long DataFrame from train execution results.

    Same invariants as collect_oos_steps (completeness, uniqueness).
    Train results have no prepend diagnostics per §W.6.
    Filter summary columns are appended only when trade_filter was enabled
    for this run (schema parity — §11.1 / §14.18).

    Parameters
    ----------
    grid_results:
        Mapping grid_point_id → list of train StepResult.
    config:
        Validated GridConfig.
    expected_n_steps:
        Expected WF steps per grid point.
    grid_points:
        Optional list of GridPoint objects for structured identity columns.

    Returns
    -------
    pd.DataFrame
        step_train_long.
    """
    gp_lookup = _build_gp_lookup(grid_points)
    filter_columns = _filter_summary_columns_for(grid_results)
    include_filter = bool(filter_columns)
    columns = _TRAIN_COLUMNS_BASE + filter_columns

    rows = []

    for gp_id, step_results in grid_results.items():
        gp_identity = gp_lookup.get(gp_id, {})
        for sr in step_results:
            if sr.error_message is not None:
                status = StepStatus.RUNTIME_ERROR
            else:
                status = assign_step_status(
                    metrics=sr.metrics,
                    effective_oos_bars=sr.effective_oos_bars,
                    config=config,
                )

            row: Dict[str, Any] = {
                "grid_point_id": gp_id,
                "atr_period": gp_identity.get("atr_period"),
                "multiplier": gp_identity.get("multiplier"),
                "trade_mode": gp_identity.get("trade_mode"),
                "wf_step": sr.wf_step,
                "train_start_idx": sr.test_start_idx,   # train uses test_start/end fields
                "train_end_idx": sr.test_end_idx,
                "step_status": status.value,
                "sum_pnl_pct": sr.metrics.get("sum_pnl_pct", 0.0),
                "sharpe": sr.metrics.get("sharpe"),
                "sortino": sr.metrics.get("sortino"),
                "max_drawdown": sr.metrics.get("max_drawdown"),
                "cagr": sr.metrics.get("cagr"),
                "win_rate": sr.metrics.get("win_rate", 0.0),
                "num_trades": sr.metrics.get("num_trades", 0),
                "profit_factor": sr.metrics.get("profit_factor"),
                "avg_trade": sr.metrics.get("avg_trade"),
                "warmup_used": sr.warmup_used,
                "warmup_effective": sr.warmup_effective,
                "effective_train_bars": sr.effective_oos_bars,
                "early_exit": sr.early_exit,
                "error_message": sr.error_message,
                "error_type": sr.error_type,
            }
            if include_filter:
                row.update(
                    _unpack_filter_summary(
                        getattr(sr, "filter_diagnostics_summary", None)
                    )
                )
            rows.append(row)

    df = pd.DataFrame(rows, columns=columns)

    _validate_train_invariants(df, grid_results, expected_n_steps)

    df = df.sort_values(["grid_point_id", "wf_step"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# GridPoint lookup builder
# ---------------------------------------------------------------------------

def _build_gp_lookup(
    grid_points: Optional[List["GridPoint"]],
) -> Dict[str, dict]:
    """Build {grid_point_id: {atr_period, multiplier, trade_mode}} from GridPoint list."""
    if not grid_points:
        return {}
    return {
        gp.grid_point_id: {
            "atr_period": gp.atr_period,
            "multiplier": gp.multiplier,
            "trade_mode": gp.trade_mode,
        }
        for gp in grid_points
    }


# ---------------------------------------------------------------------------
# Invariant validation helpers (§11.1)
# ---------------------------------------------------------------------------

def _validate_oos_invariants(
    df: pd.DataFrame,
    grid_results: Dict[str, List[StepResult]],
    expected_n_steps: Optional[int],
) -> None:
    """Check completeness and uniqueness for OOS DataFrame."""

    # Uniqueness: (grid_point_id, wf_step)
    dup = df.duplicated(subset=["grid_point_id", "wf_step"])
    if dup.any():
        dupes = df[dup][["grid_point_id", "wf_step"]].to_dict(orient="records")
        raise CollectionError(
            f"step_oos_long uniqueness violated: duplicate (grid_point_id, wf_step) pairs: "
            f"{dupes[:5]}"
        )

    # Completeness
    n_gp = len(grid_results)
    if n_gp == 0:
        return

    n_steps = expected_n_steps
    if n_steps is None:
        n_steps = max(len(v) for v in grid_results.values())

    expected_rows = n_gp * n_steps
    if len(df) != expected_rows:
        raise CollectionError(
            f"step_oos_long completeness violated: expected {expected_rows} rows "
            f"({n_gp} grid points × {n_steps} WF steps), got {len(df)}"
        )


def _validate_train_invariants(
    df: pd.DataFrame,
    grid_results: Dict[str, List[StepResult]],
    expected_n_steps: Optional[int],
) -> None:
    """Check completeness and uniqueness for train DataFrame."""
    dup = df.duplicated(subset=["grid_point_id", "wf_step"])
    if dup.any():
        dupes = df[dup][["grid_point_id", "wf_step"]].to_dict(orient="records")
        raise CollectionError(
            f"step_train_long uniqueness violated: duplicate (grid_point_id, wf_step) pairs: "
            f"{dupes[:5]}"
        )

    n_gp = len(grid_results)
    if n_gp == 0:
        return

    n_steps = expected_n_steps
    if n_steps is None:
        n_steps = max(len(v) for v in grid_results.values())

    expected_rows = n_gp * n_steps
    if len(df) != expected_rows:
        raise CollectionError(
            f"step_train_long completeness violated: expected {expected_rows} rows "
            f"({n_gp} grid points × {n_steps} WF steps), got {len(df)}"
        )
