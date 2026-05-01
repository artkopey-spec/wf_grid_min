"""
Candidate ranking for WF grid search (§7.2).

rank_candidates:
  1. Assign tiers (§7.1) via tiering.assign_tiers.
  2. Sort per-tier with tiebreakers.
  3. Assign dense rank 1..G (no gaps) across all tiers.
  4. Return DataFrame with grid_rank as first column.

Sorting rules:
  gates_score mode:
    Tier 1: tester_seed_score DESC, sum_pnl_pct_Median DESC,
            sum_pnl_pct_Std ASC, grid_point_id ASC
    Tier 2: sum_pnl_pct_Median DESC, sum_pnl_pct_Min DESC,
            sum_pnl_pct_Std ASC, grid_point_id ASC
    Tier 3: grid_point_id ASC

  legacy mode:
    All rows sorted purely by metrics (tiers assigned but NOT used for sort order):
    sort_by DESC, tiebreaker DESC, sum_pnl_pct_Std ASC, grid_point_id ASC

NaN → last in all sort columns.
Dense rank: 1-based, no gaps, assigned after final sort.
grid_point_id as final tiebreaker guarantees absolute determinism.
"""

from __future__ import annotations

import pandas as pd

from wf_grid.config.schema import GridConfig
from wf_grid.ranking.tiering import assign_tiers
from wf_grid.status.status_model import RankingMode


def rank_candidates(
    summary_wide: pd.DataFrame,
    config: GridConfig,
) -> pd.DataFrame:
    """
    Assign tiers, sort, and compute dense grid_rank.

    Parameters
    ----------
    summary_wide:
        Aggregated + gated + scored DataFrame (one row per grid_point_id).
        Required columns include ``seed_gate_passed`` (from `apply_candidate_gates`
        or full pipeline output).  Legacy mode additionally requires
        ``ranking.sort_by`` and ``ranking.tiebreaker`` columns to exist.
    config:
        Validated GridConfig (provides ranking.mode, ranking.sort_by,
        ranking.tiebreaker).

    Returns
    -------
    pd.DataFrame
        Copy with tier and grid_rank columns; grid_rank is the first column.
    """
    if summary_wide.empty:
        df = summary_wide.copy()
        df.insert(0, "grid_rank", pd.Series(dtype=int))
        df["tier"] = pd.Series(dtype=int)
        return df

    df = summary_wide.copy()

    if "seed_gate_passed" not in df.columns:
        raise ValueError(
            "rank_candidates requires 'seed_gate_passed' column. Expected output of "
            "apply_candidate_gates() or full pipeline input."
        )

    # --- Validate sort columns (legacy mode only uses sort_by / tiebreaker) ---
    mode = _resolve_mode(config)
    if mode == RankingMode.LEGACY:
        _validate_sort_columns(df, config)

    # --- Tier assignment ---
    df["tier"] = assign_tiers(df, config)

    # --- Sort ---

    if mode == RankingMode.GATES_SCORE:
        df = _sort_gates_score(df)
    else:
        df = _sort_legacy(df, config)

    # --- Dense rank 1..G ---
    df = df.reset_index(drop=True)
    df.insert(0, "grid_rank", range(1, len(df) + 1))

    return df


# ---------------------------------------------------------------------------
# Sort helpers
# ---------------------------------------------------------------------------

