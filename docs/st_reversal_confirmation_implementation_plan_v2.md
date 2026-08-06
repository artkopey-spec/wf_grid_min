# Implementation Plan v2: `trade_filter.reversal_confirmation`

## 1. Статус документа

Этот документ является самостоятельной нормативной спецификацией реализации
`trade_filter.reversal_confirmation`. Для реализации не требуются другие планы.

При конфликте между описательным текстом, примерами и критериями приёмки приоритет
имеют:

1. нормативная bar-семантика раздела 5;
2. схемы данных раздела 8;
3. golden scenarios раздела 12;
4. критерии готовности раздела 17.

Номера строк исходного кода намеренно не фиксируются. Точки интеграции задаются
путями файлов и именами символов.

## 2. Цель

Реализовать независимую capability `trade_filter.reversal_confirmation`, которая:

- задерживает только изменение уже открытой позиции после противоположного
  изменения raw Supertrend;
- не задерживает первоначальные и повторные входы из `flat`;
- поддерживает `trade_mode`: `revers`, `both`, `long`, `short`;
- работает самостоятельно (`reversal_only`) и совместно с `time_filter`
  (`reversal_and_time`);
- использует только `open_to_open`: решение принимается на close бара `t`,
  позиция изменяется на open бара `t+1`;
- не читает будущие OHLC, Supertrend или timestamps;
- сохраняет расчёт returns, equity и комиссии через существующий
  `abs(diff(positions))`;
- при отсутствующем или выключенном блоке не изменяет позиции, returns, trades,
  diagnostics, fingerprint и legacy Excel.

Пример конфигурации:

```yaml
trade_filter:
  enabled: true
  reversal_confirmation:
    enabled: true
    confirm_bars: 2
```

Совместно с временным фильтром:

```yaml
trade_filter:
  enabled: true
  time_filter:
    enabled: true
    window: "10:00-18:00"
  reversal_confirmation:
    enabled: true
    confirm_bars: 2
```

## 3. Scope и ограничения

### 3.1. Поддерживается

- tester pipeline с legacy period splits;
- прямой `run_single_backtest(..., caller_mode="single")`;
- четыре существующих `trade_mode`;
- diagnostics on/off;
- naive и timezone-aware `DatetimeIndex`;
- несколько time windows;
- sparse bars и неуникальные, но монотонные timestamps;
- условный Excel export и tester run fingerprint.

### 3.2. Не поддерживается

- ZigZag, volume, wakeup и position freeze в одной конфигурации с reversal;
- `wf_grid`, optimizer, walk-forward и equal-blocks;
- early exit;
- execution model, отличный от `OPEN_TO_OPEN`;
- streaming/incremental state между отдельными вызовами;
- отдельная reversal cycle analytics в `diagnostics_v2`;
- перенос FSM между period splits.

Неподдерживаемые комбинации должны отклоняться до расчёта Supertrend.

### 3.3. Запрещённые изменения

- изменение формулы Supertrend, ATR, warmup, returns, equity или комиссии;
- внедрение reversal-логики в ZigZag/wakeup FSM;
- изменение результатов существующего `time_only_filter.apply`;
- универсальный framework композиции всех фильтров;
- дублирование business logic в `donor TESTER/supertrend_optimizer`;
- переименование capability в `st_confirmation`.

## 4. Текущая архитектура и маршрут данных

Нормативный маршрут tester:

```text
config_tester.yaml
  → cli/tester.py::load_tester_config
  → collect_trade_filter_unknown_keys
  → collect_raw_user_keys
  → build_trade_filter_config_from_raw
  → validate_trade_filter(caller_pipeline="tester")
  → resolve_trade_filter_mode_in_place
  → testing/runner.py::run_all_periods
  → run_period
  → engine/run.py::run_single_backtest
  → core/backtest.py::run_backtest_fast
  → core/reversal_confirmation_filter.py::apply
  → positions / metadata / attempts / summary
  → returns / trades
  → signal and Excel adapters
```

Основные точки интеграции:

- `core/backtest.py::generate_positions` — baseline position generation;
- `core/time_only_filter.py::apply` — time-only position owner;
- `core/backtest.py::run_backtest_fast` — runtime dispatch;
- `engine/run.py::run_single_backtest` — общий engine entry point;
- `core/trade_filter_config.py` — единая config schema и validation;
- `core/filter_trade_diagnostics.py::attach_trade_filter_diagnostics` —
  trade attribution;
- `testing/signal_events.py::build_signal_events` — legacy raw-ST analytics;
- `testing/runner.py::PeriodResult` — period transport;
- `io/excel_tester.py::export_tester_results` — tester export;
- `io/diagnostics_v2.py` — diagnostics export.

## 5. Нормативная bar-семантика

### 5.1. Базовые определения

Для каждого бара `t`:

- `positions[t]` — позиция, действующая с open бара `t`;
- `trend[t]` — raw Supertrend, известный только после close бара `t`;
- `target[t]` — разрешённая `trade_mode` позиция из `trend[t]`;
- решение бара `t` может изменить только `positions[t+1]`;
- `positions[0] = 0`.

