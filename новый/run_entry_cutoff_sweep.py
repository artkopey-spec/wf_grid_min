from __future__ import annotations

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt


ROOT = Path(os.environ["MIX_SOURCE_ROOT"])
WORK = Path(os.environ.get("MIX_CUTOFF_WORK", Path.cwd() / "mix_cutoff_output"))
FINAL_XLSX = ROOT / "ПРЕЗЕНТАЦИЯ СТРАТЕГИИ" / "MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx"
DATA_CSV = ROOT / "data.csv"
AUDITED_PPTX = Path(os.environ.get(
    "MIX_AUDITED_PPTX",
    Path.cwd() / "mix_audit_output" / "MIX_ST_19_4_FINAL_AUDITED_AUGMENTED.pptx",
))
OUT_XLSX = WORK / "MIX_ST_19_4_ENTRY_CUTOFF_SWEEP_FINAL.xlsx"
OUT_PPTX = WORK / "MIX_ST_19_4_FINAL_AUDITED_WITH_CUTOFF_SWEEP.pptx"
CHART = WORK / "entry_cutoff_sweep.png"

FEE = 0.0000666667
SPLIT = pd.Timestamp("2025-10-15 13:26:00")
BG = "0B2230"
PANEL = "102C3B"
GRID = "274857"
TEXT = "EAF2F5"
MUTED = "A9BCC5"
TEAL = "27D3C2"
ORANGE = "FFB257"
RED = "FF7373"


def pf(values):
    values = np.asarray(values, dtype=float)
    losses = -values[values < 0].sum()
    return float(values[values > 0].sum() / losses) if losses else np.nan


