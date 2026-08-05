# Финальный план реализации `time_filter`: несколько окон и независимый time-only

Статус: контракт утверждён для поэтапной реализации после фиксации чистого
baseline.

Документ является самостоятельной технической спецификацией. Он определяет
целевое поведение, намеренные изменения совместимости, архитектуру, публичные и
внутренние API, rollout, тесты, порядок поставки и критерии приёмки.

---

## 1. Цель

Реализовать:

1. Несколько разрешённых внутридневных интервалов через
   `trade_filter.time_filter.windows`.
2. Независимый режим `time_only`, работающий без ZigZag и Volume.
3. Единый механизм временных membership/reset-событий для time-only,
   standalone-volume и ZigZag/FSM.
4. Детерминированное поведение на временных пропусках и при смене календарной
   даты.

Функциональность должна работать в:

- прямом backtest API;
- WF-оптимизации через `config.yaml`;
- single tester;
- multi-config tester batch;
- canonical batch tester в поддерживаемом legacy-period режиме;
- ZigZag-only + Time;
- Volume-only + Time;
- ZigZag + Volume + Time;
- time-only.

---

## 2. Scope

### 2.1 В scope

- activation helpers и effective mode;
- `window` + `windows`;
- строгая конфигурационная валидация;
- canonical schedule;
- cycle/date-aware reset;
- precomputed Time runtime;
- независимый time-only;
- миграция существующих Time gates в ZigZag и standalone-volume;
- capability-aware diagnostics, summary и export;
- WF/tester/batch wiring;
- snapshot/fingerprint;
- characterization, dual-run, regression и performance tests;
- обновление рабочих конфигов после зелёных тестов.

### 2.2 Вне scope

- cross-midnight windows;
- scheduled/pre-open execution;
- синтетические бары или цены исполнения;
- устранение ценовой экспозиции на промежутке без баров;
- поддержка active trade filter в `equal_blocks`;
- унификация `trade_filter.volume.baseline_session.window`;
- смена open-to-open execution model;
- изменение алгоритмов SuperTrend, ATR, ZigZag или Volume metrics;
- общий рефакторинг всего ZigZag FSM;
- новый пользовательский YAML-переключатель reset semantics.

`volume.baseline_session.window` сохраняет существующий parser и runtime. В этом
изменении он не получает `windows`, cycle reset или общий Time schedule.

---

## 3. Неизменяемые инварианты данных

`time_filter` не должен:

- обрезать DataFrame;
- удалять, сортировать или синтезировать бары;
- изменять index/timestamps;
- изменять WF train/test/OOS boundaries;
- сокращать warmup/prepend;
- исключать outside bars из SuperTrend;
- исключать outside bars из ATR;
- исключать outside bars из baseline raw target/positions;
- исключать outside bars из returns timeline или metrics;
- требовать `volume` в time-only;
- строить ZigZag global stats в time-only;
- строить VolumeRuntime в time-only.

Независимый `early_exit` может сокращать результирующие массивы по своему
существующему контракту. Это не является поведением Time.

---

## 4. Execution model

Поддерживается только:

```text
решение на close(t)
исполнение на open(t+1)
```

Смысл массивов:

```text
positions[t] — позиция, действующая во время бара t
positions[t+1] — результат решения на close(t)
positions[0] = 0
```

Следствия:

- вход, разрешённый на баре `t`, появляется в `positions[t+1]`;
- reset на баре `t` не переписывает `positions[t]`;
- reset задаёт `positions[t+1] = 0`, если следующий бар существует;
- reset-бар может находиться outside и содержать старую позицию;
- «закрытие на `12:00`» означает сигнал на close `12:00`, а не исполнение на
  open `12:00`;
- простой `raw_positions * in_window` запрещён;
- готовый lagged `raw_positions[t]` нельзя использовать как target решения на
  том же `t`.

Для решения на close `t` baseline target определяется из текущего trend:

```text
raw_target[t] = apply_trade_mode(trend[t], trade_mode)
```

Для `t+1 < n`:

```text
generate_positions(trend, trade_mode)[t+1] == raw_target[t]
```

---

## 5. Утверждённая reset-семантика

Временное окно задаёт торговые циклы. Цикл определяется:

```text
cycle_id = (локальная календарная дата индекса, индекс разрешённого окна)
```

При переходе с бара разрешённого цикла на бар другого цикла или outside
возникает reset.

Это правило применяется одинаково к:

- legacy `window`;
- новому `windows`;
- time-only;
- Volume + Time;
- ZigZag + Time;
- ZigZag + Volume + Time.

### 5.1 Намеренное изменение совместимости

Для существующего single-window Time-on изменяется поведение ряда:

```text
17:59 day 1 → 09:00 day 2
```

Оба бара могут находиться внутри одного HH:MM-окна, но принадлежат разным
календарным циклам. На `09:00 day 2` теперь возникает reset.

Это является принятой breaking migration для active Time. Она намеренно меняет:

- positions;
- returns;
- trades;
- exit reasons;
- reset counters;
- ZigZag/Volume lifecycle;
- Time-on golden snapshots.

Это изменение не маскируется требованием bit-identical Time-on parity.

### 5.2 Gap-detected close reset

При:

```text
17:59 day 1 → 09:00 day 2
```

на `09:00`:

```text
in_window = True
reset_event = True
```

При этом:

- старая позиция может присутствовать в `positions[09:00]`;
- gap return относится к старой позиции;
- новый вход на `09:00` запрещён;
- на close `09:00` формируется flat decision;
- следующий существующий бар получает position `0`;
- новый вход возможен со следующего decision bar.

Reset не устраняет gap exposure. Он запрещает перенос lifecycle/state после
обнаружения нового цикла и закрывает позицию на следующем доступном
open-to-open исполнении.

---

## 6. Oracle-таблица membership/reset

Эта таблица является единственным поведенческим oracle для общего helper,
runtime и тестов.

Обозначения:

- `I` — inside;
- `O` — outside;
- `A/B` — разные windows;
- `D1/D2` — разные даты;
- `R` — reset на текущем баре.

| Предыдущий бар | Текущий бар | Membership current | Reset current |
|---|---|---:|---:|
| отсутствует | первый бар outside | O | 0 |
| отсутствует | первый бар inside A/D1 | I | 0 |
| inside A/D1 | inside A/D1 | I | 0 |
| inside A/D1 | outside D1 | O | 1 |
| outside D1 | outside D1 | O | 0 |
| outside D1 | inside B/D1 | I | 0 |
| inside A/D1 | inside B/D1 после gap | I | 1 |
| inside A/D1 | outside после gap | O | 1 |
| inside A/D1 | inside A/D2 | I | 1 |
| inside A/D1 | inside B/D2 | I | 1 |
| outside D1 | inside A/D2 | I | 0 |
| outside D1 | outside D2 | O | 0 |

