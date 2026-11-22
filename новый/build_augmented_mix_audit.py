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
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt


ROOT = Path(os.environ["MIX_SOURCE_ROOT"])
WORK = Path(os.environ.get("MIX_AUDIT_WORK", Path.cwd() / "mix_audit_output"))
BASE_PPTX = ROOT / "ПРЕЗЕНТАЦИЯ СТРАТЕГИИ" / "MIX_ST_19_4_FINAL.pptx"
FINAL_XLSX = ROOT / "ПРЕЗЕНТАЦИЯ СТРАТЕГИИ" / "MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx"
STOP_XLSX = ROOT / "MIX_ST_19_4_FINAL_30min_first_bar_close_stop_sweep.xlsx"
TIME_XLSX = ROOT / "MIX_ST_19_4_final_entry_before15_first_bar_close_time_exit_sweep.xlsx"
IMPULSE_XLSX = ROOT / "MIX_ST_19_4_impulse_size_result_analysis.xlsx"
BOUNDARY_XLSX = ROOT / "MIX_ST_19_4_impulse_upper_boundary_analysis.xlsx"
DMI_XLSX = ROOT / "MIX_ST_19_4_single_DMI_downtrend_filter_backtest.xlsx"
WF_XLSX = ROOT / "data_wf_grid_results_20260825_142624.xlsx"
OUT_PPTX = WORK / "MIX_ST_19_4_FINAL_AUDITED_AUGMENTED.pptx"
OUT_XLSX = WORK / "MIX_ST_19_4_AUDIT_CALCULATIONS.xlsx"
SPLIT = pd.Timestamp("2025-10-15 13:26:00")

BG = "0B2230"
PANEL = "102C3B"
PANEL2 = "163746"
GRID = "274857"
TEXT = "EAF2F5"
MUTED = "A9BCC5"
TEAL = "27D3C2"
ORANGE = "FFB257"
RED = "FF7373"
GREEN = "8EEA70"
BLUE = "69A7FF"


def rgb(hex_value: str) -> RGBColor:
    return RGBColor.from_string(hex_value)


def max_drawdown(values) -> float:
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def profit_factor(values) -> float:
    values = np.asarray(values, dtype=float)
    loss = -values[values < 0].sum()
    return float(values[values > 0].sum() / loss) if loss else np.nan


def longest_loss_streak(values) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        best = max(best, current)
    return best


def bootstrap_summary(values, simulations=50_000, block=5, seed=20260827):
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    n = len(values)
    blocks = (n + block - 1) // block
    means = np.empty(simulations)
    sums = np.empty(simulations)
    dds = np.empty(simulations)
    streaks = np.empty(simulations, dtype=int)
    offsets = np.arange(block)
    for idx in range(simulations):
        starts = rng.integers(0, n, size=blocks)
        sample = np.concatenate([values[(start + offsets) % n] for start in starts])[:n]
        means[idx] = sample.mean()
        sums[idx] = sample.sum()
        dds[idx] = max_drawdown(sample)
        streaks[idx] = longest_loss_streak(sample)
    return {
        "mean_ci_low": np.percentile(means, 2.5),
        "mean_median": np.percentile(means, 50),
        "mean_ci_high": np.percentile(means, 97.5),
        "prob_mean_nonpositive": np.mean(means <= 0),
        "sum_ci_low": np.percentile(sums, 2.5),
        "sum_ci_high": np.percentile(sums, 97.5),
        "dd_median": np.percentile(dds, 50),
        "dd_95_worst": np.percentile(dds, 5),
        "dd_99_worst": np.percentile(dds, 1),
        "streak_median": np.percentile(streaks, 50),
        "streak_95": np.percentile(streaks, 95),
        "prob_streak_at_least_8": np.mean(streaks >= 8),
        "note": "Moving-block bootstrap, block=5 trades; does not correct selection bias or execution-model error.",
    }


