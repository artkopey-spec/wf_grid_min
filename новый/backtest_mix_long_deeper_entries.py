from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_flips, read_rollovers
from backtest_short_after_down_impulse import write_table


MOVEMENTS = Path("MIX_ST_19_4_analysis.xlsx")
OUTPUT = Path("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx")
IMPULSE_THRESHOLD = 0.7
COMMISSION = 0.0000666667


def candidates():
    wb = load_workbook(MOVEMENTS, read_only=True, data_only=True)
    ws = wb["Movements_Sorted"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows); col = {name: i for i, name in enumerate(headers)}
    items = []
    for row in rows:
        if row[col["st_color"]] == "GREEN" and float(row[col["movement_pct"]]) > IMPULSE_THRESHOLD:
            items.append({
                "movement_id": int(row[col["movement_id"]]),
                "end_time": row[col["end_time"]],
                "movement_pct": float(row[col["movement_pct"]]),
            })
    items.sort(key=lambda x: x["end_time"])
    first = {}
    for item in items:
        first.setdefault(item["end_time"].date(), item)
    return list(first.values())


def max_drawdown(values):
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def main():
    bars = read_bars(); flips = read_flips(); rolls = read_rollovers(bars)
    signals = candidates()
    offset = 0.0; rc = 0; by_time = {}; by_day = {}
    for bar in bars:
        while rc < len(rolls) and rolls[rc]["effective_index"] == bar["index"]:
            offset += rolls[rc]["offset_delta"]; rc += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar
        by_day.setdefault(bar["time"].date(), []).append(bar)
    days = sorted(by_day); day_pos = {d: i for i, d in enumerate(days)}
    flip_pos = {f["time"]: i for i, f in enumerate(flips)}

    def px(bar, field, anchor_offset):
        return bar[field] + bar["offset"] - anchor_offset

    setups = []
    for signal in signals:
        anchor = by_time[signal["end_time"]]
        di = day_pos[anchor["time"].date()]; fi = flip_pos[anchor["time"]]
        if di + 1 >= len(days) or fi == 0 or fi + 1 >= len(flips):
            continue
        next_open = by_day[days[di + 1]][0]
        prior_flip = flips[fi - 1]; next_flip = flips[fi + 1]
        anchor_offset = anchor["offset"]
        peak_window = bars[prior_flip["bar"] + 1:anchor["index"] + 1]
        peak_bar = max(peak_window, key=lambda b: px(b, "high", anchor_offset))
        peak = px(peak_bar, "high", anchor_offset)
        stop_index = min(next_flip["bar"], next_open["index"] - 1)
        fills = {}
        for pullback in (0.4, 0.5, 0.6):
            level = peak * (1 - pullback / 100)
            fill_bar = fill_price = fill_type = None
            close = px(anchor, "close", anchor_offset)
            if close <= level:
                fill_bar, fill_price, fill_type = anchor, close, "signal_close"
            else:
                for bar in bars[anchor["index"] + 1:stop_index + 1]:
                    op = px(bar, "open", anchor_offset); lo = px(bar, "low", anchor_offset)
                    if op <= level:
                        fill_bar, fill_price, fill_type = bar, op, "bar_open_below_level"; break
                    if lo <= level:
                        fill_bar, fill_price, fill_type = bar, level, "level_touch"; break
            if fill_bar is not None:
                fills[pullback] = (fill_bar, fill_price, fill_type)
        setups.append({**signal, "anchor": anchor, "anchor_offset": anchor_offset,
                       "peak": peak, "peak_bar": peak_bar, "next_open": next_open, "fills": fills})

    variants = {
        "100_at_0.4": [(0.4, 100)],
        "100_at_0.5": [(0.5, 100)],
        "100_at_0.6": [(0.6, 100)],
        "50_at_0.4_plus_50_at_0.6": [(0.4, 50), (0.6, 50)],
        "100_at_0.4_plus_100_at_0.6": [(0.4, 100), (0.6, 100)],
        "100_at_0.4_plus_100_on_peak_breakout": [(0.4, 100)],
        "100_at_0.4_plus_100_after_close_above_peak": [(0.4, 100)],
    }
    summaries = []; details = []
    for name, legs in variants.items():
        trades = []
        for setup in setups:
            filled = [(level, qty, setup["fills"][level]) for level, qty in legs if level in setup["fills"]]
            if not filled:
                continue
            breakout_bar = breakout_price = breakout_type = None
            if name == "100_at_0.4_plus_100_on_peak_breakout":
                initial_fill_bar = filled[0][2][0]
                for bar in bars[initial_fill_bar["index"] + 1:setup["next_open"]["index"]]:
                    op = px(bar, "open", setup["anchor_offset"])
                    hi = px(bar, "high", setup["anchor_offset"])
                    if op >= setup["peak"]:
                        breakout_bar, breakout_price, breakout_type = bar, op, "bar_open_above_peak"
                        break
                    if hi >= setup["peak"]:
                        breakout_bar, breakout_price, breakout_type = bar, setup["peak"], "peak_touch"
                        break
                if breakout_bar is not None:
                    filled.append(("peak_breakout", 100, (breakout_bar, breakout_price, breakout_type)))
            if name == "100_at_0.4_plus_100_after_close_above_peak":
                initial_fill_bar = filled[0][2][0]
                # The signal exists only after the minute has closed above the
                # impulse peak. Execute at the following minute's open so the
                # backtest does not use information unavailable at entry time.
                for confirmation_bar in bars[initial_fill_bar["index"] + 1:setup["next_open"]["index"] - 1]:
                    close = px(confirmation_bar, "close", setup["anchor_offset"])
                    if close > setup["peak"]:
                        execution_bar = bars[confirmation_bar["index"] + 1]
                        breakout_bar = execution_bar
                        breakout_price = px(execution_bar, "open", setup["anchor_offset"])
                        breakout_type = "next_bar_open_after_close_above_peak"
                        break
                if breakout_bar is not None:
                    filled.append(("confirmed_peak_breakout", 100, (breakout_bar, breakout_price, breakout_type)))
            exit_price = px(setup["next_open"], "open", setup["anchor_offset"])
            qty_total = sum(qty for _, qty, _ in filled)
            gross = sum((exit_price - fill[1]) * qty for _, qty, fill in filled)
            costs = sum((fill[1] + exit_price) * COMMISSION * qty for _, qty, fill in filled)
            notional = sum(fill[1] * qty for _, qty, fill in filled)
            row = {
                "variant": name, "movement_id": setup["movement_id"],
                "signal_time": setup["end_time"], "impulse_pct": setup["movement_pct"],
                "filled_contracts": qty_total,
                "leg_04_time": setup["fills"].get(0.4, (None, None, None))[0]["time"] if any(x[0] == .4 for x in filled) else None,
                "leg_04_price": setup["fills"].get(0.4, (None, None, None))[1] if any(x[0] == .4 for x in filled) else None,
                "leg_05_time": setup["fills"].get(0.5, (None, None, None))[0]["time"] if any(x[0] == .5 for x in filled) else None,
                "leg_05_price": setup["fills"].get(0.5, (None, None, None))[1] if any(x[0] == .5 for x in filled) else None,
                "leg_06_time": setup["fills"].get(0.6, (None, None, None))[0]["time"] if any(x[0] == .6 for x in filled) else None,
                "leg_06_price": setup["fills"].get(0.6, (None, None, None))[1] if any(x[0] == .6 for x in filled) else None,
                "breakout_time": breakout_bar["time"] if breakout_bar else None,
                "breakout_price": breakout_price,
                "breakout_fill_type": breakout_type,
                "exit_time": setup["next_open"]["time"], "exit_price": exit_price,
                "gross_pnl_rub": gross, "commission_rub": costs,
                "net_pnl_rub": gross - costs,
                "net_return_pct": (gross - costs) / notional * 100,
            }
            trades.append(row); details.append(row)
        pnl = [t["net_pnl_rub"] for t in trades]
        returns = [t["net_return_pct"] for t in trades]
        wins = [x for x in pnl if x > 0]; losses = [x for x in pnl if x < 0]
        summaries.append({
            "variant": name, "signals": len(setups), "active_trades": len(trades),
            "fill_rate_pct": len(trades) / len(setups) * 100,
            "average_filled_contracts": fmean(t["filled_contracts"] for t in trades),
            "trades_with_second_leg": sum(t["filled_contracts"] > 100 for t in trades),
            "win_rate_pct": len(wins) / len(trades) * 100,
            "total_net_pnl_rub": sum(pnl), "cumulative_net_return_pct": sum(returns),
            "average_trade_rub": fmean(pnl), "average_return_pct": fmean(returns),
            "median_trade_rub": median(pnl), "median_return_pct": median(returns),
            "profit_factor": sum(wins) / abs(sum(losses)),
            "max_drawdown_rub": max_drawdown(pnl), "max_drawdown_pct": max_drawdown(returns),
            "worst_trade_rub": min(pnl), "worst_trade_pct": min(returns),
        })

    wb = Workbook(); wb.remove(wb.active)
    write_table(wb, "Summary", summaries); write_table(wb, "Trades", details)
    write_table(wb, "Method", [
        {"item": "Signal", "definition": "first GREEN ST 19/4 impulse >0.7% per day"},
        {"item": "Entry", "definition": "during RED ST at pullback from impulse peak"},
        {"item": "Exit", "definition": "next trading-day first bar open"},
        {"item": "Costs", "definition": "commission and rollover offsets included"},
    ])
    wb.save(OUTPUT)
    for row in summaries:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
