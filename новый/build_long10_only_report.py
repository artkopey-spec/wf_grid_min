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


SOURCE_XLSX = Path(os.environ["MIX_VOLUME_TEST_XLSX"])
BASE_PPTX = Path(os.environ["MIX_VOLUME_TEST_PPTX"])
WORK = Path(os.environ.get("MIX_LONG10_WORK", Path.cwd() / "mix_long10_output"))
OUT_XLSX = WORK / "MIX_ST_19_4_LONG10_ONLY_FINAL_BACKTEST.xlsx"
OUT_PPTX = WORK / "MIX_ST_19_4_FINAL_AUDITED_LONG10_ONLY.pptx"
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


def longest_streak(values, predicate):
    best = current = 0
    for value in values:
        current = current + 1 if predicate(value) else 0
        best = max(best, current)
    return best


def stats(name, frame):
    frame = frame.sort_values("signal_time")
    ret = frame["return_pct"].to_numpy(float)
    pnl = frame["net_pnl_rub_10"].to_numpy(float)
    return {
        "segment": name,
        "trades": len(frame),
        "adds": int(frame["second_leg"].sum()),
        "wins": int((ret > 0).sum()),
        "losses": int((ret < 0).sum()),
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
        "max_consecutive_losses": longest_streak(ret, lambda value: value < 0),
        "max_consecutive_wins": longest_streak(ret, lambda value: value > 0),
    }


def calculate():
    trades = pd.read_excel(SOURCE_XLSX, sheet_name="Variant_4")
    for column in ("signal_time", "first_entry_time", "exit_time"):
        trades[column] = pd.to_datetime(trades[column])
    trades = trades.sort_values("signal_time").reset_index(drop=True)

    train = trades[trades["signal_time"] <= SPLIT]
    oos = trades[trades["signal_time"] > SPLIT]
    summary = pd.DataFrame([
        stats("Full", trades),
        stats("Train", train),
        stats("OOS", oos),
    ])

    yearly = pd.DataFrame([
        {"year": int(year), **stats(str(year), frame)}
        for year, frame in trades.groupby(trades["signal_time"].dt.year)
    ])
    exits = pd.DataFrame([
        {"exit_reason": reason, **stats(str(reason), frame)}
        for reason, frame in trades.groupby("exit_reason", sort=False)
    ])

    equity = trades[["movement_id", "signal_time", "first_entry_time", "exit_time", "return_pct", "net_pnl_rub_10"]].copy()
    equity["cumulative_return_points"] = equity["return_pct"].cumsum()
    equity["cumulative_pnl_rub"] = equity["net_pnl_rub_10"].cumsum()
    equity["drawdown_points"] = equity["cumulative_return_points"] - np.maximum.accumulate(np.r_[0.0, equity["cumulative_return_points"]])[-len(equity):]
    equity["drawdown_rub"] = equity["cumulative_pnl_rub"] - np.maximum.accumulate(np.r_[0.0, equity["cumulative_pnl_rub"]])[-len(equity):]

    inferred_notional = np.where(
        np.abs(trades["return_pct"].to_numpy(float)) > 1e-12,
        trades["net_pnl_rub_10"].to_numpy(float) / (trades["return_pct"].to_numpy(float) / 100),
        10 * trades["weighted_average_entry"].to_numpy(float),
    )
    cost_rows = []
    for bps in (0, 1, 2, 5, 10, 20):
        cost = bps / 10_000 * 10 * (
            trades["weighted_average_entry"].to_numpy(float) + trades["exit_price"].to_numpy(float)
        )
        adjusted_pnl = trades["net_pnl_rub_10"].to_numpy(float) - cost
        adjusted_return = adjusted_pnl / inferred_notional * 100
        cost_rows.append({
            "extra_slippage_bps_each_side": bps,
            "sum_return_points": adjusted_return.sum(),
            "average_return_pct": adjusted_return.mean(),
            "profit_factor_rub": profit_factor(adjusted_pnl),
            "closed_trade_max_dd_points": max_drawdown(adjusted_return),
            "net_pnl_rub": adjusted_pnl.sum(),
        })
    costs = pd.DataFrame(cost_rows)

    top = trades.sort_values("return_pct", ascending=False)
    concentration = []
    for n in (1, 3, 5, 10):
        kept = trades.drop(top.head(n).index)
        concentration.append({
            "top_n": n,
            "top_sum_return_points": top.head(n)["return_pct"].sum(),
            "sum_without_top_points": kept["return_pct"].sum(),
            "pf_without_top_rub": profit_factor(kept["net_pnl_rub_10"]),
        })
    concentration = pd.DataFrame(concentration)
    return trades, summary, yearly, exits, equity, costs, concentration


