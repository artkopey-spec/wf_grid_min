from __future__ import annotations

import csv
import json
import math
from datetime import datetime, time
from pathlib import Path
from statistics import mean, median

import openpyxl


ROOT = Path(r"C:\3.1_wf_grid")
RESULTS = ROOT / "results_tester_st_batch_20260828"
DATA = ROOT / "data.csv"
DATES_FILE = RESULTS / "vol data.xlsx"
OUTPUT = RESULTS / "zigzag_0p2_222_volatile_09_19_analysis.json"
THRESHOLD = 0.002
START = time(9, 0)
END = time(19, 0)


def read_selected_dates() -> set:
    wb = openpyxl.load_workbook(DATES_FILE, read_only=True, data_only=True)
    ws = wb.active
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    date_idx = headers.index("Дата сессии")
    dates = {
        row[date_idx].value.date()
        for row in ws.iter_rows(min_row=2)
        if row[date_idx].value is not None
    }
    wb.close()
    return dates


def read_bars() -> list[dict]:
    rows = []
    with DATA.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle, delimiter=";")):
            rows.append(
                {
                    "index": index,
                    "time": datetime.strptime(row["<DATE>"] + row["<TIME>"], "%Y%m%d%H%M%S"),
                    "open": float(row["<OPEN>"]),
                    "high": float(row["<HIGH>"]),
                    "low": float(row["<LOW>"]),
                    "close": float(row["<CLOSE>"]),
                }
            )
    return rows


def build_pivots(bars: list[dict]) -> list[dict]:
    first = bars[0]
    state = 0
    low = high = first["close"]
    low_idx = high_idx = 0
    pivots: list[dict] = []

    def add_pivot(index: int, kind: str, confirmation_index: int) -> None:
        bar = bars[index]
        pivots.append(
            {
                "index": index,
                "time": bar["time"],
                "price": bar["close"],
                "kind": kind,
                "confirmation_index": confirmation_index,
                "confirmation_time": bars[confirmation_index]["time"],
            }
        )

    for index in range(1, len(bars)):
        price = bars[index]["close"]
        if state == 0:
            if price > high:
                high, high_idx = price, index
            if price < low:
                low, low_idx = price, index
            if price >= low * (1.0 + THRESHOLD):
                add_pivot(low_idx, "LOW", index)
                state = 1
                high, high_idx = price, index
            elif price <= high * (1.0 - THRESHOLD):
                add_pivot(high_idx, "HIGH", index)
                state = -1
                low, low_idx = price, index
        elif state == 1:
            if price >= high:
                high, high_idx = price, index
            elif price <= high * (1.0 - THRESHOLD):
                add_pivot(high_idx, "HIGH", index)
                state = -1
                low, low_idx = price, index
        else:
            if price <= low:
                low, low_idx = price, index
            elif price >= low * (1.0 + THRESHOLD):
                add_pivot(low_idx, "LOW", index)
                state = 1
                high, high_idx = price, index
    return pivots


def build_legs(pivots: list[dict], selected_dates: set) -> list[dict]:
    legs = []
    for start, end in zip(pivots, pivots[1:]):
        if start["time"].date() != end["time"].date():
            continue
        if start["time"].date() not in selected_dates:
            continue
        if not (START <= start["time"].time() < END and START <= end["time"].time() < END):
            continue
        # A ZigZag extremum exists operationally only after the reversal that
        # confirms it. For a strict time-filtered sample, that confirmation
        # must also occur before 19:00 on the same selected session date.
        if end["confirmation_time"].date() != end["time"].date():
            continue
        if not (START <= end["confirmation_time"].time() < END):
            continue
        points = abs(end["price"] - start["price"])
        legs.append(
            {
                "start_index": start["index"],
                "end_index": end["index"],
                "start_time": start["time"],
                "end_time": end["time"],
                "confirmation_time": end["confirmation_time"],
                "start_price": start["price"],
                "end_price": end["price"],
                "direction": "UP" if end["price"] > start["price"] else "DOWN",
                "points": points,
                "pct": points / start["price"] * 100.0,
            }
        )
    return legs


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def top_groups(legs: list[dict]) -> list[dict]:
    ordered = sorted(legs, key=lambda row: row["points"], reverse=True)
    groups = [("Вся выборка", 1.0), ("Верхние 50%", 0.5), ("Верхние 30%", 0.3), ("Верхние 10%", 0.1), ("Верхние 5%", 0.05), ("Верхний 1%", 0.01)]
    result = []
    for label, share in groups:
        count = len(ordered) if share == 1.0 else math.ceil(len(ordered) * share)
        rows = ordered[:count]
        result.append(
            {
                "segment": label,
                "count": count,
                "mean_points": mean(row["points"] for row in rows),
                "median_points": median(row["points"] for row in rows),
                "mean_pct": mean(row["pct"] for row in rows),
                "median_pct": median(row["pct"] for row in rows),
                "entry_threshold_points": min(row["points"] for row in rows),
            }
        )
    return result


