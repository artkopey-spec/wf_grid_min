from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd


WORK = Path.cwd()
ROOT = Path(os.environ["MIX_SOURCE_ROOT"])
DATA_CSV = ROOT / "data.csv"
FINAL_XLSX = WORK / "MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx"
ADD5_XLSX = WORK / "mix_add5_output" / "MIX_ST_19_4_LONG10_ADD5_P60_FINAL_BACKTEST.xlsx"
VOLUME_XLSX = WORK / "MIX_ST_19_4_volume_filter_test.xlsx"
OUT_DIR = WORK / "mix_short_impulse_volume_test"
OUT_XLSX = OUT_DIR / "MIX_ST_19_4_SHORT_IMPULSE_VOLUME_EXCEPTION_TEST.xlsx"

FEE = 0.0000666667
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def profit_factor(values):
    values = np.asarray(values, dtype=float)
    losses = -values[values < 0].sum()
    return float(values[values > 0].sum() / losses) if losses else np.nan


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def load_bars():
    bars = pd.read_csv(DATA_CSV, sep=";")
    bars["dt"] = pd.to_datetime(
        bars["<DATE>"].astype(str) + bars["<TIME>"].astype(str).str.zfill(6),
        format="%Y%m%d%H%M%S",
    )
    return bars.set_index("dt").sort_index()


