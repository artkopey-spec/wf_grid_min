from __future__ import annotations

from bisect import bisect_left
from datetime import time, timedelta
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from analyze_st_movements_roll_adjusted import read_bars, read_rollovers


SOURCE_FILE = Path("ST_80_4_post_DOWN_impulse_0_6_paths.xlsx")
OUTPUT_FILE = Path("ST_80_4_SHORT_after_DOWN_0_6_backtest.xlsx")
CONTRACTS = 100
COMMISSION_RATE_PER_SIDE = 0.0000666667


HORIZONS = [
    ("3_hours", "ret_plus_3h_pct", "time_plus_3h_used"),
    ("same_day_19_00", "ret_at_19_pct", "time_at_19_used"),
    ("next_trading_day_open", "ret_next_day_open_pct", "time_next_day_open_used"),
    ("24h_first_available", "ret_plus_24h_pct", "time_plus_24h_used"),
]


def read_events():
    workbook = load_workbook(SOURCE_FILE, read_only=True, data_only=True)
    sheet = workbook["Events"]
    rows = sheet.iter_rows(values_only=True)
    headers = next(rows)
    return [dict(zip(headers, row)) for row in rows]


def build_trades(events, horizon, return_key, time_key):
    trades = []
    skipped_missing = 0
    for event in sorted(events, key=lambda item: item["impulse_end_time"]):
        entry_time = event["impulse_end_time"]
        exit_time = event[time_key]
        price_return_pct = event[return_key]
        if exit_time is None or price_return_pct is None:
            skipped_missing += 1
            continue
        entry_price = float(event["anchor_close"])
        exit_price_gap_neutral = entry_price * (1 + price_return_pct / 100)
        gross_points = entry_price - exit_price_gap_neutral
        gross_pnl = gross_points * CONTRACTS
        commission = (
            entry_price + exit_price_gap_neutral
        ) * COMMISSION_RATE_PER_SIDE * CONTRACTS
        net_pnl = gross_pnl - commission
        trades.append(
            {
                "horizon": horizon,
                "trade_id": len(trades) + 1,
                "movement_id": event["movement_id"],
                "impulse_size_pct": event["impulse_size_pct"],
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": exit_time,
                "holding_clock_hours": (exit_time - entry_time).total_seconds() / 3600,
                "exit_price_gap_neutral": exit_price_gap_neutral,
                "price_change_pct": price_return_pct,
                "gross_points_per_contract": gross_points,
                "gross_pnl_rub_100_contracts": gross_pnl,
                "commission_rub": commission,
                "net_pnl_rub_100_contracts": net_pnl,
            }
        )
    return trades, 0, skipped_missing


def summarize(horizon, trades, skipped_overlap, skipped_missing):
    net = [trade["net_pnl_rub_100_contracts"] for trade in trades]
    gross = [trade["gross_pnl_rub_100_contracts"] for trade in trades]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value <= 0]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_consecutive_losses = 0
    current_losses = 0
    for value in net:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value <= 0:
            current_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
        else:
            current_losses = 0
    position_events = []
    for trade in trades:
        position_events.append((trade["entry_time"], 1))
        position_events.append((trade["exit_time"], -1))
    concurrent = 0
    max_concurrent = 0
    for _, change in sorted(position_events, key=lambda item: (item[0], item[1])):
        concurrent += change
        max_concurrent = max(max_concurrent, concurrent)
    return {
        "horizon": horizon,
        "trades": len(trades),
        "skipped_overlap": skipped_overlap,
        "skipped_missing_horizon": skipped_missing,
        "win_rate_pct": len(wins) / len(net) * 100,
        "total_gross_pnl_rub": sum(gross),
        "total_commission_rub": sum(trade["commission_rub"] for trade in trades),
        "total_net_pnl_rub": sum(net),
        "average_net_pnl_rub": fmean(net),
        "median_net_pnl_rub": median(net),
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "best_trade_rub": max(net),
        "worst_trade_rub": min(net),
        "max_drawdown_rub": max_drawdown,
        "max_consecutive_losses": max_consecutive_losses,
        "max_concurrent_positions": max_concurrent,
        "max_concurrent_contracts": max_concurrent * CONTRACTS,
    }


