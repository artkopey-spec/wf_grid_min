"""Aggregate Summary sheets from tester batch XLSX reports."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "results_tester_st_batch_20260828"
DEFAULT_OUTPUT = DEFAULT_INPUT / "aggregated_tester_summaries_20260828.xlsx"

BLUE = "1F4E78"
LIGHT_BLUE = "D9EAF7"
LIGHT_GRAY = "E7E6E6"
WHITE = "FFFFFF"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def source_files(input_dir: Path, output: Path) -> list[Path]:
    files = [
        path
        for path in input_dir.glob("*.xlsx")
        if not path.name.startswith("~$")
        and path.resolve() != output.resolve()
        and not path.name.startswith("aggregated_tester_summaries_")
    ]
    return sorted(files, key=lambda path: path.name)


def read_summary(path: Path) -> tuple[dict[str, object], list[tuple[object, ...]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Summary" not in workbook.sheetnames:
            raise ValueError(f"Summary sheet not found: {path}")
        sheet = workbook["Summary"]
        raw_rows = list(
            sheet.iter_rows(
                min_row=1,
                max_row=sheet.max_row,
                max_col=sheet.max_column,
                values_only=True,
            )
        )
    finally:
        workbook.close()

    if len(raw_rows) < 5:
        raise ValueError(f"Unexpected Summary layout in {path}: {len(raw_rows)} rows")

    metrics_header = raw_rows[0]
    metrics_values = raw_rows[1]
    filter_header = raw_rows[3]
    filter_values = raw_rows[4]
    metrics = dict(zip(metrics_header, metrics_values))
    filter_stats = {
        key: value
        for key, value in zip(filter_header, filter_values)
        if key is not None
    }

    record: dict[str, object] = {
        "Source File": path.name,
        "Period": metrics.get("Period"),
        "ATR Period": metrics.get("ATR Period"),
        "Multiplier": metrics.get("Multiplier"),
        "Mode": metrics.get("Mode"),
        "Sum PnL %": metrics.get("Sum PnL %"),
        "Sharpe": metrics.get("Sharpe"),
        "Sortino": metrics.get("Sortino"),
        "Max Drawdown": metrics.get("Max Drawdown"),
        "Win Rate %": metrics.get("Win Rate"),
        "Num Trades": metrics.get("Num Trades"),
        "Bars OFF": filter_stats.get("Bars OFF"),
        "Filter Mode": filter_stats.get("Filter Mode"),
        "Time Filter Reset Count": filter_stats.get("Time Filter Reset Count"),
        "Time Filter Bars In Window": filter_stats.get("Time Filter Bars In Window"),
        "Time Filter Bars Out Window": filter_stats.get("Time Filter Bars Out Window"),
        "Time Filter Blocked Entries": filter_stats.get("Time Filter Blocked Entries"),
        "Time Filter Forced Exits": filter_stats.get("Time Filter Forced Exits"),
    }
    return record, raw_rows


def style_header(sheet, row: int = 1) -> None:
    for cell in sheet[row]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[row].height = 32


def add_table(sheet, name: str) -> None:
    table = Table(displayName=name, ref=sheet.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)


def build_workbook(
    records: list[dict[str, object]],
    raw_by_source: list[tuple[str, list[tuple[object, ...]]]],
    input_dir: Path,
) -> Workbook:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Aggregated_Summary"

    records = sorted(
        records,
        key=lambda row: float(row["Sum PnL %"]),
        reverse=True,
    )
    headers = ["Rank", *records[0].keys()]
    summary.append(headers)
    for rank, record in enumerate(records, start=1):
        summary.append([rank, *record.values()])

    style_header(summary)
    summary.freeze_panes = "A2"
    summary.auto_filter.ref = summary.dimensions
    summary.sheet_view.showGridLines = False
    add_table(summary, "AggregatedSummaryTable")

    widths = {
        "A": 8,
        "B": 54,
        "C": 11,
        "D": 12,
        "E": 12,
        "F": 12,
        "G": 14,
        "H": 12,
        "I": 12,
        "J": 15,
        "K": 13,
        "L": 13,
        "M": 11,
        "N": 15,
        "O": 22,
        "P": 23,
        "Q": 25,
        "R": 26,
        "S": 27,
    }
    for column, width in widths.items():
        summary.column_dimensions[column].width = width

    header_index = {cell.value: cell.column for cell in summary[1]}
    for row in range(2, summary.max_row + 1):
        summary.cell(row, header_index["Sum PnL %"]).number_format = '0.00"%"'
        summary.cell(row, header_index["Win Rate %"]).number_format = '0.00"%"'
        summary.cell(row, header_index["Max Drawdown"]).number_format = "0.00%"
        summary.cell(row, header_index["Multiplier"]).number_format = "0.0"
        summary.cell(row, header_index["Sharpe"]).number_format = "0.0000"
        summary.cell(row, header_index["Sortino"]).number_format = "0.0000"

    pnl_letter = summary.cell(1, header_index["Sum PnL %"]).column_letter
    dd_letter = summary.cell(1, header_index["Max Drawdown"]).column_letter
    summary.conditional_formatting.add(
        f"{pnl_letter}2:{pnl_letter}{summary.max_row}",
        ColorScaleRule(
            start_type="min", start_color="F8696B",
            mid_type="percentile", mid_value=50, mid_color="FFEB84",
            end_type="max", end_color="63BE7B",
        ),
    )
    summary.conditional_formatting.add(
        f"{dd_letter}2:{dd_letter}{summary.max_row}",
        ColorScaleRule(
            start_type="min", start_color="F8696B",
            mid_type="percentile", mid_value=50, mid_color="FFEB84",
            end_type="max", end_color="63BE7B",
        ),
    )

    raw = workbook.create_sheet("Raw_Summary")
    raw.append(["Source File", "Summary Row", *[f"Column {i}" for i in range(1, 11)]])
    for source_name, source_rows in raw_by_source:
        for row_number, values in enumerate(source_rows, start=1):
            padded = list(values[:10]) + [None] * max(0, 10 - len(values))
            raw.append([source_name, row_number, *padded[:10]])
    style_header(raw)
    raw.freeze_panes = "C2"
    raw.auto_filter.ref = raw.dimensions
    raw.sheet_view.showGridLines = False
    add_table(raw, "RawSummaryTable")
    raw.column_dimensions["A"].width = 54
    raw.column_dimensions["B"].width = 14
    for column in "CDEFGHIJKL":
        raw.column_dimensions[column].width = 22

    about = workbook.create_sheet("About")
    about.append(["Parameter", "Value"])
    about.append(["Created", datetime.now().isoformat(timespec="seconds")])
    about.append(["Source directory", str(input_dir.resolve())])
    about.append(["Source workbooks", len(records)])
    about.append(["Source sheet", "Summary"])
    about.append(["Ranking", "Descending by Sum PnL %"])
    about.append(["Aggregation", "Main metrics and time-filter statistics combined into one row per run"])
    style_header(about)
    about.sheet_view.showGridLines = False
    about.column_dimensions["A"].width = 24
    about.column_dimensions["B"].width = 100
    for row in range(2, about.max_row + 1):
        about.cell(row, 1).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        about.cell(row, 1).font = Font(bold=True)
        about.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top")

    return workbook


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output = args.output.resolve()
    files = source_files(input_dir, output)
    if not files:
        raise SystemExit(f"No source XLSX files found in {input_dir}")

    records: list[dict[str, object]] = []
    raw_by_source: list[tuple[str, list[tuple[object, ...]]]] = []
    for path in files:
        record, raw_rows = read_summary(path)
        records.append(record)
        raw_by_source.append((path.name, raw_rows))

    workbook = build_workbook(records, raw_by_source, input_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    print(f"Saved {len(records)} summaries to {output}")


if __name__ == "__main__":
    main()
