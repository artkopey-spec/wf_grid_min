# ТЗ: миграция writer backend `excel_tester.py` с openpyxl на xlsxwriter

Дата: 2026-06-25

Статус: готово для подготовки implementation plan.

## 1. Цель

Ускорить запись Excel-отчетов tester exporter за счет перехода writer backend
в `donor/supertrend_optimizer/io/excel_tester.py` с `openpyxl` на
`xlsxwriter`.

Основная мотивация: на больших данных, например 333k баров / 77k сделок,
текущая запись `Trades_*` и связанных листов через `openpyxl` может занимать
30+ минут. Главный источник задержек - per-cell formatting через
`ws.cell(...).number_format`.

Цель миграции - убрать openpyxl-specific writer API и per-cell formatting из
tester exporter, сохранив структуру и содержимое XLSX-отчета.

Важно: это не задача на удаление `openpyxl` из проекта. `openpyxl` остается
нужен для чтения XLSX в тестах и для существующего walk-forward exporter.

## 2. Scope

### Входит в scope

Основной файл:

- `donor/supertrend_optimizer/io/excel_tester.py`

Вспомогательный файл:

- `donor/supertrend_optimizer/io/excel_format_helpers.py`

Тесты:

- `donor TESTER/tests/test_phase2_wp_t7_excel_export.py`
- `donor TESTER/tests/test_xlsx_cycle_sheet.py`
- `donor TESTER/tests/test_diagnostics_v2_export.py`
- `donor TESTER/tests/test_tester_config_sheet.py`
- `donor TESTER/tests/test_phase2_tester_determinism_normalized.py`

### Не входит в scope

- `donor/supertrend_optimizer/excel_walk_forward.py`
- миграция всего проекта с `openpyxl` на `xlsxwriter`
- удаление зависимости `openpyxl`
- изменение логики бэктеста, фильтров, сигналов, сделок или метрик
- изменение имен листов
- изменение порядка листов
- изменение имен или порядка колонок
- добавление новых пользовательских XLSX-фич, если их не было до миграции

## 3. Dependency contract

Добавить `xlsxwriter` в зависимости проекта.

Локально используется pandas `3.0.3`. Для этой версии корректный API:

```python
pd.ExcelWriter(
    output_path,
    engine="xlsxwriter",
    engine_kwargs={"options": {"strings_to_urls": False}},
    datetime_format="yyyy-mm-dd hh:mm:ss",
)
```

Не использовать прямой аргумент `options=...`, так как он не соответствует
локальной сигнатуре `pd.ExcelWriter`.

`strings_to_urls=False` обязателен: на больших файлах автоматическое
распознавание URL в строках дает лишнюю нагрузку и может неожиданно менять
типы ячеек.

## 4. Совместимость и XLSX-контракт

После миграции должны сохраниться:

- имена листов;
- порядок листов;
- порядок колонок;
- значения ячеек;
- наличие autofilter на тех же таблицах, где он был;
- позиция autofilter на той же логической таблице;
- `freeze_panes` в diagnostics v2;
- ширины колонок diagnostics v2;
- datetime-значения как Excel datetime, читаемые через `openpyxl`;
- conditional formatting в `Signals`;
- возможность читать результат через `openpyxl.load_workbook`;
- возможность читать таблицы через `pd.read_excel`.

Не требуется:

- byte-identical XLSX;
- полное совпадение XML-структуры;
- полное совпадение строкового `number_format` со старой openpyxl-строкой
  `"YYYY-MM-DD HH:MM:SS"`.

Причина: xlsxwriter может записывать формат даты-времени как
`yyyy-mm-dd hh:mm:ss` или задавать его через column/default style. Тесты должны
проверять семантику datetime, а не только старую openpyxl-specific строку
формата конкретной ячейки.

## 5. Текущие openpyxl-зависимости

### 5.1 Writer backend

В `excel_tester.py` есть два writer entrypoint:

- `export_tester_results`
- `export_equal_blocks_results`

Оба сейчас используют:

```python
pd.ExcelWriter(output_path, engine="openpyxl")
```

### 5.2 Worksheet API

Внутри sheet writer-ов используется `writer.sheets[name]`.

Это сохраняется: `writer.sheets[name]` также работает с xlsxwriter, но
возвращает `xlsxwriter.Worksheet`, а не openpyxl worksheet.

### 5.3 Autofilter

Openpyxl API:

```python
ws.auto_filter.ref = "A1:Z1"
```

Нужно заменить на xlsxwriter API:

```python
ws.autofilter(first_row, first_col, last_row, last_col)
```

