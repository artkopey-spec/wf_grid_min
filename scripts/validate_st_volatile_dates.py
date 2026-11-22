"""Validate ST >0.8% session dates against independent daily-volatility metrics."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA = PROJECT_ROOT / "data.csv"
TARGET = (
    PROJECT_ROOT
    / "results_tester_st_batch_20260828"
    / "0010_10_st_28_2p4_20260828_121845_895_20072_"
    "st_flip_amplitudes_intraday_only_sessions.xlsx"
)
OUTPUT = TARGET.with_name(
    f"{TARGET.stem}_volatility_top35_validation.xlsx"
)
BLUE = "1F4E78"
WHITE = "FFFFFF"
LIGHT_BLUE = "D9EAF7"
GREEN = "C6EFCE"
YELLOW = "FFEB9C"
RED = "FFC7CE"


def calculate_daily_metrics() -> pd.DataFrame:
    bars = pd.read_csv(DATA, sep=";")
    bars.columns = [column.strip("<>").lower() for column in bars.columns]
    bars["date"] = pd.to_datetime(bars["date"].astype(str), format="%Y%m%d")
    bars["previous_bar_close"] = bars.groupby("date")["close"].shift(1)
    bars["log_return"] = np.log(bars["close"] / bars["previous_bar_close"])

    grouped = bars.groupby("date", sort=True)
    daily = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("vol", "sum"),
        bars=("close", "size"),
        realized_variance=(
            "log_return",
            lambda values: float(np.nansum(np.square(values))),
        ),
    )
    daily["previous_close"] = daily["close"].shift(1)
    daily["daily_range_points"] = daily["high"] - daily["low"]
    daily["daily_range_pct"] = daily["daily_range_points"] / daily["open"] * 100.0

    previous_close = daily["previous_close"].fillna(daily["open"])
    daily["true_range_points"] = np.maximum.reduce(
        [
            (daily["high"] - daily["low"]).to_numpy(),
            (daily["high"] - previous_close).abs().to_numpy(),
            (daily["low"] - previous_close).abs().to_numpy(),
        ]
    )
    daily["true_range_pct"] = (
        daily["true_range_points"] / previous_close * 100.0
    )
    daily["realized_vol_pct"] = np.sqrt(daily["realized_variance"]) * 100.0
    daily["natr14_pct"] = (
        daily["true_range_points"].rolling(14, min_periods=14).mean()
        / daily["close"]
        * 100.0
    )
    daily["volume_pctile"] = daily["volume"].rank(pct=True) * 100.0

    for metric in (
        "daily_range_pct",
        "true_range_pct",
        "realized_vol_pct",
        "natr14_pct",
    ):
        daily[f"{metric}_pctile"] = daily[metric].rank(pct=True) * 100.0
    return daily


def read_selected_dates() -> pd.DataFrame:
    frame = pd.read_excel(TARGET, sheet_name="Sessions_Over_0.8")
    frame["Session Date"] = pd.to_datetime(frame["Session Date"])
    return frame.set_index("Session Date")


def verdict(score: int) -> str:
    if score == 3:
        return "STRONG"
    if score == 2:
        return "CONFIRMED"
    if score == 1:
        return "PARTIAL"
    return "NOT CONFIRMED"


def build_validation(selected: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    metrics = daily.loc[selected.index].copy()
    result = pd.DataFrame(index=selected.index)
    result["ST Movements > 0.8%"] = selected["Movements > 0.8%"]
    result["ST Maximum Amplitude %"] = selected["Maximum Amplitude %"]
    result["Daily Open"] = metrics["open"]
    result["Daily High"] = metrics["high"]
    result["Daily Low"] = metrics["low"]
    result["Daily Close"] = metrics["close"]
    result["Daily Range Points"] = metrics["daily_range_points"]
    result["Daily Range %"] = metrics["daily_range_pct"]
    result["Daily Range Percentile"] = metrics["daily_range_pct_pctile"]
    result["True Range %"] = metrics["true_range_pct"]
    result["True Range Percentile"] = metrics["true_range_pct_pctile"]
    result["Realized Volatility %"] = metrics["realized_vol_pct"]
    result["Realized Volatility Percentile"] = metrics[
        "realized_vol_pct_pctile"
    ]
    result["NATR(14) %"] = metrics["natr14_pct"]
    result["NATR(14) Percentile"] = metrics["natr14_pct_pctile"]
    result["Daily Volume"] = metrics["volume"]
    result["Volume Percentile"] = metrics["volume_pctile"]
    result["Daily Range > 0.8%"] = result["Daily Range %"] > 0.8

    result["Range Above Median"] = result["Daily Range Percentile"] >= 50.0
    result["Realized Vol Above Median"] = (
        result["Realized Volatility Percentile"] >= 50.0
    )
    result["NATR14 Above Median"] = result["NATR(14) Percentile"] >= 50.0
    broad_flags = result[
        [
            "Range Above Median",
            "Realized Vol Above Median",
            "NATR14 Above Median",
        ]
    ]
    result["Broad Criteria Passed"] = broad_flags.sum(axis=1)
    result["Broad Confirmation"] = result["Broad Criteria Passed"].map(verdict)

    result["Range Top 35%"] = result["Daily Range Percentile"] >= 65.0
    result["Realized Vol Top 35%"] = (
        result["Realized Volatility Percentile"] >= 65.0
    )
    result["NATR14 Top 35%"] = result["NATR(14) Percentile"] >= 65.0
    top35_flags = result[
        ["Range Top 35%", "Realized Vol Top 35%", "NATR14 Top 35%"]
    ]
    result["Top 35% Criteria Passed"] = top35_flags.sum(axis=1)
    result["Top 35% Confirmation"] = result[
        "Top 35% Criteria Passed"
    ].map(verdict)
    return result.reset_index().rename(columns={"index": "Session Date"})


def style_header(sheet) -> None:
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 48
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False


def add_validation_sheet(workbook, frame: pd.DataFrame) -> None:
    title = "Volatility_Validation_0.8"
    if title in workbook.sheetnames:
        del workbook[title]
    sheet = workbook.create_sheet(title)
    sheet.append(list(frame.columns))
    for row in frame.itertuples(index=False, name=None):
        sheet.append(
            [None if isinstance(value, float) and math.isnan(value) else value for value in row]
        )
    style_header(sheet)
    table = Table(displayName="VolatilityValidation08", ref=sheet.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)

    headers = {cell.value: cell.column for cell in sheet[1]}
    for column in range(1, sheet.max_column + 1):
        sheet.column_dimensions[sheet.cell(1, column).column_letter].width = 20
    sheet.column_dimensions["A"].width = 14
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, headers["Session Date"]).number_format = "yyyy-mm-dd"
        for header, column in headers.items():
            if header.endswith(" %"):
                sheet.cell(row, column).number_format = '0.0000"%"'
            elif header.endswith(" Percentile"):
                sheet.cell(row, column).number_format = '0.00'

    for metric in (
        "Daily Range Percentile",
        "Realized Volatility Percentile",
        "NATR(14) Percentile",
    ):
        letter = sheet.cell(1, headers[metric]).column_letter
        sheet.conditional_formatting.add(
            f"{letter}2:{letter}{sheet.max_row}",
            ColorScaleRule(
                start_type="min", start_color="F8696B",
                mid_type="percentile", mid_value=50, mid_color="FFEB84",
                end_type="max", end_color="63BE7B",
            ),
        )

    for header in ("Broad Confirmation", "Top 35% Confirmation"):
        column = headers[header]
        for row in range(2, sheet.max_row + 1):
            cell = sheet.cell(row, column)
            if cell.value in {"STRONG", "CONFIRMED"}:
                cell.fill = PatternFill("solid", fgColor=GREEN)
            elif cell.value == "PARTIAL":
                cell.fill = PatternFill("solid", fgColor=YELLOW)
            else:
                cell.fill = PatternFill("solid", fgColor=RED)


def add_summary_sheet(workbook, validation: pd.DataFrame, daily: pd.DataFrame) -> None:
    title = "Volatility_Summary"
    if title in workbook.sheetnames:
        del workbook[title]
    sheet = workbook.create_sheet(title)
    sheet.append(["Metric", "Value", "Interpretation"])
    total = len(validation)
    broad_confirmed = int((validation["Broad Criteria Passed"] >= 2).sum())
    top35_confirmed = int((validation["Top 35% Criteria Passed"] >= 2).sum())
    rows = [
        ("Selected ST >0.8% dates", total, "Dates under validation"),
        (
            "Daily Range >0.8%",
            int(validation["Daily Range > 0.8%"].sum()),
            "Absolute intraday-range confirmation",
        ),
        (
            "Broad confirmed (>=2 of 3 above median)",
            broad_confirmed,
            f"{broad_confirmed / total * 100:.2f}% of selected dates",
        ),
        (
            "Broad not confirmed",
            total - broad_confirmed,
            "Fewer than two metrics above the market median",
        ),
        (
            "Top 35% confirmed (>=2 of 3 at or above P65)",
            top35_confirmed,
            f"{top35_confirmed / total * 100:.2f}% of selected dates",
        ),
        (
            "Top 35% not confirmed",
            total - top35_confirmed,
            "Fewer than two metrics in the market's upper 35%",
        ),
        ("All trading dates", len(daily), "Percentiles calculated across all dates"),
        (
            "Market P50 Daily Range %",
            float(daily["daily_range_pct"].quantile(0.50)),
            "Median trading day",
        ),
        (
            "Market P65 Daily Range %",
            float(daily["daily_range_pct"].quantile(0.65)),
            "Upper-35% threshold",
        ),
        (
            "Market P65 Realized Volatility %",
            float(daily["realized_vol_pct"].quantile(0.65)),
            "Upper-35% threshold",
        ),
        (
            "Market P65 NATR(14) %",
            float(daily["natr14_pct"].quantile(0.65)),
            "Upper-35% regime threshold",
        ),
    ]
    for row in rows:
        sheet.append(row)
    style_header(sheet)
    sheet.column_dimensions["A"].width = 48
    sheet.column_dimensions["B"].width = 22
    sheet.column_dimensions["C"].width = 60
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, 1).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        sheet.cell(row, 1).font = Font(bold=True)
        sheet.cell(row, 3).alignment = Alignment(wrap_text=True)


def add_criteria_sheet(workbook) -> None:
    title = "Volatility_Criteria"
    if title in workbook.sheetnames:
        del workbook[title]
    sheet = workbook.create_sheet(title)
    sheet.append(["Criterion", "Formula", "Purpose"])
    rows = [
        (
            "Daily Range %",
            "(Daily High - Daily Low) / Daily Open * 100",
            "Full intraday price excursion",
        ),
        (
            "True Range %",
            "max(H-L, |H-prev close|, |L-prev close|) / prev close * 100",
            "Range including the opening gap; diagnostic only, not used in score",
        ),
        (
            "Realized Volatility %",
            "sqrt(sum(one-minute log return^2)) * 100",
            "Accumulated intraday price variability",
        ),
        (
            "NATR(14) %",
            "mean(True Range points, 14 dates) / Daily Close * 100",
            "Whether the date belongs to an elevated volatility regime",
        ),
        (
            "Broad confirmation",
            ">=2 of Range, Realized Vol and NATR14 at or above P50",
            "Volatility above a typical trading date",
        ),
        (
            "Top 35% confirmation",
            ">=2 of Range, Realized Vol and NATR14 at or above P65",
            "Volatility in the market's upper 35%",
        ),
        (
            "STRONG / CONFIRMED / PARTIAL / NOT CONFIRMED",
            "3 / 2 / 1 / 0 passed criteria",
            "Transparent status; no date is forced to pass",
        ),
    ]
    for row in rows:
        sheet.append(row)
    style_header(sheet)
    sheet.column_dimensions["A"].width = 48
    sheet.column_dimensions["B"].width = 70
    sheet.column_dimensions["C"].width = 70
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, 1).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        sheet.cell(row, 1).font = Font(bold=True)
        sheet.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top")
        sheet.cell(row, 3).alignment = Alignment(wrap_text=True, vertical="top")


def main() -> None:
    daily = calculate_daily_metrics()
    selected = read_selected_dates()
    validation = build_validation(selected, daily)

    workbook = load_workbook(TARGET)
    add_validation_sheet(workbook, validation)
    add_summary_sheet(workbook, validation, daily)
    add_criteria_sheet(workbook)
    workbook.save(OUTPUT)

    broad = int((validation["Broad Criteria Passed"] >= 2).sum())
    top35 = int((validation["Top 35% Criteria Passed"] >= 2).sum())
    print(f"Validated dates: {len(validation)}")
    print(f"Daily range >0.8%: {int(validation['Daily Range > 0.8%'].sum())}")
    print(f"Broad confirmed: {broad}")
    print(f"Top 35% confirmed: {top35}")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
