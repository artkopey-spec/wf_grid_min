"""
Gate evaluation for WF grid search.

Pipeline order:
  1. apply_step_gates(step_oos_long, config)  — mutates step_status on ok rows.
     Runs BEFORE aggregation (A7).
  2. aggregate_candidates(...)                — §5.2 ok mask (excludes gate_failed).
  3. apply_candidate_gates(agg_df, config)    — bool columns + composite on the
     aggregated DataFrame produced by A7.

§6.1  Step-level gates
      Applied to rows with step_status == "ok".
      On failure: step_status → "gate_failed".

      | Gate          | Metric        | Operator | Default threshold            |
      |---------------|---------------|----------|------------------------------|
      | min_trades    | num_trades    | >=       | config.backtest.min_trades.. |
      | max_drawdown  | max_drawdown  | >=       | -0.50                        |

§6.2  Candidate-level gates
      Applied to the aggregated DataFrame (one row per grid_point_id).
      Each gate produces a bool column.

      | Gate                    | Formula                               | Default  |
      |-------------------------|---------------------------------------|----------|
      | gate_ok_positive_median | sum_pnl_pct_Median > threshold        | 0.0      |
      | gate_ok_min_trades      | num_trades_Median >= threshold         | 3.0      |
      | gate_ok_worst_segment   | sum_pnl_pct_Min >= threshold           | None=off |
      | gate_ok_drawdown        | max_drawdown_Min >= threshold          | -0.50    |
      | gate_ok_coverage        | ok_ratio >= min_ok_ratio              | 0.7      |

§6.3  seed_gate_passed = AND of all enabled gates.
      seed_gate_fail_reason = comma-separated list of failed gate names.

Special case: n_ok_steps == 0 → all candidate gates = False,
    seed_gate_passed = False, seed_gate_fail_reason = "no_ok_steps".
"""

from __future__ import annotations

import logging
from typing import List

import numpy as np
import pandas as pd

from wf_grid.config.schema import GridConfig
from wf_grid.status.status_model import StepStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step-level gates (§6.1) — applied BEFORE aggregation
# ---------------------------------------------------------------------------

def apply_step_gates(
    step_oos_long: pd.DataFrame,
    config: GridConfig,
) -> pd.DataFrame:
    """
    Evaluate step-level gates on ok rows; set step_status = gate_failed
    for rows that violate any step gate.

    Returns a **copy** — the original DataFrame is not mutated.
    """
    df = step_oos_long.copy()

    ok_mask = df["step_status"] == StepStatus.OK.value

    # Defensive guard only: by design, metric contracts run before gates and
    # should already mark positive max_drawdown on ok-rows as invalid.
    # If this warning appears, a caller likely bypassed/changed pipeline order.
    if "max_drawdown" in df.columns:
        positive_dd = ok_mask & (df["max_drawdown"] > 0)
        if positive_dd.any():
            bad = df.loc[positive_dd, ["grid_point_id", "wf_step", "max_drawdown"]]
            logger.warning(
                "apply_step_gates: max_drawdown > 0 detected in %d ok row(s) — "
                "this should have been filtered by metric contracts before gates; "
                "check pipeline order/contract invocation. "
                "Affected rows:\n%s",
                len(bad),
                bad.to_string(index=False),
            )

    min_trades_threshold = config.effective_step_min_trades()
    dd_threshold = config.gates.step.max_drawdown_threshold

    # min_trades gate: num_trades < threshold OR NaN → fail
    # NaN is treated as unknown quality → must not pass as valid.
    min_trades_fail = ok_mask & (
        df["num_trades"].isna() | (df["num_trades"] < min_trades_threshold)
    )

    # max_drawdown gate: max_drawdown < threshold OR NaN → fail
    # Convention (§0.3): max_drawdown is negative or zero.
    # Gate semantics: max_drawdown >= threshold (e.g. -0.10 >= -0.50 → pass).
    # Failure: max_drawdown < threshold (e.g. -0.60 < -0.50) or NaN.
    dd_fail = ok_mask & (
        df["max_drawdown"].isna() | (df["max_drawdown"] < dd_threshold)
    )

    fail_mask = min_trades_fail | dd_fail
    df.loc[fail_mask, "step_status"] = StepStatus.GATE_FAILED.value

    return df


# ---------------------------------------------------------------------------
# Candidate-level gates (§6.2 / §6.3) — applied AFTER aggregation
# ---------------------------------------------------------------------------

_CANDIDATE_GATE_COLUMNS: List[str] = [
    "gate_ok_positive_median",
    "gate_ok_min_trades",
    "worst_segment_gate_enabled",
    "gate_ok_worst_segment",
    "gate_ok_drawdown",
    "gate_ok_min_total_trades",
    "gate_ok_coverage",
    "seed_gate_passed",
    "seed_gate_fail_reason",
]


