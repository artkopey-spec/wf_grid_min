"""Backtest causal global-ZZ alignment entries after intraday ST flips."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from statistics import fmean, median

import numpy as np
from openpyxl import Workbook

from analyze_st_segment_zigzag_clusters import (
    DEFAULT_DATA,
    DEFAULT_DATES,
    DEFAULT_SIGNALS,
    build_segments,
    read_bars,
    read_dates,
    read_flips,
    style,
    validate,
)


OUTPUT = Path("results_tester_st_batch_20260828/st80_4_global_zz_flip_continuation_tp_sl.xlsx")
ZZ_THRESHOLD_PCT = 0.2
COMMISSION_ONE_WAY_PCT = 0.00667
TP_VALUES = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50)
SL_VALUES = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--dates", type=Path, default=DEFAULT_DATES)
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args()


def causal_zz_swing(closes: np.ndarray, reversal_pct: float) -> np.ndarray:
    """Current confirmed ZZ swing: +1 tracks a high, -1 tracks a low."""
    reversal = reversal_pct / 100
    result = np.zeros(len(closes), dtype=np.int8)
    state = 0
    low = high = float(closes[0])
    for index in range(1, len(closes)):
        price = float(closes[index])
        if state == 0:
            low = min(low, price)
            high = max(high, price)
            if price >= low * (1 + reversal):
                state = 1
                high = price
            elif price <= high * (1 - reversal):
                state = -1
                low = price
        elif state == 1:
            if price >= high:
                high = price
            elif price <= high * (1 - reversal):
                state = -1
                low = price
        else:
            if price <= low:
                low = price
            elif price >= low * (1 + reversal):
                state = 1
                high = price
        result[index] = state
    return result


def max_drawdown(returns: list[float]) -> float:
    equity = peak = 0.0
    drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def simulate_trade(bars: list[dict], segment: dict, tp_pct: float, sl_pct: float) -> dict:
    direction = 1 if segment["ST Color"] == "GREEN" else -1
    entry_index = segment["Start Index"] + 1
    scheduled_exit_index = segment["End Index"] + 1
    entry = bars[entry_index]["open"]
    tp = entry * (1 + direction * tp_pct / 100)
    sl = entry * (1 - direction * sl_pct / 100)
    exit_price = bars[scheduled_exit_index]["open"]
    exit_index = scheduled_exit_index
    reason = "NEXT_FLIP_OPEN"
    ambiguous = False

    for index in range(entry_index, segment["End Index"] + 1):
        bar = bars[index]
        tp_hit = bar["high"] >= tp if direction > 0 else bar["low"] <= tp
        sl_hit = bar["low"] <= sl if direction > 0 else bar["high"] >= sl
        if tp_hit and sl_hit:
            # Minute OHLC has no intrabar ordering. Use the adverse result.
            exit_price, exit_index, reason, ambiguous = sl, index, "SL_SAME_BAR_CONSERVATIVE", True
            break
        if sl_hit:
            exit_price, exit_index, reason = sl, index, "SL"
            break
        if tp_hit:
            exit_price, exit_index, reason = tp, index, "TP"
            break

    gross = direction * (exit_price - entry) / entry * 100
    net = gross - 2 * COMMISSION_ONE_WAY_PCT
    return {
        "Segment ID": segment["Segment ID"],
        "Date": segment["Date"],
        "ST Color": segment["ST Color"],
        "Entry Time": bars[entry_index]["time"],
        "Entry Price": entry,
        "Exit Time": bars[exit_index]["time"],
        "Exit Price": exit_price,
        "Exit Reason": reason,
        "Same-Bar Ambiguous": ambiguous,
        "Gross PnL %": gross,
        "Net PnL %": net,
        "Holding Minutes": exit_index - entry_index,
    }


def summarize(trades: list[dict], tp: float, sl: float, universe: str) -> dict:
    net = [row["Net PnL %"] for row in trades]
    positive = sum(value for value in net if value > 0)
    negative = -sum(value for value in net if value < 0)
    reasons = Counter(row["Exit Reason"] for row in trades)
    return {
        "Universe": universe,
        "TP %": tp,
        "SL %": sl,
        "Trades": len(trades),
        "Win Rate %": sum(value > 0 for value in net) / len(net) * 100,
        "Mean Net PnL %": fmean(net),
        "Median Net PnL %": median(net),
        "Total Net PnL % additive": sum(net),
        "Profit Factor": positive / negative if negative else None,
        "Max Drawdown % additive": max_drawdown(net),
        "TP Share %": reasons["TP"] / len(trades) * 100,
        "SL Share %": (reasons["SL"] + reasons["SL_SAME_BAR_CONSERVATIVE"]) / len(trades) * 100,
        "Flip Exit Share %": reasons["NEXT_FLIP_OPEN"] / len(trades) * 100,
        "Same-Bar Ambiguous": reasons["SL_SAME_BAR_CONSERVATIVE"],
        "Mean Holding Minutes": fmean(row["Holding Minutes"] for row in trades),
    }


def forward_path_stats(bars: list[dict], segments: list[dict]) -> list[dict]:
    rows = []
    for segment in segments:
        direction = 1 if segment["ST Color"] == "GREEN" else -1
        entry_index = segment["Start Index"] + 1
        entry = bars[entry_index]["open"]
        window = bars[entry_index:segment["End Index"] + 1]
        mfe = (max(bar["high"] for bar in window) - entry) / entry * 100 if direction > 0 else (entry - min(bar["low"] for bar in window)) / entry * 100
        mae = (entry - min(bar["low"] for bar in window)) / entry * 100 if direction > 0 else (max(bar["high"] for bar in window) - entry) / entry * 100
        rows.append({
            "Segment ID": segment["Segment ID"],
            "Date": segment["Date"],
            "ST Color": segment["ST Color"],
            "Start Flip Time": segment["Start Flip Time"],
            "Global ZZ Aligned at Flip": segment["Global ZZ Aligned at Flip"],
            "Entry Time": bars[entry_index]["time"],
            "MFE to Next Flip %": max(0.0, mfe),
            "MAE to Next Flip %": max(0.0, mae),
            "Return at Next Flip Open %": direction * (bars[segment["End Index"] + 1]["open"] - entry) / entry * 100,
        })
    return rows


def add_sheet(book: Workbook, name: str, rows: list[dict]) -> None:
    sheet = book.create_sheet(name)
    headers = list(rows[0])
    sheet.append(headers)
    for row in rows:
        sheet.append([row[key] for key in headers])


def main() -> None:
    paths = parse_args()
    dates = read_dates(paths.dates)
    bars = read_bars(paths.data)
    flips, params = read_flips(paths.signals)
    validate(bars, flips, params)
    segments, _ = build_segments(bars, flips, dates, ZZ_THRESHOLD_PCT, 0.5)
    closes = np.array([bar["close"] for bar in bars], dtype=float)
    swing = causal_zz_swing(closes, ZZ_THRESHOLD_PCT)
    for segment in segments:
        expected = 1 if segment["ST Color"] == "GREEN" else -1
        segment["Global ZZ Swing at Flip"] = int(swing[segment["Start Index"]])
        segment["Global ZZ Aligned at Flip"] = bool(swing[segment["Start Index"]] == expected)

    universes = {
        "ALL_ST_FLIPS": segments,
        "GLOBAL_ZZ_ALIGNED": [row for row in segments if row["Global ZZ Aligned at Flip"]],
        "GLOBAL_ZZ_NOT_ALIGNED": [row for row in segments if not row["Global ZZ Aligned at Flip"]],
    }
    grid, best_trades = [], []
    for universe, rows in universes.items():
        if not rows:
            continue
        for tp in TP_VALUES:
            for sl in SL_VALUES:
                trades = [simulate_trade(bars, segment, tp, sl) for segment in rows]
                result = summarize(trades, tp, sl, universe)
                grid.append(result)
        best = max((row for row in grid if row["Universe"] == universe), key=lambda row: row["Mean Net PnL %"])
        best_trades.extend(simulate_trade(bars, segment, best["TP %"], best["SL %"]) | {"Universe": universe, "TP %": best["TP %"], "SL %": best["SL %"]} for segment in rows)

    # Select TP/SL on the earlier 70% of the common chronology, then freeze
    # those parameters and evaluate only on the later 30%.
    ordered = sorted(segments, key=lambda row: row["Start Flip Time"])
    cutoff = ordered[int(len(ordered) * 0.70)]["Start Flip Time"]
    oos_results, oos_trades = [], []
    for universe, rows in universes.items():
        train_rows = [row for row in rows if row["Start Flip Time"] < cutoff]
        test_rows = [row for row in rows if row["Start Flip Time"] >= cutoff]
        if not train_rows or not test_rows:
            continue
        train_grid = []
        for tp in TP_VALUES:
            for sl in SL_VALUES:
                train_trades = [simulate_trade(bars, segment, tp, sl) for segment in train_rows]
                train_grid.append(summarize(train_trades, tp, sl, universe))
        selected = max(train_grid, key=lambda row: row["Mean Net PnL %"])
        test_trades = [simulate_trade(bars, segment, selected["TP %"], selected["SL %"]) for segment in test_rows]
        tested = summarize(test_trades, selected["TP %"], selected["SL %"], universe)
        tested = {
            "Universe": universe,
            "Chronological Cutoff": cutoff,
            "Train Trades": len(train_rows),
            "Test Trades": len(test_rows),
            "Selected TP %": selected["TP %"],
            "Selected SL %": selected["SL %"],
            "Train Mean Net PnL %": selected["Mean Net PnL %"],
            "Train Profit Factor": selected["Profit Factor"],
            **{f"Test {key}": value for key, value in tested.items() if key not in ("Universe", "TP %", "SL %", "Trades")},
        }
        oos_results.append(tested)
        oos_trades.extend(trade | {"Universe": universe, "Selected TP %": selected["TP %"], "Selected SL %": selected["SL %"]} for trade in test_trades)

    path_rows = forward_path_stats(bars, segments)
    path_summary = []
    for universe, aligned in (("ALL_ST_FLIPS", None), ("GLOBAL_ZZ_ALIGNED", True), ("GLOBAL_ZZ_NOT_ALIGNED", False)):
        rows = path_rows if aligned is None else [row for row in path_rows if row["Global ZZ Aligned at Flip"] == aligned]
        if not rows:
            continue
        path_summary.append({
            "Universe": universe,
            "Segments": len(rows),
            "Share %": len(rows) / len(path_rows) * 100,
            "Mean MFE %": fmean(row["MFE to Next Flip %"] for row in rows),
            "Median MFE %": median(row["MFE to Next Flip %"] for row in rows),
            "MFE >=0.2% %": sum(row["MFE to Next Flip %"] >= 0.2 for row in rows) / len(rows) * 100,
            "MFE >=0.3% %": sum(row["MFE to Next Flip %"] >= 0.3 for row in rows) / len(rows) * 100,
            "Mean MAE %": fmean(row["MAE to Next Flip %"] for row in rows),
            "Mean Return Next Flip Open %": fmean(row["Return at Next Flip Open %"] for row in rows),
        })

    book = Workbook()
    book.remove(book.active)
    add_sheet(book, "TP_SL_Grid", grid)
    add_sheet(book, "Chronological_OOS", oos_results)
    add_sheet(book, "OOS_Trades", oos_trades)
    add_sheet(book, "Best_Config_Trades", best_trades)
    add_sheet(book, "Forward_Path_Summary", path_summary)
    add_sheet(book, "Forward_Paths", path_rows)
    method = book.create_sheet("Methodology")
    method.append(["Parameter", "Value"])
    for row in [
        ("Sample", "Completed ST 80/4 segments on 222 selected dates, both flips in [09:00,19:00)"),
        ("Signal", "Causal continuous close-based ZZ 0.2% swing direction at the start flip"),
        ("Entry", "Open of the next one-minute bar after the ST flip"),
        ("Exit", "First TP/SL touch through the next flip bar; otherwise Open of the bar after next flip"),
        ("Same-bar TP and SL", "SL first (conservative because minute OHLC ordering is unknown)"),
        ("Commission", f"{COMMISSION_ONE_WAY_PCT:.5f}% each side; {2 * COMMISSION_ONE_WAY_PCT:.5f}% round trip"),
        ("Caveat", "Selected volatile dates are known ex post; results are conditional and not a deployable all-days test"),
    ]:
        method.append(row)
    for sheet in book.worksheets:
        style(sheet)
    paths.output.parent.mkdir(parents=True, exist_ok=True)
    book.save(paths.output)

    best_rows = []
    for universe in universes:
        available = [row for row in grid if row["Universe"] == universe]
        if available:
            best_rows.append(max(available, key=lambda row: row["Mean Net PnL %"]))
    result = {
        "parameters": {"zz_threshold_pct": ZZ_THRESHOLD_PCT, "commission_one_way_pct": COMMISSION_ONE_WAY_PCT},
        "segment_counts": {name: len(rows) for name, rows in universes.items()},
        "forward_path_summary": path_summary,
        "best_by_mean_net": best_rows,
        "chronological_oos": oos_results,
        "output": str(paths.output.resolve()),
    }
    paths.output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
