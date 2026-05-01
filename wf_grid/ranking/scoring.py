"""
Weighted normalized composite score for WF grid search (§7.3).

Formula:
    tester_seed_score = w1 * norm(sum_pnl_pct_Median)
                      + w2 * norm(profitable_segments_count)
                      + w3 * norm_inv(abs_max_drawdown_Median)   # optional
                      + w4 * norm_inv(abs_max_drawdown_Min)

Normalization modes
-------------------
"minmax" (legacy):
    norm(x) = (x - x_min) / (x_max - x_min); clamp [0, 1].
    norm_inv(x) = 1 - norm(x)  (lower-is-better).
    x_max == x_min → norm = 0.0, weight redistributed silently
      (does NOT trigger "partial" — data is present, just constant).

"rank":
    Each metric value is replaced by its rank among passed candidates,
    then mapped to [0, 1] via (rank - 1) / (N - 1).

    Direction convention (avoids sign errors):
      - For HIGHER_IS_BETTER metrics: rank(ascending=True) gives rank 1 to
        the smallest value → after mapping worst=0.0, best=1.0 once inverted:
        normalised = 1.0 - (rank - 1) / (N - 1).
      - For LOWER_IS_BETTER metrics: rank(ascending=True) gives rank 1 to the
        smallest (best) value → normalised = 1.0 - (rank - 1) / (N - 1) as well.
        Wait — that is identical.  To resolve: for LOWER_IS_BETTER we want the
        *smallest* value to yield 1.0 (best), so we use ascending=True directly:
        normalised = 1.0 - (rank - 1) / (N - 1).
        For HIGHER_IS_BETTER we want the *largest* value to yield 1.0, so we
        invert: normalised = (rank - 1) / (N - 1)  with ascending=True.

    Concrete rule (one formula, direction from flag):
        rank_0based = rank(ascending=True, method="average") - 1   # 0 … N-1
        if HIGHER_IS_BETTER:  normalised = rank_0based / (N - 1)
        if LOWER_IS_BETTER:   normalised = 1.0 - rank_0based / (N - 1)

    N = 1 contract (same as minmax min==max):
        All normalised values = 0.0, weight redistributed silently.
        This does NOT trigger "partial".

    Ties: method="average" → two identical values share average rank →
        same normalised score (neither is penalised).

    NaN rows: excluded from ranking; their normalised value = NaN;
        weight redistributed per-row (same "partial" logic as minmax).

Policy summary (shared for both modes):
  1. Normalization across *passed* rows only.
  2. NaN handling — redistribute per row:
     - After normalization: original-NaN components get weight redistributed
       proportionally to valid components of that row.
     - All NaN → score = NaN, status = "no_score".
     - Some NaN → status = "partial".
  3. Auto-normalize weights to sum == 1.0.
  4. Score only for seed_gate_passed == True.  Others → NaN.
  5. n_ok_steps == 0 → NaN + no_score immediately (no normalization attempt).
  6. score_contract_status: "ok" | "partial" | "no_score".
  7. zero-range column (minmax) or N=1 column (rank): norm = 0.0, weight
     redistributed silently — does NOT make score "partial".
"""

from __future__ import annotations

from typing import Dict, Literal, Optional, Set, Tuple

import numpy as np
import pandas as pd

from wf_grid.status.status_model import ScoreContractStatus

# ---------------------------------------------------------------------------
# Defaults (§7.3)
# ---------------------------------------------------------------------------

DEFAULT_SCORE_WEIGHTS: Dict[str, float] = {
    "sum_pnl_pct_Median": 0.45,
    "profitable_segments_count": 0.35,
    "abs_max_drawdown_Min": 0.20,
}

LOWER_IS_BETTER: Set[str] = {"abs_max_drawdown_Min", "abs_max_drawdown_Median"}

NormalizationMode = Literal["minmax", "rank"]
DEFAULT_NORMALIZATION_MODE: NormalizationMode = "minmax"


