"""
Walk-Forward optimization and validation logic.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging
import time
import numpy as np
import pandas as pd

from supertrend_optimizer.utils.time_utils import make_walk_forward_slices
from supertrend_optimizer.optimization.multi_optimization import run_single_optimization
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.core.metrics import (
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_max_drawdown,
    calculate_cagr
)
from supertrend_optimizer.utils.constants import (
    DEFAULT_ATR_BUCKET_STEP,
    DEFAULT_MULT_BUCKET_STEP,
    INVALID_METRIC_VALUE,
    EPS,
)
from supertrend_optimizer.utils.enums import ExecutionModel, MarketType
from supertrend_optimizer.data.timeframe import resolve_periods_per_year_from_config
from supertrend_optimizer.optimization.grid_search import (
    _create_empty_result as create_empty_opt_result,
    evaluate_parameter_grid,
)
from supertrend_optimizer.robustness.pipeline import calculate_robustness_for_trials
from supertrend_optimizer.utils.cache import create_atr_cache
from supertrend_optimizer.utils.math_utils import quantize_multiplier
from supertrend_optimizer.utils.warmup import apply_auto_warmup_to_config

logger = logging.getLogger(__name__)


@dataclass
class GateCheck:
    """Result of a single gate check."""
    name: str
    passed: bool
    value: Optional[float] = None
    threshold: Optional[float] = None
    message: Optional[str] = None


@dataclass
class WalkForwardGatesResult:
    """Result of Walk-Forward gates evaluation."""
    enabled: bool
    passed: bool
    checks: List[GateCheck] = field(default_factory=list)
    fail_count: int = 0


@dataclass
class GridEvaluationCounters:
    """
    Counters for mini-grid evaluation (Phase 1).
    
    Tracks how many parameter combinations were attempted, filtered, and kept
    during deterministic grid evaluation.
    """
    attempted: int = 0
    early_exit_count: int = 0
    invalid_objective_count: int = 0
    kept: int = 0
    
    def __post_init__(self):
        """Validate invariant: kept = attempted - early_exit - invalid_objective."""
        expected_kept = self.attempted - self.early_exit_count - self.invalid_objective_count
        if self.kept != expected_kept:
            raise ValueError(
                f"GridEvaluationCounters invariant violated: "
                f"kept ({self.kept}) != attempted ({self.attempted}) - "
                f"early_exit ({self.early_exit_count}) - invalid_objective ({self.invalid_objective_count})"
            )


@dataclass
class MiniGridState:
    """
    State of mini-grid built during discovery step (Phase 1).
    
    Persisted across WF steps and exported to results/Excel.
    Contains the fixed parameter grid and metadata about its construction.
    """
    # Core grid
    params: List[tuple] = field(default_factory=list)  # List[(atr_period: int, multiplier_q: float)]
    
    # Metadata: source
    source_step: int = 0
    seed_params: List[tuple] = field(default_factory=list)  # Original TOP-N seeds as (atr, mult) tuples
    seed_source: str = "robustness_df"  # "robustness_df" | "trials_df_fallback" | "empty"

    # Phase 1: Merged seed metadata (None in legacy mode, populated by new pipeline)
    # Each element is a MergedSeed with appear_count, score_mean, score_min, source_steps.
    merged_seeds: Optional[List[Any]] = field(default=None)
    
    # Metadata: truncation
    grid_size_before_truncate: int = 0
    grid_size_after_truncate: int = 0
    truncated: bool = False
    truncate_policy_used: Optional[str] = None
    
    # Metadata: config snapshot (for reproducibility)
    config_snapshot: Dict[str, Any] = field(default_factory=dict)

    # Phase 2.1: Reproducibility fields (populated by run_walk_forward)
    resolved_discovery_steps: Optional[List[int]] = field(default=None)
    resolved_top_m_per_step: Optional[int] = field(default=None)
    resolved_min_appear_count: Optional[int] = field(default=None)
    single_step_mode: bool = field(default=True)

    # Phase 2.1: Selected seed (top merged seed used as canonical representative)
    selected_seed_atr: Optional[int] = field(default=None)
    selected_seed_multiplier: Optional[float] = field(default=None)
    selected_seed_mult_ticks: Optional[int] = field(default=None)


@dataclass
class WFStepTopEntry:
    """Single entry in TOP-K for a WF step."""
    rank: int
    atr_period: int
    multiplier: float
    train_objective: float
    robust_score: float
    stress_pass: bool

    # Train metrics
    train_sum_pnl_pct: Optional[float] = None
    train_sortino: Optional[float] = None
    train_num_trades: Optional[int] = None
    train_sharpe: Optional[float] = None
    train_cagr: Optional[float] = None

    # OOS metrics (filled later)
    oos_sortino: Optional[float] = None
    oos_sum_pnl_pct: Optional[float] = None
    oos_max_dd_pct: Optional[float] = None
    oos_num_trades: Optional[int] = None
    oos_win_rate: Optional[float] = None
    oos_profit_factor: Optional[float] = None
    oos_avg_trade: Optional[float] = None

    # OOS raw arrays — retained only when retain_oos_arrays=True is passed to
    # _fill_oos_metrics_for_topk(). Used by the diversification filter.
    # Default None: zero memory overhead when diversification is disabled.
    oos_returns: Optional[np.ndarray] = None
    oos_positions: Optional[np.ndarray] = None

    in_consensus: bool = False


def _is_step_skipped(step: 'WFStepResult') -> bool:
    """Return True if step was skipped (no valid trials, OOS backtest not run)."""
    return step.best_atr_period is None or step.best_multiplier is None


def _is_step_valid_for_consensus(step: 'WFStepResult') -> bool:
    """Return True if step has valid top_entries for coverage/consensus."""
    return len(step.top_entries) > 0


@dataclass
class WFStepResult:
    """Result from a single Walk-Forward step."""
    step_index: int

    train_start_idx: int
    train_end_idx: int
    test_start_idx: int
    test_end_idx: int

    train_start_time: Optional[pd.Timestamp] = None
    train_end_time: Optional[pd.Timestamp] = None
    test_start_time: Optional[pd.Timestamp] = None
    test_end_time: Optional[pd.Timestamp] = None

    best_atr_period: Optional[int] = None
    best_multiplier: Optional[float] = None

    train_objective_value: Optional[float] = None
    train_metrics: Dict[str, float] = field(default_factory=dict)

    test_objective_value: Optional[float] = None
    test_metrics: Dict[str, float] = field(default_factory=dict)

    test_returns: Optional[np.ndarray] = None
    test_trades_df: Optional[pd.DataFrame] = None
    test_early_exit: Optional[bool] = None

    # TRAIN trades for best params (per-step); None when step is SKIPPED
    train_trades_df: Optional[pd.DataFrame] = None

    # TOP-K data (added in PR2)
    top_entries: List[WFStepTopEntry] = field(default_factory=list)
    passed_count: int = 0
    top_export_count: int = 0
    top_consensus_count: int = 0

    # Step status: "OK" | "SKIPPED" (no valid trials, OOS backtest not run)
    step_status: str = "OK"

    # Ref-pass: OOS backtest for consensus selected params (display-only, never alters selection)
    ref_atr_period: Optional[int] = None
    ref_multiplier: Optional[float] = None
    ref_test_metrics: Optional[Dict[str, Any]] = None
    # Ref-pass: TRAIN backtest for consensus selected params (display-only)
    ref_train_metrics: Optional[Dict[str, Any]] = None

    # MiniGrid Refine metadata (Stage 2); None when refine is disabled or not run
    refine_meta: Optional[Dict[str, Any]] = None

    # MiniGrid Refine — explicit fields (Stage 3); all Optional for backward compat
    refine_enabled: bool = False
    refine_grid_total: Optional[int] = None
    refine_grid_valid: Optional[int] = None
    refine_grid_early_exit: Optional[int] = None
    refine_plateau_width: Optional[float] = None
    refine_plateau_depth: Optional[float] = None
    refine_plateau_monotonicity: Optional[float] = None
    refine_best_changed: Optional[bool] = None


@dataclass
class WalkForwardResult:
    """Aggregated Walk-Forward results."""
    steps: List[WFStepResult]
    oos_metrics: Dict[str, float] = field(default_factory=dict)
    oos_trades_df: Optional[pd.DataFrame] = None
    train_trades_df: Optional[pd.DataFrame] = None  # TRAIN trades (per-step best params)
    oos_returns: Optional[np.ndarray] = None
    oos_equity_curve: Optional[np.ndarray] = None
    gates_result: Optional['WalkForwardGatesResult'] = None

    # Consensus data (added in PR2)
    consensus_df: Optional[pd.DataFrame] = None
    total_steps: int = 0
    valid_step_count: int = 0  # Steps with top_entries (excludes skipped)
    skipped_step_count: int = 0  # total_steps - valid_step_count
    skipped_step_ratio: float = 0.0  # skipped / total (0.0 if total==0)

    # Mini-Grid data (Phase 1)
    mini_grid_state: Optional['MiniGridState'] = None
    mini_grid_eval_counters: Optional['GridEvaluationCounters'] = None
    mini_grid_trials: Optional[pd.DataFrame] = None  # All MiniGrid evaluation results (step_id, atr_period, multiplier, metrics...)

    # Surface layer: all evaluated train trials across ALL steps (discovery + eval + legacy).
    # Columns: step_id, atr_period, multiplier + train metrics (sum_pnl_pct, sortino, …,
    # robust_score when available).  Used to build full-surface bucket coverage metrics.
    # None when save_all_trials=False or no trial data was collected.
    all_step_trials: Optional[pd.DataFrame] = None

    # Selected params for WF summary (after consensus/mini-grid)
    # Explicitly shows which (atr_period, multiplier) was chosen for the final WF run
    selected_atr_period: Optional[int] = None
    selected_multiplier: Optional[float] = None
    selected_rank: Optional[int] = None
    selected_coverage_count: Optional[int] = None
    selected_coverage_ratio: Optional[float] = None
    selected_source: str = "unknown"  # "consensus" | "unknown"

    # MiniGrid Refine aggregates (Stage 3) — None when no refine steps
    refine_plateau_width_mean: Optional[float] = None
    refine_plateau_width_min: Optional[float] = None
    refine_grid_size_avg: Optional[float] = None
    refine_best_changed_count: Optional[int] = None

    # Canonical top-1 params used for Ref table (step.ref_*); None when fallback to consensus
    canonical_atr_period: Optional[int] = None
    canonical_multiplier: Optional[float] = None


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2: Mini-Grid Helper Functions
# ══════════════════════════════════════════════════════════════════════════


def is_mini_grid_enabled(config: Dict[str, Any]) -> bool:
    """
    Check if mini_grid mode is enabled in configuration.
    
    Parameters
    ----------
    config : Dict[str, Any]
        Full configuration dictionary
        
    Returns
    -------
    bool
        True if walk_forward.mini_grid.enabled == true
    """
    wf_cfg = config.get("walk_forward", {})
    mini_grid_cfg = wf_cfg.get("mini_grid", {})
    return mini_grid_cfg.get("enabled", False)


def get_effective_mult_step(config: Dict[str, Any]) -> float:
    """
    Get effective multiplier step for mini-grid.
    
    Priority:
    1. walk_forward.mini_grid.mult_step (if explicitly set)
    2. optimization.multiplier_step (default inheritance)
    
    Parameters
    ----------
    config : Dict[str, Any]
        Full configuration dictionary
        
    Returns
    -------
    float
        Effective mult_step value
        
    Raises
    ------
    ConfigError
        If explicit mult_step conflicts with optimization.multiplier_step
    """
    from supertrend_optimizer.utils.exceptions import ConfigError
    
    opt_mult_step = config.get("optimization", {}).get("multiplier_step", 0.1)
    mg_cfg = config.get("walk_forward", {}).get("mini_grid", {})
    
    if "mult_step" in mg_cfg:
        explicit_step = mg_cfg["mult_step"]
        if abs(explicit_step - opt_mult_step) > 1e-9:
            raise ConfigError(
                f"mini_grid.mult_step ({explicit_step}) conflicts with "
                f"optimization.multiplier_step ({opt_mult_step}). "
                f"Remove mini_grid.mult_step to inherit automatically, "
                f"or ensure they match."
            )
        return explicit_step
    
    return opt_mult_step


# quantize_multiplier is imported from supertrend_optimizer.utils.math_utils
# (single source of truth — removed local duplicate)


# ══════════════════════════════════════════════════════════════════════════
# PHASE 1 + 2 — MiniGrid v1.0 Seed Pipeline
# ══════════════════════════════════════════════════════════════════════════

_DISCOVERY_MODES = ("first_only", "first_k", "explicit", "all")


from dataclasses import dataclass as _dataclass, field as _field


@_dataclass
class MergedSeed:
    """
    A single aggregated seed produced by merge_seeds_across_steps().

    Phase 1 (single-step): appear_count=1, score_mean==score_min==step_score.
    Phase 2 (multi-step): appear_count reflects how many discovery steps
    surfaced this seed.  score_mean / score_min aggregate across steps.
    """
    atr_period: int
    mult_ticks: int           # int(round(multiplier / mult_step))
    multiplier: float         # canonical: mult_ticks * mult_step

    appear_count: int         # Number of discovery steps that surfaced this seed
    score_mean: float         # Mean score across steps (raw or normalized)
    score_min: float          # Min  score across steps (raw or normalized)
    source_steps: List[int]   # Discovery step indices that contributed

    # Phase 2 normalization support (None when normalization is off)
    raw_score_mean: Optional[float] = _field(default=None)
    raw_score_min: Optional[float] = _field(default=None)
    normalized: bool = _field(default=False)  # True when score_mean is percentile


def extract_step_seeds(
    opt_result,
    config: Dict[str, Any],
    step_index: int = 0,
) -> List[Dict[str, Any]]:
    """
    Extract unique seeds from a single optimization result (Phase 1, C.1).

    Algorithm
    ---------
    1. Source selection (mirrors build_mini_grid seed-source logic):
       a. Use robustness_df filtered by stress_pass=True when available.
       b. Fallback to trials_df if stress_pass filters all or robustness_df absent.
    2. Sort by robust_score DESC (or objective_metric when no robust_score),
       then atr_period ASC, then mult_ticks ASC (deterministic tie-break).
    3. Deduplicate by (atr_period, mult_ticks) — keep best score.
    4. Return up to ``top_m`` unique seeds.

    Parameters
    ----------
    opt_result : OptimizationResult
        Result from one WF step's optimization.
    config : Dict[str, Any]
        Full configuration dictionary.
    step_index : int
        WF step index (for logging / source_steps tracking).

    Returns
    -------
    List[Dict[str, Any]]
        List of seed dicts with keys:
        atr_period, mult_ticks, multiplier, step_score, step_index.
        Ordered: best-first, up to top_m unique seeds.
    """
    logger = logging.getLogger(__name__)

    mg_cfg = config.get("walk_forward", {}).get("mini_grid", {})
    top_n_seeds = mg_cfg.get("top_n_seeds", 15)
    top_m = mg_cfg.get("top_m_per_step") or (top_n_seeds * 3)
    mult_step = get_effective_mult_step(config)
    objective_metric = config.get("optimization", {}).get("objective_metric", "sortino")

    # --- choose source (same priority as build_mini_grid) ---
    robustness_df: Optional[pd.DataFrame] = getattr(opt_result, "robustness_df", None)
    trials_df: Optional[pd.DataFrame] = getattr(opt_result, "trials_df", None)

    df: Optional[pd.DataFrame] = None
    score_col: str = "robust_score"

    if robustness_df is not None and len(robustness_df) > 0:
        source = robustness_df.copy()
        if "stress_pass" in source.columns:
            passed = source[source["stress_pass"] == True]
        else:
            passed = source
        if len(passed) > 0:
            df = passed
            score_col = "robust_score" if "robust_score" in df.columns else objective_metric
        else:
            # stress_pass filtered all → fallback
            logger.warning(
                "extract_step_seeds step=%d: all trials filtered by stress_pass=True, "
                "falling back to trials_df", step_index,
            )
            df = trials_df.copy() if (trials_df is not None and len(trials_df) > 0) else None
            score_col = "robust_score" if (df is not None and "robust_score" in df.columns) else objective_metric
    else:
        df = trials_df.copy() if (trials_df is not None and len(trials_df) > 0) else None
        score_col = "robust_score" if (df is not None and "robust_score" in df.columns) else objective_metric

    if df is None or len(df) == 0:
        logger.warning(
            "extract_step_seeds step=%d: no valid source data → 0 seeds", step_index
        )
        return []

    # Use robust_score when available, else fall back to objective_metric
    score_col = "robust_score" if "robust_score" in df.columns else objective_metric

    # Drop rows where score is NaN
    df = df.dropna(subset=[score_col])
    if len(df) == 0:
        logger.warning(
            "extract_step_seeds step=%d: all scores NaN → 0 seeds", step_index
        )
        return []

    # Compute mult_ticks (canonical, integer)
    df = df.copy()
    df["_mult_ticks"] = (df["multiplier"] / mult_step).round().astype(int)

    # Sort: robust_score DESC, atr_period ASC, mult_ticks ASC (stable tie-break)
    df = df.sort_values(
        by=[score_col, "atr_period", "_mult_ticks"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)

    # Deduplicate by (atr_period, mult_ticks) — keep first (best score)
    df = df.drop_duplicates(subset=["atr_period", "_mult_ticks"], keep="first")

    # Limit to top_m
    df = df.head(top_m)

    seeds = []
    for _, row in df.iterrows():
        mt = int(row["_mult_ticks"])
        seeds.append(
            {
                "atr_period": int(row["atr_period"]),
                "mult_ticks": mt,
                "multiplier": round(mt * mult_step, 6),
                "step_score": float(row[score_col]),
                "step_index": step_index,
            }
        )

    logger.debug(
        "extract_step_seeds step=%d: %d unique seeds (top_m=%d, score_col=%s)",
        step_index, len(seeds), top_m, score_col,
    )
    return seeds


def merge_seeds_across_steps(
    step_seeds_list: List[List[Dict[str, Any]]],
    top_n: int,
    mult_step: float,
    normalize: bool = False,
) -> List[MergedSeed]:
    """
    Merge seed lists from one or more WF steps (Phase 1 + Phase 2, C.3).

    Phase 1 (single-step)
    ---------------------
    * step_seeds_list has exactly one element.
    * appear_count = 1 for all seeds.
    * score_mean == score_min == step_score.
    * Ranking: step_score DESC → atr_period ASC → mult_ticks ASC.

    Phase 2 (multi-step)
    --------------------
    * Aggregation key: (atr_period, mult_ticks).
    * Ranking: appear_count DESC → score_mean DESC → score_min DESC
               → atr_period ASC → mult_ticks ASC (stable).
    * When normalize=True, per-step rank-percentile normalization is applied
      before aggregation to prevent metric-scale dominance across steps.

    Parameters
    ----------
    step_seeds_list : List[List[Dict]]
        One list of seeds per WF step (from extract_step_seeds).
    top_n : int
        Maximum number of merged seeds to return.
    mult_step : float
        Multiplier step (used to reconstruct canonical multiplier).
    normalize : bool
        When True, apply per-step rank-percentile normalization before
        aggregating scores (STEP 4).

    Returns
    -------
    List[MergedSeed]
        Merged seeds, ranked, up to top_n.
    """
    logger = logging.getLogger(__name__)

    # Phase 2: optional per-step normalization
    if normalize and len(step_seeds_list) > 1:
        step_seeds_list = _apply_score_normalization(step_seeds_list)
        logger.debug("merge_seeds_across_steps: per-step score normalization applied")

    # Flatten and aggregate by (atr_period, mult_ticks)
    agg: Dict[tuple, Dict[str, Any]] = {}
    for step_seeds in step_seeds_list:
        for s in step_seeds:
            key = (s["atr_period"], s["mult_ticks"])
            if key not in agg:
                agg[key] = {
                    "atr_period": s["atr_period"],
                    "mult_ticks": s["mult_ticks"],
                    "multiplier": s["multiplier"],
                    "scores": [],
                    "raw_scores": [],
                    "source_steps": [],
                }
            agg[key]["scores"].append(s["step_score"])
            # Preserve raw_score when normalization is on
            if "raw_score" in s:
                agg[key]["raw_scores"].append(s["raw_score"])
            if s["step_index"] not in agg[key]["source_steps"]:
                agg[key]["source_steps"].append(s["step_index"])

    multi_step = len(step_seeds_list) > 1

    merged: List[MergedSeed] = []
    for (atr, mt), data in agg.items():
        scores = data["scores"]
        raw_scores = data["raw_scores"]
        has_raw = len(raw_scores) == len(scores)

        ms = MergedSeed(
            atr_period=atr,
            mult_ticks=mt,
            multiplier=data["multiplier"],
            appear_count=len(data["source_steps"]),
            score_mean=float(np.mean(scores)),
            score_min=float(np.min(scores)),
            source_steps=sorted(data["source_steps"]),
            raw_score_mean=float(np.mean(raw_scores)) if has_raw else None,
            raw_score_min=float(np.min(raw_scores)) if has_raw else None,
            normalized=normalize and multi_step,
        )
        merged.append(ms)

    if not multi_step:
        # Phase 1 / single-step: step_score DESC → atr ASC → mult_ticks ASC
        merged.sort(key=lambda s: (-s.score_mean, s.atr_period, s.mult_ticks))
    else:
        # Phase 2: appear_count DESC → score_mean DESC → score_min DESC → atr ASC → mult_ticks ASC
        merged.sort(
            key=lambda s: (
                -s.appear_count,
                -s.score_mean,
                -s.score_min,
                s.atr_period,
                s.mult_ticks,
            )
        )

    return merged[:top_n]


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2: Discovery mode resolution
# ══════════════════════════════════════════════════════════════════════════


def resolve_discovery_steps(
    total_wf_steps: int,
    mg_cfg: Dict[str, Any],
) -> List[int]:
    """
    Resolve the list of WF step indices to use for discovery (Phase 2, STEP 1).

    Mode semantics
    --------------
    first_only  — only step 0  (default / Phase 1 fallback)
    first_k     — steps 0 .. min(first_k, total_wf_steps) - 1
    explicit    — explicit list from mg_cfg["discovery"]["steps"]
    all         — all available steps 0 .. total_wf_steps - 1

    Rules
    -----
    * Result is always sorted ascending (deterministic).
    * Indices are clamped to [0, total_wf_steps - 1].
    * If resolved list has length 1 → logs INFO (single-step fallback).
    * total_wf_steps must be >= 1.

    Parameters
    ----------
    total_wf_steps : int
        Number of WF steps available (len(windows)).
    mg_cfg : Dict[str, Any]
        walk_forward.mini_grid config section.

    Returns
    -------
    List[int]
        Sorted list of step indices to use for discovery.

    Raises
    ------
    ValueError
        If mode is unrecognised or explicit steps list is invalid.
    """
    logger = logging.getLogger(__name__)

    disc_cfg = mg_cfg.get("discovery", {})
    mode: str = disc_cfg.get("mode", "first_only")

    if mode not in _DISCOVERY_MODES:
        raise ValueError(
            f"walk_forward.mini_grid.discovery.mode must be one of "
            f"{_DISCOVERY_MODES}, got: '{mode}'"
        )

    if total_wf_steps < 1:
        return [0]  # degenerate safeguard

    if mode == "first_only":
        steps = [0]

    elif mode == "first_k":
        k: int = disc_cfg.get("first_k", 3)
        k = max(1, min(int(k), total_wf_steps))
        steps = list(range(k))

    elif mode == "explicit":
        raw: List[int] = disc_cfg.get("steps", [])
        if not raw:
            logger.warning(
                "discovery.mode='explicit' but steps=[] → fallback to first_only"
            )
            steps = [0]
        else:
            # Clamp, deduplicate, sort
            steps = sorted({max(0, min(int(i), total_wf_steps - 1)) for i in raw})

    else:  # "all"
        steps = list(range(total_wf_steps))

    if len(steps) == 1:
        logger.info(
            "MiniGrid discovery uses single step (step=%d); appear_count=1 for all seeds. "
            "Enable walk_forward.mini_grid.discovery.* for multi-step mode.",
            steps[0],
        )
    else:
        logger.info(
            "MiniGrid discovery using %d steps (mode=%s): %s",
            len(steps), mode, steps,
        )

    return steps


def _apply_score_normalization(
    step_seeds_list: List[List[Dict[str, Any]]],
) -> List[List[Dict[str, Any]]]:
    """
    Apply per-step rank-percentile normalization to step_score (STEP 4).

    For each step:
      1. Sort seeds by step_score DESC.
      2. Assign pct = (rank - 1) / max(n - 1, 1)   (rank is 1-based).
      3. Replace step_score with pct and store original in raw_score.

    This prevents a step with a "fatter" metric distribution from
    dominating the multi-step ranking.

    Parameters
    ----------
    step_seeds_list : List[List[Dict]]
        Per-step seed lists (from extract_step_seeds).

    Returns
    -------
    List[List[Dict]]
        Same structure with step_score replaced by percentile rank [0..1]
        and raw_score added.
    """
    normalized: List[List[Dict[str, Any]]] = []
    for step_seeds in step_seeds_list:
        if not step_seeds:
            normalized.append([])
            continue

        n = len(step_seeds)
        # Sort DESC by step_score for rank assignment
        sorted_seeds = sorted(step_seeds, key=lambda s: -s["step_score"])
        new_seeds: List[Dict[str, Any]] = []
        for rank_0based, seed in enumerate(sorted_seeds):
            pct = rank_0based / max(n - 1, 1)  # rank=0 → pct=1.0, rank=n-1 → pct=0.0
            # Best seed gets pct=0, worst gets pct=1 → we want higher pct = better
            # Use 1 - pct so that rank=0 (best) → normalized_score=1.0
            norm_score = 1.0 - pct
            new_seed = dict(seed)
            new_seed["raw_score"] = seed["step_score"]
            new_seed["step_score"] = round(norm_score, 6)
            new_seeds.append(new_seed)
        # Re-sort back by normalized step_score DESC, atr ASC, mult_ticks ASC (stable)
        new_seeds.sort(key=lambda s: (-s["step_score"], s["atr_period"], s["mult_ticks"]))
        normalized.append(new_seeds)
    return normalized


def _filter_merged_seeds(
    merged: List["MergedSeed"],
    mg_cfg: Dict[str, Any],
) -> List["MergedSeed"]:
    """
    Safety gate: filter out low-quality merged seeds before build_mini_grid.

    Two filters (both optional / feature-flag controlled):
    1. score_min <= INVALID_METRIC_VALUE + EPS  → always applied (silent bad seeds).
    2. appear_count < min_appear_count           → only when config key is set (int >= 1).

    Parameters
    ----------
    merged : List[MergedSeed]
        Merged seeds from merge_seeds_across_steps().
    mg_cfg : Dict[str, Any]
        walk_forward.mini_grid config section.

    Returns
    -------
    List[MergedSeed]
        Filtered list (order preserved).
    """
    _log = logging.getLogger(__name__)
    before = len(merged)

    invalid_threshold = INVALID_METRIC_VALUE + EPS
    filtered = [s for s in merged if s.score_min > invalid_threshold]
    n_invalid = before - len(filtered)

    min_appear: Optional[int] = mg_cfg.get("discovery", {}).get("min_appear_count", None)
    n_appear_filtered = 0
    if min_appear is not None and min_appear >= 1:
        prev = len(filtered)
        filtered = [s for s in filtered if s.appear_count >= min_appear]
        n_appear_filtered = prev - len(filtered)

    if n_invalid > 0 or n_appear_filtered > 0:
        _log.info(
            "MiniGrid safety gate: removed %d invalid-score seeds, "
            "%d below min_appear_count=%s; %d seeds remain",
            n_invalid, n_appear_filtered, min_appear, len(filtered),
        )

    return filtered


# ══════════════════════════════════════════════════════════════════════════
# PHASE 3: Mini-Grid Builder
# ══════════════════════════════════════════════════════════════════════════


def build_mini_grid(
    opt_result,
    config: Dict[str, Any],
    source_step: int = 0,
    merged_seeds: Optional[List["MergedSeed"]] = None,
) -> MiniGridState:
    """
    Build mini-grid from TOP-N results of discovery step (Phase 3).

    Phase 1 (single-step) flow
    --------------------------
    1. Call extract_step_seeds(opt_result, config, step_index=source_step)
    2. Call merge_seeds_across_steps([step_seeds], top_n=top_n_seeds, mult_step)
       → appear_count=1, score_mean==score_min==step_score for all seeds
    3. For each merged seed, expand ± delta with step
    4. Quantize all multipliers (float-safe dedup)
    5. Deduplicate via set of (atr_period, mult_quantized)
    6. Clamp to valid search space
    7. Apply truncation if > max_grid_size (deterministic)
    8. Return MiniGridState with full metadata

    Phase 2 (multi-step) accepts pre-built ``merged_seeds`` directly.

    ПРАВКА 1: Seeds selection fallback if stress_pass filters all
    ПРАВКА 2: Seeds sorted by same ranking_metric as WF TOP-K
    ПРАВКА 3: mult_delta uses ceil() for full coverage
    ПРАВКА 4: Truncation with full tie-breakers for determinism

    Parameters
    ----------
    opt_result : OptimizationResult
        Result from discovery step optimization (used only when merged_seeds is None)
    config : Dict[str, Any]
        Full configuration dictionary
    source_step : int, optional
        Index of the WF step used for discovery (usually 0)
    merged_seeds : List[MergedSeed], optional
        Pre-merged seeds (for Phase 2 multi-step mode).
        When None, Phase 1 single-step pipeline is used.

    Returns
    -------
    MiniGridState
        Complete mini-grid state with params and metadata
    """
    import math

    logger = logging.getLogger(__name__)

    # ═══ STEP 1: Extract config ═══
    mg_cfg = config.get("walk_forward", {}).get("mini_grid", {})
    opt_cfg = config.get("optimization", {})

    top_n_seeds = mg_cfg.get("top_n_seeds", 15)
    atr_delta = mg_cfg.get("atr_delta", 3)
    mult_delta = mg_cfg.get("mult_delta", 0.3)
    atr_step = mg_cfg.get("atr_step", 1)
    mult_step = get_effective_mult_step(config)
    clamp = mg_cfg.get("clamp_to_search_space", True)
    max_grid_size = mg_cfg.get("max_grid_size")
    truncate_policy = mg_cfg.get("truncate_policy", "best_seed_priority")

    atr_min, atr_max = opt_cfg.get("atr_period_range", [5, 60])
    mult_min, mult_max = opt_cfg.get("multiplier_range", [2.0, 6.0])

    # ═══ STEP 2: Obtain merged seeds ═══
    if merged_seeds is None:
        # Phase 1: single-step pipeline
        step_seeds = extract_step_seeds(opt_result, config, step_index=source_step)
        merged_seeds = merge_seeds_across_steps(
            [step_seeds], top_n=top_n_seeds, mult_step=mult_step
        )

    if len(merged_seeds) == 0:
        logger.warning("No valid seeds available for mini-grid")
        return MiniGridState(
            params=[],
            source_step=source_step,
            seed_params=[],
            seed_source="empty",
            merged_seeds=[],
            grid_size_before_truncate=0,
            grid_size_after_truncate=0,
            truncated=False,
            truncate_policy_used=None,
            config_snapshot=_build_config_snapshot(mg_cfg, mult_step),
        )

    # Derive seed_source for backward compatibility
    # If trials_df was the only source → "trials_df_fallback"; if robustness_df was used → "robustness_df"
    robustness_df = getattr(opt_result, "robustness_df", None)
    if robustness_df is not None and len(robustness_df) > 0:
        # Check if any passed stress
        stress_pass_col = "stress_pass" if "stress_pass" in robustness_df.columns else None
        if stress_pass_col and (robustness_df[stress_pass_col] == True).any():
            seed_source = "robustness_df"
        else:
            seed_source = "trials_df_fallback"
    else:
        trials_df = getattr(opt_result, "trials_df", None)
        seed_source = "trials_df_fallback" if (trials_df is not None and len(trials_df) > 0) else "empty"

    # Convert MergedSeed list → legacy seed_params list[(atr, mult_q)]
    seed_params = [(s.atr_period, s.multiplier) for s in merged_seeds]

    # ═══ STEP 3: Calculate expansion range (ПРАВКА 3: ceil for coverage) ═══
    mult_steps_count = math.ceil(mult_delta / mult_step)

    logger.debug(
        "Mini-grid expansion: atr ±%d (step=%d), mult ±%.2f (step=%s, requested_delta=%s)",
        atr_delta, atr_step, mult_steps_count * mult_step, mult_step, mult_delta,
    )

    # ═══ STEP 4: Expand seeds + Quantize + Deduplicate ═══
    all_points_set: set = set()
    seed_to_points: Dict[tuple, List[tuple]] = {}  # seed_key → List[(atr, mult_q, distance)]

    for seed_rank, (seed_atr, seed_mult) in enumerate(seed_params):
        expanded = []

        for atr_offset in range(-atr_delta, atr_delta + 1, atr_step):
            atr = seed_atr + atr_offset

            for mult_offset_steps in range(-mult_steps_count, mult_steps_count + 1):
                mult = seed_mult + mult_offset_steps * mult_step
                mult_q = quantize_multiplier(mult, mult_step)

                if clamp:
                    if atr < atr_min or atr > atr_max:
                        continue
                    if mult_q < mult_min or mult_q > mult_max:
                        continue

                distance = abs(atr_offset // atr_step) + abs(mult_offset_steps)

                point = (atr, mult_q)
                if point not in all_points_set:
                    all_points_set.add(point)
                    expanded.append((atr, mult_q, distance))

        seed_to_points[(seed_atr, seed_mult)] = expanded

    grid_size_before = len(all_points_set)

    # ═══ STEP 5: Truncation (ПРАВКА 4: deterministic tie-breakers) ═══
    truncated = False
    policy_used = None

    if max_grid_size is not None and grid_size_before > max_grid_size:
        truncated = True
        policy_used = truncate_policy

        if truncate_policy == "best_seed_priority":
            final_grid: List[tuple] = []
            seen: set = set()

            for seed_rank, seed_key in enumerate(seed_params):
                if seed_key not in seed_to_points:
                    continue
                points = sorted(seed_to_points[seed_key], key=lambda x: x[2])

                for atr, mult_q, dist in points:
                    if len(final_grid) >= max_grid_size:
                        break
                    key = (atr, mult_q)
                    if key not in seen:
                        seen.add(key)
                        final_grid.append(key)

                if len(final_grid) >= max_grid_size:
                    break

            all_points_set = set(final_grid)

        elif truncate_policy == "center_priority":
            all_with_meta: List[tuple] = []
            seen = set()

            for seed_rank, seed_key in enumerate(seed_params):
                if seed_key not in seed_to_points:
                    continue
                for atr, mult_q, dist in seed_to_points[seed_key]:
                    key = (atr, mult_q)
                    if key not in seen:
                        seen.add(key)
                        all_with_meta.append((dist, seed_rank, atr, mult_q))

            all_with_meta.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
            final_grid = [(atr, mult_q) for _, _, atr, mult_q in all_with_meta[:max_grid_size]]
            all_points_set = set(final_grid)

    # ═══ STEP 6: Build result ═══
    final_params = sorted(list(all_points_set), key=lambda x: (x[0], x[1]))

    logger.info(
        "Mini-grid built: %d params (seeds: %d, seed_source: %s, "
        "before_truncate: %d, truncated: %s)",
        len(final_params), len(seed_params), seed_source, grid_size_before, truncated,
    )

    return MiniGridState(
        params=final_params,
        source_step=source_step,
        seed_params=seed_params,
        seed_source=seed_source,
        merged_seeds=merged_seeds,
        grid_size_before_truncate=grid_size_before,
        grid_size_after_truncate=len(final_params),
        truncated=truncated,
        truncate_policy_used=policy_used,
        config_snapshot=_build_config_snapshot(mg_cfg, mult_step),
    )


def _build_config_snapshot(mg_cfg: Dict, effective_mult_step: float) -> Dict:
    """Build config snapshot for reproducibility."""
    return {
        "top_n_seeds": mg_cfg.get("top_n_seeds", 15),
        "atr_delta": mg_cfg.get("atr_delta", 3),
        "mult_delta": mg_cfg.get("mult_delta", 0.3),
        "atr_step": mg_cfg.get("atr_step", 1),
        "mult_step": effective_mult_step,
        "clamp_to_search_space": mg_cfg.get("clamp_to_search_space", True),
        "max_grid_size": mg_cfg.get("max_grid_size"),
        "truncate_policy": mg_cfg.get("truncate_policy", "best_seed_priority")
    }


def evaluate_mini_grid(
    param_grid: List[tuple],
    open_prices: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    index: pd.Index,
    config: Dict[str, Any]
):
    """
    Evaluate mini-grid for WF steps 1+ (Phase 5 wrapper).
    
    Wrapper around grid_search.evaluate_parameter_grid() that:
    1. Creates ATR cache for unique periods in grid (ПРАВКА 7)
    2. Calls evaluate_parameter_grid()
    3. Calculates robust_score for ALL mini-grid points (not just TOP-K)
    4. Returns OptimizationResult compatible with WF pipeline
    
    Parameters
    ----------
    param_grid : List[tuple]
        List of (atr_period, multiplier_quantized) from mini_grid_state.params
    open_prices, high, low, close : np.ndarray
        Price data arrays
    index : pd.Index
        Time index
    config : Dict[str, Any]
        Full configuration
        
    Returns
    -------
    OptimizationResult
        Result compatible with existing WF pipeline, with robust_score added to trials_df
    """
    from supertrend_optimizer.robustness.plateau import calculate_plateau_scores
    from supertrend_optimizer.robustness.stress import run_stress_tests
    from supertrend_optimizer.robustness.scoring import calculate_robust_score
    from supertrend_optimizer.engine.run import run_single_backtest
    
    logger = logging.getLogger(__name__)
    
    if len(param_grid) == 0:
        logger.warning("Mini-grid evaluation: empty param_grid")
    
    # ПРАВКА 7: Create ATR cache for unique periods only
    unique_atr_periods = sorted(set(atr for atr, _ in param_grid)) if param_grid else []
    
    atr_cache = {}
    if unique_atr_periods:
        logger.debug(
            f"Creating ATR cache for {len(unique_atr_periods)} unique periods: "
            f"{unique_atr_periods[:5]}{'...' if len(unique_atr_periods) > 5 else ''}"
        )
        
        for period in unique_atr_periods:
            atr_cache[period] = create_atr_cache(
                high=high,
                low=low,
                close=close,
                atr_period_min=period,
                atr_period_max=period
            )[period]
    
    # Call grid search
    opt_result = evaluate_parameter_grid(
        param_grid=param_grid,
        open_prices=open_prices,
        high=high,
        low=low,
        close=close,
        index=index,
        config=config,
        atr_cache=atr_cache
    )
    
    # === NEW: Calculate robust_score for ALL mini-grid points ===
    robustness_cfg = config.get("robustness", {})
    robustness_enabled = robustness_cfg.get("enabled", False)
    objective_metric = config.get("optimization", {}).get("objective_metric", "sortino")
    
    if robustness_enabled and opt_result.trials_df is not None and len(opt_result.trials_df) > 0:
        logger.debug(f"Calculating robust_score for {len(opt_result.trials_df)} mini-grid points")
        
        trials_df = opt_result.trials_df.copy()
        
        # Filter valid trials (objective metric not NaN/INVALID)
        valid_trials = trials_df[
            (trials_df[objective_metric].notna()) &
            (~np.isclose(trials_df[objective_metric], INVALID_METRIC_VALUE))
        ].copy()
        
        if len(valid_trials) > 0:
            # Calculate plateau scores for ALL trials (not just TOP-K)
            plateau_k_neighbors = robustness_cfg.get("plateau_k_neighbors", 3)
            plateau_df = calculate_plateau_scores(
                top_k_df=valid_trials,  # Pass ALL trials as "top_k"
                all_trials_df=valid_trials,
                metric_col=objective_metric,
                k_neighbors=plateau_k_neighbors
            )
            
            # Run stress tests for ALL trials
            bt_cfg = config.get("backtest", {})
            opt_cfg = config.get("optimization", {})
            val_cfg = config.get("validation", {})
            
            # Resolve periods_per_year
            from supertrend_optimizer.data.timeframe import resolve_periods_per_year_from_config
            ann_factor = bt_cfg.get("annualization_factor", 252)
            _wf_market_str = bt_cfg.get("market", None)
            _wf_explicit_basis = bt_cfg.get("annualization_basis", None)
            _wf_market_enum = MarketType(_wf_market_str) if _wf_market_str is not None else None
            if isinstance(ann_factor, str) and ann_factor == "auto":
                if isinstance(index, pd.DatetimeIndex):
                    periods_per_year = resolve_periods_per_year_from_config(
                        config_value=ann_factor,
                        index=index,
                        explicit_basis=_wf_explicit_basis,
                        market=_wf_market_enum,
                    )
                else:
                    periods_per_year = 252.0
            else:
                periods_per_year = float(ann_factor)
            
            # Resolve execution_model
            from supertrend_optimizer.utils.enums import ExecutionModel
            exec_model_val = bt_cfg.get("execution_model")
            execution_model = (
                ExecutionModel(exec_model_val)
                if exec_model_val is not None
                else ExecutionModel.OPEN_TO_OPEN
            )
            
            # Prepare backtest kwargs (without commission - will be set by stress test)
            backtest_kwargs = {
                "open_prices": open_prices,
                "high": high,
                "low": low,
                "close": close,
                "index": index,
                "trade_mode": opt_cfg.get("trade_mode", "revers"),
                "warmup_period": val_cfg.get("warmup_period", 0),
                "early_exit_enabled": bt_cfg.get("early_exit_enabled", False),
                "early_exit_max_drawdown": bt_cfg.get("early_exit_max_drawdown", 0.5),
                "early_exit_check_bars": bt_cfg.get("early_exit_check_bars", 0),
                "periods_per_year": periods_per_year,
                "min_trades_required": bt_cfg.get("min_trades_required", 3),
                "extract_trades_flag": False,
                "caller_mode": "optimizer",
                "execution_model": execution_model,
                "auto_warmup": True,
            }
            
            stress_multipliers = robustness_cfg.get("stress_multipliers", [1.0, 1.3])
            stress_pass_max_drop_pct = robustness_cfg.get("stress_pass_max_drop_pct", 30.0)
            
            stress_df = run_stress_tests(
                top_k_df=plateau_df,
                objective_metric=objective_metric,
                base_value_col=objective_metric,
                stress_multipliers=stress_multipliers,
                base_commission=bt_cfg.get("commission", 0.0),
                stress_pass_max_drop_pct=stress_pass_max_drop_pct,
                run_single_backtest_fn=run_single_backtest,
                backtest_kwargs=backtest_kwargs
            )
            
            # Calculate robust_score for ALL trials
            robust_w1 = robustness_cfg.get("robust_w1", 1.0)
            robust_w2 = robustness_cfg.get("robust_w2", 0.5)
            
            robust_scores = []
            for idx, row in stress_df.iterrows():
                robust_score = calculate_robust_score(
                    plateau_score=row["plateau_score"],
                    stress_drop_pct=row["stress_drop_pct"],
                    w1=robust_w1,
                    w2=robust_w2
                )
                robust_scores.append(robust_score)
            
            stress_df["robust_score"] = robust_scores
            
            # Merge robust_score back into trials_df
            # Use left join to preserve all trials (even if some failed stress test)
            trials_df = trials_df.merge(
                stress_df[["atr_period", "multiplier", "robust_score", "plateau_score", "stress_drop_pct", "stress_pass"]],
                on=["atr_period", "multiplier"],
                how="left"
            )
            
            # For trials that didn't get robust_score (invalid objective), set to NaN
            if "robust_score" not in trials_df.columns:
                trials_df["robust_score"] = np.nan
            
            opt_result.trials_df = trials_df
            
            logger.debug(
                f"Added robust_score to {trials_df['robust_score'].notna().sum()} / {len(trials_df)} mini-grid points"
            )
        else:
            # No valid trials - add empty robust_score column
            opt_result.trials_df["robust_score"] = np.nan
            logger.warning("No valid trials for robust_score calculation")
    elif not robustness_enabled:
        # Robustness disabled - use train objective as robust_score
        if opt_result.trials_df is not None:
            opt_result.trials_df["robust_score"] = opt_result.trials_df[objective_metric]
            logger.debug(f"Robustness disabled: using {objective_metric} as robust_score")
    
    return opt_result


# ══════════════════════════════════════════════════════════════════════════
# Stage 2 — MiniGrid Refine
# ══════════════════════════════════════════════════════════════════════════


def run_minigrid_refine(
    opt_result,
    train_open: np.ndarray,
    train_high: np.ndarray,
    train_low: np.ndarray,
    train_close: np.ndarray,
    train_index: "pd.Index",
    config: Dict[str, Any],
    step_idx: int = 0,
):
    """
    Stage 2 MiniGrid Refine — accuracy-first dense refine around the best seed.

    Algorithm
    ---------
    1.  Read config from config["walk_forward"]["mini_grid_refine"].
        If enabled=false → return opt_result unchanged (no-op).
    2.  Select seed (atr, mult) from opt_result using ranking_metric.
    3.  Build a dense ±radius grid around the seed.
        - ATR: integer steps ±atr_radius
        - Mult: ±mult_radius in multiplier_step increments
        - Clamp to optimization ranges
        - Dedup by (atr_period, mult_quantized)
        - Cap at max_grid_points (deterministic: sorted order)
    4.  Evaluate grid on TRAIN data via evaluate_parameter_grid().
        No test data — no leakage.
    5.  Merge refine trials with Optuna trials (if merge_with_optuna=true).
        Dedup key: (atr_period, mult_quantized) — grid wins on duplicates.
    6.  Recalculate robustness on ALL combined valid trials.
        top_k = len(valid_combined) — no pre-filtering.
    7.  Select refined_best = argmax(combined[ranking_metric]).
    8.  Overwrite opt_result.best_* fields so OOS backtest uses refined params.
    9.  Compute plateau metrics (grid points only) and attach to opt_result.

    Critical requirements
    ---------------------
    - opt_result.best_atr_period / best_multiplier / best_value / best_metrics
      are ALWAYS overwritten when a refined_best is found.
    - Robustness always runs on the full combined set (top_k = len(valid)).
    - Grid result wins over Optuna on dedup.
    - No test data is ever touched.

    Parameters
    ----------
    opt_result : OptimizationResult
        Stage 1 result (Optuna or mini-grid eval). Modified in-place and returned.
    train_open, train_high, train_low, train_close : np.ndarray
        TRAIN-only price arrays. Must NOT include test data.
    train_index : pd.Index
        Time index for the train window.
    config : Dict[str, Any]
        Full configuration dictionary.
    step_idx : int
        WF step index (for logging only).

    Returns
    -------
    OptimizationResult
        Same object as opt_result, with best_* fields overwritten if a
        refined best was found.
    """
    import math as _math

    logger = logging.getLogger(__name__)

    # ── 0. Guard: read config, check enabled ──────────────────────────────
    refine_cfg = config.get("walk_forward", {}).get("mini_grid_refine", {})
    if not refine_cfg.get("enabled", False):
        return opt_result

    atr_radius: int = refine_cfg.get("atr_radius", 3)
    mult_radius: float = float(refine_cfg.get("mult_radius", 0.3))
    max_grid_points: int = refine_cfg.get("max_grid_points", 150)
    merge_with_optuna: bool = refine_cfg.get("merge_with_optuna", True)
    plateau_threshold_pct: float = float(refine_cfg.get("plateau_threshold_pct", 10))
    seed_source_cfg: str = refine_cfg.get("seed_source", "optuna")
    metric_for_plateau: str = refine_cfg.get("metric_for_plateau", "objective")

    opt_cfg = config.get("optimization", {})
    robustness_cfg = config.get("robustness", {})
    bt_cfg = config.get("backtest", {})
    val_cfg = config.get("validation", {})

    objective_metric: str = opt_cfg.get("objective_metric", "sortino")
    ranking_metric: str = opt_cfg.get("ranking_metric", "value")
    mult_step: float = get_effective_mult_step(config)
    atr_min, atr_max = opt_cfg.get("atr_period_range", [5, 60])
    mult_min, mult_max = opt_cfg.get("multiplier_range", [2.0, 6.0])
    robustness_enabled: bool = robustness_cfg.get("enabled", False)

    # ── 1. Select seed ────────────────────────────────────────────────────
    # Blueprint §3.1: use ranking_metric source when available
    seed_atr: Optional[int] = None
    seed_mult: Optional[float] = None

    use_ranked = (
        ranking_metric == "robust_score"
        and opt_result.robustness_enabled
        and opt_result.best_ranked_atr_period is not None
    )

    if use_ranked:
        seed_atr = opt_result.best_ranked_atr_period
        seed_mult = opt_result.best_ranked_multiplier
    else:
        seed_atr = opt_result.best_atr_period
        seed_mult = opt_result.best_multiplier

    if seed_atr is None or seed_mult is None:
        logger.warning(
            "Step %d: run_minigrid_refine — no valid seed (best params are None). "
            "Returning opt_result unchanged.",
            step_idx,
        )
        return opt_result

    logger.info(
        "Step %d: MiniGrid Refine START seed=(%d, %.4f) radius=(atr=%d, mult=%.3f) "
        "max_grid=%d merge=%s",
        step_idx, seed_atr, seed_mult, atr_radius, mult_radius,
        max_grid_points, merge_with_optuna,
    )

    # ── 2. Build refine grid ──────────────────────────────────────────────
    mult_steps_count: int = _math.ceil(mult_radius / mult_step) if mult_step > 0 else 0

    refine_points: set = set()
    for atr_offset in range(-atr_radius, atr_radius + 1):
        atr = seed_atr + atr_offset
        if atr < atr_min or atr > atr_max:
            continue
        for mult_offset_steps in range(-mult_steps_count, mult_steps_count + 1):
            mult = seed_mult + mult_offset_steps * mult_step
            mult_q = quantize_multiplier(mult, mult_step)
            if mult_q < mult_min or mult_q > mult_max:
                continue
            refine_points.add((atr, mult_q))

    # Deterministic cap: sort then slice
    refine_grid: List[tuple] = sorted(refine_points)[:max_grid_points]

    logger.debug(
        "Step %d: refine grid size=%d (before cap=%d, cap=%d)",
        step_idx, len(refine_grid), len(refine_points), max_grid_points,
    )

    if len(refine_grid) == 0:
        logger.warning(
            "Step %d: run_minigrid_refine — empty refine grid after clamping. "
            "Returning opt_result unchanged.",
            step_idx,
        )
        return opt_result

    # ── 3. Evaluate refine grid (TRAIN only) ──────────────────────────────
    unique_atr_periods = sorted({atr for atr, _ in refine_grid})
    atr_cache = create_atr_cache(
        high=train_high,
        low=train_low,
        close=train_close,
        atr_period_min=min(unique_atr_periods),
        atr_period_max=max(unique_atr_periods),
    )

    refine_opt = evaluate_parameter_grid(
        param_grid=refine_grid,
        open_prices=train_open,
        high=train_high,
        low=train_low,
        close=train_close,
        index=train_index,
        config=config,
        atr_cache=atr_cache,
    )

    refine_trials_df: Optional["pd.DataFrame"] = getattr(refine_opt, "trials_df", None)

    if refine_trials_df is not None and len(refine_trials_df) > 0:
        refine_trials_df = refine_trials_df.copy()
        refine_trials_df["source"] = "refine_grid"
    else:
        refine_trials_df = None

    # ── 4. Merge with Optuna trials ───────────────────────────────────────
    optuna_trials_df: Optional["pd.DataFrame"] = getattr(opt_result, "trials_df", None)

    if merge_with_optuna and optuna_trials_df is not None and len(optuna_trials_df) > 0:
        optuna_copy = optuna_trials_df.copy()
        optuna_copy["source"] = "optuna"
        # Normalise multiplier key in optuna trials too
        optuna_copy["_mult_key"] = optuna_copy["multiplier"].apply(
            lambda m: quantize_multiplier(m, mult_step)
        )

        if refine_trials_df is not None:
            refine_copy = refine_trials_df.copy()
            refine_copy["_mult_key"] = refine_copy["multiplier"].apply(
                lambda m: quantize_multiplier(m, mult_step)
            )
            # Concat: refine first so it wins on dedup (keep="first")
            combined_raw = pd.concat([refine_copy, optuna_copy], ignore_index=True)
        else:
            combined_raw = optuna_copy

        # Dedup: key = (atr_period, _mult_key) — grid wins (already first)
        combined_raw = (
            combined_raw
            .drop_duplicates(subset=["atr_period", "_mult_key"], keep="first")
            .drop(columns=["_mult_key"])
            .reset_index(drop=True)
        )
        combined_trials_df = combined_raw
    elif refine_trials_df is not None:
        combined_trials_df = refine_trials_df.copy()
    else:
        # Nothing to work with — return unchanged
        logger.warning("Step %d: run_minigrid_refine — no trials after merge. Unchanged.", step_idx)
        return opt_result

    # ── 5. Full robustness recalculation on ALL combined valid trials ──────
    # Blueprint §3.5 + Критическая правка 2: top_k = len(valid_combined)

    # Filter valid combined
    valid_combined = combined_trials_df[
        combined_trials_df[objective_metric].notna() &
        ~np.isclose(combined_trials_df[objective_metric].values,
                    INVALID_METRIC_VALUE)
    ].copy()

    if len(valid_combined) == 0:
        logger.warning("Step %d: run_minigrid_refine — no valid combined trials. Unchanged.", step_idx)
        # Set meta for edge-case: all grid points invalid (Blueprint §10)
        n_refine_total = len(refine_trials_df) if refine_trials_df is not None else len(refine_grid)
        n_refine_early_exit = getattr(refine_opt, "early_exit_count", 0)
        opt_result._refine_meta = {
            "enabled": True,
            "grid_size": len(refine_grid),
            "grid_points_valid": 0,
            "grid_points_early_exit": n_refine_early_exit,
            "early_exit_ratio": n_refine_early_exit / n_refine_total if n_refine_total > 0 else 0.0,
            "plateau_width": 0.0,
            "plateau_depth": 0.0,
            "plateau_monotonicity": 0.0,
            "best_changed": False,
            "seed_atr": seed_atr,
            "seed_mult": seed_mult,
            "refined_atr": None,
            "refined_mult": None,
            "merge_with_optuna": merge_with_optuna,
            "step_idx": step_idx,
        }
        return opt_result

    # Invariant: valid_combined non-empty (guaranteed by early return above)
    if len(valid_combined) < 1:
        raise ValueError(
            "Refine: no valid combined trials after filtering (NaN/early-exit). "
            "Should have returned earlier with _refine_meta."
        )

    if robustness_enabled:
        # Resolve periods_per_year
        ann_factor = bt_cfg.get("annualization_factor", 252)
        _market_str = bt_cfg.get("market", None)
        _explicit_basis = bt_cfg.get("annualization_basis", None)
        _market_enum = MarketType(_market_str) if _market_str is not None else None
        if isinstance(ann_factor, str) and ann_factor == "auto":
            if isinstance(train_index, pd.DatetimeIndex):
                periods_per_year = resolve_periods_per_year_from_config(
                    config_value=ann_factor,
                    index=train_index,
                    explicit_basis=_explicit_basis,
                    market=_market_enum,
                )
            else:
                periods_per_year = 252.0
        else:
            periods_per_year = float(ann_factor)

        # Resolve execution_model
        exec_model_val = bt_cfg.get("execution_model")
        execution_model = (
            ExecutionModel(exec_model_val)
            if exec_model_val is not None
            else ExecutionModel.OPEN_TO_OPEN
        )

        backtest_kwargs = {
            "open_prices": train_open,
            "high": train_high,
            "low": train_low,
            "close": train_close,
            "index": train_index,
            "trade_mode": opt_cfg.get("trade_mode", "revers"),
            "warmup_period": val_cfg.get("warmup_period", 0),
            "early_exit_enabled": bt_cfg.get("early_exit_enabled", False),
            "early_exit_max_drawdown": bt_cfg.get("early_exit_max_drawdown", 0.5),
            "early_exit_check_bars": bt_cfg.get("early_exit_check_bars", 0),
            "periods_per_year": periods_per_year,
            "min_trades_required": bt_cfg.get("min_trades_required", 3),
            "extract_trades_flag": False,
            "caller_mode": "optimizer",
            "execution_model": execution_model,
            "auto_warmup": True,
        }

        try:
            # top_k = len(valid_combined) — no pre-filtering (Blueprint §3.5)
            _top_k_pass = len(valid_combined)
            if _top_k_pass < 1:
                raise ValueError(
                    "Refine: robustness requires non-empty valid_combined "
                    "(NaN/early-exit filtered). Should have returned earlier."
                )
            robustness_df, best_ranked, robustness_stats = calculate_robustness_for_trials(
                trials_df=valid_combined,
                objective_metric=objective_metric,
                ranking_metric=ranking_metric,
                top_k=_top_k_pass,
                plateau_k_neighbors=robustness_cfg.get("plateau_k_neighbors", 5),
                base_commission=bt_cfg.get("commission", 0.0),
                stress_multipliers=robustness_cfg.get("stress_multipliers", [1.0, 1.3]),
                stress_pass_max_drop_pct=robustness_cfg.get("stress_pass_max_drop_pct", 30.0),
                robust_w1=robustness_cfg.get("robust_w1", 1.0),
                robust_w2=robustness_cfg.get("robust_w2", 0.5),
                run_single_backtest_fn=run_single_backtest,
                backtest_kwargs=backtest_kwargs,
            )
        except Exception as exc:
            logger.error(
                "Step %d: run_minigrid_refine robustness failed: %s. Returning opt_result unchanged.",
                step_idx, exc,
            )
            return opt_result

        # Merge robust_score back into combined_trials_df (left join preserves all rows)
        if robustness_df is not None and len(robustness_df) > 0:
            rs_cols = [c for c in ["atr_period", "multiplier", "robust_score",
                                   "plateau_score", "stress_drop_pct", "stress_pass"]
                       if c in robustness_df.columns]
            combined_trials_df = combined_trials_df.merge(
                robustness_df[rs_cols],
                on=["atr_period", "multiplier"],
                how="left",
            )

        # Determine refined_best
        if best_ranked is not None:
            refined_atr = int(best_ranked["atr_period"])
            refined_mult = float(best_ranked["multiplier"])
        else:
            # Fallback: argmax objective
            best_row = valid_combined.sort_values(objective_metric, ascending=False).iloc[0]
            refined_atr = int(best_row["atr_period"])
            refined_mult = float(best_row["multiplier"])
            logger.warning(
                "Step %d: run_minigrid_refine — no stress-passed trials; "
                "falling back to objective-best (%d, %.4f).",
                step_idx, refined_atr, refined_mult,
            )

    else:
        # Robustness disabled — pick by objective
        robustness_df = None
        robustness_stats = None
        best_ranked = None
        best_row = valid_combined.sort_values(objective_metric, ascending=False).iloc[0]
        refined_atr = int(best_row["atr_period"])
        refined_mult = float(best_row["multiplier"])

    # ── 6. Plateau analysis (refine grid points only) ─────────────────────
    # Blueprint §3.7
    if refine_trials_df is not None and len(refine_trials_df) > 0:
        refine_valid = refine_trials_df[
            refine_trials_df[objective_metric].notna() &
            ~np.isclose(refine_trials_df[objective_metric].values, INVALID_METRIC_VALUE)
        ].copy()

        # Add robust_score to refine_valid if available
        if robustness_df is not None and "robust_score" in robustness_df.columns:
            rs_join = robustness_df[["atr_period", "multiplier", "robust_score"]].copy()
            refine_valid = refine_valid.merge(rs_join, on=["atr_period", "multiplier"], how="left")

        n_refine_total = len(refine_trials_df)
        n_refine_valid = len(refine_valid)
        n_refine_early_exit = getattr(refine_opt, "early_exit_count", 0)

        if n_refine_valid > 0:
            # Choose metric column for plateau
            if metric_for_plateau == "ranking" and ranking_metric == "robust_score":
                plateau_col = "robust_score"
            else:
                plateau_col = objective_metric

            # Use plateau_col if present in refine_valid, else fallback to objective
            if plateau_col not in refine_valid.columns or refine_valid[plateau_col].isna().all():
                plateau_col = objective_metric

            plateau_vals = refine_valid[plateau_col].dropna()
            if len(plateau_vals) > 0:
                best_val = float(plateau_vals.max())
                threshold = best_val * (1.0 - plateau_threshold_pct / 100.0)
                above = (plateau_vals >= threshold).sum()
                plateau_width = float(above) / len(plateau_vals)
                plateau_depth = float(plateau_vals.mean()) / best_val if best_val != 0 else 0.0
                plateau_monotonicity = float(plateau_vals.std()) / abs(best_val) if best_val != 0 else 0.0
            else:
                plateau_width = 0.0
                plateau_depth = 0.0
                plateau_monotonicity = 0.0
        else:
            plateau_width = 0.0
            plateau_depth = 0.0
            plateau_monotonicity = 0.0
    else:
        n_refine_total = len(refine_grid)
        n_refine_valid = 0
        n_refine_early_exit = 0
        plateau_width = 0.0
        plateau_depth = 0.0
        plateau_monotonicity = 0.0

    early_exit_ratio = n_refine_early_exit / n_refine_total if n_refine_total > 0 else 0.0

    # ── 7. Overwrite best_* (Critical requirement 1) ──────────────────────
    # Find the full metrics row for refined_best in combined_trials_df
    refined_row = combined_trials_df[
        (combined_trials_df["atr_period"] == refined_atr) &
        (combined_trials_df["multiplier"].apply(
            lambda m: abs(m - refined_mult) < 1e-9
        ))
    ]

    old_atr = opt_result.best_atr_period
    old_mult = opt_result.best_multiplier

    if len(refined_row) > 0:
        best_metrics_new = refined_row.iloc[0].to_dict()
        best_value_new = float(best_metrics_new.get(objective_metric, opt_result.best_value))

        opt_result.best_atr_period = refined_atr
        opt_result.best_multiplier = refined_mult
        opt_result.best_value = best_value_new
        opt_result.best_metrics = best_metrics_new

        # Also update ranked fields
        opt_result.best_ranked_atr_period = refined_atr
        opt_result.best_ranked_multiplier = refined_mult
        if robustness_enabled and best_ranked is not None:
            opt_result.best_ranked_value = float(best_ranked.get("ranked_value", best_value_new))
            opt_result.best_ranked_objective_value = float(best_ranked.get("objective_value", best_value_new))
            opt_result.best_ranked_reason = "minigrid_refine"
        else:
            opt_result.best_ranked_value = best_value_new
            opt_result.best_ranked_objective_value = best_value_new
            opt_result.best_ranked_reason = "minigrid_refine_objective"
    else:
        logger.warning(
            "Step %d: run_minigrid_refine — refined_best (%d, %.4f) not found in "
            "combined_trials_df. best_* NOT updated.",
            step_idx, refined_atr, refined_mult,
        )

    # Update robustness fields on opt_result
    if robustness_enabled and robustness_df is not None:
        opt_result.robustness_df = robustness_df
        opt_result.robustness_stats = robustness_stats

    # Store combined trials and refine metadata on opt_result for Excel export (Stage 4)
    opt_result.trials_df = combined_trials_df
    opt_result._refine_meta = {
        "enabled": True,
        "grid_size": len(refine_grid),
        "grid_points_valid": n_refine_valid,
        "grid_points_early_exit": n_refine_early_exit,
        "early_exit_ratio": early_exit_ratio,
        "plateau_width": plateau_width,
        "plateau_depth": plateau_depth,
        "plateau_monotonicity": plateau_monotonicity,
        "best_changed": (old_atr != refined_atr or abs((old_mult or 0) - refined_mult) > 1e-9),
        "seed_atr": seed_atr,
        "seed_mult": seed_mult,
        "refined_atr": refined_atr,
        "refined_mult": refined_mult,
        "merge_with_optuna": merge_with_optuna,
        "step_idx": step_idx,
        # Snapshot of combined trials for WF_Refine_Details export (Stage 3)
        "trials_df": combined_trials_df.copy() if combined_trials_df is not None else None,
    }

    logger.info(
        "Step %d: MiniGrid Refine DONE refined=(%d, %.4f) prev=(%s, %s) "
        "best_changed=%s grid=%d valid=%d plateau_width=%.3f",
        step_idx, refined_atr, refined_mult, old_atr, old_mult,
        opt_result._refine_meta["best_changed"],
        len(refine_grid), n_refine_valid, plateau_width,
    )

    return opt_result


# ══════════════════════════════════════════════════════════════════════════


def _to_bucket_key(
    atr_period: int,
    multiplier: float,
    atr_bucket_step: int,
    mult_bucket_step: float,
) -> tuple:
    """Map (atr_period, multiplier) to the nearest bucket centre."""
    atr_bucket = int(round(atr_period / atr_bucket_step) * atr_bucket_step)
    mult_bucket = round(round(multiplier / mult_bucket_step) * mult_bucket_step, 6)
    return (atr_bucket, mult_bucket)


def _calculate_consensus_df(
    steps: List['WFStepResult'],
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Build coverage consensus across WF steps.

    Uses ONLY entries with in_consensus=True from each VALID step (with top_entries).
    coverage_ratio = coverage_count / valid_step_count (not total_steps).
    Skipped steps (no top_entries) are excluded from both coverage counts and denominator.

    Supports two modes controlled via config["walk_forward"]["consensus"]["mode"]:
    - "exact"  (default): key = (atr_period, int(round(multiplier*10))) — same as before.
    - "bucket": key = _to_bucket_key(...) using atr_bucket_step / mult_bucket_step from config.

    Both modes always emit columns:
        atr_period, multiplier, atr_bucket, mult_bucket, coverage_count, coverage_ratio

    Parameters
    ----------
    steps : List[WFStepResult]
        List of Walk-Forward step results.
    config : dict, optional
        Full optimizer config dict.  If None, exact-mode defaults are used.

    Returns
    -------
    pd.DataFrame
        Columns: atr_period, multiplier, atr_bucket, mult_bucket,
                 coverage_count, coverage_ratio.
        Sorted by coverage_ratio DESC, coverage_count DESC, atr_period ASC, multiplier ASC.
    """
    _COLS = ["atr_period", "multiplier", "atr_bucket", "mult_bucket", "coverage_count", "coverage_ratio"]

    consensus_cfg = (config or {}).get("walk_forward", {}).get("consensus", {})
    mode = consensus_cfg.get("mode", "exact")
    atr_bucket_step = int(consensus_cfg.get("atr_bucket_step", DEFAULT_ATR_BUCKET_STEP))
    mult_bucket_step = float(consensus_cfg.get("mult_bucket_step", DEFAULT_MULT_BUCKET_STEP))
    # Default min_coverage=1 preserves backward-compatible behaviour (no filtering)
    # when config is not provided.  Set min_coverage>=2 explicitly in config to enable filtering.
    min_coverage = int(consensus_cfg.get("min_coverage", 1))

    valid_steps = [s for s in steps if _is_step_valid_for_consensus(s)]
    valid_step_count = len(valid_steps)

    if valid_step_count == 0:
        return pd.DataFrame(columns=_COLS)

    # Collect (key, step_idx, atr_period, multiplier, robust_score) entries,
    # counting each key at most once per step.
    entries: List[Dict[str, Any]] = []

    for step in valid_steps:
        seen_in_step: set = set()

        for entry in step.top_entries:
            if not entry.in_consensus:
                continue

            if mode == "bucket":
                key = _to_bucket_key(entry.atr_period, entry.multiplier, atr_bucket_step, mult_bucket_step)
            else:
                mult_q = int(round(entry.multiplier * 10))
                key = (entry.atr_period, mult_q)

            if key not in seen_in_step:
                seen_in_step.add(key)
                entries.append({
                    "key": key,
                    "step_idx": step.step_index,
                    "atr_period": entry.atr_period,
                    "multiplier": entry.multiplier,
                    "robust_score": entry.robust_score,
                })

    if not entries:
        return pd.DataFrame(columns=_COLS)

    entries_df = pd.DataFrame(entries)

    # Coverage per key
    coverage = (
        entries_df.groupby("key")["step_idx"]
        .nunique()
        .rename("coverage_count")
        .reset_index()
    )
    coverage["coverage_ratio"] = coverage["coverage_count"] / valid_step_count

    # Apply min_coverage filter; fall back to min_coverage=1 if everything is filtered out
    filtered = coverage[coverage["coverage_count"] >= min_coverage]
    if len(filtered) == 0:
        filtered = coverage  # fallback: keep all

    valid_keys = set(filtered["key"].tolist())
    entries_df = entries_df[entries_df["key"].isin(valid_keys)]

    # Representative for each key: mean robust_score per (key, atr_period, multiplier),
    # then pick top row by mean_robust_score DESC, atr_period ASC, multiplier ASC.
    mean_scores = (
        entries_df.groupby(["key", "atr_period", "multiplier"], as_index=False)["robust_score"]
        .mean()
        .rename(columns={"robust_score": "mean_robust_score"})
    )
    mean_scores_sorted = mean_scores.sort_values(
        by=["key", "mean_robust_score", "atr_period", "multiplier"],
        ascending=[True, False, True, True],
    )
    representatives = mean_scores_sorted.groupby("key", as_index=False).first()

    # Merge coverage info
    result = representatives.merge(filtered[["key", "coverage_count", "coverage_ratio"]], on="key")

    # Add bucket columns
    if mode == "bucket":
        result["atr_bucket"] = result["key"].apply(lambda k: k[0])
        result["mult_bucket"] = result["key"].apply(lambda k: k[1])
    else:
        result["atr_bucket"] = result["atr_period"]
        result["mult_bucket"] = result["multiplier"]

    result = result[_COLS].sort_values(
        by=["coverage_ratio", "coverage_count", "atr_period", "multiplier"],
        ascending=[False, False, True, True],
    ).reset_index(drop=True)

    return result


