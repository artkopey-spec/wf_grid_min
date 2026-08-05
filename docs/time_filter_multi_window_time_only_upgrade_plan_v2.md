# План реализации `time_filter` v2: несколько окон и независимый режим time-only

Статус документа: готов к реализации после фиксации чистого baseline.

Этот документ является самостоятельной спецификацией изменения. Он определяет
конфигурационный контракт, runtime-семантику, архитектуру, диагностику, тесты,
порядок внедрения и критерии готовности.

---

## 1. Цель

Реализовать два связанных изменения:

1. Поддержать несколько разрешённых внутридневных временных интервалов через
   `trade_filter.time_filter.windows`.
2. Сделать `time_filter` самостоятельным торговым подфильтром, работающим без
   ZigZag и Volume.

Функциональность должна одинаково работать в:

- WF-оптимизации через `config.yaml`;
- прямом backtest API;
- одиночном tester;
- multi-config tester batch;
- каноническом batch tester в поддерживаемом legacy-period режиме;
- комбинированных режимах с ZigZag и Volume;
- независимом режиме time-only.

---

## 2. Неизменяемые системные инварианты

`time_filter` не должен:

- обрезать исходный DataFrame;
- удалять или синтезировать бары;
- изменять timestamps;
- сокращать train/test/OOS-интервалы;
- менять границы WF-срезов;
- исключать внеоконные бары из расчёта SuperTrend;
- исключать внеоконные бары из расчёта ATR;
- исключать внеоконные бары из raw positions;
- исключать внеоконные бары из временной шкалы доходностей и метрик;
- менять существующую модель open-to-open;
- создавать цену исполнения для отсутствующего бара;
- требовать Volume-данные в режиме time-only;
- строить ZigZag global stats в режиме time-only.

Backtest выполняется по всем входным барам. Временной фильтр управляет только:

- разрешением новых позиций;
- разрешением reversal;
- закрытием позиции через reset-событие;
- очисткой lifecycle/FSM/candidate state;
- диагностикой причин блокировки и выхода.

Независимый механизм `early_exit`, если он включён, по-прежнему может сокращать
результирующие массивы. Это поведение не относится к `time_filter` и не меняется
в рамках данной работы.

---

## 3. Модель исполнения и смысл массива `positions`

Поддерживается только существующая модель:

```text
решение формируется на close(t)
исполнение происходит на open(t+1)
positions[t] — позиция, действующая во время бара t
positions[t+1] — результат решения, принятого на close(t)
```

Следствия:

- `positions[0] = 0`;
- решение о входе на первом разрешённом баре создаёт позицию только на следующем
  баре;
- решение о reset на баре `t` не переписывает `positions[t]`;
- после reset обязательно выполняется `positions[t+1] = 0`, если `t+1`
  существует;
- reset-бар может находиться вне окна и при этом содержать старую позицию,
  действовавшую до исполнения reset;
- утверждение «позиция закрывается на баре `t`» означает формирование сигнала
  закрытия на close этого бара, а не исполнение на его open.

Нельзя маскировать готовый массив `raw_positions` по текущему индексу простым
выражением вида:

```python
positions = raw_positions * in_window
```

Такой подход нарушает open-to-open, reset-приоритет и восстановление позиции в
новом окне.

---

## 4. Семантика gap-detected reset

Временные пропуски обрабатываются через causal close-reset на первом следующем
существующем баре.

Пример:

```text
17:59 текущего дня → 09:00 следующего дня
```

На баре `09:00`:

```text
time_filter_in_window = 1
time_filter_reset_event = 1
```

При этом:

- старая позиция может присутствовать в `positions[09:00]`;
- старая позиция участвовала в open-to-open доходности временного gap;
- на close `09:00` формируется reset;
- новый вход на `09:00` запрещён;
- `positions[следующий бар] = 0`;
- новое торговое решение возможно не раньше следующего бара.

Gap reset не устраняет ценовую экспозицию на промежутке без баров. Он
гарантирует, что после обнаружения смены торгового цикла:

- старый lifecycle не продолжается;
- FSM/candidate state не переносится;
- на reset-баре не выполняется новый вход;
- старая позиция закрывается по стандартной open-to-open модели на следующем
  доступном исполнении.

Полное исключение overnight/session-gap exposure требует отдельного механизма
scheduled/pre-open execution и не входит в эту работу.

---

## 5. Активация подфильтров

Корневой переключатель сохраняется:

```yaml
trade_filter:
  enabled: true
```

Подфильтр активен только при включённом корне:

```text
zigzag_active =
    trade_filter.enabled is True
    AND trade_filter.zigzag.enabled is True

volume_active =
    trade_filter.enabled is True
    AND trade_filter.volume.enabled is True

time_filter_active =
    trade_filter.enabled is True
    AND trade_filter.time_filter.enabled is True

trade_filter_active =
    zigzag_active
    OR volume_active
    OR time_filter_active
```

Нужно добавить общие helpers:

```python
is_trade_filter_enabled(tf)
is_zigzag_enabled(tf)
is_volume_enabled(tf)
is_time_filter_enabled(tf)
is_any_trade_subfilter_enabled(tf)
resolve_trade_filter_mode(tf)
```

Helpers не должны использовать `bool(value)` для непроверенных YAML-значений.
Активным считается только значение `is True`.

---

## 6. Effective mode

`resolve_trade_filter_mode(tf)` возвращает один из следующих режимов:

| ZigZag | Volume | Time | Effective mode |
|---:|---:|---:|---|
| 0 | 0 | 0 | `disabled` или ошибка, см. ниже |
| 0 | 0 | 1 | `time_only` |
| 0 | 1 | 0 | `volume_only` |
| 0 | 1 | 1 | `volume_and_time` |
| 1 | 0 | 0 | `zigzag_only` |
| 1 | 0 | 1 | `zigzag_and_time` |
| 1 | 1 | 0 | `zigzag_and_volume` |
| 1 | 1 | 1 | `zigzag_volume_and_time` |

Правила:

