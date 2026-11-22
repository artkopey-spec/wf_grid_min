from itertools import accumulate
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_rollovers
from backtest_short_after_down_impulse import write_table


SOURCE = Path("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx")
OUTPUT = Path("MIX_ST_19_4_confirmed_add_risk_sweep.xlsx")
VARIANT = "100_at_0.4_plus_100_after_close_above_peak"
COMMISSION = 0.0000666667
STOP_GRID = (0.25, 0.3, 0.4, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
TIME_GRID = (0.5, 1, 1.5, 2, 3, 4, 6)


def load_trades():
    wb = load_workbook(SOURCE, read_only=True, data_only=True)
    ws = wb["Trades"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    return [
        {name: row[i] for name, i in col.items()}
        for row in rows if row[col["variant"]] == VARIANT
    ]


def max_drawdown(values):
    curve = list(accumulate(values))
    peak = 0.0
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return worst


def summarize(rule, results, stop_pct, weak_exit_hours):
    pnl = [x["net_pnl_rub"] for x in results]
    returns = [x["return_on_initial_100_pct"] for x in results]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    reasons = {}
    for row in results:
        reasons[row["exit_reason"]] = reasons.get(row["exit_reason"], 0) + 1
    stopped = sum(v for k, v in reasons.items() if k.startswith("stop"))
    timed = sum(v for k, v in reasons.items() if k.startswith("weak"))
    return {
        "rule": rule,
        "stop_pct_from_weighted_average": stop_pct,
        "weak_exit_hours_from_first_entry": weak_exit_hours,
        "trades": len(results),
        "second_leg_realized": sum(x["realized_contracts"] == 200 for x in results),
        "average_contracts": fmean(x["realized_contracts"] for x in results),
        "stop_exits": stopped,
        "weak_time_exits": timed,
        "next_open_exits": reasons.get("next_open", 0),
        "win_rate_pct": len(wins) / len(results) * 100,
        "net_pnl_rub": sum(pnl),
        "cumulative_return_initial_100_pct": sum(returns),
        "average_trade_rub": fmean(pnl),
        "average_return_initial_100_pct": fmean(returns),
        "median_trade_rub": median(pnl),
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "max_drawdown_rub": max_drawdown(pnl),
        "max_drawdown_initial_100_pct": max_drawdown(returns),
        "worst_trade_rub": min(pnl),
        "worst_trade_initial_100_pct": min(returns),
    }


def main():
    trades = load_trades()
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

    # On an adjusted rollover date the continuous MIX feed contains two
    # isolated one-minute prints from the old contract (2024-12-18 23:19 and
    # 23:49). They jump more than 2% away and immediately return while both
    # neighboring closes agree. Such bars are not executable in the selected
    # post-roll contract and must not trigger a synthetic stop.
    rollover_dates = {r["effective_time"].date() for r in rolls if r["offset_delta"] != 0}
    bad_roll_bar_indices = {
        current["index"]
        for previous, current, following in zip(bars, bars[1:], bars[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }

    def evaluate(trade, stop_pct=None, weak_exit_hours=None):
        signal_bar = by_time[trade["signal_time"]]
        first_bar = by_time[trade["leg_04_time"]]
        scheduled_exit = by_time[trade["exit_time"]]
        add_bar = by_time.get(trade["breakout_time"]) if trade["breakout_time"] else None
        anchor_offset = signal_bar["offset"]

        def px(bar, field):
            return bar[field] + bar["offset"] - anchor_offset

        fills = [(float(trade["leg_04_price"]), 100)]
        qty = 100
        weighted_average = fills[0][0]
        exit_price = px(scheduled_exit, "open")
        exit_time = scheduled_exit["time"]
        exit_reason = "next_open"
        checkpoint_done = False
        target_ts = first_bar["time"].timestamp() + weak_exit_hours * 3600 if weak_exit_hours else None

        # The first fill occurs inside its minute, so its remaining intrabar path
        # is unknowable. Risk checks begin on the following minute.
        for bar in bars[first_bar["index"] + 1:scheduled_exit["index"]]:
            if bar["index"] in bad_roll_bar_indices:
                continue
            op = px(bar, "open")

            # Existing position's stop is active before a possible market add
            # scheduled for this same open.
            if stop_pct is not None:
                stop = weighted_average * (1 - stop_pct / 100)
                if op <= stop:
                    exit_price, exit_time = op, bar["time"]
                    exit_reason = f"stop_{stop_pct:g}_gap"
                    break

            if add_bar is not None and bar["index"] == add_bar["index"]:
                add_price = float(trade["breakout_price"])
                fills.append((add_price, 100))
                qty = 200
                weighted_average = sum(price * amount for price, amount in fills) / qty

            if stop_pct is not None:
                stop = weighted_average * (1 - stop_pct / 100)
                lo = px(bar, "low")
                if lo <= stop:
                    exit_price, exit_time = stop, bar["time"]
                    exit_reason = f"stop_{stop_pct:g}"
                    break

            if weak_exit_hours is not None and not checkpoint_done and bar["time"].timestamp() >= target_ts:
                checkpoint_done = True
                close = px(bar, "close")
                if close <= weighted_average:
                    exit_price, exit_time = close, bar["time"]
                    exit_reason = f"weak_after_{weak_exit_hours:g}h"
                    break

        gross = sum((exit_price - price) * amount for price, amount in fills)
        entry_cost = sum(price * amount * COMMISSION for price, amount in fills)
        exit_cost = exit_price * qty * COMMISSION
        net = gross - entry_cost - exit_cost
        initial_notional = fills[0][0] * 100
        return {
            "movement_id": trade["movement_id"],
            "signal_time": trade["signal_time"],
            "first_entry_time": first_bar["time"],
            "first_entry_price": fills[0][0],
            "planned_add_time": add_bar["time"] if add_bar else None,
            "realized_add_price": fills[1][0] if len(fills) == 2 else None,
            "realized_contracts": qty,
            "weighted_average_entry": weighted_average,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "net_pnl_rub": net,
            "return_on_initial_100_pct": net / initial_notional * 100,
        }

    variants = [("baseline", None, None)]
    variants += [(f"stop_{stop:g}", stop, None) for stop in STOP_GRID]
    variants += [(f"weak_{hours:g}h", None, hours) for hours in TIME_GRID]
    variants += [
        (f"stop_{stop:g}_weak_{hours:g}h", stop, hours)
        for stop in STOP_GRID for hours in TIME_GRID
    ]

    summaries = []
    details = []
    for rule, stop_pct, weak_exit_hours in variants:
        results = [evaluate(t, stop_pct, weak_exit_hours) for t in trades]
        summaries.append(summarize(rule, results, stop_pct, weak_exit_hours))
        details.extend({"rule": rule, **row} for row in results)

    ranked_net = sorted(summaries, key=lambda x: x["net_pnl_rub"], reverse=True)
    ranked_pf = sorted(summaries, key=lambda x: x["profit_factor"] or 0, reverse=True)
    ranked_risk = sorted(summaries, key=lambda x: (x["max_drawdown_rub"], x["net_pnl_rub"]), reverse=True)

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summaries)
    write_table(wb, "Ranked_Net", ranked_net)
    write_table(wb, "Ranked_PF", ranked_pf)
    write_table(wb, "Ranked_Low_DD", ranked_risk)
    write_table(wb, "Details", details)
    write_table(wb, "Method", [
        {"item": "Strategy", "definition": "Long100 at -0.4%; add100 at next-minute open after minute Close > impulse peak; exit next open"},
        {"item": "Stop", "definition": "fixed percent below current weighted-average entry; recalculated after add; gap filled at minute open"},
        {"item": "Time exit", "definition": "at first minute close >= N elapsed hours after first entry, exit only if Close <= current weighted-average entry"},
        {"item": "Entry bar", "definition": "risk checks start on following minute because path after intrabar first fill is unknown"},
        {"item": "Costs", "definition": "commission 0.00666667% each side; rollover gaps adjusted; no slippage"},
        {"item": "Rollover contamination", "definition": f"ignored {len(bad_roll_bar_indices)} isolated wrong-contract minute bars on adjusted rollover dates"},
        {"item": "Percent", "definition": "all return percentages normalized to notional of initial 100 contracts"},
    ])
    wb.save(OUTPUT)
    print("TOP NET")
    for row in ranked_net[:10]:
        print(row)
    print("TOP PF")
    for row in ranked_pf[:5]:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
