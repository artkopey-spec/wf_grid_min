"""Add per-session ST amplitude tables for additional thresholds."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import fmean, median

from openpyxl import load_workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "results_tester_st_batch_20260828"
SOURCE = RESULTS_DIR / (
    "0010_10_st_28_2p4_20260828_121845_895_20072_"
    "st_flip_amplitudes.xlsx"
)
TARGET = RESULTS_DIR / (
    "0010_10_st_28_2p4_20260828_121845_895_20072_"
    "st_flip_amplitudes_sessions_over_0p383.xlsx"
)
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
BLUE = "1F4E78"
WHITE = "FFFFFF"


def threshold_label(threshold: float) -> str:
    return f"{threshold:.3f}" if abs(threshold - 0.383) < 1e-12 else f"{threshold:.1f}"


def read_movements() -> list[dict[str, object]]:
    workbook = load_workbook(SOURCE, read_only=True, data_only=True)
    try:
        rows = workbook["Movements"].iter_rows(values_only=True)
        headers = next(rows)
        column = {name: index for index, name in enumerate(headers)}
        result = []
        for row in rows:
            result.append(
                {
                    "Movement ID": int(row[column["Movement ID"]]),
                    "ST Color": row[column["ST Color"]],
                    "Start Flip Time": row[column["Start Flip Time"]],
                    "End Flip Time": row[column["End Flip Time"]],
                    "Bars Between Flips": int(row[column["Bars Between Flips"]]),
                    "Amplitude Points": float(
                        row[column["Amplitude High-Low Points"]]
                    ),
                    "Amplitude %": float(row[column["Amplitude High-Low %"]]),
                }
            )
    finally:
        workbook.close()
    return result


def aggregate(
    movements: list[dict[str, object]], threshold: float
) -> list[dict[str, object]]:
    qualifying = [row for row in movements if float(row["Amplitude %"]) > threshold]
    grouped: dict[object, list[dict[str, object]]] = defaultdict(list)
    for movement in qualifying:
        grouped[movement["End Flip Time"].date()].append(movement)

    count_header = f"Movements > {threshold_label(threshold)}%"
    result: list[dict[str, object]] = []
    for session_date in sorted(grouped):
        rows = sorted(grouped[session_date], key=lambda row: row["End Flip Time"])
        amplitudes = [float(row["Amplitude %"]) for row in rows]
        largest = max(rows, key=lambda row: float(row["Amplitude %"]))
        result.append(
            {
                "Session Date": session_date,
                count_header: len(rows),
                "GREEN Movements": sum(row["ST Color"] == "GREEN" for row in rows),
                "RED Movements": sum(row["ST Color"] == "RED" for row in rows),
                "Maximum Amplitude %": max(amplitudes),
                "Mean Amplitude %": fmean(amplitudes),
                "Median Amplitude %": median(amplitudes),
                "First Movement Start": min(row["Start Flip Time"] for row in rows),
                "Last Movement End": max(row["End Flip Time"] for row in rows),
                "Largest Movement ID": largest["Movement ID"],
                "Largest Movement Color": largest["ST Color"],
                "Largest Movement Start": largest["Start Flip Time"],
                "Largest Movement End": largest["End Flip Time"],
                "Largest Movement Points": largest["Amplitude Points"],
                "Largest Movement Bars": largest["Bars Between Flips"],
            }
        )
    return result


def add_sheet(workbook, threshold: float, rows: list[dict[str, object]]) -> None:
    title = f"Sessions_Over_{threshold_label(threshold)}"
    if title in workbook.sheetnames:
        del workbook[title]
    sheet = workbook.create_sheet(title)
    headers = list(rows[0])
    sheet.append(headers)
    for row in rows:
        sheet.append([row[key] for key in headers])

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 42

    table_suffix = threshold_label(threshold).replace(".", "p")
    table = Table(displayName=f"SessionsOver{table_suffix}Table", ref=sheet.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)

    for column in range(1, sheet.max_column + 1):
        sheet.column_dimensions[sheet.cell(1, column).column_letter].width = 20
    sheet.column_dimensions["A"].width = 14
    for row_number in range(2, sheet.max_row + 1):
        sheet.cell(row_number, 1).number_format = "yyyy-mm-dd"
        for column, header in enumerate(headers, start=1):
            if header.endswith(" %"):
                sheet.cell(row_number, column).number_format = '0.0000"%"'
            elif "Start" in header or "End" in header:
                sheet.cell(row_number, column).number_format = "yyyy-mm-dd hh:mm:ss"

    max_pct_column = headers.index("Maximum Amplitude %") + 1
    max_pct_letter = sheet.cell(1, max_pct_column).column_letter
    sheet.conditional_formatting.add(
        f"{max_pct_letter}2:{max_pct_letter}{sheet.max_row}",
        ColorScaleRule(
            start_type="min", start_color="63BE7B",
            mid_type="percentile", mid_value=50, mid_color="FFEB84",
            end_type="max", end_color="F8696B",
        ),
    )


def main() -> None:
    movements = read_movements()
    workbook = load_workbook(TARGET)
    counts = []
    for threshold in THRESHOLDS:
        rows = aggregate(movements, threshold)
        if not rows:
            raise ValueError(f"No sessions for threshold {threshold}")
        add_sheet(workbook, threshold, rows)
        qualifying_count = sum(
            float(row["Amplitude %"]) > threshold for row in movements
        )
        counts.append((threshold, len(rows), qualifying_count))
    workbook.save(TARGET)
    for threshold, sessions, movements_count in counts:
        print(
            f"> {threshold:.1f}%: {sessions} sessions, "
            f"{movements_count} movements"
        )
    print(f"Updated: {TARGET}")


if __name__ == "__main__":
    main()
