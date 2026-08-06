# ТЗ v3: подтверждение изменения открытой позиции по Supertrend

Статус: согласованный контракт для подготовки implementation plan.

Ключевой принцип совместимости: отсутствие блока
`reversal_confirmation` или `enabled: false` сохраняет текущий baseline
побитово, включая существующую самостоятельную семантику `time_filter`.
Уточнённая проверка допустимости execution bar применяется только в новом
локальном runtime `reversal_confirmation` и
`reversal_confirmation + time_filter`.

## 0. Разграничение функций и non-goals

`trade_filter.reversal_confirmation` подтверждает только изменение уже
открытой позиции. Он не является и не должен переиспользовать:

- `wakeup_regime.entry.st_confirmation`, который задерживает вход и имеет
  отдельную YAML-семантику;
- ZigZag FSM или wakeup lifecycle;
- будущий `weekend_filter`;
- универсальную систему композиции фильтров.

Имена config, diagnostics и events новой capability всегда начинаются с
`reversal_confirmation_`. Имена вида `st_confirmation_*` для неё запрещены.

Совместимость с будущим weekend filter в v1 не реализуется. Если внешний
preprocessing удаляет weekend bars, `confirm_bars` считает только фактически
присутствующие trading bars входного датасета, а смена дня определяется по
календарной дате индекса после filtering.

## 1. Назначение

Реализовать независимый фильтр `reversal_confirmation`, который задерживает
изменение уже открытой позиции после противоположного изменения направления
Supertrend и применяет это изменение только после заданного числа подтверждающих
баров.

Фильтр:

- работает со всеми поддерживаемыми `trade_mode`: `revers`, `both`, `long`,
  `short`;
- не подтверждает первоначальный или повторный вход из `flat`;
- не требует ZigZag, wakeup Mode D, volume-фильтра или
  `wakeup_regime.position_freeze`;
- корректно взаимодействует с `time_filter`;
- не меняет расчёт Supertrend, модель исполнения и начисление комиссии;
- при отсутствии блока или `enabled: false` полностью сохраняет текущий
  результат.

Документ задаёт контракт будущей реализации. Архитектурные детали реализации
должны быть привязаны к фактическим файлам и строкам репозитория в отдельном
implementation plan.

## 2. Термины

- `trend[t]` — завершённое направление Supertrend на баре `t`:
  `+1`, `-1` или `0`.
- `positions[t]` — позиция, действующая с открытия бара `t`.
- `current_position` — `positions[t]`.
- `baseline_target[t]` — позиция, которую без нового фильтра сформировал бы
  текущий `trade_mode` по `trend[t]`.
- `t0` — бар противоположного ST-перехода, запускающий pending.
- `N` — `confirm_bars`.
- `pending` — ожидание подтверждения изменения открытой позиции.
- `reset` — событие `time_filter_reset` или календарный дневной reset
  pending-состояния.
- `initiating entry` — вход из `flat`, начинающий новый торговый цикл.

Для `open_to_open` решение формируется после завершения бара `t`, а изменение
позиции исполняется на открытии бара `t + 1` посредством записи
`positions[t + 1]`.

## 3. Конфигурация

Целевое расположение:

```yaml
trade_mode: revers

trade_filter:
  enabled: true

  time_filter:
    enabled: true
    windows:
      - "09:00-13:00"
      - "16:00-19:00"

  reversal_confirmation:
    enabled: true
    confirm_bars: 1
```

`reversal_confirmation` располагается в корне `trade_filter`, рядом с
`time_filter`, так как это независимая capability, а не часть ZigZag или wakeup.

### 3.1. Валидация

1. `reversal_confirmation` должен быть mapping.
2. Разрешены только ключи `enabled` и `confirm_bars`.
3. Если блок присутствует, `enabled` обязателен и имеет строгий тип `bool`.
4. При `enabled: true`:
   - `trade_filter.enabled` обязан быть `true`;
   - `confirm_bars` обязателен;
   - `confirm_bars` имеет строгий тип `int`;
   - `bool` не считается допустимым `int`;
   - `1 <= confirm_bars <= 1_000_000`.
5. `confirm_bars: 0` запрещён. Отключение выполняется только через
   `enabled: false` или отсутствие блока.
6. При `enabled: false` ключ `confirm_bars` должен отсутствовать, чтобы
   конфигурация не содержала молча игнорируемых значений.
7. Неизвестные ключи отклоняются существующим strict-schema механизмом.
8. Допустимы `trade_mode`: `revers`, `both`, `long`, `short`.
9. `both` имеет ту же семантику, что `revers`.
10. Верхняя граница `1_000_000` защищает runtime и diagnostics от
    непрактичных значений; внутренние счётчики хранятся как `int64`.
11. При активной capability обязателен монотонно возрастающий
    `DatetimeIndex`, независимо от состояния `time_filter`.
12. Integer/RangeIndex, немонотонный индекс и несовпадение длины индекса с
    OHLC отклоняются до запуска bar-loop.

Правило об отсутствии `confirm_bars` при `enabled: false` намеренно строже
некоторых соседних config-блоков и является anti-footgun. При миграции старых
конфигов неиспользуемый ключ нужно удалить, а не оставлять рядом с
`enabled: false`.

### 3.2. Допустимые комбинации v1

Разрешены:

- только `reversal_confirmation`;
- `reversal_confirmation + time_filter`.

При включённом `reversal_confirmation` v1 валидатор отклоняет:

- `zigzag.enabled: true`;
- `volume.enabled: true`;
- `wakeup_regime.enabled: true`;
- `wakeup_regime.position_freeze.enabled: true`.

