from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font


MOVEMENTS_FILE = Path("st_movements_roll_adjusted_percent.xlsx")
OHLC_FILE = Path("data.csv")
OUTPUT_FILE = Path("st_movements_over_0_5_frequency.xlsx")
THRESHOLD_PCT = 0.5


def main() -> None:
    trading_days = set()
    trading_weeks = set()
    with OHLC_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            day = datetime.strptime(row["<DATE>"], "%Y%m%d").date()
            trading_days.add(day)
            iso = day.isocalendar()
            trading_weeks.add((iso.year, iso.week))

    workbook = load_workbook(MOVEMENTS_FILE, read_only=True, data_only=True)
    sheet = workbook["Movements_Sorted"]
    rows = sheet.iter_rows(values_only=True)
    headers = next(rows)
    column = {name: index for index, name in enumerate(headers)}

    qualifying = []
    for row in rows:
        movement_pct = float(row[column["adjusted_movement_pct"]])
        if movement_pct > THRESHOLD_PCT:
            end_time = row[column["end_time"]]
            qualifying.append(
                {
                    "movement_id": row[column["movement_id"]],
                    "st_color": row[column["st_color"]],
                    "start_time": row[column["start_time"]],
                    "end_time": end_time,
                    "movement_pct": movement_pct,
                    "bars": row[column["bars_to_color_change"]],
                    "day": end_time.date(),
                    "week": (end_time.isocalendar().year, end_time.isocalendar().week),
                }
            )

    daily_events = Counter(item["day"] for item in qualifying)
    weekly_events = Counter(item["week"] for item in qualifying)
    daily_counts = [daily_events.get(day, 0) for day in sorted(trading_days)]
    weekly_counts = [weekly_events.get(week, 0) for week in sorted(trading_weeks)]
    daily_distribution = Counter(daily_counts)
    weekly_distribution = Counter(weekly_counts)

    days_exactly_two = sum(count == 2 for count in daily_counts)
    days_two_or_more = sum(count >= 2 for count in daily_counts)
    days_with_any = sum(count >= 1 for count in daily_counts)
    weeks_with_any = sum(count >= 1 for count in weekly_counts)
    movement_sizes = [item["movement_pct"] for item in qualifying]
    direction_counts = Counter(item["st_color"] for item in qualifying)

    summary = [
        ("Порог движения, строго больше", THRESHOLD_PCT, "%"),
        ("Количество движений", len(qualifying), "движений"),
        ("Средний размер движения >0.5%", fmean(movement_sizes), "%"),
        ("Медианный размер движения >0.5%", median(movement_sizes), "%"),
        ("Зелёных движений", direction_counts["GREEN"], "движений"),
        ("Красных движений", direction_counts["RED"], "движений"),
        ("Торговых дней", len(trading_days), "дней"),
        ("Торговых недель", len(trading_weeks), "недель"),
        ("Среднее движений за торговый день", fmean(daily_counts), "движений/день"),
        ("Среднее движений за торговую неделю", fmean(weekly_counts), "движений/неделю"),
        ("Медиана за торговый день", median(daily_counts), "движений"),
        ("Медиана за торговую неделю", median(weekly_counts), "движений"),
        ("Дней хотя бы с одним движением", days_with_any, "дней"),
        ("Доля дней хотя бы с одним движением", days_with_any / len(trading_days) * 100, "%"),
        ("Дней ровно с двумя движениями", days_exactly_two, "дней"),
        ("Доля всех торговых дней ровно с двумя", days_exactly_two / len(trading_days) * 100, "%"),
        ("Дней с двумя или более движениями", days_two_or_more, "дней"),
        ("Доля всех торговых дней с двумя или более", days_two_or_more / len(trading_days) * 100, "%"),
        ("Доля дней с >=2 среди дней с >=1", days_two_or_more / days_with_any * 100, "%"),
        ("Недель хотя бы с одним движением", weeks_with_any, "недель"),
        ("Доля недель хотя бы с одним движением", weeks_with_any / len(trading_weeks) * 100, "%"),
    ]

    output = Workbook()
    summary_sheet = output.active
    summary_sheet.title = "Summary"
    summary_sheet.append(["metric", "value", "unit"])
    for item in summary:
        summary_sheet.append(item)

    day_sheet = output.create_sheet("Daily_Distribution")
    day_sheet.append(["movements_per_day", "trading_days", "share_of_trading_days_pct"])
    for count in range(max(daily_distribution) + 1):
        days = daily_distribution.get(count, 0)
        day_sheet.append([count, days, days / len(trading_days) * 100])

    week_sheet = output.create_sheet("Weekly_Distribution")
    week_sheet.append(["movements_per_week", "trading_weeks", "share_of_trading_weeks_pct"])
    for count in range(max(weekly_distribution) + 1):
        weeks = weekly_distribution.get(count, 0)
        week_sheet.append([count, weeks, weeks / len(trading_weeks) * 100])

    size_sheet = output.create_sheet("Size_Distribution")
    size_sheet.append(["movement_range_pct", "movements", "share_pct"])
    size_bins = [
        (0.5, 0.6),
        (0.6, 0.75),
        (0.75, 1.0),
        (1.0, 1.5),
        (1.5, 2.0),
        (2.0, 3.0),
        (3.0, 5.0),
        (5.0, float("inf")),
    ]
    for lower, upper in size_bins:
        count = sum(
            (value > lower if lower == THRESHOLD_PCT else value >= lower)
            and value < upper
            for value in movement_sizes
        )
        label = f"({lower:.2f}; {upper:.2f})" if lower == THRESHOLD_PCT else (
            f"[{lower:.2f}; +inf)" if upper == float("inf") else f"[{lower:.2f}; {upper:.2f})"
        )
        size_sheet.append([label, count, count / len(qualifying) * 100])

    event_sheet = output.create_sheet("Movements_Over_0.5")
    event_headers = [
        "movement_id",
        "st_color",
        "start_time",
        "end_time",
        "movement_pct",
        "bars",
        "assigned_day",
        "iso_week",
    ]
    event_sheet.append(event_headers)
    for item in sorted(qualifying, key=lambda value: value["end_time"]):
        event_sheet.append(
            [
                item["movement_id"],
                item["st_color"],
                item["start_time"],
                item["end_time"],
                item["movement_pct"],
                item["bars"],
                item["day"],
                f"{item['week'][0]}-W{item['week'][1]:02d}",
            ]
        )

    for current_sheet in output.worksheets:
        current_sheet.freeze_panes = "A2"
        current_sheet.auto_filter.ref = current_sheet.dimensions
        for cell in current_sheet[1]:
            cell.font = Font(bold=True)

    for row in summary_sheet.iter_rows(min_row=2):
        if row[2].value == "%":
            row[1].number_format = '0.0000"%"'
    for row in day_sheet.iter_rows(min_row=2):
        row[2].number_format = '0.0000"%"'
    for row in week_sheet.iter_rows(min_row=2):
        row[2].number_format = '0.0000"%"'
    for row in size_sheet.iter_rows(min_row=2):
        row[2].number_format = '0.0000"%"'
    for row in event_sheet.iter_rows(min_row=2):
        row[4].number_format = '0.000000"%"'

    output.save(OUTPUT_FILE)

    print("SUMMARY")
    for item in summary:
        print(item)
    print("DAILY", sorted(daily_distribution.items()))
    print("WEEKLY", sorted(weekly_distribution.items()))
    print("SIZE", [tuple(row) for row in size_sheet.iter_rows(min_row=2, values_only=True)])


if __name__ == "__main__":
    main()
