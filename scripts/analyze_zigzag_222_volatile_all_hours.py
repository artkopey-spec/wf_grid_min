from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from statistics import mean, median

import analyze_zigzag_volatile_222 as base


ROOT = Path(r"C:\3.1_wf_grid")
RESULTS = ROOT / "results_tester_st_batch_20260828"
OUTPUT = RESULTS / "zigzag_0p2_222_volatile_all_hours_analysis.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.002)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args()


def build_selected_legs(pivots: list[dict], selected_dates: set) -> list[dict]:
    legs: list[dict] = []
    for start, end in zip(pivots, pivots[1:]):
        # Only a movement wholly contained in one selected date belongs to the
        # 222-day population. There is no intraday clock filter.
        if start["time"].date() != end["time"].date():
            continue
        if start["time"].date() not in selected_dates:
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


def corrections_24h(legs: list[dict], bars: list[dict]) -> dict:
    large = [row for row in legs if row["pct"] > 1.0]
    ratios = (0.33, 0.50, 0.90)
    hits = {ratio: [] for ratio in ratios}
    directions = {ratio: {"UP": 0, "DOWN": 0} for ratio in ratios}
    totals = {
        "UP": sum(row["direction"] == "UP" for row in large),
        "DOWN": sum(row["direction"] == "DOWN" for row in large),
    }

    for leg in large:
        deadline = leg["end_time"] + timedelta(hours=24)
        first_hit = {ratio: None for ratio in ratios}
        index = leg["end_index"] + 1
        while index < len(bars) and bars[index]["time"] <= deadline:
            bar = bars[index]
            for ratio in ratios:
                if first_hit[ratio] is not None:
                    continue
                if leg["direction"] == "UP":
                    level = leg["end_price"] - ratio * leg["points"]
                    touched = bar["low"] <= level
                else:
                    level = leg["end_price"] + ratio * leg["points"]
                    touched = bar["high"] >= level
                if touched:
                    elapsed = (bar["time"] - leg["end_time"]).total_seconds() / 60.0
                    first_hit[ratio] = elapsed
            if all(first_hit[ratio] is not None for ratio in ratios):
                break
            index += 1

        for ratio in ratios:
            value = first_hit[ratio]
            if value is not None:
                hits[ratio].append(value)
                directions[ratio][leg["direction"]] += 1

    result = {
        "sample_count": len(large),
        "direction_totals": totals,
        "window": "24 calendar hours from the ending extreme",
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


def main() -> None:
    args = parse_args()
    if not 0.0 < args.threshold < 1.0:
        raise ValueError("threshold must be a positive fraction below 1")
    base.THRESHOLD = args.threshold
    selected_dates = base.read_selected_dates()
    bars = base.read_bars()
    pivots = base.build_pivots(bars)
    legs = build_selected_legs(pivots, selected_dates)
    points = [row["points"] for row in legs]
    pcts = [row["pct"] for row in legs]
    selected_bars = [row for row in bars if row["time"].date() in selected_dates]

    analysis = {
        "method": {
            "zigzag_reversal_pct": base.THRESHOLD * 100.0,
            "price": "Close",
            "selected_dates": len(selected_dates),
            "time_window": "all available hours",
            "cross_day_legs": "excluded",
            "unfinished_leg": "excluded",
            "confirmation_time_filter": "none",
            "normalization_week": "5 selected volatile sessions",
            "normalization_month": "21 selected volatile sessions",
        },
        "data": {
            "all_bars": len(bars),
            "selected_date_bars": len(selected_bars),
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
            "p90_pct": base.percentile(pcts, 0.90),
            "p95_pct": base.percentile(pcts, 0.95),
            "max_points": max(points),
            "max_pct": max(pcts),
            "per_volatile_day": len(legs) / len(selected_dates),
            "per_5_volatile_days": len(legs) / len(selected_dates) * 5.0,
        },
        "top_groups": base.top_groups(legs),
        "frequency": base.frequency(legs, selected_dates),
        "corrections": corrections_24h(legs, bars),
    }
    args.output.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2, default=base.serialize),
        encoding="utf-8",
    )
    print(json.dumps(analysis, ensure_ascii=False, indent=2, default=base.serialize))
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
