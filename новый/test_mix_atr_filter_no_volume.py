from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path("MIX_ST_19_4_volume_filter_test.xlsx")
ATR_SOURCE = Path("MIX_ST_19_4_impulse_daily_ATR_stage_analysis.xlsx")
OUTPUT = Path("MIX_ST_19_4_ATR_filter_no_volume_test.xlsx")
CUTOFF = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.cumsum(np.asarray(values, dtype=float))
    curve = np.r_[0.0, curve]
    peaks = np.maximum.accumulate(curve)
    return float(np.min(curve - peaks))


def stats(name, trades, total_opportunities, removed):
    trades = trades.sort_values("signal_time")
    returns = trades["return_initial_100_pct"].astype(float)
    pnl10 = trades["net_pnl_rub"].astype(float) * 0.1
    positive = pnl10[pnl10 > 0].sum()
    negative = -pnl10[pnl10 < 0].sum()
    train = trades[trades["signal_time"] < CUTOFF]
    test = trades[trades["signal_time"] >= CUTOFF]

    def subset(part):
        p = part["net_pnl_rub"].astype(float) * 0.1
        r = part["return_initial_100_pct"].astype(float)
        losses = -p[p < 0].sum()
        return len(part), p.sum(), r.mean() if len(part) else np.nan, p[p > 0].sum() / losses if losses else np.nan

    tr_n, tr_pnl, tr_avg, tr_pf = subset(train)
    te_n, te_pnl, te_avg, te_pf = subset(test)
    return {
        "variant": name,
        "opportunities": total_opportunities,
        "trades": len(trades),
        "removed_trades": removed,
        "participation_pct": len(trades) / total_opportunities * 100,
        "second_legs": int((trades["realized_contracts"] > 100).sum()),
        "win_rate_pct": (returns > 0).mean() * 100,
        "average_return_pct": returns.mean(),
        "median_return_pct": returns.median(),
        "sum_return_pct": returns.sum(),
        "profit_factor": positive / negative if negative else np.nan,
        "max_drawdown_pct": max_drawdown(returns),
        "worst_trade_pct": returns.min(),
        "net_pnl_rub_10": pnl10.sum(),
        "max_drawdown_rub_10": max_drawdown(pnl10),
        "worst_trade_rub_10": pnl10.min(),
        "train_trades": tr_n,
        "train_net_pnl_rub_10": tr_pnl,
        "train_average_return_pct": tr_avg,
        "train_profit_factor": tr_pf,
        "test_trades": te_n,
        "test_net_pnl_rub_10": te_pnl,
        "test_average_return_pct": te_avg,
        "test_profit_factor": te_pf,
    }


def main():
    details = pd.read_excel(SOURCE, sheet_name="Details")
    base = details[details["rule"] == "baseline_all_adds"].copy()
    base["signal_time"] = pd.to_datetime(base["signal_time"])
    features = pd.read_excel(ATR_SOURCE, sheet_name="Signal_Features")
    columns = ["movement_id", "same_day_start", "pre_low_atr", "start_atr", "end_atr"]
    base = base.merge(features[columns], on="movement_id", how="left")
    valid = base["same_day_start"].fillna(False).astype(bool)

    filters = {
        "Без ATR-фильтра": pd.Series(False, index=base.index),
        "Пропуск: минимум <= -0.50 ATR и конец < открытия": valid & (base["pre_low_atr"] <= -0.50) & (base["end_atr"] < 0),
        "Пропуск: минимум <= -0.75 ATR и конец < открытия": valid & (base["pre_low_atr"] <= -0.75) & (base["end_atr"] < 0),
        "Пропуск: минимум <= -1.00 ATR и конец < открытия": valid & (base["pre_low_atr"] <= -1.00) & (base["end_atr"] < 0),
        "Пропуск: любой импульс закончен ниже открытия": valid & (base["end_atr"] < 0),
    }

    summary = []
    flags = base[["rule", "movement_id", "signal_time", "pre_low_atr", "start_atr", "end_atr", "net_pnl_rub", "return_initial_100_pct"]].copy()
    for name, skip in filters.items():
        kept = base[~skip].copy()
        summary.append(stats(name, kept, len(base), int(skip.sum())))
        flags[name] = np.where(skip, "SKIP", "TRADE")

    summary = pd.DataFrame(summary)
    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        flags.to_excel(writer, sheet_name="Trade_Flags", index=False)
        pd.DataFrame([
            ["Базовая стратегия", "Без фильтра объема; первая покупка после коррекции -0.4%; добавление после закрытия минуты выше максимума импульса; прежние стоп/30 минут/следующее открытие"],
            ["Рабочий размер", "10 контрактов на первой ноге + 10 контрактов при добавлении"],
            ["ATR", "Wilder ATR(14), только завершенные предыдущие дни, roll-adjusted"],
            ["Новый фильтр", "Пропустить сделку, если минимум до импульса <= -0.75 ATR и импульс завершился ниже открытия дня"],
            ["OOS cutoff", str(CUTOFF)],
        ], columns=["item", "value"]).to_excel(writer, sheet_name="Method", index=False)
    print(summary.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
