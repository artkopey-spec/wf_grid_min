"""Create a fully Russian copy of the volatility-validation workbook."""

from __future__ import annotations

from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    PROJECT_ROOT
    / "results_tester_st_batch_20260828"
    / "0010_10_st_28_2p4_20260828_121845_895_20072_"
    "st_flip_amplitudes_intraday_only_sessions_volatility_top35_validation.xlsx"
)
OUTPUT = SOURCE.with_name(f"{SOURCE.stem}_RU.xlsx")
BLUE = "1F4E78"
WHITE = "FFFFFF"


SHEET_NAMES = {
    "Summary": "Сводка_порогов",
    "Methodology": "Методика_сессий",
    "Sessions_Over_0.383": "Сессии_свыше_0.383",
    "Sessions_Over_0.5": "Сессии_свыше_0.5",
    "Sessions_Over_0.6": "Сессии_свыше_0.6",
    "Sessions_Over_0.7": "Сессии_свыше_0.7",
    "Sessions_Over_0.8": "Сессии_свыше_0.8",
    "Sessions_Over_0.9": "Сессии_свыше_0.9",
    "Sessions_Over_1.0": "Сессии_свыше_1.0",
    "Movements_Over_0.383": "Движения_свыше_0.383",
    "Volatility_Validation_0.8": "Проверка_волатильности",
    "Volatility_Summary": "Сводка_волатильности",
    "Volatility_Criteria": "Критерии_волатильности",
}


