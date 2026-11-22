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
WORK = Path(os.environ.get("MIX_VOLUME_WORK", Path.cwd() / "mix_volume_output"))
FINAL_XLSX = ROOT / "ПРЕЗЕНТАЦИЯ СТРАТЕГИИ" / "MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx"
DMI_XLSX = ROOT / "MIX_ST_19_4_DMI_reduced_position_backtest.xlsx"
DATA_CSV = ROOT / "data.csv"
BASE_PPTX = Path(os.environ["MIX_VOLUME_BASE_PPTX"])
OUT_XLSX = WORK / "MIX_ST_19_4_VOLUME_ADD_FILTER_FINAL_TEST.xlsx"
OUT_PPTX = WORK / "MIX_ST_19_4_FINAL_AUDITED_WITH_VOLUME_TEST.pptx"
CHART = WORK / "volume_filter_comparison.png"

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


def locate_breakout_time(row, bars):
    if pd.isna(row["breakout_price"]):
        return pd.NaT
    window = bars.loc[row["first_entry_time"]:row["exit_time"]]
    hits = window.index[np.isclose(window["<OPEN>"].astype(float), float(row["breakout_price"]))]
    return hits[0] if len(hits) else pd.NaT


def replay_from_breakout(row, bars, add_second_leg):
    entry_time = pd.Timestamp(row["first_entry_time"])
    first_price = float(row["leg_04_price"])
    breakout_time = pd.Timestamp(row["breakout_time"])
    breakout_price = float(row["breakout_price"])

    if add_second_leg:
        entries = [(10, first_price), (10, breakout_price)]
        qty = 20
        average = (first_price + breakout_price) / 2
    else:
        entries = [(10, first_price)]
        qty = 10
        average = first_price

    checked_30m = breakout_time > entry_time + pd.Timedelta(minutes=30)
    future = bars.loc[breakout_time:]
    next_day = future.index.normalize() > entry_time.normalize()
    if not next_day.any():
        raise RuntimeError(f"No next session for movement {row['movement_id']}")
    last_needed = future.index[next_day][0]
    future = future.loc[:last_needed]

    exit_time = exit_price = exit_reason = None
    for index, (timestamp, bar) in enumerate(future.iterrows()):
        is_new_session = timestamp.normalize() > entry_time.normalize()
        stop_price = average * 0.985

        # On the breakout bar the position changes at Open. The source trade
        # had already survived the old-position gap check before this event.
        if index > 0 and float(bar["<OPEN>"]) <= stop_price:
            exit_time, exit_price, exit_reason = timestamp, float(bar["<OPEN>"]), "stop_gap"
            break
        if float(bar["<LOW>"]) <= stop_price:
            exit_time, exit_price, exit_reason = timestamp, stop_price, "stop"
            break
        if is_new_session:
            exit_time, exit_price, exit_reason = timestamp, float(bar["<CLOSE>"]), "first_bar_close"
            break
        if not checked_30m and timestamp >= entry_time + pd.Timedelta(minutes=30):
            checked_30m = True
            if float(bar["<CLOSE>"]) <= average:
                exit_time, exit_price, exit_reason = timestamp, float(bar["<CLOSE>"]), "weak_30m"
                break

    if exit_time is None:
        raise RuntimeError(f"No exit for movement {row['movement_id']}")

    gross = sum(quantity * (exit_price - price) for quantity, price in entries)
    commission = FEE * (
        sum(quantity * price for quantity, price in entries) + qty * exit_price
    )
    pnl = gross - commission
    return_pct = 100 * pnl / (10 * first_price)
    return {
        "second_leg": bool(add_second_leg),
        "contracts": qty,
        "weighted_average_entry": average,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "net_pnl_rub_10": pnl,
        "return_pct": return_pct,
    }


def variant_frame(source, bars, mode):
    frame = source.copy()
    frame["breakout_time"] = [locate_breakout_time(row, bars) for _, row in frame.iterrows()]
    frame["variant"] = mode
    frame["replayed"] = False

    if mode == "Текущий RVol-фильтр":
        return frame
    if mode == "Без P60: добавлять при известном RVol":
        changed = (
            (~frame["volume_allows_add"].astype(bool))
            & frame["breakout_time"].notna()
            & frame["impulse_rvol"].notna()
        )
        add_second_leg = True
    elif mode == "Без фильтра: добавлять всегда":
        changed = (~frame["volume_allows_add"].astype(bool)) & frame["breakout_time"].notna()
        add_second_leg = True
    elif mode == "Только Long10: без добавлений":
        changed = frame["second_leg"].astype(bool) & frame["breakout_time"].notna()
        add_second_leg = False
    else:
        raise ValueError(mode)

    for index, row in frame[changed].iterrows():
        replay = replay_from_breakout(row, bars, add_second_leg=add_second_leg)
        for key, value in replay.items():
            frame.at[index, key] = value
        frame.at[index, "replayed"] = True
    return frame


