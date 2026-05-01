"""
Tier assignment for WF grid search (§7.1).

3-tier contract:
  Tier 1 (strong Phase A ok-only):
    - candidate_status == "ok"   (all WF steps ok)
    - seed_gate_passed == True
    - n_ok_steps >= min_segments_for_ranking

    Tier 1 means: strong candidate under Phase A ok-only logic.
    Tier 1 is NOT proof of edge, not a trading signal, not ready for
    paper trading or live deployment.  It is a starting point for
    Phase B validation, nothing more.  (Plan §4.7 / T1.)

  Tier 2 (viable):
    - candidate_status in {"ok", "partial"}, not in Tier 1
      Includes: ok but gates failed; partial any gate; insufficient ok steps for Tier 1.

  Tier 3 (failed):
    - candidate_status == "failed"  (n_ok_steps == 0)

min_segments_for_ranking:
  - Config key: ranking.min_segments_for_ranking
  - Default: max(2, ceil(n_segments * 0.5))
  - n_segments is derived from n_total_steps (assumed uniform across candidates).

candidate_status is derived here from n_ok_steps / n_total_steps:
  - all ok:  n_ok_steps == n_total_steps  → "ok"
  - none ok: n_ok_steps == 0             → "failed"
  - mix:                                  → "partial"
"""

from __future__ import annotations

import math

import pandas as pd

from wf_grid.config.schema import GridConfig
from wf_grid.status.status_model import CandidateStatus


def assign_tiers(summary_wide: pd.DataFrame, config: GridConfig) -> pd.Series:
    """
    Assign ranking tier (1 / 2 / 3) to every row of the aggregated DataFrame.

    Parameters
    ----------
    summary_wide:
        Aggregated + gated + scored DataFrame.
        Required columns: n_ok_steps, n_total_steps, seed_gate_passed.
    config:
        Validated GridConfig (provides ranking.min_segments_for_ranking).

    Returns
    -------
    pd.Series[int]
        Tier values (1, 2, or 3) aligned with summary_wide.index.
    """
    if summary_wide.empty:
        return pd.Series(dtype=int)

    n_ok = summary_wide["n_ok_steps"]
    n_total = summary_wide["n_total_steps"]

    # Derive candidate_status per row
    candidate_status = _derive_candidate_status(n_ok, n_total)

    # Resolve min_segments_for_ranking
    n_segments = int(n_total.max()) if len(n_total) > 0 else 1
    min_seg = _resolve_min_segments(config, n_segments)

    # Tier 1 mask
    is_ok_candidate = candidate_status == CandidateStatus.OK.value
    gate_passed = summary_wide["seed_gate_passed"].fillna(False).astype(bool)
    enough_segments = n_ok >= min_seg

    tier1_mask = is_ok_candidate & gate_passed & enough_segments

    # Tier 3 mask
    is_failed_candidate = candidate_status == CandidateStatus.FAILED.value

    tiers = pd.Series(2, index=summary_wide.index, dtype=int)
    tiers[tier1_mask] = 1
    tiers[is_failed_candidate] = 3

    return tiers


def _derive_candidate_status(n_ok: pd.Series, n_total: pd.Series) -> pd.Series:
    """Derive candidate_status string from n_ok / n_total (mirrors assign_candidate_status)."""
    result = pd.Series(CandidateStatus.PARTIAL.value, index=n_ok.index, dtype=object)
    result[n_ok == n_total] = CandidateStatus.OK.value
    result[n_ok == 0] = CandidateStatus.FAILED.value
    return result


def _resolve_min_segments(config: GridConfig, n_segments: int) -> int:
    """
    Resolve min_segments_for_ranking.

    Default formula: max(2, ceil(n_segments * 0.5)).

    The plan (§7.1) also specifies validation: 2 <= min_segments <= n_segments.
    These two constraints are internally inconsistent when n_segments < 2
    (e.g. n_segments=1 cannot satisfy both lower bound 2 and upper bound 1).

    Normalizing rule (applied here to both default and explicit config):
        result = clamp(value, low=2, high=n_segments)
    When n_segments < 2, the upper bound wins (n_segments takes priority).
    This means for n_segments=1, effective min_segments=1 — a deliberate
    relaxation for tiny WF configurations, not a bug.  Any caller should
    treat a result of 1 as "no meaningful Tier-1 segment threshold".
    """
    # FIX-2.5: use `is not None` instead of walrus truthiness check.
    # The walrus form `if explicit := value:` treats 0 as False and silently
    # falls back to the default formula, ignoring the explicit config value.
    if config.ranking.min_segments_for_ranking is not None:
        raw = int(config.ranking.min_segments_for_ranking)
    else:
        raw = max(2, math.ceil(n_segments * 0.5))

    # Clamp: upper bound (n_segments) wins when n_segments < 2
    return min(raw, n_segments) if n_segments < 2 else max(2, min(raw, n_segments))