Target mapping:

```text
revers/both: target = trend
long:        target = +1 только при trend == +1, иначе 0
short:       target = -1 только при trend == -1, иначе 0
```

### 5.2. Запуск подтверждения

Pending attempt стартует на close бара `t0`, только если:

1. на open `t0` уже есть позиция `current_position != 0`;
2. на `t0` возник новый raw opposite edge;
3. `trend[t0] == -current_position`;
4. FSM находится в состоянии `ARMED`;
5. combined time policy допускает decision bar.

Для `long` и `short` проверяется raw `trend`, а не только `target`, потому что
нулевой target не отличает противоположный тренд от warmup.

Переход из `flat` никогда не создаёт pending и исполняется по обычному
open-to-open правилу.

### 5.3. Счётчик подтверждения

`confirm_bars=N` означает:

- event bar `t0` не считается подтверждающим;
- подтверждающими являются бары `t0+1 ... t0+N`;
- на каждом из них raw trend должен оставаться противоположным исходной позиции;
- решение о подтверждении принимается на `d=t0+N`;
- исполнение происходит на `e=d+1=t0+N+1`.

При `N=1` edge на `t0`, подтверждение на `t0+1`, исполнение на `t0+2`.

### 5.4. Результат подтверждения

- `revers/both`: на `e` выполняется атомарный reversal
  `old_side → opposite_side`;
- `long/short`: на `e` выполняется только close `old_side → flat`;
- комиссия не рассчитывается внутри фильтра и остаётся positions-driven;
- close и open reversal имеют один `reversal_confirmation_event_id`.

### 5.5. Отмена и повторное вооружение

При отмене attempt позиция не изменяется, если отдельное time reset действие не
требует закрытия.

После отмены из-за возврата trend FSM переходит в `WAIT_SOURCE_RETURN`. Новый
attempt запрещён на непрерывном противоположном тренде. Возврат raw trend к
текущей стороне переводит FSM в `ARMED`; следующий свежий opposite edge может
создать новый attempt.

После подтверждённого reversal новая позиция считается текущей. Если на close
execution bar возникает свежий edge уже против новой позиции, разрешено начать
новый attempt на этом же decision bar.

### 5.6. Standalone boundary policy

В `reversal_only`:

- pending разрешено переносить через смену календарного дня;
- timezone и локальная полночь не влияют на positions;
- sparse gaps не отменяют pending;
- требуется только существование следующего execution bar;
- dataset end финализирует незавершённый attempt как `cancelled`.

Таким образом standalone capability не вводит скрытую календарную семантику.

### 5.7. Combined time policy

`reversal_and_time` использует существующие time-window membership и reset
events. Decision, confirmation и execution должны принадлежать допустимому
циклу `(local_calendar_date, window_id)`.

На `time_filter_reset[t]`:

1. active reversal attempt отменяется с причиной `time_filter_reset`;
2. выполняется одно time action `next_position = 0`;
3. forced exit отмечается только при открытой позиции и существующем `t+1`;
4. если `t` уже относится к новому окну и target ненулевой, устанавливается
   `reentry_pending`;
5. на том же баре не создаётся новый reversal attempt.

Initial/reentry и confirmed execution разрешены, только если `t+1` существует и
имеет тот же `(local_calendar_date, window_id)`, что decision bar. Это явная
combined safety policy. Существующий standalone time-only runtime своих
результатов не меняет.

### 5.8. Приоритет причин отмены

Если на одном баре применимо несколько условий, используется первый пункт:

1. `time_filter_reset`;
2. `outside_window`;
3. `trend_unready`;
4. `trend_reverted`;
5. `execution_unavailable`;
6. `execution_outside_window`;
7. `dataset_end`.

`execution_unavailable` применяется, когда N подтверждающих баров набраны, но
`t+1` отсутствует. `dataset_end` применяется к attempt, который не успел набрать
N баров.

### 5.9. Индекс и timezone

- active runtime требует `DatetimeIndex`;
- длина index равна длине trend;
- index должен быть monotonic increasing;
- duplicate timestamps разрешены;
- bar index является единственным первичным ключом linkage;
- timestamps используются только как отображаемый атрибут;
- для time-window identity используется существующая local wall-clock семантика
  `_reset_events.py`;
- DST и повторяющиеся local timestamps не объединяют события.

## 6. Архитектурное решение

### 6.1. Общий target helper

Создать `core/position_target.py`:

```python
target_from_trend(trend, trade_mode) -> np.ndarray[np.int8]
```

Helper валидирует 1-D input и `trade_mode`. `generate_positions`,
`time_only_filter` и reversal runtime используют только его.

### 6.2. Общий time transition-kernel

Нельзя копировать `raw_change/cycle_open/reentry_pending/reset` в новый runtime.
Создать небольшой модуль `core/time_entry_transition.py`:

```text
TimeEntryState:
  reentry_pending: bool

TimeEntryInputs:
  t, current_position, current_target, previous_target
  in_window, previous_in_window, time_reset

TimeEntryDecision:
  entry_attempt
  entry_allowed
  forced_flat
  forced_exit
  next_reentry_pending
  reason
```

