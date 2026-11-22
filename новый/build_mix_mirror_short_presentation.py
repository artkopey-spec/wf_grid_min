import math
from pathlib import Path

from openpyxl import load_workbook
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from build_mix_final_strategy_presentation import (
    BG, CYAN, GRID, MUTED, ORANGE, PANEL, PANEL_2, RED, TEAL, WHITE,
    bg, card, header, hbar, kpi, rect, text,
)


SOURCE = Path("MIX_ST_19_4_mirror_SHORT_optimization_exit_19.xlsx")
OUTPUT = Path("MIX_ST_19_4_mirror_SHORT_strategy.pptx")


def load_best_trades():
    wb = load_workbook(SOURCE, read_only=True, data_only=True)
    ws = wb["Best_Trades"]
    rows = ws.iter_rows(values_only=True)
    headers = next(rows)
    col = {name: i for i, name in enumerate(headers)}
    return [{name: row[i] for name, i in col.items()} for row in rows]


def add_equity(slide, trades, x, y, w, h):
    trades = sorted(trades, key=lambda t: t["entry_time"])
    equity = 0.0
    values = []
    for i, trade in enumerate(trades, 1):
        equity += float(trade["net_pnl_rub"]) / 1_000_000
        values.append((i, equity))
    raw = [v for _, v in values]
    lo = math.floor(min(0, min(raw)) / 5) * 5
    hi = math.ceil(max(raw) / 5) * 5
    if hi == lo:
        hi = lo + 5
    px, py, pw, ph = x + 0.50, y + 0.12, w - 0.66, h - 0.48
    for i in range(6):
        value = lo + (hi - lo) * i / 5
        yy = py + ph - ph * i / 5
        rect(slide, px, yy, pw, 0.012, GRID, False)
        text(slide, f"{value:.0f}", x, yy - 0.10, 0.38, 0.20, 8, MUTED, align=PP_ALIGN.RIGHT)
    sampled = values[::3]
    if sampled[-1] != values[-1]:
        sampled.append(values[-1])
    points = []
    for index, value in sampled:
        xx = px + pw * (index - 1) / max(1, len(values) - 1)
        yy = py + ph - ph * (value - lo) / (hi - lo)
        points.append((xx, yy))
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        line = slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
        line.line.color.rgb = CYAN; line.line.width = Pt(1.7)
    text(slide, "1", px, py + ph + 0.08, 0.30, 0.18, 8, MUTED)
    text(slide, str(len(values)), px + pw - 0.45, py + ph + 0.08, 0.45, 0.18, 8, MUTED, align=PP_ALIGN.RIGHT)


