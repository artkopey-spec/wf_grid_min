"""
GridConfig schema — dataclasses with defaults and validation rules.

All fields per plan §1.1.  Validation is performed in loader.py.

WP2 adds trade_filter dataclasses per plan §6.3 and Appendix A v1.1 §11.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Union

from wf_grid.status.status_model import RankingMode


# ---------------------------------------------------------------------------
# Metric constants — SINGLE SOURCE OF TRUTH is donor.
# Do NOT redefine INVALID_METRIC_VALUE / MAX_VALID_METRIC as local literals:
# they must stay bit-identical with supertrend_optimizer.utils.constants.
# Drift is guarded by test_invalid_metric_value_drift in test_config_schema.py.
# ---------------------------------------------------------------------------
from supertrend_optimizer.utils.constants import (
    INVALID_METRIC_VALUE,
    MAX_VALID_METRIC,
)

__all__ = [
    "INVALID_METRIC_VALUE",
    "MAX_VALID_METRIC",
    "ExecutionConfig",
]


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------

@dataclass
class DataConfig:
    file_path: str
    periods_per_year: Union[str, float] = "auto"
    # Optional: "calendar" | "trading".  None = donor default (CALENDAR when market=None).
    annualization_basis: Optional[str] = None


@dataclass
class OptimizationConfig:
    atr_period_range: list[int] = field(default_factory=lambda: [5, 55])
    multiplier_range: list[float] = field(default_factory=lambda: [1.5, 5.5])
    multiplier_step: float = 0.1
    atr_period_step: int = 1
    # Valid trade modes: "both", "revers", "long", "short".
    # "revers" and "both" are aliases — both enable long+short with position reversal.
    # The execution engine (core/backtest.py) accepts all four strings as-is;
    # no normalisation is applied here.  grid_point_id will encode whichever
    # string the user configured, so "both" and "revers" produce distinct IDs.
    trade_mode: str = "both"


@dataclass
class BacktestConfig:
    commission: float = 0.000235
    min_trades_required: int = 3
    # early_exit is disabled by default: enabling it on OOS steps truncates arrays
    # to exit_bar while test_start_idx/test_end_idx still reference the full window,
    # causing OOS metrics to reflect a shorter period than declared (horizon distortion).
    early_exit_enabled: bool = False
    early_exit_max_drawdown: float = 0.50
    early_exit_check_bars: int = 50


@dataclass
class WalkForwardConfig:
    train_size: str = ""
    test_size: str = ""
    step_size: Optional[str] = None   # default = test_size
    scheme: str = "rolling"
    anchor: str = "start"
    min_train_bars: int = 500
    min_test_bars: int = 100


@dataclass
class ValidationConfig:
    warmup_period: int = 0
    warmup_period_auto: bool = False
    walk_forward: WalkForwardConfig = field(default_factory=WalkForwardConfig)


@dataclass
class StepGatesConfig:
    min_trades: Optional[int] = None          # default = min_trades_required
    max_drawdown_threshold: float = -0.50


@dataclass
class CandidateGatesConfig:
    positive_median_threshold: float = 0.0
    min_trades_median: float = 3.0
    worst_segment_pnl_threshold: Optional[float] = None   # None = disabled
    max_drawdown_threshold: float = -0.50
    min_total_trades: int = 30
    min_ok_ratio: float = 0.7


@dataclass
class GatesConfig:
    step: StepGatesConfig = field(default_factory=StepGatesConfig)
    candidate: CandidateGatesConfig = field(default_factory=CandidateGatesConfig)


@dataclass
class RankingConfig:
    mode: str = "legacy"
    min_segments_for_ranking: Optional[int] = None   # default computed at runtime
    sort_by: str = "sum_pnl_pct_Median"
    tiebreaker: str = "sum_pnl_pct_Min"


@dataclass
class ScoringConfig:
    score_weights: dict[str, float] = field(default_factory=lambda: {
        "sum_pnl_pct_Median": 0.45,
        "profitable_segments_count": 0.35,
        "abs_max_drawdown_Min": 0.20,
    })
    # "minmax" (legacy, default) or "rank" (outlier-robust).
    # Default is "minmax" to preserve existing behaviour; switch to "rank"
    # only after side-by-side validation on real data.
    normalization_mode: str = "minmax"
    # Minimum number of passed candidates required for score discrimination
    # to be considered meaningful.  Below this threshold,
    # score_discrimination_status = "insufficient".
    min_passed_for_discrimination: int = 5
    # Minimum raw feature range (sum_pnl_pct_Median) required to consider
    # score discrimination meaningful.  Below this threshold,
    # score_discrimination_status = "low_spread".
    low_spread_threshold: float = 1.0


@dataclass
class StatusConfig:
    min_meaningful_bars: int = 30


@dataclass
class BucketConfig:
    atr_bucket_step: int = 2
    mult_bucket_step: float = 0.2
    min_buckets_for_median: int = 5


@dataclass
class ExecutionConfig:
    """Parallelization controls.

    DESIGN NOTE - dataclass / yaml asymmetry:
    Python-dataclass default parallel_enabled=False so direct
    programmatic callers and tests preserve sequential behaviour.
    config.yaml provides the production default of True. Override
    per run via:
        run_grid_pipeline(parallel_enabled=...)
        run.py --sequential / --max-workers N
    """

    parallel_enabled: bool = False
    max_workers: Optional[int] = None
    # Reserved for future batching. Validated, currently ignored.
    chunksize: Optional[int] = None
    fallback_to_sequential: bool = False


# ---------------------------------------------------------------------------
# Trade filter dataclasses — re-exported from active donor (WP-T2 step 0b shim)
#
# OWNER DECISION v0.5.1 §15 #1 (Phase 2 plan): TradeFilter* dataclasses live in
# `donor/supertrend_optimizer/core/trade_filter_config.py` (single source of
# truth for both WF Grid Phase 1 and Tester Phase 2). This module re-exports
# them so `from wf_grid.config.schema import TradeFilterConfig, ...` keeps
# working unchanged for Phase 1 callers.
#
# Spec reference: Appendix A v1.1 §11, §11.1-§11.3, §15.6, §17.2
# Plan reference: Phase 2 plan §5.1, §14 WP-T2 step 0b
# ---------------------------------------------------------------------------

from supertrend_optimizer.core.trade_filter_config import (  # noqa: E402,F401
    TradeFilterConfig,
    TradeFilterZigZagConfig,
    TradeFilterCandidateDurationGateConfig,
    TradeFilterTriggersConfig,
    TradeFilterTriggerToggleConfig,
    TradeFilterLifecycleConfig,
    TradeFilterDiagnosticsConfig,
    TradeFilterTimeFilterConfig,
    resolve_zigzag_mode,
)


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

@dataclass
class GridConfig:
    data: DataConfig = field(default_factory=DataConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    gates: GatesConfig = field(default_factory=GatesConfig)
    ranking: RankingConfig = field(default_factory=RankingConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    status: StatusConfig = field(default_factory=StatusConfig)
    bucket: BucketConfig = field(default_factory=BucketConfig)
    # trade_filter is None when the block is absent from YAML (§11.1: filter disabled).
    # WP2 adds schema; runtime use begins in WP5+.
    # Spec reference: Appendix A v1.1 §11, §17.1; plan §6.3
    trade_filter: Optional[TradeFilterConfig] = None

    # WP-PAR: parallelization controls (plan §1.2)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    # Resolved at load time (not from YAML directly)
    resolved_periods_per_year: Optional[float] = None

    def effective_step_min_trades(self) -> int:
        """Return effective step-gate min_trades (falls back to backtest setting)."""
        v = self.gates.step.min_trades
        return v if v is not None else self.backtest.min_trades_required