- при `trade_filter.enabled=false` effective mode всегда `disabled`;
- вложенные переключатели при выключенном корне не активируют runtime;
- при `trade_filter.enabled=true` и трёх выключенных подфильтрах конфигурация
  отклоняется;
- runtime сохраняет защитную fail-fast проверку для hand-built/test-double
  конфигураций, обходящих loader.

Текст ошибки:

```text
at least one trade subfilter must be enabled when trade_filter.enabled=true:
zigzag, volume, or time_filter
```

Error key:

```text
trade_filter_all_subfilters_disabled
```

---

## 7. Runtime-dispatch

Backtest dispatch должен иметь единственный порядок:

```text
if zigzag_active:
    run ZigZag/FSM runtime
elif volume_active:
    run standalone-volume runtime
elif time_filter_active:
    run time-only runtime
else:
    run baseline runtime
```

Смысл приоритета:

- ZigZag является владельцем lifecycle, когда он активен;
- Volume и Time передаются ZigZag как дополнительные условия;
- без ZigZag владельцем lifecycle становится standalone-volume, если Volume
  активен;
- только при выключенных ZigZag и Volume владельцем lifecycle становится
  time-only runtime;
- baseline path используется только при полном отсутствии активных
  подфильтров или выключенном корне.

Нельзя реализовывать dispatch через разрознённые выражения
`zigzag_enabled or volume_enabled or time_enabled` в каждом consumer. Для
ветвления используется effective mode/helper.

---

## 8. Формат конфигурации временных окон

### 8.1 Новый формат

```yaml
trade_filter:
  time_filter:
    enabled: true
    windows:
      - "09:00-12:00"
      - "16:00-18:00"
```

### 8.2 Legacy-формат

```yaml
trade_filter:
  time_filter:
    enabled: true
    window: "09:00-19:00"
```

Legacy `window` полностью поддерживается.

### 8.3 Взаимоисключение

Ключи `window` и `windows` взаимоисключающие.

Конфликт определяется по факту присутствия ключей в YAML, а не по их значениям.
Следовательно, конфликтом являются в том числе:

```yaml
window: null
windows: null
```

и:

```yaml
window: null
windows:
  - "09:00-12:00"
```

Конфликт проверяется независимо от:

- `trade_filter.enabled`;
- `time_filter.enabled`;
- значений обоих полей.

Error key:

```text
time_filter_window_and_windows_conflict
```

### 8.4 Обязательность

При активном Time должен присутствовать ровно один из ключей:

```text
window
windows
```

Если оба отсутствуют:

```text
trade_filter.time_filter requires exactly one of window or windows when enabled
```

Error key:

```text
time_filter_windows_missing
```

Для legacy `window: null` сохраняется legacy-классификация:

```text
time_filter_window_missing
```

Для `windows: null` используется:

```text
time_filter_windows_invalid_type
```

---

## 9. Disabled-path validation

Runtime-контракт:

```text
trade_filter.enabled=false
→ весь trade_filter является runtime no-op
```

Это не отменяет проверку структуры YAML.

Всегда проверяются:

- `trade_filter` является mapping;
- вложенные известные блоки являются mapping;
- неизвестные ключи;
- тип явно указанного `time_filter.enabled`;
- конфликт `window + windows`;
- типы root/subfilter `enabled`, если ключи присутствуют.

При `time_filter.enabled=false` не выполняется глубокая проверка содержимого
`window` или `windows`:

- формат строк не проверяется;
- диапазоны не проверяются;
- список может быть пустым;
- элементы списка не проверяются;
- `windows: null` и scalar допускаются как неактивный шаблон;
- при последующем включении применяется полная валидация.

Это является уточнённым структурным контрактом. Существующие тесты, которые
ожидают игнорирования неправильного типа `time_filter.enabled` при выключенном
корне, должны быть обновлены как намеренная валидационная миграция. Runtime
baseline при этом остаётся неизменным.

---

## 10. Политика неактивных зависимых блоков

Неактивные подфильтры могут сохранять параметры как шаблон:

```yaml
zigzag:
  enabled: false
  reversal_threshold: 0.001

volume:
  enabled: false
  short_window: 10
```

Для неактивного подфильтра:

- unknown keys и mapping shape проверяются;
- `enabled` проверяется по типу;
- глубокая семантическая валидация параметров не выполняется;
- параметры не влияют на runtime;
- параметры не влияют на effective fingerprint;
- параметры могут присутствовать в raw user snapshot.

Зависимые компоненты не могут быть активны без владельца:

```text
wakeup_regime.enabled=true requires zigzag_active
```

При `zigzag.enabled=false` допускается хранить:

```yaml
wakeup_regime:
  enabled: false
  ...
```

как неактивный шаблон.

При активном корне и `zigzag.enabled=false` запрещается
`type: zigzag_st_mode`, потому что это создаёт противоречивый effective mode.

Legacy-конфиги, в которых `zigzag.enabled` отсутствует, продолжают разрешаться
существующим compatibility resolver. Для явного time-only обязательно
указывать:

```yaml
zigzag:
  enabled: false
```

---

## 11. Семантика интервалов

Все интервалы полуоткрытые:

```text
[start, end)
```

Для:

```yaml
windows:
  - "09:00-12:00"
  - "16:00-18:00"
```

получаем:

```text
[09:00, 12:00)
[16:00, 18:00)
```

На минутных данных:

- `09:00–11:59` — внутри;
- `12:00–15:59` — снаружи;
- `16:00–17:59` — внутри;
- начиная с `18:00` — снаружи.

Секунды и наносекунды не участвуют в membership:

```text
11:59:59.999 → минута 11:59 → внутри
12:00:00.001 → минута 12:00 → снаружи
```

Компоненты даты и времени берутся из индекса as-is:

- нет UTC conversion;
- нет local timezone conversion;
- нет DST correction;
- для tz-aware индекса используется его текущее локальное wall-clock
  представление без смены timezone.

---

## 12. Валидация `windows`

При активном Time `windows` должен быть непустым YAML-списком строк.

Проверки контейнера:

- list — обязателен;
- `null`, scalar и mapping запрещены;
- пустой список запрещён.