def apply_candidate_gates(
    agg_df: pd.DataFrame,
    config: GridConfig,
) -> pd.DataFrame:
    """
    Evaluate candidate-level gates on the aggregated DataFrame from A7.

    All gate checks operate on aggregated metric columns (e.g. sum_pnl_pct_Median,
    num_trades_Median, etc.) — never on raw step_oos_long.

    Returns a **copy** with gate bool columns, seed_gate_passed, and
    seed_gate_fail_reason appended.
    """
    df = agg_df.copy()

    cand_cfg = config.gates.candidate
    no_ok = df["n_ok_steps"] == 0

    # §6.2 individual gates -------------------------------------------------

    # gate_ok_positive_median: sum_pnl_pct_Median > threshold
    df["gate_ok_positive_median"] = (
        df["sum_pnl_pct_Median"] > cand_cfg.positive_median_threshold
    ) & ~no_ok

    # gate_ok_min_trades: num_trades_Median >= threshold
    df["gate_ok_min_trades"] = (
        df["num_trades_Median"] >= cand_cfg.min_trades_median
    ) & ~no_ok

    # gate_ok_worst_segment: sum_pnl_pct_Min >= threshold (disabled when None).
    # Plan §4.5 / G1:
    #   - worst_segment_gate_enabled = False when threshold is None.
    #   - gate_ok_worst_segment = pd.NA when disabled (not True, not False).
    #   - Disabled gate excluded from composite AND and from fail_reason.
    worst_thresh = cand_cfg.worst_segment_pnl_threshold
    if worst_thresh is not None:
        df["worst_segment_gate_enabled"] = True
        df["gate_ok_worst_segment"] = (
            df["sum_pnl_pct_Min"] >= worst_thresh
        ) & ~no_ok
    else:
        df["worst_segment_gate_enabled"] = False
        # pd.NA: disabled — neither True nor False; must not enter composite AND.
        df["gate_ok_worst_segment"] = pd.NA

    # gate_ok_drawdown: max_drawdown_Min >= threshold
    df["gate_ok_drawdown"] = (
        df["max_drawdown_Min"] >= cand_cfg.max_drawdown_threshold
    ) & ~no_ok

    # gate_ok_min_total_trades: total_oos_trades >= threshold (FIX-4.1)
    min_total = cand_cfg.min_total_trades
    if "total_oos_trades" not in df.columns:
        raise ValueError(
            "apply_candidate_gates requires 'total_oos_trades' column in the aggregated "
            "DataFrame. Ensure aggregate_candidates() runs before apply_candidate_gates()."
        )
    df["gate_ok_min_total_trades"] = (
        df["total_oos_trades"] >= min_total
    ) & ~no_ok

    # gate_ok_coverage: ok_ratio >= min_ok_ratio (FIX-2)
    min_ok_ratio = cand_cfg.min_ok_ratio
    if "ok_ratio" not in df.columns:
        raise ValueError(
            "apply_candidate_gates requires 'ok_ratio' column in the aggregated "
            "DataFrame. Ensure aggregate_candidates() runs before apply_candidate_gates()."
        )
    df["gate_ok_coverage"] = (
        df["ok_ratio"] >= min_ok_ratio
    ) & ~no_ok

    # §6.3 composite --------------------------------------------------------
    # worst_segment is conditionally included: when enabled (gate_ok_worst_segment
    # is a bool Series), include it.  When disabled (gate_ok_worst_segment = pd.NA),
    # exclude from AND so seed_gate_passed remains a clean bool dtype.
    worst_enabled = worst_thresh is not None
    base_composite = (
        df["gate_ok_positive_median"]
        & df["gate_ok_min_trades"]
        & df["gate_ok_drawdown"]
        & df["gate_ok_min_total_trades"]
        & df["gate_ok_coverage"]
    )
    if worst_enabled:
        composite = base_composite & df["gate_ok_worst_segment"]
    else:
        composite = base_composite
    # Force bool dtype to guard against accidental object/NA propagation.
    df["seed_gate_passed"] = composite.astype(bool)

    # seed_gate_fail_reason
    df["seed_gate_fail_reason"] = df.apply(_build_fail_reason, axis=1)

    # Override for no_ok_steps: explicit fail reason
    df.loc[no_ok, "seed_gate_passed"] = False
    df.loc[no_ok, "seed_gate_fail_reason"] = "no_ok_steps"

    return df


def _build_fail_reason(row: pd.Series) -> str:
    """Build comma-separated list of failed gate names for a single row.

    Disabled gates (worst_segment_gate_enabled == False) are excluded from the
    fail reason regardless of the gate_ok_worst_segment value (pd.NA when disabled).
    """
    if row.get("n_ok_steps", 0) == 0:
        return "no_ok_steps"

    failed: List[str] = []

    # Gates that are always evaluated:
    always_on = [
        "gate_ok_positive_median",
        "gate_ok_min_trades",
        "gate_ok_drawdown",
        "gate_ok_min_total_trades",
        "gate_ok_coverage",
    ]
    for g in always_on:
        if not row.get(g, True):
            failed.append(g)

    # worst_segment gate: only include if enabled (not disabled via pd.NA storage).
    if bool(row.get("worst_segment_gate_enabled", False)):
        ws_val = row.get("gate_ok_worst_segment", True)
        # pd.NA is falsy in bool context via `not`, but use explicit check.
        try:
            if not bool(ws_val):
                failed.append("gate_ok_worst_segment")
        except (TypeError, ValueError):
            pass  # pd.NA → skip (disabled gate, should not reach here)

    return ",".join(failed)
