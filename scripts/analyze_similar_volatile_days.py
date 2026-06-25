from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DONOR_ROOT = PROJECT_ROOT / "donor"
if str(DONOR_ROOT) not in sys.path:
    sys.path.insert(0, str(DONOR_ROOT))

from supertrend_optimizer.cli.tester import parse_args, run_backtest_with_df
from supertrend_optimizer.data.loader import load_ohlc_csv
from supertrend_optimizer.data.validator import validate_ohlc_data


SOURCE_CSV = PROJECT_ROOT / "data.csv"
CONFIGS_DIR = PROJECT_ROOT / "config tester"
WARMUP_DIR = PROJECT_ROOT / "similar_days_newdata_warmup"
RESULTS_DIR = PROJECT_ROOT / "similar_days_newdata_results"
WARMUP_BARS = 500
ZIGZAG_THRESHOLD = 0.0012
TOP_DAYS = 20
SCORE_FEATURES = [
    "range_pct",
    "tr_pct_median",
    "tr_pct_mean",
    "zz_legs",
    "zz_leg_median_pct",
    "zz_leg_mean_pct",
    "abs_day_return_pct",
]


@dataclass(frozen=True)
class RunArtifact:
    date: str
    summary_path: Path
    execution_log_path: Path
    failed: int
    ok: int


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_terminal_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";")
    df.columns = [str(c).strip().lower().strip("<>") for c in df.columns]
    required = {"date", "time", "open", "high", "low", "close", "vol"}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"Missing columns in {path}: {missing}")

    date_s = df["date"].astype(str).str.strip()
    time_s = df["time"].astype(str).str.strip().str.zfill(6)
    idx = pd.to_datetime(date_s + time_s, format="%Y%m%d%H%M%S", errors="raise")
    out = df[["open", "high", "low", "close", "vol"]].copy()
    for col in out.columns:
        out[col] = pd.to_numeric(out[col], errors="raise")
    out.index = pd.DatetimeIndex(idx, name="datetime")
    out = out.sort_index(kind="mergesort")
    return out


def _write_terminal_csv(df: pd.DataFrame, path: Path) -> None:
    out = pd.DataFrame(
        {
            "<DATE>": df.index.strftime("%Y%m%d"),
            "<TIME>": df.index.strftime("%H%M%S"),
            "<OPEN>": df["open"].to_numpy(),
            "<HIGH>": df["high"].to_numpy(),
            "<LOW>": df["low"].to_numpy(),
            "<CLOSE>": df["close"].to_numpy(),
            "<VOL>": df["vol"].to_numpy() if "vol" in df.columns else 0,
        }
    )
    out.to_csv(path, sep=";", index=False)


def _zigzag_leg_pcts(close: pd.Series, threshold: float) -> list[float]:
    prices = close.dropna().astype(float).to_numpy()
    if len(prices) < 2:
        return []

    pivot = float(prices[0])
    extreme = pivot
    direction = 0
    legs: list[float] = []

    for raw_price in prices[1:]:
        price = float(raw_price)
        if pivot <= 0:
            pivot = price
            extreme = price
            direction = 0
            continue

        if direction == 0:
            if price >= pivot * (1.0 + threshold):
                direction = 1
                extreme = price
            elif price <= pivot * (1.0 - threshold):
                direction = -1
                extreme = price
            continue

        if direction == 1:
            if price > extreme:
                extreme = price
            elif price <= extreme * (1.0 - threshold):
                legs.append(abs(extreme - pivot) / pivot * 100.0)
                pivot = extreme
                extreme = price
                direction = -1
        else:
            if price < extreme:
                extreme = price
            elif price >= extreme * (1.0 + threshold):
                legs.append(abs(extreme - pivot) / pivot * 100.0)
                pivot = extreme
                extreme = price
                direction = 1

    if direction != 0 and pivot > 0:
        tail = abs(extreme - pivot) / pivot
        if tail >= threshold:
            legs.append(tail * 100.0)
    return legs


