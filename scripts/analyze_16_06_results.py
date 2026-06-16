from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


BASE = Path(r"C:\Users\lizko\OneDrive\Рабочий стол\массовые прогоны\16.06\results tester")
SUMMARY = BASE / "tester_summary_20260615_191639.xlsx"
PARAM_RE = re.compile(
    r"modeD_atr_fresh_(?P<id>\d+?)_(?P<trade_mode>\w+)_"
    r"sw(?P<sw>\d+)_lw(?P<lw>\d+)_mr(?P<mr>\d+)_ttl(?P<ttl>\d+)_"
    r"q(?P<q>\d+)_age(?P<age>\d+)_to(?P<to>\d+)"
)


def _fmt4(value: float) -> str:
    return f"{value:.4f}"


def _fmt2(value: float) -> str:
    return f"{value:.2f}"


def _fmt1(value: float) -> str:
    return f"{value:.1f}"


def _load() -> tuple[pd.DataFrame, pd.DataFrame, Path]:
    parallel_csv = next(BASE.glob("*.csv"))
    df = pd.read_excel(SUMMARY)
    par = pd.read_csv(parallel_csv)
    params = df["source_file"].str.extract(PARAM_RE)
    if params.isna().any().any():
        missing = int(params.isna().any(axis=1).sum())
        raise RuntimeError(f"Could not parse params for {missing} rows")
    for col in ["id", "sw", "lw", "mr", "ttl", "q", "age", "to"]:
        params[col] = params[col].astype(int)
    params["mr"] = params["mr"] / 10
    params["q"] = params["q"] / 100
    df = pd.concat([df, params], axis=1)
    df["profit"] = df["Sum PnL %"] > 0
    return df, par, parallel_csv


def _group(df: pd.DataFrame, cols: list[str], min_n: int = 1, sort: str = "mean") -> pd.DataFrame:
    g = (
        df.groupby(cols)
        .agg(
            n=("Sum PnL %", "size"),
            mean=("Sum PnL %", "mean"),
            median=("Sum PnL %", "median"),
            profit_rate=("profit", "mean"),
            max=("Sum PnL %", "max"),
            min=("Sum PnL %", "min"),
            trades=("Num Trades", "mean"),
            dd=("Max Drawdown", "mean"),
        )
        .reset_index()
    )
    g["profit_rate"] *= 100
    return g[g["n"] >= min_n].sort_values(sort, ascending=False)


def _print_group(title: str, g: pd.DataFrame) -> None:
    print("\n" + title)
    print(
        g.to_string(
            index=False,
            formatters={
                "mean": _fmt4,
                "median": _fmt4,
                "profit_rate": _fmt2,
                "max": _fmt4,
                "min": _fmt4,
                "trades": _fmt1,
                "dd": _fmt4,
            },
        )
    )


def main() -> None:
    df, par, parallel_csv = _load()
    print("FILES")
    print("summary_csv", parallel_csv.name)
    print("summary_xlsx", SUMMARY.name)
    print("counts", {ext: len(list(BASE.glob(f"*{ext}"))) for ext in [".xlsx", ".log", ".csv", ".yaml", ".json"]})
    print("statuses", par["status"].value_counts(dropna=False).to_dict())
    print("exit_codes", par["exit_code"].value_counts(dropna=False).to_dict())
    start = pd.to_datetime(par["started_at"]).min()
    finish = pd.to_datetime(par["finished_at"]).max()
    print("start", start.isoformat(timespec="seconds"))
    print("finish", finish.isoformat(timespec="seconds"))
    print("wall_minutes", f"{(finish - start).total_seconds() / 60:.2f}")
    print("duration_mean_median_max", f"{par['duration_sec'].mean():.3f}", f"{par['duration_sec'].median():.3f}", f"{par['duration_sec'].max():.3f}")

    print("\nGRID")
    for col in ["trade_mode", "sw", "lw", "mr", "ttl", "q", "age", "to"]:
        values = sorted(df[col].unique().tolist())
        print(col, values, "n=", len(values))

    print("\nPOOL")
    print("n", len(df))
    print("sum_mean", f"{df['Sum PnL %'].mean():.4f}")
    print("sum_median", f"{df['Sum PnL %'].median():.4f}")
    print("sum_max", f"{df['Sum PnL %'].max():.4f}")
    print("sum_min", f"{df['Sum PnL %'].min():.4f}")
    print("profit_count", int(df["profit"].sum()))
    print("profit_pct", f"{df['profit'].mean() * 100:.2f}")
    print("trades_mean", f"{df['Num Trades'].mean():.2f}")
    print("trades_median", f"{df['Num Trades'].median():.2f}")
    print("dd_mean", f"{df['Max Drawdown'].mean():.4f}")
    print("dd_median", f"{df['Max Drawdown'].median():.4f}")
    print("dd_worst", f"{df['Max Drawdown'].min():.4f}")
    print("dd_best", f"{df['Max Drawdown'].max():.4f}")
    print("win_mean", f"{df['Win Rate'].mean():.4f}")
    print("win_median", f"{df['Win Rate'].median():.4f}")

    cols = ["source_file", "Sum PnL %", "Max Drawdown", "Win Rate", "Num Trades", "sw", "lw", "mr", "ttl", "q", "age", "to"]
    print("\nTOP10")
    print(df.nlargest(10, "Sum PnL %")[cols].to_string(index=False))
    print("\nBOTTOM5")
    print(df.nsmallest(5, "Sum PnL %")[cols].to_string(index=False))

    for col in ["sw", "lw", "mr", "ttl", "q", "age", "to"]:
        _print_group(f"GROUP {col}", _group(df, [col]).sort_values(col))

    _print_group("BEST ATR+TTL groups by mean (n=27 fresh each)", _group(df, ["sw", "lw", "mr", "ttl"], min_n=27).head(20))
    _print_group("WORST ATR+TTL groups by mean (n=27 fresh each)", _group(df, ["sw", "lw", "mr", "ttl"], min_n=27, sort="mean").tail(10).sort_values("mean"))
    _print_group("BEST fresh groups by mean (n=216 atr/ttl each)", _group(df, ["q", "age", "to"], min_n=216).head(20))
    _print_group("WORST fresh groups by mean (n=216 atr/ttl each)", _group(df, ["q", "age", "to"], min_n=216).tail(10).sort_values("mean"))

    for cols2 in [["sw", "lw"], ["mr", "ttl"], ["q", "age", "to"], ["lw", "mr"], ["sw", "mr"], ["lw", "ttl"], ["mr", "q"]]:
        _print_group(f"BEST combo {cols2}", _group(df, cols2).head(12))

    print("\nTOP100 distributions")
    top = df.nlargest(100, "Sum PnL %")
    for col in ["sw", "lw", "mr", "ttl", "q", "age", "to"]:
        print(col, top[col].value_counts().sort_index().to_dict())


if __name__ == "__main__":
    main()
