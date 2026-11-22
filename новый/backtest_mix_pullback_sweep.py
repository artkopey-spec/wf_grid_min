from __future__ import annotations

from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook

from analyze_mix_st19 import read_bars, read_flips, read_rollovers
from backtest_mix_long_after_up_impulse import read_candidates
from backtest_short_after_down_impulse import write_table


OUTPUT = Path("MIX_ST_19_4_LONG_pullback_03_04_05_next_open.xlsx")
COMMISSION = 0.0000666667
CONTRACTS = 100
LEVELS = (0.3, 0.4, 0.5)


def drawdown(pnls):
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
    _, candidates = read_candidates()

    offset = 0.0
    roll_cursor = 0
    by_time = {}
    by_day = {}
    for bar in bars:
        while roll_cursor < len(rolls) and rolls[roll_cursor]["effective_index"] == bar["index"]:
            offset += rolls[roll_cursor]["offset_delta"]
            roll_cursor += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar
        by_day.setdefault(bar["time"].date(), []).append(bar)

    days = sorted(by_day)
    day_pos = {day: i for i, day in enumerate(days)}
    flip_pos = {flip["time"]: i for i, flip in enumerate(flips)}

    def price(bar, field, anchor_offset):
        return bar[field] + bar["offset"] - anchor_offset

    setups = []
    for candidate in candidates:
        anchor = by_time[candidate["end_time"]]
        di = day_pos[anchor["time"].date()]
        fi = flip_pos[anchor["time"]]
        if di + 1 >= len(days) or fi == 0 or fi + 1 >= len(flips):
            continue
        next_open = by_day[days[di + 1]][0]
        prior_flip = flips[fi - 1]
        next_flip = flips[fi + 1]
        anchor_offset = anchor["offset"]
        peak_window = bars[prior_flip["bar"] + 1: anchor["index"] + 1]
        peak_bar = max(peak_window, key=lambda b: price(b, "high", anchor_offset))
        peak = price(peak_bar, "high", anchor_offset)
        stop_index = min(next_flip["bar"], next_open["index"] - 1)
        setups.append((candidate, anchor, anchor_offset, peak_bar, peak, stop_index, next_open))

    all_trades = []
    summaries = []
    for decline_pct in LEVELS:
        trades = []
        for candidate, anchor, anchor_offset, peak_bar, peak, stop_index, next_open in setups:
            level = peak * (1 - decline_pct / 100)
            fill_bar = None
            fill_price = None
            fill_type = None
            anchor_close = price(anchor, "close", anchor_offset)
            if anchor_close <= level:
                fill_bar, fill_price, fill_type = anchor, anchor_close, "signal_close"
            else:
                for bar in bars[anchor["index"] + 1: stop_index + 1]:
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
                "pullback_pct": decline_pct, "movement_id": candidate["movement_id"],
                "signal_time": anchor["time"], "impulse_pct": candidate["movement_pct"],
                "peak_time": peak_bar["time"], "peak_price_continuous": peak,
                "entry_time": fill_bar["time"], "entry_price_continuous": fill_price,
                "fill_type": fill_type, "exit_time": next_open["time"],
                "exit_price_continuous": exit_price,
                "price_return_pct": (exit_price / fill_price - 1) * 100,
                "gross_pnl_rub": gross, "commission_rub": costs, "net_pnl_rub": gross - costs,
            })
        pnls = [t["net_pnl_rub"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        summaries.append({
            "pullback_pct": decline_pct, "signals": len(setups), "filled_trades": len(trades),
            "fill_rate_pct": len(trades) / len(setups) * 100,
            "win_rate_pct": len(wins) / len(trades) * 100,
            "total_net_pnl_rub": sum(pnls), "average_net_pnl_rub": fmean(pnls),
            "median_net_pnl_rub": median(pnls), "profit_factor": sum(wins) / abs(sum(losses)),
            "max_drawdown_rub": drawdown(pnls),
            "mean_price_return_pct": fmean(t["price_return_pct"] for t in trades),
            "median_price_return_pct": median(t["price_return_pct"] for t in trades),
        })
        all_trades.extend(trades)

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summaries)
    write_table(wb, "Trades", all_trades)
    write_table(wb, "Method", [
        {"item": "Signal", "definition": "first completed GREEN ST 19/4 impulse >0.6% per day"},
        {"item": "Variants", "definition": "independent LONG 100 at 0.3%, 0.4%, or 0.5% below impulse peak"},
        {"item": "Entry window", "definition": "only after RED confirmation and while ST remains RED"},
        {"item": "Exit", "definition": "next trading-day first bar open"},
        {"item": "Costs", "definition": "0.00666667% per side; no slippage; rollover offsets applied"},
    ])
    wb.save(OUTPUT)
    for row in summaries:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
