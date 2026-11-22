"""Append the close-confirmed ST impulse-exit finding to the 222-day deck."""

from __future__ import annotations

import argparse
from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN

from build_st80_volatile_222_presentation import (
    BLUE_LIGHT,
    GREEN,
    NAVY,
    ORANGE,
    ORANGE_LIGHT,
    RED,
    TEAL,
    add_callout,
    add_card,
    add_header,
    add_table,
    add_text,
    set_bg,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def set_first_run(shape, text: str) -> None:
    paragraph = shape.text_frame.paragraphs[0]
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run().text = text


def main() -> None:
    args = parse_args()
    prs = Presentation(args.input)
    if any("ИМПУЛЬС 1%" in shape.text.upper() for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text")):
        raise ValueError("The impulse-exit slide is already present")

    methodology = prs.slides[-1]
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide)
    add_header(slide, "14 · Фиксация прибыли", "Импульс 1%: удержание до флипа выгоднее", 15)

    add_card(slide, 0.68, 1.46, 2.65, 1.38, "161", "сегмент достиг 1%", color=TEAL, note="6,01% из 2 679 сегментов")
    add_card(slide, 3.50, 1.46, 2.65, 1.38, "58,4%", "ранний выход лучше", color=GREEN, note="по частоте среди достигших 1%")
    add_card(slide, 6.32, 1.46, 2.65, 1.38, "−0,021%", "среднее преимущество", color=RED, note="ранний выход минус удержание")
    add_card(slide, 9.14, 1.46, 2.65, 1.38, "−3,43 п.п.", "изменение результата", color=RED, note="по всей выборке сегментов")

    rows = [
        ("0,50%", "525 · 19,60%", "56,8%", "−28,37 п.п.", "хуже"),
        ("1,00%", "161 · 6,01%", "58,4%", "−3,43 п.п.", "держать"),
        ("1,25%", "99 · 3,70%", "63,6%", "+2,72 п.п.", "только OOS-тест"),
        ("1,75%", "31 · 1,16%", "80,6%", "+4,32 п.п.", "малая выборка"),
    ]
    add_table(
        slide,
        ["Порог", "Достигнут: n · доля", "Ранний выход лучше", "Δ результата", "Вывод"],
        rows,
        0.72,
        3.18,
        11.88,
        1.95,
        widths=[1.05, 2.15, 2.0, 1.75, 2.05],
        font_size=11,
        first_col_bold=True,
    )

    add_callout(
        slide,
        "Практическое правило: флип → вход → движение достигло 1% → позицию не закрывать, ждать следующего флипа.",
        0.72,
        5.38,
        7.82,
        0.88,
        fill=BLUE_LIGHT,
        color=NAVY,
    )
    add_callout(
        slide,
        "1,25% — исследовательский кандидат, но не готовый параметр: первая половина −2,31 п.п., вторая +5,03 п.п.",
        8.76,
        5.38,
        3.84,
        0.88,
        fill=ORANGE_LIGHT,
        color=NAVY,
    )
    add_text(
        slide,
        "Почему: ранняя фиксация выигрывает чаще, но редкие продолжительные движения дают удержанию больший суммарный результат.",
        0.78,
        6.42,
        11.6,
        0.34,
        size=11,
        color=ORANGE,
        bold=True,
        align=PP_ALIGN.CENTER,
    )

    # Move the appended slide immediately before the methodology slide.
    slide_ids = prs.slides._sldIdLst
    new_slide_id = slide_ids[-1]
    slide_ids.remove(new_slide_id)
    slide_ids.insert(len(slide_ids) - 1, new_slide_id)

    # The methodology slide shifts from page 15 to page 16.
    set_first_run(methodology.shapes[0], "15 · МЕТОДИКА")
    set_first_run(methodology.shapes[4], "16")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(args.output)
    print(f"Saved {args.output}; slides={len(prs.slides)}")


if __name__ == "__main__":
    main()