Kernel:

- является pure function;
- не создаёт positions;
- не знает о reversal attempts;
- не является универсальным filter composer;
- используется `time_only_filter.apply` и `reversal_and_time`;
- до и после refactor обязан давать bit-exact time-only parity.

### 6.3. Единый capability resolver

Создать immutable DTO:

```text
ActiveTradeFilterCapabilities:
  root_enabled
  zigzag
  volume
  time_filter
  reversal_confirmation
  effective_mode
```

Одна функция `resolve_active_trade_filter_capabilities` является источником
истины для helper, validator, runtime dispatch, snapshots и exporter.

Truth table при `trade_filter.enabled=true`:

| ZigZag | Volume | Time | Reversal | Результат |
|---:|---:|---:|---:|---|
| 0 | 0 | 0 | 0 | error `trade_filter_all_subfilters_disabled` |
| 0 | 0 | 1 | 0 | `time_only` |
| 0 | 1 | 0 | 0 | `volume_only` |
| 0 | 1 | 1 | 0 | `volume_and_time` |
| 1 | 0 | 0 | 0 | `zigzag_only` |
| 1 | 0 | 1 | 0 | `zigzag_and_time` |
| 1 | 1 | 0 | 0 | `zigzag_and_volume` |
| 1 | 1 | 1 | 0 | `zigzag_volume_and_time` |
| 0 | 0 | 0 | 1 | `reversal_only` |
| 0 | 0 | 1 | 1 | `reversal_and_time` |
| `*` | 1 | `*` | 1 | error `reversal_confirmation_conflicting_capability` |
| 1 | `*` | `*` | 1 | error `reversal_confirmation_conflicting_capability` |

Все 16 boolean-комбинаций покрываются exhaustive test. Существующие mode strings
не переименовываются.

### 6.4. Runtime FSM

Создать `core/reversal_confirmation_filter.py`:

```text
ReversalConfirmationState = ARMED | PENDING | WAIT_SOURCE_RETURN
ReversalConfirmationAttempt
ReversalConfirmationSummary
ReversalConfirmationResult
apply(...)
```

`apply` выполняет один O(n) loop и является единственным владельцем positions в
режимах `reversal_only` и `reversal_and_time`.

Порядок обработки бара:

1. прочитать bar-start position;
2. распознать исполнение, уже отражённое в `positions[t]` записью прошлого
   decision bar, и отметить executed metadata;
3. определить time identity/reset;
4. применить reset/cancel/forced-flat;
5. прочитать raw trend и target текущего close;
6. обработать flat entry через time transition-kernel;
7. начать fresh pending;
8. продолжить, отменить или подтвердить pending;
9. проверить eligibility `t+1`;
10. единожды записать `positions[t+1]`;
11. записать compact metadata;
12. при включённых diagnostics записать heavy diagnostics.

Запрещены forward search, чтение `trend[t+1]` и object arrays при diagnostics off.

## 7. Конфигурация и gates

### 7.1. Dataclass

```text
TradeFilterReversalConfirmationConfig:
  enabled: raw bool | None
  confirm_bars: raw int | None
```

Raw values сохраняются без приведения типов до validation.

### 7.2. Validation

- `enabled` обязателен при наличии блока и имеет строгий тип `bool`;
- при `enabled:true` обязателен `confirm_bars`;
- `confirm_bars` — строгий `int`, `bool` не принимается;
- диапазон `1 <= confirm_bars <= 1_000_000`;
- при `enabled:false` поле `confirm_bars` запрещено;
- non-mapping block отклоняется специфическим ключом;
- `type: zigzag_st_mode` запрещён, если ZigZag не активен;
- отсутствующий и disabled reversal block не меняют effective mode.

Stable error keys:

```text
reversal_confirmation_not_mapping
reversal_confirmation_enabled_missing
reversal_confirmation_enabled_invalid_type
reversal_confirmation_confirm_bars_missing
reversal_confirmation_confirm_bars_invalid_type
reversal_confirmation_confirm_bars_out_of_range
reversal_confirmation_confirm_bars_present_when_disabled
reversal_confirmation_conflicting_capability
reversal_confirmation_unsupported_pipeline
reversal_confirmation_equal_blocks_unsupported
reversal_confirmation_early_exit_unsupported
reversal_confirmation_execution_model_unsupported
```

### 7.3. Caller gate

Единый pure helper
`assert_reversal_confirmation_allowed(caller, early_exit_enabled,
execution_model, segmentation_mode)` используется config validation и engine.

Разрешены только:

- `caller="tester"` + legacy segmentation;
- `caller="single"`.

Запрещены `wf_grid`, `optimizer`, пустой и неизвестный caller. Active early exit и
не-OPEN_TO_OPEN запрещены для обоих разрешённых caller.

Engine вызывает gate до Supertrend и повторно непосредственно перед reversal
bar-loop как defense in depth. Для этого `run_single_backtest` передаёт
нормализованный `caller_mode` в `run_backtest_fast`; новый trailing keyword-only
аргумент имеет default `""`, чтобы не ломать inactive callers.

## 8. Runtime DTO и схемы данных