def _compute_selected_params(
    steps: List['WFStepResult'],
    consensus_df: Optional[pd.DataFrame],
) -> Dict[str, Any]:
    """
    Compute final selected parameters for WF_Summary.
    
    WF_Summary reflects only consensus-based selection. Mini-grid does not
    participate in this logic (it remains in WF_MiniGrid sheet only).
    
    Logic:
    - If consensus_df exists and not empty: take first row (top-1 after sort).
    - Else if at least one step: use first step's best params, selected_source="unknown".
    - Else: return None/NaN, selected_source="unknown".
    
    Returns
    -------
    dict with keys: selected_atr_period, selected_multiplier, selected_rank,
        selected_coverage_count, selected_coverage_ratio, selected_source
    """
    total_steps = len(steps)
    result = {
        "selected_atr_period": None,
        "selected_multiplier": None,
        "selected_rank": None,
        "selected_coverage_count": None,
        "selected_coverage_ratio": None,
        "selected_source": "unknown",
    }
    
    if consensus_df is not None and len(consensus_df) > 0:
        row = consensus_df.iloc[0]
        result["selected_atr_period"] = int(row["atr_period"])
        result["selected_multiplier"] = float(row["multiplier"])
        result["selected_rank"] = 1
        result["selected_coverage_count"] = int(row["coverage_count"])
        result["selected_coverage_ratio"] = float(row["coverage_ratio"])
        result["selected_source"] = "consensus"
    elif total_steps > 0:
        step0 = steps[0]
        if step0.best_atr_period is not None and step0.best_multiplier is not None:
            result["selected_atr_period"] = step0.best_atr_period
            result["selected_multiplier"] = step0.best_multiplier
            result["selected_rank"] = 1
            result["selected_coverage_count"] = 1
            result["selected_coverage_ratio"] = 1.0 / total_steps if total_steps > 0 else 0.0
        result["selected_source"] = "unknown"
    
    return result


