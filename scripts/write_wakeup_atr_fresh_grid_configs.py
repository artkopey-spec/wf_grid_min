"""Write Mode D tester YAML configs for the ATR + no_fresh_candidate grid."""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DONOR_ROOT = ROOT / "donor"
if str(DONOR_ROOT) not in sys.path:
    sys.path.insert(0, str(DONOR_ROOT))

from supertrend_optimizer.cli.tester import load_tester_config  # noqa: E402


DEFAULT_OUT_DIR = Path(r"G:\3.1_wf_grid\config tester atr_wakeup_fresh_grid")

TRADE_MODES = ("long",)
SHORT_WINDOWS = (5, 10, 15, 20, 25, 30)
LONG_WINDOWS = (400, 600, 800, 1000)
MIN_RATIOS = (Decimal("1.6"), Decimal("1.9"), Decimal("2.2"))
TTL_BARS = (5, 90, 110)
FRESH_QUANTILES = (Decimal("0.55"), Decimal("0.60"), Decimal("0.70"))
FRESH_MAX_AGE_BARS = (10, 15, 25)
FRESH_TIMEOUT_BARS = (20, 40, 60)


def _fmt_decimal(value: Decimal) -> str:
    return f"{value:.2f}" if value < 1 else f"{value:.1f}"


def _ratio_tag(value: Decimal) -> str:
    return f"{int(value * 10):02d}"


def _quantile_tag(value: Decimal) -> str:
    return f"q{int(value * 100):02d}"


def _render_config(
    idx: int,
    *,
    trade_mode: str,
    short_window: int,
    long_window: int,
    min_ratio: Decimal,
    ttl_bars: int,
    fresh_quantile: Decimal,
    fresh_max_age_bars: int,
    fresh_timeout_bars: int,
) -> str:
    ratio = _fmt_decimal(min_ratio)
    quantile = _fmt_decimal(fresh_quantile)
    return f"""# Auto-generated Mode D tester ATR + no_fresh_candidate grid config #{idx}
# params: trade_mode={trade_mode}, time_filter=true, candidate_height.enabled=false, atr_expansion.short_window={short_window}, atr_expansion.long_window={long_window}, atr_expansion.min_ratio={ratio}, ttl.bars={ttl_bars}, no_fresh_candidate.quantile={quantile}, no_fresh_candidate.max_age_bars={fresh_max_age_bars}, no_fresh_candidate.timeout_bars={fresh_timeout_bars}, volume_expansion.enabled=false, position_freeze.enabled=false

supertrend:
  atr_period: 20
  multiplier: 1.0

trade_mode: {trade_mode}
period: false

commission: 0.0000

warmup_period: 0
warmup_period_auto: true

periods_per_year: 252
annualization_basis: trading
execution_model: open_to_open
min_trades_required: 3

early_exit:
  enabled: false
  max_drawdown: 0.50
  check_bars: 50

segmentation:
  mode: legacy
  n_parts: 7

export:
  diagnostics: false
  signals: false
  false_start: false
  cycle: false
  trades: false
  false_start_max_bars: 4

trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    enabled: true
    mode: D
    global_stats_source: full_dataset
    leg_height_mode: pct
    reversal_threshold: 0.001
    candidate_trigger_threshold: 0.012
    global_median: auto
    local_window: 5
    daily_reset: false
    candidate_duration_gate:
      enabled: false

  lifecycle:
    freeze_confirmed_legs: 3
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
    exit_off_mode: "exit C"

  time_filter:
    enabled: true
    window: "09:00-19:00"

  volume:
    enabled: false
    mode: volume_A
    aggregation: mean
    daily_reset: true
    cycle_direction_gate: false
    short_window: 10
    baseline_window: 1000
    threshold_ratio: 1.6
    exit_hysteresis_ratio: 1.6
    exit_freeze_bars: 5
    regime_low_ratio: 0.8
    regime_high_ratio: 1.2
    direction_lookback_bars: 5
    baseline_session:
      enabled: true
      window: "09:00-19:00"

  wakeup_regime:
    enabled: true
    lock_cycle_direction: false
    entry:
      candidate_height:
        enabled: false
        quantile: 0.65
      candidate_age:
        enabled: false
        max_bars: 10
      atr_expansion:
        enabled: true
        short_window: {short_window}
        long_window: {long_window}
        min_ratio: {ratio}
      volume_expansion:
        enabled: false
        short_window: 10
        baseline_window: 1000
        min_ratio: 0.5

    exit:
      ttl:
        enabled: true
        bars: {ttl_bars}
      no_fresh_candidate:
        enabled: true
        quantile: {quantile}
        max_age_bars: {fresh_max_age_bars}
        timeout_bars: {fresh_timeout_bars}
      action:
        mode: close_position
    position_freeze:
      enabled: false
      min_hold_bars: 3
      apply_to: internal_opposite_st_flip
      release_action: apply_if_still_opposite
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    out_dir = args.out_dir
    if out_dir.exists() and any(out_dir.glob("*.yaml")):
        raise SystemExit(f"refusing to write: target already has YAML files: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    idx = 0
    for trade_mode in TRADE_MODES:
        for short_window in SHORT_WINDOWS:
            for long_window in LONG_WINDOWS:
                for min_ratio in MIN_RATIOS:
                    for ttl_bars in TTL_BARS:
                        for fresh_quantile in FRESH_QUANTILES:
                            for fresh_max_age_bars in FRESH_MAX_AGE_BARS:
                                for fresh_timeout_bars in FRESH_TIMEOUT_BARS:
                                    idx += 1
                                    filename = (
                                        f"modeD_atr_fresh_{idx:04d}_{trade_mode}"
                                        f"_sw{short_window:03d}"
                                        f"_lw{long_window:04d}"
                                        f"_mr{_ratio_tag(min_ratio)}"
                                        f"_ttl{ttl_bars:03d}"
                                        f"_{_quantile_tag(fresh_quantile)}"
                                        f"_age{fresh_max_age_bars:02d}"
                                        f"_to{fresh_timeout_bars:03d}.yaml"
                                    )
                                    path = out_dir / filename
                                    path.write_text(
                                        _render_config(
                                            idx,
                                            trade_mode=trade_mode,
                                            short_window=short_window,
                                            long_window=long_window,
                                            min_ratio=min_ratio,
                                            ttl_bars=ttl_bars,
                                            fresh_quantile=fresh_quantile,
                                            fresh_max_age_bars=fresh_max_age_bars,
                                            fresh_timeout_bars=fresh_timeout_bars,
                                        ),
                                        encoding="utf-8",
                                    )
                                    written.append(path)

    samples = [written[0], written[len(written) // 2], written[-1]]
    for path in samples:
        with contextlib.redirect_stdout(io.StringIO()):
            load_tester_config(str(path))

    print(f"written {len(written)} configs to {out_dir}")
    print("validated samples:")
    for path in samples:
        print(f" - {path.name}")


if __name__ == "__main__":
    main()
