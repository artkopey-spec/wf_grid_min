from pathlib import Path

import numpy as np
import pandas as pd

from analyze_mix_st19 import read_bars, read_rollovers


SIGNALS_FILE = Path("MIX_ST_19_4_climax_SHORT_exit_sweep.xlsx")
OUTPUT = Path("MIX_ST_19_4_climax_SHORT_time_target_stop_grid.xlsx")
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
    for col in ["signal_time", "entry_time"]:
        signals[col] = pd.to_datetime(signals[col])

    horizons = [15, 30, 45, 60, 75, 90, 120]
    targets = [None, 0.2, 0.3, 0.5, 0.7, 1.0]
    stops = [None, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]
    all_details = []
    summary_rows = []
    for horizon in horizons:
        for target_pct in targets:
            for stop_pct in stops:
                rows = []
                for _, signal in signals.iterrows():
                    entry_index = int(signal["entry_index"])
                    entry_price = float(signal["entry_price"])
                    target_time = signal["entry_time"] + pd.Timedelta(minutes=horizon)
                    exit_price = exit_time = exit_reason = None
                    for bar in bars[entry_index:]:
                        if bar["index"] in bad:
                            continue
                        # Conservative ordering when stop and target touch in one minute: stop first.
                        if stop_pct is not None and bar["adj_high"] >= entry_price * (1 + stop_pct / 100):
                            exit_price = entry_price * (1 + stop_pct / 100)
                            exit_time = pd.Timestamp(bar["time"])
                            exit_reason = "stop"
                            break
                        if target_pct is not None and bar["adj_low"] <= entry_price * (1 - target_pct / 100):
                            exit_price = entry_price * (1 - target_pct / 100)
                            exit_time = pd.Timestamp(bar["time"])
                            exit_reason = "target"
                            break
                        if pd.Timestamp(bar["time"]) >= target_time:
                            exit_price = float(bar["adj_close"])
                            exit_time = pd.Timestamp(bar["time"])
                            exit_reason = "time"
                            break
                    gross = (entry_price - exit_price) * CONTRACTS
                    costs = (entry_price + exit_price) * CONTRACTS * COMMISSION
                    net = gross - costs
                    rows.append({
                        "horizon_minutes": horizon,
                        "target_pct": target_pct,
                        "stop_pct": stop_pct,
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
                all_details.append(result)
                train = result[result["signal_time"] <= SPLIT]
                oos = result[result["signal_time"] > SPLIT]
                full_s, train_s, oos_s = stats(result), stats(train), stats(oos)
                summary_rows.append({
                    "horizon_minutes": horizon,
                    "target_pct": target_pct,
                    "stop_pct": stop_pct,
                    **{f"full_{k}": v for k, v in full_s.items()},
                    **{f"train_{k}": v for k, v in train_s.items()},
                    **{f"oos_{k}": v for k, v in oos_s.items()},
                })
    summary = pd.DataFrame(summary_rows)
    details = pd.concat(all_details, ignore_index=True)
    # Prefer robustness: positive train and OOS, then rank average PF and lower drawdown.
    summary["both_positive"] = (summary["train_sum_return_pct"] > 0) & (summary["oos_sum_return_pct"] > 0)
    summary["robust_pf"] = np.sqrt(summary["train_profit_factor"].clip(lower=0) * summary["oos_profit_factor"].clip(lower=0))
    summary["rank_robust_pf"] = summary["robust_pf"].rank(method="min", ascending=False)
    summary = summary.sort_values(["both_positive", "robust_pf", "full_sum_return_pct"], ascending=[False, False, False])

    time_only = summary[summary["target_pct"].isna() & summary["stop_pct"].isna()].copy()
    method = pd.DataFrame([
        ["Entry", "same 16 Short10 entries as exit sweep: next-minute Open after confirmed RED ST flip following first daily GREEN impulse >=2.5%"],
        ["Time exit", "Close of first available minute at or after 15/30/45/60/75/90/120 elapsed minutes"],
        ["Profit target", "intrabar Low reaches entry times (1-target); cover at target price"],
        ["Stop", "intrabar High reaches entry times (1+stop); cover at stop price"],
        ["Same-bar ambiguity", "conservative ordering: stop is assumed before target"],
        ["Selection discipline", "train and OOS are shown separately; no parameter should be accepted solely from the 16-trade full-sample maximum"],
        ["Costs", "commission included; no slippage; roll gaps adjusted; two bad bars excluded"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Grid_Summary", index=False)
        time_only.to_excel(writer, sheet_name="Time_Only", index=False)
        details.to_excel(writer, sheet_name="All_Trades", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print("TIME ONLY")
    print(time_only.to_string(index=False))
    print("\nTOP ROBUST")
    print(summary.head(25).to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