TRANSLATIONS = {
    # Generic headers and values.
    "Parameter": "Параметр",
    "Value": "Значение",
    "Metric": "Показатель",
    "Interpretation": "Интерпретация",
    "Criterion": "Критерий",
    "Formula": "Формула",
    "Purpose": "Назначение",
    "GREEN": "ЗЕЛЕНЫЙ",
    "RED": "КРАСНЫЙ",
    "STRONG": "СИЛЬНО ПОДТВЕРЖДЕНА",
    "CONFIRMED": "ПОДТВЕРЖДЕНА",
    "PARTIAL": "ЧАСТИЧНО ПОДТВЕРЖДЕНА",
    "NOT CONFIRMED": "НЕ ПОДТВЕРЖДЕНА",
    # Threshold summary.
    "Threshold Condition": "Условие порога",
    "Sessions": "Торговых дат",
    "Share of All Trading Dates %": "Доля всех торговых дат, %",
    "Movements": "Движений",
    "GREEN Movements": "Зеленых движений",
    "RED Movements": "Красных движений",
    "Maximum Amplitude %": "Максимальная амплитуда, %",
    "First Session": "Первая дата",
    "Last Session": "Последняя дата",
    "> 0.383%": "> 0,383%",
    "> 0.5%": "> 0,5%",
    "> 0.6%": "> 0,6%",
    "> 0.7%": "> 0,7%",
    "> 0.8%": "> 0,8%",
    "> 0.9%": "> 0,9%",
    "> 1.0%": "> 1,0%",
    # Methodology.
    "Source amplitudes": "Источник амплитуд",
    "OHLC source": "Источник OHLC",
    "All neighboring-flip segments": "Всего сегментов между соседними флипами",
    "Intraday-only segments": "Внутридневных сегментов",
    "Overnight segments excluded": "Исключено ночных переносов",
    "All trading dates in data.csv": "Всего торговых дат в data.csv",
    "Intraday rule": "Правило внутридневного сегмента",
    "Overnight handling": "Обработка ночных переносов",
    "Amplitude": "Амплитуда",
    "Session assignment": "Привязка к сессии",
    "Start Flip Time and End Flip Time must have the same calendar date": "Начальный и конечный флип должны находиться в одной календарной дате",
    "A segment crossing a calendar date boundary is excluded in full; it is not clipped": "Сегмент, пересекающий календарную ночь, исключается целиком и не обрезается",
    "max(High) - min(Low) over all bars of one uninterrupted ST-color segment": "максимальный High минус минимальный Low по всем барам одного непрерывного сегмента одного цвета ST",
    "The common calendar date of the start and end flips": "Общая календарная дата начального и конечного флипа",
    # Session sheets.
    "Session Date": "Дата сессии",
    "First Movement Start": "Начало первого движения",
    "Last Movement End": "Конец последнего движения",
    "Mean Amplitude %": "Средняя амплитуда, %",
    "Median Amplitude %": "Медианная амплитуда, %",
    "Largest Movement ID": "ID крупнейшего движения",
    "Largest Movement Color": "Цвет крупнейшего движения",
    "Largest Movement Start": "Начало крупнейшего движения",
    "Largest Movement End": "Конец крупнейшего движения",
    "Largest Movement Points": "Крупнейшее движение, пунктов",
    "Largest Movement Bars": "Крупнейшее движение, баров",
    "Movements > 0.383%": "Движений > 0,383%",
    "Movements > 0.5%": "Движений > 0,5%",
    "Movements > 0.6%": "Движений > 0,6%",
    "Movements > 0.7%": "Движений > 0,7%",
    "Movements > 0.8%": "Движений > 0,8%",
    "Movements > 0.9%": "Движений > 0,9%",
    "Movements > 1.0%": "Движений > 1,0%",
    # Movement detail.
    "Movement ID": "ID движения",
    "ST Color": "Цвет ST",
    "Start Flip Time": "Время начального флипа",
    "End Flip Time": "Время конечного флипа",
    "Bars Between Flips": "Баров между флипами",
    "Amplitude Points": "Амплитуда, пунктов",
    "Amplitude %": "Амплитуда, %",
    # Validation headers.
    "ST Movements > 0.8%": "ST-движений > 0,8%",
    "ST Maximum Amplitude %": "Максимальная ST-амплитуда, %",
    "Daily Open": "Открытие дня",
    "Daily High": "Максимум дня",
    "Daily Low": "Минимум дня",
    "Daily Close": "Закрытие дня",
    "Daily Range Points": "Дневной диапазон, пунктов",
    "Daily Range %": "Дневной диапазон, %",
    "Daily Range Percentile": "Процентиль дневного диапазона",
    "True Range %": "Истинный диапазон, %",
    "True Range Percentile": "Процентиль истинного диапазона",
    "Realized Volatility %": "Реализованная волатильность, %",
    "Realized Volatility Percentile": "Процентиль реализованной волатильности",
    "NATR(14) %": "NATR(14), %",
    "NATR(14) Percentile": "Процентиль NATR(14)",
    "Daily Volume": "Дневной объем",
    "Volume Percentile": "Процентиль объема",
    "Daily Range > 0.8%": "Дневной диапазон > 0,8%",
    "Range Above Median": "Диапазон выше медианы",
    "Realized Vol Above Median": "Реализованная волатильность выше медианы",
    "NATR14 Above Median": "NATR(14) выше медианы",
    "Broad Criteria Passed": "Пройдено широких критериев",
    "Broad Confirmation": "Широкое подтверждение",
    "Range Top 35%": "Диапазон в верхних 35%",
    "Realized Vol Top 35%": "Реализованная волатильность в верхних 35%",
    "NATR14 Top 35%": "NATR(14) в верхних 35%",
    "Top 35% Criteria Passed": "Пройдено критериев верхних 35%",
    "Top 35% Confirmation": "Подтверждение верхних 35%",
    # Volatility summary.
    "Selected ST >0.8% dates": "Отобрано дат со ST-движением >0,8%",
    "Dates under validation": "Даты, проходящие проверку",
    "Daily Range >0.8%": "Дневной диапазон >0,8%",
    "Absolute intraday-range confirmation": "Абсолютное подтверждение внутридневного диапазона",
    "Broad confirmed (>=2 of 3 above median)": "Широко подтверждены: минимум 2 из 3 выше медианы",
    "87.19% of selected dates": "87,19% отобранных дат",
    "Broad not confirmed": "Не получили широкого подтверждения",
    "Fewer than two metrics above the market median": "Менее двух показателей выше рыночной медианы",
    "Top 35% confirmed (>=2 of 3 at or above P65)": "Подтверждены как верхние 35%: минимум 2 из 3 не ниже P65",
    "69.38% of selected dates": "69,38% отобранных дат",
    "Top 35% not confirmed": "Не подтверждены как верхние 35%",
    "Fewer than two metrics in the market's upper 35%": "Менее двух показателей находятся в верхних 35% рынка",
    "All trading dates": "Всего торговых дат",
    "Percentiles calculated across all dates": "Процентили рассчитаны по всем торговым датам",
    "Market P50 Daily Range %": "Рыночный P50 дневного диапазона, %",
    "Median trading day": "Медианный торговый день",
    "Market P65 Daily Range %": "Рыночный P65 дневного диапазона, %",
    "Market P65 Realized Volatility %": "Рыночный P65 реализованной волатильности, %",
    "Market P65 NATR(14) %": "Рыночный P65 NATR(14), %",
    "Upper-35% threshold": "Граница верхних 35%",
    "Upper-35% regime threshold": "Граница режима верхних 35%",
    # Criteria sheet.
    "Daily Range %": "Дневной диапазон, %",
    "(Daily High - Daily Low) / Daily Open * 100": "(Максимум дня - минимум дня) / открытие дня * 100",
    "Full intraday price excursion": "Полный внутридневной размах цены",
    "max(H-L, |H-prev close|, |L-prev close|) / prev close * 100": "макс(H-L, |H-предыдущее закрытие|, |L-предыдущее закрытие|) / предыдущее закрытие * 100",
    "Range including the opening gap; diagnostic only, not used in score": "Диапазон с учетом гэпа открытия; диагностический показатель, в итоговый счет не входит",
    "Realized Volatility %": "Реализованная волатильность, %",
    "sqrt(sum(one-minute log return^2)) * 100": "корень из суммы квадратов минутных логарифмических доходностей * 100",
    "Accumulated intraday price variability": "Накопленная внутридневная изменчивость цены",
    "mean(True Range points, 14 dates) / Daily Close * 100": "средний истинный диапазон за 14 дат / закрытие дня * 100",
    "Whether the date belongs to an elevated volatility regime": "Показывает принадлежность даты к повышенному режиму волатильности",
    "Broad confirmation": "Широкое подтверждение",
    ">=2 of Range, Realized Vol and NATR14 at or above P50": "минимум 2 из 3: диапазон, реализованная волатильность и NATR(14) не ниже P50",
    "Volatility above a typical trading date": "Волатильность выше типичного торгового дня",
    "Top 35% confirmation": "Подтверждение верхних 35%",
    ">=2 of Range, Realized Vol and NATR14 at or above P65": "минимум 2 из 3: диапазон, реализованная волатильность и NATR(14) не ниже P65",
    "Volatility in the market's upper 35%": "Волатильность в верхних 35% рынка",
    "STRONG / CONFIRMED / PARTIAL / NOT CONFIRMED": "СИЛЬНО ПОДТВЕРЖДЕНА / ПОДТВЕРЖДЕНА / ЧАСТИЧНО / НЕ ПОДТВЕРЖДЕНА",
    "3 / 2 / 1 / 0 passed criteria": "пройдено 3 / 2 / 1 / 0 критериев",
    "Transparent status; no date is forced to pass": "Прозрачный статус: ни одна дата не получает подтверждение принудительно",
}