Все координаты xlsxwriter 0-based и включительные.

### 5.4 Per-cell datetime formatting

Openpyxl API:

```python
ws.cell(row=row_idx, column=col_idx).number_format = "YYYY-MM-DD HH:MM:SS"
```

Нужно заменить на формат writer-level / column-level:

```python
fmt = writer.book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
ws.set_column(col_idx, col_idx, width, fmt)
```

Или использовать `datetime_format` на уровне `pd.ExcelWriter`.

Цель: не должно остаться циклов по строкам только ради форматирования ячеек.

### 5.5 Conditional formatting

Openpyxl классы:

- `ColorScaleRule`
- `CellIsRule`
- `PatternFill`

Нужно заменить на:

- `ws.conditional_format(...)`
- `writer.book.add_format(...)`

### 5.6 Freeze panes и ширины колонок

Openpyxl:

```python
ws.freeze_panes = "A2"
ws.column_dimensions["A"].width = 12
```

Xlsxwriter:

```python
ws.freeze_panes(1, 0)
ws.set_column(col_idx, col_idx, width)
```

## 6. Общий дизайн миграции

### 6.1 Writer factory

Добавить локальный helper в `excel_tester.py`:

```python
def _create_xlsx_writer(output_path: str) -> pd.ExcelWriter:
    return pd.ExcelWriter(
        output_path,
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_urls": False}},
        datetime_format="yyyy-mm-dd hh:mm:ss",
    )
```

Использовать helper в:

- `export_tester_results`
- `export_equal_blocks_results`

### 6.2 Autofilter helper

Добавить helper:

```python
def _set_autofilter(
    ws: Any,
    header_row: int,
    n_cols: int,
    n_data_rows: int = 0,
) -> None:
    if n_cols <= 0:
        return
    last_row = header_row + max(n_data_rows, 0)
    ws.autofilter(header_row, 0, last_row, n_cols - 1)
```

Контракт:

- `header_row` - 0-based строка заголовка;
- `n_data_rows` - количество строк данных под заголовком;
- пустая таблица с заголовками получает autofilter только на строку заголовка;
- формула `last_row = header_row + n_data_rows` корректна, потому что
  `last_row` в xlsxwriter включительный.

### 6.3 Datetime column helper

Добавить helper:

```python
def _set_datetime_columns(
    writer: pd.ExcelWriter,
    ws: Any,
    df: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    width: float | None = None,
) -> None:
    fmt = writer.book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    df_columns = list(df.columns)
    for col in columns:
        if col not in df_columns:
            continue
        col_idx = df_columns.index(col)
        ws.set_column(col_idx, col_idx, width, fmt)
```

Нюанс: если pandas/xlsxwriter уже применяет writer-level datetime format, этот
helper все равно допустим как явная фиксация формата на уровне колонки.

### 6.4 Не использовать ручную генерацию букв колонок

Не писать helper через `chr(...)`: это работает только для `A-Z`.

Там, где xlsxwriter принимает координаты, использовать числовые координаты.
Если где-то потребуется A1 notation, использовать:

```python
from xlsxwriter.utility import xl_col_to_name, xl_range, xl_rowcol_to_cell
```

## 7. Изменения по функциям

### 7.1 `export_tester_results`

Заменить:

```python
with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
```

на:

```python
with _create_xlsx_writer(output_path) as writer:
```

Вызовы `to_excel` оставить без изменения, кроме мест, где один и тот же лист
получает несколько DataFrame и требуется явный `startcol=0`.

### 7.2 `export_equal_blocks_results`

Аналогично заменить writer backend на `_create_xlsx_writer(output_path)`.

Учитывать, что equal-blocks путь использует `_write_trades_sheet`, а значит
должен сохранить datetime formatting для `Trades_*`.

### 7.3 `_write_cycle_sheet`

Заменить:

- импорт `openpyxl.utils.get_column_letter`;
- `ws.auto_filter.ref = ...`;
- per-cell datetime formatting;
- guard на `_EXCEL_PER_CELL_FORMAT_LIMIT`.

На:

- `cycle_df.to_excel(...)`;
- `_set_autofilter(ws, 0, len(cycle_df.columns), len(cycle_df))`;
- `_set_datetime_columns(...)` для колонок:
  - `"Начало цикла"` в текущем display encoding проекта;
  - `"Конец цикла"` в текущем display encoding проекта.

Важно: не менять имена колонок и не исправлять encoding в рамках этой задачи.

### 7.4 `_write_false_start_sheet`

Функция пишет два блока на один лист:

- summary block;
- main false-start table.

Требования:

- добавить `startcol=0` при записи второго DataFrame;
- фильтр ставить на header row основной таблицы, а не summary block;
- убрать per-cell datetime formatting.

Логика:

```python
main_startrow = len(summary_df) + 2
false_start_df.to_excel(
    writer,
    sheet_name=FALSE_START_SHEET_NAME,
    index=False,
    startrow=main_startrow,
    startcol=0,
)
_set_autofilter(ws, main_startrow, len(false_start_df.columns), len(false_start_df))
```

Datetime columns:

- `Entry Time`
- `Exit Time`
- `Signal Time`

Использовать display names из существующих maps, не hardcode там, где уже есть
константы.

### 7.5 `_write_signals_sheet`

Самая сложная функция миграции.

Заменить:

- `openpyxl.utils.get_column_letter`;
- `ColorScaleRule`;
- `CellIsRule`;
- `PatternFill`;
- `ws.conditional_formatting.add`;
- `ws.auto_filter.ref`;
- per-cell datetime formatting.

Autofilter:

```python
_set_autofilter(ws, 0, len(signals_df.columns), len(signals_df))
```

Datetime:

- применить datetime format для `Signal Time`.

Conditional formatting:

1. Для `signal_body_pct` и `signal_range_pct`:

```python
ws.conditional_format(first_row, col, last_row, col, {
    "type": "3_color_scale",
    "min_color": "#63BE7B",
    "mid_color": "#FFEB84",
    "max_color": "#F8696B",
})
```

2. Для ratio columns:

- `signal_body_pct_median_ratio`;
- `signal_range_pct_median_ratio`.

Правила:

- `> 2` - red fill;
- `> 1.6` - yellow fill.

Форматы:

```python
ratio_strong_fmt = writer.book.add_format({"bg_color": "#F8696B"})
ratio_soft_fmt = writer.book.add_format({"bg_color": "#FFEB84"})
```

3. Для `t1_return_pct`, `t2_return_pct`, `t3_return_pct`:

- `< 0` - soft red fill.

Формат:

```python
neg_fmt = writer.book.add_format({"bg_color": "#FFC7CE"})
```

Диапазоны задавать через числовые координаты xlsxwriter. Для пустого
`signals_df` conditional formatting не применять.

### 7.6 `_format_trades_datetime`

Не удалять без замены: функция используется и в обычном tester export, и в
equal-blocks path.

Требуется заменить внутреннюю реализацию на xlsxwriter-compatible helper.

Целевой вид:

```python
def _format_trades_datetime(
    writer: pd.ExcelWriter,
    trades_df: pd.DataFrame,
    worksheet: Any,
) -> None:
    _set_datetime_columns(
        writer,
        worksheet,
        trades_df,
        (
            TRADES_DISPLAY_NAMES["entry_time"],
            TRADES_DISPLAY_NAMES["exit_time"],
        ),
    )
```

После изменения обновить оба call site:

- в `export_tester_results`;
- в `_write_trades_sheet` для equal-blocks.

### 7.7 `_write_tester_config_sheet`

Заменить:

```python
ws.auto_filter.ref = ...
```

на:

```python
_set_autofilter(ws, 0, len(cfg_df.columns), len(cfg_df))
```

### 7.8 `_write_diagnostics_v2_sheet`

Заменить:

- `openpyxl.utils.get_column_letter`;
- `ws.freeze_panes = "A2"`;
- `ws.auto_filter.ref = ...`;
- `ws.column_dimensions[...]`.

На:

```python
ws.freeze_panes(1, 0)
_set_autofilter(ws, 0, len(out.columns), len(out))
ws.set_column(col_idx, col_idx, width)
```

Сохранить проверку лимита Excel:

```python
max_excel_rows = 1_048_576
```

### 7.9 `_write_filter_diagnostics_100_sheet`

Не удалять `_FILTER_DIAGNOSTICS_100_MAX_ROWS` в этой миграции.

Причины:

- xlsxwriter быстрее, но Excel row limit остается;
- размер XLSX и memory footprint остаются рисками;
- повышение/удаление лимита требует отдельного benchmark и отдельного решения.

Функция не содержит openpyxl-specific API и может остаться без функциональных
изменений.

### 7.10 `_write_filters_summary_sheet`

Функция пишет два DataFrame на один лист:

- `params_df`;
- `period_df` со `startrow`.

Openpyxl-specific API нет, но для xlsxwriter нужно явно зафиксировать
`startcol=0` у второго блока:

```python
period_df.to_excel(
    writer,
    sheet_name=sheet,
    index=False,
    startrow=period_start,
    startcol=0,
)
```

