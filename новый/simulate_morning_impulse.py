from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, median


DATA_FILE = Path(r"D:\АКЦИИ 24.08\si минуты\data.csv")
ROLLOVER_FILE = Path(__file__).resolve().parent / "rollover_report.csv"
OUT_JSON = Path(__file__).resolve().parent / "morning_impulse_sim.json"

CONTRACTS = 100
COMMISSION_RATE_PER_SIDE = 0.0000667
IMPULSE_PCT = 0.6
RETRACE_PCT = 0.20
MORNING = (9, 0)
MOSCOW = timezone(timedelta(hours=3))


def read_bars(path: Path) -> list[dict]:
    bars = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle, delimiter=";")):
            ts = datetime.strptime(row["<DATE>"] + row["<TIME>"], "%Y%m%d%H%M%S")
            bars.append(
                {
                    "index": index,
                    "time": ts,
                    "date": ts.date(),
                    "open": float(row["<OPEN>"]),
                    "high": float(row["<HIGH>"]),
                    "low": float(row["<LOW>"]),
                    "close": float(row["<CLOSE>"]),
                }
            )
    return bars


def read_rollovers(bars: list[dict]) -> list[dict]:
    first_index_by_date = {}
    for bar in bars:
        first_index_by_date.setdefault(bar["date"], bar["index"])

    first_date = bars[0]["date"]
    last_date = bars[-1]["date"]
    applied = []
    with ROLLOVER_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            report_time = datetime.strptime(
                row["roll_timestamp"], "%Y-%m-%dT%H:%M:%S%z"
            ).astimezone(MOSCOW)
            nominal_date = report_time.date()
            if not (first_date <= nominal_date <= last_date):
                continue

            spread = float(row["intercontract_spread"])
            effective_date = nominal_date
            if abs(spread) >= 500:
                candidates = []
                for delta in range(-2, 3):
                    candidate_date = nominal_date + timedelta(days=delta)
                    index = first_index_by_date.get(candidate_date)
                    if index is None or index == 0:
                        continue
                    session_jump = bars[index]["open"] - bars[index - 1]["close"]
                    candidates.append(
                        (abs(session_jump - spread), abs(delta), candidate_date)
                    )
                if candidates:
                    _, _, effective_date = min(candidates)

            effective_index = first_index_by_date[effective_date]
            applied.append(
                {
                    "effective_index": effective_index,
                    "offset_delta": -spread,
                    "new_contract": row["new_contract"],
                    "spread": spread,
                }
            )
    return applied


def apply_offsets(bars: list[dict], rollovers: list[dict]) -> None:
    offset = 0.0
    cursor = 0
    for bar in bars:
        while (
            cursor < len(rollovers)
            and rollovers[cursor]["effective_index"] == bar["index"]
        ):
            offset += rollovers[cursor]["offset_delta"]
            cursor += 1
        bar["offset"] = offset
        bar["adj_open"] = bar["open"] + offset
        bar["adj_high"] = bar["high"] + offset
        bar["adj_low"] = bar["low"] + offset
        bar["adj_close"] = bar["close"] + offset


def morning_entry_indices(bars: list[dict]) -> set[int]:
    found: set[date] = set()
    indices: set[int] = set()
    for bar in bars:
        if bar["date"] in found:
            continue
        if bar["time"].hour > MORNING[0] or (
            bar["time"].hour == MORNING[0] and bar["time"].minute >= MORNING[1]
        ):
            indices.add(bar["index"])
            found.add(bar["date"])
    return indices