Дополнительные правила:

- переход `outside → inside` сам по себе не создаёт reset, даже при смене даты:
  reset уже должен был материализоваться на первом observed выходе из
  предыдущего разрешённого цикла; если ряд начинается или продолжается outside,
  наследуемого lifecycle нет;
- `reset_event[0] = False` всегда;
- reset определяется только между существующими соседними строками;
- повторный outside bar не создаёт повторный reset;
- reset может совпадать с `in_window=True`;
- reset имеет приоритет над entry/reversal;
- reset на последнем баре является сигналом без исполнения.

Oracle реализуется сначала как table-driven characterization tests, затем как
production helper.

---

## 7. Activation contract

```text
zigzag_active =
    trade_filter.enabled is True
    AND zigzag.enabled is True

volume_active =
    trade_filter.enabled is True
    AND volume.enabled is True

time_filter_active =
    trade_filter.enabled is True
    AND time_filter.enabled is True

trade_filter_active =
    zigzag_active
    OR volume_active
    OR time_filter_active
```

Нужны helpers:

```python
is_trade_filter_enabled(tf)
is_zigzag_enabled(tf)
is_volume_enabled(tf)
is_time_filter_enabled(tf)
is_any_trade_subfilter_enabled(tf)
resolve_trade_filter_mode(tf)
```

Правило проверки:

```python
value is True
```

Запрещено использовать `bool(value)` для YAML-derived enable fields.

Все production enable-checks должны пройти отдельный аудит. Тесты обязаны
проверять:

```yaml
enabled: 1
enabled: "true"
enabled: "yes"
enabled: null
```

как невалидные значения, а не truthy activation.

---

## 8. Effective modes

| ZigZag | Volume | Time | Mode |
|---:|---:|---:|---|
| 0 | 0 | 0 | ошибка при root=true |
| 0 | 0 | 1 | `time_only` |
| 0 | 1 | 0 | `volume_only` |
| 0 | 1 | 1 | `volume_and_time` |
| 1 | 0 | 0 | `zigzag_only` |
| 1 | 0 | 1 | `zigzag_and_time` |
| 1 | 1 | 0 | `zigzag_and_volume` |
| 1 | 1 | 1 | `zigzag_volume_and_time` |

При `trade_filter.enabled=false` mode всегда:

```text
disabled
```

При root=true и трёх disabled subfilters:

```text
at least one trade subfilter must be enabled when trade_filter.enabled=true:
zigzag, volume, or time_filter
```

Error key:

```text
trade_filter_all_subfilters_disabled
```

Validator ловит ошибку при загрузке. Backtest сохраняет defensive fail-fast для
hand-built configs, обходящих loader.

---

## 9. Runtime dispatch

Единственный dispatch:

```text
if zigzag_active:
    ZigZag/FSM owner
elif volume_active:
    standalone-volume owner
elif time_filter_active:
    time-only owner
else:
    baseline
```

Приоритет определяет владельца lifecycle:

- ZigZag владеет FSM/lifecycle/candidate state;
- standalone-volume владеет Volume-cycle state без ZigZag;
- time-only использует тонкий baseline decision adapter;
- Time является gate/reset capability, а не отдельным FSM;
- Volume является дополнительным gate внутри ZigZag path.

Consumers используют effective mode, а не собственные комбинации OR.

---

## 10. Конфигурация

### 10.1 Новый формат

```yaml
trade_filter:
  time_filter:
    enabled: true
    windows:
      - "09:00-12:00"
      - "16:00-18:00"
```

### 10.2 Legacy-формат

```yaml
trade_filter:
  time_filter:
    enabled: true
    window: "09:00-19:00"
```

### 10.3 Mutual exclusion

`window` и `windows` запрещено указывать одновременно.

Конфликт определяется присутствием YAML-ключей, а не non-null значением.

Следовательно, конфликтом являются:

```yaml
window: null
windows: null
```

и любые другие конфигурации, содержащие оба ключа.

Проверка действует независимо от root/time enabled.

Error key:

```text
time_filter_window_and_windows_conflict
```

Это намеренный строгий продуктовый контракт, а не временная MVP-эвристика.

### 10.4 Active requirement

При active Time должен быть указан ровно один ключ.

Если оба отсутствуют:

```text
time_filter_windows_missing
```

Если legacy `window` присутствует со значением `null`:

```text
time_filter_window_missing
```

Если `windows` присутствует со значением `null`:

```text
time_filter_windows_invalid_type
```

---

## 11. Disabled validation contract

При `trade_filter.enabled=false` runtime является полным no-op. Это не отменяет
структурную проверку конфигурации.

Всегда проверяются:

- mapping shape;
- unknown keys;
- тип явно заданных `enabled`;
- conflict `window + windows`;
- тип root/subfilter switches.

При `time_filter.enabled=false` не выполняется глубокая проверка `window` или
`windows`:

- формат;
- диапазоны;
- list emptiness;
- item types;
- overlap/touching.

Таким образом, inactive templates могут хранить незавершённые значения.

Проверка неправильного типа `time_filter.enabled` при выключенном root является
намеренной config-validation migration. Она не меняет baseline runtime outputs.

---

## 12. Inactive template matrix

При active root:

| Конфигурация | Допустимость | Эффект |
|---|---|---|
| `zigzag.enabled=false` + ZigZag params | допустимо | inert template |
| `volume.enabled=false` + Volume params | допустимо | inert template |
| `wakeup_regime.enabled=false` + payload | допустимо | inert template |
| `wakeup_regime.enabled=true`, ZigZag off | ошибка | dependency violation |
| Mode D params, ZigZag off | допустимо | inert template |
| triggers/lifecycle, ZigZag off | допустимо | inert template |
| diagnostics block, ZigZag off | допустимо | только применимые export flags |
| `type: zigzag_st_mode`, explicit ZigZag off | ошибка | contradictory marker |
| `zigzag.enabled` отсутствует + legacy marker | existing compatibility resolution |

Для production time-only рекомендуется явно указывать:

```yaml
zigzag:
  enabled: false

volume:
  enabled: false
```

Inactive template payload:

- проходит structural validation;
- не проходит deep active validation;
- не влияет на runtime;
- не влияет на effective fingerprint;
- может присутствовать в raw config snapshot.