def load_variant(path, sheet="Trades"):
    frame = pd.read_excel(path, sheet_name=sheet)
    if "breakout_price" not in frame.columns or "leg_04_price" not in frame.columns:
        price_reference = pd.read_excel(ADD5_XLSX, sheet_name="Trades")
        price_reference = price_reference[["movement_id", "leg_04_price", "breakout_price"]]
        frame = frame.merge(price_reference, on="movement_id", how="left", validate="one_to_one")
    for column in ("signal_time", "first_entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column])
    durations = pd.read_excel(VOLUME_XLSX, sheet_name="Impulse_Volume_Standard")
    return frame.merge(
        durations[["movement_id", "start_time", "end_time", "bars"]],
        on="movement_id",
        how="left",
        validate="one_to_one",
    )


def locate_breakout_time(row, bars):
    if pd.isna(row["breakout_price"]):
        return pd.NaT
    window = bars.loc[row["first_entry_time"] : row["exit_time"]]
    hits = window.index[
        np.isclose(window["<OPEN>"].astype(float), float(row["breakout_price"]))
    ]
    return hits[0] if len(hits) else pd.NaT


def adjusted_bar(raw_bar, timestamp, row, bars):
    values = {name: float(raw_bar[name]) for name in ("<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>")}
    # Preserve the source back-adjustment for the one trade spanning a roll.
    if timestamp.normalize() > pd.Timestamp(row["first_entry_time"]).normalize():
        published_exit = pd.Timestamp(row["exit_time"])
        if row["exit_reason"] == "first_bar_close" and published_exit in bars.index:
            offset = float(row["exit_price"]) - float(bars.loc[published_exit, "<CLOSE>"])
            if abs(offset) > 1e-9:
                values = {key: value + offset for key, value in values.items()}
    return values


def replay_add(row, bars, add_qty, breakout_time):
    entry_time = pd.Timestamp(row["first_entry_time"])
    first_price = float(row["leg_04_price"])
    second_price = float(row["breakout_price"])
    average = (10 * first_price + add_qty * second_price) / (10 + add_qty)
    qty = 10 + add_qty
    checked_30m = breakout_time > entry_time + pd.Timedelta(minutes=30)

    future = bars.loc[breakout_time:]
    next_day = future.index.normalize() > entry_time.normalize()
    if not next_day.any():
        raise RuntimeError(f"No next session for movement {row['movement_id']}")
    future = future.loc[: future.index[next_day][0]]

    exit_time = exit_price = exit_reason = None
    for position, (timestamp, raw_bar) in enumerate(future.iterrows()):
        bar = adjusted_bar(raw_bar, timestamp, row, bars)
        stop = average * 0.985
        new_session = timestamp.normalize() > entry_time.normalize()
        if position > 0 and bar["<OPEN>"] <= stop:
            exit_time, exit_price, exit_reason = timestamp, bar["<OPEN>"], "stop_gap"
            break
        if bar["<LOW>"] <= stop:
            exit_time, exit_price, exit_reason = timestamp, stop, "stop"
            break
        if new_session:
            exit_time, exit_price, exit_reason = timestamp, bar["<CLOSE>"], "first_bar_close"
            break
        if not checked_30m and timestamp >= entry_time + pd.Timedelta(minutes=30):
            checked_30m = True
            if bar["<CLOSE>"] <= average:
                exit_time, exit_price, exit_reason = timestamp, bar["<CLOSE>"], "weak_30m"
                break

    if exit_time is None:
        raise RuntimeError(f"No exit for movement {row['movement_id']}")

    entries = [(10, first_price)]
    if add_qty:
        entries.append((add_qty, second_price))
    gross = sum(quantity * (exit_price - price) for quantity, price in entries)
    commission = FEE * (sum(quantity * price for quantity, price in entries) + qty * exit_price)
    pnl = gross - commission
    return {
        "second_leg": bool(add_qty),
        "contracts": qty,
        "weighted_average_entry": average,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "net_pnl_rub_10": pnl,
        "return_pct": 100 * pnl / (10 * first_price),
    }


def stats(variant, segment, frame):
    frame = frame.sort_values("signal_time")
    ret = frame["return_pct"].to_numpy(float)
    pnl = frame["net_pnl_rub_10"].to_numpy(float)
    return {
        "variant": variant,
        "segment": segment,
        "trades": len(frame),
        "adds": int(frame["second_leg"].astype(bool).sum()),
        "win_rate_pct": 100 * np.mean(ret > 0),
        "average_return_pct": ret.mean(),
        "median_return_pct": np.median(ret),
        "sum_return_points": ret.sum(),
        "profit_factor_rub": profit_factor(pnl),
        "closed_trade_max_dd_points": max_drawdown(ret),
        "worst_trade_pct": ret.min(),
        "net_pnl_rub": pnl.sum(),
    }


def apply_exception(baseline, bars, add_qty, cutoff, require_known_rvol):
    frame = baseline.copy()
    frame["short_impulse"] = frame["bars"] < cutoff
    frame["breakout_time_candidate"] = [locate_breakout_time(row, bars) for _, row in frame.iterrows()]
    changed = (
        frame["short_impulse"]
        & ~frame["volume_allows_add"].astype(bool)
        & frame["breakout_time_candidate"].notna()
    )
    if require_known_rvol:
        changed &= frame["impulse_rvol"].notna()
    frame["exception_applied"] = False
    frame["baseline_return_pct"] = frame["return_pct"]
    frame["baseline_pnl_rub"] = frame["net_pnl_rub_10"]
    frame["baseline_exit_reason"] = frame["exit_reason"]
    for index, row in frame[changed].iterrows():
        result = replay_add(
            row,
            bars,
            add_qty=add_qty,
            breakout_time=pd.Timestamp(row["breakout_time_candidate"]),
        )
        for key, value in result.items():
            frame.at[index, key] = value
        frame.at[index, "exception_applied"] = True
    frame["delta_return_points"] = frame["return_pct"] - frame["baseline_return_pct"]
    frame["delta_pnl_rub"] = frame["net_pnl_rub_10"] - frame["baseline_pnl_rub"]
    return frame


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_bars()
    add10 = load_variant(FINAL_XLSX)
    add5 = load_variant(ADD5_XLSX)

    train = add10[add10["signal_time"] <= SPLIT]
    train_median = float(train["bars"].median())
    cutoff = 0.5 * train_median
    mean_cutoff = 0.5 * float(train["bars"].mean())

    duration_summary = pd.DataFrame([
        {
            "population": name,
            "impulses": len(frame),
            "mean_bars": frame["bars"].mean(),
            "median_bars": frame["bars"].median(),
            "half_mean_bars": frame["bars"].mean() / 2,
            "half_median_bars": frame["bars"].median() / 2,
            "min_bars": frame["bars"].min(),
            "p25_bars": frame["bars"].quantile(0.25),
            "p75_bars": frame["bars"].quantile(0.75),
            "max_bars": frame["bars"].max(),
        }
        for name, frame in (
            ("Final full", add10),
            ("Final train", train),
            ("Final OOS", add10[add10["signal_time"] > SPLIT]),
        )
    ])

    rows = []
    variants = []
    for label, baseline, quantity in (
        ("Long10+5", add5, 5),
        ("Long10+10", add10, 10),
    ):
        tested_known = apply_exception(baseline, bars, quantity, cutoff, True)
        tested_all = apply_exception(baseline, bars, quantity, cutoff, False)
        tested_mean_known = apply_exception(baseline, bars, quantity, mean_cutoff, True)
        tested_30_known = apply_exception(baseline, bars, quantity, 30.0, True)
        tested_30_all = apply_exception(baseline, bars, quantity, 30.0, False)
        variants.append((label, baseline, tested_known, tested_all, tested_mean_known, tested_30_known, tested_30_all))
        for suffix, frame in (
            ("Current P60", baseline),
            ("Median short exception · known RVol", tested_known),
            ("Median short exception · including missing RVol", tested_all),
            ("Mean short exception · known RVol", tested_mean_known),
            ("Fixed <30 exception · known RVol", tested_30_known),
            ("Fixed <30 exception · including missing RVol", tested_30_all),
        ):
            for segment, part in (
                ("Full", frame),
                ("Train", frame[frame["signal_time"] <= SPLIT]),
                ("OOS", frame[frame["signal_time"] > SPLIT]),
            ):
                rows.append(stats(f"{label} · {suffix}", segment, part))

    summary = pd.DataFrame(rows)
    changed = pd.concat([
        tested[tested["exception_applied"]].assign(size_variant=label, exception_variant=exception_name)
        for label, _, tested_known, tested_all, tested_mean_known, tested_30_known, tested_30_all in variants
        for exception_name, tested in (
            ("Median known RVol", tested_known),
            ("Median including missing", tested_all),
            ("Mean known RVol", tested_mean_known),
            ("Fixed <30 known RVol", tested_30_known),
            ("Fixed <30 including missing", tested_30_all),
        )
    ], ignore_index=True)
    method = pd.DataFrame([
        ["Frozen length sample", "82 final train trades through 2025-10-15 13:26"],
        ["Train median impulse length", train_median],
        ["Short impulse rule", f"bars < {cutoff:g} (strictly less than 50% of train median)"],
        ["Train mean impulse length", float(train["bars"].mean())],
        ["Mean-based alternative", f"bars < {mean_cutoff:g}"],
        ["Fixed logical alternative", "bars < 30; at bars >= 30 the frozen P60 rule applies"],
        ["Volume exception", "For a short impulse, P60 does not block a confirmed add"],
        ["Unchanged", "Signal, first entry, breakout confirmation, 1.5% stop, 30-minute check, next-session exit and commission"],
        ["Replay", "Only newly enabled additions are replayed minute by minute; all unchanged trades retain published P&L"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        duration_summary.to_excel(writer, sheet_name="Duration_Summary", index=False)
        changed.to_excel(writer, sheet_name="Changed_Trades", index=False)
        variants[0][2].to_excel(writer, sheet_name="Add5_Median_Known", index=False)
        variants[0][3].to_excel(writer, sheet_name="Add5_Median_All", index=False)
        variants[1][2].to_excel(writer, sheet_name="Add10_Median_Known", index=False)
        variants[1][3].to_excel(writer, sheet_name="Add10_Median_All", index=False)
        variants[0][5].to_excel(writer, sheet_name="Add5_30_Known", index=False)
        variants[0][6].to_excel(writer, sheet_name="Add5_30_All", index=False)
        variants[1][5].to_excel(writer, sheet_name="Add10_30_Known", index=False)
        variants[1][6].to_excel(writer, sheet_name="Add10_30_All", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(duration_summary.to_string(index=False))
    print("\nMedian cutoff:", cutoff, "Mean cutoff:", mean_cutoff)
    print("\n", summary.to_string(index=False))
    print("\nChanged unique trades:", changed["movement_id"].nunique())
    if len(changed):
        print(changed[["size_variant", "exception_variant", "movement_id", "signal_time", "bars", "impulse_rvol", "delta_return_points", "delta_pnl_rub"]].to_string(index=False))
    print("\nOutput:", OUT_XLSX)


if __name__ == "__main__":
    main()
