"""
Status model for WF Grid Search.

Enums and assignment functions per plan §3.1–§3.3, §7.2, §7.3.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Iterable

if TYPE_CHECKING:
    from wf_grid.config.schema import GridConfig


class StepStatus(str, Enum):
    """OOS step execution status (§3.1)."""

    OK = "ok"
    SKIPPED = "skipped"
    NO_TRADES = "no_trades"
    INSUFFICIENT_BARS = "insufficient_bars"
    INVALID = "invalid"
    GATE_FAILED = "gate_failed"
    RUNTIME_ERROR = "runtime_error"


class CandidateStatus(str, Enum):
    """Aggregate candidate status across all WF steps (§3.3)."""

    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class RankingMode(str, Enum):
    """Ranking algorithm mode (§7.2)."""

    LEGACY = "legacy"
    GATES_SCORE = "gates_score"


class ScoreContractStatus(str, Enum):
    """Status of tester_seed_score computation (§7.3)."""

    OK = "ok"
    PARTIAL = "partial"
    NO_SCORE = "no_score"


# ---------------------------------------------------------------------------
# Step status assignment (§3.2)
# ---------------------------------------------------------------------------

def assign_step_status(
    metrics: Dict[str, Any],
    effective_oos_bars: int,
    config: "GridConfig",
) -> StepStatus:
    """
    Determine StepStatus from OOS metrics and bar count (§3.2).

    Priority order (first matching rule wins):
    1. ``insufficient_bars``  — effective_oos_bars < min_meaningful_bars.
    2. ``no_trades``          — num_trades == 0 (after OOS trim/override).
    3. ``ok``                 — all checks passed.

    Notes
    -----
    - ``gate_failed`` is NOT assigned here; it is applied by the gates layer
      (A8) after step execution, by mutating step_status in step_oos_long.
    - ``runtime_error`` / ``invalid`` / ``skipped`` are assigned by the
      runner (A4) when execution itself fails; this function only handles
      the post-execution metrics validation path.
    - ``metrics`` must contain the key ``"num_trades"`` (int or float).
      Any other missing metric keys are tolerated — they do not affect
      status assignment here.

    Parameters
    ----------
    metrics:
        Dict of OOS metrics produced by run_single_backtest / trim step.
        Must include ``"num_trades"``.
    effective_oos_bars:
        Number of bars in the OOS-only slice after trim.
    config:
        Validated GridConfig (provides ``status.min_meaningful_bars``).

    Returns
    -------
    StepStatus
    """
    min_bars = config.status.min_meaningful_bars

    if effective_oos_bars < min_bars:
        return StepStatus.INSUFFICIENT_BARS

    num_trades = metrics.get("num_trades", 0)
    if num_trades == 0:
        return StepStatus.NO_TRADES

    return StepStatus.OK


# ---------------------------------------------------------------------------
# Candidate status assignment (§3.3)
# ---------------------------------------------------------------------------

def assign_candidate_status(
    step_statuses: Iterable[StepStatus],
) -> CandidateStatus:
    """
    Derive CandidateStatus from the collection of step statuses (§3.3).

    Rules:
    - ``ok``      — every step is StepStatus.OK.
    - ``failed``  — no step is StepStatus.OK (ok_steps == 0).
    - ``partial`` — mix of ok and non-ok steps.

    An empty iterable is treated as ``failed`` (no valid steps).

    Parameters
    ----------
    step_statuses:
        Iterable of StepStatus values for all WF steps of one candidate.

    Returns
    -------
    CandidateStatus
    """
    statuses = list(step_statuses)
    if not statuses:
        return CandidateStatus.FAILED

    ok_count = sum(1 for s in statuses if s == StepStatus.OK)

    if ok_count == len(statuses):
        return CandidateStatus.OK
    if ok_count == 0:
        return CandidateStatus.FAILED
    return CandidateStatus.PARTIAL
