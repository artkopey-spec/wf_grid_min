from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from analyze_mix_st19 import read_bars, read_rollovers


SOURCE = Path("MIX_ST_19_4_FINAL_30min_entry_before15_first_bar_close_backtest.xlsx")
ATR_SOURCE = Path("MIX_ST_19_4_impulse_end_ATR_quarters_no_volume.xlsx")
OUT = Path("MIX_ST_19_4_FINAL_30min_first_bar_close_strategy.pptx")
ART = Path("presentation_assets")

W, H = 13.333, 7.5
BG = "07141E"
PANEL = "0D2230"
PANEL2 = "102A39"
GRID = "294653"
TEXT = "F4F8FA"
MUTED = "A5B7C1"
TEAL = "20D3C2"
GREEN = "8CE06F"
ORANGE = "FFB45B"
RED = "FF6B6B"
BLUE = "6DA9FF"


def rgb(value):
    return RGBColor.from_string(value)


def rect(slide, x, y, w, h, fill=PANEL, radius=True, line=None):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line or fill)
    if radius:
        try:
            shape.adjustments[0] = 0.07
        except Exception:
            pass
    return shape


def text(slide, value, x, y, w, h, size=18, color=TEXT, bold=False,
         align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0.02)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    frame.vertical_anchor = valign
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    paragraph.space_after = Pt(0)
    paragraph.line_spacing = 1.0
    run = paragraph.add_run()
    run.text = str(value)
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = rgb(color)
    return box


def line(slide, x1, y1, x2, y2, color=GRID, width=1.2):
    shape = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2)
    )
    shape.line.color.rgb = rgb(color)
    shape.line.width = Pt(width)
    return shape


def circle(slide, cx, cy, diameter, fill, label=""):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.OVAL,
        Inches(cx - diameter / 2), Inches(cy - diameter / 2), Inches(diameter), Inches(diameter),
    )
    shape.fill.solid(); shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(fill)
    if label:
        text(slide, label, cx-diameter/2, cy-diameter/2, diameter, diameter,
             11, BG, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    return shape


def base(prs, title_value, subtitle, number):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = rgb(BG)
    rect(slide, 0, 0, 0.13, H, TEAL, False)
    text(slide, title_value, 0.68, 0.28, 10.7, 0.50, 25, TEXT, True)
    text(slide, "MIX · ST 19/4", 11.0, 0.34, 1.62, 0.24, 10, MUTED, True, PP_ALIGN.RIGHT)
    line(slide, 0.68, 0.94, 12.62, 0.94)
    text(slide, subtitle, 0.70, 1.03, 11.8, 0.34, 12, MUTED)
    text(slide, "21.08.2023–21.08.2026 · минутные данные · 10→20 контрактов", 0.70, 7.17, 8.0, 0.18, 8, MUTED)
    text(slide, f"{number:02d}", 12.12, 7.13, 0.48, 0.22, 9, MUTED, True, PP_ALIGN.RIGHT)
    return slide


def metric(slide, x, y, w, value, label, color=TEAL, size=22):
    rect(slide, x, y, w, 1.05, PANEL, True, GRID)
    rect(slide, x, y, 0.06, 1.05, color, False)
    text(slide, value, x+0.22, y+0.13, w-0.35, 0.40, size, TEXT, True)
    text(slide, label, x+0.22, y+0.63, w-0.35, 0.22, 9.5, MUTED)


def fmt_pct(value, digits=2, signed=True):
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{digits}f}%".replace(".", ",")


def fmt_num(value, digits=2):
    return f"{value:.{digits}f}".replace(".", ",")


final_trades = pd.read_excel(SOURCE, sheet_name="Trades")
final_trades["signal_time"] = pd.to_datetime(final_trades["signal_time"])
final_trades["first_entry_time"] = pd.to_datetime(final_trades["first_entry_time"])
final_trades["exit_time"] = pd.to_datetime(final_trades["exit_time"])
# Final simplified time rule: the first Long10 must be filled strictly before
# 15:00. Because the entry follows the initiating impulse, this single check
# also guarantees the impulse ended before 15:00. Adds after 15:00 remain part
# of position management. There is no weak/time exit after entry.
final_trades = final_trades[final_trades["first_entry_time"].dt.hour < 15].copy()
final_trades = final_trades.sort_values("signal_time").reset_index(drop=True)

returns = final_trades["return_pct"].astype(float)
pnl = final_trades["net_pnl_rub_10"].astype(float)
equity = pd.DataFrame({
    "signal_time": final_trades["signal_time"],
    "cumulative_return_pct": returns.cumsum(),
})
return_curve = returns.cumsum()
return_dd = return_curve - return_curve.cummax().clip(lower=0)
pnl_curve = pnl.cumsum()
pnl_dd = pnl_curve - pnl_curve.cummax().clip(lower=0)
filtered = pd.Series({
    "trades": len(final_trades),
    "adds": int(final_trades["second_leg"].sum()),
    "second_legs": int(final_trades["second_leg"].sum()),
    "win_rate_pct": 100 * (returns > 0).mean(),
    "average_return_pct": returns.mean(),
    "sum_return_pct": returns.sum(),
    "profit_factor": pnl[pnl > 0].sum() / -pnl[pnl < 0].sum(),
    "max_drawdown_pct": return_dd.min(),
    "max_drawdown_rub": pnl_dd.min(),
    "net_pnl_rub_10": pnl.sum(),
    "worst_return_pct": returns.min(),
})
exits = (
    final_trades.groupby("exit_reason", sort=False)
    .agg(trades=("movement_id", "size"), average_return_pct=("return_pct", "mean"), sum_return_pct=("return_pct", "sum"))
    .reset_index()
)
years = (
    final_trades.assign(year=final_trades["signal_time"].dt.year)
    .groupby("year", as_index=False)
    .agg(sum_return_pct=("return_pct", "sum"))
)
atr = pd.read_excel(ATR_SOURCE, sheet_name="ATR_Quarter_Summary")

ART.mkdir(exist_ok=True)
equity_path = ART / "mix_above_open_equity.png"
year_path = ART / "mix_above_open_years.png"

