from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path("MIX_ST_19_4_final_entry_before15_first_bar_close_time_exit_sweep.xlsx")
CANDIDATE_SOURCE = Path("MIX_ST_19_4_above_open_volume_add_filter_final_backtest.xlsx")
ENTRY_SOURCE = Path("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx")
OUTPUT = Path("MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx")
SPLIT = pd.Timestamp("2025-10-15 13:26:00")
ENTRY_VARIANT = "100_at_0.4_plus_100_after_close_above_peak"


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(name, frame):
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
        "adds": int(frame["second_leg"].sum()) if len(frame) else 0,
        "weak_30m_exits": int(frame["exit_reason"].str.startswith("weak_").sum()) if len(frame) else 0,
        "stop_exits": int(frame["exit_reason"].str.startswith("stop").sum()) if len(frame) else 0,
        "first_bar_close_exits": int((frame["exit_reason"] == "first_bar_close").sum()) if len(frame) else 0,
        "win_rate_pct": 100 * (p > 0).mean() if len(frame) else np.nan,
        "average_return_pct": r.mean() if len(frame) else np.nan,
        "median_return_pct": r.median() if len(frame) else np.nan,
        "sum_return_pct": r.sum(),
        "profit_factor": p[p > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(r) if len(frame) else np.nan,
        "worst_trade_pct": r.min() if len(frame) else np.nan,
        "best_trade_pct": r.max() if len(frame) else np.nan,
        "net_pnl_rub_10": p.sum(),
        "max_drawdown_rub_10": max_drawdown(p) if len(frame) else np.nan,
        "test_trades": len(test),
        "test_win_rate_pct": 100 * (tp > 0).mean() if len(test) else np.nan,
        "test_average_return_pct": test["return_pct"].mean() if len(test) else np.nan,
        "test_sum_return_pct": test["return_pct"].sum(),
        "test_profit_factor": tp[tp > 0].sum() / tlosses if tlosses else np.nan,
        "test_max_drawdown_pct": max_drawdown(test["return_pct"]) if len(test) else np.nan,
        "test_net_pnl_rub_10": tp.sum(),
    }


