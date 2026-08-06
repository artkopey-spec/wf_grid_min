# Implementation Plan: `trade_filter.reversal_confirmation`

## 1. Цель и границы реализации

Реализовать независимый фильтр `trade_filter.reversal_confirmation`, который:

- задерживает только изменение уже открытой позиции после противоположного изменения raw Supertrend;
- не задерживает первоначальные и повторные входы из `flat`;
- поддерживает `revers`, `both`, `long`, `short`;
- работает самостоятельно и совместно с `time_filter`;
- использует модель `open_to_open`: решение на close бара `t`, исполнение на open бара `t+1`;
- сохраняет существующую формулу комиссии через `abs(diff(positions))`;
- при отсутствии блока или `enabled: false` сохраняет positions, returns, trades, diagnostics и legacy Excel без изменений.

Запрещено:

- встраивать новую логику в ZigZag FSM, wakeup lifecycle или `wakeup_regime.entry.st_confirmation`;
- менять расчёт Supertrend, warmup, returns, equity и комиссии;
- менять control-flow и boundary-семантику `time_only_filter.apply`;
- добавлять универсальную систему композиции фильтров;
- поддерживать в v1 ZigZag, volume, wakeup, position freeze, `wf_grid`, optimizer, equal-blocks, early exit и другие execution models;
- использовать имена `st_confirmation_*` для новой capability.

## 2. Фактический маршрут данных

```text
config_tester.yaml
  → donor/supertrend_optimizer/cli/tester.py::load_tester_config
  → collect_trade_filter_unknown_keys
  → collect_raw_user_keys
  → build_trade_filter_config_from_raw
  → validate_trade_filter(caller_pipeline="tester")
  → resolve_trade_filter_mode_in_place
  → donor/supertrend_optimizer/testing/runner.py::run_all_periods
  → run_period
  → donor/supertrend_optimizer/engine/run.py::run_single_backtest
  → donor/supertrend_optimizer/core/backtest.py::run_backtest_fast
  → reversal_confirmation_filter.apply
  → positions / returns / trades / diagnostics / events
  → testing/signal_events.py
  → io/excel_tester.py::export_tester_results
```

Текущие точки формирования позиции:

- `donor/supertrend_optimizer/core/backtest.py::generate_positions` (текущая строка 68) формирует baseline и записывает `positions[1:]` по `trend[:-1]`.
- `donor/supertrend_optimizer/core/time_only_filter.py::apply` (текущая строка 41) выполняет самостоятельный time-only bar-loop и записывает `positions[t+1]` около строки 118.
- `donor/supertrend_optimizer/core/backtest.py::run_backtest_fast` (текущая строка 312) выбирает ZigZag, volume, time-only или baseline runtime.
- `donor/supertrend_optimizer/engine/run.py::run_single_backtest` (текущая строка 41) является общим engine entry point для tester, single, optimizer и WF.

Ссылки на номера строк относятся к состоянию репозитория до начала реализации. При смещении строк разработчик должен ориентироваться на указанные символы.

## 3. Граф зависимостей

```text
Шаг 1: baseline artifact и characterization
  ↓
Шаг 2: characterization shared mode/time contracts и pure helpers
  ↓
Шаг 3: runtime DTO и изолированный reversal/combined runtime
  ↓
Шаг 4: namespaced metadata, result transport, Trades и summary
  ↓
Шаг 5: active-only Signals/RawSignals/Events, Excel и diagnostics_v2
  ↓
Шаг 6: shared mode/config refactor + direct single dispatch/gates
  ↓
Шаг 7: атомарная активация tester YAML и end-to-end export
  ↓
Шаг 8: snapshots, fingerprint, документация
  ↓
Шаг 9: performance benchmark
  ↓
Шаг 10: полный regression/acceptance gate
```

Шаги нельзя переставлять так, чтобы YAML-конфигурация начала приниматься до готовности runtime, Trades, Signals, events и Excel. Ни один промежуточный commit не должен принимать конфигурацию, которую проект ещё не способен корректно исполнить и экспортировать.

## 4. Зафиксированные схемы active-capability export

### 4.1. `Signals`

При активном `reversal_confirmation` таблица строится только из переходов `positions`:

```text
event_id
event_type
side
execution_bar
execution_time
execution_price
source_signal_bar
source_signal_time
confirmation_status
```

Правила:

- `event_type`: только `open` или `close`;
- `confirmation_status`: `not_required` или `confirmed`;
- initiating entry имеет nullable `event_id` и `not_required`;
- reversal создаёт `close`, затем `open` на одном execution index;
- `long`/`short` confirmed close создаёт только `close`;
- фактическая цена — `open[execution_bar]`.

### 4.2. `RawSignals`

Используется существующая ST-аналитика из `build_signal_events`, но active-only схема:

- сохраняет текущие аналитические колонки и их относительный порядок;
- `exec_price` переименовывается в `raw_reference_price`;
- рядом добавляются `raw_reference_bar` и `raw_reference_time`;
- запрещены колонки `execution_*` и `exec_*`;
- raw reference не используется как подтверждение фактической сделки.

`Signals`, `RawSignals` и `ReversalConfirmationEvents` управляются существующим `export.signals`; новый config-флаг не вводится.

### 4.3. `ReversalConfirmationEvents`

```text
event_id
source_signal_bar
source_signal_time
confirmation_bar
confirmation_time
execution_bar
execution_time
confirmation_status
confirmation_cancel_reason
```

Одна итоговая строка на attempt. Для завершённого batch run status только `cancelled` или `confirmed`.

### 4.4. `Trades`

Текущая схема сохраняется; только при активной capability добавляются:

```text
entry_reversal_confirmation_event_id
exit_reversal_confirmation_event_id
exit_reason
```

Приоритет `exit_reason`:

```text
time/daily forced reset
→ reversal_confirmation_confirmed
→ обычный st_flip
→ terminal open-trade reason
```

### 4.5. Нормативный контракт полного владения позицией в `reversal_and_time`

`reversal_and_time` является отдельным владельцем массива `positions`; он не вызывает `time_only_filter.apply` и не изменяет его. Чтобы combined runtime не разошёлся с существующим time-only поведением, он обязан перенести следующие правила без смысловых изменений:

