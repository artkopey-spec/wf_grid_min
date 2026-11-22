from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


ROOT = Path(r"C:\3.1_wf_grid")
OUT = (
    ROOT
    / "results_tester_st_batch_20260828"
    / "ST80_4_222_volatile_days_statistics_20260828.pptx"
)

NAVY = RGBColor(18, 35, 63)
NAVY_2 = RGBColor(30, 57, 92)
TEAL = RGBColor(0, 155, 162)
TEAL_LIGHT = RGBColor(220, 244, 244)
ORANGE = RGBColor(242, 140, 40)
ORANGE_LIGHT = RGBColor(254, 239, 220)
GREEN = RGBColor(46, 139, 87)
GREEN_LIGHT = RGBColor(223, 243, 231)
RED = RGBColor(194, 63, 63)
RED_LIGHT = RGBColor(250, 227, 227)
BLUE_LIGHT = RGBColor(229, 237, 247)
BG = RGBColor(245, 248, 251)
WHITE = RGBColor(255, 255, 255)
TEXT = RGBColor(35, 45, 58)
MUTED = RGBColor(101, 116, 133)
GRID = RGBColor(211, 220, 230)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def set_bg(slide, color=BG):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_text(
    slide,
    text,
    x,
    y,
    w,
    h,
    *,
    size=18,
    color=TEXT,
    bold=False,
    align=PP_ALIGN.LEFT,
    valign=MSO_ANCHOR.TOP,
    font="Aptos",
    margin=0.04,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.margin_left = Inches(margin)
    tf.margin_right = Inches(margin)
    tf.margin_top = Inches(margin)
    tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def add_rich_lines(slide, lines, x, y, w, h, *, size=17, gap=7):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.margin_left = Inches(0.08)
    tf.margin_right = Inches(0.08)
    tf.margin_top = Inches(0.05)
    for index, (lead, body, color) in enumerate(lines):
        p = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        p.level = 0
        r1 = p.add_run()
        r1.text = lead
        r1.font.name = "Aptos"
        r1.font.size = Pt(size)
        r1.font.bold = True
        r1.font.color.rgb = color
        r2 = p.add_run()
        r2.text = body
        r2.font.name = "Aptos"
        r2.font.size = Pt(size)
        r2.font.color.rgb = TEXT
    return box


def add_header(slide, section, title, page):
    add_text(slide, section.upper(), 0.55, 0.28, 3.1, 0.25, size=10, color=TEAL, bold=True)
    add_text(slide, title, 0.55, 0.58, 12.0, 0.55, size=27, color=NAVY, bold=True)
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(1.22), Inches(12.2), Inches(0.025))
    line.fill.solid()
    line.fill.fore_color.rgb = GRID
    line.line.fill.background()
    add_text(slide, f"ST 80/4 · 222 волатильных дня · 09:00–19:00", 0.58, 7.12, 8.2, 0.2, size=9, color=MUTED)
    add_text(slide, f"{page:02d}", 12.1, 7.08, 0.6, 0.24, size=10, color=MUTED, bold=True, align=PP_ALIGN.RIGHT)


def add_card(slide, x, y, w, h, value, label, *, color=TEAL, note=None):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = WHITE
    shape.line.color.rgb = GRID
    shape.line.width = Pt(0.8)
    accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(0.08), Inches(h))
    accent.fill.solid()
    accent.fill.fore_color.rgb = color
    accent.line.fill.background()
    add_text(slide, value, x + 0.22, y + 0.16, w - 0.35, 0.48, size=26, color=color, bold=True)
    add_text(slide, label, x + 0.22, y + 0.69, w - 0.35, 0.36, size=12, color=TEXT, bold=True)
    if note:
        add_text(slide, note, x + 0.22, y + 1.04, w - 0.35, h - 1.1, size=9, color=MUTED)
    return shape


def add_callout(slide, text, x, y, w, h, *, fill=TEAL_LIGHT, color=NAVY):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    add_text(slide, text, x + 0.18, y + 0.12, w - 0.36, h - 0.24, size=14, color=color, bold=True, valign=MSO_ANCHOR.MIDDLE)


