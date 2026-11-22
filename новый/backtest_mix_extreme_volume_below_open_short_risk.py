from pathlib import Path

import numpy as np
import pandas as pd

from analyze_mix_st19 import read_bars, read_rollovers


SOURCE = Path("MIX_ST_19_4_high_volume_first_impulse_SHORT.xlsx")
OUTPUT = Path("MIX_ST_19_4_extreme_volume_below_open_SHORT_risk.xlsx")
COMMISSION = 0.0000666667
CONTRACTS = 10
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(frame):
    frame = frame.sort_values("signal_time")
    ret = frame["return_pct"].astype(float)
    pnl = frame["net_pnl_rub_10"].astype(float)
    losses = -pnl[pnl < 0].sum()
    return {
        "trades": len(frame),
        "win_rate_pct": 100 * (ret > 0).mean() if len(frame) else np.nan,
        "average_return_pct": ret.mean() if len(frame) else np.nan,
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(ret),
        "worst_trade_pct": ret.min() if len(frame) else np.nan,
        "best_trade_pct": ret.max() if len(frame) else np.nan,
        "net_pnl_rub_10": pnl.sum(),
    }


def main():
    candidates = pd.read_excel(SOURCE, sheet_name="All_First_Impulses")
    for col in ["signal_time", "entry_time"]:
        candidates[col] = pd.to_datetime(candidates[col])
    thresholds = pd.read_excel(SOURCE, sheet_name="Frozen_Thresholds").set_index("percentile")["rvol_threshold"]
    p90 = float(thresholds.loc[90])
    signals = candidates[(candidates["impulse_rvol"] > p90) &
                         (candidates["impulse_pct"] < 2.5) &
                         (~candidates["passes_above_open"])].copy()

    bars = read_bars()
    rolls = read_rollovers(bars)
    offset = 0.0
    cursor = 0
    for bar in bars:
        while cursor < len(rolls) and rolls[cursor]["effective_index"] == bar["index"]:
            offset += rolls[cursor]["offset_delta"]
            cursor += 1
        for field in ["open", "high", "low", "close"]:
            bar[f"adj_{field}"] = bar[field] + offset
    rollover_dates = {r["effective_time"].date() for r in rolls if r["offset_delta"] != 0}
    bad = {
        current["index"]
        for previous, current, following in zip(bars, bars[1:], bars[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }

    fallbacks = [("15m", 15), ("30m", 30), ("19:00", None)]
    configurations = []
    for fallback_label, minutes in fallbacks:
        configurations.append((fallback_label, minutes, "time_only", None, None))
        for level in [0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0]:
            configurations.append((fallback_label, minutes, "take_profit", level, None))
        for level in [0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0]:
            configurations.append((fallback_label, minutes, "stop_loss", level, None))
        for level in [0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0]:
            configurations.append((fallback_label, minutes, "trailing_from_entry", level, None))
        for activation in [0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0]:
            for trail in [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]:
                configurations.append((fallback_label, minutes, "profit_activated_trailing", activation, trail))
        for target in [1.0, 1.5, 2.0, 2.5, 3.0]:
            for stop in [0.4, 0.5, 0.7, 1.0]:
                configurations.append((fallback_label, minutes, "bracket_target_stop", target, stop))

    all_details = []
    summaries = []
    for fallback_label, minutes, mechanism, primary, secondary in configurations:
        rows = []
        for signal in signals.to_dict("records"):
            entry_index = int(signal["entry_index"])
            entry_price = float(signal["entry_price"])
            deadline = signal["entry_time"] + pd.Timedelta(minutes=minutes) if minutes is not None else None
            final_index = int(signal["at_19_index"]) if minutes is None and pd.notna(signal["at_19_index"]) else None
            if minutes is None and final_index is None:
                continue
            lowest_seen = entry_price
            activated = mechanism == "trailing_from_entry"
            exit_price = exit_time = exit_reason = None
            for bar in bars[entry_index:]:
                if bar["index"] in bad:
                    continue
                now = pd.Timestamp(bar["time"])
                high, low = float(bar["adj_high"]), float(bar["adj_low"])
                if mechanism == "take_profit":
                    target = entry_price * (1 - primary / 100)
                    if low <= target:
                        exit_price, exit_time, exit_reason = target, now, "take_profit"
                        break
                elif mechanism == "stop_loss":
                    stop = entry_price * (1 + primary / 100)
                    if high >= stop:
                        exit_price, exit_time, exit_reason = max(stop, float(bar["adj_open"])), now, "stop_loss"
                        break
                elif mechanism == "trailing_from_entry":
                    stop = lowest_seen * (1 + primary / 100)
                    if high >= stop:
                        exit_price, exit_time, exit_reason = max(stop, float(bar["adj_open"])), now, "trailing_stop"
                        break
                    lowest_seen = min(lowest_seen, low)
                elif mechanism == "profit_activated_trailing":
                    if activated:
                        stop = lowest_seen * (1 + secondary / 100)
                        if high >= stop:
                            exit_price, exit_time, exit_reason = max(stop, float(bar["adj_open"])), now, "profit_trailing"
                            break
                        lowest_seen = min(lowest_seen, low)
                    elif low <= entry_price * (1 - primary / 100):
                        activated = True
                        lowest_seen = low
                elif mechanism == "bracket_target_stop":
                    target = entry_price * (1 - primary / 100)
                    stop = entry_price * (1 + secondary / 100)
                    # Conservative ordering when both levels occur inside the same minute: count the stop first.
                    if high >= stop:
                        exit_price, exit_time, exit_reason = max(stop, float(bar["adj_open"])), now, "stop_loss"
                        break
                    if low <= target:
                        exit_price, exit_time, exit_reason = target, now, "take_profit"
                        break
                reached_fallback = now >= deadline if minutes is not None else bar["index"] >= final_index
                if reached_fallback:
                    exit_price, exit_time, exit_reason = float(bar["adj_close"]), now, "time_fallback"
                    break
            gross = (entry_price - exit_price) * CONTRACTS
            costs = (entry_price + exit_price) * CONTRACTS * COMMISSION
            net = gross - costs
            rows.append({
                "fallback": fallback_label,
                "mechanism": mechanism,
                "primary_pct": primary,
                "secondary_trail_pct": secondary,
                "movement_id": int(signal["movement_id"]),
                "signal_time": signal["signal_time"],
                "impulse_pct": signal["impulse_pct"],
                "impulse_rvol": signal["impulse_rvol"],
                "entry_time": signal["entry_time"],
                "entry_price": entry_price,
                "exit_time": exit_time,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "net_pnl_rub_10": net,
                "return_pct": net / (entry_price * CONTRACTS) * 100,
            })
        result = pd.DataFrame(rows)
        all_details.append(result)
        for universe, part in [("all_14", result),
                               ("entry_before19_11", result[result["entry_time"].dt.time < pd.Timestamp("19:00").time()])]:
            train = part[part["signal_time"] <= SPLIT]
            oos = part[part["signal_time"] > SPLIT]
            a, b, c = stats(part), stats(train), stats(oos)
            summaries.append({
                "universe": universe,
                "fallback": fallback_label,
                "mechanism": mechanism,
                "primary_pct": primary,
                "secondary_trail_pct": secondary,
                **{f"full_{k}": v for k, v in a.items()},
                **{f"train_{k}": v for k, v in b.items()},
                **{f"oos_{k}": v for k, v in c.items()},
                "train_oos_positive": b["sum_return_pct"] > 0 and c["sum_return_pct"] > 0,
            })
    details = pd.concat(all_details, ignore_index=True)
    summary = pd.DataFrame(summaries)
    summary["robust_pf"] = np.sqrt(summary["train_profit_factor"].clip(lower=0) * summary["oos_profit_factor"].clip(lower=0))

    yearly = details[(details["fallback"] == "15m") & (details["mechanism"] == "time_only")].copy()
    yearly["year"] = yearly["signal_time"].dt.year
    yearly = yearly.groupby("year", as_index=False).apply(lambda x: pd.Series(stats(x)), include_groups=False)

    method = pd.DataFrame([
        ["Candidate", f"first GREEN impulse of day: 0.7% <= size <2.5%, RVol > frozen train P90 {p90:.6f}, ending Close <= adjusted day Open"],
        ["Entry", "Short10 next-minute Open after confirmed RED flip"],
        ["Base exits", "15m, 30m, and 19:00; intrabar controls tested separately around each fallback"],
        ["Trailing conservatism", "current minute High is checked against a trail derived only from the lowest price known before that minute"],
        ["Costs", "commission included; no slippage; rollover gaps adjusted"],
        ["Warning", "14 trades only; every optimized target/stop/trail remains exploratory"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        details.to_excel(writer, sheet_name="All_Trades", index=False)
        signals.to_excel(writer, sheet_name="Signals_14", index=False)
        yearly.to_excel(writer, sheet_name="Yearly_15m_Base", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    cols = ["fallback", "mechanism", "primary_pct", "secondary_trail_pct", "full_trades",
            "full_average_return_pct", "full_sum_return_pct", "full_win_rate_pct", "full_profit_factor",
            "full_max_drawdown_pct", "train_sum_return_pct", "oos_sum_return_pct", "train_oos_positive"]
    print("BASE")
    cols = ["universe"] + cols
    print(summary[summary["mechanism"] == "time_only"][cols].to_string(index=False))
    print("\nBEST PER MECHANISM/FALLBACK")
    best = summary.sort_values(["universe", "fallback", "mechanism", "train_oos_positive", "full_sum_return_pct"], ascending=[True, True, True, False, False]).groupby(["universe", "fallback", "mechanism"]).head(1)
    print(best[cols].to_string(index=False))
    print("\nYEARLY 15M")
    print(yearly.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
