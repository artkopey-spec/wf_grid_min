from __future__ import annotations

import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from build_st80_volatile_222_presentation import (
    BG,
    BLUE_LIGHT,
    GREEN,
    GREEN_LIGHT,
    GRID,
    MUTED,
    NAVY,
    NAVY_2,
    ORANGE,
    ORANGE_LIGHT,
    RED,
    TEAL,
    TEAL_LIGHT,
    TEXT,
    WHITE,
    add_callout,
    add_card,
    add_horizontal_bars,
    add_rich_lines,
    add_table,
    add_text,
    set_bg,
)


ROOT = Path(r"C:\3.1_wf_grid")
RESULTS = ROOT / "results_tester_st_batch_20260828"
ANALYSIS = RESULTS / "zigzag_0p2_222_volatile_09_19_analysis.json"
OUTPUT = RESULTS / "Si_ZigZag_0p2_222_volatile_days_09_19.pptx"
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def ru(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def header(slide, section: str, title: str, page: int) -> None:
    add_text(slide, section.upper(), 0.55, 0.28, 3.6, 0.25, size=10, color=TEAL, bold=True)
    add_text(slide, title, 0.55, 0.58, 12.0, 0.55, size=27, color=NAVY, bold=True)
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(1.22), Inches(12.2), Inches(0.025))
    line.fill.solid(); line.fill.fore_color.rgb = GRID; line.line.fill.background()
    add_text(slide, "ZigZag 0,2% · Close · 222 волатильных дня · 09:00–19:00", 0.58, 7.12, 9.2, 0.2, size=9, color=MUTED)
    add_text(slide, f"{page:02d}", 12.1, 7.08, 0.6, 0.24, size=10, color=MUTED, bold=True, align=PP_ALIGN.RIGHT)


def title_slide(prs, data):
    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide, NAVY)
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), SLIDE_W, Inches(0.12))
    band.fill.solid(); band.fill.fore_color.rgb = TEAL; band.line.fill.background()
    add_text(slide, "ИССЛЕДОВАНИЕ РЫНКА", 0.72, 0.62, 4.5, 0.35, size=12, color=TEAL, bold=True)
    add_text(slide, "ZigZag-движения Si", 0.72, 1.25, 9.6, 0.82, size=40, color=WHITE, bold=True)
    add_text(slide, "Только подтверждённые волатильные дни", 0.72, 2.13, 10.8, 0.55, size=25, color=WHITE, bold=True)
    add_text(slide, "Размер ног, частота крупных движений и коррекции до конца сессии", 0.72, 2.82, 11.2, 0.4, size=17, color=RGBColor(196, 211, 226))
    legs = data["legs"]
    add_card(slide, 0.72, 4.08, 2.75, 1.48, "0,2%", "порог ZigZag", color=TEAL)
    add_card(slide, 3.66, 4.08, 2.75, 1.48, f"{legs['count']:,}".replace(",", " "), "подтверждённых ног", color=ORANGE)
    add_card(slide, 6.60, 4.08, 2.75, 1.48, "222", "волатильных дня", color=TEAL)
    add_card(slide, 9.54, 4.08, 2.75, 1.48, "09–19", "строгий фильтр времени", color=GREEN)
    add_text(slide, "Si · минутные свечи · цена экстремума Close", 0.75, 6.85, 7.0, 0.28, size=11, color=RGBColor(162, 181, 201))


