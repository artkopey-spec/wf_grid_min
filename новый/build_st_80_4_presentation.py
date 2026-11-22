from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


OUTPUT = Path("ST_80_4_Si_key_findings.pptx")

BG = RGBColor(10, 18, 30)
PANEL = RGBColor(20, 32, 49)
PANEL_2 = RGBColor(27, 42, 62)
WHITE = RGBColor(242, 247, 252)
MUTED = RGBColor(154, 172, 193)
TEAL = RGBColor(40, 207, 181)
CYAN = RGBColor(65, 169, 255)
ORANGE = RGBColor(255, 176, 73)
RED = RGBColor(255, 103, 122)
GRID = RGBColor(53, 70, 91)


def add_rect(slide, x, y, w, h, fill, radius=True, line=None):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = line or fill
    return shape


def add_text(
    slide,
    text,
    x,
    y,
    w,
    h,
    size=20,
    color=WHITE,
    bold=False,
    align=PP_ALIGN.LEFT,
    valign=MSO_ANCHOR.TOP,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.margin_left = frame.margin_right = Inches(0.02)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    frame.vertical_anchor = valign
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def add_title(slide, title, subtitle=None):
    add_text(slide, title, 0.65, 0.38, 10.7, 0.55, 27, WHITE, True)
    add_text(slide, "ST 80 / 4", 11.15, 0.4, 1.45, 0.4, 14, TEAL, True, PP_ALIGN.RIGHT)
    add_rect(slide, 0.65, 1.03, 12.0, 0.025, GRID, False)
    if subtitle:
        add_text(slide, subtitle, 0.67, 1.12, 12.0, 0.34, 12, MUTED)


def add_footer(slide, number):
    add_text(slide, "Si · минутные данные · гэпы контрактов скорректированы", 0.68, 7.18, 8.5, 0.2, 9, MUTED)
    add_text(slide, str(number), 12.2, 7.16, 0.42, 0.22, 9, MUTED, align=PP_ALIGN.RIGHT)


def add_kpi(slide, x, y, w, value, label, accent=TEAL, note=None):
    add_rect(slide, x, y, w, 1.25, PANEL)
    add_rect(slide, x, y, 0.06, 1.25, accent, False)
    add_text(slide, value, x + 0.22, y + 0.16, w - 0.35, 0.45, 24, WHITE, True)
    add_text(slide, label, x + 0.22, y + 0.66, w - 0.35, 0.34, 11, MUTED)
    if note:
        add_text(slide, note, x + 0.22, y + 0.98, w - 0.35, 0.18, 8, accent)


def add_bar(slide, label, value_text, value, max_value, x, y, w, color=TEAL):
    add_text(slide, label, x, y, 1.85, 0.28, 12, WHITE, True)
    add_rect(slide, x + 1.9, y + 0.03, w - 3.0, 0.18, GRID, False)
    add_rect(slide, x + 1.9, y + 0.03, (w - 3.0) * value / max_value, 0.18, color, False)
    add_text(slide, value_text, x + w - 1.0, y - 0.02, 1.0, 0.28, 12, WHITE, True, PP_ALIGN.RIGHT)


def main() -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # Slide 1
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = BG
    add_rect(slide, 0, 0, 0.18, 7.5, TEAL, False)
    add_text(slide, "SUPER​TREND", 0.85, 1.06, 5.5, 0.4, 14, TEAL, True)
    add_text(slide, "Движения Si\nдля ST 80 / 4", 0.82, 1.55, 7.3, 1.55, 36, WHITE, True)
    add_text(
        slide,
        "Распределение, частота и время возникновения движений более 0,5%",
        0.86,
        3.3,
        6.8,
        0.85,
        18,
        MUTED,
    )
    add_rect(slide, 8.3, 1.25, 4.05, 4.75, PANEL)
    add_text(slide, "80", 8.75, 1.72, 1.35, 0.8, 42, TEAL, True, PP_ALIGN.CENTER)
    add_text(slide, "ATR period", 8.65, 2.52, 1.55, 0.3, 12, MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "4,0", 10.55, 1.72, 1.35, 0.8, 42, CYAN, True, PP_ALIGN.CENTER)
    add_text(slide, "multiplier", 10.48, 2.52, 1.52, 0.3, 12, MUTED, align=PP_ALIGN.CENTER)
    add_rect(slide, 8.72, 3.15, 3.2, 0.025, GRID, False)
    add_text(slide, "598 933", 8.72, 3.55, 3.2, 0.45, 25, WHITE, True, PP_ALIGN.CENTER)
    add_text(slide, "минутных баров", 8.72, 4.02, 3.2, 0.28, 12, MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "20.11.2023 — 21.08.2026", 8.72, 4.68, 3.2, 0.3, 12, WHITE, True, PP_ALIGN.CENTER)
    add_text(slide, "11 роллов скорректировано", 8.72, 5.17, 3.2, 0.26, 11, ORANGE, align=PP_ALIGN.CENTER)
    add_text(slide, "Краткий аналитический обзор", 0.86, 6.72, 5.2, 0.25, 11, MUTED)

    # Slide 2
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Что именно измерялось", "Фактическая амплитуда внутри каждого цветового участка SuperTrend")
    add_kpi(slide, 0.68, 1.65, 3.75, "12 537", "завершённых движений ST", TEAL)
    add_kpi(slide, 4.79, 1.65, 3.75, "0,1907%", "среднее движение всей выборки", CYAN)
    add_kpi(slide, 8.9, 1.65, 3.75, "47,76", "минутных баров до смены цвета", ORANGE)
    add_text(slide, "ЗЕЛЁНЫЙ ST", 0.9, 3.52, 2.15, 0.3, 13, TEAL, True)
    add_text(slide, "Close смены → максимальный High", 0.9, 3.9, 3.4, 0.35, 16, WHITE, True)
    add_text(slide, "КРАСНЫЙ ST", 4.8, 3.52, 2.15, 0.3, 13, RED, True)
    add_text(slide, "Close смены → минимальный Low", 4.8, 3.9, 3.4, 0.35, 16, WHITE, True)
    add_rect(slide, 8.75, 3.35, 3.9, 2.15, PANEL_2)
    add_text(slide, "Коррекция роллов", 9.02, 3.65, 3.35, 0.35, 17, WHITE, True)
    add_text(slide, "• убран межконтрактный спред\n• знаменатель — реальная цена старта\n• обычные движения не искажены offset", 9.02, 4.15, 3.25, 1.05, 12, MUTED)
    add_text(slide, "Процент = скорректированная амплитуда / цена начала движения", 0.9, 5.95, 8.2, 0.4, 15, WHITE, True)
    add_footer(slide, 2)

    # Slide 3
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Профиль движений ST", "Среднее после сортировки по скорректированной амплитуде")
    add_bar(slide, "Вся выборка", "0,1907%", 0.1907, 0.8, 0.85, 1.78, 11.4, CYAN)
    add_bar(slide, "Верхние 50%", "0,3400%", 0.3400, 0.8, 0.85, 2.48, 11.4, TEAL)
    add_bar(slide, "Верхние 30%", "0,4659%", 0.4659, 0.8, 0.85, 3.18, 11.4, ORANGE)
    add_bar(slide, "Верхние 10%", "0,7795%", 0.7795, 0.8, 0.85, 3.88, 11.4, RED)
    add_rect(slide, 0.85, 4.75, 11.65, 1.55, PANEL)
    add_text(slide, "Порог верхних 50%", 1.12, 5.03, 2.4, 0.25, 10, MUTED)
    add_text(slide, "0,1051%", 1.12, 5.38, 2.4, 0.38, 20, WHITE, True)
    add_text(slide, "Порог верхних 30%", 4.0, 5.03, 2.4, 0.25, 10, MUTED)
    add_text(slide, "0,2068%", 4.0, 5.38, 2.4, 0.38, 20, WHITE, True)
    add_text(slide, "Порог верхних 10%", 6.88, 5.03, 2.4, 0.25, 10, MUTED)
    add_text(slide, "0,4623%", 6.88, 5.38, 2.4, 0.38, 20, WHITE, True)
    add_text(slide, "Максимум", 9.76, 5.03, 2.1, 0.25, 10, MUTED)
    add_text(slide, "7,7397%", 9.76, 5.38, 2.1, 0.38, 20, TEAL, True)
    add_footer(slide, 3)

    # Slide 4
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Движения более 0,5%", "Распределение и частота на всей выборке ST 80 / 4")
    add_kpi(slide, 0.68, 1.55, 2.85, "1 087", "всего движений", TEAL)
    add_kpi(slide, 3.74, 1.55, 2.85, "0,8253%", "средний размер", CYAN)
    add_kpi(slide, 6.8, 1.55, 2.85, "1,56", "в среднем за день", ORANGE)
    add_kpi(slide, 9.86, 1.55, 2.8, "7,55", "в среднем за неделю", RED)
    add_text(slide, "РАСПРЕДЕЛЕНИЕ РАЗМЕРОВ", 0.82, 3.2, 4.0, 0.3, 12, MUTED, True)
    size_rows = [
        ("0,50–0,60%", 28.52, TEAL),
        ("0,60–0,75%", 27.87, CYAN),
        ("0,75–1,00%", 23.92, ORANGE),
        ("1,00–1,50%", 15.00, RED),
        ("> 1,50%", 4.69, MUTED),
    ]
    for idx, (label, value, color) in enumerate(size_rows):
        add_bar(slide, label, f"{value:.2f}%".replace(".", ","), value, 30, 0.82, 3.62 + idx * 0.49, 6.2, color)
    add_rect(slide, 7.55, 3.22, 5.1, 2.95, PANEL)
    add_text(slide, "ЧАСТОТА ПО ДНЯМ", 7.88, 3.52, 4.35, 0.3, 12, MUTED, True)
    add_text(slide, "72,45%", 7.88, 4.02, 1.65, 0.45, 24, WHITE, True)
    add_text(slide, "дней хотя бы с одним", 9.55, 4.08, 2.55, 0.3, 11, MUTED)
    add_text(slide, "20,52%", 7.88, 4.72, 1.65, 0.45, 24, CYAN, True)
    add_text(slide, "дней ровно с двумя", 9.55, 4.78, 2.55, 0.3, 11, MUTED)
    add_text(slide, "42,32%", 7.88, 5.42, 1.65, 0.45, 24, TEAL, True)
    add_text(slide, "дней с двумя или более", 9.55, 5.48, 2.65, 0.3, 11, MUTED)
    add_footer(slide, 4)

    # Slide 5
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Когда движение превышает 0,5%", "Время первого достижения порога; не время последующей смены цвета")
    add_rect(slide, 0.72, 1.52, 5.75, 2.05, PANEL)
    add_text(slide, "09:00–13:00", 1.05, 1.83, 2.4, 0.35, 17, WHITE, True)
    add_text(slide, "515", 1.02, 2.28, 1.3, 0.55, 30, TEAL, True)
    add_text(slide, "52,02% внутри окна 09–19", 2.25, 2.37, 3.4, 0.3, 12, MUTED)
    add_text(slide, "19,57 события / 100 торговых часов", 1.04, 3.03, 4.7, 0.28, 12, TEAL, True)
    add_rect(slide, 6.83, 1.52, 5.78, 2.05, PANEL)
    add_text(slide, "13:00–19:00", 7.15, 1.83, 2.4, 0.35, 17, WHITE, True)
    add_text(slide, "475", 7.13, 2.28, 1.3, 0.55, 30, CYAN, True)
    add_text(slide, "47,98% внутри окна 09–19", 8.36, 2.37, 3.4, 0.3, 12, MUTED)
    add_text(slide, "11,79 события / 100 торговых часов", 7.15, 3.03, 4.7, 0.28, 12, CYAN, True)
    add_text(slide, "ПО ДНЯМ НЕДЕЛИ · событий на торговый день", 0.82, 4.05, 5.5, 0.28, 12, MUTED, True)
    weekdays = [("Пн", 1.67), ("Вт", 1.43), ("Ср", 1.59), ("Чт", 1.52), ("Пт", 1.59)]
    for idx, (label, value) in enumerate(weekdays):
        x = 0.92 + idx * 1.32
        height = value / 1.7 * 1.25
        add_rect(slide, x, 6.18 - height, 0.72, height, TEAL if idx == 0 else CYAN, False)
        add_text(slide, f"{value:.2f}".replace(".", ","), x - 0.05, 4.72, 0.82, 0.25, 11, WHITE, True, PP_ALIGN.CENTER)
        add_text(slide, label, x, 6.25, 0.72, 0.25, 11, MUTED, True, PP_ALIGN.CENTER)
    add_rect(slide, 8.05, 4.15, 4.55, 2.25, PANEL_2)
    add_text(slide, "Главный вывод", 8.38, 4.47, 3.9, 0.32, 17, WHITE, True)
    add_text(slide, "Утро 09–13 — наиболее насыщенное окно: на час торговли движения >0,5% возникают примерно в 1,66 раза чаще.", 8.38, 5.02, 3.72, 1.05, 15, TEAL, True)
    add_footer(slide, 5)

    prs.save(OUTPUT)
    print(f"saved={OUTPUT.resolve()} slides={len(prs.slides)}")


if __name__ == "__main__":
    main()
