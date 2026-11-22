from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN

from append_short_impulse_volume_slide import BG, GRID, MUTED, ORANGE, PANEL, TEAL, TEXT, box, rgb, text


ROOT = Path.cwd()
BASE = ROOT / "mix_add5_output" / "MIX_ST_19_4_FINAL_AUDITED_LONG10_ADD5.pptx"
OUT_DIR = ROOT / "mix_short_impulse_volume_test"
OUT = OUT_DIR / "MIX_ST_19_4_FINAL_AUDITED_RVOL_MIN30_ADD_POLICY.pptx"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prs = Presentation(BASE)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = rgb(BG)

    text(slide, "Добавление только после импульса длиннее 30 минут", 0.62, 0.34, 11.2, 0.42, 23, TEXT, True)
    text(slide, "≤30 минут: только Long10 · >30 минут: add при RVol < P60 = 2,922962 и подтверждённом пробое", 0.62, 0.82, 12.0, 0.28, 11, MUTED)

    cards = [
        ("8", "импульсов ≤30 мин", ORANGE),
        ("2", "прежних add отменены", TEAL),
        ("18 / 24", "длительность, минут", TEXT),
        ("0", "изменённых сделок OOS", ORANGE),
    ]
    for i, (value, label, color) in enumerate(cards):
        x = 0.62 + 3.05 * i
        box(slide, x, 1.25, 2.78, 0.96, PANEL, line=GRID)
        text(slide, value, x + 0.15, 1.43, 2.48, 0.33, 19, color, True, PP_ALIGN.CENTER)
        text(slide, label, x + 0.10, 1.82, 2.58, 0.20, 9.3, MUTED, False, PP_ALIGN.CENTER)

    text(slide, "Long10 + 5", 0.72, 2.52, 2.6, 0.28, 15, TEXT, True)
    text(slide, "Текущий P60", 4.13, 2.54, 1.85, 0.23, 10, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "P60 + >30 мин", 6.23, 2.54, 1.95, 0.23, 10, TEAL, True, PP_ALIGN.CENTER)
    text(slide, "Изменение", 8.43, 2.54, 1.65, 0.23, 10, MUTED, True, PP_ALIGN.CENTER)
    rows = [
        ("Добавлений", "34", "32", "−2"),
        ("Сумма результата", "75,776 п.п.", "76,736 п.п.", "+0,960"),
        ("Средняя сделка", "0,611%", "0,619%", "+0,008 п.п."),
        ("Profit Factor", "3,788", "3,928", "+0,140"),
        ("Max DD", "−3,627 п.п.", "−3,627 п.п.", "без изменения"),
        ("OOS результат", "24,241 п.п.", "24,241 п.п.", "без изменения"),
        ("OOS PF", "3,793", "3,793", "без изменения"),
    ]
    for i, row in enumerate(rows):
        y = 2.93 + i * 0.43
        if i % 2 == 0:
            box(slide, 0.62, y - 0.05, 9.70, 0.43, PANEL, radius=False)
        text(slide, row[0], 0.78, y, 3.05, 0.22, 10.3, TEXT, i in (1, 5))
        text(slide, row[1], 4.03, y, 2.05, 0.22, 10.3, MUTED, False, PP_ALIGN.CENTER)
        text(slide, row[2], 6.18, y, 2.05, 0.22, 10.3, TEAL, True, PP_ALIGN.CENTER)
        color = TEAL if row[3].startswith("+") else MUTED
        text(slide, row[3], 8.33, y, 1.85, 0.22, 10.3, color, True, PP_ALIGN.CENTER)

    box(slide, 10.58, 2.52, 2.12, 3.16, PANEL, line=GRID)
    text(slide, "УДАЛЁННЫЕ ADD", 10.77, 2.80, 1.74, 0.22, 10, ORANGE, True, PP_ALIGN.CENTER)
    text(slide, "18 минут", 10.78, 3.28, 1.72, 0.28, 14, TEXT, True, PP_ALIGN.CENTER)
    text(slide, "+0,228 п.п. после удаления +5", 10.83, 3.63, 1.62, 0.44, 9.5, MUTED, False, PP_ALIGN.CENTER)
    text(slide, "24 минуты", 10.78, 4.25, 1.72, 0.28, 14, TEXT, True, PP_ALIGN.CENTER)
    text(slide, "+0,732 п.п. после удаления +5", 10.83, 4.60, 1.62, 0.44, 9.5, MUTED, False, PP_ALIGN.CENTER)
    text(slide, "Обе сделки — train", 10.82, 5.24, 1.64, 0.22, 9.5, ORANGE, True, PP_ALIGN.CENTER)

    box(slide, 0.62, 6.14, 12.08, 0.78, TEAL, line=TEAL)
    text(slide, "Результат улучшился без роста исторической просадки. Правило логически чище, но статистически ещё не подтверждено OOS: после split не было ни одного добавления, отменённого условием ≤30 минут.", 0.90, 6.30, 11.52, 0.40, 11.5, BG, True, PP_ALIGN.CENTER)

    prs.save(OUT)
    print(OUT)
    print("slides", len(prs.slides))


if __name__ == "__main__":
    main()