Autofilter в рамках этой задачи не добавлять, если его не было в текущем
поведении.

### 7.11 `_write_zigzag_trigger_events_sheet`

Функция пишет один DataFrame через `to_excel` и не использует openpyxl-specific
API.

Изменений не требует.

Autofilter не добавлять в рамках этой миграции: в текущем XLSX-контракте его
нет, а добавление будет отдельным user-facing изменением.

### 7.12 Summary sheet с filter block

В `export_tester_results` Summary может получить второй DataFrame:

```python
filter_block_df.to_excel(
    writer,
    sheet_name="Summary",
    index=False,
    startrow=filter_start_row,
)
```

Добавить явный `startcol=0`.

### 7.13 Equal-blocks Summary aggregate block

В `export_equal_blocks_results` Summary получает второй DataFrame:

```python
agg_df.to_excel(writer, sheet_name="Summary", index=False, startrow=start_row)
```

Добавить явный `startcol=0`.

## 8. `excel_format_helpers.py`

Не переименовывать и не удалять:

```python
apply_openpyxl_column_formats
```

Причина: функция используется в `donor/supertrend_optimizer/excel_walk_forward.py`,
который вне scope.

Допустимо добавить рядом новую функцию для xlsxwriter:

```python
def apply_xlsxwriter_column_formats(
    ws: Any,
    df: pd.DataFrame,
    workbook: Any,
    *,
    first_col: int = 0,
) -> None:
    ...
```

Требования к новой функции:

- использовать существующую классификацию колонок;
- numeric columns форматировать через `ws.set_column`;
- форматы создавать через `workbook.add_format({"num_format": fmt})`;
- не трогать legacy `apply_openpyxl_column_formats`.

Если в `excel_tester.py` эта функция не понадобится на первом этапе, ее можно
не добавлять. Главное требование: не ломать legacy helper.

## 9. Что удалить

Удалить после перевода всех relevant call sites:

- `_EXCEL_PER_CELL_FORMAT_LIMIT`;
- условия вида `if len(...) <= _EXCEL_PER_CELL_FORMAT_LIMIT`;
- условия вида `if len(...) > _EXCEL_PER_CELL_FORMAT_LIMIT`;
- комментарии, описывающие per-cell openpyxl formatting как текущий механизм.

Не удалять:

- `_FILTER_DIAGNOSTICS_100_MAX_ROWS`.

## 10. Тестовые изменения

### 10.1 Тесты с прямым `pd.ExcelWriter(..., engine="openpyxl")`

В тестах, которые напрямую вызывают sheet writer helpers, заменить writer на
xlsxwriter-compatible writer.

Пример:

```python
with pd.ExcelWriter(
    path,
    engine="xlsxwriter",
    engine_kwargs={"options": {"strings_to_urls": False}},
    datetime_format="yyyy-mm-dd hh:mm:ss",
) as writer:
    ...
```

Можно использовать локальный test helper, чтобы не дублировать аргументы.

### 10.2 Assertions на `cell.number_format`

Переписать openpyxl-specific assertions вида:

```python
assert ws["A2"].number_format == "YYYY-MM-DD HH:MM:SS"
assert ws["B2"].number_format == "YYYY-MM-DD HH:MM:SS"
```

на семантические проверки:

- значение ячейки является `datetime.datetime`;
- значение соответствует ожидаемой дате-времени;
- при необходимости допускается xlsxwriter format string
  `yyyy-mm-dd hh:mm:ss`, но exact old openpyxl string не является обязательным.

Пример:

```python
from datetime import datetime

assert isinstance(ws["A2"].value, datetime)
assert ws["A2"].value == datetime(2026, 1, 1, 1, 2, 3)
```

### 10.3 Autofilter assertions

Сохранить проверки `ws.auto_filter.ref` там, где они фиксируют существующий
контракт.

Особенно важно:

- `cycle`: `A1:W1` для пустого листа или header-only тестов;
- `false start`: фильтр должен стоять на header row основной таблицы, а не на
  summary block;
- diagnostics v2: filter ref должен покрывать header + data rows.

### 10.4 Diagnostics v2 formatting assertions

Сохранить проверки:

- `ws.freeze_panes == "A2"` при чтении через openpyxl;
- `ws.auto_filter.ref is not None`;
- ширина колонки `A` не меньше 8.

После записи через xlsxwriter openpyxl должен читать эти свойства.

## 11. Минимальный regression suite

Запустить:

```powershell
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
python -m pytest "donor TESTER/tests/test_diagnostics_v2_export.py" -q
python -m pytest "donor TESTER/tests/test_tester_config_sheet.py" -q
python -m pytest "donor TESTER/tests/test_phase2_tester_determinism_normalized.py" -q
```

Если времени мало, минимальный smoke перед расширенной регрессией:

```powershell
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
python -m pytest "donor TESTER/tests/test_diagnostics_v2_export.py" -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
```

## 12. Acceptance criteria

Миграция считается успешной, если:

1. `export_tester_results` использует `xlsxwriter`.
2. `export_equal_blocks_results` использует `xlsxwriter`.
3. В `excel_tester.py` больше нет writer-side зависимостей:
   - `openpyxl.utils.get_column_letter`;
   - `ColorScaleRule`;
   - `CellIsRule`;
   - `PatternFill`;
   - `ws.cell(...).number_format`.
4. В `excel_tester.py` больше нет per-cell datetime formatting loops.
5. `_EXCEL_PER_CELL_FORMAT_LIMIT` удален.
6. `_FILTER_DIAGNOSTICS_100_MAX_ROWS` сохранен.
7. `apply_openpyxl_column_formats` не переименован и не удален.
8. `excel_walk_forward.py` не требует изменений в рамках этой задачи.
9. Все листы tester export сохраняют прежние имена и порядок.
10. Все колонки tester export сохраняют прежние имена и порядок.
11. XLSX читается через `openpyxl.load_workbook`.
12. XLSX читается через `pd.read_excel`.
13. Autofilter сохранен на тех же логических таблицах.
14. Diagnostics v2 сохраняет freeze panes и ширины колонок.
15. `Signals` сохраняет conditional formatting.
16. Тесты из regression suite проходят или имеют документированное, связанное
    только с XLSX-format-string отличие.

## 13. Риски и митигации

| Риск | Вес | Митигация |
|---|---:|---|
| Неверный API `pd.ExcelWriter(..., options=...)` | высокий | Использовать `engine_kwargs={"options": ...}` |
| `xlsxwriter` отсутствует в окружении | высокий | Добавить dependency и проверить импорт |
| Exact `number_format` assertions упадут | высокий | Переписать тесты на semantic datetime checks |
| Сломается equal-blocks formatting | средний | Не удалять `_format_trades_datetime` без xlsxwriter-замены |
| Фильтр попадет на wrong row в `false start` | средний | Использовать `main_startrow` как 0-based header row |
| Сломается `excel_walk_forward.py` | средний | Не переименовывать legacy helper |
| Conditional formatting будет синтаксически неверным | средний | Использовать numeric coordinate API xlsxwriter |
| Большой `FilterDiagnostics_100` станет тяжелым | средний | Сохранить `_FILTER_DIAGNOSTICS_100_MAX_ROWS` |
| Multiple `to_excel` на один лист поведет себя engine-specific | средний | Добавить явный `startcol=0` во все вторые/следующие блоки |

## 14. Входные данные для implementation plan

Перед написанием плана нужно зафиксировать:

1. Где именно добавить dependency `xlsxwriter`:
   - только `pyproject.toml`;
   - отдельный requirements/lock, если он есть вне текущего корня;
   - документация запуска, если проект не устанавливается как package.
2. Нужна ли новая функция `apply_xlsxwriter_column_formats` уже в первой
   реализации или достаточно локальных helper-ов в `excel_tester.py`.
3. Нужно ли добавлять отдельный performance smoke/benchmark как acceptance step
   или ограничиться функциональными тестами.
4. Допустим ли semantic rewrite тестов datetime formatting без отдельного
   approval, так как это изменение тестового контракта, а не бизнес-логики.
5. Нужно ли запускать walk-forward тесты как дополнительную защиту от случайной
   поломки legacy helper-а.

## 15. Рекомендуемая структура implementation plan

План реализации должен идти маленькими шагами:

1. Добавить dependency `xlsxwriter` и проверить импорт.
2. Добавить writer factory и helper-ы xlsxwriter в `excel_tester.py`.
3. Перевести простые листы:
   - `Tester_Config`;
   - diagnostics v2;
   - `cycle`.
4. Перевести multi-block листы:
   - `false start`;
   - `Summary` filter block;
   - `filters_summary`;
   - equal-blocks Summary aggregate block.
5. Перевести Trades datetime formatting в обоих export paths.
6. Перевести `Signals` conditional formatting.
7. Удалить `_EXCEL_PER_CELL_FORMAT_LIMIT` и связанные guards.
8. Обновить тесты на xlsxwriter writer и semantic datetime assertions.
9. Запустить targeted regression suite.
10. При необходимости добавить короткий performance smoke.