def _resolve_canonical_ref_params(
    wf_result: 'WalkForwardResult',
    selected: Dict[str, Any],
    config: Dict[str, Any],
) -> "tuple[int | None, float | None]":
    """
    Resolve ref params from canonical top-1 (WF_TOP_K_Canonical).
    Falls back to consensus selected on ALL_FAILED, empty agg, or any exception.
    """
    fallback_atr = selected.get("selected_atr_period")
    fallback_mult = selected.get("selected_multiplier")

    try:
        from supertrend_optimizer.scoring.aggregation import build_aggregated_topk_table
        from supertrend_optimizer.scoring.canonical import (
            calculate_canonical_rank_score,
            canonical_gates_status,
        )

        consensus_cfg = config.get("walk_forward", {}).get("consensus", {})
        atr_bucket_step = consensus_cfg.get("atr_bucket_step", DEFAULT_ATR_BUCKET_STEP)
        mult_bucket_step = consensus_cfg.get("mult_bucket_step", DEFAULT_MULT_BUCKET_STEP)

        agg_df = build_aggregated_topk_table(wf_result, config)
        if agg_df is None or len(agg_df) == 0:
            logger.warning(
                "Canonical ref params: aggregated TOP-K table is empty, "
                "falling back to consensus selected (atr=%s, mult=%s).",
                fallback_atr, fallback_mult,
            )
            return fallback_atr, fallback_mult

        scored = calculate_canonical_rank_score(
            agg_df.copy(),
            atr_bucket_step,
            mult_bucket_step,
            config=config,
            total_steps=len(wf_result.steps),
        )
        status = canonical_gates_status(scored, config)
        top1 = status.get("top1_row")

        if top1 is not None and "atr_period" in top1.index and "multiplier" in top1.index:
            ref_atr = int(top1["atr_period"])
            ref_mult = float(top1["multiplier"])
            logger.info(
                "Canonical ref params: atr_period=%d, multiplier=%.2f (canonical top-1)",
                ref_atr, ref_mult,
            )
            return ref_atr, ref_mult

        logger.warning(
            "Canonical ref params: top1_row not available (status=%s), "
            "falling back to consensus selected (atr=%s, mult=%s).",
            status.get("status", "?"), fallback_atr, fallback_mult,
        )
        return fallback_atr, fallback_mult

    except Exception:
        logger.warning(
            "Failed to compute canonical ref params, falling back to consensus selected (atr=%s, mult=%s).",
            fallback_atr, fallback_mult,
            exc_info=True,
        )
        return fallback_atr, fallback_mult


