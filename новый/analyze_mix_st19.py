from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font


OHLC_FILE = Path("mix_minute_data.csv")
SIGNALS_FILE = Path("mix_test_result_legacy.xlsx")
ROLLOVER_FILE = Path("mix_rollover_report.csv")
OUTPUT_FILE = Path("MIX_ST_19_4_analysis.xlsx")
MOSCOW = timezone(timedelta(hours=3))
THRESHOLD_PCT = 0.5


def read_bars():
    bars = []
    with OHLC_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle, delimiter=";")):
            bars.append(
                {
                    "index": index,
                    "time": datetime.strptime(row["<DATE>"] + row["<TIME>"], "%Y%m%d%H%M%S"),
                    "open": float(row["<OPEN>"]),
                    "high": float(row["<HIGH>"]),
                    "low": float(row["<LOW>"]),
                    "close": float(row["<CLOSE>"]),
                }
            )
    return bars


def read_flips():
    workbook = load_workbook(SIGNALS_FILE, read_only=True, data_only=True)
    sheet = workbook["Signals"]
    rows = sheet.iter_rows(values_only=True)
    headers = next(rows)
    column = {name: index for index, name in enumerate(headers)}
    unique = {}
    for row in rows:
        bar = int(row[column["Signal Bar Index"]])
        flip = {
            "bar": bar,
            "time": row[column["Signal Time"]],
            "price": float(row[column["Signal Close Price"]]),
            "before": row[column["ST Color Before"]],
            "after": row[column["ST Color After"]],
        }
        if bar in unique and unique[bar] != flip:
            raise ValueError(f"Conflicting signal duplicate at bar {bar}")
        unique[bar] = flip
    return [unique[index] for index in sorted(unique)]


