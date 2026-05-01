"""
Configuration validation for CLI.

This module provides validation functions for configuration files
used by the optimizer and tester CLI tools.
"""

from typing import Any, Dict, List, Union
import warnings

from supertrend_optimizer.utils.constants import (
    DEFAULT_ATR_BUCKET_STEP,
    DEFAULT_MULT_BUCKET_STEP,
    MIN_BARS_AFTER_WARMUP_THRESHOLD,
    MIN_JOBS,
    MIN_TRIALS,
    MIN_WARMUP_PERIOD,
    RANGE_LENGTH,
)
from supertrend_optimizer.utils.exceptions import ConfigError

# Amplitude filter modes are tester-only in v1.3 (patch §0).
# ZigZag filter modes are tester-only in v2.0 (plan §3.9).
# The optimizer / walk-forward pipeline must not run with any of these modes.
_TESTER_ONLY_MODES = frozenset({
    "amplitude", "amplitude_and_volume",
    "zigzag", "zigzag_and_volume",
})
from supertrend_optimizer.utils.enums import MarketType, ExecutionModel, AnnualizationBasis
from supertrend_optimizer.utils.time_utils import parse_duration_string


def validate_config(cfg: Dict[str, Any]) -> None:
    """
    Validate configuration dictionary.
    
    Ensures all required keys exist and have valid types/ranges for the
    sections validated by this function (optimization, validation, backtest, robustness).
    After this validation passes, KeyError should not occur when accessing
    keys from these validated sections in runtime.
    
    Args:
        cfg: Configuration dictionary loaded from YAML
        
    Raises:
        ConfigError: If configuration is invalid or missing required keys
    """
    # Guard: tester-only filter modes (amplitude — v1.3, zigzag — v2.0).
    _filter_mode = str(cfg.get("filters", {}).get("mode", "none")) if isinstance(
        cfg.get("filters"), dict
    ) else "none"
    if _filter_mode in _TESTER_ONLY_MODES:
        raise ConfigError(
            f"filter mode {_filter_mode!r} is not supported in the optimizer / "
            f"walk-forward pipeline (tester-only). "
            f"Remove 'filters.mode' from the optimizer config or set it to "
            f"'none', 'volatility', 'volume', or 'volatility_and_volume'."
        )

    _validate_optimization_section(cfg)
    _validate_validation_section(cfg)
    _validate_backtest_section(cfg)
    _validate_robustness_section(cfg)
    _validate_walk_forward_section(cfg)
    _validate_walk_forward_top_k_section(cfg)
    _validate_mini_grid_config(cfg)
    _validate_mini_grid_refine_config(cfg)
    _validate_canonical_ranking_section(cfg)
    _validate_canonical_soft_pnl_gate_section(cfg)
    _validate_plateau_analysis_section(cfg)


def _validate_range(
    cfg: Dict[str, Any],
    section: str,
    key: str,
    value_type: type,
    allow_zero: bool = False
) -> None:
    """
    Validate a range parameter [min, max].
    
    Args:
        cfg: Configuration section dictionary
        section: Section name for error messages (e.g., 'optimization')
        key: Key name for the range parameter
        value_type: Expected type for range values (int or float)
        allow_zero: Whether zero values are allowed (>= 0 if True, > 0 if False)
        
    Raises:
        ConfigError: If range is invalid
    """
    full_key = f"{section}.{key}"
    
    if key not in cfg:
        raise ConfigError(f"Missing required key: '{full_key}'")
    
    range_val = cfg[key]
    if not isinstance(range_val, (list, tuple)) or len(range_val) != RANGE_LENGTH:
        type_name = "integers" if value_type == int else "floats"
        actual_len = len(range_val) if isinstance(range_val, (list, tuple)) else "N/A"
        raise ConfigError(
            f"'{full_key}' must be a list of {RANGE_LENGTH} {type_name} [min, max], "
            f"got: {type(range_val).__name__} (len={actual_len})"
        )
    
    min_val, max_val = range_val
    type_name = value_type.__name__
    
    if not isinstance(min_val, value_type) or not isinstance(max_val, value_type):
        raise ConfigError(
            f"'{full_key}' values must be {type_name}, "
            f"got: [{type(min_val).__name__}, {type(max_val).__name__}]"
        )
    
    # Validate value ranges: >= 0 if allow_zero, > 0 otherwise
    if allow_zero:
        if min_val < 0:
            raise ConfigError(
                f"'{full_key}' values must be >= 0, "
                f"got: [{min_val}, {max_val}]"
            )
        if max_val < 0:
            raise ConfigError(
                f"'{full_key}' values must be >= 0, "
                f"got: [{min_val}, {max_val}]"
            )
    else:
        if min_val <= 0:
            raise ConfigError(
                f"'{full_key}' values must be > 0, "
                f"got: [{min_val}, {max_val}]"
            )
        if max_val <= 0:
            raise ConfigError(
                f"'{full_key}' values must be > 0, "
                f"got: [{min_val}, {max_val}]"
            )
    
    if min_val > max_val:
        raise ConfigError(
            f"'{full_key}[0]' must be <= {full_key}[1], "
            f"got: [{min_val}, {max_val}]"
        )


def _validate_optimization_section(cfg: Dict[str, Any]) -> None:
    """Validate optimization section of config."""
    if "optimization" not in cfg:
        raise ConfigError("Missing required section: 'optimization'")
    
    opt = cfg["optimization"]
    
    # Validate trade_mode (optional, defaults to "revers")
    if "trade_mode" in opt:
        trade_mode = opt["trade_mode"]
        if not isinstance(trade_mode, str):
            raise ConfigError(
                f"'optimization.trade_mode' must be a string, "
                f"got: {type(trade_mode).__name__}"
            )
        valid_modes = ["revers", "long", "short"]
        if trade_mode not in valid_modes:
            raise ConfigError(
                f"'optimization.trade_mode' must be one of {valid_modes}, "
                f"got: '{trade_mode}'"
            )
    
    # Validate objective_metric (optional, defaults to "sortino")
    if "objective_metric" in opt:
        metric = opt["objective_metric"]
        if not isinstance(metric, str):
            raise ConfigError(
                f"'optimization.objective_metric' must be a string, "
                f"got: {type(metric).__name__}"
            )
        valid_metrics = ["sortino", "sum_pnl_pct"]
        if metric not in valid_metrics:
            raise ConfigError(
                f"'optimization.objective_metric' must be one of {valid_metrics}, "
                f"got: '{metric}'"
            )
    
    # Validate ranking_metric (required for robustness compatibility)
    if "ranking_metric" not in opt:
        # Default to "value" if not specified
        opt["ranking_metric"] = "value"
    else:
        ranking_metric = opt["ranking_metric"]
        if not isinstance(ranking_metric, str):
            raise ConfigError(
                f"'optimization.ranking_metric' must be a string, "
                f"got: {type(ranking_metric).__name__}"
            )
        valid_ranking = ["value", "robust_score"]
        if ranking_metric not in valid_ranking:
            raise ConfigError(
                f"'optimization.ranking_metric' must be one of {valid_ranking}, "
                f"got: '{ranking_metric}'"
            )
    
    # Validate range parameters
    _validate_range(opt, "optimization", "atr_period_range", int)
    _validate_range(opt, "optimization", "multiplier_range", float)
    
    # Validate multiplier_step
    if "multiplier_step" not in opt:
        raise ConfigError("Missing required key: 'optimization.multiplier_step'")
    
    step = opt["multiplier_step"]
    if not isinstance(step, (int, float)):
        raise ConfigError(
            f"'optimization.multiplier_step' must be a number, "
            f"got: {type(step).__name__}"
        )
    
    if step <= 0:
        raise ConfigError(
            f"'optimization.multiplier_step' must be positive, got: {step}"
        )
    
    # Validate that step is reasonable relative to range
    mult_range = opt["multiplier_range"]
    range_size = mult_range[1] - mult_range[0]
    if step > range_size:
        raise ConfigError(
            f"'optimization.multiplier_step' ({step}) is larger than "
            f"multiplier_range ({mult_range[0]}-{mult_range[1]})"
        )
    
    # Validate n_trials
    if "n_trials" not in opt:
        raise ConfigError("Missing required key: 'optimization.n_trials'")
    
    n_trials = opt["n_trials"]
    if not isinstance(n_trials, int):
        raise ConfigError(
            f"'optimization.n_trials' must be an integer, "
            f"got: {type(n_trials).__name__}"
        )
    
    if n_trials < MIN_TRIALS:
        raise ConfigError(
            f"'optimization.n_trials' must be >= {MIN_TRIALS}, got: {n_trials}"
        )
    
    # Validate n_jobs
    if "n_jobs" not in opt:
        raise ConfigError("Missing required key: 'optimization.n_jobs'")
    
    n_jobs = opt["n_jobs"]
    if not isinstance(n_jobs, int):
        raise ConfigError(
            f"'optimization.n_jobs' must be an integer, "
            f"got: {type(n_jobs).__name__}"
        )
    
    if n_jobs < MIN_JOBS:
        raise ConfigError(
            f"'optimization.n_jobs' must be >= {MIN_JOBS}, got: {n_jobs}"
        )