def _fill_oos_metrics_for_topk(
    top_entries: List['WFStepTopEntry'],
    test_open: np.ndarray,
    test_high: np.ndarray,
    test_low: np.ndarray,
    test_close: np.ndarray,
    test_index: pd.Index,
    config: Dict[str, Any],
    periods_per_year: float,
    execution_model,
    # FIX 2: full dataset arrays + test boundary for prepend-based OOS backtest.
    # When provided, the backtest runs on [test_start_idx - prepend_bars : test_end_idx]
    # so the indicator is warm at test_start_idx; metrics are computed on OOS-only returns.
    full_open: Optional[np.ndarray] = None,
    full_high: Optional[np.ndarray] = None,
    full_low: Optional[np.ndarray] = None,
    full_close: Optional[np.ndarray] = None,
    full_index: Optional[pd.Index] = None,
    test_start_idx: Optional[int] = None,
    test_end_idx: Optional[int] = None,
    retain_oos_arrays: bool = False,
) -> None:
    """
    Fill OOS metrics for TOP-K entries by running backtest on test data.
    
    Modifies top_entries in-place by filling OOS fields:
    - oos_sortino
    - oos_sum_pnl_pct
    - oos_num_trades
    - oos_max_dd_pct (normalized via _normalize_dd_to_pct)
    
    When retain_oos_arrays=True, also fills:
    - oos_returns  (OOS-only bar returns array)
    - oos_positions (OOS-only positions array)
    These are used by the diversification filter. Default False = no memory overhead.
    
    Parameters
    ----------
    top_entries : List[WFStepTopEntry]
        List of TOP-K entries to fill with OOS metrics
    test_open : np.ndarray
        Test window open prices (used as fallback when full arrays not provided)
    test_high : np.ndarray
        Test window high prices
    test_low : np.ndarray
        Test window low prices
    test_close : np.ndarray
        Test window close prices
    test_index : pd.Index
        Test window time index
    config : Dict[str, Any]
        Full configuration dictionary
    periods_per_year : float
        Annualization factor for test window
    execution_model : ExecutionModel
        Execution model for backtest
    full_open, full_high, full_low, full_close, full_index : optional
        Full dataset arrays.  When provided together with test_start_idx /
        test_end_idx the backtest is run on an *extended* slice that prepends
        ``prepend_bars`` bars of history before the OOS window so that the
        SuperTrend indicator is properly warmed up.  Metrics are then computed
        only on the OOS portion (test_start_idx..test_end_idx).
    test_start_idx, test_end_idx : optional int
        Absolute indices into the full arrays that delimit the OOS window.
    retain_oos_arrays : bool
        When True, store oos_returns and oos_positions on each entry.
        Set by the caller based on diversification.enabled config flag.
    """
    if len(top_entries) == 0:
        return
    
    # Extract config sections
    bt_cfg = config.get("backtest", {})
    val_cfg = config.get("validation", {})
    opt_cfg = config.get("optimization", {})
    wf_cfg = val_cfg.get("walk_forward", {})
    
    # Determine early_exit settings for OOS
    # Use dedicated OOS setting if available, otherwise inherit from backtest config
    oos_early_exit = wf_cfg.get("oos_early_exit_enabled", bt_cfg.get("early_exit_enabled", False))
    
    # If early_exit is disabled for OOS, set check_bars to 0 to ensure no checking
    oos_check_bars = bt_cfg.get("early_exit_check_bars", 0) if oos_early_exit else 0

    logger = logging.getLogger(__name__)

    # FIX 2: determine whether we can use prepend-based OOS backtest.
    # Requires full arrays + boundary indices to be passed by the caller.
    _use_prepend = (
        full_open is not None
        and full_high is not None
        and full_low is not None
        and full_close is not None
        and full_index is not None
        and test_start_idx is not None
        and test_end_idx is not None
    )

    # Compute prepend_bars from config warmup (same formula as apply_auto_warmup_to_config).
    # We use the *full* dataset length so the prepend is at least as large as the
    # warmup that was used during optimisation.
    if _use_prepend:
        from supertrend_optimizer.utils.warmup import calculate_warmup
        _n_full = len(full_open)  # type: ignore[arg-type]
        prepend_bars = calculate_warmup(_n_full, config)
        # Clamp prepend to available history before test_start_idx
        prepend_bars = min(prepend_bars, test_start_idx)  # type: ignore[arg-type]
        ext_start_idx = test_start_idx - prepend_bars  # type: ignore[operator]
        logger.debug(
            "OOS prepend: test_start_idx=%d prepend_bars=%d ext_start_idx=%d test_end_idx=%d",
            test_start_idx, prepend_bars, ext_start_idx, test_end_idx,
        )
    
    # Run OOS backtest for each TOP-K entry
    for entry in top_entries:
        if _use_prepend and prepend_bars > 0:
            # --- Extended-slice backtest (FIX 2) ---
            # Run on [ext_start_idx : test_end_idx] so the indicator is warm.
            ext_open  = full_open[ext_start_idx:test_end_idx]   # type: ignore[index]
            ext_high  = full_high[ext_start_idx:test_end_idx]   # type: ignore[index]
            ext_low   = full_low[ext_start_idx:test_end_idx]    # type: ignore[index]
            ext_close = full_close[ext_start_idx:test_end_idx]  # type: ignore[index]
            ext_index = full_index[ext_start_idx:test_end_idx]  # type: ignore[index]

            ext_result = run_single_backtest(
                open_prices=ext_open,
                high=ext_high,
                low=ext_low,
                close=ext_close,
                index=ext_index,
                atr_period=entry.atr_period,
                multiplier=entry.multiplier,
                trade_mode=opt_cfg.get("trade_mode", "revers"),
                commission=bt_cfg.get("commission", 0.0),
                warmup_period=val_cfg.get("warmup_period", 0),
                early_exit_enabled=oos_early_exit,
                early_exit_max_drawdown=bt_cfg.get("early_exit_max_drawdown", 0.5),
                early_exit_check_bars=oos_check_bars,
                periods_per_year=periods_per_year,
                min_trades_required=bt_cfg.get("min_trades_required", 3),
                extract_trades_flag=False,
                caller_mode="optimizer",
                execution_model=execution_model,
                auto_warmup=True,
            )

            # Slice arrays to OOS-only portion (prepend_bars .. end).
            # returns has length len(ext_open) - 1 (bar returns), equity has
            # length len(ext_open).  The OOS boundary in the extended arrays is
            # at index prepend_bars (0-based).
            ext_returns   = getattr(ext_result, "returns", None)
            ext_equity    = getattr(ext_result, "equity_curve", None)
            ext_positions = getattr(ext_result, "positions", None)

            # Guard: if the result object doesn't expose raw arrays (e.g. a mock
            # in tests), fall back to using the metrics dict directly.
            # retain_oos_arrays: arrays unavailable here — entry.oos_returns/positions stay None.
            if ext_returns is None or ext_equity is None or ext_positions is None:
                oos_metrics = ext_result.metrics
                _sortino = oos_metrics.get("sortino")
                entry.oos_sortino = float(_sortino) if (
                    _sortino is not None and _sortino != INVALID_METRIC_VALUE
                ) else INVALID_METRIC_VALUE
                _pnl = oos_metrics.get("sum_pnl_pct")
                if _pnl is not None and _pnl != INVALID_METRIC_VALUE:
                    entry.oos_sum_pnl_pct = float(_pnl)
                _nt = oos_metrics.get("num_trades")
                if _nt is not None:
                    entry.oos_num_trades = int(_nt)
                # Guard: DD at 0 trades is 0.0 (flat curve) — semantically empty,
                # not an observed drawdown. Only record DD when >= 1 trade occurred.
                _fb_nt = entry.oos_num_trades or 0
                if _fb_nt >= 1:
                    entry.oos_max_dd_pct = _normalize_dd_to_pct(oos_metrics.get("max_drawdown"))
                else:
                    entry.oos_max_dd_pct = None
                _wr = oos_metrics.get("win_rate")
                if _wr is not None and _wr != INVALID_METRIC_VALUE:
                    entry.oos_win_rate = float(_wr)
                _pf = oos_metrics.get("profit_factor")
                if _pf is not None and _pf != INVALID_METRIC_VALUE and np.isfinite(_pf):
                    entry.oos_profit_factor = float(_pf)
                _at = oos_metrics.get("avg_trade")
                if _at is not None and _at != INVALID_METRIC_VALUE and np.isfinite(_at):
                    entry.oos_avg_trade = float(_at)
                continue

            # Determine OOS boundary within the extended arrays.
            # If early_exit truncated the arrays, the boundary may be beyond
            # the available data — in that case fall back to full arrays.
            oos_boundary = prepend_bars  # index into ext_returns / ext_positions
            if oos_boundary >= len(ext_returns):
                # Prepend consumed everything (shouldn't happen after cap above,
                # but guard defensively).
                logger.warning(
                    "OOS prepend boundary=%d >= len(ext_returns)=%d for "
                    "atr=%d mult=%.1f; falling back to plain OOS backtest.",
                    oos_boundary, len(ext_returns),
                    entry.atr_period, entry.multiplier,
                )
                oos_returns   = ext_returns
                oos_equity    = ext_equity
                oos_positions = ext_positions
            else:
                oos_returns   = ext_returns[oos_boundary:]
                # equity_curve is length = len(returns)+1, so equity[oos_boundary]
                # is the starting equity value at the first OOS bar.
                oos_equity    = ext_equity[oos_boundary:]
                oos_positions = ext_positions[oos_boundary:]

            # Compute OOS metrics on the OOS-only slice.
            # warmup=0 because the indicator is already warm thanks to prepend;
            # FIX 1 safety-cap in calculate_all_metrics will handle edge cases.
            from supertrend_optimizer.core.metrics import calculate_all_metrics
            oos_metrics = calculate_all_metrics(
                returns=oos_returns,
                equity_curve=oos_equity,
                positions=oos_positions,
                warmup_period=0,
                periods_per_year=periods_per_year,
                min_trades_required=bt_cfg.get("min_trades_required", 3),
            )
            # Prepend path: retain OOS-only arrays for diversification filter.
            if retain_oos_arrays:
                entry.oos_returns   = np.array(oos_returns,   dtype=np.float64)
                entry.oos_positions = np.array(oos_positions, dtype=np.float64)

        else:
            # --- Fallback: plain OOS backtest on test slice only (legacy path) ---
            oos_result = run_single_backtest(
                open_prices=test_open,
                high=test_high,
                low=test_low,
                close=test_close,
                index=test_index,
                atr_period=entry.atr_period,
                multiplier=entry.multiplier,
                trade_mode=opt_cfg.get("trade_mode", "revers"),
                commission=bt_cfg.get("commission", 0.0),
                warmup_period=val_cfg.get("warmup_period", 0),
                early_exit_enabled=oos_early_exit,
                early_exit_max_drawdown=bt_cfg.get("early_exit_max_drawdown", 0.5),
                early_exit_check_bars=oos_check_bars,
                periods_per_year=periods_per_year,
                min_trades_required=bt_cfg.get("min_trades_required", 3),
                extract_trades_flag=False,  # Don't need trades for OOS metrics
                caller_mode="optimizer",
                execution_model=execution_model,
                auto_warmup=True,
            )
            oos_metrics = oos_result.metrics
            # Fallback path: oos_result covers the full test slice (already OOS-only).
            if retain_oos_arrays:
                _fb_returns   = getattr(oos_result, "returns", None)
                _fb_positions = getattr(oos_result, "positions", None)
                if _fb_returns is not None and _fb_positions is not None:
                    entry.oos_returns   = np.array(_fb_returns,   dtype=np.float64)
                    entry.oos_positions = np.array(_fb_positions, dtype=np.float64)

        
        # Fill OOS fields (handle INVALID_METRIC_VALUE and missing keys)
        # Policy A: invalid/missing sortino → INVALID_METRIC_VALUE (-999.0), never None.
        # This ensures _build_aggregated_topk_table always receives a numeric value and
        # can compute oos_sortino_min = -999.0 (flagging the candidate for sortino_gate_fail),
        # rather than leaving NaN which triggers the misleading "missing_oos_metrics" exclusion.
        sortino = oos_metrics.get("sortino")
        if sortino is not None and sortino != INVALID_METRIC_VALUE:
            entry.oos_sortino = float(sortino)
        else:
            entry.oos_sortino = INVALID_METRIC_VALUE

        sum_pnl_pct = oos_metrics.get("sum_pnl_pct")
        if sum_pnl_pct is not None and sum_pnl_pct != INVALID_METRIC_VALUE:
            entry.oos_sum_pnl_pct = float(sum_pnl_pct)

        num_trades = oos_metrics.get("num_trades")
        if num_trades is not None:
            entry.oos_num_trades = int(num_trades)

        # Normalize max_drawdown to percentage.
        # Guard: DD at 0 trades is 0.0 (flat curve) — semantically empty,
        # not an observed drawdown. Only record DD when >= 1 trade occurred.
        max_dd = oos_metrics.get("max_drawdown")
        oos_nt = entry.oos_num_trades or 0
        if oos_nt >= 1:
            entry.oos_max_dd_pct = _normalize_dd_to_pct(max_dd)
        else:
            entry.oos_max_dd_pct = None

        _wr = oos_metrics.get("win_rate")
        if _wr is not None and _wr != INVALID_METRIC_VALUE:
            entry.oos_win_rate = float(_wr)
        _pf = oos_metrics.get("profit_factor")
        if _pf is not None and _pf != INVALID_METRIC_VALUE and np.isfinite(_pf):
            entry.oos_profit_factor = float(_pf)
        _at = oos_metrics.get("avg_trade")
        if _at is not None and _at != INVALID_METRIC_VALUE and np.isfinite(_at):
            entry.oos_avg_trade = float(_at)