def load_and_calculate():
    trades = pd.read_excel(FINAL_XLSX, sheet_name="Trades")
    trades["signal_time"] = pd.to_datetime(trades["signal_time"])
    trades["first_entry_time"] = pd.to_datetime(trades["first_entry_time"])
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])
    train = trades[trades["signal_time"] <= SPLIT].copy()
    oos = trades[trades["signal_time"] > SPLIT].copy()

    def segment_row(name, frame):
        r = frame["return_pct"].to_numpy(float)
        p = frame["net_pnl_rub_10"].to_numpy(float)
        return {
            "segment": name,
            "period": f"{frame.signal_time.min():%Y-%m-%d} — {frame.signal_time.max():%Y-%m-%d}",
            "trades": len(frame),
            "wins": int((r > 0).sum()),
            "win_rate_pct": 100 * np.mean(r > 0),
            "average_return_pct": r.mean(),
            "median_return_pct": np.median(r),
            "sum_return_points": r.sum(),
            "profit_factor_rub": profit_factor(p),
            "closed_trade_max_dd_points": max_drawdown(r),
            "net_pnl_rub": p.sum(),
            "best_trade_pct": r.max(),
            "worst_trade_pct": r.min(),
        }

    segments = pd.DataFrame([
        segment_row("Full", trades),
        segment_row("Train", train),
        segment_row("OOS", oos),
    ])

    ordered = trades.sort_values("return_pct", ascending=False).reset_index(drop=True)
    concentration_rows = []
    total_return = trades["return_pct"].sum()
    for top_n in (1, 3, 5, 10):
        removed_ids = set(ordered.head(top_n)["movement_id"])
        kept = trades[~trades["movement_id"].isin(removed_ids)]
        top_sum = ordered.head(top_n)["return_pct"].sum()
        concentration_rows.append({
            "top_n": top_n,
            "top_sum_return_points": top_sum,
            "share_of_total_pct": 100 * top_sum / total_return,
            "sum_without_top_points": kept["return_pct"].sum(),
            "pf_rub_without_top": profit_factor(kept["net_pnl_rub_10"]),
            "trades_remaining": len(kept),
        })
    concentration = pd.DataFrame(concentration_rows)

    inferred_initial_notional = np.where(
        np.abs(trades["return_pct"].to_numpy(float)) > 1e-12,
        trades["net_pnl_rub_10"].to_numpy(float) / (trades["return_pct"].to_numpy(float) / 100),
        10 * trades["weighted_average_entry"].to_numpy(float),
    )
    cost_rows = []
    for bps in (0, 1, 2, 5, 10, 20):
        extra_cost = (
            bps / 10_000
            * trades["contracts"].to_numpy(float)
            * (trades["weighted_average_entry"].to_numpy(float) + trades["exit_price"].to_numpy(float))
        )
        adjusted_pnl = trades["net_pnl_rub_10"].to_numpy(float) - extra_cost
        adjusted_return = adjusted_pnl / inferred_initial_notional * 100
        cost_rows.append({
            "extra_slippage_bps_each_side": bps,
            "net_pnl_rub": adjusted_pnl.sum(),
            "sum_return_points": adjusted_return.sum(),
            "average_trade_pct": adjusted_return.mean(),
            "profit_factor_rub": profit_factor(adjusted_pnl),
            "closed_trade_max_dd_points": max_drawdown(adjusted_return),
        })
    costs = pd.DataFrame(cost_rows)

    full_boot = bootstrap_summary(trades["return_pct"])
    oos_boot = bootstrap_summary(oos["return_pct"], seed=20260828)
    bootstrap = pd.DataFrame([
        {"segment": "Full", **full_boot},
        {"segment": "OOS", **oos_boot},
    ])

    capital_rows = []
    total_pnl = trades["net_pnl_rub_10"].sum()
    closed_dd_rub = max_drawdown(trades["net_pnl_rub_10"])
    for capital in (1_000_000, 2_000_000, 2_500_000, 3_000_000, 5_000_000):
        capital_rows.append({
            "initial_capital_rub": capital,
            "fixed_contract_pnl_pct_of_capital": 100 * total_pnl / capital,
            "closed_trade_dd_pct_of_capital": 100 * closed_dd_rub / capital,
            "note": "Not CAGR; ignores intratrade MTM, changing margin and forced liquidation risk.",
        })
    capital = pd.DataFrame(capital_rows)

    summary = pd.DataFrame([{
        "bars": 630_299,
        "first_impulses_gt_0_7": 326,
        "above_open": 234,
        "correction_entries_filled": 185,
        "no_correction_fill": 49,
        "excluded_cap_candidates": 12,
        "after_cap": 173,
        "excluded_late_entries": 49,
        "final_trades": 124,
        "full_sum_return_points": segments.loc[segments.segment == "Full", "sum_return_points"].iloc[0],
        "full_pf": segments.loc[segments.segment == "Full", "profit_factor_rub"].iloc[0],
        "oos_trades": len(oos),
        "oos_sum_return_points": oos["return_pct"].sum(),
        "oos_pf": profit_factor(oos["net_pnl_rub_10"]),
        "oos_closed_trade_dd_points": max_drawdown(oos["return_pct"]),
        "visible_direct_refinement_rows": 9 + 6 + 9 + 26 + 7,
        "separate_supertrend_grid_rows": 400,
        "audit_conclusion": "Promising OOS, but live readiness remains conditional on realistic execution and forward validation.",
    }])

    stop = pd.read_excel(STOP_XLSX, sheet_name="Summary")
    timing = pd.read_excel(TIME_XLSX, sheet_name="Summary")
    min_impulse = pd.read_excel(IMPULSE_XLSX, sheet_name="Min_Threshold_Sweep")
    cap = pd.read_excel(BOUNDARY_XLSX, sheet_name="Boundary_Sweep")
    dmi = pd.read_excel(DMI_XLSX, sheet_name="Summary")
    wf = pd.read_excel(WF_XLSX, sheet_name="summary")
    wf_19_4 = wf[(wf["atr_period"] == 19) & (wf["multiplier"] == 4.0)].copy()

    return {
        "trades": trades,
        "train": train,
        "oos": oos,
        "summary": summary,
        "segments": segments,
        "concentration": concentration,
        "costs": costs,
        "bootstrap": bootstrap,
        "capital": capital,
        "stop": stop,
        "timing": timing,
        "min_impulse": min_impulse,
        "cap": cap,
        "dmi": dmi,
        "wf_19_4": wf_19_4,
    }


