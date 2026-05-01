"""
Diversification filter for Walk-Forward parameter ensemble selection.

Leaf module — no Excel / openpyxl / io imports.
Lives in scoring/ alongside canonical.py and aggregation.py.

Public API
----------
validate_diversification_config(div_cfg)      -> (bool, str)
run_diversification_filter(canonical_df, result, config) -> DiversificationResult

Internal helpers
----------------
_normalize_param_key(atr_period, multiplier)  -> tuple[int, float]
_stitch_oos_arrays(result, atr_period, multiplier, field) -> StitchedArray | None
_pearson_corr_aligned(sa, sb, min_common_steps, min_common_bars) -> PairwiseCorrelationResult
_compute_correlation_matrix(stitched_dict, min_common_steps, min_common_bars) -> (pd.DataFrame, pd.DataFrame)
_compute_overlap_matrix(positions_dict)       -> pd.DataFrame
_compute_phase_similarity_matrix(positions_dict, lag_window) -> pd.DataFrame
_greedy_select(candidates, score_map, corr_matrix, corr_meta_lookup, overlap_matrix, phase_matrix, config, bucket_map, bucket_stability_map) -> list
_filter_to_representatives(valid_keys, bucket_map, score_map, div_cfg) -> list
_subset_portfolio_score(subset_keys, score_map, corr_matrix, lambda_penalty, bucket_map, bucket_stability_map, stability_weight, corr_thr) -> float
_subset_search(candidates, score_map, corr_matrix, overlap_matrix, phase_matrix, div_cfg, bucket_map, bucket_stability_map, corr_meta_lookup) -> (list, int, float)
_compute_candidate_stability_map(result, candidate_keys, canonical_df, top_n) -> dict
_build_step_winners_table(result, candidate_keys) -> pd.DataFrame
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from supertrend_optimizer.utils.constants import (
    DEFAULT_ATR_BUCKET_STEP,
    DEFAULT_MULT_BUCKET_STEP,
)

# Canonical label builder — import deferred to avoid circular imports at
# module level (diversification ← aggregation is safe; reverse isn't).
# Use _canonical_format_* aliases to distinguish from local deprecated helpers.
def _canonical_format_bucket_label(
    atr_bucket: int, mult_bucket_ticks: int, atr_step: int, mult_step: float,
) -> str:
    from supertrend_optimizer.scoring.aggregation import format_bucket_label
    return format_bucket_label(atr_bucket, mult_bucket_ticks, atr_step, mult_step)


def _canonical_format_atr_range(atr_bucket: int, atr_step: int) -> str:
    from supertrend_optimizer.scoring.aggregation import format_atr_range
    return format_atr_range(atr_bucket, atr_step)


def _canonical_format_mult_range(mult_bucket_ticks: int, mult_step: float) -> str:
    from supertrend_optimizer.scoring.aggregation import format_mult_range
    return format_mult_range(mult_bucket_ticks, mult_step)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Correlation status constants and result types
# ---------------------------------------------------------------------------

class CorrStatus:
    """Status codes for pairwise correlation computation."""
    OK                 = "ok"
    INSUFFICIENT_STEPS = "insufficient_steps"
    INSUFFICIENT_BARS  = "insufficient_bars"
    CONSTANT_EQUAL     = "constant_equal"    # both arrays are the same constant
    CONSTANT_UNEQUAL   = "constant_unequal"  # different constants, std=0
    NO_DATA            = "no_data"           # one or both arrays empty / no common steps


@dataclass
class PairwiseCorrelationResult:
    """Result of a single pairwise correlation computation.

    ``status`` is the single source of truth — consumers must check
    ``is_reliable`` or ``status`` directly, never ``np.isnan(corr)``.
    """
    corr: float          # Pearson value; NaN when status not in (OK, CONSTANT_*)
    status: str          # one of CorrStatus.*
    common_steps: int
    common_bars: int
    coverage_a: float    # present_steps_a / total_steps
    coverage_b: float    # present_steps_b / total_steps
    overlap_ratio: float # common_steps / union_steps

    @property
    def is_reliable(self) -> bool:
        return self.status in (
            CorrStatus.OK,
            CorrStatus.CONSTANT_EQUAL,
            CorrStatus.CONSTANT_UNEQUAL,
        )


class RejectReason:
    """Reason codes used in _greedy_select diagnostics."""
    CORR_TOO_HIGH        = "corr_too_high"
    INSUFFICIENT_OVERLAP = "insufficient_overlap"
    NO_CORR_META         = "no_corr_meta"
    OVERLAP_TOO_HIGH     = "overlap_too_high"
    PHASE_TOO_HIGH       = "phase_too_high"


# ---------------------------------------------------------------------------
# StitchedArray — per-step OOS array with provenance
# ---------------------------------------------------------------------------

@dataclass
class StitchedArray:
    """OOS array with per-step provenance.

    Stores each WF step's OOS chunk separately so that pairwise correlation
    can align on *common steps* rather than naively concatenating arrays of
    potentially different lengths.
    """
    step_chunks: Dict[int, np.ndarray]  # step_idx -> OOS array for that step
    total_steps: int                    # total WF steps in the run

    @property
    def step_indices(self) -> List[int]:
        return sorted(self.step_chunks.keys())

    @property
    def present_steps(self) -> int:
        return len(self.step_chunks)

    @property
    def coverage_ratio(self) -> float:
        return self.present_steps / max(self.total_steps, 1)

    @property
    def total_bars(self) -> int:
        return sum(len(c) for c in self.step_chunks.values())

    @property
    def data(self) -> np.ndarray:
        """Flat concatenated array in step order."""
        if not self.step_chunks:
            return np.array([], dtype=np.float64)
        return np.concatenate(
            [self.step_chunks[i] for i in self.step_indices]
        )


# ---------------------------------------------------------------------------
# Surface layer: bucket grid helpers
# ---------------------------------------------------------------------------

def _generate_full_bucket_grid(
    config: Dict[str, Any],
    *,
    atr_bucket_step: Optional[int] = None,
    mult_bucket_step: Optional[float] = None,
) -> List[Tuple[int, int]]:
    """Return every (atr_bucket, mult_bucket_ticks) implied by config ranges.

    Extracted from _build_full_bucket_step_matrix so both the top-k matrix
    and the surface matrix share a single source of truth for grid generation.

    Iterates every discrete (atr_period, multiplier) point in the search space
    and maps it through the same bucket formula used by _apply_param_buckets,
    guaranteeing zero drift between grid generation and runtime assignment.

    Returns
    -------
    List of (atr_bucket, mult_bucket_ticks) tuples, sorted in row-major order
    (atr_bucket ascending, then mult_bucket_ticks ascending).  May be empty
    when config is missing or ranges are degenerate.
    """
    from supertrend_optimizer.scoring.aggregation import _apply_param_buckets

    opt_cfg  = config.get("optimization", {})
    wf_cfg   = config.get("walk_forward", {})
    cons_cfg = wf_cfg.get("consensus", {})

    atr_range     = opt_cfg.get("atr_period_range", [10, 50])
    mult_range    = opt_cfg.get("multiplier_range", [1.8, 3.5])
    mult_step_opt = float(opt_cfg.get("multiplier_step", 0.1))
    if atr_bucket_step is None:
        atr_bucket_step = int(cons_cfg.get("atr_bucket_step", DEFAULT_ATR_BUCKET_STEP))
    if mult_bucket_step is None:
        mult_bucket_step = float(cons_cfg.get("mult_bucket_step", DEFAULT_MULT_BUCKET_STEP))

    atr_min, atr_max   = int(atr_range[0]), int(atr_range[1])
    mult_min, mult_max = float(mult_range[0]), float(mult_range[1])

    if atr_min > atr_max or mult_min > mult_max or mult_step_opt <= 0:
        return []

    # Build full discrete search-space as a DataFrame, then apply the same
    # _apply_param_buckets used at runtime — DR-1 anti-drift requirement.
    atr_vals: List[int] = list(range(atr_min, atr_max + 1))
    n_mult = int(round((mult_max - mult_min) / mult_step_opt)) + 1
    mult_vals: List[float] = []
    for i in range(n_mult):
        mv = round(mult_min + i * mult_step_opt, 10)
        if mv > mult_max + 1e-9:
            break
        mult_vals.append(mv)

    if not atr_vals or not mult_vals:
        return []

    rows = [
        {"atr_period": a, "multiplier": m}
        for a in atr_vals
        for m in mult_vals
    ]
    grid_df = pd.DataFrame(rows)
    grid_df = _apply_param_buckets(
        grid_df, atr_step=atr_bucket_step, mult_step=mult_bucket_step
    )

    seen: set = set()
    result: List[Tuple[int, int]] = []
    for ab, mt in zip(grid_df["atr_bucket"], grid_df["mult_bucket_ticks"]):
        key = (int(ab), int(mt))
        if key not in seen:
            seen.add(key)
            result.append(key)

    return sorted(result)


def _compute_expected_grid_sizes(
    config: Dict[str, Any],
    *,
    atr_bucket_step: Optional[int] = None,
    mult_bucket_step: Optional[float] = None,
) -> Dict[Tuple[int, int], int]:
    """Return the number of discrete search-space points that map to each bucket.

    DR-1 (anti-drift): uses _apply_param_buckets — the same function used
    at runtime — so expected_grid_size is guaranteed to match actual trial
    bucket assignments.

    Boundary buckets (e.g. the lowest ATR bucket when atr_min=13 and
    atr_bucket_step=3) may contain fewer than the interior bucket count
    because fewer discrete atr/mult values round into them.

    Returns
    -------
    Dict mapping (atr_bucket, mult_bucket_ticks) -> int count.
    Keys cover every bucket returned by _generate_full_bucket_grid(config).
    Values are >= 1 by construction (each bucket has at least one point).
    """
    from supertrend_optimizer.scoring.aggregation import _apply_param_buckets

    opt_cfg  = config.get("optimization", {})
    wf_cfg   = config.get("walk_forward", {})
    cons_cfg = wf_cfg.get("consensus", {})

    atr_range     = opt_cfg.get("atr_period_range", [10, 50])
    mult_range    = opt_cfg.get("multiplier_range", [1.8, 3.5])
    mult_step_opt = float(opt_cfg.get("multiplier_step", 0.1))
    if atr_bucket_step is None:
        atr_bucket_step = int(cons_cfg.get("atr_bucket_step", DEFAULT_ATR_BUCKET_STEP))
    if mult_bucket_step is None:
        mult_bucket_step = float(cons_cfg.get("mult_bucket_step", DEFAULT_MULT_BUCKET_STEP))

    atr_min, atr_max   = int(atr_range[0]), int(atr_range[1])
    mult_min, mult_max = float(mult_range[0]), float(mult_range[1])

    if atr_min > atr_max or mult_min > mult_max or mult_step_opt <= 0:
        return {}

    atr_vals: List[int] = list(range(atr_min, atr_max + 1))
    n_mult = int(round((mult_max - mult_min) / mult_step_opt)) + 1
    mult_vals: List[float] = []
    for i in range(n_mult):
        mv = round(mult_min + i * mult_step_opt, 10)
        if mv > mult_max + 1e-9:
            break
        mult_vals.append(mv)

    if not atr_vals or not mult_vals:
        return {}

    rows = [
        {"atr_period": a, "multiplier": m}
        for a in atr_vals
        for m in mult_vals
    ]
    grid_df = pd.DataFrame(rows)
    grid_df = _apply_param_buckets(
        grid_df, atr_step=atr_bucket_step, mult_step=mult_bucket_step
    )

    sizes: Dict[Tuple[int, int], int] = {}
    for ab, mt in zip(grid_df["atr_bucket"], grid_df["mult_bucket_ticks"]):
        key = (int(ab), int(mt))
        sizes[key] = sizes.get(key, 0) + 1

    return sizes


# ---------------------------------------------------------------------------
# Parameter key normalisation
# ---------------------------------------------------------------------------

def _normalize_param_key(atr_period: Any, multiplier: Any) -> Tuple[int, float]:
    """Canonical key for a parameter set: (int(atr), round(mult, 6)).

    Must be applied consistently across all code paths to avoid float-precision
    mismatches when using tuples as dict keys or matrix labels.
    """
    return (int(atr_period), round(float(multiplier), 6))


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def validate_diversification_config(div_cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate the diversification config block.

    Returns (True, "") if valid; (False, reason) if any check fails.
    Failure is non-fatal — caller should disable diversification gracefully.
    """
    checks = [
        ("enabled",            lambda v: isinstance(v, bool),
         "must be bool"),
        ("max_models",         lambda v: isinstance(v, int) and v >= 2,
         "must be int >= 2"),
        ("top_n_candidates",   lambda v: isinstance(v, int) and v >= div_cfg.get("max_models", 2),
         "must be int >= max_models"),
        ("top_m_heavy_checks", lambda v: isinstance(v, int) and v <= div_cfg.get("top_n_candidates", 20),
         "must be int <= top_n_candidates"),
        ("corr_threshold",     lambda v: isinstance(v, (int, float)) and 0 <= v <= 1,
         "must be 0 <= value <= 1"),
        ("overlap_threshold",  lambda v: isinstance(v, (int, float)) and 0 <= v <= 1,
         "must be 0 <= value <= 1"),
        ("phase_threshold",    lambda v: isinstance(v, (int, float)) and 0 <= v <= 1,
         "must be 0 <= value <= 1"),
        ("lambda_penalty",     lambda v: isinstance(v, (int, float)) and v >= 0,
         "must be >= 0"),
        ("phase_lag_window",   lambda v: isinstance(v, int) and v >= 0,
         "must be int >= 0"),
        ("min_bucket_stability_for_ensemble",
         lambda v: isinstance(v, (int, float)) and 0 <= v <= 1,
         "must be 0 <= value <= 1"),
        ("stability_weight",
         lambda v: isinstance(v, (int, float)) and 0 <= v <= 1,
         "must be 0 <= value <= 1"),
        ("min_common_steps",
         lambda v: isinstance(v, int) and v >= 1,
         "must be int >= 1"),
        ("min_common_bars",
         lambda v: isinstance(v, int) and v >= 2,
         "must be int >= 2"),
        # Bucket representative filtering (optional)
        ("representative_mode",
         lambda v: v in ("off", "per_bucket"),
         'must be "off" or "per_bucket"'),
        ("representatives_per_bucket",
         lambda v: isinstance(v, int) and v >= 1,
         "must be int >= 1"),
        ("representative_rank_basis",
         lambda v: v in ("canonical_rank", "final_score"),
         'must be "canonical_rank" or "final_score"'),
        ("preserve_global_order",
         lambda v: isinstance(v, bool),
         "must be bool"),
        # Global subset search (optional)
        ("subset_search_enabled",
         lambda v: isinstance(v, bool),
         "must be bool"),
        ("subset_search_mode",
         lambda v: v in ("pair", "pair_triple"),
         'must be "pair" or "pair_triple"'),
        ("subset_search_max_candidates",
         lambda v: isinstance(v, int) and v >= 2,
         "must be int >= 2"),
    ]
    for key, checker, msg in checks:
        if key not in div_cfg:
            continue  # optional keys use defaults; only validate if present
        val = div_cfg[key]
        try:
            ok = checker(val)
        except Exception:
            ok = False
        if not ok:
            return False, f"diversification.{key}={val!r}: {msg}"
    return True, ""


# ---------------------------------------------------------------------------
# DiversificationResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class DiversificationResult:
    """All outputs of the diversification filter.

    Consumed by the Excel writer (_write_wf_ensemble_sheet and
    _write_wf_diversification_matrix_sheet) — no io/ imports here.
    """
    # Table A: top-N candidates with in_ensemble flag
    candidates_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Table B: pair diagnostics for ensemble / top-M pairs
    pair_diagnostics_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Table C: per-step OOS pnl matrix + win stats
    step_winners_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Table D: bucket-aggregated step performance
    bucket_step_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Matrix sheet: correlation (N×N), overlap (M×M), phase (M×M)
    corr_matrix: pd.DataFrame = field(default_factory=pd.DataFrame)
    overlap_matrix: pd.DataFrame = field(default_factory=pd.DataFrame)
    phase_matrix: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Block 4: long-format correlation metadata (one row per pair)
    corr_meta_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Metadata
    enabled: bool = False
    placeholder: bool = False  # True → write headers only, no data

    # New diagnostic metadata (backward-safe defaults)
    representative_mode: str = "off"
    representative_pool_size: Optional[int] = None
    subset_search_enabled: bool = False
    subsets_evaluated: Optional[int] = None
    best_subset_score: Optional[float] = None

    @classmethod
    def empty(cls) -> "DiversificationResult":
        """Feature disabled and write_placeholder_sheets_when_disabled=False."""
        return cls(enabled=False, placeholder=False)

    @classmethod
    def make_placeholder(cls) -> "DiversificationResult":
        """Feature disabled but write_placeholder_sheets_when_disabled=True."""
        return cls(enabled=False, placeholder=True)


# ---------------------------------------------------------------------------
# Array stitching
# ---------------------------------------------------------------------------

