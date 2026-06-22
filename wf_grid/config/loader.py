"""
Config loader for WF Grid Search.

Loads YAML -> GridConfig dataclass, validates all fields,
resolves periods_per_year (auto or explicit).

periods_per_year resolution delegates to donor
``resolve_periods_per_year_from_config`` (supertrend_optimizer.data.timeframe)
to avoid divergence.  The default annualization_basis is CALENDAR (same as
donor default when market=None), which is correct for crypto / FX 24/7 data.
For stock/futures data set ``data.annualization_basis: trading`` in config.
"""

from __future__ import annotations

import copy
import math
import sys
import warnings
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Donor imports вЂ” annualization
# ---------------------------------------------------------------------------
# The donor package (supertrend_optimizer) must be on sys.path.
# In development/test the project root and donor/ are added via pytest.ini
# pythonpath.  In production install them as a package or add donor/ to PYTHONPATH.
try:
    from supertrend_optimizer.data.timeframe import resolve_periods_per_year_from_config
except ModuleNotFoundError:
    # Fallback: attempt to add donor/ to path at runtime (single-file execution).
    _DONOR_ROOT = str(Path(__file__).resolve().parents[3] / "donor")
    if _DONOR_ROOT not in sys.path:
        sys.path.insert(0, _DONOR_ROOT)
    from supertrend_optimizer.data.timeframe import resolve_periods_per_year_from_config

import logging

from wf_grid.config.schema import (
    GridConfig,
    DataConfig,
    OptimizationConfig,
    BacktestConfig,
    WalkForwardConfig,
    ValidationConfig,
    StepGatesConfig,
    CandidateGatesConfig,
    GatesConfig,
    RankingConfig,
    ScoringConfig,
    StatusConfig,
    BucketConfig,
    ExecutionConfig,
    ExportConfig,
    TradeFilterConfig,
    TradeFilterZigZagConfig,
    TradeFilterTriggersConfig,
    TradeFilterTriggerToggleConfig,
    TradeFilterLifecycleConfig,
    TradeFilterDiagnosticsConfig,
)

_logger = logging.getLogger(__name__)
from wf_grid.status.status_model import RankingMode

# Valid trade modes (same as donor constants.TRADE_MODES)
_TRADE_MODES = ("both", "revers", "long", "short")

# Valid WF schemes
_WF_SCHEMES = ("rolling", "expanding")

# Valid WF anchor values
_WF_ANCHORS = ("start", "end")

# Supported schema_version values.  Must be extended when a new schema version is added.
_SUPPORTED_SCHEMA_VERSIONS = {1}

