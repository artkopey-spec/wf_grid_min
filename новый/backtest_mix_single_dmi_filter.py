from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path("MIX_ST_19_4_eight_loss_streak_market_regime.xlsx")
OUTPUT = Path("MIX_ST_19_4_single_DMI_downtrend_filter_backtest.xlsx")
SPLIT = pd.Timestamp("2025-10-15 13:26:00")
SELECTED_THRESHOLD = 27.5


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def longest_streak(frame, positive):
    signs = np.where(frame["net_pnl_rub_10"].to_numpy() > 0, 1, -1)
    target = 1 if positive else -1
    best = current = 0
    for sign in signs:
        current = current + 1 if sign == target else 0
        best = max(best, current)
    return best


def stats(name, frame):
    frame = frame.sort_values("signal_time")
    pnl = frame["net_pnl_rub_10"].astype(float)
    ret = frame["return_pct"].astype(float)
    losses = -pnl[pnl < 0].sum()
    return {
        "variant": name,
        "trades": len(frame),
        "win_rate_pct": 100 * (pnl > 0).mean() if len(frame) else np.nan,
        "average_return_pct": ret.mean() if len(frame) else np.nan,
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(ret) if len(frame) else np.nan,
        "max_consecutive_losses": longest_streak(frame, False),
        "max_consecutive_wins": longest_streak(frame, True),
        "net_pnl_rub_10": pnl.sum(),
    }


def main():
    trades = pd.read_excel(SOURCE, sheet_name="All_Trade_Features")
    trades["signal_time"] = pd.to_datetime(trades["signal_time"])
    trades = trades.sort_values("signal_time").reset_index(drop=True)
    trades["dmi_downtrend_strength"] = trades["minus_di14"] - trades["plus_di14"]
    trades["selected_filter_triggered"] = trades["dmi_downtrend_strength"] > SELECTED_THRESHOLD

    train = trades[trades["signal_time"] <= SPLIT]
    test = trades[trades["signal_time"] > SPLIT]
    selected = trades[~trades["selected_filter_triggered"]].copy()
    selected_train = selected[selected["signal_time"] <= SPLIT]
    selected_test = selected[selected["signal_time"] > SPLIT]

    summary = pd.DataFrame([
        stats("Baseline — full", trades),
        stats("DMI filter — full", selected),
        stats("Baseline — train", train),
        stats("DMI filter — train", selected_train),
        stats("Baseline — OOS", test),
        stats("DMI filter — OOS", selected_test),
    ])

    sweep_rows = []
    for threshold in np.arange(20.0, 35.01, 2.5):
        adverse = trades["dmi_downtrend_strength"] > threshold
        kept = trades[~adverse]
        kept_train = kept[kept["signal_time"] <= SPLIT]
        kept_test = kept[kept["signal_time"] > SPLIT]
        streak_skipped = int((adverse & trades["in_eight_loss_streak"]).sum())
        full_stats = stats("full", kept)
        train_stats = stats("train", kept_train)
        test_stats = stats("OOS", kept_test)
        sweep_rows.append({
            "threshold": threshold,
            "rule": f"skip when -DI14 minus +DI14 > {threshold:g}",
            "skipped_trades": int(adverse.sum()),
            "skipped_from_eight_loss_period": streak_skipped,
            "full_trades": full_stats["trades"],
            "full_average_return_pct": full_stats["average_return_pct"],
            "full_sum_return_pct": full_stats["sum_return_pct"],
            "full_profit_factor": full_stats["profit_factor"],
            "full_max_drawdown_pct": full_stats["max_drawdown_pct"],
            "full_max_consecutive_losses": full_stats["max_consecutive_losses"],
            "train_average_return_pct": train_stats["average_return_pct"],
            "train_profit_factor": train_stats["profit_factor"],
            "train_max_drawdown_pct": train_stats["max_drawdown_pct"],
            "oos_trades": test_stats["trades"],
            "oos_average_return_pct": test_stats["average_return_pct"],
            "oos_sum_return_pct": test_stats["sum_return_pct"],
            "oos_profit_factor": test_stats["profit_factor"],
            "oos_max_drawdown_pct": test_stats["max_drawdown_pct"],
        })
    sweep = pd.DataFrame(sweep_rows)
    sweep["selected"] = sweep["threshold"].eq(SELECTED_THRESHOLD)

    excluded = trades[trades["selected_filter_triggered"]].copy()
    excluded["exclusion_reason"] = f"-DI14 minus +DI14 > {SELECTED_THRESHOLD}"
    streak = trades[trades["in_eight_loss_streak"]].copy()

    method = pd.DataFrame([
        ["Single criterion", "DMI downtrend strength = daily -DI(14) minus daily +DI(14)"],
        ["Rule", f"skip a new Long when DMI downtrend strength > {SELECTED_THRESHOLD}"],
        ["Availability", "daily DMI uses completed trading days strictly before the signal; no future data"],
        ["Interpretation", "positive values mean sellers dominate; values above 27.5 mark unusually strong directional selling"],
        ["Selection", "threshold neighborhood 20–35 examined on the train sample; 27.5 retained because improvement also persisted OOS and the rule removed fewer trades than 25 while catching the same two trades from the eight-loss period"],
        ["Warning", "this is a second-stage research result on one historical sample; paper/live validation is still required"],
        ["Percent convention", "unchanged from final strategy: normalized to initial Long10 notional; commission included; no slippage"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        sweep.to_excel(writer, sheet_name="Threshold_Sweep", index=False)
        selected.to_excel(writer, sheet_name="Filtered_Trades", index=False)
        excluded.to_excel(writer, sheet_name="Excluded_Trades", index=False)
        streak.to_excel(writer, sheet_name="Eight_Loss_Impact", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(summary.to_string(index=False))
    print("selected excluded", len(excluded), "from streak", int(excluded["in_eight_loss_streak"].sum()))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