```text
raw_change =
    baseline_target[t] != 0
    and (t == 0 or baseline_target[t] != baseline_target[t - 1])

cycle_open =
    in_window[t]
    and (t == 0 or not in_window[t - 1] or time_filter_reset[t])

if baseline_target[t] == 0:
    reentry_pending = false

entry_attempt =
    baseline_target[t] != 0
    and (raw_change or cycle_open or reentry_pending)
```

На `time_filter_reset[t]` combined runtime:

1. сначала отменяет active reversal pending с причиной `time_filter_reset`;
2. формирует ровно одно существующее time-filter действие `next_position = 0`;
3. отмечает forced exit только если `current_position != 0` и существует `t+1`;
4. если текущий бар уже принадлежит новому окну и актуальный target ненулевой, устанавливает `reentry_pending = true`;
5. не выполняет confirmation и не создаёт новый reversal pending на этом же баре.

На последующем баре entry из `flat` использует `raw_change | cycle_open | reentry_pending`, но перед записью `positions[t+1]` дополнительно применяет только новую combined-политику: execution bar должен существовать и принадлежать тому же `(local_calendar_date, window_id)`.

Обязательный shadow-parity suite:

- при отсутствии opposite edge и active pending combined positions/entry decisions совпадают с `time_only_filter.apply`;
- покрываются `raw_change`, `cycle_open`, `reentry_pending`, reset→flat→reentry, target `0`, два окна и смена дня;
- для W2/W4/W5 тесты фиксируют единственные намеренные отличия `combined_safe_execution_boundary` от `time_only_legacy_boundary`;
- любой иной diff считается regression.

Допускается вынести только pure helpers для target mapping и идентичности/eligibility окна. Общий stateful position composer не создаётся.

### 4.6. Нормативная схема `filter_execution_metadata`

Поле `RawBacktestArtifacts.filter_execution_metadata` уже существует. Его нельзя «добавлять заново» или неструктурированно расширять. Существующие ключи сохраняются:

| Ключ | dtype/shape | Индексация | Семантика |
|---|---|---|---|
| `daily_reset_event` | `int8[n]` | decision bar `t` | Daily reset, который может объяснять transition на `t+1` |
| `time_filter_reset_event` | `int8[n]` | decision bar `t` | Time reset, который может объяснять transition на `t+1` |

Новые ключи имеют обязательный namespace и создаются только active reversal runtime:

| Ключ | dtype/default | Индексация | Семантика |
|---|---|---|---|
| `reversal_confirmation_execution_event_id` | `int64[n]`, `-1` | execution bar `e` | `event_id` подтверждённого изменения, реально действующего с open `e` |
| `reversal_confirmation_execution_source_bar` | `int64[n]`, `-1` | execution bar `e` | Event/source bar `t0` |
| `reversal_confirmation_execution_decision_bar` | `int64[n]`, `-1` | execution bar `e` | Confirmation decision bar `e-1` |
| `reversal_confirmation_confirmed_close` | `int8[n]`, `0` | execution bar `e` | На `e` закрыта ранее открытая сторона из-за confirmed reversal/close |
| `reversal_confirmation_confirmed_open` | `int8[n]`, `0` | execution bar `e` | На `e` открыта новая сторона confirmed reversal; для `long/short` всегда `0` |

Правила:

- все массивы одномерные, длины `n`, без object/string dtype;
- reset metadata и reversal metadata объединяются отдельным `merge_filter_execution_metadata`, который валидирует namespace, dtype, длины и запрещает перезапись ключей;
- heavy `filter_diagnostics` и compact execution metadata передаются раздельно;
- `attach_trade_filter_diagnostics` получает compact metadata отдельным keyword-only аргументом, а не через неявную подмену общего dict;
- `BacktestResult` и `PeriodResult` получают optional `filter_execution_metadata=None`; `RawBacktestArtifacts` только расширяет существующее поле;
- inactive time/volume/ZigZag paths возвращают прежние ключи и значения.

Attempt lifecycle для `ReversalConfirmationEvents` хранится отдельной компактной таблицей/records и не кодируется object-массивами в execution metadata.

### 4.7. Карта bar semantics и trade attribution

| Событие | Decision/event bar | Execution bar | Heavy diagnostics | Compact metadata | Trade lookup |
|---|---:|---:|---|---|---|
| start pending | `t0` | — | `started=1`, `event_id=id`, `pending=1` | нет execution-флага | не используется |
| cancel | `tc` | — | `cancelled=1`, reason, `event_id=id` | нет execution-флага | сделки нет |
| confirm/schedule | `d=t0+N` | `e=d+1` | на `d`: `confirmed=1`, `execution_scheduled=1`, `event_id=id` | запись всех `reversal_confirmation_*` на `e` | linkage по `entry_index/exit_index == e` |
| execute | `e` | `e` | на `e`: `executed=1`, `event_id=id` | event ID и close/open flags на `e` | прямой lookup metadata на `e` |
| time/daily forced exit | `d` | `e=d+1` | reset flag на `d` | reset flag остаётся на `d` | legacy lookup `exit_index-1` |

`attach_trade_filter_diagnostics` обязан использовать две разные системы координат:

```text
decision_idx = max(exit_index - 1, 0)  # существующие reset/ST причины
execution_idx = exit_index             # reversal event_id/reason
```

Приоритет для exit:

1. `daily_reset_event[decision_idx]`;
2. `time_filter_reset_event[decision_idx]`;
3. `reversal_confirmation_confirmed_close[execution_idx]`;
4. существующие wakeup/volume/stopping причины;
5. terminal open-trade reason;
6. `st_flip`.

Точный относительный порядок существующих wakeup/volume/stopping ветвей сохраняется. Новая ветвь выполняется только при наличии namespaced metadata и active close flag. Для entry linkage используется `reversal_confirmation_execution_event_id[entry_index]`; для exit linkage — тот же массив на `exit_index`.

До изменения `filter_trade_diagnostics.py` добавляются conflict-тесты: reset+reversal, terminal+reversal на последнем execution bar, reversal+обычный ST, diagnostics on/off и полностью inactive metadata.

### 4.8. Shared mode/subfilter contract

В текущей реализации `trade_filter.enabled=true` без ZigZag/volume/time отклоняется одновременно в helper, mode resolver, validator и runtime guard. Поддержка `reversal_only` требует согласованного изменения четырёх точек, а не только добавления строки в mode table:

1. characterization текущих результатов `is_any_trade_subfilter_enabled` и `resolve_trade_filter_mode` для всех существующих комбинаций;
2. `is_reversal_confirmation_enabled` и включение его в `is_any_trade_subfilter_enabled`;
3. расширение mode table только комбинациями `reversal_only` и `reversal_and_time`;
4. отдельная reversal-ветвь `_validate_subfilter_legality_dispatch`; прежний `trade_filter_all_subfilters_disabled` остаётся только когда неактивны все четыре subfilter;
5. синхронное обновление `run_backtest_fast` guard;
6. public/effective snapshots и fingerprint;
7. regression всех прежних mode strings без переименования.

Эти изменения коммитятся вместе с direct-single activation на шаге 6. До этого внешний whitelist остаётся закрытым.

### 4.9. `diagnostics_v2`, rollout и performance policy

`diagnostics_v2._diagnostics_mode` сейчас классифицирует любой diagnostics dict без `trade_filter_state` как `time_only`, поэтому active reversal нельзя оставлять в текущем fallback.

Решение v1:

- `_cycle_mode_from_config` и `_diagnostics_mode` получают явный mode `reversal`;
- generic diagnostics_v2 sheets, не зависящие от ZigZag/volume cycle state, продолжают строиться;
- `derive_trade_cycle_map(..., mode="reversal")` не имитирует time-only/ZigZag cycles и возвращает детерминированный `mapping_status="unsupported_mode"` для cycle-derived строк;
- cycle-derived sheets получают явный `SKIP/unsupported_mode`, а не неверную time-only классификацию;
- active reversal с `export.diagnostics_v2=true` не падает и не публикует ложную cycle analytics;
- отдельные tests фиксируют этот контракт. Полная reversal cycle analytics не входит в v1.

Rollout:

- tester/WF whitelist не открывается до green direct runtime, metadata, trades, signals и Excel tests;
- внешняя активация находится в одном commit шага 7;
- если e2e шага 7 красный, commit не merge-ится;
- rollback — revert шага 7: YAML снова fail-closed как unknown/unsupported, а dormant internal runtime не влияет на baseline;
- отдельный runtime feature flag не вводится, чтобы не создать третье состояние помимо отсутствия блока и `enabled:false`.

Performance:

- требование ТЗ `median(reversal_only) <= median(time_only) * 1.30` сохраняется как отдельный acceptance benchmark на одной закреплённой машине/процессе;
- benchmark использует одинаковые `trend`, index, длину и diagnostics-off, фиксированную низкую/среднюю плотность opposite edges и одинаковый setup вне измеряемого интервала;
- CI `slow_perf` тест является advisory и печатает ratio; hard fail применяется только в выделенном acceptance environment;
- `confirm_bars=1_000_000` проверяется как valid boundary и по dtype/counter semantics на коротком fixture, но не создаёт миллион итераций подтверждения в unit suite;
- correctness и performance не объединяются в один тест.

## 5. Пошаговая реализация

### Шаг 1. Зафиксировать pre-change baseline и characterization harness

1. **Цель:** получить воспроизводимое доказательство disabled-path parity до production-изменений.

2. **Причина:** последующие изменения затрагивают центральные position, config и export контракты; сравнение только тестов недостаточно для доказательства побитовой совместимости.

3. **Файлы:**

   - новый `scripts/capture_st_reversal_confirmation_baseline.py`;
   - новый `docs/baselines/st_reversal_confirmation_prechange.json`;
   - новый `wf_grid/tests/test_st_reversal_confirmation_baseline.py`;
   - новый `wf_grid/tests/test_st_position_target_characterization.py`.

4. **Символы:** текущие `generate_positions`, `time_only_filter.apply`, `run_single_backtest`, `extract_trades`, `export_tester_results`, `trade_filter_effective_snapshot`.

5. **Изменения:**

   - создать короткий детерминированный OHLC fixture с `DatetimeIndex`;
   - зафиксировать для отсутствующего блока, `enabled: false` и standalone `time_only`:
     - dtype, shape и SHA-256 positions;
     - dtype, shape и SHA-256 returns;
     - нормализованный hash Trades;
     - public/effective config snapshot;
     - порядок Excel-листов и ordered column schema ключевых затрагиваемых листов: `Tester_Config`, `Summary`, `Signals`, `Trades_*`, `FilterDiagnostics_100`, `filters_summary`;
   - сохранить в JSON команду воспроизведения, параметры Python/package versions и seed, если он используется;
   - два последовательных запуска capture-команды должны создавать идентичный JSON;
   - characterization-тестами закрепить четыре `trade_mode`, `both == revers`, `trend == 0`, пустые/одноэлементные массивы и legacy time-only boundary W1/W2/W5;
   - определить freeze scope: обязательна bit-exact parity массивов positions/returns, нормализованной Trades, effective fingerprint и значений ключевых legacy sheets; бинарный ZIP/XLSX hash не используется из-за служебных workbook metadata;
   - raw YAML echo в `Tester_Config` нормализуется только по нестабильным служебным значениям, но содержательные config-поля не исключаются.

6. **Запрещено:** изменять production-код, пользовательские `config.yaml`/`config_tester.yaml` или обновлять существующие baseline-файлы без отдельного обоснования.

7. **Зависимости:** отсутствуют.

8. **Проверка:**

```powershell
python scripts/capture_st_reversal_confirmation_baseline.py --output docs/baselines/st_reversal_confirmation_prechange.json
python -m pytest wf_grid/tests/test_st_reversal_confirmation_baseline.py wf_grid/tests/test_st_position_target_characterization.py -q
```

9. **Ожидаемый результат:** committed baseline-артефакт воспроизводится и фиксирует текущую экономику и Excel schema.

10. **Commit:** `test: capture pre-change reversal confirmation baseline`.

---

### Шаг 2. Зафиксировать shared mode/time контракты и выделить только разрешённые pure helpers

1. **Цель:** до нового runtime закрепить текущие shared mode/time контракты и устранить только безопасное дублирование target/window mapping.

2. **Причина:** новый runtime должен использовать ту же семантику `trade_mode`, что baseline и time-only, а будущий mode refactor не должен случайно изменить существующие combinations.

