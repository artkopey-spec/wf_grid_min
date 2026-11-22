"""Analyze close-confirmed profit exits inside completed SuperTrend segments."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from statistics import fmean, median

import numpy as np
from openpyxl import load_workbook


THRESHOLDS_PCT = (0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00)
SESSION_START = time(9, 0)
SESSION_END = time(19, 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--dates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--slippage-bps-per-side", type=float, default=1.0)
    return parser.parse_args()


def read_bars(path: Path) -> list[dict]:
    bars: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle, delimiter=";")
        required = {"<DATE>", "<TIME>", "<OPEN>", "<CLOSE>"}
        if not required.issubset(rows.fieldnames or []):
            raise ValueError(f"Missing CSV columns: {sorted(required - set(rows.fieldnames or []))}")
        for index, row in enumerate(rows):
            timestamp = datetime.strptime(
                f"{row['<DATE>']}{int(row['<TIME>']):06d}", "%Y%m%d%H%M%S"
            )
            bars.append({
                "index": index,
                "time": timestamp,
                "open": float(row["<OPEN>"]),
                "close": float(row["<CLOSE>"]),
            })
    return bars


def read_dates(path: Path) -> set:
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book.active
        rows = sheet.iter_rows(values_only=True)
        header = next(rows)
        try:
            column = header.index("Дата сессии")
        except ValueError as exc:
            raise ValueError("Column 'Дата сессии' not found in dates workbook") from exc
        return {
            value.date() if isinstance(value, datetime) else value
            for row in rows
            if (value := row[column]) is not None
        }
    finally:
        book.close()


def read_flips_and_commission(path: Path) -> tuple[list[dict], float, dict]:
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        summary_rows = list(book["Summary"].iter_rows(min_row=1, max_row=2, values_only=True))
        params = dict(zip(summary_rows[0], summary_rows[1]))
        if int(params["ATR Period"]) != 80 or float(params["Multiplier"]) != 4.0:
            raise ValueError(f"Expected SuperTrend 80/4, got {params['ATR Period']}/{params['Multiplier']}")

        commission_fraction = None
        for row in book["Tester_Config"].iter_rows(values_only=True):
            if len(row) >= 3 and str(row[1]).strip().lower() == "commission":
                commission_fraction = float(row[2])
                break
        if commission_fraction is None:
            raise ValueError("Commission not found in Tester_Config")

        rows = book["Signals"].iter_rows(values_only=True)
        header = next(rows)
        col = {name: idx for idx, name in enumerate(header)}
        unique: dict[int, dict] = {}
        raw_reversal_rows = 0
        duplicate_rows = 0
        for row in rows:
            if not row[col["Is Reversal"]]:
                continue
            raw_reversal_rows += 1
            index = int(row[col["Signal Bar Index"]])
            item = {
                "index": index,
                "time": row[col["Signal Time"]],
                "close": float(row[col["Signal Close Price"]]),
                "before": str(row[col["ST Color Before"]]).upper(),
                "after": str(row[col["ST Color After"]]).upper(),
            }
            if index in unique:
                duplicate_rows += 1
                if unique[index] != item:
                    raise ValueError(f"Conflicting reversal duplicates at Signal Bar Index {index}")
            else:
                unique[index] = item
        audit = {
            "raw_reversal_signal_rows": raw_reversal_rows,
            "unique_flip_indices": len(unique),
            "removed_duplicate_rows": duplicate_rows,
        }
        return [unique[index] for index in sorted(unique)], commission_fraction, audit
    finally:
        book.close()


def validate_alignment(bars: list[dict], flips: list[dict]) -> None:
    for flip in flips:
        if flip["index"] < 0 or flip["index"] >= len(bars):
            raise ValueError(f"Flip index outside CSV: {flip['index']}")
        bar = bars[flip["index"]]
        if bar["time"] != flip["time"] or abs(bar["close"] - flip["close"]) > 1e-9:
            raise ValueError(f"CSV/Signals mismatch at bar {flip['index']}")


def build_segments(bars: list[dict], flips: list[dict], volatile_dates: set) -> tuple[list[dict], dict]:
    segments: list[dict] = []
    audit = defaultdict(int)
    for source_segment_id, (start, end) in enumerate(zip(flips, flips[1:]), start=1):
        audit["all_completed_flip_pairs"] += 1
        if start["after"] != end["before"]:
            raise ValueError(f"ST color discontinuity at {start['index']}..{end['index']}")
        if end["index"] + 1 >= len(bars):
            audit["excluded_missing_exit_bar"] += 1
            continue
        day = start["time"].date()
        if day != end["time"].date():
            audit["excluded_cross_session"] += 1
            continue
        if day not in volatile_dates:
            audit["excluded_nonvolatile_date"] += 1
            continue
        if not (SESSION_START <= start["time"].time() < SESSION_END):
            audit["excluded_start_outside_09_19"] += 1
            continue
        if not (SESSION_START <= end["time"].time() < SESSION_END):
            audit["excluded_end_outside_09_19"] += 1
            continue
        direction = 1 if start["after"] == "GREEN" else -1 if start["after"] == "RED" else 0
        if direction == 0:
            raise ValueError(f"Unexpected ST color {start['after']!r}")
        entry_index = start["index"] + 1
        hold_exit_index = end["index"] + 1
        if bars[entry_index]["time"].date() != day or bars[hold_exit_index]["time"].date() != day:
            audit["excluded_execution_cross_session"] += 1
            continue
        entry_price = bars[entry_index]["open"]
        hold_exit_price = bars[hold_exit_index]["open"]
        segments.append({
            "segment_id": len(segments) + 1,
            "source_segment_id": source_segment_id,
            "date": day,
            "color": start["after"],
            "direction": direction,
            "start_flip_index": start["index"],
            "start_flip_time": start["time"],
            "end_flip_index": end["index"],
            "end_flip_time": end["time"],
            "entry_index": entry_index,
            "entry_time": bars[entry_index]["time"],
            "entry_price": entry_price,
            "hold_exit_index": hold_exit_index,
            "hold_exit_time": bars[hold_exit_index]["time"],
            "hold_exit_price": hold_exit_price,
            "duration_flip_to_flip_min": end["index"] - start["index"],
            "hold_gross_pnl_pct": direction * (hold_exit_price - entry_price) / entry_price * 100,
        })
    audit["eligible_segments"] = len(segments)
    return segments, dict(audit)


def assign_period_halves(segments: list[dict]) -> dict:
    first_date = min(row["date"] for row in segments)
    last_date = max(row["date"] for row in segments)
    midpoint = first_date + (last_date - first_date) / 2
    for row in segments:
        row["period_half"] = "FIRST" if row["date"] <= midpoint else "SECOND"
    return {"first_date": first_date, "last_date": last_date, "calendar_midpoint": midpoint}


def simulate(
    bars: list[dict], segments: list[dict], commission_side_pct: float, slippage_side_pct: float
) -> list[dict]:
    details: list[dict] = []
    for segment in segments:
        direction = segment["direction"]
        entry = segment["entry_price"]
        aligned = []
        for index in range(segment["entry_index"], segment["end_flip_index"] + 1):
            favorable_pct = direction * (bars[index]["close"] - entry) / entry * 100
            aligned.append((index, favorable_pct))
        max_favorable = max(value for _, value in aligned)
        hold_gross = segment["hold_gross_pnl_pct"]
        for threshold in THRESHOLDS_PCT:
            hit = next(((idx, value) for idx, value in aligned if value >= threshold), None)
            if hit is None:
                trigger_index = None
                trigger_close_move = None
                exit_index = segment["hold_exit_index"]
                exit_reason = "NEXT_FLIP_OPEN"
                minutes_hit_to_flip = None
                post_hit_extra = None
            else:
                trigger_index, trigger_close_move = hit
                exit_index = trigger_index + 1
                exit_reason = "THRESHOLD_NEXT_OPEN"
                minutes_hit_to_flip = segment["end_flip_index"] - trigger_index
                later_values = [value for idx, value in aligned if idx > trigger_index]
                post_hit_extra = max(0.0, max(later_values, default=trigger_close_move) - trigger_close_move)
            exit_price = bars[exit_index]["open"]
            gross = direction * (exit_price - entry) / entry * 100
            advantage = gross - hold_gross if hit is not None else 0.0
            base = {
                **segment,
                "threshold_pct": threshold,
                "threshold_reached": hit is not None,
                "trigger_index": trigger_index,
                "trigger_time": bars[trigger_index]["time"] if trigger_index is not None else None,
                "trigger_close_move_pct": trigger_close_move,
                "threshold_exit_index": exit_index,
                "threshold_exit_time": bars[exit_index]["time"],
                "threshold_exit_price": exit_price,
                "exit_reason": exit_reason,
                "strategy_gross_pnl_pct": gross,
                "early_vs_hold_advantage_pct": advantage,
                "early_exit_better_than_hold": advantage > 0 if hit is not None else None,
                "max_favorable_close_move_pct": max_favorable,
                "post_hit_additional_favorable_pct": post_hit_extra,
                "minutes_hit_to_flip": minutes_hit_to_flip,
                "entry_commission_pct": commission_side_pct,
                "exit_commission_pct": commission_side_pct,
                "entry_slippage_pct": slippage_side_pct,
                "exit_slippage_pct": slippage_side_pct,
                "strategy_commission_only_pnl_pct": gross - 2 * commission_side_pct,
                "strategy_slippage_only_pnl_pct": gross - 2 * slippage_side_pct,
                "strategy_combined_net_pnl_pct": gross - 2 * (commission_side_pct + slippage_side_pct),
                "hold_commission_only_pnl_pct": hold_gross - 2 * commission_side_pct,
                "hold_slippage_only_pnl_pct": hold_gross - 2 * slippage_side_pct,
                "hold_combined_net_pnl_pct": hold_gross - 2 * (commission_side_pct + slippage_side_pct),
            }
            details.append(base)
    return details


def safe_mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def safe_median(values: list[float]) -> float | None:
    return median(values) if values else None


def summarize_group(rows: list[dict], universe: str) -> dict:
    reached = [row for row in rows if row["threshold_reached"]]
    advantage = [row["early_vs_hold_advantage_pct"] for row in reached]
    extra = [row["post_hit_additional_favorable_pct"] for row in reached]
    minutes = [row["minutes_hit_to_flip"] for row in reached]
    gross = [row["strategy_gross_pnl_pct"] for row in rows]
    hold_gross = [row["hold_gross_pnl_pct"] for row in rows]
    advantage_se = float(np.std(advantage, ddof=1) / np.sqrt(len(advantage))) if len(advantage) > 1 else None
    return {
        "universe": universe,
        "threshold_pct": rows[0]["threshold_pct"],
        "segments": len(rows),
        "reached_segments": len(reached),
        "reached_share_pct": len(reached) / len(rows) * 100,
        "early_better_count": sum(row["early_exit_better_than_hold"] for row in reached),
        "early_better_share_of_reached_pct": (
            sum(row["early_exit_better_than_hold"] for row in reached) / len(reached) * 100 if reached else None
        ),
        "early_better_share_of_all_pct": sum(row["early_exit_better_than_hold"] for row in reached) / len(rows) * 100,
        "mean_early_advantage_pct": safe_mean(advantage),
        "median_early_advantage_pct": safe_median(advantage),
        "mean_advantage_approx_95ci_low_pct": (
            safe_mean(advantage) - 1.96 * advantage_se if advantage_se is not None else None
        ),
        "mean_advantage_approx_95ci_high_pct": (
            safe_mean(advantage) + 1.96 * advantage_se if advantage_se is not None else None
        ),
        "total_hold_gross_pnl_pct": sum(hold_gross),
        "total_strategy_gross_pnl_pct": sum(gross),
        "total_result_change_gross_pct": sum(gross) - sum(hold_gross),
        "mean_strategy_gross_pnl_pct": safe_mean(gross),
        "total_strategy_commission_only_pnl_pct": sum(row["strategy_commission_only_pnl_pct"] for row in rows),
        "total_strategy_slippage_only_pnl_pct": sum(row["strategy_slippage_only_pnl_pct"] for row in rows),
        "total_strategy_combined_net_pnl_pct": sum(row["strategy_combined_net_pnl_pct"] for row in rows),
        "total_hold_combined_net_pnl_pct": sum(row["hold_combined_net_pnl_pct"] for row in rows),
        "total_result_change_combined_net_pct": sum(row["strategy_combined_net_pnl_pct"] for row in rows)
        - sum(row["hold_combined_net_pnl_pct"] for row in rows),
        "mean_post_hit_additional_favorable_pct": safe_mean(extra),
        "median_post_hit_additional_favorable_pct": safe_median(extra),
        "median_minutes_hit_to_flip": safe_median(minutes),
    }


def build_summary(details: list[dict]) -> list[dict]:
    result: list[dict] = []
    for threshold in THRESHOLDS_PCT:
        threshold_rows = [row for row in details if row["threshold_pct"] == threshold]
        groups = [
            ("ALL", threshold_rows),
            ("GREEN", [row for row in threshold_rows if row["color"] == "GREEN"]),
            ("RED", [row for row in threshold_rows if row["color"] == "RED"]),
            ("FIRST_HALF", [row for row in threshold_rows if row["period_half"] == "FIRST"]),
            ("SECOND_HALF", [row for row in threshold_rows if row["period_half"] == "SECOND"]),
        ]
        result.extend(summarize_group(rows, label) for label, rows in groups if rows)
    return result


def recommendation(summary: list[dict]) -> dict:
    by_key = {(row["threshold_pct"], row["universe"]): row for row in summary}
    candidates = []
    for threshold in THRESHOLDS_PCT:
        all_row = by_key[(threshold, "ALL")]
        first = by_key[(threshold, "FIRST_HALF")]
        second = by_key[(threshold, "SECOND_HALF")]
        green = by_key[(threshold, "GREEN")]
        red = by_key[(threshold, "RED")]
        n_hits = all_row["reached_segments"]
        stability_penalty = abs(first["mean_early_advantage_pct"] - second["mean_early_advantage_pct"])
        direction_penalty = abs(green["mean_early_advantage_pct"] - red["mean_early_advantage_pct"])
        # Prefer improvement that survives both temporal halves and is supported by at least 100 hits.
        robust = (
            n_hits >= 100
            and first["total_result_change_gross_pct"] > 0
            and second["total_result_change_gross_pct"] > 0
            and all_row["median_early_advantage_pct"] >= 0
            and all_row["mean_advantage_approx_95ci_low_pct"] > 0
        )
        score = (
            all_row["mean_early_advantage_pct"]
            - 0.35 * stability_penalty
            - 0.15 * direction_penalty
        ) * min(1.0, np.sqrt(n_hits / 100))
        candidates.append({
            "threshold_pct": threshold,
            "robust": robust,
            "score": float(score),
            "reached_segments": n_hits,
            "first_half_change_pct": first["total_result_change_gross_pct"],
            "second_half_change_pct": second["total_result_change_gross_pct"],
            "mean_advantage_pct": all_row["mean_early_advantage_pct"],
            "median_advantage_pct": all_row["median_early_advantage_pct"],
            "stability_penalty_pct": stability_penalty,
            "direction_penalty_pct": direction_penalty,
        })
    eligible = [row for row in candidates if row["robust"]]
    chosen = max(eligible, key=lambda row: row["score"]) if eligible else None
    exploratory_pool = [
        row for row in candidates
        if row["reached_segments"] >= 50
        and by_key[(row["threshold_pct"], "ALL")]["total_result_change_gross_pct"] > 0
    ]
    # If no threshold is robust, favor the broadest positive sample as a research candidate,
    # rather than selecting the largest in-sample result from a sparse tail.
    exploratory = max(exploratory_pool, key=lambda row: row["reached_segments"], default=None)
    return {
        "chosen": chosen,
        "exploratory_candidate": exploratory,
        "candidates": candidates,
        "robust_pool_nonempty": bool(eligible),
        "decision": "RECOMMEND" if chosen else "NO_ROBUST_THRESHOLD",
    }


def csv_value(value):
    if isinstance(value, (datetime,)):
        return value.isoformat(sep=" ")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(value) for key, value in row.items()})


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bars = read_bars(args.data)
    volatile_dates = read_dates(args.dates)
    flips, commission_fraction, flip_audit = read_flips_and_commission(args.signals)
    validate_alignment(bars, flips)
    segments, segment_audit = build_segments(bars, flips, volatile_dates)
    if not segments:
        raise ValueError("No eligible completed segments")
    halves = assign_period_halves(segments)
    commission_side_pct = commission_fraction * 100
    slippage_side_pct = args.slippage_bps_per_side / 100
    details = simulate(bars, segments, commission_side_pct, slippage_side_pct)
    summary = build_summary(details)
    rec = recommendation(summary)

    segment_rows = [{key: value for key, value in row.items() if key != "direction"} for row in segments]
    detail_rows = [{key: value for key, value in row.items() if key != "direction"} for row in details]
    write_csv(args.output_dir / "st_segments.csv", segment_rows)
    write_csv(args.output_dir / "st_impulse_threshold_details.csv", detail_rows)
    write_csv(args.output_dir / "st_impulse_threshold_summary.csv", summary)
    write_csv(args.output_dir / "st_impulse_recommendation_candidates.csv", rec["candidates"])

    metadata = {
        "inputs": {"data": str(args.data), "signals": str(args.signals), "dates": str(args.dates)},
        "method": {
            "supertrend": "ATR 80, multiplier 4",
            "session_filter": "both flip timestamps in [09:00, 19:00), same volatile date",
            "entry": "OPEN of minute after starting flip",
            "trigger": "first minute CLOSE with direction-aligned return from entry >= threshold",
            "threshold_exit": "OPEN of minute after trigger CLOSE",
            "hold_exit": "OPEN of minute after ending flip",
            "price_scale": "CSV <OPEN>/<CLOSE>; Signal Close Price alignment validated",
            "commission_pct_per_side": commission_side_pct,
            "slippage_pct_per_side_assumption": slippage_side_pct,
            "cost_note": "slippage absent from inputs; 1 bp/side stress assumption is configurable",
            "post_hit_extra": "max later favorable CLOSE move minus favorable move at trigger CLOSE, floored at zero",
            "result_aggregation": "additive sum of per-segment percentage returns; one position per segment",
        },
        "flip_audit": flip_audit,
        "segment_audit": segment_audit,
        "volatile_dates_in_file": len(volatile_dates),
        "period_halves": {key: csv_value(value) for key, value in halves.items()},
        "recommendation": rec,
    }
    with (args.output_dir / "analysis_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
    print(json.dumps(metadata, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
