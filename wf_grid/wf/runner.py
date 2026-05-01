"""
WF runner — iterate over WF slices for one grid point.

Calls step_executor for each step, assigns step_status via A3 status model.
Per reviewer note: runtime_error / invalid are assigned HERE before
assign_step_status, so a stronger error status is never overwritten.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

import numpy as np
import pandas as pd

from supertrend_optimizer.utils.warmup import calculate_warmup
from supertrend_optimizer.utils.time_utils import WFWindowSlice

from wf_grid.status.status_model import StepStatus, assign_step_status
from wf_grid.wf.step_executor import StepResult, execute_oos_step, execute_train_step

if TYPE_CHECKING:
    from wf_grid.config.schema import GridConfig
    from wf_grid.grid.enumeration import GridPoint

logger = logging.getLogger(__name__)


def run_wf_for_grid_point(
    grid_point: "GridPoint",
    wf_slices: List[WFWindowSlice],
    full_data: pd.DataFrame,
    config: "GridConfig",
    prepend_bars_requested: int,
    zigzag_global_stats: object = None,
) -> List[StepResult]:
    """
    Run walk-forward for one grid point across all WF slices.

    For each slice:
    1. Execute OOS step (canonical prepend path).
    2. Assign step_status from metrics/effective_oos_bars via assign_step_status.
    3. On runtime exception → StepResult with status=runtime_error, error_message set.

    Train execution is separate (execute_train_step) and called by the pipeline
    when needed; this function only handles OOS.

    Parameters
    ----------
    grid_point:
        The grid point to evaluate.
    wf_slices:
        Ordered list of WF windows.
    full_data:
        Full OHLC DataFrame with columns open/high/low/close.
    config:
        Validated GridConfig with resolved_periods_per_year.
    prepend_bars_requested:
        Result of calculate_warmup(len(full_data), raw_config).

    Returns
    -------
    list[StepResult]
        One StepResult per WF slice.
    """
    full_open = full_data["open"].values
    full_high = full_data["high"].values
    full_low = full_data["low"].values
    full_close = full_data["close"].values
    full_index = full_data.index

    results: List[StepResult] = []

    for wf_slice in wf_slices:
        try:
            step_result = execute_oos_step(
                grid_point=grid_point,
                wf_slice=wf_slice,
                full_open=full_open,
                full_high=full_high,
                full_low=full_low,
                full_close=full_close,
                full_index=full_index,
                config=config,
                prepend_bars_requested=prepend_bars_requested,
                zigzag_global_stats=zigzag_global_stats,
            )
        except Exception as exc:
            logger.error(
                "Runtime error: gp=%s step=%d error=%s",
                grid_point.grid_point_id, wf_slice.step_index, exc,
            )
            step_result = _make_error_result(
                grid_point, wf_slice, prepend_bars_requested, exc,
            )
            results.append(step_result)
            continue

        results.append(step_result)

    return results


def run_wf_train_for_grid_point(
    grid_point: "GridPoint",
    wf_slices: List[WFWindowSlice],
    full_data: pd.DataFrame,
    config: "GridConfig",
    zigzag_global_stats: object = None,
) -> List[StepResult]:
    """
    Run walk-forward train execution for one grid point across all WF slices.

    For each slice: execute_train_step (no prepend).
    On runtime exception → StepResult with status=runtime_error.
    """
    full_open = full_data["open"].values
    full_high = full_data["high"].values
    full_low = full_data["low"].values
    full_close = full_data["close"].values
    full_index = full_data.index

    results: List[StepResult] = []

    for wf_slice in wf_slices:
        try:
            step_result = execute_train_step(
                grid_point=grid_point,
                wf_slice=wf_slice,
                full_open=full_open,
                full_high=full_high,
                full_low=full_low,
                full_close=full_close,
                full_index=full_index,
                config=config,
                zigzag_global_stats=zigzag_global_stats,
            )
        except Exception as exc:
            logger.error(
                "Train runtime error: gp=%s step=%d error=%s",
                grid_point.grid_point_id, wf_slice.step_index, exc,
            )
            step_result = _make_error_result(
                grid_point, wf_slice, prepend_bars_requested=0, exc=exc,
            )
            results.append(step_result)
            continue

        results.append(step_result)

    return results


def compute_prepend_bars(full_data: pd.DataFrame, config_dict: dict) -> int:
    """Compute prepend_bars_requested once per pipeline (§W.4.2 step 1)."""
    return calculate_warmup(len(full_data), config_dict)


# ---------------------------------------------------------------------------
# Error result helper
# ---------------------------------------------------------------------------

def _make_error_result(
    grid_point: "GridPoint",
    wf_slice: WFWindowSlice,
    prepend_bars_requested: int,
    exc: Exception,
) -> StepResult:
    """Create a StepResult for a runtime error."""
    return StepResult(
        grid_point_id=grid_point.grid_point_id,
        wf_step=wf_slice.step_index + 1,
        test_start_idx=wf_slice.test_start_idx,
        test_end_idx=wf_slice.test_end_idx,
        metrics={
            "num_trades": 0,
            "sum_pnl_pct": INVALID_METRIC_VALUE,
            "sharpe": INVALID_METRIC_VALUE,
            "sortino": INVALID_METRIC_VALUE,
            "cagr": INVALID_METRIC_VALUE,
            "max_drawdown": INVALID_METRIC_VALUE,
            "win_rate": INVALID_METRIC_VALUE,
            "profit_factor": INVALID_METRIC_VALUE,
            "avg_trade": INVALID_METRIC_VALUE,
            "net_pnl_pct": INVALID_METRIC_VALUE,
        },
        oos_trades_df=None,
        prepend_bars_requested=prepend_bars_requested,
        prepend_bars_applied=0,
        used_prepend=False,
        used_legacy_oos_path=False,
        used_defensive_fallback=False,
        oos_boundary_index=0,
        warmup_used=0,
        warmup_effective=0,
        effective_oos_bars=0,
        early_exit=False,
        error_message=str(exc),
        error_type=type(exc).__name__,
    )


# Import for _make_error_result default metrics
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE  # noqa: E402