3. **Файлы:**

   - новый `donor/supertrend_optimizer/core/position_target.py`;
   - `donor/supertrend_optimizer/core/trade_filter_config.py` — characterization tests без production-правки mode table;
   - `donor/supertrend_optimizer/core/_reset_events.py`;
   - `donor/supertrend_optimizer/core/backtest.py`;
   - `donor/supertrend_optimizer/core/time_only_filter.py`;
   - новый `wf_grid/tests/test_st_position_target_parity.py`;
   - новый `wf_grid/tests/test_trade_filter_mode_characterization.py`;
   - новый `wf_grid/tests/test_time_only_shadow_contract.py`.

4. **Символы:** новый `target_from_trend`; optional pure `execution_window_identity`/`is_same_execution_window`; `generate_positions`; `_target_from_trend`; `is_any_trade_subfilter_enabled`; `resolve_trade_filter_mode`; `time_only_filter.apply`.

5. **Изменения:**

   - реализовать pure `target_from_trend(trend, trade_mode) -> np.ndarray[int8]`;
   - проверить одномерность массива и четыре допустимых mode;
   - сохранить семантику:
     - `revers/both`: `+1/-1/0`;
     - `long`: только `+1`, иначе `0`;
     - `short`: только `-1`, иначе `0`;
   - `generate_positions` использует helper и прежний сдвиг на один бар;
   - `_target_from_trend` либо становится совместимой обёрткой, либо удаляется после обновления только внутренних импортов;
   - выполнить exhaustive parity на массивах значений `{-1,0,+1}` и случайных детерминированных массивах разных длин;
   - characterization-тестом закрепить все текущие mode strings и ошибку `trade_filter_all_subfilters_disabled`;
   - тестами зафиксировать без рефакторинга фактические `raw_change`, `cycle_open`, `reentry_pending`, reset→flat→reentry и W1/W2/W5 в `time_only_filter.apply`;
   - если требуется общий код окна, вынести только pure вычисление `(local_date, window_id)` и same-window eligibility, не stateful entry/reset loop.

6. **Запрещено:** менять loop, reset, entry, `reentry_pending`, mode table, validator, boundary или diagnostics в `time_only_filter.apply`.

7. **Зависимости:** шаг 1.

8. **Проверка:**

```powershell
python -m pytest wf_grid/tests/test_st_position_target_characterization.py wf_grid/tests/test_st_position_target_parity.py wf_grid/tests/test_trade_filter_mode_characterization.py wf_grid/tests/test_time_only_shadow_contract.py wf_grid/tests/test_time_only_filter.py wf_grid/tests/test_time_filter_snapshots.py -q
```

Дополнительно сравнить с артефактом шага 1.

9. **Ожидаемый результат:** единый pure mapping и зафиксированный shared mode/time контракт при полном совпадении baseline positions и time-only результатов.

10. **Commit:** `refactor: pin shared filter contracts and centralize pure target mapping`.

---

### Шаг 3. Зафиксировать runtime DTO и реализовать изолированный reversal/combined runtime

1. **Цель:** сначала зафиксировать in-memory DTO, затем реализовать полный bar-by-bar контракт обоих modes, пока capability ещё недоступна из YAML.

2. **Причина:** FSM и combined position ownership должны быть проверены отдельно до transport, engine и exporters; это предотвращает переделку downstream schemas.

3. **Файлы:**

   - новый `donor/supertrend_optimizer/core/reversal_confirmation_filter.py`;
   - `donor/supertrend_optimizer/core/_reset_events.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_runtime.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_time_runtime.py`.

4. **Символы:**

   - `ReversalConfirmationResult`;
   - `ReversalConfirmationAttempt`;
   - `ReversalConfirmationSummary`;
   - `apply`;
   - `infer_daily_reset_event` — тонкая публичная обёртка над `_infer_daily_reset_event`;
   - private helper проверки same `(local_date, window_id)` execution boundary.

5. **Изменения:**

   - входы runtime: `trend`, `trade_mode`, `confirm_bars`, `index`, optional `TimeFilterRuntime`, `collect_filter_diagnostics`;
   - проверить до loop:
     - `OPEN_TO_OPEN`;
     - 1-D trend;
     - длины;
     - `DatetimeIndex`;
     - monotonic increasing;
   - monotonic duplicate timestamps не отклонять, поскольку ТЗ требует monotonic, но не unique;
   - реализовать состояния `ARMED`, `PENDING`, `WAIT_SOURCE_RETURN`;
   - хранить target/source как `int8`, bar/event counters как `int64`;
   - event bar не увеличивает confirmation counter;
   - pending стартует только по новому edge при открытой позиции и `trend == -current_position`;
   - `long/short` дополнительно проверяют raw trend, поскольку target `0` неоднозначен;
   - flat entry исполняется без confirmation;
   - порядок бара:
     1. bar-start position;
     2. window/reset/date events;
     3. reset/cancel/forced time action;
     4. raw trend и target;
     5. flat entry;
     6. start pending;
     7. continue/cancel/confirm pending;
     8. единственная запись `positions[t+1]`;
     9. diagnostics;
   - cancellation priority реализовать одной функцией:
     `time_filter_reset → daily_boundary → dataset_end → execution_outside_window → outside_window → trend_unready → trend_reverted`;
   - confirmation и scheduling выполнять атомарно только после проверки существования и eligibility `t+1`;
   - combined execution разрешать только в том же `(local_date, window_id)`;
   - reversal-only daily reset отменяет pending, но не закрывает позицию;
   - на dataset end финализировать каждый attempt;
   - event ID начинается с `0` в каждом `apply`, монотонен и не переиспользуется;
   - один O(n) loop, без forward search и чтения будущих OHLC/ST;
   - heavy per-bar object arrays выделять только при diagnostics on;
   - minimal numeric execution metadata создавать всегда.
   - до реализации Excel/Trades зафиксировать DTO:
     - `positions`;
     - optional heavy `filter_diagnostics`;
     - existing-reset + namespaced reversal `filter_execution_metadata` по разделу 4.6;
     - finalized attempt records по схеме `ReversalConfirmationEvents`;
     - scalar summary;
     - effective config snapshot;
   - combined runtime дословно переносит `raw_change`, `cycle_open`, `reentry_pending` и reset→flat→reentry из раздела 4.5;
   - запускать shadow-parity: при отсутствии pending combined совпадает с `time_only_filter.apply`, кроме отдельно asserted safe-boundary differences W2/W4/W5;
   - для каждого diff тест должен классифицировать его как ожидаемую boundary policy либо regression; не допускается blanket-нормализация результатов.