# ---------------------------------------------------------------------------
# Internal: normalization implementations
# ---------------------------------------------------------------------------

def _normalize_minmax(
    raw: Dict[str, pd.Series],
    passed_idx: pd.Index,
    lower_is_better: Set[str],
) -> Tuple[Dict[str, pd.Series], Set[str]]:
    """
    Min-max normalization across passed rows.

    Returns
    -------
    normalized : dict col → Series (index = passed_idx), values in [0, 1] or NaN
    zero_range_cols : set of columns where dispersion was zero (weight will be
        redistributed silently, NOT triggering "partial")
    """
    normalized: Dict[str, pd.Series] = {}
    zero_range_cols: Set[str] = set()

    for col, vals in raw.items():
        valid = vals.dropna()
        if len(valid) == 0:
            normalized[col] = pd.Series(np.nan, index=passed_idx)
            zero_range_cols.add(col)
            continue

        col_min = valid.min()
        col_max = valid.max()

        if col_max == col_min:
            # Constant column — no dispersion; 0.0 and silent redistribution.
            normalized[col] = pd.Series(0.0, index=passed_idx)
            zero_range_cols.add(col)
            continue

        # NaN values are kept as NaN — no median imputation.
        # The per-row weight redistribution loop below handles NaN components
        # correctly (same logic as rank mode): NaN → weight redistributed,
        # status = "partial".  Imputing the median here would distort the
        # normalised range for all rows and create artificial discrimination.
        normed = (vals - col_min) / (col_max - col_min)
        normed = normed.clip(0.0, 1.0)

        if col in lower_is_better:
            normed = 1.0 - normed

        normalized[col] = normed

    return normalized, zero_range_cols


