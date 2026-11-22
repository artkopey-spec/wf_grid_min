from __future__ import annotations

from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_flips, read_rollovers
from backtest_short_after_down_impulse import write_table


MOVEMENTS = Path("MIX_ST_19_4_analysis.xlsx")
OUTPUT = Path("MIX_ST_19_4_impulse_06_07_pullback_04_next_open.xlsx")
IMPULSE_THRESHOLDS = (0.6, 0.7)
PULLBACK_PCT = 0.4
CONTRACTS = 100
COMMISSION = 0.0000666667


def green_movements():
    wb = load_workbook(MOVEMENTS, read_only=True, data_only=True)
    ws = wb["Movements_Sorted"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    result = []
    for row in rows:
        if row[col["st_color"]] == "GREEN":
            result.append({
                "movement_id": int(row[col["movement_id"]]),
                "end_time": row[col["end_time"]],
                "movement_pct": float(row[col["movement_pct"]]),
            })
    return sorted(result, key=lambda x: x["end_time"])


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
    movements = green_movements()

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

    def price(bar, field, anchor_offset):
        return bar[field] + bar["offset"] - anchor_offset

    summaries = []
    all_trades = []
    for impulse_threshold in IMPULSE_THRESHOLDS:
        first_by_day = {}
        for item in movements:
            if item["movement_pct"] > impulse_threshold:
                first_by_day.setdefault(item["end_time"].date(), item)
        signals = list(first_by_day.values())
        eligible_signals = 0
        trades = []
        for signal in signals:
            anchor = by_time[signal["end_time"]]
            di = day_pos[anchor["time"].date()]
            fi = flip_pos[anchor["time"]]
            if di + 1 >= len(days) or fi == 0 or fi + 1 >= len(flips):
                continue
            eligible_signals += 1
            next_open = by_day[days[di + 1]][0]
            prior_flip = flips[fi - 1]
            next_flip = flips[fi + 1]
            anchor_offset = anchor["offset"]
            peak_window = bars[prior_flip["bar"] + 1:anchor["index"] + 1]
            peak_bar = max(peak_window, key=lambda b: price(b, "high", anchor_offset))
            peak = price(peak_bar, "high", anchor_offset)
            level = peak * (1 - PULLBACK_PCT / 100)
            stop_index = min(next_flip["bar"], next_open["index"] - 1)

            fill_bar = fill_price = fill_type = None
            anchor_close = price(anchor, "close", anchor_offset)
            if anchor_close <= level:
                fill_bar, fill_price, fill_type = anchor, anchor_close, "signal_close"
            else:
                for bar in bars[anchor["index"] + 1:stop_index + 1]:
                    op = price(bar, "open", anchor_offset)
                    lo = price(bar, "low", anchor_offset)
                    if op <= level:
                        fill_bar, fill_price, fill_type = bar, op, "bar_open_below_level"
                        break
                    if lo <= level:
                        fill_bar, fill_price, fill_type = bar, level, "level_touch"
                        break
            if fill_bar is None:
                continue
            exit_price = price(next_open, "open", anchor_offset)
            gross = (exit_price - fill_price) * CONTRACTS
            costs = (fill_price + exit_price) * COMMISSION * CONTRACTS
            trades.append({
                "minimum_impulse_pct": impulse_threshold,
                "movement_id": signal["movement_id"], "signal_time": anchor["time"],
                "actual_impulse_pct": signal["movement_pct"], "peak_time": peak_bar["time"],
                "entry_time": fill_bar["time"], "entry_price_continuous": fill_price,
                "fill_type": fill_type, "exit_time": next_open["time"],
                "exit_price_continuous": exit_price,
                "price_return_pct": (exit_price / fill_price - 1) * 100,
                "gross_pnl_rub": gross, "commission_rub": costs,
                "net_pnl_rub": gross - costs,
            })
        pnls = [t["net_pnl_rub"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        summaries.append({
            "minimum_impulse_pct": impulse_threshold,
            "signals_with_next_open": eligible_signals, "filled_trades": len(trades),
            "fill_rate_pct": len(trades) / eligible_signals * 100,
            "win_rate_pct": len(wins) / len(trades) * 100,
            "total_net_pnl_rub": sum(pnls), "net_pnl_per_signal_rub": sum(pnls) / eligible_signals,
            "average_net_pnl_per_trade_rub": fmean(pnls), "median_net_pnl_rub": median(pnls),
            "profit_factor": sum(wins) / abs(sum(losses)), "max_drawdown_rub": max_drawdown(pnls),
            "mean_price_return_pct": fmean(t["price_return_pct"] for t in trades),
            "median_price_return_pct": median(t["price_return_pct"] for t in trades),
        })
        all_trades.extend(trades)

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summaries)
    write_table(wb, "Trades", all_trades)
    write_table(wb, "Method", [
        {"item": "Variants", "definition": "first GREEN ST 19/4 impulse per day strictly >0.6% or >0.7%"},
        {"item": "Entry", "definition": "LONG 100 at 0.4% below impulse peak, after RED confirmation and while ST remains RED"},
        {"item": "Exit", "definition": "next trading-day first bar open"},
        {"item": "Costs", "definition": "0.00666667% per side; no slippage; rollover offsets applied"},
    ])
    wb.save(OUTPUT)
    for row in summaries:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
