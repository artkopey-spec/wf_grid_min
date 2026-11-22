from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from test_short_impulse_volume_exception import FEE, SPLIT, profit_factor, max_drawdown


ROOT = Path.cwd()
SOURCE = ROOT / "mix_short_impulse_volume_test" / "MIX_ST_19_4_RVOL_MIN30_ADD_POLICY_TEST.xlsx"
DATA = ROOT / "mix_minute_data.csv"
ROLLOVERS = ROOT / "mix_rollover_report.csv"
OUT_DIR = ROOT / "mix_next_day_exit_sweep"
OUT_XLSX = OUT_DIR / "MIX_ST_19_4_NEXT_DAY_EXIT_TIME_SWEEP.xlsx"

MOSCOW = timezone(timedelta(hours=3))
TARGETS = [
    ("First next-session minute", "first"),
    ("After 60 trading minutes", "hour60"),
    ("09:01", "0901"),
    ("10:01", "1001"),
    ("12:00", "1200"),
    ("15:00", "1500"),
    ("Next-day close", "close"),
]


def load_market():
    bars = pd.read_csv(DATA, sep=";")
    bars["dt"] = pd.to_datetime(
        bars["<DATE>"].astype(str) + bars["<TIME>"].astype(str).str.zfill(6),
        format="%Y%m%d%H%M%S",
    )
    bars = bars.set_index("dt").sort_index()

    first_by_date = pd.Series(np.arange(len(bars)), index=bars.index.normalize()).groupby(level=0).min()
    first_date, last_date = bars.index[0].date(), bars.index[-1].date()
    roll_events = []
    with ROLLOVERS.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            report = datetime.strptime(row["roll_timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(MOSCOW)
            date = report.date()
            stamp = pd.Timestamp(date)
            if first_date <= date <= last_date and stamp in first_by_date.index:
                roll_events.append((int(first_by_date.loc[stamp]), float(row["offset_after"]) - float(row["offset_before"])))
    delta = np.zeros(len(bars), dtype=float)
    for index, value in roll_events:
        delta[index] += value
    bars["roll_offset"] = np.cumsum(delta)
    return bars


def load_trades(sheet):
    frame = pd.read_excel(SOURCE, sheet_name=sheet)
    for column in ("signal_time", "first_entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column])
    return frame


def choose_target(day, code):
    if code == "first":
        return day.index[0]
    if code == "hour60":
        return day.index[min(59, len(day) - 1)]
    if code == "close":
        return day.index[-1]
    hour, minute = {
        "0901": (9, 0),
        "1001": (10, 0),
        "1200": (11, 59),
        "1500": (14, 59),
    }[code]
    requested = day.index[0].normalize() + pd.Timedelta(hours=hour, minutes=minute)
    eligible = day.index[day.index <= requested]
    return eligible[-1] if len(eligible) else day.index[0]


def adjusted_values(raw, relative_offset):
    return {column: float(raw[column]) + relative_offset for column in ("<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>")}


def entries_for(row):
    entries = [(10, float(row["leg_04_price"]))]
    if bool(row["second_leg"]):
        quantity = int(row["contracts"]) - 10
        entries.append((quantity, float(row["breakout_price"])))
    return entries


def pnl_result(row, exit_time, exit_price, reason):
    entries = entries_for(row)
    quantity = sum(q for q, _ in entries)
    gross = sum(q * (exit_price - price) for q, price in entries)
    commission = FEE * (sum(q * price for q, price in entries) + quantity * exit_price)
    pnl = gross - commission
    return {
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": reason,
        "net_pnl_rub_10": pnl,
        "return_pct": 100 * pnl / (10 * float(row["leg_04_price"])),
    }


def extend_survivor(row, bars, code):
    # Same-day stop and weak-30 exits remain untouched. Only the scheduled
    # next-session close is moved.
    if row["exit_reason"] != "first_bar_close":
        return {
            "exit_time": row["exit_time"],
            "exit_price": row["exit_price"],
            "exit_reason": row["exit_reason"],
            "net_pnl_rub_10": row["net_pnl_rub_10"],
            "return_pct": row["return_pct"],
        }

    next_date = pd.Timestamp(row["exit_time"]).normalize()
    day = bars[bars.index.normalize() == next_date]
    if day.empty:
        raise RuntimeError(f"Missing next session for {row['movement_id']}")
    target = choose_target(day, code)
    path = day.loc[:target]
    entry_offset = float(bars.loc[pd.Timestamp(row["first_entry_time"]), "roll_offset"])
    stop = float(row["weighted_average_entry"]) * 0.985

    for position, (stamp, raw) in enumerate(path.iterrows()):
        relative_offset = float(raw["roll_offset"]) - entry_offset
        bar = adjusted_values(raw, relative_offset)
        if bar["<OPEN>"] <= stop:
            return pnl_result(row, stamp, bar["<OPEN>"], "stop_gap_next_day")
        if bar["<LOW>"] <= stop:
            return pnl_result(row, stamp, stop, "stop_next_day")
        if stamp == target:
            return pnl_result(row, stamp, bar["<CLOSE>"], f"scheduled_{code}")
    raise RuntimeError(f"No exit for {row['movement_id']} {code}")


def strategy_stats(size_variant, target, segment, frame):
    frame = frame.sort_values("signal_time")
    ret = frame["return_pct"].to_numpy(float)
    pnl = frame["net_pnl_rub_10"].to_numpy(float)
    return {
        "size_variant": size_variant,
        "target": target,
        "segment": segment,
        "trades": len(frame),
        "wins": int((ret > 0).sum()),
        "win_rate_pct": 100 * np.mean(ret > 0),
        "average_return_pct": ret.mean(),
        "median_return_pct": np.median(ret),
        "sum_return_points": ret.sum(),
        "profit_factor_rub": profit_factor(pnl),
        "max_drawdown_points": max_drawdown(ret),
        "worst_trade_pct": ret.min(),
        "best_trade_pct": ret.max(),
        "net_pnl_rub": pnl.sum(),
        "next_day_stops": int(frame["exit_reason"].astype(str).str.contains("next_day").sum()),
    }


def next_date_after(stamp, dates):
    position = dates.searchsorted(pd.Timestamp(stamp).normalize(), side="right")
    return dates[position] if position < len(dates) else pd.NaT


def markout_rows(frame, bars, size_variant):
    dates = pd.DatetimeIndex(bars.index.normalize().unique()).sort_values()
    records = []
    for _, row in frame.iterrows():
        next_date = next_date_after(row["first_entry_time"], dates)
        if pd.isna(next_date):
            continue
        day = bars[bars.index.normalize() == next_date]
        entry_stamp = pd.Timestamp(row["first_entry_time"])
        entry_offset = float(bars.loc[entry_stamp, "roll_offset"])
        first_price = float(row["leg_04_price"])
        for label, code in TARGETS:
            target = choose_target(day, code)
            raw = day.loc[target]
            adjusted_close = float(raw["<CLOSE>"]) + float(raw["roll_offset"]) - entry_offset
            markout = 100 * (adjusted_close / first_price - 1)
            records.append({
                "size_variant": size_variant,
                "movement_id": row["movement_id"],
                "signal_time": row["signal_time"],
                "target": label,
                "target_time": target,
                "first_entry_price": first_price,
                "target_close": adjusted_close,
                "markout_pct": markout,
                "positive_close": markout > 0,
            })
    return pd.DataFrame(records)


def hypothesis_stats(size_variant, target, segment, frame):
    values = frame["markout_pct"].to_numpy(float)
    positive = values > 0
    return {
        "size_variant": size_variant,
        "target": target,
        "segment": segment,
        "observations": len(frame),
        "positive_closes": int(positive.sum()),
        "positive_close_rate_pct": 100 * positive.mean(),
        "average_markout_pct": values.mean(),
        "median_markout_pct": np.median(values),
        "worst_markout_pct": values.min(),
        "best_markout_pct": values.max(),
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market()
    all_summary = []
    detail_sheets = []
    validations = []
    all_markouts = []

    for size_variant, sheet in (("Long10+5", "Add5_All_Trades"), ("Long10+10", "Add10_All_Trades")):
        source = load_trades(sheet)
        all_markouts.append(markout_rows(source, bars, size_variant))
        for label, code in TARGETS:
            tested = source.copy()
            for index, row in tested.iterrows():
                result = extend_survivor(row, bars, code)
                for key, value in result.items():
                    tested.at[index, key] = value
            for segment, part in (
                ("Full", tested),
                ("Train", tested[tested["signal_time"] <= SPLIT]),
                ("OOS", tested[tested["signal_time"] > SPLIT]),
            ):
                all_summary.append(strategy_stats(size_variant, label, segment, part))
            if code == "first":
                survivors = source["exit_reason"] == "first_bar_close"
                validations.append({
                    "size_variant": size_variant,
                    "published_survivors": int(survivors.sum()),
                    "exit_time_matches": int((pd.to_datetime(tested.loc[survivors, "exit_time"]) == pd.to_datetime(source.loc[survivors, "exit_time"])).sum()),
                    "exit_price_matches": int(np.isclose(tested.loc[survivors, "exit_price"].astype(float), source.loc[survivors, "exit_price"].astype(float), atol=1e-8).sum()),
                    "pnl_matches_within_1_rub": int((tested.loc[survivors, "net_pnl_rub_10"].astype(float).sub(source.loc[survivors, "net_pnl_rub_10"].astype(float)).abs() < 1).sum()),
                })
            detail_sheets.append((size_variant, code, tested))

    summary = pd.DataFrame(all_summary)
    markouts = pd.concat(all_markouts, ignore_index=True)
    hypothesis_rows = []
    for size_variant in markouts["size_variant"].drop_duplicates():
        size_part = markouts[markouts["size_variant"] == size_variant]
        for target in size_part["target"].drop_duplicates():
            target_part = size_part[size_part["target"] == target]
            for segment, part in (
                ("Full", target_part),
                ("Train", target_part[target_part["signal_time"] <= SPLIT]),
                ("OOS", target_part[target_part["signal_time"] > SPLIT]),
            ):
                hypothesis_rows.append(hypothesis_stats(size_variant, target, segment, part))
    markout_summary = pd.DataFrame(hypothesis_rows)
    method = pd.DataFrame([
        ["Entry/add policy", "Long10 first; second leg only when impulse >30 minutes, RVol <2.922962 and confirmed breakout"],
        ["Preserved exits", "1.5% weighted-average stop and same-day weak-position check after 30 minutes"],
        ["Changed exit", "Only the scheduled close for trades surviving into the next session"],
        ["09:01 interpretation", "Close of the minute bar stamped 09:00"],
        ["10:01 interpretation", "Close of the minute bar stamped 10:00"],
        ["12:00 / 15:00", "Close of bars stamped 11:59 / 14:59"],
        ["After 60 trading minutes", "Close of the 60th available minute bar of the next session"],
        ["Next-day close", "Close of the last available minute bar of the next trading date"],
        ["Stop priority", "Gap/Open, then Low/stop, then scheduled Close"],
        ["Hypothesis markout", "All 124 first entries, no stop censoring; adjusted next-day Close versus first-leg entry price"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Strategy_Summary", index=False)
        markout_summary.to_excel(writer, sheet_name="Hypothesis_Summary", index=False)
        markouts.to_excel(writer, sheet_name="Hypothesis_Details", index=False)
        pd.DataFrame(validations).to_excel(writer, sheet_name="Validation", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)
        for size_variant, code, tested in detail_sheets:
            short_size = "A5" if size_variant == "Long10+5" else "A10"
            tested.to_excel(writer, sheet_name=f"{short_size}_{code}"[:31], index=False)

    print(pd.DataFrame(validations).to_string(index=False))
    print("\nLong10+5 full:")
    print(summary[(summary.size_variant == "Long10+5") & (summary.segment == "Full")].to_string(index=False))
    print("\nLong10+5 OOS:")
    print(summary[(summary.size_variant == "Long10+5") & (summary.segment == "OOS")].to_string(index=False))
    print("\nHypothesis Long10+5:")
    print(markout_summary[(markout_summary.size_variant == "Long10+5") & (markout_summary.segment.isin(["Full", "OOS"]))].to_string(index=False))
    print("\nOutput:", OUT_XLSX)


if __name__ == "__main__":
    main()