Наличие выключенных блоков не является ошибкой.

Причина ограничения: ZigZag, volume и wakeup имеют собственное состояние и
могут самостоятельно владеть позицией. Их композиция с задержкой изменения
позиции требует отдельного контракта порядка событий и не входит в v1.

### 3.3. Канонические effective modes

Новая capability добавляет только два mode:

| Активные capability | `effective_mode` | Runtime |
|---|---|---|
| только `reversal_confirmation` | `reversal_only` | новый baseline reversal runtime |
| `reversal_confirmation + time_filter` | `reversal_and_time` | тот же runtime с safe execution boundary |

Существующие mode (`time_only`, `volume_only`, `zigzag_only` и их текущие
комбинации) не переименовываются и не меняют семантику.

Обязательные config-layer изменения:

- `is_any_trade_subfilter_enabled` учитывает `reversal_confirmation`;
- `resolve_trade_filter_mode` возвращает два mode из таблицы;
- public snapshot содержит raw `enabled` и `confirm_bars`;
- effective snapshot содержит `reversal_confirmation_active`,
  `reversal_confirmation_confirm_bars`, `effective_mode` и boundary policy;
- fingerprint включает effective snapshot новой capability;
- `trade_filter_mode` diagnostics использует только канонические строки.

Ветвление `run_backtest_fast`:

```text
zigzag active                  -> существующий ZigZag runtime
volume active                  -> существующий volume runtime
reversal_confirmation active  -> reversal_only / reversal_and_time
time only                      -> существующий time_only runtime
none                           -> неизменный generate_positions baseline
```

Валидация v1 гарантирует, что reversal не попадёт одновременно в ZigZag или
volume ветвь.

### 3.4. Стабильные error keys

Новые validation/gate ошибки должны иметь стабильные ключи:

```text
reversal_confirmation_not_mapping
reversal_confirmation_enabled_missing
reversal_confirmation_enabled_invalid_type
reversal_confirmation_confirm_bars_missing
reversal_confirmation_confirm_bars_invalid_type
reversal_confirmation_confirm_bars_out_of_range
reversal_confirmation_disabled_has_confirm_bars
reversal_confirmation_conflicting_capability
reversal_confirmation_datetime_index_required
reversal_confirmation_index_not_monotonic
reversal_confirmation_unsupported_pipeline
reversal_confirmation_equal_blocks_unsupported
reversal_confirmation_early_exit_unsupported
reversal_confirmation_unknown_caller
```

## 4. Семантика по `trade_mode`

`baseline_target[t]` определяется существующей семантикой:

| `trade_mode` | `trend[t] = +1` | `trend[t] = -1` | `trend[t] = 0` |
|---|---:|---:|---:|
| `revers` / `both` | `+1` | `-1` | `0` |
| `long` | `+1` | `0` | `0` |
| `short` | `0` | `-1` | `0` |

Фильтр применяется только когда:

```text
current_position != 0
and baseline_target[t] != current_position
```

Целевое действие после подтверждения:

| `trade_mode` | Текущая позиция | Противоположный ST | Подтверждённое действие |
|---|---:|---:|---|
| `revers` / `both` | `+1` | `-1` | `+1 → -1` |
| `revers` / `both` | `-1` | `+1` | `-1 → +1` |
| `long` | `+1` | `-1` | `+1 → 0` |
| `short` | `-1` | `+1` | `-1 → 0` |

Таким образом, для `long` и `short` подтверждается закрытие открытой позиции,
а не запрещённый направлением разворот.

## 5. Вход из `flat`

Входы из `flat` не проходят через `reversal_confirmation`.

Если `current_position == 0`:

- допустимый `baseline_target[t] != 0` создаёт обычный initiating entry;
- при активном `time_filter` вход разрешён только внутри окна;
- вход формируется по актуальному направлению Supertrend, даже если исходный
  raw ST-flip произошёл вне окна;
- внешний flip не создаёт и не переносит pending;
- вход нового окна является новым инициирующим событием, а не исполнением
  сохранённого внешнего flip.

Для `open_to_open` initiating entry, сформированный на завершении первого
допустимого бара окна, исполняется на открытии следующего бара. Исполнение
разрешено только если следующий бар также входит в то же разрешённое окно.

Если окно содержит недостаточно баров для безопасного исполнения, позиция не
открывается за его пределами.

Эта проверка execution bar является семантикой только нового combined runtime
`reversal_confirmation + time_filter`. Самостоятельный существующий
`time_filter` при выключенной или отсутствующей capability не изменяется.
Изменение его исторической boundary-семантики является отдельной задачей и не
входит в данное ТЗ.

Канонические названия двух политик:

- `time_only_legacy_boundary` — существующий standalone `time_only`;
- `combined_safe_execution_boundary` — новый `reversal_and_time`.

Нормативная разница:

| Сценарий на close `t` | `time_only_legacy_boundary` | `combined_safe_execution_boundary` |
|---|---|---|
| `t` внутри, `t+1` вне окна, flat entry | сохраняет текущий baseline | entry не создаётся |
| `t` внутри, `t+1` вне окна, confirmation | неприменимо | pending отменяется: `execution_outside_window` |
| `t` — последний бар датасета | сохраняет текущий baseline | execution не создаётся, attempt отменяется |
| `t+1` в другом окне того же дня | сохраняет текущий baseline | execution запрещён |
| `t+1` в том же окне и дне | обычная запись `positions[t+1]` | обычная запись `positions[t+1]` |

