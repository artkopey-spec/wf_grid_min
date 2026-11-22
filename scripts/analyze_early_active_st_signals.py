"""Find causal early predictors of a future large ZZ leg in an ST segment."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from analyze_st_segment_zigzag_clusters import (
    DEFAULT_DATA,
    DEFAULT_DATES,
    DEFAULT_SIGNALS,
    build_segments,
    read_bars,
    read_dates,
    read_flips,
    validate,
)


OUTPUT = Path("results_tester_st_batch_20260828/st80_4_early_predictors_zz0p2_over0p5.xlsx")
FEATURES = [
    "Prefix Range %",
    "Aligned Return %",
    "Adverse Excursion %",
    "Realized Volatility %",
    "Path Efficiency",
    "Volume Ratio vs Prior 60m",
    "ATR80 at Start %",
    "Aligned Distance from Day Open %",
    "Start Hour",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--dates", type=Path, default=DEFAULT_DATES)
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--zz-threshold-pct", type=float, default=0.2)
    parser.add_argument("--min-leg-pct", type=float, default=0.5)
    return parser.parse_args()


def atr_rma(bars: list[dict], period: int = 80) -> np.ndarray:
    high = np.array([row["high"] for row in bars], dtype=float)
    low = np.array([row["low"] for row in bars], dtype=float)
    close = np.array([row["close"] for row in bars], dtype=float)
    tr = np.empty(len(bars), dtype=float)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum.reduce((high[1:] - low[1:], np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr = np.empty(len(bars), dtype=float)
    atr[:period] = tr[:period].mean()
    for index in range(period, len(bars)):
        atr[index] = (atr[index - 1] * (period - 1) + tr[index]) / period
    return atr


def day_opens(bars: list[dict]) -> dict:
    result = {}
    for bar in bars:
        result.setdefault(bar["time"].date(), bar["open"])
    return result


def make_rows(bars: list[dict], segments: list[dict], legs: list[dict], atr: np.ndarray, horizon: int) -> list[dict]:
    legs_by_segment = defaultdict(list)
    for leg in legs:
        legs_by_segment[leg["Segment ID"]].append(leg)
    opens = day_opens(bars)
    rows = []
    for segment in segments:
        if segment["Duration Bars"] <= horizon:
            continue  # At decision time this segment has already flipped.
        decision_index = segment["Start Index"] + horizon
        decision_time = bars[decision_index]["time"]
        segment_legs = legs_by_segment[segment["Segment ID"]]
        if any(leg["Confirmation Time"] <= decision_time for leg in segment_legs):
            continue  # The event is already known, so this is not a forecast.
        future_legs = [leg for leg in segment_legs if leg["Confirmation Time"] > decision_time]
        first_future_leg = min(future_legs, key=lambda leg: leg["Confirmation Time"], default=None)
        target = int(first_future_leg is not None)
        prefix = bars[segment["Start Index"]:decision_index + 1]
        closes = np.array([bar["close"] for bar in prefix], dtype=float)
        returns = np.diff(closes) / closes[:-1]
        direction = 1.0 if segment["ST Color"] == "GREEN" else -1.0
        start_close = closes[0]
        aligned_return = direction * (closes[-1] - start_close) / start_close * 100
        favorable = (max(bar["high"] for bar in prefix) - start_close) / start_close * 100 if direction > 0 else (start_close - min(bar["low"] for bar in prefix)) / start_close * 100
        adverse = (start_close - min(bar["low"] for bar in prefix)) / start_close * 100 if direction > 0 else (max(bar["high"] for bar in prefix) - start_close) / start_close * 100
        prior = bars[max(0, segment["Start Index"] - 60):segment["Start Index"]]
        prior_volume = np.mean([bar["volume"] for bar in prior]) if prior else 0.0
        prefix_volume = np.mean([bar["volume"] for bar in prefix[1:]]) if len(prefix) > 1 else prefix[0]["volume"]
        total_path = np.abs(np.diff(closes)).sum()
        day_open = opens[segment["Date"]]
        forward = bars[decision_index + 1:segment["End Index"] + 1]
        decision_close = closes[-1]
        if direction > 0:
            forward_mfe = (max(bar["high"] for bar in forward) - decision_close) / decision_close * 100
            forward_mae = (decision_close - min(bar["low"] for bar in forward)) / decision_close * 100
        else:
            forward_mfe = (decision_close - min(bar["low"] for bar in forward)) / decision_close * 100
            forward_mae = (max(bar["high"] for bar in forward) - decision_close) / decision_close * 100
        row = {
            "Horizon Minutes": horizon,
            "Segment ID": segment["Segment ID"],
            "Decision Time": decision_time,
            "ST Color": segment["ST Color"],
            "Decision Close": decision_close,
            "Minutes Remaining to Flip": segment["End Index"] - decision_index,
            "Forward Favorable Excursion %": max(0.0, forward_mfe),
            "Forward Adverse Excursion %": max(0.0, forward_mae),
            "Aligned Return to End Flip %": direction * (segment["End Close"] - decision_close) / decision_close * 100,
            "Future Large Leg": target,
            "Future Leg Direction": first_future_leg["Direction"] if first_future_leg else None,
            "Future Leg Aligns ST": (
                (first_future_leg["Direction"] == "UP") == (segment["ST Color"] == "GREEN")
                if first_future_leg else None
            ),
            "Prefix Range %": (max(bar["high"] for bar in prefix) - min(bar["low"] for bar in prefix)) / start_close * 100,
            "Aligned Return %": aligned_return,
            "Favorable Excursion %": max(0.0, favorable),
            "Adverse Excursion %": max(0.0, adverse),
            "Realized Volatility %": math.sqrt(float(np.sum(returns ** 2))) * 100,
            "Path Efficiency": abs(closes[-1] - closes[0]) / total_path if total_path else 0.0,
            "Volume Ratio vs Prior 60m": prefix_volume / prior_volume if prior_volume else 1.0,
            "ATR80 at Start %": atr[segment["Start Index"]] / start_close * 100,
            "Aligned Distance from Day Open %": direction * (closes[-1] - day_open) / day_open * 100,
            "Start Hour": segment["Start Flip Time"].hour + segment["Start Flip Time"].minute / 60,
            "Event Confirmation Time": first_future_leg["Confirmation Time"] if first_future_leg else None,
        }
        rows.append(row)
    return rows


def sigmoid(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, -35, 35)
    return 1 / (1 + np.exp(-value))


def fit_logistic(x: np.ndarray, y: np.ndarray, penalty: float = 0.2) -> np.ndarray:
    design = np.column_stack((np.ones(len(x)), x))
    weights = np.zeros(design.shape[1])
    for _ in range(5000):
        probability = sigmoid(design @ weights)
        gradient = design.T @ (probability - y) / len(y)
        gradient[1:] += penalty * weights[1:] / len(y)
        new = weights - 0.08 * gradient
        if np.max(np.abs(new - weights)) < 1e-9:
            weights = new
            break
        weights = new
    return weights


def auc(y: np.ndarray, score: np.ndarray) -> float:
    positives = int(y.sum())
    negatives = len(y) - positives
    if not positives or not negatives:
        return float("nan")
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=float)
    sorted_score = score[order]
    index = 0
    while index < len(score):
        end = index + 1
        while end < len(score) and sorted_score[end] == sorted_score[index]:
            end += 1
        ranks[order[index:end]] = (index + 1 + end) / 2
        index = end
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def metrics(y: np.ndarray, score: np.ndarray, top_share: float) -> dict:
    count = max(1, math.ceil(len(y) * top_share))
    selected = np.argsort(score)[-count:]
    base = float(y.mean())
    precision = float(y[selected].mean())
    return {
        "Top Share %": top_share * 100,
        "Selected": count,
        "Base Event Rate %": base * 100,
        "Selected Event Rate %": precision * 100,
        "Lift": precision / base if base else None,
        "Recall %": float(y[selected].sum() / y.sum() * 100) if y.sum() else 0.0,
    }


def train_test(rows: list[dict], horizon: int) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    rows.sort(key=lambda row: row["Decision Time"])
    split = int(len(rows) * 0.70)
    train, test = rows[:split], rows[split:]
    x_train = np.array([[row[name] for name in FEATURES] for row in train], dtype=float)
    x_test = np.array([[row[name] for name in FEATURES] for row in test], dtype=float)
    y_train = np.array([row["Future Large Leg"] for row in train], dtype=float)
    y_test = np.array([row["Future Large Leg"] for row in test], dtype=int)
    mean, std = x_train.mean(0), x_train.std(0)
    std[std == 0] = 1
    weights = fit_logistic((x_train - mean) / std, y_train)
    scores = sigmoid(np.column_stack((np.ones(len(x_test)), (x_test - mean) / std)) @ weights)
    for row, score in zip(test, scores):
        row["Out-of-Sample Score"] = float(score)

    result = [{
        "Horizon Minutes": horizon,
        "Eligible Samples": len(rows),
        "Training Samples": len(train),
        "Test Samples": len(test),
        "Train Event Rate %": float(y_train.mean() * 100),
        "Test Event Rate %": float(y_test.mean() * 100),
        "Test Event Aligns ST %": float(np.mean([row["Future Leg Aligns ST"] for row in test if row["Future Large Leg"]]) * 100),
        "Test Mean Forward MFE %": float(np.mean([row["Forward Favorable Excursion %"] for row in test])),
        "Test Forward MFE >=0.2% %": float(np.mean([row["Forward Favorable Excursion %"] >= 0.2 for row in test]) * 100),
        "Test Forward MFE >=0.3% %": float(np.mean([row["Forward Favorable Excursion %"] >= 0.3 for row in test]) * 100),
        "Test Mean Forward MAE %": float(np.mean([row["Forward Adverse Excursion %"] for row in test])),
        "Test Mean Return to End Flip %": float(np.mean([row["Aligned Return to End Flip %"] for row in test])),
        "Out-of-Sample AUC": auc(y_test, scores),
        **{f"Top 20% {key}": value for key, value in metrics(y_test, scores, 0.20).items() if key not in ("Top Share %", "Base Event Rate %")},
        **{f"Top 30% {key}": value for key, value in metrics(y_test, scores, 0.30).items() if key not in ("Top Share %", "Base Event Rate %")},
    }]
    coefficients = [{
        "Horizon Minutes": horizon,
        "Feature": name,
        "Standardized Coefficient": float(weight),
        "Absolute Rank": rank,
    } for rank, (name, weight) in enumerate(sorted(zip(FEATURES, weights[1:]), key=lambda item: abs(item[1]), reverse=True), start=1)]
    thresholds = {
        name: float(np.quantile([row[name] for row in train], 0.75))
        for name in ("Prefix Range %", "Aligned Return %", "Realized Volatility %", "Volume Ratio vs Prior 60m", "ATR80 at Start %")
    }
    rule_masks = {
        "Range Q75+": np.array([row["Prefix Range %"] >= thresholds["Prefix Range %"] for row in test]),
        "Aligned return Q75+": np.array([row["Aligned Return %"] >= thresholds["Aligned Return %"] for row in test]),
        "Range AND aligned return": np.array([
            row["Prefix Range %"] >= thresholds["Prefix Range %"] and row["Aligned Return %"] >= thresholds["Aligned Return %"] for row in test
        ]),
        "Range + aligned + (RV or volume)": np.array([
            row["Prefix Range %"] >= thresholds["Prefix Range %"]
            and row["Aligned Return %"] >= thresholds["Aligned Return %"]
            and (row["Realized Volatility %"] >= thresholds["Realized Volatility %"] or row["Volume Ratio vs Prior 60m"] >= thresholds["Volume Ratio vs Prior 60m"])
            for row in test
        ]),
        "ATR Q75+": np.array([row["ATR80 at Start %"] >= thresholds["ATR80 at Start %"] for row in test]),
    }
    rules = []
    base = float(y_test.mean())
    for name, mask in rule_masks.items():
        selected = int(mask.sum())
        event_rate = float(y_test[mask].mean()) if selected else 0.0
        selected_events = [test[index] for index in np.flatnonzero(mask) if test[index]["Future Large Leg"]]
        selected_rows = [test[index] for index in np.flatnonzero(mask)]
        rules.append({
            "Horizon Minutes": horizon,
            "Rule": name,
            "Training Cutoffs": "; ".join(f"{key} >= {value:.4f}" for key, value in thresholds.items()),
            "Test Selected": selected,
            "Test Coverage %": selected / len(test) * 100,
            "Test Event Rate %": event_rate * 100,
            "Lift vs Test Base": event_rate / base if base else None,
            "Test Recall %": float(y_test[mask].sum() / y_test.sum() * 100) if y_test.sum() else 0.0,
            "Event Aligns ST %": float(np.mean([row["Future Leg Aligns ST"] for row in selected_events]) * 100) if selected_events else None,
            "Mean Forward MFE %": float(np.mean([row["Forward Favorable Excursion %"] for row in selected_rows])) if selected_rows else None,
            "Median Forward MFE %": float(np.median([row["Forward Favorable Excursion %"] for row in selected_rows])) if selected_rows else None,
            "Forward MFE >=0.2% %": float(np.mean([row["Forward Favorable Excursion %"] >= 0.2 for row in selected_rows]) * 100) if selected_rows else None,
            "Forward MFE >=0.3% %": float(np.mean([row["Forward Favorable Excursion %"] >= 0.3 for row in selected_rows]) * 100) if selected_rows else None,
            "Mean Forward MAE %": float(np.mean([row["Forward Adverse Excursion %"] for row in selected_rows])) if selected_rows else None,
            "Mean Return to End Flip %": float(np.mean([row["Aligned Return to End Flip %"] for row in selected_rows])) if selected_rows else None,
        })
    return result, coefficients, rules, test


def quartile_lifts(rows: list[dict], horizon: int) -> list[dict]:
    result = []
    y = np.array([row["Future Large Leg"] for row in rows], dtype=float)
    base = y.mean()
    for feature in FEATURES:
        values = np.array([row[feature] for row in rows], dtype=float)
        for tail, mask in (("LOWEST 25%", values <= np.quantile(values, 0.25)), ("HIGHEST 25%", values >= np.quantile(values, 0.75))):
            rate = y[mask].mean()
            result.append({
                "Horizon Minutes": horizon,
                "Feature": feature,
                "Tail": tail,
                "Cutoff": float(np.quantile(values, 0.25 if tail.startswith("LOW") else 0.75)),
                "Samples": int(mask.sum()),
                "Event Rate %": float(rate * 100),
                "Lift vs Base": float(rate / base) if base else None,
            })
    return result


def style(sheet) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(30, max(11, max(len(str(cell.value or "")) for cell in column) + 2))


def add_rows(book: Workbook, name: str, rows: list[dict]) -> None:
    sheet = book.create_sheet(name)
    headers = list(rows[0])
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header) for header in headers])


def main() -> None:
    paths = parse_args()
    dates = read_dates(paths.dates)
    bars = read_bars(paths.data)
    flips, params = read_flips(paths.signals)
    validate(bars, flips, params)
    segments, legs = build_segments(bars, flips, dates, paths.zz_threshold_pct, paths.min_leg_pct)
    atr = atr_rma(bars)

    all_metrics, all_coefficients, all_rules, all_quartiles, all_test = [], [], [], [], []
    samples = {}
    for horizon in (10, 20, 30):
        rows = make_rows(bars, segments, legs, atr, horizon)
        metrics_rows, coefficients, rules, test = train_test(rows, horizon)
        all_metrics.extend(metrics_rows)
        all_coefficients.extend(coefficients)
        all_rules.extend(rules)
        all_quartiles.extend(quartile_lifts(test, horizon))
        all_test.extend(test)
        samples[horizon] = len(rows)

    book = Workbook()
    book.remove(book.active)
    add_rows(book, "OOS_Metrics", all_metrics)
    add_rows(book, "Model_Coefficients", all_coefficients)
    add_rows(book, "Causal_Rules_OOS", all_rules)
    add_rows(book, "Feature_Quartile_Lifts", all_quartiles)
    add_rows(book, "OOS_Predictions", all_test)
    method = book.create_sheet("Methodology")
    method.append(["Parameter", "Value"])
    for item in [
        ("Target", f"First future confirmed close-based ZZ leg >{paths.min_leg_pct:g}% before the next ST flip"),
        ("ZZ reversal threshold", f"{paths.zz_threshold_pct:g}%"),
        ("Decision horizons", "10, 20, 30 minutes after ST flip"),
        ("Eligibility", "Segment still alive at decision time; no qualifying leg confirmed yet"),
        ("Validation", "Chronological 70% train / later 30% test, no random shuffle"),
        ("Model", "L2-regularized logistic regression; features standardized on training sample only"),
        ("Important", "Descriptive research, not proof of net profitability; commissions and execution are not modeled"),
    ]:
        method.append(item)
    for sheet in book.worksheets:
        style(sheet)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm"
    paths.output.parent.mkdir(parents=True, exist_ok=True)
    book.save(paths.output)

    result = {"parameters": {"zz_threshold_pct": paths.zz_threshold_pct, "min_leg_pct": paths.min_leg_pct}, "samples": samples, "oos_metrics": all_metrics, "causal_rules_oos": all_rules, "top_coefficients": {str(h): [row for row in all_coefficients if row["Horizon Minutes"] == h][:5] for h in (10, 20, 30)}, "output": str(paths.output.resolve())}
    paths.output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
