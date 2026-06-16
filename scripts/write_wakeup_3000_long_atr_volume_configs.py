"""Write the compact 3000-config Mode D long ATR+volume wakeup grid."""
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


DEFAULT_OUT_DIR = Path(r"G:\3.1_wf_grid\config tester")

SHORT_WINDOW_PAIRS = (
    (5, 5),
    (15, 15),
    (25, 25),
)
ATR_LONG_WINDOWS = (400, 600, 800, 1000)
VOLUME_BASELINE_WINDOWS = (600, 800, 1000, 1200, 1400)
RATIO_PAIRS = (
    (Decimal("1.6"), Decimal("1.6")),
    (Decimal("1.9"), Decimal("1.9")),
    (Decimal("2.1"), Decimal("2.1")),
    (Decimal("2.3"), Decimal("2.3")),
    (Decimal("2.5"), Decimal("2.5")),
    (Decimal("3.0"), Decimal("3.0")),
    (Decimal("1.9"), Decimal("1.6")),
    (Decimal("2.1"), Decimal("1.9")),
    (Decimal("2.3"), Decimal("2.1")),
    (Decimal("2.5"), Decimal("2.3")),
)
TTL_BARS = (5, 25, 45, 65, 90)


def _fmt_ratio(value: Decimal) -> str:
    return f"{value:.1f}"


def _ratio_tag(value: Decimal) -> str:
    return f"{int(value * 10):02d}"


def _render_config(
    idx: int,
    *,
    atr_short_window: int,
    volume_short_window: int,
    atr_long_window: int,
    volume_baseline_window: int,
    atr_min_ratio: Decimal,
    volume_min_ratio: Decimal,
    ttl_bars: int,
) -> str:
    atr_ratio = _fmt_ratio(atr_min_ratio)
    volume_ratio = _fmt_ratio(volume_min_ratio)
    return f"""# Auto-generated Mode D tester ATR + volume wakeup compact grid config #{idx}
# params: trade_mode=long, time_filter=true, candidate_height.enabled=false, candidate_height.quantile=0.75, candidate_age.enabled=false, atr_expansion.short_window={atr_short_window}, atr_expansion.long_window={atr_long_window}, atr_expansion.min_ratio={atr_ratio}, volume_expansion.enabled=true, volume_expansion.short_window={volume_short_window}, volume_expansion.baseline_window={volume_baseline_window}, volume_expansion.min_ratio={volume_ratio}, ttl.bars={ttl_bars}, no_fresh_candidate.enabled=false, position_freeze.enabled=false

supertrend:
  atr_period: 20
  multiplier: 1.0

trade_mode: long
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
        quantile: 0.75
      candidate_age:
        enabled: false
        max_bars: 10
      atr_expansion:
        enabled: true
        short_window: {atr_short_window}
        long_window: {atr_long_window}
        min_ratio: {atr_ratio}
      volume_expansion:
        enabled: true
        short_window: {volume_short_window}
        baseline_window: {volume_baseline_window}
        min_ratio: {volume_ratio}

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


def _build_configs() -> list[tuple[str, str]]:
    configs: list[tuple[str, str]] = []
    idx = 0
    for atr_short_window, volume_short_window in SHORT_WINDOW_PAIRS:
        for atr_long_window in ATR_LONG_WINDOWS:
            for volume_baseline_window in VOLUME_BASELINE_WINDOWS:
                for atr_min_ratio, volume_min_ratio in RATIO_PAIRS:
                    for ttl_bars in TTL_BARS:
                        idx += 1
                        filename = (
                            f"modeD_wakeup3000_{idx:04d}_long"
                            f"_asw{atr_short_window:03d}"
                            f"_alw{atr_long_window:04d}"
                            f"_amr{_ratio_tag(atr_min_ratio)}"
                            f"_vsw{volume_short_window:03d}"
                            f"_vbw{volume_baseline_window:04d}"
                            f"_vmr{_ratio_tag(volume_min_ratio)}"
                            f"_ttl{ttl_bars:03d}.yaml"
                        )
                        configs.append(
                            (
                                filename,
                                _render_config(
                                    idx,
                                    atr_short_window=atr_short_window,
                                    volume_short_window=volume_short_window,
                                    atr_long_window=atr_long_window,
                                    volume_baseline_window=volume_baseline_window,
                                    atr_min_ratio=atr_min_ratio,
                                    volume_min_ratio=volume_min_ratio,
                                    ttl_bars=ttl_bars,
                                ),
                            )
                        )
    return configs


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    configs = _build_configs()
    if len(configs) != 3000:
        raise SystemExit(f"internal grid error: expected 3000 configs, got {len(configs)}")

    collisions = [out_dir / filename for filename, _ in configs if (out_dir / filename).exists()]
    if collisions:
        sample = "\n".join(f" - {path}" for path in collisions[:10])
        raise SystemExit(
            f"refusing to overwrite {len(collisions)} existing config(s):\n{sample}"
        )

    written: list[Path] = []
    for filename, text in configs:
        path = out_dir / filename
        path.write_text(text, encoding="utf-8")
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
