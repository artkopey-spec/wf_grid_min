 "Фильтр должен поддерживать lite mode: при collect_filter_diagnostics=False не аллоцировать diagnostic arrays и возвращать filter_diagnostics=None. Поведение positions должно быть идентично режиму True."



# ТЗ: ускорение lite-прогона tester без изменения торговой логики

## 1. Цель

Снизить время обработки одного lite-config в `run_configs_tester_parallel.py` с текущих примерно 7-10 секунд до целевого уровня около 5 секунд.

Оптимизация должна сохранять торговую логику, `filtered_positions`, `trades_df`, метрики и форматы XLSX. Основной источник ускорения - не строить per-bar filter diagnostics, когда пользователь не экспортирует diagnostic/signal/cycle/trades артефакты.

## 2. Термины

- **Lite-config** - tester config, в котором все export-флаги выключены:
  `export.diagnostics=false`, `export.signals=false`, `export.cycle=false`,
  `export.false_start=false`, `export.trades=false`.
- **Filter diagnostics** - per-bar словарь `filter_diagnostics`, возвращаемый backtest engine и используемый для diagnostic XLSX sheets, signal columns, cycle sheets и trade-level filter columns.
- **Торговая логика** - всё, что влияет на `positions`, `returns`, `equity_curve`, `trades_df` и итоговые метрики.
- **Диагностика** - данные, которые только экспортируются или используются для пояснительных колонок, но не должны влиять на `filtered_positions`.

## 3. Объём работ

В объёме:

1. Добавить сквозной флаг `collect_filter_diagnostics`.
2. При `collect_filter_diagnostics=false` не аллоцировать и не заполнять diagnostic arrays в `zigzag_st_filter.apply()`.
3. При `collect_filter_diagnostics=false` возвращать `filter_diagnostics=None`.
4. Сохранить полную текущую диагностику при `collect_filter_diagnostics=true`.
5. Заменить deep copy DataFrame в parallel worker на shallow copy.
6. Провести подбор `--jobs` замерами throughput.

Вне объёма:

1. Изменение FSM-семантики, формул SuperTrend/ZigZag/ATR/volume runtime.
2. Изменение `extract_trades`, состава `trades_df` в diagnostic/trades режимах и trade-level metric override.
3. Кеширование `ZigZagGlobalStats`, ATR, SuperTrend или volume runtime.
4. Векторизация, numba, cython, multiprocessing changes за пределами `--jobs`.
5. Изменение XLSX-форматов при включённых export-флагах.
6. Оптимизация equal-blocks режима.

## 4. Инварианты

1. При `collect_filter_diagnostics=true` поведение и diagnostic output должны быть идентичны текущему коду.
2. При `collect_filter_diagnostics=false` значения `filtered_positions` должны совпадать с режимом `true` на каждом баре.
3. В lite-режиме ключевые метрики и число сделок должны совпадать до и после оптимизации.
4. `trades_df` извлекается как раньше, если вызывающий передал `extract_trades_flag=true`.
5. Режим `export.trades=true` всегда включает полный сбор filter diagnostics, потому что trade-level filter columns и `exit_reason` используют `attach_trade_filter_diagnostics`.
6. Все новые параметры опциональные и по умолчанию сохраняют старое поведение.
7. `filter_config_snapshot` должен сохраняться независимо от `filter_diagnostics`; downstream code может использовать snapshot отдельно от per-bar diagnostics.

## 5. Текущая цепочка вызовов

Флаг должен пройти по реальной цепочке tester legacy mode:

1. `donor/supertrend_optimizer/cli/tester.py::run_backtest_with_df`
2. `donor/supertrend_optimizer/testing/runner.py::run_all_periods`
3. `donor/supertrend_optimizer/testing/runner.py::run_period`
4. `donor/supertrend_optimizer/engine/run.py::run_single_backtest`
5. `donor/supertrend_optimizer/core/backtest.py::run_backtest_fast`
6. `donor/supertrend_optimizer/core/zigzag_st_filter.py::apply`

Важно: `run_backtest_fast` находится в `core/backtest.py`, а не в `engine/run.py`.

Direct/internal callers of `run_single_backtest`, `run_backtest_fast` and `apply` must keep old behaviour by default. Therefore every new signature uses `collect_filter_diagnostics: bool = True`.

