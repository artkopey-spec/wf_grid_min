from pathlib import Path

import numpy as np
import pandas as pd

from analyze_mix_st19 import read_bars, read_rollovers


SIGNALS_FILE = Path("MIX_ST_19_4_climax_SHORT_exit_sweep.xlsx")
OUTPUT = Path("MIX_ST_19_4_climax_SHORT_trailing_takeprofit_stoploss.xlsx")
COMMISSION = 0.0000666667
CONTRACTS = 10
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(frame):
    frame = frame.sort_values("signal_time")
    pnl, ret = frame["net_pnl_rub_10"].astype(float), frame["return_pct"].astype(float)
    losses = -pnl[pnl < 0].sum()
    return {
        "trades": len(frame),
        "win_rate_pct": 100 * (pnl > 0).mean() if len(frame) else np.nan,
        "average_return_pct": ret.mean() if len(frame) else np.nan,
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(ret) if len(frame) else np.nan,
        "worst_trade_pct": ret.min() if len(frame) else np.nan,
        "best_trade_pct": ret.max() if len(frame) else np.nan,
    }


def main():
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
    signals = pd.read_excel(SIGNALS_FILE, sheet_name="Signals")
    signals["signal_time"] = pd.to_datetime(signals["signal_time"])
    signals["entry_time"] = pd.to_datetime(signals["entry_time"])

    configurations = []
    for fallback in [60, 90]:
        configurations.append(("time_only", fallback, None, None))
        for level in [0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.2, 1.5, 1.75, 2.0, 2.5, 3.0]:
            configurations.append(("take_profit", fallback, level, None))
        for level in [0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0]:
            configurations.append(("stop_loss", fallback, level, None))
        for trail in [0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0]:
            configurations.append(("trailing_from_entry", fallback, trail, None))
        for activation in [0.2, 0.3, 0.5, 0.7, 1.0, 1.2, 1.5]:
            for trail in [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]:
                configurations.append(("profit_activated_trailing", fallback, activation, trail))

    detail_rows = []
    summary_rows = []
    for mechanism, fallback, primary, secondary in configurations:
        rows = []
        for _, signal in signals.iterrows():
            entry_index = int(signal["entry_index"])
            entry_price = float(signal["entry_price"])
            deadline = signal["entry_time"] + pd.Timedelta(minutes=fallback)
            lowest_seen = entry_price
            activated = mechanism == "trailing_from_entry"
            exit_price = exit_time = exit_reason = None
            for bar in bars[entry_index:]:
                if bar["index"] in bad:
                    continue
                high, low = float(bar["adj_high"]), float(bar["adj_low"])
                if mechanism == "take_profit":
                    target = entry_price * (1 - primary / 100)
                    if low <= target:
                        exit_price, exit_time, exit_reason = target, pd.Timestamp(bar["time"]), "take_profit"
                        break
                elif mechanism == "stop_loss":
                    stop = entry_price * (1 + primary / 100)
                    if high >= stop:
                        exit_price, exit_time, exit_reason = stop, pd.Timestamp(bar["time"]), "stop_loss"
                        break
                elif mechanism == "trailing_from_entry":
                    # Use only the lowest price known before this minute; prevents optimistic same-bar low-then-high assumptions.
                    trailing_stop = lowest_seen * (1 + primary / 100)
                    if high >= trailing_stop:
                        exit_price, exit_time, exit_reason = max(trailing_stop, float(bar["adj_open"])), pd.Timestamp(bar["time"]), "trailing_stop"
                        break
                    lowest_seen = min(lowest_seen, low)
                elif mechanism == "profit_activated_trailing":
                    if activated:
                        trailing_stop = lowest_seen * (1 + secondary / 100)
                        if high >= trailing_stop:
                            exit_price, exit_time, exit_reason = max(trailing_stop, float(bar["adj_open"])), pd.Timestamp(bar["time"]), "profit_trailing"
                            break
                        lowest_seen = min(lowest_seen, low)
                    elif low <= entry_price * (1 - primary / 100):
                        activated = True
                        lowest_seen = low
                if pd.Timestamp(bar["time"]) >= deadline:
                    exit_price, exit_time, exit_reason = float(bar["adj_close"]), pd.Timestamp(bar["time"]), "time_fallback"
                    break
            gross = (entry_price - exit_price) * CONTRACTS
            costs = (entry_price + exit_price) * CONTRACTS * COMMISSION
            net = gross - costs
            rows.append({
                "mechanism": mechanism,
                "fallback_minutes": fallback,
                "primary_pct": primary,
                "secondary_trail_pct": secondary,
                "movement_id": int(signal["movement_id"]),
                "signal_time": signal["signal_time"],
                "impulse_pct": signal["impulse_pct"],
                "passes_above_open": signal["passes_above_open"],
                "entry_time": signal["entry_time"],
                "entry_price": entry_price,
                "exit_time": exit_time,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "net_pnl_rub_10": net,
                "return_pct": net / (entry_price * CONTRACTS) * 100,
            })
        result = pd.DataFrame(rows)
        detail_rows.append(result)
        for universe, part in [("all_16", result), ("below_open_4", result[~result["passes_above_open"]])]:
            train, oos = part[part["signal_time"] <= SPLIT], part[part["signal_time"] > SPLIT]
            a, b, c = stats(part), stats(train), stats(oos)
            summary_rows.append({
                "universe": universe,
                "mechanism": mechanism,
                "fallback_minutes": fallback,
                "primary_pct": primary,
                "secondary_trail_pct": secondary,
                **{f"full_{k}": v for k, v in a.items()},
                **{f"train_{k}": v for k, v in b.items()},
                **{f"oos_{k}": v for k, v in c.items()},
            })
    details = pd.concat(detail_rows, ignore_index=True)
    summary = pd.DataFrame(summary_rows)
    summary["train_oos_positive"] = (summary["train_sum_return_pct"] > 0) & (summary["oos_sum_return_pct"] > 0)
    summary["robust_pf"] = np.sqrt(summary["train_profit_factor"].clip(lower=0) * summary["oos_profit_factor"].clip(lower=0))
    summary = summary.sort_values(["universe", "train_oos_positive", "full_sum_return_pct"], ascending=[True, False, False])

    selected = summary[
        (summary["universe"] == "below_open_4")
        & (
            ((summary["mechanism"] == "time_only") & (summary["fallback_minutes"] == 60))
            | ((summary["mechanism"] == "take_profit") & (summary["fallback_minutes"] == 60))
            | ((summary["mechanism"] == "stop_loss") & (summary["fallback_minutes"] == 60))
            | ((summary["mechanism"] == "trailing_from_entry") & (summary["fallback_minutes"] == 60))
            | ((summary["mechanism"] == "profit_activated_trailing") & (summary["fallback_minutes"] == 60))
        )
    ].copy()

    method = pd.DataFrame([
        ["Universe", "all 16 first-daily GREEN impulses >=2.5%, plus the selected four whose ending Close <= day Open"],
        ["Entry", "Short10 at next-minute Open after confirmed RED ST flip"],
        ["Fallback", "all mechanisms close at minute Close after 60 or 90 minutes if no earlier exit occurs"],
        ["Take profit", "fixed favorable move from entry; no stop"],
        ["Stop loss", "fixed adverse move from entry; no target"],
        ["Trailing from entry", "Short trailing stop follows the lowest completed price; current-minute High is checked against the stop based on the prior known low"],
        ["Profit-activated trailing", "trailing begins only after the specified favorable move; it cannot activate and exit on the same minute"],
        ["Conservatism", "trailing logic avoids optimistic assumptions about whether the minute Low occurred before its High"],
        ["Costs", "commission included; no slippage; rollover gaps adjusted"],
        ["Warning", "selected geometry has only four trades; parameter maxima are descriptive, not production-grade"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary_All", index=False)
        selected.to_excel(writer, sheet_name="Selected_60m_Comparison", index=False)
        details.to_excel(writer, sheet_name="All_Trades", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print("SELECTED FOUR, 60-MINUTE FALLBACK")
    for mechanism in ["time_only", "take_profit", "stop_loss", "trailing_from_entry", "profit_activated_trailing"]:
        print("\n", mechanism)
        print(selected[selected["mechanism"] == mechanism].sort_values("full_sum_return_pct", ascending=False).head(12).to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