Реализация не имеет права менять control-flow `time_only_filter.apply`.
Разрешён только общий pure helper преобразования `trend -> baseline_target`,
подтверждённый parity-тестами; boundary/control-flow standalone пути остаётся
неизменным.

## 6. Точная семантика подтверждения

Пусть:

- на открытии бара `t0` действует позиция `P`;
- на завершении бара `t0` Supertrend впервые формирует
  `baseline_target[t0] != P`;
- `confirm_bars = N`.

Бар `t0` является event bar и не входит в число подтверждающих баров.
После создания pending обработка `reversal_confirmation` на `t0` завершается:
на event bar нельзя увеличить `pending_bars_confirmed` или подтвердить pending.

Подтверждающие бары:

```text
t0 + 1, t0 + 2, ..., t0 + N
```

Pending считается устойчиво подтверждённым, если на каждом подтверждающем баре
сохраняются оба условия:

```text
baseline_target[t] == pending_target
and opposite_st_condition == true
```

На завершении бара `t0 + N` принимается решение. Для `open_to_open`
подтверждённое изменение исполняется:

```text
execution_bar = t0 + N + 1
positions[execution_bar] = pending_target
```

До `execution_bar` продолжает действовать исходная позиция `P`.

### 6.1. Пример `confirm_bars: 1`

| Бар | `trend` / target | `position_active_on_open` | `decision_on_close` | Запись |
|---|---:|---:|---|---|
| `t0` | `-P` | `P` | pending started, `0/1` | `positions[t0+1] = P` |
| `t0+1` | `-P` | `P` | confirmed `1/1` | `positions[t0+2] = new_position` |
| `t0+2` | `-P` | `new_position` | execution event уже на open | без повторной записи |

`new_position = -P` для `revers/both` и `0` для `long/short`.
Execution index всегда `t0+2`; показанная на `t0+1` запись не означает, что
позиция действует на open `t0+1`.

### 6.2. Пример `confirm_bars: 3`

| Бар | Target | Позиция на open | Событие на close |
|---|---:|---:|---|
| `t0` | `-P` | `P` | pending started, confirmed `0/3` |
| `t0+1` | `-P` | `P` | confirmed `1/3` |
| `t0+2` | `-P` | `P` | confirmed `2/3` |
| `t0+3` | `-P` | `P` | confirmed `3/3` |
| `t0+4` | `-P` | новая | новая позиция действует с open |

### 6.3. Нормативные traces для `long` и `short`

Во всех таблицах решение на close `t` действует только с open `t+1`.

| Case | Исходная позиция | Последовательность `trend` | Результат |
|---|---:|---|---|
| L1 | `+1` | `+1 → 0` | pending не стартует |
| L2 | `+1` | `+1 → -1`, затем `-1` N баров | подтверждённое закрытие `+1 → 0` |
| L3 | `+1` | `+1 → -1 → 0` | pending отменён: `trend_unready` |
| L4 | `+1` | `+1 → -1 → +1` | pending отменён; state снова `ARMED` |
| S1 | `-1` | `-1 → 0` | pending не стартует |
| S2 | `-1` | `-1 → +1`, затем `+1` N баров | подтверждённое закрытие `-1 → 0` |
| S3 | `-1` | `-1 → +1 → 0` | pending отменён: `trend_unready` |
| S4 | `-1` | `-1 → +1 → -1` | pending отменён; state снова `ARMED` |

После подтверждённого close в `long/short` позиция становится flat. Если
разрешённое направление позднее снова появляется, обычный entry из flat не
проходит через confirmation.

Re-arm trace после отмены:

```text
+1 position; trend: +1 -> -1 -> 0 -> -1 -> +1 -> -1
state:        ARMED  PENDING WAIT  WAIT  ARMED PENDING
```

`trend == 0` отменяет attempt, но не считается возвратом к исходному `P`.

### 6.4. Отмена

Pending отменяется без изменения позиции, если:

- target вернулся к `current_position`;
- `trend` стал `0`;
- наступил `time_filter_reset`;
- произошла смена календарного дня;
- текущий бар или требуемый execution bar находится вне разрешённого окна;
- execution bar отсутствует, в том числе на конце набора;

После отмены новый pending возможен только после нового перехода от target,
соответствующего текущей позиции, к противоположному target.

Непрерывное пребывание Supertrend в уже отменённом противоположном направлении
не запускает pending повторно без нового edge.

Для `long` и `short` target `0` сам по себе недостаточен для старта или
продолжения pending, поскольку тот же target получается при `trend == 0`.
Условие противоположного направления всегда проверяется по raw Supertrend:

```text
opposite_st_condition =
    current_position != 0
    and trend[t] == -current_position
```

`baseline_target` определяет итоговое действие (`-P` или `0`), а
`opposite_st_condition` определяет валидность подтверждения.

Если позиция была открыта на open текущего бара, а на close этого же бара
возник новый противоположный ST-edge, разрешено создать pending: на момент
решения позиция уже является открытой.

### 6.5. Завершение датасета

Завершённый batch backtest не оставляет активный pending:

- любой незавершённый pending на последнем доступном баре отменяется;
- `cancel_reason = dataset_end`;
- подтверждение без доступного execution bar запрещено;
- cancelled attempt не создаёт trade или фактический execution signal.

Streaming/incremental runtime и сохранение `active_at_end` не входят в v1.

### 6.6. Приоритет причин отмены и атомарность исполнения

На одном баре у attempt может быть только один `cancel_reason`. Если одновременно
истинны несколько условий, выбирается первая причина:

