import math
from pathlib import Path

from openpyxl import load_workbook
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


OUTPUT = Path("MIX_ST_19_4_final_strategy.pptx")
TRADES_FILE = Path("MIX_ST_19_4_impulse_06_07_pullback_04_next_open.xlsx")

BG = RGBColor(8, 16, 28)
PANEL = RGBColor(18, 31, 48)
PANEL_2 = RGBColor(25, 41, 61)
WHITE = RGBColor(243, 247, 252)
MUTED = RGBColor(151, 171, 194)
TEAL = RGBColor(34, 211, 174)
CYAN = RGBColor(71, 166, 255)
ORANGE = RGBColor(255, 178, 75)
RED = RGBColor(255, 99, 123)
GRID = RGBColor(50, 68, 89)


def rect(slide, x, y, w, h, color, rounded=True, line=None):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid(); shape.fill.fore_color.rgb = color
    shape.line.color.rgb = line or color
    return shape


def text(slide, value, x, y, w, h, size=18, color=WHITE, bold=False,
         align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame; frame.clear()
    frame.margin_left = frame.margin_right = Inches(0.03)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    frame.vertical_anchor = valign
    p = frame.paragraphs[0]; p.alignment = align
    run = p.add_run(); run.text = value
    run.font.name = "Aptos"; run.font.size = Pt(size); run.font.bold = bold
    run.font.color.rgb = color
    return box


def bg(slide):
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG


def header(slide, title, subtitle, number):
    text(slide, title, 0.68, 0.35, 10.9, 0.53, 27, WHITE, True)
    text(slide, "MIX · ST 19 / 4", 11.0, 0.40, 1.65, 0.30, 12, TEAL, True, PP_ALIGN.RIGHT)
    rect(slide, 0.68, 1.00, 11.95, 0.025, GRID, False)
    text(slide, subtitle, 0.70, 1.10, 11.8, 0.35, 12, MUTED)
    footer(slide, number)


def footer(slide, number):
    text(slide, "MIX · минутные данные · гэпы контрактов скорректированы", 0.70, 7.18, 8.0, 0.18, 9, MUTED)
    text(slide, str(number), 12.15, 7.16, 0.45, 0.18, 9, MUTED, align=PP_ALIGN.RIGHT)


def kpi(slide, x, y, w, value, label, color=TEAL, note=None):
    rect(slide, x, y, w, 1.17, PANEL)
    rect(slide, x, y, 0.055, 1.17, color, False)
    text(slide, value, x + 0.22, y + 0.12, w - 0.35, 0.43, 23, WHITE, True)
    text(slide, label, x + 0.22, y + 0.59, w - 0.35, 0.30, 10, MUTED)
    if note:
        text(slide, note, x + 0.22, y + 0.91, w - 0.35, 0.18, 8, color)


def card(slide, x, y, w, h, title_value, body, accent=TEAL, number=None):
    rect(slide, x, y, w, h, PANEL)
    if number is not None:
        rect(slide, x + 0.22, y + 0.22, 0.42, 0.42, accent)
        text(slide, str(number), x + 0.22, y + 0.245, 0.42, 0.25, 13, BG, True, PP_ALIGN.CENTER)
        tx = x + 0.78
    else:
        rect(slide, x + 0.22, y + 0.26, 0.055, h - 0.52, accent, False)
        tx = x + 0.43
    text(slide, title_value, tx, y + 0.20, w - (tx - x) - 0.22, 0.35, 16, WHITE, True)
    text(slide, body, x + 0.25, y + 0.72, w - 0.50, h - 0.88, 11, MUTED)


def hbar(slide, label, value, max_value, x, y, w, color, value_text):
    text(slide, label, x, y, 1.48, 0.26, 10, WHITE, True)
    rect(slide, x + 1.54, y + 0.05, w - 3.58, 0.16, GRID, False)
    rect(slide, x + 1.54, y + 0.05, (w - 3.58) * value / max_value, 0.16, color, False)
    text(slide, value_text, x + w - 1.98, y - 0.01, 1.98, 0.27, 9, color, True, PP_ALIGN.RIGHT)


def equity_values():
    wb = load_workbook(TRADES_FILE, read_only=True, data_only=True)
    ws = wb["Trades"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows); col = {name: i for i, name in enumerate(headers)}
    trades = []
    for row in rows:
        if float(row[col["minimum_impulse_pct"]]) == 0.7:
            trades.append((row[col["entry_time"]], float(row[col["net_pnl_rub"]])))
    trades.sort()
    equity = 0.0
    result = []
    for index, (_, pnl) in enumerate(trades, 1):
        equity += pnl / 1_000_000
        result.append((index, equity))
    return result


def add_equity_chart(slide, x, y, w, h):
    values = equity_values()
    raw = [v for _, v in values]
    lo = math.floor(min(0, min(raw)) / 5) * 5
    hi = math.ceil(max(raw) / 5) * 5
    if hi == lo:
        hi = lo + 5
    plot_x, plot_y = x + 0.50, y + 0.12
    plot_w, plot_h = w - 0.66, h - 0.48
    for i in range(6):
        value = lo + (hi - lo) * i / 5
        yy = plot_y + plot_h - plot_h * i / 5
        rect(slide, plot_x, yy, plot_w, 0.012, GRID, False)
        text(slide, f"{value:.0f}", x, yy - 0.10, 0.38, 0.20, 8, MUTED, align=PP_ALIGN.RIGHT)
    sampled = values[::2]
    if sampled[-1] != values[-1]:
        sampled.append(values[-1])
    points = []
    for index, value in sampled:
        xx = plot_x + plot_w * (index - 1) / max(1, len(values) - 1)
        yy = plot_y + plot_h - plot_h * (value - lo) / (hi - lo)
        points.append((xx, yy))
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        line = slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
        line.line.color.rgb = TEAL; line.line.width = Pt(1.7)
    text(slide, "1", plot_x, plot_y + plot_h + 0.08, 0.30, 0.18, 8, MUTED)
    text(slide, str(len(values)), plot_x + plot_w - 0.45, plot_y + plot_h + 0.08, 0.45, 0.18, 8, MUTED, align=PP_ALIGN.RIGHT)


def main():
    prs = Presentation()
    prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1 — cover
    slide = prs.slides.add_slide(blank); bg(slide)
    rect(slide, 0, 0, 0.18, 7.5, TEAL, False)
    text(slide, "ТОРГОВАЯ СТРАТЕГИЯ", 0.86, 0.92, 5.5, 0.35, 13, TEAL, True)
    text(slide, "MIX · SuperTrend 19 / 4", 0.84, 1.40, 7.2, 0.65, 34, WHITE, True)
    text(slide, "Сильный импульс → коррекция → Long → следующее открытие", 0.86, 2.22, 7.0, 0.82, 19, MUTED)
    rect(slide, 0.86, 3.36, 6.45, 1.30, PANEL)
    text(slide, "> 0,7%", 1.18, 3.67, 1.35, 0.48, 26, TEAL, True)
    text(slide, "импульс", 1.18, 4.13, 1.25, 0.24, 10, MUTED)
    text(slide, "−0,4%", 3.20, 3.67, 1.35, 0.48, 26, CYAN, True)
    text(slide, "коррекция", 3.20, 4.13, 1.25, 0.24, 10, MUTED)
    text(slide, "100 MIX", 5.19, 3.67, 1.55, 0.48, 26, ORANGE, True)
    text(slide, "позиция", 5.19, 4.13, 1.25, 0.24, 10, MUTED)
    rect(slide, 8.20, 1.12, 4.18, 4.95, PANEL_2)
    text(slide, "+20,40", 8.63, 1.75, 3.3, 0.78, 39, TEAL, True, PP_ALIGN.CENTER)
    text(slide, "млн ₽ · Σ +77,5%", 8.63, 2.48, 3.3, 0.35, 16, MUTED, True, PP_ALIGN.CENTER)
    rect(slide, 8.67, 3.12, 3.25, 0.025, GRID, False)
    text(slide, "PF 1,64", 8.63, 3.50, 3.3, 0.40, 23, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "Win rate 59,8%", 8.63, 4.12, 3.3, 0.35, 17, CYAN, True, PP_ALIGN.CENTER)
    text(slide, "271 сделка", 8.63, 4.72, 3.3, 0.35, 15, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "Результат бэктеста · 100 контрактов", 0.86, 6.72, 6.2, 0.24, 11, MUTED)

    # 2 — setup geometry
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Логика сделки", "Первый сильный зелёный импульс дня; вход выполняется только во время красного ST", 2)
    cards = [
        ("Импульс", "Зелёное движение ST 19/4 строго больше 0,7%.", TEAL),
        ("Коррекция", "После смены ST на красный измеряем снижение от вершины.", RED),
        ("Вход", "На −0,4% от максимума покупаем 100 MIX.", CYAN),
        ("Выход", "Вся позиция закрывается на следующем торговом открытии.", ORANGE),
    ]
    for i, (ttl, body, accent) in enumerate(cards):
        card(slide, 0.72 + i * 3.10, 1.62, 2.82, 1.70, ttl, body, accent, i + 1)
        if i < 3:
            text(slide, "→", 3.49 + i * 3.10, 2.15, 0.28, 0.35, 20, MUTED, True, PP_ALIGN.CENTER)
    # stylized path
    rect(slide, 0.75, 3.78, 11.82, 2.20, PANEL)
    points = [(1.10, 5.45), (2.15, 5.18), (3.18, 4.72), (4.25, 4.10), (5.18, 4.77), (6.02, 5.00), (7.10, 4.57), (8.25, 4.35), (9.45, 4.05), (11.65, 3.98)]
    for i in range(len(points) - 1):
        x1, y1 = points[i]; x2, y2 = points[i + 1]
        line = slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
        line.line.color.rgb = TEAL if i < 4 or i >= 6 else RED
        line.line.width = Pt(3)
    text(slide, "СТАРТ", 0.98, 5.62, 0.72, 0.22, 9, MUTED)
    text(slide, "ВЕРШИНА", 4.55, 3.93, 1.15, 0.22, 9, TEAL, True)
    text(slide, "−0,4% · LONG", 5.50, 5.14, 1.55, 0.24, 10, CYAN, True)
    text(slide, "СЛЕД. ОТКРЫТИЕ", 10.25, 3.70, 1.65, 0.22, 9, ORANGE, True)
    text(slide, "Если уровень не достигнут до окончания красного ST — сделки нет", 0.84, 6.23, 7.4, 0.30, 13, WHITE, True)

    # 3 — optimization
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Почему именно 0,7% и −0,4%", "Перебор параметров на одинаковых правилах выхода и размере позиции", 3)
    rect(slide, 0.72, 1.58, 5.85, 4.95, PANEL)
    text(slide, "ПОРОГ ИМПУЛЬСА · NET МЛН ₽ / Σ%", 1.02, 1.88, 4.2, 0.28, 12, MUTED, True)
    hbar(slide, "> 0,6%", 13.44, 20.40, 1.03, 2.46, 5.10, CYAN, "+13,44 / +52,2%")
    hbar(slide, "> 0,7%", 20.40, 20.40, 1.03, 3.08, 5.10, TEAL, "+20,40 / +77,5%")
    text(slide, "0,7%", 1.03, 3.80, 1.10, 0.42, 23, TEAL, True)
    text(slide, "59,8% прибыльных · PF 1,64\nпросадка −3,97 млн ₽ / Σ −12,83%", 2.20, 3.82, 3.70, 0.85, 13, WHITE, True)
    rect(slide, 1.02, 5.05, 5.10, 0.90, PANEL_2)
    text(slide, "Меньше сигналов, но выше качество:\nприбыль +52% относительно порога 0,6%", 1.25, 5.25, 4.65, 0.52, 12, TEAL, True)
    rect(slide, 6.78, 1.58, 5.85, 4.95, PANEL)
    text(slide, "КОРРЕКЦИЯ ДЛЯ ВХОДА · NET МЛН ₽ / Σ%", 7.08, 1.88, 4.8, 0.28, 12, MUTED, True)
    hbar(slide, "−0,3%", 10.68, 13.44, 7.10, 2.46, 5.10, CYAN, "+10,68 / +41,9%")
    hbar(slide, "−0,4%", 13.44, 13.44, 7.10, 3.08, 5.10, TEAL, "+13,44 / +52,2%")
    hbar(slide, "−0,5%", 3.91, 13.44, 7.10, 3.70, 5.10, RED, "+3,91 / +18,1%")
    rect(slide, 7.08, 4.52, 5.10, 1.42, PANEL_2)
    text(slide, "−0,4% — рабочая середина", 7.33, 4.76, 4.55, 0.32, 17, WHITE, True)
    text(slide, "−0,3% слишком рано; −0,5% чаще означает уже слабый сценарий.", 7.33, 5.22, 4.55, 0.52, 11, MUTED)

    # 4 — main result
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Результат самого прибыльного варианта", "Комиссия включена; 11 межконтрактных переходов скорректированы", 4)
    kpi(slide, 0.72, 1.55, 2.25, "+20,40 млн ₽", "Σ +77,51%", TEAL)
    kpi(slide, 3.13, 1.55, 2.25, "59,8%", "прибыльных сделок", CYAN)
    kpi(slide, 5.54, 1.55, 2.25, "1,64", "Profit Factor", ORANGE)
    kpi(slide, 7.95, 1.55, 2.25, "+75 264 ₽", "средняя: +0,286%", TEAL)
    kpi(slide, 10.36, 1.55, 2.25, "−3,97 млн ₽", "Σ просадка: −12,83%", RED)
    rect(slide, 0.72, 3.05, 8.45, 3.47, PANEL)
    text(slide, "НАКОПЛЕННЫЙ P&L · МЛН ₽ · ИТОГ Σ +77,51%", 1.00, 3.29, 4.7, 0.25, 11, MUTED, True)
    add_equity_chart(slide, 0.92, 3.66, 7.95, 2.55)
    rect(slide, 9.42, 3.05, 3.20, 3.47, PANEL_2)
    text(slide, "ВЫБОРКА", 9.72, 3.33, 1.4, 0.24, 11, MUTED, True)
    text(slide, "630 299", 9.72, 3.75, 2.55, 0.39, 22, WHITE, True)
    text(slide, "минутных баров", 9.72, 4.14, 2.45, 0.24, 10, MUTED)
    text(slide, "21.08.2023 — 21.08.2026", 9.72, 4.62, 2.45, 0.48, 13, CYAN, True)
    text(slide, "326 сигналов\n271 исполненная сделка\n83,1% fill rate", 9.72, 5.25, 2.45, 0.82, 12, WHITE, True)

    # 5 — losses
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Как выглядит типичный убыток", "Основная проблема — продолжение падения после входа, а не гэп следующего открытия", 5)
    kpi(slide, 0.72, 1.55, 2.85, "−203 тыс. ₽", "медианный: −0,698%", RED)
    kpi(slide, 3.77, 1.55, 2.85, "−293 тыс. ₽", "средний: −1,050%", ORANGE)
    kpi(slide, 6.82, 1.55, 2.85, "−1,24%", "типичный максимум против входа", RED)
    kpi(slide, 9.87, 1.55, 2.75, "7 часов", "медиана до минимума", CYAN)
    rect(slide, 0.72, 3.02, 7.45, 3.42, PANEL)
    text(slide, "ГЕОМЕТРИЯ ПРОИГРЫША", 1.00, 3.32, 2.8, 0.25, 11, MUTED, True)
    stages = [
        ("1", "Вход", "−0,4% от вершины", CYAN),
        ("2", "Продолжение вниз", "ещё ≈ −1,24%", RED),
        ("3", "Частичный отскок", "восстановление неполное", ORANGE),
        ("4", "Следующее открытие", "≈ −0,69% от входа", RED),
    ]
    for i, (num, ttl, body, accent) in enumerate(stages):
        y = 3.78 + i * 0.58
        rect(slide, 1.00, y, 0.34, 0.34, accent)
        text(slide, num, 1.00, y + 0.025, 0.34, 0.22, 11, BG, True, PP_ALIGN.CENTER)
        text(slide, ttl, 1.52, y - 0.01, 2.10, 0.28, 13, WHITE, True)
        text(slide, body, 3.74, y, 3.65, 0.27, 12, accent, True)
    rect(slide, 8.42, 3.02, 4.20, 3.42, PANEL_2)
    text(slide, "90,8%", 8.80, 3.48, 1.65, 0.50, 28, TEAL, True)
    text(slide, "убытков уже были ниже входа\nперед следующим открытием", 10.20, 3.50, 2.0, 0.75, 11, MUTED)
    rect(slide, 8.78, 4.47, 3.46, 0.025, GRID, False)
    text(slide, "−89…−381 тыс. ₽", 8.80, 4.85, 3.10, 0.38, 19, WHITE, True)
    text(slide, "−0,33…−1,30%", 8.80, 5.19, 3.10, 0.24, 11, CYAN, True)
    text(slide, "центральные 50% убытков", 8.80, 5.45, 3.10, 0.24, 10, MUTED)
    text(slide, "Худшая: −2,13 млн ₽ / −7,145%", 8.80, 5.86, 3.40, 0.25, 11, RED, True)

    # 6 — risk control
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Как сократить убытки", "Тесные стопы режут восстановление; лучший компромисс — проверка позиции через 6 часов", 6)
    rows = [
        ("Без ограничения", "+20,40 / +77,5%", "−3,97 / −12,8%", "−2,13 / −7,14%", "1,64", TEAL),
        ("6 ч.: закрыть, если минус", "+18,66 / +71,1%", "−2,86 / −9,25%", "−1,05 / −3,72%", "1,66", CYAN),
        ("Стоп −1,5%", "+15,22 / +56,6%", "−2,25 / −8,92%", "−1,36 / −5,30%", "1,48", ORANGE),
        ("Точный touch + контроль", "+6,45 / +23,1%", "−0,91 / −3,20%", "−0,47 / −1,51%", "2,03", RED),
    ]
    text(slide, "ВАРИАНТ", 0.86, 1.72, 3.75, 0.24, 10, MUTED, True)
    text(slide, "NET ₽ / Σ%", 4.68, 1.72, 1.65, 0.24, 10, MUTED, True, PP_ALIGN.RIGHT)
    text(slide, "DD ₽ / Σ%", 6.75, 1.72, 1.65, 0.24, 10, MUTED, True, PP_ALIGN.RIGHT)
    text(slide, "ХУДШАЯ ₽ / %", 8.72, 1.72, 1.65, 0.24, 10, MUTED, True, PP_ALIGN.RIGHT)
    text(slide, "PF", 11.32, 1.72, 0.70, 0.24, 10, MUTED, True, PP_ALIGN.RIGHT)
    for i, (name, net, dd, worst, pf, accent) in enumerate(rows):
        y = 2.10 + i * 0.78
        rect(slide, 0.72, y, 11.90, 0.60, PANEL if i != 1 else PANEL_2)
        rect(slide, 0.72, y, 0.055, 0.60, accent, False)
        text(slide, name, 0.98, y + 0.14, 3.40, 0.27, 12, WHITE, i == 1)
        text(slide, net, 4.50, y + 0.14, 1.83, 0.27, 10, accent, True, PP_ALIGN.RIGHT)
        text(slide, dd, 6.58, y + 0.14, 1.80, 0.27, 10, WHITE, True, PP_ALIGN.RIGHT)
        text(slide, worst, 8.55, y + 0.14, 1.82, 0.27, 10, WHITE, True, PP_ALIGN.RIGHT)
        text(slide, pf, 11.30, y + 0.14, 0.70, 0.27, 13, accent, True, PP_ALIGN.RIGHT)
    rect(slide, 0.72, 5.48, 11.90, 0.92, PANEL_2)
    text(slide, "РЕКОМЕНДАЦИЯ", 1.00, 5.75, 1.55, 0.25, 11, TEAL, True)
    text(slide, "Через 6 часов: если цена ниже входа — выйти; если выше — держать до следующего открытия.", 2.62, 5.67, 9.25, 0.42, 15, WHITE, True)

    # 7 — rejected alternatives
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Что проверили и почему не выбрали", "Все варианты использовали 100 MIX либо эквивалентное суммарное количество", 7)
    alternatives = [
        ("Вход сразу после импульса", "+11,95 млн ₽ · Σ +46,12% · PF 1,24", "Хуже выбранного входа −0,4%.", CYAN),
        ("50 на −0,3% + 50 на −0,6%", "+5,34 млн ₽ · Σ +51,19%", "Денежный результат второй части практически нулевой.", ORANGE),
        ("Ждать повторного зелёного ST", "+13,56 млн ₽ · Σ +53,54% · PF 1,36", "Подтверждение запаздывает; DD −4,36 млн ₽ / Σ −14,01%.", TEAL),
        ("Фильтр красной коррекции 90%", "+7,32 млн ₽ · Σ +28,52%", "Снижает хвостовой риск, но почти вдвое режет прибыль.", RED),
    ]
    for i, (ttl, metric, body, accent) in enumerate(alternatives):
        x = 0.72 + (i % 2) * 6.05
        y = 1.60 + (i // 2) * 2.48
        rect(slide, x, y, 5.84, 2.14, PANEL)
        rect(slide, x, y, 0.06, 2.14, accent, False)
        text(slide, ttl, x + 0.30, y + 0.25, 5.15, 0.34, 16, WHITE, True)
        text(slide, metric, x + 0.30, y + 0.78, 5.15, 0.35, 18, accent, True)
        text(slide, body, x + 0.30, y + 1.31, 5.15, 0.52, 11, MUTED)
    text(slide, "Вывод: цена −0,4% даёт лучший баланс точки входа и качества сигнала; ожидание дополнительного подтверждения преимущества не добавило.", 0.82, 6.58, 11.5, 0.34, 13, WHITE, True)

    # 8 — playbook
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Итоговый торговый алгоритм", "Максимальная прибыль — базовый вариант; практический вариант добавляет контроль через 6 часов", 8)
    steps = [
        ("Найти импульс", "Первый зелёный ST 19/4 дня строго >0,7%.", TEAL),
        ("Дождаться красного ST", "Зафиксировать вершину завершённого зелёного движения.", RED),
        ("Поставить вход", "Long 100 MIX на −0,4% от вершины, пока ST красный.", CYAN),
        ("Контроль риска", "Через 6 часов закрыть позицию, только если она ниже входа.", ORANGE),
        ("Закрыть остаток", "Выход всей позиции на следующем торговом открытии.", TEAL),
    ]
    for i, (ttl, body, accent) in enumerate(steps):
        card(slide, 0.72 + (i % 3) * 4.05, 1.56 + (i // 3) * 2.15, 3.75, 1.75, ttl, body, accent, i + 1)
    rect(slide, 8.82, 3.71, 3.80, 1.75, PANEL_2)
    text(slide, "ПЕРЕД РЕАЛЬНЫМ ЗАПУСКОМ", 9.10, 4.00, 3.20, 0.28, 11, ORANGE, True)
    text(slide, "• вневыборочная проверка\n• реальное проскальзывание\n• лимит риска на капитал\n• проверка ликвидности 100 MIX", 9.10, 4.43, 3.10, 0.87, 11, WHITE)
    rect(slide, 0.72, 5.83, 11.90, 0.60, TEAL)
    text(slide, "Ключевая идея: покупать не сам импульс, а контролируемую коррекцию после сильного импульса.", 1.02, 5.98, 11.30, 0.28, 15, BG, True, PP_ALIGN.CENTER)
    text(slide, "Σ% = сумма net-доходностей к номиналу на входе; не доходность на маржу. Бэктест не гарантирует будущий результат.", 0.75, 6.72, 11.2, 0.22, 9, MUTED)

    prs.save(OUTPUT)
    print(f"saved={OUTPUT.resolve()} slides={len(prs.slides)}")


if __name__ == "__main__":
    main()
