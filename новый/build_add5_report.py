from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt


ROOT = Path(os.environ["MIX_SOURCE_ROOT"])
WORK = Path(os.environ.get("MIX_ADD5_WORK", Path.cwd() / "mix_add5_output"))
FINAL_XLSX = ROOT / "ПРЕЗЕНТАЦИЯ СТРАТЕГИИ" / "MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx"
DMI_XLSX = ROOT / "MIX_ST_19_4_DMI_reduced_position_backtest.xlsx"
DATA_CSV = ROOT / "data.csv"
LONG10_XLSX = Path(os.environ["MIX_LONG10_XLSX"])
BASE_PPTX = Path(os.environ["MIX_ADD5_BASE_PPTX"])
OUT_XLSX = WORK / "MIX_ST_19_4_LONG10_ADD5_P60_FINAL_BACKTEST.xlsx"
OUT_PPTX = WORK / "MIX_ST_19_4_FINAL_AUDITED_LONG10_ADD5.pptx"

FEE = 0.0000666667
SPLIT = pd.Timestamp("2025-10-15 13:26:00")
BG, PANEL, GRID = "0B2230", "102C3B", "274857"
TEXT, MUTED = "EAF2F5", "A9BCC5"
TEAL, ORANGE, RED, BLUE = "27D3C2", "FFB257", "FF7373", "69A7FF"


def profit_factor(values):
    values = np.asarray(values, dtype=float)
    losses = -values[values < 0].sum()
    return float(values[values > 0].sum() / losses) if losses else np.nan


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def longest_streak(values, positive=False):
    best = current = 0
    for value in values:
        hit = value > 0 if positive else value < 0
        current = current + 1 if hit else 0
        best = max(best, current)
    return best


def load_sources():
    bars = pd.read_csv(DATA_CSV, sep=";")
    bars["dt"] = pd.to_datetime(
        bars["<DATE>"].astype(str) + bars["<TIME>"].astype(str).str.zfill(6),
        format="%Y%m%d%H%M%S",
    )
    bars = bars.set_index("dt").sort_index()

    final = pd.read_excel(FINAL_XLSX, sheet_name="Trades")
    for column in ("signal_time", "first_entry_time", "exit_time"):
        final[column] = pd.to_datetime(final[column])
    dmi = pd.read_excel(DMI_XLSX, sheet_name="All_Variant_Trades")
    dmi = dmi[dmi["size_variant"] == "Baseline 10 + 10"].drop_duplicates("movement_id")
    dmi = dmi[["movement_id", "leg_04_price", "breakout_price"]]
    merged = final.merge(dmi, on="movement_id", how="left", validate="one_to_one")
    return bars, merged


def breakout_time(row, bars):
    window = bars.loc[row["first_entry_time"]:row["exit_time"]]
    hits = window.index[np.isclose(window["<OPEN>"].astype(float), float(row["breakout_price"]))]
    if not len(hits):
        raise RuntimeError(f"Breakout time not found for {row['movement_id']}")
    return hits[0]


def adjusted_bar(bar, timestamp, row, bars):
    values = {name: float(bar[name]) for name in ("<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>")}
    # One published trade spans a quarterly roll. Preserve the source
    # back-adjustment on the first new-session bar.
    if timestamp.normalize() > pd.Timestamp(row["first_entry_time"]).normalize():
        published_exit_time = pd.Timestamp(row["exit_time"])
        if row["exit_reason"] == "first_bar_close" and published_exit_time in bars.index:
            raw_close = float(bars.loc[published_exit_time, "<CLOSE>"])
            offset = float(row["exit_price"]) - raw_close
            if abs(offset) > 1e-9:
                values = {key: value + offset for key, value in values.items()}
    return values