def save_workbook(data):
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        for key in (
            "summary", "segments", "concentration", "costs", "bootstrap", "capital",
            "stop", "timing", "min_impulse", "cap", "dmi", "wf_19_4", "trades",
        ):
            data[key].to_excel(writer, sheet_name=key[:31], index=False)


def style_plot(ax):
    ax.set_facecolor("#" + BG)
    ax.figure.set_facecolor("#" + BG)
    ax.tick_params(colors="#A9BCC5", labelsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="y", color="#274857", linewidth=0.8, alpha=0.8)


def make_charts(data):
    chart_paths = {}

    fig, ax = plt.subplots(figsize=(8.8, 4.0), dpi=180)
    style_plot(ax)
    labels = ["Train\n82 сделки", "OOS\n42 сделки"]
    vals = [data["train"].return_pct.sum(), data["oos"].return_pct.sum()]
    bars = ax.bar(labels, vals, color=["#27D3C2", "#FFB257"], width=0.55)
    ax.set_ylabel("Сумма результатов, п.п.", color="#A9BCC5")
    ax.set_ylim(0, max(vals) * 1.23)
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width()/2, val + 1.2, f"+{val:.2f}", ha="center", color="#EAF2F5", fontsize=14, fontweight="bold")
    fig.tight_layout()
    path = WORK / "train_oos.png"
    fig.savefig(path, transparent=False, bbox_inches="tight")
    plt.close(fig)
    chart_paths["train_oos"] = path

    fig, ax = plt.subplots(figsize=(8.8, 4.0), dpi=180)
    style_plot(ax)
    c = data["concentration"]
    x = np.arange(len(c))
    ax.bar(x - 0.18, c["top_sum_return_points"], width=0.36, label="лучшие сделки", color="#FFB257")
    ax.bar(x + 0.18, c["sum_without_top_points"], width=0.36, label="остаток результата", color="#27D3C2")
    ax.set_xticks(x, [f"Top {n}" for n in c.top_n])
    ax.set_ylabel("п.п.", color="#A9BCC5")
    ax.legend(frameon=False, labelcolor="#EAF2F5", loc="upper right")
    fig.tight_layout()
    path = WORK / "concentration.png"
    fig.savefig(path, transparent=False, bbox_inches="tight")
    plt.close(fig)
    chart_paths["concentration"] = path

    fig, ax = plt.subplots(figsize=(8.8, 4.0), dpi=180)
    style_plot(ax)
    costs = data["costs"]
    ax.plot(costs.extra_slippage_bps_each_side, costs.profit_factor_rub, color="#27D3C2", linewidth=3, marker="o")
    ax.axhline(1, color="#FF7373", linestyle="--", linewidth=1)
    ax.set_xlabel("Дополнительное проскальзывание, б.п. на сторону", color="#A9BCC5")
    ax.set_ylabel("Profit Factor", color="#A9BCC5")
    ax.set_xticks(costs.extra_slippage_bps_each_side)
    for x, y in zip(costs.extra_slippage_bps_each_side, costs.profit_factor_rub):
        ax.text(x, y + 0.11, f"{y:.2f}", ha="center", color="#EAF2F5", fontsize=9)
    fig.tight_layout()
    path = WORK / "cost_sensitivity.png"
    fig.savefig(path, transparent=False, bbox_inches="tight")
    plt.close(fig)
    chart_paths["costs"] = path

    fig, ax = plt.subplots(figsize=(8.8, 4.0), dpi=180)
    style_plot(ax)
    stop = data["stop"]
    ax.plot(stop.stop_pct, stop.profit_factor, color="#27D3C2", linewidth=2.5, marker="o", label="Full PF")
    ax.plot(stop.stop_pct, stop.test_profit_factor, color="#FFB257", linewidth=2.5, marker="o", label="OOS PF")
    ax.axvline(1.5, color="#69A7FF", linestyle="--", linewidth=1.2)
    ax.set_xlabel("Стоп, % от средней позиции", color="#A9BCC5")
    ax.set_ylabel("Profit Factor", color="#A9BCC5")
    ax.legend(frameon=False, labelcolor="#EAF2F5")
    fig.tight_layout()
    path = WORK / "stop_robustness.png"
    fig.savefig(path, transparent=False, bbox_inches="tight")
    plt.close(fig)
    chart_paths["stop"] = path

    return chart_paths


