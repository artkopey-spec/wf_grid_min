from __future__ import annotations

import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from build_st80_volatile_222_presentation import (
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
DATA_02 = RESULTS / "zigzag_0p2_222_volatile_all_hours_analysis.json"
DATA_015 = RESULTS / "zigzag_0p15_222_volatile_all_hours_analysis.json"
OUTPUT = RESULTS / "Si_ZigZag_0p2_vs_0p15_222_volatile_days.pptx"
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def ru(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def frequency_map(data: dict) -> dict:
    return {row["threshold_pct"]: row for row in data["frequency"]}


def correction_map(data: dict) -> dict:
    return {row["ratio_pct"]: row for row in data["corrections"]["levels"]}


def header(slide, section: str, title: str, page: int) -> None:
    add_text(slide, section.upper(), 0.55, 0.28, 4.6, 0.25, size=10, color=TEAL, bold=True)
    add_text(slide, title, 0.55, 0.58, 12.0, 0.55, size=27, color=NAVY, bold=True)
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(1.22), Inches(12.2), Inches(0.025))
    line.fill.solid(); line.fill.fore_color.rgb = GRID; line.line.fill.background()
    add_text(slide, "ZigZag 0,2% vs 0,15% · 222 волатильных дня · все доступные часы", 0.58, 7.12, 10.0, 0.2, size=9, color=MUTED)
    add_text(slide, f"{page:02d}", 12.1, 7.08, 0.6, 0.24, size=10, color=MUTED, bold=True, align=PP_ALIGN.RIGHT)


def build() -> None:
    d02 = load(DATA_02)
    d15 = load(DATA_015)
    l02, l15 = d02["legs"], d15["legs"]
    f02, f15 = frequency_map(d02), frequency_map(d15)
    # Verified intermediate threshold calculated from the same selected-leg
    # populations. It is not part of the original JSON threshold set.
    f02[1.25] = {
        "count": 151,
        "share_pct": 3.406271148206632,
        "per_volatile_day": 0.6801801801801802,
        "per_5_volatile_days": 3.400900900900901,
        "dates_with_event": 120,
        "share_dates_pct": 54.054054054054056,
        "max_per_date": 5,
        "volatile_days_per_event": 1.4701986754966887,
    }
    f15[1.25] = {
        "count": 94,
        "share_pct": 1.3668750908826524,
        "per_volatile_day": 0.42342342342342343,
        "per_5_volatile_days": 2.1171171171171173,
        "dates_with_event": 80,
        "share_dates_pct": 36.03603603603604,
        "max_per_date": 4,
        "volatile_days_per_event": 2.3617021276595747,
    }
    c02, c15 = correction_map(d02), correction_map(d15)
    s02, s15 = d02["corrections"]["sample_count"], d15["corrections"]["sample_count"]

    prs = Presentation(); prs.slide_width = SLIDE_W; prs.slide_height = SLIDE_H

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide, NAVY)
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), SLIDE_W, Inches(0.12))
    band.fill.solid(); band.fill.fore_color.rgb = TEAL; band.line.fill.background()
    add_text(slide, "СРАВНИТЕЛЬНОЕ ИССЛЕДОВАНИЕ", 0.72, 0.62, 5.5, 0.35, size=12, color=TEAL, bold=True)
    add_text(slide, "ZigZag 0,2% и 0,15%", 0.72, 1.25, 10.5, 0.82, size=40, color=WHITE, bold=True)
    add_text(slide, "222 подтверждённых волатильных дня", 0.72, 2.13, 10.8, 0.55, size=25, color=WHITE, bold=True)
    add_text(slide, "Все доступные часы · Close · частота крупных ног и коррекции за 24 часа", 0.72, 2.82, 11.6, 0.4, size=17, color=RGBColor(196, 211, 226))
    add_card(slide, 0.72, 4.08, 2.75, 1.48, "0,2%", "более редкие развороты", color=TEAL)
    add_card(slide, 3.66, 4.08, 2.75, 1.48, f"{l02['count']:,}".replace(",", " "), "подтверждённых ног", color=TEAL)
    add_card(slide, 6.60, 4.08, 2.75, 1.48, "0,15%", "более чувствительный ZZ", color=ORANGE)
    add_card(slide, 9.54, 4.08, 2.75, 1.48, f"{l15['count']:,}".replace(",", " "), "подтверждённых ног", color=ORANGE)
    add_text(slide, "Si · минутные свечи · обычные дни не входят в знаменатель", 0.75, 6.85, 7.5, 0.28, size=11, color=RGBColor(162, 181, 201))

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "01 · Методика", "Единая основа для корректного сравнения", 2)
    add_card(slide, 0.72, 1.52, 2.65, 1.46, "222", "волатильные торговые даты", color=TEAL)
    add_card(slide, 3.58, 1.52, 2.65, 1.46, f"{d02['data']['selected_date_bars']:,}".replace(",", " "), "минутных баров", color=TEAL)
    add_card(slide, 6.44, 1.52, 2.65, 1.46, "5 дней", "условная волатильная неделя", color=ORANGE)
    add_card(slide, 9.30, 1.52, 2.65, 1.46, "24 часа", "окно проверки коррекции", color=GREEN)
    add_rich_lines(slide, [
        ("Цена ZigZag: ", "Close минутной свечи", TEAL),
        ("Порог разворота: ", "отдельный расчёт для 0,2% и 0,15%", TEAL),
        ("Нога: ", "абсолютное движение между соседними подтверждёнными экстремумами", TEAL),
        ("Время: ", "никакого внутридневного фильтра — все часы выбранных дат", GREEN),
        ("Граница: ", "оба экстремума в одной из 222 дат; междневные ноги исключены", RED),
        ("Коррекция: ", "касание по High–Low в следующие 24 календарных часа", ORANGE),
    ], 0.85, 3.55, 11.5, 2.55, size=16, gap=7)
    add_callout(slide, "Изменяется только порог ZigZag. Источник данных, даты, нормировка частоты и методика коррекции одинаковы.", 0.85, 6.33, 11.55, 0.52, fill=BLUE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "02 · Базовая статистика", "Как порог меняет количество и размер ног", 3)
    rows = [
        ("Подтверждённых ног", f"{l02['count']:,}".replace(",", " "), f"{l15['count']:,}".replace(",", " "), f"+{ru((l15['count']/l02['count']-1)*100,1)}%"),
        ("Ног на волатильный день", ru(l02["per_volatile_day"],2), ru(l15["per_volatile_day"],2), f"+{ru((l15['per_volatile_day']/l02['per_volatile_day']-1)*100,1)}%"),
        ("Средняя нога, пунктов", ru(l02["mean_points"],1), ru(l15["mean_points"],1), f"−{ru((1-l15['mean_points']/l02['mean_points'])*100,1)}%"),
        ("Медианная нога, пунктов", ru(l02["median_points"],1), ru(l15["median_points"],1), f"−{ru((1-l15['median_points']/l02['median_points'])*100,1)}%"),
        ("Средняя нога, %", f"{ru(l02['mean_pct'],3)}%", f"{ru(l15['mean_pct'],3)}%", f"−{ru((1-l15['mean_pct']/l02['mean_pct'])*100,1)}%"),
        ("Медианная нога, %", f"{ru(l02['median_pct'],3)}%", f"{ru(l15['median_pct'],3)}%", f"−{ru((1-l15['median_pct']/l02['median_pct'])*100,1)}%"),
        ("Максимальная нога, %", f"{ru(l02['max_pct'],2)}%", f"{ru(l15['max_pct'],2)}%", "близко"),
    ]
    add_table(slide, ["Показатель", "ZZ 0,2%", "ZZ 0,15%", "Изменение"], rows, 0.75, 1.5, 8.4, 4.75, widths=[2.5,1.4,1.4,1.3], font_size=12, first_col_bold=True)
    add_card(slide, 9.55, 1.65, 2.8, 1.45, "+55,1%", "больше ног при 0,15%", color=ORANGE)
    add_card(slide, 9.55, 3.38, 2.8, 1.45, "−22,1%", "меньше средняя нога", color=TEAL)
    add_callout(slide, "0,15% повышает чувствительность и дробит движение на большее число более коротких ног.", 9.55, 5.1, 2.8, 1.05, fill=ORANGE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "03 · Верхние группы", "Хвост распределения ног", 4)
    rows=[]
    for idx in range(len(d02["top_groups"])):
        a=d02["top_groups"][idx]; b=d15["top_groups"][idx]
        rows.append((a["segment"], f"{a['count']:,}".replace(",", " "), f"{ru(a['mean_pct'],3)}%", ru(a["entry_threshold_points"],0), f"{b['count']:,}".replace(",", " "), f"{ru(b['mean_pct'],3)}%", ru(b["entry_threshold_points"],0)))
    add_table(slide, ["Сегмент", "N · 0,2", "Ср.% · 0,2", "Порог п.", "N · 0,15", "Ср.% · 0,15", "Порог п."], rows, 0.62, 1.5, 12.1, 4.55, widths=[1.8,1.0,1.2,1.0,1.0,1.2,1.0], font_size=11, first_col_bold=True)
    add_callout(slide, f"Верхний 10%: средняя нога {ru(d02['top_groups'][3]['mean_pct'],3)}% при 0,2% против {ru(d15['top_groups'][3]['mean_pct'],3)}% при 0,15%.", 0.8, 6.25, 11.7, 0.55)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "04 · Частота", "Крупные ноги на 5 волатильных дней", 5)
    rows=[]
    for threshold in (1.0,1.25,1.5,2.0):
        a,b=f02[threshold],f15[threshold]
        rows.append((f">{ru(threshold,1)}%", a["count"], ru(a["per_5_volatile_days"],2), f"{ru(a['share_dates_pct'],1)}%", b["count"], ru(b["per_5_volatile_days"],2), f"{ru(b['share_dates_pct'],1)}%"))
    add_table(slide, ["Порог", "N · 0,2", "На 5 дней", "Доля дат", "N · 0,15", "На 5 дней", "Доля дат"], rows, 0.65, 1.55, 12.05, 2.75, widths=[1.1,1.0,1.4,1.3,1.0,1.4,1.3], font_size=13, first_col_bold=True)
    add_card(slide,0.8,4.75,2.7,1.35,f"{ru(f02[1.0]['per_5_volatile_days'],2)} / {ru(f15[1.0]['per_5_volatile_days'],2)}",">1% · 0,2 / 0,15",color=TEAL)
    add_card(slide,3.75,4.75,2.7,1.35,f"{ru(f02[1.5]['per_5_volatile_days'],2)} / {ru(f15[1.5]['per_5_volatile_days'],2)}",">1,5% · 0,2 / 0,15",color=ORANGE)
    add_card(slide,6.7,4.75,2.7,1.35,f"{ru(f02[2.0]['per_5_volatile_days'],2)} / {ru(f15[2.0]['per_5_volatile_days'],2)}",">2% · 0,2 / 0,15",color=RED)
    add_callout(slide,"Более низкий порог создаёт больше ног, но число ног, превышающих фиксированные 1–2%, становится ниже: крупное движение чаще дробится на части.",9.65,4.75,2.65,1.35,fill=BLUE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "05 · Охват дат", "В скольких волатильных днях были крупные ноги", 6)
    rows=[]
    for threshold in (1.0,1.25,1.5,2.0):
        a,b=f02[threshold],f15[threshold]
        rows.append((f">{ru(threshold,1)}%", f"{a['dates_with_event']} из 222", f"{ru(a['share_dates_pct'],1)}%", f"{a['volatile_days_per_event']:.2f}", f"{b['dates_with_event']} из 222", f"{ru(b['share_dates_pct'],1)}%", f"{b['volatile_days_per_event']:.2f}"))
    add_table(slide,["Порог","Дней · 0,2","Доля","Дней/событие","Дней · 0,15","Доля","Дней/событие"],rows,0.65,1.55,12.05,2.75,widths=[1.0,1.5,1.0,1.4,1.5,1.0,1.4],font_size=13,first_col_bold=True)
    add_rich_lines(slide,[
        ("ZZ 0,2%: ",f"нога >1% присутствовала в {ru(f02[1.0]['share_dates_pct'],1)}% выбранных дат.",TEAL),
        ("ZZ 0,15%: ",f"нога >1% присутствовала в {ru(f15[1.0]['share_dates_pct'],1)}% дат.",ORANGE),
        ("Причина: ","при меньшем разворотном пороге крупное движение чаще разделяется промежуточными экстремумами.",RED),
    ],0.85,4.85,11.55,1.55,size=16,gap=8)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "06 · Коррекция", "Единый сценарий после ноги >1%", 7)
    add_card(slide,0.75,1.52,2.55,1.42,f"{s02}","ног >1% · ZZ 0,2%",color=TEAL)
    add_card(slide,3.55,1.52,2.55,1.42,f"{s15}","ног >1% · ZZ 0,15%",color=ORANGE)
    add_card(slide,6.35,1.52,2.55,1.42,"33 / 50 / 90%","уровни возврата",color=ORANGE)
    add_card(slide,9.15,1.52,2.55,1.42,"24 часа","после экстремума",color=GREEN)
    add_rich_lines(slide,[
        ("После растущей ноги: ","Low касается уровня коррекции.",TEAL),
        ("После падающей ноги: ","High касается уровня коррекции.",TEAL),
        ("Начало отсчёта: ","конечный экстремум подтверждённой ноги.",ORANGE),
        ("Измерение: ","первое касание уровня внутри минутной свечи.",GREEN),
    ],0.85,3.65,5.7,2.15,size=16,gap=8)
    add_callout(slide,"ZigZag использует будущий подтверждающий разворот: экстремум нельзя считать известным в реальном времени в самой точке максимума или минимума.",6.9,3.65,5.3,2.15,fill=ORANGE_LIGHT)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "07 · Вероятность", "Коррекция за следующие 24 часа", 8)
    rows=[]
    for ratio in (33.0,50.0,90.0):
        a,b=c02[ratio],c15[ratio]
        rows.append((f"{ru(ratio,0)}%",f"{a['hits']} / {s02}",f"{ru(a['probability_pct'],2)}%",f"{b['hits']} / {s15}",f"{ru(b['probability_pct'],2)}%",f"{ru(b['probability_pct']-a['probability_pct'],2)} п.п."))
    add_table(slide,["Глубина","ZZ 0,2 · случаи","Вероятность","ZZ 0,15 · случаи","Вероятность","Разница"],rows,0.65,1.55,12.05,2.75,widths=[1.0,1.8,1.4,1.8,1.4,1.4],font_size=13,first_col_bold=True)
    add_card(slide,0.8,4.75,3.35,1.35,f"{ru(c02[50.0]['probability_pct'],2)}%","откат 50% · ZZ 0,2%",color=TEAL)
    add_card(slide,4.55,4.75,3.35,1.35,f"{ru(c15[50.0]['probability_pct'],2)}%","откат 50% · ZZ 0,15%",color=ORANGE)
    add_callout(slide,"Вероятности близки: смена порога сильнее влияет на число и структуру ног, чем на вероятность последующей коррекции.",8.3,4.75,3.95,1.35)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "08 · Скорость", "За сколько времени достигается коррекция", 9)
    rows=[]
    for ratio in (33.0,50.0,90.0):
        a,b=c02[ratio],c15[ratio]
        rows.append((f"{ru(ratio,0)}%",f"{ru(a['median_minutes'],1)} мин",f"{ru(a['mean_minutes']/60,2)} ч",f"{ru(a['within_2h_pct_of_sample'],1)}%",f"{ru(b['median_minutes'],1)} мин",f"{ru(b['mean_minutes']/60,2)} ч",f"{ru(b['within_2h_pct_of_sample'],1)}%"))
    add_table(slide,["Коррекция","Медиана · 0,2","Среднее · 0,2","≤2ч · 0,2","Медиана · 0,15","Среднее · 0,15","≤2ч · 0,15"],rows,0.55,1.55,12.25,2.8,widths=[1.0,1.5,1.4,1.2,1.5,1.4,1.2],font_size=12,first_col_bold=True)
    add_rich_lines(slide,[
        ("Быстрый откат: ","уровень 33% имеет медиану 30 минут при 0,2% и 23 минуты при 0,15%.",TEAL),
        ("Половинный возврат: ","медиана 78 против 74 минут — практически одинаково.",ORANGE),
        ("Глубокий возврат: ","90% требует около четырёх часов по медиане в обеих версиях.",RED),
    ],0.85,4.95,11.55,1.45,size=16,gap=8)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "09 · Направление", "Коррекция после роста и падения", 10)
    rows=[]
    for ratio in (33.0,50.0,90.0):
        a,b=c02[ratio],c15[ratio]
        rows.append((f"{ru(ratio,0)}%",f"{ru(a['up_probability_pct'],1)}%",f"{ru(a['down_probability_pct'],1)}%",f"{ru(b['up_probability_pct'],1)}%",f"{ru(b['down_probability_pct'],1)}%"))
    add_table(slide,["Коррекция","После роста · 0,2","После падения · 0,2","После роста · 0,15","После падения · 0,15"],rows,0.75,1.65,11.8,2.6,widths=[1.1,1.8,1.8,1.8,1.8],font_size=14,first_col_bold=True)
    add_callout(slide,"Для ZZ 0,2% коррекции почти симметричны. Для 0,15% откат 33% чаще после роста, а 50% — после падения; выборки всё ещё ограничены 221 крупной ногой.",0.85,4.75,11.5,0.85,fill=BLUE_LIGHT)
    add_rich_lines(slide,[
        ("Вывод: ","устойчивого сильного направленного преимущества не обнаружено.",GREEN),
        ("Практика: ","направление ноги само по себе недостаточно для выбора сценария коррекции.",ORANGE),
    ],0.9,5.85,11.3,0.75,size=15,gap=4)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "10 · Выбор порога", "Что меняется при переходе с 0,2% на 0,15%", 11)
    add_rich_lines(slide,[
        ("Чувствительность: ","0,15% создаёт 6 877 ног против 4 433 — на 55% больше.",ORANGE),
        ("Размер: ","средняя нога уменьшается с 0,483% до 0,376%.",TEAL),
        ("Крупные ноги: ","частота >1% снижается с 6,87 до 4,98 на 5 волатильных дней.",RED),
        ("Ноги >1,25%: ","3,40 на 5 дней при 0,2% против 2,12 при 0,15%.",ORANGE),
        ("Коррекция 50%: ","63,93% при 0,2% против 65,16% при 0,15% — разница небольшая.",GREEN),
        ("Интерпретация: ","0,2% лучше сохраняет цельные крупные движения; 0,15% подробнее описывает мелкую структуру.",TEAL),
    ],0.8,1.55,11.7,3.45,size=17,gap=10)
    add_card(slide,0.85,5.35,3.4,1.25,"0,2%","для анализа крупных рыночных ног",color=TEAL)
    add_card(slide,4.95,5.35,3.4,1.25,"0,15%","для более детальной структуры",color=ORANGE)
    add_callout(slide,"Если основная цель — ноги >1%, порог 0,2% выглядит практичнее: меньше шума и выше частота цельных крупных ног.",9.05,5.35,3.3,1.25)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); set_bg(slide); header(slide, "11 · Резюме", "Итог сравнительного исследования", 12)
    add_card(slide,0.8,1.55,3.5,1.48,"0,483% / 0,376%","средняя нога · 0,2 / 0,15",color=TEAL)
    add_card(slide,4.55,1.55,3.5,1.48,"6,87 / 4,98","ног >1% на 5 вол. дней",color=ORANGE)
    add_card(slide,8.3,1.55,3.5,1.48,"63,93% / 65,16%","коррекция 50% за 24 часа",color=GREEN)
    add_callout(slide,"Порог 0,15% увеличивает детализацию, но дробит крупные движения. Порог 0,2% формирует меньше ног, зато чаще сохраняет движение >1% как единый сегмент.",0.8,3.55,11.5,0.92)
    add_rich_lines(slide,[
        ("Для крупных ног: ","предпочтительнее ZigZag 0,2%.",TEAL),
        ("Для микроструктуры: ","предпочтительнее ZigZag 0,15%.",ORANGE),
        ("Общее ограничение: ","222 дня выбраны постфактум, а экстремумы ZigZag подтверждаются с задержкой.",RED),
        ("Важно: ","историческая частота не является прогнозом; комиссии и исполнение не анализируются.",RED),
    ],0.9,4.9,11.3,1.65,size=15,gap=6)

    prs.save(OUTPUT)
    print(f"Slides: {len(prs.slides)}")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    build()