def build() -> None:
    data = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    legs = data["legs"]
    freq = {row["threshold_pct"]: row for row in data["frequency"]}
    corr = {row["ratio_pct"]: row for row in data["corrections"]["levels"]}
    prs = Presentation(); prs.slide_width = SLIDE_W; prs.slide_height = SLIDE_H
    title_slide(prs, data)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "01 · Основа анализа", "Данные и методика расчёта", 2)
    add_card(slide, 0.72, 1.52, 2.65, 1.46, f"{data['data']['selected_session_bars']:,}".replace(",", " "), "минутных баров в окне", color=TEAL)
    add_card(slide, 3.58, 1.52, 2.65, 1.46, "222", "выбранные торговые даты", color=TEAL)
    add_card(slide, 6.44, 1.52, 2.65, 1.46, "44,4", "условной волатильной недели", color=ORANGE)
    add_card(slide, 9.30, 1.52, 2.65, 1.46, "10,57", "условного волатильного месяца", color=ORANGE)
    add_text(slide, "Как построен ZigZag", 0.82, 3.48, 4.5, 0.35, size=18, color=NAVY, bold=True)
    add_rich_lines(slide, [
        ("Цена: ", "Close минутной свечи", TEAL),
        ("Разворот: ", "отклонение ≥0,2% от текущего экстремума", TEAL),
        ("Нога: ", "абсолютное движение между соседними экстремумами", TEAL),
        ("Последняя нога: ", "незавершённая — исключена", ORANGE),
    ], 0.85, 3.92, 5.65, 2.2, size=15, gap=6)
    add_text(slide, "Строгий time-filter", 7.0, 3.48, 4.5, 0.35, size=18, color=NAVY, bold=True)
    add_rich_lines(slide, [
        ("Оба экстремума: ", "в одной из 222 дат и внутри 09:00–19:00", GREEN),
        ("Подтверждение: ", "конечный экстремум подтверждён до 19:00", GREEN),
        ("Ночь: ", "междневные ноги полностью исключены", RED),
        ("Нормировка: ", "5 волатильных дней = неделя; 21 = месяц", ORANGE),
    ], 7.03, 3.92, 5.35, 2.2, size=15, gap=6)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "02 · Вся выборка", "Базовая статистика ног ZigZag", 3)
    add_card(slide, 0.72, 1.52, 2.2, 1.45, f"{legs['count']:,}".replace(",", " "), "подтверждённых ног", color=TEAL)
    add_card(slide, 3.12, 1.52, 2.2, 1.45, f"{ru(legs['mean_points'], 1)} п.", f"средняя · {ru(legs['mean_pct'], 3)}%", color=TEAL)
    add_card(slide, 5.52, 1.52, 2.2, 1.45, f"{ru(legs['median_points'], 1)} п.", f"медиана · {ru(legs['median_pct'], 3)}%", color=TEAL)
    add_card(slide, 7.92, 1.52, 2.2, 1.45, "50,2 / 49,8", "вверх / вниз", color=ORANGE)
    add_card(slide, 10.32, 1.52, 2.2, 1.45, f"{ru(legs['max_points'], 0)} п.", f"максимум · {ru(legs['max_pct'], 2)}%", color=ORANGE)
    add_horizontal_bars(slide, [("Среднее", legs["mean_points"]), ("Медиана", legs["median_points"])], 0.9, 3.75, 5.6, 1.6, max_value=legs["mean_points"], color=TEAL, value_suffix=" п.")
    add_rich_lines(slide, [
        ("Направления: ", f"{legs['up_count']:,} вверх / {legs['down_count']:,} вниз".replace(",", " "), TEAL),
        ("Форма: ", "среднее на 28,6% выше медианы — правый хвост растянут крупными ногами.", ORANGE),
        ("Интенсивность: ", f"{ru(legs['per_volatile_day'], 2)} ноги на один волатильный день.", GREEN),
    ], 7.0, 3.62, 5.4, 2.0, size=16, gap=9)
    add_callout(slide, "Средний размер ноги на волатильных днях: 0,494%; медианный: 0,387%.", 0.9, 5.9, 11.5, 0.65)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "03 · Размер движения", "Крупнейшие ноги: верхние доли выборки", 4)
    group_rows = []
    for row in data["top_groups"]:
        group_rows.append((row["segment"], f"{row['count']:,}".replace(",", " "), ru(row["mean_points"], 1), ru(row["median_points"], 1), f"{ru(row['mean_pct'], 3)}%", f"{ru(row['median_pct'], 3)}%", ru(row["entry_threshold_points"], 0)))
    add_table(slide, ["Сегмент", "N", "Среднее, п.", "Медиана, п.", "Среднее, %", "Медиана, %", "Порог, п."], group_rows, 0.62, 1.48, 12.1, 4.55, widths=[1.8, 0.8, 1.3, 1.3, 1.3, 1.3, 1.1], font_size=11, first_col_bold=True)
    add_callout(slide, f"Верхний 10% начинается с {ru(data['top_groups'][3]['entry_threshold_points'],0)} пунктов; верхний 1% — с {ru(data['top_groups'][5]['entry_threshold_points'],0)} пунктов.", 0.8, 6.25, 11.7, 0.55, fill=BLUE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "04 · Частота", "Как часто возникают крупные ноги", 5)
    freq_rows = []
    for threshold in (1.0, 1.5, 2.0):
        row = freq[threshold]
        freq_rows.append((f">{ru(threshold,1)}%", row["count"], f"{ru(row['share_pct'],2)}%", ru(row["per_5_volatile_days"],2), f"{row['dates_with_event']} · {ru(row['share_dates_pct'],1)}%", row["max_per_date"]))
    add_table(slide, ["Порог", "Случаев", "Доля ног", "На 5 вол. дней", "Дней с событием", "Макс./день"], freq_rows, 0.75, 1.55, 11.9, 2.55, widths=[1.2, 1.0, 1.2, 1.6, 2.0, 1.2], font_size=13, first_col_bold=True)
    add_card(slide, 0.9, 4.65, 3.2, 1.4, "6,17×", ">1% за 5 волатильных дней", color=TEAL)
    add_card(slide, 4.45, 4.65, 3.2, 1.4, "1,62×", ">1,5% за 5 волатильных дней", color=ORANGE)
    add_card(slide, 8.0, 4.65, 3.2, 1.4, "0,36×", ">2% за 5 волатильных дней", color=RED)
    add_text(slide, ">1,5%: одно событие каждые 3,08 волатильного дня · >2%: каждые 13,88 волатильного дня", 0.9, 6.35, 11.3, 0.3, size=13, color=MUTED, bold=True, align=PP_ALIGN.CENTER)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "05 · Проверка сценария", "Как измерялась коррекция", 6)
    add_card(slide, 0.75, 1.52, 2.55, 1.42, "274", "ноги ZigZag >1%", color=TEAL)
    add_card(slide, 3.55, 1.52, 2.55, 1.42, "141 / 133", "рост / падение", color=TEAL)
    add_card(slide, 6.35, 1.52, 2.55, 1.42, "33 / 50 / 90%", "уровни возврата", color=ORANGE)
    add_card(slide, 9.15, 1.52, 2.55, 1.42, "до 19:00", "окно той же сессии", color=GREEN)
    add_text(slide, "Пример растущей ноги", 0.9, 3.55, 3.5, 0.35, size=18, color=NAVY, bold=True)
    x0, y0 = 1.0, 5.25
    pts = [(1.0,5.4),(3.0,3.8),(5.2,4.35),(7.2,4.75),(9.2,5.25)]
    for i in range(len(pts)-1):
        x1,y1=pts[i]; x2,y2=pts[i+1]
        line=slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2)); line.line.color.rgb = TEAL if i==0 else ORANGE; line.line.width=Pt(3)
    add_text(slide, "старт", 0.8, 5.55, 1.0, 0.25, size=11, color=MUTED)
    add_text(slide, "экстремум", 2.55, 3.42, 1.3, 0.25, size=11, color=TEAL, bold=True)
    add_text(slide, "33%", 4.75, 4.02, 0.8, 0.25, size=11, color=ORANGE, bold=True)
    add_text(slide, "50%", 6.75, 4.42, 0.8, 0.25, size=11, color=ORANGE, bold=True)
    add_text(slide, "90%", 8.75, 4.92, 0.8, 0.25, size=11, color=ORANGE, bold=True)
    add_rich_lines(slide, [
        ("После роста: ", "Low должен коснуться уровня коррекции", TEAL),
        ("После падения: ", "High должен коснуться уровня коррекции", TEAL),
        ("Отсчёт: ", "от конечного экстремума до 19:00 той же сессии", ORANGE),
        ("Подтверждение: ", "сам экстремум обязательно подтверждён до 19:00", GREEN),
    ], 9.75, 3.55, 2.75, 2.55, size=13, gap=6)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "06 · Результат", "Вероятность коррекции до конца сессии", 7)
    for idx, ratio in enumerate((33.0,50.0,90.0)):
        row=corr[ratio]; x=0.75+idx*4.05; color=(TEAL,ORANGE,RED)[idx]
        add_card(slide,x,1.55,3.55,1.55,f"{ru(row['probability_pct'],2)}%",f"коррекция {ru(ratio,0)}%",color=color,note=f"{row['hits']} из 274 · без коррекции: {row['misses']}")
        add_text(slide,f"Медиана: {ru(row['median_minutes'],1)} мин",x+0.2,3.45,3.15,0.35,size=15,color=NAVY,bold=True,align=PP_ALIGN.CENTER)
        add_text(slide,f"Среднее: {ru(row['mean_minutes']/60,2)} ч",x+0.2,3.92,3.15,0.35,size=14,color=MUTED,align=PP_ALIGN.CENTER)
        add_text(slide,f"≤2 часов: {row['within_2h']} · {ru(row['within_2h_pct_of_sample'],1)}%",x+0.2,4.38,3.15,0.35,size=14,color=MUTED,align=PP_ALIGN.CENTER)
    add_callout(slide, "При строгом закрытии окна в 19:00 половинный откат достигался в 50,36% случаев; возврат 90% — в 24,45%.", 0.85, 5.45, 11.65, 0.78)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "07 · Детализация", "Скорость и направление коррекции", 8)
    detail_rows=[]
    for ratio in (33.0,50.0,90.0):
        row=corr[ratio]
        detail_rows.append((f"{ru(ratio,0)}%",f"{ru(row['median_minutes'],1)} мин",f"{ru(row['mean_minutes']/60,2)} ч",f"{row['within_2h']} · {ru(row['within_2h_pct_of_sample'],1)}%",f"{row['up_hits']}/{data['corrections']['direction_totals']['UP']} · {ru(row['up_probability_pct'],1)}%",f"{row['down_hits']}/{data['corrections']['direction_totals']['DOWN']} · {ru(row['down_probability_pct'],1)}%"))
    add_table(slide,["Глубина","Медиана","Среднее","≤2 часов","После роста","После падения"],detail_rows,0.65,1.55,12.05,2.7,widths=[1.0,1.3,1.3,1.6,1.9,1.9],font_size=13,first_col_bold=True)
    add_rich_lines(slide,[
        ("Скорость: ","33%-коррекция обычно происходит быстро: медиана 20 минут.",TEAL),
        ("50%: ","медиана 50,5 минуты; 38,7% всей выборки достигли уровня за первые 2 часа.",ORANGE),
        ("Асимметрия: ","33% почти симметрично; 50% чаще после падения, 90% — после роста.",TEAL),
        ("Окно: ","поздние ноги имеют меньше времени до 19:00 — это часть строгой методики.",RED),
    ],0.85,4.85,11.55,1.65,size=15,gap=6)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "08 · Практическая интерпретация", "Что следует из данных", 9)
    add_card(slide,0.75,1.55,3.45,1.45,"81,1%","волатильных дней имели ногу >1%",color=TEAL)
    add_card(slide,4.45,1.55,3.45,1.45,"28,8%","дней имели ногу >1,5%",color=ORANGE)
    add_card(slide,8.15,1.55,3.45,1.45,"6,8%","дней имели ногу >2%",color=RED)
    add_text(slide,"Возврат цены после ноги >1%",0.85,3.52,4.4,0.35,size=18,color=NAVY,bold=True)
    add_horizontal_bars(slide,[("33%",corr[33.0]["probability_pct"]),("50%",corr[50.0]["probability_pct"]),("90%",corr[90.0]["probability_pct"])],0.9,4.0,5.5,1.85,max_value=100,color=TEAL,value_suffix="%")
    add_rich_lines(slide,[
        ("Частота ≠ равномерность: ","крупные ноги группируются внутри отдельных сессий.",ORANGE),
        ("Практически: ","после ноги >1% шанс половинного возврата до 19:00 близок к 50/50.",TEAL),
        ("Осторожно: ","экстремум ZigZag известен только после подтверждающего разворота.",RED),
    ],7.0,3.72,5.3,2.15,size=15,gap=9)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "09 · Резюме", "Итог исследования", 10)
    add_text(slide,"Три цифры, которые стоит запомнить",0.8,1.48,6.0,0.4,size=20,color=NAVY,bold=True)
    add_card(slide,0.8,2.05,3.5,1.5,"335 п. · 0,387%","медианная нога ZigZag",color=TEAL)
    add_card(slide,4.55,2.05,3.5,1.5,"6,17 / 5 дней","частота ног >1%",color=ORANGE)
    add_card(slide,8.3,2.05,3.5,1.5,"50,36%","коррекция 50% до 19:00",color=GREEN)
    add_callout(slide,"На подтверждённых волатильных днях крупные ноги встречаются часто, но половинный возврат до конца той же сессии происходит лишь примерно в половине случаев.",0.8,4.12,11.5,0.85)
    add_rich_lines(slide,[
        ("Ограничение 1: ","222 дня выбраны по итоговой волатильности полной сессии.",RED),
        ("Ограничение 2: ","ZigZag использует подтверждение и не является сигналом в точке экстремума без задержки.",RED),
        ("Ограничение 3: ","историческая частота не гарантирует повторение; комиссии и исполнение не анализируются.",RED),
    ],0.9,5.35,11.3,1.25,size=13,gap=4)

    prs.save(OUTPUT)
    print(f"Slides: {len(prs.slides)}")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    build()