6. **Запрещено:** импортировать ZigZag/wakeup runtime, конвертировать timezone, вычислять даты независимо от `_reset_events.py`, менять `time_only_filter.py`.

7. **Зависимости:** шаг 2.

8. **Проверка:**

```powershell
python -m pytest wf_grid/tests/test_reversal_confirmation_runtime.py wf_grid/tests/test_reversal_confirmation_time_runtime.py -q
```

Матрица обязана покрыть N=1, N=3, valid boundary N=1_000_000 без миллиона runtime-итераций, L1–L4, S1–S4, W1–W5, TP1–TP8 shadow-parity cases (`raw_change`, `cycle_open`, `reentry_pending`, reset/reentry, target zero, two windows, daily boundary, sparse next bar), обе стороны reversal, cancel на первом/среднем/N-м confirmation bar, re-arm, `trend=0`, последний/предпоследний бар, sparse timestamps, два окна, смену дня, naive/tz-aware/DST.

9. **Ожидаемый результат:** полностью работающий и протестированный runtime, ещё недоступный внешней конфигурации.

10. **Commit:** `feat: add isolated reversal confirmation position runtime`.

---

### Шаг 4. Расширить существующий metadata transport и только затем изменить Trades attribution/summary

1. **Цель:** namespaced-расширением существующего `RawBacktestArtifacts.filter_execution_metadata` провести lifecycle/event информацию до trades и period results независимо от diagnostics-флага.

2. **Причина:** `collect_filter_diagnostics=false` не должен уничтожать `event_id`, execution linkage и exit reason.

3. **Файлы:**

   - `donor/supertrend_optimizer/core/backtest.py`;
   - `donor/supertrend_optimizer/engine/result.py`;
   - `donor/supertrend_optimizer/engine/run.py`;
   - `donor/supertrend_optimizer/testing/runner.py`;
   - `donor/supertrend_optimizer/core/filter_trade_diagnostics.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_result_contract.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_trades.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_summary.py`.

4. **Символы:** `RawBacktestArtifacts`, `BacktestResult`, `PeriodResult`, `attach_trade_filter_diagnostics`, `_build_filter_diagnostics_summary`.

5. **Изменения:**

   - сохранить существующее поле `RawBacktestArtifacts.filter_execution_metadata` и расширить его строго по схеме раздела 4.6;
   - добавить optional fields с default `None` туда, где их сейчас нет:
     - `BacktestResult.filter_execution_metadata`;
     - `PeriodResult.filter_execution_metadata` либо read-only forwarding через `PeriodResult.result`;
     - `reversal_confirmation_events`;
     - `reversal_confirmation_summary`;
   - existing constructors и inactive results должны оставаться совместимыми;
   - `BacktestResult.__post_init__` проверяет длины/dtype minimal metadata и уникальность event IDs;
   - изменить `attach_trade_filter_diagnostics` совместимо: heavy diagnostics остаётся прежним аргументом, compact execution metadata передаётся отдельным keyword-only аргументом с default `None`;
   - расширить trade attachment:
     - reset/ST причины брать по `decision_idx=exit_index-1`;
     - entry/exit event ID и confirmed reversal reason брать по фактическому `execution_idx=entry_index/exit_index`;
     - reversal reason ставить после time/daily reset и до `st_flip`;
     - terminal reason не должен перекрывать реальное confirmed execution на последнем баре;
   - не менять `extract_trades`: сделки по-прежнему строятся только из `positions`;
   - добавить пять summary counters и проверить:
     - `started = cancelled + confirmed`;
     - `confirmed = executed`;
     - reset count растёт только при отмене активного pending по двум reset-причинам;
   - diagnostics on/off должны давать идентичные positions, returns, trades и summary;
   - до production-правки cascade добавить отдельную conflict matrix из раздела 4.7 и regression текущих ZigZag/volume/time/wakeup reasons.

6. **Запрещено:** неструктурированно merge-ить heavy diagnostics и compact metadata, добавлять active-only колонки в base `extract_trades` или менять существующие exit reasons при выключенной capability.

7. **Зависимости:** шаг 3.

8. **Проверка:**

```powershell
python -m pytest wf_grid/tests/test_reversal_confirmation_result_contract.py wf_grid/tests/test_reversal_confirmation_trades.py wf_grid/tests/test_reversal_confirmation_summary.py wf_grid/tests/test_volume_exit_metadata_fallback.py -q
```

9. **Ожидаемый результат:** execution/event metadata переживает отключение diagnostics, а Trades и summary согласованы с positions.

10. **Commit:** `feat: propagate reversal event metadata into trades and summaries`.

---

### Шаг 5. Реализовать тонкие active-only export adapters и явное поведение `diagnostics_v2`

1. **Цель:** подготовить корректный export до внешней активации capability.

2. **Причина:** текущий `build_signal_events` описывает raw ST flips и не может считаться фактическим execution export при задержанном reversal.

3. **Файлы:**

   - `donor/supertrend_optimizer/testing/signal_events.py`;
   - `donor/supertrend_optimizer/io/excel_tester.py`;
   - `donor/supertrend_optimizer/io/diagnostics_v2.py`;
   - `donor/supertrend_optimizer/testing/runner.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_signal_tables.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_excel.py`.

4. **Символы:**

   - существующий `build_signal_events` — сохранить;
   - новые `build_reversal_execution_signals`;
   - `build_reversal_raw_signals`;
   - `build_reversal_confirmation_events`;
   - optional arguments `raw_signals_df` и `reversal_confirmation_events_df` в `export_tester_results`.

5. **Изменения:**

   - фактический `Signals` строить из `np.diff(positions)`, а не из raw flips;
   - использовать minimal metadata для event ID, source bar и status;
   - строить `RawSignals` отдельным active-only builder;
   - events table строить из финализированных attempts, не из per-bar diagnostics;
   - добавить Excel display names для новых полей;
   - добавить writers листов `RawSignals` и `ReversalConfirmationEvents`;
   - сделать ровно одну ветку выбора в tester runner/export preparation: `if reversal_active` вызывает новый bundle builder, `else` сохраняет текущий `build_signal_events`;
   - новые листы писать только если capability активна и `export.signals=true`;
   - Trades писать по существующему `export.trades`;
   - per-bar diagnostics и summary сохраняют существующие diagnostics gates;
   - disabled/inactive вызов `export_tester_results` должен идти по прежней ветке и не добавлять аргументы, листы или колонки.
   - `_diagnostics_mode` возвращает `reversal`, а не неявный `time_only`;
   - generic diagnostics_v2 payloads продолжают строиться;
   - cycle-derived diagnostics_v2 rows/sheets получают явный `SKIP/unsupported_mode` по разделу 4.9;
   - добавить тест active reversal + `export.diagnostics_v2=true`, подтверждающий отсутствие crash и ложной time-only/ZigZag cycle analytics.

