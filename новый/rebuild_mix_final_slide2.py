from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


SOURCE = Path(r"D:\АКЦИИ 24.08\минуты mix\ПРЕЗЕНТАЦИЯ СТРАТЕГИИ\MIX_ST_19_4_FINAL.pptx")
OUTPUT = Path("MIX_ST_19_4_FINAL.pptx")

BG = RGBColor(7, 20, 31)
PANEL = RGBColor(14, 37, 52)
PANEL_2 = RGBColor(18, 47, 63)
WHITE = RGBColor(245, 248, 250)
MUTED = RGBColor(157, 188, 205)
TEAL = RGBColor(39, 205, 190)
GREEN = RGBColor(128, 225, 102)
BLUE = RGBColor(100, 163, 255)
ORANGE = RGBColor(255, 178, 78)
RED = RGBColor(255, 104, 110)
GRID = RGBColor(45, 79, 95)


def rect(slide, x, y, w, h, color, rounded=True, line=None):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.color.rgb = line or color
    return shape


def text(slide, value, x, y, w, h, size=14, color=WHITE, bold=False,
         align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0.03)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    frame.vertical_anchor = valign
    p = frame.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = value
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def number_badge(slide, number, x, y, color):
    rect(slide, x, y, 0.42, 0.42, color)
    text(slide, number, x, y + 0.035, 0.42, 0.23, 12, BG, True, PP_ALIGN.CENTER)


def rule_row(slide, number, title, body, x, y, w, color, height=0.88):
    rect(slide, x, y, w, height, PANEL_2)
    number_badge(slide, number, x + 0.18, y + 0.20, color)
    text(slide, title, x + 0.76, y + 0.13, w - 0.96, 0.27, 13, WHITE, True)
    text(slide, body, x + 0.76, y + 0.43, w - 0.96, height - 0.51, 10, MUTED)


def action_row(slide, number, title, body, x, y, w, color, height):
    rect(slide, x, y, w, height, PANEL_2)
    rect(slide, x, y, 0.06, height, color, False)
    number_badge(slide, number, x + 0.20, y + 0.18, color)
    text(slide, title, x + 0.78, y + 0.13, w - 1.00, 0.28, 13, WHITE, True)
    text(slide, body, x + 0.78, y + 0.43, w - 1.00, height - 0.50, 10, MUTED)


def delete_shape(shape):
    element = shape._element
    element.getparent().remove(element)


def replace_existing_text(shape, value, size, color, bold=False):
    frame = shape.text_frame
    frame.clear()
    p = frame.paragraphs[0]
    run = p.add_run()
    run.text = value
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def main():
    prs = Presentation(SOURCE)
    slide = prs.slides[1]

    # Keep the deck's background strip, header, divider, subtitle and footer (shapes 0–6).
    for shape in list(slide.shapes)[7:]:
        delete_shape(shape)

    # Refresh the header copy while preserving its original styling.
    replace_existing_text(slide.shapes[1], "Алгоритм стратегии: шаг за шагом", 27, WHITE, True)
    replace_existing_text(slide.shapes[4], "Слева — обязательные фильтры сигнала; справа — точное исполнение и порядок выходов", 11, MUTED)

    # Left section: signal qualification.
    rect(slide, 0.70, 1.50, 4.02, 5.23, PANEL)
    text(slide, "A · СИГНАЛ ДОПУЩЕН", 1.00, 1.78, 2.75, 0.26, 12, TEAL, True)
    text(slide, "Все три условия должны выполняться одновременно", 1.00, 2.10, 3.35, 0.26, 10, MUTED)
    rule_row(slide, "1", "Первый зелёный импульс дня",
             "Берём только первый завершённый GREEN-импульс SuperTrend 19/4 текущего торгового дня.",
             0.96, 2.52, 3.50, TEAL, 0.94)
    rule_row(slide, "2", "Размер строго > 0,7% и < 2,5%",
             "0,7% — минимальная сила сигнала; 2,5% — верхняя граница, выше которой начинается отдельный сценарий кульминации.",
             0.96, 3.62, 3.50, ORANGE, 1.06)
    rule_row(slide, "3", "Импульс завершился выше Open дня",
             "Close конечной минуты импульса должен быть выше скорректированной цены открытия этого торгового дня.",
             0.96, 4.84, 3.50, GREEN, 1.03)
    rect(slide, 0.96, 6.08, 3.50, 0.40, RED)
    text(slide, "Любое условие не выполнено → сделки нет", 1.10, 6.17, 3.20, 0.20,
         10, BG, True, PP_ALIGN.CENTER)

    # Right section: execution and position management.
    rect(slide, 4.94, 1.50, 7.68, 5.23, PANEL)
    text(slide, "B · ИСПОЛНЕНИЕ И СОПРОВОЖДЕНИЕ", 5.24, 1.78, 4.40, 0.26, 12, BLUE, True)
    text(slide, "Время отсчитывается от первого Long10", 9.53, 1.80, 2.72, 0.23, 9, MUTED,
         align=PP_ALIGN.RIGHT)

    action_row(slide, "4", "Коррекция −0,4% → первый Long10",
               "Ждём снижения на 0,4% от максимума инициирующего импульса. Покупаем 10 контрактов на этом уровне только если исполнение произошло строго до 15:00; в 15:00 или позже сигнал пропускаем.",
               5.20, 2.20, 7.16, BLUE, 1.04)
    action_row(slide, "5", "Подтверждённый пробой → добавить ещё 10",
               "Добавление разрешено только при RVol импульса ≤ 2,923×. После закрытия минутного бара выше максимума импульса покупаем +10 по Open следующей минуты. Добавление после 15:00 разрешено.",
               5.20, 3.39, 7.16, ORANGE, 1.07)
    action_row(slide, "6", "Стоп −1,5% от средней цены позиции",
               "Стоп действует сразу после входа. После добавления средняя цена и стоп пересчитываются. Если рынок перескочил стоп, исполнение учитывается по Open минутного бара.",
               5.20, 4.61, 7.16, RED, 0.93)
    action_row(slide, "7", "Два плановых контроля выхода",
               "Через 30 минут: если Close ≤ текущей средней цены — закрыть всю позицию; если выше — продолжать держать. Затем на первой минуте новой сессии: сначала действует стоп на Open и Low, после чего остаток закрывается по Close этой минуты.",
               5.20, 5.68, 7.16, GREEN, 1.00)

    prs.save(OUTPUT)
    print(f"saved {OUTPUT.resolve()} · slides={len(prs.slides)}")


if __name__ == "__main__":
    main()