Проверки каждого элемента:

- тип строго `str`;
- формат строго `HH:MM-HH:MM`;
- пробелы и сокращённые часы не допускаются;
- часы находятся в `00–23`;
- минуты находятся в `00–59`;
- начало строго раньше конца;
- zero-length запрещён;
- cross-midnight запрещён.

После разбора интервалы сортируются по `(start_minutes, end_minutes)`.

Для соседних нормализованных интервалов обязательно:

```text
previous.end < next.start
```

Запрещаются:

- дубли;
- частичные пересечения;
- полные вложения;
- соприкасающиеся интервалы.

Соприкосновение:

```text
[09:00, 12:00)
[12:00, 18:00)
```

запрещено. Непрерывный диапазон задаётся одним интервалом:

```yaml
windows:
  - "09:00-18:00"
```

Проверка выполняется за `O(k log k)` из-за сортировки. При сравнении необходимо
сохранять исходный YAML-индекс каждого элемента, чтобы сообщение об ошибке
указывало пользовательский путь, а не позицию после сортировки.

Пример:

```text
trade_filter.time_filter.windows[1] overlaps windows[0]
```

---

## 13. Ошибки конфигурации

Legacy error keys сохраняются:

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

Приоритет классификации ошибок:

1. mapping shape и unknown keys;
2. conflict `window + windows`;
3. тип `enabled`;
4. отсутствие активного значения;
5. тип контейнера;
6. пустой контейнер;
7. item type;
8. item format;
9. hours;
10. minutes;
11. zero-length;
12. cross-midnight;
13. duplicate;
14. overlap/nesting;
15. touching.

Один элемент может давать только одну первичную item-ошибку. Cross-item
проверки выполняются только для успешно разобранных элементов.

Все новые keys добавляются в централизованный registry validation error keys и
проверяются одинаково WF-loader и tester-loader.

---

## 14. Каноническое представление

Dataclass расширяется:

```python
@dataclass
class TradeFilterTimeFilterConfig:
    enabled: object = False
    window: Optional[str] = None
    windows: Optional[list[str]] = None
    _windows_minutes: tuple[tuple[int, int], ...] = ()
```

После успешной валидации resolver создаёт:

```python
_windows_minutes = (
    (540, 720),
    (960, 1080),
)
```

Legacy:

```yaml
window: "09:00-19:00"
```

нормализуется в:

```python
_windows_minutes = ((540, 1140),)
```

Runtime читает только `_windows_minutes`.

Legacy resolver fields:

```text
_start_hour
_start_minute
_end_hour
_end_minute
```

могут временно заполняться только для одного окна и только как compatibility
adapter для существующих test doubles. Новый production runtime не должен
использовать их как основной контракт.

Если Time активен, а `_windows_minutes` не материализован, production runtime
выдаёт fail-fast `ConfigError` с указанием вызвать resolver.

---

## 15. Общий schedule и временные события

В `_reset_events.py` вводится общий неизменяемый schedule:

```python
@dataclass(frozen=True)
class TimeWindowSchedule:
    windows_minutes: tuple[tuple[int, int], ...]
```

И общий результат:

```python
@dataclass(frozen=True)
class ResetEvents:
    daily_reset_enabled: bool
    daily_reset_event: np.ndarray
    time_filter_enabled: bool
    time_filter_in_window: np.ndarray
    time_filter_reset_event: np.ndarray
    combined_reset_event: np.ndarray
```

Единый resolver отвечает за:

- disabled all-true/all-false contract;
- DatetimeIndex validation;
- length validation;
- monotonic validation;
- time membership;
- gap-aware cycle transition;
- daily reset;
- override arrays для unit tests;
- combined reset;
- dtype/shape validation.

ZigZag, standalone-volume и time-only не вычисляют временную математику
самостоятельно.

---

## 16. Построение membership и cycle identity

Для каждого timestamp вычисляются:

```text
local_date
minute_of_day
window_index или None
```

Membership:

```text
time_filter_in_window[t] =
    существует interval i:
    interval.start <= minute_of_day[t] < interval.end
```

Для разрешённого бара:

```text
cycle_id[t] = (local_date[t], window_index[t])
```

Для запрещённого бара:

```text
cycle_id[t] = None
```

Reset:

```text
time_filter_reset_event[0] = False

time_filter_reset_event[t] =
    cycle_id[t-1] is not None
    AND cycle_id[t] != cycle_id[t-1]
```

Это правило покрывает:

- плотный выход `11:59 → 12:00`;
- пропуск `11:59 → 12:01`;
- переход `11:59 → 16:00`;
- переход `17:59 → 09:00` следующего дня;
- переход между любыми двумя разными разрешёнными окнами;
- отсутствие повторных reset на последовательных внеоконных барах.

На баре нового окна после gap возможна комбинация:

```text
in_window = True
reset_event = True
```

Reset всегда имеет приоритет над входом.

---

## 17. Требования к индексу

При активном Time:

- индекс должен быть `pandas.DatetimeIndex`;
- длина индекса должна совпадать с длиной runtime-массивов;
- индекс должен быть monotonic increasing;
- пустой индекс допустим и возвращает пустые массивы;
- первый reset всегда `False`;
- timezone не конвертируется;
- duplicate timestamps не создают reset сами по себе.

Основной data validator по-прежнему отвечает за политику дубликатов входных
данных. Общий helper не удаляет и не сортирует строки.

При выключенном Time:

```text
time_filter_in_window = np.ones(n, dtype=bool)
time_filter_reset_event = np.zeros(n, dtype=bool)
```

В disabled path helper не требует DatetimeIndex.

---

## 18. Предвычисление и производительность

Временные события зависят только от:

- индекса;
- канонического schedule;
- флага enabled.

Они не зависят от ATR period, multiplier или trade mode. Поэтому WF-grid не
должен пересчитывать полную маску для каждого grid point.

Добавляется:

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

Runtime строится:

- один раз на полном dataset в WF orchestrator;
- один раз в single tester до period slicing;
- один раз на dataset в batch tester;
- локально внутри direct backtest только если caller не передал runtime.

