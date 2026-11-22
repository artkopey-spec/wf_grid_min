from datetime import datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_mix_st19 import read_bars, read_rollovers


ENTRY_FILE = Path("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx")
OPEN_FILE = Path("MIX_ST_19_4_above_open_final_backtest.xlsx")
VOLUME_FILE = Path("MIX_ST_19_4_volume_filter_test.xlsx")
OUTPUT = Path("MIX_ST_19_4_final_entry_before15_first_bar_close_time_exit_sweep.xlsx")
ENTRY_VARIANT = "100_at_0.4_plus_100_after_close_above_peak"
VOLUME_RULE = "add_only_if_impulse_in_bottom_60pct_train_threshold"
SPLIT = pd.Timestamp("2025-10-15 13:26:00")
COMMISSION = 0.0000666667
STOP_PCT = 1.5
SCALE = 0.1


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def summarize(name, frame):
    frame = frame.sort_values("signal_time")
    p = frame["net_pnl_rub_10"].astype(float)
    r = frame["return_pct"].astype(float)
    loss = -p[p < 0].sum()
    test = frame[frame["signal_time"] > SPLIT]
    tp = test["net_pnl_rub_10"].astype(float)
    tloss = -tp[tp < 0].sum()
    return {
        "variant": name,
        "trades": len(frame),
        "adds": int(frame["second_leg"].sum()),
        "weak_exits": int(frame["exit_reason"].str.startswith("weak_").sum()),
        "stop_exits": int(frame["exit_reason"].str.startswith("stop").sum()),
        "first_bar_close_exits": int((frame["exit_reason"] == "first_bar_close").sum()),
        "win_rate_pct": 100 * (p > 0).mean(),
        "average_return_pct": r.mean(),
        "median_return_pct": r.median(),
        "sum_return_pct": r.sum(),
        "profit_factor": p[p > 0].sum() / loss if loss else np.nan,
        "max_drawdown_pct": max_drawdown(r),
        "worst_trade_pct": r.min(),
        "net_pnl_rub_10": p.sum(),
        "max_drawdown_rub_10": max_drawdown(p),
        "test_trades": len(test),
        "test_average_return_pct": test["return_pct"].mean(),
        "test_sum_return_pct": test["return_pct"].sum(),
        "test_profit_factor": tp[tp > 0].sum() / tloss if tloss else np.nan,
        "test_net_pnl_rub_10": tp.sum(),
    }


