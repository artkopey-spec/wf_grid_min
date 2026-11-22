from pathlib import Path

import numpy as np
import pandas as pd

from analyze_mix_st19 import read_bars, read_rollovers


ENTRY_FILE = Path("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx")
OPEN_FILE = Path("MIX_ST_19_4_above_open_final_backtest.xlsx")
VOLUME_FILE = Path("MIX_ST_19_4_volume_filter_test.xlsx")
OUTPUT = Path("MIX_ST_19_4_FINAL_30min_first_bar_close_day_close_loss_test.xlsx")
ENTRY_VARIANT = "100_at_0.4_plus_100_after_close_above_peak"
COMMISSION = 0.0000666667
STOP_PCT = 1.5
SCALE = 0.1
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def summarize(name, frame):
    frame = frame.sort_values("signal_time")
    p = frame["net_pnl_rub_10"].astype(float)
    r = frame["return_pct"].astype(float)
    losses = -p[p < 0].sum()
    test = frame[frame["signal_time"] > SPLIT]
    tp = test["net_pnl_rub_10"].astype(float)
    tlosses = -tp[tp < 0].sum()
    return {
        "variant": name,
        "trades": len(frame),
        "adds": int(frame["second_leg"].sum()),
        "weak_30m_exits": int((frame["exit_reason"] == "weak_30m").sum()),
        "day_close_exits": int(frame["exit_reason"].str.startswith("day_close").sum()),
        "day_close_loss_exits": int(frame["exit_reason"].str.startswith("day_close_loss").sum()),
        "stop_exits": int(frame["exit_reason"].str.startswith("stop").sum()),
        "first_bar_close_exits": int((frame["exit_reason"] == "first_bar_close").sum()),
        "overnight_positions": int((frame["exit_reason"] == "first_bar_close").sum()),
        "win_rate_pct": 100 * (p > 0).mean(),
        "average_return_pct": r.mean(),
        "median_return_pct": r.median(),
        "sum_return_pct": r.sum(),
        "profit_factor": p[p > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(r),
        "worst_trade_pct": r.min(),
        "net_pnl_rub_10": p.sum(),
        "max_drawdown_rub_10": max_drawdown(p),
        "test_trades": len(test),
        "test_average_return_pct": test["return_pct"].mean(),
        "test_sum_return_pct": test["return_pct"].sum(),
        "test_profit_factor": tp[tp > 0].sum() / tlosses if tlosses else np.nan,
        "test_max_drawdown_pct": max_drawdown(test["return_pct"]),
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
    last_bar_by_date = {}
    for bar in bars:
        while roll_cursor < len(rolls) and rolls[roll_cursor]["effective_index"] == bar["index"]:
            offset += rolls[roll_cursor]["offset_delta"]
            roll_cursor += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar
        last_bar_by_date[bar["time"].date()] = bar

    rollover_dates = {r["effective_time"].date() for r in rolls if r["offset_delta"] != 0}
    bad_roll_bar_indices = {
        current["index"]
        for previous, current, following in zip(bars, bars[1:], bars[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }

    variants = [
        ("Без выхода на закрытии", "none"),
        ("Закрыть убыточную по цене", "gross"),
        ("Закрыть убыточную с комиссией", "net"),
        ("Закрыть все позиции на закрытии", "all"),
    ]

    def evaluate(trade, label, close_rule):
        signal_bar = by_time[trade["signal_time"]]
        first_bar = by_time[trade["leg_04_time"]]
        scheduled_exit = by_time[trade["exit_time"]]
        day_close_bar = last_bar_by_date[first_bar["time"].date()]
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
        close_price_seen = np.nan
        losing_at_day_close_gross = False
        losing_at_day_close_net = False
        checkpoint_target = first_bar["time"] + pd.Timedelta(minutes=30)
        checkpoint_done = False

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

            if not checkpoint_done and bar["time"] >= checkpoint_target:
                checkpoint_done = True
                if px(bar, "close") <= weighted_average:
                    exit_price, exit_time, exit_reason = px(bar, "close"), bar["time"], "weak_30m"
                    break

            if bar["index"] == scheduled_exit["index"]:
                exit_price, exit_time, exit_reason = px(bar, "close"), bar["time"], "first_bar_close"
                break

            if bar["index"] == day_close_bar["index"]:
                close = px(bar, "close")
                close_price_seen = close
                losing_at_day_close_gross = close <= weighted_average
                candidate_gross = sum((close - price) * amount for price, amount in fills)
                candidate_entry_cost = sum(price * amount * COMMISSION for price, amount in fills)
                candidate_exit_cost = close * qty * COMMISSION
                losing_at_day_close_net = candidate_gross - candidate_entry_cost - candidate_exit_cost <= 0
                should_exit = (
                    (close_rule == "gross" and losing_at_day_close_gross)
                    or (close_rule == "net" and losing_at_day_close_net)
                    or close_rule == "all"
                )
                if should_exit:
                    exit_price, exit_time = close, bar["time"]
                    exit_reason = "day_close_all" if close_rule == "all" else f"day_close_loss_{close_rule}"
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
            "day_close_time": day_close_bar["time"],
            "day_close_price": close_price_seen,
            "losing_at_day_close_gross": losing_at_day_close_gross,
            "losing_at_day_close_net": losing_at_day_close_net,
            "impulse_rvol": trade["impulse_rvol"],
            "second_leg": len(fills) == 2,
            "contracts": int(qty * SCALE),
            "weighted_average_entry": weighted_average,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "net_pnl_rub_10": net * SCALE,
            "return_pct": net / initial_notional * 100,
        }

    summaries = []
    details = []
    for label, close_rule in variants:
        result = pd.DataFrame([evaluate(row, label, close_rule) for _, row in trades.iterrows()])
        details.append(result)
        summaries.append(summarize(label, result))
    details = pd.concat(details, ignore_index=True)
    summary = pd.DataFrame(summaries)

    baseline = details[details["variant"] == variants[0][0]][["movement_id", "net_pnl_rub_10", "return_pct"]]
    baseline = baseline.rename(columns={"net_pnl_rub_10": "baseline_pnl", "return_pct": "baseline_return"})
    comparison = details.merge(baseline, on="movement_id", how="left")
    comparison["delta_pnl_vs_baseline"] = comparison["net_pnl_rub_10"] - comparison["baseline_pnl"]
    comparison["delta_return_vs_baseline"] = comparison["return_pct"] - comparison["baseline_return"]

    method = pd.DataFrame([
        ["Final base strategy", "initiating impulse strictly >0.7% and <2.5%; first Long10 strictly before 15:00; add after 15:00 allowed; RVol add filter; 1.5% weighted-average stop; mandatory 30-minute weak-position check"],
        ["30-minute check", "at the first available minute Close at or after first entry +30 minutes, exit all if Close <= current weighted-average entry"],
        ["Trading-day close", "last available minute bar of the first-entry calendar date before the next trading Open; normally 23:49 Moscow time"],
        ["Gross loss rule", "at day-close minute, exit at Close when Close <= current weighted-average entry"],
        ["Net loss rule", "at day-close minute, estimate immediate exit including entry and exit commissions; exit when net trade P&L <= 0"],
        ["Profitable at close", "hold through the first minute of the next session and exit at that bar's Close unless the 1.5% stop triggers"],
        ["First new-session bar", "the stop remains active at the bar's Open and against its Low before the Close exit"],
        ["Close all control", "exit every surviving position at the trading-day Close; this is the only tested rule that fully removes overnight position exposure"],
        ["Stop", "1.5% below current weighted-average price; checked before the conditional day-close rule"],
        ["Costs", "commission 0.00666667% per side; rollover gaps adjusted; two wrong-contract bars excluded; no slippage"],
        ["OOS", f"fixed chronological test starts after {SPLIT}"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        comparison.to_excel(writer, sheet_name="Details", index=False)
        comparison[comparison["exit_reason"].str.startswith("day_close")].to_excel(writer, sheet_name="Day_Close_Exits", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(summary.to_string(index=False))
    print("signals", len(trades), "p60", p60)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
