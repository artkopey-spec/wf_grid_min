from pathlib import Path
from bisect import bisect_left

import numpy as np
import pandas as pd

from analyze_mix_st19 import read_bars, read_rollovers


SIGNALS_FILE = Path("MIX_ST_19_4_above_open_final_backtest.xlsx")
VOLUME_FILE = Path("MIX_ST_19_4_volume_filter_test.xlsx")
OUTPUT = Path("MIX_ST_19_4_high_volume_first_impulse_SHORT.xlsx")
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
        "median_return_pct": ret.median() if len(frame) else np.nan,
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(ret) if len(frame) else np.nan,
        "worst_trade_pct": ret.min() if len(frame) else np.nan,
        "best_trade_pct": ret.max() if len(frame) else np.nan,
        "net_pnl_rub_10": pnl.sum(),
    }


def main():
    bars = read_bars()
    rolls = read_rollovers(bars)
    offset = 0.0
    roll_cursor = 0
    for bar in bars:
        while roll_cursor < len(rolls) and rolls[roll_cursor]["effective_index"] == bar["index"]:
            offset += rolls[roll_cursor]["offset_delta"]
            roll_cursor += 1
        for field in ["open", "high", "low", "close"]:
            bar[f"adj_{field}"] = bar[field] + offset
    by_time = {pd.Timestamp(bar["time"]): bar for bar in bars}

    rollover_dates = {r["effective_time"].date() for r in rolls if r["offset_delta"] != 0}
    bad = {
        current["index"]
        for previous, current, following in zip(bars, bars[1:], bars[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }
    valid_bars = [bar for bar in bars if bar["index"] not in bad]
    valid_times = [pd.Timestamp(bar["time"]) for bar in valid_bars]
    bars_by_date = {}
    for bar in valid_bars:
        bars_by_date.setdefault(pd.Timestamp(bar["time"]).date(), []).append(bar)
    trading_dates = sorted(bars_by_date)
    next_date = {date: trading_dates[i + 1] if i + 1 < len(trading_dates) else None
                 for i, date in enumerate(trading_dates)}

    thresholds = pd.read_excel(VOLUME_FILE, sheet_name="Train_Thresholds")
    p = {
        int(row.train_percentile): float(row.threshold)
        for row in thresholds[thresholds["feature"] == "impulse_rvol"].itertuples()
    }
    volumes = pd.read_excel(VOLUME_FILE, sheet_name="Impulse_Volume_Standard")
    signals = pd.read_excel(SIGNALS_FILE, sheet_name="Signals")
    for col in ["start_time", "end_time"]:
        signals[col] = pd.to_datetime(signals[col])
    signals = signals.merge(volumes[["movement_id", "impulse_rvol"]], on="movement_id", how="left")
    signals = signals[signals["impulse_rvol"].notna()].copy()

    candidates = []
    for signal in signals.itertuples():
        flip_bar = by_time.get(pd.Timestamp(signal.end_time))
        if flip_bar is None:
            continue
        entry_index = flip_bar["index"] + 1
        while entry_index < len(bars) and entry_index in bad:
            entry_index += 1
        if entry_index >= len(bars):
            continue
        entry_bar = bars[entry_index]
        entry_time = pd.Timestamp(entry_bar["time"])
        same_date = [b for b in bars_by_date.get(entry_time.date(), []) if b["index"] >= entry_index]
        at_19 = next((b for b in same_date if pd.Timestamp(b["time"]).time() >= pd.Timestamp("19:00").time()), None)
        day_last = same_date[-1] if same_date else None
        following_date = next_date.get(entry_time.date())
        next_session = bars_by_date[following_date][0] if following_date is not None else None
        candidates.append({
            "movement_id": int(signal.movement_id),
            "signal_time": pd.Timestamp(signal.end_time),
            "impulse_start_time": pd.Timestamp(signal.start_time),
            "impulse_pct": float(signal.impulse_pct),
            "impulse_rvol": float(signal.impulse_rvol),
            "passes_above_open": bool(signal.passes_above_open),
            "entry_index": entry_index,
            "entry_time": entry_time,
            "entry_hour": entry_time.hour + entry_time.minute / 60,
            "entry_price": float(entry_bar["adj_open"]),
            "at_19_index": at_19["index"] if at_19 else None,
            "day_last_index": day_last["index"] if day_last else None,
            "next_session_index": next_session["index"] if next_session else None,
        })
    candidates = pd.DataFrame(candidates)

    rules = [("15m", "elapsed", 15), ("30m", "elapsed", 30),
             ("45m", "elapsed", 45), ("60m", "elapsed", 60),
             ("90m", "elapsed", 90), ("120m", "elapsed", 120),
             ("180m", "elapsed", 180), ("19:00", "index", "at_19_index"),
             ("day_last_close", "index", "day_last_index"),
             ("next_session_first_close", "index", "next_session_index")]
    details = []
    for candidate in candidates.to_dict("records"):
        for label, kind, parameter in rules:
            if kind == "elapsed":
                deadline = candidate["entry_time"] + pd.Timedelta(minutes=parameter)
                pos = bisect_left(valid_times, deadline)
                exit_bar = valid_bars[pos] if pos < len(valid_bars) else None
            else:
                index = candidate[parameter]
                exit_bar = bars[int(index)] if pd.notna(index) else None
            if exit_bar is None:
                continue
            entry_price = candidate["entry_price"]
            exit_price = float(exit_bar["adj_close"])
            gross = (entry_price - exit_price) * CONTRACTS
            costs = (entry_price + exit_price) * CONTRACTS * COMMISSION
            net = gross - costs
            details.append({
                "exit_rule": label,
                **{k: v for k, v in candidate.items() if not k.endswith("_index")},
                "exit_time": pd.Timestamp(exit_bar["time"]),
                "exit_price": exit_price,
                "holding_minutes": (pd.Timestamp(exit_bar["time"]) - candidate["entry_time"]).total_seconds() / 60,
                "net_pnl_rub_10": net,
                "return_pct": net / (entry_price * CONTRACTS) * 100,
            })
    details = pd.DataFrame(details)

    universes = {
        "P80_all_sizes": lambda d: d["impulse_rvol"] > p[80],
        "P80_size_0.7_to_2.5": lambda d: (d["impulse_rvol"] > p[80]) & (d["impulse_pct"] < 2.5),
        "P80_size_0.7_to_2.5_above_open": lambda d: (d["impulse_rvol"] > p[80]) & (d["impulse_pct"] < 2.5) & d["passes_above_open"],
        "P80_size_0.7_to_2.5_below_open": lambda d: (d["impulse_rvol"] > p[80]) & (d["impulse_pct"] < 2.5) & ~d["passes_above_open"],
        "P80_climax_ge_2.5": lambda d: (d["impulse_rvol"] > p[80]) & (d["impulse_pct"] >= 2.5),
        "P90_size_0.7_to_2.5": lambda d: (d["impulse_rvol"] > p[90]) & (d["impulse_pct"] < 2.5),
        "P80_size_0.7_to_2.5_entry_before15": lambda d: (d["impulse_rvol"] > p[80]) & (d["impulse_pct"] < 2.5) & (d["entry_hour"] < 15),
        "P80_size_0.7_to_2.5_entry_15plus": lambda d: (d["impulse_rvol"] > p[80]) & (d["impulse_pct"] < 2.5) & (d["entry_hour"] >= 15),
    }
    summary_rows = []
    for universe, mask_fn in universes.items():
        for rule, _, _ in rules:
            part = details[(details["exit_rule"] == rule) & mask_fn(details)]
            train = part[part["signal_time"] <= SPLIT]
            oos = part[part["signal_time"] > SPLIT]
            a, b, c = stats(part), stats(train), stats(oos)
            summary_rows.append({
                "universe": universe,
                "exit_rule": rule,
                **{f"full_{k}": v for k, v in a.items()},
                **{f"train_{k}": v for k, v in b.items()},
                **{f"oos_{k}": v for k, v in c.items()},
                "train_oos_positive": b["sum_return_pct"] > 0 and c["sum_return_pct"] > 0,
            })
    summary = pd.DataFrame(summary_rows)
    summary["robust_pf"] = np.sqrt(summary["train_profit_factor"].clip(lower=0) * summary["oos_profit_factor"].clip(lower=0))

    # Direct volume threshold profile at the least optimized 60-minute exit.
    threshold_rows = []
    sixty = details[(details["exit_rule"] == "60m") & (details["impulse_pct"] < 2.5)]
    for percentile in [50, 60, 70, 80, 90]:
        part = sixty[sixty["impulse_rvol"] > p[percentile]]
        a = stats(part)
        threshold_rows.append({"volume_percentile": percentile, "rvol_threshold": p[percentile], **a})
    threshold_profile = pd.DataFrame(threshold_rows)

    method = pd.DataFrame([
        ["High volume", f"impulse RVol above frozen train P80 = {p[80]:.6f}; threshold uses data only through the chronological split"],
        ["Signal", "first GREEN ST 19/4 impulse of each trading day, known completely at the confirmed RED flip"],
        ["Entry", "Short10 at Open of the next available minute after the RED flip minute has closed"],
        ["Primary non-overlap universe", "0.7% <= impulse < 2.5%; impulses >=2.5% are reported separately because they belong to the climax-short study"],
        ["No look-ahead", "the short decision uses only completed impulse size, its completed volume, day Open geometry, and confirmed ST flip"],
        ["Time exits", "Close of first available minute at or after each elapsed horizon"],
        ["Costs", "commission 0.00666667% per side; no slippage; rollover gaps adjusted; isolated wrong-contract rollover bars excluded"],
        ["OOS", f"chronological split after {SPLIT}"],
        ["Current long-rule clarification", "the newest base Long enters the first 10 at any RVol and uses P60 only to decide the second +10; full P80 exclusion belongs to an earlier tiered version"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary_Time_Exits", index=False)
        threshold_profile.to_excel(writer, sheet_name="Volume_Threshold_Profile", index=False)
        details.to_excel(writer, sheet_name="All_Trades", index=False)
        candidates.to_excel(writer, sheet_name="All_First_Impulses", index=False)
        pd.DataFrame([{"percentile": k, "rvol_threshold": v} for k, v in p.items()]).to_excel(writer, sheet_name="Frozen_Thresholds", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    cols = ["universe", "exit_rule", "full_trades", "full_average_return_pct", "full_sum_return_pct",
            "full_profit_factor", "full_max_drawdown_pct", "train_sum_return_pct", "oos_sum_return_pct", "train_oos_positive"]
    best = summary.sort_values(["universe", "train_oos_positive", "full_sum_return_pct"], ascending=[True, False, False]).groupby("universe").head(3)
    print("P80", p[80], "P90", p[90])
    print(best[cols].to_string(index=False))
    print("\nTHRESHOLD PROFILE 60M")
    print(threshold_profile.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