def _validate_validation_section(cfg: Dict[str, Any]) -> None:
    """Validate validation section of config."""
    if "validation" not in cfg:
        raise ConfigError("Missing required section: 'validation'")
    
    val = cfg["validation"]
    
    # warmup_period
    if "warmup_period" not in val:
        raise ConfigError("Missing required key: 'validation.warmup_period'")
    
    warmup = val["warmup_period"]
    if not isinstance(warmup, int):
        raise ConfigError(
            f"'validation.warmup_period' must be an integer, "
            f"got: {type(warmup).__name__}"
        )
    
    if warmup < MIN_WARMUP_PERIOD:
        raise ConfigError(
            f"'validation.warmup_period' must be >= {MIN_WARMUP_PERIOD}, got: {warmup}"
        )
    
    # warmup_period_auto (optional, defaults to False)
    if "warmup_period_auto" in val:
        auto = val["warmup_period_auto"]
        if not isinstance(auto, bool):
            raise ConfigError(
                f"'validation.warmup_period_auto' must be a boolean, "
                f"got: {type(auto).__name__}"
            )
    
    # min_bars_after_warmup (optional, defaults to 100)
    if "min_bars_after_warmup" in val:
        min_bars = val["min_bars_after_warmup"]
        if not isinstance(min_bars, int):
            raise ConfigError(
                f"'validation.min_bars_after_warmup' must be an integer, "
                f"got: {type(min_bars).__name__}"
            )
        if min_bars < MIN_BARS_AFTER_WARMUP_THRESHOLD:
            raise ConfigError(
                f"'validation.min_bars_after_warmup' must be >= {MIN_BARS_AFTER_WARMUP_THRESHOLD}, got: {min_bars}"
            )


def _validate_backtest_section(cfg: Dict[str, Any]) -> None:
    """Validate backtest section of config."""
    if "backtest" not in cfg:
        raise ConfigError("Missing required section: 'backtest'")
    
    bt = cfg["backtest"]
    
    # commission
    if "commission" not in bt:
        raise ConfigError("Missing required key: 'backtest.commission'")
    
    commission = bt["commission"]
    if not isinstance(commission, (int, float)):
        raise ConfigError(
            f"'backtest.commission' must be a number, "
            f"got: {type(commission).__name__}"
        )
    
    if commission < 0:
        raise ConfigError(
            f"'backtest.commission' must be >= 0, got: {commission}"
        )
    
    if commission >= 1:
        raise ConfigError(
            f"'backtest.commission' must be < 1 (use decimal format, e.g., 0.001 for 0.1%), got: {commission}"
        )
    
    # early_exit_enabled
    if "early_exit_enabled" not in bt:
        raise ConfigError("Missing required key: 'backtest.early_exit_enabled'")
    
    early_exit = bt["early_exit_enabled"]
    if not isinstance(early_exit, bool):
        raise ConfigError(
            f"'backtest.early_exit_enabled' must be a boolean, "
            f"got: {type(early_exit).__name__}"
        )
    
    # If early exit is enabled, validate related parameters
    if early_exit:
        if "early_exit_max_drawdown" not in bt:
            raise ConfigError("Missing required key: 'backtest.early_exit_max_drawdown' (required when early_exit_enabled=true)")
        
        max_dd = bt["early_exit_max_drawdown"]
        if not isinstance(max_dd, (int, float)):
            raise ConfigError(
                f"'backtest.early_exit_max_drawdown' must be a number, "
                f"got: {type(max_dd).__name__}"
            )
        
        if max_dd <= 0 or max_dd > 1:
            raise ConfigError(
                f"'backtest.early_exit_max_drawdown' must be between 0 and 1, got: {max_dd}"
            )
        
        if "early_exit_check_bars" not in bt:
            raise ConfigError("Missing required key: 'backtest.early_exit_check_bars' (required when early_exit_enabled=true)")
        
        check_bars = bt["early_exit_check_bars"]
        if not isinstance(check_bars, int):
            raise ConfigError(
                f"'backtest.early_exit_check_bars' must be an integer, "
                f"got: {type(check_bars).__name__}"
            )
        
        if check_bars < 1:
            raise ConfigError(
                f"'backtest.early_exit_check_bars' must be >= 1, got: {check_bars}"
            )
    
    # annualization_factor (int or "auto")
    if "annualization_factor" not in bt:
        raise ConfigError("Missing required key: 'backtest.annualization_factor'")
    
    ann_factor = bt["annualization_factor"]
    if isinstance(ann_factor, str):
        if ann_factor != "auto":
            raise ConfigError(
                f"'backtest.annualization_factor' must be an integer >= 1 or 'auto', "
                f"got string: '{ann_factor}'"
            )
    elif isinstance(ann_factor, int):
        if ann_factor < 1:
            raise ConfigError(
                f"'backtest.annualization_factor' must be >= 1, got: {ann_factor}"
            )
    else:
        raise ConfigError(
            f"'backtest.annualization_factor' must be an integer or 'auto', "
            f"got: {type(ann_factor).__name__}"
        )
    
    # annualization_basis (optional: "calendar" | "trading")
    if "annualization_basis" in bt:
        basis = bt["annualization_basis"]
        if not isinstance(basis, str):
            raise ConfigError(
                f"'backtest.annualization_basis' must be a string, "
                f"got: {type(basis).__name__}"
            )
        try:
            AnnualizationBasis(basis)
        except ValueError:
            valid_values = [b.value for b in AnnualizationBasis]
            raise ConfigError(
                f"'backtest.annualization_basis' must be one of {valid_values}, "
                f"got: '{basis}'"
            )
    
    # market (optional: "stocks" | "crypto" | "futures" | "forex")
    if "market" in bt:
        market = bt["market"]
        if not isinstance(market, str):
            raise ConfigError(
                f"'backtest.market' must be a string, "
                f"got: {type(market).__name__}"
            )
        try:
            MarketType(market)
        except ValueError:
            valid_values = [m.value for m in MarketType]
            raise ConfigError(
                f"'backtest.market' must be one of {valid_values}, "
                f"got: '{market}'"
            )
    
    # execution_model (optional: "open_to_open" | "close_to_close")
    if "execution_model" in bt:
        exec_model = bt["execution_model"]
        if not isinstance(exec_model, str):
            raise ConfigError(
                f"'backtest.execution_model' must be a string, "
                f"got: {type(exec_model).__name__}"
            )
        try:
            ExecutionModel(exec_model)
        except ValueError:
            valid_values = [e.value for e in ExecutionModel]
            raise ConfigError(
                f"'backtest.execution_model' must be one of {valid_values}, "
                f"got: '{exec_model}'"
            )
    
    # min_trades_required
    if "min_trades_required" not in bt:
        raise ConfigError("Missing required key: 'backtest.min_trades_required'")
    
    min_trades = bt["min_trades_required"]
    if not isinstance(min_trades, int):
        raise ConfigError(
            f"'backtest.min_trades_required' must be an integer, "
            f"got: {type(min_trades).__name__}"
        )
    
    if min_trades < 0:
        raise ConfigError(
            f"'backtest.min_trades_required' must be >= 0, got: {min_trades}"
        )