6. **Запрещено:** глобально менять семантику или schema существующего `build_signal_events`, дублировать runtime reconstruction в exporters, называть raw reference фактическим execution или молча классифицировать reversal как time-only diagnostics.

7. **Зависимости:** шаг 4.

8. **Проверка:**

```powershell
python -m pytest wf_grid/tests/test_reversal_confirmation_signal_tables.py wf_grid/tests/test_reversal_confirmation_excel.py wf_grid/tests/test_time_filter_tester_export.py -q
```

Проверить exact column order, close-before-open, nullable fields, cancelled attempt без Signals/Trades и совпадение execution index во всех таблицах.

9. **Ожидаемый результат:** все active export-компоненты готовы, но внешний YAML пока по-прежнему не активирован.

10. **Commit:** `feat: add reversal execution signals and conditional Excel tables`.

---

### Шаг 6. Атомарно расширить shared mode/config contract и direct-single engine dispatch

1. **Цель:** согласованно изменить все точки контракта «at least one subfilter», затем реализовать dataclass, validation, modes, snapshots и guards для hand-built/direct `single` вызовов.

2. **Причина:** engine должен быть fail-closed даже при обходе YAML loader.

3. **Файлы:**

   - `donor/supertrend_optimizer/core/trade_filter_config.py`;
   - `donor/supertrend_optimizer/core/backtest.py`;
   - `donor/supertrend_optimizer/engine/run.py`;
   - `wf_grid/config/schema.py` — только re-export donor dataclass, без отдельной модели/валидации;
   - новый `wf_grid/tests/test_reversal_confirmation_config_contract.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_engine.py`.

4. **Символы:**

   - `TradeFilterReversalConfirmationConfig`;
   - поле `TradeFilterConfig.reversal_confirmation`;
   - `is_reversal_confirmation_enabled`;
   - `is_any_trade_subfilter_enabled`;
   - `resolve_trade_filter_mode`;
   - `_validate_subfilter_legality_dispatch`;
   - новый общий `assert_reversal_confirmation_allowed`;
   - public/effective snapshot builders;
   - `run_backtest_fast`;
   - `run_single_backtest`.

5. **Изменения:**

   - dataclass хранит raw `enabled` и `confirm_bars` без coercion;
   - validator выдаёт стабильные error keys из ТЗ;
   - строгий `bool`, строгий `int`, `bool` не принимается как int;
   - `1 <= confirm_bars <= 1_000_000`;
   - disabled + `confirm_bars` отклоняется;
   - конфликтующие active capability отклоняются;
   - выполнить shared mode refactor строго в порядке раздела 4.8: helper → mode table → validator else-branch → runtime guard → snapshots/fingerprint;
   - modes только `reversal_only` и `reversal_and_time`, все прежние mode strings остаются без изменений;
   - `trade_filter_all_subfilters_disabled` выдаётся только если выключены ZigZag, volume, time и reversal;
   - public snapshot содержит raw block;
   - effective snapshot содержит active flag, count, mode и boundary policy;
   - inactive block не меняет effective snapshot/fingerprint;
   - dispatch order:
     `zigzag → volume → reversal → time-only → generate_positions`;
   - direct active call разрешается только с `caller_mode="single"`;
   - общий pure/fail-fast `assert_reversal_confirmation_allowed` формирует одинаковые stable error keys для caller/config и engine checks;
   - вызвать его до расчёта Supertrend в `run_single_backtest` и повторно перед bar-loop в `run_backtest_fast`, не дублируя условия вручную;
   - inactive `caller_mode=""` остаётся допустимым;
   - на этом шаге не добавлять reversal в YAML whitelist tester или WF loader: внешняя конфигурация ещё должна отклоняться как unknown key;
   - `wf_grid/config/schema.py` только экспортирует новый dataclass для совместимости импортов; runtime plumbing и whitelist здесь не добавляются.

6. **Запрещено:** частично обновлять helper без validator/runtime guard, активировать reversal в `wf_grid`, optimizer или tester YAML до шага 7.

7. **Зависимости:** шаги 3–5.

8. **Проверка:**

```powershell
python -m pytest wf_grid/tests/test_reversal_confirmation_config_contract.py wf_grid/tests/test_reversal_confirmation_engine.py wf_grid/tests/test_w0_trade_filter_helpers.py wf_grid/tests/test_wp7_backtest_integration.py -q
```

9. **Ожидаемый результат:** direct `single` работает с полностью сформированным результатом; обход guards невозможен; tester YAML ещё не принимает capability.

10. **Commit:** `feat: add reversal config contract and guarded single-engine dispatch`.

---

### Шаг 7. Атомарно активировать tester YAML и end-to-end legacy pipeline

1. **Цель:** открыть capability в поддерживаемом tester pipeline только после готовности всех downstream-компонентов.

2. **Причина:** нельзя допустить commit, где YAML принимается, но создаёт неверные Signals, Trades или Excel.

3. **Файлы:**

   - `donor/supertrend_optimizer/core/trade_filter_config.py`;
   - `donor/supertrend_optimizer/cli/tester.py`;
   - `donor/supertrend_optimizer/testing/runner.py`;
   - `donor TESTER/run_batch_tester.py`;
   - `wf_grid/config/loader.py`;
   - `donor/supertrend_optimizer/io/excel_tester.py`;
   - новый `donor TESTER/tests/test_reversal_confirmation_tester_config.py`;
   - новый `donor TESTER/tests/test_reversal_confirmation_tester_e2e.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_pipeline_gates.py`.

4. **Символы:** `TRADE_FILTER_ALLOWED_KEYS`, `build_trade_filter_config_from_raw`, `validate_trade_filter`, `_validate_caller_pipeline_gate`, `load_tester_config`, `run_all_periods`, `run_period`, batch tester `main`.