Nested wakeup components не активируются, если
`wakeup_regime.enabled is not True`.

---

## 13. Interval semantics

Все интервалы полуоткрытые:

```text
[start, end)
```

Пример:

```text
[09:00, 12:00)
[16:00, 18:00)
```

Membership:

- `09:00–11:59` inside;
- `12:00–15:59` outside;
- `16:00–17:59` inside;
- `18:00+` outside.

Секунды и наносекунды игнорируются при membership.

Time components берутся из index as-is:

- без UTC conversion;
- без timezone conversion;
- без DST correction;
- tz-aware wall clock используется в текущей timezone индекса.

---

## 14. Validation `windows`

При active Time:

- `windows` должен быть YAML list;
- list должен быть непустым;
- каждый item должен быть `str`;
- формат строго `HH:MM-HH:MM`;
- hours `00–23`;
- minutes `00–59`;
- start < end;
- zero-length запрещён;
- cross-midnight запрещён.

После parse интервалы сортируются по:

```text
(start_minutes, end_minutes)
```

Запрещаются:

- duplicates;
- partial overlaps;
- full nesting;
- touching.

Для соседних intervals:

```text
previous.end < next.start
```

Touching hard-reject является утверждённым контрактом. Разные элементы списка
обозначают разные lifecycle-циклы. Если нужен непрерывный интервал:

```yaml
windows:
  - "09:00-18:00"
```

Пользовательские YAML-индексы сохраняются при сортировке, чтобы сообщения
указывали исходные paths:

```text
trade_filter.time_filter.windows[1] overlaps windows[0]
```

Сложность:

```text
O(k log k)
```

---

## 15. Validation error keys

Сохраняются:

```text
time_filter_enabled_invalid_type
time_filter_window_missing
time_filter_window_invalid_format
time_filter_window_invalid_hours
time_filter_window_invalid_minutes
time_filter_window_zero_length
time_filter_window_cross_midnight
```

Добавляются:

```text
time_filter_window_and_windows_conflict
time_filter_windows_missing
time_filter_windows_invalid_type
time_filter_windows_empty
time_filter_windows_item_invalid_type
time_filter_windows_item_invalid_format
time_filter_windows_item_invalid_hours
time_filter_windows_item_invalid_minutes
time_filter_windows_item_zero_length
time_filter_windows_item_cross_midnight
time_filter_windows_duplicate
time_filter_windows_overlap
time_filter_windows_touching
trade_filter_all_subfilters_disabled
```

Детерминированный порядок:

1. mapping/unknown key;
2. conflict;
3. enabled type;
4. required field;
5. container type;
6. empty;
7. item type;
8. format;
9. hours;
10. minutes;
11. zero-length;
12. cross-midnight;
13. duplicate;
14. overlap/nesting;
15. touching.

Один item получает одну первичную parse/range ошибку. Cross-item validation
работает только на успешно parsed items.

---

## 16. Config model и resolver

```python
@dataclass
class TradeFilterTimeFilterConfig:
    enabled: object = False
    window: Optional[str] = None
    windows: Optional[list[str]] = None
    _windows_minutes: tuple[tuple[int, int], ...] = ()
```

Canonical result:

```python
_windows_minutes = (
    (540, 720),
    (960, 1080),
)
```

Legacy single window:

```python
_windows_minutes = ((540, 1140),)
```

Production runtime читает только `_windows_minutes`.

Legacy:

```text
_start_hour
_start_minute
_end_hour
_end_minute
```

временно заполняются только для одного окна как compatibility adapter для
существующих test doubles. Они не являются production source of truth.

Active Time без resolved `_windows_minutes` вызывает fail-fast `ConfigError`.

---

## 17. Shared reset architecture

В shared core определяются:

```python
@dataclass(frozen=True)
class TimeWindowSchedule:
    windows_minutes: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class ResetEvents:
    daily_reset_enabled: bool
    daily_reset_event: np.ndarray
    time_filter_enabled: bool
    time_filter_in_window: np.ndarray
    time_filter_reset_event: np.ndarray
    combined_reset_event: np.ndarray
```

Shared resolver отвечает за:

- index validation;
- disabled all-true/all-false arrays;
- membership;
- cycle identity;
- date/gap reset;
- daily reset;
- priority-ready combined mask;
- override arrays;
- shape/dtype validation.

### 17.1 Migration map

Существующий ZigZag-local `_ApplyResetEvents`:

- не сосуществует с новым типом;
- заменяется на shared `ResetEvents`;
- existing `_resolve_apply_reset_events` либо перемещается в shared module,
  либо становится тонким compatibility wrapper;
- основное тело ZigZag только consume готовые arrays.

Standalone-volume local:

```text
_resolve_daily_reset_event
_resolve_time_filter_events
```

удаляются после перевода на shared resolver.

Test override API:

```python
daily_reset_event=
time_filter_events=(in_window, reset_event)
```

сохраняется на migration period. Compatibility wrapper переводит overrides в
`ResetEvents` и применяет те же validation checks.

Нельзя оставлять два production resolver paths.

---

## 18. Membership и cycle algorithm

Для каждого timestamp:

```text
local_date
minute_of_day
window_index или None
```

```text
in_window[t] = window_index[t] is not None
```

```text
cycle_id[t] =
    (local_date[t], window_index[t])
    if in_window[t]
    else None
```

```text
reset[0] = False

reset[t] =
    cycle_id[t-1] is not None
    AND cycle_id[t] != cycle_id[t-1]
```

Алгоритм обязан соответствовать oracle-таблице из раздела 6.

Cycle id нужен только во время build. Постоянное хранение object tuples на
каждый bar запрещено из-за memory/IPC cost. Реализация может использовать
integer cycle codes.

---

## 19. Index contract

При active Time:

- `pandas.DatetimeIndex`;
- length совпадает с data arrays;
- monotonic increasing;
- empty index допустим;
- timezone не конвертируется;
- duplicates не создают reset сами по себе;
- helper не сортирует и не удаляет строки.

При disabled Time:

```text
in_window = ones(n, bool)
reset = zeros(n, bool)
```

Disabled helper не требует DatetimeIndex.

Data validator сохраняет ответственность за duplicate policy входного dataset.

---

## 20. TimeFilterRuntime и slicing

```python
@dataclass(frozen=True)
class TimeFilterRuntime:
    reference_length: int
    schedule: TimeWindowSchedule
    in_window: np.ndarray
    reset_event: np.ndarray

    def slice(self, start: int, end: int) -> "TimeFilterRuntime":
        ...
```

Build выполняется:

- один раз на полном dataset в WF;
- один раз на полном dataset в tester;
- один раз в batch worker/run;
- локально в direct backtest, если runtime не передан.

Критический slice contract:

```text
sliced.reset_event[0] = False
```

Причина: новый backtest/slice не наследует событие, вычисленное относительно
бара до своей границы.

Membership slice-ится напрямую. Reset со второго элемента совпадает с full
runtime.

Массивы:

- 1-D;
- bool;
- pickle-safe;
- read-only после build, где возможно.

---

## 21. Performance и IPC acceptance

На reference dataset до 500 000 баров:

- полный TimeFilterRuntime build ≤ 1.0 секунды на reference workstation;
- peak временная память build ≤ 32 MB сверх index/data;
- постоянные runtime arrays ≤ 3 bytes/bar без учёта Python object header;
- serialized payload ≤ 3 bytes/bar + 64 KB metadata;
- build count в WF не зависит от количества grid points;
- runtime передаётся worker initializer один раз, а не в каждой task payload;
- Time mask build добавляет ≤ 1% к полному WF wall-clock по сравнению с тем же
  active Time run, где маска заранее подготовлена;
- `slice()` не выполняет повторный parse schedule или timestamp normalization.

Если платформа не позволяет устойчиво выполнить абсолютный 1-second budget,
фиксируется reference measurement, а допустимый regression ограничивается
`+20%` от утверждённого baseline helper benchmark.

Обязательные тесты:

- pickle round-trip;
- sequential/parallel equality;
- worker reuse;
- first reset after unpickle/slice;
- payload size;
- 100k/500k benchmark.

---

## 22. Daily reset

```text
combined_reset =
    daily_reset_event
    OR time_filter_reset_event
```

Priority:

```text
daily_reset > time_filter_reset
```

При совпадении:

```text
filter_block_reason = "daily_reset"
exit_reason = "filter_daily_reset"
```

Time mask counter может учитывать физический Time reset на том же баре, но:

- lifecycle reset выполняется один раз;
- forced exit учитывается один раз;
- exit attribution выбирает daily.

В time-only:

```text
daily_reset_enabled = False
daily_reset_event = zeros
```

Inactive ZigZag/Volume daily reset не активируется.

---

## 23. Shared decision gate

Time-only является отдельным тонким adapter-модулем, но priority и Time gate не
копируются между runtime owners.

Shared kernel принимает:

```text
entry_attempt_event
time_filter_in_window
combined_reset_event
daily_reset_event
time_filter_reset_event
```

Формулы:

```text
entry_allowed[t] =
    entry_attempt_event[t]
    AND time_filter_in_window[t]
    AND NOT combined_reset_event[t]

entry_blocked_outside[t] =
    entry_attempt_event[t]
    AND NOT time_filter_in_window[t]
    AND NOT combined_reset_event[t]
```

Priority reason:

```text
if daily_reset:
    daily_reset
elif time_reset:
    time_filter_reset
elif entry_blocked_outside:
    time_filter_out_of_window
else:
    owner-specific reason
```

Kernel не владеет ZigZag/Volume FSM. Runtime owner формирует реальное
`entry_attempt_event` в своей точке принятия решения.

---

## 24. Формальный time-only `entry_attempt`

Определения:

```text
target[t] = apply_trade_mode(trend[t])
inside[t] = time_filter_in_window[t]
reset[t] = combined_reset_event[t]
```

```text
raw_change[0] =
    target[0] != 0

raw_change[t] =
    target[t] != 0
    AND target[t] != target[t-1]
```

```text
cycle_open[0] =
    inside[0]

cycle_open[t] =
    inside[t]
    AND (
        NOT inside[t-1]
        OR time_filter_reset_event[t]
    )
```

`cycle_open` выводится из публичных reset/membership arrays. Постоянное хранение
per-bar cycle codes в TimeFilterRuntime для этого не требуется.

Runtime state:

```text
reentry_pending = False на старте
```

`reentry_pending` является локальным состоянием конкретного backtest slice:

- не наследуется от полного dataset;
- не переносится между WF train/OOS executions;
- не переносится между tester periods;
- всегда инициализируется `False` вместе с clean position/FSM state.

Если reset произошёл на inside-баре и `target[t] != 0`:

```text
reentry_pending = True
```

На следующем non-reset inside decision bar pending может быть consumed.

До применения reset priority:

```text
entry_attempt[t] =
    target[t] != 0
    AND (
        raw_change[t]
        OR cycle_open[t]
        OR reentry_pending
    )
```

После gate:

- на reset-баре attempt не разрешается;
- если reset inside, pending сохраняется для следующего бара;
- successful allowed entry consumes pending;
- target `0` очищает pending;
- выход outside без inside gap не устанавливает pending: следующий вход будет
  инициирован `cycle_open`;
- passive outside bar с неизменным target не создаёт новый attempt;
- reversal outside создаёт `raw_change` и блокируется как
  `time_filter_out_of_window`.

### 24.1 Decision table time-only

| Reset | Inside | Target | Event | Next target | Reason |
|---:|---:|---:|---|---:|---|
| 1 daily | any | any | any | 0 | `daily_reset` |
| 1 time | any | any | any | 0 | `time_filter_reset` |
| 0 | 0 | nonzero | attempt=1 | 0 | `time_filter_out_of_window` |
| 0 | 0 | nonzero | attempt=0 | 0 | `none` |
| 0 | 0 | 0 | any | 0 | `none` |
| 0 | 1 | nonzero | attempt=1 | target | allowed |
| 0 | 1 | nonzero | attempt=0, already aligned | current | `none` |
| 0 | 1 | 0 | exit/flat | 0 | owner baseline exit |

---

## 25. Entry attempts в Volume и ZigZag

Единой baseline-формулы для всех owners нет.

### 25.1 Standalone Volume

`entry_attempt_event` возникает в существующей точке, где runtime в state OFF
выполняет cycle-start evaluation.

Для сохранения owner semantics:

- каждая фактическая OFF-state start evaluation считается attempt;
- Time gate выбирается до Volume entry;
- outside evaluation получает `time_filter_out_of_window`;
- reset получает приоритет;
- window open позволяет повторную evaluation без требования нового ST flip;
- existing repeated Volume evaluations остаются допустимыми и явно считаются
  реальными owner decisions, а не passive baseline bars.

Это сохраняет смысл standalone-volume. Его blocked counters не обязаны быть
численно сопоставимы с edge-based time-only counters.

### 25.2 ZigZag/FSM

`entry_attempt_event` возникает только в точке реального FSM entry/reversal:

