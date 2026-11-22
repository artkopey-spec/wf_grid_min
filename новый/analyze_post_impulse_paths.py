from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook
from openpyxl.styles import Font

from analyze_st_movements_roll_adjusted import read_bars, read_rollovers
from simulate_st_impulse_strategy import read_candidates


OUTPUT_FILE = Path("ST_80_4_post_impulse_0_6_paths.xlsx")
MAX_EXACT_DELAY = timedelta(minutes=5)


def percentile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def main() -> None:
    bars = read_bars()
    rollovers = read_rollovers(bars)
    candidates = read_candidates()

    offset = 0.0
    rollover_cursor = 0
    by_time = {}
    by_day = defaultdict(list)
    for bar in bars:
        while (
            rollover_cursor < len(rollovers)
            and rollovers[rollover_cursor]["effective_index"] == bar["index"]
        ):
            offset += rollovers[rollover_cursor]["offset_delta"]
            rollover_cursor += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar
        by_day[bar["time"].date()].append(bar)

    times = [bar["time"] for bar in bars]
    days = sorted(by_day)
    first_candidate_by_day = {}
    for candidate in candidates:
        first_candidate_by_day.setdefault(candidate["end_time"].date(), candidate)
    events = list(first_candidate_by_day.values())

    def exact_future(target):
        index = bisect_left(times, target)
        if index >= len(bars) or bars[index]["time"] - target > MAX_EXACT_DELAY:
            return None
        return bars[index]

    def return_pct(anchor_bar, future_bar):
        if future_bar is None:
            return None
        # Locally rebase any rollover crossed between anchor and horizon.
        future_price = (
            future_bar["open"] + future_bar["offset"] - anchor_bar["offset"]
        )
        return (future_price / anchor_bar["close"] - 1) * 100

    rows = []
    for candidate in events:
        anchor = by_time[candidate["end_time"]]
        anchor_time = anchor["time"]

        one_hour = exact_future(anchor_time + timedelta(hours=1))
        three_hours = exact_future(anchor_time + timedelta(hours=3))
        twenty_four_hours = exact_future(anchor_time + timedelta(hours=24))

        nineteen = None
        if anchor_time.time() < time(19, 0):
            target_19 = datetime.combine(anchor_time.date(), time(19, 0))
            index_19 = bisect_left(times, target_19)
            if index_19 < len(bars) and bars[index_19]["time"].date() == anchor_time.date():
                nineteen = bars[index_19]

        next_open = None
        later_days = [day for day in days if day > anchor_time.date()]
        if later_days:
            next_open = by_day[later_days[0]][0]

        rows.append(
            {
                "movement_id": candidate["movement_id"],
                "impulse_end_time": anchor_time,
                "impulse_size_pct": candidate["movement_pct"],
                "anchor_close": anchor["close"],
                "ret_plus_1h_pct": return_pct(anchor, one_hour),
                "ret_plus_3h_pct": return_pct(anchor, three_hours),
                "ret_at_19_pct": return_pct(anchor, nineteen),
                "ret_next_day_open_pct": return_pct(anchor, next_open),
                "ret_plus_24h_pct": return_pct(anchor, twenty_four_hours),
                "time_plus_1h_used": one_hour["time"] if one_hour else None,
                "time_plus_3h_used": three_hours["time"] if three_hours else None,
                "time_at_19_used": nineteen["time"] if nineteen else None,
                "time_next_day_open_used": next_open["time"] if next_open else None,
                "time_plus_24h_used": twenty_four_hours["time"] if twenty_four_hours else None,
            }
        )

    horizons = [
        ("Через 1 час", "ret_plus_1h_pct"),
        ("Через 3 часа", "ret_plus_3h_pct"),
        ("В 19:00 того же дня", "ret_at_19_pct"),
        ("На открытии следующего торгового дня", "ret_next_day_open_pct"),
        ("Ровно через 24 часа", "ret_plus_24h_pct"),
    ]
    summary = []
    for label, key in horizons:
        values = [row[key] for row in rows if row[key] is not None]
        summary.append(
            {
                "horizon": label,
                "observations": len(values),
                "mean_return_pct": fmean(values),
                "median_return_pct": median(values),
                "price_lower_pct": sum(value < 0 for value in values) / len(values) * 100,
                "price_higher_pct": sum(value > 0 for value in values) / len(values) * 100,
                "unchanged_pct": sum(value == 0 for value in values) / len(values) * 100,
                "p25_return_pct": percentile(values, 0.25),
                "p75_return_pct": percentile(values, 0.75),
                "minimum_return_pct": min(values),
                "maximum_return_pct": max(values),
            }
        )

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Summary"
    summary_headers = list(summary[0])
    summary_sheet.append(summary_headers)
    for item in summary:
        summary_sheet.append([item[key] for key in summary_headers])

    events_sheet = workbook.create_sheet("Events")
    event_headers = list(rows[0])
    events_sheet.append(event_headers)
    for row in rows:
        events_sheet.append([row[key] for key in event_headers])

    assumptions = workbook.create_sheet("Method")
    assumptions.append(["item", "definition"])
    method_rows = [
        ("SuperTrend", "80 / 4.0"),
        ("Event", "first completed GREEN movement strictly >0.6% per trading day"),
        ("Anchor", "Close of ST color-change bar ending the GREEN movement"),
        ("Future price", "Open of minute bar at the requested horizon"),
        ("1h / 3h / 24h", "calendar time; first bar at/after target, max delay 5 minutes"),
        ("19:00", "first available bar at/after 19:00 on the same date"),
        ("Next-day open", "first available minute bar of next trading date"),
        ("Rollover", "gap-neutral local adjustment if a roll is crossed"),
        ("Sign", "positive = Si/USD higher; negative = RUB stronger"),
    ]
    for item in method_rows:
        assumptions.append(item)

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True)
    workbook.save(OUTPUT_FILE)

    print(f"events={len(events)}")
    for item in summary:
        print(item)
    print(f"saved={OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