5. **Изменения:**

   - добавить root key `reversal_confirmation` и child whitelist `enabled/confirm_bars`;
   - non-mapping block должен возвращать `reversal_confirmation_not_mapping`, а не generic ошибку;
   - builder обязан сохранить raw type/presence до validation;
   - loader вызывает shared validator с `caller_pipeline="tester"`;
   - equal-blocks возвращает `reversal_confirmation_equal_blocks_unsupported`;
   - `early_exit` в tester config при active capability отклоняется с `reversal_confirmation_early_exit_unsupported`;
   - `wf_grid/config/loader.py` распознаёт block, но shared validator с caller `wf_grid` возвращает `reversal_confirmation_unsupported_pipeline`;
   - optimizer, `wf_grid`, пустой и неизвестный caller не доходят до runtime;
   - каждый legacy period split вызывает отдельный `run_single_backtest`, получает свежий FSM и `event_id=0`;
   - tester active branch строит и передаёт все три signal/event таблицы;
   - disabled/inactive branch продолжает использовать прежний `build_signal_events` и прежний export call;
   - batch launcher пропускает equal-blocks с зафиксированным stable reason и выполняет legacy;
   - ошибки config/caller и engine должны использовать одинаковые stable keys.
   - перед merge этого commit обязаны быть green все direct runtime/metadata/trade/export tests шагов 3–6;
   - rollback-процедура: revert только этого activation commit возвращает unknown/unsupported YAML gate; dormant internal runtime/DTO остаются недостижимы и не влияют на baseline;
   - в `donor TESTER` меняются только canonical launcher plumbing и tests; новая бизнес-логика не дублируется в shadow-tree.

6. **Запрещено:** передавать reversal config в `run_equal_blocks`, `wf_grid/wf/step_executor.py`, optimizer или walk-forward runners.

7. **Зависимости:** шаг 6 и все export-шаги до него.

8. **Проверка:**

```powershell
python -m pytest "donor TESTER/tests/test_reversal_confirmation_tester_config.py" "donor TESTER/tests/test_reversal_confirmation_tester_e2e.py" wf_grid/tests/test_reversal_confirmation_pipeline_gates.py -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t5_equal_blocks_gate.py" wf_grid/tests/test_time_filter_tester_export.py -q
```

End-to-end fixture должен проверить `reversal_only`, `reversal_and_time`, четыре mode, diagnostics on/off, period split isolation и conditional Excel sheets.

9. **Ожидаемый результат:** tester YAML полностью поддерживает capability, все неподдерживаемые pipelines fail-closed до backtest, а активация безопасно откатывается одним commit.

10. **Commit:** `feat: activate reversal confirmation in legacy tester pipeline`.

---

### Шаг 8. Закрепить snapshots, fingerprint и пользовательскую документацию

1. **Цель:** сделать конфигурационную идентичность и правила использования наблюдаемыми и воспроизводимыми.

2. **Причина:** effective runtime settings должны участвовать в fingerprint; неактивный template не должен создавать ложное отличие baseline.

3. **Файлы:**

   - `wf_grid/baseline/fingerprint.py` — менять только если текущего делегирования shared snapshot недостаточно;
   - `donor/supertrend_optimizer/io/excel_tester.py`;
   - новый `docs/st_reversal_confirmation_usage.md`;
   - новый `wf_grid/tests/test_reversal_confirmation_snapshots.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_disabled_parity.py`.

4. **Символы:** `trade_filter_public_snapshot`, `trade_filter_effective_snapshot`, `_trade_filter_effective_snapshot_from_result_config`, Tester_Config metadata writer.

5. **Изменения:**

   - active effective snapshot включает mode, confirm bars и boundary policy;
   - fingerprint изменяется при изменении active `confirm_bars` или time combination;
   - отсутствующий/disabled block не меняет effective snapshot и fingerprint;
   - active public snapshot содержит raw fields;
   - disabled block не создаёт active-only Excel metadata, листы или колонки;
   - документация содержит минимальные YAML-примеры для `revers`, `both`, `long`, `short`, time combination, disabled block и invalid combinations;
   - описать `t0`, N confirmation bars и execution `t0+N+1`;
   - описать, что streaming, weekend filter и unsupported compositions не входят в v1.

6. **Запрещено:** обновлять pre-change baseline под новые результаты или скрывать реальную active-конфигурацию из fingerprint.

7. **Зависимости:** шаг 7.

8. **Проверка:**

```powershell
python -m pytest wf_grid/tests/test_reversal_confirmation_snapshots.py wf_grid/tests/test_reversal_confirmation_disabled_parity.py wf_grid/tests/test_time_filter_snapshots.py wf_grid/tests/test_baseline_fingerprint.py -q
```

Повторно запустить capture-команду в verify-mode без перезаписи baseline.

9. **Ожидаемый результат:** active run имеет однозначный fingerprint; absent/disabled run совпадает с pre-change baseline.

10. **Commit:** `docs: document reversal confirmation and pin snapshot parity`.

---

### Шаг 9. Добавить отдельный performance benchmark

1. **Цель:** доказать выполнение O(n) и лимита `reversal_only <= time_only + 30%`.

2. **Причина:** correctness-тесты не выявляют случайный forward search, повторное выделение object arrays и нелинейную деградацию.

3. **Файлы:**

   - новый `scripts/benchmark_reversal_confirmation.py`;
   - новый `wf_grid/tests/test_reversal_confirmation_performance_contract.py`.

4. **Символы:** `reversal_confirmation_filter.apply`, `time_only_filter.apply`.

5. **Изменения:**

   - генерировать одинаковый controlled trend и monotonic `DatetimeIndex` минимум на 1 000 000 баров, с зафиксированной плотностью opposite edges;
   - прогревать оба runtime;
   - запускать каждый пять раз в одном процессе;
   - сравнивать медианы;
   - reversal запускать с diagnostics off;
   - выводить individual timings, median, ratio, Python/NumPy/platform metadata;
   - в acceptance environment завершаться ненулевым кодом при превышении 1.30;
   - в обычном CI `slow_perf` режиме считать проверку advisory: печатать ratio и environment drift, но не блокировать correctness suite;
   - принимать параметры `--hard-fail` и `--advisory`, причём release/acceptance команда обязана использовать `--hard-fail`;
   - не использовать N=1_000_000 как длину подтверждаемой последовательности; это граница config/counter, а не отдельный workload.