def add_text(slide, x, y, w, h, text, size=16, color=TEXT, bold=False, align=PP_ALIGN.LEFT, font="Aptos"):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Pt(1)
    frame.margin_right = Pt(1)
    frame.margin_top = Pt(1)
    frame.margin_bottom = Pt(1)
    p = frame.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = rgb(color)
    return box


def add_panel(slide, x, y, w, h, fill=PANEL, line=GRID, radius=True):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line)
    shape.line.width = Pt(0.8)
    return shape


def add_slide_base(prs, number, title, subtitle=""):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = rgb(BG)
    add_text(slide, 0.6, 0.34, 11.5, 0.45, title, 25, TEXT, True)
    if subtitle:
        add_text(slide, 0.62, 0.83, 11.6, 0.34, subtitle, 10.5, MUTED)
    add_text(slide, 12.25, 0.39, 0.45, 0.25, f"{number:02d}", 9.5, MUTED, False, PP_ALIGN.RIGHT)
    return slide


def metric_card(slide, x, y, w, h, label, value, note="", value_color=TEAL):
    add_panel(slide, x, y, w, h)
    add_text(slide, x + 0.17, y + 0.12, w - 0.34, 0.26, label.upper(), 9, MUTED, True)
    add_text(slide, x + 0.17, y + 0.45, w - 0.34, 0.45, value, 23, value_color, True)
    if note:
        add_text(slide, x + 0.17, y + h - 0.38, w - 0.34, 0.26, note, 8.5, MUTED)


def bullet_list(slide, x, y, w, h, items, size=13.5, colors=None):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Pt(2)
    frame.margin_right = Pt(2)
    for idx, item in enumerate(items):
        p = frame.paragraphs[0] if idx == 0 else frame.add_paragraph()
        p.text = item
        p.font.name = "Aptos"
        p.font.size = Pt(size)
        p.font.color.rgb = rgb(colors[idx] if colors else TEXT)
        p.space_after = Pt(8)
        p.level = 0
        p.text = "• " + p.text
    return box