- разрешённый ST flip;
- immediate candidate entry;
- wakeup entry;
- иной существующий lifecycle start decision.

Time gate применяется к attempt до state transition.

При active Time blocked ZigZag attempt получает:

```text
time_filter_out_of_window
```

вместо generic `filter_off`, если Time является фактической приоритетной
причиной.

Это является принятой diagnostics migration для Time-on ZigZag. Time-off reason
arrays остаются bit-identical.

---

## 26. Time-only runtime

Модуль:

```text
donor/supertrend_optimizer/core/time_only_filter.py
```

Он:

- не реализует FSM;
- не строит ZigZag/Volume data;
- использует baseline `trend`;
- формирует time-only attempt state;
- вызывает shared decision gate;
- пишет `positions[t+1]`;
- формирует common/time diagnostics.

Порядок на баре:

```text
1. raw target
2. owner entry attempt
3. daily/time reset priority
4. outside gate
5. baseline target/exit
6. positions[t+1]
7. diagnostics/state update
```

### 26.1 Re-entry

Dense window open:

```text
08:59 outside, target long
09:00 inside → attempt/decision long
09:01 position long
```

Gap window transition:

```text
11:59 window A → 16:00 window B
16:00 reset, no entry
16:01 flat, reentry decision
16:02 position по актуальному target
```

Новый ST flip не требуется.

### 26.2 Last bar

Если reset возник на последнем баре:

- reset diagnostic = 1;
- reset count увеличивается;
- `t+1` отсутствует;
- исполненного forced exit нет;
- открытая trade получает `pending_open_trade_at_end`;
- forced-exit counter не увеличивается.

---

## 27. Standalone Volume + Time

Standalone-volume остаётся lifecycle owner.

Порядок:

```text
daily reset
time reset
volume forced exit/reversal
time entry gate
volume start/reversal evaluation
```

Time:

- запрещает starts/reversals outside;
- закрывает active state на reset;
- очищает Volume-cycle state;
- блокирует entry на inside gap-reset bar;
- не требует повторного построения mask.

При Time off:

- positions bit-identical;
- returns bit-identical;
- trades bit-identical;
- reasons/summary bit-identical.

При Time on с date/gap переходом ожидается новая семантика.

---

## 28. ZigZag + Time и ZigZag + Volume + Time

ZigZag остаётся owner:

- FSM;
- candidate state;
- confirmed legs;
- lifecycle counters;
- wakeup;
- positions.

Shared ResetEvents:

- передаётся в ZigZag `apply`;
- используется ZigZag per-bar reset-sensitive computations;
- очищает FSM/candidate/lifecycle один раз;
- не пересчитывается в основном теле `apply`.

Time:

- gate’ит реальные ZigZag entry attempts;
- закрывает position через combined reset;
- блокирует entry на gap-reset bar;
- устанавливает приоритетный Time reason.

Volume внутри ZigZag:

- остаётся дополнительным gate;
- не владеет reset;
- не строит Time mask;
- не удваивает summary события.

Осторожное ограничение рефакторинга:

```text
в ZigZag меняется только получение/consume reset и Time reason alignment;
FSM transitions вне этих точек не переписываются.
```

---

## 29. Diagnostics contract

### 29.1 Common capability

При любом active trade filter:

```text
trade_filter_enabled
trade_filter_mode
filter_allowed_entry
filter_block_reason
daily_reset_event
```

### 29.2 Time capability

```text
time_filter_enabled
time_filter_in_window
time_filter_reset_event
```

### 29.3 Capability-specific

ZigZag columns появляются только при ZigZag.

Volume columns появляются только при Volume.

Wakeup columns появляются только при active wakeup.

Time-only не создаёт fake:

```text
trade_filter_state
trade_filter_state_code
trade_filter_trigger_source
median_stop_triggered
zz_leg_stop_triggered
volume_regime
volume_condition_allowed
```

Consumers не могут требовать `trade_filter_state` как common key.

### 29.4 Diagnostics migration

При Time-on ZigZag:

- blocked entry reason меняется с generic `filter_off` на
  `time_filter_out_of_window`, когда Time является причиной;
- соответствующие summary/Excel golden обновляются;
- Time-off diagnostics сохраняются.

Миграция documented и не смешивается с position parity.

---

## 30. Exit attribution

Приоритет:

```text
filter_daily_reset
filter_time_reset
owner-specific forced exit
pending_open_trade_at_end
normal baseline/FSM exit
```

`filter_time_reset` назначается только при исполненном transition:

```text
nonzero position → zero position
```

который был вызван Time reset signal на предыдущем decision bar.

Exit attribution не должен зависеть от тяжёлого Excel per-bar diagnostics
export. Минимальные cause arrays/state передаются в trade extraction даже при
`collect_filter_diagnostics=false`, либо хранятся в отдельном компактном
execution metadata object.

При совпадении daily/time выбирается daily.

---

## 31. Summary contract

Common:

```text
mode
trade_filter_enabled
```

Time:

```text
time_filter_enabled
time_filter_reset_count
time_filter_bars_in_window
time_filter_bars_out_window
time_filter_blocked_entry_count
time_filter_forced_exit_count
```

```text
time_filter_reset_count =
    sum(time_filter_reset_event)

time_filter_blocked_entry_count =
    count(filter_block_reason == "time_filter_out_of_window")

time_filter_forced_exit_count =
    count(executed exits attributed to filter_time_reset)
```

`mode` всегда равен полному effective mode, а не приблизительному
`volume_only`/`zigzag_st_mode`.

Summary access к capability-specific arrays выполняется через `.get()`/schema
capability, без обязательных ZigZag keys.

---

## 32. Diagnostics v2 и export

Решение фиксировано:

- common sheets/columns строятся для любого active trade filter;
- Time sheets/columns строятся при active Time;
- ZigZag sheets строятся только при active ZigZag;
- Volume sheets строятся только при active Volume;
- wakeup sheets строятся только при active wakeup;
- неприменимые optional sheets получают metadata status
  `not_applicable`, если workbook contract требует запись их статуса;
- пустые fake ZigZag/Volume columns не создаются.

Signals/Trades/Cycles:

- используют common contract;
- не требуют `trade_filter_state` в time-only;
- поддерживают absence capability columns;
- сохраняют фиксированный порядок common columns;
- сохраняют existing Time-off Excel layout.

Canonical batch skip equal-blocks записывается:

```text
status = skipped_unsupported
reason = trade_filter_equal_blocks_not_supported
```

и сопровождается visible warning.

---

## 33. Snapshot и fingerprint