def frequency(legs: list[dict], selected_dates: set) -> list[dict]:
    result = []
    for threshold in (1.0, 1.5, 2.0):
        rows = [row for row in legs if row["pct"] > threshold]
        by_date: dict = {}
        for row in rows:
            day = row["end_time"].date()
            by_date[day] = by_date.get(day, 0) + 1
        result.append(
            {
                "threshold_pct": threshold,
                "count": len(rows),
                "share_pct": len(rows) / len(legs) * 100.0,
                "per_volatile_day": len(rows) / len(selected_dates),
                "per_5_volatile_days": len(rows) / len(selected_dates) * 5.0,
                "dates_with_event": len(by_date),
                "share_dates_pct": len(by_date) / len(selected_dates) * 100.0,
                "max_per_date": max(by_date.values()) if by_date else 0,
                "volatile_days_per_event": len(selected_dates) / len(rows) if rows else None,
            }
        )
    return result


def corrections(legs: list[dict], bars: list[dict]) -> dict:
    large = [row for row in legs if row["pct"] > 1.0]
    ratios = (0.33, 0.50, 0.90)
    hits = {ratio: [] for ratio in ratios}
    directions = {ratio: {"UP": 0, "DOWN": 0} for ratio in ratios}
    totals = {
        "UP": sum(row["direction"] == "UP" for row in large),
        "DOWN": sum(row["direction"] == "DOWN" for row in large),
    }

    for leg in large:
        session_day = leg["end_time"].date()
        leg_size = leg["points"]
        first_hit = {ratio: None for ratio in ratios}
        index = leg["end_index"] + 1
        while index < len(bars):
            bar = bars[index]
            if bar["time"].date() != session_day or bar["time"].time() >= END:
                break
            for ratio in ratios:
                if first_hit[ratio] is not None:
                    continue
                if leg["direction"] == "UP":
                    level = leg["end_price"] - ratio * leg_size
                    touched = bar["low"] <= level
                else:
                    level = leg["end_price"] + ratio * leg_size
                    touched = bar["high"] >= level
                if touched:
                    elapsed_minutes = (bar["time"] - leg["end_time"]).total_seconds() / 60.0
                    first_hit[ratio] = elapsed_minutes
            if all(first_hit[ratio] is not None for ratio in ratios):
                break
            index += 1
        for ratio in ratios:
            if first_hit[ratio] is not None:
                hits[ratio].append(first_hit[ratio])
                directions[ratio][leg["direction"]] += 1

    result = {
        "sample_count": len(large),
        "direction_totals": totals,
        "window": "from ending extreme to 19:00 of the same selected session",
        "levels": [],
    }
    for ratio in ratios:
        values = hits[ratio]
        result["levels"].append(
            {
                "ratio_pct": ratio * 100.0,
                "hits": len(values),
                "probability_pct": len(values) / len(large) * 100.0 if large else 0.0,
                "misses": len(large) - len(values),
                "median_minutes": median(values) if values else None,
                "mean_minutes": mean(values) if values else None,
                "within_2h": sum(value <= 120 for value in values),
                "within_2h_pct_of_sample": sum(value <= 120 for value in values) / len(large) * 100.0 if large else 0.0,
                "up_hits": directions[ratio]["UP"],
                "down_hits": directions[ratio]["DOWN"],
                "up_probability_pct": directions[ratio]["UP"] / totals["UP"] * 100.0 if totals["UP"] else 0.0,
                "down_probability_pct": directions[ratio]["DOWN"] / totals["DOWN"] * 100.0 if totals["DOWN"] else 0.0,
            }
        )
    return result


def serialize(value):
    if isinstance(value, (datetime,)):
        return value.isoformat(sep=" ")
    raise TypeError(type(value).__name__)


def main() -> None:
    selected_dates = read_selected_dates()
    bars = read_bars()
    pivots = build_pivots(bars)
    legs = build_legs(pivots, selected_dates)
    points = [row["points"] for row in legs]
    pcts = [row["pct"] for row in legs]
    session_bars = [
        row for row in bars if row["time"].date() in selected_dates and START <= row["time"].time() < END
    ]
    analysis = {
        "method": {
            "zigzag_reversal_pct": THRESHOLD * 100.0,
            "price": "Close",
            "selected_dates": len(selected_dates),
            "time_window": "09:00-19:00",
            "cross_day_legs": "excluded",
            "unfinished_leg": "excluded",
            "normalization_week": "5 selected volatile sessions",
            "normalization_month": "21 selected volatile sessions",
        },
        "data": {
            "all_bars": len(bars),
            "selected_session_bars": len(session_bars),
            "first_time": bars[0]["time"],
            "last_time": bars[-1]["time"],
            "all_confirmed_pivots": len(pivots),
            "selected_legs": len(legs),
            "selected_dates_with_legs": len({row["end_time"].date() for row in legs}),
        },
        "legs": {
            "count": len(legs),
            "up_count": sum(row["direction"] == "UP" for row in legs),
            "down_count": sum(row["direction"] == "DOWN" for row in legs),
            "mean_points": mean(points),
            "median_points": median(points),
            "mean_pct": mean(pcts),
            "median_pct": median(pcts),
            "p90_pct": percentile(pcts, 0.90),
            "p95_pct": percentile(pcts, 0.95),
            "max_points": max(points),
            "max_pct": max(pcts),
            "per_volatile_day": len(legs) / len(selected_dates),
            "per_5_volatile_days": len(legs) / len(selected_dates) * 5.0,
        },
        "top_groups": top_groups(legs),
        "frequency": frequency(legs, selected_dates),
        "corrections": corrections(legs, bars),
    }
    OUTPUT.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, default=serialize), encoding="utf-8")
    print(json.dumps(analysis, ensure_ascii=False, indent=2, default=serialize))
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
