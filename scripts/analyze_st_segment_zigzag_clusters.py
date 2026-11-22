"""Count large ZigZag legs inside intraday SuperTrend flip-to-flip segments."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import datetime, time
from pathlib import Path
from statistics import fmean, median

import numpy as np
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill


DEFAULT_DATA = Path(r"D:\Si\минута\28.08\data.csv")
DEFAULT_DATES = Path(r"D:\Si\минута\vol data.xlsx")
DEFAULT_SIGNALS = Path(r"D:\Si\минута\28.08\test_result_legacy_20260828_133527.xlsx")
DEFAULT_OUTPUT = Path("results_tester_st_batch_20260828/st80_4_zz0p15_over0p4_clusters_09_19.xlsx")
SESSION_START = time(9, 0)
SESSION_END = time(19, 0)


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--dates", type=Path, default=DEFAULT_DATES)
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--zz-threshold-pct", type=float, default=0.15)
    parser.add_argument("--min-leg-pct", type=float, default=0.4)
    return parser.parse_args()


def read_dates(path: Path) -> set:
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book.active
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows)
        column = headers.index("Дата сессии")
        return {row[column].date() for row in rows if row[column] is not None}
    finally:
        book.close()


def read_bars(path: Path) -> list[dict]:
    result = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle, delimiter=";")):
            result.append({
                "index": index,
                "time": datetime.strptime(row["<DATE>"] + row["<TIME>"], "%Y%m%d%H%M%S"),
                "open": float(row["<OPEN>"]),
                "high": float(row["<HIGH>"]),
                "low": float(row["<LOW>"]),
                "close": float(row["<CLOSE>"]),
                "volume": float(row.get("<VOL>") or 0),
            })
    return result


def read_flips(path: Path) -> tuple[list[dict], dict]:
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        summary = list(book["Summary"].iter_rows(min_row=1, max_row=2, values_only=True))
        params = dict(zip(summary[0], summary[1]))
        rows = book["Signals"].iter_rows(values_only=True)
        headers = next(rows)
        col = {name: idx for idx, name in enumerate(headers)}
        unique = {}
        for row in rows:
            if not row[col["Is Reversal"]]:
                continue
            index = int(row[col["Signal Bar Index"]])
            value = {
                "index": index,
                "time": row[col["Signal Time"]],
                "close": float(row[col["Signal Close Price"]]),
                "before": row[col["ST Color Before"]],
                "after": row[col["ST Color After"]],
            }
            if index in unique and unique[index] != value:
                raise ValueError(f"Conflicting flip duplicates at bar {index}")
            unique[index] = value
        return [unique[idx] for idx in sorted(unique)], params
    finally:
        book.close()


def validate(bars: list[dict], flips: list[dict], params: dict) -> None:
    if int(params["ATR Period"]) != 80 or float(params["Multiplier"]) != 4.0:
        raise ValueError(f"Expected ST 80/4, got {params['ATR Period']}/{params['Multiplier']}")
    for flip in flips:
        bar = bars[flip["index"]]
        if bar["time"] != flip["time"] or abs(bar["close"] - flip["close"]) > 1e-9:
            raise ValueError(f"CSV/Signals mismatch at bar {flip['index']}")


def zigzag_legs(closes: list[float], offset: int, reversal_pct: float, min_leg_pct: float) -> list[dict]:
    """Confirmed close-based ZZ legs; the final unconfirmed swing is excluded."""
    if len(closes) < 2:
        return []
    state = 0
    low = high = closes[0]
    low_idx = high_idx = 0
    pivots = []

    def pivot(index: int, kind: str, confirmation: int) -> None:
        pivots.append((index, closes[index], kind, confirmation))

    for index, price in enumerate(closes[1:], start=1):
        if state == 0:
            if price > high:
                high, high_idx = price, index
            if price < low:
                low, low_idx = price, index
            if price >= low * (1 + reversal_pct / 100):
                pivot(low_idx, "LOW", index)
                state, high, high_idx = 1, price, index
            elif price <= high * (1 - reversal_pct / 100):
                pivot(high_idx, "HIGH", index)
                state, low, low_idx = -1, price, index
        elif state == 1:
            if price >= high:
                high, high_idx = price, index
            elif price <= high * (1 - reversal_pct / 100):
                pivot(high_idx, "HIGH", index)
                state, low, low_idx = -1, price, index
        else:
            if price <= low:
                low, low_idx = price, index
            elif price >= low * (1 + reversal_pct / 100):
                pivot(low_idx, "LOW", index)
                state, high, high_idx = 1, price, index

    legs = []
    for start, end in zip(pivots, pivots[1:]):
        pct = abs(end[1] - start[1]) / start[1] * 100
        legs.append({
            "start_index": offset + start[0],
            "end_index": offset + end[0],
            "confirmation_index": offset + end[3],
            "direction": "UP" if end[1] > start[1] else "DOWN",
            "start_price": start[1],
            "end_price": end[1],
            "size_pct": pct,
            "qualifies": pct > min_leg_pct,
        })
    return legs


def build_segments(bars: list[dict], flips: list[dict], dates: set, reversal_pct: float, min_leg_pct: float) -> tuple[list[dict], list[dict]]:
    segments, qualifying_legs = [], []
    for segment_id, (start, end) in enumerate(zip(flips, flips[1:]), start=1):
        if start["after"] != end["before"]:
            raise ValueError(f"ST color continuity error at {start['index']}..{end['index']}")
        day = start["time"].date()
        if day != end["time"].date() or day not in dates:
            continue
        if not (SESSION_START <= start["time"].time() < SESSION_END):
            continue
        if not (SESSION_START <= end["time"].time() < SESSION_END):
            continue

        window = bars[start["index"]:end["index"] + 1]
        legs = zigzag_legs([bar["close"] for bar in window], start["index"], reversal_pct, min_leg_pct)
        large = [leg for leg in legs if leg["qualifies"]]
        for leg_number, leg in enumerate(large, start=1):
            qualifying_legs.append({
                "Segment ID": segment_id,
                "Leg Number in Segment": leg_number,
                "ST Color": start["after"],
                "Start Time": bars[leg["start_index"]]["time"],
                "End Time": bars[leg["end_index"]]["time"],
                "Confirmation Time": bars[leg["confirmation_index"]]["time"],
                "Direction": leg["direction"],
                "Start Price": leg["start_price"],
                "End Price": leg["end_price"],
                "Size %": leg["size_pct"],
            })

        high = max(bar["high"] for bar in window)
        low = min(bar["low"] for bar in window)
        total_large_pct = sum(leg["size_pct"] for leg in large)
        segments.append({
            "Segment ID": segment_id,
            "Date": day,
            "ST Color": start["after"],
            "Start Flip Time": start["time"],
            "End Flip Time": end["time"],
            "Start Index": start["index"],
            "End Index": end["index"],
            "Duration Bars": end["index"] - start["index"],
            "Start Close": start["close"],
            "End Close": end["close"],
            "ST Range %": (high - low) / start["close"] * 100,
            "Close Change Abs %": abs(end["close"] - start["close"]) / start["close"] * 100,
            "All Confirmed ZZ Legs": len(legs),
            "Qualifying ZZ Legs": len(large),
            "Sum Qualifying ZZ Legs %": total_large_pct,
            "Max ZZ Leg %": max((leg["size_pct"] for leg in large), default=0.0),
        })
    return segments, qualifying_legs


def kmeans(x: np.ndarray, k: int, seed: int = 20260830) -> tuple[np.ndarray, np.ndarray, float]:
    rng = np.random.default_rng(seed + k)
    best = None
    for _ in range(20):
        centers = [x[rng.integers(len(x))]]
        for _ in range(1, k):
            dist2 = np.min(((x[:, None, :] - np.array(centers)[None, :, :]) ** 2).sum(2), axis=1)
            centers.append(x[rng.choice(len(x), p=dist2 / dist2.sum())] if dist2.sum() else x[rng.integers(len(x))])
        centers = np.array(centers)
        for _ in range(100):
            labels = ((x[:, None, :] - centers[None, :, :]) ** 2).sum(2).argmin(1)
            new = np.array([x[labels == idx].mean(0) if np.any(labels == idx) else centers[idx] for idx in range(k)])
            if np.max(np.abs(new - centers)) < 1e-7:
                centers = new
                break
            centers = new
        inertia = float(np.sum((x - centers[labels]) ** 2))
        if best is None or inertia < best[2]:
            best = labels.copy(), centers.copy(), inertia
    return best


def silhouette_sample(x: np.ndarray, labels: np.ndarray, seed: int = 20260830) -> float:
    rng = np.random.default_rng(seed)
    sample = rng.choice(len(x), size=min(1500, len(x)), replace=False)
    values = []
    for idx in sample:
        distances = np.sqrt(np.sum((x - x[idx]) ** 2, axis=1))
        same = labels == labels[idx]
        a = distances[same].sum() / max(1, same.sum() - 1)
        b = min(distances[labels == other].mean() for other in set(labels) if other != labels[idx])
        values.append((b - a) / max(a, b) if max(a, b) else 0.0)
    return float(np.mean(values))


def cluster_segments(segments: list[dict]) -> tuple[list[dict], list[dict]]:
    names = ["Duration Bars", "ST Range %", "Close Change Abs %", "Qualifying ZZ Legs", "Sum Qualifying ZZ Legs %"]
    raw = np.array([[float(row[name]) for name in names] for row in segments])
    transformed = raw.copy()
    transformed[:, 0] = np.log1p(transformed[:, 0])
    transformed[:, 3] = np.log1p(transformed[:, 3])
    transformed[:, 4] = np.log1p(transformed[:, 4])
    mean = transformed.mean(0)
    std = transformed.std(0)
    x = (transformed - mean) / np.where(std == 0, 1, std)

    diagnostics = []
    candidates = {}
    for k in range(2, 7):
        labels, centers, inertia = kmeans(x, k)
        score = silhouette_sample(x, labels)
        diagnostics.append({"K": k, "Inertia": inertia, "Silhouette (sample <=1500)": score})
        candidates[k] = (labels, centers)
    best_k = max(diagnostics, key=lambda row: row["Silhouette (sample <=1500)"])["K"]
    labels = candidates[best_k][0]

    # Stable labels ordered from quiet/simple to active/complex.
    order = sorted(range(best_k), key=lambda label: (
        raw[labels == label, 3].mean(), raw[labels == label, 1].mean(), raw[labels == label, 0].mean()
    ))
    remap = {old: new + 1 for new, old in enumerate(order)}
    for row, label in zip(segments, labels):
        row["Cluster"] = remap[int(label)]

    profiles = []
    for cluster in range(1, best_k + 1):
        rows = [row for row in segments if row["Cluster"] == cluster]
        counts = [row["Qualifying ZZ Legs"] for row in rows]
        profiles.append({
            "Cluster": cluster,
            "Segments": len(rows),
            "Share %": len(rows) / len(segments) * 100,
            "Mean Duration Bars": fmean(row["Duration Bars"] for row in rows),
            "Mean ST Range %": fmean(row["ST Range %"] for row in rows),
            "Mean Close Change Abs %": fmean(row["Close Change Abs %"] for row in rows),
            "Mean Qualifying ZZ Legs": fmean(counts),
            "Median Qualifying ZZ Legs": median(counts),
            "Segments with >=1 Large Leg %": sum(value >= 1 for value in counts) / len(rows) * 100,
            "Mean Sum Large Legs %": fmean(row["Sum Qualifying ZZ Legs %"] for row in rows),
            "Green Share %": sum(row["ST Color"] == "GREEN" for row in rows) / len(rows) * 100,
        })
    return diagnostics, profiles


def percentile(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q))


def summary(segments: list[dict], dates: set, legs: list[dict], min_leg_pct: float) -> list[tuple]:
    counts = [row["Qualifying ZZ Legs"] for row in segments]
    active = [value for value in counts if value > 0]
    return [
        ("Selected dates", len(dates), "dates"),
        ("Completed intraday ST segments", len(segments), "segments"),
        (f"Mean ZZ legs >{min_leg_pct:g}% per ST segment", fmean(counts), "legs/segment"),
        (f"Median ZZ legs >{min_leg_pct:g}% per ST segment", median(counts), "legs/segment"),
        ("P75 count", percentile(counts, 0.75), "legs/segment"),
        ("P90 count", percentile(counts, 0.90), "legs/segment"),
        ("Maximum count", max(counts), "legs/segment"),
        ("Segments with zero qualifying legs", sum(v == 0 for v in counts), "segments"),
        ("Zero-leg segment share", sum(v == 0 for v in counts) / len(counts) * 100, "%"),
        ("Segments with >=1 qualifying leg", len(active), "segments"),
        ("Mean count conditional on >=1", fmean(active) if active else 0, "legs/active segment"),
        ("Total qualifying ZZ legs", len(legs), "legs"),
        ("Mean ST segment range", fmean(row["ST Range %"] for row in segments), "%"),
        ("Mean segment duration", fmean(row["Duration Bars"] for row in segments), "minutes"),
    ]


def style(sheet) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(28, max(11, max(len(str(cell.value or "")) for cell in column) + 2))


def write(output: Path, segments: list[dict], legs: list[dict], summary_rows: list[tuple], diagnostics: list[dict], profiles: list[dict], paths: argparse.Namespace) -> None:
    book = Workbook()
    ws = book.active
    ws.title = "Summary"
    ws.append(["Metric", "Value", "Unit"])
    for row in summary_rows:
        ws.append(row)

    dist = book.create_sheet("Count_Distribution")
    dist.append(["Qualifying ZZ Legs", "Segments", "Share %"])
    counter = Counter(row["Qualifying ZZ Legs"] for row in segments)
    for value in range(max(counter) + 1):
        dist.append([value, counter[value], counter[value] / len(segments) * 100])

    for title, rows in (("Cluster_Profiles", profiles), ("Cluster_Diagnostics", diagnostics), ("Segments", segments), ("Qualifying_ZZ_Legs", legs)):
        sheet = book.create_sheet(title)
        headers = list(rows[0])
        sheet.append(headers)
        for row in rows:
            sheet.append([row[key] for key in headers])

    method = book.create_sheet("Methodology")
    method.append(["Parameter", "Value"])
    for row in [
        ("OHLC source", str(paths.data.resolve())), ("ST signals source", str(paths.signals.resolve())),
        ("Date source", str(paths.dates.resolve())), ("SuperTrend", "ATR 80, multiplier 4.0; flips from Signals sheet"),
        ("Session filter", "Both neighboring flips on the same selected date and in [09:00, 19:00)"),
        ("Segment boundary", "Start-flip Close through end-flip Close, inclusive"),
        ("ZigZag", f"Close-based, {paths.zz_threshold_pct:g}% reversal; initialized independently in every ST segment"),
        ("Qualifying leg", f"Confirmed ZZ leg strictly greater than {paths.min_leg_pct:g}%; unfinished final swing excluded"),
        ("Clustering", "K-means; standardized log-duration/count/sum plus range and close change; K=2..6 chosen by sampled silhouette"),
    ]:
        method.append(row)

    for sheet in book.worksheets:
        style(sheet)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm"
    output.parent.mkdir(parents=True, exist_ok=True)
    book.save(output)


def main() -> None:
    paths = args()
    dates = read_dates(paths.dates)
    bars = read_bars(paths.data)
    flips, params = read_flips(paths.signals)
    validate(bars, flips, params)
    segments, legs = build_segments(bars, flips, dates, paths.zz_threshold_pct, paths.min_leg_pct)
    if not segments:
        raise ValueError("No segments passed the date/time filters")
    diagnostics, profiles = cluster_segments(segments)
    summary_rows = summary(segments, dates, legs, paths.min_leg_pct)
    write(paths.output, segments, legs, summary_rows, diagnostics, profiles, paths)
    result = {
        "summary": {key: value for key, value, _ in summary_rows},
        "distribution": dict(sorted(Counter(row["Qualifying ZZ Legs"] for row in segments).items())),
        "cluster_diagnostics": diagnostics,
        "cluster_profiles": profiles,
        "output": str(paths.output.resolve()),
    }
    json_path = paths.output.with_suffix(".json")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