def make_trade(
    trade_id: int,
    entry: dict,
    exit_bar: dict,
    exit_adj: float,
    exit_raw: float,
    reason: str,
    triggered: bool,
    trigger_bar: dict | None,
    peak_adj: float,
    trough_adj: float,
    wait_days: int,
    status: str,
) -> dict:
    gross_points = exit_adj - entry["adj_open"]
    gross_rub = gross_points * CONTRACTS
    commission_rub = (entry["open"] + exit_raw) * COMMISSION_RATE_PER_SIDE * CONTRACTS
    net_rub = gross_rub - commission_rub
    mfe_pct = (peak_adj - entry["adj_open"]) / entry["open"] * 100
    mae_pct = (trough_adj - entry["adj_open"]) / entry["open"] * 100
    captured_pct = gross_points / entry["open"] * 100
    return {
        "trade_id": trade_id,
        "entry_time": entry["time"].isoformat(sep=" "),
        "exit_time": exit_bar["time"].isoformat(sep=" "),
        "entry_price": entry["open"],
        "exit_price": exit_raw,
        "reason": reason,
        "status": status,
        "triggered": triggered,
        "trigger_time": trigger_bar["time"].isoformat(sep=" ") if trigger_bar else None,
        "wait_calendar_days": wait_days,
        "holding_minutes": exit_bar["index"] - entry["index"] + 1,
        "gross_points": round(gross_points, 4),
        "gross_rub": round(gross_rub, 2),
        "commission_rub": round(commission_rub, 2),
        "net_rub": round(net_rub, 2),
        "net_pct": captured_pct - commission_rub / (entry["open"] * CONTRACTS) * 100,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "giveback_pct": mfe_pct - captured_pct if triggered else None,
        "impulse_to_peak_pct": mfe_pct if triggered else None,
        "entry_hour": entry["time"].hour,
        "exit_hour": exit_bar["time"].hour,
        "trigger_hour": trigger_bar["time"].hour if trigger_bar else None,
        "entry_date": entry["date"].isoformat(),
        "exit_date": exit_bar["date"].isoformat(),
        "exit_month": exit_bar["time"].strftime("%Y-%m"),
    }


def last_bar_indices(bars: list[dict]) -> set[int]:
    last = set()
    for i, bar in enumerate(bars):
        if i == len(bars) - 1 or bars[i + 1]["date"] != bar["date"]:
            last.add(bar["index"])
    return last


def close_position(trades, position, bar, exit_adj, exit_raw, reason, status="closed"):
    entry = position["entry"]
    trigger = position["trigger_bar"]
    wait_days = (
        (trigger["date"] - entry["date"]).days if trigger else (bar["date"] - entry["date"]).days
    )
    trades.append(
        make_trade(
            len(trades) + 1,
            entry,
            bar,
            exit_adj,
            exit_raw,
            reason,
            position["triggered"],
            trigger,
            position["peak_adj"],
            position["trough_adj"],
            wait_days,
            status,
        )
    )


def simulate(bars: list[dict], retrace_pct: float, flatten_eod: bool) -> list[dict]:
    impulse_frac = IMPULSE_PCT / 100.0
    retrace_frac = retrace_pct / 100.0
    trades: list[dict] = []
    position = None
    skip_until: date | None = None
    mornings = morning_entry_indices(bars)
    eod = last_bar_indices(bars)

    for bar in bars:
        morning = bar["index"] in mornings

        if position is None and morning and (skip_until is None or bar["date"] > skip_until):
            position = {
                "entry": bar,
                "peak_adj": bar["adj_open"],
                "trough_adj": bar["adj_open"],
                "triggered": False,
                "trigger_bar": None,
            }

        if position is None:
            continue

        entry = position["entry"]
        raw_entry = entry["open"]
        retrace_points = retrace_frac * raw_entry

        if position["triggered"]:
            stop_adj = position["peak_adj"] - retrace_points
            hit = False
            if bar["adj_open"] <= stop_adj:
                exit_adj = bar["adj_open"]
                exit_raw = bar["open"]
                hit = True
            elif bar["adj_low"] <= stop_adj:
                exit_adj = stop_adj
                exit_raw = stop_adj - bar["offset"]
                hit = True
            if hit:
                close_position(
                    trades,
                    position,
                    bar,
                    exit_adj,
                    exit_raw,
                    f"impulse_ended_{retrace_pct:.2f}_retrace",
                )
                position = None
                skip_until = bar["date"]
                continue

        position["peak_adj"] = max(position["peak_adj"], bar["adj_high"])
        position["trough_adj"] = min(position["trough_adj"], bar["adj_low"])
        if not position["triggered"]:
            move_pct = (position["peak_adj"] - entry["adj_open"]) / raw_entry
            if move_pct > impulse_frac:
                position["triggered"] = True
                position["trigger_bar"] = bar

        if flatten_eod and bar["index"] in eod and position is not None:
            reason = (
                f"impulse_eod_no_{retrace_pct:.2f}_retrace"
                if position["triggered"]
                else "no_impulse_eod"
            )
            close_position(
                trades,
                position,
                bar,
                bar["adj_close"],
                bar["close"],
                reason,
            )
            position = None
            skip_until = bar["date"]

    if position is not None:
        last = bars[-1]
        close_position(
            trades,
            position,
            last,
            last["adj_close"],
            last["close"],
            "end_of_sample_mtm",
            status="open_mtm",
        )
    return trades


