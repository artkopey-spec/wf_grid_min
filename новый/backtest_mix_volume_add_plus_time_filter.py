from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path("MIX_ST_19_4_above_open_volume_add_filter_final_backtest.xlsx")
OUTPUT = Path("MIX_ST_19_4_volume_add_plus_time_filter_15_19.xlsx")
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(name, frame):
    frame = frame.sort_values("signal_time")
    r = frame["return_pct"].astype(float)
    p = frame["net_pnl_rub_10"].astype(float)
    loss = -p[p < 0].sum()
    test = frame[frame["signal_time"] > SPLIT]
    tp = test["net_pnl_rub_10"].astype(float)
    tloss = -tp[tp < 0].sum()
    return {
        "variant": name,
        "trades": len(frame),
        "adds": int(frame["second_leg"].sum()),
        "win_rate_pct": (p > 0).mean() * 100,
        "average_return_pct": r.mean(),
        "median_return_pct": r.median(),
        "sum_return_pct": r.sum(),
        "profit_factor": p[p > 0].sum() / loss,
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
    trades = pd.read_excel(SOURCE, sheet_name="Trades")
    trades["signal_time"] = pd.to_datetime(trades["signal_time"])
    trades["end_hour"] = trades["signal_time"].dt.hour
    excluded = trades[(trades["end_hour"] >= 15) & (trades["end_hour"] < 19)].copy()
    kept = trades[~((trades["end_hour"] >= 15) & (trades["end_hour"] < 19))].copy()

    summary = pd.DataFrame([
        stats("Только объёмный фильтр добавления", trades),
        stats("Объёмный + пропуск 15:00–18:59", kept),
        stats("Исключённая группа 15:00–18:59", excluded),
    ])

    equity = kept.sort_values("signal_time")[["signal_time", "return_pct", "net_pnl_rub_10"]].copy()
    equity["cumulative_return_pct"] = equity["return_pct"].cumsum()
    equity["cumulative_pnl_rub_10"] = equity["net_pnl_rub_10"].cumsum()

    exit_rows = []
    for reason, group in kept.groupby("exit_reason", sort=False):
        exit_rows.append({"exit_reason": reason, **stats(reason, group)})

    year_rows = []
    for year, group in kept.groupby(kept["signal_time"].dt.year, sort=True):
        year_rows.append({"year": int(year), **stats(str(year), group)})

    method = pd.DataFrame([
        ["Base strategy", "first GREEN ST 19/4 >0.7%; impulse Close > day Open; Long10 at -0.4% correction"],
        ["Volume", "first Long10 is never blocked; add +10 only if impulse relative volume <= frozen train P60 = 2.922962"],
        ["Time", "skip complete trade when initiating impulse ends from 15:00:00 through 18:59:59"],
        ["Risk", "2% weighted-average stop; 30-minute weak exit; otherwise next trading Open"],
        ["Costs", "commission included; no slippage; roll gaps adjusted; two wrong-contract bars excluded"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        kept.sort_values("signal_time").to_excel(writer, sheet_name="Kept_Trades", index=False)
        excluded.sort_values("signal_time").to_excel(writer, sheet_name="Excluded_15_19", index=False)
        pd.DataFrame(exit_rows).to_excel(writer, sheet_name="Exit_Reasons", index=False)
        pd.DataFrame(year_rows).to_excel(writer, sheet_name="Yearly", index=False)
        equity.to_excel(writer, sheet_name="Equity", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(summary.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
