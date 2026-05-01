# =============================================================================
# ВЫРЕЗКА из репозитория 2.0 — только листы WF_Config, WF_Gates, WF_01..WF_N,
# WF_Trades, WF_Train_Trades + format_excel_export_df / _to_excel_safe
#
# Источники (оригинальные пути):
#   C:\2.0\supertrend_optimizer\io\excel_format_helpers.py  (часть)
#   C:\2.0\supertrend_optimizer\io\excel_walk_forward.py     (часть)
#
# Назначение: приложение к ТЗ / референс для grid-проекта без зависимости
# от полного excel_walk_forward.py. Типы WFStepResult / WalkForwardResult
# здесь не импортируются — используйте duck typing или подставьте свои.
# =============================================================================

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# --- excel_format_helpers.py (фрагмент: только то, что нужно format_excel_export_df)
# ---------------------------------------------------------------------------

INTEGER_LIKE_COLS: frozenset[str] = frozenset({
    "rank", "prod_rank", "canonical_rank", "single_rank", "pair_rank",
    "triple_rank", "grid_index",
    "unified_rank", "final_rank",
    "agreement_count", "max_possible",
    "component_id", "unified_component_size",
    "atr_period", "ATR Period", "best_atr_period",
    "step", "wf_step", "n_steps",
    "num_trades", "Num Trades", "oos_num_trades", "oos_num_trades_median",
    "Сделок",
    "coverage_count", "coverage_count_min", "coverage_count_100",
    "coverage_count_90",
    "bucket_vote_count", "region_size", "source_sheet_count",
    "bars_held", "Bars Held", "trade_id", "Trade ID",
    "entry_index", "Entry Index", "exit_index", "Exit Index",
    "n_trials", "early_exit_count",
    "stress_pass_count", "stress_fail_count",
    "Bars", "Prepend Bars",
    "Warmup (requested)", "Warmup (effective)",
    "Rank", "ATR", "Trades", "Gate Fails",
    "worst_rank", "rank_9y", "rank_10y", "canonical_rank_9y", "canonical_rank_10y",
    "overlap_available_count", "phase_available_count", "same_cluster_penalty",
    "cluster_diversity",
    "atr_bucket", "mult_bucket_ticks",
    "atr_bucket_9y", "atr_bucket_10y",
    "mult_bucket_ticks_9y", "mult_bucket_ticks_10y",
    "rank_100", "rank_90",
    "count",
})

MULTIPLIER_COLS: frozenset[str] = frozenset({
    "multiplier", "Multiplier", "ST Mult", "best_multiplier",
    "mult_bucket",
    "Mult",
})

METRIC_COLS: frozenset[str] = frozenset({
    "sum_pnl_pct", "Sum PnL %", "oos_sum_pnl_pct", "train_sum_pnl_pct",
    "gross_pnl_pct", "Gross PnL %", "net_pnl_pct", "Net PnL %",
    "avg_trade", "Avg Trade", "median_pnl",
    "max_drawdown", "Max Drawdown", "worst_oos_max_dd_pct", "worst_max_dd_pct",
    "oos_max_dd_pct", "Test Max DD %",
    "win_rate", "Win Rate", "coverage_ratio", "coverage_ratio_effective",
    "fallback_share", "skipped_step_ratio", "positive_ratio", "positive_pnl_ratio",
    "plateau_share",
    "sharpe", "Sharpe", "oos_sharpe",
    "sortino", "Sortino", "oos_sortino", "oos_sortino_min",
    "cagr", "CAGR", "oos_cagr",
    "profit_factor", "Profit Factor", "oos_profit_factor",
    "robust_score", "Robust Score", "robust_score_std",
    "plateau_score", "Plateau Score",
    "final_score", "regime_score", "bucket_stability_score",
    "final_prod_score", "final_selection_score",
    "quality_score", "pair_quality", "pair_stability",
    "triple_quality", "triple_stability",
    "avg_final_score", "avg_robust_score", "avg_regime_score", "avg_bucket_stability",
    "pnl_norm", "dd_norm",
    "canonical_composite_norm", "plateau_conservative_norm",
    "bucket_support_avg_norm", "bucket_universality",
    "canonical_composite_raw", "rank_consistency", "robust_score_min",
    "plateau_conservative_raw",
    "production_score", "combined_score", "policy_score",
    "center_proximity_score", "horizon_stability_bonus",
    "neighbor_support_raw", "neighbor_support_norm",
    "exact_score", "bucket_support", "confidence",
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
    "corr_9y", "corr_10y", "overlap_9y", "overlap_10y",
    "phase_9y", "phase_10y", "worst_abs_corr", "avg_corr",
    "worst_overlap", "avg_overlap", "worst_phase", "avg_phase",
    "OOS PnL%", "OOS DD%", "Sortino", "Sharpe", "PF",
    "base_value", "worst_stressed", "stress_drop_pct",
    "Equity",
    "Value", "Threshold",
    "Mean", "Std", "Min", "Max", "Median",
    "portfolio_score",
    "best_value", "best_ranked_value", "best_ranked_objective_value",
})


def _is_numeric_col(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series)


def _safe_to_int64(series: pd.Series) -> pd.Series:
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


# ---------------------------------------------------------------------------
# --- excel_walk_forward.py (фрагмент)
# ---------------------------------------------------------------------------

