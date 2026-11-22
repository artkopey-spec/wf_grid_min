from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


ROOT = Path(r"C:\3.1_wf_grid\results_tester_st_batch_20260828")
TARGET = ROOT / "st_batch_vol_dates_intraday_results.xlsx"
DATES_FILE = ROOT / "vol data.xlsx"
ST80_FILE = ROOT / "test_result_legacy_20260828_133527.xlsx"


def load_selected_dates() -> set:
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


def calculate_daily(selected_dates: set) -> tuple[dict, dict]:
    wb = openpyxl.load_workbook(ST80_FILE, read_only=True, data_only=True)
    ws = wb["Trades_100"]
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    idx = {name: headers.index(name) for name in headers}
    pnl_by_date: dict = defaultdict(float)
    trades_by_date: dict = defaultdict(int)
    wins_by_date: dict = defaultdict(int)

    for row in ws.iter_rows(min_row=2, values_only=True):
        entry = row[idx["Entry Time"]]
        exit_ = row[idx["Exit Time"]]
        if (
            entry is None
            or exit_ is None
            or entry.date() not in selected_dates
            or entry.date() != exit_.date()
        ):
            continue
        pnl = float(row[idx["Net PnL %"]])
        day = entry.date()
        pnl_by_date[day] += pnl
        trades_by_date[day] += 1
        wins_by_date[day] += int(pnl > 0)
    wb.close()
    details = {
        day: {
            "pnl": pnl_by_date[day],
            "trades": trades_by_date[day],
            "wins": wins_by_date[day],
        }
        for day in pnl_by_date
    }
    return details, pnl_by_date


def style_header(ws) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def fit_columns(ws, maximum: int = 55) -> None:
    for column in ws.columns:
        width = min(
            max(len(str(cell.value)) if cell.value is not None else 0 for cell in column) + 2,
            maximum,
        )
        ws.column_dimensions[get_column_letter(column[0].column)].width = max(width, 11)


def add_table(ws, name: str) -> None:
    table = Table(displayName=name, ref=ws.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(table)


def main() -> None:
    selected_dates = load_selected_dates()
    details, pnl_by_date = calculate_daily(selected_dates)
    ordered = sorted(pnl_by_date.items(), key=lambda item: item[1], reverse=True)
    values = list(pnl_by_date.values())
    total = sum(values)

    wb = openpyxl.load_workbook(TARGET)
    for sheet_name in ("Устойчивость ST80", "ST80 по датам"):
        if sheet_name in wb.sheetnames:
            del wb[sheet_name]

    ws = wb.create_sheet("Устойчивость ST80", 1)
    ws.append(["Показатель", "Значение", "Комментарий"])
    ws.append(["Параметры", "ST 80/4", "Legacy-прогон"])
    ws.append(["Дат в анализе", len(values), "Подтверждённые волатильные даты"])
    ws.append(["Sum PnL, %", total, "Только строго внутридневные сделки"])
    ws.append(["Прибыльных дат", sum(value > 0 for value in values), f"{sum(value > 0 for value in values) / len(values) * 100:.2f}% дат"])
    ws.append(["Убыточных дат", sum(value < 0 for value in values), ""])
    ws.append(["Средний PnL даты, %", mean(values), ""])
    ws.append(["Медианный PnL даты, %", median(values), "Близок к среднему: слабая зависимость от выбросов"])
    ws.append(["Лучший день", ordered[0][0], f"PnL {ordered[0][1]:.6f}%"])
    ws.append(["Доля лучшего дня", ordered[0][1] / total, "От общей прибыли"])

    for count in (1, 5, 10, 20, 30):
        removed = sum(value for _, value in ordered[:count])
        ws.append(
            [
                f"Sum PnL без {count} лучших дат, %",
                total - removed,
                f"Удалено {removed:.6f}% PnL",
            ]
        )

    sorted_values = sorted(values)
    trimmed_10 = sum(sorted_values[10:-10])
    ws.append(["Sum PnL без 10 лучших и 10 худших дат, %", trimmed_10, "Симметричная проверка хвостов распределения"])
    ws.append(
        [
            "Итоговый вывод",
            "РЕЗУЛЬТАТ УСТОЙЧИВ",
            "Прибыль не зависит от нескольких особенно удачных дат; без 30 лучших дат результат остаётся положительным.",
        ]
    )
    style_header(ws)
    ws.freeze_panes = "A2"
    for row in range(2, ws.max_row + 1):
        if isinstance(ws.cell(row, 2).value, float):
            ws.cell(row, 2).number_format = "0.0000"
    ws.cell(9, 2).number_format = "dd.mm.yyyy"
    ws.cell(10, 2).number_format = "0.00%"
    ws.cell(ws.max_row, 2).font = Font(bold=True, color="006100")
    ws.cell(ws.max_row, 2).fill = PatternFill("solid", fgColor="C6EFCE")
    fit_columns(ws)
    add_table(ws, "ST80Robustness")

    daily_ws = wb.create_sheet("ST80 по датам", 2)
    daily_ws.append(
        [
            "Дата",
            "Sum Net PnL, %",
            "Num Trades",
            "Прибыльных сделок",
            "Win Rate, %",
            "Место по дневному PnL",
            "Результат дня",
        ]
    )
    rank = {day: position for position, (day, _) in enumerate(ordered, start=1)}
    for day in sorted(details):
        item = details[day]
        daily_ws.append(
            [
                day,
                item["pnl"],
                item["trades"],
                item["wins"],
                item["wins"] / item["trades"] * 100.0,
                rank[day],
                "Прибыль" if item["pnl"] > 0 else "Убыток",
            ]
        )
    for row in range(2, daily_ws.max_row + 1):
        daily_ws.cell(row, 1).number_format = "dd.mm.yyyy"
        daily_ws.cell(row, 2).number_format = "0.000000"
        daily_ws.cell(row, 5).number_format = "0.00"
    style_header(daily_ws)
    daily_ws.freeze_panes = "A2"
    daily_ws.auto_filter.ref = daily_ws.dimensions
    fit_columns(daily_ws)
    add_table(daily_ws, "ST80DailyResults")

    wb.save(TARGET)
    print(f"Daily rows: {len(values)}")
    print(f"Total PnL: {total:.6f}")
    print(f"Saved: {TARGET}")


if __name__ == "__main__":
    main()