def _sort_gates_score(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by tier then per-tier tiebreaker chain (gates_score mode)."""
    t1 = df[df["tier"] == 1].copy()
    t2 = df[df["tier"] == 2].copy()
    t3 = df[df["tier"] == 3].copy()

    t1 = _sort_with_na_last(
        t1,
        cols=["tester_seed_score", "sum_pnl_pct_Median", "sum_pnl_pct_Std", "grid_point_id"],
        asc=[False, False, True, True],
    )
    t2 = _sort_with_na_last(
        t2,
        cols=["sum_pnl_pct_Median", "sum_pnl_pct_Min", "sum_pnl_pct_Std", "grid_point_id"],
        asc=[False, False, True, True],
    )
    t3 = _sort_with_na_last(
        t3,
        cols=["grid_point_id"],
        asc=[True],
    )

    return pd.concat([t1, t2, t3], ignore_index=True)


def _sort_legacy(df: pd.DataFrame, config: GridConfig) -> pd.DataFrame:
    """Sort all rows by legacy config tiebreaker chain.

    seed_gate_passed is used as the primary sort key so that candidates that
    pass all gates always rank above those that fail, regardless of raw metric
    values.  Within each gate group, rows are sorted by the configured
    sort_by / tiebreaker chain as before.

    This ensures coverage-gate failures (FIX-2) and other gate failures
    actually affect rank in legacy mode, not just tier assignment.
    """
    sort_by = config.ranking.sort_by or "sum_pnl_pct_Median"
    tiebreaker = config.ranking.tiebreaker or "sum_pnl_pct_Min"

    # seed_gate_passed: True (1) sorts before False (0) with ascending=False
    cols = ["seed_gate_passed", sort_by, tiebreaker, "sum_pnl_pct_Std", "grid_point_id"]
    asc = [False, False, False, True, True]

    # Deduplicate in case sort_by == tiebreaker
    seen: list[str] = []
    seen_asc: list[bool] = []
    for c, a in zip(cols, asc):
        if c not in seen:
            seen.append(c)
            seen_asc.append(a)

    return _sort_with_na_last(df, cols=seen, asc=seen_asc)


def _sort_with_na_last(
    df: pd.DataFrame,
    cols: list[str],
    asc: list[bool],
) -> pd.DataFrame:
    """
    Sort df by cols / asc, putting NaN last regardless of ascending flag.

    For descending columns, pandas na_position='last' would put NaN first
    in some versions.  We use a sentinel approach: replace NaN in sort keys
    with worst-possible value.
    """
    if df.empty:
        return df

    work = df.copy()
    sort_keys: list[str] = []

    for col, ascending in zip(cols, asc):
        if col not in work.columns:
            # Column absent → treat as NaN for all rows; add constant sentinel
            proxy = f"__sort_{col}"
            work[proxy] = 0.0 if not ascending else float("inf")
            sort_keys.append(proxy)
            continue

        if work[col].dtype.kind in ("f", "i"):
            proxy = f"__sort_{col}"
            if ascending:
                sentinel = float("inf")
            else:
                sentinel = float("-inf")
            work[proxy] = work[col].fillna(sentinel)
            sort_keys.append(proxy)
        else:
            # String column (e.g. grid_point_id): NaN → empty string last
            proxy = f"__sort_{col}"
            work[proxy] = work[col].fillna("\xff\xff")
            sort_keys.append(proxy)

    sort_asc = list(asc)
    result = work.sort_values(sort_keys, ascending=sort_asc).drop(
        columns=[c for c in work.columns if c.startswith("__sort_")]
    )
    return result


def _resolve_mode(config: GridConfig) -> RankingMode:
    """Resolve ranking mode from config string."""
    try:
        return RankingMode(config.ranking.mode)
    except ValueError:
        return RankingMode.LEGACY


def _validate_sort_columns(df: pd.DataFrame, config: GridConfig) -> None:
    """Raise ValueError if sort_by or tiebreaker columns are absent from df.

    Only called in legacy mode where these columns are user-configurable.
    In gates_score mode the sort key set is fixed and validated by design.
    """
    available = sorted(df.columns.tolist())

    sort_by = config.ranking.sort_by or "sum_pnl_pct_Median"
    tiebreaker = config.ranking.tiebreaker or "sum_pnl_pct_Min"

    if sort_by not in df.columns:
        raise ValueError(
            f"ranking.sort_by column '{sort_by}' not found in DataFrame. "
            f"Available columns: {available}"
        )
    if tiebreaker not in df.columns:
        raise ValueError(
            f"ranking.tiebreaker column '{tiebreaker}' not found in DataFrame. "
            f"Available columns: {available}"
        )