| Приоритет | Причина |
|---:|---|
| 1 | `time_filter_reset` |
| 2 | `daily_boundary` |
| 3 | `dataset_end` |
| 4 | `execution_outside_window` |
| 5 | `outside_window` |
| 6 | `trend_unready` |
| 7 | `trend_reverted` |

`reversal_confirmation_reset_count` увеличивается только для причин
`time_filter_reset` и `daily_boundary`, и только если до обработки reset
pending был активен.

Confirmation и scheduling являются одной атомарной операцией:

```text
if confirmation counter reaches N:
    first validate that t+1 exists and is execution-eligible
    if invalid:
        emit cancelled with exactly one cancel_reason
    else:
        write positions[t+1]
        emit confirmed[t]
        emit execution_scheduled[t]
        premark executed[t+1]
```

Состояние «confirmed, но execution ещё может быть отменён» запрещено. Поэтому
между confirmation и execution нет отдельного постоянного FSM-state, а
`confirmed_count = executed_count` является обязательным инвариантом.

## 7. Торговые окна и торговые циклы

### 7.1. Начало окна

На новом окне:

1. pending предыдущего окна отсутствует;
2. позиция находится в `flat` согласно существующему time reset;
3. по актуальному `baseline_target` формируется новый initiating entry;
4. initiating entry не требует `confirm_bars`;
5. исполнение допускается только внутри того же окна.

### 7.2. Выход из окна

На `time_filter_reset`:

1. reset имеет наивысший приоритет;
2. pending отменяется;
3. подтверждение на том же баре не выполняется;
4. причина отмены — `time_filter_reset`;
5. существующая логика закрытия позиции time-filter сохраняется;
6. reversal-фильтр не создаёт дополнительной позиции или повторного закрытия.

### 7.3. Разрыв между окнами

- pending не переносится между окнами одного дня;
- target, сформированный вне окна, не создаёт pending;
- открытие следующего окна может создать новый initiating entry из `flat` по
  актуальному target;
- этот вход не связывается с raw flip предыдущего окна или межоконного разрыва.

### 7.4. Граница дня

Pending никогда не переносится на следующий календарный день.

При включённом `time_filter` смена даты входит в идентичность торгового цикла.
При выключенном `time_filter` reversal runtime самостоятельно определяет смену
локальной календарной даты только для очистки pending. Такой pending-reset:

- не закрывает позицию;
- не создаёт новую позицию;
- не меняет общий daily-reset контракт других фильтров.

Источник локального времени:

- для timezone-aware `DatetimeIndex` используется timezone самого индекса;
- naive timestamps считаются уже представленными в локальном времени;
- окна являются полуинтервалами `[start, end)`;
- идентичность окна задаётся парой `(local_calendar_date, window_id)`.

DST обрабатывается правилами timezone индекса. Дополнительная конвертация
timezone внутри reversal runtime запрещена.

Reversal-only не реализует собственное сравнение дат. Он обязан использовать
существующий canonical daily-reset helper `_infer_daily_reset_event` либо его
тонкую публичную обёртку с тем же контрактом в `_reset_events.py`.

### 7.5. Нормативные traces границы окна

Пусть окно `[09:00, 09:03)`, бары имеют timestamps `09:00`, `09:01`,
`09:02`, `09:03`.

| Case | Решение на close | Execution bar | `time_only_legacy_boundary` | `combined_safe_execution_boundary` |
|---|---|---|---|---|
| W1 flat entry | `09:00` | `09:01` | открыть | открыть |
| W2 flat entry | `09:02` | `09:03` | сохранить текущий baseline | не открывать |
| W3 confirmation | `09:01` | `09:02` | неприменимо | исполнить |
| W4 confirmation | `09:02` | `09:03` | неприменимо | cancel `execution_outside_window` |
| W5 dataset end | последний доступный бар | отсутствует | сохранить текущий baseline | cancel `dataset_end` |

Если между `09:02` и следующим баром есть временной разрыв, eligibility
определяется timestamp фактического `t+1`, а не ожидаемой частотой ряда.

## 8. Порядок обработки бара

Нормативный порядок для бара `t`:

1. Считать `positions[t]` и сохранить bar-start snapshot.
2. Определить `time_filter_in_window[t]`, `time_filter_reset[t]` и смену даты.
3. Если есть reset:
   - отменить pending;
   - применить существующее time-filter действие к позиции;
   - не обрабатывать подтверждение и новый pending на этом баре.
4. Рассчитать raw `trend[t]` и `baseline_target[t]`.
5. Если позиция `flat`, обработать только initiating entry.
6. Если позиция открыта и pending отсутствует:
   - обнаружить новый противоположный edge;
   - проверить разрешённое окно;
   - создать pending.
7. Если pending активен:
   - не выполнять этот шаг, если pending создан на текущем баре;
   - отменить его при возврате target или `trend == 0`;
   - иначе увеличить число подтверждённых баров, не считая event bar;
   - при достижении `confirm_bars` проверить наличие и допустимость
     execution bar.
8. Записать ровно одно итоговое значение `positions[t + 1]`, если индекс
   существует.
9. Записать diagnostics после принятия решения, но с явно разделёнными
   bar-start state и event flags.

Reset всегда приоритетнее confirmation. Confirmation приоритетнее создания
нового pending на том же баре.

При активном `reversal_confirmation + time_filter` до записи
`positions[t+1]` проверяется, что `t+1` существует, входит в то же разрешённое
окно и принадлежит тому же календарному циклу. Проверка использует только
известный timestamp и расписание, но не OHLC или Supertrend будущего бара.

## 9. Runtime-состояние

