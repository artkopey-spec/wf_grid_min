import os
from datetime import time
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_flips, read_rollovers
from backtest_short_after_down_impulse import write_table


MOVEMENTS = Path("MIX_ST_19_4_analysis.xlsx")
FILTER_BEFORE_19 = os.environ.get("MIX_MIRROR_BEFORE_19") == "1"
OUTPUT = Path(
    "MIX_ST_19_4_mirror_SHORT_optimization_signals_before_19.xlsx"
    if FILTER_BEFORE_19 else "MIX_ST_19_4_mirror_SHORT_optimization_exit_19.xlsx"
)
IMPULSE_LEVELS = tuple(round(0.3 + 0.1 * i, 1) for i in range(10))
REBOUND_LEVELS = tuple(round(0.0 + 0.1 * i, 1) for i in range(11))
CONTRACTS = 100
COMMISSION = 0.0000666667


def red_movements():
    wb = load_workbook(MOVEMENTS, read_only=True, data_only=True)
    ws = wb["Movements_Sorted"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    result = []
    for row in rows:
        if row[col["st_color"]] == "RED" and float(row[col["movement_pct"]]) > min(IMPULSE_LEVELS):
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
    movements = red_movements()

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

    def px(bar, field, anchor_offset):
        return bar[field] + bar["offset"] - anchor_offset

    # Precompute every RED impulse and all possible rebound fills once.
    setups = []
    for movement in movements:
        anchor = by_time[movement["end_time"]]
        if FILTER_BEFORE_19 and anchor["time"].time() >= time(19, 0):
            continue
        fi = flip_pos[anchor["time"]]
        di = day_pos[anchor["time"].date()]
        if fi == 0 or fi + 1 >= len(flips):
            continue
        if flips[fi]["after"] != "GREEN" or flips[fi + 1]["after"] != "RED":
            raise ValueError("Unexpected ST color chain")
        start_flip = flips[fi - 1]
        next_flip = flips[fi + 1]
        exit_19 = None
        if anchor["time"].time() < time(19, 0):
            candidates_19 = [b for b in by_day[anchor["time"].date()] if b["time"].time() >= time(19, 0)]
            exit_19 = candidates_19[0] if candidates_19 else None
        if exit_19 is None and not FILTER_BEFORE_19:
            for later_day in days[di + 1:]:
                candidates_19 = [b for b in by_day[later_day] if b["time"].time() >= time(19, 0)]
                if candidates_19:
                    exit_19 = candidates_19[0]
                    break
        if exit_19 is None:
            continue
        anchor_offset = anchor["offset"]
        red_window = bars[start_flip["bar"] + 1:anchor["index"] + 1]
        trough_bar = min(red_window, key=lambda b: px(b, "low", anchor_offset))
        trough = px(trough_bar, "low", anchor_offset)
        stop_index = min(next_flip["bar"], exit_19["index"] - 1)
        fills = {}
        for rebound_pct in REBOUND_LEVELS:
            level = trough * (1 + rebound_pct / 100)
            fill_bar = fill_price = fill_type = None
            anchor_close = px(anchor, "close", anchor_offset)
            if anchor_close >= level:
                fill_bar, fill_price, fill_type = anchor, anchor_close, "signal_close"
            else:
                for bar in bars[anchor["index"] + 1:stop_index + 1]:
                    op = px(bar, "open", anchor_offset)
                    hi = px(bar, "high", anchor_offset)
                    if op >= level:
                        fill_bar, fill_price, fill_type = bar, op, "bar_open_above_level"
                        break
                    if hi >= level:
                        fill_bar, fill_price, fill_type = bar, level, "level_touch"
                        break
            if fill_bar is not None:
                fills[rebound_pct] = (fill_bar, fill_price, fill_type)
        setups.append({
            **movement, "anchor": anchor, "trough_bar": trough_bar, "trough": trough,
            "anchor_offset": anchor_offset, "exit_19": exit_19, "fills": fills,
        })

    summaries = []
    trade_cache = {}
    for impulse_threshold in IMPULSE_LEVELS:
        first_by_day = {}
        for setup in setups:
            if setup["movement_pct"] > impulse_threshold:
                first_by_day.setdefault(setup["end_time"].date(), setup)
        selected = list(first_by_day.values())
        for rebound_pct in REBOUND_LEVELS:
            trades = []
            for setup in selected:
                fill = setup["fills"].get(rebound_pct)
                if fill is None:
                    continue
                fill_bar, entry, fill_type = fill
                exit_bar = setup["exit_19"]
                exit_price = px(exit_bar, "open", setup["anchor_offset"])
                gross = (entry - exit_price) * CONTRACTS
                costs = (entry + exit_price) * COMMISSION * CONTRACTS
                trades.append({
                    "minimum_down_impulse_pct": impulse_threshold,
                    "rebound_entry_pct": rebound_pct,
                    "movement_id": setup["movement_id"],
                    "signal_time": setup["end_time"],
                    "actual_impulse_pct": setup["movement_pct"],
                    "trough_time": setup["trough_bar"]["time"],
                    "trough_price_continuous": setup["trough"],
                    "entry_time": fill_bar["time"], "entry_price_continuous": entry,
                    "fill_type": fill_type, "exit_time": exit_bar["time"],
                    "exit_price_continuous": exit_price,
                    "short_price_return_pct": (entry / exit_price - 1) * 100,
                    "gross_pnl_rub": gross, "commission_rub": costs,
                    "net_pnl_rub": gross - costs,
                    "net_return_on_entry_notional_pct": (gross - costs) / (entry * CONTRACTS) * 100,
                })
            trades.sort(key=lambda x: x["entry_time"])
            pnl = [t["net_pnl_rub"] for t in trades]
            wins = [p for p in pnl if p > 0]
            losses = [p for p in pnl if p < 0]
            returns = [t["net_return_on_entry_notional_pct"] for t in trades]
            return_losses = [r for r in returns if r < 0]
            summaries.append({
                "minimum_down_impulse_pct": impulse_threshold,
                "rebound_entry_pct": rebound_pct,
                "signals": len(selected), "filled_trades": len(trades),
                "fill_rate_pct": len(trades) / len(selected) * 100 if selected else 0,
                "win_rate_pct": len(wins) / len(trades) * 100 if trades else 0,
                "total_net_pnl_rub": sum(pnl),
                "cumulative_net_return_pct": sum(returns),
                "net_pnl_per_signal_rub": sum(pnl) / len(selected) if selected else 0,
                "average_trade_rub": fmean(pnl) if pnl else 0,
                "median_trade_rub": median(pnl) if pnl else 0,
                "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
                "max_drawdown_rub": max_drawdown(pnl),
                "max_drawdown_pct": max_drawdown(returns),
                "average_loss_rub": fmean(losses) if losses else None,
                "median_loss_rub": median(losses) if losses else None,
                "worst_trade_rub": min(pnl) if pnl else None,
                "worst_trade_pct": min(returns) if returns else None,
            })
            trade_cache[(impulse_threshold, rebound_pct)] = trades

    ranked = sorted(summaries, key=lambda x: x["total_net_pnl_rub"], reverse=True)
    for rank, row in enumerate(ranked, 1):
        row["net_profit_rank"] = rank
    best = ranked[0]
    best_key = (best["minimum_down_impulse_pct"], best["rebound_entry_pct"])

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Grid_Ranked", ranked)
    write_table(wb, "Grid_By_Parameters", summaries)
    write_table(wb, "Best_Trades", trade_cache[best_key])
    write_table(wb, "Method", [
        {"item": "Instrument", "definition": "MIX; ST 19/4; minute bars"},
        {"item": "Signal", "definition": "first completed RED impulse per day strictly above threshold" + (" and before 19:00" if FILTER_BEFORE_19 else "; all signal times retained")},
        {"item": "Entry", "definition": "SHORT 100 after GREEN confirmation at rebound above RED impulse trough"},
        {"item": "Entry window", "definition": "while ST remains GREEN and before the scheduled 19:00 exit"},
        {"item": "Exit", "definition": "same-date 19:00" if FILTER_BEFORE_19 else "nearest trading 19:00; post-19:00 signals exit at the next trading 19:00"},
        {"item": "Grid", "definition": "impulse 0.3%-1.2%; rebound 0.0%-1.0%; steps 0.1 pp"},
        {"item": "Costs", "definition": "0.00666667% per side; no slippage; rollover offsets applied"},
    ])
    wb.save(OUTPUT)
    print("setups", len(setups), "grid", len(summaries))
    print("best", best)
    for row in ranked[:10]:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
