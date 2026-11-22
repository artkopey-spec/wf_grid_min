"""Calculate full High-Low ranges between neighboring SuperTrend flips."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
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
    / "0010_10_st_28_2p4_20260828_121845_895_20072.xlsx"
)
DEFAULT_DATA = PROJECT_ROOT / "data.csv"

BLUE = "1F4E78"
WHITE = "FFFFFF"
LIGHT_BLUE = "D9EAF7"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def read_bars(path: Path) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle, delimiter=";")):
            bars.append(
                {
                    "index": index,
                    "time": datetime.strptime(
                        row["<DATE>"] + row["<TIME>"], "%Y%m%d%H%M%S"
                    ),
                    "high": float(row["<HIGH>"]),
                    "low": float(row["<LOW>"]),
                    "close": float(row["<CLOSE>"]),
                }
            )
    if not bars:
        raise ValueError(f"No bars found in {path}")
    return bars


def read_flips(path: Path) -> list[dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Signals" not in workbook.sheetnames:
            raise ValueError(f"Signals sheet not found in {path}")
        rows = workbook["Signals"].iter_rows(values_only=True)
        headers = next(rows)
        column = {name: index for index, name in enumerate(headers)}
        required = {
            "Signal Time",
            "Signal Bar Index",
            "Signal Close Price",
            "ST Color Before",
            "ST Color After",
            "Is Reversal",
        }
        missing = required.difference(column)
        if missing:
            raise ValueError(f"Signals columns missing: {sorted(missing)}")

        unique: dict[int, dict[str, object]] = {}
        for row in rows:
            if not bool(row[column["Is Reversal"]]):
                continue
            bar_index = int(row[column["Signal Bar Index"]])
            flip = {
                "bar": bar_index,
                "time": row[column["Signal Time"]],
                "price": float(row[column["Signal Close Price"]]),
                "before": row[column["ST Color Before"]],
                "after": row[column["ST Color After"]],
            }
            existing = unique.get(bar_index)
            if existing is not None and existing != flip:
                raise ValueError(f"Conflicting duplicate flip at bar {bar_index}")
            unique[bar_index] = flip
    finally:
        workbook.close()

    flips = [unique[index] for index in sorted(unique)]
    if len(flips) < 2:
        raise ValueError("At least two unique reversals are required")
    return flips


def validate_alignment(
    bars: list[dict[str, object]], flips: list[dict[str, object]]
) -> None:
    for flip in flips:
        index = int(flip["bar"])
        if index < 0 or index >= len(bars):
            raise ValueError(f"Flip bar outside data: {index}")
        bar = bars[index]
        if bar["time"] != flip["time"]:
            raise ValueError(
                f"Time mismatch at bar {index}: {bar['time']} != {flip['time']}"
            )
        if abs(float(bar["close"]) - float(flip["price"])) > 1e-9:
            raise ValueError(
                f"Close mismatch at bar {index}: {bar['close']} != {flip['price']}"
            )


def calculate_movements(
    bars: list[dict[str, object]], flips: list[dict[str, object]]
) -> list[dict[str, object]]:
    movements: list[dict[str, object]] = []
    for movement_id, (start, end) in enumerate(zip(flips, flips[1:]), start=1):
        start_index = int(start["bar"])
        end_index = int(end["bar"])
        if end_index <= start_index:
            raise ValueError(f"Non-increasing flips: {start_index}, {end_index}")
        if start["after"] != end["before"]:
            raise ValueError(
                f"ST color mismatch between bars {start_index} and {end_index}"
            )

        # Flip is confirmed at the close of start_index. Therefore the new ST
        # segment begins with the next bar and includes the bar that causes the
        # following flip.
        window = bars[start_index + 1 : end_index + 1]
        high_bar = max(window, key=lambda bar: float(bar["high"]))
        low_bar = min(window, key=lambda bar: float(bar["low"]))

        start_price = float(start["price"])
        end_price = float(end["price"])
        maximum = float(high_bar["high"])
        minimum = float(low_bar["low"])
        amplitude_points = maximum - minimum
        amplitude_pct = amplitude_points / start_price * 100.0
        close_change_points = end_price - start_price
        close_change_pct = close_change_points / start_price * 100.0
        close_to_close_abs_points = abs(close_change_points)
        close_to_close_abs_pct = close_to_close_abs_points / start_price * 100.0

        if start["after"] == "GREEN":
            favorable_points = maximum - start_price
            adverse_points = start_price - minimum
        elif start["after"] == "RED":
            favorable_points = start_price - minimum
            adverse_points = maximum - start_price
        else:
            raise ValueError(f"Unexpected ST color: {start['after']!r}")

        high_index = int(high_bar["index"])
        low_index = int(low_bar["index"])
        high_low_order = "HIGH_THEN_LOW" if high_index < low_index else (
            "LOW_THEN_HIGH" if low_index < high_index else "SAME_BAR"
        )

        movements.append(
            {
                "Movement ID": movement_id,
                "ST Color": start["after"],
                "Start Flip Time": start["time"],
                "Start Flip Index": start_index,
                "Start Flip Close": start_price,
                "End Flip Time": end["time"],
                "End Flip Index": end_index,
                "End Flip Close": end_price,
                "Bars Between Flips": end_index - start_index,
                "Segment Max High": maximum,
                "Max High Time": high_bar["time"],
                "Max High Index": high_index,
                "Segment Min Low": minimum,
                "Min Low Time": low_bar["time"],
                "Min Low Index": low_index,
                "Amplitude High-Low Points": amplitude_points,
                "Amplitude High-Low %": amplitude_pct,
                "Close-to-Close Change Points": close_change_points,
                "Close-to-Close Change %": close_change_pct,
                "Close-to-Close Abs Points": close_to_close_abs_points,
                "Close-to-Close Abs %": close_to_close_abs_pct,
                "Range Minus Close Abs Points": amplitude_points
                - close_to_close_abs_points,
                "Favorable Excursion Points": favorable_points,
                "Favorable Excursion %": favorable_points / start_price * 100.0,
                "Adverse Excursion Points": adverse_points,
                "Adverse Excursion %": adverse_points / start_price * 100.0,
                "High-Low Order": high_low_order,
                "Crosses Calendar Day": start["time"].date() != end["time"].date(),
            }
        )
    return movements


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(movements: list[dict[str, object]]) -> list[dict[str, object]]:
    groups = [
        ("ALL", movements),
        ("GREEN", [row for row in movements if row["ST Color"] == "GREEN"]),
        ("RED", [row for row in movements if row["ST Color"] == "RED"]),
    ]
    result: list[dict[str, object]] = []
    for label, rows in groups:
        points = [float(row["Amplitude High-Low Points"]) for row in rows]
        percentages = [float(row["Amplitude High-Low %"]) for row in rows]
        bars = [float(row["Bars Between Flips"]) for row in rows]
        result.append(
            {
                "Group": label,
                "Segments": len(rows),
                "Mean Amplitude Points": fmean(points),
                "Median Amplitude Points": median(points),
                "P75 Amplitude Points": percentile(points, 0.75),
                "P90 Amplitude Points": percentile(points, 0.90),
                "P95 Amplitude Points": percentile(points, 0.95),
                "Max Amplitude Points": max(points),
                "Mean Amplitude %": fmean(percentages),
                "Median Amplitude %": median(percentages),
                "P75 Amplitude %": percentile(percentages, 0.75),
                "P90 Amplitude %": percentile(percentages, 0.90),
                "P95 Amplitude %": percentile(percentages, 0.95),
                "Max Amplitude %": max(percentages),
                "Mean Bars": fmean(bars),
                "Median Bars": median(bars),
            }
        )
    return result


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


def write_workbook(
    output: Path,
    source: Path,
    data: Path,
    flips: list[dict[str, object]],
    movements: list[dict[str, object]],
) -> None:
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Summary"
    summary_rows = summarize(movements)
    summary_headers = list(summary_rows[0])
    summary_sheet.append(summary_headers)
    for row in summary_rows:
        summary_sheet.append([row[key] for key in summary_headers])
    style_table(summary_sheet, "AmplitudeSummaryTable")
    summary_sheet.column_dimensions["A"].width = 14
    for column in range(2, summary_sheet.max_column + 1):
        summary_sheet.column_dimensions[
            summary_sheet.cell(1, column).column_letter
        ].width = 23
    for row in summary_sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, float):
                cell.number_format = '0.0000'
        for column, header in enumerate(summary_headers, start=1):
            if header.endswith(" %"):
                summary_sheet.cell(row[0].row, column).number_format = '0.0000"%"'

    movement_sheet = workbook.create_sheet("Movements")
    movement_headers = list(movements[0])
    movement_sheet.append(movement_headers)
    for row in movements:
        movement_sheet.append([row[key] for key in movement_headers])
    style_table(movement_sheet, "FlipMovementsTable")
    movement_sheet.column_dimensions["A"].width = 13
    movement_sheet.column_dimensions["B"].width = 11
    for column in ("C", "F", "K", "N"):
        movement_sheet.column_dimensions[column].width = 20
    for column in range(3, movement_sheet.max_column + 1):
        letter = movement_sheet.cell(1, column).column_letter
        if movement_sheet.column_dimensions[letter].width == 13:
            movement_sheet.column_dimensions[letter].width = 18
    percent_columns = {
        index
        for index, header in enumerate(movement_headers, start=1)
        if header.endswith(" %")
    }
    datetime_columns = {
        index
        for index, header in enumerate(movement_headers, start=1)
        if header.endswith(" Time")
    }
    for row in range(2, movement_sheet.max_row + 1):
        for column in percent_columns:
            movement_sheet.cell(row, column).number_format = '0.0000"%"'
        for column in datetime_columns:
            movement_sheet.cell(row, column).number_format = "yyyy-mm-dd hh:mm:ss"

    amplitude_column = movement_headers.index("Amplitude High-Low %") + 1
    amplitude_letter = movement_sheet.cell(1, amplitude_column).column_letter
    movement_sheet.conditional_formatting.add(
        f"{amplitude_letter}2:{amplitude_letter}{movement_sheet.max_row}",
        ColorScaleRule(
            start_type="min", start_color="63BE7B",
            mid_type="percentile", mid_value=50, mid_color="FFEB84",
            end_type="max", end_color="F8696B",
        ),
    )

    top_sheet = workbook.create_sheet("Top_1000_Amplitude")
    top_sheet.append(["Rank", *movement_headers])
    top_rows = sorted(
        movements,
        key=lambda row: float(row["Amplitude High-Low Points"]),
        reverse=True,
    )[:1000]
    for rank, row in enumerate(top_rows, start=1):
        top_sheet.append([rank, *[row[key] for key in movement_headers]])
    style_table(top_sheet, "TopAmplitudeTable")
    for column in range(1, top_sheet.max_column + 1):
        top_sheet.column_dimensions[top_sheet.cell(1, column).column_letter].width = 18
    for row in range(2, top_sheet.max_row + 1):
        for column in {value + 1 for value in percent_columns}:
            top_sheet.cell(row, column).number_format = '0.0000"%"'
        for column in {value + 1 for value in datetime_columns}:
            top_sheet.cell(row, column).number_format = "yyyy-mm-dd hh:mm:ss"

    about = workbook.create_sheet("Methodology")
    about.append(["Parameter", "Value"])
    about.append(["Source workbook", str(source.resolve())])
    about.append(["OHLC source", str(data.resolve())])
    about.append(["Unique flips", len(flips)])
    about.append(["Neighboring-flip segments", len(movements)])
    about.append(["Range formula", "max(High) - min(Low) across every bar in the segment"])
    about.append(["Segment boundary", "Bars start_flip_index + 1 through end_flip_index, inclusive"])
    about.append(["Start flip bar", "Excluded because the new ST color is confirmed at its Close"])
    about.append(["End flip bar", "Included because its intrabar movement leads to the next flip"])
    about.append(["Price adjustment", "Raw data.csv prices; no rollover adjustment applied"])
    for cell in about[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
    about.sheet_view.showGridLines = False
    about.column_dimensions["A"].width = 28
    about.column_dimensions["B"].width = 110
    for row in range(2, about.max_row + 1):
        about.cell(row, 1).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        about.cell(row, 1).font = Font(bold=True)
        about.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top")

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    data = args.data.resolve()
    output = (
        args.output.resolve()
        if args.output is not None
        else source.with_name(f"{source.stem}_st_flip_amplitudes.xlsx")
    )

    bars = read_bars(data)
    flips = read_flips(source)
    validate_alignment(bars, flips)
    movements = calculate_movements(bars, flips)
    write_workbook(output, source, data, flips, movements)

    all_summary = summarize(movements)[0]
    print(f"Unique flips: {len(flips)}")
    print(f"Segments: {len(movements)}")
    print(f"Median amplitude: {all_summary['Median Amplitude Points']:.4f} points")
    print(f"Median amplitude: {all_summary['Median Amplitude %']:.4f}%")
    print(f"Mean amplitude: {all_summary['Mean Amplitude Points']:.4f} points")
    print(f"Mean amplitude: {all_summary['Mean Amplitude %']:.4f}%")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
