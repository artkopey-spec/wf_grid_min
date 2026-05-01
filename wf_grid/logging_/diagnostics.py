"""
Pipeline diagnostics summary (§A13).

Produces a structured summary log after pipeline execution:
  - Grid size (total grid points, WF steps)
  - Step-status distribution
  - Tier distribution
  - Top-5 ranked candidates
  - Timing breakdown per pipeline stage
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StageTimer:
    """Collects wall-clock timing per pipeline stage."""
    timings: Dict[str, float] = field(default_factory=dict)

    def record(self, stage: str, elapsed_sec: float) -> None:
        self.timings[stage] = elapsed_sec


@dataclass
class PipelineDiagnostics:
    """Summary diagnostics for a completed pipeline run."""
    grid_size: int = 0
    n_wf_steps: int = 0
    step_status_counts: Dict[str, int] = field(default_factory=dict)
    tier_counts: Dict[int, int] = field(default_factory=dict)
    top5_ranked: List[Dict] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)


def build_diagnostics(
    grid_size: int,
    n_wf_steps: int,
    step_oos_long: pd.DataFrame,
    ranked: pd.DataFrame,
    timer: StageTimer,
) -> PipelineDiagnostics:
    """Build diagnostics from pipeline outputs."""
    diag = PipelineDiagnostics(
        grid_size=grid_size,
        n_wf_steps=n_wf_steps,
        timings=dict(timer.timings),
    )

    if not step_oos_long.empty and "step_status" in step_oos_long.columns:
        diag.step_status_counts = step_oos_long["step_status"].value_counts().to_dict()

    if not ranked.empty and "tier" in ranked.columns:
        diag.tier_counts = ranked["tier"].value_counts().to_dict()

    if not ranked.empty and "grid_rank" in ranked.columns:
        top = ranked.nsmallest(5, "grid_rank")
        cols = ["grid_rank", "grid_point_id", "tier", "tester_seed_score",
                "sum_pnl_pct_Median", "seed_gate_passed"]
        present = [c for c in cols if c in top.columns]
        diag.top5_ranked = top[present].to_dict(orient="records")

    return diag


def log_diagnostics(diag: PipelineDiagnostics) -> None:
    """Log diagnostics summary to the module logger."""
    logger.info("=" * 60)
    logger.info("Pipeline Diagnostics Summary")
    logger.info("=" * 60)
    logger.info("Grid: %d points × %d WF steps", diag.grid_size, diag.n_wf_steps)
    logger.info("Step status distribution: %s", diag.step_status_counts)
    logger.info("Tier distribution: %s", diag.tier_counts)
    logger.info("Top 5 ranked:")
    for entry in diag.top5_ranked:
        logger.info("  %s", entry)
    if diag.timings:
        logger.info("Timing breakdown:")
        for stage, t in diag.timings.items():
            logger.info("  %s: %.2fs", stage, t)
    logger.info("=" * 60)
