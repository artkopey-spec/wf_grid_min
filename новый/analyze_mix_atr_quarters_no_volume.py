from pathlib import Path

import numpy as np
import pandas as pd


ATR_FILE = Path("MIX_ST_19_4_impulse_daily_ATR_stage_analysis.xlsx")
STRATEGY_FILE = Path("MIX_ST_19_4_volume_filter_test.xlsx")
OUTPUT = Path("MIX_ST_19_4_impulse_end_ATR_quarters_no_volume.xlsx")
CUTOFF = pd.Timestamp("2025-10-15 13:26:00")


ZONE_ORDER = [
    "0_ниже_-1_ATR",
    "1_-1_до_-0.5_ATR",
    "2_-0.5_до_0_ATR",
    "3_0_до_+0.5_ATR",
    "4_+0.5_до_+1_ATR",
    "5_выше_+1_ATR",
]


def zone(value):
    if pd.isna(value):
        return "нет_ATR"
    if value < -1:
        return ZONE_ORDER[0]
    if value < -0.5:
        return ZONE_ORDER[1]
    if value < 0:
        return ZONE_ORDER[2]
    if value < 0.5:
        return ZONE_ORDER[3]
    if value <= 1:
        return ZONE_ORDER[4]
    return ZONE_ORDER[5]


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def summarize(frame, label):
    signals = len(frame)
    trades = frame[frame["return_pct"].notna()].sort_values("signal_time")
    r = trades["return_pct"].astype(float)
    p = trades["pnl_rub_10"].astype(float)
    losses = -p[p < 0].sum()
    oos = trades[trades["signal_time"] >= CUTOFF]
    op = oos["pnl_rub_10"].astype(float)
    oloss = -op[op < 0].sum()
    return {
        "ATR_end_zone": label,
        "signals": signals,
        "share_of_all_signals_pct": signals / TOTAL_SIGNALS * 100,
        "strategy_trades_no_volume": len(trades),
        "entry_rate_pct": len(trades) / signals * 100 if signals else np.nan,
        "win_rate_pct": (r > 0).mean() * 100 if len(r) else np.nan,
        "average_return_pct": r.mean() if len(r) else np.nan,
        "median_return_pct": r.median() if len(r) else np.nan,
        "sum_return_pct": r.sum(),
        "profit_factor": p[p > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(r) if len(r) else np.nan,
        "net_pnl_rub_10": p.sum(),
        "oos_trades": len(oos),
        "oos_average_return_pct": oos["return_pct"].mean() if len(oos) else np.nan,
        "oos_sum_return_pct": oos["return_pct"].sum(),
        "oos_profit_factor": op[op > 0].sum() / oloss if oloss else np.nan,
    }


features = pd.read_excel(ATR_FILE, sheet_name="Signal_Features")
details = pd.read_excel(STRATEGY_FILE, sheet_name="Details")
base = details[details["rule"] == "baseline_all_adds"].copy()
base = base[["movement_id", "signal_time", "exit_time", "exit_reason", "realized_contracts", "net_pnl_rub", "return_initial_100_pct"]]
base["signal_time"] = pd.to_datetime(base["signal_time"])
base["exit_time"] = pd.to_datetime(base["exit_time"])
base = base.rename(columns={"net_pnl_rub": "pnl_rub_100", "return_initial_100_pct": "return_pct"})
base["pnl_rub_10"] = base["pnl_rub_100"] * 0.1

data = features.merge(base, on="movement_id", how="left", suffixes=("", "_strategy"))
data["signal_time"] = pd.to_datetime(data["impulse_end_time"])
data["ATR_end_zone"] = data["end_atr"].map(zone)
global TOTAL_SIGNALS
TOTAL_SIGNALS = len(data)

summary = pd.DataFrame([summarize(data[data["ATR_end_zone"] == label], label) for label in ZONE_ORDER])

# Main four quarters only, for a clean ranking by strategy quality.
main = summary.iloc[1:5].copy()
main["rank_by_average_return"] = main["average_return_pct"].rank(ascending=False, method="min").astype("Int64")
main["rank_by_profit_factor"] = main["profit_factor"].rank(ascending=False, method="min").astype("Int64")
summary = summary.merge(main[["ATR_end_zone", "rank_by_average_return", "rank_by_profit_factor"]], on="ATR_end_zone", how="left")


def filter_stats(name, frame):
    trades = frame[frame["return_pct"].notna()].sort_values("signal_time")
    r = trades["return_pct"].astype(float)
    p = trades["pnl_rub_10"].astype(float)
    loss = -p[p < 0].sum()
    oos = trades[trades["signal_time"] >= CUTOFF]
    op = oos["pnl_rub_10"].astype(float)
    oloss = -op[op < 0].sum()
    return {
        "filter": name,
        "trades": len(trades),
        "average_return_pct": r.mean(),
        "sum_return_pct": r.sum(),
        "profit_factor": p[p > 0].sum() / loss if loss else np.nan,
        "max_drawdown_pct": max_drawdown(r),
        "net_pnl_rub_10": p.sum(),
        "oos_trades": len(oos),
        "oos_average_return_pct": oos["return_pct"].mean(),
        "oos_sum_return_pct": oos["return_pct"].sum(),
        "oos_profit_factor": op[op > 0].sum() / oloss if oloss else np.nan,
    }


tradeable = data[data["return_pct"].notna()].copy()
filter_summary = pd.DataFrame([
    filter_stats("Все окончания", tradeable),
    filter_stats("Исключить только -0.5..0 ATR", tradeable[~((tradeable["end_atr"] >= -0.5) & (tradeable["end_atr"] < 0))]),
    filter_stats("Только окончание >= 0 ATR", tradeable[tradeable["end_atr"] >= 0]),
    filter_stats("Только окончание 0..+1 ATR", tradeable[(tradeable["end_atr"] >= 0) & (tradeable["end_atr"] <= 1)]),
    filter_stats("Только окончание +0.5..+1 ATR", tradeable[(tradeable["end_atr"] >= 0.5) & (tradeable["end_atr"] <= 1)]),
])

method = pd.DataFrame([
    ["Сигнал", "Первый зеленый импульс ST 19/4 > 0.7% за день"],
    ["Координата окончания", "(roll-adjusted цена окончания импульса - roll-adjusted открытие дня) / ATR(14)"],
    ["ATR", "Wilder ATR(14), рассчитан только по завершенным предыдущим дням; известен на открытии"],
    ["Основные зоны", "[-1;-0.5), [-0.5;0), [0;+0.5), [+0.5;+1] ATR; выходы за +/-1 ATR отдельно"],
    ["Стратегия", "Без фильтра объема; прежние вход -0.4%, добавление на закрытии минуты выше high, стоп/30 минут/следующее открытие"],
    ["Размер", "10 контрактов + еще 10 при добавлении"],
    ["OOS", f"Сигналы с {CUTOFF}"],
], columns=["item", "definition"])

with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
    summary.to_excel(writer, sheet_name="ATR_Quarter_Summary", index=False)
    filter_summary.to_excel(writer, sheet_name="Filter_Strategy_Summary", index=False)
    data.sort_values("signal_time").to_excel(writer, sheet_name="All_Impulse_Details", index=False)
    method.to_excel(writer, sheet_name="Method", index=False)

print(summary.to_string(index=False))
print("signals", TOTAL_SIGNALS, "trades matched", int(data["return_pct"].notna().sum()))
print("saved", OUTPUT.resolve())