def _build_topk_entries(
    opt_result,
    objective_metric: str,
    config: Dict[str, Any]
) -> tuple:
    """
    Build TOP-K entries from optimization result.
    
    Filters by stress_pass, sorts by robust_score (then train_objective),
    and creates WFStepTopEntry objects for TOP-K export.
    
    Parameters
    ----------
    opt_result : OptimizationResult
        Optimization result containing trials_df and optionally robustness_df
    objective_metric : str
        Name of the objective metric (e.g., "sortino", "sum_pnl_pct")
    config : Dict[str, Any]
        Full configuration dictionary
        
    Returns
    -------
    tuple
        (top_entries, passed_count, top_export_count, top_consensus_count)
        - top_entries: List[WFStepTopEntry]
        - passed_count: int (total trials that passed stress test)
        - top_export_count: int (actual number exported)
        - top_consensus_count: int (actual number in consensus)
    """
    import logging
    
    # Read TOP-K config from walk_forward section (not validation.walk_forward)
    wf_top_cfg = config.get("walk_forward", {})
    top_k_export = wf_top_cfg.get("top_k_export", 50)
    top_k_consensus = wf_top_cfg.get("top_k_consensus", 10)
    
    # Check if robustness is enabled
    robustness_cfg = config.get("robustness", {})
    robustness_enabled = robustness_cfg.get("enabled", False)
    
    trials_df = getattr(opt_result, "trials_df", None)
    robustness_df = getattr(opt_result, "robustness_df", None)
    
    # Guard: if we need trials_df but it's None/empty, return empty step
    use_robustness_df = robustness_enabled and robustness_df is not None
    if not use_robustness_df:
        if trials_df is None or (hasattr(trials_df, "empty") and trials_df.empty):
            logging.warning(
                "Step has 0 valid trials (trials_df is None or empty). Skipping TOP-K."
            )
            return [], 0, 0, 0
    
    # Determine source DataFrame
    if use_robustness_df:
        # Use robustness_df (already has robust_score and stress_pass)
        source_df = robustness_df.copy()
    elif robustness_enabled and robustness_df is None:
        # Robustness enabled but no robustness_df - fallback with warning
        logging.warning(
            "Robustness is enabled but opt_result.robustness_df is None. "
            "Using fallback: stress_pass=True, robust_score=train_objective for all trials."
        )
        source_df = trials_df.copy()
        source_df["stress_pass"] = True
        source_df["robust_score"] = source_df[objective_metric]
    else:
        # Robustness disabled - use fallback
        source_df = trials_df.copy()
        source_df["stress_pass"] = True
        source_df["robust_score"] = source_df[objective_metric]
    
    # Filter by stress_pass
    passed_df = source_df[source_df["stress_pass"] == True].copy()
    passed_count = len(passed_df)
    
    # Handle edge case: no trials passed
    if passed_count == 0:
        return [], 0, 0, 0
    
    # Sort by robust_score DESC, then by train_objective DESC
    passed_df = passed_df.sort_values(
        by=["robust_score", objective_metric],
        ascending=[False, False]
    ).reset_index(drop=True)
    
    # === DEDUPLICATION BY NORMALIZED PARAMETER KEY (atr_period, multiplier) ===
    # Normalize to the optimization search grid to avoid float artifacts.
    # Uses multiplier_step from config (optimization.multiplier_step, default 0.1).
    step = float(config.get("optimization", {}).get("multiplier_step", 0.1))
    passed_df["_atr_norm"] = passed_df["atr_period"].astype(int)
    passed_df["_mult_norm"] = (
        (passed_df["multiplier"] / step).round().astype(int) * step
    ).round(6)
    # Drop duplicates AFTER sorting → keep best (first)
    passed_df = (
        passed_df
        .drop_duplicates(subset=["_atr_norm", "_mult_norm"], keep="first")
        .drop(columns=["_atr_norm", "_mult_norm"])
        .reset_index(drop=True)
    )
    # === END DEDUPLICATION ===
    
    # Calculate actual TOP sizes
    actual_export = min(top_k_export, passed_count)
    actual_consensus = min(top_k_consensus, passed_count)
    
    # Build TOP-K entries
    top_entries = []
    for idx, row in passed_df.head(actual_export).iterrows():
        rank = idx + 1

        # Extract train metrics if available
        train_sum_pnl = row.get("sum_pnl_pct", None)
        if train_sum_pnl is not None and not (isinstance(train_sum_pnl, float) and np.isnan(train_sum_pnl)):
            train_sum_pnl_pct = float(train_sum_pnl)
        else:
            train_sum_pnl_pct = None
        train_sortino_val = row.get("sortino", None)
        if train_sortino_val is not None and not (isinstance(train_sortino_val, float) and np.isnan(train_sortino_val)) and train_sortino_val != INVALID_METRIC_VALUE:
            train_sortino_val = float(train_sortino_val)
        else:
            train_sortino_val = None
        train_num_trades_val = row.get("num_trades", None)
        if train_num_trades_val is not None and not (isinstance(train_num_trades_val, float) and np.isnan(train_num_trades_val)):
            train_num_trades_val = int(train_num_trades_val)
        else:
            train_num_trades_val = None
        train_sharpe_val = row.get("sharpe", None)
        if train_sharpe_val is not None and not (isinstance(train_sharpe_val, float) and np.isnan(train_sharpe_val)) and train_sharpe_val != INVALID_METRIC_VALUE:
            train_sharpe_val = float(train_sharpe_val)
        else:
            train_sharpe_val = None
        train_cagr_val = row.get("cagr", None)
        if train_cagr_val is not None and not (isinstance(train_cagr_val, float) and np.isnan(train_cagr_val)) and train_cagr_val != INVALID_METRIC_VALUE:
            train_cagr_val = float(train_cagr_val)
        else:
            train_cagr_val = None

        entry = WFStepTopEntry(
            rank=rank,
            atr_period=int(row["atr_period"]),
            multiplier=float(row["multiplier"]),
            train_objective=float(row[objective_metric]),
            robust_score=float(row["robust_score"]),
            stress_pass=True,  # All entries here passed stress
            train_sum_pnl_pct=train_sum_pnl_pct,
            train_sortino=train_sortino_val,
            train_num_trades=train_num_trades_val,
            train_sharpe=train_sharpe_val,
            train_cagr=train_cagr_val,
            # OOS metrics remain None (will be filled in PR5)
            oos_sortino=None,
            oos_sum_pnl_pct=None,
            oos_max_dd_pct=None,
            oos_num_trades=None,
            # Mark if in consensus
            in_consensus=(rank <= actual_consensus)
        )
        top_entries.append(entry)
    
    return top_entries, passed_count, actual_export, actual_consensus


def _normalize_dd_to_pct(dd_value: Optional[float]) -> Optional[float]:
    """
    Normalize max drawdown to positive percentage format.
    
    Auto-detects whether the input is a fraction (e.g., -0.35) or 
    already a percentage (e.g., -35.0) and converts to positive percentage.
    
    Parameters
    ----------
    dd_value : Optional[float]
        Max drawdown value (can be negative fraction, positive fraction,
        negative percentage, or positive percentage)
        
    Returns
    -------
    Optional[float]
        Positive percentage (e.g., 35.0), or None if value is invalid
        
    Examples
    --------
    >>> _normalize_dd_to_pct(-0.35)
    35.0
    >>> _normalize_dd_to_pct(0.12)
    12.0
    >>> _normalize_dd_to_pct(-35.0)
    35.0
    >>> _normalize_dd_to_pct(35.0)
    35.0
    >>> _normalize_dd_to_pct(INVALID_METRIC_VALUE)
    None
    >>> _normalize_dd_to_pct(np.nan)
    None
    >>> _normalize_dd_to_pct(None)
    None
    """
    # Handle None
    if dd_value is None:
        return None
    
    # Handle INVALID_METRIC_VALUE
    if dd_value == INVALID_METRIC_VALUE:
        return None
    
    # Handle NaN
    if np.isnan(dd_value):
        return None
    
    # Auto-detect format and normalize
    abs_value = abs(dd_value)
    
    if abs_value <= 1.0:
        # Treat as fraction (e.g., 0.35 or -0.35)
        return abs_value * 100.0
    else:
        # Treat as already percentage (e.g., 35.0 or -35.0)
        return abs_value


def _aggregate_oos_trades(steps: List[WFStepResult]) -> Optional[pd.DataFrame]:
    """
    Aggregate trades from all OOS test windows.

    Parameters
    ----------
    steps : List[WFStepResult]
        List of Walk-Forward step results

    Returns
    -------
    Optional[pd.DataFrame]
        Aggregated trades DataFrame with WF metadata columns, or None if no trades
    """
    trades_list = []

    for step in steps:
        if step.test_trades_df is None or len(step.test_trades_df) == 0:
            continue

        # Copy trades and add WF metadata
        trades = step.test_trades_df.copy()
        trades["wf_step"] = step.step_index
        trades["wf_test_start"] = step.test_start_time
        trades["wf_test_end"] = step.test_end_time

        trades_list.append(trades)

    if len(trades_list) == 0:
        return None

    # Concatenate all trades
    aggregated = pd.concat(trades_list, ignore_index=True)

    # Stable sort for deterministic ordering
    sort_cols = [c for c in ["wf_step", "entry_time", "entry_index", "exit_time", "exit_index"] if c in aggregated.columns]
    if sort_cols:
        aggregated = aggregated.sort_values(
            by=sort_cols,
            ascending=True,
            na_position="last",
            kind="mergesort",
        ).reset_index(drop=True)

    # Renumber trade_id sequentially if it exists
    if "trade_id" in aggregated.columns:
        aggregated["trade_id"] = range(1, len(aggregated) + 1)

    return aggregated


def _aggregate_train_trades(steps: List[WFStepResult]) -> Optional[pd.DataFrame]:
    """
    Aggregate TRAIN trades from all Walk-Forward steps using per-step best params.

    Trades are extracted from the full train slice; warmup affects ratio-metrics,
    but does not filter/trim the trades output.

    Parameters
    ----------
    steps : List[WFStepResult]
        List of Walk-Forward step results

    Returns
    -------
    Optional[pd.DataFrame]
        Aggregated train trades DataFrame with WF metadata columns, or None if no trades
    """
    trades_list = []

    for step in steps:
        if step.train_trades_df is None or len(step.train_trades_df) == 0:
            continue

        trades = step.train_trades_df.copy()
        trades["wf_step"] = step.step_index
        trades["wf_train_start"] = step.train_start_time
        trades["wf_train_end"] = step.train_end_time

        trades_list.append(trades)

    if len(trades_list) == 0:
        return None

    aggregated = pd.concat(trades_list, ignore_index=True)

    # Stable sort for deterministic ordering
    sort_cols = [c for c in ["wf_step", "entry_time", "entry_index", "exit_time", "exit_index"] if c in aggregated.columns]
    if sort_cols:
        aggregated = aggregated.sort_values(
            by=sort_cols,
            ascending=True,
            na_position="last",
            kind="mergesort",
        ).reset_index(drop=True)

    # Renumber trade_id sequentially
    if "trade_id" in aggregated.columns:
        aggregated["trade_id"] = range(1, len(aggregated) + 1)

    return aggregated


