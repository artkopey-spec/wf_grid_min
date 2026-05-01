"""
Metric contracts validator for WF Grid Search.

Purpose
-------
After ``collect_oos_steps(...)`` assembles ``step_oos_long``, the orchestrator
calls :func:`validate_metric_contracts` BEFORE ``apply_step_gates(...)``.

Contracts enforced (§4.3 of the fix plan)
-----------------------------------------
A step with ``step_status == "ok"`` must satisfy:

1. **Hard-required metrics** — ``sum_pnl_pct``, ``max_drawdown``, ``num_trades`` —
   must all be:
   - present (column exists in DataFrame; NaN counts as "missing value"),
   - finite (no ``NaN`` and no ``±inf``),
   - not equal to ``INVALID_METRIC_VALUE`` (donor sentinel).

2. ``max_drawdown`` must be ``<= 0`` (drawdown is negative-or-zero by convention).
   A strictly positive ``max_drawdown`` on an ok-step is a metric contract violation.

3. **Soft metrics** — ``win_rate``, ``profit_factor``, ``avg_trade``, ``sharpe``,
   ``sortino``, ``cagr`` — may legitimately be ``NaN`` (insufficient data, no
   losers, etc.).  Soft metrics fail ONLY when they equal
   ``INVALID_METRIC_VALUE``.  Soft ``NaN`` is tolerated and later dropped by the
   aggregator's ``dropna()`` semantics.

Any violation turns the step into:

    step_status = "invalid"
    error_type  = "metric_contract"
    error_message = "<first violation message>"

Non-ok steps (``gate_failed``, ``runtime_error``, ``insufficient_bars``, etc.)
are passed through unchanged.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd

from wf_grid.config.schema import INVALID_METRIC_VALUE
from wf_grid.status.status_model import StepStatus


# ---------------------------------------------------------------------------
# Metric sets (authoritative — cross-referenced by unit tests)
# ---------------------------------------------------------------------------

HARD_REQUIRED_METRICS: Tuple[str, ...] = (
    "sum_pnl_pct",
    "max_drawdown",
    "num_trades",
)

SOFT_METRICS: Tuple[str, ...] = (
    "win_rate",
    "profit_factor",
    "avg_trade",
    "sharpe",
    "sortino",
    "cagr",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_metric_contracts(step_oos_long: pd.DataFrame) -> pd.DataFrame:
    """
    Enforce metric contracts on ok-steps; return a **copy** of the DataFrame.

    For every row with ``step_status == "ok"`` that violates any contract,
    mutate the status to ``invalid`` and populate ``error_type`` /
    ``error_message`` so downstream gates/aggregation exclude the step
    from ok-only semantics.

    The input DataFrame is not mutated.

    Parameters
    ----------
    step_oos_long:
        Long-format DataFrame produced by
        :func:`wf_grid.collect.step_collector.collect_oos_steps`.

    Returns
    -------
    pd.DataFrame
        Copy with contract-violating ok-rows re-tagged as ``invalid``.
    """
    df = step_oos_long.copy()

    # Edge case: empty frame — nothing to validate.
    if df.empty:
        return df

    # Ensure error columns exist (they do for canonical collector output, but
    # tests may construct minimal DataFrames — be forgiving).
    if "error_type" not in df.columns:
        df["error_type"] = None
    if "error_message" not in df.columns:
        df["error_message"] = None
    if "step_status" not in df.columns:
        raise ValueError(
            "validate_metric_contracts requires 'step_status' column in step_oos_long"
        )

    ok_mask = df["step_status"] == StepStatus.OK.value
    if not ok_mask.any():
        return df

    for idx in df.index[ok_mask]:
        row = df.loc[idx]
        violation = _check_row_contracts(row)
        if violation is not None:
            df.at[idx, "step_status"] = StepStatus.INVALID.value
            df.at[idx, "error_type"] = "metric_contract"
            df.at[idx, "error_message"] = violation

    return df


# ---------------------------------------------------------------------------
# Internal row-level checks
# ---------------------------------------------------------------------------

def _check_row_contracts(row: pd.Series) -> str | None:
    """Return the first contract-violation message or None if the row is fine."""

    # 1. Hard-required metrics: present + finite + not sentinel.
    for name in HARD_REQUIRED_METRICS:
        if name not in row.index:
            return f"hard metric '{name}' is missing from step_oos_long columns"

        value = row[name]

        if _is_missing(value):
            return f"hard metric '{name}' is missing (NaN/None) on ok-step"

        if not _is_finite(value):
            return (
                f"hard metric '{name}' is not finite on ok-step "
                f"(got {value!r})"
            )

        if _is_sentinel(value):
            return (
                f"hard metric '{name}' equals INVALID_METRIC_VALUE "
                f"({INVALID_METRIC_VALUE}) on ok-step"
            )

    # 2. max_drawdown convention: must be <= 0 on ok-step.
    #    Positive max_drawdown on an ok-step is a hard contract violation
    #    (fail-closed, not a warning — see plan §4.1).
    md = row["max_drawdown"]
    if _is_finite(md) and float(md) > 0.0:
        return (
            f"max_drawdown > 0 on ok-step (got {md!r}); "
            "convention requires max_drawdown <= 0"
        )

    # 3. Soft metrics: sentinel => invalid; NaN is tolerated.
    for name in SOFT_METRICS:
        if name not in row.index:
            continue  # schema tolerates missing soft metric columns
        value = row[name]
        if _is_missing(value):
            continue  # soft NaN is legitimate (plan §4.3)
        if _is_sentinel(value):
            return (
                f"soft metric '{name}' equals INVALID_METRIC_VALUE "
                f"({INVALID_METRIC_VALUE}) on ok-step"
            )

    return None


# ---------------------------------------------------------------------------
# Scalar helpers (tolerant to numpy / python types)
# ---------------------------------------------------------------------------

def _is_missing(v) -> bool:
    """True when value is None or NaN (pd.NA included)."""
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _is_finite(v) -> bool:
    """True when v is numeric and finite (no inf / nan)."""
    if _is_missing(v):
        return False
    try:
        return bool(np.isfinite(float(v)))
    except (TypeError, ValueError):
        return False


def _is_sentinel(v) -> bool:
    """True when v bit-matches the donor INVALID_METRIC_VALUE."""
    try:
        return float(v) == float(INVALID_METRIC_VALUE)
    except (TypeError, ValueError):
        return False


# Exported for tests / external wiring
__all__ = [
    "HARD_REQUIRED_METRICS",
    "SOFT_METRICS",
    "validate_metric_contracts",
]
