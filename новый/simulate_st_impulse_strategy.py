from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from analyze_st_movements_roll_adjusted import read_bars, read_rollovers


MOVEMENTS_FILE = Path("st_movements_roll_adjusted_percent.xlsx")
OUTPUT_FILE = Path("ST_80_4_impulse_0_6_trade_simulation.xlsx")
THRESHOLD_PCT = 0.6
CONTRACTS = 100
COMMISSION_RATE_PER_SIDE = 0.0000666667
MORNING_ENTRY_TIME = time(9, 0)


def read_candidates() -> list[dict]:
    workbook = load_workbook(MOVEMENTS_FILE, read_only=True, data_only=True)
    sheet = workbook["Movements_Sorted"]
    rows = sheet.iter_rows(values_only=True)
    headers = next(rows)
    column = {name: index for index, name in enumerate(headers)}
    candidates = []
    for row in rows:
        if (
            row[column["st_color"]] == "GREEN"
            and float(row[column["adjusted_movement_pct"]]) > THRESHOLD_PCT
        ):
            candidates.append(
                {
                    "movement_id": int(row[column["movement_id"]]),
                    "start_time": row[column["start_time"]],
                    "end_time": row[column["end_time"]],
                    "movement_pct": float(row[column["adjusted_movement_pct"]]),
                }
            )
    return sorted(candidates, key=lambda item: item["end_time"])


def prepare_bars():
    bars = read_bars()
    rollovers = read_rollovers(bars)
    offset = 0.0
    rollover_cursor = 0
    by_time = {}
    by_day = defaultdict(list)
    for bar in bars:
        while (
            rollover_cursor < len(rollovers)
            and rollovers[rollover_cursor]["effective_index"] == bar["index"]
        ):
            offset += rollovers[rollover_cursor]["offset_delta"]
            rollover_cursor += 1
        bar["offset"] = offset
        bar["adjusted_open"] = bar["open"] + offset
        bar["adjusted_close"] = bar["close"] + offset
        by_time[bar["time"]] = bar
        by_day[bar["time"].date()].append(bar)
    return bars, rollovers, by_time, dict(sorted(by_day.items()))


def make_trade(mode, trade_id, entry_bar, exit_bar, candidate=None, status="closed"):
    gross_points = exit_bar["adjusted_close"] - entry_bar["adjusted_open"]
    gross_rub = gross_points * CONTRACTS
    commission_rub = (
        entry_bar["open"] + exit_bar["close"]
    ) * COMMISSION_RATE_PER_SIDE * CONTRACTS
    net_rub = gross_rub - commission_rub
    return {
        "mode": mode,
        "trade_id": trade_id,
        "entry_time": entry_bar["time"],
        "entry_price_raw": entry_bar["open"],
        "exit_time": exit_bar["time"],
        "exit_price_raw": exit_bar["close"],
        "holding_bars": exit_bar["index"] - entry_bar["index"] + 1,
        "holding_calendar_days": (exit_bar["time"].date() - entry_bar["time"].date()).days + 1,
        "exit_reason": "green_impulse_gt_0.6_finished" if candidate else "end_of_day_or_sample",
        "impulse_movement_id": candidate["movement_id"] if candidate else None,
        "impulse_size_pct": candidate["movement_pct"] if candidate else None,
        "gross_points_per_contract": gross_points,
        "gross_pnl_rub_100_contracts": gross_rub,
        "commission_rub": commission_rub,
        "net_pnl_rub_100_contracts": net_rub,
        "gross_return_pct_on_notional": gross_points / entry_bar["open"] * 100,
        "net_return_pct_on_notional": net_rub / (entry_bar["open"] * CONTRACTS) * 100,
        "status": status,
    }


def morning_entry(day_bars):
    eligible = [bar for bar in day_bars if bar["time"].time() >= MORNING_ENTRY_TIME]
    return eligible[0] if eligible else None


def simulate_carry(bars, candidates, by_day, by_time):
    days = list(by_day)
    entry_bar = morning_entry(by_day[days[0]])
    trades = []
    candidate_index = 0
    while entry_bar is not None:
        while (
            candidate_index < len(candidates)
            and candidates[candidate_index]["end_time"] < entry_bar["time"]
        ):
            candidate_index += 1
        if candidate_index >= len(candidates):
            trades.append(
                make_trade(
                    "carry_until_impulse",
                    len(trades) + 1,
                    entry_bar,
                    bars[-1],
                    status="open_mark_to_market",
                )
            )
            break
        candidate = candidates[candidate_index]
        exit_bar = by_time[candidate["end_time"]]
        trades.append(
            make_trade(
                "carry_until_impulse", len(trades) + 1, entry_bar, exit_bar, candidate
            )
        )
        next_days = [day for day in days if day > exit_bar["time"].date()]
        if not next_days:
            break
        entry_bar = morning_entry(by_day[next_days[0]])
        if entry_bar is None:
            later_days = [day for day in next_days[1:] if morning_entry(by_day[day])]
            entry_bar = morning_entry(by_day[later_days[0]]) if later_days else None
        candidate_index += 1
    return trades


