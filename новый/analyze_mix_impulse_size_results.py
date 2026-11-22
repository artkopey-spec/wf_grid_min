from pathlib import Path

import numpy as np
import pandas as pd


TRADES_FILE = Path("MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx")
ENTRY_FILE = Path("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx")
OUTPUT = Path("MIX_ST_19_4_impulse_size_result_analysis.xlsx")
ENTRY_VARIANT = "100_at_0.4_plus_100_after_close_above_peak"
SPLIT = pd.Timestamp("2025-10-15 13:26:00")
FIXED_BINS = [0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0, 3.0, np.inf]


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(name, frame):
    frame = frame.sort_values("signal_time")
    pnl = frame["net_pnl_rub_10"].astype(float)
    ret = frame["return_pct"].astype(float)
    losses = -pnl[pnl < 0].sum()
    return {
        "group": name,
        "trades": len(frame),
        "adds": int(frame["second_leg"].sum()) if len(frame) else 0,
        "win_rate_pct": 100 * (pnl > 0).mean() if len(frame) else np.nan,
        "average_return_pct": ret.mean() if len(frame) else np.nan,
        "median_return_pct": ret.median() if len(frame) else np.nan,
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(ret) if len(frame) else np.nan,
        "weak_30m_exits": int(frame["exit_reason"].astype(str).str.startswith("weak_").sum()) if len(frame) else 0,
        "stop_exits": int(frame["exit_reason"].astype(str).str.startswith("stop").sum()) if len(frame) else 0,
        "first_bar_close_exits": int((frame["exit_reason"] == "first_bar_close").sum()) if len(frame) else 0,
    }


