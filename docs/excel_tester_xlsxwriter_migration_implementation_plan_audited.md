# План реализации: миграция `excel_tester.py` с openpyxl writer на xlsxwriter

Дата: 2026-06-25

Статус: пересобран после архитектурного аудита, готов к реализации.

Документ-истина для реализации: этот implementation plan. Если он
противоречит `docs/tz_excel_tester_xlsxwriter_migration.md`, приоритет имеет
этот файл. Известное противоречие: ТЗ в разделах 7.3, 7.4 и 7.7 передает
`n_data_rows=len(df)` для `cycle`, `false start` и `Tester_Config`; для этих
листов это ошибочно. Их текущий XLSX-контракт - header-only autofilter.

## 1. Цель

Ускорить tester XLSX export за счет перевода writer backend в
`donor/supertrend_optimizer/io/excel_tester.py` с `openpyxl` на `xlsxwriter`,
убрав writer-side openpyxl API и per-cell datetime formatting loops.

Миграция не должна менять бизнес-логику, состав листов, порядок листов, имена
листов, порядок колонок, значения ячеек и gating optional sheets.

Важно: это не миграция всего проекта и не удаление `openpyxl`. `openpyxl`
остается нужен для чтения XLSX в тестах и для walk-forward exporter.

## 2. Scope

Основной файл:

- `donor/supertrend_optimizer/io/excel_tester.py`

Файл, который нельзя ломать:

- `donor/supertrend_optimizer/io/excel_format_helpers.py`

Вне scope:

- `donor/supertrend_optimizer/excel_walk_forward.py`
- `wf_grid/export/xlsx_writer.py`
- `wf_grid/export/bucket_sheet_writer.py`
- `donor zigzag/supertrend_optimizer/io/excel_tester.py`
- удаление `openpyxl` из проекта
- миграция всех Excel writer-ов репозитория
- изменение XLSX user-facing контракта сверх engine-level различий

## 3. Dependency contract

Перед изменением writer backend нужно принять явное решение, где фиксируется
runtime dependency `xlsxwriter`.

Текущее состояние:

- в корне есть `pyproject.toml`, но там только pytest-настройки;
- `requirements*.txt` в корне нет;
- локально `xlsxwriter` отсутствует;
- локально используется pandas `3.0.3`.

Рекомендуемое решение для первой реализации:

1. Добавить минимальный `requirements.txt`, если проект запускается как набор
   scripts без package install.
2. Зафиксировать в нем как минимум:
   - `xlsxwriter`
3. Не переносить все зависимости проекта в `pyproject.toml` в рамках этой
   задачи, чтобы не расширять scope.

Если будет принято решение не заводить requirements-файл, нужно обязательно
добавить run/install note в docs и явно указать `pip install xlsxwriter`.
Но это слабее, чем машинно проверяемая фиксация.

Writer API для pandas 3.0.3:

```python
pd.ExcelWriter(
    output_path,
    engine="xlsxwriter",
    engine_kwargs={"options": {"strings_to_urls": False}},
    datetime_format="yyyy-mm-dd hh:mm:ss",
)
```

Не использовать прямой `options=...`.

Preflight после фиксации зависимости:

```powershell
python -c "import pandas as pd, xlsxwriter; print(pd.__version__); print(xlsxwriter.__version__)"
```

## 4. XLSX compatibility contract

После миграции должны сохраниться:

- имена листов;
- порядок листов;
- имена и порядок колонок;
- значения ячеек;
- gating optional sheets;
- возможность читать XLSX через `openpyxl.load_workbook`;
- возможность читать таблицы через `pd.read_excel`;
- autofilter на тех же логических таблицах;
- freeze panes и ширины колонок diagnostics v2;
- conditional formatting на `Signals`;
- datetime-значения как Excel datetime, читаемые через openpyxl.

Не требуется:

- byte-identical XLSX;
- полное совпадение XML-структуры;
- exact old openpyxl `number_format == "YYYY-MM-DD HH:MM:SS"`.

Сознательное engine-level изменение: intraday guard снимается. Раньше datetime
format применялся только если первый timestamp имел ненулевое время
(`hour/minute/second != 0`). После миграции datetime format применяется ко всем
целевым datetime-колонкам независимо от time component. Значения остаются теми
же; меняется только форматирование daily midnight timestamps.

Тесты datetime должны проверять семантику:

- значение ячейки является `datetime`;
- значение равно ожидаемому timestamp без timezone;
- format string допускает xlsxwriter-вариант `yyyy-mm-dd hh:mm:ss`.

## 5. Главные архитектурные решения

### 5.1 Локальные helper-ы вместо нового shared helper

В первой реализации не добавлять `apply_xlsxwriter_column_formats`.
Достаточно локальных helper-ов в `excel_tester.py`.

Причина:

- задача узко про tester exporter;
- legacy `apply_openpyxl_column_formats` используется walk-forward exporter-ом;
- shared helper увеличит blast radius и потребует отдельной архитектуры
  форматирования числовых колонок.

### 5.2 Не трогать legacy openpyxl helper

Не переименовывать и не удалять:

```python
apply_openpyxl_column_formats
```

`excel_walk_forward.py` остается на openpyxl writer.

### 5.3 Autofilter helper должен поддерживать два режима

Нельзя blindly заменить все `ws.auto_filter.ref = "A1:Z1"` на full-table
autofilter. В текущем контракте часть листов имеет header-only filter.

Предупреждение: helper API из ТЗ superseded. Не использовать вариант:

```python
def _set_autofilter(ws, header_row, n_cols, n_data_rows=0):
    last_row = header_row + max(n_data_rows, 0)
```

Такой helper делает вызовы с `len(df)` full-table по умолчанию и ломает
header-only контракт для `cycle`, `false start` и `Tester_Config`.

Нужен helper с явным режимом:

```python
def _set_autofilter(
    ws: Any,
    header_row: int,
    n_cols: int,
    n_data_rows: int = 0,
    *,
    include_data_rows: bool = False,
) -> None:
    if n_cols <= 0:
        return
    last_row = header_row + max(n_data_rows, 0) if include_data_rows else header_row
    ws.autofilter(header_row, 0, last_row, n_cols - 1)
```

Контракт:

- `header_row` - 0-based row with headers;
- coordinates are xlsxwriter numeric, 0-based, inclusive;
- `include_data_rows=False` preserves old header-only filters;
- `include_data_rows=True` is used only where old contract covered data rows.

Expected usage:

- `cycle`: header-only, expected `A1:W1`;
- `Tester_Config`: header-only;
- `Signals`: header-only;
- `false start`: header-only on main table header row;
- diagnostics v2: include data rows;
- do not add autofilter to sheets where it did not exist.

### 5.4 Datetime helper is writer-aware

Add:

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

Important nuance:

- primary datetime serialization comes from `pd.ExcelWriter(datetime_format=...)`;
- column format is an explicit default/width style;
- tests must verify datetime values, not rely only on exact cell format strings.

### 5.5 Timezone safety is explicit

Before `to_excel`, all datetime-like values that may contain timezone must be
converted to tz-naive.

Add or reuse small helpers for:

- datetime64 tz columns;
- object columns containing `pd.Timestamp` / `datetime` values;
- equal-blocks `Start Date` and `End Date`;
- trades entry/exit time;
- signals signal time;
- false-start Entry/Exit/Signal Time;
- diagnostics v2 payloads.

Do not rely on xlsxwriter to handle tz-aware values.

## 6. Implementation steps

### Step 0. Preflight

1. Confirm current import failure / version state:

```powershell
python -c "import importlib.util as u; import pandas as pd; print('xlsxwriter', bool(u.find_spec('xlsxwriter'))); print('pandas', pd.__version__)"
```

2. Verify that the only openpyxl-specific writer API in `excel_tester.py` is in
   known migration targets. This protects engine-agnostic functions such as
   `_write_filter_diagnostics_100_sheet` and `_write_zigzag_trigger_events_sheet`.

```powershell
rg -n "get_column_letter|\.cell\(|\.auto_filter|freeze_panes|column_dimensions|ColorScaleRule|CellIsRule|PatternFill" donor\supertrend_optimizer\io\excel_tester.py
```

Expected matches must be limited to:

- `_write_cycle_sheet`;
- `_write_false_start_sheet`;
- `_write_signals_sheet`;
- `_format_trades_datetime`;
- `_write_tester_config_sheet`;
- `_write_diagnostics_v2_sheet`;
- writer entrypoints using `engine="openpyxl"`.