def simulate_daily(candidates, by_day, by_time):
    candidates_by_day = defaultdict(list)
    for candidate in candidates:
        candidates_by_day[candidate["end_time"].date()].append(candidate)
    trades = []
    for day, day_bars in by_day.items():
        entry_bar = morning_entry(day_bars)
        if entry_bar is None:
            continue
        possible = [
            candidate
            for candidate in candidates_by_day.get(day, [])
            if candidate["end_time"] >= entry_bar["time"]
        ]
        candidate = possible[0] if possible else None
        exit_bar = by_time[candidate["end_time"]] if candidate else day_bars[-1]
        trades.append(
            make_trade(
                "daily_close_if_no_impulse",
                len(trades) + 1,
                entry_bar,
                exit_bar,
                candidate,
            )
        )
    return trades


def summarize(mode, trades):
    closed = [trade for trade in trades if trade["status"] == "closed"]
    net_values = [trade["net_pnl_rub_100_contracts"] for trade in closed]
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in net_values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    wins = sum(value > 0 for value in net_values)
    impulse_exits = sum(trade["impulse_movement_id"] is not None for trade in closed)
    return {
        "mode": mode,
        "closed_trades": len(closed),
        "impulse_exits": impulse_exits,
        "win_rate_net_pct": wins / len(closed) * 100,
        "total_gross_pnl_rub_100_contracts": sum(
            trade["gross_pnl_rub_100_contracts"] for trade in closed
        ),
        "total_commission_rub": sum(trade["commission_rub"] for trade in closed),
        "total_net_pnl_rub_100_contracts": sum(net_values),
        "average_net_pnl_rub_per_trade": fmean(net_values),
        "median_net_pnl_rub_per_trade": median(net_values),
        "average_holding_bars": fmean(trade["holding_bars"] for trade in closed),
        "average_holding_calendar_days": fmean(
            trade["holding_calendar_days"] for trade in closed
        ),
        "max_drawdown_rub_closed_trade_equity": max_drawdown,
        "open_mtm_pnl_rub": sum(
            trade["net_pnl_rub_100_contracts"]
            for trade in trades
            if trade["status"] != "closed"
        ),
    }


def write_sheet(workbook, name, rows):
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
    bars, rollovers, by_time, by_day = prepare_bars()
    candidates = read_candidates()
    carry = simulate_carry(bars, candidates, by_day, by_time)
    daily = simulate_daily(candidates, by_day, by_time)
    summaries = [
        summarize("carry_until_impulse", carry),
        summarize("daily_close_if_no_impulse", daily),
    ]

    workbook = Workbook()
    workbook.remove(workbook.active)
    summary_sheet = write_sheet(workbook, "Summary", summaries)
    assumptions = [
        {"parameter": "SuperTrend", "value": "ATR 80 / multiplier 4.0"},
        {"parameter": "Impulse", "value": "GREEN movement strictly > 0.6%"},
        {"parameter": "Position", "value": "LONG 100 Si contracts"},
        {"parameter": "Entry", "value": "first minute Open at or after 09:00 when flat"},
        {"parameter": "Exit", "value": "close of ST color-change bar"},
        {"parameter": "After exit", "value": "flat until next trading morning"},
        {"parameter": "Rollover", "value": "gap-neutral adjusted PnL"},
        {"parameter": "Commission per side", "value": COMMISSION_RATE_PER_SIDE},
        {"parameter": "Slippage", "value": 0},
        {"parameter": "Point value assumption", "value": "1 RUB per point per contract"},
    ]
    write_sheet(workbook, "Assumptions", assumptions)
    write_sheet(workbook, "Carry_Trades", carry)
    write_sheet(workbook, "Daily_Variant_Trades", daily)
    write_sheet(workbook, "Eligible_Impulses", candidates)

    for row in summary_sheet.iter_rows(min_row=2):
        row[3].number_format = '0.00"%"'
    workbook.save(OUTPUT_FILE)

    print(f"candidates={len(candidates)} rollovers={len(rollovers)}")
    for item in summaries:
        print(item)
    print(f"saved={OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