Критический контракт `slice()`:

```text
sliced.reset_event[0] = False
```

Reset первого бара нельзя наследовать от бара за пределами нового backtest/WF
slice. Membership можно slice-ить напрямую; reset внутри slice совпадает с
полным runtime начиная со второго элемента.

Массивы runtime должны быть pickle-safe и по возможности read-only для
parallel WF workers.

---

## 19. Daily reset

Общий combined reset:

```text
combined_reset_event =
    daily_reset_event
    OR time_filter_reset_event
```

Приоритет причин:

```text
daily_reset > time_filter_reset
```

Следствия:

- при совпадении двух reset `filter_block_reason = "daily_reset"`;
- trade-level причина `exit_reason = "filter_daily_reset"`;
- time reset не учитывается второй раз как отдельный forced exit;
- счётчик `time_filter_reset_count` отражает физическую time-mask и может
  включать бар совпадения;
- общий reset выполняется ровно один раз.

В чистом time-only нет независимого daily reset:

```text
daily_reset_enabled = False
daily_reset_event = all False
```

`zigzag.daily_reset` и `volume.daily_reset` не активируются, если соответствующий
подфильтр выключен.

---

## 20. Time-only runtime

Рекомендуемый модуль:

```text
donor/supertrend_optimizer/core/time_only_filter.py
```

Входы:

```text
trend
trade_mode
execution_model
ResetEvents или TimeFilterRuntime
collect_filter_diagnostics
```

Time-only не принимает и не строит:

- ZigZag global stats;
- ZigZag per-bar data;
- VolumeRuntime;
- volume array;
- wakeup state.

### 20.1 Raw baseline target

SuperTrend рассчитывается существующим backtest-кодом на всех барах.

Для решения на close `t` используется:

```text
raw_target[t] = trade-mode-adjusted trend[t]
```

Эквивалентность baseline:

```text
generate_positions(trend, trade_mode)[t+1] == raw_target[t]
```

для всех баров, где `t+1` существует.

Нельзя повторно сдвигать уже lagged `raw_positions`.

### 20.2 Decision order

На каждом баре `t`:

```text
1. определить current active position/lifecycle target;
2. если daily reset:
       target_next = 0
       запретить вход
       очистить lifecycle
3. elif time reset:
       target_next = 0
       запретить вход
       очистить lifecycle
4. elif not in_window:
       target_next = 0
       запретить новый вход/reversal
5. else:
       target_next = raw_target[t]
6. если t+1 существует:
       positions[t+1] = target_next
```

### 20.3 Восстановление позиции в новом окне

Если новое окно началось без gap-reset:

- time-only не ждёт нового ST flip;
- на первом разрешённом баре берётся текущий `raw_target`;
- позиция появляется на следующем баре.

Пример:

```text
08:59 outside, trend=long
09:00 inside, trend=long → решение long
09:01 positions=long
```

### 20.4 Reset-бар внутри нового окна после gap

Пример:

```text
11:59 → 16:00
```

На `16:00`:

```text
in_window = True
reset_event = True
```

Поведение:

- старая позиция может действовать на баре `16:00`;
- на close формируется flat;
- новый long/short на этом баре не формируется;
- следующий бар flat;
- решение в направлении текущего SuperTrend возможно со следующего close.

### 20.5 Reversal вне окна

Если baseline меняет направление вне окна:

- старая позиция уже должна быть flat после исполнения reset;
- противоположная позиция не открывается;
- событие может получить `time_filter_out_of_window`;
- при начале следующего окна используется актуальное направление, даже если
  flip произошёл вне окна.

---

## 21. Формальное определение entry attempt

Чтобы не создавать искусственную блокировку на каждом внеоконном баре,
вводится:

```text
entry_attempt_event[t]
```

Для time-only это True, когда:

- lifecycle flat и текущий raw target стал ненулевым после изменения baseline;
- произошёл reversal baseline;
- началось разрешённое окно и lifecycle может восстановить текущий ненулевой
  target;
- runtime после разрешённого состояния пытается изменить flat на ненулевое
  значение.

Это False:

- на пассивном внеоконном баре с неизменным raw target;
- на reset-баре;
- при `raw_target == 0`;
- когда текущая разрешённая позиция уже совпадает с target.

Диагностика:

```text
filter_allowed_entry[t] = 1
```

только для реально разрешённого entry/reversal decision.

```text
filter_block_reason[t] = "time_filter_out_of_window"
```

только для `entry_attempt_event[t]` вне окна.

```text
filter_block_reason[t] = "time_filter_reset"
```

на time reset, независимо от наличия entry attempt.

---

## 22. Standalone Volume + Time

Standalone-volume остаётся владельцем lifecycle.

Time:

- запрещает старт Volume-cycle вне окна;
- запрещает reversal entry вне окна;
- закрывает активную позицию на time reset независимо от Volume;
- полностью сбрасывает standalone-volume state;
- запрещает новый Volume-entry на gap-reset баре внутри нового окна.

Приоритет событий:

```text
daily reset
time reset
volume forced exit/reversal
entry evaluation
```

При выключенном Time результат standalone-volume должен остаться побитово
совместимым.

Standalone-volume получает готовый `ResetEvents`; его локальные функции
`_resolve_time_filter_events` и дублирующая математика удаляются.

---

## 23. ZigZag + Time и ZigZag + Volume + Time

При активном ZigZag он остаётся владельцем:

- FSM;
- lifecycle;
- candidate state;
- confirmed-leg counters;
- wakeup state;
- позиции.

Time:

- запрещает lifecycle entry вне окна;
- закрывает позицию через combined reset;
- очищает FSM;
- очищает candidate state;
- очищает ZigZag per-bar reset-sensitive state;
- запрещает новый вход на reset-баре;
- начинает новый независимый цикл после reset.

При ZigZag + Volume + Time:

- Volume и Time являются независимыми дополнительными gates;
- time reset закрывает позицию независимо от Volume;
- reset вычисляется один раз;
- ZigZag per-bar получает готовый `combined_reset_event`;
- diagnostics не удваивают событие;
- summary не удваивает forced exit.

