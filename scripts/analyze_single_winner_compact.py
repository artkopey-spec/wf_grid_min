from __future__ import annotations

import pandas as pd


P = "test_result_single_20260615_195656.xlsx"


def main() -> None:
    cycle = pd.read_excel(P, sheet_name="cycle")
    print("CYCLE rows", len(cycle))
    print(cycle["Причина завершения"].value_counts(dropna=False).to_string())
    print("\nCYCLE_RESULT_BY_REASON")
    print(
        cycle.groupby("Причина завершения")["Фин результат цикла, %"]
        .agg(["count", "sum", "mean", "median", "min", "max"])
        .to_string()
    )
    print("\nCYCLE_TRADES_BY_REASON")
    print(
        cycle.groupby("Причина завершения")["Сделок в цикле"]
        .agg(["count", "sum", "mean", "median", "max"])
        .to_string()
    )
    print("\nCYCLE_DESCRIBE")
    print(
        cycle[
            [
                "Баров в цикле",
                "Сделок в цикле",
                "Фин результат цикла, %",
                "Макс. просадка внутри цикла, %",
            ]
        ]
        .describe()
        .to_string()
    )

    diag = pd.read_excel(
        P,
        sheet_name="FilterDiagnostics_100",
        usecols=[
            "Wakeup Exit TTL Triggered",
            "Wakeup Exit No Fresh Candidate Triggered",
            "Wakeup Exit Reason",
            "Wakeup Position Action",
            "Wakeup Regime Active",
            "Wakeup Entry All OK",
            "Wakeup Entry ATR OK",
            "Wakeup Entry ATR Ratio",
            "Wakeup Cycle Age Bars",
            "Wakeup Bars Since Fresh Candidate",
        ],
    )
    print("\nDIAG rows", len(diag))
    for col in [
        "Wakeup Regime Active",
        "Wakeup Entry All OK",
        "Wakeup Entry ATR OK",
        "Wakeup Exit TTL Triggered",
        "Wakeup Exit No Fresh Candidate Triggered",
        "Wakeup Exit Reason",
        "Wakeup Position Action",
    ]:
        print("\n" + col)
        print(diag[col].value_counts(dropna=False).head(20).to_string())

    ratio = diag["Wakeup Entry ATR Ratio"].dropna()
    print("\nATR_RATIO")
    print("count", len(ratio))
    print("mean", ratio.mean())
    print("median", ratio.median())
    print("min", ratio.min())
    print("max", ratio.max())
    print(ratio.quantile([0.5, 0.75, 0.9, 0.95, 0.99]).to_string())

    active = diag[diag["Wakeup Regime Active"] == 1]
    print("\nACTIVE_AGE")
    print(active[["Wakeup Cycle Age Bars", "Wakeup Bars Since Fresh Candidate"]].describe().to_string())


if __name__ == "__main__":
    main()