def replace_text(prs):
    replacements = {
        "+91,27%": "Σ +91,27 п.п.",
        "+2,46 млн ₽ · эквивалент +91,27%": "+2,46 млн ₽ · не CAGR / не доходность счёта",
        "+91,27%\nобщая сумма результатов": "+91,27 п.п.\nсумма нормированных результатов",
        "+91,27%\nсумма результатов": "+91,27 п.п.\nсумма нормированных результатов",
    }
    for slide in prs.slides:
        for shape in slide.shapes:
            if not hasattr(shape, "text_frame") or not shape.has_text_frame:
                continue
            text = shape.text
            if text in replacements:
                shape.text_frame.paragraphs[0].runs[0].text = replacements[text]
            elif slide == prs.slides[4]:
                if "173" in text and "кандидат" in text:
                    shape.text = text.replace("173", "185").replace(
                        "кандидата после ограничения импульса <2,5%",
                        "исполненных коррекций −0,4% до cap/time",
                    )
                if "12" in text and "кандидат" in text and "2,5" in text:
                    shape.text = "12\nкандидатов ≥2,5% исключено → остаётся 173"
    # Slide 5 uses separate text boxes for values and labels, so repair the
    # funnel explicitly rather than relying on combined-text replacement.
    slide5 = prs.slides[4]
    for shape in slide5.shapes:
        if not hasattr(shape, "text"):
            continue
        current = shape.text.strip()
        if current == "173":
            shape.text = "185"
        elif current == "кандидата после ограничения импульса <2,5%":
            shape.text = "исполненных коррекций −0,4%"
        elif current == "кандидатов с импульсом ≥2,5% исключены как кульминация":
            shape.text = "кандидатов ≥2,5% исключено → остаётся 173"
    # Audit badges and clarified funnel note.
    slide1 = prs.slides[0]
    add_panel(slide1, 10.42, 0.17, 2.05, 0.36, fill="173746", line=TEAL)
    add_text(slide1, 10.52, 0.225, 1.83, 0.20, "AUDIT UPDATE · 27.08.2026", 8.5, TEAL, True, PP_ALIGN.CENTER)
    add_panel(slide5, 7.13, 5.05, 5.52, 0.52, fill="173746", line=ORANGE)
    add_text(slide5, 7.33, 5.18, 5.12, 0.25, "234 − 49 без fill = 185 − 12 cap = 173 − 49 late = 124", 9.2, ORANGE, True, PP_ALIGN.CENTER)


