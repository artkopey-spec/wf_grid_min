"""
Trades collector — assembles OOS and train trade rows into long-format DataFrames.

Design contract (per reviewer note and §W.9.5):

1.  Identity columns added to every row: ``grid_point_id``, ``wf_step``,
    ``step_status``.  ``grid_point_id + wf_step`` is the authoritative join key.

2.  Inclusion policy: trades are collected for ALL steps regardless of
    ``step_status`` (raw diagnostics contract).  The ``step_status`` column
    is present on every row so downstream (export, aggregation) can filter
    as needed.  Silent exclusion by the collector would introduce hidden bias.

3.  Prepend leakage (Invariant E): OOS trades from ``StepResult.oos_trades_df``
    are already filtered and rebased by ``step_executor`` — entry_index >= 0,
    rebased to OOS-window origin.  Collector verifies this invariant.

4.  Train trades are not rebased (they start at bar 0 of the train slice).

5.  Empty / None trades_df → no rows added (not an error).

Column order:
    identity:  grid_point_id, wf_step, step_status
    window:    test_start_idx, test_end_idx  (or train_start_idx, train_end_idx)
    donor:     trade_id, direction, entry_time, entry_index, entry_price,
               exit_time, exit_index, exit_price, bars_held,
               gross_pnl_pct, commission_pct, net_pnl_pct
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from wf_grid.status.status_model import StepStatus, assign_step_status
from wf_grid.wf.step_executor import StepResult


# Donor columns produced by extract_trades (from core/trades.py)
_DONOR_TRADE_COLS = [
    "trade_id",
    "direction",
    "entry_time",
    "entry_index",
    "entry_price",
    "exit_time",
    "exit_index",
    "exit_price",
    "bars_held",
    "gross_pnl_pct",
    "commission_pct",
    "net_pnl_pct",
]

# WP9: optional filter trade columns attached by attach_trade_filter_diagnostics.
# These are preserved when present — included ONLY when diagnostics are attached.
_FILTER_TRADE_COLS = [
    "entry_filter_state",
    "entry_trigger_source",
    "exit_reason",
]

# Full OOS trades long schema
_OOS_TRADE_COLUMNS = (
    ["grid_point_id", "wf_step", "step_status", "test_start_idx", "test_end_idx"]
    + _DONOR_TRADE_COLS
)

# Full train trades long schema
_TRAIN_TRADE_COLUMNS = (
    ["grid_point_id", "wf_step", "step_status", "train_start_idx", "train_end_idx"]
    + _DONOR_TRADE_COLS
)


class TradesCollectionError(ValueError):
    """Raised when a trades invariant is violated."""


def collect_oos_trades(
    grid_results: Dict[str, List[StepResult]],
    config,
) -> pd.DataFrame:
    """
    Assemble WF_Trades long DataFrame from OOS step results.

    Trades from all steps are included (raw diagnostics contract).
    ``step_status`` column allows downstream filtering.

    Verifies Invariant E (§W.9.5): all ``entry_index >= 0`` in collected rows.

    Parameters
    ----------
    grid_results:
        Mapping grid_point_id → list of OOS StepResult.
    config:
        Validated GridConfig (for assign_step_status).

    Returns
    -------
    pd.DataFrame
        OOS trades long with schema ``_OOS_TRADE_COLUMNS``.
        May be empty if no trades exist.
    """
    chunks: List[pd.DataFrame] = []

    for gp_id, step_results in grid_results.items():
        for sr in step_results:
            status = _resolve_status(sr, config)

            trades_df = sr.oos_trades_df
            if trades_df is None or len(trades_df) == 0:
                continue

            chunk = _enrich_chunk(
                trades_df=trades_df,
                gp_id=gp_id,
                wf_step=sr.wf_step,
                status=status,
                start_idx=sr.test_start_idx,
                end_idx=sr.test_end_idx,
                window_col_start="test_start_idx",
                window_col_end="test_end_idx",
            )
            chunks.append(chunk)

    df = _concat_or_empty(chunks, _OOS_TRADE_COLUMNS)

    # Invariant E: no prepend-zone entries (entry_index >= 0)
    if len(df) > 0 and (df["entry_index"] < 0).any():
        bad = df[df["entry_index"] < 0][["grid_point_id", "wf_step", "entry_index"]]
        raise TradesCollectionError(
            f"Invariant E violated: OOS trades with entry_index < 0 detected "
            f"(prepend leakage). First bad rows:\n{bad.head(5).to_string()}"
        )

    return df


def collect_train_trades(
    grid_results: Dict[str, List[StepResult]],
    config,
) -> pd.DataFrame:
    """
    Assemble WF_Train_Trades long DataFrame from train step results.

    Same inclusion policy as OOS: all steps, ``step_status`` present.
    Train trades are not rebased (indices are 0-based within train slice).

    Parameters
    ----------
    grid_results:
        Mapping grid_point_id → list of train StepResult.
    config:
        Validated GridConfig.

    Returns
    -------
    pd.DataFrame
        Train trades long with schema ``_TRAIN_TRADE_COLUMNS``.
    """
    chunks: List[pd.DataFrame] = []

    for gp_id, step_results in grid_results.items():
        for sr in step_results:
            status = _resolve_status(sr, config)

            trades_df = sr.oos_trades_df  # train uses same field (oos_trades_df)
            if trades_df is None or len(trades_df) == 0:
                continue

            chunk = _enrich_chunk(
                trades_df=trades_df,
                gp_id=gp_id,
                wf_step=sr.wf_step,
                status=status,
                start_idx=sr.test_start_idx,
                end_idx=sr.test_end_idx,
                window_col_start="train_start_idx",
                window_col_end="train_end_idx",
            )
            chunks.append(chunk)

    return _concat_or_empty(chunks, _TRAIN_TRADE_COLUMNS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_status(sr: StepResult, config) -> str:
    """Determine step_status string for this StepResult."""
    if sr.error_message is not None:
        return StepStatus.RUNTIME_ERROR.value
    return assign_step_status(
        metrics=sr.metrics,
        effective_oos_bars=sr.effective_oos_bars,
        config=config,
    ).value


def _enrich_chunk(
    trades_df: pd.DataFrame,
    gp_id: str,
    wf_step: int,
    status: str,
    start_idx: int,
    end_idx: int,
    window_col_start: str,
    window_col_end: str,
) -> pd.DataFrame:
    """Add identity + window columns to a trades chunk."""
    chunk = trades_df.copy()

    # Ensure all donor columns present (fill missing with None)
    for col in _DONOR_TRADE_COLS:
        if col not in chunk.columns:
            chunk[col] = None

    chunk.insert(0, "grid_point_id", gp_id)
    chunk.insert(1, "wf_step", wf_step)
    chunk.insert(2, "step_status", status)
    chunk.insert(3, window_col_start, start_idx)
    chunk.insert(4, window_col_end, end_idx)

    # Keep known columns in canonical order; append any filter columns that are
    # actually present (§10.6.1 — filter columns only when diagnostics attached).
    identity_window = [
        "grid_point_id", "wf_step", "step_status", window_col_start, window_col_end
    ]
    filter_cols_present = [c for c in _FILTER_TRADE_COLS if c in chunk.columns]
    full_cols = identity_window + _DONOR_TRADE_COLS + filter_cols_present
    return chunk[full_cols]


def _concat_or_empty(
    chunks: List[pd.DataFrame],
    columns: List[str],
) -> pd.DataFrame:
    """Concatenate chunks, or return empty DataFrame with correct schema.

    Known ``columns`` come first in the specified order; any extra columns
    (e.g. filter trade columns added by attach_trade_filter_diagnostics) are
    appended after the known columns (§10.6.2 — no silent drop).
    """
    if not chunks:
        return pd.DataFrame(columns=columns)
    df = pd.concat(chunks, ignore_index=True)
    # Known columns first (guaranteed order), then any extras
    known_present = [c for c in columns if c in df.columns]
    extras = [c for c in df.columns if c not in set(columns)]
    return df[known_present + extras]
