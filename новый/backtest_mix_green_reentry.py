from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_flips, read_rollovers
from backtest_short_after_down_impulse import write_table


MOVEMENTS = Path("MIX_ST_19_4_analysis.xlsx")
OUTPUT = Path("MIX_ST_19_4_LONG_on_GREEN_reentry.xlsx")
IMPULSE_THRESHOLD = 0.7
CONTRACTS = 100
COMMISSION = 0.0000666667


def candidates():
    wb = load_workbook(MOVEMENTS, read_only=True, data_only=True)
    ws = wb["Movements_Sorted"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    items = []
    for row in rows:
        if row[col["st_color"]] == "GREEN" and float(row[col["movement_pct"]]) > IMPULSE_THRESHOLD:
            items.append({
                "movement_id": int(row[col["movement_id"]]),
                "start_time": row[col["start_time"]], "end_time": row[col["end_time"]],
                "movement_pct": float(row[col["movement_pct"]]),
            })
    items.sort(key=lambda x: x["end_time"])
    first = {}
    for item in items:
        first.setdefault(item["end_time"].date(), item)
    return list(first.values())


def max_drawdown(pnls):
    equity = peak = worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def main():
    bars = read_bars()
    flips = read_flips()
    rolls = read_rollovers(bars)
    signals = candidates()

    offset = 0.0
    rc = 0
    by_time = {}
    by_day = {}
    for bar in bars:
        while rc < len(rolls) and rolls[rc]["effective_index"] == bar["index"]:
            offset += rolls[rc]["offset_delta"]
            rc += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar
        by_day.setdefault(bar["time"].date(), []).append(bar)
    days = sorted(by_day)
    day_pos = {day: i for i, day in enumerate(days)}
    flip_pos = {flip["time"]: i for i, flip in enumerate(flips)}

    trades = []
    skipped_no_exit = 0
    for signal in signals:
        red_start = by_time[signal["end_time"]]
        fi = flip_pos[red_start["time"]]
        if fi + 1 >= len(flips):
            continue
        green_flip = flips[fi + 1]
        if green_flip["after"] != "GREEN":
            raise ValueError("Expected RED-to-GREEN flip")
        entry_bar = by_time[green_flip["time"]]
        di = day_pos[entry_bar["time"].date()]
        if di + 1 >= len(days):
            skipped_no_exit += 1
            continue
        exit_bar = by_day[days[di + 1]][0]

        # Express the whole trade in the entry contract's coordinate system.
        entry = entry_bar["close"]
        exit_price = exit_bar["open"] + exit_bar["offset"] - entry_bar["offset"]
        gross = (exit_price - entry) * CONTRACTS
        costs = (entry + exit_price) * COMMISSION * CONTRACTS

        start_flip = flips[fi - 1]
        peak_window = bars[start_flip["bar"] + 1:red_start["index"] + 1]
        peak_global = max(bar["high"] + bar["offset"] for bar in peak_window)
        impulse_start_bar = bars[start_flip["bar"]]
        impulse_start_global = impulse_start_bar["close"] + impulse_start_bar["offset"]
        entry_global = entry_bar["close"] + entry_bar["offset"]
        correction_at_entry = (entry_global / peak_global - 1) * 100
        red_window = bars[red_start["index"] + 1:entry_bar["index"] + 1]
        red_low_global = min(bar["low"] + bar["offset"] for bar in red_window)
        max_correction = (red_low_global / peak_global - 1) * 100
        correction_ratio = (peak_global - red_low_global) / (peak_global - impulse_start_global) * 100

        trades.append({
            "movement_id": signal["movement_id"], "impulse_end_time": red_start["time"],
            "impulse_pct": signal["movement_pct"], "entry_time_green_flip": entry_bar["time"],
            "red_correction_hours": (entry_bar["time"] - red_start["time"]).total_seconds() / 3600,
            "maximum_correction_from_peak_pct": max_correction,
            "correction_to_impulse_ratio_pct": correction_ratio,
            "correction_remaining_at_entry_pct": correction_at_entry,
            "entry_price": entry, "exit_time": exit_bar["time"],
            "exit_price_gap_neutral": exit_price,
            "price_return_pct": (exit_price / entry - 1) * 100,
            "gross_pnl_rub": gross, "commission_rub": costs, "net_pnl_rub": gross - costs,
        })

    pnl = [t["net_pnl_rub"] for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    summary = [{
        "signals": len(signals), "trades": len(trades), "skipped_no_next_open": skipped_no_exit,
        "win_rate_pct": len(wins) / len(trades) * 100,
        "total_net_pnl_rub": sum(pnl), "average_net_pnl_rub": fmean(pnl),
        "median_net_pnl_rub": median(pnl), "profit_factor": sum(wins) / abs(sum(losses)),
        "max_drawdown_rub": max_drawdown(pnl), "worst_trade_rub": min(pnl),
        "average_loss_rub": fmean(losses), "median_loss_rub": median(losses),
        "mean_price_return_pct": fmean(t["price_return_pct"] for t in trades),
        "median_price_return_pct": median(t["price_return_pct"] for t in trades),
        "median_red_correction_hours": median(t["red_correction_hours"] for t in trades),
        "median_maximum_correction_pct": median(t["maximum_correction_from_peak_pct"] for t in trades),
        "median_correction_remaining_at_entry_pct": median(t["correction_remaining_at_entry_pct"] for t in trades),
    }]

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summary)
    write_table(wb, "Trades", trades)
    write_table(wb, "Method", [
        {"item": "Signal", "definition": "first completed GREEN ST 19/4 impulse strictly >0.7% per day"},
        {"item": "Entry", "definition": "LONG 100 at close of the next RED-to-GREEN ST flip"},
        {"item": "Exit", "definition": "first bar open of the next trading date after actual entry"},
        {"item": "Costs", "definition": "0.00666667% per side; no slippage; rollover offsets applied"},
    ])
    wb.save(OUTPUT)
    print(summary[0])
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