def save_workbook(trades, summary, yearly, exits, equity, costs, concentration):
    method = pd.DataFrame([
        ["Position", "Fixed Long10; no second leg under any RVol"],
        ["Signal", "First GREEN ST 19/4 impulse of day; 0.7-2.5%; final Close above adjusted Open"],
        ["Entry", "-0.4% correction; fill strictly before 15:00"],
        ["Stop", "1.5% below first-entry price"],
        ["Weakness", "At first Close with >=30 elapsed minutes, exit if Close <= entry"],
        ["Final exit", "Close of first available minute of next trading session"],
        ["Costs", "Commission included; slippage sensitivity supplied separately"],
        ["Percent convention", "Return normalized to the fixed Long10 notional"],
        ["OOS", f"signal_time > {SPLIT}"],
        ["Limitation", "Closed-trade DD; intratrade MTM, bid/ask and queue position not modelled"],
    ], columns=["item", "definition"])
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        trades.to_excel(writer, sheet_name="Trades", index=False)
        yearly.to_excel(writer, sheet_name="Yearly", index=False)
        exits.to_excel(writer, sheet_name="Exit_Reasons", index=False)
        equity.to_excel(writer, sheet_name="Equity", index=False)
        costs.to_excel(writer, sheet_name="Cost_Sensitivity", index=False)
        concentration.to_excel(writer, sheet_name="Concentration", index=False)
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


def metric(slide, x, y, w, label, value, note="", color=TEAL):
    panel(slide, x, y, w, 1.22, line=color)
    text_box(slide, x + 0.18, y + 0.14, w - 0.36, 0.22, label.upper(), 9, MUTED, True)
    text_box(slide, x + 0.18, y + 0.43, w - 0.36, 0.36, value, 20, color, True)
    text_box(slide, x + 0.18, y + 0.88, w - 0.36, 0.19, note, 8.5, MUTED)


def add_slide(summary, yearly, exits, costs):
    full = summary.set_index("segment").loc["Full"]
    oos = summary.set_index("segment").loc["OOS"]
    prs = Presentation(BASE_PPTX)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(BG)
    text_box(slide, 0.60, 0.35, 11.4, 0.43, "Упрощённый вариант: постоянный Long10", 24, TEXT, True)
    text_box(slide, 0.62, 0.84, 11.3, 0.30, "Объём и добавление исключены; сигнал, вход, стоп, 30 минут и финальный выход не меняются", 10.5, MUTED)
    text_box(slide, 12.18, 0.39, 0.45, 0.24, f"{len(prs.slides):02d}", 9, MUTED, False, PP_ALIGN.RIGHT)

    metric(slide, 0.70, 1.40, 2.55, "Сумма", f"+{full.sum_return_points:.2f} п.п.", "+1,69 млн ₽")
    metric(slide, 3.43, 1.40, 2.55, "Profit Factor", f"{full.profit_factor_rub:.2f}", "124 сделки")
    metric(slide, 6.16, 1.40, 2.55, "Closed DD", f"{full.closed_trade_max_dd_points:.2f} п.п.", f"{full.closed_trade_max_dd_rub/1000:.1f} тыс. ₽", RED)
    metric(slide, 8.89, 1.40, 2.95, "OOS", f"+{oos.sum_return_points:.2f} п.п.", f"PF {oos.profit_factor_rub:.2f}", ORANGE)

    panel(slide, 0.70, 2.97, 5.46, 2.52)
    text_box(slide, 0.96, 3.22, 4.96, 0.28, "СТРУКТУРА РЕЗУЛЬТАТА", 11, BLUE, True)
    exit_map = exits.set_index("exit_reason")
    lines = []
    for reason, row in exit_map.iterrows():
        lines.append(f"{reason}: {int(row.trades)} · Σ {row.sum_return_points:+.2f}")
    text_box(slide, 0.96, 3.68, 4.94, 1.48, "\n".join(lines), 12.2, TEXT)

    panel(slide, 6.40, 2.97, 5.44, 2.52)
    text_box(slide, 6.66, 3.22, 4.94, 0.28, "ПРОФИЛЬ РИСКА", 11, ORANGE, True)
    risk_lines = (
        f"Средняя сделка: {full.average_return_pct:+.3f}%\n"
        f"Медиана: {full.median_return_pct:+.3f}%\n"
        f"Худшая сделка: {full.worst_trade_pct:.3f}%\n"
        f"Макс. убытков подряд: {int(full.max_consecutive_losses)}\n"
        f"10 б.п. slippage: PF {costs.set_index('extra_slippage_bps_each_side').loc[10,'profit_factor_rub']:.2f}"
    )
    text_box(slide, 6.66, 3.68, 4.92, 1.52, risk_lines, 12.0, TEXT)

    panel(slide, 0.70, 5.78, 11.14, 0.70, fill="173746", line=BLUE)
    text_box(slide, 0.94, 5.97, 10.68, 0.32, "Long10 сохраняет устойчивый edge и уменьшает размер худшей сделки, но отдаёт 28,76 п.п. результата относительно управляемого 10→20.", 10.7, BLUE, True, PP_ALIGN.CENTER)
    prs.save(OUT_PPTX)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    trades, summary, yearly, exits, equity, costs, concentration = calculate()
    save_workbook(trades, summary, yearly, exits, equity, costs, concentration)
    add_slide(summary, yearly, exits, costs)
    print(summary.to_string(index=False))
    print(yearly.to_string(index=False))
    print(exits.to_string(index=False))
    print(costs.to_string(index=False))
    print(f"XLSX={OUT_XLSX}")
    print(f"PPTX={OUT_PPTX}")


if __name__ == "__main__":
    main()