# ---------------------------------------------------------------------------
# Strict schema: allowed keys per YAML path
# Paths use dot-notation; "__top__" means the root mapping.
# A sub-dict is recursively checked only when its path appears here.
# Free-form dicts (e.g. scoring.score_weights) are intentionally absent вЂ”
# their keys are user-defined and not checked.
# ---------------------------------------------------------------------------
_ALLOWED_KEYS: dict[str, set[str]] = {
    "__top__": {
        "schema_version",
        "data",
        "optimization",
        "backtest",
        "validation",
        "gates",
        "ranking",
        "scoring",
        "status",
        "bucket",
        # WP2: trade_filter block (plan В§6.4; Appendix A v1.1 В§11)
        "trade_filter",
        # WP-PAR: parallelization controls (plan В§1.3)
        "execution",
        "export",
    },
    "data": {"file_path", "periods_per_year", "annualization_basis"},
    "optimization": {
        "atr_period_range",
        "multiplier_range",
        "multiplier_step",
        "atr_period_step",
        "trade_mode",
    },
    "backtest": {
        "commission",
        "min_trades_required",
        "early_exit_enabled",
        "early_exit_max_drawdown",
        "early_exit_check_bars",
    },
    "validation": {"warmup_period", "warmup_period_auto", "walk_forward"},
    "validation.walk_forward": {
        "train_size",
        "test_size",
        "step_size",
        "scheme",
        "anchor",
        "min_train_bars",
        "min_test_bars",
    },
    "gates": {"step", "candidate"},
    "gates.step": {"min_trades", "max_drawdown_threshold"},
    "gates.candidate": {
        "positive_median_threshold",
        "min_trades_median",
        "worst_segment_pnl_threshold",
        "max_drawdown_threshold",
        "min_ok_ratio",
        "min_total_trades",
    },
    "ranking": {
        "mode",
        "min_segments_for_ranking",
        "sort_by",
        "tiebreaker",
    },
    "scoring": {
        "score_weights",
        "normalization_mode",
        "min_passed_for_discrimination",
        "low_spread_threshold",
    },
    "status": {"min_meaningful_bars"},
    "bucket": {"atr_bucket_step", "mult_bucket_step", "min_buckets_for_median"},
    # WP-PAR: execution subtree (plan В§1.3)
    "execution": {
        "parallel_enabled",
        "max_workers",
        "chunksize",
        "fallback_to_sequential",
    },
    "export": {
        "retain_per_bar_filter_diagnostics",
    },
    # --- trade_filter subtree (plan В§6.4; Appendix A v1.1 В§11) ---
    "trade_filter": {
        "enabled",
        "type",
        "zigzag",
        "triggers",
        "lifecycle",
        "diagnostics",
        "time_filter",
        "volume",
        "wakeup_regime",
    },
    "trade_filter.time_filter": {
        "enabled",
        "window",
    },
    "trade_filter.zigzag": {
        "enabled",
        "global_stats_source",
        "leg_height_mode",
        "reversal_threshold",
        "candidate_trigger_threshold",
        "candidate_trigger_quantile",
        "global_median",
        "local_window",
        "daily_reset",
        # v3 fields (WP-V3-1)
        "mode",
        "candidate_duration_gate",
        # candidate_entry is whitelisted only to allow the validator to emit
        # a specific deprecated error (РўР— v3 В§4.5 candidate_entry_deprecated)
        "candidate_entry",
    },
    "trade_filter.zigzag.candidate_duration_gate": {
        "enabled",
        "max_bars",
    },
    "trade_filter.triggers": {
        "candidate_threshold",
        "confirmed_median",
    },
    "trade_filter.triggers.candidate_threshold": {"enabled"},
    "trade_filter.triggers.confirmed_median": {"enabled"},
    "trade_filter.lifecycle": {
        "freeze_confirmed_legs",
        "stop_check",
        "stopping_exit",
        "exit_off_mode",
        "exit_off_zz_leg_count",
        "exit_b_immediate_off",
    },
    "trade_filter.diagnostics": {
        "export_state_columns",
        "export_trigger_columns",
    },
    "trade_filter.volume": {
        "enabled",
        "mode",
        "aggregation",
        "daily_reset",
        "cycle_direction_gate",
        "short_window",
        "baseline_window",
        "threshold_ratio",
        "exit_hysteresis_ratio",
        "exit_freeze_bars",
        "regime_low_ratio",
        "regime_high_ratio",
        "direction_lookback_bars",
        "baseline_session",
    },
    "trade_filter.volume.baseline_session": {
        "enabled",
        "window",
    },
    "trade_filter.wakeup_regime": {
        "enabled",
        "lock_cycle_direction",
        "entry",
        "exit",
        "position_freeze",
    },
    "trade_filter.wakeup_regime.entry": {
        "candidate_height",
        "candidate_age",
        "atr_expansion",
        "volume_expansion",
        "direction_mode",
    },
    "trade_filter.wakeup_regime.entry.candidate_height": {
        "enabled",
        "quantile",
    },
    "trade_filter.wakeup_regime.entry.candidate_age": {
        "enabled",
        "max_bars",
    },
    "trade_filter.wakeup_regime.entry.atr_expansion": {
        "enabled",
        "short_window",
        "long_window",
        "min_ratio",
    },
    "trade_filter.wakeup_regime.entry.volume_expansion": {
        "enabled",
        "short_window",
        "baseline_window",
        "min_ratio",
    },
    "trade_filter.wakeup_regime.exit": {
        "ttl",
        "no_fresh_candidate",
        "max_trades_per_cycle",
        "cycle_take_profit",
        "local_median_stop",
        "action",
    },
    "trade_filter.wakeup_regime.exit.ttl": {
        "enabled",
        "bars",
    },
    "trade_filter.wakeup_regime.exit.no_fresh_candidate": {
        "enabled",
        "quantile",
        "max_age_bars",
        "timeout_bars",
    },
    "trade_filter.wakeup_regime.exit.max_trades_per_cycle": {
        "enabled",
        "max_trades",
    },
    "trade_filter.wakeup_regime.exit.cycle_take_profit": {
        "enabled",
        "pnl_pct",
    },
    "trade_filter.wakeup_regime.exit.local_median_stop": {
        "enabled",
    },
    "trade_filter.wakeup_regime.exit.action": {
        "mode",
    },
    "trade_filter.wakeup_regime.position_freeze": {
        "enabled",
        "min_hold_bars",
        "apply_to",
        "release_action",
    },
}


def _collect_unknown_keys(raw: dict, path: str = "__top__") -> list[str]:
    """Return list of error messages for every unknown key found recursively."""
    allowed = _ALLOWED_KEYS.get(path, set())
    errors: list[str] = []
    for key in raw:
        # Build user-visible dotted path for the error message
        display = key if path == "__top__" else f"{path}.{key}"
        if key not in allowed:
            errors.append(f"unknown config key: '{display}'")
        else:
            child = raw[key]
            child_path = key if path == "__top__" else f"{path}.{key}"
            if isinstance(child, dict) and child_path in _ALLOWED_KEYS:
                errors.extend(_collect_unknown_keys(child, child_path))
    return errors