def translate_workbook(workbook) -> None:
    for old_name, new_name in SHEET_NAMES.items():
        if old_name in workbook.sheetnames:
            workbook[old_name].title = new_name

    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value in TRANSLATIONS:
                    cell.value = TRANSLATIONS[cell.value]

        # Keep Excel table column metadata synchronized with translated headers.
        for table in sheet.tables.values():
            header_values = [cell.value for cell in sheet[1]]
            for table_column, header in zip(table.tableColumns, header_values):
                table_column.name = str(header)


def add_confirmed_sheet(workbook) -> int:
    source = workbook["Проверка_волатильности"]
    headers = [cell.value for cell in source[1]]
    column = {name: index for index, name in enumerate(headers)}
    selected_headers = [
        "Дата сессии",
        "Подтверждение верхних 35%",
        "Пройдено критериев верхних 35%",
        "ST-движений > 0,8%",
        "Максимальная ST-амплитуда, %",
        "Дневной диапазон, %",
        "Процентиль дневного диапазона",
        "Реализованная волатильность, %",
        "Процентиль реализованной волатильности",
        "NATR(14), %",
        "Процентиль NATR(14)",
        "Диапазон в верхних 35%",
        "Реализованная волатильность в верхних 35%",
        "NATR(14) в верхних 35%",
    ]

    title = "Подтвержденные_0.8"
    if title in workbook.sheetnames:
        del workbook[title]
    sheet = workbook.create_sheet(title, 0)
    sheet.append(selected_headers)
    statuses = {"СИЛЬНО ПОДТВЕРЖДЕНА", "ПОДТВЕРЖДЕНА"}
    count = 0
    for row in source.iter_rows(min_row=2, values_only=True):
        if row[column["Подтверждение верхних 35%"]] not in statuses:
            continue
        sheet.append([row[column[header]] for header in selected_headers])
        count += 1

    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 52
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for column_index in range(1, sheet.max_column + 1):
        sheet.column_dimensions[sheet.cell(1, column_index).column_letter].width = 25
    sheet.column_dimensions["A"].width = 14
    for row_index in range(2, sheet.max_row + 1):
        sheet.cell(row_index, 1).number_format = "yyyy-mm-dd"
        for column_index, header in enumerate(selected_headers, start=1):
            if header.endswith(", %"):
                sheet.cell(row_index, column_index).number_format = '0.0000"%"'
            elif header.startswith("Процентиль"):
                sheet.cell(row_index, column_index).number_format = "0.00"
        status_cell = sheet.cell(row_index, 2)
        status_cell.fill = PatternFill(
            "solid",
            fgColor="C6EFCE" if status_cell.value == "СИЛЬНО ПОДТВЕРЖДЕНА" else "D9EAF7",
        )

    table = Table(displayName="ConfirmedVolatileDates08", ref=sheet.dimensions)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)
    return count


def main() -> None:
    workbook = load_workbook(SOURCE)
    translate_workbook(workbook)
    confirmed_count = add_confirmed_sheet(workbook)
    workbook.save(OUTPUT)
    print(f"Confirmed dates sheet: {confirmed_count} rows")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