def stats(name, frame):
    frame = frame.sort_values("signal_time")
    pnl = frame["net_pnl_rub_10"].to_numpy(float)
    ret = frame["return_pct"].to_numpy(float)
    oos = frame[frame["signal_time"] > SPLIT]
    op = oos["net_pnl_rub_10"].to_numpy(float)
    or_ = oos["return_pct"].to_numpy(float)
    return {
        "variant": name,
        "trades": len(frame),
        "adds": int(frame["second_leg"].sum()),
        "replayed_counterfactual_trades": int(frame["replayed"].sum()),
        "win_rate_pct": 100 * np.mean(ret > 0),
        "average_return_pct": ret.mean(),
        "median_return_pct": np.median(ret),
        "sum_return_points": ret.sum(),
        "profit_factor_rub": profit_factor(pnl),
        "closed_trade_max_dd_points": max_drawdown(ret),
        "worst_trade_pct": ret.min(),
        "net_pnl_rub": pnl.sum(),
        "oos_trades": len(oos),
        "oos_adds": int(oos["second_leg"].sum()),
        "oos_average_return_pct": or_.mean(),
        "oos_sum_return_points": or_.sum(),
        "oos_profit_factor_rub": profit_factor(op),
        "oos_closed_trade_max_dd_points": max_drawdown(or_),
        "oos_net_pnl_rub": op.sum(),
    }


def calculate():
    bars, source = load_sources()
    modes = [
        "Текущий RVol-фильтр",
        "Без P60: добавлять при известном RVol",
        "Без фильтра: добавлять всегда",
        "Только Long10: без добавлений",
    ]
    variants = {mode: variant_frame(source, bars, mode) for mode in modes}
    summary = pd.DataFrame([stats(mode, variants[mode]) for mode in modes])

    current = variants[modes[0]].set_index("movement_id")
    comparisons = []
    for mode in modes[1:]:
        candidate = variants[mode].set_index("movement_id")
        changed = candidate[candidate["replayed"]]
        for movement_id, row in changed.iterrows():
            base = current.loc[movement_id]
            comparisons.append({
                "variant": mode,
                "movement_id": movement_id,
                "signal_time": row["signal_time"],
                "first_entry_time": row["first_entry_time"],
                "impulse_rvol": row["impulse_rvol"],
                "breakout_time": row["breakout_time"],
                "breakout_price": row["breakout_price"],
                "baseline_second_leg": base["second_leg"],
                "new_second_leg": row["second_leg"],
                "baseline_return_pct": base["return_pct"],
                "new_return_pct": row["return_pct"],
                "delta_return_points": row["return_pct"] - base["return_pct"],
                "baseline_pnl_rub": base["net_pnl_rub_10"],
                "new_pnl_rub": row["net_pnl_rub_10"],
                "delta_pnl_rub": row["net_pnl_rub_10"] - base["net_pnl_rub_10"],
                "baseline_exit_reason": base["exit_reason"],
                "new_exit_reason": row["exit_reason"],
            })
    comparisons = pd.DataFrame(comparisons)

    actual_adds = source[source["second_leg"].astype(bool)].copy()
    actual_adds["breakout_time"] = [locate_breakout_time(row, bars) for _, row in actual_adds.iterrows()]
    validation = pd.DataFrame([{
        "published_actual_adds": len(actual_adds),
        "breakout_time_found_before_published_exit": int(actual_adds["breakout_time"].notna().sum()),
        "weighted_average_exact_matches": int(np.isclose(
            (actual_adds["leg_04_price"] + actual_adds["breakout_price"]) / 2,
            actual_adds["weighted_average_entry"],
        ).sum()),
        "volume_disallowed_trades": int((~source["volume_allows_add"].astype(bool)).sum()),
        "new_adds_without_p60_known_rvol": int(variants[modes[1]]["replayed"].sum()),
        "new_adds_when_volume_ignored": int(variants[modes[2]]["replayed"].sum()),
        "removed_adds_in_long10_variant": int(variants[modes[3]]["replayed"].sum()),
        "commission_per_side": FEE,
        "note": "Only trades whose position changes are replayed. Unchanged trades retain exact published final P&L.",
    }])
    return summary, variants, comparisons, validation


def save_workbook(summary, variants, comparisons, validation):
    method = pd.DataFrame([
        ["Current", "First Long10 always; +10 only when impulse RVol <= frozen train P60 2.922962"],
        ["No P60 threshold", "Every confirmed breakout with available RVol adds +10; missing standard still means no add"],
        ["No volume system", "Every confirmed breakout adds +10, including missing RVol standard"],
        ["Long10 only", "No second leg under any RVol"],
        ["Common rules", "0.7-2.5% impulse, above Open, entry before 15:00, 1.5% stop, 30m weakness, first new-session Close"],
        ["Replay", "Only trades affected by the position-size policy are replayed from exact entry and breakout prices"],
        ["Limitations", "Minute OHLC path, queue position and slippage are not modelled"],
    ], columns=["item", "definition"])
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        comparisons.to_excel(writer, sheet_name="Changed_Trades", index=False)
        validation.to_excel(writer, sheet_name="Replay_Validation", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)
        for idx, (name, frame) in enumerate(variants.items(), start=1):
            frame.to_excel(writer, sheet_name=f"Variant_{idx}", index=False)