Минимальное постоянное состояние:

```text
pending_active: bool
pending_target: int8       # -1, 0, +1
pending_source_position: int8
pending_start_bar: int64
pending_bars_confirmed: int64
rearm_state: ARMED | PENDING | WAIT_SOURCE_RETURN
```

Переходы re-arm:

```text
ARMED + новый opposite edge       -> PENDING
PENDING + отмена                  -> WAIT_SOURCE_RETURN
WAIT_SOURCE_RETURN + возврат к P  -> ARMED
PENDING + подтверждение           -> ARMED для новой действующей позиции
```

В `WAIT_SOURCE_RETURN` непрерывное противоположное направление не запускает
новый pending. Возврат через `trend == 0` сам по себе не вооружает runtime:
требуется валидный target, равный исходной позиции `P`.

На `time_filter_reset` состояние очищается вместе с торговым циклом. При
выключенном time-filter календарный pending-reset отменяет pending, но re-arm
остаётся в `WAIT_SOURCE_RETURN`, пока ST не вернётся к исходной позиции.

Состояние хранится локально в baseline position runtime. Новая общая FSM и
встраивание в ZigZag FSM не требуются.

## 10. Warmup и невалидное направление

Текущий tester использует warmup для метрик, а не как границу торгового runtime.
Новый фильтр не должен вводить отдельную торговую семантику warmup.

Правила:

- `trend == 0` не запускает pending или entry;
- `trend == 0` во время pending отменяет его с причиной `trend_unready`;
- переход `0 → ±1` может сформировать initiating entry из `flat`, но не
  reversal pending;
- отдельного reset на границе metric warmup нет.

## 11. Execution model, комиссия и последний бар

V1 поддерживает только фактически поддерживаемую tester модель
`open_to_open`.

- V1 поддерживает `reversal_confirmation` только при отключённом механизме
  early exit.
- Если `reversal_confirmation.enabled: true` и early exit включён через
  конфигурацию или параметры caller-а, комбинация отклоняется до начала
  backtest.
- Проверка выполняется как на уровне tester/caller validation, так и
  fail-closed guard-ом в общем backtest entry point, чтобы прямой вызов
  `run_single_backtest` не мог обойти ограничение.
- Поддержка early exit потребует отдельного terminal-reconciliation контракта
  для переклассификации подтверждённого, но удалённого усечением исполнения и
  не входит в v1.
- решение на close `t` исполняется на open `t+1`;
- запрещено читать OHLC или Supertrend будущего бара;
- календарная принадлежность `t+1` разрешённому окну может проверяться заранее,
  так как расписание является конфигурацией, а timestamp известен;
- комиссия рассчитывается существующим `abs(diff(positions))` механизмом;
- reversal `+1 → -1` или `-1 → +1` сохраняет существующую стоимость двух
  операций;
- close `±1 → 0` сохраняет стоимость одной операции;
- новый фильтр меняет только индекс изменения позиции, не формулу комиссии;
- на последнем баре не создаётся фиктивная позиция или pending trade без
  доступного execution bar.

## 12. Diagnostics

При включённом сборе diagnostics добавить per-bar поля:

| Поле | Тип | Default | Значение |
|---|---|---:|---|
| `reversal_confirmation_enabled` | `int8` | `0` | Capability включена |
| `reversal_confirmation_pending` | `int8` | `0` | Pending после обработки бара |
| `reversal_confirmation_target` | `int8` | `0` | Цель `-1/0/+1` |
| `reversal_confirmation_source_position` | `int8` | `0` | Исходная позиция |
| `reversal_confirmation_bars_left` | `int64` | `0` | Осталось после обработки бара |
| `reversal_confirmation_started` | `int8` | `0` | Event на текущем баре |
| `reversal_confirmation_cancelled` | `int8` | `0` | Event на текущем баре |
| `reversal_confirmation_confirmed` | `int8` | `0` | Event на текущем баре |
| `reversal_confirmation_execution_scheduled` | `int8` | `0` | На confirmation bar запланировано изменение в `positions[t+1]` |
| `reversal_confirmation_executed` | `int8` | `0` | На текущем execution bar начала действовать новая позиция |
| `reversal_confirmation_event_id` | `int64` | `-1` | Идентификатор attempt/execution |
| `reversal_confirmation_cancel_reason` | `object/string` | `"none"` | Причина отмены |

Допустимые причины отмены:

```text
none
trend_reverted
trend_unready
time_filter_reset
daily_boundary
outside_window
execution_outside_window
dataset_end
```

На event bar старта:

- `started = 1`;
- `pending = 1`;
- `bars_left = confirm_bars`.

На обычном подтверждающем баре:

- `pending = 1`;
- `bars_left` уменьшается после зачёта текущего бара.

На confirmation bar:

- `confirmed = 1`;
- `pending = 0`;
- `bars_left = 0`;
- `execution_scheduled = 1`, только если `positions[t+1]` действительно записана.

На execution bar:

- `executed = 1`;
- `event_id` совпадает с подтверждённой попыткой;
- переход `positions` и фактические Signals/Trades используют этот же индекс.

На cancel/reset bar:

- `cancelled = 1`;
- `pending = 0`;
- `bars_left = 0`;
- задана ненулевая `cancel_reason`.

Тяжёлые per-bar массивы создаются только при
`collect_filter_diagnostics=true`. Для trade exit attribution при выключенной
диагностике сохраняется только минимальная execution metadata.

### 12.1. Summary

Добавить счётчики:

```text
reversal_confirmation_started_count
reversal_confirmation_cancelled_count
reversal_confirmation_confirmed_count
reversal_confirmation_executed_count
reversal_confirmation_reset_count
```

