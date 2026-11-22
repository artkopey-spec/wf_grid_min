from __future__ import annotations

from bisect import bisect_left
from datetime import time, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_rollovers
from backtest_short_after_down_impulse import HORIZONS, build_trades, summarize, write_table


MOVEMENTS_FILE = Path("MIX_ST_19_4_analysis.xlsx")
OUTPUT_FILE = Path("MIX_ST_19_4_SHORT_after_DOWN_0_6_backtest.xlsx")
THRESHOLD_PCT = 0.6


def read_candidates():
    workbook = load_workbook(MOVEMENTS_FILE, read_only=True, data_only=True)
    sheet = workbook["Movements_Sorted"]
    rows = sheet.iter_rows(values_only=True)
    headers = next(rows)
    column = {name: index for index, name in enumerate(headers)}
    candidates = []
    for row in rows:
        movement_pct = float(row[column["movement_pct"]])
        if row[column["st_color"]] == "RED" and movement_pct > THRESHOLD_PCT:
            candidates.append(
                {
                    "movement_id": int(row[column["movement_id"]]),
                    "end_time": row[column["end_time"]],
                    "movement_pct": movement_pct,
                }
            )
    candidates.sort(key=lambda item: item["end_time"])
    first_by_day = {}
    for candidate in candidates:
        first_by_day.setdefault(candidate["end_time"].date(), candidate)
    return candidates, list(first_by_day.values())


def main():
    bars = read_bars()
    rollovers = read_rollovers(bars)
    all_candidates, candidates = read_candidates()

    offset = 0.0
    cursor = 0
    by_time = {}
    by_day = {}
    for bar in bars:
        while cursor < len(rollovers) and rollovers[cursor]["effective_index"] == bar["index"]:
            offset += rollovers[cursor]["offset_delta"]
            cursor += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar
        by_day.setdefault(bar["time"].date(), []).append(bar)
    times = [bar["time"] for bar in bars]
    days = sorted(by_day)

    def first_after(target):
        index = bisect_left(times, target)
        return bars[index] if index < len(bars) else None

    def gap_neutral_return(anchor, future):
        if future is None:
            return None
        future_price = future["open"] + future["offset"] - anchor["offset"]
        return (future_price / anchor["close"] - 1) * 100

    events = []
    for candidate in candidates:
        anchor = by_time[candidate["end_time"]]
        three_hours = first_after(anchor["time"] + timedelta(hours=3))
        twenty_four = first_after(anchor["time"] + timedelta(hours=24))

        later_days = [day for day in days if day > anchor["time"].date()]
        next_open = by_day[later_days[0]][0] if later_days else None

        exit_19 = None
        if anchor["time"].time() < time(19, 0):
            eligible = [bar for bar in by_day[anchor["time"].date()] if bar["time"].time() >= time(19, 0)]
            exit_19 = eligible[0] if eligible else None
        if exit_19 is None:
            for day in later_days:
                eligible = [bar for bar in by_day[day] if bar["time"].time() >= time(19, 0)]
                if eligible:
                    exit_19 = eligible[0]
                    break

        events.append(
            {
                "movement_id": candidate["movement_id"],
                "impulse_end_time": anchor["time"],
                "impulse_size_pct": candidate["movement_pct"],
                "anchor_close": anchor["close"],
                "ret_plus_3h_pct": gap_neutral_return(anchor, three_hours),
                "time_plus_3h_used": three_hours["time"] if three_hours else None,
                "ret_at_19_pct": gap_neutral_return(anchor, exit_19),
                "time_at_19_used": exit_19["time"] if exit_19 else None,
                "ret_next_day_open_pct": gap_neutral_return(anchor, next_open),
                "time_next_day_open_used": next_open["time"] if next_open else None,
                "ret_plus_24h_pct": gap_neutral_return(anchor, twenty_four),
                "time_plus_24h_used": twenty_four["time"] if twenty_four else None,
            }
        )

    summaries = []
    horizon_trades = {}
    for horizon, return_key, time_key in HORIZONS:
        trades, overlap, missing = build_trades(events, horizon, return_key, time_key)
        horizon_trades[horizon] = trades
        summaries.append(summarize(horizon, trades, overlap, missing))

    output = Workbook()
    output.remove(output.active)
    write_table(output, "Summary", summaries)
    for horizon, trades in horizon_trades.items():
        write_table(output, horizon[:31], trades)
    method = [
        {"item": "Instrument", "definition": "MIX futures; 25-point tick = 25 RUB, therefore 1 quote point = 1 RUB/contract"},
        {"item": "SuperTrend", "definition": "19 / 4.0"},
        {"item": "Signal", "definition": "first completed RED impulse strictly >0.6% per day"},
        {"item": "Entry", "definition": "SHORT 100 MIX at Close of RED-to-GREEN flip bar"},
        {"item": "Position sizing", "definition": "every signal opens +100 contracts; overlaps add"},
        {"item": "3h / 24h", "definition": "first available bar at/after target"},
        {"item": "19:00 late signal", "definition": "exit at first available 19:00 point next trading day"},
        {"item": "Costs", "definition": "0.00667% per side; no slippage"},
        {"item": "Rollover", "definition": "11 nonzero intercontract offsets applied"},
    ]
    write_table(output, "Method", method)
    write_table(output, "Events", events)
    output.save(OUTPUT_FILE)

    print(f"all_red_candidates={len(all_candidates)} first_per_day={len(events)}")
    for item in summaries:
        print(item)
    print(f"saved={OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