plt.rcParams.update({"font.family": "DejaVu Sans"})
fig, ax = plt.subplots(figsize=(10.8, 3.6), facecolor="#" + PANEL)
ax.set_facecolor("#" + PANEL)
ax.plot(equity["signal_time"], equity["cumulative_return_pct"], color="#" + TEAL, lw=2.4)
ax.axhline(0, color="#" + GRID, lw=1)
ax.grid(True, axis="y", color="#" + GRID, alpha=0.65)
ax.tick_params(colors="#" + MUTED, labelsize=8)
for spine in ax.spines.values(): spine.set_visible(False)
ax.set_ylabel("Сумма результатов, %", color="#" + MUTED, fontsize=9)
fig.tight_layout(pad=0.8)
fig.savefig(equity_path, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
plt.close(fig)

fig, ax = plt.subplots(figsize=(8.8, 3.4), facecolor="#" + PANEL)
ax.set_facecolor("#" + PANEL)
colors = ["#" + TEAL if y < 2026 else "#" + ORANGE for y in years["year"]]
bars = ax.bar(years["year"].astype(str), years["sum_return_pct"], color=colors, width=0.58)
for bar, value in zip(bars, years["sum_return_pct"]):
    ax.text(bar.get_x()+bar.get_width()/2, value+1.2, fmt_pct(value, 1), ha="center", color="#"+TEXT, fontsize=9, fontweight="bold")
ax.grid(True, axis="y", color="#" + GRID, alpha=0.65)
ax.tick_params(colors="#" + MUTED, labelsize=9)
for spine in ax.spines.values(): spine.set_visible(False)
ax.set_ylabel("Сумма результатов, %", color="#" + MUTED, fontsize=9)
fig.tight_layout(pad=0.8)
fig.savefig(year_path, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
plt.close(fig)

# Representative trades: closest to the median positive and median negative
# return of the final strategy. This avoids showcasing cherry-picked extremes.
entry_source = pd.read_excel("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx", sheet_name="Trades")
entry_source = entry_source[entry_source["variant"] == "100_at_0.4_plus_100_after_close_above_peak"].copy()
entry_source["leg_04_time"] = pd.to_datetime(entry_source["leg_04_time"])
entry_source["breakout_time"] = pd.to_datetime(entry_source["breakout_time"])
movements = pd.read_excel("MIX_ST_19_4_analysis.xlsx", sheet_name="Movements_Sorted")
movements["start_time"] = pd.to_datetime(movements["start_time"])
movement_start = movements.set_index("movement_id")["start_time"].to_dict()

trade_examples = final_trades.merge(
    entry_source[["movement_id", "impulse_pct", "leg_04_time", "leg_04_price", "breakout_time", "breakout_price"]],
    on="movement_id", how="left",
)
trade_examples["movement_start"] = trade_examples["movement_id"].map(movement_start)

def median_example(frame):
    target = frame["return_pct"].median()
    # Prefer an impulse completed within one session so the teaching chart is
    # readable, while keeping the target equal to the full group's median.
    intraday = frame[
        pd.to_datetime(frame["movement_start"]).dt.date
        == pd.to_datetime(frame["signal_time"]).dt.date
    ]
    candidates = intraday if not intraday.empty else frame
    return candidates.loc[(candidates["return_pct"] - target).abs().idxmin()].copy()

typical_profit = median_example(trade_examples[trade_examples["return_pct"] > 0])
typical_loss = median_example(trade_examples[trade_examples["return_pct"] < 0])

price_bars = read_bars()
rolls = read_rollovers(price_bars)
offset = 0.0; roll_cursor = 0; price_by_time = {}
for bar in price_bars:
    while roll_cursor < len(rolls) and rolls[roll_cursor]["effective_index"] == bar["index"]:
        offset += rolls[roll_cursor]["offset_delta"]
        roll_cursor += 1
    bar["offset"] = offset
    price_by_time[bar["time"]] = bar

def trade_chart(row, path, positive):
    signal_bar = price_by_time[row["signal_time"]]
    entry_bar = price_by_time[row["leg_04_time"]]
    exit_bar = price_by_time[row["exit_time"]]
    start_bar = price_by_time[movement_start[int(row["movement_id"])]]
    anchor_offset = signal_bar["offset"]
    lo = max(0, start_bar["index"] - 8)
    hi = min(len(price_bars), exit_bar["index"] + 8)
    window = price_bars[lo:hi]
    xs = list(range(len(window)))

    def px(bar, field):
        return bar[field] + bar["offset"] - anchor_offset

    closes = [px(bar, "close") for bar in window]
    highs = [px(bar, "high") for bar in window]
    lows = [px(bar, "low") for bar in window]
    local = {bar["time"]: i for i, bar in enumerate(window)}
    signal_x = local[row["signal_time"]]
    entry_x = local[row["leg_04_time"]]
    exit_x = local[row["exit_time"]]
    signal_price = px(signal_bar, "close")
    entry_price = float(row["leg_04_price"])
    exit_price = px(exit_bar, "open" if row["exit_reason"] == "next_open" else "close")
    impulse_slice = price_bars[start_bar["index"]:signal_bar["index"] + 1]
    impulse_peak = max(px(bar, "high") for bar in impulse_slice)

    fig, ax = plt.subplots(figsize=(11.0, 4.3), facecolor="#" + PANEL)
    ax.set_facecolor("#" + PANEL)
    ax.fill_between(xs, lows, highs, color="#" + BLUE, alpha=0.08, linewidth=0)
    ax.plot(xs, closes, color="#" + TEXT, lw=1.25, alpha=0.92)
    ax.axhline(impulse_peak, color="#" + ORANGE, lw=1.0, ls="--", alpha=0.8)
    ax.axvspan(local.get(start_bar["time"], 0), signal_x, color="#" + GREEN, alpha=0.08)
    ax.scatter(signal_x, signal_price, s=55, color="#" + GREEN, zorder=5)
    ax.annotate("конец импульса", (signal_x, signal_price), xytext=(6, 12), textcoords="offset points", color="#"+GREEN, fontsize=8)
    ax.scatter(entry_x, entry_price, s=70, marker="^", color="#" + BLUE, zorder=6)
    ax.annotate("Long 10", (entry_x, entry_price), xytext=(6, -18), textcoords="offset points", color="#"+BLUE, fontsize=9, fontweight="bold")

    if bool(row["second_leg"]) and pd.notna(row["breakout_time"]) and row["breakout_time"] in local:
        add_x = local[row["breakout_time"]]
        add_price = float(row["breakout_price"])
        ax.scatter(add_x, add_price, s=72, marker="^", color="#" + ORANGE, zorder=6)
        ax.annotate("+10", (add_x, add_price), xytext=(6, 10), textcoords="offset points", color="#"+ORANGE, fontsize=9, fontweight="bold")
    elif pd.notna(row["breakout_time"]) and row["breakout_time"] <= row["exit_time"] and row["breakout_time"] in local:
        blocked_x = local[row["breakout_time"]]
        ax.scatter(blocked_x, float(row["breakout_price"]), s=60, marker="x", color="#" + ORANGE, zorder=6)
        ax.annotate("пробой; +10 запрещено объёмом", (blocked_x, float(row["breakout_price"])), xytext=(6, 10), textcoords="offset points", color="#"+ORANGE, fontsize=8)

    exit_color = GREEN if positive else RED
    ax.scatter(exit_x, exit_price, s=78, marker="v", color="#" + exit_color, zorder=7)
    ax.annotate("выход", (exit_x, exit_price), xytext=(-8, 12), ha="right", textcoords="offset points", color="#"+exit_color, fontsize=9, fontweight="bold")

    ticks = sorted(set(int(i*(len(window)-1)/6) for i in range(7)))
    ax.set_xticks(ticks)
    tick_labels = []
    previous_date = None
    for position, i in enumerate(ticks):
        stamp = window[i]["time"]
        if position == 0 or stamp.date() != previous_date:
            tick_labels.append(stamp.strftime("%d.%m\n%H:%M"))
        else:
            tick_labels.append(stamp.strftime("%H:%M"))
        previous_date = stamp.date()
    ax.set_xticklabels(tick_labels, color="#"+MUTED, fontsize=8)
    ax.tick_params(axis="y", colors="#" + MUTED, labelsize=8)
    ax.grid(True, axis="y", color="#" + GRID, alpha=0.55)
    for spine in ax.spines.values(): spine.set_visible(False)
    ax.set_ylabel("Цена MIX", color="#" + MUTED, fontsize=9)
    fig.tight_layout(pad=0.8)
    fig.savefig(path, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

profit_path = ART / "mix_typical_profit_trade.png"
loss_path = ART / "mix_typical_loss_trade.png"
trade_chart(typical_profit, profit_path, True)
trade_chart(typical_loss, loss_path, False)

prs = Presentation()
prs.slide_width = Inches(W); prs.slide_height = Inches(H)
prs.core_properties.title = "MIX ST 19/4 — финальная оптимизированная стратегия"
prs.core_properties.subject = "Long10 до 15:00, проверка слабой позиции через 30 минут и выход по Close первой минуты новой сессии"
prs.core_properties.author = "OpenAI Codex"

# 1 — cover
s = prs.slides.add_slide(prs.slide_layouts[6])
s.background.fill.solid(); s.background.fill.fore_color.rgb = rgb(BG)
rect(s, 0, 0, 0.16, H, TEAL, False)
text(s, "ФИНАЛЬНАЯ СТРАТЕГИЯ", 0.80, 0.72, 5.4, 0.28, 12, TEAL, True)
text(s, "MIX", 0.76, 1.14, 2.8, 0.84, 49, TEXT, True)
text(s, "SuperTrend 19 / 4", 0.80, 2.00, 5.8, 0.44, 25, MUTED)
text(s, "Финальная система", 0.80, 2.65, 6.2, 0.48, 29, TEXT, True)
text(s, "Open · импульс <2,5% · вход до 15:00 · объём · 30 минут", 0.82, 3.24, 6.5, 0.34, 15, ORANGE, True)

rect(s, 7.30, 0.68, 5.30, 4.55, PANEL, True, GRID)
text(s, "ИТОГОВЫЙ РЕЗУЛЬТАТ", 7.72, 1.02, 4.35, 0.26, 11, MUTED, True)
metric(s, 7.72, 1.48, 2.00, fmt_pct(filtered["average_return_pct"], 3), "средняя сделка", GREEN, 20)
metric(s, 9.92, 1.48, 2.18, fmt_num(filtered["profit_factor"], 2), "Profit Factor", TEAL, 23)
metric(s, 7.72, 2.76, 2.00, fmt_pct(filtered["max_drawdown_pct"], 2), "Max DD", ORANGE, 20)
metric(s, 9.92, 2.76, 2.18, fmt_pct(filtered["sum_return_pct"], 2), "сумма результатов", GREEN, 19)
cover_pnl = f"{filtered['net_pnl_rub_10']/1e6:+.2f}".replace(".", ",")
text(s, f"{cover_pnl} млн ₽ · эквивалент {fmt_pct(filtered['sum_return_pct'], 2)}", 7.76, 4.20, 4.20, 0.30, 16, TEXT, True)
text(s, "комиссия включена · проскальзывание не включено", 7.76, 4.61, 4.20, 0.24, 10, MUTED)

metric(s, 0.80, 5.15, 2.32, f"{int(filtered['trades'])}", "всего сделок", BLUE, 22)
metric(s, 3.30, 5.15, 1.84, f"{int(filtered['second_legs'])}", "добавлений +10", ORANGE, 22)
metric(s, 5.32, 5.15, 1.84, fmt_pct(filtered["win_rate_pct"], 1, False), "выигрышных сделок", TEAL, 19)
text(s, "21.08.2023–21.08.2026 · минуты · гэпы контрактов скорректированы", 0.82, 6.67, 7.1, 0.24, 10, MUTED)
text(s, "01", 12.10, 7.10, 0.45, 0.22, 9, MUTED, True, PP_ALIGN.RIGHT)

# 2 — algorithm
s = base(prs, "Точный алгоритм сделки", "Все решения формальны и доступны в момент исполнения", 2)
steps = [
    ("1", "ИМПУЛЬС", "Первый зелёный ST дня\n>0,7% и <2,5%", GREEN),
    ("2", "ФИЛЬТР", "Close импульса\nвыше Open дня", TEAL),
    ("3", "ВРЕМЯ / ВХОД", "Long10 до 15:00;\nкоррекция −0,4%", BLUE),
    ("4", "ДОБАВИТЬ 10", "Close > max; след. Open\nи объём ≤ 2,923×", ORANGE),
    ("5", "КОНТРОЛЬ / ВЫХОД", "30 мин · стоп −1,5% ·\nClose 1-й минуты", TEXT),
]
for i, (n, title_value, body, color) in enumerate(steps):
    x = 0.72 + i * 2.45
    rect(s, x, 1.55, 2.16, 2.22, PANEL, True, GRID)
    circle(s, x+0.34, 1.91, 0.38, color, n)
    text(s, title_value, x+0.62, 1.77, 1.35, 0.36, 10.5, color, True)
    text(s, body, x+0.24, 2.42, 1.72, 0.72, 12.5, TEXT, True)
    if i < 4: text(s, "→", x+2.18, 2.43, 0.27, 0.30, 17, MUTED, True, PP_ALIGN.CENTER)

rect(s, 0.72, 4.12, 11.90, 1.50, PANEL2, True, GRID)
text(s, "РИСК-КОНТРОЛЬ", 1.02, 4.40, 2.10, 0.24, 11, BLUE, True)
text(s, "Стоп −1,5%", 1.02, 4.86, 1.52, 0.30, 17, RED, True)
text(s, "от средней цены; после добавления пересчитать", 2.50, 4.90, 3.22, 0.27, 11, MUTED)
text(s, "Через 30 минут", 6.05, 4.86, 1.95, 0.30, 17, ORANGE, True)
text(s, "Close ≤ средней → закрыть всё; иначе держать", 8.02, 4.86, 3.70, 0.46, 11, MUTED)
rect(s, 0.72, 6.02, 11.90, 0.62, ORANGE, True, ORANGE)
text(s, "Первый Long10 должен быть исполнен строго до 15:00. Уже открытую позицию сопровождаем без ограничения времени; добавление после 15:00 разрешено.", 1.03, 6.20, 11.10, 0.25, 12, BG, True)

# 3 — ATR geometry
s = base(prs, "Почему важна цена окончания импульса", "Положение стандартизировано относительно открытия и дневного ATR(14)", 3)
zones = [
    ("−1…−0,5 ATR", 13, "+0,446%", "PF 3,24", ORANGE),
    ("−0,5…0 ATR", 69, "−0,011%", "PF 1,00", RED),
    ("0…+0,5 ATR", 159, "+0,557%", "PF 2,78", TEAL),
    ("+0,5…+1 ATR", 21, "+0,730%", "PF 12,12", GREEN),
]
for i, (band, trades, avg, pf, color) in enumerate(zones):
    x = 0.72 + i * 2.98
    rect(s, x, 1.52, 2.70, 2.36, PANEL, True, color)
    text(s, band, x+0.22, 1.78, 2.25, 0.30, 15, color, True, PP_ALIGN.CENTER)
    text(s, avg, x+0.22, 2.34, 2.25, 0.38, 23, TEXT, True, PP_ALIGN.CENTER)
    text(s, "средняя сделка", x+0.22, 2.75, 2.25, 0.22, 9.5, MUTED, False, PP_ALIGN.CENTER)
    text(s, f"{trades} сделок · {pf}", x+0.22, 3.28, 2.25, 0.24, 11, MUTED, True, PP_ALIGN.CENTER)

rect(s, 0.72, 4.25, 7.45, 1.76, PANEL2, True, GRID)
text(s, "ФИНАЛЬНЫЙ ФИЛЬТР", 1.02, 4.55, 2.30, 0.24, 11, TEAL, True)
text(s, "Торговать только если импульс закончился выше Open дня", 1.02, 5.03, 6.62, 0.34, 19, TEXT, True)
text(s, "ATR нужен для анализа геометрии; в рабочем правиле достаточно сравнить Close импульса с Open.", 1.02, 5.54, 6.55, 0.24, 10.5, MUTED)
rect(s, 8.42, 4.25, 4.20, 1.76, PANEL, True, GRID)
text(s, "Важно", 8.76, 4.55, 1.0, 0.24, 11, ORANGE, True)
text(s, "Зона −1…−0,5 ATR мала: всего 13 сделок; высокий PF держится на нескольких крупных выигрышах.", 8.76, 4.98, 3.32, 0.65, 12, TEXT, True)

# 4 — volume tiers
s = base(prs, "Объём решает только вопрос добавления", "Распределение показано до временного фильтра; первый вход повышенным объёмом не запрещается", 4)
tiers = [
    ("НИЖНИЕ 60%", "≤ 2,923×", "10 + 10", "140 сигналов", GREEN),
    ("ВЕРХНИЕ 40%", "> 2,923×", "только 10", "89 сигналов", TEAL),
    ("НЕТ СТАНДАРТА", "5 сигналов", "только 10", "добавление запрещено", ORANGE),
]
for i, (title_value, threshold, size_rule, count, color) in enumerate(tiers):
    x = 0.72 + i * 3.98
    rect(s, x, 1.55, 3.68, 2.58, PANEL, True, color)
    text(s, title_value, x+0.26, 1.83, 3.12, 0.28, 12, color, True, PP_ALIGN.CENTER)
    text(s, threshold, x+0.26, 2.30, 3.12, 0.36, 22, TEXT, True, PP_ALIGN.CENTER)
    text(s, size_rule, x+0.26, 2.83, 3.12, 0.34, 18, color, True, PP_ALIGN.CENTER)
    text(s, count, x+0.26, 3.48, 3.12, 0.24, 10.5, MUTED, True, PP_ALIGN.CENTER)
rect(s, 0.72, 4.48, 7.55, 1.48, PANEL2, True, GRID)
text(s, "КАК СЧИТАЕТСЯ", 1.03, 4.76, 1.82, 0.24, 11, BLUE, True)
text(s, "RVol импульса = фактический объём / стандартный объём", 1.03, 5.16, 6.65, 0.30, 17, TEXT, True)
text(s, "Стандарт — медиана объёма для тех же минут по предыдущим 60 наблюдениям.", 1.03, 5.56, 6.60, 0.24, 10.5, MUTED)
rect(s, 8.53, 4.48, 4.09, 1.48, PANEL, True, GRID)
text(s, "Главное правило", 8.87, 4.82, 1.62, 0.34, 16, ORANGE, True)
text(s, "объём никогда не отменяет первые 10 — он может отменить только добавление", 10.42, 4.73, 1.65, 0.72, 10.5, MUTED)
rect(s, 0.72, 6.28, 11.90, 0.54, ORANGE, True, ORANGE)
text(s, "Порог P60 = 2,923× рассчитан на ранних 70% истории и затем зафиксирован — будущие данные не использовались.", 1.03, 6.43, 11.05, 0.24, 11.5, BG, True)

# 5 — methodology
s = base(prs, "Что именно пересчитано", "Полная цепочка от минутных баров до итоговой сделки с новой верхней границей импульса", 5)
items = [
    ("630 299", "минутных баров", TEAL),
    ("326", "проверяемых первых импульсов >0,7%", GREEN),
    ("234", "импульса завершились выше открытия", BLUE),
    ("173", "кандидата после ограничения импульса <2,5%", BLUE),
    (f"{int(filtered['trades'])}", "финальных сделок с первым входом до 15:00", ORANGE),
]
for i, (value, label, color) in enumerate(items):
    metric(s, 0.72 + i*2.38, 1.50, 2.14, value, label, color, 20)

checks = [
    ("11", "межконтрактных гэпов скорректировано"),
    ("2", "ошибочных минутных бара исключено"),
    ("12", "кандидатов с импульсом ≥2,5% исключены как кульминация"),
    ("49", "сделок без первого входа до 15:00 исключены"),
]
for i, (value, label) in enumerate(checks):
    x = 0.72 + (i % 2) * 5.95
    y = 3.03 + (i // 2) * 1.18
    rect(s, x, y, 5.66, 0.92, PANEL2, True, GRID)
    text(s, value, x+0.25, y+0.20, 1.18, 0.34, 18, TEXT, True)
    text(s, label, x+1.48, y+0.24, 3.75, 0.30, 11, MUTED)

rect(s, 0.72, 5.65, 11.90, 0.80, PANEL, True, GRID)
text(s, "Ограничение теста", 1.02, 5.88, 1.70, 0.24, 11, ORANGE, True)
text(s, "Проскальзывание и изменение гарантийного обеспечения не моделировались; проценты нормированы к первым 10 контрактам.", 2.77, 5.88, 9.30, 0.30, 11, MUTED)

# 6 — one final result set
s = base(prs, "Единый итог стратегии", "Окончательная версия: Open + импульс <2,5% + время + объём добавления", 6)
cards = [
    (f"{int(filtered['trades'])}", "сделок", BLUE),
    (fmt_pct(filtered["average_return_pct"], 3), "средняя сделка", GREEN),
    (fmt_num(filtered["profit_factor"], 2), "Profit Factor", TEAL),
    (fmt_pct(filtered["win_rate_pct"], 1, False), "выигрышных сделок", TEAL),
    (fmt_pct(filtered["sum_return_pct"], 2), "сумма результатов", GREEN),
    (fmt_pct(filtered["max_drawdown_pct"], 2), "Max DD", ORANGE),
    (fmt_pct(filtered["worst_return_pct"], 2), "худшая сделка", RED),
    (f"{int(filtered['second_legs'])}", "добавлений вторых 10", ORANGE),
]
for i, (value, label, color) in enumerate(cards):
    col = i % 4; row = i // 4
    metric(s, 0.72 + col*2.98, 1.55 + row*1.48, 2.70, value, label, color, 21)
rect(s, 0.72, 4.82, 11.90, 1.18, PANEL2, True, GRID)
text(s, "Денежный эквивалент для 10→20 контрактов", 1.03, 5.10, 3.85, 0.25, 11, MUTED, True)
result_pnl = f"{filtered['net_pnl_rub_10']/1e6:+.2f}".replace(".", ",")
result_dd = f"{filtered['max_drawdown_rub']/1000:.0f}".replace(".", ",")
text(s, f"{result_pnl} млн ₽ / {fmt_pct(filtered['sum_return_pct'], 2)}", 5.08, 5.00, 3.10, 0.36, 20, GREEN, True)
text(s, f"Max DD {result_dd} тыс. ₽ / {fmt_pct(filtered['max_drawdown_pct'], 2)}", 8.55, 5.06, 3.45, 0.30, 14, ORANGE, True)
rect(s, 0.72, 6.28, 11.90, 0.54, TEAL, True, TEAL)
text(s, "Единый backtest: Long10 до 15:00; проверка слабой позиции через 30 минут; затем стоп или Close первой минуты новой сессии.", 1.03, 6.43, 11.05, 0.24, 11.5, BG, True)

# 7 — result path
s = base(prs, "Динамика единого результата", f"Графики раскладывают общий итог {fmt_pct(filtered['sum_return_pct'], 2)}; это не отдельные варианты стратегии", 7)
s.shapes.add_picture(str(equity_path), Inches(0.72), Inches(1.47), width=Inches(7.35), height=Inches(3.15))
s.shapes.add_picture(str(year_path), Inches(8.35), Inches(1.47), width=Inches(4.27), height=Inches(3.15))
metric(s, 0.72, 4.92, 2.72, fmt_pct(filtered["sum_return_pct"], 2), "общая сумма результатов", GREEN, 20)
metric(s, 3.64, 4.92, 2.20, fmt_num(filtered["profit_factor"], 2), "общий Profit Factor", TEAL, 22)
metric(s, 6.04, 4.92, 2.74, fmt_pct(filtered["max_drawdown_pct"], 2), "общая Max DD", ORANGE, 20)
metric(s, 8.98, 4.92, 3.64, f"{int(filtered['trades'])} сделки", "единая полная выборка", BLUE, 21)
text(s, "Годы показывают вклад в общий итог; 2023 и 2026 неполные. Кривая — сумма результатов, не сложный процент на маржу.", 0.76, 6.34, 11.10, 0.25, 10, MUTED)

# 8 — exits and conclusion
s = base(prs, "Три варианта завершения сделки", "Слабость через 30 минут ограничивает ранний убыток; затем работают стоп и выход по первой минуте", 8)
exit_map = {row["exit_reason"]: row for _, row in exits.iterrows()}
weak_key = next(key for key in exit_map if str(key).startswith("weak_"))
exit_cards = [
    ("ПРОВЕРКА 30 МИН", exit_map[weak_key], RED, "слабая позиция закрыта"),
    ("СТОП −1,5%", exit_map["stop"], ORANGE, "аварийный выход"),
    ("CLOSE 1-Й МИНУТЫ", exit_map["first_bar_close"], GREEN, "основной источник прибыли"),
]
for i, (title_value, row, color, note) in enumerate(exit_cards):
    x = 0.72 + i * 3.98
    exit_count = int(row["trades"])
    exit_word = "сделка" if exit_count % 10 == 1 and exit_count % 100 != 11 else ("сделки" if exit_count % 10 in (2, 3, 4) and exit_count % 100 not in (12, 13, 14) else "сделок")
    rect(s, x, 1.52, 3.68, 2.30, PANEL, True, color)
    text(s, title_value, x+0.25, 1.78, 2.08, 0.28, 12, color, True)
    text(s, f"{exit_count} {exit_word}", x+2.18, 1.80, 1.18, 0.25, 11, MUTED, True, PP_ALIGN.RIGHT)
    text(s, fmt_pct(row["average_return_pct"], 3), x+0.25, 2.34, 3.18, 0.38, 23, TEXT, True, PP_ALIGN.CENTER)
    text(s, "средняя сделка", x+0.25, 2.75, 3.18, 0.22, 9.5, MUTED, False, PP_ALIGN.CENTER)
    text(s, f"Σ {fmt_pct(row['sum_return_pct'], 2)} · {note}", x+0.25, 3.27, 3.18, 0.24, 10, MUTED, True, PP_ALIGN.CENTER)

rect(s, 0.72, 4.20, 7.62, 1.72, PANEL2, True, GRID)
text(s, "ИТОГОВОЕ РЕШЕНИЕ", 1.03, 4.48, 2.30, 0.24, 11, TEAL, True)
text(s, "Правила зафиксированы — дальнейшую оптимизацию остановить", 1.03, 4.90, 6.76, 0.32, 18, TEXT, True)
text(s, "Следующий этап — только новые данные, оценка проскальзывания и лимит дневного риска.", 1.03, 5.40, 6.75, 0.34, 10.5, MUTED)

rect(s, 8.60, 4.20, 4.02, 1.72, PANEL, True, GRID)
text(s, f"ПОЧЕМУ WORST = {fmt_pct(filtered['worst_return_pct'], 2)}", 8.92, 4.48, 3.30, 0.24, 11, ORANGE, True)
text(s, "Стоп −1,5% применяется к позиции 20 контрактов, а результат нормирован к первым 10. Поэтому эквивалент может приблизиться к −3%.", 8.92, 4.91, 3.18, 0.70, 11, TEXT, True)
rect(s, 0.72, 6.28, 11.90, 0.54, GREEN, True, GREEN)
text(s, "Формула: 0,7–2,5% · Close > Open → −0,4%: Long10 до 15:00 → +10 по RVol → 30 мин: слабость = выход → стоп / Close 1-й минуты.", 1.02, 6.43, 11.10, 0.24, 11.5, BG, True)

# 9 — time-control selection on one slide
s = base(prs, "Почему вернули контроль через 30 минут", "Все варианты используют одинаковый вход, стоп и выход по Close первой минуты новой сессии", 9)
stages = [
    ("1 · БЕЗ КОНТРОЛЯ", "Только стоп / 1-й Close", "124", "+0,714%", "2,58", "−5,17%", "+88,51%", MUTED),
    ("2 · 19:00", "Слабость на вечернем срезе", "124", "+0,605%", "2,31", "−6,88%", "+74,96%", BLUE),
    ("3 · 1 ЧАС", "Слабость через 60 минут", "124", "+0,681%", "3,29", "−4,68%", "+84,43%", TEAL),
    ("4 · 30 МИНУТ", "Слабость через 30 минут", "124", "+0,736%", "4,03", "−4,00%", "+91,27%", GREEN),
]
for i, (stage, rule, trades_v, avg_v, pf_v, dd_v, sum_v, color) in enumerate(stages):
    x = 0.68 + i * 3.03
    rect(s, x, 1.48, 2.75, 4.25, PANEL, True, color)
    text(s, stage, x+0.24, 1.76, 2.27, 0.28, 12, color, True, PP_ALIGN.CENTER)
    text(s, rule, x+0.24, 2.15, 2.27, 0.54, 11, TEXT, True, PP_ALIGN.CENTER)
    rows = [("Сделок", trades_v), ("Средняя", avg_v), ("PF", pf_v), ("Max DD", dd_v), ("Σ", sum_v)]
    for j, (label, value) in enumerate(rows):
        y = 2.91 + j * 0.49
        text(s, label, x+0.28, y, 0.92, 0.24, 10, MUTED, True)
        text(s, value, x+1.18, y-0.02, 1.25, 0.27, 12, color if j in (1,2,3) else TEXT, True, PP_ALIGN.RIGHT)
    if i < 3:
        text(s, "→", x+2.76, 3.48, 0.27, 0.32, 18, ORANGE, True, PP_ALIGN.CENTER)

rect(s, 0.72, 6.05, 11.90, 0.72, PANEL2, True, GRID)
text(s, "30 минут против отсутствия контроля:", 1.02, 6.27, 2.62, 0.25, 11, MUTED, True)
text(s, "средняя +3%", 3.70, 6.23, 1.72, 0.28, 14, GREEN, True)
text(s, "PF +56%", 5.68, 6.23, 1.35, 0.28, 14, TEAL, True)
text(s, "просадка −23%", 7.35, 6.23, 1.90, 0.28, 14, BLUE, True)
text(s, "сумма +3%", 9.40, 6.23, 1.95, 0.28, 14, GREEN, True)
text(s, "ФИНАЛ", 11.58, 6.23, 0.60, 0.28, 12, GREEN, True, PP_ALIGN.RIGHT)

# 10 — trade frequency
trading_dates = sorted({bar["time"].date() for bar in price_bars})
trade_dates = sorted(set(final_trades["signal_time"].dt.date))
trading_day_pos = {day: i for i, day in enumerate(trading_dates)}
trade_day_gaps = [trading_day_pos[b] - trading_day_pos[a] for a, b in zip(trade_dates, trade_dates[1:])]
calendar = pd.DataFrame({"date": pd.to_datetime(trading_dates)})
calendar["week"] = calendar["date"].dt.strftime("%G-%V")
trade_weeks = final_trades["signal_time"].dt.strftime("%G-%V").value_counts()
all_weeks = calendar["week"].drop_duplicates()
weekly_counts = all_weeks.map(trade_weeks).fillna(0).astype(int)
weekly_distribution = weekly_counts.value_counts().sort_index()
trades_per_day = len(final_trades) / len(trading_dates)
trades_per_week = len(final_trades) / len(all_weeks)
calendar_months = calendar["date"].dt.to_period("M").nunique()
trades_per_month = len(final_trades) / calendar_months
active_day_pct = 100 * len(trade_dates) / len(trading_dates)

s = base(prs, "Как часто возникают сделки", "В среднем — около одной сделки в неделю; фактический поток неравномерный", 10)
frequency_cards = [
    (fmt_num(trades_per_week, 2), "сделки в неделю", TEAL),
    (fmt_num(trades_per_month, 2), "сделки в месяц", GREEN),
    (f"1 / {fmt_num(1 / trades_per_day, 1)}", "торговых дней", BLUE),
    (fmt_pct(active_day_pct, 1, False), "дней со сделкой", ORANGE),
]
for i, (value, label, color) in enumerate(frequency_cards):
    metric(s, 0.72 + i*2.98, 1.50, 2.70, value, label, color, 21)

rect(s, 0.72, 2.88, 7.62, 2.88, PANEL, True, GRID)
text(s, "РАСПРЕДЕЛЕНИЕ НЕДЕЛЬ", 1.02, 3.16, 2.65, 0.25, 11, MUTED, True)
max_share = max(weekly_distribution / len(all_weeks) * 100)
for i, count in enumerate(range(5)):
    share = 100 * int(weekly_distribution.get(count, 0)) / len(all_weeks)
    y = 3.62 + i*0.39
    deal_word = "сделок" if count == 0 else ("сделка" if count == 1 else "сделки")
    text(s, f"{count} {deal_word}", 1.02, y, 1.05, 0.22, 10, TEXT, True)
    rect(s, 2.17, y+0.01, 4.35 * share/max_share, 0.18, TEAL if count else GRID, False)
    text(s, fmt_pct(share, 1, False), 6.72, y-0.01, 0.82, 0.23, 10, MUTED, True, PP_ALIGN.RIGHT)

rect(s, 8.61, 2.88, 4.01, 2.88, PANEL2, True, GRID)
text(s, "ИНТЕРВАЛ МЕЖДУ СДЕЛКАМИ", 8.94, 3.16, 3.25, 0.25, 11, BLUE, True)
text(s, f"{pd.Series(trade_day_gaps).median():.0f}", 8.94, 3.65, 1.15, 0.42, 25, TEXT, True)
text(s, "торговых дня — медиана", 10.10, 3.73, 2.10, 0.25, 10.5, MUTED)
text(s, fmt_num(pd.Series(trade_day_gaps).mean(), 1), 8.94, 4.33, 1.15, 0.36, 21, TEXT, True)
text(s, "торгового дня — среднее", 10.10, 4.39, 2.10, 0.25, 10.5, MUTED)
text(s, f"{max(trade_day_gaps)}", 8.94, 4.97, 1.15, 0.36, 21, ORANGE, True)
text(s, "торговых дней — максимум", 10.10, 5.03, 2.10, 0.25, 10.5, MUTED)
rect(s, 0.72, 6.10, 11.90, 0.62, TEAL, True, TEAL)
frequency_gap = f"{1/trades_per_day:.1f}".replace(".", ",")
text(s, f"{len(final_trades)} сделки за {len(trading_dates)} торговых дней и {len(all_weeks)} недель: базовое ожидание — одна сделка примерно раз в {frequency_gap} торгового дня.", 1.03, 6.28, 11.05, 0.25, 12, BG, True)

# 11 — representative profitable trade
s = base(prs, "Типичная прибыльная сделка", "Сделка, ближайшая к медианной прибыли всех положительных результатов", 11)
s.shapes.add_picture(str(profit_path), Inches(0.72), Inches(1.46), width=Inches(8.12), height=Inches(4.82))
rect(s, 9.08, 1.46, 3.54, 4.82, PANEL, True, GRID)
text(s, typical_profit["signal_time"].strftime("%d.%m.%Y"), 9.40, 1.76, 2.90, 0.32, 18, TEXT, True)
text(s, f"Импульс  {fmt_pct(typical_profit['impulse_pct'], 3)}", 9.40, 2.28, 2.90, 0.26, 11, GREEN, True)
text(s, f"RVol  {fmt_num(typical_profit['impulse_rvol'], 2)}×", 9.40, 2.65, 2.90, 0.26, 11, ORANGE, True)
text(s, f"Первый вход  {typical_profit['leg_04_time'].strftime('%H:%M')} · 10", 9.40, 3.13, 2.90, 0.28, 12, BLUE, True)
add_note = "Добавление +10" if bool(typical_profit["second_leg"]) else "Добавление запрещено объёмом"
text(s, add_note, 9.40, 3.53, 2.90, 0.42, 11, ORANGE, True)
text(s, f"Выход  {typical_profit['exit_time'].strftime('%d.%m %H:%M')}", 9.40, 4.10, 2.90, 0.28, 12, TEXT, True)
text(s, "Close первой минуты новой сессии", 9.40, 4.43, 2.90, 0.38, 10, MUTED)
text(s, fmt_pct(typical_profit["return_pct"], 3), 9.40, 4.95, 2.90, 0.46, 26, GREEN, True)
text(s, f"{typical_profit['net_pnl_rub_10']/1000:+.1f} тыс. ₽", 9.40, 5.47, 2.90, 0.28, 13, TEXT, True)
rect(s, 0.72, 6.42, 11.90, 0.40, GREEN, True, GREEN)
text(s, "Типовой выигрыш: позиция пережила внутридневной шум и закрылась по Close первой минуты новой сессии.", 1.03, 6.52, 11.05, 0.22, 10.5, BG, True)

# 12 — representative losing trade
s = base(prs, "Типичная убыточная сделка", "Сделка, ближайшая к медианному убытку всех отрицательных результатов", 12)
s.shapes.add_picture(str(loss_path), Inches(0.72), Inches(1.46), width=Inches(8.12), height=Inches(4.82))
rect(s, 9.08, 1.46, 3.54, 4.82, PANEL, True, GRID)
text(s, typical_loss["signal_time"].strftime("%d.%m.%Y"), 9.40, 1.76, 2.90, 0.32, 18, TEXT, True)
text(s, f"Импульс  {fmt_pct(typical_loss['impulse_pct'], 3)}", 9.40, 2.28, 2.90, 0.26, 11, GREEN, True)
text(s, f"RVol  {fmt_num(typical_loss['impulse_rvol'], 2)}×", 9.40, 2.65, 2.90, 0.26, 11, TEAL, True)
text(s, f"Первый вход  {typical_loss['leg_04_time'].strftime('%H:%M')} · 10", 9.40, 3.13, 2.90, 0.28, 12, BLUE, True)
loss_add_note = "Добавление +10 исполнено" if bool(typical_loss["second_leg"]) else "Добавления +10 не было"
text(s, loss_add_note, 9.40, 3.53, 2.90, 0.42, 11, ORANGE, True)
loss_is_stop = str(typical_loss["exit_reason"]).startswith("stop")
loss_is_weak = str(typical_loss["exit_reason"]).startswith("weak_")
loss_exit_label = "Стоп" if loss_is_stop else ("Проверка 30 минут" if loss_is_weak else "Close 1-й минуты")
text(s, f"{loss_exit_label}  {typical_loss['exit_time'].strftime('%d.%m %H:%M')}", 9.40, 4.10, 2.90, 0.28, 12, TEXT, True)
loss_rule_note = "Close ниже средней позиции" if loss_is_weak else "стоп активен до фактического выхода"
text(s, loss_rule_note, 9.40, 4.43, 2.90, 0.42, 10, MUTED)
text(s, fmt_pct(typical_loss["return_pct"], 3), 9.40, 4.95, 2.90, 0.46, 26, RED, True)
text(s, f"{typical_loss['net_pnl_rub_10']/1000:+.1f} тыс. ₽", 9.40, 5.47, 2.90, 0.28, 13, TEXT, True)
rect(s, 0.72, 6.42, 11.90, 0.40, RED, True, RED)
loss_takeaway = (
    "Типовой убыток: через 30 минут позиция оставалась слабой и была закрыта без переноса риска дальше."
    if loss_is_stop else (
        "Типовой убыток: через 30 минут цена оставалась ниже средней — слабая позиция была закрыта досрочно."
        if loss_is_weak else
        "Типовой убыток: стоп не сработал, выход исполнен по Close первой минуты новой сессии."
    )
)
text(s, loss_takeaway, 1.03, 6.52, 11.05, 0.22, 10.5, BG, True)

# 13 — consecutive winning and losing trades
ordered = final_trades.sort_values(["signal_time", "movement_id"]).reset_index(drop=True).copy()
ordered["run_sign"] = ordered["net_pnl_rub_10"].map(lambda value: 1 if value > 0 else (-1 if value < 0 else 0))
ordered["run_id"] = ordered["run_sign"].ne(ordered["run_sign"].shift()).cumsum()
run_rows = []
for _, group in ordered.groupby("run_id", sort=True):
    run_rows.append({
        "sign": int(group["run_sign"].iloc[0]),
        "count": len(group),
        "start": group["signal_time"].iloc[0],
        "end": group["signal_time"].iloc[-1],
        "sum_pct": group["return_pct"].sum(),
    })
runs = pd.DataFrame(run_rows)
loss_runs = runs[runs["sign"] < 0].copy()
win_runs = runs[runs["sign"] > 0].copy()
max_loss = loss_runs.sort_values(["count", "start"], ascending=[False, True]).iloc[0]
max_wins = win_runs[win_runs["count"] == win_runs["count"].max()].sort_values("start")
max_win = max_wins.iloc[0]

s = base(prs, "Серии прибыльных и убыточных сделок", "Последовательность считается по хронологии сигналов, а не по календарным дням", 13)
rect(s, 0.72, 1.48, 5.72, 2.34, PANEL, True, RED)
text(s, "МАКСИМУМ УБЫТКОВ ПОДРЯД", 1.04, 1.77, 3.55, 0.28, 12, RED, True)
text(s, f"{int(max_loss['count'])}", 1.04, 2.18, 1.12, 0.62, 35, TEXT, True)
text(s, "сделок", 2.03, 2.44, 1.12, 0.28, 12, MUTED, True)
text(s, f"{max_loss['start'].strftime('%d.%m.%Y')} — {max_loss['end'].strftime('%d.%m.%Y')}", 3.24, 2.22, 2.70, 0.30, 14, TEXT, True, PP_ALIGN.RIGHT)
text(s, f"результат серии  {fmt_pct(max_loss['sum_pct'], 2)}", 3.24, 2.72, 2.70, 0.30, 13, RED, True, PP_ALIGN.RIGHT)
text(s, "30-минутная проверка ограничила глубину серии: восемь минусов дали не −8 стопов, а суммарно только −2,68%.", 1.04, 3.18, 4.98, 0.43, 10.5, MUTED)

rect(s, 6.72, 1.48, 5.90, 2.34, PANEL, True, GREEN)
text(s, "МАКСИМУМ ПРИБЫЛЕЙ ПОДРЯД", 7.04, 1.77, 3.70, 0.28, 12, GREEN, True)
text(s, f"{int(max_win['count'])}", 7.04, 2.18, 1.12, 0.62, 35, TEXT, True)
text(s, "сделок", 8.03, 2.44, 1.12, 0.28, 12, MUTED, True)
text(s, f"{max_win['start'].strftime('%d.%m.%Y')} — {max_win['end'].strftime('%d.%m.%Y')}", 9.25, 2.22, 2.95, 0.30, 14, TEXT, True, PP_ALIGN.RIGHT)
text(s, f"результат серии  {fmt_pct(max_win['sum_pct'], 2)}", 9.25, 2.72, 2.95, 0.30, 13, GREEN, True, PP_ALIGN.RIGHT)
if len(max_wins) > 1:
    second_win = max_wins.iloc[1]
    second_note = f"Вторая серия из {int(second_win['count'])}: {second_win['start'].strftime('%d.%m.%Y')} — {second_win['end'].strftime('%d.%m.%Y')}, результат {fmt_pct(second_win['sum_pct'], 2)}."
else:
    second_note = "Максимальная прибыльная серия встретилась один раз."
text(s, second_note, 7.04, 3.18, 5.04, 0.43, 10.5, MUTED)

rect(s, 0.72, 4.12, 7.62, 1.78, PANEL2, True, GRID)
text(s, "СТРУКТУРА СЕРИЙ", 1.03, 4.39, 2.00, 0.25, 11, TEAL, True)
loss_dist = loss_runs["count"].value_counts().sort_index()
win_dist = win_runs["count"].value_counts().sort_index()
loss_line = " · ".join(f"{int(length)}×: {int(number)}" for length, number in loss_dist.items())
win_line = " · ".join(f"{int(length)}×: {int(number)}" for length, number in win_dist.items())
text(s, "Убыточные серии", 1.03, 4.84, 1.58, 0.25, 10.5, RED, True)
text(s, loss_line, 2.64, 4.84, 5.30, 0.25, 10.5, TEXT)
text(s, "Прибыльные серии", 1.03, 5.28, 1.58, 0.25, 10.5, GREEN, True)
text(s, win_line, 2.64, 5.28, 5.30, 0.25, 10.5, TEXT)

rect(s, 8.62, 4.12, 4.00, 1.78, PANEL, True, GRID)
text(s, "ВСЯ ВЫБОРКА", 8.94, 4.39, 2.10, 0.25, 11, BLUE, True)
text(s, f"{int((ordered['run_sign'] < 0).sum())}", 8.94, 4.82, 0.90, 0.40, 22, RED, True)
text(s, "убыточных сделок", 9.83, 4.91, 2.05, 0.25, 10.5, MUTED)
text(s, f"{int((ordered['run_sign'] > 0).sum())}", 8.94, 5.29, 0.90, 0.40, 22, GREEN, True)
text(s, "прибыльных сделок", 9.83, 5.38, 2.05, 0.25, 10.5, MUTED)

rect(s, 0.72, 6.25, 11.90, 0.57, ORANGE, True, ORANGE)
text(s, "Практический вывод: психологически и по капиталу нужно быть готовым минимум к 8 последовательным убыточным сделкам.", 1.03, 6.42, 11.05, 0.25, 11.5, BG, True)

# 14 — new upper impulse boundary, now part of the base system
s = base(prs, "Новое базовое правило: импульс <2,5%", "Слишком большой первый импульс рассматривается как кульминация, после которой продолжение не подтверждается", 14)
rect(s, 0.72, 1.48, 4.00, 3.90, PANEL, True, RED)
text(s, "КУЛЬМИНАЦИОННЫЙ ХВОСТ", 1.04, 1.78, 3.34, 0.28, 12, RED, True, PP_ALIGN.CENTER)
text(s, "≥ 2,5%", 1.04, 2.24, 3.34, 0.48, 28, TEXT, True, PP_ALIGN.CENTER)
metric(s, 1.04, 2.98, 1.45, "8", "сделок", BLUE, 21)
metric(s, 2.80, 2.98, 1.45, "12,5%", "выигрышных", ORANGE, 18)
metric(s, 1.04, 4.16, 1.45, "−0,116%", "средняя", RED, 17)
metric(s, 2.80, 4.16, 1.45, "0,34", "PF", RED, 20)

rect(s, 4.98, 1.48, 3.48, 3.90, PANEL, True, MUTED)
text(s, "БЕЗ ГРАНИЦЫ", 5.28, 1.78, 2.88, 0.28, 12, MUTED, True, PP_ALIGN.CENTER)
metric(s, 5.28, 2.32, 2.88, "+90,34%", "сумма результатов", GREEN, 21)
metric(s, 5.28, 3.40, 1.30, "+0,684%", "средняя", TEXT, 17)
metric(s, 6.82, 3.40, 1.30, "3,84", "PF", TEAL, 20)
metric(s, 5.28, 4.54, 2.88, "132", "сделки", BLUE, 21)

rect(s, 8.72, 1.48, 3.90, 3.90, PANEL, True, GREEN)
text(s, "С ГРАНИЦЕЙ <2,5%", 9.03, 1.78, 3.28, 0.28, 12, GREEN, True, PP_ALIGN.CENTER)
metric(s, 9.03, 2.32, 3.28, "+91,27%", "сумма результатов", GREEN, 21)
metric(s, 9.03, 3.40, 1.45, "+0,736%", "средняя", TEXT, 17)
metric(s, 10.72, 3.40, 1.45, "4,03", "PF", TEAL, 20)
metric(s, 9.03, 4.54, 3.28, "124", "сделки", BLUE, 21)

rect(s, 0.72, 5.72, 11.90, 1.10, PANEL2, True, GRID)
text(s, "ПРАВИЛО", 1.03, 5.97, 1.05, 0.25, 11, ORANGE, True)
text(s, "0,7% < первый импульс < 2,5%", 2.18, 5.91, 3.60, 0.34, 18, TEXT, True)
text(s, "Хвост ≥2,5% отрицателен на полной, train и OOS-выборках. Наблюдений всего 8, поэтому правило простое, но его нужно продолжать контролировать на новых данных.", 5.86, 5.87, 6.22, 0.62, 10.5, MUTED)

# 15 — informational DMI improvement, deliberately not part of the base system
s = base(prs, "Как можно улучшить стратегию", "Информационный вариант контроля сильного дневного даунтренда — в базовую систему не включён", 15)

rect(s, 0.72, 1.48, 3.66, 3.88, PANEL, True, GREEN)
text(s, "БАЗОВАЯ СИСТЕМА", 1.02, 1.77, 2.90, 0.28, 12, GREEN, True, PP_ALIGN.CENTER)
text(s, "БЕЗ DMI-ФИЛЬТРА", 1.02, 2.16, 2.90, 0.34, 17, TEXT, True, PP_ALIGN.CENTER)
metric(s, 1.02, 2.72, 1.42, "+91,27%", "сумма", GREEN, 18)
metric(s, 2.63, 2.72, 1.42, "4,03", "PF", TEAL, 20)
metric(s, 1.02, 4.00, 1.42, "−4,00%", "Max DD", ORANGE, 18)
metric(s, 2.63, 4.00, 1.42, "124", "сделки", BLUE, 20)

rect(s, 4.62, 1.48, 3.66, 3.88, PANEL, True, BLUE)
text(s, "ВАРИАНТ 1", 4.92, 1.77, 2.90, 0.28, 12, BLUE, True, PP_ALIGN.CENTER)
text(s, "ПРОПУСКАТЬ LONG", 4.92, 2.16, 2.90, 0.34, 17, TEXT, True, PP_ALIGN.CENTER)
text(s, "если −DI(14) − +DI(14) > 27,5", 4.92, 2.55, 2.90, 0.32, 10.5, MUTED, True, PP_ALIGN.CENTER)
metric(s, 4.92, 3.06, 1.42, "+85,94%", "сумма", GREEN, 18)
metric(s, 6.53, 3.06, 1.42, "4,63", "PF", TEAL, 20)
metric(s, 4.92, 4.30, 1.42, "6", "убытков подряд", ORANGE, 20)
metric(s, 6.53, 4.30, 1.42, "104", "сделок", BLUE, 20)

rect(s, 8.52, 1.48, 4.10, 3.88, PANEL, True, ORANGE)
text(s, "ВАРИАНТ 2", 8.84, 1.77, 3.45, 0.28, 12, ORANGE, True, PP_ALIGN.CENTER)
text(s, "ПЕРВЫЙ ВХОД 5", 8.84, 2.16, 3.45, 0.34, 17, TEXT, True, PP_ALIGN.CENTER)
text(s, "при DMI > 27,5; добавление +10 оставить", 8.84, 2.55, 3.45, 0.32, 10.5, MUTED, True, PP_ALIGN.CENTER)
metric(s, 8.84, 3.06, 1.52, "+89,04%", "сумма", GREEN, 18)
metric(s, 10.69, 3.06, 1.52, "4,31", "PF", TEAL, 20)
metric(s, 8.84, 4.30, 1.52, "−2,87%", "OOS DD", BLUE, 18)
metric(s, 10.69, 4.30, 1.52, "8", "убытков подряд", ORANGE, 20)

rect(s, 0.72, 5.68, 11.90, 1.14, PANEL2, True, GRID)
text(s, "РЕШЕНИЕ", 1.03, 5.93, 1.10, 0.25, 11, TEAL, True)
text(s, "Не внедрять в базовую систему", 2.18, 5.88, 3.52, 0.34, 18, TEXT, True)
text(s, "Выгода по PF и локальному риску небольшая относительно дополнительного индикатора, чувствительного порога и потери части прибыли. DMI оставить для наблюдения и повторной проверки на новых данных.", 5.62, 5.84, 6.55, 0.62, 10.5, MUTED)

prs.save(OUT)
print("saved", OUT.resolve(), "slides", len(prs.slides))