def build_daily_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    dupes = int(df.index.duplicated(keep=False).sum())
    if dupes:
        df = df[~df.index.duplicated(keep="last")].sort_index()

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr_pct = tr / prev_close * 100.0

    work = df.copy()
    work["tr_pct"] = tr_pct
    work["date"] = work.index.date

    rows: list[dict[str, Any]] = []
    for day, g in work.groupby("date", sort=True):
        open_day = float(g["open"].iloc[0])
        high_day = float(g["high"].max())
        low_day = float(g["low"].min())
        close_day = float(g["close"].iloc[-1])
        legs = _zigzag_leg_pcts(g["close"], ZIGZAG_THRESHOLD)
        rows.append(
            {
                "date": str(day),
                "bars": int(len(g)),
                "open_day": open_day,
                "high_day": high_day,
                "low_day": low_day,
                "close_day": close_day,
                "range_pct": (high_day - low_day) / open_day * 100.0,
                "day_return_pct": (close_day - open_day) / open_day * 100.0,
                "abs_day_return_pct": abs((close_day - open_day) / open_day * 100.0),
                "tr_pct_median": float(g["tr_pct"].median(skipna=True)),
                "tr_pct_mean": float(g["tr_pct"].mean(skipna=True)),
                "zz_legs": int(len(legs)),
                "zz_leg_median_pct": float(pd.Series(legs).median()) if legs else 0.0,
                "zz_leg_mean_pct": float(pd.Series(legs).mean()) if legs else 0.0,
            }
        )

    daily = pd.DataFrame(rows)
    median_bars = int(daily["bars"].median())
    full_threshold = int(math.ceil(median_bars * 0.80))
    daily["is_full_day"] = daily["bars"] >= full_threshold

    full = daily[daily["is_full_day"]].copy()
    for feature in SCORE_FEATURES:
        full[f"{feature}_pct_rank"] = full[feature].rank(pct=True, method="average")
    rank_cols = [f"{f}_pct_rank" for f in SCORE_FEATURES]
    full["composite_volatility_score"] = full[rank_cols].mean(axis=1)
    full = full.sort_values(
        ["composite_volatility_score", "range_pct", "tr_pct_mean"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    full["regime_rank"] = range(1, len(full) + 1)

    daily = daily.merge(
        full[["date", "composite_volatility_score", "regime_rank"]],
        on="date",
        how="left",
    )
    daily = daily.sort_values("date").reset_index(drop=True)
    return daily, full, median_bars, full_threshold


def create_day_csvs(df: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    WARMUP_DIR.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    for row in selected.itertuples(index=False):
        day = pd.Timestamp(row.date).date()
        mask = df.index.date == day
        day_positions = [i for i, flag in enumerate(mask) if flag]
        if not day_positions:
            raise ValueError(f"No bars found for selected day {day}")
        start_pos = day_positions[0]
        end_pos = day_positions[-1] + 1
        warmup_start = max(0, start_pos - WARMUP_BARS)
        out_df = df.iloc[warmup_start:end_pos].copy()
        warmup_rows = start_pos - warmup_start
        day_rows = end_pos - start_pos
        path = WARMUP_DIR / f"newdata_{day}_warmup{WARMUP_BARS}.csv"
        _write_terminal_csv(out_df, path)

        manifest_rows.append(
            {
                "regime_rank": int(row.regime_rank),
                "date": str(day),
                "composite_volatility_score": float(row.composite_volatility_score),
                "rows_total": int(len(out_df)),
                "warmup_rows": int(warmup_rows),
                "day_rows": int(day_rows),
                "file_path": str(path.resolve()),
                "bars": int(row.bars),
                "range_pct": float(row.range_pct),
                "tr_pct_median": float(row.tr_pct_median),
                "tr_pct_mean": float(row.tr_pct_mean),
                "zz_legs": int(row.zz_legs),
                "zz_leg_median_pct": float(row.zz_leg_median_pct),
                "zz_leg_mean_pct": float(row.zz_leg_mean_pct),
                "abs_day_return_pct": float(row.abs_day_return_pct),
                "day_return_pct": float(row.day_return_pct),
            }
        )
    manifest = pd.DataFrame(manifest_rows).sort_values("regime_rank")
    manifest.to_csv(WARMUP_DIR / "manifest.csv", index=False, encoding="utf-8-sig")
    return manifest


def validate_configs() -> pd.DataFrame:
    configs = sorted(CONFIGS_DIR.glob("*.y*ml"), key=lambda p: p.name)
    if not configs:
        raise ValueError(f"No YAML configs found in {CONFIGS_DIR}")

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in configs:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        st = raw.get("supertrend", {})
        export = raw.get("export", {})
        trade_filter = raw.get("trade_filter", {})
        row = {
            "config_path": str(path.resolve()),
            "config_name": path.name,
            "atr_period": int(st.get("atr_period")),
            "multiplier": float(st.get("multiplier")),
            "warmup_period": raw.get("warmup_period"),
            "warmup_period_auto": raw.get("warmup_period_auto"),
            "export_diagnostics": export.get("diagnostics"),
            "export_signals": export.get("signals"),
            "export_false_start": export.get("false_start"),
            "export_cycle": export.get("cycle"),
            "export_trades": export.get("trades"),
            "trade_filter_enabled": trade_filter.get("enabled"),
        }
        rows.append(row)
        if row["warmup_period"] != WARMUP_BARS:
            errors.append(f"{path.name}: warmup_period={row['warmup_period']}")
        if row["warmup_period_auto"] is not False:
            errors.append(f"{path.name}: warmup_period_auto={row['warmup_period_auto']}")
        for key in [
            "export_diagnostics",
            "export_signals",
            "export_false_start",
            "export_cycle",
            "export_trades",
        ]:
            if row[key] is not False:
                errors.append(f"{path.name}: {key}={row[key]}")
        if row["trade_filter_enabled"] is not False:
            errors.append(f"{path.name}: trade_filter.enabled={row['trade_filter_enabled']}")

    config_df = pd.DataFrame(rows)
    config_df.to_csv(RESULTS_DIR / "config_grid_audit.csv", index=False, encoding="utf-8-sig")
    if errors:
        raise ValueError("Config audit failed: " + "; ".join(errors[:20]))
    return config_df


def _read_100pct_summary(xlsx_path: Path) -> dict[str, Any]:
    summary = pd.read_excel(xlsx_path, sheet_name="Summary")
    row = summary[summary["Period"].astype(str) == "100%"]
    if row.empty:
        row = summary.head(1)
    return row.iloc[0].to_dict()


def run_backtests(manifest: pd.DataFrame, config_df: pd.DataFrame, run_id: str) -> list[RunArtifact]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    artifacts: list[RunArtifact] = []

    for m in manifest.itertuples(index=False):
        date_s = str(m.date)
        csv_path = Path(m.file_path)
        day_dir = RESULTS_DIR / date_s
        day_dir.mkdir(parents=True, exist_ok=True)
        execution_rows: list[dict[str, Any]] = []
        summary_rows: list[dict[str, Any]] = []

        df_loaded = load_ohlc_csv(str(csv_path), sep=";")
        df_loaded = validate_ohlc_data(df_loaded)

        print(f"[{date_s}] running {len(config_df)} configs")
        for i, cfg in enumerate(config_df.itertuples(index=False), start=1):
            cfg_path = Path(cfg.config_path)
            stem = cfg_path.stem
            xlsx_path = day_dir / f"{i:04d}_{stem}_{run_id}.xlsx"
            log_path = day_dir / f"{i:04d}_{stem}_{run_id}.log"
            started = datetime.now().isoformat(timespec="seconds")
            t0 = perf_counter()
            status = "ok"
            error_message = ""
            actual_output = str(xlsx_path)

            with log_path.open("w", encoding="utf-8") as log_file:
                with contextlib.redirect_stdout(log_file), contextlib.redirect_stderr(log_file):
                    try:
                        tester_args = parse_args(
                            [
                                "--csv",
                                str(csv_path),
                                "--config",
                                str(cfg_path),
                                "--out",
                                str(xlsx_path),
                                "--exact-output-path",
                            ]
                        )
                        actual_output = run_backtest_with_df(
                            tester_args,
                            df_loaded.copy(deep=True),
                            csv_path_for_metadata=str(csv_path),
                        )
                    except Exception as exc:
                        status = "failed"
                        error_message = f"{type(exc).__name__}: {exc}"
                        traceback.print_exc()

            if status == "ok":
                try:
                    summary_row = _read_100pct_summary(Path(actual_output))
                    summary_row["date"] = date_s
                    summary_row["source_xlsx"] = str(Path(actual_output).resolve())
                    summary_row["config_path"] = str(cfg_path.resolve())
                    summary_rows.append(summary_row)
                except Exception as exc:
                    status = "failed"
                    error_message = f"summary read failed: {type(exc).__name__}: {exc}"

            execution_rows.append(
                {
                    "date": date_s,
                    "config_index": i,
                    "config_path": str(cfg_path.resolve()),
                    "status": status,
                    "started_at": started,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "duration_sec": round(perf_counter() - t0, 3),
                    "output_path": str(Path(actual_output).resolve()),
                    "log_path": str(log_path.resolve()),
                    "error_message": error_message,
                }
            )
            if i % 10 == 0 or i == len(config_df):
                ok_count = sum(1 for r in execution_rows if r["status"] == "ok")
                print(f"[{date_s}] {i}/{len(config_df)} done, ok={ok_count}")

        exec_df = pd.DataFrame(execution_rows)
        execution_log_path = day_dir / f"execution_log_{date_s}.csv"
        exec_df.to_csv(execution_log_path, index=False, encoding="utf-8-sig")

        summary_df = pd.DataFrame(summary_rows)
        summary_path = day_dir / f"tester_summary_{date_s}.xlsx"
        with pd.ExcelWriter(summary_path, engine="openpyxl") as writer:
            summary_df.to_excel(writer, sheet_name="summary", index=False)
            exec_df.to_excel(writer, sheet_name="execution_log", index=False)

        artifacts.append(
            RunArtifact(
                date=date_s,
                summary_path=summary_path,
                execution_log_path=execution_log_path,
                failed=int((exec_df["status"] != "ok").sum()),
                ok=int((exec_df["status"] == "ok").sum()),
            )
        )
    return artifacts


def build_stability(
    selected: pd.DataFrame,
    artifacts: list[RunArtifact],
    run_id: str,
) -> tuple[Path, Path, pd.DataFrame, dict[str, Any]]:
    per_day_frames: list[pd.DataFrame] = []
    for artifact in artifacts:
        df = pd.read_excel(artifact.summary_path, sheet_name="summary")
        per_day_frames.append(df)
    per_day = pd.concat(per_day_frames, ignore_index=True)
    per_day["date"] = per_day["date"].astype(str)
    per_day["ATR Period"] = pd.to_numeric(per_day["ATR Period"])
    per_day["Multiplier"] = pd.to_numeric(per_day["Multiplier"])
    per_day["Sum PnL %"] = pd.to_numeric(per_day["Sum PnL %"])
    per_day["Max Drawdown"] = pd.to_numeric(per_day["Max Drawdown"])
    per_day["Num Trades"] = pd.to_numeric(per_day["Num Trades"])
    per_day["day_rank"] = per_day.groupby("date")["Sum PnL %"].rank(
        ascending=False, method="min"
    )

    all_summary_path = RESULTS_DIR / f"tester_summary_ALL_{run_id}.xlsx"
    with pd.ExcelWriter(all_summary_path, engine="openpyxl") as writer:
        per_day.to_excel(writer, sheet_name="all_summary", index=False)

    grouped = per_day.groupby(["ATR Period", "Multiplier"], as_index=False)
    stability = grouped.agg(
        n_days=("date", "nunique"),
        positive_days=("Sum PnL %", lambda s: int((s > 0).sum())),
        total_pnl=("Sum PnL %", "sum"),
        mean_pnl=("Sum PnL %", "mean"),
        median_pnl=("Sum PnL %", "median"),
        q25_pnl=("Sum PnL %", lambda s: float(s.quantile(0.25))),
        min_pnl=("Sum PnL %", "min"),
        max_pnl=("Sum PnL %", "max"),
        std_pnl=("Sum PnL %", "std"),
        avg_rank=("day_rank", "mean"),
        median_rank=("day_rank", "median"),
        worst_rank=("day_rank", "max"),
        median_dd=("Max Drawdown", "median"),
        worst_dd=("Max Drawdown", "max"),
        median_trades=("Num Trades", "median"),
        total_trades=("Num Trades", "sum"),
    )
    stability["positive_ratio"] = stability["positive_days"] / stability["n_days"]
    stability["std_pnl"] = stability["std_pnl"].fillna(0.0)
    stability = stability.sort_values(
        ["positive_days", "median_pnl", "min_pnl", "avg_rank", "total_pnl"],
        ascending=[False, False, False, True, False],
    ).reset_index(drop=True)
    stability.insert(0, "stability_rank", range(1, len(stability) + 1))

    top_total = stability.sort_values(
        ["total_pnl", "positive_days", "median_pnl"], ascending=[False, False, False]
    ).reset_index(drop=True)
    top_total.insert(0, "total_pnl_rank", range(1, len(top_total) + 1))

    top_median = stability.sort_values(
        ["median_pnl", "positive_days", "min_pnl"], ascending=[False, False, False]
    ).reset_index(drop=True)
    top_median.insert(0, "median_pnl_rank", range(1, len(top_median) + 1))

    selected_days = selected.sort_values("regime_rank").copy()
    workbook_path = RESULTS_DIR / f"newdata_param_stability_20days_{run_id}.xlsx"
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        stability.to_excel(writer, sheet_name="param_stability", index=False)
        per_day.to_excel(writer, sheet_name="per_day_results", index=False)
        selected_days.to_excel(writer, sheet_name="selected_days", index=False)
        top_total.to_excel(writer, sheet_name="top_by_total_pnl", index=False)
        top_median.to_excel(writer, sheet_name="top_by_median_pnl", index=False)

    plateau = analyze_plateau(stability)
    plateau_path = RESULTS_DIR / f"plateau_analysis_{run_id}.json"
    plateau_path.write_text(
        json.dumps(plateau, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return all_summary_path, workbook_path, stability, plateau


def _close_enough(value: float, leader: float, frac: float, absolute: float = 0.0) -> bool:
    tolerance = max(abs(leader) * frac, absolute)
    return value >= leader - tolerance


def analyze_plateau(stability: pd.DataFrame) -> dict[str, Any]:
    leader = stability.iloc[0]
    atr_values = sorted(stability["ATR Period"].unique())
    mult_values = sorted(stability["Multiplier"].unique())
    atr = leader["ATR Period"]
    mult = leader["Multiplier"]
    atr_i = atr_values.index(atr)
    mult_i = mult_values.index(mult)
    neighbor_atrs = set(atr_values[max(0, atr_i - 1) : min(len(atr_values), atr_i + 2)])
    neighbor_mults = set(mult_values[max(0, mult_i - 1) : min(len(mult_values), mult_i + 2)])
    neighborhood = stability[
        stability["ATR Period"].isin(neighbor_atrs)
        & stability["Multiplier"].isin(neighbor_mults)
    ].copy()
    neighbors = neighborhood[
        ~(
            (neighborhood["ATR Period"] == atr)
            & (neighborhood["Multiplier"] == mult)
        )
    ].copy()

    rank_tolerance = max(5.0, len(stability) * 0.10)
    close_rows: list[dict[str, Any]] = []
    for _, row in neighbors.iterrows():
        row_d = row.to_dict()
        ok = (
            row_d["positive_days"] >= int(leader["positive_days"]) - 1
            and _close_enough(float(row_d["median_pnl"]), float(leader["median_pnl"]), 0.20, 0.05)
            and _close_enough(float(row_d["total_pnl"]), float(leader["total_pnl"]), 0.20, 0.10)
            and _close_enough(float(row_d["min_pnl"]), float(leader["min_pnl"]), 0.25, 0.05)
            and float(row_d["avg_rank"]) <= float(leader["avg_rank"]) + rank_tolerance
        )
        row_d["diff_positive_days"] = int(row_d["positive_days"] - leader["positive_days"])
        row_d["diff_median_pnl"] = float(row_d["median_pnl"] - leader["median_pnl"])
        row_d["diff_total_pnl"] = float(row_d["total_pnl"] - leader["total_pnl"])
        row_d["diff_min_pnl"] = float(row_d["min_pnl"] - leader["min_pnl"])
        row_d["diff_avg_rank"] = float(row_d["avg_rank"] - leader["avg_rank"])
        row_d["close_to_leader"] = bool(ok)
        if ok:
            close_rows.append(row_d)

    plateau_confirmed = len(close_rows) >= 2
    if close_rows:
        atr_min = min(float(r["ATR Period"]) for r in close_rows + [leader.to_dict()])
        atr_max = max(float(r["ATR Period"]) for r in close_rows + [leader.to_dict()])
        mult_min = min(float(r["Multiplier"]) for r in close_rows + [leader.to_dict()])
        mult_max = max(float(r["Multiplier"]) for r in close_rows + [leader.to_dict()])
        clean_close_rows = json.loads(pd.DataFrame(close_rows).to_json(orient="records"))
    else:
        atr_min = atr_max = float(atr)
        mult_min = mult_max = float(mult)
        clean_close_rows = []

    return {
        "leader": {
            "atr_period": int(leader["ATR Period"]),
            "multiplier": float(leader["Multiplier"]),
            "positive_days": int(leader["positive_days"]),
            "n_days": int(leader["n_days"]),
            "total_pnl": float(leader["total_pnl"]),
            "median_pnl": float(leader["median_pnl"]),
            "q25_pnl": float(leader["q25_pnl"]),
            "min_pnl": float(leader["min_pnl"]),
            "avg_rank": float(leader["avg_rank"]),
            "median_dd": float(leader["median_dd"]),
            "median_trades": float(leader["median_trades"]),
        },
        "plateau_confirmed": plateau_confirmed,
        "close_neighbor_count": len(close_rows),
        "plateau_atr_range": [atr_min, atr_max],
        "plateau_multiplier_range": [mult_min, mult_max],
        "close_neighbors": clean_close_rows,
        "neighborhood": json.loads(neighbors.to_json(orient="records")),
        "criteria": {
            "positive_days": "leader - 1 or better",
            "median_pnl": "within 20% of leader, at least 0.05 absolute tolerance",
            "total_pnl": "within 20% of leader, at least 0.10 absolute tolerance",
            "min_pnl": "within 25% of leader, at least 0.05 absolute tolerance",
            "avg_rank": f"leader avg_rank + {rank_tolerance:.2f} or better",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-backtests", action="store_true")
    args = parser.parse_args()

    run_id = _now_id()
    WARMUP_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading data.csv")
    df = _read_terminal_csv(SOURCE_CSV)
    duplicate_datetimes = int(df.index.duplicated(keep=False).sum())
    print(f"Rows read: {len(df)}")
    print(f"Date range: {df.index.min()} -> {df.index.max()}")
    print(f"Duplicate datetimes: {duplicate_datetimes}")

    print("Building daily features")
    daily, full, median_bars, full_threshold = build_daily_features(df)
    daily_features_path = WARMUP_DIR / "daily_features.csv"
    daily.to_csv(daily_features_path, index=False, encoding="utf-8-sig")
    selected = full.head(min(TOP_DAYS, len(full))).copy()
    print(f"Median bars/day: {median_bars}; full-day threshold: {full_threshold}")
    print(f"Full days: {len(full)}; selected: {len(selected)}")

    manifest = create_day_csvs(df, selected)
    print(f"Daily features: {daily_features_path}")
    print(f"Manifest: {WARMUP_DIR / 'manifest.csv'}")

    print("Auditing configs")
    config_df = validate_configs()
    print(f"Configs: {len(config_df)}")

    if args.skip_backtests:
        print("Skipping backtests by request")
        return 0

    artifacts = run_backtests(manifest, config_df, run_id)
    all_summary_path, workbook_path, stability, plateau = build_stability(
        selected, artifacts, run_id
    )

    report = {
        "run_id": run_id,
        "rows_read": len(df),
        "date_min": str(df.index.min()),
        "date_max": str(df.index.max()),
        "duplicate_datetimes": duplicate_datetimes,
        "median_bars": median_bars,
        "full_day_threshold": full_threshold,
        "full_days": len(full),
        "selected_days": selected["date"].astype(str).tolist(),
        "daily_features_path": str(daily_features_path.resolve()),
        "manifest_path": str((WARMUP_DIR / "manifest.csv").resolve()),
        "warmup_dir": str(WARMUP_DIR.resolve()),
        "results_dir": str(RESULTS_DIR.resolve()),
        "all_summary_path": str(all_summary_path.resolve()),
        "stability_workbook_path": str(workbook_path.resolve()),
        "plateau": plateau,
        "artifacts": [
            {
                "date": a.date,
                "summary_path": str(a.summary_path.resolve()),
                "execution_log_path": str(a.execution_log_path.resolve()),
                "ok": a.ok,
                "failed": a.failed,
            }
            for a in artifacts
        ],
    }
    report_path = RESULTS_DIR / f"run_report_{run_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"All summary: {all_summary_path}")
    print(f"Stability workbook: {workbook_path}")
    print(f"Run report: {report_path}")
    print("Top 10 stability:")
    cols = [
        "stability_rank",
        "ATR Period",
        "Multiplier",
        "positive_days",
        "n_days",
        "total_pnl",
        "median_pnl",
        "q25_pnl",
        "min_pnl",
        "avg_rank",
        "median_dd",
        "median_trades",
    ]
    print(stability[cols].head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
