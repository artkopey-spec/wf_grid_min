import csv
from collections import defaultdict, deque
from itertools import accumulate
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from analyze_mix_st19 import OHLC_FILE, read_bars, read_rollovers
from backtest_short_after_down_impulse import write_table


SOURCE = Path("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx")
MOVEMENTS = Path("MIX_ST_19_4_analysis.xlsx")
OUTPUT = Path("MIX_ST_19_4_volume_filter_test.xlsx")
VARIANT = "100_at_0.4_plus_100_after_close_above_peak"
COMMISSION = 0.0000666667
STOP_PCT = 2.0
WEAK_EXIT_HOURS = 0.5


def max_drawdown(values):
    peak = 0.0
    worst = 0.0
    for value in accumulate(values):
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return worst


def profit_factor(values):
    wins = sum(x for x in values if x > 0)
    losses = abs(sum(x for x in values if x < 0))
    return wins / losses if losses else None


def load_source_trades():
    wb = load_workbook(SOURCE, read_only=True, data_only=True)
    rows = wb["Trades"].iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    return [
        {name: row[i] for name, i in col.items()}
        for row in rows if row[col["variant"]] == VARIANT
    ]


def load_movement_starts():
    wb = load_workbook(MOVEMENTS, read_only=True, data_only=True)
    rows = wb["Movements_Sorted"].iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    records = [
        {
            "movement_id": int(row[col["movement_id"]]),
            "st_color": row[col["st_color"]],
            "start_time": row[col["start_time"]],
            "end_time": row[col["end_time"]],
            "movement_pct": float(row[col["movement_pct"]]),
            "bars_to_color_change": int(row[col["bars_to_color_change"]]),
        }
        for row in rows
    ]
    starts = {row["movement_id"]: row["start_time"] for row in records}
    qualifying = sorted(
        (row for row in records if row["st_color"] == "GREEN" and row["movement_pct"] > 0.7),
        key=lambda row: row["end_time"],
    )
    first_by_day = {}
    for row in qualifying:
        first_by_day.setdefault(row["end_time"].date(), row)
    return starts, list(first_by_day.values())


def load_volumes(bars):
    volumes = []
    with OHLC_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for bar, row in zip(bars, csv.DictReader(handle, delimiter=";")):
            stamp = row["<DATE>"] + row["<TIME>"]
            if stamp != bar["time"].strftime("%Y%m%d%H%M%S"):
                raise ValueError(f"OHLC/volume alignment mismatch at {bar['index']}")
            volumes.append(float(row["<VOL>"]))
    if len(volumes) != len(bars):
        raise ValueError("OHLC/volume row count mismatch")
    return volumes


def rolling_expected_volume(bars, volumes):
    history = defaultdict(lambda: deque(maxlen=60))
    expected = [None] * len(bars)
    for bar, volume in zip(bars, volumes):
        key = (bar["time"].hour, bar["time"].minute)
        prior = history[key]
        if len(prior) >= 10:
            expected[bar["index"]] = median(prior)
        prior.append(volume)
    return expected


