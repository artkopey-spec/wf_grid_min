"""
Candidate-level aggregator for WF grid search.

Aggregation policy (§5):
  §5.2  Mask:     only rows with step_status == "ok" participate.
  §5.3  Sentinel: INVALID_METRIC_VALUE (-999.0) → NaN before aggregation.
  §5.4  Capped:   profit_factor == MAX_VALID_METRIC → NaN;
                  sharpe        == MAX_VALID_METRIC → NaN;
                  sortino       == MAX_VALID_METRIC → NaN;
                  cagr          == MAX_VALID_METRIC → NaN.
  §5.5  Reliability: n_ok_steps, n_total_steps, ok_ratio per candidate.

Per-metric aggregates produced: Mean, Std, Min, Max, Median.
  - Std with < 2 valid values → NaN (§6.3 xlsx spec).
  - All-NaN metric → all aggregates NaN (§6.3).

Additional column: profitable_segments_count (§7 xlsx spec).
  - Count of ok steps where sum_pnl_pct > 0 (after sentinel→NaN substitution).

Trade-layer (step_oos_long) is NOT modified here; only ok-masked aggregate
rows are returned.  The raw step_oos_long with all statuses is preserved for
export/diagnostics downstream.
"""

from __future__ import annotations

import logging
from typing import List

import numpy as np
import pandas as pd

from wf_grid.config.schema import INVALID_METRIC_VALUE, MAX_VALID_METRIC
from wf_grid.status.status_model import StepStatus

logger = logging.getLogger(__name__)


# Metrics aggregated per §5.1
_PHASE_A_METRICS: List[str] = [
    "sum_pnl_pct",
    "sharpe",
    "sortino",
    "max_drawdown",
    "cagr",
    "win_rate",
    "num_trades",
    "profit_factor",
    "avg_trade",
]

# Metrics where MAX_VALID_METRIC is treated as capped-extreme and zeroed (§5.4).
# Explicit list — do not generalise to "all ratio metrics" automatically.
# FIX-2.4: added sortino and cagr (donor can produce 9999.0 sentinel for these).
_CAPPED_METRICS: dict[str, float] = {
    "profit_factor": MAX_VALID_METRIC,
    "sharpe": MAX_VALID_METRIC,
    "sortino": MAX_VALID_METRIC,
    "cagr": MAX_VALID_METRIC,
}

# Stat suffixes produced per metric
_STAT_SUFFIXES: List[str] = ["Mean", "Std", "Min", "Max", "Median"]


