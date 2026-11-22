from collections import Counter
from pathlib import Path
from statistics import fmean, median

from openpyxl import load_workbook

from analyze_mix_st19 import read_bars, read_rollovers


REPORT = Path("MIX_ST_19_4_impulse_06_07_pullback_04_next_open.xlsx")


def percentile(values, p):
    values = sorted(values)
    k = (len(values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def read_trades():
    wb = load_workbook(REPORT, read_only=True, data_only=True)
    ws = wb["Trades"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    result = []
    for row in rows:
        if float(row[col["minimum_impulse_pct"]]) != 0.7:
            continue
        pnl = float(row[col["net_pnl_rub"]])
        result.append({name: row[i] for name, i in col.items()})
    return result


def main():
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

    all_trades = read_trades()
    losses = [trade for trade in all_trades if float(trade["net_pnl_rub"]) < 0]
    enriched = []
    for trade in losses:
        signal = by_time[trade["signal_time"]]
        entry_bar = by_time[trade["entry_time"]]
        exit_bar = by_time[trade["exit_time"]]
        base_offset = signal["offset"]
        entry = float(trade["entry_price_continuous"])
        exit_price = float(trade["exit_price_continuous"])
        # Start with the next minute: the entry bar's earlier high/low was not
        # necessarily tradable after a close/level-touch execution.
        window = bars[entry_bar["index"] + 1:exit_bar["index"] + 1]

        def px(bar, field):
            return bar[field] + bar["offset"] - base_offset

        low_bar = min(window, key=lambda b: px(b, "low"))
        high_bar = max(window, key=lambda b: px(b, "high"))
        low_price = min(entry, px(low_bar, "low"))
        high_price = max(entry, px(high_bar, "high"))
        previous_bar = bars[exit_bar["index"] - 1]
        previous_close = px(previous_bar, "close")
        pre_open_return = (previous_close / entry - 1) * 100
        opening_jump = (exit_price / previous_close - 1) * 100
        net_return = float(trade["price_return_pct"])
        enriched.append({
            "pnl": float(trade["net_pnl_rub"]), "return": net_return,
            "mae": (low_price / entry - 1) * 100,
            "mfe": (high_price / entry - 1) * 100,
            "hours_to_low": 0.0 if low_price == entry else (low_bar["time"] - entry_bar["time"]).total_seconds() / 3600,
            "pre_open_return": pre_open_return, "opening_jump": opening_jump,
            "positive_before_open": pre_open_return > 0,
            "fill_type": trade["fill_type"],
            "entry_hour": entry_bar["time"].hour,
        })

    print("losses", len(enriched))
    for key in ["pnl", "return", "mae", "mfe", "hours_to_low", "pre_open_return", "opening_jump"]:
        vals = [x[key] for x in enriched]
        print(key, "mean", fmean(vals), "median", median(vals), "p25", percentile(vals, .25), "p75", percentile(vals, .75), "min", min(vals), "max", max(vals))
    for name, test in {
        "positive_before_open": lambda x: x["positive_before_open"],
        "never_positive_0_1": lambda x: x["mfe"] < .1,
        "continued_down_0_3": lambda x: x["mae"] <= -.3,
        "continued_down_0_5": lambda x: x["mae"] <= -.5,
        "continued_down_1_0": lambda x: x["mae"] <= -1.0,
        "opening_jump_worse_0_1": lambda x: x["opening_jump"] <= -.1,
    }.items():
        n = sum(test(x) for x in enriched)
        print(name, n, n / len(enriched) * 100)
    print("fill_types", Counter(x["fill_type"] for x in enriched))
    print("entry_hours", Counter(x["entry_hour"] for x in enriched).most_common())
    for fill_type in sorted({trade["fill_type"] for trade in all_trades}):
        group = [trade for trade in all_trades if trade["fill_type"] == fill_type]
        group_losses = [trade for trade in group if float(trade["net_pnl_rub"]) < 0]
        print("fill_group", fill_type, "trades", len(group), "losses", len(group_losses),
              "loss_rate", len(group_losses) / len(group) * 100,
              "mean_pnl", fmean(float(trade["net_pnl_rub"]) for trade in group))


if __name__ == "__main__":
    main()