Из основного тела ZigZag `apply` удаляется повторный расчёт daily/time reset.
После `_resolve_apply_reset_events` или его общего replacement используется
только возвращённый `ResetEvents`.

При выключенном Time ZigZag-only, Volume-only и ZigZag+Volume должны сохранить
прежние positions, returns, trades и diagnostics.

---

## 24. Общий diagnostic contract

На любом активном trade-filter пути обязательны общие колонки:

```text
trade_filter_enabled
trade_filter_mode
filter_allowed_entry
filter_block_reason
daily_reset_event
```

Time capability добавляет:

```text
time_filter_enabled
time_filter_in_window
time_filter_reset_event
```

ZigZag capability добавляет только ZigZag/FSM-поля.

Volume capability добавляет только Volume-поля.

Time-only не обязан создавать:

```text
trade_filter_state
trade_filter_state_code
trade_filter_trigger_source
median_stop_triggered
zz_leg_stop_triggered
volume_regime
volume_condition_allowed
```

как фиктивные значения.

Consumer, который ранее требовал `trade_filter_state`, должен быть адаптирован к
общему contract и обрабатывать capability-specific колонки только при их
наличии.

При `collect_filter_diagnostics=false` positions и trades сохраняют корректное
поведение, а per-bar diagnostic dict может отсутствовать согласно текущему API.
Trade exit attribution не должна зависеть от включённого полного Excel-export.
Минимальные execution-cause arrays должны либо сохраняться отдельно, либо
передаваться в trade extraction независимо от тяжёлой диагностики.

---

## 25. Причины блокировки и выхода

Приоритет `filter_block_reason`:

```text
daily_reset
time_filter_reset
режим-специфичная forced-exit причина
time_filter_out_of_window
volume reason
zigzag reason
none
```

Обязательные time-причины:

```text
time_filter_reset
time_filter_out_of_window
```

Trade-level причины:

```text
filter_daily_reset
filter_time_reset
```

Приоритет:

```text
filter_daily_reset > filter_time_reset
```

`filter_time_reset` назначается только если reset-сигнал реально привёл к
исполненному переходу позиции в flat.

Если reset возник на последнем баре и `t+1` отсутствует:

- reset остаётся в per-bar diagnostics;
- `time_filter_reset_count` увеличивается;
- исполненного закрытия нет;
- открытая сделка сохраняет существующую end-of-data причину
  `pending_open_trade_at_end`;
- `time_filter_forced_exit_count` не увеличивается.

Это предотвращает ложное утверждение об исполнении, для которого нет следующего
open.

---

## 26. Summary contract

Общий summary содержит:

```text
mode
trade_filter_enabled
```

При активном Time:

```text
time_filter_enabled
time_filter_reset_count
time_filter_bars_in_window
time_filter_bars_out_window
time_filter_blocked_entry_count
time_filter_forced_exit_count
```

Определения:

```text
time_filter_reset_count =
    sum(time_filter_reset_event)

time_filter_bars_in_window =
    sum(time_filter_in_window)

time_filter_bars_out_window =
    n - time_filter_bars_in_window

time_filter_blocked_entry_count =
    count(filter_block_reason == "time_filter_out_of_window")

time_filter_forced_exit_count =
    count(executed nonzero → zero transitions attributed to time reset)
```

Для time-only:

```text
mode = "time_only"
```

Для Volume+Time и ZigZag+Time используется полный effective mode, а не
приближённое имя владельца lifecycle.

Summary builder не должен обращаться к отсутствующим capability-specific
массивам по прямому индексу.

---

## 27. Snapshot и сериализация

Вводятся два разных представления.

### 27.1 Public/raw snapshot

Содержит пользовательские поля:

```text
trade_filter.enabled
trade_filter.type
zigzag.enabled
volume.enabled
time_filter.enabled
time_filter.window
time_filter.windows
```

Raw YAML snapshot может сохранять исходный порядок `windows`.

Internal fields с префиксом `_` не экспортируются в пользовательский YAML/XLSX
config snapshot.

### 27.2 Effective snapshot

Содержит:

```text
effective_mode
zigzag_active
volume_active
time_filter_active
canonical_windows_minutes
```

`canonical_windows_minutes` всегда отсортирован.

Effective snapshot является единственным источником для:

- effective fingerprint;
- cache identity;
- сравнения эквивалентных конфигураций;
- runtime debug metadata.

Неактивные template-параметры не входят в effective fingerprint.

Эквивалентны:

```yaml
windows:
  - "16:00-18:00"
  - "09:00-12:00"
```

и:

```yaml
windows:
  - "09:00-12:00"
  - "16:00-18:00"
```

Legacy `window: "09:00-19:00"` имеет тот же effective fingerprint, что:

```yaml
windows:
  - "09:00-19:00"
```

Нужно заменить generic dataclass serialization, включающую underscore fields,
на явные public/effective serializers.

---

## 28. Data validation

Data requirements зависят от активных capabilities:

```text
time_only → OHLC + DatetimeIndex
volume_only / volume_and_time → OHLCV + DatetimeIndex
zigzag_without_volume → OHLC + DatetimeIndex, если Time или daily reset требуют индекс
zigzag_with_volume → OHLCV
```

Time-only:

- не требует `volume`;
- не вызывает `build_volume_global_metrics`;
- не вызывает `build_zigzag_global_stats`;
- требует DatetimeIndex только потому, что Time активен.

Проверка Time index должна происходить до запуска большого WF grid, чтобы
ошибка была fail-fast, а не повторялась в каждом grid point.

---

## 29. WF и slicing

WF orchestration:

1. загружает и валидирует полный DataFrame;
2. строит canonical `TimeWindowSchedule`;
3. строит полный `TimeFilterRuntime` один раз;
4. строит ZigZag stats только при `zigzag_active`;
5. строит VolumeRuntime только при `volume_active`;
6. создаёт WF slices без участия Time;
7. передаёт sliced runtimes в каждый backtest.

Каждый extended WF slice начинается с чистого runtime state:

