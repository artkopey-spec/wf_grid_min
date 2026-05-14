"""
Summary wide-table builder for WF grid search (xlsx spec §5–§10).

build_summary_wide:
  Combines step_oos_long, aggregated (A7), ranked (A10) into one wide
  DataFrame — one row per grid_point_id.

  Column blocks (xlsx spec §10):
    Block A — identity + rank (§10.1)
    Block B — key decision summaries (§10.2)
    Block C — remaining: segment columns S1_*..SN_*, other aggregates,
               diagnostics (§10.3)

  Segment columns (xlsx spec §5):
    For each wf_step/segment label S1..SN, wide columns {L}_{metric}.
    Segment label: S{wf_step} where wf_step is 1-indexed.

  Aggregate columns (xlsx spec §6):
    Already present in aggregated DataFrame from A7.

  Decision columns (xlsx spec §8.2):
    Present in ranked DataFrame from A10 + candidate gates A8.

  ok_ratio already in aggregated; propagated into Block A (§10.1 note).

invariants:
  - One row per grid_point_id (xlsx spec §12.3).
  - grid_rank is first column (xlsx spec §12.4).
  - All rows preserved (xlsx spec §12.5).
  - Segment columns in order S1 .. SN (xlsx spec §12.6).
  - Block A then Block B then Block C (xlsx spec §12.9).
"""

from __future__ import annotations

import logging
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

from wf_grid.config.schema import INVALID_METRIC_VALUE, GridConfig


# ---------------------------------------------------------------------------
# Block column definitions (xlsx spec §10)
# ---------------------------------------------------------------------------

# Block A — identity + rank (§10.1)
# Priority order for manual analysis: id/rank → params → scoring → reliability
# → gate diagnostics.
_BLOCK_A: List[str] = [
    "grid_rank",
    "grid_point_id",
    "atr_period",
    "multiplier",
    "trade_mode",
    "tier",
    "aggregation_scope",
    "seed_gate_passed",
    "tester_seed_score",
    "ranking_mode",
    "score_contract_status",
    "score_discrimination_status",
    "score_interpretation_note",
    "quantile_gates_status",
    "seed_gate_fail_reason",
    "reliability_flag",
    "ok_ratio",
    "survivorship_warning",
    "n_ok_steps",
    "n_total_steps",
    "n_segments",
    "profitable_segments_count",
    "n_passed_for_scoring",
    "total_oos_trades",
    "has_defensive_fallback_steps",
    "bucket_matrix_status",
    "bucket_matrix_error",
    "grid_search_caveat",
]

# Block B — key decision summaries (§10.2)
# Phase A: no streak columns
_BLOCK_B: List[str] = [
    "sum_pnl_pct_Median",
    "sum_pnl_pct_Sum",
    "sum_pnl_pct_Min",
    "sum_pnl_pct_Std",
    "max_drawdown_Min",
    "max_drawdown_Median",
    "profit_factor_Median",
    "num_trades_Median",
    "sharpe_Median",
    "sortino_Median",
    "cagr_Median",
    "win_rate_Median",
    "avg_trade_Median",
    "gate_ok_positive_median",
    "gate_ok_min_trades",
    "gate_ok_worst_segment",
    "gate_ok_drawdown",
    "gate_ok_min_total_trades",
    "gate_ok_coverage",
]

# Block D — tail aggregates appended after segment blocks (§10.3)
_BLOCK_TAIL: List[str] = [
    "sum_pnl_pct_Mean",
    "sum_pnl_pct_Max",
    "sharpe_Mean",
    "sharpe_Std",
    "sharpe_Min",
    "sharpe_Max",
    "sortino_Mean",
    "sortino_Std",
    "sortino_Min",
    "sortino_Max",
    "max_drawdown_Mean",
    "max_drawdown_Std",
    "max_drawdown_Max",
    "cagr_Mean",
    "cagr_Std",
    "cagr_Min",
    "cagr_Max",
    "win_rate_Mean",
    "win_rate_Std",
    "win_rate_Min",
    "win_rate_Max",
    "num_trades_Mean",
    "num_trades_Std",
    "num_trades_Min",
    "num_trades_Max",
    "profit_factor_Mean",
    "profit_factor_Std",
    "profit_factor_Min",
    "profit_factor_Max",
    "avg_trade_Mean",
    "avg_trade_Std",
    "avg_trade_Min",
    "avg_trade_Max",
]

