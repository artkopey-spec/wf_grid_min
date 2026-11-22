"""Build ST session threshold tables excluding every overnight segment."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo

from add_st_session_threshold_sheets import (
    SOURCE,
    aggregate,
    add_sheet,
    read_movements,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA = PROJECT_ROOT / "data.csv"
OUTPUT = SOURCE.with_name(f"{SOURCE.stem}_intraday_only_sessions.xlsx")
THRESHOLDS = (0.383, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
BLUE = "1F4E78"
WHITE = "FFFFFF"
LIGHT_BLUE = "D9EAF7"


def trading_dates() -> set[str]:
    result: set[str] = set()
    with DATA.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            result.add(row["<DATE>"])
    return result


def qualifying_movements(
    movements: list[dict[str, object]], threshold: float
) -> list[dict[str, object]]:
    return [
        row
        for row in movements
        if float(row["Amplitude %"]) > threshold
    ]


def add_summary(
    workbook: Workbook,
    intraday: list[dict[str, object]],
    all_movements_count: int,
    total_trading_dates: int,
) -> None:
    sheet = workbook.create_sheet("Summary", 0)
    headers = [
        "Threshold Condition",
        "Sessions",
        "Share of All Trading Dates %",
        "Movements",
        "GREEN Movements",
        "RED Movements",
        "Maximum Amplitude %",
        "First Session",
        "Last Session",
    ]
    sheet.append(headers)
    for threshold in THRESHOLDS:
        selected = qualifying_movements(intraday, threshold)
        dates = sorted({row["End Flip Time"].date() for row in selected})
        sheet.append(
            [
                f"> {threshold:.3f}%" if threshold == 0.383 else f"> {threshold:.1f}%",
                len(dates),
                len(dates) / total_trading_dates * 100.0,
                len(selected),
                sum(row["ST Color"] == "GREEN" for row in selected),
                sum(row["ST Color"] == "RED" for row in selected),
                max(float(row["Amplitude %"]) for row in selected),
                dates[0],
                dates[-1],
            ]
        )

    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 42
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for column in range(1, sheet.max_column + 1):
        sheet.column_dimensions[sheet.cell(1, column).column_letter].width = 24
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, 3).number_format = '0.00"%"'
        sheet.cell(row, 7).number_format = '0.0000"%"'
        sheet.cell(row, 8).number_format = "yyyy-mm-dd"
        sheet.cell(row, 9).number_format = "yyyy-mm-dd"
    table = Table(displayName="IntradayThresholdSummary", ref=sheet.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)

    methodology = workbook.create_sheet("Methodology")
    methodology.append(["Parameter", "Value"])
    methodology.append(["Source amplitudes", str(SOURCE.resolve())])
    methodology.append(["OHLC source", str(DATA.resolve())])
    methodology.append(["All neighboring-flip segments", all_movements_count])
    methodology.append(["Intraday-only segments", len(intraday)])
    methodology.append(["Overnight segments excluded", all_movements_count - len(intraday)])
    methodology.append(["All trading dates in data.csv", total_trading_dates])
    methodology.append(
        [
            "Intraday rule",
            "Start Flip Time and End Flip Time must have the same calendar date",
        ]
    )
    methodology.append(
        [
            "Overnight handling",
            "A segment crossing a calendar date boundary is excluded in full; it is not clipped",
        ]
    )
    methodology.append(
        [
            "Amplitude",
            "max(High) - min(Low) over all bars of one uninterrupted ST-color segment",
        ]
    )
    methodology.append(
        [
            "Session assignment",
            "The common calendar date of the start and end flips",
        ]
    )
    for cell in methodology[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
    methodology.sheet_view.showGridLines = False
    methodology.column_dimensions["A"].width = 32
    methodology.column_dimensions["B"].width = 115
    for row in range(2, methodology.max_row + 1):
        methodology.cell(row, 1).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        methodology.cell(row, 1).font = Font(bold=True)
        methodology.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top")


def add_detail_sheet(
    workbook: Workbook, movements: list[dict[str, object]], threshold: float
) -> None:
    selected = qualifying_movements(movements, threshold)
    sheet = workbook.create_sheet("Movements_Over_0.383")
    headers = [
        "Movement ID",
        "Session Date",
        "ST Color",
        "Start Flip Time",
        "End Flip Time",
        "Bars Between Flips",
        "Amplitude Points",
        "Amplitude %",
    ]
    sheet.append(headers)
    for row in sorted(selected, key=lambda value: value["End Flip Time"]):
        sheet.append(
            [
                row["Movement ID"],
                row["End Flip Time"].date(),
                row["ST Color"],
                row["Start Flip Time"],
                row["End Flip Time"],
                row["Bars Between Flips"],
                row["Amplitude Points"],
                row["Amplitude %"],
            ]
        )
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for column in range(1, sheet.max_column + 1):
        sheet.column_dimensions[sheet.cell(1, column).column_letter].width = 22
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, 2).number_format = "yyyy-mm-dd"
        sheet.cell(row, 4).number_format = "yyyy-mm-dd hh:mm:ss"
        sheet.cell(row, 5).number_format = "yyyy-mm-dd hh:mm:ss"
        sheet.cell(row, 8).number_format = '0.0000"%"'
    table = Table(displayName="IntradayMovementsOver0383", ref=sheet.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)


def main() -> None:
    all_movements = read_movements()
    intraday = [
        row
        for row in all_movements
        if row["Start Flip Time"].date() == row["End Flip Time"].date()
    ]
    total_dates = len(trading_dates())

    workbook = Workbook()
    del workbook[workbook.sheetnames[0]]
    add_summary(workbook, intraday, len(all_movements), total_dates)
    for threshold in THRESHOLDS:
        rows = aggregate(intraday, threshold)
        add_sheet(workbook, threshold, rows)
    add_detail_sheet(workbook, intraday, 0.383)
    workbook.save(OUTPUT)

    print(f"All movements: {len(all_movements)}")
    print(f"Intraday movements: {len(intraday)}")
    print(f"Overnight excluded: {len(all_movements) - len(intraday)}")
    for threshold in THRESHOLDS:
        selected = qualifying_movements(intraday, threshold)
        sessions = {row["End Flip Time"].date() for row in selected}
        print(f"> {threshold}%: {len(sessions)} sessions, {len(selected)} movements")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
