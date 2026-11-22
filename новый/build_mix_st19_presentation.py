from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from build_st_80_4_presentation import (
    BG, CYAN, GRID, MUTED, ORANGE, PANEL, PANEL_2, RED, TEAL, WHITE,
    add_bar, add_kpi, add_rect, add_text,
)


OUTPUT = Path("MIX_ST_19_4_key_findings.pptx")


def footer(slide, number):
    add_text(slide, "MIX · минутные данные · межконтрактные гэпы скорректированы", 0.68, 7.18, 9.0, 0.2, 9, MUTED)
    add_text(slide, str(number), 12.2, 7.16, 0.42, 0.22, 9, MUTED, align=PP_ALIGN.RIGHT)


def title(slide, heading, subtitle=None):
    add_text(slide, heading, 0.65, 0.38, 10.7, 0.55, 27, WHITE, True)
    add_rect(slide, 0.65, 1.03, 12.0, 0.025, GRID, False)
    if subtitle:
        add_text(slide, subtitle, 0.67, 1.12, 12.0, 0.34, 12, MUTED)


def main():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    add_rect(slide, 0, 0, 0.18, 7.5, TEAL, False)
    add_text(slide, "SUPER​TREND", 0.85, 1.06, 5.5, 0.4, 14, TEAL, True)
    add_text(slide, "Движения MIX\nдля ST 19 / 4", 0.82, 1.55, 7.3, 1.55, 36, WHITE, True)
    add_text(slide, "Распределение, частота и время возникновения движений более 0,5%", 0.86, 3.3, 6.8, 0.85, 18, MUTED)
    add_rect(slide, 8.3, 1.25, 4.05, 4.75, PANEL)
    add_text(slide, "19", 8.75, 1.72, 1.35, 0.8, 42, TEAL, True, PP_ALIGN.CENTER)
    add_text(slide, "ATR period", 8.65, 2.52, 1.55, 0.3, 12, MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "4,0", 10.55, 1.72, 1.35, 0.8, 42, CYAN, True, PP_ALIGN.CENTER)
    add_text(slide, "multiplier", 10.48, 2.52, 1.52, 0.3, 12, MUTED, align=PP_ALIGN.CENTER)
    add_rect(slide, 8.72, 3.15, 3.2, 0.025, GRID, False)
    add_text(slide, "630 299", 8.72, 3.55, 3.2, 0.45, 25, WHITE, True, PP_ALIGN.CENTER)
    add_text(slide, "минутных баров", 8.72, 4.02, 3.2, 0.28, 12, MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "21.08.2023 — 21.08.2026", 8.72, 4.68, 3.2, 0.3, 12, WHITE, True, PP_ALIGN.CENTER)
    add_text(slide, "11 роллов скорректировано", 8.72, 5.17, 3.2, 0.26, 11, ORANGE, align=PP_ALIGN.CENTER)
    add_text(slide, "Краткий аналитический обзор", 0.86, 6.72, 5.2, 0.25, 11, MUTED)

    # 2
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    title(slide, "Что именно измерялось", "Фактическая амплитуда внутри каждого цветового участка SuperTrend")
    add_text(slide, "ST 19 / 4", 11.15, 0.4, 1.45, 0.4, 14, TEAL, True, PP_ALIGN.RIGHT)
    add_kpi(slide, 0.68, 1.65, 3.75, "13 287", "завершённых движений ST", TEAL)
    add_kpi(slide, 4.79, 1.65, 3.75, "0,2448%", "среднее движение всей выборки", CYAN)
    add_kpi(slide, 8.9, 1.65, 3.75, "47,42", "минутных баров до смены цвета", ORANGE)
    add_text(slide, "ЗЕЛЁНЫЙ ST", 0.9, 3.52, 2.15, 0.3, 13, TEAL, True)
    add_text(slide, "Close смены → максимальный High", 0.9, 3.9, 3.4, 0.35, 16, WHITE, True)
    add_text(slide, "КРАСНЫЙ ST", 4.8, 3.52, 2.15, 0.3, 13, RED, True)
    add_text(slide, "Close смены → минимальный Low", 4.8, 3.9, 3.4, 0.35, 16, WHITE, True)
    add_rect(slide, 8.75, 3.35, 3.9, 2.15, PANEL_2)
    add_text(slide, "Коррекция роллов", 9.02, 3.65, 3.35, 0.35, 17, WHITE, True)
    add_text(slide, "• применены реальные offset\n• малые гэпы без offset не менялись\n• знаменатель — цена начала движения", 9.02, 4.15, 3.25, 1.05, 12, MUTED)
    add_text(slide, "Процент = скорректированная амплитуда / цена начала движения", 0.9, 5.95, 8.2, 0.4, 15, WHITE, True)
    footer(slide, 2)

    # 3
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    title(slide, "Профиль движений ST", "Среднее после сортировки по скорректированной амплитуде")
    add_text(slide, "ST 19 / 4", 11.15, 0.4, 1.45, 0.4, 14, TEAL, True, PP_ALIGN.RIGHT)
    add_bar(slide, "Вся выборка", "0,2448%", 0.2448, 1.12, 0.85, 1.78, 11.4, CYAN)
    add_bar(slide, "Верхние 50%", "0,4455%", 0.4455, 1.12, 0.85, 2.48, 11.4, TEAL)
    add_bar(slide, "Верхние 30%", "0,6250%", 0.6250, 1.12, 0.85, 3.18, 11.4, ORANGE)
    add_bar(slide, "Верхние 10%", "1,1010%", 1.1010, 1.12, 0.85, 3.88, 11.4, RED)
    add_rect(slide, 0.85, 4.75, 11.65, 1.55, PANEL)
    for idx, (label, value, color) in enumerate([
        ("Порог верхних 50%", "0,1186%", WHITE),
        ("Порог верхних 30%", "0,2468%", WHITE),
        ("Порог верхних 10%", "0,6086%", WHITE),
        ("Максимум", "5,6773%", TEAL),
    ]):
        x = 1.12 + idx * 2.88
        add_text(slide, label, x, 5.03, 2.4, 0.25, 10, MUTED)
        add_text(slide, value, x, 5.38, 2.4, 0.38, 20, color, True)
    footer(slide, 3)

    # 4
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    title(slide, "Движения более 0,5%", "Распределение и частота на всей выборке ST 19 / 4")
    add_text(slide, "ST 19 / 4", 11.15, 0.4, 1.45, 0.4, 14, TEAL, True, PP_ALIGN.RIGHT)
    add_kpi(slide, 0.68, 1.55, 2.85, "1 802", "всего движений", TEAL)
    add_kpi(slide, 3.74, 1.55, 2.85, "0,9570%", "средний размер", CYAN)
    add_kpi(slide, 6.8, 1.55, 2.85, "2,36", "в среднем за день", ORANGE)
    add_kpi(slide, 9.86, 1.55, 2.8, "11,48", "в среднем за неделю", RED)
    add_text(slide, "РАСПРЕДЕЛЕНИЕ РАЗМЕРОВ", 0.82, 3.2, 4.0, 0.3, 12, MUTED, True)
    size_rows = [
        ("0,50–0,60%", 24.53, TEAL),
        ("0,60–0,75%", 23.58, CYAN),
        ("0,75–1,00%", 22.20, ORANGE),
        ("1,00–1,50%", 18.42, RED),
        ("> 1,50%", 11.27, MUTED),
    ]
    for idx, (label, value, color) in enumerate(size_rows):
        add_bar(slide, label, f"{value:.2f}%".replace(".", ","), value, 26, 0.82, 3.62 + idx * 0.49, 6.2, color)
    add_rect(slide, 7.55, 3.22, 5.1, 2.95, PANEL)
    add_text(slide, "ЧАСТОТА ПО ДНЯМ", 7.88, 3.52, 4.35, 0.3, 12, MUTED, True)
    add_text(slide, "82,48%", 7.88, 4.02, 1.65, 0.45, 24, WHITE, True)
    add_text(slide, "дней хотя бы с одним", 9.55, 4.08, 2.55, 0.3, 11, MUTED)
    add_text(slide, "19,87%", 7.88, 4.72, 1.65, 0.45, 24, CYAN, True)
    add_text(slide, "дней ровно с двумя", 9.55, 4.78, 2.55, 0.3, 11, MUTED)
    add_text(slide, "60,92%", 7.88, 5.42, 1.65, 0.45, 24, TEAL, True)
    add_text(slide, "дней с двумя или более", 9.55, 5.48, 2.65, 0.3, 11, MUTED)
    footer(slide, 4)

    # 5
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = BG
    title(slide, "Когда движение превышает 0,5%", "Время первого достижения порога; не время последующей смены цвета")
    add_text(slide, "ST 19 / 4", 11.15, 0.4, 1.45, 0.4, 14, TEAL, True, PP_ALIGN.RIGHT)
    add_rect(slide, 0.72, 1.52, 5.75, 2.05, PANEL)
    add_text(slide, "09:00–13:00", 1.05, 1.83, 2.4, 0.35, 17, WHITE, True)
    add_text(slide, "686", 1.02, 2.28, 1.3, 0.55, 30, TEAL, True)
    add_text(slide, "45,34% внутри окна 09–19", 2.25, 2.37, 3.4, 0.3, 12, MUTED)
    add_text(slide, "23,86 события / 100 торговых часов", 1.04, 3.03, 4.7, 0.28, 12, TEAL, True)
    add_rect(slide, 6.83, 1.52, 5.78, 2.05, PANEL)
    add_text(slide, "13:00–19:00", 7.15, 1.83, 2.4, 0.35, 17, WHITE, True)
    add_text(slide, "827", 7.13, 2.28, 1.3, 0.55, 30, CYAN, True)
    add_text(slide, "54,66% внутри окна 09–19", 8.36, 2.37, 3.4, 0.3, 12, MUTED)
    add_text(slide, "18,89 события / 100 торговых часов", 7.15, 3.03, 4.7, 0.28, 12, CYAN, True)
    add_text(slide, "ПО ДНЯМ НЕДЕЛИ · событий на торговый день", 0.82, 4.05, 5.5, 0.28, 12, MUTED, True)
    weekdays = [("Пн", 2.40), ("Вт", 2.37), ("Ср", 2.37), ("Чт", 2.37), ("Пт", 2.27)]
    for idx, (label, value) in enumerate(weekdays):
        x = 0.92 + idx * 1.32
        height = value / 2.45 * 1.25
        add_rect(slide, x, 6.18 - height, 0.72, height, TEAL if idx == 0 else CYAN, False)
        add_text(slide, f"{value:.2f}".replace(".", ","), x - 0.05, 4.72, 0.82, 0.25, 11, WHITE, True, PP_ALIGN.CENTER)
        add_text(slide, label, x, 6.25, 0.72, 0.25, 11, MUTED, True, PP_ALIGN.CENTER)
    add_rect(slide, 8.05, 4.15, 4.55, 2.25, PANEL_2)
    add_text(slide, "Главный вывод", 8.38, 4.47, 3.9, 0.32, 17, WHITE, True)
    add_text(slide, "По количеству лидирует окно 13–19. Но на час торговли плотность событий в 09–13 примерно в 1,26 раза выше.", 8.38, 5.02, 3.72, 1.05, 14, TEAL, True)
    footer(slide, 5)

    prs.save(OUTPUT)
    print(f"saved={OUTPUT.resolve()} slides={len(prs.slides)}")


if __name__ == "__main__":
    main()
