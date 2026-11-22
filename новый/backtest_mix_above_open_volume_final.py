from pathlib import Path

import numpy as np
import pandas as pd


OPEN_FILE = Path("MIX_ST_19_4_above_open_final_backtest.xlsx")
VOLUME_FILE = Path("MIX_ST_19_4_volume_filter_test.xlsx")
OUTPUT = Path("MIX_ST_19_4_above_open_volume_add_filter_final_backtest.xlsx")
RULE = "add_only_if_impulse_in_bottom_60pct_train_threshold"
SPLIT = pd.Timestamp("2025-10-15 13:26:00")
SCALE = 0.1


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(frame):
    frame = frame.sort_values("signal_time")
    r = frame["return_pct"].astype(float)
    p = frame["net_pnl_rub_10"].astype(float)
    loss = -p[p < 0].sum()
    return {
        "trades": len(frame),
        "second_legs": int(frame["second_leg"].sum()) if len(frame) else 0,
        "win_rate_pct": (p > 0).mean() * 100 if len(frame) else np.nan,
        "average_return_pct": r.mean() if len(frame) else np.nan,
        "median_return_pct": r.median() if len(frame) else np.nan,
        "sum_return_pct": r.sum(),
        "profit_factor": p[p > 0].sum() / loss if loss else np.nan,
        "max_drawdown_pct": max_drawdown(r) if len(frame) else np.nan,
        "worst_trade_pct": r.min() if len(frame) else np.nan,
        "net_pnl_rub_10": p.sum(),
        "max_drawdown_rub_10": max_drawdown(p) if len(frame) else np.nan,
        "worst_trade_rub_10": p.min() if len(frame) else np.nan,
    }


def main():
    signals = pd.read_excel(OPEN_FILE, sheet_name="Signals")
    signals["end_time"] = pd.to_datetime(signals["end_time"])
    signals = signals[signals["passes_above_open"] == True].copy()

    standards = pd.read_excel(VOLUME_FILE, sheet_name="Impulse_Volume_Standard")
    thresholds = pd.read_excel(VOLUME_FILE, sheet_name="Train_Thresholds")
    p60 = float(thresholds[(thresholds["feature"] == "impulse_rvol") & (thresholds["train_percentile"] == 60)]["threshold"].iloc[0])
    p80 = float(thresholds[(thresholds["feature"] == "impulse_rvol") & (thresholds["train_percentile"] == 80)]["threshold"].iloc[0])
    signals = signals.merge(standards[["movement_id", "impulse_rvol"]], on="movement_id", how="left")

    def band(value):
        if pd.isna(value): return "Нет стандарта объёма — только 10"
        if value <= p60: return "Нижние 60% — 10+10"
        if value <= p80: return "60–80% — только 10"
        return "Верхние 20% — только 10"

    signals["volume_band"] = signals["impulse_rvol"].map(band)
    signals["volume_allows_trade"] = True
    signals["volume_allows_add"] = signals["impulse_rvol"].notna() & (signals["impulse_rvol"] <= p60)

    details = pd.read_excel(VOLUME_FILE, sheet_name="Details")
    details = details[details["rule"] == RULE].copy()
    details["signal_time"] = pd.to_datetime(details["signal_time"])
    details["exit_time"] = pd.to_datetime(details["exit_time"])
    allowed_ids = set(signals["movement_id"])
    details = details[details["movement_id"].isin(allowed_ids)].copy()
    details["return_pct"] = details["return_initial_100_pct"].astype(float)
    details["net_pnl_rub_10"] = details["net_pnl_rub"].astype(float) * SCALE
    details["contracts"] = (details["realized_contracts"].astype(int) * SCALE).astype(int)
    details["second_leg"] = details["realized_contracts"].astype(int) == 200
    details = details.merge(signals[["movement_id", "end_vs_open_pct", "volume_band", "volume_allows_add"]], on="movement_id", how="left")

    overall = stats(details)
    train = stats(details[details["signal_time"] <= SPLIT])
    test = stats(details[details["signal_time"] > SPLIT])
    summary = pd.DataFrame([{
        "strategy": "Above Open + volume controls add only",
        "all_first_impulses": 326,
        "signals_above_open": len(signals),
        "signals_allowed_by_volume": int(signals["volume_allows_trade"].sum()),
        "entry_fill_rate_from_allowed_pct": len(details) / signals["volume_allows_trade"].sum() * 100,
        "p60_rvol_threshold": p60,
        "p80_rvol_threshold": p80,
        **overall,
        "train_trades": train["trades"],
        "train_average_return_pct": train["average_return_pct"],
        "train_sum_return_pct": train["sum_return_pct"],
        "train_profit_factor": train["profit_factor"],
        "test_trades": test["trades"],
        "test_average_return_pct": test["average_return_pct"],
        "test_sum_return_pct": test["sum_return_pct"],
        "test_profit_factor": test["profit_factor"],
    }])

    band_rows = []
    for label, group in signals.groupby("volume_band", sort=False):
        traded = details[details["movement_id"].isin(group["movement_id"])]
        band_rows.append({
            "volume_band": label,
            "signals": len(group),
            "share_above_open_signals_pct": len(group) / len(signals) * 100,
            **stats(traded),
        })

    exit_rows = []
    for reason, group in details.groupby("exit_reason", sort=False):
        exit_rows.append({"exit_reason": reason, **stats(group)})

    year_rows = []
    for year, group in details.groupby(details["signal_time"].dt.year, sort=True):
        year_rows.append({"year": int(year), **stats(group)})

    equity = details.sort_values("signal_time")[["signal_time", "return_pct", "net_pnl_rub_10"]].copy()
    equity["cumulative_return_pct"] = equity["return_pct"].cumsum()
    equity["cumulative_pnl_rub_10"] = equity["net_pnl_rub_10"].cumsum()

    method = pd.DataFrame([
        ["Signal", "first GREEN ST 19/4 impulse >0.7% per day; impulse final close > adjusted day open"],
        ["Volume standard", "impulse actual volume / median expected volume for same minute-of-day; thresholds frozen on chronological train"],
        ["Bottom 60%", f"rvol <= {p60:.6f}: first Long10 and confirmed add +10 allowed"],
        ["Above P60", f"rvol > {p60:.6f}: only first Long10; no add"],
        ["Missing volume standard", "first Long10 is allowed; no add"],
        ["Entry", "Long10 at -0.4% correction from impulse high"],
        ["Add", "minute Close > impulse high; buy at next minute Open"],
        ["Risk", "2% stop from weighted average; at 30 minutes exit all if Close <= weighted average"],
        ["Final exit", "next trading-day first available Open"],
        ["Costs", "commission included; no slippage; roll gaps adjusted; two wrong-contract bars excluded"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(band_rows).to_excel(writer, sheet_name="Volume_Bands", index=False)
        pd.DataFrame(exit_rows).to_excel(writer, sheet_name="Exit_Reasons", index=False)
        pd.DataFrame(year_rows).to_excel(writer, sheet_name="Yearly", index=False)
        signals.to_excel(writer, sheet_name="Signals_Above_Open", index=False)
        details.to_excel(writer, sheet_name="Trades", index=False)
        equity.to_excel(writer, sheet_name="Equity", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(summary.to_string(index=False))
    print(pd.DataFrame(band_rows).to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