def _validate_strict_schema(raw: dict) -> None:
    """Raise ConfigError if raw dict contains unknown keys or bad schema_version.

    Called in load_grid_config BEFORE _build_config so that any typo in a
    nested key is caught before silently defaulting to the dataclass default.
    """
    errors = _collect_unknown_keys(raw)

    sv = raw.get("schema_version")
    if sv is not None and sv not in _SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"schema_version {sv!r} is not supported; "
            f"supported versions: {sorted(_SUPPORTED_SCHEMA_VERSIONS)}"
        )

    if errors:
        raise ConfigError(
            "Config schema validation failed:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


class ConfigError(ValueError):
    """Raised when config is invalid."""


# _collect_raw_keys is a thin shim around the shared helper.
# Phase 1 plan В§6.4.1 / Phase 2 plan В§5.1 В§14 WP-T2 step 0b.
from supertrend_optimizer.core.trade_filter_config import (  # noqa: E402
    collect_raw_user_keys as _collect_raw_keys,
    collect_trade_filter_unknown_keys as _collect_trade_filter_unknown_keys,
)


def load_grid_config(path: str, ohlc_data: Optional[pd.DataFrame] = None) -> GridConfig:
    """
    Load and validate grid config from YAML file.

    Parameters
    ----------
    path:
        Path to the YAML config file.
    ohlc_data:
        Optional OHLC DataFrame used for auto periods_per_year resolution.
        Required when data.periods_per_year == "auto" and data has DatetimeIndex.
        If not provided, auto-detect is deferred until data is available.

    Returns
    -------
    GridConfig
        Validated and fully populated config.

    Raises
    ------
    ConfigError
        On any validation failure.
    FileNotFoundError
        If config file does not exist.
    """
    raw = _read_yaml(path)
    _validate_strict_schema(raw)
    _validate_trade_filter_unknown_keys(raw)
    # Collect raw key-paths BEFORE _build_config fills in dataclass defaults; this
    # preserves the distinction between "user supplied the key" and "absent / default".
    # Required by plan В§6.4.1 for the numeric-threshold + explicit-quantile rule.
    raw_user_keys = _collect_raw_keys(raw)
    cfg = _build_config(raw)

    _resolve_zigzag_enabled_in_place(cfg, raw_user_keys)
    _resolve_volume_enabled_in_place(cfg, raw_user_keys)

    _validate_config(cfg, raw_user_keys=raw_user_keys)

    # v3 WP-V3-2: resolve effective zigzag mode in-place after validation so
    # build_zigzag_global_stats can read zigzag.mode directly (always non-None).
    _resolve_trade_filter_mode_in_place(cfg, raw_user_keys)
    _resolve_exit_off_mode_in_place(cfg, raw_user_keys)
    _resolve_exit_b_immediate_off_in_place(cfg, raw_user_keys)
    _resolve_time_filter_in_place(cfg, raw_user_keys)
    _resolve_volume_baseline_session_in_place(cfg, raw_user_keys)
    _resolve_volume_defaults_in_place(cfg, raw_user_keys)

    if ohlc_data is not None:
        cfg = _resolve_periods_per_year(cfg, ohlc_data)

    return cfg


def _validate_trade_filter_unknown_keys(raw: dict[str, Any]) -> None:
    """Run the shared trade_filter unknown-key collector as an explicit phase."""
    tf_raw = raw.get("trade_filter")
    if tf_raw is None:
        return
    if not isinstance(tf_raw, dict):
        raise ConfigError(
            f"trade_filter must be a YAML mapping, got "
            f"{type(tf_raw).__name__!r}"
        )
    errors = _collect_trade_filter_unknown_keys(tf_raw, "trade_filter")
    if errors:
        raise ConfigError(
            "trade_filter schema validation failed:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_yaml(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path!r}")
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ConfigError(f"Config file is empty: {path!r}")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Config file must be a YAML mapping at top level, got {type(raw).__name__!r}: {path!r}"
        )
    return raw


def _get(raw: dict, *keys, default=None):
    """Safe nested get."""
    cur = raw
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is default:
            return default
    return cur


# _build_trade_filter_config is a thin shim around the shared helper.
# Phase 1 plan В§6.4 / Phase 2 plan В§5.1 В§14 WP-T2 step 0b.
from supertrend_optimizer.core.trade_filter_config import (  # noqa: E402
    build_trade_filter_config_from_raw as _build_trade_filter_config,
)


