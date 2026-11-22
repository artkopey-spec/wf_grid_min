from __future__ import annotations

import argparse
import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from build_st80_volatile_222_presentation import (
    BG,
    BLUE_LIGHT,
    GREEN,
    GRID,
    MUTED,
    NAVY,
    ORANGE,
    ORANGE_LIGHT,
    RED,
    TEAL,
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
ANALYSIS = RESULTS / "zigzag_0p2_222_volatile_all_hours_analysis.json"
OUTPUT = RESULTS / "Si_ZigZag_0p2_222_volatile_days_all_hours.pptx"
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
FOOTER_THRESHOLD = "0,2%"


def ru(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def compact_pct(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, default=ANALYSIS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args()


def header(slide, section: str, title: str, page: int) -> None:
    add_text(slide, section.upper(), 0.55, 0.28, 4.0, 0.25, size=10, color=TEAL, bold=True)
    add_text(slide, title, 0.55, 0.58, 12.0, 0.55, size=27, color=NAVY, bold=True)
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(1.22), Inches(12.2), Inches(0.025))
    line.fill.solid(); line.fill.fore_color.rgb = GRID; line.line.fill.background()
    add_text(slide, f"ZigZag {FOOTER_THRESHOLD} · Close · 222 волатильных дня · все доступные часы", 0.58, 7.12, 9.8, 0.2, size=9, color=MUTED)
    add_text(slide, f"{page:02d}", 12.1, 7.08, 0.6, 0.24, size=10, color=MUTED, bold=True, align=PP_ALIGN.RIGHT)


def build() -> None:
    args = parse_args()
    data = json.loads(args.analysis.read_text(encoding="utf-8"))
    global FOOTER_THRESHOLD
    FOOTER_THRESHOLD = compact_pct(data["method"]["zigzag_reversal_pct"])
    legs = data["legs"]
    freq = {row["threshold_pct"]: row for row in data["frequency"]}
    corr = {row["ratio_pct"]: row for row in data["corrections"]["levels"]}
    sample = data["corrections"]["sample_count"]
    up_total = data["corrections"]["direction_totals"]["UP"]
    down_total = data["corrections"]["direction_totals"]["DOWN"]

    prs = Presentation(); prs.slide_width = SLIDE_W; prs.slide_height = SLIDE_H

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide, NAVY)
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), SLIDE_W, Inches(0.12))
    band.fill.solid(); band.fill.fore_color.rgb = TEAL; band.line.fill.background()
    add_text(slide, "ИССЛЕДОВАНИЕ РЫНКА", 0.72, 0.62, 4.5, 0.35, size=12, color=TEAL, bold=True)
    add_text(slide, "ZigZag-движения Si", 0.72, 1.25, 9.6, 0.82, size=40, color=WHITE, bold=True)
    add_text(slide, "Только 222 подтверждённых волатильных дня", 0.72, 2.13, 11.2, 0.55, size=25, color=WHITE, bold=True)
    add_text(slide, "Размер ног, частота крупных движений и глубина коррекций · без фильтра времени", 0.72, 2.82, 11.6, 0.4, size=17, color=RGBColor(196, 211, 226))
    add_card(slide, 0.72, 4.08, 2.75, 1.48, FOOTER_THRESHOLD, "порог ZigZag", color=TEAL)
    add_card(slide, 3.66, 4.08, 2.75, 1.48, f"{legs['count']:,}".replace(",", " "), "подтверждённых ног", color=ORANGE)
    add_card(slide, 6.60, 4.08, 2.75, 1.48, "222", "волатильных дня", color=TEAL)
    add_card(slide, 9.54, 4.08, 2.75, 1.48, "24 часа", "окно анализа коррекции", color=GREEN)
    add_text(slide, "Si · минутные свечи · цена экстремума Close · все доступные часы выбранных дат", 0.75, 6.85, 9.0, 0.28, size=11, color=RGBColor(162, 181, 201))

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "01 · Основа анализа", "Данные и методика расчёта", 2)
    add_card(slide, 0.72, 1.52, 2.65, 1.46, f"{data['data']['selected_date_bars']:,}".replace(",", " "), "минутных баров 222 дат", color=TEAL)
    add_card(slide, 3.58, 1.52, 2.65, 1.46, "222", "выбранные торговые даты", color=TEAL)
    add_card(slide, 6.44, 1.52, 2.65, 1.46, "44,4", "условной волатильной недели", color=ORANGE)
    add_card(slide, 9.30, 1.52, 2.65, 1.46, "10,57", "условного волатильного месяца", color=ORANGE)
    add_text(slide, "Как построен ZigZag", 0.82, 3.48, 4.5, 0.35, size=18, color=NAVY, bold=True)
    add_rich_lines(slide, [
        ("Цена: ", "Close минутной свечи", TEAL),
        ("Разворот: ", f"отклонение ≥{FOOTER_THRESHOLD} от текущего экстремума", TEAL),
        ("Нога: ", "абсолютное движение между соседними экстремумами", TEAL),
        ("Последняя нога: ", "незавершённая — исключена", ORANGE),
    ], 0.85, 3.92, 5.65, 2.2, size=15, gap=6)
    add_text(slide, "Границы выборки", 7.0, 3.48, 4.5, 0.35, size=18, color=NAVY, bold=True)
    add_rich_lines(slide, [
        ("Время: ", "никакого внутридневного фильтра", GREEN),
        ("Дата: ", "оба экстремума находятся в одной из 222 дат", GREEN),
        ("Междневные ноги: ", "исключены, чтобы не включать обычные дни", RED),
        ("Частота: ", "5 волатильных дней = неделя; 21 = месяц", ORANGE),
    ], 7.03, 3.92, 5.35, 2.2, size=15, gap=6)
    add_callout(slide, "Для коррекции после ноги >1% используются последующие High–Low в течение 24 календарных часов — полностью аналогично исходной презентации.", 0.85, 6.27, 11.55, 0.55, fill=BLUE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "02 · Вся выборка", "Базовая статистика ног ZigZag", 3)
    add_card(slide, 0.72, 1.52, 2.2, 1.45, f"{legs['count']:,}".replace(",", " "), "подтверждённых ног", color=TEAL)
    add_card(slide, 3.12, 1.52, 2.2, 1.45, f"{ru(legs['mean_points'], 1)} п.", f"средняя · {ru(legs['mean_pct'], 3)}%", color=TEAL)
    add_card(slide, 5.52, 1.52, 2.2, 1.45, f"{ru(legs['median_points'], 1)} п.", f"медиана · {ru(legs['median_pct'], 3)}%", color=TEAL)
    add_card(slide, 7.92, 1.52, 2.2, 1.45, "49,9 / 50,1", "вверх / вниз", color=ORANGE)
    add_card(slide, 10.32, 1.52, 2.2, 1.45, f"{ru(legs['max_points'], 0)} п.", f"максимум · {ru(legs['max_pct'], 2)}%", color=ORANGE)
    add_horizontal_bars(slide, [("Среднее", legs["mean_points"]), ("Медиана", legs["median_points"])], 0.9, 3.75, 5.6, 1.6, max_value=legs["mean_points"], color=TEAL, value_suffix=" п.")
    add_rich_lines(slide, [
        ("Направления: ", f"{legs['up_count']:,} вверх / {legs['down_count']:,} вниз".replace(",", " "), TEAL),
        ("Форма: ", "среднее выше медианы на 28,8% — крупные ноги растягивают правый хвост.", ORANGE),
        ("Интенсивность: ", f"{ru(legs['per_volatile_day'], 2)} ноги на один волатильный день.", GREEN),
    ], 7.0, 3.62, 5.4, 2.0, size=16, gap=9)
    add_callout(slide, f"Средний размер ноги: {ru(legs['mean_pct'],3)}%; медианный: {ru(legs['median_pct'],3)}%.", 0.9, 5.9, 11.5, 0.65)

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
    add_card(slide, 0.9, 4.65, 3.2, 1.4, f"{ru(freq[1.0]['per_5_volatile_days'],2)}×", ">1% за 5 волатильных дней", color=TEAL)
    add_card(slide, 4.45, 4.65, 3.2, 1.4, f"{ru(freq[1.5]['per_5_volatile_days'],2)}×", ">1,5% за 5 волатильных дней", color=ORANGE)
    add_card(slide, 8.0, 4.65, 3.2, 1.4, f"{ru(freq[2.0]['per_5_volatile_days'],2)}×", ">2% за 5 волатильных дней", color=RED)
    add_text(slide, f">1,5%: одно событие каждые {ru(freq[1.5]['volatile_days_per_event'],2)} волатильного дня · >2%: каждые {ru(freq[2.0]['volatile_days_per_event'],2)} дня", 0.9, 6.35, 11.3, 0.3, size=13, color=MUTED, bold=True, align=PP_ALIGN.CENTER)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "05 · Проверка сценария", "Как измерялась коррекция", 6)
    add_card(slide, 0.75, 1.52, 2.55, 1.42, f"{sample}", "ноги ZigZag >1%", color=TEAL)
    add_card(slide, 3.55, 1.52, 2.55, 1.42, f"{up_total} / {down_total}", "рост / падение", color=TEAL)
    add_card(slide, 6.35, 1.52, 2.55, 1.42, "33 / 50 / 90%", "уровни возврата", color=ORANGE)
    add_card(slide, 9.15, 1.52, 2.55, 1.42, "24 часа", "после конечного экстремума", color=GREEN)
    add_text(slide, "Пример растущей ноги", 0.9, 3.55, 3.5, 0.35, size=18, color=NAVY, bold=True)
    pts = [(1.0,5.4),(3.0,3.8),(5.2,4.35),(7.2,4.75),(9.2,5.25)]
    for i in range(len(pts)-1):
        x1,y1=pts[i]; x2,y2=pts[i+1]
        line=slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2)); line.line.color.rgb = TEAL if i==0 else ORANGE; line.line.width=Pt(3)
    for text,x,y,color in [("старт",0.8,5.55,MUTED),("экстремум",2.55,3.42,TEAL),("33%",4.75,4.02,ORANGE),("50%",6.75,4.42,ORANGE),("90%",8.75,4.92,ORANGE)]:
        add_text(slide,text,x,y,1.3,0.25,size=11,color=color,bold=color!=MUTED)
    add_rich_lines(slide, [
        ("После роста: ", "Low должен коснуться уровня коррекции", TEAL),
        ("После падения: ", "High должен коснуться уровня коррекции", TEAL),
        ("Отсчёт: ", "от конечного экстремума ноги", ORANGE),
        ("Окно: ", "следующие 24 календарных часа", GREEN),
    ], 9.75, 3.55, 2.75, 2.55, size=13, gap=6)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "06 · Результат", "Вероятность коррекции за 24 часа", 7)
    for idx, ratio in enumerate((33.0,50.0,90.0)):
        row=corr[ratio]; x=0.75+idx*4.05; color=(TEAL,ORANGE,RED)[idx]
        add_card(slide,x,1.55,3.55,1.55,f"{ru(row['probability_pct'],2)}%",f"коррекция {ru(ratio,0)}%",color=color,note=f"{row['hits']} из {sample} · без коррекции: {row['misses']}")
        add_text(slide,f"Медиана: {ru(row['median_minutes'],1)} мин",x+0.2,3.45,3.15,0.35,size=15,color=NAVY,bold=True,align=PP_ALIGN.CENTER)
        add_text(slide,f"Среднее: {ru(row['mean_minutes']/60,2)} ч",x+0.2,3.92,3.15,0.35,size=14,color=MUTED,align=PP_ALIGN.CENTER)
        add_text(slide,f"≤2 часов: {row['within_2h']} · {ru(row['within_2h_pct_of_sample'],1)}%",x+0.2,4.38,3.15,0.35,size=14,color=MUTED,align=PP_ALIGN.CENTER)
    add_callout(slide, f"В течение суток коррекция 50% достигалась в {ru(corr[50.0]['probability_pct'],2)}% случаев; возврат 90% — в {ru(corr[90.0]['probability_pct'],2)}%.", 0.85, 5.45, 11.65, 0.78)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "07 · Детализация", "Скорость и направление коррекции", 8)
    detail_rows=[]
    for ratio in (33.0,50.0,90.0):
        row=corr[ratio]
        detail_rows.append((f"{ru(ratio,0)}%",f"{ru(row['median_minutes'],1)} мин",f"{ru(row['mean_minutes']/60,2)} ч",f"{row['within_2h']} · {ru(row['within_2h_pct_of_sample'],1)}%",f"{row['up_hits']}/{up_total} · {ru(row['up_probability_pct'],1)}%",f"{row['down_hits']}/{down_total} · {ru(row['down_probability_pct'],1)}%"))
    add_table(slide,["Глубина","Медиана","Среднее","≤2 часов","После роста","После падения"],detail_rows,0.65,1.55,12.05,2.7,widths=[1.0,1.3,1.3,1.6,1.9,1.9],font_size=13,first_col_bold=True)
    add_rich_lines(slide,[
        ("33%: ",f"медиана {ru(corr[33.0]['median_minutes'],0)} минут; {ru(corr[33.0]['within_2h_pct_of_sample'],1)}% всей выборки достигли уровня за 2 часа.",TEAL),
        ("50%: ",f"медиана {ru(corr[50.0]['median_minutes'],0)} минут; вероятность {ru(corr[50.0]['probability_pct'],1)}%.",ORANGE),
        ("90%: ",f"медиана {ru(corr[90.0]['median_minutes']/60,2)} часа; вероятность {ru(corr[90.0]['probability_pct'],1)}%.",RED),
        ("Направление: ","вероятности после растущих и падающих ног почти симметричны.",GREEN),
    ],0.85,4.85,11.55,1.65,size=15,gap=6)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "08 · Практическая интерпретация", "Что следует из данных", 9)
    add_card(slide,0.75,1.55,3.45,1.45,f"{ru(freq[1.0]['share_dates_pct'],1)}%","волатильных дней имели ногу >1%",color=TEAL)
    add_card(slide,4.45,1.55,3.45,1.45,f"{ru(freq[1.5]['share_dates_pct'],1)}%","дней имели ногу >1,5%",color=ORANGE)
    add_card(slide,8.15,1.55,3.45,1.45,f"{ru(freq[2.0]['share_dates_pct'],1)}%","дней имели ногу >2%",color=RED)
    add_text(slide,"Возврат цены после ноги >1%",0.85,3.52,4.4,0.35,size=18,color=NAVY,bold=True)
    add_horizontal_bars(slide,[("33%",corr[33.0]["probability_pct"]),("50%",corr[50.0]["probability_pct"]),("90%",corr[90.0]["probability_pct"])],0.9,4.0,5.5,1.85,max_value=100,color=TEAL,value_suffix="%")
    add_rich_lines(slide,[
        ("Частота ≠ равномерность: ","крупные ноги группируются внутри отдельных волатильных сессий.",ORANGE),
        ("Практически: ",f"после ноги >1% половинный возврат за сутки наблюдался в {ru(corr[50.0]['probability_pct'],1)}% случаев.",TEAL),
        ("Осторожно: ","экстремум ZigZag известен только после подтверждающего разворота.",RED),
    ],7.0,3.72,5.3,2.15,size=15,gap=9)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "09 · Резюме", "Итог исследования", 10)
    add_text(slide,"Три цифры, которые стоит запомнить",0.8,1.48,6.0,0.4,size=20,color=NAVY,bold=True)
    add_card(slide,0.8,2.05,3.5,1.5,f"{ru(legs['median_points'],0)} п. · {ru(legs['median_pct'],3)}%","медианная нога ZigZag",color=TEAL)
    add_card(slide,4.55,2.05,3.5,1.5,f"{ru(freq[1.0]['per_5_volatile_days'],2)} / 5 дней","частота ног >1%",color=ORANGE)
    add_card(slide,8.3,2.05,3.5,1.5,f"{ru(corr[50.0]['probability_pct'],2)}%","коррекция 50% за 24 часа",color=GREEN)
    add_callout(slide,f"На 222 подтверждённых волатильных днях крупные ноги встречались часто: >1% в {ru(freq[1.0]['share_dates_pct'],1)}% дат. Половинный возврат за следующие сутки происходил в {ru(corr[50.0]['probability_pct'],2)}% случаев.",0.8,4.12,11.5,0.85)
    add_rich_lines(slide,[
        ("Ограничение 1: ","222 дня выбраны по итоговой волатильности полной сессии.",RED),
        ("Ограничение 2: ","ZigZag подтверждает экстремум с задержкой и не является сигналом в самой точке разворота.",RED),
        ("Ограничение 3: ","историческая частота не гарантирует повторение; комиссии и исполнение не анализируются.",RED),
    ],0.9,5.35,11.3,1.25,size=13,gap=4)

    prs.save(args.output)
    print(f"Slides: {len(prs.slides)}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    build()