# Per-segment metrics (subset included in wide segment blocks)
_SEGMENT_METRICS: List[str] = [
    "sum_pnl_pct",
    "sharpe",
    "sortino",
    "max_drawdown",
    "cagr",
    "win_rate",
    "num_trades",
    "profit_factor",
    "avg_trade",
    "step_status",
    "prepend_bars_applied",
    "effective_oos_bars",
    "used_defensive_fallback",
    "n_volume_blocked_start_attempts",
    "n_volume_warmup_blocked_start_attempts",
    "n_volume_below_baseline_blocked_start_attempts",
    "n_volume_above_baseline_blocked_start_attempts",
    "n_volume_baseline_zero_blocked_start_attempts",
    "n_volume_direction_warmup_blocked_start_attempts",
    "n_volume_unknown_direction_blocked_start_attempts",
    "n_volume_trade_mode_disallowed_direction_blocked_start_attempts",
    "n_volume_cycle_direction_mismatch_blocked_bars",
    "avg_median_relative_volume",
    "n_volume_started_cycles",
    "n_volume_suppressed_cycles",
]

# Desired metric order within each Sx_* segment block
_SEGMENT_METRIC_ORDER: List[str] = [
    "step_status",
    "sum_pnl_pct",
    "max_drawdown",
    "num_trades",
    "profit_factor",
    "sharpe",
    "sortino",
    "cagr",
    "win_rate",
    "avg_trade",
    "effective_oos_bars",
    "prepend_bars_applied",
    "used_defensive_fallback",
    "n_volume_blocked_start_attempts",
    "n_volume_warmup_blocked_start_attempts",
    "n_volume_below_baseline_blocked_start_attempts",
    "n_volume_above_baseline_blocked_start_attempts",
    "n_volume_baseline_zero_blocked_start_attempts",
    "n_volume_direction_warmup_blocked_start_attempts",
    "n_volume_unknown_direction_blocked_start_attempts",
    "n_volume_trade_mode_disallowed_direction_blocked_start_attempts",
    "n_volume_cycle_direction_mismatch_blocked_bars",
    "avg_median_relative_volume",
    "n_volume_started_cycles",
    "n_volume_suppressed_cycles",
    "segment_label",
    "wf_step",
]

_VOLUME_SEGMENT_METRICS: frozenset[str] = frozenset({
    "n_volume_blocked_start_attempts",
    "n_volume_warmup_blocked_start_attempts",
    "n_volume_below_baseline_blocked_start_attempts",
    "n_volume_above_baseline_blocked_start_attempts",
    "n_volume_baseline_zero_blocked_start_attempts",
    "n_volume_direction_warmup_blocked_start_attempts",
    "n_volume_unknown_direction_blocked_start_attempts",
    "n_volume_trade_mode_disallowed_direction_blocked_start_attempts",
    "n_volume_cycle_direction_mismatch_blocked_bars",
    "avg_median_relative_volume",
    "n_volume_started_cycles",
    "n_volume_suppressed_cycles",
})