## 6. Источник значения флага в CLI tester

В `run_backtest_with_df` после merge CLI/config вычислить:

```python
collect_filter_diagnostics = (
    params["export"]["diagnostics"]
    or params["export"]["signals"]
    or params["export"]["cycle"]
    or params["export"]["trades"]
)
```

Пояснения:

- Lite-config даёт `False`.
- `export.trades=true` обязательно даёт `True`.
- `export.false_start=true` сам по себе не включает сбор diagnostics: текущий false-start sheet не требует `filter_diagnostics`, когда `export.signals=false`. Перед реализацией нужно подтвердить это по `io/excel_tester.py`; если exporter начнёт требовать diagnostics для false-start-only режима, формулу и тесты нужно обновить до реализации.
- `export.diagnostics=true`, `export.signals=true` и `export.cycle=true` требуют diagnostics для XLSX/signal/cycle артефактов.

## 7. Обязательный аудит перед изменениями

Перед изменением `apply()` нужно создать артефакт аудита: таблицу всех массивов и per-bar записей внутри `donor/supertrend_optimizer/core/zigzag_st_filter.py::apply`.

Аудит должен покрыть не только `_allocate_apply_arrays()`, но и runtime/per_bar массивы, которые потом частично попадают в `filter_diagnostics`.

Для каждого массива указать:

1. Где создаётся.
2. Где пишется в главном цикле.
3. Где читается в главном цикле.
4. Влияет ли на `filtered_positions`.
5. Решение: always-on logic/runtime или optional diagnostics.

### 7.1 Known logic/runtime data, которые нельзя отключать

Эти данные используются торговой логикой и остаются всегда:

- `filtered_positions`.
- Скалярное состояние FSM: `state`, `held_pos`, `confirmed_legs_since_start`, `zz_legs_since_lifecycle_start`, `_stopping_start`.
- Скалярное состояние wakeup/position-freeze: `wakeup_cycle_age`, `wakeup_bars_since_fresh`, `wakeup_active_direction`, `wakeup_exit_c_fired`, `cycle_direction`, `pos_freeze_until`, `pos_freeze_pending`.
- SuperTrend/ZigZag inputs: `trend_arr`, `confirm_event`, `cand_height`, `local_median_N`, `local_median_available`, `cand_age_bars`, `cand_leg_dir`.
- Reset/time-filter inputs: `daily_reset_event`, `time_filter_in_window`, `time_filter_reset_event`, `combined_reset_event`.
- Volume decision input: `volume_condition_allowed_runtime`.
- Wakeup decision inputs: `wakeup_atr_ratio`, `wakeup_volume_ratio`, wakeup thresholds and wakeup config values.

Эти массивы могут также экспортироваться в diagnostics, но их вычисление не считается diagnostic overhead и не отключается этим ТЗ.

### 7.2 Diagnostic arrays из `_allocate_apply_arrays()`

Следующие массивы являются optional diagnostics после выполнения пункта 7.3:

- `state_arr`
- `state_code_arr`
- `trigger_source_arr`
- `confirmed_legs_since_start_arr`
- `st_flip_dir_arr`
- `trade_filter_enabled_arr`
- `reversal_threshold_arr`
- `ctt_diag_arr`
- `local_window_arr`
- `global_median_arr`
- `global_stats_available_arr`
- `freeze_confirmed_legs_arr`
- `median_stop_triggered_arr`
- `stopping_started_at_arr`
- `filter_allowed_entry_arr`
- `filter_block_reason_arr`
- `exit_off_mode_arr`
- `exit_off_zz_leg_count_arr`
- `zz_legs_since_lifecycle_start_arr`
- `zz_leg_stop_triggered_arr`
- `exit_b_immediate_off_triggered_arr`
- `exit_b_immediate_off_config_arr`
- `candidate_threshold_ok_arr`
- `candidate_component_ok_arr`
- `confirmed_median_ok_arr`
- `b_component_ok_arr`
- `immediate_allowed_arr`
- `candidate_duration_gate_passed_arr`
- `state_at_bar_start_arr`
- `held_pos_at_bar_start_arr`
- `confirmed_legs_at_bar_start_arr`
- `zigzag_mode_arr`
- `candidate_duration_gate_enabled_arr`
- `candidate_duration_max_bars_arr`
- `immediate_used_arr`
- `immediate_block_reason_arr`
- `wakeup_regime_active_arr`
- `wakeup_entry_all_ok_arr`
- `wakeup_entry_candidate_height_ok_arr`
- `wakeup_entry_candidate_age_ok_arr`
- `wakeup_entry_candidate_direction_ok_arr`
- `wakeup_entry_trade_mode_ok_arr`
- `wakeup_entry_atr_ok_arr`
- `wakeup_entry_volume_ok_arr`
- `wakeup_entry_candidate_height_value_arr`
- `wakeup_entry_candidate_height_threshold_arr`
- `wakeup_entry_candidate_age_bars_arr`
- `wakeup_entry_candidate_leg_direction_arr`
- `wakeup_entry_atr_ratio_arr`
- `wakeup_entry_volume_ratio_arr`
- `wakeup_cycle_age_bars_arr`
- `wakeup_bars_since_fresh_candidate_arr`
- `wakeup_exit_ttl_triggered_arr`
- `wakeup_exit_no_fresh_candidate_triggered_arr`
- `wakeup_exit_close_triggered_arr`
- `wakeup_exit_action_mode_arr`
- `wakeup_exit_reason_arr`
- `wakeup_position_action_arr`
- `wakeup_active_direction_arr`
- `wakeup_lock_cycle_direction_config_arr`
- `position_freeze_active_arr`
- `position_freeze_bars_left_arr`
- `position_freeze_ignored_opposite_st_flip_arr`
- `position_freeze_release_action_arr`

### 7.3 Обязательное устранение logic-read из `trigger_source_arr`

В текущем коде `trigger_source_arr[t]` используется не только как output diagnostics, но и читается логикой Mode D/wakeup. Перед отключением diagnostic arrays это нужно исправить.

Требование:

- Ввести per-bar локальные переменные, например `trigger_source_this_bar` и `wakeup_started_this_bar`.
- Все runtime-решения внутри цикла должны читать локальные переменные, а не `trigger_source_arr[t]`.
- `trigger_source_arr[t] = ...` остаётся только diagnostic write и выполняется только при `collect_filter_diagnostics=true`.
- Поведение `collect_filter_diagnostics=true` должно сохранить прежние значения `trade_filter_trigger_source`.

Это изменение не должно менять FSM-семантику: оно только отделяет logic state от diagnostic storage.

### 7.4 Стоп-правило аудита

Если аудит найдёт любой другой diagnostic array, чтение которого влияет на `filtered_positions`, этот массив нельзя отключать до рефакторинга на локальное logic state.

Нельзя заменять такие массивы dummy arrays или пустыми массивами: это скрывает ошибку классификации. Нужно либо оставить массив always-on, либо убрать logic-read через локальную переменную и покрыть тестом.

## 8. Реализация `collect_filter_diagnostics`

### 8.1 Сигнатуры

Добавить kw-only optional parameter `collect_filter_diagnostics: bool = True` в:

- `zigzag_st_filter.apply`
- `core/backtest.py::run_backtest_fast`
- `engine/run.py::run_single_backtest`
- `testing/runner.py::run_period`
- `testing/runner.py::run_all_periods`

В `cli/tester.py::run_backtest_with_df` вычислять значение из export-флагов и передавать в `run_all_periods`.

### 8.2 Поведение при `True`

При `collect_filter_diagnostics=true`:

- `_allocate_apply_arrays()` возвращает тот же diagnostic keyset.
- Все per-bar writes выполняются как сейчас.
- `_record_wakeup_entry_diagnostics()` вызывается как сейчас.
- `_finalize_apply_result()` возвращает `filter_diagnostics` с теми же ключами, dtype и значениями.
- `attach_trade_filter_diagnostics` получает такой же diagnostics dict, если `extract_trades_flag=true`.

Это режим backward compatibility.

### 8.3 Поведение при `False`

При `collect_filter_diagnostics=false`:

- Аллоцировать только `filtered_positions` и always-on runtime data.
- Не аллоцировать object diagnostic arrays:
  `state_arr`, `trigger_source_arr`, `filter_block_reason_arr`, `zigzag_mode_arr`,
  wakeup reason/action arrays, immediate block reason arrays, position-freeze action arrays.
