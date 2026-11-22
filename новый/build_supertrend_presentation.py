from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt


OUT = Path(r"C:\3.1_wf_grid\Supertrend_Si_statistics_2023-2026.pptx")

W = Inches(13.333)
H = Inches(7.5)

BG = RGBColor(10, 18, 30)
PANEL = RGBColor(20, 31, 47)
PANEL_2 = RGBColor(27, 41, 60)
WHITE = RGBColor(244, 247, 251)
MUTED = RGBColor(158, 174, 194)
GRID = RGBColor(56, 72, 94)
TEAL = RGBColor(45, 212, 191)
BLUE = RGBColor(83, 142, 255)
GREEN = RGBColor(62, 207, 142)
RED = RGBColor(255, 100, 112)
AMBER = RGBColor(249, 190, 72)


prs = Presentation()
prs.slide_width = W
prs.slide_height = H
blank = prs.slide_layouts[6]


def rect(slide, x, y, w, h, fill, radius=False, line=None):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(shape_type, x, y, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.color.rgb = line or fill
    if radius:
        try:
            sh.adjustments[0] = 0.08
        except Exception:
            pass
    return sh


def line(slide, x, y, w, h, color=GRID, width=1):
    sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = color
    sh.line.color.rgb = color
    sh.line.width = Pt(width)
    return sh


def text(slide, value, x, y, w, h, size=18, color=WHITE, bold=False,
         align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP, font="Aptos", margin=0.04,
         line_spacing=1.0):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.clear()
    tf.margin_left = Inches(margin)
    tf.margin_right = Inches(margin)
    tf.margin_top = Inches(margin)
    tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = line_spacing
    r = p.add_run()
    r.text = str(value)
    r.font.name = font
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    return box


def rich_text(slide, runs, x, y, w, h, size=18, align=PP_ALIGN.LEFT,
              valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.clear()
    tf.margin_left = tf.margin_right = Inches(0.04)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    for value, color, bold in runs:
        r = p.add_run()
        r.text = value
        r.font.name = "Aptos"
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
    return box


def base_slide(title, kicker, num):
    slide = prs.slides.add_slide(blank)
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = BG
    rect(slide, Inches(0), Inches(0), Inches(0.12), H, TEAL)
    text(slide, kicker.upper(), Inches(0.55), Inches(0.35), Inches(4.3), Inches(0.3),
         10, TEAL, True)
    text(slide, title, Inches(0.55), Inches(0.68), Inches(12.1), Inches(0.62),
         27, WHITE, True)
    line(slide, Inches(0.55), Inches(1.34), Inches(12.15), Inches(0.015), GRID)
    text(slide, "Si 1m · Supertrend ATR(80) × 4,0 · 20.11.2023–21.08.2026",
         Inches(0.55), Inches(7.12), Inches(9.0), Inches(0.22), 9, MUTED)
    text(slide, f"{num:02d}", Inches(12.0), Inches(7.05), Inches(0.7), Inches(0.3),
         11, MUTED, True, PP_ALIGN.RIGHT)
    return slide


def card(slide, x, y, w, h, value, label, accent=TEAL, sub=None):
    rect(slide, x, y, w, h, PANEL, True)
    line(slide, x, y, Inches(0.06), h, accent)
    text(slide, value, x + Inches(0.2), y + Inches(0.16), w - Inches(0.35), Inches(0.55),
         26, accent, True)
    text(slide, label, x + Inches(0.2), y + Inches(0.79), w - Inches(0.35), Inches(0.43),
         12, WHITE, True)
    if sub:
        text(slide, sub, x + Inches(0.2), y + Inches(1.18), w - Inches(0.35), h - Inches(1.28),
             10, MUTED)


def table(slide, x, y, w, h, headers, rows, widths=None, font_size=12,
          highlight_last=0):
    shape = slide.shapes.add_table(len(rows) + 1, len(headers), x, y, w, h)
    tbl = shape.table
    if widths:
        for i, frac in enumerate(widths):
            tbl.columns[i].width = int(w * frac)
    for j, header in enumerate(headers):
        cell = tbl.cell(0, j)
        cell.fill.solid(); cell.fill.fore_color.rgb = PANEL_2
        cell.margin_left = cell.margin_right = Inches(0.08)
        cell.margin_top = cell.margin_bottom = Inches(0.04)
        cell.text = header
        p = cell.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.CENTER
        for r in p.runs:
            r.font.name = "Aptos"; r.font.size = Pt(font_size - 1); r.font.bold = True; r.font.color.rgb = MUTED
    for i, row in enumerate(rows, 1):
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.fill.solid()
            is_hi = highlight_last and i > len(rows) - highlight_last
            cell.fill.fore_color.rgb = RGBColor(24, 43, 57) if is_hi else (PANEL if i % 2 else PANEL_2)
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            cell.text = str(val)
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.CENTER
            for r in p.runs:
                r.font.name = "Aptos"; r.font.size = Pt(font_size)
                r.font.bold = j == 0 or is_hi
                r.font.color.rgb = TEAL if is_hi and j in (0, 1, 3) else WHITE
    return tbl


def hbar(slide, label, value, max_value, x, y, w, color, suffix=""):
    text(slide, label, x, y, Inches(1.0), Inches(0.25), 11, WHITE, True)
    bar_x = x + Inches(1.0)
    rect(slide, bar_x, y + Inches(0.03), w, Inches(0.18), PANEL_2, True)
    fill_w = max(Inches(0.03), int(w * value / max_value))
    rect(slide, bar_x, y + Inches(0.03), fill_w, Inches(0.18), color, True)
    text(slide, f"{value:.2f}{suffix}", bar_x + w + Inches(0.12), y - Inches(0.01),
         Inches(0.95), Inches(0.27), 11, color, True, PP_ALIGN.RIGHT)


# 1 — Cover
slide = prs.slides.add_slide(blank)
slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
rect(slide, Inches(0), Inches(0), Inches(0.16), H, TEAL)
text(slide, "ИССЛЕДОВАНИЕ РЫНКА", Inches(0.72), Inches(0.72), Inches(4.0), Inches(0.3), 11, TEAL, True)
text(slide, "Движения цены\nв одном цвете Supertrend", Inches(0.72), Inches(1.35), Inches(8.2), Inches(1.6), 34, WHITE, True)
text(slide, "Два взгляда: флип → флип и полный MAX–MIN внутри цвета",
     Inches(0.75), Inches(3.15), Inches(8.7), Inches(0.45), 17, MUTED)
card(slide, Inches(0.75), Inches(4.15), Inches(2.55), Inches(1.65), "12 563", "движений между флипами", BLUE)
card(slide, Inches(3.48), Inches(4.15), Inches(2.55), Inches(1.65), "143,6", "календарной недели", TEAL)
card(slide, Inches(6.21), Inches(4.15), Inches(2.55), Inches(1.65), "11", "роллов скорректировано", AMBER)
rect(slide, Inches(9.48), Inches(0.9), Inches(3.15), Inches(5.7), PANEL, True)
text(slide, "0,141%", Inches(9.83), Inches(1.58), Inches(2.5), Inches(0.65), 31, BLUE, True, PP_ALIGN.CENTER)
text(slide, "среднее |флип → флип|", Inches(9.75), Inches(2.25), Inches(2.7), Inches(0.35), 12, MUTED, False, PP_ALIGN.CENTER)
line(slide, Inches(9.9), Inches(3.0), Inches(2.3), Inches(0.02), GRID)
text(slide, "0,291%", Inches(9.83), Inches(3.48), Inches(2.5), Inches(0.65), 31, TEAL, True, PP_ALIGN.CENTER)
text(slide, "средний MAX–MIN цвета", Inches(9.75), Inches(4.15), Inches(2.7), Inches(0.35), 12, MUTED, False, PP_ALIGN.CENTER)
text(slide, "Si · минутные данные\n20.11.2023 — 21.08.2026", Inches(9.78), Inches(5.15), Inches(2.65), Inches(0.75), 11, WHITE, True, PP_ALIGN.CENTER)
text(slide, "Аналитический материал · не является инвестиционной рекомендацией",
     Inches(0.75), Inches(7.05), Inches(11.8), Inches(0.25), 9, MUTED)


# 2 — Executive summary
slide = base_slide("Главное в четырёх цифрах", "01 · Резюме", 2)
card(slide, Inches(0.65), Inches(1.72), Inches(2.85), Inches(1.75), "0,1412%", "среднее движение между флипами", BLUE,
     "После коррекции роллов · медиана 0,0980%")
card(slide, Inches(3.7), Inches(1.72), Inches(2.85), Inches(1.75), "0,2907%", "средний MAX–MIN одного цвета", TEAL,
     "После коррекции роллов · медиана 0,2140%")
card(slide, Inches(6.75), Inches(1.72), Inches(2.85), Inches(1.75), "6,43 ATR", "медианный диапазон цвета", GREEN,
     "Среднее 8,59 ATR")
card(slide, Inches(9.8), Inches(1.72), Inches(2.85), Inches(1.75), "2,31 / нед.", "цветовых диапазонов >1%", AMBER,
     "331 случай · 97 из 144 недель")
rect(slide, Inches(0.65), Inches(3.88), Inches(12.0), Inches(2.55), PANEL, True)
text(slide, "Ключевой вывод", Inches(0.95), Inches(4.15), Inches(2.2), Inches(0.35), 13, TEAL, True)
rich_text(slide, [
    ("MAX–MIN почти вдвое больше", WHITE, True),
    (" движения между ценами соседних флипов: он учитывает весь внутрисегментный High–Low, включая откаты. ", MUTED, False),
    ("Поэтому эти две таблицы отвечают на разные вопросы", WHITE, True),
    (" и не должны смешиваться.", MUTED, False),
], Inches(0.95), Inches(4.62), Inches(11.35), Inches(0.8), 18)
text(slide, "Роллы заметно искажали отдельные экстремумы, но почти не изменили общую частоту порогов. После коррекции число MAX–MIN >1% снизилось с 334 до 331.",
     Inches(0.95), Inches(5.55), Inches(11.1), Inches(0.55), 13, MUTED)


# 3 — Method
slide = base_slide("Что именно измеряется", "02 · Методика", 3)
rect(slide, Inches(0.65), Inches(1.7), Inches(5.85), Inches(4.75), PANEL, True)
text(slide, "A · ФЛИП → ФЛИП", Inches(0.95), Inches(1.98), Inches(2.8), Inches(0.35), 13, BLUE, True)
text(slide, "Цена следующего флипа", Inches(1.15), Inches(2.72), Inches(2.15), Inches(0.3), 12, MUTED)
text(slide, "Цена текущего флипа", Inches(3.92), Inches(2.72), Inches(2.0), Inches(0.3), 12, MUTED, False, PP_ALIGN.RIGHT)
line(slide, Inches(1.25), Inches(3.55), Inches(4.55), Inches(0.04), GRID)
rect(slide, Inches(1.15), Inches(3.36), Inches(0.38), Inches(0.38), BLUE, True)
rect(slide, Inches(5.52), Inches(3.36), Inches(0.38), Inches(0.38), BLUE, True)
text(slide, "|P₂ / P₁ − 1| × 100%", Inches(1.4), Inches(4.12), Inches(4.4), Inches(0.5), 22, WHITE, True, PP_ALIGN.CENTER)
text(slide, "Показывает чистое смещение цены между точками смены цвета.", Inches(1.05), Inches(5.0), Inches(5.05), Inches(0.7), 13, MUTED, False, PP_ALIGN.CENTER)

rect(slide, Inches(6.78), Inches(1.7), Inches(5.85), Inches(4.75), PANEL, True)
text(slide, "B · MAX–MIN ВНУТРИ ЦВЕТА", Inches(7.08), Inches(1.98), Inches(3.6), Inches(0.35), 13, TEAL, True)
line(slide, Inches(7.35), Inches(3.55), Inches(4.5), Inches(0.04), GRID)
rect(slide, Inches(7.52), Inches(2.92), Inches(0.14), Inches(0.63), TEAL)
rect(slide, Inches(8.08), Inches(2.52), Inches(0.14), Inches(1.03), TEAL)
rect(slide, Inches(8.64), Inches(2.78), Inches(0.14), Inches(0.77), TEAL)
rect(slide, Inches(9.20), Inches(2.18), Inches(0.14), Inches(1.37), GREEN)
rect(slide, Inches(9.76), Inches(2.68), Inches(0.14), Inches(0.87), TEAL)
rect(slide, Inches(10.32), Inches(2.38), Inches(0.14), Inches(1.17), TEAL)
rect(slide, Inches(10.88), Inches(2.95), Inches(0.14), Inches(0.60), TEAL)
text(slide, "(MAX High − MIN Low) / P₁", Inches(7.15), Inches(4.12), Inches(5.1), Inches(0.5), 20, WHITE, True, PP_ALIGN.CENTER)
text(slide, "Показывает полный внутрисегментный размах, включая откаты и шпильки.", Inches(7.15), Inches(5.0), Inches(5.1), Inches(0.7), 13, MUTED, False, PP_ALIGN.CENTER)
text(slide, "Сегмент начинается на баре флипа и заканчивается перед следующим флипом. Последний незавершённый цвет исключён.",
     Inches(0.78), Inches(6.62), Inches(11.7), Inches(0.35), 11, MUTED, False, PP_ALIGN.CENTER)


# 4 — Flip table
slide = base_slide("Движение между соседними флипами", "03 · Флип → флип", 4)
text(slide, "12 563 завершённых движения после коррекции роллов · пороги накопительные", Inches(0.65), Inches(1.52), Inches(8.5), Inches(0.32), 13, MUTED)
headers = ["Движение", "Количество", "Доля", "В неделю", "Недель ≥1"]
rows = [
    ["> 0,3%", "1 151", "9,16%", "8,02", "141 / 144 · 97,9%"],
    ["> 0,5%", "428", "3,41%", "2,98", "126 / 144 · 87,5%"],
    ["> 0,75%", "174", "1,39%", "1,21", "82 / 144 · 56,9%"],
    ["> 1,0%", "81", "0,64%", "0,56", "47 / 144 · 32,6%"],
]
table(slide, Inches(0.65), Inches(2.0), Inches(12.0), Inches(2.65), headers, rows,
      [0.17, 0.17, 0.14, 0.17, 0.35], 15)
rect(slide, Inches(0.65), Inches(5.05), Inches(12.0), Inches(1.32), PANEL, True)
text(slide, "Как читать", Inches(0.92), Inches(5.28), Inches(1.4), Inches(0.3), 12, BLUE, True)
text(slide, "Движение >1% происходило в среднем 0,56 раза в неделю, но хотя бы один такой случай был только в 32,6% календарных недель — события кластеризуются.",
     Inches(2.22), Inches(5.19), Inches(9.95), Inches(0.72), 14, WHITE)
text(slide, "Важно: движение >1% также входит в строки >0,75%, >0,5% и >0,3%.", Inches(2.22), Inches(5.91), Inches(9.95), Inches(0.3), 10, MUTED)


# 5 — Practical frequency
slide = base_slide("Практическая частота между флипами", "04 · Флип → флип", 5)
rect(slide, Inches(0.65), Inches(1.7), Inches(7.7), Inches(4.8), PANEL, True)
text(slide, "СРЕДНЕЕ ЧИСЛО ДВИЖЕНИЙ В НЕДЕЛЮ", Inches(0.95), Inches(1.98), Inches(4.3), Inches(0.3), 12, MUTED, True)
hbar(slide, ">0,3%", 8.02, 8.02, Inches(1.0), Inches(2.65), Inches(5.2), BLUE)
hbar(slide, ">0,5%", 2.98, 8.02, Inches(1.0), Inches(3.35), Inches(5.2), TEAL)
hbar(slide, ">0,75%", 1.21, 8.02, Inches(1.0), Inches(4.05), Inches(5.2), GREEN)
hbar(slide, ">1,0%", 0.56, 8.02, Inches(1.0), Inches(4.75), Inches(5.2), AMBER)
text(slide, "Средняя частота ≠ равномерность. В спокойные недели крупных движений может не быть, а в волатильные — несколько подряд.",
     Inches(0.98), Inches(5.64), Inches(6.95), Inches(0.55), 12, MUTED)
rect(slide, Inches(8.65), Inches(1.7), Inches(4.0), Inches(4.8), PANEL, True)
text(slide, "ОПЕРАЦИОННЫЙ ОРИЕНТИР", Inches(8.95), Inches(1.98), Inches(3.35), Inches(0.3), 12, MUTED, True, PP_ALIGN.CENTER)
card(slide, Inches(9.05), Inches(2.55), Inches(3.2), Inches(1.35), "≈ 8×", ">0,3% в неделю", BLUE)
card(slide, Inches(9.05), Inches(4.12), Inches(3.2), Inches(1.35), "≈ 3×", ">0,5% в неделю", TEAL)
text(slide, ">0,75% — примерно раз в 0,83 недели\n>1% — примерно раз в 1,77 недели",
     Inches(8.98), Inches(5.72), Inches(3.4), Inches(0.6), 12, WHITE, True, PP_ALIGN.CENTER)


# 6 — MAX-MIN table
slide = base_slide("Полный MAX–MIN одного цвета", "05 · После коррекции роллов", 6)
text(slide, "12 563 сегмента после повторного расчёта ST на roll-adjusted HLC", Inches(0.65), Inches(1.5), Inches(8.5), Inches(0.32), 13, MUTED)
rows = [
    ["> 0,3%", "4 025", "32,04%", "28,03", "144 / 144 · 100%"],
    ["> 0,5%", "1 679", "13,36%", "11,69", "143 / 144 · 99,3%"],
    ["> 0,75%", "695", "5,53%", "4,84", "130 / 144 · 90,3%"],
    ["> 1,0%", "331", "2,63%", "2,31", "97 / 144 · 67,4%"],
    ["> 1,25%", "169", "1,35%", "1,18", "68 / 144 · 47,2%"],
    ["> 1,5%", "78", "0,62%", "0,54", "37 / 144 · 25,7%"],
]
table(slide, Inches(0.65), Inches(1.95), Inches(12.0), Inches(3.95), headers, rows,
      [0.17, 0.17, 0.14, 0.17, 0.35], 13, highlight_last=2)
text(slide, ">1,25%: примерно один случай каждые 0,85 недели    ·    >1,5%: примерно один случай каждые 1,84 недели",
     Inches(0.85), Inches(6.22), Inches(11.6), Inches(0.45), 14, WHITE, True, PP_ALIGN.CENTER)
text(slide, "MAX–MIN учитывает High/Low всего цвета; поэтому частота выше, чем у движения между ценами флипов.",
     Inches(0.85), Inches(6.66), Inches(11.6), Inches(0.3), 10, MUTED, False, PP_ALIGN.CENTER)


# 7 — Comparison
slide = base_slide("Почему MAX–MIN заметно больше", "06 · Сопоставление", 7)
rect(slide, Inches(0.65), Inches(1.68), Inches(8.0), Inches(4.88), PANEL, True)
text(slide, "СЛУЧАЕВ В НЕДЕЛЮ", Inches(0.95), Inches(1.96), Inches(2.8), Inches(0.3), 12, MUTED, True)
comparisons = [(" >0,3%", 8.02, 28.03), (" >0,5%", 2.98, 11.69), (" >0,75%", 1.21, 4.84), (" >1,0%", 0.56, 2.31)]
maxv = 28.03
for idx, (lab, flip, mm) in enumerate(comparisons):
    yy = 2.5 + idx * 0.9
    text(slide, lab, Inches(0.95), Inches(yy), Inches(1.0), Inches(0.3), 12, WHITE, True)
    rect(slide, Inches(2.0), Inches(yy + 0.02), Inches(5.6), Inches(0.16), PANEL_2, True)
    rect(slide, Inches(2.0), Inches(yy + 0.02), int(Inches(5.6) * flip / maxv), Inches(0.16), BLUE, True)
    text(slide, f"{flip:.2f}", Inches(7.65), Inches(yy - 0.05), Inches(0.65), Inches(0.25), 10, BLUE, True, PP_ALIGN.RIGHT)
    rect(slide, Inches(2.0), Inches(yy + 0.31), Inches(5.6), Inches(0.16), PANEL_2, True)
    rect(slide, Inches(2.0), Inches(yy + 0.31), int(Inches(5.6) * mm / maxv), Inches(0.16), TEAL, True)
    text(slide, f"{mm:.2f}", Inches(7.65), Inches(yy + 0.24), Inches(0.65), Inches(0.25), 10, TEAL, True, PP_ALIGN.RIGHT)
text(slide, "■ флип → флип     ■ MAX–MIN цвета", Inches(2.0), Inches(6.13), Inches(4.8), Inches(0.3), 11, WHITE, True)
rect(slide, Inches(8.93), Inches(1.68), Inches(3.72), Inches(4.88), PANEL, True)
text(slide, "Средний размер", Inches(9.25), Inches(2.05), Inches(3.05), Inches(0.35), 14, MUTED, True, PP_ALIGN.CENTER)
text(slide, "0,1412%", Inches(9.35), Inches(2.68), Inches(2.85), Inches(0.55), 28, BLUE, True, PP_ALIGN.CENTER)
text(slide, "флип → флип", Inches(9.35), Inches(3.19), Inches(2.85), Inches(0.3), 12, MUTED, False, PP_ALIGN.CENTER)
text(slide, "× 2,06", Inches(9.4), Inches(3.75), Inches(2.75), Inches(0.45), 20, WHITE, True, PP_ALIGN.CENTER)
text(slide, "0,2907%", Inches(9.35), Inches(4.58), Inches(2.85), Inches(0.55), 28, TEAL, True, PP_ALIGN.CENTER)
text(slide, "MAX–MIN цвета", Inches(9.35), Inches(5.09), Inches(2.85), Inches(0.3), 12, MUTED, False, PP_ALIGN.CENTER)


# 8 — ATR
slide = base_slide("Диапазон цвета в единицах ATR", "07 · Нормализация волатильностью", 8)
card(slide, Inches(0.65), Inches(1.7), Inches(2.65), Inches(1.55), "8,59 ATR", "среднее", TEAL)
card(slide, Inches(3.5), Inches(1.7), Inches(2.65), Inches(1.55), "6,43 ATR", "медиана", BLUE)
card(slide, Inches(6.35), Inches(1.7), Inches(2.65), Inches(1.55), "15,99 ATR", "90-й процентиль", AMBER)
card(slide, Inches(9.2), Inches(1.7), Inches(3.45), Inches(1.55), "8,51 / 8,67", "GREEN / RED · среднее ATR", GREEN)
headers_atr = ["Диапазон", "Количество", "Доля", "В неделю"]
rows_atr = [["> 4 ATR", "11 598", "92,32%", "80,77"], ["> 6 ATR", "6 853", "54,55%", "47,73"],
            ["> 8 ATR", "4 696", "37,38%", "32,71"], ["> 10 ATR", "3 352", "26,68%", "23,35"]]
table(slide, Inches(0.65), Inches(3.68), Inches(8.1), Inches(2.55), headers_atr, rows_atr,
      [0.24, 0.26, 0.22, 0.28], 14)
rect(slide, Inches(9.05), Inches(3.68), Inches(3.6), Inches(2.55), PANEL, True)
text(slide, "Формула", Inches(9.35), Inches(3.98), Inches(2.95), Inches(0.3), 12, TEAL, True, PP_ALIGN.CENTER)
text(slide, "MAX–MIN диапазон\n──────────────\nсредний ATR(80)\nвнутри сегмента",
     Inches(9.35), Inches(4.48), Inches(2.95), Inches(1.25), 17, WHITE, True, PP_ALIGN.CENTER)
text(slide, "ATR позволяет сравнивать участки разных режимов волатильности.", Inches(9.3), Inches(5.82), Inches(3.05), Inches(0.45), 10, MUTED, False, PP_ALIGN.CENTER)


# 9 — Daily clustering
slide = base_slide("Кластеризация движений >0,5% внутри дня", "08 · Флип → флип", 9)
text(slide, "Учитываются движения, которые начались и завершились в один календарный день", Inches(0.65), Inches(1.5), Inches(8.8), Inches(0.3), 13, MUTED)
headers_d = ["Событий за день", "Таких дней", "Дней в неделю", "Дней в месяц", "Периодичность"]
rows_d = [["≥ 2", "78", "0,543", "2,36", "≈ каждые 0,42 мес."],
          ["≥ 3", "20", "0,139", "0,61", "≈ каждые 1,65 мес."],
          ["≥ 4", "7", "0,049", "0,21", "≈ каждые 4,72 мес."]]
table(slide, Inches(0.65), Inches(2.0), Inches(12.0), Inches(2.35), headers_d, rows_d,
      [0.18, 0.17, 0.18, 0.18, 0.29], 15)
rect(slide, Inches(0.65), Inches(4.78), Inches(12.0), Inches(1.55), PANEL, True)
text(slide, "Распределение дней", Inches(0.95), Inches(5.05), Inches(2.2), Inches(0.3), 12, TEAL, True)
text(slide, "Ровно 2 движения: 58 дней   ·   ровно 3: 13 дней   ·   ровно 4: 4 дня   ·   5 движений: 2 дня   ·   6 движений: 1 день",
     Inches(0.95), Inches(5.53), Inches(11.15), Inches(0.35), 14, WHITE, True, PP_ALIGN.CENTER)
text(slide, "28 движений >0,5%, пересёкшие границу календарного дня, в этой таблице не учитывались.",
     Inches(0.95), Inches(6.03), Inches(11.15), Inches(0.3), 10, MUTED, False, PP_ALIGN.CENTER)


# 10 — Rollovers
slide = base_slide("Как устранено влияние роллов", "09 · Контроль качества", 10)
rect(slide, Inches(0.65), Inches(1.7), Inches(7.6), Inches(4.8), PANEL, True)
text(slide, "11 РОЛЛОВ В ПЕРИОДЕ", Inches(0.95), Inches(1.98), Inches(3.2), Inches(0.3), 12, AMBER, True)
dates_left = ["20.12.2023  Z3 → H4", "20.03.2024  H4 → M4", "19.06.2024  M4 → U4",
              "18.09.2024  U4 → Z4", "18.12.2024  Z4 → H5", "19.03.2025  H5 → M5"]
dates_right = ["18.06.2025  M5 → U5", "17.09.2025  U5 → Z5", "17.12.2025  Z5 → H6",
               "18.03.2026  H6 → M6", "18.06.2026  M6 → U6"]
for i, val in enumerate(dates_left):
    text(slide, val, Inches(1.0), Inches(2.5 + i * 0.55), Inches(3.15), Inches(0.28), 12, WHITE, i in (0, 5))
for i, val in enumerate(dates_right):
    text(slide, val, Inches(4.45), Inches(2.5 + i * 0.55), Inches(3.25), Inches(0.28), 12, WHITE, i in (0, 1, 2))
text(slide, "Источник: rollover_report.csv · все события подтверждены уровнем A0 по официальным свечам MOEX.",
     Inches(0.98), Inches(5.98), Inches(6.8), Inches(0.35), 10, MUTED)
rect(slide, Inches(8.55), Inches(1.7), Inches(4.1), Inches(4.8), PANEL, True)
text(slide, "Коррекция", Inches(8.88), Inches(2.0), Inches(3.42), Inches(0.35), 14, TEAL, True, PP_ALIGN.CENTER)
steps = [("1", "Применены межконтрактные смещения"), ("2", "Повторно рассчитаны ATR(80) и ST"),
         ("3", "MAX–MIN измерен на непрерывном HLC")]
for i, (n, label) in enumerate(steps):
    yy = 2.62 + i * 0.95
    rect(slide, Inches(8.98), Inches(yy), Inches(0.52), Inches(0.52), TEAL, True)
    text(slide, n, Inches(9.0), Inches(yy + 0.05), Inches(0.48), Inches(0.35), 14, BG, True, PP_ALIGN.CENTER)
    text(slide, label, Inches(9.72), Inches(yy - 0.02), Inches(2.45), Inches(0.62), 12, WHITE, True)
text(slide, "MAX–MIN >1%", Inches(8.95), Inches(5.53), Inches(1.6), Inches(0.3), 11, MUTED)
rich_text(slide, [("334", RED, True), ("  →  ", MUTED, False), ("331", GREEN, True)],
          Inches(10.55), Inches(5.46), Inches(1.45), Inches(0.4), 18, PP_ALIGN.CENTER)
text(slide, "Большие ролловые экстремумы исчезли; агрегаты изменились мало.", Inches(8.95), Inches(6.0), Inches(3.2), Inches(0.35), 10, MUTED, False, PP_ALIGN.CENTER)


# 11 — Conclusions and limitations
slide = base_slide("Что следует из статистики", "10 · Итоги", 11)
items = [
    ("1", "Типичный цвет короткий", "Медиана между флипами — 0,098%; медианный полный диапазон — 0,214%."),
    ("2", "Внутрисегментный размах важнее результата", "MAX–MIN в среднем в 2,06 раза больше смещения между флипами."),
    ("3", "Движения >1% не редкость", "MAX–MIN >1% — 331 случай, 2,31 раза в неделю и 67% календарных недель."),
    ("4", "Хвост кластеризуется", ">1,5% — 78 случаев, но события появились лишь в 37 из 144 недель."),
]
for i, (n, head, body) in enumerate(items):
    yy = 1.72 + i * 1.16
    rect(slide, Inches(0.68), Inches(yy), Inches(0.58), Inches(0.58), TEAL, True)
    text(slide, n, Inches(0.70), Inches(yy + 0.06), Inches(0.54), Inches(0.35), 15, BG, True, PP_ALIGN.CENTER)
    text(slide, head, Inches(1.55), Inches(yy - 0.01), Inches(3.35), Inches(0.32), 15, WHITE, True)
    text(slide, body, Inches(1.55), Inches(yy + 0.37), Inches(6.55), Inches(0.45), 12, MUTED)
rect(slide, Inches(8.55), Inches(1.72), Inches(4.1), Inches(4.64), PANEL, True)
text(slide, "Ограничения", Inches(8.9), Inches(2.04), Inches(3.4), Inches(0.35), 14, AMBER, True, PP_ALIGN.CENTER)
limitations = [
    "Историческая частота не является прогнозом.",
    "Пороги накопительные, а события не независимы.",
    "MAX–MIN чувствителен к ошибочным High/Low.",
    "Свеча 09.06.2025 11:29 содержит аномальный Low 73 406.",
    "Комиссии и исполнение здесь не анализируются.",
]
for i, val in enumerate(limitations):
    text(slide, "• " + val, Inches(8.95), Inches(2.68 + i * 0.58), Inches(3.05), Inches(0.47), 11, WHITE)
text(slide, "Источники: test_result_legacy_20260824_143531.xlsx · data.csv · rollover_report.csv",
     Inches(0.8), Inches(6.7), Inches(11.7), Inches(0.3), 9, MUTED, False, PP_ALIGN.CENTER)


prs.save(OUT)
print(OUT)
