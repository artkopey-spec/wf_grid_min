from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path("MIX_ST_19_4_impulse_size_result_analysis.xlsx")
OUTPUT = Path("MIX_ST_19_4_impulse_upper_boundary_analysis.xlsx")
SPLIT = pd.Timestamp("2025-10-15 13:26:00")
SELECTED_CAP = 2.5


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(frame):
    frame = frame.sort_values("signal_time")
    pnl = frame["net_pnl_rub_10"].astype(float)
    ret = frame["return_pct"].astype(float)
    losses = -pnl[pnl < 0].sum()
    return {
        "trades": len(frame),
        "wins": int((pnl > 0).sum()),
        "win_rate_pct": 100 * (pnl > 0).mean() if len(frame) else np.nan,
        "average_return_pct": ret.mean() if len(frame) else np.nan,
        "median_return_pct": ret.median() if len(frame) else np.nan,
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(ret) if len(frame) else np.nan,
    }


def main():
    trades = pd.read_excel(SOURCE, sheet_name="All_Trades")
    trades["signal_time"] = pd.to_datetime(trades["signal_time"])
    trades = trades.sort_values("signal_time").reset_index(drop=True)
    train_mask = trades["signal_time"] <= SPLIT

    sweep_rows = []
    for threshold in np.arange(1.5, 4.01, 0.1):
        threshold = round(float(threshold), 2)
        tail = trades[trades["impulse_pct"] >= threshold]
        tail_train = tail[tail["signal_time"] <= SPLIT]
        tail_oos = tail[tail["signal_time"] > SPLIT]
        kept = trades[trades["impulse_pct"] < threshold]
        kept_train = kept[kept["signal_time"] <= SPLIT]
        kept_oos = kept[kept["signal_time"] > SPLIT]
        a, b, c = stats(tail), stats(tail_train), stats(tail_oos)
        ka, kb, kc = stats(kept), stats(kept_train), stats(kept_oos)
        sweep_rows.append({
            "upper_boundary_pct": threshold,
            "tail_trades": a["trades"],
            "tail_wins": a["wins"],
            "tail_average_return_pct": a["average_return_pct"],
            "tail_sum_return_pct": a["sum_return_pct"],
            "tail_profit_factor": a["profit_factor"],
            "tail_train_trades": b["trades"],
            "tail_train_sum_return_pct": b["sum_return_pct"],
            "tail_train_profit_factor": b["profit_factor"],
            "tail_oos_trades": c["trades"],
            "tail_oos_sum_return_pct": c["sum_return_pct"],
            "tail_oos_profit_factor": c["profit_factor"],
            "kept_trades": ka["trades"],
            "kept_average_return_pct": ka["average_return_pct"],
            "kept_sum_return_pct": ka["sum_return_pct"],
            "kept_profit_factor": ka["profit_factor"],
            "kept_max_drawdown_pct": ka["max_drawdown_pct"],
            "kept_train_sum_return_pct": kb["sum_return_pct"],
            "kept_train_profit_factor": kb["profit_factor"],
            "kept_oos_sum_return_pct": kc["sum_return_pct"],
            "kept_oos_profit_factor": kc["profit_factor"],
        })
    sweep = pd.DataFrame(sweep_rows)
    sweep["tail_negative_full_train_oos"] = (
        (sweep["tail_sum_return_pct"] < 0)
        & (sweep["tail_train_sum_return_pct"] < 0)
        & (sweep["tail_oos_sum_return_pct"] < 0)
        & (sweep["tail_train_trades"] >= 3)
        & (sweep["tail_oos_trades"] >= 3)
    )
    sweep["selected"] = sweep["upper_boundary_pct"].eq(SELECTED_CAP)

    kept = trades[trades["impulse_pct"] < SELECTED_CAP].copy()
    excluded = trades[trades["impulse_pct"] >= SELECTED_CAP].copy()
    comparison = pd.DataFrame([
        {"variant": "Baseline", **stats(trades)},
        {"variant": f"Skip impulse >= {SELECTED_CAP}%", **stats(kept)},
        {"variant": f"Excluded tail >= {SELECTED_CAP}%", **stats(excluded)},
        {"variant": "Baseline train", **stats(trades[train_mask])},
        {"variant": f"Filtered train < {SELECTED_CAP}%", **stats(kept[kept["signal_time"] <= SPLIT])},
        {"variant": "Baseline OOS", **stats(trades[~train_mask])},
        {"variant": f"Filtered OOS < {SELECTED_CAP}%", **stats(kept[kept["signal_time"] > SPLIT])},
    ])

    observed = trades.sort_values("impulse_pct")[[
        "movement_id", "signal_time", "impulse_pct", "return_pct", "exit_reason", "second_leg"
    ]]
    method = pd.DataFrame([
        ["Question", "find an upper initiating-impulse boundary above which Long trades become loss-making"],
        ["Tail test", "for every 0.1% boundary from 1.5% to 4.0%, evaluate all trades at or above the boundary"],
        ["Robustness rule", "tail must be negative on full, train, and OOS samples, with at least three tail trades in both train and OOS"],
        ["Observed data gap", "there are no initiating impulses between 2.219% and 2.730%; therefore the exact transition cannot be estimated inside that interval"],
        ["Practical boundary", "2.5% is the rounded midpoint of the observed transition gap and produces the same excluded set for any threshold above 2.219% and at or below 2.730%"],
        ["Production warning", "only eight trades are above the selected cap; treat the cap as a candidate until validated on new data"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        comparison.to_excel(writer, sheet_name="Comparison", index=False)
        sweep.to_excel(writer, sheet_name="Boundary_Sweep", index=False)
        excluded.to_excel(writer, sheet_name="Excluded_Above_2.5", index=False)
        kept.to_excel(writer, sheet_name="Kept_Below_2.5", index=False)
        observed.to_excel(writer, sheet_name="All_Sorted_By_Impulse", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(comparison.to_string(index=False))
    print("\nCandidate neighborhood\n", sweep[sweep["upper_boundary_pct"].between(2.0, 3.1)].to_string(index=False))
    print("\nExcluded\n", excluded[["signal_time", "impulse_pct", "return_pct", "exit_reason"]].sort_values("impulse_pct").to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