def _calculate_oos_metrics(
    trades_df: Optional[pd.DataFrame],
    oos_equity: np.ndarray,
    stitched_returns: np.ndarray,
    periods_per_year: float,
    min_trades_required: int
) -> Dict[str, float]:
    """
    Calculate OOS metrics from aggregated data.

    Parameters
    ----------
    trades_df : Optional[pd.DataFrame]
        Aggregated trades DataFrame
    oos_equity : np.ndarray
        OOS equity curve
    stitched_returns : np.ndarray
        Stitched returns array
    periods_per_year : float
        Periods per year for annualization
    min_trades_required : int
        Minimum trades required for valid ratio metrics

    Returns
    -------
    Dict[str, float]
        Dictionary with calculated metrics
    """
    metrics = {}

    # Trade metrics (if trades_df exists)
    if trades_df is not None and len(trades_df) > 0:
        num_trades = len(trades_df)
        metrics["num_trades"] = num_trades

        # Calculate trade-based metrics
        if "net_pnl_pct" in trades_df.columns:
            sum_pnl_pct = trades_df["net_pnl_pct"].sum()
            metrics["sum_pnl_pct"] = float(sum_pnl_pct)

            # Average trade
            avg_trade = sum_pnl_pct / num_trades if num_trades > 0 else INVALID_METRIC_VALUE
            metrics["avg_trade"] = float(avg_trade)

            # Win rate
            winning_trades = (trades_df["net_pnl_pct"] > 0).sum()
            win_rate = winning_trades / num_trades if num_trades > 0 else INVALID_METRIC_VALUE
            metrics["win_rate"] = float(win_rate)

            # Profit factor
            profits = trades_df[trades_df["net_pnl_pct"] > 0]["net_pnl_pct"].sum()
            losses = abs(trades_df[trades_df["net_pnl_pct"] < 0]["net_pnl_pct"].sum())

            if losses > 0:
                profit_factor = profits / losses
                metrics["profit_factor"] = float(profit_factor)
            elif profits > 0:
                # F-08: all-winning trades — cap at 9999.0, same as core/metrics.py
                metrics["profit_factor"] = 9999.0
            else:
                metrics["profit_factor"] = INVALID_METRIC_VALUE
        else:
            # No net_pnl_pct column
            metrics["sum_pnl_pct"] = INVALID_METRIC_VALUE
            metrics["avg_trade"] = INVALID_METRIC_VALUE
            metrics["win_rate"] = INVALID_METRIC_VALUE
            metrics["profit_factor"] = INVALID_METRIC_VALUE
    else:
        # No trades
        metrics["num_trades"] = 0
        metrics["sum_pnl_pct"] = INVALID_METRIC_VALUE
        metrics["avg_trade"] = INVALID_METRIC_VALUE
        metrics["win_rate"] = INVALID_METRIC_VALUE
        metrics["profit_factor"] = INVALID_METRIC_VALUE

    # Check minimum trades requirement
    num_trades = metrics.get("num_trades", 0)
    insufficient_trades = num_trades < min_trades_required

    # Ratio metrics (if returns length >= 2)
    if len(stitched_returns) >= 2 and not insufficient_trades:
        metrics["sharpe"] = calculate_sharpe_ratio(stitched_returns, int(periods_per_year))
        metrics["sortino"] = calculate_sortino_ratio(stitched_returns, int(periods_per_year))
    else:
        metrics["sharpe"] = INVALID_METRIC_VALUE
        metrics["sortino"] = INVALID_METRIC_VALUE

    # DD/CAGR metrics (if equity length >= 2)
    if len(oos_equity) >= 2:
        metrics["max_drawdown"] = calculate_max_drawdown(oos_equity)

        if not insufficient_trades:
            metrics["cagr"] = calculate_cagr(oos_equity, int(periods_per_year))
        else:
            metrics["cagr"] = INVALID_METRIC_VALUE
    else:
        metrics["max_drawdown"] = INVALID_METRIC_VALUE
        metrics["cagr"] = INVALID_METRIC_VALUE

    return metrics


def _aggregate_oos_results(
    steps: List[WFStepResult],
    periods_per_year: float,
    min_trades_required: int = 3
) -> dict:
    """
    Aggregate OOS results from all Walk-Forward steps.

    Parameters
    ----------
    steps : List[WFStepResult]
        List of Walk-Forward step results
    periods_per_year : float
        Periods per year for annualization
    min_trades_required : int, optional
        Minimum trades required for valid ratio metrics (default: 3)

    Returns
    -------
    dict
        Dictionary with keys: trades_df, returns, equity_curve, index, metrics
    """
    returns_list = []
    index_list = []

    for step in steps:
        if step.test_returns is None or len(step.test_returns) == 0:
            continue

        returns_list.append(step.test_returns)

        # Handle index
        # Get test_index from the step (need to extract from test data)
        # For now, we'll construct it from test_start_idx and test_end_idx
        # But we need the original index - this should be passed or stored
        # For PR5, we'll use timestamps if available
        if step.test_start_time is not None and step.test_end_time is not None:
            # Create a simple range for now - in real implementation,
            # this would come from the actual test data index
            # We'll handle this properly by checking step attributes
            pass

    # Aggregate trades
    trades_df = _aggregate_oos_trades(steps)

    # If no returns, return empty result
    if len(returns_list) == 0:
        metrics = _calculate_oos_metrics(
            trades_df=trades_df,
            oos_equity=np.array([1.0]),
            stitched_returns=np.array([]),
            periods_per_year=periods_per_year,
            min_trades_required=min_trades_required
        )

        return {
            "trades_df": trades_df,
            "returns": np.array([]),
            "equity_curve": np.array([1.0]),
            "index": pd.RangeIndex(0),
            "metrics": metrics
        }

    # Stitch returns
    stitched_returns = np.concatenate(returns_list)

    # Calculate OOS equity: [1.0, cumprod(1 + returns)]
    oos_equity = np.concatenate([[1.0], np.cumprod(1.0 + stitched_returns)])

    # Invariant check
    if len(oos_equity) != len(stitched_returns) + 1:
        raise ValueError(
            f"Equity length invariant violated: {len(oos_equity)} != {len(stitched_returns)} + 1"
        )

    # Construct stitched index
    # For now, use RangeIndex - proper implementation would stitch actual indices
    stitched_index = pd.RangeIndex(len(stitched_returns))

    # Calculate metrics
    metrics = _calculate_oos_metrics(
        trades_df=trades_df,
        oos_equity=oos_equity,
        stitched_returns=stitched_returns,
        periods_per_year=periods_per_year,
        min_trades_required=min_trades_required
    )

    return {
        "trades_df": trades_df,
        "returns": stitched_returns,
        "equity_curve": oos_equity,
        "index": stitched_index,
        "metrics": metrics
    }


def evaluate_walk_forward_gates(
    wf_result: WalkForwardResult,
    config: Dict[str, Any]
) -> WalkForwardGatesResult:
    """
    Evaluate OOS gates on Walk-Forward results.
    
    Checks aggregated OOS metrics and optionally per-step metrics
    against configured thresholds.
    
    Args:
        wf_result: WalkForwardResult with OOS metrics and steps
        config: Configuration dictionary
        
    Returns:
        WalkForwardGatesResult with pass/fail status and detailed checks
    """
    gates_cfg = config.get("validation", {}).get("walk_forward", {}).get("oos_gates", {})
    
    # If gates not enabled, return disabled result
    if not gates_cfg.get("enabled", False):
        return WalkForwardGatesResult(enabled=False, passed=True)
    
    checks = []
    fail_count = 0
    
    oos_metrics = wf_result.oos_metrics
    
    # Check: min_trades
    min_trades = gates_cfg.get("min_trades")
    if min_trades is not None:
        num_trades = oos_metrics.get("num_trades", 0)
        passed = num_trades >= min_trades
        checks.append(GateCheck(
            name="min_trades",
            passed=passed,
            value=float(num_trades),
            threshold=float(min_trades),
            message=f"OOS trades: {num_trades} (required: >= {min_trades})"
        ))
        if not passed:
            fail_count += 1
    
    # Check: min_sortino
    min_sortino = gates_cfg.get("min_sortino")
    if min_sortino is not None:
        sortino = oos_metrics.get("sortino", INVALID_METRIC_VALUE)
        passed = sortino != INVALID_METRIC_VALUE and sortino >= min_sortino
        checks.append(GateCheck(
            name="min_sortino",
            passed=passed,
            value=sortino if sortino != INVALID_METRIC_VALUE else None,
            threshold=float(min_sortino),
            message=f"OOS Sortino: {sortino:.3f} (required: >= {min_sortino})" if sortino != INVALID_METRIC_VALUE else "OOS Sortino: INVALID"
        ))
        if not passed:
            fail_count += 1
    
    # Check: min_sharpe
    min_sharpe = gates_cfg.get("min_sharpe")
    if min_sharpe is not None:
        sharpe = oos_metrics.get("sharpe", INVALID_METRIC_VALUE)
        passed = sharpe != INVALID_METRIC_VALUE and sharpe >= min_sharpe
        checks.append(GateCheck(
            name="min_sharpe",
            passed=passed,
            value=sharpe if sharpe != INVALID_METRIC_VALUE else None,
            threshold=float(min_sharpe),
            message=f"OOS Sharpe: {sharpe:.3f} (required: >= {min_sharpe})" if sharpe != INVALID_METRIC_VALUE else "OOS Sharpe: INVALID"
        ))
        if not passed:
            fail_count += 1
    
    # Check: min_sum_pnl_pct
    min_sum_pnl = gates_cfg.get("min_sum_pnl_pct")
    if min_sum_pnl is not None:
        sum_pnl = oos_metrics.get("sum_pnl_pct", INVALID_METRIC_VALUE)
        passed = sum_pnl != INVALID_METRIC_VALUE and sum_pnl >= min_sum_pnl
        checks.append(GateCheck(
            name="min_sum_pnl_pct",
            passed=passed,
            value=sum_pnl if sum_pnl != INVALID_METRIC_VALUE else None,
            threshold=float(min_sum_pnl),
            message=f"OOS Sum PnL %: {sum_pnl:.2f} (required: >= {min_sum_pnl})" if sum_pnl != INVALID_METRIC_VALUE else "OOS Sum PnL %: INVALID"
        ))
        if not passed:
            fail_count += 1
    
    # Check: max_drawdown (note: max_drawdown is negative, threshold is positive %)
    # Use _normalize_dd_to_pct to handle both fraction (-0.5375) and percent (-53.75) formats
    max_dd_threshold = gates_cfg.get("max_drawdown")
    if max_dd_threshold is not None:
        max_dd = oos_metrics.get("max_drawdown", INVALID_METRIC_VALUE)
        max_dd_pct = _normalize_dd_to_pct(max_dd)
        passed = max_dd_pct is not None and max_dd_pct <= max_dd_threshold
        checks.append(GateCheck(
            name="max_drawdown",
            passed=passed,
            value=max_dd_pct,
            threshold=float(max_dd_threshold),
            message=f"OOS Max DD: {max_dd_pct:.1f}% (required: <= {max_dd_threshold}%)" if max_dd_pct is not None else "OOS Max DD: INVALID"
        ))
        if not passed:
            fail_count += 1
    
    # Check: min_profit_factor
    min_pf = gates_cfg.get("min_profit_factor")
    if min_pf is not None:
        pf = oos_metrics.get("profit_factor", INVALID_METRIC_VALUE)
        # Handle infinity (all winning trades)
        if pf == INVALID_METRIC_VALUE:
            passed = False
        elif np.isinf(pf):
            passed = True  # Infinity is always > threshold
        else:
            passed = pf >= min_pf
        
        checks.append(GateCheck(
            name="min_profit_factor",
            passed=passed,
            value=pf if pf != INVALID_METRIC_VALUE and not np.isinf(pf) else None,
            threshold=float(min_pf),
            message=f"OOS Profit Factor: {pf:.2f} (required: >= {min_pf})" if pf != INVALID_METRIC_VALUE and not np.isinf(pf) else ("OOS Profit Factor: INF" if np.isinf(pf) else "OOS Profit Factor: INVALID")
        ))
        if not passed:
            fail_count += 1
    
    # Per-step checks: only consider steps that were NOT skipped
    # (Skipped = no valid trials, OOS backtest not run → gates were never evaluated)
    step_failures = []
    
    # Check: step_min_sortino
    step_min_sortino = gates_cfg.get("step_min_sortino")
    if step_min_sortino is not None:
        for step in wf_result.steps:
            if _is_step_skipped(step):
                continue  # Skip does NOT count as FAIL
            sortino = step.test_metrics.get("sortino", INVALID_METRIC_VALUE) if step.test_metrics else INVALID_METRIC_VALUE
            # INVALID_METRIC_VALUE (-999.0) means undefined downside (no losing trades),
            # not a bad result — skip the threshold check rather than treating it as a fail.
            # NaN is treated the same way.
            if sortino == INVALID_METRIC_VALUE or (isinstance(sortino, float) and np.isnan(sortino)):
                continue  # Undefined sortino does NOT count as a step fail
            if sortino < step_min_sortino:
                step_failures.append(f"Step {step.step_index}: Sortino {sortino:.3f} < {step_min_sortino}")
    
    # Check: step_min_sum_pnl_pct
    step_min_pnl = gates_cfg.get("step_min_sum_pnl_pct")
    if step_min_pnl is not None:
        for step in wf_result.steps:
            if _is_step_skipped(step):
                continue  # Skip does NOT count as FAIL
            sum_pnl = step.test_metrics.get("sum_pnl_pct", INVALID_METRIC_VALUE) if step.test_metrics else INVALID_METRIC_VALUE
            if sum_pnl == INVALID_METRIC_VALUE or sum_pnl < step_min_pnl:
                step_failures.append(f"Step {step.step_index}: Sum PnL % {sum_pnl:.2f} < {step_min_pnl}")
    
    # Check: max_fail_steps
    max_fail_steps = gates_cfg.get("max_fail_steps", 0)
    if len(step_failures) > 0:
        passed = len(step_failures) <= max_fail_steps
        checks.append(GateCheck(
            name="max_fail_steps",
            passed=passed,
            value=float(len(step_failures)),
            threshold=float(max_fail_steps),
            message=f"Failed steps: {len(step_failures)} (max allowed: {max_fail_steps}). Failures: {'; '.join(step_failures[:3])}"
        ))
        if not passed:
            fail_count += 1
    
    # Overall result
    overall_passed = fail_count == 0
    
    return WalkForwardGatesResult(
        enabled=True,
        passed=overall_passed,
        checks=checks,
        fail_count=fail_count
    )


def _compute_ref_oos_for_selected(
    steps: List['WFStepResult'],
    open_prices: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    index: pd.Index,
    selected_atr_period: Optional[int],
    selected_multiplier: Optional[float],
    config: Dict[str, Any],
) -> None:
    """
    Run a reference OOS backtest for the consensus-selected params on every step.

    Fills step.ref_atr_period, step.ref_multiplier, step.ref_test_metrics in-place.
    Does NOT touch step.step_status, test_metrics, or any aggregation/gates data.
    Gracefully skips if selected params are None.

    Uses the same prepend-based OOS logic as _fill_oos_metrics_for_topk (FIX 2):
    runs the backtest on [test_start_idx - prepend_bars : test_end_idx] so the
    SuperTrend indicator is warm at the OOS boundary, then slices the raw arrays
    to the OOS-only portion and computes metrics with warmup=0.
    """
    if selected_atr_period is None or selected_multiplier is None:
        return

    from supertrend_optimizer.utils.warmup import calculate_warmup
    from supertrend_optimizer.core.metrics import calculate_all_metrics

    bt_cfg = config.get("backtest", {})
    val_cfg = config.get("validation", {})
    wf_cfg = val_cfg.get("walk_forward", {})

    oos_early_exit = wf_cfg.get("oos_early_exit_enabled", bt_cfg.get("early_exit_enabled", False))
    oos_check_bars = bt_cfg.get("early_exit_check_bars", 0) if oos_early_exit else 0

    # TRAIN early_exit mirrors what the optimizer uses (bt_cfg.early_exit_enabled directly)
    train_early_exit = bt_cfg.get("early_exit_enabled", False)
    train_check_bars = bt_cfg.get("early_exit_check_bars", 0) if train_early_exit else 0

    trade_mode = config.get("optimization", {}).get("trade_mode", "revers")
    commission = bt_cfg.get("commission", 0.0)
    warmup_period = val_cfg.get("warmup_period", 0)
    early_exit_max_drawdown = bt_cfg.get("early_exit_max_drawdown", 0.5)
    min_trades_required = bt_cfg.get("min_trades_required", 3)

    exec_model_val = bt_cfg.get("execution_model")
    execution_model = (
        ExecutionModel(exec_model_val)
        if exec_model_val is not None
        else ExecutionModel.OPEN_TO_OPEN
    )

    # Resolve annualization settings for per-step periods_per_year
    ann_factor = bt_cfg.get("annualization_factor", 252)
    _market_str = bt_cfg.get("market", None)
    _explicit_basis = bt_cfg.get("annualization_basis", None)
    _market_enum = MarketType(_market_str) if _market_str is not None else None

    # Compute prepend_bars once for the full dataset (same formula as TOPK path).
    _n_full = len(open_prices)
    prepend_bars_full = calculate_warmup(_n_full, config)

    _logger = logging.getLogger(__name__)

    for step in steps:
        test_start_idx = step.test_start_idx
        test_end_idx = step.test_end_idx

        # --- Prepend logic (1-in-1 with _fill_oos_metrics_for_topk / FIX 2) ---
        prepend_bars = min(prepend_bars_full, test_start_idx)
        ext_start_idx = test_start_idx - prepend_bars

        ext_open  = open_prices[ext_start_idx:test_end_idx]
        ext_high  = high[ext_start_idx:test_end_idx]
        ext_low   = low[ext_start_idx:test_end_idx]
        ext_close = close[ext_start_idx:test_end_idx]
        ext_index = index[ext_start_idx:test_end_idx]

        # Resolve periods_per_year against the OOS index (not the extended slice).
        oos_index = index[test_start_idx:test_end_idx]
        if isinstance(ann_factor, str) and ann_factor == "auto":
            if isinstance(oos_index, pd.DatetimeIndex):
                periods_per_year = resolve_periods_per_year_from_config(
                    config_value=ann_factor,
                    index=oos_index,
                    explicit_basis=_explicit_basis,
                    market=_market_enum,
                )
            else:
                periods_per_year = 252.0
        else:
            periods_per_year = float(ann_factor)

        _logger.debug(
            "Ref OOS prepend: test_start_idx=%d prepend_bars=%d ext_start_idx=%d test_end_idx=%d",
            test_start_idx, prepend_bars, ext_start_idx, test_end_idx,
        )

        ext_result = run_single_backtest(
            open_prices=ext_open,
            high=ext_high,
            low=ext_low,
            close=ext_close,
            index=ext_index,
            atr_period=selected_atr_period,
            multiplier=selected_multiplier,
            trade_mode=trade_mode,
            commission=commission,
            warmup_period=0,
            early_exit_enabled=oos_early_exit,
            early_exit_max_drawdown=early_exit_max_drawdown,
            early_exit_check_bars=oos_check_bars,
            periods_per_year=periods_per_year,
            min_trades_required=min_trades_required,
            extract_trades_flag=False,
            caller_mode="optimizer",
            execution_model=execution_model,
            auto_warmup=False,
        )

        ext_returns   = getattr(ext_result, "returns", None)
        ext_equity    = getattr(ext_result, "equity_curve", None)
        ext_positions = getattr(ext_result, "positions", None)

        if ext_returns is None or ext_equity is None or ext_positions is None or prepend_bars == 0:
            # Fallback: use metrics dict directly (no raw arrays available, or no prepend).
            ref_metrics = ext_result.metrics
        else:
            oos_boundary = prepend_bars
            if oos_boundary >= len(ext_returns):
                _logger.warning(
                    "Ref OOS prepend boundary=%d >= len(ext_returns)=%d; "
                    "falling back to full-slice metrics.",
                    oos_boundary, len(ext_returns),
                )
                oos_returns   = ext_returns
                oos_equity    = ext_equity
                oos_positions = ext_positions
            else:
                oos_returns   = ext_returns[oos_boundary:]
                oos_equity    = ext_equity[oos_boundary:]
                oos_positions = ext_positions[oos_boundary:]

            ref_metrics = calculate_all_metrics(
                returns=oos_returns,
                equity_curve=oos_equity,
                positions=oos_positions,
                warmup_period=0,
                periods_per_year=periods_per_year,
                min_trades_required=min_trades_required,
            )

        step.ref_atr_period = selected_atr_period
        step.ref_multiplier = selected_multiplier
        step.ref_test_metrics = ref_metrics

        # --- Ref TRAIN: backtest selected params on the TRAIN slice (display-only) ---
        train_open = open_prices[step.train_start_idx:step.train_end_idx]
        train_high = high[step.train_start_idx:step.train_end_idx]
        train_low = low[step.train_start_idx:step.train_end_idx]
        train_close = close[step.train_start_idx:step.train_end_idx]
        train_index = index[step.train_start_idx:step.train_end_idx]

        if isinstance(ann_factor, str) and ann_factor == "auto":
            if isinstance(train_index, pd.DatetimeIndex):
                train_periods_per_year = resolve_periods_per_year_from_config(
                    config_value=ann_factor,
                    index=train_index,
                    explicit_basis=_explicit_basis,
                    market=_market_enum,
                )
            else:
                train_periods_per_year = 252.0
        else:
            train_periods_per_year = float(ann_factor)

        ref_train_result = run_single_backtest(
            open_prices=train_open,
            high=train_high,
            low=train_low,
            close=train_close,
            index=train_index,
            atr_period=selected_atr_period,
            multiplier=selected_multiplier,
            trade_mode=trade_mode,
            commission=commission,
            warmup_period=warmup_period,
            early_exit_enabled=train_early_exit,
            early_exit_max_drawdown=early_exit_max_drawdown,
            early_exit_check_bars=train_check_bars,
            periods_per_year=train_periods_per_year,
            min_trades_required=min_trades_required,
            extract_trades_flag=False,
            caller_mode="optimizer",
            execution_model=execution_model,
            auto_warmup=True,
        )

        step.ref_train_metrics = ref_train_result.metrics


