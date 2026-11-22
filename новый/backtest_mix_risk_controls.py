from bisect import bisect_left
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_rollovers
from backtest_short_after_down_impulse import write_table


SOURCE = Path("MIX_ST_19_4_impulse_06_07_pullback_04_next_open.xlsx")
OUTPUT = Path("MIX_ST_19_4_risk_controls.xlsx")
COMMISSION = 0.0000666667
CONTRACTS = 100


def load_trades():
    wb = load_workbook(SOURCE, read_only=True, data_only=True)
    ws = wb["Trades"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    return [
        {name: row[i] for name, i in col.items()}
        for row in rows if float(row[col["minimum_impulse_pct"]]) == 0.7
    ]


def drawdown(values):
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def stats(name, results, total_signals=326):
    pnl = [r["pnl"] for r in results]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    return {
        "rule": name, "trades": len(results), "trades_per_326_signals_pct": len(results) / total_signals * 100,
        "win_rate_pct": len(wins) / len(results) * 100,
        "net_pnl_rub": sum(pnl), "net_per_signal_rub": sum(pnl) / total_signals,
        "average_trade_rub": fmean(pnl), "median_trade_rub": median(pnl),
        "average_loss_rub": fmean(losses), "median_loss_rub": median(losses),
        "profit_factor": sum(wins) / abs(sum(losses)), "max_drawdown_rub": drawdown(pnl),
        "worst_trade_rub": min(pnl),
    }


def main():
    source_trades = load_trades()
    bars = read_bars()
    rolls = read_rollovers(bars)
    offset = 0.0
    rc = 0
    by_time = {}
    for bar in bars:
        while rc < len(rolls) and rolls[rc]["effective_index"] == bar["index"]:
            offset += rolls[rc]["offset_delta"]
            rc += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar

    def evaluate(trade, stop_pct=None, weak_exit_hours=None):
        signal = by_time[trade["signal_time"]]
        entry_bar = by_time[trade["entry_time"]]
        scheduled_exit = by_time[trade["exit_time"]]
        anchor_offset = signal["offset"]
        entry = float(trade["entry_price_continuous"])
        exit_price = float(trade["exit_price_continuous"])
        exit_time = scheduled_exit["time"]
        reason = "next_open"

        def px(bar, field):
            return bar[field] + bar["offset"] - anchor_offset

        if stop_pct is not None:
            stop = entry * (1 - stop_pct / 100)
            # Entry-bar extremes are excluded because their order relative to fill is unknown.
            for bar in bars[entry_bar["index"] + 1:scheduled_exit["index"]]:
                op = px(bar, "open")
                lo = px(bar, "low")
                if op <= stop:
                    exit_price, exit_time, reason = op, bar["time"], f"stop_{stop_pct}_gap"
                    break
                if lo <= stop:
                    exit_price, exit_time, reason = stop, bar["time"], f"stop_{stop_pct}"
                    break
            if reason == "next_open" and px(scheduled_exit, "open") <= stop:
                exit_price, exit_time, reason = px(scheduled_exit, "open"), scheduled_exit["time"], f"stop_{stop_pct}_open_gap"

        if weak_exit_hours is not None and reason == "next_open":
            target = entry_bar["time"].timestamp() + weak_exit_hours * 3600
            candidates = [b for b in bars[entry_bar["index"] + 1:scheduled_exit["index"]] if b["time"].timestamp() >= target]
            if candidates:
                checkpoint = candidates[0]
                checkpoint_price = px(checkpoint, "close")
                if checkpoint_price < entry:
                    exit_price, exit_time, reason = checkpoint_price, checkpoint["time"], f"weak_after_{weak_exit_hours}h"

        gross = (exit_price - entry) * CONTRACTS
        costs = (entry + exit_price) * COMMISSION * CONTRACTS
        return {"pnl": gross - costs, "reason": reason, "source_fill_type": trade["fill_type"]}

    variants = [("baseline", {})]
    variants += [(f"stop_{x:.1f}%", {"stop_pct": x}) for x in (0.3, 0.5, 0.7, 1.0, 1.5)]
    variants += [(f"exit_if_negative_after_{x}h", {"weak_exit_hours": x}) for x in (3, 6)]
    variants += [("stop_1.5%_and_exit_if_negative_after_6h", {"stop_pct": 1.5, "weak_exit_hours": 6})]
    summaries = []
    details = []
    for name, kwargs in variants:
        results = [evaluate(trade, **kwargs) for trade in source_trades]
        summaries.append(stats(name, results))
        for result in results:
            details.append({"rule": name, **result})

    touch_only = [trade for trade in source_trades if trade["fill_type"] == "level_touch"]
    touch_results = [evaluate(trade) for trade in touch_only]
    summaries.append(stats("only_exact_0.4_touch", touch_results))
    for result in touch_results:
        details.append({"rule": "only_exact_0.4_touch", **result})
    touch_risk_results = [evaluate(trade, stop_pct=1.5, weak_exit_hours=6) for trade in touch_only]
    summaries.append(stats("exact_touch_plus_stop_1.5%_and_6h", touch_risk_results))
    for result in touch_risk_results:
        details.append({"rule": "exact_touch_plus_stop_1.5%_and_6h", **result})

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summaries)
    write_table(wb, "Details", details)
    write_table(wb, "Method", [
        {"item": "Base strategy", "definition": "impulse >0.7%; LONG 100 at -0.4%; next open exit"},
        {"item": "Stops", "definition": "first executable minute after entry; gap through stop filled at bar open"},
        {"item": "Weak exit", "definition": "at first available bar after 3h/6h, exit only if close is below entry"},
        {"item": "Costs", "definition": "commission included; rollover offsets applied; no slippage"},
    ])
    wb.save(OUTPUT)
    for row in summaries:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