### 8.1. Attempt record

```text
event_id: int64
source_signal_bar: int64
source_signal_time: datetime
source_position: int8
source_raw_trend: int8
confirmation_bar: nullable int64
confirmation_time: nullable datetime
execution_bar: nullable int64
execution_time: nullable datetime
confirmation_status: confirmed | cancelled
confirmation_cancel_reason: nullable string
```

Одна финальная запись на attempt. `event_id` начинается с 0 в каждом `apply`,
монотонен и не переиспользуется.

### 8.2. Summary

```text
started
confirmed
cancelled
executed
cancelled_by_time_reset
cancelled_by_boundary
```

Инварианты завершённого run:

```text
started == confirmed + cancelled
confirmed == executed
active_pending == 0
```

### 8.3. Compact execution metadata

Существующие reset keys сохраняются:

| Ключ | dtype/default | Индексация |
|---|---|---|
| `daily_reset_event` | `int8[n]`, 0 | decision bar |
| `time_filter_reset_event` | `int8[n]`, 0 | decision bar |

Active reversal добавляет:

| Ключ | dtype/default | Индексация |
|---|---|---|
| `reversal_confirmation_execution_event_id` | `int64[n]`, -1 | execution bar |
| `reversal_confirmation_execution_origin_bar` | `int64[n]`, -1 | execution bar |
| `reversal_confirmation_execution_decision_bar` | `int64[n]`, -1 | execution bar |
| `reversal_confirmation_execution_transition_kind` | `int8[n]`, 0 | execution bar |
| `reversal_confirmation_confirmed_close` | `int8[n]`, 0 | execution bar |
| `reversal_confirmation_confirmed_open` | `int8[n]`, 0 | execution bar |

Коды `reversal_confirmation_execution_transition_kind`:

```text
0 = none
1 = flat_raw_entry
2 = time_window_reentry
3 = time_forced_close
4 = confirmed_change
```

Для non-confirmed transition `event_id=-1`, но origin/decision/kind заполняются.
Для confirmed transition origin указывает original opposite edge, decision —
confirmation bar. Эти поля позволяют строить export без восстановления FSM.

`merge_filter_execution_metadata` проверяет namespace, 1-D shape, dtype, длину и
запрещает перезапись ключей.

Heavy diagnostics, compact metadata и attempt records всегда передаются
раздельно.

### 8.4. Result transport

Optional fields с default `None`:

- `RawBacktestArtifacts.filter_execution_metadata` — поле уже существует,
  расширяется без смены назначения;
- `BacktestResult.filter_execution_metadata`;
- `BacktestResult.reversal_confirmation_events`;
- `BacktestResult.reversal_confirmation_summary`;
- forwarding properties или поля в `PeriodResult`.

`BacktestResult.__post_init__` проверяет:

- длины и dtype metadata;
- уникальность non-negative execution event IDs;
- уникальность attempt IDs;
- соответствие каждого execution ID confirmed attempt;
- отсутствие ссылок на bar вне positions;
- summary invariants.

## 9. Trades attribution

`extract_trades` не меняется: trades остаются производными только от positions.

При active reversal добавляются nullable колонки:

```text
entry_reversal_confirmation_event_id: Int64
exit_reversal_confirmation_event_id: Int64
exit_reason: string
```

Lookup использует две системы координат:

```text
decision_idx = max(exit_index - 1, 0)
execution_idx = exit_index
```

Приоритет `exit_reason`:

1. существующий daily reset;
2. существующий time reset;
3. `reversal_confirmation_confirmed`;
4. неизменённый относительный порядок wakeup/volume/stopping причин;
5. terminal open-trade reason;
6. `st_flip`.

`attach_trade_filter_diagnostics` получает:

```python
attach_trade_filter_diagnostics(
    trades_df,
    heavy_diagnostics,
    *,
    execution_metadata=None,
)
```

Нельзя неявно подменять heavy diagnostics compact metadata.

## 10. Signal и Excel export

### 10.1. Обратная совместимость `Signals`

Существующий лист `Signals` и `build_signal_events` сохраняют текущую raw-ST
схему и порядок колонок при active reversal. Он остаётся источником для:

- false-start analytics;
- blocked raw signal attribution;
- generic `diagnostics_v2`;
- существующих внешних Excel consumers.

При active reversal `Signals.exec_price` является гипотетической raw-ST reference
price, а не доказательством фактической сделки. В `Tester_Config` записывается:

```text
signals_schema_version = 1
signals_semantics = raw_st_analytics
```

Схема legacy `Signals` не переименовывается и не урезается.

### 10.2. Новый `ExecutionSignals`

Фактические переходы positions экспортируются отдельным active-only листом:

```text
execution_signal_id
reversal_confirmation_event_id
event_type
side
transition_reason
execution_bar
execution_time
execution_price
origin_bar
origin_time
decision_bar
decision_time
confirmation_status
```

Правила:

- `execution_signal_id` уникален для каждой строки;
- `reversal_confirmation_event_id` nullable;
- `event_type`: `open` или `close`;
- `confirmation_status`: `not_required` или `confirmed`;
- confirmed reversal создаёт close, затем open на одном execution bar;
- `long/short` confirmation создаёт только close;
- фактическая цена равна `open[execution_bar]`;
- таблица строится из `np.diff(positions)` и compact metadata, без reconstruction
  runtime FSM;
- `origin_bar` означает raw-entry/reset/reentry decision для non-confirmed
  перехода и original opposite edge для confirmed transition;
- `decision_bar` всегда является close, который непосредственно запланировал
  изменение на следующем open.

Transition taxonomy:

| Переход | event ID | status | source |
|---|---|---|---|
| initial/flat raw entry | null | not_required | entry decision bar |
| time-window reentry | null | not_required | reentry decision bar |
| time forced close | null | not_required | reset decision bar |
| confirmed close/reversal | attempt ID | confirmed | original edge bar |

### 10.3. `ReversalConfirmationEvents`

Лист точно повторяет attempt schema раздела 8.1. Cancelled attempt не создаёт
`ExecutionSignals` или Trades transition.

### 10.4. Export gates

- `Signals`, `ExecutionSignals` и `ReversalConfirmationEvents` управляются
  существующим `export.signals`;
- Trades управляется `export.trades`;
- heavy diagnostics и summary управляются существующими diagnostics flags;
- inactive/disabled export следует прежней ветке и не создаёт новые листы,
  колонки или metadata;
- workbook получает `export_schema_version = 2` только при active reversal.

### 10.5. False-start и diagnostics compatibility

False-start всегда получает legacy `Signals`, а не `ExecutionSignals`.
Regression проверяет не только наличие листа, но и совпадение attribution,
forward-return и candle-ratio значений.

`diagnostics_v2` получает explicit effective capability mode из resolver:

- generic sheets продолжают строиться из legacy Signals/trades/diagnostics;
- `mode="reversal"` никогда не выводится из эвристики наличия колонок;
- `derive_trade_cycle_map(..., mode="reversal")` возвращает
  `mapping_status="unsupported_mode"`;
- cycle-derived sheets получают явный `SKIP/unsupported_mode`;
- reversal не классифицируется как `time_only`.

## 11. Tester run fingerprint

Создать `testing/run_identity.py` с canonical serializer и SHA-256:

```text
tester_run_fingerprint = sha256(
  input_data_identity
  + strategy_parameters
  + execution_model
  + effective_trade_filter_snapshot
  + export_schema_version
)
```

В identity входят:

- hash нормализованных OHLCV/index values;
- ATR period, multiplier, trade mode, commission;
- `confirm_bars`;
- effective mode;
- canonical time windows и boundary policy;
- period split definition;
- execution model.

Python/NumPy/platform записываются как информационная metadata, но не входят в
stable hash. Raw disabled template не входит в effective identity.

Fingerprint создаётся tester pipeline, не зависит от `wf_grid` и записывается в
`Tester_Config` только при active reversal. Изменение active `confirm_bars`,
time windows или boundary policy обязано менять fingerprint.

## 12. Нормативные golden scenarios

Нотация: `P[t]` — позиция на open `t`; `T[t]` — raw trend на close `t`;
`E(id,t)` — start attempt; `C(id,t)` — confirmation decision;
`X(id,t,reason)` — cancel; `R(id,e)` — execution.

Каждый сценарий реализуется как явный fixture с полными ожидаемыми arrays,
attempt records, metadata, trades и execution signals.

| ID | Условия | Ожидаемый результат |
|---|---|---|
| G01 | `flat`, `T=+1` | entry на следующем open, без attempt |
| G02 | повторный entry из flat после target 0 | entry без confirmation |
| G03 | `both` на тех же данных, что `revers` | bit-exact equality |
| R01 | long position, edge `+1→-1`, N=1, trend остаётся -1 | start `t0`, confirm `t0+1`, reversal `t0+2` |
| R02 | short mirror R01 | симметричный reversal |
| R03 | N=3, continuous opposite | execution строго `t0+4` |
| R04 | trend возвращается до N | cancelled `trend_reverted`, position unchanged |
| R05 | после R04 opposite остаётся непрерывным | новый attempt не создаётся |
| R06 | source trend вернулся, затем fresh edge | новый event ID и attempt |
| R07 | execution на текущем open, fresh opposite edge на том же close | разрешён новый pending |
| L01 | `trade_mode=long`, raw edge `+1→-1` | confirmed close в flat, без open short |
| L02 | continuous -1 после L01 | остаётся flat, без повторного attempt |
| L03 | после L01 raw trend возвращается +1 | обычный flat entry без confirmation |
| S01 | mirror L01 для short | confirmed close в flat |
| B01 | edge на последнем баре, N не набран | cancelled `dataset_end` |
| B02 | N набран на последнем баре | cancelled `execution_unavailable` |
| B03 | edge на предпоследнем баре, N=1 | нет execution bar, `execution_unavailable` |
| B04 | standalone attempt пересекает local midnight | attempt продолжается |
| B05 | standalone sparse gap | gap не отменяет attempt |
| T01 | combined decision и next bar в одном окне | entry/execution разрешены |
| T02 | decision внутри окна, next bar вне окна | execution запрещено |
| T03 | next bar в другом окне того же дня | `execution_outside_window` |
| T04 | next bar в том же window ID следующего дня | execution запрещено |
| T05 | time reset при active pending | cancel `time_filter_reset`, одно forced-flat действие |
| T06 | reset в начало нового окна с ненулевым target | flat, затем reentry через `reentry_pending` |
| T07 | target 0 очищает `reentry_pending` | повторного entry нет |
| T08 | два окна, без pending | combined совпадает с time kernel, кроме явной next-bar safety policy |
| I01 | duplicate timestamps | разные bar IDs и event linkage не сливаются |
| I02 | timezone-aware DST fallback | deterministic bar-index linkage |
| I03 | non-monotonic index | fail-fast до loop |
| D01 | diagnostics on/off | одинаковые positions, returns, trades, attempts, summary |
| M01 | reset и reversal metadata на одном transition | reset reason имеет приоритет |
| M02 | confirmed reversal на последнем существующем execution bar | reversal reason не перекрывается terminal reason |

