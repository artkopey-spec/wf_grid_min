"""
excel_format_helpers.py — helpers for formatting export-facing DataFrames.

Applied ONLY to final DataFrames immediately before to_excel() calls.
Never modifies internal pipeline DataFrames.

Column classification:
  INTEGER_LIKE_COLS  → nullable Int64 (no float tails)
  MULTIPLIER_COLS    → round(2)
  METRIC_COLS        → round(4)

Rules:
  - integer conversion is safe (uses pd.array with Int64 dtype, NaN-safe)
  - multiplier / metric rounding uses pandas .round(), preserving NaN
  - string / bool / datetime columns are never touched
  - unknown columns are left as-is
"""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Column name sets
# ---------------------------------------------------------------------------

INTEGER_LIKE_COLS: frozenset[str] = frozenset({
    # ranks
    "rank", "prod_rank", "canonical_rank", "single_rank", "pair_rank",
    "triple_rank", "grid_index",
    # unified ranking
    "unified_rank", "final_rank",
    "agreement_count", "max_possible",
    "component_id", "unified_component_size",
    # ATR / step params
    "atr_period", "ATR Period", "best_atr_period",
    "step", "wf_step", "n_steps",
    # trade counts
    "num_trades", "Num Trades", "oos_num_trades", "oos_num_trades_median",
    "Сделок",
    # coverage / bucket counts
    "coverage_count", "coverage_count_min", "coverage_count_100",
    "coverage_count_90",
    "bucket_vote_count", "region_size", "source_sheet_count",
    # misc integer fields
    "bars_held", "Bars Held", "trade_id", "Trade ID",
    "entry_index", "Entry Index", "exit_index", "Exit Index",
    "n_trials", "early_exit_count",
    "stress_pass_count", "stress_fail_count",
    "Bars", "Prepend Bars",
    "Warmup (requested)", "Warmup (effective)",
    # wf_compare
    "Rank", "ATR", "Trades", "Gate Fails",
    # diversification
    "worst_rank", "rank_9y", "rank_10y", "canonical_rank_9y", "canonical_rank_10y",
    "overlap_available_count", "phase_available_count", "same_cluster_penalty",
    "cluster_diversity",
    # bucket ids
    "atr_bucket", "mult_bucket_ticks",
    "atr_bucket_9y", "atr_bucket_10y",
    "mult_bucket_ticks_9y", "mult_bucket_ticks_10y",
    # opt_compare
    "rank_100", "rank_90",
    # walk-forward step sheets
    "count",
})

MULTIPLIER_COLS: frozenset[str] = frozenset({
    "multiplier", "Multiplier", "ST Mult", "best_multiplier",
    "mult_bucket",
    # wf_compare
    "Mult",
})

METRIC_COLS: frozenset[str] = frozenset({
    # PnL / returns
    "sum_pnl_pct", "Sum PnL %", "oos_sum_pnl_pct", "train_sum_pnl_pct",
    "gross_pnl_pct", "Gross PnL %", "net_pnl_pct", "Net PnL %",
    "avg_trade", "Avg Trade", "median_pnl",
    # drawdown
    "max_drawdown", "Max Drawdown", "worst_oos_max_dd_pct", "worst_max_dd_pct",
    "oos_max_dd_pct", "Test Max DD %",
    # ratios / rates
    "win_rate", "Win Rate", "coverage_ratio", "coverage_ratio_effective",
    "fallback_share", "skipped_step_ratio", "positive_ratio", "positive_pnl_ratio",
    "plateau_share",
    # Sharpe / Sortino / CAGR / PF
    "sharpe", "Sharpe", "oos_sharpe",
    "sortino", "Sortino", "oos_sortino", "oos_sortino_min",
    "cagr", "CAGR", "oos_cagr",
    "profit_factor", "Profit Factor", "oos_profit_factor",
    # scores / robust
    "robust_score", "Robust Score", "robust_score_std",
    "plateau_score", "Plateau Score",
    "final_score", "regime_score", "bucket_stability_score",
    "final_prod_score", "final_selection_score",
    "quality_score", "pair_quality", "pair_stability",
    "triple_quality", "triple_stability",
    "avg_final_score", "avg_robust_score", "avg_regime_score", "avg_bucket_stability",
    # norm components
    "pnl_norm", "dd_norm",
    "canonical_composite_norm", "plateau_conservative_norm",
    "bucket_support_avg_norm", "bucket_universality",
    "canonical_composite_raw", "rank_consistency", "robust_score_min",
    "plateau_conservative_raw",
    "production_score", "combined_score", "policy_score",
    "center_proximity_score", "horizon_stability_bonus",
    "neighbor_support_raw", "neighbor_support_norm",
    "exact_score", "bucket_support", "confidence",
    # opt_compare
    "conservative_score", "average_score",
    "plateau_composite", "plateau_quality",
    "score_100", "score_90",
    "worst_step_penalty_100", "worst_step_penalty_90",
    "plateau_ranking_score_100", "plateau_ranking_score_90",
    "plateau_quality_100", "plateau_quality_90",
    "median_pnl_100", "median_pnl_90",
    "positive_ratio_100", "positive_ratio_90",
    "std_pnl_100", "std_pnl_90",
    "worst_max_dd_100", "worst_max_dd_90", "worst_max_dd_conservative",
    "conservative_return", "median_pnl_conservative",
    "positive_ratio_conservative", "std_pnl_conservative",
    "rank_consistency", "canonical_composite",
    "robust_score_100", "robust_score_90",
    "oos_sum_pnl_pct_100", "oos_sum_pnl_pct_90",
    # diversification
    "corr_9y", "corr_10y", "overlap_9y", "overlap_10y",
    "phase_9y", "phase_10y", "worst_abs_corr", "avg_corr",
    "worst_overlap", "avg_overlap", "worst_phase", "avg_phase",
    # wf_compare
    "OOS PnL%", "OOS DD%", "Sortino", "Sharpe", "PF",
    # robustness
    "base_value", "worst_stressed", "stress_drop_pct",
    # stress_Xx columns matched by prefix in format_excel_export_df
    # equity
    "Equity",
    # gates
    "Value", "Threshold",
    # imputation fractions
    # summary agg
    "Mean", "Std", "Min", "Max", "Median",
    # ensemble
    "portfolio_score",
    # misc
    "best_value", "best_ranked_value", "best_ranked_objective_value",
})


