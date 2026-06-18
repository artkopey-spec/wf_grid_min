"""Write lite tester YAML configs (filters off) for ATR × multiplier grid."""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DONOR_ROOT = ROOT / "donor"
if str(DONOR_ROOT) not in sys.path:
    sys.path.insert(0, str(DONOR_ROOT))

from supertrend_optimizer.cli.tester import load_tester_config  # noqa: E402


OUT_DIR = ROOT / "config tester"

ATR_PERIODS = (10, 30, 50, 70, 90, 110)
MULT_MIN = 1.0
MULT_MAX = 6.0
MULT_STEP = 0.2
TRADE_MODES = ("revers", "long")


def _mult_values() -> list[float]:
    values: list[float] = []
    tick = round(MULT_MIN / MULT_STEP)
    tick_max = round(MULT_MAX / MULT_STEP)
    while tick <= tick_max:
        values.append(round(tick * MULT_STEP, 10))
        tick += 1
    return values


def _mult_tag(mult: float) -> str:
    return f"{int(round(mult * 10)):02d}"


def _atr_tag(atr_period: int) -> str:
    return f"{atr_period:03d}"


def _render_config(
    idx: int,
    *,
    trade_mode: str,
    atr_period: int,
    multiplier: float,
) -> str:
    mode_label = "long" if trade_mode == "long" else "lite"
    header = (
        f"# Auto-generated {mode_label} tester config #{idx}\n"
        f"# params: all filters disabled"
    )
    if trade_mode == "long":
        header += f", trade_mode=long"
    header += f", atr_period={atr_period}, multiplier={multiplier:.1f}\n"

    return f"""{header}
supertrend:
  atr_period: {atr_period}
  multiplier: {multiplier:.1f}

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
  enabled: false
  type: zigzag_st_mode

  zigzag:
    enabled: false
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
    enabled: false
    lock_cycle_direction: false
    entry:
      candidate_height:
        enabled: false
        quantile: 0.65
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
        enabled: false
        bars: 5
      no_fresh_candidate:
        enabled: false
        quantile: 0.60
        max_age_bars: 15
        timeout_bars: 20
      action:
        mode: close_position
    position_freeze:
      enabled: false
      min_hold_bars: 2
      apply_to: internal_opposite_st_flip
      release_action: apply_if_still_opposite
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for old in OUT_DIR.glob("lite*.yaml"):
        old.unlink()

    written: list[Path] = []
    idx = 0
    for trade_mode in TRADE_MODES:
        prefix = "lite_long" if trade_mode == "long" else "lite"
        for atr_period in ATR_PERIODS:
            for multiplier in _mult_values():
                idx += 1
                filename = (
                    f"{prefix}_atr{_atr_tag(atr_period)}"
                    f"_mult{_mult_tag(multiplier)}.yaml"
                )
                path = OUT_DIR / filename
                path.write_text(
                    _render_config(
                        idx,
                        trade_mode=trade_mode,
                        atr_period=atr_period,
                        multiplier=multiplier,
                    ),
                    encoding="utf-8",
                )
                written.append(path)

    samples = [written[0], written[len(written) // 2], written[-1]]
    for path in samples:
        with contextlib.redirect_stdout(io.StringIO()):
            load_tester_config(str(path))

    mult_count = len(_mult_values())
    print(f"written {len(written)} configs to {OUT_DIR}")
    print(
        f"grid: {len(ATR_PERIODS)} ATR x {mult_count} mult "
        f"({MULT_MIN}..{MULT_MAX}, step {MULT_STEP}) x {len(TRADE_MODES)} modes"
    )
    print("validated samples:")
    for path in samples:
        print(f" - {path.name}")


if __name__ == "__main__":
    main()