def _normalize_rank(
    raw: Dict[str, pd.Series],
    passed_idx: pd.Index,
    lower_is_better: Set[str],
) -> Tuple[Dict[str, pd.Series], Set[str]]:
    """
    Rank-based normalization across passed rows.

    Convention (see module docstring):
        rank_0based = rank(ascending=True, method="average") - 1   # 0 … N-1
        HIGHER_IS_BETTER → normalised = rank_0based / (N - 1)
        LOWER_IS_BETTER  → normalised = 1.0 - rank_0based / (N - 1)

    NaN values are excluded from ranking (rank is computed on non-NaN subset).
    NaN rows receive NaN normalised value and trigger weight redistribution.

    N = 1 (or all values equal): normalised = 0.0, silent redistribution.

    Returns
    -------
    normalized : dict col → Series (index = passed_idx)
    zero_range_cols : columns where N_valid <= 1 (silent redistribution)
    """
    normalized: Dict[str, pd.Series] = {}
    zero_range_cols: Set[str] = set()

    for col, vals in raw.items():
        valid_mask = vals.notna()
        n_valid = int(valid_mask.sum())

        if n_valid == 0:
            normalized[col] = pd.Series(np.nan, index=passed_idx)
            zero_range_cols.add(col)
            continue

        if n_valid == 1:
            # N=1 contract: 0.0 and silent redistribution (mirrors minmax min==max).
            normed = pd.Series(np.nan, index=passed_idx)
            normed.loc[valid_mask[valid_mask].index] = 0.0
            normalized[col] = normed
            zero_range_cols.add(col)
            continue

        # Rank only over non-NaN subset (ascending=True: smallest → rank 1).
        valid_vals = vals[valid_mask]
        ranks = valid_vals.rank(method="average", ascending=True)
        rank_0based = ranks - 1.0  # [0, N_valid - 1]

        if col in lower_is_better:
            # Smallest value (rank 0) → 1.0 (best); largest → 0.0 (worst).
            normed_valid = 1.0 - rank_0based / (n_valid - 1)
        else:
            # Largest value (rank N_valid-1) → 1.0 (best); smallest → 0.0.
            normed_valid = rank_0based / (n_valid - 1)

        normed = pd.Series(np.nan, index=passed_idx)
        normed.loc[normed_valid.index] = normed_valid.values

        # Check for all-ties: entire range collapses to 0.0.
        if normed_valid.nunique(dropna=True) == 1:
            zero_range_cols.add(col)

        normalized[col] = normed

    return normalized, zero_range_cols


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_seed_score(
    df: pd.DataFrame,
    passed_mask: pd.Series,
    score_weights: Optional[Dict[str, float]] = None,
    lower_is_better: Optional[Set[str]] = None,
    normalization_mode: NormalizationMode = DEFAULT_NORMALIZATION_MODE,
) -> Tuple[pd.Series, pd.Series]:
    """
    Compute tester_seed_score and score_contract_status.

    Parameters
    ----------
    df:
        Aggregated + gated DataFrame (one row per grid_point_id).
        Must contain the metric columns referenced in score_weights.
    passed_mask:
        Boolean Series aligned with df; True for seed_gate_passed rows.
    score_weights:
        {metric_name: weight}.  None → DEFAULT_SCORE_WEIGHTS.
    lower_is_better:
        Set of metric names where lower values are better.
        None → LOWER_IS_BETTER.
    normalization_mode:
        "minmax" (default, legacy) or "rank" (rank-based, outlier-robust).

    Returns
    -------
    (tester_seed_score, score_contract_status)
        Both pd.Series aligned with df index.
        Non-passed rows: NaN / "no_score".
    """
    weights = dict(score_weights or DEFAULT_SCORE_WEIGHTS)
    lib = set(lower_is_better) if lower_is_better is not None else set(LOWER_IS_BETTER)

    n = len(df)
    scores = pd.Series(np.nan, index=df.index, dtype=float)
    statuses = pd.Series(
        ScoreContractStatus.NO_SCORE.value, index=df.index, dtype=object,
    )

    if n == 0 or not passed_mask.any():
        return scores, statuses

    # --- derive abs_* columns from raw max_drawdown_* if missing ---
    df_copied = False

    if "abs_max_drawdown_Min" in weights and "abs_max_drawdown_Min" not in df.columns:
        if "max_drawdown_Min" in df.columns:
            df = df.copy()
            df_copied = True
            df["abs_max_drawdown_Min"] = df["max_drawdown_Min"].abs()
        else:
            raise KeyError(
                "Score weight 'abs_max_drawdown_Min' is configured, "
                "but neither 'abs_max_drawdown_Min' nor "
                "'max_drawdown_Min' are present in the DataFrame. "
                "Check aggregator output."
            )

    if "abs_max_drawdown_Median" in weights and "abs_max_drawdown_Median" not in df.columns:
        if "max_drawdown_Median" in df.columns:
            if not df_copied:
                df = df.copy()
                df_copied = True
            df["abs_max_drawdown_Median"] = df["max_drawdown_Median"].abs()
        else:
            raise KeyError(
                "Score weight 'abs_max_drawdown_Median' is configured, "
                "but neither 'abs_max_drawdown_Median' nor "
                "'max_drawdown_Median' are present in the DataFrame. "
                "Check aggregator output."
            )

    # --- fail-fast: every configured weight column must be present ---
    missing = [col for col in weights if col not in df.columns]
    if missing:
        raise KeyError(
            f"Score weights reference columns missing from DataFrame: {missing}. "
            "Check aggregator output or scoring config. "
            "Every column listed in score_weights must be present."
        )

    # --- auto-normalize weights to sum == 1.0 ---
    available = dict(weights)
    w_sum = sum(available.values())
    if w_sum <= 0:
        return scores, statuses
    norm_weights = {col: w / w_sum for col, w in available.items()}

    # --- passed subset ---
    passed_idx = df.index[passed_mask]
    if len(passed_idx) == 0:
        return scores, statuses

    passed_df = df.loc[passed_idx]

    # --- collect raw values and track original NaN positions ---
    raw: Dict[str, pd.Series] = {}
    is_nan: Dict[str, pd.Series] = {}
    for col in norm_weights:
        vals = passed_df[col].astype(float)
        is_nan[col] = vals.isna()
        raw[col] = vals

    # --- normalization (delegated to mode-specific function) ---
    if normalization_mode == "rank":
        normalized, zero_range_cols = _normalize_rank(raw, passed_idx, lib)
    else:
        normalized, zero_range_cols = _normalize_minmax(raw, passed_idx, lib)

    # --- weighted sum with per-row NaN redistribution ---
    for idx in passed_idx:
        active_weight = 0.0
        component_sum = 0.0
        has_data_nan = False
        n_contributing = 0

        for col, w in norm_weights.items():
            original_was_nan = bool(is_nan[col].loc[idx])
            col_zero_range = col in zero_range_cols

            if original_was_nan:
                has_data_nan = True
            elif col_zero_range:
                # No dispersion / N=1 → 0.0, weight redistributed silently.
                pass
            else:
                active_weight += w
                component_sum += w * float(normalized[col].loc[idx])
                n_contributing += 1

        if n_contributing == 0:
            scores.loc[idx] = np.nan
            statuses.loc[idx] = ScoreContractStatus.NO_SCORE.value
        else:
            score = component_sum / active_weight
            scores.loc[idx] = score
            if has_data_nan:
                statuses.loc[idx] = ScoreContractStatus.PARTIAL.value
            else:
                statuses.loc[idx] = ScoreContractStatus.OK.value

    return scores, statuses