def day_impulse_stats(bars: list[dict]) -> dict:
    morning_idx = morning_entry_indices(bars)
    mornings = {bar["date"]: bar for bar in bars if bar["index"] in morning_idx}
    by_day: dict[date, list[dict]] = defaultdict(list)
    for bar in bars:
        by_day[bar["date"]].append(bar)

    moves = []
    for day, morning in mornings.items():
        window = [bar for bar in by_day[day] if bar["index"] >= morning["index"]]
        peak = max(bar["adj_high"] for bar in window)
        move_pct = (peak - morning["adj_open"]) / morning["open"] * 100
        close_pct = (window[-1]["adj_close"] - morning["adj_open"]) / morning["open"] * 100
        trough = min(bar["adj_low"] for bar in window)
        mae_pct = (trough - morning["adj_open"]) / morning["open"] * 100
        moves.append(
            {
                "date": day.isoformat(),
                "month": day.strftime("%Y-%m"),
                "impulse": move_pct > IMPULSE_PCT,
                "move_pct": move_pct,
                "close_pct": close_pct,
                "mae_pct": mae_pct,
            }
        )
    impulse_days = [m for m in moves if m["impulse"]]
    years: dict[str, dict] = defaultdict(lambda: {"days": 0, "impulse": 0})
    for item in moves:
        year = item["date"][:4]
        years[year]["days"] += 1
        years[year]["impulse"] += int(item["impulse"])
    return {
        "days": len(moves),
        "impulse_days": len(impulse_days),
        "impulse_share_pct": len(impulse_days) / len(moves) * 100 if moves else 0.0,
        "avg_impulse_day_peak_pct": fmean(m["move_pct"] for m in impulse_days) if impulse_days else 0.0,
        "median_impulse_day_peak_pct": median(m["move_pct"] for m in impulse_days) if impulse_days else 0.0,
        "avg_day_peak_pct": fmean(m["move_pct"] for m in moves) if moves else 0.0,
        "years": {k: {**v, "share_pct": v["impulse"] / v["days"] * 100} for k, v in sorted(years.items())},
    }


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return dd


