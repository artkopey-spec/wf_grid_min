from collections import defaultdict
from itertools import accumulate
from pathlib import Path
from random import Random
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import read_bars, read_rollovers
from backtest_short_after_down_impulse import write_table


SOURCE = Path("MIX_ST_19_4_volume_filter_test.xlsx")
OUTPUT = Path("MIX_ST_19_4_impulse_daily_ATR_stage_analysis.xlsx")
BASELINE_RULE = "baseline_all_adds"
TIERED_RULE = "tiered_impulse_volume_bottom60_add_bottom80_trade"
ATR_PERIOD = 14
MONEY_SCALE_10 = 0.1


def read_table(workbook, sheet):
    rows = workbook[sheet].iter_rows(values_only=True)
    headers = next(rows)
    return [{name: row[i] for i, name in enumerate(headers)} for row in rows]


def profit_factor(values):
    wins = sum(x for x in values if x > 0)
    losses = abs(sum(x for x in values if x < 0))
    return wins / losses if losses else None


def drawdown(values):
    peak = 0.0
    worst = 0.0
    for value in accumulate(values):
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return worst


def strategy_stats(rows, prefix):
    returns = [row[f"{prefix}_return_pct"] for row in rows if row[f"{prefix}_return_pct"] is not None]
    pnls = [row[f"{prefix}_pnl_rub_10"] for row in rows if row[f"{prefix}_pnl_rub_10"] is not None]
    return {
        f"{prefix}_trades": len(returns),
        f"{prefix}_win_rate_pct": sum(x > 0 for x in returns) / len(returns) * 100 if returns else None,
        f"{prefix}_average_return_pct": fmean(returns) if returns else None,
        f"{prefix}_median_return_pct": median(returns) if returns else None,
        f"{prefix}_sum_return_pct": sum(returns),
        f"{prefix}_profit_factor": profit_factor(pnls),
        f"{prefix}_max_dd_pct": drawdown(returns),
        f"{prefix}_net_pnl_rub_10": sum(pnls),
    }


def bootstrap_and_permutation(a, b, iterations=20000):
    if len(a) < 3 or len(b) < 3:
        return None, None, None
    rng = Random(42)
    differences = []
    for _ in range(iterations):
        sample_a = [a[rng.randrange(len(a))] for _ in a]
        sample_b = [b[rng.randrange(len(b))] for _ in b]
        differences.append(fmean(sample_b) - fmean(sample_a))
    differences.sort()
    low = differences[int(iterations * 0.025)]
    high = differences[int(iterations * 0.975)]

    pooled = a + b
    observed = abs(fmean(b) - fmean(a))
    extreme = 0
    for _ in range(iterations):
        shuffled = pooled[:]
        rng.shuffle(shuffled)
        difference = abs(fmean(shuffled[len(a):]) - fmean(shuffled[:len(a)]))
        extreme += difference >= observed
    return low, high, (extreme + 1) / (iterations + 1)


