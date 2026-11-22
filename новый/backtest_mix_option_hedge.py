from pathlib import Path
from statistics import fmean, median

from openpyxl import Workbook, load_workbook

from backtest_short_after_down_impulse import write_table


LONG_FILE = Path("MIX_ST_19_4_impulse_06_07_pullback_04_next_open.xlsx")
SHORT_FILE = Path("MIX_ST_19_4_mirror_SHORT_optimization_exit_19.xlsx")
OUTPUT = Path("MIX_ST_19_4_option_hedge_0_5pct.xlsx")
PREMIUM_PCT = 0.5
CONTRACTS = 100


def read_rows(file_name, sheet_name):
    wb = load_workbook(file_name, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    return [{name: row[i] for i, name in enumerate(headers)} for row in rows]


def drawdown(values):
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def summarize(strategy, scenario, trades):
    pnl = [t["scenario_net_pnl_rub"] for t in trades]
    returns = [t["scenario_net_return_pct"] for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    return {
        "strategy": strategy, "scenario": scenario, "trades": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100,
        "total_net_pnl_rub": sum(pnl), "cumulative_net_return_pct": sum(returns),
        "average_trade_rub": fmean(pnl), "average_trade_return_pct": fmean(returns),
        "median_trade_rub": median(pnl), "median_trade_return_pct": median(returns),
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "max_drawdown_rub": drawdown(pnl), "max_drawdown_pct": drawdown(returns),
        "average_loss_rub": fmean(losses) if losses else None,
        "average_loss_pct": fmean(t["scenario_net_return_pct"] for t in trades if t["scenario_net_pnl_rub"] < 0) if losses else None,
        "median_loss_rub": median(losses) if losses else None,
        "median_loss_pct": median(t["scenario_net_return_pct"] for t in trades if t["scenario_net_pnl_rub"] < 0) if losses else None,
        "worst_trade_rub": min(pnl), "worst_trade_pct": min(returns),
        "total_option_premium_rub": sum(t["option_premium_rub"] for t in trades),
        "total_option_payoff_rub": sum(t["option_payoff_rub"] for t in trades),
    }


def build_base_trades():
    long_rows = [
        r for r in read_rows(LONG_FILE, "Trades")
        if float(r["minimum_impulse_pct"]) == 0.7
    ]
    long_rows.sort(key=lambda r: r["entry_time"])
    long = []
    for r in long_rows:
        long.append({
            "trade_id": len(long) + 1, "entry_time": r["entry_time"],
            "exit_time": r["exit_time"], "entry": float(r["entry_price_continuous"]),
            "exit": float(r["exit_price_continuous"]), "base_net": float(r["net_pnl_rub"]),
        })

    short_rows = read_rows(SHORT_FILE, "Best_Trades")
    short_rows.sort(key=lambda r: r["entry_time"])
    short = []
    for r in short_rows:
        short.append({
            "trade_id": len(short) + 1, "entry_time": r["entry_time"],
            "exit_time": r["exit_time"], "entry": float(r["entry_price_continuous"]),
            "exit": float(r["exit_price_continuous"]), "base_net": float(r["net_pnl_rub"]),
        })
    return {"LONG": long, "SHORT": short}


def main():
    strategies = build_base_trades()
    summaries = []
    details = []
    for strategy, base_trades in strategies.items():
        for scenario in ("unhedged", "premium_only", "full_ATM_payoff"):
            scenario_trades = []
            for trade in base_trades:
                notional = trade["entry"] * CONTRACTS
                premium = 0.0 if scenario == "unhedged" else notional * PREMIUM_PCT / 100
                if scenario == "full_ATM_payoff":
                    if strategy == "LONG":
                        payoff = max(trade["entry"] - trade["exit"], 0.0) * CONTRACTS
                    else:
                        payoff = max(trade["exit"] - trade["entry"], 0.0) * CONTRACTS
                else:
                    payoff = 0.0
                pnl = trade["base_net"] - premium + payoff
                row = {
                    "strategy": strategy, "scenario": scenario, **trade,
                    "entry_notional_rub": notional, "option_premium_rub": premium,
                    "option_payoff_rub": payoff, "scenario_net_pnl_rub": pnl,
                    "scenario_net_return_pct": pnl / notional * 100,
                }
                scenario_trades.append(row)
                details.append(row)
            summaries.append(summarize(strategy, scenario, scenario_trades))

    wb = Workbook()
    wb.remove(wb.active)
    write_table(wb, "Summary", summaries)
    write_table(wb, "Details", details)
    write_table(wb, "Method", [
        {"item": "Premium", "definition": "0.5% of entry notional in every hedged trade"},
        {"item": "Premium only", "definition": "premium deducted; no option payoff modeled"},
        {"item": "Full ATM payoff", "definition": "idealized payoff at strategy exit: put offsets LONG downside; call offsets SHORT upside"},
        {"item": "Limitations", "definition": "assumes strike=entry, full 100-contract hedge, matching expiry/settlement and no option fees/slippage"},
    ])
    wb.save(OUTPUT)
    for row in summaries:
        print(row)
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()