def add_table(slide, headers, rows, x, y, w, h, *, widths=None, font_size=11, first_col_bold=False):
    table_shape = slide.shapes.add_table(len(rows) + 1, len(headers), Inches(x), Inches(y), Inches(w), Inches(h))
    table = table_shape.table
    if widths:
        total = sum(widths)
        for idx, width in enumerate(widths):
            table.columns[idx].width = Inches(w * width / total)
    for col, header in enumerate(headers):
        cell = table.cell(0, col)
        cell.text = str(header)
        cell.fill.solid()
        cell.fill.fore_color.rgb = NAVY_2
        cell.margin_left = Inches(0.05)
        cell.margin_right = Inches(0.05)
        cell.margin_top = Inches(0.04)
        cell.margin_bottom = Inches(0.04)
        for p in cell.text_frame.paragraphs:
            p.alignment = PP_ALIGN.CENTER
            for run in p.runs:
                run.font.name = "Aptos"
                run.font.size = Pt(font_size)
                run.font.bold = True
                run.font.color.rgb = WHITE
    for row_idx, row in enumerate(rows, start=1):
        for col_idx, value in enumerate(row):
            cell = table.cell(row_idx, col_idx)
            cell.text = str(value)
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE if row_idx % 2 else BLUE_LIGHT
            cell.margin_left = Inches(0.05)
            cell.margin_right = Inches(0.05)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            for p in cell.text_frame.paragraphs:
                p.alignment = PP_ALIGN.LEFT if col_idx == 0 else PP_ALIGN.CENTER
                for run in p.runs:
                    run.font.name = "Aptos"
                    run.font.size = Pt(font_size)
                    run.font.color.rgb = TEXT
                    run.font.bold = bool(first_col_bold and col_idx == 0)
    return table


def add_horizontal_bars(slide, items, x, y, w, h, *, max_value=None, color=TEAL, value_suffix=""):
    max_value = max_value or max(value for _, value in items)
    row_h = h / len(items)
    label_w = 1.25
    for idx, (label, value) in enumerate(items):
        yy = y + idx * row_h
        add_text(slide, label, x, yy + 0.04, label_w, row_h - 0.05, size=11, color=TEXT, bold=True, valign=MSO_ANCHOR.MIDDLE)
        track = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x + label_w), Inches(yy + 0.14), Inches(w - label_w - 0.8), Inches(0.22))
        track.fill.solid()
        track.fill.fore_color.rgb = GRID
        track.line.fill.background()
        bar_w = max(0.04, (w - label_w - 0.8) * value / max_value)
        bar = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x + label_w), Inches(yy + 0.14), Inches(bar_w), Inches(0.22))
        bar.fill.solid()
        bar.fill.fore_color.rgb = color
        bar.line.fill.background()
        add_text(slide, f"{value:.2f}{value_suffix}", x + w - 0.72, yy + 0.05, 0.7, row_h - 0.05, size=10, color=NAVY, bold=True, align=PP_ALIGN.RIGHT, valign=MSO_ANCHOR.MIDDLE)


def title_slide(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, NAVY)
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), SLIDE_W, Inches(0.12))
    band.fill.solid()
    band.fill.fore_color.rgb = TEAL
    band.line.fill.background()
    add_text(slide, "ИССЛЕДОВАНИЕ СТРАТЕГИИ", 0.72, 0.65, 4.5, 0.35, size=12, color=TEAL, bold=True)
    add_text(slide, "SuperTrend 80 / 4", 0.72, 1.28, 8.6, 0.85, size=42, color=WHITE, bold=True)
    add_text(slide, "Только подтверждённые волатильные дни", 0.72, 2.18, 10.5, 0.55, size=25, color=WHITE, bold=True)
    add_text(slide, "222 торговые даты · фильтр времени 09:00–19:00 · Si 1m", 0.72, 2.88, 10.3, 0.42, size=17, color=RGBColor(196, 211, 226))
    add_card(slide, 0.72, 4.18, 2.75, 1.46, "+76,29%", "Sum Net PnL", color=TEAL)
    add_card(slide, 3.66, 4.18, 2.75, 1.46, "−4,75%", "Max Drawdown", color=GREEN)
    add_card(slide, 6.60, 4.18, 2.75, 1.46, "39,24%", "Win Rate", color=ORANGE)
    add_card(slide, 9.54, 4.18, 2.75, 1.46, "3 038", "внутридневных сделок", color=ORANGE)
    add_text(slide, "Подготовлено 28.08.2026 · аналитический материал", 0.75, 6.92, 7.0, 0.25, size=10, color=RGBColor(162, 181, 201))