Дополнительно property-based tests генерируют короткие sequences из
`{-1,0,+1}` и проверяют:

- `positions ∈ {-1,0,+1}`;
- отсутствие чтения будущего trend;
- один write `positions[t+1]` на decision bar;
- уникальность event IDs;
- `started == confirmed + cancelled`;
- `confirmed == executed`;
- cancelled attempt не создаёт trade/execution transition;
- все metadata bar references находятся в границах.

## 13. Пошаговая реализация

### 13.1. Карта production-файлов

Новые модули:

- `donor/supertrend_optimizer/core/position_target.py`;
- `donor/supertrend_optimizer/core/time_entry_transition.py`;
- `donor/supertrend_optimizer/core/reversal_confirmation_filter.py`;
- `donor/supertrend_optimizer/testing/reversal_confirmation_export.py`;
- `donor/supertrend_optimizer/testing/run_identity.py`;
- `docs/st_reversal_confirmation_usage.md`;
- `scripts/benchmark_reversal_confirmation.py`.

Изменяемые модули:

- `donor/supertrend_optimizer/core/backtest.py`;
- `donor/supertrend_optimizer/core/time_only_filter.py`;
- `donor/supertrend_optimizer/core/trade_filter_config.py`;
- `donor/supertrend_optimizer/core/filter_trade_diagnostics.py`;
- `donor/supertrend_optimizer/engine/run.py`;
- `donor/supertrend_optimizer/engine/result.py`;
- `donor/supertrend_optimizer/testing/runner.py`;
- `donor/supertrend_optimizer/testing/signal_events.py` — только сохранение и
  characterization legacy-контракта, без смены схемы;
- `donor/supertrend_optimizer/io/diagnostics_v2.py`;
- `donor/supertrend_optimizer/io/excel_tester.py`;
- `donor/supertrend_optimizer/cli/tester.py`;
- `donor TESTER/run_batch_tester.py`;
- `wf_grid/config/schema.py` — re-export dataclass;
- `wf_grid/config/loader.py` — unsupported pipeline gate без runtime plumbing.

Новая business logic существует только под `donor/supertrend_optimizer`.

### 13.2. Карта тестовых файлов

- `wf_grid/tests/test_reversal_confirmation_prechange_contract.py`;
- `wf_grid/tests/test_position_target_parity.py`;
- `wf_grid/tests/test_time_entry_transition_parity.py`;
- `wf_grid/tests/test_reversal_confirmation_runtime.py`;
- `wf_grid/tests/test_reversal_confirmation_time_runtime.py`;
- `wf_grid/tests/test_reversal_confirmation_properties.py`;
- `wf_grid/tests/test_reversal_confirmation_metadata.py`;
- `wf_grid/tests/test_reversal_confirmation_trades.py`;
- `wf_grid/tests/test_reversal_confirmation_export.py`;
- `wf_grid/tests/test_reversal_confirmation_diagnostics_v2.py`;
- `wf_grid/tests/test_reversal_confirmation_config.py`;
- `wf_grid/tests/test_reversal_confirmation_engine_gates.py`;
- `wf_grid/tests/test_reversal_confirmation_run_identity.py`;
- `wf_grid/tests/test_reversal_confirmation_disabled_parity.py`;
- `wf_grid/tests/test_reversal_confirmation_performance.py`;
- `donor TESTER/tests/test_reversal_confirmation_tester_config.py`;
- `donor TESTER/tests/test_reversal_confirmation_tester_e2e.py`.

### Шаг 1. Characterization и baseline

Файлы:

- новый `wf_grid/tests/test_reversal_confirmation_prechange_contract.py`;
- новый deterministic fixture/helper в tests;
- golden JSON только для нормализованных arrays, trades и Excel schemas.

Зафиксировать absent, disabled и time-only:

- positions/returns dtype, shape и canonical hash;
- normalized Trades;
- effective snapshot;
- порядок листов и колонок;
- false-start attribution;
- четыре trade mode и `both == revers`.

