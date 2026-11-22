from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


SOURCE = Path("MIX_ST_19_4_climax_SHORT_trailing_takeprofit_stoploss.xlsx")
OUTPUT = Path("MIX_ST_19_4_climax_SHORT_risk_controls.pptx")

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


def add_text(slide, value, x, y, w, h, size=16, color=WHITE, bold=False,
             align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0.04)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    frame.vertical_anchor = valign
    p = frame.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = str(value)
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def add_rect(slide, x, y, w, h, color, rounded=True, line=None):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.color.rgb = line or color
    return shape


def set_bg(slide):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = BG


def footer(slide, number):
    add_text(slide, "MIX · минутные данные · межконтрактные гэпы скорректированы",
             0.68, 7.15, 8.0, 0.20, 9, MUTED)
    add_text(slide, str(number), 12.15, 7.15, 0.46, 0.20, 9, MUTED,
             align=PP_ALIGN.RIGHT)


def header(slide, title, subtitle, number):
    add_text(slide, title, 0.68, 0.34, 10.8, 0.52, 27, WHITE, True)
    add_text(slide, "MIX · ST 19/4 · SHORT", 10.65, 0.39, 1.95, 0.28, 11,
             TEAL, True, PP_ALIGN.RIGHT)
    add_rect(slide, 0.68, 0.98, 11.94, 0.02, GRID, False)
    add_text(slide, subtitle, 0.70, 1.08, 11.65, 0.38, 11, MUTED)
    footer(slide, number)


def kpi(slide, x, y, w, value, label, accent=TEAL, note=None):
    add_rect(slide, x, y, w, 1.22, PANEL)
    add_rect(slide, x, y, 0.06, 1.22, accent, False)
    add_text(slide, value, x + 0.23, y + 0.13, w - 0.36, 0.43, 23, WHITE, True)
    add_text(slide, label, x + 0.23, y + 0.61, w - 0.36, 0.28, 10, MUTED)
    if note:
        add_text(slide, note, x + 0.23, y + 0.93, w - 0.36, 0.18, 8, accent)


def card(slide, x, y, w, h, title, body, accent=TEAL, number=None):
    add_rect(slide, x, y, w, h, PANEL)
    if number is not None:
        add_rect(slide, x + 0.20, y + 0.20, 0.40, 0.40, accent)
        add_text(slide, number, x + 0.20, y + 0.23, 0.40, 0.23, 12, BG, True,
                 PP_ALIGN.CENTER)
        title_x = x + 0.74
    else:
        add_rect(slide, x + 0.20, y + 0.23, 0.05, h - 0.46, accent, False)
        title_x = x + 0.39
    add_text(slide, title, title_x, y + 0.18, w - (title_x - x) - 0.18,
             0.34, 15, WHITE, True)
    add_text(slide, body, x + 0.23, y + 0.68, w - 0.46, h - 0.82,
             10, MUTED)


def bar(slide, label, value, max_value, x, y, w, color, value_text):
    add_text(slide, label, x, y - 0.03, 1.10, 0.25, 10, WHITE, True)
    add_rect(slide, x + 1.10, y + 0.03, w - 2.15, 0.16, GRID, False)
    usable = w - 2.15
    width = max(0.02, usable * max(0, value) / max_value)
    add_rect(slide, x + 1.10, y + 0.03, width, 0.16, color, False)
    add_text(slide, value_text, x + w - 1.00, y - 0.04, 1.00, 0.26, 9,
             color, True, PP_ALIGN.RIGHT)


def result(summary, mechanism, primary=None, secondary=None, universe="below_open_4"):
    rows = summary[(summary["universe"] == universe) &
                   (summary["mechanism"] == mechanism) &
                   (summary["fallback_minutes"] == 60)]
    if primary is not None:
        rows = rows[rows["primary_pct"] == primary]
    if secondary is not None:
        rows = rows[rows["secondary_trail_pct"] == secondary]
    if len(rows) != 1:
        raise ValueError((mechanism, primary, secondary, len(rows)))
    return rows.iloc[0]