def summarize(rule, results, total_opportunities, split_time):
    pnl = [r["net_pnl_rub"] for r in results]
    returns = [r["return_initial_100_pct"] for r in results]
    wins = [x for x in pnl if x > 0]
    train = [r for r in results if r["signal_time"] <= split_time]
    test = [r for r in results if r["signal_time"] > split_time]

    def segment(rows):
        values = [r["net_pnl_rub"] for r in rows]
        return {
            "trades": len(rows),
            "net": sum(values),
            "pf": profit_factor(values),
            "avg": fmean(values) if values else None,
        }

    tr = segment(train)
    te = segment(test)
    return {
        "rule": rule,
        "opportunities": total_opportunities,
        "trades": len(results),
        "participation_pct": len(results) / total_opportunities * 100,
        "realized_second_legs": sum(r["realized_contracts"] == 200 for r in results),
        "win_rate_pct": len(wins) / len(results) * 100 if results else None,
        "net_pnl_rub": sum(pnl),
        "net_per_original_opportunity_rub": sum(pnl) / total_opportunities,
        "cumulative_return_initial_100_pct": sum(returns),
        "average_trade_rub": fmean(pnl) if pnl else None,
        "profit_factor": profit_factor(pnl),
        "max_drawdown_rub": max_drawdown(pnl),
        "max_drawdown_initial_100_pct": max_drawdown(returns),
        "worst_trade_rub": min(pnl) if pnl else None,
        "worst_trade_initial_100_pct": min(returns) if returns else None,
        "train_trades": tr["trades"],
        "train_net_pnl_rub": tr["net"],
        "train_pf": tr["pf"],
        "test_trades": te["trades"],
        "test_net_pnl_rub": te["net"],
        "test_pf": te["pf"],
        "test_average_trade_rub": te["avg"],
    }


