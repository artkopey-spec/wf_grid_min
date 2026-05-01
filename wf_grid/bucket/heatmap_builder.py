"""
Heatmap builder for BucketMatrix_Median.

Builds ATR × Mult pivot tables from the bucket matrix DataFrame.

Public API
----------
build_stability_heatmap(bucket_matrix_df, atr_bucket_step, mult_bucket_step) -> pd.DataFrame
build_risk_heatmap(bucket_matrix_df, atr_bucket_step, mult_bucket_step) -> pd.DataFrame

Both parameters are required (not default kwargs) — fix for donor bug where
hardcoded defaults were used instead of config-resolved values.
"""

from __future__ import annotations

import pandas as pd

from wf_grid.bucket.assignment import format_atr_range, format_mult_range

_REQUIRED_COLS = frozenset({"atr_bucket", "mult_bucket_ticks", "bucket_stability_score"})
_RISK_REQUIRED_COLS = frozenset({"atr_bucket", "mult_bucket_ticks", "max_drawdown_Median"})


def build_stability_heatmap(
    bucket_matrix_df: pd.DataFrame,
    atr_bucket_step: int,
    mult_bucket_step: float,
) -> pd.DataFrame:
    """Build ATR × Mult stability heatmap DataFrame.

    Rows = atr_bucket (sorted ASC), columns = mult_bucket_ticks (sorted ASC).
    Cell values = ``bucket_stability_score``.  Missing combinations → NaN
    (no fillna/dropna applied).

    Index and column labels are human-readable range strings (en-dash, .1f mult).
    Top-left index label = ``"ATR \\ MULT"``.

    Parameters
    ----------
    bucket_matrix_df:
        Output of ``build_median_bucket_matrix``.
    atr_bucket_step:
        ATR bucket width (required — not a default kwarg).
    mult_bucket_step:
        Multiplier bucket width (required — not a default kwarg).

    Returns
    -------
    pd.DataFrame
        Pivot table.  Empty DataFrame on empty or missing-columns input.
    """
    if bucket_matrix_df is None or bucket_matrix_df.empty:
        return pd.DataFrame()

    if not _REQUIRED_COLS.issubset(bucket_matrix_df.columns):
        return pd.DataFrame()

    pivot = bucket_matrix_df.pivot_table(
        index="atr_bucket",
        columns="mult_bucket_ticks",
        values="bucket_stability_score",
        aggfunc="first",
    )
    pivot = pivot.sort_index(axis=0).sort_index(axis=1)

    # Replace raw integer bucket IDs with human-readable range labels
    pivot.index = [
        format_atr_range(int(v), atr_bucket_step) for v in pivot.index
    ]
    pivot.columns = [
        format_mult_range(int(v), mult_bucket_step) for v in pivot.columns
    ]

    pivot.index.name = "ATR \\ MULT"
    pivot.columns.name = None

    return pivot


def build_risk_heatmap(
    bucket_matrix_df: pd.DataFrame,
    atr_bucket_step: int,
    mult_bucket_step: float,
) -> pd.DataFrame:
    """Build ATR × Mult risk heatmap DataFrame.

    Rows = atr_bucket (sorted ASC), columns = mult_bucket_ticks (sorted ASC).
    Cell values = ``max_drawdown_Median``.  Missing combinations → NaN
    (no fillna/dropna applied).

    Index and column labels are human-readable range strings (en-dash, .1f mult).
    Top-left index label = ``"ATR \\ MULT"``.

    Parameters
    ----------
    bucket_matrix_df:
        Output of ``build_median_bucket_matrix``.
    atr_bucket_step:
        ATR bucket width (required — not a default kwarg).
    mult_bucket_step:
        Multiplier bucket width (required — not a default kwarg).

    Returns
    -------
    pd.DataFrame
        Pivot table.  Empty DataFrame on empty or missing-columns input.
    """
    if bucket_matrix_df is None or bucket_matrix_df.empty:
        return pd.DataFrame()

    if not _RISK_REQUIRED_COLS.issubset(bucket_matrix_df.columns):
        return pd.DataFrame()

    pivot = bucket_matrix_df.pivot_table(
        index="atr_bucket",
        columns="mult_bucket_ticks",
        values="max_drawdown_Median",
        aggfunc="first",
    )
    pivot = pivot.sort_index(axis=0).sort_index(axis=1)

    pivot.index = [
        format_atr_range(int(v), atr_bucket_step) for v in pivot.index
    ]
    pivot.columns = [
        format_mult_range(int(v), mult_bucket_step) for v in pivot.columns
    ]

    pivot.index.name = "ATR \\ MULT"
    pivot.columns.name = None

    return pivot
