"""Write a top-33% follow-up tester grid from the 19.06 batch results."""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import math
import re
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DONOR_ROOT = ROOT / "donor"
if str(DONOR_ROOT) not in sys.path:
    sys.path.insert(0, str(DONOR_ROOT))

from supertrend_optimizer.cli.tester import load_tester_config  # noqa: E402


DEFAULT_SUMMARY = Path(
    r"C:\Users\lizko\OneDrive\Рабочий стол\массовые прогоны\19.06"
    r"\tester_summary_20260617_122258.xlsx"
)
DEFAULT_OUT_DIR = ROOT / "config tester 19.06 top33 candidate_height"
TOP_FRACTION = Decimal("0.33")
CANDIDATE_HEIGHT_QUANTILES = (
    Decimal("0.30"),
    Decimal("0.45"),
    Decimal("0.60"),
    Decimal("0.80"),
)

PARAM_RE = re.compile(
    r"(?P<source_idx>\d+)_modeD_lite_(?P<trade_mode>[a-z]+)_"
    r"asw(?P<asw>\d+)_alw(?P<alw>\d+)_amr(?P<amr>\d+)_"
    r"vsw(?P<vsw>\d+)_vbw(?P<vbw>\d+)_vmr(?P<vmr>\d+)_"
    r"ttl(?P<ttl>\d+)_hold(?P<hold>\d+)"
)


def _ratio_from_tag(value: str) -> Decimal:
    return Decimal(int(value)) / Decimal(10)


def _fmt_ratio(value: Decimal) -> str:
    return f"{value:.1f}"


def _fmt_quantile(value: Decimal) -> str:
    return f"{value:.2f}"


def _quantile_tag(value: Decimal) -> str:
    return f"q{int(value * 100):02d}"


def _parse_params(source_file: str) -> dict[str, object]:
    match = PARAM_RE.search(source_file)
    if match is None:
        raise ValueError(f"could not parse params from source_file: {source_file}")
    data = match.groupdict()
    return {
        "source_idx": int(data["source_idx"]),
        "trade_mode": data["trade_mode"],
        "asw": int(data["asw"]),
        "alw": int(data["alw"]),
        "amr": _ratio_from_tag(data["amr"]),
        "vsw": int(data["vsw"]),
        "vbw": int(data["vbw"]),
        "vmr": _ratio_from_tag(data["vmr"]),
        "ttl": int(data["ttl"]),
        "hold": int(data["hold"]),
    }


def _render_config(
    *,
    output_idx: int,
    rank: int,
    source_file: str,
    source_pnl: float,
    quantile: Decimal,
    params: dict[str, object],
) -> str:
    atr_ratio = _fmt_ratio(params["amr"])  # type: ignore[arg-type]
    volume_ratio = _fmt_ratio(params["vmr"])  # type: ignore[arg-type]
    q = _fmt_quantile(quantile)
    return f"""# Auto-generated top-33% 19.06 candidate-height branch config #{output_idx}
# source_rank={rank}, source_pnl_pct={source_pnl:.4f}
# source_file={source_file}
# params: trade_mode={params["trade_mode"]}, candidate_height.enabled=true, candidate_height.quantile={q}, atr_expansion.short_window={params["asw"]}, atr_expansion.long_window={params["alw"]}, atr_expansion.min_ratio={atr_ratio}, volume_expansion.short_window={params["vsw"]}, volume_expansion.baseline_window={params["vbw"]}, volume_expansion.min_ratio={volume_ratio}, ttl.bars={params["ttl"]}, position_freeze.min_hold_bars={params["hold"]}

supertrend:
  atr_period: 20
  multiplier: 1.0

trade_mode: {params["trade_mode"]}
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
        enabled: true
        quantile: {q}
      candidate_age:
        enabled: false
        max_bars: 10
      atr_expansion:
        enabled: true
        short_window: {params["asw"]}
        long_window: {params["alw"]}
        min_ratio: {atr_ratio}
      volume_expansion:
        enabled: true
        short_window: {params["vsw"]}
        baseline_window: {params["vbw"]}
        min_ratio: {volume_ratio}

    exit:
      ttl:
        enabled: true
        bars: {params["ttl"]}
      no_fresh_candidate:
        enabled: false
        quantile: 0.60
        max_age_bars: 15
        timeout_bars: 20
      action:
        mode: close_position

    position_freeze:
      enabled: true
      min_hold_bars: {params["hold"]}
      apply_to: internal_opposite_st_flip
      release_action: apply_if_still_opposite
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    summary_path = args.summary
    out_dir = args.out_dir

    if not summary_path.is_file():
        raise SystemExit(f"summary not found: {summary_path}")
    if out_dir.exists() and any(out_dir.glob("*.y*ml")) and not args.force:
        raise SystemExit(f"refusing to write: target already has YAML files: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(summary_path, sheet_name="summary", engine="openpyxl")
    required = {"source_file", "Sum PnL %"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"summary is missing columns: {sorted(missing)}")

    df = df.sort_values("Sum PnL %", ascending=False, kind="mergesort").reset_index(drop=True)
    top_count = math.floor(len(df) * float(TOP_FRACTION))
    top = df.head(top_count)

    manifest_rows: list[dict[str, object]] = []
    written: list[Path] = []
    output_idx = 0
    for rank, (_, row) in enumerate(top.iterrows(), start=1):
        source_file = str(row["source_file"])
        source_pnl = float(row["Sum PnL %"])
        params = _parse_params(source_file)
        stem = Path(source_file).stem
        source_idx = int(params["source_idx"])
        for quantile in CANDIDATE_HEIGHT_QUANTILES:
            output_idx += 1
            filename = (
                f"{output_idx:05d}_src{source_idx:04d}_{_quantile_tag(quantile)}_"
                f"{stem}.yaml"
            )
            path = out_dir / filename
            text = _render_config(
                output_idx=output_idx,
                rank=rank,
                source_file=source_file,
                source_pnl=source_pnl,
                quantile=quantile,
                params=params,
            )
            path.write_text(text, encoding="utf-8")
            written.append(path)
            manifest_rows.append(
                {
                    "output_file": filename,
                    "source_rank": rank,
                    "source_file": source_file,
                    "source_pnl_pct": f"{source_pnl:.4f}",
                    "candidate_height_quantile": _fmt_quantile(quantile),
                    "source_idx": source_idx,
                    "trade_mode": params["trade_mode"],
                    "asw": params["asw"],
                    "alw": params["alw"],
                    "amr": _fmt_ratio(params["amr"]),  # type: ignore[arg-type]
                    "vsw": params["vsw"],
                    "vbw": params["vbw"],
                    "vmr": _fmt_ratio(params["vmr"]),  # type: ignore[arg-type]
                    "ttl": params["ttl"],
                    "hold": params["hold"],
                }
            )

    manifest_path = out_dir / "manifest.csv"
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)

    samples = [written[0], written[len(written) // 2], written[-1]]
    for path in samples:
        with contextlib.redirect_stdout(io.StringIO()):
            load_tester_config(str(path))

    print(f"summary: {summary_path}")
    print(f"top configs: {top_count} / {len(df)} ({float(TOP_FRACTION) * 100:.0f}%)")
    print(f"branches per config: {len(CANDIDATE_HEIGHT_QUANTILES)}")
    print(f"written yaml: {len(written)}")
    print(f"out dir: {out_dir}")
    print(f"manifest: {manifest_path}")
    print("validated samples:")
    for path in samples:
        print(f" - {path.name}")


if __name__ == "__main__":
    main()