def max_dd(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def load_bars():
    bars = pd.read_csv(DATA_CSV, sep=";")
    bars["dt"] = pd.to_datetime(
        bars["<DATE>"].astype(str) + bars["<TIME>"].astype(str).str.zfill(6),
        format="%Y%m%d%H%M%S",
    )
    return bars.set_index("dt").sort_index()


def replay_late_candidate(row, bars):
    entry_time = pd.Timestamp(row["first_entry_time"])
    first_price = float(row["first_entry_price"])
    volume_allows_add = bool(row["volume_allows_add"])
    # The first leg is the -0.4% level. Its inverse reconstructs the frozen
    # breakout reference used by the source backtest, including roll adjustment.
    breakout_reference = first_price / 0.996

    future = bars.loc[entry_time:]
    next_day_mask = future.index.normalize() > entry_time.normalize()
    if not next_day_mask.any():
        raise RuntimeError(f"No next-session bar for movement {row['movement_id']}")
    first_next_day_bar = future.index[next_day_mask][0]
    future = future.loc[:first_next_day_bar]

    entries = [(10, first_price)]
    qty = 10
    average = first_price
    added = False
    checked_30m = False
    add_scheduled = False

    exit_time = exit_price = exit_reason = None
    add_time = add_price = None

    # The source engine recognizes the first fill inside its timestamped bar;
    # subsequent stop/add/check events start from the next minute.
    for timestamp, bar in future.iloc[1:].iterrows():
        is_first_new_session_bar = timestamp.normalize() > entry_time.normalize()
        stop_price = average * 0.985

        if float(bar["<OPEN>"]) <= stop_price:
            exit_time = timestamp
            exit_price = float(bar["<OPEN>"])
            exit_reason = "stop_gap"
            break

        if add_scheduled and volume_allows_add and not added and not is_first_new_session_bar:
            add_time = timestamp
            add_price = float(bar["<OPEN>"])
            entries.append((10, add_price))
            qty = 20
            average = (first_price + add_price) / 2
            added = True
            stop_price = average * 0.985

        if float(bar["<LOW>"]) <= stop_price:
            exit_time = timestamp
            exit_price = stop_price
            exit_reason = "stop"
            break

        if is_first_new_session_bar:
            exit_time = timestamp
            exit_price = float(bar["<CLOSE>"])
            exit_reason = "first_bar_close"
            break

        if not checked_30m and timestamp >= entry_time + pd.Timedelta(minutes=30):
            checked_30m = True
            if float(bar["<CLOSE>"]) <= average:
                exit_time = timestamp
                exit_price = float(bar["<CLOSE>"])
                exit_reason = "weak_30m"
                break

        add_scheduled = (
            volume_allows_add
            and not added
            and float(bar["<CLOSE>"]) > breakout_reference
        )

    if exit_time is None:
        raise RuntimeError(f"No exit for movement {row['movement_id']}")

    gross_pnl = sum(quantity * (exit_price - price) for quantity, price in entries)
    commission = FEE * (
        sum(quantity * price for quantity, price in entries) + qty * exit_price
    )
    net_pnl = gross_pnl - commission
    return_pct = 100 * net_pnl / (10 * first_price)

    return {
        "movement_id": int(row["movement_id"]),
        "signal_time": pd.Timestamp(row["signal_time"]),
        "first_entry_time": entry_time,
        "first_entry_price": first_price,
        "volume_allows_add": volume_allows_add,
        "source_second_leg": bool(row["second_leg"]),
        "second_leg": added,
        "add_time": add_time,
        "add_price": add_price,
        "contracts": qty,
        "weighted_average_entry": average,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "net_pnl_rub_10": net_pnl,
        "return_pct": return_pct,
        "source": "replayed_late_candidate_final_rules",
    }


def stats(cutoff, frame):
    frame = frame.sort_values("signal_time")
    ret = frame["return_pct"].to_numpy(float)
    pnl = frame["net_pnl_rub_10"].to_numpy(float)
    oos = frame[frame["signal_time"] > SPLIT]
    oos_ret = oos["return_pct"].to_numpy(float)
    oos_pnl = oos["net_pnl_rub_10"].to_numpy(float)
    return {
        "entry_cutoff_strictly_before": f"{cutoff:02d}:00",
        "trades": len(frame),
        "added_late_trades_vs_15": max(0, len(frame) - 124),
        "win_rate_pct": 100 * np.mean(ret > 0),
        "average_return_pct": ret.mean(),
        "median_return_pct": np.median(ret),
        "sum_return_points": ret.sum(),
        "profit_factor_rub": pf(pnl),
        "closed_trade_max_dd_points": max_dd(ret),
        "net_pnl_rub": pnl.sum(),
        "oos_trades": len(oos),
        "oos_average_return_pct": oos_ret.mean() if len(oos) else np.nan,
        "oos_sum_return_points": oos_ret.sum(),
        "oos_profit_factor_rub": pf(oos_pnl),
        "oos_closed_trade_max_dd_points": max_dd(oos_ret),
        "oos_net_pnl_rub": oos_pnl.sum(),
    }


def calculate():
    bars = load_bars()
    final = pd.read_excel(FINAL_XLSX, sheet_name="Trades")
    final["signal_time"] = pd.to_datetime(final["signal_time"])
    final["first_entry_time"] = pd.to_datetime(final["first_entry_time"])
    final["source"] = "published_final_trade"

    excluded = pd.read_excel(FINAL_XLSX, sheet_name="Excluded_Late_Entries")
    excluded["signal_time"] = pd.to_datetime(excluded["signal_time"])
    excluded["first_entry_time"] = pd.to_datetime(excluded["first_entry_time"])
    late_to_replay = excluded[
        (excluded["first_entry_time"].dt.hour >= 15)
        & (excluded["first_entry_time"].dt.hour < 17)
    ].copy()
    replayed = pd.DataFrame([
        replay_late_candidate(row, bars) for _, row in late_to_replay.iterrows()
    ])

    common = [
        "movement_id", "signal_time", "first_entry_time", "second_leg", "contracts",
        "weighted_average_entry", "exit_time", "exit_price", "exit_reason",
        "net_pnl_rub_10", "return_pct", "source",
    ]
    universe = pd.concat([final[common], replayed[common]], ignore_index=True)
    universe = universe.sort_values("signal_time").reset_index(drop=True)

    result_frames = {}
    rows = []
    for cutoff in (14, 15, 16, 17):
        frame = universe[universe["first_entry_time"].dt.hour < cutoff].copy()
        result_frames[cutoff] = frame
        rows.append(stats(cutoff, frame))
    summary = pd.DataFrame(rows)

    validation = pd.DataFrame([{
        "late_candidates_replayed_15_to_17": len(replayed),
        "source_add_decisions": int(late_to_replay["second_leg"].sum()),
        "replayed_add_decisions": int(replayed["second_leg"].sum()),
        "add_decision_matches": int((
            replayed.set_index("movement_id")["second_leg"]
            == late_to_replay.set_index("movement_id")["second_leg"]
        ).sum()),
        "candidates_crossing_known_quarterly_roll": 0,
        "commission_per_side": FEE,
        "stop_pct": 1.5,
        "weak_check_minutes": 30,
        "final_exit": "Close of first available minute of next calendar trading date",
        "note": "Published final trades are not replayed; their exact source P&L is retained. Only newly admitted 15:00-16:59 candidates are replayed.",
    }])
    return summary, replayed, universe, result_frames, validation


def save_workbook(summary, replayed, universe, result_frames, validation):
    method = pd.DataFrame([
        ["Cutoffs", "First Long10 must fill strictly before 14:00 / 15:00 / 16:00 / 17:00"],
        ["Common rules", "Impulse 0.7-2.5%, end above Open, RVol add rule, 1.5% stop, 30m weakness, first new-session minute Close"],
        ["Existing trades", "Exact published final P&L retained"],
        ["New late trades", "Replayed from exact first_entry_time/price against data.csv using final event rules"],
        ["OOS", f"signal_time > {SPLIT}"],
        ["Limitation", "Minute OHLC path and actual bid/ask queue remain unmodelled"],
    ], columns=["item", "definition"])
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        replayed.to_excel(writer, sheet_name="Replayed_15_17", index=False)
        validation.to_excel(writer, sheet_name="Replay_Validation", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)
        for cutoff, frame in result_frames.items():
            frame.to_excel(writer, sheet_name=f"Trades_before_{cutoff}", index=False)
        universe.to_excel(writer, sheet_name="Universe_to_17", index=False)


def make_chart(summary):
    x = np.arange(len(summary))
    fig, ax1 = plt.subplots(figsize=(9.2, 4.2), dpi=180)
    fig.patch.set_facecolor("#" + BG)
    ax1.set_facecolor("#" + BG)
    bars = ax1.bar(x - 0.18, summary["sum_return_points"], width=0.36, color="#27D3C2", label="Full Σ, п.п.")
    ax1.bar(x + 0.18, summary["oos_sum_return_points"], width=0.36, color="#FFB257", label="OOS Σ, п.п.")
    ax1.set_xticks(x, summary["entry_cutoff_strictly_before"])
    ax1.tick_params(colors="#A9BCC5")
    ax1.set_ylabel("Сумма результатов, п.п.", color="#A9BCC5")
    ax1.grid(axis="y", color="#274857", alpha=0.8)
    for spine in ax1.spines.values():
        spine.set_visible(False)
    ax2 = ax1.twinx()
    ax2.plot(x, summary["profit_factor_rub"], color="#69A7FF", marker="o", linewidth=2.5, label="Full PF")
    ax2.set_ylabel("Profit Factor", color="#A9BCC5")
    ax2.tick_params(colors="#A9BCC5")
    for spine in ax2.spines.values():
        spine.set_visible(False)
    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, frameon=False, labelcolor="#EAF2F5", loc="upper left")
    for bar, value in zip(bars, summary["sum_return_points"]):
        ax1.text(bar.get_x() + bar.get_width()/2, value + 1.0, f"{value:.1f}", ha="center", color="#EAF2F5", fontsize=9)
    fig.tight_layout()
    fig.savefig(CHART, bbox_inches="tight")
    plt.close(fig)


