from itertools import accumulate
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_rollovers
from backtest_short_after_down_impulse import write_table


MOVEMENTS = Path("MIX_ST_19_4_analysis.xlsx")
RISK_RESULTS = Path("MIX_ST_19_4_volume_filter_test.xlsx")
OUTPUT = Path("MIX_ST_19_4_above_open_final_backtest.xlsx")
IMPULSE_THRESHOLD = 0.7
SPLIT_TIME = None
MONEY_SCALE = 0.1  # source trades use 100; working size is 10 + 10


def profit_factor(values):
    gains = sum(x for x in values if x > 0)
    losses = abs(sum(x for x in values if x < 0))
    return gains / losses if losses else None


def max_drawdown(values):
    peak = 0.0
    worst = 0.0
    for value in accumulate(values):
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return worst


def load_first_impulses():
    wb = load_workbook(MOVEMENTS, read_only=True, data_only=True)
    rows = wb["Movements_Sorted"].iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    qualifying = []
    for row in rows:
        if row[col["st_color"]] == "GREEN" and float(row[col["movement_pct"]]) > IMPULSE_THRESHOLD:
            qualifying.append({
                "movement_id": int(row[col["movement_id"]]),
                "start_time": row[col["start_time"]],
                "end_time": row[col["end_time"]],
                "impulse_pct": float(row[col["movement_pct"]]),
            })
    qualifying.sort(key=lambda x: x["end_time"])
    first = {}
    for row in qualifying:
        first.setdefault(row["end_time"].date(), row)
    return list(first.values())


def load_risk_trades():
    wb = load_workbook(RISK_RESULTS, read_only=True, data_only=True)
    rows = wb["Details"].iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    result = {}
    for row in rows:
        if row[col["rule"]] != "baseline_all_adds":
            continue
        movement_id = int(row[col["movement_id"]])
        result[movement_id] = {
            name: row[i] for name, i in col.items()
        }
    return result


def segment_stats(rows):
    pnl = [row["net_pnl_rub_10"] for row in rows]
    returns = [row["return_pct"] for row in rows]
    return {
        "trades": len(rows),
        "win_rate_pct": sum(x > 0 for x in pnl) / len(pnl) * 100 if pnl else None,
        "average_return_pct": fmean(returns) if returns else None,
        "median_return_pct": median(returns) if returns else None,
        "sum_return_pct": sum(returns),
        "profit_factor": profit_factor(pnl),
        "max_drawdown_pct": max_drawdown(returns),
        "worst_trade_pct": min(returns) if returns else None,
        "net_pnl_rub_10": sum(pnl),
        "max_drawdown_rub_10": max_drawdown(pnl),
        "worst_trade_rub_10": min(pnl) if pnl else None,
    }