def _validate_robustness_section(cfg: Dict[str, Any]) -> None:
    """
    Validate robustness section of config.
    
    Robustness section is optional. If missing or disabled, no validation is performed.
    """
    if "robustness" not in cfg:
        # Robustness section is optional
        return
    
    rob = cfg["robustness"]
    
    # Validate enabled flag
    if "enabled" not in rob:
        # Default to false if not specified
        rob["enabled"] = False
    else:
        enabled = rob["enabled"]
        if not isinstance(enabled, bool):
            raise ConfigError(
                f"'robustness.enabled' must be a boolean, "
                f"got: {type(enabled).__name__}"
            )
    
    # If disabled, skip further validation
    if not rob["enabled"]:
        return
    
    # Validate top_k_for_robustness
    if "top_k_for_robustness" not in rob:
        raise ConfigError("Missing required key: 'robustness.top_k_for_robustness' (required when robustness.enabled=true)")
    
    top_k = rob["top_k_for_robustness"]
    if not isinstance(top_k, int):
        raise ConfigError(
            f"'robustness.top_k_for_robustness' must be an integer, "
            f"got: {type(top_k).__name__}"
        )
    
    if top_k <= 0:
        raise ConfigError(
            f"'robustness.top_k_for_robustness' must be > 0, got: {top_k}"
        )
    
    # Validate plateau_k_neighbors
    if "plateau_k_neighbors" not in rob:
        raise ConfigError("Missing required key: 'robustness.plateau_k_neighbors' (required when robustness.enabled=true)")
    
    k_neighbors = rob["plateau_k_neighbors"]
    if not isinstance(k_neighbors, int):
        raise ConfigError(
            f"'robustness.plateau_k_neighbors' must be an integer, "
            f"got: {type(k_neighbors).__name__}"
        )
    
    if k_neighbors <= 0:
        raise ConfigError(
            f"'robustness.plateau_k_neighbors' must be > 0, got: {k_neighbors}"
        )
    
    # Validate stress_multipliers
    if "stress_multipliers" not in rob:
        raise ConfigError("Missing required key: 'robustness.stress_multipliers' (required when robustness.enabled=true)")
    
    multipliers = rob["stress_multipliers"]
    if not isinstance(multipliers, list):
        raise ConfigError(
            f"'robustness.stress_multipliers' must be a list, "
            f"got: {type(multipliers).__name__}"
        )
    
    if len(multipliers) < 1:
        raise ConfigError(
            "'robustness.stress_multipliers' must contain at least one value"
        )
    
    for i, m in enumerate(multipliers):
        if not isinstance(m, (int, float)):
            raise ConfigError(
                f"'robustness.stress_multipliers[{i}]' must be a number, "
                f"got: {type(m).__name__}"
            )
        if m <= 0:
            raise ConfigError(
                f"'robustness.stress_multipliers[{i}]' must be > 0, got: {m}"
            )
    
    # Warning if 1.0 is not in multipliers (not an error, just log)
    if 1.0 not in multipliers:
        import warnings
        warnings.warn(
            "robustness.stress_multipliers does not include 1.0. "
            "The base commission (1.0x) will be taken from existing trial metrics.",
            UserWarning
        )
    
    # Validate stress_pass_max_drop_pct
    if "stress_pass_max_drop_pct" not in rob:
        raise ConfigError("Missing required key: 'robustness.stress_pass_max_drop_pct' (required when robustness.enabled=true)")
    
    max_drop = rob["stress_pass_max_drop_pct"]
    if not isinstance(max_drop, (int, float)):
        raise ConfigError(
            f"'robustness.stress_pass_max_drop_pct' must be a number, "
            f"got: {type(max_drop).__name__}"
        )
    
    if max_drop < 0:
        raise ConfigError(
            f"'robustness.stress_pass_max_drop_pct' must be >= 0, got: {max_drop}"
        )
    
    # Validate robust_w1
    if "robust_w1" not in rob:
        raise ConfigError("Missing required key: 'robustness.robust_w1' (required when robustness.enabled=true)")
    
    w1 = rob["robust_w1"]
    if not isinstance(w1, (int, float)):
        raise ConfigError(
            f"'robustness.robust_w1' must be a number, "
            f"got: {type(w1).__name__}"
        )
    
    if w1 < 0:
        raise ConfigError(
            f"'robustness.robust_w1' must be >= 0, got: {w1}"
        )
    
    # Validate robust_w2
    if "robust_w2" not in rob:
        raise ConfigError("Missing required key: 'robustness.robust_w2' (required when robustness.enabled=true)")
    
    w2 = rob["robust_w2"]
    if not isinstance(w2, (int, float)):
        raise ConfigError(
            f"'robustness.robust_w2' must be a number, "
            f"got: {type(w2).__name__}"
        )
    
    if w2 < 0:
        raise ConfigError(
            f"'robustness.robust_w2' must be >= 0, got: {w2}"
        )