Инвариант завершённого прогона:

```text
started_count
= cancelled_count + confirmed_count

confirmed_count = executed_count
```

`reset_count` считает только reset-события, которые отменили активный pending.
Общее количество календарных и time-filter reset bars в этот счётчик не входит.

## 13. Signals, trades и Excel

### 13.1. Signals и события подтверждения

При выключенной или отсутствующей capability существующий лист Signals, его
колонки, значения и порядок строк остаются полностью неизменными.

При активной capability экспорт разделяется на три явные таблицы:

| Таблица | Одна строка | Семантика |
|---|---|---|
| `Signals` | фактический open/close execution | строится из переходов `positions` |
| `RawSignals` | raw ST-flip | аналитика, не утверждает факт исполнения |
| `ReversalConfirmationEvents` | одна попытка подтверждения | lifecycle attempt от start до final status |

Минимальная схема фактического `Signals`:

```text
event_id                 # nullable для not_required entry
event_type               # open / close
side                     # LONG / SHORT
execution_bar
execution_time
execution_price
source_signal_bar
source_signal_time
confirmation_status      # not_required / confirmed
```

`RawSignals` сохраняет текущие аналитические поля raw flip. Поля, ранее
называвшиеся как фактическое исполнение, в active-capability export должны
иметь префикс `raw_reference_` (`raw_reference_bar`,
`raw_reference_time`, `raw_reference_price`) и не могут называться
`execution_*` или `exec_*`. Silent overwrite их смысла запрещён.

Для новой capability вводится
`ReversalConfirmationEvents` — одна строка на одну попытку подтверждения.

Для каждой попытки хранить:

```text
event_id
source_signal_bar
source_signal_time
confirmation_bar
confirmation_time
execution_bar
execution_time
confirmation_status    # cancelled/confirmed для завершённого batch backtest
confirmation_cancel_reason
```

`confirmation_bar/time` и `execution_bar/time` nullable: у отменённой попытки
они отсутствуют, если соответствующее событие не наступило.

`event_id` — монотонный `int64`, начинающийся с `0` отдельно в каждом
самостоятельном backtest/legacy period split. Он назначается на start attempt,
не переиспользуется после cancel и переносится в confirmation, execution,
Signals и Trades.

Правила:

- raw flip на `t0` создаёт строку attempt без фиктивного `exec_price`;
- строка attempt после завершения имеет финальный status
  `cancelled` или `confirmed`;
- отменённый attempt не создаёт фактические open/close rows;
- подтверждённый candidate получает execution index `t0 + N + 1`;
- фактические execution rows строятся из переходов массива `positions` и
  содержат ссылку на `event_id`;
- для `revers/both` подтверждённый reversal создаёт close старой стороны и
  open новой стороны на одном execution index;
- для `long/short` подтверждение создаёт только close row;
- initiating entry из `flat` в фактических execution signals помечается как
  `confirmation_status = not_required`;
- существующий raw Signals может сохранить ST-аналитику, но при активной
  capability не должен указывать raw `t+1` как фактический execution index.

Raw signal, confirmation attempt и execution являются разными сущностями.
Смешивать их в одной строке без `event_id` запрещено.

### 13.2. Trades

- `entry_index` и `exit_index` определяются только изменениями `positions`;
- exit reason для подтверждённого close:
  `reversal_confirmation_confirmed`;
- приоритет exit reason:
  `time/daily forced reset` → `reversal_confirmation_confirmed` →
  обычный baseline ST exit → terminal open trade;
- отменённый pending не создаёт trade;
- initiating entry сохраняет обычный entry reason time-filter/baseline.

К существующей Trades schema при активной capability аддитивно добавляются:

```text
entry_reversal_confirmation_event_id   # nullable
exit_reversal_confirmation_event_id    # nullable
exit_reason
```

Имена и смысл остальных trade-колонок не меняются.

При `collect_filter_diagnostics=false` минимальная execution metadata всё равно
хранит `event_id`, execution event и exit reason. Тяжёлые per-bar diagnostics
для этого не требуются.

Интеграция с существующим `filter_trade_diagnostics` использует единую таблицу:

| Событие на exit decision bar | Итоговый `exit_reason` |
|---|---|
| `time_filter_reset` | существующий `filter_time_reset` |
| `daily_boundary` с forced exit | существующий `filter_daily_reset` |
| подтверждённый reversal close | `reversal_confirmation_confirmed` |
| обычный baseline ST close | существующий `st_flip` |
| открытая сделка на конце | существующий terminal reason |

На conflict bar выбирается первая применимая строка. Добавление reversal reason
не меняет существующие reason для reset, ZigZag, volume или wakeup в runs, где
новая capability выключена.

### 13.3. Excel

Добавить человекочитаемые заголовки для всех полей diagnostics и summary.
Новые листы/колонки включаются только при активной capability и
соответствующем export flag. При выключенном фильтре структура существующих
листов должна оставаться идентичной baseline.

## 14. Поддерживаемые pipelines v1

Поддерживается:

- tester и batch tester;
- прямой `single` вызов общего backtest entry point;
- `segmentation.mode: legacy`;
- `open_to_open`;
- все четыре `trade_mode`;
- time-filter enabled или disabled;
- early exit disabled.

Не поддерживается и отклоняется до запуска backtest:

- `segmentation.mode: equal_blocks`;
- `wf_grid` optimization pipeline;
- ZigZag/volume/wakeup композиции;
- включённый early exit;
- иные execution models.

