"""Write reduced Mode D tester YAML configs for the ATR wakeup grid."""
from __future__ import annotations

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


OUT_DIR = ROOT / "config tester atr_wakeup_grid_reduced"

TRADE_MODES = ("revers", "long")
SHORT_WINDOWS = (5, 10, 15, 20, 30, 45, 60)
LONG_WINDOWS = (60, 100, 150, 250, 400, 600)
MIN_RATIOS = (
    Decimal("1.3"),
    Decimal("1.6"),
    Decimal("1.9"),
    Decimal("2.2"),
    Decimal("2.6"),
    Decimal("3.0"),
    Decimal("3.3"),
)
TTL_BARS = (5, 10, 20, 35, 60, 90)


def _fmt_ratio(value: Decimal) -> str:
    return f"{value:.1f}"


def _ratio_tag(value: Decimal) -> str:
    return f"{int(value * 10):02d}"


def _render_config(
    idx: int,
    *,
    trade_mode: str,
    short_window: int,
    long_window: int,
    min_ratio: Decimal,
    ttl_bars: int,
) -> str:
    ratio = _fmt_ratio(min_ratio)
    return f"""# Auto-generated Mode D tester reduced ATR grid config #{idx}
# params: trade_mode={trade_mode}, time_filter=true, candidate_height.enabled=false, atr_expansion.short_window={short_window}, atr_expansion.long_window={long_window}, atr_expansion.min_ratio={ratio}, ttl.bars={ttl_bars}, volume_expansion.enabled=false, no_fresh_candidate.enabled=false, position_freeze.enabled=false

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
        enabled: false
        quantile: 0.60
        max_age_bars: 15
        timeout_bars: 20
      action:
        mode: close_position
    position_freeze:
      enabled: false
      min_hold_bars: 3
      apply_to: internal_opposite_st_flip
      release_action: apply_if_still_opposite
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    idx = 0
    for trade_mode in TRADE_MODES:
        for short_window in SHORT_WINDOWS:
            for long_window in LONG_WINDOWS:
                for min_ratio in MIN_RATIOS:
                    for ttl_bars in TTL_BARS:
                        idx += 1
                        filename = (
                            f"modeD_atr_reduced_{idx:04d}_{trade_mode}"
                            f"_sw{short_window:03d}"
                            f"_lw{long_window:03d}"
                            f"_mr{_ratio_tag(min_ratio)}"
                            f"_ttl{ttl_bars:03d}.yaml"
                        )
                        path = OUT_DIR / filename
                        path.write_text(
                            _render_config(
                                idx,
                                trade_mode=trade_mode,
                                short_window=short_window,
                                long_window=long_window,
                                min_ratio=min_ratio,
                                ttl_bars=ttl_bars,
                            ),
                            encoding="utf-8",
                        )
                        written.append(path)

    samples = [written[0], written[len(written) // 2], written[-1]]
    for path in samples:
        with contextlib.redirect_stdout(io.StringIO()):
            load_tester_config(str(path))

    print(f"written {len(written)} configs to {OUT_DIR}")
    print("validated samples:")
    for path in samples:
        print(f" - {path.name}")


if __name__ == "__main__":
    main()
