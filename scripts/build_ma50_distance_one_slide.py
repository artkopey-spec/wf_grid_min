from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


ROOT = Path(r"C:\3.1_wf_grid")
OUTPUT = (
    ROOT
    / "results_tester_st_batch_20260828"
    / "MA50_distance_222_volatile_days_cooldown_2h_one_slide.pptx"
)

NAVY = RGBColor(18, 35, 63)
NAVY_2 = RGBColor(30, 57, 92)
TEAL = RGBColor(0, 155, 162)
GREEN = RGBColor(46, 139, 87)
ORANGE = RGBColor(242, 140, 40)
RED = RGBColor(194, 63, 63)
PURPLE = RGBColor(116, 83, 166)
WHITE = RGBColor(255, 255, 255)
BG = RGBColor(245, 248, 251)
TEXT = RGBColor(35, 45, 58)
MUTED = RGBColor(101, 116, 133)
GRID = RGBColor(211, 220, 230)
BLUE_LIGHT = RGBColor(229, 237, 247)

ROWS = [
    ("±0,40%", "499", "2,25", "11,24", "99,1%", "ПРАКТИЧЕСКИ ЕЖЕДНЕВНО", TEAL),
    ("±0,50%", "342", "1,54", "7,70", "91,0%", "ОЧЕНЬ ЧАСТОЕ", GREEN),
    ("±0,60%", "239", "1,08", "5,38", "76,1%", "ЧАСТОЕ", ORANGE),
    ("±0,70%", "160", "0,72", "3,60", "55,4%", "УМЕРЕННО РЕДКОЕ", RED),
    ("±0,80%", "89", "0,40", "2,00", "33,3%", "РЕДКОЕ", PURPLE),
]


def text(slide, value, x, y, w, h, *, size=16, color=TEXT, bold=False, align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = shape.text_frame
    frame.clear()
    frame.margin_left = Inches(0.04); frame.margin_right = Inches(0.04)
    frame.margin_top = Inches(0.02); frame.margin_bottom = Inches(0.02)
    frame.vertical_anchor = valign
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run(); run.text = value
    run.font.name = "Aptos"; run.font.size = Pt(size); run.font.bold = bold; run.font.color.rgb = color
    return shape


def build() -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG

    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.12))
    band.fill.solid(); band.fill.fore_color.rgb = TEAL; band.line.fill.background()
    text(slide, "ОТКЛОНЕНИЕ ЦЕНЫ ОТ SMA(50)", 0.58, 0.32, 5.0, 0.25, size=10, color=TEAL, bold=True)
    text(slide, "Частота независимых сигналов на 222 волатильных днях", 0.58, 0.7, 11.8, 0.5, size=27, color=NAVY, bold=True)
    text(slide, "Si 1m · Close · все доступные часы · один сигнал не чаще раза в 120 минут", 0.6, 1.22, 10.5, 0.3, size=12, color=MUTED)

    headers = ["Уровень", "Сигналов", "На 1 день", "На 5 дней", "Дней с событием", "Классификация"]
    x_positions = [0.65, 2.0, 3.35, 4.8, 6.25, 8.3]
    widths = [1.2, 1.2, 1.3, 1.3, 1.85, 4.35]
    for x, width, header in zip(x_positions, widths, headers):
        shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(1.75), Inches(width), Inches(0.55))
        shape.fill.solid(); shape.fill.fore_color.rgb = NAVY_2; shape.line.fill.background()
        text(slide, header, x + 0.04, 1.79, width - 0.08, 0.45, size=11, color=WHITE, bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)

    for row_index, row in enumerate(ROWS):
        y = 2.38 + row_index * 0.67
        values = row[:5]
        category = row[5]
        category_color = row[6]
        for col, (x, width, value) in enumerate(zip(x_positions[:5], widths[:5], values)):
            cell = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(width), Inches(0.58))
            cell.fill.solid(); cell.fill.fore_color.rgb = WHITE if row_index % 2 == 0 else BLUE_LIGHT
            cell.line.color.rgb = GRID; cell.line.width = Pt(0.5)
            text(slide, value, x + 0.04, y + 0.04, width - 0.08, 0.48, size=14, color=NAVY, bold=col == 0, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
        badge = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x_positions[5] + 0.13), Inches(y + 0.08), Inches(widths[5] - 0.26), Inches(0.42))
        badge.fill.solid(); badge.fill.fore_color.rgb = category_color; badge.line.fill.background()
        text(slide, category, x_positions[5] + 0.2, y + 0.1, widths[5] - 0.4, 0.36, size=12, color=WHITE, bold=True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)

    callout = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.68), Inches(5.93), Inches(7.7), Inches(0.82))
    callout.fill.solid(); callout.fill.fore_color.rgb = BLUE_LIGHT; callout.line.fill.background()
    text(slide, "Главный вывод", 0.9, 6.05, 1.55, 0.25, size=13, color=TEAL, bold=True)
    text(slide, "Практический редкий сигнал начинается примерно с ±0,80%. Уровни 0,40–0,60% возникают слишком часто для самостоятельного события.", 2.35, 6.0, 5.75, 0.55, size=13, color=NAVY, bold=True, valign=MSO_ANCHOR.MIDDLE)

    note = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.65), Inches(5.93), Inches(4.0), Inches(0.82))
    note.fill.solid(); note.fill.fore_color.rgb = RGBColor(254, 239, 220); note.line.fill.background()
    text(slide, "Очень редких событий нет", 8.88, 6.02, 3.55, 0.25, size=13, color=RED, bold=True, align=PP_ALIGN.CENTER)
    text(slide, "Для категории «очень редкое» потребуется проверить уровни выше ±0,80%.", 8.9, 6.29, 3.5, 0.32, size=11, color=TEXT, align=PP_ALIGN.CENTER)

    text(slide, "Событие = первое crossover/crossunder после окончания общего cooldown 120 минут. Частота нормирована на 5 волатильных дней.", 0.68, 7.05, 11.8, 0.22, size=9, color=MUTED)
    prs.save(OUTPUT)
    print(f"Slides: {len(prs.slides)}")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    build()
