"""
Batch launcher for SuperTrend Tester — 2 runs: equal_blocks, then legacy.

Reads a single config_tester.yaml (SuperTrend params, trade_mode, commission,
warmup, etc.) and runs both segmentation modes sequentially.

Usage:
    python run_batch_tester.py --csv data.csv [--config config_tester.yaml] [--output-dir .]
"""

import argparse
import copy
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Mode-C sys.path bootstrap (MUST precede any `from supertrend_optimizer.*`).
#
# Why this exists:
#   When run as a CLI from `donor TESTER/`, Python sets sys.path[0] to the
#   script's directory.  That directory contains the legacy package marker
#   `donor TESTER/supertrend_optimizer/__init__.py`, which would otherwise
#   shadow the active donor.  After WP-T3 dedup the legacy subtree no longer
#   ships `cli/tester.py`, `engine/run.py`, etc., so a shadow resolution
#   produces ``ModuleNotFoundError: No module named 'supertrend_optimizer.cli.tester'``.
#
# Fix:
#   Mirror the test-harness logic from `donor TESTER/tests/conftest.py`:
#   put `<repo>/donor` ahead of `<repo>/donor TESTER` on sys.path BEFORE
#   any `supertrend_optimizer.*` import is evaluated.  Identical dedupe
#   pattern keeps the bootstrap idempotent if the script is re-imported.
#
# Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt
#                 §3.1 (Mode C), §14 WP-T3 step 5 (extended owner audit-fix).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DONOR_ROOT = _REPO_ROOT / "donor"
_TESTER_ROOT = _REPO_ROOT / "donor TESTER"

if not (_DONOR_ROOT / "supertrend_optimizer" / "__init__.py").is_file():
    raise RuntimeError(
        "Mode-C bootstrap: active-donor package not found at "
        f"{_DONOR_ROOT / 'supertrend_optimizer'}; cannot resolve "
        "supertrend_optimizer.* imports."
    )

# Insert in reverse order so DONOR_ROOT ends up at sys.path[0].
for _path in (str(_TESTER_ROOT), str(_DONOR_ROOT)):
    while _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

# Defensive runtime check: top-level `supertrend_optimizer` MUST resolve from
# `donor/`.  Fails loudly with a clear message if the bootstrap above was
# circumvented by a PYTHONPATH override or a stale install.
import supertrend_optimizer as _so_pkg  # noqa: E402

_so_file = Path(_so_pkg.__file__).resolve()
_expected_top_level = (
    _DONOR_ROOT / "supertrend_optimizer" / "__init__.py"
).resolve()
if _so_file != _expected_top_level:
    raise RuntimeError(
        "Mode-C bootstrap failed: `supertrend_optimizer` resolved from "
        f"{_so_file}, expected {_expected_top_level}.  "
        "Check PYTHONPATH and sys.path order."
    )

del _path, _so_pkg, _so_file, _expected_top_level

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from supertrend_optimizer.cli.tester import load_tester_config, merge_cli_and_config  # noqa: E402
from supertrend_optimizer.data.loader import load_ohlc_csv  # noqa: E402
from supertrend_optimizer.data.validator import (  # noqa: E402
    validate_ohlc_data,
    validate_volume_filter_data,
)
from supertrend_optimizer.data.timeframe import (  # noqa: E402
    detect_timeframe,
    resolve_periods_per_year_from_config,
    validate_market_vs_timeframe,
)
from supertrend_optimizer.io.excel_tester import (  # noqa: E402
    export_tester_results,
    export_equal_blocks_results,
)
from supertrend_optimizer.core.volume_metrics import (  # noqa: E402
    _warn_if_volume_baseline_window_large,
    build_volume_global_metrics,
)
from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats  # noqa: E402
from supertrend_optimizer.core.trade_filter_config import (  # noqa: E402
    is_volume_enabled,
    is_zigzag_enabled,
)
from supertrend_optimizer.testing.runner import run_all_periods, run_equal_blocks  # noqa: E402
from supertrend_optimizer.testing.signal_events import build_signal_events  # noqa: E402
from supertrend_optimizer.utils.config import load_config  # noqa: E402
from supertrend_optimizer.utils.enums import ExecutionModel, MarketType  # noqa: E402
from supertrend_optimizer.utils.exceptions import ConfigError, DataValidationError  # noqa: E402
from supertrend_optimizer.utils.warmup import calculate_warmup_tester  # noqa: E402


