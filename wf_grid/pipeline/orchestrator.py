"""
Pipeline orchestrator — runs A1–A12 as a single sequential pipeline (§9).

run_grid_pipeline(config_path) -> PipelineResult:
  1.  Config load + validation             (A1)
  2.  Data load                            (A1)
  3.  periods_per_year resolution          (A1)
  4.  Warmup resolution                    (A4)
  5.  WF slicing                           (A4)
  6.  Grid enumeration                     (A2)
  7.  WF execution (OOS per grid point)    (A4)
  8a. Step gates                           (A8)
  8b. Step + trades collection             (A5/A6)
  9.  Aggregation                          (A7)
  10. Candidate gates                      (A8)
  11. Scoring                              (A9)
  12. Ranking                              (A10)
  13. Summary build                        (A11)
  14. Pre-export validation                (A12)
  15. XLSX export                          (A12)
  16. Logging / diagnostics                (A13)

Error handling: per-grid-point execution failures are captured as
runtime_error StepResults — they do NOT crash the pipeline.
"""

from __future__ import annotations

import copy
import logging
import multiprocessing
import os
import pickle
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from wf_grid.aggregate.aggregator import aggregate_candidates
from wf_grid.collect.metric_contracts import validate_metric_contracts
from wf_grid.collect.step_collector import collect_oos_steps, collect_train_steps
from wf_grid.collect.trades_collector import collect_oos_trades, collect_train_trades
from wf_grid.config.loader import load_grid_config, resolve_periods_per_year, ConfigError
from wf_grid.config.schema import GridConfig
from wf_grid.export.summary_builder import build_summary_wide
from wf_grid.export.xlsx_writer import export_workbook
from wf_grid.gates.gates import apply_candidate_gates, apply_step_gates
from wf_grid.grid.enumeration import GridPoint, enumerate_grid
from wf_grid.logging_.diagnostics import (
    PipelineDiagnostics,
    StageTimer,
    build_diagnostics,
    log_diagnostics,
)
from wf_grid.ranking.ranker import rank_candidates
from wf_grid.ranking.scoring import calculate_seed_score, compute_score_discrimination
from wf_grid.wf.runner import run_wf_for_grid_point, run_wf_train_for_grid_point, compute_prepend_bars
from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
from wf_grid.wf.step_executor import StepResult

from supertrend_optimizer.utils.time_utils import make_walk_forward_slices
from supertrend_optimizer.data.validator import validate_ohlc_data

logger = logging.getLogger(__name__)

# WP-PAR: project-root anchors for Windows spawn import safety (plan §3.1)
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_DONOR_ROOT: Path = _PROJECT_ROOT / "donor"

# WP-PAR: Narrow TypeError subclass raised only for pickle/submit/initargs
# failures (infrastructure).  This separates infra TypeErrors from plain
# TypeError raised by worker task code, which must NOT trigger fallback.
class _InfraTypeError(TypeError):
    """TypeError originating from ProcessPoolExecutor setup or serialisation."""


