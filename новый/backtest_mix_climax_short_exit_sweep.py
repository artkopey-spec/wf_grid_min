from pathlib import Path

import numpy as np
import pandas as pd

from analyze_mix_st19 import read_bars, read_flips, read_rollovers


SIGNALS_FILE = Path("MIX_ST_19_4_above_open_final_backtest.xlsx")
MOVEMENTS_FILE = Path("MIX_ST_19_4_analysis.xlsx")
OUTPUT = Path("MIX_ST_19_4_climax_SHORT_exit_sweep.xlsx")
IMPULSE_MIN = 2.5
COMMISSION = 0.0000666667
CONTRACTS = 10
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def summarize(name, frame):
    frame = frame.sort_values("signal_time")
    pnl = frame["net_pnl_rub_10"].astype(float)
    ret = frame["return_pct"].astype(float)
    losses = -pnl[pnl < 0].sum()
    test = frame[frame["signal_time"] > SPLIT]
    tp = test["net_pnl_rub_10"].astype(float)
    tlosses = -tp[tp < 0].sum()
    return {
        "exit_rule": name,
        "trades": len(frame),
        "win_rate_pct": 100 * (pnl > 0).mean() if len(frame) else np.nan,
        "average_return_pct": ret.mean() if len(frame) else np.nan,
        "median_return_pct": ret.median() if len(frame) else np.nan,
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(ret) if len(frame) else np.nan,
        "worst_trade_pct": ret.min() if len(frame) else np.nan,
        "best_trade_pct": ret.max() if len(frame) else np.nan,
        "net_pnl_rub_10": pnl.sum(),
        "oos_trades": len(test),
        "oos_average_return_pct": test["return_pct"].mean() if len(test) else np.nan,
        "oos_sum_return_pct": test["return_pct"].sum(),
        "oos_profit_factor": tp[tp > 0].sum() / tlosses if tlosses else np.nan,
        "oos_max_drawdown_pct": max_drawdown(test["return_pct"]) if len(test) else np.nan,
    }


