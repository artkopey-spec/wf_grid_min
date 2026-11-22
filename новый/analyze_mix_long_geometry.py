from bisect import bisect_left
from collections import Counter
from statistics import fmean, median

from backtest_mix_long_after_up_impulse import read_candidates
from analyze_mix_st19 import read_bars, read_rollovers


def pct(values, p):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def main():
    bars = read_bars()
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
    day_pos = {day: i for i, day in enumerate(days)}
    records = []
    for c in candidates:
        anchor = by_time[c["end_time"]]
        pos = day_pos[anchor["time"].date()]
        if pos + 1 >= len(days):
            continue
        next_open = by_day[days[pos + 1]][0]
        window = bars[anchor["index"] + 1: next_open["index"] + 1]
        if not window:
            continue

        def adj(bar, key):
            return bar[key] + bar["offset"] - anchor["offset"]

        entry = anchor["close"]
        low_bar = min(window, key=lambda b: adj(b, "low"))
        high_bar = max(window, key=lambda b: adj(b, "high"))
        low = adj(low_bar, "low")
        high = adj(high_bar, "high")
        exit_price = adj(next_open, "open")
        mae = (low / entry - 1) * 100
        mfe = (high / entry - 1) * 100
        final = (exit_price / entry - 1) * 100
        rebound = (exit_price / low - 1) * 100
        records.append({
            "mae": mae,
            "mfe": mfe,
            "final": final,
            "rebound": rebound,
            "low_first": low_bar["index"] < high_bar["index"],
            "low_hours": (low_bar["time"] - anchor["time"]).total_seconds() / 3600,
            "high_hours": (high_bar["time"] - anchor["time"]).total_seconds() / 3600,
            "open_hours": (next_open["time"] - anchor["time"]).total_seconds() / 3600,
            "low_time": low_bar["time"],
            "high_time": high_bar["time"],
            "entry_time": anchor["time"],
        })

    print("n", len(records))
    for key in ["mae", "mfe", "final", "rebound", "low_hours", "high_hours", "open_hours"]:
        vals = [r[key] for r in records]
        print(key, "mean", fmean(vals), "median", median(vals), "p25", pct(vals,.25), "p75", pct(vals,.75))
    tests = {
        "next_open_above_entry": lambda r: r["final"] > 0,
        "low_first": lambda r: r["low_first"],
        "correction_then_positive_open": lambda r: r["low_first"] and r["mae"] < 0 and r["final"] > 0,
        "never_below_entry": lambda r: r["mae"] >= 0,
        "drawdown_gt_0_1": lambda r: r["mae"] <= -.1,
        "drawdown_gt_0_3": lambda r: r["mae"] <= -.3,
        "drawdown_gt_0_5": lambda r: r["mae"] <= -.5,
        "mfe_gt_0_3": lambda r: r["mfe"] >= .3,
        "mfe_gt_0_5": lambda r: r["mfe"] >= .5,
    }
    for name, fn in tests.items():
        count = sum(fn(r) for r in records)
        print(name, count, count / len(records) * 100)
    print("low_hour", Counter(r["low_time"].hour for r in records).most_common(10))
    print("high_hour", Counter(r["high_time"].hour for r in records).most_common(10))


if __name__ == "__main__":
    main()