# WP-PAR: classes considered "infrastructure failures" — these trigger fallback
# when fallback_to_sequential=True (plan §3.6 / §4).  Plain task exceptions
# (RuntimeError / TypeError raised inside worker code) are deliberately
# excluded: they propagate into result.error without engaging fallback.
_PARALLEL_INFRA_FAILURES: tuple[type, ...] = (
    BrokenProcessPool,
    pickle.PicklingError,
    _InfraTypeError,
)


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Container for all pipeline outputs.

    step_oos_long / step_train_long cover ALL grid points (full grid metrics).
    trades_oos / trades_train contain trades for rank-1 grid point ONLY
    (export filter — trade-level data for the full grid is computed internally
    but not persisted in PipelineResult to keep export volume manageable).
    bucket_matrix_median: median-based bucket matrix (built from pre-gate data).
    """
    config: Optional[GridConfig] = None
    grid_points: List[GridPoint] = field(default_factory=list)
    step_oos_long: Optional[pd.DataFrame] = None
    step_train_long: Optional[pd.DataFrame] = None
    trades_oos: Optional[pd.DataFrame] = None
    trades_train: Optional[pd.DataFrame] = None
    aggregated: Optional[pd.DataFrame] = None
    ranked: Optional[pd.DataFrame] = None
    summary_wide: Optional[pd.DataFrame] = None
    bucket_matrix_median: Optional[pd.DataFrame] = None
    bucket_matrix_status: str = "disabled"
    bucket_matrix_error: Optional[str] = None
    output_path: Optional[Path] = None
    diagnostics: Optional[PipelineDiagnostics] = None
    error: Optional[str] = None
    # WP-PAR: runtime execution mode marker (plan §3.2).
    # Allowed values: "sequential", "parallel", "parallel_then_fallback".
    # Excluded from baseline fingerprint (plan §3.9).
    execution_mode: str = "sequential"


# ---------------------------------------------------------------------------
# Override validation helper
# ---------------------------------------------------------------------------

def _validate_execution_overrides(
    parallel_enabled: Optional[bool],
    max_workers: Optional[int],
) -> None:
    """Validate API/CLI execution overrides before applying them to config.

    Raises ValueError with a descriptive message for any invalid value.
    Called after load_grid_config so YAML-level validation is already done;
    this only covers the kwargs that bypass the YAML schema.
    """
    if parallel_enabled is not None:
        if not isinstance(parallel_enabled, bool):
            raise ValueError(
                f"parallel_enabled override must be bool, "
                f"got {type(parallel_enabled).__name__!r}: {parallel_enabled!r}"
            )
    if max_workers is not None:
        # Explicitly reject bool: isinstance(True, int) is True in Python.
        if isinstance(max_workers, bool):
            raise ValueError(
                f"max_workers override must be int >= 1, "
                f"got bool: {max_workers!r}"
            )
        if not isinstance(max_workers, int):
            raise ValueError(
                f"max_workers override must be int >= 1, "
                f"got {type(max_workers).__name__!r}: {max_workers!r}"
            )
        if max_workers < 1:
            raise ValueError(
                f"max_workers override must be >= 1, got {max_workers!r}"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_grid_pipeline(
    config_path: str,
    data_path: Optional[str] = None,
    output_path: Optional[str] = None,
    parallel_enabled: Optional[bool] = None,
    max_workers: Optional[int] = None,
) -> PipelineResult:
    """
    Execute the full grid search pipeline.

    Parameters
    ----------
    config_path:
        Path to the YAML configuration file.
    data_path:
        Override for config.data.file_path.  If None, uses config value.
    output_path:
        Override for XLSX output location.  If None, auto-generates next to data.
    parallel_enabled:
        When not None, overrides config.execution.parallel_enabled for this
        call only.  _original_config is never mutated (plan §5.1 / §6.14).
        Precedence: explicit kwarg > config.yaml > dataclass default.
    max_workers:
        When not None, overrides config.execution.max_workers for this call
        only.  Same non-mutation guarantee applies.

    Returns
    -------
    PipelineResult
        Contains all intermediate and final artefacts.
    """
    result = PipelineResult()
    timer = StageTimer()

    try:
        # --- 1. Config load + validation ---
        t0 = time.time()
        _original_config = load_grid_config(config_path)
        config = copy.deepcopy(_original_config)
        if data_path:
            config.data.file_path = data_path
        # Validate overrides before applying: catches bad API/CLI values that
        # bypass YAML schema validation (e.g. max_workers=0, max_workers=True).
        _validate_execution_overrides(parallel_enabled, max_workers)
        # Apply execution overrides after deepcopy so _original_config is
        # never mutated (plan §5.1 / §6.14 / acceptance criterion A6).
        if parallel_enabled is not None:
            config.execution.parallel_enabled = parallel_enabled
        if max_workers is not None:
            config.execution.max_workers = max_workers
        result.config = config
        timer.record("config_load", time.time() - t0)

        # --- 2. Data load ---
        t0 = time.time()
        data = pd.read_csv(config.data.file_path, parse_dates=True, index_col=0)
        timer.record("data_load", time.time() - t0)

        # --- 2b. Data validation ---
        t0 = time.time()
        data = validate_ohlc_data(data, strict=True)
        timer.record("data_validation", time.time() - t0)

        # --- 3. periods_per_year resolution ---
        t0 = time.time()
        config = resolve_periods_per_year(config, data)
        result.config = config
        timer.record("ppy_resolve", time.time() - t0)

        # --- 4. Warmup resolution ---
        t0 = time.time()
        config_dict = _config_to_raw_dict(config)
        prepend_bars = compute_prepend_bars(data, config_dict)
        timer.record("warmup_resolve", time.time() - t0)

        # --- 4b. ZigZag global stats (WP8) ---
        # Computed once on the full dataset before any WF execution.
        # Fails immediately (ConfigError) if the filter is enabled but
        # global stats cannot be materialized — init-failure before WF.
        # When the filter is absent or disabled, zigzag_global_stats stays None
        # and the disabled baseline path remains bit-identical.
        t0 = time.time()
        zigzag_global_stats = None
        _tf = config.trade_filter
        if _tf is not None and bool(getattr(_tf, "enabled", False)):
            full_close = data["close"].values
            zigzag_global_stats = build_zigzag_global_stats(
                close=full_close,
                trade_filter_config=_tf,
            )
            logger.info(
                "ZigZag global stats built: global_median=%.6f, "
                "candidate_trigger_threshold=%.6f, n_legs=%d",
                float(zigzag_global_stats.global_median),
                float(zigzag_global_stats.candidate_trigger_threshold),
                int(zigzag_global_stats.n_legs_total),
            )
        timer.record("zigzag_global_stats", time.time() - t0)

        # --- 5. WF slicing ---
        t0 = time.time()
        wf_slices = make_walk_forward_slices(
            index=data.index,
            train_size=config.validation.walk_forward.train_size,
            test_size=config.validation.walk_forward.test_size,
            step_size=config.validation.walk_forward.step_size or config.validation.walk_forward.test_size,
            scheme=config.validation.walk_forward.scheme,
            anchor=config.validation.walk_forward.anchor,
            min_train_bars=config.validation.walk_forward.min_train_bars,
            min_test_bars=config.validation.walk_forward.min_test_bars,
        )
        if not wf_slices:
            raise ValueError(
                "No walk-forward slices generated for the current configuration. "
                "Check train_size, test_size, min_train_bars, min_test_bars and data length."
            )
        if config.validation.walk_forward.anchor == "end":
            unused_bars = wf_slices[0].train_start_idx
            if unused_bars > 0:
                logger.info(
                    "anchor='end': first train starts at bar %d, %d bars unused at start of data",
                    unused_bars, unused_bars,
                )
        n_wf_steps = len(wf_slices)
        timer.record("wf_slicing", time.time() - t0)

        # --- 6. Grid enumeration ---
        t0 = time.time()
        grid_points = enumerate_grid(config)
        result.grid_points = grid_points
        timer.record("grid_enum", time.time() - t0)

        logger.info(
            "Pipeline: %d grid points × %d WF steps = %d total evaluations",
            len(grid_points), n_wf_steps, len(grid_points) * n_wf_steps,
        )

        # --- 7. WF execution (OOS + Train) ---
        if config.resolved_periods_per_year is None:
            raise ConfigError(
                "resolved_periods_per_year must be set before WF execution — "
                "ensure resolve_periods_per_year() was called"
            )
        t0 = time.time()
        all_oos_results, all_train_results = _execute_wf_grid(
            grid_points=grid_points,
            wf_slices=wf_slices,
            data=data,
            config=config,
            prepend_bars=prepend_bars,
            zigzag_global_stats=zigzag_global_stats,
            _result=result,
        )
        timer.record("wf_grid_execution", time.time() - t0)

        # --- 8. Step collection (OOS + Train metrics) ---
        t0 = time.time()
        step_oos_long = collect_oos_steps(
            all_oos_results, config, expected_n_steps=n_wf_steps,
            grid_points=grid_points,
        )
        step_train_long = collect_train_steps(
            all_train_results, config, expected_n_steps=n_wf_steps,
            grid_points=grid_points,
        )
        result.step_train_long = step_train_long
        timer.record("collection", time.time() - t0)

        # --- 8.1 Metric contracts (M1/M2/M3) ---
        # Fail-closed validation: ok-steps with broken hard metrics
        # (missing / NaN / inf / INVALID_METRIC_VALUE) or positive max_drawdown
        # are re-tagged step_status="invalid" with error_type="metric_contract"
        # BEFORE the bucket matrix, step gates, and aggregation consume the data.
        t0 = time.time()
        step_oos_long = validate_metric_contracts(step_oos_long)
        timer.record("metric_contracts", time.time() - t0)

        # --- 8-bucket. BucketMatrix_Median (pre-gate, isolated) ---
        # Builder operates on pre-gate step_oos_long: before apply_step_gates,
        # the gate_failed status does not exist yet — ok-filter sees only
        # genuine statuses (ok, no_trades, insufficient_bars, runtime_error,
        # invalid).  This matches donor semantics where top_entries were never gated.
        step_oos_long_pre_gate = step_oos_long
        try:
            from wf_grid.bucket.median_matrix_builder import build_median_bucket_matrix
            t0 = time.time()
            result.bucket_matrix_median = build_median_bucket_matrix(
                step_oos_long_pre_gate, config,
            )
            timer.record("bucket_matrix_median", time.time() - t0)
            result.bucket_matrix_status = "ok"
            result.bucket_matrix_error = None
        except Exception as exc:
            logger.warning(
                "Bucket matrix build failed, continuing pipeline: %s", exc,
            )
            result.bucket_matrix_median = None
            result.bucket_matrix_status = "failed"
            result.bucket_matrix_error = str(exc)

        # --- 8a. Step gates (applied after collection) ---
        t0 = time.time()
        step_oos_long = apply_step_gates(step_oos_long_pre_gate, config)
        result.step_oos_long = step_oos_long
        timer.record("step_gates", time.time() - t0)

        # --- 9. Aggregation ---
        t0 = time.time()
        aggregated = aggregate_candidates(step_oos_long, config)
        result.aggregated = aggregated
        timer.record("aggregation", time.time() - t0)

        # --- 10. Candidate gates ---
        t0 = time.time()
        gated = apply_candidate_gates(aggregated, config)
        timer.record("candidate_gates", time.time() - t0)

        # --- 11. Scoring ---
        t0 = time.time()
        passed_mask = gated["seed_gate_passed"].fillna(False).astype(bool)
        scores, statuses = calculate_seed_score(
            gated,
            passed_mask,
            score_weights=config.scoring.score_weights,
            normalization_mode=config.scoring.normalization_mode,
        )
        gated["tester_seed_score"] = scores
        gated["score_contract_status"] = statuses
        gated["score_discrimination_status"] = compute_score_discrimination(
            gated,
            passed_mask,
            min_passed=config.scoring.min_passed_for_discrimination,
            low_spread_threshold=config.scoring.low_spread_threshold,
        )
        timer.record("scoring", time.time() - t0)

        # --- 12. Ranking ---
        t0 = time.time()
        ranked = rank_candidates(gated, config)
        result.ranked = ranked
        timer.record("ranking", time.time() - t0)

        # --- 12b. Collect trades for rank-1 only (export filter) ---
        # Trades are already computed for all grid points during execution
        # (steps 7/7b). Here we filter to rank-1 only for XLSX export.
        t0 = time.time()
        trades_oos, trades_train = _collect_top1_trades(
            ranked, all_oos_results, all_train_results, config,
        )
        result.trades_oos = trades_oos
        result.trades_train = trades_train
        timer.record("top1_trade_export_filter", time.time() - t0)

        # --- 13. Summary build ---
        t0 = time.time()
        summary_wide = build_summary_wide(
            step_oos_long,
            aggregated,
            ranked,
            config,
            bucket_matrix_status=result.bucket_matrix_status,
            bucket_matrix_error=result.bucket_matrix_error,
        )
        result.summary_wide = summary_wide
        timer.record("summary_build", time.time() - t0)

        # --- 14–15. Pre-export validation + XLSX export ---
        t0 = time.time()
        out_path = _resolve_output_path(output_path, config)
        xlsx_path = export_workbook(
            summary_wide=summary_wide,
            step_oos_long=step_oos_long,
            trades_oos=trades_oos,
            trades_train=trades_train,
            config=config,
            output_path=out_path,
            bucket_matrix_median=result.bucket_matrix_median,
        )
        result.output_path = xlsx_path
        timer.record("xlsx_export", time.time() - t0)

        # --- 16. Diagnostics ---
        diag = build_diagnostics(
            grid_size=len(grid_points),
            n_wf_steps=n_wf_steps,
            step_oos_long=step_oos_long,
            ranked=ranked,
            timer=timer,
        )
        result.diagnostics = diag
        log_diagnostics(diag)

    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        result.error = str(exc)

    return result


# ---------------------------------------------------------------------------
# WF grid execution (sequential; parallel branch added in Step 5)
# ---------------------------------------------------------------------------

def _resolve_max_workers(config, grid_points) -> int:
    """Resolve effective worker count (plan §3.7).

    Precedence: configured value > os.cpu_count() (fallback 1).
    Capped to len(grid_points) and 32, never below 1.
    Empty grid_points always yields 1 (sequential short-circuit).
    """
    configured = config.execution.max_workers
    cpu = configured if configured is not None else (os.cpu_count() or 1)
    if len(grid_points) == 0:
        return 1
    return max(1, min(cpu, len(grid_points), 32))


def _execute_wf_grid(
    grid_points,
    wf_slices,
    data,
    config,
    prepend_bars,
    zigzag_global_stats,
    *,
    _result: "PipelineResult",
):
    """Dispatch WF execution; sets execution_mode on result (plan §3.4).

    Branch selection:
      - parallel_enabled=False, n_workers==1, or empty grid -> sequential.
      - otherwise: enter parallel branch and mark execution_mode="parallel"
        BEFORE submitting work (C3-1 enter-then-mark).

    On infrastructure failure (plan §3.6 / §4):
      - fallback_to_sequential=False -> re-raise (caught by outer pipeline).
      - fallback_to_sequential=True  -> rerun the whole stage sequentially,
        then mark execution_mode="parallel_then_fallback".

    Returns (all_oos_results, all_train_results) dicts keyed by grid_point_id.
    """
    n_workers = _resolve_max_workers(config, grid_points)

    if (
        not config.execution.parallel_enabled
        or n_workers == 1
        or len(grid_points) == 0
    ):
        _result.execution_mode = "sequential"
        return _execute_wf_grid_sequential(
            grid_points, wf_slices, data, config,
            prepend_bars, zigzag_global_stats,
        )

    # C3-1 enter-then-mark: once this line runs, a later failure must still
    # report that the parallel branch was attempted.  fallback success may
    # later upgrade execution_mode to "parallel_then_fallback".
    _result.execution_mode = "parallel"

    try:
        return _execute_wf_grid_parallel(
            grid_points, wf_slices, data, config,
            prepend_bars, zigzag_global_stats, n_workers,
        )
    except _PARALLEL_INFRA_FAILURES as exc:
        if not config.execution.fallback_to_sequential:
            raise
        logger.warning(
            "Parallel execution failed (%s: %s); falling back to "
            "sequential mode for the entire wf_grid_execution stage.",
            type(exc).__name__,
            exc,
        )
        out = _execute_wf_grid_sequential(
            grid_points, wf_slices, data, config,
            prepend_bars, zigzag_global_stats,
        )
        _result.execution_mode = "parallel_then_fallback"
        return out


def _execute_wf_grid_parallel(
    grid_points,
    wf_slices,
    data,
    config,
    prepend_bars,
    zigzag_global_stats,
    n_workers,
):
    """Parallel OOS+Train execution via ProcessPoolExecutor (plan §3.8).

    Windows spawn is the only multiprocessing context used.  Worker
    initialisation packs OHLC into picklable arrays once per worker; tasks
    only ship a GridPoint.

    Completion order does NOT affect outputs because downstream collectors
    sort by (grid_point_id, wf_step) before returning.

    Raises:
        BrokenProcessPool / pickle.PicklingError / TypeError on
        infrastructure failure (caller decides fallback policy).
        Plain task exceptions are re-raised verbatim by future.result().
        RuntimeError if the result set is incomplete (defensive guard).
    """
    from wf_grid.wf._mp_helpers import (
        pack_data,
        _init_worker,
        _run_grid_point_both,
    )

    ctx = multiprocessing.get_context("spawn")
    data_dict = pack_data(data)
    project_root = str(_PROJECT_ROOT)
    donor_path = str(_DONOR_ROOT)

    all_oos: Dict[str, List[StepResult]] = {}
    all_train: Dict[str, List[StepResult]] = {}

    try:
        executor = ProcessPoolExecutor(
            max_workers=n_workers,
            mp_context=ctx,
            initializer=_init_worker,
            initargs=(
                project_root,
                donor_path,
                data_dict,
                wf_slices,
                config,
                prepend_bars,
                zigzag_global_stats,
            ),
        )
    except TypeError as exc:
        raise _InfraTypeError(str(exc)) from exc

    with executor as ex:
        try:
            futures = {
                ex.submit(_run_grid_point_both, gp): gp.grid_point_id
                for gp in grid_points
            }
        except TypeError as exc:
            raise _InfraTypeError(str(exc)) from exc
        done = 0
        for fut in as_completed(futures):
            gp_id = futures[fut]
            rid, oos_results, train_results = fut.result()
            if rid != gp_id:
                raise RuntimeError(
                    f"Worker returned grid_point_id {rid!r}, "
                    f"expected {gp_id!r}"
                )
            all_oos[gp_id] = oos_results
            all_train[gp_id] = train_results
            done += 1
            if done % max(1, len(grid_points) // 10) == 0:
                logger.info(
                    "  Parallel WF: %d/%d grid points done",
                    done, len(grid_points),
                )

    expected_ids = {gp.grid_point_id for gp in grid_points}
    missing_oos = expected_ids - set(all_oos)
    missing_train = expected_ids - set(all_train)
    if missing_oos or missing_train:
        raise RuntimeError(
            f"Parallel WF execution incomplete: "
            f"missing OOS {sorted(missing_oos)}, "
            f"missing Train {sorted(missing_train)}"
        )

    return all_oos, all_train


def _execute_wf_grid_sequential(
    grid_points,
    wf_slices,
    data,
    config,
    prepend_bars,
    zigzag_global_stats,
):
    """Sequential OOS + Train WF execution over all grid points.

    Returns (all_oos_results, all_train_results) dicts keyed by grid_point_id.
    """
    from wf_grid.wf._mp_helpers import _strip_filter_diagnostics_arrays

    all_oos: Dict[str, List[StepResult]] = {}
    for i, gp in enumerate(grid_points):
        if (i + 1) % max(1, len(grid_points) // 10) == 0:
            logger.info(
                "  Executing grid point %d/%d (%s)",
                i + 1, len(grid_points), gp.grid_point_id,
            )
        step_results = run_wf_for_grid_point(
            grid_point=gp,
            wf_slices=wf_slices,
            full_data=data,
            config=config,
            prepend_bars_requested=prepend_bars,
            zigzag_global_stats=zigzag_global_stats,
        )
        # Drop per-bar filter_diagnostics_oos: only the small derived
        # filter_diagnostics_summary is consumed downstream (DEBUG-A1813C).
        _strip_filter_diagnostics_arrays(step_results)
        all_oos[gp.grid_point_id] = step_results

    all_train: Dict[str, List[StepResult]] = {}
    for gp in grid_points:
        train_results = run_wf_train_for_grid_point(
            grid_point=gp,
            wf_slices=wf_slices,
            full_data=data,
            config=config,
            zigzag_global_stats=zigzag_global_stats,
        )
        _strip_filter_diagnostics_arrays(train_results)
        all_train[gp.grid_point_id] = train_results

    return all_oos, all_train


# ---------------------------------------------------------------------------
# Top-1 trade export filter
# ---------------------------------------------------------------------------

def _collect_top1_trades(
    ranked: pd.DataFrame,
    all_oos_results: Dict[str, List[StepResult]],
    all_train_results: Dict[str, List[StepResult]],
    config: GridConfig,
) -> tuple:
    """Filter already-computed results to collect trades for rank-1 only.

    Trades are computed for all grid points during OOS/train execution
    (needed for trade-based metrics like sum_pnl_pct).  This function
    filters the already-available results to export only the rank-1
    point's trades into WF_Trades / WF_Train_Trades sheets.

    No re-execution or replay occurs here.

    Returns (trades_oos, trades_train) DataFrames.  If no rank-1 exists
    (e.g. all candidates failed), returns empty DataFrames.
    """
    rank1 = ranked.loc[ranked["grid_rank"] == 1, "grid_point_id"]
    if rank1.empty:
        logger.warning("No rank-1 candidate found — trades export will be empty")
        return pd.DataFrame(), pd.DataFrame()

    top_gp_id = rank1.iloc[0]

    top_oos = {top_gp_id: all_oos_results[top_gp_id]} if top_gp_id in all_oos_results else {}
    top_train = {top_gp_id: all_train_results[top_gp_id]} if top_gp_id in all_train_results else {}

    trades_oos = collect_oos_trades(top_oos, config)
    trades_train = collect_train_trades(top_train, config)

    return trades_oos, trades_train


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config_to_raw_dict(config: GridConfig) -> dict:
    """Convert to dict suitable for donor calculate_warmup."""
    import dataclasses
    def _to_dict(obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return {f.name: _to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
        if isinstance(obj, list):
            return [_to_dict(i) for i in obj]
        if isinstance(obj, dict):
            return {k: _to_dict(v) for k, v in obj.items()}
        return obj
    return _to_dict(config)


def _resolve_output_path(output_path: Optional[str], config: GridConfig) -> Path:
    """Resolve output XLSX path from explicit or config-derived default.

    If output_path is explicit — used as-is (no timestamp injected).
    If auto-generated — timestamp suffix added: _wf_grid_results_YYYYMMDD_HHMMSS.xlsx
    """
    if output_path:
        return Path(output_path)
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_path = Path(config.data.file_path)
    return data_path.parent / f"{data_path.stem}_wf_grid_results_{ts}.xlsx"
