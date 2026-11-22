from collections import defaultdict
from datetime import datetime
from itertools import accumulate
from pathlib import Path
from statistics import fmean, median

from openpyxl import load_workbook
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


SOURCE = Path("MIX_ST_19_4_volume_filter_test.xlsx")
OUT = Path("MIX_ST_19_4_volume_tiered_strategy_10_contracts.pptx")
RULE = "tiered_impulse_volume_bottom60_add_bottom80_trade"
BASELINE = "baseline_all_adds"
SCALE = 0.1  # source results use 100 initial contracts; presentation uses 10

W, H = 13.333, 7.5
BG = "07141E"
PANEL = "0D2230"
PANEL_2 = "102A39"
GRID = "24404E"
TEXT = "F4F8FA"
MUTED = "9EB2BE"
TEAL = "20D3C2"
GREEN = "89E36B"
ORANGE = "FFB45B"
RED = "FF6B6B"
BLUE = "65A7FF"


def rgb(value):
    return RGBColor.from_string(value)


def add_rect(slide, x, y, w, h, fill, radius=True, line=None):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h),
    )
    shape.fill.solid(); shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line or fill)
    if radius:
        try:
            shape.adjustments[0] = 0.08
        except Exception:
            pass
    return shape


def add_text(slide, text, x, y, w, h, size=18, color=TEXT, bold=False,
             align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP, italic=False):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame; tf.clear(); tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.02)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]; p.alignment = align; p.space_after = Pt(0); p.line_spacing = 1.0
    run = p.add_run(); run.text = text
    run.font.name = "Aptos"; run.font.size = Pt(size); run.font.bold = bold; run.font.italic = italic
    run.font.color.rgb = rgb(color)
    return box


def add_line(slide, x1, y1, x2, y2, color=GRID, width=1.4, dash=None):
    shape = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2)
    )
    shape.line.color.rgb = rgb(color); shape.line.width = Pt(width)
    if dash:
        shape.line.dash_style = dash
    return shape


