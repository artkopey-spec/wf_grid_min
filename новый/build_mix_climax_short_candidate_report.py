from pathlib import Path

import numpy as np
import pandas as pd


EXIT_FILE = Path("MIX_ST_19_4_climax_SHORT_exit_sweep.xlsx")
GRID_FILE = Path("MIX_ST_19_4_climax_SHORT_time_target_stop_grid.xlsx")
OUTPUT = Path("MIX_ST_19_4_climax_SHORT_below_open_candidate.xlsx")
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(name, frame):
    frame = frame.sort_values("signal_time")
    pnl, ret = frame["net_pnl_rub_10"].astype(float), frame["return_pct"].astype(float)
    losses = -pnl[pnl < 0].sum()
    return {
        "variant": name,
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
    }


def main():
    exits = pd.read_excel(EXIT_FILE, sheet_name="Trades")
    exits["signal_time"] = pd.to_datetime(exits["signal_time"])
    one_hour = exits[exits["exit_rule"] == "1 hour"].copy()
    below = one_hour[~one_hour["passes_above_open"]].copy()
    above = one_hour[one_hour["passes_above_open"]].copy()
    train = below[below["signal_time"] <= SPLIT]
    oos = below[below["signal_time"] > SPLIT]

    comparison = pd.DataFrame([
        stats("All impulses >=2.5%, Short at RED flip, exit 1h", one_hour),
        stats("Impulse ends above day Open", above),
        stats("Impulse ends below/equal day Open — selected", below),
        stats("Selected — train", train),
        stats("Selected — OOS", oos),
    ])

    time_rows = []
    for rule in ["30 minutes", "1 hour", "2 hours", "3 hours", "6 hours", "Next GREEN flip, next-minute Open", "19:00 same date", "Next-session first minute Close"]:
        part = exits[(exits["exit_rule"] == rule) & (~exits["passes_above_open"])]
        time_rows.append(stats(rule, part))
    time_comparison = pd.DataFrame(time_rows)

    grid = pd.read_excel(GRID_FILE, sheet_name="All_Trades")
    grid["signal_time"] = pd.to_datetime(grid["signal_time"])
    optimized = grid[
        (~grid["passes_above_open"])
        & (grid["horizon_minutes"] == 90)
        & (grid["target_pct"] == 1.0)
        & (grid["stop_pct"] == 0.7)
    ].copy()
    controls = pd.DataFrame([
        stats("Simple recommendation: exit after 60 minutes", below),
        stats("Optimized control: target 1%, stop 0.7%, fallback 90m", optimized),
    ])

    method = pd.DataFrame([
        ["Signal", "first GREEN ST 19/4 impulse of the trading day >=2.5%"],
        ["Geometry filter", "impulse-ending Close <= adjusted Open of that trading date; impulses ending above Open are not shorted"],
        ["Flip", "wait for first RED ST minute to close"],
        ["Entry", "Short10 at next-minute Open after the confirmed RED flip"],
        ["Recommended exit", "cover at Close of first available minute at or after 60 elapsed minutes"],
        ["Why not 90m optimization", "target 1% plus 90m fallback adds only 0.05 percentage point across four trades and introduces two extra parameters"],
        ["Stop", "no tested stop improved the simple four-trade result; a 0.7% emergency stop did not trigger in the selected sample and therefore is not statistically validated"],
        ["Do not hold", "2h, 19:00, next session, and reverse GREEN flip are all inferior; the effect is short-lived"],
        ["Costs", "commission included; no slippage; rollover gaps adjusted"],
        ["Status", "research candidate only: four qualifying signals (two train, two OOS) are insufficient for production deployment"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        comparison.to_excel(writer, sheet_name="Summary", index=False)
        time_comparison.to_excel(writer, sheet_name="Exit_Comparison", index=False)
        controls.to_excel(writer, sheet_name="Simple_vs_Optimized", index=False)
        below.to_excel(writer, sheet_name="Four_Selected_Trades", index=False)
        optimized.to_excel(writer, sheet_name="Optimized_Control_Trades", index=False)
        one_hour.to_excel(writer, sheet_name="All_16_One_Hour", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(comparison.to_string(index=False))
    print("\n", time_comparison.to_string(index=False))
    print("\n", controls.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