def _build_config(raw: dict[str, Any]) -> GridConfig:
    data_raw = raw.get("data", {})
    opt_raw = raw.get("optimization", {})
    bt_raw = raw.get("backtest", {})
    val_raw = raw.get("validation", {})
    wf_raw = val_raw.get("walk_forward", {})
    gates_raw = raw.get("gates", {})
    step_gates_raw = gates_raw.get("step", {})
    cand_gates_raw = gates_raw.get("candidate", {})
    ranking_raw = raw.get("ranking", {})
    scoring_raw = raw.get("scoring", {})
    status_raw = raw.get("status", {})
    bucket_raw = raw.get("bucket", {})
    export_raw = raw.get("export", {})
    if export_raw is None:
        export_raw = {}
    elif not isinstance(export_raw, dict):
        raise ConfigError(
            f"export must be a YAML mapping, got {type(export_raw).__name__!r}"
        )

    # data
    file_path = data_raw.get("file_path", "")
    periods_per_year = data_raw.get("periods_per_year", "auto")
    annualization_basis = data_raw.get("annualization_basis", None)

    # optimization
    atr_range = opt_raw.get("atr_period_range", [5, 55])
    mult_range = opt_raw.get("multiplier_range", [1.5, 5.5])
    mult_step = opt_raw.get("multiplier_step", 0.1)
    # Raw read (no type coercion) — needed so _validate_config can produce
    # accurate error messages like "got 0.5" instead of "got 0".
    # Mirrors the execution.max_workers pattern.
    atr_period_step_raw = opt_raw.get("atr_period_step", 1)
    trade_mode = opt_raw.get("trade_mode", "both")

    # backtest
    commission = bt_raw.get("commission", 0.000235)
    min_trades = bt_raw.get("min_trades_required", 3)
    early_exit_enabled = bt_raw.get("early_exit_enabled", False)
    early_exit_dd = bt_raw.get("early_exit_max_drawdown", 0.50)
    early_exit_bars = bt_raw.get("early_exit_check_bars", 50)

    # validation
    warmup = val_raw.get("warmup_period", 0)
    warmup_auto = val_raw.get("warmup_period_auto", False)

    # walk_forward
    train_size = wf_raw.get("train_size", "")
    test_size = wf_raw.get("test_size", "")
    step_size = wf_raw.get("step_size", None)
    scheme = wf_raw.get("scheme", "rolling")
    anchor = wf_raw.get("anchor", "start")
    min_train_bars = wf_raw.get("min_train_bars", 500)
    min_test_bars = wf_raw.get("min_test_bars", 100)

    # gates.step
    sg_min_trades = step_gates_raw.get("min_trades", None)
    sg_max_dd = step_gates_raw.get("max_drawdown_threshold", -0.50)

    # gates.candidate
    cg_pos_median = cand_gates_raw.get("positive_median_threshold", 0.0)
    cg_min_trades = cand_gates_raw.get("min_trades_median", 3.0)
    cg_worst = cand_gates_raw.get("worst_segment_pnl_threshold", None)
    cg_max_dd = cand_gates_raw.get("max_drawdown_threshold", -0.50)
    cg_min_ok_ratio = float(cand_gates_raw.get("min_ok_ratio", 0.7))
    cg_min_total_trades = int(cand_gates_raw.get("min_total_trades", 30))

    # ranking
    rk_mode = ranking_raw.get("mode", "legacy")
    rk_min_seg = ranking_raw.get("min_segments_for_ranking", None)
    rk_sort_by = ranking_raw.get("sort_by", "sum_pnl_pct_Median")
    rk_tiebreaker = ranking_raw.get("tiebreaker", "sum_pnl_pct_Min")

    # scoring
    default_weights = {
        "sum_pnl_pct_Median": 0.45,
        "profitable_segments_count": 0.35,
        "abs_max_drawdown_Min": 0.20,
    }
    score_weights = scoring_raw.get("score_weights", default_weights)
    normalization_mode = scoring_raw.get("normalization_mode", "minmax")
    min_passed_for_discrimination = int(scoring_raw.get("min_passed_for_discrimination", 5))
    low_spread_threshold = float(scoring_raw.get("low_spread_threshold", 1.0))

    # status
    min_meaningful = status_raw.get("min_meaningful_bars", 30)

    # bucket
    bk_atr_step = bucket_raw.get("atr_bucket_step", 2)
    bk_mult_step = bucket_raw.get("mult_bucket_step", 0.2)
    bk_min_buckets = bucket_raw.get("min_buckets_for_median", 5)

    # trade_filter (WP2; plan В§6.4; Appendix A v1.1 В§11)
    tf_raw = raw.get("trade_filter")
    trade_filter = _build_trade_filter_config(tf_raw) if tf_raw is not None else None

    # execution (WP-PAR; plan В§1.4) вЂ” no type coercion; _validate_config rejects bad types
    ex_raw = raw.get("execution", {})
    if ex_raw is None:
        ex_raw = {}
    elif not isinstance(ex_raw, dict):
        raise ConfigError(
            f"execution must be a YAML mapping, got {type(ex_raw).__name__!r}"
        )
    execution = ExecutionConfig(
        parallel_enabled=ex_raw.get("parallel_enabled", False),
        max_workers=ex_raw.get("max_workers", None),
        chunksize=ex_raw.get("chunksize", None),
        fallback_to_sequential=ex_raw.get("fallback_to_sequential", False),
    )
    export = ExportConfig(
        retain_per_bar_filter_diagnostics=export_raw.get(
            "retain_per_bar_filter_diagnostics", False
        ),
    )

    return GridConfig(
        data=DataConfig(
            file_path=file_path,
            periods_per_year=periods_per_year,
            annualization_basis=annualization_basis,
        ),
        optimization=OptimizationConfig(
            atr_period_range=list(atr_range) if not isinstance(atr_range, list) else atr_range,
            multiplier_range=list(mult_range) if not isinstance(mult_range, list) else mult_range,
            multiplier_step=float(mult_step),
            atr_period_step=atr_period_step_raw,
            trade_mode=str(trade_mode),
        ),
        backtest=BacktestConfig(
            commission=float(commission),
            min_trades_required=int(min_trades),
            early_exit_enabled=bool(early_exit_enabled),
            early_exit_max_drawdown=float(early_exit_dd),
            early_exit_check_bars=int(early_exit_bars),
        ),
        validation=ValidationConfig(
            warmup_period=int(warmup),
            warmup_period_auto=bool(warmup_auto),
            walk_forward=WalkForwardConfig(
                train_size=str(train_size),
                test_size=str(test_size),
                step_size=str(step_size) if step_size is not None else None,
                scheme=str(scheme),
                anchor=str(anchor),
                min_train_bars=int(min_train_bars),
                min_test_bars=int(min_test_bars),
            ),
        ),
        gates=GatesConfig(
            step=StepGatesConfig(
                min_trades=int(sg_min_trades) if sg_min_trades is not None else None,
                max_drawdown_threshold=float(sg_max_dd),
            ),
            candidate=CandidateGatesConfig(
                positive_median_threshold=float(cg_pos_median),
                min_trades_median=float(cg_min_trades),
                worst_segment_pnl_threshold=float(cg_worst) if cg_worst is not None else None,
                max_drawdown_threshold=float(cg_max_dd),
                min_ok_ratio=cg_min_ok_ratio,
                min_total_trades=cg_min_total_trades,
            ),
        ),
        ranking=RankingConfig(
            mode=str(rk_mode),
            min_segments_for_ranking=int(rk_min_seg) if rk_min_seg is not None else None,
            sort_by=str(rk_sort_by),
            tiebreaker=str(rk_tiebreaker),
        ),
        scoring=ScoringConfig(
            score_weights=dict(score_weights),
            normalization_mode=str(normalization_mode),
            min_passed_for_discrimination=min_passed_for_discrimination,
            low_spread_threshold=low_spread_threshold,
        ),
        status=StatusConfig(
            min_meaningful_bars=int(min_meaningful),
        ),
        bucket=BucketConfig(
            atr_bucket_step=int(bk_atr_step),
            mult_bucket_step=float(bk_mult_step),
            min_buckets_for_median=int(bk_min_buckets),
        ),
        trade_filter=trade_filter,
        execution=execution,
        export=export,
    )