def add_circle(slide, cx, cy, diameter, fill, label=None, size=11):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.OVAL, Inches(cx-diameter/2), Inches(cy-diameter/2), Inches(diameter), Inches(diameter)
    )
    shape.fill.solid(); shape.fill.fore_color.rgb = rgb(fill); shape.line.color.rgb = rgb(fill)
    if label is not None:
        add_text(slide, str(label), cx-diameter/2, cy-diameter/2, diameter, diameter,
                 size, BG, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    return shape


def base_slide(prs, title, subtitle, number):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = rgb(BG)
    add_rect(slide, 0, 0, 0.12, H, TEAL, False)
    add_text(slide, title, 0.68, 0.28, 10.75, 0.50, 25, TEXT, True)
    add_text(slide, "MIX · ST 19/4", 11.0, 0.34, 1.62, 0.24, 10, MUTED, True, PP_ALIGN.RIGHT)
    add_line(slide, 0.68, 0.94, 12.62, 0.94, GRID, 1)
    add_text(slide, subtitle, 0.70, 1.03, 11.9, 0.35, 12, MUTED)
    add_text(slide, "21.08.2023–21.08.2026 · минутные данные · 10→20 MIX", 0.70, 7.17, 8.2, 0.18, 8, MUTED)
    add_text(slide, f"{number:02d}", 12.12, 7.13, 0.48, 0.22, 9, MUTED, True, PP_ALIGN.RIGHT)
    return slide


def metric_card(slide, x, y, w, value, label, color=TEAL, value_size=22):
    add_rect(slide, x, y, w, 1.04, PANEL, True, GRID)
    add_rect(slide, x, y, 0.06, 1.04, color, False)
    add_text(slide, value, x+0.22, y+0.14, w-0.34, 0.40, value_size, TEXT, True)
    add_text(slide, label, x+0.22, y+0.62, w-0.34, 0.22, 9.5, MUTED)


def read_table(workbook, sheet):
    rows = workbook[sheet].iter_rows(values_only=True)
    headers = next(rows)
    return [{name: row[i] for i, name in enumerate(headers)} for row in rows]


wb = load_workbook(SOURCE, read_only=True, data_only=True)
summaries = {row["rule"]: row for row in read_table(wb, "Summary")}
summary = summaries[RULE]
baseline = summaries[BASELINE]
details = [row for row in read_table(wb, "Details") if row["rule"] == RULE]
baseline_details = [row for row in read_table(wb, "Details") if row["rule"] == BASELINE]
standard = [row for row in read_table(wb, "Impulse_Standard_Summary") if row["segment"] == "all"]
standard_by_session = {row["session"]: row for row in standard}
thresholds = [row for row in read_table(wb, "Train_Thresholds") if row["feature"] == "impulse_rvol"]
threshold_by_pct = {int(row["train_percentile"]): row["threshold"] for row in thresholds}
volume_buckets = [row for row in read_table(wb, "Volume_Buckets") if row["feature"] == "impulse_rvol"]

returns = [float(row["return_initial_100_pct"]) for row in details]
pnls_10 = [float(row["net_pnl_rub"]) * SCALE for row in details]
wins_pct = [value for value in returns if value > 0]
losses_pct = [value for value in returns if value < 0]
split_time = datetime(2025, 10, 15, 13, 26)

def dd(values):
    peak = 0.0; worst = 0.0
    for value in accumulate(values):
        peak = max(peak, value); worst = min(worst, value-peak)
    return worst


def segment_stats(rows):
    ret = [float(r["return_initial_100_pct"]) for r in rows]
    pnl = [float(r["net_pnl_rub"]) * SCALE for r in rows]
    wins = sum(x for x in pnl if x > 0); losses = abs(sum(x for x in pnl if x < 0))
    return {
        "trades": len(rows), "sum_pct": sum(ret), "avg_pct": fmean(ret),
        "pf": wins / losses if losses else None, "net_10": sum(pnl), "dd_pct": dd(ret),
    }


train_details = [row for row in details if row["signal_time"] <= split_time]
test_details = [row for row in details if row["signal_time"] > split_time]
train_stats = segment_stats(train_details)
test_stats = segment_stats(test_details)
base_train = [row for row in baseline_details if row["signal_time"] <= split_time]
base_test = [row for row in baseline_details if row["signal_time"] > split_time]
base_test_stats = segment_stats(base_test)

year_rows = []
grouped = defaultdict(list)
for row in details:
    grouped[row["signal_time"].year].append(row)
for year in sorted(grouped):
    stats = segment_stats(grouped[year])
    year_rows.append((year, stats))

prs = Presentation(); prs.slide_width = Inches(W); prs.slide_height = Inches(H)
prs.core_properties.title = "MIX ST 19/4 · объёмная ступенчатая стратегия · 10 контрактов"
prs.core_properties.subject = "Финальная стратегия с фильтром объёма импульса и контролем риска"
prs.core_properties.author = "OpenAI Codex"
prs.core_properties.comments = "Проценты нормированы к номиналу первых 10 контрактов; это не доходность на маржу."

# 1 — cover
s = prs.slides.add_slide(prs.slide_layouts[6])
s.background.fill.solid(); s.background.fill.fore_color.rgb = rgb(BG)
add_rect(s, 0, 0, 0.16, H, TEAL, False)
add_text(s, "ФИНАЛЬНАЯ СТРАТЕГИЯ", 0.78, 0.70, 5.4, 0.28, 12, TEAL, True)
add_text(s, "MIX", 0.76, 1.14, 2.8, 0.84, 49, TEXT, True)
add_text(s, "SuperTrend 19 / 4 · объёмный фильтр", 0.80, 2.02, 6.3, 0.44, 23, MUTED)
add_text(s, "10 контрактов → максимум 20", 0.80, 2.73, 6.25, 0.46, 27, TEXT, True)
add_text(s, "Экстремальный объём — сделки нет", 0.82, 3.32, 5.9, 0.34, 17, ORANGE, True)

add_rect(s, 7.40, 0.66, 5.18, 4.92, PANEL, True, GRID)
add_text(s, "ОБЪЁМ УПРАВЛЯЕТ РАЗМЕРОМ", 7.80, 1.02, 4.25, 0.25, 11, MUTED, True)
tiers = [
    ("НИЖНИЕ 60%", "10 + 10", "умеренный объём", GREEN),
    ("60–80%", "только 10", "повышенный объём", TEAL),
    ("ВЕРХНИЕ 20%", "0", "возможная кульминация", RED),
]
for i, (band, qty, note, color) in enumerate(tiers):
    y = 1.57 + i * 1.12
    add_rect(s, 7.78, y, 4.35, 0.88, PANEL_2, True, color)
    add_text(s, band, 8.03, y+0.15, 1.42, 0.24, 11, color, True)
    add_text(s, qty, 9.45, y+0.10, 1.12, 0.30, 18, TEXT, True, PP_ALIGN.CENTER)
    add_text(s, note, 10.55, y+0.18, 1.34, 0.24, 9, MUTED, False, PP_ALIGN.RIGHT)

metric_card(s, 0.80, 5.20, 2.28, "+93,78%", "Σ результатов сделок", GREEN, 22)
metric_card(s, 3.24, 5.20, 1.82, "3,01", "Profit Factor", TEAL, 23)
metric_card(s, 5.22, 5.20, 1.82, "−5,53%", "max DD", ORANGE, 21)
add_text(s, f"10 контрактов · net ≈ {sum(pnls_10)/1_000_000:.2f} млн ₽ · комиссия включена", 0.82, 6.64, 6.6, 0.24, 10, MUTED)
add_text(s, "01", 12.10, 7.10, 0.45, 0.22, 9, MUTED, True, PP_ALIGN.RIGHT)

# 2 — exact algorithm
s = base_slide(prs, "Алгоритм сделки", "Все решения формальны: цена, закрытие минуты, объёмный процентиль и время", 2)
steps = [
    ("1", "ИМПУЛЬС", "Первый зелёный ST дня\nстрого >0,7%", GREEN),
    ("2", "ОБЪЁМ", "Верхние 20% —\nсделки нет", ORANGE),
    ("3", "ВХОД 10", "Красный ST; −0,4%\nот вершины", TEAL),
    ("4", "ДОБАВИТЬ 10", "Close > max; след. Open\nтолько в нижних 60%", BLUE),
    ("5", "РИСК / ВЫХОД", "30 мин.; стоп 2%;\nиначе след. открытие", TEXT),
]
for i, (n, title, body, color) in enumerate(steps):
    x = 0.72 + i * 2.45
    add_rect(s, x, 1.54, 2.16, 2.20, PANEL, True, GRID)
    add_circle(s, x+0.34, 1.90, 0.38, color, n, 11)
    add_text(s, title, x+0.62, 1.76, 1.38, 0.38, 10.5, color, True)
    add_text(s, body, x+0.24, 2.42, 1.72, 0.68, 12.5, TEXT, True)
    if i < 4:
        add_text(s, "→", x+2.18, 2.42, 0.27, 0.30, 17, MUTED, True, PP_ALIGN.CENTER)
add_rect(s, 0.72, 4.10, 11.90, 1.58, PANEL_2, True, GRID)
add_text(s, "КОНТРОЛЬ ЧЕРЕЗ 30 МИНУТ", 1.02, 4.38, 3.25, 0.25, 11, BLUE, True)
add_text(s, "Close ≤ средней цены позиции", 1.02, 4.88, 3.25, 0.30, 16, RED, True)
add_text(s, "закрыть всё", 4.12, 4.88, 1.52, 0.30, 16, RED, True)
add_text(s, "Close > средней цены", 6.08, 4.88, 2.52, 0.30, 16, GREEN, True)
add_text(s, "держать по плану", 8.61, 4.88, 2.35, 0.30, 16, GREEN, True)
add_text(s, "Аварийный стоп: 2% ниже текущей средневзвешенной цены; после добавления уровень пересчитывается.", 1.02, 5.40, 10.85, 0.25, 10.5, MUTED)
add_rect(s, 0.72, 6.10, 11.90, 0.60, ORANGE, True, ORANGE)
add_text(s, "Важно: вторые 10 покупаются по Open следующей минуты после Close > max; касания High недостаточно.", 1.02, 6.27, 11.10, 0.25, 12, BG, True)

# 3 — volume standard
s = base_slide(prs, "Что считается обычным объёмом", "Объём сравнивается с той же минутой за предыдущие 60 наблюдений — время суток уже учтено", 3)
all_std = standard_by_session["ALL"]
metric_card(s, 0.72, 1.50, 2.75, "2,69×", "медианный импульс >0,7%", TEAL, 23)
metric_card(s, 3.65, 1.50, 2.75, "3,23×", "граница 60-го процентиля", GREEN, 23)
metric_card(s, 6.58, 1.50, 2.75, "5,15×", "граница верхних 20%", ORANGE, 23)
metric_card(s, 9.51, 1.50, 3.11, "7,59×", "верхние 10% · экстремум", RED, 23)

add_rect(s, 0.72, 2.88, 7.42, 3.30, PANEL, True, GRID)
add_text(s, "РАСПРЕДЕЛЕНИЕ 327 ИМПУЛЬСОВ", 1.02, 3.17, 3.30, 0.25, 11, MUTED, True)
levels = [(0.0, "0"), (1.0, "1×"), (all_std["median_p50"], "P50"), (all_std["p60"], "P60"), (all_std["p80"], "P80"), (all_std["p90"], "P90")]
x0, y0, width = 1.16, 4.45, 6.35
add_line(s, x0, y0, x0+width, y0, GRID, 5)
max_level = all_std["p90"]
for value, label in levels[1:]:
    x = x0 + min(value/max_level, 1) * width
    color = GREEN if label in ("P50", "P60") else (ORANGE if label == "P80" else RED)
    add_circle(s, x, y0, 0.18, color)
    add_text(s, label, x-0.32, y0-0.58, 0.64, 0.22, 9, color, True, PP_ALIGN.CENTER)
    add_text(s, f"{value:.2f}×", x-0.42, y0+0.25, 0.84, 0.22, 9, TEXT, True, PP_ALIGN.CENTER)
add_text(s, "обычный / умеренный", 1.10, 5.42, 2.40, 0.24, 11, GREEN, True)
add_text(s, "повышенный", 4.54, 5.42, 1.55, 0.24, 11, ORANGE, True)
add_text(s, "кульминационный", 6.36, 5.42, 1.45, 0.24, 11, RED, True)

add_rect(s, 8.40, 2.88, 4.22, 3.30, PANEL_2, True, GRID)
add_text(s, "АБСОЛЮТНЫЕ ОРИЕНТИРЫ", 8.75, 3.17, 3.20, 0.25, 11, MUTED, True)
absolute = [("110", "минут · медианная длительность"), ("30 466", "контрактов · медианный объём"), ("278", "контрактов в минуту · медиана")]
for i, (value, label) in enumerate(absolute):
    y = 3.72 + i * 0.70
    add_text(s, value, 8.75, y, 1.28, 0.34, 20, TEXT, True)
    add_text(s, label, 10.02, y+0.06, 1.90, 0.28, 9.5, MUTED)
add_text(s, "Для решения используется относительный объём, а не сырая сумма контрактов.", 8.75, 5.86, 3.10, 0.24, 9, ORANGE, True)

# 4 — why not high volume
s = base_slide(prs, "Почему экстремальный объём исключён", "Проверка показала обратную зависимость: самые объёмные импульсы не были самыми качественными", 4)
add_rect(s, 0.72, 1.52, 8.05, 4.78, PANEL, True, GRID)
add_text(s, "PROFIT FACTOR ПО КВИНТИЛЯМ ОБЪЁМА ИМПУЛЬСА", 1.02, 1.83, 5.20, 0.25, 11, MUTED, True)
max_pf = max(float(row["profit_factor"]) for row in volume_buckets)
for i, row in enumerate(volume_buckets):
    x = 1.12 + i * 1.43
    pf = float(row["profit_factor"])
    bar_h = 2.55 * pf / max_pf
    color = GREEN if i < 2 else (ORANGE if i < 4 else RED)
    add_rect(s, x, 5.55-bar_h, 0.90, bar_h, color, True, color)
    add_text(s, f"{pf:.2f}", x-0.04, 5.22-bar_h, 0.98, 0.26, 11, color, True, PP_ALIGN.CENTER)
    add_text(s, f"Q{i+1}", x+0.12, 5.72, 0.66, 0.22, 10, TEXT, True, PP_ALIGN.CENTER)
add_text(s, "низкий", 1.04, 6.05, 1.20, 0.22, 9, GREEN, True)
add_text(s, "→ объём импульса →", 3.64, 6.05, 2.50, 0.22, 9, MUTED, True, PP_ALIGN.CENTER)
add_text(s, "экстремальный", 7.15, 6.05, 1.36, 0.22, 9, RED, True, PP_ALIGN.RIGHT)

add_rect(s, 9.04, 1.52, 3.58, 4.78, PANEL_2, True, GRID)
add_text(s, "ИНТЕРПРЕТАЦИЯ", 9.38, 1.84, 2.75, 0.25, 11, MUTED, True)
add_text(s, "Очень высокий объём", 9.38, 2.40, 2.64, 0.28, 16, RED, True)
add_text(s, "может означать, что основная масса покупателей уже вошла, а движение приблизилось к насыщению.", 9.38, 2.80, 2.64, 1.02, 12, TEXT)
add_line(s, 9.38, 4.03, 12.16, 4.03, GRID, 1)
add_text(s, "Следствие", 9.38, 4.35, 2.64, 0.24, 11, TEAL, True)
add_text(s, "Объём используется как ограничитель риска, а не как разрешение купить больше.", 9.38, 4.75, 2.64, 0.90, 14, TEXT, True)

# 5 — sizing tiers
s = base_slide(prs, "Три режима размера позиции", "Процентиль считается среди предыдущих аналогичных импульсов >0,7%; границы пересчитываются со временем", 5)
tier_cards = [
    ("≤ P60", "10 + 10", "Импульс обычный", "При Close > max добавить 10 по Open следующей минуты", GREEN),
    ("P60–P80", "только 10", "Объём повышенный", "Первый вход разрешён; усиление запрещено", TEAL),
    ("> P80", "0", "Возможная кульминация", "Сделку пропустить полностью", RED),
]
for i, (band, qty, title, body, color) in enumerate(tier_cards):
    x = 0.72 + i * 4.02
    add_rect(s, x, 1.55, 3.76, 3.86, PANEL, True, color)
    add_text(s, band, x+0.30, 1.88, 1.22, 0.34, 18, color, True)
    add_text(s, qty, x+1.64, 1.82, 1.70, 0.48, 27, TEXT, True, PP_ALIGN.RIGHT)
    add_line(s, x+0.30, 2.55, x+3.42, 2.55, GRID, 1)
    add_text(s, title, x+0.30, 2.88, 3.02, 0.30, 15, TEXT, True)
    add_text(s, body, x+0.30, 3.43, 3.02, 0.82, 12, MUTED)
    tag = "РАБОЧИЙ" if i == 0 else ("ОСТОРОЖНО" if i == 1 else "ПРОПУСК")
    add_rect(s, x+0.30, 4.63, 1.55, 0.38, color, True, color)
    add_text(s, tag, x+0.30, 4.72, 1.55, 0.18, 9, BG, True, PP_ALIGN.CENTER)
add_rect(s, 0.72, 5.76, 11.90, 0.82, PANEL_2, True, GRID)
add_text(s, "Текущие справочные границы:", 1.02, 6.02, 2.42, 0.24, 11, MUTED, True)
add_text(s, "P60 ≈ 3,23×", 3.54, 5.95, 1.74, 0.32, 16, GREEN, True)
add_text(s, "P80 ≈ 5,15×", 5.62, 5.95, 1.74, 0.32, 16, ORANGE, True)
add_text(s, "В OOS пороги были заморожены на обучающей части: 2,92× и 4,51×.", 7.55, 6.00, 4.45, 0.25, 10, TEXT)

# 6 — results percent first
s = base_slide(prs, "Результат всей выборки", "Главные показатели — в процентах; рублёвый эквивалент рассчитан для рабочего объёма 10→20 MIX", 6)
metric_card(s, 0.72, 1.50, 2.78, "+93,78%", "Σ результатов", GREEN, 23)
metric_card(s, 3.72, 1.50, 2.78, "+0,483%", "средняя сделка", TEAL, 23)
metric_card(s, 6.72, 1.50, 2.78, "−5,53%", "max DD", ORANGE, 23)
metric_card(s, 9.72, 1.50, 2.90, "−4,04%", "худшая сделка", RED, 23)

add_rect(s, 0.72, 2.88, 7.50, 3.42, PANEL, True, GRID)
add_text(s, "КАЧЕСТВО СТРАТЕГИИ", 1.02, 3.18, 3.20, 0.25, 11, MUTED, True)
stats = [
    ("Profit Factor", f"{summary['profit_factor']:.2f}", TEAL),
    ("Прибыльных сделок", f"{summary['win_rate_pct']:.2f}%", GREEN),
    ("Сделок", f"{int(summary['trades'])}", TEXT),
    ("С добавлением до 20", f"{int(summary['realized_second_legs'])}", BLUE),
]
for i, (label, value, color) in enumerate(stats):
    y = 3.72 + i * 0.56
    add_text(s, label, 1.04, y, 3.15, 0.24, 11, MUTED)
    add_text(s, value, 5.83, y-0.02, 1.62, 0.28, 15, color, True, PP_ALIGN.RIGHT)
    if i < 3:
        add_line(s, 1.04, y+0.34, 7.46, y+0.34, GRID, 0.7)

add_rect(s, 8.48, 2.88, 4.14, 3.42, PANEL_2, True, GRID)
add_text(s, "ЭКВИВАЛЕНТ ДЛЯ 10 MIX", 8.82, 3.18, 3.10, 0.25, 11, MUTED, True)
money = [
    ("Net", f"+{sum(pnls_10)/1_000_000:.2f} млн ₽", GREEN),
    ("Средняя сделка", f"+{fmean(pnls_10):,.0f} ₽".replace(",", " "), TEAL),
    ("Max DD", f"−{abs(summary['max_drawdown_rub']*SCALE):,.0f} ₽".replace(",", " "), ORANGE),
    ("Худшая", f"−{abs(summary['worst_trade_rub']*SCALE):,.0f} ₽".replace(",", " "), RED),
]
for i, (label, value, color) in enumerate(money):
    y = 3.73 + i * 0.57
    add_text(s, label, 8.82, y, 1.35, 0.22, 10, MUTED)
    add_text(s, value, 10.05, y-0.02, 1.83, 0.27, 13, color, True, PP_ALIGN.RIGHT)
add_text(s, "Σ% — сумма результатов сделок к номиналу первых 10, не доходность на маржу.", 0.78, 6.62, 11.2, 0.25, 10, ORANGE, False, italic=True)

# 7 — comparison
s = base_slide(prs, "Цена снижения риска", "Ступенчатый объём отдаёт часть общей прибыли, но значительно повышает качество и устойчивость", 7)
comparison = [
    ("Σ результатов", baseline["cumulative_return_initial_100_pct"], summary["cumulative_return_initial_100_pct"], "%", GREEN),
    ("Profit Factor", baseline["profit_factor"], summary["profit_factor"], "", TEAL),
    ("Max DD", abs(baseline["max_drawdown_initial_100_pct"]), abs(summary["max_drawdown_initial_100_pct"]), "%", ORANGE),
    ("Сделок", baseline["trades"], summary["trades"], "", TEXT),
]
add_text(s, "БЕЗ ОБЪЁМНОГО ФИЛЬТРА", 4.42, 1.54, 2.65, 0.24, 10, MUTED, True, PP_ALIGN.CENTER)
add_text(s, "СТУПЕНЧАТАЯ ВЕРСИЯ", 8.50, 1.54, 2.65, 0.24, 10, TEAL, True, PP_ALIGN.CENTER)
for i, (label, old, new, suffix, color) in enumerate(comparison):
    y = 2.05 + i * 0.88
    add_rect(s, 0.72, y-0.10, 11.90, 0.67, PANEL if i % 2 == 0 else PANEL_2, True, GRID)
    add_text(s, label, 1.03, y+0.05, 2.80, 0.25, 12, TEXT, True)
    add_text(s, f"{old:.2f}{suffix}" if isinstance(old, float) else str(int(old)), 4.42, y, 2.65, 0.32, 17, MUTED, True, PP_ALIGN.CENTER)
    add_text(s, f"{new:.2f}{suffix}" if isinstance(new, float) else str(int(new)), 8.50, y, 2.65, 0.32, 17, color, True, PP_ALIGN.CENTER)

add_rect(s, 0.72, 5.77, 11.90, 0.82, PANEL_2, True, GRID)
add_text(s, "ИТОГ", 1.02, 6.02, 0.92, 0.23, 10, TEAL, True)
add_text(s, "PF +25% · просадка меньше на 56% · Σ результатов ниже на 15,5%", 2.00, 5.95, 8.90, 0.31, 16, TEXT, True)

# 8 — OOS
s = base_slide(prs, "Проверка на последних 30% данных", "Пороги объёма определены до 15.10.2025 и затем заморожены — поздний участок не участвовал в подборе", 8)
metric_card(s, 0.72, 1.50, 2.78, f"+{test_stats['sum_pct']:.2f}%", "OOS · Σ результатов", GREEN, 22)
metric_card(s, 3.72, 1.50, 2.78, f"{test_stats['pf']:.2f}", "OOS · Profit Factor", TEAL, 23)
metric_card(s, 6.72, 1.50, 2.78, f"+{test_stats['avg_pct']:.3f}%", "OOS · средняя сделка", BLUE, 21)
metric_card(s, 9.72, 1.50, 2.90, f"{test_stats['trades']}", "OOS · сделок", TEXT, 23)

add_rect(s, 0.72, 2.92, 11.90, 2.72, PANEL, True, GRID)
add_text(s, "OOS: БЕЗ ФИЛЬТРА vs СТУПЕНЧАТЫЙ ОБЪЁМ", 1.02, 3.22, 4.80, 0.25, 11, MUTED, True)
oos_rows = [
    ("Profit Factor", base_test_stats["pf"], test_stats["pf"], ""),
    ("Средняя сделка", base_test_stats["avg_pct"], test_stats["avg_pct"], "%"),
    ("Σ результатов", base_test_stats["sum_pct"], test_stats["sum_pct"], "%"),
]
for i, (label, old, new, suffix) in enumerate(oos_rows):
    y = 3.80 + i * 0.54
    add_text(s, label, 1.05, y, 2.65, 0.23, 11, TEXT, True)
    add_text(s, f"{old:.3f}{suffix}", 5.00, y, 1.65, 0.24, 12, MUTED, True, PP_ALIGN.RIGHT)
    add_text(s, "→", 6.90, y, 0.55, 0.24, 12, MUTED, True, PP_ALIGN.CENTER)
    add_text(s, f"{new:.3f}{suffix}", 8.00, y, 1.72, 0.24, 13, GREEN, True, PP_ALIGN.RIGHT)
add_text(s, f"Для 10 MIX: OOS net ≈ +{test_stats['net_10']:,.0f} ₽".replace(",", " "), 1.05, 5.22, 4.10, 0.25, 11, TEAL, True)
add_rect(s, 0.72, 5.93, 11.90, 0.68, GREEN, True, GREEN)
add_text(s, "OOS подтвердил идею: сделок меньше, но PF и средний процент на сделку заметно выше.", 1.04, 6.12, 11.05, 0.28, 14, BG, True)

# 9 — yearly stability
s = base_slide(prs, "Результат по календарным годам", "Процентная сумма показывает вклад сделок каждого года; 2023 и 2026 — неполные периоды", 9)
add_rect(s, 0.72, 1.50, 11.90, 4.92, PANEL, True, GRID)
max_abs = max(abs(stats["sum_pct"]) for _, stats in year_rows)
add_text(s, "ГОД", 1.05, 1.82, 0.68, 0.22, 10, MUTED, True)
add_text(s, "СДЕЛКИ", 2.15, 1.82, 0.82, 0.22, 10, MUTED, True)
add_text(s, "Σ%", 3.45, 1.82, 0.72, 0.22, 10, MUTED, True)
add_text(s, "PF", 8.75, 1.82, 0.62, 0.22, 10, MUTED, True)
add_text(s, "СРЕДНЯЯ", 10.28, 1.82, 1.20, 0.22, 10, MUTED, True)
for i, (year, stats) in enumerate(year_rows):
    y = 2.24 + i * 0.63
    color = GREEN if stats["sum_pct"] >= 0 else RED
    add_text(s, str(year), 1.05, y, 0.72, 0.25, 12, TEXT, True)
    add_text(s, str(stats["trades"]), 2.20, y, 0.62, 0.25, 12, TEXT, True)
    add_text(s, f"{stats['sum_pct']:+.2f}%", 3.18, y, 1.02, 0.25, 12, color, True, PP_ALIGN.RIGHT)
    add_rect(s, 4.48, y+0.03, 3.55, 0.20, GRID, True, GRID)
    if stats["sum_pct"] >= 0:
        add_rect(s, 4.48, y+0.03, 3.55*stats["sum_pct"]/max_abs, 0.20, color, True, color)
    add_text(s, f"{stats['pf']:.2f}", 8.70, y, 0.72, 0.25, 12, TEAL, True, PP_ALIGN.RIGHT)
    add_text(s, f"{stats['avg_pct']:+.3f}%", 10.20, y, 1.20, 0.25, 12, color, True, PP_ALIGN.RIGHT)
add_text(s, "Годовая таблица нужна для контроля стабильности, а не для выбора нового параметра по каждому году.", 0.78, 6.65, 11.4, 0.25, 10, ORANGE, False, italic=True)

# 10 — implementation
s = base_slide(prs, "Как применять в реальной торговле", "Правило готово к формализации, но объёмный процентиль должен обновляться только по уже завершённым импульсам", 10)
items = [
    ("01", "Вести историю", "Сохранять относительный объём каждого первого GREEN-импульса >0,7%.", TEAL),
    ("02", "Пересчитывать P60 / P80", "Использовать только предыдущие аналогичные импульсы; ориентир — последние 100.", GREEN),
    ("03", "Фиксировать режим", "До входа определить: 10+10, только 10 или полный пропуск.", BLUE),
    ("04", "Исполнять без подглядывания", "Добавление — только после Close > max, по Open следующей минуты.", ORANGE),
    ("05", "Контролировать риск", "Через 30 минут проверить Close; аварийный стоп 2% от средней.", RED),
]
for i, (n, title, body, color) in enumerate(items):
    y = 1.52 + i * 0.91
    add_text(s, n, 0.78, y+0.15, 0.52, 0.28, 14, color, True)
    add_rect(s, 1.48, y, 7.03, 0.72, PANEL, True, GRID)
    add_text(s, title, 1.78, y+0.12, 2.10, 0.25, 11, color, True)
    add_text(s, body, 3.92, y+0.10, 4.20, 0.40, 10.5, TEXT)

add_rect(s, 8.78, 1.52, 3.84, 4.36, PANEL_2, True, GRID)
add_text(s, "ОГРАНИЧЕНИЯ БЭКТЕСТА", 9.12, 1.86, 3.00, 0.25, 11, MUTED, True)
risks = [
    "нет моделирования проскальзывания",
    "процентиль может дрейфовать",
    "OOS содержит 49 сделок",
    "Σ% не равна доходности на маржу",
    "результат не гарантирует будущее",
]
for i, text in enumerate(risks):
    add_circle(s, 9.18, 2.52+i*0.57, 0.11, ORANGE)
    add_text(s, text, 9.40, 2.39+i*0.57, 2.58, 0.30, 10.5, TEXT)
add_rect(s, 0.72, 6.18, 11.90, 0.58, TEAL, True, TEAL)
add_text(s, "РАБОЧАЯ ВЕРСИЯ", 1.02, 6.34, 1.62, 0.22, 10, BG, True)
add_text(s, "10 MIX → максимум 20 · P60/P80 по прошлым импульсам · 30 минут · стоп 2% · следующее открытие", 2.67, 6.29, 9.28, 0.29, 13, BG, True)

prs.save(OUT)
print(OUT.resolve())
print("slides", len(prs.slides), "trades", len(details), "oos", len(test_details))
