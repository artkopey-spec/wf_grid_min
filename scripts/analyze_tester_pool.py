from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd


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
    parser = argparse.ArgumentParser()
    parser.add_argument("results_dir", type=Path)
    args = parser.parse_args()
    base = args.results_dir
    summary = next(p for p in base.glob("tester_summary_*.xlsx") if not p.name.startswith("~$"))
    parallel = next(base.glob("tester_parallel_summary_*.csv"))

    df = pd.read_excel(summary)
    par = pd.read_csv(parallel)
    params = df["source_file"].str.extract(PARAM_RE)
    if params.isna().any().any():
        raise RuntimeError(f"could not parse params for {params.isna().any(axis=1).sum()} rows")
    for col in ["id", "sw", "lw", "mr", "ttl", "q", "age", "to"]:
        params[col] = params[col].astype(int)
    params["mr"] = params["mr"] / 10
    params["q"] = params["q"] / 100
    df = pd.concat([df, params], axis=1)
    df["profit"] = df["Sum PnL %"] > 0

    print("FILES")
    print("summary_csv", parallel.name)
    print("summary_xlsx", summary.name)
    print("counts", {ext: len(list(base.glob(f"*{ext}"))) for ext in [".xlsx", ".log", ".csv", ".yaml", ".json"]})
    print("statuses", par["status"].value_counts(dropna=False).to_dict())
    print("exit_codes", par["exit_code"].value_counts(dropna=False).to_dict())
    start = pd.to_datetime(par["started_at"]).min()
    finish = pd.to_datetime(par["finished_at"]).max()
    print("start", start.isoformat(timespec="seconds"))
    print("finish", finish.isoformat(timespec="seconds"))
    print("wall_minutes", f"{(finish - start).total_seconds() / 60:.2f}")

    print("\nGRID")
    for col in ["trade_mode", "sw", "lw", "mr", "ttl", "q", "age", "to"]:
        vals = sorted(df[col].unique().tolist())
        print(col, vals, "n=", len(vals))

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

    cols = ["source_file", "Sum PnL %", "Max Drawdown", "Win Rate", "Num Trades", "sw", "lw", "mr", "ttl", "q", "age", "to"]
    print("\nTOP10")
    print(df.nlargest(10, "Sum PnL %")[cols].to_string(index=False))

    for col in ["sw", "lw", "mr", "ttl", "q", "age", "to"]:
        _print_group(f"GROUP {col}", _group(df, [col]).sort_values(col))

    _print_group("BEST ATR+TTL groups", _group(df, ["sw", "lw", "mr", "ttl"], min_n=27).head(15))
    _print_group("BEST fresh groups", _group(df, ["q", "age", "to"], min_n=216).head(12))
    _print_group("BEST mr+ttl", _group(df, ["mr", "ttl"]).head(9))
    _print_group("BEST sw+lw", _group(df, ["sw", "lw"]).head(12))

    print("\nTOP100 distributions")
    top = df.nlargest(100, "Sum PnL %")
    for col in ["sw", "lw", "mr", "ttl", "q", "age", "to"]:
        print(col, top[col].value_counts().sort_index().to_dict())


if __name__ == "__main__":
    main()