def _validate_walk_forward_section(cfg: Dict[str, Any]) -> None:
    """
    Validate Walk-Forward configuration section.
    
    Walk-Forward is optional. If present and enabled, validates required
    parameters, duration strings, and emits warnings for potential issues.
    
    Args:
        cfg: Full configuration dictionary
        
    Raises:
        ConfigError: If Walk-Forward configuration is invalid
    """
    val = cfg.get("validation", {})
    wf = val.get("walk_forward")
    
    # WF section is optional
    if wf is None:
        return
    
    # If section exists but enabled is missing, default to False and return
    if "enabled" not in wf:
        return
    
    enabled = wf.get("enabled", False)
    
    # If disabled, no further validation needed
    if not enabled:
        return
    
    # WF is enabled - validate required keys
    if "train_size" not in wf:
        raise ConfigError("Missing required key: 'validation.walk_forward.train_size' (required when walk_forward.enabled=true)")
    
    if "test_size" not in wf:
        raise ConfigError("Missing required key: 'validation.walk_forward.test_size' (required when walk_forward.enabled=true)")
    
    # Validate duration strings
    train_size = wf["train_size"]
    test_size = wf["test_size"]
    step_size = wf.get("step_size")
    
    # Validate train_size
    if not isinstance(train_size, str):
        raise ConfigError(
            f"'validation.walk_forward.train_size' must be a string, "
            f"got: {type(train_size).__name__}"
        )
    
    try:
        train_value, train_unit = parse_duration_string(train_size)
    except ValueError as e:
        raise ConfigError(
            f"'validation.walk_forward.train_size' has invalid duration format: {e}"
        )
    
    # Validate test_size
    if not isinstance(test_size, str):
        raise ConfigError(
            f"'validation.walk_forward.test_size' must be a string, "
            f"got: {type(test_size).__name__}"
        )
    
    try:
        test_value, test_unit = parse_duration_string(test_size)
    except ValueError as e:
        raise ConfigError(
            f"'validation.walk_forward.test_size' has invalid duration format: {e}"
        )
    
    # Validate step_size if provided
    if step_size is not None:
        if not isinstance(step_size, str):
            raise ConfigError(
                f"'validation.walk_forward.step_size' must be a string, "
                f"got: {type(step_size).__name__}"
            )
        
        try:
            parse_duration_string(step_size)
        except ValueError as e:
            raise ConfigError(
                f"'validation.walk_forward.step_size' has invalid duration format: {e}"
            )
    
    # Validate scheme
    scheme = wf.get("scheme", "rolling")
    if not isinstance(scheme, str):
        raise ConfigError(
            f"'validation.walk_forward.scheme' must be a string, "
            f"got: {type(scheme).__name__}"
        )
    
    if scheme not in ["rolling", "expanding"]:
        raise ConfigError(
            f"'validation.walk_forward.scheme' must be 'rolling' or 'expanding', "
            f"got: '{scheme}'"
        )
    
    # Validate anchor
    anchor = wf.get("anchor", "start")
    if not isinstance(anchor, str):
        raise ConfigError(
            f"'validation.walk_forward.anchor' must be a string, "
            f"got: {type(anchor).__name__}"
        )
    
    if anchor not in ["start", "end"]:
        raise ConfigError(
            f"'validation.walk_forward.anchor' must be 'start' or 'end', "
            f"got: '{anchor}'"
        )
    
    # Validate min_train_bars
    min_train_bars = wf.get("min_train_bars", 500)
    if not isinstance(min_train_bars, int):
        raise ConfigError(
            f"'validation.walk_forward.min_train_bars' must be an integer, "
            f"got: {type(min_train_bars).__name__}"
        )
    
    if min_train_bars < 1:
        raise ConfigError(
            f"'validation.walk_forward.min_train_bars' must be >= 1, "
            f"got: {min_train_bars}"
        )
    
    # Validate min_test_bars
    min_test_bars = wf.get("min_test_bars", 100)
    if not isinstance(min_test_bars, int):
        raise ConfigError(
            f"'validation.walk_forward.min_test_bars' must be an integer, "
            f"got: {type(min_test_bars).__name__}"
        )
    
    if min_test_bars < 1:
        raise ConfigError(
            f"'validation.walk_forward.min_test_bars' must be >= 1, "
            f"got: {min_test_bars}"
        )
    
    # Validate boolean flags
    reoptimize_each_step = wf.get("reoptimize_each_step", True)
    if not isinstance(reoptimize_each_step, bool):
        raise ConfigError(
            f"'validation.walk_forward.reoptimize_each_step' must be a boolean, "
            f"got: {type(reoptimize_each_step).__name__}"
        )
    
    store_all_trials = wf.get("store_all_trials", False)
    if not isinstance(store_all_trials, bool):
        raise ConfigError(
            f"'validation.walk_forward.store_all_trials' must be a boolean, "
            f"got: {type(store_all_trials).__name__}"
        )
    
    early_exit_train = wf.get("early_exit_train", True)
    if not isinstance(early_exit_train, bool):
        raise ConfigError(
            f"'validation.walk_forward.early_exit_train' must be a boolean, "
            f"got: {type(early_exit_train).__name__}"
        )
    
    early_exit_test = wf.get("early_exit_test", False)
    if not isinstance(early_exit_test, bool):
        raise ConfigError(
            f"'validation.walk_forward.early_exit_test' must be a boolean, "
            f"got: {type(early_exit_test).__name__}"
        )
    
    # Warning: early_exit_test may truncate OOS windows
    if early_exit_test:
        warnings.warn(
            "validation.walk_forward.early_exit_test=True: OOS test windows may be truncated "
            "if early exit is triggered, which can affect aggregated metrics.",
            UserWarning,
            stacklevel=2
        )
    
    # Validate oos_early_exit_enabled (optional, defaults to backtest.early_exit_enabled)
    oos_early_exit = wf.get("oos_early_exit_enabled")
    if oos_early_exit is not None and not isinstance(oos_early_exit, bool):
        raise ConfigError(
            f"'validation.walk_forward.oos_early_exit_enabled' must be a boolean, "
            f"got: {type(oos_early_exit).__name__}"
        )
    
    # Warning: oos_early_exit_enabled=True may cause identical max_dd for similar strategies
    if oos_early_exit is True:
        warnings.warn(
            "validation.walk_forward.oos_early_exit_enabled=True: OOS metrics may be distorted "
            "if early exit triggers at the same bar for multiple parameter sets. "
            "Recommended: set to false for accurate OOS evaluation.",
            UserWarning,
            stacklevel=2
        )
    
    # Warning: train_size too close to warmup (only for bar-based)
    if train_unit == "bars":
        # Estimate effective warmup
        warmup_period = val.get("warmup_period", 0)
        warmup_auto = val.get("warmup_period_auto", False)
        
        # Get max ATR period from optimization range
        opt = cfg.get("optimization", {})
        atr_range = opt.get("atr_period_range", [5, 60])
        max_atr = atr_range[1] if isinstance(atr_range, (list, tuple)) and len(atr_range) >= 2 else 60
        
        if warmup_auto:
            effective_warmup = max(warmup_period, max_atr)
        else:
            effective_warmup = warmup_period
        
        # Warn if train_size is less than 2x effective warmup
        if train_value < 2 * effective_warmup:
            warnings.warn(
                f"validation.walk_forward.train_size ({train_size}) is close to or smaller than "
                f"2x effective warmup ({2 * effective_warmup} bars). This may result in very few "
                f"bars available for ratio metrics calculation after warmup is applied.",
                UserWarning,
                stacklevel=2
            )
    
    # Validate OOS gates (optional)
    gates = wf.get("oos_gates")
    if gates is not None:
        _validate_oos_gates_section(gates, cfg)


