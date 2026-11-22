"""Build a per-session table for ST movements above an amplitude threshold."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    PROJECT_ROOT
    / "results_tester_st_batch_20260828"
    / "0010_10_st_28_2p4_20260828_121845_895_20072_st_flip_amplitudes.xlsx"
)
THRESHOLD_PCT = 0.383
BLUE = "1F4E78"
WHITE = "FFFFFF"
LIGHT_BLUE = "D9EAF7"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--threshold", type=float, default=THRESHOLD_PCT)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def read_qualifying(path: Path, threshold: float) -> list[dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["Movements"]
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows)
        column = {name: index for index, name in enumerate(headers)}
        required = {
            "Movement ID",
            "ST Color",
            "Start Flip Time",
            "End Flip Time",
            "Bars Between Flips",
            "Segment Max High",
            "Segment Min Low",
            "Amplitude High-Low Points",
            "Amplitude High-Low %",
        }
        missing = required.difference(column)
        if missing:
            raise ValueError(f"Movement columns missing: {sorted(missing)}")

        result: list[dict[str, object]] = []
        for row in rows:
            amplitude_pct = float(row[column["Amplitude High-Low %"]])
            if amplitude_pct <= threshold:
                continue
            end_time = row[column["End Flip Time"]]
            result.append(
                {
                    "Movement ID": int(row[column["Movement ID"]]),
                    "Session Date": end_time.date(),
                    "ST Color": row[column["ST Color"]],
                    "Start Flip Time": row[column["Start Flip Time"]],
                    "End Flip Time": end_time,
                    "Bars Between Flips": int(row[column["Bars Between Flips"]]),
                    "Segment Max High": float(row[column["Segment Max High"]]),
                    "Segment Min Low": float(row[column["Segment Min Low"]]),
                    "Amplitude Points": float(
                        row[column["Amplitude High-Low Points"]]
                    ),
                    "Amplitude %": amplitude_pct,
                }
            )
    finally:
        workbook.close()
    return result


def aggregate_sessions(
    movements: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[date, list[dict[str, object]]] = defaultdict(list)
    for movement in movements:
        grouped[movement["Session Date"]].append(movement)

    sessions: list[dict[str, object]] = []
    for session_date in sorted(grouped):
        rows = sorted(grouped[session_date], key=lambda row: row["End Flip Time"])
        amplitudes = [float(row["Amplitude %"]) for row in rows]
        largest = max(rows, key=lambda row: float(row["Amplitude %"]))
        sessions.append(
            {
                "Session Date": session_date,
                "Movements > 0.383%": len(rows),
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
    return sessions


def style_table(sheet, table_name: str) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 42
    table = Table(displayName=table_name, ref=sheet.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)


def write_output(
    output: Path,
    source: Path,
    threshold: float,
    sessions: list[dict[str, object]],
    movements: list[dict[str, object]],
) -> None:
    workbook = Workbook()
    session_sheet = workbook.active
    session_sheet.title = "Sessions_Over_0.383"
    session_headers = list(sessions[0])
    session_sheet.append(session_headers)
    for row in sessions:
        session_sheet.append([row[key] for key in session_headers])
    style_table(session_sheet, "SessionsOverThresholdTable")
    for column in range(1, session_sheet.max_column + 1):
        letter = session_sheet.cell(1, column).column_letter
        session_sheet.column_dimensions[letter].width = 20
    session_sheet.column_dimensions["A"].width = 14
    for row in range(2, session_sheet.max_row + 1):
        session_sheet.cell(row, 1).number_format = "yyyy-mm-dd"
        for column, header in enumerate(session_headers, start=1):
            if header.endswith(" %"):
                session_sheet.cell(row, column).number_format = '0.0000"%"'
            elif "Start" in header or "End" in header:
                session_sheet.cell(row, column).number_format = "yyyy-mm-dd hh:mm:ss"

    max_pct_column = session_headers.index("Maximum Amplitude %") + 1
    max_pct_letter = session_sheet.cell(1, max_pct_column).column_letter
    session_sheet.conditional_formatting.add(
        f"{max_pct_letter}2:{max_pct_letter}{session_sheet.max_row}",
        ColorScaleRule(
            start_type="min", start_color="63BE7B",
            mid_type="percentile", mid_value=50, mid_color="FFEB84",
            end_type="max", end_color="F8696B",
        ),
    )

    detail_sheet = workbook.create_sheet("Qualifying_Movements")
    detail_headers = list(movements[0])
    detail_sheet.append(detail_headers)
    for row in sorted(movements, key=lambda item: item["End Flip Time"]):
        detail_sheet.append([row[key] for key in detail_headers])
    style_table(detail_sheet, "QualifyingMovementsTable")
    for column in range(1, detail_sheet.max_column + 1):
        detail_sheet.column_dimensions[
            detail_sheet.cell(1, column).column_letter
        ].width = 21
    for row in range(2, detail_sheet.max_row + 1):
        detail_sheet.cell(row, detail_headers.index("Session Date") + 1).number_format = "yyyy-mm-dd"
        detail_sheet.cell(row, detail_headers.index("Amplitude %") + 1).number_format = '0.0000"%"'
        for header in ("Start Flip Time", "End Flip Time"):
            detail_sheet.cell(row, detail_headers.index(header) + 1).number_format = "yyyy-mm-dd hh:mm:ss"

    summary = workbook.create_sheet("Summary")
    summary.append(["Metric", "Value"])
    summary.append(["Amplitude condition", f"> {threshold:.3f}%"])
    summary.append(["Sessions with at least one qualifying movement", len(sessions)])
    summary.append(["Qualifying movements", len(movements)])
    summary.append(["GREEN movements", sum(row["ST Color"] == "GREEN" for row in movements)])
    summary.append(["RED movements", sum(row["ST Color"] == "RED" for row in movements)])
    summary.append(["First qualifying session", min(row["Session Date"] for row in sessions)])
    summary.append(["Last qualifying session", max(row["Session Date"] for row in sessions)])
    summary.append(["Maximum movement %", max(float(row["Amplitude %"]) for row in movements)])
    for cell in summary[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
    summary.sheet_view.showGridLines = False
    summary.column_dimensions["A"].width = 48
    summary.column_dimensions["B"].width = 30
    for row in range(2, summary.max_row + 1):
        summary.cell(row, 1).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        summary.cell(row, 1).font = Font(bold=True)

    methodology = workbook.create_sheet("Methodology")
    methodology.append(["Parameter", "Value"])
    methodology.append(["Source", str(source.resolve())])
    methodology.append(["Condition", f"Amplitude High-Low % > {threshold:.3f}% (strict)"])
    methodology.append(["Session assignment", "Calendar date of End Flip Time"])
    methodology.append(["Reason", "The complete segment amplitude becomes known at the next flip"])
    methodology.append(["Movement definition", "One continuous ST-color segment between neighboring flips"])
    for cell in methodology[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
    methodology.sheet_view.showGridLines = False
    methodology.column_dimensions["A"].width = 28
    methodology.column_dimensions["B"].width = 110
    for row in range(2, methodology.max_row + 1):
        methodology.cell(row, 1).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        methodology.cell(row, 1).font = Font(bold=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    threshold = float(args.threshold)
    output = (
        args.output.resolve()
        if args.output is not None
        else source.with_name(f"{source.stem}_sessions_over_0p383.xlsx")
    )
    movements = read_qualifying(source, threshold)
    if not movements:
        raise SystemExit(f"No movements above {threshold}%")
    sessions = aggregate_sessions(movements)
    write_output(output, source, threshold, sessions, movements)
    print(f"Sessions: {len(sessions)}")
    print(f"Movements: {len(movements)}")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