3. Run a pre-migration performance baseline before code changes if feasible.
   Minimum useful baseline: elapsed time for an export with non-empty `Trades_100`
   and at least 1,000 trades. Record dataset shape, trade count, pandas version,
   and elapsed time in the implementation notes.
4. Fix dependency contract.
5. Verify `xlsxwriter` import.

### Step 1. Add writer/helper layer

In `donor/supertrend_optimizer/io/excel_tester.py` add:

- `_create_xlsx_writer`;
- `_set_autofilter`;
- `_set_datetime_columns`;
- optional `_strip_tz_from_excel_values` / `_strip_tz_columns` helper.

No A-Z / `chr` column generation. Use numeric xlsxwriter coordinates.

### Step 2. Switch writer entrypoints

Replace:

```python
with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
```

with:

```python
with _create_xlsx_writer(output_path) as writer:
```

In:

- `export_tester_results`;
- `export_equal_blocks_results`.

### Step 3. Multi-DataFrame same-sheet writes

For every second/subsequent DataFrame written to the same sheet, add explicit
`startcol=0`.

Required places:

- Summary filter block in `export_tester_results`;
- `_write_false_start_sheet` main table;
- `_write_filters_summary_sheet` period block;
- equal-blocks Summary aggregate block.

This is low-risk and makes layout engine-independent.

### Step 4. Simple sheet writers

#### `_write_tester_config_sheet`

Replace:

- `get_column_letter`;
- `ws.auto_filter.ref`.

With:

- `_set_autofilter(ws, 0, len(cfg_df.columns), include_data_rows=False)`.

#### `_write_diagnostics_v2_sheet`

Replace:

- `get_column_letter`;
- `ws.freeze_panes = "A2"`;
- `ws.auto_filter.ref = ...`;
- `ws.column_dimensions[...]`.

With:

```python
ws.freeze_panes(1, 0)
_set_autofilter(ws, 0, len(out.columns), len(out), include_data_rows=True)
for col_idx, col_name in enumerate(out.columns, start=0):
    ws.set_column(col_idx, col_idx, width)
```

Keep Excel row limit check.

Important: xlsxwriter coordinates are 0-based. Do not copy the current
openpyxl loop `enumerate(out.columns, start=1)` into `ws.set_column`, or all
widths will shift one column to the right and column A will keep the default
width.

#### `_write_cycle_sheet`

Replace:

- `get_column_letter`;
- openpyxl `auto_filter.ref`;
- per-cell `ws.cell(...).number_format`;
- `_EXCEL_PER_CELL_FORMAT_LIMIT` guard.

With:

- `_set_autofilter(ws, 0, len(cycle_df.columns), include_data_rows=False)`;
- `_set_datetime_columns` for current display names:
  - `"РќР°С‡Р°Р»Рѕ С†РёРєР»Р°"`;
  - `"РљРѕРЅРµС† С†РёРєР»Р°"`.

Do not rename columns or fix encoding in this migration.

### Step 5. Multi-block sheet writers

#### `_write_false_start_sheet`

Required changes:

- remove `get_column_letter`;
- write main table with `startcol=0`;
- set filter on `main_startrow`, header-only;
- replace per-cell datetime loop with `_set_datetime_columns`;
- keep `_strip_tz_from_false_start_datetimes`.

Use:

```python
_set_autofilter(
    ws,
    main_startrow,
    len(false_start_df.columns),
    include_data_rows=False,
)
```

Datetime columns:

- `TRADES_DISPLAY_NAMES["entry_time"]`;
- `TRADES_DISPLAY_NAMES["exit_time"]`;
- `SIGNALS_DISPLAY_NAMES["signal_time"]`.

#### `_write_filters_summary_sheet`

Add `startcol=0` for the second block.

Do not add autofilter unless it existed before.

#### Summary filter block

Add `startcol=0` to the `filter_block_df.to_excel(...)` call.

#### Equal-blocks Summary aggregate block

Add `startcol=0` to `agg_df.to_excel(...)`.

### Step 6. Trades datetime formatting

Change `_format_trades_datetime` signature from:

```python
def _format_trades_datetime(trades_df: pd.DataFrame, worksheet: Any) -> None:
```

to:

```python
def _format_trades_datetime(
    writer: pd.ExcelWriter,
    trades_df: pd.DataFrame,
    worksheet: Any,
) -> None:
```

Implementation:

```python
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

Update all call sites with a grep check:

- normal tester export;
- `_write_trades_sheet` in equal-blocks path.

The equal-blocks call site is easy to miss because it is nested in
`_write_trades_sheet`. It must become:

```python
_format_trades_datetime(writer, trades_df, writer.sheets[sheet_name])
```

Run:

```powershell
rg -n "_format_trades_datetime\(" donor\supertrend_optimizer\io\excel_tester.py
```

Expected after migration: one function definition and two updated calls.

Keep function name to avoid unnecessary caller churn.

### Step 7. Signals sheet

Rewrite `_write_signals_sheet` from openpyxl conditional formatting to
xlsxwriter conditional formatting.

Keep:

- rename behavior via `SIGNALS_DISPLAY_NAMES`;
- empty signals fallback columns;
- autofilter on header row even when empty;
- no conditional formatting when `signals_df` has no data;
- tz stripping for `Signal Time`.

Autofilter:

```python
_set_autofilter(ws, 0, len(signals_df.columns), include_data_rows=False)
```

Datetime:

```python
_set_datetime_columns(
    writer,
    ws,
    signals_df,
    (SIGNALS_DISPLAY_NAMES["signal_time"],),
)
```

Conditional formatting coordinates:

- first data row: `1`;
- last data row: `len(signals_df)`;
- columns are 0-based.

3-color scale for:

- `signal_body_pct`;
- `signal_range_pct`.

Use:

```python
ws.conditional_format(first_row, col, last_row, col, {
    "type": "3_color_scale",
    "min_color": "#63BE7B",
    "mid_color": "#FFEB84",
    "max_color": "#F8696B",
})
```

Ratio rules for:

- `signal_body_pct_median_ratio`;
- `signal_range_pct_median_ratio`.

Use xlsxwriter cell rules:

```python
ws.conditional_format(first_row, col, last_row, col, {
    "type": "cell",
    "criteria": ">",
    "value": 2,
    "format": ratio_strong_fmt,
    "stop_if_true": True,
})
ws.conditional_format(first_row, col, last_row, col, {
    "type": "cell",
    "criteria": ">",
    "value": 1.6,
    "format": ratio_soft_fmt,
})
```

Negative return fill for:

- `t1_return_pct`;
- `t2_return_pct`;
- `t3_return_pct`.

Use:

```python
ws.conditional_format(first_row, col, last_row, col, {
    "type": "cell",
    "criteria": "<",
    "value": 0,
    "format": neg_fmt,
})
```

### Step 8. Keep unchanged sheets stable

Do not change logic in:

- `_write_filter_diagnostics_100_sheet`, except if tests require xlsxwriter
  writer in direct helper tests;
- `_write_zigzag_trigger_events_sheet`;
- `_build_*` DataFrame builders.

Keep:

- `_FILTER_DIAGNOSTICS_100_MAX_ROWS`.

### Step 9. Cleanup

After all writer-side openpyxl call sites are migrated:

- remove `_EXCEL_PER_CELL_FORMAT_LIMIT`;
- remove guards based on it;
- remove comments that describe per-cell openpyxl formatting as current behavior.

Static check for target file:

```powershell
rg -n "get_column_letter|ColorScaleRule|CellIsRule|PatternFill|ws\\.cell\\(.+number_format|_EXCEL_PER_CELL_FORMAT_LIMIT|engine=\"openpyxl\"" donor\supertrend_optimizer\io\excel_tester.py
```

Expected: no matches.

Also verify:

```powershell
rg -n "_FILTER_DIAGNOSTICS_100_MAX_ROWS|apply_openpyxl_column_formats" donor\supertrend_optimizer\io
```

Expected:

- `_FILTER_DIAGNOSTICS_100_MAX_ROWS` still exists in `excel_tester.py`;
- `apply_openpyxl_column_formats` still exists in `excel_format_helpers.py`.

## 7. Test updates

### 7.1 Direct writer tests

Only update tests that directly call migrated helper writers and therefore need
xlsxwriter worksheet API.

Known required changes:

- `donor TESTER/tests/test_xlsx_cycle_sheet.py`
  - direct `_write_cycle_sheet` tests should use xlsxwriter writer factory or a
    test-local xlsxwriter writer helper;
  - exact old `number_format == "YYYY-MM-DD HH:MM:SS"` should become semantic
    datetime assertions.

For `_write_diagnostics_v2_sheet`, current coverage is primarily integration
coverage through `export_tester_results`. During implementation either:

- add a small direct helper test that creates an xlsxwriter writer and calls
  `_write_diagnostics_v2_sheet`; or
- explicitly defer diagnostics v2 verification to the smoke suite after both
  writer entrypoints are switched.

Do not blindly change unrelated openpyxl writer tests for walk-forward or
`wf_grid/export`.

Update tests in lockstep with each migrated writer helper. Do not wait until
after cleanup: intermediate smoke runs should distinguish real regressions from
expected writer API changes.

### 7.2 Autofilter tests

Preserve exact expectations where they encode current contract:

- `cycle`: `A1:W1`;
- false-start: filter starts on main table header row, not summary block;
- diagnostics v2: filter covers header + data rows and starts with `A1:F`;
- empty Signals: header autofilter exists, but no conditional formatting.

Diagnostics v2 column-width tests should stay semantic. Prefer `>= 8` or
similar lower-bound checks over exact width equality, because xlsxwriter and
openpyxl may round Excel character-width units slightly differently.

### 7.3 Conditional formatting tests

Add or keep tests that verify:

- `Signals` has conditional formatting when non-empty;
- empty `Signals` has no conditional formatting rules;
- CF ranges target expected columns;
- workbook is readable by openpyxl after xlsxwriter write.

Exact internal openpyxl rule object equality is not required.

### 7.4 Datetime tests

Replace exact old format assertions with:

```python
from datetime import datetime