def _stitch_oos_arrays(
    result: Any,
    atr_period: int,
    multiplier: float,
    field_name: str,
) -> Optional[StitchedArray]:
    """Stitch per-step OOS arrays for a given (atr_period, multiplier) pair.

    Iterates result.steps, finds WFStepTopEntry objects matching the key,
    and stores each step's array separately in a StitchedArray so that
    pairwise correlation can align on common steps.

    Returns None if no data found for this candidate.
    """
    key = _normalize_param_key(atr_period, multiplier)
    total_steps = len(result.steps)
    step_chunks: Dict[int, np.ndarray] = {}

    for step_idx, step in enumerate(result.steps):
        for entry in step.top_entries:
            entry_key = _normalize_param_key(entry.atr_period, entry.multiplier)
            if entry_key == key:
                arr = getattr(entry, field_name, None)
                if arr is not None and len(arr) > 0:
                    step_chunks[step_idx] = np.asarray(arr, dtype=np.float64)
                break  # only one entry per (atr, mult) per step

    if not step_chunks:
        return None
    return StitchedArray(step_chunks=step_chunks, total_steps=total_steps)


# ---------------------------------------------------------------------------
# Pairwise metric matrices
# ---------------------------------------------------------------------------

def _pearson_corr_safe(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation on the common overlapping (shortest) portion.

    Deprecated: superseded by _pearson_corr_aligned for correlation matrix
    computation.  Kept to avoid breaking any external callers or tests.
    """
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a_, b_ = a[:n], b[:n]
    if np.std(a_) == 0 or np.std(b_) == 0:
        return 1.0 if np.allclose(a_, b_) else 0.0
    return float(np.corrcoef(a_, b_)[0, 1])


def _pearson_corr_aligned(
    sa: StitchedArray,
    sb: StitchedArray,
    min_common_steps: int = 2,
    min_common_bars: int = 400,
) -> PairwiseCorrelationResult:
    """Pearson correlation aligned on common WF steps.

    Only bars from steps present in *both* StitchedArrays are used.
    Within each common step the shorter chunk is used (min-length trim).
    Returns a PairwiseCorrelationResult whose ``status`` is the single
    source of truth — consumers must never rely on ``np.isnan(corr)``.
    """
    common_set = set(sa.step_indices) & set(sb.step_indices)
    union_set  = set(sa.step_indices) | set(sb.step_indices)
    common_steps = len(common_set)
    overlap_ratio = common_steps / max(len(union_set), 1)

    meta: Dict[str, Any] = dict(
        common_steps=common_steps,
        common_bars=0,
        coverage_a=sa.coverage_ratio,
        coverage_b=sb.coverage_ratio,
        overlap_ratio=overlap_ratio,
    )

    if common_steps == 0:
        return PairwiseCorrelationResult(
            corr=float("nan"), status=CorrStatus.NO_DATA, **meta)

    parts_a: List[np.ndarray] = []
    parts_b: List[np.ndarray] = []
    for idx in sorted(common_set):
        ca, cb = sa.step_chunks[idx], sb.step_chunks[idx]
        n = min(len(ca), len(cb))
        parts_a.append(ca[:n])
        parts_b.append(cb[:n])

    r_a = np.concatenate(parts_a)
    r_b = np.concatenate(parts_b)
    meta["common_bars"] = len(r_a)

    if common_steps < min_common_steps:
        return PairwiseCorrelationResult(
            corr=float("nan"), status=CorrStatus.INSUFFICIENT_STEPS, **meta)

    if len(r_a) < min_common_bars:
        return PairwiseCorrelationResult(
            corr=float("nan"), status=CorrStatus.INSUFFICIENT_BARS, **meta)

    std_a, std_b = float(np.std(r_a)), float(np.std(r_b))
    if std_a == 0 or std_b == 0:
        if np.allclose(r_a, r_b):
            return PairwiseCorrelationResult(
                corr=1.0, status=CorrStatus.CONSTANT_EQUAL, **meta)
        return PairwiseCorrelationResult(
            corr=0.0, status=CorrStatus.CONSTANT_UNEQUAL, **meta)

    corr = float(np.corrcoef(r_a, r_b)[0, 1])
    return PairwiseCorrelationResult(
        corr=corr, status=CorrStatus.OK, **meta)


def _compute_correlation_matrix(
    stitched_dict: Dict[Tuple[int, float], Any],
    min_common_steps: int = 2,
    min_common_bars: int = 400,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """N×N Pearson correlation matrix with full pair metadata.

    ``stitched_dict`` values may be either ``StitchedArray`` objects or plain
    ``np.ndarray`` (legacy / test usage).  Plain arrays are automatically
    wrapped into a single-step ``StitchedArray`` so the function is backward
    compatible with existing tests and callers.

    Returns
    -------
    corr_matrix : pd.DataFrame
        N×N symmetric matrix; NaN for pairs with insufficient overlap.
    corr_meta_df : pd.DataFrame
        Long-format table, one row per pair, with status and overlap metadata.
    """
    # Normalise: wrap plain arrays into StitchedArray (step_idx=0, total_steps=1).
    # When any value is a plain array (legacy / test usage) we relax the thresholds
    # to match the old _pearson_corr_safe behaviour (no step/bar minimums).
    has_legacy = any(not isinstance(v, StitchedArray) for v in stitched_dict.values())
    if has_legacy:
        min_common_steps = 1
        min_common_bars  = 2

    normalised: Dict[Tuple[int, float], StitchedArray] = {}
    for k, v in stitched_dict.items():
        if isinstance(v, StitchedArray):
            normalised[k] = v
        else:
            arr = np.asarray(v, dtype=np.float64)
            normalised[k] = StitchedArray(
                step_chunks={0: arr} if len(arr) > 0 else {},
                total_steps=1,
            )

    keys = list(normalised.keys())
    labels = [f"atr={k[0]}_mult={k[1]}" for k in keys]
    n = len(keys)
    mat = np.eye(n)
    meta_rows: List[Dict[str, Any]] = []

    for i in range(n):
        for j in range(i + 1, n):
            res = _pearson_corr_aligned(
                normalised[keys[i]], normalised[keys[j]],
                min_common_steps, min_common_bars,
            )
            mat[i, j] = res.corr
            mat[j, i] = res.corr
            meta_rows.append({
                "model_A":       labels[i],
                "model_B":       labels[j],
                "corr_returns":  res.corr,
                "status":        res.status,
                "common_steps":  res.common_steps,
                "common_bars":   res.common_bars,
                "coverage_a":    res.coverage_a,
                "coverage_b":    res.coverage_b,
                "overlap_ratio": res.overlap_ratio,
            })

    corr_df = pd.DataFrame(mat, index=labels, columns=labels)
    meta_df = pd.DataFrame(meta_rows) if meta_rows else pd.DataFrame(
        columns=["model_A", "model_B", "corr_returns", "status",
                 "common_steps", "common_bars", "coverage_a", "coverage_b",
                 "overlap_ratio"]
    )
    return corr_df, meta_df


def _overlap_rate(pos_i: np.ndarray, pos_j: np.ndarray) -> float:
    """Fraction of bars where both strategies are simultaneously in a position.

    overlap_rate = |{t: pos_i[t]!=0 AND pos_j[t]!=0}|
                 / |{t: pos_i[t]!=0 OR  pos_j[t]!=0}|

    Returns 0 if the union denominator is zero.
    """
    n = min(len(pos_i), len(pos_j))
    if n == 0:
        return 0.0
    a, b = pos_i[:n] != 0, pos_j[:n] != 0
    union = int(np.sum(a | b))
    if union == 0:
        return 0.0
    return float(np.sum(a & b)) / union


def _compute_overlap_matrix(
    positions_dict: Dict[Tuple[int, float], np.ndarray],
) -> pd.DataFrame:
    """M×M trade overlap rate matrix."""
    keys = list(positions_dict.keys())
    labels = [f"atr={k[0]}_mult={k[1]}" for k in keys]
    n = len(keys)
    mat = np.zeros((n, n))
    for i in range(n):
        mat[i, i] = 1.0
        for j in range(i + 1, n):
            r = _overlap_rate(positions_dict[keys[i]], positions_dict[keys[j]])
            mat[i, j] = r
            mat[j, i] = r
    return pd.DataFrame(mat, index=labels, columns=labels)


def _active_bar_phase_similarity(
    pos_i: np.ndarray,
    pos_j: np.ndarray,
    lag_window: int,
) -> float:
    """Max active-bar phase similarity across lags in [-W, +W].

    Similarity is computed only on bars where at least one strategy is active
    (non-zero position), avoiding inflation from shared flat periods.

    Returns 0 if no active bars exist for any lag.
    """
    n = min(len(pos_i), len(pos_j))
    if n == 0:
        return 0.0

    best = 0.0
    for lag in range(-lag_window, lag_window + 1):
        if lag >= 0:
            a = pos_i[:n - lag] if lag > 0 else pos_i[:n]
            b = pos_j[lag:n] if lag > 0 else pos_j[:n]
        else:
            shift = -lag
            a = pos_i[shift:n]
            b = pos_j[:n - shift]

        m = min(len(a), len(b))
        if m == 0:
            continue
        a, b = a[:m], b[:m]
        active_mask = (np.abs(a) + np.abs(b)) > 0
        active_count = int(np.sum(active_mask))
        if active_count == 0:
            continue
        sim = float(np.sum(a[active_mask] == b[active_mask])) / active_count
        if sim > best:
            best = sim

    return best


def _compute_phase_similarity_matrix(
    positions_dict: Dict[Tuple[int, float], np.ndarray],
    lag_window: int,
) -> pd.DataFrame:
    """M×M active-bar phase similarity matrix."""
    keys = list(positions_dict.keys())
    labels = [f"atr={k[0]}_mult={k[1]}" for k in keys]
    n = len(keys)
    mat = np.zeros((n, n))
    for i in range(n):
        mat[i, i] = 1.0
        for j in range(i + 1, n):
            s = _active_bar_phase_similarity(
                positions_dict[keys[i]], positions_dict[keys[j]], lag_window
            )
            mat[i, j] = s
            mat[j, i] = s
    return pd.DataFrame(mat, index=labels, columns=labels)


# ---------------------------------------------------------------------------
# Greedy forward selection
# ---------------------------------------------------------------------------

def _portfolio_score(
    ensemble_keys: List[Tuple[int, float]],
    candidate_key: Tuple[int, float],
    score_map: Dict[Tuple[int, float], float],
    corr_matrix: pd.DataFrame,
    lambda_penalty: float,
    bucket_map: Optional[Dict[Tuple[int, float], Tuple[int, int]]],
    bucket_stability_map: Optional[Dict[Tuple[int, float], float]] = None,
    stability_weight: float = 0.0,
    corr_thr: float = 0.6,
) -> float:
    """Portfolio score for the hypothetical ensemble + candidate.

    score = mean(final_score of enlarged ensemble)
          - lambda_penalty * mean(all pairwise correlations)
          + small bucket-diversity bonus (soft tie-breaker)
          + stability_weight * candidate bucket_stability_score  (new)

    When stability_weight=0.0 (default) the result is identical to the
    pre-stability-gate behaviour, preserving full backward compatibility.

    NaN or missing correlation values are treated as corr_thr (conservative
    penalty) rather than 0.0 (optimistic) to avoid inflating portfolio scores
    for pairs with insufficient overlap data.
    """
    enlarged = ensemble_keys + [candidate_key]
    mean_score = float(np.mean([score_map.get(k, 0.0) for k in enlarged]))

    # Pairwise correlation penalty
    pairs = [
        (enlarged[i], enlarged[j])
        for i in range(len(enlarged))
        for j in range(i + 1, len(enlarged))
    ]
    if pairs:
        corrs = []
        for ka, kb in pairs:
            la = f"atr={ka[0]}_mult={ka[1]}"
            lb = f"atr={kb[0]}_mult={kb[1]}"
            if la in corr_matrix.index and lb in corr_matrix.columns:
                c = float(corr_matrix.loc[la, lb])
                # NaN means insufficient overlap — use corr_thr as conservative penalty
                corrs.append(corr_thr if np.isnan(c) else c)
            else:
                # Missing from matrix entirely — conservative penalty
                corrs.append(corr_thr)
        penalty = lambda_penalty * float(np.mean(corrs))
    else:
        penalty = 0.0

    # Bucket diversity bonus (soft tie-breaker, weight 0.01)
    bonus = 0.0
    if bucket_map is not None:
        existing_buckets = {bucket_map[k] for k in ensemble_keys if k in bucket_map}
        cand_bucket = bucket_map.get(candidate_key)
        if cand_bucket is not None and cand_bucket not in existing_buckets:
            bonus = 0.01

    # Bucket stability bonus (additive; disabled when stability_weight=0)
    stability_bonus = 0.0
    if bucket_stability_map is not None and stability_weight > 0:
        cand_stability = bucket_stability_map.get(candidate_key, 0.0)
        stability_bonus = stability_weight * cand_stability

    return mean_score - penalty + bonus + stability_bonus


def _label(key: Tuple[int, float]) -> str:
    return f"atr={key[0]}_mult={key[1]}"


def _greedy_select(
    candidates: List[Tuple[int, float]],
    score_map: Dict[Tuple[int, float], float],
    corr_matrix: pd.DataFrame,
    overlap_matrix: pd.DataFrame,
    phase_matrix: pd.DataFrame,
    config: Dict[str, Any],
    bucket_map: Optional[Dict[Tuple[int, float], Tuple[int, int]]] = None,
    bucket_stability_map: Optional[Dict[Tuple[int, float], float]] = None,
    corr_meta_lookup: Optional[Dict[Tuple[str, str], PairwiseCorrelationResult]] = None,
) -> List[Tuple[int, float]]:
    """Greedy forward selection of ensemble members.

    Always seeds with the top-ranked eligible candidate (candidates[0]).
    At each slot evaluates ALL remaining candidates and picks the one that
    maximises portfolio_score — not just the next by rank.

    Correlation filter (fail-closed when corr_meta_lookup is provided):
        When corr_meta_lookup is supplied (keyed by both (A,B) and (B,A)),
        a pair passes only when:
          - metadata exists in the lookup, AND
          - status is reliable (OK / CONSTANT_*), AND
          - corr < corr_thr
        Any other outcome (missing, insufficient overlap, NaN) → reject.
        This prevents silently accepting pairs with unknown correlation.

        When corr_meta_lookup is None (legacy / test mode), falls back to
        the old matrix-based check: missing entry → silent pass (skip filter).

    Overlap / phase filters (F-11 fail-closed):
        When overlap_filter or phase_filter is enabled, a pair with no entry
        in the respective matrix is REJECTED (fail-closed), matching the
        behaviour of _subset_search.  Previously greedy used fail-open
        (missing entry → silent pass), creating inconsistent semantics between
        the two selection algorithms.

    Stability gate (pre-filter):
        If bucket_stability_map is provided (non-empty), candidates whose
        bucket_stability_score is below min_bucket_stability_for_ensemble
        are excluded before the greedy loop begins.  Candidates with no
        entry in bucket_stability_map are treated as unstable and excluded
        with a WARNING — this indicates a wiring problem, not a silent pass.
        If bucket_stability_map is empty the gate is skipped entirely.
    """
    if not candidates:
        return []

    max_models       = int(config.get("max_models", 3))
    corr_thr         = float(config.get("corr_threshold", 0.6))
    lambda_penalty   = float(config.get("lambda_penalty", 0.3))
    overlap_enabled  = bool(config.get("overlap_filter", True))
    overlap_thr      = float(config.get("overlap_threshold", 0.6))
    phase_enabled    = bool(config.get("phase_filter", True))
    phase_thr        = float(config.get("phase_threshold", 0.75))
    min_stab         = float(config.get("min_bucket_stability_for_ensemble", 0.35))
    stability_weight = float(config.get("stability_weight", 0.05))

    # --- Stability hard gate (pre-selection filter) ---
    if bucket_stability_map:
        filtered: List[Tuple[int, float]] = []
        for c in candidates:
            stab = bucket_stability_map.get(c)
            if stab is None:
                logger.warning(
                    "Stability gate: candidate %s has no bucket_stability_score "
                    "— excluding from ensemble selection (treated as unstable). "
                    "Check that atr_bucket/mult_bucket_ticks columns are present "
                    "in canonical_df and step_winners_df is non-empty.",
                    _label(c),
                )
                continue
            if stab < min_stab:
                continue
            filtered.append(c)
        if not filtered:
            logger.warning(
                "Stability gate: all %d candidates excluded "
                "(min_bucket_stability_for_ensemble=%.3f). "
                "Returning empty ensemble.",
                len(candidates),
                min_stab,
            )
            return []
        candidates = filtered
    else:
        logger.info(
            "Stability gate: bucket_stability_map is empty — "
            "skipping stability filtering for this run."
        )

    ensemble: List[Tuple[int, float]] = [candidates[0]]
    remaining = list(candidates[1:])

    while len(ensemble) < max_models and remaining:
        best_key: Optional[Tuple[int, float]] = None
        best_ps = float("-inf")

        for cand in remaining:
            # Hard filters against every existing ensemble member
            passes = True
            reject_reason: Optional[str] = None
            for member in ensemble:
                la, lb = _label(cand), _label(member)

                # Correlation filter
                if corr_meta_lookup is not None:
                    # Fail-closed: missing or unreliable → reject
                    pair_meta = corr_meta_lookup.get((la, lb))
                    if pair_meta is None:
                        logger.warning(
                            "Greedy select: no correlation metadata for pair "
                            "(%s, %s) — rejecting (fail-closed).", la, lb,
                        )
                        passes = False
                        reject_reason = RejectReason.NO_CORR_META
                        break
                    if not pair_meta.is_reliable:
                        passes = False
                        reject_reason = RejectReason.INSUFFICIENT_OVERLAP
                        break
                    if pair_meta.corr >= corr_thr:
                        passes = False
                        reject_reason = RejectReason.CORR_TOO_HIGH
                        break
                else:
                    # Legacy fallback: use raw matrix (missing entry → silent pass)
                    if la in corr_matrix.index and lb in corr_matrix.columns:
                        if float(corr_matrix.loc[la, lb]) >= corr_thr:
                            passes = False
                            reject_reason = RejectReason.CORR_TOO_HIGH
                            break

                # Trade overlap filter — F-11 fail-closed: missing entry → reject
                if overlap_enabled:
                    if la not in overlap_matrix.index or lb not in overlap_matrix.columns:
                        logger.warning(
                            "Greedy select: no overlap data for pair (%s, %s) "
                            "— rejecting (fail-closed).", la, lb,
                        )
                        passes = False
                        reject_reason = RejectReason.OVERLAP_TOO_HIGH
                        break
                    if float(overlap_matrix.loc[la, lb]) >= overlap_thr:
                        passes = False
                        reject_reason = RejectReason.OVERLAP_TOO_HIGH
                        break

                # Signal phase filter — F-11 fail-closed: missing entry → reject
                if phase_enabled:
                    if la not in phase_matrix.index or lb not in phase_matrix.columns:
                        logger.warning(
                            "Greedy select: no phase data for pair (%s, %s) "
                            "— rejecting (fail-closed).", la, lb,
                        )
                        passes = False
                        reject_reason = RejectReason.PHASE_TOO_HIGH
                        break
                    if float(phase_matrix.loc[la, lb]) >= phase_thr:
                        passes = False
                        reject_reason = RejectReason.PHASE_TOO_HIGH
                        break

            if not passes:
                logger.debug(
                    "Greedy select: candidate %s rejected (reason=%s).",
                    _label(cand), reject_reason,
                )
                continue

            ps = _portfolio_score(
                ensemble, cand, score_map, corr_matrix, lambda_penalty, bucket_map,
                bucket_stability_map=bucket_stability_map,
                stability_weight=stability_weight,
                corr_thr=corr_thr,
            )
            if ps > best_ps:
                best_ps = ps
                best_key = cand

        if best_key is None:
            break  # no candidate passes all hard filters
        ensemble.append(best_key)
        remaining.remove(best_key)

    return ensemble


# ---------------------------------------------------------------------------
# Bucket representative filtering  (Phase 3.7)
# ---------------------------------------------------------------------------

def _filter_to_representatives(
    valid_keys: List[Tuple[int, float]],
    bucket_map: Optional[Dict[Tuple[int, float], Tuple[int, int]]],
    score_map: Dict[Tuple[int, float], float],
    div_cfg: Dict[str, Any],
) -> List[Tuple[int, float]]:
    """Filter valid_keys to at most K representatives per bucket.

    Parameters
    ----------
    valid_keys : list
        Candidate keys ordered by canonical_rank ascending (rank 1 = best).
    bucket_map : dict or None
        key -> (atr_bucket, mult_bucket_ticks).  May be None when bucket
        columns are absent from canonical_df.
    score_map : dict
        key -> final_score (or robust_score fallback).
    div_cfg : dict
        Diversification config block.

    Returns
    -------
    list
        Filtered list of keys.  When ``representative_mode`` is ``"off"``
        (default) the input list is returned unchanged.
    """
    mode = div_cfg.get("representative_mode", "off")
    if mode == "off":
        return valid_keys

    k_per_bucket   = int(div_cfg.get("representatives_per_bucket", 1))
    rank_basis     = div_cfg.get("representative_rank_basis", "canonical_rank")
    preserve_order = bool(div_cfg.get("preserve_global_order", True))

    # Group keys by bucket; keys with no bucket_map entry get a unique singleton
    bucket_groups: Dict[Any, List[Tuple[int, float]]] = {}
    for idx, key in enumerate(valid_keys):
        if bucket_map is not None and key in bucket_map:
            bkt = bucket_map[key]
        else:
            logger.warning(
                "Representative filter: candidate %s has no bucket mapping "
                "— treated as unique bucket.",
                _label(key),
            )
            bkt = ("__singleton__", key)  # unique per key
        bucket_groups.setdefault(bkt, []).append(key)

    # Within each bucket, keep top-K by rank_basis
    selected: List[Tuple[int, float]] = []
    for bkt, keys_in_bucket in bucket_groups.items():
        if rank_basis == "final_score":
            # Higher score = better → sort descending
            sorted_keys = sorted(
                keys_in_bucket,
                key=lambda k: score_map.get(k, 0.0),
                reverse=True,
            )
        else:
            # "canonical_rank": position in valid_keys = rank (ascending)
            rank_lookup = {k: i for i, k in enumerate(valid_keys)}
            sorted_keys = sorted(
                keys_in_bucket,
                key=lambda k: rank_lookup.get(k, len(valid_keys)),
            )
        selected.extend(sorted_keys[:k_per_bucket])

    if preserve_order:
        # Restore original valid_keys order (canonical_rank ascending)
        selected_set = set(selected)
        selected = [k for k in valid_keys if k in selected_set]

    return selected


# ---------------------------------------------------------------------------
# Subset portfolio score  (used by _subset_search)
# ---------------------------------------------------------------------------

def _subset_portfolio_score(
    subset_keys: List[Tuple[int, float]],
    score_map: Dict[Tuple[int, float], float],
    corr_matrix: pd.DataFrame,
    lambda_penalty: float,
    bucket_map: Optional[Dict[Tuple[int, float], Tuple[int, int]]],
    bucket_stability_map: Optional[Dict[Tuple[int, float], float]] = None,
    stability_weight: float = 0.0,
    corr_thr: float = 0.6,
) -> float:
    """Score a fixed subset S for global subset search.

    Formula
    -------
    subset_score = mean(score_map[k] for k in S)
                 - lambda_penalty * mean(corr(ki, kj) for all i < j)
                 + bucket_diversity_bonus
                 + stability_bonus

    Correlation uses raw Pearson (NOT abs) — negative correlation reduces
    penalty, consistent with _portfolio_score.  This is intentional: a
    negatively-correlated pair is genuinely better for diversification and
    should score higher.
    Note the deliberate asymmetry with the hard rejection gate in
    _subset_search: rejection uses ``pair_meta.corr >= corr_thr`` (also raw,
    not abs), so a pair with corr = -0.9 passes the gate AND contributes a
    negative penalty term that improves the score.  If you want to reject
    strongly anti-correlated pairs as well, apply ``abs(corr) >= corr_thr``
    in the rejection check — but that is a separate policy decision.
    NaN / missing correlation → corr_thr as conservative fallback.

    bucket_diversity_bonus = 0.01 * (distinct_buckets - 1) / max(n - 1, 1)
    stability_bonus = stability_weight * mean(bucket_stability_map[k] for k in S)
    """
    n = len(subset_keys)
    if n == 0:
        return float("-inf")

    mean_score = float(np.mean([score_map.get(k, 0.0) for k in subset_keys]))

    # Pairwise correlation penalty
    pairs = [
        (subset_keys[i], subset_keys[j])
        for i in range(n)
        for j in range(i + 1, n)
    ]
    if pairs:
        corrs = []
        for ka, kb in pairs:
            la, lb = _label(ka), _label(kb)
            if la in corr_matrix.index and lb in corr_matrix.columns:
                c = float(corr_matrix.loc[la, lb])
                corrs.append(corr_thr if np.isnan(c) else c)
            else:
                corrs.append(corr_thr)
        penalty = lambda_penalty * float(np.mean(corrs))
    else:
        penalty = 0.0

    # Bucket diversity bonus
    bonus = 0.0
    if bucket_map is not None and n > 1:
        distinct = len({bucket_map[k] for k in subset_keys if k in bucket_map})
        bonus = 0.01 * (distinct - 1) / max(n - 1, 1)

    # Stability bonus
    stability_bonus = 0.0
    if bucket_stability_map is not None and stability_weight > 0:
        stab_vals = [bucket_stability_map.get(k, 0.0) for k in subset_keys]
        stability_bonus = stability_weight * float(np.mean(stab_vals))

    return mean_score - penalty + bonus + stability_bonus


# ---------------------------------------------------------------------------
# Global subset search  (Phase 4 alternative to greedy)
# ---------------------------------------------------------------------------

def _subset_search(
    candidates: List[Tuple[int, float]],
    score_map: Dict[Tuple[int, float], float],
    corr_matrix: pd.DataFrame,
    overlap_matrix: pd.DataFrame,
    phase_matrix: pd.DataFrame,
    div_cfg: Dict[str, Any],
    bucket_map: Optional[Dict[Tuple[int, float], Tuple[int, int]]],
    bucket_stability_map: Optional[Dict[Tuple[int, float], float]],
    corr_meta_lookup: Optional[Dict[Tuple[str, str], PairwiseCorrelationResult]],
) -> Tuple[List[Tuple[int, float]], int, float]:
    """Exhaustive subset search for best diversified pair or triple.

    Returns
    -------
    (ensemble_keys, subsets_evaluated, best_score)
        ensemble_keys : selected subset (may be empty if all subsets rejected)
        subsets_evaluated : total number of subsets scored
        best_score : score of the winning subset (or NaN if none passed)
    """
    import itertools

    mode         = div_cfg.get("subset_search_mode", "pair_triple")
    max_cands    = int(div_cfg.get("subset_search_max_candidates", 12))
    corr_thr     = float(div_cfg.get("corr_threshold", 0.6))
    lambda_pen   = float(div_cfg.get("lambda_penalty", 0.3))
    overlap_en   = bool(div_cfg.get("overlap_filter", True))
    overlap_thr  = float(div_cfg.get("overlap_threshold", 0.6))
    phase_en     = bool(div_cfg.get("phase_filter", True))
    phase_thr    = float(div_cfg.get("phase_threshold", 0.75))
    min_stab     = float(div_cfg.get("min_bucket_stability_for_ensemble", 0.35))
    stab_weight  = float(div_cfg.get("stability_weight", 0.05))

    # Truncate to max_cands by existing ranking order
    pool = candidates[:max_cands]

    # Determine subset sizes
    sizes = [2] if mode == "pair" else [2, 3]

    best_keys:  List[Tuple[int, float]] = []
    best_score: float = float("-inf")
    n_evaluated = 0

    for size in sizes:
        for subset in itertools.combinations(pool, size):
            subset_list = list(subset)
            # Hard filters — fail-closed for all pairs within subset
            passed = True
            for i in range(len(subset_list)):
                if not passed:
                    break
                for j in range(i + 1, len(subset_list)):
                    ka, kb = subset_list[i], subset_list[j]
                    la, lb = _label(ka), _label(kb)

                    # Correlation: fail-closed
                    if corr_meta_lookup is not None:
                        pair_meta = corr_meta_lookup.get((la, lb))
                        if pair_meta is None:
                            passed = False
                            break
                        if not pair_meta.is_reliable:
                            passed = False
                            break
                        if pair_meta.corr >= corr_thr:
                            passed = False
                            break
                    else:
                        if la in corr_matrix.index and lb in corr_matrix.columns:
                            if float(corr_matrix.loc[la, lb]) >= corr_thr:
                                passed = False
                                break

                    # Overlap: fail-closed when filter enabled
                    if overlap_en:
                        if la not in overlap_matrix.index or lb not in overlap_matrix.columns:
                            passed = False
                            break
                        if float(overlap_matrix.loc[la, lb]) >= overlap_thr:
                            passed = False
                            break

                    # Phase: fail-closed when filter enabled
                    if phase_en:
                        if la not in phase_matrix.index or lb not in phase_matrix.columns:
                            passed = False
                            break
                        if float(phase_matrix.loc[la, lb]) >= phase_thr:
                            passed = False
                            break

            if not passed:
                continue

            # Stability gate: every member must meet min_bucket_stability
            if bucket_stability_map:
                for k in subset_list:
                    stab = bucket_stability_map.get(k)
                    if stab is None or stab < min_stab:
                        passed = False
                        break

            if not passed:
                continue

            n_evaluated += 1
            score = _subset_portfolio_score(
                subset_list, score_map, corr_matrix, lambda_pen,
                bucket_map, bucket_stability_map, stab_weight, corr_thr,
            )
            if score > best_score:
                best_score = score
                best_keys = subset_list

    if not best_keys:
        logger.warning(
            "Subset search: no subset passed all hard filters "
            "(pool=%d, sizes=%s). Returning empty ensemble.",
            len(pool), sizes,
        )

    return best_keys, n_evaluated, best_score


# ---------------------------------------------------------------------------
# Step winners table
# ---------------------------------------------------------------------------

def _build_step_winners_table(
    result: Any,
    candidate_keys: List[Tuple[int, float]],
) -> pd.DataFrame:
    """Build per-step OOS pnl matrix with win stats.

    Rows = candidate parameter sets.
    Columns = Step0..StepK + wins_count + win_steps + top3_count.
    Values = oos_sum_pnl_pct for that (param, step) combination.
    """
    n_steps = len(result.steps)
    step_cols = [f"Step{i}" for i in range(n_steps)]

    # Build lookup: key -> step_index -> oos_sum_pnl_pct
    data: Dict[Tuple[int, float], Dict[int, float]] = {k: {} for k in candidate_keys}
    for step_idx, step in enumerate(result.steps):
        for entry in step.top_entries:
            key = _normalize_param_key(entry.atr_period, entry.multiplier)
            if key in data and entry.oos_sum_pnl_pct is not None:
                data[key][step_idx] = float(entry.oos_sum_pnl_pct)

    rows = []
    for key in candidate_keys:
        row: Dict[str, Any] = {"param": _label(key)}
        pnl_by_step = data[key]
        for s in range(n_steps):
            row[f"Step{s}"] = pnl_by_step.get(s, float("nan"))
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("param")

    # Win stats: per step, which candidate had the best pnl?
    wins_count = {k: 0 for k in candidate_keys}
    win_steps_list: Dict[Tuple[int, float], List[int]] = {k: [] for k in candidate_keys}
    top3_count = {k: 0 for k in candidate_keys}

    for s in range(n_steps):
        col = f"Step{s}"
        if col not in df.columns:
            continue
        step_vals = df[col].dropna()
        if step_vals.empty:
            continue
        sorted_vals = step_vals.sort_values(ascending=False)
        winner_label = sorted_vals.index[0]
        top3_labels = set(sorted_vals.index[:3])

        for key in candidate_keys:
            lbl = _label(key)
            if lbl == winner_label:
                wins_count[key] += 1
                win_steps_list[key].append(s)
            if lbl in top3_labels:
                top3_count[key] += 1

    df["wins_count"] = [wins_count[k] for k in candidate_keys]
    df["win_steps"]  = [",".join(str(s) for s in win_steps_list[k]) for k in candidate_keys]
    df["top3_count"] = [top3_count[k] for k in candidate_keys]

    return df.reset_index()


# ---------------------------------------------------------------------------
# Bucket aggregated step performance table
# ---------------------------------------------------------------------------

def _build_bucket_aggregated_table(
    step_winners_df: pd.DataFrame,
    candidates_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build bucket-aggregated step performance table.

    Groups parameters by (atr_bucket, mult_bucket_ticks) and computes the
    median oos_sum_pnl_pct across all parameters in each bucket for each step.

    Columns: bucket_param, bucket_size, bucket_stability_score,
             Step0..StepN, wins_count, win_steps, top3_count
    """
    if step_winners_df.empty or candidates_df.empty:
        return pd.DataFrame()

    # Detect step columns from step_winners_df
    step_cols = [c for c in step_winners_df.columns if c.startswith("Step")]
    if not step_cols:
        return pd.DataFrame()

    total_steps = len(step_cols)

    # Build param -> (atr_bucket, mult_bucket_ticks) mapping from candidates_df.
    # candidates_df may be the full canonical_df which contains metadata rows
    # (canonical_rank is a non-numeric string) — skip those defensively.
    param_to_bucket: Dict[str, Tuple[Any, Any]] = {}
    bucket_size_map: Dict[Tuple[Any, Any], int] = {}
    for _, row in candidates_df.iterrows():
        atr_period = row.get("atr_period")
        multiplier = row.get("multiplier")
        atr_bucket = row.get("atr_bucket")
        mult_bucket = row.get("mult_bucket_ticks")
        # Skip metadata / non-numeric rows
        try:
            atr_int  = int(float(atr_period))
            mult_f   = round(float(multiplier), 6)
        except (TypeError, ValueError):
            continue
        # Skip rows without valid bucket info
        try:
            atr_b_val  = None if (atr_bucket is None or pd.isna(atr_bucket)) else atr_bucket
            mult_b_val = None if (mult_bucket is None or pd.isna(mult_bucket)) else mult_bucket
        except (TypeError, ValueError):
            atr_b_val, mult_b_val = None, None
        if atr_b_val is None or mult_b_val is None:
            continue
        lbl = f"atr={atr_int}_mult={mult_f}"
        param_to_bucket[lbl] = (atr_b_val, mult_b_val)
        key = (atr_b_val, mult_b_val)
        bucket_size_map[key] = bucket_size_map.get(key, 0) + 1

    # Attach bucket info to step_winners_df rows
    sw = step_winners_df.copy()
    sw["_atr_bucket"] = sw["param"].map(lambda p: param_to_bucket.get(p, (None, None))[0])
    sw["_mult_bucket"] = sw["param"].map(lambda p: param_to_bucket.get(p, (None, None))[1])

    # Drop rows with no bucket assignment
    sw = sw.dropna(subset=["_atr_bucket", "_mult_bucket"])
    if sw.empty:
        return pd.DataFrame()

    # Compute median per bucket per step
    bucket_groups = sw.groupby(["_atr_bucket", "_mult_bucket"])
    bucket_keys = list(bucket_groups.groups.keys())

    bucket_rows: List[Dict[str, Any]] = []
    for bk in bucket_keys:
        atr_b, mult_b = bk
        group = bucket_groups.get_group(bk)
        row: Dict[str, Any] = {
            "bucket_param": _canonical_format_bucket_label(
                int(atr_b), int(mult_b), DEFAULT_ATR_BUCKET_STEP, DEFAULT_MULT_BUCKET_STEP,
            ),
            "_atr_bucket": atr_b,
            "_mult_bucket": mult_b,
        }
        for col in step_cols:
            vals = group[col].dropna()
            row[col] = float(np.median(vals)) if not vals.empty else float("nan")
        bucket_rows.append(row)

    if not bucket_rows:
        return pd.DataFrame()

    bdf = pd.DataFrame(bucket_rows).set_index("bucket_param")

    # Compute win stats across buckets
    wins_count: Dict[str, int] = {r["bucket_param"]: 0 for r in bucket_rows}
    win_steps_list: Dict[str, List[int]] = {r["bucket_param"]: [] for r in bucket_rows}
    top3_count: Dict[str, int] = {r["bucket_param"]: 0 for r in bucket_rows}

    for col in step_cols:
        step_idx = int(col.replace("Step", ""))
        step_vals = bdf[col].dropna()
        if step_vals.empty:
            continue
        sorted_vals = step_vals.sort_values(ascending=False)
        winner_label = sorted_vals.index[0]
        top3_labels = set(sorted_vals.index[:3])

        for lbl in bdf.index:
            if pd.isna(bdf.loc[lbl, col]):
                continue
            if lbl == winner_label:
                wins_count[lbl] += 1
                win_steps_list[lbl].append(step_idx)
            if lbl in top3_labels:
                top3_count[lbl] += 1

    bdf["wins_count"] = [wins_count[lbl] for lbl in bdf.index]
    bdf["win_steps"] = [",".join(str(s) for s in win_steps_list[lbl]) for lbl in bdf.index]
    bdf["top3_count"] = [top3_count[lbl] for lbl in bdf.index]

    # Stability metrics: above_median_count, above_median_ratio, dominance_score
    if step_cols:
        # F-12: Use true median across all individual candidates per step,
        # not median-of-bucket-medians.  sw contains one row per candidate per
        # step, so sw[col].dropna() gives all individual values for that step.
        step_medians = pd.Series(
            {col: float(np.median(sw[col].dropna())) if not sw[col].dropna().empty else float("nan")
             for col in step_cols},
            dtype=float,
        )
        above_matrix = bdf[step_cols].ge(step_medians) & bdf[step_cols].notna()
        bdf["above_median_count"] = above_matrix.sum(axis=1).astype(int)
        # F-13: Use valid_steps (steps where bucket is present) as denominator
        # instead of total_steps to avoid double-penalty for absent steps.
        # Absence is already penalised in presence_ratio; using total_steps here
        # penalises it a second time in above_median_ratio.
        presence_counts = bdf[step_cols].notna().sum(axis=1)
        bdf["above_median_ratio"] = [
            float(a / max(int(p), 1))
            for a, p in zip(bdf["above_median_count"], presence_counts)
        ]
        percentiles = bdf[step_cols].rank(pct=True)
        bdf["dominance_score"] = percentiles.mean(axis=1)
        # presence_count: steps where bucket has a non-NaN value
        presence_matrix = bdf[step_cols].notna()
        bdf["presence_count"] = presence_matrix.sum(axis=1).astype(int)
        # above_median_ratio_present: presence-denominated diagnostic (not used in scoring)
        bdf["above_median_ratio_present"] = [
            float(a / p) if p > 0 else 0.0
            for a, p in zip(
                bdf["above_median_count"].tolist(),
                bdf["presence_count"].tolist(),
            )
        ]
    else:
        bdf["above_median_count"] = 0
        bdf["above_median_ratio"] = 0.0
        bdf["dominance_score"] = np.nan
        bdf["presence_count"] = 0
        bdf["above_median_ratio_present"] = 0.0

    # bucket_size: total candidates in the bucket (from full candidates_df)
    bdf["bucket_size"] = [
        bucket_size_map.get((bdf.loc[lbl, "_atr_bucket"], bdf.loc[lbl, "_mult_bucket"]), 0)
        for lbl in bdf.index
    ]

    # bucket_stability_score: 0.6 * presence_ratio + 0.4 * above_median_ratio
    # F-13: above_median_ratio now uses valid_steps as denominator (presence-denominated),
    # so absence is only penalised once (in presence_ratio).
    stability_scores: List[float] = []
    for lbl in bdf.index:
        valid_steps = int(sum(1 for col in step_cols if not pd.isna(bdf.loc[lbl, col])))
        above_med = int(bdf.loc[lbl, "above_median_count"])
        if total_steps > 0:
            presence_ratio = valid_steps / total_steps
            # F-13: denominator = valid_steps, not total_steps
            above_median_ratio = above_med / max(valid_steps, 1)
            score = 0.6 * presence_ratio + 0.4 * above_median_ratio
        else:
            score = 0.0
        stability_scores.append(round(score, 6))
    bdf["bucket_stability_score"] = stability_scores

    # Rename internal helpers to public columns for heatmap use
    bdf = bdf.rename(columns={"_atr_bucket": "atr_bucket", "_mult_bucket": "mult_bucket_ticks"})

    # Add human-readable display labels (display only — downstream logic uses atr_bucket/mult_bucket_ticks)
    bdf["atr_range_label"] = bdf["atr_bucket"].apply(
        lambda v: _canonical_format_atr_range(int(v), DEFAULT_ATR_BUCKET_STEP) if pd.notna(v) else ""
    )
    bdf["mult_range_label"] = bdf["mult_bucket_ticks"].apply(
        lambda v: _canonical_format_mult_range(int(v), DEFAULT_MULT_BUCKET_STEP) if pd.notna(v) else ""
    )
    bdf["bucket_range"] = bdf.apply(
        lambda r: _canonical_format_bucket_label(
            int(r["atr_bucket"]), int(r["mult_bucket_ticks"]),
            DEFAULT_ATR_BUCKET_STEP, DEFAULT_MULT_BUCKET_STEP,
        )
        if pd.notna(r["atr_bucket"]) and pd.notna(r["mult_bucket_ticks"]) else "",
        axis=1,
    )

    # bucket_key: stable surrogate join key — invariant I-4/I-5
    bdf["bucket_key"] = bdf.apply(
        lambda r: f"{int(r['atr_bucket'])}_{int(r['mult_bucket_ticks'])}"
        if pd.notna(r["atr_bucket"]) and pd.notna(r["mult_bucket_ticks"]) else "",
        axis=1,
    )

    # Final column order — atr_bucket/mult_bucket_ticks kept for downstream heatmap;
    # bucket_range placed early for readability in Excel
    result_df = bdf.reset_index()
    ordered_cols = (
        ["bucket_param", "bucket_range", "bucket_key", "atr_bucket", "mult_bucket_ticks",
         "atr_range_label", "mult_range_label",
         "bucket_size", "bucket_stability_score"]
        + step_cols
        + ["wins_count", "win_steps", "top3_count",
           "above_median_count", "above_median_ratio",
           "presence_count", "above_median_ratio_present",
           "dominance_score"]
    )
    existing = [c for c in ordered_cols if c in result_df.columns]
    return result_df[existing]


# ---------------------------------------------------------------------------
# Full bucket step matrix (ALL buckets from config × step top-100 presence)
# ---------------------------------------------------------------------------

def _build_full_bucket_step_matrix(
    result: Any,
    candidates_df: pd.DataFrame,
    config: Dict[str, Any],
    *,
    atr_bucket_step: Optional[int] = None,
    mult_bucket_step: Optional[float] = None,
) -> pd.DataFrame:
    """Build a COMPLETE bucket × step matrix using ALL buckets from config ranges.

    Unlike _build_bucket_aggregated_table (which only covers buckets present in
    the winners subset), this function:
    - Generates every (atr_bucket, mult_bucket_ticks) combination implied by
      the optimization search space and bucket step sizes from config.
    - For each step, scans ALL top_k_export entries (not just top-N candidates).
    - Cell value = max(oos_sum_pnl_pct) among entries of that bucket in that
      step's top-100 (NaN when bucket is absent from the step).

    Bucket assignment uses the same formula as _apply_param_buckets (single
    source of truth):
        atr_bucket        = round(atr_period / atr_step) * atr_step
        mult_bucket_ticks = round(multiplier / mult_step)

    Parameters
    ----------
    result : WalkForwardResult
        Walk-forward result with .steps[i].top_entries.
    candidates_df : pd.DataFrame
        Full canonical_df (used for bucket_size counting only).
        Must contain atr_period, multiplier columns.
    config : dict
        Full run config. Reads:
          optimization.atr_period_range
          optimization.multiplier_range
          optimization.multiplier_step
          walk_forward.consensus.atr_bucket_step
          walk_forward.consensus.mult_bucket_step

    Returns
    -------
    pd.DataFrame with columns:
        bucket_param, atr_bucket, mult_bucket_ticks, bucket_size,
        Step0..StepN,
        bucket_presence_steps, mean_oos_pnl,
        wins_count, win_steps, top3_count,
        above_median_count, above_median_ratio,
        bucket_stability_score, dominance_score
    """
    # Import here to avoid circular imports (diversification is a leaf module,
    # aggregation is a sibling — safe to import at function scope).
    from supertrend_optimizer.scoring.aggregation import _apply_param_buckets

    # ------------------------------------------------------------------ #
    # 1. Read config parameters                                           #
    # ------------------------------------------------------------------ #
    opt_cfg  = config.get("optimization", {})
    wf_cfg   = config.get("walk_forward", {})
    cons_cfg = wf_cfg.get("consensus", {})

    atr_range     = opt_cfg.get("atr_period_range", [10, 50])
    mult_range    = opt_cfg.get("multiplier_range", [1.8, 3.5])
    mult_step_opt = float(opt_cfg.get("multiplier_step", 0.1))

    if atr_bucket_step is None:
        atr_bucket_step = int(cons_cfg.get("atr_bucket_step", DEFAULT_ATR_BUCKET_STEP))
    if mult_bucket_step is None:
        mult_bucket_step = float(cons_cfg.get("mult_bucket_step", DEFAULT_MULT_BUCKET_STEP))
    min_buckets_for_median = int(cons_cfg.get("min_buckets_for_median", 5))

    atr_min  = int(atr_range[0])
    atr_max  = int(atr_range[1])
    mult_min = float(mult_range[0])
    mult_max = float(mult_range[1])

    # ------------------------------------------------------------------ #
    # 2. Generate full grid of (atr_bucket, mult_bucket_ticks)            #
    #    Uses the same formula as _apply_param_buckets:                   #
    #        atr_bucket        = round(atr / atr_step) * atr_step        #
    #        mult_bucket_ticks = round(mult / mult_step)                  #
    # ------------------------------------------------------------------ #
    atr_buckets_set: set = set()
    for atr in range(atr_min, atr_max + 1):
        # Identical to: ((pd.Series([atr]) / atr_bucket_step).round()
        #                * atr_bucket_step).astype(int)
        b = int(round(atr / atr_bucket_step) * atr_bucket_step)
        atr_buckets_set.add(b)
    atr_buckets = sorted(atr_buckets_set)

    # Step through multiplier range using integer arithmetic to avoid drift
    mult_ticks_set: set = set()
    n_mult_steps = int(round((mult_max - mult_min) / mult_step_opt)) + 1
    for i in range(n_mult_steps):
        mv = round(mult_min + i * mult_step_opt, 10)
        if mv > mult_max + 1e-9:
            break
        # Identical to: (pd.Series([mv]) / mult_bucket_step).round().astype("Int64")
        t = int(round(mv / mult_bucket_step))
        mult_ticks_set.add(t)
    mult_ticks = sorted(mult_ticks_set)

    if not atr_buckets or not mult_ticks:
        return pd.DataFrame()

    all_bucket_keys: List[Tuple[int, int]] = [
        (ab, mt) for ab in atr_buckets for mt in mult_ticks
    ]

    # ------------------------------------------------------------------ #
    # 3. Build bucket_size map from candidates_df                         #
    #    Uses _apply_param_buckets so labels are guaranteed identical.    #
    # ------------------------------------------------------------------ #
    bucket_size_map: Dict[Tuple[int, int], int] = {}
    if not candidates_df.empty and "atr_period" in candidates_df.columns:
        _cdf = candidates_df.copy()
        # Keep only rows with parseable atr_period / multiplier
        try:
            _cdf["atr_period"] = pd.to_numeric(_cdf["atr_period"], errors="coerce")
            _cdf["multiplier"]  = pd.to_numeric(_cdf["multiplier"],  errors="coerce")
            _cdf = _cdf.dropna(subset=["atr_period", "multiplier"])
            if not _cdf.empty:
                _cdf = _apply_param_buckets(
                    _cdf, atr_step=atr_bucket_step, mult_step=mult_bucket_step
                )
                for _, row in _cdf.iterrows():
                    try:
                        ab = int(row["atr_bucket"])
                        mt = int(row["mult_bucket_ticks"])
                    except (TypeError, ValueError):
                        continue
                    key = (ab, mt)
                    bucket_size_map[key] = bucket_size_map.get(key, 0) + 1
        except Exception:
            pass  # leave bucket_size_map empty; non-fatal

    # ------------------------------------------------------------------ #
    # 4. Build per-step per-bucket max(oos_sum_pnl_pct)                   #
    #    Uses same bucket formula → guaranteed consistent with grid.      #
    # ------------------------------------------------------------------ #
    n_steps  = len(result.steps) if result is not None else 0
    step_cols = [f"Step{i}" for i in range(n_steps)]

    # Accumulate all pnl values per (step, bucket) so we can take the max
    # safely without risking max() on an empty sequence.
    step_bucket_vals: List[Dict[Tuple[int, int], List[float]]] = [
        {} for _ in range(n_steps)
    ]

    if result is not None:
        for step_idx, step in enumerate(result.steps):
            for entry in step.top_entries:
                if entry.oos_sum_pnl_pct is None:
                    continue
                try:
                    atr_p = int(entry.atr_period)
                    mult_v = float(entry.multiplier)
                    pnl   = float(entry.oos_sum_pnl_pct)
                except (TypeError, ValueError):
                    continue
                # Same formula as _apply_param_buckets
                ab = int(round(atr_p / atr_bucket_step) * atr_bucket_step)
                mt = int(round(mult_v / mult_bucket_step))
                bk = (ab, mt)
                step_bucket_vals[step_idx].setdefault(bk, []).append(pnl)

    # ------------------------------------------------------------------ #
    # 5. Build main DataFrame rows                                        #
    # ------------------------------------------------------------------ #
    rows: List[Dict[str, Any]] = []
    for (ab, mt) in all_bucket_keys:
        row: Dict[str, Any] = {
            "bucket_param":      _canonical_format_bucket_label(ab, mt, atr_bucket_step, mult_bucket_step),
            "bucket_key":        f"{ab}_{mt}",
            "atr_bucket":        ab,
            "mult_bucket_ticks": mt,
            "bucket_size":       bucket_size_map.get((ab, mt), 0),
        }
        for s_idx, col in enumerate(step_cols):
            vals = step_bucket_vals[s_idx].get((ab, mt), [])
            # Safe max: only over non-empty lists (NaN entries already excluded above)
            row[col] = float(max(vals)) if vals else float("nan")
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    bdf = pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # 6. Compute per-row metrics                                          #
    # ------------------------------------------------------------------ #
    total_steps = n_steps

    # ── wins_count, win_steps, top3_count ─────────────────────────────
    wins_count:     List[int]        = [0] * len(bdf)
    win_steps_list: List[List[int]]  = [[] for _ in range(len(bdf))]
    top3_count:     List[int]        = [0] * len(bdf)

    for s_idx, col in enumerate(step_cols):
        if col not in bdf.columns:
            continue
        step_vals = pd.to_numeric(bdf[col], errors="coerce").dropna()
        if step_vals.empty:
            continue

        # Winner: row with the highest value (NaN already dropped)
        winner_idx = int(step_vals.idxmax())

        # Top-3: use rank(method="min") so ties share the same rank
        ranks = step_vals.rank(ascending=False, method="min")
        top3_idxs = set(ranks[ranks <= 3].index)

        for row_i in range(len(bdf)):
            v = bdf.at[row_i, col]
            if pd.isna(v):
                continue
            if row_i == winner_idx:
                wins_count[row_i] += 1
                win_steps_list[row_i].append(s_idx)
            if row_i in top3_idxs:
                top3_count[row_i] += 1

    # WF-style labels: "WF0,WF3,WF5" (consistent with other WF sheets)
    bdf["wins_count"] = wins_count
    bdf["win_steps"]  = [
        ",".join(f"WF{s}" for s in ws) for ws in win_steps_list
    ]
    bdf["top3_count"] = top3_count

    # ── bucket_presence_steps ─────────────────────────────────────────
    # Steps where the bucket was present in top-100 (non-NaN), regardless
    # of rank.  Uses same WF-style labels as win_steps.
    presence_steps_list: List[str] = []
    for row_i in range(len(bdf)):
        present = [
            f"WF{s_idx}"
            for s_idx, col in enumerate(step_cols)
            if col in bdf.columns and not pd.isna(bdf.at[row_i, col])
        ]
        presence_steps_list.append(",".join(present))
    bdf["bucket_presence_steps"] = presence_steps_list

    # ── mean_oos_pnl ──────────────────────────────────────────────────
    # Mean of non-NaN step values.  NaN when bucket is absent from all steps.
    if step_cols:
        step_data = bdf[step_cols].apply(pd.to_numeric, errors="coerce")
        bdf["mean_oos_pnl"] = step_data.mean(axis=1, skipna=True)
        # Rows with no valid step values → NaN (mean of empty = NaN by default)
    else:
        bdf["mean_oos_pnl"] = float("nan")

    # ── above_median_count, above_median_ratio ────────────────────────
    # min_buckets_for_median: steps with fewer non-NaN buckets than this
    # threshold are excluded from median comparison (step eligible_median_steps).
    #
    # TODO(phase2): above_median_ratio uses total_steps as denominator (legacy).
    # This means absence from a step penalises a bucket in both presence_ratio
    # and above_median_ratio (double-penalty).  Diagnostic columns provided:
    #   above_median_ratio_present  — denominator = presence_count
    #   above_median_ratio_eligible — denominator = eligible_median_steps_count
    # Changing the ranking denominator requires recalibrating bucket_stability_score
    # weights and is deferred to Phase 2.
    above_median_count: List[int] = [0] * len(bdf)
    eligible_median_steps: List[int] = [0] * len(bdf)

    for col in step_cols:
        if col not in bdf.columns:
            continue
        col_vals = pd.to_numeric(bdf[col], errors="coerce")
        non_nan  = col_vals.dropna()
        if non_nan.empty:
            continue
        if len(non_nan) < min_buckets_for_median:
            continue  # step has too few buckets for meaningful median comparison
        step_median = float(non_nan.median())
        for row_i in range(len(bdf)):
            v = col_vals.iloc[row_i]
            if not pd.isna(v):
                eligible_median_steps[row_i] += 1
                if float(v) >= step_median:
                    above_median_count[row_i] += 1

    bdf["above_median_count"] = above_median_count
    if total_steps > 0:
        bdf["above_median_ratio"] = [c / total_steps for c in above_median_count]
    else:
        bdf["above_median_ratio"] = 0.0

    # presence_count: steps where bucket has a non-NaN value
    presence_count_per_row: List[int] = [
        int(sum(
            1 for col in step_cols
            if col in bdf.columns and not pd.isna(bdf.at[row_i, col])
        ))
        for row_i in range(len(bdf))
    ]
    bdf["presence_count"] = presence_count_per_row
    # above_median_ratio_present: presence-denominated diagnostic (not used in scoring)
    bdf["above_median_ratio_present"] = [
        float(c / p) if p > 0 else 0.0
        for c, p in zip(above_median_count, presence_count_per_row)
    ]

    # eligible_median_steps_count: steps where bucket was present AND
    # the step had >= min_buckets_for_median non-NaN buckets.
    bdf["eligible_median_steps_count"] = eligible_median_steps
    bdf["above_median_ratio_eligible"] = [
        float(c / e) if e > 0 else 0.0
        for c, e in zip(above_median_count, eligible_median_steps)
    ]

    # ── bucket_stability_score ────────────────────────────────────────
    # TODO(phase2): above_median_ratio / total_steps here double-penalises
    # absent steps.  See above_median_ratio_present for the presence-
    # denominated variant and above_median_ratio_eligible for the eligible
    # variant.
    stability_scores: List[float] = []
    for row_i in range(len(bdf)):
        if total_steps > 0:
            valid_steps = int(sum(
                1 for col in step_cols
                if col in bdf.columns and not pd.isna(bdf.at[row_i, col])
            ))
            presence_ratio     = valid_steps / total_steps
            above_median_ratio = above_median_count[row_i] / total_steps
            score = 0.6 * presence_ratio + 0.4 * above_median_ratio
        else:
            score = 0.0
        stability_scores.append(round(score, 6))
    bdf["bucket_stability_score"] = stability_scores

    # ── dominance_score ───────────────────────────────────────────────
    dominance_scores: List[float] = []
    for row_i in range(len(bdf)):
        if total_steps > 0:
            valid_steps = int(sum(
                1 for col in step_cols
                if col in bdf.columns and not pd.isna(bdf.at[row_i, col])
            ))
            presence = valid_steps / total_steps
            top3_r   = top3_count[row_i] / total_steps
            wins_r   = wins_count[row_i] / total_steps
            d_score  = 0.4 * presence + 0.3 * top3_r + 0.3 * wins_r
        else:
            d_score = 0.0
        dominance_scores.append(round(d_score, 6))
    bdf["dominance_score"] = dominance_scores

    # ------------------------------------------------------------------ #
    # 7. Final column order                                               #
    # ------------------------------------------------------------------ #
    ordered_cols = (
        ["bucket_param", "bucket_key", "atr_bucket", "mult_bucket_ticks", "bucket_size"]
        + step_cols
        + ["bucket_presence_steps", "mean_oos_pnl",
           "wins_count", "win_steps", "top3_count",
           "above_median_count", "above_median_ratio",
           "presence_count", "above_median_ratio_present",
           "eligible_median_steps_count", "above_median_ratio_eligible",
           "bucket_stability_score", "dominance_score"]
    )
    existing = [c for c in ordered_cols if c in bdf.columns]
    return bdf[existing]


# ---------------------------------------------------------------------------
# Median-based bucket step matrix (companion to _build_full_bucket_step_matrix)
# ---------------------------------------------------------------------------

def _build_median_bucket_step_matrix(
    result: Any,
    candidates_df: pd.DataFrame,
    config: Dict[str, Any],
    *,
    atr_bucket_step: Optional[int] = None,
    mult_bucket_step: Optional[float] = None,
) -> pd.DataFrame:
    """Build a COMPLETE bucket × step matrix using MEDIAN aggregation.

    This is the median-based companion to _build_full_bucket_step_matrix.
    The winner-based sheet uses max(oos_sum_pnl_pct) per bucket per step;
    this function uses median(oos_sum_pnl_pct) instead — reflecting zone
    strength rather than the best local winner.

    Extra columns (not present in the winner-based sheet):
        std_bucket            — std-dev of all non-NaN step values across all
                                params in the bucket (population std, ddof=0).
        pct_params_positive_pnl — fraction of params whose mean observed OOS
                                  PnL (over steps where they appear in top-K)
                                  is > 0.  Conditional metric: only steps where
                                  the param was actually present in top-K are
                                  counted (no zero-fill for absent steps).

    dominance_score is renamed zone_dominance_score to distinguish it from
    the winner-based dominance_score on the BucketMatrix_Full sheet.
    Formula is identical: 0.4*presence + 0.3*top3_r + 0.3*wins_r, but
    computed from median step values.

    Parameters
    ----------
    result : WalkForwardResult
    candidates_df : pd.DataFrame
        Full canonical_df (used for bucket_size counting only).
    config : dict
        Full run config.

    Returns
    -------
    pd.DataFrame with columns:
        bucket_param, atr_bucket, mult_bucket_ticks, bucket_size,
        Step0..StepN,
        bucket_presence_steps, mean_oos_pnl, std_bucket,
        pct_params_positive_pnl,
        wins_count, win_steps, top3_count,
        above_median_count, above_median_ratio,
        bucket_stability_score, zone_dominance_score
    """
    from supertrend_optimizer.scoring.aggregation import _apply_param_buckets

    # ------------------------------------------------------------------ #
    # 1. Read config parameters (identical to winner-based function)      #
    # ------------------------------------------------------------------ #
    opt_cfg  = config.get("optimization", {})
    wf_cfg   = config.get("walk_forward", {})
    cons_cfg = wf_cfg.get("consensus", {})

    atr_range     = opt_cfg.get("atr_period_range", [10, 50])
    mult_range    = opt_cfg.get("multiplier_range", [1.8, 3.5])
    mult_step_opt = float(opt_cfg.get("multiplier_step", 0.1))

    if atr_bucket_step is None:
        atr_bucket_step = int(cons_cfg.get("atr_bucket_step", DEFAULT_ATR_BUCKET_STEP))
    if mult_bucket_step is None:
        mult_bucket_step = float(cons_cfg.get("mult_bucket_step", DEFAULT_MULT_BUCKET_STEP))
    min_buckets_for_median = int(cons_cfg.get("min_buckets_for_median", 5))

    atr_min  = int(atr_range[0])
    atr_max  = int(atr_range[1])
    mult_min = float(mult_range[0])
    mult_max = float(mult_range[1])

    # ------------------------------------------------------------------ #
    # 2. Generate full grid of (atr_bucket, mult_bucket_ticks)            #
    # ------------------------------------------------------------------ #
    atr_buckets_set: set = set()
    for atr in range(atr_min, atr_max + 1):
        b = int(round(atr / atr_bucket_step) * atr_bucket_step)
        atr_buckets_set.add(b)
    atr_buckets = sorted(atr_buckets_set)

    mult_ticks_set: set = set()
    n_mult_steps = int(round((mult_max - mult_min) / mult_step_opt)) + 1
    for i in range(n_mult_steps):
        mv = round(mult_min + i * mult_step_opt, 10)
        if mv > mult_max + 1e-9:
            break
        t = int(round(mv / mult_bucket_step))
        mult_ticks_set.add(t)
    mult_ticks = sorted(mult_ticks_set)

    if not atr_buckets or not mult_ticks:
        return pd.DataFrame()

    all_bucket_keys: List[Tuple[int, int]] = [
        (ab, mt) for ab in atr_buckets for mt in mult_ticks
    ]

    # ------------------------------------------------------------------ #
    # 3. Build bucket_size map from candidates_df                         #
    # ------------------------------------------------------------------ #
    bucket_size_map: Dict[Tuple[int, int], int] = {}
    if not candidates_df.empty and "atr_period" in candidates_df.columns:
        _cdf = candidates_df.copy()
        try:
            _cdf["atr_period"] = pd.to_numeric(_cdf["atr_period"], errors="coerce")
            _cdf["multiplier"]  = pd.to_numeric(_cdf["multiplier"],  errors="coerce")
            _cdf = _cdf.dropna(subset=["atr_period", "multiplier"])
            if not _cdf.empty:
                _cdf = _apply_param_buckets(
                    _cdf, atr_step=atr_bucket_step, mult_step=mult_bucket_step
                )
                for _, row in _cdf.iterrows():
                    try:
                        ab = int(row["atr_bucket"])
                        mt = int(row["mult_bucket_ticks"])
                    except (TypeError, ValueError):
                        continue
                    key = (ab, mt)
                    bucket_size_map[key] = bucket_size_map.get(key, 0) + 1
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 4. Accumulate per-step per-bucket ALL pnl values (for median)       #
    #    Also accumulate per-param per-step pnl (for pct_params_positive) #
    # ------------------------------------------------------------------ #
    n_steps   = len(result.steps) if result is not None else 0
    step_cols = [f"Step{i}" for i in range(n_steps)]

    # step_bucket_vals[step_idx][(ab, mt)] = [pnl, ...]
    step_bucket_vals: List[Dict[Tuple[int, int], List[float]]] = [
        {} for _ in range(n_steps)
    ]

    # param_bucket_step_vals[(ab, mt)][param_key][step_idx] = [pnl, ...]
    # Used to compute pct_params_positive_pnl per bucket.
    # param_key = (atr_period_int, mult_float_rounded)
    param_bucket_step_vals: Dict[
        Tuple[int, int],
        Dict[Tuple[int, float], Dict[int, List[float]]]
    ] = {}

    if result is not None:
        for step_idx, step in enumerate(result.steps):
            for entry in step.top_entries:
                if entry.oos_sum_pnl_pct is None:
                    continue
                try:
                    atr_p = int(entry.atr_period)
                    mult_v = float(entry.multiplier)
                    pnl   = float(entry.oos_sum_pnl_pct)
                except (TypeError, ValueError):
                    continue
                ab = int(round(atr_p / atr_bucket_step) * atr_bucket_step)
                mt = int(round(mult_v / mult_bucket_step))
                bk = (ab, mt)

                # Accumulate for median step value
                step_bucket_vals[step_idx].setdefault(bk, []).append(pnl)

                # Accumulate for pct_params_positive_pnl
                param_key = (atr_p, round(mult_v, 6))
                if bk not in param_bucket_step_vals:
                    param_bucket_step_vals[bk] = {}
                if param_key not in param_bucket_step_vals[bk]:
                    param_bucket_step_vals[bk][param_key] = {}
                param_bucket_step_vals[bk][param_key].setdefault(step_idx, []).append(pnl)

    # ------------------------------------------------------------------ #
    # 5. Build main DataFrame rows using MEDIAN                           #
    # ------------------------------------------------------------------ #
    rows: List[Dict[str, Any]] = []
    for (ab, mt) in all_bucket_keys:
        row: Dict[str, Any] = {
            "bucket_param":      _canonical_format_bucket_label(ab, mt, atr_bucket_step, mult_bucket_step),
            "bucket_key":        f"{ab}_{mt}",
            "atr_bucket":        ab,
            "mult_bucket_ticks": mt,
            "bucket_size":       bucket_size_map.get((ab, mt), 0),
        }
        for s_idx, col in enumerate(step_cols):
            vals = step_bucket_vals[s_idx].get((ab, mt), [])
            # MEDIAN of all non-NaN param values in this bucket on this step
            row[col] = float(np.median(vals)) if vals else float("nan")
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    bdf = pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # 6. Compute per-row metrics from median step values                  #
    # ------------------------------------------------------------------ #
    total_steps = n_steps

    # ── wins_count, win_steps, top3_count ─────────────────────────────
    wins_count:     List[int]       = [0] * len(bdf)
    win_steps_list: List[List[int]] = [[] for _ in range(len(bdf))]
    top3_count:     List[int]       = [0] * len(bdf)

    for s_idx, col in enumerate(step_cols):
        if col not in bdf.columns:
            continue
        step_vals = pd.to_numeric(bdf[col], errors="coerce").dropna()
        if step_vals.empty:
            continue
        winner_idx = int(step_vals.idxmax())
        ranks = step_vals.rank(ascending=False, method="min")
        top3_idxs = set(ranks[ranks <= 3].index)
        for row_i in range(len(bdf)):
            v = bdf.at[row_i, col]
            if pd.isna(v):
                continue
            if row_i == winner_idx:
                wins_count[row_i] += 1
                win_steps_list[row_i].append(s_idx)
            if row_i in top3_idxs:
                top3_count[row_i] += 1

    bdf["wins_count"] = wins_count
    bdf["win_steps"]  = [
        ",".join(f"WF{s}" for s in ws) for ws in win_steps_list
    ]
    bdf["top3_count"] = top3_count

    # ── bucket_presence_steps ─────────────────────────────────────────
    presence_steps_list: List[str] = []
    for row_i in range(len(bdf)):
        present = [
            f"WF{s_idx}"
            for s_idx, col in enumerate(step_cols)
            if col in bdf.columns and not pd.isna(bdf.at[row_i, col])
        ]
        presence_steps_list.append(",".join(present))
    bdf["bucket_presence_steps"] = presence_steps_list

    # ── mean_oos_pnl ──────────────────────────────────────────────────
    if step_cols:
        step_data = bdf[step_cols].apply(pd.to_numeric, errors="coerce")
        bdf["mean_oos_pnl"] = step_data.mean(axis=1, skipna=True)
    else:
        bdf["mean_oos_pnl"] = float("nan")

    # ── std_bucket ────────────────────────────────────────────────────
    # Population std of ALL non-NaN per-step pnl values across all params
    # in this bucket (all steps combined).
    std_bucket_vals: List[float] = []
    for row_i in range(len(bdf)):
        ab = int(bdf.at[row_i, "atr_bucket"])
        mt = int(bdf.at[row_i, "mult_bucket_ticks"])
        bk = (ab, mt)
        # Collect all raw pnl values across all steps for this bucket
        all_vals: List[float] = []
        for s_idx in range(n_steps):
            all_vals.extend(step_bucket_vals[s_idx].get(bk, []))
        arr = np.array(all_vals, dtype=float)
        arr = arr[~np.isnan(arr)]
        std_bucket_vals.append(float(np.std(arr, ddof=0)) if len(arr) > 0 else float("nan"))
    bdf["std_bucket"] = std_bucket_vals

    # ── pct_params_positive_pnl ───────────────────────────────────────
    # Conditional metric: among params that appeared in top-K inside this
    # bucket, what fraction has a positive mean OOS PnL over their observed
    # steps (only steps where the param was actually present in top-K are
    # counted — no zero-fill for absent steps).
    pct_positive_vals: List[float] = []
    for row_i in range(len(bdf)):
        ab = int(bdf.at[row_i, "atr_bucket"])
        mt = int(bdf.at[row_i, "mult_bucket_ticks"])
        bk = (ab, mt)
        param_map = param_bucket_step_vals.get(bk, {})
        valid_count = 0
        positive_count = 0
        for param_key, step_pnl_map in param_map.items():
            # Collect all observed pnl values for this param across steps
            observed: List[float] = []
            for step_pnls in step_pnl_map.values():
                observed.extend(step_pnls)
            obs_arr = np.array(observed, dtype=float)
            obs_arr = obs_arr[~np.isnan(obs_arr)]
            if len(obs_arr) == 0:
                continue  # no valid observations — exclude from denominator
            valid_count += 1
            if float(np.mean(obs_arr)) > 0.0:
                positive_count += 1
        if valid_count > 0:
            pct_positive_vals.append(positive_count / valid_count)
        else:
            pct_positive_vals.append(float("nan"))
    bdf["pct_params_positive_pnl"] = pct_positive_vals

    # ── above_median_count, above_median_ratio ────────────────────────
    # min_buckets_for_median: steps with fewer non-NaN buckets than this
    # threshold are excluded from median comparison (step eligible_median_steps).
    #
    # TODO(phase2): above_median_ratio uses total_steps as denominator (legacy).
    # This means absence from a step penalises a bucket in both presence_ratio
    # and above_median_ratio (double-penalty).  Diagnostic columns provided:
    #   above_median_ratio_present  — denominator = presence_count
    #   above_median_ratio_eligible — denominator = eligible_median_steps_count
    # Changing the ranking denominator requires recalibrating bucket_stability_score
    # weights and is deferred to Phase 2.
    above_median_count: List[int] = [0] * len(bdf)
    eligible_median_steps: List[int] = [0] * len(bdf)

    for col in step_cols:
        if col not in bdf.columns:
            continue
        col_vals = pd.to_numeric(bdf[col], errors="coerce")
        non_nan  = col_vals.dropna()
        if non_nan.empty:
            continue
        if len(non_nan) < min_buckets_for_median:
            continue  # step has too few buckets for meaningful median comparison
        step_median = float(non_nan.median())
        for row_i in range(len(bdf)):
            v = col_vals.iloc[row_i]
            if not pd.isna(v):
                eligible_median_steps[row_i] += 1
                if float(v) >= step_median:
                    above_median_count[row_i] += 1

    bdf["above_median_count"] = above_median_count
    if total_steps > 0:
        bdf["above_median_ratio"] = [c / total_steps for c in above_median_count]
    else:
        bdf["above_median_ratio"] = 0.0

    # presence_count: steps where bucket has a non-NaN value
    presence_count_per_row: List[int] = [
        int(sum(
            1 for col in step_cols
            if col in bdf.columns and not pd.isna(bdf.at[row_i, col])
        ))
        for row_i in range(len(bdf))
    ]
    bdf["presence_count"] = presence_count_per_row
    # above_median_ratio_present: presence-denominated diagnostic (not used in scoring)
    bdf["above_median_ratio_present"] = [
        float(c / p) if p > 0 else 0.0
        for c, p in zip(above_median_count, presence_count_per_row)
    ]

    # eligible_median_steps_count: steps where bucket was present AND
    # the step had >= min_buckets_for_median non-NaN buckets.
    bdf["eligible_median_steps_count"] = eligible_median_steps
    bdf["above_median_ratio_eligible"] = [
        float(c / e) if e > 0 else 0.0
        for c, e in zip(above_median_count, eligible_median_steps)
    ]

    # ── bucket_stability_score ────────────────────────────────────────
    # TODO(phase2): above_median_ratio / total_steps here double-penalises
    # absent steps.  See above_median_ratio_present for the presence-
    # denominated variant and above_median_ratio_eligible for the eligible
    # variant.
    stability_scores: List[float] = []
    for row_i in range(len(bdf)):
        if total_steps > 0:
            valid_steps = int(sum(
                1 for col in step_cols
                if col in bdf.columns and not pd.isna(bdf.at[row_i, col])
            ))
            presence_ratio     = valid_steps / total_steps
            above_med_ratio    = above_median_count[row_i] / total_steps
            score = 0.6 * presence_ratio + 0.4 * above_med_ratio
        else:
            score = 0.0
        stability_scores.append(round(score, 6))
    bdf["bucket_stability_score"] = stability_scores

    # ── zone_dominance_score ──────────────────────────────────────────
    # Same formula as dominance_score on the winner-based sheet, but computed
    # from median step values.  Renamed to zone_dominance_score to make the
    # semantic difference explicit: this reflects zone strength (median-based),
    # not winner-inside-bucket dominance.
    # Formula: 0.4 * presence + 0.3 * top3_r + 0.3 * wins_r
    zone_dominance_scores: List[float] = []
    for row_i in range(len(bdf)):
        if total_steps > 0:
            valid_steps = int(sum(
                1 for col in step_cols
                if col in bdf.columns and not pd.isna(bdf.at[row_i, col])
            ))
            presence = valid_steps / total_steps
            top3_r   = top3_count[row_i] / total_steps
            wins_r   = wins_count[row_i] / total_steps
            d_score  = 0.4 * presence + 0.3 * top3_r + 0.3 * wins_r
        else:
            d_score = 0.0
        zone_dominance_scores.append(round(d_score, 6))
    bdf["zone_dominance_score"] = zone_dominance_scores

    # ------------------------------------------------------------------ #
    # 7. Final column order                                               #
    # ------------------------------------------------------------------ #
    ordered_cols = (
        ["bucket_param", "bucket_key", "atr_bucket", "mult_bucket_ticks", "bucket_size"]
        + step_cols
        + ["bucket_presence_steps", "mean_oos_pnl", "std_bucket",
           "pct_params_positive_pnl",
           "wins_count", "win_steps", "top3_count",
           "above_median_count", "above_median_ratio",
           "presence_count", "above_median_ratio_present",
           "eligible_median_steps_count", "above_median_ratio_eligible",
           "bucket_stability_score", "zone_dominance_score"]
    )
    existing = [c for c in ordered_cols if c in bdf.columns]
    return bdf[existing]


# ---------------------------------------------------------------------------
# Surface bucket step matrix  (train-layer, full search-space coverage)
# ---------------------------------------------------------------------------

def _build_surface_bucket_step_matrix(
    all_step_trials: "pd.DataFrame",
    config: Dict[str, Any],
    wf_total_steps: int,
    reliable_fill_threshold: float = 0.5,
    min_reliable_ratio: float = 0.2,
    *,
    atr_bucket_step: Optional[int] = None,
    mult_bucket_step: Optional[float] = None,
) -> pd.DataFrame:
    """Build a bucket × WF-step matrix from *all* evaluated train trials.

    Unlike _build_full/median_bucket_step_matrix (which operate on top-k
    entries only), this function uses every trial row collected before
    top-k truncation, giving a true picture of bucket search-space coverage.

    ── Source semantics ────────────────────────────────────────────────────
    ``all_step_trials`` contains TRAIN metrics (sum_pnl_pct, sortino, …).
    It is *not* comparable in absolute magnitude to OOS values in the top-k
    sheets — the two layers answer different questions:
        top-k layer  → "did the bucket win the competition in this step?"
        surface layer → "was the bucket even explored in this step?"

    ── Two-level presence ──────────────────────────────────────────────────
    structural presence  : at least 1 distinct (atr, mult) point in bucket
    reliable coverage    : fill_ratio = observed_distinct / expected_grid_size
                           >= reliable_fill_threshold (default 0.5)

    ── Display vs scoring layers ───────────────────────────────────────────
    Display layer  : step cell = median(sum_pnl_pct) when structurally present
                     (NaN only when bucket absent — 0 observed points)
    Scoring layer  : surface_above_median_* and surface_stability_score use
                     ONLY reliable steps (fill_ratio >= threshold).
                     surface_above_median_ratio denominator = reliable_steps_count.
                     surface_stability_score denominator = wf_total_steps.

    Parameters
    ----------
    all_step_trials : pd.DataFrame
        Must contain: step_id, atr_period, multiplier, sum_pnl_pct.
        May contain:  robust_score, sortino, surface_source_type.
    config : dict
        Full run config.  Reads optimization.* and walk_forward.consensus.*.
    wf_total_steps : int
        Total number of WF steps (= len(result.steps)).  Used as denominator
        for surface_presence_ratio, reliable_steps_ratio, surface_stability_score.
        Must be >= 1.
    reliable_fill_threshold : float
        Minimum fill_ratio for a step to count as "reliable".  Default 0.5.
    min_reliable_ratio : float
        Scoring floor: if reliable_steps_ratio < min_reliable_ratio the
        surface_stability_score is set to 0.0 (bucket lacks sufficient coverage
        for auto-select).  Default 0.2.

    Returns
    -------
    pd.DataFrame with columns:
        bucket_param, atr_bucket, mult_bucket_ticks,
        expected_grid_size,
        Step0 … StepN  (median train pnl; NaN when absent),
        surface_presence_steps,          # "WF0,WF2,…" string
        surface_presence_count,          # int
        surface_presence_ratio,          # / wf_total_steps
        surface_point_count_mean,        # mean(observed_distinct) over present steps
        surface_fill_ratio_mean,         # mean(fill_ratio) over present steps
        reliable_steps_count,            # int
        reliable_steps_ratio,            # / wf_total_steps
        surface_above_median_count,      # reliable steps where bucket >= step median
        surface_above_median_ratio,      # / reliable_steps_count  (0.0 when 0 reliable)
        surface_stability_score,         # 0.6*reliable_ratio + 0.4*above_median_ratio
                                         # 0.0 when reliable_ratio < min_reliable_ratio
    """
    from supertrend_optimizer.scoring.aggregation import _apply_param_buckets

    # ── 0. Guard ────────────────────────────────────────────────────────────
    if (
        all_step_trials is None
        or not isinstance(all_step_trials, pd.DataFrame)
        or all_step_trials.empty
    ):
        return pd.DataFrame()

    required_cols = {"step_id", "atr_period", "multiplier", "sum_pnl_pct"}
    if not required_cols.issubset(all_step_trials.columns):
        missing = required_cols - set(all_step_trials.columns)
        logger.warning(
            "_build_surface_bucket_step_matrix: missing columns %s — returning empty",
            missing,
        )
        return pd.DataFrame()

    if wf_total_steps < 1:
        logger.warning(
            "_build_surface_bucket_step_matrix: wf_total_steps=%d < 1 — returning empty",
            wf_total_steps,
        )
        return pd.DataFrame()

    # ── 1. Config ────────────────────────────────────────────────────────────
    wf_cfg   = config.get("walk_forward", {})
    cons_cfg = wf_cfg.get("consensus", {})
    if atr_bucket_step is None:
        atr_bucket_step = int(cons_cfg.get("atr_bucket_step", DEFAULT_ATR_BUCKET_STEP))
    if mult_bucket_step is None:
        mult_bucket_step = float(cons_cfg.get("mult_bucket_step", DEFAULT_MULT_BUCKET_STEP))
    min_buckets_for_median = int(cons_cfg.get("min_buckets_for_median", 5))

    # ── 2. Bucket assignment (anti-drift: same _apply_param_buckets as runtime) ─
    trials = all_step_trials.copy()
    trials["atr_period"] = pd.to_numeric(trials["atr_period"], errors="coerce")
    trials["multiplier"]  = pd.to_numeric(trials["multiplier"],  errors="coerce")
    trials["sum_pnl_pct"] = pd.to_numeric(trials["sum_pnl_pct"], errors="coerce")
    trials["step_id"]     = pd.to_numeric(trials["step_id"],     errors="coerce")
    trials = trials.dropna(subset=["atr_period", "multiplier", "sum_pnl_pct", "step_id"])
    trials["step_id"] = trials["step_id"].astype(int)

    if trials.empty:
        return pd.DataFrame()

    trials = _apply_param_buckets(trials, atr_step=atr_bucket_step, mult_step=mult_bucket_step)

    # ── 3. Full bucket grid and expected sizes ───────────────────────────────
    all_bucket_keys: List[Tuple[int, int]] = _generate_full_bucket_grid(
        config, atr_bucket_step=atr_bucket_step, mult_bucket_step=mult_bucket_step,
    )
    expected_sizes: Dict[Tuple[int, int], int] = _compute_expected_grid_sizes(
        config, atr_bucket_step=atr_bucket_step, mult_bucket_step=mult_bucket_step,
    )

    if not all_bucket_keys:
        return pd.DataFrame()

    n_steps  = wf_total_steps
    step_cols = [f"Step{i}" for i in range(n_steps)]

    # ── 4. Per-(step, bucket): observed distinct points and median pnl ───────
    # observed_distinct[(step_id, bucket_key)] = set of (atr, mult) pairs seen
    # pnl_vals[(step_id, bucket_key)]           = list of pnl values for median
    observed_distinct: Dict[Tuple[int, Tuple[int, int]], set] = {}
    pnl_vals:          Dict[Tuple[int, Tuple[int, int]], List[float]] = {}

    for _, row in trials.iterrows():
        s_id = int(row["step_id"])
        bk   = (int(row["atr_bucket"]), int(row["mult_bucket_ticks"]))
        pt   = (int(row["atr_period"]), float(round(row["multiplier"], 6)))
        pnl  = float(row["sum_pnl_pct"])

        sb_key = (s_id, bk)
        if sb_key not in observed_distinct:
            observed_distinct[sb_key] = set()
            pnl_vals[sb_key] = []
        observed_distinct[sb_key].add(pt)
        pnl_vals[sb_key].append(pnl)

    # ── 5. Build per-(step, bucket) fill_ratio and display cell ──────────────
    # fill_ratio[(step_id, bucket_key)]  = observed_distinct_count / expected_size
    # cell_val  [(step_id, bucket_key)]  = median(pnl)  — display layer
    fill_ratio_map: Dict[Tuple[int, Tuple[int, int]], float] = {}
    cell_val_map:   Dict[Tuple[int, Tuple[int, int]], float] = {}

    for sb_key, pts in observed_distinct.items():
        s_id, bk = sb_key
        exp_size = expected_sizes.get(bk, 1)  # fallback 1: never div-by-zero
        obs_cnt  = len(pts)                    # distinct (atr, mult) pairs
        fill_ratio_map[sb_key] = obs_cnt / exp_size
        # Median over all pnl values collected for this (step, bucket)
        cell_val_map[sb_key] = float(np.median(pnl_vals[sb_key]))

    # ── 6. Build main DataFrame rows (display layer) ─────────────────────────
    rows: List[Dict[str, Any]] = []
    for (ab, mt) in all_bucket_keys:
        exp_size = expected_sizes.get((ab, mt), 0)
        row: Dict[str, Any] = {
            "bucket_param":      _canonical_format_bucket_label(ab, mt, atr_bucket_step, mult_bucket_step),
            "bucket_key":        f"{ab}_{mt}",
            "atr_bucket":        ab,
            "mult_bucket_ticks": mt,
            "expected_grid_size": exp_size,
        }
        for s_idx, col in enumerate(step_cols):
            sb_key = (s_idx, (ab, mt))
            row[col] = cell_val_map.get(sb_key, float("nan"))
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    bdf = pd.DataFrame(rows)

    # ── 7. Per-bucket reliability and scoring metrics ─────────────────────────
    #
    # Definitions:
    #   structural presence : observed_distinct_count >= 1  for that (step, bucket)
    #   reliable coverage   : fill_ratio >= reliable_fill_threshold
    #
    # Display layer  → step cell set above (always median when structurally present)
    # Scoring layer  → reliable steps only for above_median and stability_score

    presence_count_list:          List[int]   = []
    presence_steps_list:          List[str]   = []
    fill_ratio_mean_list:         List[float] = []
    point_count_mean_list:        List[float] = []
    reliable_count_list:          List[int]   = []
    above_median_count_list:      List[int]   = []
    eligible_median_steps_list:   List[int]   = []

    for row_i, (ab, mt) in enumerate(all_bucket_keys):
        bk = (ab, mt)
        exp_size = expected_sizes.get(bk, 1)

        present_steps:  List[int] = []
        reliable_steps: List[int] = []
        fill_ratios:    List[float] = []
        point_counts:   List[int]  = []

        for s_idx in range(n_steps):
            sb_key = (s_idx, bk)
            if sb_key not in observed_distinct:
                continue  # bucket absent in this step
            obs_cnt  = len(observed_distinct[sb_key])
            fr       = fill_ratio_map[sb_key]
            present_steps.append(s_idx)
            fill_ratios.append(fr)
            point_counts.append(obs_cnt)
            if fr >= reliable_fill_threshold:
                reliable_steps.append(s_idx)

        presence_count_list.append(len(present_steps))
        presence_steps_list.append(",".join(f"WF{s}" for s in present_steps))
        fill_ratio_mean_list.append(
            float(np.mean(fill_ratios)) if fill_ratios else 0.0
        )
        point_count_mean_list.append(
            float(np.mean(point_counts)) if point_counts else 0.0
        )
        reliable_count_list.append(len(reliable_steps))

        # above_median: scoring layer — reliable steps only, AND step must
        # have >= min_buckets_for_median non-NaN buckets (structural presence).
        # Eligible = reliable AND passes min_buckets_for_median threshold.
        n_above = 0
        n_eligible = 0
        for s_idx in reliable_steps:
            col = f"Step{s_idx}"
            if col not in bdf.columns:
                continue
            step_cells = pd.to_numeric(bdf[col], errors="coerce").dropna()
            if step_cells.empty:
                continue
            if len(step_cells) < min_buckets_for_median:
                continue  # step has too few buckets for meaningful median comparison
            step_median = float(step_cells.median())
            bucket_cell = bdf.at[row_i, col]
            if not pd.isna(bucket_cell):
                n_eligible += 1
                if float(bucket_cell) >= step_median:
                    n_above += 1
        above_median_count_list.append(n_above)
        eligible_median_steps_list.append(n_eligible)

    bdf["surface_presence_count"]  = presence_count_list
    bdf["surface_presence_ratio"]  = [c / n_steps for c in presence_count_list]
    bdf["surface_presence_steps"]  = presence_steps_list
    bdf["surface_point_count_mean"] = point_count_mean_list
    bdf["surface_fill_ratio_mean"] = fill_ratio_mean_list
    bdf["reliable_steps_count"]    = reliable_count_list
    bdf["reliable_steps_ratio"]    = [c / n_steps for c in reliable_count_list]
    bdf["surface_above_median_count"] = above_median_count_list

    # surface_above_median_ratio: quality-given-reliable-coverage
    #   denominator = reliable_steps_count  (orthogonal to coverage component)
    #   0.0 when reliable_steps_count == 0  (no reliable data → no quality signal)
    above_median_ratio_list: List[float] = []
    for cnt, rel in zip(above_median_count_list, reliable_count_list):
        above_median_ratio_list.append(float(cnt / rel) if rel > 0 else 0.0)
    bdf["surface_above_median_ratio"] = above_median_ratio_list

    # surface_eligible_median_steps_count: reliable steps that also passed
    # the min_buckets_for_median threshold.
    bdf["surface_eligible_median_steps_count"] = eligible_median_steps_list
    bdf["surface_above_median_ratio_eligible"] = [
        float(c / e) if e > 0 else 0.0
        for c, e in zip(above_median_count_list, eligible_median_steps_list)
    ]

    # surface_stability_score:
    #   0.6 * reliable_steps_ratio  (coverage breadth, denominator = wf_total_steps)
    # + 0.4 * surface_above_median_ratio  (quality depth, denominator = reliable_count)
    # = 0.0  when reliable_steps_ratio < min_reliable_ratio  (scoring floor)
    stability_scores: List[float] = []
    for rel_ratio, amr in zip(
        bdf["reliable_steps_ratio"].tolist(),
        above_median_ratio_list,
    ):
        if rel_ratio < min_reliable_ratio:
            stability_scores.append(0.0)
        else:
            stability_scores.append(round(0.6 * rel_ratio + 0.4 * amr, 6))
    bdf["surface_stability_score"] = stability_scores

    # ── 8. Final column order ─────────────────────────────────────────────────
    ordered_cols = (
        ["bucket_param", "bucket_key", "atr_bucket", "mult_bucket_ticks", "expected_grid_size"]
        + step_cols
        + [
            "surface_presence_steps",
            "surface_presence_count",
            "surface_presence_ratio",
            "surface_point_count_mean",
            "surface_fill_ratio_mean",
            "reliable_steps_count",
            "reliable_steps_ratio",
            "surface_above_median_count",
            "surface_above_median_ratio",
            "surface_eligible_median_steps_count",
            "surface_above_median_ratio_eligible",
            "surface_stability_score",
        ]
    )
    existing = [c for c in ordered_cols if c in bdf.columns]
    return bdf[existing]


# ---------------------------------------------------------------------------
# Pre-selection candidate stability map
# ---------------------------------------------------------------------------

def _compute_candidate_stability_map(
    result: Any,
    candidate_keys: List[Tuple[int, float]],
    canonical_df: pd.DataFrame,
    top_n: int,
) -> Dict[Tuple[int, float], float]:
    """Build a lightweight map: candidate_key -> bucket_stability_score.

    Computes bucket_stability_score (same formula as _build_bucket_aggregated_table)
    but BEFORE greedy selection so the stability gate can filter candidates.

    Returns an empty dict if step data or bucket info is unavailable — the
    caller must treat an empty dict as "stability filtering unavailable" and
    skip the gate entirely (do not exclude candidates).
    """
    step_winners_df = _build_step_winners_table(result, candidate_keys[:top_n])
    if step_winners_df.empty:
        return {}

    step_cols = [c for c in step_winners_df.columns if c.startswith("Step")]
    if not step_cols:
        return {}

    total_steps = len(step_cols)

    # Build param_label -> (atr_bucket, mult_bucket_ticks) from canonical_df
    param_to_bucket: Dict[str, Tuple[Any, Any]] = {}
    for _, row in canonical_df.iterrows():
        try:
            atr_int = int(float(row.get("atr_period", 0)))
            mult_f = round(float(row.get("multiplier", 0)), 6)
        except (TypeError, ValueError):
            continue
        atr_b = row.get("atr_bucket")
        mult_b = row.get("mult_bucket_ticks")
        try:
            if atr_b is None or pd.isna(atr_b) or mult_b is None or pd.isna(mult_b):
                continue
        except (TypeError, ValueError):
            continue
        lbl = f"atr={atr_int}_mult={mult_f}"
        param_to_bucket[lbl] = (atr_b, mult_b)

    if not param_to_bucket:
        return {}

    # Attach bucket to step_winners rows
    sw = step_winners_df.copy()
    sw["_bucket"] = sw["param"].map(lambda p: param_to_bucket.get(p))
    sw = sw.dropna(subset=["_bucket"])
    if sw.empty:
        return {}

    # Group by bucket and compute per-bucket stability score (same formula)
    bucket_groups = {bk: grp for bk, grp in sw.groupby("_bucket")}

    # Precompute per-step per-bucket medians for above_median counting
    # Also retain wins/top3 computation for diagnostics (not used in score)
    bucket_stability: Dict[Any, float] = {}
    for bk, group in bucket_groups.items():
        valid_steps = sum(
            1 for col in step_cols if not group[col].dropna().empty
        )
        above_median_count = 0
        for col in step_cols:
            all_medians: Dict[Any, float] = {}
            for bk2, g2 in bucket_groups.items():
                vals = g2[col].dropna()
                if not vals.empty:
                    # NOTE: np.median(vals) over multiple candidates in the
                    # same bucket — this is the per-bucket median for this step.
                    all_medians[bk2] = float(np.median(vals))
            if not all_medians:
                continue
            bk_vals = group[col].dropna()
            if bk_vals.empty:
                continue
            bk_median = float(np.median(bk_vals))
            # TODO(phase2/deferred-high-priority): step_median is computed as
            # median(list(all_medians.values())) — i.e. median of per-bucket
            # medians, which is a median-of-medians.  This is mathematically
            # incorrect: the true step median should be the median of all
            # individual candidate values across all buckets in that step.
            # Fixing this changes stability gate behaviour, candidate filtering,
            # and greedy selection results — behaviour-changing refactor deferred
            # to Phase 2.
            step_median = float(np.median(list(all_medians.values())))
            if bk_median >= step_median:
                above_median_count += 1

        # TODO(phase2): above_median_ratio uses total_steps as denominator
        # (legacy, same as _build_full/median_bucket_step_matrix).
        # Changing the denominator requires recalibrating stability gate
        # thresholds and is deferred to Phase 2.
        if total_steps > 0:
            presence_ratio = valid_steps / total_steps
            above_median_ratio = above_median_count / total_steps
            score = 0.6 * presence_ratio + 0.4 * above_median_ratio
        else:
            score = 0.0
        bucket_stability[bk] = round(score, 6)

    # Map each candidate key to its bucket's stability score
    result_map: Dict[Tuple[int, float], float] = {}
    for key in candidate_keys:
        lbl = _label(key)
        bucket = param_to_bucket.get(lbl)
        if bucket is not None and bucket in bucket_stability:
            result_map[key] = bucket_stability[bucket]

    return result_map


# ---------------------------------------------------------------------------
# Pair diagnostics table
# ---------------------------------------------------------------------------

def _build_pair_diagnostics(
    keys: List[Tuple[int, float]],
    ensemble_set: set,
    corr_matrix: pd.DataFrame,
    corr_meta_lookup: Dict[Tuple[str, str], PairwiseCorrelationResult],
    overlap_matrix: pd.DataFrame,
    phase_matrix: pd.DataFrame,
    config: Dict[str, Any],
    extra_keys: Optional[List[Tuple[int, float]]] = None,
) -> pd.DataFrame:
    """Build pair diagnostics for all C(N,2) pairs in *keys*, plus any extra
    pairs needed to guarantee every ensemble pair is covered.

    Parameters
    ----------
    keys :
        Primary key list (top-M heavy-check candidates).
    ensemble_set :
        Set of keys selected into the ensemble — used to ensure coverage.
    corr_matrix, overlap_matrix, phase_matrix :
        Pre-computed metric matrices.  Metrics are looked up by canonical label
        ``atr={int}_mult={float}``.  Missing matrix entries produce NaN.
    corr_meta_lookup :
        Symmetric lookup (both (A,B) and (B,A) keyed) of PairwiseCorrelationResult.
        Used to populate corr_status and overlap metadata columns, and to
        determine corr_pass via is_reliable rather than np.isnan.
    config :
        Diversification config dict.
    extra_keys :
        Additional keys (e.g. ensemble members not in *keys*) whose pairs with
        every ensemble member must also appear in Table B.
    """
    corr_thr       = float(config.get("corr_threshold", 0.6))
    overlap_thr    = float(config.get("overlap_threshold", 0.6))
    phase_thr      = float(config.get("phase_threshold", 0.75))
    overlap_enabled = bool(config.get("overlap_filter", True))
    phase_enabled   = bool(config.get("phase_filter", True))

    # Build the full ordered key universe for this table:
    # Start with the primary heavy-check keys, then append any extra ensemble
    # members that are not already present.  Order is stable (primary first).
    seen: set = set()
    all_keys: List[Tuple[int, float]] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            all_keys.append(k)
    if extra_keys:
        for k in extra_keys:
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

    rows = []
    seen_pairs: set = set()

    for i in range(len(all_keys)):
        for j in range(i + 1, len(all_keys)):
            ka, kb = all_keys[i], all_keys[j]
            # Canonical A/B ordering: smaller key first so the pair is unique
            if ka > kb:
                ka, kb = kb, ka
            pair_key = (ka, kb)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            la, lb = _label(ka), _label(kb)

            corr_val = float(corr_matrix.loc[la, lb]) if (
                la in corr_matrix.index and lb in corr_matrix.columns
            ) else float("nan")

            overlap_val = float(overlap_matrix.loc[la, lb]) if (
                overlap_enabled
                and la in overlap_matrix.index
                and lb in overlap_matrix.columns
            ) else float("nan")

            phase_val = float(phase_matrix.loc[la, lb]) if (
                phase_enabled
                and la in phase_matrix.index
                and lb in phase_matrix.columns
            ) else float("nan")

            # corr_pass via is_reliable — never np.isnan
            pair_meta = corr_meta_lookup.get((la, lb))
            if pair_meta is not None and pair_meta.is_reliable:
                corr_pass = pair_meta.corr < corr_thr
            else:
                corr_pass = False  # insufficient data → not passed

            overlap_pass = (overlap_val < overlap_thr) if (
                overlap_enabled and not np.isnan(overlap_val)
            ) else True
            phase_pass   = (phase_val < phase_thr) if (
                phase_enabled and not np.isnan(phase_val)
            ) else True
            pair_passed  = corr_pass and overlap_pass and phase_pass

            rows.append({
                # Canonical machine-readable keys (for programmatic lookup)
                "model_A_key":        la,
                "model_B_key":        lb,
                # Human-readable parameter columns
                "atr_A":              ka[0],
                "mult_A":             ka[1],
                "atr_B":              kb[0],
                "mult_B":             kb[1],
                # Legacy display columns (kept for backward compatibility)
                "model_A":            la,
                "model_B":            lb,
                # Pair metrics
                "corr_returns":       corr_val,
                "trade_overlap_rate": overlap_val,
                "phase_similarity":   phase_val,
                "pair_passed":        pair_passed,
                # Correlation metadata columns (+6)
                "corr_status":        pair_meta.status if pair_meta else CorrStatus.NO_DATA,
                "common_steps":       pair_meta.common_steps if pair_meta else 0,
                "common_bars":        pair_meta.common_bars if pair_meta else 0,
                "coverage_a":         pair_meta.coverage_a if pair_meta else 0.0,
                "coverage_b":         pair_meta.coverage_b if pair_meta else 0.0,
                "overlap_ratio":      pair_meta.overlap_ratio if pair_meta else 0.0,
            })

    _PAIR_COLS = [
        "model_A_key", "model_B_key",
        "atr_A", "mult_A", "atr_B", "mult_B",
        "model_A", "model_B",
        "corr_returns", "trade_overlap_rate", "phase_similarity", "pair_passed",
        "corr_status", "common_steps", "common_bars",
        "coverage_a", "coverage_b", "overlap_ratio",
    ]
    if not rows:
        return pd.DataFrame(columns=_PAIR_COLS)
    return pd.DataFrame(rows)[_PAIR_COLS]


def _validate_ensemble_pairs_in_table_b(
    ensemble_keys: List[Tuple[int, float]],
    pair_diagnostics_df: pd.DataFrame,
) -> None:
    """Raise ValueError if any selected ensemble pair is missing from Table B.

    Every pair (A, B) that was selected into the ensemble must appear as an
    exact row in Table B so the user can audit the pair metrics.  A missing
    row means the traceability guarantee has been violated.

    Parameters
    ----------
    ensemble_keys :
        Ordered list of keys selected into the ensemble.
    pair_diagnostics_df :
        The pair diagnostics DataFrame (Table B) produced by
        ``_build_pair_diagnostics``.

    Raises
    ------
    ValueError
        If one or more ensemble pairs are absent from Table B.
    """
    if len(ensemble_keys) < 2:
        return  # single-model fallback — no pairs to check

    if pair_diagnostics_df.empty:
        raise ValueError(
            "Diversification integrity error: Table B (pair_diagnostics_df) is "
            "empty but the ensemble contains multiple models.  "
            "Every selected ensemble pair must be traceable in Table B."
        )

    # Build a set of canonical (A, B) pairs present in Table B.
    # Both model_A_key and model_B_key columns store canonical labels; we store
    # each pair as a frozenset so A/B ordering is irrelevant.
    if "model_A_key" not in pair_diagnostics_df.columns or \
       "model_B_key" not in pair_diagnostics_df.columns:
        raise ValueError(
            "Diversification integrity error: Table B is missing the required "
            "'model_A_key' / 'model_B_key' columns.  "
            "Run _build_pair_diagnostics with the updated code."
        )

    table_b_pairs: set = {
        frozenset([row["model_A_key"], row["model_B_key"]])
        for _, row in pair_diagnostics_df.iterrows()
    }

    missing: List[str] = []
    for i in range(len(ensemble_keys)):
        for j in range(i + 1, len(ensemble_keys)):
            ka, kb = ensemble_keys[i], ensemble_keys[j]
            la, lb = _label(ka), _label(kb)
            pair_fs = frozenset([la, lb])
            if pair_fs not in table_b_pairs:
                missing.append(f"({la}, {lb})")

    if missing:
        raise ValueError(
            f"Diversification integrity error: the following selected ensemble "
            f"pair(s) are absent from Table B (pair_diagnostics_df): "
            f"{', '.join(missing)}.  "
            f"This should never happen — Table B must cover all ensemble pairs."
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_diversification_filter(
    canonical_df: pd.DataFrame,
    result: Any,
    config: Dict[str, Any],
) -> DiversificationResult:
    """Run the diversification filter and return a DiversificationResult.

    Parameters
    ----------
    canonical_df : pd.DataFrame
        Output of _write_wf_topk_canonical_sheet() — fully scored, gated,
        sorted DataFrame (may contain metadata rows with string canonical_rank).
    result : WalkForwardResult
        Full walk-forward result object.
    config : dict
        Full optimizer config dict.

    Returns
    -------
    DiversificationResult
        .empty()       when feature disabled and placeholder sheets off
        .placeholder() when feature disabled and placeholder sheets on
        full result    when feature enabled
    """
    div_cfg = config.get("diversification", {})

    if not div_cfg.get("enabled", False):
        if div_cfg.get("write_placeholder_sheets_when_disabled", True):
            return DiversificationResult.make_placeholder()
        return DiversificationResult.empty()

    # --- Config validation ---
    valid, reason = validate_diversification_config(div_cfg)
    if not valid:
        logger.warning(
            "Diversification config invalid (%s) — disabling for this run.", reason
        )
        if div_cfg.get("write_placeholder_sheets_when_disabled", True):
            return DiversificationResult.make_placeholder()
        return DiversificationResult.empty()

    # --- Config parameters ---
    top_n        = int(div_cfg.get("top_n_candidates", 20))
    top_m        = int(div_cfg.get("top_m_heavy_checks", 10))
    lag_window   = int(div_cfg.get("phase_lag_window", 5))

    # --- Defensive column validation ---
    # These columns must be present for Table A to be fully populated.
    # If any are missing, log a warning so silent empty output is detectable.
    _expected_cols = {
        "canonical_rank", "final_score", "regime_score",
        "mult_bucket_ticks", "robust_score",
    }
    _missing_cols = _expected_cols - set(canonical_df.columns)
    if _missing_cols:
        logger.warning(
            "Diversification: canonical_df is missing expected columns %s. "
            "Table A will have empty cells for those columns. "
            "Ensure run_diversification_filter receives the fully canonical-scored "
            "dataframe (output of _write_wf_topk_canonical_sheet).",
            sorted(_missing_cols),
        )

    # --- Phase 1: Candidate extraction ---
    # Strip metadata rows (canonical_rank is a string like "# gates_status")
    df = canonical_df.copy()
    if "canonical_rank" in df.columns:
        numeric_mask = pd.to_numeric(df["canonical_rank"], errors="coerce").notna()
        df = df[numeric_mask].copy()

    # Keep only tradeability-passed, non-gate-excluded rows
    if "tradeability_passed" in df.columns:
        df = df[df["tradeability_passed"].astype(bool)].copy()
    if "gate_exclusion_reason" in df.columns:
        df = df[df["gate_exclusion_reason"].isna() | (df["gate_exclusion_reason"] == "")].copy()

    # Sort by canonical_rank ascending (rank 1 = best)
    if "canonical_rank" in df.columns:
        df = df.sort_values("canonical_rank").reset_index(drop=True)

    # Take top-N
    df = df.head(top_n).copy()

    if df.empty:
        logger.warning("Diversification: no eligible candidates after filtering.")
        return DiversificationResult.make_placeholder()

    # Normalise keys and build candidate list (ordered by canonical_rank)
    candidate_keys: List[Tuple[int, float]] = [
        _normalize_param_key(row["atr_period"], row["multiplier"])
        for _, row in df.iterrows()
    ]

    # Score map: final_score per candidate key
    score_col = "final_score" if "final_score" in df.columns else "robust_score"
    score_map: Dict[Tuple[int, float], float] = {}
    for _, row in df.iterrows():
        k = _normalize_param_key(row["atr_period"], row["multiplier"])
        val = row.get(score_col, 0.0)
        score_map[k] = float(val) if pd.notna(val) else 0.0

    # Bucket map for diversity bonus: key -> (atr_bucket, mult_bucket_ticks)
    bucket_map: Optional[Dict[Tuple[int, float], Tuple[int, int]]] = None
    if "atr_bucket" in df.columns and "mult_bucket_ticks" in df.columns:
        bucket_map = {}
        for _, row in df.iterrows():
            k = _normalize_param_key(row["atr_period"], row["multiplier"])
            try:
                ab = int(row["atr_bucket"])
                mb = int(row["mult_bucket_ticks"])
                bucket_map[k] = (ab, mb)
            except (TypeError, ValueError):
                pass

    # --- Config: correlation alignment thresholds ---
    min_common_steps = int(div_cfg.get("min_common_steps", 2))
    min_common_bars  = int(div_cfg.get("min_common_bars", 400))

    # --- Stitch OOS arrays into StitchedArray objects ---
    stitched_returns:   Dict[Tuple[int, float], StitchedArray] = {}
    positions_dict: Dict[Tuple[int, float], np.ndarray] = {}

    for key in candidate_keys:
        atr, mult = key
        sa = _stitch_oos_arrays(result, atr, mult, "oos_returns")
        pos = _stitch_oos_arrays(result, atr, mult, "oos_positions")
        if sa is not None and sa.total_bars > 0:
            stitched_returns[key] = sa
        if pos is not None and pos.total_bars > 0:
            # positions_dict keeps flat arrays for overlap/phase (unchanged)
            positions_dict[key] = pos.data

    # Candidates with no return data are excluded from matrix computation
    valid_keys = [k for k in candidate_keys if k in stitched_returns]

    if len(valid_keys) < 2:
        logger.warning(
            "Diversification: fewer than 2 candidates have OOS return data "
            "(diversification.enabled=true but retain_oos_arrays may be false). "
            "Returning placeholder."
        )
        return DiversificationResult.make_placeholder()

    # --- Phase 2: Correlation matrix (full N×N on valid_keys) ---
    corr_matrix, corr_meta_df = _compute_correlation_matrix(
        {k: stitched_returns[k] for k in valid_keys},
        min_common_steps=min_common_steps,
        min_common_bars=min_common_bars,
    )

    # Build symmetric corr_meta_lookup: (label_A, label_B) and (label_B, label_A)
    # → PairwiseCorrelationResult.  Used by _greedy_select and _build_pair_diagnostics.
    corr_meta_lookup: Dict[Tuple[str, str], PairwiseCorrelationResult] = {}
    for _, row in corr_meta_df.iterrows():
        la_m, lb_m = str(row["model_A"]), str(row["model_B"])
        res = PairwiseCorrelationResult(
            corr=float(row["corr_returns"]),
            status=str(row["status"]),
            common_steps=int(row["common_steps"]),
            common_bars=int(row["common_bars"]),
            coverage_a=float(row["coverage_a"]),
            coverage_b=float(row["coverage_b"]),
            overlap_ratio=float(row["overlap_ratio"]),
        )
        corr_meta_lookup[(la_m, lb_m)] = res
        corr_meta_lookup[(lb_m, la_m)] = res

    # --- Phase 3: Heavy filters (top-M only for selection; extended for Table B) ---
    # Selection uses only top-M for overlap/phase filtering (performance budget).
    # When subset_search_enabled, expand heavy_keys to cover all subset candidates
    # so that fail-closed overlap/phase checks have real data.
    #
    # Boundary derivation when subset_search_enabled=True:
    #   heavy_limit = min(subset_search_max_candidates, len(valid_keys))
    #               = exact number of candidates subset search will consider
    #   heavy_keys  = valid_keys[:max(heavy_limit, top_m)]
    #               = covers all subset candidates (heavy_limit) AND preserves
    #                 at least top_m so the greedy fallback is never degraded.
    #   The max() is a safety floor for the edge case where
    #   subset_search_max_candidates < top_m; in the normal case
    #   (max_candidates >= top_m) heavy_keys = valid_keys[:heavy_limit].
    subset_search_enabled = bool(div_cfg.get("subset_search_enabled", False))
    subset_search_max_candidates = int(div_cfg.get("subset_search_max_candidates", 12))

    if subset_search_enabled:
        heavy_limit = min(subset_search_max_candidates, len(valid_keys))
        heavy_keys = valid_keys[:max(heavy_limit, top_m)]
    else:
        heavy_keys = valid_keys[:top_m]

    heavy_pos  = {k: positions_dict[k] for k in heavy_keys if k in positions_dict}

    if heavy_pos and bool(div_cfg.get("overlap_filter", True)):
        overlap_matrix = _compute_overlap_matrix(heavy_pos)
    else:
        overlap_matrix = pd.DataFrame()

    if heavy_pos and bool(div_cfg.get("phase_filter", True)):
        phase_matrix = _compute_phase_similarity_matrix(heavy_pos, lag_window)
    else:
        phase_matrix = pd.DataFrame()

    # --- Phase 3.5: Bucket stability map (pre-selection) ---
    # Built before greedy selection so the stability gate can filter candidates.
    # Uses the full canonical_df (not just top-N) for bucket mapping, and all
    # candidate_keys (not just valid_keys) to match _build_step_winners_table.
    bucket_stability_map = _compute_candidate_stability_map(
        result, candidate_keys, canonical_df, top_n
    )

    # --- Phase 3.7: Bucket representative filtering (optional) ---
    repr_mode = div_cfg.get("representative_mode", "off")
    pool_before_repr = len(valid_keys)
    selection_keys = _filter_to_representatives(
        valid_keys, bucket_map, score_map, div_cfg
    )
    if repr_mode != "off":
        logger.info(
            "Representative mode: %s (%d per bucket)",
            repr_mode,
            int(div_cfg.get("representatives_per_bucket", 1)),
        )
        logger.info(
            "Representative pool: %d → %d candidates",
            pool_before_repr,
            len(selection_keys),
        )

    # --- Phase 4: Selection — greedy or global subset search ---
    _subsets_evaluated: Optional[int] = None
    _best_subset_score: Optional[float] = None

    if subset_search_enabled:
        logger.info(
            "Subset search: enabled (%s, max_candidates=%d)",
            div_cfg.get("subset_search_mode", "pair_triple"),
            subset_search_max_candidates,
        )
        ensemble_keys, _subsets_evaluated, _best_subset_score_raw = _subset_search(
            candidates=selection_keys,
            score_map=score_map,
            corr_matrix=corr_matrix,
            overlap_matrix=overlap_matrix,
            phase_matrix=phase_matrix,
            div_cfg=div_cfg,
            bucket_map=bucket_map,
            bucket_stability_map=bucket_stability_map,
            corr_meta_lookup=corr_meta_lookup,
        )
        _best_subset_score = (
            None if _best_subset_score_raw == float("-inf") else _best_subset_score_raw
        )
        logger.info("Subsets evaluated: %d", _subsets_evaluated)
        if _best_subset_score is not None:
            logger.info("Best subset score: %.4f", _best_subset_score)
        logger.info(
            "Selected ensemble: %s",
            [_label(k) for k in ensemble_keys],
        )
    else:
        ensemble_keys = _greedy_select(
            candidates=selection_keys,
            score_map=score_map,
            corr_matrix=corr_matrix,
            overlap_matrix=overlap_matrix,
            phase_matrix=phase_matrix,
            config=div_cfg,
            bucket_map=bucket_map,
            bucket_stability_map=bucket_stability_map,
            corr_meta_lookup=corr_meta_lookup,
        )

    if len(ensemble_keys) < 2:
        logger.warning(
            "Diversification: ensemble has %d member(s) after selection "
            "(single-model fallback).", len(ensemble_keys)
        )

    ensemble_set = set(ensemble_keys)

    # --- Extend overlap/phase matrices to cover all ensemble members ---
    # Greedy selection only used heavy_keys (top-M) for overlap/phase hard
    # filters.  If an ensemble member ranked beyond top-M was chosen (possible
    # because selection iterates all valid_keys), its position data was never
    # included in heavy_pos.  We recompute the matrices over the union of
    # heavy_keys ∪ ensemble_keys so Table B has real metric values (not NaN)
    # for every selected pair.
    ensemble_extras = [k for k in ensemble_keys if k not in set(heavy_keys)]
    if ensemble_extras:
        extended_pos = dict(heavy_pos)
        for k in ensemble_extras:
            if k in positions_dict:
                extended_pos[k] = positions_dict[k]

        if extended_pos and bool(div_cfg.get("overlap_filter", True)):
            overlap_matrix = _compute_overlap_matrix(extended_pos)

        if extended_pos and bool(div_cfg.get("phase_filter", True)):
            phase_matrix = _compute_phase_similarity_matrix(extended_pos, lag_window)

    # Build the list of extra keys for Table B (ensemble members beyond top-M)
    # using the (possibly updated) heavy_keys baseline.
    diag_extra_keys = [k for k in ensemble_keys if k not in set(heavy_keys)]

    # --- Build Table A: candidates_df ---
    ensemble_rank_map = {k: i + 1 for i, k in enumerate(ensemble_keys)}
    rows_a = []
    for _, row in df.iterrows():
        k = _normalize_param_key(row["atr_period"], row["multiplier"])
        in_ens = k in ensemble_set

        # portfolio_score for ensemble members: recompute for display
        if in_ens:
            idx = ensemble_keys.index(k)
            ps = _portfolio_score(
                ensemble_keys[:idx], k, score_map, corr_matrix,
                float(div_cfg.get("lambda_penalty", 0.3)), bucket_map,
                bucket_stability_map=bucket_stability_map,
                stability_weight=float(div_cfg.get("stability_weight", 0.05)),
                corr_thr=float(div_cfg.get("corr_threshold", 0.6)),
            )
        else:
            ps = float("nan")

        # Always include all 13 columns matching the placeholder schema.
        # Columns absent from the canonical DataFrame are filled with NaN so
        # the schema is identical regardless of ranking mode (classic vs regime_robust).
        r: Dict[str, Any] = {
            "atr_period":            row.get("atr_period"),
            "multiplier":            row.get("multiplier"),
            "canonical_rank":        row.get("canonical_rank"),
            "final_score":           row.get("final_score"),
            "robust_score":          row.get("robust_score"),
            "regime_score":          row.get("regime_score", np.nan),
            "coverage_ratio_bucket": row.get("coverage_ratio_bucket"),
            "atr_bucket":            row.get("atr_bucket", np.nan),
            "mult_bucket_ticks":     row.get("mult_bucket_ticks", np.nan),
            "bucket_stability_score": bucket_stability_map.get(k, float("nan")),
            "in_ensemble":           in_ens,
            "ensemble_rank":         ensemble_rank_map.get(k, float("nan")),
            "portfolio_score":       ps,
        }
        rows_a.append(r)

    # Enforce fixed column order matching the placeholder schema.
    _CANDIDATES_COLS = [
        "atr_period", "multiplier", "canonical_rank", "final_score",
        "robust_score", "regime_score", "coverage_ratio_bucket",
        "atr_bucket", "mult_bucket_ticks", "bucket_stability_score",
        "in_ensemble", "ensemble_rank", "portfolio_score",
    ]
    candidates_df = pd.DataFrame(rows_a)[_CANDIDATES_COLS]

    # --- Build Table B: pair diagnostics ---
    # Primary coverage: top-M heavy-check candidates (have full matrix data).
    # Extra coverage: any ensemble member that ranked beyond top-M is passed as
    # extra_keys so its pairs appear in Table B with real (not NaN) metrics —
    # the matrices were extended above to cover those keys.
    diag_keys = heavy_keys if heavy_keys else valid_keys
    pair_diagnostics_df = _build_pair_diagnostics(
        diag_keys, ensemble_set,
        corr_matrix, corr_meta_lookup, overlap_matrix, phase_matrix, div_cfg,
        extra_keys=diag_extra_keys,
    )

    # --- Integrity check: every selected pair must be traceable in Table B ---
    _validate_ensemble_pairs_in_table_b(ensemble_keys, pair_diagnostics_df)

    # --- Build Table C: step winners ---
    step_winners_df = _build_step_winners_table(result, valid_keys[:top_n])

    # --- Build Table D: bucket-aggregated step performance ---
    # Use the full canonical_df (not just top-N candidates_df) so that every
    # param in step_winners_df can be mapped to its bucket.
    bucket_step_df = _build_bucket_aggregated_table(step_winners_df, canonical_df)

    # --- Debug diagnostics ---
    logger.debug(
        "Diversification data-flow summary: "
        "canonical_df rows=%d | candidates_df rows=%d | "
        "step_winners_df rows=%d | bucket_step_df rows=%d | "
        "corr_meta_df rows=%d | missing_cols=%s",
        len(canonical_df),
        len(candidates_df),
        len(step_winners_df),
        len(bucket_step_df),
        len(corr_meta_df),
        sorted(_missing_cols) if _missing_cols else "none",
    )

    return DiversificationResult(
        candidates_df=candidates_df,
        pair_diagnostics_df=pair_diagnostics_df,
        step_winners_df=step_winners_df,
        bucket_step_df=bucket_step_df,
        corr_matrix=corr_matrix,
        corr_meta_df=corr_meta_df,
        overlap_matrix=overlap_matrix,
        phase_matrix=phase_matrix,
        enabled=True,
        placeholder=False,
        representative_mode=repr_mode,
        representative_pool_size=len(selection_keys) if repr_mode != "off" else None,
        subset_search_enabled=subset_search_enabled,
        subsets_evaluated=_subsets_evaluated,
        best_subset_score=_best_subset_score,
    )