def build_summary_wide(
    step_oos_long: pd.DataFrame,
    aggregated: pd.DataFrame,
    ranked: pd.DataFrame,
    config: GridConfig,
    *,
    min_ok_ratio_for_reliability: Optional[float] = None,
    min_total_trades_for_reliability: Optional[int] = None,
    bucket_matrix_status: str = "disabled",
    bucket_matrix_error: Optional[str] = None,
) -> pd.DataFrame:
    """
    Build summary wide-table from long-format inputs.

    Parameters
    ----------
    step_oos_long:
        OOS step results (one row per grid_point_id × wf_step).
        Required columns: grid_point_id, wf_step + metric columns.
    aggregated:
        Aggregated candidate-level metrics from A7.
        One row per grid_point_id.
    ranked:
        Tiered + ranked DataFrame from A10.
        Includes grid_rank, tier, gate columns, tester_seed_score.
    config:
        Validated GridConfig.
    min_ok_ratio_for_reliability:
        Override the ok_ratio threshold used in reliability_flag HIGH tier.
        Defaults to config.gates.candidate.min_ok_ratio so the flag stays
        in sync with the coverage gate threshold.
    min_total_trades_for_reliability:
        Override the total_oos_trades threshold for reliability_flag HIGH tier.
        Defaults to config.gates.candidate.min_total_trades.

    Returns
    -------
    pd.DataFrame
        Wide table: one row per grid_point_id, Block A → B → C columns.
        grid_rank is first column.
    """
    if step_oos_long.empty or aggregated.empty or ranked.empty:
        return pd.DataFrame()

    # Resolve reliability thresholds from config if not overridden
    _min_ok_ratio = (
        min_ok_ratio_for_reliability
        if min_ok_ratio_for_reliability is not None
        else config.gates.candidate.min_ok_ratio
    )
    _min_total_trades = (
        min_total_trades_for_reliability
        if min_total_trades_for_reliability is not None
        else config.gates.candidate.min_total_trades
    )
    _min_passed_for_discrimination = config.scoring.min_passed_for_discrimination

    # --- Build segment (S1..SN) wide columns from step_oos_long ---
    seg_wide = _build_segment_columns(step_oos_long)

    # --- Merge aggregated + ranked ---
    # ranked already contains aggregated columns (it was built on top of it)
    # but to be safe, join cleanly
    base = ranked.copy()

    # Add aggregated columns not already in ranked
    agg_extra = [
        c for c in aggregated.columns
        if c != "grid_point_id" and c not in base.columns
    ]
    if agg_extra:
        base = base.merge(
            aggregated[["grid_point_id"] + agg_extra],
            on="grid_point_id",
            how="left",
        )

    # --- Derive identity columns from grid_point_id ---
    base = _add_identity_columns(base)

    # --- Add n_segments (plan §4.9 / S1) ---
    # Use nunique() — unique step count — not max().  For sparse WF steps like
    # [1, 3, 4], max() would return 4 (wrong) while nunique() returns 3 (correct).
    n_segments = (
        step_oos_long["wf_step"].nunique()
        if "wf_step" in step_oos_long.columns
        else 0
    )
    base["n_segments"] = int(n_segments)

    # --- A1: aggregation scope marker (plan §4.6) ---
    # Explicitly documents that all aggregate statistics (Median, Min, Std …)
    # are computed on ok-status steps ONLY.  Non-ok rows (gate_failed,
    # insufficient_bars, runtime_error, invalid, etc.) are excluded from
    # every aggregate column in the summary.
    base["aggregation_scope"] = "ok_steps_only"

    # --- Add ranking_mode + quantile_gates_status stubs ---
    if "ranking_mode" not in base.columns:
        base["ranking_mode"] = config.ranking.mode
    if "quantile_gates_status" not in base.columns:
        base["quantile_gates_status"] = None

    # --- FIX-5.3: survivorship_warning ---
    base["survivorship_warning"] = base["ok_ratio"].apply(
        _compute_survivorship_warning
    ) if "ok_ratio" in base.columns else ""

    # --- FIX-5.4: score_interpretation_note ---
    disc_col = base["score_discrimination_status"] if "score_discrimination_status" in base.columns else None
    cont_col = base["score_contract_status"] if "score_contract_status" in base.columns else None
    if disc_col is not None and cont_col is not None:
        base["score_interpretation_note"] = [
            _compute_score_interpretation_note(d, c, _min_passed_for_discrimination)
            for d, c in zip(disc_col, cont_col)
        ]
    else:
        base["score_interpretation_note"] = ""

    # --- FIX-3.1: n_passed_for_scoring ---
    if "seed_gate_passed" in base.columns:
        n_passed = int(base["seed_gate_passed"].fillna(False).astype(bool).sum())
    else:
        n_passed = 0
    base["n_passed_for_scoring"] = n_passed

    # --- B1: bucket matrix status visibility (plan §4.9 / B1) ---
    # Propagate build outcome so consumers can see whether the bucket matrix
    # is available ("ok"), failed ("failed"), or was never built ("disabled").
    base["bucket_matrix_status"] = bucket_matrix_status
    base["bucket_matrix_error"] = bucket_matrix_error if bucket_matrix_error else pd.NA

    # --- grid_search_caveat: multiple comparisons reminder (same value for every row) ---
    n_total_candidates = len(base)
    base["grid_search_caveat"] = (
        f"N={n_total_candidates} combinations tested. "
        "Rank 1 is NOT an independent OOS proof — it is the best result among all tested. "
        "Require forward test + multiple testing correction before any trading decision."
    )

    # --- FIX-3.1: reliability_flag (thresholds synced with gate config) ---
    base["reliability_flag"] = base.apply(
        lambda row: _compute_reliability_flag(row, _min_ok_ratio, _min_total_trades),
        axis=1,
    )

    # --- Merge segment columns ---
    wide = base.merge(seg_wide, on="grid_point_id", how="left")

    # --- Apply Block A → B → C column ordering ---
    wide = _order_columns(wide)

    return wide.reset_index(drop=True)


# ---------------------------------------------------------------------------
# FIX-3.1 / 6.1: reliability flag
# ---------------------------------------------------------------------------