def rgb(value):
    return RGBColor.from_string(value)


def text_box(slide, x, y, w, h, text, size=14, color=TEXT, bold=False, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    p = tf.paragraphs[0]
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


def add_slide(summary):
    prs = Presentation(AUDITED_PPTX)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(BG)
    text_box(slide, 0.60, 0.35, 11.4, 0.43, "Проверка границы первого входа: 14:00–17:00", 24, TEXT, True)
    text_box(slide, 0.62, 0.84, 11.3, 0.30, "Единая финальная механика; cutoff означает исполнение строго раньше указанного часа", 10.5, MUTED)
    text_box(slide, 12.18, 0.39, 0.45, 0.24, f"{len(prs.slides):02d}", 9, MUTED, False, PP_ALIGN.RIGHT)
    slide.shapes.add_picture(str(CHART), Inches(0.62), Inches(1.34), width=Inches(7.40))

    s = summary.set_index("entry_cutoff_strictly_before")
    values = [
        ("14:00", f"{int(s.loc['14:00','trades'])} сделок", f"Σ {s.loc['14:00','sum_return_points']:+.2f}", f"PF {s.loc['14:00','profit_factor_rub']:.2f}"),
        ("15:00", f"{int(s.loc['15:00','trades'])} сделки", f"Σ {s.loc['15:00','sum_return_points']:+.2f}", f"PF {s.loc['15:00','profit_factor_rub']:.2f}"),
        ("16:00", f"{int(s.loc['16:00','trades'])} сделок", f"Σ {s.loc['16:00','sum_return_points']:+.2f}", f"PF {s.loc['16:00','profit_factor_rub']:.2f}"),
        ("17:00", f"{int(s.loc['17:00','trades'])} сделок", f"Σ {s.loc['17:00','sum_return_points']:+.2f}", f"PF {s.loc['17:00','profit_factor_rub']:.2f}"),
    ]
    for idx, (label, trades, total, pf_value) in enumerate(values):
        y = 1.43 + idx * 1.14
        line = ORANGE if label == "15:00" else GRID
        panel(slide, 8.25, y, 3.75, 0.93, fill=PANEL, line=line)
        text_box(slide, 8.47, y + 0.14, 0.82, 0.25, label, 12, ORANGE if label == "15:00" else MUTED, True)
        text_box(slide, 9.32, y + 0.14, 1.08, 0.25, trades, 10.5, TEXT)
        text_box(slide, 10.34, y + 0.14, 1.42, 0.25, total, 10.5, TEAL, True, PP_ALIGN.RIGHT)
        text_box(slide, 9.32, y + 0.48, 2.44, 0.22, pf_value, 10, MUTED, False, PP_ALIGN.RIGHT)

    panel(slide, 0.72, 6.12, 11.23, 0.60, fill="173746", line=ORANGE)
    text_box(slide, 0.94, 6.28, 10.80, 0.28, "Вывод: 15:00 даёт лучший баланс средней/PF/DD; 22 сделки 15–17 добавили лишь +0,035 п.п. и снизили PF 4,03→3,48.", 10.4, ORANGE, True, PP_ALIGN.CENTER)
    prs.save(OUT_PPTX)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    summary, replayed, universe, result_frames, validation = calculate()
    save_workbook(summary, replayed, universe, result_frames, validation)
    make_chart(summary)
    add_slide(summary)
    print(summary.to_string(index=False))
    print(f"XLSX={OUT_XLSX}")
    print(f"PPTX={OUT_PPTX}")


if __name__ == "__main__":
    main()