# ---------------------------------------------------------------------------
# FIX-3.2 / FIX-4.1: score discrimination status (raw feature spread)
# ---------------------------------------------------------------------------

# Module-level defaults retained for backward-compat when called without config.
_LOW_SPREAD_THRESHOLD: float = 1.0  # absolute range in sum_pnl_pct_Median
_MIN_PASSED_FOR_DISCRIMINATION: int = 5


def compute_score_discrimination(
    df: pd.DataFrame,
    passed_mask: pd.Series,
    spread_column: str = "sum_pnl_pct_Median",
    low_spread_threshold: float = _LOW_SPREAD_THRESHOLD,
    min_passed: int = _MIN_PASSED_FOR_DISCRIMINATION,
) -> pd.Series:
    """Assess whether scoring can meaningfully discriminate between candidates.

    Evaluated on **raw** feature values (not normalized scores).

    Parameters
    ----------
    min_passed:
        Minimum number of seed_gate_passed candidates required for
        discrimination to be considered possible.  Below this threshold,
        all passed candidates receive "insufficient".
        Configurable via scoring.min_passed_for_discrimination (default 5).
    low_spread_threshold:
        Minimum raw feature range (spread_column) required for "ok".
        Below this, passed candidates receive "low_spread".
        Configurable via scoring.low_spread_threshold (default 1.0).

    Returns pd.Series (one value per row, aligned with df.index):
      "insufficient" — fewer than min_passed passed candidates
      "low_spread"   — min_passed+ candidates but raw feature range < threshold
      "ok"           — meaningful discrimination possible
      "no_score"     — row not passed (not scored)
    """
    result = pd.Series("no_score", index=df.index, dtype=object)

    if df.empty or not passed_mask.any():
        return result

    n_passed = int(passed_mask.sum())
    passed_idx = df.index[passed_mask]

    if n_passed < min_passed:
        result.loc[passed_idx] = "insufficient"
        return result

    if spread_column in df.columns:
        passed_vals = df.loc[passed_idx, spread_column].dropna()
        if len(passed_vals) >= 2:
            spread = passed_vals.max() - passed_vals.min()
            if spread < low_spread_threshold:
                result.loc[passed_idx] = "low_spread"
                return result

    result.loc[passed_idx] = "ok"
    return result