Не выполняется big-bang rewrite всей сериализации.

Добавляются targeted functions:

```python
trade_filter_public_snapshot(tf)
trade_filter_effective_snapshot(tf)
```

### 33.1 Public snapshot

Содержит user-facing fields:

```text
trade_filter.enabled
trade_filter.type
zigzag.enabled
volume.enabled
time_filter.enabled
time_filter.window
time_filter.windows
```

Не содержит fields, начинающиеся с `_`.

Raw YAML tester snapshot может сохранять исходный порядок `windows`.

### 33.2 Effective snapshot

```text
effective_mode
zigzag_active
volume_active
time_filter_active
canonical_windows_minutes
time_reset_semantics = "calendar_cycle_close_reset"
```

Используется для:

- effective fingerprint;
- cache identity;
- debug metadata;
- equivalence tests.

Inactive template payload не влияет на effective fingerprint.

Эквивалентны:

- различный порядок `windows`;
- legacy `window` и one-item `windows`.

Generic serializers в затронутых export paths должны исключить internal fields,
но общий framework всего проекта не переписывается.

---

## 34. Data validation

| Mode | Required data |
|---|---|
| time_only | OHLC + DatetimeIndex |
| volume_only | OHLCV |
| volume_and_time | OHLCV + DatetimeIndex |
| zigzag_only | OHLC |
| zigzag_and_time | OHLC + DatetimeIndex |
| zigzag_and_volume | OHLCV |
| zigzag_volume_and_time | OHLCV + DatetimeIndex |

Time index validation происходит до большого WF grid.

Time-only:

- не требует `volume`;
- не вызывает Volume builder;
- не вызывает ZigZag stats builder.

Inactive wakeup volume template не требует `volume`.

---

## 35. WF orchestration

Порядок:

1. load/validate config;
2. load/validate full data;
3. resolve effective mode;
4. build full TimeFilterRuntime, если Time active;
5. build ZigZag stats, если ZigZag active;
6. build VolumeRuntime, если Volume active;
7. build WF slices независимо от Time;
8. slice runtimes;
9. clear first reset каждого backtest slice;
10. execute.

Каждый extended slice:

```text
positions[0] = 0
reset[0] = False
FSM/lifecycle state = clean
```

Warmup/prepend внутри slice может сформировать state к OOS. State до ext_start
не наследуется.

Parallel:

- runtimes pickle-safe;
- передаются worker initializer;
- arrays read-only;
- sequential/parallel outputs равны.

---

## 36. Tester и batch

Single tester:

- строит full runtimes один раз;
- slice’ит для period runs;
- очищает first reset;
- не наследует positions/state между periods.

Multi-config batch:

- каждый config получает свой resolved schedule/effective mode;
- эквивалентные schedules могут использовать cache по effective snapshot;
- результаты совпадают с single tester.

Canonical batch:

- при trade filter off сохраняет existing equal-blocks + legacy behavior;
- при trade filter active помечает equal-blocks `skipped_unsupported`;
- выдаёт visible warning;
- выполняет legacy-period run;
- не считает documented skip общим failure.

Active trade filter внутри equal-blocks остаётся fail-fast unsupported на
низкоуровневом API.

---

## 37. Dual-run и rollback

До переключения production semantics создаётся offline comparator:

```text
legacy transition reset
vs
calendar-cycle reset
```

Он сравнивает:

- in_window;
- reset events;
- positions;
- returns;
- trades;
- exit reasons;
- summaries.

Comparator запускается на:

- synthetic dense data;
- synthetic gap data;
- реальном MOEX-подобном dataset;
- single-window current configs;
- multi-window configs.

Low-level builder временно принимает внутренний enum:

```python
TimeResetSemantics.LEGACY_TRANSITION
TimeResetSemantics.CALENDAR_CYCLE_CLOSE_RESET
```

Production wiring при выпуске жёстко выбирает:

```text
CALENDAR_CYCLE_CLOSE_RESET
```

Пользовательский YAML-switch не добавляется. Effective snapshot записывает
выбранную semantics для воспроизводимости.

Legacy enum сохраняется только на период dual-run/rollback и удаляется после
стабилизации, если больше не нужен characterization tests.

Rollback безопасен:

- production wiring можно временно вернуть на legacy builder;
- config schema `windows` и time-only при этом не откатываются автоматически;
- rollback decision документируется по observed comparator delta.

---

## 38. Parity и migration guarantees

| Сценарий | Гарантия |
|---|---|
| Trade filter отсутствует | bit-identical |
| `trade_filter.enabled=false` | runtime bit-identical |
| Time off, ZigZag-only | bit-identical |
| Time off, Volume-only | bit-identical |
| Time off, ZigZag+Volume | bit-identical |
| Time on, single window, dense same-date transitions | bit-identical positions/reset |
| Time on, date/session gap | намеренно новая semantics |
| Multiple windows | новая semantics |
| Time-only | новый режим |
| ZigZag Time-on blocked reason | намеренная diagnostics migration |
| Invalid nested enabled при root off | намеренная validation migration |

Golden policy:

- Time-off golden не обновляются при расхождении: расхождение является bug;
- dense same-date single-window golden не обновляются без investigation;
- date/gap Time-on golden создаются заново после dual-run review;
- diagnostics reason golden обновляются отдельно от position golden;
- обновление golden выполняется вручную с приложенным delta report.

---

## 39. API diff

### 39.1 Config API

Добавляются:

```text
TradeFilterTimeFilterConfig.windows
TradeFilterTimeFilterConfig._windows_minutes
is_time_filter_enabled
is_any_trade_subfilter_enabled
resolve_trade_filter_mode
```

### 39.2 Reset API

Добавляются:

```text
TimeWindowSchedule
ResetEvents
TimeFilterRuntime
build_time_filter_runtime
resolve_reset_events
```

Compatibility сохраняется временно:

```text
daily_reset_event override
time_filter_events override
legacy _start/_end fields for one-window test doubles
```

### 39.3 Backtest API

Добавляется optional:

```text
time_filter_runtime
```

Trade filter dispatch использует effective mode.

### 39.4 Diagnostics API

Добавляются:

```text
trade_filter_mode
time_filter_blocked_entry_count
time_filter_forced_exit_count
```

`trade_filter_state` перестаёт быть обязательным common field.

---

## 40. File-touch map