def write_table(workbook, name, rows):
    sheet = workbook.create_sheet(name)
    headers = list(rows[0])
    sheet.append(headers)
    for row in rows:
        sheet.append([row[key] for key in headers])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    return sheet


def main() -> None:
    events = read_events()
    bars = read_bars()
    rollovers = read_rollovers(bars)
    offset = 0.0
    rollover_cursor = 0
    by_time = {}
    by_day = {}
    for bar in bars:
        while (
            rollover_cursor < len(rollovers)
            and rollovers[rollover_cursor]["effective_index"] == bar["index"]
        ):
            offset += rollovers[rollover_cursor]["offset_delta"]
            rollover_cursor += 1
        bar["offset"] = offset
        by_time[bar["time"]] = bar
        by_day.setdefault(bar["time"].date(), []).append(bar)
    times = [bar["time"] for bar in bars]

    # For +3h and +24h use the first actually available bar at or after the
    # target, including the next session after a weekend or market closure.
    for event in events:
        anchor = by_time[event["impulse_end_time"]]
        for hours, return_key, time_key in (
            (3, "ret_plus_3h_pct", "time_plus_3h_used"),
            (24, "ret_plus_24h_pct", "time_plus_24h_used"),
        ):
            target = event["impulse_end_time"] + timedelta(hours=hours)
            index = bisect_left(times, target)
            if index >= len(bars):
                event[time_key] = None
                event[return_key] = None
                continue
            future = bars[index]
            future_gap_neutral = future["open"] + future["offset"] - anchor["offset"]
            event[time_key] = future["time"]
            event[return_key] = (
                future_gap_neutral / anchor["close"] - 1
            ) * 100

        # If the signal appeared after 19:00, close at the first available
        # 19:00 session point on the next trading date so every signal is traded.
        if event["time_at_19_used"] is None:
            later_days = sorted(
                day for day in by_day if day > event["impulse_end_time"].date()
            )
            future_19 = None
            for day in later_days:
                eligible = [bar for bar in by_day[day] if bar["time"].time() >= time(19, 0)]
                if eligible:
                    future_19 = eligible[0]
                    break
            if future_19 is not None:
                future_gap_neutral = (
                    future_19["open"] + future_19["offset"] - anchor["offset"]
                )
                event["time_at_19_used"] = future_19["time"]
                event["ret_at_19_pct"] = (
                    future_gap_neutral / anchor["close"] - 1
                ) * 100
    all_trades = []
    summaries = []
    horizon_trades = {}
    for horizon, return_key, time_key in HORIZONS:
        trades, overlap, missing = build_trades(
            events, horizon, return_key, time_key
        )
        horizon_trades[horizon] = trades
        all_trades.extend(trades)
        summaries.append(summarize(horizon, trades, overlap, missing))

    workbook = Workbook()
    workbook.remove(workbook.active)
    write_table(workbook, "Summary", summaries)
    for horizon, trades in horizon_trades.items():
        write_table(workbook, horizon[:31], trades)
    assumptions = [
        {"item": "SuperTrend", "definition": "80 / 4.0"},
        {"item": "Signal", "definition": "first completed RED impulse strictly >0.6% per day"},
        {"item": "Entry", "definition": "SHORT 100 Si at Close of RED-to-GREEN flip bar"},
        {"item": "Position sizing", "definition": "every signal opens a new SHORT 100 Si; overlapping positions add"},
        {"item": "Costs", "definition": "0.00666667% per side; no slippage"},
        {"item": "Rollover", "definition": "gap-neutral prices"},
        {"item": "3h / 24h", "definition": "first available bar at/after target, including after weekends"},
        {"item": "19:00 after late signal", "definition": "if signal is after 19:00, exit at first available 19:00 point next trading day"},
    ]
    write_table(workbook, "Method", assumptions)
    workbook.save(OUTPUT_FILE)

    print(f"events={len(events)}")
    for item in summaries:
        print(item)
    print(f"saved={OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