assert isinstance(ws["A2"].value, datetime)
assert ws["A2"].value == datetime(2026, 1, 1, 1, 2, 3)
```

Add tz-aware smoke if absent:

- input timestamp has timezone;
- exported cell is tz-naive expected datetime;
- export does not raise.

## 8. Regression suite

### Smoke suite

Run first:

```powershell
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
python -m pytest "donor TESTER/tests/test_diagnostics_v2_export.py" -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
```

The smoke suite must include an equal-blocks export with non-empty trades. The
existing `test_phase2_wp_t7_excel_export.py` contains equal-blocks coverage; if
that coverage is skipped or becomes too broad/slow, add or select a focused
test that calls `export_equal_blocks_results` and verifies:

- at least one `Trades_*` sheet exists;
- entry/exit datetime cells are readable through openpyxl;
- the Summary aggregate block is still at column A.

### Targeted donor tester suite

Then run:

```powershell
python -m pytest "donor TESTER/tests/test_tester_config_sheet.py" -q
python -m pytest "donor TESTER/tests/test_phase2_tester_determinism_normalized.py" -q
python -m pytest "donor TESTER/tests/test_w7_tester_volume_integration.py" -q
```

### Cross-tree regression surface

Run relevant `wf_grid` tests that import tester exporter/helper contracts:

```powershell
python -m pytest wf_grid/tests/test_v3_9_regression.py -q
python -m pytest wf_grid/tests/test_wp9_diagnostics_export.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
```

If runtime is too high, start with collect-only or selected tests that directly
touch `supertrend_optimizer.io.excel_tester`.

### Full targeted suite from TZ

Run the full targeted suite from `docs/tz_excel_tester_xlsxwriter_migration.md`
after smoke passes.

## 9. Performance smoke

Performance is the reason for the migration, so do at least one lightweight
confirmation after functional regression passes.

Recommended:

```powershell
python benchmark_tester_fast_export.py
```

If full benchmark is too slow, add a short representative export smoke with:

- enough trades to exercise `Trades_*`;
- non-empty Signals;
- filter diagnostics disabled unless specifically testing them;
- elapsed time logged.

Minimum acceptance gate:

- compare against the pre-migration baseline from Step 0 when available;
- for an export with more than 1,000 trades, xlsxwriter elapsed time must be
  strictly lower than the openpyxl baseline;
- if no baseline is available, record elapsed time and explicitly mark the
  performance gate as not fully verified.

This is still intentionally lightweight: it catches accidental remaining
per-cell loops without making the first iteration depend on a 333k-bar full
benchmark.

## 10. Acceptance criteria

Migration is complete when:

1. `export_tester_results` uses `_create_xlsx_writer`.
2. `export_equal_blocks_results` uses `_create_xlsx_writer`.
3. `xlsxwriter` dependency is explicitly documented or machine-fixed.
4. `excel_tester.py` has no writer-side openpyxl API:
   - `get_column_letter`;
   - `ColorScaleRule`;
   - `CellIsRule`;
   - `PatternFill`;
   - `ws.cell(...).number_format`;
   - `engine="openpyxl"`.
5. `_EXCEL_PER_CELL_FORMAT_LIMIT` is removed.
6. `_FILTER_DIAGNOSTICS_100_MAX_ROWS` remains.
7. `apply_openpyxl_column_formats` remains unchanged.
8. `excel_walk_forward.py` is not changed for this migration.
9. Sheet names and sheet order remain stable.
10. Column names and column order remain stable.
11. XLSX opens via `openpyxl.load_workbook`.
12. XLSX reads via `pd.read_excel`.
13. Autofilter refs preserve existing logical contract.
14. Diagnostics v2 preserves freeze panes and column widths.
15. `Signals` preserves conditional formatting for non-empty data.
16. Empty `Signals` has autofilter but no conditional formatting.
17. Trades datetime works in both normal tester and equal-blocks exports.
18. Tests listed in regression suite pass or failures are documented as known
    format-string-only differences.
19. Equal-blocks export with non-empty trades is covered by smoke or targeted
    regression.
20. Performance smoke is recorded; if no pre-migration baseline exists, that gap
    is explicitly documented.

## 11. Main risks and mitigations

| Risk | Severity | Mitigation |
|---|---:|---|
| `xlsxwriter` missing in runtime env | High | Fix dependency before code switch |
| Autofilter ranges change from header-only to full-table | High | Helper supports `include_data_rows`; preserve exact known refs |
| `Signals` conditional formatting silently differs | High | Use exact xlsxwriter rule dicts and openpyxl readback tests |
| tz-aware datetime export fails | High | Explicit tz stripping before `to_excel` |
| `_format_trades_datetime` call site missed | Medium | Change signature and grep all calls |
| `ws.set_column` receives 1-based column indexes | Medium | Use `enumerate(..., start=0)` for xlsxwriter |
| Equal-blocks Summary or filters_summary layout shifts | Medium | Add `startcol=0` to second blocks |
| Remaining per-cell formatting loop keeps performance bad | Medium | Static grep and performance smoke |
| Accidentally patching `donor zigzag` copy | Medium | Scope static checks to `donor/supertrend_optimizer/io/excel_tester.py` |
| Over-updating unrelated tests | Medium | Update only tests coupled to migrated writer helpers |
| ТЗ helper examples override this plan mentally | High | Treat this plan as document-истина; TЗ autofilter examples are superseded |

## 12. Recommended implementation order

Important intermediate-state rule: while steps 4-8 are in progress, migrated
helper writers may use xlsxwriter worksheet APIs while the top-level export
entrypoints still use `engine="openpyxl"`. Do not run integration smoke through
`export_tester_results` or `export_equal_blocks_results` during that window.
Run integration smoke only after both writer entrypoints are switched. Direct
helper tests with an xlsxwriter writer are allowed during the intermediate
state.

1. Confirm document hierarchy: this plan overrides conflicting helper examples in the TЗ.
2. Run preflight static checks and pre-migration performance baseline if feasible.
3. Fix dependency and verify import.
4. Add `_create_xlsx_writer`, `_set_autofilter`, `_set_datetime_columns`, tz helper.
5. Add explicit `startcol=0` to same-sheet secondary blocks.
6. Migrate simple sheets: `Tester_Config`, diagnostics v2, `cycle`, updating focused tests alongside each helper.
7. Switch both writer entrypoints once simple sheet writers are xlsxwriter-compatible.
8. Migrate multi-block sheets: `false start`, Summary filter block,
   `filters_summary`, equal-blocks Summary aggregate block.
9. Migrate `_format_trades_datetime` and both runtime call sites, including `_write_trades_sheet`.
10. Migrate `Signals` conditional formatting.
11. Remove `_EXCEL_PER_CELL_FORMAT_LIMIT` and stale comments.
12. Run smoke suite, including equal-blocks non-empty trades coverage.
13. Run targeted donor tester suite.
14. Run cross-tree regression surface.
15. Run static acceptance grep.
16. Run lightweight performance smoke and compare with baseline when available.

Rollback strategy: keep changes grouped by the steps above in one working tree,
but do not mix unrelated refactors. If a late step fails, revert or disable only
that migrated helper path in the branch/worktree; do not partially reintroduce
openpyxl API into already migrated helpers.
