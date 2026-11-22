"""Assign continuous global ZigZag legs to the ST segment where each leg ends."""

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


DEFAULT_OUTPUT = Path("results_tester_st_batch_20260828/st80_4_global_zz0p2_over0p5_by_end_segment_09_19.xlsx")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--dates", type=Path, default=DEFAULT_DATES)
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--zz-threshold-pct", type=float, default=0.2)
    parser.add_argument("--min-leg-pct", type=float, default=0.5)
    return parser.parse_args()


def assign_global_legs(bars: list[dict], flips: list[dict], eligible_segments: list[dict], reversal_pct: float, min_leg_pct: float) -> list[dict]:
    global_legs = zigzag_legs([bar["close"] for bar in bars], 0, reversal_pct, min_leg_pct)
    flip_indices = [flip["index"] for flip in flips]
    eligible = {row["Segment ID"]: row for row in eligible_segments}
    assigned = []
    all_counts = Counter()
    qualifying_by_segment = defaultdict(list)

    for leg in global_legs:
        # Half-open ST ownership: [start flip, next flip). A pivot exactly on a
        # flip bar belongs to the new color confirmed at that bar.
        end_position = bisect_right(flip_indices, leg["end_index"]) - 1
        if end_position < 0 or end_position >= len(flips) - 1:
            continue
        segment_id = end_position + 1
        if segment_id not in eligible:
            continue
        all_counts[segment_id] += 1
        if not leg["qualifies"]:
            continue
        start_position = bisect_right(flip_indices, leg["start_index"]) - 1
        segment = eligible[segment_id]
        item = {
            "Segment ID": segment_id,
            "Assigned ST Color": segment["ST Color"],
            "Leg Start Time": bars[leg["start_index"]]["time"],
            "Leg End Time": bars[leg["end_index"]]["time"],
            "Confirmation Time": bars[leg["confirmation_index"]]["time"],
            "Leg Direction": leg["direction"],
            "Start Price": leg["start_price"],
            "End Price": leg["end_price"],
            "Size %": leg["size_pct"],
            "Start ST Color": flips[start_position]["after"] if 0 <= start_position < len(flips) else "BEFORE_FIRST_FLIP",
            "End ST Color": segment["ST Color"],
            "Crossed ST Flips": max(0, end_position - start_position),
            "Started Outside Assigned Segment": leg["start_index"] < segment["Start Index"],
            "Confirmed After Assigned Segment": leg["confirmation_index"] >= segment["End Index"],
        }
        assigned.append(item)
        qualifying_by_segment[segment_id].append(item)

    for segment in eligible_segments:
        rows = qualifying_by_segment[segment["Segment ID"]]
        segment["All Confirmed ZZ Legs"] = all_counts[segment["Segment ID"]]
        segment["Qualifying ZZ Legs"] = len(rows)
        segment["Sum Qualifying ZZ Legs %"] = sum(row["Size %"] for row in rows)
        segment["Max ZZ Leg %"] = max((row["Size %"] for row in rows), default=0.0)
    return assigned


def add_sheet(book: Workbook, name: str, rows: list[dict]) -> None:
    sheet = book.create_sheet(name)
    headers = list(rows[0])
    sheet.append(headers)
    for row in rows:
        sheet.append([row[key] for key in headers])


def write_report(paths: argparse.Namespace, segments: list[dict], legs: list[dict], summary_rows: list[tuple], diagnostics: list[dict], profiles: list[dict]) -> None:
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
    add_sheet(book, "Assigned_Global_ZZ_Legs", legs)

    method = book.create_sheet("Methodology")
    method.append(["Parameter", "Value"])
    for row in [
        ("OHLC source", str(paths.data.resolve())),
        ("ST signals source", str(paths.signals.resolve())),
        ("Date source", str(paths.dates.resolve())),
        ("SuperTrend", "ATR 80, multiplier 4.0; flips from Signals sheet"),
        ("Eligible ST segment", "Both neighboring flips on the same selected date and in [09:00, 19:00)"),
        ("ZigZag", f"One continuous close-based ZigZag over the full history; reversal {paths.zz_threshold_pct:g}%"),
        ("Qualifying leg", f"Confirmed global ZZ leg strictly greater than {paths.min_leg_pct:g}%"),
        ("Ownership", "Leg assigned by its ending pivot to the half-open ST segment [start flip, next flip)"),
        ("Exact flip boundary", "An ending pivot exactly on a flip bar belongs to the new ST color"),
        ("Cross-segment leg", "Kept in full and assigned to the segment/color containing its ending pivot"),
        ("Confirmation", "May occur after the assigned segment; endpoint determines ownership, not confirmation time"),
        ("Clustering", "Same standardized K-means K=2..6 selection by sampled silhouette as the local-ZZ report"),
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
    legs = assign_global_legs(bars, flips, segments, paths.zz_threshold_pct, paths.min_leg_pct)
    diagnostics, profiles = cluster_segments(segments)
    summary_rows = summary(segments, dates, legs, paths.min_leg_pct)
    cross = [row for row in legs if row["Started Outside Assigned Segment"]]
    delayed = [row for row in legs if row["Confirmed After Assigned Segment"]]
    summary_rows.extend([
        ("Qualifying legs started in another ST segment", len(cross), "legs"),
        ("Cross-segment qualifying leg share", len(cross) / len(legs) * 100 if legs else 0, "%"),
        ("Qualifying legs confirmed after assigned segment", len(delayed), "legs"),
    ])
    write_report(paths, segments, legs, summary_rows, diagnostics, profiles)
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
