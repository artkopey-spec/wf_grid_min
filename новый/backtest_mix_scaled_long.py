from __future__ import annotations

from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook

from analyze_mix_st19 import read_bars, read_flips, read_rollovers
from backtest_mix_long_after_up_impulse import read_candidates
from backtest_short_after_down_impulse import write_table


OUTPUT = Path("MIX_ST_19_4_scaled_LONG_03_06_next_open.xlsx")
COMMISSION = 0.0000666667


def max_drawdown(values):
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def main():
    bars = read_bars()
    flips = read_flips()
    rolls = read_rollovers(bars)
    _, candidates = read_candidates()

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
    day_index = {day: i for i, day in enumerate(days)}
    flip_by_time = {flip["time"]: i for i, flip in enumerate(flips)}

    def continuous(bar, field, anchor_offset):
        return bar[field] + bar["offset"] - anchor_offset

    def fill_level(start_bar, stop_index, level, anchor_offset):
        # ST turns RED only at start_bar close, so no retroactive intrabar fill.
        start_close = continuous(start_bar, "close", anchor_offset)
        if start_close <= level:
            return start_bar, start_close, "signal_close"
        for bar in bars[start_bar["index"] + 1: stop_index + 1]:
            op = continuous(bar, "open", anchor_offset)
            lo = continuous(bar, "low", anchor_offset)
            if op <= level:
                return bar, op, "bar_open_below_level"
            if lo <= level:
                return bar, level, "level_touch"
        return None, None, None

    trades = []
    for candidate in candidates:
        anchor = by_time[candidate["end_time"]]
        current_day_pos = day_index[anchor["time"].date()]
        if current_day_pos + 1 >= len(days):
            continue
        next_open = by_day[days[current_day_pos + 1]][0]

        end_flip_pos = flip_by_time[anchor["time"]]
        if end_flip_pos == 0 or end_flip_pos + 1 >= len(flips):
            continue
        start_flip = flips[end_flip_pos - 1]
        next_flip = flips[end_flip_pos + 1]
        peak_window = bars[start_flip["bar"] + 1: anchor["index"] + 1]
        anchor_offset = anchor["offset"]
        peak_bar = max(peak_window, key=lambda b: continuous(b, "high", anchor_offset))
        peak = continuous(peak_bar, "high", anchor_offset)

        # Entries are permitted only while the correction remains RED and before exit.
        stop_index = min(next_flip["bar"], next_open["index"] - 1)
        fills = []
        for leg, decline, qty in [(1, .003, 50), (2, .006, 50)]:
            level = peak * (1 - decline)
            fill_bar, fill_price, fill_type = fill_level(anchor, stop_index, level, anchor_offset)
            if fill_bar is not None:
                fills.append((leg, qty, level, fill_bar, fill_price, fill_type))

        if not fills:
            trades.append({
                "movement_id": candidate["movement_id"], "signal_time": anchor["time"],
                "impulse_pct": candidate["movement_pct"], "peak_time": peak_bar["time"],
                "peak_price_continuous": peak,
                "leg1_time": None, "leg1_price": None, "leg1_fill_type": None,
                "leg2_time": None, "leg2_price": None, "leg2_fill_type": None,
                "filled_contracts": 0, "weighted_entry": None,
                "exit_time": next_open["time"],
                "exit_price_continuous": continuous(next_open, "open", anchor_offset),
                "return_on_weighted_entry_pct": None,
                "leg1_net_pnl_rub": 0.0, "leg2_net_pnl_rub": 0.0,
                "gross_pnl_rub": 0.0, "commission_rub": 0.0, "net_pnl_rub": 0.0,
            })
            continue

        exit_price = continuous(next_open, "open", anchor_offset)
        gross = sum((exit_price - fill_price) * qty for _, qty, _, _, fill_price, _ in fills)
        costs = sum((fill_price + exit_price) * COMMISSION * qty for _, qty, _, _, fill_price, _ in fills)
        weighted_entry = sum(fill_price * qty for _, qty, _, _, fill_price, _ in fills) / sum(x[1] for x in fills)
        leg_net = {}
        for leg, qty, _, _, fill_price, _ in fills:
            leg_net[leg] = (exit_price - fill_price) * qty - (fill_price + exit_price) * COMMISSION * qty
        row = {
            "movement_id": candidate["movement_id"], "signal_time": anchor["time"],
            "impulse_pct": candidate["movement_pct"], "peak_time": peak_bar["time"],
            "peak_price_continuous": peak,
            "leg1_time": fills[0][3]["time"] if fills and fills[0][0] == 1 else None,
            "leg1_price": fills[0][4] if fills and fills[0][0] == 1 else None,
            "leg1_fill_type": fills[0][5] if fills and fills[0][0] == 1 else None,
            "leg2_time": next((x[3]["time"] for x in fills if x[0] == 2), None),
            "leg2_price": next((x[4] for x in fills if x[0] == 2), None),
            "leg2_fill_type": next((x[5] for x in fills if x[0] == 2), None),
            "filled_contracts": sum(x[1] for x in fills), "weighted_entry": weighted_entry,
            "exit_time": next_open["time"], "exit_price_continuous": exit_price,
            "return_on_weighted_entry_pct": (exit_price / weighted_entry - 1) * 100,
            "leg1_net_pnl_rub": leg_net.get(1, 0.0), "leg2_net_pnl_rub": leg_net.get(2, 0.0),
            "gross_pnl_rub": gross, "commission_rub": costs, "net_pnl_rub": gross - costs,
        }
        trades.append(row)

    active = [t for t in trades if t["filled_contracts"]]
    pnl = [t["net_pnl_rub"] for t in active]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    fills50 = sum(t["filled_contracts"] == 50 for t in active)
    fills100 = sum(t["filled_contracts"] == 100 for t in active)
    summary = [{
        "signals": len(trades), "trades_with_any_fill": len(active),
        "no_fill": len(trades) - len(active), "only_50_contracts": fills50,
        "full_100_contracts": fills100,
        "win_rate_pct": len(wins) / len(active) * 100,
        "total_net_pnl_rub": sum(pnl), "average_net_pnl_per_active_trade_rub": fmean(pnl),
        "median_net_pnl_rub": median(pnl),
        "profit_factor": sum(wins) / abs(sum(losses)), "max_drawdown_rub": max_drawdown(pnl),
        "average_filled_contracts": fmean(t["filled_contracts"] for t in active),
        "mean_return_from_weighted_entry_pct": fmean(t["return_on_weighted_entry_pct"] for t in active),
        "median_return_from_weighted_entry_pct": median(t["return_on_weighted_entry_pct"] for t in active),
        "total_leg1_net_pnl_rub": sum(t["leg1_net_pnl_rub"] for t in active),
        "total_leg2_net_pnl_rub": sum(t["leg2_net_pnl_rub"] for t in active),
    }]

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summary)
    write_table(wb, "Trades", trades)
    write_table(wb, "Method", [
        {"item": "Signal", "definition": "first completed GREEN ST 19/4 impulse >0.6% per day"},
        {"item": "Leg 1", "definition": "LONG 50 while ST is RED at 0.3% below impulse peak"},
        {"item": "Leg 2", "definition": "LONG 50 while ST is RED at 0.6% below impulse peak"},
        {"item": "Execution", "definition": "after RED confirmation: signal close if through level; otherwise level touch/open gap"},
        {"item": "Exit", "definition": "all filled contracts at next trading-day first bar open"},
        {"item": "Costs", "definition": "0.00666667% per side; no slippage; rollover offsets applied"},
    ])
    wb.save(OUTPUT)
    print(summary[0])
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