```text
positions[0] = 0
reset_event[0] = False
FSM/lifecycle/candidate state = clean
```

Warmup/prepend бары внутри конкретного extended slice участвуют в расчётах и
могут сформировать состояние к началу OOS, как и в существующей архитектуре.
Состояние не наследуется от бара до `ext_start`.

Паритет direct/WF сравнивается только при одинаковых:

- фактических входных барах;
- warmup/prepend;
- global offset;
- global stats/runtime;
- execution model.

Нельзя сравнивать full direct backtest с OOS-only массивом без выравнивания
среза.

---

## 30. Single tester и period slices

Single tester:

- строит TimeFilterRuntime один раз на полном DataFrame;
- для `100%`, `75%`, `50%`, `33%`, `25%` использует slice runtime;
- очищает первый reset каждого period slice;
- не наследует позицию между period runs;
- не строит ZigZag stats в time-only;
- не строит VolumeRuntime в time-only;
- экспортирует effective mode и доступные capability columns.

`raw positions` для parity-тестов вычисляются из возвращённого `trend` через
baseline `generate_positions`; добавлять их как постоянный тяжёлый diagnostics
array необязательно.

---

## 31. Batch tester и equal-blocks

Поддерживаемый batch scope:

- multi-config batch, запускающий tester для набора конфигов;
- канонический batch tester в legacy-period режиме;
- `config_tester.yaml` с active time-only или комбинированным trade filter.

`equal_blocks + trade_filter.enabled=true` остаётся неподдерживаемым в этой
работе.

Канонический batch launcher, который исторически запускает одновременно
equal-blocks и legacy:

- при выключенном trade filter сохраняет оба запуска;
- при активном trade filter не должен пытаться выполнить equal-blocks;
- явно пишет, что equal-blocks пропущен как unsupported;
- выполняет legacy-period run;
- не считает пропуск equal-blocks ошибкой всего batch;
- не скрывает это решение в metadata.

Добавление полноценной поддержки trade filter в equal-blocks является
отдельным изменением.

---

## 32. Exporter architecture

Exporter определяется effective mode и capabilities:

```text
common columns → при любом active trade filter
time columns → при time_filter_active
volume columns → при volume_active
zigzag columns → при zigzag_active
wakeup columns → при активном wakeup
```

Требования:

- отсутствие ZigZag columns не является ошибкой time-only;
- отсутствие Volume columns не является ошибкой time-only;
- Signals sheet использует common diagnostic contract;
- Trades/Cycles не требуют `trade_filter_state` для time-only;
- diagnostics v2 либо поддерживает capability-based sheets, либо пропускает
  неприменимые ZigZag sheets с явным статусом `not_applicable`;
- фиксированный порядок общих колонок сохраняется;
- старые ZigZag/Volume sheets не меняются при выключенном Time;
- time reset exit attribution работает и при отключённом тяжёлом per-bar
  diagnostics export.

---

## 33. Characterization baseline перед реализацией

До первого production-изменения необходимо:

1. выбрать чистый reference commit;
2. отделить существующие пользовательские изменения рабочего дерева;
3. запустить текущий regression suite с доступным pytest temp;
4. классифицировать существующие failures;
5. зафиксировать outputs для:
   - baseline без trade filter;
   - `trade_filter.enabled=false`;
   - ZigZag-only;
   - Volume-only;
   - ZigZag+Volume;
   - legacy single-window Time в существующих комбинированных режимах;
6. сохранить positions, returns, trades, diagnostics keys/dtypes, summaries и
   fingerprints;
7. не обновлять golden snapshots автоматически.

Если reference baseline не зелёный, известные failures документируются отдельно
до реализации и не смешиваются с регрессиями новой функции.

---

## 34. Тесты конфигурации

Одинаковые cases выполняются через shared validator, WF-loader и tester-loader.

### 34.1 Legacy `window`

- valid;
- missing;
- null;
- invalid type;
- invalid format;
- invalid hours;
- invalid minutes;
- zero-length;
- cross-midnight;
- disabled path;
- эквивалентность одному `windows` item.

### 34.2 `windows`

- valid one item;
- valid multiple items;
- automatic sort;
- reversed input order;
- absent;
- null;
- scalar;
- mapping;
- empty list;
- non-string item;
- invalid format;
- invalid hours;
- invalid minutes;
- zero-length;
- cross-midnight;
- duplicate;
- partial overlap;
- full nesting;
- touching;
- original YAML index в error message;
- unknown key;
- disabled no-deep-validation path.

### 34.3 Conflict

- valid `window` + valid `windows`;
- null `window` + valid `windows`;
- valid `window` + null `windows`;
- both null;
- conflict при `time_filter.enabled=false`;
- conflict при `trade_filter.enabled=false`.

### 34.4 Activation matrix

Все восемь комбинаций ZigZag/Volume/Time:

- семь допустимых active modes;
- all-disabled error при включённом корне;
- always-disabled runtime при выключенном корне;
- exact effective mode;
- exact error key.

### 34.5 Dependent templates

- inactive ZigZag payload accepted and ignored;
- inactive Volume payload accepted and ignored;
- inactive wakeup template accepted;
- active wakeup rejected without active ZigZag;
- `type: zigzag_st_mode` rejected при explicit `zigzag.enabled=false` и активном
  корне.

---

## 35. Тесты общего time helper

На плотном индексе:

```text
08:59 → outside
09:00 → inside
11:59 → inside
12:00 → outside + reset
12:01–15:59 → outside, no reset
16:00 → inside
17:59 → inside
18:00 → outside + reset
после 18:00 → outside, no reset
```

Дополнительно:

- seconds/nanoseconds ignored;
- tz-naive;
- tz-aware;
- DST-aware wall-clock behavior without conversion;
- non-DatetimeIndex;
- non-monotonic index;
- duplicate timestamps;
- length mismatch;
- empty index;
- one-bar index;
- disabled filter with non-DatetimeIndex;
- legacy window parity;
- window-order parity;
- dtype and shape contract.

---

## 36. Тесты временных пропусков

