"""Append ST majority-amplitude ZigZag findings to the existing presentation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pptx import Presentation
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
    add_callout,
    add_card,
    add_rich_lines,
    add_table,
    add_text,
    set_bg,
)


DEFAULT_SOURCE = Path(r"D:\Si\минута\Si_ZigZag_0p2_vs_0p15_222_volatile_days.pptx")
DEFAULT_DATA = Path("results_tester_st_batch_20260828/st80_4_global_zz0p2_majority_threshold_comparison_09_19.json")
DEFAULT_OUTPUT = Path("results_tester_st_batch_20260828/Si_ZigZag_0p2_vs_0p15_222_volatile_days_with_ST_majority.pptx")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def ru(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def header(slide, section: str, title: str, page: int) -> None:
    add_text(slide, section.upper(), 0.55, 0.28, 5.7, 0.25, size=10, color=TEAL, bold=True)
    add_text(slide, title, 0.55, 0.58, 12.0, 0.55, size=27, color=NAVY, bold=True)
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.55), Inches(1.22), Inches(12.2), Inches(0.025))
    line.fill.solid(); line.fill.fore_color.rgb = GRID; line.line.fill.background()
    add_text(slide, "ST 80/4 · глобальный ZZ 0,2% · 222 волатильных дня · 09:00–19:00", 0.58, 7.12, 10.4, 0.2, size=9, color=MUTED)
    add_text(slide, f"{page:02d}", 12.1, 7.08, 0.6, 0.24, size=10, color=MUTED, bold=True, align=PP_ALIGN.RIGHT)


def main() -> None:
    args = parse_args()
    data = json.loads(args.data.read_text(encoding="utf-8"))
    rows_by_threshold = {row["Leg Threshold Strictly Greater %"]: row for row in data["comparison"]}
    prs = Presentation(args.source)
    blank = prs.slide_layouts[6]

    # Slide 13: methodology and scope.
    slide = prs.slides.add_slide(blank); set_bg(slide)
    header(slide, "12 · Новая атрибуция", "Каким ST-сегментом сформирована глобальная ZZ-нога", 13)
    add_card(slide, 0.72, 1.50, 2.65, 1.35, "0,2%", "порог глобального ZigZag", color=TEAL)
    add_card(slide, 3.58, 1.50, 2.65, 1.35, "80 / 4", "ATR / multiplier SuperTrend", color=ORANGE)
    add_card(slide, 6.44, 1.50, 2.65, 1.35, "2 679", "завершённых ST-сегментов", color=GREEN)
    add_card(slide, 9.30, 1.50, 2.65, 1.35, "222", "выбранные волатильные даты", color=TEAL)
    add_rich_lines(slide, [
        ("Глобальный ZZ: ", "не перезапускается при смене цвета SuperTrend.", TEAL),
        ("Рост ноги: ", "каждый новый максимум Close добавляет амплитуду текущему ST-сегменту.", GREEN),
        ("Падение ноги: ", "каждый новый минимум Close распределяется аналогично.", RED),
        ("Победитель: ", "нога назначается единственному сегменту, набравшему строго больше 50% амплитуды.", ORANGE),
        ("Без большинства: ", "статус NO_MAJORITY; принудительного назначения и двойного счёта нет.", RED),
        ("Фильтр: ", "оба флипа сегмента-победителя в одной выбранной дате и в окне 09:00–19:00.", TEAL),
    ], 0.82, 3.30, 11.65, 2.75, size=15, gap=7)
    add_callout(slide, "Новый блок дополняет исходные 12 слайдов: здесь единица анализа — сегмент ST 80/4, а не только дата или самостоятельная ZZ-нога за все часы.", 0.82, 6.25, 11.65, 0.58, fill=BLUE_LIGHT)

    # Slide 14: exact threshold comparison.
    slide = prs.slides.add_slide(blank); set_bg(slide)
    header(slide, "13 · Частота крупных ног", "Строгие пороги и частота внутри ST-сегментов", 14)
    table_rows = []
    for threshold in (1.00, 1.25, 1.50, 1.75, 2.00):
        row = rows_by_threshold[threshold]
        table_rows.append((
            f">{ru(threshold, 2)}%",
            f"{row['Assigned Legs']}",
            ru(row["Mean Legs per Segment"], 4),
            ru(row["Segments per Leg"], 1),
            f"{ru(row['Active Segment Share %'], 2)}%",
            ru(row["Selected Days per Leg"], 2),
        ))
    add_table(
        slide,
        ["Нога", "Количество", "Среднее / сегмент", "Сегментов / ногу", "Активных сегментов", "Дней / ногу"],
        table_rows,
        0.62, 1.48, 12.10, 3.35,
        widths=[1.10, 1.25, 1.65, 1.55, 1.85, 1.35], font_size=12, first_col_bold=True,
    )
    add_card(slide, 0.82, 5.22, 3.25, 1.18, "1 / 9,4", "нога >1% · сегментов", color=TEAL)
    add_card(slide, 4.43, 5.22, 3.25, 1.18, "1 / 34,3", "нога >1,5% · сегментов", color=ORANGE)
    add_card(slide, 8.04, 5.22, 3.25, 1.18, "1 / 134", "нога >2% · сегментов", color=RED)
    add_callout(slide, "Все границы строгие: нога ровно 1,50% не входит в группу >1,50%.", 0.82, 6.55, 11.47, 0.40, fill=ORANGE_LIGHT)

    # Slide 15: takeaways and segment geometry.
    slide = prs.slides.add_slide(blank); set_bg(slide)
    header(slide, "14 · Выводы", "Как редеют ноги и меняется активный ST-сегмент", 15)
    add_card(slide, 0.75, 1.48, 3.45, 1.32, "10,23%", "сегментов содержат ногу >1%", color=TEAL)
    add_card(slide, 4.45, 1.48, 3.45, 1.32, "2,87%", "сегментов содержат ногу >1,5%", color=ORANGE)
    add_card(slide, 8.15, 1.48, 3.45, 1.32, "0,75%", "сегментов содержат ногу >2%", color=RED)
    add_rich_lines(slide, [
        ("Частота: ", "повышение порога примерно на 0,25 п.п. почти вдвое сокращает число ног.", TEAL),
        ("Редкие экстремумы: ", ">1,75% и >2% не повторялись дважды внутри одного сегмента.", RED),
        ("Конечный сегмент: ", "победитель совпадает с ним в 96,8% ног >1% и в 100% ног >1,75%.", GREEN),
        ("Длительность: ", "средний активный сегмент растёт с 88 минут для >1% до 119 минут для >2%.", ORANGE),
        ("Диапазон ST: ", "среднее расширение активного сегмента увеличивается с 1,39% до 2,28%.", TEAL),
        ("Повторяемость: ", "при >1% только 9 из 274 активных сегментов содержали больше одной ноги.", RED),
    ], 0.82, 3.30, 11.65, 2.55, size=15, gap=7)
    add_callout(slide, "Практический смысл: крупная нога — редкий режим длинного и широкого ST-сегмента; историческая частота не является готовым сигналом входа.", 0.82, 6.20, 11.65, 0.62, fill=BLUE_LIGHT)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(args.output)
    print(f"Slides: {len(prs.slides)}")
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