def _compute_reliability_flag(
    row: pd.Series,
    min_ok_ratio: float = 0.7,
    min_total_trades: int = 30,
) -> str:
    """Assign reliability tier per candidate based on coverage and trade count.

    NOTE on scope: reliability_flag evaluates THIS candidate's own quality
    (its n_ok_steps, ok_ratio, total_oos_trades).  It is NOT the same as
    score_discrimination_status, which evaluates the entire scoring cohort
    (how many candidates passed seed gate).  Both fields are present in
    Block A but measure different things — do not conflate them.

    Thresholds are synced with gate config so they stay consistent:
      min_ok_ratio  → config.gates.candidate.min_ok_ratio  (default 0.7)
      min_total_trades → config.gates.candidate.min_total_trades (default 30)

    HIGH:   ok_ratio >= min_ok_ratio  AND  n_ok_steps >= 5
            AND  total_oos_trades >= min_total_trades
    MEDIUM: ok_ratio >= min_ok_ratio * 0.7 (min 0.3)
            AND  total_oos_trades >= min_total_trades // 2 (min 10)
    LOW:    everything else
    """
    ok_ratio = row.get("ok_ratio", 0.0) or 0.0
    n_ok = row.get("n_ok_steps", 0) or 0
    total_trades = row.get("total_oos_trades", 0) or 0

    medium_ratio_thresh = max(0.3, min_ok_ratio * 0.7)
    medium_trades_thresh = max(10, min_total_trades // 2)

    if ok_ratio >= min_ok_ratio and n_ok >= 5 and total_trades >= min_total_trades:
        return "HIGH"
    if ok_ratio >= medium_ratio_thresh and total_trades >= medium_trades_thresh:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Segment column builder (xlsx spec §5)
# ---------------------------------------------------------------------------

def _build_segment_columns(step_oos_long: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot step_oos_long to wide segment columns S1_metric, S2_metric, ...

    Segment label: S{wf_step}  (wf_step assumed 1-indexed).
    Sentinel INVALID_METRIC_VALUE preserved as-is (downstream xlsx-layer
    handles display; aggregation already cleaned them).
    """
    rows: dict[str, dict] = {}

    for _, row in step_oos_long.iterrows():
        gp_id = row["grid_point_id"]
        step = int(row["wf_step"])
        label = f"S{step}"

        if gp_id not in rows:
            rows[gp_id] = {"grid_point_id": gp_id}

        for metric in _segment_metrics_for(step_oos_long):
            if metric in row.index:
                rows[gp_id][f"{label}_{metric}"] = row[metric]

        # Always include segment label and index
        rows[gp_id][f"{label}_segment_label"] = label
        rows[gp_id][f"{label}_wf_step"] = step

    if not rows:
        return pd.DataFrame(columns=["grid_point_id"])

    return pd.DataFrame(list(rows.values()))


def _segment_metrics_for(step_oos_long: pd.DataFrame) -> List[str]:
    if any(c in step_oos_long.columns for c in _VOLUME_SEGMENT_METRICS):
        return list(_SEGMENT_METRICS)
    return [m for m in _SEGMENT_METRICS if m not in _VOLUME_SEGMENT_METRICS]


# ---------------------------------------------------------------------------
# Identity column derivation
# ---------------------------------------------------------------------------

def _add_identity_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure atr_period, multiplier, trade_mode columns exist.

    Primary path: columns were propagated from GridPoint at collection time
    (FIX-3.3).  Fallback: parse from grid_point_id string (deprecated).
    """
    _IDENTITY_COLS = ("atr_period", "multiplier", "trade_mode")
    if all(c in df.columns for c in _IDENTITY_COLS):
        return df

    logger.warning(
        "Identity columns missing — falling back to grid_point_id string parsing. "
        "Pass grid_points to collect_oos_steps to avoid this.",
    )
    parsed = df["grid_point_id"].apply(_parse_grid_point_id)
    for col in _IDENTITY_COLS:
        if col not in df.columns:
            df = df.copy()
            df[col] = parsed.apply(lambda x: x.get(col))

    return df


def _parse_grid_point_id(gp_id: str) -> dict:
    """Parse 'atr{atr}_m{mult}_{mode}' → {atr_period, multiplier, trade_mode}.

    Deprecated fallback — prefer structured identity columns from GridPoint.
    Returns empty dict if format not recognized.
    """
    import re
    m = re.match(r"^atr(\d+)_m([\d.]+)_(.+)$", gp_id)
    if m:
        return {
            "atr_period": int(m.group(1)),
            "multiplier": float(m.group(2)),
            "trade_mode": m.group(3),
        }
    return {}


# ---------------------------------------------------------------------------
# Column ordering: Block A → B → C (segments) → D (tail) (xlsx spec §10)
# ---------------------------------------------------------------------------

def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Reorder columns: Block-A → Block-B → segment blocks Sx → Block-D tail → rest."""
    present_a = [c for c in _BLOCK_A if c in df.columns]
    present_b = [c for c in _BLOCK_B if c in df.columns]
    present_tail = [c for c in _BLOCK_TAIL if c in df.columns]

    fixed = set(present_a) | set(present_b) | set(present_tail)

    # Block C: segment columns in S1..SN order with deterministic per-segment metric order
    seg_cols = _sorted_segment_columns(
        [c for c in df.columns if c not in fixed and _is_segment_col(c)]
    )
    # Any remaining columns not placed above
    other_c = [
        c for c in df.columns
        if c not in fixed and not _is_segment_col(c)
    ]

    ordered = present_a + present_b + seg_cols + present_tail + other_c
    return df[ordered]


def _is_segment_col(col: str) -> bool:
    """True if column starts with S{digits}_."""
    import re
    return bool(re.match(r"^S\d+_", col))


def _sorted_segment_columns(cols: List[str]) -> List[str]:
    """Sort S{n}_metric columns by segment number n, then by _SEGMENT_METRIC_ORDER."""
    import re

    metric_rank = {m: i for i, m in enumerate(_SEGMENT_METRIC_ORDER)}

    def _key(c: str):
        m = re.match(r"^S(\d+)_(.*)", c)
        if m:
            seg_num = int(m.group(1))
            metric = m.group(2)
            return (seg_num, metric_rank.get(metric, len(_SEGMENT_METRIC_ORDER)), metric)
        return (9999, len(_SEGMENT_METRIC_ORDER), c)

    return sorted(cols, key=_key)


# ---------------------------------------------------------------------------
# FIX-5.3: Survivorship bias warning helper
# ---------------------------------------------------------------------------

def _compute_score_interpretation_note(
    score_discrimination_status: str,
    score_contract_status: str,
    min_passed_for_discrimination: int,
) -> str:
    """Return a human-readable interpretation note for a scored candidate.

    Derived exclusively from existing pipeline fields — no new computations.

    SCOPE: this note describes the COHORT-level discrimination quality
    (score_discrimination_status), not the individual candidate's reliability.
    For per-candidate quality see reliability_flag and ok_ratio.

    ``min_passed_for_discrimination`` must match ``config.scoring.min_passed_for_discrimination``
    (passed from ``build_summary_wide``) so the insufficient-cohort message stays
    aligned with the discrimination threshold used in scoring.

    Rules (evaluated in priority order):
        no_score contract  → "Not scored"
        insufficient       → "fewer than {N} passed candidates: ranking is unreliable"
                             (N = min_passed_for_discrimination)
        low_spread         → "Low spread: scores barely differ"
        ok / anything else → ""  (no noise when scoring is meaningful)

    The note is intentionally terse: it sits next to score_discrimination_status
    in Block A and provides a plain-English translation for non-technical readers.
    """
    disc = str(score_discrimination_status) if score_discrimination_status is not None else ""
    cont = str(score_contract_status) if score_contract_status is not None else ""

    if cont == "no_score" or disc == "no_score":
        return "Not scored"
    if disc == "insufficient":
        return (
            f"fewer than {min_passed_for_discrimination} passed candidates: "
            "ranking is unreliable"
        )
    if disc == "low_spread":
        return "Low spread: scores barely differ"
    return ""


def _compute_survivorship_warning(ok_ratio: float) -> str:
    """Return a human-readable survivorship bias warning for a given ok_ratio.

    Thresholds:
        ok_ratio >= 0.9  → ""               (no warning; single drop acceptable)
        ok_ratio >= 0.7  → "MODERATE: X% segments excluded"
        ok_ratio >= 0.5  → "HIGH: X% segments excluded"
        ok_ratio  < 0.5  → "CRITICAL: X% segments excluded"

    The excluded percentage is rounded to the nearest whole number so that
    a human reading the summary can immediately assess the severity without
    doing arithmetic.
    """
    try:
        ratio = float(ok_ratio)
    except (TypeError, ValueError):
        return ""

    excluded_pct = round((1.0 - ratio) * 100)

    if ratio >= 0.9:
        return ""
    if ratio >= 0.7:
        return f"MODERATE: {excluded_pct}% segments excluded"
    if ratio >= 0.5:
        return f"HIGH: {excluded_pct}% segments excluded"
    return f"CRITICAL: {excluded_pct}% segments excluded"
