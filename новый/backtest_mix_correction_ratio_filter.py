from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from backtest_short_after_down_impulse import write_table


SOURCE = Path("MIX_ST_19_4_LONG_on_GREEN_reentry.xlsx")
OUTPUT = Path("MIX_ST_19_4_GREEN_reentry_correction_ratio_filters.xlsx")
THRESHOLDS = (50, 90, 110)
TOTAL_SIGNALS = 326


def load_trades():
    wb = load_workbook(SOURCE, read_only=True, data_only=True)
    ws = wb["Trades"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    trades = []
    for row in rows:
        trade = {name: row[i] for name, i in col.items()}
        trade["correction_to_impulse_ratio_pct"] = float(trade["correction_to_impulse_ratio_pct"])
        trades.append(trade)
    return trades


def drawdown(values):
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def summarize(label, threshold, trades, all_count):
    pnl = [float(t["net_pnl_rub"]) for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    return {
        "rule": label, "maximum_correction_ratio_pct": threshold,
        "trades": len(trades), "skipped_too_deep": all_count - len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100,
        "total_net_pnl_rub": sum(pnl), "net_pnl_per_original_signal_rub": sum(pnl) / TOTAL_SIGNALS,
        "average_trade_rub": fmean(pnl), "median_trade_rub": median(pnl),
        "profit_factor": sum(wins) / abs(sum(losses)), "max_drawdown_rub": drawdown(pnl),
        "average_loss_rub": fmean(losses), "median_loss_rub": median(losses),
        "worst_trade_rub": min(pnl),
        "mean_price_return_pct": fmean(float(t["price_return_pct"]) for t in trades),
        "median_correction_ratio_pct": median(t["correction_to_impulse_ratio_pct"] for t in trades),
    }


def main():
    trades = load_trades()
    summaries = [summarize("no_ratio_filter", None, trades, len(trades))]
    selected_rows = []
    for threshold in THRESHOLDS:
        selected = [t for t in trades if t["correction_to_impulse_ratio_pct"] <= threshold]
        summaries.append(summarize(f"skip_if_correction_gt_{threshold}%", threshold, selected, len(trades)))
        for trade in selected:
            selected_rows.append({"filter_threshold_pct": threshold, **trade})

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summaries)
    write_table(wb, "Selected_Trades", selected_rows)
    write_table(wb, "All_Trades_With_Ratio", trades)
    write_table(wb, "Method", [
        {"item": "Base", "definition": "impulse >0.7%; wait RED-to-GREEN; LONG 100; exit next open"},
        {"item": "Ratio", "definition": "maximum RED correction from impulse peak / prior GREEN impulse size"},
        {"item": "Filter", "definition": "skip entry when completed RED correction ratio is above 50%, 90%, or 110%"},
        {"item": "Costs", "definition": "same gap adjustment and commissions as base backtest"},
    ])
    wb.save(OUTPUT)
    for row in summaries:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
