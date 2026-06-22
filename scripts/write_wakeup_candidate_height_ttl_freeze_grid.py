"""Write lite-style Mode D tester configs for candidate-height/TTL/freeze grid."""
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


DEFAULT_OUT_DIR = ROOT / "config tester"

ATR_PERIODS = (10, 20, 30, 40, 50)
MULTIPLIERS = (
    Decimal("1.0"),
    Decimal("1.5"),
    Decimal("2.0"),
    Decimal("2.5"),
)
CANDIDATE_HEIGHT_QUANTILES = (
    Decimal("0.4"),
    Decimal("0.6"),
    Decimal("0.8"),
    Decimal("1.0"),
)
TTL_BARS = (60, 70, 80, 90)
MIN_HOLD_BARS = (1, 3, 5)
LOCK_CYCLE_DIRECTIONS = (False, True)
LOCAL_WINDOWS = (3, 5, 8, 13)
REVERSAL_THRESHOLDS = (
    Decimal("0.001"),
    Decimal("0.002"),
    Decimal("0.005"),
)

DEFAULT_LOCAL_WINDOW = 5
DEFAULT_REVERSAL_THRESHOLD = Decimal("0.001")


def _fmt_decimal(value: Decimal) -> str:
    return f"{value:.1f}"


def _fmt_threshold(value: Decimal) -> str:
    return f"{value:.3f}"


def _multiplier_tag(value: Decimal) -> str:
    return f"mult{int(value * 10):02d}"


def _atr_tag(value: int) -> str:
    return f"atr{value:03d}"


def _quantile_tag(value: Decimal) -> str:
    return f"q{int(value * 100):03d}"


def _reversal_tag(value: Decimal) -> str:
    return f"rev{int(value * 1000):03d}"


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def _render_config(
    idx: int,
    *,
    atr_period: int,
    multiplier: Decimal,
    candidate_height_quantile: Decimal,
    ttl_bars: int,
    min_hold_bars: int,
    lock_cycle_direction: bool,
    local_window: int,
    reversal_threshold: Decimal,
) -> str:
    multiplier_text = _fmt_decimal(multiplier)
    quantile = _fmt_decimal(candidate_height_quantile)
    lock_text = _yaml_bool(lock_cycle_direction)
    reversal_threshold_text = _fmt_threshold(reversal_threshold)
    return f"""# Auto-generated lite-style Mode D tester candidate-height + TTL + freeze grid config #{idx}
# params: atr_period={atr_period}, multiplier={multiplier_text}, trade_mode=revers, time_filter=false, lock_cycle_direction={lock_text}, zigzag.local_window={local_window}, zigzag.reversal_threshold={reversal_threshold_text}, candidate_height.enabled=true, candidate_height.quantile={quantile}, atr_expansion.enabled=false, volume_expansion.enabled=false, ttl.enabled=true, ttl.bars={ttl_bars}, local_median_stop.enabled=true, position_freeze.enabled=true, position_freeze.min_hold_bars={min_hold_bars}

supertrend:
  atr_period: {atr_period}
  multiplier: {multiplier_text}

trade_mode: revers
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
    reversal_threshold: {reversal_threshold_text}
    candidate_trigger_threshold: 0.012
    global_median: auto
    local_window: {local_window}
    daily_reset: false
    candidate_duration_gate:
      enabled: false

  lifecycle:
    freeze_confirmed_legs: 3
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
    exit_off_mode: "exit C"

  time_filter:
    enabled: false
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
      enabled: false
      window: "09:00-19:00"

  wakeup_regime:
    enabled: true
    lock_cycle_direction: {lock_text}
    entry:
      candidate_height:
        enabled: true
        quantile: {quantile}
      candidate_age:
        enabled: false
        max_bars: 10
      atr_expansion:
        enabled: false
        short_window: 30
        long_window: 400
        min_ratio: 1.6
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
        enabled: false
        quantile: 0.60
        max_age_bars: 15
        timeout_bars: 20
      local_median_stop:
        enabled: true
      action:
        mode: close_position

    position_freeze:
      enabled: true
      min_hold_bars: {min_hold_bars}
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
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped: list[Path] = []
    target_paths: list[Path] = []
    idx = 0
    for atr_period in ATR_PERIODS:
        for multiplier in MULTIPLIERS:
            for quantile in CANDIDATE_HEIGHT_QUANTILES:
                for ttl_bars in TTL_BARS:
                    for min_hold_bars in MIN_HOLD_BARS:
                        for lock_cycle_direction in LOCK_CYCLE_DIRECTIONS:
                            for local_window in LOCAL_WINDOWS:
                                for reversal_threshold in REVERSAL_THRESHOLDS:
                                    idx += 1
                                    lock_suffix = "_lock1" if lock_cycle_direction else ""
                                    local_suffix = (
                                        ""
                                        if (
                                            local_window == DEFAULT_LOCAL_WINDOW
                                            and reversal_threshold == DEFAULT_REVERSAL_THRESHOLD
                                        )
                                        else (
                                            f"_lw{local_window:02d}"
                                            f"_{_reversal_tag(reversal_threshold)}"
                                        )
                                    )
                                    filename = (
                                        f"lite_{_atr_tag(atr_period)}_{_multiplier_tag(multiplier)}"
                                        f"_ch_{_quantile_tag(quantile)}"
                                        f"_ttl{ttl_bars:03d}"
                                        f"_hold{min_hold_bars:02d}"
                                        f"{local_suffix}"
                                        f"{lock_suffix}.yaml"
                                    )
                                    path = out_dir / filename
                                    target_paths.append(path)
                                    if path.exists():
                                        skipped.append(path)
                                        continue
                                    path.write_text(
                                        _render_config(
                                            idx,
                                            atr_period=atr_period,
                                            multiplier=multiplier,
                                            candidate_height_quantile=quantile,
                                            ttl_bars=ttl_bars,
                                            min_hold_bars=min_hold_bars,
                                            lock_cycle_direction=lock_cycle_direction,
                                            local_window=local_window,
                                            reversal_threshold=reversal_threshold,
                                        ),
                                        encoding="utf-8",
                                    )
                                    written.append(path)

    expected = (
        len(ATR_PERIODS)
        * len(MULTIPLIERS)
        * len(CANDIDATE_HEIGHT_QUANTILES)
        * len(TTL_BARS)
        * len(MIN_HOLD_BARS)
        * len(LOCK_CYCLE_DIRECTIONS)
        * len(LOCAL_WINDOWS)
        * len(REVERSAL_THRESHOLDS)
    )
    if len(target_paths) != expected:
        raise SystemExit(
            f"internal grid error: expected {expected} configs, got {len(target_paths)}"
        )

    samples = [target_paths[0], target_paths[len(target_paths) // 2], target_paths[-1]]
    for path in samples:
        with contextlib.redirect_stdout(io.StringIO()):
            load_tester_config(str(path))

    print(f"target grid: {len(target_paths)} configs")
    print(f"written {len(written)} configs to {out_dir}")
    print(f"skipped existing: {len(skipped)}")
    print("validated samples:")
    for path in samples:
        print(f" - {path.name}")


if __name__ == "__main__":
    main()