def _flatten_config_to_rows(cfg: dict | None, prefix: str = "") -> list[dict]:
    """Flatten nested config dict to flat rows (Section, Parameter, Value)."""
    rows = []
    for k, v in (cfg or {}).items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            rows.extend(_flatten_config_to_rows(v, key))
        else:
            if v is None:
                val = "null"
            elif isinstance(v, (list, tuple)):
                val = str(list(v))
            else:
                val = str(v)
            if "." in key:
                section, param = key.split(".", 1)
            else:
                section, param = "root", key
            rows.append({"Section": section, "Parameter": param, "Value": val})
    return rows


def _write_config_sheet(writer: pd.ExcelWriter, config: dict | None) -> None:
    """Write WF_Config sheet with full flattened config."""
    rows = _flatten_config_to_rows(config)
    df = pd.DataFrame(rows, columns=["Section", "Parameter", "Value"])
    if not df.empty:
        df = df.sort_values(["Section", "Parameter"], kind="mergesort")
    df.to_excel(writer, sheet_name="WF_Config", index=False)


def _to_excel_safe(val: Any) -> Any:
    """Convert value to Excel-compatible type (naive datetime, native int/float)."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    if isinstance(val, pd.Timestamp):
        return val.tz_localize(None) if val.tz is not None else val
    if isinstance(val, (np.integer, np.int64, np.int32)):
        return int(val)
    if isinstance(val, (np.floating, np.float64, np.float32)):
        return float(val) if not np.isnan(val) else None
    return val


def _write_step_sheet(step: Any, writer: pd.ExcelWriter, sheet_name: str) -> None:
    """
    Write TOP-K entries for a single WF step into a dedicated sheet (e.g. WF_01).

    Ожидается step с атрибутами:
      - train_objective_value
      - top_entries: итерируемый набор записей с полями:
        rank, atr_period, multiplier, train_objective, train_sum_pnl_pct,
        robust_score, oos_sortino, oos_sum_pnl_pct, oos_max_dd_pct, oos_num_trades, in_consensus
    """
    columns = [
        "rank",
        "atr_period",
        "multiplier",
        "entry_train_objective",
        "step_best_train_objective",
        "train_sum_pnl_pct",
        "robust_score",
        "oos_sortino",
        "oos_sum_pnl_pct",
        "worst_oos_max_dd_pct",
        "oos_num_trades",
        "in_consensus",
    ]

    top_entries = getattr(step, "top_entries", None) or []
    if len(top_entries) == 0:
        df = pd.DataFrame(columns=columns)
    else:
        step_best = _to_excel_safe(getattr(step, "train_objective_value", None))
        rows = []
        for entry in top_entries:
            rows.append({
                "rank": entry.rank,
                "atr_period": entry.atr_period,
                "multiplier": entry.multiplier,
                "entry_train_objective": _to_excel_safe(entry.train_objective),
                "step_best_train_objective": step_best,
                "train_sum_pnl_pct": entry.train_sum_pnl_pct,
                "robust_score": entry.robust_score,
                "oos_sortino": entry.oos_sortino,
                "oos_sum_pnl_pct": entry.oos_sum_pnl_pct,
                "worst_oos_max_dd_pct": entry.oos_max_dd_pct,
                "oos_num_trades": entry.oos_num_trades,
                "in_consensus": entry.in_consensus,
            })
        df = pd.DataFrame(rows, columns=columns)

    format_excel_export_df(df).to_excel(writer, sheet_name=sheet_name, index=False)


def _write_trades_sheet(result: Any, writer: pd.ExcelWriter) -> None:
    """Write WF_Trades sheet with all OOS/Test trades (per-step best params)."""
    oos = getattr(result, "oos_trades_df", None)
    if oos is not None and len(oos) > 0:
        df = oos.copy()
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                if hasattr(df[col].dtype, "tz") and df[col].dtype.tz is not None:
                    df[col] = df[col].dt.tz_localize(None)
        df.to_excel(writer, sheet_name="WF_Trades", index=False)
    else:
        pd.DataFrame().to_excel(writer, sheet_name="WF_Trades", index=False)


def _write_train_trades_sheet(result: Any, writer: pd.ExcelWriter) -> None:
    """Write WF_Train_Trades sheet with all TRAIN trades (per-step best params)."""
    tr = getattr(result, "train_trades_df", None)
    if tr is not None and len(tr) > 0:
        df = tr.copy()
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                if hasattr(df[col].dtype, "tz") and df[col].dtype.tz is not None:
                    df[col] = df[col].dt.tz_localize(None)
        df.to_excel(writer, sheet_name="WF_Train_Trades", index=False)
    else:
        pd.DataFrame().to_excel(writer, sheet_name="WF_Train_Trades", index=False)


def _write_gates_sheet(result: Any, writer: pd.ExcelWriter) -> None:
    """Write WF_Gates sheet with gate check details."""
    gates_result = getattr(result, "gates_result", None)
    if gates_result is None or not getattr(gates_result, "enabled", False):
        return

    rows = []
    for check in gates_result.checks:
        row = {
            "Name": check.name,
            "Passed": "PASS" if check.passed else "FAIL",
            "Value": check.value if check.value is not None else np.nan,
            "Threshold": check.threshold if check.threshold is not None else np.nan,
            "Message": check.message if check.message else "",
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    format_excel_export_df(df).to_excel(writer, sheet_name="WF_Gates", index=False)


__all__ = [
    "INTEGER_LIKE_COLS",
    "MULTIPLIER_COLS",
    "METRIC_COLS",
    "format_excel_export_df",
    "_flatten_config_to_rows",
    "_write_config_sheet",
    "_to_excel_safe",
    "_write_step_sheet",
    "_write_trades_sheet",
    "_write_train_trades_sheet",
    "_write_gates_sheet",
]
