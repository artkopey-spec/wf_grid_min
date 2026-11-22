from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook
from pptx import Presentation
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from build_st_80_4_presentation import (
    BG,
    CYAN,
    GRID,
    MUTED,
    ORANGE,
    PANEL,
    PANEL_2,
    RED,
    TEAL,
    WHITE,
    add_bar,
    add_footer,
    add_kpi,
    add_rect,
    add_text,
    add_title,
)


SOURCE = Path("ST_80_4_SHORT_after_DOWN_0_6_backtest.xlsx")
OUTPUT = Path("ST_80_4_SHORT_strategy_presentation.pptx")


def read_table(sheet):
    rows = sheet.iter_rows(values_only=True)
    headers = next(rows)
    return [dict(zip(headers, row)) for row in rows]


def add_strategy_footer(slide, number):
    add_text(
        slide,
        "Si · Short после импульса вниз >0,6% · 100 контрактов",
        0.68,
        7.18,
        8.5,
        0.2,
        9,
        MUTED,
    )
    add_text(slide, str(number), 12.2, 7.16, 0.42, 0.22, 9, MUTED, align=PP_ALIGN.RIGHT)


def main() -> None:
    workbook = load_workbook(SOURCE, read_only=True, data_only=True)
    summary = {row["horizon"]: row for row in read_table(workbook["Summary"])}
    preferred_trades = read_table(workbook["next_trading_day_open"])

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1 — title
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_rect(slide, 0, 0, 0.18, 7.5, RED, False)
    add_text(slide, "ТОРГОВАЯ СТРАТЕГИЯ", 0.85, 1.0, 5.8, 0.35, 14, RED, True)
    add_text(slide, "Short Si после\nсильного импульса вниз", 0.82, 1.48, 7.4, 1.55, 34, WHITE, True)
    add_text(
        slide,
        "SuperTrend 80 / 4 · импульс >0,6% · фиксированные 100 контрактов",
        0.86,
        3.28,
        7.25,
        0.65,
        17,
        MUTED,
    )
    add_rect(slide, 8.55, 1.25, 3.8, 4.55, PANEL)
    add_text(slide, "ST", 9.12, 1.78, 2.65, 0.45, 23, MUTED, True, PP_ALIGN.CENTER)
    add_text(slide, "80 / 4", 8.92, 2.25, 3.05, 0.72, 36, TEAL, True, PP_ALIGN.CENTER)
    add_rect(slide, 9.12, 3.2, 2.65, 0.025, GRID, False)
    add_text(slide, "288", 9.12, 3.58, 2.65, 0.52, 29, WHITE, True, PP_ALIGN.CENTER)
    add_text(slide, "сигналов в backtest", 9.12, 4.12, 2.65, 0.3, 12, MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "+1,29 млн ₽", 8.87, 4.74, 3.15, 0.45, 24, TEAL, True, PP_ALIGN.CENTER)
    add_text(slide, "выход на следующем открытии", 8.82, 5.2, 3.25, 0.3, 10, MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "Исследовательский backtest · не торговая рекомендация", 0.86, 6.72, 6.7, 0.25, 10, MUTED)

    # 2 — mechanics
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Механика стратегии", "Трендовое продолжение после сильного движения вниз")
    steps = [
        ("1", "Держим RUB", "Позиции в Si нет", CYAN),
        ("2", "Красный ST-импульс", "Амплитуда строго >0,6%", RED),
        ("3", "Импульс завершён", "ST переключается RED → GREEN", ORANGE),
        ("4", "Открываем Short", "100 контрактов на Close сигнала", TEAL),
        ("5", "Закрываем", "На следующем торговом открытии", CYAN),
    ]
    for idx, (number, title, note, color) in enumerate(steps):
        x = 0.72 + idx * 2.48
        add_rect(slide, x, 2.0, 2.12, 2.65, PANEL)
        add_text(slide, number, x + 0.18, 2.2, 0.42, 0.42, 20, color, True, PP_ALIGN.CENTER)
        add_text(slide, title, x + 0.2, 2.82, 1.72, 0.58, 16, WHITE, True, PP_ALIGN.CENTER)
        add_text(slide, note, x + 0.2, 3.62, 1.72, 0.62, 11, MUTED, align=PP_ALIGN.CENTER)
        if idx < len(steps) - 1:
            add_text(slide, "→", x + 2.14, 3.02, 0.34, 0.36, 18, GRID, True, PP_ALIGN.CENTER)
    add_rect(slide, 1.16, 5.35, 11.0, 0.83, PANEL_2)
    add_text(slide, "Каждый сигнал = новая сделка +100 контрактов. Если позиции пересекаются, объём суммируется.", 1.38, 5.62, 10.55, 0.32, 15, WHITE, True, PP_ALIGN.CENTER)
    add_strategy_footer(slide, 2)

    # 3 — test design
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Как проведён backtest", "Одинаковые правила для всей истории без ручного отбора сделок")
    add_kpi(slide, 0.68, 1.55, 2.85, "598 933", "минутных баров", CYAN)
    add_kpi(slide, 3.74, 1.55, 2.85, "288", "первых красных импульсов дня", RED)
    add_kpi(slide, 6.8, 1.55, 2.85, "100 Si", "фиксированный объём сделки", TEAL)
    add_kpi(slide, 9.86, 1.55, 2.8, "11", "роллов скорректировано", ORANGE)
    items = [
        ("Период", "20.11.2023 — 21.08.2026"),
        ("Вход", "Close бара завершения красного ST-импульса"),
        ("Комиссия", "0,006667% за сторону"),
        ("Проскальзывание", "не учитывалось"),
        ("Гэпы", "межконтрактный спред исключён локально"),
        ("Сигналы", "каждый сигнал торгуется; перекрытия разрешены"),
    ]
    for idx, (label, value) in enumerate(items):
        col = idx % 2
        row = idx // 2
        x = 0.88 + col * 6.0
        y = 3.35 + row * 0.88
        add_text(slide, label.upper(), x, y, 1.6, 0.25, 10, MUTED, True)
        add_text(slide, value, x + 1.7, y - 0.02, 4.05, 0.4, 14, WHITE, True)
    add_strategy_footer(slide, 3)

    # 4 — comparison
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Сравнение вариантов выхода", "Net P&L после комиссии, 100 контрактов на сигнал")
    comparison = [
        ("Через 3 часа", summary["3_hours"], RED),
        ("В 19:00", summary["same_day_19_00"], ORANGE),
        ("Следующее открытие", summary["next_trading_day_open"], TEAL),
        ("24ч / первый бар", summary["24h_first_available"], CYAN),
    ]
    max_abs = max(abs(row[1]["total_net_pnl_rub"]) for row in comparison)
    for idx, (label, row, color) in enumerate(comparison):
        y = 1.68 + idx * 1.08
        value = row["total_net_pnl_rub"]
        add_text(slide, label, 0.82, y, 2.25, 0.34, 14, WHITE, True)
        add_rect(slide, 3.25, y + 0.04, 5.5, 0.22, GRID, False)
        bar_width = 5.5 * abs(value) / max_abs
        add_rect(slide, 3.25, y + 0.04, bar_width, 0.22, color, False)
        value_text = f"{value/1_000_000:+.2f} млн ₽".replace(".", ",")
        add_text(slide, value_text, 8.98, y - 0.02, 1.65, 0.35, 14, color, True, PP_ALIGN.RIGHT)
        add_text(slide, f"PF {row['profit_factor']:.2f}".replace(".", ","), 10.88, y - 0.02, 0.8, 0.35, 12, WHITE, True, PP_ALIGN.RIGHT)
        add_text(slide, f"Win {row['win_rate_pct']:.1f}%".replace(".", ","), 11.82, y - 0.02, 0.78, 0.35, 10, MUTED, align=PP_ALIGN.RIGHT)
    add_rect(slide, 0.82, 6.12, 11.8, 0.52, PANEL_2)
    add_text(slide, "Лучший баланс полной выборки: закрытие Short на следующем торговом открытии.", 1.02, 6.26, 11.38, 0.25, 15, TEAL, True, PP_ALIGN.CENTER)
    add_strategy_footer(slide, 4)

    # 5 — preferred variant + equity curve
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Выбранный вариант: следующее открытие", "Все 288 сигналов; перекрывающихся позиций нет")
    chosen = summary["next_trading_day_open"]
    add_kpi(slide, 0.68, 1.5, 2.85, "+1 291 172 ₽", "итоговый Net P&L", TEAL)
    add_kpi(slide, 3.74, 1.5, 2.85, "1,17", "Profit Factor", CYAN)
    add_kpi(slide, 6.8, 1.5, 2.85, "53,47%", "прибыльных сделок", ORANGE)
    add_kpi(slide, 9.86, 1.5, 2.8, "−1 476 545 ₽", "максимальная просадка", RED)

    cumulative = []
    value = 0.0
    for trade in preferred_trades:
        value += trade["net_pnl_rub_100_contracts"]
        cumulative.append(value)
    sampled = cumulative[:: max(1, len(cumulative) // 90)]
    if sampled[-1] != cumulative[-1]:
        sampled.append(cumulative[-1])
    chart_x, chart_y, chart_w, chart_h = 0.92, 3.42, 7.45, 2.55
    add_rect(slide, chart_x, chart_y, chart_w, chart_h, PANEL)
    add_text(slide, "Накопленный Net P&L", chart_x + 0.24, chart_y + 0.18, 3.2, 0.28, 11, MUTED, True)
    minimum, maximum = min(sampled), max(sampled)
    span = maximum - minimum or 1
    points = []
    for idx, current in enumerate(sampled):
        x = chart_x + 0.3 + (chart_w - 0.6) * idx / (len(sampled) - 1)
        y = chart_y + chart_h - 0.35 - (chart_h - 0.8) * (current - minimum) / span
        points.append((x, y))
    for first, second in zip(points, points[1:]):
        line = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            Inches(first[0]), Inches(first[1]), Inches(second[0]), Inches(second[1])
        )
        line.line.color.rgb = TEAL
        line.line.width = Inches(0.018)
    add_rect(slide, 8.78, 3.42, 3.83, 2.55, PANEL_2)
    add_text(slide, "РИСК-ПРОФИЛЬ", 9.08, 3.72, 3.25, 0.28, 11, MUTED, True)
    add_text(slide, "+4 483 ₽", 9.08, 4.18, 1.45, 0.35, 20, TEAL, True)
    add_text(slide, "средняя сделка", 10.58, 4.23, 1.5, 0.25, 10, MUTED)
    add_text(slide, "+447 235 ₽", 9.08, 4.78, 1.55, 0.35, 18, WHITE, True)
    add_text(slide, "лучшая", 10.7, 4.83, 1.25, 0.25, 10, MUTED)
    add_text(slide, "−539 832 ₽", 9.08, 5.34, 1.55, 0.35, 18, RED, True)
    add_text(slide, "худшая", 10.7, 5.39, 1.25, 0.25, 10, MUTED)
    add_strategy_footer(slide, 5)

    # 6 — conclusions and caveats
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_title(slide, "Вывод и следующий шаг", "Статистическое преимущество есть, но оно умеренное и сопровождается крупными хвостовыми рисками")
    add_rect(slide, 0.72, 1.55, 5.75, 4.95, PANEL)
    add_text(slide, "ТОРГОВОЕ ПРАВИЛО", 1.05, 1.88, 4.9, 0.28, 11, MUTED, True)
    add_text(slide, "Красный импульс\nST 80/4 >0,6%", 1.05, 2.35, 4.9, 0.82, 24, WHITE, True)
    add_text(slide, "↓", 1.05, 3.2, 4.9, 0.4, 24, RED, True, PP_ALIGN.CENTER)
    add_text(slide, "Short 100 Si", 1.05, 3.72, 4.9, 0.42, 24, TEAL, True, PP_ALIGN.CENTER)
    add_text(slide, "↓", 1.05, 4.28, 4.9, 0.4, 24, RED, True, PP_ALIGN.CENTER)
    add_text(slide, "Откуп на следующем открытии", 1.05, 4.85, 4.9, 0.42, 19, WHITE, True, PP_ALIGN.CENTER)
    add_text(slide, "Один сигнал в день · гэп-нейтральный P&L", 1.05, 5.75, 4.9, 0.3, 11, MUTED, align=PP_ALIGN.CENTER)
    add_rect(slide, 6.83, 1.55, 5.78, 4.95, PANEL_2)
    add_text(slide, "ПЕРЕД ПРИМЕНЕНИЕМ", 7.18, 1.88, 4.95, 0.28, 11, MUTED, True)
    caveats = [
        "Вход перенести с Close сигнала на следующий доступный бар",
        "Добавить фактическое проскальзывание и биржевые сборы",
        "Проверить out-of-sample и по годам / режимам волатильности",
        "Ограничить риск: худшая сделка ≈ −540 тыс. ₽",
        "Сопоставить просадку −1,48 млн ₽ с размером капитала",
    ]
    for idx, text in enumerate(caveats):
        y = 2.42 + idx * 0.72
        add_text(slide, "•", 7.18, y, 0.25, 0.28, 14, RED, True)
        add_text(slide, text, 7.5, y - 0.01, 4.55, 0.5, 13, WHITE)
    add_strategy_footer(slide, 6)

    prs.save(OUTPUT)
    print(f"saved={OUTPUT.resolve()} slides={len(prs.slides)}")


if __name__ == "__main__":
    main()
