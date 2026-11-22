from __future__ import annotations

import csv
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font


SIGNALS_FILE = Path("test_result_legacy_20260824_143531.xlsx")
OHLC_FILE = Path("data.csv")
ROLLOVER_FILE = Path("rollover_report.csv")
OUTPUT_FILE = Path("st_movements_roll_adjusted_percent.xlsx")
MOSCOW = timezone(timedelta(hours=3))


def read_bars() -> list[dict]:
    result = []
    with OHLC_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle, delimiter=";")):
            result.append(
                {
                    "index": index,
                    "time": datetime.strptime(
                        row["<DATE>"] + row["<TIME>"], "%Y%m%d%H%M%S"
                    ),
                    "open": float(row["<OPEN>"]),
                    "high": float(row["<HIGH>"]),
                    "low": float(row["<LOW>"]),
                    "close": float(row["<CLOSE>"]),
                }
            )
    return result


def read_flips() -> list[dict]:
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
            raise ValueError(f"Conflicting duplicate reversal at bar {bar}")
        unique[bar] = flip
    return [unique[index] for index in sorted(unique)]


def read_rollovers(bars: list[dict]) -> list[dict]:
    first_index_by_date = {}
    for bar in bars:
        first_index_by_date.setdefault(bar["time"].date(), bar["index"])

    first_date = bars[0]["time"].date()
    last_date = bars[-1]["time"].date()
    applied = []
    with ROLLOVER_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            report_time = datetime.strptime(
                row["roll_timestamp"], "%Y-%m-%dT%H:%M:%S%z"
            ).astimezone(MOSCOW)
            nominal_date = report_time.date()
            if not (first_date <= nominal_date <= last_date):
                continue

            spread = float(row["intercontract_spread"])
            effective_date = nominal_date
            detection = "report_date"

            # The minute continuous source can switch one session earlier/later
            # than the official H1 source. Large spreads are identifiable at the
            # daily session boundary; use the closest matching jump within ±2 days.
            if abs(spread) >= 500:
                candidates = []
                for delta in range(-2, 3):
                    candidate_date = nominal_date + timedelta(days=delta)
                    index = first_index_by_date.get(candidate_date)
                    if index is None or index == 0:
                        continue
                    session_jump = bars[index]["open"] - bars[index - 1]["close"]
                    candidates.append(
                        (abs(session_jump - spread), abs(delta), candidate_date, session_jump)
                    )
                if candidates:
                    _, _, effective_date, _ = min(candidates)
                    if effective_date != nominal_date:
                        detection = "matched_minute_session_jump"

            effective_index = first_index_by_date[effective_date]
            session_jump = bars[effective_index]["open"] - bars[effective_index - 1]["close"]
            offset_delta = -spread
            report_offset_delta = float(row["offset_after"]) - float(row["offset_before"])
            if abs(offset_delta - report_offset_delta) > 1e-9:
                raise ValueError(f"Offset delta mismatch for {row['new_contract']}")

            applied.append(
                {
                    "report_time_msk": report_time.replace(tzinfo=None),
                    "effective_time_minute_data": bars[effective_index]["time"],
                    "old_contract": row["old_contract"],
                    "new_contract": row["new_contract"],
                    "spread_points": spread,
                    "offset_delta": offset_delta,
                    "observed_session_jump": session_jump,
                    "alignment_method": detection,
                    "effective_index": effective_index,
                }
            )
    return sorted(applied, key=lambda item: item["effective_index"])