Не создавать второй универсальный baseline framework. Использовать существующие
canonical fingerprint/normalization helpers, где они применимы.

### Шаг 2. Pure refactor без feature activation

- добавить `position_target.py`;
- добавить `time_entry_transition.py`;
- перевести baseline и time-only на общие helpers;
- доказать bit-exact parity time-only и disabled path;
- не менять mode table или YAML whitelist.

Gate: полный time-filter regression green.

### Шаг 3. Изолированный reversal runtime

- реализовать FSM, DTO, standalone и combined loop;
- использовать общий time transition-kernel;
- реализовать boundary/cancel policy раздела 5;
- покрыть golden scenarios G/R/L/S/B/T/I;
- runtime пока недоступен из YAML и engine dispatch.

### Шаг 4. Metadata, result transport и trades

- реализовать namespaced metadata merge;
- расширить `BacktestResult` и `PeriodResult`;
- добавить summary validation;
- изменить trade attachment с отдельным metadata argument;
- покрыть conflict matrix M01/M02 и inactive parity.

### Шаг 5. Export adapters и diagnostics

- сохранить legacy `Signals`;
- добавить `ExecutionSignals` и `ReversalConfirmationEvents`;
- добавить schema metadata;
- адаптировать explicit diagnostics mode;
- доказать содержательную false-start parity;
- проверить exact column order, nullable dtypes и close-before-open.

### Шаг 6. Config resolver и direct-single activation

- добавить dataclass и capability resolver;
- покрыть truth table всех 16 combinations;
- добавить validation/error keys;
- добавить единый caller/runtime gate;
- подключить dispatch:

```text
zigzag → volume → reversal → time-only → baseline
```

- разрешить только hand-built validated config с `caller_mode="single"`;
- YAML whitelist tester/WF пока не открывать.

Gate: direct single выдаёт полный согласованный result; early exit и неверный
caller отклоняются до Supertrend.

### Шаг 7. Tester run identity

- реализовать tester-specific canonical fingerprint;
- проверить sensitivity к active config и insensitivity к disabled template;
- добавить metadata в active `Tester_Config`;
- не изменять inactive workbook.

### Шаг 8. Атомарная активация tester YAML

- открыть root/child whitelist;
- подключить builder и validator;
- разрешить `caller_pipeline="tester"`;
- `wf_grid` должен распознавать block, но возвращать stable unsupported key;
- equal-blocks, optimizer и WF не передают config в engine;
- каждый period split создаёт свежий FSM и начинает event IDs с 0;
- CLI и batch launcher строят один export bundle;
- rollback этого commit снова закрывает YAML, не меняя baseline.

Gate: runtime, metadata, trades, export, fingerprint и disabled parity tests green
до merge activation commit.

### Шаг 9. Документация и snapshots

Создать пользовательскую документацию с:

- YAML examples для четырёх trade mode;
- `t0`, N confirmation bars и execution `t0+N+1`;
- standalone midnight policy;
- combined same-window execution policy;
- unsupported combinations;
- описанием `Signals` и `ExecutionSignals`;
- fingerprint и schema version.

### Шаг 10. Performance

Benchmark:

- одинаковые trend/index и setup для обоих runtime;
- diagnostics off у reversal и time-only;
- setup исключён из измеряемого интервала;
- размеры 125k, 250k, 500k, 1m;
- минимум 7 runs, median и coefficient of variation;
- low и medium edge density;
- peak RSS измеряется отдельно;
- проверяется приблизительно линейный time/memory slope;
- acceptance machine проверяет
  `median(reversal_only) <= median(time_only) * 1.30`;
- обычный CI выводит advisory result, но не блокирует correctness.

`confirm_bars=1_000_000` проверяется на коротком fixture как config/counter
boundary, а не миллионным pending loop.

### Шаг 11. Финальный acceptance

Targeted correctness:

```powershell
python -m pytest wf_grid/tests/test_reversal_confirmation_runtime.py wf_grid/tests/test_reversal_confirmation_time_runtime.py wf_grid/tests/test_reversal_confirmation_properties.py wf_grid/tests/test_reversal_confirmation_metadata.py wf_grid/tests/test_reversal_confirmation_trades.py wf_grid/tests/test_reversal_confirmation_export.py wf_grid/tests/test_reversal_confirmation_diagnostics_v2.py wf_grid/tests/test_reversal_confirmation_config.py wf_grid/tests/test_reversal_confirmation_engine_gates.py wf_grid/tests/test_reversal_confirmation_run_identity.py wf_grid/tests/test_reversal_confirmation_disabled_parity.py -q
```

Existing shared-contract regression:

```powershell
python -m pytest wf_grid/tests/test_time_only_filter.py wf_grid/tests/test_time_filter.py wf_grid/tests/test_time_filter_multi_window_config.py wf_grid/tests/test_time_filter_snapshots.py wf_grid/tests/test_time_filter_tester_export.py wf_grid/tests/test_time_filter_rollout.py wf_grid/tests/test_time_filter_reset_runtime.py wf_grid/tests/test_w0_trade_filter_helpers.py wf_grid/tests/test_w9_regression_matrix.py wf_grid/tests/test_regression_suite.py -q
```