def main():
    trades = load_best_trades()
    prs = Presentation()
    prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1
    slide = prs.slides.add_slide(blank); bg(slide)
    rect(slide, 0, 0, 0.18, 7.5, CYAN, False)
    text(slide, "ЗЕРКАЛЬНАЯ СТРАТЕГИЯ", 0.86, 0.92, 5.7, 0.35, 13, CYAN, True)
    text(slide, "MIX · Short после импульса вниз", 0.84, 1.40, 7.3, 1.05, 32, WHITE, True)
    text(slide, "ST 19/4 · импульс → отскок → Short → 19:00", 0.86, 2.55, 7.0, 0.50, 18, MUTED)
    rect(slide, 0.86, 3.38, 6.48, 1.30, PANEL)
    text(slide, "> 0,5%", 1.18, 3.69, 1.35, 0.48, 26, RED, True)
    text(slide, "импульс вниз", 1.18, 4.14, 1.45, 0.24, 10, MUTED)
    text(slide, "+0,1%", 3.22, 3.69, 1.35, 0.48, 26, CYAN, True)
    text(slide, "отскок", 3.22, 4.14, 1.25, 0.24, 10, MUTED)
    text(slide, "100 MIX", 5.22, 3.69, 1.55, 0.48, 26, ORANGE, True)
    text(slide, "Short", 5.22, 4.14, 1.25, 0.24, 10, MUTED)
    rect(slide, 8.20, 1.12, 4.18, 4.95, PANEL_2)
    text(slide, "+15,47", 8.63, 1.75, 3.3, 0.78, 39, CYAN, True, PP_ALIGN.CENTER)
    text(slide, "млн ₽ · Σ +53,08%", 8.63, 2.48, 3.3, 0.35, 16, MUTED, True, PP_ALIGN.CENTER)
    rect(slide, 8.67, 3.12, 3.25, 0.025, GRID, False)
    text(slide, "PF 1,33", 8.63, 3.50, 3.3, 0.40, 23, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "Win rate 56,1%", 8.63, 4.12, 3.3, 0.35, 17, TEAL, True, PP_ALIGN.CENTER)
    text(slide, "492 сделки", 8.63, 4.72, 3.3, 0.35, 15, WHITE, True, PP_ALIGN.CENTER)
    text(slide, "Все сигналы сохранены · вечерние позиции переносятся", 0.86, 6.72, 7.0, 0.24, 11, MUTED)

    # 2
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Логика зеркальной сделки", "Первый сильный красный импульс дня; вход только после зелёного подтверждения ST", 2)
    flow = [
        ("Импульс вниз", "Красное движение ST строго больше 0,5%.", RED),
        ("Смена ST", "Красный ST переключается на зелёный — падение завершено.", TEAL),
        ("Вход Short", "Отскок +0,1% от минимума; продаём 100 MIX.", CYAN),
        ("Выход", "Ближайшие торговые 19:00 после сигнала.", ORANGE),
    ]
    for i, (ttl, body, accent) in enumerate(flow):
        card(slide, 0.72 + i * 3.10, 1.62, 2.82, 1.70, ttl, body, accent, i + 1)
        if i < 3:
            text(slide, "→", 3.49 + i * 3.10, 2.15, 0.28, 0.35, 20, MUTED, True, PP_ALIGN.CENTER)
    rect(slide, 0.75, 3.78, 11.82, 2.16, PANEL)
    points = [(1.10, 4.28), (2.15, 4.55), (3.20, 5.03), (4.25, 5.52), (5.20, 4.96), (6.15, 4.70), (7.25, 4.92), (8.55, 5.13), (9.75, 5.25), (11.68, 5.40)]
    for i in range(len(points) - 1):
        x1, y1 = points[i]; x2, y2 = points[i + 1]
        line = slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
        line.line.color.rgb = RED if i < 4 or i >= 6 else TEAL
        line.line.width = Pt(3)
    text(slide, "ИМПУЛЬС >0,5%", 1.00, 4.00, 1.70, 0.23, 9, RED, True)
    text(slide, "МИНИМУМ", 4.66, 5.54, 1.10, 0.22, 9, RED, True)
    text(slide, "+0,1% · SHORT", 5.45, 4.42, 1.58, 0.24, 10, CYAN, True)
    text(slide, "19:00", 11.05, 5.56, 0.78, 0.22, 9, ORANGE, True)
    text(slide, "Сигнал после 19:00 не удаляется: выход переносится на следующие торговые 19:00", 0.84, 6.22, 9.5, 0.30, 13, WHITE, True)

    # 3
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Подбор параметров", "110 комбинаций: импульс 0,3–1,2% и отскок 0–1,0%, шаг 0,1 п.п.", 3)
    rect(slide, 0.72, 1.56, 6.08, 4.98, PANEL)
    text(slide, "ЛУЧШИЕ КОМБИНАЦИИ · NET МЛН ₽ / Σ%", 1.02, 1.88, 4.8, 0.26, 12, MUTED, True)
    combos = [
        (">0,5% / +0,1%", 15.47, CYAN, "+53,08%"),
        (">0,5% / сразу", 15.43, TEAL, "+52,98%"),
        (">0,5% / +0,2%", 14.75, ORANGE, "+50,68%"),
        (">0,4% / +0,2%", 13.72, MUTED, "+45,36%"),
        (">0,6% / +0,1%", 13.10, RED, "+43,39%"),
    ]
    for i, (label, value, accent, pct) in enumerate(combos):
        hbar(slide, label, value, 15.47, 1.02, 2.42 + i * 0.61, 5.35, accent, f"{value:.2f} / {pct}".replace(".", ","))
    rect(slide, 7.05, 1.56, 5.57, 4.98, PANEL_2)
    text(slide, "УСТОЙЧИВАЯ ОБЛАСТЬ", 7.40, 1.90, 2.8, 0.28, 12, CYAN, True)
    text(slide, "Импульс >0,5%", 7.40, 2.48, 4.45, 0.42, 23, WHITE, True)
    text(slide, "Отскок 0–0,2%", 7.40, 3.04, 4.45, 0.42, 23, WHITE, True)
    rect(slide, 7.38, 3.75, 4.83, 0.025, GRID, False)
    text(slide, "Глубокого отката ждать не нужно:\nзелёное переключение ST уже даёт достаточное подтверждение.", 7.40, 4.16, 4.42, 0.78, 14, CYAN, True)
    text(slide, "Более осторожный вариант:\n>0,7% / +0,1% · DD −2,41 млн ₽ / Σ −11,56%", 7.40, 5.28, 4.42, 0.70, 12, MUTED)

    # 4
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Результат стратегии", "100 MIX · комиссии включены · межконтрактные переходы скорректированы", 4)
    kpi(slide, 0.72, 1.55, 2.25, "+15,47 млн ₽", "Σ +53,08%", CYAN)
    kpi(slide, 3.13, 1.55, 2.25, "56,1%", "прибыльных сделок", TEAL)
    kpi(slide, 5.54, 1.55, 2.25, "1,33", "Profit Factor", ORANGE)
    kpi(slide, 7.95, 1.55, 2.25, "+31 434 ₽", "средняя: +0,108%", CYAN)
    kpi(slide, 10.36, 1.55, 2.25, "−3,66 млн ₽", "Σ просадка: −13,15%", RED)
    rect(slide, 0.72, 3.05, 8.45, 3.47, PANEL)
    text(slide, "НАКОПЛЕННЫЙ P&L · МЛН ₽ · ИТОГ Σ +53,08%", 1.00, 3.29, 4.7, 0.25, 11, MUTED, True)
    add_equity(slide, trades, 0.92, 3.66, 7.95, 2.55)
    rect(slide, 9.42, 3.05, 3.20, 3.47, PANEL_2)
    text(slide, "ВЫБОРКА", 9.72, 3.33, 1.4, 0.24, 11, MUTED, True)
    text(slide, "630 299", 9.72, 3.75, 2.55, 0.39, 22, WHITE, True)
    text(slide, "минутных баров", 9.72, 4.14, 2.45, 0.24, 10, MUTED)
    text(slide, "21.08.2023 — 21.08.2026", 9.72, 4.62, 2.45, 0.48, 13, CYAN, True)
    text(slide, "492 сигнала\n492 сделки\n100% fill rate", 9.72, 5.25, 2.45, 0.82, 12, WHITE, True)

    # 5
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Почему оставляем вечерние сигналы", "Сравнение одинаковых параметров >0,5% / +0,1%", 5)
    rect(slide, 0.72, 1.58, 5.78, 3.28, PANEL_2)
    text(slide, "ВСЕ СИГНАЛЫ", 1.05, 1.93, 2.2, 0.28, 12, CYAN, True)
    text(slide, "+15,47 млн ₽ · Σ +53,08%", 1.05, 2.45, 4.7, 0.55, 24, WHITE, True)
    text(slide, "492 сделки · PF 1,33\nпросадка −3,66 млн ₽ / Σ −13,15%", 1.05, 3.20, 4.7, 0.72, 14, MUTED)
    rect(slide, 6.84, 1.58, 5.78, 3.28, PANEL)
    text(slide, "ТОЛЬКО СИГНАЛЫ ДО 19:00", 7.17, 1.93, 3.5, 0.28, 12, ORANGE, True)
    text(slide, "+12,02 млн ₽ · Σ +41,29%", 7.17, 2.45, 4.7, 0.55, 24, WHITE, True)
    text(slide, "462 сделки · PF 1,26\nпросадка −3,25 млн ₽ / Σ −13,15%", 7.17, 3.20, 4.7, 0.72, 14, MUTED)
    rect(slide, 0.72, 5.15, 11.90, 1.15, PANEL_2)
    text(slide, "+3,44 млн ₽ · Σ +11,79%", 1.06, 5.46, 3.15, 0.42, 19, TEAL, True)
    text(slide, "вклад 30 вечерних сигналов", 4.28, 5.49, 3.05, 0.30, 13, WHITE, True)
    text(slide, "≈ +115 тыс. ₽ / +0,393% на сделку", 7.52, 5.49, 4.60, 0.30, 12, CYAN, True)
    text(slide, "Цена преимущества — перенос позиции до следующих торговых 19:00.", 0.82, 6.62, 8.8, 0.28, 12, MUTED)

    # 6
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Профиль убытков", "Результаты оптимального варианта >0,5% / +0,1%", 6)
    kpi(slide, 0.72, 1.55, 2.85, "−159 тыс. ₽", "медианный: −0,570%", RED)
    kpi(slide, 3.77, 1.55, 2.85, "−220 тыс. ₽", "средний: −0,798%", ORANGE)
    kpi(slide, 6.82, 1.55, 2.85, "−1,10 млн ₽", "худшая: −4,417%", RED)
    kpi(slide, 9.87, 1.55, 2.75, "−3,66 млн ₽", "Σ просадка: −13,15%", CYAN)
    rect(slide, 0.72, 3.08, 7.15, 3.25, PANEL)
    text(slide, "ЧТО СОЗДАЁТ РИСК", 1.02, 3.39, 2.6, 0.26, 12, MUTED, True)
    text(slide, "1", 1.02, 4.00, 0.38, 0.28, 14, RED, True)
    text(slide, "После отскока падение не продолжается", 1.58, 3.99, 5.55, 0.30, 14, WHITE, True)
    text(slide, "2", 1.02, 4.73, 0.38, 0.28, 14, RED, True)
    text(slide, "Short превращается в ставку против нового роста", 1.58, 4.72, 5.55, 0.30, 14, WHITE, True)
    text(slide, "3", 1.02, 5.46, 0.38, 0.28, 14, RED, True)
    text(slide, "Вечерний сигнал несёт overnight-риск", 1.58, 5.45, 5.55, 0.30, 14, WHITE, True)
    rect(slide, 8.15, 3.08, 4.47, 3.25, PANEL_2)
    text(slide, "КОНТРОЛЬ", 8.50, 3.40, 1.6, 0.25, 11, CYAN, True)
    text(slide, "• фиксированный размер 100 MIX\n• отдельный лимит overnight-риска\n• не усреднять Short\n• заранее принять выход в 19:00", 8.50, 3.98, 3.60, 1.52, 13, WHITE)
    text(slide, "Стоп-параметры для этой версии отдельно не оптимизировались.", 8.50, 5.67, 3.55, 0.42, 10, MUTED)

    # 7
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Long и зеркальный Short — разные профили", "Обе стратегии используют ST 19/4 и размер 100 MIX", 7)
    rect(slide, 0.72, 1.56, 5.78, 4.95, PANEL)
    text(slide, "LONG ПОСЛЕ ИМПУЛЬСА ВВЕРХ", 1.05, 1.91, 4.6, 0.28, 12, TEAL, True)
    text(slide, ">0,7% → коррекция −0,4%", 1.05, 2.46, 4.8, 0.38, 19, WHITE, True)
    text(slide, "Выход: следующее открытие", 1.05, 3.03, 4.6, 0.30, 13, MUTED)
    text(slide, "+20,40 млн ₽ · Σ +77,51%", 1.05, 3.66, 4.8, 0.48, 22, TEAL, True)
    text(slide, "Win 59,8% · PF 1,64\n271 сделка · DD −3,97 млн ₽ / Σ −12,83%", 1.05, 4.48, 4.7, 0.82, 14, WHITE, True)
    rect(slide, 6.84, 1.56, 5.78, 4.95, PANEL_2)
    text(slide, "SHORT ПОСЛЕ ИМПУЛЬСА ВНИЗ", 7.17, 1.91, 4.6, 0.28, 12, CYAN, True)
    text(slide, ">0,5% → отскок +0,1%", 7.17, 2.46, 4.8, 0.38, 19, WHITE, True)
    text(slide, "Выход: ближайшие 19:00", 7.17, 3.03, 4.6, 0.30, 13, MUTED)
    text(slide, "+15,47 млн ₽ · Σ +53,08%", 7.17, 3.66, 4.8, 0.48, 22, CYAN, True)
    text(slide, "Win 56,1% · PF 1,33\n492 сделки · DD −3,66 млн ₽ / Σ −13,15%", 7.17, 4.48, 4.7, 0.82, 14, WHITE, True)
    text(slide, "Зеркальность касается идеи, но оптимальные пороги и время выхода различаются.", 0.84, 6.65, 10.8, 0.28, 13, WHITE, True)

    # 8
    slide = prs.slides.add_slide(blank); bg(slide)
    header(slide, "Итоговый алгоритм Short", "Максимально прибыльная конфигурация с сохранением всех сигналов", 8)
    steps = [
        ("Найти импульс", "Первый красный ST 19/4 дня строго >0,5%.", RED),
        ("Зафиксировать минимум", "Ждать переключения ST с красного на зелёный.", TEAL),
        ("Открыть Short", "Продать 100 MIX после отскока +0,1% от минимума.", CYAN),
        ("Определить выход", "До 19:00 — сегодня; после 19:00 — следующие торговые 19:00.", ORANGE),
        ("Закрыть позицию", "В 19:00 закрыть все 100 контрактов; не усреднять.", RED),
    ]
    for i, (ttl, body, accent) in enumerate(steps):
        card(slide, 0.72 + (i % 3) * 4.05, 1.56 + (i // 3) * 2.15, 3.75, 1.75, ttl, body, accent, i + 1)
    rect(slide, 8.82, 3.71, 3.80, 1.75, PANEL_2)
    text(slide, "ПЕРЕД РЕАЛЬНЫМ ЗАПУСКОМ", 9.10, 4.00, 3.20, 0.28, 11, ORANGE, True)
    text(slide, "• вневыборочная проверка\n• реальное проскальзывание\n• overnight-лимит\n• проверка ликвидности", 9.10, 4.43, 3.10, 0.87, 11, WHITE)
    rect(slide, 0.72, 5.83, 11.90, 0.60, CYAN)
    text(slide, "Ключевая идея: после сильного падения продавать короткий отскок и фиксировать результат в 19:00.", 0.92, 5.98, 11.50, 0.28, 15, BG, True, PP_ALIGN.CENTER)
    text(slide, "Σ% = сумма net-доходностей к номиналу на входе; не доходность на маржу. Бэктест не гарантирует будущий результат.", 0.75, 6.72, 11.2, 0.22, 9, MUTED)

    prs.save(OUTPUT)
    print(f"saved={OUTPUT.resolve()} slides={len(prs.slides)} trades={len(trades)}")


if __name__ == "__main__":
    main()