def main():
    trades = pd.read_excel(SOURCE, sheet_name="Details")
    trades = trades[trades["variant"] == "30 минут"].copy()
    for col in ["signal_time", "first_entry_time", "exit_time"]:
        trades[col] = pd.to_datetime(trades[col])

    before_15 = trades["first_entry_time"].dt.hour < 15
    kept = trades[before_15].sort_values("signal_time").reset_index(drop=True)
    # Build separate audits for the new upper impulse cap and the first-entry time rule.
    candidates = pd.read_excel(CANDIDATE_SOURCE, sheet_name="Trades")
    candidates["signal_time"] = pd.to_datetime(candidates["signal_time"])
    entry_times = pd.read_excel(ENTRY_SOURCE, sheet_name="Trades")
    entry_times = entry_times[entry_times["variant"] == ENTRY_VARIANT].copy()
    entry_times["leg_04_time"] = pd.to_datetime(entry_times["leg_04_time"])
    audited = candidates.merge(
        entry_times[["movement_id", "leg_04_time", "leg_04_price"]],
        on="movement_id", how="left",
    ).rename(columns={"leg_04_time": "first_entry_time", "leg_04_price": "first_entry_price"})
    excluded_cap = audited[audited["impulse_pct"] >= 2.5].copy()
    excluded_cap["exclusion_reason"] = "initiating impulse >= 2.5% (climax cap)"
    excluded_cap = excluded_cap.sort_values("impulse_pct", ascending=False).reset_index(drop=True)
    cap_eligible = audited[audited["impulse_pct"] < 2.5].copy()
    excluded = cap_eligible[cap_eligible["first_entry_time"].dt.hour >= 15].copy()
    excluded["exclusion_reason"] = "first Long10 filled at 15:00 or later"
    excluded = excluded.sort_values("first_entry_time").reset_index(drop=True)

    summary = pd.DataFrame([stats("Финальная стратегия: Long10 до 15:00 + проверка 30 минут", kept)])
    summary["candidate_trades_before_cap_and_time_rules"] = len(candidates)
    summary["excluded_impulse_2_5_or_higher"] = len(excluded_cap)
    summary["candidates_after_impulse_cap"] = len(cap_eligible)
    summary["excluded_first_entries_15_or_later"] = len(excluded)

    exit_rows = []
    for reason, group in kept.groupby("exit_reason", sort=False):
        exit_rows.append({"exit_reason": reason, **stats(reason, group)})

    year_rows = []
    for year, group in kept.groupby(kept["signal_time"].dt.year, sort=True):
        year_rows.append({"year": int(year), **stats(str(year), group)})

    equity = kept[["signal_time", "first_entry_time", "return_pct", "net_pnl_rub_10"]].copy()
    equity["cumulative_return_pct"] = equity["return_pct"].cumsum()
    equity["cumulative_pnl_rub_10"] = equity["net_pnl_rub_10"].cumsum()
    equity["drawdown_pct"] = equity["cumulative_return_pct"] - equity["cumulative_return_pct"].cummax().clip(lower=0)
    equity["drawdown_rub_10"] = equity["cumulative_pnl_rub_10"] - equity["cumulative_pnl_rub_10"].cummax().clip(lower=0)

    method = pd.DataFrame([
        ["Instrument", "MIX minute bars, ST 19/4"],
        ["Signal", "first GREEN ST impulse of the day, strictly >0.7% and <2.5%; impulse final Close > adjusted day Open"],
        ["Climax cap", "initiating impulse >=2.5% is skipped; the rounded boundary is supported by a negative tail on both train and OOS"],
        ["First entry", "Long10 at -0.4% correction from initiating impulse high; fill must occur strictly before 15:00"],
        ["Time rule", "only first-entry time is checked; no separate impulse-end cutoff; an entry at 15:00 or later is skipped"],
        ["Filter audit", f"{len(candidates)} filled candidates before cap/time rules; {len(excluded_cap)} impulses >=2.5% excluded; then {len(excluded)} first entries at 15:00 or later excluded; {len(kept)} final trades"],
        ["Add", "+10 at next-minute Open after minute Close > impulse high; allowed only when impulse RVol <= frozen train P60 2.922962"],
        ["Late add", "an add after 15:00 is allowed because it manages a position already opened before 15:00"],
        ["Stop", "1.5% below current weighted-average price; recalculated after add; gap filled at minute Open"],
        ["30-minute check", "at the first available minute Close at least 30 elapsed minutes after first Long10: exit all if Close <= current weighted-average entry; otherwise continue"],
        ["Final exit", "Close of the first available minute bar of the next trading session if neither stop nor 30-minute check has closed the position"],
        ["First new-session bar", "the 1.5% stop remains active at its Open and against its Low before the Close exit"],
        ["Costs", "commission 0.00666667% per side included; no slippage"],
        ["Data cleaning", "rollover gaps adjusted; two isolated wrong-contract bars removed"],
        ["Percent convention", "returns normalized to initial Long10 notional; after add to 20 contracts, a 1.5% stop can approach -3%"],
        ["OOS split", f"fixed chronological test starts after {SPLIT}"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        kept.to_excel(writer, sheet_name="Trades", index=False)
        excluded_cap.to_excel(writer, sheet_name="Excluded_Impulse_Cap", index=False)
        excluded.to_excel(writer, sheet_name="Excluded_Late_Entries", index=False)
        pd.DataFrame(exit_rows).to_excel(writer, sheet_name="Exit_Reasons", index=False)
        pd.DataFrame(year_rows).to_excel(writer, sheet_name="Yearly", index=False)
        equity.to_excel(writer, sheet_name="Equity", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(summary.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