def _validate_oos_gates_section(gates: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    """
    Validate OOS gates configuration.
    
    Args:
        gates: OOS gates configuration dictionary
        cfg: Full configuration dictionary
        
    Raises:
        ConfigError: If gates configuration is invalid
    """
    # If enabled is missing, default to False and return
    if "enabled" not in gates:
        return
    
    enabled = gates.get("enabled", False)
    
    # If disabled, no further validation needed
    if not enabled:
        return
    
    # Validate min_trades
    min_trades = gates.get("min_trades")
    if min_trades is not None:
        if not isinstance(min_trades, int):
            raise ConfigError(
                f"'validation.walk_forward.oos_gates.min_trades' must be an integer, "
                f"got: {type(min_trades).__name__}"
            )
        
        if min_trades < 0:
            raise ConfigError(
                f"'validation.walk_forward.oos_gates.min_trades' must be >= 0, "
                f"got: {min_trades}"
            )
    
    # Validate max_fail_steps
    max_fail_steps = gates.get("max_fail_steps")
    if max_fail_steps is not None:
        if not isinstance(max_fail_steps, int):
            raise ConfigError(
                f"'validation.walk_forward.oos_gates.max_fail_steps' must be an integer, "
                f"got: {type(max_fail_steps).__name__}"
            )
        
        if max_fail_steps < 0:
            raise ConfigError(
                f"'validation.walk_forward.oos_gates.max_fail_steps' must be >= 0, "
                f"got: {max_fail_steps}"
            )
    
    # Validate float thresholds (can be None)
    float_thresholds = [
        "min_sortino",
        "min_sharpe",
        "min_sum_pnl_pct",
        "max_drawdown",
        "min_profit_factor",
        "step_min_sortino",
        "step_min_sum_pnl_pct"
    ]
    
    for key in float_thresholds:
        value = gates.get(key)
        if value is not None:
            if not isinstance(value, (int, float)):
                raise ConfigError(
                    f"'validation.walk_forward.oos_gates.{key}' must be a number or null, "
                    f"got: {type(value).__name__}"
                )
    
    # Validate max_drawdown is positive
    max_drawdown = gates.get("max_drawdown")
    if max_drawdown is not None and max_drawdown <= 0:
        raise ConfigError(
            f"'validation.walk_forward.oos_gates.max_drawdown' must be > 0 (percentage), "
            f"got: {max_drawdown}"
        )
    
    # Warning if step gates are specified
    if gates.get("step_min_sortino") is not None or gates.get("step_min_sum_pnl_pct") is not None:
        warnings.warn(
            "validation.walk_forward.oos_gates: per-step gates (step_min_*) are enabled. "
            "Ensure you have enough windows for meaningful validation.",
            UserWarning,
            stacklevel=3
        )


def _validate_consensus_section(consensus: Dict[str, Any]) -> None:
    """
    Validate Walk-Forward consensus configuration.
    
    Args:
        consensus: Consensus configuration dictionary
        
    Raises:
        ConfigError: If consensus configuration is invalid
    """
    # Validate mode
    mode = consensus.get("mode", "exact")
    if not isinstance(mode, str):
        raise ConfigError(
            f"'walk_forward.consensus.mode' must be a string, "
            f"got: {type(mode).__name__}"
        )
    
    if mode not in ["exact", "bucket"]:
        raise ConfigError(
            f"'walk_forward.consensus.mode' must be 'exact' or 'bucket', "
            f"got: '{mode}'"
        )
    
    # Validate min_coverage
    min_coverage = consensus.get("min_coverage", 1)
    if not isinstance(min_coverage, int):
        raise ConfigError(
            f"'walk_forward.consensus.min_coverage' must be an integer, "
            f"got: {type(min_coverage).__name__}"
        )
    
    if min_coverage < 1:
        raise ConfigError(
            f"'walk_forward.consensus.min_coverage' must be >= 1, got: {min_coverage}"
        )
    
    # Validate atr_bucket_step
    atr_bucket_step = consensus.get("atr_bucket_step", DEFAULT_ATR_BUCKET_STEP)
    if not isinstance(atr_bucket_step, int):
        raise ConfigError(
            f"'walk_forward.consensus.atr_bucket_step' must be an integer, "
            f"got: {type(atr_bucket_step).__name__}"
        )
    
    if atr_bucket_step < 1:
        raise ConfigError(
            f"'walk_forward.consensus.atr_bucket_step' must be >= 1, got: {atr_bucket_step}"
        )
    
    # Validate mult_bucket_step
    mult_bucket_step = consensus.get("mult_bucket_step", DEFAULT_MULT_BUCKET_STEP)
    if not isinstance(mult_bucket_step, (int, float)):
        raise ConfigError(
            f"'walk_forward.consensus.mult_bucket_step' must be a number, "
            f"got: {type(mult_bucket_step).__name__}"
        )
    
    if mult_bucket_step <= 0:
        raise ConfigError(
            f"'walk_forward.consensus.mult_bucket_step' must be > 0, got: {mult_bucket_step}"
        )
    
    if mult_bucket_step > 1.0:
        raise ConfigError(
            f"'walk_forward.consensus.mult_bucket_step' must be <= 1.0, got: {mult_bucket_step}"
        )

    # Validate min_buckets_for_median
    min_buckets_for_median = consensus.get("min_buckets_for_median", 5)
    if not isinstance(min_buckets_for_median, int):
        raise ConfigError(
            f"'walk_forward.consensus.min_buckets_for_median' must be an integer, "
            f"got: {type(min_buckets_for_median).__name__}"
        )
    if min_buckets_for_median < 1:
        raise ConfigError(
            f"'walk_forward.consensus.min_buckets_for_median' must be >= 1, "
            f"got: {min_buckets_for_median}"
        )


def _validate_walk_forward_top_k_section(cfg: Dict[str, Any]) -> None:
    """
    Validate Walk-Forward TOP-K parameters.
    
    These parameters control how many parameters are exported to Excel
    and how many participate in consensus calculation.
    
    Parameters are read from config["walk_forward"] (top-level section),
    NOT from config["validation"]["walk_forward"].
    
    Args:
        cfg: Full configuration dictionary
        
    Raises:
        ConfigError: If TOP-K configuration is invalid
    """
    # Check if walk_forward section exists at top level
    wf_top = cfg.get("walk_forward")
    
    # Section is optional - if missing, use defaults
    if wf_top is None:
        return
    
    # Default values
    DEFAULT_TOP_K_EXPORT = 50
    DEFAULT_TOP_K_CONSENSUS = 10
    
    # Get values with defaults
    top_k_export = wf_top.get("top_k_export", DEFAULT_TOP_K_EXPORT)
    top_k_consensus = wf_top.get("top_k_consensus", DEFAULT_TOP_K_CONSENSUS)
    
    # Validate top_k_export
    if not isinstance(top_k_export, int):
        raise ConfigError(
            f"'walk_forward.top_k_export' must be an integer, "
            f"got: {type(top_k_export).__name__}"
        )
    
    if top_k_export < 1:
        raise ConfigError(
            f"'walk_forward.top_k_export' must be >= 1, got: {top_k_export}"
        )
    
    # Validate top_k_consensus
    if not isinstance(top_k_consensus, int):
        raise ConfigError(
            f"'walk_forward.top_k_consensus' must be an integer, "
            f"got: {type(top_k_consensus).__name__}"
        )
    
    if top_k_consensus < 1:
        raise ConfigError(
            f"'walk_forward.top_k_consensus' must be >= 1, got: {top_k_consensus}"
        )
    
    # Validate relationship: consensus <= export
    if top_k_consensus > top_k_export:
        raise ConfigError(
            f"'walk_forward.top_k_consensus' ({top_k_consensus}) must be <= "
            f"'walk_forward.top_k_export' ({top_k_export})"
        )
    
    # Validate consensus section (optional)
    consensus = wf_top.get("consensus")
    if consensus is not None:
        _validate_consensus_section(consensus)


def _validate_mini_grid_config(cfg: Dict[str, Any]) -> None:
    """
    Validate mini_grid configuration (Phase 1).
    
    Mini-grid is an optional mode for Walk-Forward optimization that uses
    a fixed parameter grid on steps 1+ instead of running Optuna on each step.
    
    Args:
        cfg: Full configuration dictionary
        
    Raises:
        ConfigError: If mini_grid configuration is invalid
    """
    import math
    
    # Check if walk_forward section exists at top level
    wf_top = cfg.get("walk_forward")
    
    # Section is optional
    if wf_top is None:
        return
    
    # Check if mini_grid subsection exists
    mg_cfg = wf_top.get("mini_grid")
    
    # Subsection is optional
    if mg_cfg is None:
        return
    
    # Check if enabled
    enabled = mg_cfg.get("enabled", False)
    
    # If not enabled, skip validation
    if not enabled:
        return
    
    # === Mini-grid is enabled - validate all parameters ===
    
    # Get optimization config for mult_step check
    opt_cfg = cfg.get("optimization", {})
    opt_mult_step = opt_cfg.get("multiplier_step", 0.1)
    
    # 1. Validate mult_step consistency (ПРАВКА: mult_step должен совпадать)
    if "mult_step" in mg_cfg:
        mg_mult_step = mg_cfg["mult_step"]
        
        if not isinstance(mg_mult_step, (int, float)):
            raise ConfigError(
                f"'walk_forward.mini_grid.mult_step' must be a number, "
                f"got: {type(mg_mult_step).__name__}"
            )
        
        if mg_mult_step <= 0:
            raise ConfigError(
                f"'walk_forward.mini_grid.mult_step' must be > 0, got: {mg_mult_step}"
            )
        
        # Check consistency with optimization.multiplier_step
        if abs(mg_mult_step - opt_mult_step) > 1e-9:
            raise ConfigError(
                f"'walk_forward.mini_grid.mult_step' ({mg_mult_step}) must equal "
                f"'optimization.multiplier_step' ({opt_mult_step}). "
                f"Either remove 'mini_grid.mult_step' to inherit automatically, "
                f"or ensure both values match."
            )
    
    # 2. Validate top_n_seeds
    top_n_seeds = mg_cfg.get("top_n_seeds", 15)
    if not isinstance(top_n_seeds, int):
        raise ConfigError(
            f"'walk_forward.mini_grid.top_n_seeds' must be an integer, "
            f"got: {type(top_n_seeds).__name__}"
        )
    
    if top_n_seeds < 1:
        raise ConfigError(
            f"'walk_forward.mini_grid.top_n_seeds' must be >= 1, got: {top_n_seeds}"
        )
    
    # 3. Validate atr_delta
    atr_delta = mg_cfg.get("atr_delta", 3)
    if not isinstance(atr_delta, int):
        raise ConfigError(
            f"'walk_forward.mini_grid.atr_delta' must be an integer, "
            f"got: {type(atr_delta).__name__}"
        )
    
    if atr_delta < 0:
        raise ConfigError(
            f"'walk_forward.mini_grid.atr_delta' must be >= 0, got: {atr_delta}"
        )
    
    # 4. Validate mult_delta
    mult_delta = mg_cfg.get("mult_delta", 0.3)
    if not isinstance(mult_delta, (int, float)):
        raise ConfigError(
            f"'walk_forward.mini_grid.mult_delta' must be a number, "
            f"got: {type(mult_delta).__name__}"
        )
    
    if mult_delta < 0:
        raise ConfigError(
            f"'walk_forward.mini_grid.mult_delta' must be >= 0, got: {mult_delta}"
        )
    
    # ПРАВКА 3: Warning if mult_delta not mult_step-aligned
    effective_mult_step = mg_cfg.get("mult_step", opt_mult_step)
    if mult_delta > 0 and effective_mult_step > 0:
        steps_count = mult_delta / effective_mult_step
        ceiled_steps = math.ceil(steps_count)
        
        if abs(steps_count - ceiled_steps) > 1e-9:
            # Not aligned - will be rounded up
            actual_delta = ceiled_steps * effective_mult_step
            warnings.warn(
                f"walk_forward.mini_grid.mult_delta ({mult_delta}) is not aligned with "
                f"mult_step ({effective_mult_step}). It will be rounded up to {actual_delta:.2f} "
                f"for full coverage ({ceiled_steps} steps).",
                UserWarning,
                stacklevel=2
            )
    
    # 5. Validate atr_step
    atr_step = mg_cfg.get("atr_step", 1)
    if not isinstance(atr_step, int):
        raise ConfigError(
            f"'walk_forward.mini_grid.atr_step' must be an integer, "
            f"got: {type(atr_step).__name__}"
        )
    
    if atr_step < 1:
        raise ConfigError(
            f"'walk_forward.mini_grid.atr_step' must be >= 1, got: {atr_step}"
        )
    
    # 6. Validate clamp_to_search_space
    clamp = mg_cfg.get("clamp_to_search_space", True)
    if not isinstance(clamp, bool):
        raise ConfigError(
            f"'walk_forward.mini_grid.clamp_to_search_space' must be a boolean, "
            f"got: {type(clamp).__name__}"
        )
    
    # 7. Validate max_grid_size
    max_grid_size = mg_cfg.get("max_grid_size")
    if max_grid_size is not None:
        if not isinstance(max_grid_size, int):
            raise ConfigError(
                f"'walk_forward.mini_grid.max_grid_size' must be an integer or null, "
                f"got: {type(max_grid_size).__name__}"
            )
        
        if max_grid_size < 1:
            raise ConfigError(
                f"'walk_forward.mini_grid.max_grid_size' must be >= 1 or null, "
                f"got: {max_grid_size}"
            )
    
    # 8. Validate truncate_policy
    truncate_policy = mg_cfg.get("truncate_policy", "best_seed_priority")
    if not isinstance(truncate_policy, str):
        raise ConfigError(
            f"'walk_forward.mini_grid.truncate_policy' must be a string, "
            f"got: {type(truncate_policy).__name__}"
        )
    
    valid_policies = ["best_seed_priority", "center_priority"]
    if truncate_policy not in valid_policies:
        raise ConfigError(
            f"'walk_forward.mini_grid.truncate_policy' must be one of {valid_policies}, "
            f"got: '{truncate_policy}'"
        )
    
    # 9. Validate extract_trades
    extract_trades = mg_cfg.get("extract_trades", True)
    if not isinstance(extract_trades, bool):
        raise ConfigError(
            f"'walk_forward.mini_grid.extract_trades' must be a boolean, "
            f"got: {type(extract_trades).__name__}"
        )

    # 10. Validate top_m_per_step (Phase 1, optional)
    top_m_per_step = mg_cfg.get("top_m_per_step")
    if top_m_per_step is not None:
        if not isinstance(top_m_per_step, int):
            raise ConfigError(
                f"'walk_forward.mini_grid.top_m_per_step' must be a positive integer or null, "
                f"got: {type(top_m_per_step).__name__}"
            )
        if top_m_per_step < 1:
            raise ConfigError(
                f"'walk_forward.mini_grid.top_m_per_step' must be >= 1 or null, "
                f"got: {top_m_per_step}"
            )

    # 11. Validate plateau section (optional)
    plateau_cfg = mg_cfg.get("plateau")
    if plateau_cfg is not None:
        if not isinstance(plateau_cfg, dict):
            raise ConfigError(
                f"'walk_forward.mini_grid.plateau' must be a dict, "
                f"got: {type(plateau_cfg).__name__}"
            )
        plateau_enabled = plateau_cfg.get("enabled", False)
        if not isinstance(plateau_enabled, bool):
            raise ConfigError(
                f"'walk_forward.mini_grid.plateau.enabled' must be a boolean, "
                f"got: {type(plateau_enabled).__name__}"
            )
        if plateau_enabled:
            threshold = plateau_cfg.get("threshold", 0.95)
            if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
                raise ConfigError(
                    f"'walk_forward.mini_grid.plateau.threshold' must be a number, "
                    f"got: {type(threshold).__name__}"
                )
            if threshold < 0.8 or threshold > 1.0:
                raise ConfigError(
                    f"'walk_forward.mini_grid.plateau.threshold' must be in [0.8, 1.0], "
                    f"got: {threshold}"
                )

    # 12. Validate discovery section (Phase 2, optional)
    _valid_disc_modes = ("first_only", "first_k", "explicit", "all")
    disc_cfg = mg_cfg.get("discovery", {})
    if disc_cfg:
        disc_mode = disc_cfg.get("mode", "first_only")
        if not isinstance(disc_mode, str):
            raise ConfigError(
                f"'walk_forward.mini_grid.discovery.mode' must be a string, "
                f"got: {type(disc_mode).__name__}"
            )
        if disc_mode not in _valid_disc_modes:
            raise ConfigError(
                f"'walk_forward.mini_grid.discovery.mode' must be one of "
                f"{_valid_disc_modes}, got: '{disc_mode}'"
            )

        first_k = disc_cfg.get("first_k", 3)
        if not isinstance(first_k, int) or first_k < 1:
            raise ConfigError(
                f"'walk_forward.mini_grid.discovery.first_k' must be a positive integer, "
                f"got: {first_k!r}"
            )

        explicit_steps = disc_cfg.get("steps", [])
        if not isinstance(explicit_steps, list):
            raise ConfigError(
                f"'walk_forward.mini_grid.discovery.steps' must be a list, "
                f"got: {type(explicit_steps).__name__}"
            )
        for i, s in enumerate(explicit_steps):
            if not isinstance(s, int) or s < 0:
                raise ConfigError(
                    f"'walk_forward.mini_grid.discovery.steps[{i}]' must be a non-negative integer, "
                    f"got: {s!r}"
                )

        normalize_score = disc_cfg.get("normalize_score", False)
        if not isinstance(normalize_score, bool):
            raise ConfigError(
                f"'walk_forward.mini_grid.discovery.normalize_score' must be a boolean, "
                f"got: {type(normalize_score).__name__}"
            )

        min_appear_count = disc_cfg.get("min_appear_count", None)
        if min_appear_count is not None:
            if not isinstance(min_appear_count, int):
                raise ConfigError(
                    f"'walk_forward.mini_grid.discovery.min_appear_count' must be a positive "
                    f"integer or null, got: {type(min_appear_count).__name__}"
                )
            if min_appear_count < 1:
                raise ConfigError(
                    f"'walk_forward.mini_grid.discovery.min_appear_count' must be >= 1 or null, "
                    f"got: {min_appear_count}"
                )


_MINI_GRID_REFINE_KNOWN_KEYS = frozenset({
    "enabled",
    "atr_radius",
    "mult_radius",
    "max_grid_points",
    "merge_with_optuna",
    "plateau_threshold_pct",
    "seed_source",
    "metric_for_plateau",
})


def _validate_mini_grid_refine_config(cfg: Dict[str, Any]) -> None:
    """
    Validate mini_grid_refine configuration (Stage 2).

    mini_grid_refine is an optional Stage 2 refine pass that builds a dense
    grid around the best seed from Stage 1 (Optuna or mini-grid eval).

    Config path: config["walk_forward"]["mini_grid_refine"]
    Note: this section lives under the root-level "walk_forward" key,
    NOT under "validation.walk_forward".

    When enabled=false (default) the section is ignored entirely and
    walk-forward behaviour is 100% identical to before Stage 2.

    Args:
        cfg: Full configuration dictionary

    Raises:
        ConfigError: If mini_grid_refine configuration is invalid
    """
    wf_top = cfg.get("walk_forward")
    if wf_top is None:
        return

    refine_cfg = wf_top.get("mini_grid_refine")
    if refine_cfg is None:
        return

    # Warn on unknown keys to catch silent typos (e.g. "plateu_threshold_pct")
    unknown_keys = set(refine_cfg.keys()) - _MINI_GRID_REFINE_KNOWN_KEYS
    if unknown_keys:
        warnings.warn(
            f"walk_forward.mini_grid_refine contains unknown key(s): "
            f"{sorted(unknown_keys)}. "
            f"Known keys: {sorted(_MINI_GRID_REFINE_KNOWN_KEYS)}. "
            f"Check for typos.",
            UserWarning,
            stacklevel=2,
        )

    # enabled
    enabled = refine_cfg.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.enabled' must be a boolean, "
            f"got: {type(enabled).__name__}"
        )

    # If not enabled, skip further validation — behaviour is 100% unchanged
    if not enabled:
        return

    # atr_radius — must be a non-negative integer.
    # YAML sometimes deserialises "3" as int and "3.0" as float; we keep
    # strict int here since ATR period steps are always whole numbers.
    atr_radius = refine_cfg.get("atr_radius", 3)
    if not isinstance(atr_radius, int) or isinstance(atr_radius, bool):
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.atr_radius' must be an integer, "
            f"got: {type(atr_radius).__name__}"
        )
    if atr_radius < 0:
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.atr_radius' must be >= 0, got: {atr_radius}"
        )

    # mult_radius — float (or int) >= 0, matching multiplier_step granularity.
    mult_radius = refine_cfg.get("mult_radius", 0.3)
    if isinstance(mult_radius, bool) or not isinstance(mult_radius, (int, float)):
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.mult_radius' must be a number, "
            f"got: {type(mult_radius).__name__}"
        )
    if mult_radius < 0:
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.mult_radius' must be >= 0, got: {mult_radius}"
        )

    # max_grid_points
    max_grid_points = refine_cfg.get("max_grid_points", 150)
    if not isinstance(max_grid_points, int) or isinstance(max_grid_points, bool):
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.max_grid_points' must be an integer, "
            f"got: {type(max_grid_points).__name__}"
        )
    if max_grid_points < 1:
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.max_grid_points' must be >= 1, "
            f"got: {max_grid_points}"
        )

    # merge_with_optuna
    merge_with_optuna = refine_cfg.get("merge_with_optuna", True)
    if not isinstance(merge_with_optuna, bool):
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.merge_with_optuna' must be a boolean, "
            f"got: {type(merge_with_optuna).__name__}"
        )

    # plateau_threshold_pct
    threshold_pct = refine_cfg.get("plateau_threshold_pct", 10)
    if isinstance(threshold_pct, bool) or not isinstance(threshold_pct, (int, float)):
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.plateau_threshold_pct' must be a number, "
            f"got: {type(threshold_pct).__name__}"
        )
    if threshold_pct < 0 or threshold_pct > 100:
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.plateau_threshold_pct' must be in [0, 100], "
            f"got: {threshold_pct}"
        )

    # seed_source
    seed_source = refine_cfg.get("seed_source", "optuna")
    if not isinstance(seed_source, str):
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.seed_source' must be a string, "
            f"got: {type(seed_source).__name__}"
        )
    valid_seed_sources = ("optuna", "refined")
    if seed_source not in valid_seed_sources:
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.seed_source' must be one of "
            f"{valid_seed_sources}, got: '{seed_source}'"
        )

    # metric_for_plateau
    metric_for_plateau = refine_cfg.get("metric_for_plateau", "objective")
    if not isinstance(metric_for_plateau, str):
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.metric_for_plateau' must be a string, "
            f"got: {type(metric_for_plateau).__name__}"
        )
    valid_plateau_metrics = ("objective", "ranking")
    if metric_for_plateau not in valid_plateau_metrics:
        raise ConfigError(
            f"'walk_forward.mini_grid_refine.metric_for_plateau' must be one of "
            f"{valid_plateau_metrics}, got: '{metric_for_plateau}'"
        )