- Не аллоцировать numeric diagnostic arrays, которые нужны только для XLSX/summary/trades columns.
- Не выполнять per-bar writes в diagnostic arrays.
- Не вызывать `_record_wakeup_entry_diagnostics()`.
- Не строить `filter_diagnostics_out`.
- Вернуть `ZigZagSTFilterResult(positions=filtered_positions, filter_diagnostics=None, internal_legs=None, filter_config_snapshot=...)`.

При этом все локальные вычисления, влияющие на позицию, остаются.

### 8.4 Рекомендуемая структура кода

Использовать явное разделение:

- `positions = np.zeros(n, dtype=np.int8)` всегда.
- `diagnostics = _allocate_apply_arrays(...) if collect_filter_diagnostics else None`.
- Diagnostic writes выполнять через `if diagnostics is not None:`.
- Для часто пишущихся полей внутри цикла можно вынести локальный boolean `diag_enabled = diagnostics is not None`.
- Не использовать fake arrays для выключенного режима.

Если сохранение текущей функции `_allocate_apply_arrays()` удобнее, она должна стать allocator только для diagnostics plus `filtered_positions` либо быть разделена на:

- `_allocate_positions(n)`
- `_allocate_diagnostic_arrays(...)`

Критерий выбора - минимальный diff без размывания контракта.

## 9. Совместимость потребителей

Перед merge подтвердить, что downstream уже корректно обрабатывает `filter_diagnostics=None`:

- `testing/runner.py::_build_filter_diagnostics_summary` возвращает `None`, если `result.filter_diagnostics is None`.
- `io/excel_tester.py` не пишет `FilterDiagnostics_100`, `ZigZag_Trigger_Events`, `filters_summary`, `cycle`, если diagnostics отсутствуют или export flag выключен.
- `engine/run.py` вызывает `attach_trade_filter_diagnostics` только когда `filter_diagnostics is not None`, `trades_df is not None` и trades не пустые.
- `build_signal_events` должен получать `filter_diagnostics=None` в lite mode и не добавлять filter columns.

Новые изменения в этих потребителях не требуются, кроме передачи флага по call chain и корректировки тестов, если они явно ожидали diagnostics в lite-path.

## 10. `export.trades=true` contract

`export.trades=true` означает полный сбор diagnostics даже если остальные export-флаги выключены.

Причина: `attach_trade_filter_diagnostics` читает не только `trade_filter_state`, но и:

- `trade_filter_trigger_source`
- `daily_reset_event`
- `time_filter_reset_event`
- `exit_b_immediate_off_triggered`
- `filter_block_reason`
- `state_at_bar_start`
- wakeup reason/action fields when present
- volume block reason when present

Поэтому в trades-export режиме запрещено собирать урезанный diagnostics subset. Нужно либо full diagnostics, либо `None`; для `export.trades=true` выбирается full diagnostics.

## 11. DataFrame copy в parallel worker

Файл: `run_configs_tester_parallel.py`.

Текущий worker делает:

```python
df = _WORKER_DF.copy(deep=True)
```

Изменить на:

```python
df = _WORKER_DF.copy(deep=False)
```

Обоснование:

- В одном worker configs выполняются последовательно.
- Shallow copy сохраняет отдельный DataFrame object и защищает от случайной структурной мутации columns/index на объекте, который получает config run.
- При этом не копируются numeric blocks, что уменьшает overhead.

Ограничение:

- Shallow copy не защищает от in-place value mutation shared blocks.
- Полностью убрать `.copy()` нельзя без отдельного mutation hash-test.

### 11.1 Mutation hash-test

До рассмотрения полного снятия copy нужно проверить:

```python
before_hash = pd.util.hash_pandas_object(_WORKER_DF, index=True).sum()
before_shape = _WORKER_DF.shape
before_dtypes = tuple(map(str, _WORKER_DF.dtypes))

# run one representative config through run_backtest_with_df

after_hash = pd.util.hash_pandas_object(_WORKER_DF, index=True).sum()
after_shape = _WORKER_DF.shape
after_dtypes = tuple(map(str, _WORKER_DF.dtypes))
```

Полное снятие copy разрешено только если hash, shape и dtypes совпадают на нескольких representative configs:

- lite ZigZag config
- Mode D/wakeup config
- volume-enabled config, если volume filter используется
- config с time/daily reset, если такие configs есть в целевом наборе