def main():
    bars = read_bars()
    flips = read_flips()
    rolls = read_rollovers(bars)
    offset = 0.0
    cursor = 0
    for bar in bars:
        while cursor < len(rolls) and rolls[cursor]["effective_index"] == bar["index"]:
            offset += rolls[cursor]["offset_delta"]
            cursor += 1
        bar["offset"] = offset
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

    movements = pd.read_excel(MOVEMENTS_FILE, sheet_name="Movements_Sorted")
    for col in ["start_time", "end_time"]:
        movements[col] = pd.to_datetime(movements[col])
    movements = movements.sort_values("start_time").reset_index(drop=True)
    movement_pos = {int(mid): i for i, mid in enumerate(movements["movement_id"])}

    signals = pd.read_excel(SIGNALS_FILE, sheet_name="Signals")
    for col in ["start_time", "end_time"]:
        signals[col] = pd.to_datetime(signals[col])
    signals = signals[signals["impulse_pct"] >= IMPULSE_MIN].sort_values("end_time").reset_index(drop=True)

    candidates = []
    for _, signal in signals.iterrows():
        mid = int(signal["movement_id"])
        pos = movement_pos[mid]
        red_move = movements.iloc[pos + 1]
        if red_move["st_color"] != "RED":
            raise ValueError(f"movement {mid} is not followed by RED")
        flip_bar = by_time[pd.Timestamp(signal["end_time"])]
        entry_index = flip_bar["index"] + 1
        while entry_index < len(bars) and entry_index in bad:
            entry_index += 1
        if entry_index >= len(bars):
            continue
        entry_bar = bars[entry_index]

        # The next GREEN flip is confirmed at the end of the following RED movement.
        reverse_flip_bar = by_time[pd.Timestamp(red_move["end_time"])]
        reverse_exit_index = reverse_flip_bar["index"] + 1
        while reverse_exit_index < len(bars) and reverse_exit_index in bad:
            reverse_exit_index += 1
        next_session_index = next((b["index"] for b in bars[entry_index + 1:] if b["time"].date() > entry_bar["time"].date() and b["index"] not in bad), None)
        same_day_indices = [b["index"] for b in bars[entry_index:] if b["time"].date() == entry_bar["time"].date() and b["index"] not in bad]
        day_close_index = same_day_indices[-1] if same_day_indices else None
        at_19_index = next((b["index"] for b in bars[entry_index:] if b["time"].date() == entry_bar["time"].date() and b["time"].time() >= pd.Timestamp("19:00").time() and b["index"] not in bad), None)
        candidates.append({
            "movement_id": mid,
            "signal_time": pd.Timestamp(signal["end_time"]),
            "impulse_start_time": pd.Timestamp(signal["start_time"]),
            "impulse_pct": float(signal["impulse_pct"]),
            "passes_above_open": bool(signal["passes_above_open"]),
            "entry_index": entry_index,
            "entry_time": pd.Timestamp(entry_bar["time"]),
            "entry_price": float(entry_bar["adj_open"]),
            "reverse_flip_exit_index": reverse_exit_index,
            "next_session_index": next_session_index,
            "day_close_index": day_close_index,
            "at_19_index": at_19_index,
        })

    rules = [
        ("30 minutes", "elapsed", pd.Timedelta(minutes=30)),
        ("1 hour", "elapsed", pd.Timedelta(hours=1)),
        ("2 hours", "elapsed", pd.Timedelta(hours=2)),
        ("3 hours", "elapsed", pd.Timedelta(hours=3)),
        ("6 hours", "elapsed", pd.Timedelta(hours=6)),
        ("19:00 same date", "index", "at_19_index"),
        ("Entry-day last minute Close", "index", "day_close_index"),
        ("Next-session first minute Close", "index", "next_session_index"),
        ("Next GREEN flip, next-minute Open", "open_index", "reverse_flip_exit_index"),
        ("24 elapsed hours", "elapsed", pd.Timedelta(hours=24)),
    ]

    details = []
    for candidate in candidates:
        for label, kind, parameter in rules:
            exit_index = None
            exit_field = "adj_close"
            if kind == "elapsed":
                target = candidate["entry_time"] + parameter
                exit_index = next((b["index"] for b in bars[candidate["entry_index"]:] if pd.Timestamp(b["time"]) >= target and b["index"] not in bad), None)
            elif kind == "index":
                exit_index = candidate[parameter]
            elif kind == "open_index":
                exit_index = candidate[parameter]
                exit_field = "adj_open"
            if exit_index is None or exit_index >= len(bars):
                continue
            exit_bar = bars[exit_index]
            entry_price = candidate["entry_price"]
            exit_price = float(exit_bar[exit_field])
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
    summary = pd.DataFrame([summarize(label, details[details["exit_rule"] == label]) for label, _, _ in rules])
    summary["rank_sum"] = summary["sum_return_pct"].rank(method="min", ascending=False).astype(int)
    summary["rank_pf"] = summary["profit_factor"].rank(method="min", ascending=False).astype(int)
    summary["rank_low_dd"] = summary["max_drawdown_pct"].rank(method="min", ascending=False).astype(int)

    method = pd.DataFrame([
        ["Signal universe", "first GREEN ST 19/4 impulse of each trading day with movement size >=2.5%; no Close>day-Open or entry-time filter"],
        ["Flip confirmation", "the first RED ST minute bar closes; this is the end_time of the initiating GREEN movement"],
        ["Entry", "Short10 at Open of the next available minute; entering at the first RED bar Open is not allowed because the flip is not yet known"],
        ["Time exits", "Close of first available minute bar at or after the elapsed target"],
        ["Reverse flip exit", "after first GREEN bar confirms the reverse flip, cover at next-minute Open"],
        ["Next session", "Close of the first minute bar whose calendar date is after entry date"],
        ["Costs", "commission 0.00666667% per side; no slippage; rollover gaps adjusted; two wrong-contract bars excluded"],
        ["Position overlap", "every signal is evaluated as an independent 10-contract trade"],
        ["Stops", "not applied in this first exit sweep; tested separately after selecting an exit family"],
        ["OOS", f"fixed chronological split after {SPLIT}"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        details.to_excel(writer, sheet_name="Trades", index=False)
        pd.DataFrame(candidates).to_excel(writer, sheet_name="Signals", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(summary.to_string(index=False))
    print("signals", len(candidates))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