def main():
    global SPLIT_TIME
    bars = read_bars()
    rolls = read_rollovers(bars)
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

    rollover_dates = {r["effective_time"].date() for r in rolls if r["offset_delta"] != 0}
    bad_indices = {
        current["index"]
        for previous, current, following in zip(bars, bars[1:], bars[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }

    daily_open = {}
    for day, day_bars in by_day.items():
        valid = [bar for bar in day_bars if bar["index"] not in bad_indices]
        daily_open[day] = valid[0]["open"] + valid[0]["offset"]

    ordered_days = sorted(by_day)
    day_position = {day: i for i, day in enumerate(ordered_days)}
    # A signal on the final available day cannot be evaluated because the
    # strategy requires the next trading open. Keep only fully observable cases.
    impulses = [
        row for row in load_first_impulses()
        if day_position[row["end_time"].date()] + 1 < len(ordered_days)
    ]
    risk_trades = load_risk_trades()
    ordered_source = sorted(risk_trades.values(), key=lambda x: x["signal_time"])
    SPLIT_TIME = ordered_source[int(len(ordered_source) * 0.70) - 1]["signal_time"]

    signal_rows = []
    all_trades = []
    filtered_trades = []
    for impulse in impulses:
        end_bar = by_time[impulse["end_time"]]
        open_price = daily_open[end_bar["time"].date()]
        end_price = end_bar["close"] + end_bar["offset"]
        above = end_price > open_price
        source = risk_trades.get(impulse["movement_id"])
        row = {
            **impulse,
            "day_open_adjusted": open_price,
            "impulse_end_close_adjusted": end_price,
            "end_vs_open_pct": (end_price / open_price - 1) * 100,
            "passes_above_open": above,
            "entry_filled": source is not None,
        }
        if source is not None:
            trade = {
                **row,
                "realized_contracts": int(source["realized_contracts"] * MONEY_SCALE),
                "second_leg": int(source["realized_contracts"]) == 200,
                "exit_time": source["exit_time"],
                "exit_reason": source["exit_reason"],
                "return_pct": float(source["return_initial_100_pct"]),
                "net_pnl_rub_10": float(source["net_pnl_rub"]) * MONEY_SCALE,
            }
            all_trades.append(trade)
            if above:
                filtered_trades.append(trade)
            row.update({k: trade[k] for k in ("realized_contracts", "second_leg", "exit_time", "exit_reason", "return_pct", "net_pnl_rub_10")})
        signal_rows.append(row)

    all_trades.sort(key=lambda x: x["end_time"])
    filtered_trades.sort(key=lambda x: x["end_time"])
    eligible = [row for row in signal_rows if row["passes_above_open"]]

    summaries = []
    for label, trades, opportunities in (
        ("Без фильтра открытия", all_trades, len(impulses)),
        ("Финал: импульс закончен выше открытия", filtered_trades, len(eligible)),
    ):
        overall = segment_stats(trades)
        train = segment_stats([x for x in trades if x["end_time"] <= SPLIT_TIME])
        test = segment_stats([x for x in trades if x["end_time"] > SPLIT_TIME])
        summaries.append({
            "variant": label,
            "signals": opportunities,
            "trades": len(trades),
            "fill_rate_pct": len(trades) / opportunities * 100,
            "second_legs": sum(x["second_leg"] for x in trades),
            **overall,
            "train_trades": train["trades"],
            "train_average_return_pct": train["average_return_pct"],
            "train_sum_return_pct": train["sum_return_pct"],
            "train_profit_factor": train["profit_factor"],
            "test_trades": test["trades"],
            "test_average_return_pct": test["average_return_pct"],
            "test_sum_return_pct": test["sum_return_pct"],
            "test_profit_factor": test["profit_factor"],
            "test_net_pnl_rub_10": test["net_pnl_rub_10"],
        })

    exit_rows = []
    for reason in ("weak_30m", "stop", "stop_gap", "next_open"):
        group = [x for x in filtered_trades if x["exit_reason"] == reason]
        if not group:
            continue
        st = segment_stats(group)
        exit_rows.append({"exit_reason": reason, **st})

    year_rows = []
    for year in sorted({x["end_time"].year for x in filtered_trades}):
        group = [x for x in filtered_trades if x["end_time"].year == year]
        year_rows.append({"year": year, **segment_stats(group)})

    equity_rows = []
    cum_pct = 0.0
    cum_rub = 0.0
    for trade in filtered_trades:
        cum_pct += trade["return_pct"]
        cum_rub += trade["net_pnl_rub_10"]
        equity_rows.append({
            "signal_time": trade["end_time"],
            "return_pct": trade["return_pct"],
            "net_pnl_rub_10": trade["net_pnl_rub_10"],
            "cumulative_return_pct": cum_pct,
            "cumulative_pnl_rub_10": cum_rub,
        })

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summaries)
    write_table(wb, "Exit_Reasons", exit_rows)
    write_table(wb, "Yearly", year_rows)
    write_table(wb, "Signals", signal_rows)
    write_table(wb, "Trades", filtered_trades)
    write_table(wb, "Equity", equity_rows)
    write_table(wb, "Method", [
        {"item": "Signal", "definition": "first GREEN ST 19/4 impulse >0.7% per trading day; final impulse close strictly above adjusted day open"},
        {"item": "First entry", "definition": "Long 10 on -0.4% correction from initiating impulse high"},
        {"item": "Add", "definition": "after a minute CLOSE above initiating impulse high; buy +10 at next minute OPEN"},
        {"item": "Stop", "definition": "2% below current weighted-average entry; recalculated after add; gaps exit at available open"},
        {"item": "30-minute check", "definition": "first available minute close at/after 30 minutes from first entry; if close <= weighted average, exit all"},
        {"item": "Time exit", "definition": "next trading-day first available bar open"},
        {"item": "Costs", "definition": "commission 0.00666667% per side included; no slippage"},
        {"item": "Cleaning", "definition": f"11 roll gaps adjusted; {len(bad_indices)} isolated wrong-contract minute bars excluded"},
        {"item": "OOS", "definition": f"chronological 70/30 split; test strictly after {SPLIT_TIME}"},
    ])
    wb.save(OUTPUT)
    print("split", SPLIT_TIME, "bad", len(bad_indices))
    print("signals", len(impulses), "eligible", len(eligible), "trades", len(filtered_trades))
    for row in summaries:
        print(row)
    print("EXITS")
    for row in exit_rows:
        print(row)
    print("YEARS")
    for row in year_rows:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