В рамках этого ТЗ выполняется только shallow copy. Полное снятие copy остаётся отдельной опцией после hash-test.

## 12. Подбор `--jobs`

Изменение `--jobs` не является изменением логики.

Порядок:

1. Подготовить фиксированный набор 20-40 lite-configs.
2. Использовать один и тот же `data.csv`.
3. Сделать warm-up run для прогрева OS cache.
4. Замерить `--jobs 4`, `6`, `8`, `10`.
5. Для каждого значения сделать минимум 3 повтора.
6. Основная метрика - `configs/min`.
7. Выбрать медианное лучшее значение, если оно стабильно и не увеличивает failure rate.

Команда-шаблон:

```powershell
python .\run_configs_tester_parallel.py `
  --jobs 8 `
  --configs-dir .\configs_lite_benchmark `
  --output-dir .\_bench\jobs_8_run_1 `
  --csv .\data.csv `
  --glob "*.yml"
```

Для latency одного config использовать `--jobs 1` и набор из 1 config, но performance decision принимать по пачке 20-40 configs.

## 13. Тесты

Обязательные unit/integration tests:

```powershell
python -m pytest wf_grid/tests/test_wp4_zigzag_per_bar.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
```

Если затронуты wakeup/volume/time reset branches, дополнительно:

```powershell
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wakeup_ohlc_atr_backtest.py -q
python -m pytest wf_grid/tests/test_wakeup_volume_plumbing.py -q
python -m pytest wf_grid/tests/test_time_filter.py -q
python -m pytest wf_grid/tests/test_daily_reset.py -q
```

Новые или обновлённые тесты:

1. `apply(..., collect_filter_diagnostics=True)` возвращает прежний diagnostics keyset.
2. `apply(..., collect_filter_diagnostics=False)` возвращает `filter_diagnostics is None`.
3. `positions` при `True` и `False` побарно идентичны на representative ZigZag config.
4. Mode D/wakeup case: `positions` при `True` и `False` идентичны; это покрывает refactor `trigger_source_arr`.
5. `run_single_backtest(..., collect_filter_diagnostics=False)` возвращает `BacktestResult.filter_diagnostics is None`, но метрики и trades count совпадают с `True`.
6. CLI source formula: при всех export-флагах `false` runner получает `collect_filter_diagnostics=False`.
7. CLI source formula: при `export.trades=true` runner получает `collect_filter_diagnostics=True`.
8. Parallel worker still passes a different DataFrame object to `run_backtest_with_df`, but with equal contents, after `deep=False`.

## 14. Golden acceptance

### 14.1 Lite metrics equality

На 5-10 реальных lite-configs:

1. Зафиксировать baseline до изменений.
2. Прогнать после изменений.
3. Сравнить:
   - `num_trades`
   - `sum_pnl_pct`
   - `win_rate`
   - `profit_factor`
   - `avg_trade`
   - `sharpe`
   - `sortino`
   - `cagr`
   - `max_drawdown`

Допуск: точное равенство сохранённых чисел после одинаковой сериализации. Если Excel/CSV форматирует float, сравнивать raw summary CSV или DataFrame values, а не визуальный вид ячейки.

### 14.2 Diagnostics regression

Один representative config с `export.diagnostics=true`.

Проверка:

- `filter_diagnostics` keyset совпадает.
- Для каждого ключа совпадают dtype, длина и значения.
- XLSX sheets, зависящие от diagnostics, совпадают по данным.

Не требовать byte-identical XLSX file, если writer меняет zip metadata. Сравнивать нормализованное содержимое sheets.

### 14.3 Trades regression

Кейс: `export.trades=true`, остальные export-флаги `false`.

Проверка:

- Trades sheet создаётся.
- Набор trade filter columns совпадает.
- Значения `entry_filter_state`, `entry_trigger_source`, `exit_reason` и wakeup/volume columns при наличии совпадают.
- Trade-level metrics совпадают.

### 14.4 Positions invariant

Для representative configs сохранить и сравнить `filtered_positions`:

```python
np.testing.assert_array_equal(result_true.positions, result_false.positions)
```

Это главный acceptance gate для отключения diagnostics.

## 15. Performance acceptance

Фиксировать:

- CPU model
- RAM
- storage type, если известно
- OS
- Python version
- pandas/numpy versions
- commit hash до/после
- config set
- `data.csv` fingerprint

Таблица latency:

| Variant | jobs | configs | repeat 1 sec | repeat 2 sec | repeat 3 sec | median sec/config |
|---|---:|---:|---:|---:|---:|---:|
| before | 1 | 1 |  |  |  |  |
| after P1 | 1 | 1 |  |  |  |  |
| after P1+P2 | 1 | 1 |  |  |  |  |

Таблица throughput:

| Variant | jobs | configs | repeat 1 configs/min | repeat 2 configs/min | repeat 3 configs/min | median configs/min |
|---|---:|---:|---:|---:|---:|---:|
| before | 4 | 20-40 |  |  |  |  |
| after | 4 | 20-40 |  |  |  |  |
| after | 6 | 20-40 |  |  |  |  |
| after | 8 | 20-40 |  |  |  |  |
| after | 10 | 20-40 |  |  |  |  |

Performance goal:

- Основная цель: заметное снижение median sec/config в lite mode.
- Целевой ориентир: около 5 секунд на один lite-config на текущем железе.
- Если 5 секунд не достигнуты, но `apply()` time существенно снизился, зафиксировать новый bottleneck отдельно; не расширять scope внутри этого ТЗ.

## 16. Порядок реализации

Рекомендуемые commits:

1. `test/audit: classify zigzag apply diagnostics`
   - Добавить аудит-артефакт или тестовую таблицу.
   - Зафиксировать, какие массивы logic/runtime, какие optional diagnostics.

2. `refactor: decouple trigger source diagnostics from zigzag logic`
   - Убрать logic reads из `trigger_source_arr`.
   - Покрыть Mode D/wakeup positions equality.

3. `perf: gate zigzag filter diagnostics collection`
   - Добавить `collect_filter_diagnostics`.
   - Прокинуть флаг по call chain.
   - При `False` возвращать `filter_diagnostics=None`.
   - Добавить tests for true/false/keyset/positions/trades flag.

4. `perf: use shallow dataframe copy in tester workers`
   - Заменить `deep=True` на `deep=False`.
   - Обновить/добавить worker test.

5. `bench: record tester lite jobs recommendation`
   - Зафиксировать таблицы замеров и рекомендованный `--jobs`.

Коммиты 1-4 являются code/test changes. Коммит 5 может быть docs-only.

## 17. Rollback plan

Если не сходится `filtered_positions`:

1. Откатить commit с gating diagnostics.
2. Оставить audit/refactor commit только если он сам проходит positions equality.
3. Повторить аудит массивов и найти оставшийся logic-read.

Если ломается `export.trades=true`:

1. Проверить, что CLI formula включает `export.trades`.
2. Проверить, что runner получает `collect_filter_diagnostics=True`.
3. Проверить, что `attach_trade_filter_diagnostics` получает full diagnostics dict.
4. При необходимости откатить gating commit.

Если ломается diagnostic XLSX:

1. Проверить режим `collect_filter_diagnostics=true`.
2. Сравнить keyset/dtype/values `filter_diagnostics`.
3. Откатить только diagnostic gating commit, не трогая DataFrame shallow copy.

Если hash-test показывает mutation после `deep=False`:

1. Откатить shallow copy commit.
2. Оставить diagnostics gating commit, если acceptance tests зелёные.

## 18. Definition of Done

Работа считается завершённой, когда:

1. Аудит массивов выполнен и сохранён.
2. Logic reads из optional diagnostic arrays устранены или массивы оставлены always-on.
3. `collect_filter_diagnostics=false` в lite mode реально возвращает `filter_diagnostics=None`.
4. `filtered_positions` совпадает при `True` и `False`.
5. Lite metrics and trades count совпадают до/после.
6. `export.diagnostics=true` сохраняет diagnostics output.
7. `export.trades=true` при остальных export-флагах `false` сохраняет trade filter columns and values.
8. Обязательные pytest tests зелёные.
9. `run_configs_tester_parallel.py` использует shallow copy, и worker test зелёный.
10. Performance measurements записаны, рекомендованный `--jobs` выбран.
11. Не менялись `extract_trades`, формулы метрик и XLSX-форматы за пределами conditional absence of diagnostics in lite mode.