def aggregate_candidates(
    step_oos_long: pd.DataFrame,
    config,
) -> pd.DataFrame:
    """
    Aggregate step_oos_long into candidate-level rows.

    Parameters
    ----------
    step_oos_long:
        Output of collect_oos_steps — one row per (grid_point_id, wf_step).
    config:
        Validated GridConfig (not used directly, kept for API symmetry).

    Returns
    -------
    pd.DataFrame
        One row per grid_point_id.
        Columns:
          - grid_point_id
          - n_ok_steps, n_total_steps, ok_ratio
          - {metric}_{stat} for metric in _PHASE_A_METRICS, stat in _STAT_SUFFIXES
          - profitable_segments_count
          - total_oos_trades
          - has_defensive_fallback_steps
    """
    if step_oos_long.empty:
        return _empty_aggregated_df()

    # --- §5.2: ok mask ---
    ok_mask = step_oos_long["step_status"] == StepStatus.OK.value

    rows = []
    for gp_id, group in step_oos_long.groupby("grid_point_id", sort=False):
        ok_rows = group[group["step_status"] == StepStatus.OK.value]

        n_total = len(group)
        n_ok = len(ok_rows)
        ok_ratio = n_ok / n_total if n_total > 0 else 0.0

        agg_row: dict = {
            "grid_point_id": gp_id,
            "n_ok_steps": n_ok,
            "n_total_steps": n_total,
            "ok_ratio": ok_ratio,
        }

        first_row = group.iloc[0]
        for id_col in ("atr_period", "multiplier", "trade_mode"):
            if id_col in group.columns:
                agg_row[id_col] = first_row[id_col]

        # FIX-2.3: max_drawdown sign guard — warn if ok rows have positive values.
        # Convention (§0.3): max_drawdown <= 0.  Positive value breaks gate / ranking.
        if n_ok > 0 and "max_drawdown" in ok_rows.columns:
            positive_dd = ok_rows["max_drawdown"] > 0
            if positive_dd.any():
                bad_vals = ok_rows.loc[positive_dd, "max_drawdown"].tolist()
                logger.warning(
                    "aggregate_candidates: grid_point_id=%r has %d ok step(s) with "
                    "max_drawdown > 0 — convention requires max_drawdown <= 0; "
                    "check donor backtest output. Values: %s",
                    gp_id,
                    len(bad_vals),
                    bad_vals,
                )

        for metric in _PHASE_A_METRICS:
            if metric not in ok_rows.columns:
                for stat in _STAT_SUFFIXES:
                    agg_row[f"{metric}_{stat}"] = np.nan
                continue

            # §5.3: sentinel → NaN
            series = ok_rows[metric].replace(INVALID_METRIC_VALUE, np.nan)

            # §5.4: capped-extreme → NaN
            if metric in _CAPPED_METRICS:
                cap = _CAPPED_METRICS[metric]
                series = series.where(series != cap, np.nan)

            values = series.dropna()
            n_valid = len(values)

            if n_valid == 0:
                for stat in _STAT_SUFFIXES:
                    agg_row[f"{metric}_{stat}"] = np.nan
            else:
                agg_row[f"{metric}_Mean"] = float(values.mean())
                agg_row[f"{metric}_Std"] = float(values.std(ddof=1)) if n_valid >= 2 else np.nan
                agg_row[f"{metric}_Min"] = float(values.min())
                agg_row[f"{metric}_Max"] = float(values.max())
                agg_row[f"{metric}_Median"] = float(values.median())

        # profitable_segments_count (§7 xlsx spec):
        # ok steps where sum_pnl_pct is valid (not sentinel/NaN) and > 0
        if "sum_pnl_pct" in ok_rows.columns:
            pnl = ok_rows["sum_pnl_pct"].replace(INVALID_METRIC_VALUE, np.nan)
            agg_row["profitable_segments_count"] = int((pnl > 0).sum())
        else:
            agg_row["profitable_segments_count"] = 0

        # sum_pnl_pct_Sum: total PnL across all valid ok segments
        if "sum_pnl_pct" in ok_rows.columns:
            pnl_valid = ok_rows["sum_pnl_pct"].replace(INVALID_METRIC_VALUE, np.nan).dropna()
            agg_row["sum_pnl_pct_Sum"] = float(pnl_valid.sum()) if len(pnl_valid) > 0 else np.nan
        else:
            agg_row["sum_pnl_pct_Sum"] = np.nan

        # FIX-3.1: total_oos_trades — sum of num_trades across ok steps
        if "num_trades" in ok_rows.columns:
            trades_series = ok_rows["num_trades"].replace(INVALID_METRIC_VALUE, np.nan)
            agg_row["total_oos_trades"] = int(trades_series.sum()) if not trades_series.isna().all() else 0
        else:
            agg_row["total_oos_trades"] = 0

        # FIX-3.1: has_defensive_fallback_steps — any step in group used fallback
        if "used_defensive_fallback" in group.columns:
            agg_row["has_defensive_fallback_steps"] = bool(group["used_defensive_fallback"].any())
        else:
            agg_row["has_defensive_fallback_steps"] = False

        rows.append(agg_row)

    df = pd.DataFrame(rows)
    if df.empty:
        return _empty_aggregated_df()

    # Stable column order — include only columns present in the result
    ordered = [c for c in _output_columns() if c in df.columns]
    df = df[ordered]
    return df.reset_index(drop=True)


def _output_columns() -> List[str]:
    """Canonical output column order for aggregated DataFrame."""
    cols = ["grid_point_id", "atr_period", "multiplier", "trade_mode",
            "n_ok_steps", "n_total_steps", "ok_ratio"]
    for m in _PHASE_A_METRICS:
        for s in _STAT_SUFFIXES:
            cols.append(f"{m}_{s}")
    cols.append("profitable_segments_count")
    cols.append("sum_pnl_pct_Sum")
    cols.append("total_oos_trades")
    cols.append("has_defensive_fallback_steps")
    return cols


def _empty_aggregated_df() -> pd.DataFrame:
    """Return empty DataFrame with correct schema."""
    return pd.DataFrame(columns=_output_columns())