Ожидаемые обязательные изменения:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/_reset_events.py
donor/supertrend_optimizer/core/backtest.py
donor/supertrend_optimizer/core/time_only_filter.py          # new
donor/supertrend_optimizer/core/volume_only_filter.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/data/validator.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/testing/signal_events.py
donor/supertrend_optimizer/cli/tester.py
donor/supertrend_optimizer/io/excel_tester.py
donor/supertrend_optimizer/io/diagnostics_v2.py
donor TESTER/run_batch_tester.py
wf_grid/config/loader.py
wf_grid/config/schema.py
wf_grid/pipeline/orchestrator.py
wf_grid/wf/_mp_helpers.py
wf_grid/wf/step_executor.py
wf_grid/export/xlsx_writer.py
wf_grid/baseline/fingerprint.py
config.yaml
config_tester.yaml
docs/time_filter_spec.md
```

Новые/обновляемые tests:

```text
config/activation tests
reset oracle tests
gap characterization
time-only unit tests
ZigZag/Volume migration tests
WF sequential/parallel tests
single/multi/batch parity
export capability tests
snapshot/fingerprint tests
performance/pickle tests
```

Фактический diff может быть уже, но пропуск listed consumer требует
документированного обоснования.

---

## 41. Baseline quarantine

До feature branch:

1. Выбрать clean reference commit.
2. Перенести unrelated dirty-tree изменения в отдельную ветку/commit/stash,
   управляемый владельцем.
3. Не начинать feature с mixed exporter/trades changes.
4. Настроить writable pytest temp.
5. Запустить полный suite.
6. Классифицировать existing failures.
7. Сохранить baseline artifacts.

Baseline modes:

- no filter;
- root disabled;
- ZigZag-only;
- Volume-only;
- ZigZag+Volume;
- current single-window Time-on на dense data;
- current single-window Time-on на real gap data.

Artifacts:

- positions;
- returns/equity;
- trades;
- reset arrays;
- diagnostic keys/dtypes;
- summaries;
- Excel sheet/column snapshots;
- fingerprints;
- performance measurements.

Feature work не стартует из неклассифицированного грязного дерева.

---

## 42. Config tests

WF-loader, tester-loader и shared validator:

### 42.1 Legacy window

- valid;
- missing;
- null;
- invalid type/format/hours/minutes;
- zero-length;
- cross-midnight;
- disabled deep-validation bypass;
- one-item windows equivalence.

### 42.2 Windows

- one/multiple;
- automatic sort;
- reversed order equivalence;
- absent/null/scalar/mapping/empty;
- non-string item;
- invalid format/hours/minutes;
- zero-length/cross-midnight;
- duplicate;
- partial overlap;
- nesting;
- touching;
- original YAML index in error;
- unknown key;
- disabled deep-validation bypass.

### 42.3 Conflict

- both valid;
- one null;
- both null;
- Time disabled;
- root disabled.

### 42.4 Activation

- 8 combinations;
- exact mode;
- exact all-disabled error/key;
- root disabled no runtime activation;
- truthy non-bool enabled rejection.

### 42.5 Dependency matrix

- inactive templates;
- wakeup without ZigZag;
- Mode D inert template;
- contradictory type marker;
- missing explicit ZigZag enabled compatibility.

---

## 43. Reset oracle tests

Каждая строка oracle-таблицы реализуется как отдельный параметризованный case.

Дополнительно:

- `08:59`, `09:00`, `11:59`, `12:00`, `16:00`, `17:59`, `18:00`;
- seconds/nanoseconds;
- tz-naive;
- tz-aware;
- DST fold as-is;
- non-DatetimeIndex;
- non-monotonic;
- duplicate timestamps;
- length mismatch;
- empty/one-bar;
- disabled non-Datetime;
- legacy/windows parity;
- schedule order;
- slice first reset;
- last bar;
- override validation.

---

## 44. Time-only tests

Проверить:

- config/effective mode;
- no VolumeRuntime;
- no ZigZag stats;
- no volume column requirement;
- all bars preserved;
- ATR/ST/raw target на всех bars;
- no double lag;
- first inside decision → next-bar position;
- dense re-entry without ST flip;
- gap reset → reset bar old position allowed;
- next bar flat;
- reentry decision only after reset bar;
- outside reversal blocked;
- passive outside unchanged target produces no repeated reason;
- first outside nonzero target produces one attempt;
- reset priority;
- last-bar non-executed exit;
- common/time diagnostics only;
- summary counters;
- exit attribution without heavy diagnostics export.

---

## 45. Combined mode tests

### 45.1 Volume + Time

- owner attempt semantics;
- outside Time reason;
- time reset closes state;
- gap reset;
- daily priority;
- Time-off bit parity.

### 45.2 ZigZag + Time

- native FSM attempts;
- Time reason alignment;
- FSM/candidate wipe;
- gap reset;
- Mode A/B/C/D coverage;
- wakeup reset;
- Time-off golden unchanged.

### 45.3 ZigZag + Volume + Time

- all gates;
- one mask build;
- one reset;
- no double counters;
- owner-specific diagnostics;
- Time-off parity.

---

## 46. Runtime path parity

Пути подключаются и gate’ятся последовательно:

1. direct backtest;
2. WF sequential;
3. WF parallel;
4. single tester;
5. multi-config batch;
6. canonical legacy batch.

Для сравнения выравниваются:

- input slice;
- warmup/prepend;
- global offset;
- execution model;
- schedule;
- ZigZag/Volume runtimes;
- diagnostics collection mode.

Сравниваются:

```text
trend
raw positions
final positions
returns
in_window/reset
entry attempts
allowed entry
block reasons
trades/indexes
exit reasons
summary
effective snapshot
```

Паритет всех путей не является gate одного большого PR. Каждый новый wiring path
становится отдельным merge gate.

---

## 47. Поэтапная поставка с безопасными stop-points

### PR 0 — Baseline quarantine

- clean branch;
- green/classified suite;
- characterization;
- real-data comparator fixtures.

Stop-point: production code не менялся.

### PR 1 — Config и canonical schedule

- dataclass;
- schemas;
- validation;
- resolver;
- activation/effective mode;
- targeted snapshots.

Runtime behaviour не меняется.

Новые `windows` и time-only на этом промежуточном этапе доступны только
config/unit tests внутри feature branch и не объявляются production-ready.
Release/production wiring не переключается до появления runtime support.

До PR 4 любой runtime-вызов с resolved mode `time_only` обязан завершаться
явной временной fail-fast ошибкой:

```text
time_only mode resolved but runtime not wired
```

Тихий fallback в baseline запрещён. В PR 4 временный guard заменяется реальным
time-only dispatch.

Stop-point: существующие production configs и backtest outputs identical.

### PR 2 — Shared ResetEvents в legacy semantics

- shared types;
- migration adapters;
- ZigZag/Volume consume shared arrays;
- legacy transition builder;
- Time-off and existing dense parity.

Stop-point: никакой date-cycle breaking migration ещё не включено.

### PR 3 — Calendar-cycle reset

- oracle implementation;
- TimeFilterRuntime;
- slicing;
- dual-run comparator;
- accepted gap golden;
- ZigZag Time-on reason alignment:
  `time_filter_out_of_window` вместо generic `filter_off`, когда Time является
  фактической причиной;
- отдельное обновление diagnostics golden после review reason delta;
- perf/pickle tests.

Stop-point: existing owners используют новую semantics, time-only ещё не
добавлен.

### PR 4 — Thin time-only

- shared decision gate;
- formal attempt state;
- backtest dispatch;
- direct API tests;
- exit attribution.

Stop-point: direct time-only готов, orchestration подключается далее.

### PR 5 — WF

- orchestrator;
- worker initializer;
- sequential/parallel;
- fingerprint.

### PR 6 — Tester и batch

- single tester;
- multi-config batch;
- canonical legacy batch;
- equal-blocks skip metadata.

### PR 7 — Capability export

- summaries;
- Signals/Trades/Cycles;
- diagnostics v2;
- Excel snapshots;
- consume уже стабилизированного common/capability reason contract без
  дополнительного изменения runtime reasons.

### PR 8 — Документация и рабочие конфиги

- runtime spec;
- migration note;
- production config switch;
- full smoke.

Каждый PR должен быть самостоятельно откатываемым и иметь собственный
regression gate.

---

## 48. Rollout

До production switch:

1. dual-run на real dataset;
2. delta report по каждому session gap;
3. ручная проверка representative trades;
4. согласование новых PnL/golden;
5. sequential/parallel parity;
6. tester/batch parity;
7. export review;
8. performance review.

Migration note пользователю:

```text
Active Time теперь создаёт reset при переходе между календарными датами или
разными окнами даже когда два соседних существующих бара оба входят в
разрешённое время. Это может изменить overnight positions, returns и trades.
Gap PnL сохраняется до следующего доступного open-to-open исполнения.
```

---

## 49. Рабочие конфиги

Целевой time-only:

```yaml
trade_filter:
  enabled: true

  zigzag:
    enabled: false

  time_filter:
    enabled: true
    windows:
      - "09:00-12:00"
      - "16:00-18:00"

  volume:
    enabled: false

  wakeup_regime:
    enabled: false
