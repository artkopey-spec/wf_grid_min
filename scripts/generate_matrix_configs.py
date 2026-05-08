"""Generate 60 wf_grid YAML configs from the agreed matrix (one file per table row)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wf_grid.config.loader import load_grid_config

COMMON_HEADER = """# Автогенерация: сетка matrix 12x5 (60 конфигов).
# Общие блоки заданы идентично для всех файлов (см. задачу пользователя).

schema_version: 1

data:
  file_path: "data.csv"
  periods_per_year: 252
  annualization_basis: "trading"

optimization:
  atr_period_range: [20, 60]
  multiplier_range: [1.0, 2.0]
  multiplier_step: 0.2
  trade_mode: "revers"

backtest:
  commission: 0.0000
  min_trades_required: 3
  early_exit_enabled: false
  early_exit_max_drawdown: 0.50
  early_exit_check_bars: 50

validation:
  warmup_period: 0
  warmup_period_auto: true
  walk_forward:
    train_size: "90d"
    test_size: "30d"
    step_size: "30d"
    scheme: "rolling"
    anchor: "start"
    min_train_bars: 500
    min_test_bars: 100

gates:
  step:
    min_trades: 0
    max_drawdown_threshold: -10.0
  candidate:
    positive_median_threshold: -1000.0
    min_trades_median: 0.0
    worst_segment_pnl_threshold: null
    max_drawdown_threshold: -10.0
    min_total_trades: 0
    min_ok_ratio: 0.0

ranking:
  mode: "gates_score"
  min_segments_for_ranking: null
  sort_by: "sum_pnl_pct_Median"
  tiebreaker: "sum_pnl_pct_Min"

scoring:
  normalization_mode: "rank"
  min_passed_for_discrimination: 1
  low_spread_threshold: 1.0e-6
  score_weights:
    sum_pnl_pct_Median: 0.42
    profitable_segments_count: 0.34
    abs_max_drawdown_Median: 0.12
    abs_max_drawdown_Min: 0.12

status:
  min_meaningful_bars: 1

bucket:
  atr_bucket_step: 2
  mult_bucket_step: 0.2
  min_buckets_for_median: 1

execution:
  parallel_enabled: true
  max_workers: null
  chunksize: null
  fallback_to_sequential: false