6. **Запрещено:** использовать benchmark как parity proof или включать создание fixture в измеряемый интервал.

7. **Зависимости:** шаг 8.

8. **Проверка:**

```powershell
python scripts/benchmark_reversal_confirmation.py --bars 1000000 --runs 5 --hard-fail
python -m pytest wf_grid/tests/test_reversal_confirmation_performance_contract.py -m slow_perf -q
```

9. **Ожидаемый результат:** пять измерений, медианный ratio не выше 1.30, память растёт линейно.

10. **Commit:** `perf: add one-million-bar reversal confirmation benchmark`.

---

### Шаг 10. Выполнить финальный regression и acceptance gate

1. **Цель:** подтвердить готовность реализации целиком.

2. **Причина:** новая capability пересекает config, engine, time windows, trades, diagnostics, Excel и batch splits.

3. **Файлы:** production-файлы не изменяются; допускаются только недостающие тестовые fixtures/assertions, обнаруженные финальным прогоном.

4. **Модули проверки:** весь новый runtime и все существующие trade-filter/time/tester contracts.

5. **Проверки:**

   - все stable error keys;
   - отсутствующий и disabled block;
   - все mode и `both == revers`;
   - runtime N=1/N=3 и config/counter boundary N=1_000_000 без миллионного unit-loop;
   - обе стороны reversal;
   - long/short raw opposite condition;
   - flat entry и повторный entry;
   - position opened on current open + opposite edge on same close;
   - cancel/re-arm/continuous opposite trend;
   - совпадение нескольких cancel reasons;
   - dataset end и execution на последнем существующем баре;
   - W1–W5, два окна, смена дня, sparse bars;
   - naive, timezone-aware, DST;
   - reset > confirmation;
   - diagnostics on/off parity;
   - commission one-operation/two-operation;
   - positions/Signals/Trades/events execution-index linkage;
   - clean state и `event_id=0` на каждом split;
   - inactive Excel parity;
   - unsupported pipeline gates;
   - performance budget.

6. **Запрещено:** исправлять провал regression ослаблением assertion, обновлением baseline или изменением disabled-path semantics.

7. **Зависимости:** шаги 1–9.

8. **Команды:**

```powershell
python -m pytest wf_grid/tests/test_reversal_confirmation_*.py "donor TESTER/tests/test_reversal_confirmation_*.py" -q

python -m pytest wf_grid/tests/test_time_only_filter.py wf_grid/tests/test_time_filter.py wf_grid/tests/test_time_filter_multi_window_config.py wf_grid/tests/test_time_filter_snapshots.py wf_grid/tests/test_time_filter_tester_export.py wf_grid/tests/test_time_filter_rollout.py wf_grid/tests/test_time_filter_reset_runtime.py wf_grid/tests/test_w0_trade_filter_helpers.py wf_grid/tests/test_w9_regression_matrix.py wf_grid/tests/test_regression_suite.py -q

python -m pytest "donor TESTER/tests/test_phase2_tester_disabled_baseline.py" "donor TESTER/tests/test_phase2_tester_indexing_open_to_open.py" "donor TESTER/tests/test_phase2_tester_trade_diagnostics_attachment.py" "donor TESTER/tests/test_phase2_tester_cli_backward_compat.py" "donor TESTER/tests/test_phase2_wp_t5_equal_blocks_gate.py" -q

python -m pytest -q
python -m pytest -c donor/pytest.ini donor/tests -q
python scripts/benchmark_reversal_confirmation.py --bars 1000000 --runs 5 --hard-fail
```

9. **Ожидаемый результат:** все correctness/regression suites проходят, baseline verify не показывает отличий, benchmark укладывается в бюджет.

10. **Commit:** `test: complete reversal confirmation acceptance matrix`.

### Обязательные владельцы финального review

- `backtest.py` и `reversal_confirmation_filter.py`: Senior Python Developer + QA review off-by-one/state transitions;
- `trade_filter_config.py`: Tech Lead review shared mode/validation compatibility;
- `filter_trade_diagnostics.py`: Code Reviewer review bar-index mapping и legacy reason cascade;
- `signal_events.py`, `diagnostics_v2.py`, `excel_tester.py`: QA review exact schemas, flags и disabled parity;
- activation commit шага 7: Software Architect review pipeline gates и rollback.

Ориентировочный объём: **1.5–3 недели работы одного Senior Developer**, включая review и стабилизацию, без учёта ожидания выделенного performance environment. Планирование выполняется по отдельным commit-шагам; шаги 3–7 считаются critical path.

## 6. Итоговые критерии готовности

Реализация принимается только при одновременном выполнении условий:

- active YAML строго валидируется;
- direct caller не обходит gates;
- runtime не читает будущие OHLC/ST;
- event bar не считается confirmation bar;
- confirmation и запись `positions[t+1]` атомарны;
- reset имеет приоритет над confirmation;
- completed batch не оставляет active pending;
- `confirmed_count == executed_count`;
- cancelled attempt не создаёт Signals или Trades;
- combined execution остаётся в том же окне и календарном цикле;
- combined non-pending/flat entry scenarios проходят shadow-parity с `time_only_filter.apply`, кроме явно перечисленных safe-boundary differences;
- standalone time-only полностью совпадает с baseline;
- комиссия и trade extraction остаются positions-driven;
- diagnostics off не уничтожает event linkage;
- reset attribution использует decision bar, reversal attribution — execution bar;
- `diagnostics_v2` не классифицирует reversal как time-only и явно маркирует неподдерживаемую cycle analytics;
- absent/disabled capability проходит pre-change fingerprint и Excel parity;
- `wf_grid`, optimizer, equal-blocks, early exit и неизвестные callers отклоняются;
- полный suite и отдельный benchmark проходят.

## 7. Оценка реализуемости

- `reversal_only`: высокая реализуемость после shared mode refactor.
- `reversal_and_time`: средне-высокая реализуемость при обязательном shadow-parity suite раздела 4.5.
- Основной остаточный риск: точное воспроизведение time-only position ownership при сохранении отдельного combined runtime.

Оценка вероятности успешной реализации по обновлённому плану: **80% до начала реализации**, с ожидаемым ростом после green шагов 3–5. Оценка не заменяет acceptance gates и не используется как критерий merge.