def main():
    bars = read_bars()
    rolls = read_rollovers(bars)
    volumes = load_volumes(bars)
    expected = rolling_expected_volume(bars, volumes)
    source_trades = load_source_trades()
    movement_starts, all_qualifying_impulses = load_movement_starts()

    offset = 0.0
    rc = 0
    by_time = {}
    for bar in bars:
        while rc < len(rolls) and rolls[rc]["effective_index"] == bar["index"]:
            offset += rolls[rc]["offset_delta"]
            rc += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar

    rollover_dates = {r["effective_time"].date() for r in rolls if r["offset_delta"] != 0}
    bad_roll_bar_indices = {
        current["index"]
        for previous, current, following in zip(bars, bars[1:], bars[2:])
        if current["time"].date() in rollover_dates
        and abs(previous["close"] / following["close"] - 1) < 0.005
        and abs(current["close"] / ((previous["close"] + following["close"]) / 2) - 1) > 0.02
    }

    def window_rvol(start_index, end_index):
        indices = [i for i in range(start_index, end_index + 1) if i not in bad_roll_bar_indices and expected[i]]
        full_count = max(0, end_index - start_index + 1)
        if not indices or len(indices) < max(1, int(full_count * 0.8)):
            return None
        return sum(volumes[i] for i in indices) / sum(expected[i] for i in indices)

    impulse_standard_rows = []
    for impulse in all_qualifying_impulses:
        start_bar = by_time[impulse["start_time"]]
        end_bar = by_time[impulse["end_time"]]
        indices = [
            i for i in range(start_bar["index"] + 1, end_bar["index"] + 1)
            if i not in bad_roll_bar_indices
        ]
        valid_expected = [i for i in indices if expected[i]]
        rvol = window_rvol(start_bar["index"] + 1, end_bar["index"])
        hour = end_bar["time"].hour
        session = "09-13" if hour < 13 else ("13-19" if hour < 19 else "19+")
        impulse_standard_rows.append({
            "movement_id": impulse["movement_id"],
            "start_time": impulse["start_time"],
            "end_time": impulse["end_time"],
            "movement_pct": impulse["movement_pct"],
            "bars": len(indices),
            "total_volume_contracts": sum(volumes[i] for i in indices),
            "average_volume_per_bar": fmean(volumes[i] for i in indices) if indices else None,
            "expected_volume_sum": sum(expected[i] for i in valid_expected) if valid_expected else None,
            "impulse_rvol": rvol,
            "session": session,
        })

    feature_rows = []
    features = {}
    for trade in source_trades:
        movement_id = int(trade["movement_id"])
        start_bar = by_time[movement_starts[movement_id]]
        signal_bar = by_time[trade["signal_time"]]
        first_bar = by_time[trade["leg_04_time"]]
        add_bar = by_time.get(trade["breakout_time"]) if trade["breakout_time"] else None
        impulse_rvol = window_rvol(start_bar["index"] + 1, signal_bar["index"])
        correction_rvol = None
        # Measure the RED correction through its trough before the confirmed
        # breakout, rather than only until the -0.4% fill (which is often on
        # the signal bar itself).
        if add_bar is not None and add_bar["index"] - 1 > signal_bar["index"]:
            correction_candidates = [
                b for b in bars[signal_bar["index"] + 1:add_bar["index"]]
                if b["index"] not in bad_roll_bar_indices
            ]
            if correction_candidates:
                trough_bar = min(correction_candidates, key=lambda b: b["low"] + b["offset"])
                correction_rvol = window_rvol(signal_bar["index"] + 1, trough_bar["index"])
        breakout_rvol = None
        if add_bar is not None and add_bar["index"] > 0:
            confirmation_index = add_bar["index"] - 1
            if confirmation_index not in bad_roll_bar_indices and expected[confirmation_index]:
                breakout_rvol = volumes[confirmation_index] / expected[confirmation_index]
        correction_vs_impulse = correction_rvol / impulse_rvol if correction_rvol and impulse_rvol else None
        breakout_vs_correction = breakout_rvol / correction_rvol if breakout_rvol and correction_rvol else None
        row = {
            "movement_id": movement_id,
            "signal_time": trade["signal_time"],
            "impulse_pct": trade["impulse_pct"],
            "impulse_rvol": impulse_rvol,
            "correction_rvol": correction_rvol,
            "correction_vs_impulse": correction_vs_impulse,
            "breakout_rvol": breakout_rvol,
            "breakout_vs_correction": breakout_vs_correction,
        }
        features[movement_id] = row
        feature_rows.append(row)

    def evaluate(trade, allow_add=True):
        signal_bar = by_time[trade["signal_time"]]
        first_bar = by_time[trade["leg_04_time"]]
        scheduled_exit = by_time[trade["exit_time"]]
        add_bar = by_time.get(trade["breakout_time"]) if allow_add and trade["breakout_time"] else None
        anchor_offset = signal_bar["offset"]

        def px(bar, field):
            return bar[field] + bar["offset"] - anchor_offset

        fills = [(float(trade["leg_04_price"]), 100)]
        qty = 100
        average = fills[0][0]
        target_ts = first_bar["time"].timestamp() + WEAK_EXIT_HOURS * 3600
        checkpoint_done = False
        exit_price = px(scheduled_exit, "open")
        exit_time = scheduled_exit["time"]
        reason = "next_open"

        for bar in bars[first_bar["index"] + 1:scheduled_exit["index"]]:
            if bar["index"] in bad_roll_bar_indices:
                continue
            op = px(bar, "open")
            stop = average * (1 - STOP_PCT / 100)
            if op <= stop:
                exit_price, exit_time, reason = op, bar["time"], "stop_gap"
                break
            if add_bar is not None and bar["index"] == add_bar["index"]:
                fills.append((float(trade["breakout_price"]), 100))
                qty = 200
                average = sum(price * amount for price, amount in fills) / qty
            stop = average * (1 - STOP_PCT / 100)
            if px(bar, "low") <= stop:
                exit_price, exit_time, reason = stop, bar["time"], "stop"
                break
            if not checkpoint_done and bar["time"].timestamp() >= target_ts:
                checkpoint_done = True
                close = px(bar, "close")
                if close <= average:
                    exit_price, exit_time, reason = close, bar["time"], "weak_30m"
                    break

        gross = sum((exit_price - price) * amount for price, amount in fills)
        costs = sum(price * amount * COMMISSION for price, amount in fills) + exit_price * qty * COMMISSION
        net = gross - costs
        return {
            "movement_id": int(trade["movement_id"]),
            "signal_time": trade["signal_time"],
            "realized_contracts": qty,
            "exit_time": exit_time,
            "exit_reason": reason,
            "net_pnl_rub": net,
            "return_initial_100_pct": net / (fills[0][0] * 100) * 100,
        }

    def available(value):
        return value is not None

    ordered = sorted(source_trades, key=lambda x: x["signal_time"])
    split_index = int(len(ordered) * 0.70) - 1
    split_time = ordered[split_index]["signal_time"]

    def percentile(values, probability):
        values = sorted(values)
        position = (len(values) - 1) * probability
        lo = int(position)
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)

    train_features = [row for row in feature_rows if row["signal_time"] <= split_time]
    train_impulse_standard = [row for row in impulse_standard_rows if row["end_time"] <= split_time]
    percentile_thresholds = []
    thresholds_by_feature = {}
    for field in ("impulse_rvol", "breakout_rvol", "correction_vs_impulse"):
        source_rows = train_impulse_standard if field == "impulse_rvol" else train_features
        values = [row[field] for row in source_rows if row[field] is not None]
        thresholds_by_feature[field] = {}
        for p in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            value = percentile(values, p)
            thresholds_by_feature[field][p] = value
            percentile_thresholds.append({
                "feature": field, "train_percentile": p * 100,
                "threshold": value, "train_observations": len(values),
                "train_end": split_time,
            })

    impulse_standard_summary = []
    for segment, segment_rows in (
        ("all", impulse_standard_rows),
        ("train_through_split", train_impulse_standard),
    ):
        for session in ("ALL", "09-13", "13-19", "19+"):
            group = segment_rows if session == "ALL" else [r for r in segment_rows if r["session"] == session]
            values = sorted(r["impulse_rvol"] for r in group if r["impulse_rvol"] is not None)
            if not values:
                continue
            impulse_standard_summary.append({
                "segment": segment,
                "session": session,
                "impulses": len(group),
                "valid_rvol": len(values),
                "mean_rvol": fmean(values),
                "p10": percentile(values, 0.1),
                "p20": percentile(values, 0.2),
                "p40": percentile(values, 0.4),
                "median_p50": percentile(values, 0.5),
                "p60": percentile(values, 0.6),
                "p80": percentile(values, 0.8),
                "p90": percentile(values, 0.9),
            })

    variants = [("baseline_all_adds", lambda f: True, lambda f: True)]
    variants.append(("base_100_never_add", lambda f: True, lambda f: False))
    for threshold in (0.8, 1.0, 1.2, 1.5, 2.0):
        variants.append((f"trade_if_impulse_rvol_ge_{threshold:g}",
                         lambda f, t=threshold: available(f["impulse_rvol"]) and f["impulse_rvol"] >= t,
                         lambda f: True))
        variants.append((f"add_if_breakout_rvol_ge_{threshold:g}",
                         lambda f: True,
                         lambda f, t=threshold: available(f["breakout_rvol"]) and f["breakout_rvol"] >= t))
    for threshold in (0.5, 0.75, 1.0, 1.25):
        variants.append((f"add_if_correction_vs_impulse_le_{threshold:g}",
                         lambda f: True,
                         lambda f, t=threshold: available(f["correction_vs_impulse"]) and f["correction_vs_impulse"] <= t))
    for impulse_threshold in (1.0, 1.2, 1.5):
        for breakout_threshold in (1.0, 1.2, 1.5):
            variants.append((f"trade_impulse_ge_{impulse_threshold:g}_add_breakout_ge_{breakout_threshold:g}",
                             lambda f, t=impulse_threshold: available(f["impulse_rvol"]) and f["impulse_rvol"] >= t,
                             lambda f, t=breakout_threshold: available(f["breakout_rvol"]) and f["breakout_rvol"] >= t))
    for impulse_threshold in (1.0, 1.2):
        for breakout_threshold in (1.0, 1.2):
            variants.append((f"volume_structure_imp_{impulse_threshold:g}_corr_le_1_break_{breakout_threshold:g}",
                             lambda f, t=impulse_threshold: available(f["impulse_rvol"]) and f["impulse_rvol"] >= t,
                             lambda f, t=breakout_threshold: available(f["correction_vs_impulse"])
                             and f["correction_vs_impulse"] <= 1.0
                             and available(f["breakout_rvol"]) and f["breakout_rvol"] >= t))

    # Percentile filters are calibrated on the chronological training segment
    # only, then frozen for the later 30% test segment.
    for top_share in (0.5, 0.4, 0.3, 0.2, 0.1):
        p = 1 - top_share
        impulse_threshold = thresholds_by_feature["impulse_rvol"][p]
        breakout_threshold = thresholds_by_feature["breakout_rvol"][p]
        label = int(top_share * 100)
        variants.append((f"trade_top_{label}pct_impulse_volume_train_threshold",
                         lambda f, t=impulse_threshold: available(f["impulse_rvol"]) and f["impulse_rvol"] >= t,
                         lambda f: True))
        variants.append((f"add_top_{label}pct_breakout_volume_train_threshold",
                         lambda f: True,
                         lambda f, t=breakout_threshold: available(f["breakout_rvol"]) and f["breakout_rvol"] >= t))
    for bottom_share in (0.5, 0.4, 0.3):
        p = bottom_share
        correction_threshold = thresholds_by_feature["correction_vs_impulse"][p]
        label = int(bottom_share * 100)
        variants.append((f"add_bottom_{label}pct_correction_volume_ratio_train_threshold",
                         lambda f: True,
                         lambda f, t=correction_threshold: available(f["correction_vs_impulse"])
                         and f["correction_vs_impulse"] <= t))

    for bottom_share in (0.4, 0.6, 0.8):
        threshold = thresholds_by_feature["impulse_rvol"][bottom_share]
        label = int(bottom_share * 100)
        variants.append((f"trade_bottom_{label}pct_impulse_volume_train_threshold",
                         lambda f, t=threshold: available(f["impulse_rvol"]) and f["impulse_rvol"] <= t,
                         lambda f: True))
        variants.append((f"add_only_if_impulse_in_bottom_{label}pct_train_threshold",
                         lambda f: True,
                         lambda f, t=threshold: available(f["impulse_rvol"]) and f["impulse_rvol"] <= t))
    for top_share in (0.5, 0.4, 0.2):
        threshold = thresholds_by_feature["impulse_rvol"][1 - top_share]
        label = int(top_share * 100)
        variants.append((f"add_only_if_impulse_in_top_{label}pct_train_threshold",
                         lambda f: True,
                         lambda f, t=threshold: available(f["impulse_rvol"]) and f["impulse_rvol"] >= t))
    for bottom_share in (0.6, 0.8, 0.9):
        threshold = thresholds_by_feature["breakout_rvol"][bottom_share]
        label = int(bottom_share * 100)
        variants.append((f"add_bottom_{label}pct_breakout_volume_train_threshold",
                         lambda f: True,
                         lambda f, t=threshold: available(f["breakout_rvol"]) and f["breakout_rvol"] <= t))
    for low_p, high_p in ((0.2, 0.8), (0.3, 0.7)):
        low = thresholds_by_feature["breakout_rvol"][low_p]
        high = thresholds_by_feature["breakout_rvol"][high_p]
        label = int((high_p - low_p) * 100)
        variants.append((f"add_middle_{label}pct_breakout_volume_train_band",
                         lambda f: True,
                         lambda f, lo=low, hi=high: available(f["breakout_rvol"]) and lo <= f["breakout_rvol"] <= hi))

    imp50 = thresholds_by_feature["impulse_rvol"][0.5]
    imp60 = thresholds_by_feature["impulse_rvol"][0.6]
    imp80 = thresholds_by_feature["impulse_rvol"][0.8]
    break50 = thresholds_by_feature["breakout_rvol"][0.5]
    corr50 = thresholds_by_feature["correction_vs_impulse"][0.5]
    variants.append(("volume_structure_train_medians",
                     lambda f: available(f["impulse_rvol"]) and f["impulse_rvol"] >= imp50,
                     lambda f: available(f["correction_vs_impulse"])
                     and f["correction_vs_impulse"] <= corr50
                     and available(f["breakout_rvol"]) and f["breakout_rvol"] >= break50))
    variants.append(("tiered_impulse_volume_bottom60_add_bottom80_trade",
                     lambda f: available(f["impulse_rvol"]) and f["impulse_rvol"] <= imp80,
                     lambda f: available(f["impulse_rvol"]) and f["impulse_rvol"] <= imp60))

    summaries = []
    details = []
    for rule, trade_filter, add_filter in variants:
        results = []
        for trade in source_trades:
            feature = features[int(trade["movement_id"])]
            if not trade_filter(feature):
                continue
            result = evaluate(trade, allow_add=add_filter(feature))
            results.append(result)
            details.append({"rule": rule, **feature, **result})
        summaries.append(summarize(rule, results, len(source_trades), split_time))

    ranked = sorted(summaries, key=lambda x: (x["test_pf"] or 0, x["test_net_pnl_rub"]), reverse=True)
    ranked_net = sorted(summaries, key=lambda x: x["net_pnl_rub"], reverse=True)

    # Descriptive quintiles for impulse and breakout relative volume.
    buckets = []
    baseline_details = [row for row in details if row["rule"] == "baseline_all_adds"]
    for field in ("impulse_rvol", "breakout_rvol", "correction_vs_impulse"):
        valid = [row for row in baseline_details if row[field] is not None]
        valid.sort(key=lambda x: x[field])
        for bucket in range(5):
            lo = len(valid) * bucket // 5
            hi = len(valid) * (bucket + 1) // 5
            group = valid[lo:hi]
            pnl = [x["net_pnl_rub"] for x in group]
            buckets.append({
                "feature": field,
                "quintile": bucket + 1,
                "trades": len(group),
                "feature_min": min(x[field] for x in group) if group else None,
                "feature_max": max(x[field] for x in group) if group else None,
                "average_pnl_rub": fmean(pnl) if pnl else None,
                "net_pnl_rub": sum(pnl),
                "profit_factor": profit_factor(pnl),
                "win_rate_pct": sum(x > 0 for x in pnl) / len(pnl) * 100 if pnl else None,
            })

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summaries)
    write_table(wb, "Ranked_OOS_PF", ranked)
    write_table(wb, "Ranked_Net", ranked_net)
    write_table(wb, "Volume_Buckets", buckets)
    write_table(wb, "Train_Thresholds", percentile_thresholds)
    write_table(wb, "Impulse_Volume_Standard", impulse_standard_rows)
    write_table(wb, "Impulse_Standard_Summary", impulse_standard_summary)
    write_table(wb, "Features", feature_rows)
    write_table(wb, "Details", details)
    write_table(wb, "Method", [
        {"item": "Current strategy", "definition": "Long100 at -0.4%; confirmed add100; 30-minute weak exit; 2% weighted-average stop; next-open exit"},
        {"item": "Relative volume", "definition": "actual volume / median volume for the same minute-of-day over the previous 60 observations; minimum 10"},
        {"item": "Impulse volume", "definition": "sum actual / sum expected volume over the complete initiating GREEN ST movement"},
        {"item": "Correction volume", "definition": "sum actual / sum expected volume from impulse end to first -0.4% entry"},
        {"item": "Breakout volume", "definition": "relative volume of the closed confirmation minute immediately before the add execution"},
        {"item": "OOS", "definition": f"chronological 70/30 split; train through {split_time}; later trades reported as test"},
        {"item": "Data cleaning", "definition": f"roll gaps adjusted; ignored {len(bad_roll_bar_indices)} isolated old-contract bars on adjusted rollover dates"},
        {"item": "Costs", "definition": "commission included; no slippage"},
    ])
    wb.save(OUTPUT)
    print("split", split_time, "features", len(feature_rows))
    print("TOP FULL NET")
    for row in ranked_net[:10]:
        print(row)
    print("TOP OOS PF")
    for row in ranked[:10]:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