def main():
    workbook = load_workbook(SOURCE, read_only=True, data_only=True)
    impulses = read_table(workbook, "Impulse_Volume_Standard")
    details = read_table(workbook, "Details")
    baseline = {int(row["movement_id"]): row for row in details if row["rule"] == BASELINE_RULE}
    tiered = {int(row["movement_id"]): row for row in details if row["rule"] == TIERED_RULE}

    bars = read_bars()
    rolls = read_rollovers(bars)
    offset = 0.0
    rc = 0
    by_time = {}
    by_day = defaultdict(list)
    for bar in bars:
        while rc < len(rolls) and rolls[rc]["effective_index"] == bar["index"]:
            offset += rolls[rc]["offset_delta"]
            rc += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar
        by_day[bar["time"].date()].append(bar)

    rollover_dates = {row["effective_time"].date() for row in rolls if row["offset_delta"] != 0}
    bad_indices = {
        current["index"]
        for previous, current, following in zip(bars, bars[1:], bars[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }

    def adjusted(bar, field):
        return bar[field] + bar["offset"]

    daily = {}
    for day, day_bars in by_day.items():
        valid = [bar for bar in day_bars if bar["index"] not in bad_indices]
        daily[day] = {
            "first_index": valid[0]["index"],
            "last_index": valid[-1]["index"],
            "open": adjusted(valid[0], "open"),
            "high": max(adjusted(bar, "high") for bar in valid),
            "low": min(adjusted(bar, "low") for bar in valid),
            "close": adjusted(valid[-1], "close"),
            "open_time": valid[0]["time"],
        }

    days = sorted(daily)
    atr_for_day = {}
    tr_history = []
    atr_state = None
    previous_close = None
    for day in days:
        item = daily[day]
        atr_for_day[day] = atr_state
        tr = item["high"] - item["low"] if previous_close is None else max(
            item["high"] - item["low"],
            abs(item["high"] - previous_close),
            abs(item["low"] - previous_close),
        )
        if atr_state is None:
            tr_history.append(tr)
            if len(tr_history) == ATR_PERIOD:
                atr_state = fmean(tr_history)
        else:
            atr_state = (atr_state * (ATR_PERIOD - 1) + tr) / ATR_PERIOD
        previous_close = item["close"]

    day_position = {day: i for i, day in enumerate(days)}

    def start_bin(value):
        if value < -0.5:
            return "1_<-0.5 ATR"
        if value < 0:
            return "2_-0.5..0"
        if value < 0.5:
            return "3_0..+0.5"
        if value < 1:
            return "4_+0.5..+1"
        return "5_>+1 ATR"

    def end_bin(value):
        if value < 0:
            return "1_<0 (ниже открытия)"
        if value < 0.5:
            return "2_0..+0.5 ATR"
        if value < 1:
            return "3_+0.5..+1 ATR"
        if value < 1.5:
            return "4_+1..+1.5 ATR"
        return "5_>+1.5 ATR"

    feature_rows = []
    for impulse in impulses:
        movement_id = int(impulse["movement_id"])
        start_bar = by_time[impulse["start_time"]]
        end_bar = by_time[impulse["end_time"]]
        day = end_bar["time"].date()
        atr = atr_for_day.get(day)
        if atr is None or atr <= 0 or day_position[day] + 1 >= len(days):
            continue
        day_item = daily[day]
        day_open = day_item["open"]
        same_day_start = start_bar["index"] >= day_item["first_index"]
        pre_start = day_item["first_index"]
        pre_end = max(pre_start, start_bar["index"])
        pre_window = [
            bar for bar in bars[pre_start:pre_end + 1]
            if bar["index"] not in bad_indices
        ]
        movement_window = [
            bar for bar in bars[start_bar["index"] + 1:end_bar["index"] + 1]
            if bar["index"] not in bad_indices
        ]
        start_price = adjusted(start_bar, "close")
        end_price = adjusted(end_bar, "close")
        peak_price = max([start_price] + [adjusted(bar, "high") for bar in movement_window])
        pre_low = min(adjusted(bar, "low") for bar in pre_window)
        pre_high = max(adjusted(bar, "high") for bar in pre_window)
        next_day = days[day_position[day] + 1]
        next_open = daily[next_day]["open"]
        base = baseline.get(movement_id)
        current = tiered.get(movement_id)
        row = {
            "movement_id": movement_id,
            "impulse_start_time": impulse["start_time"],
            "impulse_end_time": impulse["end_time"],
            "same_day_start": same_day_start,
            "daily_open": day_open,
            "daily_atr_points_known_at_open": atr,
            "daily_atr_pct_of_open": atr / day_open * 100,
            "pre_low_atr": (pre_low - day_open) / atr,
            "pre_high_atr": (pre_high - day_open) / atr,
            "start_atr": (start_price - day_open) / atr,
            "end_atr": (end_price - day_open) / atr,
            "peak_atr": (peak_price - day_open) / atr,
            "impulse_pct": impulse["movement_pct"],
            "impulse_rvol": impulse["impulse_rvol"],
            "next_open_return_from_impulse_end_pct": (next_open / end_price - 1) * 100,
            "baseline_filled": base is not None,
            "baseline_return_pct": float(base["return_initial_100_pct"]) if base else None,
            "baseline_pnl_rub_10": float(base["net_pnl_rub"]) * MONEY_SCALE_10 if base else None,
            "tiered_trade": current is not None,
            "tiered_return_pct": float(current["return_initial_100_pct"]) if current else None,
            "tiered_pnl_rub_10": float(current["net_pnl_rub"]) * MONEY_SCALE_10 if current else None,
        }
        row["start_atr_bin"] = start_bin(row["start_atr"]) if same_day_start else "0_overnight"
        row["end_atr_bin"] = end_bin(row["end_atr"])
        feature_rows.append(row)

    same_day = [row for row in feature_rows if row["same_day_start"]]
    grid_groups = defaultdict(list)
    for row in same_day:
        grid_groups[(row["start_atr_bin"], row["end_atr_bin"])].append(row)
    grid_rows = []
    for (start_stage, end_stage), group in sorted(grid_groups.items()):
        row = {
            "start_stage": start_stage,
            "end_stage": end_stage,
            "signals": len(group),
            "average_daily_atr_pct": fmean(x["daily_atr_pct_of_open"] for x in group),
            "average_impulse_rvol": fmean(x["impulse_rvol"] for x in group if x["impulse_rvol"] is not None),
            "baseline_fill_rate_pct": sum(x["baseline_filled"] for x in group) / len(group) * 100,
            "tiered_trade_rate_pct": sum(x["tiered_trade"] for x in group) / len(group) * 100,
            "average_next_open_return_from_end_pct": fmean(x["next_open_return_from_impulse_end_pct"] for x in group),
        }
        row.update(strategy_stats(group, "baseline"))
        row.update(strategy_stats(group, "tiered"))
        grid_rows.append(row)

    scenarios = [
        ("A_deep_down_impulse_ends_below_open", "pre-low <= -0.75 ATR; impulse end < day open",
         lambda x: x["pre_low_atr"] <= -0.75 and x["end_atr"] < 0),
        ("B_deep_down_impulse_reclaims_open", "pre-low <= -0.75 ATR; impulse end >= day open",
         lambda x: x["pre_low_atr"] <= -0.75 and x["end_atr"] >= 0),
        ("C_start_above_0.25_end_above_1ATR", "impulse start >= +0.25 ATR; end >= +1 ATR",
         lambda x: x["start_atr"] >= 0.25 and x["end_atr"] >= 1.0),
        ("C2_start_above_open_end_above_0.5ATR", "impulse start >= day open; end >= +0.5 ATR",
         lambda x: x["start_atr"] >= 0 and x["end_atr"] >= 0.5),
        ("D_any_impulse_end_above_1ATR", "impulse end >= +1 ATR",
         lambda x: x["end_atr"] >= 1.0),
        ("E_impulse_end_0_to_1ATR", "day open <= impulse end < +1 ATR",
         lambda x: 0 <= x["end_atr"] < 1.0),
        ("F_impulse_end_below_open", "impulse end < day open",
         lambda x: x["end_atr"] < 0),
    ]
    scenario_rows = []
    scenario_members = {}
    for name, definition, predicate in scenarios:
        group = [row for row in same_day if predicate(row)]
        scenario_members[name] = group
        row = {
            "scenario": name,
            "definition": definition,
            "signals": len(group),
            "average_pre_low_atr": fmean(x["pre_low_atr"] for x in group) if group else None,
            "average_start_atr": fmean(x["start_atr"] for x in group) if group else None,
            "average_end_atr": fmean(x["end_atr"] for x in group) if group else None,
            "average_next_open_return_from_end_pct": fmean(x["next_open_return_from_impulse_end_pct"] for x in group) if group else None,
            "baseline_fill_rate_pct": sum(x["baseline_filled"] for x in group) / len(group) * 100 if group else None,
            "tiered_trade_rate_pct": sum(x["tiered_trade"] for x in group) / len(group) * 100 if group else None,
        }
        row.update(strategy_stats(group, "baseline"))
        row.update(strategy_stats(group, "tiered"))
        scenario_rows.append(row)

    comparison_rows = []
    comparisons = [
        ("C_minus_A", "A_deep_down_impulse_ends_below_open", "C_start_above_0.25_end_above_1ATR"),
        ("B_minus_A", "A_deep_down_impulse_ends_below_open", "B_deep_down_impulse_reclaims_open"),
        ("D_minus_F", "F_impulse_end_below_open", "D_any_impulse_end_above_1ATR"),
        ("E_minus_F", "F_impulse_end_below_open", "E_impulse_end_0_to_1ATR"),
        ("C2_minus_A", "A_deep_down_impulse_ends_below_open", "C2_start_above_open_end_above_0.5ATR"),
    ]
    for label, left, right in comparisons:
        for prefix in ("baseline", "tiered"):
            a = [x[f"{prefix}_return_pct"] for x in scenario_members[left] if x[f"{prefix}_return_pct"] is not None]
            b = [x[f"{prefix}_return_pct"] for x in scenario_members[right] if x[f"{prefix}_return_pct"] is not None]
            low, high, p_value = bootstrap_and_permutation(a, b)
            comparison_rows.append({
                "comparison": label,
                "strategy": prefix,
                "left_scenario": left,
                "right_scenario": right,
                "left_trades": len(a),
                "right_trades": len(b),
                "left_average_return_pct": fmean(a) if a else None,
                "right_average_return_pct": fmean(b) if b else None,
                "difference_right_minus_left_pct": fmean(b) - fmean(a) if a and b else None,
                "bootstrap_95_low_pct": low,
                "bootstrap_95_high_pct": high,
                "permutation_p_value": p_value,
            })

    output = Workbook()
    output.remove(output.active)
    write_table(output, "Scenario_Summary", scenario_rows)
    write_table(output, "ATR_Stage_Grid", grid_rows)
    write_table(output, "Statistical_Comparisons", comparison_rows)
    write_table(output, "Signal_Features", feature_rows)
    write_table(output, "Method", [
        {"item": "Signal", "definition": "first completed GREEN ST 19/4 impulse >0.7% per day"},
        {"item": "Daily ATR", "definition": "Wilder ATR(14) in points, known at current-day open; uses completed prior days only"},
        {"item": "ATR coordinate", "definition": "(adjusted price - adjusted day open) / ATR(14)"},
        {"item": "Scenario A", "definition": "pre-impulse low <= -0.75 ATR and impulse ends below day open"},
        {"item": "Scenario C", "definition": "impulse starts >= +0.25 ATR and ends >= +1 ATR"},
        {"item": "Baseline strategy", "definition": "all volume tiers allowed; 30-minute weak exit; 2% stop; next-open exit"},
        {"item": "Tiered strategy", "definition": "bottom60 volume: 10+10; 60-80: 10 only; top20: no trade"},
        {"item": "Money", "definition": "RUB PnL linearly scaled to initial 10 contracts; percent normalized to initial 10-contract notional"},
        {"item": "Cleaning", "definition": f"roll gaps adjusted; ignored {len(bad_indices)} isolated old-contract minute bars"},
        {"item": "Significance", "definition": "20,000 bootstrap samples for mean-difference CI; 20,000 two-sided permutation samples"},
    ])
    output.save(OUTPUT)

    print("signals", len(feature_rows), "same_day", len(same_day), "bad", len(bad_indices))
    print("SCENARIOS")
    for row in scenario_rows:
        print(row)
    print("COMPARISONS")
    for row in comparison_rows:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