def _validate_canonical_ranking_section(cfg: Dict[str, Any]) -> None:
    """
    Validate walk_forward.canonical.ranking configuration.

    Section is optional. If absent, defaults apply (mode=classic).
    If mode=regime_robust, the regime sub-dict must be present and valid.

    Args:
        cfg: Full configuration dictionary

    Raises:
        ConfigError: If ranking configuration is invalid
    """
    wf = cfg.get("walk_forward")
    if wf is None:
        return

    canonical = wf.get("canonical")
    if canonical is None:
        return

    ranking = canonical.get("ranking")
    if ranking is None:
        return

    # mode
    mode = ranking.get("mode", "classic")
    if not isinstance(mode, str):
        raise ConfigError(
            f"'walk_forward.canonical.ranking.mode' must be a string, "
            f"got: {type(mode).__name__}"
        )
    valid_modes = ("classic", "regime_robust")
    if mode not in valid_modes:
        raise ConfigError(
            f"'walk_forward.canonical.ranking.mode' must be one of "
            f"{valid_modes}, got: '{mode}'"
        )

    if mode != "regime_robust":
        return

    # regime sub-dict required when mode=regime_robust
    regime = ranking.get("regime")
    if regime is None:
        raise ConfigError(
            "'walk_forward.canonical.ranking.regime' sub-dict is required "
            "when mode='regime_robust'"
        )

    # alpha: (int|float), > 0
    alpha = regime.get("alpha", 2)
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.alpha' must be a number, "
            f"got: {type(alpha).__name__}"
        )
    if alpha <= 0:
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.alpha' must be > 0, "
            f"got: {alpha}"
        )

    # lambda: (int|float), >= 0
    lambda_ = regime.get("lambda", 0.004)
    if isinstance(lambda_, bool) or not isinstance(lambda_, (int, float)):
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.lambda' must be a number, "
            f"got: {type(lambda_).__name__}"
        )
    if lambda_ < 0:
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.lambda' must be >= 0, "
            f"got: {lambda_}"
        )

    # mu: (int|float), >= 0
    mu = regime.get("mu", 0.015)
    if isinstance(mu, bool) or not isinstance(mu, (int, float)):
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.mu' must be a number, "
            f"got: {type(mu).__name__}"
        )
    if mu < 0:
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.mu' must be >= 0, "
            f"got: {mu}"
        )

    # nu: (int|float), >= 0
    nu = regime.get("nu", 0.015)
    if isinstance(nu, bool) or not isinstance(nu, (int, float)):
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.nu' must be a number, "
            f"got: {type(nu).__name__}"
        )
    if nu < 0:
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.nu' must be >= 0, "
            f"got: {nu}"
        )

    # missing_oos_policy: str, one of two values
    policy = regime.get("missing_oos_policy", "penalize_as_floor")
    if not isinstance(policy, str):
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.missing_oos_policy' must be a string, "
            f"got: {type(policy).__name__}"
        )
    valid_policies = ("penalize_as_floor", "penalize_only_missing_regimes")
    if policy not in valid_policies:
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.missing_oos_policy' must be one of "
            f"{valid_policies}, got: '{policy}'"
        )

    # oos_floor_pnl: (int|float), <= 0
    oos_floor = regime.get("oos_floor_pnl", -30.0)
    if isinstance(oos_floor, bool) or not isinstance(oos_floor, (int, float)):
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.oos_floor_pnl' must be a number, "
            f"got: {type(oos_floor).__name__}"
        )
    if oos_floor > 0:
        raise ConfigError(
            f"'walk_forward.canonical.ranking.regime.oos_floor_pnl' must be <= 0, "
            f"got: {oos_floor}"
        )