def main() -> None:
    bars = read_bars()
    flips = read_flips()
    rollovers = read_rollovers(bars)

    for flip in flips:
        source_bar = bars[flip["bar"]]
        if source_bar["time"] != flip["time"] or source_bar["close"] != flip["price"]:
            raise ValueError(f"OHLC/Signals mismatch at bar {flip['bar']}")

    offset = 0.0
    rollover_cursor = 0
    for bar in bars:
        while (
            rollover_cursor < len(rollovers)
            and rollovers[rollover_cursor]["effective_index"] == bar["index"]
        ):
            offset += rollovers[rollover_cursor]["offset_delta"]
            rollover_cursor += 1
        bar["roll_offset"] = offset
        bar["adjusted_high"] = bar["high"] + offset
        bar["adjusted_low"] = bar["low"] + offset
        bar["adjusted_close"] = bar["close"] + offset

    movements = []
    for movement_id, (start, end) in enumerate(zip(flips, flips[1:]), start=1):
        if start["after"] != end["before"]:
            raise ValueError(f"Color mismatch at bars {start['bar']}..{end['bar']}")
        window = bars[start["bar"] + 1 : end["bar"] + 1]
        start_bar = bars[start["bar"]]
        adjusted_start = start_bar["adjusted_close"]

        if start["after"] == "GREEN":
            raw_extreme = max(window, key=lambda bar: bar["high"])
            adjusted_extreme = max(window, key=lambda bar: bar["adjusted_high"])
            raw_distance = max(0.0, raw_extreme["high"] - start["price"])
            adjusted_distance = max(
                0.0, adjusted_extreme["adjusted_high"] - adjusted_start
            )
        else:
            raw_extreme = min(window, key=lambda bar: bar["low"])
            adjusted_extreme = min(window, key=lambda bar: bar["adjusted_low"])
            raw_distance = max(0.0, start["price"] - raw_extreme["low"])
            adjusted_distance = max(
                0.0, adjusted_start - adjusted_extreme["adjusted_low"]
            )

        crossed = [
            item
            for item in rollovers
            if start["bar"] < item["effective_index"] <= end["bar"]
        ]
        movements.append(
            {
                "movement_id": movement_id,
                "st_color": start["after"],
                "start_time": start["time"],
                "end_time": end["time"],
                "adjusted_extreme_time": adjusted_extreme["time"],
                "bars_to_color_change": end["bar"] - start["bar"],
                "bars_to_adjusted_extreme": adjusted_extreme["index"] - start["bar"],
                "rollovers_crossed": len(crossed),
                "contracts_crossed": ", ".join(
                    f"{x['old_contract']}>{x['new_contract']}" for x in crossed
                ),
                "raw_movement_pct": raw_distance / start["price"] * 100,
                "adjusted_movement_pct": adjusted_distance / start["price"] * 100,
                "adjustment_effect_pct": (adjusted_distance - raw_distance)
                / start["price"]
                * 100,
            }
        )

    movements.sort(key=lambda item: item["adjusted_movement_pct"], reverse=True)
    groups = [("Вся выборка", 1.0), ("Верхние 50%", 0.5), ("Верхние 30%", 0.3), ("Верхние 10%", 0.1)]
    summary = []
    for label, share in groups:
        count = len(movements) if share == 1 else math.ceil(len(movements) * share)
        selected = movements[:count]
        summary.append(
            {
                "group": label,
                "count": count,
                "mean_adjusted_movement_pct": fmean(
                    x["adjusted_movement_pct"] for x in selected
                ),
                "mean_bars_to_color_change": fmean(
                    x["bars_to_color_change"] for x in selected
                ),
                "mean_bars_to_adjusted_extreme": fmean(
                    x["bars_to_adjusted_extreme"] for x in selected
                ),
                "minimum_adjusted_pct_in_group": min(
                    x["adjusted_movement_pct"] for x in selected
                ),
                "maximum_adjusted_pct_in_group": max(
                    x["adjusted_movement_pct"] for x in selected
                ),
            }
        )

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Summary"
    summary_headers = list(summary[0])
    summary_sheet.append(summary_headers)
    for item in summary:
        summary_sheet.append([item[key] for key in summary_headers])

    movement_sheet = workbook.create_sheet("Movements_Sorted")
    movement_headers = ["rank", *list(movements[0])]
    movement_sheet.append(movement_headers)
    for rank, item in enumerate(movements, start=1):
        movement_sheet.append([rank, *[item[key] for key in movement_headers[1:]]])

    rollover_sheet = workbook.create_sheet("Rollovers_Applied")
    rollover_headers = [key for key in rollovers[0] if key != "effective_index"]
    rollover_sheet.append(rollover_headers)
    for item in rollovers:
        rollover_sheet.append([item[key] for key in rollover_headers])

    affected_sheet = workbook.create_sheet("Affected_Movements")
    affected_sheet.append(movement_headers)
    affected = [item for item in movements if item["rollovers_crossed"]]
    for rank, item in enumerate(affected, start=1):
        affected_sheet.append([rank, *[item[key] for key in movement_headers[1:]]])

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True)

    for row in summary_sheet.iter_rows(min_row=2):
        for index in (2, 5, 6):
            row[index].number_format = '0.000000"%"'
        for index in (3, 4):
            row[index].number_format = "0.0000"
    for sheet in (movement_sheet, affected_sheet):
        for row in sheet.iter_rows(min_row=2):
            for index in (10, 11, 12):
                row[index].number_format = '0.000000"%"'

    workbook.save(OUTPUT_FILE)
    print(
        f"bars={len(bars)} flips={len(flips)} movements={len(movements)} "
        f"rollovers={len(rollovers)} affected_movements={len(affected)}"
    )
    for item in summary:
        print(item)
    print("ROLLS")
    for item in rollovers:
        print(item)


if __name__ == "__main__":
    main()