def build():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    title_slide(prs)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "01 · Резюме", "Главное в шести цифрах", 2)
    add_card(slide, 0.65, 1.55, 2.0, 1.5, "222", "волатильных дня", color=TEAL, note="125 STRONG + 97 CONFIRMED")
    add_card(slide, 2.83, 1.55, 2.0, 1.5, "+76,29%", "Sum Net PnL", color=GREEN, note="Комиссия учтена")
    add_card(slide, 5.01, 1.55, 2.0, 1.5, "−4,75%", "Max Drawdown", color=GREEN)
    add_card(slide, 7.19, 1.55, 2.0, 1.5, "39,24%", "Win Rate", color=ORANGE)
    add_card(slide, 9.37, 1.55, 2.0, 1.5, "13,07", "флипа в день", color=TEAL)
    add_card(slide, 11.55, 1.55, 1.15, 1.5, "0,249%", "ср. флип→флип", color=TEAL)
    add_rich_lines(slide, [
        ("Победитель: ", "ST 80/4 занял первое место среди 11 протестированных вариантов.", TEAL),
        ("Устойчивость: ", "148 из 222 дат прибыльны; без 30 лучших дат остаётся +16,25%.", GREEN),
        ("Время: ", "до 13:00 сформировано 94,55% общей прибыли.", ORANGE),
        ("Механика: ", "средняя прибыльная сделка в 1,90 раза больше среднего убытка.", TEAL),
    ], 0.75, 3.55, 11.9, 2.35, size=17, gap=10)
    add_callout(slide, "Основной результат: ST 80/4 показывает положительное ожидание именно внутри выбранного режима волатильности.", 0.75, 6.25, 11.9, 0.58)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "02 · Выборка", "Как сформированы 222 волатильных дня", 3)
    add_card(slide, 0.7, 1.55, 2.75, 1.45, "320", "дней с ST-движением >0,8%", color=ORANGE)
    add_card(slide, 3.68, 1.55, 2.75, 1.45, "222", "подтверждены ≥2 критериями", color=TEAL)
    add_card(slide, 6.66, 1.55, 2.75, 1.45, "125", "STRONG · 3 из 3", color=GREEN)
    add_card(slide, 9.64, 1.55, 2.75, 1.45, "97", "CONFIRMED · 2 из 3", color=TEAL)
    add_text(slide, "Дополнительные критерии верхних 35% волатильности", 0.75, 3.42, 6.0, 0.35, size=18, color=NAVY, bold=True)
    add_rich_lines(slide, [
        ("1. ", "Дневной диапазон High–Low, %", TEAL),
        ("2. ", "Реализованная минутная волатильность, %", TEAL),
        ("3. ", "NATR(14), %", TEAL),
    ], 0.8, 3.92, 5.8, 1.75, size=16)
    add_text(slide, "Правила расчёта стратегии", 7.0, 3.42, 5.2, 0.35, size=18, color=NAVY, bold=True)
    add_rich_lines(slide, [
        ("Время: ", "входы в рамках 09:00–19:00", ORANGE),
        ("Сделки: ", "только Entry Date = Exit Date", ORANGE),
        ("Overnight: ", "одна сделка 20.03→23.03 исключена", RED),
    ], 7.05, 3.92, 5.2, 1.75, size=16)
    add_callout(slide, "Важно для прода: текущий список дней определён по итогам полной сессии. Реальный фильтр волатильности должен использовать только данные, известные к моменту входа.", 0.75, 6.15, 11.9, 0.72, fill=ORANGE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "03 · Сравнение", "ST 80/4 — лидер среди 11 вариантов", 4)
    rows = [
        ("1", "80 / 4,0", "+76,29%", "−4,75%", "39,24%", "3 038"),
        ("2", "40 / 4,0", "+74,83%", "−7,90%", "39,20%", "2 778"),
        ("3", "73 / 4,0", "+74,41%", "−6,51%", "39,23%", "3 000"),
        ("4", "60 / 3,8", "+66,61%", "−6,33%", "38,64%", "3 137"),
        ("5", "30 / 3,6", "+63,95%", "−7,83%", "38,34%", "3 138"),
    ]
    add_table(slide, ["Место", "ST", "Sum PnL", "Max DD", "Win Rate", "Сделок"], rows, 0.75, 1.62, 7.7, 3.75, widths=[0.7, 1.3, 1.4, 1.4, 1.3, 1.2], font_size=13)
    add_card(slide, 8.78, 1.62, 3.8, 1.42, "+1,46 п.п.", "преимущество PnL над ST 40/4", color=TEAL)
    add_card(slide, 8.78, 3.28, 3.8, 1.42, "3,15 п.п.", "лучше просадка, чем ST 40/4", color=GREEN)
    add_callout(slide, "ST 80/4 одновременно лучший по прибыли, просадке и Win Rate в выбранном режиме.", 8.78, 4.95, 3.8, 1.12)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "04 · Устойчивость", "Прибыль не зависит от нескольких удачных дат", 5)
    add_card(slide, 0.7, 1.55, 2.5, 1.4, "148", "прибыльных дат · 66,7%", color=GREEN)
    add_card(slide, 3.42, 1.55, 2.5, 1.4, "74", "убыточных даты", color=RED)
    add_card(slide, 6.14, 1.55, 2.5, 1.4, "+0,344%", "средний PnL даты", color=TEAL)
    add_card(slide, 8.86, 1.55, 2.5, 1.4, "+0,304%", "медианный PnL даты", color=TEAL)
    stress_rows = [
        ("Все даты", "+76,29%", "—"),
        ("Без 1 лучшей", "+72,22%", "−4,07%"),
        ("Без 5 лучших", "+61,09%", "−15,20%"),
        ("Без 10 лучших", "+50,26%", "−26,03%"),
        ("Без 20 лучших", "+31,21%", "−45,09%"),
        ("Без 30 лучших", "+16,25%", "−60,04%"),
    ]
    add_table(slide, ["Стресс-тест", "Оставшийся PnL", "Удалено"], stress_rows, 0.75, 3.38, 6.2, 2.65, widths=[2.2, 1.6, 1.4], font_size=12, first_col_bold=True)
    add_rich_lines(slide, [
        ("Лучший день: ", "13.06.2024 · +4,07% · только 5,34% общей прибыли", TEAL),
        ("Усечение хвостов: ", "без 10 лучших и 10 худших дат остаётся +69,90%", GREEN),
        ("Вывод: ", "результат распределён по выборке, а не сформирован единичными выбросами", GREEN),
    ], 7.35, 3.48, 5.15, 2.45, size=15, gap=10)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "05 · Стабильность", "Результат положителен во всех годах", 6)
    year_rows = [
        ("2023", "57", "41", "+18,91%", "811"),
        ("2024", "35", "27", "+29,60%", "416"),
        ("2025", "86", "53", "+22,67%", "1 165"),
        ("2026*", "44", "27", "+5,12%", "646"),
    ]
    add_table(slide, ["Год", "Дат", "Прибыльных", "Sum PnL", "Сделок"], year_rows, 0.75, 1.62, 6.6, 3.45, widths=[1, 1, 1.4, 1.4, 1.2], font_size=14)
    add_horizontal_bars(slide, [("2023",18.91),("2024",29.60),("2025",22.67),("2026*",5.12)], 7.8, 1.75, 4.6, 3.0, max_value=30, color=TEAL, value_suffix="%")
    add_callout(slide, "2026 год неполный. Положительный результат присутствует во всех четырёх календарных подвыборках.", 0.75, 5.55, 11.7, 0.7)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "06 · Флипы", "Как часто ST 80/4 меняет цвет", 7)
    add_card(slide, 0.7, 1.55, 2.5, 1.48, "2 901", "уникальный флип", color=TEAL)
    add_card(slide, 3.42, 1.55, 2.5, 1.48, "13,07", "в среднем за день", color=TEAL)
    add_card(slide, 6.14, 1.55, 2.5, 1.48, "13", "медиана за день", color=TEAL)
    add_card(slide, 8.86, 1.55, 2.5, 1.48, "6–23", "диапазон флипов в день", color=ORANGE)
    add_text(slide, "Средняя частота", 0.8, 3.55, 3.0, 0.35, size=18, color=NAVY, bold=True)
    add_card(slide, 0.8, 4.02, 3.3, 1.45, "1,31 / час", "по полному окну 09:00–19:00", color=TEAL)
    add_card(slide, 4.35, 4.02, 3.3, 1.45, "1,36 / час", "по фактически имеющимся барам", color=TEAL)
    add_card(slide, 7.9, 4.02, 3.3, 1.45, "44–46 мин", "примерно один флип", color=ORANGE)
    add_callout(slide, "Уникальный флип считается один раз, даже если в Signals он представлен строками закрытия и нового открытия.", 0.8, 5.92, 11.4, 0.65, fill=BLUE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "07 · Внутри дня", "До 13:00 преимущество значительно сильнее", 8)
    time_rows = [
        ("Флипов", "1 494", "1 407"),
        ("Флипов на час фактических баров", "1,75", "1,10"),
        ("Сделок", "1 627", "1 411"),
        ("Sum PnL", "+72,13%", "+4,16%"),
        ("Win Rate", "40,32%", "37,99%"),
        ("Средняя прибыль", "+0,3657%", "+0,3166%"),
        ("Средний убыток", "−0,1728%", "−0,1892%"),
        ("Средняя сделка", "+0,0443%", "+0,0030%"),
        ("Profit Factor", "1,430", "1,025"),
    ]
    add_table(slide, ["Показатель", "09:00–13:00", "13:00–19:00"], time_rows, 0.75, 1.5, 7.4, 4.85, widths=[2.5, 1.5, 1.5], font_size=11, first_col_bold=True)
    add_card(slide, 8.55, 1.68, 3.65, 1.5, "94,55%", "общей прибыли получено до 13:00", color=GREEN)
    add_card(slide, 8.55, 3.48, 3.65, 1.5, "≈15×", "выше средняя сделка утром", color=TEAL)
    add_callout(slide, "После 13:00 результат почти нейтрален: PF 1,025 и средняя сделка +0,0030%.", 8.55, 5.3, 3.65, 1.02, fill=ORANGE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "08 · Сделки", "Экономика одной сделки", 9)
    add_card(slide, 0.7, 1.55, 2.55, 1.5, "+0,3436%", "средняя прибыльная сделка", color=GREEN)
    add_card(slide, 3.48, 1.55, 2.55, 1.5, "−0,1805%", "средняя убыточная сделка", color=RED)
    add_card(slide, 6.26, 1.55, 2.55, 1.5, "+0,0251%", "средняя любая сделка", color=TEAL)
    add_card(slide, 9.04, 1.55, 2.55, 1.5, "1,229", "Profit Factor", color=TEAL)
    trade_rows = [
        ("Всего", "3 038", "100%"),
        ("Прибыльные", "1 192", "39,24%"),
        ("Убыточные", "1 846", "60,76%"),
    ]
    add_table(slide, ["Сделки", "Количество", "Доля"], trade_rows, 0.8, 3.65, 4.9, 2.05, widths=[1.7, 1.3, 1.1], font_size=14, first_col_bold=True)
    add_card(slide, 6.25, 3.65, 5.1, 1.45, "1,90×", "средняя прибыль / средний убыток", color=GREEN)
    add_callout(slide, "Стратегия прибыльна не за счёт высокого Win Rate, а за счёт положительной асимметрии размера выигрыша и убытка.", 6.25, 5.35, 5.1, 0.82)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "09 · Флип → флип", "Чистое движение между ценами соседних флипов", 10)
    add_card(slide, 0.75, 1.48, 2.55, 1.38, "2 679", "завершённых сегментов", color=TEAL)
    add_card(slide, 3.55, 1.48, 2.55, 1.38, "0,249%", "среднее движение", color=TEAL)
    add_card(slide, 6.35, 1.48, 2.55, 1.38, "0,180%", "медиана", color=TEAL)
    add_callout(slide, "Формула: |P₂ / P₁ − 1| × 100%. High–Low и внутрисегментные откаты не учитываются.", 9.15, 1.48, 3.3, 1.38, fill=BLUE_LIGHT)
    flip_rows = [
        (">0,30%", "624", "2,81", "14,05"),
        (">0,50%", "272", "1,23", "6,13"),
        (">0,75%", "144", "0,65", "3,24"),
        (">1,00%", "80", "0,36", "1,80"),
        (">1,25%", "45", "0,20", "1,01"),
        (">1,50%", "17", "0,08", "0,38"),
    ]
    add_table(slide, ["Порог", "Всего", "На 1 вол. день", "На 5 вол. дней"], flip_rows, 0.8, 3.25, 11.55, 2.75, widths=[1.4, 1.2, 2.0, 2.0], font_size=13, first_col_bold=True)
    add_text(slide, "Условная неделя = 5 волатильных торговых дней. Обычные дни не входят в знаменатель.", 0.85, 6.25, 11.2, 0.3, size=12, color=MUTED)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "10 · Кластеризация", "Дни с несколькими движениями флип→флип >0,50%", 11)
    cluster_rows = [
        ("≥2 движения", "65", "29,28%", "6,15", "каждые 3,4 вол. дня"),
        ("≥3 движения", "18", "8,11%", "1,70", "каждые 12,3 вол. дня"),
        ("≥4 движения", "7", "3,15%", "0,66", "каждые 31,7 вол. дня"),
    ]
    add_table(slide, ["Условие", "Дней", "Доля 222 дней", "На 21 вол. день", "Периодичность"], cluster_rows, 0.75, 1.72, 11.9, 2.65, widths=[1.7, 0.8, 1.5, 1.6, 2.4], font_size=13, first_col_bold=True)
    add_card(slide, 0.9, 4.9, 3.35, 1.35, "≈29%", "волатильных дней имеют ≥2 движения", color=TEAL)
    add_card(slide, 4.95, 4.9, 3.35, 1.35, "≈8%", "волатильных дней имеют ≥3 движения", color=ORANGE)
    add_card(slide, 9.0, 4.9, 3.0, 1.35, "≈3%", "имеют ≥4 движения", color=RED)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "11 · MAX–MIN", "Полный диапазон одного цвета ST", 12)
    add_card(slide, 0.75, 1.48, 2.55, 1.38, "0,501%", "средний MAX–MIN", color=ORANGE)
    add_card(slide, 3.55, 1.48, 2.55, 1.38, "0,361%", "медианный MAX–MIN", color=ORANGE)
    add_callout(slide, "Формула: (MAX High − MIN Low) / P₁ × 100%. Учитывает весь размах, откаты и шпильки внутри одного цвета.", 6.35, 1.48, 6.0, 1.38, fill=ORANGE_LIGHT)
    range_rows = [
        (">0,50%", "867", "3,91", "19,53"),
        (">0,75%", "473", "2,13", "10,65"),
        (">1,00%", "275", "1,24", "6,19"),
        (">1,25%", "159", "0,72", "3,58"),
        (">1,50%", "88", "0,40", "1,98"),
    ]
    add_table(slide, ["Порог", "Всего", "На 1 вол. день", "На 5 вол. дней"], range_rows, 0.8, 3.25, 11.55, 2.45, widths=[1.4, 1.2, 2.0, 2.0], font_size=14, first_col_bold=True)
    add_text(slide, "Границы как в исходной презентации: бар начального флипа включён, бар следующего флипа исключён.", 0.85, 6.08, 11.2, 0.35, size=12, color=MUTED)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "12 · Сопоставление", "MAX–MIN примерно вдвое больше флип→флип", 13)
    add_card(slide, 0.75, 1.5, 2.8, 1.45, "0,249%", "среднее флип→флип", color=TEAL)
    add_card(slide, 3.85, 1.5, 2.8, 1.45, "2,01×", "отношение средних", color=ORANGE)
    add_card(slide, 6.95, 1.5, 2.8, 1.45, "0,501%", "средний MAX–MIN", color=ORANGE)
    compare_rows = [
        (">0,50%", "6,13", "19,53", "3,19×"),
        (">0,75%", "3,24", "10,65", "3,29×"),
        (">1,00%", "1,80", "6,19", "3,44×"),
        (">1,25%", "1,01", "3,58", "3,54×"),
        (">1,50%", "0,38", "1,98", "5,21×"),
    ]
    add_table(slide, ["Порог", "Флип→флип / 5 дней", "MAX–MIN / 5 дней", "Отношение"], compare_rows, 0.75, 3.55, 9.6, 2.4, widths=[1.3, 2.2, 2.2, 1.3], font_size=13, first_col_bold=True)
    add_callout(slide, "Флип→флип отвечает на вопрос о чистом смещении цены. MAX–MIN — о полном реализованном размахе внутри цвета. Смешивать показатели нельзя.", 10.65, 3.55, 1.9, 2.4, fill=BLUE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "13 · Практический вывод", "Что следует из статистики", 14)
    add_rich_lines(slide, [
        ("1 · Параметр: ", "ST 80/4 — лучший из 11 вариантов на выбранных волатильных датах.", TEAL),
        ("2 · Время: ", "основная зона преимущества — 09:00–13:00; после 13:00 ожидание близко к нулю.", ORANGE),
        ("3 · Риск/доход: ", "низкий Win Rate компенсируется отношением средней прибыли к убытку 1,90×.", GREEN),
        ("4 · Устойчивость: ", "результат положителен по годам и сохраняется без лучших дат.", GREEN),
        ("5 · Движения: ", "чистый флип→флип в среднем 0,249%; полный MAX–MIN — 0,501%.", TEAL),
    ], 0.8, 1.55, 11.8, 3.55, size=17, gap=10)
    add_text(slide, "Что требуется перед автоматизацией", 0.8, 5.3, 4.5, 0.35, size=18, color=NAVY, bold=True)
    add_rich_lines(slide, [
        ("• ", "реальный волатильностный gate без знания будущего дня", RED),
        ("• ", "жёсткое закрытие на последнем доступном баре сессии", RED),
        ("• ", "roll-adjusted данные для фьючерсной истории", RED),
    ], 0.85, 5.72, 11.3, 1.05, size=14, gap=4)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); add_header(slide, "14 · Методика", "Определения и источники", 15)
    source_rows = [
        ("Результат ST 80/4", "test_result_legacy_20260828_133527.xlsx"),
        ("Список 222 дат", "vol data.xlsx"),
        ("Минутные OHLC", "C:\\3.1_wf_grid\\data.csv"),
        ("Сравнение 11 прогонов", "st_batch_vol_dates_intraday_results.xlsx"),
    ]
    add_table(slide, ["Источник", "Файл"], source_rows, 0.75, 1.55, 11.9, 2.1, widths=[2.3, 5.7], font_size=13, first_col_bold=True)
    add_rich_lines(slide, [
        ("Флип: ", "уникальная смена ST Color Before → ST Color After.", TEAL),
        ("Флип→флип: ", "абсолютное изменение цен закрытия двух соседних флипов.", TEAL),
        ("MAX–MIN: ", "полный High–Low внутри одного непрерывного цвета ST.", ORANGE),
        ("Волатильная неделя: ", "5 выбранных волатильных торговых дней; обычные дни исключены.", GREEN),
        ("Волатильный месяц: ", "21 выбранный волатильный торговый день.", GREEN),
    ], 0.8, 4.05, 11.7, 2.2, size=15, gap=6)
    add_callout(slide, "Все PnL-показатели рассчитаны по Net PnL %, комиссия учтена. Историческая статистика не является гарантией будущего результата.", 0.8, 6.42, 11.7, 0.48, fill=ORANGE_LIGHT)

    prs.save(OUT)
    print(f"Slides: {len(prs.slides)}")
    print(f"Saved: {OUT}")


if __name__ == "__main__":
    build()
