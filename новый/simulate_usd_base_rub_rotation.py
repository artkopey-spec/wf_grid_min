from __future__ import annotations

from collections import defaultdict
from datetime import time
from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook
from openpyxl.styles import Font

from analyze_st_movements_roll_adjusted import read_bars, read_rollovers
from simulate_st_impulse_strategy import read_candidates


OUTPUT_FILE = Path("ST_80_4_USD_base_RUB_rotation_0_6.xlsx")
INITIAL_USD = 100_000.0
COMMISSION_RATE_PER_SIDE = 0.0000666667
MORNING_TIME = time(9, 0)


def main() -> None:
    bars = read_bars()
    rollovers = read_rollovers(bars)
    candidates = read_candidates()

    offset = 0.0
    rollover_cursor = 0
    bars_by_time = {}
    bars_by_day = defaultdict(list)
    for bar in bars:
        while (
            rollover_cursor < len(rollovers)
            and rollovers[rollover_cursor]["effective_index"] == bar["index"]
        ):
            offset += rollovers[rollover_cursor]["offset_delta"]
            rollover_cursor += 1
        bar["offset"] = offset
        bars_by_time[bar["time"]] = bar
        bars_by_day[bar["time"].date()].append(bar)

    trading_days = sorted(bars_by_day)
    first_candidate_by_day = {}
    for candidate in candidates:
        first_candidate_by_day.setdefault(candidate["end_time"].date(), candidate)

    usd_gross = INITIAL_USD
    usd_net = INITIAL_USD
    peak_net = INITIAL_USD
    max_drawdown_pct = 0.0
    cycles = []

    for sell_day, candidate in sorted(first_candidate_by_day.items()):
        later_days = [day for day in trading_days if day > sell_day]
        if not later_days:
            continue
        buy_day = later_days[0]
        morning_bars = [
            bar for bar in bars_by_day[buy_day] if bar["time"].time() >= MORNING_TIME
        ]
        if not morning_bars:
            continue

        sell_bar = bars_by_time[candidate["end_time"]]
        buy_bar = morning_bars[0]

        # Rebase only the offset change inside this RUB-holding interval. This
        # removes a futures contract roll without distorting the USD/RUB level.
        buy_rate_gap_neutral = buy_bar["open"] + buy_bar["offset"] - sell_bar["offset"]
        gross_multiplier = sell_bar["close"] / buy_rate_gap_neutral
        net_multiplier = gross_multiplier * (1 - COMMISSION_RATE_PER_SIDE) ** 2

        usd_before_gross = usd_gross
        usd_before_net = usd_net
        usd_gross *= gross_multiplier
        usd_net *= net_multiplier
        peak_net = max(peak_net, usd_net)
        drawdown_pct = (usd_net / peak_net - 1) * 100
        max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)

        cycles.append(
            {
                "cycle_id": len(cycles) + 1,
                "sell_usd_time": sell_bar["time"],
                "sell_usd_rate": sell_bar["close"],
                "buy_usd_time": buy_bar["time"],
                "buy_usd_rate_raw": buy_bar["open"],
                "buy_usd_rate_gap_neutral": buy_rate_gap_neutral,
                "impulse_movement_id": candidate["movement_id"],
                "impulse_size_pct": candidate["movement_pct"],
                "gross_cycle_return_pct_usd": (gross_multiplier - 1) * 100,
                "net_cycle_return_pct_usd": (net_multiplier - 1) * 100,
                "gross_usd_before": usd_before_gross,
                "gross_usd_after": usd_gross,
                "net_usd_before": usd_before_net,
                "net_usd_after": usd_net,
                "net_profit_usd": usd_net - usd_before_net,
                "drawdown_pct": drawdown_pct,
                "roll_offset_change": buy_bar["offset"] - sell_bar["offset"],
            }
        )

    returns = [cycle["net_cycle_return_pct_usd"] for cycle in cycles]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    summary = [
        ("Strategy", "USD base; temporary RUB after GREEN ST impulse >0.6%"),
        ("SuperTrend", "80 / 4.0"),
        ("Initial USD", INITIAL_USD),
        ("Completed RUB intervals", len(cycles)),
        ("Win rate pct", len(wins) / len(cycles) * 100),
        ("Final USD gross", usd_gross),
        ("Gross return pct", (usd_gross / INITIAL_USD - 1) * 100),
        ("Final USD net", usd_net),
        ("Net return pct", (usd_net / INITIAL_USD - 1) * 100),
        ("Net profit USD", usd_net - INITIAL_USD),
        ("Mean net cycle return pct", fmean(returns)),
        ("Median net cycle return pct", median(returns)),
        ("Mean winning cycle pct", fmean(wins)),
        ("Mean losing cycle pct", fmean(losses)),
        ("Best cycle pct", max(returns)),
        ("Worst cycle pct", min(returns)),
        ("Max drawdown pct", max_drawdown_pct),
        ("Commission per side", COMMISSION_RATE_PER_SIDE),
        ("Benchmark", "Hold USD = 100,000 USD throughout"),
    ]

    output = Workbook()
    summary_sheet = output.active
    summary_sheet.title = "Summary"
    summary_sheet.append(["metric", "value"])
    for row in summary:
        summary_sheet.append(row)

    cycle_sheet = output.create_sheet("RUB_Intervals")
    headers = list(cycles[0])
    cycle_sheet.append(headers)
    for cycle in cycles:
        cycle_sheet.append([cycle[key] for key in headers])

    assumptions_sheet = output.create_sheet("Assumptions")
    assumptions_sheet.append(["item", "definition"])
    assumptions = [
        ("Base asset", "USD; between signals wealth is unchanged in USD terms"),
        ("Trigger", "first completed GREEN ST 80/4 movement strictly >0.6% per day"),
        ("Action", "sell USD at signal-bar Close; hold RUB"),
        ("Re-entry", "buy USD at first minute Open >=09:00 on next trading day"),
        ("Position size", "whole USD account; initial 100,000 USD ~= 100 Si contracts"),
        ("Rollover", "intercontract spread removed only if crossed during RUB interval"),
        ("Costs", "0.00666667% per conversion side; no slippage"),
    ]
    for row in assumptions:
        assumptions_sheet.append(row)

    for sheet in output.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True)
    output.save(OUTPUT_FILE)

    print("SUMMARY")
    for row in summary:
        print(row)
    print(f"saved={OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