Каждый legacy period split (`100%`, `75%`, `50%`, `33%`, `25%`) является
самостоятельным backtest и начинает работу с чистым reversal runtime. Состояние
из полного периода или предыдущего split не переносится.

Общий `run_single_backtest` используется несколькими pipelines, поэтому новый
runtime активируется только при явно включённой capability. Caller-pipeline
validation не должна позволять случайно применить v1 в `wf_grid`.

Разрешённые caller identities при активной capability: `tester` и `single`.
`wf_grid`, `optimizer`, пустой и неизвестный caller отклоняются fail-closed.
Для `single` также обязательно `early_exit_enabled=false`.

`caller_mode="single"` — публичный контракт прямого вызова
`run_single_backtest`: вызывающая сторона обязана передать его явно вместе с
валидированным `TradeFilterConfig`. Значение по умолчанию `caller_mode=""`
разрешено только при выключенной capability; при активной capability это
ошибка `reversal_confirmation_unknown_caller`.

### 14.1. Двухуровневые fail-closed gates

| Ограничение | Caller/config validation | `run_single_backtest` guard |
|---|---|---|
| `equal_blocks` | tester loader отклоняет | equal-blocks runner не передаёт capability; прямой обход отклоняется по caller |
| `wf_grid` / `optimizer` | shared validator отклоняет | engine отклоняет caller identity |
| early exit | tester/single validation отклоняет | engine проверяет `early_exit_enabled` |
| ZigZag/volume/wakeup | shared config validator отклоняет | runtime проверяет отсутствие конфликтующих active capability |
| неизвестный/пустой caller | validator отклоняет | engine отклоняет |

Оба уровня используют стабильные error keys из раздела 3.4. Ни CLI, ни hand-built
dataclass, ни прямой вызов engine не должны обходить ограничения.

### 14.2. Производительность

V1 допускает один Python bar-loop уровня `time_only_filter.apply`:

- временная сложность `O(n)`;
- запрещены вложенный поиск вперёд и пересчёт истории на каждом баре;
- при выключенных diagnostics не создаются тяжёлые object/string per-bar arrays;
- обязательные positions и минимальная execution metadata остаются `O(n)`;
- benchmark на серии не менее 1 000 000 баров выполняется пять раз;
- медианное время `reversal_only`, diagnostics off, не должно превышать
  медианное время `time_only` более чем на 30% на одной машине и одном процессе.

Benchmark не является parity proof и запускается отдельно от correctness tests.

## 15. Обязательные тесты

### 15.1. Конфиг

1. Блок отсутствует — полная parity.
2. `enabled: false` — полная parity.
3. `enabled: true`, `confirm_bars: 1` — valid.
4. `confirm_bars: 0`, отрицательное, больше `1_000_000`, float, string, bool,
   null — reject.
5. Неизвестный ключ — reject.
6. `confirm_bars` при `enabled: false` — reject.
7. Все `trade_mode` принимаются.
8. `both` и `revers` дают одинаковую position semantics.
9. Конфликты с ZigZag, volume, wakeup и position freeze — reject.
10. `wf_grid` и equal-blocks — reject.
10a. `reversal_only` и `reversal_and_time` разрешаются и попадают в
     public/effective snapshot и fingerprint.
10b. Активная capability с RangeIndex, немонотонным DatetimeIndex или
     несовпадающей длиной index — reject.
10c. `enabled: false` вместе с `confirm_bars` возвращает стабильный error key
     `reversal_confirmation_disabled_has_confirm_bars`.

### 15.2. Bar-by-bar runtime

11. `revers`, `N=1`: reversal исполняется на `t0+2`.
12. `revers`, `N=3`: reversal исполняется на `t0+4`.
13. Симметричные long-to-short и short-to-long reversal.
14. `long`: подтверждённый противоположный ST закрывает `+1 → 0`.
15. `short`: подтверждённый противоположный ST закрывает `-1 → 0`.
16. Возврат target на первом, среднем и последнем confirmation bar отменяет
    pending.
17. После отмены непрерывный противоположный target не перезапускает pending.
18. Новый edge после отмены запускает новый pending.
19. После подтверждённого изменения следующий противоположный edge работает
    симметрично.
20. `trend == 0` отменяет pending.
20a. `trend == 0` при открытой позиции не запускает pending.
21. Flat initiating entry не задерживается.
22. Повторный вход из flat после подтверждённого close не задерживается.
22a. Позиция, открытая на open текущего бара, может создать pending по новому
     противоположному edge на close этого же бара.
22b. Event bar не увеличивает confirmation counter.
22c. Нормативные traces L1–L4 и S1–S4 выполняются bar-by-bar.
22d. При совпадении нескольких причин cancel выбирается ровно одна причина по
     таблице раздела 6.6.

### 15.3. Time-filter

23. Pending стартует только внутри окна.
24. Flip вне окна не создаёт pending.
25. Новое окно создаёт initiating entry по актуальному target.
26. Pending отменяется на выходе из окна.
27. Pending не переносится между двумя окнами одного дня.
28. Pending не переносится на следующий день.
29. Confirmation и time reset на одном баре: reset побеждает.
30. Execution bar вне окна: изменение позиции не выполняется.
31. Однобарное/слишком короткое окно не создаёт позицию вне окна.
32. При выключенном time-filter подтверждение работает без окон.
33. При выключенном time-filter pending очищается на календарной границе, но
    открытая позиция не закрывается этим pending-reset.
33a. Fixtures W1–W5 подтверждают различие legacy и combined boundary policies.

### 15.4. Execution и exports