Tester activation and legacy regression:

```powershell
python -m pytest "donor TESTER/tests/test_reversal_confirmation_tester_config.py" "donor TESTER/tests/test_reversal_confirmation_tester_e2e.py" "donor TESTER/tests/test_phase2_tester_disabled_baseline.py" "donor TESTER/tests/test_phase2_tester_indexing_open_to_open.py" "donor TESTER/tests/test_phase2_tester_trade_diagnostics_attachment.py" "donor TESTER/tests/test_phase2_tester_cli_backward_compat.py" "donor TESTER/tests/test_phase2_wp_t5_equal_blocks_gate.py" -q
```

Full suites and acceptance performance:

```powershell
python -m pytest -q
python -m pytest -c donor/pytest.ini donor/tests -q
python scripts/benchmark_reversal_confirmation.py --bars 125000 250000 500000 1000000 --runs 7 --hard-fail
```

Baseline verification выполняется отдельным verify-mode тестом без перезаписи
golden artifact.

PowerShell-команды не должны полагаться на shell glob вида
`test_reversal_confirmation_*.py`; список файлов формируется явно или через
pytest discovery directory.

## 14. Rollout и rollback

- До шага 8 YAML остаётся fail-closed.
- Activation находится в одном commit.
- Нельзя merge activation с красными downstream tests.
- Disabled и absent paths не получают новые runtime branches или Excel fields.
- Rollback — revert activation commit; internal runtime остаётся недостижим из
  YAML.
- Workbook schema version позволяет внешним consumers явно различать active
  extended export.
- Release notes предупреждают, что `ExecutionSignals` является источником
  фактических исполнений, а `Signals` — raw-ST analytics.

## 15. Технические риски и mitigation

| Риск | Mitigation |
|---|---|
| Drift time-only/combined | общий time transition-kernel + bit-exact parity |
| Off-by-one confirmation | golden N=1/N=3 + bar-index metadata assertions |
| Поломка false-start | legacy Signals сохраняется + content regression |
| Ложный diagnostics mode | explicit capability mode, без column heuristic |
| Поломка существующих mode strings | единый resolver + exhaustive truth table |
| Metadata теряется при diagnostics off | отдельный compact transport |
| Early-exit dangling events | единый fail-fast gate до Supertrend |
| Excel consumer breakage | новый лист, schema version, legacy Signals intact |
| Timezone ambiguity | bar index primary key + DST fixtures |
| Performance noise | multi-size/multi-run benchmark, fixed environment |
| Config drift между pipelines | shared validator и одинаковые stable keys |

## 16. Оценка работ

Реалистичная оценка для одного Senior Developer:

| Блок | Оценка |
|---|---:|
| Characterization и pure refactor | 3–5 дней |
| Runtime standalone/combined | 5–7 дней |
| Metadata, trades и result transport | 3–5 дней |
| Export, diagnostics и fingerprint | 4–6 дней |
| Config activation и E2E | 3–4 дня |
| Performance, docs, stabilization | 3–5 дней |

Итого: **4–6 рабочих недель**, включая code review и стабилизацию, без ожидания
выделенного performance environment.

Critical path: шаги 2–8. Резерв не менее 20% закладывается на Excel/diagnostics
integration defects.

## 17. Итоговые критерии готовности

Реализация принимается только при одновременном выполнении условий:

- absent/disabled path проходит pre-change positions, returns, trades,
  fingerprint и Excel parity;
- все 16 capability combinations имеют однозначный mode или stable error;
- все unsupported callers и early exit отклоняются до Supertrend;
- runtime не читает будущие OHLC/ST/index;
- event bar не считается confirmation bar;
- execution происходит строго на `t0+N+1`;
- standalone pending переносится через local midnight;
- combined execution остаётся в том же local date/window;
- time-only results после shared refactor bit-exact прежним;
- completed run не оставляет active pending;
- summary invariants выполняются;
- cancelled attempt не создаёт execution signal или trade;
- commission и trade extraction остаются positions-driven;
- diagnostics on/off дают одинаковую экономику и event linkage;
- reset/reversal attribution использует правильные decision/execution indices;
- legacy Signals и false-start сохраняют схему и содержательную работоспособность;
- ExecutionSignals отражает каждый фактический position transition;
- diagnostics_v2 не классифицирует reversal как time-only;
- active tester fingerprint чувствителен ко всем effective settings;
- inactive workbook не получает active-only metadata, sheets или columns;
- все golden scenarios и property tests green;
- полный regression suite green;
- acceptance performance budget выполнен.

## 18. Обязательный review

- Senior Python Developer: FSM, off-by-one, shared time kernel;
- Tech Lead: capability resolver, validation и pipeline gates;
- QA: golden scenarios, diagnostics on/off, period isolation;
- Data/Reporting reviewer: Signals, ExecutionSignals, false-start и Excel schemas;
- Software Architect: activation commit, fingerprint и rollback;
- Performance owner: hard benchmark на закреплённой машине.

После review любые изменения нормативной bar-семантики, boundary policy или
export schema требуют обновления golden scenarios и schema version до merge.
