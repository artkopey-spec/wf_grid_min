from pathlib import Path
from statistics import fmean, median

from openpyxl import load_workbook


ROOT = Path(__file__).parent


def rows_from(file_name, sheet_name):
    wb = load_workbook(ROOT / file_name, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    return [{name: row[i] for i, name in enumerate(headers)} for row in rows]


def max_drawdown(values):
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def quantile(values, p):
    values = sorted(values)
    k = (len(values) - 1) * p
    lo = int(k); hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def summarize(pnls, notionals):
    returns = [pnl / notional * 100 for pnl, notional in zip(pnls, notionals)]
    losses = [r for r in returns if r < 0]
    return {
        "cum_pct": sum(returns),
        "avg_pct": fmean(returns),
        "median_pct": median(returns),
        "dd_pct": max_drawdown(returns),
        "worst_pct": min(returns),
        "avg_loss_pct": fmean(losses),
        "median_loss_pct": median(losses),
        "loss_p25_pct": quantile(losses, .25),
        "loss_p75_pct": quantile(losses, .75),
    }


def long_base():
    rows = rows_from("MIX_ST_19_4_impulse_06_07_pullback_04_next_open.xlsx", "Trades")
    rows = [r for r in rows if float(r["minimum_impulse_pct"]) == .7]
    rows.sort(key=lambda r: r["entry_time"])
    return rows, summarize(
        [float(r["net_pnl_rub"]) for r in rows],
        [float(r["entry_price_continuous"]) * 100 for r in rows],
    )


def risk_metrics():
    source, _ = long_base()
    details = rows_from("MIX_ST_19_4_risk_controls.xlsx", "Details")
    result = {}
    for rule in sorted({r["rule"] for r in details}):
        group = [r for r in details if r["rule"] == rule]
        base = [r for r in source if r["fill_type"] == "level_touch"] if rule.startswith("exact_touch") or rule == "only_exact_0.4_touch" else source
        result[rule] = summarize(
            [float(r["pnl"]) for r in group],
            [float(r["entry_price_continuous"]) * 100 for r in base],
        )
    return result


def long_alternatives():
    result = {}
    immediate = rows_from("MIX_ST_19_4_LONG_after_UP_0_6_backtest.xlsx", "next_trading_day_open")
    result["immediate"] = summarize(
        [float(r["net_pnl_rub_100_contracts"]) for r in immediate],
        [float(r["entry_price"]) * 100 for r in immediate],
    )
    staged = [r for r in rows_from("MIX_ST_19_4_scaled_LONG_03_06_next_open.xlsx", "Trades") if float(r["filled_contracts"]) > 0]
    result["staged"] = summarize(
        [float(r["net_pnl_rub"]) for r in staged],
        [float(r["weighted_entry"]) * float(r["filled_contracts"]) for r in staged],
    )
    green = rows_from("MIX_ST_19_4_LONG_on_GREEN_reentry.xlsx", "Trades")
    result["green_reentry"] = summarize(
        [float(r["net_pnl_rub"]) for r in green],
        [float(r["entry_price"]) * 100 for r in green],
    )
    ratio90 = [r for r in rows_from("MIX_ST_19_4_GREEN_reentry_correction_ratio_filters.xlsx", "Selected_Trades") if float(r["filter_threshold_pct"]) == 90]
    result["ratio90"] = summarize(
        [float(r["net_pnl_rub"]) for r in ratio90],
        [float(r["entry_price"]) * 100 for r in ratio90],
    )
    return result


def short_grid(file_name):
    rows = rows_from(file_name, "Grid_By_Parameters")
    return {(float(r["minimum_down_impulse_pct"]), float(r["rebound_entry_pct"])): r for r in rows}


if __name__ == "__main__":
    _, long = long_base()
    print("LONG_BASE", long)
    print("LONG_RISK", risk_metrics())
    print("LONG_ALT", long_alternatives())
    all_grid = short_grid("MIX_ST_19_4_mirror_SHORT_optimization_exit_19.xlsx")
    before_grid = short_grid("MIX_ST_19_4_mirror_SHORT_optimization_signals_before_19.xlsx")
    for key in [(.5, .1), (.5, 0), (.5, .2), (.4, .2), (.6, .1), (.7, .1)]:
        print("SHORT_ALL", key, all_grid[key])
    print("SHORT_BEFORE", before_grid[(.5, .1)])
