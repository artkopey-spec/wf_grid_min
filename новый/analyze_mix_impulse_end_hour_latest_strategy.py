from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path("MIX_ST_19_4_above_open_final_backtest.xlsx")
OUTPUT = Path("MIX_ST_19_4_impulse_end_hour_no_volume_analysis.xlsx")
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(signals, trades, label, order):
    trades = trades.sort_values("signal_time")
    r = trades["return_pct"].astype(float)
    p = trades["net_pnl_rub_10"].astype(float)
    loss = -p[p < 0].sum()
    test = trades[trades["signal_time"] > SPLIT]
    tr = trades[trades["signal_time"] <= SPLIT]
    tp = test["net_pnl_rub_10"].astype(float)
    tloss = -tp[tp < 0].sum()
    return {
        "time_group": label,
        "sort_order": order,
        "qualifying_impulses": len(signals),
        "trades": len(trades),
        "fill_rate_pct": len(trades) / len(signals) * 100 if len(signals) else np.nan,
        "adds": int(trades["second_leg"].sum()) if len(trades) else 0,
        "win_rate_pct": (p > 0).mean() * 100 if len(trades) else np.nan,
        "average_return_pct": r.mean() if len(trades) else np.nan,
        "median_return_pct": r.median() if len(trades) else np.nan,
        "sum_return_pct": r.sum(),
        "profit_factor": p[p > 0].sum() / loss if loss else np.nan,
        "max_drawdown_pct": max_drawdown(r) if len(trades) else np.nan,
        "worst_trade_pct": r.min() if len(trades) else np.nan,
        "net_pnl_rub_10": p.sum(),
        "train_trades": len(tr),
        "train_average_return_pct": tr["return_pct"].mean() if len(tr) else np.nan,
        "test_trades": len(test),
        "test_average_return_pct": test["return_pct"].mean() if len(test) else np.nan,
        "test_sum_return_pct": test["return_pct"].sum(),
        "test_profit_factor": tp[tp > 0].sum() / tloss if tloss else np.nan,
    }


def main():
    signals = pd.read_excel(SOURCE, sheet_name="Signals")
    signals = signals[signals["passes_above_open"] == True].copy()
    trades = pd.read_excel(SOURCE, sheet_name="Trades")
    signals["end_time"] = pd.to_datetime(signals["end_time"])
    if "signal_time" not in trades.columns:
        trades["signal_time"] = trades["end_time"]
    trades["signal_time"] = pd.to_datetime(trades["signal_time"])
    signals["end_hour"] = signals["end_time"].dt.hour
    trades["end_hour"] = trades["signal_time"].dt.hour

    hourly = []
    for hour in sorted(signals["end_hour"].unique()):
        label = f"{hour:02d}:00–{hour:02d}:59"
        hourly.append(stats(
            signals[signals["end_hour"] == hour],
            trades[trades["end_hour"] == hour],
            label,
            int(hour),
        ))
    hourly = pd.DataFrame(hourly)
    hourly["rank_by_average_return"] = hourly["average_return_pct"].rank(ascending=False, method="min").astype("Int64")
    hourly["rank_by_profit_factor"] = hourly["profit_factor"].rank(ascending=False, method="min").astype("Int64")
    hourly["reliability"] = np.select(
        [hourly["trades"] >= 20, hourly["trades"] >= 10, hourly["trades"] >= 5],
        ["достаточная", "средняя", "низкая"],
        default="очень низкая",
    )

    windows = [
        ("До 09:00", 0, 9, 1),
        ("09:00–12:59", 9, 13, 2),
        ("13:00–15:59", 13, 16, 3),
        ("16:00–18:59", 16, 19, 4),
        ("19:00 и позже", 19, 24, 5),
    ]
    broad = []
    for label, lo, hi, order in windows:
        broad.append(stats(
            signals[(signals["end_hour"] >= lo) & (signals["end_hour"] < hi)],
            trades[(trades["end_hour"] >= lo) & (trades["end_hour"] < hi)],
            label,
            order,
        ))
    broad = pd.DataFrame(broad)
    broad["rank_by_average_return"] = broad["average_return_pct"].rank(ascending=False, method="min").astype("Int64")
    broad["rank_by_profit_factor"] = broad["profit_factor"].rank(ascending=False, method="min").astype("Int64")

    detail = trades.sort_values("signal_time").copy()
    detail["hour_group"] = detail["signal_time"].dt.strftime("%H:00–%H:59")

    method = pd.DataFrame([
        ["Strategy", "first GREEN ST 19/4 impulse >0.7%; final impulse Close > day Open; Long10 at -0.4%; add +10 after minute Close > impulse high; no volume filter"],
        ["Grouping", "by Moscow exchange timestamp hour containing the final minute of the initiating impulse"],
        ["Signals", "all 234 impulses above day open; profitability calculated on 185 signals that reached the -0.4% first entry"],
        ["Costs", "commission included; roll gaps adjusted; two wrong-contract bars excluded; no slippage"],
        ["Reliability", ">=20 trades sufficient; 10-19 medium; 5-9 low; <5 very low"],
        ["OOS", f"chronological final segment strictly after {SPLIT}"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        hourly.sort_values("sort_order").to_excel(writer, sheet_name="Hourly", index=False)
        hourly.sort_values(["rank_by_average_return", "trades"], ascending=[True, False]).to_excel(writer, sheet_name="Hourly_Ranked", index=False)
        broad.to_excel(writer, sheet_name="Time_Windows", index=False)
        detail.to_excel(writer, sheet_name="Trade_Details", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print("HOURLY")
    print(hourly.to_string(index=False))
    print("WINDOWS")
    print(broad.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