def _get_openpyxl_number_format(col: str) -> str:
    """Return the appropriate openpyxl number_format string for a column name."""
    if col in INTEGER_LIKE_COLS:
        return "0"
    if col in MULTIPLIER_COLS:
        return "0.00"
    if col in METRIC_COLS:
        return "0.0000"
    if isinstance(col, str) and col.startswith("stress_") and col.endswith("x"):
        return "0.0000"
    # fallback for any other numeric column
    return "0.0000"


def apply_openpyxl_column_formats(
    ws: "Any",
    df: "pd.DataFrame",
    data_start_row: int = 2,
) -> None:
    """Apply per-column number formats to an openpyxl worksheet.

    Must be called AFTER writing data with to_excel() / openpyxl.
    Only touches columns that have a numeric dtype in *df*.

    Parameters
    ----------
    ws:
        openpyxl worksheet object (``writer.sheets[sheet_name]``).
    df:
        The formatted DataFrame that was written to the sheet.
        Column order must match the sheet column order.
    data_start_row:
        First data row (1-based).  Default 2 assumes a single header row.
    """
    n_rows = len(df)
    for col_idx, col in enumerate(df.columns, start=1):
        series = df[col]
        if not _is_numeric_col(series):
            continue
        fmt = _get_openpyxl_number_format(col)
        for row_idx in range(data_start_row, data_start_row + n_rows):
            ws.cell(row=row_idx, column=col_idx).number_format = fmt


def _is_numeric_col(series: pd.Series) -> bool:
    """Return True if series has a numeric dtype (int or float, including nullable)."""
    return pd.api.types.is_numeric_dtype(series)


def _safe_to_int64(series: pd.Series) -> pd.Series:
    """
    Convert a numeric series to nullable Int64.

    Preserves NaN as pd.NA.  Rounds floats to nearest integer before
    conversion so that 40.000000 → 40 without raising on non-integer floats
    (e.g. 40.0000001 due to floating-point noise).
    """
    try:
        rounded = series.round(0)
        return rounded.astype("Int64")
    except (TypeError, ValueError):
        return series


def format_excel_export_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Format a final export-facing DataFrame before writing to Excel.

    Applies:
      - INTEGER_LIKE_COLS  → nullable Int64 (no float tails)
      - MULTIPLIER_COLS    → round(2)
      - METRIC_COLS        → round(4)
      - columns starting with "stress_" and ending with "x" → round(4)

    Only touches columns that:
      1. Exist in the DataFrame
      2. Have a numeric dtype

    Returns a copy — original DataFrame is not modified.
    """
    df = df.copy()

    for col in df.columns:
        if col not in df.columns:
            continue
        series = df[col]
        if not _is_numeric_col(series):
            continue

        if col in INTEGER_LIKE_COLS:
            df[col] = _safe_to_int64(series)
        elif col in MULTIPLIER_COLS:
            df[col] = series.round(2)
        elif col in METRIC_COLS:
            df[col] = series.round(4)
        elif (
            isinstance(col, str)
            and col.startswith("stress_")
            and col.endswith("x")
        ):
            df[col] = series.round(4)

    return df