def main():
    summary = pd.read_excel(SOURCE, sheet_name="Summary_All")
    base = result(summary, "time_only")
    tp15 = result(summary, "take_profit", 1.5)
    stop07 = result(summary, "stop_loss", 0.7)
    trailing = result(summary, "profit_activated_trailing", 1.5, 0.1)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1 — cover
    slide = prs.slides.add_slide(blank)
    set_bg(slide)
    add_rect(slide, 0, 0, 0.18, 7.5, TEAL, False)
    add_text(slide, "ПОБОЧНАЯ СТРАТЕГИЯ", 0.82, 0.84, 5.2, 0.32, 13, TEAL, True)
    add_text(slide, "Short после кульминации", 0.80, 1.34, 7.1, 0.65, 34, WHITE, True)
    add_text(slide, "Импульс MIX ≥ 2,5% → красный флип ST → короткая позиция",
             0.82, 2.15, 7.0, 0.68, 18, MUTED)
    add_rect(slide, 0.82, 3.18, 6.62, 1.48, PANEL)
    add_text(slide, "≥2,5%", 1.12, 3.54, 1.30, 0.42, 25, GREEN, True)
    add_text(slide, "импульс", 1.12, 4.02, 1.30, 0.22, 10, MUTED)
    add_text(slide, "RED flip", 3.20, 3.54, 1.55, 0.42, 25, RED, True)
    add_text(slide, "подтверждён Close", 3.20, 4.02, 1.65, 0.22, 10, MUTED)
    add_text(slide, "Short 10", 5.46, 3.54, 1.50, 0.42, 25, BLUE, True)
    add_text(slide, "следующий Open", 5.46, 4.02, 1.50, 0.22, 10, MUTED)
    add_rect(slide, 8.20, 1.12, 4.18, 4.95, PANEL_2)
    add_text(slide, "+1,092%", 8.55, 1.76, 3.50, 0.66, 37, TEAL, True,
             PP_ALIGN.CENTER)
    add_text(slide, "средняя сделка", 8.55, 2.44, 3.50, 0.28, 13, MUTED,
             align=PP_ALIGN.CENTER)
    add_rect(slide, 8.68, 3.02, 3.15, 0.02, GRID, False)
    add_text(slide, "+4,369%", 8.55, 3.40, 3.50, 0.48, 26, WHITE, True,
             PP_ALIGN.CENTER)
    add_text(slide, "сумма результатов", 8.55, 3.88, 3.50, 0.28, 12, MUTED,
             align=PP_ALIGN.CENTER)
    add_text(slide, "TP 1,5% · иначе выход 60 минут", 8.48, 4.55, 3.64, 0.60,
             15, ORANGE, True, PP_ALIGN.CENTER)
    add_text(slide, "Исследовательская версия · только 4 сделки", 8.48, 5.36,
             3.64, 0.32, 11, RED, True, PP_ALIGN.CENTER)
    add_text(slide, "Комиссия включена · проскальзывание не включено",
             0.82, 6.78, 6.0, 0.24, 10, MUTED)

    # 2 — rules and geometry
    slide = prs.slides.add_slide(blank)
    set_bg(slide)
    header(slide, "Правила входа", "Геометрия оказалась важнее самого размера кульминационного импульса", 2)
    rules = [
        ("Кульминация", "Первый зелёный импульс ST 19/4 за день размером не меньше 2,5%.", GREEN),
        ("Фильтр Open", "Конец импульса должен быть на уровне или ниже скорректированной цены открытия дня.", ORANGE),
        ("Подтверждение", "Ждём закрытия первой красной минуты ST. До закрытия флип неизвестен.", RED),
        ("Исполнение", "Открываем Short 10 MIX по Open следующей минутной свечи.", BLUE),
    ]
    for i, (ttl, body, accent) in enumerate(rules):
        card(slide, 0.70 + i * 3.03, 1.56, 2.77, 1.80, ttl, body, accent, str(i + 1))
    add_rect(slide, 0.70, 3.70, 11.92, 2.60, PANEL)
    add_text(slide, "ПОЧЕМУ НУЖЕН ФИЛЬТР ОТНОСИТЕЛЬНО OPEN", 0.98, 3.98, 4.8, 0.30,
             12, MUTED, True)
    kpi(slide, 1.00, 4.48, 3.10, "12 сделок", "импульс завершился выше Open", RED)
    add_text(slide, "−0,920%", 1.28, 5.87, 2.55, 0.28, 16, RED, True,
             PP_ALIGN.CENTER)
    add_text(slide, "сумма при выходе через 1 час", 1.10, 6.15, 2.90, 0.20, 9,
             MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "→", 4.47, 4.84, 0.60, 0.50, 27, MUTED, True, PP_ALIGN.CENTER)
    kpi(slide, 5.35, 4.48, 3.10, "4 сделки", "конец импульса ≤ Open", TEAL)
    add_text(slide, "+2,952%", 5.63, 5.87, 2.55, 0.28, 16, TEAL, True,
             PP_ALIGN.CENTER)
    add_text(slide, "сумма при выходе через 1 час", 5.45, 6.15, 2.90, 0.20, 9,
             MUTED, align=PP_ALIGN.CENTER)
    add_rect(slide, 9.00, 4.48, 3.05, 1.48, PANEL_2)
    add_text(slide, "Вывод", 9.27, 4.72, 2.50, 0.30, 15, WHITE, True)
    add_text(slide, "Short имеет смысл только после импульса, который не смог вернуть цену выше открытия дня.",
             9.27, 5.12, 2.48, 0.68, 11, TEAL, True)

    # 3 — take profit
    slide = prs.slides.add_slide(blank)
    set_bg(slide)
    header(slide, "Тейк-профит", "Все уровни проверены отдельно; если тейк не достигнут — закрытие через 60 минут", 3)
    add_rect(slide, 0.70, 1.54, 7.25, 4.95, PANEL)
    add_text(slide, "СУММА РЕЗУЛЬТАТОВ ЧЕТЫРЁХ СДЕЛОК", 0.98, 1.85, 4.2, 0.28,
             12, MUTED, True)
    tp_data = [("TP 0,5%", 1.369, BLUE), ("TP 1,0%", 2.869, BLUE),
               ("TP 1,2%", 3.469, TEAL), ("TP 1,5%", 4.369, GREEN),
               ("TP 1,75%", 2.952, ORANGE)]
    for i, (label, value, color) in enumerate(tp_data):
        bar(slide, label, value, 4.369, 1.00, 2.42 + i * 0.70, 6.45,
            color, f"+{value:.3f}%".replace(".", ","))
    add_rect(slide, 8.22, 1.54, 4.40, 2.26, PANEL_2)
    add_text(slide, "ЛУЧШИЙ ТЕСТ", 8.55, 1.88, 2.8, 0.24, 11, MUTED, True)
    add_text(slide, "TP 1,5%", 8.54, 2.30, 3.20, 0.48, 27, GREEN, True)
    add_text(slide, "+1,092% средняя · PF 31,87\n+4,369% сумма · Max DD −0,091%",
             8.54, 2.87, 3.42, 0.62, 12, WHITE, True)
    add_rect(slide, 8.22, 4.05, 4.40, 2.44, PANEL_2)
    add_text(slide, "ОГРАНИЧЕНИЕ", 8.55, 4.38, 2.8, 0.24, 11, RED, True)
    add_text(slide, "Три прибыльные сделки достигли 1,5%, но ни одна не достигла 1,75%.",
             8.54, 4.82, 3.45, 0.67, 15, WHITE, True)
    add_text(slide, "Поэтому 1,5% — перспективный кандидат, а не доказанный устойчивый оптимум.",
             8.54, 5.58, 3.45, 0.58, 11, ORANGE, True)

    # 4 — stop and trailing
    slide = prs.slides.add_slide(blank)
    set_bg(slide)
    header(slide, "Стоп и трейлинг", "Близкие защитные правила конфликтуют с неровной геометрией движения", 4)
    add_rect(slide, 0.70, 1.54, 5.80, 4.95, PANEL)
    add_text(slide, "ФИКСИРОВАННЫЙ СТОП", 1.00, 1.86, 3.0, 0.27, 12, MUTED, True)
    stop_data = [("0,2%", -0.853), ("0,3%", 0.424), ("0,4%", 0.124),
                 ("0,5%", 1.332), ("0,7%", 2.952)]
    for i, (label, value) in enumerate(stop_data):
        color = TEAL if value >= 2.9 else (ORANGE if value > 0 else RED)
        add_text(slide, label, 1.03, 2.38 + i * 0.55, 0.75, 0.24, 11, WHITE, True)
        add_text(slide, ("+" if value >= 0 else "") + f"{value:.3f}%".replace(".", ","),
                 2.03, 2.38 + i * 0.55, 1.25, 0.24, 11, color, True)
    add_rect(slide, 3.54, 2.34, 2.48, 2.90, PANEL_2)
    add_text(slide, "Стоп 0,7%", 3.80, 2.67, 1.95, 0.30, 16, TEAL, True)
    add_text(slide, "не сработал ни в одной из четырёх выбранных сделок",
             3.80, 3.15, 1.88, 0.82, 11, WHITE, True)
    add_text(slide, "Результат совпал с обычным выходом через 60 минут. Это только аварийная защита.",
             3.80, 4.20, 1.88, 0.76, 10, MUTED)
    add_rect(slide, 6.78, 1.54, 5.84, 4.95, PANEL)
    add_text(slide, "ТРЕЙЛИНГ", 7.08, 1.86, 3.0, 0.27, 12, MUTED, True)
    kpi(slide, 7.08, 2.32, 2.30, "+2,952%", "без трейлинга · 60 минут", TEAL)
    kpi(slide, 9.72, 2.32, 2.30, "+0,201%", "обычный трейлинг 0,5%", RED)
    add_rect(slide, 7.08, 3.92, 4.94, 1.12, PANEL_2)
    add_text(slide, "После прибыли 1,5% → трейлинг 0,1%", 7.37, 4.17, 4.30, 0.30,
             15, WHITE, True)
    add_text(slide, "+3,995% · уступает простому TP 1,5% (+4,369%)",
             7.37, 4.57, 4.30, 0.26, 11, ORANGE, True)
    add_text(slide, "Вывод: обычный трейлинг закрывает позицию на рыночном шуме. Активируемый трейлинг работает, но добавляет лишний параметр без преимущества.",
             7.10, 5.37, 4.88, 0.72, 12, MUTED)

    # 5 — decision
    slide = prs.slides.add_slide(blank)
    set_bg(slide)
    header(slide, "Рабочее решение", "Простая версия лучше соответствует объёму имеющейся статистики", 5)
    add_rect(slide, 0.70, 1.54, 7.45, 4.95, PANEL)
    add_text(slide, "ПРЕДВАРИТЕЛЬНЫЙ АЛГОРИТМ", 1.00, 1.86, 4.4, 0.28, 12, MUTED, True)
    decisions = [
        ("1", "Первый зелёный импульс дня ≥ 2,5%; конец импульса ≤ Open дня.", GREEN),
        ("2", "После закрытия красной минуты ST открыть Short 10 на следующем Open.", RED),
        ("3", "Выставить тейк-профит 1,5% от цены входа.", TEAL),
        ("4", "Если тейк не исполнен — закрыть по Close через 60 минут.", BLUE),
        ("5", "Стоп 0,7% использовать только как аварийную защиту; трейлинг не применять.", ORANGE),
    ]
    for i, (num, body, color) in enumerate(decisions):
        y = 2.35 + i * 0.72
        add_rect(slide, 1.00, y, 0.40, 0.40, color)
        add_text(slide, num, 1.00, y + 0.04, 0.40, 0.22, 12, BG, True, PP_ALIGN.CENTER)
        add_text(slide, body, 1.62, y - 0.01, 5.96, 0.52, 12, WHITE, i in (2, 3))
    add_rect(slide, 8.42, 1.54, 4.20, 2.38, PANEL_2)
    add_text(slide, "ИТОГ ТЕСТА", 8.74, 1.86, 2.4, 0.24, 11, MUTED, True)
    add_text(slide, "+4,369%", 8.72, 2.28, 3.40, 0.52, 29, TEAL, True)
    add_text(slide, "сумма четырёх результатов", 8.74, 2.82, 3.15, 0.28, 11, MUTED)
    add_text(slide, "75% прибыльных · PF 31,87", 8.74, 3.32, 3.10, 0.28, 13, WHITE, True)
    add_rect(slide, 8.42, 4.18, 4.20, 2.31, PANEL_2)
    add_text(slide, "СТАТУС: ИССЛЕДОВАНИЕ", 8.74, 4.50, 3.2, 0.25, 11, RED, True)
    add_text(slide, "Всего 4 сделки: 2 на старом периоде и 2 OOS. Параметр TP 1,5% нельзя считать подтверждённым до накопления новых сигналов.",
             8.73, 4.94, 3.36, 0.92, 13, WHITE, True)
    add_text(slide, "Проценты — сумма результатов сделок, без сложного реинвестирования.",
             8.73, 6.00, 3.40, 0.26, 9, MUTED)

    prs.save(OUTPUT)
    print(f"saved {OUTPUT.resolve()} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
