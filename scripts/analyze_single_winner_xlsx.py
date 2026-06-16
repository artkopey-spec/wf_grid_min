from __future__ import annotations

from pathlib import Path

import pandas as pd


PATH = Path("test_result_single_20260615_195656.xlsx")


def _pct(x: float) -> str:
    return f"{x:.2f}%"


def _num(x: float) -> str:
    return f"{x:.4f}"


def _print_df(title: str, df: pd.DataFrame, n: int | None = None) -> None:
    print("\n" + title)
    if n is not None:
        df = df.head(n)
    print(df.to_string(index=False))


def main() -> None:
    xl = pd.ExcelFile(PATH)
    print("SHEETS", xl.sheet_names)

    config = pd.read_excel(PATH, sheet_name="Tester_Config")
    summary = pd.read_excel(PATH, sheet_name="Summary")
    metrics_sheets = [s for s in xl.sheet_names if s.startswith("Metrics_")]
    metrics = pd.concat(
        [pd.read_excel(PATH, sheet_name=s).assign(sheet=s) for s in metrics_sheets],
        ignore_index=True,
    )
    trades = pd.read_excel(PATH, sheet_name="Trades_100")
    signals = pd.read_excel(PATH, sheet_name="Signals")
    false_start = pd.read_excel(PATH, sheet_name="false start")
    filters_summary = pd.read_excel(PATH, sheet_name="filters_summary")
    triggers = pd.read_excel(PATH, sheet_name="ZigZag_Trigger_Events")
    cycle = pd.read_excel(PATH, sheet_name="cycle")
    diag_cols = [
        "Bar Index",
        "Filter State",
        "Trigger Source",
        "Filter Allowed Entry",
        "Filter Block Reason",
        "Time Filter In Window",
        "Wakeup Regime Active",
        "Wakeup Entry All OK",
        "Wakeup Entry ATR OK",
        "Wakeup Entry ATR Ratio",
        "Wakeup Cycle Age Bars",
        "Wakeup Bars Since Fresh Candidate",
        "Wakeup Exit TTL Triggered",
        "Wakeup Exit No Fresh Candidate Triggered",
        "Wakeup Exit Reason",
        "Wakeup Position Action",
    ]
    diag = pd.read_excel(PATH, sheet_name="FilterDiagnostics_100", usecols=diag_cols)

    print("\nCONFIG_SELECTED")
    keys = [
        "trade_mode",
        "period",
        "supertrend.atr_period",
        "supertrend.multiplier",
        "execution_model",
        "commission",
        "warmup_period_auto",
        "trade_filter.wakeup_regime.entry.atr_expansion.short_window",
        "trade_filter.wakeup_regime.entry.atr_expansion.long_window",
        "trade_filter.wakeup_regime.entry.atr_expansion.min_ratio",
        "trade_filter.wakeup_regime.exit.ttl.bars",
        "trade_filter.wakeup_regime.exit.no_fresh_candidate.enabled",
        "trade_filter.wakeup_regime.exit.no_fresh_candidate.quantile",
        "trade_filter.wakeup_regime.exit.no_fresh_candidate.max_age_bars",
        "trade_filter.wakeup_regime.exit.no_fresh_candidate.timeout_bars",
        "trade_filter.time_filter.enabled",
        "trade_filter.time_filter.window",
    ]
    c2 = config[config["Parameter"].isin(keys)]
    print(c2.to_string(index=False))

    _print_df("SUMMARY", summary)
    _print_df(
        "METRICS",
        metrics[
            [
                "Period",
                "Sum PnL %",
                "Sharpe",
                "Sortino",
                "Max Drawdown",
                "CAGR",
                "Win Rate",
                "Num Trades",
                "Profit Factor",
                "Avg Trade",
            ]
        ],
    )

    print("\nTRADES_BASIC")
    print("rows", len(trades))
    print("net_sum", _num(trades["Net PnL %"].sum()))
    print("net_mean", _num(trades["Net PnL %"].mean()))
    print("net_median", _num(trades["Net PnL %"].median()))
    print("net_min", _num(trades["Net PnL %"].min()))
    print("net_max", _num(trades["Net PnL %"].max()))
    print("win_rate", _pct((trades["Net PnL %"] > 0).mean() * 100))
    print("bars_mean", _num(trades["Bars Held"].mean()))
    print("bars_median", _num(trades["Bars Held"].median()))
    print("bars_min_max", int(trades["Bars Held"].min()), int(trades["Bars Held"].max()))
    for q in [0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]:
        print(f"net_q{int(q*100)}", _num(trades["Net PnL %"].quantile(q)))
    print("gross_sum", _num(trades["Gross PnL %"].sum()))
    print("commission_sum", _num(trades["Commission %"].sum()))

    for col in [
        "Direction",
        "SuperTrend Color",
        "Entry Filter State",
        "Entry Trigger Source",
        "Wakeup Cycle Exit Reason",
        "Wakeup Position Action",
        "Trade Close Reason",
    ]:
        print("\nTRADE_COUNTS", col)
        print(trades[col].value_counts(dropna=False).to_string())
        g = trades.groupby(col, dropna=False)["Net PnL %"].agg(["count", "sum", "mean", "median", "min", "max"])
        print(g.to_string())

    print("\nTRADES_BY_BARS_HELD")
    print(trades.groupby("Bars Held")["Net PnL %"].agg(["count", "sum", "mean", "median"]).head(20).to_string())

    trades["entry_date"] = pd.to_datetime(trades["Entry Time"]).dt.to_period("M").astype(str)
    monthly = trades.groupby("entry_date")["Net PnL %"].agg(["count", "sum", "mean", "median"])
    _print_df("MONTHLY_TRADES", monthly.reset_index())

    print("\nSIGNALS")
    print("rows", len(signals))
    for col in ["Event Type", "Direction", "Filter Decision", "Filter Block Reason", "Filter Trigger Source"]:
        print("\nSIGNAL_COUNTS", col)
        print(signals[col].value_counts(dropna=False).head(20).to_string())

    print("\nFALSE_START_HEAD")
    print(false_start.head(30).to_string(index=False))

    print("\nTRIGGERS")
    print("rows", len(triggers))
    for col in ["Trigger Source", "Triggered Lifecycle Start", "Linked Trade ID", "ZigZag Mode", "Immediate Candidate Entry Used"]:
        print("\nTRIGGER_COUNTS", col)
        print(triggers[col].value_counts(dropna=False).head(20).to_string())
    if "Candidate Height %" in triggers:
        print("candidate_height_mean_median_max", _num(triggers["Candidate Height %"].mean()), _num(triggers["Candidate Height %"].median()), _num(triggers["Candidate Height %"].max()))

    print("\nCYCLE")
    print("rows", len(cycle))
    for col in ["Направление цикла", "Причина завершения"]:
        print("\nCYCLE_COUNTS", col)
        print(cycle[col].value_counts(dropna=False).to_string())
    cycle_num_cols = [
        "Баров в цикле",
        "Ног ZigZag в цикле",
        "Размер цикла, %",
        "Макс. движение по циклу, %",
        "Макс. просадка внутри цикла, %",
        "Сделок в цикле",
        "Фин результат цикла, %",
        "% сделок с положительным фин результатом в цикле",
    ]
    print(cycle[cycle_num_cols].describe().to_string())
    print("\nCYCLE_RESULT_BY_REASON")
    print(cycle.groupby("Причина завершения")["Фин результат цикла, %"].agg(["count", "sum", "mean", "median", "min", "max"]).to_string())
    print("\nCYCLE_TRADES_BY_REASON")
    print(cycle.groupby("Причина завершения")["Сделок в цикле"].agg(["count", "sum", "mean", "median", "max"]).to_string())

    print("\nDIAGNOSTICS")
    print("rows", len(diag))
    bool_like = [
        "Time Filter In Window",
        "Wakeup Regime Active",
        "Wakeup Entry All OK",
        "Wakeup Entry ATR OK",
        "Wakeup Exit TTL Triggered",
        "Wakeup Exit No Fresh Candidate Triggered",
        "Filter Allowed Entry",
    ]
    for col in bool_like:
        print("\nDIAG_COUNTS", col)
        print(diag[col].value_counts(dropna=False).to_string())
    for col in ["Filter State", "Trigger Source", "Filter Block Reason", "Wakeup Exit Reason", "Wakeup Position Action"]:
        print("\nDIAG_COUNTS", col)
        print(diag[col].value_counts(dropna=False).head(30).to_string())
    ratio = diag["Wakeup Entry ATR Ratio"].dropna()
    print("\nATR_RATIO")
    print("count", len(ratio), "mean", _num(ratio.mean()), "median", _num(ratio.median()), "min", _num(ratio.min()), "max", _num(ratio.max()))
    for q in [0.5, 0.75, 0.9, 0.95, 0.99]:
        print(f"ratio_q{int(q*100)}", _num(ratio.quantile(q)))

    print("\nFILTERS_SUMMARY")
    print(filters_summary.head(80).to_string(index=False))


if __name__ == "__main__":
    main()