def bin_rows(frame, column):
    rows = []
    for label, part in frame.groupby(column, observed=True, sort=True):
        row = stats(str(label), part)
        row["impulse_min_pct"] = part["impulse_pct"].min()
        row["impulse_max_pct"] = part["impulse_pct"].max()
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    trades = pd.read_excel(TRADES_FILE, sheet_name="Trades")
    trades["signal_time"] = pd.to_datetime(trades["signal_time"])
    entries = pd.read_excel(ENTRY_FILE, sheet_name="Trades")
    entries = entries[entries["variant"] == ENTRY_VARIANT][["movement_id", "impulse_pct"]]
    trades = trades.merge(entries, on="movement_id", how="left").sort_values("signal_time").reset_index(drop=True)
    trades["impulse_bin"] = pd.cut(trades["impulse_pct"], FIXED_BINS, right=False)
    trades["quartile"] = pd.qcut(trades["impulse_pct"], 4, duplicates="drop")

    pearson = trades[["impulse_pct", "return_pct"]].corr(method="pearson").iloc[0, 1]
    spearman = trades[["impulse_pct", "return_pct"]].corr(method="spearman").iloc[0, 1]
    overview = pd.DataFrame([{
        "trades": len(trades),
        "impulse_min_pct": trades["impulse_pct"].min(),
        "impulse_median_pct": trades["impulse_pct"].median(),
        "impulse_mean_pct": trades["impulse_pct"].mean(),
        "impulse_max_pct": trades["impulse_pct"].max(),
        "pearson_impulse_vs_return": pearson,
        "spearman_impulse_vs_return": spearman,
        "conclusion": "No monotonic relationship; result depends on impulse-size zone",
    }])

    full_bins = bin_rows(trades, "impulse_bin")
    train_bins = bin_rows(trades[trades["signal_time"] <= SPLIT], "impulse_bin").add_prefix("train_")
    oos_bins = bin_rows(trades[trades["signal_time"] > SPLIT], "impulse_bin").add_prefix("oos_")
    train_oos = full_bins.merge(train_bins, left_on="group", right_on="train_group", how="left")
    train_oos = train_oos.merge(oos_bins, left_on="group", right_on="oos_group", how="left")
    quartiles = bin_rows(trades, "quartile")

    minimum_rows = []
    for threshold in np.arange(0.7, 1.51, 0.1):
        kept = trades[trades["impulse_pct"] >= threshold]
        train = kept[kept["signal_time"] <= SPLIT]
        oos = kept[kept["signal_time"] > SPLIT]
        full_stat, train_stat, oos_stat = stats("full", kept), stats("train", train), stats("OOS", oos)
        minimum_rows.append({
            "minimum_impulse_pct": round(threshold, 2),
            "trades": full_stat["trades"],
            "average_return_pct": full_stat["average_return_pct"],
            "sum_return_pct": full_stat["sum_return_pct"],
            "profit_factor": full_stat["profit_factor"],
            "max_drawdown_pct": full_stat["max_drawdown_pct"],
            "train_trades": train_stat["trades"],
            "train_average_return_pct": train_stat["average_return_pct"],
            "train_profit_factor": train_stat["profit_factor"],
            "oos_trades": oos_stat["trades"],
            "oos_average_return_pct": oos_stat["average_return_pct"],
            "oos_sum_return_pct": oos_stat["sum_return_pct"],
            "oos_profit_factor": oos_stat["profit_factor"],
        })
    minimum_sweep = pd.DataFrame(minimum_rows)

    cap_rows = []
    for cap in [0.9, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, np.inf]:
        kept = trades[trades["impulse_pct"] < cap]
        train = kept[kept["signal_time"] <= SPLIT]
        oos = kept[kept["signal_time"] > SPLIT]
        full_stat, train_stat, oos_stat = stats("full", kept), stats("train", train), stats("OOS", oos)
        cap_rows.append({
            "maximum_impulse_exclusive_pct": cap,
            "trades": full_stat["trades"],
            "average_return_pct": full_stat["average_return_pct"],
            "sum_return_pct": full_stat["sum_return_pct"],
            "profit_factor": full_stat["profit_factor"],
            "max_drawdown_pct": full_stat["max_drawdown_pct"],
            "train_average_return_pct": train_stat["average_return_pct"],
            "train_profit_factor": train_stat["profit_factor"],
            "oos_trades": oos_stat["trades"],
            "oos_average_return_pct": oos_stat["average_return_pct"],
            "oos_sum_return_pct": oos_stat["sum_return_pct"],
            "oos_profit_factor": oos_stat["profit_factor"],
        })
    cap_sweep = pd.DataFrame(cap_rows)

    candidate_rules = {
        "Baseline": pd.Series(True, index=trades.index),
        "Skip 0.9% <= impulse < 1.0%": ~trades["impulse_pct"].between(0.9, 1.0, inclusive="left"),
        "Skip impulse >= 3.0%": trades["impulse_pct"] < 3.0,
        "Skip both weak zones": (~trades["impulse_pct"].between(0.9, 1.0, inclusive="left")) & (trades["impulse_pct"] < 3.0),
    }
    candidate_rows = []
    for name, keep in candidate_rules.items():
        selected = trades[keep]
        train = selected[selected["signal_time"] <= SPLIT]
        oos = selected[selected["signal_time"] > SPLIT]
        all_stat, train_stat, oos_stat = stats(name, selected), stats(name, train), stats(name, oos)
        candidate_rows.append({
            "rule": name,
            "trades": all_stat["trades"],
            "average_return_pct": all_stat["average_return_pct"],
            "sum_return_pct": all_stat["sum_return_pct"],
            "profit_factor": all_stat["profit_factor"],
            "max_drawdown_pct": all_stat["max_drawdown_pct"],
            "train_trades": train_stat["trades"],
            "train_sum_return_pct": train_stat["sum_return_pct"],
            "train_profit_factor": train_stat["profit_factor"],
            "oos_trades": oos_stat["trades"],
            "oos_sum_return_pct": oos_stat["sum_return_pct"],
            "oos_profit_factor": oos_stat["profit_factor"],
        })
    candidates = pd.DataFrame(candidate_rows)

    method = pd.DataFrame([
        ["Impulse", "initiating first GREEN ST 19/4 movement size used by the final strategy; all included impulses are strictly >0.7%"],
        ["Fixed zones", "0.7–0.8, 0.8–0.9, 0.9–1.0, 1.0–1.2, 1.2–1.5, 1.5–2.0, 2.0–3.0, >=3.0%"],
        ["OOS", f"fixed chronological test starts after {SPLIT}"],
        ["Interpretation", "bin and candidate-rule results are descriptive; small extreme bins must not be promoted to production filters without new data"],
        ["Costs", "final strategy P&L with 1.5% stop, 30-minute check, commission included, no slippage"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        overview.to_excel(writer, sheet_name="Overview", index=False)
        full_bins.to_excel(writer, sheet_name="Fixed_Bins", index=False)
        train_oos.to_excel(writer, sheet_name="Bins_Train_OOS", index=False)
        quartiles.to_excel(writer, sheet_name="Quartiles", index=False)
        minimum_sweep.to_excel(writer, sheet_name="Min_Threshold_Sweep", index=False)
        cap_sweep.to_excel(writer, sheet_name="Max_Cap_Sweep", index=False)
        candidates.to_excel(writer, sheet_name="Candidate_Filters", index=False)
        trades.to_excel(writer, sheet_name="All_Trades", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(overview.to_string(index=False))
    print("\nFIXED BINS\n", full_bins.to_string(index=False))
    print("\nCANDIDATES\n", candidates.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
