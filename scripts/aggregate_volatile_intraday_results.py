from __future__ import annotations

from collections import Counter
from pathlib import Path

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


ROOT = Path(r"C:\3.1_wf_grid\results_tester_st_batch_20260828")
DATES_FILE = ROOT / "vol data.xlsx"
OUTPUT = ROOT / "st_batch_vol_dates_intraday_results.xlsx"

FILES = [
    "test_result_legacy_20260828_133527.xlsx",
    "0001_01_st_34_3p8_20260828_121845_895_20072.xlsx",
    "0002_02_st_60_3p8_20260828_121845_895_20072.xlsx",
    "0003_03_st_30_3p6_20260828_121845_895_20072.xlsx",
    "0004_04_st_10_3p0_20260828_121845_895_20072.xlsx",
    "0005_05_st_73_4p0_20260828_121845_895_20072.xlsx",
    "0006_06_st_13_3p4_20260828_121845_895_20072.xlsx",
    "0007_07_st_40_4p0_20260828_121845_895_20072.xlsx",
    "0008_08_st_10_3p2_20260828_121845_895_20072.xlsx",
    "0009_09_st_10_2p8_20260828_121845_895_20072.xlsx",
    "0010_10_st_28_2p4_20260828_121845_895_20072.xlsx",
]


def load_dates() -> set:
    wb = openpyxl.load_workbook(DATES_FILE, read_only=True, data_only=True)
    ws = wb.active
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    date_col = headers.index("Дата сессии")
    dates = {
        row[date_col].value.date()
        for row in ws.iter_rows(min_row=2)
        if row[date_col].value is not None
    }
    wb.close()
    return dates


def max_drawdown(pnl_values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for pnl_pct in pnl_values:
        equity *= 1.0 + pnl_pct / 100.0
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1.0)
    return drawdown