def main():
    entries = pd.read_excel(ENTRY_FILE, sheet_name="Trades")
    entries = entries[entries["variant"] == ENTRY_VARIANT].copy()
    for col in ["signal_time", "leg_04_time", "breakout_time", "exit_time"]:
        entries[col] = pd.to_datetime(entries[col])

    signals = pd.read_excel(OPEN_FILE, sheet_name="Signals")
    signals = signals[signals["passes_above_open"] == True].copy()
    standards = pd.read_excel(VOLUME_FILE, sheet_name="Impulse_Volume_Standard")
    thresholds = pd.read_excel(VOLUME_FILE, sheet_name="Train_Thresholds")
    p60 = float(thresholds[
        (thresholds["feature"] == "impulse_rvol")
        & (thresholds["train_percentile"] == 60)
    ]["threshold"].iloc[0])
    signals = signals.merge(standards[["movement_id", "impulse_rvol"]], on="movement_id", how="left")
    signals["volume_allows_add"] = signals["impulse_rvol"].notna() & (signals["impulse_rvol"] <= p60)

    trades = entries.merge(
        signals[["movement_id", "impulse_rvol", "volume_allows_add", "end_vs_open_pct"]],
        on="movement_id", how="inner",
    )
    trades = trades[(trades["impulse_pct"] < 2.5) & (trades["leg_04_time"].dt.hour < 15)].copy()

    bars = read_bars()
    rolls = read_rollovers(bars)
    offset = 0.0
    roll_cursor = 0
    by_time = {}
    for bar in bars:
        while roll_cursor < len(rolls) and rolls[roll_cursor]["effective_index"] == bar["index"]:
            offset += rolls[roll_cursor]["offset_delta"]
            roll_cursor += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar

    rollover_dates = {r["effective_time"].date() for r in rolls if r["offset_delta"] != 0}
    bad_roll_bar_indices = {
        current["index"]
        for previous, current, following in zip(bars, bars[1:], bars[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }

    variants = [
        ("30 минут", 0.5, None),
        ("1 час", 1.0, None),
        ("2 часа", 2.0, None),
        ("3 часа", 3.0, None),
        ("19:00", None, time(19, 0)),
        ("Без временного выхода", None, None),
    ]

    def evaluate(trade, label, elapsed_hours, clock_time):
        signal_bar = by_time[trade["signal_time"]]
        first_bar = by_time[trade["leg_04_time"]]
        scheduled_exit = by_time[trade["exit_time"]]
        add_bar = None
        if bool(trade["volume_allows_add"]) and pd.notna(trade["breakout_time"]):
            add_bar = by_time.get(trade["breakout_time"])
        anchor_offset = signal_bar["offset"]

        def px(bar, field):
            return bar[field] + bar["offset"] - anchor_offset

        fills = [(float(trade["leg_04_price"]), 100)]
        qty = 100
        weighted_average = fills[0][0]
        exit_price = px(scheduled_exit, "close")
        exit_time = scheduled_exit["time"]
        exit_reason = "first_bar_close"
        checkpoint_done = elapsed_hours is None and clock_time is None
        if elapsed_hours is not None:
            checkpoint_target = first_bar["time"] + pd.Timedelta(hours=elapsed_hours)
        elif clock_time is not None:
            checkpoint_target = datetime.combine(first_bar["time"].date(), clock_time)
            if checkpoint_target < first_bar["time"]:
                checkpoint_target = first_bar["time"]
        else:
            checkpoint_target = None

        for bar in bars[first_bar["index"] + 1:scheduled_exit["index"] + 1]:
            if bar["index"] in bad_roll_bar_indices:
                continue
            op = px(bar, "open")
            stop = weighted_average * (1 - STOP_PCT / 100)
            if op <= stop:
                exit_price, exit_time, exit_reason = op, bar["time"], "stop_gap"
                break

            if add_bar is not None and bar["index"] == add_bar["index"]:
                fills.append((float(trade["breakout_price"]), 100))
                qty = 200
                weighted_average = sum(price * amount for price, amount in fills) / qty

            stop = weighted_average * (1 - STOP_PCT / 100)
            if px(bar, "low") <= stop:
                exit_price, exit_time, exit_reason = stop, bar["time"], "stop"
                break

            if bar["index"] == scheduled_exit["index"]:
                exit_price, exit_time, exit_reason = px(bar, "close"), bar["time"], "first_bar_close"
                break

            if not checkpoint_done and bar["time"] >= checkpoint_target:
                checkpoint_done = True
                close = px(bar, "close")
                if close <= weighted_average:
                    exit_price, exit_time, exit_reason = close, bar["time"], f"weak_{label}"
                    break

        gross = sum((exit_price - price) * amount for price, amount in fills)
        entry_cost = sum(price * amount * COMMISSION for price, amount in fills)
        exit_cost = exit_price * qty * COMMISSION
        net = gross - entry_cost - exit_cost
        initial_notional = fills[0][0] * 100
        return {
            "variant": label,
            "movement_id": int(trade["movement_id"]),
            "signal_time": trade["signal_time"],
            "first_entry_time": first_bar["time"],
            "impulse_rvol": trade["impulse_rvol"],
            "volume_allows_add": bool(trade["volume_allows_add"]),
            "second_leg": len(fills) == 2,
            "contracts": int(qty * SCALE),
            "weighted_average_entry": weighted_average,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "net_pnl_rub_10": net * SCALE,
            "return_pct": net / initial_notional * 100,
        }

    details = []
    summaries = []
    for label, elapsed_hours, clock_time in variants:
        result = pd.DataFrame([
            evaluate(row, label, elapsed_hours, clock_time)
            for _, row in trades.iterrows()
        ])
        details.append(result)
        summaries.append(summarize(label, result))

    details = pd.concat(details, ignore_index=True)
    summary = pd.DataFrame(summaries)
    summary["rank_net"] = summary["net_pnl_rub_10"].rank(method="min", ascending=False).astype(int)
    summary["rank_pf"] = summary["profit_factor"].rank(method="min", ascending=False).astype(int)
    summary["rank_low_dd"] = summary["max_drawdown_pct"].rank(method="min", ascending=False).astype(int)

    exit_rows = []
    for (variant, reason), group in details.groupby(["variant", "exit_reason"], sort=False):
        row = summarize(variant, group)
        exit_rows.append({"variant": variant, "exit_reason": reason, **row})

    method = pd.DataFrame([
        ["Signal", "first GREEN ST 19/4 impulse strictly >0.7% and <2.5%; final Close > adjusted day Open"],
        ["Entry", "Long10 at -0.4% correction from initiating impulse high; first entry strictly before 15:00"],
        ["Add", f"+10 at next-minute Open after minute Close > impulse high, only if impulse RVol <= frozen train P60 {p60:.6f}"],
        ["Stop", "1.5% below current weighted-average entry; recalculated after add; gap filled at minute Open"],
        ["Elapsed checkpoint", "at first available minute Close >= 30m/1h/2h/3h after first entry; exit only when Close <= weighted average"],
        ["19:00 checkpoint", "at first available minute Close >= 19:00 on entry date; exit only when Close <= weighted average"],
        ["Fallback", "if stop and weak checkpoint do not close the position, exit at Close of the first available minute bar of the next session"],
        ["First new-session bar", "the 1.5% stop remains active at its Open and against its Low before the Close exit"],
        ["Costs", "commission 0.00666667% per side; rollover gaps adjusted; two wrong-contract bars excluded; no slippage"],
        ["Percent", "returns normalized to notional of initial 10 contracts; a 20-contract stop can approach -4%"],
        ["OOS", f"fixed chronological test starts after {SPLIT}"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        details.to_excel(writer, sheet_name="Details", index=False)
        pd.DataFrame(exit_rows).to_excel(writer, sheet_name="Exit_Reasons", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(summary.to_string(index=False))
    print("signals", len(trades), "p60", p60)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