RUNS = [
    ("equal_blocks", "result_equal_blocks.xlsx"),
    ("legacy",       "result_legacy.xlsx"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch SuperTrend Tester (equal_blocks + legacy)"
    )
    parser.add_argument(
        "--csv",
        type=str,
        default="data.csv",
        help="Path to OHLC CSV file",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config_tester.yaml",
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Directory for output Excel files (default: current directory)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not Path(args.csv).exists():
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    if not Path(args.config).exists():
        print(f"Error: Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        config_yaml_snapshot = load_config(args.config)
        cfg = load_tester_config(args.config, loaded_raw=config_yaml_snapshot)
    except (ConfigError, yaml.YAMLError, FileNotFoundError, UnicodeDecodeError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        df = load_ohlc_csv(args.csv)
        df = validate_ohlc_data(df)
        df = validate_volume_filter_data(df, cfg.get("trade_filter"))
    except (FileNotFoundError, DataValidationError, pd.errors.ParserError, ValueError) as e:
        print(f"Data error: {e}", file=sys.stderr)
        sys.exit(1)

    n = len(df)

    # Build a minimal argparse.Namespace so merge_cli_and_config() is happy
    # (all CLI overrides are None — config is the single source of truth)
    cli_stub = argparse.Namespace(
        atr=None,
        mult=None,
        mode=None,
        execution_model=None,
        market=None,
        annualization_basis=None,
        periods_per_year=None,
    )

    try:
        base_params = merge_cli_and_config(cli_stub, cfg)
    except ValueError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    market_enum = MarketType(base_params["market"]) if base_params["market"] else None

    periods_per_year = resolve_periods_per_year_from_config(
        config_value=base_params["annualization_factor"],
        index=df.index,
        explicit_basis=base_params["annualization_basis"],
        market=market_enum,
    )

    if market_enum is not None:
        stats = detect_timeframe(df.index)
        if stats.calendar_days_span >= 30:
            for warning in validate_market_vs_timeframe(market_enum, stats):
                print(f"WARNING: {warning}")

    execution_model = (
        ExecutionModel(base_params["execution_model"])
        if base_params["execution_model"]
        else ExecutionModel.OPEN_TO_OPEN
    )

    if base_params["warmup_period_auto"]:
        warmup_period = calculate_warmup_tester(
            n=n,
            atr_period=base_params["atr_period"],
            warmup_period_auto=True,
        )
    else:
        warmup_period = max(base_params["warmup_period"], base_params["atr_period"])

    # Phase 2 (WP-T4) — plan §7.1: materialise ZigZag global stats ONCE from
    # the full validated df.  Used ONLY for the legacy run; equal_blocks + enabled
    # is rejected by the config gate in load_tester_config (WP-T2 equal_blocks
    # gate) before we reach this point.
    #
    # cfg.trade_filter is the TradeFilterConfig attached during load_tester_config.
    # base_params also carries it under key "trade_filter" (via merge_cli_and_config).
    tf_cfg = base_params.get("trade_filter")
    zigzag_global_stats = None
    full_volume_runtime = None
    if is_zigzag_enabled(tf_cfg):
        try:
            zigzag_global_stats = build_zigzag_global_stats(
                close=df["close"].values,
                trade_filter_config=tf_cfg,
            )
        except (ConfigError, ValueError) as e:
            print(f"Trade-filter stats error: {e}", file=sys.stderr)
            sys.exit(1)
    if is_volume_enabled(tf_cfg):
        try:
            full_volume_runtime = build_volume_global_metrics(
                df["volume"].to_numpy(),
                df["close"].to_numpy(),
                tf_cfg.volume,
                index=df.index,
            )
            _warn_if_volume_baseline_window_large(tf_cfg.volume, len(df))
        except (ConfigError, ValueError) as e:
            print(f"Trade-filter volume error: {e}", file=sys.stderr)
            sys.exit(1)

    print("=" * 48)
    print("Batch SuperTrend Tester (equal_blocks + legacy)")
    print("=" * 48)
    print(f"  CSV:          {args.csv}")
    print(f"  Config:       {args.config}")
    print(f"  ATR period:   {base_params['atr_period']}")
    print(f"  Multiplier:   {base_params['multiplier']}")
    print(f"  Trade mode:   {base_params['trade_mode']}")
    print(f"  Commission:   {base_params['commission']}")
    print(f"  Warmup:       {warmup_period} bars")
    print(f"  Periods/year: {periods_per_year:.2f}")
    if is_zigzag_enabled(tf_cfg):
        print(
            f"  trade_filter: enabled (type={tf_cfg.type}), "
            f"n_legs={zigzag_global_stats.n_legs_total}, "
            f"threshold={zigzag_global_stats.candidate_trigger_threshold:.4f} "
            f"({zigzag_global_stats.candidate_trigger_source})"
        )
    else:
        print(f"  trade_filter: disabled")
    print("=" * 48)

    run_metadata_base = {
        "config_path": str(Path(args.config).resolve()),
        "csv_path": str(Path(args.csv).resolve()),
        "resolved_periods_per_year": periods_per_year,
        "annualization_factor_config": base_params["annualization_factor"],
        "warmup_period_resolved": warmup_period,
        "warmup_period_auto": base_params["warmup_period_auto"],
        "execution_model": execution_model.value,
        "market": base_params["market"],
        "annualization_basis": base_params["annualization_basis"],
    }

    saved_files: list[str] = []

    for run_idx, (seg_mode, out_basename) in enumerate(RUNS, start=1):
        label = f"[{run_idx}/{len(RUNS)}] {seg_mode}"
        print(f"\n{label} ... started")

        params = copy.deepcopy(base_params)
        params["segmentation"]["mode"] = seg_mode
        n_parts = params["segmentation"]["n_parts"]

        try:
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
                    trade_filter_config=tf_cfg,
                )
                for s in segment_results:
                    m = s.segment_metrics
                    print(
                        f"  {s.segment_label} [{s.range_label}]: "
                        f"{m.get('num_trades', 0)} trades, "
                        f"Sum PnL: {m.get('sum_pnl_pct', 0):.2f}%"
                    )
                out_path = str(output_dir / out_basename)
                run_metadata = {
                    **run_metadata_base,
                    "output_path_requested": out_path,
                    "segmentation": {"mode": seg_mode, "n_parts": n_parts},
                    "warmup_period_effective": (
                        segment_results[0].ext_slice_effective_warmup
                    ),
                }
                actual_output = export_equal_blocks_results(
                    segment_results,
                    out_path,
                    config_yaml_snapshot=config_yaml_snapshot,
                    run_metadata=run_metadata,
                )

            else:
                # Phase 2 (WP-T4): pass pre-materialised stats (plan §7.1).
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
                    trade_filter_config=tf_cfg,
                    zigzag_global_stats=zigzag_global_stats,
                    volume_runtime=full_volume_runtime,
                    include_period_splits=params["period"],
                )
                for r in results:
                    print(
                        f"  {r.period_label}: {r.metrics['num_trades']} trades, "
                        f"Sum PnL: {r.metrics['sum_pnl_pct']:.2f}%"
                    )
                # WP-T6: pass filter_diagnostics from the 100% period result.
                # None (disabled path) → bit-identical Signals output.
                # not None (enabled path) → 4 filter columns appended (plan §8.2).
                signals_df = None
                if params["export"]["signals"]:
                    signals_df = build_signal_events(
                        df=df,
                        trend=results[0].result.trend,
                        atr_period=params["atr_period"],
                        trade_mode=params["trade_mode"],
                        execution_model=execution_model,
                        filter_diagnostics=results[0].filter_diagnostics,
                    )
                out_path = str(output_dir / out_basename)
                run_metadata = {
                    **run_metadata_base,
                    "output_path_requested": out_path,
                    "segmentation": {"mode": seg_mode, "n_parts": n_parts},
                    "warmup_period_effective": results[0].effective_warmup,
                }
                actual_output = export_tester_results(
                    results,
                    out_path,
                    signals_df=signals_df,
                    false_start_max_bars=base_params["false_start_max_bars"],
                    trade_filter_config=tf_cfg,
                    df=df,
                    config_yaml_snapshot=config_yaml_snapshot,
                    run_metadata=run_metadata,
                    export_diagnostics=params["export"]["diagnostics"],
                    export_signals=params["export"]["signals"],
                    export_false_start=params["export"]["false_start"],
                    export_cycle=params["export"]["cycle"],
                    export_trades=params["export"]["trades"],
                )

        except (ConfigError, DataValidationError) as e:
            print(f"\n{label} FAILED (validation): {e}", file=sys.stderr)
            sys.exit(1)
        except PermissionError as e:
            print(f"\n{label} FAILED (permission — close Excel?): {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"\n{label} FAILED: {e}", file=sys.stderr)
            sys.exit(1)

        saved_files.append(actual_output)
        print(f"{label} ... done -> {Path(actual_output).name}")

    print("\n" + "=" * 48)
    print(f"All {len(RUNS)} runs completed successfully!")
    for i, path in enumerate(saved_files, start=1):
        print(f"  {i}. {path}")
    print("=" * 48)


if __name__ == "__main__":
    main()
