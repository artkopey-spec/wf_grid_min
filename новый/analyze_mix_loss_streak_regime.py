from pathlib import Path

import numpy as np
import pandas as pd

from analyze_mix_st19 import read_bars, read_rollovers


TRADES_FILE = Path("MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx")
OUTPUT = Path("MIX_ST_19_4_eight_loss_streak_market_regime.xlsx")
STREAK_START = pd.Timestamp("2024-07-22")
STREAK_END = pd.Timestamp("2024-09-19 23:59:59")


def adx(frame, period=14):
    high, low, close = frame["high"], frame["low"], frame["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return plus_di, minus_di, dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def stats(frame):
    pnl = frame["net_pnl_rub_10"].astype(float)
    ret = frame["return_pct"].astype(float)
    loss = -pnl[pnl < 0].sum()
    return {
        "trades": len(frame),
        "win_rate_pct": 100 * (pnl > 0).mean() if len(frame) else np.nan,
        "average_return_pct": ret.mean() if len(frame) else np.nan,
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / loss if loss else np.nan,
        "max_drawdown_pct": max_drawdown(ret) if len(frame) else np.nan,
    }


def main():
    raw = read_bars()
    rolls = read_rollovers(raw)
    rollover_dates = {r["effective_time"].date() for r in rolls if r["offset_delta"] != 0}
    bad = {
        current["index"]
        for previous, current, following in zip(raw, raw[1:], raw[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }

    offset = 0.0
    cursor = 0
    records = []
    for bar in raw:
        while cursor < len(rolls) and rolls[cursor]["effective_index"] == bar["index"]:
            offset += rolls[cursor]["offset_delta"]
            cursor += 1
        if bar["index"] in bad:
            continue
        records.append({
            "time": pd.Timestamp(bar["time"]),
            "open": bar["open"] + offset,
            "high": bar["high"] + offset,
            "low": bar["low"] + offset,
            "close": bar["close"] + offset,
        })
    minute = pd.DataFrame(records).set_index("time").sort_index()

    daily = minute.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    for n in [20, 50, 100, 200]:
        daily[f"sma{n}"] = daily["close"].rolling(n).mean()
    daily["sma20_slope5_pct"] = (daily["sma20"] / daily["sma20"].shift(5) - 1) * 100
    daily["sma50_slope10_pct"] = (daily["sma50"] / daily["sma50"].shift(10) - 1) * 100
    for n in [5, 10, 20]:
        daily[f"roc{n}_pct"] = (daily["close"] / daily["close"].shift(n) - 1) * 100
    daily["acceleration_5d_pp"] = daily["roc5_pct"] - (daily["close"].shift(5) / daily["close"].shift(10) - 1) * 100
    daily["plus_di14"], daily["minus_di14"], daily["adx14"] = adx(daily)

    hourly = minute.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    for n in [20, 50, 100]:
        hourly[f"sma{n}h"] = hourly["close"].rolling(n).mean()
    hourly["sma20h_slope5_pct"] = (hourly["sma20h"] / hourly["sma20h"].shift(5) - 1) * 100
    hourly["roc20h_pct"] = (hourly["close"] / hourly["close"].shift(20) - 1) * 100

    trades = pd.read_excel(TRADES_FILE, sheet_name="Trades").sort_values("signal_time").reset_index(drop=True)
    trades["signal_time"] = pd.to_datetime(trades["signal_time"])
    feature_rows = []
    for _, trade in trades.iterrows():
        signal = trade["signal_time"]
        signal_price = minute.loc[:signal, "close"].iloc[-1]
        prior_daily = daily[daily.index.date < signal.date()].iloc[-1]
        prior_hour = hourly[hourly.index + pd.Timedelta(hours=1) <= signal].iloc[-1]
        row = trade.to_dict()
        row.update({
            "adjusted_signal_price": signal_price,
            "prior_day": prior_daily.name,
            "prior_day_close": prior_daily["close"],
            "price_vs_sma20_pct": (signal_price / prior_daily["sma20"] - 1) * 100,
            "price_vs_sma50_pct": (signal_price / prior_daily["sma50"] - 1) * 100,
            "price_vs_sma100_pct": (signal_price / prior_daily["sma100"] - 1) * 100,
            "price_vs_sma200_pct": (signal_price / prior_daily["sma200"] - 1) * 100,
            "sma20_slope5_pct": prior_daily["sma20_slope5_pct"],
            "sma50_slope10_pct": prior_daily["sma50_slope10_pct"],
            "roc5_pct": prior_daily["roc5_pct"],
            "roc10_pct": prior_daily["roc10_pct"],
            "roc20_pct": prior_daily["roc20_pct"],
            "acceleration_5d_pp": prior_daily["acceleration_5d_pp"],
            "adx14": prior_daily["adx14"],
            "plus_di14": prior_daily["plus_di14"],
            "minus_di14": prior_daily["minus_di14"],
            "hour_close": prior_hour["close"],
            "hour_price_vs_sma20_pct": (signal_price / prior_hour["sma20h"] - 1) * 100,
            "hour_price_vs_sma50_pct": (signal_price / prior_hour["sma50h"] - 1) * 100,
            "hour_price_vs_sma100_pct": (signal_price / prior_hour["sma100h"] - 1) * 100,
            "hour_sma20_slope5_pct": prior_hour["sma20h_slope5_pct"],
            "hour_roc20_pct": prior_hour["roc20h_pct"],
        })
        feature_rows.append(row)
    featured = pd.DataFrame(feature_rows)
    featured["below_daily_sma20"] = featured["price_vs_sma20_pct"] < 0
    featured["below_daily_sma50"] = featured["price_vs_sma50_pct"] < 0
    featured["daily_sma20_falling"] = featured["sma20_slope5_pct"] < 0
    featured["daily_bearish_stack"] = (
        (featured["price_vs_sma20_pct"] < 0)
        & ((featured["prior_day_close"] * 0 + 1).notna())
    )
    # Recreate the stack directly from price distances: price < SMA20 and SMA20 < SMA50.
    featured["daily_bearish_stack"] = (
        (featured["price_vs_sma20_pct"] < 0)
        & ((featured["adjusted_signal_price"] / (1 + featured["price_vs_sma20_pct"] / 100))
           < (featured["adjusted_signal_price"] / (1 + featured["price_vs_sma50_pct"] / 100)))
    )
    featured["below_hour_sma20"] = featured["hour_price_vs_sma20_pct"] < 0
    featured["hour_sma20_falling"] = featured["hour_sma20_slope5_pct"] < 0
    featured["in_eight_loss_streak"] = featured["signal_time"].between(STREAK_START, STREAK_END)

    streak = featured[featured["in_eight_loss_streak"]].copy()
    market_start = minute.loc[:streak["signal_time"].min(), "close"].iloc[-1]
    market_end = minute.loc[:streak["signal_time"].max(), "close"].iloc[-1]
    period = minute.loc[streak["signal_time"].min():streak["signal_time"].max()]
    period_summary = pd.DataFrame([{
        "first_signal": streak["signal_time"].min(),
        "last_signal": streak["signal_time"].max(),
        "signals": len(streak),
        "market_change_first_to_last_signal_pct": (market_end / market_start - 1) * 100,
        "market_max_drawdown_pct": ((period["close"] / period["close"].cummax() - 1) * 100).min(),
        "streak_sum_return_pct": streak["return_pct"].sum(),
        "below_daily_sma20_count": int(streak["below_daily_sma20"].sum()),
        "below_daily_sma50_count": int(streak["below_daily_sma50"].sum()),
        "falling_daily_sma20_count": int(streak["daily_sma20_falling"].sum()),
        "daily_bearish_stack_count": int(streak["daily_bearish_stack"].sum()),
        "below_hour_sma20_count": int(streak["below_hour_sma20"].sum()),
        "falling_hour_sma20_count": int(streak["hour_sma20_falling"].sum()),
        "negative_acceleration_count": int((streak["acceleration_5d_pp"] < 0).sum()),
        "minus_di_above_plus_di_count": int((streak["minus_di14"] > streak["plus_di14"]).sum()),
    }])

    conditions = {
        "No additional regime filter": pd.Series(False, index=featured.index),
        "Skip below daily SMA20": featured["below_daily_sma20"],
        "Skip below daily SMA50": featured["below_daily_sma50"],
        "Skip below SMA20 with falling SMA20": featured["below_daily_sma20"] & featured["daily_sma20_falling"],
        "Skip daily bearish stack": featured["daily_bearish_stack"],
        "Skip negative daily ROC20": featured["roc20_pct"] < 0,
        "Skip below falling hourly SMA20": featured["below_hour_sma20"] & featured["hour_sma20_falling"],
        "Skip confirmed daily downtrend ADX>25": (
            (featured["price_vs_sma50_pct"] < 0)
            & featured["daily_sma20_falling"]
            & (featured["minus_di14"] > featured["plus_di14"])
            & (featured["adx14"] > 25)
        ),
        "Skip confirmed daily downtrend ADX>30": (
            (featured["price_vs_sma50_pct"] < 0)
            & featured["daily_sma20_falling"]
            & (featured["minus_di14"] > featured["plus_di14"])
            & (featured["adx14"] > 30)
        ),
        "Skip ROC20<-2%, below SMA50, ADX>25": (
            (featured["roc20_pct"] < -2)
            & (featured["price_vs_sma50_pct"] < 0)
            & (featured["minus_di14"] > featured["plus_di14"])
            & (featured["adx14"] > 25)
        ),
    }
    comparison_rows = []
    for name, adverse in conditions.items():
        kept = featured[~adverse].copy()
        comparison_rows.append({
            "rule": name,
            "skipped_trades": int(adverse.sum()),
            "skipped_streak_trades": int((adverse & featured["in_eight_loss_streak"]).sum()),
            **stats(kept),
        })
    comparison = pd.DataFrame(comparison_rows)

    method = pd.DataFrame([
        ["Price series", "continuous MIX minute series adjusted for 12 rollover gaps; two isolated wrong-contract bars removed"],
        ["Daily indicators", "calculated only from completed calendar trading days before each signal; no future data"],
        ["Hourly indicators", "calculated from completed 60-minute bars before each signal; no future data"],
        ["Acceleration", "latest 5-day return minus the preceding 5-day return, percentage points"],
        ["Filter comparison", "descriptive sensitivity test on the same 132 final trades; not a newly approved production filter"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        period_summary.to_excel(writer, sheet_name="Period_Summary", index=False)
        streak.to_excel(writer, sheet_name="Eight_Loss_Trades", index=False)
        comparison.to_excel(writer, sheet_name="Filter_Comparison", index=False)
        featured.to_excel(writer, sheet_name="All_Trade_Features", index=False)
        daily.reset_index().to_excel(writer, sheet_name="Daily_Indicators", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(period_summary.to_string(index=False))
    print(streak[[
        "signal_time", "return_pct", "price_vs_sma20_pct", "price_vs_sma50_pct",
        "sma20_slope5_pct", "roc20_pct", "acceleration_5d_pp", "adx14",
        "minus_di14", "plus_di14", "hour_price_vs_sma20_pct", "hour_sma20_slope5_pct",
    ]].to_string(index=False))
    print(comparison.to_string(index=False))
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
