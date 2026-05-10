"""
CLI entrypoint for SuperTrend Tester.

Usage:
    python -m supertrend_optimizer.cli.tester --csv data.csv --atr 14 --mult 3.0 --mode revers --out result.xlsx
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

from supertrend_optimizer.data.loader import load_ohlc_csv
from supertrend_optimizer.data.validator import validate_ohlc_data, validate_volume_filter_data
from supertrend_optimizer.data.timeframe import (
    coerce_annualization_config_value,
    detect_timeframe,
    resolve_periods_per_year_from_config,
    validate_market_vs_timeframe,
)
from supertrend_optimizer.io.excel_tester import export_tester_results, export_equal_blocks_results
from supertrend_optimizer.testing.runner import run_all_periods, run_equal_blocks
from supertrend_optimizer.testing.signal_events import build_signal_events
from supertrend_optimizer.utils.config import load_config
from supertrend_optimizer.utils.enums import MarketType, ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError, DataValidationError
from supertrend_optimizer.utils.warmup import calculate_warmup_tester

# Phase 2 (WP-T2): trade_filter contract is owned by the active donor module.
# This is the SINGLE permitted import path for tester (plan §5.1, owner decision
# v0.5.1 §15 #1 — variant (a)). NEVER import TradeFilter* from wf_grid.config.
from supertrend_optimizer.core.trade_filter_config import (
    TradeFilterConfig,
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    collect_trade_filter_unknown_keys,
    is_trade_filter_enabled,
    is_volume_enabled,
    is_zigzag_enabled,
    resolve_zigzag_enabled_in_place,
    resolve_volume_enabled_in_place,
    resolve_trade_filter_mode_in_place,
    resolve_exit_off_mode_in_place,
    resolve_exit_b_immediate_off_in_place,
    resolve_time_filter_in_place,
    resolve_volume_defaults_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.core.volume_metrics import (
    _warn_if_volume_baseline_window_large,
    build_volume_global_metrics,
)

# Phase 2 (WP-T4): ZigZag global stats materialisation for legacy CLI path.
# Plan §7.1 — single build_zigzag_global_stats call BEFORE run_all_periods,
# reused across all 5 legacy slices.  Equal-blocks path never reaches this
# (config gate rejects enabled filter + equal_blocks before data loading).
from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats

# Legacy Excel export: trades with bars_held < N appear on the ``false start`` sheet.
DEFAULT_FALSE_START_MAX_BARS = 4


_ALLOWED_TESTER_ROOT_KEYS = frozenset({
    "annualization_basis",
    "annualization_factor",
    "commission",
    "early_exit",
    "execution_model",
    "export",
    "market",
    "min_trades_required",
    "periods_per_year",
    "segmentation",
    "supertrend",
    "trade_filter",
    "trade_mode",
    "warmup_period",
    "warmup_period_auto",
})


def _validate_false_start_max_bars(raw: Any) -> int:
    """
    Parse ``export.false_start_max_bars`` from YAML.

    Raises:
        ConfigError: if the value is not an integer >= 1.
    """
    if raw is None:
        raise ConfigError(
            "export.false_start_max_bars must be an integer >= 1, got: null"
        )
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ConfigError(
            "export.false_start_max_bars must be an integer >= 1, "
            f"got type {type(raw).__name__!r} with value {raw!r}"
        )
    if raw < 1:
        raise ConfigError(
            f"export.false_start_max_bars must be an integer >= 1, got: {raw!r}"
        )
    return raw


def _merge_export_config(loaded_config: Dict[str, Any], config: Dict[str, Any]) -> None:
    """Update ``config['export']`` from ``loaded_config['export']`` (in-place)."""
    export_raw = loaded_config.get("export")
    if export_raw is None:
        return
    if not isinstance(export_raw, dict):
        raise ConfigError(
            "export must be a mapping (e.g. {false_start_max_bars: 4}), "
            f"got {type(export_raw).__name__!r}"
        )
    if "false_start_max_bars" in export_raw:
        config["export"]["false_start_max_bars"] = _validate_false_start_max_bars(
            export_raw["false_start_max_bars"]
        )


def parse_args(args=None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="SuperTrend Tester - backtest with fixed parameters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m supertrend_optimizer.cli.tester --csv data.csv --atr 14 --mult 3.0 --mode revers --out result.xlsx
  python -m supertrend_optimizer.cli.tester --csv data.csv --atr 10 --mult 2.5 --mode long --config config.yaml --out result.xlsx
        """
    )
    
    parser.add_argument(
        "--csv",
        type=str,
        required=True,
        help="Path to OHLC CSV file"
    )
    
    parser.add_argument(
        "--atr",
        type=int,
        required=False,
        default=None,
        help="ATR period for SuperTrend (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--mult",
        type=float,
        required=False,
        default=None,
        help="ATR multiplier for SuperTrend (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--mode",
        type=str,
        required=False,
        default=None,
        choices=["revers", "long", "short"],
        help="Trading mode: revers (long+short), long, short (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Path to output XLSX file"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to YAML config file (optional, for supertrend/trade_mode/commission/warmup settings)"
    )
    
    parser.add_argument(
        "--execution-model",
        type=str,
        required=False,
        default=None,
        choices=["open_to_open"],
        help="Execution model: only 'open_to_open' is supported. "
             "close_to_close was removed due to look-ahead bias."
    )
    
    parser.add_argument(
        "--market",
        type=str,
        required=False,
        default=None,
        choices=["stocks", "crypto", "futures", "forex"],
        help="Market type for annualization defaults and validation (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--annualization-basis",
        type=str,
        required=False,
        default=None,
        choices=["calendar", "trading"],
        help="Annualization basis: calendar (365.25 days/year) or trading (252 days/year) (can be set in config.yaml)"
    )
    
    parser.add_argument(
        "--periods-per-year",
        type=str,
        required=False,
        default=None,
        help="Periods per year for annualization: integer or 'auto' (can be set in config.yaml)"
    )
    
    return parser.parse_args(args)


