"""
Core builder for BucketMatrix_Median.

Builds a median-based bucket × WF-step matrix from pre-gate ``step_oos_long``.

Public API
----------
build_median_bucket_matrix(step_oos_long, config, *, metric_column) -> pd.DataFrame

The returned DataFrame has 24 + 2N columns (N = total_steps):
    Block A (5):  bucket_param, bucket_key, atr_bucket, mult_bucket_ticks, bucket_size
    Block B+D (2N, interleaved): Step1, DD_Step1, Step2, DD_Step2, ..., StepN, DD_StepN
    Block E (2):  max_drawdown_Median, max_drawdown_Min
    Block C (17): bucket_presence_steps, mean_oos_pnl, median_oos_pnl, std_bucket,
        pct_params_positive_pnl, wins_count, win_steps, top3_count,
        above_median_count, above_median_ratio, presence_count,
        above_median_ratio_present, eligible_median_steps_count,
        above_median_ratio_eligible, bucket_stability_score,
        zone_dominance_score, bucket_balanced_score
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from wf_grid.bucket.assignment import (
    apply_param_buckets,
    compute_expected_bucket_sizes,
    format_bucket_label,
    generate_full_bucket_grid,
)
from wf_grid.config.schema import INVALID_METRIC_VALUE

if TYPE_CHECKING:
    from wf_grid.config.schema import GridConfig

_logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = frozenset({
    "wf_step", "step_status", "atr_period", "multiplier",
    "sum_pnl_pct", "max_drawdown", "trade_mode", "grid_point_id",
})


def build_median_bucket_matrix(
    step_oos_long: pd.DataFrame,
    config: "GridConfig",
    *,
    metric_column: str = "sum_pnl_pct",
) -> pd.DataFrame:
    """Build the full median bucket matrix from pre-gate ``step_oos_long``.

    Parameters
    ----------
    step_oos_long:
        Pre-gate DataFrame with all grid points × all WF steps.
        Must contain ``max_drawdown`` column (equity-based, ≤ 0).
    config:
        Validated ``GridConfig``.
    metric_column:
        Column name for the metric to aggregate.  Only ``"sum_pnl_pct"``
        is supported in MVP.

    Returns
    -------
    pd.DataFrame
        Matrix with ``24 + 2N`` columns (N = total_steps), sorted by
        ``bucket_stability_score`` DESC, then ``atr_bucket`` ASC,
        ``mult_bucket_ticks`` ASC.

        Column layout (interleaved):
            Block A (5):  bucket identifiers
            Block B+D (2N): Step1, DD_Step1, Step2, DD_Step2, ..., StepN, DD_StepN
            Block E (2):  max_drawdown_Median, max_drawdown_Min
            Block C (17): derived metrics — bucket_presence_steps, mean_oos_pnl,
                median_oos_pnl, std_bucket, pct_params_positive_pnl, wins_count,
                win_steps, top3_count, above_median_count, above_median_ratio,
                presence_count, above_median_ratio_present,
                eligible_median_steps_count, above_median_ratio_eligible,
                bucket_stability_score, zone_dominance_score,
                bucket_balanced_score

    Raises
    ------
    NotImplementedError
        If ``metric_column`` is not ``"sum_pnl_pct"``.
    ValueError
        On missing required columns, duplicate rows, multiple trade modes,
        or non-consecutive WF steps.
    """
    # ── 1. metric_column guard ───────────────────────────────────────────
    if metric_column != "sum_pnl_pct":
        raise NotImplementedError(
            f"Only metric_column='sum_pnl_pct' is supported in MVP, "
            f"got {metric_column!r}"
        )

    # ── 2. Empty input ───────────────────────────────────────────────────
    if len(step_oos_long) == 0:
        return pd.DataFrame()

    # ── 3. Required columns guard ────────────────────────────────────────
    missing = _REQUIRED_COLUMNS - set(step_oos_long.columns)
    if missing:
        raise ValueError(
            f"step_oos_long is missing required columns: {sorted(missing)}"
        )

    # ── 4. Duplicate guard ───────────────────────────────────────────────
    if step_oos_long.duplicated(subset=["grid_point_id", "wf_step"]).any():
        raise ValueError(
            "step_oos_long contains duplicate (grid_point_id, wf_step) rows. "
            "Duplicates silently corrupt medians and derived metrics."
        )

    # ── 5. trade_mode guard (on full df, before ok-filter) ───────────────
    n_modes = step_oos_long["trade_mode"].nunique()
    if n_modes > 1:
        modes = sorted(step_oos_long["trade_mode"].unique())
        raise ValueError(
            f"BucketMatrix_Median requires exactly 1 trade_mode, "
            f"found {n_modes}: {modes}"
        )

    # ── 6. total_steps from full df (before ok-filter) ───────────────────
    total_steps = int(step_oos_long["wf_step"].nunique())

    # ── 7. wf_step continuity guard ──────────────────────────────────────
    expected_steps = set(range(1, total_steps + 1))
    actual_steps = set(step_oos_long["wf_step"].unique())
    if actual_steps != expected_steps:
        extra = actual_steps - expected_steps
        missing_steps = expected_steps - actual_steps
        raise ValueError(
            f"wf_step values must be consecutive 1..{total_steps}. "
            f"Missing: {sorted(missing_steps)}, extra: {sorted(extra)}"
        )

    # ── 8. ok-filter (mandatory .copy()) ─────────────────────────────────
    ok_df = step_oos_long[step_oos_long["step_status"] == "ok"].copy()

    # ── 9. Sentinel-cleaning (mandatory assign-back) ─────────────────────
    ok_df[metric_column] = ok_df[metric_column].replace(
        INVALID_METRIC_VALUE, np.nan
    )
    ok_df["max_drawdown"] = ok_df["max_drawdown"].replace(
        INVALID_METRIC_VALUE, np.nan
    )

    # ── 10. Bucket assignment on ok_df ───────────────────────────────────
    bk = config.bucket
    atr_bucket_step = bk.atr_bucket_step
    mult_bucket_step = bk.mult_bucket_step
    min_buckets_for_median = bk.min_buckets_for_median

    if len(ok_df) > 0:
        ok_df = apply_param_buckets(ok_df, atr_bucket_step, mult_bucket_step)

    # ── 11. Full bucket grid from config ─────────────────────────────────
    all_bucket_keys = generate_full_bucket_grid(config)
    if not all_bucket_keys:
        return pd.DataFrame()

    bucket_sizes = compute_expected_bucket_sizes(config)

    # ── 12. Step column names ────────────────────────────────────────────
    step_cols = [f"Step{s}" for s in range(1, total_steps + 1)]

    # ── 13. Accumulate per-bucket per-step values ────────────────────────
    # PnL accumulator: step_bucket_vals[wf_step][(ab, mt)] = [pnl, ...]
    step_bucket_vals: dict[int, dict[tuple[int, int], list[float]]] = {
        s: {} for s in range(1, total_steps + 1)
    }
    # param_bucket_step_vals[(ab, mt)][(atr_period, multiplier)][wf_step] = [pnl, ...]
    param_bucket_step_vals: dict[
        tuple[int, int],
        dict[tuple[int, float], dict[int, list[float]]]
    ] = {}
    # DD accumulator (independent NaN-guard from PnL)
    dd_step_bucket_vals: dict[int, dict[tuple[int, int], list[float]]] = {
        s: {} for s in range(1, total_steps + 1)
    }

    for row in ok_df.itertuples(index=False):
        wf_step = int(row.wf_step)
        ab = int(row.atr_bucket)
        mt = int(row.mult_bucket_ticks)
        bk_key = (ab, mt)

        # PnL block — independent NaN-guard
        pnl_val = getattr(row, metric_column)
        if not pd.isna(pnl_val):
            pnl = float(pnl_val)
            step_bucket_vals[wf_step].setdefault(bk_key, []).append(pnl)
            param_key = (int(row.atr_period), float(row.multiplier))
            if bk_key not in param_bucket_step_vals:
                param_bucket_step_vals[bk_key] = {}
            if param_key not in param_bucket_step_vals[bk_key]:
                param_bucket_step_vals[bk_key][param_key] = {}
            param_bucket_step_vals[bk_key][param_key].setdefault(
                wf_step, []
            ).append(pnl)

        # DD block — independent NaN-guard
        dd_val = getattr(row, "max_drawdown")
        if not pd.isna(dd_val):
            dd_step_bucket_vals[wf_step].setdefault(
                bk_key, []
            ).append(float(dd_val))

    # ── 14. Build rows — Block A + Block B ───────────────────────────────
    rows: list[dict] = []
    for (ab, mt) in all_bucket_keys:
        row_dict: dict = {
            "bucket_param": format_bucket_label(
                ab, mt, atr_bucket_step, mult_bucket_step
            ),
            "bucket_key": f"{ab}_{mt}",
            "atr_bucket": ab,
            "mult_bucket_ticks": mt,
            "bucket_size": bucket_sizes.get((ab, mt), 0),
        }
        for s in range(1, total_steps + 1):
            vals = step_bucket_vals[s].get((ab, mt), [])
            row_dict[f"Step{s}"] = (
                float(np.median(vals)) if vals else float("nan")
            )
        rows.append(row_dict)

    bdf = pd.DataFrame(rows)

    # ── 15. pd.to_numeric coerce on Step columns ────────────────────────
    for col in step_cols:
        if col in bdf.columns:
            bdf[col] = pd.to_numeric(bdf[col], errors="coerce")

    # ── 15b. Risk columns (Block D + Block E) ─────────────────────────
    bdf = _build_risk_columns(
        bdf,
        dd_step_bucket_vals=dd_step_bucket_vals,
        all_bucket_keys=all_bucket_keys,
        total_steps=total_steps,
    )

    # ── 16. Derived metrics (Block C) ────────────────────────────────────
    bdf = _compute_derived_metrics(
        bdf,
        step_cols=step_cols,
        total_steps=total_steps,
        step_bucket_vals=step_bucket_vals,
        param_bucket_step_vals=param_bucket_step_vals,
        min_buckets_for_median=min_buckets_for_median,
    )

    # ── 17. Sort ─────────────────────────────────────────────────────────
    bdf = bdf.sort_values(
        ["bucket_stability_score", "atr_bucket", "mult_bucket_ticks"],
        ascending=[False, True, True],
    ).reset_index(drop=True)

    # ── 18. Final column order (interleaved PnL + DD steps) ──────────────
    interleaved_step_cols: list[str] = []
    for s in range(1, total_steps + 1):
        interleaved_step_cols.append(f"Step{s}")
        interleaved_step_cols.append(f"DD_Step{s}")

    ordered_cols = (
        ["bucket_param", "bucket_key", "atr_bucket", "mult_bucket_ticks",
         "bucket_size"]
        + interleaved_step_cols
        + ["max_drawdown_Median", "max_drawdown_Min"]
        + ["bucket_presence_steps", "mean_oos_pnl", "median_oos_pnl",
           "std_bucket", "pct_params_positive_pnl",
           "wins_count", "win_steps", "top3_count",
           "above_median_count", "above_median_ratio",
           "presence_count", "above_median_ratio_present",
           "eligible_median_steps_count", "above_median_ratio_eligible",
           "bucket_stability_score", "zone_dominance_score",
           "bucket_balanced_score"]
    )
    existing = [c for c in ordered_cols if c in bdf.columns]
    return bdf[existing]


# ---------------------------------------------------------------------------
# Risk columns (Block D + Block E)
# ---------------------------------------------------------------------------

def _build_risk_columns(
    bdf: pd.DataFrame,
    *,
    dd_step_bucket_vals: dict[int, dict[tuple[int, int], list[float]]],
    all_bucket_keys: list[tuple[int, int]],
    total_steps: int,
) -> pd.DataFrame:
    """Compute DD_Step* columns and risk summary columns on *bdf* in-place.

    Contracts
    ---------
    - ``DD_Step{s} = median(max_drawdown)`` across ok-rows of the bucket on
      that step.  NaN if no valid DD values for the bucket/step combination.
    - ``max_drawdown_Median = median(DD_Step1, ..., DD_StepN, skipna=True)``
      — **step-derived**, not raw-level.  Each step has equal weight.
    - ``max_drawdown_Min = min(DD_Step1, ..., DD_StepN, skipna=True)``
      — worst step median DD.  This is NOT the min of all raw individual
      ``max_drawdown`` values; it is the most negative *step median*.

    Notes
    -----
    - ``max_drawdown`` is equity-based: it is valid even when ``num_trades=0``
      (unlike ``sum_pnl_pct`` which is overwritten to 0 for zero-trade rows).
      This is intentional asymmetry inherited from the pipeline.
    - PnL-presence and DD-presence on a step are **independent** concepts.
      A row with ``sum_pnl_pct=NaN`` but valid ``max_drawdown`` contributes
      to DD_Step but not to Step (PnL).  ``bucket_presence_steps`` reflects
      PnL-presence only.
    """
    dd_step_cols: list[str] = []

    for s in range(1, total_steps + 1):
        col_name = f"DD_Step{s}"
        dd_step_cols.append(col_name)
        dd_vals_per_bucket: dict[tuple[int, int], list[float]] = (
            dd_step_bucket_vals.get(s, {})
        )
        col_values: list[float] = []
        for (ab, mt) in all_bucket_keys:
            vals = dd_vals_per_bucket.get((ab, mt), [])
            col_values.append(
                float(np.median(vals)) if vals else float("nan")
            )
        bdf[col_name] = col_values

    for col in dd_step_cols:
        bdf[col] = pd.to_numeric(bdf[col], errors="coerce")

    if dd_step_cols:
        bdf["max_drawdown_Median"] = bdf[dd_step_cols].median(
            axis=1, skipna=True
        )
        bdf["max_drawdown_Min"] = bdf[dd_step_cols].min(
            axis=1, skipna=True
        )
    else:
        bdf["max_drawdown_Median"] = float("nan")
        bdf["max_drawdown_Min"] = float("nan")

    return bdf


# ---------------------------------------------------------------------------
# Derived metrics computation (Block C)
# ---------------------------------------------------------------------------

def _compute_derived_metrics(
    bdf: pd.DataFrame,
    *,
    step_cols: list[str],
    total_steps: int,
    step_bucket_vals: dict[int, dict[tuple[int, int], list[float]]],
    param_bucket_step_vals: dict[
        tuple[int, int],
        dict[tuple[int, float], dict[int, list[float]]]
    ],
    min_buckets_for_median: int,
) -> pd.DataFrame:
    """Compute all 17 derived columns (Block C) on *bdf* in-place and return it."""
    n_rows = len(bdf)

    # ── wins_count, win_steps, top3_count ────────────────────────────────
    wins_count: list[int] = [0] * n_rows
    win_steps_list: list[list[int]] = [[] for _ in range(n_rows)]
    top3_count: list[int] = [0] * n_rows

    for col in step_cols:
        if col not in bdf.columns:
            continue
        step_vals = pd.to_numeric(bdf[col], errors="coerce")
        non_nan = step_vals.dropna()
        if non_nan.empty:
            continue
        winner_idx = int(non_nan.idxmax())
        ranks = non_nan.rank(ascending=False, method="min")
        top3_idxs = set(ranks[ranks <= 3].index)
        # Extract step number from column name "Step{N}"
        step_num = int(col.replace("Step", ""))
        for row_i in range(n_rows):
            if pd.isna(step_vals.iloc[row_i]):
                continue
            if bdf.index[row_i] == winner_idx:
                wins_count[row_i] += 1
                win_steps_list[row_i].append(step_num)
            if bdf.index[row_i] in top3_idxs:
                top3_count[row_i] += 1

    bdf["wins_count"] = wins_count
    bdf["win_steps"] = [
        ",".join(f"Step{s}" for s in ws) for ws in win_steps_list
    ]
    bdf["top3_count"] = top3_count

    # ── bucket_presence_steps ────────────────────────────────────────────
    presence_steps_list: list[str] = []
    for row_i in range(n_rows):
        present = []
        for col in step_cols:
            if col in bdf.columns and not pd.isna(bdf.at[bdf.index[row_i], col]):
                step_num = int(col.replace("Step", ""))
                present.append(f"Step{step_num}")
        presence_steps_list.append(",".join(present))
    bdf["bucket_presence_steps"] = presence_steps_list

    # ── mean_oos_pnl ────────────────────────────────────────────────────
    if step_cols:
        existing_step_cols = [c for c in step_cols if c in bdf.columns]
        if existing_step_cols:
            step_data = bdf[existing_step_cols].apply(
                pd.to_numeric, errors="coerce"
            )
            bdf["mean_oos_pnl"] = step_data.mean(axis=1, skipna=True)
        else:
            bdf["mean_oos_pnl"] = float("nan")
    else:
        bdf["mean_oos_pnl"] = float("nan")

    # ── median_oos_pnl ───────────────────────────────────────────────────
    if step_cols:
        existing_step_cols = [c for c in step_cols if c in bdf.columns]
        if existing_step_cols:
            step_data_for_median = bdf[existing_step_cols].apply(
                pd.to_numeric, errors="coerce"
            )
            bdf["median_oos_pnl"] = step_data_for_median.median(axis=1, skipna=True)
        else:
            bdf["median_oos_pnl"] = float("nan")
    else:
        bdf["median_oos_pnl"] = float("nan")

    # ── std_bucket (population std, ddof=0, on raw ok+valid values) ──────
    std_bucket_vals: list[float] = []
    for row_i in range(n_rows):
        ab = int(bdf.at[bdf.index[row_i], "atr_bucket"])
        mt = int(bdf.at[bdf.index[row_i], "mult_bucket_ticks"])
        bk_key = (ab, mt)
        all_vals: list[float] = []
        for s in step_bucket_vals:
            all_vals.extend(step_bucket_vals[s].get(bk_key, []))
        arr = np.array(all_vals, dtype=float)
        arr = arr[~np.isnan(arr)]
        std_bucket_vals.append(
            float(np.std(arr, ddof=0)) if len(arr) > 0 else float("nan")
        )
    bdf["std_bucket"] = std_bucket_vals

    # ── pct_params_positive_pnl ──────────────────────────────────────────
    pct_positive_vals: list[float] = []
    for row_i in range(n_rows):
        ab = int(bdf.at[bdf.index[row_i], "atr_bucket"])
        mt = int(bdf.at[bdf.index[row_i], "mult_bucket_ticks"])
        bk_key = (ab, mt)
        param_map = param_bucket_step_vals.get(bk_key, {})
        valid_count = 0
        positive_count = 0
        for _param_key, step_pnl_map in param_map.items():
            observed: list[float] = []
            for step_pnls in step_pnl_map.values():
                observed.extend(step_pnls)
            obs_arr = np.array(observed, dtype=float)
            obs_arr = obs_arr[~np.isnan(obs_arr)]
            if len(obs_arr) == 0:
                continue
            valid_count += 1
            if float(np.mean(obs_arr)) > 0.0:
                positive_count += 1
        if valid_count > 0:
            pct_positive_vals.append(positive_count / valid_count)
        else:
            pct_positive_vals.append(float("nan"))
    bdf["pct_params_positive_pnl"] = pct_positive_vals

    # ── above_median_count, eligible_median_steps_count ──────────────────
    above_median_count: list[int] = [0] * n_rows
    eligible_median_steps: list[int] = [0] * n_rows

    for col in step_cols:
        if col not in bdf.columns:
            continue
        col_vals = pd.to_numeric(bdf[col], errors="coerce")
        non_nan = col_vals.dropna()
        if non_nan.empty:
            continue
        if len(non_nan) < min_buckets_for_median:
            continue
        step_median = float(non_nan.median())
        for row_i in range(n_rows):
            v = col_vals.iloc[row_i]
            if not pd.isna(v):
                eligible_median_steps[row_i] += 1
                if float(v) >= step_median:
                    above_median_count[row_i] += 1

    bdf["above_median_count"] = above_median_count
    bdf["above_median_ratio"] = [
        c / total_steps if total_steps > 0 else 0.0
        for c in above_median_count
    ]

    # ── presence_count ───────────────────────────────────────────────────
    presence_count_per_row: list[int] = []
    for row_i in range(n_rows):
        count = 0
        for col in step_cols:
            if col in bdf.columns and not pd.isna(
                bdf.at[bdf.index[row_i], col]
            ):
                count += 1
        presence_count_per_row.append(count)
    bdf["presence_count"] = presence_count_per_row

    # ── above_median_ratio_present (diagnostic, zero-div → 0.0) ─────────
    bdf["above_median_ratio_present"] = [
        float(c / p) if p > 0 else 0.0
        for c, p in zip(above_median_count, presence_count_per_row)
    ]

    # ── eligible_median_steps_count ──────────────────────────────────────
    bdf["eligible_median_steps_count"] = eligible_median_steps

    # ── above_median_ratio_eligible (diagnostic, zero-div → 0.0) ────────
    bdf["above_median_ratio_eligible"] = [
        float(c / e) if e > 0 else 0.0
        for c, e in zip(above_median_count, eligible_median_steps)
    ]

    # ── bucket_stability_score ───────────────────────────────────────────
    stability_scores: list[float] = []
    for row_i in range(n_rows):
        if total_steps > 0:
            presence_ratio = presence_count_per_row[row_i] / total_steps
            above_med_ratio = above_median_count[row_i] / total_steps
            score = 0.6 * presence_ratio + 0.4 * above_med_ratio
        else:
            score = 0.0
        stability_scores.append(round(score, 6))
    bdf["bucket_stability_score"] = stability_scores

    # ── zone_dominance_score ─────────────────────────────────────────────
    zone_dominance_scores: list[float] = []
    for row_i in range(n_rows):
        if total_steps > 0:
            presence = presence_count_per_row[row_i] / total_steps
            top3_r = top3_count[row_i] / total_steps
            wins_r = wins_count[row_i] / total_steps
            d_score = 0.4 * presence + 0.3 * top3_r + 0.3 * wins_r
        else:
            d_score = 0.0
        zone_dominance_scores.append(round(d_score, 6))
    bdf["zone_dominance_score"] = zone_dominance_scores

    # ── bucket_balanced_score ────────────────────────────────────────────
    # Components: presence (already in [0,1]), min-max normalised median PnL
    # (higher is better), inverted min-max normalised max_drawdown_Median
    # (lower/more-negative is worse → inverted so higher = better).
    # All three are in [0,1]; equal weights 1/3 each.
    # Any NaN component → NaN score (no weight redistribution).
    w = 1.0 / 3.0

    # presence component — already normalised
    c_presence = pd.Series(
        [
            (presence_count_per_row[i] / total_steps) if total_steps > 0 else 0.0
            for i in range(n_rows)
        ],
        index=bdf.index,
        dtype=float,
    )

    # pnl component — min-max normalise median_oos_pnl across non-NaN rows.
    # NaN rows stay NaN regardless of zero-range: a missing median means the
    # component is unknown, not zero.
    pnl_series = pd.to_numeric(bdf["median_oos_pnl"], errors="coerce")
    pnl_valid = pnl_series.dropna()
    if len(pnl_valid) == 0:
        c_pnl = pd.Series(float("nan"), index=bdf.index, dtype=float)
    elif pnl_valid.max() == pnl_valid.min():
        # Zero range: assign 0.0 only where the source is not NaN
        c_pnl = pnl_series.where(pnl_series.isna(), 0.0)
    else:
        pnl_min = pnl_valid.min()
        pnl_max = pnl_valid.max()
        c_pnl = (pnl_series - pnl_min) / (pnl_max - pnl_min)
        c_pnl = c_pnl.clip(0.0, 1.0)

    # dd component — min-max normalise max_drawdown_Median.
    # max_drawdown_Median is negative: closer to 0 is better (lower absolute DD).
    # dd_min = most negative (worst), dd_max = least negative (best).
    # Standard min-max: (val - min) / (max - min) gives 1.0 for best, 0.0 for worst.
    # NaN rows stay NaN regardless of zero-range.
    dd_series = pd.to_numeric(bdf["max_drawdown_Median"], errors="coerce")
    dd_valid = dd_series.dropna()
    if len(dd_valid) == 0:
        c_dd = pd.Series(float("nan"), index=bdf.index, dtype=float)
    elif dd_valid.max() == dd_valid.min():
        # Zero range: assign 0.0 only where the source is not NaN
        c_dd = dd_series.where(dd_series.isna(), 0.0)
    else:
        dd_min = dd_valid.min()
        dd_max = dd_valid.max()
        c_dd = (dd_series - dd_min) / (dd_max - dd_min)
        c_dd = c_dd.clip(0.0, 1.0)

    balanced = w * c_presence + w * c_pnl + w * c_dd
    bdf["bucket_balanced_score"] = balanced.round(6)

    return bdf