def read_rollovers(bars):
    first_by_date = {}
    for bar in bars:
        first_by_date.setdefault(bar["time"].date(), bar["index"])
    first_date, last_date = bars[0]["time"].date(), bars[-1]["time"].date()
    result = []
    with ROLLOVER_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            report_time = datetime.strptime(row["roll_timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(MOSCOW)
            roll_date = report_time.date()
            if not (first_date <= roll_date <= last_date) or roll_date not in first_by_date:
                continue
            index = first_by_date[roll_date]
            delta = float(row["offset_after"]) - float(row["offset_before"])
            result.append(
                {
                    "effective_index": index,
                    "effective_time": bars[index]["time"],
                    "old_contract": row["old_contract"],
                    "new_contract": row["new_contract"],
                    "spread_points": float(row["intercontract_spread"]),
                    "offset_delta": delta,
                    "status": row["status"],
                    "observed_session_jump": bars[index]["open"] - bars[index - 1]["close"],
                }
            )
    return sorted(result, key=lambda item: item["effective_index"])


def main():
    bars = read_bars()
    flips = read_flips()
    rollovers = read_rollovers(bars)

    for flip in flips:
        bar = bars[flip["bar"]]
        if bar["time"] != flip["time"] or bar["close"] != flip["price"]:
            raise ValueError(f"Signal/OHLC mismatch at bar {flip['bar']}")

    offset = 0.0
    cursor = 0
    for bar in bars:
        while cursor < len(rollovers) and rollovers[cursor]["effective_index"] == bar["index"]:
            offset += rollovers[cursor]["offset_delta"]
            cursor += 1
        bar["offset"] = offset
        bar["adj_high"] = bar["high"] + offset
        bar["adj_low"] = bar["low"] + offset
        bar["adj_close"] = bar["close"] + offset

    movements = []
    for movement_id, (start, end) in enumerate(zip(flips, flips[1:]), start=1):
        if start["after"] != end["before"]:
            raise ValueError(f"Color chain mismatch at movement {movement_id}")
        window = bars[start["bar"] + 1 : end["bar"] + 1]
        start_bar = bars[start["bar"]]
        adjusted_start = start_bar["adj_close"]
        first_cross_time = None
        if start["after"] == "GREEN":
            extreme = max(window, key=lambda bar: bar["adj_high"])
            distance = max(0.0, extreme["adj_high"] - adjusted_start)
            for current in window:
                move_pct = (current["adj_high"] - adjusted_start) / start["price"] * 100
                if move_pct > THRESHOLD_PCT:
                    first_cross_time = current["time"]
                    break
        else:
            extreme = min(window, key=lambda bar: bar["adj_low"])
            distance = max(0.0, adjusted_start - extreme["adj_low"])
            for current in window:
                move_pct = (adjusted_start - current["adj_low"]) / start["price"] * 100
                if move_pct > THRESHOLD_PCT:
                    first_cross_time = current["time"]
                    break
        crossed = [r for r in rollovers if start["bar"] < r["effective_index"] <= end["bar"] and r["offset_delta"] != 0]
        movements.append(
            {
                "movement_id": movement_id,
                "st_color": start["after"],
                "start_time": start["time"],
                "end_time": end["time"],
                "extreme_time": extreme["time"],
                "first_cross_0_5_time": first_cross_time,
                "bars_to_color_change": end["bar"] - start["bar"],
                "bars_to_extreme": extreme["index"] - start["bar"],
                "movement_pct": distance / start["price"] * 100,
                "rollovers_crossed": len(crossed),
            }
        )

    sorted_moves = sorted(movements, key=lambda item: item["movement_pct"], reverse=True)
    groups = [("Вся выборка", 1.0), ("Верхние 50%", 0.5), ("Верхние 30%", 0.3), ("Верхние 10%", 0.1)]
    movement_summary = []
    for label, share in groups:
        count = len(sorted_moves) if share == 1 else math.ceil(len(sorted_moves) * share)
        selected = sorted_moves[:count]
        movement_summary.append(
            {
                "group": label,
                "count": count,
                "mean_movement_pct": fmean(x["movement_pct"] for x in selected),
                "mean_bars_to_extreme": fmean(x["bars_to_extreme"] for x in selected),
                "mean_bars_to_color_change": fmean(x["bars_to_color_change"] for x in selected),
                "minimum_pct_in_group": min(x["movement_pct"] for x in selected),
                "maximum_pct_in_group": max(x["movement_pct"] for x in selected),
            }
        )

    qualifying = [x for x in movements if x["movement_pct"] > THRESHOLD_PCT]
    trading_days = sorted({bar["time"].date() for bar in bars})
    trading_weeks = sorted({(day.isocalendar().year, day.isocalendar().week) for day in trading_days})
    daily_events = Counter(x["end_time"].date() for x in qualifying)
    weekly_events = Counter((x["end_time"].isocalendar().year, x["end_time"].isocalendar().week) for x in qualifying)
    daily_counts = [daily_events.get(day, 0) for day in trading_days]
    weekly_counts = [weekly_events.get(week, 0) for week in trading_weeks]
    days_any = sum(value >= 1 for value in daily_counts)
    days_exact_two = sum(value == 2 for value in daily_counts)
    days_two_plus = sum(value >= 2 for value in daily_counts)
    frequency_summary = [
        {"metric": "movements_gt_0_5", "value": len(qualifying)},
        {"metric": "mean_size_pct", "value": fmean(x["movement_pct"] for x in qualifying)},
        {"metric": "median_size_pct", "value": median(x["movement_pct"] for x in qualifying)},
        {"metric": "mean_per_trading_day", "value": fmean(daily_counts)},
        {"metric": "mean_per_trading_week", "value": fmean(weekly_counts)},
        {"metric": "days_with_any_pct", "value": days_any / len(trading_days) * 100},
        {"metric": "days_exactly_two_pct", "value": days_exact_two / len(trading_days) * 100},
        {"metric": "days_two_or_more_pct", "value": days_two_plus / len(trading_days) * 100},
    ]

    size_bins = [(0.5, 0.6), (0.6, 0.75), (0.75, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, float("inf"))]
    size_distribution = []
    for lower, upper in size_bins:
        count = sum((x["movement_pct"] > lower if lower == 0.5 else x["movement_pct"] >= lower) and x["movement_pct"] < upper for x in qualifying)
        label = f"{lower:.2f}-{upper:.2f}" if upper != float("inf") else f">={lower:.2f}"
        size_distribution.append({"range_pct": label, "count": count, "share_pct": count / len(qualifying) * 100})

    timing = Counter()
    timing_days = defaultdict(set)
    weekday_events = Counter()
    for item in qualifying:
        event = item["first_cross_0_5_time"]
        if event is None:
            continue
        if time(9, 0) <= event.time() < time(13, 0):
            label = "09-13"
        elif time(13, 0) <= event.time() < time(19, 0):
            label = "13-19"
        else:
            label = "outside_09_19"
        timing[label] += 1
        timing_days[label].add(event.date())
        weekday_events[event.weekday()] += 1
    bar_exposure = Counter()
    for bar in bars:
        if time(9, 0) <= bar["time"].time() < time(13, 0):
            label = "09-13"
        elif time(13, 0) <= bar["time"].time() < time(19, 0):
            label = "13-19"
        else:
            label = "outside_09_19"
        bar_exposure[label] += 1
    in_window = timing["09-13"] + timing["13-19"]
    time_summary = []
    for label in ("09-13", "13-19", "outside_09_19"):
        time_summary.append(
            {
                "window": label,
                "events": timing[label],
                "share_all_pct": timing[label] / len(qualifying) * 100,
                "share_inside_09_19_pct": timing[label] / in_window * 100 if label != "outside_09_19" else None,
                "events_per_100_trading_hours": timing[label] / (bar_exposure[label] / 60) * 100,
            }
        )
    weekday_days = Counter(day.weekday() for day in trading_days)
    weekday_summary = [
        {"weekday": name, "events": weekday_events[index], "trading_days": weekday_days[index], "events_per_day": weekday_events[index] / weekday_days[index]}
        for index, name in enumerate(("Пн", "Вт", "Ср", "Чт", "Пт"))
    ]

    output = Workbook()
    output.remove(output.active)
    tables = {
        "Movement_Summary": movement_summary,
        "Frequency_Summary": frequency_summary,
        "Size_Distribution": size_distribution,
        "Time_Summary": time_summary,
        "Weekday_Summary": weekday_summary,
        "Movements_Sorted": sorted_moves,
        "Rollovers": rollovers,
    }
    for name, rows in tables.items():
        sheet = output.create_sheet(name)
        headers = list(rows[0])
        sheet.append(headers)
        for row in rows:
            sheet.append([row[key] for key in headers])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True)
    output.save(OUTPUT_FILE)

    print(f"bars={len(bars)} flips={len(flips)} movements={len(movements)} rollovers_adjusted={sum(r['offset_delta'] != 0 for r in rollovers)}")
    print("MOVEMENT", movement_summary)
    print("FREQUENCY", frequency_summary)
    print("SIZE", size_distribution)
    print("TIME", time_summary)
    print("WEEKDAY", weekday_summary)
    print(f"saved={OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