def _validate_canonical_soft_pnl_gate_section(cfg: Dict[str, Any]) -> None:
    """
    Validate walk_forward.canonical.gates soft PnL gate configuration.

    All soft gate keys are optional. Validation only runs when
    soft_pnl_gate_enabled is present and true.

    Raises:
        ConfigError: If soft gate configuration is invalid
    """
    wf = cfg.get("walk_forward")
    if wf is None:
        return

    canonical = wf.get("canonical")
    if canonical is None:
        return

    gates = canonical.get("gates")
    if gates is None:
        return

    enabled = gates.get("soft_pnl_gate_enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError(
            f"'walk_forward.canonical.gates.soft_pnl_gate_enabled' must be a boolean, "
            f"got: {type(enabled).__name__}"
        )

    if not enabled:
        return

    # soft_pnl_gate_mode
    mode = gates.get("soft_pnl_gate_mode", "allow_n_violations")
    if not isinstance(mode, str):
        raise ConfigError(
            f"'walk_forward.canonical.gates.soft_pnl_gate_mode' must be a string, "
            f"got: {type(mode).__name__}"
        )
    valid_modes = ("allow_n_violations", "percentile")
    if mode not in valid_modes:
        raise ConfigError(
            f"'walk_forward.canonical.gates.soft_pnl_gate_mode' must be one of "
            f"{valid_modes}, got: '{mode}'"
        )

    # soft_pnl_gate_max_violations
    max_v = gates.get("soft_pnl_gate_max_violations", 1)
    if isinstance(max_v, bool) or not isinstance(max_v, int):
        raise ConfigError(
            f"'walk_forward.canonical.gates.soft_pnl_gate_max_violations' must be an integer, "
            f"got: {type(max_v).__name__}"
        )
    if max_v < 0:
        raise ConfigError(
            f"'walk_forward.canonical.gates.soft_pnl_gate_max_violations' must be >= 0, "
            f"got: {max_v}"
        )

    # soft_pnl_gate_percentile
    percentile = gates.get("soft_pnl_gate_percentile", 0.25)
    if isinstance(percentile, bool) or not isinstance(percentile, (int, float)):
        raise ConfigError(
            f"'walk_forward.canonical.gates.soft_pnl_gate_percentile' must be a number, "
            f"got: {type(percentile).__name__}"
        )
    if not (0.0 < float(percentile) < 0.5):
        raise ConfigError(
            f"'walk_forward.canonical.gates.soft_pnl_gate_percentile' must be in (0, 0.5), "
            f"got: {percentile}"
        )


