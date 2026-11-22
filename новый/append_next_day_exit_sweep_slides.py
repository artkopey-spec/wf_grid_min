from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN

from append_short_impulse_volume_slide import BG, GRID, MUTED, ORANGE, PANEL, TEAL, TEXT, box, rgb, text


ROOT = Path.cwd()
BASE = ROOT / "mix_short_impulse_volume_test" / "MIX_ST_19_4_FINAL_AUDITED_RVOL_MIN30_ADD_POLICY.pptx"
OUT_DIR = ROOT / "mix_next_day_exit_sweep"
OUT = OUT_DIR / "MIX_ST_19_4_FINAL_AUDITED_NEXT_DAY_EXIT_SWEEP.pptx"


def add_strategy_slide(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(BG)
    text(slide, "Позднее закрытие следующего дня не улучшает стратегию", 0.62, 0.34, 11.7, 0.42, 23, TEXT, True)
    text(slide, "Long10+5 · стоп 1,5% и выход слабой позиции через 30 минут сохранены", 0.62, 0.82, 11.5, 0.27, 11, MUTED)

    headers = [("Время выхода", 0.76, 3.0), ("Результат", 3.90, 1.35), ("PF", 5.38, 1.05), ("Max DD", 6.56, 1.45), ("OOS", 8.15, 1.35), ("OOS PF", 9.62, 1.15)]
    for value, x, w in headers:
        text(slide, value, x, 1.38, w, 0.23, 10, MUTED, True, PP_ALIGN.CENTER if x > 3 else PP_ALIGN.LEFT)
    rows = [
        ("Первая минута сессии", "76,736", "3,928", "−3,627", "24,241", "3,793"),
        ("После 60 торговых минут", "69,472", "3,360", "−4,328", "18,969", "3,027"),
        ("09:01", "71,749", "3,613", "−4,011", "19,941", "3,004"),
        ("10:01", "65,576", "3,257", "−4,258", "16,960", "2,791"),
        ("12:00", "66,463", "2,901", "−4,028", "17,028", "2,341"),
        ("15:00", "64,211", "2,611", "−5,318", "16,308", "2,067"),
        ("Закрытие следующего дня", "63,214", "2,246", "−8,369", "17,441", "1,900"),
    ]
    for i, row in enumerate(rows):
        y = 1.78 + i * 0.55
        fill = TEAL if i == 0 else (PANEL if i % 2 else BG)
        box(slide, 0.62, y - 0.07, 10.30, 0.48, fill, radius=False, line=fill)
        fg = BG if i == 0 else TEXT
        secondary = BG if i == 0 else MUTED
        text(slide, row[0], 0.78, y, 3.00, 0.24, 10.5, fg, i == 0)
        for value, x, w in zip(row[1:], [3.90, 5.38, 6.56, 8.15, 9.62], [1.35, 1.05, 1.45, 1.35, 1.15]):
            text(slide, value, x, y, w, 0.24, 10.5, secondary, True, PP_ALIGN.CENTER)

    box(slide, 11.18, 1.34, 1.52, 4.44, PANEL, line=GRID)
    text(slide, "ЛУЧШИЙ ВЫХОД", 11.35, 1.65, 1.18, 0.42, 10, TEAL, True, PP_ALIGN.CENTER)
    text(slide, "Первая минутная свеча следующей сессии", 11.32, 2.38, 1.24, 0.90, 14, TEXT, True, PP_ALIGN.CENTER)
    text(slide, "Позднее ожидание снижает прибыль на 6–18% и PF на 8–43%", 11.33, 3.73, 1.22, 1.05, 10, MUTED, False, PP_ALIGN.CENTER)
    text(slide, "На закрытии DD более чем удваивается", 11.34, 5.05, 1.20, 0.48, 9.5, ORANGE, True, PP_ALIGN.CENTER)

    box(slide, 0.62, 6.14, 12.08, 0.78, TEAL, line=TEAL)
    text(slide, "Результат одинаков в train и OOS: преимущество возникает к началу следующей сессии, а дальнейшее удержание его постепенно отдаёт. Текущий ранний выход оставить.", 0.90, 6.30, 11.52, 0.40, 11.5, BG, True, PP_ALIGN.CENTER)


def add_hypothesis_slide(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(BG)
    text(slide, "Импульс действительно предсказывает положительный следующий день", 0.62, 0.34, 12.0, 0.42, 23, TEXT, True)
    text(slide, "Markout всех 124 первых входов без цензурирования стопом: Close следующего дня против цены Long10", 0.62, 0.82, 12.0, 0.27, 11, MUTED)

    headers = [("Момент", 0.78, 2.7), ("Положит., full", 3.58, 1.65), ("Средний full", 5.35, 1.55), ("Положит., OOS", 7.07, 1.65), ("Средний OOS", 8.86, 1.55)]
    for value, x, w in headers:
        text(slide, value, x, 1.42, w, 0.24, 10, MUTED, True, PP_ALIGN.CENTER if x > 3 else PP_ALIGN.LEFT)
    rows = [
        ("Первая минута сессии", "64,5%", "+0,568%", "54,8%", "+0,341%"),
        ("После 60 торговых минут", "66,9%", "+0,509%", "61,9%", "+0,182%"),
        ("09:01", "62,9%", "+0,509%", "50,0%", "+0,187%"),
        ("10:01", "64,5%", "+0,480%", "57,1%", "+0,131%"),
        ("12:00", "66,1%", "+0,456%", "59,5%", "+0,131%"),
        ("15:00", "59,7%", "+0,448%", "50,0%", "+0,191%"),
        ("Закрытие следующего дня", "57,3%", "+0,478%", "52,4%", "+0,167%"),
    ]
    for i, row in enumerate(rows):
        y = 1.82 + i * 0.53
        if i % 2 == 0:
            box(slide, 0.62, y - 0.06, 9.95, 0.46, PANEL, radius=False)
        text(slide, row[0], 0.78, y, 2.70, 0.23, 10.5, TEXT, i in (0, 1))
        for value, x, w, color in zip(row[1:], [3.58, 5.35, 7.07, 8.86], [1.65, 1.55, 1.65, 1.55], [TEAL, TEXT, TEAL, TEXT]):
            text(slide, value, x, y, w, 0.23, 10.5, color, True, PP_ALIGN.CENTER)

    box(slide, 10.82, 1.42, 1.88, 4.36, PANEL, line=GRID)
    text(slide, "ГИПОТЕЗА", 11.06, 1.72, 1.40, 0.24, 10, ORANGE, True, PP_ALIGN.CENTER)
    text(slide, "Подтверждается по направлению", 11.03, 2.24, 1.46, 0.68, 14, TEXT, True, PP_ALIGN.CENTER)
    text(slide, "Но эффект OOS слабее: средний markout +0,13…+0,34%", 11.02, 3.35, 1.48, 0.90, 10, MUTED, False, PP_ALIGN.CENTER)
    text(slide, "Положительное закрытие не означает, что долгое удержание оптимально", 11.03, 4.68, 1.46, 0.78, 9.5, ORANGE, True, PP_ALIGN.CENTER)

    box(slide, 0.62, 6.14, 12.08, 0.78, ORANGE, line=ORANGE)
    text(slide, "Вывод: сигнал содержит переносимый overnight-эффект, но основная премия реализуется рано. К закрытию дня доля положительных исходов и риск-скорректированная прибыль снижаются.", 0.90, 6.30, 11.52, 0.40, 11.5, BG, True, PP_ALIGN.CENTER)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prs = Presentation(BASE)
    add_strategy_slide(prs)
    add_hypothesis_slide(prs)
    prs.save(OUT)
    print(OUT)
    print("slides", len(prs.slides))


if __name__ == "__main__":
    main()
