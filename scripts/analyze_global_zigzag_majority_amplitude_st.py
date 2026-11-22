"""Assign a global ZZ leg to the ST segment that built >50% of its amplitude."""

from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from analyze_st_segment_zigzag_clusters import (
    DEFAULT_DATA,
    DEFAULT_DATES,
    DEFAULT_SIGNALS,
    build_segments,
    cluster_segments,
    read_bars,
    read_dates,
    read_flips,
    style,
    summary,
    validate,
    zigzag_legs,
)


DEFAULT_OUTPUT = Path("results_tester_st_batch_20260828/st80_4_global_zz0p2_over0p5_majority_amplitude_09_19.xlsx")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--dates", type=Path, default=DEFAULT_DATES)
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--zz-threshold-pct", type=float, default=0.2)
    parser.add_argument("--min-leg-pct", type=float, default=0.5)
    return parser.parse_args()


def amplitude_contributions(leg: dict, bars: list[dict], flip_indices: list[int]) -> tuple[dict[int, float], float]:
    contributions: dict[int, float] = defaultdict(float)
    running = leg["start_price"]
    for index in range(leg["start_index"] + 1, leg["end_index"] + 1):
        price = bars[index]["close"]
        increment = 0.0
        if leg["direction"] == "UP" and price > running:
            increment = price - running
            running = price
        elif leg["direction"] == "DOWN" and price < running:
            increment = running - price
            running = price
        if increment <= 0:
            continue
        position = bisect_right(flip_indices, index) - 1
        # Position -1 means amplitude accumulated before the first known ST
        # segment. It remains in the denominator and cannot win attribution.
        contributions[position] += increment
    full_amplitude = abs(leg["end_price"] - leg["start_price"])
    allocated = sum(contributions.values())
    if abs(allocated - full_amplitude) > max(1e-7, full_amplitude * 1e-10):
        raise ValueError(f"Amplitude allocation mismatch: {allocated} != {full_amplitude}")
    return dict(contributions), full_amplitude


def assign_legs(bars: list[dict], flips: list[dict], eligible_segments: list[dict], reversal_pct: float, min_leg_pct: float) -> tuple[list[dict], list[dict], dict]:
    global_legs = zigzag_legs([bar["close"] for bar in bars], 0, reversal_pct, min_leg_pct)
    flip_indices = [flip["index"] for flip in flips]
    eligible = {row["Segment ID"]: row for row in eligible_segments}
    all_counts = Counter()
    qualifying_by_segment = defaultdict(list)
    assigned, breakdown = [], []
    diagnostics = Counter()

    for global_leg_id, leg in enumerate(global_legs, start=1):
        contributions, amplitude = amplitude_contributions(leg, bars, flip_indices)
        valid = {position: value for position, value in contributions.items() if 0 <= position < len(flips) - 1}
        winner_position, winner_points = max(valid.items(), key=lambda item: item[1]) if valid else (None, 0.0)
        winner_share = winner_points / amplitude * 100 if amplitude else 0.0
        has_majority = winner_position is not None and winner_share > 50.0
        winner_segment_id = winner_position + 1 if has_majority else None

        if leg["qualifies"]:
            diagnostics["global_qualifying"] += 1
            diagnostics["majority" if has_majority else "no_majority"] += 1
        if has_majority and winner_segment_id in eligible:
            all_counts[winner_segment_id] += 1
        if not leg["qualifies"] or not has_majority or winner_segment_id not in eligible:
            continue

        segment = eligible[winner_segment_id]
        start_position = bisect_right(flip_indices, leg["start_index"]) - 1
        end_position = bisect_right(flip_indices, leg["end_index"]) - 1
        item = {
            "Global Leg ID": global_leg_id,
            "Assigned Segment ID": winner_segment_id,
            "Assigned ST Color": segment["ST Color"],
            "Leg Start Time": bars[leg["start_index"]]["time"],
            "Leg End Time": bars[leg["end_index"]]["time"],
            "Confirmation Time": bars[leg["confirmation_index"]]["time"],
            "Direction": leg["direction"],
            "Start Price": leg["start_price"],
            "End Price": leg["end_price"],
            "Size %": leg["size_pct"],
            "Full Amplitude Points": amplitude,
            "Winning Contribution Points": winner_points,
            "Winning Contribution %": winner_share,
            "Contributing ST Segments": sum(position >= 0 and value > 0 for position, value in contributions.items()),
            "Start ST Color": flips[start_position]["after"] if 0 <= start_position < len(flips) else "BEFORE_FIRST_FLIP",
            "End ST Color": flips[end_position]["after"] if 0 <= end_position < len(flips) else "OUTSIDE",
            "Winner Is End Segment": winner_position == end_position,
            "Confirmation After Assigned Segment": leg["confirmation_index"] >= segment["End Index"],
        }
        assigned.append(item)
        qualifying_by_segment[winner_segment_id].append(item)
        diagnostics["selected_assigned"] += 1

        for position, points in sorted(contributions.items()):
            if points <= 0:
                continue
            breakdown.append({
                "Global Leg ID": global_leg_id,
                "Assigned Segment ID": winner_segment_id,
                "Contribution Segment ID": position + 1 if position >= 0 else None,
                "Contribution ST Color": flips[position]["after"] if 0 <= position < len(flips) else "OUTSIDE",
                "Contribution Points": points,
                "Contribution %": points / amplitude * 100,
                "Is Majority Winner": position == winner_position,
            })

    for segment in eligible_segments:
        rows = qualifying_by_segment[segment["Segment ID"]]
        segment["All Confirmed ZZ Legs"] = all_counts[segment["Segment ID"]]
        segment["Qualifying ZZ Legs"] = len(rows)
        segment["Sum Qualifying ZZ Legs %"] = sum(row["Size %"] for row in rows)
        segment["Max ZZ Leg %"] = max((row["Size %"] for row in rows), default=0.0)
    return assigned, breakdown, dict(diagnostics)