def _validate_plateau_analysis_section(cfg: Dict[str, Any]) -> None:
    """Validate plateau_analysis configuration section.

    Section is optional.  Only keys that are explicitly present in the YAML
    are validated; absent keys fall back to PlateauAnalysisConfig defaults.
    Fail fast on out-of-range values so no silent degradation reaches runtime.

    Raises:
        ConfigError: If any present key has an invalid value.
    """
    pa = cfg.get("plateau_analysis")
    if pa is None:
        return

    # ── dd_quality_penalty_floor ∈ [0.0, 1.0] ────────────────────────────
    if "dd_quality_penalty_floor" in pa:
        v = pa["dd_quality_penalty_floor"]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigError(
                f"'plateau_analysis.dd_quality_penalty_floor' must be a number, "
                f"got: {type(v).__name__}"
            )
        if not (0.0 <= float(v) <= 1.0):
            raise ConfigError(
                f"'plateau_analysis.dd_quality_penalty_floor' must be in [0.0, 1.0], "
                f"got: {v}"
            )

    # ── dd_quality_hard_cap ∈ [0.0, 1.0] ────────────────────────────────
    if "dd_quality_hard_cap" in pa:
        v = pa["dd_quality_hard_cap"]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigError(
                f"'plateau_analysis.dd_quality_hard_cap' must be a number, "
                f"got: {type(v).__name__}"
            )
        if not (0.0 <= float(v) <= 1.0):
            raise ConfigError(
                f"'plateau_analysis.dd_quality_hard_cap' must be in [0.0, 1.0], "
                f"got: {v}"
            )

    # ── dd_quality_min_observed — int >= 1 ───────────────────────────────
    if "dd_quality_min_observed" in pa:
        v = pa["dd_quality_min_observed"]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ConfigError(
                f"'plateau_analysis.dd_quality_min_observed' must be an integer, "
                f"got: {type(v).__name__}"
            )
        if v < 1:
            raise ConfigError(
                f"'plateau_analysis.dd_quality_min_observed' must be >= 1, "
                f"got: {v}"
            )