def replay(row, bars, add_qty):
    entry_time = pd.Timestamp(row["first_entry_time"])
    first_price = float(row["leg_04_price"])
    second_price = float(row["breakout_price"])
    second_time = breakout_time(row, bars)
    average = (10 * first_price + add_qty * second_price) / (10 + add_qty)
    qty = 10 + add_qty
    entries = [(10, first_price), (add_qty, second_price)] if add_qty else [(10, first_price)]
    checked_30m = second_time > entry_time + pd.Timedelta(minutes=30)

    future = bars.loc[second_time:]
    next_day = future.index.normalize() > entry_time.normalize()
    if not next_day.any():
        raise RuntimeError(f"No next session for {row['movement_id']}")
    future = future.loc[:future.index[next_day][0]]

    exit_time = exit_price = exit_reason = None
    for index, (timestamp, raw_bar) in enumerate(future.iterrows()):
        bar = adjusted_bar(raw_bar, timestamp, row, bars)
        stop = average * 0.985
        is_new_session = timestamp.normalize() > entry_time.normalize()
        if index > 0 and bar["<OPEN>"] <= stop:
            exit_time, exit_price, exit_reason = timestamp, bar["<OPEN>"], "stop_gap"
            break
        if bar["<LOW>"] <= stop:
            exit_time, exit_price, exit_reason = timestamp, stop, "stop"
            break
        if is_new_session:
            exit_time, exit_price, exit_reason = timestamp, bar["<CLOSE>"], "first_bar_close"
            break
        if not checked_30m and timestamp >= entry_time + pd.Timedelta(minutes=30):
            checked_30m = True
            if bar["<CLOSE>"] <= average:
                exit_time, exit_price, exit_reason = timestamp, bar["<CLOSE>"], "weak_30m"
                break

    if exit_time is None:
        raise RuntimeError(f"No exit for {row['movement_id']}")
    gross = sum(quantity * (exit_price - price) for quantity, price in entries)
    commission = FEE * (
        sum(quantity * price for quantity, price in entries) + qty * exit_price
    )
    pnl = gross - commission
    return_pct = 100 * pnl / (10 * first_price)
    return {
        "second_leg": bool(add_qty),
        "add_qty": add_qty,
        "contracts": qty,
        "weighted_average_entry": average,
        "breakout_time": second_time,
        "breakout_price": second_price,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "net_pnl_rub_10": pnl,
        "return_pct": return_pct,
    }


def stats(name, frame):
    frame = frame.sort_values("signal_time")
    ret = frame["return_pct"].to_numpy(float)
    pnl = frame["net_pnl_rub_10"].to_numpy(float)
    return {
        "segment": name,
        "trades": len(frame),
        "adds": int(frame["second_leg"].sum()),
        "max_contracts": int(frame["contracts"].max()),
        "win_rate_pct": 100 * np.mean(ret > 0),
        "average_return_pct": ret.mean(),
        "median_return_pct": np.median(ret),
        "sum_return_points": ret.sum(),
        "profit_factor_rub": profit_factor(pnl),
        "closed_trade_max_dd_points": max_drawdown(ret),
        "best_trade_pct": ret.max(),
        "worst_trade_pct": ret.min(),
        "net_pnl_rub": pnl.sum(),
        "closed_trade_max_dd_rub": max_drawdown(pnl),
        "max_consecutive_losses": longest_streak(ret),
        "max_consecutive_wins": longest_streak(ret, positive=True),
    }