### 36.1 Выход в gap

```text
11:59 → 12:01
```

Ожидание:

```text
reset[12:01] = 1
```

### 36.2 Переход между окнами

```text
11:59 → 16:00
```

Ожидание:

```text
in_window[16:00] = 1
reset[16:00] = 1
entry_allowed[16:00] = 0
positions[next_bar] = 0
```

### 36.3 Переход между днями

```text
17:59 day 1 → 09:00 day 2
```

Ожидание:

- reset на `09:00`;
- gap return остаётся в старой позиции;
- позиция на `09:00` может быть старой;
- следующий бар flat;
- новый вход не выполняется на reset-баре.

### 36.4 Slice boundary

- первый бар отдельного backtest не получает synthetic/inherited reset;
- slice полного TimeFilterRuntime очищает `reset[0]`;
- второй и последующие reset совпадают с локальным пересчётом.

---

## 37. Тесты time-only

Конфигурация:

```yaml
trade_filter:
  enabled: true

  zigzag:
    enabled: false

  volume:
    enabled: false

  time_filter:
    enabled: true
    windows:
      - "09:00-12:00"
      - "16:00-18:00"
```

Проверить:

- config принимается;
- effective mode `time_only`;
- VolumeRuntime не строится;
- ZigZag global stats не строятся;
- volume column не требуется;
- исходный DataFrame не изменяется;
- число баров результата совпадает с входом без unrelated early exit;
- WF-границы не меняются;
- SuperTrend и ATR рассчитаны на всех барах;
- raw baseline positions рассчитаны на всех барах;
- первый разрешённый bar формирует решение;
- позиция появляется на следующем баре;
- текущее направление восстанавливается без нового ST flip;
- пассивные outside bars не получают ложный block reason;
- reversal outside получает `time_filter_out_of_window`;
- reset имеет приоритет над reversal и entry;
- reset очищает lifecycle;
- reset-bar может содержать старую позицию;
- следующий бар flat;
- last-bar reset не помечается как исполненный forced exit;
- trade получает `filter_time_reset` только при исполненном закрытии;
- common/time diagnostics присутствуют;
- ZigZag/Volume diagnostics не подделываются;
- отключение тяжёлого diagnostics export не меняет positions и exit attribution.

---

## 38. Тесты комбинированных режимов

### 38.1 Time + Volume

- Volume определяет допустимость cycle start;
- Time дополнительно запрещает start/reversal outside;
- time reset закрывает независимо от Volume;
- daily reset имеет приоритет;
- gap-reset bar не открывает новый Volume-cycle;
- Time-off результат побитово совпадает с Volume-only baseline.

### 38.2 Time + ZigZag

- ZigZag владеет FSM;
- Time запрещает lifecycle entry outside;
- time reset очищает FSM/candidate/lifecycle;
- gap-reset bar не создаёт новый ZigZag entry;
- новое окно начинает новый цикл;
- Time-off результат совпадает с ZigZag-only golden.

### 38.3 Time + ZigZag + Volume

- все gates применяются;
- временная маска строится один раз;
- reset выполняется один раз;
- summary не удваивает события;
- combined priority детерминирован;
- Time-off результат совпадает с ZigZag+Volume baseline.

---

## 39. Паритет runtime-путей

На одном детерминированном dataset сравниваются:

- direct backtest;
- WF с точно выровненным extended slice;
- single tester `100%`;
- multi-config tester batch;
- canonical legacy batch.

Сравниваются:

```text
trend
raw baseline positions
final positions
returns
time_filter_in_window
time_filter_reset_event
filter_allowed_entry
filter_block_reason
entry indexes
exit indexes
exit_reason
summary counters
effective mode
effective snapshot
```

Перед сравнением должны совпадать:

- входной slice;
- warmup;
- execution model;
- global offset;
- precomputed runtimes;
- diagnostics collection mode.

---

## 40. Регрессионные гарантии

Обязательные доказательства:

1. Отсутствующий trade filter совпадает с baseline.
2. `trade_filter.enabled=false` совпадает с baseline по runtime outputs.
3. Legacy `window` совпадает с одним эквивалентным `windows`.
4. Порядок `windows` не влияет на runtime.
5. Порядок `windows` не влияет на effective fingerprint.
6. ZigZag-only не меняется.
7. Volume-only не меняется.
8. ZigZag+Volume без Time не меняется.
9. Existing diagnostics/export без Time не ломаются.
10. Time-only не требует Volume.
11. Time-only не строит ZigZag stats.
12. Внеоконные бары остаются в данных и метриках.
13. Первый reset slice всегда False.
14. Parallel и sequential WF совпадают.
15. Public snapshot не содержит underscore resolver fields.

Изменение structural validation для неправильного nested `time_filter.enabled`
при выключенном корне является намеренной config-validation миграцией и не
считается runtime baseline regression.

---

## 41. Обновление рабочих конфигов

Рабочие конфиги изменяются только после:

- зелёных unit/integration/regression tests;
- successful loader tests;
- direct smoke;
- WF smoke;
- tester smoke;
- legacy batch smoke.

Целевой time-only блок:

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

Перед включением:

- убрать или отключить `type: zigzag_st_mode`;
- `wakeup_regime.enabled` установить в `false`;
- проверить все wakeup entry/exit dependencies;
- убедиться, что triggers/lifecycle не влияют на effective snapshot;
- проверить Volume wakeup dependency;
- проверить отсутствие требования volume column;
- загрузить оба config loader;
- выполнить короткий smoke на малом диапазоне grid.

Неактивные параметры могут оставаться как шаблон, но рекомендуется сократить
production config до реально используемых блоков, чтобы уменьшить риск
случайной активации.

---

## 42. Документация

После реализации обновить `docs/time_filter_spec.md` как runtime-спецификацию.

Документация должна содержать:

- activation/effective mode matrix;
- time-only;
- `window` и `windows`;
- mutual exclusion;
- disabled validation contract;
- half-open semantics;
- auto-sort;
- запрет duplicate/overlap/nesting/touching;
- timezone as-is;
- cycle identity;
- dense reset;
- gap-detected close reset;
- явное сохранение gap exposure;
- reset priority;
- first-bar rule;
- last-bar rule;
- open-to-open position semantics;
- восстановление baseline target в новом окне;
- отсутствие data trimming;
- common/capability diagnostics;
- summary counters;
- snapshot/fingerprint contract;
- equal-blocks limitation;
- примеры всех effective modes.

Также обновить комментарии в `config.yaml` и `config_tester.yaml`.

---

## 43. Порядок реализации

### Этап 0. Baseline

1. Выбрать reference commit.
2. Получить чистый regression run.
3. Зафиксировать golden outputs текущих режимов.
4. Отделить unrelated working-tree changes.

### Этап 1. Activation и config model

1. Добавить activation helpers и effective mode.
2. Расширить dataclass полями `windows` и `_windows_minutes`.
3. Добавить `windows` в обе strict schemas.
4. Реализовать structural/active validation phases.
5. Реализовать error keys и deterministic precedence.
6. Реализовать canonical resolver.
7. Добавить public/effective serializers.

На этом этапе runtime ещё не меняется.

### Этап 2. Общий time/reset core

1. Добавить `TimeWindowSchedule`.
2. Реализовать membership/cycle-id алгоритм.
3. Добавить `ResetEvents`.
4. Добавить `TimeFilterRuntime`.
5. Реализовать boundary-safe slicing.
6. Добавить unit/property tests helper.

### Этап 3. Миграция существующих runtimes

1. Перевести ZigZag на единый `ResetEvents`.
2. Удалить повторный daily/time расчёт.
3. Перевести standalone-volume.
4. Подтвердить golden parity при Time-off.
5. Подтвердить single-window parity при Time-on.

### Этап 4. Time-only

1. Добавить `time_only_filter.py`.
2. Реализовать raw target без двойного лага.
3. Реализовать entry-attempt semantics.
4. Реализовать reset/entry priority.
5. Реализовать common/time diagnostics.
6. Реализовать executed forced-exit attribution.

### Этап 5. Wiring

1. Обновить backtest dispatch.
2. Обновить WF orchestrator/workers.
3. Обновить tester runner.
4. Обновить single tester CLI.
5. Обновить multi-config batch.
6. Обновить canonical batch legacy behavior.
7. Добавить fail-fast data/index validation.

### Этап 6. Diagnostics/export

1. Перевести summary на effective mode.
2. Перевести trade attachment на common contract.
3. Адаптировать Signals/Trades/Cycles.
4. Адаптировать diagnostics v2.
5. Адаптировать XLSX config snapshot.
6. Проверить отсутствие fake capability columns.

### Этап 7. Regression и performance

1. Запустить activation/config tests.
2. Запустить helper/time-only tests.
3. Запустить combined-mode tests.
4. Запустить parity matrix.
5. Запустить sequential/parallel WF.
6. Запустить полный regression suite.
7. Выполнить benchmark на полном индексе.
8. Проверить отсутствие повторного mask build на каждый grid point.

### Этап 8. Документация и рабочие конфиги

1. Обновить спецификацию.
2. Обновить примеры.
3. Изменить рабочие конфиги.
4. Выполнить loader smoke.
5. Выполнить direct/WF/tester/batch smoke.

---

## 44. Stop conditions

Рабочие конфиги нельзя переключать на time-only, если:

- baseline reference не определён;
- regression suite содержит неклассифицированные failures;
- ZigZag-only или Volume-only parity нарушен;
- gap behavior не соответствует разделу 4;
- first-bar reset наследуется из полного runtime;
- exporter требует fake ZigZag/Volume поля;
- time-only требует volume column;
- canonical batch всё ещё пытается выполнить unsupported equal-blocks;
- public snapshot содержит `_windows_minutes`;
- parallel и sequential результаты различаются.

---

## 45. Критерии готовности

Работа завершена, если одновременно выполняется следующее:

- `time_filter` работает при выключенных ZigZag и Volume;
- legacy `window` работает;
- новый `windows` работает;
- интервалы сортируются канонически;
- conflict/overlap/touching отклоняются детерминированно;
- внеоконные entry/reversal запрещены;
- пассивные outside bars не создают ложные blocked decisions;
- reset возникает при выходе из каждого торгового цикла;
- gap reset возникает на первом следующем существующем баре;
- gap exposure честно сохраняется в старой позиции;
- reset-бар может содержать старую позицию;
- следующий существующий execution bar становится flat;
- reset имеет приоритет над входом;
- позиция восстанавливается в новом окне без нового ST flip;
- open-to-open модель не изменена;
- данные и WF-периоды не обрезаются Time-фильтром;
- Time mask не пересчитывается для каждого grid point;
- все effective modes имеют определённый dispatch;
- WF, direct, single tester и поддерживаемые batch paths дают паритет;
- disabled runtime baseline не изменился;
- ZigZag-only, Volume-only и ZigZag+Volume без Time не регрессировали;
- diagnostics capability-based и не содержат фиктивных полей;
- trade exit reasons отражают только исполненные выходы;
- public snapshot не содержит внутренних resolver fields;
- effective fingerprint не зависит от порядка окон;
- equal-blocks limitation явно соблюдается;
- оба рабочих конфига загружаются;
- direct/WF/tester/legacy-batch smoke успешны;
- полный regression suite зелёный либо содержит только заранее
  классифицированные baseline failures.

---

## 46. Оценка объёма

Ожидаемый объём для одного опытного разработчика:

- activation/config/serialization: 2–3 дня;
- reset core и precomputed runtime: 2–3 дня;
- миграция ZigZag/Volume: 2–3 дня;
- time-only runtime: 2–3 дня;
- WF/tester/batch/export: 3–5 дней;
- regression/performance/docs/config smoke: 3–5 дней.

Итого:

```text
14–22 инженерных дня
```

Оценка не включает:

- поддержку trade filter в equal-blocks;
- scheduled/pre-open session flatten;
- устранение gap exposure;
- изменение execution model;
- восстановление или переработку unrelated существующих exporter failures.
