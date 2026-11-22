from __future__ import annotations

import csv
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import openpyxl


ROOT = Path(r"C:\3.1_wf_grid")
DATA = ROOT / "data.csv"
DATES_FILE = ROOT / "results_tester_st_batch_20260828" / "vol data.xlsx"
MA_LENGTH = 50
LEVELS = (0.4, 0.5, 0.6, 0.7, 0.8)
COOLDOWN_MINUTES = 120


def selected_dates() -> set:
    workbook = openpyxl.load_workbook(DATES_FILE, read_only=True, data_only=True)
    sheet = workbook.active
    headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    date_index = headers.index("Дата сессии")
    result = {
        row[date_index].value.date()
        for row in sheet.iter_rows(min_row=2)
        if row[date_index].value is not None
    }
    workbook.close()
    return result


def main() -> None:
    dates = selected_dates()
    window: deque[float] = deque()
    rolling_sum = 0.0
    previous_distance: float | None = None
    counts = {level: {"upper": 0, "lower": 0} for level in LEVELS}
    event_dates = {level: defaultdict(int) for level in LEVELS}
    filtered_counts = {level: {"upper": 0, "lower": 0} for level in LEVELS}
    filtered_dates = {level: defaultdict(int) for level in LEVELS}
    last_filtered_signal = {level: None for level in LEVELS}
    selected_bars = 0

    with DATA.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            close = float(row["<CLOSE>"])
            timestamp = datetime.strptime(row["<DATE>"] + row["<TIME>"], "%Y%m%d%H%M%S")
            window.append(close)
            rolling_sum += close
            if len(window) > MA_LENGTH:
                rolling_sum -= window.popleft()
            if len(window) < MA_LENGTH:
                continue

            ma = rolling_sum / MA_LENGTH
            distance = (close - ma) / ma * 100.0 if ma != 0.0 else None
            if timestamp.date() in dates:
                selected_bars += 1
                if previous_distance is not None and distance is not None:
                    for level in LEVELS:
                        upper = previous_distance <= level and distance > level
                        lower = previous_distance >= -level and distance < -level
                        if upper:
                            counts[level]["upper"] += 1
                            event_dates[level][timestamp.date()] += 1
                        if lower:
                            counts[level]["lower"] += 1
                            event_dates[level][timestamp.date()] += 1
                        if upper or lower:
                            last_signal = last_filtered_signal[level]
                            elapsed_minutes = (
                                (timestamp - last_signal).total_seconds() / 60.0
                                if last_signal is not None
                                else None
                            )
                            if elapsed_minutes is None or elapsed_minutes >= COOLDOWN_MINUTES:
                                direction = "upper" if upper else "lower"
                                filtered_counts[level][direction] += 1
                                filtered_dates[level][timestamp.date()] += 1
                                last_filtered_signal[level] = timestamp
            previous_distance = distance

    print(f"Selected dates: {len(dates)}")
    print(f"Selected valid-SMA bars: {selected_bars}")
    for level in LEVELS:
        upper = counts[level]["upper"]
        lower = counts[level]["lower"]
        total = upper + lower
        days = len(event_dates[level])
        print(
            f"level={level:.1f}% upper={upper} lower={lower} total={total} "
            f"per_day={total / len(dates):.6f} "
            f"per_5_days={total / len(dates) * 5:.6f} "
            f"days_with_event={days} share_days={days / len(dates) * 100:.6f}% "
            f"volatile_days_per_event={len(dates) / total:.6f}"
        )
        filtered_upper = filtered_counts[level]["upper"]
        filtered_lower = filtered_counts[level]["lower"]
        filtered_total = filtered_upper + filtered_lower
        filtered_day_count = len(filtered_dates[level])
        print(
            f"cooldown={COOLDOWN_MINUTES}m level={level:.1f}% "
            f"upper={filtered_upper} lower={filtered_lower} total={filtered_total} "
            f"per_day={filtered_total / len(dates):.6f} "
            f"per_5_days={filtered_total / len(dates) * 5:.6f} "
            f"days_with_event={filtered_day_count} "
            f"share_days={filtered_day_count / len(dates) * 100:.6f}% "
            f"raw_reduction={(1.0 - filtered_total / total) * 100.0:.6f}%"
        )


if __name__ == "__main__":
    main()