def analyze(path: Path, selected_dates: set) -> tuple[dict, list[dict]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Trades_100"]
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    idx = {name: headers.index(name) for name in headers}

    included: list[tuple] = []
    excluded: list[dict] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        entry = row[idx["Entry Time"]]
        exit_ = row[idx["Exit Time"]]
        if entry is None or exit_ is None or entry.date() not in selected_dates:
            continue
        if entry.date() != exit_.date():
            excluded.append(
                {
                    "file": path.name,
                    "trade_id": row[idx["Trade ID"]],
                    "entry": entry,
                    "exit": exit_,
                    "pnl": float(row[idx["Net PnL %"]]),
                    "reason": row[idx["Trade Close Reason"]],
                }
            )
            continue
        included.append(row)

    pnl = [float(row[idx["Net PnL %"]]) for row in included]
    atr_period = None
    multiplier = None
    if "Summary" in wb.sheetnames:
        summary = wb["Summary"]
        atr_period = summary.cell(2, 2).value
        multiplier = summary.cell(2, 3).value
    wb.close()

    wins = sum(value > 0 for value in pnl)
    result = {
        "file": path.name,
        "atr_period": atr_period,
        "multiplier": multiplier,
        "sum_pnl": sum(pnl),
        "max_drawdown": max_drawdown(pnl),
        "win_rate": wins / len(pnl) * 100.0 if pnl else 0.0,
        "num_trades": len(pnl),
        "excluded": len(excluded),
    }
    return result, excluded


def style_sheet(ws, freeze: str = "A2") -> None:
    ws.freeze_panes = freeze
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for column in ws.columns:
        values = [str(cell.value) if cell.value is not None else "" for cell in column]
        width = min(max(len(value) for value in values) + 2, 48)
        ws.column_dimensions[get_column_letter(column[0].column)].width = max(width, 11)


def add_table(ws, name: str) -> None:
    if ws.max_row < 2:
        return
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
    selected_dates = load_dates()
    results = []
    all_excluded = []
    for filename in FILES:
        result, excluded = analyze(ROOT / filename, selected_dates)
        results.append(result)
        all_excluded.extend(excluded)

    ranked = sorted(results, key=lambda item: item["sum_pnl"], reverse=True)
    rank_by_file = {item["file"]: rank for rank, item in enumerate(ranked, start=1)}

    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    headers = [
        "Место по Sum PnL",
        "ATR Period",
        "Multiplier",
        "Sum PnL %",
        "Max Drawdown",
        "Max Drawdown %",
        "Win Rate %",
        "Num Trades",
        "Исключено overnight",
        "Вывод",
        "Исходный файл",
    ]
    ws.append(headers)
    for item in sorted(results, key=lambda row: rank_by_file[row["file"]]):
        conclusion = (
            "Положительный результат на подтверждённых волатильных датах"
            if item["sum_pnl"] > 0
            else "Отрицательный результат на подтверждённых волатильных датах"
        )
        ws.append(
            [
                rank_by_file[item["file"]],
                item["atr_period"],
                item["multiplier"],
                item["sum_pnl"],
                item["max_drawdown"],
                item["max_drawdown"] * 100.0,
                item["win_rate"],
                item["num_trades"],
                item["excluded"],
                conclusion,
                item["file"],
            ]
        )
    for row in range(2, ws.max_row + 1):
        ws.cell(row, 4).number_format = "0.00"
        ws.cell(row, 5).number_format = "0.0000"
        ws.cell(row, 6).number_format = "0.00"
        ws.cell(row, 7).number_format = "0.00"
    style_sheet(ws)
    add_table(ws, "VolatileIntradaySummary")

    methods = wb.create_sheet("Методика")
    method_rows = [
        ("Параметр", "Описание"),
        ("Источник дат", DATES_FILE.name),
        ("Количество выбранных дат", len(selected_dates)),
        ("Отбор по дате", "Дата Entry Time присутствует в vol data.xlsx"),
        ("Строго внутридневной фильтр", "Дата Entry Time равна дате Exit Time"),
        ("PnL", "Сумма Net PnL %, комиссия уже учтена"),
        ("Win Rate", "Доля сделок с Net PnL % > 0"),
        (
            "Max Drawdown",
            "Минимальное отклонение от предыдущего пика компаундированной кривой капитала",
        ),
        ("Порядок сделок", "Исходный хронологический порядок листа Trades_100"),
    ]
    for row in method_rows:
        methods.append(row)
    style_sheet(methods)

    excluded_ws = wb.create_sheet("Исключенные overnight")
    excluded_ws.append(
        [
            "ATR Period",
            "Multiplier",
            "Trade ID",
            "Entry Time",
            "Exit Time",
            "Net PnL %",
            "Причина закрытия",
            "Исходный файл",
        ]
    )
    params_by_file = {
        item["file"]: (item["atr_period"], item["multiplier"]) for item in results
    }
    for item in all_excluded:
        atr, multiplier = params_by_file[item["file"]]
        excluded_ws.append(
            [
                atr,
                multiplier,
                item["trade_id"],
                item["entry"],
                item["exit"],
                item["pnl"],
                item["reason"],
                item["file"],
            ]
        )
    for row in range(2, excluded_ws.max_row + 1):
        excluded_ws.cell(row, 4).number_format = "dd.mm.yyyy hh:mm"
        excluded_ws.cell(row, 5).number_format = "dd.mm.yyyy hh:mm"
        excluded_ws.cell(row, 6).number_format = "0.000000"
    style_sheet(excluded_ws)
    add_table(excluded_ws, "ExcludedOvernightTrades")

    wb.save(OUTPUT)
    print(f"Selected dates: {len(selected_dates)}")
    print(f"Runs: {len(results)}")
    print(f"Excluded overnight trades: {len(all_excluded)}")
    for item in ranked:
        print(
            f"ST {item['atr_period']}/{item['multiplier']}: "
            f"PnL={item['sum_pnl']:.6f}, DD={item['max_drawdown']:.6f}, "
            f"WR={item['win_rate']:.6f}, N={item['num_trades']}, "
            f"overnight={item['excluded']}"
        )
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