```

Перед switch:

- убрать active `type: zigzag_st_mode`;
- explicit ZigZag false;
- explicit Volume false;
- wakeup root false;
- проверить nested wakeup volume template;
- убедиться, что volume column не требуется;
- загрузить оба loaders;
- direct smoke;
- WF short smoke;
- tester smoke;
- canonical legacy batch smoke.

Production config рекомендуется очистить от лишних inert templates, хотя schema
их допускает.

---

## 50. Documentation deliverables

Runtime specification должна содержать:

- activation/effective modes;
- legacy/new config;
- strict conflict;
- disabled validation;
- interval validation;
- touching rationale;
- timezone;
- oracle reset table;
- accepted date/gap breaking migration;
- gap exposure limitation;
- open-to-open examples;
- time-only attempt formula;
- owner-specific attempts;
- daily priority;
- first/last bar;
- precompute/slice;
- diagnostics capabilities;
- exit attribution;
- effective snapshot;
- equal-blocks limitation;
- migration guide.

---

## 51. Stop conditions

Нельзя продолжать rollout, если:

- нет clean baseline;
- Time-off golden изменились;
- dense same-date single-window unexpectedly изменился;
- oracle cases расходятся;
- `reset[0]` наследуется после slice;
- gap delta не рассмотрен;
- ZigZag FSM меняется вне reset/gate точек;
- time-only требует Volume/ZigZag;
- entry attempt не соответствует decision table;
- exit reason заявляет неисполненный last-bar exit;
- MP payload/build повторяется на grid task;
- diagnostics v2 требует fake fields;
- equal-blocks skip не виден пользователю;
- internal resolver fields попадают в public snapshot;
- performance/IPC budget нарушен без утверждённого waiver.

---

## 52. Definition of Done

Работа завершена, когда:

- `windows` и legacy `window` загружаются;
- canonical order стабилен;
- conflict/duplicate/overlap/touching отклоняются;
- activation matrix реализована;
- all-disabled root fail-fast работает;
- time-only работает без ZigZag/Volume;
- full DataFrame/WF intervals сохраняются;
- outside attempts блокируются;
- passive time-only outside bars не создают повторных reasons;
- re-entry не требует нового ST flip;
- cycle/date gap создаёт reset;
- gap exposure остаётся честно учтённой;
- reset bar может содержать старую position;
- next execution bar flat;
- last-bar reset не считается исполненным exit;
- daily priority соблюдён;
- ZigZag/Volume используют один ResetEvents;
- дублирующая Time math удалена;
- Time-off modes bit-identical;
- dense same-date single-window parity подтверждён;
- date/gap golden утверждены как migration;
- ZigZag Time reason migration утверждена;
- first reset каждого slice False;
- Time runtime строится один раз;
- sequential/parallel равны;
- direct/WF/tester/batch parity подтверждён поэтапно;
- capability export не создаёт fake fields;
- diagnostics v2 выдаёт deterministic applicable/not_applicable;
- public snapshot не содержит internal fields;
- effective fingerprint canonical;
- equal-blocks явно skipped/unsupported;
- performance/IPC criteria выполнены;
- working configs проходят loader и smoke;
- полный regression suite зелёный либо содержит только заранее
  классифицированные baseline failures.

---

## 53. Оценка

Реалистичный бюджет:

```text
28–40 инженерных дней
```

Ориентировочно:

- baseline/characterization/dual-run fixtures: 3–4 дня;
- config/activation/snapshot: 3–4 дня;
- shared reset core/migration adapters: 4–6 дней;
- cycle runtime/perf/IPC: 3–4 дня;
- ZigZag/Volume migration и golden: 5–7 дней;
- time-only kernel/runtime: 3–4 дня;
- WF/tester/batch: 3–5 дней;
- export/diagnostics/docs/config smoke: 4–6 дней.

Не включены:

- active trade filter в equal-blocks;
- scheduled/pre-open flatten;
- устранение gap exposure;
- cross-midnight support;
- общий ZigZag FSM redesign;
- унификация Volume baseline session;
- unrelated exporter/trades defects, существовавшие до feature branch.