def calculate():
    bars, source = load_sources()
    add5 = source.copy()
    add5["add_qty"] = np.where(add5["second_leg"].astype(bool), 5, 0)
    add5["replayed"] = False

    validation_rows = []
    for index, row in add5[add5["second_leg"].astype(bool)].iterrows():
        replay10 = replay(row, bars, add_qty=10)
        validation_rows.append({
            "movement_id": int(row["movement_id"]),
            "published_exit_time": row["exit_time"],
            "replay10_exit_time": replay10["exit_time"],
            "published_exit_price": row["exit_price"],
            "replay10_exit_price": replay10["exit_price"],
            "published_pnl": row["net_pnl_rub_10"],
            "replay10_pnl": replay10["net_pnl_rub_10"],
            "exit_time_match": pd.Timestamp(row["exit_time"]) == replay10["exit_time"],
            "exit_price_match": abs(float(row["exit_price"]) - replay10["exit_price"]) < 1e-6,
            "pnl_match": abs(float(row["net_pnl_rub_10"]) - replay10["net_pnl_rub_10"]) < 1.0,
        })
        replay5 = replay(row, bars, add_qty=5)
        for key, value in replay5.items():
            add5.at[index, key] = value
        add5.at[index, "replayed"] = True

    validation = pd.DataFrame(validation_rows)
    add5 = add5.sort_values("signal_time").reset_index(drop=True)
    train = add5[add5["signal_time"] <= SPLIT]
    oos = add5[add5["signal_time"] > SPLIT]
    summary = pd.DataFrame([stats("Full", add5), stats("Train", train), stats("OOS", oos)])
    yearly = pd.DataFrame([
        {"year": int(year), **stats(str(year), frame)}
        for year, frame in add5.groupby(add5["signal_time"].dt.year)
    ])
    exits = pd.DataFrame([
        {"exit_reason": reason, **stats(str(reason), frame)}
        for reason, frame in add5.groupby("exit_reason", sort=False)
    ])

    equity = add5[["movement_id", "signal_time", "first_entry_time", "exit_time", "return_pct", "net_pnl_rub_10"]].copy()
    equity["cumulative_return_points"] = equity["return_pct"].cumsum()
    equity["cumulative_pnl_rub"] = equity["net_pnl_rub_10"].cumsum()
    equity["drawdown_points"] = equity["cumulative_return_points"] - np.maximum.accumulate(np.r_[0.0, equity["cumulative_return_points"]])[-len(equity):]
    equity["drawdown_rub"] = equity["cumulative_pnl_rub"] - np.maximum.accumulate(np.r_[0.0, equity["cumulative_pnl_rub"]])[-len(equity):]

    current = pd.read_excel(FINAL_XLSX, sheet_name="Summary")
    long10 = pd.read_excel(LONG10_XLSX, sheet_name="Summary")
    comparison = pd.DataFrame([
        {
            "variant": "Long10",
            "trades": int(long10.iloc[0]["trades"]),
            "adds": 0,
            "max_contracts": 10,
            "sum_return_points": long10.iloc[0]["sum_return_points"],
            "average_return_pct": long10.iloc[0]["average_return_pct"],
            "profit_factor_rub": long10.iloc[0]["profit_factor_rub"],
            "closed_trade_max_dd_points": long10.iloc[0]["closed_trade_max_dd_points"],
            "worst_trade_pct": long10.iloc[0]["worst_trade_pct"],
            "oos_sum_return_points": long10.iloc[2]["sum_return_points"],
            "oos_profit_factor_rub": long10.iloc[2]["profit_factor_rub"],
            "oos_closed_trade_max_dd_points": long10.iloc[2]["closed_trade_max_dd_points"],
        },
        {
            "variant": "Long10 +5 by P60",
            "trades": 124,
            "adds": int(summary.iloc[0]["adds"]),
            "max_contracts": 15,
            "sum_return_points": summary.iloc[0]["sum_return_points"],
            "average_return_pct": summary.iloc[0]["average_return_pct"],
            "profit_factor_rub": summary.iloc[0]["profit_factor_rub"],
            "closed_trade_max_dd_points": summary.iloc[0]["closed_trade_max_dd_points"],
            "worst_trade_pct": summary.iloc[0]["worst_trade_pct"],
            "oos_sum_return_points": summary.iloc[2]["sum_return_points"],
            "oos_profit_factor_rub": summary.iloc[2]["profit_factor_rub"],
            "oos_closed_trade_max_dd_points": summary.iloc[2]["closed_trade_max_dd_points"],
        },
        {
            "variant": "Long10 +10 by P60",
            "trades": int(current.iloc[0]["trades"]),
            "adds": int(current.iloc[0]["adds"]),
            "max_contracts": 20,
            "sum_return_points": current.iloc[0]["sum_return_pct"],
            "average_return_pct": current.iloc[0]["average_return_pct"],
            "profit_factor_rub": current.iloc[0]["profit_factor"],
            "closed_trade_max_dd_points": current.iloc[0]["max_drawdown_pct"],
            "worst_trade_pct": current.iloc[0]["worst_trade_pct"],
            "oos_sum_return_points": current.iloc[0]["test_sum_return_pct"],
            "oos_profit_factor_rub": current.iloc[0]["test_profit_factor"],
            "oos_closed_trade_max_dd_points": current.iloc[0]["test_max_drawdown_pct"],
        },
    ])
    return add5, summary, yearly, exits, equity, validation, comparison