def add_sheet(book: Workbook, name: str, rows: list[dict]) -> None:
    sheet = book.create_sheet(name)
    headers = list(rows[0])
    sheet.append(headers)
    for row in rows:
        sheet.append([row[key] for key in headers])


def write_report(paths: argparse.Namespace, segments: list[dict], legs: list[dict], breakdown: list[dict], summary_rows: list[tuple], diagnostics: list[dict], profiles: list[dict]) -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "Summary"
    sheet.append(["Metric", "Value", "Unit"])
    for row in summary_rows:
        sheet.append(row)

    distribution = book.create_sheet("Count_Distribution")
    distribution.append(["Qualifying ZZ Legs", "Segments", "Share %"])
    counter = Counter(row["Qualifying ZZ Legs"] for row in segments)
    for count in range(max(counter) + 1):
        distribution.append([count, counter[count], counter[count] / len(segments) * 100])

    add_sheet(book, "Cluster_Profiles", profiles)
    add_sheet(book, "Cluster_Diagnostics", diagnostics)
    add_sheet(book, "Segments", segments)
    add_sheet(book, "Assigned_Legs", legs)
    add_sheet(book, "Amplitude_Breakdown", breakdown)

    method = book.create_sheet("Methodology")
    method.append(["Parameter", "Value"])
    for row in [
        ("OHLC source", str(paths.data.resolve())),
        ("ST signals source", str(paths.signals.resolve())),
        ("Date source", str(paths.dates.resolve())),
        ("SuperTrend", "ATR 80, multiplier 4.0; flips from Signals sheet"),
        ("Eligible assigned segment", "Both neighboring flips on the same selected date and in [09:00, 19:00)"),
        ("ZigZag", f"One continuous close-based ZigZag over full history; reversal {paths.zz_threshold_pct:g}%"),
        ("Qualifying leg", f"Confirmed global ZZ leg strictly greater than {paths.min_leg_pct:g}%"),
        ("Amplitude allocation", "Each new Close maximum (UP) or minimum (DOWN) increment is credited to its half-open ST segment [start flip, next flip)"),
        ("Assignment", "Exactly one ST segment must accumulate strictly >50% of the leg's full Close-to-Close amplitude"),
        ("No majority", "Leg remains NO_MAJORITY and is not forced into any segment"),
        ("Double counting", "None: each qualifying leg has at most one assigned segment"),
        ("Confirmation", "May occur after the assigned segment; amplitude formation determines attribution"),
    ]:
        method.append(row)

    for current in book.worksheets:
        style(current)
        for row in current.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm"
    paths.output.parent.mkdir(parents=True, exist_ok=True)
    book.save(paths.output)


def main() -> None:
    paths = parse_args()
    dates = read_dates(paths.dates)
    bars = read_bars(paths.data)
    flips, params = read_flips(paths.signals)
    validate(bars, flips, params)
    segments, _ = build_segments(bars, flips, dates, paths.zz_threshold_pct, paths.min_leg_pct)
    legs, breakdown, allocation = assign_legs(bars, flips, segments, paths.zz_threshold_pct, paths.min_leg_pct)
    diagnostics, profiles = cluster_segments(segments)
    summary_rows = summary(segments, dates, legs, paths.min_leg_pct)
    summary_rows.extend([
        ("Global qualifying legs across full history", allocation.get("global_qualifying", 0), "legs"),
        ("Global qualifying legs with a >50% majority", allocation.get("majority", 0), "legs"),
        ("Global qualifying legs with NO_MAJORITY", allocation.get("no_majority", 0), "legs"),
        ("NO_MAJORITY share globally", allocation.get("no_majority", 0) / allocation.get("global_qualifying", 1) * 100, "%"),
        ("Assigned selected legs whose winner is end segment", sum(row["Winner Is End Segment"] for row in legs), "legs"),
        ("Winner-is-end-segment share", sum(row["Winner Is End Segment"] for row in legs) / len(legs) * 100 if legs else 0, "%"),
        ("Mean winning amplitude share", sum(row["Winning Contribution %"] for row in legs) / len(legs) if legs else 0, "%"),
    ])
    write_report(paths, segments, legs, breakdown, summary_rows, diagnostics, profiles)
    result = {
        "summary": {name: value for name, value, _ in summary_rows},
        "distribution": dict(sorted(Counter(row["Qualifying ZZ Legs"] for row in segments).items())),
        "cluster_diagnostics": diagnostics,
        "cluster_profiles": profiles,
        "output": str(paths.output.resolve()),
    }
    paths.output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