def validate_paths(csv_path: str, output_path: str) -> None:
    """
    Validate input and output file paths.
    
    Args:
        csv_path: Path to input CSV file
        output_path: Path to output Excel file
        
    Raises:
        FileNotFoundError: If CSV file does not exist
        ValueError: If output directory does not exist
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"Input CSV file not found: {csv_path}")
    
    output_file = Path(output_path)
    output_dir = output_file.parent
    if output_dir != Path('.') and not output_dir.exists():
        raise ValueError(f"Output directory does not exist: {output_dir}")


def load_tester_config(
    config_path: Optional[str],
    *,
    loaded_raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Load and parse tester configuration.

    Contract:
        If ``config_path`` is provided (non-empty string), any failure to read or
        parse the file — missing path, OS/read error, invalid YAML, a non-mapping
        root, or semantic validation expressed as ``ConfigError`` / ``ValueError``
        from ``load_config`` / YAML — must **fail-fast** (exception propagates).
        The caller must **not** fall back to built-in defaults with only a warning,
        as that would silently change commission, annualization, segmentation, etc.

        If ``config_path`` is ``None`` or ``""``, returns the built-in defaults
        without reading any file.

    Phase 2 (WP-T2): the optional ``trade_filter`` block is parsed into a
    ``TradeFilterConfig`` and attached as ``config["trade_filter"]``. Strict
    schema rejects unknown keys inside ``trade_filter.*``. ``raw_user_keys``
    presence tracking is preserved (plan §5.1, §5.3, §5.3.1; Appendix A v1.1
    §11–§11.3).

    Args:
        config_path: Path to YAML config file (optional)
        loaded_raw: If given with ``config_path``, use this dict instead of
            calling ``load_config`` again (same object the snapshot was built from).

    Returns:
        Dictionary with configuration values (with defaults when no path given).
        ``config["trade_filter"]`` is ``None`` when the YAML block is absent;
        otherwise a validated ``TradeFilterConfig``.
    """
    # Default values
    config = {
        "commission": 0.0,
        "warmup_period": 0,
        "warmup_period_auto": False,
        "min_trades_required": 5,
        "annualization_factor": 252,  # int or "auto" after coerce
        "annualization_basis": None,  # Optional: "calendar" | "trading"
        "market": None,  # Optional: "stocks" | "crypto" | "futures" | "forex"
        "execution_model": None,  # Optional: "open_to_open"
        "trade_mode": None,
        "supertrend": {
            "atr_period": None,
            "multiplier": None,
        },
        "segmentation": {
            "mode": "legacy",
            "n_parts": 5,
        },
        "export": {
            "false_start_max_bars": DEFAULT_FALSE_START_MAX_BARS,
        },
        # Phase 2 (WP-T2): trade_filter is None when the YAML block is absent
        # (Appendix A v1.1 §11.1 baseline — filter disabled, baseline preserved).
        "trade_filter": None,
    }

    if not config_path:
        return config

    loaded_config = load_config(config_path) if loaded_raw is None else loaded_raw
    if not isinstance(loaded_config, dict):
        raise ConfigError(
            f"Config root must be a YAML mapping, got {type(loaded_config).__name__}: "
            f"{config_path!r}"
        )
    unknown_root_keys = sorted(
        str(key) for key in loaded_config if key not in _ALLOWED_TESTER_ROOT_KEYS
    )
    if unknown_root_keys:
        raise ConfigError(
            "Config root contains unknown key(s): "
            + ", ".join(repr(k) for k in unknown_root_keys)
            + f"; allowed keys: {sorted(_ALLOWED_TESTER_ROOT_KEYS)}"
        )

    _missing_ann = object()
    raw_af = loaded_config.get("annualization_factor", _missing_ann)
    raw_ppy = loaded_config.get("periods_per_year", _missing_ann)
    if raw_af is not _missing_ann:
        raw_ann = raw_af
    elif raw_ppy is not _missing_ann:
        raw_ann = raw_ppy
    else:
        raw_ann = 252
    try:
        config["annualization_factor"] = coerce_annualization_config_value(raw_ann)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    config["commission"] = loaded_config.get("commission", 0.0)
    config["warmup_period"] = loaded_config.get("warmup_period", 0)
    config["warmup_period_auto"] = loaded_config.get("warmup_period_auto", False)
    config["min_trades_required"] = loaded_config.get("min_trades_required", 5)
    config["annualization_basis"] = loaded_config.get("annualization_basis", None)
    config["market"] = loaded_config.get("market", None)
    config["execution_model"] = loaded_config.get("execution_model", None)
    if config["execution_model"] is not None and config["execution_model"] != "open_to_open":
        raise ConfigError(
            f"execution_model must be 'open_to_open' or absent, "
            f"got: {config['execution_model']!r}. "
            "close_to_close was removed due to look-ahead bias."
        )
    config["trade_mode"] = loaded_config.get("trade_mode", None)

    supertrend_config = loaded_config.get("supertrend", {})
    if supertrend_config:
        config["supertrend"]["atr_period"] = supertrend_config.get("atr_period", None)
        config["supertrend"]["multiplier"] = supertrend_config.get("multiplier", None)

    seg_config = loaded_config.get("segmentation", {})
    if seg_config:
        raw_mode = seg_config.get("mode", "legacy")
        if raw_mode not in ("legacy", "equal_blocks"):
            raise ConfigError(
                f"segmentation.mode must be 'legacy' or 'equal_blocks', got: {raw_mode!r}"
            )
        config["segmentation"]["mode"] = raw_mode
        raw_n = seg_config.get("n_parts", 5)
        if not isinstance(raw_n, int) or raw_n < 2:
            raise ConfigError(
                f"segmentation.n_parts must be an integer >= 2, got: {raw_n!r}"
            )
        config["segmentation"]["n_parts"] = raw_n

    _merge_export_config(loaded_config, config)

    # Phase 2 (WP-T2): parse and validate trade_filter block (plan §5.1, §5.3).
    # Block absent => filter disabled, baseline preserved (Appendix A v1.1 §11.1).
    tf_raw = loaded_config.get("trade_filter")
    if tf_raw is not None:
        if not isinstance(tf_raw, dict):
            raise ConfigError(
                f"trade_filter must be a YAML mapping, got "
                f"{type(tf_raw).__name__!r}: {tf_raw!r}"
            )
        # Strict schema: reject unknown keys inside trade_filter subtree (§5.3.1)
        unknown_errors = collect_trade_filter_unknown_keys(tf_raw, "trade_filter")
        if unknown_errors:
            raise ConfigError(
                "trade_filter schema validation failed:\n"
                + "\n".join(f"  - {e}" for e in unknown_errors)
            )
        # Raw-key presence tracking BEFORE materialising defaults (§5.3.2)
        raw_user_keys = collect_raw_user_keys({"trade_filter": tf_raw})
        # Build TradeFilterConfig (raw values preserved for type validation)
        tf_cfg = build_trade_filter_config_from_raw(tf_raw)
        resolve_zigzag_enabled_in_place(tf_cfg, raw_user_keys)
        resolve_volume_enabled_in_place(tf_cfg, raw_user_keys)
        # Validate against Appendix A v1.1 §11–§11.3
        errors: list[str] = []
        validate_trade_filter(
            tf_cfg, errors, raw_user_keys, caller_pipeline="tester"
        )
        if errors:
            raise ConfigError(
                "trade_filter validation failed:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
        resolve_trade_filter_mode_in_place(tf_cfg, raw_user_keys)
        resolve_exit_off_mode_in_place(tf_cfg, raw_user_keys)
        resolve_exit_b_immediate_off_in_place(tf_cfg, raw_user_keys)
        resolve_time_filter_in_place(tf_cfg, raw_user_keys)
        resolve_volume_defaults_in_place(tf_cfg, raw_user_keys)
        config["trade_filter"] = tf_cfg

        # Phase 2 (WP-T2 + WP-T5 advanced into WP-T2 fail-fast slot, plan §5.5):
        # zigzag_st_mode is supported only with segmentation.mode=legacy. Reject
        # equal_blocks + enabled=true at config-validation time, BEFORE any
        # stats / backtest work. Use the strict helper so malformed raw values
        # are never coerced into an enabled runtime branch.
        if (
            is_trade_filter_enabled(tf_cfg)
            and config["segmentation"]["mode"] == "equal_blocks"
        ):
            raise ConfigError(
                "equal_blocks segmentation is not supported with "
                "trade_filter.enabled=true; use 'legacy' segmentation instead. "
                "zigzag_st_mode is supported only with segmentation.mode=legacy."
            )

    print(f"Loaded config from: {config_path}")
    if config["supertrend"]["atr_period"] is not None:
        print(f"  atr_period: {config['supertrend']['atr_period']}")
    if config["supertrend"]["multiplier"] is not None:
        print(f"  multiplier: {config['supertrend']['multiplier']}")
    if config["trade_mode"] is not None:
        print(f"  trade_mode: {config['trade_mode']}")
    print(f"  commission: {config['commission']}")
    print(f"  warmup_period: {config['warmup_period']}")
    print(f"  warmup_period_auto: {config['warmup_period_auto']}")
    print(f"  min_trades_required: {config['min_trades_required']}")
    print(f"  annualization_factor: {config['annualization_factor']}")
    if config["annualization_basis"] is not None:
        print(f"  annualization_basis: {config['annualization_basis']}")
    if config["market"] is not None:
        print(f"  market: {config['market']}")
    if config["execution_model"] is not None:
        print(f"  execution_model: {config['execution_model']}")
    seg = config["segmentation"]
    print(f"  segmentation.mode: {seg['mode']}")
    if seg["mode"] == "equal_blocks":
        print(f"  segmentation.n_parts: {seg['n_parts']}")
    print(
        f"  export.false_start_max_bars: {config['export']['false_start_max_bars']}"
    )
    if config["trade_filter"] is not None:
        tf_status = "enabled" if config["trade_filter"].enabled else "disabled"
        tf_type = config["trade_filter"].type or "<unset>"
        print(f"  trade_filter: {tf_status} (type={tf_type})")

    return config


def merge_cli_and_config(
    parsed: argparse.Namespace,
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge CLI arguments with config, CLI takes precedence.
    
    Args:
        parsed: Parsed command line arguments
        config: Loaded configuration
        
    Returns:
        Merged configuration
        
    Raises:
        ValueError: If required parameters are missing
    """
    # CLI arguments override config values
    if parsed.atr is not None:
        atr_period = parsed.atr
    elif config["supertrend"]["atr_period"] is not None:
        atr_period = config["supertrend"]["atr_period"]
    else:
        raise ValueError(
            "atr_period must be specified either via --atr argument or in config.yaml"
        )
    
    if parsed.mult is not None:
        multiplier = parsed.mult
    elif config["supertrend"]["multiplier"] is not None:
        multiplier = config["supertrend"]["multiplier"]
    else:
        raise ValueError(
            "multiplier must be specified either via --mult argument or in config.yaml"
        )
    
    if parsed.mode is not None:
        trade_mode = parsed.mode
    elif config["trade_mode"] is not None:
        trade_mode = config["trade_mode"]
    else:
        raise ValueError(
            "trade_mode must be specified either via --mode argument or in config.yaml"
        )
    
    # CLI overrides for new parameters
    annualization_factor = config["annualization_factor"]
    if parsed.periods_per_year is not None:
        # Parse CLI argument (can be "auto" or integer string)
        if parsed.periods_per_year == "auto":
            annualization_factor = "auto"
        else:
            try:
                annualization_factor = int(parsed.periods_per_year)
            except ValueError:
                raise ValueError(
                    f"--periods-per-year must be 'auto' or an integer, got: {parsed.periods_per_year}"
                )
    
    annualization_basis = config["annualization_basis"]
    if parsed.annualization_basis is not None:
        annualization_basis = parsed.annualization_basis
    
    market = config["market"]
    if parsed.market is not None:
        market = parsed.market
    
    execution_model = config["execution_model"]
    if parsed.execution_model is not None:
        execution_model = parsed.execution_model
    
    return {
        "atr_period": atr_period,
        "multiplier": multiplier,
        "trade_mode": trade_mode,
        "commission": config["commission"],
        "warmup_period": config["warmup_period"],
        "warmup_period_auto": config["warmup_period_auto"],
        "min_trades_required": config["min_trades_required"],
        "annualization_factor": annualization_factor,
        "annualization_basis": annualization_basis,
        "market": market,
        "execution_model": execution_model,
        "segmentation": config["segmentation"],
        "false_start_max_bars": config["export"]["false_start_max_bars"],
        # Phase 2 (WP-T2): trade_filter passes through unchanged. None means
        # the YAML block was absent (baseline preserved).
        "trade_filter": config.get("trade_filter"),
    }


def run_backtest(args: argparse.Namespace) -> str:
    """
    Run the backtest pipeline.
    
    Args:
        args: Parsed command line arguments
        
    Returns:
        Path to the output file
    """
    # Validate paths
    validate_paths(args.csv, args.out)
    
    # Load config (single read of YAML when a file is given — snapshot + normalize)
    config_yaml_snapshot: Optional[Dict[str, Any]] = None
    if args.config:
        config_yaml_snapshot = load_config(args.config)
        config = load_tester_config(args.config, loaded_raw=config_yaml_snapshot)
    else:
        config = load_tester_config(None)

    # Merge CLI and config
    params = merge_cli_and_config(args, config)
    
    # Load and validate data
    df = load_ohlc_csv(args.csv)
    df = validate_ohlc_data(df)
    df = validate_volume_filter_data(df, params.get("trade_filter"))
    tf_cfg = params.get("trade_filter")
    if is_volume_enabled(tf_cfg):
        full_volume_runtime = build_volume_global_metrics(
            df["volume"].to_numpy(),
            df["close"].to_numpy(),
            tf_cfg.volume,
        )
        _warn_if_volume_baseline_window_large(
            tf_cfg.volume, len(df)
        )
    else:
        full_volume_runtime = None
    
    n = len(df)
    
    # Resolve periods_per_year from config/CLI + data
    market_enum = MarketType(params["market"]) if params["market"] else None
    
    periods_per_year = resolve_periods_per_year_from_config(
        config_value=params["annualization_factor"],
        index=df.index,
        explicit_basis=params["annualization_basis"],
        market=market_enum
    )
    
    print(f"\nResolved periods_per_year: {periods_per_year:.2f}")
    
    # Emit market warnings if applicable (only for calendar_days_span >= 30)
    if market_enum is not None:
        stats = detect_timeframe(df.index)
        if stats.calendar_days_span >= 30:
            warnings = validate_market_vs_timeframe(market_enum, stats)
            for warning in warnings:
                print(f"WARNING: {warning}")
    
    # Resolve execution_model
    if params["execution_model"] is not None:
        execution_model = ExecutionModel(params["execution_model"])
    else:
        execution_model = ExecutionModel.OPEN_TO_OPEN  # Default
    
    # Calculate warmup period
    if params["warmup_period_auto"]:
        warmup_period = calculate_warmup_tester(
            n=n,
            atr_period=params["atr_period"],
            warmup_period_auto=True
        )
        print(f"Using auto-warmup: {warmup_period} bars (10% of {n} bars, clamped)")
    else:
        warmup_period = max(params["warmup_period"], params["atr_period"])
    
    seg_mode = params["segmentation"]["mode"]
    n_parts = params["segmentation"]["n_parts"]
    run_metadata_common: Dict[str, Any] = {
        "config_path": str(Path(args.config).resolve()) if args.config else "",
        "csv_path": str(Path(args.csv).resolve()),
        "output_path_requested": str(Path(args.out)),
        "segmentation": {
            "mode": seg_mode,
            "n_parts": n_parts,
        },
        "resolved_periods_per_year": periods_per_year,
        "annualization_factor_config": params["annualization_factor"],
        "warmup_period_resolved": warmup_period,
        "warmup_period_auto": params["warmup_period_auto"],
        "execution_model": execution_model.value,
        "market": params["market"],
        "annualization_basis": params["annualization_basis"],
    }

    print(f"\nRunning backtest...")
    print(f"  CSV: {args.csv}")
    print(f"  ATR period: {params['atr_period']}")
    print(f"  Multiplier: {params['multiplier']}")
    print(f"  Mode: {params['trade_mode']}")
    print(f"  Commission: {params['commission']}")
    print(f"  Warmup period: {warmup_period}")
    print(f"  auto_warmup: {params['warmup_period_auto']}")
    print(f"  min_trades_required: {params['min_trades_required']}")
    print(f"  Execution model: {execution_model.value}")
    print(f"  Segmentation mode: {seg_mode}")
    if seg_mode == "equal_blocks":
        print(f"  n_parts: {n_parts}")

    if seg_mode == "equal_blocks":
        segment_results = run_equal_blocks(
            df=df,
            n_parts=n_parts,
            warmup_period=warmup_period,
            atr_period=params["atr_period"],
            multiplier=params["multiplier"],
            trade_mode=params["trade_mode"],
            commission=params["commission"],
            periods_per_year=periods_per_year,
            execution_model=execution_model,
            min_trades_required=params["min_trades_required"],
        )

        print(f"\nBacktest completed ({n_parts} segments):")
        for s in segment_results:
            m = s.segment_metrics
            print(
                f"  {s.segment_label} [{s.range_label}]: "
                f"{m.get('num_trades', 0)} trades, "
                f"Sum PnL: {m.get('sum_pnl_pct', 0):.2f}%"
            )

        print(f"\nExporting to Excel: {args.out}")
        run_metadata_export = dict(run_metadata_common)
        run_metadata_export["warmup_period_effective"] = (
            segment_results[0].ext_slice_effective_warmup
        )
        actual_output = export_equal_blocks_results(
            segment_results,
            args.out,
            config_yaml_snapshot=config_yaml_snapshot,
            run_metadata=run_metadata_export,
        )

    else:
        # Legacy mode: 5 tail slices.
        #
        # Phase 2 (WP-T4) — plan §7.1: materialise ZigZag global stats ONCE from
        # the full validated df BEFORE slicing, so all 5 legacy periods share the
        # same full-dataset statistics (spec §12: global_median is a full-dataset
        # statistic, NOT slice-local).
        #
        # Disabled path: trade_filter is None or enabled=False → zigzag_global_stats
        # stays None; run_all_periods disabled path is bit-identical baseline.
        zigzag_global_stats = None
        if is_zigzag_enabled(tf_cfg):
            print("  [trade_filter] Building ZigZag global stats from full dataset...")
            zigzag_global_stats = build_zigzag_global_stats(
                close=df["close"].values,
                trade_filter_config=tf_cfg,
            )
            print(
                f"  [trade_filter] Stats built: "
                f"n_legs={zigzag_global_stats.n_legs_total}, "
                f"global_median={zigzag_global_stats.global_median:.4f}, "
                f"threshold={zigzag_global_stats.candidate_trigger_threshold:.4f} "
                f"({zigzag_global_stats.candidate_trigger_source})"
            )

        results = run_all_periods(
            df=df,
            atr_period=params["atr_period"],
            multiplier=params["multiplier"],
            trade_mode=params["trade_mode"],
            commission=params["commission"],
            warmup_period=warmup_period,
            periods_per_year=periods_per_year,
            execution_model=execution_model,
            auto_warmup=params["warmup_period_auto"],
            min_trades_required=params["min_trades_required"],
            # Phase 2 (WP-T4): pass filter config + pre-materialised stats
            trade_filter_config=tf_cfg,
            zigzag_global_stats=zigzag_global_stats,
            volume_runtime=full_volume_runtime,
        )

        print(f"\nBacktest completed:")
        for r in results:
            print(f"  {r.period_label}: {r.metrics['num_trades']} trades, Sum PnL: {r.metrics['sum_pnl_pct']:.2f}%")

        # WP-T6: pass filter_diagnostics from the 100% period result.
        # None (disabled path) → bit-identical Signals output.
        # not None (enabled path) → 4 filter columns appended (plan §8.2).
        signals_df = build_signal_events(
            df=df,
            trend=results[0].result.trend,
            atr_period=params["atr_period"],
            trade_mode=params["trade_mode"],
            execution_model=execution_model,
            filter_diagnostics=results[0].filter_diagnostics,
        )

        print(f"\nExporting to Excel: {args.out}")
        run_metadata_export = dict(run_metadata_common)
        run_metadata_export["warmup_period_effective"] = results[0].effective_warmup
        # WP-T7: pass trade_filter_config and df for conditional filter sheets/columns
        actual_output = export_tester_results(
            results,
            args.out,
            signals_df=signals_df,
            false_start_max_bars=params["false_start_max_bars"],
            trade_filter_config=tf_cfg,
            df=df,
            config_yaml_snapshot=config_yaml_snapshot,
            run_metadata=run_metadata_export,
        )

    print(f"\n[SUCCESS] Results exported to: {actual_output}")

    return actual_output


def main(args=None) -> None:
    """Main entry point with error handling."""
    parsed = parse_args(args)
    
    try:
        run_backtest(parsed)
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
        
    except (ConfigError, DataValidationError) as e:
        print(f"Validation error: {e}", file=sys.stderr)
        sys.exit(1)
        
    except yaml.YAMLError as e:
        print(f"Configuration file error: {e}", file=sys.stderr)
        sys.exit(1)
        
    except pd.errors.ParserError as e:
        print(f"CSV parsing error: {e}", file=sys.stderr)
        sys.exit(1)
        
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    except PermissionError as e:
        print(
            f"Error: could not write output file (is it open in another program?): {e}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