def make_chart(summary):
    labels = ["RVol filter", "No P60", "Always add", "Long10 only"]
    x = np.arange(4)
    fig, ax1 = plt.subplots(figsize=(9.2, 4.2), dpi=180)
    fig.patch.set_facecolor("#" + BG)
    ax1.set_facecolor("#" + BG)
    bars = ax1.bar(x, summary["sum_return_points"], color=["#27D3C2", "#FFB257", "#FF7373", "#69A7FF"], width=0.55)
    ax1.set_xticks(x, labels)
    ax1.set_ylabel("Сумма результатов, п.п.", color="#A9BCC5")
    ax1.tick_params(colors="#A9BCC5")
    ax1.grid(axis="y", color="#274857", alpha=0.8)
    for spine in ax1.spines.values():
        spine.set_visible(False)
    ax2 = ax1.twinx()
    ax2.plot(x, summary["profit_factor_rub"], color="#EAF2F5", marker="o", linewidth=2.3)
    ax2.set_ylabel("Profit Factor", color="#A9BCC5")
    ax2.tick_params(colors="#A9BCC5")
    for spine in ax2.spines.values():
        spine.set_visible(False)
    for bar, total, pf_value in zip(bars, summary["sum_return_points"], summary["profit_factor_rub"]):
        ax1.text(bar.get_x() + bar.get_width()/2, total + 1.4, f"Σ {total:+.1f}\nPF {pf_value:.2f}", ha="center", color="#EAF2F5", fontsize=9)
    fig.tight_layout()
    fig.savefig(CHART, bbox_inches="tight")
    plt.close(fig)


def rgb(value):
    return RGBColor.from_string(value)


def text_box(slide, x, y, w, h, text, size=14, color=TEXT, bold=False, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
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


def add_presentation_slide(summary):
    prs = Presentation(BASE_PPTX)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(BG)
    text_box(slide, 0.60, 0.35, 11.4, 0.43, "Проверка объёма: фильтр добавления или фиксированный размер", 23, TEXT, True)
    text_box(slide, 0.62, 0.84, 11.3, 0.30, "Первый Long10 не фильтруется; меняется только правило второй ноги +10", 10.5, MUTED)
    text_box(slide, 12.18, 0.39, 0.45, 0.24, f"{len(prs.slides):02d}", 9, MUTED, False, PP_ALIGN.RIGHT)
    slide.shapes.add_picture(str(CHART), Inches(0.62), Inches(1.35), width=Inches(7.38))

    colors = [TEAL, ORANGE, RED, BLUE]
    labels = ["Текущий RVol", "Без P60", "Всегда +10", "Только Long10"]
    for idx, (_, row) in enumerate(summary.iterrows()):
        y = 1.26 + idx * 1.14
        panel(slide, 8.24, y, 3.78, 0.98, line=colors[idx])
        text_box(slide, 8.48, y + 0.14, 1.42, 0.25, labels[idx], 11.2, colors[idx], True)
        text_box(slide, 9.88, y + 0.14, 1.90, 0.25, f"{int(row['adds'])} добавлений", 10, MUTED, False, PP_ALIGN.RIGHT)
        text_box(slide, 8.48, y + 0.47, 1.55, 0.25, f"Σ {row['sum_return_points']:+.2f}", 12.5, TEXT, True)
        text_box(slide, 10.04, y + 0.47, 1.74, 0.25, f"PF {row['profit_factor_rub']:.2f}", 12.5, TEXT, True, PP_ALIGN.RIGHT)
        text_box(slide, 8.48, y + 0.76, 3.30, 0.16, f"DD {row['closed_trade_max_dd_points']:.2f} · OOS PF {row['oos_profit_factor_rub']:.2f}", 8.6, MUTED, False, PP_ALIGN.RIGHT)

    current = summary.iloc[0]
    no_p60 = summary.iloc[1]
    always = summary.iloc[2]
    long10 = summary.iloc[3]
    panel(slide, 0.72, 6.10, 11.24, 0.64, fill="173746", line=TEAL)
    conclusion = (
        f"Вывод: P60 оставить — без порога PF {current.profit_factor_rub:.2f}→{no_p60.profit_factor_rub:.2f}, "
        f"DD {current.closed_trade_max_dd_points:.2f}→{no_p60.closed_trade_max_dd_points:.2f}; "
        f"полный отказ от добавлений даёт Σ {long10.sum_return_points:+.1f}."
    )
    text_box(slide, 0.93, 6.27, 10.82, 0.30, conclusion, 10.1, TEAL, True, PP_ALIGN.CENTER)
    prs.save(OUT_PPTX)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    summary, variants, comparisons, validation = calculate()
    save_workbook(summary, variants, comparisons, validation)
    make_chart(summary)
    add_presentation_slide(summary)
    print(summary.to_string(index=False))
    print(validation.to_string(index=False))
    print(f"XLSX={OUT_XLSX}")
    print(f"PPTX={OUT_PPTX}")


if __name__ == "__main__":
    main()