def add_audit_slides(prs, data, charts):
    n = len(prs.slides) + 1

    slide = add_slide_base(prs, n, "Аудит: что подтверждено данными", "Проверены 39 XLSX, 17 PPTX, исходный CSV, конфигурации и код сборки")
    n += 1
    metric_card(slide, 0.65, 1.38, 2.65, 1.25, "Финальные сделки", "124", "полный trade-list")
    metric_card(slide, 3.48, 1.38, 2.65, 1.25, "Chronological OOS", "42", "после 15.10.2025", ORANGE)
    metric_card(slide, 6.31, 1.38, 2.65, 1.25, "OOS PF", "4,06", "по ₽ P&L", ORANGE)
    metric_card(slide, 9.14, 1.38, 3.15, 1.25, "OOS результат", "+28,63 п.п.", "closed trades", ORANGE)
    add_panel(slide, 0.65, 2.95, 5.72, 3.25)
    add_text(slide, 0.88, 3.18, 5.25, 0.30, "ПОДТВЕРЖДЕНО", 11, GREEN, True)
    bullet_list(slide, 0.88, 3.62, 5.10, 2.25, [
        "Сводная арифметика и разрезы выходов сходятся",
        "Train и OOS положительны; PF близок: 4,02 / 4,06",
        "Воронка восстановлена до 234 → 185 → 173 → 124",
        "Стоп 1,25–2,0% и несколько time-exit вариантов сохраняют положительный результат",
    ], 12.3)
    add_panel(slide, 6.62, 2.95, 5.67, 3.25)
    add_text(slide, 6.85, 3.18, 5.20, 0.30, "НЕ ПОДТВЕРЖДЕНО", 11, RED, True)
    bullet_list(slide, 6.85, 3.62, 5.05, 2.25, [
        "Intratrade / daily mark-to-market drawdown",
        "Реальное bid/ask, очередь лимитной заявки и проскальзывание",
        "Достаточный капитал и историческое гарантийное обеспечение",
        "Независимый live-forward после фиксации всех правил",
    ], 12.3)

    slide = add_slide_base(prs, n, "Train и OOS: результат сохранился", "OOS — полностью более поздние сделки, но всё ещё только 42 наблюдения")
    n += 1
    slide.shapes.add_picture(str(charts["train_oos"]), Inches(0.65), Inches(1.34), width=Inches(7.15))
    metric_card(slide, 8.08, 1.42, 1.90, 1.28, "Train PF", "4,02", "82 сделки")
    metric_card(slide, 10.20, 1.42, 1.90, 1.28, "OOS PF", "4,06", "42 сделки", ORANGE)
    metric_card(slide, 8.08, 2.90, 1.90, 1.28, "Train DD", "−4,00", "п.п.; closed")
    metric_card(slide, 10.20, 2.90, 1.90, 1.28, "OOS DD", "−3,63", "п.п.; closed", ORANGE)
    add_panel(slide, 8.08, 4.48, 4.02, 1.53, fill="173746", line=ORANGE)
    add_text(slide, 8.30, 4.70, 3.58, 0.30, "Положительный OOS — сильный аргумент", 12, ORANGE, True)
    add_text(slide, 8.30, 5.10, 3.58, 0.62, "Но split использовался при выборе cap, stop и time-exit. Поэтому это validation-set, а не окончательный untouched test.", 10.5, TEXT)

    slide = add_slide_base(prs, n, "Прибыль концентрирована, но не одной сделкой", "Медиана сделки −0,029%; edge создаётся редкими крупными продолжениями")
    n += 1
    slide.shapes.add_picture(str(charts["concentration"]), Inches(0.65), Inches(1.33), width=Inches(7.35))
    c = data["concentration"].set_index("top_n")
    metric_card(slide, 8.22, 1.42, 3.85, 1.20, "Лучшая сделка", "+19,11 п.п.", "20,9% общего результата", ORANGE)
    metric_card(slide, 8.22, 2.82, 3.85, 1.20, "Без Top 5", f"+{c.loc[5, 'sum_without_top_points']:.2f} п.п.", f"PF {c.loc[5, 'pf_rub_without_top']:.2f}")
    metric_card(slide, 8.22, 4.22, 3.85, 1.20, "Без Top 10", f"+{c.loc[10, 'sum_without_top_points']:.2f} п.п.", f"PF {c.loc[10, 'pf_rub_without_top']:.2f}")
    add_text(slide, 8.24, 5.67, 3.85, 0.43, "Вывод: стратегия остаётся положительной, но PF 4,03 существенно зависит от хвоста распределения.", 10.5, MUTED)

    slide = add_slide_base(prs, n, "Проскальзывание: запас есть, но DD растёт", "Сценарий применяет одинаковые дополнительные bps ко всему исполненному notional; комиссия уже включена")
    n += 1
    slide.shapes.add_picture(str(charts["costs"]), Inches(0.65), Inches(1.35), width=Inches(7.45))
    costs = data["costs"].set_index("extra_slippage_bps_each_side")
    metric_card(slide, 8.32, 1.44, 3.70, 1.16, "0 б.п. / сторона", "PF 4,03", "+91,27 п.п.")
    metric_card(slide, 8.32, 2.80, 3.70, 1.16, "5 б.п. / сторона", f"PF {costs.loc[5, 'profit_factor_rub']:.2f}", f"+{costs.loc[5, 'sum_return_points']:.2f} п.п.", ORANGE)
    metric_card(slide, 8.32, 4.16, 3.70, 1.16, "10 б.п. / сторона", f"PF {costs.loc[10, 'profit_factor_rub']:.2f}", f"+{costs.loc[10, 'sum_return_points']:.2f} п.п.", ORANGE)
    add_text(slide, 8.34, 5.64, 3.70, 0.48, "Это sensitivity, не замена тиковому моделированию очереди и bid/ask.", 10.3, MUTED)

    slide = add_slide_base(prs, n, "Устойчивость стопа: не одиночный пик", "Полный и OOS результаты положительны на соседних значениях; максимум всё же выбран по данным")
    n += 1
    slide.shapes.add_picture(str(charts["stop"]), Inches(0.65), Inches(1.30), width=Inches(7.45))
    metric_card(slide, 8.35, 1.48, 3.65, 1.20, "Рабочая зона", "1,25–2,0%", "PF full 3,68–4,03")
    metric_card(slide, 8.35, 2.90, 3.65, 1.20, "Выбранный стоп", "1,50%", "лучший full и OOS", ORANGE)
    add_panel(slide, 8.35, 4.35, 3.65, 1.45, fill="173746", line=ORANGE)
    add_text(slide, 8.57, 4.57, 3.20, 0.28, "Практическое правило", 11, ORANGE, True)
    add_text(slide, 8.57, 4.96, 3.20, 0.58, "Не менять 1,5% без нового независимого блока данных. Соседние значения использовать как robustness-check.", 10.4, TEXT)

    slide = add_slide_base(prs, n, "Multiple testing: OOS уже участвовал в выборе", "В файлах видна широкая исследовательская цепочка; точный effective trial count восстановить нельзя")
    n += 1
    metric_card(slide, 0.70, 1.40, 2.55, 1.30, "ST grid", "400", "иная базовая revers-модель", RED)
    metric_card(slide, 3.43, 1.40, 2.55, 1.30, "Прямые sweeps", "≥57", "stop/time/impulse/DMI", ORANGE)
    metric_card(slide, 6.16, 1.40, 2.55, 1.30, "ST19/4 grid", "9/9 −", "OOS-сегменты отрицательны", RED)
    metric_card(slide, 8.89, 1.40, 3.05, 1.30, "Seed gates", "FAILED", "для revers grid", RED)
    add_panel(slide, 0.70, 3.05, 11.24, 2.80)
    bullet_list(slide, 0.98, 3.34, 10.66, 2.10, [
        "400-точечный grid относится к другой revers-стратегии и не опровергает финальную механику — но не может служить её подтверждением.",
        "Стоп 1,5%, выход 30 минут и cap 2,5% сравнивались также по OOS; поэтому OOS корректнее называть validation-set.",
        "Bootstrap CI не учитывает выбор лучшего варианта. Следующий блок должен быть полностью untouched/live-forward.",
    ], 12.2)

    slide = add_slide_base(prs, n, "Bootstrap риска: исторический DD не является пределом", "50 000 moving-block выборок по 5 сделок; сохраняется часть серийной зависимости")
    n += 1
    b = data["bootstrap"].set_index("segment").loc["Full"]
    metric_card(slide, 0.72, 1.45, 2.55, 1.30, "Исторический DD", "−4,00 п.п.", "closed trades")
    metric_card(slide, 3.48, 1.45, 2.55, 1.30, "Медианный DD", f"{b.dd_median:.2f} п.п.", "bootstrap")
    metric_card(slide, 6.24, 1.45, 2.55, 1.30, "95% worst DD", f"{b.dd_95_worst:.2f} п.п.", "bootstrap", ORANGE)
    metric_card(slide, 9.00, 1.45, 2.55, 1.30, "99% worst DD", f"{b.dd_99_worst:.2f} п.п.", "bootstrap", RED)
    add_panel(slide, 0.72, 3.07, 5.35, 2.45)
    add_text(slide, 0.98, 3.34, 4.85, 0.30, "СЕРИИ УБЫТКОВ", 11, ORANGE, True)
    bullet_list(slide, 0.98, 3.78, 4.80, 1.35, [
        f"Медиана максимальной серии: {b.streak_median:.0f}",
        f"95-й процентиль: {b.streak_95:.0f}",
        f"P(серия ≥8) ≈ {100*b.prob_streak_at_least_8:.1f}%",
    ], 12.2)
    add_panel(slide, 6.34, 3.07, 5.21, 2.45)
    add_text(slide, 6.60, 3.34, 4.70, 0.30, "ОГРАНИЧЕНИЕ", 11, RED, True)
    add_text(slide, 6.60, 3.82, 4.62, 1.30, "Bootstrap предполагает, что историческое распределение сохранится. Он не моделирует смену режима, selection bias, intrabar path и ликвидность.", 12, TEXT)

    slide = add_slide_base(prs, n, "Проценты и капитал: правильная интерпретация", "Фиксированные 10→20 контрактов дали +2,46 млн ₽; масштаб счёта в бэктесте не задан")
    n += 1
    cap = data["capital"].set_index("initial_capital_rub")
    metric_card(slide, 0.72, 1.45, 3.42, 1.32, "Сумма сделок", "+91,27 п.п.", "не CAGR / не compounding", ORANGE)
    metric_card(slide, 4.38, 1.45, 3.42, 1.32, "P&L 10→20", "+2,46 млн ₽", "фиксированный размер")
    metric_card(slide, 8.04, 1.45, 3.42, 1.32, "Closed DD", "−100,7 тыс. ₽", "не intratrade MTM", RED)
    add_panel(slide, 0.72, 3.08, 10.74, 2.52)
    add_text(slide, 1.02, 3.34, 10.10, 0.30, "ИЛЛЮСТРАЦИЯ P&L / НАЧАЛЬНЫЙ КАПИТАЛ", 11, MUTED, True)
    rows = [(1_000_000, "1,0 млн"), (2_500_000, "2,5 млн"), (5_000_000, "5,0 млн")]
    for idx, (amount, label) in enumerate(rows):
        x = 1.02 + idx * 3.32
        add_text(slide, x, 3.90, 2.80, 0.28, label, 11, MUTED, True, PP_ALIGN.CENTER)
        add_text(slide, x, 4.29, 2.80, 0.42, f"P&L {cap.loc[amount, 'fixed_contract_pnl_pct_of_capital']:+.1f}%", 17, TEAL, True, PP_ALIGN.CENTER)
        add_text(slide, x, 4.78, 2.80, 0.28, f"closed DD {cap.loc[amount, 'closed_trade_dd_pct_of_capital']:.1f}%", 10.5, RED, False, PP_ALIGN.CENTER)
    add_text(slide, 0.78, 5.83, 10.65, 0.32, "До расчёта исторического ГО и intraday MTM эти сценарии нельзя использовать для выбора минимального депозита.", 10.2, MUTED, False, PP_ALIGN.CENTER)

    slide = add_slide_base(prs, n, "Архитектура исполнения: фиксированный приоритет", "Это должно быть одинаково реализовано в backtest, paper и live")
    n += 1
    steps = [
        ("1", "Завершить импульс", "только закрытый minute bar; первый GREEN дня"),
        ("2", "Проверить фильтры", ">0,7%; <2,5%; Close > adjusted Open"),
        ("3", "Поставить Long10", "−0,4% от high; fill строго до 15:00"),
        ("4", "Сразу включить стоп", "1,5% от средней; gap → фактический Open"),
        ("5", "Проверять add", "Close > high → +10 next Open; только RVol ≤2,923"),
        ("6", "Контроль 30 минут", "первый Close при elapsed ≥30; после stop/add событий"),
        ("7", "Новая сессия", "Open gap-stop → Low stop → иначе Close первой минуты"),
    ]
    for idx, (num, title, note) in enumerate(steps):
        y = 1.30 + idx * 0.70
        add_panel(slide, 0.75, y, 0.48, 0.48, fill="173746", line=TEAL)
        add_text(slide, 0.75, y + 0.075, 0.48, 0.22, num, 11, TEAL, True, PP_ALIGN.CENTER)
        add_text(slide, 1.47, y + 0.02, 3.20, 0.26, title, 12.2, TEXT, True)
        add_text(slide, 4.55, y + 0.02, 7.45, 0.30, note, 11.2, MUTED)
    add_panel(slide, 0.75, 6.29, 11.25, 0.55, fill="321E26", line=RED)
    add_text(slide, 0.98, 6.44, 10.78, 0.26, "Одновременные события внутри OHLC-минуты разрешать консервативно или проверять на тиках.", 10.5, RED, True, PP_ALIGN.CENTER)

    slide = add_slide_base(prs, n, "Решение по стратегии после расширенного аудита", "Статус повышен с «прототип» до «кандидат на ограниченный forward-test»")
    n += 1
    metric_card(slide, 0.72, 1.45, 2.48, 1.25, "Логика", "7/10", "последовательна")
    metric_card(slide, 3.42, 1.45, 2.48, 1.25, "Математика", "8/10", "сводки сходятся")
    metric_card(slide, 6.12, 1.45, 2.48, 1.25, "Валидация", "6/10", "положительный OOS", ORANGE)
    metric_card(slide, 8.82, 1.45, 2.72, 1.25, "Live readiness", "4/10", "нет tick/MTM", RED)
    add_panel(slide, 0.72, 3.05, 5.20, 2.85)
    add_text(slide, 0.98, 3.32, 4.70, 0.30, "МОЖНО", 12, GREEN, True)
    bullet_list(slide, 0.98, 3.75, 4.62, 1.80, [
        "Paper trading по зафиксированным правилам",
        "Затем 1 контракт без изменения параметров",
        "Собирать фактический bid/ask и slippage",
    ], 12.2)
    add_panel(slide, 6.18, 3.05, 5.36, 2.85)
    add_text(slide, 6.44, 3.32, 4.86, 0.30, "ПОКА НЕЛЬЗЯ", 12, RED, True)
    bullet_list(slide, 6.44, 3.75, 4.74, 1.80, [
        "Запускать сразу 10→20 контрактов",
        "Считать +91,27 п.п. доходностью счёта",
        "Оптимизировать параметры на следующих убытках",
    ], 12.2)
    add_text(slide, 0.75, 6.25, 10.80, 0.40, "Критерий следующего допуска: минимум 30–50 новых untouched сделок и совпадение live fills с консервативной моделью.", 12.2, ORANGE, True, PP_ALIGN.CENTER)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    data = load_and_calculate()
    save_workbook(data)
    charts = make_charts(data)
    prs = Presentation(BASE_PPTX)
    replace_text(prs)
    add_audit_slides(prs, data, charts)
    prs.core_properties.title = "MIX ST 19/4 — audited and augmented strategy"
    prs.core_properties.subject = "Expanded quantitative audit, OOS, cost sensitivity, risk and execution architecture"
    prs.core_properties.comments = "Original source presentation preserved; this is a separate augmented copy."
    prs.save(OUT_PPTX)
    print(f"PPTX={OUT_PPTX}")
    print(f"XLSX={OUT_XLSX}")
    print(data["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