"""

# Rows: (block_id 1..12, mode, gate_on, max_bars or None, exit_is_b, imm_true, time_on)
# gate_on False => только enabled: false, без max_bars
# max_bars используется только если gate_on


def _row(
    block: int,
    mode: str,
    gate_on: bool,
    max_bars: int | None,
    exit_b: bool,
    imm: bool | None,
    time_on: bool,
) -> dict:
    return {
        "block": block,
        "mode": mode,
        "gate_on": gate_on,
        "max_bars": max_bars,
        "exit_b": exit_b,
        "imm": imm,
        "time_on": time_on,
    }


def all_rows() -> list[dict]:
    """12 блоков x 5 режимов — порядок как в матрице пользователя."""
    rows: list[dict] = []

    def add_block(
        bid: int,
        patterns: list[tuple[str, bool, int | None]],
        exit_b: bool,
        imm: bool | None,
        time_on: bool,
    ) -> None:
        """patterns: (mode, gate_on, max_bars if gate_on else ignored)."""
        for mode, gon, mb in patterns:
            mx = mb if gon else None
            rows.append(_row(bid, mode, gon, mx, exit_b, imm, time_on))

    pat_exitA_tf_off = [
        ("A", False, None),
        ("B", False, None),
        ("C", False, None),
        ("A+B", False, None),
        ("C+B", False, None),
    ]
    pat_exitA_tf_off_mixed_gate = [
        ("A", True, 30),
        ("B", False, None),
        ("C", True, 30),
        ("A+B", True, 30),
        ("C+B", True, 30),
    ]
    pat_exitB_tf_off_goff = [
        ("A", False, None),
        ("B", False, None),
        ("C", False, None),
        ("A+B", False, None),
        ("C+B", False, None),
    ]
    pat_exitB_tf_off_mixed = [
        ("A", True, 30),
        ("B", False, None),  # «off» + лишний 30 в таблице — валидно только off
        ("C", True, 30),
        ("A+B", True, 30),
        ("C+B", True, 30),
    ]

    add_block(1, pat_exitA_tf_off, exit_b=False, imm=None, time_on=False)
    add_block(2, pat_exitA_tf_off_mixed_gate, exit_b=False, imm=None, time_on=False)
    add_block(3, pat_exitB_tf_off_goff, exit_b=True, imm=False, time_on=False)
    add_block(4, pat_exitB_tf_off_mixed, exit_b=True, imm=False, time_on=False)
    add_block(5, pat_exitB_tf_off_mixed, exit_b=True, imm=True, time_on=False)
    add_block(6, pat_exitB_tf_off_goff, exit_b=True, imm=True, time_on=False)

    add_block(8, pat_exitA_tf_off, exit_b=False, imm=None, time_on=True)
    add_block(9, pat_exitA_tf_off_mixed_gate, exit_b=False, imm=None, time_on=True)
    add_block(10, pat_exitB_tf_off_goff, exit_b=True, imm=False, time_on=True)
    add_block(11, pat_exitB_tf_off_mixed, exit_b=True, imm=False, time_on=True)
    add_block(12, pat_exitB_tf_off_mixed, exit_b=True, imm=True, time_on=True)
    add_block(13, pat_exitB_tf_off_goff, exit_b=True, imm=True, time_on=True)

    return rows


def build_trade_filter_yaml(r: dict) -> str:
    mode_yaml = str(r["mode"])
    if "+" in mode_yaml:
        mode_line = f'    mode: "{mode_yaml}"\n'
    else:
        mode_line = f"    mode: {mode_yaml}\n"

    if r["gate_on"]:
        gate_yaml = (
            "    candidate_duration_gate:\n"
            "      enabled: true\n"
            f"      max_bars: {int(r['max_bars'])}\n"
        )
    else:
        gate_yaml = (
            "    candidate_duration_gate:\n"
            "      enabled: false\n"
        )

    if r["exit_b"]:
        extra = '    exit_off_mode: "exit B"\n    exit_off_zz_leg_count: 5\n'
        if r["imm"]:
            extra += "    exit_b_immediate_off: true\n"
    else:
        extra = '    exit_off_mode: "exit A"\n'

    life = (
        "  lifecycle:\n"
        "    freeze_confirmed_legs: 3\n"
        "    stop_check: confirm_bar_only\n"
        "    stopping_exit: opposite_st_flip\n"
        f"{extra}"
    )

    if r["time_on"]:
        tf = (
            "  time_filter:\n"
            "    enabled: true\n"
            '    window: "09:00-19:00"\n'
        )
    else:
        tf = (
            "  time_filter:\n"
            "    enabled: false\n"
        )

    return (
        "trade_filter:\n"
        "  enabled: true\n"
        "  type: zigzag_st_mode\n"
        "  zigzag:\n"
        f"{mode_line}"
        "    reversal_threshold: 0.002\n"
        "    candidate_trigger_threshold: auto\n"
        "    candidate_trigger_quantile: 0.80\n"
        "    local_window: 5\n"
        "    daily_reset: true\n"
        f"{gate_yaml}"
        f"{life}"
        "  diagnostics:\n"
        "    export_state_columns: true\n"
        "    export_trigger_columns: true\n"
        f"{tf}"
    )


def main() -> None:
    rows = all_rows()
    assert len(rows) == 60, len(rows)

    out_dir = ROOT / "config"
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, r in enumerate(rows, start=1):
        meta = (
            f"# matrix block {r['block']}, mode {r['mode']}, "
            f"exit={'B' if r['exit_b'] else 'A'}, "
            f"gate={'on' if r['gate_on'] else 'off'}, "
            f"imm={r['imm']}, time_filter={'on' if r['time_on'] else 'off'}\n"
        )
        content = meta + COMMON_HEADER + build_trade_filter_yaml(r)
        path = out_dir / f"grid_matrix_{i:03d}.yaml"
        path.write_text(content, encoding="utf-8")
        load_grid_config(str(path))
        print("OK", path.name)

    print("written", len(rows), "files to", out_dir)


if __name__ == "__main__":
    main()