def summarize(trades: list[dict], bars: list[dict], retrace_pct: float) -> dict:
    closed = [t for t in trades if t["status"] == "closed"]
    impulse = [t for t in closed if t["triggered"]]
    nets = [t["net_rub"] for t in closed]
    wins = [t for t in closed if t["net_rub"] > 0]
    losses = [t for t in closed if t["net_rub"] <= 0]
    gross_win = sum(t["net_rub"] for t in wins)
    gross_loss = abs(sum(t["net_rub"] for t in losses)) or 1e-12
    equity = []
    running = 0.0
    for t in closed:
        running += t["net_rub"]
        equity.append(running)
    monthly = defaultdict(float)
    for t in closed:
        monthly[t["exit_month"]] += t["net_rub"]
    trigger_hours = defaultdict(int)
    exit_hours = defaultdict(int)
    for t in impulse:
        if t["trigger_hour"] is not None:
            trigger_hours[t["trigger_hour"]] += 1
        exit_hours[t["exit_hour"]] += 1
    wait_buckets = {"same day": 0, "1 day": 0, "2-5 days": 0, "6-20 days": 0, "21+ days": 0}
    for t in impulse:
        d = t["wait_calendar_days"]
        if d <= 0:
            wait_buckets["same day"] += 1
        elif d == 1:
            wait_buckets["1 day"] += 1
        elif d <= 5:
            wait_buckets["2-5 days"] += 1
        elif d <= 20:
            wait_buckets["6-20 days"] += 1
        else:
            wait_buckets["21+ days"] += 1

    reasons: dict[str, int] = defaultdict(int)
    for t in closed:
        key = "impulse_retrace" if t["triggered"] and "retrace" in t["reason"] else t["reason"]
        reasons[key] += 1

    streak = max_streak = 0
    for t in closed:
        if t["net_rub"] <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    def pctile(sample: list[float], q: float) -> float:
        if not sample:
            return 0.0
        ordered = sorted(sample)
        idx = min(len(ordered) - 1, max(0, math.floor(q * (len(ordered) - 1))))
        return ordered[idx]

    no_impulse = [t for t in closed if not t["triggered"]]
    return {
        "retrace_pct": retrace_pct,
        "closed_trades": len(closed),
        "impulse_exits": len(impulse),
        "no_impulse_exits": len(no_impulse),
        "mtm_open": [t for t in trades if t["status"] != "closed"],
        "win_rate_pct": (len(wins) / len(closed) * 100) if closed else 0.0,
        "wins": len(wins),
        "losses": len(losses),
        "total_gross_rub": sum(t["gross_rub"] for t in closed),
        "total_commission_rub": sum(t["commission_rub"] for t in closed),
        "total_net_rub": sum(nets),
        "avg_net_rub": fmean(nets) if nets else 0.0,
        "median_net_rub": median(nets) if nets else 0.0,
        "avg_win_rub": fmean([t["net_rub"] for t in wins]) if wins else 0.0,
        "avg_loss_rub": fmean([t["net_rub"] for t in losses]) if losses else 0.0,
        "p05_net_rub": pctile(nets, 0.05),
        "p95_net_rub": pctile(nets, 0.95),
        "profit_factor": gross_win / gross_loss,
        "max_dd_rub": max_drawdown(nets),
        "max_losing_streak": max_streak,
        "avg_wait_days": fmean([t["wait_calendar_days"] for t in impulse]) if impulse else 0.0,
        "median_wait_days": median([t["wait_calendar_days"] for t in impulse]) if impulse else 0.0,
        "avg_mae_pct": fmean([t["mae_pct"] for t in closed]) if closed else 0.0,
        "worst_mae_pct": min((t["mae_pct"] for t in closed), default=0.0),
        "avg_mfe_pct": fmean([t["mfe_pct"] for t in impulse]) if impulse else 0.0,
        "avg_net_pct": fmean([t["net_pct"] for t in closed]) if closed else 0.0,
        "avg_captured_pct": fmean([t["net_pct"] for t in impulse]) if impulse else 0.0,
        "avg_no_impulse_pct": fmean([t["net_pct"] for t in no_impulse]) if no_impulse else 0.0,
        "avg_giveback_pct": fmean(
            [t["giveback_pct"] for t in impulse if t["giveback_pct"] is not None]
        )
        if impulse
        else 0.0,
        "avg_hold_minutes": fmean([t["holding_minutes"] for t in closed]) if closed else 0.0,
        "trading_days": len({bar["date"] for bar in bars}),
        "reasons": dict(reasons),
        "monthly": dict(sorted(monthly.items())),
        "equity_end": equity[-1] if equity else 0.0,
        "trigger_hours": dict(sorted(trigger_hours.items())),
        "exit_hours": dict(sorted(exit_hours.items())),
        "wait_buckets": wait_buckets,
    }


