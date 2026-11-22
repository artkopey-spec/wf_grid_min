from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt


ROOT = Path.cwd()
BASE = ROOT / "mix_add5_output" / "MIX_ST_19_4_FINAL_AUDITED_LONG10_ADD5.pptx"
OUT_DIR = ROOT / "mix_short_impulse_volume_test"
OUT = OUT_DIR / "MIX_ST_19_4_FINAL_AUDITED_SHORT_IMPULSE_VOLUME_RULE.pptx"

BG, PANEL, GRID = "0B2230", "102C3B", "274857"
TEXT, MUTED = "EAF2F5", "A9BCC5"
TEAL, ORANGE, RED, BLUE = "27D3C2", "FFB257", "FF7373", "69A7FF"


def rgb(value):
    return RGBColor.from_string(value)


def box(slide, x, y, w, h, fill, radius=True, line=None):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line or fill)
    return shape


def text(slide, value, x, y, w, h, size=12, color=TEXT, bold=False, align=PP_ALIGN.LEFT):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = value
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = rgb(color)
    return shape


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prs = Presentation(BASE)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background = slide.background.fill
    background.solid()
    background.fore_color.rgb = rgb(BG)

    text(slide, "Длительность уточняет смысл RVol", 0.62, 0.34, 9.8, 0.42, 23, TEXT, True)
    text(slide, "Порог заморожен только на train: медиана 104 минуты → короткий импульс <52 минут", 0.62, 0.82, 11.7, 0.28, 11, MUTED)

    cards = [
        ("104 мин", "медиана train", TEAL),
        ("<52 мин", "исключение RVol", ORANGE),
        ("19", "коротких сделок", BLUE),
        ("8", "новых add при известном RVol", TEAL),
    ]
    for index, (value, label, color) in enumerate(cards):
        x = 0.62 + index * 3.05
        box(slide, x, 1.27, 2.78, 1.00, PANEL, line=GRID)
        text(slide, value, x + 0.16, 1.46, 2.46, 0.34, 20, color, True, PP_ALIGN.CENTER)
        text(slide, label, x + 0.12, 1.88, 2.54, 0.20, 9.5, MUTED, False, PP_ALIGN.CENTER)

    text(slide, "Long10 + 5", 0.72, 2.57, 2.4, 0.30, 15, TEXT, True)
    text(slide, "Текущий", 4.28, 2.58, 1.35, 0.24, 10, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "Исключение", 6.05, 2.58, 1.45, 0.24, 10, TEAL, True, PP_ALIGN.CENTER)
    text(slide, "Изменение", 7.92, 2.58, 1.45, 0.24, 10, MUTED, True, PP_ALIGN.CENTER)
    rows = [
        ("Сумма результата", "75,776 п.п.", "76,973 п.п.", "+1,197"),
        ("Средняя сделка", "0,611%", "0,621%", "+0,010 п.п."),
        ("Profit Factor", "3,788", "3,700", "−0,088"),
        ("Max DD", "−3,627 п.п.", "−4,316 п.п.", "хуже на 0,689"),
        ("OOS сумма", "24,241 п.п.", "25,112 п.п.", "+0,870"),
        ("OOS PF", "3,793", "3,687", "−0,106"),
    ]
    for i, row in enumerate(rows):
        y = 2.95 + i * 0.48
        if i % 2 == 0:
            box(slide, 0.62, y - 0.06, 8.95, 0.43, PANEL, radius=False)
        text(slide, row[0], 0.77, y, 3.15, 0.22, 10.5, TEXT, i in (0, 4))
        text(slide, row[1], 4.05, y, 1.80, 0.22, 10.5, MUTED, False, PP_ALIGN.CENTER)
        text(slide, row[2], 5.83, y, 1.90, 0.22, 10.5, TEAL, True, PP_ALIGN.CENTER)
        change_color = TEAL if row[3].startswith("+") else ORANGE
        text(slide, row[3], 7.72, y, 1.80, 0.22, 10.5, change_color, True, PP_ALIGN.CENTER)

    box(slide, 9.82, 2.57, 2.88, 2.40, PANEL, line=GRID)
    text(slide, "КРИТИЧЕСКОЕ УТОЧНЕНИЕ", 10.04, 2.82, 2.43, 0.23, 10, ORANGE, True, PP_ALIGN.CENTER)
    text(slide, "Если RVol отсутствует, добавление по исключению не разрешать.", 10.08, 3.25, 2.35, 0.58, 13, TEXT, True, PP_ALIGN.CENTER)
    text(slide, "Единственная такая OOS-сделка снизила результат на 3,575 п.п. и ухудшила DD до −6,598.", 10.10, 4.02, 2.31, 0.62, 10, MUTED, False, PP_ALIGN.CENTER)

    box(slide, 0.62, 6.06, 12.08, 0.86, TEAL, line=TEAL)
    text(slide, "Решение: использовать медиану train и правило bars <52 только при известном RVol. Прибыль немного выше, но PF и DD хуже — правило логично, статистическое подтверждение пока слабое: всего 8 изменённых сделок, из них 5 OOS.", 0.88, 6.26, 11.55, 0.43, 12, BG, True, PP_ALIGN.CENTER)

    prs.save(OUT)
    print(OUT)
    print("slides", len(prs.slides))


if __name__ == "__main__":
    main()