def save_workbook(add5, summary, yearly, exits, equity, validation, comparison):
    method = pd.DataFrame([
        ["Position", "Long10; on P60-approved confirmed breakout add exactly +5; maximum 15 contracts"],
        ["Volume", "Same frozen train P60 threshold 2.922962 as final strategy"],
        ["Common rules", "0.7-2.5%, above Open, entry before 15:00, 1.5% stop, 30m weakness, first new-session Close"],
        ["Recalculation", "All 34 affected trades replayed from exact first and breakout prices"],
        ["Unchanged", "90 trades without an actual P60 add retain exact published P&L"],
        ["Costs", "Commission included; slippage not included"],
        ["OOS", f"signal_time > {SPLIT}"],
    ], columns=["item", "definition"])
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        comparison.to_excel(writer, sheet_name="Comparison", index=False)
        add5.to_excel(writer, sheet_name="Trades", index=False)
        yearly.to_excel(writer, sheet_name="Yearly", index=False)
        exits.to_excel(writer, sheet_name="Exit_Reasons", index=False)
        equity.to_excel(writer, sheet_name="Equity", index=False)
        validation.to_excel(writer, sheet_name="Replay_Validation", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)


def rgb(value):
    return RGBColor.from_string(value)


def text_box(slide, x, y, w, h, text, size=14, color=TEXT, bold=False, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    p = frame.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = rgb(color)
    return box


def panel(slide, x, y, w, h, fill=PANEL, line=GRID):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line)
    return shape


def add_slide(comparison, summary):
    prs = Presentation(BASE_PPTX)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(BG)
    text_box(slide, 0.60, 0.35, 11.4, 0.43, "Компромисс размера: Long10 +5 по текущему P60", 23.5, TEXT, True)
    text_box(slide, 0.62, 0.84, 11.3, 0.30, "34 подтверждённых добавления пересчитаны до максимальной позиции 15 контрактов", 10.5, MUTED)
    text_box(slide, 12.18, 0.39, 0.45, 0.24, f"{len(prs.slides):02d}", 9, MUTED, False, PP_ALIGN.RIGHT)

    colors = [BLUE, ORANGE, TEAL]
    for idx, (_, row) in enumerate(comparison.iterrows()):
        x = 0.72 + idx * 3.78
        panel(slide, x, 1.42, 3.48, 3.75, line=colors[idx])
        text_box(slide, x + 0.22, 1.67, 3.04, 0.28, str(row["variant"]), 12.2, colors[idx], True, PP_ALIGN.CENTER)
        text_box(slide, x + 0.22, 2.18, 3.04, 0.42, f"Σ {row['sum_return_points']:+.2f} п.п.", 19, TEXT, True, PP_ALIGN.CENTER)
        text_box(slide, x + 0.22, 2.79, 3.04, 0.30, f"Средняя {row['average_return_pct']:+.3f}%", 11.5, MUTED, False, PP_ALIGN.CENTER)
        text_box(slide, x + 0.22, 3.23, 3.04, 0.30, f"PF {row['profit_factor_rub']:.2f}", 14, TEXT, True, PP_ALIGN.CENTER)
        text_box(slide, x + 0.22, 3.69, 3.04, 0.30, f"DD {row['closed_trade_max_dd_points']:.2f}", 12, RED, True, PP_ALIGN.CENTER)
        text_box(slide, x + 0.22, 4.10, 3.04, 0.30, f"Worst {row['worst_trade_pct']:.2f}%", 11, MUTED, False, PP_ALIGN.CENTER)
        text_box(slide, x + 0.22, 4.54, 3.04, 0.30, f"OOS Σ {row['oos_sum_return_points']:+.2f} · PF {row['oos_profit_factor_rub']:.2f}", 10.3, ORANGE, True, PP_ALIGN.CENTER)

    full = summary.set_index("segment").loc["Full"]
    panel(slide, 0.72, 5.66, 11.04, 0.80, fill="173746", line=ORANGE)
    text_box(slide, 0.96, 5.86, 10.56, 0.36, f"Long10 +5 сохраняет {100*full.sum_return_points/comparison.iloc[2].sum_return_points:.1f}% результата 10→20; worst {full.worst_trade_pct:.2f}% и DD {full.closed_trade_max_dd_points:.2f} п.п.", 11, ORANGE, True, PP_ALIGN.CENTER)
    prs.save(OUT_PPTX)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    add5, summary, yearly, exits, equity, validation, comparison = calculate()
    save_workbook(add5, summary, yearly, exits, equity, validation, comparison)
    add_slide(comparison, summary)
    print(summary.to_string(index=False))
    print(comparison.to_string(index=False))
    print("validation", validation[["exit_time_match", "exit_price_match", "pnl_match"]].sum().to_dict(), "of", len(validation))
    print(f"XLSX={OUT_XLSX}")
    print(f"PPTX={OUT_PPTX}")


if __name__ == "__main__":
    main()