# ---------------------------------------------------------------------------
# Trade-filter validation вЂ” re-exported from active donor shared module.
#
# OWNER DECISION v0.5.1 В§15 #1 (Phase 2 plan): the trade_filter validator and
# its lifecycle/type whitelists live in
# `donor/supertrend_optimizer/core/trade_filter_config.py`. This file keeps
# `_validate_trade_filter` as a thin shim with the historical (positional-only)
# signature used by `_validate_config`, so Phase 1 behaviour is unchanged.
#
# Plan reference: Phase 2 plan В§5.1, В§14 WP-T2 step 0b
# ---------------------------------------------------------------------------

from supertrend_optimizer.core.trade_filter_config import (
    _LIFECYCLE_STOP_CHECK_VALUES,
    _LIFECYCLE_STOPPING_EXIT_VALUES,
    _SUPPORTED_TRADE_FILTER_TYPES,
    validate_trade_filter as _shared_validate_trade_filter,
)


def _validate_trade_filter(
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Phase 1 shim around the shared validate_trade_filter (Phase 2 plan В§5.1).

    Delegates to the active-donor module with caller_pipeline="wf_grid". This
    preserves the legacy positional signature used elsewhere in this loader.

    Spec reference: Appendix A v1.1 В§11, В§11.1, В§11.2, В§11.3, В§15.6, В§17.2
    """
    _shared_validate_trade_filter(
        tf, errors, raw_user_keys, caller_pipeline="wf_grid"
    )
    return


def _validate_trade_filter_legacy_inline(  # pragma: no cover  (kept for diff reviewers)
    tf: TradeFilterConfig,
    errors: list[str],
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Pre-shim local validator. Retained ONLY as a documentation marker so
    reviewers can locate the migration point. NEVER called at runtime.

    All В§6.5 rules are implemented in
    ``supertrend_optimizer.core.trade_filter_config.validate_trade_filter``;
    the one non-rule remains: freeze_confirmed_legs < local_window is VALID
    (В§3.2, В§17.20) вЂ” no reject, no warning.
    """
    raise RuntimeError(
        "_validate_trade_filter_legacy_inline is not callable; use the shim "
        "_validate_trade_filter instead."
    )


from supertrend_optimizer.core.trade_filter_config import (  # noqa: E402
    resolve_trade_filter_mode_in_place as _resolve_trade_filter_mode_shared,
    resolve_exit_off_mode_in_place as _resolve_exit_off_mode_shared,
    resolve_exit_b_immediate_off_in_place as _resolve_exit_b_immediate_off_shared,
    resolve_time_filter_in_place as _resolve_time_filter_shared,
    resolve_volume_baseline_session_in_place as _resolve_volume_baseline_session_shared,
    resolve_zigzag_enabled_in_place as _resolve_zigzag_enabled_shared,
    resolve_volume_enabled_in_place as _resolve_volume_enabled_shared,
    resolve_volume_defaults_in_place as _resolve_volume_defaults_shared,
)


def _resolve_zigzag_enabled_in_place(
    cfg: GridConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Phase 3 resolver hook for ``trade_filter.zigzag.enabled``."""
    _resolve_zigzag_enabled_shared(cfg.trade_filter, raw_user_keys)
    return


def _resolve_volume_enabled_in_place(
    cfg: GridConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Phase 3 resolver hook for ``trade_filter.volume.enabled``."""
    _resolve_volume_enabled_shared(cfg.trade_filter, raw_user_keys)
    return


def _resolve_trade_filter_mode_in_place(
    cfg: GridConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Resolve effective zigzag mode after validation and set it on the config.

    After this call ``cfg.trade_filter.zigzag.mode`` is always a valid mode
    literal (never None) for enabled filters.  This ensures that downstream
    consumers (``build_zigzag_global_stats``) can read mode directly without
    re-implementing the legacy migration logic.

    For disabled or absent trade_filter, this function is a no-op.

    WP-V3-2 (РўР— v3 В§3.1, В§5)
    """
    _resolve_trade_filter_mode_shared(cfg.trade_filter, raw_user_keys)
    return


def _resolve_exit_off_mode_in_place(
    cfg: GridConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Resolve default lifecycle.exit_off_mode when YAML key is absent."""
    _resolve_exit_off_mode_shared(cfg.trade_filter, raw_user_keys)
    return


def _resolve_exit_b_immediate_off_in_place(
    cfg: GridConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Resolve default lifecycle.exit_b_immediate_off when YAML key is absent."""
    _resolve_exit_b_immediate_off_shared(cfg.trade_filter, raw_user_keys)
    return


def _resolve_time_filter_in_place(
    cfg: GridConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Materialise parsed time-window fields when time_filter.enabled=True."""
    _resolve_time_filter_shared(cfg.trade_filter, raw_user_keys)
    return


def _resolve_volume_baseline_session_in_place(
    cfg: GridConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Materialise parsed volume baseline-session window fields."""
    _resolve_volume_baseline_session_shared(cfg.trade_filter, raw_user_keys)
    return


def _resolve_volume_defaults_in_place(
    cfg: GridConfig,
    raw_user_keys: frozenset[tuple[str, ...]],
) -> None:
    """Phase 5 default hook for future volume config."""
    _resolve_volume_defaults_shared(cfg.trade_filter, raw_user_keys)
    return




def _validate_config(
    cfg: GridConfig,
    raw_user_keys: Optional[frozenset[tuple[str, ...]]] = None,
) -> None:
    errors: list[str] = []

    # data.file_path вЂ” required (non-empty string); actual file existence
    # checked later when data is loaded (not here, to allow tests without real files)
    if not cfg.data.file_path:
        errors.append("data.file_path is required and must be non-empty")

    # data.periods_per_year
    ppy = cfg.data.periods_per_year
    if ppy != "auto":
        try:
            v = float(ppy)
            if not math.isfinite(v) or v <= 0:
                errors.append(
                    f"data.periods_per_year must be 'auto' or a positive finite number, got {ppy!r}"
                )
        except (TypeError, ValueError):
            errors.append(
                f"data.periods_per_year must be 'auto' or a numeric value, got {ppy!r}"
            )

    # optimization.atr_period_range
    atr = cfg.optimization.atr_period_range
    if not isinstance(atr, list) or len(atr) != 2:
        errors.append("optimization.atr_period_range must be a list [min, max] with 2 elements")
    else:
        try:
            a_min, a_max = int(atr[0]), int(atr[1])
            if a_min < 2:
                errors.append(f"optimization.atr_period_range[0] (min) must be >= 2, got {a_min}")
            if a_max < a_min:
                errors.append(
                    f"optimization.atr_period_range: max ({a_max}) must be >= min ({a_min})"
                )
        except (TypeError, ValueError):
            errors.append("optimization.atr_period_range values must be integers")

    # optimization.multiplier_range
    mlt = cfg.optimization.multiplier_range
    if not isinstance(mlt, list) or len(mlt) != 2:
        errors.append("optimization.multiplier_range must be a list [min, max] with 2 elements")
    else:
        try:
            m_min, m_max = float(mlt[0]), float(mlt[1])
            if m_min <= 0:
                errors.append(f"optimization.multiplier_range[0] (min) must be > 0, got {m_min}")
            if m_max < m_min:
                errors.append(
                    f"optimization.multiplier_range: max ({m_max}) must be >= min ({m_min})"
                )
        except (TypeError, ValueError):
            errors.append("optimization.multiplier_range values must be numeric")

    # optimization.multiplier_step
    if cfg.optimization.multiplier_step <= 0:
        errors.append(
            f"optimization.multiplier_step must be > 0, got {cfg.optimization.multiplier_step}"
        )

    # optimization.atr_period_step — strict type validation, no coercion.
    # bool is rejected explicitly because isinstance(True, int) is True in Python.
    v = cfg.optimization.atr_period_step
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        errors.append(
            f"optimization.atr_period_step must be an integer >= 1, got {v!r}"
        )

    # optimization.trade_mode
    if cfg.optimization.trade_mode not in _TRADE_MODES:
        errors.append(
            f"optimization.trade_mode must be one of {_TRADE_MODES}, got {cfg.optimization.trade_mode!r}"
        )

    # backtest.commission
    if not math.isfinite(cfg.backtest.commission) or cfg.backtest.commission < 0:
        errors.append(
            f"backtest.commission must be >= 0 and finite, got {cfg.backtest.commission}"
        )

    # backtest.min_trades_required
    if cfg.backtest.min_trades_required < 0:
        errors.append(
            f"backtest.min_trades_required must be >= 0, got {cfg.backtest.min_trades_required}"
        )

    # backtest.early_exit_enabled вЂ” must be False for WF OOS pipeline.
    # When enabled, the donor truncates all arrays to exit_bar while
    # test_start_idx/test_end_idx still reflect the full declared OOS window.
    # This causes OOS metrics to silently cover a shorter horizon than declared
    # (horizon distortion).  Disable early_exit at the config level to prevent this.
    if cfg.backtest.early_exit_enabled:
        errors.append(
            "backtest.early_exit_enabled must be false for the WF grid search pipeline. "
            "Enabling early_exit on OOS steps causes horizon distortion: arrays are "
            "truncated to exit_bar while test window indices remain unchanged, so "
            "reported metrics cover a shorter period than declared. "
            "Set early_exit_enabled: false in config.yaml."
        )

    # backtest.early_exit_max_drawdown
    if cfg.backtest.early_exit_max_drawdown <= 0:
        errors.append(
            f"backtest.early_exit_max_drawdown must be > 0, got {cfg.backtest.early_exit_max_drawdown}"
        )

    # backtest.early_exit_check_bars
    if cfg.backtest.early_exit_check_bars < 0:
        errors.append(
            f"backtest.early_exit_check_bars must be >= 0, got {cfg.backtest.early_exit_check_bars}"
        )

    # validation.warmup_period
    if cfg.validation.warmup_period < 0:
        errors.append(
            f"validation.warmup_period must be >= 0, got {cfg.validation.warmup_period}"
        )

    # validation.walk_forward.train_size вЂ” required
    if not cfg.validation.walk_forward.train_size:
        errors.append("validation.walk_forward.train_size is required and must be non-empty")

    # validation.walk_forward.test_size вЂ” required
    if not cfg.validation.walk_forward.test_size:
        errors.append("validation.walk_forward.test_size is required and must be non-empty")

    # validation.walk_forward.scheme
    if cfg.validation.walk_forward.scheme not in _WF_SCHEMES:
        errors.append(
            f"validation.walk_forward.scheme must be one of {_WF_SCHEMES}, "
            f"got {cfg.validation.walk_forward.scheme!r}"
        )

    # validation.walk_forward.anchor
    if cfg.validation.walk_forward.anchor not in _WF_ANCHORS:
        errors.append(
            f"validation.walk_forward.anchor must be one of {_WF_ANCHORS}, "
            f"got {cfg.validation.walk_forward.anchor!r}"
        )

    # validation.walk_forward.min_train_bars
    if cfg.validation.walk_forward.min_train_bars < 1:
        errors.append(
            f"validation.walk_forward.min_train_bars must be >= 1, "
            f"got {cfg.validation.walk_forward.min_train_bars}"
        )

    # validation.walk_forward.min_test_bars
    if cfg.validation.walk_forward.min_test_bars < 1:
        errors.append(
            f"validation.walk_forward.min_test_bars must be >= 1, "
            f"got {cfg.validation.walk_forward.min_test_bars}"
        )

    # gates.step.max_drawdown_threshold <= 0
    if cfg.gates.step.max_drawdown_threshold > 0:
        errors.append(
            f"gates.step.max_drawdown_threshold must be <= 0, got {cfg.gates.step.max_drawdown_threshold}"
        )

    # gates.step.min_trades >= 0
    if cfg.gates.step.min_trades is not None and cfg.gates.step.min_trades < 0:
        errors.append(
            f"gates.step.min_trades must be >= 0, got {cfg.gates.step.min_trades}"
        )

    # gates.candidate.min_trades_median >= 0
    if cfg.gates.candidate.min_trades_median < 0:
        errors.append(
            f"gates.candidate.min_trades_median must be >= 0, got {cfg.gates.candidate.min_trades_median}"
        )

    # gates.candidate.max_drawdown_threshold <= 0
    if cfg.gates.candidate.max_drawdown_threshold > 0:
        errors.append(
            f"gates.candidate.max_drawdown_threshold must be <= 0, "
            f"got {cfg.gates.candidate.max_drawdown_threshold}"
        )

    # gates.candidate.min_ok_ratio вЂ” must be in [0.0, 1.0]
    mor = cfg.gates.candidate.min_ok_ratio
    if not (0.0 <= mor <= 1.0):
        errors.append(
            f"gates.candidate.min_ok_ratio must be in [0.0, 1.0], got {mor}"
        )

    # gates.candidate.min_total_trades вЂ” must be >= 0
    if cfg.gates.candidate.min_total_trades < 0:
        errors.append(
            f"gates.candidate.min_total_trades must be >= 0, got {cfg.gates.candidate.min_total_trades}"
        )

    # ranking.min_segments_for_ranking вЂ” if set, must be an integer >= 1
    # FIX-2.5: 0 and negative values are semantically meaningless.
    if cfg.ranking.min_segments_for_ranking is not None:
        ms = cfg.ranking.min_segments_for_ranking
        if not isinstance(ms, int):
            errors.append(
                f"ranking.min_segments_for_ranking must be an integer, got {type(ms).__name__!r}"
            )
        elif ms < 1:
            errors.append(
                f"ranking.min_segments_for_ranking must be >= 1, got {ms}"
            )

    # ranking.mode
    valid_modes = {m.value for m in RankingMode}
    if cfg.ranking.mode not in valid_modes:
        errors.append(
            f"ranking.mode must be one of {sorted(valid_modes)}, got {cfg.ranking.mode!r}"
        )

    # scoring.score_weights вЂ” all values > 0
    for k, v in cfg.scoring.score_weights.items():
        if v <= 0:
            errors.append(f"scoring.score_weights[{k!r}] must be > 0, got {v}")

    # scoring.normalization_mode
    _NORM_MODES = ("minmax", "rank")
    if cfg.scoring.normalization_mode not in _NORM_MODES:
        errors.append(
            f"scoring.normalization_mode must be one of {_NORM_MODES}, "
            f"got {cfg.scoring.normalization_mode!r}"
        )

    # scoring.min_passed_for_discrimination вЂ” must be >= 1
    if cfg.scoring.min_passed_for_discrimination < 1:
        errors.append(
            f"scoring.min_passed_for_discrimination must be >= 1, "
            f"got {cfg.scoring.min_passed_for_discrimination}"
        )

    # scoring.low_spread_threshold вЂ” must be > 0
    if cfg.scoring.low_spread_threshold <= 0:
        errors.append(
            f"scoring.low_spread_threshold must be > 0, "
            f"got {cfg.scoring.low_spread_threshold}"
        )

    # status.min_meaningful_bars > 0
    if cfg.status.min_meaningful_bars <= 0:
        errors.append(
            f"status.min_meaningful_bars must be > 0, got {cfg.status.min_meaningful_bars}"
        )

    # bucket.atr_bucket_step > 0
    if cfg.bucket.atr_bucket_step <= 0:
        errors.append(
            f"bucket.atr_bucket_step must be > 0, got {cfg.bucket.atr_bucket_step}"
        )

    # bucket.mult_bucket_step > 0
    if cfg.bucket.mult_bucket_step <= 0:
        errors.append(
            f"bucket.mult_bucket_step must be > 0, got {cfg.bucket.mult_bucket_step}"
        )

    # bucket.min_buckets_for_median >= 1
    if cfg.bucket.min_buckets_for_median < 1:
        errors.append(
            f"bucket.min_buckets_for_median must be >= 1, got {cfg.bucket.min_buckets_for_median}"
        )

    # execution (WP-PAR; plan В§1.5) вЂ” strict type validation, no coercion
    ex = cfg.execution
    if not isinstance(ex.parallel_enabled, bool):
        errors.append(
            f"execution.parallel_enabled must be bool, got "
            f"{type(ex.parallel_enabled).__name__!r}"
        )
    if not isinstance(ex.fallback_to_sequential, bool):
        errors.append(
            f"execution.fallback_to_sequential must be bool, got "
            f"{type(ex.fallback_to_sequential).__name__!r}"
        )
    if ex.max_workers is not None:
        if (
            not isinstance(ex.max_workers, int)
            or isinstance(ex.max_workers, bool)
            or ex.max_workers < 1
        ):
            errors.append(
                f"execution.max_workers must be None or int >= 1, "
                f"got {ex.max_workers!r}"
            )
    if ex.chunksize is not None:
        if (
            not isinstance(ex.chunksize, int)
            or isinstance(ex.chunksize, bool)
            or ex.chunksize < 1
        ):
            errors.append(
                f"execution.chunksize must be None or int >= 1, "
                f"got {ex.chunksize!r}"
            )

    # export
    exp = cfg.export
    if not isinstance(exp.retain_per_bar_filter_diagnostics, bool):
        errors.append(
            "export.retain_per_bar_filter_diagnostics must be bool, got "
            f"{type(exp.retain_per_bar_filter_diagnostics).__name__!r}"
        )

    # trade_filter (WP2; plan В§6.5; Appendix A v1.1 В§11-В§11.3, В§15.6)
    if cfg.trade_filter is not None:
        _ruk = raw_user_keys if raw_user_keys is not None else frozenset()
        _validate_trade_filter(cfg.trade_filter, errors, _ruk)

    if errors:
        raise ConfigError("Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    # Compatibility warnings (not errors) вЂ” issued after validation passes
    if cfg.export.retain_per_bar_filter_diagnostics:
        warnings.warn(
            "retain_per_bar_filter_diagnostics=true may increase memory and IPC usage",
            RuntimeWarning,
            stacklevel=2,
        )
    _warn_bucket_step_compatibility(cfg)


def _warn_bucket_step_compatibility(cfg: GridConfig) -> None:
    """Issue warnings (not errors) when bucket steps are incompatible with grid steps."""
    mult_step = cfg.optimization.multiplier_step
    mult_bucket_step = cfg.bucket.mult_bucket_step

    if mult_step > 0 and mult_bucket_step > 0:
        ratio = mult_bucket_step / mult_step
        # Check if ratio is close to an integer (mult_bucket_step is integer multiple of mult_step)
        if abs(ratio - round(ratio)) > 1e-9:
            _logger.warning(
                "bucket.mult_bucket_step=%.4f is not an integer multiple of "
                "optimization.multiplier_step=%.4f (ratio=%.6f). "
                "Bucket boundaries will not align with grid points exactly.",
                mult_bucket_step, mult_step, ratio,
            )

    atr_range = cfg.optimization.atr_period_range
    atr_bucket_step = cfg.bucket.atr_bucket_step
    if (
        isinstance(atr_range, list)
        and len(atr_range) == 2
        and atr_bucket_step > 0
    ):
        atr_span = int(atr_range[1]) - int(atr_range[0])
        if atr_bucket_step > atr_span:
            _logger.warning(
                "bucket.atr_bucket_step=%d is larger than the ATR range span=%d "
                "[%d, %d]. All ATR grid points may fall into a single bucket.",
                atr_bucket_step, atr_span, int(atr_range[0]), int(atr_range[1]),
            )

    # Warnings A/B — interaction between atr_period_step and atr_bucket_step.
    # Both are silent at atr_period_step == 1 (clean regression).
    # atr_period_step is guaranteed int >= 1 by _validate_config at this point.
    atr_period_step = cfg.optimization.atr_period_step
    if atr_period_step >= 1 and atr_bucket_step > 0:
        # Warning A: bucket step not multiple of grid step (integer modulo).
        if atr_bucket_step % atr_period_step != 0:
            _logger.warning(
                "bucket.atr_bucket_step=%d is not an integer multiple of "
                "optimization.atr_period_step=%d. "
                "Bucket boundaries will not align with grid points exactly.",
                atr_bucket_step, atr_period_step,
            )
        # Warning B: grid step larger than bucket step.
        if atr_period_step > atr_bucket_step:
            _logger.warning(
                "optimization.atr_period_step=%d is greater than "
                "bucket.atr_bucket_step=%d. Each bucket will contain at most one "
                "ATR grid point, but bucket labels still display the full bucket width.",
                atr_period_step, atr_bucket_step,
            )


def resolve_periods_per_year(cfg: GridConfig, data: pd.DataFrame) -> GridConfig:
    """
    Resolve periods_per_year and return a new GridConfig with resolved value set.

    Delegates to donor ``resolve_periods_per_year_from_config`` for full
    algorithm parity (CALENDAR vs TRADING basis, active-day median, etc.).

    Uses deepcopy to guarantee no shared mutable state between the original
    config and the returned copy (safe for concurrent/iterative grid runs).

    Parameters
    ----------
    cfg:
        GridConfig (validated, not yet resolved).
    data:
        OHLC DataFrame.  DatetimeIndex required when periods_per_year="auto".

    Returns
    -------
    GridConfig
        Deep copy with ``resolved_periods_per_year`` set.

    Raises
    ------
    ConfigError
        If auto-detect is requested but data has no DatetimeIndex, or if
        the donor resolver raises ValueError.
    """
    ppy = cfg.data.periods_per_year

    if ppy == "auto" and not isinstance(data.index, pd.DatetimeIndex):
        raise ConfigError(
            "Cannot auto-detect periods_per_year without DatetimeIndex; "
            "set data.periods_per_year explicitly"
        )

    try:
        resolved = resolve_periods_per_year_from_config(
            config_value=ppy,
            index=data.index,
            explicit_basis=cfg.data.annualization_basis,
            market=None,
        )
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"periods_per_year resolution failed: {exc}") from exc

    cfg_copy = copy.deepcopy(cfg)
    cfg_copy.resolved_periods_per_year = float(resolved)
    return cfg_copy


# Keep internal alias for backward compat
_resolve_periods_per_year = resolve_periods_per_year
