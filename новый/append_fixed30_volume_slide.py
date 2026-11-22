from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN

from append_short_impulse_volume_slide import BG, GRID, MUTED, ORANGE, PANEL, RED, TEAL, TEXT, box, rgb, text


ROOT = Path.cwd()
BASE = ROOT / "mix_add5_output" / "MIX_ST_19_4_FINAL_AUDITED_LONG10_ADD5.pptx"
OUT_DIR = ROOT / "mix_short_impulse_volume_test"
OUT = OUT_DIR / "MIX_ST_19_4_FINAL_AUDITED_RVOL_FIXED30_RULE.pptx"


def metric_row(slide, y, label, current, known, literal, stripe=False):
    if stripe:
        box(slide, 0.62, y - 0.05, 12.08, 0.43, PANEL, radius=False)
    text(slide, label, 0.78, y, 3.05, 0.22, 10.3, TEXT, label in ("Сумма результата", "OOS сумма"))
    text(slide, current, 3.90, y, 2.05, 0.22, 10.3, MUTED, False, PP_ALIGN.CENTER)
    text(slide, known, 6.08, y, 2.15, 0.22, 10.3, TEAL, True, PP_ALIGN.CENTER)
    text(slide, literal, 8.43, y, 2.15, 0.22, 10.3, ORANGE, True, PP_ALIGN.CENTER)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prs = Presentation(BASE)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = rgb(BG)

    text(slide, "RVol применяется только при импульсе ≥30 минут", 0.62, 0.34, 10.7, 0.42, 23, TEXT, True)
    text(slide, "Фиксированный логический порог; остальные правила стратегии и P60 не меняются", 0.62, 0.82, 11.5, 0.27, 11, MUTED)

    for i, (value, label, color) in enumerate([
        ("<30 мин", "RVol считается ненадёжным", ORANGE),
        ("3 сделки", "имеют пробой при запрете add", TEXT),
        ("2", "известный RVol", TEAL),
        ("1", "RVol отсутствует", RED),
    ]):
        x = 0.62 + 3.05 * i
        box(slide, x, 1.25, 2.78, 0.96, PANEL, line=GRID)
        text(slide, value, x + 0.15, 1.43, 2.48, 0.33, 19, color, True, PP_ALIGN.CENTER)
        text(slide, label, x + 0.10, 1.82, 2.58, 0.20, 9.3, MUTED, False, PP_ALIGN.CENTER)

    text(slide, "Long10 + 5", 0.72, 2.53, 2.8, 0.28, 15, TEXT, True)
    text(slide, "Текущий P60", 3.90, 2.55, 2.05, 0.23, 10, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "<30; NaN не добавлять", 6.08, 2.55, 2.15, 0.23, 10, TEAL, True, PP_ALIGN.CENTER)
    text(slide, "<30; фильтра нет совсем", 8.43, 2.55, 2.15, 0.23, 10, ORANGE, True, PP_ALIGN.CENTER)
    rows = [
        ("Добавлений", "34", "36", "37"),
        ("Сумма результата", "75,776 п.п.", "76,178 п.п.", "72,603 п.п."),
        ("Средняя сделка", "0,611%", "0,614%", "0,586%"),
        ("Profit Factor", "3,788", "3,750", "3,492"),
        ("Max DD", "−3,627 п.п.", "−4,316 п.п.", "−6,598 п.п."),
        ("OOS сумма", "24,241 п.п.", "23,553 п.п.", "19,978 п.п."),
        ("OOS PF", "3,793", "3,528", "2,829"),
    ]
    for i, row in enumerate(rows):
        metric_row(slide, 2.94 + i * 0.43, *row, stripe=i % 2 == 0)

    box(slide, 10.84, 2.53, 1.86, 3.06, PANEL, line=GRID)
    text(slide, "2 СДЕЛКИ С ИЗВЕСТНЫМ RVOL", 11.00, 2.78, 1.54, 0.48, 10, TEXT, True, PP_ALIGN.CENTER)
    text(slide, "Train", 11.08, 3.42, 1.38, 0.22, 10, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "+1,091 п.п.", 11.03, 3.72, 1.48, 0.28, 15, TEAL, True, PP_ALIGN.CENTER)
    text(slide, "OOS", 11.08, 4.22, 1.38, 0.22, 10, MUTED, True, PP_ALIGN.CENTER)
    text(slide, "−0,689 п.п.", 11.03, 4.52, 1.48, 0.28, 15, ORANGE, True, PP_ALIGN.CENTER)
    text(slide, "Эффект не повторился вне обучения", 11.01, 5.00, 1.52, 0.42, 9.5, MUTED, False, PP_ALIGN.CENTER)

    box(slide, 0.62, 6.14, 12.08, 0.78, ORANGE, line=ORANGE)
    text(slide, "Вывод: логика порога 30 минут понятна, но тест её не подтверждает — OOS ухудшается. Буквальное отключение фильтра при <30 особенно опасно при отсутствующем RVol. Оставить текущий P60 до появления большей выборки.", 0.90, 6.30, 11.52, 0.40, 11.5, BG, True, PP_ALIGN.CENTER)

    prs.save(OUT)
    print(OUT)
    print("slides", len(prs.slides))


if __name__ == "__main__":
    main()