def compact_summary(item: dict) -> dict:
    return {
        "trades": item["closed_trades"],
        "impulse_exits": item["impulse_exits"],
        "no_impulse_exits": item.get("no_impulse_exits", 0),
        "win_rate_pct": item["win_rate_pct"],
        "total_net_rub": item["total_net_rub"],
        "avg_net_rub": item["avg_net_rub"],
        "median_net_rub": item["median_net_rub"],
        "avg_win_rub": item["avg_win_rub"],
        "avg_loss_rub": item["avg_loss_rub"],
        "p05_net_rub": item.get("p05_net_rub", 0.0),
        "p95_net_rub": item.get("p95_net_rub", 0.0),
        "max_dd_rub": item["max_dd_rub"],
        "profit_factor": item["profit_factor"],
        "avg_net_pct": item.get("avg_net_pct", 0.0),
        "avg_captured_pct": item["avg_captured_pct"],
        "avg_no_impulse_pct": item.get("avg_no_impulse_pct", 0.0),
        "avg_mae_pct": item["avg_mae_pct"],
        "worst_mae_pct": item["worst_mae_pct"],
        "avg_mfe_pct": item["avg_mfe_pct"],
        "commission_rub": item["total_commission_rub"],
        "max_losing_streak": item.get("max_losing_streak", 0),
        "wins": item["wins"],
        "losses": item["losses"],
    }


def lite(trade: dict) -> dict:
    return {
        "id": trade["trade_id"],
        "entry": trade["entry_time"][:16],
        "exit": trade["exit_time"][:16],
        "wait": trade["wait_calendar_days"],
        "net_rub": round(trade["net_rub"], 0),
        "net_pct": round(trade["net_pct"], 3),
        "mfe": round(trade["mfe_pct"], 3),
        "mae": round(trade["mae_pct"], 3),
        "reason": trade["reason"],
        "triggered": trade["triggered"],
    }


def main() -> None:
    print(f"loading {DATA_FILE}")
    bars = read_bars(DATA_FILE)
    rollovers = read_rollovers(bars)
    apply_offsets(bars, rollovers)
    print(f"bars={len(bars)} days={len({b['date'] for b in bars})} rollovers={len(rollovers)}")

    carry_trades = simulate(bars, RETRACE_PCT, flatten_eod=False)
    daily_trades = simulate(bars, RETRACE_PCT, flatten_eod=True)
    carry = summarize(carry_trades, bars, RETRACE_PCT)
    daily = summarize(daily_trades, bars, RETRACE_PCT)
    days = day_impulse_stats(bars)

    daily_closed = [t for t in daily_trades if t["status"] == "closed"]
    carry_closed = [t for t in carry_trades if t["status"] == "closed"]

    payload = {
        "source": str(DATA_FILE),
        "first_bar": bars[0]["time"].isoformat(sep=" "),
        "last_bar": bars[-1]["time"].isoformat(sep=" "),
        "bars": len(bars),
        "days": len({b["date"] for b in bars}),
        "rollovers": len(rollovers),
        "contracts": CONTRACTS,
        "impulse_pct": IMPULSE_PCT,
        "retrace_pct": RETRACE_PCT,
        "commission": COMMISSION_RATE_PER_SIDE,
        "day_stats": days,
        "carry": {k: v for k, v in carry.items() if k != "mtm_open"},
        "carry_compact": compact_summary(carry),
        "carry_mtm": carry["mtm_open"],
        "carry_trades": [lite(t) for t in carry_closed],
        "daily": {k: v for k, v in daily.items() if k != "mtm_open"},
        "daily_compact": compact_summary(daily),
        "daily_best": [lite(t) for t in sorted(daily_closed, key=lambda x: x["net_rub"], reverse=True)[:8]],
        "daily_worst": [lite(t) for t in sorted(daily_closed, key=lambda x: x["net_rub"])[:8]],
        "daily_monthly": daily["monthly"],
        "carry_monthly": carry["monthly"],
        "daily_trigger_hours": daily["trigger_hours"],
        "daily_reasons": daily["reasons"],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    print("DAY", json.dumps(days, ensure_ascii=False, indent=2))
    print("CARRY", json.dumps(compact_summary(carry), ensure_ascii=False, indent=2))
    if carry["mtm_open"]:
        mtm = carry["mtm_open"][0]
        print(
            "CARRY_MTM",
            mtm["entry_time"],
            "net",
            round(mtm["net_rub"]),
            "mae",
            round(mtm["mae_pct"], 2),
        )
    print("DAILY", json.dumps(compact_summary(daily), ensure_ascii=False, indent=2))
    print("DAILY_REASONS", daily["reasons"])
    print("DAILY_HOURS", daily["trigger_hours"])
    print(f"saved {OUT_JSON}")


if __name__ == "__main__":
    main()