def run_walk_forward(
    open_prices: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    index: pd.Index,
    config: Dict[str, Any],
) -> WalkForwardResult:
    """
    Run Walk-Forward optimization and validation.

    Parameters
    ----------
    open_prices : np.ndarray
        Open prices array
    high : np.ndarray
        High prices array
    low : np.ndarray
        Low prices array
    close : np.ndarray
        Close prices array
    index : pd.Index
        Time index (DatetimeIndex or RangeIndex)
    config : Dict[str, Any]
        Full configuration dictionary

    Returns
    -------
    WalkForwardResult
        Walk-Forward results with per-step and aggregated metrics

    Raises
    ------
    ValueError
        If input validation fails or WF is not enabled
    """
    logger = logging.getLogger(__name__)

    # Validate inputs
    n = len(index)
    if not (len(open_prices) == len(high) == len(low) == len(close) == n):
        raise ValueError(
            f"All price arrays and index must have the same length. "
            f"Got: open={len(open_prices)}, high={len(high)}, low={len(low)}, "
            f"close={len(close)}, index={n}"
        )

    # Ensure warmup_period is resolved (idempotent when CLI already did it)
    config = apply_auto_warmup_to_config(config, n)
    logger.info(
        "run_walk_forward: warmup_bars_resolved=%d (warmup_period_auto=%s, n=%d)",
        config["validation"]["warmup_period"],
        config.get("validation", {}).get("warmup_period_auto", False),
        n,
    )

    # Check if WF is enabled
    if "validation" not in config or "walk_forward" not in config["validation"]:
        raise ValueError("walk_forward configuration not found in config")

    wf_cfg = config["validation"]["walk_forward"]

    if not wf_cfg.get("enabled", False):
        raise ValueError("walk_forward.enabled must be true")

    # Extract WF parameters
    train_size = wf_cfg["train_size"]
    test_size = wf_cfg["test_size"]
    step_size = wf_cfg.get("step_size")
    scheme = wf_cfg.get("scheme", "rolling")
    anchor = wf_cfg.get("anchor", "start")
    min_train_bars = wf_cfg.get("min_train_bars", 500)
    min_test_bars = wf_cfg.get("min_test_bars", 100)

    # Generate windows
    windows = make_walk_forward_slices(
        index=index,
        train_size=train_size,
        test_size=test_size,
        step_size=step_size,
        scheme=scheme,
        anchor=anchor,
        min_train_bars=min_train_bars,
        min_test_bars=min_test_bars
    )

    if len(windows) == 0:
        raise ValueError("No Walk-Forward windows were generated")

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 5 / Phase 2: Mini-Grid state initialization
    # ═══════════════════════════════════════════════════════════════════════
    mini_grid_enabled = is_mini_grid_enabled(config)
    mini_grid_state: Optional[MiniGridState] = None
    agg_counters: Optional[GridEvaluationCounters] = None

    # Phase 2: resolve discovery steps set
    _mg_cfg = config.get("walk_forward", {}).get("mini_grid", {})
    _disc_cfg = _mg_cfg.get("discovery", {})
    _normalize_score: bool = _disc_cfg.get("normalize_score", False)

    if mini_grid_enabled:
        # Resolve discovery steps using total window count
        discovery_step_indices: List[int] = resolve_discovery_steps(
            total_wf_steps=len(windows),
            mg_cfg=_mg_cfg,
        )
        _discovery_set = set(discovery_step_indices)

        # Storage for multi-step discovery: opt_results per discovery step
        _discovery_opt_results: Dict[int, Any] = {}  # step_index → OptimizationResult

        # Initialize counters for eval steps (non-discovery steps in mini-grid mode)
        agg_counters = GridEvaluationCounters(
            attempted=0,
            early_exit_count=0,
            invalid_objective_count=0,
            kept=0
        )
        mini_grid_trials_list: List[Any] = []

        logger.info(
            "Mini-grid mode enabled: discovery_steps=%s, normalize_score=%s",
            discovery_step_indices, _normalize_score,
        )
    else:
        discovery_step_indices = []
        _discovery_set = set()
        _discovery_opt_results = {}
        mini_grid_trials_list = []

    # Surface layer: collect train trials across all steps (discovery + eval + legacy)
    # when save_all_trials is enabled (default True).
    _surface_cfg = config.get("walk_forward", {}).get("surface", {})
    _save_all_trials: bool = _surface_cfg.get("save_all_trials", True)
    all_step_trials_list: List[Any] = []

    # Process each window
    steps: List[WFStepResult] = []

    for window in windows:
        # Slice train data
        train_open = open_prices[window.train_start_idx:window.train_end_idx]
        train_high = high[window.train_start_idx:window.train_end_idx]
        train_low = low[window.train_start_idx:window.train_end_idx]
        train_close = close[window.train_start_idx:window.train_end_idx]
        train_index = index[window.train_start_idx:window.train_end_idx]

        # ═══════════════════════════════════════════════════════════════════
        # PHASE 5 / Phase 2: Branching logic — discovery vs eval vs legacy
        # ═══════════════════════════════════════════════════════════════════
        if mini_grid_enabled:
            step_idx = window.step_index

            if step_idx in _discovery_set:
                # ── DISCOVERY STEP: run Optuna, collect opt_result ──────────
                opt_result = run_single_optimization(
                    open_prices=train_open,
                    high=train_high,
                    low=train_low,
                    close=train_close,
                    index=train_index,
                    config=config
                )

                # ── MiniGrid Refine on discovery step (Etap 4) ───────────────
                _ref_cfg = config.get("walk_forward", {}).get("mini_grid_refine", {})
                _refine_seed_source = _ref_cfg.get("seed_source", "optuna")
                opt_result_raw = opt_result  # always keep raw for seed_source="optuna"

                if _ref_cfg.get("enabled", False):
                    opt_result = run_minigrid_refine(
                        opt_result=opt_result,
                        train_open=train_open,
                        train_high=train_high,
                        train_low=train_low,
                        train_close=train_close,
                        train_index=train_index,
                        config=config,
                        step_idx=step_idx,
                    )
                    _rmeta_disc = getattr(opt_result, "_refine_meta", None)
                    if _rmeta_disc is not None:
                        logger.info(
                            "Refine(discovery) step=%d seed_source=%s best_changed=%s "
                            "grid_total=%d grid_valid=%d early_exit_ratio=%.3f plateau_width=%.4f",
                            step_idx, _refine_seed_source,
                            _rmeta_disc.get("best_changed", False),
                            _rmeta_disc.get("grid_size", 0),
                            _rmeta_disc.get("grid_points_valid", 0),
                            _rmeta_disc.get("early_exit_ratio", 0.0),
                            _rmeta_disc.get("plateau_width", 0.0),
                        )

                # seed_source controls what goes into the discovery storage
                # "optuna"  → seeds from raw Optuna result (before refine)
                # "refined" → seeds from refined result
                _opt_for_seeds = opt_result_raw if _refine_seed_source == "optuna" else opt_result
                _discovery_opt_results[step_idx] = _opt_for_seeds

                # Surface layer: collect trials for discovery step (Optuna or merged refine)
                if _save_all_trials:
                    _disc_trials = getattr(opt_result, "trials_df", None)
                    if _disc_trials is not None and len(_disc_trials) > 0:
                        _disc_copy = _disc_trials.copy()
                        _disc_copy["step_id"] = step_idx
                        _disc_copy["surface_source_type"] = "optuna"
                        all_step_trials_list.append(_disc_copy)

                # Build mini-grid once all discovery steps have been processed
                all_discovery_done = all(
                    i in _discovery_opt_results for i in discovery_step_indices
                    if i <= step_idx
                ) and (step_idx == max(discovery_step_indices))

                if all_discovery_done:
                    # ── PASS 1 → PASS 2: Build grid from merged seeds ───────
                    mult_step_val = config.get("optimization", {}).get("multiplier_step", 0.1)
                    top_n_seeds = _mg_cfg.get("top_n_seeds", 15)

                    all_step_seeds: List[List[Dict[str, Any]]] = []
                    for disc_idx in sorted(_discovery_opt_results.keys()):
                        step_seeds = extract_step_seeds(
                            _discovery_opt_results[disc_idx],
                            config,
                            step_index=disc_idx,
                        )
                        all_step_seeds.append(step_seeds)

                    _num_raw_seeds_total = sum(len(ss) for ss in all_step_seeds)

                    merged = merge_seeds_across_steps(
                        all_step_seeds,
                        top_n=top_n_seeds,
                        mult_step=mult_step_val,
                        normalize=_normalize_score,
                    )
                    _num_unique_merged = len(merged)

                    # Safety gate: filter bad / low-coverage seeds
                    merged = _filter_merged_seeds(merged, _mg_cfg)

                    _grid_t0 = time.perf_counter()
                    # source_step = first discovery step for metadata
                    mini_grid_state = build_mini_grid(
                        opt_result=_discovery_opt_results[discovery_step_indices[0]],
                        config=config,
                        source_step=discovery_step_indices[0],
                        merged_seeds=merged,
                    )
                    _grid_build_ms = int((time.perf_counter() - _grid_t0) * 1000)

                    _single_step_mode = len(discovery_step_indices) == 1

                    # Populate reproducibility + selected seed fields
                    _top_m_resolved = _mg_cfg.get("top_m_per_step") or (
                        _mg_cfg.get("top_n_seeds", 15) * 3
                    )
                    _min_appear_cfg = _disc_cfg.get("min_appear_count", None)
                    mini_grid_state.resolved_discovery_steps = list(discovery_step_indices)
                    mini_grid_state.resolved_top_m_per_step = _top_m_resolved
                    mini_grid_state.resolved_min_appear_count = _min_appear_cfg
                    mini_grid_state.single_step_mode = _single_step_mode
                    if merged:
                        _ts0 = merged[0]
                        mini_grid_state.selected_seed_atr = _ts0.atr_period
                        mini_grid_state.selected_seed_multiplier = _ts0.multiplier
                        mini_grid_state.selected_seed_mult_ticks = _ts0.mult_ticks

                    _top_seed_info: Optional[Dict[str, Any]] = None
                    if merged:
                        _ts = merged[0]
                        _top_seed_info = {
                            "atr_period": _ts.atr_period,
                            "multiplier": _ts.multiplier,
                            "mult_ticks": _ts.mult_ticks,
                            "appear_count": _ts.appear_count,
                            "score_mean": round(_ts.score_mean, 4),
                            "score_min": round(_ts.score_min, 4),
                        }

                    if len(mini_grid_state.params) == 0:
                        logger.warning(
                            "Mini-grid skipped: no seeds from %d discovery step(s). "
                            "Remaining steps will produce skipped results.",
                            len(discovery_step_indices),
                        )
                    else:
                        logger.info(
                            "MiniGrid discovery complete: %s",
                            {
                                "discovery_steps": discovery_step_indices,
                                "single_step_mode": _single_step_mode,
                                "normalize_score": _normalize_score,
                                "num_raw_seeds_total": _num_raw_seeds_total,
                                "num_unique_seeds_merged": _num_unique_merged,
                                "top_seed": _top_seed_info,
                                "grid_size_requested": mini_grid_state.grid_size_before_truncate,
                                "grid_size_unique": len(mini_grid_state.params),
                                "fallback_used": mini_grid_state.seed_source != "robustness_df",
                                "fallback_reason": (
                                    mini_grid_state.seed_source
                                    if mini_grid_state.seed_source != "robustness_df"
                                    else None
                                ),
                                "grid_build_ms": _grid_build_ms,
                            },
                        )

                        # ── Performance guardrails (WARNING only, no exceptions) ──
                        _GRID_BUILD_WARN_MS = 2000
                        if _grid_build_ms > _GRID_BUILD_WARN_MS:
                            logger.warning(
                                "MiniGrid grid_build_ms=%d exceeds threshold=%d ms. "
                                "Consider reducing max_grid_size or grid deltas.",
                                _grid_build_ms, _GRID_BUILD_WARN_MS,
                            )

                        _low_seed_threshold = max(1, top_n_seeds // 2)
                        if _num_unique_merged < _low_seed_threshold:
                            logger.warning(
                                "MiniGrid num_unique_seeds_merged=%d < top_n_seeds/2=%d. "
                                "Discovery may be under-sampling: check discovery steps, "
                                "data quality, or reduce min_appear_count.",
                                _num_unique_merged, _low_seed_threshold,
                            )

                elif mini_grid_state is None:
                    # Not all discovery steps done yet — use this step's result
                    # as best single-step grid temporarily (will be replaced)
                    pass  # mini_grid_state remains None until all discovery done

            else:
                # ── EVAL STEP: evaluate fixed grid ──────────────────────────
                if mini_grid_state is None:
                    # Discovery not yet complete (shouldn't normally happen with
                    # first_only/first_k modes since step 0 always runs first)
                    logger.warning(
                        "Step %d: mini_grid_state not built yet (discovery incomplete), "
                        "running legacy Optuna for this step.",
                        step_idx,
                    )
                    opt_result = run_single_optimization(
                        open_prices=train_open,
                        high=train_high,
                        low=train_low,
                        close=train_close,
                        index=train_index,
                        config=config
                    )
                elif len(mini_grid_state.params) == 0:
                    logger.warning(
                        "Mini-grid evaluation skipped for step %d: no seeds available.", step_idx
                    )
                    opt_result = create_empty_opt_result(
                        config, attempted=0, early_exit_count=0, invalid_objective_count=0
                    )
                else:
                    opt_result = evaluate_mini_grid(
                        param_grid=mini_grid_state.params,
                        open_prices=train_open,
                        high=train_high,
                        low=train_low,
                        close=train_close,
                        index=train_index,
                        config=config,
                    )

                    # Aggregate counters (only for eval steps)
                    kept_count = (
                        opt_result.n_trials
                        - opt_result.early_exit_count
                        - opt_result.invalid_objective_count
                    )
                    agg_counters.attempted += opt_result.n_trials
                    agg_counters.early_exit_count += opt_result.early_exit_count
                    agg_counters.invalid_objective_count += opt_result.invalid_objective_count
                    agg_counters.kept += kept_count

                    # Collect MiniGrid trials for Excel export
                    if opt_result.trials_df is not None and len(opt_result.trials_df) > 0:
                        trials_copy = opt_result.trials_df.copy()
                        trials_copy["step_id"] = step_idx
                        mini_grid_trials_list.append(trials_copy)

                        # Surface layer: collect eval trials
                        if _save_all_trials:
                            _eval_copy = trials_copy.copy()
                            _eval_copy["surface_source_type"] = "mini_grid"
                            all_step_trials_list.append(_eval_copy)

                    _eval_fallback = mini_grid_state.seed_source != "robustness_df"
                    logger.info(
                        "MiniGrid eval step %d: %s",
                        step_idx,
                        {
                            "attempted": opt_result.n_trials,
                            "kept": kept_count,
                            "early_exit": opt_result.early_exit_count,
                            "invalid_objective": opt_result.invalid_objective_count,
                            "grid_size": len(mini_grid_state.params),
                            "fallback_used": _eval_fallback,
                            "fallback_reason": (
                                mini_grid_state.seed_source if _eval_fallback else None
                            ),
                        },
                    )
        else:
            # ── LEGACY: Optuna on every step ────────────────────────────────
            opt_result = run_single_optimization(
                open_prices=train_open,
                high=train_high,
                low=train_low,
                close=train_close,
                index=train_index,
                config=config
            )

            # ── MiniGrid Refine (Stage 2) — legacy branch only ───────────────
            _ref_cfg = config.get("walk_forward", {}).get("mini_grid_refine", {})
            if _ref_cfg.get("enabled", False):
                step_idx = window.step_index
                opt_result = run_minigrid_refine(
                    opt_result=opt_result,
                    train_open=train_open,
                    train_high=train_high,
                    train_low=train_low,
                    train_close=train_close,
                    train_index=train_index,
                    config=config,
                    step_idx=step_idx,
                )
                _rmeta = getattr(opt_result, "_refine_meta", None)
                if _rmeta is not None:
                    logger.info(
                        "Step %d: MiniGrid Refine — grid_total=%d grid_valid=%d "
                        "early_exit_ratio=%.3f plateau_width=%.4f best_changed=%s",
                        step_idx,
                        _rmeta.get("grid_size", 0),
                        _rmeta.get("grid_points_valid", 0),
                        _rmeta.get("early_exit_ratio", 0.0),
                        _rmeta.get("plateau_width", 0.0),
                        _rmeta.get("best_changed", False),
                    )

        # Capture refine metadata (set by run_minigrid_refine; None when not run)
        _step_refine_meta: Optional[Dict[str, Any]] = getattr(opt_result, "_refine_meta", None)

        # Surface layer: collect trials for legacy branch (Optuna ± refine)
        # Mini-grid branches already collected above (discovery / eval sections).
        if not mini_grid_enabled and _save_all_trials:
            _legacy_step_idx = window.step_index
            _legacy_trials = getattr(opt_result, "trials_df", None)
            if _legacy_trials is not None and len(_legacy_trials) > 0:
                _legacy_copy = _legacy_trials.copy()
                _legacy_copy["step_id"] = _legacy_step_idx
                _has_refine = _step_refine_meta is not None
                _legacy_copy["surface_source_type"] = (
                    "optuna_refine" if _has_refine else "optuna"
                )
                all_step_trials_list.append(_legacy_copy)

        # Extract best parameters
        best_atr_period = opt_result.best_atr_period
        best_multiplier = opt_result.best_multiplier
        train_objective_value = opt_result.best_value
        train_metrics = getattr(opt_result, "best_metrics", {})
        
        # === PR4: Build TOP-K entries for this WF step ===
        objective_metric = config.get("optimization", {}).get("objective_metric", "sortino")
        top_entries, passed_count, top_export_count, top_consensus_count = _build_topk_entries(
            opt_result=opt_result,
            objective_metric=objective_metric,
            config=config
        )

        # Slice test data
        test_open = open_prices[window.test_start_idx:window.test_end_idx]
        test_high = high[window.test_start_idx:window.test_end_idx]
        test_low = low[window.test_start_idx:window.test_end_idx]
        test_close = close[window.test_start_idx:window.test_end_idx]
        test_index = index[window.test_start_idx:window.test_end_idx]

        # Resolve periods_per_year for test window (handles "auto")
        _bt_cfg_oos = config.get("backtest", {})
        ann_factor = _bt_cfg_oos.get("annualization_factor", 252)
        _oos_market_str = _bt_cfg_oos.get("market", None)
        _oos_explicit_basis = _bt_cfg_oos.get("annualization_basis", None)
        _oos_market_enum = MarketType(_oos_market_str) if _oos_market_str is not None else None
        if isinstance(ann_factor, str) and ann_factor == "auto":
            if isinstance(test_index, pd.DatetimeIndex):
                periods_per_year = resolve_periods_per_year_from_config(
                    config_value=ann_factor,
                    index=test_index,
                    explicit_basis=_oos_explicit_basis,
                    market=_oos_market_enum,
                )
            else:
                periods_per_year = 252.0
        else:
            periods_per_year = float(ann_factor)

        # Resolve execution_model
        exec_model_val = config.get("backtest", {}).get("execution_model")
        execution_model = (
            ExecutionModel(exec_model_val)
            if exec_model_val is not None
            else ExecutionModel.OPEN_TO_OPEN
        )

        # === PR5: Fill OOS metrics for TOP-K entries ===
        # Pass full arrays + window boundaries so _fill_oos_metrics_for_topk can
        # prepend history before test_start_idx for indicator warm-up (FIX 2).
        _retain_arrays = config.get("diversification", {}).get("enabled", False)
        _fill_oos_metrics_for_topk(
            top_entries=top_entries,
            test_open=test_open,
            test_high=test_high,
            test_low=test_low,
            test_close=test_close,
            test_index=test_index,
            config=config,
            periods_per_year=periods_per_year,
            execution_model=execution_model,
            full_open=open_prices,
            full_high=high,
            full_low=low,
            full_close=close,
            full_index=index,
            test_start_idx=window.test_start_idx,
            test_end_idx=window.test_end_idx,
            retain_oos_arrays=_retain_arrays,
        )

        # Run backtest on test data with best parameters
        bt_cfg = config.get("backtest", {})
        val_cfg = config.get("validation", {})
        wf_cfg = val_cfg.get("walk_forward", {})
        
        # Skip OOS backtest when optimization returned no valid params (empty result)
        if best_atr_period is None or best_multiplier is None:
            test_metrics = {}
            test_returns = None
            test_trades_df = None
            test_early_exit = None
            train_trades_df = None
            logger.warning(
                f"Step {window.step_index}: Optimization returned no valid trials; skipping OOS backtest. "
                f"Train window may be too small or all trials invalid."
            )
        else:
            # Use OOS early_exit setting (same as for TOP-K entries)
            oos_early_exit = wf_cfg.get("oos_early_exit_enabled", bt_cfg.get("early_exit_enabled", False))
            
            # If early_exit is disabled for OOS, set check_bars to 0 to ensure no checking
            oos_check_bars = bt_cfg.get("early_exit_check_bars", 0) if oos_early_exit else 0

            # F-07: Prepend warmup bars from end of train window so SuperTrend
            # indicator is stable at the first OOS bar (same logic as TOP-K path).
            from supertrend_optimizer.utils.warmup import calculate_warmup
            _prepend_bars = calculate_warmup(len(open_prices), config)
            _prepend_bars = min(_prepend_bars, window.test_start_idx)

            if _prepend_bars > 0:
                _ext_start = window.test_start_idx - _prepend_bars
                ext_open  = open_prices[_ext_start:window.test_end_idx]
                ext_high  = high[_ext_start:window.test_end_idx]
                ext_low   = low[_ext_start:window.test_end_idx]
                ext_close = close[_ext_start:window.test_end_idx]
                ext_index = index[_ext_start:window.test_end_idx]

                logger.debug(
                    "OOS main prepend: step=%d test_start=%d prepend=%d ext_start=%d",
                    window.step_index, window.test_start_idx, _prepend_bars, _ext_start,
                )

                ext_result = run_single_backtest(
                    open_prices=ext_open,
                    high=ext_high,
                    low=ext_low,
                    close=ext_close,
                    index=ext_index,
                    atr_period=best_atr_period,
                    multiplier=best_multiplier,
                    trade_mode=config.get("optimization", {}).get("trade_mode", "revers"),
                    commission=bt_cfg.get("commission", 0.0),
                    warmup_period=val_cfg.get("warmup_period", 0),
                    early_exit_enabled=oos_early_exit,
                    early_exit_max_drawdown=bt_cfg.get("early_exit_max_drawdown", 0.5),
                    early_exit_check_bars=oos_check_bars,
                    periods_per_year=periods_per_year,
                    min_trades_required=bt_cfg.get("min_trades_required", 3),
                    extract_trades_flag=True,
                    caller_mode="optimizer",
                    execution_model=execution_model,
                    auto_warmup=True,
                )

                _ext_returns   = ext_result.returns
                _ext_equity    = getattr(ext_result, "equity_curve", None)
                _ext_positions = getattr(ext_result, "positions", None)

                _oos_bnd = _prepend_bars
                if (_ext_returns is not None
                        and _ext_equity is not None
                        and _ext_positions is not None
                        and _oos_bnd < len(_ext_returns)):
                    oos_returns   = _ext_returns[_oos_bnd:]
                    oos_equity    = _ext_equity[_oos_bnd:]
                    oos_positions = _ext_positions[_oos_bnd:]

                    from supertrend_optimizer.core.metrics import calculate_all_metrics
                    test_metrics = calculate_all_metrics(
                        returns=oos_returns,
                        equity_curve=oos_equity,
                        positions=oos_positions,
                        warmup_period=0,
                        periods_per_year=periods_per_year,
                        min_trades_required=bt_cfg.get("min_trades_required", 3),
                    )
                    test_returns = oos_returns

                    # Filter trades_df to OOS-only portion
                    _ext_trades = ext_result.trades_df
                    if (_ext_trades is not None
                            and not _ext_trades.empty
                            and "entry_index" in _ext_trades.columns):
                        test_trades_df = _ext_trades[
                            _ext_trades["entry_index"] >= _oos_bnd
                        ].copy()
                        if not test_trades_df.empty:
                            test_trades_df["entry_index"] = (
                                test_trades_df["entry_index"] - _oos_bnd
                            )
                            test_trades_df["exit_index"] = (
                                test_trades_df["exit_index"] - _oos_bnd
                            )
                    else:
                        test_trades_df = _ext_trades

                    test_early_exit = getattr(ext_result, "early_exit", None)
                else:
                    logger.warning(
                        "OOS prepend boundary=%d >= len(returns)=%d for step %d; "
                        "falling back to plain OOS backtest.",
                        _oos_bnd,
                        len(_ext_returns) if _ext_returns is not None else 0,
                        window.step_index,
                    )
                    test_metrics = ext_result.metrics
                    test_returns = _ext_returns
                    test_trades_df = ext_result.trades_df
                    test_early_exit = getattr(ext_result, "early_exit", None)
            else:
                # No prepend possible (first step or test_start_idx == 0)
                test_result = run_single_backtest(
                    open_prices=test_open,
                    high=test_high,
                    low=test_low,
                    close=test_close,
                    index=test_index,
                    atr_period=best_atr_period,
                    multiplier=best_multiplier,
                    trade_mode=config.get("optimization", {}).get("trade_mode", "revers"),
                    commission=bt_cfg.get("commission", 0.0),
                    warmup_period=val_cfg.get("warmup_period", 0),
                    early_exit_enabled=oos_early_exit,
                    early_exit_max_drawdown=bt_cfg.get("early_exit_max_drawdown", 0.5),
                    early_exit_check_bars=oos_check_bars,
                    periods_per_year=periods_per_year,
                    min_trades_required=bt_cfg.get("min_trades_required", 3),
                    extract_trades_flag=True,
                    caller_mode="optimizer",
                    execution_model=execution_model,
                    auto_warmup=True,
                )
                test_metrics = test_result.metrics
                test_returns = test_result.returns
                test_trades_df = test_result.trades_df
                test_early_exit = getattr(test_result, "early_exit", None)

            # TRAIN trades for best params (mirrors optimizer train-semantics for early_exit)
            train_early_exit = bt_cfg.get("early_exit_enabled", False)
            train_check_bars = bt_cfg.get("early_exit_check_bars", 0) if train_early_exit else 0

            train_bt_result = run_single_backtest(
                open_prices=train_open,
                high=train_high,
                low=train_low,
                close=train_close,
                index=train_index,
                atr_period=best_atr_period,
                multiplier=best_multiplier,
                trade_mode=config.get("optimization", {}).get("trade_mode", "revers"),
                commission=bt_cfg.get("commission", 0.0),
                warmup_period=val_cfg.get("warmup_period", 0),
                early_exit_enabled=train_early_exit,
                early_exit_max_drawdown=bt_cfg.get("early_exit_max_drawdown", 0.5),
                early_exit_check_bars=train_check_bars,
                periods_per_year=periods_per_year,
                min_trades_required=bt_cfg.get("min_trades_required", 3),
                extract_trades_flag=True,
                caller_mode="optimizer",
                execution_model=execution_model,
                auto_warmup=True,
            )
            train_trades_df = train_bt_result.trades_df

        # Get objective value from test metrics (if available)
        objective_metric = config.get("optimization", {}).get("objective_metric", "sortino")
        test_objective_value = test_metrics.get(objective_metric)

        # Step status: SKIPPED when no valid params (OOS backtest not run)
        step_status = "SKIPPED" if (best_atr_period is None or best_multiplier is None) else "OK"

        # Unpack explicit refine fields from _refine_meta (Stage 3)
        _rmeta = _step_refine_meta or {}
        _refine_enabled_step: bool = bool(_rmeta.get("enabled", False))
        _refine_grid_total = int(_rmeta["grid_size"]) if "grid_size" in _rmeta else None
        _refine_grid_valid = int(_rmeta["grid_points_valid"]) if "grid_points_valid" in _rmeta else None
        _refine_grid_early_exit = int(_rmeta["grid_points_early_exit"]) if "grid_points_early_exit" in _rmeta else None
        _refine_plateau_width = float(_rmeta["plateau_width"]) if "plateau_width" in _rmeta else None
        _refine_plateau_depth = float(_rmeta["plateau_depth"]) if "plateau_depth" in _rmeta else None
        _refine_plateau_mono = float(_rmeta["plateau_monotonicity"]) if "plateau_monotonicity" in _rmeta else None
        _refine_best_changed = bool(_rmeta["best_changed"]) if "best_changed" in _rmeta else None

        # Create step result
        step_result = WFStepResult(
            step_index=window.step_index,
            train_start_idx=window.train_start_idx,
            train_end_idx=window.train_end_idx,
            test_start_idx=window.test_start_idx,
            test_end_idx=window.test_end_idx,
            train_start_time=window.train_start_time,
            train_end_time=window.train_end_time,
            test_start_time=window.test_start_time,
            test_end_time=window.test_end_time,
            best_atr_period=best_atr_period,
            best_multiplier=best_multiplier,
            train_objective_value=train_objective_value,
            train_metrics=train_metrics,
            test_objective_value=test_objective_value,
            test_metrics=test_metrics,
            test_returns=test_returns,
            test_trades_df=test_trades_df,
            test_early_exit=test_early_exit,
            train_trades_df=train_trades_df,
            # TOP-K data (PR4)
            top_entries=top_entries,
            passed_count=passed_count,
            top_export_count=top_export_count,
            top_consensus_count=top_consensus_count,
            step_status=step_status,
            # MiniGrid Refine metadata (Stage 2)
            refine_meta=_step_refine_meta,
            # MiniGrid Refine explicit fields (Stage 3)
            refine_enabled=_refine_enabled_step,
            refine_grid_total=_refine_grid_total,
            refine_grid_valid=_refine_grid_valid,
            refine_grid_early_exit=_refine_grid_early_exit,
            refine_plateau_width=_refine_plateau_width,
            refine_plateau_depth=_refine_plateau_depth,
            refine_plateau_monotonicity=_refine_plateau_mono,
            refine_best_changed=_refine_best_changed,
        )

        steps.append(step_result)

    # Get periods_per_year for OOS aggregation
    _bt_cfg_agg = config.get("backtest", {})
    annualization_factor = _bt_cfg_agg.get("annualization_factor", 252)
    _agg_market_str = _bt_cfg_agg.get("market", None)
    _agg_explicit_basis = _bt_cfg_agg.get("annualization_basis", None)
    _agg_market_enum = MarketType(_agg_market_str) if _agg_market_str is not None else None
    if isinstance(annualization_factor, str) and annualization_factor == "auto":
        # Use helper for auto-detection
        if isinstance(index, pd.DatetimeIndex):
            periods_per_year = resolve_periods_per_year_from_config(
                config_value=annualization_factor,
                index=index,
                explicit_basis=_agg_explicit_basis,
                market=_agg_market_enum,
            )
        else:
            # Fallback to default for non-DatetimeIndex
            periods_per_year = 252.0
    else:
        periods_per_year = float(annualization_factor)

    # Get min_trades_required
    min_trades_required = config.get("backtest", {}).get("min_trades_required", 3)

    # Aggregate OOS results
    agg = _aggregate_oos_results(
        steps=steps,
        periods_per_year=periods_per_year,
        min_trades_required=min_trades_required
    )
    
    # === PR6: Calculate consensus by coverage ===
    consensus_df = _calculate_consensus_df(steps, config=config)

    # Aggregate TRAIN trades (per-step best params) — must be before _compute_ref_oos_for_selected,
    # which operates on consensus params and does not produce per-step train trades.
    train_trades_df_agg = _aggregate_train_trades(steps)

    # Combine MiniGrid trials from all steps
    mini_grid_trials_df = None
    if mini_grid_enabled and len(mini_grid_trials_list) > 0:
        mini_grid_trials_df = pd.concat(mini_grid_trials_list, ignore_index=True)
        logger.info(f"Collected {len(mini_grid_trials_df)} MiniGrid trial results across {len(mini_grid_trials_list)} steps")

    # Combine all surface trials (discovery + eval + legacy)
    all_step_trials_df = None
    if _save_all_trials and len(all_step_trials_list) > 0:
        all_step_trials_df = pd.concat(all_step_trials_list, ignore_index=True)
        _surface_steps_available = int(all_step_trials_df["step_id"].nunique())
        logger.info(
            "Surface layer: collected %d train trials across %d/%d steps (source types: %s)",
            len(all_step_trials_df),
            _surface_steps_available,
            len(windows),
            list(all_step_trials_df["surface_source_type"].unique())
            if "surface_source_type" in all_step_trials_df.columns else "unknown",
        )

    # Compute selected params for WF_Summary (consensus only; mini-grid not used)
    selected = _compute_selected_params(steps=steps, consensus_df=consensus_df)

    # valid_step_count: steps with top_entries (for coverage_ratio denominator)
    valid_step_count = sum(1 for s in steps if _is_step_valid_for_consensus(s))
    total_steps = len(steps)
    skipped_step_count = total_steps - valid_step_count
    skipped_step_ratio = skipped_step_count / total_steps if total_steps > 0 else 0.0

    # === Stage 3: Compute refine aggregates ===
    _refine_steps = [s for s in steps if s.refine_enabled]
    if _refine_steps:
        _pw_vals = [s.refine_plateau_width for s in _refine_steps if s.refine_plateau_width is not None]
        _gs_vals = [s.refine_grid_total for s in _refine_steps if s.refine_grid_total is not None]
        _bc_vals = [s for s in _refine_steps if s.refine_best_changed is True]
        _refine_plateau_width_mean = float(np.mean(_pw_vals)) if _pw_vals else None
        _refine_plateau_width_min = float(np.min(_pw_vals)) if _pw_vals else None
        _refine_grid_size_avg = float(np.mean(_gs_vals)) if _gs_vals else None
        _refine_best_changed_count = len(_bc_vals)
    else:
        _refine_plateau_width_mean = None
        _refine_plateau_width_min = None
        _refine_grid_size_avg = None
        _refine_best_changed_count = None

    # Create result (PHASE 5: add mini-grid state and counters)
    wf_result = WalkForwardResult(
        steps=steps,
        oos_trades_df=agg["trades_df"],        # OOS/Test trades (per-step best params)
        train_trades_df=train_trades_df_agg,   # TRAIN trades (per-step best params)
        oos_returns=agg["returns"],
        oos_equity_curve=agg["equity_curve"],
        oos_metrics=agg["metrics"],
        consensus_df=consensus_df,
        total_steps=total_steps,
        valid_step_count=valid_step_count,
        skipped_step_count=skipped_step_count,
        skipped_step_ratio=skipped_step_ratio,
        mini_grid_state=mini_grid_state if mini_grid_enabled else None,
        mini_grid_eval_counters=agg_counters if mini_grid_enabled else None,
        mini_grid_trials=mini_grid_trials_df,
        all_step_trials=all_step_trials_df,
        selected_atr_period=selected["selected_atr_period"],
        selected_multiplier=selected["selected_multiplier"],
        selected_rank=selected["selected_rank"],
        selected_coverage_count=selected["selected_coverage_count"],
        selected_coverage_ratio=selected["selected_coverage_ratio"],
        selected_source=selected["selected_source"],
        # Stage 3: refine aggregates
        refine_plateau_width_mean=_refine_plateau_width_mean,
        refine_plateau_width_min=_refine_plateau_width_min,
        refine_grid_size_avg=_refine_grid_size_avg,
        refine_best_changed_count=_refine_best_changed_count,
    )

    # Ref-pass: OOS backtest for canonical top-1 params on every step (display-only)
    # Ref table (step.ref_*) reflects canonical top-1; fallback to consensus on ALL_FAILED/exception
    ref_atr, ref_mult = _resolve_canonical_ref_params(wf_result, selected, config)
    wf_result.canonical_atr_period = ref_atr
    wf_result.canonical_multiplier = ref_mult
    _compute_ref_oos_for_selected(
        steps=steps,
        open_prices=open_prices,
        high=high,
        low=low,
        close=close,
        index=index,
        selected_atr_period=ref_atr,
        selected_multiplier=ref_mult,
        config=config,
    )

    # Evaluate gates if enabled
    gates_result = evaluate_walk_forward_gates(wf_result, config)
    wf_result.gates_result = gates_result

    if skipped_step_count > 0:
        logger.warning(
            f"WF finished with {skipped_step_count}/{total_steps} skipped steps ({skipped_step_ratio:.1%}). "
            "This may indicate overly strict train constraints (early-exit/min_trades/warmup)."
        )

    return wf_result