34. `open_to_open` без look-ahead и off-by-one.
35. Последний бар: нет фиктивного исполнения или pending trade.
35a. Незавершённый pending на конце получает `cancelled`,
     `cancel_reason=dataset_end`.
36. `reversal_confirmation.enabled: true` вместе с включённым early exit
    отклоняется до начала backtest, включая прямой вызов
    `run_single_backtest`.
37. Комиссия reversal остаётся двойной, close — одинарной.
38. Positions, execution Signals, trades и diagnostics
    `reversal_confirmation_executed` имеют одинаковый execution index;
    confirmation diagnostics остаются на предыдущем decision bar.
39. Cancelled pending отсутствует в trades.
40. Diagnostics start/cancel/confirm точно соответствуют событиям.
41. Summary-инварианты выполняются.
42. При отключённом diagnostics торговый результат идентичен.
43. При выключенной capability Excel и результаты идентичны baseline.
43a. При выключенной capability существующая самостоятельная семантика
     `time_filter` остаётся идентичной baseline на границах окон.
44. `reset_count` увеличивается только когда reset отменяет активный pending.
45. При `collect_filter_diagnostics=false` exit reason и event linkage
    сохраняются через минимальную execution metadata.
46. Каждый legacy period split стартует с чистым reversal runtime.
47. `wf_grid`, `optimizer`, пустой и неизвестный caller отклоняются.
48. Timezone-aware, naive и DST fixtures соблюдают контракт локальной даты.
49. `event_id` монотонен, не переиспользуется и начинается с `0` отдельно в
    каждом period split.
50. При активной capability Signals, RawSignals,
    ReversalConfirmationEvents и Trades соответствуют схеме раздела 13.
51. Conflict exit bars соблюдают таблицу приоритетов `filter_trade_diagnostics`.
52. Direct call с `caller_mode="single"` работает, а с `caller_mode=""`
    отклоняется.
53. Benchmark выполняет требования раздела 14.2.

Тесты runtime должны использовать контролируемый массив `trend`, а
интеграционные тесты — короткий детерминированный OHLC fixture. Нельзя полагаться
на случайное возникновение требуемого ST-паттерна.

## 16. Требования к implementation plan

План реализации должен:

1. Проследить путь YAML от tester loader до `run_single_backtest`.
2. Указать точные изменения config dataclass, builder, whitelist, validator и
   caller-pipeline gate.
3. Показать текущую точку формирования baseline target и `positions[t+1]`.
4. Предложить отдельный локальный reversal position runtime рядом с
   `time_only_filter.py`, без новой общей FSM и без изменения control-flow
   standalone `time_only`.
5. До дедупликации поставить parity-harness на `generate_positions` и
   `time_only_filter.apply`; разрешить общий только pure target-mapping helper.
6. Зафиксировать порядок reset, pending, confirmation и записи позиции.
7. Указать изменения Signals, trades, diagnostics, summary и Excel.
8. Перечислить изменяемые production- и test-файлы со ссылками на строки.
9. Разбить реализацию на небольшие этапы, каждый с наблюдаемой проверкой.
10. Привести команды точечных и полных тестов.
11. Зафиксировать baseline fingerprint/parity proof до начала изменений.
12. Явно показать обновление public/effective config snapshot и fingerprint.
13. Сохранить текущую самостоятельную семантику `time_filter`; её изменение не
    включать в реализацию этой capability.
14. Зафиксировать canonical modes, двухуровневые gates, стабильные error keys и
    обязательность `DatetimeIndex`.
15. Привести schema diff для Signals, RawSignals,
    ReversalConfirmationEvents и Trades.
16. Создать до первого production-изменения baseline-артефакт
    `docs/baselines/st_reversal_confirmation_prechange.json`. Артефакт должен
    содержать hashes positions/returns/trades, effective config snapshot,
    Excel sheet/column schema и команду воспроизведения.
17. Добавить отдельную benchmark-команду для требований раздела 14.2.

План не должен встраивать независимую функцию в ZigZag FSM или предлагать
универсальную систему фильтров без необходимости.

## 17. Критерии приёмки

Реализация готова, когда:

- конфиг строго валидируется по разделу 3;
- effective modes равны `reversal_only` или `reversal_and_time`;
- активная capability требует валидный monotonic `DatetimeIndex`;
- поддерживаются `revers`, `both`, `long`, `short`;
- initiating entry из `flat` не задерживается;
- изменение уже открытой позиции проходит через подтверждение;
- отменённый pending не меняет позицию и не создаёт сделку;
- pending не протекает между окнами и днями;
- execution никогда не создаётся на отсутствующем или запрещённом баре;
- confirmation и запись `positions[t+1]` атомарны;
- сочетание с включённым early exit отклоняется до начала backtest;
- reset побеждает confirmation;
- нет look-ahead и off-by-one;
- комиссия использует существующую модель;
- positions, Signals, trades и diagnostics согласованы;
- standalone `time_only_legacy_boundary` сохраняет baseline, а combined runtime
  использует только `combined_safe_execution_boundary`;
- `ReversalConfirmationEvents` содержит одну итоговую строку на attempt и
  связывается с execution через `event_id`;
- завершённый batch backtest не оставляет активный pending;
- отключённая capability даёт идентичный baseline, включая Excel;
- diagnostics не влияют на торговый результат и не выделяются без нужды;
- caller/config и engine gates дают одинаковый fail-closed результат;
- выполняется performance budget раздела 14.2;
- проходят новые тесты и весь релевантный существующий suite;
- документация содержит минимальные примеры для всех `trade_mode`.
